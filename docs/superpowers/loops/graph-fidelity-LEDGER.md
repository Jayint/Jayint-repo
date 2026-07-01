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

## Improve iterations
<!-- append one Observation/Why/What/Verification block per iteration -->
