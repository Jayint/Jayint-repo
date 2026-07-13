# v3 e2e benchmark — handoff (2026-07-07)

**Goal:** get `scripts/run_v3_e2e.py` into a state where it can run 15 medium-sized
benchmark repos, then run them. Phases: (A) perf so cycles are fast, (B) robustness
fixes, (C) run 15.

**Branch:** `john-v3-multi-lang` — commits are LOCAL, NOT pushed. Current HEAD `50e5801`.
`git add` only named files (working tree has unrelated `.context/codex-session-id` +
untracked plan docs). Docker native/foreground. macOS has no `timeout`/`setsid` binary.
Creds: `<scratchpad>/llm.env` (OPENROUTER_API_KEY); model `deepseek-v4-flash`.

---

## TL;DR — the one thing left

The one thing left (wall-clock, not correctness): the loop converges but burns ~3 cycles per
over-predicted/unsatisfiable node (phantom `tool:less`, optional `atheris`/`mypyc`), so
residual/over-predicted repos take ~5–8 cycles and a 15-run would be ~3–5 hr. The
fast-termination fix — when the testability gate passes, declare DONE regardless of remaining
MISSING optional nodes — is specced but not started (was near the context limit). Full spec in
"THE FAST-TERMINATION FIX" below.

Everything else (Phase A perf + B7 churn fix + B8 ensure-pytest) is landed, committed locally,
and verified. Suite green.

---

## STATUS: what's DONE (all committed locally, verified)

Full test suite green: **tests/envstate 174, tests/depgraph 1126** (1 pre-existing
collection error `tests/evals/test_stage_translate.py` — envstate package shadowing,
orthogonal, ignore).

**Phase A — performance (done, validated):**
- `b89e97c` perf(sandbox): `container.stop(timeout=0)` ×3 sites.
- `f98aeb0` fix(e2e): enable pip/apt cache volume + `sandbox.close()` on exit (fixes the
  22.85GB dangling-image leak). CONFIRMED reset_to_base re-applies volumes → cache persists across cycles.
- `bb53b4d` perf(loop): bake python-shim into setup.sh + drop per-cycle live-shim commit
  (also fixes a latent gap — the live shim never reached the fresh-replay container).
- `1008b1a` refactor(sandbox): delete dead per-command snapshot commit (run_v3 never consumes it).
- `9fcdf9e` perf(loop): VerifyTestCache memo — collapse the 2 back-to-back pytest gate runs.
- `ba1cba7` perf(loop): skip redundant post-emit certify_refresh when replay clean + graph fully certified.
- Result: per-cycle ~50–110s (was the 4–5min the handoff cited) — at its DESIGN FLOOR
  (fresh-replay-every-cycle + one-pip-per-package, both deliberate). A4 batch-pip CANCELLED
  per user directive (keep one `pip install` per package for debuggability; see memory
  `per-package-install-no-batch`). Construction (~400s/repo) is the dominant remaining cost
  (deferred; simple work-removing levers designed in `design/construction-parallelize.md`).

**Phase B — robustness (done):**
- `61e21c5` test(runtime): fixed 2 pre-existing stale runtime-discovery tests (yaml→PyYAML).
- B7 churn fix — the loop no longer churns FOREVER on unfixable failures:
  `a77c3c0` (no-progress detector, gate_signature.py) + `28acad0` (thread failing test output
  as repair evidence) + `69b6359` (diagnose RESIDUAL when tool/config token co-occurs with
  AssertionError) + **`8221cc7` (THE working fix: drop RESIDUAL-diagnosed nodes from the
  frontier + a handout-immune `_residual_stall` counter → `GIVEUP_RESIDUAL`).**
- B8 ensure-pytest — **`50e5801` bake `pip install pytest` into setup.sh preamble** (guarded,
  after the python shim). VALIDATED LIVE: cachetools `test=False`→clean `DONE`; tomli tests PASS.
  (First tried a resolver-root injection — reverted: it bloated every graph with a pytest-closure
  resolve, slowing construction AND the test suite. The bake approach touches only 2 offline goldens.)

**Live validation (small-repo pilots):**
- itsdangerous, needrepair, cachetools → clean **DONE** (both gates).
- click, tomli → **converge but were watchdog-killed before reaching their terminal** (see below).

---

## THE REMAINING ISSUE — slow convergence (wall-clock, NOT correctness)

The loop only checks tests when the scheduler frontier is EMPTY. Any repo with an
unsatisfiable / over-predicted node keeps that node MISSING on the frontier and hands it out
`attempt_cap=3` times before moving on — so each stuck node burns ~3 cycles before the loop
reaches its terminal:
- **click** — a phantom `tool:less` (tests FAIL, residual) → gradeable `GIVEUP_RESIDUAL` ~cycle 5.
- **tomli** — over-predicted optional imports `import:atheris` (cyc 1–3) then `import:mypyc`
  (cyc 4–6); **tests PASS every cycle** but the loop keeps repairing the optionals; would reach
  `DONE` ~cycle 7–8 after both exhaust attempt_cap.

At ~110s/cycle + ~400s construction, a stuck repo takes ~15–25 min. **A 15-run is ~3–5 hr.**
Results ARE gradeable (planner_done / planner_giveup) — this is purely wall-clock.

### THE FAST-TERMINATION FIX (next work — NOT started; ~80% context reached)

**Core idea: when the testability gate PASSES, declare DONE regardless of remaining MISSING
nodes** (if tests pass without them, they are over-predictions — recall-first philosophy says
tests-passing is the SUFFICIENT signal). This collapses tomli-style churn to cycle 1.

- The loop ALREADY samples the verified gate each cycle: `_gate_passed_now = _run_tests_verified()`
  in `src/envstate/orchestrator.py` run_v3 (added by `a77c3c0`, near the `GIVEUP_NO_PROGRESS`
  block). **When `_gate_passed_now` is True AND the installability gate passes, short-circuit to
  the DONE door** (`_finalize_if_replayed(DONE)`) instead of continuing the scheduler frontier.
  Verify anti-hollow-success is preserved (`done_gate._verified_test_run_passed` already rejects
  hollow passes — a verified pass is real).
- For the residual-FAIL case (click), optionally speed up: change the `_residual_stall` increment
  condition (E8 in `design/residual-node-drop.md`) from `elif _cycle_had_residual:` to
  `elif not _gate_passed_now:` (count ANY cycle with failing tests + no real ENVIRONMENT repair)
  → gives up ~cycle 3 instead of ~5. A real ENVIRONMENT repair still resets it (never cuts a
  progressing multi-cycle repair). CAUTION: re-check IT tests in `tests/envstate/test_v3_residual_giveup.py`.
- Touch points: `src/envstate/orchestrator.py` run_v3 loop (mainly); maybe `graph_scheduler.py`.
  Add tests: a repo whose tests pass with a MISSING optional node → DONE at cycle 1–2 (not churn);
  and confirm cachetools-style clean-DONE + the existing residual IT tests still pass.
- Design context: `<scratchpad>/design/residual-node-drop.md`, `residual-giveup-fix.md`,
  `live-repair-analysis.md`.

---

## HOW TO RUN (once fast-termination lands, or now with a long watchdog)

Scratchpad = `/private/tmp/claude-501/-Users-john-john-v3-multi-lang/fd87cef1-ff68-471f-b2cf-bd515da5690c/scratchpad`

- **Single timed run:** `bash <scratchpad>/run_timed.sh <repo_path> <label> python:3.11-slim <deadline_s>`
  (native arm64 base, V3_LOOP_VERBOSE + ENVSTATE_LLM_LOG, per-line timestamps, hard watchdog).
  Parse timing: `python3 <scratchpad>/parse_timing.py <scratchpad>/out/<label>-timed.log`.
- **Matrix:** `run_pilot_matrix.sh` / `run_reval.sh` are examples — copy, set the REPOS list +
  per-repo deadlines, run DETACHED, poll `out/<name>-summary.txt`.
- **Detach long runs** (harness SIGTERMs tracked background bash ~500s): launch via
  `nohup python3 -c "import os; os.setsid(); os.execvp('bash', ['bash','<script>'])" >drv.log 2>&1 &`
  then watch with the Monitor tool on the summary file. Watchdog per repo must EXCEED the loop's
  terminal (~cycle 5–8 today ⇒ ~1500–1800s; ~cycle 1–3 after the fast-termination fix ⇒ ~900s).
- **15 medium repos:** curated in `<scratchpad>/design/medium15-corpus.md` — 7 from
  `datasets/rat_python_medlarge15.json` (anthropic-sdk, mvt, python-semantic-release, postgres-mcp,
  vizro, typer, slither) + 8 supplemented pure-python (tomli, cachetools, more-itertools, arrow,
  markupsafe, tenacity, wrapt, jinja2). Clone commands + per-repo risk flags in that doc
  (slither→solc, postgres-mcp→Postgres, vizro→monorepo scope vizro-core, typer→308 test files).
  Already cloned in `<scratchpad>/repos/`: click, itsdangerous, needrepair, tomli, cachetools.
- Report per repo: base image, node count, cycles, stop_reason, both gate results, patchgate count,
  wall-clock. PRUNE Docker between repos (`docker image prune -f`; the user's ~21 `depgraph-probe-*`
  containers are THEIRS — never touch; killed runs leak containers since close() is skipped).

---

## GOTCHAS learned this session

- **Subagents die if they launch a background test run and wait for a notification** (they aren't
  re-invoked like the main loop). Have implementer subagents run tests FOREGROUND/synchronously,
  or verify+commit their work yourself. Two agents this session left work uncommitted this way
  (I recovered + committed manually).
- `pytest-timeout` is NOT installed (can't `--timeout` the suite).
- tests/depgraph baseline ~75s but can hit ~240s under system load (many Docker containers running) —
  not a code regression.
- Host python low-CPU ≠ stalled (blocks on exec_run while the container works).
- The v3 loop's phantom-minting is nondeterministic (depends on whether a cycle's pytest blob
  carries a literal `AssertionError`) — that's why the Part-C same-text co-occurrence guard misses
  and the frontier-drop fix (8221cc7) is the robust one.

## Durable artifacts
- Ledger: `<scratchpad>/MASTER-PLAN.md` (full chronological detail).
- Designs: `<scratchpad>/design/*.md` (residual-node-drop, ensure-pytest, residual-giveup-fix,
  live-repair-analysis, construction-parallelize, simplicity-pass, snapshot-commit, testgate-certify,
  medium15-corpus).
- Memories updated: `per-package-install-no-batch` (+ index in MEMORY.md).
