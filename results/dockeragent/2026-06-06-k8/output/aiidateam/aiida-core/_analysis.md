# Failure Analysis — aiidateam/aiida-core

**Status**: harness success, true outcome: success_tests_all_error (hollow success)  
**Category**: service_dependency_required  
**Pytest**: pass_rate=1.0 (vacuous), total_tests=0, errors={"TimeoutError": 1}

## Root cause

The test suite timed out after 180 seconds before any test results could be reported. The environment is fully installed (all 165 dependencies including test framework, DB fixtures, and message queue support), and test collection succeeded (3738 tests found). However, when pytest attempts to actually execute tests, it hangs indefinitely — most likely because the test suite requires live PostgreSQL and RabbitMQ services (pgtest, pgsu, pg8000, aio-pika, kiwipy[rmq] all present in requirements), which are unavailable in the sandbox. The harness's 180s timeout expired before any test could complete.

## Environment / trajectory state at termination

- **Agent steps**: 11 total; agent terminated with "Finished — Agent has reached a conclusion"
- **Agent step budget**: Not exhausted
- **Installed**: flit_core, aiida-core (with 165 dependencies including pytest, pytest-timeout, pgtest, pytest-asyncio, pytest-xdist, circus, kiwipy[rmq])
- **Missing**: External services (PostgreSQL database, RabbitMQ broker, circus daemon)
- **Last action**: Executed `pip install -e ".[tests]"` successfully in Step 9 (snapshot sha256:a2205). Agent then terminated with verification bundle reporting "pytest --collect-only -q --disable-warnings" as the test command.
- **Test execution**: Attempted via test harness with 180s timeout; resulted in TimeoutError with zero test results

## Key evidence

```
[Step 9] pip install -e ".[tests]" succeeded
[Snapshot Created] sha256:a2205
Successfully installed aiida-core-2.8.0.post0 and 165 packages

[Step 11] Verification Bundle: Accepted 1 test command(s) from agent report
Thought: Test collection succeeded with 3738 tests collected

[Pytest Result] error_breakdown: {"TimeoutError": 1}
Message: Pytest timed out (180s)
returncode: -1
```

## Takeaway for DockerAgent

The agent correctly identified and installed all required Python packages, including test-specific extras ("[tests]" editable install). The problem is **architectural**: this repository's test suite is fundamentally integration-test-heavy, requiring live external services (PostgreSQL + RabbitMQ broker). The agent has no mechanism to:

1. Detect that tests require external services before attempting to run them
2. Spawn or mock those services (e.g., run PostgreSQL in a sidecar, start an RabbitMQ container)
3. Adjust pytest timeout or skip service-dependent tests

The 180s timeout is insufficient for a test suite this large (3738 tests) with heavy fixture setup. The correct fix would require either:
- Service dependency detection + provisioning (plausibly hard)
- Test collection timeout separation from execution timeout
- Test filtering to exclude service-dependent tests

## Fixability

**service_dependency_required** — The repo requires PostgreSQL and RabbitMQ to run integration tests. The harness does not support external service provisioning, and the agent cannot compensate without explicit service spawning capabilities.
