import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import graph.advise as advise
from graph.model import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
from graph.runtime_plan import RuntimePlan


def test_classify_hook_invoked_and_plan_returned(monkeypatch):
    # Task 4: classify returns a RuntimePlan; service_obligations passes the GRAPH
    # through unchanged and threads the plan out (return_plan=True).
    base = DepGraph()

    class _FakeScratch:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(advise, "DockerExecutor", lambda *a, **k: _FakeScratch())
    monkeypatch.setattr(advise, "build_dep_graph", lambda *a, **k: base)
    monkeypatch.setattr(advise, "render_dep_graph_advisory", lambda g: "ADV")

    tag = Node(id="service:tagged", type=NodeType.SERVICE, name="tagged", layer=Layer.SERVICES,
               discovered_by=DiscoveredBy.RUNTIME, state=State.MISSING)
    def _classify(graph, repo_path):
        return RuntimePlan(service_obligations=(tag,))

    adv, graph, plan = advise.build_advisory_for_repo(
        "/repo", "python:3.11-slim", classify=_classify, return_plan=True)
    assert adv == "ADV"
    assert graph is base                                  # graph passthrough (unchanged)
    assert plan.get_service("service:tagged") is not None  # classify's plan threaded out


def test_classify_none_is_passthrough(monkeypatch):
    base = DepGraph()
    class _FakeScratch:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(advise, "DockerExecutor", lambda *a, **k: _FakeScratch())
    monkeypatch.setattr(advise, "build_dep_graph", lambda *a, **k: base)
    monkeypatch.setattr(advise, "render_dep_graph_advisory", lambda g: "ADV")
    adv, graph = advise.build_advisory_for_repo("/repo", "python:3.11-slim")   # classify defaults None
    assert graph is base                                  # unchanged
