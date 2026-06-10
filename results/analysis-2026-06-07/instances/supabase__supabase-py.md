# supabase/supabase-py

- DA pass-rate: 0/0 (no tests executed) | RAT pass-rate: 0/32 | bucket: BOTH_FAIL
- DA build_success/test_success: false/false | error_breakdown: no Dockerfile generated
- DA failure: `failure_reason="no_dockerfile"`, `error="agent produced no Dockerfile: Dockerfile generation failed"`
- RAT result: status=success, 32 tests collected, all failed with ModuleNotFoundError

## Failure stage & category

**DA: docker_build / empty_or_rejected_verification_bundle**
**RAT: test_execution / dataset_hard_rat_also_failed**

## Root cause (why DA failed)

DA's agent successfully analyzed the monorepo structure and ran per-package test collection (e.g., `cd /app/src/auth && uv run --package supabase-auth pytest --collect-only`), collecting ~162 tests successfully across all packages. However, when DA attempted to generate a unified verification bundle with these commands, the **synthesizer rejected the bundle** because:

1. The agent proposed per-package pytest runs with `uv run --package <name>` from different working directories
2. The synthesizer could not reconcile multi-directory, multi-context commands into a single executable recipe
3. With no accepted verification bundle, the synthesizer skipped Dockerfile generation entirely (line 2946 in DA run.log: "[Warning] Configuration did not complete successfully. No Dockerfile will be generated")
4. DA result: `skip_evaluation=true`, `test_command_source="missing_agent_verification_bundle"`

## What RAT did differently

- RAT ran `uv sync --index-url https://mirrors.aliyun.com/pypi/simple` at the monorepo root
- RAT then ran **per-package `uv run --package <name> pytest`** commands from the root directory (not `cd` into subdirectories)
  - Examples from RAT's outer_commands.json:
    - `$ uv run --package supabase_functions pytest --co -q`
    - `$ uv run --package supabase_auth pytest --co -q`
    - `$ uv run --package storage3 pytest --co -q`
- RAT **installed additional test dependencies** that were not in the root pyproject.toml: `pyjwt[crypto]`, `faker`, `respx`, `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `pytest-depends`, `pyotp`
- RAT executed per-package test runs and collected test data, allowing the framework to proceed with Dockerfile generation and test execution

## Evidence

- DA run.log line 2674: `!!!!!!!!!!!!!!!!!!!!!! Interrupted: 22 errors during collection !!!!!!!!!!!!!!!!!!` (root-level pytest collection failed due to conftest module path conflicts)
- DA run.log line 2946: `[Warning] Configuration did not complete successfully. No Dockerfile will be generated.`
- DA JSON: `"test_command_source": "missing_agent_verification_bundle"`, `"skip_evaluation": true`
- RAT run.log line 1316: `` `run-pytest-collect` executes with returncode: 1`` (initial collection failed)
- RAT run.log lines 1329-1344: RAT's agent installed test dependencies (`pyjwt[crypto]`, `faker`, `respx`, pytest plugins) with `uv pip install`
- RAT outer_commands: multiple `uv run --package <name> pytest` commands executed successfully

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In agent.py**: When analyzing a monorepo with multiple packages, prefer **per-package test execution** commands (using `uv run --package <name>` from the root) over mixed working-directory approaches. The synthesizer's verification bundle expects a single, linearizable command sequence.

2. **In src/synthesizer.py**: When the verification bundle contains heterogeneous commands (different directories, different contexts), **flatten them into a single-directory execution plan** before rejecting the bundle. For example, convert:
   - `[cd /app/src/auth && pytest, cd /app/src/functions && pytest]` → `[uv run --package auth pytest, uv run --package functions pytest]` (all from root)

3. **In src/recipe_repair.py**: Add a repair loop that detects **`missing_agent_verification_bundle`** and suggests to the agent: "Try using `uv run --package <name>` commands from a single directory rather than `cd` into subdirectories. This makes your commands mergeable into a single test recipe."

4. **Test dependency detection**: Ensure the synthesizer or pre-flight check includes heuristics to detect and install test dependencies (`pytest`, `faker`, `respx`, `pyjwt[crypto]`, etc.) that are listed in sub-package pyproject.tomls but not in the root. RAT's explicit `uv pip install` of these deps was crucial.

5. **Document the monorepo pattern**: This repo uses `uv sync` + `uv run --package <name>` as the intended test pattern. Agents should recognize this and avoid naive root-level pytest runs that trigger conftest path conflicts.
