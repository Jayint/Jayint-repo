from python_deps.depgraph.block import Block
from python_deps.depgraph.patch import EdgeSpec, PatchProposal, ProviderSpec, ScriptPatch
from python_deps.depgraph.patch_gate import admit_proposal
from python_deps.depgraph.schema import DiscoveredBy, Layer, Node, NodeType, State

def test_admit_rejects_with_errors(_graph_with_missing_syslib):
    bad = PatchProposal(add_providers=(ProviderSpec(
        id="apt:x", kind="apt", command="echo not-an-install", provides=("syslib:libpq",)),))
    res = admit_proposal(_graph_with_missing_syslib, bad, known_evidence_ids=frozenset())
    assert res.accepted is False and res.errors

def test_admit_accepts_and_recomposes(_graph_with_missing_syslib):
    good = PatchProposal(add_providers=(ProviderSpec(
        id="apt:libpq-dev", kind="apt", command="apt-get install -y libpq-dev",
        provides=("syslib:libpq",), override=True),))
    res = admit_proposal(_graph_with_missing_syslib, good, known_evidence_ids=frozenset())
    assert res.accepted is True
    assert any("libpq" in c for b in res.blocks for c in b.commands)

def test_admit_empty_proposal_accepts_noop(_graph_with_missing_syslib):
    res = admit_proposal(_graph_with_missing_syslib, PatchProposal(), known_evidence_ids=frozenset())
    assert res.accepted is True


def test_noop_provider_rejection_keeps_edge_batch_atomic(_graph_with_missing_syslib):
    graph = _graph_with_missing_syslib.with_node(Node(
        id="test:repo_tests_pass", type=NodeType.TEST, name="tests",
        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.MISSING,
    ))
    proposal = PatchProposal(
        add_providers=(ProviderSpec(
            id="apt:libpq-dev", kind="apt",
            command="apt-get install -y --fix-missing libpq-dev",
            provides=("syslib:libpq",), override=False,
        ),),
        add_edges=(EdgeSpec(
            source="test:repo_tests_pass", target="syslib:libpq",
        ),),
    )

    result = admit_proposal(graph, proposal, known_evidence_ids=frozenset())

    assert result.accepted is False
    assert any("override=true" in error for error in result.errors)
    assert result.graph == graph
    assert result.graph.edges == graph.edges


def _manual(command="old-command"):
    return Block(
        block_id="pip.compat", wave="pip", commands=(command,),
        target_node_ids=("syslib:libpq",), check_commands=("dpkg -s libpq-dev",),
    )


def test_admit_replace_block_substitutes_in_place(_graph_with_missing_syslib):
    replacement = ScriptPatch(
        op="replace_block", block_id="pip.compat", wave="pip",
        commands=("new-command",), target_node_ids=("syslib:libpq",),
        checks=("dpkg -s libpq-dev",), evidence_ref="ev1",
    )
    res = admit_proposal(
        _graph_with_missing_syslib,
        PatchProposal(script_patches=(replacement,)),
        manual_blocks=(_manual(),), known_evidence_ids=frozenset({"ev1"}),
    )
    assert res.accepted is True
    assert len(res.manual_blocks) == 1
    assert res.manual_blocks[0].commands == ("new-command",)


def test_admit_rejects_duplicate_add_block(_graph_with_missing_syslib):
    duplicate = ScriptPatch(
        block_id="pip.compat", wave="pip", commands=("new-command",),
        target_node_ids=("syslib:libpq",), checks=("dpkg -s libpq-dev",),
        evidence_ref="ev1",
    )
    res = admit_proposal(
        _graph_with_missing_syslib,
        PatchProposal(script_patches=(duplicate,)),
        manual_blocks=(_manual(),), known_evidence_ids=frozenset({"ev1"}),
    )
    assert res.accepted is False
    assert any("replace_block" in error for error in res.errors)
