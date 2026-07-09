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

---

## Forensic deep-dive — 7 sonnet subagents, 2026-07-09 (per-repo, artifact-verified)

Dispatched one read-only forensic subagent per regressed repo (VM `root@167.233.64.96`). All 7 converge. Findings below are anchored to real bytes (emitted `setup.sh`, `run.log`, `run_pytest_results.json`, `_result_row.json`) + source read of `src/react_repair/{loop,actions,entry}.py`.

### Q: were all 30 cycles consumed? — NO, not one.
Every repo exited early via **PLATEAU** (`_PLATEAU_PATIENCE=2` — two consecutive non-improving rebuilds ends the loop). LLM-call counts: Scrapling **2**, ezdata **3**, promnesia 5, anthropic-sdk 7, podman-compose 7, ingestr 15, pre-commit 24. The 30-turn budget was never the binding constraint. Turns ≤ these counts (some calls are compaction).

### Q: what did the repair agent try that failed? — 4 failure classes
Notably, in several cases **the agent diagnosed the RIGHT problem** but the fix never reached disk as valid code, and the loop shipped the broken artifact instead of the seed.

| repo | emitted class | seed | what the agent actually tried | why it failed |
|---|---|---|---|---|
| anthropic-sdk | **PROSE** | 314L/57 `--no-deps` installs | correctly spotted the optional `aiohttp` extra gap | shipped its *diagnosis sentence* as the script → build_failed |
| Scrapling | **PROSE** | 354L/113 installs | correctly diagnosed missing `patchright`, re-reasoned the correct version `1.48.0.post0` | shipped the *reasoning sentence*; apostrophe in "I'll" → `bash: unexpected EOF` |
| pre-commit | **PROSE** | 222L/21 installs | may have started from an EMPTY script (see seed-staging note) | shipped turn-1 Thought → `I: command not found`, exit 127 |
| ingestr | **STRIPPED** | 77L/4 actions (apt + pinned psycopg2 + editable) | rewrote to one line | stray trailing backtick → `bash EOF`; dropped apt + `psycopg2`. **Bug-A ruled out** (full clone worked; pure bash-parse failure) |
| podman-compose | **REAL-BUT-UNDERINSTALL** | 3.6KB/14 pinned installs | clean from-scratch rewrite (`pip install -e . && pip install pytest`) | dropped the `parameterized==0.9.0` **devel-extra** → 371 parametrized tests uncollected (512→141), 440→55; build stayed green (`ebsr=true`) |
| ezdata | **EXPLORE-PROBE** | 1407L/188 installs | still *exploring* (`cd && find && ls && cat`) when loop ended | probe installs nothing; `cat`/`ls` exit 0 → **false-green** build, only 30 dep-free tests pass |
| promnesia | **EXPLORE-PROBE** | 369L/~100 installs | still *exploring* (`cd && cat && ls`) | same false-green; 41 dep-free tests pass |

### The two root-cause bugs, sharpened (both confirmed in source)
1. **`loop.py::run_react` — no keep-best / seed-floor (PRIMARY).** `register()` tracks a scalar `best_passed` COUNT for plateau detection but never the *script text*. PLATEAU/GIVEUP `return outcome, script, graph` returns the **current** (last) `script`. No rollback to seed or best. **This single fix floors all 7 back at seed level.**
2. **`actions.py::parse_action` — non-scripts become patches (ENABLER), two gaps:**
   - **Prose:** a single English line in a fence is neither a recognized probe nor ≥2 read-only lines → falls through to `Action("patch")`.
   - **`cd`-probes:** a single-line `cd … && cat/ls/find …` compound — `cd` is NOT in `_READ_PROBE_CMDS`, and being one physical line it dodges `_is_all_readonly_block` (needs ≥2). The deployed Bug-B fix (`4155197`/`2e82f2e`) was live for this run and still missed this shape. **Cleanest fix:** run `is_read_only()` on the *whole compound line* regardless of first token (that check already exists in `patch_gate.py` and is correct — only the first-token allowlist gate in front of it is too narrow); and reject fenced blocks containing no shell command at all (prose).

### Three NEW secondary findings that change the plan
1. **Seed baseline doesn't reproduce in the react Sandbox → the loop "repairs" scripts that were actually fine.** ingestr needs **postgres**, podman-compose needs a real **podman binary** (72 unfixable `FileNotFoundError`), promnesia needs services — none present in the Sandbox. The seed scores *below* its construction number there, the **absolute 0.9 threshold is unreachable**, so the loop keeps patching a good script and degrades it. **Implication:** the DONE gate should be **relative to the seed's in-sandbox baseline**, not absolute 0.9; and services must be provisioned in the react Sandbox for a fair test. (This compounds with, but is distinct from, the model confound.)
2. **The Thought/Action/Observation trace was NEVER written** — the adapter's `_run_v3()` never passes `--trace-out` to `run_v3_e2e.py`, so `ReactLog(trace_path=None)`. It wasn't lost after the fact; it was never requested. **One-line fix, and it MUST land before the re-run** so the next investigation reads a real trace instead of reconstructing from artifacts.
3. **`case_study.json` is cross-contaminated** — `final_dockerfile`/`base_image`/`environment` show *unrelated* repos' data (a Go project in ingestr, a PyInstaller pipeline in podman-compose) and `llm_turns` is always `0`. A consolidator bug in `scripts/consolidate_run.py`. Any environment/turn-count analysis must cross-check `run.log`/`usage.json` directly.

### pre-commit seed-staging caveat (do not over-generalize)
pre-commit's prose says the setup.sh was "currently empty," and its `input/repo/` dir had no staged seed — suggesting a possible per-repo seed-staging miss. BUT only **1 of 50** repos shows this "empty" phrase; anthropic-sdk and Scrapling provably loaded their seeds. So treat pre-commit as a possible intermittent per-repo staging edge case, NOT a batch-wide seed-wiring bug.

## Recommended fix ORDER (revised after forensics)
1. **Wire `--trace-out` in the adapter** — do this FIRST (observability before re-running).
2. **`loop.py` keep-best/seed-floor** — makes below-seed regression structurally impossible (highest leverage).
3. **`actions.py` parser tighten** — prose + `cd`-probe can't become patches (`is_read_only` on whole compound line + reject command-less prose).
4. **DONE-gate relative to seed baseline** + provision services in the react Sandbox — fixes the "repairing a fine script" trap and removes the model/env confound.
5. Then re-run full-49 + a **MiniMax construction-only** pass for the clean A/B.
6. (lower priority) fix the `consolidate_run.py` environment/`llm_turns` cross-contamination; consider raising `_PLATEAU_PATIENCE` and/or not counting failed-build patches as "attempts."
