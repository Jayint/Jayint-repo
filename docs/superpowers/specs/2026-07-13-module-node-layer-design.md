# Module Node Layer — Design

> ## ⛔ WITHDRAWN — 2026-07-13
>
> **Superseded by [`2026-07-13-local-module-resolution-fixes.md`](./2026-07-13-local-module-resolution-fixes.md).**
> Retained for the record; **do not implement.**
>
> Adversarial review refuted three load-bearing claims:
>
> 1. **The headline motivation (§1.2 item 3) is false.** "A declared package whose
>    install fails routes to `REPO_INTERNAL_REF` and cannot be repaired" — a pip
>    failure produces neither `module_not_found` nor `import_name_error`, so it routes
>    to `AMBIGUOUS` and repair **runs** (executed, 3/3). It is also self-contradictory:
>    `setup.sh` runs `set -Eeuo pipefail`, so a failed install aborts before tests run.
>    §1.2 also mis-cites the frontier drop — `REPO_INTERNAL_REF` does not touch
>    `_residual_ids`; only `RESIDUAL` does.
> 2. **The rule in §2 is broken on PEP 420.** `src/flask/sansio/` has no `__init__.py`
>    but is a real subpackage (`flask.sansio.app`). The walk stops there and mints
>    top-level **`app`** — the exact generic-name pollution §1.2 indicts. 12 such dirs
>    across 6 of 21 repos, so §9's "if zero, move on" precondition is false.
> 3. **The safety property in §5 is broken.** With `MAX_PYTHON_FILES = 1000`, netbox
>    (1,184 `.py`) loses `extras` from the module set → classified external → Phase-A
>    `ACCEPT`s the **real PyPI package `extras`** → `setup.sh` gains a **wrong**
>    package. Reintroduces the wrong-guess class driven 6→0 by
>    `phase2-identity-fallback-deletion`.
>
> Also: recovered packages on this corpus are **zero** (all 12 are declared and already
> installed); no consumer traverses `CONTAINS`/`IMPORTS` edges; and
> `diagnose.is_local_import` **already** does the top-level projection §2.1 presents as
> the design's unifying insight — only its populator was ever wrong.

**Date:** 2026-07-13
**Status:** WITHDRAWN (see above)
**Scope:** `src/python_deps/` (construction), `src/envstate/` (scheduler), Python provider only

---

## 1. Why

### 1.1 The coherence gap

The dependency graph models everything about the environment *except the repository*.

`scan.scan_to_nodes` (`src/python_deps/depgraph/scan.py:143-181`) keeps only `external`
imports. Project-local imports are dropped on the floor:

```python
if finding.classification != "external":
    continue
...
if name in local_names:          # local_names = _local_module_names(repo_path)
    continue
```

So external imports get an in-graph provider chain (`import:yaml → pkg:PyYAML`), while local
imports are **deleted from existence** and then reconstructed *outside* the graph as a
`frozenset[str]` side-table (`scan.local_module_names`, an `os.walk` at orchestrator startup,
threaded through as `RepoContext.local_names`).

Locality is therefore a fact *about* the graph rather than a fact *in* it. Every import also
hangs directly off the single `Test` goal node, which is a lie for a runtime import — the test
does not require `yaml`, `src/foo.py` does.

### 1.2 The bug this exposes (the real motivation)

`scan._local_module_names` (`scan.py:75-99`) harvests **bare basenames from anywhere in the
tree** — every `.py` stem and every directory containing `__init__.py`:

```python
if "__init__.py" in filenames:
    names.add(os.path.basename(dirpath))
for fname in filenames:
    if fname.endswith(".py") and fname != "__init__.py":
        names.add(fname[:-3])
```

Measured on the five repos in `outputs/graph_fidelity/_smoke_services/`:

| repo | `.py` files | names harvested | correct top-levels |
|---|---|---|---|
| wagtail | 1,255 | **761** | 19 |
| netbox | 1,184 | **564** | 23 |
| healthchecks | 650 | **400** | 3 |
| searxng | 397 | **383** | 6 |
| jupyterhub | 113 | **102** | 21 |

The harvested set includes every Django migration stem (`0001_initial`, `0002_admin`, …) and
every generic module name (`app`, `base`, `auth`, `utils`).

Since Python 3, `import traitlets` inside `jupyterhub/` does **not** resolve to
`jupyterhub/traitlets.py` — absolute imports mean that file is `jupyterhub.traitlets` and
nothing else. But the bare stem `traitlets` is harvested, so the graph believes `traitlets` is
repo-local.

**14 imports are misclassified as repo-local across these five repos; 12 of them are real,
declared PyPI distributions:**

| repo | file on disk | real module name | bare stem harvested | package killed |
|---|---|---|---|---|
| jupyterhub | `jupyterhub/traitlets.py` | `jupyterhub.traitlets` | `traitlets` | `traitlets>=4.3.2` |
| netbox | `netbox/utilities/jinja2.py` | `utilities.jinja2` | `jinja2` | `Jinja2==3.1.6` |
| netbox | `netbox/utilities/mptt.py` | `utilities.mptt` | `mptt` | `django-mptt==0.18.0` |
| netbox | `netbox/utilities/markdown.py` | `utilities.markdown` | `markdown` | `Markdown==3.10.2` |
| netbox | — | — | `jsonschema` | `jsonschema==4.26.0` |
| netbox | — | — | `graphql` | `strawberry-graphql==0.320.0` |
| wagtail | `wagtail/telepath.py` | `wagtail.telepath` | `telepath` | `telepath` |
| wagtail | `.../frontend_cache/backends/azure.py` | `…backends.azure` | `azure` | `azure` (optional) |
| wagtail | — | — | `embedly` | `embedly` (optional) |
| wagtail | — | — | `sendfile` | `sendfile` (optional) |
| healthchecks | `hc/lib/statsd.py` | `hc.lib.statsd` | `statsd` | `statsd==4.0.1` |
| healthchecks | `hc/integrations/apprise/` | `hc.integrations.apprise` | `apprise` | `apprise==1.9.9` |

The remaining two (searxng's `extended_types` and `search`) are repo-internal oddities, not
PyPI distributions. They still become Import nodes after the fix — and correctly surface as
`unresolved`, which is the honest outcome. Import-node growth is therefore **+14/repo-set**;
recovered *packages* is **12**.

**Consequences, in ascending severity:**

1. **The verification edge is missing.** No Import node means `relink` never builds the
   certified `import:jinja2 → pkg:Jinja2` edge. The graph installs Jinja2 and has *no evidence
   it is importable*. The audit layer has a hole exactly where the name collision is.

2. **Phase-A has a blind spot.** `build._phase_a_fixpoint` (`build.py:389-395`) audits
   non-optional Import nodes against the resolved closure's RECORD union and repairs
   under-declarations. An import that never becomes a node can never be audited — so
   under-declaration repair is disabled for precisely the names most likely to be confusing.

3. **The repair loop silently gives up — and this fires regardless of declaration.** The same
   over-broad set feeds `diagnose.is_local_import` via `RepoContext`
   (`orchestrator.py:731-735`). At runtime, `ModuleNotFoundError: jinja2` on netbox routes to
   `Mode.REPO_INTERNAL_REF` → **no repair, dropped from the frontier**
   (`orchestrator.py:762-793`). If Jinja2 is declared but its *install fails* — version
   conflict, build error, bad wheel — **the loop is structurally forbidden from repairing it**
   and concludes the repo has a source bug.

(3) is the headline. It is not an under-install; it is a silent, unfixable give-up on a
declared dependency.

### 1.3 Why the two existing implementations cannot be patched in place

There are two, and they **disagree**, because each covers the other's failure:

- `import_graph.collect_project_local_modules` (`import_graph.py:35-48`) — **under**-detects.
  Only looks at the repo root plus `SOURCE_ROOT_NAMES = {"src", "lib", "python"}`, so it misses
  netbox's `dcim`/`extras`/`utilities`, which *are* bare-importable because `netbox/` is the
  sys.path root. Returns 2-6 names per repo.
- `scan._local_module_names` (`scan.py:75-99`) — **over**-detects, as measured above. Exists
  precisely to cover the first one's misses (its docstring says so), at the cost of killing 14
  real packages.

They disagree on 399-760 names per repo. Adding a third rule to patch the second would repeat
the mistake. There is one correct rule, and it belongs in the graph.

---

## 2. The rule

**A module's importable name is not a function of its path. It is a function of its path
relative to a sys.path root — and a repo has several.**

The rule is pytest's own basedir algorithm, which is also CPython's:

> From any `.py` file, walk up while the directory contains `__init__.py`. The first directory
> *without* one is the **sys.path root**. The **dotted name** is the path from that root.

```
src/flask/app.py           src/flask has __init__, src does not   → root=src/    name=flask.app
tests/blueprintapp/__init__.py  tests has no __init__             → root=tests/  name=blueprintapp
tests/test_app.py                                                 → root=tests/  name=test_app
foo.py (flat repo)                                                → root=.       name=foo
jupyterhub/traitlets.py    jupyterhub has __init__                → root=.       name=jupyterhub.traitlets
netbox/utilities/jinja2.py utilities has __init__, netbox does not→ root=netbox/ name=utilities.jinja2
```

This is not a heuristic. It is what the interpreter does. It subsumes every case the two
current implementations special-case:

- **src-layout** — falls out (`src/` has no `__init__.py`, so it *is* the root).
- **`package_dir = {"": "lib"}`** — falls out, same reason. No table needed.
- **flask-style bare-name test fixtures** — falls out (`tests/` has no `__init__.py`).
- **netbox's non-standard `netbox/` source root** — falls out. `SOURCE_ROOT_NAMES` is deleted.
- **the traitlets/jinja2/mptt collisions** — fixed, because `jupyterhub/traitlets.py` is
  `jupyterhub.traitlets`, whose *top-level* is `jupyterhub`, not `traitlets`.

### 2.1 Identity, and the top-level projection

Module **identity** is `(sys_path_root, dotted_name)` — the id space admits the same file under
several roots, per design decision (a).

**But note: the basedir walk is single-valued.** Walking up from a file terminates at exactly
one root, so Phase 1 mints **one module node per `.py` file**. The multi-name case decision (a)
anticipated does not arise from the walk itself — it arises only from explicit `sys.path`
manipulation (a `conftest.py` doing `sys.path.insert(...)`, or a `.pth` file), which this spec
does **not** model. The two-part id is chosen so that support can be added later without an id
migration; no alias machinery is built now (§10).

The **locality lookup is top-level**, because Python's is:

```python
is_local(import_name) == import_name.split(".", 1)[0] in {m.dotted.split(".")[0] for m in modules}
```

This is exactly the fix. `traitlets` is not a top-level module of jupyterhub — `jupyterhub`
is. Dotted names carry the structure (`contains`/`imports` edges); their top-level projection
drives locality. One rule, both jobs.

---

## 3. Node model

```
project:netbox
  --contains--> module:utilities.jinja2   [path=netbox/utilities/jinja2.py, root=netbox/]
        --imports--> import:jinja2        --requires--> pkg:Jinja2     (external)
        --imports--> module:utilities.utils                            (local, in-graph)
```

**`NodeType.MODULE`**
- `tier = 0` — joins Test/Project/Import on what `schema.py:33` already calls "the demand
  side, not a tier".
- `state` — permanently `UNKNOWN`. A module is **not an obligation**, so it carries no
  certification. `State` is a certification axis; giving 1,000 structural nodes a state would
  make the axis meaningless. (Precedent: the `Project` node is uncertified today.)
- `check_command = None`, `setup_commands = ()`, `strength = SOFT`.
- `provenance` — the file path. `data` — `{"sys_path_root": "netbox/"}`.

**New `EdgeType.CONTAINS` and `EdgeType.IMPORTS`** — deliberately *not* overloaded `requires`.
`requires` carries certification semantics: it drives the frontier, tier descent, and the
closure (`EDGE_RULES`, `schema.py:108-120`). Structural edges must enter none of them.
`EDGE_RULES` gains two entries.

### 3.1 Module nodes cannot reach `setup.sh` — by construction, not by convention

Three independent existing gates reject them, each keyed on something a module node lacks:

1. `emit.partition` filters on an allowlist (`emit.py:25-29, 110`):
   ```python
   _INSTALLABLE: tuple[NodeType, ...] = (NodeType.PACKAGE, NodeType.SYSTEM_LIB, NodeType.TOOL)
   ...
   if n.type not in _INSTALLABLE:
       continue
   ```
   `MODULE` joins Import/Test/Project/Runtime/Platform, which are *already* excluded.
2. `emit._is_emittable` opens with `if node.state is not State.MISSING: return False`. A node
   with no `check_command` is never certified (`certify.py:79` skips it), so it can never be
   `MISSING`.
3. The renderer emits from `setup_commands`. Module nodes have none.

Leaking a module node into `setup.sh` would require adding `MODULE` to `_INSTALLABLE` **and**
giving it a `check_command` **and** giving it `setup_commands`.

### 3.2 The Project node gains its missing check

`_add_project_node` (`build.py:194-207`) sets `pip install -e .` as `setup_commands`
(`populate.py:30`) but **no `check_command`**. Per `certify.py:59,79` it is therefore never
certified — it sits `UNKNOWN` forever and nothing ever proves the editable install worked.

The Project node gains `check_command = python -c "import <top_level>"`. The module layer is
what makes this **computable** — the sys.path roots yield the repo's real importable
top-levels (`wagtail`, `hc`, `searx`, `jupyterhub`) instead of a guess from `[project] name`.

The module layer *informs* the obligation without *being* one.

---

## 4. Import → distribution mapping (unchanged)

The module layer **does not touch the mapper.** It only changes *which names reach it*. Stated
here so the spec is self-contained.

**Forward (post-install) — ground truth, one subprocess.** `relink.certified_import_links`
(`relink.py:183-201`) runs `importlib.metadata.packages_distributions()` **once** for the whole
graph and reads the real `{top_level: [dists]}` map out of the installed environment. This is
the **only** place a certified `Import → Package` edge is minted. The graph does not guess the
edge; it observes it. Cost is O(1) subprocesses, not O(imports).

**Backward (pre-install) — guess, then verify.** Phase-A cannot use the above, so
`repair.generate_candidates` proposes candidates (`normalize_candidates`: identity, lowercased,
dashed, `python-X`, `X-python`; plus the 14-entry `CURATED_IMPORT_TO_PACKAGE` table), and
`repair.record_grounds` (`repair.py:146-173`) grounds each by reading *that candidate's* wheel
`top_level.txt`:

- exactly one confirm → `ACCEPT` → AUDIT root → resolved → installed
- two or more → `AMBIGUOUS` → **no node**
- zero → `UNRESOLVED` → **no node**

The identity fallback is still a *candidate*; what was deleted (per `phase2-identity-fallback-
deletion`) is *blind acceptance* of it. That distinction is why wrong-guesses went 6→0.

**After this change, every import has exactly one provider, and the kind is the diagnosis:**

```
import:jinja2  --requires-->    pkg:Jinja2      external  (observed)
import:hc.lib  --provided_by--> module:hc.lib   local     (graph topology)
import:app     (no provider)                    unresolved (honest — no node fabricated)
```

---

## 5. Does this change package detection?

**Roots: no.** `select_roots` (`roots.py:289-360`) reads `evidence.declared_dependencies` only
— pyproject `[project.dependencies]` / optional-deps / PEP 735 `[dependency-groups]` /
`[tool.poetry.dependencies]`, `setup.cfg`, `setup.py` (AST-parsed, never executed),
`requirements*.txt` (following `-r`), `constraints*.txt`. **No lockfile is read.**
`resolve_closure` writes a *throwaway* pyproject containing exactly those roots and runs
`uv lock` fresh, so the closure is their transitive expansion and nothing else. `build.py:489`:
*"imports never generate roots; graph is passed but not consulted for root selection."*

**Phase-A: yes — this is a real import→package channel.** An import that is non-optional and
absent from the resolved closure's RECORD union becomes an AUDIT root if the candidate ladder
returns exactly one RECORD-confirmed dist, and is then **installed**. Package detection is
therefore *manifests-first with imports as a bounded, evidence-gated repair signal* — not
manifests-only.

**Safety property.** Every basedir top-level is either a `.py` stem or an `__init__` directory
basename — both of which the broad walk already harvests. Therefore:

> `new_local ⊆ old_local` — the new rule is strictly **narrower**.

The Import node set can only **grow**. No package can be lost. `setup.sh` can only gain lines.

**Measured effect on the five smoke repos: none.** All 14 recovered names are *declared*
(`traitlets>=4.3.2`, `Jinja2==3.1.6`, `django-mptt==0.18.0`, `statsd==4.0.1`, …), so they are
already in the resolved closure's RECORD union, are excluded from Phase-A's `missing` list, and
add no root. **`setup.sh` is byte-identical on this corpus.** That is a real predicted value,
not a tautology.

**On a repo that under-declares a colliding name, `setup.sh` *will* gain a package.** That is
the fix working, and such a diff must be inspected, not assumed.

---

## 6. Cost and blast radius

### 6.1 Module nodes are nearly free

Not in `_INSTALLABLE`, no `check_command` (never certified → zero subprocesses), no
`setup_commands` (never rendered), not in the frontier. They cost graph memory
(~1,255 nodes on wagtail; the scan already caps at `MAX_PYTHON_FILES = 1000`) and nothing else.

### 6.2 Import nodes are NOT free — and the fix creates 14 more

This is the entire cost of the change, and it attaches to the *correctness fix*, not to the
module nodes.

- **`certify.certify_all` walks by `Layer`**, and Import nodes live in `Layer.NAMING`, which is
  in `EXECUTION_LAYER_ORDER` (`certify.py:30-39`). Every Import node gets its own
  `python -c "import X"` subprocess (`certify.py:47-115`).
- **`probe.import_probe`** (`probe.py:231-280`, called at `build.py:641`) runs the same command
  again, per import name.
- A single construction pass therefore hits every Import node **twice** (`build.py:641`, then
  `build.py:737`).
- The **live loop** re-certifies **every cycle** (`certify_refresh`, `depgraph_live.py:39-64`);
  `react_repair` keeps `Layer.NAMING` (`react_repair/entry.py:11-19`) so it pays it **every
  step, up to `max_steps=30`**.

→ **+14 Import nodes ≈ +28 subprocesses per construction, +14 per cycle, +14 per react step.**

### 6.3 The sharp edge: Import nodes pollute the scheduler frontier

`schedule._is_actionable` (`schedule.py:34-51`) excludes `SERVICE` and `CONFIG` by type — but
**not `IMPORT`**. A `MISSING` Import node with a `check_command` therefore passes, lands in
`scheduler_frontier`, and is handed to the LLM as a real `Task` up to `attempt_cap` (default 3)
times. The orchestrator already documents this waste verbatim (`orchestrator.py:1160-1164`):

> *"an over-predicted OPTIONAL node (a phantom tool, an unused optional import) keeps the
> frontier non-empty and next_decision hands it out attempt_cap times WITHOUT re-checking
> tests — burning ~3 cycles per stuck node even though the suite already passes."*

This is a **pre-existing category error**: *you cannot install an import.* You install the
package that provides it. An Import node is an audit signal, not an actionable obligation.

The 14 recovered names are declared, so they certify `SATISFIED` and are harmless. But
wagtail's optional integrations (`azure`, `embedly`, `sendfile`) will be `MISSING` and would
each burn up to 3 LLM cycles.

**Therefore `NodeType.IMPORT` must be excluded from `_is_actionable` BEFORE the Import set
grows.** See Phase 2 — the ordering is load-bearing.

### 6.4 Eval metrics will shift

- `src/eval/language_package_eval/run_ours_pkg.py:43-56` reports `unresolved_imports` /
  `unresolved_runtime_imports` straight off `NodeType.IMPORT` node flags.
- `root_selection_ab.py`, `pkg_layer_ab.py`, `fault_injection.py` all score recall/precision
  against a **gold imports** map keyed by Import-node names.

Growing the Import set moves all of them. The 30/0/30/0 root-selection result needs
re-baselining. **Phase 3 must not land mid-benchmark.**

---

## 7. Phasing

The ordering is not cosmetic — Phase 2 must precede Phase 3 or the fix amplifies §6.3.

### Phase 1 — Module layer, additive (zero behavior change)

- New `src/python_deps/depgraph/modules.py`: one pure function
  `repo_modules(repo_path) -> tuple[ModuleDef, ...]`, `ModuleDef = (sys_path_root, dotted, path)`.
  No network, no container, no fixpoint. Sole input: the filesystem.
- `NodeType.MODULE`, `EdgeType.CONTAINS`, `EdgeType.IMPORTS`; `EDGE_RULES` extended;
  `ids.module_id()`.
- Wire as **Stage 0** of `build_dep_graph`, before `scan_to_nodes`.
- `is_local_import` **still reads the old frozenset.** Nothing else changes.
- **Gate:** `setup.sh` byte-identical on all five smoke repos, and on the eval corpus.

### Phase 2 — Take Import nodes out of the scheduler frontier

- Exclude `NodeType.IMPORT` from `schedule._is_actionable`.
- **Prerequisite check:** grep existing run traces for whether an Import node reaching the
  frontier has *ever* produced a successful repair. If it has, narrow the exclusion to
  `data["optional"] is True` imports only rather than all imports.
- **Gate:** no regression in EBSR/ESSR on the eval corpus; cycle counts flat or lower.

### Phase 3 — Flip the guard (the bug fix)

- `is_local_import(name, frozenset)` → `graph.has_module(name)` (top-level projection, §2.1).
- Delete `scan._local_module_names`, `import_graph.collect_project_local_modules`, and
  `SOURCE_ROOT_NAMES`.
- Import set grows by ~14/repo. `traitlets`/`jinja2`/`mptt`/`statsd` stop being misrouted to
  `REPO_INTERNAL_REF`.
- **Gate:** `setup.sh` diff is empty on the five smoke repos (predicted, §5), or every diff is
  explained as a recovered under-declaration.
- **Re-baseline** `unresolved_imports` and the root-selection A/B numbers.
- **Sequencing:** lands *after* the gold-set rebuild on `rat_python50`, never during it.

### Phase 4 — Project node certification

- `check_command = python -c "import <top_level>"`, derived from the module layer's sys.path
  roots.

---

## 8. Testing

- **Unit — the rule.** Table-driven over the six cases in §2, plus: flat repo, `src`-layout,
  `package_dir` remap, a file reachable under two roots, a repo with no packages at all.
- **Unit — the collisions.** `jupyterhub/traitlets.py → jupyterhub.traitlets` (top-level
  `jupyterhub`, so `traitlets` is **external**); same for netbox `jinja2`/`mptt`/`markdown`,
  wagtail `telepath`, healthchecks `statsd`. These are regression tests for the actual bug and
  should be named for it.
- **Unit — the safety property.** Property test: for a generated repo tree,
  `basedir_top_levels ⊆ scan._local_module_names(...)`. Guards §5's "can only grow".
- **Integration — byte-identical.** Phase 1 asserts identical rendered `setup.sh` across the
  smoke corpus. Phase 3 asserts empty-or-explained diff.
- **Integration — no leakage.** Assert no `MODULE` node appears in `emit.partition()`'s
  certified/emittable/frontier sets, and that none is certified (all `UNKNOWN`).
- **Integration — the diagnosis fix.** Given a netbox graph and a synthetic
  `ModuleNotFoundError: jinja2`, assert `diagnose()` returns `Mode.ENVIRONMENT`, **not**
  `Mode.REPO_INTERNAL_REF`. This is the headline bug; it gets a named test.

---

## 9. Risks

**PEP 420 namespace packages (the one case where the rule is *wrong*, not approximate).** A
namespace package directory has no `__init__.py`, so the basedir walk reads it as a sys.path
root and its children are misnamed (`mynamespace/mypkg/x.py` → `mypkg.x`, but the real name is
`mynamespace.mypkg.x`). Under decision (a) — multiple names per file — this degrades to a
*missing alias* rather than a wrong one, so an import of `mynamespace.mypkg` would be
classified external rather than local. Mitigation is a cross-check against declared packages in
pyproject. **Do not build for this until it is measured on the corpus** — count namespace
packages across the eval repos first; if zero, log and move on.

**Non-standard `package_dir` remaps** (`{"mypkg": "some/other/dir"}`) also break the walk.
Rarer than PEP 420. Same disposition: measure, then decide.

**Node count.** ~1,255 module nodes on wagtail. Free in the graph object, but anything that
serializes the whole graph verbatim would balloon. No such consumer exists today
(`graph_repair_ablation/context.py:31-33` already excludes demand-side nodes from the LLM
prompt). If one appears, it needs an explicit projection — see §10.

---

## 10. Out of scope (deliberate)

- **The LLM projection.** Nothing serializes module nodes today. Build the projection when a
  consumer actually needs it, not before.
- **`scan._in_scope_files`** (`scan.py:64`), the path-segment heuristic that drops imports seen
  only in `examples/`/`docs/`/`build/`. It is a *behavior-changing drop filter*; replacing it
  with real module reachability is a separate change with its own eval. This spec deletes **two
  of the three** duplicate implementations, not three.
- **Alias machinery** for files reachable under multiple sys.path roots (§2.1). The basedir walk
  is single-valued, so this only arises from explicit `sys.path` manipulation in `conftest.py`
  or a `.pth` file. The two-part module id reserves room for it; nothing is built now.
- **Test-reachability pruning** of the module set. Static reachability misses dynamic imports,
  plugins, and conftest; an over-prune would drop a real dependency.
- **Non-Python ecosystems.** Rust/Node providers are untouched.
