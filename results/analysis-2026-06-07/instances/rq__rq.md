# rq/rq

- **DA pass-rate:** 0.0029 (1/346 tests) | **RAT pass-rate:** 1.0 (556/573 tests) | **bucket:** DA_LOSS
- **DA build_success/test_success:** False / False | **error_breakdown:** 345 ConnectionError (redis daemon not running)

## Failure stage & category

**Stage:** test_execution
**Category:** missing_runtime_or_test_deps

## Root cause (why DA lost)

DA's agent correctly identified that `redis-server --daemonize yes` was required to start the Redis daemon for tests (visible in run.log line 19103 Verification Bundle proposal). However, DA's artifact verification system rejected this command (line 19106) because it was "not previously observed succeeding in the final environment." This rejection caused the runtime_preparation_commands to be auto-finalized as EMPTY (line 19108), so the final Dockerfile never included the `redis-server --daemonize yes` RUN step. Self-verify attempted 3 repair rounds but each one still lacked the daemon start, causing all 345 test collection/execution attempts to fail with "ConnectionError: Error 111 connecting to localhost:6379. Connection refused." (lines 19055-19084).

## What RAT did differently

- RAT explicitly executed `redis-server --daemonize yes` in its container setup (outer_commands.json line: `"$ redis-server --daemonize yes -> rc 0"`) before running pytest.
- This was done as a runtime step in the container, not baked into a Dockerfile, but it ensured the Redis daemon was running.
- DA's Dockerfile (final artifact at _result_row.json) has no equivalent RUN command for starting Redis as a daemon; it only installs redis-server via apt.

## Evidence

- **File:** `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/rq/rq/run.log` lines 19103, 19106, 19108
  - Agent proposed: `"runtime_preparation_commands": ["redis-server --daemonize yes"]`
  - Rejected: `[Verification Bundle] Rejected agent-reported bundle because at least one command was not previously observed succeeding in the final environment.`
  - Auto-finalized empty: `[Verification Bundle] Auto-finalized from previously verified test commands.`

- **File:** `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/rq/rq/_result_row.json`
  - `pytest_pass_rate: 0.0029` (1 pass, 345 errors)
  - `error_breakdown: { "ConnectionError": 345 }`
  - `verified_runtime_preparation_commands: []` (empty)

- **File:** `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/rq/rq/run.log` lines 19055-19084
  - All ConnectionError messages: `"redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. Connection refused."`

- **File:** `/Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/rq/rq/outer_commands.json`
  - RAT command: `"$ redis-server --daemonize yes -> rc 0"` (successful execution)

- **File:** `/Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/rq/rq/_result_row.json`
  - `pytest_pass_rate: 1.0` (556 passed, 0 errors)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Artifact verification rejection too strict:** The verification system in `src/artifact_verify.py` rejected the redis-server command before it was ever tested in a clean container. Modify the rejection logic to:
   - Allow commands proposed by the LLM agent to be tested in the first self-verify round before rejection.
   - Only reject after a self-verify round explicitly tests and fails the command in a clean environment.

2. **Runtime preparation → Dockerfile mapping:** In `src/synthesizer.py`, ensure that `verified_runtime_preparation_commands` are translated into explicit RUN steps in the Dockerfile. For daemon processes like Redis, generate:
   ```dockerfile
   RUN redis-server --daemonize yes && sleep 1 && redis-cli ping
   ```
   This ensures the daemon persists in the image layer.

3. **Self-verify repair loop awareness:** The repair loop gave up after 3 rounds without actually addressing the root cause (missing redis daemon start). Enhance repair prompts in `src/recipe_repair.py` to:
   - Parse ConnectionError messages and identify the service (redis, postgres, etc.) that should be running.
   - Explicitly suggest starting the service daemon in the Dockerfile.
   - Validate that the repair included the daemon start before accepting a solution.

4. **Test-driven verification order:** Prioritize verifying runtime dependencies (services, daemons, environment variables) BEFORE verifying test collection. This surfaced the issue earlier in the self-verify loop.
