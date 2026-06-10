# sooperset/mcp-atlassian

- **DA pass-rate:** 0/0 (0%, test collection failed) | **RAT pass-rate:** 2578/2739 (94%) | **Bucket:** DA_LOSS

- **DA build_success/test_success:** True / False | **error_breakdown:** pytest_collect_success=false

## Failure stage & category

**Stage:** test_collection  
**Category:** missing_runtime_or_test_deps

## Root cause (why DA lost)

DA's Dockerfile used `uv sync` to install dependencies but did not include test extras or explicit test framework dependencies. The pyproject.toml defines test dependencies under optional extras (e.g., `pytest-anyio`), which are not installed by default `uv sync` without flags. At test collection time, pytest failed with `ModuleNotFoundError: No module named 'anyio'`, preventing any tests from being collected or executed. DA's synthesizer never installed the repo itself (`pip install -e .`) nor explicitly installed the test dependency group, causing a hard failure at the pytest-collect stage.

## What RAT did differently

- `pip install hatchling uv-dynamic-versioning && pip install -e /repo` — explicitly installed the repository in editable mode, ensuring all declared dependencies (base + extras) are resolved
- `pip install pytest pytest-asyncio pytest-anyio pytest-cov` — explicitly installed the test framework and all required test plugins (including `pytest-anyio`, which provides `anyio`)

In contrast, DA's Dockerfile contained only:
```
RUN uv sync
RUN pip install --no-cache-dir pytest
```

This approach installed base dependencies via `uv sync` but skipped the editable repo install and the full test dependency group. RAT's explicit `pip install -e /repo` and separate test dependency install ensured `anyio` was available.

## Evidence

**DA log excerpt (run.log, line ~3731):**
```
ModuleNotFoundError: No module named 'anyio'
```

**DA Dockerfile (from _result_row.json):**
```dockerfile
RUN uv sync
```
(Note: No `pip install -e .` and no explicit test dependencies after this line.)

**RAT outer_commands.json:**
```
$ pip install hatchling uv-dynamic-versioning -> rc 0
$ pip install -e /repo -> rc 0
$ pip install pytest pytest-asyncio pytest-anyio pytest-cov -> rc 0
$ run-pytest-collect -> rc 0
```

**RAT run.log confirmation:**
```
Requirement already satisfied: anyio in /usr/local/lib/python3.10/site-packages (from pytest-anyio)
```

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In src/synthesizer.py:** When generating a Dockerfile for a Python project with `uv` or `pip` as the dependency manager, ALWAYS include:
   - An explicit editable repo install: `pip install -e .` or `uv pip install -e .` (if using uv)
   - Test dependencies via either:
     - `uv sync --all-extras --dev` (if uv is the primary manager), OR
     - A post-sync step: `pip install pytest [test-plugin-names]` with discovery of required test plugins (pytest-asyncio, pytest-anyio, pytest-cov, etc.)

2. **In artifact_verify.py or recipe_repair.py:** Add a **pre-test check** that runs `pytest --collect-only -q` and flags ModuleNotFoundError as a **test-dependency collection failure**, triggering a repair loop that adds missing test plugins explicitly.

3. **Root cause in agent reasoning:** The agent must recognize that `uv sync` alone is insufficient for test environments. Probe the pyproject.toml for `[project.optional-dependencies]` with keys like "test", "dev", "testing" and ensure those groups are installed before pytest is invoked.
