# Failure Analysis — Yelp/dumb-init

**Status:** error (build_failed) | **True Outcome:** build_failed | **Category:** dockerfile_missing_setup_step | **Tests:** 0 collected (build failed before pytest)

## Root cause

The synthesized Dockerfile attempts to run `pip3 install` (lines 19–20) without first installing Python/pip3. The base image `buildpack-deps:jammy` does not include Python by default. The agent successfully used Python in its sandbox environment (likely inherited from the host), but failed to emit an explicit `apt-get install python3 python3-pip` RUN command before invoking pip. When docker build evaluates the Dockerfile, `pip3` is not found (exit code 127), causing the build to fail.

## Environment / trajectory state at termination

- **Steps executed:** 19 agent steps (full ReAct loop completed)
- **Sandbox result:** Agent successfully ran `make build` and verified pytest collection (182 tests), generating a test command
- **Dockerfile synthesis:** Produced 4 build commands, but missing Python bootstrap
- **Eval build outcome:** Failed on step 7/10 (RUN pip3 install -r requirements-dev.txt) with `/bin/sh: 1: pip3: not found`
- **Tests run:** None (eval build failed before pytest could run)

## Key evidence

```
#9 [ 6/10] RUN make build
#9 0.263 cc -std=gnu99 -static -s -Wall -Werror -O3 -o dumb-init dumb-init.c
#9 DONE 0.5s

#10 [ 7/10] RUN JAYINT_PIP_ATTEMPT=1; ... do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip3 install -r requirements-dev.txt' ...
#10 0.266 /bin/sh: 1: pip3: not found
#10 5.291 /bin/sh: 1: pip3: not found
#10 10.31 /bin/sh: 1: pip3: not found
#10 ERROR: process ... exit code: 127
```

The Dockerfile (lines 18–20 of eval_build/Dockerfile):
```
RUN make build
RUN JAYINT_PIP_ATTEMPT=1; ... /bin/sh -lc 'pip3 install -r requirements-dev.txt' ...
RUN JAYINT_PIP_ATTEMPT=1; ... /bin/sh -lc 'pip3 install -e .' ...
```

No `RUN apt-get install -y python3 python3-pip` or equivalent before invoking pip3.

## Takeaway for DockerAgent

When the synthesizer converts sandbox commands to Dockerfile RUN instructions, it must:
1. Track all system commands executed (e.g., `pip3`, `make`, `python`) and their implicit dependencies
2. Explicitly emit `apt-get install` (or equivalent) for any system package referenced in a RUN instruction that is not guaranteed by the base image
3. For `buildpack-deps:jammy`, verify that Python/pip3 are included; if not, add an install step before the first pip invocation
4. Do not assume the sandbox environment's state carries over to the eval image without explicit RUN instructions

## Fixability

**planner_strategy_fix** — The agent's ReAct loop worked correctly and collected all tests. The problem is a gap in the Dockerfile synthesis logic: missing system dependency installation (Python) before pip invocation. Fixing the synthesizer's dependency tracking and insertion logic will resolve this class of failures.
