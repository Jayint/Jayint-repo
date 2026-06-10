# NewFuture/DDNS

- **DA pass-rate**: 99.53% (853/877 passed, 4 failed) | **RAT pass-rate**: 100% (852/877, 4 skipped) | **Bucket**: PARTIAL_TIE
- **DA build_success/test_success**: True/False | **Error breakdown**: 4 × AssertionError (permission tests)

## Failure stage & category

**Stage**: test_execution  
**Category**: da_outperformed_rat

## Root cause (why DA lost)

DockerAgent ran the full test suite without handling the environment-specific constraints that arise when executing inside a Docker container running as root. Four permission-related tests fail because the root user bypasses file permission checks at the OS level, causing assertions like `assertRaises(PermissionError)` to fail—the exception is never raised when root attempts to write to read-only files or inaccessible paths. RAT generated a pytest conftest.py hook that detects root execution at test-collection time and skips these four tests, allowing the remaining 852 tests to pass cleanly. DockerAgent's agent report only included a bare `pytest --collect-only` command with no runtime preparation, missing the adaptive fixture that RAT synthesized.

## What RAT did differently

RAT synthesized and injected a conftest.py file that:
- Detects root execution via `os.geteuid() == 0`
- Skips exactly four permission-related tests by name when running as root:
  - `test_load_config_permission_denied`
  - `test_save_config_invalid_path`
  - `test_save_config_permission_denied`
  - `test_save_config_readonly_file`

RAT's command sequence (commands 59, 61, 66, 68 in outer_commands.json):
```
cat > /repo/tests/conftest.py << 'EOF'
# coding=utf-8
"""
pytest conftest - handle root user permission issues
"""
import os
import pytest

def pytest_collection_modifyitems(items):
    """Skip permission-related tests when running as root (root bypasses file permissions)"""
    if os.geteuid() == 0:
        skip_root = pytest.mark.skip(reason="Running as root - file permission checks are bypassed by the OS")
        for item in items:
            if item.name in (
                'test_load_config_permission_denied',
                'test_save_config_invalid_path',
                'test_save_config_permission_denied',
                'test_save_config_readonly_file',
            ) and item.location[0].endswith('test_config_file.py'):
                item.add_marker(skip_root)
EOF
```

## Evidence

**DA run.log** (lines 3751–3762):
```
Verification Bundle:
[Verification Bundle] Accepted 0 runtime preparation command(s) and 1 test command(s) from the agent report.
[Self-Verify] Round 0: building clean-room image…
[Self-Verify] Round 0: tests executed (tests_passed). Done.
[Self-Verify] status=resolved; keeping original recipe.
```
DA's recipe contained no conftest injection; verified_runtime_preparation_commands is empty.

**DA run_pytest_results.json**:
```json
"pytest_pass_rate": 0.9953,
"pytest_total_tests": 877,
"pytest_passed": 853,
"pytest_failed": 4,
"error_breakdown": {
  "AssertionError": 4
}
```

**DA run.log failure details**:
```
tests/test_config_file.py::TestConfigFile::test_load_config_permission_denied FAILED
tests/test_config_file.py::TestConfigFile::test_save_config_invalid_path FAILED
tests/test_config_file.py::TestConfigFile::test_save_config_permission_denied FAILED
tests/test_config_file.py::TestConfigFile::test_save_config_readonly_file FAILED

E   AssertionError: Exception not raised
E   AssertionError: (<class 'OSError'>, ...) not raised
E   AssertionError: (<class 'OSError'>, ...) not raised
```

**RAT outer_commands.json** (commands 59, 61, 66, 68):
```
[59] $ cat > /repo/tests/conftest.py << 'EOF'
...conftest.py content...
EOF -> rc 0

[61] $ python3 -c " content = '''...conftest.py via Python...''' ..."
[66] $ python3 << 'PYEOF'
...conftest.py creation via heredoc...
PYEOF -> rc 0

[68] $ python3 -c "...conftest.py one-liner..." -> rc 0
[70] $ python -m pytest --tb=short -q 2>&1 | tail -15 -> rc 0
[72] $ run-pytest-collect -> rc 0
[74] $ run-pytest -> rc 0
```

RAT's _result_row.json:
```json
"pytest_pass_rate": 1.0,
"pytest_total_tests": 877,
"pytest_passed": 852,
"pytest_failed": 0,
"pytest_errors": 0
```

## Fix recommendation

1. **In src/synthesizer.py**: Add heuristic detection for permission-related test failures. When the agent's initial test run yields AssertionError failures on tests matching `test_*permission*`, `test_*readonly*`, or `test_*invalid_path*`, inject a conftest.py with root-detection logic.

2. **In src/recipe_repair.py**: Enhance the artifact repair loop to recognize environment-specific test incompatibilities. If a test fails with "Exception not raised" and the exception type is PermissionError/IOError/OSError, treat this as a fixable condition and synthesize a conftest.py skip hook.

3. **Alternatively, in agent.py**: Instruct the agent explicitly to detect and handle permission-sensitive tests by creating conftest.py fixtures before reporting the recipe. Include in the system prompt: "If tests check file permissions, create a pytest conftest.py that skips those tests when running as root (os.geteuid() == 0)."

4. **Test this pattern proactively** on projects with permission tests (test_config_file.py, test_file_permissions.py, etc.) to avoid the 4-test regression.
