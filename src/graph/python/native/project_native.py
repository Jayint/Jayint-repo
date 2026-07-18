"""Project-native build-obligation construction + its static manifest scanner.

Folds the two project-native leaves (3c-3):
  * ``project_native_scan`` — host-side static scan of the repo's OWN
    ``setup.py`` / ``pyproject.toml`` for declared ``Extension(libraries=[...])``
    link targets (``scan_native_build_surface`` / ``has_native_build_signal``);
  * ``project_native_deps`` — assembles the Project node's build-dep priors from
    four additive sources (``project_native_obligations``).
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from graph.contracts.executor import Executor
from graph.model import apt_build_id
from graph.model import DepGraph, Edge, EdgeType, NodeType
from graph.python.native.apt import ObservedNeed, capability_id
from graph.python.native.build_deps import (
    PACKAGE_TO_BUILD_NEEDS,
    _apt_build_node,
    _apt_installable,
    _build_essential_node,
    _capability_node,
    _BUILD_ESSENTIAL_ID,
    needs_from_pyproject,
)
from graph.python.native.debian_builddeps import debian_build_deps
from graph.python.util.import_mapping import normalize_package_name

logger = logging.getLogger(__name__)


# === project_native_scan: static native-build-surface scanner ===
# setuptools/pybind11/torch/Cython call names whose ``libraries=[...]`` keyword
# (or mere presence, for the coarse signal) marks a native build.
_NATIVE_CALL_NAMES: frozenset[str] = frozenset(
    {"Extension", "Pybind11Extension", "CppExtension", "cythonize"}
)

# Build-backend names that mean "definitely native, no per-library extraction
# attempted in v1" (CMake/Meson-driven; see R1 §2.4 point 2, deferred to a
# follow-up CMakeLists.txt/meson.build scanner).
_NATIVE_BUILD_BACKENDS: tuple[str, ...] = (
    "mesonpy", "meson-python", "scikit_build_core", "scikit-build-core",
)

# Directories never worth descending into for the coarse .pyx/.pxd sweep
# (vendored/third-party trees, VCS metadata, caches) — mirrors config_scan.py's
# exclusion shape.
_EXCLUDED_SEGMENTS: frozenset[str] = frozenset(
    {
        "build", "dist", ".git", ".hg", ".svn", "__pycache__",
        ".venv", "venv", "node_modules", ".tox", ".mypy_cache",
        ".pytest_cache", "site-packages",
    }
)


def _call_name(node: ast.Call) -> str | None:
    """Bare call name: ``Extension(...)`` -> ``"Extension"``; ``m.Pybind11Extension(...)``
    -> ``"Pybind11Extension"``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal_str_list(value: ast.AST) -> list[str]:
    """String-literal elements of a ``[...]``/``(...)`` node; ``[]`` for anything
    else (a call, a name, a comprehension) — a computed ``libraries=`` value is a
    silent miss, never a wrong guess."""
    if not isinstance(value, (ast.List, ast.Tuple)):
        return []
    return [
        elt.value for elt in value.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    ]


def _read_setup_py(repo_path: str) -> ast.Module | None:
    """Parsed ``setup.py`` AST, or ``None`` on any absence/read/syntax failure.
    ``ast.parse`` only — never ``exec``/``import`` (repos are untrusted input)."""
    path = os.path.join(repo_path, "setup.py")
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return None
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def _has_native_call(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call) and _call_name(node) in _NATIVE_CALL_NAMES
        for node in ast.walk(tree)
    )


def _setup_py_library_names(tree: ast.Module) -> list[str]:
    """Order-preserving ``libraries=[...]`` literal names from every
    ``Extension``/``Pybind11Extension``/``CppExtension``/``cythonize`` call."""
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in _NATIVE_CALL_NAMES:
            continue
        for kw in node.keywords:
            if kw.arg == "libraries":
                out.extend(_literal_str_list(kw.value))
    return out


def _load_pyproject(repo_path: str) -> dict:
    """Parsed ``pyproject.toml``, or ``{}`` on any absence/decode failure."""
    path = os.path.join(repo_path, "pyproject.toml")
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _pyproject_library_names(data: dict) -> list[str]:
    """Order-preserving ``libraries`` names from every
    ``[[tool.setuptools.ext-modules]]`` table."""
    tool = data.get("tool")
    setuptools_cfg = tool.get("setuptools") if isinstance(tool, dict) else None
    ext_modules = (
        setuptools_cfg.get("ext-modules") if isinstance(setuptools_cfg, dict) else None
    )
    if not isinstance(ext_modules, list):
        return []
    out: list[str] = []
    for mod in ext_modules:
        if not isinstance(mod, dict):
            continue
        libs = mod.get("libraries")
        if isinstance(libs, list):
            out.extend(lib for lib in libs if isinstance(lib, str))
    return out


def _has_native_build_backend(data: dict) -> bool:
    build_system = data.get("build-system")
    backend = build_system.get("build-backend") if isinstance(build_system, dict) else None
    if not isinstance(backend, str):
        return False
    return any(name in backend for name in _NATIVE_BUILD_BACKENDS)


def _has_pyx_file(repo_path: str) -> bool:
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d.lower() not in _EXCLUDED_SEGMENTS]
        if any(fname.endswith((".pyx", ".pxd")) for fname in filenames):
            return True
    return False


def scan_native_build_surface(repo_path: str) -> list[ObservedNeed]:
    """The repo-under-test's OWN declared linker libraries, as ``ObservedNeed``s.

    Reads ``setup.py`` (``Extension``/``Pybind11Extension``/``CppExtension``/
    ``cythonize`` calls' literal ``libraries=[...]``) and ``pyproject.toml``
    (``[[tool.setuptools.ext-modules]]``'s ``libraries``), de-duplicated by name
    in first-seen order (setup.py before pyproject.toml). ``[]`` when neither
    manifest is present/parseable or no literal ``libraries`` were declared —
    never a wrong guess, only a recall miss (matches the module docstring's
    computed-value contract). Each hit funnels straight through
    ``os_resolver.resolve()`` unchanged (``linker_lib`` is already
    ecosystem-neutral there).
    """
    ordered: list[tuple[str, str]] = []
    tree = _read_setup_py(repo_path)
    if tree is not None:
        ordered.extend(
            (name, "setup.py:Extension.libraries")
            for name in _setup_py_library_names(tree)
        )
    ordered.extend(
        (name, "pyproject.toml:tool.setuptools.ext-modules.libraries")
        for name in _pyproject_library_names(_load_pyproject(repo_path))
    )
    seen: set[str] = set()
    needs: list[ObservedNeed] = []
    for name, evidence in ordered:
        if name in seen:
            continue
        seen.add(name)
        needs.append(
            ObservedNeed(
                "linker_lib", name, context="build", strength="curated",
                evidence=evidence,
            )
        )
    return needs


def has_native_build_signal(repo_path: str) -> bool:
    """Coarse "this repo compiles something" signal — drives the §2.5
    unconditional build-essential floor even when no specific library was
    statically extractable.

    True iff ANY of: an ``Extension(...)``/``cythonize(...)`` (etc.) call in
    ``setup.py`` (regardless of whether its ``libraries=`` was a literal); any
    ``*.pyx``/``*.pxd`` file anywhere in the repo (Cython, even with no external
    lib); or a ``[build-system] build-backend`` naming a Meson/CMake-backed
    backend (``mesonpy``/``meson-python``/``scikit_build_core``/
    ``scikit-build-core``) in ``pyproject.toml``. ``False`` for a pure-Python
    repo, an absent/unparseable manifest, or garbage input — precision is
    irrelevant here (build-essential is always safe to add); only recall
    matters, so this is deliberately broader than ``scan_native_build_surface``.
    """
    tree = _read_setup_py(repo_path)
    if tree is not None and _has_native_call(tree):
        return True
    if _has_pyx_file(repo_path):
        return True
    return _has_native_build_backend(_load_pyproject(repo_path))


# === project_native_deps: Project-node build-obligation assembly ===
def _project_edge(proj_id: str, node_id: str) -> Edge:
    return Edge(src=proj_id, dst=node_id, relation=EdgeType.REQUIRES, origin="resolver")


def project_native_obligations(
    graph: DepGraph,
    repo_path: str,
    host_executor: Executor,
    container_executor: Executor,
) -> DepGraph:
    """Seed the Project node's OWN native build-dep prior, additively.

    ``host_executor`` is accepted for symmetry with the other aux-once
    stages (``wheel_preflight_probe``) and future host-side sources; every
    source wired today (static scan resolution, Debian ``Build-Depends``)
    reads through ``container_executor`` per R1b's spec. A no-op (returns
    ``graph`` unchanged, minus any no-op source contribution) when there is
    no ``NodeType.PROJECT`` node in the graph at all.
    """
    project_node = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
    if project_node is None:
        return graph

    proj_id = project_node.id
    new = graph
    cap_nodes = 0
    aptdep_nodes = 0

    def _seed_capability(need) -> None:
        nonlocal new, cap_nodes
        node_id = capability_id(need)
        if new.get(node_id) is None:
            new = new.with_node(_capability_node(need, container_executor))
            cap_nodes += 1
        new = new.with_edge(_project_edge(proj_id, node_id))

    def _seed_apt(name: str) -> None:
        nonlocal new, aptdep_nodes
        node_id = apt_build_id(name)
        if new.get(node_id) is None:
            new = new.with_node(_apt_build_node(name))
            aptdep_nodes += 1
        new = new.with_edge(_project_edge(proj_id, node_id))

    # §2.4 (primary) — the repo's OWN declared native-build surface
    # (setup.py Extension.libraries / pyproject ext-modules), resolved
    # through the ecosystem-neutral os_resolver.
    try:
        scanned_needs = list(scan_native_build_surface(repo_path))
    except Exception:
        logger.exception(
            "project_native_obligations: scan_native_build_surface failed for %s", repo_path
        )
        scanned_needs = []
    for need in scanned_needs:
        _seed_capability(need)

    # Native-build signal (computed ONCE, reused by §2.3's gate below and
    # §2.5's floor). Degrades to False on any scan failure.
    try:
        native_signal = bool(has_native_build_signal(repo_path))
    except Exception:
        logger.exception(
            "project_native_obligations: has_native_build_signal failed for %s", repo_path
        )
        native_signal = False

    # §2.3 — Debian Build-Depends + the curated table, keyed by the PROJECT'S
    # OWN distribution name (not a dependency's) — GATED on the native-build
    # signal. A pure-Python project (no Extension/.pyx/native backend) needs
    # ZERO system build-deps, so its Debian NAMESAKE must never be consulted:
    # `apt-cache showsrc <name>` can return an UNRELATED same-named Debian
    # source (e.g. Ubuntu's Click package manager for Python "click", or
    # rich's `pybuild-plugin-pyproject`), over-predicting apt for a repo that
    # compiles nothing. Only a project that actually builds native code looks
    # up its own Build-Depends. lxml/pygraphviz (signal True) keep this path.
    if native_signal:
        canonical = normalize_package_name(project_node.name)
        for need in PACKAGE_TO_BUILD_NEEDS.get(canonical, ()):
            _seed_capability(need)
        try:
            debian_names = list(debian_build_deps(canonical, container_executor))
        except Exception:
            logger.exception(
                "project_native_obligations: debian_build_deps failed for %s", canonical
            )
            debian_names = []
        # apt-installability guard (mirrors build_deps.build_dep_prior): a
        # project whose own Debian source has a conflicting/uninstallable JOINT
        # Build-Depends (the uWSGI class) would break the whole apt step at
        # replay. Simulate-install the set (`apt-get install -s`); on failure
        # drop the ENTIRE Debian set, keeping §2.4/§2.5/§2.2.
        if debian_names and not _apt_installable(debian_names, container_executor):
            logger.info(
                "project_native_obligations: %s debian set not apt-installable, "
                "dropping %s", canonical, debian_names,
            )
            debian_names = []
        for name in debian_names:
            _seed_apt(name)

    # §2.2 — PEP 725 [external], read directly off the local checkout (no
    # sdist download: the manifest is already on disk for the repo under
    # test). Near-zero recall today; future-proofing.
    pyproject_path = Path(repo_path) / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            pyproject_text = pyproject_path.read_text(encoding="utf-8")
        except OSError:
            pyproject_text = None
        if pyproject_text is not None:
            try:
                pep_needs = list(
                    needs_from_pyproject(pyproject_text, source=project_node.name)
                )
            except Exception:
                logger.exception(
                    "project_native_obligations: needs_from_pyproject failed for %s",
                    repo_path,
                )
                pep_needs = []
            for need in pep_needs:
                _seed_capability(need)

    # §2.5 — unconditional build-essential floor for ANY detected
    # native-build signal (``native_signal``, computed above), even when no
    # specific library was statically extractable. Reuses seed.py's singleton
    # node/id — deduped, never a second copy.
    if native_signal:
        if new.get(_BUILD_ESSENTIAL_ID) is None:
            new = new.with_node(_build_essential_node())
        new = new.with_edge(_project_edge(proj_id, _BUILD_ESSENTIAL_ID))

    logger.info(
        "project_native_obligations: project=%s cap_nodes=%d aptdep_nodes=%d "
        "native_signal=%s",
        project_node.name, cap_nodes, aptdep_nodes, native_signal,
    )
    return new
