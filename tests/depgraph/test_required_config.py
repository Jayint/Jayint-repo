from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.emit import partition
from python_deps.depgraph.required_config import enrich_required_config_templates
from python_deps.depgraph.schema import DepGraph, DiscoveredBy, Layer, Node, NodeType, State


def _cfg_graph():
    return DepGraph().with_node(Node(
        id="config:daytona_api_key",
        type=NodeType.CONFIG,
        name="daytona_api_key",
        layer=Layer.CONFIG,
        discovered_by=DiscoveredBy.RUNTIME,
        state=State.MISSING,
        check_command="printenv daytona_api_key",
    ))


def test_required_config_template_becomes_replayable_config_node(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (tmp_path / "README.md").write_text(
        "cp config/config.example-daytona.toml config/config.toml\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("config/config.toml\n", encoding="utf-8")
    (config / "config.example-daytona.toml").write_text(
        "[daytona]\ndaytona_api_key = \"\"\n",
        encoding="utf-8",
    )

    graph = enrich_required_config_templates(_cfg_graph(), tmp_path)
    node = graph.get("config:daytona_api_key")

    assert node.data["provider_backed"] is True
    assert node.data["asset_kind"] == "config_template"
    assert node.setup_commands == (
        "test -e config/config.toml || cp -- config/config.example-daytona.toml config/config.toml",
    )
    script = render_build_script(graph)
    assert "cp -- config/config.example-daytona.toml config/config.toml" in script

    parts = partition(graph)
    assert [n.id for n in parts.emittable] == ["config:daytona_api_key"]
    assert parts.frontier == ()


def test_required_config_template_requires_ignored_target(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (tmp_path / "README.md").write_text(
        "cp config/config.example-daytona.toml config/config.toml\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("other.toml\n", encoding="utf-8")
    (config / "config.example-daytona.toml").write_text(
        "[daytona]\ndaytona_api_key = \"\"\n",
        encoding="utf-8",
    )

    graph = enrich_required_config_templates(_cfg_graph(), tmp_path)
    node = graph.get("config:daytona_api_key")

    assert not node.setup_commands
    assert node.check_command == "printenv daytona_api_key"
    parts = partition(graph)
    assert parts.emittable == ()
    assert parts.frontier == ()
