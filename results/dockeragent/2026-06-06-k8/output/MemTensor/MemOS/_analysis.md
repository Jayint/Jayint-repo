# Failure Analysis — MemTensor/MemOS

**Harness Status**: success | **True Outcome**: success_tests_all_error | **Category**: test_deps_not_installed | **Pytest**: pass_rate=0.2662, 154 total, 107 errors (113 ModuleNotFoundError), 41 passed, 6 failed

## Root cause

The agent ran `poetry install --with dev,test` which installed pydantic (visible in logs: "Installing pydantic (2.11.7)"), but when pytest actually runs in the eval Docker build, pydantic is not available. The test discovery phase (Step 10) succeeded and collected all 154 tests cleanly, but when the eval harness runs pytest for real in a fresh container build (via the synthesized Dockerfile), it fails with 113 ModuleNotFoundError for pydantic/fastapi/yaml/transformers, indicating the project dependencies were not persisted into the final Docker image.

## Environment / trajectory state at termination

**Steps used**: 11/11 (no budget constraint)

**What got installed in sandbox during agent run**:
- poetry (pip install poetry)
- poetry install --with dev,test (executed successfully, showed "Installing pydantic (2.11.7)" and 100+ other packages)
- torch (via `pip install torch`)
- pytest collected 154 tests cleanly in Step 10

**What's missing in the eval image**:
- pydantic, fastapi, yaml, transformers, and other project dependencies
- The project itself was never installed in editable mode (`pip install -e .`)

**Last action attempted**: Step 10 ran `poetry run pytest --collect-only -q --disable-warnings` and collected 154 tests cleanly, reporting "All 621 tests collected successfully with zero errors" (internally inconsistent—it reported 621 tests collected but only 154 are in the actual pytest results). Step 11 concluded the agent had "succeeded," but the agent was deceived by the collect-only output.

## Key evidence

```
RUN poetry install --with dev,test
RUN JAYINT_PIP_ATTEMPT=1; ... 'pip install torch' ...
RUN poetry run pip install torch
```

(from synthesized Dockerfile, lines 64-66)

The Dockerfile lacks an explicit `pip install -e .` or equivalent to install the MemoryOS package itself into the Poetry virtual environment. Step 7 in the sandbox reported success for `poetry install --with dev,test`, but this does not guarantee the current project is installed in the venv—only its dependencies are installed. When the eval image later runs `poetry run pytest`, the venv has pydantic etc. but the MemoryOS module itself is not importable, causing all test imports to fail with "No module named 'pydantic'" (the first dependency that each test tries to import, which fails because the conftest or the test module itself cannot be imported—the real culprit is the missing project package).

Pytest output showed:
- Step 10 (sandbox, collect-only): succeeded, listed tests
- Eval build (real Docker build): pytest ran and hit 113 ModuleNotFoundError on test setup

The agent declared success after seeing collection succeed, but did not verify that the project itself was installed.

## Takeaway for DockerAgent

1. **Poetry install is insufficient**: Running `poetry install --with dev,test` installs dependencies but does NOT install the current project package into the venv. The agent should follow up with `poetry run pip install -e .` or check that `poetry install` in the repo directory installs the project itself (many pyproject.toml files do include the project as an editable install, but this is not automatic).

2. **Collect-only is a false positive for hollowed environments**: A test collection that succeeds (or even partially succeeds) does NOT prove the environment is ready. The agent reached Step 11 conclusion ("Success") based on test collection, but test execution in a fresh eval image revealed the environment was incomplete. The agent should have run an actual test execution (not just collection) as the final verification to ensure imports work end-to-end.

3. **Dockerfile synthesis needs a safety check**: When synthesizing a Dockerfile that uses Poetry, the build recipe should verify that:
   - The current project is installed (not just its dependencies)
   - A sample test import or import of the main package succeeds in the final image

## Fixability

**trivial_synthesizer_fix** — The fix is one line: add `RUN poetry run pip install -e .` after `poetry install --with dev,test`, or verify that poetry install properly installs the project. This is a code generation issue in the synthesizer, not a genuine project setup complexity.
