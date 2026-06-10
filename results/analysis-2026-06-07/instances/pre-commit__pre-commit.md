# pre-commit/pre-commit

- **DA pass-rate**: 0.0 (0/820 tests executed, timeout) | **RAT pass-rate**: 92.28% (753/820 passed) | **bucket**: DA_LOSS
- **DA build_success/test_success**: False / False | **error_breakdown**: timeout (600s) during test execution phase
- **pytest_executed**: False (DA timed out before executing actual tests)

## Failure stage & category

**Stage**: test_execution | **Category**: empty_or_rejected_verification_bundle

## Root cause (why DA lost)

DA's verification bundle was **rejected** because the agent submitted only a `pytest --collect-only` command as the test command, not an actual test execution command. The verification system requires that submitted test commands must have been previously observed succeeding in the build environment. Since only `--collect-only` was used during agent setup (not a full test run), the evaluator auto-finalized from previously verified commands but marked `skip_evaluation=True`, resulting in no test script being generated. The evaluation phase then timed out waiting for `/run_pytest.py` (which was never created).

## What RAT did differently

RAT's container had pre-built wrapper scripts (`run-pytest-collect` and `run-pytest`) available via the base image, which RAT directly invoked:
- `run-pytest-collect` ✅ rc:0 (collected 820 tests)
- `run-pytest` ✅ rc:0 (executed full test suite, 753 passed)

RAT's commands were accepted immediately because they matched pre-existing verified execution patterns in the container environment.

## Evidence

**DA's rejected verification bundle** (from run.log):
```
Verification Bundle:
{"runtime_preparation_commands": ["export GIT_AUTHOR_NAME=test && ..."], 
 "test_commands": ["GIT_AUTHOR_NAME=test ... pytest --collect-only -q --disable-warnings"]}

[Verification Bundle] Rejected agent-reported bundle because at least one command 
was not previously observed succeeding in the final environment.
[Warning] Agent claimed success but did not provide a valid Verification Bundle.
[Verification Bundle] Auto-finalized from previously verified test commands.
```

**DA's final state** (from _result_row.json):
- `"status": "timeout"`
- `"pytest_executed": false`
- `"skip_evaluation": True` (logs)
- `"verified_test_commands": ["GIT_AUTHOR_NAME=test ... pytest --collect-only -q ..."]` (collect-only, not full test)

**RAT's execution** (from outer_commands.json):
- Line 8: `run-pytest-collect` → rc:0
- Line 9: `run-pytest` → rc:0 (full test suite execution)

**DA's Dockerfile** (correctly contains):
```dockerfile
RUN pip install -e ".[dev]"
RUN pip install -r requirements-dev.txt
```
Dependencies were installed correctly; the issue was not in build but in test command validation.

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Agent must distinguish collection vs. execution**: When the agent runs `pytest --collect-only` during setup, it should immediately follow up with a short actual test run (e.g., `pytest tests/store_test.py::test_our_session_fixture_works -xvs`) on the same verified environment to establish that a **full test execution command** is valid, not just collection.

2. **Verification bundle must contain executable test command**: In `src/recipe_repair.py` or the verification logic, ensure the test_commands list contains a command that actually *runs* tests (without `--collect-only`), not just collects them. If the agent only verified collection, the repair loop should immediately execute at least one test to validate the full pipeline.

3. **Fallback wrapper detection**: In `src/synthesizer.py`, detect and use pre-existing test wrapper scripts in the base image (e.g., `run-pytest`, `pytest-runner`, `tox`) before synthesizing custom pytest invocations. This mirrors RAT's strategy and avoids rejection due to unverified custom commands.

4. **Runtime env vars in runner script, not runtime_preparation**: The agent correctly identified needed env vars (GIT_AUTHOR_NAME, GIT_COMMITTER_NAME, etc.) but placed them in `runtime_preparation_commands` which may not persist across script invocation. Instead, embed them directly in the generated test script (e.g., `/run_pytest.py`) so they are guaranteed to be set when tests execute.
