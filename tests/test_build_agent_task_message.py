from src.envstate.build_agent import BuildAgent
from src.envstate.world_model import Task, TransitionProposal


def test_task_message_includes_targets_and_proposal():
    ba = BuildAgent.__new__(BuildAgent)  # no LLM needed for the pure formatter
    tp = TransitionProposal("install_python_package", "torch", "install torch", ("pip install torch",))
    task = Task("install torch", "pytest runs", "deps", ("torch missing",),
                target_node_ids=("contract:python_package_importable:torch",), transition_proposal=tp)
    msg = ba._build_task_message(task)
    assert "contract:python_package_importable:torch" in msg
    assert "install_python_package" in msg and "pip install torch" in msg


def test_task_message_backcompat_without_grounding():
    ba = BuildAgent.__new__(BuildAgent)
    msg = ba._build_task_message(Task("g", "d", "deps", ()))
    assert "Task goal: g" in msg and "Target graph nodes" not in msg
