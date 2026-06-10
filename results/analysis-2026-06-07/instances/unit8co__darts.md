# unit8co/darts

- DA pass-rate: 0.0% (0/1 tests; pytest collection failed) | RAT pass-rate: 99.82% (9627/9707 tests passed) | bucket: DA_LOSS
- DA build_success/test_success: true/false | error_breakdown: ModuleNotFoundError (narwhals missing at test time)

## Failure stage & category

test_collection_error / missing_runtime_or_test_deps

## Root cause (why DA lost)

DockerAgent's agent verification phase ran in a container with working directory `/app`, while the synthesized Dockerfile used working directory `/testbed`. The verified test command references `/app/.venv/bin/pytest`, but the actual Docker build runs `cd /testbed && uv sync --group dev-all`, creating the venv at `/testbed/.venv`. At eval time, pytest runs via `python -m pytest` (system Python, not the venv) due to incomplete PATH setup, causing `ModuleNotFoundError: No module named 'narwhals'` during test collection (line 12085 of run.log). Although narwhals was installed in the uv sync (visible at line 11860), it was installed in `/testbed/.venv/bin`, not on the system Python path.

## What RAT did differently

RAT's approach:
- `pip install -q -e "/repo[dev,optional]"` — installed darts package with dev and optional dependency groups into system/activated pip environment
- `pip install -q "darts[torch]"` — explicitly installed torch variant
- `pip install -q pytest-cov pytest-timeout testfixtures ty` — installed additional test dependencies
- Used environment variable hooks (likely `run-pytest-collect` and `run-pytest` bash wrappers) that ensure correct Python interpreter is used

RAT did NOT use uv or virtual environment isolation; instead, pip-installed directly to the accessible Python path. This ensured narwhals and all test dependencies were visible when pytest ran.

## Evidence

- File: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/unit8co/darts/run.log`, line 12042: `🔧 Command: python -m pytest --co -q /testbed` (system python, not venv)
- File: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/unit8co/darts/run.log`, lines 12081-12085: Error traceback shows `darts/timeseries.py:58: in <module> import narwhals as nw` → `ModuleNotFoundError: No module named 'narwhals'`
- File: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/unit8co/darts/run.log`, line 11860: `narwhals==2.21.2` was installed during uv sync, confirming it exists in venv
- File: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/unit8co/darts/unit8co__darts.json`, line ~50: verified_test_command references `/app/.venv/bin/pytest` but Dockerfile uses `/testbed` WORKDIR
- RAT outer_commands.json shows direct pip installs: `pip install -q -e "/repo[dev,optional]"`, `pip install -q "darts[torch]"`, `pip install -q pytest-cov pytest-timeout testfixtures ty`

## Fix recommendation (for agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In src/synthesizer.py**: When generating the Dockerfile, ensure WORKDIR matches the directory used during agent verification. If the agent verified in `/app`, the Dockerfile should use `WORKDIR /app`. Alternatively, if using `/testbed`, verify the agent's commands in the same working directory.

2. **In src/recipe_repair.py or artifact_verify.py**: When building the self-verify Docker image, use the same WORKDIR as the final Dockerfile to catch path mismatches before finalization.

3. **In agent.py or the eval script generation**: Ensure runtime_preparation_commands (e.g., `export PATH`) are actually applied to the test execution environment. The current eval_script sets PATH but then runs `python -m pytest` instead of using the explicit venv path. Either:
   - Use `/testbed/.venv/bin/pytest` directly (no PATH export needed), or
   - Verify that `export PATH="/testbed/.venv/bin:$PATH"` is actually active before pytest runs

4. **Consider uv vs pip strategy**: RAT's direct pip installs with extras (`pip install -e ".[dev,optional]"`) are simpler and avoid venv isolation issues during test execution. If DA continues using uv, ensure the venv location is consistent and explicitly used in test commands (not relying on PATH exports).
