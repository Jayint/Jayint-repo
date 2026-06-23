"""Wiring: refresh_host_graph seeds depgraph obligations into the contract graph.

Task 3 connects the pure adapter (Task 2, ``seed_contracts_from_depgraph``) to
the host projection.  When ``world_map.dep_graph`` is present, every depgraph
obligation appears as an atomic Contract after a host refresh; when it is None
the off-state path is byte-identical (nothing extra seeded).
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

from src.envstate.contracts import ids  # noqa: E402
from src.envstate.contracts.projection import refresh_host_graph  # noqa: E402
from src.envstate.ledger import ActionLedger  # noqa: E402
from src.envstate.world_model import initial_map  # noqa: E402
from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph,
    DiscoveredBy,
    Layer,
    Node as DNode,
    NodeType,
    State,
)


def _base_kwargs() -> dict:
    return dict(
        base_image="python:3.11",
        workdir="/repo",
        language="python 3.11",
        build_system="pip",
        repo_layout=("tests/", "requirements.txt"),
    )


def _depgraph_with_missing_import() -> DepGraph:
    n = DNode(id="import:cv2", type=NodeType.IMPORT, name="cv2",
              layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
              state=State.MISSING, check_command='python -c "import cv2"')
    return DepGraph(nodes=(n,))


def test_refresh_seeds_depgraph_contract():
    m = initial_map(**_base_kwargs(), dep_graph=_depgraph_with_missing_import())
    m2 = refresh_host_graph(m, ActionLedger(), snapshot=None, exec_readonly=None,
                            current_revision=0)
    assert m2.contract_graph.has_node(ids.contract_id("python_import", "cv2"))


def test_refresh_without_dep_graph_seeds_nothing_extra():
    m = initial_map(**_base_kwargs())  # dep_graph is None
    m2 = refresh_host_graph(m, ActionLedger(), snapshot=None, exec_readonly=None,
                            current_revision=0)
    assert not m2.contract_graph.has_node(ids.contract_id("python_import", "cv2"))
