# nomadkaraoke/karaoke-gen

- **DA pass-rate:** 0/0 tests (0%) | **RAT pass-rate:** 4824/5103 tests (98.19%) | **bucket:** DA_LOSS
- **DA build_success/test_success:** True / False | **error_breakdown:** None reported

## Failure stage & category

**Stage:** test_execution  
**Category:** wrong_test_command

## Root cause (why DA lost)

DockerAgent's synthesizer extracted and proposed ONLY the `--collect-only` flag, which collects tests but does NOT execute them. The verified test command became `cd /app && poetry run pytest --collect-only -q --disable-warnings`. When the harness ran this, pytest collected 4999+ tests but executed zero, resulting in 0 passed / 0 failed and a 0% pass-rate. RAT correctly executed the full test suite using its harness's `run-pytest` script, which performs actual pytest execution and achieved 98.19% pass-rate (4824/5103 passed).

## What RAT did differently

- RAT ran `run-pytest` (harness-provided test runner script), which executes the full pytest suite with results logged to `/repo/logs/run_pytest_results.json`
- DA proposed `cd /app && poetry run pytest --collect-only -q --disable-warnings`, which ONLY collects tests without executing them
- The repo's Makefile specifies the correct test command: `poetry run pytest -n auto --dist loadscope` (parallel execution), which DA never extracted

## Evidence

**File:** `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/nomadkaraoke/karaoke-gen/nomadkaraoke__karaoke-gen.json`
- `"verified_test_commands": ["cd /app && poetry run pytest --collect-only -q --disable-warnings"]`
- `"pytest_collect_success": false` (0 tests collected in final eval)
- `"pytest_pass_rate": 0.0`, `"pytest_total_tests": 0`

**File:** `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/nomadkaraoke/karaoke-gen/run.log`
- Line ~2689: Agent ran `cd /app && poetry run pytest --collect-only -q --disable-warnings` for verification
- Line ~2710: Agent concluded "The environment is successfully set up" based on collection-only result
- Line ~2715: Agent wrapped the collect-only command as the final test command in Verification Bundle

**File:** `/Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/nomadkaraoke/karaoke-gen/_result_row.json`
- `"pytest_pass_rate": 0.9819`, `"pytest_total_tests": 5103`, `"pytest_passed": 4824`

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In `agent.py` or the synthesis loop:** When proposing test commands, distinguish between TEST COLLECTION and TEST EXECUTION. The agent must extract and propose the full pytest command from Makefile/pyproject.toml/scripts (e.g., `poetry run pytest -n auto --dist loadscope` or equivalent), NOT just `pytest --collect-only`.

2. **In the validation logic:** After test collection verification, perform a SECOND check by running the proposed test command in a clean container to confirm it actually EXECUTES tests (not just collects them). Check for `> 0 executed` tests in the summary, not just collection success.

3. **In `src/synthesizer.py`:** When extracting test targets from Makefile/pyproject.toml, explicitly look for test execution commands (targets like `test:`, `test-unit:`, `test-integration:`, or `pytest` commands with actual test paths or default runners). Avoid capturing collection-only variants (`--collect-only`, `--co`) as the final test command.
