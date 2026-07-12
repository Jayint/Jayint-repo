# Local-Module Resolution — Three-Way Diagnosis

**Date:** 2026-07-13
**Status:** Landed (Tasks 1-7 committed; this document rewritten by Task 8 to describe
what was actually built, and to lock the safety invariant as a permanent test —
`tests/depgraph/test_construction_boundary.py`)
**Supersedes:** `2026-07-13-module-node-layer-design.md` (withdrawn — see §6, unchanged
by this rewrite)
**Scope:** `src/python_deps/depgraph/repo_modules.py` (new module), `src/python_deps/depgraph/diagnose.py`,
`src/envstate/orchestrator.py`, `src/envstate/repair_scope.py`

---

## Superseded within this document

This spec originally proposed **two fixes**: (1) exclude `NodeType.IMPORT` from
`schedule._is_actionable`, and (2) replace `scan._local_module_names` wholesale with a
sys.path-accurate walk, consumed by both construction (`scan_to_nodes`) and diagnosis
(`is_local_import`). Neither shipped as specified. Both were refuted during
implementation:

- **Fix 1 (exclude `NodeType.IMPORT` from `_is_actionable`) — DROPPED.**
  `orchestrator.py` routes tasks by `target_node_ids` into `run_structured_repair`,
  which hands the LLM a typed patch scope — so it *can* propose
  `pip install azure-mgmt-cdn` for a stuck Import node. Excluding `IMPORT` from the
  scheduler frontier would delete the strictly-more-capable repair channel for the
  exact case Fix 2 exists to serve. Separately, the "documented live waste" this
  spec originally cited from `orchestrator.py:1160` turned out, on inspection, to be
  the *rationale comment for the fast-termination fix* on the lines immediately below
  it (a verified test pass now short-circuits the frontier before an over-predicted
  Import node can burn `attempt_cap` cycles) — already mitigated by a different,
  already-landed mechanism, not by excluding `IMPORT`.
- **Fix 2 (replace the broad set wholesale in `scan_to_nodes`) — NARROWED.** The
  original design assumed the corrected, sys.path-accurate name set was safe to
  swap in everywhere the old broad set was read. typer's `items` disproves that:
  `items` is a real PyPI distribution, and `tutorial001/items.py` makes `items` a
  false positive for "external" under the precise rule applied to construction — a
  false-external there reaches Phase-A's identity candidate ladder, which will
  `ACCEPT` and install the real `items` package. Over-breadth (calling `items`
  local, even though it isn't importable as a bare top-level) is the *correct,
  conservative* bias for construction, because construction's failure mode for a
  false-external is an actual wrong install, not just a missed audit edge. The
  corrected, precise rule went to **diagnosis only** — see §1.

The rest of this document describes the three-way design that replaced both fixes,
as actually built and committed (Tasks 1-7).

---

## 1. The three-way design that was built

Three functions now answer three different questions about the same repo tree, and
each is used by exactly one consumer:

| function | question it answers | measured size | consumer |
|---|---|---|---|
| `scan.local_module_names` (unchanged) | "every `.py` stem or `__init__` dir basename anywhere in the tree" — deliberately over-broad | wagtail **757**, typer **161**, netbox **564** | `scan.scan_to_nodes` (**construction**) |
| `repo_modules.top_level_names` (new) | "which top-level names does this repo actually make importable, per sys.path semantics" — precise | wagtail **4**, typer **35**, netbox **17** | `diagnose.RepoContext.local_names` (**diagnosis**, via `orchestrator.py`) |
| `repo_modules.stem_collisions` (new) | the DIFFERENCE between the two, mapped to the real dotted module name | e.g. wagtail `azure` → `wagtail.contrib.frontend_cache.backends.azure` | `diagnose.RepoContext.collisions` (**diagnosis**, evidence only — never a verdict) |

`repo_modules.top_level_names` implements the basedir/PEP-420 rule: from a `.py`
file, climb while the directory has `__init__.py`, **or** while the directory's
*parent* has `__init__.py` (a namespace-package clause — without it,
`src/flask/sansio/app.py`, which has no `__init__.py` of its own, would mint the
bogus top-level `app` instead of `flask`). The first directory failing both is the
sys.path root; the dotted name is the path from there, and the top-level is its
first segment.

`repo_modules.stem_collisions` is exactly the set difference `local_module_names −
top_level_names`, restricted to valid Python identifiers (the only shape a
`ModuleNotFoundError`'s top-level segment can take), each mapped to the real dotted
module name that produces it — or, for the one residual case (a repo root that
itself has `__init__.py`), to the repo-relative path of the `__init__.py` that
produced the name.

**Diagnosis now routes three ways** (`diagnose.Locality`, `diagnose.classify_locality`):

- **`REPO_MODULE`** — a genuine importable top-level (`top_level_names` hit) →
  `Mode.REPO_INTERNAL_REF` → give up, no repair attempt. Correct: the graph cannot
  close a repo-internal reference by adding a node.
- **`STEM_COLLISION`** — a broad-walk stem that is *not* an importable top-level
  (`stem_collisions` hit) → `Mode.AMBIGUOUS`, carrying the real dotted module path as
  evidence, **routed to the repair loop, never silently dropped**. This case is not
  decidable from a static tree walk — `wagtail...backends.azure` and
  `tutorial001.items` are structurally identical (a `.py` stem colliding with a real
  PyPI name); what distinguishes them is whether the failing importer ran as a
  script or was loaded as a package, which is a runtime fact visible only in the
  traceback.
- **`EXTERNAL`** — matches neither set → normal `ModuleNotFoundError` → package-mapper
  classification → `Mode.ENVIRONMENT` → install, as before.

Both sets are computed from the same tree walk pruned by the identical
`scan.SKIP_WALK_DIRS` (promoted from the private `_SKIP_WALK_DIRS`, with a
back-compat alias kept for callers that still import the old private name) — if the
two walks pruned differently, `stem_collisions`'s subset arithmetic would be wrong.
`repo_modules.repo_modules` is uncapped (unlike
`import_graph._iter_python_files`'s `MAX_PYTHON_FILES = 1000`): netbox has **1,184**
`.py` files, and a capped walk would drop its core `extras` app from the module set,
classify it external, and let Phase-A install the real PyPI package named `extras`.

---

## 2. The safety mechanism: collision evidence via the `constraints` channel

`python_deps.depgraph.patch_gate.validate_proposal` is **purely structural** — it
checks that a proposed patch is well-formed, not that its content is *wise*. It will
admit `pip install items` without complaint; nothing at the gate layer knows `items`
collides with a repo file. The fix therefore does not live in the gate. It lives in
what the repair loop tells the LLM before it proposes anything.

When `orchestrator._repair_or_route` diagnoses a `STEM_COLLISION` (via
`diagnose.classify_locality`), it builds a `constraints` entry —
`{"local_module_collision": "<import> is NOT an importable top-level module of this
repo, but the repo DOES define <real dotted name>. This is either a genuinely
missing external package OR a sys.path/PYTHONPATH problem with a sibling import. DO
NOT install a PyPI package named <import> unless the traceback shows the importer
was loaded as an installed package (not run as a script)."} — and threads it through
the previously-unused `constraints` channel:

```
run_structured_repair → build_repair_scope → RepairScope.constraints → render_repair_scope
```

`render_repair_scope` renders `RepairScope.constraints` verbatim into the prompt
text handed to `build_agent.propose`. This is the only place in the pipeline where
the collision evidence — the real dotted module name, and the instruction not to
install a same-named PyPI package unless the traceback proves it was loaded as an
installed package — reaches the agent. A plain external `ModuleNotFoundError` (no
collision) gets no such constraint; only names that classify as `STEM_COLLISION` do.

---

## 3. Expected effect — measured

**Local-name set sizes** (broad → precise), verified twice against real checkouts
under `outputs/graph_fidelity/_smoke_services/` and `outputs/build_script_eval/_smoke/`:

| repo | broad (`local_module_names`) | precise (`top_level_names`) | notable collision(s) |
|---|---|---|---|
| wagtail | 757 | **4** | `azure` → `wagtail.contrib.frontend_cache.backends.azure` |
| typer | 161 | **35** | `items` → `tutorial001.items`; `lands`/`reigns`/`towns` → `tutorial003.*`; `users` → `tutorial001.users` |
| netbox | 564 | **17** | `extras`/`dcim`/`utilities`/`circuits`/`ipam` all stay **local** — netbox has 1,184 `.py` files, so a 1000-file-capped walk would drop `extras` and install the real PyPI package named `extras` |

`jupyterhub`'s bare `traitlets` correctly is **not** a top-level (`traitlets` is a
declared PyPI dependency; `jupyterhub/traitlets.py` is `jupyterhub.traitlets`).

**Unchanged (verified by review against the code):**

- `setup.sh`: **byte-identical.** Construction (`scan.scan_to_nodes`,
  `scan._local_module_names`) is completely untouched by this plan — see §1's table
  and the out-of-scope list below.
- Import node set: **unchanged.** `scan_to_nodes` still reads the same broad set.
- Eval metrics (`unresolved_imports`, root-selection A/B): **unchanged.** Both read
  construction output only; neither imports `diagnose`.
- The runtime-ingest path is also unchanged. `make_diagnostic_classifier` returns a
  `Discovery` only for `Mode.ENVIRONMENT`; both `REPO_INTERNAL_REF` and `AMBIGUOUS`
  return `None`, so `ingest_runtime_failures` mints no node either way. The fix
  operates solely through `_repair_or_route`, which reads `d.mode` directly.

**Changed — where a collision previously hit `REPO_INTERNAL_REF` (a pure no-op), it
now falls through to `run_structured_repair`:**

1. **LLM turns**: 0 → up to `MAX_REPAIRS_PER_BLOCK` (5) attempts × up to 2
   `propose()` calls, drawn from the run-global `_repair_turns` budget (seeded from
   `max_cycles`, default 12).
2. `_cycle_had_env_repair = True` is set, which resets `_residual_stall` — the
   counter that would otherwise end the run via `GIVEUP_RESIDUAL`. A repeatedly
   failing collision keeps the run alive longer than the old no-op did.
3. `PatchGateRecord` tracing fires (it did not before).
4. The graph, `manual_blocks`, and `known_invalid` can now be mutated on this path.
5. `_budget_exhausted` can now flip on this path.
6. The container can now be touched on this path.

**Budget risk, stated plainly.** netbox has **547** collision names, wagtail
**753**. Those are *static exposure*, not incidence — a collision costs nothing
unless it actually raises `ModuleNotFoundError` at runtime, and the smoke corpus
surfaced exactly **one** (wagtail's `azure`). But there is **no collision-specific
cap**: they compete for the same global `_repair_turns` budget as any real repair. A
repo with several simultaneously-failing collisions (e.g. multiple extras-gated
deps) could burn the run's repair budget on them. If that shows up, cap
collision-driven repairs separately — do not widen the budget.

**Startup cost.** The router now runs three full `os.walk` traversals where there
was one before (`top_level_names`, `stem_collisions`, and the broad walk
`stem_collisions` itself calls). Measured: wagtail ~1.85s vs ~0.46s; netbox ~0.6s vs
~0.18s. One-time per run, not per cycle. If it matters, have `stem_collisions`
return both sets from a single walk.

---

## 4. Out of scope (deliberate)

- **`scan.scan_to_nodes` / `scan._local_module_names`.** Unchanged, byte-for-byte
  except the `_SKIP_WALK_DIRS` → `SKIP_WALK_DIRS` rename (plus back-compat alias).
  Its over-breadth is the correct conservative bias — see §1 and the "Superseded"
  note above.
- **`schedule._is_actionable`.** The `IMPORT` exclusion is dropped entirely.
  `orchestrator.py` routes tasks by `target_node_ids` into `run_structured_repair`,
  so the LLM *can* repair an Import obligation — excluding it would delete the more
  capable channel for exactly the case this plan serves.
- **`import_graph.collect_project_local_modules` / `SOURCE_ROOT_NAMES`.** Still used
  by `scan_imports` for its `project_local`/`stdlib`/`external` classification,
  which feeds `pkg_layer` and eval consumers. Deleting it is a separate change with
  its own blast radius.
- **`scan._in_scope_files`.** A behavior-changing drop filter; separate change,
  separate eval.
- **A `NodeType.MODULE` graph layer.** Withdrawn — see §6.

---

## 5. Testing

- **`tests/depgraph/test_repo_modules.py`** — table-driven unit tests of the
  basedir/PEP-420 rule (flat repo, `src`-layout, namespace packages, repo root with
  `__init__.py`, etc.) and of `stem_collisions`'s set-difference and precedence
  rules.
- **`tests/depgraph/test_repo_modules_real_repos.py`** — regression tests against
  real checkouts, one per bug a prior design shipped or nearly shipped: wagtail's
  `azure` (silent give-up), typer's `items` (would-be wrong install),
  jupyterhub's `traitlets` (shadowing submodule), netbox's `extras`/`dcim`/etc. (the
  1000-file-cap landmine), plus a subset-invariant check
  (`top_level_names(repo) <= local_module_names(repo)`). Cases `pytest.skip` when
  their checkout is absent under `outputs/`, but
  `test_required_real_repo_checkouts_are_present` fails loudly if any of the four
  required checkouts are missing, so the gap can never pass silently.
- **`tests/depgraph/test_diagnose_types.py` / `test_diagnose_router.py` /
  `test_diagnose_reconciliations.py` / `test_diagnose_ingest_guard.py` /
  `test_diagnose_residual_mint.py`** — the `Locality`/`Mode` classification and
  routing logic in `diagnose.py`, including the three-way `classify_locality` split
  and the REPO_MODULE/STEM_COLLISION/EXTERNAL precedence (`REPO_MODULE` wins over
  `STEM_COLLISION`).
- **`tests/envstate/test_repair_routing.py`** — end-to-end through `run_v3`:
  `test_repo_internal_ref_bundle_skips_repair` (a real top-level never spends a
  repair turn), `test_stem_collision_bundle_spends_a_repair_turn` (the `azure` bug,
  end-to-end, against a real synthetic tree), `test_collision_evidence_reaches_the_repair_prompt`
  (the `items` bug: asserts the rendered repair prompt contains the real dotted name
  and a "do not install" instruction), and
  `test_plain_external_gets_no_collision_constraint` (a genuine external failure
  gets no collision constraint). Also
  `test_single_repair_call_site_and_no_block_emit_in_source`, a source-assertion
  test in the same style as this plan's new construction-boundary guard.
- **`tests/depgraph/test_construction_boundary.py`** (Task 8, new) — the permanent
  guard rail: `inspect.getsource` over `scan`, `build`, and `roots` asserts none of
  `top_level_names`, `stem_collisions`, `repo_modules` appear as real code (comment
  prose is stripped before the check, so `scan.py`'s own cross-reference comment in
  `SKIP_WALK_DIRS` does not false-positive it), plus a check that
  `scan_to_nodes` still reads `_local_module_names(repo_path)`. This is the
  single most safety-critical invariant in the whole plan: the precise set is
  diagnosis-only, and a future unrelated change that starts calling it from
  construction would silently start installing wrong PyPI packages
  (typer's `items`, netbox's `extras`). A test, not a one-time `grep`, is what
  stops that from landing unnoticed.

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
