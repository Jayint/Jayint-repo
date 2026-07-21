"""Phase-3 seam: provider.service_obligations wraps the injected service classifier.

Task 4 — the classifier returns a RuntimePlan; service_obligations returns
``(graph, plan)`` with the GRAPH passed through UNCHANGED (Service/Config no longer
live in the constructed graph — the loop re-admits plan services at loop start)."""
from __future__ import annotations

from graph.python.provider import PythonProvider
from graph.contracts.registry import PROVIDERS
from graph.model import DepGraph
from graph.runtime_plan import RuntimePlan, EMPTY_PLAN


def test_none_classifier_is_passthrough():
    g = DepGraph()
    graph, plan = PythonProvider().service_obligations(g, "/repo", None)
    assert graph is g
    assert plan is EMPTY_PLAN


def test_injected_classifier_runs_and_plan_flows():
    g = DepGraph()
    sentinel_plan = RuntimePlan()
    seen = {}

    def fake_classifier(graph, repo):
        seen["repo"] = repo
        seen["graph"] = graph
        return sentinel_plan

    graph, plan = PythonProvider().service_obligations(g, "/repo", fake_classifier)
    assert graph is g                                    # graph passthrough (unchanged)
    assert plan is sentinel_plan
    assert seen == {"repo": "/repo", "graph": g}


def test_all_registered_providers_expose_service_obligations():
    for p in PROVIDERS:
        assert callable(getattr(p, "service_obligations", None))
