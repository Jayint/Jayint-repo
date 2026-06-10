# NevaMind-AI/memU-server

- DA pass-rate: 0.0% (0/8 tests) | RAT pass-rate: 39.24% (31/80 tests) | bucket: DA_LOSS
- DA build_success/test_success: True/False | error_breakdown: 8x ModuleNotFoundError

## Failure stage & category

**Stage:** test_execution (tests collected but all 8 collected tests fail with import errors)

**Category:** missing_project_self_install (DA's synthesizer failed to install the repo package itself into the Docker image)

## Root cause (why DA lost)

DA's synthesizer generated `pip install --group dev` as the sole installation command. In a fresh Dockerfile context (not an interactive session), this command is syntactically invalid: `--group dev` requires a package specifier (`.` for current directory or a PyPI name), but none was provided. The command should have been `pip install --group dev .` or `pip install -e ".[dev]"`. Without the repo package installed, all tests fail with ModuleNotFoundError (missing 'memu', 'fastapi', 'pydantic', 'temporalio'). RAT correctly used `pip install -e ".[dev]"` (command #36 in outer_commands.json) and achieved 39% pass rate by installing the repo itself plus all dev dependencies.

## What RAT did differently

- RAT executed `pip install -e ".[dev]" -i https://mirrors.aliyun.com/pypi/simple` (editable install of local package with dev dependency group)
- DA executed only `pip install --group dev` (which fails to identify a package target in fresh Dockerfile context)
- RAT also debugged memu-py import resolution across 50+ diagnostic steps (showing extensive troubleshooting), ultimately succeeding despite module resolution challenges

## Evidence

- **DA Dockerfile**: Line 937 (run.log) shows `pip install --group dev` with no package specifier—syntactically invalid for Dockerfile (not an interactive shell where CWD context persists)
- **DA self-verify loop**: Lines 1427–1438 (run.log) show three repair attempts, all ending with `status=unresolved` after detecting missing=['memu'] in Round 2
- **DA error breakdown**: _result_row.json shows 8x ModuleNotFoundError (8 tests collected but all failed)
- **RAT outer_commands.json command #36**: `pip install -e ".[dev]" -i https://mirrors.aliyun.com/pypi/simple 2>&1 | tail -20` → rc 0 (success)
- **DA verified commands**: Only `['pytest --collect-only -q --disable-warnings']` with no pre-test installation command
- **RAT result**: 31/80 tests passed (39.24%) despite encountering same memu-py import issues, because the repo package was installed

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In synthesizer.py**: When generating install commands for pyproject.toml-based projects, always append ` .` (current directory) to pip install commands for local package installation. Rule: `pip install --group <group>` → `pip install --group <group> .` (or use `-e .` for editable mode).

2. **In recipe_repair.py**: When the repair loop detects ModuleNotFoundError for the repo's own modules (e.g., 'app', 'memu' from memu-server package), detect this pattern and suggest adding `pip install -e .` as a runtime preparation command, not just adding it to dependency lists.

3. **Self-verify rejection logic**: If repair rounds converge on the same missing modules, classify as "missing_project_self_install" rather than "unresolved", and auto-inject `pip install -e .` before re-testing.

