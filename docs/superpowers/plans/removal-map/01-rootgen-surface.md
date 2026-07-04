# Removal map — root-generator surface (arm-b: import→dist PREDICTION/GENERATOR)

Design doc: `docs/superpowers/plans/2026-07-04-declared-roots-two-phase-construction.md`
Branch: `john-planner-v3-core-autoresearch`
Scope: the generator surface being replaced by declared-roots + certified audit
(Phase A / Phase B). This file maps exactly what to delete/modify/keep and what
breaks, symbol by symbol, with every caller found across `src/`, `tests/`,
`scripts/`, and `docs/`.

Legend: **DELETE** = code removed outright · **MODIFY** = code stays but role/
authority changes · **KEEP-provisional** = code stays unchanged, but the design
doc explicitly reclassifies its trust level (still "keep" for this refactor).

---

## 1. `roots.py` — the scan gap-fill loop

**Definition:** `src/python_deps/depgraph/roots.py:289-299`, inside `select_roots`
(def at `roots.py:235`).

```python
# 2. Scan gap-fill: mapped imports not already covered by a declaration.
for import_node_id, dist_name in package_roots(graph, declared_names):
    node = graph.get(import_node_id)
    module_name = node.name if node is not None else import_node_id
    if _is_non_distribution(module_name):
        continue
    normalized = normalize_package_name(dist_name)
    if normalized in seen:
        continue
    seen.add(normalized)
    roots.append((import_node_id, dist_name))
```

**Does:** appends an `(import_id, dist_name)` root for every scanned `Import`
node that `naming.package_roots` maps to a distribution (declared-match or
curated-table) and that isn't already covered by loop 1 (manifest-declared).
This is the "generator" half of `select_roots`; loop 1 (lines 274-287,
manifest-declared) is KEPT untouched.

**Helper used only by this loop:** none uniquely — `_is_non_distribution`
(line 144) and `normalize_package_name` (imported from `import_mapping`) are
**shared** with loop 1 and stay. The `package_roots` import (line 31,
`from python_deps.depgraph.naming import package_roots`) becomes dead once
this loop is deleted.

**Verdict: DELETE** (lines 289-299 + the now-unused `package_roots` import at
line 31).

**Callers of the enclosing `select_roots` function (signature unchanged, so
these don't break at the call-site level — only behaviorally):**

- `src/python_deps/depgraph/build.py:329` — production wiring (Stage 2). No
  code change needed; the returned root list simply shrinks to declared-only.
- `tests/depgraph/test_roots.py` (24 tests) — see breakdown below.
- `tests/depgraph/test_build.py` (19 tests) — see §6, the real blast radius.
- `scripts/eval/graph_fidelity/root_selection_ab.py` — Track A A/B harness;
  partitions `select_roots` output into `generator` (`import_id` present) vs
  `verifier` (`import_id is None`) sets. Once the gap-fill is deleted, `select_roots`
  IS the verifier set — `partition_roots`'s `divergence` tuple is always empty.
  The script still runs (no crash) but its A/B measurement becomes a no-op.
  This is the harness that already produced the "30 divergent adds, 0 good / 30
  bad" verdict driving this whole refactor.
- `scripts/eval/graph_fidelity/pkg_layer_ab.py` — compares CURRENT
  (`depgraph.roots.select_roots`) vs NEW (`pkg_layer.contract.select_roots`)
  root sets. Post-deletion the two converge (CURRENT becomes declared-only,
  matching NEW's contract-only design) — the eval's "divergence" report goes
  to zero. No crash, but the comparison stops being informative.
- `scripts/eval/graph_fidelity/fault_injection.py:107` — Track B stress test;
  deletes a declared dep from evidence, re-runs `select_roots`, and checks
  whether the "generator" arm re-derives it via the gap-fill. Post-deletion,
  `recovery_flags`' `generator_recovered` becomes structurally identical to
  `verifier_recovered` (both always `False`) — the script still runs but the
  distinction it measures ceases to exist.

**What breaks if removed:** nothing crashes (the function signature and
return shape are unchanged — still `list[tuple[str | None, str]]`). What
changes is *what's in the list*: any repo whose runtime imports aren't
covered by a manifest declaration will simply resolve fewer/no packages for
those imports. See §6 for the concrete, severe instance of this in
`test_build.py`'s core fixture.

**Test-level breakdown, `tests/depgraph/test_roots.py`:**
- `test_scanned_curated_import_gap_fills_only_uncovered` (line 77) — asserts
  `yaml` (undeclared, curated-table match) becomes a root keyed by
  `import:yaml`. **Will FAIL** — this is the gap-fill's core positive-path
  test; must be deleted or rewritten as a negative assertion per the design
  doc's own test plan ("an excluded optional extra whose module is imported
  is NOT re-injected").
- `test_scanned_import_gap_fills_only_uncovered` (line 66) — asserts `boto3`
  (no curated entry, undeclared) does NOT become a root. Already passes today
  (identity fallback was deleted in Phase 2); continues to pass post-deletion
  for the same reason, now vacuously (there's no gap-fill loop to have added
  it either way).
- The remaining ~22 tests in the file exercise loop 1 (manifest-declared),
  the filters (`_is_non_distribution`, `_env_marker_excludes`), and dedup —
  all unaffected.

---

## 2. `naming.py` — `package_roots`

**Definition:** `src/python_deps/depgraph/naming.py:24-61` (the entire
functional content of the 61-line module; lines 1-23 are module docstring).

```python
def package_roots(
    graph: DepGraph,
    declared_names: set[str] | None = None,
) -> list[tuple[str, str]]:
    ...
```

**Does:** for every `Import` node in the graph, resolves it to a distribution
name via (1) declared-manifest-name match, then (2) `import_mapping.
map_import_to_package` (curated table); omits imports that resolve to neither
(`is_unresolved`). This is the exact function the gap-fill loop in §1 calls.

**Imports used only in service of this function:** `is_unresolved`,
`map_import_to_package`, `normalize_package_name`, all from
`python_deps.import_mapping` (naming.py:17-21) — these become dead in this
module once `package_roots` is deleted, though `import_mapping` itself is
untouched (see §3).

**Verdict: DELETE** (whole module — its only production caller is
`roots.py:31`/`:290`, both deleted in §1; `grep` confirms no other production
file imports `python_deps.depgraph.naming`). The design doc explicitly allows
"relocate to the repair overlay if reused" — checked: `pkg_layer/repair.py`
and `pkg_layer/align.py` do NOT import `naming.package_roots`; they call
`import_mapping.map_import_to_package` directly. Nothing to relocate.

**Every caller/reference (whole repo):**
- `src/python_deps/depgraph/roots.py:31` (import) and `:290` (call) — deleted
  together with §1.
- `tests/depgraph/test_naming.py` — **the entire file, all 7 tests**, exists
  solely to test this function: `test_curated_native_and_aliased_mappings`,
  `test_new_native_aliases_added_additively`, `test_unmapped_import_yields_no_root`,
  `test_returns_one_pair_per_import_in_node_order`,
  `test_declared_name_precedence_over_curated`, `test_non_import_nodes_are_ignored`,
  `test_package_roots_omits_unresolved_import`. All become orphaned — either
  deleted with the module or, if the maintainer wants a paper trail, migrated
  as-is onto whatever the repair-overlay's curated-table-lookup function ends
  up being (none currently reuses this exact `(graph) -> [(import_id, dist)]`
  shape; `pkg_layer/repair.py`'s repair ladder operates per-import-name, not
  per-graph).
- Docs referencing it (historical/design context only, not code that runs):
  `docs/superpowers/plans/2026-07-02-phase2-delete-identity-fallback.md`,
  `docs/superpowers/specs/2026-07-02-import-to-distribution-resolution-design.md`,
  `docs/superpowers/specs/2026-06-23-uv-enriched-depgraph.md`,
  `docs/superpowers/loops/graph-fidelity-LEDGER.md`,
  `docs/superpowers/loops/2026-07-03-pkg-layer-improvement-backlog.md`. No
  action needed (historical record), but future readers of these docs should
  know `naming.py` no longer exists.

**What breaks if removed:** nothing outside `roots.py` (§1, already being
deleted) and its own dedicated test file. Zero production blast radius beyond
§1.

---

## 3. `import_mapping` as PRE-INSTALL ROOT AUTHORITY vs POST-INSTALL/repair use

`src/python_deps/import_mapping.py` itself is **not deleted** — the curated
table (`CURATED_IMPORT_TO_PACKAGE`, lines 7-23), `map_import_to_package`
(71-97), `is_unresolved` (44-49), and `normalize_package_name` (62-64) all
survive. What changes is *which callers are allowed to treat its output as an
install-root authority* vs *an untrusted repair candidate*. Design doc: "⬇️
`import_mapping` curated table — pre-install root authority → post-install
repair candidate (untrusted, RECORD-verified)."

### 3a. Root-authority uses (in scope — demoted/deleted)

| Caller | Location | Role | Verdict |
|---|---|---|---|
| `naming.package_roots` | naming.py:57-58 | curated-table lookup feeding the gap-fill's roots | **DELETE** (dies with §2) |
| `resolve_link.link_imports_to_packages` | resolve_link.py:100-108 | curated-table lookup to link an Import to an already-resolved Package | **MODIFY** — see §4 |

### 3b. Non-root-authority uses (out of scope — KEEP, unaffected by this refactor)

| Caller | Location | Role |
|---|---|---|
| `python_deps.evidence._build_import_mappings` | evidence.py:206-225, called at evidence.py:54 | Builds `PythonDependencyEvidence.import_package_mappings` — a purely informational/reporting field (serialized via `models.py:106-107` `to_dict`). **Confirmed dead-end**: `grep` for `.import_package_mappings` shows it is written but never read by `roots.py`/`build.py`/anything in the construction path — it is not consulted as a root authority today. |
| `pkg_layer/repair.py` (rung 2) | repair.py:93-99 | **This IS the design's promoted repair ladder** — "curated alias table" rung, RECORD-verified before acceptance. Already built, matches the design doc's Phase-A repair-ladder spec almost exactly. |
| `pkg_layer/align.py` | align.py:68-70 | Post-install alignment/audit: does a curated alias cover an under-declared import? Diagnostic, not root generation. |
| `depgraph/runtime_classify.py` | runtime_classify.py:80,92 | Runtime-failure classification (`DiscoveredBy.RUNTIME` discovery) — post-cycle, not construction. |
| `normalize_package_name` (bare utility, no ladder) | `roots.py`, `resolve_link.py` (no — uses its own `_canon`), `relink.py`, `probe.py`, `ldd_probe.py`, `service_tables.py`, `config_tables.py`, `runtime_ingest.py`, `envstate/orchestrator.py:634`, `build.py:67` | Pure PEP-503 name normalization, not a root-selection mechanism. Unaffected everywhere. |

**What breaks if `import_mapping` itself were touched:** nothing — it isn't
being modified in this refactor, only *un-wired* from two specific call
sites (§1/§2 delete their calls entirely; §4 keeps its call but changes the
authority framing). `tests/test_import_mapping.py` (7 tests, direct unit
tests of `map_import_to_package`/`is_unresolved`) is entirely unaffected.

---

## 4. `link_imports_to_packages` (resolve_link.py, Stage 3a)

**Definition:** `src/python_deps/depgraph/resolve_link.py:78-119`. Re-exported
unchanged through `src/python_deps/depgraph/resolve.py:107-112` (`from
python_deps.depgraph.resolve_link import ... link_imports_to_packages ...`).

**Does:** for every `Import` node not yet linked to a `Package` node, tries
(a) curated-table mapping (`map_import_to_package`) against existing Package
nodes, then (b) reconciliation-by-own-canonical-name against an
already-present Package. Adds `origin="reconcile"` edges. Complements
`_import_edges` (resolve_link.py:49-75), which only links imports that were
themselves resolver roots (i.e., had a non-`None` `import_id` in the roots
list — this only happens via the gap-fill being deleted in §1, so
post-deletion `_import_edges` never fires for import-sourced roots at all;
manifest-declared roots still carry `import_id=None` so it was already a
no-op for them).

**Called at:** `build.py:375` (Stage 3a) — `graph = link_imports_to_packages(graph)`.

**Verdict: MODIFY (demote to provisional-only).** The design doc marks this
row "keep, provisional-only (4a certifies)" but flags it as **Open decision
#3**: "Keep or drop the pre-install heuristic link (3a) now that 4a
certifies." No code change is strictly required by this refactor alone, but:
- Its **relative importance grows** post-§1-deletion: since `_import_edges`
  (the other Import→Package edge source before install) will now never fire
  for import-derived roots, `link_imports_to_packages` becomes the *sole*
  pre-certification Import→Package linker for curated-alias cases (e.g.
  `cv2`→`opencv-python` when `opencv-python` is declared but the code imports
  `cv2`). Do not delete this without confirming Stage 4a (`certified_import_links`,
  §5-adjacent, itself KEPT/promoted) covers the same ground — it does, but
  only *after* install, so removing 3a would leave a window (between
  `install_closure` and `certified_import_links`) where these edges don't
  exist. Low practical risk since nothing currently reads edges in that
  window, but worth a TODO note rather than a silent deletion.
- Its docstring (resolve_link.py:78-89) should be updated to say "provisional
  heuristic, superseded by Stage 4a's certified relink" rather than reading
  as an authoritative reconciliation pass.

**Callers/tests:**
- `build.py:375` — production wiring, unchanged call site.
- `tests/depgraph/test_resolve.py` — 3 tests: `test_link_imports_to_packages_reconciles_manifest_sourced_packages`
  (line 1832), `test_link_imports_skips_unresolved_mapping` (1876),
  `test_link_imports_reconciles_unresolved_by_own_name` (1901). All construct
  their own hand-built graphs (Package nodes already present) — **unaffected**
  by the §1/§2 deletions; they test this function in isolation.
- `tests/depgraph/test_build.py:150-170` (`test_build_requires_topology`) —
  **indirectly load-bearing**: the `(import_id("cv2"), package_id("opencv-python",
  ...))` edge assertion currently could be satisfied by *either* mechanism
  (gap-fill's `_import_edges` OR Stage 3a's curated-table match), but since
  the fixture (`_make_repo`, see §6) has **no manifest at all**, post-§1-deletion
  `opencv-python` won't even be a Package node any more (no root ever
  resolves it) — so this edge assertion fails regardless of what happens to
  `link_imports_to_packages` itself. This is a §6 problem, not a §4 one, but
  it means §4's test coverage for the curated-table-linking behavior only
  exists when a Package node is already present by some other means (as in
  the isolated `test_resolve.py` unit tests) — the integration-level exercise
  of "3a rescues a curated alias" evaporates along with §6's fixture.

**What breaks if removed outright (not just demoted):** any repo that
declares `opencv-python` in its manifest but only imports `cv2` (no manifest
name literally matching the import) would lose its pre-install Import→Package
edge; Stage 4a (`certified_import_links`, post-install) would still repair
this once `packages_distributions()` runs, so the *end-state* graph is
undamaged, but any code inspecting the graph *between* resolve and install
(none currently does) would see it disconnected. `test_link_imports_*` (3
tests) would need deletion.

---

## 5. `relink._drop_superseded_ghosts`

**Definition:** `src/python_deps/depgraph/relink.py:92-126`. Called from
exactly one place: `relink.py:182`, the last line of `certified_import_links`
— `return flag_unresolved_imports(_drop_superseded_ghosts(new, edges))`.
`certified_import_links` itself, `flag_unresolved_imports`,
`parse_packages_distributions`, and `import_to_package_edges` are **explicitly
KEPT/promoted** per the task brief — only this one inner helper is in scope.

**Does:** after Stage 4a certifies a real Import→Package edge, deletes any
`State.MISSING` Package node that (a) has the same canonical name as the
Import's top-level module, (b) is not itself a certified target of *any*
certified edge, and (c) is superseded by the newly-certified real provider.
This cleans up "ghost" placeholder Package nodes left behind when a
root that came from an import-name guess failed to resolve/build correctly
under its guessed name.

**Verdict: becomes vestigial, not a safe hard-delete yet.** The design doc
says: "with declared-only roots no import ever becomes a placeholder
package, so there are no identity-fallback ghosts to drop; it becomes
vestigial." Two caveats worth flagging explicitly:
1. **The identity-fallback mechanism that most directly produced these ghosts
   was already deleted** in an earlier phase (see memory: "Phase 2
   identity-fallback deletion LANDED", commits `e6177c9..d08b86e`). The two
   existing tests that exercise this path (`test_certified_import_links_drops_superseded_ghost`,
   `test_certified_import_links_drops_superseded_versioned_ghost`) already
   hand-construct the "ghost" `Node` directly in the test fixture rather than
   deriving it from any live pipeline path — meaning this helper may
   *already* be effectively unreachable in production before this refactor,
   and this refactor's §1/§2 deletion removes the last remaining
   theoretical path (a curated-table-mapped gap-fill root whose distribution
   name fails to build/resolve under that exact name).
2. Because a *manifest-declared* root can still independently be a
   `State.MISSING` Package (e.g., a real version conflict, unrelated to any
   import-name guess), `_drop_superseded_ghosts`'s guard conditions (not
   `protected`, `state is MISSING`, canonical-name match to an Import) are
   narrow enough that this should still be safe to leave in place rather than
   hard-deleted — it just becomes dead code that never fires, not a
   correctness hazard if left. Recommend leaving the function in place
   (title it "vestigial" in a comment) rather than deleting it in the same
   PR as §1/§2, since deleting it requires re-verifying none of the 5 tests
   below encode a scenario that's still reachable through the manifest path.

**Every caller/reference:**
- `relink.py:182` (only production call site).
- `tests/depgraph/test_relink.py` — 5 tests directly exercise this via
  `certified_import_links`: `test_certified_import_links_drops_superseded_ghost`
  (131), `test_certified_import_links_drops_superseded_versioned_ghost` (167),
  `test_certified_import_links_keeps_versioned_missing_without_replacement`
  (204), `test_certified_import_links_keeps_ghost_without_replacement` (232),
  `test_drop_ghost_never_removes_a_certified_target` (354).
- Docs: `docs/superpowers/specs/2026-07-02-import-to-distribution-resolution-design.md`
  (lines 77, 219, 246, 357 — discusses known bugs/fixes in this exact
  function), `docs/superpowers/plans/2026-07-01-graph-construction-correctness-fixes.md:603`
  (references it as a pattern to mirror elsewhere).

**What breaks if removed:** if the 5 `test_relink.py` tests above are deleted
along with the function, nothing in production breaks (per the vestigial
argument above) — but if the function is deleted while any test still
constructs a MISSING-ghost fixture expecting it to be dropped, those 5 tests
fail with an `AttributeError`/`ImportError` or an assertion failure (ghost
node still present). Recommend: keep the function + its 5 tests as
regression coverage for the "don't leave dangling superseded placeholders"
invariant, note it as unreachable-in-practice, and revisit hard deletion only
after confirming (via the Phase-A repair loop's own tests) that no new path
reintroduces import-guessed placeholder Packages.

---

## 6. Collateral finding — `tests/depgraph/test_build.py`'s core fixture IS the generator arm

**This is the riskiest single item in this removal map.** `_make_repo`
(test_build.py:62-66) writes **only** `app.py` (`import cv2`, `from PIL
import Image`, `import psycopg2`) — **no `pyproject.toml`, no `setup.py`, no
manifest of any kind.** `evidence.declared_dependencies` is therefore empty
for this fixture, so today's `opencv-python` / `Pillow` / `psycopg2` Package
nodes exist **only** because §1's gap-fill maps `cv2`→`opencv-python`,
`PIL`→`Pillow` (curated key `pil`), `psycopg2`→`psycopg2` via
`CURATED_IMPORT_TO_PACKAGE`.

Once §1 is deleted, `select_roots(_make_repo(...), graph)` returns `[]`
(no declared deps, no gap-fill) → `resolve_closure` hits its `if not roots:
return [], []` early-return (`resolve.py:264-265`) → **zero Package nodes,
zero SystemLib nodes, zero Tool nodes** are ever produced for this fixture,
regardless of the canned `uv pip compile`/install/probe executor responses
wired up in `_make_executor()` (lines 69-97), because those commands are
never even invoked.

**Tests that call `_build(tmp_path)` / `_make_repo(tmp_path)` and assert on
the resulting Package/SystemLib/Tool layer (will FAIL outright, not just
degrade):**
- `test_build_produces_all_node_types` (line 105) — asserts existence of
  `opencv-python`, `numpy`, `Pillow`, `psycopg2` Package nodes and
  `libGL.so.1`/`pg_config` gap nodes.
- `test_build_requires_topology` (150) — asserts the full Test→Import→
  Package→SystemLib/Tool edge topology.
- `test_build_certified_states` (173) — asserts `State.SATISFIED`/`MISSING`
  on Package/Import/SystemLib/Tool nodes that won't exist.
- `test_build_native_gaps_are_fresh_probe_nodes_without_a_prior` (190) —
  asserts `discovered_by`/`check_command`/`fix_candidates` on SystemLib/Tool
  nodes that depend on `opencv-python`/`psycopg2` having been installed.
- `test_build_discovered_by_stamping` (210) — asserts `discovered_by` on
  `cv2` import and `numpy` package.
- `test_build_discovered_cycle_per_stage` (221) — asserts `discovered_cycle`
  on the same nodes.

That's **6 of 19** tests in `test_build.py` (roughly a third of the file's
integration coverage) that hard-fail the moment §1 is deleted, not because
of any logic bug but because the fixture never had a manifest and was
silently relying on curated-table gap-fill to populate it. Two more tests
(`test_build_dep_graph_threads_needed_extras_into_roots_and_resolve` at 468,
`test_build_dep_graph_default_needed_extras_is_runtime_only` at ~499) reuse
`_make_repo` but only assert on spied `needed_extras` plumbing values, not
graph content — these should keep passing.

**Fix required alongside §1's deletion (not optional, blocking):** give
`_make_repo` a real `pyproject.toml` declaring `opencv-python`, `Pillow`, and
`psycopg2` as runtime dependencies (matching what the design doc's Phase A
expects: declared intent, not import-derived guesses), OR — if the intent is
to keep exercising the "under-declared repo" path — wire the new Phase-A
repair loop into `build_dep_graph` in the same change and let repair
(`discovered_by=AUDIT`) re-add these three via the curated-table repair rung
(§3b, already built in `pkg_layer/repair.py`). Either fix changes the
fixture/test file, not `roots.py` itself, but it must land in the same PR or
the entire `depgraph` test suite goes red on `test_build.py`.

---

## Summary table

| Symbol | Location | Verdict | Tests directly hit |
|---|---|---|---|
| `roots.py` scan gap-fill | roots.py:289-299 (+ import line 31) | **DELETE** | 2 in test_roots.py (1 fails, 1 vacuous-pass) |
| `naming.package_roots` | naming.py:24-61 (whole module) | **DELETE** | 7 in test_naming.py (all orphaned) |
| `import_mapping` as root authority (2 call sites) | naming.py:57-58 (dies with above); resolve_link.py:100-108 | **DELETE** / **MODIFY** | see rows above / below |
| `link_imports_to_packages` (Stage 3a) | resolve_link.py:78-119, called build.py:375 | **MODIFY** (demote to provisional; open decision to drop) | 3 in test_resolve.py (unaffected in isolation) |
| `relink._drop_superseded_ghosts` | relink.py:92-126, called relink.py:182 | **KEEP-provisional** (vestigial, not hard-deleted this round) | 5 in test_relink.py (keep as regression coverage) |
| Collateral: `test_build.py` fixture | test_build.py:62-66, `_make_repo`/`_build` | **MUST FIX in same PR** | 6 of 19 tests in test_build.py fail outright |
