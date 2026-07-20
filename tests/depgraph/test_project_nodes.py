from dataclasses import replace

from python_deps.depgraph.block import compile_blocks, compile_replay_blocks
from python_deps.depgraph.build import _add_project_node
from python_deps.depgraph.ids import TEST_NODE_ID, package_id, project_id
from python_deps.depgraph.ids import tool_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Layer,
    Node,
    NodeType,
    State,
)


def _node(node_id, node_type, name, layer, state=State.UNKNOWN, **kwargs):
    return Node(
        id=node_id,
        type=node_type,
        name=name,
        layer=layer,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        state=state,
        **kwargs,
    )


def test_test_bearing_monorepo_project_is_a_certifiable_editable_node(tmp_path):
    project = tmp_path / "packages" / "core"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_core.py").write_text("def test_ok(): pass\n")
    (project / "pyproject.toml").write_text(
        "[project]\nname='core-pkg'\nversion='0.1.0'\n"
        "dependencies=['requests>=2']\n"
    )

    graph = DepGraph()
    graph = graph.with_node(
        _node(TEST_NODE_ID, NodeType.TEST, "repo tests", Layer.TESTS)
    )
    graph = graph.with_node(
        _node(
            package_id("requests", None),
            NodeType.PACKAGE,
            "requests",
            Layer.PIP,
            state=State.SATISFIED,
            version="2.32.0",
        )
    )

    graph = _add_project_node(graph, str(tmp_path))
    project_node = graph.get(project_id("core-pkg"))
    assert project_node is not None
    assert project_node.chosen_fix.endswith("-e packages/core")
    assert "direct_url.json" in project_node.check_command
    assert "m.distributions()" in project_node.check_command
    assert "m.distribution(" not in project_node.check_command
    assert len(project_node.check_command.splitlines()) == 1
    assert project_node.data["project_path"] == "packages/core"
    assert (TEST_NODE_ID, project_node.id) in {(e.src, e.dst) for e in graph.edges}
    assert (project_node.id, package_id("requests", None)) in {
        (e.src, e.dst) for e in graph.edges
    }

    graph = graph.with_node(replace(project_node, state=State.MISSING))
    live_blocks = compile_blocks(graph)
    assert [block.target_node_ids for block in live_blocks] == [(project_node.id,)]
    assert live_blocks[0].commands == (project_node.chosen_fix,)

    replay = compile_replay_blocks(graph)
    commands = [command for block in replay for command in block.commands]
    assert commands[-1] == project_node.chosen_fix


def test_vcs_versioned_project_requires_git_before_editable_install(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cli.py").write_text("def test_ok(): pass\n")
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=['hatchling','hatch-vcs']\n"
        "build-backend='hatchling.build'\n"
        "[project]\nname='vcs-project'\ndynamic=['version']\n"
        "[tool.hatch.version]\nsource='vcs'\n"
    )
    graph = DepGraph().with_node(
        _node(TEST_NODE_ID, NodeType.TEST, "repo tests", Layer.TESTS)
    )

    graph = _add_project_node(graph, str(tmp_path))
    project = graph.get(project_id("vcs-project"))
    git = graph.get(tool_id("git"))
    assert project is not None and git is not None
    assert git.chosen_fix == "apt:git"
    assert (project.id, git.id) in {(edge.src, edge.dst) for edge in graph.edges}

    graph = graph.with_node(replace(project, state=State.MISSING))
    graph = graph.with_node(replace(git, state=State.MISSING))
    assert [block.target_node_ids for block in compile_blocks(graph)] == [(git.id,)]

    graph = graph.with_node(replace(git, state=State.SATISFIED))
    assert [block.target_node_ids for block in compile_blocks(graph)] == [(project.id,)]


def test_setup_cfg_project_check_does_not_guess_distribution_name(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok(): pass\n")
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = distribution-name-unrelated-to-directory\n"
    )
    graph = DepGraph().with_node(
        _node(TEST_NODE_ID, NodeType.TEST, "repo tests", Layer.TESTS)
    )
    graph = _add_project_node(graph, str(tmp_path))
    projects = [node for node in graph.nodes if node.type is NodeType.PROJECT]
    assert len(projects) == 1
    assert "m.distributions()" in projects[0].check_command


def test_tool_only_pyproject_is_structural_hub_without_editable_recipe(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_proxy.py").write_text("def test_ok(): pass\n")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.setuptools]\npy-modules = []\n\n"
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n\n"
        "[tool.coverage.run]\nsource = ['.']\n"
    )
    graph = DepGraph().with_node(
        _node(TEST_NODE_ID, NodeType.TEST, "repo tests", Layer.TESTS)
    )

    graph = _add_project_node(graph, str(tmp_path))
    projects = [node for node in graph.nodes if node.type is NodeType.PROJECT]
    assert len(projects) == 1
    project = projects[0]
    assert project.chosen_fix is None
    assert project.check_command is None
    assert all(
        project.id not in block.target_node_ids
        for block in compile_replay_blocks(graph)
    )
