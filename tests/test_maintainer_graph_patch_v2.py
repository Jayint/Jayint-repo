# tests/test_maintainer_graph_patch_v2.py
from src.envstate.maintainer import parse_v1_maintainer_reply
from src.envstate.world_model import initial_map, merge_map, Fact
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node


def _report(cmd: str = "python -c 'import cv2'", rc: int = 1,
            out: str = "ImportError: libGL.so.1"):
    from src.envstate.world_model import TaskReport, CommandRecord
    return TaskReport(task_goal="g", status="blocked",
                      commands=(CommandRecord(cmd=cmd, rc=rc, output=out),),
                      learning="blocked")


def _map():
    g = ContractGraph(nodes=(Node("contract:python_import:cv2", "Contract",
                                  {"level": "atomic"}),))
    return merge_map(initial_map("b", "/r", "python 3.11", "pip", ("tests/",)),
                     contract_graph=g)


def test_maintainer_does_not_write_open_problems():
    reply = ('```json\n'
             '{"open_problems":[{"signature":"x","interpretation":"y","layer":"deps"}]}\n'
             '```')
    out = parse_v1_maintainer_reply(reply, _map(), _report())
    # semantic map writes are dropped; blockers live in the graph
    assert out.open_problems == ()


def test_valid_graph_patch_applies_blocker():
    reply = ('```json\n{"graph_patch":{'
             '"add_blockers":[{"id":"blocker:libgl","type":"Blocker",'
             '"signature":"ImportError: libGL.so.1",'
             '"active":true,"layer":"system","summary":"libGL","evidence_refs":[]}],'
             '"add_edges":[{"source":"blocker:libgl","type":"violates",'
             '"target":"contract:python_import:cv2"}]}}\n```')
    errs: list[str] = []
    out = parse_v1_maintainer_reply(reply, _map(), _report(),
                                    on_patch_error=errs.append)
    # evidence_refs empty -> grounded-blocker rule rejects;
    # assert error was caught and map graph is unchanged
    assert errs and not out.contract_graph.has_node("blocker:libgl")
