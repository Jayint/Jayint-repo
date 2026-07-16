# Two-Lane Model — Integration & Refactor Spec

**Date:** 2026-07-17
**Status:** Proposed; ready for implementation planning
**Scope:** *How* to land the two-lane causal graph model in the current `src/python_deps/depgraph/` codebase — the concrete stage-by-stage integration, edit sites, deletions, and safe sequence. This is the **HOW**; the model itself (the **WHAT/WHY**) is fixed by the design spec and is not re-opened here.
**Design source (locked):** `docs/superpowers/specs/2026-07-16-two-lane-causal-graph-and-import-classification-design.md`. Node model `project·file·import·pkg` + preserved native overlay (`SystemLib`+`Tool`, unmerged); `NodeType` stays a superset; two lanes with cure = provider node type; keep `layer`, drop `tier`; classifier ladder; install-lane pipreqs→LLM→grounding, no identity fallback; `relink`/`packages_distributions()` is the certified `satisfied-by` source; the Phase-A fixpoint is the existing loop.
**In-flight unit:** `docs/superpowers/plans/2026-07-17-import-dist-pipeline.md` (the install-lane candidate-gen swap) is already being implemented and is one row of the integration map below.
**Preserved unchanged:** the system-package / native detection subsystem (`os_resolver`, `wheel_preflight`/`wheel_oracle`, `ldd_probe`, `pep725`, `debian_builddeps`, `build_deps`, `apt_verify`, `syslib`, `seed`, `probe` native fabricators, `project_native_*`). The new graph consumes the `pkg → {SystemLib, Tool}` overlay exactly as today.

## Executive summary

**This is targeted insertions at existing seams plus deletions — not a rewrite.** The current `build_dep_graph` already runs in the exact order the two-lane model needs: **interpreter → declared-roots → resolve/install → relink → certify.** Every piece of the new model rides one of those existing seams, so integration is a set of swaps/inserts at known lines, not new scaffolding.

The single most useful property for planning: **the blocker (collision-zone certificate arbitration) gates exactly one change — `route-not-drop` — not the whole model.** Three pieces (certify-by-import, drop `tier`, the install-lane candidate swap) integrate with no blocker at all and can land immediately and independently. Only the `import→file` config lane is the coupled effort.

## The current construction pipeline (code-grounded)

From the live orchestrator — `_python_package_obligations` (`build.py:711`) → `_python_native_obligations` (`build.py:989`) → `build_dep_graph` (`build.py:1030`):

| Stage | Site | Does today |
|---|---|---|
| 1 — static scan | `build.py:787` `scan_to_nodes` | Emits `Import` + `Test` nodes; **drops** stdlib + project-local imports (`scan.py:79` `_local_module_names`, drop at `scan.py:152-153`) |
| 1.5 — target env | `build.py:802` `detect_target_env` | **Resolves the interpreter** (already before roots) |
| 2 — declared roots | `build.py:827` `select_roots` | Manifest-declared-only roots; imports never generate roots |
| 2a — era anchor | `build.py:838` | Resolve cutoff from pins |
| A — repair fixpoint | `build.py:876` `_phase_a_fixpoint` (`:346`) | resolve→install→coverage→repair; candidate-gen at `:429-432`, coverage `resolved_record_coverage` `:408`, missing `:409-415`, add-root `:450` |
| 3a′–3b‴ — native priors | `build.py:937-989` | wheel-soname / sdist build-dep / project-native (preserved) |
| B — tier descent | `build.py:1002` | LOOK-then-derive on the converged closure |
| 4a — relink | `build.py:1003` `relink` | Certified `Import→Package` from `packages_distributions()`; **sole** `satisfied-by` source; provided-check = has outgoing `REQUIRES→Package` edge (`relink.py:145-154`) |
| 4.5 — ldd | `build.py:1011` | Authoritative run-time native-lib discovery (preserved) |
| 4b — apt reconcile | `build.py:1119` | Release-aware apt-name reconciliation (preserved) |
| 5 — certify | `build.py:1124` | Host certification, `layer`-ordered; flips `state` |

## Integration map — where each model piece lands

| New-model piece | Current seam | Change type | Blocker? |
|---|---|---|---|
| Interpreter-before-classifier | Stage 1.5 `detect_target_env` (`build.py:802`) | **Reuse as-is** — already runs first | no |
| Classifier ladder (declared→target-stdlib→`repo_modules`→external) | *no seam yet* — needs interpreter (1.5) + declared (2) | **Insert new stage after Stage 2** (see Ordering) | no (module), yes (arbitration) |
| Route-not-drop + `file` nodes | Stage 1 `scan_to_nodes`; drop at `scan.py:152-153` | **Behavioral** — stop dropping project-local; emit `file` nodes | **yes** |
| `import→pkg` install lane | Phase-A candidate-gen (`build.py:429`, `repair.generate_candidates`) | **Swap** to pipreqs→LLM→ground (in-flight plan); loop unchanged | no |
| `import→file` config-lane cure | `populate.py:57` `_EDITABLE_INSTALL` (capstone) + `config_scan.py:416-428` pytest/rootdir readers | **Sequence earlier** so first-party imports certify | **yes** |
| relink learns `file` dst | `relink.py:145-154` provided-check (`REQUIRES→Package` only) | **Extend** — also accept a `file` destination | no |
| certify-by-import | Stage 5 (`build.py:1124`); project gate today = `pip` rc0 | **Swap** project gate → `python -c "import <targets>"` | no |
| Drop `tier` | `schema.py:34-47` (`TYPE_TO_TIER`/`tier_for_type`), `:155` field, `:190-191` derive, `:224`/`:272` serialize; `schedule.py:67,90,96` | **Delete** (small — see Cleanup) | no |
| Stop emitting Test/Runtime/Config/Service/Platform | scan (Test) + demoted stages | **Stop-emit; keep enum members** (superset) | no (follows route-not-drop) |
| Edge `scope ∈ {runtime,test}` | `Edge.data` (`schema.py:305`) | **Add attribute** — replaces Test-hub scope | no |

## Ordering subtlety — route-not-drop is two edits, not one

Route-not-drop is **not** a change made entirely inside `scan.py`. The classifier's rungs need the interpreter (Stage 1.5) and the declared set (Stage 2), both of which run **after** scan. So it splits cleanly:

1. **Stage 1 `scan`** stops discarding project-local imports (`scan.py:152-153`) — emits raw `import` nodes, makes **no** routing decision.
2. **A new classifier stage after Stage 2** (interpreter + declared both in hand) rewrites each import's `satisfied-by?` to a `file` (internal) or `pkg` (external) via the ladder, and owns the collision-zone arbitration.

This keeps `scan` dumb and puts all routing + arbitration in **one new module**, which also satisfies the tripwire (`tests/depgraph/test_construction_boundary.py`): `scan`/`roots`/`build` never reference `repo_modules`/`stem_collisions` — the new classifier stage does. The structural guard is rewritten to point at the new module rather than being deleted.

## Independent vs. blocked

**Land now, no blocker, any order:**
- **certify-by-import** (Stage 5) — design-spec migration step 1, highest measured leverage (attacks the 34→14 collect cliff directly), fully independent of any graph change.
- **the install-lane candidate swap** (Phase-A `:429`) — already in flight; does not touch the file lane.
- **drop `tier`** — mechanical.

**The one coupled effort (needs the arbitration owner first):** the `import→file` config lane — route-not-drop + the classifier stage + config-cure sequencing + relink file-dst + rewriting the tripwire's behavioral guard. Grounding does **not** protect the collision case (a wheel that genuinely provides `import items` RECORD-confirms, so removing the broad drop without a config-first certificate re-introduces the wrong-install false-green). The design spec's "The collision zone → certificate arbitration" section owns this; it is the hard prerequisite for step 4.

## Cleanup this integration authorizes

Driven by "stop emitting the demoted types," not by an upfront refactor. Deletion is the cleanup — the model change is what classifies each file:

- **Delete now (safe, test-covered):** `schema.py` `tier` surface (`TYPE_TO_TIER`, `tier_for_type`, `Node.tier`, the `__post_init__` derive, the `to_dict`/`from_dict` key) with `schedule.py:67,90,96` + two eval readers updated; `repair.normalize_candidates` / `curated_candidates` / `decide()` once the candidate swap lands (their `test_repair_ladder.py` cases too). The 295 repo-wide `tier` grep hits are almost all the *word* "tier" in docstrings and unrelated locals (`artifact_map`, `react_repair`, `config_scan`/`build_deps` prose) — the real `Node.tier` consumers are few.
- **Keep the enum members (superset-locked):** `NodeType.{SERVICE,CONFIG,TEST,RUNTIME,PLATFORM}` stay — measured live references outside construction: `SERVICE`×60, `CONFIG`×48, `TEST`×45, `RUNTIME`×12, `PLATFORM`×2 (`envstate/*`, eval `oracle.py`, react-arm). Deleting them breaks import-time. Stop *emitting*, don't remove.
- **Repurpose, don't delete:** `config_scan.py` is not demoted-dead — it becomes the config-lane cure's rootdir/pytest engine (`scan_pytest_ini` `:416`, `scan_setup_cfg_pytest` `:422`, `scan_pyproject_pytest` `:428`).
- **Retire from build path (not delete):** the static `resolve_link.link_imports_to_packages` — `relink` is already the sole certified source; `resolve_link._import_edges` is a live no-op under declared-only roots (edit its two call sites, don't chase it as dead).
- **Do NOT delete (looks dead, load-bearing):** `naming.package_roots` (feeds the root-selection A/B eval); `import_mapping.map_import_to_package` / `CURATED_IMPORT_TO_PACKAGE` (live in `evidence.py`, `integrate.py`, `runtime_classify.py`, `pkg_layer/`); `scan.local_module_names` (called *inside* `repo_modules.stem_collisions` — the new classifier depends on it).

Not in scope for this integration: the react-arm / repair-loop / emit layer (~4,918 lines, ~24% of the module) is a *consumer* of the finished `DepGraph` via `schema.py`. Because the enum stays a superset, it keeps working untouched. Whether to later split `depgraph/` into construct/emit/repair sub-packages is a separate boundary decision, deliberately deferred.

## Sequence (additive-first, sweep-gated)

Each step keeps already-passing repos green; the regression sweep is the gate (memory `regression-sweep-is-the-gate`: prior graph work destroyed 3 of 33 passing repos — sweep the repos that PASS before any run).

1. **certify-by-import** (Stage 5) — independent, highest leverage, lands first.
2. **drop `tier`** + **finish the install-lane candidate swap** — both independent (swap already in flight).
3. **Additively add `file` nodes + edge-`scope`** — enum member + emission; keep the old Test-hub wiring; sweep stays green because nothing routes yet.
4. **The coupled file lane** — build + prove the config-first arbitration owner → knowingly rewrite `test_construction_boundary.py`'s behavioral guard to the new invariant (a collision name is not install-accepted unless the config-cured certificate shows it does not resolve locally; test with a stubbed certificate) → flip route-not-drop (`scan.py:152-153`) + wire the classifier stage after Stage 2 + sequence the config-cure (`populate.py:57` editable install) before the install lane can accept a collision name + teach `relink._provided_imports` the `file` dst + add a one-line guard in `probe._probe_targets` (`probe.py:496`) skipping imports whose `satisfied-by` is a `file`, so native probing stays on package-backed imports only (a **preservation** measure — it keeps native detection behaving as today under the grown input set, not a detection change).
5. **Delete the now-dead demoted-tier construction code** — Test/Runtime/Config/Service/Platform *emission*; enum members stay (superset); `config_scan` is repurposed, not deleted.

Steps 1–3 make real progress on the measured problem with zero blocker exposure; step 4 is the one place the blocker is paid for; step 5 is cleanup that falls out for free.

## Regression-sweep gate (invariant)

Every step above is a construction change on already-passing repos. Before any scored run: sweep the repos that currently PASS, not only the broken ones. Route-not-drop (step 4) rewrites construction on *every* repo and is the highest-risk step — it stays behind the arbitration owner and the rewritten tripwire, and is validated on the pass-repos before the old drop path is pruned.

## Native-overlay invariants (pinned)

Verified against code, restated so "preserved unchanged" is not misread:

- **`Tool` and `SystemLib` are both actively-emitted node types** — not demoted, not merged. `probe.py` fabricates both live (`probe.py:440` `NodeType.TOOL`, `:468` `NodeType.SYSTEM_LIB`). The emitted set in the new model is `project · file · import · pkg · SystemLib · Tool`; the demoted-and-superset-retained set is `Test · Runtime · Config · Service · Platform`.
- **The overlay is a flat one-hop fan-out off `pkg`** (`pkg → SystemLib`, `pkg → Tool`), siblings not a chain — there is no `SystemLib → Tool` edge. Both are **leaf sinks**, never `requires` sources: `EDGE_RULES["requires"]` (`schema.py:110-113`) lists `SystemLib`/`Tool` only in the destination set. The paired `-dev → runtime` relationship (e.g. `libpq-dev → libpq5`) is apt's to resolve transitively at install time, deliberately not a graph edge.
- **Native detection logic is untouched.** No native module reads `.tier` (so the `tier` drop is invisible to it), and the discovery/resolution/ordering modules are not in the change list. The overlay's *only* contact with this integration is the `probe._probe_targets` input guard in step 4 above, which exists to keep that behavior constant under route-not-drop's grown import set.

## Open questions / to validate

- **Config-bundle recovery rate:** how often does editable-install + rootdir clear the collect cliff on the pilot repos (diff vs the gold Dockerfile)? Gates whether step 4 pays off.
- **Collision-zone frequency:** how large is the undeclared-and-name-colliding population in practice; does config-first + flagged-install converge without manual intervention?
- **Ordering cost:** confirm no stage between 1.5 and 4a needs the internal/external split earlier than the new classifier stage.
- **`tier` eval readers:** enumerate the exact eval/schedule consumers to update in step 2 (the two beyond `schedule.py`).

## References

- Design spec (the WHAT): `docs/superpowers/specs/2026-07-16-two-lane-causal-graph-and-import-classification-design.md`.
- In-flight plan (one row here): `docs/superpowers/plans/2026-07-17-import-dist-pipeline.md`.
- Code anchors: `build.py` (`_python_package_obligations:711`, `_phase_a_fixpoint:346`, stage comments `:787`–`:1124`), `scan.py:79,152-153`, `relink.py:145-154`, `populate.py:57`, `config_scan.py:416-428`, `schema.py:34-47,155,190,224,272,305`, `schedule.py:67,90,96`, `tests/depgraph/test_construction_boundary.py`.
- Memory: `regression-sweep-is-the-gate`, `self-install-false-green-vector`, `two-phase-declared-roots-construction-landed`, `package-layer-not-source-aware`.
