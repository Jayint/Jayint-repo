# rayai-labs/agentic-ray

- DA pass-rate: 0/17 (0%) | RAT pass-rate: 0/0 (–) | bucket: BOTH_FAIL
- DA build_success/test_success: true/false | RAT build_success/test_success: true/false
- error_breakdown: 17 ModuleNotFoundError (httpx, websockets, superserve)

## Failure stage & category

**Stage:** test_execution  
**Category:** missing_runtime_or_test_deps

## Root cause (why DA failed)

DA detected this polyglot repository as **Python-only** (via ImageSelector matching `.python-version` and `pyproject.toml`), then executed only Python pytest commands against `packages/python-sdk` and `tests/sdk-e2e-py`. However, the repository is a **Node.js/TypeScript monorepo** with Python test directories as secondary components. DA's Dockerfile used `python:3.12` and `uv sync` to install Python dependencies from the workspace root's `pyproject.toml`, but this did NOT resolve the TypeScript SDK test dependencies (httpx, websockets, superserve), which are JavaScript npm packages listed in `packages/sdk/package.json`. The Python SDK tests tried to import these Node-installed packages and failed with ModuleNotFoundError × 17.

## What RAT did differently

RAT correctly identified the repository as **Node.js-first** (ran `npm install -g bun@1.3.5` followed by `bun install`) and focused on the TypeScript/Vitest test suite in `packages/sdk/`. Rather than attempt pytest on the Python SDK, RAT diagnosed a vitest timer issue in `http.test.ts`, modified the test setup (reordered `vi.useRealTimers()` before `vi.unstubAllGlobals()`), and attempted `npx vitest run`. While RAT's own test execution still failed (rc 1 on final run), it never encountered the missing-module errors that plagued DA, because it installed the correct dependency manager (bun/npm) for the primary test suite.

Key RAT commands:
- `npm install -g bun@1.3.5 --registry=https://registry.npmmirror.com`
- `bun install --registry=https://registry.npmmirror.com`
- `bun run test --filter=@superserve/sdk`
- `npx vitest run --reporter=verbose tests/http.test.ts`

## Evidence

**DA log markers:**
- `[ImageSelector] Detected language: python (via llm)` — incorrectly classified polyglot repo as Python
- `[ImageSelector] Selected base image: python:3.12`
- `Language: python` in ImageSelector output
- `cd /app/packages/python-sdk && uv run pytest` — ran pytest, not vitest
- Error log lines 953–1529: all 17 test errors are `ModuleNotFoundError: No module named 'httpx'`, `'websockets'`, `'superserve'` — these are npm packages, not pip packages

**DA Dockerfile:**
```dockerfile
FROM python:3.12
RUN uv sync
```
Did not install Node.js, bun, or npm.

**RAT log markers:**
- `which bun 2>/dev/null || echo "bun not found"` — checked for bun early
- `npm install -g bun@1.3.5` — installed bun globally
- `bun install --registry=https://registry.npmmirror.com` — installed npm/node dependencies
- `bun run test --filter=@superserve/sdk` — targeted the TypeScript SDK tests
- RAT never attempted Python pytest; instead, all test commands were Node-based (vitest, bun run)

**Repository structure (visible in both logs):**
- `packages/sdk/` → TypeScript/Vitest tests, npm package (@superserve/sdk)
- `packages/python-sdk/` → Python SDK, pytest
- `tests/sdk-e2e-py/` → Python end-to-end tests, pytest
- **Primary** test suite is TypeScript (vitest), not Python (pytest)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Multi-language detection:** Extend ImageSelector to detect **primary** language by counting test files (not just config file presence). A repo with >10 TypeScript test files and 2 Python test directories is Node-first, not Python-first. Prefer matching test files over generic config presence.

2. **Monorepo handling:** When multiple `package.json` files exist (e.g., root + `packages/*/`), prioritize root-level `package.json` with workspaces/monorepo config (turbo.json, lerna.json) as a signal of JavaScript monorepo, even if Python pyproject.toml is also present.

3. **Synthesizer recipe:** If language is ambiguous, generate recipes for BOTH the primary AND secondary test suites. In this case, generate:
   - Node + bun/npm + vitest (primary)
   - Python + uv + pytest (secondary, only if Node suite runs cleanly)
   
4. **Dockerfile base image:** For polyglot repos, default to Node.js (node:18 or node:20) and install Python 3.x on top if both test suites are required, rather than defaulting to Python and omitting Node tooling.

5. **Test verification:** In the self-verify loop, if test collection fails with "module not found" errors mentioning npm package names (e.g., 'httpx', 'superserve'), abort early and suggest switching the language/dependency manager rather than retrying the same broken setup 17 times.
