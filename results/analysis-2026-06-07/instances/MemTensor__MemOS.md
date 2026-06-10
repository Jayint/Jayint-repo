# MemTensor/MemOS

- DA pass-rate: 0% (0/0, build failed, eval skipped) | RAT pass-rate: 97.57% (602/621) | bucket: DA_LOSS
- DA build_success/test_success: False/False | error_breakdown: agent crash, malformed Dockerfile, empty Verification Bundle

## Failure stage & category

**Failure stage**: docker_build  
**Failure category**: da_specific

## Root cause (why DA lost)

DockerAgent crashed during Step 2 of synthesis with error "expected string or bytes-like object", preventing any verified setup commands from being extracted. The fallback Dockerfile generator produced syntactically invalid RUN commands (four RUN statements stacked without line continuation backslashes), causing docker build to fail with "Unable to locate package RUN". No Verification Bundle test commands were generated, so the evaluation was skipped entirely, yielding 0% pass rate.

## What RAT did differently

- `pip install -q -r /repo/docker/requirements.txt` — installed base dependencies from project's docker requirements
- `pip install -q -e ".[all]"` — installed repo as editable package with all extras
- `pip install -q -e ".[tree-mem,mem-scheduler,mem-user,mem-reader,pref-mem]"` — installed specific extras (tree-mem, mem-scheduler, mem-user, mem-reader, pref-mem)
- `pip install -q pytest pytest-asyncio pytest-cov pytest-html` — installed test framework and plugins
- Iterative discovery and installation: pytorch (CPU), qdrant-client, volcengine-python-sdk, transformers version pinning (4.51.3+,<4.55.0)

## Evidence

**DA run.log lines 2097-2100** (Dockerfile generation failure):
```
#7 2.068 E: Unable to locate package RUN
#7 2.068 E: Unable to locate package install
------
Dockerfile:18
--------------------
  17 |     # Agent's verified setup instructions
  18 | >>> RUN apt-get update && apt-get install -y \
  19 | >>> RUN pip install --upgrade pip && \
  20 | >>> RUN apt-get update && apt-get install -y \
  21 | >>> RUN chown -R memos:memos /testbed
```

**DA run.log line 2133**:
```
An error occurred during execution: expected string or bytes-like object
```

**DA run.log line 2138**:
```
No accepted Verification Bundle test commands were found; skipping evaluation script generation.
```

**DA _result_row.json**:
- `"status": "error"`, `"failure_reason": "build_failed"`
- `"verified_test_commands": []`, `"verified_runtime_preparation_commands": []`
- `"skip_evaluation": true`

**RAT outer_commands.json** (successful commands executed):
- `pip install -q -r /repo/docker/requirements.txt -> rc 0`
- `pip install -q -e ".[all]" -> rc 0`
- `pip install -q -e ".[tree-mem,mem-scheduler,mem-user,mem-reader,pref-mem]" -> rc 0`
- `pip install -q pytest pytest-asyncio pytest-cov pytest-html -> rc 0`
- `pip install torch --index-url https://download.pytorch.org/whl/cpu -> rc 0`
- `pip install -q qdrant-client volcengine-python-sdk -> rc 0`

## Fix recommendation (for agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Wrap agent execution in try-catch** (src/synthesizer.py or agent.py): If the agent crashes mid-execution with AttributeError/TypeError (e.g., "expected string or bytes-like object"), catch and log the error, then gracefully fall back to parsing the partial logs and state to extract at least some commands.

2. **Validate Dockerfile syntax before docker build** (src/recipe_repair.py or eval harness): Before attempting `docker build`, parse the Dockerfile and check for malformed RUN commands (e.g., backslash-less line continuations). If detected, auto-fix by inserting backslashes at line ends or splitting into separate RUN commands with && chains.

3. **Log agent execution errors explicitly**: The current error "expected string or bytes-like object" is opaque. Add explicit logging to identify which parsing step failed (Step 1 thought parsing? Action extraction? Container state?).

4. **Implement defensive editable install fallback**: If agent generation fails or produces empty Verification Bundle, auto-synthesize a minimal command sequence:
   - `pip install -r docker/requirements.txt` (if present)
   - `pip install -e .` (repo as editable)
   - `pip install pytest` (test framework)
   This minimal baseline would have at least generated some test commands here.
