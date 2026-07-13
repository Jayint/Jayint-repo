"""Graph context for the react arm (spec Rev 3.3 §6). Pure — no Docker, no network.

This module owns EVERY rule about what an edge means. Nothing downstream of
``verdict()`` touches an edge attribute. Today those rules are smeared across
``emit._toolchain_ready`` (soft), ``emit._conflicted_ids``/``_is_emittable``
(conflicts), and ``resolve_lock``'s marker pruning (markers); the arm gets ONE
copy, unit-tested against hand-built graphs.
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from python_deps.depgraph.schema import DepGraph, Edge, EdgeType, Node, NodeType, State

logger = logging.getLogger(__name__)

ACTIONABLE = "ACTIONABLE"      # MISSING, nothing missing beneath it -> the agent acts HERE
WAITING = "WAITING"            # a hard prerequisite is missing -> fix that first
BLOCKED = "BLOCKED"            # in a version conflict -> NO install will ever work
SATISFIED_OK = "SATISFIED_OK"  # already fine -> nothing to do, and NO record
UNCERTIFIED = "UNCERTIFIED"    # UNKNOWN state -> a root CANDIDATE, never a confirmed one


def _marker_holds(edge: Edge, target_env: dict | None) -> bool:
    """True when the edge's PEP 508 marker holds for the target (or is unevaluable).

    A universal lock lists dependencies for the WHOLE requires-python range, so an edge
    carrying `python_version < "3.9"` is not causal on a 3.12 target. When we cannot
    evaluate — no target env, or an unparseable marker — we traverse CONSERVATIVELY:
    dropping a real prerequisite is far worse than keeping a spurious one, because the
    spurious one will simply certify SATISFIED and land in the rule-out ring.
    """
    marker = getattr(edge, "marker", None)
    if not marker or target_env is None:
        return True
    try:
        from packaging.markers import Marker
        return bool(Marker(marker).evaluate(target_env))
    except Exception:                                  # noqa: BLE001 — never break the render
        logger.debug("graph_context: unevaluable marker %r; traversing", marker)
        return True


def blocks(graph: DepGraph, edge: Edge, target_env: dict | None = None) -> bool:
    """Does this edge currently BLOCK its source node from being installed?

    This mirrors `emit._toolchain_ready` (emit.py:63-81), which is the incumbent authority:
    the build-script renderer already decides what may be emitted, and if the arm reached a
    different conclusion we would tell the agent to fix something the renderer would happily
    have installed — a wasted turn, and every turn is a full container rebuild.

    An edge is not a blocker when:
      * it is not a REQUIRES edge (CONFLICTS_WITH is a constraint, not a need);
      * it is SOFT -- emit.py:69-70, "soft requires edges never block (invariant #10)";
      * its environment marker does not hold for the target (resolve_lock.py:442-451);
      * its target is already SATISFIED.

    And then DEPENDENCY TYPE decides, exactly as `_toolchain_ready` decides it:

      SystemLib  blocks ALWAYS. A wheel dlopens a runtime .so just as a source build links
                 against it, so a missing SystemLib defeats both.
      Tool       blocks a SOURCE build and an UNKNOWN build mode -- but NOT a known wheel
                 (`build_from_source is False`). A wheel needs no compiler, so telling the
                 agent to apt-get a build tool it will never invoke is a wasted rebuild.
      Package    never blocks: `pip install X` resolves and installs X's own dependencies.
                 (emit does not gate on these either -- it topologically orders them instead.)
      Config /   never blocks: those edges are SOFT by construction (the LLM's Config/Service
      Service    edges), so they are already excluded above.

    Taking the source node's build mode into account is why this needs the GRAPH and not just
    the edge: an edge alone cannot answer "is my owner a wheel?".
    """
    if edge.relation is not EdgeType.REQUIRES:
        return False
    if not (edge.data or {}).get("hard", True):
        return False
    if not _marker_holds(edge, target_env):
        return False
    dep = graph.get(edge.dst)
    if dep is None or dep.state is State.SATISFIED:
        return False
    if dep.type is NodeType.SYSTEM_LIB:
        return True
    if dep.type is NodeType.TOOL:
        src = graph.get(edge.src)
        return src is None or src.build_from_source is not False
    return False


def in_conflict(graph: DepGraph, node: Node) -> bool:
    """True when the node sits on a CONFLICTS_WITH edge (uv unsat core).

    `emit._is_emittable` (emit.py:84-100) already refuses to emit such a node: it cannot be
    installed at ANY version. Without this check the node looks like a perfectly good root
    -- MISSING, with no missing prerequisite -- and we would tell the agent to `pip install`
    it, forever.
    """
    return any(
        e.relation is EdgeType.CONFLICTS_WITH and node.id in (e.src, e.dst)
        for e in graph.edges
    )


def verdict(graph: DepGraph, node: Node, target_env: dict | None = None) -> str:
    """SATISFIED_OK | BLOCKED | UNCERTIFIED | WAITING | ACTIONABLE — the node's decision state.

    Order matters, and every early return is load-bearing:

      1. A SATISFIED node is DONE. Checking prerequisites first would call a satisfied leaf
         ACTIONABLE (it has no MISSING prerequisites, after all) and hand it a "fix this"
         record — telling the agent to install something that is already installed.
      2. A CONFLICTED node cannot be installed at ANY version (emit._is_emittable already
         refuses to emit it), yet it too has no missing prerequisites and would otherwise
         look like a perfectly good root. The agent would `pip install` it forever.
      3. An UNKNOWN node was never certified against the container — typically because it has
         no `check_command`. `emit._is_emittable` refuses every non-MISSING node for exactly
         this reason. It may be a root CANDIDATE; presenting it as a confirmed one would be
         passing off a guess as a measurement (spec §6.4: "UNKNOWN never masquerades as
         MISSING").

    Only after all three do prerequisites decide WAITING vs ACTIONABLE.
    """
    if node.state is State.SATISFIED:
        return SATISFIED_OK
    if in_conflict(graph, node):
        return BLOCKED
    if node.state is not State.MISSING:
        return UNCERTIFIED
    for edge in graph.edges:
        if edge.src == node.id and blocks(graph, edge, target_env):
            return WAITING
    return ACTIONABLE


# FALLBACK ONLY -- see `_count_tests`. `def test_x(` / `async def test_x(` at any indent.
# The prefix match (`test\w*`, not `test_\w*`) is deliberate: pytest's own collection rule is
# `name.startswith("test")` (`python_functions` default ini value is `["test"]`; see
# `_pytest.python.PyCollector._matches_prefix_or_glob_option`), so a function named
# `testing_helper` really is collected by pytest today.
_TEST_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+test\w*[ \t]*\(", re.MULTILINE)

_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_test_name(name: str) -> bool:
    # pytest matches on PREFIX, not on `test_`: `python_functions = ["test"]`, compared with
    # `name.startswith(...)`. So `testing_helper` really is a test as far as pytest is concerned.
    return name.startswith("test")


def _count_tests(text: str) -> int:
    """Count what pytest WOULD collect from this source, at module scope and in Test* classes.

    An AST walk rather than a line regex, because the regex systematically OVER-counts in a way
    that skews the very ranking this number exists to fix:

      * a `def test_x` in a `class Helper:` -- pytest only descends into classes matching
        `python_classes = ["Test"]`, so those methods are never collected. A shared helper class
        full of `def test_*` methods could single-handedly manufacture the large estimate we
        then rank on.
      * a `def test_x` nested inside a fixture or another function -- never collected.
      * a `def test_x` inside a triple-quoted string -- not code at all.

    It remains an ESTIMATE. It still under-counts `@pytest.mark.parametrize` expansion (which
    pytest resolves at collection time -- exactly the thing that did not happen) and test methods
    inherited from a base class in another module (which needs imports to resolve, and importing
    is what failed).
    """
    count = 0
    for node in ast.parse(text).body:
        if isinstance(node, _FUNC) and _is_test_name(node.name):
            count += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            count += sum(
                isinstance(m, _FUNC) and _is_test_name(m.name) for m in node.body
            )
    return count


def tests_hidden(repo_path: str | None, module: str) -> int | None:
    """Static estimate of how many tests a module holds. None when undeterminable.

    A COLLECTION error is per-FILE: the tests inside were never created as items, so
    pytest cannot tell us how many it hid. `Cause.count` for such a row is MODULES, not
    tests -- rank by it and a 23-test AssertionError outranks an import error hiding 200
    tests. This is the weight that fixes the ranking.

    It is an ESTIMATE and MUST be rendered as one ("~200 tests hidden, est.").

    THE ONLY I/O IN THIS MODULE: one read-only read of a file in the repo checkout. No Docker,
    no network, no subprocess, and nothing is mutated. When the file cannot be read the estimate
    is simply absent (None) and the render carries on without a weight.

    `module` is parsed out of pytest's own stdout/stderr, so it is UNTRUSTED input: never
    let it read a path outside `repo_path`.
    """
    if not repo_path or not module:
        return None
    root = Path(repo_path).resolve()
    try:
        # Resolve *before* the containment check so a symlink that points outside the repo
        # is caught too -- checking containment on the un-resolved path would approve the
        # link itself (it lives under `root`) and only dereference it afterwards.
        path = (root / module).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    if not path.is_file():
        return None
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    try:
        count = _count_tests(text)
    except (SyntaxError, ValueError, RecursionError):
        # The module does not even parse -- which a collection error can absolutely mean. A
        # rough over-count beats no weight at all, so fall back to the line regex here.
        count = len(_TEST_DEF.findall(text))
    return count or None
