import sys
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for _path in (str(_ROOT), str(_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Layer,
    Node,
    NodeType,
    State,
)
from src.envstate.depgraph_live import certify_refresh
from src.envstate.deterministic_maintainer import DeterministicMaintainer
from src.envstate.incremental_executor import IncrementalExecutionResult
from src.envstate.ledger import ActionLedger
from src.envstate.orchestrator import run_v3
from src.envstate.repair_loop import RepairOutcome
from src.envstate.run_trace import RunTracer
from src.envstate.world_model import initial_map
from src.sandbox import InstallResult


class _Agent:
    client = object()
    container_id = "fake"

    def propose(self, *args, **kwargs):
        raise AssertionError("no repair should be needed")


def _demo_world():
    node = Node(
        id="pkg:demo",
        type=NodeType.PACKAGE,
        name="demo",
        version="1.0",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        setup_commands=("install-demo",),
        check_command="check demo",
    )
    return initial_map(
        base_image="python:3.11-slim",
        workdir="/app",
        language="python",
        build_system="setuptools",
        repo_layout=(),
        dep_graph=DepGraph(nodes=(node,)),
    )


def test_failed_incremental_setup_does_not_run_test_gate():
    pytest_calls = 0
    incremental_calls = 0
    replay_calls = 0

    def sandbox_execute(command):
        nonlocal pytest_calls
        if "pytest" in command:
            pytest_calls += 1
        return True, "1 passed"

    def exec_readonly(command):
        return 1, "missing"

    def incremental_execute(graph, manual_blocks, cycle):
        nonlocal incremental_calls
        incremental_calls += 1
        return IncrementalExecutionResult(
            graph=graph,
            install_result=InstallResult(1, "install-demo", 1, "network failure"),
            failed_block_id="pip.demo",
            failed_node_id="pkg:demo",
            plan_hash="sha256:failed",
            total_blocks=1,
            reused_blocks=0,
            executed_block_ids=(),
            restored_checkpoint=None,
            created_checkpoints=(),
        )

    def run_install_script(script):
        nonlocal replay_calls
        replay_calls += 1
        return InstallResult(0, None, None, "")

    agent = _Agent()
    agent.client = None
    _final_map, stop = run_v3(
        agent,
        maintainer=DeterministicMaintainer(v3_only=True),
        initial_world_map=_demo_world(),
        ledger=ActionLedger(),
        sandbox_execute=sandbox_execute,
        exec_readonly=exec_readonly,
        reset_to_base=lambda: None,
        run_install_script=run_install_script,
        incremental_execute=incremental_execute,
        max_cycles=1,
    )

    assert stop == "planner_giveup"
    assert incremental_calls == 1
    assert pytest_calls == 0
    assert replay_calls == 0


def test_test_gate_waits_until_incremental_suffix_is_complete():
    installed = False
    incremental_calls = 0
    pytest_after_incremental_calls = []

    def exec_readonly(command):
        if command == "check demo":
            return ((0, "present") if installed else (1, "missing"))
        return 0, ""

    def sandbox_execute(command):
        if "pytest" in command:
            assert installed, "pytest must not run against a partial setup prefix"
            pytest_after_incremental_calls.append(incremental_calls)
            return True, "collected 1 item\n.\n1 passed in 0.01s"
        return True, ""

    def incremental_execute(graph, manual_blocks, cycle):
        nonlocal installed, incremental_calls
        incremental_calls += 1
        if incremental_calls == 1:
            return IncrementalExecutionResult(
                graph=graph,
                install_result=InstallResult(1, "install-demo", 1, "transient failure"),
                failed_block_id="pip.demo",
                failed_node_id="pkg:demo",
                plan_hash="sha256:prefix",
                total_blocks=1,
                reused_blocks=0,
                executed_block_ids=(),
                restored_checkpoint=None,
                created_checkpoints=(),
            )
        installed = True
        graph = certify_refresh(
            graph, exec_readonly, cycle, certify_tests=False
        )
        return IncrementalExecutionResult(
            graph=graph,
            install_result=InstallResult(0, None, None, ""),
            failed_block_id=None,
            failed_node_id=None,
            plan_hash="sha256:complete",
            total_blocks=1,
            reused_blocks=0,
            executed_block_ids=("pip.demo",),
            restored_checkpoint=None,
            created_checkpoints=("exec-1-complete",),
        )

    def reset_to_base():
        nonlocal installed
        installed = False

    def run_install_script(script):
        nonlocal installed
        installed = True
        return InstallResult(0, None, None, "")

    agent = _Agent()
    agent.client = None
    final_map, stop = run_v3(
        agent,
        maintainer=DeterministicMaintainer(v3_only=True),
        initial_world_map=_demo_world(),
        ledger=ActionLedger(),
        sandbox_execute=sandbox_execute,
        exec_readonly=exec_readonly,
        reset_to_base=reset_to_base,
        run_install_script=run_install_script,
        incremental_execute=incremental_execute,
        max_cycles=3,
    )

    assert stop == "planner_done"
    assert final_map.dep_graph.get("pkg:demo").state is State.SATISFIED
    assert incremental_calls == 2
    assert pytest_after_incremental_calls == [2, 2]


def test_incremental_search_has_one_terminal_fresh_replay_certificate():
    node = Node(
        id="pkg:demo",
        type=NodeType.PACKAGE,
        name="demo",
        version="1.0",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        setup_commands=("install-demo",),
        check_command="check demo",
    )
    graph = DepGraph(nodes=(node,))
    world = initial_map(
        base_image="python:3.11-slim",
        workdir="/app",
        language="python",
        build_system="setuptools",
        repo_layout=(),
        dep_graph=graph,
    )

    installed = False
    incremental_calls = 0
    resets = 0
    full_replays: list[str] = []
    gates = []
    pytest_calls = 0

    def exec_readonly(command: str):
        if command == "check demo":
            return (0, "present") if installed else (1, "missing")
        return 0, ""

    def sandbox_execute(command: str):
        nonlocal pytest_calls
        if "pytest" in command:
            pytest_calls += 1
            return True, "collected 1 item\n.\n1 passed in 0.01s"
        return True, ""

    def incremental_execute(current_graph, manual_blocks, cycle):
        nonlocal installed, incremental_calls
        incremental_calls += 1
        installed = True
        current_graph = certify_refresh(current_graph, exec_readonly, cycle)
        return IncrementalExecutionResult(
            graph=current_graph,
            install_result=InstallResult(0, None, None, ""),
            failed_block_id=None,
            failed_node_id=None,
            plan_hash="sha256:test",
            total_blocks=1,
            reused_blocks=0,
            executed_block_ids=("pip.demo",),
            restored_checkpoint=None,
            created_checkpoints=("exec-1-test",),
        )

    def reset_to_base():
        nonlocal installed, resets
        installed = False
        resets += 1

    def run_install_script(script: str):
        nonlocal installed
        full_replays.append(script)
        assert "install-demo" in script
        installed = True
        return InstallResult(0, None, None, "")

    tracer = RunTracer(repo="demo/repo", loop_mode="v3_graph_execute_agent")
    final_map, stop = run_v3(
        _Agent(),
        maintainer=DeterministicMaintainer(v3_only=True),
        initial_world_map=world,
        ledger=ActionLedger(),
        sandbox_execute=sandbox_execute,
        exec_readonly=exec_readonly,
        reset_to_base=reset_to_base,
        run_install_script=run_install_script,
        incremental_execute=incremental_execute,
        enable_gate_observability=True,
        gate_observer=gates.append,
        max_cycles=3,
        tracer=tracer,
    )

    trace = tracer.snapshot(stop_reason=stop, gates={})
    assert stop == "planner_done"
    assert final_map.dep_graph.get("pkg:demo").state is State.SATISFIED
    assert incremental_calls == 1
    assert resets == 1
    assert len(full_replays) == 1
    assert len(trace.incremental) == 1
    assert len(trace.replays) == 1
    assert pytest_calls == 2  # incremental success gate + fresh binding gate
    assert trace.last_replay.setup_rc == 0
    assert trace.last_replay.test_rc == 0
    assert gates and all(gate.passed for gate in gates[-1])


def test_terminal_fresh_failure_is_repaired_and_replayed_again(monkeypatch):
    import src.envstate.orchestrator as orchestrator

    node = Node(
        id="pkg:demo",
        type=NodeType.PACKAGE,
        name="demo",
        version="1.0",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        setup_commands=("python -c 'import demo'",),
        check_command="check demo",
    )
    world = initial_map(
        base_image="python:3.11-slim", workdir="/app", language="python",
        build_system="setuptools", repo_layout=(), dep_graph=DepGraph(nodes=(node,)),
    )
    installed = False
    replay_scripts = []
    repair_ids = []

    def exec_readonly(command):
        return ((0, "present") if installed else (1, "missing"))

    def sandbox_execute(command):
        if "pytest" in command:
            return True, "collected 1 item\n.\n1 passed in 0.01s"
        return True, ""

    def incremental_execute(graph, manual_blocks, cycle):
        nonlocal installed
        installed = True
        graph = certify_refresh(graph, exec_readonly, cycle)
        return IncrementalExecutionResult(
            graph=graph,
            install_result=InstallResult(0, None, None, ""),
            failed_block_id=None,
            failed_node_id=None,
            plan_hash="sha256:initial",
            total_blocks=1,
            reused_blocks=0,
            executed_block_ids=("pip.demo",),
            restored_checkpoint=None,
            created_checkpoints=("exec-1-initial",),
        )

    def reset_to_base():
        nonlocal installed
        installed = False

    def run_install_script(script):
        nonlocal installed
        replay_scripts.append(script)
        if "install-demo-fixed" in script:
            installed = True
            return InstallResult(0, None, None, "")
        return InstallResult(
            1,
            "python -c 'import demo'",
            3,
            "ModuleNotFoundError: No module named 'demo'",
        )

    def fake_repair(graph, failed_id, bundle, cycle, *, manual_blocks=(), **kwargs):
        repair_ids.append(failed_id)
        repaired_node = replace(
            graph.get("pkg:demo"),
            setup_commands=("install-demo-fixed",),
            state=State.MISSING,
        )
        return RepairOutcome(
            graph=graph.with_node(repaired_node),
            still_failing_id=None,
            manual_blocks=manual_blocks,
            known_invalid=frozenset(),
            turns_spent=1,
            budget_exhausted=False,
        )

    monkeypatch.setattr(orchestrator, "run_structured_repair", fake_repair)
    final_map, stop = run_v3(
        _Agent(),
        maintainer=DeterministicMaintainer(v3_only=True),
        initial_world_map=world,
        ledger=ActionLedger(),
        sandbox_execute=sandbox_execute,
        exec_readonly=exec_readonly,
        reset_to_base=reset_to_base,
        run_install_script=run_install_script,
        incremental_execute=incremental_execute,
        max_cycles=3,
    )

    assert stop == "planner_done"
    assert repair_ids == ["pkg:demo"]
    assert len(replay_scripts) == 2
    assert "install-demo-fixed" in replay_scripts[-1]
    assert final_map.dep_graph.get("pkg:demo").state is State.SATISFIED
