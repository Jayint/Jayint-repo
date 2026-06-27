"""Task 13 — off-state byte-identity + integration regression.

Verifies that the in-image service provisioning arm is truly inert when
*disabled* (the default).  Three tests:

1. ``build_dep_graph`` default (no kwarg) == ``enable_service_provision=False``
   — graph dicts are byte-identical and no ``syslib:postgresql`` node appears.
2. ``compose_in_image_service_commands`` returns ``[]`` when the run_summary
   has no ``confirmed_in_image_services`` field.
3. A confirmed service node WITHOUT a ``start_recipe`` in its data renders the
   legacy SERVICES block (no ``needs (System):`` / ``start:`` lines).

All three assert *already-implemented* off-state behaviour.  A FAIL means a
real regression — do NOT weaken the assertions to force a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure both repo root and src/ are importable.
_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
for _p in (str(_REPO), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Inline helpers (mirrors tests/depgraph/test_build.py _r + FakeExecutor).
# FakeExecutor is imported directly from the depgraph conftest module so we
# share exactly the same implementation without coupling to pytest conftest
# discovery order.
# ---------------------------------------------------------------------------

def _r(returncode: int = 0, stdout: str = "", stderr: str = ""):
    from python_deps.depgraph.executor import CommandResult
    return CommandResult(command="", returncode=returncode, stdout=stdout, stderr=stderr)


def _get_fake_executor_class():
    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "depgraph_conftest",
        str(_REPO / "tests" / "depgraph" / "conftest.py"),
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.FakeExecutor


# ---------------------------------------------------------------------------
# Test 1: off-state byte-identity for build_dep_graph
# ---------------------------------------------------------------------------

def test_build_off_state_byte_identical(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", raising=False)

    FakeExecutor = _get_fake_executor_class()
    from python_deps.depgraph.build import build_dep_graph

    # Minimal repo with a CI workflow that declares a postgres service.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "0"\n'
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "db.py").write_text("import psycopg2\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:14\n"
    )

    ex = FakeExecutor(default=_r(returncode=1, stderr="x"))

    # Default (no kwarg) must equal explicit enable_service_provision=False.
    a = build_dep_graph(str(tmp_path), ex, host_executor=ex, target_python="3.11")
    b = build_dep_graph(
        str(tmp_path), ex, host_executor=ex, target_python="3.11",
        enable_service_provision=False,
    )
    assert a.to_dict() == b.to_dict()

    from python_deps.depgraph.ids import syslib_id
    assert a.get(syslib_id("postgresql")) is None  # no provisioning nodes off-arm


# ---------------------------------------------------------------------------
# Test 2: eval wrapper unchanged without confirmed_in_image_services field
# ---------------------------------------------------------------------------

def test_eval_wrapper_unchanged_without_field():
    from run_repo2run_benchmark import compose_in_image_service_commands
    assert compose_in_image_service_commands({"verified_test_commands": ["pytest"]}) == []


# ---------------------------------------------------------------------------
# Test 3: advisory unchanged for advisory-only service (no start_recipe)
# ---------------------------------------------------------------------------

def test_advisory_unchanged_for_advisory_only_service():
    # A confirmed service WITHOUT a start_recipe (arm off at build) renders the
    # legacy SERVICES block exactly — no needs/start lines.
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State,
    )
    from python_deps.depgraph.advise import render_dep_graph_advisory

    svc = Node(
        id="service:postgres",
        type=NodeType.SERVICE,
        name="postgres",
        layer=Layer.SERVICES,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.UNKNOWN,
        fix_candidates=("service:postgres:16",),
        data={"service_confidence": "confirmed"},
    )
    out = render_dep_graph_advisory(DepGraph().with_node(svc))
    assert "needs (System)" not in out and "start:" not in out
