# Blast radius — removing the import→dist generator path from `depgraph/`

Context: `docs/superpowers/plans/2026-07-04-declared-roots-two-phase-construction.md`.
Scope of the removal (the "✂️ deleted" items in that doc's table, not the
"⬇️ demoted/optional" ones):

- `roots.py:289-299` — the scan gap-fill block inside `select_roots`
  (`for import_node_id, dist_name in package_roots(graph, declared_names): ...`).
- `naming.package_roots` as a *root source* — its only caller (`roots.py`) goes
  away, so it becomes dead code unless relocated into the future repair overlay.
- `relink._drop_superseded_ghosts` — identity-fallback ghost-package cleanup;
  vestigial once no import can ever seed a placeholder Package.

Explicitly **kept** (per the design doc, "⬇️ demoted" not "✂️ deleted"):
`certified_import_links`, `packages_distributions`/`parse_packages_distributions`,
`flag_unresolved_imports`, `resolve_link.link_imports_to_packages` (demoted to
best-effort hint, not touched by this pass), `import_mapping.map_import_to_package`
(demoted from pre-install root authority to untrusted repair-candidate source,
function itself unchanged).

Method: read every production caller of the five removed/demoted symbols, then
read every test file that imports them, then executed them to confirm the
current baseline (79 tests across the 6 most load-bearing files, all green)
before reasoning about what breaks. Repo state at HEAD (`d08b86e`), no changes
made — this is a map, not a patch.

## Headline counts

| bucket | test functions | files |
|---|---|---|
| DELETE (assert dead generator behavior, will fail) | 11 | 4 |
| REWRITE (fixture needs a declared dep; mechanism still valid) | 16 | 4 |
| SURVIVES (no change needed) | ~240 | 9+ |
| Eval scripts needing re-run or structural rework | 3 | 3 |

---

## 1. DELETE — tests that assert the generator behavior itself

These don't just need a fixture tweak; the thing they assert (an import
fabricating/recovering a root) is exactly what's being removed. They should be
deleted, not patched.

- **`tests/depgraph/test_roots.py::test_scanned_curated_import_gap_fills_only_uncovered`**
  — asserts `select_roots` returns `(import:yaml, PyYAML)` for an undeclared-but-
  curated import. Post-removal, `select_roots` never looks at imports at all.
- **`tests/depgraph/test_naming.py`** — **all 7 tests** (`test_curated_native_and_aliased_mappings`,
  `test_new_native_aliases_added_additively`, `test_unmapped_import_yields_no_root`,
  `test_returns_one_pair_per_import_in_node_order`, `test_declared_name_precedence_over_curated`,
  `test_non_import_nodes_are_ignored`, `test_package_roots_omits_unresolved_import`).
  Every one of them tests `naming.package_roots` directly. Once `roots.py` stops
  calling it, `package_roots` has zero production callers — the whole file is
  orphaned. If `package_roots` is literally deleted, all 7 fail on import. If it
  is *relocated* into the Phase-A repair overlay (the design doc's "relocate...
  if reused" escape hatch), these tests need to move with it and be rewritten
  against the new call site (candidate generator, not certified root) — either
  way this file doesn't survive as-is.
- **`tests/depgraph/test_relink.py::test_certified_import_links_drops_superseded_ghost`**
  and **`test_certified_import_links_drops_superseded_versioned_ghost`** — both
  build a `State.MISSING` placeholder Package and assert `certified_import_links`
  removes it once the real provider is certified. That removal *is*
  `_drop_superseded_ghosts`. Deleting the function makes both assertions fail
  (`out.get(package_id("dateutil", None))` stays non-`None`).
- **`tests/eval/graph_fidelity/test_fault_injection.py::test_curated_alias_recovered_by_generator`**
  — asserts `res["generator_recovered"] is True` for a deleted `yaml`→`PyYAML`
  dependency. Post-removal `select_roots` can't recover it either, so this
  becomes `False`, same as `verifier_recovered`. Direct assertion of the
  behavior being deleted.

## 2. REWRITE — fixture needs a declared dependency; the mechanism under test is unaffected

**`tests/depgraph/test_build.py`** — the shared `_make_repo` fixture (line 62)
writes only `app.py` with `import cv2 / from PIL import Image / import psycopg2`
and **no manifest at all**. Today `select_roots` gap-fills all three via the
curated table (`cv2`→opencv-python, `PIL`→Pillow, `psycopg2`→psycopg2). Once
gap-fill is gone, `select_roots` returns `[]`, and `resolve_closure([], ...)`
short-circuits to `([], [])` (confirmed via `test_resolve_closure_empty_roots_returns_empty`
in `test_resolve.py:1088`) — **no packages are ever created**, regardless of what
the `FakeExecutor`'s canned `uv pip compile` output says. Seven tests built on
this fixture assert on the resulting Package/SystemLib/Tool nodes and will fail:
`test_build_produces_all_node_types`, `test_build_requires_topology`,
`test_build_certified_states`, `test_build_native_gaps_are_fresh_probe_nodes_without_a_prior`,
`test_build_discovered_by_stamping`, `test_build_discovered_cycle_per_stage`.
A sibling fixture in the same file, `test_build_ldd_probe_creates_fresh_node_without_a_prior`
(repo = `import cv2` only, no manifest), breaks the same way.
**Fix is mechanical**: add a `pyproject.toml` declaring
`dependencies = ["opencv-python", "Pillow", "psycopg2"]` (and `dateutil` where
used) alongside the existing imports — the stage-ordering/certification logic
these tests actually exercise doesn't change, only the root source does.

**`tests/depgraph/test_build_target_env.py`** — **all 4 tests**
(`test_build_lock_command_carries_detected_platform_tag`,
`test_build_lock_command_reflects_detected_arm_musl_target`,
`test_build_target_python_override_wins_over_detected_probe`,
`test_build_target_platform_override_wins_over_detected_probe`). Its `_make_repo`
docstring is unusually explicit about the dependency this removal breaks:
> "`yaml` is curated (-> PyYAML) so it resolves to a root and triggers `uv lock`;
> an uncurated/undeclared import ... would now be unresolved and never reach
> the resolver, defeating these tests' purpose."
Post-removal `yaml` is *also* unresolved (curated or not, since imports are
never consulted at all) — `roots=[]` — `uv lock` is never invoked — every
`assert lock_calls, "build_dep_graph must attempt uv lock"` fails. Needs a
`pyproject.toml` with `dependencies = ["PyYAML"]`.

**`tests/depgraph/test_roots.py`** — 4 tests keep passing but for the wrong
reason (they exercise a scan/gap-fill path that will no longer exist, so they'd
silently become tautological regression guards for the *declared*-path filter
they were never meant to isolate): `test_scanned_import_gap_fills_only_uncovered`
(the "boto3 is unresolved -> not gap-filled" comment stops being about gap-fill
at all), `test_typing_only_stub_filtered` and `test_junk_and_dunder_filtered`
(fixtures only put `_typeshed`/`__main__`/`_private` in an *imported*, never
*declared*, position — post-removal these never become root candidates by any
path, so the filter itself is never exercised), `test_manifest_scan_dedup_via_normalization`
(declared `Flask` + imported `flask` — dedup now trivially holds because the
imported side never produces a candidate to dedup against). Recommend
rewriting these to declare the junk/typing name directly (mirroring
`test_declared_py2_shim_filtered`, which already does this correctly for the
py2-shim case) so the filter is still under test. Also update the module
docstring (`"""Root selection — manifest-first, scan-gap-filled, non-distribution filtered."""`)
and `roots.py`'s own module docstring (lines 1-23, "2. Scan gap-fill...").

**`tests/depgraph/test_relink.py::test_drop_ghost_never_removes_a_certified_target`**
— survives *vacuously* (with no drop logic at all, nothing is ever removed, so
the "protected target not dropped" assertion trivially holds) but documents a
scenario (`_drop_superseded_ghosts`'s protected-target guard) that no longer
exists in the source. Recommend deleting alongside the two DELETE-bucket ghost
tests rather than keeping a passing-but-meaningless regression guard.

## 3. SURVIVES — no change needed

- **`tests/depgraph/test_relink.py`** — 16 of 19 tests: `parse_packages_distributions`
  (3), `import_to_package_edges` (4), `certified_import_links_adds_edge`,
  `certified_import_links_graceful_on_command_failure`,
  `certified_import_links_keeps_versioned_missing_without_replacement`,
  `certified_import_links_keeps_ghost_without_replacement`, and all 5
  `flag_unresolved_imports` tests. All exercise kept functions with unaffected
  semantics (verified by tracing what `certified_import_links` does once the
  ghost-drop call is removed: `flag_unresolved_imports(new)` directly).
- **`tests/depgraph/test_resolve.py`** — all 79 tests. `link_imports_to_packages`
  (3a, demoted-but-kept) tests build graphs by hand, independent of `select_roots`.
  `resolve_closure` tests pass a **literal** `ROOTS = [("import:cv2", "opencv-python"),
  ("import:PIL", "pillow"), (None, "pandas")]` fixture (line 960) rather than
  calling `select_roots` — this capability (linking an import-tagged root to its
  resolved Package) stays relevant because the future Phase-A repair ladder will
  also emit import-tagged roots with `discovered_by=AUDIT` provenance.
- **`tests/depgraph/test_build.py`** — 12 of 19 tests: `test_build_discovers_subprocess_cli_tools`
  (adb Tool node is subprocess-scan-derived, not root-derived),
  `test_build_empty_repo_yields_only_test_node` (already expects zero
  Import/Package nodes), all 7 Project-node installability tests (manifest-file
  detection, unrelated to roots), `test_build_invokes_certified_relink_stage`
  (asserts stage *ordering* of two probes that both run unconditionally on
  Import nodes from the static scan, regardless of resolve outcome — confirmed
  by tracing that the Import node for `dateutil` is created by `scan_to_nodes`
  independent of `select_roots`), and the two `needed_extras` threading tests
  (`test_build_dep_graph_threads_needed_extras_into_roots_and_resolve`,
  `test_build_dep_graph_default_needed_extras_is_runtime_only` — these only
  assert what kwarg reaches `select_roots`/`resolve_closure`, not what comes
  back out).
- **`tests/depgraph/test_roots.py`** — remaining 19 of 24 (declared-path
  filtering, dedup, per-dep extras, needed_extras gating, all `target_env`
  marker-exclusion tests) — all exercise the declared-manifest path only.
- **`tests/depgraph/test_ldd_probe_docker.py`**, **`test_runtime_node.py`** —
  both already declare a manifest (`pygame` / `requires-python`), unaffected.
- **`tests/test_import_mapping.py`** (10 tests) — tests `map_import_to_package`
  directly; the function is demoted in *role* (pre-install authority →
  untrusted repair candidate) but not touched, deleted, or changed.
- **`tests/depgraph/test_evidence.py::test_build_import_mappings_omits_unresolved`**
  — `evidence._build_import_mappings` is a third, independent consumer of
  `map_import_to_package` that populates `PythonDependencyEvidence.import_package_mappings`.
  Traced downstream: nothing in `build.py`/`roots.py` reads that field — it's
  dead/advisory output already, orthogonal to this removal.
- **`tests/depgraph/test_runtime_parsers.py::test_dispatch_unresolved_import_yields_none_package`**
  — `runtime_classify.py`'s use of `map_import_to_package` is post-hoc failure
  diagnosis (the runtime-feedback loop), not construction-time root selection.
- **`tests/eval/graph_fidelity/test_fault_injection.py`** — 5 of 6 (`test_classify_identity`,
  `test_classify_curated_alias`, `test_classify_other`,
  `test_identity_dep_not_recovered_by_generator`, `test_aggregate_buckets_by_naming`).
- **`tests/eval/graph_fidelity/test_root_selection_ab.py`** (9) and
  **`test_pkg_layer_ab.py`** (15) — both fully survive; every test builds
  `roots`/`current_roots` tuples by hand and never calls the real
  `select_roots`/`scan_to_nodes` pipeline (deliberately, per their own
  docstrings: "pure ... no `python_deps` import").
- **`tests/pkg_layer/test_contract.py`** (17) — `pkg_layer/` is a separate
  module from `depgraph/`, explicitly built "SEPARATE from `python_deps.depgraph`
  (per plan) so the two designs can be A/B-evaluated" (`contract.py:11`). Not
  touched by this removal at all. Note for later: once `depgraph.roots.select_roots`
  becomes declared-only, it and `pkg_layer.contract.select_roots` implement
  *nearly* the same thing in two places — worth a follow-up decision on whether
  `pkg_layer` gets merged in or stays a parallel track, but that's a design
  question, not a test breakage.

## 4. Eval scripts — comparisons that encode the current generator as baseline

- **`scripts/eval/graph_fidelity/root_selection_ab.py`** — no code change
  required (its `score_repo` calls the real `select_roots`, so it will
  automatically reflect the new behavior), but its **entire reason for
  existing** collapses: `partition_roots` splits `select_roots`'s output into
  `generator` (all roots) vs `verifier` (`import_id is None` only) from *one*
  call. Once no root can ever carry a non-`None` import_id, `generator ==
  verifier` for every repo, `total_divergence` is always 0, and `aggregate()`
  always reports `verdict: "identical"`. This is *expected* — it's the design
  doc's own Test Plan item ("re-run after the deletion lands ... confirm
  30/0/30/0 holds in-tree") — but someone needs to actually re-run it against
  the live-probe clones and record the new (trivial) headline, or the stale
  "30 divergent adds" narrative from the prior eval run persists in docs/memory.
- **`scripts/eval/graph_fidelity/pkg_layer_ab.py`** — same shape: `current_select_roots`
  (depgraph, CURRENT) and `new_select_roots` (pkg_layer, NEW) should converge
  to near-identical output post-removal (mod the version-specifier/extras-bracket
  normalization `_bare_name` already handles). Needs a re-run to confirm; also
  worth noting `pkg_layer.contract.select_roots` has **no `target_env` marker
  filtering** at all (no such parameter), while `depgraph.roots.select_roots`
  drops env-marker-false deps — a residual, currently-masked divergence source
  that could surface once the gap-fill noise is gone.
- **`scripts/eval/graph_fidelity/fault_injection.py`** — **needs an actual
  structural rewrite, not just a re-run.** Track B's whole method is: delete a
  declared dep, call `select_roots` once, and split the *same* roots list into
  `generator`/`verifier` via `partition_roots` (imported from `root_selection_ab`)
  to see which "architecture" recovers it. Post-removal there is only one
  architecture — `generator_recovered` and `verifier_recovered` will be
  identical for every fault, always. The script will run to completion and
  emit a well-formatted report/table with no error, so this is silent
  degradation, not a crash (see risk section below). To keep Track B
  meaningful it needs to be repointed at the new Phase-A repair ladder once
  built (measure whether the repair loop recovers the deleted dep), or
  retired with a note that its function is superseded by that loop's own
  tests (`docs/.../2026-07-04-declared-roots-two-phase-construction.md`'s
  "Phase-A fixpoint" test-plan item already covers this ground).

## 5. Callers outside `depgraph/`

- **`src/python_deps/depgraph/advise.py`** — zero references to `select_roots`,
  `package_roots`, `map_import_to_package`, or `link_imports_to_packages`
  (grepped directly). No impact.
- **`src/envstate/orchestrator.py`** — imports only `normalize_package_name`
  from `import_mapping` (line 634), unrelated to root generation. No impact.
- **`src/python_deps/pkg_layer/contract.py`** — imports `_requirement_group`
  (a private helper) from `depgraph.roots`. That helper is not part of the
  removed surface (it's declared-path extras-group parsing, used identically
  by both designs) — no impact.
- **`src/python_deps/depgraph/build.py`** — the one real internal caller of
  all three removed/demoted symbols (`select_roots`, `link_imports_to_packages`,
  `certified_import_links`). This is where the design doc's Phase-A loop
  actually needs to be wired in; out of scope for this map (it's the
  implementation, not the blast radius) but flagged as the load-bearing edit.
- **`src/python_deps/depgraph/resolve.py` / `resolve_link.py`** — `_import_edges`
  and `link_imports_to_packages` keep working unmodified; both are
  root-shape-agnostic (`(import_id | None, dist)` pairs), so they'll handle
  future Phase-A repair roots the same way they handle today's gap-fill roots.
  No caller changes needed, per the design doc's "keep, provisional-only."

## 6. Flags / config gating

- **No `--arm` flag or feature flag gates this path.** Grepped `--arm` across
  `src/`, `scripts/`, `tests/` — all hits are the RAT-benchmark agent-execution
  arms (`v1`, `v1g`, `arm0`, `v3`, etc.), unrelated to root selection. Grepped
  for `ROOT_SELECTION`/`GAP_FILL`/`enable_gap_fill`/`feature_flag` — no
  matches in source (one filename false-positive in `test_config_scan.py`).
- **Practical consequence: this is an all-or-nothing change.** There is no
  runtime switch between "generator" and "declared-only" today (unlike the
  `pkg_layer` parallel-module approach, which *is* how the two designs are
  currently kept A/B-able). Once `roots.py:289-299` is deleted, every caller
  of `build_dep_graph` gets declared-only roots immediately, everywhere,
  with no rollback lever short of `git revert`.

## Most dangerous silent-change risk

**`fault_injection.py` (Track B) degrades from a real A/B measurement to a
tautology that still runs and still prints a clean report.** Nothing errors;
`generator_recovered` and `verifier_recovered` just become permanently equal
(both `False` for identity-named deps, both `False` for curated-alias deps
too, since the recovery mechanism itself is gone). Anyone re-running this
script post-removal without reading its source could misread "gen == ver, 0
regressions" as "recovery behavior preserved," when what actually happened is
"the thing that used to distinguish them was deleted." The real-world mirror
of this: any repo that under-declares a dependency the code imports (e.g.
`import cv2` without `opencv-python` in `pyproject.toml`) goes from *silently
installed via curated-table gap-fill* to *flagged `unresolved`, nothing
installed* — which is the intended, more-honest behavior per the design doc,
but only if the Phase-A repair ladder ships in the same change. If the
gap-fill deletion lands before the repair loop is built, this is a **real
coverage regression for under-declared repos**, not just a test-fixture
artifact — and nothing in the current test suite or eval scripts will fail
loudly to say so (the closest signal, `fault_injection.py`, degrades silently
as described above rather than alarming).
