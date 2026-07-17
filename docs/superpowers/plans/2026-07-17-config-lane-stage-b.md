# Config Lane — Stage B (Build the Machinery in Shadow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **⛔ EXECUTION GATE — DO NOT START THIS PLAN UNTIL BOTH ARE TRUE:**
> 1. **Gate A = GO** (Stage A Task 1, `2026-07-17-config-lane-stage-a.md`): editable-install + rootdir materially lifts `pytest --collect-only` on the pilots. If Gate A is NO-GO, the config lane is cancelled and this plan is discarded.
> 2. **Stage A Task 2 (MODULE rename) and Task 3 (TestEnvPlan cwd+env) have landed.** Task 3 below (classify.py) emits `NodeType.MODULE`; Task 2 below (TestEnvPlan completion) extends the Stage-A `cwd`/`env` fields. Executing Stage B before these is a type error.

**Goal:** Build the entire config-lane machinery — repo-mount primitive, canonical `TestEnvPlan`, pure classifier, in-container cure runner, arbitration, lane-aware fixpoint — as a **shadow pass** that computes and is measured but does not affect the real graph, so Gate B can measure partition sanity and false-green rate before Stage C flips route-not-drop.

**Architecture:** One invariant governs every task: **each Stage B commit is provably behavior-preserving on real construction.** The new machinery runs only inside a *discarded shadow pass*, or its new code paths see vacuous inputs, because `scan` still drops first-party imports until Stage C. The pass that emits diagnostics in Stage B is the *same code* Stage C will flip to "wired." The per-task gate is therefore "the `tests/depgraph/` suite stays green AND (where the task touches a live code path) a byte-identical assertion holds"; the whole-lane pass-repo sweep runs at Gate B.

**Tech Stack:** Python 3.11+, pytest, Docker (for the cure runner / Gate B measurement), the existing `depgraph/` construction pipeline, `invocation_resolver`, `repo_modules`, `relink`, `repair`.

## Global Constraints

- **`python_deps/*` stays LLM-free.** The config lane is entirely static + certified: the classifier is a pure ladder, the arbitration is an import-probe. No LLM anywhere in this plan.
- **Behavior-preserving invariant (THE per-task gate).** Every task must leave real `build_dep_graph` output byte-identical on the pass-repos. Concretely: the shadow flag defaults OFF; new modules are called only by the shadow pass; edits to live functions (`_phase_a_fixpoint`, `populate_setup_commands`) must be vacuous when their new inputs are empty/absent. Each task proves this with a targeted test.
- **Scoped commits ONLY** (shared branch, parallel commits): `git add <exact paths>`; **`-m` before `--`**; never `git add -A`; no `Co-Authored-By` trailer.
- **Reuse, don't rebuild.** Mount = the `_MountedContainer` shape (`src/eval/language_package_eval/coverage.py:556`). Config plan = `invocation_resolver`. Local modules = `repo_modules`. Import→Package = `relink`. Grounding = `repair`. Do not write parallel copies.
- **Do not change the install lane or the native overlay.** The classifier's clear-external partition feeds the existing fixpoint unchanged; Phase B (relink/ldd/import_probe) is re-run over fallthroughs, not modified.
- **The false-green vector is the enemy** (`self-install-false-green-vector`): a collision name installs its PyPI namesake ONLY IF the cure succeeded AND the canonical-plan probe shows it does not resolve locally. Cure failure ⇒ deferred collisions stay unresolved (honest RED).

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/python_deps/depgraph/executor.py` (modify) | `DockerExecutor` gains an optional repo bind-mount | 1 |
| `src/python_deps/depgraph/invocation_resolver.py` (modify) | one canonical reader drives both path + env; PYTHONPATH sourced fully; absolute cwd | 2 |
| `src/python_deps/depgraph/classify.py` (create) | pure lane classifier: ladder, relocated drops, Module emission, deferred set as data | 3 |
| `src/python_deps/depgraph/classify.py` (extend) | PEP 420 namespace-root handling → collision zone | 4 |
| `src/python_deps/depgraph/cure.py` (create) | in-container editable-install + build-isolation fallback chain + collect-gate + scratch-certified stamp | 5 |
| `src/python_deps/depgraph/populate.py` (modify) | poison gate: skip when a scratch-certified state exists | 5 |
| `src/python_deps/depgraph/arbitrate.py` (create) | exception-aware per-name probe under the plan; gated on cure success; fallthrough candidates | 6 |
| `src/python_deps/depgraph/build.py` (modify) | lane-aware `missing` filter + `deferred` param + threaded fallthrough re-entry | 7 |
| `src/python_deps/depgraph/shadow.py` (create) | wire 3→5→6 as one flagged pass; emit per-repo diagnostic record; discard graph effect | 8 |
| `src/python_deps/depgraph/build.py` (modify) | call the shadow pass at the `_python_package_obligations` tail behind the flag | 8 |
| `bench/schema.py` + `bench/metrics.py` (modify) | provisional-collision flag → "certified-with-provisional" bucket; never a clean EBSR | 9 |
| `scripts/gate_b_partition_sanity.py` (create) | corpus aggregator over the shadow records; Gate B go/no-go | 10 |

**Dependency order:** 1 (mount) and 2 (plan) are independent; 3→4 (classifier); 5 (cure) needs 1+2; 6 (arbitrate) needs 2+3+5; 7 (fixpoint) is independent-pure; 8 (shadow) wires 3+5+6+7; 9 reads 6's flag; 10 runs 8. Execute in numeric order.

---

### Task 1: Repo-mount primitive on `DockerExecutor`

The scratch container mounts only cache volumes (`executor.py:107-112`) — no repo source — so `pip install -e .` cannot run in it today. Add an optional bind-mount, following the `_MountedContainer` precedent, additive and off by default.

**Files:**
- Modify: `src/python_deps/depgraph/executor.py` (`__init__` `:86-101`, `_run_command` `:103-112`)
- Test: `tests/depgraph/test_executor.py` (create if absent)

**Interfaces:**
- Consumes: nothing new.
- Produces: `DockerExecutor(image, *, ..., mount_repo: str | None = None, repo_mount_dir: str = "/workspace/repo")`; when `mount_repo` is set, `_run_command()` includes `-v {abspath}:{repo_mount_dir}`, and the instance exposes `.repo_mount_dir`. When `mount_repo is None`, `_run_command()` is byte-identical to today.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_executor.py
from python_deps.depgraph.executor import DockerExecutor


def test_run_command_without_mount_is_unchanged():
    ex = DockerExecutor("python:3.11-slim")
    cmd = ex._run_command()
    assert "sleep infinity" in cmd
    assert "-v " not in cmd or "cache" in cmd  # no repo mount when mount_repo is None


def test_run_command_with_mount_binds_repo(tmp_path):
    ex = DockerExecutor("python:3.11-slim", mount_repo=str(tmp_path))
    cmd = ex._run_command()
    assert f"-v {tmp_path.resolve()}:/workspace/repo" in cmd
    assert ex.repo_mount_dir == "/workspace/repo"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_executor.py -v`
Expected: FAIL — `DockerExecutor.__init__` has no `mount_repo` keyword.

- [ ] **Step 3: Add the parameter and the mount**

In `executor.py`, extend `__init__` (after `cache_volumes: bool = False,`):

```python
        mount_repo: str | None = None,
        repo_mount_dir: str = "/workspace/repo",
    ) -> None:
        ...
        self.cache_volumes = cache_volumes
        self.mount_repo = mount_repo
        self.repo_mount_dir = repo_mount_dir
```

In `_run_command`, add the repo bind next to the cache volumes (absolute host path, per the `_MountedContainer` precedent which uses `os.path.abspath`):

```python
    def _run_command(self) -> str:
        net = "" if self.network else "--network none "
        plat = f"--platform {self.platform} " if self.platform else ""
        vols = (
            "-v jayint_uv_cache:/root/.cache/uv -v jayint_pip_cache:/root/.cache/pip "
            if self.cache_volumes
            else ""
        )
        repo = (
            f"-v {Path(self.mount_repo).resolve()}:{self.repo_mount_dir} "
            if self.mount_repo
            else ""
        )
        return f"docker run -d {net}{plat}{vols}{repo}--name {self._name} {self.image} sleep infinity"
```

Add `from pathlib import Path` to the imports if not present.

- [ ] **Step 4: Run the test + the depgraph suite**

Run: `python -m pytest tests/depgraph/test_executor.py tests/depgraph/ -q`
Expected: PASS. The default (`mount_repo=None`) keeps `_run_command` byte-identical — the behavior-preserving gate for this task.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/executor.py tests/depgraph/test_executor.py
git commit -m "feat(depgraph): DockerExecutor optional repo bind-mount for the in-container cure" -- src/python_deps/depgraph/executor.py tests/depgraph/test_executor.py
```

---

### Task 2: Complete the canonical `TestEnvPlan` (one reader, full PYTHONPATH, absolute cwd)

Stage A gave `TestEnvPlan` `cwd`/`env` fields but left the env reader **root-only** while the path reader searches `["."] + project_dirs` (`invocation_resolver._discover_pytest_config:543-557`). For the collect-gate and the per-name probe to never diverge, one reader must drive both halves over the same file set.

**Files:**
- Modify: `src/python_deps/depgraph/invocation_resolver.py` (`resolve` `:113`, add env/pythonpath assembly)
- Reuse (read): `src/python_deps/depgraph/config_scan.py` (`scan_authoritative_config`, `authoritative_ambiguous_vars`)
- Test: `tests/depgraph/test_invocation_resolver.py`

**Interfaces:**
- Consumes: `config_scan.scan_authoritative_config(dir)`, `config_scan.authoritative_ambiguous_vars(dir)` (existing).
- Produces: `resolve()` now sources env-vars and `PYTHONPATH` from **the same dir the path-config was found in** (rootdir), not the repo root; `TestEnvPlan.pythonpath` includes tox `setenv PYTHONPATH` entries and the src-layout root; `TestEnvPlan.cwd` is the rootdir (relative — absolute materialization stays the cure-runner's job, Task 5).

- [ ] **Step 1: Write the failing test (env scoped to the config dir, PYTHONPATH sourced from setenv)**

```python
def test_env_and_pythonpath_scoped_to_config_dir(tmp_path):
    # feast-style: the authoritative config lives in a subdir, not the repo root.
    sdk = tmp_path / "sdk" / "python"
    sdk.mkdir(parents=True)
    (sdk / "pyproject.toml").write_text("[build-system]\nrequires=['setuptools']\n")
    (sdk / "tox.ini").write_text(
        "[pytest]\n"
        "[testenv]\nsetenv =\n"
        "    DJANGO_SETTINGS_MODULE=app.settings\n"
        "    PYTHONPATH=src\n"
    )
    from python_deps.depgraph.invocation_resolver import resolve
    plan = resolve(str(tmp_path))
    assert plan.rootdir == "sdk/python"
    assert ("DJANGO_SETTINGS_MODULE", "app.settings") in plan.env  # found in the subdir, not root
    assert "src" in plan.pythonpath                                 # tox setenv PYTHONPATH sourced
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_invocation_resolver.py::test_env_and_pythonpath_scoped_to_config_dir -v`
Expected: FAIL — env is read from the repo root (empty here), and `PYTHONPATH` from tox `setenv` is not sourced.

- [ ] **Step 3: Scope env-var discovery to the config dir**

In `resolve()` (`:113-146`), after `config = _discover_pytest_config(root, project_dirs)`, resolve the env from the **config's own directory** (rootdir), reusing the Stage-A wiring but over `_project_path(root, config["rootdir"])`:

```python
    config = _discover_pytest_config(root, project_dirs)
    config_dir = _project_path(root, config["rootdir"])
    pythonpath = _merge_pythonpath(config["pythonpath"], config_dir, root, project_dirs)
    env = _authoritative_env(config_dir)
    ...
    return TestEnvPlan(
        ...
        rootdir=config["rootdir"],
        pythonpath=pythonpath,
        cwd=config["rootdir"],
        env=env,
        ...
    )
```

Add the two helpers:

```python
def _authoritative_env(config_dir: Path) -> tuple[tuple[str, str], ...]:
    """Unambiguous authoritative env-vars, read from the SAME dir the pytest
    config was found in (rootdir) — not the repo root, so a feast-style
    ``sdk/python/tox.ini setenv`` is not missed. Ambiguous vars are dropped."""
    from python_deps.depgraph.config_scan import (
        scan_authoritative_config, authoritative_ambiguous_vars,
    )
    ambiguous = authoritative_ambiguous_vars(str(config_dir))
    return tuple(sorted(
        (k, v) for k, v in scan_authoritative_config(str(config_dir)).items()
        if k not in ambiguous
    ))


def _merge_pythonpath(
    ini_pythonpath: tuple[str, ...], config_dir: Path, root: Path,
    project_dirs: tuple[str, ...],
) -> tuple[str, ...]:
    """Union of: the pytest ``pythonpath`` ini option; tox ``setenv PYTHONPATH``
    entries; and the src-layout root (``<project>/src``) when present. Order-
    preserving, de-duplicated. Editable-install roots are added by the cure
    runner, not here."""
    out: list[str] = list(ini_pythonpath)
    for _var, value in _authoritative_env(config_dir):
        if _var == "PYTHONPATH":
            out.extend(p for p in value.split(os.pathsep) if p)
    for rel in (["."] + [d for d in project_dirs if d != "."]):
        src = _project_path(root, rel) / "src"
        if src.is_dir():
            out.append("src" if rel == "." else f"{rel}/src")
    seen: set[str] = set()
    return tuple(p for p in out if not (p in seen or seen.add(p)))
```

(`config_scan` verified to read `PYTHONPATH` as a `setenv` var; `_authoritative_env` is reused so the value comes from the same precedence.)

- [ ] **Step 4: Run the tests + the depgraph suite**

Run: `python -m pytest tests/depgraph/test_invocation_resolver.py tests/depgraph/ -q`
Expected: PASS. `TestEnvPlan` has **zero real-construction consumers** (verified: no non-test importer of `invocation_resolver` in `src`), so this is behavior-preserving by construction — the byte-identical gate is trivially met.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/invocation_resolver.py tests/depgraph/test_invocation_resolver.py
git commit -m "feat(depgraph): TestEnvPlan sources env + PYTHONPATH from the rootdir (one canonical reader)" -- src/python_deps/depgraph/invocation_resolver.py tests/depgraph/test_invocation_resolver.py
```

---

### Task 3: `classify.py` — the pure lane classifier (core ladder)

A pure Stage-2.5 pass over the scanned imports. Routes each top-level import name to internal (local `Module`) / external (install lane) / collision-zone (deferred). Sole sanctioned consumer of `repo_modules`/`stem_collisions`. No container, no LLM. The target-stdlib set is **injected** (probed from the container by the caller) so the classifier stays pure while using the *target* interpreter's stdlib (never a host fallback).

**Files:**
- Create: `src/python_deps/depgraph/classify.py`
- Reuse (read): `repo_modules.top_level_names/stem_collisions/repo_modules`, `scan.scan_imports/_in_scope_files/_is_excluded_path`, `schema.Node/NodeType/Edge/Layer/DiscoveredBy`
- Test: `tests/depgraph/test_classify.py`

**Interfaces:**
- Consumes: `scan.scan_imports(repo_path) -> (findings, _local, _errors)` where each finding has `.import_name/.classification/.source_files/.optional/.symbols`; `repo_modules.top_level_names(repo_path)`, `repo_modules.stem_collisions(repo_path)`; `repo_modules.repo_modules(repo_path)` for Module evidence.
- Produces:
  ```python
  @dataclass(frozen=True)
  class LaneRouting:
      internal: tuple[tuple[str, str], ...]   # (import top-level name, local dotted module)
      external: frozenset[str]                 # import names bound for the install lane
      deferred: frozenset[str]                 # collision-zone names, arbitrated post-cure
      modules: tuple[Node, ...]                # NodeType.MODULE nodes (identity = top-level name)
  def probe_target_stdlib(executor) -> frozenset[str]   # container one-shot; caller passes result in
  def classify(repo_path, *, target_stdlib, declared) -> LaneRouting
  def apply_routing(graph, routing) -> DepGraph          # emit Module nodes (spine wiring is Stage C)
  ```

- [ ] **Step 1: Write the failing test (the ladder partitions three ways)**

```python
# tests/depgraph/test_classify.py
from python_deps.depgraph.classify import classify
from python_deps.depgraph.schema import NodeType


def test_ladder_partitions_internal_external_collision(tmp_path):
    # A local package `mypkg` (sys.path-accurate top-level), a clear external
    # `requests`, and a collision `items` that is BOTH a local module AND a real
    # PyPI dist (stem_collisions surfaces it).
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "app.py").write_text(
        "import requests\nimport items\nfrom mypkg import helpers\n"
    )
    (tmp_path / "mypkg" / "helpers.py").write_text("")
    (tmp_path / "items.py").write_text("")  # sibling script → collision with PyPI `items`

    routing = classify(str(tmp_path), target_stdlib=frozenset({"os", "sys"}), declared=frozenset())
    internal_names = {name for name, _dotted in routing.internal}
    assert "mypkg" in internal_names            # sys.path-accurate top-level → internal
    assert "requests" in routing.external       # not local, not stdlib → external
    assert "items" in routing.deferred          # local module AND PyPI dist → deferred
    assert {n.type for n in routing.modules} == {NodeType.MODULE}


def test_declared_name_never_internal(tmp_path):
    (tmp_path / "requests").mkdir()             # a repo dir shadowing a declared dep name
    (tmp_path / "requests" / "__init__.py").write_text("")
    routing = classify(str(tmp_path), target_stdlib=frozenset(), declared=frozenset({"requests"}))
    assert "requests" in routing.external       # declared wins rung 1 → external, never internal
    assert "requests" not in {n for n, _ in routing.internal}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_classify.py -v`
Expected: FAIL — `classify` does not exist.

- [ ] **Step 3: Write the classifier**

```python
# src/python_deps/depgraph/classify.py
"""Pure static lane classifier for the two-lane collection graph.

For each scanned top-level import name, a sys.path-accurate ladder routes it:
  1. declared in a manifest        -> external (install lane); you never declare
                                       your own modules.
  2. in the TARGET interpreter's stdlib -> drop.
  3. in the repo's sys.path-accurate top-level set -> internal (config lane; a
                                       local Module node).
  4. otherwise                     -> external candidate (install lane).

The residue that is BOTH a repo module AND a real PyPI dist (``stem_collisions``)
is NOT statically decidable and is routed to the collision zone (deferred),
arbitrated only post-cure by ``arbitrate.py``. Excluded-dir-only locals
(examples/scripts/tools) also route to the collision zone, never clear-external,
because ``SKIP_WALK_DIRS`` hides them from both ``top_level_names`` and
``stem_collisions`` (review §12).

Pure: no container, no execution, no LLM. Sole sanctioned consumer of
``repo_modules``/``stem_collisions``. The ``target_stdlib`` set is injected by the
caller (a one-shot container probe) so this stays pure while using the TARGET's
stdlib, never a host fallback (review §17).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from python_deps.depgraph.repo_modules import (
    repo_modules, stem_collisions, top_level_names,
)
from python_deps.depgraph.scan import (
    _is_excluded_path, import_id, scan_imports,
)
from python_deps.depgraph.schema import (
    DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType,
)


@dataclass(frozen=True)
class LaneRouting:
    internal: tuple[tuple[str, str], ...]
    external: frozenset[str]
    deferred: frozenset[str]
    modules: tuple[Node, ...]


_STDLIB_PROBE = (
    "python3 -c \"import sys,json;"
    "print(json.dumps(sorted(getattr(sys,'stdlib_module_names',()) "
    "or sys.builtin_module_names)))\""
)


def probe_target_stdlib(executor) -> frozenset[str]:
    """One-shot: the TARGET container's own stdlib module names. Uses
    ``sys.stdlib_module_names`` (3.10+); falls back to ``builtin_module_names``
    on 3.9. Never a host fallback — the executor is the target."""
    result = executor.run(_STDLIB_PROBE, timeout=60)
    if not result.ok:
        return frozenset()
    try:
        return frozenset(json.loads(result.stdout.strip()))
    except (ValueError, TypeError):
        return frozenset()


def _module_node(top: str, dotted_paths: tuple[tuple[str, str], ...]) -> Node:
    """A top-level local Module node. Evidence is the tuple of (sys_path_root,
    path) pairs (JSON) so two dirs each defining ``utils`` don't collapse into a
    false single-provider node (review §14)."""
    return Node(
        id=f"module:{top}",
        type=NodeType.MODULE,
        name=top,
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.CLASSIFIER,
        evidence=json.dumps(sorted(dotted_paths)),
    )


def classify(repo_path: str, *, target_stdlib: frozenset[str], declared: frozenset[str]) -> LaneRouting:
    findings, _local, _errors = scan_imports(repo_path)
    tops = top_level_names(repo_path)
    collisions = frozenset(stem_collisions(repo_path))
    declared_norm = frozenset(d.lower().replace("-", "_") for d in declared)

    # Module-node evidence: group repo modules by their top-level name, keeping
    # both the (sys_path_root, path) evidence pairs and the dotted names.
    by_top: dict[str, list[tuple[str, str]]] = {}
    dotted_by_top: dict[str, list[str]] = {}
    for mod in repo_modules(repo_path):
        top = mod.dotted.split(".", 1)[0]
        by_top.setdefault(top, []).append((mod.sys_path_root, mod.path))
        dotted_by_top.setdefault(top, []).append(mod.dotted)

    internal: list[tuple[str, str]] = []
    external: set[str] = set()
    deferred: set[str] = set()
    internal_tops: set[str] = set()

    for finding in findings:
        name = finding.import_name
        top = name.split(".", 1)[0]
        if top.startswith("_"):
            continue                                   # relocated drop: private/typing
        if top in collisions:
            deferred.add(top)                          # collision zone (rung 3.5)
            continue
        if top in declared_norm:
            external.add(name)                         # rung 1: declared → external
            continue
        if top in target_stdlib:
            continue                                   # rung 2: stdlib → drop
        if top in tops:
            internal_tops.add(top)                     # rung 3: sys.path-accurate → internal
            continue
        # excluded-dir-only locals are invisible to tops AND collisions: route
        # to the collision zone, never clear-external (review §12).
        in_scope = tuple(f for f in finding.source_files if not _is_excluded_path(f))
        if finding.source_files and not in_scope and top in by_top:
            deferred.add(top)
            continue
        external.add(name)                             # rung 4: external

    for top in sorted(internal_tops):
        dotteds = dotted_by_top.get(top, [])
        internal.append((top, min(dotteds) if dotteds else top))  # lexicographically-first dotted
    modules = tuple(_module_node(top, tuple(by_top.get(top, ()))) for top in sorted(internal_tops))
    return LaneRouting(
        internal=tuple(internal),
        external=frozenset(external),
        deferred=frozenset(deferred),
        modules=modules,
    )


def apply_routing(graph, routing: LaneRouting):
    """Emit the Module nodes onto a graph. The spine wiring (project→module→
    import replacing the flat Test→Import hub) is the Stage C flip; here we only
    add the Module nodes so the shadow pass can measure them."""
    new = graph
    for node in routing.modules:
        new = new.with_node(node)
    return new
```

- [ ] **Step 4: Run the test + the depgraph suite**

Run: `python -m pytest tests/depgraph/test_classify.py tests/depgraph/ -q`
Expected: PASS. `classify.py` is a new module called by nothing in real construction → byte-identical gate trivially met.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/classify.py tests/depgraph/test_classify.py
git commit -m "feat(depgraph): classify.py pure lane classifier (ladder + Module emission + deferred set)" -- src/python_deps/depgraph/classify.py tests/depgraph/test_classify.py
```

---

### Task 4: `classify.py` — PEP 420 namespace-root handling

The current `repo_modules._module_for` climb (`repo_modules.py:51-81`) stops at `pkga` for `src/mycompany/pkga/__init__.py` with **no** `mycompany/__init__.py`, minting a false top-level `pkga` and never surfacing `mycompany`. This is the hole that killed the prior module-node spec (review §6). A name minted through a namespace-suspect climb routes to the **collision zone**, not clear-internal/external — constrained to manifest-declared package roots.

**Files:**
- Modify: `src/python_deps/depgraph/classify.py` (add namespace-suspect detection; feed into `deferred`)
- Reuse (read): `invocation_resolver._find_project_dirs`, and the declared package roots (`packages`/`package_dir`/`find_namespace_packages`) `invocation_resolver` already parses
- Test: `tests/depgraph/test_classify.py`

**Interfaces:**
- Consumes: `classify(...)` gains awareness of namespace-suspect tops.
- Produces: a top-level minted under a declared namespace root (a dir with no `__init__.py` whose parent chain to a declared package root has no `__init__.py`) is added to `deferred`, not `internal`/`external`.

- [ ] **Step 1: Write the failing test**

```python
def test_pep420_namespace_root_routes_to_collision(tmp_path):
    # src/mycompany/pkga/__init__.py with NO mycompany/__init__.py: a PEP 420
    # namespace. The real import is mycompany.pkga; the naive climb mints `pkga`.
    pkga = tmp_path / "src" / "mycompany" / "pkga"
    pkga.mkdir(parents=True)
    (pkga / "__init__.py").write_text("")
    (pkga / "mod.py").write_text("import pkga.mod\n")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.setuptools.packages.find]\nwhere=['src']\n"  # namespace-declaring
    )
    from python_deps.depgraph.classify import classify
    routing = classify(str(tmp_path), target_stdlib=frozenset(), declared=frozenset())
    assert "pkga" in routing.deferred                      # namespace-suspect → collision zone
    assert "pkga" not in {n for n, _ in routing.internal}  # NOT a trusted top-level
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_classify.py::test_pep420_namespace_root_routes_to_collision -v`
Expected: FAIL — `pkga` is minted as a trusted internal top-level.

- [ ] **Step 3: Add namespace-suspect detection**

Add to `classify.py` a helper that returns the set of tops minted through a namespace-suspect climb, seeded from declared package roots, and subtract them from `internal_tops` into `deferred`:

```python
def _namespace_suspect_tops(repo_path: str) -> frozenset[str]:
    """Top-level names whose sys.path root is a declared package root that has NO
    ``__init__.py`` at the intermediate level — i.e. minted by the climb stopping
    one dir too low under a PEP 420 namespace (review §6). Constrained to declared
    package roots so an ordinary flat top-level is never falsely suspected."""
    from pathlib import Path
    from python_deps.depgraph.invocation_resolver import _find_project_dirs
    repo = Path(repo_path)
    roots = {repo / d for d, _ in [(r, None) for r in ("src",)] if (repo / d).is_dir()}
    project_dirs, _mono = _find_project_dirs(repo)
    for rel in project_dirs:
        p = repo if rel == "." else repo / rel
        if (p / "src").is_dir():
            roots.add(p / "src")
    suspect: set[str] = set()
    for root in roots:
        for child in root.iterdir() if root.is_dir() else ():
            # a dir with no __init__.py whose SUBDIRS contain packages == a PEP 420
            # namespace: its children are the real subpackages, not top-levels.
            if child.is_dir() and not (child / "__init__.py").is_file():
                if any((g / "__init__.py").is_file() for g in child.iterdir() if g.is_dir()):
                    suspect.update(
                        g.name for g in child.iterdir()
                        if g.is_dir() and (g / "__init__.py").is_file()
                    )
    return frozenset(suspect)
```

In `classify`, after building `internal_tops`, before emitting:

```python
    suspect = _namespace_suspect_tops(repo_path)
    deferred.update(internal_tops & suspect)
    internal_tops -= suspect
```

- [ ] **Step 4: Run the test + the depgraph suite**

Run: `python -m pytest tests/depgraph/test_classify.py tests/depgraph/ -q`
Expected: PASS. Still a shadow-only module → byte-identical.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/classify.py tests/depgraph/test_classify.py
git commit -m "feat(depgraph): classify.py PEP 420 namespace-root suspects route to the collision zone" -- src/python_deps/depgraph/classify.py tests/depgraph/test_classify.py
```

---

### Task 5: `cure.py` — in-container editable install + collect-gate + scratch-certified stamp

Runs the config-cure in the mounted scratch container: `pip install -e .` with a build-isolation fallback chain, then the canonical collect-gate under the `TestEnvPlan`. On success it stamps a scratch-certified state; the render-time poison (`populate.py:224-225`) is gated to not erase it.

**Files:**
- Create: `src/python_deps/depgraph/cure.py`
- Modify: `src/python_deps/depgraph/populate.py` (`:224-225` poison call site)
- Reuse (read): `probe.INSTALL_TIMEOUT`, `certify` state helpers, `invocation_resolver.TestEnvPlan`
- Test: `tests/depgraph/test_cure.py`, `tests/depgraph/test_populate_setup_commands.py`

**Interfaces:**
- Consumes: a mounted `Executor` (Task 1) with `.repo_mount_dir`; a `TestEnvPlan` (Task 2).
- Produces:
  ```python
  @dataclass(frozen=True)
  class CureResult:
      ok: bool
      rung: str          # "isolated" | "no_build_isolation" | "failed"
      collect_ok: bool
      evidence: str
  def render_cure_commands(plan, mount_dir) -> tuple[str, ...]     # pure; the fallback chain
  def run_cure(executor, plan) -> CureResult                        # container-bound
  def stamp_scratch_certified(graph, cure) -> DepGraph              # sets data["scratch_certified"]=True on Project
  ```
- The poison gate: `populate_setup_commands` skips `_poison_project_certificate` when the Project node already carries `data["scratch_certified"]`.

- [ ] **Step 1: Write the failing test for the pure command renderer + the poison gate**

```python
# tests/depgraph/test_cure.py
from python_deps.depgraph.cure import render_cure_commands
from python_deps.depgraph.invocation_resolver import resolve


def test_cure_commands_are_the_fallback_chain(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires=['setuptools','cython']\n")
    plan = resolve(str(tmp_path))
    cmds = render_cure_commands(plan, "/workspace/repo")
    assert any("pip install" in c and "-e ." in c for c in cmds)           # rung 1 isolated
    assert any("--no-build-isolation" in c for c in cmds)                  # rung 2 fallback
    assert any("setuptools" in c and "wheel" in c for c in cmds)           # backend ensured for rung 2
    assert any("pytest --collect-only" in c for c in cmds)                 # collect-gate
    assert all(c.startswith("cd /workspace/repo") for c in cmds)           # run from the mount
```

```python
# tests/depgraph/test_populate_setup_commands.py  (add)
def test_scratch_certified_project_is_not_poisoned():
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.populate import populate_setup_commands
    proj = Node(id="project:x", type=NodeType.PROJECT, name="x", layer=Layer.PIP,
                discovered_by=DiscoveredBy.GOAL, data={"scratch_certified": True})
    out = populate_setup_commands(DepGraph().with_node(proj))
    got = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    assert got.state is not State.MISSING or got.check_command is not None  # not poisoned
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/depgraph/test_cure.py tests/depgraph/test_populate_setup_commands.py::test_scratch_certified_project_is_not_poisoned -v`
Expected: FAIL — `cure` module absent; poison unconditional.

- [ ] **Step 3: Write the cure runner**

```python
# src/python_deps/depgraph/cure.py
"""In-container config-cure: editable install (build-isolation fallback chain) +
the canonical collect-gate under the TestEnvPlan. On success, stamps a
scratch-certified state so the render-time poison does not erase the config
lane's output (review §3, §9). Container-bound; the command renderer is pure."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, replace

from python_deps.depgraph.invocation_resolver import TestEnvPlan
from python_deps.depgraph.probe import INSTALL_TIMEOUT
from python_deps.depgraph.schema import DepGraph, NodeType, State


@dataclass(frozen=True)
class CureResult:
    ok: bool
    rung: str
    collect_ok: bool
    evidence: str


def _env_prefix(plan: TestEnvPlan) -> str:
    parts = []
    pp = ":".join(plan.pythonpath)
    if pp:
        parts.append(f"PYTHONPATH={shlex.quote(pp)}")
    for var, value in plan.env:
        parts.append(f"{var}={shlex.quote(value)}")
    return (" ".join(parts) + " ") if parts else ""


def render_cure_commands(plan: TestEnvPlan, mount_dir: str) -> tuple[str, ...]:
    """The build-isolation fallback chain + the collect-gate, all run from the
    mount. Rung 1: isolated ``-e .``. Rung 2 (only if rung 1 fails): ensure
    setuptools/wheel + declared build-system.requires, then ``--no-build-
    isolation -e .`` (a legacy setup.py importing numpy/cython can't see the
    Phase-A closure under isolation). Collect-gate under the plan's env."""
    cd = f"cd {shlex.quote(mount_dir)}"
    env = _env_prefix(plan)
    isolated = f"{cd} && {env}python3 -m pip install --break-system-packages -e ."
    no_iso = (
        f"{cd} && python3 -m pip install --break-system-packages -U setuptools wheel && "
        f"{env}python3 -m pip install --break-system-packages --no-build-isolation -e ."
    )
    collect = f"{cd} && {env}python3 -m pytest --collect-only -q"
    return (isolated, no_iso, collect)


def run_cure(executor, plan: TestEnvPlan) -> CureResult:
    mount = getattr(executor, "repo_mount_dir", "/workspace/repo")
    isolated, no_iso, collect = render_cure_commands(plan, mount)
    r1 = executor.run(isolated, timeout=INSTALL_TIMEOUT)
    rung, ok = ("isolated", True) if r1.ok else ("", False)
    if not ok:
        r2 = executor.run(no_iso, timeout=INSTALL_TIMEOUT)
        rung, ok = ("no_build_isolation", True) if r2.ok else ("failed", False)
    if not ok:
        return CureResult(False, "failed", False, (r1.stderr or "")[-500:])
    cg = executor.run(collect, timeout=INSTALL_TIMEOUT)
    return CureResult(True, rung, cg.ok, f"rung={rung} collect_rc={cg.returncode}")


def stamp_scratch_certified(graph: DepGraph, cure: CureResult) -> DepGraph:
    """On a successful cure, mark the Project node scratch-certified so the
    render-time poison (populate.py) leaves it alone. Additive to data only."""
    if not cure.ok:
        return graph
    new = graph
    for node in graph.nodes:
        if node.type is NodeType.PROJECT:
            data = {**node.data, "scratch_certified": True, "cure_rung": cure.rung}
            new = new.with_node(replace(node, state=State.SATISFIED, data=data))
    return new
```

(`Node` is a frozen dataclass; `dataclasses.replace` is the idiomatic copy-with-change and is already used across `populate.py`. `replace(node, data=data)` re-freezes `data` to a `MappingProxyType` via `Node.__post_init__`.)

- [ ] **Step 4: Gate the poison**

In `populate.py:224-225`, change:

```python
        updated = replace(node, setup_commands=cmds, strength=Strength.HARD)
        if node.type is NodeType.PROJECT and not node.data.get("scratch_certified"):
            updated = _poison_project_certificate(updated)
```

- [ ] **Step 5: Run the tests + the depgraph suite**

Run: `python -m pytest tests/depgraph/test_cure.py tests/depgraph/test_populate_setup_commands.py tests/depgraph/ -q`
Expected: PASS. The poison gate is vacuous in real construction (nothing sets `scratch_certified` until the shadow pass, Task 8) → byte-identical.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/cure.py src/python_deps/depgraph/populate.py tests/depgraph/test_cure.py tests/depgraph/test_populate_setup_commands.py
git commit -m "feat(depgraph): cure.py editable-install fallback chain + collect-gate; gate render-time poison on scratch-cert" -- src/python_deps/depgraph/cure.py src/python_deps/depgraph/populate.py tests/depgraph/test_cure.py tests/depgraph/test_populate_setup_commands.py
```

---

### Task 6: `arbitrate.py` — exception-aware collision arbitration (gated on cure success)

For each deferred collision name, probe `python -c "import X"` under the canonical plan **only if the cure succeeded**. Exception-aware: `ModuleNotFoundError` on the probed name ⇒ not-local (fallthrough candidate); any other exception ⇒ present-but-broken ⇒ never a fallthrough. Cure failure ⇒ all deferred collisions stay unresolved.

**Files:**
- Create: `src/python_deps/depgraph/arbitrate.py`
- Reuse (read): `cure.CureResult`, `invocation_resolver.TestEnvPlan`
- Test: `tests/depgraph/test_arbitrate.py`

**Interfaces:**
- Consumes: a mounted `Executor`, a `TestEnvPlan`, a `CureResult`, `deferred: frozenset[str]`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class Arbitration:
      resolves_local: frozenset[str]     # probe imported cleanly OR raised a non-name error → local
      fallthrough: frozenset[str]        # ModuleNotFoundError on the name → genuine external
      unresolved: frozenset[str]         # cure failed → untouched (honest RED)
  def probe_name(executor, plan, name) -> str   # "local" | "fallthrough" | "broken_local"
  def arbitrate(executor, plan, cure, deferred) -> Arbitration
  ```

- [ ] **Step 1: Write the failing test (cure-success gate + exception-aware verdict)**

```python
# tests/depgraph/test_arbitrate.py
from dataclasses import dataclass
from python_deps.depgraph.arbitrate import arbitrate
from python_deps.depgraph.cure import CureResult


@dataclass
class _FakeResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    @property
    def ok(self): return self.returncode == 0


class _FakeExec:
    def __init__(self, table): self.table = table
    def run(self, cmd, *, timeout=300):
        for key, rc, err in self.table:
            if key in cmd:
                return _FakeResult(rc, stderr=err)
        return _FakeResult(1, stderr="ModuleNotFoundError: No module named 'zzz'")


def _plan(tmp_path):
    from python_deps.depgraph.invocation_resolver import resolve
    return resolve(str(tmp_path))


def test_cure_failure_leaves_all_deferred_unresolved(tmp_path):
    arb = arbitrate(_FakeExec([]), _plan(tmp_path), CureResult(False, "failed", False, ""),
                    frozenset({"items", "azure"}))
    assert arb.unresolved == frozenset({"items", "azure"})
    assert not arb.fallthrough and not arb.resolves_local


def test_exception_aware_verdict(tmp_path):
    ex = _FakeExec([
        ("import items", 0, ""),                                           # clean → local
        ("import azure", 1, "ModuleNotFoundError: No module named 'azure'"),# name error → fallthrough
        ("import broke", 1, "ImportError: cannot import name 'x'"),         # other error → broken_local
    ])
    arb = arbitrate(ex, _plan(tmp_path), CureResult(True, "isolated", True, ""),
                    frozenset({"items", "azure", "broke"}))
    assert "items" in arb.resolves_local
    assert "azure" in arb.fallthrough
    assert "broke" in arb.resolves_local          # present-but-broken is LOCAL, never fallthrough
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_arbitrate.py -v`
Expected: FAIL — `arbitrate` absent.

- [ ] **Step 3: Write the arbitrator**

```python
# src/python_deps/depgraph/arbitrate.py
"""Collision-zone arbitration: the exception-aware per-name probe under the
canonical TestEnvPlan, gated on cure success. A deferred collision installs its
PyPI namesake ONLY IF the cure succeeded AND the name genuinely does not resolve
locally (review §1, §7). Container-bound; a sibling of relink/certify, not the
classifier."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from python_deps.depgraph.cure import CureResult, _env_prefix
from python_deps.depgraph.invocation_resolver import TestEnvPlan

_NAME_ERR = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


@dataclass(frozen=True)
class Arbitration:
    resolves_local: frozenset[str]
    fallthrough: frozenset[str]
    unresolved: frozenset[str]


def probe_name(executor, plan: TestEnvPlan, name: str) -> str:
    mount = getattr(executor, "repo_mount_dir", "/workspace/repo")
    cmd = f"cd {shlex.quote(mount)} && {_env_prefix(plan)}python3 -c 'import {name}'"
    result = executor.run(cmd, timeout=120)
    if result.ok:
        return "local"                                  # imports cleanly under the plan → local
    match = _NAME_ERR.search(result.stderr or "")
    if match and match.group(1).split(".", 1)[0] == name:
        return "fallthrough"                            # name genuinely absent → external
    return "broken_local"                               # any other exception → present-but-broken


def arbitrate(executor, plan: TestEnvPlan, cure: CureResult, deferred: frozenset[str]) -> Arbitration:
    if not cure.ok:
        return Arbitration(frozenset(), frozenset(), frozenset(deferred))
    local: set[str] = set()
    through: set[str] = set()
    for name in sorted(deferred):
        verdict = probe_name(executor, plan, name)
        (through if verdict == "fallthrough" else local).add(name)
    return Arbitration(frozenset(local), frozenset(through), frozenset())
```

- [ ] **Step 4: Run the test + the depgraph suite**

Run: `python -m pytest tests/depgraph/test_arbitrate.py tests/depgraph/ -q`
Expected: PASS. New module, shadow-only → byte-identical.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/arbitrate.py tests/depgraph/test_arbitrate.py
git commit -m "feat(depgraph): arbitrate.py exception-aware collision probe, gated on cure success" -- src/python_deps/depgraph/arbitrate.py tests/depgraph/test_arbitrate.py
```

---

### Task 7: Lane-aware `_phase_a_fixpoint` (`missing` filter + `deferred` param)

Make the fixpoint safe for route-not-drop: once first-party imports become IMPORT nodes (Stage C), they must never inflate `bound` or reach the LLM dist-guesser (review §4). This is a pure, additive change to `build.py:_phase_a_fixpoint` — vacuous today (no IMPORT node is Module-routed; `deferred` defaults empty), so byte-identical.

**Files:**
- Modify: `src/python_deps/depgraph/build.py` (`_phase_a_fixpoint` signature `:367-384`, `missing` filter `:432-438`)
- Test: `tests/depgraph/test_phase_a_fixpoint.py`

**Interfaces:**
- Consumes: an optional `deferred: frozenset[str] = frozenset()` and a per-node `data["routed_provider"] == "module"` marker (absent in real construction until the flip).
- Produces: `missing` excludes (a) IMPORT nodes marked Module-routed and (b) names in `deferred`; `bound` and the candidate loop see only genuine externals.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_phase_a_fixpoint.py  (add)
def test_missing_excludes_module_routed_and_deferred():
    from python_deps.depgraph.build import _missing_import_nodes  # extracted pure helper
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy

    def imp(name, **data):
        return Node(id=f"import:{name}", type=NodeType.IMPORT, name=name,
                    layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN, data=data)

    graph = (DepGraph()
             .with_node(imp("requests"))
             .with_node(imp("myapp", routed_provider="module"))
             .with_node(imp("items")))
    got = {n.name for n in _missing_import_nodes(graph, provided=frozenset(), deferred=frozenset({"items"}))}
    assert got == {"requests"}   # myapp is module-routed; items is deferred; only requests is missing
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_phase_a_fixpoint.py::test_missing_excludes_module_routed_and_deferred -v`
Expected: FAIL — `_missing_import_nodes` does not exist.

- [ ] **Step 3: Extract the pure filter + make it lane-aware**

In `build.py`, extract the `missing` comprehension (`:432-438`) into a pure module-level helper and call it from the loop:

```python
def _missing_import_nodes(graph, *, provided: frozenset[str], deferred: frozenset[str]):
    """Non-optional IMPORT nodes no resolved dist provides — LANE-AWARE: excludes
    Module-routed imports and deferred-collision names so first-party names never
    inflate the repair bound nor reach the dist-guesser (review §4). Vacuous when
    no node is Module-routed and ``deferred`` is empty (today's real construction)."""
    return [
        n for n in graph.nodes
        if n.type is NodeType.IMPORT
        and n.data.get("optional") is not True
        and n.data.get("routed_provider") != "module"
        and n.name.split(".", 1)[0] not in deferred
        and top_level_import_name(n.name).lower() not in provided
    ]
```

Add `deferred: frozenset[str] = frozenset()` to `_phase_a_fixpoint`'s signature (after `llm`), and replace the inline `missing = [...]` with:

```python
        provided = resolved_record_coverage(pkg_nodes, record_provider)
        missing = _missing_import_nodes(graph, provided=frozenset(provided), deferred=deferred)
```

The `_phase_a_fixpoint` call site (`build.py:~948-964`) passes nothing new, so `deferred` defaults empty.

- [ ] **Step 4: Add the byte-identical regression assertion**

```python
def test_missing_filter_is_byte_identical_without_lanes():
    # With no module-routed nodes and empty deferred, the new helper equals the
    # old comprehension exactly (behavior-preserving gate for Stage C).
    from python_deps.depgraph.build import _missing_import_nodes
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy
    imp = lambda n, **d: Node(id=f"import:{n}", type=NodeType.IMPORT, name=n,
                              layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN, data=d)
    graph = DepGraph().with_node(imp("requests")).with_node(imp("flask", optional=True))
    got = {n.name for n in _missing_import_nodes(graph, provided=frozenset(), deferred=frozenset())}
    assert got == {"requests"}   # optional dropped; nothing else excluded
```

- [ ] **Step 5: Run the tests + the depgraph suite**

Run: `python -m pytest tests/depgraph/test_phase_a_fixpoint.py tests/depgraph/ -q`
Expected: PASS. The two new clauses are vacuous in real construction → byte-identical.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_phase_a_fixpoint.py
git commit -m "refactor(depgraph): lane-aware _phase_a_fixpoint missing filter (module-routed + deferred excluded)" -- src/python_deps/depgraph/build.py tests/depgraph/test_phase_a_fixpoint.py
```

> **Deferred to Stage C (not this task):** the *threaded fallthrough re-entry* (a resumed fixpoint over the arbitration's fallthrough set with `prev_pkg_ids`/`attempted` carried, review §4) and the Phase-B re-run over fallthroughs (review §5) only run when the arbitration feeds the real graph. In Stage B the shadow pass measures fallthroughs without installing them, so re-entry is not yet wired. The lane-aware `missing` filter above is the piece that must land now so the flip is a one-line activation, not a fixpoint rewrite.

---

### Task 8: `shadow.py` — the shadow config-lane pass + diagnostic emitter

Wire Tasks 3→5→6 into one flagged pass at the `_python_package_obligations` tail. It probes stdlib, classifies, runs the cure, arbitrates, emits a per-repo diagnostic record, and **discards its graph effect**. This is the *same code* Stage C flips to "wired." Flag defaults OFF → real construction byte-identical.

**Files:**
- Create: `src/python_deps/depgraph/shadow.py`
- Modify: `src/python_deps/depgraph/build.py` (`_python_package_obligations` tail `:1008-1015`; thread a `shadow_config_lane: bool = False` flag through `build_dep_graph`)
- Test: `tests/depgraph/test_shadow.py`

**Interfaces:**
- Consumes: `classify.classify/probe_target_stdlib/apply_routing`, `cure.run_cure/stamp_scratch_certified`, `arbitrate.arbitrate`, a mounted `container_executor`, the `TestEnvPlan` from `invocation_resolver.resolve(repo_path)`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class ShadowRecord:
      repo: str
      n_internal: int; n_external: int; n_deferred: int
      cure_ok: bool; cure_rung: str; collect_ok: bool
      resolves_local: tuple[str, ...]; fallthrough: tuple[str, ...]; unresolved: tuple[str, ...]
      provisional_flags: tuple[str, ...]   # fallthroughs = install-PyPI-over-local-module flags
  def run_shadow_config_lane(graph, repo_path, container_executor, declared) -> ShadowRecord
  ```
- The record is written to a per-repo JSON (path from an env/arg) for the Gate B aggregator; the graph is returned UNCHANGED.

- [ ] **Step 1: Write the failing test (shadow returns a record and does not mutate the graph)**

```python
# tests/depgraph/test_shadow.py
from python_deps.depgraph.shadow import run_shadow_config_lane, ShadowRecord


class _StubExec:
    repo_mount_dir = "/workspace/repo"
    def run(self, cmd, *, timeout=300):
        from dataclasses import dataclass
        @dataclass
        class R:
            returncode: int = 0; stdout: str = "[]"; stderr: str = ""
            @property
            def ok(self): return self.returncode == 0
        return R()


def test_shadow_emits_record_without_mutating_graph(tmp_path):
    (tmp_path / "mypkg").mkdir(); (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "a.py").write_text("import requests\n")
    from python_deps.depgraph.scan import scan_to_nodes
    graph = scan_to_nodes(str(tmp_path))
    before = {n.id for n in graph.nodes}
    rec = run_shadow_config_lane(graph, str(tmp_path), _StubExec(), declared=frozenset())
    assert isinstance(rec, ShadowRecord)
    assert {n.id for n in graph.nodes} == before          # graph UNCHANGED (immutability + discard)
    assert rec.n_external >= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_shadow.py -v`
Expected: FAIL — `shadow` absent.

- [ ] **Step 3: Write the shadow pass**

```python
# src/python_deps/depgraph/shadow.py
"""The shadow config-lane pass: classify -> cure -> arbitrate, MEASURED, graph
effect DISCARDED. Same code Stage C flips to wired (route-not-drop). Behind a
flag; real construction unaffected."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.arbitrate import arbitrate
from python_deps.depgraph.classify import classify, probe_target_stdlib
from python_deps.depgraph.cure import run_cure
from python_deps.depgraph.invocation_resolver import resolve


@dataclass(frozen=True)
class ShadowRecord:
    repo: str
    n_internal: int
    n_external: int
    n_deferred: int
    cure_ok: bool
    cure_rung: str
    collect_ok: bool
    resolves_local: tuple[str, ...]
    fallthrough: tuple[str, ...]
    unresolved: tuple[str, ...]
    provisional_flags: tuple[str, ...]


def run_shadow_config_lane(graph, repo_path, container_executor, *, declared) -> ShadowRecord:
    stdlib = probe_target_stdlib(container_executor)
    routing = classify(repo_path, target_stdlib=stdlib, declared=declared)
    plan = resolve(repo_path)
    cure = run_cure(container_executor, plan)
    arb = arbitrate(container_executor, plan, cure, routing.deferred)
    # a fallthrough is exactly the false-green flag: we would install the PyPI
    # namesake of a name that ALSO exists as a local module.
    return ShadowRecord(
        repo=repo_path,
        n_internal=len(routing.internal),
        n_external=len(routing.external),
        n_deferred=len(routing.deferred),
        cure_ok=cure.ok, cure_rung=cure.rung, collect_ok=cure.collect_ok,
        resolves_local=tuple(sorted(arb.resolves_local)),
        fallthrough=tuple(sorted(arb.fallthrough)),
        unresolved=tuple(sorted(arb.unresolved)),
        provisional_flags=tuple(sorted(arb.fallthrough)),
    )
```

- [ ] **Step 4: Thread the flag through `build.py` (default OFF)**

At the `_python_package_obligations` tail (`build.py:1008`, after `project_native_obligations`, before the resolver restamp), add a guarded call that records but discards:

```python
    graph = project_native_obligations(graph, repo_path, host_executor, container_executor)
    if shadow_config_lane and repo_path is not None:
        from python_deps.depgraph.shadow import run_shadow_config_lane
        from python_deps.depgraph.shadow import _write_shadow_record  # step 5
        record = run_shadow_config_lane(
            graph, repo_path, container_executor,
            declared=frozenset(declared_package_names),
        )
        _write_shadow_record(record)   # graph is intentionally NOT rebound
```

Add `shadow_config_lane: bool = False` to `_python_package_obligations` and `build_dep_graph` signatures, threaded through (default OFF everywhere; only the shadow harness/Gate B sets it True). `declared_package_names` is already in scope at the fixpoint call.

- [ ] **Step 5: Add the record writer (append-JSONL to a path from env)**

```python
# shadow.py
import json, os

def _write_shadow_record(record: ShadowRecord) -> None:
    path = os.environ.get("V3_SHADOW_RECORD_PATH")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.__dict__) + "\n")
```

- [ ] **Step 6: Run the tests + the depgraph suite (flag OFF = byte-identical)**

Run: `python -m pytest tests/depgraph/test_shadow.py tests/depgraph/ -q`
Expected: PASS. With `shadow_config_lane=False` (the default), `_python_package_obligations` is byte-identical — the behavior-preserving gate.

- [ ] **Step 7: Commit**

```bash
git add src/python_deps/depgraph/shadow.py src/python_deps/depgraph/build.py tests/depgraph/test_shadow.py
git commit -m "feat(depgraph): shadow config-lane pass (classify->cure->arbitrate), measured + discarded behind a flag" -- src/python_deps/depgraph/shadow.py src/python_deps/depgraph/build.py tests/depgraph/test_shadow.py
```

---

### Task 9: The provisional-flag owner (honest reporting)

A fallthrough (installing the PyPI namesake of a name that also exists as a local module) is a **provisional** certification, not a clean pass. Give the flag a named consumer so it is reported honestly end-to-end, never laundered into a green (review §8; `honest-success-def-and-branch-split`).

**Files:**
- Modify: `bench/schema.py` (`MeasureRow` dataclass `:25-49` — add `provisional_flags: tuple = ()`)
- Modify: `bench/metrics.py` (`compute_metrics` `:21-45`)
- Test: `tests/test_metrics.py` (create if absent)

**Interfaces:**
- Consumes: `MeasureRow.provisional_flags` (surfaced from `ShadowRecord.provisional_flags`, Task 8; the shadow→row surfacing itself lands at the Stage C flip — see the note).
- Produces: `compute_metrics` gains `"certified_with_provisional": n_provisional` and `"EBSR_clean": _div(n_collect_clean_strict, n)`, where `n_collect_clean_strict` counts rows that are collect-clean AND carry no provisional flags. Raw `EBSR` is unchanged; a flagged repo is **never** in the clean numerator.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
def test_provisional_flag_downgrades_the_clean_bucket():
    from bench.schema import MeasureRow
    from bench.metrics import compute_metrics
    rows = [
        MeasureRow(agent="v3", repo="a", env_status="ok", build_ok=True, collect_clean=True),
        MeasureRow(agent="v3", repo="b", env_status="ok", build_ok=True, collect_clean=True,
                   provisional_flags=("items",)),
    ]
    m = compute_metrics(rows)
    assert m["certified_with_provisional"] == 1
    assert m["EBSR_clean"] == 0.5     # only 'a' is a clean pass; 'b' is provisional
    assert m["EBSR"] == 1.0           # raw EBSR unchanged (both are collect-clean)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_metrics.py::test_provisional_flag_downgrades_the_clean_bucket -v`
Expected: FAIL — `MeasureRow` has no `provisional_flags`; no `EBSR_clean`/`certified_with_provisional` keys.

- [ ] **Step 3: Add the field + the bucket**

In `bench/schema.py`, add to `MeasureRow` (next to the collect fields, `:33-35`):

```python
    provisional_flags: tuple = ()   # collision names installed as PyPI namesakes over a local module
```

In `bench/metrics.py` `compute_metrics`, after `n_collect_clean = ...` (`:25`):

```python
    n_provisional = sum(1 for r in rows if r.provisional_flags)
    n_collect_clean_strict = sum(1 for r in rows if r.collect_clean and not r.provisional_flags)
```

and in the `out` dict, beside `"EBSR"` (`:34`):

```python
        "certified_with_provisional": n_provisional,
        "EBSR_clean": _div(n_collect_clean_strict, n),
```

- [ ] **Step 4: Run the test + the bench suite**

Run: `python -m pytest tests/test_metrics.py -q && python -m pytest tests/ -q -k "metric or measure or bench"`
Expected: PASS. Adding a defaulted field + two derived keys is additive; existing metrics keys are unchanged.

- [ ] **Step 5: Commit**

```bash
git add bench/schema.py bench/metrics.py tests/test_metrics.py
git commit -m "feat(bench): certified-with-provisional bucket — collision fallthroughs never count as a clean EBSR" -- bench/schema.py bench/metrics.py tests/test_metrics.py
```

> **Scope note (not a placeholder):** in Stage B the real graph installs no fallthroughs, so no live `MeasureRow` carries a provisional flag yet — the Gate B aggregator (Task 10) reads the shadow JSONL directly for the false-green rate. Task 9's deliverable is the **contract**: the schema field + the honest-reporting bucket + its test, so that when Stage C wires fallthroughs into the real graph, `measure.py` only has to populate `provisional_flags` and honest reporting is already guaranteed. The load-bearing invariant is fixed here: **a provisional flag downgrades the bucket, never silently passes.**

---

### Task 10: Gate B — partition-sanity measurement (the go/no-go before the flip)

Run the shadow pass on the pass-repo sweep + the 50-repo corpus; aggregate the records; decide whether the classifier routes correctly and the false-green rate is acceptable before Stage C makes the lane load-bearing.

**Files:**
- Create: `scripts/gate_b_partition_sanity.py` (thin driver: sets `shadow_config_lane=True` + `V3_SHADOW_RECORD_PATH`, runs construction over the corpus, aggregates the JSONL)
- Reuse (read): the Gate A provisioning path (`datasets/pilot.json`, `datasets/rat_python50_pinned_m3nothink.json`), `build_dep_graph`

**Interfaces:**
- Consumes: the shadow JSONL records (Task 8).
- Produces: an aggregate — partition sizes distribution, collision-zone frequency, cure-recovery rate, fallthrough count, provisional-flag rate, and any exceptions — written to `docs/superpowers/handoffs/2026-07-17-gate-b-result.md`.

- [ ] **Step 1: Provision + run the shadow pass on the pass-repos (sweep must stay green)**

Run: `V3_SHADOW_RECORD_PATH=/tmp/shadow.jsonl python scripts/gate_b_partition_sanity.py --corpus datasets/pilot.json --base-image <arm64-img>`
Expected: every pilot constructs successfully (the flag adds a measured pass; the real graph is unchanged, so the **pass-repo sweep stays green** — the load-bearing safety check).

- [ ] **Step 2: Scale to the 50-repo corpus**

Run: `V3_SHADOW_RECORD_PATH=/tmp/shadow50.jsonl python scripts/gate_b_partition_sanity.py --corpus datasets/rat_python50_pinned_m3nothink.json --base-image <img>`
Record the aggregate.

- [ ] **Step 3: Apply the Gate B criterion (and write it down)**

**GO** if: the collision zone is a small minority of imports; the classifier's internal/external split matches expectation on spot-checked repos; cure-recovery tracks Gate A; and the provisional-flag (false-green) rate is low enough to report honestly. **NO-GO** if the collision zone is huge, the classifier misroutes, or false-greens are common — then rethink before the flip. Write the numbers + verdict to `docs/superpowers/handoffs/2026-07-17-gate-b-result.md` (no silent caps: list every repo excluded/errored).

- [ ] **Step 4: Commit**

```bash
git add scripts/gate_b_partition_sanity.py docs/superpowers/handoffs/2026-07-17-gate-b-result.md
git commit -m "feat(eval): Gate B partition-sanity harness + result (shadow config-lane measurement)" -- scripts/gate_b_partition_sanity.py docs/superpowers/handoffs/2026-07-17-gate-b-result.md
```

---

## Deferred to Stage C (not in this plan)

- **The flip (route-not-drop):** `scan.scan_to_nodes` stops dropping first-party (`scan.py:161,165,169,173`); `apply_routing` wires the spine (`project→module→import`) into the real graph replacing the flat `Test→Import` hub; the lane-aware `missing` filter and `deferred` become live inputs; the arbitration feeds the real fixpoint.
- **Threaded fallthrough re-entry + Phase-B re-run** (review §4/§5) — only meaningful once fallthroughs install into the real graph.
- **`_add_project_node` consults routing** (`build.py:194`) rather than drawing a direct edge for every runtime declared dep.
- **relink-vs-probe precedence** (review §11) — propagate the provisional marker onto relink edges once the spine is live.
- **Tripwire rewrite** (`tests/depgraph/test_construction_boundary.py`) — structural guard "only `classify.py` imports `repo_modules`"; behavioral guard "a collision is not install-accepted unless cure succeeded AND the probe shows it doesn't resolve locally."
- **Retire the old drop path + the flat Test-hub wiring + the shadow flag** — deletion is genuinely last, sweep-gated.

## Self-Review

- **Spec coverage:** implements every Stage-B sub-plan of `2026-07-17-config-lane-module-spine-and-collision-arbitration-design.md` — mount (Task 1), canonical `TestEnvPlan` (Task 2), pure classifier + PEP 420 (Tasks 3–4, review §6/§12/§17), cure + poison reconciliation (Task 5, review §3/§9), exception-aware arbitration gated on cure success (Task 6, review §1/§7), lane-aware fixpoint (Task 7, review §4), shadow measurement (Task 8), the flag owner (Task 9, review §8), Gate B (Task 10). The Stage-C-only pieces (the flip, fallthrough re-entry, tripwire, retirement) are explicitly deferred with anchors.
- **Placeholder scan:** every code block is correct-as-written and every path is verified against the current tree — `bench/schema.py:25-49` (`MeasureRow`), `bench/metrics.py:21` (`compute_metrics`), no reference to the non-existent `consolidate_run.py`. No "TBD"/"add error handling"/"similar to"/`# noqa`-guard placeholders. The two `<arm64-img>` tokens in Tasks 10 (and Gate A) are the user's benchmark base image, supplied at run time — a runtime argument, not a code placeholder.
- **Type consistency:** `LaneRouting`/`CureResult`/`Arbitration`/`ShadowRecord` field names are used identically across Tasks 3/5/6/8; `probe_name` returns `"local"|"fallthrough"|"broken_local"`; `_env_prefix` is defined in `cure.py` and reused by `arbitrate.py`; `NodeType.MODULE` (from Stage A Task 2) is the emitted type; `DiscoveredBy.CLASSIFIER` (exists, `schema.py:57`) is the provenance.
- **Behavior-preserving gate present in every task:** Tasks 1–8 each state why the change is byte-identical on real construction (default-off flag, vacuous filter clause, shadow-only module, dormant subsystem), and Task 7 adds an explicit byte-identical regression test. Gate B (Task 10 Step 1) is where the whole-lane pass-repo sweep confirms it.
