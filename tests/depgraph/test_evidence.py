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
    UvSourceConfig,
    _add_requirement_line,
    _build_import_mappings,
    _parse_requirement_line,
    collect_python_dependency_evidence,
    uv_source_config,
)
from python_deps.models import ImportFinding, PythonDependencyEvidence, PythonRequirement


def test_parse_requirement_line_returns_four_tuple_with_extras():
    # Task 1 (Fix 1, post-measurement): a 5th element (the PEP 508 direct-
    # reference URL, "" when absent) was added to this tuple. Ordinary
    # requirements carry "" in that slot.
    parsed = _parse_requirement_line("uvicorn[standard]>=0.20")
    assert parsed == ("uvicorn", ">=0.20", "", ("standard",), "")


def test_parse_requirement_line_no_extras_yields_empty_tuple():
    parsed = _parse_requirement_line("requests>=2.0")
    assert parsed == ("requests", ">=2.0", "", (), "")


def test_parse_requirement_line_multiple_extras_sorted():
    parsed = _parse_requirement_line("uvicorn[standard,dotenv]>=0.20")
    assert parsed is not None
    name, specifier, marker, extras, url = parsed
    assert name == "uvicorn"
    assert specifier == ">=0.20"
    assert marker == ""
    assert extras == ("dotenv", "standard")  # sorted, deterministic
    assert url == ""


def test_parse_requirement_line_vcs_url_yields_no_extras():
    # The git+/egg= fallback path (a BARE url with no leading "name @", not a
    # PEP 508 direct reference) never parses extras and carries no URL in the
    # 5th slot -- it is not a name we can key a non-PyPI-source set by.
    parsed = _parse_requirement_line("git+https://example.com/x.git#egg=widget")
    assert parsed == ("widget", "git+https://example.com/x.git#egg=widget", "", (), "")


# --------------------------------------------------------------------------- #
# Fix 1 (docs/superpowers/plans/2026-07-14-post-measurement-fixes.md): PEP 508
# direct references (`name @ <url>`) are a non-PyPI source exactly like
# `[tool.uv.sources]`, just via inline syntax. ArchipelagoMW/Archipelago's
# root requirements.txt declares `kivymd @ git+https://github.com/kivymd/
# KivyMD@5ff9d0d` -- before this fix, `_parse_requirement_line` special-cased
# ANY line containing "://" as a bare-URL/egg= line BEFORE ever trying
# `packaging.requirements.Requirement`, so a NAMED direct reference (which
# `Requirement` parses fine, exposing `.url`) fell through the `#egg=` regex
# and vanished with no trace at all -- not a root, not a MISSING node, no
# evidence. `Requirement` is now tried FIRST; a hit with `.url` set is
# recognized as a direct reference here.
# --------------------------------------------------------------------------- #
def test_parse_requirement_line_git_direct_reference_preserves_url():
    parsed = _parse_requirement_line(
        "kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d"
    )
    assert parsed == (
        "kivymd",
        "",
        "",
        (),
        "git+https://github.com/kivymd/KivyMD@5ff9d0d",
    )


def test_parse_requirement_line_https_direct_reference_preserves_url():
    parsed = _parse_requirement_line("foo @ https://example.com/foo-1.0.tar.gz")
    assert parsed == ("foo", "", "", (), "https://example.com/foo-1.0.tar.gz")


def test_parse_requirement_line_file_direct_reference_preserves_url():
    parsed = _parse_requirement_line("foo @ file:///tmp/foo-1.0.tar.gz")
    assert parsed == ("foo", "", "", (), "file:///tmp/foo-1.0.tar.gz")


def test_parse_requirement_line_direct_reference_with_marker():
    parsed = _parse_requirement_line(
        'foo @ git+https://example.com/foo.git ; python_version >= "3.8"'
    )
    assert parsed is not None
    name, specifier, marker, extras, url = parsed
    assert name == "foo"
    assert specifier == ""
    assert marker == 'python_version >= "3.8"'
    assert extras == ()
    assert url == "git+https://example.com/foo.git"


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


def test_add_requirement_line_direct_reference_records_url_on_evidence():
    # Passing the evidence object (as every real caller in this module does)
    # routes the URL onto `evidence.direct_reference_sources`, keyed by
    # canonical name, while the PythonRequirement itself still lands in
    # `target` with an empty specifier (so select_roots keeps treating it as
    # an ordinary declared dependency for SCOPE purposes).
    target: list[PythonRequirement] = []
    ev = PythonDependencyEvidence(repo_path="/repo")
    _add_requirement_line(
        target,
        "kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d",
        "requirements.txt",
        evidence=ev,
    )
    assert len(target) == 1
    assert target[0].name == "kivymd"
    assert target[0].specifier == ""
    assert ev.direct_reference_sources == {
        "kivymd": "git+https://github.com/kivymd/KivyMD@5ff9d0d"
    }


def test_add_requirement_line_ordinary_dependency_does_not_touch_direct_reference_sources():
    target: list[PythonRequirement] = []
    ev = PythonDependencyEvidence(repo_path="/repo")
    _add_requirement_line(target, "requests>=2.0", "requirements.txt", evidence=ev)
    assert ev.direct_reference_sources == {}


def test_add_requirement_line_evidence_optional_backward_compatible():
    # No `evidence=` kwarg at all must still work (every pre-existing caller
    # of this internal helper in this test module omits it).
    target: list[PythonRequirement] = []
    _add_requirement_line(
        target, "kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d", "requirements.txt"
    )
    assert target[0].name == "kivymd"


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


def test_uv_workspace_member_dependency_keeps_its_true_kind(tmp_path):
    # FIX 3 (C1) background: PostHog/posthog (77,642 gold tests) produced ZERO
    # pip packages because `[tool.uv.sources] hogli = { workspace = true }`
    # made resolve.py's synthetic pyproject hand "hogli" -- an internal
    # workspace package that does not exist on PyPI -- to `uv lock` as if it
    # were an ordinary distribution name, collapsing the WHOLE resolve for all
    # 263 real deps at once.
    #
    # Task 7: the earlier mitigation re-tagged such a requirement's `kind` to
    # a sentinel so roots.py's `_in_test_scope` fallthrough would drop it --
    # but that destroyed the true dependency/optional_dependency/dev_group
    # distinction select_roots needs for SCOPE, forcing a second,
    # scope-bypassing pass elsewhere to put it back. The real fix carries the
    # source through (`uv_sources`) instead of dropping the name, so a
    # `[tool.uv.sources]`-carrying dependency now keeps its TRUE kind exactly
    # like any other declared dependency.
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "posthog"
        version = "0.1.0"
        dependencies = ["hogli", "requests"]

        [tool.uv.workspace]
        members = ["tools/hogli"]

        [tool.uv.sources]
        hogli = { workspace = true }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    by_name = _by_name(ev)

    assert by_name["requests"].kind == "dependency"
    hogli = by_name["hogli"]
    assert hogli.kind == "dependency"  # true kind preserved, never retagged
    # ...and its source is still captured, verbatim, for resolve.py to honor.
    assert ev.uv_sources["hogli"] == ({"workspace": True},)


def test_uv_git_sourced_dependency_keeps_its_true_kind(tmp_path):
    # A git-pinned fork (PostHog's `infi-clickhouse-orm` / `pytest-split`) is
    # likewise not resolvable from a bare PyPI name at the pinned rev/tag --
    # but must still keep its true declared kind (Task 7: no retag).
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "posthog"
        version = "0.1.0"
        dependencies = ["infi-clickhouse-orm", "requests"]

        [tool.uv.sources]
        infi-clickhouse-orm = { git = "https://github.com/PostHog/infi.clickhouse_orm", rev = "abc123" }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    by_name = _by_name(ev)

    assert by_name["requests"].kind == "dependency"
    assert by_name["infi-clickhouse-orm"].kind == "dependency"
    assert ev.uv_sources["infi-clickhouse-orm"] == (
        {"git": "https://github.com/PostHog/infi.clickhouse_orm", "rev": "abc123"},
    )


def test_uv_sources_index_only_override_is_not_excluded_but_is_captured(tmp_path):
    # A `[tool.uv.sources]` entry naming only an alternate INDEX (not a
    # workspace/git/url/path) still resolves by plain name -- evidence.py
    # never re-tags ANY declared dependency's kind (Task 7), so this stays a
    # normal root-eligible dependency exactly like every other kind of
    # `[tool.uv.sources]` override. The index spec itself is still captured,
    # verbatim, onto `evidence.uv_sources` for resolve.py to render into its
    # synthetic pyproject's `[[tool.uv.index]]` config.
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["mirrored-pkg"]

        [tool.uv.sources]
        mirrored-pkg = { index = "internal" }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert _by_name(ev)["mirrored-pkg"].kind == "dependency"
    assert ev.uv_sources["mirrored-pkg"] == ({"index": "internal"},)


def test_uv_sources_conditional_list_form_git_entry_keeps_its_true_kind(tmp_path):
    # uv also allows a LIST of source tables (marker-conditional sources);
    # regardless of shape, the declared dependency's `kind` is never touched
    # (Task 7) -- only its captured `uv_sources` entry reflects the git+index
    # conditional pair.
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["forked-pkg"]

        [[tool.uv.sources.forked-pkg]]
        git = "https://example.com/forked-pkg.git"
        marker = "sys_platform == 'darwin'"

        [[tool.uv.sources.forked-pkg]]
        index = "pypi"
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert _by_name(ev)["forked-pkg"].kind == "dependency"


def test_uv_sourced_dependency_scope_governed_entirely_by_true_kind_via_select_roots(tmp_path):
    # Integration proof (reads roots.py, does not modify it): since evidence.py
    # no longer retags any declared dependency's `kind` (Task 7), select_roots'
    # EXISTING, unmodified `_in_test_scope` policy applies to a
    # `[tool.uv.sources]`-carrying dependency exactly like any other declared
    # dependency -- a runtime dep (`hogli`) is IN regardless of its uv source,
    # while a feature extra (`hogli-cuda`, gated behind a `gpu` group the repo
    # never activates) stays OUT, uv source or not. Scope is the ONLY thing
    # deciding membership; there is no separate uv-sources carve-out either
    # way.
    from graph.python.lanes.install.roots import select_roots
    from graph.schema import DepGraph

    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "posthog"
        version = "0.1.0"
        dependencies = ["hogli", "requests"]

        [project.optional-dependencies]
        gpu = ["hogli-cuda"]

        [tool.uv.sources]
        hogli = { workspace = true }
        hogli-cuda = { git = "https://example.com/hogli-cuda.git" }
        """,
    )
    roots = select_roots(str(tmp_path), DepGraph())
    dist_names = {dist for _import_id, dist in roots}
    assert "requests" in dist_names
    assert "hogli" in dist_names
    assert "hogli-cuda" not in dist_names


# --------------------------------------------------------------------------- #
# `UvSourceConfig` / `uv_source_config`: capture the FULL `[tool.uv.sources]` /
# `[tool.uv.workspace]` / `[[tool.uv.index]]` config, verbatim. Purely
# additive: these tests only assert evidence surface, never a `kind`/
# root-selection assertion (a `[tool.uv.sources]`-carrying dependency's `kind`
# is never touched -- see the tests above).
# --------------------------------------------------------------------------- #


def test_uv_source_config_dataclass_populated_from_pyproject(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"

        [tool.uv.workspace]
        members = ["packages/*"]

        [tool.uv.sources]
        widget = { path = "./widget" }
        """,
    )
    config = uv_source_config(tmp_path)
    assert isinstance(config, UvSourceConfig)
    assert config.sources == {"widget": ({"path": "./widget"},)}
    assert config.workspace_members == ("packages/*",)
    assert config.indexes == ()


def test_uv_source_config_absent_pyproject_returns_empty(tmp_path):
    config = uv_source_config(tmp_path)
    assert config.sources == {}
    assert config.workspace_members == ()
    assert config.indexes == ()


def test_uv_source_config_malformed_pyproject_returns_empty_not_raises(tmp_path):
    _write(tmp_path, "pyproject.toml", "this is not valid toml {{{")
    config = uv_source_config(tmp_path)
    assert config.sources == {}
    assert config.workspace_members == ()
    assert config.indexes == ()


def test_collect_evidence_survives_malformed_pyproject_for_uv_sources(tmp_path):
    # collect_python_dependency_evidence must not raise or lose other
    # collectors' output when the uv-sources read hits a parse error.
    _write(tmp_path, "pyproject.toml", "this is not valid toml {{{")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.uv_sources == {}
    assert ev.uv_workspace_members == ()
    assert ev.uv_indexes == ()


def test_uv_workspace_source_and_members_captured_verbatim(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "posthog"
        version = "0.1.0"
        dependencies = ["hogli", "requests"]

        [tool.uv.workspace]
        members = ["tools/hogli"]

        [tool.uv.sources]
        hogli = { workspace = true }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.uv_sources["hogli"] == ({"workspace": True},)
    assert ev.uv_workspace_members == ("tools/hogli",)


def test_uv_git_source_with_rev_captured_verbatim(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "posthog"
        version = "0.1.0"
        dependencies = ["infi-clickhouse-orm"]

        [tool.uv.sources]
        infi-clickhouse-orm = { git = "https://github.com/PostHog/infi.clickhouse_orm", rev = "abc123" }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.uv_sources["infi-clickhouse-orm"] == (
        {"git": "https://github.com/PostHog/infi.clickhouse_orm", "rev": "abc123"},
    )


def test_uv_git_source_with_tag_captured_verbatim(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["pytest-split"]

        [tool.uv.sources]
        pytest-split = { git = "https://github.com/example/pytest-split", tag = "v1.2.3" }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.uv_sources["pytest-split"] == (
        {"git": "https://github.com/example/pytest-split", "tag": "v1.2.3"},
    )


def test_uv_path_and_url_sources_captured_verbatim(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["local-pkg", "remote-pkg"]

        [tool.uv.sources]
        local-pkg = { path = "../local-pkg" }
        remote-pkg = { url = "https://example.com/remote-pkg-1.0.tar.gz" }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.uv_sources["local-pkg"] == ({"path": "../local-pkg"},)
    assert ev.uv_sources["remote-pkg"] == ({"url": "https://example.com/remote-pkg-1.0.tar.gz"},)


def test_uv_index_block_captured_alongside_index_source_reference(tmp_path):
    # Step 2's hole: `index =` sources AND the `[[tool.uv.index]]` blocks they
    # reference must both be captured -- Task 5 needs both to reconstruct the
    # intended index in the synthetic pyproject.
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["mirrored-pkg"]

        [[tool.uv.index]]
        name = "internal"
        url = "https://pypi-corp.example.com/simple"

        [tool.uv.sources]
        mirrored-pkg = { index = "internal" }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.uv_sources["mirrored-pkg"] == ({"index": "internal"},)
    assert ev.uv_indexes == ({"name": "internal", "url": "https://pypi-corp.example.com/simple"},)


def test_uv_sources_conditional_list_form_captures_every_entry(tmp_path):
    # The marker-conditional list-of-tables form must normalise to a tuple
    # with every entry preserved (not just the one that determines exclusion).
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["forked-pkg"]

        [[tool.uv.sources.forked-pkg]]
        git = "https://example.com/forked-pkg.git"
        marker = "sys_platform == 'darwin'"

        [[tool.uv.sources.forked-pkg]]
        index = "pypi"
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.uv_sources["forked-pkg"] == (
        {"git": "https://example.com/forked-pkg.git", "marker": "sys_platform == 'darwin'"},
        {"index": "pypi"},
    )


def test_uv_sources_name_canonicalized_for_lookup(tmp_path):
    # `Foo_Bar` declared in [tool.uv.sources] must be retrievable by its
    # canonical PyPI-normalised name "foo-bar" (same normalisation
    # `normalize_package_name` applies everywhere else in this module).
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["Foo-Bar"]

        [tool.uv.sources]
        Foo_Bar = { path = "../foo-bar" }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.uv_sources["foo-bar"] == ({"path": "../foo-bar"},)


# --------------------------------------------------------------------------- #
# Fix 1 end-to-end (docs/superpowers/plans/2026-07-14-post-measurement-fixes.md):
# `collect_python_dependency_evidence` must surface a PEP 508 direct reference
# via `direct_reference_sources`, from BOTH a requirements.txt line and a
# pyproject `[project] dependencies` entry -- and, critically, the OTHER
# ordinary pinned deps in the same file must be unaffected (the headline bug:
# one unresolvable `uv lock` root killed the entire closure).
# --------------------------------------------------------------------------- #
def test_requirements_txt_direct_reference_recorded_and_other_deps_unaffected(tmp_path):
    _write(
        tmp_path,
        "requirements.txt",
        """
        kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d
        colorama==0.4.6
        PyYAML==6.0.1
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    by = _by_name(ev)

    assert ev.direct_reference_sources == {
        "kivymd": "git+https://github.com/kivymd/KivyMD@5ff9d0d"
    }
    # kivymd still appears in declared_dependencies (its true kind, so
    # select_roots' scope rules apply to it like any other dep) -- build.py's
    # root-exclusion pass is what actually keeps it out of the resolver.
    assert by["kivymd"].kind == "dependency"
    assert by["kivymd"].specifier == ""
    # the ordinary deps parse completely normally.
    assert by["colorama"].specifier == "==0.4.6"
    assert by["PyYAML"].specifier == "==6.0.1"


def test_pyproject_dependencies_direct_reference_recorded(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = [
            "kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d",
            "requests>=2.0",
        ]
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    by = _by_name(ev)

    assert ev.direct_reference_sources == {
        "kivymd": "git+https://github.com/kivymd/KivyMD@5ff9d0d"
    }
    assert by["kivymd"].kind == "dependency"
    assert by["requests"].specifier == ">=2.0"


def test_direct_reference_http_url_recorded(tmp_path):
    _write(
        tmp_path,
        "requirements.txt",
        """
        foo @ https://example.com/foo-1.0.tar.gz
        requests==2.32.3
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.direct_reference_sources == {"foo": "https://example.com/foo-1.0.tar.gz"}
    assert _by_name(ev)["requests"].specifier == "==2.32.3"


def test_direct_reference_file_url_recorded(tmp_path):
    _write(
        tmp_path,
        "requirements.txt",
        """
        foo @ file:///tmp/foo-1.0.tar.gz
        requests==2.32.3
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.direct_reference_sources == {"foo": "file:///tmp/foo-1.0.tar.gz"}
    assert _by_name(ev)["requests"].specifier == "==2.32.3"


def test_no_direct_references_leaves_new_field_empty(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.direct_reference_sources == {}


def test_editable_and_include_option_lines_still_ignored_alongside_direct_reference(tmp_path):
    # Existing option-line handling (-e ., -e <url>, -r, -c) must be
    # completely undisturbed by the new direct-reference detection.
    _write(
        tmp_path,
        "requirements.txt",
        """
        -e .
        kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d
        flask
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    by = _by_name(ev)
    assert "." not in by
    assert "flask" in by
    assert ev.direct_reference_sources == {
        "kivymd": "git+https://github.com/kivymd/KivyMD@5ff9d0d"
    }


def test_evidence_to_dict_includes_uv_source_fields(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["widget"]

        [tool.uv.workspace]
        members = ["packages/*"]

        [[tool.uv.index]]
        name = "internal"
        url = "https://pypi-corp.example.com/simple"

        [tool.uv.sources]
        widget = { path = "./widget" }
        """,
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    payload = ev.to_dict()
    assert payload["uv_sources"] == {"widget": [{"path": "./widget"}]}
    assert payload["uv_workspace_members"] == ["packages/*"]
    assert payload["uv_indexes"] == [
        {"name": "internal", "url": "https://pypi-corp.example.com/simple"}
    ]
    assert payload["direct_reference_sources"] == {}


def test_evidence_to_dict_includes_direct_reference_sources(tmp_path):
    _write(
        tmp_path,
        "requirements.txt",
        "kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d\n",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    payload = ev.to_dict()
    assert payload["direct_reference_sources"] == {
        "kivymd": "git+https://github.com/kivymd/KivyMD@5ff9d0d"
    }


def test_evidence_uv_source_fields_default_empty():
    ev = PythonDependencyEvidence(repo_path="/x")
    assert ev.uv_sources == {}
    assert ev.uv_workspace_members == ()
    assert ev.uv_indexes == ()
    assert ev.direct_reference_sources == {}


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


def _canon_deps(evidence, kind):
    return {(r.name, r.kind, r.source) for r in evidence.declared_dependencies if r.kind == kind}


def test_collect_dependency_groups_basic(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "proj"
            version = "0.1.0"
            dependencies = ["flask"]

            [dependency-groups]
            test = ["pytest", "pytest-cov"]
            """
        ),
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    dev = _canon_deps(ev, "dev_group")
    assert ("pytest", "dev_group", "pyproject.toml:dependency-groups.test") in dev
    assert ("pytest-cov", "dev_group", "pyproject.toml:dependency-groups.test") in dev
    # runtime dep still classified as dependency
    assert any(r.name == "flask" and r.kind == "dependency" for r in ev.declared_dependencies)


def test_collect_dependency_groups_include_group_flattens_transitively(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "proj"
            version = "0.1.0"

            [dependency-groups]
            test = ["pytest"]
            typing = [{include-group = "test"}, "mypy"]
            """
        ),
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    typing = {r.name for r in ev.declared_dependencies
              if r.source == "pyproject.toml:dependency-groups.typing"}
    assert typing == {"pytest", "mypy"}  # test's member flattened under typing


def test_collect_dependency_groups_cycle_terminates_and_records_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "proj"
            version = "0.1.0"

            [dependency-groups]
            a = [{include-group = "b"}, "pkg-a"]
            b = [{include-group = "a"}, "pkg-b"]
            """
        ),
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))  # must not hang
    names = {r.name for r in ev.declared_dependencies if r.kind == "dev_group"}
    assert "pkg-a" in names and "pkg-b" in names
    assert any("cycle" in e.lower() for e in ev.collection_errors)


def test_collect_dependency_groups_absent_is_noop(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "proj"
            version = "0.1.0"
            dependencies = ["flask"]
            """
        ),
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert not [r for r in ev.declared_dependencies if r.kind == "dev_group"]


def _by_name(evidence):
    return {r.name: r for r in evidence.declared_dependencies}


def test_requirements_txt_is_runtime(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert _by_name(ev)["flask"].kind == "dependency"


def test_requirements_dev_is_dev_group(tmp_path):
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["pytest"]
    assert req.kind == "dev_group"
    assert req.source == "requirements-file.dev"


def test_nested_docs_requirements_is_dev_group_docs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.txt").write_text("sphinx\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["sphinx"]
    assert req.kind == "dev_group"
    assert req.source == "requirements-file.docs"


def test_nested_requirements_dir_test_file_is_dev_group_test(tmp_path):
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "test.txt").write_text("pytest-xdist\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["pytest-xdist"]
    assert req.kind == "dev_group"
    assert req.source == "requirements-file.test"


def test_nested_requirements_dir_base_file_is_runtime(tmp_path):
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "base.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert _by_name(ev)["flask"].kind == "dependency"


def test_editable_self_extras_captured_into_used_extras(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "-e .[http2,socks]\npytest\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert {"http2", "socks"} <= ev.used_extras
    # the -e line is NOT added as a distribution named "."/project
    assert "." not in _by_name(ev)


def test_bare_editable_self_is_ignored(tmp_path):
    (tmp_path / "requirements.txt").write_text("-e .\nflask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.used_extras == set()
    assert "flask" in _by_name(ev)


def test_dash_r_include_is_followed_with_referenced_file_role(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("-r requirements.txt\npytest\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    by = _by_name(ev)
    assert by["flask"].kind == "dependency"       # base file's role
    assert by["pytest"].kind == "dev_group"        # dev file's role
    assert by["pytest"].source == "requirements-file.dev"


def test_dash_r_self_cycle_terminates(tmp_path):
    (tmp_path / "requirements.txt").write_text("-r requirements.txt\nflask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))  # must not hang
    assert "flask" in _by_name(ev)


def test_option_lines_are_ignored(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "--index-url https://example.com/simple\n-i https://example.com/simple\nflask\n",
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert set(_by_name(ev)) == {"flask"}


def test_dash_c_constraint_include_routes_to_constraints_not_declared(tmp_path):
    # Bug this guards: a -c/--constraint include must land in
    # evidence.constraint_dependencies (kind="constraint"), NOT be followed as
    # a -r/--requirement include into declared_dependencies.
    (tmp_path / "requirements.txt").write_text(
        "-c constraints.txt\nflask\n", encoding="utf-8"
    )
    (tmp_path / "constraints.txt").write_text("foo==1.0\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))

    assert _by_name(ev)["flask"].kind == "dependency"
    assert "foo" not in _by_name(ev)

    constraint = {r.name: r for r in ev.constraint_dependencies}["foo"]
    assert constraint.kind == "constraint"


def test_dash_c_include_target_is_visited_not_also_soft_installed(tmp_path):
    # Finding 2 (MEDIUM): a -c/--constraint include target must be added to
    # `visited` too, not just read for its constraint lines. Before the fix,
    # `nested/requirements.txt` (the -c target) was never marked visited, so
    # it ALSO survived as a SOFT candidate and got listed in
    # soft_requirements_files -- which the renderer would then
    # `pip install -r` on top of the pinned closure, converting a constraint
    # file into an install list. It must be visited (and therefore excluded
    # from soft) exactly like a -r/--requirement target already is.
    (tmp_path / "requirements.txt").write_text(
        "-c nested/requirements.txt\nflask\n", encoding="utf-8"
    )
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "requirements.txt").write_text(
        "urllib3==1.26\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))

    assert _by_name(ev)["flask"].kind == "dependency"
    assert "urllib3" not in _by_name(ev)
    constraint = {r.name: r for r in ev.constraint_dependencies}["urllib3"]
    assert constraint.kind == "constraint"
    # The -c target must never also appear as a soft best-effort install.
    assert ev.soft_requirements_files == []


def test_nested_tests_dir_requirements_file_is_discovered(tmp_path):
    # Nested-dir allowlist branch: tests/*requirements*.txt is discovered even
    # though it isn't a root-level requirements*.txt file.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "requirements.txt").write_text(
        "pytest-mock\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["pytest-mock"]
    assert req.kind == "dev_group"
    assert req.source == "requirements-file.test"


def test_include_depth_cap_bites_on_linear_chain(tmp_path):
    # _MAX_INCLUDE_DEPTH = 5; the guard in _ingest_requirements_file is
    # `depth > _MAX_INCLUDE_DEPTH`, so depths 0..5 are processed and depth 6
    # is cut off before its file is even read. requirements.txt is discovered
    # at depth 0; chain1..chain6.txt are reachable ONLY via -r includes (they
    # don't match the root requirements*.txt glob), landing at depths 1..6.
    #
    # Finding 3 (MEDIUM): the guard used to return silently -- a six-level -r
    # chain dropped pkg6's declaration with NO trace at all (not hard, not
    # soft, not an error). The guard itself is still correct (a cycle/blowup
    # defence) but must now be LOUD: record a collection_errors entry naming
    # the cut-off file and depth.
    (tmp_path / "requirements.txt").write_text("-r chain1.txt\npkg0\n", encoding="utf-8")
    for i in range(1, 6):
        (tmp_path / f"chain{i}.txt").write_text(
            f"-r chain{i + 1}.txt\npkg{i}\n", encoding="utf-8"
        )
    (tmp_path / "chain6.txt").write_text("pkg6\n", encoding="utf-8")

    ev = collect_python_dependency_evidence(str(tmp_path))
    names = set(_by_name(ev))

    for i in range(6):  # pkg0..pkg5: depths 0..5, all processed
        assert f"pkg{i}" in names
    assert "pkg6" not in names  # depth 6: guard `depth > 5` returns before reading
    assert any(
        "chain6.txt" in e and "depth" in e.lower()
        for e in ev.collection_errors
    )


def test_root_ci_requirements_suffix_convention_is_discovered(tmp_path):
    # FIX 1 (A2): the root-level requirements glob was PREFIX-anchored
    # ("requirements*.txt"), so it missed the common SUFFIX convention
    # ArchipelagoMW/Archipelago's `ci-requirements.txt` uses (gold installs
    # it). The nested-dir allowlist already substring-matches ("requirement"
    # in candidate.name.lower()); the root-level check must be consistent.
    (tmp_path / "ci-requirements.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert "flask" in _by_name(ev)


def test_root_test_requirements_suffix_convention_is_dev_group(tmp_path):
    (tmp_path / "test-requirements.txt").write_text("pytest\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["pytest"]
    assert req.kind == "dev_group"


def test_root_dev_requirements_suffix_convention_is_discovered(tmp_path):
    (tmp_path / "dev-requirements.txt").write_text("black\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["black"]
    assert req.kind == "dev_group"
    assert req.source == "requirements-file.dev"


def test_deeply_nested_requirements_files_discovered_via_recursive_walk(tmp_path):
    # FIX 2 (B4): ArchipelagoMW/Archipelago produced ZERO pip packages because
    # its real dependencies live in ~84 `worlds/*/requirements.txt` files plus
    # `WebHostLib/requirements.txt` -- directory names never on the old fixed
    # 4-dir allowlist (requirements/, tests/, test/, docs/). Gold succeeded by
    # looping `for f in worlds/*/requirements.txt; do pip install -r "$f" ...`.
    #
    # Task 1+2 (hard/soft split): the recursive walk finding these files must
    # NOT make them hard resolver roots -- none of `worlds/alttp`,
    # `worlds/factorio`, `WebHostLib` is the repo root or a top-level
    # requirements/tests/test/docs directory, so all three land in
    # `soft_requirements_files` and declared_dependencies stays empty. This is
    # what the invariant demands: discovery widened, roots did not.
    (tmp_path / "worlds" / "alttp").mkdir(parents=True)
    (tmp_path / "worlds" / "alttp" / "requirements.txt").write_text(
        "z3-solver\n", encoding="utf-8"
    )
    (tmp_path / "worlds" / "factorio").mkdir(parents=True)
    (tmp_path / "worlds" / "factorio" / "requirements.txt").write_text(
        "factorio-data\n", encoding="utf-8"
    )
    (tmp_path / "WebHostLib").mkdir()
    (tmp_path / "WebHostLib" / "requirements.txt").write_text(
        "flask\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    names = set(_by_name(ev))
    assert not ({"z3-solver", "factorio-data", "flask"} & names)
    assert ev.soft_requirements_files == [
        "WebHostLib/requirements.txt",
        "worlds/alttp/requirements.txt",
        "worlds/factorio/requirements.txt",
    ]


def test_recursive_discovery_prunes_vendored_and_venv_dirs(tmp_path):
    # The bounded walk must still skip vendored/build/vcs/venv noise -- it is
    # bounded, not a full uncontrolled tree walk.
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "requirements.txt").write_text(
        "should-not-be-collected\n", encoding="utf-8"
    )
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "requirements.txt").write_text(
        "also-not-collected\n", encoding="utf-8"
    )
    (tmp_path / "build" / "lib").mkdir(parents=True)
    (tmp_path / "build" / "lib" / "requirements.txt").write_text(
        "build-artifact-not-collected\n", encoding="utf-8"
    )
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert set(_by_name(ev)) == {"flask"}


def test_recursive_discovery_file_count_is_capped(tmp_path, monkeypatch):
    # A sane cap so a pathological repo can't make discovery explode; the cap
    # must never silently swallow the fact that it was hit.
    #
    # Finding 5 (LOW): all ten files here are SOFT (none sits at the repo
    # root or under a top-level requirements/tests/test/docs dir), so the
    # ORIGINAL assertion (`len(_by_name(ev)) <= 3`) was vacuously true no
    # matter how many soft files survived the cap -- none of them is ever
    # ingested into declared_dependencies regardless. Assert the actual
    # capped quantity instead: the retained SOFT path count, and that a
    # specific excluded file really is gone.
    import python_deps.evidence as evidence_module

    cap = 3
    monkeypatch.setattr(evidence_module, "_MAX_DISCOVERED_REQUIREMENTS_FILES", cap)
    # "Also" note: derive the file count (and the dropped file's name) from
    # `cap` rather than hard-coding a magic literal -- a hard-coded name
    # (e.g. "pkg9") silently stops meaning anything if `cap` ever changes.
    n_files = cap + 7  # comfortably over the cap so several are guaranteed dropped
    for i in range(n_files):
        d = tmp_path / f"pkg{i}"
        d.mkdir()
        (d / "requirements.txt").write_text(f"dep{i}\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert _by_name(ev) == {}  # still zero -- all are soft, none is hard
    assert len(ev.soft_requirements_files) == cap
    dropped_index = n_files - 1  # lexically last -- guaranteed dropped
    assert f"pkg{dropped_index}/requirements.txt" not in ev.soft_requirements_files
    assert any(
        "cap" in e.lower() and "requirements" in e.lower()
        for e in ev.collection_errors
    )


def test_requirements_dir_base_file_still_discovered_when_recursive(tmp_path):
    # Regression: a `requirements/` directory allowlists ALL *.txt files
    # (e.g. requirements/base.txt has no "requirement" substring of its own);
    # the recursive rewrite must preserve this without the fixed-dir special
    # case.
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "base.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert _by_name(ev)["flask"].kind == "dependency"


def test_requirements_file_source_survives_symlinked_root(tmp_path):
    # Bug: _ingest_requirements_file resolves `path` (for cycle detection) but
    # NOT `root` before computing the relpath-based `source`. On a repo root
    # that resolves through a symlink (e.g. macOS /var -> /private/var), the
    # divergent prefix makes os.path.relpath walk upward, corrupting `source`
    # into something like "../../../../private/var/.../requirements.txt"
    # instead of the clean relative path "requirements.txt".
    #
    # Constructed via an explicit symlink (real/proj -> link/proj) so the
    # root/resolved divergence is forced on every platform, not just macOS.
    real_root = tmp_path / "real" / "proj"
    real_root.mkdir(parents=True)
    (real_root / "requirements.txt").write_text("flask\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real_root.parent)
    repo_path = link / "proj"

    ev = collect_python_dependency_evidence(str(repo_path))
    req = _by_name(ev)["flask"]

    assert req.source == "requirements.txt"
    assert ".." not in req.source
    assert "/private/" not in req.source


# --------------------------------------------------------------------------- #
# Task 1+2 -- split requirements-file DISCOVERY from ROLE.
#
# The recursive walk (FIX 2/B4 above) widened WHICH files are found. That is
# safe only if finding a file is kept separate from promoting it to a hard
# resolve root: HARD is exactly what the pre-walk 4-directory allowlist would
# have found (repo root, or a top-level requirements/tests/test/docs
# directory); everything else the recursive walk newly finds is SOFT --
# recorded for a later best-effort, non-blocking install, never ingested into
# declared_dependencies.
# --------------------------------------------------------------------------- #


def test_root_requirements_file_is_hard_root_not_soft(tmp_path):
    # 1. A qualifying root-level file (including the FIX 1/A2 suffix
    # convention) must still land in declared_dependencies, and must not also
    # show up as soft.
    (tmp_path / "ci-requirements.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert "flask" in _by_name(ev)
    assert ev.soft_requirements_files == []


def test_world_requirements_file_is_soft_not_declared(tmp_path):
    # 2. A per-world file two levels below a non-allowlisted top-level
    # directory is discovered but must never become a hard root.
    (tmp_path / "worlds" / "aquaria").mkdir(parents=True)
    (tmp_path / "worlds" / "aquaria" / "requirements.txt").write_text(
        "aquaria-dep\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.soft_requirements_files == ["worlds/aquaria/requirements.txt"]
    assert "aquaria-dep" not in _by_name(ev)


def test_test_fixture_requirements_file_does_not_poison_declared_roots(tmp_path):
    # 3. The poisoning case: a deliberately-ancient pin inside a test FIXTURE
    # requirements file must never become a real resolver root just because
    # the recursive walk can now see it.
    (tmp_path / "tests" / "fixtures" / "upstream").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "upstream" / "requirements.txt").write_text(
        "requests==2.0.0\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert "requests" not in _by_name(ev)
    assert ev.soft_requirements_files == ["tests/fixtures/upstream/requirements.txt"]


def test_top_level_docs_requirements_file_still_hard(tmp_path):
    # 4. Companion to test_nested_docs_requirements_is_dev_group_docs (which
    # must keep passing unmodified): a top-level docs/requirements.txt stays
    # HARD, and nothing leaks into soft_requirements_files for it.
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.txt").write_text("sphinx\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert "sphinx" in _by_name(ev)
    assert ev.soft_requirements_files == []


def test_top_level_requirements_dir_base_file_still_hard(tmp_path):
    # 5. requirements/base.txt (top-level requirements/ directory) stays HARD.
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "base.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert "flask" in _by_name(ev)
    assert ev.soft_requirements_files == []


def test_capitalized_top_level_tests_dir_is_soft_not_hard_case_sensitive(tmp_path):
    # Finding 1 (HIGH): the pre-walk allowlist opened only the EXACT paths
    # root/"tests", root/"test", root/"requirements", root/"docs" --
    # case-sensitively. On a case-sensitive filesystem (Linux -- production,
    # in Docker) `Tests/requirements.txt` (capital T) is a DIFFERENT path from
    # `tests/requirements.txt` and must stay SOFT: a pin like
    # `requests==2.0.0` here must never silently constrain the global lock.
    (tmp_path / "Tests").mkdir()
    (tmp_path / "Tests" / "requirements.txt").write_text(
        "requests==2.0.0\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert "requests" not in _by_name(ev)
    assert ev.soft_requirements_files == ["Tests/requirements.txt"]


def test_symlinked_top_level_allowlist_dir_is_still_hard_root(tmp_path):
    # Finding 1 (HIGH, round 2): `os.walk()` never descends into a symlinked
    # directory (no `followlinks=True`, deliberately -- see module
    # docstring), so `repo/tests -> /some/dir` used to lose
    # `/some/dir/requirements.txt` silently: no dependency, no soft path, no
    # error -- a hard root vanished. HARD is now computed by a direct,
    # non-recursive glob of each allowlisted top-level directory
    # (`_discover_hard_requirements_files`), and `Path.glob` follows a
    # symlinked directory transparently (same as the OLD pre-walk allowlist's
    # own `(root / "tests").glob(...)`), so the file is a hard root again.
    external = tmp_path / "external"
    external.mkdir()
    (external / "requirements.txt").write_text("flask\n", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").symlink_to(external, target_is_directory=True)

    ev = collect_python_dependency_evidence(str(repo))
    assert _by_name(ev)["flask"].kind == "dependency"
    assert ev.soft_requirements_files == []


def test_cap_never_drops_a_hard_root(tmp_path, monkeypatch):
    # Finding 2 (MEDIUM): the cap must apply ONLY to SOFT files. HARD files
    # come from a bounded, non-recursive set (repo root + four top-level
    # dirs) and can never explode, so capping them the same way the
    # (unbounded) recursive walk's SOFT output is capped could silently drop
    # a real hard root -- present in neither declared_dependencies nor
    # soft_requirements_files. Every one of these ten HARD files must survive
    # even though the (monkeypatched, tiny) cap is far smaller than ten.
    import python_deps.evidence as evidence_module

    monkeypatch.setattr(evidence_module, "_MAX_DISCOVERED_REQUIREMENTS_FILES", 3)
    (tmp_path / "requirements").mkdir()
    for i in range(10):
        (tmp_path / "requirements" / f"pkg{i}-requirements.txt").write_text(
            f"dep{i}\n", encoding="utf-8"
        )
    ev = collect_python_dependency_evidence(str(tmp_path))
    names = set(_by_name(ev))
    for i in range(10):
        assert f"dep{i}" in names


def test_dash_r_include_is_trusted_transitively_hard_regardless_of_location(tmp_path):
    # Finding 3 (part 1): an explicit `-r` include inside a HARD file is the
    # repo ITSELF declaring "this is my dependency" -- trusted and
    # transitively HARD, even though the included file's own location (three
    # levels deep under tests/) is outside the top-level allowlist the
    # recursive walk uses to decide hard-vs-soft for files it discovers
    # unprompted.
    (tmp_path / "requirements.txt").write_text(
        "-r tests/fixtures/upstream/requirements.txt\nflask\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "fixtures" / "upstream").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "upstream" / "requirements.txt").write_text(
        "requests==2.0.0\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    by = _by_name(ev)
    assert "flask" in by
    assert "requests" in by  # trusted transitively-hard include


def test_included_file_is_not_also_listed_soft(tmp_path):
    # Finding 3 (part 2): the same included file above is ALSO independently
    # discoverable by the recursive walk (it qualifies on filename alone). It
    # must not be double-listed as soft -- that would make the renderer
    # redundantly re-`pip install -r` it best-effort on top of the pinned
    # closure it already contributed hard requirements to.
    (tmp_path / "requirements.txt").write_text(
        "-r tests/fixtures/upstream/requirements.txt\nflask\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "fixtures" / "upstream").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "upstream" / "requirements.txt").write_text(
        "requests==2.0.0\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.soft_requirements_files == []


def test_soft_requirements_file_symlink_escaping_repo_is_skipped_with_error(tmp_path):
    # Finding 4 (MEDIUM): a soft requirements file whose LEAF is a symlink
    # pointing outside the repo must not be silently promoted to an
    # out-of-repo path like "../../tmp/other.txt" -- the renderer would
    # happily `pip install -r` that, producing a wrong environment instead of
    # a loud failure. It must be skipped and the skip recorded.
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "other.txt"
    target.write_text("mystery-pkg\n", encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / "worlds" / "x").mkdir(parents=True)
    link = repo / "worlds" / "x" / "requirements.txt"
    link.symlink_to(target)

    ev = collect_python_dependency_evidence(str(repo))
    assert ev.soft_requirements_files == []
    assert "mystery-pkg" not in _by_name(ev)
    assert any(
        ("escape" in e.lower() or "outside" in e.lower()) and "requirements" in e.lower()
        for e in ev.collection_errors
    )


def test_dangling_soft_symlink_is_recorded_not_silently_skipped(tmp_path):
    # Finding 4 (LOW): a DANGLING (broken) soft symlink -- one whose target
    # does not exist at all, as opposed to the escaping-but-real-file case
    # above -- must not be silently dropped either. Before the fix,
    # `not candidate.is_file()` rejected the candidate before the containment
    # check ever ran, producing neither a soft path nor a collection error.
    repo = tmp_path / "repo"
    (repo / "worlds" / "x").mkdir(parents=True)
    link = repo / "worlds" / "x" / "requirements.txt"
    link.symlink_to(repo / "worlds" / "x" / "does-not-exist.txt")

    ev = collect_python_dependency_evidence(str(repo))
    assert ev.soft_requirements_files == []
    assert any(
        ("broken" in e.lower() or "dangling" in e.lower() or "not a regular file" in e.lower())
        and "requirements" in e.lower()
        for e in ev.collection_errors
    )


def test_cap_keeps_shallowest_when_deep_vendored_subtree_sorts_first(tmp_path):
    # 6. Order-independent cap: reproduce the mid-walk early-return bug. A
    # lexically-early ("_vendor" sorts before "worlds"), DEEPLY nested
    # vendored subtree with more than _MAX_DISCOVERED_REQUIREMENTS_FILES
    # qualifying files must not be able to consume the whole cap before the
    # walk ever reaches a shallower "real" project file that sorts later.
    # The fix walks fully, then keeps the shallowest N by (depth, path) --
    # the vendored files here are nested one level deeper than the real file
    # specifically so depth (not lexical order) decides survival.
    import python_deps.evidence as evidence_module

    n_vendor = evidence_module._MAX_DISCOVERED_REQUIREMENTS_FILES + 50
    for i in range(n_vendor):
        vendor_dir = tmp_path / "_vendor" / f"pkg{i:04d}" / "nested"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "requirements.txt").write_text(f"vendor-dep-{i}\n", encoding="utf-8")

    (tmp_path / "worlds" / "aquaria").mkdir(parents=True)
    (tmp_path / "worlds" / "aquaria" / "requirements.txt").write_text(
        "aquaria-dep\n", encoding="utf-8"
    )

    ev = collect_python_dependency_evidence(str(tmp_path))

    assert "worlds/aquaria/requirements.txt" in ev.soft_requirements_files
    # Finding 5 (LOW): strengthen beyond "the shallow one survived" -- assert
    # the exact retained count (exactly the cap) and that a specific deep
    # vendored file (the lexically-last one, guaranteed to sort into the
    # dropped tail regardless of exactly how many are dropped) is absent.
    assert len(ev.soft_requirements_files) == evidence_module._MAX_DISCOVERED_REQUIREMENTS_FILES
    # "Also" note: derive the dropped file's name from `n_vendor` (itself
    # derived from the cap) instead of hard-coding the magic literal
    # "pkg0549" -- a hard-coded name silently stops testing anything once the
    # cap constant changes.
    last_vendor_index = n_vendor - 1
    assert (
        f"_vendor/pkg{last_vendor_index:04d}/nested/requirements.txt"
        not in ev.soft_requirements_files
    )
    assert any(
        "cap" in e.lower() and "requirements" in e.lower()
        for e in ev.collection_errors
    )
