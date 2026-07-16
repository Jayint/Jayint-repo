from __future__ import annotations

import ast
import configparser
import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from packaging.requirements import InvalidRequirement, Requirement

from .import_graph import collect_pydeps_evidence, scan_imports
from .import_mapping import is_unresolved, map_import_to_package, normalize_package_name
from .models import (
    ImportPackageMapping,
    PythonDependencyEvidence,
    PythonRequirement,
    PythonVersionRequirement,
)

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 — fall back to the tomli backport.
    # WITHOUT this fallback the whole declared-dependency reader silently returns
    # ZERO requirements on a <3.11 interpreter (e.g. the 3.10 benchmark box):
    # `tomllib is None` -> pyproject parsing is skipped -> select_roots gets no
    # roots -> the entire declared closure (runtime AND dev/test groups) vanishes.
    # Every other tomllib site in this codebase already does this; evidence.py was
    # the lone exception. tomli is declared in requirements.txt for python<3.11.
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - neither parser available
        tomllib = None


def collect_python_dependency_evidence(repo_path: str | Path) -> PythonDependencyEvidence:
    root = Path(repo_path)
    evidence = PythonDependencyEvidence(repo_path=str(root))

    collectors = (
        _collect_pyproject_metadata,
        _collect_dependency_groups,
        _collect_setup_cfg_metadata,
        _collect_setup_py_metadata,
        _collect_requirements_files,
        _collect_constraints_files,
        # Task 4/7: full `[tool.uv.sources]`/`[tool.uv.workspace]`/
        # `[[tool.uv.index]]` capture (additive -- does not affect
        # declared_dependencies/roots; a declared dep's true `kind` is never
        # overwritten, so select_roots applies the same scope rules to it as
        # to every other declared dependency -- see `uv_source_config` for
        # what this carries and to whom).
        _collect_uv_source_config,
    )
    for collector in collectors:
        try:
            collector(root, evidence)
        except Exception as error:  # Evidence collection must not abort an agent run.
            evidence.collection_errors.append(f"{collector.__name__}: {error}")

    try:
        imports, project_local_modules, import_errors = scan_imports(root)
        evidence.imports.extend(imports)
        evidence.project_local_modules.extend(project_local_modules)
        evidence.collection_errors.extend(import_errors)
    except Exception as error:
        evidence.collection_errors.append(f"scan_imports: {error}")

    evidence.pydeps = collect_pydeps_evidence(root)
    evidence.import_package_mappings.extend(_build_import_mappings(evidence))
    return evidence


def _collect_pyproject_metadata(root: Path, evidence: PythonDependencyEvidence) -> None:
    path = root / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    requires_python = project.get("requires-python")
    if isinstance(requires_python, str) and requires_python.strip():
        evidence.python_requires.append(
            PythonVersionRequirement(
                specifier=requires_python.strip(),
                source="pyproject.toml:project.requires-python",
            )
        )
    for requirement in project.get("dependencies", []) or []:
        _add_requirement_line(
            evidence.declared_dependencies,
            requirement,
            "pyproject.toml:project.dependencies",
            evidence=evidence,
        )
    optional_dependencies = project.get("optional-dependencies", {}) or {}
    if isinstance(optional_dependencies, dict):
        for group, requirements in optional_dependencies.items():
            for requirement in requirements or []:
                _add_requirement_line(
                    evidence.declared_dependencies,
                    requirement,
                    f"pyproject.toml:project.optional-dependencies.{group}",
                    kind="optional_dependency",
                    trust="medium",
                    evidence=evidence,
                )

    poetry_dependencies = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    if isinstance(poetry_dependencies, dict):
        for name, value in poetry_dependencies.items():
            if name.lower() == "python":
                if isinstance(value, str):
                    evidence.python_requires.append(
                        PythonVersionRequirement(
                            specifier=value,
                            source="pyproject.toml:tool.poetry.dependencies.python",
                        )
                    )
                continue
            specifier = value if isinstance(value, str) else ""
            evidence.declared_dependencies.append(
                PythonRequirement(
                    name=name,
                    specifier=specifier,
                    source="pyproject.toml:tool.poetry.dependencies",
                )
            )


def _collect_dependency_groups(root: Path, evidence: PythonDependencyEvidence) -> None:
    """PEP 735 ``[dependency-groups]`` reader.

    Each group maps to a list whose members are requirement strings and/or
    ``{include-group = "<name>"}`` reference objects. include-group references are
    resolved transitively (a group may include another group) with cycle
    detection; the flattened requirements are attributed to the TOP-LEVEL group
    being expanded and tagged ``kind="dev_group"``.
    """
    path = root / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    groups = data.get("dependency-groups", {})
    if not isinstance(groups, dict):
        return
    for group_name in groups:
        if not isinstance(group_name, str):
            continue
        requirements, cycle = _resolve_dependency_group(group_name, groups, ())
        if cycle:
            evidence.collection_errors.append(
                f"_collect_dependency_groups: include-group cycle involving '{group_name}'"
            )
        for requirement in requirements:
            _add_requirement_line(
                evidence.declared_dependencies,
                requirement,
                f"pyproject.toml:dependency-groups.{group_name}",
                kind="dev_group",
                trust="medium",
                evidence=evidence,
            )


def _resolve_dependency_group(
    name: str, groups: dict, seen: tuple[str, ...]
) -> tuple[list[str], bool]:
    """Flatten a dependency-group's members to concrete requirement strings.

    Returns ``(requirement_strings, cycle_detected)``. ``include-group`` refs are
    expanded depth-first; a group already on the current ``seen`` path is a cycle:
    its expansion is truncated (skipped) and ``cycle_detected`` is set True.
    """
    if name in seen:
        return [], True
    members = groups.get(name)
    if not isinstance(members, list):
        return [], False
    out: list[str] = []
    cycle = False
    for member in members:
        if isinstance(member, str):
            out.append(member)
        elif isinstance(member, dict) and isinstance(member.get("include-group"), str):
            sub, sub_cycle = _resolve_dependency_group(member["include-group"], groups, seen + (name,))
            out.extend(sub)
            cycle = cycle or sub_cycle
    return out, cycle


# --------------------------------------------------------------------------- #
# `[tool.uv.sources]` / `[tool.uv.workspace]` / `[[tool.uv.index]]`.
#
# PostHog/posthog (77,642 gold tests -- the largest repo in the corpus)
# produced ZERO pip packages: its pyproject.toml declares 263 ordinary deps
# PLUS `[tool.uv.sources] hogli = { workspace = true }` (an internal workspace
# package with no PyPI existence at all) and a couple of git-pinned forks
# (`infi-clickhouse-orm`, `pytest-split`). resolve.py's synthetic throwaway
# pyproject (`_write_pyproject`) used to write every declared name as a bare
# PyPI dependency -- one unresolvable name (`hogli`) made `uv lock` fail for
# the ENTIRE 263-package closure at once.
#
# An earlier fix re-tagged any such dependency's `kind` to a sentinel so
# roots.py's `_in_test_scope` would drop it (falls through to `return False`
# for an unrecognized kind) -- which kept `uv lock` from dying, but also
# destroyed the dependency's TRUE kind (`dependency` / `optional_dependency` /
# `dev_group`), the only thing `select_roots` uses to decide scope. That
# forced a second pass (`build.py`'s since-deleted `_reinstate_uv_sourced_roots`)
# to re-add these deps as roots AFTER `select_roots` had already run --
# bypassing scope filtering entirely (a `[tool.uv.sources]`-carrying dep in a
# never-activated optional-dependency group would still resurface as a root).
#
# The real fix: carry the source spec through instead of dropping the name.
# `uv_source_config`/`_collect_uv_source_config` below capture the FULL
# `[tool.uv.sources]` / `[tool.uv.workspace]` / `[[tool.uv.index]]` config onto
# ``evidence.uv_sources`` / ``.uv_workspace_members`` / ``.uv_indexes``,
# verbatim, WITHOUT ever touching ``declared_dependencies`` or its `kind`
# field. `resolve.py` reads this evidence and renders the matching
# `[tool.uv.sources]`/`[tool.uv.workspace]`/`[[tool.uv.index]]` tables into its
# synthetic pyproject (see resolve_closure's ``uv_sources``/``uv_indexes``/
# ``workspace_members`` parameters), so `uv lock` can actually resolve a
# workspace/git/url/path/index-sourced name instead of needing it excluded.
# A declared dependency's `kind` therefore always reflects its real
# declaration, so `select_roots` applies the SAME scope rules to a
# `[tool.uv.sources]`-carrying dep as to every other declared dependency --
# no special case, no bypass. The one thing that DOES still key off
# `evidence.uv_sources` is `build.py`'s Phase-A repair-ladder protection
# (`_declared_package_names_for_repair`): a git-pinned `acme-sdk` must never
# be "repaired" into the unrelated, same-named PUBLIC PyPI package -- that
# HIGH-bug protection is real and stays, keyed off `uv_sources` presence, not
# off any `kind` sentinel.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UvSourceConfig:
    """Full, verbatim `[tool.uv.sources]` / `[tool.uv.workspace]` / `[[tool.uv.index]]` read.

    Every source spec dict is kept verbatim so a consumer (`resolve.py`, which
    carries these sources into its synthetic throwaway pyproject) has
    `rev`/`tag`/`subdirectory`/`marker`/`editable`/etc. without having to
    re-parse `pyproject.toml` itself or guess at what an earlier pass threw
    away.

    See `collect_python_dependency_evidence` for where this is populated onto
    the evidence object (`_collect_uv_source_config`), and the module note
    above for why this capture is kept entirely separate from
    `declared_dependencies`/`kind`.
    """

    sources: Mapping[str, tuple[dict, ...]] = field(default_factory=dict)
    workspace_members: tuple[str, ...] = ()
    indexes: tuple[dict, ...] = ()


def uv_source_config(root: Path) -> UvSourceConfig:
    """Read the full uv source-override config for ``root``, verbatim.

    Own guarded TOML parse, independent of every other collector: any parse
    issue -- missing file, unavailable ``tomllib``, malformed TOML, an
    unexpected shape -- yields an empty ``UvSourceConfig``, never raises.

    A `[tool.uv.sources]` entry's spec may be a single table or a LIST of
    tables (uv's marker-conditional-source form); both shapes are normalised
    to a tuple of dicts here so callers never have to branch on it again.
    Names are canonicalised with `normalize_package_name` so e.g. `Foo_Bar` in
    the TOML is retrievable as `foo-bar`.
    """
    empty = UvSourceConfig()
    path = root / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return empty
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty

    tool_uv = data.get("tool", {}).get("uv", {})
    if not isinstance(tool_uv, dict):
        return empty

    raw_sources = tool_uv.get("sources", {})
    sources: dict[str, tuple[dict, ...]] = {}
    if isinstance(raw_sources, dict):
        for name, spec in raw_sources.items():
            if not isinstance(name, str):
                continue
            entries = spec if isinstance(spec, list) else [spec]
            normalised = tuple(entry for entry in entries if isinstance(entry, dict))
            if normalised:
                sources[normalize_package_name(name)] = normalised

    workspace = tool_uv.get("workspace", {})
    members: tuple[str, ...] = ()
    if isinstance(workspace, dict):
        raw_members = workspace.get("members", [])
        if isinstance(raw_members, list):
            members = tuple(member for member in raw_members if isinstance(member, str))

    raw_indexes = tool_uv.get("index", [])
    indexes: tuple[dict, ...] = ()
    if isinstance(raw_indexes, list):
        indexes = tuple(entry for entry in raw_indexes if isinstance(entry, dict))

    return UvSourceConfig(sources=sources, workspace_members=members, indexes=indexes)


def _collect_uv_source_config(root: Path, evidence: PythonDependencyEvidence) -> None:
    """Populate ``evidence.uv_sources`` / ``uv_workspace_members`` / ``uv_indexes``.

    Pure passthrough of :func:`uv_source_config` onto the evidence object --
    that function does the actual parsing/normalising. This never reads or
    touches ``declared_dependencies``, so it cannot change which requirements
    become resolver roots or their `kind` -- `select_roots` alone (via each
    requirement's true, never-retagged `kind`) decides that.
    """
    config = uv_source_config(root)
    evidence.uv_sources = dict(config.sources)
    evidence.uv_workspace_members = config.workspace_members
    evidence.uv_indexes = config.indexes


def _collect_setup_cfg_metadata(root: Path, evidence: PythonDependencyEvidence) -> None:
    path = root / "setup.cfg"
    if not path.is_file():
        return
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if parser.has_option("options", "python_requires"):
        evidence.python_requires.append(
            PythonVersionRequirement(
                specifier=parser.get("options", "python_requires").strip(),
                source="setup.cfg:options.python_requires",
            )
        )
    if parser.has_option("options", "install_requires"):
        for line in _split_multiline_value(parser.get("options", "install_requires")):
            _add_requirement_line(
                evidence.declared_dependencies,
                line,
                "setup.cfg:options.install_requires",
                evidence=evidence,
            )
    if parser.has_section("options.extras_require"):
        for group, value in parser.items("options.extras_require"):
            for line in _split_multiline_value(value):
                _add_requirement_line(
                    evidence.declared_dependencies,
                    line,
                    f"setup.cfg:options.extras_require.{group}",
                    kind="optional_dependency",
                    trust="medium",
                    evidence=evidence,
                )


def _collect_setup_py_metadata(root: Path, evidence: PythonDependencyEvidence) -> None:
    path = root / "setup.py"
    if not path.is_file():
        return
    content = path.read_text(encoding="utf-8")
    if len(content) > 250_000:
        evidence.collection_errors.append("setup.py: skipped metadata parse because file is too large")
        return
    try:
        tree = ast.parse(content)
    except SyntaxError as error:
        evidence.collection_errors.append(f"setup.py: syntax error while parsing metadata: {error}")
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if func_name != "setup":
            continue
        for keyword in node.keywords:
            if keyword.arg == "python_requires":
                value = _literal_string(keyword.value)
                if value:
                    evidence.python_requires.append(
                        PythonVersionRequirement(
                            specifier=value,
                            source="setup.py:setup.python_requires",
                        )
                    )
            elif keyword.arg == "install_requires":
                for requirement in _literal_string_list(keyword.value):
                    _add_requirement_line(
                        evidence.declared_dependencies,
                        requirement,
                        "setup.py:setup.install_requires",
                        evidence=evidence,
                    )
            elif keyword.arg == "extras_require":
                for group, requirements in _literal_extras_require(keyword.value).items():
                    for requirement in requirements:
                        _add_requirement_line(
                            evidence.declared_dependencies,
                            requirement,
                            f"setup.py:setup.extras_require.{group}",
                            kind="optional_dependency",
                            trust="medium",
                            evidence=evidence,
                        )


# Editable self-install with extras: ``-e .[http2,socks]`` / ``--editable .[...]``.
_EDITABLE_SELF_EXTRAS_RE = re.compile(r"^(?:-e|--editable)\s+\.\s*\[([^\]]*)\]\s*$")
# Include directives: ``-r other.txt`` / ``--requirement other.txt`` (deps) and
# ``-c other.txt`` / ``--constraint other.txt`` (constraints). Optional ``=``.
_INCLUDE_RE = re.compile(r"^(-r|--requirement|-c|--constraint)\s*=?\s*(\S+)")
_MAX_INCLUDE_DEPTH = 5


# Directories pruned from the bounded recursive SOFT-requirements-file walk
# (`_discover_soft_requirements_files`) -- HARD discovery (a direct,
# non-recursive glob, see `_discover_hard_requirements_files`) never walks at
# all, so this set does not apply to it.
# Deliberately NARROWER than `depgraph.scan.SKIP_WALK_DIRS` / its
# `_EXCLUDED_SEGMENTS` (which additionally exclude "docs"/"doc"/"tests"/
# "test"/"scripts"/"tools"/"examples"/"benchmarks"): those are exactly where
# real per-directory requirements files live (root-level `docs/requirements.txt`
# is an existing, tested discovery target -- see
# ``test_nested_docs_requirements_is_dev_group_docs``), so reusing that set
# here would be a REGRESSION, not a fix. This set is limited to vendored/
# build-artifact/vcs/virtualenv noise that can never legitimately hold a
# project's own requirements files.
_REQUIREMENTS_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
        "node_modules", "site-packages",
        ".venv", "venv", "env", ".env",
        "build", "dist", ".eggs",
    }
)

# Sane cap so a pathological repo (or a vendored tree that slips past the
# skip-dir prune) cannot make SOFT discovery unbounded (Finding 5: HARD files
# -- the direct allowlist glob -- are returned uncapped, correctly; this cap
# applies only to the SOFT side). ArchipelagoMW/Archipelago's ~85 real
# per-world requirements files sit nowhere near this.
_MAX_DISCOVERED_REQUIREMENTS_FILES = 500


def _collect_requirements_files(root: Path, evidence: PythonDependencyEvidence) -> None:
    """Split requirements-file DISCOVERY from ROLE/trust -- HARD files first.

    HARD is computed DIRECTLY (:func:`_discover_hard_requirements_files`) by
    reproducing the OLD pre-walk allowlist's own globs, not by reconstructing
    its result set from walk-derived predicates (Finding 1) -- every HARD
    file (see :func:`_is_hard_requirements_file`) is ingested before any SOFT
    candidate is even looked at. Ingesting a HARD file also transitively
    ingests anything it ``-r``/``--requirement`` includes, however deeply
    nested or wherever located on disk (Finding 3, deliberate exception to
    the discovery invariant): an explicit include is the repo itself
    DECLARING a dependency, which is categorically different from the
    recursive walk DISCOVERING a file unprompted, so the hard/soft boundary
    that governs discovery does not apply to it at all -- see
    :func:`_ingest_requirements_file`, which records every resolved path it
    visits (including ``-c`` targets, Finding 2) into ``visited``.

    Only once every HARD file (and everything it includes) has been fully
    ingested are the remaining SOFT candidates
    (:func:`_discover_soft_requirements_files` -- everything the recursive
    walk finds MINUS the HARD set) processed:

    * A SOFT candidate whose resolved path was already visited via some HARD
      file's ``-r``/``-c`` include is dropped, never double-listed -- otherwise
      the renderer would redundantly ``pip install -r`` it a second time,
      best-effort, on top of the pinned closure/constraints it already
      contributed to.
    * A SOFT candidate whose RESOLVED (symlink-followed) location escapes the
      repo root is dropped with a recorded collection error instead of being
      silently stored as an out-of-repo relative path (Finding 4) -- see
      :func:`_resolved_path_escapes_root`.
    """
    visited: set[Path] = set()
    hard_files = _discover_hard_requirements_files(root)
    hard_resolved = frozenset(path.resolve() for path in hard_files)

    # Record the HARD file PATHS (mirroring soft_requirements_files below). Purely
    # additive: the ingestion loop right below is unchanged, so this does not alter
    # which requirements land in declared_dependencies or their kind.
    evidence.hard_requirements_files = sorted(
        {_relative_posix_path(root, path) for path in hard_files}
    )

    for path in hard_files:
        _ingest_requirements_file(root, path, evidence, visited, depth=0)

    soft_candidates, truncated = _discover_soft_requirements_files(root, hard_resolved, evidence)
    if truncated:
        evidence.collection_errors.append(
            "_discover_soft_requirements_files: capped at "
            f"{_MAX_DISCOVERED_REQUIREMENTS_FILES} SOFT requirements files; "
            "some nested requirements files were not read (HARD files are "
            "never capped)"
        )

    for path in soft_candidates:
        if path.resolve() in visited:
            # Finding 3: already ingested HARD via an explicit -r/-c include
            # from some other hard file -- do not also list it as soft.
            continue
        if _resolved_path_escapes_root(root, path):
            # leaf symlink resolves outside the repo -- fail loud instead of
            # silently storing an out-of-repo relative path the renderer
            # would later `pip install -r`.
            evidence.collection_errors.append(
                "_collect_requirements_files: soft requirements file "
                f"{_relative_posix_path(root, path)!r} resolves outside the "
                "repo root (symlink?); skipped"
            )
            continue
        # SOFT: discovered by the recursive walk but outside the pre-walk
        # allowlist -- recorded for a later best-effort install, never
        # ingested into declared_dependencies (see
        # _is_hard_requirements_file's invariant docstring).
        evidence.soft_requirements_files.append(_relative_posix_path(root, path))
    evidence.soft_requirements_files.sort()


def _resolved_path_escapes_root(root: Path, path: Path) -> bool:
    """True if ``path``'s RESOLVED (symlinks followed) location is not under
    ``root``'s RESOLVED location (Finding 4).

    A soft requirements file is retained only when this returns False. A leaf
    symlink pointing outside the repo (e.g. ``worlds/x/requirements.txt`` ->
    ``/tmp/other.txt``) is classified soft LEXICALLY by
    :func:`_is_hard_requirements_file` -- but must not be stored as if it
    were a normal repo-relative path once its resolved target turns out to
    live outside the repo; the renderer would otherwise happily
    ``pip install -r ../../tmp/other.txt``, producing a wrong environment
    instead of a loud failure. Any resolution failure (e.g. a broken
    symlink) is treated as escaping too -- fail closed, not open.
    """
    try:
        return not path.resolve().is_relative_to(root.resolve())
    except OSError:
        return True


def _is_requirements_txt_filename(name: str) -> bool:
    """True for any ``*.txt`` file whose name contains "requirement".

    Substring (not prefix) match on purpose: covers both the
    ``requirements-dev.txt`` PREFIX convention and the
    ``dev-requirements.txt`` / ``ci-requirements.txt`` SUFFIX convention real
    repos use (ArchipelagoMW/Archipelago's ``ci-requirements.txt`` is gold-
    installed) — the same rule the nested-dir allowlist already applied, now
    applied consistently everywhere (FIX 1 / A2).
    """
    lower = name.lower()
    return lower.endswith(".txt") and "requirement" in lower


def _is_under_requirements_dir(root: Path, path: Path) -> bool:
    """True when ``path`` lives anywhere under a directory literally named
    ``requirements`` (any depth) — preserves the pre-existing allowance for
    e.g. ``requirements/base.txt``, whose OWN filename has no "requirement"
    substring, now generalized to any depth under the walk.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return any(segment.lower() == "requirements" for segment in relative.parts[:-1])


def _path_depth(root: Path, path: Path) -> int:
    """Number of path components of ``path`` relative to ``root``."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return len(relative.parts)


# Top-level directories the OLD pre-walk allowlist read from (see the diff
# this module carries: ``for sub in ("requirements", "tests", "test",
# "docs"):``). Kept private to `_discover_hard_requirements_files` -- this is
# the no-regression invariant boundary, not a general "requirements-ish dir"
# rule. EXACT, case-sensitive strings -- the old allowlist opened the literal
# paths ``root/"requirements"`` etc.; see `_discover_hard_requirements_files`
# for why the comparison must not lower-case either side.
_HARD_TOP_LEVEL_DIRS: frozenset[str] = frozenset({"requirements", "tests", "test", "docs"})


def _discover_hard_requirements_files(root: Path) -> list[Path]:
    """Reproduce the OLD pre-walk allowlist DIRECTLY (Finding 1 fix).

    HARD used to be *reconstructed* from the recursive walk's output via a
    path-shape predicate (`len(parts) == 1 or (len(parts) == 2 and
    parts[0] in _HARD_TOP_LEVEL_DIRS)`). That reconstruction quietly assumed
    the walk could see everything the old allowlist could see -- false for a
    symlinked top-level directory (``repo/tests -> /external/dir``):
    ``os.walk`` without ``followlinks=True`` never descends into it, so a
    requirements file living only through that symlink vanished with no
    dependency, no soft path, and no error.

    The fix is structural, not another predicate patch: HARD is computed the
    same way the OLD allowlist computed it -- a non-recursive glob of the
    repo root, plus a non-recursive glob of each of
    ``root/"requirements"``, ``root/"tests"``, ``root/"test"``,
    ``root/"docs"``. ``Path.glob`` follows a symlinked *directory* itself
    transparently (it just lists the target's contents), exactly like the
    old allowlist's own ``(root / "tests").glob("*.txt")`` did -- so a
    symlinked allowlist directory works again, without ever adding
    ``followlinks=True`` to the recursive SOFT-discovery walk (that would
    invite symlink cycles / walking into huge external trees, which the walk
    must not do).

    The top-level directory name match is EXACT and CASE-SENSITIVE -- and
    checked against each of ``root``'s own directory ENTRY NAMES
    (``root.iterdir()``), never by directly opening a constructed
    ``root / "tests"`` path. The latter would be wrong on a case-INSENSITIVE,
    case-PRESERVING filesystem (macOS's default APFS mode): opening
    ``root / "tests"`` there transparently resolves to an on-disk ``Tests/``
    too, silently WIDENING the allowlist match beyond what the old allowlist
    (and production, on a case-sensitive Linux/Docker filesystem) would ever
    have matched. Iterating ``root``'s entries and comparing each entry's own
    ``.name`` string is exact and case-sensitive on every filesystem: a
    directory named e.g. ``Tests/`` is a DIFFERENT string from ``tests`` and
    is correctly left out of ``_HARD_TOP_LEVEL_DIRS`` membership everywhere.
    Contrast :func:`_is_requirements_txt_filename`, whose lower-cased
    substring match is a DELIBERATE, separate widening (it makes e.g.
    root-level ``ci-requirements.txt`` discoverable) — only the TOP-LEVEL
    DIRECTORY comparison is case-sensitive.

    Unlike SOFT discovery (:func:`_discover_soft_requirements_files`), this is
    never capped (Finding 5: the OLD allowlist was equally uncapped, so this
    is not a regression). It cannot explode the way an unbounded recursive
    walk can: it is a bounded, non-recursive set -- the repo root plus four
    top-level directories, never recursed into further. Capping it anyway
    could silently drop a real hard root -- e.g. 501 files directly under
    ``requirements/`` would leave the 501st file neither in
    ``declared_dependencies`` nor in ``soft_requirements_files``, present
    nowhere, breaking the hard/soft invariant.

    The invariant this establishes BY CONSTRUCTION: the recursive walk can
    never change which files are hard roots, because it is never consulted
    for that decision at all -- see :func:`_is_hard_requirements_file`.
    """
    hard = _qualifying_txt_files_in_dir(root, root)
    try:
        entries = list(root.iterdir())
    except OSError:
        entries = []
    matched_dirs = sorted(entry for entry in entries if entry.name in _HARD_TOP_LEVEL_DIRS)
    for directory in matched_dirs:
        hard.extend(_qualifying_txt_files_in_dir(root, directory))
    return sorted(hard)


def _qualifying_txt_files_in_dir(root: Path, directory: Path) -> list[Path]:
    """Non-recursive glob of ``directory`` for qualifying ``*.txt`` files.

    "Qualifying" is the same rule used everywhere else in this module
    (:func:`_is_requirements_txt_filename` OR :func:`_is_under_requirements_dir`)
    -- unchanged by Finding 1; only WHERE the candidates come from (a direct
    glob instead of a walk-derived predicate) changed.
    """
    try:
        candidates = sorted(directory.glob("*.txt"))
    except OSError:
        return []
    return [
        path
        for path in candidates
        if path.is_file()
        and (_is_requirements_txt_filename(path.name) or _is_under_requirements_dir(root, path))
    ]


def _is_hard_requirements_file(hard_resolved: frozenset[Path], path: Path) -> bool:
    """Membership test against the pre-computed HARD set (Finding 1 fix).

    No longer a path-shape predicate (a candidate's own depth/parent-dir name
    is never inspected here) -- HARD is now a fact computed once, directly,
    by :func:`_discover_hard_requirements_files`; this function only asks "is
    ``path`` in that set", by RESOLVED path so a symlink cannot produce a
    duplicate (the same real file reached two different lexical ways -- once
    via the HARD glob, once via the SOFT walk -- must count as the one HARD
    file it is, not also as a distinct SOFT one).
    """
    try:
        return path.resolve() in hard_resolved
    except OSError:
        return False


def _discover_soft_requirements_files(
    root: Path,
    hard_resolved: frozenset[Path],
    evidence: PythonDependencyEvidence,
) -> tuple[list[Path], bool]:
    """Bounded recursive discovery of SOFT requirements files.

    SOFT = everything the recursive walk finds, MINUS the HARD set (Finding
    1): with HARD now computed independently
    (:func:`_discover_hard_requirements_files`), this walk's only job is
    discovering candidates outside that set -- real repos scatter
    requirements files under arbitrary directory names (ArchipelagoMW/
    Archipelago: ``worlds/*/requirements.txt``, ``WebHostLib/requirements.txt``)
    that no fixed allowlist can enumerate (FIX 2 / B4).

    A ``.txt`` file qualifies when its own name contains "requirement"
    (:func:`_is_requirements_txt_filename`) OR it sits under a
    ``requirements/`` directory (:func:`_is_under_requirements_dir`). The walk
    is pruned by :data:`_REQUIREMENTS_SKIP_DIRS` (vendored/build/vcs/venv
    noise only — see that constant's docstring for why it is narrower than
    ``scan.SKIP_WALK_DIRS``); it deliberately does NOT pass
    ``followlinks=True`` (a symlinked allowlist directory is handled by the
    HARD glob instead, see :func:`_discover_hard_requirements_files` -- adding
    it here would invite symlink cycles / walking into huge external trees).

    Finding 4: a candidate that fails ``is_file()`` (a broken/dangling
    symlink) is recorded as a collection error instead of being silently
    dropped -- the fail-closed ``OSError`` branch in
    :func:`_resolved_path_escapes_root` would otherwise be unreachable, since
    nothing ever reached it for a leaf that cannot even be stat'd.

    SOFT discovery (and ONLY soft discovery -- see
    :func:`_discover_hard_requirements_files` for why HARD is never capped)
    is capped at :data:`_MAX_DISCOVERED_REQUIREMENTS_FILES` so a pathological
    repo cannot make it unbounded. The walk always runs to completion (cheap
    — it is a directory walk with no file reads) so the cap decision is
    ORDER-INDEPENDENT: truncating mid-walk let a lexically-early vendored
    subtree (``os.walk`` is top-down with sorted dirnames) exhaust the whole
    budget before the walk ever reached a legitimate, shallower directory
    sorting later (e.g. ``worlds/``). Once every candidate is known, an
    over-the-cap result is trimmed by ``(depth, path)`` ascending and only the
    shallowest N survive — a project's own requirements files are
    overwhelmingly more likely to sit near the root than deep inside a
    vendored tree.

    Returns ``(files, truncated)`` — ``truncated`` is True iff the cap was
    hit (the caller records this in ``evidence.collection_errors`` rather
    than silently dropping the overflow).
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d.lower() not in _REQUIREMENTS_SKIP_DIRS
        )
        for fname in sorted(filenames):
            if not fname.lower().endswith(".txt"):
                continue
            candidate = Path(dirpath) / fname
            if not (
                _is_requirements_txt_filename(fname)
                or _is_under_requirements_dir(root, candidate)
            ):
                continue
            if not candidate.is_file():
                evidence.collection_errors.append(
                    "_discover_soft_requirements_files: candidate "
                    f"{_relative_posix_path(root, candidate)!r} is not a "
                    "regular file (broken symlink?); skipped"
                )
                continue
            if _is_hard_requirements_file(hard_resolved, candidate):
                continue  # already ingested HARD via the direct allowlist glob
            found.append(candidate)

    if len(found) <= _MAX_DISCOVERED_REQUIREMENTS_FILES:
        return sorted(found), False

    shallowest = sorted(found, key=lambda p: (_path_depth(root, p), p))
    kept = shallowest[:_MAX_DISCOVERED_REQUIREMENTS_FILES]
    return sorted(kept), True


def _requirements_role(root: Path, path: Path) -> str:
    """Role for a requirements file from its dir/basename tokens.

    Returns one of ``"docs"``, ``"test"``, ``"dev"``, ``"runtime"`` (checked in
    that precedence). Token/segment matching (not raw substring) keeps false
    positives low.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    dir_segments = {segment.lower() for segment in relative.parts[:-1]}
    stem_tokens = {tok for tok in re.split(r"[-_.]", path.stem.lower()) if tok}
    docs_markers = {"docs", "doc", "documentation"}
    test_markers = {"test", "tests", "testing"}
    if stem_tokens & docs_markers or dir_segments & docs_markers:
        return "docs"
    if stem_tokens & test_markers or dir_segments & test_markers:
        return "test"
    if "dev" in stem_tokens or "dev" in dir_segments:
        return "dev"
    return "runtime"


def _role_kind_source(role: str, root: Path, path: Path) -> tuple[str, str]:
    """Map a requirements-file role to ``(kind, source)`` for its rows."""
    if role == "runtime":
        return "dependency", _relative_source(root, path)
    return "dev_group", f"requirements-file.{role}"


def _ingest_requirements_file(
    root: Path,
    path: Path,
    evidence: PythonDependencyEvidence,
    visited: set[Path],
    depth: int,
) -> None:
    resolved = path.resolve()
    if depth > _MAX_INCLUDE_DEPTH:
        # Finding 3: the depth guard is a cycle/blowup defence, not a place to
        # silently drop declared dependencies. Recording a collection error
        # BEFORE returning (instead of returning bare) means a six-level
        # ``-r`` chain now leaves a trace -- neither hard, soft, nor silent.
        evidence.collection_errors.append(
            "_ingest_requirements_file: include depth exceeded "
            f"{_MAX_INCLUDE_DEPTH} at {_relative_source(root, resolved)!r} "
            f"(depth={depth}); file not read, dependencies it declares are "
            "MISSING"
        )
        return
    if resolved in visited or not resolved.is_file():
        return
    visited.add(resolved)
    role = _requirements_role(root, resolved)
    kind, source = _role_kind_source(role, root, resolved)
    for raw_line in _iter_raw_requirement_lines(resolved):
        line = _strip_inline_comment(raw_line).strip()
        if not line:
            continue
        editable = _EDITABLE_SELF_EXTRAS_RE.match(line)
        if editable:
            for extra in editable.group(1).split(","):
                stripped = extra.strip()
                if stripped:
                    # PEP 685: normalize separators (-/_/.) the same way as
                    # distribution names so `-e .[socks-extra]` matches an
                    # optional-dependencies group declared `socks_extra`.
                    evidence.used_extras.add(normalize_package_name(stripped))
            continue
        include = _INCLUDE_RE.match(line)
        if include:
            target = (resolved.parent / include.group(2)).resolve()
            if include.group(1) in ("-c", "--constraint"):
                # Finding 2: a -c/--constraint target must be visited too, not
                # just read -- otherwise it survives as a SOFT candidate and
                # the renderer later `pip install -r`s it on top of the
                # closure, silently turning a constraint file into an install
                # list.
                visited.add(target)
                for constraint_line in _read_requirement_lines(target) if target.is_file() else ():
                    _add_requirement_line(
                        evidence.constraint_dependencies,
                        constraint_line,
                        _relative_source(root, target),
                        kind="constraint",
                        evidence=evidence,
                    )
            else:
                _ingest_requirements_file(root, target, evidence, visited, depth + 1)
            continue
        if line.startswith("-"):
            # any other option / editable form (``-i``, ``--hash``, bare ``-e .``,
            # ``-e <url>``) — ignored, matching prior behavior.
            continue
        _add_requirement_line(
            evidence.declared_dependencies, line, source, kind=kind, evidence=evidence
        )


def _iter_raw_requirement_lines(path: Path) -> Iterable[str]:
    """Yield every non-empty line (INCLUDING ``-``-prefixed directives)."""
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if stripped:
            yield raw_line


def _collect_constraints_files(root: Path, evidence: PythonDependencyEvidence) -> None:
    for path in _glob_metadata_files(root, "constraints*.txt"):
        for line in _read_requirement_lines(path):
            _add_requirement_line(
                evidence.constraint_dependencies,
                line,
                _relative_source(root, path),
                kind="constraint",
                evidence=evidence,
            )


def _build_import_mappings(evidence: PythonDependencyEvidence) -> list[ImportPackageMapping]:
    declared_package_names = {
        requirement.name for requirement in evidence.declared_dependencies if requirement.name
    }
    mappings: list[ImportPackageMapping] = []
    for finding in evidence.imports:
        if finding.classification != "external":
            continue
        mapping = map_import_to_package(finding.import_name, declared_package_names)
        if is_unresolved(mapping):
            continue
        mappings.append(
            ImportPackageMapping(
                import_name=mapping.import_name,
                package_name=mapping.package_name,
                source=mapping.source,
                trust=mapping.trust,
            )
        )
    return sorted(mappings, key=lambda item: item.import_name)


def _glob_metadata_files(root: Path, pattern: str) -> list[Path]:
    matches = [
        Path(path)
        for path in glob.glob(str(root / pattern))
        if Path(path).is_file()
    ]
    return sorted(matches)


def _read_requirement_lines(path: Path) -> Iterable[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")
    for raw_line in content.splitlines():
        line = _strip_inline_comment(raw_line).strip()
        if not line or line.startswith(("-", "--")):
            continue
        yield line


def _add_requirement_line(
    target: list[PythonRequirement],
    line: object,
    source: str,
    *,
    kind: str = "dependency",
    trust: str = "high",
    evidence: "PythonDependencyEvidence | None" = None,
) -> None:
    """Parse ``line`` and append the resulting :class:`PythonRequirement` to
    ``target`` (``evidence.declared_dependencies`` or ``.constraint_dependencies``
    in every real caller).

    Fix 1 (docs/superpowers/plans/2026-07-14-post-measurement-fixes.md): when
    ``line`` is a PEP 508 direct reference (``name @ <url>``), the parsed
    requirement still lands in ``target`` with its TRUE ``kind`` (never
    dropped, never retagged -- ``select_roots`` applies the same scope rules
    to it as to any other declared dependency, exactly the precedent
    `[tool.uv.sources]` deps already set). The URL is additionally recorded
    onto ``evidence.direct_reference_sources`` (when an ``evidence`` object is
    given -- optional so this stays a pure parse-and-append helper for
    existing/test callers that pass a bare list), keyed by canonical name, so
    build.py's root-exclusion / MISSING-node / repair-ladder guards can treat
    it as a non-PyPI source exactly like a `[tool.uv.sources]` entry.
    """
    if not isinstance(line, str):
        return
    parsed = _parse_requirement_line(line)
    if not parsed:
        return
    name, specifier, marker, extras, url = parsed
    target.append(
        PythonRequirement(
            name=name,
            specifier=specifier,
            marker=marker,
            extras=extras,
            source=source,
            kind=kind,
            trust=trust,
        )
    )
    if url and evidence is not None:
        evidence.direct_reference_sources[normalize_package_name(name)] = url


def _parse_requirement_line(
    line: str,
) -> tuple[str, str, str, tuple[str, ...], str] | None:
    """Parse one requirement-string line to ``(name, specifier, marker, extras, url)``.

    ``url`` is the raw PEP 508 direct-reference URL (``git+``/``http(s)://``/
    ``file:``/etc.) when ``line`` is of the form ``name @ <url>``, else ``""``.

    ``packaging.requirements.Requirement`` is tried FIRST now (Fix 1): before,
    this function special-cased ANY line containing "://" as a bare,
    nameless direct URL (the legacy ``git+https://host/x.git#egg=name`` pip
    syntax, which is NOT valid PEP 508 -- ``Requirement`` raises
    ``InvalidRequirement`` for it, which is exactly why the old code had to
    detect it via a heuristic instead of trying ``Requirement`` at all) --
    which meant a NAMED direct reference like
    ``kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d`` (which
    ``Requirement`` parses perfectly fine, exposing ``.url``) got caught by
    that same "://" heuristic FIRST, found no ``#egg=`` fragment, and
    silently vanished -- never a root, never a MISSING node, no evidence
    trail at all. Trying ``Requirement`` first fixes both without disturbing
    the legacy fallback: an ordinary requirement or a real direct reference
    parses immediately; a legacy bare-URL line still raises
    ``InvalidRequirement`` and falls through to the unchanged ``#egg=``
    heuristic below.
    """
    cleaned = _strip_inline_comment(line).strip()
    if not cleaned or cleaned.startswith(("-", "--")):
        return None
    try:
        requirement = Requirement(cleaned)
    except InvalidRequirement:
        pass
    else:
        marker = str(requirement.marker) if requirement.marker is not None else ""
        extras = tuple(sorted(requirement.extras))
        if requirement.url:
            # PEP 508 direct reference: no version specifier is possible
            # alongside a URL (mutually exclusive in the grammar) -- carry
            # the URL in the 5th slot instead of discarding it.
            return requirement.name, "", marker, extras, requirement.url
        return requirement.name, str(requirement.specifier), marker, extras, ""
    # Legacy pip direct-URL syntax with no leading "name @" (e.g.
    # `git+https://host/x.git#egg=name`) -- unchanged fallback.
    if "://" in cleaned or cleaned.startswith(("git+", "hg+", "svn+")):
        egg_match = re.search(r"[#&]egg=([A-Za-z0-9_.-]+)", cleaned)
        if egg_match:
            return egg_match.group(1), cleaned, "", (), ""
        return None
    return None


def _strip_inline_comment(line: str) -> str:
    if " #" not in line:
        return line
    return line.split(" #", 1)[0]


def _split_multiline_value(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _literal_string(node: ast.AST) -> str | None:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _literal_string_list(node: ast.AST) -> list[str]:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def _literal_extras_require(node: ast.AST) -> dict[str, list[str]]:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for group, requirements in value.items():
        if not isinstance(group, str):
            continue
        if isinstance(requirements, str):
            result[group] = [requirements]
        elif isinstance(requirements, (list, tuple)):
            result[group] = [item for item in requirements if isinstance(item, str)]
    return result


def _relative_source(root: Path, path: Path) -> str:
    """Repo-relative provenance path, resolved on BOTH sides.

    ``path`` is frequently pre-resolved by the caller (e.g.
    ``_ingest_requirements_file`` resolves for cycle detection) while ``root``
    is left as given. On a repo root that resolves through a symlink (macOS
    ``/var`` -> ``/private/var`` is the common case), comparing a resolved
    ``path`` against an unresolved ``root`` makes ``os.path.relpath`` walk up
    through the divergent prefix and emit garbage like
    ``../../../../private/var/.../requirements.txt`` instead of the intended
    ``requirements.txt``. Resolving both sides here keeps this function
    correct regardless of what the caller already resolved.
    """
    return os.path.relpath(Path(path).resolve(), Path(root).resolve())


def _relative_posix_path(root: Path, path: Path) -> str:
    """Repo-relative path using ``/`` separators, regardless of platform.

    Used for ``evidence.soft_requirements_files``, which a later stage
    renders verbatim into shell command lines. Deliberately LEXICAL (Finding
    4) -- unlike :func:`_relative_source`, this does NOT resolve ``path``'s
    leaf: a soft requirements file that is itself a symlink pointing outside
    the repo must not have its symlink TARGET reported as if it were the
    repo-relative path (e.g. ``../../tmp/other.txt``, which the renderer
    would then happily ``pip install -r``). Escape detection -- comparing the
    RESOLVED path against the RESOLVED root -- is a separate, prior check the
    caller already performed (see :func:`_resolved_path_escapes_root`); by
    the time this function runs, the caller has established the file's
    resolved location is safely under root, so the lexical (as-discovered)
    relative path is exactly what a later ``pip install -r <this path>``
    command should use.

    ``path`` is always constructed directly from the given ``root`` by
    ``os.walk`` in :func:`_discover_soft_requirements_files`, so a plain
    (non-resolving) ``relative_to`` is exact here -- no divergent-prefix
    correction is needed the way :func:`_relative_source` needs one for
    callers that may have already resolved one side.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return relative.as_posix()
