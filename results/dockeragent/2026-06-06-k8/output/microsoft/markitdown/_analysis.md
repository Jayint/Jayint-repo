# Failure Analysis — microsoft/markitdown

**Status**: error | **True Outcome**: build_failed | **Category**: dockerfile_synthesis_malformed | **Test Result**: No pytest executed (build failed)

## Root cause

The synthesizer extracted incomplete and syntactically malformed RUN commands from the agent's sandbox logs. Lines 18-21 of the eval_build Dockerfile contain fragmented shell commands that were never fully verified:
- Line 18: `RUN apt-get update && apt-get install -y --no-install-recommends \` (incomplete, missing package list)
- Line 19: `RUN if [ "$INSTALL_GIT" = "true" ]; then \` (invalid: RUN keyword at statement start; should be continuation of line 18 or single chained command)
- Line 20: `RUN rm -rf /var/lib/apt/lists/*` (orphaned RUN)
- Line 21: `RUN pip --no-cache-dir install \` (incomplete, no packages specified)

These fragments do not correspond to any successfully executed command in the sandbox. The agent internally used complete commands (e.g., `pip install --no-cache-dir -e "packages/markitdown[all]" ...`), but the synthesizer captured partial/intermediate state during extraction.

## Environment / trajectory state at termination

**Agent steps used**: 11 (full reachability, no step budget exhaustion)

**Installed in sandbox**:
- System: ffmpeg, exiftool, libxcb-cursor0, git, build essentials
- Python: markitdown[all] with all extras, markitdown-sample-plugin, pytest, openai
- Pytest collection succeeded with 336 tests found

**Missing/broken at termination**:
- Valid Verification Bundle: Agent reported success 3 times (steps 9, 10, 11) but each was rejected because the reported test command was never previously verified in the sandbox.
- The agent never generated a valid Dockerfile extraction; the synthesizer fell back to reconstructing from partial execution traces, producing malformed RUN lines.

**Last failing action**: Docker build of eval_build/Dockerfile failed with shell syntax error:
```
/bin/sh: 1: Syntax error: "then" unexpected
```
This occurred on line 18-20 where RUN statements were incorrectly merged.

## Key evidence

From eval_build/Dockerfile lines 18-21:
```
RUN apt-get update && apt-get install -y --no-install-recommends \
RUN if [ "$INSTALL_GIT" = "true" ]; then \
RUN rm -rf /var/lib/apt/lists/*
RUN pip --no-cache-dir install \
```

From run.log error (line 2344):
```
#9 ERROR: process "/bin/sh -c apt-get update && apt-get install -y --no-install-recommends RUN if [ \"$INSTALL_GIT\" = \"true\" ]; then RUN rm -rf /var/lib/apt/lists/*" did not complete successfully: exit code: 2
```

From run.log (lines 2405-2406):
```
==================== Environment Configuration FAILED ====================
[Warning] Configuration did not complete successfully. No Dockerfile will be generated.
```

## Takeaway for DockerAgent

The synthesizer's Dockerfile extraction logic failed to handle the case where:
1. Agent claimed success but did not provide a verifiable Verification Bundle
2. Harness rejected all three final bundles as unverified
3. Harness fell back to extracting Dockerfile from sandbox history/snapshots

The fallback extraction produced incomplete RUN commands by slicing multi-line command sequences at incorrect boundaries. The synthesizer code generator must validate that each RUN line is complete and syntactically valid before writing it to the Dockerfile, or more fundamentally, the agent should have successfully produced a valid Verification Bundle before terminating (i.e., the agent should have re-run the verified commands in a final step to ensure they are reproducible).

Additionally, the agent's repeated claim of success despite invalid Verification Bundles suggests the planner or observation loop was not effectively enforcing the requirement that "Final Answer: Success" must be backed by a previously verified test command.

## Fixability

**trivial_synthesizer_fix** — The Dockerfile fragment extraction is a known fallback path when Verification Bundles are missing or invalid. The fix is: (1) validate extracted RUN statements for syntactic completeness (multi-line RUNs must have matching backslash continuations), (2) do not include incomplete lines (lines ending with `\` without a continuation), or (3) prevent this fallback entirely by forcing the agent to produce a valid Verification Bundle or timeout before considering the run successful.
