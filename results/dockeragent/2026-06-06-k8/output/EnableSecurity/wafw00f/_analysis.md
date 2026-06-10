# Failure Analysis — EnableSecurity/wafw00f

**Harness Status:** success | **True Outcome:** success_tests_all_error | **Category:** test_deps_not_installed | **Pytest:** 0.6667 pass_rate (6 pass, 3 errors from ModuleNotFoundError on collection)

## Root cause

The agent successfully installed dependencies in the sandbox (Step 6: `pip install -e ".[dev]"` succeeded, collecting all 48 tests), but the synthesizer failed to record those commands in the generated Dockerfile. The eval image contains only `pip install pytest` but lacks the runtime dependency `requests` and test dependency `responses`, causing all dependent test modules to fail collection with ModuleNotFoundError.

## Environment / trajectory state at termination

**Agent steps executed:** 8 (read pyproject.toml, attempted combined command which was rejected, split into pip upgrade, editable install which succeeded, pytest collection which succeeded)

**Installed in sandbox:** requests, responses, pytest, pytest-mock, prospector (via `pip install -e ".[dev]"` in Step 6)

**Installed in eval image:** pytest only (via `pip install --no-cache-dir pytest` added by harness)

**Missing in eval image:** requests (runtime), responses (test)

**Last failing action:** Collection in eval image fails because tests/test_detection.py, tests/test_evillib.py, and tests/test_matching.py all import modules that depend on requests/responses which are not in the image.

## Key evidence

```
Step 6 output:
Successfully installed [...] requests-2.34.2 [...] responses-0.26.1 [...] wafw00f-2.4.2

Step 7 output:
========================= 48 tests collected in 0.24s ==========================

eval_build/Dockerfile line 22:
RUN pip install --no-cache-dir pytest

agent_run_summary.json build_recipe:
"build_commands": [],
"excluded_commands": [{"command": "pip install --upgrade pip && pip install -e \".[dev]\"", "reason": "Rejected by system before execution"}]

pytest error in eval image:
tests/test_detection.py:4: in <module>
    import responses
E   ModuleNotFoundError: No module named 'responses'
```

## Takeaway for DockerAgent

The agent's sandbox environment was correctly configured in Step 6 (editable install with dev deps succeeded), but the Dockerfile synthesizer failed to carry those successful commands forward to the final image. The synthesizer's `build_recipe` logic incorrectly claims the combined command was "rejected by system before execution" when in fact Step 6 split the command and the editable install succeeded independently. The synthesizer must track which setup commands executed successfully in the sandbox and emit them into the Dockerfile, not skip them based on a previous rejection.

## Fixability

**trivial_synthesizer_fix** — The Dockerfile is missing a single line: `RUN pip install -e ".[dev]"` (or equivalent to install the dev extras). The build logic has the information (successful Step 6 execution) but failed to emit it. Adding the missing install command to the Dockerfile template would immediately resolve the test collection errors.
