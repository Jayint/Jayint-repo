# feast-dev/feast

- DA pass-rate: 0% | RAT pass-rate: 0% | bucket: BOTH_FAIL
- DA build_success/test_success: False/False | error_breakdown: {}
- RAT pytest_executed: true, but 0 passed (5 errors + 2 failures on 7 collected tests)

## Failure stage & category
- DA: `docker_build` / `native_system_deps_missing`
- RAT: `test_execution` / `missing_runtime_or_test_deps`

## Root cause (why DA lost)

**DockerAgent failed to generate a Dockerfile due to an infrastructure error (disk space)**, not a dependency discovery issue. After correctly diagnosing the missing `grpc` module (part of `feast[grpcio]`), the agent attempted to install the missing extra via `pip install "feast[grpcio]"` but crashed mid-installation with "no space left on device" (line 1031 of run.log). This prevented DA from ever outputting a Dockerfile artifact, triggering an immediate `no_dockerfile` failure.

In contrast, RAT was able to generate an executable environment (a container with Python + test framework), though it failed to install all optional test dependencies (`minio`, full `grpcio` suite) and thus could not successfully run any tests. RAT's environment at least allowed pytest collection and partial test execution; DA's environment collapsed before artifact generation.

## What RAT did differently

- RAT successfully installed the base package: `pip install -e "sdk/python[test]"` (rc=0, line 826 in run.log)
- RAT executed pytest collection and test runs despite missing some optional deps
- RAT's Dockerfile-equivalent environment remained intact throughout; no OOM crash
- RAT did not explicitly install `feast[grpcio]`, but the test execution revealed missing `minio` (ModuleNotFoundError in junit_report.xml)
- RAT ran actual test collection and execution (7 tests collected, 2 failures + 5 errors)

DA attempted the same install flow but:
- Installed `pip install -e "/app[test]"` successfully (step 10, line 581)
- Discovered missing `grpc` during collection (step 15, line 985: "ModuleNotFoundError: No module named 'grpc'")
- Tried to add the missing extra: `pip install "feast[grpcio]"` (step 17, line 1027)
- **Crashed with OOM before completion** (line 1031)

## Evidence

**DA failure marker (run.log line 1031):**
```
An error occurred during execution: 500 Server Error... "failed to export layer: ... no space left on device"
```

**DA result (from _result_row.json):**
```json
"status": "error",
"failure_reason": "no_dockerfile",
"error": "agent produced no Dockerfile: Dockerfile generation failed"
```

**RAT successful install + test collection (run.log lines 808-813, 826-840):**
```
cd /repo && pip install -q -e "sdk/python[test]" -i https://mirrors.aliyun.com/pypi/simple --timeout 120
Running `pip install ...` executes with returncode: 0
```

**RAT test results (junit_report.xml):**
- 7 tests collected
- ModuleNotFoundError: No module named 'minio' (conftest.py import chain)
- ModuleNotFoundError: No module named 'example_repo' (RAG example)
- 2 failures (FileNotFoundError, UnboundLocalError in test templates)
- 5 errors (missing fixtures, missing modules)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Immediate: Monitor container disk space during large pip installs.** Add a pre-install check for available disk space and fail gracefully if <2GB remains. Consider using `pip install --no-cache-dir` to reduce cache bloat mid-installation.

2. **Optional extras handling:** When test collection fails due to ModuleNotFoundError for optional extras (e.g., `grpcio`, `minio`), implement a discovery phase that parses pyproject.toml `[project.optional-dependencies]` and installs likely test extras (`[test]`, `[grpc]`, `[cloud]`, etc.) instead of reactive single-package fixes. This avoids cascading install failures.

3. **Dockerfile generation resilience:** Ensure the synthesizer generates a valid Dockerfile *before* attempting to run the container. If the container crashes mid-setup, the Dockerfile should already be saved and available for fallback or inspection. Currently, DA's Dockerfile is only generated *after* successful test collection, leaving no artifact if setup fails.

4. **Error classification:** Distinguish between transient OOM/disk errors (retry-worthy) and permanent dependency issues (not retry-worthy). The current "no_dockerfile" bucket masks the underlying disk space problem.
