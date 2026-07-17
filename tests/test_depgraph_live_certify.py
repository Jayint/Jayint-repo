from __future__ import annotations

import sys
from pathlib import Path

# Put <repo>/src on the path so python_deps.depgraph.* resolves
# (mirrors the pattern in test_world_model_dep_graph.py and tests/depgraph/conftest.py).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.envstate.depgraph_live import (  # noqa: E402
    certify_refresh,
    certify_targets,
    ensure_python_shim,
)
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy  # noqa: E402


def _pkg(name):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="1.0",
                check_command=f'python -c "import {name}"')


def test_certify_refresh_flips_state_from_live_checks():
    g = DepGraph(nodes=(_pkg("flask"), _pkg("ghost")))

    def exec_readonly(cmd):
        # flask import succeeds (rc 0); ghost fails (rc 1)
        return (0, "") if "import flask" in cmd else (1, "ModuleNotFoundError: ghost")

    out = certify_refresh(g, exec_readonly, cycle=3)
    assert out.get("pkg:flask").state is State.SATISFIED
    assert out.get("pkg:flask").certified_cycle == 3
    assert out.get("pkg:ghost").state is State.MISSING


def test_certify_refresh_noop_when_disabled_or_empty():
    g = DepGraph(nodes=(_pkg("flask"),))
    assert certify_refresh(g, None, cycle=0) is g          # no executor
    assert certify_refresh(None, lambda c: (0, ""), 0) is None  # no graph


def test_ensure_python_shim_skips_mutation_when_python_exists():
    # Fix #5: symlink python->python3 so `python -m pip show` certifies on a
    # python3-only base (else installs never certify and the drain re-emits forever).
    mutations = []
    probes = []

    def sandbox_execute(cmd):
        mutations.append(cmd)
        return True, ""

    def exec_readonly(cmd):
        probes.append(cmd)
        return 0, "/usr/local/bin/python\n"

    ensure_python_shim(sandbox_execute, exec_readonly)
    assert probes == ["command -v python >/dev/null 2>&1"]
    assert mutations == []


def test_ensure_python_shim_separates_probe_from_symlink_mutation():
    mutations = []
    probes = []

    def sandbox_execute(cmd):
        mutations.append(cmd)
        return True, ""

    def exec_readonly(cmd):
        probes.append(cmd)
        return (1, "") if cmd.startswith("command -v python ") else (0, "")

    ensure_python_shim(sandbox_execute, exec_readonly)
    assert probes == [
        "command -v python >/dev/null 2>&1",
        "command -v python3 >/dev/null 2>&1",
    ]
    assert mutations == ['ln -sf "$(command -v python3)" /usr/local/bin/python']


def test_ensure_python_shim_noop_and_safe_without_executor():
    ensure_python_shim(None)  # must not raise


# --- Task 4: certify confirmed in-image services via loopback probe (arm-gated) ---

def test_certify_refresh_certifies_confirmed_service_when_allowed():
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=State.UNKNOWN, check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed"})
    g = DepGraph().with_node(svc)
    out = certify_refresh(g, lambda cmd: (0, "accepting connections"), cycle=1,
                          allow_service_certify=True)
    assert out.get("service:postgres").state is State.SATISFIED
    out_off = certify_refresh(g, lambda cmd: (0, ""), cycle=1)   # default: off
    assert out_off.get("service:postgres").state is State.UNKNOWN


def test_service_enabled_refresh_leaves_full_tests_to_scheduler_gate():
    svc = Node(id="service:redis", type=NodeType.SERVICE, name="redis",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.RUNTIME,
               state=State.UNKNOWN, check_command="redis-cli ping",
               data={"service_confidence": "confirmed"})
    test = Node(id="test:repo_tests_pass", type=NodeType.TEST,
                name="repo_tests_pass", layer=Layer.TESTS,
                discovered_by=DiscoveredBy.GOAL, state=State.UNKNOWN,
                check_command="python -m pytest -q")
    calls = []

    def exec_readonly(cmd):
        calls.append(cmd)
        return 0, "ok"

    out = certify_refresh(
        DepGraph(nodes=(test, svc)), exec_readonly, cycle=2,
        allow_service_certify=True,
    )

    assert calls == ["redis-cli ping"]
    assert out.get(svc.id).state is State.SATISFIED
    assert out.get(test.id).state is State.UNKNOWN

    explicit = certify_refresh(
        DepGraph(nodes=(test, svc)), exec_readonly, cycle=3,
        allow_service_certify=True, certify_tests=True,
    )
    assert explicit.get(test.id).state is State.SATISFIED
    assert calls[-1] == "python -m pytest -q"


def test_service_enabled_target_certification_skips_test_nodes():
    test = Node(id="test:repo_tests_pass", type=NodeType.TEST,
                name="repo_tests_pass", layer=Layer.TESTS,
                discovered_by=DiscoveredBy.GOAL, state=State.MISSING,
                check_command="python -m pytest -q")
    calls = []

    def exec_readonly(cmd):
        calls.append(cmd)
        return 0, "1 passed"

    graph = DepGraph(nodes=(test,))
    skipped = certify_targets(
        graph, exec_readonly, cycle=1, node_ids=(test.id,),
        allow_service_certify=True,
    )
    assert calls == []
    assert skipped.get(test.id).state is State.MISSING

    explicit = certify_targets(
        graph, exec_readonly, cycle=2, node_ids=(test.id,),
        allow_service_certify=True, certify_tests=True,
    )
    assert calls == ["python -m pytest -q"]
    assert explicit.get(test.id).state is State.SATISFIED
