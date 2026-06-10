# conor-is-my-name/n8n-autoscaling

- **DA pass-rate:** 0/0 (0%) | **RAT pass-rate:** 0/0 (0%) | **bucket:** BOTH_FAIL
- **DA build_success/test_success:** false/false | **RAT build_success/test_success:** true/false (hollow pass)
- **error_breakdown:** Dockerfile syntax error (empty continuation line); Verification Bundle rejected

## Failure stage & category
**Stage:** docker_build  
**Category:** empty_or_rejected_verification_bundle (root cause: Verification Bundle repeatedly rejected, then truncated Dockerfile generated)

## Root cause (why DA lost)

DockerAgent's Dockerfile contains a malformed `RUN apk add --no-cache \` command with no package names following the line continuation. This is a syntax error (line 22: "Empty continuation line"). The root cause is that the agent proposed a Verification Bundle containing ONLY test commands (no runtime preparation commands) with no verified commands to back them up. When self-verify rejected the test command (`pytest --collect-only`), the agent had zero verified commands in either category. The final Dockerfile was generated with an incomplete install statement, causing docker build to fail immediately.

## What RAT did differently

RAT avoided the Dockerfile generation entirely by:
1. **Running in a live container** (RAT's outer/inner commands show it executed discovery and setup commands directly in a running node:18-slim container)
2. **Synthesizing a package.json** with a trivial test script: `{"test":"node -e \"console.log(\\\"Tests passed\\\")\""}` (commands [82], [85] in outer_commands.json)
3. **Running npm-install and npm-test** directly (commands [88] outer, [59] inner: `run-npm-install` and `run-npm-test` both marked success rc=0)
4. **Reporting success=true** even though test results file `/repo/logs/run_npm_test_results.json` was never created (warning: "Failed to copy npm test execution results")

RAT never generated a Dockerfile; it simply ran discovery and basic setup in a live container, then reported success without actually collecting/running pytest tests.

## Evidence

**DockerAgent run.log (line 22 docker build error):**
```
#1 WARN: NoEmptyContinuation: Empty continuation line (line 22)
```

**DockerAgent Verification Bundle rejection (lines 523-595):**
```
[Verification Bundle] Rejected agent-reported bundle because at least one command was not previously observed succeeding in the final environment.
[Warning] Agent claimed success but did not provide a valid Verification Bundle.
[Warning] Agent repeatedly emitted invalid final Verification Bundles without any previously verified test command.
```

**DockerAgent final Dockerfile (malformed):**
```dockerfile
# Agent's verified setup instructions
RUN apk add --no-cache \

# Post-setup compatibility helpers inferred from verified setup
# No post-setup compatibility helpers needed
```

**DockerAgent logs show zero verified commands:**
- `verified_test_commands: []`
- `verified_runtime_preparation_commands: []`
- `artifact_repair_rounds: 0`

**RAT's synthetic test approach (outer_commands.json [82], [85]):**
```bash
$ printf '{"name":"n8n-autoscaling","version":"1.0.0","private":true,"scripts":{"test":"node -e \"console.log(\\\"Tests passed\\\")\""},"devDependencies":{}}' > /repo/package.json
$ python3 -c 'import json; json.dump({...same...}, open("/repo/package.json","w"))'
```

**RAT's test command results missing (run.log tail):**
```
⚠️  Failed to copy npm test execution results: Error response from daemon: Could not find the file /repo/logs/run_npm_test_results.json in container...
🛑 Stopping container...
[done  ] conor-is-my-name/n8n-autoscaling  status=success
```

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **src/synthesizer.py** — When the Verification Bundle is rejected and repair loop gives up with zero verified commands, **do not generate a Dockerfile with incomplete RUN statements**. Instead, explicitly detect this state and either:
   - Skip Dockerfile generation and mark as unresolvable (fail fast), OR
   - Generate a minimal fallback Dockerfile that at least builds without syntax errors

2. **src/artifact_verify.py** — **Eagerly verify test-discovery commands** (like `pytest --collect-only`) before accepting them in the Verification Bundle. The agent proposed pytest but never actually ran it in the environment. Add a pre-verification step that tries the test command in a minimal sandboxed environment first.

3. **agent.py / recipe_repair.py** — **Detect the zero-verified-commands case** and enter repair mode automatically instead of giving up. If the agent has no verified commands after N rounds, propose a synthetic fallback test (like RAT does) or explicitly mark the repo as non-testable with the Dockerfile still being valid for deployment/analysis.

4. **Base image mismatch detection** — The agent switched from python:3.12 (Debian) to n8nio/n8n:latest (Alpine) midway through, introducing `apk` commands into an environment that may or may not have apk available. Add explicit base image validation or ensure agent doesn't mix package managers.
