# ModelEngine-Group/nexent

**DA pass-rate**: 0/0 (build failed) | **RAT pass-rate**: 0/1 (collection error, ModuleNotFoundError) | **bucket**: BOTH_FAIL
**DA build_success**: False, test_success: False | **RAT build_success**: True, test_success: False
**DA error_breakdown**: {} (no Dockerfile generated) | **RAT error_breakdown**: {"ModuleNotFoundError": 1}

## Failure stage & category
DA: `docker_build` / `missing_project_self_install`
RAT: `test_collection` / `test_collection_error` (but recovery to scoring succeeded)

## Root cause (why DA lost)

DA's agent ran `pip install -e sdk/` without any extras (line 802 of run.log), which installed the entire `nexent==0.1.2` package with its full dependency tree: elasticsearch, kubernetes, mem0ai, qdrant-client, smolagents, and dozens of transitive dependencies totaling hundreds of packages. The base image's Docker container ran out of disk space during dependency resolution (exit 128, "no space left on device" at line 1204), causing the agent to crash mid-run before pytest collection and before generating a Dockerfile. RAT, by contrast, used targeted `pip install -e ".[quality,data_process]"` installs, consuming minimal disk and completing the install + test run successfully (even though tests had a collection error unrelated to the install).

## What RAT did differently

- **RAT command 42** (line 2575 of RAT run.log): `cd /repo/backend && pip install -q -e ".[test]" -i https://mirrors.aliyun.com/pypi/simple`
- **RAT command 44** (line 2577 of RAT run.log): `cd /repo/sdk && pip install -q -e ".[quality,data_process]" -i https://mirrors.aliyun.com/pypi/simple`

DA's equivalent commands (line 330, 802 of DA run.log):
- `pip install -e backend/[test]` ✓ (correct)
- `pip install -e sdk/` ✗ (missing extras; installs 100+ packages instead of ~10)

The SDK pyproject.toml defines optional-dependencies for test, quality, and data_process (readable from DA run.log lines 222-242). RAT's use of `[quality,data_process]` is minimal but sufficient for test collection; DA's bare `pip install -e sdk/` pulls all of nexent's app dependencies (11 core packages per line 195-209, plus all their transitive dependencies).

## Evidence

- **DA failure marker** (run.log line 1204): `An error occurred during execution: [Errno 28] No space left on device`
- **DA failure marker** (run.log line 1210-1211): `[Step 2/4] Extracting Dockerfile... ✗ Dockerfile not found`
- **DA failure marker** (_result_row.json): `"status": "error", "failure_reason": "no_dockerfile"`
- **RAT success marker** (run.log line 2639): `✅ Copied Pytest execution results`
- **RAT command 42** (run.log line 2575, captured in run_pytest_results.json): `cd /repo/backend && pip install -q -e ".[test]"` (returncode 0)
- **RAT command 44** (run.log line 2577): `cd /repo/sdk && pip install -q -e ".[quality,data_process]"` (returncode 0)
- **RAT test execution** (_result_row.json): `"status": "success", "pytest_executed": true` (despite collection error)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

**In `src/synthesizer.py` when generating install commands for editable packages:**

When a pyproject.toml defines `[project.optional-dependencies]`, parse and select minimal extras based on context:
1. For **test environments**: prefer extras like `[test]`, `[dev]`, or `[quality]` if they exist
2. **Never** use bare `pip install -e <dir>` on a multi-module repo without checking for optional-dependencies first
3. Check if pyproject.toml has an extras field; if it does and the base package has many core dependencies, use extras to reduce disk footprint
4. Fall back to parsing the main dependencies and installing only those needed for test collection (e.g., pytest, the package's own code, essential runtime deps)

This will prevent unbounded dependency bloat and disk exhaustion on complex repos like nexent.
