# microsoft/markitdown

- DA pass-rate: 0.9852 (333/341) | RAT pass-rate: 0.9946 (367/373) | bucket: PARTIAL_TIE
- DA build_success/test_success: True/False | error_breakdown: ModuleNotFoundError: 5

## Failure stage & category

test_execution / test_collection_error

## Root cause (why DA lost)

DA collected and installed the main `markitdown` package successfully but failed to configure the monorepo's sub-packages (`markitdown-ocr`, `markitdown-sample-plugin`) for test discovery. The agent explicitly ignored those packages in its test command (`--ignore=packages/markitdown-ocr --ignore=packages/markitdown-sample-plugin`), reporting them as a "common monorepo pattern" with separate import requirements. However, these packages were still invoked by pytest and failed at collection time with `ModuleNotFoundError: No module named 'tests.test_*'` (5 errors). RAT intervened by creating missing `__init__.py` files in package hierarchy (`/repo/packages/__init__.py`, `/repo/packages/markitdown-ocr/__init__.py`, `/repo/packages/markitdown-sample-plugin/__init__.py`) and a `conftest.py` at the repo root to inject test paths, allowing pytest to discover the sub-package tests despite the module import issues.

## What RAT did differently

- Created missing `__init__.py` files: `touch /repo/packages/__init__.py /repo/packages/markitdown-ocr/__init__.py /repo/packages/markitdown-sample-plugin/__init__.py /repo/packages/markitdown/__init__.py`
- Created `/repo/conftest.py` with sys.path manipulation to inject `/repo/packages/markitdown-sample-plugin/tests` into the path, enabling import of sub-package test modules
- Reinstalled sub-packages after path setup: `pip install -e "/repo/packages/markitdown-ocr"` and `pip install -e "/repo/packages/markitdown-sample-plugin"`

## Evidence

- DA run.log: 5 `ModuleNotFoundError: No module named 'tests.test_*'` errors during pytest execution (lines 1721, 1729, 1737, 1745, 1753)
- DA Dockerfile: installs `packages/markitdown[all]` but includes no remediation for sub-package test discovery
- DA's verified test command: `pytest --collect-only -q --disable-warnings --ignore=packages/markitdown-ocr --ignore=packages/markitdown-sample-plugin` — explicitly ignores the problematic sub-packages
- RAT's command sequence (outer_commands.json): 
  - Command 19: `touch /repo/packages/__init__.py /repo/packages/markitdown-ocr/__init__.py /repo/packages/markitdown-sample-plugin/__init__.py /repo/packages/markitdown/__init__.py`
  - Command 32: `cat > /repo/conftest.py` with `sys.path.insert(0, '/repo/packages/markitdown-sample-plugin/tests')`
  - Command 33: `echo 'import sys; sys.path.insert(0, "/repo/packages/markitdown-sample-plugin/tests")' > /repo/conftest.py`
  - All return code 0 (success)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In `src/synthesizer.py`**: When pytest collection fails with `ModuleNotFoundError` in sub-packages and the repo has a monorepo structure (multiple packages under `/packages` or similar), do not silently ignore those packages. Instead, add remediation logic:
   - Detect missing `__init__.py` files in the package hierarchy and create them
   - Generate a `conftest.py` at the repo root with appropriate sys.path manipulation based on failing imports
   - This should happen during the initial setup phase, not deferred to repair cycles

2. **In `src/recipe_repair.py`**: Add a repair rule for `ModuleNotFoundError` during test collection:
   - Trigger when pytest collect fails with this error and the error module path suggests a monorepo sub-package
   - Run commands to create `__init__.py` in parent directories and generate a `conftest.py` that maps test paths
   - Re-run collection after repair

3. **Consider monorepo detection in the agent**: During environment analysis, explicitly check for monorepo patterns (multiple `pyproject.toml` or `setup.py` files under subdirectories) and proactively set up the path structure rather than treating sub-packages as optional.
