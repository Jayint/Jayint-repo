# How to run the Repo2Run baseline on the curated 50 (official paper code)

**Date:** 2026-06-07. Goal: a Repo2Run-baseline ESSR on our curated 50, comparable to
RAT (0.6775) and DockerAgent (0.3729). "Repo2Run baseline" = the external **Repo2Run
configuration agent**, wrapped by the paper's `eval/models/repo2run_model.py:Repo2RunModel`.

## How the official code drives it

`eval/repo2run/eval_repo2run.py` → `eval/common/eval_runner.run_evaluation(model_class=Repo2RunModel)`:
- Uses **W&B Weave** + loads the dataset from **HuggingFace** by language split
  (`python_all` / `python_eval` via `--use-eval`). It does **NOT** accept a custom
  `--repos-json`, so it cannot be pointed at our curated 50 without pushing them as a HF split.
- `Repo2RunModel.predict(full_name)` (eval/models/repo2run_model.py): clones the repo →
  invokes `python3 <root_path>/Repo2Run/build_agent/main.py --full_name … --sha … --root_path … --num_turn … --llm …`
  → expects Repo2Run to emit `output/<full_name>/Dockerfile` + `track.json` → `docker build` →
  runs tests in-container by mounting `<root_path>/libkit/tools/run_pytest.py` + `run_pytest_collect.py`
  → scored by the SAME `eval/common/scorers.py` we used for RAT/DockerAgent.

## Two routes

### Route A — our runner (RECOMMENDED, comparable to RAT/DockerAgent)
`run_rat_benchmark.py --model repo2run` uses the **same `Repo2RunModel` + same scorers**, driven
by our runner on our `--repos-json` 50 (no Weave/HF). This is the apples-to-apples path (same 50,
same honest patched scorer as RAT 0.6775 / DockerAgent 0.3729).

```bash
# AFTER prerequisites below are met:
cd /opt/runanything/src        # root_path must contain libkit/ AND Repo2Run/
export DOCKERAGENT_ROOT=/opt/rat-bench-integration RAT_ROOT=/opt/runanything/src RAT_PYTEST_TIMEOUT=1800
/opt/rat_venv/bin/python /opt/rat-bench-integration/run_rat_benchmark.py \
  --model repo2run \
  --repos-json /opt/rat-bench-integration/datasets/rat_python_hard_subset.json \
  --root-path <ROOT_WITH_libkit_AND_Repo2Run> \
  --llm deepseek/deepseek-v4-flash \
  --concurrency 12 --timeout 7800 --num-turn 15      # repo2run default num-turn is 15, not 30
```

### Route B — official driver (NOT our 50)
`scripts/eval_repo2run.sh` → `eval/repo2run/eval_repo2run.py --use-eval` runs the paper's exact
Weave pipeline, but on the HF `python_eval`/`python_all` split (the 500 / eval subset), **not** our
curated 50. Needs `WANDB_API_KEY` + `HF_TOKEN`. Use only to reproduce the paper's own number, not
for our head-to-head.

## BLOCKERS (currently NOT met — verified on VM 2026-06-07)

1. **The external Repo2Run tool is not present anywhere.** `Repo2RunModel` requires
   `<root_path>/Repo2Run/build_agent/main.py`; `find /opt … -path '*Repo2Run/build_agent/main.py'`
   returns nothing (only the eval *wrapper* `/opt/runanything/src/eval/repo2run/` exists). **The
   anonymized paper repo does NOT bundle the Repo2Run baseline** (no top-level `Repo2Run/`); it must
   be obtained separately. The README clone address is anonymized (`<Hidden Repository Address>`).
   → NEED the actual Repo2Run source (e.g. the public Repo2Run project the paper used as baseline).

2. **root_path structure.** Unlike RATModel/DockerAgentModel (which copy tools from the harness dir),
   `Repo2RunModel` reads `<root_path>/libkit/tools/run_pytest.py` + `run_pytest_collect.py` (both
   exist under /opt/runanything/src ✓) AND `<root_path>/Repo2Run/…`. So `--root-path` must be a dir
   that contains BOTH `libkit/` and `Repo2Run/` — i.e. `/opt/runanything/src` itself, or a fresh dir
   staged with a `libkit` symlink + the `Repo2Run` checkout. (Our `./rat_run_*` output dirs do NOT
   qualify — this differs from how RAT/DockerAgent ran.)

3. **Repo2Run's own dependencies** (its `requirements.txt`) must be installed into `/opt/rat_venv`.

4. **Do NOT confuse** with our repo's `run_repo2run_benchmark.py` — that runs OUR native agent on the
   Repo2Run *Table-15 dataset* (a different benchmark, being retired), NOT the Repo2Run baseline agent.

## Setup steps once the Repo2Run source is available
1. `git clone <repo2run-source> /opt/runanything/src/Repo2Run` (so `…/Repo2Run/build_agent/main.py` exists).
2. `/opt/rat_venv/bin/pip install -r /opt/runanything/src/Repo2Run/requirements.txt`.
3. Either run with `--root-path /opt/runanything/src` (outputs land under the harness dir), or stage a
   clean root: `mkdir rat_run_repo2run && ln -s /opt/runanything/src/libkit rat_run_repo2run/libkit &&
   ln -s /opt/runanything/src/Repo2Run rat_run_repo2run/Repo2Run` then `--root-path ./rat_run_repo2run`.
4. Run Route A command above; score with the same honest scorer; compare to RAT 0.6775 / DockerAgent 0.3729.

**Paper reference:** Repo2Run = 44.8 ESSR on the full python set (Table 2) — different population; our
curated-50 number will differ. The point is a same-50 3-way head-to-head.
