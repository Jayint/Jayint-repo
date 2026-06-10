# frappe/press

- DA pass-rate: 0% (0/0 tests) | RAT pass-rate: 0% (0/0 tests) | bucket: BOTH_FAIL
- DA build_success/test_success: False/False | error_breakdown: Dockerfile generation failed
- RAT build_success/test_success: success/N/A | pytest_executed: False

## Failure stage & category

**Stage:** docker_build  
**Category:** docker_build_failed

## Root cause (why DA lost)

DockerAgent ran `pip install -e .` in step 19, which triggered a Docker daemon error: "no space left on device" during the container snapshot/commit operation. The Docker error corrupted the sandbox state, and the subsequent Dockerfile extraction step found no Dockerfile (`✗ Dockerfile not found` at Step 2/4). The repo is a hybrid Python/Node monorepo (Frappe backend + Vue dashboard), and DA selected `python:3.12` as the base image rather than `node:18-slim`. When DA attempted to install the Python package, the Docker layer commit failed and the entire run was aborted before generating any recipe.

## What RAT did differently

RAT:
1. Used `node:18-slim` as the base image (correct for the primary test suite in `/repo/dashboard`)
2. Ran exploration commands (`yarn install`, `run-npm-test`) which initially failed due to a Vite/Vue plugin version mismatch
3. Applied **surgical fixes** to the dependency tree:
   - Patched `/repo/dashboard/node_modules/@vitejs/plugin-vue-jsx/dist/index.mjs` by adding null-coalescing checks before accessing `this.meta`
   - Modified two critical lines with `sed` replacements (commands 69-70, 80)
   - Re-ran `run-npm-test` after each patch
4. Continued iterating through the inner_commands.json (89 commands total) despite test failures

DA:
- Never reached the point of identifying which test suite to run
- Never attempted package installation due to early Docker infrastructure failure
- Generated no Dockerfile, skipped evaluation bundle, and short-circuited the run with `skip_evaluation=True`

## Evidence

**DA failure markers (run.log):**
- Line 1330: `An error occurred during execution: 500 Server Error ... failed to write file header: write ... no space left on device`
- Line 1337: `✗ Dockerfile not found`
- Line 1340: `No accepted Verification Bundle test commands were found; skipping evaluation script generation.`
- `/result_row.json`: `"status": "error", "failure_reason": "no_dockerfile", "skip_evaluation": true`

**RAT success markers (run.log & commands):**
- Line 6-56: Dockerfile build succeeded with cached layers (node:18-slim image pulled and built)
- outer_commands.json: 90 commands executed, including `run-npm-test` (command 62, 85)
- Deliberate patch-apply logic: sed/python3 rewrites to `@vitejs/plugin-vue-jsx` to fix null-coalescing bug

**DA vs RAT image selection:**
- DA: `python:3.12` (selected by LLM, but wrong for a Node-primary monorepo)
- RAT: `node:18-slim` (correct for `/dashboard` test suite)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Image selection for hybrid repos:** When a repo contains both `pyproject.toml` AND `package.json` with substantial code in both directories, prioritize the language that owns the primary test suite. In this case, vitest is in `dashboard/`, so Node should be the base image unless the primary pytest tests are in `/press/` (they aren't—they require Frappe DB setup).

2. **Docker daemon error handling:** Catch "no space left on device" errors during layer commits and gracefully fall back to generating a Dockerfile recipe from the command sequence, rather than hard-failing with "no_dockerfile". The recipe repair loop should attempt to synthesize a Dockerfile even if the container snapshot fails.

3. **Early test command detection:** Before running heavy installations, detect the actual test command structure (e.g., check for `vitest` in package.json, `pytest` in pyproject.toml) and validate that base image + runtime prep will satisfy it. DA should have detected that the `node:18-slim` base image is needed for the test command `yarn test` in the dashboard.

4. **Hydration priority in synthesizer:** If both `pip install -e .` and `yarn install` are needed, sequence them carefully and ensure each is idempotent. Consider wrapping each in a null-check that confirms the package is installed before attempting fixes.
