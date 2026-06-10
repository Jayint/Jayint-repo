# yutto-dev/yutto

- DA pass-rate: 0% (0/17 tests; 17 errors) | RAT pass-rate: 94.64% (106/113 passed, 6 failed)
- Bucket: **DA_LOSS** (significant failure vs. RAT success)
- DA build_success/test_success: true / false
- Error breakdown: 17x ModuleNotFoundError (100% of test errors)

## Failure Stage & Category

**Stage:** test_execution  
**Category:** missing_runtime_or_test_deps

## Root Cause (why DA lost)

DockerAgent's recipe installed `uv sync --group dev` but never installed the yutto package itself as editable via `pip install -e .`. Since yutto is a local package in the /testbed source tree and not on PyPI, all test imports fail with `ModuleNotFoundError: No module named 'yutto'`. RAT correctly identified this requirement and ran `pip install -e .` after uv sync, allowing all tests to import the main package and run successfully.

## What RAT did differently

- RAT ran: `uv sync --dev -p 3.10 2>&1 | tail -30` (full build with project install)
- RAT then ran: **`pip install -e . -i https://mirrors.aliyun.com/pypi/simple 2>&1 | tail -20`** (explicitly installed the yutto package as editable)
- RAT additionally ran: `pip install pytest pytest-rerunfailures syrupy pytest-codspeed -i https://mirrors.aliyun.com/pypi/simple` (pinned test runner deps)

DA's Dockerfile only contained:
- `pip install uv`
- `uv sync --group dev`
- No `pip install -e .` step

## Evidence

**DA run.log markers (line 962-1167):**
- Line 962–1012: All test collection attempts fail with `ModuleNotFoundError: No module named 'yutto'` or `ModuleNotFoundError: No module named 'httpx'`
- Line 1081: Error breakdown shows "ModuleNotFoundError: 17"
- Line 1164–1167: Self-verify reports "Round 0: tests executed (tests_passed). Done." but this is misleading—pytest actually collected 17 errors, not passes. The self-verify loop exited with status=resolved because build_success=true, masking the test failure.

**DA Dockerfile (yutto-dev__yutto.json):**
```dockerfile
RUN uv sync --group dev
# No post-setup compatibility helpers needed
```

**DA build_recipe (logs.build_recipe.build_commands):**
```
"build_commands": [
  "pip install uv",
  "uv sync --group dev"
]
```

**RAT outer_commands.json sequence:**
- Command 39: `pip install uv -i https://mirrors.aliyun.com/pypi/simple` → rc=0
- Command 42: `uv sync --dev -p 3.10 --no-install-project 2>&1` → rc=0 (first attempt without project)
- Command 49: `uv sync --dev -p 3.10 2>&1` → rc=0 (full sync with project)
- **Command 57: `pip install -e . -i https://mirrors.aliyun.com/pypi/simple 2>&1`** → rc=0 ← **DA LACKED THIS**
- Command 59: `pip install pytest pytest-rerunfailures syrupy pytest-codspeed` → rc=0

**RAT result:** pytest_collect_success=true, pytest_pass_rate=0.9464 (106/113 tests passed)

## Fix Recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In src/synthesizer.py**: When analyzing build output from `uv sync`, check if the repo has a local package (pyproject.toml at root with a [tool.uv] or [project] section). If yes and `uv sync` is used, always append an explicit `pip install -e .` step unless [project] explicitly sets install-scripts or the pyproject.toml is package-free.

2. **In src/artifact_verify.py**: Enhance self-verify logic to catch pytest *collection* failures (0 tests collected or all errors) as a hard failure, not a pass. Currently, `tests_passed` (0 test passes) is being treated as success when pytest exits with collection errors. Ensure that `pytest_errors > 0` or `pytest_total_tests == 0` triggers repair.

3. **In agent.py**: When the agent proposes a build recipe with uv/pip install, follow up with a sanity-check question: "Does this repo have a local package that needs editable install?" Inspect pyproject.toml for `packages = [...]` or `[tool.uv]` markers and suggest `pip install -e .` as a followup if the root is a package.

4. **Recipe repair loop**: After `uv sync` fails to make tests importable, the repair loop should recognize the pattern and suggest `pip install -e .` as the next command to try.
