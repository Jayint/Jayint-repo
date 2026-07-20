# Honest-success scoring + metrics: branch-split plan

**Date:** 2026-06-20 · **Status:** PLAN (not yet implemented). Derived from two adversarial workflows
(`w6t0tlo6l` unified-metrics design + skeptic; `wj12rt5bg` hollow-success gate investigation) and the
in-progress v1g 50-run (`run-20260619-171120`).

## 0. The problem in one line
The **agent** finalizes on a real in-sandbox `pytest` pass, but the **benchmark runner** sets `status=success`
purely on **Docker-build-success** (no pass-rate floor), so `_result_row.status=success` appears with
`pytest_pass_rate=0.0` ("hollow success"). The two gates disagree, and the per-repo `status` field is the lenient one.

## 0b. Hollow-success diagnosis (workflow `wj12rt5bg`, 2026-06-20) — verdict: BOTH H1 + H2, H2 dominant
- **Agent gate is mostly sound** (rejects collect-only; `_collect_only_passed` is dead code) BUT with two real caveats:
  - **`MIN_PASS_RATIO=0.8` is NOT the standing bar.** It is only checked on Gate 2 Path 3's `rc!=0` (partial-fail)
    branch (`agent.py:1289`). On the normal `rc==0` path the gate requires only **N≥1 passed** (1/550 would pass).
  - **H1 hole (active on pynitrokey):** `_shows_pytest_completion` matches `[100%]`, which pytest prints even for
    ALL-SKIPPED runs, and `maintainer.py:214` lets `shows_completion` bypass `_shows_execution` → `done_flag` fires on
    zero real passes. (Also a graph-OFF hole: `orchestrator.py:111-116` skips the gate when planner says 'done' and the
    contract graph is off — inactive while `DOCKERAGENT_ENABLE_CONTRACT_GRAPH=1`, but bites `--arm v1`.)
- **H2 (dominant): the runner never applies the agent's goal.** `success_scorer` (`scorers.py:44`) returns
  `{"success": output['status']=='success'}` — the agent sets `status='success'` when the image builds + artifacts
  copy out. `success` and `pytest_pass_rate` are independent scorers; EBSR (`compute_essr.py:100`) has no pass-rate
  floor ("pass-rate is NOT required"). The runner never references `MIN_PASS_RATIO`.
- **Per-repo:** django-oauth = synth gap (agent verified 550 real passes; synthesizer dropped `DJANGO_SETTINGS_MODULE`
  from the Dockerfile ENV → rebuilt image `ImproperlyConfigured`, pass=0) — **a NEW synth-gap class: dropped ENV, not
  heredoc**. pynitrokey = H1 hole + genuinely unrunnable (USB hardware absent → 190 skipped). nba_api = H2 (agent
  verified a narrow 5-test subset; runner ran the full 48 → 5 pass/43 ModuleNotFound → 0.10).
- **Impact on the live run:** of 15 current "successes," **3 flip** under `pass_rate≥0.8` (django-oauth 0.0,
  pynitrokey 0.0, nba_api 0.10) = ~20% hollow; the other 12 are 0.979–1.0. Real-success ≈ 12/15 so far.

## 1. The `real-success` definition (lock this in everywhere)
Anchor the binary success bar at the **same threshold the agent uses**, computed from raw pytest data
(`run_pytest_results.json`), never from `_result_row.status`:

| Tier | Predicate | Use |
|---|---|---|
| lenient (today's `status`) | built AND tests *executed* (collection counts) | **deprecate as a metric** — hollow-prone |
| floor (anti-hollow) | `ebsr AND passed ≥ 1` | filter hollow repos out of the economy/efficiency set; "real-exec rate" |
| **real-success** | **`ebsr AND pass_rate ≥ 0.8`** | the headline binary success count (matches the agent done-gate) |
| strict | `pass_rate ≥ 0.999` | full-pass (every test) |

`ebsr` = synthesised Dockerfile built and tests ran (`parse_method != 'build_failed'`).
`pass_rate = passed / (total − skipped)`. All computable from `compute_essr.score_agent` row fields — **pure
post-hoc re-score, no re-run, applies retroactively to every past run.**

> Note: the agent's *internal* gate does not uniformly enforce ≥0.8 today (its `rc==0` path needs only N≥1 passed;
> ≥0.8 binds only on the `rc!=0` partial path — see §0b / A5). The `pass_rate≥0.8` real-success bar above is the
> *scoring/headline* definition; A5 is the optional agent-side change to make the agent's internal bar match it.

## 2. Source of truth (verified by both workflows)
`compute_essr.score_agent(run_root)` recomputes EBSR, coverage, ESSR÷exec/÷all/micro, full-pass, hollow detection,
and A/B/C/D/U **uniformly across all arms from raw pytest data**. It is the metric authority; the runner already
calls it via `_print_paper_faithful_essr`. **Never trust `rat_results.json` (format varies) or `_result_row.status`
(stale post-repair).** Honest cross-method numbers from it (v1g PARTIAL at 28/50 — not yet final-comparable):

| Method | EBSR | ESSR÷all | Full-Pass | Hollow | micro |
|---|---|---|---|---|---|
| radical/arm0 (50) | 0.64 | 0.24 | 3 | 5 | 0.85 |
| **v1g (28, partial)** | 0.77 | **0.54** | **12** | **1** | 0.98 |
| v1 no-graph (50) | 0.22 | 0.16 | 4 | 2 | 0.89 |
| rat (50) | 0.92 | 0.62 | 16 | 6 | 0.82 |
| repo2run (50) | 0.62 | 0.39 | 5 | 10 | 0.73 |

## 3. AGENT-SIDE changes → GitHub agent branch `john-planner-v1` (port to `radical` only if needed)
Agent branches change what the agent *does and records*. (`multi_docker_eval_adapter.py` lives in the agent repo —
it is the agent's side of the runner contract.)

| # | Change | File | Why agent-side |
|---|---|---|---|
| A1 | **Recording-truncation fix** (heredoc body loss) — `_extract_worker_action` records only `.splitlines()[0]`, dropping heredoc bodies | `src/envstate/build_agent.py` | Changes what the agent records/executes → recovers heredoc-built envs that currently leak at synthesis (the real bucket-C losses). Highest-value remaining fix. **Behavioral change (touches execution) — scope/guard before implementing.** |
| A2 | **Emit a real in-sandbox test-pass signal** into the instance artifact: `verification_source='v1_test_run_finalize'`, `in_build_pass_rate`, `passed≥1` flag | `agent.py` / `multi_docker_eval_adapter.py` | Today `test_success` is *always False* for v1g (done_flag, not artifact verify) → attribution **C is structurally 0** and the two-stage in-build proxy is a guess. The runner cannot measure the synth-gap honestly unless the agent reports its real in-sandbox pass. |
| A3 | **Persist `agent_run_summary.json` into the run-output dir** at finalize | `multi_docker_eval_adapter.py` | Workplace copy is overwritten by the next run → per-phase tokens/steps permanently lost (arm0's already gone). Economy metrics need this saved per-run. |
| A4 | **Close the `[100%]` all-skipped Gate-1 hole** — change `maintainer.py:214` `if not (shows_completion or _shows_execution(output))` → `if not _shows_execution(output)` (or add an `_all_skipped` guard) | `src/envstate/maintainer.py` | `[100%]` prints for all-skipped runs, letting `done_flag` fire on 0 real passes (pynitrokey). Strictly stricter (risk = false-negatives, est. 0–1/50; promptwright case already covered by the 2300-char output window). |
| A5 (optional) | **Apply `MIN_PASS_RATIO=0.8` on the `rc==0` path too** (currently only `rc!=0` Gate 2 Path 3), and/or close the graph-OFF planner-done gate-skip (`orchestrator.py:111-116`) | `agent.py` / `src/envstate/orchestrator.py` | Makes the agent's in-sandbox bar a true ≥0.8 (today `rc==0` needs only N≥1 passed); closes the `--arm v1` no-graph gate-skip. Lower priority — only matters if you want the agent's *internal* bar to match the headline ≥0.8. |

(Already done + pushed on `john-planner-v1`: Bug 1 project-in-closure URL fallback; Bug 2 drop body-less heredocs in synthesis.)

## 4. RUNNER-SIDE changes → harness branch `ratbench-integration` (VM `/opt/harness`)
Harness changes how results are *scored and compared*.

| # | Change | File | Notes |
|---|---|---|---|
| R1 | **Align scoring success with the agent goal** — add `passes_agent_goal = bool(ebsr AND pass_rate≥0.8)` to the row dict + aggregate `agent_goal_rate`; make the headline use it, not `success_scorer`'s build-only `status` (`scorers.py:44` returns `status=='success'`, no pass-rate floor) | `scripts/compute_essr.py` (~L100/150; + `run_rat_benchmark.py` headline) | The "use the same success goal as the agent" fix. Zero-risk **additive** field; pure post-hoc re-score; retroactive (existing `_result_row` fields suffice). |
| R2 | **`unified_metrics.py`** — 6-table cross-method comparison, reusing `score_agent` as sole authority | new `scripts/unified_metrics.py` | Apply the skeptic's 6 revisions (below). Consumes baselines unrelated to the agent repo → must be runner-side. |
| R3 | **Attribution upgrade** — once A2 ships, have `classify()` read the agent's real-pass signal so C stops being structurally 0 | `scripts/attribution.py` | Pairs with A2. |

### Skeptic's required revisions for `unified_metrics.py` (R2)
1. Two-stage in-build: split `in_build_real` (non-collect vtcmd) vs `in_build_any`; never put arm0 (0.02, collect-only *by design*) and v1g (0.64, done_flag) under one comparable column without a per-arm operationalization label.
2. Economy `tokens_total`: mark arm0 **PARTIAL/N/A** (its synth + image_selector token costs are unrecoverable from the overwritten workplace); only compare the `agent_loop` token column cross-arm.
3. Define `real_success` **once** as `(ebsr AND passed≥1)` for the anti-hollow floor / economy denominator; drop any `OR status==success` (status isn't in the row dict).
4. Intersection economy: use real-success as the `solved` predicate; **gate behind manifest `status != running`** (refuse/banner for the live run); print the exact repo set.
5. `steps`: per-arm units differ (arm0 `[Tokens]`-line count = LLM calls; v1g = env-mutating ledger entries) — separate, clearly-labelled columns; never one `steps` header. Make `--arm` mandatory when the manifest is absent/ambiguous (rat vs repo2run are otherwise indistinguishable).
6. v1g in-build proxy: require a real test-pass signal (`v1_test_run_finalize` or ledger rc=0), not bare `v1_done_flag`; report strict and loose counts.

## 5. Ordering / dependency (call out for the supervisor)
The honest two-stage / synth-gap story **cannot be measured until agent-side A2 ships** — the runner has no
real-in-sandbox-pass signal to read today. Sequence:
1. Agent branch: A1 (recover envs) + A2 (emit signal) [+ A3 persist telemetry] → push.
2. Re-run the 50-set on the new agent commit.
3. Runner branch: R1 (real-success) + R2 (`unified_metrics.py`) + R3 (attribution) → score honestly.

**Do NOT finalize the arm0-vs-v1g comparison while v1g is partial (28/50)** — it's partial-vs-complete. Wait for 50/50.

## 6. Agent-side implementation status (2026-06-20) — COMPLETE on `john-planner-v1`, NOT pushed
All five agent-side items landed via subagent-driven-development (implement → spec review → code review), 9 commits, **0 regressions** (405 passed; the one failing `test_nested_pytester_django_target_uses_null_pytest_config` is pre-existing — no commit touches `_generate_test_script`). Final holistic review: APPROVED FOR PUSH.

| Item | Commits | Outcome / key decision |
|---|---|---|
| A4 (`[100%]` gate hole) | `bc1be99` | **Rejected the plan's literal `if not _shows_execution`** (would regress promptwright's genuine 45/45). Added `_all_skipped` guard (`maintainer.py`). Residual: bare-`[100%]`-no-skip-summary still finalizes (symmetric twin of promptwright) → follow-up. |
| A3 (persist run summary) | `2c0f9c5` | Best-effort copy to `output_dir/<instance_id>.run_summary.json`. **Runner must confirm `score_agent` ignores `*.run_summary.json`** (see R1). |
| A2 (real in-sandbox signal) | `3704daf`, `ac2489b` | `verification_source` was **already** emitted; added `in_build_pass_rate`+`in_build_passed_ge1` (honest, only from real pytest output). Review caught a **Path-3 all-skipped honesty hole** (stamped `passed_ge1=True` on 0 passes + falsely finalized) → fixed; also tightened the active-re-run gate. |
| A1 (heredoc recording-truncation) | `6fdbfe1`, `5d79fd2` | BEHAVIORAL (the truncated opener was also what *executed* → file never built). Real-trace evidence: heredocs are **plain/unfenced** in production → added `_reconstruct_plain_heredoc`. Review caught a **missing-terminator regression** (re-opened Bug 2) → fixed (returns opener-only → synthesis drops it). Fenced-branch gap → follow-up. |
| DROPPED_ENV (bake ENV) | `39069e7`, `9de21d8`, `ddaa140` | Root cause: `RUN export` doesn't persist across Docker layers. django-oauth's real pattern is **inline-prefix** (`X=cfg pytest`), captured via ledger + verified-test-command; `export`+bare-pytest is **architecturally impossible** (sandbox preflight forbids combined setup+verify), so dropping unreferenced exports is *correct*. Hardening: denylist secret-named vars + `PYTHONPATH`. **Dockerfile `$`-escape corrected to `\$`** (empirically proven: `$$` is docker-compose syntax and stores `ab` for `a$b`; `\$` stores `a$b`). DRY: env-bake extracted to `_bake_test_env_vars()`. |

**Blocker for the re-run:** the live `run-20260619-171120` is **credit-wall contaminated** (11/49 result rows are OpenRouter 402; `rat_results.json` never generated) — NOT a valid comparison. The clean re-run (step 5.2) requires topped-up OpenRouter credits AND the new agent commit. Until then, runner-side R1–R3 can be *written* but not *validated*.
