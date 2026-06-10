# python-websockets/websockets

- DA pass-rate: 0.0% (0/0 executed) | RAT pass-rate: 80.3% (2218/2786) | bucket: DA_LOSS
- DA build_success/test_success: True/False | error_breakdown: timeout (docker_timeout after 599.9s)

## Failure stage & category

**test_execution** / **wrong_test_command**

## Root cause (why DA lost)

DA's agent only issued a pytest collection-only command (`python -m pytest --collect-only -q --disable-warnings`) rather than an actual test execution command. The agent reached Step 10 and concluded "The environment is fully configured" with no runtime preparation needed, but failed to request a full pytest run. RAT issued `python3 /home/tools/run_pytest.py` which executed the full test suite (2786 tests total), while DA's collection-only command collected tests but never ran them, causing the docker exec to hang and eventually timeout after 599.9 seconds with 0 tests executed.

## What RAT did differently

- RAT ran: `$ run-pytest -> rc 0` (outer) and `$ python3 /home/tools/run_pytest.py -> rc 1` (inner, partial failure acceptable)
- RAT also set environment variable: `export WEBSOCKETS_TESTS_TIMEOUT_FACTOR=12` to handle timeout-sensitive tests
- RAT's recipe included actual pytest execution; DA's only included collection

## Evidence

- DA log line 5334: `[Recorded Test Command] python -m pytest --collect-only -q --disable-warnings`
- DA log line 5345: `{"runtime_preparation_commands": [], "test_commands": ["python -m pytest --collect-only -q --disable-warnings"]}`
- DA log line 5342: `Thought: The environment is fully configured. Pytest collection succeeded with 2249 tests, no errors. No runtime preparation commands are needed.` — agent stopped after collection
- DA run.log line 496: `[Action] python -m pytest --collect-only -q --disable-warnings` (Step 9 only verified collection, no test execution step)
- RAT outer_commands.json: `run-pytest` completed with rc 0
- RAT inner_commands.json: `python3 /home/tools/run_pytest.py` completed with rc 1 (expected partial failures)
- DA _result_row.json: `"pytest_executed": false, "pytest_total_tests": 0, "pytest_passed": 0`
- RAT _result_row.json: `"pytest_executed": true, "pytest_total_tests": 2786, "pytest_passed": 2218, "pytest_pass_rate": 0.803`

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

DA's agent must distinguish between **test collection** and **test execution**. After a successful `pytest --collect-only`, the agent must issue a subsequent step (e.g., Step 10) that performs the actual test run via `pytest -v` or equivalent without `--collect-only`. The agent's conclusion logic should not terminate after seeing "pytest collection succeeded" — it must verify that actual test execution commands are in the Verification Bundle. Add a check in the agent reasoning: if `test_commands` contains only `--collect-only` flags, automatically append or substitute a full test execution command (e.g., `python -m pytest` without `--collect-only`). Alternatively, force the agent to explicitly reason about and request test execution in a separate action after verifying test collection.
