from pathlib import Path
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
