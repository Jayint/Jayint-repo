# Dynamic Dependency Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two hardcoded name-mapping tables in the depgraph module (import→PyPI-dist and soname→apt) with certified/deterministic dynamic resolution that runs in the target container, keeping the curated tables only as an offline fallback.

**Architecture:** Two additive changes to the existing 5-stage pipeline (`scan → roots → resolve → seed → probe → certify`). (A) A new `apt_resolve` helper lets the probe stage fall back to `apt-file search` for sonames the curated table misses. (B) A new Stage 4a (`relink`) runs `importlib.metadata.packages_distributions()` in the container *after* the closure is installed and adds certified `Import→Package` edges that the pre-install heuristic missed. Both follow the repo's pure-parser-plus-thin-executor-orchestrator split, both return new immutable graphs, and neither flips a node's certification `state` (host still owns truth).

**Tech Stack:** Python 3.11, stdlib only (`json`, `re`, `os`, `shlex`), the existing `Executor` protocol, pytest with the in-repo `FakeExecutor` (no Docker/network in unit tests).

## Background

Validated empirically (2026-06-23, three Opus subagents running real Docker `debian:bookworm-slim` / `python:3.11-slim`):

- **`packages_distributions()`** reproduced all 12 curated import→dist rows post-install, is *more* accurate (reports the actually-installed variant), reads metadata so it works even when the C-extension import would fail, and auto-covers every other dist in the closure. Limitation: only sees **installed** dists → must run after `install_closure`. Namespace packages return a list (`google` → 4 dists). Keys are real module names (`PIL`, `MySQLdb`, `OpenSSL`) → normalize on lookup.
- **`apt-file search`** reproduced 10/10 curated sonames *with* a multiarch-path filter (raw `libgomp.so.1` alone returns ~40 cross-compile packages). It is offline once the index is baked into the base image. Tools/headers intentionally keep the curated table (it points at metapackages like `build-essential` that ship no files; `cc` is unresolvable by apt-file).
- **PyPI variant-guessing** for *pre-install* import→dist is unsafe alone (wrong on 10/12 collision cases — squatters/dead forks). Deferred to Future Work as propose-then-certify.

Design references: `docs/DESIGN-static-probe-certified-dependency-graph.md` (§4.4 probe, §3.1 certification invariant), `docs/IMPL-STATUS-depgraph.md`.

## File Structure

- **Create** `src/python_deps/depgraph/apt_resolve.py` — soname→apt resolution: curated table first, then `apt-file search` + multiarch-path filter. Pure parser (`parse_apt_file_search`) + executor orchestrator (`resolve_soname_apt`). One responsibility: turn a soname into an apt package name.
- **Create** `src/python_deps/depgraph/relink.py` — certified import→package linking from `packages_distributions()`. Pure parser (`parse_packages_distributions`) + pure edge builder (`import_to_package_edges`) + executor orchestrator (`certified_import_links`).
- **Modify** `src/python_deps/depgraph/probe.py` — `import_probe` uses `resolve_soname_apt(...)` instead of the pure `apt_for_soname(...)`; `_make_syslib_node` accepts the resolved apt.
- **Modify** `src/python_deps/depgraph/build.py` — insert Stage 4a (`certified_import_links`) between `install_closure` and `import_probe`.
- **Create** `tests/depgraph/test_apt_resolve.py`, `tests/depgraph/test_relink.py`.
- **Modify** `tests/depgraph/test_build.py` — add one focused wiring test.

## Global Constraints

- **Target Python: 3.11** (`packages_distributions()` requires 3.10+). Target OS: **Debian/Ubuntu** containers.
- **Repo immutability:** every "mutation" returns a NEW `DepGraph`/`Node`/`Edge`; never mutate inputs.
- **Pure-vs-executor split:** parsing logic is a pure function (no executor, unit-testable); the executor is touched only in a thin orchestrator.
- **Certification invariant (design §3.1):** discovery only. These tasks add edges and `fix_candidates` and `Attempt` records — they MUST NOT set a node `state` to `SATISFIED`. Only the host certifier (Stage 5) flips state.
- **Graceful degradation / never worse than today:** if `apt-file`, `gcc`, or the `packages_distributions` command fails or is absent, the function returns the graph unchanged (or `None` apt) exactly as the current hardcoded path would — no exceptions propagate out of a stage.
- **No new runtime dependencies:** stdlib only.
- **Keep the curated tables** (`tables.py`, `import_mapping.py`) as the offline fast-path / fallback — do not delete them.
- **Do NOT run an import-pruning lint autofix (ruff `F401` / `--fix`) between task commits.** The new modules `apt_resolve.py` and `relink.py` accumulate imports across tasks (Task 1's imports gain their use in Task 2; Task 4's in Tasks 5–6). Stripping a not-yet-used import after one task's commit will break the next task. Run lint once at the end, or use format-only autofix. (`pytest` — the only gate in this plan — is unaffected.)
- **Dynamic soname resolution is dormant until the base image ships `apt-file` + its Contents index.** Default slim images have neither, so `resolve_soname_apt` returns `("unresolved")` and falls back to today's table-only behavior — safe, but the apt-file path does not fire. Baking the index into the base image is Future Work; the relink (Feature B) needs no such prep and is live immediately.

---

## Task 1: apt-file output parser (pure)

**Files:**
- Create: `src/python_deps/depgraph/apt_resolve.py`
- Test: `tests/depgraph/test_apt_resolve.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces: `parse_apt_file_search(stdout: str, soname: str, triplet: str | None) -> str | None` — given raw `apt-file search <soname>` output, return the single apt package that ships exactly `/usr/lib/<triplet>/<soname>` (or, when `triplet is None`, exactly one multiarch dir under `/usr/lib`), preferring non-`-dev`/non-`-dbg`, then shortest name. `None` when nothing matches.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_apt_resolve.py
"""Unit tests for dynamic soname->apt resolution (no Docker/network)."""

from __future__ import annotations

from python_deps.depgraph.apt_resolve import parse_apt_file_search


def test_parse_filters_to_exact_multiarch_path():
    stdout = (
        "libgl1: /usr/lib/x86_64-linux-gnu/libGL.so.1\n"
        "primus-libs: /usr/lib/primus/libGL.so.1\n"
        "libgl1-mesa-dev: /usr/lib/x86_64-linux-gnu/libGL.so\n"
    )
    assert parse_apt_file_search(stdout, "libGL.so.1", "x86_64-linux-gnu") == "libgl1"


def test_parse_rejects_cross_compile_and_picks_runtime_over_dev():
    stdout = (
        "libgomp1: /usr/lib/x86_64-linux-gnu/libgomp.so.1\n"
        "libgomp1-amd64-cross: /usr/x86_64-linux-gnu/lib/libgomp.so.1\n"
        "libgomp1-dev: /usr/lib/x86_64-linux-gnu/libgomp.so.1\n"
    )
    assert parse_apt_file_search(stdout, "libgomp.so.1", "x86_64-linux-gnu") == "libgomp1"


def test_parse_no_triplet_accepts_single_multiarch_dir():
    stdout = "libpq5: /usr/lib/aarch64-linux-gnu/libpq.so.5\n"
    assert parse_apt_file_search(stdout, "libpq.so.5", None) == "libpq5"


def test_parse_returns_none_when_no_match():
    assert parse_apt_file_search("", "libGL.so.1", "x86_64-linux-gnu") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_apt_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.apt_resolve'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/apt_resolve.py
"""Dynamic soname -> apt resolution (curated table first, apt-file fallback).

Pure parser + thin executor orchestrator, mirroring resolve.py. The curated
``tables.apt_for_soname`` is the offline authority; ``apt-file search`` resolves
sonames the table does not know about. Build tools/headers deliberately stay on
the curated table (it encodes metapackages apt-file cannot return), so only the
soname path has a dynamic fallback. Debian/Ubuntu only.
"""

from __future__ import annotations

import os
import re
import shlex

from python_deps.depgraph.executor import Executor
from python_deps.depgraph.tables import apt_for_soname


def parse_apt_file_search(stdout: str, soname: str, triplet: str | None) -> str | None:
    """Pick the apt package that ships exactly the multiarch ``soname``.

    ``apt-file search`` does a substring match, so its output is noisy (subdirs,
    cross-compile dirs, ``-gdb.py`` autoload scripts). Keep only a line whose path
    is ``/usr/lib/<triplet>/<soname>`` (basename == soname exactly). When
    ``triplet`` is unknown, accept exactly one multiarch dir under ``/usr/lib``.
    Prefer a runtime package over ``-dev``/``-dbg``, then the shortest name.
    """
    candidates: list[str] = []
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        pkg, _, path = line.partition(":")
        pkg = pkg.strip()
        path = path.strip()
        if not pkg or os.path.basename(path) != soname:
            continue
        if triplet is not None:
            if path != f"/usr/lib/{triplet}/{soname}":
                continue
        elif not re.fullmatch(rf"/usr/lib/[^/]+/{re.escape(soname)}", path):
            continue
        if pkg not in candidates:
            candidates.append(pkg)
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.endswith("-dev"), p.endswith("-dbg"), len(p), p))
    return candidates[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_apt_resolve.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/apt_resolve.py tests/depgraph/test_apt_resolve.py
git commit -m "feat(depgraph): pure apt-file search parser for soname->apt"
```

---

## Task 2: soname→apt orchestrator (table-first, apt-file fallback)

**Files:**
- Modify: `src/python_deps/depgraph/apt_resolve.py`
- Test: `tests/depgraph/test_apt_resolve.py`

**Interfaces:**
- Consumes: `parse_apt_file_search` (Task 1); `tables.apt_for_soname`; `Executor.run`.
- Produces:
  - `multiarch_triplet(executor: Executor) -> str | None` — the container's multiarch triplet via Python's `sysconfig` (e.g. `"x86_64-linux-gnu"`), `None` on failure. Uses `sysconfig`, NOT `gcc -print-multiarch`, because gcc is absent in slim base images — exactly where the filter must still work.
  - `resolve_soname_apt(soname: str, executor: Executor) -> tuple[str | None, str]` — returns `(apt_pkg_or_None, source)` where `source` ∈ `{"table", "apt-file", "unresolved"}`. Curated table wins (no executor call); else `apt-file search` filtered by `parse_apt_file_search`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/depgraph/test_apt_resolve.py
from python_deps.depgraph.apt_resolve import multiarch_triplet, resolve_soname_apt


def test_resolve_known_soname_uses_table_without_executor(fake_executor):
    # libGL.so.1 is in the curated table -> resolve must NOT touch the executor.
    pkg, source = resolve_soname_apt("libGL.so.1", fake_executor)
    assert (pkg, source) == ("libgl1", "table")
    assert fake_executor.calls == []


def test_resolve_unknown_soname_falls_back_to_apt_file(fake_executor, make_result_fixture):
    fake_executor.responses = {
        "sysconfig": make_result_fixture(stdout="x86_64-linux-gnu\n"),
        "apt-file search": make_result_fixture(
            stdout="libfoo7: /usr/lib/x86_64-linux-gnu/libfoo.so.7\n"
        ),
    }
    pkg, source = resolve_soname_apt("libfoo.so.7", fake_executor)
    assert (pkg, source) == ("libfoo7", "apt-file")


def test_resolve_unknown_soname_unresolved_when_apt_file_missing(fake_executor):
    # Empty FakeExecutor -> apt-file search returns rc 127 (not ok) -> unresolved.
    pkg, source = resolve_soname_apt("libbar.so.9", fake_executor)
    assert pkg is None
    assert source == "unresolved"


def test_multiarch_triplet_none_when_probe_fails(fake_executor):
    assert multiarch_triplet(fake_executor) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_apt_resolve.py -k resolve -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_soname_apt'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/python_deps/depgraph/apt_resolve.py


# Multiarch triplet probe — uses Python's own sysconfig (always present in the
# target image) instead of ``gcc -print-multiarch``: gcc is absent in slim base
# images, which is exactly where the multiarch-path filter must still work.
_MULTIARCH_CMD = (
    "python -c \"import sysconfig; "
    "print(sysconfig.get_config_var('MULTIARCH') or '')\""
)


def multiarch_triplet(executor: Executor) -> str | None:
    """Container's multiarch triplet (``x86_64-linux-gnu``), or None on failure."""
    result = executor.run(_MULTIARCH_CMD)
    triplet = (result.stdout or "").strip() if result.ok else ""
    return triplet or None


def resolve_soname_apt(soname: str, executor: Executor) -> tuple[str | None, str]:
    """Resolve a ``.so`` soname to an apt package: table first, then apt-file.

    The curated table is authoritative and offline, so a hit short-circuits before
    any executor call. On a miss, query ``apt-file search`` in the container and
    filter to the exact multiarch path. Any failure (apt-file absent, no match)
    returns ``(None, "unresolved")`` — never worse than today's table-only path.
    """
    hit = apt_for_soname(soname)
    if hit:
        return hit, "table"
    triplet = multiarch_triplet(executor)
    result = executor.run(f"apt-file search {shlex.quote(soname)}")
    if not result.ok:
        return None, "unresolved"
    pkg = parse_apt_file_search(result.stdout, soname, triplet)
    if pkg:
        return pkg, "apt-file"
    return None, "unresolved"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_apt_resolve.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/apt_resolve.py tests/depgraph/test_apt_resolve.py
git commit -m "feat(depgraph): table-first soname->apt resolver with apt-file fallback"
```

---

## Task 3: wire dynamic apt resolution into the probe stage

**Files:**
- Modify: `src/python_deps/depgraph/probe.py` (imports; `import_probe` body; `_make_syslib_node` signature)
- Test: `tests/depgraph/test_probe.py`

**Interfaces:**
- Consumes: `resolve_soname_apt` (Task 2).
- Produces: unchanged public API (`install_closure`, `import_probe`). `import_probe` now sets a `SystemLib` node's `fix_candidates` from the dynamically-resolved apt package when the curated table misses. `_make_syslib_node(soname, stderr, command, apt=None)` — when `apt is None` it falls back to `apt_for_soname(soname)` (preserves existing callers/tests).

- [ ] **Step 1: Write the failing test**

`syslib_id`, `_import`, and all schema symbols are already imported at the top of `test_probe.py` (see lines 10-21) — add only the test function:

```python
# append to tests/depgraph/test_probe.py
def test_import_probe_unknown_soname_uses_apt_file_fallback(fake_executor, make_result_fixture):
    # An import whose runtime gap is a soname NOT in the curated table.
    imp = _import("widget")
    graph = DepGraph().with_node(imp)
    fake_executor.responses = {
        'python -c "import widget"': make_result_fixture(
            returncode=1,
            stderr="ImportError: libwidget.so.3: cannot open shared object file",
        ),
        "sysconfig": make_result_fixture(stdout="x86_64-linux-gnu\n"),
        "apt-file search": make_result_fixture(
            stdout="libwidget3: /usr/lib/x86_64-linux-gnu/libwidget.so.3\n"
        ),
    }

    out = import_probe(graph, fake_executor)

    lib = out.get(syslib_id("libwidget.so.3"))
    assert lib is not None
    assert lib.type is NodeType.SYSTEM_LIB
    assert lib.state is State.MISSING
    assert lib.fix_candidates == ("apt:libwidget3",)
```

Note: `NATIVE_LIBRARY_RE` (`python_deps/failure_classifier.py`) matches `libwidget.so.3` and captures it as named group `library`; the stderr MUST contain `cannot open shared object file` (the regex's tail alternation requires it), as the canned value above does. Known sonames in the existing probe tests (`libGL.so.1`, `libpq.so.5`) stay on the curated table, so `resolve_soname_apt` short-circuits with zero executor calls — those tests' `.calls`/`.timeouts` assertions are unaffected.

Design note (advisory divergence): for a soname NOT in the curated `NATIVE_LIB_TO_APT` table, the apt-file fallback may return a different apt package than a seed prediction derived from `PACKAGE_TO_SYSTEM_DEPS`. This does **not** create a new unreconciled node — the predicted-vs-observed id-space gap (`syslib_id(apt)` for predictions vs `syslib_id(soname)` for observations) is pre-existing and unchanged here — but the observed node's `fix_candidates` may suggest a different `apt:` package than a sibling prediction. That is acceptable for an advisory graph; deduping divergent fix advisories is Future Work.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_probe.py::test_import_probe_unknown_soname_uses_apt_file_fallback -v`
Expected: FAIL — `fix_candidates` is `()` (table miss → today no fallback), or node absent.

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/depgraph/probe.py`, add the import near the other depgraph imports (after the `tables` import block):

```python
from python_deps.depgraph.apt_resolve import resolve_soname_apt
```

In `import_probe`, replace these two lines:

```python
        apt = apt_for_soname(soname)
        predicted_id = syslib_id(apt) if apt else None
```

with:

```python
        apt, _apt_source = resolve_soname_apt(soname, executor)
        predicted_id = syslib_id(apt) if apt else None
```

and in the fresh-node branch of `import_probe`, change:

```python
            node = _make_syslib_node(soname, stderr, command)
```

to:

```python
            node = _make_syslib_node(soname, stderr, command, apt=apt)
```

Update `_make_syslib_node` to accept the resolved apt:

```python
def _make_syslib_node(soname: str, stderr: str, command: str, apt: str | None = None) -> Node:
    if apt is None:
        apt = apt_for_soname(soname)
    check = f"ldconfig -p | grep {soname}"
    node = Node(
        id=syslib_id(soname),
        type=NodeType.SYSTEM_LIB,
        name=soname,
        layer=Layer.SYSTEM,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        check_command=check,
        evidence=_first_line_with(stderr, soname),
        fix_candidates=(f"apt:{apt}",) if apt else (),
    )
    return node.with_attempt(
        Attempt(command=command, outcome="failed", check=check)
    )
```

- [ ] **Step 4: Run test to verify it passes (and no regressions)**

Run: `python -m pytest tests/depgraph/test_probe.py -v`
Expected: PASS — the new test plus all existing probe tests (known sonames like `libGL.so.1` hit the curated table, so `resolve_soname_apt` short-circuits and never calls the executor → behavior identical).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/probe.py tests/depgraph/test_probe.py
git commit -m "feat(depgraph): probe falls back to apt-file for unknown sonames"
```

---

## Task 4: packages_distributions output parser (pure)

**Files:**
- Create: `src/python_deps/depgraph/relink.py`
- Test: `tests/depgraph/test_relink.py`

**Interfaces:**
- Consumes: nothing (pure stdlib `json`).
- Produces:
  - `PACKAGES_DIST_CMD: str` — the container command emitting the JSON map.
  - `parse_packages_distributions(stdout: str) -> dict[str, list[str]]` — parse the JSON `{import_name: [dist, ...]}` map; return `{}` on any malformed input (graceful).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_relink.py
"""Unit tests for certified import->package relink (no Docker/network)."""

from __future__ import annotations

from python_deps.depgraph.relink import (
    PACKAGES_DIST_CMD,
    parse_packages_distributions,
)


def test_parse_valid_map():
    stdout = '{"cv2": ["opencv-python"], "yaml": ["PyYAML"], "google": ["google-auth", "protobuf"]}'
    out = parse_packages_distributions(stdout)
    assert out["cv2"] == ["opencv-python"]
    assert out["google"] == ["google-auth", "protobuf"]


def test_parse_malformed_returns_empty():
    assert parse_packages_distributions("not json") == {}
    assert parse_packages_distributions("") == {}
    assert parse_packages_distributions("[1, 2, 3]") == {}


def test_command_is_stdlib_only():
    assert "packages_distributions" in PACKAGES_DIST_CMD
    assert "importlib.metadata" in PACKAGES_DIST_CMD
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_relink.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.relink'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/relink.py
"""Stage 4a — certified Import->Package relink from packages_distributions().

After ``install_closure`` has installed the resolved closure, the container can
report the ground-truth import-name -> distribution map via
``importlib.metadata.packages_distributions()`` (Python 3.10+). This stage uses it
to add CERTIFIED ``Import->Package`` edges that the pre-install heuristic
(``resolve.link_imports_to_packages``) missed — e.g. ``import dateutil`` provided
by dist ``python-dateutil``. Discovery only: it adds edges, never node state.

Pure parser + pure edge builder + thin executor orchestrator (repo immutability:
every "mutation" returns a NEW ``DepGraph``).
"""

from __future__ import annotations

import json

from python_deps.depgraph.executor import Executor
from python_deps.depgraph.schema import DepGraph, Edge, EdgeType, NodeType
from python_deps.import_mapping import normalize_package_name, top_level_import_name

PACKAGES_DIST_CMD = (
    'python -c "import importlib.metadata, json; '
    'print(json.dumps(importlib.metadata.packages_distributions()))"'
)


def parse_packages_distributions(stdout: str) -> dict[str, list[str]]:
    """Parse the JSON ``{import_name: [dist, ...]}`` map; ``{}`` if malformed."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, val in data.items():
        if isinstance(key, str) and isinstance(val, list):
            out[key] = [v for v in val if isinstance(v, str)]
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_relink.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/relink.py tests/depgraph/test_relink.py
git commit -m "feat(depgraph): parse packages_distributions import->dist map"
```

---

## Task 5: certified Import→Package edge builder (pure)

**Files:**
- Modify: `src/python_deps/depgraph/relink.py`
- Test: `tests/depgraph/test_relink.py`

**Interfaces:**
- Consumes: `DepGraph`, `Node`, `Edge`, `NodeType`, `EdgeType`; `normalize_package_name`, `top_level_import_name`.
- Produces: `import_to_package_edges(graph: DepGraph, dist_map: dict[str, list[str]]) -> list[Edge]` — for every `Import` node, look up its top-level name (case-insensitively) in `dist_map`, and for each named dist that matches a `Package` node (by canonical name) emit `Edge(src=import_id, dst=pkg_id, REQUIRES, origin="certified")`. Skips edges that already exist. Namespace imports (multiple dists) link to every matching Package.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/depgraph/test_relink.py
from python_deps.depgraph.ids import import_id, package_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from python_deps.depgraph.relink import import_to_package_edges


def _imp(name):
    return Node(
        id=import_id(name), type=NodeType.IMPORT, name=name,
        layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN,
    )


def _pkg(name, version="1.0"):
    return Node(
        id=package_id(name, version), type=NodeType.PACKAGE, name=name,
        layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version=version,
    )


def test_edge_builder_links_unmapped_import():
    # Heuristic identity guess would say dateutil->dateutil and find no package;
    # packages_distributions says dateutil is provided by python-dateutil.
    graph = DepGraph().with_node(_imp("dateutil")).with_node(_pkg("python-dateutil", "2.9.0"))
    edges = import_to_package_edges(graph, {"dateutil": ["python-dateutil"]})
    assert len(edges) == 1
    e = edges[0]
    assert e.src == import_id("dateutil")
    assert e.dst == package_id("python-dateutil", "2.9.0")
    assert e.relation is EdgeType.REQUIRES
    assert e.origin == "certified"


def test_edge_builder_case_insensitive_module_key():
    # packages_distributions key is the real module name "PIL"; Import node too.
    graph = DepGraph().with_node(_imp("PIL")).with_node(_pkg("pillow", "10.3.0"))
    edges = import_to_package_edges(graph, {"PIL": ["pillow"]})
    assert len(edges) == 1
    assert edges[0].dst == package_id("pillow", "10.3.0")


def test_edge_builder_namespace_links_all_present_dists():
    graph = (
        DepGraph()
        .with_node(_imp("google"))
        .with_node(_pkg("google-auth", "2.0"))
        .with_node(_pkg("protobuf", "4.0"))
    )
    edges = import_to_package_edges(graph, {"google": ["google-auth", "protobuf", "google-api-core"]})
    dsts = {e.dst for e in edges}
    assert package_id("google-auth", "2.0") in dsts
    assert package_id("protobuf", "4.0") in dsts
    # google-api-core has no Package node in the closure -> no edge.
    assert len(edges) == 2


def test_edge_builder_skips_existing_edge():
    graph = (
        DepGraph()
        .with_node(_imp("yaml"))
        .with_node(_pkg("PyYAML", "6.0"))
    )
    graph = graph.with_edge(
        Edge(src=import_id("yaml"), dst=package_id("PyYAML", "6.0"),
             relation=EdgeType.REQUIRES, origin="reconcile")
    )
    edges = import_to_package_edges(graph, {"yaml": ["PyYAML"]})
    assert edges == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_relink.py -k edge_builder -v`
Expected: FAIL — `ImportError: cannot import name 'import_to_package_edges'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/python_deps/depgraph/relink.py


def import_to_package_edges(
    graph: DepGraph, dist_map: dict[str, list[str]]
) -> list[Edge]:
    """Certified Import->Package edges from a packages_distributions() map.

    Module keys are real names (``PIL``, ``MySQLdb``) so match case-insensitively;
    distribution names match a Package node by canonical (PEP 503) name. A
    namespace import (multiple dists) links to every dist that is present as a
    Package node. Edges already in the graph are skipped (no duplicates).
    """
    pkg_by_canon = {
        normalize_package_name(n.name): n.id
        for n in graph.nodes
        if n.type is NodeType.PACKAGE
    }
    dist_by_module = {module.lower(): dists for module, dists in dist_map.items()}
    existing = {
        (e.src, e.dst) for e in graph.edges if e.relation is EdgeType.REQUIRES
    }

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for node in graph.nodes:
        if node.type is not NodeType.IMPORT:
            continue
        module = top_level_import_name(node.name).lower()
        for dist in dist_by_module.get(module, ()):
            pkg_id = pkg_by_canon.get(normalize_package_name(dist))
            if pkg_id is None:
                continue
            key = (node.id, pkg_id)
            if key in existing or key in seen:
                continue
            seen.add(key)
            edges.append(
                Edge(
                    src=node.id,
                    dst=pkg_id,
                    relation=EdgeType.REQUIRES,
                    origin="certified",
                )
            )
    return edges
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_relink.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/relink.py tests/depgraph/test_relink.py
git commit -m "feat(depgraph): build certified import->package edges from dist map"
```

---

## Task 6: relink orchestrator (executor-backed)

**Files:**
- Modify: `src/python_deps/depgraph/relink.py`
- Test: `tests/depgraph/test_relink.py`

**Interfaces:**
- Consumes: `parse_packages_distributions` (Task 4), `import_to_package_edges` (Task 5), `Executor.run`, `PACKAGES_DIST_CMD`.
- Produces: `certified_import_links(graph: DepGraph, executor: Executor) -> DepGraph` — run `PACKAGES_DIST_CMD` in the container, parse, and add the certified edges. Returns the graph unchanged when the command fails (graceful).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/depgraph/test_relink.py
from python_deps.depgraph.relink import certified_import_links


def test_certified_import_links_adds_edge(fake_executor, make_result_fixture):
    graph = DepGraph().with_node(_imp("dateutil")).with_node(_pkg("python-dateutil", "2.9.0"))
    fake_executor.responses = {
        "packages_distributions": make_result_fixture(
            stdout='{"dateutil": ["python-dateutil"]}'
        )
    }

    out = certified_import_links(graph, fake_executor)

    deps = out.requires_of(import_id("dateutil"))
    assert any(d.id == package_id("python-dateutil", "2.9.0") for d in deps)


def test_certified_import_links_graceful_on_command_failure(fake_executor):
    # Empty FakeExecutor -> command returns rc 127 (not ok) -> graph unchanged.
    graph = DepGraph().with_node(_imp("dateutil")).with_node(_pkg("python-dateutil", "2.9.0"))
    out = certified_import_links(graph, fake_executor)
    assert out.edges == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_relink.py -k certified_import_links -v`
Expected: FAIL — `ImportError: cannot import name 'certified_import_links'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/python_deps/depgraph/relink.py


def certified_import_links(graph: DepGraph, executor: Executor) -> DepGraph:
    """Stage 4a: add certified Import->Package edges from the container.

    Runs ``packages_distributions()`` in the (post-install) container and links
    every Import to its certified provider Package. On command failure the graph
    is returned unchanged — never worse than the pre-install heuristic alone.
    """
    result = executor.run(PACKAGES_DIST_CMD)
    if not result.ok:
        return graph
    dist_map = parse_packages_distributions(result.stdout)
    new = graph
    for edge in import_to_package_edges(graph, dist_map):
        new = new.with_edge(edge)
    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_relink.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/relink.py tests/depgraph/test_relink.py
git commit -m "feat(depgraph): certified_import_links stage-4a orchestrator"
```

---

## Task 7: wire Stage 4a into the build pipeline

**Files:**
- Modify: `src/python_deps/depgraph/build.py` (import; insert call between `install_closure` and `import_probe`)
- Test: `tests/depgraph/test_build.py` (add one focused wiring test)

**Interfaces:**
- Consumes: `certified_import_links` (Task 6).
- Produces: unchanged `build_dep_graph` signature. After `install_closure`, before `import_probe`, the graph gains certified Import→Package edges so `import_probe`'s owner attribution walks the ground-truth links.

The end-to-end *edge-creation* logic is already fully covered by Task 6 (`certified_import_links`) and Task 5 (`import_to_package_edges`). This task only needs to prove Stage 4a is **wired into `build_dep_graph` at the right place** — which is robustly asserted by checking the `packages_distributions` command is actually invoked during a build, with no dependence on reproducing the uv-resolve canned responses.

- [ ] **Step 1: Write the failing test**

`build_dep_graph` is imported at `test_build.py:22` and `_r` is defined at `test_build.py:53`; only `FakeExecutor` needs the local `from conftest import` (matching the existing pattern at `test_build.py:67,207`).

```python
# append to tests/depgraph/test_build.py
def test_build_invokes_certified_relink_stage(tmp_path):
    """Stage 4a is wired: build runs the packages_distributions probe in the
    container, and it runs BEFORE the import probe."""
    from conftest import FakeExecutor  # type: ignore

    (tmp_path / "app.py").write_text("import dateutil\n")
    # Permissive executor: every command 'succeeds' with empty output, so the
    # pipeline runs end-to-end without crashing and we can inspect .calls.
    executor = FakeExecutor(default=_r(returncode=0))

    build_dep_graph(str(tmp_path), executor, host_executor=executor, target_python="3.11")

    relink_calls = [i for i, c in enumerate(executor.calls) if "packages_distributions" in c]
    assert relink_calls, "build_dep_graph must invoke the Stage 4a relink probe"
    relink_idx = relink_calls[0]
    import_idx = next(
        (i for i, c in enumerate(executor.calls) if 'python -c "import dateutil"' in c),
        10**9,
    )
    # Stage 4a must run before the import probe so its certified edges exist when
    # import_probe attributes gaps to owning packages.
    assert relink_idx < import_idx
```

Note: with an all-`rc0` executor and no `uv.lock` written, the resolve stage yields no Package nodes and `install_closure` returns early — but Stage 4a runs unconditionally, so the `packages_distributions` probe is recorded in `.calls` regardless. The test asserts only ordering (relink before import probe), so it is independent of resolver internals.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_build.py::test_build_invokes_certified_relink_stage -v`
Expected: FAIL — `packages_distributions` never appears in `executor.calls` (Stage 4a not wired).

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/depgraph/build.py`, add the import alongside the other stage imports:

```python
from python_deps.depgraph.relink import certified_import_links
```

In `build_dep_graph`, change the Stage 4 block from:

```python
    graph = install_closure(graph, container_executor)
    graph = import_probe(graph, container_executor)
```

to:

```python
    graph = install_closure(graph, container_executor)
    # Stage 4a — certified Import->Package relink (packages_distributions, CONTAINER).
    graph = certified_import_links(graph, container_executor)
    graph = import_probe(graph, container_executor)
```

- [ ] **Step 4: Run test to verify it passes (and no regressions)**

Run: `python -m pytest tests/depgraph/test_build.py -v`
Expected: PASS — the new wiring test plus the existing end-to-end test (its `FakeExecutor` has no `packages_distributions` response, so the Stage 4a command returns rc 127 / not-ok → graph unchanged → no regression).

- [ ] **Step 5: Run the full depgraph suite**

Run: `python -m pytest tests/depgraph/ -v`
Expected: PASS — all prior tests plus the new `test_apt_resolve.py`, `test_relink.py`, and the two new probe/build tests.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_build.py
git commit -m "feat(depgraph): wire stage-4a certified relink into build pipeline"
```

---

## Manual verification (optional, real Docker)

After the unit suite is green, sanity-check against a real container (not part of CI; mirrors the validation runs):

```bash
# In a python:3.11-slim container with apt-file installed + indexed:
python -c "import importlib.metadata, json; print(json.dumps(importlib.metadata.packages_distributions()))"
apt-get install -y apt-file && apt-file update          # ~12s, ~76MB index (bake into base image)
apt-file search libGL.so.1                              # -> libgl1: /usr/lib/<triplet>/libGL.so.1
```

Confirm `resolve_soname_apt` returns the table value for known sonames (no executor call) and an apt-file value for an unknown one, and that `certified_import_links` adds edges for closure dists whose import name differs from the dist name.

## Future Work (not in this plan)

- **Pre-install import→dist (PyPI propose-then-certify).** For bare `import X` with no manifest entry and not yet installed, generate variant candidates (`{X}`, `{X}-python`, `py{X}`) against the PyPI JSON API, then **confirm** by reading the candidate wheel's `top_level.txt` / `RECORD` over HTTP Range before trusting it (variant-guessing alone is wrong ~10/12 on collision cases). Runs host-side.
- **Persisted resolution cache** (`pypi_mapping_cache.json`-style) so live PyPI/apt-file lookups stay deterministic across runs (pairs with `exclude_newer` reproducibility).
- **Bake the apt-file Contents index into the base image** so soname resolution is fully offline at run time.
- **Promote edge provenance**: `origin="certified"` is a new value beyond `Edge.origin`'s documented `scan|resolver|probe|runtime` set. `with_edge` does not validate `origin` and `export.py` treats unknown origins generically, so it is safe today, but consider (a) extending the documented set in `schema.py`, and (b) optionally letting a `"certified"` edge replace a same-key `"reconcile"`/`"resolver"` edge (currently `with_edge` keeps the first by key; functionally identical dst, so this is cosmetic provenance only).

## Done Criteria

- `tests/depgraph/` fully green, including `test_apt_resolve.py`, `test_relink.py`, the new probe test, and the new build wiring test.
- Known sonames still resolve via the curated table with zero executor calls; unknown sonames resolve via apt-file; both degrade to today's behavior on failure.
- `build_dep_graph` adds certified Import→Package edges for closure dists whose import name differs from the dist name, with no regression to the existing end-to-end test.
- Certified relink and apt-file fallback add edges / `fix_candidates` only — no node `state` is set by these stages (certification invariant intact).
