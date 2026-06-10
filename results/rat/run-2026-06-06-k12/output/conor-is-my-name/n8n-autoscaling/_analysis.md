# Analysis — conor-is-my-name/n8n-autoscaling

**Harness status:** success | **True outcome:** no_tests | **Category:** repo2run_weak_ci_service

**Pytest:** 0 total, 0 passed (0.0), 0 failed, 0 errors, 0 skipped

**Real tests existed:** no | **Tests executed:** no

## Root cause
This is a **node** instance (an n8n docker-compose autoscaling infra repo) and `/repo` was checked out **completely empty** — `ls -la /repo/` shows only `.` and `..` (`total 8`), no `package.json`, no `.git` (every `git` command returned 128), and an exhaustive filesystem-wide `find ... -name "package.json"` located nothing relevant. There was no project to configure and no test suite to run. The agent spent its entire turn budget exploring the empty container, then tried to fabricate a synthetic `test-project` placeholder (a `greet()` function with one mocha-style assertion); both heredoc write attempts timed out at ~600s and the final `python3` write also failed to materialize any files (the post-write `ls -la /repo/` is still empty). On max-turns the harness auto-ran `run-npm-install` and `run-npm-test`, both of which failed with `package.json not found`. The `status:success`/`success:true` flag is therefore hollow scorekeeping — it reflects only that the run terminated, not that any environment was set up or any test passed (`pytest_executed=false`, `pytest_pass_rate=0.0`).

## Environment / trajectory state at termination
- **Steps / tool calls used:** 65 trajectory messages; 55 inner commands executed; tool_stats records 1 `run-npm-install` (rc=1, failed) and 1 `run-npm-test` (rc=1, failed), both auto-executed by the harness at max-turns. Duration 1468.5s.
- **What the agent did (key inner_commands):** Repeatedly probed the empty repo and host (`ls -la /repo`, system-wide `find` for `package.json`, `mount`, `find / -name ".git"`, `cat /tmp/patch/*`), inspected the harness tools under `/home/tools/`, confirmed node 18 / npm present, then attempted to invent a fake project (`cat > package.json << 'EOF' ...`, `cat > test/test.js ...`, then a `python3` file-writer).
- **Last action and where it terminated:** Final agent action was the `python3 -c` placeholder-creation attempt; it reported "Files created successfully" but no files persisted. The agent hit "0 turns left", after which `[SYSTEM AUTO-EXECUTION]` ran `run-npm-install` and `run-npm-test` — both returned `❌ package.json not found: /repo/package.json`. Container stopped and removed.

## Key evidence

Empty repo + no git, from inner_commands.json / trajectory:
```
ls -la /repo/        -> total 8 ; drwxr-xr-x 2 root root .  ;  ..   (no files)
git status 2>&1      -> returncode: 128
git log --oneline -5 -> returncode: 128
find / -name "package.json" ... 2>/dev/null   -> (no repo package.json found)
```

Agent's fabricated placeholder attempt (both timed out ~600s, files never persisted):
```
cat > package.json << 'EOF'
{ "name": "test-project", "version": "1.0.0", ... "scripts": { "test": "node --test" } }
EOF ...            -> timed out after 300 seconds; returncode: 1
# post-attempt:
ls -la /repo/        -> total 8  (still empty)
ls -la /repo/test/   -> ls: cannot access '/repo/test/': No such file or directory  (rc 2)
```

Harness auto-execution at max-turns (pytest analog for node) — verbatim from run.log:
```
⚠️  Max turns reached and run-npm-install not run; auto-running...
### Auto-run run-npm-install output:
Running `python3 /home/tools/run_npm_install.py`...   ❌ package.json not found: /repo/package.json
⚠️  Max turns reached and run-npm-test not run; auto-running...
### Auto-run run-npm-test output:
Running `python3 /home/tools/run_npm_test.py`...       ❌ package.json not found: /repo/package.json
```

Discovery files absent (this is a node instance, not Python):
```
construct_test_result.json  -> ABSENT
run_pytest_results.json     -> ABSENT
run_pytest_collect_results.json -> ABSENT
# run.log:
⚠️ Could not find /repo/logs/run_npm_install_results.json in container
⚠️ Could not find /repo/logs/run_npm_test_results.json in container
```

## Reconciliation & caveats
- **Total vs breakdown + subtests:** `pytest_total_tests=0` and `passed+failed+skipped+errors=0`; consistent. No subtests detected (no pytest ran; this is a node repo).
- **Collection vs execution:** No pytest collection ran (`pytest_collect_success=false`, `pytest_executed=false`). The node analog (`run-npm-install` / `run-npm-test`) both failed because `package.json` was absent. No `N tests collected` line exists.
- **Warnings incl. uncollectable classes:** No pytest warnings block (no pytest). `uncollectable_classes=0`, `warnings=0` — but this is the absence of a test run, not a clean run.
- **Hollow-success check:** Real tests? No — the repo was empty so `has_tests` is effectively false (construct_test_result.json was never produced). Placeholder? The agent *attempted* to inject a synthetic `greet()`/`test-project` placeholder but it never persisted, so not even a hollow pass was recorded. `pytest_pass_rate=0.0` and `pass_rate_exclude_code_issues=0.0` agree — both zero, so no dual-metric divergence. The `status:success` flag is purely a termination flag and contradicts the 0.0 pass rate.

## Takeaway
This instance demonstrates RAT's harness "success" decoupling from real capability in its starkest form: the container was delivered with an empty `/repo` (no checkout, no `package.json`, no `.git`), so there was never an environment to configure or a test suite to run. The agent had no path to success and correctly identified the repo as empty, but then wasted the bulk of its 1468s budget on heredoc writes that timed out, and its placeholder fabrication never even materialized. The scorecard's `success:true` says nothing about RAT's setup ability here — the real signal is `pytest_pass_rate=0.0`, `pytest_executed=false`, 0 tests. It tells us nothing about RAT's competence on n8n-autoscaling because RAT was never given the repo.

## Fixability
**harness_bug** — The root cause is upstream of the agent: `/repo` was checked out empty (no source, no `package.json`, no `.git`), and the node install/test result artifacts (`run_npm_install_results.json`, `run_npm_test_results.json`) could never be produced. This is a checkout/clone failure in the benchmark harness, not an environment problem the agent could solve. Secondary harness friction: heredoc writes hung for ~600s each, draining the turn budget. Until the repo is actually populated in the container, no agent — RAT or otherwise — can configure or test this instance; the run should be classified as an environment-delivery failure (no_tests), not a setup success.
