# Graph-Fidelity Eval Loop — Ledger

Protocol: `docs/superpowers/loops/2026-07-02-graph-fidelity-eval-loop.md`
Resume from the first unchecked item. Trust this ledger + `git log` over memory.

## Bootstrap checklist (build harness before the improve loop)
- [x] `baseline_labels.py` — one-shot VM fetch + `compute_essr` re-score → `outputs/graph_fidelity/baseline_labels.json` (+ dataset cached). DONE d3f2102. **Feasible = 10** (honest RAT pass_rate≥0.8, not the looser "reached tests"=13). Dropped: Qiskit, pretix (RAT never reached tests) + Archipelago, baserow, anthropic-sdk-python (RAT ran but <0.8 — key/network suites). Present: rat13/repo2run8/ccdf11/radical11.
- [ ] `oracle.py` — held-out recipe → declared node set per tier (TDD) — deterministic recall/content grader (IS the grader → allowed to read the held-out recipe)
- [ ] `qualitative_judge.py` — cheap LLM judge (Haiku/Sonnet) comparing held-out recipe + baseline outcome vs our graph + `setup.sh` → structured qualitative gaps. Diagnostic lens, NOT the headline metric. (user directive 2026-07-02: pure numbers mislead)
- [ ] `edge_cases/` corpus — hand-crafted synthetic known-answer repos isolating hard gap classes (soname→apt, pg_config, no-wheel→build-essential, marker target-vs-host, extras, requires-python floor, PIL→Pillow). Fast regression guard. (user directive 2026-07-02)
- [ ] `scorecard.py` — per-repo run + 3-delta grade + qualitative judge → JSON (TDD)
- [ ] `gaps.py` — typed gaps attributed to constructor stage (TDD)
- [ ] `report.py` — aggregate + cluster ranking (TDD)
- [ ] `run_eval.py` — CLI, per-repo result cache; first run on `--seed`
- [ ] Seed trusted (harness output hand-verified on the ~8 seed repos)
- [ ] Expanded to all 15

## Improve iterations
<!-- append one Observation/Why/What/Verification block per iteration -->
