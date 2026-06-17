# tests/test_contract_graph_e2e.py
from src.envstate.contracts.goals import evaluate_goal_readiness
from src.envstate.ledger import ActionLedger
from src.envstate.orchestrator import run_v1
from src.envstate.snapshot import EnvSnapshot
from src.envstate.world_model import (
    CommandRecord, Fact, PlannerDecision, Task, TaskReport, TransitionProposal,
    initial_map, merge_map,
)


class _ScriptedPlanner:
    """Cycle 1: install torch (grounded). Cycle 2: advisory done."""
    def __init__(self):
        self._calls = 0

    def decide(self, m):
        self._calls += 1
        if self._calls == 1:
            tp = TransitionProposal("install_python_package", "torch", "install torch", ("pip install torch",))
            return PlannerDecision("task", task=Task(
                "install torch", "pytest runs", "deps", ("torch missing",),
                target_node_ids=("contract:python_package_importable:torch",), transition_proposal=tp))
        return PlannerDecision("done", satisfied_goal_contract_ids=("contract:goal:repo_tests_run",))


class _ScriptedBuildAgent:
    def run(self, task, sandbox_execute, ledger, step_offset=0):
        ok, out = sandbox_execute("pip install torch")
        from src.envstate.ledger import make_action_event
        ledger.append(make_action_event(step=step_offset + 1, cmd="pip install torch", success=ok, stdout=out,
                                         env_revision_before=0, env_revision_after=1, mutation_class="pip_install",
                                         container_id="c1"))
        return TaskReport("install torch", "done", (CommandRecord("pip install torch", 0, out),), "installed")


class _PassthroughMaintainer:
    def update(self, m, report):
        return m  # host graph already carries the truth; no semantic patch needed for this scenario


def test_torch_scenario_reaches_done_with_satisfied_graph():
    # installed state flips after cycle 1 (probe reflects torch present).
    state = {"installed": ()}

    def probe():
        return EnvSnapshot(installed=state["installed"])

    def sandbox(cmd):
        if cmd.startswith("pip install"):
            state["installed"] = (Fact("torch", "2.1.0"),)
            return True, "Successfully installed torch-2.1.0"
        if "pytest" in cmd:
            return True, "collected 5 items\n5 passed in 0.4s"
        return True, "ok"

    def exec_readonly(cmd):
        # import torch passes only once installed
        if "import torch" in cmd:
            return (0, "") if state["installed"] else (1, "ModuleNotFoundError: torch")
        return (0, "")  # pytest --collect-only

    m0 = merge_map(
        initial_map("python:3.12", "/repo", "python 3.12", "pip", ("requirements.txt", "tests/")),
        required=(Fact("torch", ">=2.0"),),
    )
    manifest = type("M", (), {"required": (Fact("torch", ">=2.0"),), "build_system": "pip"})()
    final_map, reason = run_v1(
        _ScriptedPlanner(), _ScriptedBuildAgent(), _PassthroughMaintainer(), m0, ActionLedger(),
        sandbox_execute=sandbox, max_cycles=4, probe=probe, manifest=manifest,
        exec_readonly=exec_readonly, enable_contract_graph=True,
    )
    g = final_map.contract_graph
    assert reason == "planner_done"
    assert final_map.done_flag is True
    assert evaluate_goal_readiness(g) is True
    # the import contract ended satisfied, backed by a passing validator command
    ev = g.latest_status("contract:python_package_importable:torch")
    assert ev is not None and ev.status == "satisfied"
    # the transition was committed and linked to the install command
    assert g.node("transition:install_python_package:torch") is not None
    assert any(e.type == "executed_as" for e in g.edges)
