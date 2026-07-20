from dataclasses import replace

from python_deps.depgraph.block import Block
from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.execution_plan import (
    block_signature,
    compile_execution_plan,
    execution_plan_hash,
)
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Layer,
    Node,
    NodeType,
    State,
)


def _apt(node_id: str, name: str) -> Node:
    return Node(
        id=node_id,
        type=NodeType.SYSTEM_LIB,
        name=name,
        layer=Layer.SYSTEM,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        chosen_fix=f"apt:{name}",
        check_command=f"check {name}",
    )


def _pkg(node_id: str, name: str) -> Node:
    return Node(
        id=node_id,
        type=NodeType.PACKAGE,
        name=name,
        version="1.0",
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        check_command=f"check {name}",
    )


def test_plan_preserves_renderer_order_commands_and_manual_block_metadata():
    graph = DepGraph(nodes=(
        _pkg("pkg:client", "client"),
        _apt("syslib:libclient", "libclient"),
    ))
    manual = Block(
        block_id="config.client",
        wave="config",
        commands=("write-client-config",),
        target_node_ids=("config:client",),
        provider_ids=("config-provider",),
        check_commands=("check config",),
        evidence_refs=("ev.config",),
    )

    plan = compile_execution_plan(graph, (manual,))
    script = render_build_script(graph, (manual,))

    assert [block.block_id for block in plan] == [
        "system.libclient", "pip.client", "config.client"
    ]
    assert plan[0].commands[:2] == (
        "export DEBIAN_FRONTEND=noninteractive", "apt-get update"
    )
    for block in plan:
        positions = [script.index(command) for command in block.commands]
        assert positions == sorted(positions)
    assert plan[-1] == manual


def test_plan_is_state_independent_but_semantic_patch_changes_only_its_suffix():
    first = _apt("syslib:first", "first")
    second = _pkg("pkg:second", "second")
    graph = DepGraph(nodes=(first, second))
    original = compile_execution_plan(graph)

    certified = graph.with_node(first.with_state(State.SATISFIED, cycle=1))
    assert tuple(map(block_signature, compile_execution_plan(certified))) == tuple(
        map(block_signature, original)
    )

    changed_second = replace(
        second, setup_commands=("install-second-with-extra-prerequisite",)
    )
    patched = graph.with_node(changed_second)
    updated = compile_execution_plan(patched)
    assert block_signature(updated[0]) == block_signature(original[0])
    assert block_signature(updated[1]) != block_signature(original[1])
    assert execution_plan_hash(updated) != execution_plan_hash(original)


def test_explicit_package_provider_matches_plan_and_clean_replay_script():
    package = replace(
        _pkg("pkg:fakeredis", "fakeredis"),
        version="2.20.1",
        chosen_fix=(
            "python3 -m pip install --break-system-packages fakeredis==2.20.1"
        ),
    )
    graph = DepGraph(nodes=(package,))
    command = package.chosen_fix

    plan = compile_execution_plan(graph)
    script = render_build_script(graph)

    assert plan[0].commands == (command,)
    assert command in script
    assert "--no-deps fakeredis" not in script


def test_explicit_apt_provider_matches_plan_and_clean_replay_script():
    command = "apt-get update -o Acquire::Retries=5 && apt-get install -y --fix-missing libpq"
    syslib = replace(
        _apt("syslib:libpq", "libpq"),
        setup_commands=(command,),
    )
    graph = DepGraph(nodes=(syslib,))

    plan = compile_execution_plan(graph)
    script = render_build_script(graph)

    assert plan[0].commands[-1] == command
    assert command in script
    assert "apt-get install -y --no-install-recommends libpq" not in script
