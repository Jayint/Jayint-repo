"""RuntimePlan — the Service + Config construction artifact / serialization
boundary (Task 4). Pure dataclass + JSON round-trip + the ADMISSION helper.
"""
from __future__ import annotations

from graph.model import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from graph.runtime_plan import (
    ConfigObligation, RuntimePlan, EMPTY_PLAN, config_bake_eligible,
)
from graph.python.services.service_recipes import render_probe_poll


def _service(id_="service:postgres", name="postgres", setup=None):
    setup = setup if setup is not None else {
        "install": ["apt-get install -y postgresql"], "start": "",
        "probe": "pg_isready", "createdb": None, "post": [], "bind": [],
    }
    check = render_probe_poll(setup["probe"]) if setup.get("probe") else None
    return Node(id=id_, type=NodeType.SERVICE, name=name, layer=Layer.SERVICES,
                discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING,
                check_command=check, data={"setup": setup})


# ── config_bake_eligible (relocated fail-closed provenance gate) ─────────────

def test_bake_eligible_true_for_recognized_pairs():
    assert config_bake_eligible({"rung": 1, "source": "authoritative_config"})
    assert config_bake_eligible({"rung": 2, "source": ".env.example"})
    assert config_bake_eligible({"rung": 2, "source": ".env.sample"})
    assert config_bake_eligible({"rung": 3, "source": "code_scan_setdefault"})


def test_bake_eligible_fails_closed():
    for bad in (None, {}, "nope", 3, {"rung": 99},
                {"source": "code_scan_setdefault"}, {"rung": 1},
                {"rung": 1, "source": ""}, {"rung": 1, "source": None},
                {"rung": 1, "source": "bogus"}, {"rung": 2, "source": ".env.bogus"},
                {"rung": 3, "source": "code_scan_fallback"},
                {"rung": True, "source": "authoritative_config"},
                {"rung": 1.0, "source": "authoritative_config"}):
        assert not config_bake_eligible(bad), bad


# ── ConfigObligation ─────────────────────────────────────────────────────────

def test_config_obligation_create_computes_bake_eligible():
    ob = ConfigObligation.create("DJANGO_SETTINGS_MODULE", "s",
                                 {"rung": 1, "source": "authoritative_config"})
    assert ob.bake_eligible is True
    ob2 = ConfigObligation.create("X", "v", {"rung": 3, "source": "code_scan_fallback"})
    assert ob2.bake_eligible is False


def test_config_obligation_round_trip():
    ob = ConfigObligation.create("FLASK_APP", "app.wsgi",
                                 {"rung": 2, "source": ".env.example"})
    assert ConfigObligation.from_dict(ob.to_dict()) == ob


# ── RuntimePlan ──────────────────────────────────────────────────────────────

def test_empty_plan_is_empty():
    assert EMPTY_PLAN.is_empty()
    assert RuntimePlan().is_empty()
    assert not RuntimePlan(config_obligations=(ConfigObligation("X"),)).is_empty()


def test_plan_queries():
    svc = _service()
    cfg = ConfigObligation.create("FLASK_APP", "a", {"rung": 2, "source": ".env.example"})
    plan = RuntimePlan(service_obligations=(svc,), config_obligations=(cfg,))
    assert plan.get_service("service:postgres") is svc
    assert plan.get_service("service:none") is None
    assert plan.get_config("FLASK_APP") is cfg
    assert plan.get_config("MISSING") is None


def test_plan_json_round_trip_preserves_service_setup_and_config():
    svc = _service()
    cfg = ConfigObligation.create("DJANGO_SETTINGS_MODULE", "s",
                                  {"rung": 1, "source": "authoritative_config"})
    plan = RuntimePlan(service_obligations=(svc,), config_obligations=(cfg,))
    restored = RuntimePlan.from_dict(plan.to_dict())
    rsvc = restored.get_service("service:postgres")
    assert rsvc is not None
    assert rsvc.data["setup"]["probe"] == "pg_isready"
    assert rsvc.check_command == svc.check_command
    assert restored.get_config("DJANGO_SETTINGS_MODULE") == cfg


def test_admit_services_adds_service_nodes():
    plan = RuntimePlan(service_obligations=(_service(),))
    g = plan.admit_services(DepGraph())
    node = g.get("service:postgres")
    assert node is not None and node.type is NodeType.SERVICE


def test_admit_services_is_id_idempotent():
    # A runtime-discovered service with the SAME id must collapse (with_node
    # replaces by id) — no duplicate SERVICE node after admission.
    runtime_copy = _service()  # same id, e.g. discovered live at runtime
    g = DepGraph(nodes=(runtime_copy,))
    plan = RuntimePlan(service_obligations=(_service(),))
    g2 = plan.admit_services(g)
    assert len([n for n in g2.nodes if n.id == "service:postgres"]) == 1


def test_admit_services_empty_plan_is_noop():
    g = DepGraph(nodes=(_service("service:x", "x"),))
    assert EMPTY_PLAN.admit_services(g) is g
