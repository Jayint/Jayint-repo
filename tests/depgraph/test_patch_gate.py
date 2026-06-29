"""Tests: DataAsset support in patch_gate (_KIND_PREFIX) + ids.data_asset_id.

Task 1 of GSM Slice C. Covers two new behaviors:
  1. ids.data_asset_id(name) -> "data:{name}"
  2. admit_proposal accepts a DataAsset node with a "data:" id prefix.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import DepGraph, NodeType
from python_deps.depgraph.ids import data_asset_id
from python_deps.depgraph.patch import PatchProposal, NodeSpec
from python_deps.depgraph.patch_gate import admit_proposal


def test_data_asset_id_prefix():
    assert data_asset_id("fixtures.db") == "data:fixtures.db"


def test_patch_gate_admits_data_asset_node():
    ev = frozenset({"env.00"})
    prop = PatchProposal(add_requirements=(NodeSpec(
        id="data:fixtures.db", type="DataAsset", name="fixtures.db", layer="config",
        check_command="test -f fixtures.db", evidence_ref="env.00", promotion="hint"),))
    res = admit_proposal(DepGraph(), prop, known_evidence_ids=ev)
    assert res.accepted, res.errors
    node = res.graph.get("data:fixtures.db")
    assert node is not None and node.type is NodeType.DATA_ASSET
