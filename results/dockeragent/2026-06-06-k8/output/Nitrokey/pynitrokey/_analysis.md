# Failure Analysis — Nitrokey/pynitrokey

**Harness status**: success | **True outcome**: success_tests_all_error | **Category**: editable_install_missing | **Pytest result**: 0 passed, 1 error (ModuleNotFoundError: No module named 'nitrokey')

## Root cause

The local package `pynitrokey` was not installed as an editable dependency in the final eval Docker image, despite `poetry install --with dev` being invoked. The agent verified setup successfully in the sandbox (190 tests collected), but the final eval image lacks the `nitrokey` module import path. The Dockerfile correctly places `WORKDIR /testbed` and clones the repo, but `poetry install --with dev` alone does not guarantee the local package is installed in editable mode without explicit configuration or a postinstall step.

## Environment / trajectory state at termination

- **Agent steps**: 6 (completed)
- **Installed**: poetry, 63 external dependencies (ansi2html, cryptography, hidapi, pytest, etc.), pytest added by harness
- **Missing**: pynitrokey package itself (not installed in editable mode in final image)
- **Last action**: Step 5 successfully collected 190 tests in sandbox via `poetry run pytest --collect-only`. Step 6 concluded "Success" based on successful collection, but did not verify the import actually works in the final Docker build context.
- **Failure point**: Eval image build/test execution: `from nitrokey.nk3.secrets_app import Instruction, SecretsApp` in `pynitrokey/conftest.py:13` fails because the `nitrokey` module (the local package) is not discoverable.

## Key evidence

```dockerfile
RUN git clone https://github.com/Nitrokey/pynitrokey /testbed
RUN poetry install --with dev
```

Pytest error in eval image:
```
pynitrokey/conftest.py:13: in <module>
    from nitrokey.nk3.secrets_app import Instruction, SecretsApp
E   ModuleNotFoundError: No module named 'nitrokey'
```

Agent's sandbox verification (Step 5, line 239-430):
```
poetry run pytest --collect-only -q --disable-warnings
[... 190 tests collected in 1.39s ...]
```

The agent verified collection succeeded but did not re-verify in the final eval image context.

## Takeaway for DockerAgent

When a local Python package must be imported by tests:
1. Verify that `poetry install` or `pip install` includes the **local package in editable mode** (typically via `poetry install` with the local source in `pyproject.toml`, or explicit `pip install -e .`).
2. Do not rely solely on successful test collection in the sandbox; verify that the package is actually importable (e.g., `python -c "import nitrokey"`) **before finalizing the Dockerfile**.
3. If tests collected successfully in sandbox but fail to import in the final image, the issue is likely that the verification command used a different Python environment or path than the eval image will use. Ensure the Dockerfile's Python environment (after `poetry install`) matches the sandbox verification exactly.

## Fixability

**trivial_synthesizer_fix** — The agent correctly diagnosed that `poetry install --with dev` was needed and verified it succeeded. The issue is that in the eval Docker context, the local package installation was not persisted correctly. Adding an explicit post-install verification step (e.g., `RUN python -c "from nitrokey.nk3.secrets_app import Instruction, SecretsApp"`) or ensuring `poetry install` with editable mode in `pyproject.toml` would catch and fix this before the eval image runs tests.
