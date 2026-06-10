# Failure Analysis — yutto-dev/yutto

**Status**: success (harness) | **True Outcome**: success_tests_all_error | **Category**: test_deps_not_installed | **Pytest**: 17 tests, 0 passed, 17 ModuleNotFoundError

## Root cause

The agent successfully configured the environment with `uv sync`, which created a `.venv` containing yutto and its dependencies. The verification step (Step 11) confirmed `import yutto` works when run via `uv run`. However, the Dockerfile installs pytest to the system Python (line 110: `pip install --no-cache-dir pytest`) rather than into the `.venv`. When the eval harness executes actual tests, pytest runs from the system Python context and cannot import `yutto`, which lives in the `.venv`. The agent only verified test *collection* (which uses `--collect-only`), not test *execution*.

## Environment / trajectory state at termination

- **Agent steps run**: 13 (completed normally)
- **Build result**: Dockerfile built successfully
- **Installed in environment**: 
  - System Python: uv, pytest (from final line 110)
  - .venv: yutto, httpx, biliass, and 36+ dependencies (from `uv sync`)
- **Last failing action**: Eval image test execution — pytest runs from system Python and cannot find `yutto` module in the `.venv`
- **Test collection succeeded**: 113 tests collected in Step 12 via `uv run pytest --collect-only`
- **Test execution failed**: All 17 test modules error with `ModuleNotFoundError: No module named 'yutto'` when pytest runs without `uv run` activation

## Key evidence

```dockerfile
# Line 19: Creates .venv with dependencies
RUN uv sync

# Line 20: Verification succeeds (runs within uv run context)
RUN uv run python3 -c "import yutto; print('yutto imported successfully')"

# Line 110: Installs pytest to SYSTEM Python (not .venv)
RUN pip install --no-cache-dir pytest
```

```
E   ModuleNotFoundError: No module named 'yutto'
E   ModuleNotFoundError: No module named 'httpx'
```

At Step 12, collection succeeded via `uv run pytest --collect-only` (113 tests collected), but eval image test execution fails because pytest from system Python cannot access the `.venv`.

## Takeaway for DockerAgent

When using `uv` with a `.venv`, either:
1. Install test dependencies (pytest, etc.) into the `.venv` via `pyproject.toml` so they are included in `uv sync`, OR
2. Ensure the verified test command uses `uv run` to activate the `.venv` at execution time, not just at collection time

The agent verified test *collection* but should also verify test *execution* to catch this environment incompleteness. The final test command should be `uv run pytest ...` (execution), not just `uv run pytest --collect-only` (collection).

## Fixability

**trivial_synthesizer_fix** — The Dockerfile synthesis logic should either (a) add pytest as a dev dependency in the project's pyproject.toml so it gets installed to the `.venv` during `uv sync`, or (b) ensure all test invocations use `uv run pytest` rather than raw `pytest`. This is a straightforward code-generation fix in the synthesizer's Dockerfile building logic.
