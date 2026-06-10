# copier-org/copier

- DA pass-rate: 0.0% (0/0 tests executed) | RAT pass-rate: 99.55% (1098/1113 tests)
- DA build_success/test_success: true/false | error_breakdown: ModuleNotFoundError(plumbum, pydantic, dunamai) in self-verify loop

## Failure stage & category
test_execution / missing_project_self_install

## Root cause (why DA lost)

DA's synthesizer failed to include `pip install -e .` in the Dockerfile, causing the copier package and its runtime dependencies to never be installed in the test environment. During self-verify, the test collection succeeded locally (1113 tests collected) but when running in the clean-room image, tests failed immediately with `ModuleNotFoundError: No module named 'plumbum'` (and subsequently pydantic, dunamai). The self-verify loop detected the missing imports, attempted repair 2 times, but gave up with status=unresolved. The original broken recipe was kept, resulting in 0 tests collected in the final evaluation.

## What RAT did differently

- `pip install -q -e "/repo"` — installed the package itself in editable mode from /repo
- `pip install -q pexpect plumbum funcy pathspec pygments jinja2 jinja2-ansible-filters packaging pydantic pyyaml questionary colorama dunamai platformdirs typing-extensions` — explicitly installed all copier's declared runtime dependencies (colorama, dunamai, funcy, jinja2, jinja2-ansible-filters, packaging, pathspec, plumbum, pydantic, pygments, pyyaml, questionary, platformdirs, typing-extensions) that DA omitted

## Evidence

- **DA Dockerfile**: Contains only test deps (pytest, pytest-cov, pytest-gitconfig, pytest-xdist) and dev deps (codespell, commitizen, mypy, ruff, etc.) but NO `pip install -e /testbed` and NO explicit runtime deps.
- **DA self-verify log** (run.log:2169-2178):
  - Round 0: `[Self-Verify] Round 0: tests did not execute (tests_did_not_execute); missing=['plumbum']`
  - Round 1: `[Self-Verify] Round 1: tests did not execute (tests_did_not_execute); missing=['pydantic']`
  - Round 2: `[Self-Verify] Round 2: tests did not execute (tests_did_not_execute); missing=['dunamai']`
  - Final: `[Self-Verify] status=unresolved; keeping original recipe`
- **RAT outer_commands.json**: Explicit `pip install -q -e "/repo"` followed by explicit runtime deps install (all 13 packages from copier's pyproject.toml dependencies list)
- **copier's pyproject.toml**: Declares 13 runtime dependencies (colorama, dunamai, funcy, jinja2, jinja2-ansible-filters, packaging, pathspec, plumbum, pydantic, pygments, pyyaml, questionary, platformdirs); these are critical for the copier package to function, not optional dev tools

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

The synthesizer must ALWAYS include `pip install -e .` (or `pip install -e /repo` for the Docker context) to install the project package and transitively pull in its declared runtime dependencies from pyproject.toml. Currently, the synthesizer appears to only extract and install test/dev dependencies from optional groups (`[dependency-groups]`), completely missing the mandatory `[project] dependencies = [...]` section. Update `src/synthesizer.py` to:
1. Parse pyproject.toml's `[project] dependencies` section (not just optional groups)
2. Either install these explicitly with `pip install <dep1> <dep2> ...` OR rely on `pip install -e .` to pull them transitively (the latter is simpler and more correct)
3. Ensure `pip install -e .` is ALWAYS present as the first step after cloning, before running pytest collection or tests
