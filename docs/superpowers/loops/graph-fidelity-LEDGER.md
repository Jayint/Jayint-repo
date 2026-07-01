# Graph-Fidelity Eval Loop — Ledger

Protocol: `docs/superpowers/loops/2026-07-02-graph-fidelity-eval-loop.md`
Resume from the first unchecked item. Trust this ledger + `git log` over memory.

## Bootstrap checklist (build harness before the improve loop)
- [x] `baseline_labels.py` — one-shot VM fetch + `compute_essr` re-score → `outputs/graph_fidelity/baseline_labels.json` (+ dataset cached). DONE d3f2102. **Feasible = 10** (honest RAT pass_rate≥0.8, not the looser "reached tests"=13). Dropped: Qiskit, pretix (RAT never reached tests) + Archipelago, baserow, anthropic-sdk-python (RAT ran but <0.8 — key/network suites). Present: rat13/repo2run8/ccdf11/radical11.
- [ ] `oracle.py` — held-out recipe → declared node set per tier (TDD) — deterministic recall/content grader (IS the grader → allowed to read the held-out recipe)
- [x] `qualitative_judge.py` — DONE bed3d36. Cheap Haiku judge, 2–3 consensus, safe-degrade to low-conf match; never sets pass_rate. WIRING TODO: confirm the haiku model slug resolves via the configured provider (OpenRouter may want an `anthropic/…` prefix) before the first live judge call.
- [x] `edge_cases/` corpus — DONE 8b2f44b (7 cases + `manifest.json` known answers). RECONCILE @ scorecard: (a) build-essential/pg_config tiered TOOL not SYSTEM_LIB — grader must use ONE consistent tier taxonomy; (b) pins plausibility-checked from memory, not live PyPI — the container run confirms each case truly isolates its class.
- [ ] `scorecard.py` — per-repo run + 3-delta grade + qualitative judge → JSON (TDD)
- [ ] `gaps.py` — typed gaps attributed to constructor stage (TDD)
- [ ] `report.py` — aggregate + cluster ranking (TDD)
- [ ] `run_eval.py` — CLI, per-repo result cache; first run on `--seed`
- [ ] Seed trusted (harness output hand-verified on the ~8 seed repos)
- [ ] Expanded to all 15

## Improve iterations
<!-- append one Observation/Why/What/Verification block per iteration -->
