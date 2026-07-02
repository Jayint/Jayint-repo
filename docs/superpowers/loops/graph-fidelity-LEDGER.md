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

## Phase B — autonomous sequence (run once construction is green; user directive 2026-07-02)
- [ ] 1. Refactor `qualitative_judge.py` → pure helper (drop `complete_with_retry`); model call = orchestrator Haiku subagent.
- [ ] 2. `scorecard.py` — construction path + oracle-diff + `-slim` container run + `compute_essr` + write judge-inputs (TDD).
- [ ] 3. `gaps.py` + `report.py` + `run_eval.py` (TDD).
- [ ] 4. Front-load Haiku holes-preview on seed repos → gap punch-list.
- [ ] 5. Start measured improve iterations (§4).

## Improve iterations
<!-- append one Observation/Why/What/Verification block per iteration -->
