# R3 — Runner IO Contract

## (1) predict() Output Dict: Exact Key Shape

**Source:** `/tmp/runanything/src/eval/models/dockeragent_model.py` lines 37–117.

The `predict()` return value is assembled from three dicts merged inline:

```
ok   = {"root_path": self.root_path, "full_name": full_name}
meta = {"requested_model": self.llm, "base_image": ..., "head_sha": ""}
```

### Keys present on every return path

| Key | Type | Notes |
|-----|------|-------|
| `status` | str | `"success"` / `"error"` / `"timeout"` |
| `failure_reason` | str\|None | `None` on success; e.g. `"no_dockerfile"`, `"build_failed"` |
| `root_path` | str | Passed-through from model |
| `full_name` | str | e.g. `"resend/resend-python"` |
| `requested_model` | str | The `--llm` value |
| `base_image` | str | `"auto"` or resolved base, from `meta` |
| `head_sha` | str | HEAD commit SHA inside container, or `""` |
| `error` | str | Present on error/timeout paths only |

On success (`status == "success"`) **no additional payload keys** are emitted — the Dockerfile, recipe, successful\_actions, failed\_actions, and build\_recipe are **not** forwarded into the `predict()` return dict. The model writes the Dockerfile to `eval_build/Dockerfile` and copies pytest result JSON files to `out_dir`, then returns the minimal `{"status":"success", ...ok, ...meta}` dict (line 104).

**Critical finding re: agent fields:**

- `successful_actions` exists on the live `DockerAgent` object (set at `agent.py` line 150, appended at line 1645, compacted at lines 1934–1935 in `_build_run_summary`).
- `build_recipe` exists on the agent object (line 154).
- Both are written by `MultiDockerEvalAdapter.process_single_instance()` into the per-instance recipe JSON at `out_dir/{instance_id}.json` (adapter line 913 `result["logs"]["build_recipe"]`; line 3018 `"successful_actions": (run_summary or {}).get("successful_actions")`).
- **Neither reaches `predict()`'s return dict.** `dockeragent_model.py` never reads `res["successful_actions"]`, `res["failed_actions"]`, or `res["build_recipe"]` (lines 47–104). The `res` dict from `MultiDockerEvalAdapter.process_single_instance()` is only consulted for `res.get("base_image")`, `res.get("dockerfile")`, and `res.get("setup_scripts")`.
- `agent_run_summary` is similarly absent from the `predict()` return value; it is written to the workplace dir (`workplace/agent_run_summary.json`, adapter line 2680) but the workplace path is not exposed to the RAT runner.

**Conclusion:** `_repair_and_rescore` cannot obtain `successful_actions`, `failed_actions`, or `build_recipe` from the `predict()` return dict. It must read them from the on-disk recipe JSON (see section 2 below).

---

## (2) On-Disk Layout Under per-repo output dir

**Confirmed from:** `ls /Users/john/rat-bench-integration/rat_run/output/resend/resend-python/` and reading the files directly.

```
{root_path}/output/{owner}/{repo}/
  eval_build/
    Dockerfile                   ← self-contained eval Dockerfile (written by dockeragent_model.py line 69)
  {owner}__{repo}.json           ← adapter recipe JSON (written by MultiDockerEvalAdapter._save_result, adapter line 3254)
  run_pytest_results.json        ← copied from container by dockeragent_model.py line 102
  run_pytest_collect_results.json ← copied from container by dockeragent_model.py line 100
```

**Recipe JSON filename pattern:** `instance_id.json` where `instance_id = full_name.replace("/", "__")`.
For `resend/resend-python` → `resend__resend-python.json`.
(Adapter line 48: `"instance_id": full_name.replace("/", "__")"`; adapter `_save_result` line 3254: `output_file = self.output_dir / f"{instance_id}.json"`.)

**Key fields in the recipe JSON relevant to the repair loop:**

- `dockerfile` (str) — the eval Dockerfile text (same as `eval_build/Dockerfile`).
- `logs.build_recipe` (dict\|None) — the agent's `build_recipe` object (adapter line 913).
- `logs.successful_actions` is NOT stored directly; however `logs.verified_test_commands`, `logs.verified_runtime_preparation_commands`, `logs.recipe_test_commands`, and `logs.recipe_runtime_preparation_commands` are stored (adapter lines 864–872). The full `successful_actions` list is only available in the workplace `agent_run_summary.json`, which lives outside `out_dir`.

**Note:** The `run_pytest_results.json` / `run_pytest_collect_results.json` are consumed by the RAT scorers (`success_scorer`, `pytest_pass_rate_scorer`, `pytest_collect_scorer`) at `run_rat_benchmark.py` lines 207–209.

---

## (3) Docker and Network Availability; Dockerfile Self-Containedness

**Docker availability:** The runner calls `subprocess.run(["docker", "build", ...])` at `dockeragent_model.py` line 73 and `subprocess.run(["docker", "run", ...])` at lines 84–87. These succeed in the current dev environment. The scheduler (`run_rat_benchmark.py`) launches child subprocesses that each call `_run_one` → `model.predict()` inline, so docker is available to every child.

**Network (git clone):** `docker build` runs inside the Docker daemon with outbound network access. The eval Dockerfile contains `RUN git clone ...` as its repo acquisition step.

**Dockerfile self-containedness — verbatim quote** (from `eval_build/Dockerfile`, `resend/resend-python`):

```dockerfile
FROM python:3.14.5
WORKDIR /testbed

# Configure apt reliability for eval image builds
RUN printf '%s\n' 'Acquire::Retries "5";' ...

# Install git for cloning
RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git)

# Clone repository and checkout base commit
RUN git clone https://github.com/resend/resend-python /testbed
# No base commit provided; using repository default branch HEAD

# Agent's verified setup instructions
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
...
RUN pip install --no-cache-dir pytest
```

The Dockerfile is **fully self-contained**: it installs git, clones from GitHub into `/testbed`, checks out the commit (or HEAD), and installs all dependencies. No host-side build context is needed for the repo source — `docker build` only passes the `eval_build/` directory as context (which contains only the Dockerfile itself). `_repair_and_rescore` may therefore call `docker build eval_build/` without any additional context preparation.

---

## (4) Exact Injection Point in _run_one

**Injection line:** `run_rat_benchmark.py` line 199, immediately after the `print(f"[done  ]")` line and before the `end_ts = time.time()` assignment.

Context window at injection time:

```python
# line 190     out = model.predict(full_name)          ← out dict available
# line 199     print(f"[done  ] {full_name}  status={out.get('status')}")
#              ↑ INJECT HERE  ↑
# line 201     end_ts = time.time()
```

Inputs available at injection time:

| Variable | Type | Content |
|----------|------|---------|
| `full_name` | str | `"owner/repo"` — the repo identifier |
| `out` | dict | The `predict()` return dict (keys listed in section 1) |
| `root_path` | str | The output root; `out_dir = f"{root_path}/output/{full_name}"` |
| `out_dir` | — | **Not yet bound at line 199** — must compute as `os.path.join(root_path, "output", full_name)` |
| `model` | DockerAgentModel | The model instance (carries `.llm`, `.root_path`) |

On-disk inputs derivable from `root_path` and `full_name`:

- `eval_build/Dockerfile` — `os.path.join(root_path, "output", full_name, "eval_build", "Dockerfile")`
- Recipe JSON — `os.path.join(root_path, "output", full_name, full_name.replace("/","__") + ".json")`
- `run_pytest_results.json` — `os.path.join(root_path, "output", full_name, "run_pytest_results.json")`
- `run_pytest_collect_results.json` — same dir

The repair call should only trigger when `out.get("status") == "success"` and at least one scorer indicates a failure (e.g., `pytest_pass_rate == 0` or `pytest_collect_success == False`). Score the `out` dict first via `success_scorer(out)` / `pytest_collect_scorer(out)` / `pytest_pass_rate_scorer(out)` to decide whether repair is needed, consistent with the Repo2Run runner's `attempt_success` gate (run_repo2run_benchmark.py lines 3434–3440).

---

## (5) How --llm/model and client are Constructed for Reuse

**_make_model factory:** `run_rat_benchmark.py` lines 82–100. For `--model dockeragent` it returns:

```python
DockerAgentModel(root_path=root_path, timeout=timeout, llm=llm, num_turn=num_turn)
```

`DockerAgentModel` is a Weave `BaseEvalModel` with fields `llm: str`, `num_turn: int`, `base_image: str` (`dockeragent_model.py` lines 27–29). It carries **no OpenAI client object** — the model only calls `MultiDockerEvalAdapter` which in turn creates a `DockerAgent`, which internally constructs its own `OpenAI` client at `agent.py` line 222.

**For the repair loop,** the `create_openai_client_from_env()` function in `src/workplace_replay.py` (lines 71–79) constructs the canonical client:

```python
def create_openai_client_from_env() -> OpenAI:
    api_key = (os.getenv("OPENROUTER_API_KEY")
               or os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY"))
    base_url = (os.getenv("OPENROUTER_API_BASE")
                or os.getenv("MINIMAX_API_BASE") or os.getenv("OPENAI_API_BASE"))
    ...
    return OpenAI(api_key=api_key, base_url=base_url if base_url else None)
```

The repair LLM model string comes from `model.llm` (the `DockerAgentModel.llm` attribute, which equals the `--llm` CLI argument). The port module must call `create_openai_client_from_env()` once (lazily, on first repair invocation) and pass both `client` and `model.llm` to `repair_dockerfile_with_llm()`.

`run_repo2run_benchmark.py` uses the identical `create_openai_client_from_env` import (line 31: `from src.workplace_replay import ... create_openai_client_from_env`) and calls it at line 3502: `repair_client = create_openai_client_from_env()`. The RAT runner port should do the same.

---

## 6-Line Summary

1. `predict()` returns only 7 keys (`status`, `failure_reason`, `root_path`, `full_name`, `requested_model`, `base_image`, `head_sha`); `successful_actions`, `build_recipe`, and the recipe file path are NOT in the return dict — they must be loaded from `{out_dir}/{owner}__{repo}.json`.
2. On-disk layout per repo: `eval_build/Dockerfile` (self-contained with `RUN git clone`), `{instance_id}.json` (recipe with `logs.build_recipe` / `logs.verified_test_commands`), `run_pytest_results.json`, `run_pytest_collect_results.json`.
3. The `eval_build/Dockerfile` is fully self-contained (git clone baked in) so `_repair_and_rescore` can call `docker build eval_build/` directly without extra context.
4. Inject the repair call at `run_rat_benchmark.py` line 199, after `predict()` completes and `out` is bound, before `end_ts`; derive `out_dir` as `os.path.join(root_path, "output", full_name)`.
5. The LLM model string is `model.llm` (set from `--llm` CLI); construct the OpenAI client via `create_openai_client_from_env()` from `src.workplace_replay` (same call used by `run_repo2run_benchmark.py` line 3502).
6. Docker and network are available to every `_run_one` invocation (both sequential and per-child-subprocess paths call `docker build`/`run` inline), so the repair loop's docker rebuild requires no additional process setup.
