# tests/test_maintainer_graph_patch.py
import json
from types import SimpleNamespace

from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node
from src.envstate.maintainer import parse_v1_maintainer_reply
from src.envstate.world_model import CommandRecord, TaskReport, initial_map, merge_map


def _map_with_failure():
    g = ContractGraph(nodes=(
        Node("contract:python_package_importable:torch", "Contract",
             {"level": "atomic", "kind": "python_package_importable", "subject": "torch"}),
        Node("failure:cmd:007", "Failure", {"kind": "command_failed", "command_id": "cmd:007"}),
    ))
    return merge_map(initial_map("img", "/r", "py", "pip", ()), contract_graph=g)


def _report():
    return TaskReport("install torch", "blocked",
                      (CommandRecord("python -c 'import torch'", 1, "ModuleNotFoundError: torch"),), "still missing")


def test_valid_graph_patch_is_applied():
    m = _map_with_failure()
    reply = "```json\n" + json.dumps({
        "open_problems": [{"signature": "ModuleNotFoundError: torch", "kind": "import_failure"}],
        "resolved": [], "planner_notes": [],
        "graph_patch": {
            "add_edges": [{"source": "failure:cmd:007", "type": "violates",
                           "target": "contract:python_package_importable:torch"}],
            "add_status_events": [{"contract_id": "contract:python_package_importable:torch",
                                   "status": "violated", "revision_id": "envrev:003",
                                   "evidence_ids": ["failure:cmd:007"]}],
        },
    }) + "\n```"
    out = parse_v1_maintainer_reply(reply, m, _report())
    assert out.contract_graph.latest_status("contract:python_package_importable:torch").status == "violated"
    assert any(e.type == "violates" for e in out.contract_graph.edges)


def test_invalid_graph_patch_is_dropped_but_flat_fields_apply():
    m = _map_with_failure()
    errs = []
    reply = "```json\n" + json.dumps({
        "open_problems": [{"signature": "boom", "kind": "import_failure"}],
        "graph_patch": {"add_nodes": [{"id": "capability:x", "type": "Capability"}]},  # forbidden for maintainer
    }) + "\n```"
    out = parse_v1_maintainer_reply(reply, m, _report(), on_patch_error=errs.append)
    assert errs  # patch rejected
    assert out.contract_graph.node("capability:x") is None  # not applied
    assert any(op.signature == "boom" for op in out.open_problems)  # flat field still applied
