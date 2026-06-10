# Repo2Run baseline port — handoff (2026-06-07)

Goal: a **Repo2Run baseline ESSR on the curated 50**, comparable to RAT (0.6775) and
DockerAgent (0.3729) — same model, harness, honest scorer. Driven via
`run_rat_benchmark.py --model repo2run`. The public bytedance/Repo2Run is broken-as-published;
we are porting it. **Status (2026-06-07): PORT COMPLETE — all 8 bugs fixed. wafw00f smoke passes
end-to-end via the full harness (status=success, image builds, 48/48 tests pass, pytest_executed=true,
pass_rate=1.0). Full 50-repo run LAUNCHED (concurrency 10, num-turn 30); auto-save watcher running.**

## Environment (VM)
- ssh `root@167.233.64.96`; SSH Bash needs `dangerouslyDisableSandbox: true`.
- Repo2Run staged at `/opt/rat-bench-integration/rat_run_repo2run/Repo2Run` (clone of bytedance/Repo2Run).
- Root for runs: `/opt/rat-bench-integration/rat_run_repo2run/` (has `libkit` symlink → `/opt/runanything/src/libkit` + `Repo2Run/`).
- Interpreter: `/opt/rat_venv/bin/python` (has openai 2.41, docker, pexpect, openpyxl, pandas, pipreqs). The Repo2Run subprocess uses `python3` → run with `PATH=/opt/rat_venv/bin:$PATH`.
- Pristine clone for diffing: `/tmp/Repo2Run_inspect` (re-clone if gone: `git clone --depth 1 https://github.com/bytedance/Repo2Run.git /tmp/Repo2Run_inspect`).
- OpenRouter creds come from `/opt/rat-bench-integration/.env` (loaded by run_rat_benchmark via dotenv; inherited by the subprocess). **Key still needs rotation (leaked earlier).**

## Bugs found & FIXED (vendored: `patches/repo2run/`, applied live on VM)
1. **0001** `build_agent/utils/llm.py` — used removed `openai.ChatCompletion`; rewritten to new SDK + OpenRouter + deepseek (Alibaba pin), returns `(list_of_contents, usage_dict)`, default max_tokens 4096.
2. **0002** `build_agent/main.py` — passed `100` as the model + no `--num_turn`; now passes `llm` + adds `--num_turn`→`Configuration(max_turn)`.
3. **0003** `build_agent/agents/configuration.py` — model gate accepted only gpt/claude (line 236,548) → also accept deepseek; AND guarded the `/dev/vdb` disk check (line ~331, `float('')` crash on boxes without /dev/vdb).
4. **0004** `build_agent/utils/sandbox.py` — (a) repo `docker cp` used `{project_directory}`→`{root_path}` (lines 288/454/461); (b) removed hardcoded `cpuset_cpus='0-19'` (needs 20 cores; box has 8).
5. **0005** `build_agent/utils/integrate_dockerfile.py` — TWO fixes (combined patch `0005-integrate-dockerfile.patch`):
   (a) `if len(outer_command)>0` NameError on the `COPY code_edit.py` guard;
   (b) **bug #8** — see below.

6. **0008** (in the same `0005` patch) — **docker build of the generated Dockerfile** (see RESOLVED section).

Result: deepseek LLM drives the config agent end-to-end AND the generated Dockerfile builds, tests run,
and a scored `_result_row.json` is produced. wafw00f: 48/48 tests, pass_rate=1.0, status=success (~184s).

## RESOLVED — bug #8 (docker build of the generated Dockerfile)
Stock `integrate_dockerfile.py` unconditionally emitted, whenever `output/<repo>/patch/` existed:
```
COPY search_patch /search_patch     # nothing named search_patch is staged (dir is patch/)
COPY code_edit.py /code_edit.py     # never invoked by any RUN step; not staged
```
…and that `patch/` dir **always** exists (generate_diff.py always writes `patch/final_patch.diff`),
so `docker build` died on the first `COPY` for *every* repo. Root cause: those COPYs belong only to the
`code_edit.py` replay path (whose RUN steps are `git apply /patch/patch_N.diff`), but stock code gated
them on patch-dir existence instead of actual code_edit usage, used the wrong dir name (`search_patch`
vs `patch`), and dragged in a dead `COPY code_edit.py` (replay uses `git apply`, never re-runs the tool).
`final_patch.diff` itself is a record-keeping artifact (e.g. for wafw00f it just notes the repo's own
Dockerfile was deleted during setup) and is never meant to be applied — the rebuild re-clones + checks
out the SHA fresh, then sets up the env by replaying the agent's pip/version commands.

**Fix (vendored in `0005-integrate-dockerfile.patch`):**
- `copy_st`: `COPY search_patch /search_patch` → `COPY patch /patch` (matches the `/patch/patch_N.diff` apply path).
- Track `has_code_edit` (set True only when the agent ran `python /home/tools/code_edit.py`; reset on base-image change).
- Gate the single `COPY patch /patch` on `has_code_edit` (not patch-dir existence).
- Drop the dead `COPY code_edit.py` line + its now-unused `copy_edit_st` variable.
- Reset `diff_no = 1` on a base-image change (post-review follow-up) — closes a pre-existing
  edge case where code_edit *after* a FROM change emitted `git apply -R` of a cleared patch.
  Output-neutral for the curated 50 (verified: no repo does code_edit both before and after a base change).
→ dep-install-only repos (most of the 50, e.g. wafw00f) emit no COPY and build clean; code_edit repos
get a consistent `COPY patch /patch` + `git apply /patch/patch_N.diff`.

No further bugs surfaced in the test/score step — the wrapper reuses the same `run_pytest.py` +
`success/collect/pass_rate` scorers as RAT/DockerAgent, so parity holds.

## How to re-smoke (single repo, ~2-3 min)
```bash
ssh root@167.233.64.96   # (dangerouslyDisableSandbox)
cd /opt/rat-bench-integration
rm -rf rat_run_repo2run/output/EnableSecurity/wafw00f rat_run_repo2run/utils/repo/EnableSecurity/wafw00f rat_run_repo2run/_smoke_wafw00f.log
export RAT_ROOT=/opt/runanything/src DOCKERAGENT_ROOT=/opt/rat-bench-integration RAT_PYTEST_TIMEOUT=1800
export PATH=/opt/rat_venv/bin:$PATH
nohup /opt/rat_venv/bin/python run_rat_benchmark.py --model repo2run --only EnableSecurity/wafw00f \
  --root-path ./rat_run_repo2run --repos-json datasets/rat_python_hard_subset.json \
  --llm deepseek/deepseek-v4-flash --timeout 7800 --num-turn 15 \
  > rat_run_repo2run/_smoke_wafw00f.log 2>&1 < /dev/null &
# success markers: output/<repo>/Dockerfile builds, "Running test container", _result_row.json with pytest_executed=true
```
Tail the log: `rat_run_repo2run/_smoke_wafw00f.log`. Per-repo outputs: `rat_run_repo2run/output/EnableSecurity/wafw00f/`.

## After the smoke passes → full 50-repo run + save
```bash
cd /opt/rat-bench-integration
export RAT_ROOT=/opt/runanything/src DOCKERAGENT_ROOT=/opt/rat-bench-integration RAT_PYTEST_TIMEOUT=1800 PATH=/opt/rat_venv/bin:$PATH
nohup /opt/rat_venv/bin/python run_rat_benchmark.py --model repo2run \
  --root-path ./rat_run_repo2run_full --repos-json datasets/rat_python_hard_subset.json \
  --llm deepseek/deepseek-v4-flash --concurrency 12 --timeout 7800 --num-turn 30 \
  > _run50_repo2run.log 2>&1 < /dev/null &
```
NOTE: use **`--num-turn 30`** for parity with RAT/DockerAgent (Repo2Run's own eval default is 15). The
staged root `rat_run_repo2run` mixes smoke + per-repo `Repo2Run/`/`libkit`; for the full run either reuse
it (`--root-path ./rat_run_repo2run`, it has libkit+Repo2Run) or stage a fresh root the same way.
Then auto-save locally: `scripts/watch_and_save.sh rat_run_repo2run_full <pid> --agent repo2run &`
→ lands in `results/repo2run/<date>-repo2run_full/` with a README + ESSR.

## Auto-save scripts (built, local, chmod+x)
- `scripts/save_run.sh <vm_run_subdir> [--smoke] [--agent N] [--name N]` — pulls output+aggregate+logs → `results/<agent>/<date>-<name>[_smoke]/`, infers agent, computes ESSR, writes README. Appends `_smoke` for smoke/single-repo runs.
- `scripts/watch_and_save.sh <vm_run_subdir> <pid> [save args]` — polls run, fires save_run.sh on completion.

## Baselines already banked (the 2-way is solid)
- `results/rat/2026-06-07-corrected/` — RAT ESSR **0.6775** (46/50). 
- `results/dockeragent/2026-06-07-baseline/` — DockerAgent ESSR **0.3729** (32/50).
- Reports: `HEADTOHEAD_DOCKERAGENT_VS_RAT.md`, `RAT_BASELINE_FIDELITY_REPORT.md`, `DOCKERAGENT_RUN_SANITY.md`, `RESIDUAL_TRIAGE.md`. All num-turn 30, deepseek-v4-flash, honest patched scorer.

## Constraints
- READ-ONLY on other runs' dirs; never delete `.env`, `results/`, `rat_run_rat_corrected`, `rat_run_dockeragent`, `workplace/`, `memory/`. Don't print the OpenRouter key.
- Label the Repo2Run result "repaired Repo2Run fork (deepseek-v4-flash)" — it's a heavily-patched port, not stock; the paper's reported 44.8 used a different setup.
