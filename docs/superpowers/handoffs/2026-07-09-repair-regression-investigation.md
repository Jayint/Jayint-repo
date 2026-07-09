# Handoff — investigate why the react repair loop REGRESSES vs construction-only

**Branch:** `john-v3-multi-lang` (shared — commit locally, append-only; push only when asked, clean ff). **Date:** 2026-07-09. **Deployed:** `/opt/agents/john-react` @ `2e82f2e`; run with `/opt/rat_venv/bin/python3`. **Memory:** `[[react-arm-vm-state-and-radical-parity]]`, `[[honest-success-def-and-branch-split]]`.

## The finding
The repair-ablation (react loop) scored **LOWER** than the construction-only baseline (initial build scripts, no repair). The repair loop **net-regressed**: +4 improved, **-7 regressed**, 39 unchanged. 4 of the 7 regressions went from *working* → **`build_failed`**. A repair loop should be **impossible** to regress below its seed — so this is a bug, not just model noise.

## Run locations (VM)
| what | path (VM) | model | notes |
|---|---|---|---|
| **Repair-ablation** (react loop, THIS run) | `/opt/runs/john-planner-v3/repair-ablation-python50-c5/` | MiniMax-M2.7-highspeed, thinking-off | seeds from construction's setup.sh; c5→killed→resumed c3; the ablation under study |
| **Construction-only** (initial build script ONLY, no repair) = the A/B baseline | `/opt/runs/john-planner-v3/construction-python50-20260707-072356/` | **deepseek/deepseek-v4-flash** | its `output/<owner>/<repo>/setup.sh` files are EXACTLY what the ablation seeds from |

Both have `rat_results.json` + per-repo `_result_row.json`. Score with `unified_metrics.py` (see below).

## Scores (recorded 2026-07-09)
**Repair-ablation (react loop) — OUR CURRENT REPAIR BASELINE:**
```
T1  EBSR(build+exec) = 34/50 = 0.68     real-success(>=0.8) = 0.24 (12 repos)   fullpass = 5
    ESSR ÷all = 0.291    ÷exec = 0.428 (paper macro)    micro = 0.795    hollow = 4
T4  LLM calls = 12.5/repo    agentLoopTok = 70,156/repo    (tokens calculated)
```
**Construction-only (initial scripts, no repair) — the baseline it should BEAT:**
```
T1  EBSR = 34/50 = 0.68     real-success(>=0.8) = 0.30 (15 repos)   fullpass = 6
    ESSR ÷all = 0.347    ÷exec = 0.510    micro = 0.805    hollow = 3
```
**Delta: repair loop LOST -3 real-successes (15→12) and -0.082 ESSR÷exec (0.510→0.428).**

Reproduce both side-by-side:
```
cd /opt/harness/scripts && /opt/rat_venv/bin/python3 unified_metrics.py \
  construction-only=/opt/runs/john-planner-v3/construction-python50-20260707-072356 \
  repair-ablation-dockeragent=/opt/runs/john-planner-v3/repair-ablation-python50-c5
```
(the `dockeragent` in the ablation's label → `infer_arm`→arm0 → T4 reads `run.log` `[Tokens]` for token economy.)

## Per-repo regression/improvement (the diagnostic)
**REGRESSED (construction passed more; repair broke it) — 7:**
| repo | construction | → ablation |
|---|---|---|
| pre-commit/pre-commit | 753 (0.92) | **0 — build_failed** |
| anthropics/anthropic-sdk-python | 1701 (0.48) | **0 — build_failed** |
| D4Vinci/Scrapling | 399 (0.85) | **0 — build_failed** |
| bruin-data/ingestr | 5 (1.00) | **0 — build_failed** |
| containers/podman-compose | 440 (0.86) | 55 (0.39) |
| xuwei95/ezdata | 199 (0.83) | 30 (0.16) |
| karlicoss/promnesia | 105 (0.65) | 41 (0.22) |

**IMPROVED (repair fixed it) — 4:** microsoft/markitdown 0→309 (0.92), GoogleCloudPlatform/PerfKitBenchmarker 73→2510 (0.91), tinygrad/tinygrad 0→738 (0.33), supabase/supabase-py 0→6 (0.16).

Regenerate this table:
```python
import json, glob, os
A="/opt/runs/john-planner-v3/repair-ablation-python50-c5"
C="/opt/runs/john-planner-v3/construction-python50-20260707-072356"
cons={r["full_name"]:r for r in json.load(open(C+"/rat_results.json"))["rows"]}
abl={json.load(open(f)).get("full_name"):json.load(open(f)) for f in glob.glob(A+"/output/*/*/_result_row.json")}
for fn in sorted(set(cons)|set(abl)):
    c=cons.get(fn,{}); a=abl.get(fn,{})
    d=a.get("pytest_pass_rate",0)-c.get("pytest_pass_rate",0)
    if abs(d)>=0.1: print("%+.2f %-34s C %d(%.2f) -> A %d(%.2f) [%s]"%(d,fn,c.get("pytest_passed",0),c.get("pytest_pass_rate",0),a.get("pytest_passed",0),a.get("pytest_pass_rate",0),a.get("status")))
```

## Root cause (two compounding flaws — VERIFIED by artifact)
Evidence: pre-commit's emitted `setup.sh` = **1 line of English prose** (`"I see the setup.sh is currently empty and the build failed. The repo is at /app…"`); ingestr's = **1 line** (`pip install -e .` only, all dep-installs gone). The loop overwrote a 25-line working script with junk and shipped the junk.

1. **No "keep best / never worse than seed" guard.** `src/react_repair/loop.py::run_react` resets the *container* each turn but the *script* is last-write-wins (`script = action.new_script`). On `PLATEAU`/`GIVEUP` it returns the *current* `script` — the last (possibly broken) patch. It tracks `best_passed` (a COUNT, for early-stop) but never the best *script*, and never falls back to the seed. **← the primary fix.**
2. **Parser gap: single-line prose becomes a patch.** `src/react_repair/actions.py::parse_action` — Bug-B/C fixes catch fenced *probe* blocks (single read-only cmd via `_explore_from_script_block`, or ≥2 all-read-only lines via `_is_all_readonly_block`). But a **single line of prose** in a fence is neither → becomes `new_script`. That's how English text became the setup.sh.

## Proposed fix (make repair strictly ≥ seed)
- **loop.py:** track `best_script` (highest passing-test count that also BUILDS; seed is the floor). Emit `best_script` on every non-DONE exit. Never return a script that builds worse than the seed. This alone flips the 4 build_failed regressions back to seed-level → repair becomes ≥ construction while keeping the 4 real fixes.
- **actions.py:** reject a fenced block that isn't a plausible build script (e.g. no install/mutation line AND not a recognized probe → invalid, not a patch). Prose should never become a patch.
- TDD (loop + actions have Docker-free unit tests: `tests/react_repair/test_loop.py`, `test_actions.py`), then re-run.

## Caveats before concluding "repair hurts"
- **Model confound:** construction = **deepseek**, repair = **MiniMax**. NOT a clean A/B. For the clean comparison, run **construction-only with MiniMax** (`V3_CONSTRUCTION_ONLY=1 --llm MiniMax-M2.7-highspeed`) vs the MiniMax repair-ablation.
- The **regress-to-build_failed** pattern is a bug INDEPENDENT of model (a green env should never become unbuildable), so fix the loop first regardless.
- Env difference: the react Sandbox (uv/apt bootstrap, `--break-system-packages`, full clone at head_sha) differs from construction's eval image, so a seed's baseline in the Sandbox may not reproduce construction's score — another reason the loop must keep-best rather than trust the baseline.

## Ops notes (from the run)
- **Docker build cache** balloons unbounded at high concurrency; only reclaimable via `docker builder prune -af` (WITH `-a`) or by stopping the run. Keep concurrency ≤3 + `-af` prune each monitor cycle. The scheduler RESUMES on the same `--root-path` (skips repos with existing `run_pytest_results.json`).
- Full clones (Bug-A pin) are disk-heavy; reap `output/*/v3_src` of finished repos.
