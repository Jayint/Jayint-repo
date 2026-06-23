"""Pure seed adapter: built DepGraph -> atomic Contract nodes.

The proactive twin of ``extract.promote_atomic_contracts`` — it sources atomic
Contract obligations from the certified dependency graph (all of them, not only
the ones that already failed) instead of from stderr signatures.  Contracts
only: no Blockers, no edges, no state assertions; the host still certifies.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Put <repo>/src on the path so the canonical ``python_deps.depgraph.*`` import
# resolves (mirrors tests/depgraph/conftest.py; this test lives one level up).
# ``src.envstate.*`` resolves via the repo root added by tests/conftest.py.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.envstate.contracts.depgraph_seed import seed_contracts_from_depgraph  # noqa: E402
from src.envstate.contracts.graph import ContractGraph  # noqa: E402
from src.envstate.contracts.nodes import Node  # noqa: E402
from src.envstate.contracts import ids  # noqa: E402
from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph,
    Node as DNode,
    NodeType,
    Layer,
    DiscoveredBy,
    State,
)


def _imp(name, state=State.MISSING):
    return DNode(id=f"import:{name}", type=NodeType.IMPORT, name=name,
                 layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
                 state=state, check_command=f'python -c "import {name}"')


def _syslib(soname):
    return DNode(id=f"syslib:{soname}", type=NodeType.SYSTEM_LIB, name=soname,
                 layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE,
                 state=State.MISSING, check_command=f"ldconfig -p | grep {soname}")


def _tool(name):
    return DNode(id=f"tool:{name}", type=NodeType.TOOL, name=name,
                 layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.PROBE,
                 state=State.MISSING, check_command=f"command -v {name}")


def test_import_becomes_python_import_contract():
    g = DepGraph(nodes=(_imp("cv2"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert len(out) == 1
    n = out[0]
    assert n.id == ids.contract_id("python_import", "cv2")
    assert n.type == "Contract"
    assert n.data["level"] == "atomic"
    assert n.data["kind"] == "python_import"
    assert n.data["subject"] == "cv2"
    assert n.data["layer"] == "deps"


def test_syslib_becomes_system_library_contract():
    g = DepGraph(nodes=(_syslib("libGL.so.1"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert out[0].data["kind"] == "system_library"
    assert out[0].data["layer"] == "system"
    assert out[0].id == ids.contract_id("system_library", "libGL.so.1")


def test_tool_becomes_binary_contract():
    g = DepGraph(nodes=(_tool("pg_config"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert out[0].data["kind"] == "binary"
    assert out[0].data["layer"] == "system"


def test_skips_non_obligation_node_types():
    test_node = DNode(id="test:repo_tests_pass", type=NodeType.TEST, name="repo_tests_pass",
                      layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL)
    proj = DNode(id="project:x", type=NodeType.PROJECT, name="x",
                 layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN)
    pkg = DNode(id="pkg:numpy==2.0", type=NodeType.PACKAGE, name="numpy",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="2.0")
    g = DepGraph(nodes=(test_node, proj, pkg))
    assert seed_contracts_from_depgraph(ContractGraph.empty(), g) == []


def test_seeds_all_states_not_only_missing():
    g = DepGraph(nodes=(_imp("numpy", state=State.SATISFIED),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert len(out) == 1  # satisfied import is still an obligation


def test_idempotent_skips_existing_contract_id():
    existing = Node(ids.contract_id("python_import", "cv2"), "Contract",
                    {"level": "atomic", "kind": "python_import", "subject": "cv2",
                     "layer": "deps", "check": "", "source_refs": ["signature:x"],
                     "evidence_refs": [], "description": "x", "metadata": {}})
    g = DepGraph(nodes=(_imp("cv2"),))
    out = seed_contracts_from_depgraph(ContractGraph(nodes=(existing,)), g)
    assert out == []


def test_dedupes_within_one_pass():
    # Two depgraph nodes that canonicalize to the same contract id.
    g = DepGraph(nodes=(_imp("cv2"), _imp("cv2")))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert len(out) == 1


def test_provenance_records_depgraph_node_id():
    g = DepGraph(nodes=(_imp("cv2"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert out[0].data["source_refs"] == ["depgraph:import:cv2"]


def test_carries_check_command():
    g = DepGraph(nodes=(_imp("cv2"),))
    out = seed_contracts_from_depgraph(ContractGraph.empty(), g)
    assert out[0].data["check"] == 'python -c "import cv2"'
