# FoundationAgents/OpenManus

- **DA pass-rate:** 0/0 (pytest_execute=false) | **RAT pass-rate:** 0/25 (pytest_execute=true, 7 failed + 18 errors) | **bucket:** BOTH_FAIL
- **DA build_success/test_success:** false/false | **error_breakdown:** docker build syntax error (DOCKERFILE_MALFORMED)

## Failure stage & category

**stage:** `docker_build`
**category:** `scoring_or_infra_artifact` (Dockerfile generation bug, not test code or dataset issue)

## Root cause (why DA lost)

DA's synthesizer generated a malformed Dockerfile with a broken RUN instruction continuation. Line 18 ends with a backslash (line continuation) followed by `\nRUN uv pip install...` on line 19. Docker interprets this as a single RUN command where `apt-get install` receives the literal string `RUN` as an argument, causing `apt-get`'s argument parser to reject it (`--system is not understood in combination with the other options`). The docker build fails immediately, preventing any test collection or execution.

RAT successfully built a container (via a different mechanism) and ran the test harness inside it, achieving test collection and execution (though with 0/25 pass rate due to FileNotFoundError test errors).

## What RAT did differently

RAT ran commands sequentially inside a working container:
- `pip install --quiet -r /repo/requirements.txt` (installed dependencies from requirements.txt)
- `pip install --quiet -e /repo` (installed the repo package itself in editable mode, critical for `from app.*` imports)
- Multiple additional `pip install` calls with version pinning for specific conflicts
- `run-pytest-collect` (test discovery, succeeded with 25 tests found)
- `run-pytest` (test execution, returned 7 failed + 18 errors, all with FileNotFoundError)

DA never reached these steps due to docker build failure.

## Evidence

**DA Dockerfile (lines 18-19):**
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends git curl \
RUN uv pip install --system -r requirements.txt
```

**Docker build error output:**
```
#9 ERROR: process "/bin/sh -c apt-get update && apt-get install -y --no-install-recommends git curl RUN uv pip install --system -r requirements.txt" did not complete successfully: exit code: 100
#9 1.290 E: Command line option --system is not understood in combination with the other options
```

**DA result markers:**
- `success: false`
- `pytest_execute: false`
- `pytest_collect_success: false`
- `skip_evaluation: true` (from run.log: "No accepted Verification Bundle test commands were found")

**RAT result markers:**
- `success: true`
- `pytest_execute: true`
- `pytest_collect_success: true`
- `pytest_total_tests: 25` (proof of successful collection)
- Test execution showed FileNotFoundError errors in test code, not build/install failures

## Fix recommendation (for src/synthesizer.py / src/recipe_repair.py)

The bug is in `src/synthesizer.py` where RUN instructions are generated. When combining multiple operations into a single RUN line using backslash continuation, the synthesizer must NOT insert a literal `RUN` keyword in the continuation. 

**Current broken pattern:**
```python
# Line construction leaves backslash at line end, next append adds "RUN" prefix
lines.append("RUN apt-get update && ... \\")
lines.append("RUN pip install ...")  # BUG: should be just "pip install" as continuation
```

**Fix:**
```python
# Ensure all RUN instructions are complete, or chain with && instead of backslash
# Option 1: Use && chaining in a single RUN
lines.append("RUN apt-get update && apt-get install -y git curl && uv pip install -r requirements.txt")

# Option 2: If using backslash continuation, do NOT prepend "RUN" to continuation lines
if lines[-1].endswith('\\'):
    lines.append("  uv pip install -r requirements.txt")  # Continuation, not new RUN
else:
    lines.append("RUN uv pip install -r requirements.txt")  # New RUN statement
```

Also verify: the synthesizer should ensure the Dockerfile is built (via docker build) and has a smoke test before returning it as "verified".
