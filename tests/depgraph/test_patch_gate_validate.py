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


def test_package_requirement_requires_pinned_version():
    p = PatchProposal(add_requirements=(NodeSpec(
        id="pkg:requests", type="Package", name="requests", layer="pip",
        check_command="python -m pip show requests", evidence_ref="ev1",
    ),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("pinned version" in e for e in errs)


def test_package_requirement_rejects_version_range_as_non_exact():
    p = PatchProposal(add_requirements=(NodeSpec(
        id="pkg:werkzeug", type="Package", name="werkzeug", layer="pip",
        version=">=2.0,<4", check_command="python -m pip show werkzeug",
        evidence_ref="ev1",
    ),))
    errs = validate_proposal(_graph_with_test_node(), p, known_evidence_ids=_EV)
    assert any("exact PEP-440 version" in error for error in errs)


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


def test_existing_provider_requires_explicit_override(_graph_with_missing_syslib):
    p = PatchProposal(add_providers=(ProviderSpec(
        id="apt:libpq-dev", kind="apt",
        command="apt-get install -y --fix-missing libpq-dev",
        provides=("syslib:libpq",), override=False),))
    errs = validate_proposal(
        _graph_with_missing_syslib,
        p,
        known_evidence_ids=frozenset(),
    )
    assert any("set override=true" in error for error in errs)
    assert any("do not repeat syslib:libpq in add_requirements" in error for error in errs)


def test_conflicting_existing_node_error_gives_executable_repair_instruction():
    graph = DepGraph().with_node(Node(
        id="pkg:setuptools", type=NodeType.PACKAGE, name="setuptools",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING, version="83.0.0",
        check_command="python -m pip show setuptools",
        chosen_fix="pip:setuptools",
    ))
    proposal = PatchProposal(
        add_requirements=(NodeSpec(
            id="pkg:setuptools", type="Package", name="setuptools", layer="pip",
            version="75.6.0", check_command='python -c "import pkg_resources"',
            evidence_ref="ev1",
        ),),
        add_providers=(ProviderSpec(
            id="pip:setuptools", kind="pip",
            command="python3 -m pip install setuptools==75.6.0",
            provides=("pkg:setuptools",), override=False,
        ),),
    )

    errs = validate_proposal(graph, proposal, known_evidence_ids=_EV)

    assert any(
        "remove pkg:setuptools from add_requirements" in error
        and "add_providers with override=true" in error
        for error in errs
    )
    assert any(
        "set override=true" in error
        and "do not repeat pkg:setuptools in add_requirements" in error
        for error in errs
    )


def test_existing_provider_accepts_explicit_override(_graph_with_missing_syslib):
    p = PatchProposal(add_providers=(ProviderSpec(
        id="apt:libpq-dev", kind="apt",
        command="apt-get install -y --fix-missing libpq-dev",
        provides=("syslib:libpq",), override=True),))
    errs = validate_proposal(
        _graph_with_missing_syslib,
        p,
        known_evidence_ids=frozenset(),
    )
    assert not any("set override=true" in error for error in errs)


def _unresolved_pytest_graph() -> DepGraph:
    return _graph_with_test_node().with_node(Node(
        id="pkg:pytest", type=NodeType.PACKAGE, name="pytest",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RUNTIME,
        state=State.MISSING, version=None,
        check_command="python3 -m pip show pytest",
        chosen_fix="pip:pytest",
    ))


def test_unresolved_package_accepts_one_matching_exact_pip_pin():
    proposal = PatchProposal(add_providers=(ProviderSpec(
        id="pip:pytest", kind="pip",
        command="python3 -m pip install --break-system-packages pytest==8.3.3",
        provides=("pkg:pytest",), override=True,
    ),))

    assert validate_proposal(
        _unresolved_pytest_graph(), proposal, known_evidence_ids=frozenset()
    ) == []


def test_unresolved_package_rejects_unpinned_pip_provider():
    proposal = PatchProposal(add_providers=(ProviderSpec(
        id="pip:pytest", kind="pip", command="python3 -m pip install pytest",
        provides=("pkg:pytest",), override=True,
    ),))

    errors = validate_proposal(
        _unresolved_pytest_graph(), proposal, known_evidence_ids=frozenset()
    )
    assert any("exactly one matching pinned requirement" in error for error in errors)


def test_unresolved_package_rejects_wrong_or_multiple_pip_targets():
    for command in (
        "python3 -m pip install requests==2.32.4",
        "python3 -m pip install pytest==8.3.3 requests==2.32.4",
    ):
        proposal = PatchProposal(add_providers=(ProviderSpec(
            id="pip:pytest", kind="pip", command=command,
            provides=("pkg:pytest",), override=True,
        ),))
        errors = validate_proposal(
            _unresolved_pytest_graph(), proposal, known_evidence_ids=frozenset()
        )
        assert any("exactly one matching pinned requirement" in error for error in errors)


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
    g = DepGraph().with_node(Node(
        id="import:x", type=NodeType.IMPORT, name="x", layer=Layer.PIP,
        discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
    ))
    script = _sp(target_node_ids=("import:x",), checks=("python -c 'import x'",))
    errs = validate_proposal(g, PatchProposal(script_patches=(script,)),
                             known_evidence_ids=_SP_EV)
    assert errs == []


def test_rejects_parallel_block_for_graph_native_target():
    provider = ProviderSpec(
        id="apt:libx", kind="apt", command="apt-get install -y libx",
        provides=("syslib:x",),
    )
    errs = validate_proposal(
        _graph_with("syslib:x"),
        PatchProposal(add_providers=(provider,), script_patches=(_sp(),)),
        known_evidence_ids=_SP_EV,
    )
    assert any("duplicates graph-native target" in error for error in errs)


def test_rejects_unknown_script_patch_op():
    errs = validate_proposal(
        _graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(op="append_forever"),)),
        known_evidence_ids=_SP_EV,
    )
    assert any("illegal op" in error for error in errs)


def test_rejects_replace_for_missing_manual_block():
    errs = validate_proposal(
        _graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(op="replace_block"),)),
        known_evidence_ids=_SP_EV,
    )
    assert any("does not exist" in error for error in errs)
