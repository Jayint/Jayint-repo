# Failure Analysis — copier-org/copier

**Harness status:** success  
**True outcome:** success_tests_all_error  
**Pytest:** pass_rate=0, total_tests=0, error_breakdown={"ModuleNotFoundError": 1}  

## Root cause

The synthesizer's build-recipe generation excluded `pip install --group dev .` from the Dockerfile, incorrectly classifying it as "alternative approach not in the successful trajectory." In reality, this command is essential: it installs test dependencies like `coverage`, which conftest.py imports. The agent verified its environment in the sandbox AFTER installing dev dependencies, so the tests appeared to collect successfully in step 6. But the synthesized Dockerfile only includes `pip install --upgrade "pip>=25.1"`, causing the eval image to fail immediately when pytest tries to load conftest.

## Environment / trajectory state at termination

- Agent steps: 7 steps ran
- Installed: pip upgraded, copier cloned, only pip itself upgraded via RUN command
- Missing: coverage, pytest-cov, and all dev dependencies that were installed in the sandbox during step 5 but not captured in the build recipe
- Last failing action: pytest collection in eval image fails at conftest load with `ModuleNotFoundError: No module named 'coverage'`
- Build status: Docker image built successfully (no synthesis error); test collection failed due to missing import

## Key evidence

From agent_run_summary.json (build_recipe.excluded_commands):
```json
{
  "command": "pip install --group dev .",
  "reason": "alternative approach not in the successful trajectory that led to final verification; not needed for build"
}
```

From eval pytest output:
```
ImportError while loading conftest '/testbed/tests/conftest.py'.
tests/conftest.py:12: in <module>
    from coverage.tracer import CTracer
E   ModuleNotFoundError: No module named 'coverage'
```

From eval_build/Dockerfile (only 22 lines, missing editable install):
```dockerfile
RUN JAYINT_PIP_ATTEMPT=1; ... pip install --upgrade "pip>=25.1"' ...
RUN pip install --no-cache-dir pytest
```

## Takeaway for DockerAgent

The synthesizer incorrectly filtered out `pip install --group dev .` because it labeled it as "redundant" or "alternative," but it was actually the only successful dev-dependency install in the agent's trajectory. The agent verified in a sandbox where this command had already executed, creating a false sense of completeness. The planner must preserve state-changing commands that are necessary for the final verification environment, especially when the final test command depends on test-only dependencies. If dev/test dependencies are installed in the agent's workspace, they must be captured in the build recipe so the eval image is identical.

## Fixability

**trivial_synthesizer_fix** — The synthesizer's exclusion logic needs to preserve build commands that install dependencies required by test imports (coverage, pytest plugins, conftest fixtures). Adding a check: "if a command installs a package referenced in test imports (e.g., conftest.py imports), include it in build_recipe" would fix this. Alternatively, using `pip install -e ".[dev]"` or a .txt file with all dev deps would avoid the need to filter by heuristic.
