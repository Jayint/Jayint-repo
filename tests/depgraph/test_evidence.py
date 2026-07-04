"""Evidence-parsing unit tests for extras handling (Task 8: targeted extras).

Bug this guards: per-dep extras (``uvicorn[standard]``) were silently stripped
by ``_parse_requirement_line`` before ever reaching a ``PythonRequirement``, so
the extra's transitive deps vanished under a ``--no-deps`` install. These tests
pin the 3-tuple -> 4-tuple parse change and the ``PythonRequirement.extras``
field it feeds, plus the existing optional-dependency group tag it must not
disturb.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from python_deps.evidence import (
    _add_requirement_line,
    _build_import_mappings,
    _parse_requirement_line,
    collect_python_dependency_evidence,
)
from python_deps.models import ImportFinding, PythonDependencyEvidence, PythonRequirement


def test_parse_requirement_line_returns_four_tuple_with_extras():
    parsed = _parse_requirement_line("uvicorn[standard]>=0.20")
    assert parsed == ("uvicorn", ">=0.20", "", ("standard",))


def test_parse_requirement_line_no_extras_yields_empty_tuple():
    parsed = _parse_requirement_line("requests>=2.0")
    assert parsed == ("requests", ">=2.0", "", ())


def test_parse_requirement_line_multiple_extras_sorted():
    parsed = _parse_requirement_line("uvicorn[standard,dotenv]>=0.20")
    assert parsed is not None
    name, specifier, marker, extras = parsed
    assert name == "uvicorn"
    assert specifier == ">=0.20"
    assert marker == ""
    assert extras == ("dotenv", "standard")  # sorted, deterministic


def test_parse_requirement_line_vcs_url_yields_no_extras():
    # The git+/egg= fallback path never parses extras; must still be a 4-tuple.
    parsed = _parse_requirement_line("git+https://example.com/x.git#egg=widget")
    assert parsed == ("widget", "git+https://example.com/x.git#egg=widget", "", ())


def test_add_requirement_line_stores_extras_on_requirement():
    target: list[PythonRequirement] = []
    _add_requirement_line(target, "uvicorn[standard]>=0.20", "pyproject.toml:project.dependencies")
    assert len(target) == 1
    req = target[0]
    assert req.name == "uvicorn"
    assert req.extras == ("standard",)
    assert req.specifier == ">=0.20"


def test_add_requirement_line_no_extras_defaults_empty_tuple():
    target: list[PythonRequirement] = []
    _add_requirement_line(target, "requests>=2.0", "pyproject.toml:project.dependencies")
    assert target[0].extras == ()


def _write(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def test_collect_pyproject_optional_dependency_group_tag_preserved(tmp_path):
    # The group tag (kind + source suffix) must survive alongside the new
    # extras field -- this is what roots.py's needed_extras gate reads.
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["uvicorn[standard]>=0.20"]

        [project.optional-dependencies]
        test = ["pytest"]
        docs = ["sphinx"]
        """,
    )
    evidence = collect_python_dependency_evidence(str(tmp_path))
    by_name = {req.name: req for req in evidence.declared_dependencies}

    uvicorn = by_name["uvicorn"]
    assert uvicorn.kind == "dependency"
    assert uvicorn.extras == ("standard",)

    pytest_req = by_name["pytest"]
    assert pytest_req.kind == "optional_dependency"
    assert pytest_req.source.endswith("optional-dependencies.test")

    sphinx_req = by_name["sphinx"]
    assert sphinx_req.kind == "optional_dependency"
    assert sphinx_req.source.endswith("optional-dependencies.docs")


def test_build_import_mappings_omits_unresolved(monkeypatch):
    # Bug this guards: an unresolved import carries no distribution name to
    # advise (Task 6). Before the guard, _build_import_mappings would still
    # emit an ImportPackageMapping for it (package_name=None), polluting the
    # advisory evidence layer with a mapping nobody can act on.
    import python_deps.evidence as evidence_module
    from python_deps.import_mapping import MappingResult, unresolved_result

    monkeypatch.setattr(
        evidence_module,
        "map_import_to_package",
        lambda name, *a, **k: unresolved_result(name)
        if name == "mystery"
        else MappingResult(name, name, "direct_name", "low"),
    )

    ev = PythonDependencyEvidence(repo_path=".")
    ev.imports.extend(
        [
            ImportFinding(import_name="requests", classification="external"),
            ImportFinding(import_name="mystery", classification="external"),
        ]
    )

    mappings = _build_import_mappings(ev)
    names = {m.import_name for m in mappings}
    assert "requests" in names
    assert "mystery" not in names


def test_evidence_used_extras_defaults_to_empty_set():
    ev = PythonDependencyEvidence(repo_path="/x")
    assert ev.used_extras == set()


def test_evidence_to_dict_includes_sorted_used_extras():
    ev = PythonDependencyEvidence(repo_path="/x")
    ev.used_extras.update({"socks", "http2"})
    assert ev.to_dict()["used_extras"] == ["http2", "socks"]
