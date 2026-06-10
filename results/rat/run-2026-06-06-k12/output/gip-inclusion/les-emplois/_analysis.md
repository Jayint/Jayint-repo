# Analysis — gip-inclusion/les-emplois

**Harness status:** success | **True outcome:** harness_error | **Category:** winnable_large

**Pytest:** 0 total, 0 passed (0.0), 0 failed, 0 errors, 0 skipped

**Real tests existed:** unknown (repo never reached container) | **Tests executed:** no

## Root cause
The repository cloned fine on the host (`git clone --depth=1 ... ./rat_run_rat/input/repo/gip-inclusion/les-emplois` → "Successfully cloned"), but the harness then tried to copy it into the container from the WRONG path: it ran `docker cp /opt/runanything/src/input/repo/gip-inclusion/les-emplois/. <container>:/repo`, while the clone actually landed under the relative `./rat_run_rat/input/...` tree. That absolute source path does not exist (the log earlier emits `lstat /opt/runanything/src/input: no such file or directory`), so the `docker cp` returned non-zero ("Container start faild ... returned non-zero exit status 1") and `/repo` was left completely empty inside the container. The agent then explored an empty `/repo`, correctly concluded there was no project code, and called `stop`. This is a harness path-mismatch bug, not an agent or dependency failure — yet the run was still flagged `status=success`.

## Environment / trajectory state at termination
- **Steps / tool calls used:** 19 bash commands total (18 exploratory + 1 `stop`); only the `stop` tool was invoked per `tool_stats.json` (no `run-pytest`, no `run-pytest-collect`). ~25 turns left unused. Wall time 97s; agent cost_time ~75s.
- **What the agent did (key inner_commands):** `ls /repo` / `ls -la /repo` (empty), `find /repo -type f` (nothing), `ls -la /` and probes of `/tmp`, `/root`, `/home/tools`, `/opt`, then `cat /root/.gitconfig`, `cat /root/.pip/pip.conf`, and finally `git status` / `git log` in `/repo` ("not a git repository"). Every command returned 0 but found no project.
- **Last action and termination:** After confirming `/repo` was an empty, non-git directory, the agent reasoned "The repository is empty - there's no project code to configure ... I should stop" and issued `stop`. The harness then failed to copy `run_pytest_results.json` / `run_pytest_collect_results.json` / `junit_report.xml` out of the container (they were never created) and recorded `status=success`.

## Key evidence

Host clone succeeded, but the docker cp into the container failed due to a path mismatch (run.log):
```
lstat /opt/runanything/src/input: no such file or directory
...
📋 Running command: git clone --depth=1 https://github.com/gip-inclusion/les-emplois.git ./rat_run_rat/input/repo/gip-inclusion/les-emplois
✅ Successfully cloned repo gip-inclusion/les-emplois
...
📋 Running command: docker cp /opt/runanything/src/input/repo/gip-inclusion/les-emplois/. rat_gip_inclusion_les_emplois_024c2081:/repo
Container start faild: Command 'docker cp /opt/runanything/src/input/repo/gip-inclusion/les-emplois/. rat_gip_inclusion_les_emplois_024c2081:/repo' returned non-zero exit status 1.
```

Agent finds an empty repo and stops (trajectory tail):
```
### Observation:
Running `ls -la /repo`...
total 8
drwxr-xr-x 2 root root 4096 Jun  5 15:39 .
drwxr-xr-x 1 root root 4096 Jun  5 17:08 ..
...
### Thought: The repository is empty - there's no project code to configure. Since there's no project to work with, I should stop and report this.
### Action:
```bash
    stop
```
```

No pytest tool ever ran (`tool_stats.json` — only `stop`):
```
{ "stop": { "count": 1, "success_count": 1, ... } }
```

No execution/collection results existed to copy out (run.log tail):
```
⚠️  Failed to copy Pytest execution results: ... Could not find the file /repo/logs/run_pytest_results.json ...
⚠️  Failed to copy Pytest collection results: ... Could not find the file /repo/logs/run_pytest_collect_results.json ...
⚠️  Failed to copy JUnit XML report: ... Could not find the file /repo/logs/junit_report.xml ...
🛑 Stopping container...
✅ Completed. Time: 97.06s
[done  ] gip-inclusion/les-emplois  status=success
```

`construct_test_result.json` is NOT valid JSON — it literally contains the string `File not found` (14 bytes), so no discovery/`has_tests` info is available:
```
$ xxd construct_test_result.json
00000000: 4669 6c65 206e 6f74 2066 6f75 6e64       File not found
```

## Reconciliation & caveats
- **Total vs breakdown + subtests:** `pytest_total_tests=0`, `passed/failed/errors=0`. No execution summary file exists (`run_pytest_results.json` absent). No subtests possible. `subtests_detected=0`.
- **Collection vs execution:** `pytest_collect_success=false` and `pytest_executed=false`. Neither `run-pytest-collect` nor `run-pytest` was ever invoked (tool_stats has only `stop`); both result files are absent from the container. There is no "N tests collected" line to reconcile. `uncollectable_classes=0` (no collection run).
- **Warnings / uncollectable classes:** No pytest `warnings summary` block exists because pytest never ran. `warnings_count=0`, `uncollectable_classes=0`. (The only "warnings" in the log are harness-level: the failed `docker cp` and the failed copy-out of nonexistent result files.)
- **Hollow-success check:** `pytest_pass_rate=0.0` and `pass_rate_exclude_code_issues=0.0` — they agree, both zero. This is NOT a high-pass-rate hollow success; it is the opposite — a zero-test run that the harness nonetheless flags `status=success`/`success=true`. The "success" here reflects only that the agent terminated via `stop` without an exception, not that any environment was set up or any test passed. Whether the real repo has tests is unknown because the repo (which does contain a real Django project: pyproject.toml, Makefile, docker-compose.yml per the host-side analysis) never reached `/repo` inside the container.
- **Category mismatch:** `_category=winnable_large` — the upstream repo is a substantial Django app, but it was never made available to the agent, so "winnable" was never actually testable in this run.

## Takeaway
This run tells us nothing about RAT's real capability on gip-inclusion/les-emplois, because the agent never received the code. A harness-side path mismatch (clone to relative `./rat_run_rat/input/...` vs `docker cp` from absolute `/opt/runanything/src/input/...`) left the container's `/repo` empty, and the `docker cp` failure was swallowed rather than aborting the run. The agent behaved sensibly given an empty workspace — it explored thoroughly and stopped — but the headline `status=success` is misleading: zero tests were collected or executed and no environment was configured. This is a false/hollow success driven entirely by infrastructure, and it should be excluded from any real pass-rate accounting (it is neither a genuine pass nor a fair agent failure).

## Fixability
**harness_bug** — The clone destination and the `docker cp` source paths disagree (`./rat_run_rat/input/...` vs `/opt/runanything/src/input/...`), so the repo is never injected into the container; the resulting `docker cp` non-zero exit is logged but not treated as fatal, letting the run finish as `status=success` on an empty `/repo`. Fix: make the `docker cp` source point at the actual clone path (or normalize both to one absolute base), and treat a failed repo-injection `docker cp` as a hard run failure (set `failure_reason` and mark the instance error) instead of proceeding to the agent loop. Until then, exclude this instance from pass-rate metrics.
