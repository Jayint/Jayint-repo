from dataclasses import replace
import sys
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
from python_deps.depgraph.block import Block
from src.envstate.incremental_executor import IncrementalPlanExecutor
from src.sandbox import InstallResult


def _node(
    node_id: str,
    name: str,
    *,
    layer: Layer,
    node_type: NodeType,
    command: str,
) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=name,
        version="1.0" if node_type is NodeType.PACKAGE else None,
        layer=layer,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        chosen_fix=f"apt:{name}" if node_type is not NodeType.PACKAGE else None,
        setup_commands=(command,),
        check_command=f"check {name}",
    )


def test_patch_restores_longest_valid_prefix_and_executes_only_changed_suffix():
    first = _node(
        "syslib:first", "first", layer=Layer.SYSTEM,
        node_type=NodeType.SYSTEM_LIB, command="install-a",
    )
    second = _node(
        "pkg:second", "second", layer=Layer.PIP,
        node_type=NodeType.PACKAGE, command="install-b-bad",
    )
    graph = DepGraph(nodes=(first, second))

    environment: set[str] = set()
    snapshots: dict[str, set[str]] = {}
    install_calls: list[str] = []
    restores: list[str] = []
    base_restores = 0

    def run_install(script: str) -> InstallResult:
        install_calls.append(script)
        if "install-a" in script:
            environment.add("first")
            return InstallResult(0, None, None, "")
        if "install-b-bad" in script:
            return InstallResult(1, "install-b-bad", 3, "bad provider")
        if "install-b-good" in script:
            environment.add("second")
            return InstallResult(0, None, None, "")
        raise AssertionError(script)

    def check(command: str) -> tuple[int, str]:
        name = command.removeprefix("check ")
        return (0, "present") if name in environment else (1, "missing")

    def restore_base() -> None:
        nonlocal base_restores
        base_restores += 1
        environment.clear()

    def create_checkpoint(name: str) -> str:
        snapshots[name] = set(environment)
        return name

    def restore_checkpoint(name: str) -> None:
        restores.append(name)
        environment.clear()
        environment.update(snapshots[name])

    def drop_checkpoint(name: str) -> None:
        snapshots.pop(name, None)

    executor = IncrementalPlanExecutor(
        run_install_script=run_install,
        exec_readonly=check,
        restore_base=restore_base,
        create_checkpoint=create_checkpoint,
        restore_checkpoint=restore_checkpoint,
        drop_checkpoint=drop_checkpoint,
        checkpoint_interval=50,
        expensive_block_seconds=999,
    )

    failed = executor.execute(graph, (), cycle=1)
    assert failed.failed_block_id == "pip.second"
    assert failed.failed_node_id == "pkg:second"
    assert failed.executed_block_ids == ("system.first",)
    assert failed.created_checkpoints

    patched_graph = failed.graph.with_node(
        replace(second, setup_commands=("install-b-good",), state=State.MISSING)
    )
    repaired = executor.execute(patched_graph, (), cycle=2)

    assert repaired.install_result.rc == 0
    assert repaired.reused_blocks == 1
    assert repaired.restored_checkpoint == failed.created_checkpoints[-1]
    assert repaired.executed_block_ids == ("pip.second",)
    assert restores == [failed.created_checkpoints[-1]]
    assert base_restores == 0
    assert sum("install-a" in script for script in install_calls) == 1

    repeated = executor.execute(repaired.graph, (), cycle=3)
    assert repeated.reused_blocks == 2
    assert repeated.executed_block_ids == ()
    assert len(install_calls) == 3


def test_manual_block_must_host_certify_non_reciped_target():
    node = Node(
        id="import:demo", type=NodeType.IMPORT, name="demo", layer=Layer.NAMING,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
        check_command="check demo",
    )
    graph = DepGraph(nodes=(node,))
    environment = set()

    def run_install(script):
        if "install-demo" in script:
            environment.add("demo")
        return InstallResult(0, None, None, "")

    executor = IncrementalPlanExecutor(
        run_install_script=run_install,
        exec_readonly=lambda command: (
            (0, "present") if "demo" in environment else (1, "missing")
        ),
        restore_base=environment.clear,
        checkpoint_interval=50,
        expensive_block_seconds=999,
    )
    broken = Block(
        block_id="pip.demo", wave="pip", commands=("do-nothing",),
        target_node_ids=(node.id,), check_commands=(node.check_command,),
        evidence_refs=("ev.demo",),
    )
    failed = executor.execute(graph, (broken,), cycle=1)
    assert failed.failed_block_id == "pip.demo"
    assert failed.failed_node_id == "import:demo"

    fixed = Block(
        block_id="pip.demo", wave="pip", commands=("install-demo",),
        target_node_ids=(node.id,), check_commands=(node.check_command,),
        evidence_refs=("ev.demo",),
    )
    repaired = executor.execute(failed.graph, (fixed,), cycle=2)
    assert repaired.install_result.rc == 0
    assert repaired.graph.get(node.id).state is State.SATISFIED


def test_partial_pytest_pass_rate_satisfies_incremental_test_block():
    node = Node(
        id="test:pytest",
        type=NodeType.TEST,
        name="pytest",
        layer=Layer.TESTS,
        discovered_by=DiscoveredBy.GOAL,
        state=State.MISSING,
        check_command="python -m pytest -q",
    )
    graph = DepGraph(nodes=(node,))

    def run_install(script):
        assert "python -m pytest -q" in script
        return InstallResult(
            rc=1,
            failing_command="python -m pytest -q",
            lineno=3,
            stderr="1 failed, 312 passed, 1 skipped in 125.35s",
        )

    executor = IncrementalPlanExecutor(
        run_install_script=run_install,
        exec_readonly=lambda command: (1, "should not certify tests here"),
        restore_base=lambda: None,
        checkpoint_interval=50,
        expensive_block_seconds=999,
    )
    block = Block(
        block_id="tests.pytest",
        wave="tests",
        commands=("python -m pytest -q",),
        target_node_ids=(node.id,),
        check_commands=(node.check_command,),
        evidence_refs=("ev.pytest",),
    )

    result = executor.execute(graph, (block,), cycle=1)

    assert result.install_result.rc == 0
    assert result.failed_block_id is None
    assert result.executed_block_ids == ("tests.pytest",)
    assert result.graph.get(node.id).state is State.SATISFIED
