from python_deps.depgraph.patch import (
    PatchProposal, NodeSpec, ProviderSpec, EdgeSpec, ScriptPatch,
)
from python_deps.depgraph.patch_gate import validate_proposal
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)

_EV = frozenset({"ev1", "ev2"})


def _good():
    return PatchProposal(
        add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib", name="libpq.so",
                                   layer="system", check_command="ldconfig -p | grep -q libpq",
                                   evidence_ref="ev1"),),
        add_providers=(ProviderSpec(id="apt:libpq-dev", kind="apt",
                                    command="apt-get install -y --no-install-recommends libpq-dev",
                                    provides=("syslib:libpq.so",)),),
        add_edges=(EdgeSpec(source="test:repo_tests_pass", target="syslib:libpq.so"),),
        request_checks=("syslib:libpq.so",),
    )


def _graph_with_test_node():
    return DepGraph().with_node(Node(id="test:repo_tests_pass", type=NodeType.TEST,
        name="tests", layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.MISSING))


def test_accepts_well_formed_proposal():
    assert validate_proposal(_graph_with_test_node(), _good(), known_evidence_ids=_EV) == []


def test_rejects_satisfied_or_bad_promotion():
    p = PatchProposal(add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib",
        name="libpq.so", layer="system", check_command="ldconfig -p", evidence_ref="ev1",
        promotion="SATISFIED"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("promotion" in e.lower() for e in errs)


def test_rejects_non_canonical_node_id():
    p = PatchProposal(add_requirements=(NodeSpec(id="pkgconfig:libpq", type="SystemLib",
        name="libpq", layer="system", check_command="ldconfig -p", evidence_ref="ev1"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("canonical" in e.lower() or "prefix" in e.lower() for e in errs)


def test_rejects_missing_evidence():
    p = PatchProposal(add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib",
        name="libpq.so", layer="system", check_command="ldconfig -p", evidence_ref="nope"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("evidence" in e.lower() for e in errs)


def test_rejects_dangling_script_target():
    p = PatchProposal(script_patches=(ScriptPatch(block_id="system.x", wave="system",
        commands=("apt-get install -y libpq-dev",), target_node_ids=("syslib:ghost",),
        evidence_ref="ev1"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("ghost" in e or "target" in e.lower() for e in errs)


def test_rejects_mutating_check_command():
    p = PatchProposal(add_requirements=(NodeSpec(id="syslib:libpq.so", type="SystemLib",
        name="libpq.so", layer="system", check_command="apt-get install -y libpq-dev",
        evidence_ref="ev1"),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("read-only" in e.lower() or "mutating" in e.lower() for e in errs)


def test_rejects_action_class_mismatch():
    p = PatchProposal(add_providers=(ProviderSpec(id="apt:libpq-dev", kind="apt",
        command="pip install psycopg2", provides=()),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("action class" in e.lower() for e in errs)


def test_rejects_duplicate_ids_within_proposal():
    n = NodeSpec(id="syslib:libpq.so", type="SystemLib", name="libpq.so", layer="system",
                 check_command="ldconfig -p", evidence_ref="ev1")
    p = PatchProposal(add_requirements=(n, n))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("duplicate" in e.lower() for e in errs)


def test_rejects_illegal_edge_relation_types():
    # requires-edge dst SystemLib is legal; src SystemLib is NOT in EDGE_RULES allowed src.
    g = _graph_with_test_node().with_node(Node(id="syslib:a.so", type=NodeType.SYSTEM_LIB,
        name="a.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING))
    p = PatchProposal(add_edges=(EdgeSpec(source="syslib:a.so", target="test:repo_tests_pass"),))
    errs = validate_proposal(g, p, known_evidence_ids=_EV)
    assert any("edge" in e.lower() or "source type" in e.lower() for e in errs)


def test_empty_script_target_rejected(_graph_with_evidence):
    g, _ = _graph_with_evidence
    p = PatchProposal(script_patches=(ScriptPatch(
        block_id="system.x", wave="system", commands=("apt-get install -y x",),
        target_node_ids=(), evidence_ref="ev.1.0"),))
    errs = validate_proposal(g, p, known_evidence_ids=frozenset({"ev.1.0"}))
    assert any("target" in e for e in errs)


def test_provider_provides_unknown_node_rejected(_graph_with_evidence):
    g, _ = _graph_with_evidence
    p = PatchProposal(add_providers=(ProviderSpec(
        id="apt:libpq-dev", kind="apt", command="apt-get install -y libpq-dev",
        provides=("syslib:does-not-exist",)),))
    errs = validate_proposal(g, p, known_evidence_ids=frozenset({"ev.1.0"}))
    assert any("does-not-exist" in e for e in errs)


# --- script-patch hardening: empty/blank commands, illegal wave, unknown provides ---

def _graph_with(nid: str) -> DepGraph:
    return DepGraph().with_node(Node(id=nid, type=NodeType.SYSTEM_LIB, name="x",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING))


def _sp(**kw) -> ScriptPatch:
    base = dict(block_id="blk:1", wave="system", commands=("apt-get install -y libx",),
                target_node_ids=("syslib:x",), checks=("dpkg -s libx",), evidence_ref="ev:1")
    base.update(kw); return ScriptPatch(**base)


_SP_EV = frozenset({"ev:1"})


def test_rejects_empty_commands():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(commands=()),)), known_evidence_ids=_SP_EV)
    assert any("empty commands" in e for e in errs)


def test_rejects_blank_command():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(commands=("apt-get install -y libx", "   ")),)),
        known_evidence_ids=_SP_EV)
    assert any("blank" in e for e in errs)


def test_rejects_illegal_wave():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(wave="post-install"),)), known_evidence_ids=_SP_EV)
    assert any("illegal wave" in e for e in errs)


def test_rejects_provides_unknown_node():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(provides=("syslib:ghost",)),)), known_evidence_ids=_SP_EV)
    assert any("provides unknown node" in e for e in errs)


def test_accepts_legal_script_patch():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(),)), known_evidence_ids=_SP_EV)
    assert errs == []
