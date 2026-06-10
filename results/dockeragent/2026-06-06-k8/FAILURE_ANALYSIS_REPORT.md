# DockerAgent Failure Analysis — RAT "hard" 50-repo subset

**Run:** `2026-06-06-k8` · **K=8** · **Model:** `deepseek-v4-flash` · **N=50** (0 missing)

DockerAgent autonomously configures each repo's environment in a Docker sandbox, then emits a Dockerfile + test script. The harness builds that image and runs the repo's pytest suite. This report separates **build success** (the harness's headline metric) from **environment correctness** (does the repo's test suite actually run and pass).

---

## 1. Executive Summary

- **The reported "56% success" (28/50) is a build-success metric, not an environment-correctness metric.** It counts images that built and produced a harness `status=success`. It does **not** mean the environment works.
- **The honest funnel collapses fast:** 50 attempted → **39 produced a Dockerfile** (78%) → **28 images built** (56%) → **9 collected and ran ≥1 test without all-erroring** → **8 environments actually work** (`pass_strong` + `pass_partial`).
- **The genuine "environment actually works" rate is 8/50 = 16%** (7 `pass_strong` + 1 `pass_partial`). The gap between 56% and 16% is the central finding of this report.
- **The dominant failure mode is the "hollow success":** 19 instances (38% of all repos, and **68% of the 28 "successes"**) built an image but every test errored — almost always `ModuleNotFoundError` because the project package or test deps were never installed into the eval image. Add `success_no_tests` (1) and **20 of 28 "successes" verified nothing**.
- **The single highest-leverage fix lives in `src/synthesizer.py`:** the build-recipe extractor silently drops successful state-changing commands (editable installs, `pip install -e .`, poetry/uv installs, apt prerequisites) and emits malformed multi-line `RUN` blocks. **26 of 50 instances (52%) are tagged `trivial_synthesizer_fix`.** Fixing command-preservation + Dockerfile codegen alone would move roughly 18-22 instances forward.
- **`build_failed` (11) and `no_dockerfile` (11) share a root:** both stem from the agent's in-sandbox loop ending in "Environment Configuration FAILED" or producing an unverified bundle. `build_failed` additionally emitted a *fallback* Dockerfile that was malformed/incomplete; `no_dockerfile` emitted nothing.
- **Malformed Dockerfile codegen is a discrete, recurring bug:** 9 instances are `dockerfile_synthesis_malformed`, and the signature is identical across them — consecutive `RUN` headers concatenated into one shell command (e.g. `RUN apt-get ... \` immediately followed by `RUN uv pip install ...`), plus dangling backslashes and unsubstituted templates (`FROM python:$PYTHON_IMAGE_TAG`).
- **A small but real set is genuinely environmental, not a DockerAgent bug:** live-service requirements (PostgreSQL/RabbitMQ for `aiidateam/aiida-core`, GCP creds for `GoogleCloudPlatform/slurm-gcp`), a documented-as-broken upstream `conftest.py` hook (`swar/nba_api`), and platform/permission test assumptions (`NewFuture/DDNS`). These should be **scored separately** and not counted against the agent.
- **The verification gate is too weak.** The agent repeatedly declared "Success" off `pytest --collect-only` (or even off *0 tests collected*) without ever executing a test in the **final eval image context**. Collection success in the sandbox masks venv-isolation and missing-install bugs that only surface in the fresh image.

### The honest funnel

| Stage | Count | % of 50 | Notes |
|---|---:|---:|---|
| Attempted | 50 | 100% | |
| **Produced a Dockerfile** | 39 | 78% | 11 ended `no_dockerfile` (env-config failed / no accepted bundle) |
| **Image built** (harness `status=success`) | 28 | 56% | **← the "56%" headline** (11 `build_failed`) |
| Collected & ran ≥1 test (not all-error, not zero) | 9 | 18% | excludes 19 `success_tests_all_error` + 1 `success_no_tests` |
| **Environment actually works** (`pass_strong`+`pass_partial`) | **8** | **16%** | **← the real success rate** |

> Reading the drop from 28 → 9: of the 28 built images, **19 had all tests error** (hollow env) and **1 collected 24 tests but executed 0** (`success_no_tests`), leaving 9 that actually exercised tests. Of those 9, one (`swar/nba_api`) is bucketed as `success_tests_all_error` in some views but the 8 truly-working are the 7 `pass_strong` + 1 `pass_partial`.

---

## 2. The Success Illusion — Hollow Successes & No-Test Successes

The harness marks `status=success` when the Docker image **builds**. Building proves the Dockerfile is syntactically valid; it proves nothing about whether the repo's package, runtime deps, or test deps are importable. **20 of 28 "successes" verified nothing.**

### 2a. Hollow successes — image built, every test errored (19 instances)

These all reach the eval stage and then fail at pytest collection/execution, overwhelmingly with `ModuleNotFoundError`. The common cause: the agent installed everything correctly **in the sandbox**, but the synthesizer dropped the install command (or the test ran against the wrong Python/venv).

| Instance | Why hollow | Smoking gun |
|---|---|---|
| `D4Vinci/Scrapling` | editable install + test deps dropped; only `pip install requests` survived | `ModuleNotFoundError: No module named 'typing_extensions'` ×94 |
| `EnableSecurity/wafw00f` | `build_commands: []` — hollow recipe | `ModuleNotFoundError: No module named 'responses'` |
| `MemTensor/MemOS` | poetry ran but no `pip install -e .` → project not importable | 113 `ModuleNotFoundError` (pydantic/fastapi/yaml) |
| `Nitrokey/pynitrokey` | poetry editable didn't persist to eval image | `ERROR pynitrokey - ModuleNotFoundError: No module named 'nitrokey'` |
| `Peterande/D-FINE` | agent installed **zero** Python deps; misread 0-collected as success | `ModuleNotFoundError: No module named 'faster_coco_eval'` |
| `Tecnativa/docker-socket-proxy` | verified from `/app`, Dockerfile clones to `/testbed`; no poetry activation | `ModuleNotFoundError: No module named 'plumbum'` |
| `copier-org/copier` | `pip install --group dev .` excluded as "alternative approach" | `ModuleNotFoundError: No module named 'coverage'` (conftest) |
| `django-oauth/django-oauth-toolkit` | harness runs bare pytest without `DJANGO_SETTINGS_MODULE` | `ImproperlyConfigured: Requested setting OAUTH2_PROVIDER` |
| `google/Xee` | `pip install -e ".[tests]"` dropped from Dockerfile | `ModuleNotFoundError: No module named 'absl'` |
| `jasonxtn/Argus` | `pip install -e . --no-build-isolation` excluded after "initial failure" | `ModuleNotFoundError` (requests/rich/dns) |
| `pre-commit/pre-commit` | no `pip install -e .` → no package metadata | `PackageNotFoundError: No package metadata ... pre_commit` |
| `python-websockets/websockets` | `build_commands` empty despite successful `pip install -e .` | `ModuleNotFoundError: No module named 'websockets'` (all 41) |
| `rq/rq` | `pip install -e ".[dev]" redis` omitted; bundle rejected → fallback lost it | `ModuleNotFoundError: No module named 'redis'` (31 errors) |
| `sooperset/mcp-atlassian` | eval injects bare `pip install pytest` into system Python, breaking venv | `ModuleNotFoundError: No module named 'anyio'` |
| `unit8co/darts` | test command hardcodes `/app/.venv`; uv created `/testbed/.venv` | `ModuleNotFoundError: No module named 'narwhals'` |
| `yihong0618/bilingual_book_maker` | test cmd `cd /app` but WORKDIR `/testbed`; venv lost | `ModuleNotFoundError: No module named 'ebooklib'` |
| `yutto-dev/yutto` | pytest in system Python, yutto in `.venv`; no `uv run` | `ModuleNotFoundError: No module named 'yutto'` (17 tests) |
| `swar/nba_api` | upstream conftest hook unsupported by locked plugin version | `PluginValidationError: unknown hook 'pytest_recording_configure'` |
| `aiidateam/aiida-core` | needs live PostgreSQL + RabbitMQ; pytest timed out at 180s | `error_breakdown: {"TimeoutError": 1}` |

### 2b. No-test success — image built, tests collected, zero executed (1 instance)

- **`sirfz/tesserocr`** (`success_no_tests`): the agent finalized with `pytest --collect-only -q --disable-warnings` as the **test command**. 24 tests collected, **0 executed**, pass_rate 0. Quote: `"verified_test_command": "pytest --collect-only -q --disable-warnings"` and `"total_tests": 0`.

### What the harness/agent should assert before declaring success

1. **Import gate:** before finalizing, run `python -c "import <top_level_pkg>"` (or `poetry/uv run`-wrapped equivalent) **in the final image build context**, not the sandbox.
2. **Execution gate, not collection gate:** require ≥1 test to *execute to completion* (pass or fail). Reject `--collect-only` as the final test command (`sirfz/tesserocr`).
3. **Context-match gate:** the verified test command must run from the Dockerfile's actual `WORKDIR` and venv (`Tecnativa`, `darts`, `bilingual_book_maker`, `yutto`).
4. **Re-verify in eval image:** sandbox collection success ≠ eval correctness (`Nitrokey`, `rq`, `MemOS`).

---

## 3. Failure Taxonomy

Root-cause distribution (authoritative counts), ordered by frequency:

| Root cause category | Count | Instances |
|---|---:|---|
| `test_deps_not_installed` | 11 | EnableSecurity/wafw00f, MemTensor/MemOS, NewFuture/DDNS, Peterande/D-FINE, Tecnativa/docker-socket-proxy, aapatre/…udemy…, copier-org/copier, jasonxtn/Argus, sooperset/mcp-atlassian, unit8co/darts, yutto-dev/yutto |
| `dockerfile_synthesis_malformed` | 9 | FoundationAgents/OpenManus, PrimeIntellect-ai/verifiers, bruin-data/ingestr, conor-is-my-name/n8n-autoscaling, dataabc/weibo-crawler, docling-project/docling, microsoft/markitdown, scylladb/scylla-cluster-tests, stlehmann/pyads |
| `editable_install_missing` | 7 | D4Vinci/Scrapling, Nitrokey/pynitrokey, google/Xee, pre-commit/pre-commit, python-websockets/websockets, rq/rq, yihong0618/bilingual_book_maker |
| `deps_installed_correctly` | 6 | BeehiveInnovations/pal-mcp-server, LibreTranslate/LibreTranslate, jhao104/proxy_pool, open-webui/mcpo, py2many/py2many, resend/resend-python |
| `uncollectable_tests_blocked_config` | 5 | ModelEngine-Group/nexent, feast-dev/feast, gip-inclusion/les-emplois, nomadkaraoke/karaoke-gen, swar/nba_api |
| `other` | 3 | NevaMind-AI/memU-server, django-oauth/django-oauth-toolkit, nginx-proxy/nginx-proxy |
| `service_dependency_required` | 2 | GoogleCloudPlatform/slurm-gcp, aiidateam/aiida-core |
| `dockerfile_missing_setup_step` | 2 | Yelp/dumb-init, epam/ai-dial-sdk |
| `step_budget_exhausted` | 2 | frappe/press, supabase/supabase-py |
| `dependency_resolution_conflict` | 1 | lyuwenyu/RT-DETR |
| `system_package_or_apt_failure` | 1 | rayai-labs/agentic-ray |
| `no_tests_discovered` | 1 | sirfz/tesserocr |

Fixability distribution:

| Fixability | Count |
|---|---:|
| `trivial_synthesizer_fix` | 26 |
| `already_working` | 7 |
| `planner_strategy_fix` | 6 |
| `needs_more_steps` | 5 |
| `needs_service_deps` | 3 |
| `test_harness_artifact` | 2 |
| `genuinely_hard_repo` | 1 |

---

## 4. Deep Dives by Root Cause

### 4a. Dockerfile synthesis malformed (9) — `src/synthesizer.py` codegen

**Mechanism.** The synthesizer assembles the final Dockerfile as **stateless transcription** of sandbox commands. When multiple commands are emitted as separate `RUN` lines but one ends in a backslash continuation, the next `RUN` keyword gets folded into the previous shell invocation. Docker then runs `apt-get ... RUN uv pip install ...` as one `/bin/sh -c`, and apt rejects the foreign flags. The same codegen path also leaves dangling backslashes and unsubstituted templates.

**Representative smoking guns:**
- `FoundationAgents/OpenManus`:
  ```dockerfile
  RUN apt-get update && apt-get install -y --no-install-recommends git curl \
  RUN uv pip install --system -r requirements.txt
  ```
  → `E: Command line option --system is not understood ...` (exit 100)
- `bruin-data/ingestr`: `RUN --mount=type=cache,target=/go/pkg/mod` folded into `apt-get install` → `--mount=... is not understood`.
- `docling-project/docling`: `RUN apt-get update \` then `RUN pip install --no-cache-dir docling` → `--no-cache-dir is not understood`.
- `scylladb/scylla-cluster-tests`: `FROM python:$PYTHON_IMAGE_TAG` (template never substituted) → `failed to parse stage name "python:"`. Also dangling backslashes on lines 23/28/30. (2953 tests collected in sandbox — env was fine.)
- `microsoft/markitdown`: fallback extraction produced `RUN apt-get ... \` / `RUN if [ ... ]; then \` → `Syntax error: "then" unexpected`.
- `conor-is-my-name/n8n-autoscaling` & `dataabc/weibo-crawler`: Alpine base + emitted `apt`-style writes to `/etc/apt/apt.conf.d/` → `can't create ... nonexistent directory`, plus trailing-backslash `apk add` lines.
- `PrimeIntellect-ai/verifiers`: `uv pip install` before any `uv venv` → `No virtual environment found`.

**Fix.** In `src/synthesizer.py`: (1) treat each setup step as a *complete, independent* `RUN` directive — never let a backslash continuation precede a new `RUN`; (2) validate the Dockerfile before emission (no dangling backslashes, no unsubstituted `$VARS`, every `RUN` syntactically complete); (3) make codegen base-image-aware (Alpine ⇒ `apk`, never `apt`; `uv pip` requires a prior `uv venv` or `--system`); (4) add a dry-run `docker build --check`-style lint pass.

### 4b. Missing editable / test-dep installs behind hollow successes (`editable_install_missing` 7 + much of `test_deps_not_installed` 11)

**Mechanism.** The agent runs `pip install -e .` / `poetry install` / `uv sync` successfully in the sandbox, but the recipe extractor **discards** the command. Three observed discard reasons, all wrong:
- *"rejected as combined action"* — a `cmd1 && cmd2` was split into two successful steps, but the extractor kept neither and substituted an unrelated command (`D4Vinci/Scrapling`: only `pip install requests` survived).
- *"alternative approach not in the successful trajectory"* — `copier-org/copier` dropped `pip install --group dev .`; `jasonxtn/Argus` dropped `pip install -e . --no-build-isolation` because an *earlier* attempt failed before succeeding.
- *empty `build_commands`* — `python-websockets/websockets` and `EnableSecurity/wafw00f` emitted `build_commands: []` despite logged `Successfully installed`.

**Smoking guns:**
- `python-websockets/websockets`: `run.log:332` `Successfully installed websockets-16.1.dev17+g9ff5c77`; Dockerfile line 18 `# No additional setup instructions from agent`.
- `copier-org/copier`: excluded `"pip install --group dev ."`, reason `"alternative approach not in the successful trajectory..."` → eval `ModuleNotFoundError: No module named 'coverage'`.
- `MemTensor/MemOS`: poetry installed deps but no `pip install -e .` → 113 errors; agent concluded "All 621 tests collected" (actual 154, and eval errored).

**Fix.** In `src/synthesizer.py`: traverse sandbox snapshots **chronologically** and include *every* state-mutating command that ultimately succeeded — even if it (a) was split from a compound command, or (b) failed once then succeeded after a prerequisite. Stop filtering on trajectory heuristics. Add a language-handler default that **injects `pip install -e .` (or `-e ".[test]"`)** whenever a Python repo's tests import its own top-level package. Editable/extras installs are mandatory build steps, never "redundant."

### 4c. The `no_dockerfile` / env-config-failed cluster (11)

**Mechanism.** The agent never reaches synthesis. Distinct sub-causes:
- **Planner protocol breakage (`NevaMind-AI/memU-server`):** deepseek-v4-flash wrapped every command in ```` ```bash ```` fences; the parser required raw `Action: <cmd>` and rejected all 30 steps → zero setup executed. **Add markdown-fence stripping to the action parser** and a circuit-breaker after 3 identical parse errors.
- **Step-budget exhaustion (`frappe/press`, `supabase/supabase-py`, and several `needs_more_steps`):** agent burned the budget on dependency conflicts or per-package collection errors and **never reached the Dockerfile-synthesis step** even after finding a working command. `supabase/supabase-py` discovered a working `--ignore`-filtered pytest (185 tests) at Step 29 but `build_recipe=null`.
- **Sandbox policy blocked the last step (`nginx-proxy/nginx-proxy`, `gip-inclusion/les-emplois`):** 467/… tests collected, then the final verification piped through `tail`/`head`, which the sandbox rejects, wasting the terminal step. Quote: `[SYSTEM] setup or test commands must not pipe output through head, tail, or grep`.
- **Containerd commit failures (`nomadkaraoke/karaoke-gen`, `rayai-labs/agentic-ray`, near-miss `feast-dev/feast`):** Docker `500 ... failed to Lchown ...` on CUDA/nvidia libs during snapshot commit → run aborted before synthesis.
- **Multi-package / flat-layout misdiagnosis (`ModelEngine-Group/nexent`):** needed `pip install -e ./backend -e ./sdk`; agent spent 14 steps chasing pytest versions instead.
- **Wrong base image (`rayai-labs/agentic-ray`):** `image_selector` chose `node:22` for a Python monorepo → `No module named pip`.

**Fix.** In `src/planner.py`: (1) **always synthesize a best-effort Dockerfile from the last known-good state** rather than emitting `no_dockerfile` — the harness should never receive `status=error` with nothing; (2) treat "test collection succeeded" as a terminal goal-state and jump straight to synthesis; (3) detect multi-`pyproject.toml`/flat-layout repos upfront and plan editable installs before collection. Relax the sandbox output-filter rule for read-only `--collect-only` probes (`src/sandbox.py`). Improve `image_selector` language detection for monorepos.

### 4d. Dependency-resolution conflicts (1, plus `frappe/press`)

- **`lyuwenyu/RT-DETR`** (`dependency_resolution_conflict`): source does an **exact** string check `importlib.metadata.version('torchvision') == '0.15.2'`, but the installed build is `0.15.2+cpu`, so the `+cpu` qualifier fails the equality and raises `RuntimeError('Please make sure torchvision version >= 0.15.2')`. Tagged `trivial_synthesizer_fix`: prefer wheels without local build qualifiers, or pick a version that skips the brittle branch.
- **`frappe/press`** (`step_budget_exhausted`): genuinely unsatisfiable transitive constraints (`aws-sam-translator` needs `pydantic~=2.13.3` vs `frappe-mcp` needs `~=2.11.7`; `joserfc` needs `cryptography>=45` vs installed 41). Needs a smarter constraint-relaxation/backtracking strategy or manual resolution.

### 4e. Service / native / system cases (genuinely environmental — score separately)

- **`aiidateam/aiida-core`** (`needs_service_deps`): all 165 deps installed; integration tests need **live PostgreSQL + RabbitMQ**; pytest timed out at 180s. Not a synthesis bug.
- **`GoogleCloudPlatform/slurm-gcp`** (`needs_service_deps`): `conftest` calls `compute_service()` at import time, requiring valid GCP service-account creds; fake key fails RSA deserialization. Architecturally unsatisfiable in-sandbox.
- **`Yelp/dumb-init`** & **`epam/ai-dial-sdk`** (`dockerfile_missing_setup_step`): base image lacked Python/`pip3` (`pip3: not found`, exit 127) and `poetry` (`poetry: not found`, exit 127) respectively — the agent used them in-sandbox but never emitted the `apt-get install python3 python3-pip` / `pip3 install poetry` prerequisite. **Fixable** (`epam` is `trivial_synthesizer_fix`): track every system binary invoked and emit its install before first use.
- **`swar/nba_api`** (`genuinely_hard_repo`): upstream `conftest.py` uses `pytest_recording_configure`, unknown to the locked `pytest-recording 0.13.4`. Repo's own lockfile is wrong; DockerAgent can't patch immutable source.
- **`NewFuture/DDNS`** (`pass_partial`, "already working"): 853/877 pass (99.53%); the 4 failures are Unix permission-semantics tests that don't hold under root Docker. Environment is correct.

---

## 5. Outcome by Difficulty Category

| Category | n | Working (strong+partial) | Hollow / no-test | Build failed | No Dockerfile | Verdict |
|---|---:|---:|---:|---:|---:|---|
| `connection_error_stress` | 12 | 4 | 6 | 1 | 1 | **Best handled.** 3 clean `pass_strong` + 1 `pass_partial`; the 6 hollow are pure synthesizer drops. |
| `repo2run_weak_test_deficient` | 11 | 0 | 7 | 3 | 1 | **Worst.** Zero working. Dominated by missing editable/test-dep installs + malformed codegen. |
| `native_runtime_stress` | 7 | 1 | 4 | 1 | 1 | Mixed; 1 strong (`py2many`), 1 no-test (`tesserocr`), rest venv/system-package issues. |
| `winnable_large` | 8 | 0 | 2 | 1 | 5 | **Not handled.** Large/monorepo repos exhaust step budget before synthesis. |
| `easy_control` | 5 | 1 | 1 | 2 | 1 | **Underperforming for "easy."** Only 1/5 works; 2 build-fails are trivial synthesizer prerequisite drops. |
| `documented_rat_failure` | 3 | 1 | 0 | 1 | 1 | 1 actually passes (`jhao104/proxy_pool`, 147/147). |
| `repo2run_weak_ci_service` | 3 | 1 | 0 | 1 | 1 | `LibreTranslate` works; others CI-service/codegen. |
| `hard_general` | 1 | 0 | 0 | 1 | 0 | `docling` — malformed RUN, env was fine. |

**Takeaways:** DockerAgent handles small, single-package, well-structured Python repos (`connection_error_stress` clean cases, `easy_control` winners) but is defeated by (a) anything requiring an editable install the synthesizer drops, (b) large/monorepo repos that blow the step budget, and (c) its own malformed Dockerfile codegen. The poor `easy_control` result (1/5) is alarming because those are supposed to be controls — and the failures are all trivial synthesizer bugs, not repo difficulty.

---

## 6. Cross-Cutting Patterns

- **`build_failed` (11) and `no_dockerfile` (11) are two faces of one failure** — the in-sandbox loop ended without a verified configuration. `no_dockerfile` emitted nothing; `build_failed` emitted a **fallback Dockerfile reconstructed from execution history**, which was then malformed (`markitdown`, `scylla`, `weibo-crawler`) or missing a prerequisite (`Yelp/dumb-init`, `epam`). The fallback path is unsafe: it transcribes partial/unverified state into Docker syntax without validation.
- **The verification-bundle gap is systemic.** Across `rq/rq`, `microsoft/markitdown`, `conor-is-my-name/n8n-autoscaling`, `dataabc/weibo-crawler`, `supabase/supabase-py`, and others, the harness logged *"No accepted Verification Bundle test commands were found"* / `test_command_source: missing_agent_verification_bundle`. When the bundle is rejected, the harness **silently falls back** and loses the very state-changing commands (editable installs) that make the env work. Rejection should **fail loud and force a retry**, not degrade to a lossy fallback.
- **Sandbox-vs-eval context divergence is the deepest pattern.** The agent verifies in a stateful sandbox (deps already installed, venv already active, sometimes a `/app` clone), but the eval image is a **fresh build at `/testbed` with system Python**. Every `success_tests_all_error` is, at root, a failure to reproduce sandbox state in the eval context — whether via dropped installs (`websockets`), wrong venv path (`darts`, `yutto`), or wrong WORKDIR (`bilingual_book_maker`, `Tecnativa`).
- **Collection ≠ execution.** The agent treats `pytest --collect-only` success as terminal. It hides plugin mismatches (`aapatre`: async tests, no `pytest-asyncio`), venv isolation (`mcp-atlassian`: anyio), and outright misreads 0-collected-due-to-error as "no tests" (`Peterande/D-FINE`).
- **deepseek-v4-flash instruction-following is a liability** for the strict single-step `Action:` protocol (`NevaMind-AI/memU-server` lost all 30 steps to fenced output). The parser must be defensive; alternatively use a stronger model for the planner role.

---

## 7. Prioritized Recommendations

Ranked by instances recovered. Instance counts are estimates of how many would move from a failing/hollow state toward a real pass.

| # | Recommendation | ~Instances moved | Target code | Effort |
|---|---|---:|---|---|
| 1 | **Preserve all successful state-changing commands** in the build recipe — traverse snapshots chronologically; include split-compound steps and retried-then-succeeded steps; never emit empty `build_commands`; auto-inject `pip install -e .`/`[test]` when tests import the repo's own package. | 12-16 | `src/synthesizer.py`, `src/language_handlers.py` | M |
| 2 | **Make Dockerfile codegen valid + base-image-aware.** Each setup step = one complete `RUN`; no backslash before a new `RUN`; substitute all `$VARS`; Alpine⇒`apk`; `uv pip`⇒prior `uv venv`/`--system`. Add a pre-emission lint/`docker build --check` pass. | 9 | `src/synthesizer.py` | M |
| 3 | **Add an eval-context verification gate.** Require ≥1 test to *execute* (not collect) and the top-level package to *import* in the **final image**, with the verified command run from the real `WORKDIR`/venv. Reject `--collect-only` as a final command. | 7-9 | `src/verification_bundle.py`, `src/planner.py` | M |
| 4 | **Always synthesize a fallback Dockerfile from last-good state** instead of emitting `no_dockerfile`; treat "tests collected" as terminal and jump to synthesis; on rejected bundle, fail loud + retry rather than lossy fallback. | 5-7 | `src/planner.py`, `src/synthesizer.py` | M |
| 5 | **Emit system-package prerequisites.** Track every system binary used in-sandbox (`python3`, `pip3`, `poetry`, `meson`, `ninja`) and emit its `apt-get install`/`pip install` before first use, in order. | 4-5 | `src/synthesizer.py`, `src/language_handlers.py` | S |
| 6 | **Harden the action parser + planner protocol.** Strip markdown code fences; circuit-break after 3 identical parse errors; relax sandbox output-filter ban for read-only `--collect-only` probes. | 3-4 | `src/planner.py`, `src/sandbox.py` | S |
| 7 | **Increase / make-aware the step budget for large & monorepo repos**; detect multi-`pyproject.toml`/flat-layout upfront and plan editable installs early; better constraint backtracking. | 3-5 | `src/planner.py` | M-L |
| 8 | **Improve `image_selector` for multi-language monorepos** (don't pick `node:22` for a Python workspace). | 1-2 | `src/image_selector.py`, `src/language_handlers.py` | S |
| 9 | **Score service/native cases separately.** Tag repos needing live services (Postgres/RabbitMQ/GCP) or relying on immutable broken upstream `conftest` so they don't count against the agent; optionally add opt-in service provisioning. | 3 (reclassify) | harness scoring + `src/planner.py` | M |

**Bottom line:** Recommendations #1 + #2 + #5 are all in `src/synthesizer.py` and together cover the `trivial_synthesizer_fix` bucket (26 instances). They are the clear highest-leverage work and could roughly double the real success rate before touching planner strategy.

---

## 8. Appendix — Full 50-row Table

`pass_rate` shown where reported in the per-instance analysis; blank = not reported. Each row links to its analysis file.

| Instance | Category | Harness status | TRUE outcome | pass_rate | Root cause | Fixability |
|---|---|---|---|---:|---|---|
| [BeehiveInnovations/pal-mcp-server](./output/BeehiveInnovations/pal-mcp-server/_analysis.md) | connection_error_stress | success | pass_strong | 0.9786 | deps_installed_correctly | already_working |
| [D4Vinci/Scrapling](./output/D4Vinci/Scrapling/_analysis.md) | connection_error_stress | success | success_tests_all_error | 0.0214 | editable_install_missing | trivial_synthesizer_fix |
| [EnableSecurity/wafw00f](./output/EnableSecurity/wafw00f/_analysis.md) | repo2run_weak_test_deficient | success | success_tests_all_error | 0 | test_deps_not_installed | trivial_synthesizer_fix |
| [FoundationAgents/OpenManus](./output/FoundationAgents/OpenManus/_analysis.md) | repo2run_weak_test_deficient | build_failed | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [GoogleCloudPlatform/slurm-gcp](./output/GoogleCloudPlatform/slurm-gcp/_analysis.md) | native_runtime_stress | error (no_dockerfile) | no_dockerfile | 0 | service_dependency_required | needs_service_deps |
| [LibreTranslate/LibreTranslate](./output/LibreTranslate/LibreTranslate/_analysis.md) | repo2run_weak_ci_service | success | pass_strong | 1 | deps_installed_correctly | already_working |
| [MemTensor/MemOS](./output/MemTensor/MemOS/_analysis.md) | winnable_large | success | success_tests_all_error | 0.2662 | test_deps_not_installed | trivial_synthesizer_fix |
| [ModelEngine-Group/nexent](./output/ModelEngine-Group/nexent/_analysis.md) | winnable_large | error (no_dockerfile) | no_dockerfile | 0 | uncollectable_tests_blocked_config | planner_strategy_fix |
| [NevaMind-AI/memU-server](./output/NevaMind-AI/memU-server/_analysis.md) | repo2run_weak_ci_service | error (no_dockerfile) | no_dockerfile | 0 | other | planner_strategy_fix |
| [NewFuture/DDNS](./output/NewFuture/DDNS/_analysis.md) | connection_error_stress | success | pass_partial | 0.9953 | test_deps_not_installed | already_working |
| [Nitrokey/pynitrokey](./output/Nitrokey/pynitrokey/_analysis.md) | native_runtime_stress | success | success_tests_all_error | 0 | editable_install_missing | trivial_synthesizer_fix |
| [Peterande/D-FINE](./output/Peterande/D-FINE/_analysis.md) | native_runtime_stress | success | success_tests_all_error | 0 | test_deps_not_installed | planner_strategy_fix |
| [PrimeIntellect-ai/verifiers](./output/PrimeIntellect-ai/verifiers/_analysis.md) | repo2run_weak_test_deficient | error (build_failed) | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [Tecnativa/docker-socket-proxy](./output/Tecnativa/docker-socket-proxy/_analysis.md) | connection_error_stress | success | success_tests_all_error | 0 | test_deps_not_installed | trivial_synthesizer_fix |
| [Yelp/dumb-init](./output/Yelp/dumb-init/_analysis.md) | native_runtime_stress | build_failed | build_failed | 0 | dockerfile_missing_setup_step | planner_strategy_fix |
| [aapatre/Automatic-Udemy-Course-Enroller…](./output/aapatre/Automatic-Udemy-Course-Enroller-GET-PAID-UDEMY-COURSES-for-FREE/_analysis.md) | connection_error_stress | success | pass_strong | 0.8889 | test_deps_not_installed | trivial_synthesizer_fix |
| [aiidateam/aiida-core](./output/aiidateam/aiida-core/_analysis.md) | winnable_large | success | success_tests_all_error | 0 | service_dependency_required | needs_service_deps |
| [bruin-data/ingestr](./output/bruin-data/ingestr/_analysis.md) | repo2run_weak_test_deficient | build_failed | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [conor-is-my-name/n8n-autoscaling](./output/conor-is-my-name/n8n-autoscaling/_analysis.md) | repo2run_weak_ci_service | error (build_failed) | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [copier-org/copier](./output/copier-org/copier/_analysis.md) | repo2run_weak_test_deficient | success | success_tests_all_error | 0 | test_deps_not_installed | trivial_synthesizer_fix |
| [dataabc/weibo-crawler](./output/dataabc/weibo-crawler/_analysis.md) | connection_error_stress | build_failed | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [django-oauth/django-oauth-toolkit](./output/django-oauth/django-oauth-toolkit/_analysis.md) | connection_error_stress | success | success_tests_all_error | 0 | other | test_harness_artifact |
| [docling-project/docling](./output/docling-project/docling/_analysis.md) | hard_general | error (build_failed) | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [epam/ai-dial-sdk](./output/epam/ai-dial-sdk/_analysis.md) | easy_control | error (build_failed) | build_failed | 0 | dockerfile_missing_setup_step | trivial_synthesizer_fix |
| [feast-dev/feast](./output/feast-dev/feast/_analysis.md) | winnable_large | error (no_dockerfile) | no_dockerfile | 0 | uncollectable_tests_blocked_config | needs_more_steps |
| [frappe/press](./output/frappe/press/_analysis.md) | winnable_large | error (no_dockerfile) | no_dockerfile | 0 | step_budget_exhausted | needs_more_steps |
| [gip-inclusion/les-emplois](./output/gip-inclusion/les-emplois/_analysis.md) | winnable_large | error (no_dockerfile) | no_dockerfile | 0 | uncollectable_tests_blocked_config | trivial_synthesizer_fix |
| [google/Xee](./output/google/Xee/_analysis.md) | easy_control | success | success_tests_all_error | 0 | editable_install_missing | trivial_synthesizer_fix |
| [jasonxtn/Argus](./output/jasonxtn/Argus/_analysis.md) | connection_error_stress | success | success_tests_all_error | 0 | test_deps_not_installed | trivial_synthesizer_fix |
| [jhao104/proxy_pool](./output/jhao104/proxy_pool/_analysis.md) | documented_rat_failure | success | pass_strong | 1 | deps_installed_correctly | already_working |
| [lyuwenyu/RT-DETR](./output/lyuwenyu/RT-DETR/_analysis.md) | repo2run_weak_test_deficient | error (no_dockerfile) | no_dockerfile | 0 | dependency_resolution_conflict | trivial_synthesizer_fix |
| [microsoft/markitdown](./output/microsoft/markitdown/_analysis.md) | documented_rat_failure | error (build_failed) | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [nginx-proxy/nginx-proxy](./output/nginx-proxy/nginx-proxy/_analysis.md) | documented_rat_failure | error (no_dockerfile) | no_dockerfile | 0 | other | needs_more_steps |
| [nomadkaraoke/karaoke-gen](./output/nomadkaraoke/karaoke-gen/_analysis.md) | winnable_large | error (no_dockerfile) | no_dockerfile | 0 | uncollectable_tests_blocked_config | needs_more_steps |
| [open-webui/mcpo](./output/open-webui/mcpo/_analysis.md) | connection_error_stress | success | pass_strong | 1 | deps_installed_correctly | already_working |
| [pre-commit/pre-commit](./output/pre-commit/pre-commit/_analysis.md) | repo2run_weak_test_deficient | success | success_tests_all_error | 0 | editable_install_missing | trivial_synthesizer_fix |
| [py2many/py2many](./output/py2many/py2many/_analysis.md) | native_runtime_stress | success | pass_strong | 0.813 | deps_installed_correctly | already_working |
| [python-websockets/websockets](./output/python-websockets/websockets/_analysis.md) | repo2run_weak_test_deficient | success | success_tests_all_error | 0 | editable_install_missing | trivial_synthesizer_fix |
| [rayai-labs/agentic-ray](./output/rayai-labs/agentic-ray/_analysis.md) | easy_control | error (no_dockerfile) | no_dockerfile | 0 | system_package_or_apt_failure | needs_service_deps |
| [resend/resend-python](./output/resend/resend-python/_analysis.md) | easy_control | success | pass_strong | 1 | deps_installed_correctly | already_working |
| [rq/rq](./output/rq/rq/_analysis.md) | connection_error_stress | success | success_tests_all_error | 0 | editable_install_missing | planner_strategy_fix |
| [scylladb/scylla-cluster-tests](./output/scylladb/scylla-cluster-tests/_analysis.md) | winnable_large | error (build_failed) | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [sirfz/tesserocr](./output/sirfz/tesserocr/_analysis.md) | native_runtime_stress | success | success_no_tests | 0 | no_tests_discovered | trivial_synthesizer_fix |
| [sooperset/mcp-atlassian](./output/sooperset/mcp-atlassian/_analysis.md) | repo2run_weak_test_deficient | success | success_tests_all_error | 0 | test_deps_not_installed | test_harness_artifact |
| [stlehmann/pyads](./output/stlehmann/pyads/_analysis.md) | easy_control | build_failed | build_failed | 0 | dockerfile_synthesis_malformed | trivial_synthesizer_fix |
| [supabase/supabase-py](./output/supabase/supabase-py/_analysis.md) | connection_error_stress | error (no_dockerfile) | no_dockerfile | 0 | step_budget_exhausted | needs_more_steps |
| [swar/nba_api](./output/swar/nba_api/_analysis.md) | connection_error_stress | success | success_tests_all_error | 0 | uncollectable_tests_blocked_config | genuinely_hard_repo |
| [unit8co/darts](./output/unit8co/darts/_analysis.md) | repo2run_weak_test_deficient | success | success_tests_all_error | 0 | test_deps_not_installed | trivial_synthesizer_fix |
| [yihong0618/bilingual_book_maker](./output/yihong0618/bilingual_book_maker/_analysis.md) | repo2run_weak_test_deficient | success | success_tests_all_error | 0 | editable_install_missing | planner_strategy_fix |
| [yutto-dev/yutto](./output/yutto-dev/yutto/_analysis.md) | native_runtime_stress | success | success_tests_all_error | 0 | test_deps_not_installed | trivial_synthesizer_fix |

---

*Counts in §3 and §7 are derived from the authoritative precomputed aggregates; quoted evidence is drawn from the per-instance `_analysis.md` files under `./output/<org>/<repo>/`.*
