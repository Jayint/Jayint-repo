# open-webui/mcpo

- DA pass-rate: 0/2 (0.0%) | RAT pass-rate: 27/27 (100%) | bucket: DA_LOSS
- DA build_success/test_success: true/false | error_breakdown: ModuleNotFoundError × 2

## Failure stage & category

**Stage:** test_execution  
**Category:** missing_project_self_install

## Root cause (why DA lost)

DockerAgent agent successfully executed `pip install -e ".[dev]"` in Step 5 (confirmed in run.log: "Command succeeded"), but the recipe synthesis pipeline failed to capture this command in the final Dockerfile build_commands list. The synthesizer incorrectly excluded the install command, leaving only `pip install pytest pytest-asyncio` in the Dockerfile. When the container executed, the mcpo package and its core dependencies (typer, fastapi, pydantic, python-dotenv) were never installed, causing ModuleNotFoundError on both test collections. RAT explicitly ran `pip install -q ".[dev]"` and achieved 100% pass rate (27/27 tests).

## What RAT did differently

- RAT ran: `$ pip install -q ".[dev]"` → rc 0 (succeeded)
- RAT additionally ran: `$ pip install -q pytest pytest-asyncio` (redundant but safe)
- DA's Dockerfile contained only: `RUN ... pip install pytest pytest-asyncio ...` (missing package install)

## Evidence

**DA artifact JSON** (`/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/open-webui/mcpo/open-webui__mcpo.json` line 70):
```json
{
  "command": "ls -la; pip install -e \".[dev]\" 2>&1 | tail -20",
  "reason": "command rejected (pipe hides failures), not executed"
}
```
This is a false negative: the command with the pipe WAS rejected by sandbox preflight, but the clean command (Step 5) succeeded.

**DA run.log** (Step 5):
```
==================== Step 5 ====================
[Action] pip install -e ".[dev]"
Executing: pip install -e ".[dev]"
Command succeeded.
```

**DA Dockerfile** (`open-webui__mcpo.json` dockerfile field):
- Contains only: `pip install pytest pytest-asyncio`
- Missing: `pip install -e ".[dev]"` or equivalent

**RAT commands** (`outer_commands.json`):
```
$ pip install -q ".[dev]" -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com -> rc 0
```

**DA test error** (run.log lines 477, 487):
```
ModuleNotFoundError: No module named 'typer'
```
The import chain: `src/mcpo/__init__.py:3: in <module>` → `import typer` → ModuleNotFoundError. This module comes from the package's runtime dependencies in pyproject.toml, only installed via `pip install -e ".[dev]"` or equivalent.

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In `src/synthesizer.py` or `src/recipe_repair.py`**: When the action_log contains a successful command (exit code 0), ensure it is captured in the build_commands or runtime_preparation_commands list, regardless of whether an earlier variant of the command was rejected by the sandbox.

2. **Improve recipe extraction logic**: Parse the action_log to collect ALL state-changing commands in execution order. If a command was rejected (by preflight, pipes, etc.), check if a corrected version of that same logical command succeeded later in the log (e.g., rejected `pip install -e ".[dev]" 2>&1 | tail -20` vs. successful `pip install -e ".[dev]"`). Preserve the successful variant.

3. **Prioritize editable installs**: For Python projects with pyproject.toml, `pip install -e ".[dev]"` (or other extras) should be treated as a **mandatory dependency-installation command**, not optional. If the agent reports it executed successfully, always include it in the Dockerfile, even if pytest is also listed separately.

4. **Self-verify loop resilience**: The self-verify loop correctly detected missing=['typer', 'dotenv', 'fastapi', 'pydantic'] and attempted repair, but gave up after 2 rounds with status=unresolved. Consider extending the repair strategy to detect and re-insert missing package install commands (e.g., by searching the action_log history for successful `pip install` commands that may have been dropped).
