# tests/depgraph/test_compose_script.py
from python_deps.depgraph.block import Block
from python_deps.depgraph.patch_gate import compose_script
from python_deps.depgraph.script import render_setup_sh, parse_setup_sh
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)


def _graph_two_waves():
    g = DepGraph()
    g = g.with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB, name="libpq.so",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
        check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev"))
    g = g.with_node(Node(id="pkg:psycopg2==2.9.9", type=NodeType.PACKAGE, name="psycopg2",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="2.9.9",
        check_command="python -m pip show psycopg2"))
    return g


def test_compiled_only_when_no_manual():
    blocks = compose_script(_graph_two_waves())
    ids = [b.block_id for b in blocks]
    assert ids == ["system.libpq.so", "pip.psycopg2==2.9.9"]   # compiled topo order preserved


def test_manual_system_block_slots_into_system_wave_before_pip():
    manual = (Block(block_id="system.extra", wave="system",
                    commands=("make install",), target_node_ids=("syslib:libpq.so",)),)
    blocks = compose_script(_graph_two_waves(), manual)
    ids = [b.block_id for b in blocks]
    # manual system block after compiled system block, both before the pip block
    assert ids.index("system.extra") > ids.index("system.libpq.so")
    assert ids.index("system.extra") < ids.index("pip.psycopg2==2.9.9")


def test_dedupe_block_id_compiled_wins():
    manual = (Block(block_id="system.libpq.so", wave="system",
                    commands=("echo override",), target_node_ids=("syslib:libpq.so",)),)
    blocks = compose_script(_graph_two_waves(), manual)
    libpq = [b for b in blocks if b.block_id == "system.libpq.so"]
    assert len(libpq) == 1 and "override" not in libpq[0].commands[0]   # compiled kept


def test_round_trips_through_render_parse():
    manual = (Block(block_id="system.extra", wave="system",
                    commands=("make install",), target_node_ids=("syslib:libpq.so",)),)
    blocks = compose_script(_graph_two_waves(), manual)
    assert parse_setup_sh(render_setup_sh(blocks)) == blocks


def _graph_two_pip():
    g = DepGraph()
    g = g.with_node(Node(id="pkg:aaa==1.0", type=NodeType.PACKAGE, name="aaa",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="1.0",
        check_command="python -m pip show aaa"))
    g = g.with_node(Node(id="pkg:zzz==1.0", type=NodeType.PACKAGE, name="zzz",
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="1.0",
        check_command="python -m pip show zzz"))
    return g


def test_manual_overlay_does_not_reorder_same_wave_compiled_blocks():
    # Regression guard: the stable sort must NOT reorder two same-wave (pip) compiled
    # blocks relative to each other when a manual block of that wave is overlaid.
    g = _graph_two_pip()
    compiled_ids = [b.block_id for b in compose_script(g)]
    assert len(compiled_ids) == 2                      # both pip packages compiled
    manual = (Block(block_id="pip.extra", wave="pip",
                    commands=("python -m pip install extra",), target_node_ids=("pkg:aaa==1.0",)),)
    with_manual = [b.block_id for b in compose_script(g, manual)]
    assert "pip.extra" in with_manual
    # removing the manual block recovers the compiled order EXACTLY (no intra-wave reorder)
    assert [i for i in with_manual if i != "pip.extra"] == compiled_ids
