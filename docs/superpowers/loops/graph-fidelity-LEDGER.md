# Graph-Fidelity Eval Loop — Ledger

Protocol: `docs/superpowers/loops/2026-07-02-graph-fidelity-eval-loop.md`
Resume from the first unchecked item. Trust this ledger + `git log` over memory.

## Bootstrap checklist (build harness before the improve loop)
- [x] `baseline_labels.py` — one-shot VM fetch + `compute_essr` re-score → `outputs/graph_fidelity/baseline_labels.json` (+ dataset cached). DONE d3f2102. **Feasible = 10** (honest RAT pass_rate≥0.8, not the looser "reached tests"=13). Dropped: Qiskit, pretix (RAT never reached tests) + Archipelago, baserow, anthropic-sdk-python (RAT ran but <0.8 — key/network suites). Present: rat13/repo2run8/ccdf11/radical11.
- [x] `oracle.py` — DONE. `parse_oracle(repo_dir) -> OracleResult(declared_by_tier, source, held_out)`, tier keys = NodeType names. 18 tests; sanity-validated on postgres-mcp. NOTE for scorecard: apt→SYSTEM_LIB only (no TOOL split), `FROM python:X.Y` only (uv/poetry Dockerfiles → empty PACKAGE tier; top-level compose only) → treat SYSTEM_LIB/RUNTIME as the primary signal + reconcile the edge_cases TOOL tiering here.
- [x] `qualitative_judge.py` — DONE bed3d36 (schema + tolerant parse + consensus + tests). **REDESIGN (user 2026-07-02): the judge runs as Claude Code Haiku SUBAGENTS dispatched by the orchestrator (`Agent`, model=haiku), NOT the metered API.** REFACTOR TODO @ wiring: drop the `complete_with_retry` transport from the module → expose `build_judge_prompt`/`parse_judge_response`/`consensus` as the pure public API (parse/consensus/tests kept); model call = orchestrator Haiku subagent. Kills the provider-slug concern.
- [x] `edge_cases/` corpus — DONE 8b2f44b (7 cases + `manifest.json` known answers). RECONCILE @ scorecard: (a) build-essential/pg_config tiered TOOL not SYSTEM_LIB — grader must use ONE consistent tier taxonomy; (b) pins plausibility-checked from memory, not live PyPI — the container run confirms each case truly isolates its class.
- [ ] `scorecard.py` — per-repo run + 3-delta grade + qualitative judge → JSON (TDD)
- [ ] `gaps.py` — typed gaps attributed to constructor stage (TDD)
- [ ] `report.py` — aggregate + cluster ranking (TDD)
- [ ] `run_eval.py` — CLI, per-repo result cache; first run on `--seed`
- [ ] Seed trusted (harness output hand-verified on the ~8 seed repos)
- [ ] Expanded to all 15

> Phase-B note: `scorecard.py` / `gaps.py` / `report.py` / `run_eval.py` + seed/expand above are the
> GRADING harness (Phase B), gated on construction working e2e.

## Phase A — construction e2e (co-owned; user's construction agent + my seed smoke test)
- [x] Seed construction smoke (typer), construction-only (skip pytest cert, NO agent): RAN e2e but produced **0 PACKAGE nodes / empty setup.sh** — root-caused below.
- [x] **ROOT CAUSE + FIX (4621e8d, reviewed APPROVE):** `resolve.py::_lock_command` passed `uv lock --python-platform <tag>`, but uv 0.10.4's `uv lock` REJECTS that flag (only `uv pip compile`/`export` accept it) → every `uv lock` failed → `resolve_closure` returned 0 packages for EVERY repo → empty graphs. Fix = drop the flag + its now-dead param; platform targeting stays where it belongs (parse-time `parse_uv_lock(target_env=…)` on the UNIVERSAL lock). Empirically verified: correctness preserved (typer → exact known-good closure + structured edges), target-env honesty preserved (marker eval honors container not host — independent of the flag). Reviewer mutation-tested all 5 rewritten tests (real teeth) + added a real-`uv` regression test (mocked tests never caught this). 675 depgraph tests green.
- [ ] FINDING (render, OPEN design Q for first-pass-clean): `build_script._is_reciped` emits PACKAGE(has version) / SYSTEM_LIB·TOOL(has `apt:` fix) **regardless of `State.SATISFIED`** — so `setup.sh` can emit "emitted-but-uncertified" commands = first-pass error risk. Options: (a) gate render on SATISFIED (strict replay → provably clean, uncertified→annotation the agent ADDS not DEBUGS); (b) keep best-effort + build an eval that flags `_is_reciped AND state!=SATISFIED` as predicted failure sites. (needs/CONFIG/SERVICE already emit comment-only, no guessed command.)
- Construction corpus-wide verification/fixes: **OWNED BY USER'S AGENT** — do NOT duplicate.
- GATE: construction green e2e → run the Phase-B autonomous sequence below.

## Two-phase eval plan (user re-scope 2026-07-02) — settle P1 before P2
- **P1 = graph COVERAGE** ("does the graph capture ~everything needed?"): per-tier recall vs held-out recipe oracle (proxy) + execution-discovered missing (install closure → import + `pytest --collect-only` → classify ModuleNotFound/ldd-not-found/cmd-not-found = graph gaps) + baseline feasibility + Haiku judge + edge_cases known-answers. **Building now: `coverage.py` (a0b19a0b) across feasible seed repos, defer darts.**
- **P2 = TRANSFORMATION** ("does render → a WORKING setup.sh?"): [x] `render_fidelity.py` DONE (e20ef5e) — `check_render`: topo/complete/single-emit/valid-bash/emitted-but-uncertified; mutation-teeth verified; 13 tests, no new deps. STILL QUEUED (build on a shared `replay` helper factored out of coverage's container probe, to avoid duplication): installability replay (rc + first-fail + class) + round-trip conservation.
- Shared container run; FAILURE ATTRIBUTION splits phases: missing-node → P1 coverage gap; order/bad-cmd/render → P2 transformation bug. (Installability probe deferred — it's P2.)

### P1 coverage — first-run findings (d2517b7; 6 seed repos, darts deferred). NUMBERS NOISY (D/E) — qualitative findings are the signal:
- **A (biggest; render/P2 but BLOCKS P1): `setup.sh` never installs the repo itself.** `render_build_script`/`_is_reciped` skip the PROJECT node → no `pip install -e .`. Corpus-wide; src-layout / name-mismatch repos (mvt, python-semantic-release, postgres-mcp) then fail `import <own_pkg>`. CONFIRMED: typer's script = 12 pip installs, zero editable install. Note `recipe.build_closure_recipe` HAS the `-e . --no-deps` step; the live `render_build_script` path doesn't emit it.
- **B (construction): import-name→dist-name misresolution.** vizro's `import github` → defunct PyPI `github==1.2.6` (sdist build fails → install_ok=FALSE) instead of `PyGithub`. Same class as the `pil_pillow` edge case. **ROOT CAUSE (a25d4a8d):** `naming.package_roots` ladder = declared-match → 13-entry static `import_mapping.py` table → identity fallback (`package_name=import_name`, trust=low). NO pre-install dynamic PyPI lookup — the "dynamic mapper" was DESIGNED + DEFERRED to "Future Work" in `docs/superpowers/plans/2026-06-23-dynamic-dependency-mapping.md`, never built. `github` absent from table → identity fallback → but `github` IS a real (defunct) PyPI pkg so uv locks `github==1.2.6` (the "unresolvable→no fix" net never fires). Chicken-and-egg: wrong guess claims the root slot at Stage 2 → PyGithub never installed → Stage 4a `packages_distributions()` relink (the ONLY dynamic piece; post-install) can only relabel already-installed dists → structurally can't rescue it. FIX (diagnosis, for construction agent): hybrid — static table fast-path + implement the deferred **propose-then-CERTIFY** dynamic lookup (candidates via PyPI, confirm via the candidate wheel's `top_level.txt`/`RECORD` before trusting — bare guessing was wrong 10/12 per the plan's own data). **v3-diff (ac0afa4b): NOTHING TO PORT** — `naming.py` byte-identical v3↔v3-core; `import_mapping.py` differs by 1 line where v3-core is MORE correct (dropped wrong `image→Pillow`, 06720b5); NO dynamic import→dist resolver in EITHER branch; v3's pruned Z3/`pypi_metadata` stack does VERSION resolution for known dist names, not import→dist guessing (fed `github` it would false-positive-confirm). Immediate fix = add table entries (`github→PyGithub`, `attr→attrs`, `serial→pyserial`, `docx→python-docx`); general fix = net-new propose-then-CERTIFY.
- **C (coverage-gap CLASS): subprocess CLI tools undiscovered.** mvt needs adb/git/default-jre/sqlite3/libusb/etc.; ldd+apt discovery only finds libs LINKED into compiled extensions, never tools the repo's OWN code shells out to via subprocess. New edge-case + discovery mechanism.
- **D (measurement): oracle.py parser noise — FIXED dc97396.** PEP508-validity gate + drop flags/flag-args/local-paths/shell-vars/build-tooling denylist + DEREF `-r/-c` into the referenced file's packages. Noise removed on all 6 repos (typer/slither/mvt→0, PSR 6→1, vizro 7→4). 91 oracle+coverage tests.
- **E (measurement) — my host-leak diagnosis was WRONG (dc97396 investigation).** No host fallback exists in oracle/coverage; typer/slither/mvt's CI matrices GENUINELY declare 3.14 (mvt tests 3.10–3.14) — coincidence with host 3.14. Guard tests added (undeclared runtime → None, never host). **REAL residual (still open, small):** RUNTIME diffs a SINGLE-target graph (picks one python) vs a MULTI-version CI matrix → false "missing" for non-target pythons. FIX = membership/correctness check (`graph_python ∈ declared_set` → `runtime_ok`), not full-set coverage.
- **F (minor, pre-existing): `runtime_base.py` `~=` normalization** treats PEP440 `~= 3.8` as poetry `~` (python-semantic-release) → degrades to 3.11-slim, misleading reason. 
- CONFIG/SERVICE tiers structurally empty (LLM env_classifier is a separate out-of-scope path) — not a real 0% signal.
- NEXT: fix D+E (trust the numbers) → A (unblock imports + a genuinely working env) → hand B/C to the construction agent.

## Phase B — autonomous sequence (run once construction is green; user directive 2026-07-02)
- [ ] 1. Refactor `qualitative_judge.py` → pure helper (drop `complete_with_retry`); model call = orchestrator Haiku subagent.
- [ ] 2. `scorecard.py` — construction path + oracle-diff + `-slim` container run + `compute_essr` + write judge-inputs (TDD).
- [ ] 3. `gaps.py` + `report.py` + `run_eval.py` (TDD).
- [ ] 4. Front-load Haiku holes-preview on seed repos → gap punch-list.
- [ ] 5. Start measured improve iterations (§4).

## Improve iterations
<!-- append one Observation/Why/What/Verification block per iteration -->
