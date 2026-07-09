# Handoff — react arm: VM 50-repo repair-ablation sweep + MiniMax M3 verification

**Branch:** `john-v3-multi-lang` (shared — commit locally, append-only, NEVER push/rebase/reset).
**Date:** 2026-07-09. **Memory:** `[[react-script-repair-arm-design]]` has full detail.

## State (all committed, 53 tests green, `python3 -m pytest tests/react_repair/ tests/eval/test_react_repair_eval.py -q`)
The `--arm react` flat-ReAct script-repair arm is built, reviewed, arm-C retired, and hardened:
- `src/react_repair/{gate,actions,log,history,planner,loop,entry,script_prep}.py` + offline eval `src/eval/react_repair_eval/`.
- Session commits `724edb8..e373a3e` (react arm → retire arm C → de-graph → timeout/obs-bound/`--max-steps` → tolerant parser `9fdd560` → anti-gaming guard `d01f2ca` → threshold+plateau `00b01d2` → seed-script `524ab94`/`fa28444`/`e373a3e`).
- Live-validated: happy-path (itsdangerous 297/297, pycurl 847/850), single+multi patch repair, explore, plateau, giveup. Gate = `--test-threshold` (default 0.9) + plateau early-stop (patience 2). Anti-gaming guard proven (stub-fabrication → honest GIVEUP). Services OFF by default (`V3_INCLUDE_SERVICES` unset).

---

## TASK 1 — run the 50-repo repair-ablation sweep on the VM

**Goal:** controlled study of "how much does the repair loop help in total" — run the react loop SEEDED from a prior construction-only run's already-generated `setup.sh` (construction skipped → first-pass script held FIXED, so the only variable is repair). Services off, threshold 0.9, plateau bounds cost.

**Script:** `docs/superpowers/handoffs/repair_ablation_sweep.sh` (dry-run-validated locally: discovery, `FROM` extraction, skip handling, flag threading, continue-on-failure, per-repo `timeout`, summary/tally all confirmed).

**Prior run to seed from:** `/opt/runs/john-planner-v3/construction-python50-20260707-072356` (saves per repo: `output/<owner>/<repo>/setup.sh`, `eval_build/Dockerfile`, `v3_src/`; graph NOT saved — fine, repair-only doesn't need it). **CONFIRMED present on VM, 50 repos** (2026-07-09).

**⚠️ DEPLOYMENT BLOCKER (verified 2026-07-09):** neither the react arm nor the sweep is on the VM yet.
- VM `/opt/agents/john-planner-v3` is at `bedf97c` (branch `john-planner-v3`) — `src/react_repair/` MISSING, `--seed-script` MISSING. It has MiniMax thinking-off (`bedf97c`) but NOT the 8 react commits (`b32c8d7..e373a3e`).
- `origin/john-v3-multi-lang` also points to `bedf97c` — the react commits are **local-only** (not pushed, not on origin, not on any VM checkout).
- `repair_ablation_sweep.sh` is NOT on the VM (local-only under `docs/superpowers/handoffs/`).
- **So before the sweep can run on the VM:** (1) get `b32c8d7..e373a3e` onto `/opt/agents/john-planner-v3` (deploy — respects the shared-branch append-only rule; use the relay/deploy path, ask before pushing), then (2) `scp` the sweep script over. The sweep's `V3_DRIVER=/opt/agents/john-planner-v3/scripts/run_v3_e2e.py` needs the react arm present or every repo errors on the unknown `--arm react`/`--seed-script` flags.

**Run (on the VM):**
```bash
scp docs/superpowers/handoffs/repair_ablation_sweep.sh <vm>:/opt/runs/   # or copy the file over
OPENROUTER_API_KEY=... OPENROUTER_API_BASE=https://openrouter.ai/api/v1 \
V3_DRIVER=/opt/agents/john-planner-v3/scripts/run_v3_e2e.py \
  bash /opt/runs/repair_ablation_sweep.sh \
  /opt/runs/john-planner-v3/construction-python50-20260707-072356 \
  /opt/runs/john-planner-v3/repair-ablation-$(date +%Y%m%d-%H%M%S) \
  deepseek/deepseek-v4-flash 30 0.9
```
(args: `<prior_run_dir> <out_dir> [model] [max_steps] [test_threshold]`; `PER_REPO_TIMEOUT` env, default 2400s.)

**Sanity-check the FIRST repo before letting it run:** its log must show `seed-script mode … (construction skipped, empty graph)` and **NO** `dep-graph: N nodes` line (proves it uses the saved script, not re-constructing), and its step-0 `TEST_GATE` should match that repo's first-pass result from the prior run.

**The metric:** `verdict=PASS` count in `out_dir/summary.csv` − the prior run's first-pass PASS count = the repair loop's net contribution. Expect most repos to show zero delta (construction is robust; the loop only fires on the under-predicting minority).

**Note:** to run the sweep with MiniMax M3 instead of deepseek, first finish Task 2, then pass the MiniMax model slug as arg 3 with `MINIMAX_API_KEY`/`MINIMAX_API_BASE` set (drop the OpenRouter vars).

---

## TASK 2 — verify MiniMax M3 (thinking-off) works for the react arm

**Why:** run the VM sweep with the SAME model as the baseline, reasoning OFF, via `MINIMAX_API_KEY` + `MINIMAX_API_BASE=https://api.minimaxi.com/v1`.

**RESOLVED on VM (2026-07-09):**
- **Exact baseline slug = `MiniMax-M2.7-highspeed`** (NOT `minimax-m3` — that was a guess). Sources: radical `agent.py:234`, `constants.py:1`, README `--model MiniMax-M2.7-highspeed`. Matches our local `src/constants.py` `DEFAULT_LLM_MODEL` exactly. `.lower()` starts with `minimax` → routes to MiniMax.
- **VM HAS the key:** `/opt/harness/.env` → `MINIMAX_API_KEY` (len 125, real), `MINIMAX_API_BASE=https://api.minimaxi.com/v1`, `LLM_API_PROVIDER=openrouter`. The provider being `openrouter` does NOT matter: `libkit/config.py:125` and our `run_v3_e2e.py:160` both OR the model-slug check, so a `MiniMax-*` slug routes to MiniMax regardless. No precedence bug.
- **`stop=["Observation:"]` risk RETIRED:** the radical baseline passes the *identical* `stop=["Observation:"]` to MiniMax (`planner.py:222`) and it is the working baseline. Our planner passes the same. Proven-safe on MiniMax — no fix needed. See `2026-07-09-radical-vs-react-history-design.md`.
- Local `.env` has NO MiniMax key (empty/commented) — so the smoke test must run on the VM, not locally.

**Investigation finding (near-certain: NO code change needed — the react arm already inherits it):**
Commit `bedf97c` added MiniMax M3 thinking-off to the v3 agent centrally in `src/envstate/llm_response.py`:
- `apply_minimax_thinking(client, kwargs)` injects `extra_body={"thinking":{"type":"disabled"}}` when base_url contains `"minimaxi"` (gate), controlled by `MINIMAX_THINKING` env (default `disabled`). It's applied inside `_create_with_backoff` (llm_response.py:222) → **every `complete_with_retry` caller gets it automatically**.
- `complete_with_retry` returns `response_text(response)` (llm_response.py:299) which strips reasoning markup + MiniMax `<minimax:tool_call>` XML (`strip_reasoning_markup`/`strip_minimax_toolcall`).
- The **react arm's ReactPlanner** calls `complete_with_retry(self.client, self.model, messages, temperature=0, stop=["Observation:"])` (planner.py:55) and `_make_compressor` (entry.py) also uses it → both inherit thinking-off + stripped text for free.
- `run_v3_e2e._resolve_llm_endpoint(model)` already routes `minimax-*`/`abab-*` slugs (or `LLM_API_PROVIDER=minimax`) to `MINIMAX_API_KEY`/`MINIMAX_API_BASE`, even when OPENROUTER_* is set.

**So Task 2 is VERIFICATION, not implementation. Do this:**
1. **Confirm the exact MiniMax M3 model slug the baseline uses** — check the VM's `libkit/config.py` `get_llm_config` / the baseline RAT run config. The slug must start with `minimax` or `abab` for auto-routing, else set `LLM_API_PROVIDER=minimax`.
2. **Live smoke test the react arm on MiniMax** (small repo, e.g. `/tmp/itsdangerous`, native arm64):
   ```bash
   MINIMAX_API_KEY=... MINIMAX_API_BASE=https://api.minimaxi.com/v1 \
   PYTHONUNBUFFERED=1 REACT_VERBOSE=1 \
     python3 -u scripts/run_v3_e2e.py /tmp/itsdangerous --arm react \
     --base-image python:3.11-slim --model MiniMax-M2.7-highspeed \
     --out /tmp/mm.sh --trace-out /tmp/mm_trace.jsonl --max-steps 8
   ```
   Confirm: it reaches `DONE`, and inspect `/tmp/mm_trace.jsonl` `plan` records — the `reply_raw` should be CLEAN (no `<think>`/reasoning markup, no `<minimax:tool_call>` XML) and `action.kind` should parse (explore/patch, not `invalid`). If the planner reports lots of `invalid`, the response format needs handling (unlikely — thinking-off + no tools → clean text; the tolerant parser `9fdd560` already accepts any fenced block).
3. **One known RISK to check:** the planner passes `stop=["Observation:"]`. If the MiniMax endpoint rejects the `stop` param (400/bad-request), that's the one fix needed — make `stop` optional/omitted for MiniMax (or catch and retry without it). Verify in the smoke test; if it 400s on `stop`, fix in `planner.py`.
4. **Then** re-run the Task-1 sweep with the MiniMax slug + `MINIMAX_THINKING=disabled` (the default) to match the baseline.

**If the smoke test passes with clean parsing and DONE → no code change; the react arm is MiniMax-ready.** Only write code if step 2/3 surfaces a real gap.

---

## Gotchas (carry forward)
- Always run the driver with `PYTHONUNBUFFERED=1` (else stdout block-buffers to the logfile and looks hung).
- Construction (non-seed mode) takes ~5-6 min/repo and emits no DESIGN tags — early silence is normal. Seed mode skips it.
- Pre-existing container leak in the construction/sandbox probe layer (`depgraph-probe-*`) — confirm the VM harness prunes between repos, or a long run fills disk.
- Deferred (not blocking): gate Knob-3 = failure-classification (env-done iff no import/collection/connection errors remain) to fix false-negatives when a repo legit-caps below threshold.
