# Graph Module Reorganization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reorganize the flat, five-concern `src/python_deps/depgraph/` into a layered `graph/` package (+ evict services/orchestration to `runtime/`, repair to `repair_agent/`), tailored to the two-lane model — as a **behavior-preserving** refactor that the pass-repo sweep can prove green at every step.

**Architecture — the key structural decision:** the refactor splits into **Phase 0** (a series of small, individually behavior-preserving *decoupling* commits that fix backward cluster-edges on the CURRENT flat layout) and **Phase 1** (the single atomic file-move + import-rewrite commit). Phase 0 is what makes Phase 1 tractable and low-risk: each decoupling removes one cross-cluster edge *before* the directories exist, so the move itself is a pure relocation with the arrows already pointing the right way. Phase 0 is Stage-C-independent and can land now; Phase 1 is gated on a frozen tree (after Stage C + the in-flight grounding work).

**Grounding:** every number and edge below was verified by four read-only research agents against the current tree (design: `docs/superpowers/specs/2026-07-17-graph-module-architecture-reorg-design.md`). Their corrections to that design are folded in here.

## Global Constraints

- **Behavior-preserving is THE gate — and it is DEFINED as a golden-output differential** (see "Validation harness" below), not a vague "still green." Every Phase-0 commit and the Phase-1 move must leave `pytest` green AND produce a **byte-identical `graph.to_dict()` + `setup.sh` per pass-repo** vs the frozen baseline. Phase 0 changes are pure relocations of symbols within the *current* package; Phase 1 is a pure move. No logic changes ride along.
- **Scoped commits ONLY** (shared branch): `git add <exact paths>`; **`-m` before `--`**; never `git add -A`; no `Co-Authored-By`.
- **Shims are UNSAFE for ~35 identity-sensitive sites.** `monkeypatch.setattr(mod, …)` / `patch.object(mod, …)` bind the shim, so patching silently no-ops against the real moved module (research 1 cat. 5 — it would corrupt `fault_injection.py`'s eval numbers with no error). Those sites MUST be rewritten to the real new module in the atomic commit; a re-export shim is allowed ONLY for plain `from X import symbol` value imports, for one window.
- **The blast radius is 830 import sites** (163 production + 667 test), 288 of them function-local (52 production), across 240 files. Completeness of the import rewrite is the pass/fail criterion for Phase 1.
- **The file target is ~45–47, not ~40** (research 3): `resolve.py` is 1,057 lines alone; `build_deps` merged is 1,075 — neither collapses under the 800 ceiling.
- **Phase 1 is gated on a FROZEN tree** — after Stage C (which deletes demoted-tier emission and finalizes the lanes) and after the in-flight `integrate`/`exec_trace` grounding work lands. Phase 0 has no such gate.

---

## Validation harness — the behavior-preserving gate, defined

The existing eval already validates this refactor end-to-end; it needs only a golden-diff wrapper (which does NOT exist today — the "sweep" is currently a convention). The eval is two decoupled stages: **construct+render** (`run_v3_e2e --construction-only` → `build_advisory_for_repo` → graph → `render_build_script` → `setup.sh`) and **measure** (`bench`/`run_replay_ladder` → run the `setup.sh` in a container → `pytest --collect-only`/`pytest`). The measure side is refactor-AGNOSTIC (it reads the artifact, not construction internals), so a reorg that preserves graph+setup.sh is proven by re-measuring.

**The golden-output differential (the gate for every Phase-0 + Phase-1 step):**
- **Baseline** (captured ONCE, before any touch): per pass-repo, `json.dumps(graph.to_dict(), sort_keys=True)` (`schema.py:445`) + the `--construction-only` `setup.sh`, with LLM responses recorded (base-image/service-config/dist-guesser, all `temperature=0`) so the diff isolates construction from model variance.
- **After each step:** regenerate and diff. **Byte-identical graph + setup.sh = behavior-preserving PROVEN** — stronger than "tests pass" (it catches a subtly reordered edge a green suite misses). A byte-identical `setup.sh` means the container run is identical, so `bench`/collect need only run on CHANGED repos.
- **Layers beneath it:** the unit suite (`tests/depgraph`) + the LLM-free `graph_fidelity` edge-cases (deterministic graph correctness on fixtures).

**For the NEW design's e2e** (post-Stage-C, separate from this reorg): Gate A (`gate_a_cure_recovery.py` — cure clears the collect cliff), Gate B (`gate_b_partition_sanity.py` — shadow partition sanity), and `run_v3_e2e` full → `bench` EBSR (`collect_clean`) + the provisional-flag bucket. Those already exist and answer "does the two-lane model work e2e."

**Catch:** the harness scripts (`run_v3_e2e`, the gates) IMPORT the moving entry points (`build_advisory_for_repo`→`runtime`, `build_dep_graph`→`core`, `render_build_script`→`emit`), so they are IN the 830-site blast radius — the baseline must be captured with the PRE-reorg harness and compared with the rewritten harness running on reorged code.

---

## PHASE 0 — Decoupling prep (behavior-preserving, lands now, shrinks the move)

Each task removes one backward cluster-edge on the current flat layout, is independently testable, and ends in a small scoped commit. Order matters only where noted.

### Task 0.0: Build the golden-output differential harness + freeze the baseline

Do this FIRST — it is the gate every subsequent step (and both audits) is checked against. Without it, "behavior-preserving" is an assertion, not a proof.

**Files:** Create `scripts/graph_golden_diff.py` (thin driver over the existing eval — reuses `build_advisory_for_repo`/`build_dep_graph`, `render_build_script`, `graph.to_dict()`).

- [ ] **Step 1:** Per repo in the **pass-repo set** (the repos that currently produce a clean result — never baseline against a broken repo; memory `regression-sweep-is-the-gate`), call construction → `graph`, write `json.dumps(graph.to_dict(), sort_keys=True)` to `<baseline>/<repo>/graph.json` and the `--construction-only` `setup.sh` to `<baseline>/<repo>/setup.sh`. Record the LLM responses so re-runs are deterministic.
- [ ] **Step 2:** Add a `--compare <baseline>` mode that regenerates and diffs `graph.json` + `setup.sh` per repo, exits non-zero on ANY diff, and prints the per-repo delta.
- [ ] **Step 3:** Capture the baseline on the pass-repo set; commit the driver + the baseline artifacts.
- [ ] **Step 4:** Prove the gate works: a trivial no-op construction edit → `--compare` green; a deliberate 1-node change → red; revert. (A gate that can't go red is not a gate.)

### Task 0.1: Hoist the Layer-order constants to `schema.py`

The single highest-leverage decoupling: `EXECUTION_LAYER_ORDER` (and `_SERVICE_LAYER_ORDER`, `_LAYER_ORDER`) live in `certify.py` but are consumed by `build_script.py:20`, `patch_gate.py:14`, `react_repair/entry.py:27`(`:11`), `envstate/depgraph_live.py:59`. In the target layout those are `emit→core`, `mutate→core`, and cross-plane edges. Moving the constants to `schema.py` (the future `model.py` waist) kills all of them at once.

**Files:** Modify `src/python_deps/depgraph/schema.py` (add the constants), `certify.py` (import them from schema, keep a re-export alias so nothing else breaks this task), and confirm consumers still resolve.

- [ ] **Step 1:** Read `certify.py:40` and the definitions of `EXECUTION_LAYER_ORDER`, `_SERVICE_LAYER_ORDER`, `_LAYER_ORDER`. Confirm they depend only on `Layer`/`NodeType` (already in `schema.py`) — if so the move is pure.
- [ ] **Step 2:** Add the three constants to `schema.py` (after the `Layer` enum). In `certify.py`, replace the definitions with `from python_deps.depgraph.schema import EXECUTION_LAYER_ORDER, _SERVICE_LAYER_ORDER, _LAYER_ORDER` (keep the names importable from `certify` for this window).
- [ ] **Step 3:** Run `python -m pytest tests/depgraph/ -q` and `python -m pytest tests/ -q -k "certify or build_script or patch_gate"`. Expected: PASS (pure relocation).
- [ ] **Step 4:** Commit.
```bash
git add src/python_deps/depgraph/schema.py src/python_deps/depgraph/certify.py
git commit -m "refactor(depgraph): hoist Layer-order constants to schema (the model waist)" -- src/python_deps/depgraph/schema.py src/python_deps/depgraph/certify.py
```

### Task 0.2: Relocate the emit node-classification predicates to a shared low module

`populate.py` splitting into `emit/`(walk) + `commands.py`(pip/apt strings) creates a **bidirectional cycle**, and `block.py`(mutate) imports 5 emit predicates (`block.py:7` — a `mutate→emit` inversion). Both dissolve if the shared predicates `_is_reciped`, `_is_installable_project`, `_is_service_reciped`, `_apt_name`, `_pip_spec` (currently `emit.py:122-239`) move to a low shared module both sides import downward.

**Files:** Create `src/python_deps/depgraph/node_recipes.py` (the 5 predicates + `partition`/`topo_order` if co-dependent — verify); modify `emit.py`, `populate.py`, `block.py` to import from it.

- [ ] **Step 1:** Read `emit.py:122-239` (the 5 predicates) and `block.py:7`/`build_script.py` to confirm the exact symbol set and that the predicates depend only on `schema`/`Node`.
- [ ] **Step 2:** Move the 5 predicates (+ `partition`, `topo_order` if they form one cohesive walk-primitive cluster — research 4 named these in the `block→emit` edge) into `node_recipes.py`. Re-export from `emit.py` for this window. Update `block.py:7` to import from `node_recipes`.
- [ ] **Step 3:** Run `python -m pytest tests/depgraph/ -q`. Expected: PASS.
- [ ] **Step 4:** Commit (scoped to the three files + the new one).

### Task 0.3: Decouple `patch_gate` from the service subsystem

`patch_gate.py:21` imports `service_recipes.render_probe_poll` (used `:259`) and `:22` imports `service_tables.KNOWN_SERVICE_KINDS` (used `:106`). In the target these are `mutate→runtime` edges — and Agent 4's key finding: **homing `patch_gate` in `graph/mutate/` only breaks the cycle IF these two imports stop pointing up; otherwise the cycle merely relocates to `graph↔runtime`, which is worse.**

**Files:** `patch_gate.py`, `service_recipes.py`, `service_tables.py`, `schema.py`.

- [ ] **Step 1:** Move `render_probe_poll` (a pure leaf — `service_recipes.py` has no module-level deps; it only lazily imports `patch_gate.is_read_only` at `:23`, a band-aid this dissolves) to sit with `patch_gate` (or a shared spot both import). Confirm the lazy `service_recipes.py:23` import can be removed.
- [ ] **Step 2:** Move the vocabulary constant `KNOWN_SERVICE_KINDS` (`= frozenset(SERVICE_DEFAULTS)`, `service_tables.py:25`) to `schema.py` next to `NodeType` (it's an admission-time vocabulary, not a runtime detail). Import ONLY the constant — do not drag the table (`service_tables` also imports `import_mapping`).
- [ ] **Step 3:** Update `patch_gate.py:21-22` to the new sources. Run `python -m pytest tests/ -q -k "patch_gate or service"`. Expected: PASS.
- [ ] **Step 4:** Commit.

### Task 0.4: Fold `reconcile_apt_names` into the provider's native obligations

`build_dep_graph` calls `reconcile_apt_names` (`build.py:1182`, from `apt_verify`) directly — a `core→native` edge after the provider seam. Move the call into `provider.native_obligations` (or a provider hook) so `core/orchestrate` imports no native module.

**Files:** `build.py`, `src/ecosystems/python/provider.py`, `apt_verify.py` (no change, just re-homed caller).

- [ ] **Step 1:** Read `build.py:1182` context and `provider.native_obligations` (`provider.py:81`-ish region for the native phase). Confirm the apt reconcile is the last native step and has the inputs it needs inside the provider.
- [ ] **Step 2:** Move the `reconcile_apt_names(...)` call from `build_dep_graph` into the provider's native-obligations tail. Verify the graph is threaded identically.
- [ ] **Step 3:** Run the full depgraph suite + a construction smoke (`pytest tests/depgraph/ -q`). Expected: PASS, byte-identical graph.
- [ ] **Step 4:** Commit.

### Task 0.5: Hoist the cross-destination shared helpers

Three split files share a private helper across their future destinations (research 2/3): `build._canon` (fixpoint+skeleton+pipeline), `probe._ingest_need`+`_first_line_with` (install+native+contracts), and the `invocation_resolver`→`roots` two constants (`_DEV_GROUP_DENYLIST`, `_TEST_SCOPE_EXTRA_ALLOWLIST`). Hoist each to a shared home so the later cut is clean.

**Files:** `build.py`/`import_mapping.py` (for `_canon`); `probe.py`/a shared native base (for `_ingest_need`); `roots.py`/a shared `read` config (for the two constants).

- [ ] **Step 1:** `_canon` → `import_mapping.py` (the future `util/`; it's a PEP-503 canonicalizer, already `util`-natured) as a public/underscored helper; repoint `build.py`'s ~4 call sites. (Note: `resolve_lock.py` has its OWN `_canon` — leave it.)
- [ ] **Step 2:** `_ingest_need` + `_first_line_with` — designate `probe`'s native fabricators as the owner; confirm `install_closure`, `import_probe`, `test_gate_probe` all import them from that one spot (they will land in `native/system_libs`; install + contracts import back — the allowed direction).
- [ ] **Step 3:** Hoist `_DEV_GROUP_DENYLIST`, `_TEST_SCOPE_EXTRA_ALLOWLIST` from `roots.py` to a shared location `invocation_resolver` can import without reaching into the install lane (e.g. a `read`-level constants module, or `schema`). Repoint `invocation_resolver.py:36-37`.
- [ ] **Step 4:** Run the full suite after each hoist. Commit each hoist separately (three small commits) or as one "hoist shared helpers" commit if the suite stays green throughout.

### Task 0.6: Delete the four verify-then-delete dead files

Shrink the move surface: `resolve_link.py`, `script.py`, `translate_sanitize.py`, `config_tables.py` are retired/test-only (design §3). `resolve_link` needs its `resolve.py:114` re-export (`_stamp, _import_edges, link_imports_to_packages, _merge`) repointed first.

- [ ] **Step 1:** For `resolve_link`: find where `link_imports_to_packages` (the public one) actually lives / should live; repoint `resolve.py:114`'s import and any external importer (research 1: `test_resolve.py` imports `resolve_link`). Confirm no production consumer outside tests.
- [ ] **Step 2:** Delete the four files + their now-dead tests (or the test's dead references). Run the full suite. Expected: PASS (they were test-only/retired).
- [ ] **Step 3:** Commit (scoped to the deleted files + repointed `resolve.py` + touched tests).

**End of Phase 0:** every backward cluster-edge the target layout would forbid is gone, three shared helpers are hoisted, four dead files removed — the atomic move is now a pure relocation. Re-run the full suite + the pass-repo sweep before starting Phase 1.

---

## PHASE 1 — The atomic move (ONE sweep-gated commit; gated on a FROZEN tree)

**Do not start Phase 1 until:** Stage C has landed (lanes finalized, demoted-tier emission deleted), the in-flight `integrate`/`exec_trace` work is committed, and the working tree is clean. Then freeze the tree and execute this as ONE commit.

### The corrected file map (research-validated)

Consolidation is less aggressive than the design's §3:

- **`native/` = 16 → ~7:** `system_libs.py` (ldd_probe+syslib+failure_signatures+probe's import_probe/reconcile_predicted/fabricators, ~700 ✓<800 after deduping the 3 syslib-node builders), `wheel.py` (460), `apt.py` (470), `build_deps.py` (**seed only**, 474), `source_metadata.py` (debian_builddeps+pep725, 601), `project_native.py` (443), `tables.py` (64).
- **`lanes/install/` = 12 → ~6–7:** keep `resolve.py` (1057, standalone), `resolve_lock.py` (+pins, 701), `resolve_errors.py` separate; `link.py` (relink+naming, 267), `ground.py` (repair+pipreqs_map+coverage, 515), `roots.py` (406), `install_closure.py` (from probe).
- **`probe.py` splits FOUR ways:** `install_closure` family → `lanes/install/install_closure.py`; `import_probe`/`reconcile_predicted`/fabricators/`_ingest_need` → `native/system_libs.py`; `test_gate_probe` → **`native/`** (NOT `contracts/` — it calls `_ingest_need`; homing it in contracts inverts the layer); `INSTALL_TIMEOUT` → shared (imported by `cure` + `install_closure`).
- **`shadow.py`** (unplaced in §3) → `graph/python/` root next to `pipeline.py` (it drives classify→cure→arbitrate; it's a provider-level composition, not a lane).
- Everything else per design §3 (with the collision fixes: unify `_make_syslib_node` on `syslib.make_syslib_node`; dedup `_first_line_with`).

### The import-rewrite checklist (the pass/fail of this commit)

All 830 sites must be rewritten. Handle by category — the last four are the silent-failure classes:

- [ ] **Top-level dotted imports** (`from python_deps.depgraph.X import …`, `python_deps.{evidence,models,…}`) → new `graph.*` paths. Mechanical, but two are SPLITS: `repo_modules`→`read/modules`(top_level_names)+`route/collisions`(stem_collisions), and `naming`→`link`; every consumer calling *both* halves (e.g. `react_repair/entry.py:10,237,239`, `orchestrator.py:731`) must be re-pointed per-symbol, not renamed.
- [ ] **288 function-local imports** (52 production) — grep for INDENTED `import`/`from` inside defs; a top-of-file pass misses them. Heaviest: `envstate/orchestrator.py` (24 sites: lines 162,165,196–198,343,364,677,678,702,728,731–733,887,890,952–954,1072–1074,1264,1265).
- [ ] **~35 identity-sensitive sites — REWRITE, NO SHIM:** `monkeypatch.setattr(mod,…)`/`patch.object(mod,…)` on a module object (all 31 `import … as mod` sites + `fault_injection.py:121-123`). A shim silently no-ops these.
- [ ] **4 `importlib.import_module("python_deps.depgraph.X")` string paths** (`tests/depgraph/test_no_service_tables.py:7,12,20,27`).
- [ ] **8 `caplog … logger="python_deps.depgraph.X"` name strings** (test_artifact_map:228, test_build_deps:311, test_debian_builddeps:316/325, test_pep725:209/218, test_test_gate_probe:98, test_wheel_preflight:174) — logger name = module `__name__`, so these must track the rename or caplog assertions go vacuous.
- [ ] **`pkg_layer/` 8 imports** (align/closure/contract/repair/usage.py) → new `graph.*` paths; PRESERVE the private `roots._requirement_group` that `contract.py:16` needs. `pkg_layer/` stays a sibling consumer (not folded into graph/).
- [ ] **The 5 root files' mutual relative imports** (`evidence.py:14-16`, `failure_classifier.py:5`, `import_graph.py:11`) → new subpackage paths (they split across `read/` + `util/`).
- [ ] **`depgraph/__init__.py:13,15,45` re-exports** (incl. `DockerExecutor` → `runtime/executor`) → repoint or drop.
- [ ] **`envstate/check_quality.py:8` re-export** of `depgraph.check_quality` — verify the re-export's downstream.

### The tripwire rewrite (must be in THIS commit — research 4)

`tests/depgraph/test_construction_boundary.py` goes vacuous on the rename+split. Rewrite:
- [ ] `_FORBIDDEN` → `frozenset({"top_level_names", "stem_collisions"})` (drop `"repo_modules"`, dead; do NOT add generic `"modules"`/`"collisions"`).
- [ ] Guarded set → a **computed filesystem walk**: glob every `*.py` under `graph/core/` ∪ `graph/python/`, EXCLUDE `graph/python/route/**` (the sanctioned consumers) and the definer `graph/python/read/modules.py`. Run the existing AST `_referenced_names` guard on each survivor. Delete the hardcoded `from python_deps.depgraph import build, roots, scan`. This auto-guards future splits.
- [ ] Repoint the behavioral test `test_scan_to_nodes_drops_…` to `graph.python.read.scan.scan_to_nodes`; update the `test_ast_guard_actually_catches_a_reintroduction` leak strings to the new import shapes.
- [ ] Add the Stage-C behavioral invariant at the arbitrate layer (route/ is name-guard-exempt, so it needs a behavioral test): a collision name is not install-accepted unless the cure succeeded AND the canonical-plan probe shows it doesn't resolve locally.

### New-layout cycle tripwire

- [ ] Add a structural test: `native/system_libs.py`'s back-edge to `lanes/install/link.py` (`import_probe`→`relink.flag_runtime_import_failure`) is acyclic ONLY because `link` is a leaf. Assert `graph/python/lanes/install/link.py` imports nothing from `resolve`/`ground`/`roots` — so the install↔native relationship can never become a true cycle.

### Phase 1 gate

- [ ] Full suite green (`pytest tests/ -q`) — zero import errors (proves the 830-site rewrite is complete).
- [ ] The pass-repo sweep green (behavior-preserving proof — this is the load-bearing gate; prior graph work destroyed 3 of 33 passing repos).
- [ ] The rewritten tripwire green AND its self-test proves the new leak shapes trip.
- [ ] One commit, pathspec-scoped to the moved/rewritten files.

---

## Sequencing summary

0. **Ideally first (separate passes):** the verified dead-code audit AND the test-decoupling half (see the two Follow-up sections below) — dead symbols removed first are never relocated, and tests decoupled from module internals shrink the 667-site test rewrite. Neither is gated on anything; neither belongs in a move commit.
1. **Now (Phase 0):** the six decoupling tasks — Stage-C-independent, each behavior-preserving, each shrinks the move. Land them incrementally; re-run the sweep at the end.
2. **After Stage C + in-flight work lands:** freeze the tree, then Phase 1 as one atomic sweep-gated commit.
3. Keep `pkg_layer/` and the `eval/` harnesses on the import-rewrite checklist — they're downstream consumers, rewritten in the same commit, not folded in.

## Follow-up (separate pass, NOT part of this refactor): verified dead-code audit

Explicitly queued. This refactor deliberately does not delete beyond the four whole files in Task 0.6 — deletions don't belong in a behavior-preserving move commit. Three cleanup buckets exist; this follow-up owns the third:

| Bucket | What | Owner |
|---|---|---|
| Whole dead files | `resolve_link`, `script`, `translate_sanitize`, `config_tables` | ✅ this plan, Task 0.6 |
| Dead emission code (~3k lines) | demoted-tier node construction (Test-hub, Config/Runtime/Platform) | Stage C model change |
| **Orphan symbols** | zero-consumer functions / classes / constants | **this follow-up** |

**Method — verified-delete, not raw tool output:**
1. Run `vulture src/python_deps/` (or equivalent) for unused-symbol candidates over the graph concern + the 5 root files.
2. For each candidate, run a repo-wide **zero-importer** check — top-level AND function-local imports, `getattr`/`importlib`/string references, and monkeypatch targets (the same silent-reference classes the blast-radius audit enumerated). A vulture hit that is dynamically referenced, re-exported, or legitimately test-only is NOT dead.
3. Produce a removal guide: per surviving candidate, its definition site + the evidence of no consumer + a one-line justification. Delete each in its own scoped, sweep-gated commit.

**Seed candidates already surfaced by the reorg research (start here):**
- `build._detect_target_python` (`build.py:282`) — superseded by `detect_target_env`.
- `config_scan.configured_vars` (`:516`) and `_config_node` (`:545`) — Agent 2 found no external consumer.
- The overlapping-linker cluster to review for further dead paths — `resolve` / `resolve_lock` / `relink` / `naming` (`resolve_link` already covered by Task 0.6); this is handoff §9's "retired code never gets deleted" theme.

**Sequencing:** a SEPARATE pass, ideally run BEFORE Phase 0 (it shrinks the move surface). NEVER fold a deletion into a move/relocation commit — it makes both un-reviewable and the sweep cannot attribute a regression.

## Follow-up (separate pass, NOT part of this refactor): test decoupling + bloat reduction

Explicitly queued. The test suite is not grossly oversized (graph-concern tests = **136 files / 28,023 lines**, a healthy **1.4× test-to-source ratio**), but it is **structurally coupled to implementation** — which is precisely why the reorg is a 667-site *test* rewrite (80% of the blast radius). Decoupling the tests reduces bloat AND shrinks the move; the two are one problem.

**Measured coupling baseline (2026-07-17):**
- **76** private-symbol imports in `tests/depgraph/` (tests importing `_ingest_need`/`_canon`/`_make_syslib_node`… — test *how*, not *what*; they vanish/move in consolidation).
- **33** module-object alias imports + **262** mock/monkeypatch sites — the `import mod; monkeypatch.setattr(mod, "_private", …)` pattern; the shim-unsafe, refactor-fragile class.
- **45** `caplog` usages (8 assert on logger-name strings that break silently on a rename).
- Redundancy hotspots: `test_resolve.py` **2,788 lines vs 1,057 source (2.6×)**, `test_probe.py` 1,270 vs 587 (2.2×); **136 test files for ~82 source files** — per-file mirroring that won't collapse with the 82→~45 source consolidation unless the tests are deliberately merged.

**Method:**
1. **Decouple** (the reorg de-risker): replace private-helper tests with public-seam tests (`build_dep_graph`/`classify`/`install_closure`/`resolve_closure`); replace `monkeypatch.setattr(module, …)` with injection through the existing `Executor`/`RecordProvider`/`DistGuesser` seams; replace logger-name `caplog` asserts with state/graph-shape asserts.
2. **De-bloat:** consolidate test files alongside the source consolidation (16 native → ~7 test files, dropping merged private-helper duplicates); parametrize the 2.6× giants.
3. Each change is behavior-equivalent coverage, run under the same suite; a coverage delta (not just pass/fail) is the guard against silently dropping a real case.

**Sequencing:** the **decouple** half is ideally BEFORE Phase 0 — it directly shrinks the 667-site test rewrite (rewrite public-API imports, not 76 private paths + 33 module-object patches). The **de-bloat** half naturally rides alongside Phase 1's file moves (test files follow source files). A separate pass either way — never mixed into a move commit.

## Self-Review

- **Spec coverage:** implements the reorg design (`2026-07-17-graph-module-architecture-reorg-design.md`) with the four research agents' corrections folded in — the corrected file map (§ "corrected file map"), the missed backward edges (Phase 0 Tasks 0.1–0.4), the shared-helper hoists (0.5), the shim-unsafe policy (Global Constraints + Phase 1 checklist), the 4-way probe split, `shadow.py`'s home, `pkg_layer`'s disposition, and the exact tripwire delta.
- **Placeholder scan:** line/symbol anchors are concrete (from the research). The Phase-1 per-site line numbers are given by category with the heavy consumers enumerated; the exhaustive 830-site list is re-derived against the frozen tree at execution (the design gates Phase 1 on that freeze, so pinning today's line numbers would be stale — that is a deliberate gate, not a placeholder).
- **Consistency:** the file target (~45–47), `native` 16→7, `install` 12→6–7, and the probe 4-way split are consistent between the corrected-map section and the checklist; the `EXECUTION_LAYER_ORDER`→`schema` move (0.1) is the same constant the Phase-1 `model.py` waist expects.
- **The one risk the plan cannot fully pre-close:** `envstate/orchestrator.py` (60 sites, 24 function-local) is the densest consumer and has been under active edit — Phase 1 must run against it frozen, or the rewrite races the edits.
