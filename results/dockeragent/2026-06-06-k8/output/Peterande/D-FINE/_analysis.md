# Failure Analysis — Peterande/D-FINE

**Harness status:** success | **True outcome:** success_tests_all_error | **Category:** test_deps_not_installed | **Pytest:** 1 error (ModuleNotFoundError), 0 tests collected

## Root cause

The repository's core package (`src/`) imports `src.data.dataset.coco_dataset`, which requires `faster_coco_eval`, a runtime dependency that the agent never installed. The test file `src/nn/backbone/test_resnet.py` exists but cannot be imported/collected because the package import chain fails upstream. The agent ran 30 steps but produced zero setup instructions and mistakenly declared success based on "pytest --collect-only" collecting 0 tests—failing to diagnose that the lack of test discovery was due to an import error, not absence of tests.

## Environment / trajectory state at termination

- **Steps used:** 30 (full budget or agent surrender)
- **Agent setup instructions produced:** 0
- **Installed:** git, apt retries/timeouts (bootstrap only)
- **Missing:** `faster_coco_eval`, all Python dependencies from the repo's dependency specification (requirements.txt, setup.py, or pyproject.toml were never inspected or acted upon)
- **Last failing action:** Steps 28–30 repeatedly ran `pytest --collect-only -q --disable-warnings`, which reported "no tests collected" due to the import error, but the agent misinterpreted this as success (repo has no tests) rather than a collection failure

## Key evidence

```
src/data/dataset/coco_dataset.py:8: in <module>
    import faster_coco_eval.core.mask as coco_mask
E   ModuleNotFoundError: No module named 'faster_coco_eval'

--- and ---

Agent Summary: "agent_steps in summary: 0"
Dockerfile: "# Agent's verified setup instructions\n# No additional setup instructions from agent"

--- and ---

Step 30: "All dependencies are installed and core module imports succeed."
[Verification Bundle] Rejected ... because at least one command was not previously observed succeeding
[Warning] Agent repeatedly emitted invalid final Verification Bundles without any previously verified test command.
```

## Takeaway for DockerAgent

1. **Distinguish "0 tests collected" from "uncollectable tests":** The agent must run an actual import test (e.g., `python -c "import src; import src.data; import src.data.dataset.coco_dataset"`) to detect upstream import failures before concluding success. A pytest `--collect-only` that yields 0 items + 1 error is a failure state, not success.

2. **Inspect and act on dependency declarations:** The agent should parse and install the repo's `requirements.txt`, `setup.py`, `pyproject.toml`, or `environment.yml` before attempting any test discovery. Handcrafting setup without consulting the repo's own dependency metadata results in incomplete environments.

3. **Do not accept verification bundles unless they have been verified:** The agent correctly rejected its own unverified bundles in steps 28–30, but then continued looping instead of escalating or switching strategy. When bundles are repeatedly rejected, the agent should investigate the failure reason (import error) rather than re-submitting the same bundle.

4. **Diagnose import errors early:** When `pytest --collect-only` fails with ModuleNotFoundError, immediately escalate to tracing the import chain and installing the missing package, rather than re-attempting the same failing command.

## Fixability

**`test_deps_not_installed`** — The repository has a standard dependency specification (inferred from the import of `faster_coco_eval`), and the agent simply failed to discover and install it. Adding logic to parse and install dependencies from the repo's standard dependency files (requirements.txt, setup.py, pyproject.toml) and to diagnose import errors (not just pytest collection failures) would resolve this. This is a **planner_strategy_fix**: the agent needs to follow a discovery → install → verify workflow for Python packages, rather than skipping straight to test collection.
