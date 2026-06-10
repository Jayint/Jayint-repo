# gip-inclusion/les-emplois

- DA pass-rate: 0.0 (0/0 tests) | RAT pass-rate: 0.0 (0/5640 tests) | bucket: BOTH_FAIL
- DA build_success/test_success: false/false | error_breakdown: "no_dockerfile" (Dockerfile generation failed)

## Failure stage & category
- **DA failure stage:** project_self_install (environment setup failed, no Dockerfile produced)
- **DA failure category:** docker_build_failed
- **RAT failure stage:** test_execution (environment built, tests collected but all errored on setup)
- **RAT failure category:** other (dataset-hard: 5639 tests all errored with OtherError during pytest setup)

## Root cause (why DA lost vs RAT)

**DockerAgent failed at the agent loop stage** — the agent's LLM response generator is emitting malformed HTML artifacts (`</th>`, `</tr>`, `</table>` tags) appended to shell commands, causing repeated sandbox rejections. Additionally, the agent tried chaining independent setup mutations together (e.g., `pg_lsclusters && pg_ctlcluster 17 main start`), which the sandbox explicitly rejects with "COMMAND REJECTED BEFORE EXECUTION: this Action combines multiple independent setup mutations." After ~30 steps, the agent gave up with "Environment Configuration FAILED" and no Dockerfile was generated.

**RAT succeeded at environment setup and pytest collection** but all 5640 collected tests errored during pytest setup with database/extension initialization failures (`CREATE EXTENSION IF NOT EXISTS postgis` via PostGIS setup). This is a dataset-hard issue (not RAT-specific) — the environment was correctly configured (PostgreSQL, Redis started, credentials set), but the Django test setup itself failed on all tests.

## What RAT did differently

- Ran individual setup commands separately (not chained): `apt-get update && apt-get install ...` as separate steps
- Installed system dependencies: `apt-get install -y binutils build-essential libproj-dev gdal-bin libpq-dev`
- Installed Python dependencies: `pip install -q -r /repo/requirements/test.txt`
- Set up PostgreSQL service: `apt-get install postgresql postgresql-client`, created user/database
- Installed PostGIS extension: `apt-get install postgresql-17-postgis-3` + `CREATE EXTENSION postgis`
- Started Redis service: `apt-get install redis-server` + `service redis-server start`
- Exported environment variables: `PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=itou PGUSER=postgres PGPASSWORD=password REDIS_URL=redis://127.0.0.1:6379 DJANGO_SETTINGS_MODULE=config.settings.test`
- Ran `run-pytest` wrapper command (which executed `python -m pytest`)

## Evidence

- **DA run.log line 62-95:** Agent emits `cat "pyproject.toml</th>` and similar malformed commands; sandbox rejects with "COMMAND REJECTED BEFORE EXECUTION"
- **DA run.log line 475-488:** Agent chains `apt-get update && apt-get install ...` together; sandbox rejects "multiple independent setup mutations"
- **DA run.log line 1545:** Final failure marker: "Environment Configuration FAILED" → "No Dockerfile will be generated"
- **DA _result_row.json:** `"status": "error"`, `"failure_reason": "no_dockerfile"`, `"error": "agent produced no Dockerfile: Dockerfile generation failed"`
- **RAT _result_row.json:** `"status": "success"`, `"success": true`, `"pytest_collect_success": true`, `"pytest_total_tests": 5640`, `"pytest_pass_rate": 0.0` (all tests errored, not because environment failed but because test setup itself failed)
- **RAT outer_commands.json:** Shows individual commands executed separately; all return rc 0 up through `run-pytest-collect` and first `run-pytest`

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Agent response sanitization** (in `agent.py` or LLM output handler): Strip HTML artifacts from LLM action outputs before sending to sandbox. Implement regex to remove `</th>`, `</tr>`, `</table>`, etc. that may leak from HTML-formatted prompts.

2. **Command chaining validation** (in `src/synthesizer.py` or sandbox interface): Detect when the agent tries to chain mutations (AND/OR/semicolon operators combining state-changing commands) and either:
   - Auto-split into individual commands before sandbox execution, OR
   - Add explicit guidance to the agent prompt: "Run each setup mutation, verification, or probe as a separate Action"

3. **Agent early termination fallback** (in `agent.py`): If the agent fails N consecutive steps with "COMMAND REJECTED" or identical error patterns, auto-generate a Dockerfile template from observed repo structure (pyproject.toml, requirements/*.txt, Makefile, etc.) rather than giving up entirely. This would at least allow some test execution attempt.

4. **Repository analysis pre-flight** (in `src/synthesizer.py`): Before agent loop, scan for common dependency files and pre-populate a base Dockerfile with standard layers (system deps, Python deps, service setup), then let agent refine. For this repo specifically: detect `requirements/test.txt`, PostGIS references in pyproject.toml/docker-compose.yml, and Redis usage → include PostGIS + Redis setup by default.
