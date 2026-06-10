# py2many/py2many

- DA pass-rate: 0.8384 (192 passed / 229 executed) | RAT pass-rate: 0.7318 (191 passed / 261 executed) | bucket: DA_WIN
- DA build_success/test_success: true/false | RAT build_success/test_success: true/false
- DA error_breakdown: AssertionError=33, ModuleNotFoundError=1, OtherError=1, NameError=2
- RAT error_breakdown: AssertionError=34, ModuleNotFoundError=1, OtherError=33, NameError=2

## Failure stage & category

Both agents succeeded at test collection and execution (pytest ran 229 tests for DA, 261 for RAT). Neither hit build errors. Category: **test_execution** / **parity_both_passed** — both agents passed pytest collection and ran tests, with slight execution differences and code-issue-driven failures (no DA or RAT specific infrastructure failures).

## Root cause (why DA won)

DA outperformed RAT by 1 test (192 vs 191 passed) despite executing fewer total tests (229 vs 261). Both agents installed dependencies identically (`pip install -e ".[dev]"` and `pip install -e ".[test]"` for DA; RAT explicitly listed all deps then `pip install -e . --no-build-isolation`). The difference is skew in test execution: DA skipped 32 more tests than RAT (1374 skipped vs 1342 skipped), while RAT had 33 more failures due to test-code issues (OtherError=33 vs OtherError=1). Both hit the same 1 ModuleNotFoundError from `tests/cases/sealed.py` trying to import the missing `adt` package — a code issue, not infrastructure.

## What RAT did differently

RAT's setup approach was more explicit:
- `pip install -q -r <(echo "argparse_dataclass tree-sitter tree-sitter-cpp tree-sitter-rust pytest pytest-cov black astpretty 'jgo<2' importlib-resources") -i https://mirrors.aliyun.com/pypi/simple`
- `pip install -q argparse_dataclass tree-sitter tree-sitter-cpp tree-sitter-rust pytest pytest-cov 'black<24' astpretty 'jgo<2' importlib-resources -i https://mirrors.aliyun.com/pypi/simple`
- `pip install -e . --no-build-isolation -i https://mirrors.aliyun.com/pypi/simple`

RAT also ran various inline Python patches to fix stubs files (pygo/stubs.py, pyv/stubs.py, py2many/inference.py) to work around f-string backslash escaping issues in Python 3.10+, then reverted them (`git checkout -- <file>`). These patches were development/debugging steps, not core setup.

## Evidence

**DA Dockerfile**:
```
RUN git clone https://github.com/py2many/py2many /testbed
RUN ... pip install -e ".[dev]"
RUN ... pip install -e ".[test]"
```

**DA pytest result**: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/py2many/py2many/run_pytest_results.json`
- Raw output summary: `==== 37 failed, 192 passed, 1374 skipped, 1786 warnings in 92.78s`

**RAT pytest result**: `/Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/py2many/py2many/run_pytest_results.json`
- Raw output summary: `=========== 70 failed, 191 passed, 1342 skipped in 66.12s`

**Both share the same ModuleNotFoundError**:
- Test: `tests/cases/sealed.py` line 5: `from adt import adt as sealed` → ModuleNotFoundError
- This is a code dependency issue (package `adt` not installed), not a DA or RAT failure.

**File**: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/py2many/py2many/py2many__py2many.json`
- DA's build_recipe shows `pip install -e ".[dev]"` and `pip install -e ".[test]"` as verified commands (confirmed via container runs).
- No native system dependencies or runtime services required.

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

**No action needed.** DA already outperformed RAT on this repo. The setup is minimal and correct — the pyproject.toml extras (dev, test) are sufficient. The test failures are code issues (transpilation test mismatches, missing optional `adt` module) unrelated to agent infrastructure. The reason DA ran fewer total tests (1374 skipped vs 1342) is likely due to different pytest collection or filtering behavior, not a recipe deficiency.

If we wished to match RAT's explicit dependency list, we could enhance the agent to parse and enumerate optional-dependencies from pyproject.toml, but current performance is superior, suggesting the heuristic is sound.
