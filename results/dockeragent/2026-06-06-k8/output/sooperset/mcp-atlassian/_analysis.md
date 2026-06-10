# Failure Analysis — sooperset/mcp-atlassian

**Harness status: success | True outcome: success_tests_all_error | Category: test_deps_not_installed | Pytest: 0 tests, 0 passed, ModuleNotFoundError**

## Root cause

The Dockerfile synthesizes `uv sync --no-editable` to install dependencies into a virtual environment (.venv), but the eval_build/Dockerfile also injects `RUN pip install --no-cache-dir pytest` into the system Python (line 23). When the test command runs `uv run pytest`, pytest (from system context) attempts to load the 'anyio' plugin but cannot find it in the system Python, causing a ModuleNotFoundError and preventing test collection entirely.

## Environment / trajectory state at termination

**Agent steps:** 12 completed  
**Installed:** All 140 project dependencies (including anyio==4.12.1, pytest==9.0.2, pytest-anyio==0.0.0) via `uv sync` into .venv  
**Missing:** pytest plugin discovery chain fails because pytest is installed system-wide (line 23) but tries to load anyio plugin from system context, not from venv  
**Last failing action:** Step 11 attempted `uv run pytest --collect-only`, which successfully invoked the venv but pytest failed during conftest/plugin import with `ModuleNotFoundError: No module named 'anyio'`

## Key evidence

```
[Step 11 collection attempt]
Traceback (most recent call last):
  File "/usr/local/lib/python3.13/site-packages/_pytest/config/__init__.py", line 885, in import_plugin
    __import__(importspec)
ModuleNotFoundError: No module named 'anyio'

ImportError: Error importing plugin "anyio": No module named 'anyio'

[eval_build Dockerfile line 23 - injected by harness]
RUN pip install --no-cache-dir pytest

[Step 10 observations - uv sync succeeded]
Installed 140 packages in 106ms
 + anyio==4.12.1
 + pytest==9.0.2
 + pytest-anyio==0.0.0
```

The root issue: `uv sync --no-editable` correctly installed all dependencies into .venv, but the eval_build injected a bare `pip install pytest` that installed pytest into /usr/local/lib/python3.13/site-packages (system), creating a mismatch where pytest can't access venv plugins.

## Takeaway for DockerAgent

When `uv sync` is used for environment setup, the final test command must ensure pytest runs within that venv context. The synthesized Dockerfile correctly used `uv run pytest`, but the harness's eval_build post-processing (adding bare `pip install pytest`) introduced a system/venv conflict. Either:
1. Avoid bare pip installs in eval_build after uv-based setup, or
2. Ensure pytest is already included in the project's test dependencies (which it should be), so `uv sync` includes it.

The agent's strategy was sound; the issue is in eval_build post-processing, not DockerAgent's synthesis.

## Fixability

**needs_service_deps** — This appears to be a test_harness_artifact (the eval_build injecting `pip install pytest` breaks the uv venv setup). The agent correctly installed all dependencies; the integration layer introduced the conflict.
