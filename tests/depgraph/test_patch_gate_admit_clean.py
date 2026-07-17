"""CR6 (Inc 3): admit the CLEAN setup-shape Service node.

A setup-shape Service carries `data.setup` (the pinned install/start/probe recipe) and
derives its bounded `check_command` from `render_probe_poll(setup["probe"])`. Truth stays
`state` (MISSING -> host certify flips it). The empty/non-read-only-probe guard is the
anti-deadlock gate: a probe-less service must NEVER be admitted with a `render_probe_poll("")`
check_command (a broken shell + a node that can never demote). As of Inc 5B the setup-shape
is the SOLE Service shape (the legacy confidence/binding shape was deleted)."""
from graph.mutate.patch import PatchProposal, NodeSpec
from graph.mutate.patch_gate import admit_proposal, _requirement_errors
from graph.model import DepGraph, State
from graph.service_recipes import render_probe_poll

_EV = frozenset({"e0"})
_PROBE = "nc -z 127.0.0.1 6379"
_SETUP = {"install": ["apt-get update"], "start": "redis-server --daemonize yes",
          "probe": _PROBE, "createdb": None, "post": []}


def _setup_spec(**over):
    setup = over.pop("setup", dict(_SETUP))
    return NodeSpec(id="service:redis", type="Service", name="redis", layer="services",
                    evidence_ref="e0", setup=setup, service_kind="redis", **over)


def test_setup_service_admits_with_probe_poll():
    res = admit_proposal(DepGraph(), PatchProposal(add_requirements=(_setup_spec(),)),
                         known_evidence_ids=_EV)
    assert res.accepted is True, res.errors
    node = res.graph.get("service:redis")
    assert node is not None
    assert node.state is State.MISSING                       # host certify owns SATISFIED
    assert node.check_command == render_probe_poll(_PROBE)   # probe-poll drives the bounded check
    assert node.data["setup"] == _SETUP
    assert node.data["service_kind"] == "redis"
    # clean shape emits NONE of the legacy keys
    assert "service_confidence" not in node.data
    assert "binding" not in node.data
    assert "start_recipe" not in node.data


def test_setup_service_exotic_kind_admits():
    """A clean setup-shape Service may carry an EXOTIC kind (couchdb) — the KNOWN_SERVICE_KINDS
    check is relaxed for setup nodes (Inc 5)."""
    spec = NodeSpec(id="service:couch", type="Service", name="couch", layer="services",
                    evidence_ref="e0", setup=dict(_SETUP), service_kind="couchdb")
    res = admit_proposal(DepGraph(), PatchProposal(add_requirements=(spec,)),
                         known_evidence_ids=_EV)
    assert res.accepted is True, res.errors
    assert res.graph.get("service:couch").data["service_kind"] == "couchdb"


def test_legacy_exotic_kind_still_rejected():
    """The relax is setup-ONLY: a legacy node (setup=None) with an exotic kind is still rejected."""
    spec = NodeSpec(id="service:couch", type="Service", name="couch", layer="services",
                    evidence_ref="e0", setup=None, service_kind="couchdb",
                    check_command="nc -z 127.0.0.1 5984")
    assert any("unknown service_kind" in e for e in _requirement_errors(DepGraph(), spec, _EV))


def test_setup_empty_probe_rejected():
    """Anti-deadlock guard: an empty probe must be REJECTED, never render_probe_poll("")."""
    spec = _setup_spec(setup={**_SETUP, "probe": ""})
    res = admit_proposal(DepGraph(), PatchProposal(add_requirements=(spec,)),
                         known_evidence_ids=_EV)
    assert res.accepted is False
    assert any("probe" in e for e in res.errors)
    assert res.graph.get("service:redis") is None            # NOT admitted
    # the guard fires at the per-requirement level too (env_classifier._sanitize path)
    assert any("non-empty probe" in e for e in _requirement_errors(DepGraph(), spec, _EV))


def test_setup_curl_probe_rejected():
    spec = _setup_spec(setup={**_SETUP, "probe": "curl -f http://localhost:8080/h"})
    res = admit_proposal(DepGraph(), PatchProposal(add_requirements=(spec,)),
                         known_evidence_ids=_EV)
    assert res.accepted is False
    assert any("read-only" in e for e in res.errors)
    assert res.graph.get("service:redis") is None


def test_setup_empty_start_admits():
    """Evidence-only Service nodes carry NO start command (the agent writes it at
    repair). The gate must ADMIT an empty (but string) `start`, while the non-empty
    read-only PROBE guard stays load-bearing."""
    spec = _setup_spec(setup={**_SETUP, "start": ""})
    res = admit_proposal(DepGraph(), PatchProposal(add_requirements=(spec,)),
                         known_evidence_ids=_EV)
    assert res.accepted is True, res.errors
    node = res.graph.get("service:redis")
    assert node is not None
    assert node.data["setup"]["start"] == ""                 # empty start is legal
    assert node.check_command == render_probe_poll(_PROBE)   # probe still drives the check


def test_setup_non_string_start_rejected():
    """The relaxation is 'must be a string', not 'anything goes': a non-string start
    (a list, from a malformed proposal) is still rejected."""
    spec = _setup_spec(setup={**_SETUP, "start": ["redis-server"]})
    errs = _requirement_errors(DepGraph(), spec, _EV)
    assert any("start" in e for e in errs)
