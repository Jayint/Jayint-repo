from src.envstate.contracts import goals
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import ContractStatusEvent, Edge, Node
from src.envstate.world_model import Fact


def test_seed_template_nodes_and_edges():
    nodes, edges = goals.seed_goal_template((Fact("torch", ">=2.0"),))
    ids_ = {n.id for n in nodes}
    assert "verify:pytest_run" in ids_
    assert "contract:goal:repo_tests_run" in ids_
    assert "contract:pytest_runnable" in ids_
    assert "contract:python_package_importable:torch" in ids_
    goal = next(n for n in nodes if n.id == "contract:goal:repo_tests_run")
    assert goal.data["level"] == "goal" and goal.data["required"] is True
    deps = {e.target for e in edges if e.source == "contract:goal:repo_tests_run" and e.type == "depends_on"}
    assert {"contract:pytest_runnable", "contract:python_package_importable:torch"} <= deps


def test_readiness_false_until_goal_and_deps_satisfied():
    nodes, edges = goals.seed_goal_template((Fact("torch", ""),))
    g = ContractGraph(nodes=tuple(nodes), edges=tuple(edges))
    assert goals.evaluate_goal_readiness(g) is False
    # satisfy deps + goal
    sat = lambda cid: ContractStatusEvent(cid, "satisfied", "envrev:004", ("cmd:010",))
    g2 = ContractGraph(
        nodes=g.nodes + (Node("cmd:010", "CommandExecution", {"exit_code": 0}),),
        edges=g.edges,
        status_events=(
            sat("contract:pytest_runnable"),
            sat("contract:python_package_importable:torch"),
            sat("contract:goal:repo_tests_run"),
        ),
    )
    assert goals.evaluate_goal_readiness(g2) is True


def test_readiness_false_if_goal_satisfied_but_dep_not():
    nodes, edges = goals.seed_goal_template((Fact("torch", ""),))
    g = ContractGraph(
        nodes=tuple(nodes),
        edges=tuple(edges),
        status_events=(ContractStatusEvent("contract:goal:repo_tests_run", "satisfied", "envrev:004", ("cmd:010",)),),
    )
    assert goals.evaluate_goal_readiness(g) is False
