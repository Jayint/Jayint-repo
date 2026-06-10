# Nitrokey/pynitrokey

- DA pass-rate: 0% (0/1 collected) | RAT pass-rate: 0% (0/190 collected) | bucket: BOTH_FAIL
- DA build_success/test_success: True / False | error_breakdown: ModuleNotFoundError (nitrokey not found)

## Failure stage & category

**Stage:** test_execution
**Category:** missing_project_self_install

## Root cause (why DA lost)

DA's synthesizer verified that the test command should be `poetry run pytest`, but never actually invoked `poetry install` or any equivalent to populate the poetry environment. The Dockerfile only installs poetry as a binary, installs system dependencies, but skips the critical step of installing the repository's own package and its declared dependencies. When the test collection runs, the `nitrokey` module (the package being tested) is not installed, causing ModuleNotFoundError. Self-verify attempted repair across 3 rounds but gave up (`status=unresolved`) after discovering missing dependencies (nitrokey, click, tqdm) that should have been installed during build.

## What RAT did differently

- RAT explicitly **extracted dependencies from `pyproject.toml`** into `/tmp/requirements.txt` using multi-attempt Python parsing
- RAT ran **`pip install -q -r /tmp/requirements.txt`** to install all declared dependencies (cffi, click, cryptography, fido2, hidapi, intelhex, libusb1, nethsm, nitrokey)
- RAT ran **`pip install -e .`** (editable install) to install the pynitrokey package itself into the environment
- RAT ran `pip install pytest` explicitly before test collection
- RAT successfully collected 190 tests (vs DA's 1 test with error)

## Evidence

**DA's Dockerfile** (verified_setup from logs):
```dockerfile
RUN pip3 install poetry  # ← installs poetry binary only
RUN apt-get install -y libudev-dev libhidapi-dev gcc python3-dev  # ← system deps only
# [MISSING: poetry install, pip install -e ., pip install -r requirements.txt]
```

**DA's verified commands** (from logs):
```
verified_test_commands: ['cd /app && poetry run pytest --collect-only -q --disable-warnings']
verified_runtime_preparation_commands: []  # ← EMPTY: no package installation
```

**DA's self-verify failure trace** (from run.log, lines 1436-1445):
```
[Self-Verify] Round 0: tests did not execute (collection_or_env_error); missing=['nitrokey'].
[Self-Verify] Round 1: tests did not execute (collection_or_env_error); missing=['click'].
[Self-Verify] Round 2: tests did not execute (collection_or_env_error); missing=['tqdm'].
[Self-Verify] status=unresolved; keeping original recipe.
```

**RAT's equivalent commands** (from outer_commands.json):
- cmd 38: `pip install -q -r /tmp/requirements.txt -i https://mirrors.aliyun.com/pypi/simple` → rc 0
- cmd 40: `pip install -e . -i https://mirrors.aliyun.com/pypi/simple` → rc 0
- cmd 46: `run-pytest` → rc 0 (collected 190 tests successfully)

**Pytest execution comparison:**
- DA: 1 test collected with 1 error (ModuleNotFoundError: No module named 'nitrokey')
- RAT: 190 tests collected successfully, 0 errors during collection

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

**In `src/synthesizer.py`:** When detecting a Python project with `pyproject.toml`, the synthesizer must:

1. **Always extract and install project dependencies** before running tests. For poetry-based projects:
   - Parse `dependencies = [...]` from `pyproject.toml` (RAT's pattern using Python regex is robust)
   - Run `pip install -r requirements.txt` with all extracted dependencies
   - OR run `poetry install` followed by commands prefixed with `poetry run`

2. **Always install the package itself** via `pip install -e .` after dependencies are installed. This is critical for test discovery and imports.

3. **Verify that verified_runtime_preparation_commands is non-empty** when a test command references package imports or dependency modules. An empty list after `poetry install poetry` and system deps should trigger a warning or repair attempt.

4. **Improve deterministic repair logic** in `src/recipe_repair.py`:
   - When missing modules are detected during self-verify, append `pip install -e .` to the build phase, not just patch individual missing packages
   - For poetry-based projects specifically, ensure `poetry install` is called before `poetry run` commands
   - Track whether a "package self-install" command exists; if not, add it before retrying

5. **Align verified_runtime_prep with verified_test_commands:** If a test command references package modules (e.g., `import nitrokey`), ensure that at least `pip install -e .` is in the verified_runtime_preparation_commands list, or it's baked into the Dockerfile.
