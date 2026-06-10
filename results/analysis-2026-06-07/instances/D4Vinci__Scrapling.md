# D4Vinci/Scrapling

- DA pass-rate: 92.71% (674/727) | RAT pass-rate: 93.24% (676/725) | bucket: PARTIAL_TIE
- DA build_success/test_success: true/false | RAT success: true
- Error breakdown DA: OtherError=51, ModuleNotFoundError=2 | RAT: OtherError=49, ModuleNotFoundError=0

## Failure stage & category
test_execution / missing_runtime_or_test_deps

## Root cause (why DA lost)

DockerAgent's synthesized Dockerfile omitted `IPython` from the dependency install list, causing 2 test failures with `ModuleNotFoundError: No module named 'IPython'` in the shell_functionality tests. RAT detected these failures during test execution, iteratively diagnosed the root cause via `failed_tests` JSON inspection, and ran `pip install IPython` to fix it. DockerAgent never attempted runtime repair after the initial build, leaving the incomplete dependency set in place.

## What RAT did differently

RAT executed these critical repair actions that DA omitted:

- `pip install -q --no-cache-dir IPython -i https://mirrors.aliyun.com/pypi/simple` (after diagnosing `ModuleNotFoundError: No module named 'IPython'` via `failed_tests` inspection)
- Examined `pyproject.toml` and found `"IPython>=8.37"` listed as a dependency
- Re-ran tests after installing IPython to verify the fix

DockerAgent's Dockerfile only installed:
- `pip install -e ".[fetchers]"`
- `pip install -r tests/requirements.txt`
- Browser libraries (libnspr4, libnss3, etc.)
- `pip install "scrapling[ai]" requests`
- `pip install autoscraper`
- `pip install mechanicalsoup`
- `pip install pyquery selectolax parsel`

But never included `IPython`, despite it being declared in `pyproject.toml`.

## Evidence

- DA run.log line 10673-10674: `FAILED tests/cli/test_shell_functionality.py::TestCustomShell::test_shell_initialization - ModuleNotFoundError: No module named 'IPython'` and `test_shell_namespace` similarly failed
- DA run.log line 11065-11070: Error breakdown shows 2 ModuleNotFoundErrors for IPython
- DA _result_row.json: `"pytest_passed": 674, "pytest_failed": 51, "pytest_errors": 2` with `"ModuleNotFoundError": 2`
- RAT run.log line 1837-1838: Found the same ModuleNotFoundError via inspection
- RAT run.log line 1859, 1862: Diagnosed and executed `pip install -q --no-cache-dir IPython`
- RAT run.log line 2592: IPython install listed in repair commands executed
- RAT _result_row.json: `"pytest_passed": 676, "pytest_failed": 49, "pytest_errors": 0` (no ModuleNotFoundErrors)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

**Issue:** DockerAgent's synthesis pipeline does not extract optional test dependencies from `pyproject.toml` (e.g., extras_require, test group dependencies declared under `[project.optional-dependencies]` or `[tool.poetry.group.test]`).

**Fix in `src/synthesizer.py`:**

1. When parsing `pyproject.toml` or `setup.cfg`, extract not only base dependencies but also optional groups marked for testing:
   - `[project.optional-dependencies]` keys like `test`, `dev`, `testing`
   - `[tool.poetry.group.test]` dependencies
   - Conditional dependencies in install_requires if they include test-specific markers

2. Include these in the install recipe. For this repo, `IPython>=8.37` is declared in `pyproject.toml` under test dependencies and must be installed alongside the main package.

3. Alternatively, when synthesizing from `tests/requirements.txt`, ensure the agent does not drop optional package groups declared in the base manifest. If `tests/requirements.txt` does not list IPython but `pyproject.toml` declares it as a test dependency, reconcile the conflict by installing both declarative sources.

**Long-term:** Implement post-test-run inspection (similar to RAT's `failed_tests` JSON parsing) to detect ModuleNotFoundError and automatically trigger runtime repair commands. This would catch missing optional dependencies even if they were omitted during synthesis.
