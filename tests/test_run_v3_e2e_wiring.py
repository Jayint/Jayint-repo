"""The 'loop closed' assertion: the selected+pinned image reaches all three
consumers, and target_python == the pinned minor. No Docker, no network."""
import types
import scripts.run_v3_e2e as e2e
from src.envstate.base_image_selection import BaseImageChoice
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


def _gate(name, passed, provisional=False):
    return types.SimpleNamespace(name=name, passed=passed, provisional=provisional)


def test_e2e_success_uses_binding_gates_despite_soft_unresolved_nodes():
    gates = [[
        _gate("installability", True),
        _gate("testability", True),
    ]]
    assert e2e._e2e_succeeded(
        "planner_done", ["service:redis", "config:DB_CONN"], gates
    )


def test_e2e_success_rejects_failed_or_provisional_binding_gate():
    assert not e2e._e2e_succeeded(
        "planner_done",
        [],
        [[_gate("installability", True, provisional=True), _gate("testability", True)]],
    )
    assert not e2e._e2e_succeeded(
        "planner_done",
        [],
        [[_gate("installability", True), _gate("testability", False)]],
    )


def test_e2e_success_without_gate_observability_keeps_legacy_rule():
    assert e2e._e2e_succeeded("planner_done", [], [])
    assert not e2e._e2e_succeeded("planner_done", ["pkg:missing"], [])


def test_v3_command_timeout_env_is_positive(monkeypatch):
    monkeypatch.delenv("V3_COMMAND_TIMEOUT_SECONDS", raising=False)
    assert e2e._positive_env_seconds("V3_COMMAND_TIMEOUT_SECONDS", 600) == 600
    monkeypatch.setenv("V3_COMMAND_TIMEOUT_SECONDS", "240")
    assert e2e._positive_env_seconds("V3_COMMAND_TIMEOUT_SECONDS", 600) == 240
    monkeypatch.setenv("V3_COMMAND_TIMEOUT_SECONDS", "0")
    assert e2e._positive_env_seconds("V3_COMMAND_TIMEOUT_SECONDS", 600) == 600
    monkeypatch.setenv("V3_COMMAND_TIMEOUT_SECONDS", "invalid")
    assert e2e._positive_env_seconds("V3_COMMAND_TIMEOUT_SECONDS", 600) == 600


def test_runtime_handoff_exports_only_certified_confirmed_services():
    confirmed = Node(
        id="service:redis", type=NodeType.SERVICE, name="redis",
        layer=Layer.SERVICES, discovered_by=DiscoveredBy.RUNTIME,
        state=State.SATISFIED,
        data={
            "service_confidence": "confirmed",
            "start_recipe": {"start": "redis-server --daemonize yes"},
        },
        check_command=(
            "python3 -c \"import socket; "
            "s=socket.create_connection(('127.0.0.1',6379),2); s.close()\""
        ),
    )
    inferred = Node(
        id="service:mongo", type=NodeType.SERVICE, name="mongo",
        layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.SATISFIED,
        data={
            "service_confidence": "inferred",
            "start_recipe": {"start": "mongod --fork"},
        },
    )
    graph = DepGraph().with_node(confirmed).with_node(inferred)
    assert e2e._runtime_handoff(graph) == {
        "version": 1,
        "services": [{
            "kind": "redis",
            "start": "redis-server --daemonize yes",
            "check": (
                "python3 -c \"import socket; "
                "s=socket.create_connection(('127.0.0.1',6379),2); s.close()\""
            ),
        }],
        "runtime_commands": ["redis-server --daemonize yes"],
    }


def test_runtime_handoff_exports_explicit_pytest_policy():
    assert e2e._runtime_handoff(
        None, pytest_addopts=("--import-mode=importlib",)
    ) == {
        "version": 1,
        "services": [],
        "runtime_commands": [],
        "environment": {"PYTEST_ADDOPTS": "--import-mode=importlib"},
    }


def test_selected_image_and_minor_thread_to_all_consumers(monkeypatch, tmp_path):
    seen = {}
    call_order = []

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    # Facade returns a known pinned choice with a non-None platform override,
    # so the assertion below locks the platform thread through Sandbox(...).
    choice = BaseImageChoice("python:3.10-slim", "3.10", "linux/amd64", "auto: test")
    monkeypatch.setattr(e2e, "choose_base_image", lambda *a, **k: choice, raising=False)
    monkeypatch.setattr(
        e2e,
        "discover_test_dependency_intent",
        lambda _repo: types.SimpleNamespace(
            needed_groups=frozenset({"test"}), pytest_addopts=()
        ),
        raising=False,
    )

    def _fake_advisory(
        repo,
        image,
        *,
        host_executor=None,
        target_python=None,
        classify=None,
        needed_extras=frozenset(),
        platform=None,
    ):
        call_order.append("advisory")
        seen["advisory_image"] = image
        seen["advisory_platform"] = platform
        seen["advisory_target_python"] = target_python
        seen["advisory_needed_extras"] = needed_extras
        return "", None
    monkeypatch.setattr(e2e, "build_advisory_for_repo", _fake_advisory, raising=False)

    def _fake_initial_map(*, base_image, **k):
        seen["map_image"] = base_image
        return types.SimpleNamespace(dep_graph=None)
    monkeypatch.setattr(e2e, "initial_map", _fake_initial_map, raising=False)

    class _FakeSandbox:
        def __init__(self, *, base_image, workdir="/app", platform=None, seed_dir=None, **k):
            call_order.append("sandbox")
            seen["sandbox_image"] = base_image
            seen["sandbox_platform"] = platform
            seen["sandbox_ensure_native"] = k.get("ensure_native_platform")
            seen["sandbox_command_timeout"] = k.get("command_timeout_seconds")
            seen["sandbox_environment"] = k.get("environment")
            self.base_image_ref = "sha256:resolved-amd64"
            self.platform = platform
            self.container = None
        def execute(self, *a, **k): return None
        def exec_readonly(self, *a, **k): return None
        def reset_to_base(self): return None
        def run_install_script(self, *a, **k): return None
    monkeypatch.setattr(e2e, "Sandbox", _FakeSandbox, raising=False)

    monkeypatch.setattr(e2e, "run_v3",
                        lambda *a, **k: (types.SimpleNamespace(dep_graph=None), "done"),
                        raising=False)

    rc = e2e.main_with_args([str(tmp_path)])  # see Step 3 for the seam

    assert call_order.index("sandbox") < call_order.index("advisory")
    assert seen["advisory_image"] == "sha256:resolved-amd64"
    assert seen["advisory_platform"] == "linux/amd64"
    assert seen["advisory_target_python"] == "3.10"      # loop closed: minor -> graph
    assert seen["advisory_needed_extras"] == frozenset({"test"})
    assert seen["map_image"] == "python:3.10-slim"
    assert seen["sandbox_image"] == "python:3.10-slim"
    assert seen["sandbox_platform"] == "linux/amd64"     # platform thread -> Sandbox(platform=...)
    assert seen["sandbox_ensure_native"] is False
    assert seen["sandbox_command_timeout"] == 600
    assert seen["sandbox_environment"] is None
