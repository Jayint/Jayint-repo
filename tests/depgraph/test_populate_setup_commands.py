from dataclasses import replace

from python_deps.depgraph.populate import populate_setup_commands
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State, Strength,
)


def _pkg():
    return Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                version="2.0", state=State.MISSING, chosen_fix="pip:requests")


def _syslib():
    return Node(id="syslib:libpq", type=NodeType.SYSTEM_LIB, name="libpq",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                state=State.MISSING, chosen_fix="apt:libpq-dev")


def _service():
    return Node(id="service:redis", type=NodeType.SERVICE, name="redis",
                layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.MISSING)


def _tool():
    return Node(id="tool:cmake", type=NodeType.TOOL, name="cmake",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                state=State.MISSING, chosen_fix="apt:cmake")


def test_fills_reciped_package_with_pinned_no_deps_pip():
    n = populate_setup_commands(DepGraph(nodes=(_pkg(),))).get("pkg:requests")
    assert n.setup_commands == (
        "python3 -m pip install --break-system-packages --no-deps requests==2.0",
    )
    assert n.strength is Strength.HARD


def test_agent_package_provider_command_survives_population():
    package = replace(
        _pkg(),
        chosen_fix=(
            "python3 -m pip install --break-system-packages requests==2.0"
        ),
    )
    n = populate_setup_commands(DepGraph(nodes=(package,))).get("pkg:requests")
    assert n.setup_commands == (
        "python3 -m pip install --break-system-packages requests==2.0",
    )


def test_fills_reciped_syslib_with_apt():
    n = populate_setup_commands(DepGraph(nodes=(_syslib(),))).get("syslib:libpq")
    assert n.setup_commands == ("apt-get install -y --no-install-recommends libpq-dev",)
    assert n.strength is Strength.HARD


def test_explicit_apt_provider_command_survives_population():
    command = "apt-get update -o Acquire::Retries=5 && apt-get install -y --fix-missing libpq-dev"
    syslib = replace(_syslib(), setup_commands=(command,))
    n = populate_setup_commands(DepGraph(nodes=(syslib,))).get("syslib:libpq")
    assert n.setup_commands == (command,)


def test_leaves_non_reciped_service_untouched():
    n = populate_setup_commands(DepGraph(nodes=(_service(),))).get("service:redis")
    assert n.setup_commands == ()
    assert n.strength is Strength.SOFT


def test_fills_reciped_tool_with_apt():
    n = populate_setup_commands(DepGraph(nodes=(_tool(),))).get("tool:cmake")
    assert n.setup_commands == ("apt-get install -y --no-install-recommends cmake",)
    assert n.strength is Strength.HARD


def test_idempotent_does_not_overwrite_existing():
    g = DepGraph(nodes=(_pkg(),))
    once = populate_setup_commands(g)
    twice = populate_setup_commands(once)
    assert once.get("pkg:requests").setup_commands == twice.get("pkg:requests").setup_commands
    assert once.get("pkg:requests").strength == twice.get("pkg:requests").strength
