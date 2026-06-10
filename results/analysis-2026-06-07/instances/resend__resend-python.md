# resend/resend-python

- DA pass-rate: 59.91% (257/429 tests passed after eval script fallback) | RAT pass-rate: 100% (429/429) | bucket: DA_LOSS
- DA build_success: False | test_success: False | error_breakdown: OtherError × 172 (tests failed due to missing dependencies)

## Failure stage & category

**Stage:** dependency_install  
**Category:** missing_runtime_or_test_deps

## Root cause (why DA lost)

DA never verified a test command (Verification Bundle was repeatedly rejected), skipping the entire evaluation script. The root cause: DA's Dockerfile ran `pip install -r requirements.txt` but then never ran `pip install -e .` to install the resend package itself in editable mode. When the agent later attempted `pip install -e .` during verification, setup.py failed because it imports the package during the build process, triggering `ModuleNotFoundError: No module named 'typing_extensions'`. This is because `typing_extensions` is a runtime dependency declared in setup.py but was never pre-installed. RAT avoided this by installing `typing_extensions` explicitly before running `pip install -e .`.

## What RAT did differently

RAT executed these commands in sequence (from outer_commands.json):
- `pip install -q requests typing_extensions httpx pytest pytest-asyncio -i https://mirrors.aliyun.com/pypi/simple` — pre-installed `typing_extensions` and core test dependencies
- `pip install -e . -i https://mirrors.aliyun.com/pypi/simple` — installed the resend package in editable mode after deps were ready
- `run-pytest-collect` and `run-pytest` — both collected and ran all 429 tests successfully

DA's Dockerfile never included `pip install -e .` and did not pre-install `typing_extensions`. When the agent later tried to import/install the package, it failed.

## Evidence

**DA run.log markers (lines from grep output):**
- Line 383: `ModuleNotFoundError: No module named 'typing_extensions'` during `pip install -e .` attempt
- Line 5565, 5577, 5589: `[Verification Bundle] Rejected agent-reported bundle because at least one command was not previously observed succeeding in the final environment.`
- Line 5591: `[Warning] Agent repeatedly emitted invalid final Verification Bundles without any previously verified test command.`
- Line 5604: `No accepted Verification Bundle test commands were found; skipping evaluation script generation.`

**DA Dockerfile (from _result_row.json):**
```
RUN pip install -r requirements.txt
RUN pip install tox
RUN pip install setuptools==68.2.2
RUN pip install wheel
```
No `pip install -e .` present. No explicit `typing_extensions` installation.

**RAT outer_commands.json (from direct command extraction):**
```
$ pip install -q requests typing_extensions httpx pytest pytest-asyncio -i https://mirrors.aliyun.com/pypi/simple -> rc 0
$ pip install -e . -i https://mirrors.aliyun.com/pypi/simple -> rc 0
$ run-pytest-collect -> rc 0
$ run-pytest -> rc 0
```

**DA verified_test_commands:** Empty list (no test commands verified before eval fallback)
**RAT test results:** 429 passed, 0 failed

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In src/synthesizer.py:** Ensure `pip install -e .` is always included in the setup RUN commands for Python projects with a setup.py or pyproject.toml, not just `pip install -r requirements.txt`.

2. **Dependency resolution order:** Before installing the package in editable mode, extract and pre-install any direct runtime dependencies that are imported during setup. Consider inspecting setup.py/pyproject.toml for `install_requires` and ensuring those are installed first.

3. **Verification loop hardening:** The agent attempted verification 3 times (emitted 3 rejected bundles) but kept the same recipe. Add a fallback in src/recipe_repair.py to detect this pattern and force a re-synthesis with explicit `pip install -e .` as a mandatory step.

4. **Test command generation:** Ensure the synthesized test commands (e.g., `pytest`) are only claimed as verified after a successful import test (e.g., `python -c "import resend"`) proves the package is actually installed.
