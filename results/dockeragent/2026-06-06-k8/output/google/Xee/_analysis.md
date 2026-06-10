# Failure Analysis — google/Xee

**Harness status**: success | **True outcome**: success_tests_all_error | **Category**: editable_install_missing | **Pytest**: 0 tests collected in eval image (conftest import error)

## Root cause

The agent successfully installed the package and its test dependencies in the sandbox (`pip install -e ".[tests]"` in Step 3), and verified that 59 tests could be collected. However, the synthesized Dockerfile (lines 82-105 in digest) only cloned the repo and installed pytest—it **never included the package installation step**. When the eval image ran pytest, conftest.py failed to import `absl` (which is a direct dependency of xee), causing test collection to fail.

## Environment / trajectory state at termination

- **Agent steps**: 5 completed (reported in line 80)
- **Sandbox state**: Successfully ran `pip install -e ".[tests]"` in Step 3; installed absl-py and 40+ other deps; pytest reported 59 tests collected
- **Eval image state**: Only pytest installed; xee package not in image; dependencies missing
- **Last agent action**: Step 5 concluded "Environment is fully configured" and returned a verification bundle with test command only (no setup commands)
- **Critical gap**: Agent verified test collection using sandbox env but did not propagate the `pip install -e ".[tests]"` command to the Dockerfile generation

## Key evidence

```dockerfile
# From eval_build/Dockerfile (lines 82-105)
FROM python:3.13
RUN git clone https://github.com/google/Xee /testbed
RUN pip install --no-cache-dir pytest
# Missing: pip install -e ".[tests]" or equivalent
```

```
# Pytest error (run_pytest_collect_results.json, line 110)
ImportError while loading conftest '/testbed/conftest.py'.
conftest.py:16: in <module>
    from absl import app
E   ModuleNotFoundError: No module named 'absl'
```

```
# Agent's successful sandbox install (Step 3, line 405)
Successfully installed ... absl-py-2.4.0 ... xee-0.1.1 ...
```

## Takeaway for DockerAgent

When the agent runs setup commands in the sandbox and verifies test collection succeeds, it must explicitly include those setup commands (especially editable installs with extras like `.[tests]`) in the final Dockerfile. The verification bundle's test_commands field should only contain the test invocation; the setup/installation commands must be captured in the Dockerfile build recipe. Currently, the agent is dropping the critical `pip install -e ".[tests]"` step when generating the final image.

## Fixability

**trivial_synthesizer_fix** — The agent successfully identified and executed the correct setup command in the sandbox. The issue is purely in the Dockerfile synthesis: the setup command should have been materialized as a RUN line before the pytest install. This is a code-generation bug, not a dependency conflict or missing logic.
