# stlehmann/pyads

- **DA pass-rate:** 0% (0/114 tests) | **RAT pass-rate:** 100% (114/114 tests) | **bucket:** DA_LOSS
- **DA build_success/test_success:** True / False | **error_breakdown:** docker build failed (ninja not found)

## Failure stage & category

**Stage:** docker_build  
**Category:** native_system_deps_missing

## Root cause (why DA lost)

DockerAgent instructed the Dockerfile to run `ninja -C /testbed/adslib/build` without first ensuring that `ninja` was installed in the container. The agent ran commands in the sandbox environment where `meson` and `ninja` were installed (via `pip install meson ninja`), but failed to synthesize those system-dependency-installation commands into the final Dockerfile. The Dockerfile only contains `RUN pip install meson ninja` is missing and instead directly invokes `ninja` at line 18, causing a "not found" error during docker build.

## What RAT did differently

RAT explicitly installed both build tools BEFORE running the build:
- **Command 38:** `pip install meson ninja -q -i https://mirrors.aliyun.com/pypi/simple` (rc=0)
- **Command 76:** `apt-get install -y -qq build-essential` (rc=0)
- **Command 71:** `meson setup /repo/adslib/build /repo/adslib` (rc=0)
- **Command 72:** `ninja -C /repo/adslib/build` (rc=0)
- **Command 82:** `cp /repo/adslib/build/libadslib.so /repo/src/adslib.so` (rc=0)

RAT also issued `git submodule update --init --recursive` (cmd 62, 67) and resolved the build directory path issues.

## Evidence

**DA logs (run.log, lines 1011-1026):**
```
#9 [6/8] RUN ninja -C /testbed/adslib/build
#9 0.373 /bin/sh: 1: ninja: not found
#9 ERROR: process "/bin/sh -c ninja -C /testbed/adslib/build" did not complete successfully: exit code 127
```

**DA Dockerfile generated (stlehmann__pyads.json):**
The Dockerfile includes:
```
RUN ninja -C /testbed/adslib/build
RUN JAYINT_PIP_ATTEMPT=1; ... pip install -e .[tests] ...
```

Note: The `pip install meson ninja` command is mentioned in the agent's reasoning (run.log, line 574: "pip install meson ninja") but was NEVER synthesized into the verified_runtime_preparation_commands or added to the Dockerfile. The agent ran it in sandbox but failed to carry it forward to the final recipe.

**Agent reasoning (run.log, line 474-477):**
```
The `adslib/` submodule is not initialized (empty directory). I need to fetch the submodule and install build tools (meson, ninja) and Python dependencies.
git submodule update --init --recursive && pip install meson ninja 2>&1
```

The agent recognized the need but only reported:
- `verified_runtime_preparation_commands: []` (empty!)
- `verified_test_commands: ['pytest --collect-only -q --disable-warnings']`

The critical pre-test setup steps (submodule init, meson/ninja install, libadslib.so build, copy to src/) were never synthesized into the final artifact.

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In agent.py:** When the agent identifies native build tools (meson, ninja, gcc, etc.) as required dependencies, explicitly capture these in the synthesized setup commands. The agent's reasoning mentions `pip install meson ninja` but this is lost in the final report.

2. **In src/synthesizer.py:** Ensure that all commands executed during the sandbox session that are necessary for the build (submodule init, system package installs, pre-build compilation steps) are carried forward to `verified_runtime_preparation_commands`. Currently, only pytest-specific commands populate this field.

3. **In src/recipe_repair.py:** Add validation that all tools referenced in the Dockerfile (ninja, meson, gcc, etc.) are explicitly installed before they are invoked. A self-verification round should catch the missing ninja install and trigger a repair loop to synthesize a `RUN pip install meson ninja && apt-get install -y build-essential` command.

4. **Ensure submodule-aware build:** When .gitmodules exists, automatically synthesize `git submodule update --init --recursive` as a pre-build step, and validate that cloned submodules contain expected files (e.g., /repo/adslib/meson.build).
