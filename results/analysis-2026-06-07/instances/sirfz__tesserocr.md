# sirfz/tesserocr

- DA pass-rate: 0% (0/24 tests) | RAT pass-rate: 0% (0/24 tests) | bucket: BOTH_FAIL
- DA build_success: true, test_success: false | RAT language: python | error_breakdown: both frameworks found tests but reported 0 pass-rate

## Failure stage & category

test_execution / test_collection_error (parse/hang)

## Root cause (why DA lost / both failed)

**DA-specific issue:** DockerAgent reported `pytest --collect-only -q --disable-warnings` as its only test command, which is a *collection* command, not a test *execution* command. The evaluation framework ran this command and parsed 0 test results because `--collect-only` does not execute tests; it only discovers them. When the eval framework attempted to auto-run full `pytest`, execution hung at `test_detect_os` and failed to generate the JUnit XML report, resulting in 0 pass-rate.

**RAT's advantage:** RAT discovered (via interactive exploration) that `test_detect_os` hangs and must be excluded with `-k "not test_detect_os"`, and it installed an additional tessdata package (`tesseract-ocr-eng`) and set `TESSDATA_PREFIX` environment variable to work around tesseract runtime issues. RAT ran multiple troubleshooting attempts: `python -m pytest -v --tb=short`, `timeout 30 python -m pytest`, `timeout 60 python -m pytest -v -k "not test_detect_os"`, and direct Python API tests. Despite these efforts, RAT also reported 0 pass-rate—indicating the test suite itself is fragile even with workarounds.

**Why both are 0:** The test that hangs (`test_detect_os`) is likely a real bug in the test suite or environment-specific issue, not an environment-setup failure. Both agents achieved the same outcome (0 tests executed successfully), but DA never discovered or attempted the workarounds that RAT tried.

## What RAT did differently

- Ran actual test execution commands: `python -m pytest -v --tb=short`, `python -m pytest -v --tb=long`, `timeout 30 python -m pytest -v`, `timeout 60 python -m pytest -v --tb=short -k "not test_detect_os"`
- Discovered and installed `tesseract-ocr-eng` (language data package)
- Set `TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata/`
- Excluded the problematic test: `-k "not test_detect_os"`
- Ran direct Python API tests to verify tesserocr functionality
- Attempted multiple error-recovery loops when tests failed/timed out

DA only reported: `pytest --collect-only -q --disable-warnings` (collection, not execution)

## Evidence

**DA Result JSON** (`_result_row.json`):
- `pytest_pass_rate: 0.0`
- `pytest_total_tests: 0`
- `build_success: true`
- `test_success: false`

**DA Verified Test Commands** (from `sirfz__tesserocr.json`):
- `verified_test_commands: ["pytest --collect-only -q --disable-warnings"]`

**DA Run Log** (lines 1516-1526):
```
tests/test_api.py::TestTessBaseApi::test_detect_os 

============================================================
⚠️  Failed to parse JUnit XML: JUnit XML file not found: /testbed/logs/junit_report.xml
📝 Falling back to text output parsing...

Total tests: 0
✅ Passed: 0
```

**RAT Result JSON** (`_result_row.json`):
- `pytest_pass_rate: 0.0`
- `pytest_total_tests: 0`
- Same outcome, but achieved after running actual test execution commands

**RAT Outer Commands** (from `outer_commands.json`):
- `pip install -e . -i https://mirrors.aliyun.com/pypi/simple`
- `apt-get install -y -qq tesseract-ocr-eng`
- `timeout 30 python -m pytest -v --tb=short`
- `timeout 60 python -m pytest -v --tb=short -k "not test_detect_os"`

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In agent.py**: Ensure the agent distinguishes between test *collection* (`pytest --collect-only`) and test *execution* (`pytest` or `python -m pytest`). The verification bundle should require at least one actual test execution command, not just collection.

2. **In src/recipe_repair.py** or **src/synthesizer.py**: Add a validation step that rejects test command lists containing only `--collect-only` flags. If the agent reports a collect-only command, flag it for repair by asking for an actual execution command.

3. **Timeout handling**: Tesserocr tests hang on certain tests (e.g., `test_detect_os`). Add a recipe-repair loop that, after a test timeout, suggests excluding problematic tests with `-k` filters or increasing timeout values.

4. **Tessdata environment variable**: For OCR-heavy packages like tesserocr, include a post-install step in the Dockerfile to export `TESSDATA_PREFIX` if tessdata is detected in the system.
