from pathlib import Path

import graph.python.read.evidence as evidence_mod
from graph.python.read.evidence import collect_python_dependency_evidence


def test_soft_subdir_requirements_parse_into_soft_declared(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=[]\n")
    api = tmp_path / "api"
    api.mkdir()
    (api / "requirements.txt").write_text("fastapi[all]==0.128.2\ncelery==5.4.0\n.\n")

    ev = collect_python_dependency_evidence(str(tmp_path))

    soft_names = sorted(r.name for r in ev.soft_declared_dependencies)
    assert soft_names == ["celery", "fastapi"]           # bare `.` excluded
    fastapi = next(r for r in ev.soft_declared_dependencies if r.name == "fastapi")
    assert "all" in fastapi.extras                        # extras preserved
    assert str(fastapi.specifier) == "==0.128.2"          # version preserved
    # HARD path + roots must be untouched
    assert [r.name for r in ev.declared_dependencies] == []


def test_soft_file_read_error_does_not_abort_collection(tmp_path: Path, monkeypatch):
    """A per-file read failure on ONE soft file must NOT abort the soft loop.

    Regression for the Task-1 additive parse: `_parse_requirement_lines(path)`
    reads each accepted soft file, so a read error (the file vanished between
    discovery and parse, or a permission/OS error -- NOT UnicodeDecodeError,
    which `_iter_raw_requirement_lines` already handles) used to propagate out
    of the `for path in soft_candidates:` loop. That left LATER soft files out
    of `soft_requirements_files` and skipped the final `.sort()`, regressing the
    "soft_requirements_files byte-unchanged" invariant the additive parse
    promises. The failure must instead be recorded on `collection_errors` and
    the loop must continue.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=[]\n")
    api = tmp_path / "api"
    api.mkdir()
    api_req = api / "requirements.txt"
    api_req.write_text("fastapi==0.128.2\n")
    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "requirements.txt").write_text("celery==5.4.0\n")

    real_iter = evidence_mod._iter_raw_requirement_lines

    def flaky_iter(path):
        # Fail ONLY on the first (lexically-sorted) soft file; delegate the rest
        # to the real reader. Patched as a module global so the bare-name call
        # inside `_parse_requirement_lines` picks it up.
        if Path(path).resolve() == api_req.resolve():
            raise OSError("simulated read failure")
        return real_iter(path)

    monkeypatch.setattr(evidence_mod, "_iter_raw_requirement_lines", flaky_iter)

    ev = evidence_mod.collect_python_dependency_evidence(str(tmp_path))

    # Byte-unchanged soft-file behavior despite the read failure: the failing
    # file's path is still recorded (append precedes parse), the OTHER soft file
    # is still recorded, and the list is sorted.
    assert ev.soft_requirements_files == ["api/requirements.txt", "worker/requirements.txt"]
    assert ev.soft_requirements_files == sorted(ev.soft_requirements_files)
    # The loop did not abort: the second soft file still parsed.
    assert [r.name for r in ev.soft_declared_dependencies] == ["celery"]
    # The failure left a trail rather than being swallowed silently.
    assert any("api/requirements.txt" in err for err in ev.collection_errors)
