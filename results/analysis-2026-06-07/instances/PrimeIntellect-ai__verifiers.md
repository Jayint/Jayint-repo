# PrimeIntellect-ai/verifiers

- **DA pass-rate:** 0.0 (0/0 tests collected) | **RAT pass-rate:** 0.9563 (1487/1560) | **bucket:** DA_LOSS
- **DA build_success/test_success:** true/false | **error_breakdown:** ModuleNotFoundError for 'datasets' at test collection time

## Failure stage & category

**Stage:** test_collection

**Category:** missing_runtime_or_test_deps

## Root cause (why DA lost)

DA's synthesizer generated a recipe using `uv sync --group dev`, which correctly created a Python virtual environment in `.venv` with all dependencies (including 'datasets'). However, DA's Dockerfile did NOT activate the venv or configure the environment PATH. When the test harness invoked `python -m pytest`, it used the system Python interpreter without the installed packages, causing ModuleNotFoundError. In contrast, RAT executed `pip install -e /repo` directly to the system Python's site-packages, making all dependencies immediately available to any subsequent test invocation.

## What RAT did differently

- Executed `pip install -e /repo/packages/harnesses` (editable install of subpackage)
- Executed `pip install -e /repo/packages/tasksets` (editable install of subpackage)
- Executed `pip install -e /repo` (editable install of main package to system site-packages)

These pip installs placed all dependencies (including 'datasets') directly into the system Python environment, so any subsequent `python -m pytest` call automatically had access to them.

## Evidence

From DA's verified test commands (PrimeIntellect-ai__verifiers.json):
- `"verified_test_command": ".venv/bin/pytest --collect-only -q --disable-warnings"`
- Generated Dockerfile RUN command: `RUN uv sync --group dev`
- Dockerfile has NO `ENV PATH` or activation step

From DA's run.log actual execution:
```
🔧 Command: python -m pytest --co -q /testbed
ImportError while loading conftest '/testbed/tests/conftest.py'.
tests/conftest.py:8: in <module>
    from datasets import Dataset
E   ModuleNotFoundError: No module named 'datasets'
```

From RAT's outer_commands.json:
```
$ pip install -e /repo/packages/harnesses -i https://mirrors.aliyun.com/pypi/simple 2>&1 | tail -5 -> rc 0
$ pip install -e /repo/packages/tasksets -i https://mirrors.aliyun.com/pypi/simple 2>&1 | tail -5 -> rc 0
$ pip install -e /repo -i https://mirrors.aliyun.com/pypi/simple 2>&1 | tail -20 -> rc 0
```

RAT's final result: 1487 tests passed out of 1560 collected.

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In synthesizer.py:** When extracting `uv sync` or `poetry install` commands that create isolated virtual environments, add a corresponding `ENV PATH` directive or use the venv's Python directly in the test command.

2. **In src/synthesizer.py (build_commands generation):** Detect when a package manager creates a venv (uv → .venv, poetry → .venv, virtualenv → .venv) and emit a follow-up `ENV PATH` instruction that prepends the venv's bin directory.

3. **Alternative approach (safer):** When the agent proposes venv-based installs, rewrite them to system-level installs if the venv is not explicitly activated. For this project specifically, replace `uv sync --group dev` with explicit editable installs: `pip install -e /repo/packages/harnesses && pip install -e /repo/packages/tasksets && pip install -e /repo`, matching RAT's approach.

4. **In recipe_repair.py:** If self-verify detects test collection failures with ModuleNotFoundError and a previous build step created a venv, emit an `ENV PATH` step or revert to system-level installs before the next repair round.
