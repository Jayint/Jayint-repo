from conftest import FakeExecutor
from graph.core.orchestrate import build_dep_graph
from graph.contracts.executor import CommandResult
from graph.compile.build_script import render_build_script
from graph.model import project_id, project_resolved_python
from graph.model import NodeType


def test_runtime_id_is_stable():
    # The id helper stays for OLD-graph compatibility even though construction
    # no longer mints a RUNTIME node (old serialized graphs still carry one).
    from graph.model import runtime_id
    assert runtime_id("3.10") == "runtime:python-3.10"


def _build(tmp_path, minor="3.10"):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nrequires-python = ">={minor}"\n'
    )
    ex = FakeExecutor(default=CommandResult(command="", returncode=0, stdout="", stderr=""))
    return build_dep_graph(str(tmp_path), ex, host_executor=ex, target_python=minor)


def test_build_mints_no_runtime_node(tmp_path):
    g = _build(tmp_path, "3.10")
    assert [n for n in g.nodes if n.type is NodeType.RUNTIME] == []


def test_build_stamps_resolved_python_on_project_node(tmp_path):
    g = _build(tmp_path, "3.10")
    proj = g.get(project_id("x"))
    assert proj is not None
    assert proj.data.get("resolved_python") == "3.10"
    # and the shared reader surfaces it (no RUNTIME fallback needed)
    assert project_resolved_python(g) == "3.10"


def test_rendered_setup_asserts_interpreter_minor(tmp_path):
    g = _build(tmp_path, "3.10")
    script = render_build_script(g)
    lines = [ln for ln in script.splitlines() if ln.strip()]
    # The assertion is the FIRST graph-derived preamble line: right after the
    # `set -Eeuo pipefail` shell-safety line, before the interpreter-agnostic
    # normalize/pytest instrument floor.
    pipefail_idx = lines.index("set -Eeuo pipefail")
    assert_idx = next(
        i for i, ln in enumerate(lines)
        if ln.startswith("python3 -c") and "sys.version_info[:2]==(3,10)" in ln.replace(" ", "")
    )
    normalize_idx = next(i for i, ln in enumerate(lines) if "command -v python " in ln)
    assert pipefail_idx < assert_idx < normalize_idx
    assert "python 3.10" in script  # clear error message names the required minor
