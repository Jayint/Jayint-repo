# scylladb/scylla-cluster-tests

- DA pass-rate: 0/0 (build_failed) | RAT pass-rate: 0/44 (test_errors, pytest_executed=true) | bucket: BOTH_FAIL
- DA: build_success=false, test_success=false | RAT: success=true, pytest_collected=44, all errors (ModuleNotFoundError)

## Failure stage & category

**DA:** docker_build / missing_runtime_or_test_deps
**RAT:** test_execution / parity_both_passed (parity: dataset-hard, both hit ModuleNotFoundError)

## Root cause (why DA lost)

DA's synthesizer generated a **malformed Dockerfile with incomplete RUN statements** (lines 26-30 in eval_build/Dockerfile have broken syntax: `RUN echo \` followed by disconnected `RUN echo '...'`, `RUN apt-get install -y --no-install-recommends \` with no packages, `RUN uv sync --frozen` disconnected from prior context). The Dockerfile also references **undeclared build args** (`$PYTHON_IMAGE_TAG`, `$KUBECTL_VERSION`, `$EKSCTL_VERSION`, `$HELM_VERSION`), causing Docker build to fail immediately at line 1. Additionally, **DA never generated verified test commands** (test_command_source: "missing_agent_verification_bundle"), so evaluation was skipped (skip_evaluation=true).

In contrast, RAT ran the actual environment setup commands sequentially in the container: `pip install -e . --no-build-isolation`, `uv sync --frozen`, followed by dependency extraction and installation. Despite that, RAT hit 44 ModuleNotFoundError/ImportError at test collection (not a DA-specific issue—the repo's deps have missing or incompatible modules for the Python version in-use).

## What RAT did differently

- **Actual installation sequence (verified commands):**
  - `pip install -e . --no-build-isolation` (command #12 in outer_commands.json, rc=0)
  - `pip install uv` (command #14, rc=0)
  - `uv sync --frozen` (command #15, rc=0)
  - Extracted `pyproject.toml` dependencies and installed with `pip install -r /tmp/requirements.txt` (commands #25, #36, rc=0)
  - Switched Python version and re-installed deps for Python 3.11 (commands #38, #42-43)
  - Final dependency: `pip install enum34` (command #64) before pytest collection

- **Avoided Dockerfile generation:** RAT's harness runs commands directly in the container, never relied on a pre-built Dockerfile.

## Evidence

**DA failure signals:**
- `_result_row.json`: `"status": "error"`, `"failure_reason": "build_failed"`, `pytest_executed: false`
- run.log line 231: `UndefinedArgInFrom: FROM argument 'PYTHON_IMAGE_TAG' is not declared (line 1)`
- run.log line 243: `invalid reference format` (Docker parser rejected undeclared build arg)
- run.log line 423: `500 Server Error ... no space left on device` (disk full during step 8)
- scylladb__scylla-cluster-tests.json line 27: `"test_command_source": "missing_agent_verification_bundle"`, line 32: `"skip_evaluation": true`

**Dockerfile syntax errors (eval_build/Dockerfile):**
```dockerfile
RUN echo \                                                    (line 26: incomplete, missing continuation)
RUN echo 'deb [signed-by=...' | tee ...                      (line 27: orphaned, not part of prior RUN)
RUN apt-get install -y --no-install-recommends \             (line 31: no packages listed)
RUN uv sync --frozen                                         (line 32: disconnected from prior)
```

**RAT's working flow:**
- outer_commands.json commands 11-15: Navigates to /repo, runs `pip install -e .`, `uv sync`
- All execute successfully (rc=0)
- Commands 37+: Pytest collection finds 44 tests, but all fail at runtime with ModuleNotFoundError (44 test_errors)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In `src/synthesizer.py` (Dockerfile generation):**
   - **Validate ARG declarations:** Before using `$VAR` in FROM/RUN, ensure all non-standard build args are declared with `ARG VAR=default` at the top (or remove hardcoded references like `$KUBECTL_VERSION`).
   - **Fix incomplete RUN continuations:** Do not split multi-line RUN statements across separate `RUN` commands. Use proper `\` line continuation *within a single RUN*. Current code generates `RUN echo \` followed by `RUN echo '...'` — these should be merged into one `RUN` block.
   - **Strip incomplete commands:** Remove dangling `RUN apt-get install -y --no-install-recommends \` with no packages following. Validate all `RUN` statements are syntactically complete before serializing to Dockerfile.

2. **In `src/recipe_repair.py` (Verification Bundle extraction):**
   - Ensure the repair loop captures the agent's verified_test_commands and verified_runtime_preparation_commands. If the bundle is empty or rejected, retry the agent with explicit instruction to output a test command (e.g., "pytest -v /repo/unit_tests").

3. **In `agent.py` (Agent interaction):**
   - Add a **preflight check** after agent synthesis: validate Dockerfile syntax locally (e.g., `docker build --dry-run` or simple regex checks for undeclared args) before attempting a real build.
   - Log a warning/error if `verified_test_commands` is empty — this signals a broken artifact and should trigger a repair round or fallback to a default pytest command.

**Note:** The ModuleNotFoundError on 44 tests is NOT a DA-specific failure — RAT hit the same error, indicating the dataset itself requires additional environment setup (possibly missing non-Python system deps, or stale lockfile). This is a *parity* issue, not a win/loss indicator.
