from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import ContractStatusEvent, Edge, Node
from src.envstate.contracts.render import render_graph_for_planner, serialize_graph_for_maintainer


def _g():
    return ContractGraph(
        nodes=(
            Node("contract:goal:repo_tests_run", "Contract", {"level": "goal", "required": True, "description": "tests run"}),
            Node("contract:python_package_importable:torch", "Contract",
                 {"level": "atomic", "subject": "torch", "description": "torch importable"}),
            Node("failure:cmd:007", "Failure", {"summary": "ModuleNotFoundError torch", "command_id": "cmd:007"}),
            Node("dead", "Contract", {"level": "atomic"}, invalidated=True),
        ),
        edges=(Edge("contract:goal:repo_tests_run", "depends_on", "contract:python_package_importable:torch"),),
        status_events=(ContractStatusEvent("contract:python_package_importable:torch", "violated", "envrev:003", ("failure:cmd:007",)),),
    )


def test_planner_view_lists_ids_statuses_and_omits_invalidated():
    out = render_graph_for_planner(_g())
    assert "contract:python_package_importable:torch" in out
    assert "violated" in out
    assert "failure:cmd:007" in out
    assert "dead" not in out  # invalidated excluded
    assert "Contract Graph" in out


def test_planner_view_empty_graph():
    assert "empty" in render_graph_for_planner(ContractGraph()).lower()


def test_maintainer_view_has_active_nodes_and_latest_status():
    d = serialize_graph_for_maintainer(_g())
    node_ids = {n["id"] for n in d["nodes"]}
    assert "contract:python_package_importable:torch" in node_ids
    assert "dead" not in node_ids
    assert d["latest_status"]["contract:python_package_importable:torch"] == "violated"
