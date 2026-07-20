from dataclasses import replace
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for _path in (str(_ROOT), str(_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph,
    DiscoveredBy,
    Edge,
    Layer,
    Node,
    NodeType,
    State,
)
from src.envstate.incremental_executor import IncrementalPlanExecutor  # noqa: E402
from src.envstate.ledger import ActionLedger  # noqa: E402
from src.envstate.orchestrator import VERIFY_TEST_CMD, run_v3  # noqa: E402
from src.envstate.world_model import initial_map, merge_map  # noqa: E402
from src.sandbox import InstallResult  # noqa: E402


def _node(node_id, name, layer, command):
    node_type = {
        Layer.SYSTEM: NodeType.SYSTEM_LIB,
        Layer.TOOLCHAIN: NodeType.TOOL,
    }.get(layer, NodeType.PACKAGE)
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


def _harness():
    first = _node("syslib:first", "first", Layer.SYSTEM, "install-a")
    second = _node("pkg:second", "second", Layer.PIP, "install-b-bad")
    third = _node("pkg:third", "third", Layer.PIP, "install-c")
    graph = DepGraph(nodes=(first, second, third))

    state = {
        "working": set(),
        "snapshots": {},
        "candidates": {},
        "working_calls": [],
        "candidate_calls": [],
        "restores": [],
        "candidate_bases": [],
        "promoted": [],
        "aborted": [],
        "dropped": [],
    }

    def apply(script, environment, calls):
        calls.append(script)
        if "install-a-new" in script:
            environment.add("first")
        elif "install-a" in script:
            environment.add("first")
        if "install-b-bad" in script or "install-b-still-bad" in script:
            command = "install-b-still-bad" if "still" in script else "install-b-bad"
            return InstallResult(1, command, 3, "candidate provider failed")
        if "install-b-good" in script:
            environment.add("second")
        if "install-c" in script:
            environment.add("third")
        return InstallResult(0, None, None, "")

    def run_install(script):
        return apply(script, state["working"], state["working_calls"])

    def check_in(environment, command):
        name = command.removeprefix("check ")
        return (0, "present") if name in environment else (1, "missing")

    def create_checkpoint(name):
        state["snapshots"][name] = set(state["working"])
        return name

    def restore_checkpoint(name):
        state["restores"].append(name)
        state["working"] = set(state["snapshots"][name])

    def drop_checkpoint(name):
        state["dropped"].append(name)
        state["snapshots"].pop(name, None)

    def create_candidate(transaction_id, checkpoint_name):
        base = checkpoint_name or "base"
        state["candidate_bases"].append(base)
        environment = (
            set(state["snapshots"][checkpoint_name]) if checkpoint_name else set()
        )
        state["candidates"][transaction_id] = environment
        return transaction_id

    def candidate_run(handle, script):
        return apply(
            script,
            state["candidates"][handle],
            state["candidate_calls"],
        )

    def candidate_check(handle, command):
        return check_in(state["candidates"][handle], command)

    def promote(handle):
        state["working"] = state["candidates"].pop(handle)
        state["promoted"].append(handle)

    def abort(handle):
        state["candidates"].pop(handle, None)
        state["aborted"].append(handle)

    executor = IncrementalPlanExecutor(
        run_install_script=run_install,
        exec_readonly=lambda command: check_in(state["working"], command),
        restore_base=lambda: state.update(working=set()),
        create_checkpoint=create_checkpoint,
        restore_checkpoint=restore_checkpoint,
        drop_checkpoint=drop_checkpoint,
        create_candidate=create_candidate,
        candidate_run_install_script=candidate_run,
        candidate_exec_readonly=candidate_check,
        promote_candidate=promote,
        abort_candidate=abort,
        checkpoint_interval=50,
        expensive_block_seconds=999,
    )
    state["check"] = lambda command: check_in(state["working"], command)
    state["run_install"] = run_install
    return graph, executor, state


def test_success_atomically_promotes_candidate_and_continues_after_repaired_block():
    graph, executor, state = _harness()
    failed = executor.execute(graph, (), cycle=1)
    assert failed.failed_block_id == "pip.second"
    official_graph = failed.graph
    checkpoint = failed.created_checkpoints[-1]

    second = official_graph.get("pkg:second")
    candidate_graph = official_graph.with_node(
        replace(second, setup_commands=("install-b-good",), state=State.MISSING)
    )
    transaction = executor.validate_candidate(
        official_graph,
        (),
        candidate_graph,
        (),
        failed_block_id="pip.second",
        target_node_id="pkg:second",
        cycle=2,
        transaction_id="txn-success",
    )

    assert transaction.committed is True
    assert transaction.base_checkpoint == checkpoint
    assert transaction.executed_block_ids == ("pip.second",)
    assert state["promoted"] == ["txn-success"]
    assert state["working"] == {"first", "second"}
    assert sum("install-a" in call for call in state["candidate_calls"]) == 0
    assert official_graph.get("pkg:second").setup_commands == ("install-b-bad",)

    continued = executor.execute(transaction.graph, (), cycle=3)
    assert continued.install_result.rc == 0
    assert continued.executed_block_ids == ("pip.third",)
    assert state["working"] == {"first", "second", "third"}
    assert sum("install-b-good" in call for call in state["candidate_calls"]) == 1


def test_failed_candidate_does_not_pollute_graph_or_working_container():
    graph, executor, state = _harness()
    failed = executor.execute(graph, (), cycle=1)
    official_graph = failed.graph
    official_environment = set(state["working"])
    official_prefix = executor._executed_prefix
    official_signatures = executor._plan_signatures

    second = official_graph.get("pkg:second")
    candidate_graph = official_graph.with_node(
        replace(second, setup_commands=("install-b-still-bad",), state=State.MISSING)
    )
    transaction = executor.validate_candidate(
        official_graph,
        (),
        candidate_graph,
        (),
        failed_block_id="pip.second",
        target_node_id="pkg:second",
        cycle=2,
        transaction_id="txn-abort",
    )

    assert transaction.committed is False
    assert state["aborted"] == ["txn-abort"]
    assert state["promoted"] == []
    assert state["candidates"] == {}
    assert state["working"] == official_environment
    assert official_graph.get("pkg:second").setup_commands == ("install-b-bad",)
    assert executor._executed_prefix == official_prefix
    assert executor._plan_signatures == official_signatures


def test_change_before_checkpoint_falls_back_to_base_and_replays_only_from_base():
    graph, executor, state = _harness()
    failed = executor.execute(graph, (), cycle=1)
    official_graph = failed.graph
    first = official_graph.get("syslib:first")
    second = official_graph.get("pkg:second")
    candidate_graph = official_graph.with_node(
        replace(first, setup_commands=("install-a-new",), state=State.MISSING)
    ).with_node(
        replace(second, setup_commands=("install-b-good",), state=State.MISSING)
    )

    transaction = executor.validate_candidate(
        official_graph,
        (),
        candidate_graph,
        (),
        failed_block_id="pip.second",
        target_node_id="pkg:second",
        cycle=2,
        transaction_id="txn-base",
    )

    assert transaction.committed is True
    assert transaction.base_checkpoint == "base"
    assert transaction.base_prefix_len == 0
    assert transaction.executed_block_ids == ("system.first", "pip.second")
    assert state["candidate_bases"][-1] == "base"
    assert any("install-a-new" in call for call in state["candidate_calls"])
    assert state["dropped"], "the stale checkpoint after the changed first block must be invalidated"


def test_committed_candidate_still_requires_and_passes_terminal_clean_replay():
    graph, executor, state = _harness()
    failed = executor.execute(graph, (), cycle=1)
    second = failed.graph.get("pkg:second")
    candidate_graph = failed.graph.with_node(
        replace(second, setup_commands=("install-b-good",), state=State.MISSING)
    )
    transaction = executor.validate_candidate(
        failed.graph,
        (),
        candidate_graph,
        (),
        failed_block_id="pip.second",
        target_node_id="pkg:second",
        cycle=2,
        transaction_id="txn-clean-replay",
    )
    completed = executor.execute(transaction.graph, (), cycle=3)
    assert completed.install_result.rc == 0

    base = initial_map(
        base_image="python:3.11-slim",
        workdir="/app",
        language="python",
        build_system="pip",
        repo_layout=(),
    )
    world = merge_map(base, dep_graph=completed.graph)
    resets = []

    class _Agent:
        client = None

    class _Maintainer:
        def update(self, current, report):
            return current

    def sandbox_execute(command):
        if command == VERIFY_TEST_CMD:
            return True, "collected 1 item\n1 passed in 0.01s"
        return True, "ok"

    def reset_to_base():
        resets.append(True)
        state["working"] = set()

    final_map, stop = run_v3(
        _Agent(),
        _Maintainer(),
        world,
        ActionLedger(),
        sandbox_execute,
        max_cycles=2,
        exec_readonly=state["check"],
        reset_to_base=reset_to_base,
        run_install_script=state["run_install"],
        incremental_execute=executor.execute,
        candidate_validate=executor.validate_candidate,
    )

    assert stop == "planner_done"
    assert resets == [True]
    assert state["working"] == {"first", "second", "third"}
    assert all(node.state is State.SATISFIED for node in final_map.dep_graph.nodes)


def test_changed_predecessor_skips_stale_latest_checkpoint_and_uses_earlier_one():
    system = _node("syslib:system", "system", Layer.SYSTEM, "install-system")
    tool = _node("tool:compiler", "compiler", Layer.TOOLCHAIN, "install-tool-old")
    package = _node("pkg:package", "package", Layer.PIP, "install-package-bad")
    graph = DepGraph(nodes=(system, tool, package))
    working = set()
    snapshots = {}
    candidates = {}
    bases = []

    def apply(script, environment):
        if "install-system" in script:
            environment.add("system")
        if "install-tool-old" in script or "install-tool-new" in script:
            environment.add("compiler")
        if "install-package-bad" in script:
            return InstallResult(1, "install-package-bad", None, "bad package recipe")
        if "install-package-good" in script:
            environment.add("package")
        return InstallResult(0, None, None, "")

    def check(environment, command):
        return (0, "present") if command.removeprefix("check ") in environment else (1, "missing")

    def checkpoint(name):
        snapshots[name] = set(working)
        return name

    def create_candidate(transaction_id, checkpoint_name):
        bases.append(checkpoint_name or "base")
        candidates[transaction_id] = set(snapshots[checkpoint_name]) if checkpoint_name else set()
        return transaction_id

    def promote(handle):
        working.clear()
        working.update(candidates.pop(handle))

    executor = IncrementalPlanExecutor(
        run_install_script=lambda script: apply(script, working),
        exec_readonly=lambda command: check(working, command),
        restore_base=working.clear,
        create_checkpoint=checkpoint,
        restore_checkpoint=lambda name: (working.clear(), working.update(snapshots[name])),
        drop_checkpoint=lambda name: snapshots.pop(name, None),
        create_candidate=create_candidate,
        candidate_run_install_script=lambda handle, script: apply(script, candidates[handle]),
        candidate_exec_readonly=lambda handle, command: check(candidates[handle], command),
        promote_candidate=promote,
        abort_candidate=lambda handle: candidates.pop(handle, None),
        checkpoint_interval=50,
        expensive_block_seconds=999,
    )
    failed = executor.execute(graph, (), cycle=1)
    assert len(failed.created_checkpoints) == 2
    earlier, latest = failed.created_checkpoints

    candidate_graph = failed.graph.with_node(replace(
        failed.graph.get("tool:compiler"),
        setup_commands=("install-tool-new",),
        state=State.MISSING,
    )).with_node(replace(
        failed.graph.get("pkg:package"),
        setup_commands=("install-package-good",),
        state=State.MISSING,
    ))
    result = executor.validate_candidate(
        failed.graph,
        (),
        candidate_graph,
        (),
        failed_block_id="pip.package",
        target_node_id="pkg:package",
        cycle=2,
        transaction_id="txn-earlier-checkpoint",
    )

    assert result.committed is True
    assert result.base_checkpoint == earlier
    assert result.base_checkpoint != latest
    assert result.executed_block_ids == ("toolchain.compiler", "pip.package")
    assert bases[-1] == earlier


def test_goal_without_block_checks_goal_but_defers_unreplayed_affected_suffix():
    """A provider repair from an old checkpoint must not invent suffix failures.

    This models an import repair that changes setuptools before a later package
    which also depends on setuptools.  The candidate must prove the import goal,
    defer the not-yet-replayed package check, and let normal execution continue
    through that suffix after promotion.
    """
    bootstrap = _node("syslib:bootstrap", "bootstrap", Layer.SYSTEM, "install-bootstrap")
    setuptools = _node("pkg:setuptools", "setuptools", Layer.PIP, "install-setuptools-old")
    apscheduler = replace(
        _node("pkg:apscheduler", "apscheduler", Layer.PIP, "install-apscheduler"),
        check_command="check apscheduler-package",
    )
    future = _node("pkg:fakeredis", "fakeredis", Layer.PIP, "install-fakeredis")
    goal = Node(
        id="import:apscheduler",
        type=NodeType.IMPORT,
        name="apscheduler",
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        check_command="check apscheduler",
    )
    future_goal = Node(
        id="import:fakeredis",
        type=NodeType.IMPORT,
        name="fakeredis",
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.PROBE,
        state=State.SATISFIED,
        check_command="check fakeredis-import",
    )
    graph = DepGraph(
        nodes=(bootstrap, setuptools, apscheduler, future, goal, future_goal),
        edges=(
            Edge("pkg:apscheduler", "pkg:setuptools"),
            Edge("pkg:fakeredis", "pkg:setuptools"),
            Edge("import:fakeredis", "pkg:fakeredis"),
            Edge("import:apscheduler", "pkg:apscheduler"),
        ),
    )

    working = set()
    snapshots = {}
    candidates = {}
    candidate_checks = []

    def apply(script, environment):
        if "install-bootstrap" in script:
            environment.add("bootstrap")
        if "install-setuptools-old" in script:
            environment.add("setuptools-old")
        if "install-setuptools-good" in script:
            environment.discard("setuptools-old")
            environment.add("setuptools-good")
        if "install-apscheduler" in script:
            environment.add("apscheduler-package")
        if "install-fakeredis" in script:
            environment.add("fakeredis")
        return InstallResult(0, None, None, "")

    def check(environment, command):
        name = command.removeprefix("check ")
        if name == "setuptools":
            return (0, "present") if (
                "setuptools-old" in environment or "setuptools-good" in environment
            ) else (1, "missing")
        if name == "apscheduler":
            return (0, "present") if (
                "setuptools-good" in environment
                and "apscheduler-package" in environment
            ) else (1, "missing")
        if name == "apscheduler-package":
            return (0, "present") if "apscheduler-package" in environment else (1, "missing")
        if name == "fakeredis-import":
            return (0, "present") if "fakeredis" in environment else (1, "missing")
        return (0, "present") if name in environment else (1, "missing")

    def checkpoint(name):
        snapshots[name] = set(working)
        return name

    def create_candidate(transaction_id, checkpoint_name):
        candidates[transaction_id] = (
            set(snapshots[checkpoint_name]) if checkpoint_name else set()
        )
        return transaction_id

    def candidate_check(handle, command):
        candidate_checks.append(command)
        return check(candidates[handle], command)

    def promote(handle):
        working.clear()
        working.update(candidates.pop(handle))

    executor = IncrementalPlanExecutor(
        run_install_script=lambda script: apply(script, working),
        exec_readonly=lambda command: check(working, command),
        restore_base=working.clear,
        create_checkpoint=checkpoint,
        restore_checkpoint=lambda name: (working.clear(), working.update(snapshots[name])),
        drop_checkpoint=lambda name: snapshots.pop(name, None),
        create_candidate=create_candidate,
        candidate_run_install_script=lambda handle, script: apply(script, candidates[handle]),
        candidate_exec_readonly=candidate_check,
        promote_candidate=promote,
        abort_candidate=lambda handle: candidates.pop(handle, None),
        checkpoint_interval=1,
        expensive_block_seconds=999,
    )

    completed = executor.execute(graph, (), cycle=1)
    assert completed.install_result.rc == 0
    assert working == {
        "bootstrap", "setuptools-old", "apscheduler-package", "fakeredis"
    }

    candidate_graph = completed.graph.with_node(replace(
        completed.graph.get("pkg:setuptools"),
        setup_commands=("install-setuptools-good",),
        state=State.MISSING,
    ))
    transaction = executor.validate_candidate(
        completed.graph,
        (),
        candidate_graph,
        (),
        failed_block_id="import:apscheduler",
        target_node_id="import:apscheduler",
        cycle=2,
        transaction_id="txn-goal-from-old-checkpoint",
    )

    assert transaction.committed is True
    assert transaction.executed_block_ids == ("pip.setuptools", "pip.apscheduler")
    assert "check apscheduler" in candidate_checks
    assert "check fakeredis" not in candidate_checks
    assert "check fakeredis-import" not in candidate_checks
    assert working == {"bootstrap", "setuptools-good", "apscheduler-package"}

    continued = executor.execute(transaction.graph, (), cycle=3)
    assert continued.install_result.rc == 0
    assert continued.executed_block_ids == ("pip.fakeredis",)
    assert working == {
        "bootstrap", "setuptools-good", "apscheduler-package", "fakeredis"
    }
