# aapatre/Automatic-Udemy-Course-Enroller-GET-PAID-UDEMY-COURSES-for-FREE

- DA pass-rate: 0.0% (0/0 tests) | RAT pass-rate: 100.0% (27/27 tests) | bucket: DA_LOSS
- DA build_success/test_success: true/false | error_breakdown: tests_did_not_execute (missing selenium, webdriver_manager, bs4)

## Failure stage & category
test_execution / missing_runtime_or_test_deps

## Root cause (why DA lost)

DA's agent during the interactive setup phase successfully ran `poetry install` which installed all dependencies including selenium and webdriver_manager. However, the agent attempted to execute this as a combined command `cd /workspace/... && poetry config virtualenvs.create false && poetry install`, which was **rejected by the sandbox preflight check** with reason "this Action combines multiple independent setup mutations." The agent then split the command and ran `poetry config` and `poetry install` separately, but both executed in the interactive session's working directory, not in the /testbed directory that exists in the final Dockerfile. Consequently, the synthesized Dockerfile never captured the `poetry install` command—it only contains `pip install --upgrade pip setuptools wheel` and `pip install poetry` without the actual dependency installation step. When pytest ran in the eval phase, it failed to collect tests because selenium, webdriver_manager, and beautifulsoup4 modules were missing.

## What RAT did differently

RAT took a direct approach:
- `pip install -q -r /repo/requirements.txt -i https://mirrors.aliyun.com/pypi/simple` (command 31 of outer_commands.json)
- This single command installs all dependencies from requirements.txt including selenium, webdriver_manager, and beautifulsoup4

DA instead relied on poetry, but:
- The combined poetry command was rejected by sandbox preflight
- Poetry install was executed in the wrong context during setup
- The final Dockerfile synthesis failed to capture the poetry install step
- Result: dependencies were not installed in the final evaluated image

## Evidence

**DA failure markers:**
- `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/aapatre/Automatic-Udemy-Course-Enroller-GET-PAID-UDEMY-COURSES-for-FREE/run.log:391-395` — Command rejected: "this Action combines multiple independent setup mutations"
- `run.log:1005` — "[Self-Verify] Round 0: tests did not execute (tests_did_not_execute); missing=['selenium']."
- `run.log:1009` — "[Self-Verify] Round 1: tests did not execute (tests_did_not_execute); missing=['webdriver_manager']."
- `run.log:1013` — "[Self-Verify] Round 2: tests did not execute (tests_did_not_execute); missing=['bs4']."
- `run.log:1014` — "[Self-Verify] status=unresolved; keeping original recipe."
- `aapatre__Automatic-Udemy-Course-Enroller-GET-PAID-UDEMY-COURSES-for-FREE.json:88` — build_recipe rationale states "poetry.lock" not installed; poetry install was excluded from Dockerfile with reason "combined command rejected by agent and not executed"

**RAT success markers:**
- `outer_commands.json:31` — `pip install -q -r /repo/requirements.txt -i https://mirrors.aliyun.com/pypi/simple -> rc 0`
- `_result_row.json:pytest_pass_rate: 1.0` (27/27 tests passed)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In agent.py or the sandbox environment setup:** Allow compound cd+install commands or remove the preflight check that rejects them for dependency installation contexts. The restriction "combines multiple independent setup mutations" is too strict for standard dependency installation patterns like `cd /dir && pip install -r requirements.txt` or `cd /dir && poetry install`.

2. **In src/synthesizer.py (recipe generation):** After the interactive setup phase, scan the executed commands for dependency installation patterns (poetry install, pip install -r, etc.) that executed successfully but were not captured in the initial command synthesis. Explicitly add these to the Dockerfile if they are missing.

3. **In src/recipe_repair.py (self-verify loop):** When repair fails after 3 rounds with missing packages, instead of giving up, attempt a fallback: check if the repo has requirements.txt and fall back to `pip install -r requirements.txt` as a final repair attempt before declaring status=unresolved.

4. **Alternative approach:** The agent should detect when poetry.lock or pyproject.toml exists and prefer `pip install -r requirements.txt` if available, as a more direct path that avoids the combined-command rejection issue.
