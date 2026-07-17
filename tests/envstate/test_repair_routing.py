"""Tests: run_v3 diagnoses every failure BEFORE typed repair (Phase 6).

``_repair_or_route`` (a ``run_v3``-scope closure) is the SINGLE
``run_structured_repair`` call site left in ``run_v3`` — both the main-loop
site (``_dep_emit_phase``) and the task-branch obligation-repair site route
through it. These tests drive the main-loop site: it fires unconditionally
every cycle once ``enable_dep_emit`` is on and a reciped node stays
unsatisfied after a (mocked) install, which is the simplest way to hand
``_repair_or_route`` a real ``EvidenceBundle`` built from install stderr.

``graph_scheduler.next_decision`` is patched to a decision that never carries
``target_node_ids`` and never stops the loop, so the task-branch's OWN
``_repair_or_route`` call site (which would otherwise fire immediately after
the main-loop site in the same cycle, since the syslib node stays MISSING)
never runs — isolating the main-loop site's diagnosis routing for these
tests. Patching strategy mirrors test_v3_task_branch.py: ``next_decision`` is
patched on the SOURCE module (``src.envstate.graph_scheduler``) because
``run_v3`` resolves it via a local ``from ... import`` inside the function
body.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.envstate.graph_scheduler as gs_module
from graph.python.read import repo_modules as repo_modules_module
from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import (
    PlannerDecision,
    Task,
    initial_map,
    merge_map,
)
from src.sandbox import InstallResult
from graph.model import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _FakeClient:
    """Non-None sentinel for the ``getattr(build_agent, "client", None)`` guard."""


class _RecordingBuildAgent:
    """Minimal build agent with a non-None .client and a counting .propose.

    .run must never be called by run_v3 (no free-text path left) — raising
    turns any accidental call into an immediate, loud test failure.
    """

    def __init__(self):
        self.client = _FakeClient()
        self.model = "fake-model"
        self.propose_calls = 0

    def run(self, task, sandbox_execute, ledger, step_offset=0, check=None, budget=None):
        raise AssertionError("build_agent.run must never be called by run_v3")

    def propose(self, scope, exec_readonly=None, **kwargs):
        self.propose_calls += 1
        return None   # no proposal -> run_structured_repair returns after one turn


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _syslib_map():
    """WorldModelMap with one MISSING, reciped SystemLib node (mirrors
    test_v3_repair_wiring._syslib_map — a node _binding_emit's
    certify_reciped_only will always report unsatisfied)."""
    node = Node(
        id="syslib:libpq.so",
        type=NodeType.SYSTEM_LIB,
        name="libpq.so",
        layer=Layer.SYSTEM,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        check_command="ldconfig -p | grep -q libpq",
        chosen_fix="apt:libpq-dev",
    )
    base = initial_map(
        base_image="python:3.11-slim",
        workdir="/repo",
        language="python",
        build_system="pip",
        repo_layout=(),
    )
    return merge_map(base, dep_graph=DepGraph().with_node(node))


def _noop_reset_to_base() -> None:
    pass


def _harmless_decision(*_args, **_kwargs):
    """next_decision replacement: a discover task (empty target_node_ids)
    every call — never selects a targeted obligation and never stops the
    loop, so a run_v3 cycle exercises exactly the main-loop repair site."""
    task = Task(
        goal="discover", done_when=orchestrator.VERIFY_TEST_CMD,
        layer="tests", facts=(), target_node_ids=(),
    )
    return PlannerDecision(action="task", task=task), None


def _base_inputs(build_agent, run_install_script, *, repo_path=None, max_cycles=1):
    led = ActionLedger()

    def sandbox(cmd: str):
        return (True, "ok")   # discover-gate probe always "passes" -> no ledger noise

    def ro(cmd: str):
        return (1, "")        # node check always fails -> stays MISSING every cycle

    return dict(
        build_agent=build_agent,
        maintainer=_NoopMaintainer(),
        initial_world_map=_syslib_map(),
        ledger=led,
        sandbox_execute=sandbox,
        max_cycles=max_cycles,
        exec_readonly=ro,
        enable_dep_emit=True,
        reset_to_base=_noop_reset_to_base,
        run_install_script=run_install_script,
        repo_path=repo_path,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_repo_internal_ref_bundle_skips_repair(monkeypatch):
    """A ModuleNotFoundError for a repo-local module must never spend a
    repair turn: _repair_or_route must diagnose it REPO_INTERNAL_REF and
    return the graph unchanged, WITHOUT ever calling build_agent.propose."""
    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)
    # The orchestrator now diagnoses against repo_modules (precise top-levels +
    # collisions), NOT scan.local_module_names (the over-broad construction set).
    monkeypatch.setattr(
        repo_modules_module, "top_level_names",
        lambda repo_path: frozenset({"docs_src"}),
    )
    monkeypatch.setattr(
        repo_modules_module, "stem_collisions", lambda repo_path: {}
    )

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="python -c \"import docs_src\"", lineno=None,
            stderr="ModuleNotFoundError: No module named 'docs_src'",
        )

    agent = _RecordingBuildAgent()
    inputs = _base_inputs(agent, run_install_script, repo_path="/fake/repo")

    orchestrator.run_v3(**inputs)

    assert agent.propose_calls == 0, (
        "propose was called for a repo-internal reference; the diagnosis "
        "router must skip repair for Mode.REPO_INTERNAL_REF"
    )


def test_invalid_attempt_records_normalized_name_no_repair(monkeypatch):
    """A pip-disproven package name must never be retried.

    Cycle 1: 'No matching distribution found for Frobnicate_9000' is
    diagnosed INVALID_ATTEMPT unconditionally (no context needed) and the
    normalized name ('frobnicate-9000') is recorded into the router's
    RepoContext.invalid_names.

    Cycle 2: a PLAIN 'ModuleNotFoundError: No module named 'frobnicate_9000''
    for the SAME (normalized) name would, on its own, resolve to
    Mode.ENVIRONMENT (classify_observation maps it to a Discovery) and call
    propose -- UNLESS the name is already in ctx.invalid_names, in which case
    diagnose() takes the "previously disproven" branch back to
    INVALID_ATTEMPT. Asserting propose is never called across both cycles
    therefore proves the name recorded in cycle 1 was carried into cycle 2's
    RepoContext and reused (not just two independent INVALID_ATTEMPT hits).
    """
    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)

    calls = {"n": 0}

    def run_install_script(script):
        calls["n"] += 1
        stderr = (
            "No matching distribution found for Frobnicate_9000"
            if calls["n"] == 1
            else "ModuleNotFoundError: No module named 'frobnicate_9000'"
        )
        return InstallResult(
            rc=1, failing_command="pip install frobnicate-9000", lineno=None,
            stderr=stderr,
        )

    agent = _RecordingBuildAgent()
    inputs = _base_inputs(agent, run_install_script, max_cycles=2)

    orchestrator.run_v3(**inputs)

    assert calls["n"] >= 2, "test setup did not reach a second replay cycle"
    assert agent.propose_calls == 0, (
        "propose was called; the disproven name from cycle 1 was not carried "
        "into cycle 2's RepoContext.invalid_names"
    )


def test_environment_bundle_invokes_typed_repair_with_replay_emit(monkeypatch):
    """A genuine external-package ModuleNotFoundError must route through
    run_structured_repair — i.e. build_agent.propose IS called — proving
    Mode.ENVIRONMENT reaches typed repair with the replay (_binding_emit)
    emit closure wired in."""
    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="python -c \"import requests\"", lineno=None,
            stderr="ModuleNotFoundError: No module named 'requests'",
        )

    agent = _RecordingBuildAgent()
    inputs = _base_inputs(agent, run_install_script)

    orchestrator.run_v3(**inputs)

    assert agent.propose_calls >= 1, (
        "propose was never called for a genuine external-package "
        "ModuleNotFoundError; Mode.ENVIRONMENT must reach typed repair"
    )


def test_residual_bundle_skips_repair(monkeypatch):
    """An AssertionError (residual, non-environment failure) must never spend
    a repair turn: _repair_or_route must diagnose it Mode.RESIDUAL and return
    the graph unchanged, WITHOUT ever calling build_agent.propose."""
    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="pytest -q", lineno=None,
            stderr="AssertionError: assert 1 == 2",
        )

    agent = _RecordingBuildAgent()
    inputs = _base_inputs(agent, run_install_script)

    orchestrator.run_v3(**inputs)

    assert agent.propose_calls == 0, (
        "propose was called for a residual AssertionError; the diagnosis "
        "router must skip repair for Mode.RESIDUAL"
    )


def test_ambiguous_bundle_invokes_typed_repair(monkeypatch):
    """An unclassifiable failure (maps to no package, not local, not a proven
    invalid attempt) is diagnosed Mode.AMBIGUOUS, which — like ENVIRONMENT —
    still routes through run_structured_repair (spending a propose turn to
    disambiguate) rather than being silently dropped."""
    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="some_cmd", lineno=None,
            stderr="some totally unclassifiable gibberish output xyz123",
        )

    agent = _RecordingBuildAgent()
    inputs = _base_inputs(agent, run_install_script)

    orchestrator.run_v3(**inputs)

    assert agent.propose_calls >= 1, (
        "propose was never called for an unclassifiable (AMBIGUOUS) failure; "
        "Mode.AMBIGUOUS must reach typed repair, same as Mode.ENVIRONMENT"
    )


def test_stem_collision_bundle_spends_a_repair_turn(tmp_path, monkeypatch):
    """The `azure` bug, end-to-end through run_v3.

    A REAL tree whose only `azure` is `wagtail/backends/azure.py` — i.e. the
    module `wagtail.backends.azure`, NOT an importable top-level. The old broad
    rule called it repo-local and returned REPO_INTERNAL_REF without ever calling
    build_agent.propose — a silent give-up on a genuinely missing package.
    It must now reach the repair loop.
    """
    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)

    (tmp_path / "wagtail" / "backends").mkdir(parents=True)
    (tmp_path / "wagtail" / "__init__.py").write_text("")
    (tmp_path / "wagtail" / "backends" / "__init__.py").write_text("")
    (tmp_path / "wagtail" / "backends" / "azure.py").write_text("")

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="python -m pytest -q", lineno=None,
            stderr="ModuleNotFoundError: No module named 'azure'",
        )

    agent = _RecordingBuildAgent()
    inputs = _base_inputs(agent, run_install_script, repo_path=str(tmp_path))

    orchestrator.run_v3(**inputs)

    assert agent.propose_calls > 0, (
        "propose was never called for 'azure': the router still treats a stem "
        "collision as a repo-internal reference and gives up silently"
    )


def test_collision_evidence_reaches_the_repair_prompt(tmp_path, monkeypatch):
    """The LLM MUST be told the name collides with a repo file, or it will
    happily `pip install items` — a real PyPI package that must never be
    installed. patch_gate validates structure only; it will admit it."""
    from src.envstate.repair_scope import render_repair_scope

    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)

    (tmp_path / "docs_src" / "subcommands" / "tutorial001").mkdir(parents=True)
    (tmp_path / "docs_src" / "subcommands" / "tutorial001" / "__init__.py").write_text("")
    (tmp_path / "docs_src" / "subcommands" / "tutorial001" / "items.py").write_text("")

    seen_scopes = []

    class _ScopeCapturingAgent(_RecordingBuildAgent):
        def propose(self, scope, exec_readonly=None, **kwargs):
            seen_scopes.append(scope)
            return super().propose(scope, exec_readonly, **kwargs)

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="python -m pytest -q", lineno=None,
            stderr="ModuleNotFoundError: No module named 'items'",
        )

    agent = _ScopeCapturingAgent()
    inputs = _base_inputs(agent, run_install_script, repo_path=str(tmp_path))
    orchestrator.run_v3(**inputs)

    assert seen_scopes, "propose was never called — the collision was dropped"
    rendered = render_repair_scope(seen_scopes[0])
    assert "tutorial001.items" in rendered, (
        "the repair prompt does not tell the agent that 'items' is a repo file; "
        "it will install the PyPI package `items`, which is WRONG"
    )
    assert "do not install" in rendered.lower()


def test_plain_external_gets_no_collision_constraint(tmp_path, monkeypatch):
    """A genuine external-package failure must get NO collision constraint —
    the constraint is only for names that classify as Locality.STEM_COLLISION."""
    from src.envstate.repair_scope import render_repair_scope

    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")

    seen = []

    class _Cap(_RecordingBuildAgent):
        def propose(self, scope, exec_readonly=None, **kwargs):
            seen.append(scope)
            return super().propose(scope, exec_readonly, **kwargs)

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="python -m pytest -q", lineno=None,
            stderr="ModuleNotFoundError: No module named 'requests'",
        )

    inputs = _base_inputs(_Cap(), run_install_script, repo_path=str(tmp_path))
    orchestrator.run_v3(**inputs)

    assert seen
    assert "local_module_collision" not in render_repair_scope(seen[0])


def test_single_repair_call_site_and_no_block_emit_in_source():
    """Source-level pin: exactly one literal run_structured_repair( call
    site remains in run_v3 (inside _repair_or_route), and block_emit is not
    an executable code path (replay/_binding_emit is the only executor)."""
    import inspect
    src = inspect.getsource(orchestrator.run_v3)
    assert src.count("run_structured_repair(") == 1   # only inside _repair_or_route
    assert "block_emit(" not in src                   # replay is the only executor
