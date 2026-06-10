# Failure Analysis — python-websockets/websockets

**Status:** success (harness) | **True outcome:** success_tests_all_error | **Category:** repo2run_weak_test_deficient | **Pytest:** 41 errors, 0 passed, ModuleNotFoundError: No module named 'websockets'

## Root cause

The agent successfully executed `pip install -e .` (confirmed: "Successfully installed websockets-16.1.dev17+g9ff5c77" at run.log:332), but **failed to record this command in the build_recipe** (`build_recipe.build_commands` is empty `[]`). The synthesized Dockerfile therefore lacks the installation step, resulting in a hollow success: the Docker image builds without error but cannot import the websockets package, causing all 41 test collection attempts to fail with ModuleNotFoundError.

## Environment / trajectory state at termination

- **Agent steps:** 8 (completed within budget)
- **Sandbox execution:** agent successfully ran `pip install -e .` and installed test dependencies (mitmproxy, trio, pytest, etc.) — all confirmed installed
- **Dockerfile synthesis:** **CRITICAL BUG** — the Dockerfile contains zero build commands (`build_recipe.build_commands: []`), only the repo clone and pytest install
- **Last failing action:** pytest collection in the eval image fails because websockets is not installed (not carried forward from sandbox to Dockerfile)
- **Error:** All 41 test modules fail at collection with "ModuleNotFoundError: No module named 'websockets'"

## Key evidence

From `python-websockets__websockets.json` (the agent's final summary):

```json
"build_recipe": {
  "build_commands": [],
  "post_test_patch_commands": [],
  ...
  "rationale": "The environment setup consisted of two successful state-changing commands: installing the package in editable mode, then installing test dependencies (mitmproxy, python-socks[asyncio], trio, werkzeug, pytest). These were executed in order and successfully verified by the test collection without errors."
}
```

And from `run.log:332`:
```
Successfully installed websockets-16.1.dev17+g9ff5c77
```

Yet the eval Dockerfile (line 18):
```
# Agent's verified setup instructions
# No additional setup instructions from agent
```

The agent's reasoning and execution were correct, but the **build_recipe capture logic failed to extract and record the successful `pip install -e .` command** into the Dockerfile synthesis layer.

## Takeaway for DockerAgent

1. **Synthesizer contract violation:** The synthesizer must extract successful, verified state-changing commands from the agent's sandbox trajectory and inject them into the Dockerfile as RUN instructions. When `build_recipe.build_commands` is empty but the rationale mentions successful installation, the capture logic is broken.
2. **Root of mismatch:** The agent's verification bundle likely recorded only the test command (`pytest --collect-only`) but not the preceding setup commands, or the build_recipe extraction did not traverse the full command history correctly.
3. **Recommended fix:** Audit the trajectory-to-recipe extraction code — ensure all state-changing commands executed before the final verification command are captured in build_recipe, especially editable installs and pip/package-manager operations.

## Fixability

**trivial_synthesizer_fix** — This is a code-gen bug in the synthesizer's recipe extraction, not a genuine dependency conflict or environmental issue. The agent did its job correctly (found and installed dependencies); the synthesizer failed to transcribe the successful commands into the Dockerfile. A fix to the recipe capture logic will resolve this for all similar cases.
