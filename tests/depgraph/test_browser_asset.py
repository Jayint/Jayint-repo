from pathlib import Path

from python_deps.depgraph.browser_asset import enrich_browser_assets
from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.emit import _is_reciped
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.execution_plan import compile_execution_plan
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Layer,
    Node,
    NodeType,
    State,
    Strength,
)


def _provider(*, declared=True, resolved=True, name="playwright") -> Node:
    return Node(
        id=f"pkg:{name}",
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.MISSING,
        version="1.61.0" if resolved else None,
        manifest_source="tests/requirements.txt" if declared else None,
        resolution_status="resolved" if resolved else "failed",
        resolved_python="3.13",
        resolved_platform="x86_64-manylinux_2_28",
        check_command=f"python3 -m pip show {name}",
    )


def _test_goal() -> Node:
    return Node(
        id="test:repo_tests_pass",
        type=NodeType.TEST,
        name="repo tests pass",
        layer=Layer.TESTS,
        discovered_by=DiscoveredBy.GOAL,
        state=State.MISSING,
        check_command="python3 -m pytest -q",
    )


def _graph(**provider_kwargs) -> DepGraph:
    return DepGraph(nodes=(_provider(**provider_kwargs), _test_goal()))


def _workflow(root: Path, text: str) -> None:
    path = root / ".github" / "workflows" / "tests.yml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def test_complete_static_evidence_adds_certified_replayable_asset(tmp_path):
    _workflow(tmp_path, """
steps:
  - name: install browser
    run: |
      python3 -m playwright install chromium
      python3 -m playwright install-deps chromium
""")

    enriched = enrich_browser_assets(_graph(), tmp_path)
    asset = enriched.get("data:browser-playwright-chromium")

    assert asset is not None
    # The emitted commands are copied from repository evidence; their order is
    # made replay-safe without synthesizing a missing action.
    assert asset.setup_commands == (
        "python3 -m playwright install-deps chromium",
        "python3 -m playwright install chromium",
    )
    assert asset.strength is Strength.HARD
    assert asset.data["provider_backed"] is True
    assert asset.resolved_platform == "x86_64-manylinux_2_28"
    assert "executable_path" in asset.check_command
    assert "os.access" in asset.check_command
    assert "chrome-linux" not in asset.check_command  # architecture-neutral
    assert _is_reciped(asset)

    requires = {(edge.src, edge.dst) for edge in enriched.edges}
    assert (asset.id, "pkg:playwright") in requires
    assert ("test:repo_tests_pass", asset.id) in requires


def test_combined_with_deps_command_is_preserved_exactly(tmp_path):
    _workflow(tmp_path, "python3 -m patchright install --with-deps chromium\n")
    enriched = enrich_browser_assets(_graph(name="patchright"), tmp_path)
    asset = enriched.get("data:browser-patchright-chromium")
    assert asset is not None
    assert asset.setup_commands == (
        "python3 -m patchright install --with-deps chromium",
    )


def test_missing_any_static_gate_is_a_noop(tmp_path):
    _workflow(tmp_path, """
python3 -m playwright install-deps chromium
python3 -m playwright install chromium
""")
    undeclared = _graph(declared=False)
    unresolved = _graph(resolved=False)
    no_test = DepGraph(nodes=(_provider(),))

    assert enrich_browser_assets(undeclared, tmp_path) is undeclared
    assert enrich_browser_assets(unresolved, tmp_path) is unresolved
    assert enrich_browser_assets(no_test, tmp_path) is no_test


def test_incomplete_or_guessed_provider_commands_are_rejected(tmp_path):
    # Browser-only install omits the repository-authored system dependency
    # action; wrapper-only and bare commands would require us to guess a replay
    # command.  None is promoted.
    _workflow(tmp_path, """
python3 -m playwright install chromium
uv run playwright install --with-deps chromium
playwright install --with-deps chromium
echo python3 -m playwright install-deps chromium
""")
    graph = _graph()
    assert enrich_browser_assets(graph, tmp_path) is graph


def test_comments_docs_and_dockerfiles_are_not_usage_evidence(tmp_path):
    _workflow(tmp_path, "# python3 -m playwright install --with-deps chromium\n")
    (tmp_path / "Dockerfile").write_text(
        "RUN python3 -m playwright install --with-deps chromium\n", encoding="utf-8"
    )
    graph = _graph()
    assert enrich_browser_assets(graph, tmp_path) is graph


def test_ordinary_data_asset_remains_advisory_and_non_reciped():
    ordinary = Node(
        id="data:fixtures.db",
        type=NodeType.DATA_ASSET,
        name="fixtures.db",
        layer=Layer.CONFIG,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        check_command="test -f fixtures.db",
        setup_commands=("python3 generate-fixtures.py",),
        strength=Strength.HARD,
    )
    assert not _is_reciped(ordinary)


def test_asset_is_present_in_final_script_and_structured_plan(tmp_path):
    _workflow(tmp_path, """
python3 -m playwright install-deps chromium
python3 -m playwright install chromium
""")
    enriched = enrich_browser_assets(_graph(), tmp_path)
    script = render_build_script(enriched)
    plan = compile_execution_plan(enriched)
    asset_blocks = [block for block in plan if block.target_node_ids == (
        "data:browser-playwright-chromium",
    )]

    assert len(asset_blocks) == 1
    assert asset_blocks[0].commands == (
        "python3 -m playwright install-deps chromium",
        "python3 -m playwright install chromium",
    )
    assert "#@node data:browser-playwright-chromium" in script
    assert "#@need data:browser-playwright-chromium" not in script
    assert "python3 -m playwright install-deps chromium" in script
    assert "python3 -m playwright install chromium" in script
    assert enrich_browser_assets(enriched, tmp_path) == enriched


def test_asset_state_is_written_only_by_its_host_check(tmp_path):
    _workflow(tmp_path, "python3 -m playwright install --with-deps chromium\n")
    enriched = enrich_browser_assets(_graph(), tmp_path)
    asset = enriched.get("data:browser-playwright-chromium")
    assert asset.state is State.MISSING

    class _PassingExecutor:
        def run(self, command, *, timeout=300):
            return CommandResult(command, 0, "", "")

    certified = certify_all(enriched, _PassingExecutor(), cycle=7)
    checked = certified.get(asset.id)
    assert checked.state is State.SATISFIED
    assert checked.certified_cycle == 7
