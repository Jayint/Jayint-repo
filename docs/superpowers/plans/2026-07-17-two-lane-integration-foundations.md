# Two-Lane Integration — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the three unblocked foundation changes of the two-lane model integration — certify-by-import for the project node, dropping the `tier` axis, and adding the `FILE` node type + edge scaffolding — each independently testable and regression-sweep-safe.

**Architecture:** Targeted edits at existing seams in `src/python_deps/depgraph/`. No new pipeline stages; no behavioral routing change (route-not-drop is deliberately out of scope — it is blocked on the collision-zone arbitration owner). Task 1 sets a `check_command` on the existing `Project` node; Task 2 removes a derived field and its few consumers; Task 3 adds one enum member + two `EDGE_RULES` entries so the file lane has schema support before anything emits it.

**Tech Stack:** Python 3.11+, pytest, frozen dataclasses (`schema.py`).

## Global Constraints

- **`python_deps/*` stays LLM-free.** None of these tasks introduce a model call.
- **Keep `layer`; only `tier` is dropped.** `layer` is the install-ordering backbone (`certify.EXECUTION_LAYER_ORDER`) and is untouched.
- **`NodeType` enum stays a superset.** Task 3 *adds* `FILE`; no member is removed. Demoted types (Test/Runtime/Config/Service/Platform) remain.
- **`pkg_layer`'s `.tier` is a DIFFERENT field.** `pkg_layer/planes.py:91,94` and `tests/pkg_layer/*` use a `Tier` enum on the closure model — NOT `schema.Node.tier`. Task 2 must not touch them.
- **Native detection is untouched.** No task edits any native module; no native module reads `.tier` (verified).
- **Regression-sweep is the gate.** After each task, the depgraph test suite must stay green; Tasks 1–2 additionally warrant a pass-repo sweep before a scored run (they change project-node certification / serialization).
- **Shared branch (`john-v3-multi-lang`): commits are pathspec-scoped.** Always `git add <exact paths> && git commit -- <exact paths>`. Never `git add -A`. No `Co-Authored-By` trailer (attribution disabled globally).

**Source spec:** `docs/superpowers/specs/2026-07-17-two-lane-model-integration-refactor.md` (migration steps 1–3). Steps 4 (route-not-drop + classifier + config-cure) and 5 (delete demoted-tier emission) are **out of scope** here — see "Deferred" at the end.

---

### Task 1: Certify-by-import for the Project node

The `Project` node is created today with **no `check_command`** (`build.py:199-218`), so `certify` (`certify.py:79`) leaves it `UNKNOWN` forever — the graph never checks that the repo's own package imports, which is the dominant measured collect-cliff failure (`ModuleNotFoundError` on the project namespace: azure 453×, frappe 290×). This task sets `check_command = python -c "import <mod>"` when the project's distribution name maps unambiguously (dash→underscore, lowercased) to one of its own top-level modules. When the import name differs from the dist name (`scikit-learn`→`sklearn`) there is no tripwire-safe static match, so the node stays `UNKNOWN` — never certified against a guess. `build.py` must not import `repo_modules` (construction-boundary tripwire); the source here is `evidence.project_local_modules`, already computed in `_add_project_node`.

**Files:**
- Modify: `src/python_deps/depgraph/build.py` — add `_project_import_target`, wire it into `_add_project_node` (`:177-242`)
- Test: `tests/depgraph/test_build.py`

**Interfaces:**
- Consumes: `PythonDependencyEvidence.project_local_modules` (`models.py:127`), already collected at `build.py:198`.
- Produces: `_project_import_target(project_name: str, evidence) -> str | None`; the `Project` node now carries `check_command` when a match exists.

- [ ] **Step 1: Write the failing test**

In `tests/depgraph/test_build.py` (add these two tests; imports `_add_project_node`, `DepGraph`, `NodeType` as the module already uses):

```python
def test_project_node_certifies_by_import_when_name_matches_local_module(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "frappe"\nversion = "0.0.0"\n'
    )
    pkg = tmp_path / "frappe"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    from python_deps.depgraph.build import _add_project_node
    from python_deps.depgraph.schema import DepGraph, NodeType
    graph = _add_project_node(DepGraph(), str(tmp_path))
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert proj.check_command == 'python -c "import frappe"'


def test_project_node_no_import_check_when_dist_name_differs_from_import(tmp_path):
    # scikit-learn -> sklearn: no tripwire-safe static match -> stay UNKNOWN
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "scikit-learn"\nversion = "0.0.0"\n'
    )
    pkg = tmp_path / "sklearn"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    from python_deps.depgraph.build import _add_project_node
    from python_deps.depgraph.schema import DepGraph, NodeType
    graph = _add_project_node(DepGraph(), str(tmp_path))
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert proj.check_command is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/depgraph/test_build.py::test_project_node_certifies_by_import_when_name_matches_local_module -v`
Expected: FAIL — `proj.check_command` is `None` (no check set today).

- [ ] **Step 3: Add the helper**

In `src/python_deps/depgraph/build.py`, above `_add_project_node`:

```python
def _project_import_target(project_name: str, evidence) -> str | None:
    """The project's own top-level import module to certify-by-import, or None.

    Maps the distribution name to an import name (dash->underscore, lowercased)
    and returns it ONLY when that exact name is one of the repo's own top-level
    modules (``evidence.project_local_modules``). When the import name differs
    from the dist name (``scikit-learn`` -> ``sklearn``) there is no
    tripwire-safe static match, so we return None and leave the Project UNKNOWN
    rather than certify against a guess -- the relink-based mapping (config lane,
    a later task) covers that case with a certified source. ``build.py`` must not
    import ``repo_modules`` (construction-boundary tripwire), so the source here
    is the already-collected ``project_local_modules``.
    """
    canon = project_name.lower().replace("-", "_")
    return canon if canon in set(evidence.project_local_modules) else None
```

- [ ] **Step 4: Wire it into `_add_project_node`**

In `_add_project_node`, immediately after `evidence = collect_python_dependency_evidence(repo_path)` (`build.py:198`):

```python
    import_target = _project_import_target(name, evidence)
    project_check = f'python -c "import {import_target}"' if import_target else None
```

Then add `check_command=project_check,` to the `Node(...)` built at `build.py:199-218` (alongside `state=State.UNKNOWN,`).

- [ ] **Step 5: Run both tests to verify they pass**

Run: `python -m pytest tests/depgraph/test_build.py -k "certifies_by_import or no_import_check" -v`
Expected: PASS (both).

- [ ] **Step 6: Run the depgraph suite (no regressions)**

Run: `python -m pytest tests/depgraph/ -q`
Expected: PASS. (A `Project` node that now flips `MISSING` is the intended new signal; confirm no test asserted the project stays `UNKNOWN`.)

- [ ] **Step 7: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_build.py
git commit -m "feat(depgraph): certify the Project node by import when dist name maps to a local module" -- src/python_deps/depgraph/build.py tests/depgraph/test_build.py
```

---

### Task 2: Drop the `tier` axis

`tier` is a derived provider-int (`TYPE_TO_TIER`) redundant with `layer`. Remove it from `schema.py` and its three real consumers. **Do not touch `pkg_layer`** — its `.tier` is an unrelated `Tier` enum.

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (`:34-47`, `:155`, `:190-191`, `:224`, `:272`)
- Modify: `src/python_deps/depgraph/schedule.py` (`:67`, `:90`, `:96`)
- Modify: `src/eval/graph_repair_ablation/context.py` (`:31`, `:36`)
- Modify: `tests/depgraph/test_schema_roundtrip.py` (`:211` + `_maximal_graph` + `_UNDERIVABLE_TIER`)
- Test: `tests/depgraph/test_schema_roundtrip.py`

**Interfaces:**
- Removes: `schema.TYPE_TO_TIER`, `schema.tier_for_type`, `Node.tier`. No replacement — `layer` already carries ordering.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schema_roundtrip.py`:

```python
def test_node_has_no_tier_field():
    from python_deps.depgraph.schema import Node, NodeType, Layer, DiscoveredBy
    n = Node(id="x", type=NodeType.PACKAGE, name="x", layer=Layer.PIP,
             discovered_by=DiscoveredBy.RESOLVER)
    assert not hasattr(n, "tier")
    assert "tier" not in n.to_dict()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_schema_roundtrip.py::test_node_has_no_tier_field -v`
Expected: FAIL — `n` still has `tier`, and `to_dict()` still emits `"tier"`.

- [ ] **Step 3: Remove `tier` from `schema.py`**

- Delete the `TYPE_TO_TIER` dict (`:34-42`) and `tier_for_type` (`:45-47`).
- Delete the `tier: int = 0` field (`:155`).
- In `__post_init__`, delete the two lines that derive tier (`:190-191`: the `if self.tier == 0:` block and its `object.__setattr__(self, "tier", ...)`).
- In `to_dict`, delete the `"tier": self.tier,` line (`:224`).
- In `from_dict`, delete the `tier=d.get("tier", 0),` line (`:272`).

- [ ] **Step 4: Update the three consumers**

`src/python_deps/depgraph/schedule.py`:
- Delete the `tier: int` field from `ObligationPacket` (`:67`).
- Change the goal f-string (`:90`) from `f"Satisfy obligation '{node.name}' ({node.type.value}, tier {node.tier}): "` to `f"Satisfy obligation '{node.name}' ({node.type.value}): "`.
- Delete the `tier=node.tier,` argument (`:96`).

`src/eval/graph_repair_ablation/context.py`:
- Change the sort key (`:31`) from `key=lambda x: (x.tier, x.type.value, x.name)` to `key=lambda x: (x.layer.value, x.type.value, x.name)`.
- Change the label (`:36`) from `f"- [{n.type.value} tier={n.tier}] {n.name} "` to `f"- [{n.type.value}] {n.name} "`.

`tests/depgraph/test_schema_roundtrip.py`:
- Delete the `assert n.tier == _UNDERIVABLE_TIER` line (`:211`).
- Remove the `tier=_UNDERIVABLE_TIER` argument from the `Node(...)` constructed in `_maximal_graph()`.
- Delete the now-unused `_UNDERIVABLE_TIER` module constant.

- [ ] **Step 5: Run the schema + schedule + eval tests**

Run: `python -m pytest tests/depgraph/test_schema_roundtrip.py tests/depgraph/ -q`
Expected: PASS, including `test_node_has_no_tier_field`.

- [ ] **Step 6: Confirm no stray `Node.tier` reader remains**

Run: `grep -rnE '\.tier\b' src/python_deps/depgraph src/eval --include='*.py' | grep -v pkg_layer`
Expected: no matches (every remaining `.tier` in the repo is `pkg_layer`'s unrelated `Tier`).

- [ ] **Step 7: Commit**

```bash
git add src/python_deps/depgraph/schema.py src/python_deps/depgraph/schedule.py src/eval/graph_repair_ablation/context.py tests/depgraph/test_schema_roundtrip.py
git commit -m "refactor(depgraph): drop the redundant tier axis (layer carries ordering)" -- src/python_deps/depgraph/schema.py src/python_deps/depgraph/schedule.py src/eval/graph_repair_ablation/context.py tests/depgraph/test_schema_roundtrip.py
```

---

### Task 3: Add the `FILE` node type + edge scaffolding

The `import→file` (config) lane needs `FILE` as a node type and `EDGE_RULES` support so a later task can *emit* file nodes without a second schema change. This task adds the scaffolding only — **nothing emits `FILE` yet**, so it is purely additive and sweep-safe. `scope ∈ {runtime,test}` rides the existing `Edge.data` (`schema.py:305`) — no new field.

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (`NodeType` `:19-29`; `EDGE_RULES["requires"]` `:110-114`)
- Test: `tests/depgraph/test_schema_roundtrip.py` (or `tests/depgraph/test_build.py`)

**Interfaces:**
- Produces: `NodeType.FILE`; `File` is a legal `requires` **source** (bridge: `file→import`) and **destination** (satisfied-by: `import→file`).

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schema_roundtrip.py`:

```python
def test_file_node_is_legal_requires_src_and_dst():
    from python_deps.depgraph.schema import (
        DepGraph, Node, Edge, NodeType, EdgeType, Layer, DiscoveredBy,
    )
    imp = Node(id="import:foo", type=NodeType.IMPORT, name="foo",
               layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN)
    fil = Node(id="file:pkg/foo.py", type=NodeType.FILE, name="pkg/foo.py",
               layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN)
    ext = Node(id="import:numpy", type=NodeType.IMPORT, name="numpy",
               layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN)
    g = DepGraph().with_node(imp).with_node(fil).with_node(ext)
    # import -> file : the config-lane satisfied-by (File as dst)
    g = g.with_edge(Edge(src="import:foo", dst="file:pkg/foo.py",
                         relation=EdgeType.REQUIRES, origin="scan"))
    # file -> import : the bridge / container (File as src)
    g = g.with_edge(Edge(src="file:pkg/foo.py", dst="import:numpy",
                         relation=EdgeType.REQUIRES, origin="scan"))
    assert any(e.dst == "file:pkg/foo.py" for e in g.edges)
    assert any(e.src == "file:pkg/foo.py" for e in g.edges)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_schema_roundtrip.py::test_file_node_is_legal_requires_src_and_dst -v`
Expected: FAIL — `NodeType` has no `FILE`; `with_edge` also raises `illegal requires source/destination type 'File'`.

- [ ] **Step 3: Add the enum member**

In `schema.py`, add to `NodeType` (after `CONFIG = "Config"`, `:29`):

```python
    FILE = "File"  # first-party module (config-cured lane); emitted by the classifier stage (later task)
```

- [ ] **Step 4: Allow `File` in the `requires` rule**

In `EDGE_RULES["requires"]` (`schema.py:110-114`), add `"File"` to **both** frozensets:

```python
    "requires": (
        frozenset({"Test", "Project", "Import", "Package", "Service", "Config", "File"}),
        frozenset({"Project", "Import", "Package", "SystemLib", "Tool", "Runtime",
                   "Platform", "Service", "Config", "File"}),
    ),
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/depgraph/test_schema_roundtrip.py::test_file_node_is_legal_requires_src_and_dst -v`
Expected: PASS.

- [ ] **Step 6: Run the depgraph suite (additive — nothing else changes)**

Run: `python -m pytest tests/depgraph/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema_roundtrip.py
git commit -m "feat(depgraph): add FILE node type + requires edge rules for the config lane" -- src/python_deps/depgraph/schema.py tests/depgraph/test_schema_roundtrip.py
```

---

## Deferred — not in this plan

- **Integration step 4 (the coupled file lane):** route-not-drop (`scan.py:152-153`), the classifier stage, config-cure sequencing (`populate.py:57`), `relink._provided_imports` file-dst, the `probe._probe_targets` input-guard, and rewriting `test_construction_boundary.py`. Blocked on the **collision-zone certificate arbitration owner** (design spec §"The collision zone"). Needs its own design→plan cycle before it can be written to this standard.
- **Integration step 5 (delete demoted-tier emission):** follows step 4.
- **Install-lane candidate swap:** already covered by `docs/superpowers/plans/2026-07-17-import-dist-pipeline.md`.
- **certify-by-import hardening:** the `dist name != import name` case (`scikit-learn`→`sklearn`) is left `UNKNOWN` here; the certified fix is `relink`'s `packages_distributions()` map post-editable-install, which belongs with the config lane (step 4).

## Self-review

- **Spec coverage:** Tasks 1–3 implement migration-spec steps 1 (certify-by-import), 5-partial (drop `tier`), and 3-partial (`FILE` scaffolding). Steps 4/5 and the install-lane swap are explicitly deferred with reasons — no silent gap.
- **Placeholder scan:** none — every code step shows complete added code or names exact deletions by line/symbol.
- **Type consistency:** `_project_import_target(project_name, evidence) -> str | None` is defined and called in Task 1; `NodeType.FILE` is added in Task 3 and used in its test; `Node.tier` is removed in Task 2 with all three real consumers updated and `pkg_layer`'s unrelated `Tier` explicitly excluded.
