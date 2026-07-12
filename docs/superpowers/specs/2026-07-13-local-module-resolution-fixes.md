# Local-Module Resolution — Two Fixes

**Date:** 2026-07-13
**Status:** Spec, pending approval
**Supersedes:** `2026-07-13-module-node-layer-design.md` (withdrawn — see §6)
**Scope:** `src/python_deps/depgraph/{scan,schedule}.py`, `src/python_deps/import_graph.py`

Two small, independent fixes. **No new node types, no new edge types, no new
construction stage.** The first is a live-waste bug fix that depends on nothing. The
second corrects an over-broad heuristic that is currently a latent landmine.

---

## 1. Fix 1 — Import nodes must leave the scheduler frontier

### The defect

`schedule._is_actionable` (`schedule.py:34-51`) excludes `SERVICE` and `CONFIG` by
type, but **not `IMPORT`**:

```python
return (node.state is State.MISSING
        and service_ok
        and node.type is not NodeType.CONFIG
        and bool(node.check_command)
        and _dependencies_satisfied(graph, node)
        and not _is_emittable(graph, node, _conflicted_ids(graph)))
```

Every Import node carries a `check_command` unconditionally (`scan.py:132`). An
unresolved or uninstalled-optional Import has no outgoing `REQUIRES` edge, so
`_dependencies_satisfied` is vacuously true. `_is_emittable` returns `False` for any
non-`PACKAGE`/`SYSTEM_LIB`/`TOOL` type (`emit.py:84-100`), so that gate passes too.

A `MISSING` Import node therefore lands in `scheduler_frontier` and
`graph_scheduler.next_decision` (`graph_scheduler.py:97-139`) hands it to the LLM as a
real `Task`, up to `attempt_cap` (default 3) times. `packet_to_task`
(`graph_scheduler.py:47-53`) renders the goal literally as:

> *"Satisfy obligation 'azure' (Import, tier 0): make the host check
> `python -c "import azure"` succeed."*

**This is a category error. You cannot install an import.** You install the package
that provides it. An Import node is an audit signal, not an actionable obligation.

The orchestrator already documents the resulting waste verbatim
(`orchestrator.py:1160-1164`):

> *"an over-predicted OPTIONAL node (a phantom tool, an unused optional import) keeps
> the frontier non-empty and next_decision hands it out attempt_cap times WITHOUT
> re-checking tests — burning ~3 cycles per stuck node even though the suite already
> passes."*

This fires **today**, on existing unresolved imports (searxng's `extended_types` and
`search` are live examples). It is not hypothetical and it is not introduced by Fix 2.

### The change

Add an explicit type exclusion to `_is_actionable`:

```python
and node.type is not NodeType.IMPORT
```

### The prerequisite — do NOT skip this

**Before landing a blanket exclusion, determine whether an Import node reaching the
frontier has ever produced a successful repair.** Local traces do not answer this — v3
and react run traces live on the VM, not in this checkout. Grep them.

Mitigating evidence that the risk is bounded: when the frontier is empty and tests
still fail, `next_decision` (`graph_scheduler.py:131-139`) falls back to a generic
`_discover_task()` — *"install or provide whatever the running code actually needs…
until tests pass."* So excluding `IMPORT` removes a **targeted framing**, not the only
repair channel.

- If the traces show the Import-frontier path has produced repairs → narrow the
  exclusion to `node.data.get("optional") is True` only.
- If they show it has not → exclude all `IMPORT`.

### Why this is the net win

It is a pure `schedule.py` change. It depends on nothing else in this spec, it fixes a
documented live waste, and it can land immediately.

---

## 2. Fix 2 — Correct the local-module rule, in place

### The defect

`scan._local_module_names` (`scan.py:75-99`) harvests **bare basenames from anywhere in
the tree** — every `.py` stem and every directory containing `__init__.py`:

```python
if "__init__.py" in filenames:
    names.add(os.path.basename(dirpath))
for fname in filenames:
    if fname.endswith(".py") and fname != "__init__.py":
        names.add(fname[:-3])
```

Since Python 3, `import traitlets` inside `jupyterhub/` does **not** resolve to
`jupyterhub/traitlets.py` — absolute imports mean that file is `jupyterhub.traitlets`
and nothing else. But the bare stem `traitlets` is harvested, so the graph believes
`traitlets` is repo-local.

Measured, this set is 400-757 names per repo, including every Django migration stem
(`0001_initial`, `0002_admin`) and every generic module name (`app`, `base`, `utils`).

It is consumed in two places:

1. `scan_to_nodes` (`scan.py:159`) — drops the Import node entirely.
2. `diagnose.is_local_import` via `RepoContext` (`orchestrator.py:731-735`) — routes a
   `ModuleNotFoundError` to `Mode.REPO_INTERNAL_REF`, which returns the graph unchanged
   (`orchestrator.py:766-774`) — a **silent, unconditional give-up, no repair attempted**.

There is a second, contradictory implementation:
`import_graph.collect_project_local_modules` (`import_graph.py:35-48`), which
**under**-detects — it only looks at the repo root plus
`SOURCE_ROOT_NAMES = {"src", "lib", "python"}`, so it misses netbox's `dcim`/`extras`
(bare-importable, because `netbox/` is the sys.path root). The two disagree on 379-760
names per repo. Each exists to paper over the other's failure mode.

### What it actually costs today — measured, and smaller than it looks

**This must be stated honestly, because it drives the priority.**

12 declared PyPI distributions are misclassified as repo-local across the five smoke
repos (`traitlets`, `Jinja2`, `django-mptt`, `Markdown`, `jsonschema`,
`strawberry-graphql`, `telepath`, `azure`, `embedly`, `sendfile`, `statsd`, `apprise`).

**Packages this fix recovers: zero.** Roots are manifest-declared
(`roots.select_roots`, which never reads imports), so all 12 are already installed. For
8 of them, `import jinja2` simply succeeds, no `ModuleNotFoundError` is ever raised, and
the guard is never consulted. The loss is one missing certified
`import:X → pkg:Y` audit edge, which nothing consumes for a decision. **Cosmetic.**

**Phase-A under-declaration repair recovers nothing either.** For a colliding name to be
lost there it must be imported, collide with a local stem, be absent from the closure,
*and* be resolvable by the candidate ladder to exactly one RECORD-confirmed dist. On
this corpus, zero names satisfy all four.

**One instance is real, and it arrives through a mechanism worth naming: extras-gating.**
wagtail declares `azure-mgmt-cdn` in `[project.optional-dependencies] testing`
(`pyproject.toml:63-70`). `roots.py:187-188` admits an optional dependency only if its
group is in `needed_extras`, which defaults to `frozenset()` (`build.py:444`) and is
never populated by any orchestrator call site. So azure is **never installed**, and
`wagtail/contrib/frontend_cache/tests.py:5` does an unguarded module-top
`from azure.mgmt.cdn import CdnManagementClient` → collection-time
`ModuleNotFoundError: azure` → `is_local_import("azure", …)` is `True` →
`REPO_INTERNAL_REF` → **silent give-up.** Executed and confirmed:

```
wagtail  ModuleNotFoundError: azure   ('azure' in local_names: True)
  WITH bug   -> Mode.REPO_INTERNAL_REF   repair_runs=False
  WITHOUT    -> Mode.ENVIRONMENT         repair_runs=True
```

Even this buys a **repair turn, not an install** — the deterministic candidate ladder
cannot map `azure → azure-mgmt-cdn` (`normalize_candidates` yields
`{azure, python-azure, azure-python}`; `CURATED_IMPORT_TO_PACKAGE` has no entry).

### So why fix it

Because it is a **live landmine with a cheap disarm**, not because it pays off today:

- The failure mode is a *silent, unconditional give-up* on a real environment problem.
  There is no error, no retry, no escalation — the loop simply concludes the repo has a
  source bug and stops.
- The trigger is any import that is (a) not installed and (b) collides with any `.py`
  stem anywhere in the repo. Extras-gating and under-declaration both produce (a);
  Django-style repos produce (b) constantly.
- We are about to run 50 repos. This is a tail risk, and the fix is ~60 lines.

### The rule

Terminate the walk at a **real sys.path root** — a directory that is neither a package
nor inside one:

> From a `.py` file, climb while the directory has `__init__.py`, **or** while the
> directory's *parent* has `__init__.py` (a directory without `__init__.py` whose parent
> has one is a **PEP 420 namespace package**, not a sys.path root). The first directory
> failing both is the sys.path root; the dotted name is the path from there. Never climb
> above the repo root.

The parent-climb clause is load-bearing and was missing from the withdrawn spec. Without
it, `src/flask/sansio/` (a real subpackage with **no** `__init__.py`, imported as
`flask.sansio.app`) reads as a sys.path root and mints top-level **`app`** — reproducing
the exact generic-name pollution being fixed.

| file | root | dotted name | top-level |
|---|---|---|---|
| `src/flask/app.py` | `src/` | `flask.app` | `flask` |
| `src/flask/sansio/app.py` *(no `__init__.py`)* | `src/` | `flask.sansio.app` | `flask` |
| `jupyterhub/traitlets.py` | `.` | `jupyterhub.traitlets` | `jupyterhub` |
| `netbox/utilities/jinja2.py` | `netbox/` | `utilities.jinja2` | `utilities` |
| `netbox/extras/models/x.py` | `netbox/` | `extras.models.x` | `extras` |
| `tests/blueprintapp/__init__.py` | `tests/` | `blueprintapp` | `blueprintapp` |
| `hc/lib/statsd.py` | `.` | `hc.lib.statsd` | `hc` |

The locality predicate is unchanged — `diagnose.is_local_import` (`diagnose.py:49-58`)
**already** does the top-level projection:

```python
return import_name.split(".", 1)[0] in local_names
```

Only its **populator** changes. `is_local_import` keeps its `frozenset[str]` signature;
`RepoContext` is untouched; no call site moves.

### Implementation constraints (normative — the safety property depends on all three)

1. **Uncapped walk.** Do **not** use `import_graph._iter_python_files` — it caps at
   `MAX_PYTHON_FILES = 1000`. netbox has 1,184 `.py` files. Under the cap, netbox's
   core app `extras` (bare-imported at 270 sites) falls out of the module set, is
   classified **external**, reaches Phase-A, and — because `extras` is a **real PyPI
   distribution** whose wheel confirms the module — is `ACCEPT`ed as an AUDIT root and
   **installed**. That reintroduces exactly the wrong-guess class that
   `phase2-identity-fallback-deletion` drove 6→0. It is also nondeterministic:
   `os.walk` order is filesystem-dependent, so *which* 1,000 files survive varies by
   machine.
2. **Prune exactly `scan._SKIP_WALK_DIRS`** (case-insensitively), not
   `import_graph.EXCLUDED_DIRS`. The two sets differ
   (`{docs, examples, samples, benchmarks, scripts, tools, .github, …}`), and using the
   latter produces 199 subset violations across 21 repos.
3. **Never climb above the repo root** (guards a repo whose root has `__init__.py`).

Promote `_SKIP_WALK_DIRS` to a shared constant so the two consumers cannot drift apart
again.

### The safety property — proven, and it is conditional

> Every corrected top-level is either a `.py` stem or an `__init__` directory basename
> **within the same visited file set**. The old walk harvests both. Therefore
> `new_local ⊆ old_local`: the rule is strictly **narrower**, the Import set can only
> **grow**, and no package can be lost.

**This holds only under all three constraints above.** It is not a property of the rule;
it is a property of the rule *plus its implementation*. Violate constraint 1 and netbox
installs a wrong package.

**Measured across all 21 repos on disk** (`outputs/graph_fidelity/_smoke_services/` +
`outputs/build_script_eval/_smoke/`):

| check | result |
|---|---|
| subset violations (uncapped, `_SKIP_WALK_DIRS`) | **0 / 21** |
| bogus top-levels removed by the parent-climb | **25** |
| the 12 collisions stay external | **PASS, 4/4 repos** |
| real repo modules stay local (netbox `extras`/`dcim`, flask `flask`, healthchecks `hc`) | **PASS, 4/4 repos** |

Local-name set sizes: wagtail **757 → 4**, netbox **564 → 17**, healthchecks **400 → 3**,
searxng **383 → 6**, jupyterhub **102 → 20**, flask **61 → 32**.

### What gets deleted

- `import_graph.collect_project_local_modules` and `SOURCE_ROOT_NAMES`.
- `scan._local_module_names`'s body (replaced; the public alias `local_module_names`
  keeps its signature).

`scan._in_scope_files` (`scan.py:64`) is **not** touched — it is a behavior-changing
drop filter and belongs to a separate change with its own eval.

---

## 3. Expected effect

`setup.sh` is expected to be **byte-identical** on the smoke corpus: all 12 recovered
names are manifest-declared, so they are already in the resolved closure's RECORD union,
are excluded from Phase-A's `missing` list, and add no root. Any diff is a recovered
under-declaration and must be inspected, not assumed.

The Import node set grows ~10% (140 → 154 across the five smoke repos; +16% on wagtail,
+3% on jupyterhub). Each Import node costs `python -c "import X"` twice per construction
(`certify_all` at `build.py:737`, `import_probe` at `build.py:641`), once per live cycle,
and once per react step. That is ~+28 sub-second subprocesses per construction — real but
modest. **Fix 1 must land first**, or the new `MISSING` imports (wagtail's `azure`,
`embedly`, `sendfile`) each burn up to 3 LLM cycles in the frontier.

**Eval metrics.** `run_ours_pkg.py:43-56` reports `unresolved_imports` off
`NodeType.IMPORT` and will shift. The `root_selection_ab` / `pkg_layer_ab` harnesses were
**re-run with a patched rule and their aggregates did not move** — the collisions this
fixes do not occur in their 16-repo library corpus. (Both harnesses have separately
drifted from their committed snapshots for unrelated reasons — `root_selection_ab.json`
records 30/0/30/0; a fresh run yields 19/0/19/0. Worth a look, unrelated to this spec.)

---

## 4. Phasing

1. **Fix 1** — `schedule._is_actionable` excludes `IMPORT`. Gated on the VM-trace
   prerequisite (§1). Zero dependencies; lands immediately.
2. **Fix 2** — the corrected rule. Gated on `setup.sh` byte-identical (or explained) on
   the smoke corpus.

Fix 2 grows the Import set, so **Fix 1 precedes it**. Fix 2 shifts `unresolved_imports`,
so it lands **after** the gold-set rebuild on `rat_python50`, never during it.

---

## 5. Testing

- **Rule, table-driven** — the seven rows in §2's table, plus: flat repo, `src`-layout,
  `package_dir` remap, repo root with `__init__.py`, repo with no packages.
- **PEP-420 regression** — `src/flask/sansio/app.py` must yield top-level `flask`, not
  `app`. Named for the bug.
- **Collision regression** — `jupyterhub/traitlets.py` must leave `traitlets`
  **external**; likewise netbox `jinja2`/`mptt`/`markdown`, wagtail `telepath`,
  healthchecks `statsd`.
- **Local-module regression** — netbox `extras`/`dcim`/`utilities` must be **local**.
  This is the guard against the wrong-package install (§2, constraint 1).
- **Safety property** — `new_local ⊆ old_local` over the corpus. Note this asserts
  *narrowness only*; it passes on a rule that still misnames namespace packages, so it is
  **not** sufficient on its own — the PEP-420 and collision tests are the correctness
  oracle.
- **Diagnosis fix** — given wagtail and a synthetic `ModuleNotFoundError: azure`, assert
  `diagnose()` returns `Mode.ENVIRONMENT`, not `Mode.REPO_INTERNAL_REF`.
- **Frontier fix** — a `MISSING` Import node must not appear in `scheduler_frontier`.
- **Integration** — `setup.sh` byte-identical across the smoke corpus.

---

## 6. Withdrawn: the module-node layer

The predecessor spec proposed a `NodeType.MODULE` layer
(`project --contains--> module --imports--> import --requires--> package`) for graph
coherence. It is withdrawn. Adversarial review found:

- **The headline motivation was false.** The claim "a declared package whose install
  fails routes to `REPO_INTERNAL_REF` and cannot be repaired" does not hold — a pip
  failure produces neither `module_not_found` nor `import_name_error`, so it routes to
  `AMBIGUOUS` and repair **runs**. It is also self-contradictory: `setup.sh` uses
  `set -Eeuo pipefail`, so a failed install aborts before tests run.
- **The rule as specified was broken** on PEP 420 (flask `sansio`), inventing the very
  generic top-levels it set out to eliminate.
- **The safety property was broken** by `MAX_PYTHON_FILES`, and would have installed the
  wrong PyPI package (`extras`) into netbox.
- **No consumer exists** for `CONTAINS`/`IMPORTS` edges, as the spec itself conceded.
  Every edge walk in the codebase filters on `EdgeType.REQUIRES`.
- **`is_local_import` already performs the top-level projection** the design presented as
  its unifying insight. Only the populator was ever wrong.
- Cost: ~600-900 LOC of representation with no reader, plus `DepGraph.with_node` is an
  O(n) linear rescan, making ~1,255 node insertions quadratic.

The coherence goal remains legitimate. Revisit it when something real needs to traverse
those edges — driven by that consumer's requirements, not ahead of them.
