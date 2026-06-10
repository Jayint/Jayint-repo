# Analysis — lyuwenyu/RT-DETR

**Harness status:** success | **True outcome:** harness_error | **Category:** repo2run_weak_test_deficient

**Pytest:** 0 total, 0 passed (0.0), 0 failed, 0 errors, 0 skipped

**Real tests existed:** no (none reached the container; repo source was never mounted) | **Tests executed:** no (pytest auto-ran but collected 0 items, returncode 5)

## Root cause
The harness reported `status=success` and `success=true`, but this is a false success: the container's `/repo` directory was mounted **completely empty** (`ls -la /repo` showed only `.` and `..`). The git clone of `lyuwenyu/RT-DETR` succeeded on the host (the full source tree — `rtdetr_pytorch`, `rtdetrv2_pytorch`, `rtdetr_paddle`, `benchmark`, README, multiple `requirements.txt` — is present under `input/repo/lyuwenyu/RT-DETR/`), and the SetupAgent's image-inference step even read those host files. But none of that code was ever made available inside the container. The agent spent all 56 inner commands exploring an empty `/repo` and the `/home/tools` toolbox, never found any project to configure, never created a test, and hit max-turns. The harness then auto-executed `run-pytest` (returncode 5, "no tests ran") and `run-pytest-collect` (returncode 5, "no tests collected"). Because RAT's "success" flag only reflects that setup/build steps completed without crashing — not that tests passed — it labeled this empty-container run a success.

## Environment / trajectory state at termination
- **Steps / tool calls used:** 65 trajectory messages (30 assistant turns), 56 inner commands executed. Scored tools: `run-pytest` count=1 (returncode 5, failed_count=1), `run-pytest-collect` count=1 (returncode 5). Duration 310s. `failure_reason: null`.
- **What the agent did (key inner_commands):** `ls -la /repo` (empty) → `find /repo -maxdepth 3 -type f` (nothing) → `cat /repo/.git` → "No .git file" → searched the whole filesystem for `.git` dirs, `src`/`workspace` dirs, READMEs, any non-empty files (all excluding `/proc`, `/usr`, `/home/tools`) → inspected every helper in `/home/tools` (`detect_environment.py`, `create_test.py`, `search_repo.py`, `run_pytest.py`, `run_test.py`) → checked pip/pytest availability. Its own reasoning at msg 54: *"The /repo directory is completely empty. Since there's no project code to work with..."*. It never created a test file (0 write/create invocations) and never recovered the source.
- **Last action and where it terminated:** Last agent action was `detect_environment.py --format json`. The agent then ran out of turns; the harness emitted "Max turns reached and run-pytest not run; auto-running...", auto-ran pytest and pytest-collect (both 0 items / rc 5), and stopped. Final log line: `[done] lyuwenyu/RT-DETR status=success`.

## Key evidence
Container `/repo` is empty (from run.log):
```
Running `ls -la /repo`...
drwxr-xr-x 2 root root 4096 Jun  5 16:26 .
drwxr-xr-x 1 root root 4096 Jun  5 16:31 ..
`ls -la /repo` executes with returncode: 0
...
Running `cat /repo/.git 2>/dev/null || echo "No .git file"`...
No .git file
```

Agent realized the repo was empty (trajectory msg 54):
```
### Thought: The /repo directory is completely empty. Since there's no project
code to work with, let me check if there are any environment variables that
indicate what project this should be, or if I need to create a simple test project.
```

Pytest summary tail (run_pytest_results.json raw_output):
```
collecting ... collected 0 items
--------------- generated xml file: /repo/logs/junit_report.xml ----------------
============================ no tests ran in 0.00s =============================
```
returncode: 5

Collection tail (run_pytest_collect_results.json):
```
no tests collected in 0.00s
```
success=true, returncode=5

`construct_test_result.json` is **ABSENT** from the instance dir; no discovery/`has_tests`/`created_test` record exists. The harness's own auto-runner reported the discovery directly (trajectory tail):
```
📁 Found 0 test files under /repo
⚠️  Warning: no test files found; running pytest anyway (may collect other tests)
```

Host-side clone DID contain real code (proving the repo is fine; only the mount failed):
```
input/repo/lyuwenyu/RT-DETR/rtdetr_pytorch/tools/train.py
input/repo/lyuwenyu/RT-DETR/rtdetr_pytorch/src/nn/backbone/test_resnet.py
input/repo/lyuwenyu/RT-DETR/rtdetrv2_pytorch/src/nn/backbone/test_resnet.py
input/repo/lyuwenyu/RT-DETR/rtdetr_paddle/ppdet/.../test_ms_deformable_attn_op.py
(253 .py files total)
```

## Reconciliation & caveats
- **Total vs breakdown + subtests:** `summary.total_tests = 0` and `passed+failed+skipped+errors+xfailed+xpassed = 0` — consistent, no gap. **0 subtests** detected (no "N subtests passed" line; there was no test session output at all).
- **Collection vs execution:** Collection = 0 items (rc 5); execution = 0 tests (rc 5). Consistent — both agree nothing exists. The agreement is not a healthy signal; it reflects an empty container, not a clean no-op.
- **Warnings incl. uncollectable classes:** **0 warnings** and **0 "cannot collect test class"** occurrences in any raw_output (no warnings-summary block — pytest had nothing to load). No ResourceWarning / tracebacks captured.
- **Hollow-success check:** Real tests? **No** — none reached the container (`/repo` empty); the repo *does* ship 3 `test_*.py` scripts on the host, but the agent never saw them. Placeholder? **No** — unlike a typical hollow success, the harness did **not** inject a `test_placeholder.py`; `create_test.py` was inspected but never invoked, so there is not even a synthetic test. `pytest_pass_rate (0.0)` == `pass_rate_exclude_code_issues (0.0)` — they match, and both are 0, so there is no code-issue exclusion masking anything. This is therefore **not** a hollow 1.0 pass; it is a 0.0 pass that the harness nonetheless flagged `success=true` because its success flag tracks setup completion, not test outcome.
- **Status vs reality:** `pytest_executed=true` is technically correct (pytest was launched) but misleading — it ran against an empty directory. `pytest_collect_success=true` likewise means "collection didn't crash," not "tests were found."

## Takeaway
This instance tells us essentially nothing about RAT's real capability on RT-DETR, because the agent was never given the repository to work on. The container `/repo` mount was empty despite a successful host-side clone — an infrastructure/harness wiring failure, not an agent reasoning failure or a genuinely test-deficient repo. The agent behaved reasonably (it correctly diagnosed the empty directory) but had no mechanism to re-fetch or mount the source, so it burned its turn budget on exploration and the harness auto-closed with a vacuous "success." Any aggregate that counts this row as a success (or even as a legitimate "0 tests, test-deficient repo") is double-wrong: the repo is real and non-trivial (253 Python files, 3 test scripts, multiple frameworks), and the run simply never had a chance to set it up. This row should be excluded from pass-rate denominators or re-run after fixing the mount.

## Fixability
**harness_bug** — The container received an empty `/repo` even though the clone into `input/repo/lyuwenyu/RT-DETR/` succeeded and the SetupAgent read the cloned files during image inference. The source-to-container handoff (volume mount / copy of the cloned repo into `/repo`) failed for this instance. Fixing requires ensuring the cloned repo is actually mounted/copied into the container before the agent loop starts (and ideally failing the run loudly when `/repo` is empty, rather than reporting `status=success`). Secondary harness issue: `success=true` should not be emitted when 0 tests were collected against an empty working directory. This is not env_fixable by the agent, not test_deficient (the repo ships tests), and not a hollow_success (no placeholder/inflated pass rate was produced).
