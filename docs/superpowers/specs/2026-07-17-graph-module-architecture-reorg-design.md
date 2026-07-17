# Graph Module Architecture Reorganization (design)

**Date:** 2026-07-17
**Status:** Proposed; adversarially reviewed (Fable); a FUTURE refactor sequenced AFTER the config-lane Stage C flip. Not to be executed now.
**Scope:** Reorganize the tangled `src/python_deps/depgraph/` (77 files, ~17,600 lines, one flat directory mixing five concerns) into a layered `graph/` package tailored to the two-lane graph model, plus evict the non-construction concerns (services, orchestration, repair) to sibling packages. Python profile.
**Builds on:** the two-lane model + config-lane specs (`2026-07-16-two-lane-causal-graph-and-import-classification-design.md`, `2026-07-17-config-lane-module-spine-and-collision-arbitration-design.md`) and the cleanup diagnosis in `2026-07-17-config-lane-stage-bc-and-cleanup-handoff.md` §9.
**Explicitly deferred:** execution. This is a behavior-preserving move-refactor; it runs LAST (after Stage C deletes demoted-tier emission and finalizes the lanes), as one atomic sweep-gated commit. See §7.

## 1. Why, and the honest scope

The problem is not file *count* — 77 files at ~228 lines each is healthy by the "many small files" rule. The problem is that one flat directory mixes **five lifecycles**: graph construction, emit (render → `setup.sh`), the react repair loop, service detection, and orchestration. A reader of "graph construction" wades through ~40% non-construction code.

This reorg does two things and honestly claims only those:
1. **Concern eviction** — ~22 non-construction files move to `runtime/` (services + orchestration) and `repair_agent/` (repair), landing where their consumers already live. `graph/` ends holding only what is actually in `build_dep_graph`'s path.
2. **Legibility** — the flat directory becomes ~11 labeled subpackages whose shape *teaches* the two-lane model; the dense native/install subsystems consolidate one-file-per-step into cohesive modules; the multi-concern grab-bag files (`build.py`, `probe.py`, `executor.py`) split into single-purpose files.

**It deletes ≈ zero lines.** The genuine ~3k-line deletions belong to the Stage C model change (demoted-tier emission, the Test-hub, the old scan drop); this refactor only relocates. Net accounting: **~77 → ~40 files** in `graph/`, ~17.6k → ~10–11k lines, all in the construction+emit path.

## 2. The organizing principle

The folder structure IS the causal pipeline. A reader traverses `graph/python/` top-to-bottom and reads the two-lane model: **read the repo → assemble the spine → route each import → cure it in its lane → certify.** Two design mantras become structure:

- *"Classification is static; arbitration is certified"* → `route/classify.py` + `route/arbitrate.py`.
- *"The provider's node type IS the cure"* → `lanes/config` (module) + `lanes/install` (package), with `native/` as the downstream overlay on install-lane packages (NOT a third lane).

## 3. The target structure

```
graph/                       (~40 files, ~10–11k lines — all in build_dep_graph's path)
  model.py                   # Node/Edge/DepGraph/NodeType/State/Layer + the Layer-order constants  ← schema.py, ids.py
  contracts/                 # the agnostic seams everyone writes against
    executor.py              #   CommandResult + Executor Protocol + LocalSubprocessExecutor + TIMEOUT_RC  (NOT DockerExecutor)
    provider.py              #   the EcosystemProvider protocol   ← ecosystems/base.py
    registry.py              #   language → provider              ← ecosystems/registry.py
  mutate/                    # the shared graph-MUTATION contract (used by BOTH construction and repair — breaks the cycle)
    patch.py  patch_gate.py  action_class.py  block.py  check_quality.py
  core/                      # agnostic composition
    orchestrate.py           #   build_dep_graph — the composition root (registry knowledge OK; apt folded into the provider)
    certify.py               #   host-certified state transitions
  python/                    # the Python EcosystemProvider
    read/                    # inputs: what the repo declares + imports
      declarations.py        #   ← evidence.py, models.py
      scan.py                #   ← scan.py, import_graph.py, subprocess_scan.py (scan half)
      modules.py             #   ← repo_modules.py (top_level_names / repo_modules)
      target_env.py          #   ← target_env.py
    plan.py                  # the ONE canonical TestEnvPlan       ← invocation_resolver.py + config_scan.py (pytest+env-authoritative halves)
    skeleton.py              # assemble project→module→import (the spine)  ← build.py (_add_project_node + apply_routing)
    route/                   # "static, then certified"
      classify.py            #   the static ladder → module|package|collision  ← classify.py
      collisions.py          #   stem_collisions + namespace suspects          ← repo_modules.py (stem_collisions)
      arbitrate.py           #   certified resolution of the collision zone     ← arbitrate.py
    pipeline.py              # the provider bodies (~700 lines)     ← build.py (_python_package_obligations, _python_native_obligations, uv-sources machinery)
    fixpoint.py              # resolve→install→look→repair loop     ← build.py (_phase_a_fixpoint)   [STAYS in python/, not core — see §4]
    lanes/
      config/  cure.py       # import→module: editable install + rootdir + INSTALL_TIMEOUT  ← cure.py
      install/               # import→package (CONSOLIDATED 12 → 4)
        resolve.py           #   ← resolve.py + resolve_lock.py + resolve_errors.py + pins.py + probe.install_closure
        link.py              #   ← relink.py + naming.py            (certified import→package)
        ground.py            #   ← repair.py + pipreqs_map.py + coverage.py   (candidate grounding)
        roots.py             #   ← roots.py                         (declared-only roots)
    native/                  # overlay on install-lane packages (CONSOLIDATED 16 → 6)
      system_libs.py         #   ← ldd_probe + syslib + probe.import_probe + probe.reconcile_predicted + failure_signatures
      wheel.py               #   ← wheel_preflight + wheel_inspect + wheel_oracle
      apt.py                 #   ← apt_verify + os_resolver
      build_deps.py          #   ← build_deps + debian_builddeps + pep725 + seed
      project_native.py      #   ← project_native_deps + project_native_scan
      tables.py              #   ← tables.py (NATIVE_RISK_PACKAGES, CLI_TOOL_TO_APT)
    util/                    # shared python-provider utils
      import_mapping.py      #   ← import_mapping.py (~20 importers: normalize_package_name)
      failure_classifier.py  #   ← failure_classifier.py
    commands.py              # node → pip/apt install string        ← populate.py (command-string half)
  emit/                      # agnostic: walk the graph → setup.sh
    emit.py                  #   ← emit.py + populate.py (walk half; KEEP the scratch_certified poison-gate)
    build_script.py          #   ← build_script.py
    export.py                #   ← export.py

runtime/  (execution plane; ← src/envstate/)
    executor.py              #   DockerExecutor                     ← executor.py (impl half)
    services/                #   ← service_construct, service_evidence, service_parse, service_recipes,
                             #     service_relevance, service_scan, service_sources, service_tables (8)
    advise.py  schedule.py  evidence_log.py  static_collect.py  req_slice.py  repoint.py  discovery_expand.py
    env_scan.py              #   ← config_scan.py (generic env-evidence half, consumed by classify_services_clean)

repair_agent/  (← src/react_repair/, plus the repair-support evicted from depgraph/)
    graph_context.py  diagnose.py  runtime_classify.py  runtime_ingest.py  graph_enrich.py
    integrate.py  exec_trace.py    # ← MOVE ONLY AFTER the in-flight grounding work lands (§7)

DELETED (verify-then-delete; real removal, not relocation)
    resolve_link.py          #   retired from the build path; edit the resolve.py:114 re-export, then delete
    script.py                #   render_setup_sh — test-only
    translate_sanitize.py    #   test-only
    config_tables.py         #   demoted Config-tier table, test-only
```

## 4. Corrected invariants (from the Fable adversarial review)

The first sketch of this reorg had five load-bearing errors; the review traced real imports and corrected them. These are now baked into §3:

- **`model.py` is the narrow waist.** `emit/` and `repair_agent/` depend on `model.py` (incl. the Layer-order constants) but NOT on `core/`'s fixpoint — they consume finished graphs. The superset-`NodeType` lock makes this structural.
- **Split `executor.py`, don't evict it [was BLOCKER].** 17+ construction files import it. The `CommandResult` + `Executor` protocol + `LocalSubprocessExecutor` + `TIMEOUT_RC` are the seam construction is *written against* → `contracts/`. Only `DockerExecutor` (the impl) → `runtime/`.
- **`mutate/` is a real package [was the CYCLE].** `patch`/`patch_gate` admission is a shared graph-*mutation* contract used by BOTH the repair loop AND service detection. Homing it in `repair_agent/` created a `repair_agent ↔ runtime` cycle. It belongs in `graph/mutate/`, depended on by both planes.
- **`fixpoint.py` STAYS in `python/`, not `core/` [was ASPIRATIONAL].** `EcosystemProvider` exposes only three phase-level methods; `_phase_a_fixpoint` is soaked in uv/pip/RECORD specifics. Hoisting it into agnostic `core/` needs ~6 new protocol methods — a redesign, not a move. Hoist only when a *second* ecosystem exists to validate the abstraction. The lane-aware `missing` filter's drift-guard is the `routed_provider` *marker convention* (documented in `model.py`), not the file's location.
- **`pipeline.py` is named, not defaulted [was UNDER-SPECIFIED].** `_python_package_obligations`/`_python_native_obligations`/`_add_project_node` + the uv-sources machinery (~700 lines) are the Python *provider bodies*. If they defaulted into `core/orchestrate.py`, `core/` would import scan/resolve/evidence/native and the agnostic claim would die on day one. They go to `graph/python/pipeline.py`; `orchestrate.py` keeps only `build_dep_graph` minus the apt stage.
- **`plan.py` sharing is a TARGET, not yet code.** Today `cure.py`/`arbitrate.py`/`classify.py` consume `invocation_resolver`; `certify.py` does NOT yet. Co-location does not deliver divergence-immunity — the two-config-reader reconciliation does. Land the certify-consumes-plan change (unfinished Stage B) before relying on the shared-plan invariant.
- **`native/` and `lanes/install/` consolidate (this design's addition).** The dense folders merge one-file-per-step into cohesive modules (16→6, 12→4). This trades against "many small files" — merged modules land ~600–650 lines, near the 800 ceiling but under it. The `resolve*`/`relink`/`naming` cluster consolidation also folds in genuine dead-code cleanup (`resolve_link` deletion) the §9 diagnosis already named.

## 5. What leaves `graph/`, and why it is safe

Each eviction was verified against real imports to confirm construction does not transitively depend on it:

- **`runtime/` (~15 files):** the 8 `service_*` — a self-contained cluster (`service_construct` imports `service_evidence`/`parse`/`relevance`/`sources`) whose **two drivers are both execution-plane and also evicted here**: `envstate/classify_services_clean.py:42` (→ `service_construct.build_service_nodes`) and `advise.py:360` (→ `provider.service_obligations`, Phase 3, "live-only" — after the dep-graph is built). Core construction (`build.py` Phases A/B) references them only in comments; the sole graph-side coupling is `patch_gate` → `service_recipes`/`service_tables`, which is decoupled at the move (§4, §9). Then `advise`/`schedule`/`static_collect`/`req_slice`/`repoint`/`discovery_expand`/`evidence_log` (consumers are exclusively `envstate`), `DockerExecutor` (impl, not protocol), and `config_scan`'s generic env-evidence half.
- **`repair_agent/` (~7 files):** `graph_context`, `diagnose`, `runtime_classify/ingest`, `graph_enrich`, and — only after the in-flight grounding work lands — `integrate`, `exec_trace`. All consume a finished graph + failure text; none is in `build_dep_graph`'s path.
- **Deleted (~4):** `resolve_link` (retired), `script`/`translate_sanitize`/`config_tables` (test-only).

## 6. Loose ends the review named (resolve during planning, not now)

- **`pkg_layer/`** (`src/python_deps/pkg_layer/`) imports `resolve_closure`/`executor`/`schema`/`target_env` and is unaddressed here — it breaks on any move without a coordinated rewrite. Include it in the migration's import-rewrite pass.
- **`artifact_map.py`** is eval-only → move to eval-support, not `graph/`.
- **`probe.py` splits three ways:** `install_closure`+`INSTALL_TIMEOUT` → `lanes/install`/`lanes/config`; `import_probe`+`reconcile_predicted` → `native/system_libs`; `test_gate_probe` → `contracts/` (graph exposes it; `runtime` imports it).
- **`wheel_oracle`** has one install-lane importer (`resolve_lock`) but lives in `native/wheel.py` — a one-way install→native edge; document it as allowed.
- **`import_mapping`/`failure_classifier`** are the shared python-provider utils with ~20 importers — `python/util/`.

## 7. Migration & sequencing (behavior-preserving, sweep-gated, LAST)

Ordering is not optional — the working tree shows active edits in `build.py`, `resolve*.py`, `repair.py`, `roots.py`, `evidence.py`, `import_mapping.py`, and the `integrate`/`exec_trace` grounding work.

1. **After Stage C.** The config-lane model change (route-not-drop) is what deletes the demoted-tier emission and finalizes the two lanes. You cannot correctly classify a file as dead/repurposed/kept until then — `config_scan` looks like dead "Config-tier" code but the config lane repurposes it. Model change first; this reorg last.
2. **Resolve the `mutate/` boundary first.** The patch/patch_gate contract split changes the package boundaries themselves; splitting it across commits leaves a broken intermediate. Settle it before the move.
3. **After the in-flight grounding lands.** `integrate.py`/`exec_trace.py` are active work; moving them mid-flight guarantees a shared-branch collision.
4. **Shims, then one atomic move.** Do NOT physically move `schema.py`/`ids.py` early — create `graph/model.py` with the old paths as re-export shims for one window (100+ import sites, function-local imports in `orchestrator.py`, and module-identity monkeypatching in `eval/graph_fidelity/fault_injection.py`). The move commit must contain: the full import rewrite (incl. function-local + module-identity sites), the tripwire rewrite (§8), and the composed suite + pass-repo sweep.

## 8. The tripwire must be rewritten in the same commit

`tests/depgraph/test_construction_boundary.py` forbids the literal name `"repo_modules"` and guards the module set `{build, roots, scan}`. After the `repo_modules`→`read/modules`+`route/collisions` rename and the `build.py` split, the name-match never fires again and the guarded set no longer covers the new surface — the test passes **vacuously** and the regression sweep cannot see it. Rewrite the guard, in the move commit, to: (a) forbid `top_level_names`/`stem_collisions` imports outside `route/`; (b) enumerate every module under `graph/{core,python}` except `route/`; (c) carry Stage C's behavioral guard forward (a collision name is not install-accepted unless the cure succeeded AND the canonical-plan probe shows it doesn't resolve locally).

## 9. Open questions / to validate

- The consolidation's file sizes: confirm `native/system_libs.py` and `build_deps.py` land under the 800-line ceiling after merging; if not, keep the finer split for those two.
- Whether `service_recipes` rendering belongs in `emit/` rather than `runtime/services/` (it renders probe-poll scripts — an emit concern with a service-table dependency).
- The exact `config_scan` split line between `plan.py` (authoritative pytest/env) and `runtime/env_scan.py` (generic env-evidence), and where its private AST helpers land (`read/`).

## 10. References

- Specs: `2026-07-16-two-lane-causal-graph-and-import-classification-design.md`, `2026-07-17-config-lane-module-spine-and-collision-arbitration-design.md`.
- Cleanup diagnosis: `2026-07-17-config-lane-stage-bc-and-cleanup-handoff.md` §9.
- Config-lane plans: `2026-07-17-config-lane-stage-a.md`, `2026-07-17-config-lane-stage-b.md`.
- The Fable adversarial review that corrected §4 (blockers: executor split, the mutate cycle, aspirational fixpoint-in-core, under-specified build split, future-tense plan sharing).
- Memory: `architecture-python-coupled-provider-seam`, `multi-language-seam-slice1-landed`, `regression-sweep-is-the-gate`, `core-branch-paper-interpretability-priority`.
