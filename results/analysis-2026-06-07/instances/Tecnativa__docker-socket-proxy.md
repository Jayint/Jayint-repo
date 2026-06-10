# Tecnativa/docker-socket-proxy

- DA pass-rate: 0.0 (0/0 tests) | RAT pass-rate: 0.0 (0/5 tests, failed with KeyError) | bucket: BOTH_FAIL
- DA: build_success=True, test_success=False, pytest_collect_success=False (error: ModuleNotFoundError: No module named 'plumbum')
- RAT: build_success=True, test_success=False, pytest_collect_success=True, pytest_executed=True (5 tests failed with KeyError: '2375/tcp')

## Failure stage & category

test_collection_error / missing_runtime_or_test_deps

## Root cause (why DA lost)

DA's agent successfully ran collection during planning (reported verified test command: `cd /app && poetry run pytest --collect-only -q --disable-warnings` with 5 tests collected), but the synthesized Dockerfile's `poetry install` failed to include plumbum in the final evaluation environment. The agent's sandbox environment had plumbum available from its iterative setup, but that state was not faithfully reproduced in the Dockerfile. When the eval container ran the collection command, it failed with `ModuleNotFoundError: No module named 'plumbum'` because plumbum (a dev-dependency in pyproject.toml) was not installed in the clean-room image.

## What RAT did differently

- RAT explicitly ran: `pip install 'pytest>=7.0.0' pytest-xdist plumbum --upgrade`
- RAT then verified plumbum import: `python -c "from plumbum.cmd import docker; print('ok')"`
- RAT further diagnosed with: `python -c "import plumbum; print(plumbum.__version__); from plumbum import cmd; ..."`
- RAT used: `pip install 'plumbum<2.0'` after initial failures to pin version compatibility
- RAT then re-ran: `python -c "from plumbum.cmd import docker; print('ok'); print(docker)"`

RAT's approach: after poetry install, it explicitly verified test imports and installed missing dependencies (plumbum, pytest-xdist) via pip with version constraints, ensuring the import chain worked before running pytest collection.

## Evidence

**DA's logs:**
- `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/Tecnativa/docker-socket-proxy/run_pytest_collect_results.json`:
  ```
  "success": false,
  "returncode": 4,
  "errors": ["E   ModuleNotFoundError: No module named 'plumbum'"],
  "raw_output": "...tests/conftest.py:8: in <module>\n    from plumbum import local\nE   ModuleNotFoundError: No module named 'plumbum'"
  ```

- `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/Tecnativa/docker-socket-proxy/_result_row.json`:
  ```json
  "pytest_collect_success": false,
  "pytest_total_tests": 0,
  "pytest_passed": 0,
  "pytest_failed": 0
  ```

**DA's planning transcript (run.log):**
- Line ~607: Agent correctly identified that docker is required for tests
- Line ~1793-1833: Agent executed `cd /app && poetry install` in sandbox, which succeeded
- Line ~1913-1924: Self-Verify Round 0 reported "tests did not execute (internal_repo_import_error)" but then Round 1 reported "tests executed (tests_passed)" after repair
- Line ~1916: Verification Bundle accepted 1 test command: `cd /app && poetry run pytest --collect-only -q --disable-warnings`
- Despite agent's sandbox success, Dockerfile synthesis did NOT add explicit plumbum installation

**RAT's commands (outer_commands.json + inner_commands.json):**
```
pip install 'pytest>=7.0.0' pytest-xdist plumbum --upgrade -i https://mirrors.aliyun.com/pypi/simple
pip install 'plumbum<2.0' -i https://mirrors.aliyun.com/pypi/simple
python -c "from plumbum.cmd import docker; print('ok')"
```

**RAT's result (run_pytest_results.json):**
```json
"summary": {"total_tests": 5, "passed": 0, "failed": 5, "errors": 0},
"error_breakdown": {"KeyError": 5},
"failed_tests": [{"error_message": "KeyError: '2375/tcp'"}]
```

RAT successfully collected and executed all 5 tests; they failed at runtime due to missing Docker proxy port (a runtime environment issue, not a collection issue).

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

**Root issue:** DA's synthesis trusts that `poetry install` will pull all dev dependencies defined in pyproject.toml, but `poetry install` behavior varies by environment (lockfile state, Python version, platform). When the agent's sandbox environment successfully ran `poetry install` and tests collected, the synthesizer did not capture the intermediate verification step where plumbum was confirmed importable.

**Short term (for synthesizer.py):**
1. When `poetry install` is a build command AND test collection uses `pytest`, explicitly add verification steps to check that test imports work:
   - After `poetry install`, insert `python -c "import pytest; from plumbum import cmd; print('ok')"` or similar to catch missing imports.
   - If verification fails, synthesizer should suggest explicit `pip install <missing_module>` commands for dev dependencies.

2. In synthesizer's `build_commands` extraction: if a test command requires `plumbum` (detected from test file imports or conftest imports), ensure plumbum is explicitly listed in build_commands, either via poetry or pip.

**Medium term (for artifact_verify.py / recipe_repair.py):**
1. When artifact self-verify detects `ModuleNotFoundError` in test collection, trigger repair that explicitly installs the missing module:
   - Parse the import error message (e.g., `No module named 'plumbum'`)
   - Insert `pip install plumbum` (or `pip install plumbum<2.0` if version constraints apply) before the test command
   - Re-run collection to confirm

2. In recipe_repair, preserve version constraints from agent's diagnostic commands (e.g., if RAT used `plumbum<2.0`, capture that reason and replicate).

**Deep fix (agent.py / planning loop):**
- When the agent reaches test-command verification, it should explicitly verify that each imported module is available before declaring "tests collected successfully."
- If any import fails, the agent should install the missing dependency and re-verify, rather than relying on poetry's implicit behavior.
