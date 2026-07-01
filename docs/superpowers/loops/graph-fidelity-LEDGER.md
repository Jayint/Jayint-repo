# Graph-Fidelity Eval Loop — Ledger

Protocol: `docs/superpowers/loops/2026-07-02-graph-fidelity-eval-loop.md`
Resume from the first unchecked item. Trust this ledger + `git log` over memory.

## Bootstrap checklist (build harness before the improve loop)
- [ ] `baseline_labels.py` — one-shot VM fetch + `compute_essr` re-score → `outputs/graph_fidelity/baseline_labels.json` (+ dataset cached to `datasets/rat_python_medlarge15.json`)
- [ ] `oracle.py` — held-out recipe → declared node set per tier (TDD)
- [ ] `scorecard.py` — per-repo run + 3-delta grade → JSON (TDD)
- [ ] `gaps.py` — typed gaps attributed to constructor stage (TDD)
- [ ] `report.py` — aggregate + cluster ranking (TDD)
- [ ] `run_eval.py` — CLI, per-repo result cache; first run on `--seed`
- [ ] Seed trusted (harness output hand-verified on the ~8 seed repos)
- [ ] Expanded to all 15

## Improve iterations
<!-- append one Observation/Why/What/Verification block per iteration -->
