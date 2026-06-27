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


# ---------------------------------------------------------------------------
# Config-Binding (Task 10) — off-state byte-identity for the binding paths.
#
# Each assertion proves the binding feature is INERT off-arm, and pairs that
# with an on-arm counter-assertion so the off-state test is NON-VACUOUS (the
# trigger genuinely fires when armed). All three assert already-implemented
# behaviour — a FAIL is a real regression; do NOT weaken to force a pass.
# ---------------------------------------------------------------------------


def _confirmed_pg_graph_with_binding():
    """Confirmed postgres SERVICE node carrying a discovered binding in its
    ``data`` (``bound_config`` / ``bound_config_url`` / ``db``), so that
    ``attach_in_image_provisioning(..., enabled=True)`` builds a binding CONFIG
    node. Mirrors ``_confirmed_pg_graph`` in tests/depgraph/test_service_binding.py.
    """
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State,
    )
    from python_deps.depgraph.ids import service_id

    svc = Node(
        id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
        layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.UNKNOWN,
        check_command="pg_isready -h postgres -p 5432",
        fix_candidates=("service:postgres:14",), chosen_fix="service:postgres:14",
        evidence="compose", provenance="service scan",
        data={"service_confidence": "confirmed", "host": "postgres", "port": 5432,
              "bound_config": "DB_STRING",
              "bound_config_url": "postgresql://postgres:test@db:5432/appdb",
              "db": "appdb"},
    )
    return DepGraph(nodes=(svc,), edges=())


# ---------------------------------------------------------------------------
# Test 4: attach_in_image_provisioning is byte-identical off-arm
# ---------------------------------------------------------------------------

def test_binding_off_state_byte_identical():
    from python_deps.depgraph.service_scan import attach_in_image_provisioning
    from python_deps.depgraph.ids import config_id

    g = _confirmed_pg_graph_with_binding()

    # OFF-arm: no binding CONFIG node added -> graph dict byte-identical.
    assert attach_in_image_provisioning(g, enabled=False).to_dict() == g.to_dict()

    # ON-arm (non-vacuous): the binding CONFIG node IS created.
    assert attach_in_image_provisioning(g, enabled=True).get(config_id("DB_STRING")) is not None


# ---------------------------------------------------------------------------
# Test 5: scheduler_frontier excludes the binding node when services are off
# ---------------------------------------------------------------------------

def test_binding_excluded_from_frontier_off_arm():
    import dataclasses
    from python_deps.depgraph.service_scan import attach_in_image_provisioning
    from python_deps.depgraph.schedule import scheduler_frontier
    from python_deps.depgraph.schema import State
    from python_deps.depgraph.ids import service_id, config_id

    # Arm the build to materialise the binding CONFIG node + REQUIRES edge.
    g = attach_in_image_provisioning(_confirmed_pg_graph_with_binding(), enabled=True)
    bid = config_id("DB_STRING")
    sid = service_id("postgres")

    # Make the binding actionable: service SATISFIED (its dep), binding MISSING.
    g = g.with_node(dataclasses.replace(g.get(sid), state=State.SATISFIED))
    g = g.with_node(dataclasses.replace(g.get(bid), state=State.MISSING))

    # OFF-arm (allow_services=False): the binding CONFIG node is NOT scheduled.
    off_ids = {n.id for n in scheduler_frontier(g, allow_services=False)}
    assert bid not in off_ids

    # ON-arm (non-vacuous): with services armed the binding IS in the frontier.
    on_ids = {n.id for n in scheduler_frontier(g, allow_services=True)}
    assert bid in on_ids


# ---------------------------------------------------------------------------
# Test 6: eval wrapper emits no `export` for a service dict lacking `var`
# ---------------------------------------------------------------------------

def test_eval_wrapper_no_export_without_binding_var():
    from run_repo2run_benchmark import compose_in_image_service_commands

    # Service WITHOUT a binding `var` -> no `export` line emitted.
    no_var = {"confirmed_in_image_services": [
        {"start": "svc-start", "wait": "svc-wait", "createdb": "svc-createdb"},
    ]}
    out = compose_in_image_service_commands(no_var)
    assert not any(line.startswith("export ") for line in out)

    # WITH `var`+`url` (non-vacuous): the `export` line IS emitted.
    with_var = {"confirmed_in_image_services": [
        {"start": "svc-start", "var": "DB_STRING",
         "url": "postgresql://postgres:postgres@127.0.0.1:5432/appdb"},
    ]}
    out2 = compose_in_image_service_commands(with_var)
    assert any(line.startswith("export DB_STRING=") for line in out2)
