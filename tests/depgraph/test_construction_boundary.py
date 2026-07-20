"""The construction module boundary, after THE FLIP (route-not-drop + spine).

``repo_modules``' sys.path-accurate precise set (``top_level_names`` /
``stem_collisions``) is DIAGNOSIS-grade and CATASTROPHIC in construction if used to
decide install-vs-drop directly: a false-*external* reaches Phase-A's identity
candidate ladder, which will ACCEPT and install an identically-named real PyPI
distribution (typer's ``items``, netbox's ``extras`` are both real dists).

Pre-flip, ``scan`` protected against this by over-broadly DROPPING every locally
shadowed name. THE FLIP removed that drop: locally shadowed names are now minted as
Import nodes and ROUTED by ``classify.py`` — the sole sanctioned consumer of the
precise set — to the collision zone (``deferred``), where a name installs its PyPI
namesake ONLY after a cure-verified certificate proves it does not resolve locally.

So the boundary moved but did not weaken. Two guards enforce it, both of which land
with the flip (the old pre-flip drop-based guards would fail it):

  * STRUCTURAL — only ``classify.py`` may reference the precise set; ``scan`` /
    ``roots`` / ``skeleton`` / ``fixpoint`` / ``pipeline`` / ``orchestrate`` stay
    clean (they would re-open the wrong-install vector), and the sanctioned
    consumer must actually consume it (a vacuous guard is worthless).
  * BEHAVIORAL — a collision name is install-accepted (a fallthrough) ONLY IF the
    cure succeeded AND the canonical-plan probe returned genuinely-not-local; a
    failed cure or a locally-resolving / present-but-broken probe never installs.

If either fails, do NOT relax it.
"""
from __future__ import annotations

import ast
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from graph.core import orchestrate
from graph.python import fixpoint, pipeline, skeleton
from graph.python.lanes.install import roots
from graph.python.read import scan
from graph.python.route import classify as classify_mod

from graph.python.route.arbitrate import arbitrate
from graph.python.lanes.config.cure import CureResult
from graph.python.invocation_resolver import TestEnvPlan

_FORBIDDEN = frozenset({"top_level_names", "stem_collisions", "repo_modules"})


def _referenced_names(source: str) -> set[str]:
    """Names a module actually REFERENCES in code -- not in comments or prose.

    AST-based on purpose. A substring scan over the raw source cannot tell code
    from documentation, and ``scan.py`` legitimately mentions ``repo_modules`` in
    a comment (the two walks must prune identically, so they are cross-referenced).
    Stripping ``#`` text to work around that is worse than the disease: it is not
    Python-aware, so a line like ``marker = "#"; x = repo_modules.top_level_names(p)``
    would be truncated at the hash INSIDE the string literal and the real call
    would sail through the guard.

    Collects every import (plain, aliased, from-import, relative) and every
    attribute base, which together cover the ways the precise set could be reached.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.update(node.module.split("."))
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def test_construction_never_references_the_diagnosis_only_precise_set():
    """Structural guard: no construction path may reach the precise set directly.

    ``classify.py`` is DELIBERATELY absent from this list — it is the one sanctioned
    consumer (see the companion positive test). Every OTHER construction module must
    stay clean: a leak there re-opens the wrong-PyPI-install vector.
    """
    for module in (scan, skeleton, fixpoint, pipeline, orchestrate, roots):
        referenced = _referenced_names(inspect.getsource(module))
        leaked = sorted(_FORBIDDEN & referenced)
        assert not leaked, (
            f"{module.__name__} references {leaked}. The precise module set is "
            f"routing-authority-ONLY (classify.py). Using it elsewhere in "
            f"construction makes Phase-A install a wrong PyPI package (typer "
            f"`items`, netbox `extras` are real dists)."
        )


def test_classify_is_the_sanctioned_consumer_of_the_precise_set():
    """Positive half of the boundary: the router MUST actually consume the precise
    set. A structural ban that nothing balances would pass even if the precise set
    were dead everywhere — then the collision zone would never be populated and the
    flip's route-not-drop safety would be vacuous. classify.py is where the
    sys.path-accurate ladder lives, so it is the ONE place the set is allowed AND
    required."""
    referenced = _referenced_names(inspect.getsource(classify_mod))
    assert _FORBIDDEN <= referenced, (
        "classify.py must reference the precise set (repo_modules / top_level_names "
        f"/ stem_collisions); missing: {sorted(_FORBIDDEN - referenced)}. It is the "
        "sanctioned routing authority — if it stops consuming the set, collision "
        "names stop being routed and the route-not-drop safety is gone."
    )


def test_ast_guard_actually_catches_a_reintroduction():
    """The guard above is worthless if it cannot fail. Prove each leak shape trips it.

    The last case is the one a substring-with-comment-stripping guard MISSES: the
    hash lives inside a string literal, so naive stripping truncates the line and
    the real call disappears.
    """
    leaks = (
        "from graph import repo_modules",
        "from graph.python.read.repo_modules import top_level_names",
        "import graph.python.read.repo_modules as rm",
        "local = rm.top_level_names(repo_path)",
        'marker = "#"; local = repo_modules.top_level_names(repo_path)',
    )
    for src in leaks:
        assert _FORBIDDEN & _referenced_names(src), f"guard failed to catch: {src!r}"

    # ...and does NOT trip on the legitimate prose cross-reference in scan.py.
    assert not (_FORBIDDEN & _referenced_names("# PUBLIC: `repo_modules` prunes identically\nx = 1"))


# --------------------------------------------------------------------------- #
# Behavioral guard — the collision certificate. A stubbed CureResult + a canned
# per-name probe stand in for the container; the invariant under test is arbitrate's
# gate, not the container.
# --------------------------------------------------------------------------- #
def _plan() -> TestEnvPlan:
    return TestEnvPlan(
        interpreter="python3",
        interpreter_confidence="default",
        project_dirs=(".",),
        install_plan=(),
        rootdir=".",
        pythonpath=(),
        import_mode="prepend",
        layout="flat",
    )


@dataclass
class _StubResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class _ProbeExecutor:
    """Answers every ``import X`` probe with one canned verdict — no container."""

    repo_mount_dir = "/workspace/repo"

    def __init__(self, result: _StubResult):
        self._result = result

    def run(self, *_a, **_k) -> _StubResult:
        return self._result


_CURED = CureResult(ok=True, rung="isolated", collect_ok=True, evidence="cured")
_UNCURED = CureResult(ok=False, rung="", collect_ok=False, evidence="cure failed")


def test_failed_cure_never_installs_a_collision():
    """The blocker (spec review §1): if the cure did NOT succeed, EVERY deferred
    collision stays unresolved — never a batch of wrong-installs on exactly the
    repos the config lane exists to fix."""
    # A probe that WOULD say fallthrough — proving the gate is the cure, not the probe.
    ex = _ProbeExecutor(_StubResult(1, stderr="ModuleNotFoundError: No module named 'items'"))
    arb = arbitrate(ex, _plan(), _UNCURED, frozenset({"items"}))
    assert arb.fallthrough == frozenset()          # nothing install-accepted
    assert arb.unresolved == frozenset({"items"})  # honest RED
    assert arb.resolves_local == frozenset()


def test_cured_and_genuinely_not_local_is_the_only_install_path():
    """A collision installs its PyPI namesake ONLY when cure succeeded AND the
    canonical-plan probe raised ModuleNotFoundError for the probed name itself
    (genuinely absent)."""
    ex = _ProbeExecutor(_StubResult(1, stderr="ModuleNotFoundError: No module named 'items'"))
    arb = arbitrate(ex, _plan(), _CURED, frozenset({"items"}))
    assert arb.fallthrough == frozenset({"items"})  # install-accepted, but flagged upstream
    assert arb.resolves_local == frozenset()
    assert arb.unresolved == frozenset()


def test_cured_but_resolves_locally_is_never_installed():
    """Cure succeeded but the name imports cleanly under the plan -> it IS local
    (a Module), so it must NOT install the PyPI namesake."""
    ex = _ProbeExecutor(_StubResult(0))  # import X returns rc 0 -> local
    arb = arbitrate(ex, _plan(), _CURED, frozenset({"items"}))
    assert arb.resolves_local == frozenset({"items"})
    assert arb.fallthrough == frozenset()


def test_cured_but_present_but_broken_is_never_installed():
    """A probe that raises a DIFFERENT error (not a MNFE for the probed name) is
    present-but-broken -> the module IS local, never an install fallthrough
    (spec review §7). Here a DIFFERENT missing module surfaces."""
    ex = _ProbeExecutor(_StubResult(1, stderr="ModuleNotFoundError: No module named 'numpy'"))
    arb = arbitrate(ex, _plan(), _CURED, frozenset({"items"}))
    assert arb.resolves_local == frozenset({"items"})  # broken-local, not a fallthrough
    assert arb.fallthrough == frozenset()
