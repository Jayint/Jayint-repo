import pytest

from ecosystems.base import CertifyMode, ClosureMode
from ecosystems.python import provider as provmod
from ecosystems.python.provider import PythonProvider


def test_name_and_certify_mode():
    assert PythonProvider().name == "python"
    assert PythonProvider().certify_mode is CertifyMode.INSTALL


def test_detect_manifest_repo_is_1(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    assert PythonProvider().detect(str(tmp_path)) == 1.0


def test_detect_imports_only_repo_clears_threshold(tmp_path):
    (tmp_path / "app.py").write_text("import os\n")  # no manifest; import evidence
    assert PythonProvider().detect(str(tmp_path)) >= 0.5


def test_detect_requirements_only_repo_clears_threshold(tmp_path):
    # No manifest, no *.py — Python-ness is ONLY in requirements.txt. The dropped
    # rglob heuristic would score this 0.0 (regression); evidence-based detect
    # scores it positive via collect_python_dependency_evidence.
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    assert PythonProvider().detect(str(tmp_path)) >= 0.5


def test_detect_non_python_repo_is_0(tmp_path):
    (tmp_path / "README.md").write_text("hi\n")
    assert PythonProvider().detect(str(tmp_path)) == 0.0


def test_closure_mode_resolve_without_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    assert PythonProvider().closure_mode_for(str(tmp_path)) is ClosureMode.RESOLVE


def test_closure_mode_lock_with_uv_lock(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    assert PythonProvider().closure_mode_for(str(tmp_path)) is ClosureMode.LOCK


def test_package_obligations_delegates_and_threads_record_provider(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_helper(repo, ce, **kw):
        seen["repo"] = repo
        seen["ce"] = ce
        seen["kw"] = kw
        return sentinel

    monkeypatch.setattr(provmod, "_python_package_obligations", fake_helper)
    out = PythonProvider().package_obligations(
        "/r", "CE",
        host_executor="HE", target_python="3.11", target_platform="linux-x86_64",
        exclude_newer="2024-01-01", needed_extras=frozenset({"test"}),
        record_provider="RP",
    )
    assert out is sentinel
    assert seen["repo"] == "/r" and seen["ce"] == "CE"
    assert seen["kw"]["host_executor"] == "HE"
    assert seen["kw"]["needed_extras"] == frozenset({"test"})
    assert seen["kw"]["record_provider"] == "RP"          # threaded (INV signature-stability)


def test_package_obligations_threads_llm_dist_guesser(monkeypatch):
    """The seam forwards an injected ``llm_dist_guesser`` down to the fixpoint
    helper. Without this hop, ``build_dep_graph(llm_dist_guesser=g)`` is a silent
    no-op (the guesser is dropped at the provider boundary) — a false-green the
    project cannot ship. Task 5/6 install-lane guesser thread-through."""
    seen = {}
    sentinel = object()

    def fake_helper(repo, ce, **kw):
        seen["kw"] = kw
        return ("G", [], object(), None)

    monkeypatch.setattr(provmod, "_python_package_obligations", fake_helper)
    PythonProvider().package_obligations(
        "/r", "CE", host_executor="HE", llm_dist_guesser=sentinel,
    )
    assert seen["kw"]["llm_dist_guesser"] is sentinel


def test_build_dep_graph_reaches_fixpoint_llm_end_to_end(tmp_path):
    """HOP A + full chain: ``build_dep_graph(llm_dist_guesser=g)`` must reach
    ``_phase_a_fixpoint``'s ``llm`` and call it on a pipreqs MISS. Uses the
    lightweight fake-executor harness (no container/network). ``zzznope`` is a
    guaranteed pipreqs miss, so the fixpoint's map-miss branch invokes the guesser
    with ``(import_name, symbols)`` — proving the guesser is not dropped at the
    ``build_dep_graph -> provider seam -> _python_package_obligations`` boundary."""
    from conftest import SequencedFakeExecutor  # type: ignore

    from graph.build import build_dep_graph
    from graph.executor import CommandResult

    def _r(rc=0, stdout="", stderr=""):
        return CommandResult(command="", returncode=rc, stdout=stdout, stderr=stderr)

    (tmp_path / "app.py").write_text("import zzznope\n")
    ex = SequencedFakeExecutor(
        responses={"uv lock": [_r(1, stderr="x")], "uv pip compile": [_r(0, stdout="")]},
        default=_r(0),
    )
    calls = []

    def guesser(import_name, symbols):
        calls.append((import_name, symbols))
        return []  # propose nothing -> deterministic residue, but proves reachability

    build_dep_graph(
        str(tmp_path), ex, host_executor=ex,
        record_provider=lambda _dist: None, llm_dist_guesser=guesser,
    )
    assert ("zzznope", ()) in calls  # the guesser was reached and fed the import


def test_build_dep_graph_threads_shadow_config_lane_through_seam(monkeypatch, tmp_path):
    """HOP A: ``build_dep_graph(shadow_config_lane=True)`` must reach
    ``_python_package_obligations`` THROUGH the ecosystem seam
    (``build_dep_graph -> EcosystemProvider.package_obligations ->
    PythonProvider.package_obligations -> _python_package_obligations``).
    Stage-B Task 8 added the flag to BOTH ends but the seam did not forward it,
    so the flag was inert from ``build_dep_graph`` — a silent no-op. This mirrors
    ``test_package_obligations_threads_llm_dist_guesser``'s capture technique but
    drives it end-to-end from ``build_dep_graph``; a sentinel exception
    short-circuits the downstream (native/certify) so this stays a pure wiring
    proof — no container/network."""
    from graph import build as buildmod

    seen = {}

    class _Stop(Exception):
        pass

    def fake_helper(repo, ce, **kw):
        seen["kw"] = kw
        raise _Stop  # capture-and-stop before any downstream stage runs

    monkeypatch.setattr(provmod, "_python_package_obligations", fake_helper)
    (tmp_path / "app.py").write_text("import os\n")  # real repo -> Python provider

    with pytest.raises(_Stop):
        buildmod.build_dep_graph(
            str(tmp_path), "CE", host_executor="HE", shadow_config_lane=True,
        )
    assert seen["kw"]["shadow_config_lane"] is True  # threaded through the seam

    seen.clear()
    with pytest.raises(_Stop):
        buildmod.build_dep_graph(str(tmp_path), "CE", host_executor="HE")
    assert seen["kw"]["shadow_config_lane"] is False  # default OFF => byte-identical


def test_native_obligations_delegates(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_native(graph, ce):
        seen["graph"] = graph
        seen["ce"] = ce
        return sentinel

    monkeypatch.setattr(provmod, "_python_native_obligations", fake_native)
    out = PythonProvider().native_obligations("G", "CE")
    assert out is sentinel and seen["graph"] == "G" and seen["ce"] == "CE"


def test_provider_preserves_hermeticity_symbols():
    """INV-8: importing the provider must NOT fork the record-provider symbols the
    autouse _no_pypi_network stub patches (build.py's module imports are untouched;
    the composite default is still built at the old 569-571 site inside the helper).
    End-to-end hermeticity is re-proven by oracle (a) after Task 7."""
    from graph import build, coverage, relink

    assert build.pypi_record_provider is coverage.pypi_record_provider
    assert build.composite_record_provider is coverage.composite_record_provider
    assert build.default_record_provider is coverage.default_record_provider
    assert "fetch" in coverage.pypi_record_provider.__kwdefaults__  # the patched lever
    assert relink.PACKAGES_DIST_CMD is not None                     # unchanged object


def test_degenerate_repo_still_dispatches_to_python(tmp_path):
    """No manifest, no *.py, no evidence -> detect()==0.0, but the dispatch
    default (Task 7) must STILL route to PythonProvider, never raise LookupError.
    Reference: test_build_empty_repo_yields_only_test_node (in the frozen 1111)
    must also still pass — its stdlib-import repo has a *.py so it clears the
    threshold directly; this test covers the truly-degenerate case the `default`
    seam guards, which oracle (a) does not otherwise exercise."""
    from ecosystems.registry import PROVIDERS, select_provider

    (tmp_path / "README.md").write_text("no python here\n")
    picked = select_provider(str(tmp_path), PROVIDERS, default=PROVIDERS[0])
    assert picked is PROVIDERS[0] and picked.name == "python"
