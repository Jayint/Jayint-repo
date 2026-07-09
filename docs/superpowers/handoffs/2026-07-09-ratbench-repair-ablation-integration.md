# Handoff — integrate the react repair-ablation into the RAT runner (ESSR scoring)

**Branch:** `john-v3-multi-lang` (shared — commit locally, append-only; pushing is OK when explicitly asked, clean fast-forward only). **Date:** 2026-07-09. **Memory:** `[[react-arm-vm-state-and-radical-parity]]`, `[[react-script-repair-arm-design]]`, `[[ratbench-essr-divexec-deviation]]`, `[[v3-core-ratbench-integration-landed]]`.

## Goal
Run the react repair loop **through the RAT runner** so results are scored by RAT's own mechanism (**ESSR**, `case_studies`, junit/pytest JSONs) — identical artifacts to the baseline RAT runs — but with **construction SKIPPED**: seed each instance from the **pre-generated `setup.sh`** and let ONLY the repair loop vary. This purely measures the repair loop's gain, comparable to the other runs. Supersedes the bespoke `repair_ablation_sweep.sh` (which produces a non-RAT `summary.csv` — keep as a quick fallback).

## State (all DONE, don't redo)
- React arm fully built + history overhaul + lineno, committed `531a2a1..fb93887`, **pushed** to `origin/john-v3-multi-lang` (@ `fb93887`).
- **Deployed on VM** as a clean git worktree: **`/opt/agents/john-react`** @ `fb93887`. Run it with **`/opt/rat_venv/bin/python3`** (the venv with `pyelftools`; bare `python3` lacks deps).
- **DO NOT TOUCH `/opt/agents/john-planner-v3`** — it has ~37 uncommitted WIP files (construction-side). The worktree exists precisely to avoid clobbering it.
- Seed corpus: **`/opt/runs/john-planner-v3/construction-python50-20260707-072356/output/<owner>/<repo>/setup.sh`** (50 repos; each also has `eval_build/Dockerfile` (the FROM base image) + `v3_src`).
- Seed-mode path ALREADY EXISTS + verified: `run_react_arm(graph=DepGraph(), initial_script=<seed>)` applies `strip_graph_framing` (removes all 467 `#@node`/`requires=`/`DO NOT EDIT` graph annotations — verified on a real seed; clean baseline). Local smoke on rq PASSED (widened `connection refused: localhost:6379` signature fired; 0/5 → repair → 683/683 → DONE).
- MiniMax baseline model = **`MiniMax-M2.7-highspeed`** (key in `/opt/harness/.env`; routes to MiniMax via slug prefix regardless of `LLM_API_PROVIDER=openrouter`).

## The RAT seam (recon done 2026-07-09 — CORRECTED)
The earlier draft's claim that the model "imports v3 directly (NOT a `run_v3_e2e` subprocess)" was **WRONG**. The real chain:
- **`/opt/harness/run_rat_benchmark.py`** → `_make_model(...)` dispatches `dockeragent | rat | repo2run | claudecode`.
- **`dockeragent` = `DockerAgentModel.predict()`** (`/opt/harness/eval/models/dockeragent_model.py`, ~120 lines — DO NOT TOUCH). It does NOT construct anything itself; it delegates to **`MultiDockerEvalAdapter(out_dir).process_single_instance(...)`** (imported from OUR repo via `DOCKERAGENT_ROOT`), consumes `res["dockerfile"]`/`res["setup_scripts"]`/`res["base_image"]`, then `docker build` → mounts RAT's `run_pytest[_collect].py` → copies result JSONs → RAT scores.
- **`multi_docker_eval_adapter.py::_run_v3` (OUR repo) shells out to `scripts/run_v3_e2e.py` as a SUBPROCESS** (`_RUN_V3_E2E`), gets a certified `setup.sh` + resolved base, and `_render_dockerfile` bakes it into a self-contained Dockerfile. This is the construct→emit flow — and the insertion point.
- **ESSR** = `/opt/harness/scripts/compute_essr.py` (consumes the pytest JSONs). Paper ESSR is ÷exec; our ÷all is coverage-penalized — report ÷all + coverage per `[[ratbench-essr-divexec-deviation]]`.

## IMPLEMENTED 2026-07-09 (local, TDD; committed on `john-v3-multi-lang`)
Env-gated repair-only mode in **`multi_docker_eval_adapter.py` ONLY** — mirrors the existing `V3_CONSTRUCTION_ONLY` gate. `run_v3_e2e.py`, `dockeragent_model.py`, `run_rat_benchmark.py`: **UNCHANGED** (run_v3_e2e already had `--arm react --seed-script`).
- **`V3_REPAIR_ABLATION=1`** + **`V3_SEED_DIR=<construction run>/output`** turns it on.
- `_resolve_seed(full_name)`: maps `<owner>/<repo>` → `<V3_SEED_DIR>/<owner>/<repo>/setup.sh`; base image from that instance's `_meta.json["base_image"]`, falling back to `eval_build/Dockerfile` FROM. Missing/empty seed → raises → clean `no_dockerfile` skip (the 1/50 empty-seed repo).
- `_run_v3(..., full_name=, max_steps=)`: when seeded, appends `--arm react --seed-script <seed> --max-steps <n>` and forces `--base-image <seed base>` (seed mode rejects `auto`); `elif V3_CONSTRUCTION_ONLY` (mutually exclusive — ablation wins). Default path byte-identical.
- Tests: `tests/test_multi_docker_eval_adapter.py` (+8; 19 pass). Full react_repair+adapter suite 100 pass.
- **To run on VM** (after deploy to `/opt/agents/john-react` @ new SHA, `DOCKERAGENT_ROOT=/opt/agents/john-react`):
  ```
  V3_REPAIR_ABLATION=1 \
  V3_SEED_DIR=/opt/runs/john-planner-v3/construction-python50-20260707-072356/output \
  DOCKERAGENT_ROOT=/opt/agents/john-react \
  /opt/rat_venv/bin/python3 /opt/harness/run_rat_benchmark.py \
    --model dockeragent --llm MiniMax-M2.7-highspeed --repos-json <python50.json> [--num-turn 30]
  ```

## VERIFIED end-to-end (2026-07-09) — deployed `/opt/agents/john-react` @ `e9fe34c`
Two single-instance full-worker runs through the RAT pipeline (`--only`, `MiniMax-M2.7-highspeed`, `--repair-mode off`), both `status=success` with valid RAT scoring:
- **`coderamp-labs/gitingest`** (happy path, non-VCS): baseline built but **0 tests executed** → **1 repair patch** → **157/160 passed** (0.9812, `pass_rate_exclude_code_issues=1.0`; the 3 "fails" are dead-bitbucket network tests). 160 executed = NO skip-gaming; matches construction exactly. Proves the loop does REAL repair and the green is valid.
- **`bruin-data/ingestr`** (VCS/`hatch-vcs`): after the Bug-A pin fix, the seed's `pip install -e .` succeeds; loop reaches DONE at baseline with the clean real seed (0 patches). 5 passed / 14 skipped (live-DB tests, same as construction). Pin verified: v3_src is-shallow=false, 336 tags, HEAD==construction `head_sha` (`a98d1193`), `git describe`=`v1.0.68`, eval Dockerfile `checkout --detach a98d1193`.

**Two bugs the smoke caught + fixed (committed, pushed, deployed):**
- **Bug B — `4155197`** (`src/react_repair/actions.py` + `history_view.py`): the planner accepted ANY ```` ```bash ```` fence as a patch, so MiniMax wrapping a read-only probe (`Action: cat …`, bare `find … | head`) REPLACED the whole setup.sh with a non-installing one-liner → build "succeeds" installing nothing → skip-heavy suite gamed the gate (FALSE GREEN). Fix: a single-line fence that is a mis-wrapped explore (an `Action:` directive OR a bare read-only investigation command in the probe allowlist) is recovered as the explore it meant, not applied as a patch. Also: explore history now carries a compact finding, not just the command.
- **Bug A — `e9fe34c`** (`multi_docker_eval_adapter.py`): adapter cloned current HEAD, shallow (`--depth=1`, no tags) → (1) drifted tree = unfaithful A/B, (2) VCS-versioned backends (hatch-vcs/setuptools_scm) can't compute a version without reachable tags → `metadata-generation-failed`, seed never reproduces baseline. Fix (ablation only): `_seed_head_sha` reads `_meta.json head_sha`; `_clone`/`_render_dockerfile` do a FULL clone (history+tags) pinned to that commit, in BOTH the react Sandbox and the eval image.

Tests across the two fixes: adapter 25 pass, react_repair suite; combined **123 pass**.

## Concurrency verified (2026-07-09) — deployed @ `2e82f2e`
A 2-repo scheduler run (`--concurrency 2`, ingestr+gitingest) FIRST exposed two bugs, then (after fixes) came back clean:
- **Bug C (`2e82f2e`, actions.py):** a fenced block whose EVERY meaningful line is read-only (≥2 lines) was applied as a patch → setup.sh replaced by a non-installing probe script → false green. Now `invalid` → re-prompt. (Seen: gitingest 2-line version-probes, ingestr 3-line find/cat.)
- **Bug D (`2e82f2e`, sandbox.py + run_v3_e2e.py):** concurrent Sandboxes shared fixed-name rw cache volumes (`jayint_{pip,uv,apt}_cache`) → cross-container race + cross-repo contamination. New `isolate_cache` (unique per-run volumes, removed on close) enabled for the react arm; default shared (construction unchanged).
- **Tokens (`2e82f2e`, loop.py + adapter):** loop now emits `[Tokens] Input/Output/Total` per call; adapter relays them from the swallowed subprocess stdout into the per-repo `run.log` + writes `usage.json`.

Post-fix concurrency-2 result: ingestr real green (5/19, 10-line script), gitingest **157/160** (was 0 under the cache race), 0 leaked volumes, tokens captured. `unified_metrics.py repair-ablation-**dockeragent**=<root>` → **arm=arm0**, T1 EBSR 1.00 / real-success 1.00 / ÷all=÷exec **0.991**, T4 **agentLoopTok=25384**. (Name MUST contain `dockeragent` so arm0 telemetry reads the tokens; the label only affects T2/T4, not the T1 headline which is raw-pytest.)

## Remaining
- **Run the full 49** (1 repo empty seed → clean `no_dockerfile` skip). Scheduler: `--concurrency N` (env: `V3_REPAIR_ABLATION=1`, `V3_SEED_DIR=<constr>/output`, `DOCKERAGENT_ROOT=/opt/agents/john-react`), then score with `unified_metrics.py repair-ablation-dockeragent=<root>` (aggregate `rat_results.json` is written by the scheduler). Watch disk (full clones); start N=4.
- Compare vs the construction-only baseline (`V3_CONSTRUCTION_ONLY=1`) to isolate the repair-loop gain (ESSR ÷exec + tokens).
- **Weak-discriminator caveat:** repos whose tests mostly skip without live services (ingestr: 14/19) barely move with env quality — their pass count isn't a good repair signal. gitingest-style repos (real executed suites) are. When reporting ESSR, note which repos are service-gated.
- Consider a 2–3 repo spot-check before the full 49 (one VCS, one service-repo, one plain lib).

## Watch-outs
- **Service daemons don't survive to the test container.** If the react arm provisions a daemon (e.g. rq's redis) inside `setup.sh`, it starts at Docker *build* time and is dead when RAT `docker exec … pytest` runs later. Same limitation `--construction-only` has. Fix path if service repos matter: `V3_INCLUDE_SERVICES=1` (adds the ENTRYPOINT seam via `services_start.sh`) — but the react arm's repaired setup.sh isn't wired to emit a services script; would need work. For now, service repos under-score; note it, don't let it silently read as a repair failure.
- Seed-mode needs an explicit base image — handled (`_seed_base_image`: `_meta.json` → Dockerfile FROM). Never `auto`.
- deepseek led with prose → first move parsed `invalid` (1 wasted step, self-corrected); MiniMax may differ — `stop=["Observation:"]` is accepted by MiniMax (radical's baseline uses the identical `stop`, so low risk).
- The react arm's `certify` runs but its result is unused in the baseline (graph_context=None) — harmless, ignore.
- Keep `/opt/agents/john-planner-v3` untouched; use `/opt/agents/john-react`.
