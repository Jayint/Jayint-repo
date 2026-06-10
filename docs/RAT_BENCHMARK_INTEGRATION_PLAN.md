# Plan: Run our DockerAgent against the RunAnyThing (RAT) Python benchmark

> Goal: use **RunAnyThing**'s Python benchmark (the new env-setup benchmark replacing Repo2Run)
> to evaluate **our** `DockerAgent` system on identical terms to RAT's own baselines
> (RAT, Repo2Run, SWE-agent, Pipreqs, Installamatic, ZeroShot).
>
> Source analyzed: `RunAnyThing_Anonymous` (anonymized repo), extracted locally to `/tmp/runanything/src`.
> Our system: this repo (`/Users/john/rat-bench-integration`, "Repo Dockerizer Agent").

> **Review status (2026-06-05, verified against real code):** architecture is sound and **will work
> with the §4.2 skeleton as corrected below.** Three blockers in the original draft were fixed after
> reading `multi_docker_eval_adapter.py`: (1) eval image puts the repo at **`/testbed`**, not `/app`;
> (2) `process_single_instance` **returns** the eval Dockerfile as a string — it does not write a file,
> so `predict()` must write a clean build context itself; (3) two distinct repo roots are needed
> (RAT repo for `libkit/`+`eval/`, our repo for `multi_docker_eval_adapter`). The eval Dockerfile is
> **confirmed self-contained** (`git clone … /testbed`), which resolves the old Task 0 unknown.

> **BUILD STATUS — Phase 1 DONE & verified (2026-06-05):** Both files exist and pass every static gate.
> - `eval/models/dockeragent_model.py` (in RAT tree) — verbatim §4.2; `ast.parse` ✅; imports ✅; constructs as a `BaseEvalModel` ✅.
> - `run_rat_benchmark.py` (this repo) — §4.4 + a full argparse CLI (`--repos-json --root-path --limit --offset --timeout --llm --num-turn --tier --category`); `--help` runs end-to-end ✅; offline pipeline (selection→scorers→per-category report→`rat_results.json`) validated with a mocked `predict` ✅; added an empty-selection guard (was `ZeroDivisionError`).
> - **Verified runtime prerequisites for the *run* (these bit the gate; document them):**
>   1. A non-3.14 Python (used **python3.12**); 3.14 has no wheels for parts of the stack.
>   2. `pip install` of: RAT side → `weave`, **`datasets`** (importing `eval.common.scorers` eagerly triggers `eval/common/__init__.py → eval_runner → from datasets import load_dataset`; not a download, just the lib), **`pexpect`** (importing our model triggers `eval/models/__init__.py → rat_model → libkit.environment → pexpect`); our agent side → `docker python-dotenv openai pypdf`. **No `torch`/`sentence-transformers` needed** — they're lazy and only used when long-term memory is on (we leave it off). `sweagent` is **not** import-required (baselines shell out).
>   3. A reusable venv with all of the above is at **`/tmp/rat_venv`** (`/tmp/rat_venv/bin/python`).
> - **EXECUTION BLOCKER (Phases 2–4):** our DockerAgent needs an LLM key — `agent.py:206` reads `MINIMAX_API_KEY` or `OPENAI_API_KEY` (+ `*_API_BASE`) via `load_dotenv()`. No `.env` / no env var is set, so no repo can be run. Drop a `.env` (or export the key) and pick `--llm` to a model that gateway serves, then run the smoke tier.

---

## 1. TL;DR / recommendation

RAT's eval harness is **pluggable by design**. Each method is a `BaseEvalModel` subclass with one
method, `predict(full_name) -> dict`, and **every method is scored by the same shared scorers**
that read pytest result JSONs out of `output/{full_name}/`. Fairness is structural.

So integrating our agent = **write one adapter** `DockerAgentModel(BaseEvalModel)` whose `predict()`
mirrors `eval/models/repo2run_model.py`:

1. clone repo → 2. run **our** `DockerAgent` → 3. emit a self-contained eval `Dockerfile` →
4. `docker build` → 5. `docker run` with RAT's `run_pytest.py` / `run_pytest_collect.py` mounted →
6. `docker cp` the result JSONs to `output/{full_name}/` → 7. return `{status, root_path, full_name}`.

**Build it in two layers:**

- **Path B (do this first) — offline local runner.** No Weave, no W&B, no HuggingFace.
  Loop over `datasets/python/python_repos_all.json`, call `DockerAgentModel.predict()`, then call
  RAT's own scorer functions on the output. Reuses RAT's `scorers.py` + `run_pytest*.py` verbatim →
  **identical metric definitions**, fully offline, fast to stand up.
- **Path A (do this second) — official harness.** `eval/models/dockeragent_model.py` +
  `eval/dockeragent/eval_dockeragent.py` calling `run_evaluation(...)`. Produces paper-comparable
  numbers and W&B dashboards next to RAT's baselines. Requires Weave/W&B + resolving the
  (anonymized) HuggingFace dataset name.

Both layers share the exact same `predict()` body — write it once.

---

## 2. How the RAT harness works (the contract we must satisfy)

### 2.1 Model interface — `eval/common/base_model.py`
```python
class BaseEvalModel(weave.Model, ABC):
    root_path: str
    timeout: int
    @weave.op
    @abstractmethod
    def predict(self, full_name: str) -> dict:        # full_name == "owner/repo"
        ...  # returns {"status": "success"|"error"|"timeout", "root_path": str, "full_name": str, ...}
```

### 2.2 Runner — `eval/common/eval_runner.py :: run_evaluation(...)`
```python
run_evaluation(args, model_class, model_kwargs, weave_project, language="python", use_eval_dataset=False)
```
Flow: set W&B env → `weave.init(weave_project)` (UNCONDITIONAL) → `load_dataset(<HF name omitted>,
split=f"python_all[{offset}:{limit}]", token=HF_TOKEN)` → `weave.Dataset.from_hf(...)` →
`scorers = get_scorers_for_language("python")` → `model = model_class(**model_kwargs)` →
`Evaluation(dataset, scorers).evaluate(model)` (async) → `stop_and_remove_container()`.

Weave maps the dataset's **`full_name`** column to the `predict(full_name)` argument.

### 2.3 Scorers — `eval/common/scorers.py` (verified line refs)
```python
get_scorers_for_language("python") -> [success_scorer, pytest_pass_rate_scorer, pytest_collect_scorer]   # :22

success_scorer(output)        -> {"success": output.get("status") == "success"}                          # :44
pytest_pass_rate_scorer(out)  -> reads f"{out['root_path']}/output/{out['full_name']}/run_pytest_results.json"          # :88
                                 pass_rate = passed / (total_tests - skipped)                              # :111
                                 + pytest_total_tests/passed/failed/errors/executed/error_breakdown
                                 + pass_rate_exclude_code_issues = passed/(passed+ModuleNotFound+Import)    # :132
pytest_collect_scorer(out)    -> reads f"{out['root_path']}/output/{out['full_name']}/run_pytest_collect_results.json"  # :186
                                 -> {"pytest_collect_success": bool}
```
**Key:** scorers take ONLY the `predict()` return dict; they re-derive paths from `root_path` +
`full_name` and read JSON off disk. So `predict()` must (a) return correct `root_path`/`full_name`,
and (b) have written those two JSON files to `{root_path}/output/{full_name}/`.

### 2.4 In-container test tools — `libkit/tools/run_pytest.py`, `run_pytest_collect.py` (verified)
- Both compute `repo_path = os.getcwd()` and write `logs/run_pytest_results.json` /
  `logs/run_pytest_collect_results.json` **relative to CWD** (run_pytest.py:634/639,
  run_pytest_collect.py:186/188). → The repo location is whatever `docker run -w <dir>` sets.
- `run_pytest.py`: `python -m pytest -v --tb=short --continue-on-collection-errors --junit-xml=logs/junit_report.xml`;
  parses JUnit XML (regex fallback). `run_pytest_results.json` =
  `{summary:{total_tests,passed,failed,skipped,errors,xfailed,xpassed}, error_breakdown:{}, failed_tests:[], error_tests:[], returncode, parse_method}`.
- `run_pytest_collect.py`: `python -m pytest --co -q <repo>`; `success = returncode in [0,5]`
  (5 = "no tests collected" still counts as a clean collect). Output `{success, returncode, errors, raw_output}`.
- Tools assume `pytest` present (they try `pip install pytest` if missing — don't rely on that offline).

### 2.5 The Python dataset — `datasets/python/`
| File | Count | Notes |
|---|---|---|
| `python_repos_all.json` | **500** | **authoritative** split. small 258 / medium 190 / large 52 (by `code_bytes`). |
| `python_repos.json` | 200 | high-star curated set (avg ~36k stars). NOT a subset of `_all`. |
| `repo2run.json` | 420 | the retired Repo2Run set re-expressed in RAT format (3 fields only). |

Per-instance schema (`_all`): `full_name, clone_url, default_branch, language("Python"),
stargazers_count, forks_count, description, html_url, topics[], total_bytes, code_bytes,
language_breakdown{}, size("small"|"medium"|"large")`.
**No `sha` / `base_commit`** → the benchmark clones the **default-branch HEAD** (repo2run_model does
`git clone --depth=1` then `git rev-parse HEAD`). The harness keys on `full_name` only.

### 2.6 Reference image shape — `libkit/dockerfile_templates/python.template`
```dockerfile
FROM {BASE_IMAGE_WITH_VERSION}
RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*
RUN rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED 2>/dev/null || true
RUN mkdir -p ~/.pip && printf '[global]\nindex-url=...aliyun...\n' > ~/.pip/pip.conf
RUN pip install --no-cache-dir pytest openai
RUN git config --global --add safe.directory /repo
WORKDIR /repo
```
i.e. baselines put the repo at `/repo` + ensure `pytest` exists. We don't have to match `/repo`
(see §4.1), but we DO have to ensure `pytest` is installed and the repo is in the image.

---

## 3. How our DockerAgent works (the source we wrap)

From `agent.py` + `src/*` + `multi_docker_eval_adapter.py` (verified signatures):

```python
class DockerAgent:
    def __init__(self, repo_url, base_image="auto", model=DEFAULT_LLM_MODEL, workplace="workplace",
                 base_commit=None, problem_statement="", test_patch="",
                 benchmark_evaluation_target=None, language="",
                 enable_observation_compression=False, enable_long_term_memory=False,
                 memory_path=None, memory_embedding_model=DEFAULT_MEMORY_EMBEDDING_MODEL,
                 command_timeout_seconds=1800): ...
    def run(self, max_steps=30, keep_container=False) -> None: ...

# After run():
agent.workplace                              # abs path; cloned repo + artifacts on host
agent.build_recipe                           # {build_commands, runtime_preparation_commands, test_commands, ...}
agent.verified_test_commands : List[str]
agent.verification_bundle, agent.verification_source
# files: {workplace}/Dockerfile  (only if configuration_success)   ;  {workplace}/agent_run_summary.json (always)
```
- The agent's **interactive sandbox** WORKDIR is `/app` (hardcoded in `src/sandbox.py`). **But this is
  not the image we test.** `MultiDockerEvalAdapter` synthesizes a *separate* **eval Dockerfile** that
  relocates everything to **`/testbed`** (it even normalizes `/app`→`/testbed`). The eval image is what
  we build and score — so the repo runs at **`/testbed`**, not `/app`.
- Needs `MINIMAX_API_KEY` **or** `OPENAI_API_KEY` (+ optional `*_API_BASE`). Headless, no stdin prompts.
- `MultiDockerEvalAdapter.process_single_instance(instance, ...) -> dict` (verified
  `multi_docker_eval_adapter.py:668`): runs the agent, then **returns** a `docker_res` dict whose
  `dockerfile` key is a **self-contained eval Dockerfile string** — `WORKDIR /testbed`, installs git,
  bakes the agent's verified setup commands, then `RUN git clone {repo_url} /testbed` (`:514,526`).
  It does **not** write a `Dockerfile` to disk and does **not** guarantee `pytest` is installed.
  Constructor: `MultiDockerEvalAdapter(output_dir=".../multi_docker_eval_output")`.

---

## 4. Integration design

```
RAT Evaluation (Path A)         OR     Local runner (Path B, offline)
        |                                        |
        v                                        v
   DockerAgentModel(BaseEvalModel).predict(full_name)   <-- SHARED, written once
        |
        |  init_output_and_repo(root_path, full_name)         # creates output/{full_name}/
        |  res = MultiDockerEvalAdapter(output_dir=out_dir).process_single_instance({repo_url, language:python},
        |          enable_artifact_preflight=False)   # agent clones+configures internally; returns docker_res dict
        |  dockerfile_str = res["dockerfile"]          # self-contained: git clone -> /testbed, deps baked
        |  ensure 'pip install pytest' in dockerfile_str ; write it + setup_scripts to a CLEAN ctx dir
        |  docker build -t dockeragent-eval-<slug>  <ctx>       # ctx = out_dir/eval_build (NOT the huge workplace)
        |  docker run -d -w /testbed -v run_pytest.py -v run_pytest_collect.py  <img> tail -f /dev/null
        |  docker exec ... python3 /run_pytest_collect.py ; docker cp .../testbed/logs/run_pytest_collect_results.json -> out_dir/
        |  docker exec ... python3 /run_pytest.py         ; docker cp .../testbed/logs/run_pytest_results.json         -> out_dir/
        |  return {"status":"success","root_path":root_path,"full_name":full_name}
        v
   scorers: success_scorer + pytest_collect_scorer + pytest_pass_rate_scorer  (read those 2 JSONs)
```

### 4.1 Mismatches & resolutions
| # | Mismatch | Resolution |
|---|---|---|
| 1 | Repo path inside images: RAT baselines use `/repo`; our **eval** image uses `/testbed`. | **Resolved — use `/testbed`.** Tools use `os.getcwd()`, so run `docker run -w /testbed …`; they write `/testbed/logs/*.json`; `docker cp` those out. (The agent's *sandbox* /app is irrelevant — we never test that image.) **Hardcoding `/app` would break scoring** — pytest would run in an empty dir. |
| 2 | Official runner loads dataset from an **anonymized HF name** + needs **Weave/W&B**. | Path B avoids both (reads local JSON, calls scorer fns directly). For Path A: either patch `eval_runner` to load the local `python_repos_all.json`, or publish our own HF mirror, or obtain the real dataset name. Note: `eval/README.md`'s `--repos-json` flag is **stale** — current `eval_*.py` load from HF. |
| 3 | `_all` repos have **no SHA/base_commit & no test_patch**. | Good — simpler. Pass `base_commit=None, test_patch="", problem_statement=""`. RAT scores **whole-repo `pytest`**, not specific targets, so our agent's job is just "make the repo's own suite collectable/runnable." We can **drop** the test-patch/eval-script machinery from `MultiDockerEvalAdapter`. |
| 4 | Our success notion = verified test commands (EBSR); RAT = whole-repo pytest pass-rate. | We bake the **environment** (deps) into the image; RAT runs pytest itself. Don't bake our `verified_test_commands` as the gate — only bake build + runtime-prep commands so `pytest` can import/collect. |
| 5 | Eval Dockerfile must be **self-contained** (clone repo + install deps). | **Confirmed self-contained** — `_build_eval_dockerfile` emits `WORKDIR /testbed` + `RUN git clone {repo_url} /testbed` + baked recipe (`:514,526`). No COPY of repo needed. (`base_commit=None` ⇒ default-branch HEAD, matching RAT.) |
| 6 | Must ensure `pytest` is installed in the image. | The eval Dockerfile does **not** add pytest. In `predict()`, if `"pytest" not in dockerfile_str`, append `RUN pip install --no-cache-dir pytest` before building. |
| 7 | `process_single_instance` returns a dict, doesn't write a Dockerfile; build context bloat. | `predict()` extracts `res["dockerfile"]` (+ `res["setup_scripts"]`) and writes them to a **clean `out_dir/eval_build/` context** — never `docker build out_dir` directly (out_dir contains the agent's multi-GB `workplace/`, which would be sent as build context). |
| 8 | Redundant clone. | The eval Dockerfile clones the repo itself at build time, and the agent clones internally — so `predict()` does **not** need its own `git clone` to `input/repo`. (Optionally `git ls-remote` to log the resolved HEAD SHA for reproducibility.) |

### 4.2 `predict()` skeleton (shared by both paths)
```python
# eval/models/dockeragent_model.py   (lives in the RAT repo tree)
import os, sys, time, subprocess, weave

# Two repo roots — DISTINCT (this was the original draft's bug):
RAT_ROOT   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # RAT: libkit/, eval/
AGENT_ROOT = os.environ["DOCKERAGENT_ROOT"]            # OUR repo, e.g. /Users/john/rat-bench-integration
sys.path[:0] = [RAT_ROOT, AGENT_ROOT]

from libkit.command import init_output_and_repo                 # RAT repo
from eval.common.base_model import BaseEvalModel                # RAT repo
from eval.common.utils import TimeoutException                  # RAT repo
from multi_docker_eval_adapter import MultiDockerEvalAdapter    # OUR repo

RP  = f"{RAT_ROOT}/libkit/tools/run_pytest.py"
RPC = f"{RAT_ROOT}/libkit/tools/run_pytest_collect.py"

class DockerAgentModel(BaseEvalModel):
    llm: str
    num_turn: int = 30
    base_image: str = "auto"

    @weave.op
    def predict(self, full_name: str) -> dict:
        start = time.time()
        slug = full_name.lower().replace("/", "-")
        image, container = f"dockeragent-eval-{slug}", f"dockeragent-{slug}"
        out_dir = f"{self.root_path}/output/{full_name}"
        ctx     = f"{out_dir}/eval_build"                  # CLEAN build context (avoid the agent's huge workplace/)
        ok = {"root_path": self.root_path, "full_name": full_name}
        try:
            try:
                init_output_and_repo(self.root_path, full_name, renew=True)
                os.makedirs(ctx, exist_ok=True)

                # 1) Run OUR agent -> docker_res dict. The eval Dockerfile (a STRING) is self-contained:
                #    it `git clone`s the repo into /testbed and bakes the verified setup recipe.
                res = MultiDockerEvalAdapter(output_dir=out_dir).process_single_instance(
                    {"instance_id": full_name.replace("/", "__"),
                     "repo_url": f"https://github.com/{full_name}", "language": "python"},
                    base_image=self.base_image, model=self.llm, max_steps=self.num_turn,
                    enable_artifact_preflight=False)           # RAT scores it; skip our Multi-Docker-Eval preflight
                res = res.get(full_name.replace("/", "__"), res)        # tolerate {id: result} or result
                dockerfile = res.get("dockerfile")
                if not dockerfile:
                    raise Exception(f"agent produced no Dockerfile: {res.get('logs', {}).get('error')}")
                self._check_timeout(start, "agent")

                # 2) Ensure pytest, write a clean build context, build.
                if "pytest" not in dockerfile:
                    dockerfile = dockerfile.rstrip() + "\nRUN pip install --no-cache-dir pytest\n"
                with open(f"{ctx}/Dockerfile", "w") as f: f.write(dockerfile)
                for name, content in (res.get("setup_scripts") or {}).items():   # any files the Dockerfile COPYs
                    with open(f"{ctx}/{name}", "w") as f: f.write(content)
                subprocess.run(["docker", "build", "-t", image, ctx], check=True)

                # 3) Mount RAT's tools, run them AT /testbed (CWD == repo), copy result JSONs to out_dir.
                W = "/testbed"
                subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True)
                subprocess.run(["docker","run","-d","--name",container,"-w",W,
                                "-v",f"{RP}:/run_pytest.py","-v",f"{RPC}:/run_pytest_collect.py",
                                image,"tail","-f","/dev/null"], check=True)
                subprocess.run(["docker","exec",container,"mkdir","-p",f"{W}/logs"], check=True)
                subprocess.run(["docker","exec",container,"python3","/run_pytest_collect.py"], check=False)
                subprocess.run(["docker","cp",f"{container}:{W}/logs/run_pytest_collect_results.json",
                                f"{out_dir}/run_pytest_collect_results.json"], check=False)   # check=False: missing
                subprocess.run(["docker","exec",container,"python3","/run_pytest.py"], check=False)
                subprocess.run(["docker","cp",f"{container}:{W}/logs/run_pytest_results.json",
                                f"{out_dir}/run_pytest_results.json"], check=False)            # JSON => scorer default
                return {"status": "success", **ok}
            except (TimeoutException, subprocess.TimeoutExpired):
                return {"status": "timeout", **ok}
            except Exception as e:
                return {"status": "error", "error": str(e), **ok}
            finally:
                subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True)
                subprocess.run(f"docker rmi {image} >/dev/null 2>&1", shell=True)
        except KeyboardInterrupt:
            subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True); raise
```
> **Semantics:** `status="success"` means "we built & exercised an image", **not** "tests passed".
> Agent/build failure ⇒ `status="error"` ⇒ `success_scorer`=False; missing JSONs ⇒ `pytest_collect`=False,
> `pytest_pass_rate`=0.0 (scorers default gracefully). Same behavior as the baselines.
> **`docker cp` uses `check=False` on purpose** — if pytest crashes before writing JSON, we want a
> default-0 score, not a `predict()` crash. (repo2run_model used `check=True`; that's stricter/brittler.)

### 4.3 Path A entry script
```python
# eval/dockeragent/eval_dockeragent.py
from eval.common.eval_runner import run_evaluation
from eval.models.dockeragent_model import DockerAgentModel
def main():
    args = parse_args()   # --root-path --llm --num-turn --timeout --language --limit --offset --weave-project
    return run_evaluation(args, DockerAgentModel,
        {"root_path": args.root_path, "timeout": args.timeout, "llm": args.llm, "num_turn": args.num_turn},
        weave_project="dockeragent-evaluation", language=args.language)
```
Plus `scripts/eval_dockeragent.sh` (mirror `scripts/eval_repo2run.sh`).

### 4.4 Path B local runner (offline, recommended first)
```python
# run_rat_benchmark.py  (lives in OUR repo)
import json, os, sys
sys.path[:0] = [os.environ["RAT_ROOT"]]               # RAT repo: scorers + the model file
from eval.common.scorers import success_scorer, pytest_pass_rate_scorer, pytest_collect_scorer
from eval.models.dockeragent_model import DockerAgentModel   # reuse the SAME predict()

def load_repos(path):                                 # handle bare list OR our subset {"repos":[...]}
    d = json.load(open(path)); return d["repos"] if isinstance(d, dict) else d

def main(repos_json, root_path, limit=None, timeout=7200, llm="deepseek-chat", num_turn=30):
    repos = load_repos(repos_json)[:limit]
    model = DockerAgentModel(root_path=root_path, timeout=timeout, llm=llm, num_turn=num_turn)
    rows = []
    for r in repos:
        done = f"{root_path}/output/{r['full_name']}/run_pytest_results.json"
        if os.path.exists(done):                      # resume: skip finished repos
            out = {"status": "success", "root_path": root_path, "full_name": r["full_name"]}
        else:
            out = model.predict(r["full_name"])       # writes output/{full_name}/*.json
        rows.append({**out, "_category": r.get("_category", "?"),
                     **success_scorer(out), **pytest_collect_scorer(out), **pytest_pass_rate_scorer(out)})
    n = len(rows); mean = lambda k: round(sum(x[k] for x in rows)/n, 4)
    print(f"n={n}  build_success={mean('success')}  collect_success={mean('pytest_collect_success')}")
    print(f"mean_pass_rate={mean('pytest_pass_rate')}  mean_pass_rate_excl_code={mean('pass_rate_exclude_code_issues')}")
    json.dump(rows, open(f"{root_path}/rat_results.json","w"), indent=2)
```
Identical scorer math to Path A; **no W&B login / no HF** (but `weave` must be `pip install`ed — the
model file imports it and decorates `predict` with `@weave.op`; it's never `weave.init()`-ed offline).
Report **all four** metrics, not just pass-rate. **For the `repo2run_weak_test_deficient`/`_ci_service`
(S2/S3) repos in `datasets/rat_python_hard_subset.json`, `pytest_pass_rate` is meaningless** (no real
tests ⇒ 0.0) — judge those on `build_success` + `pytest_collect_success`. Break the report down by
`_category` to keep that honest.

---

## 5. Metric mapping & fidelity to the paper (RAT, arXiv 2604.23190)

> **VERIFIED against the paper + the released code (see §5.1).** The paper's headline metric is
> **ESSR (Environment Setup Success Rate) = N_pass / N_verified** — a *unit-test pass rate* — reported
> under **three scenarios S1/S2/S3**. The released open-source scorers implement only a **subset** of
> this. Reusing them gives numbers comparable to the *released baselines*, but **does NOT reproduce
> the paper's reported ESSR (Tables 2/3)** without extra work.

### 5.1 Paper method vs released code — discrepancy table
| Paper (arXiv 2604.23190) | Released code (`eval/common/scorers.py` etc.) | Verdict |
|---|---|---|
| **ESSR = N_pass / N_verified**, reported per scenario **S1/S2/S3** | One aggregate `pytest_pass_rate = passed/(total−skipped)` over all repos; **no scenario partitioning anywhere** (grep finds none; `eval/report/*` doesn't stratify; dataset has no `has_tests`/`has_dockerfile`/scenario flag) | **Not in open source** — Tables 2/3 not reproducible from released code |
| **S1 (artifact-guided)**: N_verified = all existing tests | `pytest_pass_rate = passed/(total−skipped)` (≈ naive ESSR over *all* repos, unpartitioned) | Partial — closest proxy, not scenario-scoped |
| **S2 (artifact-free)**: N_verified excludes tests failing on code defects | `pass_rate_exclude_code_issues = passed/(passed+ModuleNotFound+Import)` — excludes **only** import/module errors; lumps ConnectionError/RuntimeError/Assertion together; denominator form differs | **Partial match** (`scorers.py:124-143`) |
| **S3 (test-deficient)**: synthesize smoke tests; score their execution (paper: **92.0**) | `construct-test`→`run_test.py` writes `run_test_results.json`, which **no scorer reads**; auto-create of a smoke `test_basic.py` is **commented out** (`create_test.py:~1100`); `pytest_pass_rate` sees only real `test_*.py` → reports **0.0** for test-deficient repos | **Not reproducible** from released scorers |
| **Non-Python = build success** (mvn/gradle, cargo, npm) | `java_build_scorer`, `cargo_build_scorer`, `npm_install_scorer` (build returncode) | **Match** |
| temp 0.0; **30 turns**; cmd timeout **600s**, **global 7200s**; **150K token** budget | temp 0.0 ✓; 30 turns ✓ (eval override); but `eval_rat.py --timeout` default **2400s** (≠7200); token budget is **character-count proxy** (`len(str(msg))`, not real tokens) | **Mixed** — set `--timeout` ≥7200; token cap is approximate |
| Efficiency: **Latency & Tokens per repo** | `stage_timing_scorer`/`tool_usage_scorer` exist but are **dead code** (not in `get_scorers_for_language`); `predict()` doesn't emit timings/tokens | **Not captured** — must instrument |
| Dataset: 500 Python, tiers by **code_bytes** (258/190/52), stars min 10, size×popularity stratified | 500 ✓, code_bytes ✓, 258/190/52 ✓, stars min **11** (off-by-one), stratified ✓; eval loads HF `python_all`/`python_eval` (a `_eval` split exists on HF, **not** in local archive) | **Match** (modulo min-stars & `_eval` split) |
| Quirk (not in paper) | `pass_rate = 1.0` when *all* tests are `TimeoutError` (`scorers.py:112-120`) | **Extra** — can inflate; consider overriding |

### 5.2 What you can honestly report
| If your goal is… | Then… |
|---|---|
| **Compare our DockerAgent head-to-head with RAT's released baselines** (RAT, Repo2Run, SWE-agent…) | Reusing the released scorers is **correct & fair** — every method graded identically. Report `pytest_pass_rate` + `pytest_collect_success` + `success`. This is the recommended, defensible path. |
| **Reproduce / claim the paper's ESSR numbers (Tables 2/3)** | The released code is **insufficient.** You must additionally: (a) classify each repo into S1/S2/S3 (by Dockerfile/CI + test presence), (b) implement scenario-aware `N_verified`, (c) score S3 synthesized tests (read `run_test_results.json` / re-enable smoke-test scoring), (d) obtain the exact HF `_eval` split, and ideally (e) confirm with the authors how the per-scenario `N_verified` was computed. Treat the paper's numbers as **not reproducible from the release alone.** |

**Headline metric for "did our agent configure the env correctly":** the paper's answer is **ESSR (pass rate)**, with `pass_rate_exclude_code_issues` as the env-isolating variant. (`pytest_collect_success` is an intermediate gate, **not** the paper's reported metric — corrected from an earlier draft of this plan.)

---

## 6. Phased task list

**Task 0 — One-repo build smoke test (30 min).** The architecture questions are already answered
(self-contained eval Dockerfile, repo at `/testbed`). What remains is a *runtime* check: run
`MultiDockerEvalAdapter.process_single_instance(...)` on ONE small repo, write `res["dockerfile"]` to a
ctx dir, `docker build`, then `docker run -w /testbed` + exec the two RAT tools, and confirm
`run_pytest_results.json` + `run_pytest_collect_results.json` appear. Picks: an `easy_control` repo from
`datasets/rat_python_hard_subset.json` (e.g. `resend/resend-python`). Watch for: pytest missing (Task-2
fix handles it) and `res["dockerfile"]` empty (agent failed → that's a legit `error`).

**Task 1 — Stage RAT into our repo.** `git clone` (or vendor) the RAT repo as `others_work/RunAnyThing/`
and set `RAT_ROOT`/`DOCKERAGENT_ROOT` env vars. We need `eval/common/{scorers,utils,base_model}.py`,
`eval/common/eval_runner.py`, `libkit/tools/run_pytest.py` + `run_pytest_collect.py`,
`libkit/command.py::init_output_and_repo`, and (for Path A) `datasets/python/python_repos_all.json`.
The full local copy already extracted at `/tmp/runanything/src` can seed this.

**Task 2 — Write `DockerAgentModel.predict()`** exactly as §4.2 (corrected). Key points: extract
`res["dockerfile"]` (don't expect a file), build from a clean `eval_build/` ctx, force `pytest`,
`docker run -w /testbed`, `docker cp` from `/testbed/logs/`. Pass no `test_patch`/`patch`.

**Task 3 — Write Path B runner** `run_rat_benchmark.py` (§4.4). Smoke-test on the 16 `smoke`-tier repos
in `datasets/rat_python_hard_subset.json` (or `--limit 3`). Confirm the two JSONs appear per repo and
all four metrics print sane values; spot-check one S3 repo reads `pass_rate=0` but `collect/build` true.

**Task 4 — Scale Path B.** Run the rest of the 50-repo subset, then (if desired) the `small` split.
Add concurrency (N containers in parallel — unique container/image names already keyed by slug),
per-repo timeout (**7200s to match the paper**, not 2400), aggressive `docker rmi`/`docker image prune`
(README warns about image bloat), and the resume-on-restart already in the runner.

**Task 5 (optional) — Path A official harness.** Add `eval/models/dockeragent_model.py` +
`eval/dockeragent/eval_dockeragent.py` + `scripts/eval_dockeragent.sh`. Resolve the dataset source
(patch `eval_runner` to read local JSON, or get the HF name) and stand up Weave (W&B login or local
server). Run head-to-head with RAT's own `eval_rat.sh` / `eval_repo2run.sh` for a comparison table.

**Task 6 — Report.** Emit a per-repo CSV/JSON + an aggregate table
(success / collect_success / pass_rate / pass_rate_exclude_code_issues), broken down by size split,
alongside RAT's published baseline numbers.

---

## 7. Risks / open items
- **Weave/W&B requirement (Path A only).** `weave.init()` is unconditional. Needs W&B account or a
  local Weave server. Path B sidesteps this entirely — start there.
- **Anonymized HF dataset name** in `eval_runner.load_dataset(...)`. The local
  `datasets/python/python_repos_all.json` is the same data; prefer loading it directly.
- **No pinned SHA** → cloning HEAD is non-reproducible over time. Record the resolved
  `git rev-parse HEAD` per repo (repo2run_model does) for our own reproducibility.
- **Cost/throughput.** 500 repos × LLM agent run × docker build/test is expensive. Gate by `size`
  split, cap `num_turn`, run small-first, parallelize, and clean images aggressively.
- **`pytest` not installed by our agent's recipe** → collect fails spuriously. Always append
  `RUN pip install pytest` to the eval Dockerfile.
- **Network installs inside `docker build`** can flake (PyPI). Consider a pip cache/mirror (RAT's
  template uses an Aliyun mirror) and a retry. Note the eval Dockerfile also `git clone`s at build time.
- **`weave` is a hard import dependency even for Path B** — the model file does `import weave` + `@weave.op`.
  Path B avoids `weave.init()`/W&B login, but `pip install weave` is still required.
- **Build-context bloat:** never `docker build out_dir` — `out_dir` holds the agent's `workplace/`
  (cloned repo + snapshots, multi-GB). Always build the clean `out_dir/eval_build/` ctx (§4.2).
- **HEAD drift:** the eval Dockerfile clones HEAD at *build* time; the agent cloned HEAD at *run* time —
  tiny window where they differ. For strict reproducibility, resolve+pin a SHA per repo.
- **Status verified against source** (`multi_docker_eval_adapter.py`, `scorers.py`, `run_pytest*.py`,
  `eval_runner.py`): the §4.2 skeleton matches the real contracts. Remaining unknowns are *runtime*
  (does a given repo build/test cleanly), which Task 0/3 exercise — not architectural.

---

## 8. Quick-start (after Tasks 1-3)
```bash
# offline smoke test (Path B), curated subset, smoke tier
export OPENAI_API_KEY=...                       # or MINIMAX_API_KEY  (LLM for our agent)
export RAT_ROOT=/path/to/RunAnyThing            # RAT repo (scorers, tools, the model file)
export DOCKERAGENT_ROOT=/Users/john/rat-bench-integration   # our agent repo
pip install weave                               # imported by the model file (no W&B login needed)

python run_rat_benchmark.py \
  --repos-json datasets/rat_python_hard_subset.json \
  --root-path ./rat_run --limit 16 --llm deepseek-chat --num-turn 30 --timeout 7200
# -> ./rat_run/output/<owner>/<repo>/{eval_build/Dockerfile, run_pytest_results.json, run_pytest_collect_results.json}
# -> ./rat_run/rat_results.json + printed: build_success / collect_success / mean_pass_rate / mean_pass_rate_excl_code
```
> Reminder: for the S2/S3 (`repo2run_weak_*`) subset repos, read `build_success` + `collect_success`,
> not `pass_rate` (no real tests → 0.0 by design).
