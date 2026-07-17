"""Deterministic test-invocation resolver for the collection-scope graph.

A pure function — no execution, no network, no LLM — mapping a repo's declaration
surface to a :class:`TestEnvPlan`: the interpreter to run, the project dir(s) to
editable-install, the ordered (wheels-first) install plan, and the pytest
path/config half (rootdir / pythonpath / import-mode / layout) the repo already
declares.

Step 1 of the collection-graph simplification design
(``docs/superpowers/specs/2026-07-16-collection-graph-simplification-design.md``,
"The deterministic resolver"). ADDITIVE: reuses the existing declaration parsing
(:func:`python_deps.evidence.collect_python_dependency_evidence` reads
``requires-python``, declared deps, PEP 735 ``[dependency-groups]``, extras, and
requirements files incl. nested ones) and the root-scope policy constants from
:mod:`python_deps.depgraph.roots`. Three build priorities, in order: (a)
interpreter — prefer ``requires-python`` and the CI-matrix default/most-common
version, never the max; (b) install-target discovery — root, else a subdir
(feast ``sdk/python``), else a monorepo (vizro), with the full declared closure;
(c) wheels-first install. The residual (an interpreter no declaration names —
PerfKit 3.12; monorepo ``.pth`` reconciliation) is Class B, owned by the
execution/repair plane.
"""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from python_deps.depgraph.config_scan import (
    authoritative_ambiguous_vars,
    scan_authoritative_config,
)
from python_deps.depgraph.roots import (
    _DEV_GROUP_DENYLIST,
    _TEST_SCOPE_EXTRA_ALLOWLIST,
)
from python_deps.evidence import collect_python_dependency_evidence
from python_deps.import_mapping import normalize_package_name
from python_deps.models import PythonDependencyEvidence

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 — fall back to the tomli backport.
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - neither parser available
        tomllib = None  # type: ignore[assignment]


# Ascending — interpreter policy searches within this supported minor set.
SUPPORTED_MINORS: tuple[str, ...] = ("3.9", "3.10", "3.11", "3.12", "3.13")
DEFAULT_MINOR = "3.11"

# Dirs never descended for install-target manifests and never counted as a repo's
# own top-level packages (vendored/build/vcs/venv/harness noise). Includes
# ``tests``/``docs`` — setuptools flat-layout auto-discovery excludes those too,
# so they never disambiguate a multi-package flat layout. The vendored-tree names
# (``vendor``/``third_party``/``deps``/...) keep a manifest-less repo that bundles
# third-party packages from being mis-read as a monorepo and editable-installing
# code it does not own.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".tox", "node_modules", "site-packages", ".venv", "venv",
        "env", ".env", "build", "dist", ".eggs", ".github", "tests", "test",
        "docs", "doc", "examples", "example", "samples", "sample", "scripts",
        "script", "migrations",
        "vendor", "vendored", "third_party", "third-party", "thirdparty",
        "deps", "contrib", "external",
    }
)

# Build-backends that do their OWN package discovery (never setuptools' fragile
# flat-layout auto-discovery), so >=2 top-level packages under them is not the
# setuptools hazard. A ``setuptools.build_meta`` backend, or none declared,
# still uses the discovery that can raise — so it stays checked.
_NON_SETUPTOOLS_BACKEND_PREFIX = "setuptools"

_MAX_PROJECT_DEPTH = 3  # feast's sdk/python is depth 2
_GROUP_FROM_SOURCE = re.compile(
    r"(?:optional-dependencies|extras_require|dependency-groups|requirements-file)\.(.+)$"
)
_IMPORT_MODE_RE = re.compile(r"--import-mode[=\s]+([A-Za-z]+)")
_VERSION_TOKEN_RE = re.compile(r"\b(\d+\.\d+)\b")
_TOX_ENV_RE = re.compile(r"py(\d)\.?(\d{1,2})")


@dataclass(frozen=True)
class TestEnvPlan:
    """The deterministic plan for reaching clean ``pytest --collect-only``.

    Every field is a pure function of the declaration surface; nothing here is
    discovered by executing the environment.
    """

    # Not a dataclass field (no annotation): keeps pytest from collecting this
    # ``Test*``-named class as a test case when a test module imports it.
    __test__ = False

    interpreter: str
    interpreter_confidence: str  # declared | ci_default | default | undiscoverable
    project_dirs: tuple[str, ...]
    install_plan: tuple[str, ...]
    rootdir: str
    pythonpath: tuple[str, ...]
    import_mode: str
    layout: str  # src | flat | flat_ambiguous | monorepo | none
    flags: tuple[str, ...] = field(default_factory=tuple)
    # The working dir the canonical collect invocation runs from (defaults to the
    # discovered rootdir) and the unambiguous authoritative env-vars it must carry
    # (sorted ``(var, value)`` pairs; vars the authoritative sources disagree on
    # are dropped, never guessed). Defaulted -> additive: existing callers that
    # don't pass them are unaffected.
    cwd: str = "."
    env: tuple[tuple[str, str], ...] = ()


def resolve(repo_path: str | Path) -> TestEnvPlan:
    """Resolve ``repo_path``'s declaration surface into a :class:`TestEnvPlan`."""
    root = Path(repo_path)
    evidence = collect_python_dependency_evidence(str(root))

    project_dirs, is_monorepo = _find_project_dirs(root)
    config = _discover_pytest_config(root, project_dirs)
    # One canonical reader drives both halves: env-vars and PYTHONPATH are sourced
    # from the SAME dir the path-config was found in (the rootdir), never the repo
    # root, so a feast-style nested ``sdk/python/tox.ini setenv`` is not missed.
    config_dir = _project_path(root, config["rootdir"])
    pythonpath = _merge_pythonpath(config["pythonpath"], config_dir, root, project_dirs)
    env = _authoritative_env(config_dir)

    ambiguous = (
        (not is_monorepo)
        and bool(project_dirs)
        and not _is_src_layout(root, project_dirs[0], pythonpath)
        and _flat_layout_ambiguous(root, project_dirs[0])
    )
    interpreter, confidence = _resolve_interpreter(evidence, root)

    flags: list[str] = []
    if ambiguous:
        flags.append("flat_layout_ambiguous")
    if confidence == "undiscoverable":
        flags.append("interpreter_undiscoverable")

    rootdir = config["rootdir"]
    # cwd defaults to the discovered rootdir (absolute materialization is the
    # cure-runner's job in Stage B; here it mirrors rootdir).
    cwd = rootdir

    return TestEnvPlan(
        interpreter=interpreter,
        interpreter_confidence=confidence,
        project_dirs=project_dirs,
        install_plan=_build_install_plan(root, evidence, project_dirs),
        rootdir=rootdir,
        pythonpath=pythonpath,
        import_mode=config["import_mode"],
        layout=_classify_layout(root, project_dirs, is_monorepo, pythonpath, ambiguous),
        flags=tuple(flags),
        cwd=cwd,
        env=env,
    )


# --- (a) interpreter policy ------------------------------------------------- #


def _resolve_interpreter(
    evidence: PythonDependencyEvidence, root: Path
) -> tuple[str, str]:
    """``(interpreter, confidence)`` — prefer requires-python and the CI default,
    cross-checked; never the max version.

    Any resolved minor is clamped to :data:`SUPPORTED_MINORS`; a declared
    ``requires-python`` no supported minor satisfies (">=3.99" / a contradictory
    intersection) OR a CI matrix that names only out-of-range minors (3.14, or
    ``py38`` below the floor) is honest ``undiscoverable`` — a hand-off to a
    try-newer-Python repair, never a raw out-of-range value passed through (that
    is the 3.14 trap this whole design exists to prevent).
    """
    spec = _combined_python_spec(evidence)
    ci_ranked = _ci_ranked_versions(root)
    ci_supported = [v for v in ci_ranked if v in SUPPORTED_MINORS]

    if spec is not None:
        satisfying = [
            m
            for m in SUPPORTED_MINORS
            if spec.contains(Version(f"{m}.0"), prereleases=False)
        ]
        if not satisfying:
            return DEFAULT_MINOR, "undiscoverable"
        for version in ci_supported:  # prefer the CI default inside the constraint
            if version in satisfying:
                return version, "declared"
        if DEFAULT_MINOR in satisfying:
            return DEFAULT_MINOR, "declared"
        return satisfying[0], "declared"  # floor of the supported range, never max

    if ci_ranked:
        if ci_supported:
            return ci_supported[0], "ci_default"
        # CI declared versions, but every one is out of the supported range:
        # don't silently pass a raw 3.14 / 3.8 through — hand off.
        return DEFAULT_MINOR, "undiscoverable"
    return DEFAULT_MINOR, "default"


def _combined_python_spec(evidence: PythonDependencyEvidence) -> SpecifierSet | None:
    """Intersect every declared ``requires-python`` into one SpecifierSet, or
    ``None`` when nothing parseable is declared."""
    specs: list[SpecifierSet] = []
    for requirement in evidence.python_requires:
        raw = (requirement.specifier or "").strip()
        if not raw:
            continue
        try:
            specs.append(SpecifierSet(_normalize_python_constraint(raw)))
        except Exception:
            continue
    if not specs:
        return None
    combined = specs[0]
    for extra in specs[1:]:
        combined = combined & extra
    return combined


def _normalize_python_constraint(raw: str) -> str:
    """Poetry ``^``/``~`` shorthand -> PEP 440 (``^3.10`` -> ``>=3.10,<4.0``;
    ``~3.10`` -> ``>=3.10,<3.11``); an already-PEP-440 specifier is unchanged."""
    raw = raw.strip()
    if raw.startswith("^"):
        ver = raw[1:].strip()
        return f">={ver},<{int(ver.split('.')[0]) + 1}.0"
    if raw.startswith("~"):
        ver = raw[1:].strip()
        parts = ver.split(".")
        if len(parts) >= 2:
            return f">={ver},<{parts[0]}.{int(parts[1]) + 1}"
        return f">={ver},<{int(parts[0]) + 1}"
    return raw


def _ci_ranked_versions(root: Path) -> list[str]:
    """CI-declared python minors ranked by frequency (ties -> lowest version).

    Most-common is the closest deterministic proxy for a matrix's "default" job;
    ties toward the LOWEST guarantee we never pick the max (the POC's 3.14/3.13
    mistake). Sources: GitHub Actions matrices and tox ``envlist``.
    ``.python-version`` is deliberately ignored (a pyenv floor trap, per the
    runtime-base pin policy).
    """
    counts: dict[str, int] = {}
    for version in _github_matrix_versions(root) + _tox_envlist_versions(root):
        counts[version] = counts.get(version, 0) + 1
    return sorted(counts, key=lambda v: (-counts[v], Version(v)))


def _github_matrix_versions(root: Path) -> list[str]:
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return []
    versions: list[str] = []
    for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        versions.extend(_scan_workflow_python_versions(_read_text(path)))
    return versions


def _scan_workflow_python_versions(text: str) -> list[str]:
    """python-version tokens from a workflow's ``python-version`` keys — inline
    list (``["3.10", "3.11"]``) and YAML block-list forms both."""
    versions: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = re.search(r"python[-_]version\s*:\s*(.*)$", line)
        if not match:
            continue
        inline = _VERSION_TOKEN_RE.findall(_strip_yaml_comment(match.group(1)))
        if inline:
            versions.extend(inline)
            continue
        base_indent = len(line) - len(line.lstrip())
        for follow in lines[i + 1 :]:  # block list: more-indented "- <ver>" items
            if not follow.strip():
                continue
            if len(follow) - len(follow.lstrip()) <= base_indent:
                break
            if not follow.lstrip().startswith("-"):
                break
            versions.extend(_VERSION_TOKEN_RE.findall(_strip_yaml_comment(follow)))
    return versions


def _strip_yaml_comment(text: str) -> str:
    """Drop a trailing ``# ...`` YAML comment so a "bump to 3.14 later" note can't
    inject a spurious minor into the version scan."""
    return text.split(" #", 1)[0].strip()


def _tox_envlist_versions(root: Path) -> list[str]:
    path = root / "tox.ini"
    if not path.is_file():
        return []
    match = re.search(r"(?ms)^\s*envlist\s*=(.*?)(?:^\S|\Z)", _read_text(path))
    if not match:
        return []
    return [f"{major}.{minor}" for major, minor in _TOX_ENV_RE.findall(match.group(1))]


# --- (b) install-target discovery + layout ---------------------------------- #


def _find_project_dirs(root: Path) -> tuple[tuple[str, ...], bool]:
    """Editable-install project dir(s): ``(dirs, is_monorepo)``.

    Root first (single-package repo); else a bounded search for subdir manifests
    — exactly one is a subdir project (feast ``sdk/python``), several a monorepo
    (vizro). A found project's own subtree is never descended into.
    """
    if _is_project_dir(root):
        return (".",), False

    found: list[str] = []
    for dirpath, dirnames, _files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else len(rel.split(os.sep))
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        )
        keep: list[str] = []
        for name in dirnames:
            child = Path(dirpath) / name
            if _is_project_dir(child):
                found.append(_relposix(root, child))  # don't descend into it
            elif depth < _MAX_PROJECT_DEPTH:
                keep.append(name)
        dirnames[:] = keep

    unique = tuple(sorted(set(found)))
    if len(unique) == 1:
        return unique, False
    if len(unique) > 1:
        return unique, True
    return (), False


def _is_project_dir(directory: Path) -> bool:
    """True when ``directory`` declares an installable Python distribution."""
    data = _load_pyproject(directory)
    if data is not None:
        tool = data.get("tool", {})
        if (
            "project" in data
            or "build-system" in data
            or (isinstance(tool, dict) and "poetry" in tool)
        ):
            return True
    if (directory / "setup.py").is_file():
        return True
    return _setup_cfg_has_metadata(directory / "setup.cfg")


def _classify_layout(
    root: Path,
    project_dirs: tuple[str, ...],
    is_monorepo: bool,
    pythonpath: tuple[str, ...],
    ambiguous: bool,
) -> str:
    if is_monorepo:
        return "monorepo"
    if not project_dirs:
        return "none"
    if _is_src_layout(root, project_dirs[0], pythonpath):
        return "src"
    return "flat_ambiguous" if ambiguous else "flat"


def _is_src_layout(root: Path, project_rel: str, pythonpath: tuple[str, ...]) -> bool:
    if "src" in pythonpath:
        return True
    src = _project_path(root, project_rel) / "src"
    return src.is_dir() and _dir_has_python(src, recursive=True)


def _flat_layout_ambiguous(root: Path, project_rel: str) -> bool:
    """Static check for setuptools' flat-layout auto-discovery hazard: fires when
    a flat project would use setuptools' discovery, declares no explicit
    packages/py-modules, yet has >=2 top-level packages — setuptools then errors
    "Multiple top-level packages discovered in a flat-layout" (Spoolman).

    A NON-setuptools build backend (hatchling / flit / pdm / poetry) does its own
    discovery and is not subject to this, so it suppresses the flag; a
    ``setuptools.build_meta`` backend, or no ``[build-system]`` at all, still uses
    the fragile auto-discovery and stays checked (the setuptools case the earlier
    ``build-system``-present shortcut wrongly cleared).
    """
    directory = _project_path(root, project_rel)
    data = _load_pyproject(directory)
    if _uses_non_setuptools_backend(data):
        return False
    if _has_explicit_packages(directory, data):
        return False
    return _count_top_level_packages(directory) >= 2


def _uses_non_setuptools_backend(data: dict | None) -> bool:
    """True when ``[build-system] build-backend`` is a declared non-setuptools
    backend. An absent build-system, or a setuptools backend, is False (kept
    checked); an unrecognized/empty backend is treated as setuptools-like."""
    if data is None:
        return False
    build_system = data.get("build-system")
    if not isinstance(build_system, dict):
        return False
    backend = build_system.get("build-backend")
    if not isinstance(backend, str) or not backend.strip():
        return False
    return not backend.strip().startswith(_NON_SETUPTOOLS_BACKEND_PREFIX)


def _has_explicit_packages(directory: Path, data: dict | None) -> bool:
    if data is not None:
        tool = data.get("tool", {})
        st = tool.get("setuptools", {}) if isinstance(tool, dict) else {}
        if isinstance(st, dict) and (
            st.get("packages") or st.get("py-modules") or st.get("py_modules")
        ):
            return True
        poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
        if isinstance(poetry, dict) and poetry.get("packages"):
            return True
    setup_py = directory / "setup.py"
    if setup_py.is_file() and any(
        tok in _read_text(setup_py)
        for tok in ("packages=", "py_modules=", "find_packages")
    ):
        return True
    cfg = directory / "setup.cfg"
    return cfg.is_file() and bool(
        re.search(r"(?m)^\s*(packages|py_modules)\s*=", _read_text(cfg))
    )


def _count_top_level_packages(directory: Path) -> int:
    """Immediate subdirs that are real (non-namespace) packages — an
    ``__init__.py``, matching default ``find_packages`` — excluding the
    non-package/skip dirs setuptools also ignores. A stray dir with a loose
    ``.py`` but no ``__init__.py`` is NOT a discovered package, so it does not
    manufacture a false hazard."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    return sum(
        1
        for entry in entries
        if entry.is_dir()
        and entry.name not in _SKIP_DIRS
        and not entry.name.startswith(".")
        and entry.name != "src"
        and (entry / "__init__.py").is_file()
    )


# --- (c) wheels-first install plan ------------------------------------------ #


def _build_install_plan(
    root: Path, evidence: PythonDependencyEvidence, project_dirs: tuple[str, ...]
) -> tuple[str, ...]:
    """Ordered actions: lockfile (wheels-first) -> editable project(s) -> extras
    -> PEP 735 groups -> requirements files (incl. nested)."""
    plan: list[str] = []
    lockfile = _find_lockfile(root, project_dirs)
    if lockfile:
        plan.append(f"lockfile:{lockfile}")
    plan.extend(f"editable:{directory}" for directory in project_dirs)
    plan.extend(f"extra:{extra}" for extra in _extras_in_scope(evidence))
    plan.extend(f"group:{group}" for group in _groups_in_scope(evidence))
    plan.extend(f"requirements:{req}" for req in _requirements_files(root, evidence))
    return tuple(plan)


def _find_lockfile(root: Path, project_dirs: tuple[str, ...]) -> str | None:
    """The repo's wheels-first lockfile, if any (uv > poetry > pipenv)."""
    for name in ("uv.lock", "poetry.lock", "Pipfile.lock"):
        for rel in ["."] + [d for d in project_dirs if d != "."]:
            candidate = _project_path(root, rel) / name
            if candidate.is_file():
                return _relposix(root, candidate)
    return None


# Only a source with THIS prefix is a genuine PEP 735 ``[dependency-groups]``
# entry (installable via ``--group <name>``). ``evidence`` also tags a HARD
# dev/test requirements FILE with ``kind="dev_group"`` but ``source =
# "requirements-file.<role>"`` — those are installed via a ``requirements:``
# action, and must NOT be emitted as a phantom ``group:<role>`` (``--group test``
# would error, since no such PEP 735 group exists).
_PEP735_GROUP_SOURCE_PREFIX = "pyproject.toml:dependency-groups."


def _groups_in_scope(evidence: PythonDependencyEvidence) -> list[str]:
    """In-scope GENUINE PEP 735 group names, docs/release excluded (roots'
    policy). Requirements-file-sourced dev groups are deliberately excluded — they
    are installed as files, not as ``--group`` targets."""
    groups: list[str] = []
    seen: set[str] = set()
    for requirement in evidence.declared_dependencies:
        if requirement.kind != "dev_group":
            continue
        if not requirement.source.startswith(_PEP735_GROUP_SOURCE_PREFIX):
            continue
        group = _group_from_source(requirement.source)
        normalized = normalize_package_name(group)
        if not group or normalized in seen or normalized in _DEV_GROUP_DENYLIST:
            continue
        seen.add(normalized)
        groups.append(group)
    return groups


def _extras_in_scope(evidence: PythonDependencyEvidence) -> list[str]:
    """Test-scoped feature extras: the repo's own ``-e .[...]`` signals
    (``used_extras``) or a name on the test-scope allowlist."""
    used = {normalize_package_name(extra) for extra in evidence.used_extras}
    extras: list[str] = []
    seen: set[str] = set()
    for requirement in evidence.declared_dependencies:
        if requirement.kind != "optional_dependency":
            continue
        group = _group_from_source(requirement.source)
        normalized = normalize_package_name(group)
        if not group or normalized in seen:
            continue
        if normalized in used or normalized in _TEST_SCOPE_EXTRA_ALLOWLIST:
            seen.add(normalized)
            extras.append(group)
    return extras


def _requirements_files(root: Path, evidence: PythonDependencyEvidence) -> list[str]:
    """Every requirements file evidence discovered — HARD (root + top-level
    requirements/tests/test/docs, incl. ``tests/requirements.txt``) plus SOFT
    (nested per-subdir, Archipelago ``worlds/*``). Reuses evidence's own
    discovery (:func:`python_deps.evidence._discover_hard_requirements_files`)
    rather than re-globbing, so no HARD file — and thus no declared test dep — is
    ever dropped from the install plan."""
    return sorted(
        set(evidence.hard_requirements_files) | set(evidence.soft_requirements_files)
    )


# --- pytest inifile discovery (rootdir / pythonpath / import-mode) ---------- #


def _discover_pytest_config(root: Path, project_dirs: tuple[str, ...]) -> dict:
    """Replicate pytest's inifile discovery: the first config found across the
    repo root then each project dir, precedence pytest.ini > pyproject
    ``[tool.pytest.ini_options]`` > tox.ini ``[pytest]`` > setup.cfg
    ``[tool:pytest]``. rootdir is that config's dir (may be a subdir, feast
    ``sdk/python``); pythonpath/import-mode are read from it verbatim."""
    seen: set[str] = set()
    for rel in ["."] + [d for d in project_dirs if d != "."]:
        if rel in seen:
            continue
        seen.add(rel)
        config = _pytest_config_in_dir(_project_path(root, rel), rel)
        if config is not None:
            return config
    return {"rootdir": ".", "pythonpath": (), "import_mode": "prepend"}


def _pytest_config_in_dir(directory: Path, rel: str) -> dict | None:
    if (directory / "pytest.ini").is_file():
        return _finalize_ini(rel, _read_ini_section(directory / "pytest.ini", "pytest"))
    data = _load_pyproject(directory)
    if data is not None:
        section = data.get("tool", {}).get("pytest", {}).get("ini_options")
        if isinstance(section, dict):
            return _finalize_toml(rel, section)
    for name, ini_section in (("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        if (directory / name).is_file():
            section = _read_ini_section(directory / name, ini_section)
            if section is not None:
                return _finalize_ini(rel, section)
    return None


def _finalize_ini(rel: str, options: dict[str, str] | None) -> dict:
    options = options or {}
    raw = options.get("pythonpath", "")
    return {
        "rootdir": rel,
        "pythonpath": tuple(raw.split()) if raw else (),
        "import_mode": _import_mode_from(options.get("addopts", ""), options),
    }


def _finalize_toml(rel: str, section: dict) -> dict:
    raw = section.get("pythonpath", [])
    if isinstance(raw, str):
        pythonpath: tuple[str, ...] = (raw,)
    elif isinstance(raw, list):
        pythonpath = tuple(str(item) for item in raw)
    else:
        pythonpath = ()
    addopts = section.get("addopts", "")
    if isinstance(addopts, list):
        addopts = " ".join(str(item) for item in addopts)
    return {
        "rootdir": rel,
        "pythonpath": pythonpath,
        "import_mode": _import_mode_from(str(addopts), section),
    }


def _import_mode_from(addopts: str, section: dict) -> str:
    for key in ("import_mode", "import-mode"):
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    match = _IMPORT_MODE_RE.search(addopts or "")
    return match.group(1) if match else "prepend"


# --- canonical env + PYTHONPATH assembly (scoped to the rootdir) ------------ #


def _authoritative_env(config_dir: Path) -> tuple[tuple[str, str], ...]:
    """Unambiguous authoritative env-vars, read from the SAME dir the pytest
    config was found in (rootdir) -- not the repo root, so a feast-style
    ``sdk/python/tox.ini setenv`` is not missed. Ambiguous vars are dropped."""
    ambiguous = authoritative_ambiguous_vars(str(config_dir))
    return tuple(
        sorted(
            (k, v)
            for k, v in scan_authoritative_config(str(config_dir)).items()
            if k not in ambiguous
        )
    )


def _merge_pythonpath(
    ini_pythonpath: tuple[str, ...],
    config_dir: Path,
    root: Path,
    project_dirs: tuple[str, ...],
) -> tuple[str, ...]:
    """Union of: the pytest ``pythonpath`` ini option; tox ``setenv PYTHONPATH``
    entries; and the src-layout root (``<project>/src``) when present. Order-
    preserving, de-duplicated. Editable-install roots are added by the cure
    runner, not here."""
    out: list[str] = list(ini_pythonpath)
    for var, value in _authoritative_env(config_dir):
        if var == "PYTHONPATH":
            out.extend(p for p in value.split(os.pathsep) if p)
    for rel in ["."] + [d for d in project_dirs if d != "."]:
        src = _project_path(root, rel) / "src"
        if src.is_dir():
            out.append("src" if rel == "." else f"{rel}/src")
    seen: set[str] = set()
    return tuple(p for p in out if not (p in seen or seen.add(p)))


# --- small filesystem/parsing helpers --------------------------------------- #


def _project_path(root: Path, rel: str) -> Path:
    return root if rel == "." else root / rel


def _relposix(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _group_from_source(source: str) -> str:
    match = _GROUP_FROM_SOURCE.search(source or "")
    return match.group(1) if match else ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except OSError:
        return ""


def _dir_has_python(directory: Path, *, recursive: bool) -> bool:
    if (directory / "__init__.py").is_file():
        return True
    try:
        return any(True for _ in directory.glob("**/*.py" if recursive else "*.py"))
    except OSError:
        return False


def _load_pyproject(directory: Path) -> dict | None:
    path = directory / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return None
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except Exception:
        return None


def _read_ini_section(path: Path, section: str) -> dict[str, str] | None:
    """The ``section`` of an ini file as ``{key: value}``, or ``None`` when the
    section is absent (or the file fails to parse — best-effort)."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError):
        return None
    if not parser.has_section(section):
        return None
    return {key: (value or "").strip() for key, value in parser.items(section)}


def _setup_cfg_has_metadata(path: Path) -> bool:
    return path.is_file() and bool(_read_ini_section(path, "metadata"))
