"""Stage C Task 2 — the editable-install cure runs LIVE in construction and the
render-time poison is reconciled with the scratch-certificate.

On a successful in-container cure (the ``pip install -e .`` rung chain + a
``pytest --collect-only`` gate), ``stamp_scratch_certified`` marks the Project node
``scratch_certified``; the render-time poison (``emit._poison_project_certificate``)
then leaves the project's capstone ``check_command`` intact, so a ``#@check`` renders
on the capstone. On cure FAILURE nothing is stamped, the poison applies exactly as
before, and the rendered ``setup.sh`` is byte-identical to today.
"""
import json

from conftest import FakeExecutor, SequencedFakeExecutor, make_result  # type: ignore

from graph.compile.build_script import render_build_script
from graph.contracts.executor import CommandResult
from graph.core.orchestrate import build_dep_graph
from graph.model import DepGraph, DiscoveredBy, Layer, Node, NodeType, State
from graph.python.invocation_resolver import resolve
from graph.python.lanes.config.cure import CureResult, run_cure, stamp_scratch_certified
from graph.python.pipeline import _apply_live_config_cure


def _installable_repo(tmp_path):
    """A minimal installable repo: a build-system pyproject + an importable
    top-level package, so ``resolve`` returns a real root-config plan (cwd ``.``)
    and ``_add_project_node`` hands the Project a ``check_command``."""
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\n"
        "[project]\nname='myproj'\nversion='0.0.0'\n"
    )
    (tmp_path / "myproj").mkdir()
    (tmp_path / "myproj" / "__init__.py").write_text("")
    return str(tmp_path)


def _graph_with_installable_project():
    """A one-node graph whose installable Project carries a real capstone check —
    the exact shape ``_add_project_node`` produces for an importable repo."""
    proj = Node(
        id="project:myproj", type=NodeType.PROJECT, name="myproj", layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
        check_command='python -c "import myproj"',
        data={"installable": True},
    )
    return DepGraph().with_node(proj)


# --- _apply_live_config_cure: stamp policy --------------------------------- #


def test_cure_success_stamps_scratch_certified(tmp_path):
    repo = _installable_repo(tmp_path)
    out = _apply_live_config_cure(
        _graph_with_installable_project(), repo,
        FakeExecutor(default=make_result(returncode=0)),
    )
    proj = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    assert proj.data.get("scratch_certified") is True     # cure succeeded -> stamped
    assert proj.data.get("cure_rung") == "isolated"       # evidence: which rung cured
    assert proj.data.get("cure_collect_ok") is True        # evidence: collect gate result
    assert proj.state is State.SATISFIED                   # existing stamp policy kept


def test_cure_failure_leaves_project_unstamped(tmp_path):
    repo = _installable_repo(tmp_path)
    out = _apply_live_config_cure(
        _graph_with_installable_project(), repo,
        FakeExecutor(default=make_result(returncode=1)),
    )
    proj = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    assert proj.data.get("scratch_certified") is not True  # both rungs failed -> no stamp


def test_cure_is_fail_open_on_executor_error(tmp_path):
    class _Boom:
        def run(self, *_a, **_k):
            raise RuntimeError("container gone")

    graph = _graph_with_installable_project()
    out = _apply_live_config_cure(graph, _installable_repo(tmp_path), _Boom())
    assert out is graph      # any exception swallowed; construction is untouched


# --- run_cure: rung chain + collect gate ----------------------------------- #


def test_run_cure_fails_fast_without_collect_on_install_failure(tmp_path):
    ex = FakeExecutor(default=make_result(returncode=1))
    cure = run_cure(ex, resolve(_installable_repo(tmp_path)))
    assert cure.ok is False and cure.rung == "failed"
    assert not any("--collect-only" in c for c in ex.calls)          # never reached the gate
    assert any("-e ." in c and "--no-build-isolation" not in c for c in ex.calls)  # rung 1
    assert any("--no-build-isolation" in c for c in ex.calls)        # rung 2 attempted


def test_run_cure_records_collect_failure_but_still_ok(tmp_path):
    ex = FakeExecutor(
        responses={"--collect-only": make_result(returncode=1)},
        default=make_result(returncode=0),
    )
    cure = run_cure(ex, resolve(_installable_repo(tmp_path)))
    assert cure.ok is True and cure.rung == "isolated" and cure.collect_ok is False


# --- render reconciliation ------------------------------------------------- #


def test_cured_project_check_survives_render():
    cured = stamp_scratch_certified(
        _graph_with_installable_project(), CureResult(True, "isolated", True, "ok")
    )
    assert '#@check python -c "import myproj"' in render_build_script(cured)


def test_uncured_project_is_poisoned_render_has_no_check():
    # No cure -> the poison strips the capstone check, so no #@check renders.
    assert '#@check python -c "import myproj"' not in render_build_script(
        _graph_with_installable_project()
    )


def test_cure_failure_render_is_byte_identical_to_no_cure():
    graph = _graph_with_installable_project()
    baseline = render_build_script(graph)
    failed = stamp_scratch_certified(graph, CureResult(False, "failed", False, ""))
    assert render_build_script(failed) == baseline   # a failed cure moves not a byte


# --- end-to-end: cure wired into real construction ------------------------- #


def test_build_dep_graph_stamps_scratch_certified_end_to_end(tmp_path):
    """All-rc0 stub: the in-container cure 'succeeds', so real construction stamps
    the Project node scratch_certified — proving the cure runs live inside
    ``build_dep_graph``, not just as an isolated helper."""
    def _r(rc=0, stdout="", stderr=""):
        return CommandResult(command="", returncode=rc, stdout=stdout, stderr=stderr)

    ex = SequencedFakeExecutor(
        responses={"stdlib_module_names": [_r(0, stdout=json.dumps(["os", "sys"]))]},
        default=_r(0),
    )
    graph = build_dep_graph(_installable_repo(tmp_path), ex, host_executor=ex)
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert proj.data.get("scratch_certified") is True
