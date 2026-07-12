# Three-Way Local-Import Diagnosis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the diagnosis router from silently giving up on a real missing package whose name collides with any `.py` stem in the repo, without ever letting the construction path install a wrong package.

**Architecture:** Today one over-broad name set (`scan.local_module_names` — every `.py` stem and `__init__` dir basename anywhere, 400-757 names/repo) serves two consumers with **opposite error asymmetries**. In `scan_to_nodes` a false-*local* is cosmetic while a false-*external* makes Phase-A's identity candidate ladder install a **wrong PyPI package** — so over-breadth is the *correct*, conservative bias there. In `diagnose.is_local_import` a false-*local* is a **silent give-up** on a real environment failure. We leave construction alone and split the diagnosis decision three ways: a precise sys.path-accurate top-level set says "definitely ours"; the residue (stem collisions) says "undecidable statically — hand it to the repair loop *with evidence*, never give up silently"; everything else is external.

**Tech Stack:** Python 3.11+, pytest, no new dependencies.

## Global Constraints

- **`scan.scan_to_nodes` and `scan._local_module_names` behavior MUST NOT change.** Construction keeps its conservative drop. If Phase-A ever sees typer's `items` or netbox's `extras` as an Import node, it will `ACCEPT` the identically-named real PyPI distribution and install it. This is the failure that killed two prior designs.
- **`setup.sh` MUST be byte-identical** on the smoke corpus after every task. Nothing in this plan touches construction.
- The new walk MUST be **uncapped** (never use `import_graph._iter_python_files`, which caps at `MAX_PYTHON_FILES = 1000`; netbox has 1,184 `.py` files) and MUST prune **exactly** `scan.SKIP_WALK_DIRS`.
- The new walk MUST **never climb above the repo root**.
- `RepoContext` changes must be **additive with safe defaults** — every existing test constructs `RepoContext(local_names=...)` or `RepoContext()` and must keep passing unchanged.
- Type annotations on all signatures; frozen dataclasses; PEP 8.

## Reference: the two repos this exists to get right

| repo | file on disk | real module name | bare import | correct verdict |
|---|---|---|---|---|
| wagtail | `wagtail/contrib/frontend_cache/backends/azure.py` | `wagtail.contrib.frontend_cache.backends.azure` | `import azure` in `…/frontend_cache/tests.py` | **external** — `azure-mgmt-cdn` is extras-gated and never installed; today this silently gives up |
| typer | `docs_src/subcommands/tutorial001/items.py` | `tutorial001.items` | `import items` in sibling `main.py` | **NOT external** — `items` is a real PyPI package; installing it is wrong. Repo's own oracle `src/eval/graph_fidelity/ab_gold_labels.py:59-62` labels it `"local"` |

Both are `.py` files whose stem collides with a PyPI name. **No tree walk can tell them apart** — the difference is whether the importer was run as a script (`sys.path[0]` = its dir) or imported as a package module. That is a runtime fact. Hence the third mode.

---

### Task 1: Precise repo-module walk (`repo_modules.py`)

**Files:**
- Create: `src/python_deps/depgraph/repo_modules.py`
- Modify: `src/python_deps/depgraph/scan.py:70-72` (promote `_SKIP_WALK_DIRS` → public `SKIP_WALK_DIRS`)
- Test: `tests/depgraph/test_repo_modules.py`

**Interfaces:**
- Consumes: `scan.SKIP_WALK_DIRS` (frozenset[str])
- Produces:
  - `ModuleDef` — frozen dataclass with `sys_path_root: str`, `dotted: str`, `path: str`
  - `repo_modules(repo_path: str) -> tuple[ModuleDef, ...]`
  - `top_level_names(repo_path: str) -> frozenset[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_repo_modules.py`:

```python
"""Tests for repo_modules — the sys.path-accurate repo module walk (pure, no Docker)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.repo_modules import repo_modules, top_level_names


def _write(root: Path, rel: str, body: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _dotted(root: Path) -> dict[str, str]:
    """path -> dotted, for compact assertions."""
    return {m.path: m.dotted for m in repo_modules(str(root))}


def test_src_layout(tmp_path):
    _write(tmp_path, "src/flask/__init__.py")
    _write(tmp_path, "src/flask/app.py")
    assert _dotted(tmp_path)["src/flask/app.py"] == "flask.app"
    assert top_level_names(str(tmp_path)) == {"flask"}


def test_pep420_namespace_package_keeps_climbing(tmp_path):
    """flask/sansio: a real subpackage with NO __init__.py. It is NOT a sys.path
    root — its parent is a package — so the walk must climb THROUGH it."""
    _write(tmp_path, "src/flask/__init__.py")
    _write(tmp_path, "src/flask/sansio/app.py")          # no sansio/__init__.py
    assert _dotted(tmp_path)["src/flask/sansio/app.py"] == "flask.sansio.app"
    assert top_level_names(str(tmp_path)) == {"flask"}   # NOT {"flask", "app"}


def test_shadowing_submodule_is_not_a_top_level(tmp_path):
    """jupyterhub/traitlets.py is `jupyterhub.traitlets` — bare `import traitlets`
    resolves to PyPI, not to this file."""
    _write(tmp_path, "jupyterhub/__init__.py")
    _write(tmp_path, "jupyterhub/traitlets.py")
    assert top_level_names(str(tmp_path)) == {"jupyterhub"}


def test_non_standard_source_root(tmp_path):
    """netbox: source root is `netbox/`, which has no __init__.py."""
    _write(tmp_path, "netbox/extras/__init__.py")
    _write(tmp_path, "netbox/extras/models.py")
    _write(tmp_path, "netbox/utilities/__init__.py")
    _write(tmp_path, "netbox/utilities/jinja2.py")
    tops = top_level_names(str(tmp_path))
    assert "extras" in tops        # bare-importable; must stay LOCAL
    assert "utilities" in tops
    assert "jinja2" not in tops    # utilities.jinja2 — must be EXTERNAL


def test_test_fixture_package_is_bare_importable(tmp_path):
    """flask's tests/blueprintapp: tests/ has no __init__.py, so it IS a root."""
    _write(tmp_path, "tests/blueprintapp/__init__.py")
    assert top_level_names(str(tmp_path)) == {"blueprintapp"}


def test_flat_repo(tmp_path):
    _write(tmp_path, "foo.py")
    assert _dotted(tmp_path)["foo.py"] == "foo"
    assert top_level_names(str(tmp_path)) == {"foo"}


def test_never_climbs_above_repo_root(tmp_path):
    """A repo whose ROOT has __init__.py must not produce names from outside it."""
    _write(tmp_path, "__init__.py")
    _write(tmp_path, "mod.py")
    assert _dotted(tmp_path)["mod.py"] == "mod"
    assert top_level_names(str(tmp_path)) == {"mod"}


def test_skips_pruned_dirs(tmp_path):
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, ".git/hooks/thing.py")
    _write(tmp_path, "build/generated.py")
    _write(tmp_path, "__pycache__/cached.py")
    assert top_level_names(str(tmp_path)) == {"pkg"}


def test_typer_tutorial_leaf_package_under_non_package_parent(tmp_path):
    """typer docs_src: tutorial001/ HAS __init__.py, its parent does NOT.
    So items.py is `tutorial001.items` and `items` is NOT a top-level.
    This is the case the diagnosis router must treat as a STEM COLLISION,
    never as a plain external (see Task 4)."""
    _write(tmp_path, "docs_src/subcommands/tutorial001/__init__.py")
    _write(tmp_path, "docs_src/subcommands/tutorial001/items.py")
    _write(tmp_path, "docs_src/subcommands/tutorial001/main.py", "import items\n")
    mods = _dotted(tmp_path)
    assert mods["docs_src/subcommands/tutorial001/items.py"] == "tutorial001.items"
    assert "items" not in top_level_names(str(tmp_path))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_repo_modules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.repo_modules'`

- [ ] **Step 3: Promote the shared skip-set constant**

In `src/python_deps/depgraph/scan.py`, replace lines 69-72:

```python
# Dirs never worth walking for local-name detection (vcs/build/venv noise).
# PUBLIC: `repo_modules` walks the SAME tree and MUST prune identically —
# if the two walks diverge, the subset invariant in repo_modules breaks.
SKIP_WALK_DIRS: frozenset[str] = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache"}
) | _EXCLUDED_SEGMENTS

_SKIP_WALK_DIRS: frozenset[str] = SKIP_WALK_DIRS  # back-compat alias
```

- [ ] **Step 4: Write the implementation**

Create `src/python_deps/depgraph/repo_modules.py`:

```python
"""Sys.path-accurate repo module walk.

Answers ONE question: which top-level module names does this repository
actually define? That is NOT "which .py stems exist" — since Python 3, absolute
imports mean ``jupyterhub/traitlets.py`` is ``jupyterhub.traitlets`` and bare
``import traitlets`` resolves to PyPI, never to that file.

The rule is CPython/pytest's basedir algorithm:

    From a .py file, climb while the directory has ``__init__.py``, OR while the
    directory's PARENT has ``__init__.py`` (a directory with no ``__init__.py``
    whose parent has one is a PEP 420 namespace package -- NOT a sys.path root).
    The first directory failing both is the sys.path root; the dotted name is the
    path from there. Never climb above the repo root.

The parent-climb clause is load-bearing: ``src/flask/sansio/`` has no
``__init__.py`` but is a real subpackage (``flask.sansio.app``). Without it the
walk stops there and mints the top-level ``app`` -- exactly the generic-name
pollution this module exists to eliminate.

CONSUMER WARNING. This set is NARROWER than ``scan.local_module_names``. It is
correct for DIAGNOSIS (deciding whether a failing import is ours) but MUST NOT
replace the construction-time drop in ``scan.scan_to_nodes``: there, a
false-external reaches Phase-A's identity candidate ladder, which will install
an identically-named real PyPI distribution (typer's ``items``, netbox's
``extras`` are both real packages). See
``docs/superpowers/specs/2026-07-13-local-module-resolution-fixes.md``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from python_deps.depgraph.scan import SKIP_WALK_DIRS


@dataclass(frozen=True)
class ModuleDef:
    """One importable module defined by the repo."""

    sys_path_root: str   # repo-relative dir that must be on sys.path ("." for root)
    dotted: str          # importable name from that root, e.g. "flask.sansio.app"
    path: str            # repo-relative file path, e.g. "src/flask/sansio/app.py"


def _has_init(directory: Path) -> bool:
    return (directory / "__init__.py").is_file()


def _module_for(file_path: Path, repo: Path) -> ModuleDef | None:
    parts: list[str] = []
    current = file_path.parent
    # The repo root is ALWAYS a terminal sys.path root: `while current != repo`
    # is what makes "never climb above the repo root" structural rather than a
    # check we could forget. A repo whose own root has __init__.py must NOT have
    # its own directory name consumed as a package segment -- that would walk out
    # of the tree and `relative_to(repo)` below would raise ValueError.
    while current != repo:
        # Climb through a package dir, and through a PEP 420 namespace dir (no
        # __init__.py of its own, but its parent has one -> still inside a
        # package, not a sys.path root). Without the second clause,
        # `src/flask/sansio/` reads as a root and mints the top-level `app`.
        if _has_init(current) or _has_init(current.parent):
            parts.insert(0, current.name)
            current = current.parent
            continue
        break

    stem = file_path.stem
    segments = parts if stem == "__init__" else parts + [stem]
    dotted = ".".join(segments)
    if not dotted:
        return None

    root = "." if current == repo else current.relative_to(repo).as_posix()
    return ModuleDef(
        sys_path_root=root,
        dotted=dotted,
        path=file_path.relative_to(repo).as_posix(),
    )


def repo_modules(repo_path: str) -> tuple[ModuleDef, ...]:
    """Every module the repo defines, with its sys.path root and dotted name.

    Uncapped by design -- ``import_graph._iter_python_files`` caps at 1000 files
    and netbox has 1,184, which would drop its core ``extras`` app from the set.
    """
    repo = Path(repo_path)
    found: list[ModuleDef] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_WALK_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            module = _module_for(Path(dirpath) / filename, repo)
            if module is not None:
                found.append(module)
    return tuple(found)


def top_level_names(repo_path: str) -> frozenset[str]:
    """The bare names a repo module can actually be imported by.

    ``jupyterhub/traitlets.py`` contributes ``jupyterhub`` -- NOT ``traitlets``.
    """
    return frozenset(m.dotted.split(".", 1)[0] for m in repo_modules(repo_path))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_repo_modules.py -q`
Expected: PASS, 9 passed

- [ ] **Step 6: Verify nothing else broke**

Run: `python3 -m pytest tests/depgraph/ -q`
Expected: PASS (the `_SKIP_WALK_DIRS` alias keeps `scan.py` callers working)

- [ ] **Step 7: Commit**

```bash
git add src/python_deps/depgraph/repo_modules.py src/python_deps/depgraph/scan.py tests/depgraph/test_repo_modules.py
git commit -m "feat(depgraph): sys.path-accurate repo module walk (basedir + PEP-420 parent-climb)"
```

---

### Task 2: Stem-collision derivation

**Files:**
- Modify: `src/python_deps/depgraph/repo_modules.py`
- Test: `tests/depgraph/test_repo_modules.py`

**Interfaces:**
- Consumes: `repo_modules()`, `top_level_names()` (Task 1); `scan.local_module_names()`
- Produces: `stem_collisions(repo_path: str) -> dict[str, str]` — bare name → the real dotted module name of a file whose stem collides with it. Keys are exactly `scan.local_module_names(repo) - top_level_names(repo)`.

A collision is a name the *broad* walk harvests (some `.py` stem or `__init__` dir basename) that is **not** an importable top-level. `azure` and `items` are both collisions. The value is the evidence a repair agent needs.

- [ ] **Step 1: Write the failing test**

Append to `tests/depgraph/test_repo_modules.py`:

```python
from python_deps.depgraph.repo_modules import stem_collisions


def test_stem_collisions_are_broad_minus_precise(tmp_path):
    _write(tmp_path, "wagtail/__init__.py")
    _write(tmp_path, "wagtail/contrib/__init__.py")
    _write(tmp_path, "wagtail/contrib/backends/__init__.py")
    _write(tmp_path, "wagtail/contrib/backends/azure.py")

    collisions = stem_collisions(str(tmp_path))
    assert "azure" in collisions
    assert collisions["azure"] == "wagtail.contrib.backends.azure"
    assert "wagtail" not in collisions        # a real top-level, not a collision


def test_stem_collisions_include_leaf_package_siblings(tmp_path):
    """typer's `items`: a collision, NOT a plain external."""
    _write(tmp_path, "docs_src/subcommands/tutorial001/__init__.py")
    _write(tmp_path, "docs_src/subcommands/tutorial001/items.py")

    collisions = stem_collisions(str(tmp_path))
    assert collisions["items"] == "tutorial001.items"


def test_nested_module_is_a_collision(tmp_path):
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, "pkg/core.py")
    # `core` IS a broad-walk stem but is NOT a top-level -> it IS a collision.
    # Correct: `import core` cannot reach pkg/core.py (absolute imports).
    assert stem_collisions(str(tmp_path)) == {"core": "pkg.core"}


def test_package_dir_basename_is_also_a_collision(tmp_path):
    """The broad walk harvests dir basenames too; the leaf of a package's own
    __init__.py ModuleDef covers them."""
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, "pkg/backends/__init__.py")
    assert stem_collisions(str(tmp_path))["backends"] == "pkg.backends"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_repo_modules.py -q -k collision`
Expected: FAIL — `ImportError: cannot import name 'stem_collisions'`

- [ ] **Step 3: Write the implementation**

Append to `src/python_deps/depgraph/repo_modules.py`:

```python
def stem_collisions(repo_path: str) -> dict[str, str]:
    """Names the BROAD walk harvests that are NOT importable top-levels.

    ``scan.local_module_names`` collects every ``.py`` stem and ``__init__`` dir
    basename anywhere in the tree. The difference between that set and
    :func:`top_level_names` is the COLLISION ZONE: names like wagtail's ``azure``
    (really ``wagtail...backends.azure``) and typer's ``items`` (really
    ``tutorial001.items``).

    A collision is NOT decidable statically. ``azure`` is a real missing PyPI
    package; ``items`` is a sibling script reachable only because its directory
    lands on ``sys.path[0]`` when ``main.py`` is run directly. Both look
    identical to any tree walk -- the difference is HOW the importer was loaded,
    a runtime fact. The router therefore routes collisions to ``AMBIGUOUS`` and
    attaches this mapping as evidence, rather than deciding.

    Returns ``{bare_name: real_dotted_name}``. On a name backed by several files,
    the lexicographically-first dotted name wins (deterministic).
    """
    from python_deps.depgraph.scan import local_module_names

    modules = repo_modules(repo_path)
    tops = frozenset(m.dotted.split(".", 1)[0] for m in modules)
    broad = local_module_names(repo_path)

    evidence: dict[str, str] = {}
    for module in modules:
        # The LEAF segment is what the broad walk harvests: a .py stem
        # (`azure` from `...backends.azure`) or a package dir basename (`backends`
        # from its own `__init__.py`, whose dotted name ends in `backends`).
        # A module's TOP-level segment is by definition in `tops`, so it can never
        # be a collision — only the leaf can.
        leaf = module.dotted.rsplit(".", 1)[-1]
        if leaf in broad and leaf not in tops:
            previous = evidence.get(leaf)
            if previous is None or module.dotted < previous:
                evidence[leaf] = module.dotted
    return evidence
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_repo_modules.py -q`
Expected: PASS, 13 passed (9 from Task 1 + 4 here)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/repo_modules.py tests/depgraph/test_repo_modules.py
git commit -m "feat(depgraph): derive stem collisions (broad walk minus importable top-levels)"
```

---

### Task 3: `Locality` classification in the router

**Files:**
- Modify: `src/python_deps/depgraph/diagnose.py:30-58`
- Test: `tests/depgraph/test_diagnose_types.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2 at runtime (pure over the context)
- Produces:
  - `Locality` enum — `REPO_MODULE`, `STEM_COLLISION`, `EXTERNAL`
  - `classify_locality(import_name: str, ctx: RepoContext) -> Locality`
  - `RepoContext.collisions: dict[str, str]` (new field, default `{}`, `compare=False`)
  - `is_local_import` — **signature and behavior unchanged**

This task adds the classifier but does **not** change routing. Routing changes in Task 4, so a reviewer can reject one without the other.

- [ ] **Step 1: Write the failing test**

Append to `tests/depgraph/test_diagnose_types.py`:

```python
from python_deps.depgraph.diagnose import Locality, classify_locality


def test_locality_repo_module():
    ctx = RepoContext(local_names=frozenset({"wagtail"}))
    assert classify_locality("wagtail", ctx) is Locality.REPO_MODULE
    assert classify_locality("wagtail.admin", ctx) is Locality.REPO_MODULE


def test_locality_stem_collision():
    ctx = RepoContext(
        local_names=frozenset({"wagtail"}),
        collisions={"azure": "wagtail.contrib.frontend_cache.backends.azure"},
    )
    assert classify_locality("azure", ctx) is Locality.STEM_COLLISION
    assert classify_locality("azure.mgmt.cdn", ctx) is Locality.STEM_COLLISION


def test_locality_external():
    ctx = RepoContext(local_names=frozenset({"wagtail"}))
    assert classify_locality("requests", ctx) is Locality.EXTERNAL
    assert classify_locality("", ctx) is Locality.EXTERNAL


def test_repo_module_wins_over_collision():
    """A name in BOTH sets is a real top-level — never a collision."""
    ctx = RepoContext(local_names=frozenset({"pkg"}), collisions={"pkg": "other.pkg"})
    assert classify_locality("pkg", ctx) is Locality.REPO_MODULE


def test_default_context_has_no_collisions():
    """Back-compat: every existing RepoContext(...) call site keeps working."""
    assert RepoContext().collisions == {}
    assert classify_locality("anything", RepoContext()) is Locality.EXTERNAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_diagnose_types.py -q`
Expected: FAIL — `ImportError: cannot import name 'Locality'`

- [ ] **Step 3: Write the implementation**

In `src/python_deps/depgraph/diagnose.py`, add after the `Mode` enum (line 27):

```python
class Locality(enum.Enum):
    """Where a failing import's name comes from, as far as the TREE can tell.

    The three-way split exists because the tree CANNOT decide the middle case.
    ``wagtail...backends.azure`` and ``tutorial001.items`` are structurally
    identical -- a .py file whose stem collides with a real PyPI name -- yet
    ``azure`` needs installing and ``items`` must never be installed. What
    separates them is whether the importer ran as a script (``sys.path[0]`` = its
    own directory) or was imported as a package module. That is a runtime fact,
    visible in the traceback and invisible to any walk.
    """

    REPO_MODULE = "repo_module"        # an importable top-level the repo defines
    STEM_COLLISION = "stem_collision"  # matches a .py stem, but is NOT importable as a top-level
    EXTERNAL = "external"              # the repo has nothing by this name
```

Extend `RepoContext` (replace lines 30-39):

```python
@dataclass(frozen=True)
class RepoContext:
    # PRECISE, sys.path-accurate top-level module names (repo_modules.top_level_names).
    # NOT scan.local_module_names -- that set is deliberately over-broad (it harvests
    # every .py stem anywhere) and using it here is what makes the router give up
    # silently on `azure`/`traitlets`/`jinja2`.
    local_names: frozenset[str] = field(default_factory=frozenset)
    # PEP-503-normalized (see ``_norm``) disproven package names — callers must
    # normalize before constructing (``diagnose`` compares both sides via
    # ``_norm`` so ``Frobnicate_9000`` and ``frobnicate-9000`` are the same
    # entry). Kept separate from ``repair_loop.known_invalid``, which is a
    # heterogeneous key space of raw failed commands + node/block ids —
    # mixing normalized package names into it would corrupt equality lookups.
    invalid_names: frozenset[str] = field(default_factory=frozenset)
    # {bare_name: real_dotted_name} for names the broad walk harvests that are NOT
    # importable top-levels (repo_modules.stem_collisions). Evidence, not a verdict:
    # the router hands these to the repair loop rather than deciding. Excluded from
    # eq/hash (dict is unhashable; RepoContext is otherwise a value object).
    collisions: dict[str, str] = field(default_factory=dict, compare=False)
```

Add after `is_local_import` (after line 58):

```python
def classify_locality(import_name: str, ctx: RepoContext) -> Locality:
    """Three-way locality of a failing import, by its top-level segment.

    ``REPO_MODULE`` wins over ``STEM_COLLISION``: a name that is a genuine
    importable top-level is never merely a collision.
    """
    if not import_name:
        return Locality.EXTERNAL
    top = import_name.split(".", 1)[0]
    if top in ctx.local_names:
        return Locality.REPO_MODULE
    if top in ctx.collisions:
        return Locality.STEM_COLLISION
    return Locality.EXTERNAL
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_diagnose_types.py -q`
Expected: PASS

- [ ] **Step 5: Verify every existing diagnose test still passes**

Run: `python3 -m pytest tests/depgraph/ tests/envstate/ -q`
Expected: PASS — `collisions` defaults to `{}`, so routing is unchanged

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/diagnose.py tests/depgraph/test_diagnose_types.py
git commit -m "feat(diagnose): add Locality three-way classifier + RepoContext.collisions"
```

---

### Task 4: Route stem collisions to AMBIGUOUS with evidence

**Files:**
- Modify: `src/python_deps/depgraph/diagnose.py:105-118` (`module_not_found`) and `:130-145` (`import_name_error`)
- Test: `tests/depgraph/test_diagnose_router.py`

**Interfaces:**
- Consumes: `Locality`, `classify_locality`, `RepoContext.collisions` (Task 3)
- Produces: no new symbols. `diagnose()` behavior changes for names in `ctx.collisions`.

**Why AMBIGUOUS and not ENVIRONMENT:** both route to `run_structured_repair` (`orchestrator.py:762-793`), so `azure` gets its repair turn either way. But `AMBIGUOUS` carries no `Discovery`, so the deterministic ingest tier cannot auto-mint a `pkg:` node — the LLM must propose one against the failure text and pass `patch_gate`. That is exactly the caution the collision zone warrants.

- [ ] **Step 1: Write the failing test**

Append to `tests/depgraph/test_diagnose_router.py`:

```python
_WAGTAIL_CTX = RepoContext(
    local_names=frozenset({"wagtail", "runtests", "setup", "tests"}),
    collisions={"azure": "wagtail.contrib.frontend_cache.backends.azure"},
)
_TYPER_CTX = RepoContext(
    local_names=frozenset({"typer", "tests"}),
    collisions={"items": "tutorial001.items"},
)


def test_stem_collision_does_not_silently_give_up():
    """wagtail `azure`: azure-mgmt-cdn is extras-gated and never installed.
    The OLD broad rule called this repo-local and returned REPO_INTERNAL_REF —
    a silent give-up with no repair attempted. It must reach the repair loop."""
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'azure'",
                 _WAGTAIL_CTX)
    assert d.mode is not Mode.REPO_INTERNAL_REF
    assert d.mode is Mode.AMBIGUOUS


def test_stem_collision_carries_the_real_module_path_as_evidence():
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'azure'",
                 _WAGTAIL_CTX)
    assert "wagtail.contrib.frontend_cache.backends.azure" in d.reason


def test_stem_collision_mints_no_discovery():
    """AMBIGUOUS carries no Discovery, so the deterministic ingest tier cannot
    auto-mint pkg:azure. The LLM must propose it against the failure text."""
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'azure'",
                 _WAGTAIL_CTX)
    assert d.discovery is None


def test_stem_collision_is_not_environment_for_a_syspath_sibling():
    """typer `items`: a REAL PyPI package, but installing it is WRONG — it is a
    sibling script reachable via sys.path[0]. Routing this to ENVIRONMENT would
    hand the deterministic tier a mapped package. It must stay AMBIGUOUS."""
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'items'",
                 _TYPER_CTX)
    assert d.mode is not Mode.ENVIRONMENT
    assert d.mode is Mode.AMBIGUOUS
    assert d.discovery is None


def test_real_repo_module_still_routes_repo_internal_ref():
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'wagtail'",
                 _WAGTAIL_CTX)
    assert d.mode is Mode.REPO_INTERNAL_REF


def test_plain_external_still_routes_environment():
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'requests'",
                 _WAGTAIL_CTX)
    assert d.mode is Mode.ENVIRONMENT
    assert d.discovery is not None


def test_collision_precedes_invalid_attempt_check():
    """A collision name already pip-disproven must NOT be retried."""
    ctx = RepoContext(
        local_names=frozenset({"wagtail"}),
        collisions={"azure": "wagtail.contrib.backends.azure"},
        invalid_names=frozenset({"azure"}),
    )
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'azure'",
                 ctx)
    assert d.mode is Mode.INVALID_ATTEMPT


def test_import_name_error_stem_collision_routes_ambiguous():
    """Task 4 patches BOTH branches. The import_name_error branch needs its own
    test or a regression there ships silently."""
    d = diagnose(
        "python -m pytest -q",
        "ImportError: cannot import name 'Foo' from 'items' "
        "(docs_src/subcommands/tutorial001/items.py)",
        _TYPER_CTX,
    )
    assert d.mode is Mode.AMBIGUOUS
    assert d.discovery is None
    assert "tutorial001.items" in d.reason


def test_import_name_error_repo_module_routes_repo_internal_ref():
    d = diagnose(
        "python -m pytest -q",
        "ImportError: cannot import name 'Foo' from 'wagtail' (wagtail/__init__.py)",
        _WAGTAIL_CTX,
    )
    assert d.mode is Mode.REPO_INTERNAL_REF
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_diagnose_router.py -q -k "collision or syspath or give_up"`
Expected: FAIL — collisions currently hit `is_local_import` and return `REPO_INTERNAL_REF`

- [ ] **Step 3: Write the implementation**

In `src/python_deps/depgraph/diagnose.py`, add this helper just above `diagnose` (after `_RESIDUAL_RE`, line 83):

```python
def _locality_diagnosis(import_name: str, ctx: RepoContext) -> Diagnosis | None:
    """Route on locality alone, BEFORE consulting the package mapper.

    Returns ``None`` when the name is EXTERNAL and normal classification should
    proceed. Handles both terminal locality modes:

    * ``REPO_MODULE``     -> REPO_INTERNAL_REF. Genuinely ours; the graph cannot
      close it by adding a node.
    * ``STEM_COLLISION``  -> INVALID_ATTEMPT if pip already disproved the name,
      else AMBIGUOUS carrying the real dotted module path as evidence. NEVER a
      silent give-up (that is the `azure` bug) and NEVER an ENVIRONMENT discovery
      (that is the `items` bug -- a real PyPI package that must not be installed).
    """
    locality = classify_locality(import_name, ctx)
    if locality is Locality.REPO_MODULE:
        return Diagnosis(Mode.REPO_INTERNAL_REF, None,
                         f"{import_name!r} resolves to a repo-local module")
    if locality is Locality.STEM_COLLISION:
        if _previously_disproven(None, import_name, ctx.invalid_names):
            return Diagnosis(Mode.INVALID_ATTEMPT, None,
                             f"import {import_name!r} was previously disproven")
        top = import_name.split(".", 1)[0]
        real = ctx.collisions[top]
        return Diagnosis(
            Mode.AMBIGUOUS, None,
            f"import {import_name!r} collides with repo file {real!r} but is NOT an "
            f"importable top-level of this repo — it is either a missing external "
            f"package or a sys.path-dependent sibling import; probe before repair",
        )
    return None
```

Replace the `module_not_found` branch (lines 105-118):

```python
    if dep.failure_type == "module_not_found":
        import_name = dep.import_name or ""
        by_locality = _locality_diagnosis(import_name, ctx)
        if by_locality is not None:
            return by_locality
        disc = classify_observation(command, text)
        if disc is None:
            return Diagnosis(Mode.AMBIGUOUS, None,
                             f"import {import_name!r} had no package mapping")
        if _previously_disproven(disc, import_name, ctx.invalid_names):
            return Diagnosis(Mode.INVALID_ATTEMPT, None,
                             f"import {import_name!r} was previously disproven")
        return Diagnosis(Mode.ENVIRONMENT, disc,
                         f"external import {import_name!r} -> package requirement")
```

Replace the head of the `import_name_error` branch (lines 130-134):

```python
    if dep.failure_type == "import_name_error":
        import_name = dep.import_name or ""
        by_locality = _locality_diagnosis(import_name, ctx)
        if by_locality is not None:
            return by_locality
        failed_from = dep.details.get("module_name", import_name)
```

(the rest of that branch is unchanged)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_diagnose_router.py -q`
Expected: PASS

- [ ] **Step 5: Verify the whole suite**

Run: `python3 -m pytest tests/depgraph/ tests/envstate/ -q`
Expected: PASS — existing tests pass empty `collisions`, so their routing is untouched

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/diagnose.py tests/depgraph/test_diagnose_router.py
git commit -m "fix(diagnose): route stem collisions to AMBIGUOUS with evidence, not a silent give-up"
```

---

### Task 5: Wire the precise sets into the orchestrator

**Files:**
- Modify: `src/envstate/orchestrator.py:729-735` (RepoContext construction) and `:964-978` (`_guarded_llm`)
- Test: `tests/envstate/test_repair_routing.py`

**Interfaces:**
- Consumes: `repo_modules.top_level_names`, `repo_modules.stem_collisions` (Tasks 1-2); `classify_locality`, `Locality` (Task 3)
- Produces: no new symbols.

`_guarded_llm` currently suppresses the LLM classifier tier for any name in the broad set. It must keep suppressing for **both** `REPO_MODULE` and `STEM_COLLISION` — that tier auto-mints `pkg:` nodes, and a collision must not be auto-minted. Suppression behavior is therefore **unchanged in effect**; only its source of truth moves.

**Existing test that WILL break — this is the point of the task.** `tests/envstate/test_repair_routing.py:148-170` (`test_repo_internal_ref_bundle_skips_repair`) monkeypatches `scan_module.local_module_names` and passes `repo_path="/fake/repo"`. Once the orchestrator reads `repo_modules.top_level_names` instead, that monkeypatch stops taking effect, the real walk runs on a nonexistent path, `docs_src` is no longer local, and the test fails. It must be re-pointed at the new seam — **not deleted, and not made to pass by weakening the assertion.**

(`tests/envstate/test_ingest_local_import_guard.py` and `tests/envstate/scenarios/test_repo_local_import_guard.py` build **real** `tmp_path/docs_src/__init__.py` trees, so they exercise the new walk for real and must keep passing untouched. If either fails, the walk is wrong — do not patch the test.)

- [ ] **Step 1: Write the failing test — the orchestrator must route a collision to repair**

Append to `tests/envstate/test_repair_routing.py`:

```python
def test_stem_collision_bundle_spends_a_repair_turn(tmp_path, monkeypatch):
    """The `azure` bug, end-to-end through run_v3.

    A REAL tree whose only `azure` is `wagtail/backends/azure.py` — i.e. the
    module `wagtail.backends.azure`, NOT an importable top-level. The old broad
    rule called it repo-local and returned REPO_INTERNAL_REF without ever calling
    build_agent.propose — a silent give-up on a genuinely missing package.
    It must now reach the repair loop.
    """
    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)

    (tmp_path / "wagtail" / "backends").mkdir(parents=True)
    (tmp_path / "wagtail" / "__init__.py").write_text("")
    (tmp_path / "wagtail" / "backends" / "__init__.py").write_text("")
    (tmp_path / "wagtail" / "backends" / "azure.py").write_text("")

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="python -m pytest -q", lineno=None,
            stderr="ModuleNotFoundError: No module named 'azure'",
        )

    agent = _RecordingBuildAgent()
    inputs = _base_inputs(agent, run_install_script, repo_path=str(tmp_path))

    orchestrator.run_v3(**inputs)

    assert agent.propose_calls > 0, (
        "propose was never called for 'azure': the router still treats a stem "
        "collision as a repo-internal reference and gives up silently"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/envstate/test_repair_routing.py::test_stem_collision_bundle_spends_a_repair_turn -q`
Expected: FAIL — `assert agent.propose_calls > 0` (currently `scan.local_module_names` harvests the bare stem `azure`, so the router returns `REPO_INTERNAL_REF` and never proposes)

- [ ] **Step 3: Wire RepoContext construction**

In `src/envstate/orchestrator.py`, replace lines 729-735:

```python
    from python_deps.depgraph.diagnose import (
        Locality, RepoContext, Mode, classify_locality, diagnose_all,
    )
    from python_deps.depgraph import repo_modules as _repo_modules
    from python_deps.import_mapping import normalize_package_name
    # PRECISE top-levels for the give-up decision; the COLLISION zone (broad-walk
    # stems that are not importable top-levels) is routed to AMBIGUOUS with
    # evidence instead of being silently dropped. Construction (scan_to_nodes)
    # deliberately still uses the over-broad scan.local_module_names — a
    # false-external THERE reaches Phase-A's identity ladder and installs a wrong
    # PyPI package (typer `items`, netbox `extras` are both real dists).
    _local_names = (
        _repo_modules.top_level_names(repo_path) if repo_path else frozenset()
    )
    _collisions = _repo_modules.stem_collisions(repo_path) if repo_path else {}
    _invalid_names: set[str] = set()

    def _repo_ctx() -> RepoContext:
        return RepoContext(
            local_names=_local_names,
            invalid_names=frozenset(_invalid_names),
            collisions=_collisions,
        )
```

- [ ] **Step 4: Update `_guarded_llm`**

In `src/envstate/orchestrator.py`, replace the body of `_guarded_llm` (around lines 971-978):

```python
                def _guarded_llm(cmd, out):
                    disc = _bounded_llm(cmd, out)
                    if disc is None:
                        return None
                    imp = (disc.data or {}).get("import_name") or disc.name
                    # Suppress for BOTH repo modules and stem collisions: this tier
                    # auto-mints pkg: nodes, and a collision must never be auto-minted
                    # (typer's `items` is a real PyPI dist that must not be installed).
                    # Same suppression set as before — only its source of truth moved.
                    if classify_locality(imp or "", _ctx) is not Locality.EXTERNAL:
                        return None
                    return disc
```

Remove the now-unused `is_local_import` from the import at line 916 if nothing else in that scope uses it (`grep -n "is_local_import" src/envstate/orchestrator.py`).

- [ ] **Step 5: Re-point the stale monkeypatch in the existing guard test**

`tests/envstate/test_repair_routing.py:153-155` patches a seam the orchestrator no longer calls. Replace:

```python
    monkeypatch.setattr(
        scan_module, "local_module_names", lambda repo_path: frozenset({"docs_src"})
    )
```

with:

```python
    # The orchestrator now diagnoses against repo_modules (precise top-levels +
    # collisions), NOT scan.local_module_names (the over-broad construction set).
    monkeypatch.setattr(
        repo_modules_module, "top_level_names",
        lambda repo_path: frozenset({"docs_src"}),
    )
    monkeypatch.setattr(
        repo_modules_module, "stem_collisions", lambda repo_path: {}
    )
```

and add the import alongside the existing `scan_module` import at the top of the file:

```python
from python_deps.depgraph import repo_modules as repo_modules_module
```

Drop the now-unused `scan_module` import **only if** nothing else in the file uses it (`grep -n "scan_module" tests/envstate/test_repair_routing.py`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/envstate/test_repair_routing.py -q`
Expected: PASS — both `test_repo_internal_ref_bundle_skips_repair` (still gives up on a genuine repo module) and `test_stem_collision_bundle_spends_a_repair_turn` (now repairs `azure`)

- [ ] **Step 7: Verify the real-tree guard tests still pass UNTOUCHED**

Run: `python3 -m pytest tests/envstate/test_ingest_local_import_guard.py tests/envstate/scenarios/test_repo_local_import_guard.py -q`
Expected: PASS with **no edits to those files**. They build a real `tmp_path/docs_src/__init__.py` tree, so `top_level_names` must find `docs_src` for real. If either fails, the walk in Task 1 is wrong — fix `repo_modules.py`, not the test.

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/envstate/orchestrator.py tests/envstate/test_repair_routing.py
git commit -m "feat(orchestrator): diagnose on precise top-levels + collision evidence"
```

---

### Task 6: Plumb the collision evidence into the repair prompt (SAFETY-CRITICAL)

**Files:**
- Modify: `src/envstate/orchestrator.py` — `_repair_or_route` (the `run_structured_repair` call, ~line 787)
- Test: `tests/envstate/test_repair_routing.py`

**Interfaces:**
- Consumes: `classify_locality`, `Locality`, `RepoContext.collisions` (Task 3); the existing `constraints` parameter of `run_structured_repair`.
- Produces: no new symbols.

**Without this task, Tasks 1-5 are unsafe and the design's safety argument is fiction.**

`_repair_or_route` reads only `d.mode` — never `d.reason`. `run_structured_repair` passes `bundle` (raw command + stderr) to `build_repair_scope`, which builds a `RepairScope` with no knowledge of the diagnosis. `render_repair_scope` therefore shows the LLM a bare `ModuleNotFoundError: No module named 'items'` with **no signal that this is a stem collision**. And `patch_gate.validate_proposal` is purely *structural* — it checks id prefixes, layers, evidence refs, and that the check command is read-only. It has **no semantic check** that a proposed package is a real external distribution. A single `propose()` turn returning `pip install items` is admitted with zero errors.

So routing collisions to `AMBIGUOUS` without plumbing the evidence just swaps a silent give-up for a silent **wrong install** — the exact failure that killed two prior designs, relocated from the deterministic ladder to the LLM path.

The channel already exists and is unused: `run_structured_repair(..., constraints=None)` → `build_repair_scope(constraints=cons)` → `RepairScope.constraints` → `render_repair_scope` emits `"Constraints: k=v"`. `_repair_or_route` never passes it. That is exactly the right vehicle — a constraint *constrains what the LLM may propose*.

- [ ] **Step 1: Write the failing test**

Append to `tests/envstate/test_repair_routing.py`:

```python
def test_collision_evidence_reaches_the_repair_prompt(tmp_path, monkeypatch):
    """The LLM MUST be told the name collides with a repo file, or it will
    happily `pip install items` — a real PyPI package that must never be
    installed. patch_gate validates structure only; it will admit it."""
    from src.envstate.repair_scope import render_repair_scope

    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)

    (tmp_path / "docs_src" / "subcommands" / "tutorial001").mkdir(parents=True)
    (tmp_path / "docs_src" / "subcommands" / "tutorial001" / "__init__.py").write_text("")
    (tmp_path / "docs_src" / "subcommands" / "tutorial001" / "items.py").write_text("")

    seen_scopes = []

    class _ScopeCapturingAgent(_RecordingBuildAgent):
        def propose(self, scope, rejection_errors=None):
            seen_scopes.append(scope)
            return super().propose(scope, rejection_errors=rejection_errors)

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="python -m pytest -q", lineno=None,
            stderr="ModuleNotFoundError: No module named 'items'",
        )

    agent = _ScopeCapturingAgent()
    inputs = _base_inputs(agent, run_install_script, repo_path=str(tmp_path))
    orchestrator.run_v3(**inputs)

    assert seen_scopes, "propose was never called — the collision was dropped"
    rendered = render_repair_scope(seen_scopes[0])
    assert "tutorial001.items" in rendered, (
        "the repair prompt does not tell the agent that 'items' is a repo file; "
        "it will install the PyPI package `items`, which is WRONG"
    )
    assert "do not install" in rendered.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/envstate/test_repair_routing.py::test_collision_evidence_reaches_the_repair_prompt -q`
Expected: FAIL — `assert "tutorial001.items" in rendered`. The scope carries no collision signal.

- [ ] **Step 3: Write the implementation**

In `src/envstate/orchestrator.py`, inside `_repair_or_route`, immediately before the `run_structured_repair(...)` call, build the constraint and pass it:

```python
        # SAFETY: a stem collision is NOT decidable from the tree (see
        # repo_modules.stem_collisions). `azure` is a genuinely missing package;
        # `items` is a sys.path sibling AND a real PyPI distribution that must
        # never be installed. patch_gate validates STRUCTURE only -- it will admit
        # `pip install items` without complaint. The agent has the traceback (which
        # shows HOW the importer was loaded); give it the one fact the traceback
        # lacks -- that the repo defines a file by this name, and where.
        _cons: dict[str, str] = {}
        for _d in _diagnoses:
            _imp = (_d.discovery.data or {}).get("import_name") if _d.discovery else None
            _imp = _imp or _failed_import_name(bundle)
            if _imp and classify_locality(_imp, _ctx) is Locality.STEM_COLLISION:
                _real = _ctx.collisions[_imp.split(".", 1)[0]]
                _cons["local_module_collision"] = (
                    f"{_imp!r} is NOT an importable top-level module of this repo, but the "
                    f"repo DOES define the file {_real!r}. This is either a genuinely missing "
                    f"external package OR a sys.path/PYTHONPATH problem with a sibling import. "
                    f"DO NOT install a PyPI package named {_imp!r} unless the traceback shows "
                    f"the importer was loaded as an installed package (not run as a script)."
                )

        outcome = run_structured_repair(
            graph, failed_id, bundle, cycle,
            propose=..., emit=...,            # unchanged — keep the existing kwargs
            constraints=_cons or None,        # NEW
            target_hint=target_hint, cap_failed_id=cap_failed_id,
        )
```

`_diagnoses` is the existing `diagnose_all(...)` result already computed in `_repair_or_route` (the local that feeds `modes`). Add the small helper near `_repo_ctx`:

```python
    def _failed_import_name(bundle) -> str | None:
        """Top-level name from a ModuleNotFoundError in the bundle, if any."""
        from python_deps.failure_classifier import classify_dependency_failure
        for ev in (bundle.items if bundle is not None else ()):
            if ev.rc == 0:
                continue
            dep = classify_dependency_failure(ev.command, ev.output_excerpt or "")
            if dep.failure_type in ("module_not_found", "import_name_error"):
                return dep.import_name or None
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/envstate/test_repair_routing.py -q`
Expected: PASS

- [ ] **Step 5: Verify a genuine external repair is UNAFFECTED**

The constraint must appear only for collisions. Add:

```python
def test_plain_external_gets_no_collision_constraint(tmp_path, monkeypatch):
    from src.envstate.repair_scope import render_repair_scope

    monkeypatch.setattr(gs_module, "next_decision", _harmless_decision)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")

    seen = []

    class _Cap(_RecordingBuildAgent):
        def propose(self, scope, rejection_errors=None):
            seen.append(scope)
            return super().propose(scope, rejection_errors=rejection_errors)

    def run_install_script(script):
        return InstallResult(
            rc=1, failing_command="python -m pytest -q", lineno=None,
            stderr="ModuleNotFoundError: No module named 'requests'",
        )

    inputs = _base_inputs(_Cap(), run_install_script, repo_path=str(tmp_path))
    orchestrator.run_v3(**inputs)

    assert seen
    assert "local_module_collision" not in render_repair_scope(seen[0])
```

Run: `python3 -m pytest tests/envstate/test_repair_routing.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/envstate/orchestrator.py tests/envstate/test_repair_routing.py
git commit -m "fix(orchestrator): plumb stem-collision evidence into the repair prompt

Without this, routing a collision to AMBIGUOUS swaps a silent give-up for a
silent WRONG INSTALL: patch_gate validates structure only and will admit
'pip install items' (a real PyPI dist that must never be installed for typer)."
```

---

### Task 7: Real-repo regression tests

**Files:**
- Test: `tests/depgraph/test_repo_modules_real_repos.py`

**Interfaces:**
- Consumes: `top_level_names`, `stem_collisions` (Tasks 1-2)
- Produces: nothing.

These lock in the four cases every prior design got wrong. They skip cleanly when the checkouts are absent, so CI on a bare clone stays green.

- [ ] **Step 1: Write the test**

Create `tests/depgraph/test_repo_modules_real_repos.py`:

```python
"""Regression tests against REAL repo checkouts.

Each case is a bug a prior design shipped or nearly shipped. They skip when the
checkout is absent so a bare clone still passes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.repo_modules import stem_collisions, top_level_names

_SERVICES = Path("outputs/graph_fidelity/_smoke_services")
_LIBS = Path("outputs/build_script_eval/_smoke")


def _repo(base: Path, name: str) -> str:
    path = base / name
    if not path.is_dir():
        pytest.skip(f"{path} not checked out")
    return str(path)


def test_wagtail_azure_is_a_collision_not_a_repo_module():
    """THE bug: azure-mgmt-cdn is extras-gated and never installed, and the old
    broad rule called `azure` repo-local -> silent give-up, no repair."""
    repo = _repo(_SERVICES, "wagtail")
    assert "azure" not in top_level_names(repo)
    assert "azure" in stem_collisions(repo)
    assert "wagtail" in top_level_names(repo)


def test_typer_items_is_a_collision_not_an_external():
    """The inverse bug: `items` IS a real PyPI dist. Classifying it external
    lets Phase-A install it. The repo's own oracle
    (src/eval/graph_fidelity/ab_gold_labels.py:59-62) labels it "local"."""
    repo = _repo(_LIBS, "typer")
    collisions = stem_collisions(repo)
    for name in ("items", "lands", "reigns", "towns", "users"):
        assert name not in top_level_names(repo), f"{name} must not be a top-level"
        assert name in collisions, f"{name} must be a COLLISION, never a plain external"


def test_jupyterhub_traitlets_is_not_a_repo_module():
    """jupyterhub/traitlets.py is `jupyterhub.traitlets`; bare `import traitlets`
    is the PyPI package (declared, 24 importers)."""
    repo = _repo(_SERVICES, "jupyterhub")
    assert "traitlets" not in top_level_names(repo)
    assert "jupyterhub" in top_level_names(repo)


def test_netbox_core_apps_stay_local():
    """netbox has 1,184 .py files. An import-capped walk drops `extras` from the
    set -> classified external -> Phase-A installs the REAL PyPI `extras`.
    The walk must be uncapped."""
    repo = _repo(_SERVICES, "netbox")
    tops = top_level_names(repo)
    for app in ("extras", "dcim", "utilities", "circuits", "ipam"):
        assert app in tops, f"{app} is bare-importable under netbox/ and must be LOCAL"
    for collision in ("jinja2", "mptt", "markdown", "jsonschema"):
        assert collision not in tops, f"{collision} is a declared PyPI dist, not a top-level"


def test_precise_set_is_a_subset_of_the_broad_set():
    """The safety invariant: the new set is strictly NARROWER, so nothing that is
    local today becomes external tomorrow by accident.

    NOTE the non-emptiness assertion. `frozenset() <= anything` is trivially True,
    so a bug that made top_level_names() always return an empty set (e.g. broken
    repo-path plumbing) would pass a bare subset check on every repo with zero
    signal. Assert the set is populated AND that a known top-level is in it.
    """
    from python_deps.depgraph.scan import local_module_names

    expected = {"wagtail": "wagtail", "netbox": "dcim", "flask": "flask", "typer": "typer"}
    for base, name in ((_SERVICES, "wagtail"), (_SERVICES, "netbox"),
                       (_LIBS, "flask"), (_LIBS, "typer")):
        repo = _repo(base, name)
        precise = top_level_names(repo)
        assert precise, f"{name}: top_level_names is EMPTY — subset check would be vacuous"
        assert expected[name] in precise, f"{name}: missing known top-level"
        assert precise <= local_module_names(repo), name
```

- [ ] **Step 2: Make a fully-skipped run loud**

These five are the highest-value regressions in the plan — they lock in the four cases every prior design got wrong. But the checkouts live under `outputs/`, which is **gitignored**, and this project has **no hosted CI**. So on a fresh clone, a new contributor's machine, or an agent in an isolated worktree, all five `pytest.skip` quietly among hundreds of other tests and nobody notices.

Append to the same file:

```python
def test_at_least_one_real_repo_checkout_is_present():
    """Fail loudly rather than skipping the entire real-repo regression suite.

    If this fails, the checkouts are missing and the four regressions this file
    exists for did NOT run. Populate outputs/ or accept the gap knowingly —
    do not let it pass silently.
    """
    present = [
        p.name
        for base in (_SERVICES, _LIBS)
        if base.is_dir()
        for p in base.iterdir()
        if p.is_dir()
    ]
    assert present, (
        "no real repo checkouts under outputs/ — the wagtail/azure, typer/items, "
        "jupyterhub/traitlets and netbox/extras regressions did NOT run. These are "
        "the cases every prior design got wrong."
    )
```

- [ ] **Step 3: Run the tests**

Run: `python3 -m pytest tests/depgraph/test_repo_modules_real_repos.py -q -v`
Expected: PASS, 6 passed (**not** skipped — if you see skips, the checkouts are missing and the regressions did not run)

- [ ] **Step 4: Commit**

```bash
git add tests/depgraph/test_repo_modules_real_repos.py
git commit -m "test(depgraph): real-repo regressions — wagtail/azure, typer/items, netbox/extras"
```

---

### Task 8: Lock the construction boundary as a TEST, and update the spec

**Files:**
- Create: `tests/depgraph/test_construction_boundary.py`
- Modify: `docs/superpowers/specs/2026-07-13-local-module-resolution-fixes.md`

**Interfaces:** none.

- [ ] **Step 1: Make the construction boundary a permanent test, not a one-time grep**

The invariant "the precise set must never reach construction" is the single most safety-critical fact in this plan — violating it installs a wrong PyPI package. A `grep` an implementer runs once cannot stop a future unrelated change from reintroducing it. This codebase already has precedent for source-assertion tests (`tests/envstate/test_repair_routing.py::test_single_repair_call_site_and_no_block_emit_in_source` uses `inspect.getsource`).

Create `tests/depgraph/test_construction_boundary.py`:

```python
"""The precise (diagnosis-only) module set must NEVER reach construction.

`repo_modules.top_level_names` is NARROWER than `scan.local_module_names`. That is
correct for DIAGNOSIS (a false-local is a silent give-up) and CATASTROPHIC for
CONSTRUCTION (a false-external reaches Phase-A's identity candidate ladder, which
will ACCEPT and install an identically-named real PyPI distribution -- typer's
`items` and netbox's `extras` are both real packages).

This test is the guard rail. If it fails, do NOT relax it.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph import build, roots, scan

_FORBIDDEN = ("top_level_names", "stem_collisions", "repo_modules")


def test_construction_never_uses_the_diagnosis_only_precise_set():
    for module in (scan, build, roots):
        source = inspect.getsource(module)
        for name in _FORBIDDEN:
            assert name not in source, (
                f"{module.__name__} references {name!r}. The precise module set is "
                f"DIAGNOSIS-ONLY. Using it in construction makes Phase-A install a "
                f"wrong PyPI package (typer `items`, netbox `extras`)."
            )


def test_scan_to_nodes_still_uses_the_broad_set():
    """The conservative drop must stay conservative."""
    source = inspect.getsource(scan.scan_to_nodes)
    assert "_local_module_names(repo_path)" in source
```

- [ ] **Step 2: Run it**

Run: `python3 -m pytest tests/depgraph/test_construction_boundary.py -q`
Expected: PASS, 2 passed

- [ ] **Step 3: Prove `setup.sh` is byte-identical**

```bash
git diff main --stat -- src/python_deps/depgraph/scan.py
```
Expected: only the `SKIP_WALK_DIRS` rename (Task 1, Step 3). No change to `_local_module_names`, `scan_to_nodes`, or `_in_scope_files`.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS

> **Note on the baseline:** the working tree currently has ~58 pre-existing failures from unrelated in-flight WIP (`emit.py`, `resolve_link.py`, `resolve_lock.py`, `wheel_oracle.py`, `react_repair/*` are all modified). Capture the baseline **before** starting (`git stash && pytest tests/ -q | tail -3 && git stash pop`) and compare, or you will chase failures this plan did not cause.

- [ ] **Step 5: Rewrite the spec's design section**

The spec currently describes a superseded two-fix design (replace `scan._local_module_names` wholesale; exclude `NodeType.IMPORT` from `schedule._is_actionable`). Both were refuted. Replace §1 and §2 with the three-way design, and add a "Superseded within this document" note recording why:

- **Fix 1 (exclude `IMPORT` from `_is_actionable`) was dropped.** `orchestrator.py:1213-1251` routes tasks by `target_node_ids` into `run_structured_repair`, which hands the LLM a typed patch scope. It *can* propose `pip install azure-mgmt-cdn`. Excluding `IMPORT` would delete the strictly-more-capable repair channel for the exact case Fix 2 exists to serve. Also, the "documented live waste" cited from `orchestrator.py:1160` is the *rationale comment for the fast-termination fix* on the lines immediately below it — already mitigated.
- **Fix 2 (replace the broad set wholesale) was narrowed.** typer's `items` proves a precise set is *unsafe* in `scan_to_nodes`: `items` is a real PyPI distribution, and a false-external there reaches Phase-A's identity candidate ladder and installs it. Over-breadth is the correct conservative bias in construction. Only `diagnose` gets the precise set.

- [ ] **Step 6: Commit**

```bash
git add tests/depgraph/test_construction_boundary.py docs/superpowers/specs/2026-07-13-local-module-resolution-fixes.md
git commit -m "docs(spec)+test: three-way diagnosis supersedes the two-fix design; lock the construction boundary"
```

---

## Out of scope (deliberate)

- **`scan.scan_to_nodes` / `scan._local_module_names`.** Unchanged. Its over-breadth is the correct conservative bias — see Global Constraints.
- **`schedule._is_actionable`.** The `IMPORT` exclusion is dropped entirely (Task 8, Step 5). `orchestrator.py:1213-1251` routes tasks by `target_node_ids` into `run_structured_repair`, so the LLM *can* repair an Import obligation — excluding it would delete the more capable channel for exactly the case this plan serves.
- **`import_graph.collect_project_local_modules` / `SOURCE_ROOT_NAMES`.** Still used by `scan_imports` for its `project_local`/`stdlib`/`external` classification, which feeds `pkg_layer` and eval consumers. Deleting it is a separate change with its own blast radius.
- **`scan._in_scope_files`.** A behavior-changing drop filter; separate change, separate eval.
- **A `NodeType.MODULE` graph layer.** Withdrawn — see `docs/superpowers/specs/2026-07-13-module-node-layer-design.md`.

## Expected effect

**Unchanged (verified by review against the code):**
- `setup.sh`: **byte-identical.** Construction is untouched.
- Import node set: **unchanged.** `scan_to_nodes` still reads the same broad set.
- Eval metrics (`unresolved_imports`, root-selection A/B): **unchanged.** Both read construction output only; neither imports `diagnose`.
- **The runtime-ingest path is also unchanged.** `make_diagnostic_classifier` returns a `Discovery` only for `ENVIRONMENT`; both `REPO_INTERNAL_REF` and `AMBIGUOUS` return `None`, so `ingest_runtime_failures` mints no node either way. The fix operates **solely** through `_repair_or_route`, which reads `d.mode` directly. This is narrower than "collisions now reach the loop" might suggest.

**Changed — and it is more than one thing.** Where a collision previously hit `REPO_INTERNAL_REF` (`return graph`, a pure no-op), it now falls through to `run_structured_repair`. That differs in every one of these ways, all of which a reviewer should expect:
1. **LLM turns**: 0 → up to `MAX_REPAIRS_PER_BLOCK` (5) attempts × up to 2 `propose()` calls, drawn from the run-global `_repair_turns` budget (seeded from `max_cycles`, default 12).
2. **`_cycle_had_env_repair = True`** is set, which resets `_residual_stall` — the counter that would otherwise end the run via `GIVEUP_RESIDUAL`. A repeatedly-failing collision keeps the run alive longer than the old no-op did.
3. `PatchGateRecord` tracing fires (it did not before).
4. The graph, `manual_blocks`, and `known_invalid` can now be mutated on this path.
5. `_budget_exhausted` can now flip on this path.
6. The container can now be touched on this path.

**Budget risk, stated plainly.** netbox has **547** collision names, wagtail **753**. Those are *static exposure*, not incidence — a collision costs nothing unless it actually raises `ModuleNotFoundError` at runtime, and the smoke corpus surfaced exactly **one** (wagtail's `azure`). But there is **no collision-specific cap**: they compete for the same global `_repair_turns` budget as any real repair. A repo with several simultaneously-failing collisions (e.g. multiple extras-gated deps) could burn the run's repair budget on them. If that shows up, cap collision-driven repairs separately — do not widen the budget.

**Startup cost.** Task 5 runs three full `os.walk` traversals where there is one today (`top_level_names`, `stem_collisions`, and the broad walk `stem_collisions` calls). Measured: wagtail ~1.85s vs ~0.46s; netbox ~0.6s vs ~0.18s. One-time per run, not per cycle. If it matters, have `stem_collisions` return both sets from a single walk.

Because construction does not move, this can land **before** the gold-set rebuild rather than after it.
