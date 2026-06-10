# Failure Analysis — epam/ai-dial-sdk

**Harness status**: error (failure_reason: build_failed) | **True outcome**: build_failed | **Category**: dockerfile_missing_setup_step | **Pytest**: 0/0 tests (not collected due to build failure)

## Root cause

The synthesizer omitted a critical prerequisite step from the Dockerfile. The agent successfully installed poetry via `pip3 install poetry --quiet` in the sandbox (step 7), but the generated Dockerfile's build recipe contains only `poetry install --all-extras -n` without first installing poetry. The python:3.13 base image does not include poetry, so docker build fails at step 6/7 with exit code 127 (`poetry: not found`).

## Environment / trajectory state at termination

The agent completed 11 steps (not 0 as the summary reports). Steps 5–9 involved:
- Step 5: Attempted `which poetry || pip3 install poetry --quiet` but sandbox rejected it (combined verification + mutation)
- Step 6: Ran `which poetry` (failed, poetry not yet installed)
- Step 7: Ran `pip3 install poetry --quiet` (succeeded)
- Step 8: Verified `poetry --version` (succeeded)
- Step 9: Ran `poetry install --all-extras -n` (succeeded, installed 79 packages)
- Step 10: Collected 1479 tests successfully with `poetry run pytest --collect-only -q --disable-warnings`

The sandbox environment was fully configured and working. However, the synthesizer's build recipe only captured the final `poetry install --all-extras -n` command and excluded the prerequisite `pip3 install poetry`, resulting in an incomplete Dockerfile.

## Key evidence

From the run.log (lines 366–430) and build_recipe (epam__ai-dial-sdk.json, lines 78–79):
```
"command": "which poetry || pip3 install poetry --quiet (rejected: combined mutation and verification)",
```

From eval_build/Dockerfile (line 18):
```dockerfile
RUN poetry install --all-extras -n
```

From docker build failure (run.log, lines 2051–2053):
```
#9 [6/7] RUN poetry install --all-extras -n
#9 0.230 /bin/sh: 1: poetry: not found
#9 ERROR: process "/bin/sh -c poetry install --all-extras -n" did not complete successfully: exit code: 127
```

## Takeaway for DockerAgent

The synthesizer must reconstruct the full dependency chain from the sandbox trajectory. When a command is rejected by the sandbox (step 5: combined verification + mutation), the agent correctly splits it into separate mutations and verifications (steps 6–8). The build recipe synthesizer should detect that `pip3 install poetry` (step 7) is a prerequisite for `poetry install` (step 9) and include it as an RUN step in the Dockerfile *before* the poetry install command. Currently, the excluded commands list includes the `pip3 install poetry` line, but that command is never transcribed into the Dockerfile.

## Fixability

**trivial_synthesizer_fix** — The Dockerfile generation logic needs to include the `pip3 install poetry --quiet` RUN command (executed in step 7 and already tracked in excluded_commands). A one-line addition to the synthesizer would prepend `RUN pip3 install poetry --quiet` before `RUN poetry install --all-extras -n`.
