"""Task 8 (companion-plan): the local-import guard must also apply at INGEST
time, not just at repair time.

``_repair_or_route`` (the ``_dep_emit_phase``/install-failure repair site)
already routes every bundle through ``diagnose_all``/``RepoContext`` before
spending a repair turn -- see
``tests/envstate/scenarios/test_repo_local_import_guard.py``. But
``_runtime_ingest_phase`` (the discover-gate/``ingest_runtime_failures`` site)
built its classifier tier from the raw ``classify_observation`` regex
classifier, which knows nothing about repo-local names. A
``ModuleNotFoundError: No module named 'docs_src'`` surfaced by the
deterministic discover gate (``VERIFY_TEST_CMD``) therefore got ingested next
cycle as a bogus ``pkg:docs_src`` node -- which then fails every fresh replay
forever, since no such PyPI distribution exists.

These two tests drive that exact DISCOVER -> INGEST path end-to-end through
``run_v3`` (fake sandbox, no exec_readonly/client -- pure deterministic
classifier tier) and assert:
  1. a repo-local import (``docs_src``, a real ``tmp_path`` package with an
     ``__init__.py``) never becomes a ``pkg:docs_src``/``pkg:docs-src`` node.
  2. a genuine external import (``requests``) IS still ingested as
     ``pkg:requests`` -- proving the guard filters, it does not over-block.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import initial_map
from python_deps.depgraph.schema import DepGraph


class _Agent:
    """No ``client`` attribute -> ``_runtime_ingest_phase`` uses the
    deterministic-only classifier tier (no LLM tier constructed)."""


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _empty_map():
    return initial_map(
        base_image="python:3.11-slim", workdir="/repo", language="python",
        build_system="pip", repo_layout=(), dep_graph=DepGraph(),
    )


def _make_repo_with_local_docs_src(tmp_path):
    """A real filesystem tree: tmp_path/docs_src/__init__.py, so
    ``scan.local_module_names`` discovers it for real (not monkeypatched)."""
    pkg_dir = tmp_path / "docs_src"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    return tmp_path


def _failing_verify_sandbox(stderr_line: str):
    """Fake sandbox: VERIFY_TEST_CMD always fails with the given traceback
    line. Never called for anything else in this scenario (exec_readonly is
    None, so ``_dep_emit_phase`` never touches the sandbox; no typed-repair
    task ever fires since no node ever reaches ``State.MISSING``)."""
    def sandbox_execute(cmd):
        return (False, f"collecting tests...\n{stderr_line}\n1 error\n")
    return sandbox_execute


def _unreachable_run_install_script(script):
    raise AssertionError(
        "run_install_script must never be called in this scenario "
        "(exec_readonly=None disables _dep_emit_phase's certify/emit path)"
    )


def test_discover_gate_repo_local_import_never_becomes_package_node(tmp_path):
    repo = _make_repo_with_local_docs_src(tmp_path)
    sandbox_execute = _failing_verify_sandbox(
        "ModuleNotFoundError: No module named 'docs_src'"
    )

    final_map, stop = orchestrator.run_v3(
        build_agent=_Agent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_empty_map(),
        ledger=ActionLedger(),
        sandbox_execute=sandbox_execute,
        max_cycles=5,
        exec_readonly=None,
        enable_dep_emit=True,
        reset_to_base=lambda: None,
        run_install_script=_unreachable_run_install_script,
        repo_path=str(repo),
    )

    all_ids = tuple(n.id for n in (final_map.dep_graph.nodes if final_map.dep_graph else ()))
    assert not any(nid in ("pkg:docs_src", "pkg:docs-src") for nid in all_ids), (
        "a bogus package node was added for the repo-local import 'docs_src' "
        f"via the discover-gate/runtime-ingest path: {all_ids}"
    )
    # The graph never gains an actionable obligation from a repo-local import,
    # so the scheduler can never close the loop -- the run must give up
    # honestly, never report success.
    assert stop != "planner_done"


def test_discover_gate_external_import_is_still_ingested_as_package(tmp_path):
    # docs_src is present in the repo tree but is NOT the failing import here
    # -- proves the guard is precise (blocks docs_src, allows requests), not
    # a blanket "runtime ingest is broken" false negative.
    repo = _make_repo_with_local_docs_src(tmp_path)
    sandbox_execute = _failing_verify_sandbox(
        "ModuleNotFoundError: No module named 'requests'"
    )

    final_map, stop = orchestrator.run_v3(
        build_agent=_Agent(),
        maintainer=_NoopMaintainer(),
        initial_world_map=_empty_map(),
        ledger=ActionLedger(),
        sandbox_execute=sandbox_execute,
        max_cycles=5,
        exec_readonly=None,
        enable_dep_emit=True,
        reset_to_base=lambda: None,
        run_install_script=_unreachable_run_install_script,
        repo_path=str(repo),
    )

    all_ids = tuple(n.id for n in (final_map.dep_graph.nodes if final_map.dep_graph else ()))
    assert "pkg:requests" in all_ids, (
        "a genuine external import ('requests') was NOT ingested as a package "
        f"node via the discover-gate/runtime-ingest path: {all_ids}"
    )
