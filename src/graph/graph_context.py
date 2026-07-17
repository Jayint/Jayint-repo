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

from graph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Node, NodeType, State,
)

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


# ── render_graph_context (Task 5) ────────────────────────────────────────────
#
# 🔴 Deviation from the plan's Task-5 snippet, found while implementing against the CURRENT
# `blocks()`/`verdict()` (Tasks 3/4 were revised after a second-model review; the plan's Task 5
# code was written against the OLDER, narrower `blocks(edge, target_env)`). The plan's
# `_down_edges` used `blocks(e, target_env)` -- both the wrong arity (missing `graph`) AND the
# wrong PREDICATE for this job: today's `blocks()` mirrors `emit._toolchain_ready` and answers
# "does this edge stop the OWNER from installing" -- it returns False for a SATISFIED dst and
# for ANY Package dst (pip resolves its own deps). Using it to decide which edges get RENDERED
# would silently drop every SATISFIED prerequisite line (breaking "[state] inline on every edge
# line", §6.6) and every Package->Package edge, satisfied or missing (breaking the closure
# summary AND the MISSING-member promotion, §3.2). `verdict()` already calls the real `blocks()`
# internally to decide WAITING vs ACTIONABLE; the renderer does not need to call it again -- it
# only needs to decide what to SHOW, which is a different, wider question.

_MAX_ERROR_NODES = 3          # a SUBGRAPH is expensive (two edge lists); a record is cheap
_IMPLAUSIBLE_FRONTIER = 15    # more ACTIONABLE nodes than this means OUR model is wrong
_FRONTIER_SAMPLE = 5


def _fmt_state(node: Node) -> str:
    if node.state is State.SATISFIED:
        return f"check passed: {node.check_command}" if node.check_command else "[SATISFIED]"
    if node.state is State.UNKNOWN:
        return "[UNKNOWN — no check command; state unverified]"
    return "[MISSING]"


def _anchor_for_cause(graph: DepGraph, cause) -> Node | None:
    """The graph node a pytest cause names. Matches a quoted module name against Package
    nodes by canonical name (the same normalized-name match runtime_ingest._find_existing_node
    uses -- do not reinvent it)."""
    from graph.python.lanes.install.naming import normalize_package_name

    m = re.search(r"['\"]([\w.\-]+)['\"]", cause.detail or "")
    if not m:
        return None
    wanted = normalize_package_name(m.group(1).split(".", 1)[0])
    for n in graph.nodes:
        if n.type is NodeType.PACKAGE and normalize_package_name(n.name) == wanted:
            return n
    return None


def _weight(cause, repo_path: str | None) -> tuple[int, bool]:
    """(tests blocked, is_estimate). A collect-phase count is MODULES, not tests (§4.2)."""
    if cause.phase == "collect":
        est = tests_hidden(repo_path, cause.module)
        if est is not None:
            return est, True
    return cause.count, False


def _down_edges(graph: DepGraph, node: Node, target_env: dict | None):
    """Prerequisite edges, with the SATISFIED pip closure collapsed. Returns (lines, followed).

    Renders every REQUIRES edge whose marker holds for the target -- regardless of the dst's
    own state or the edge's hard/soft flag (§6.3/§6.6: "[state] goes inline on every edge line";
    a soft edge still "traverses, marked advisory"). `blocks()`/`verdict()` decide WAITING vs
    ACTIONABLE elsewhere; this function decides only what the agent gets to SEE. The one
    collapse is Package->Package when the dst is SATISFIED (the lockfile closure, §3.2) -- a
    MISSING package dependency is exactly the "promoted" case and falls through to its own line.
    """
    closure_sat, lines, follow = 0, [], []
    for e in graph.edges:
        if e.src != node.id:
            continue
        dst = graph.get(e.dst)
        if dst is None:
            continue
        if e.relation is EdgeType.CONFLICTS_WITH:
            lines.append(f"    {node.id}  --conflicts-->  {dst.id}  [BLOCKED]      ✖")
            continue
        if e.relation is not EdgeType.REQUIRES:
            continue
        if not _marker_holds(e, target_env):
            continue                          # not causal on this target -- not even shown
        if node.type is NodeType.PACKAGE and dst.type is NodeType.PACKAGE:
            if dst.state is State.SATISFIED:
                closure_sat += 1
                continue                      # a MISSING member falls through and is PROMOTED
        # A SOFT edge is advisory: §6.3 says it "traverses, marked advisory" but "never confers
        # root status alone". So it is SHOWN -- the agent should know the dependency was posited
        # -- but it earns no ★ and it does not feed `follow`, so it gets no "fix this" record.
        # Starring it would tell the agent to install something on the strength of a guess.
        if not (e.data or {}).get("hard", True):
            lines.append(f"    {node.id}  --requires-->  {dst.id}  {_fmt_state(dst)}"
                         f"  (advisory)")
            continue
        mark = "  ★" if verdict(graph, dst, target_env) == ACTIONABLE else ""
        lines.append(f"    {node.id}  --requires-->  {dst.id}  {_fmt_state(dst)}{mark}")
        follow.append(dst)
    if closure_sat:
        lines.append(f"    {node.id}  --requires-->  ({closure_sat} transitive pip deps)"
                     f"  [{closure_sat} SATISFIED]")
    return lines, follow


def _plural(n: int, word: str) -> str:
    # The output is read by an LLM. "1 tests" is noise that costs it a beat.
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _as_command(fix: str) -> str:
    """`apt:libpq-dev` -> `apt-get install -y libpq-dev`.

    `chosen_fix` is stored as a PROVIDER ID, not a shell command — `build_deps._capability_node`
    writes `f"apt:{top.package}"`, and `emit._apt_name` strips that prefix when it renders the
    build script. The record must do the same. A `fix` field the agent cannot paste into setup.sh
    is not a fix; it is a riddle, and the agent falls back to guessing an apt name out of the
    error text — exactly the behaviour the graph exists to replace.
    """
    return f"apt-get install -y {fix[4:]}" if fix.startswith("apt:") else fix


def _up_edges(graph: DepGraph, node: Node, weight: int, est: bool) -> list[str]:
    out = []
    for src in graph.required_by(node.id):
        state = "" if src.type is NodeType.TEST else f"  {_fmt_state(src)}"
        out.append(f"    {src.id}  --requires-->  {node.id}{state}")
    if weight:                     # a build-fail anchor has no test weight -- say nothing
        tag = (f"(~{_plural(weight, 'test')} hidden, est.)" if est
               else f"({_plural(weight, 'test')})")
        out.append(f"    → blocks {tag}")
    return out


def _record(graph: DepGraph, node: Node, target_env,
            blocked_by_us: list[tuple[str, int, bool]]) -> list[str]:
    """A record goes ONLY where a DECISION is possible. Fields self-select on content."""
    from graph.advise import _conflict_note

    v = verdict(graph, node, target_env)
    if v == BLOCKED:
        out = [f"✖ {node.id}    MISSING — CANNOT INSTALL"]
        note = _conflict_note(graph, node)
        if note:
            out.append(f"    conflict {note}")
        out.append("    note     no `pip install` fixes this — one of the two must change version")
    else:
        out = [f"★ {node.id}    {node.state.value.upper()}"]
        if node.check_command:
            out.append(f"    check    {node.check_command}")
        fix = node.chosen_fix or (node.fix_candidates[0] if node.fix_candidates else None)
        if fix:
            out.append(f"    fix      {_as_command(fix)}")
        for alt in node.fix_candidates[1:]:
            out.append(f"             alt: {_as_command(alt)}")
        # `why`/`source` only when RUNTIME-discovered: a Debian build-deps-table node need not
        # justify itself; one we appended from a log line MUST (it is how the agent audits us).
        if node.discovered_by is DiscoveredBy.RUNTIME:
            if node.evidence:
                out.append(f"    why      {node.evidence}")
            src = node.provenance or "runtime discovery"
            out.append(f"    source   {src}")
    # The ANTI-THRASH field: agents re-retry disproven fixes because their memory is lossy prose.
    for a in node.attempts:
        out.append(f"    tried    turn {a.cycle}: {a.command} → {a.outcome.upper()}")
    if len(blocked_by_us) > 1:
        parts = []
        for anchor_id, w, est in blocked_by_us:
            tag = f"~{_plural(w, 'test')}" if est else _plural(w, "test")
            parts.append(f"{anchor_id} ({tag})")
        out.append(f"    blocks   {' · '.join(parts)}")
    return out


def render_graph_context(graph: DepGraph, result, causes, prev_states,
                         repo_path: str | None = None, target_env: dict | None = None) -> str:
    """The GRAPH CONTEXT block (spec §6). Pure — every state was certified by loop.py:196.

    `result` is a `RunResult`-shaped object (`.ok`, `.failing_command`, `.output`) or `None`.
    `result` is REQUIRED, not optional (§6.7): `loop.py` only runs pytest once the build is
    GREEN, so on a build-fail turn `causes` is EMPTY and the only failure to anchor at is the
    failing build command. `prev_states` is `dict[str, State]` from the turn before this one.
    Returns `""` when there is nothing worth saying.
    """
    sub, records, notes = [], {}, []
    blocked_by: dict[str, list[tuple[str, int, bool]]] = {}

    # --- error nodes ---------------------------------------------------------
    # 🔴 TWO SEPARATE PASSES, and they must NOT share a cap.
    #   * SUBGRAPHS are expensive (two edge lists each) -> capped at _MAX_ERROR_NODES.
    #   * RECORDS are cheap, and ACTIONABLE roots are INDEPENDENT -> the agent should batch
    #     ALL of them in one patch, and every hidden root costs a full container rebuild (§6.4).
    # Collecting records only from the CAPPED anchors would transitively cap records too,
    # silently reintroducing the very top-N truncation §6.4 exists to forbid.
    anchors: list[tuple[Node, int, bool, str]] = []          # -> SUBGRAPH (capped)
    all_anchors: list[tuple[Node, int, bool]] = []            # -> RECORDS (uncapped)

    if result is not None and not result.ok and result.failing_command:
        from graph.graph_enrich import owner_node_for_command
        owner = owner_node_for_command(graph, result.failing_command)
        n = graph.get(owner) if owner else None
        if n is not None:
            anchors.append((n, 0, False, f"BUILD FAILED  {result.failing_command}"))
            all_anchors.append((n, 0, False))
        else:
            # No node owns this command — an apt command, a batch install, or a package we never
            # modelled. We must STILL speak: this is a build-fail turn, so `causes` is empty
            # (pytest never ran), and a renderer that returns "" here goes silent on exactly the
            # turns where the install tier is broken — the §6.7 failure mode in person.
            notes.append(
                "BUILD FAILED — NO GRAPH EXPLANATION\n"
                f"    {result.failing_command}\n"
                "    No node in the model owns this command. The graph cannot localise it — "
                "read the build log."
            )

    ranked = sorted(causes, key=lambda c: _weight(c, repo_path)[0], reverse=True)
    seen: set[str] = set()
    for c in ranked:
        if c.phase in ("call", "teardown"):        # §4.3 — structurally NOT an env failure
            notes.append(f"NOT AN ENVIRONMENT FAILURE\n    {c.exc}: {c.detail}  "
                         f"[{c.phase}]  ({c.count} tests) — test body. No env fix exists.")
            continue
        node = _anchor_for_cause(graph, c)
        if node is None:
            notes.append(f"NO GRAPH EXPLANATION\n    {c.exc}: {c.detail}  [{c.phase}] — "
                         f"not in the model. Explore.")
            continue
        if node.id in seen:
            continue
        seen.add(node.id)
        w, est = _weight(c, repo_path)
        all_anchors.append((node, w, est))          # EVERY anchor feeds records — no cap here
        if len(anchors) < _MAX_ERROR_NODES:         # only the top-N get a rendered SUBGRAPH
            anchors.append((node, w, est, f"ERROR NODE  {node.id}   {_fmt_state(node)}\n"
                                          f"  ← {c.exc}: {c.detail}   [{c.phase}]"))

    # --- records: from ALL anchors, uncapped (§6.4) --------------------------
    for anchor_node, w, est in all_anchors:
        _down, follow = _down_edges(graph, anchor_node, target_env)
        for cand in [anchor_node, *follow]:
            if verdict(graph, cand, target_env) in (ACTIONABLE, BLOCKED):
                records.setdefault(cand.id, cand)
                blocked_by.setdefault(cand.id, []).append((anchor_node.id, w, est))

    # --- subgraph: only the top-N anchors ------------------------------------
    for node, w, est, header in anchors:
        sub.append(header)
        down, _follow = _down_edges(graph, node, target_env)
        if down:
            sub.append("\n  REQUIRES — what it needs (the fix is in here)")
            sub.extend(down)
        sub.append("\n  REQUIRED BY — what breaks because of it (the impact)")
        sub.extend(_up_edges(graph, node, w, est))
        sub.append("")
    dropped = len(all_anchors) - len(anchors)
    if dropped > 0:                                # NEVER silently drop (§6.6)
        sub.append(f"  + {_plural(dropped, 'more failure class')}; "
                   "their roots appear in the records below.")

    build_failed = result is not None and not result.ok
    if not sub and not notes and not build_failed:
        return ""

    # --- frontier health (§6.4.1) -------------------------------------------
    actionable = [n for n in graph.nodes
                  if n.state is not State.SATISFIED
                  and verdict(graph, n, target_env) == ACTIONABLE]
    head = ["GRAPH CONTEXT — certified against the container your script just built", ""]
    if len(actionable) > _IMPLAUSIBLE_FRONTIER:
        # ONE line per sentence — the test asserts on substrings, and wrapping a phrase across
        # two list entries puts a "\n" in the middle of it.
        head += [
            f"⚠ {len(actionable)} nodes are ACTIONABLE. That is implausible — independent roots"
            " do not arrive in bulk.",
            "  A shared prerequisite is almost certainly MISSING FROM THE MODEL"
            " (check the runtime/venv tier).",
            f"  Showing the {_FRONTIER_SAMPLE} largest; treat the graph as unreliable this turn.",
            "",
        ]
        # ...so they had better actually BE the largest. Slicing in insertion order while the
        # header claims "the largest" is a lie in the output, and it buries the one root that
        # matters: a 100-test root sitting at index 15 would be dropped for five 1-test roots.
        def _blocked_weight(nid: str) -> int:
            return sum(w for _anchor, w, _est in blocked_by.get(nid, []))

        keep = sorted(records, key=_blocked_weight, reverse=True)[:_FRONTIER_SAMPLE]
        records = {k: records[k] for k in keep}

    body: list[str] = []
    if sub:                       # no subgraph -> no legend, no separator rule (§6.6)
        body += ["SUBGRAPH  (edges around this turn's failures; [state] inline)", ""] + sub
        body += ["  ★ actionable — nothing missing beneath it",
                 "  ✖ blocked — no install will work", "", "─" * 74, ""]
    for nid, node in records.items():
        body += _record(graph, node, target_env, blocked_by.get(nid, [])) + [""]
    body += notes

    if result is not None and not result.ok:
        body += ["", "TESTS DID NOT RUN — the build failed. No pytest signal this turn."]

    delta = []
    for nid, was in (prev_states or {}).items():
        now = graph.get(nid)
        if now is not None and now.state is not was:
            delta.append(f"    {nid}   {was.value.upper()} → {now.state.value.upper()}")
    if delta:
        body += ["", "SINCE YOUR LAST EDIT"] + delta

    return "\n".join(head + body).rstrip() + "\n"
