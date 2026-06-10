# epam/ai-dial-sdk

- **DA pass-rate:** 0% (0/0 tests collected) | **RAT pass-rate:** 100% (1471/1479) | **bucket:** DA_LOSS
- **DA build_success/test_success:** True / False | **error_breakdown:** ModuleNotFoundError: No module named 'fastapi'

## Failure stage & category

**test_collection_error** / **missing_runtime_or_test_deps** — DA's synthesizer failed to install the repo's own package into the poetry venv, leaving fastapi and other dependencies unresolved at test runtime.

## Root cause (why DA lost)

DA's Dockerfile correctly installs Poetry and runs `poetry install --with test`, which installs fastapi, pytest, and all test dependencies into a `.venv` inside `/testbed`. However, the test command DA chose—`poetry run pytest --collect-only -q --disable-warnings`—is executed *outside* the Docker build phase, in the evaluation container. When the evaluation harness runs that command, it fails with `ModuleNotFoundError: No module named 'fastapi'` because fastapi was installed into the Docker image's venv, not the evaluation environment. DA never installed the package itself (no `pip install -e .` or equivalent), and the evaluation container has no access to the Docker venv.

## What RAT did differently

RAT executed a sequence of explicit `pip install` commands to install both runtime and test dependencies directly into the evaluation environment:
- `pip install -q "fastapi>=0.51,<1.0" "uvicorn>=0.19,<1.0" "pydantic>=1.10.17,<3" "wrapt>=1.10,<2"` → rc 0
- `pip install -q "pytest>=9.0.3" "pytest-asyncio>=1.3.0" "httpx>=0.25.0,<1.0" "respx>=0.21.1" "aiohttp>=3.13.4" "aioresponses>=0.7.6" "requests>=2.33" "responses>=0.25.3" "pillow>=12.2.0"` → rc 0
- `pip install -q -e /repo --no-build-isolation` → rc 0 (installed the repo's own package into the environment)
- Confirmed import with `python -c "from aidial_sdk import DIALApp; print('Import OK')"` → rc 0

RAT's `run-pytest-collect` and `run-pytest` then executed against a fully provisioned environment, collecting and running 1479 tests successfully.

## Evidence

**DA's Dockerfile (lines 23-24):**
```dockerfile
RUN JAYINT_PIP_ATTEMPT=1; ... pip install poetry ...
RUN poetry install --with test
```

**DA's test command:**
```
poetry run pytest --collect-only -q --disable-warnings
```

**DA's run.log output (test collection failure):**
```
ImportError while loading conftest '/testbed/tests/conftest.py'.
tests/conftest.py:4: in <module>
    from aidial_sdk import DIALApp
aidial_sdk/__init__.py:1: in <module>
    from aidial_sdk.application import DIALApp
aidial_sdk/application.py:8: in <module>
    from fastapi import FastAPI, HTTPException, Request
E   ModuleNotFoundError: No module named 'fastapi'
```

**DA's verified_test_commands:**
```json
["poetry run pytest --collect-only -q --disable-warnings"]
```

**DA's verified_runtime_preparation_commands:**
```json
[]
```

**RAT's commands (from outer_commands.json):**
- `pip install -q "fastapi>=0.51,<1.0" "uvicorn>=0.19,<1.0" "pydantic>=1.10.17,<3" "wrapt>=1.10,<2" -i https://mirrors.aliyun.com/pypi/simple` → rc 0
- `pip install -q "pytest>=9.0.3" "pytest-asyncio>=1.3.0" "httpx>=0.25.0,<1.0" "respx>=0.21.1" "aiohttp>=3.13.4" "aioresponses>=0.7.6" "requests>=2.33" "responses>=0.25.3" "pillow>=12.2.0" -i https://mirrors.aliyun.com/pypi/simple` → rc 0
- `pip install -q -e /repo --no-build-isolation -i https://mirrors.aliyun.com/pypi/simple` → rc 0
- `python -c "from aidial_sdk import DIALApp; print('Import OK')"` → rc 0
- `run-pytest-collect` → rc 0 (collected 1479 tests)
- `run-pytest` → rc 0 (1471/1479 passed)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

**Root issue:** The synthesizer trusts Poetry's isolated venv isolation and generates `poetry run` commands as the test runner. But the evaluation harness executes those commands *outside* the Docker build phase, where the venv is inaccessible.

**Fix in synthesizer.py:**
1. When the agent chooses Poetry as the dependency manager, the synthesizer must **not** rely on `poetry run`. Instead, extract the dependencies from `pyproject.toml` and convert them into explicit `pip install` commands for inclusion in `verified_runtime_preparation_commands`.
2. Ensure the repo's own package is installed with `pip install -e /repo --no-build-isolation` or equivalent.
3. Use `python -m pytest` (or bare `pytest`) as the test runner, not `poetry run pytest`.
4. Verify via import test (`python -c "from <main_module> import ..."`).

**Alternative (less preferred):** Generate a Dockerfile that exports the venv's site-packages and adds it to `PYTHONPATH`, or copies the venv to a persistent location. This is fragile and violates the intent of the venv.

**Why this matters:** The RAT baseline demonstrates that Poetry projects are handled by explicit pip installs of extracted dependencies, not by relying on Poetry's internal venv at test time. DA's synthesizer must align with this pattern.
