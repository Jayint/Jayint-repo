from src.envstate.contracts.projection import refresh_host_graph
from src.envstate.contracts.goals import evaluate_goal_readiness
from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.snapshot import EnvSnapshot
from src.envstate.world_model import Fact, initial_map, merge_map


def _map_with(required=(), installed=(), done=False, open_problems=()):
    m = initial_map("img", "/repo", "python 3.12", "pip", ("requirements.txt", "tests/"))
    return merge_map(m, required=required, installed=installed, done_flag=done, open_problems=open_problems)


def _ledger(events):
    led = ActionLedger()
    for e in events:
        led.append(e)
    return led


def test_builds_grounded_graph_and_is_idempotent():
    m = _map_with(required=(Fact("torch", ">=2.0"),), installed=(Fact("torch", "2.1.0"),))
    led = _ledger([ActionEvent(step=1, cmd="pip install torch", rc=0, env_revision_before=0, env_revision_after=1)])
    ex = lambda cmd: (0, "")  # all read-only validators pass
    m1 = refresh_host_graph(m, led, EnvSnapshot(), exec_readonly=ex, current_revision=1)
    g1 = m1.contract_graph
    assert g1.node("artifact:requirements.txt") is not None
    assert g1.node("requirement:python_dependency:torch") is not None
    assert g1.node("contract:goal:repo_tests_run") is not None
    assert g1.node("cmd:001") is not None
    n_nodes = len(g1.nodes)
    # idempotent: feeding the refreshed map back adds no new structural nodes
    m2 = refresh_host_graph(m1, led, EnvSnapshot(), exec_readonly=ex, current_revision=1)
    assert len(m2.contract_graph.nodes) == n_nodes


def test_done_flag_marks_goal_satisfied_when_deps_satisfied():
    m = _map_with(required=(Fact("torch", ""),), installed=(Fact("torch", "2.1.0"),), done=True)
    led = _ledger([ActionEvent(step=9, cmd="python -m pytest -q", rc=0, env_revision_before=1, env_revision_after=1)])
    ex = lambda cmd: (0, "")
    m1 = refresh_host_graph(m, led, EnvSnapshot(), exec_readonly=ex, current_revision=1)
    assert evaluate_goal_readiness(m1.contract_graph) is True


def test_host_patch_passes_host_validation():
    # regression guard: the composed host patch must be valid
    m = _map_with(required=(Fact("torch", ""),), open_problems=())
    m1 = refresh_host_graph(m, ActionLedger(), EnvSnapshot(), exec_readonly=lambda c: (0, ""), current_revision=0)
    # if validation had failed, refresh logs and drops; assert the goal contract survived
    assert m1.contract_graph.node("contract:goal:repo_tests_run") is not None
