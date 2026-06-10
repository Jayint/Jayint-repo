# RAT harness patches

The RunAnyThing (RAT) harness is installed on the runner box as a **gitless unzip**
(`RAT_ROOT`, default `/opt/runanything/src`). Fixes to it are not under version control
there, so we keep them here as patches and (re)apply them on every deploy.

## 0001-rat-harness-repo-path-fix.patch

**Bug:** `libkit/environment.py` copied the cloned repo into the eval container with
`docker cp {project_directory}/input/repo/{full_name} ... :/repo`, where
`project_directory` is the harness install dir (`RAT_ROOT`). But repos are cloned to
`{self.root_path}/input/repo/{full_name}` (the run's `--root-path`). When `root_path`
!= `RAT_ROOT` (i.e. any normal run), the `docker cp` source does not exist, the copy
fails, and `/repo` is empty. The agent then finds no tests, a placeholder test is
generated, and the run reports a hollow "success". This silently invalidated the entire
`run-2026-06-06-k12` baseline (50/50 empty `/repo`).

**Fix:** swap the repo-copy source from `{project_directory}` to `{self.root_path}` at
both sites — `create_container` (line ~750) and `rollback_to_temp_image` (line ~1434).
The `docker cp {project_directory}/libkit/tools ... :/home` lines are left unchanged
(tools really do live in the harness dir).

**Proof:** real `--model rat` run on `EnableSecurity/wafw00f` went from 3 hollow tests
(broken k12) to 48 real tests passing, `/repo` fully populated.

## Metric-collection correction patches (0002-0005) — the CORRECTED VARIANT

Patch `0001` was a **validity** fix (without it `/repo` was empty and the whole run was
hollow). Patches `0002`-`0005` are a different, opt-in category: they correct four
**metric-collection** behaviors that are byte-identical to the paper's published code
(`/tmp/ratref`) and that produced the paper's numbers, but that are methodologically
indefensible as a headline. They are a **clearly-labeled corrected variant**, NOT a
replacement for the paper-faithful baseline.

### Faithfulness tradeoff (read this first)

The paper's headline Python ESSR is a **macro mean of `pytest_pass_rate` over repos
where pytest executed** (`generate_latex_report.py:282-284,326-329`), with the special
case (`scorers.py:112-120`) that a timeout-only / zero-tests repo is scored `1.0`. On our
50-repo fixed re-run this reference recipe yields **0.7229 across 45 executed repos**.
Of those 45, **9 are phantom `1.0`s** produced purely by the timeout heuristic
(nexent, verifiers, aiida-core, copier, les-emplois, karaoke-gen, websockets,
scylla-cluster-tests, darts). If those 9 are scored `0.0` instead, the macro ESSR drops
to **0.5229** (-0.20 absolute, ~-28% relative) — the single largest source of optimism
in the headline.

We **PRESERVE** the paper-faithful baseline: every patched file is backed up on the VM as
`<file>.bak-metricfix` (sha256-verified identical to `/tmp/ratref`), and patch 0002 keeps
180s exactly reproducible via `RAT_PYTEST_TIMEOUT=180`. The corrected variant is what an
honest scoreboard should report; the baseline is what reconciles with the paper.

### 0002-pytest-timeout-configurable.patch — `libkit/tools/run_pytest.py`

**Behavior:** `main()` (line ~635) hardcoded `timeout = 180`, silently overriding
`run_pytest()`'s own `600` default and contradicting the paper TEXT's stated 600s. Large
suites that legitimately need >180s were killed; via the scorer's timeout heuristic they
became phantom perfect passes (the agent itself recognized this — aiida-core's log shows
the LLM running `sed -i s/timeout=180/timeout=600/`).

**Fix:** read the subprocess timeout from `RAT_PYTEST_TIMEOUT` (seconds), defaulting
**higher (1800s)** so real totals are recorded. `RAT_PYTEST_TIMEOUT=180` exactly
reproduces the paper-faithful baseline; `=600` matches the paper text.

**Evidence (first-hand, on VM):** env resolution `unset->1800`, `=180->180`, `=600->600`;
and the live patched `run_pytest()` on a slow suite returns `total_tests=0,
{TimeoutError:1}` at a 3s cap vs `total_tests=3, passed=3` at the corrected default.

### 0003-scorer-timeout-not-pass.patch — `eval/common/scorers.py`

**Behavior:** the `total_tests==0 AND error_breakdown=={TimeoutError:1} -> pass_rate=1.0`
heuristic (lines ~112-120 and ~135-141, both the `pytest_pass_rate` and the S2-style
`pass_rate_exclude_code_issues` branches). "No error within timeout" is NOT "all tests
pass"; it rewards suites too slow to finish.

**Fix:** score such repos `0.0` (unverified), in BOTH branches, and add a
`pytest_timeout_unverified: true` flag so honest scoreboards can report/exclude them
separately rather than silently counting them as either pass or fail.

**Evidence (first-hand, on VM, real recorded inputs):** against the *actual* recorded
`run_pytest_results.json` for `PrimeIntellect-ai/verifiers` and `copier-org/copier`
(both `total=0, {TimeoutError:1}`): OLD scorer loaded from `.bak-metricfix` returns
`pytest_pass_rate=1.0` (and `pass_rate_exclude_code_issues=1.0`); the patched scorer
returns `0.0` with `pytest_timeout_unverified=True`.

### 0004-results-file-recursive-glob.patch — `libkit/codeagent.py`

**Behavior:** `_copy_test_results_from_container` / `_copy_junit_xml_from_container`
docker-cp from a fixed `/repo/logs/<file>`. If the agent ran pytest from a subdirectory
(e.g. `microsoft/markitdown` ran from `/repo/packages/markitdown`, writing
`/repo/packages/markitdown/logs/run_pytest_results.json`), the fixed-path copy found
nothing and the scorer silently returned `0.0` / `pytest_executed=false` — a *false*
zero (deflationary). markitdown's own run.log shows `332 passed` then
`Failed to copy ... Could not find /repo/logs/run_pytest_results.json`.

**Fix:** before giving up, search the container recursively
(`find /repo -type f -path '*/logs/<basename>'`, newest by mtime) via `docker exec` and
copy the found file. Helper `_find_newest_in_container` + `_copy_one_with_fallback`.

**Evidence (first-hand, on VM):** reproduced markitdown's container layout (results only
in the subdir, `/repo/logs/` absent). OLD fixed path MISSES; the NEW recursive `find`
(the exact shell the patch runs) recovers the `332 passed / 336 total` file.

### 0005-language-detection.patch — `libkit/utils/language_detector.py`

**Behavior:** `detect_language` trusts the GitHub API's majority-bytes verdict. Python/
infra repos get mislabeled `node` (e.g. `conor-is-my-name/n8n-autoscaling` -> `node`
despite **no package.json anywhere, zero .js/.ts files, 4 .py files, 2 requirements.txt**).
pytest then never runs and a vacuous `npm test: exit 0` can fake a pass.

**Fix:** when the API verdict is `node`, cross-check the local tree
(`_looks_like_python_not_node`): if there is NO `package.json` anywhere AND the repo has
substantial Python (any `requirements*.txt`/`setup.py`/`pyproject.toml`/`setup.cfg`/
`Pipfile`/`poetry.lock`, or >=3 `.py` files), override to `python`. Conservative: a
genuine polyglot (has `package.json` AND Python) is left as `node` (paper-faithful).

**Evidence (first-hand, on VM, real input tree):** with the documented API verdict
`node`, the OLD detector (from `.bak-metricfix`) returns `node`; the patched detector
returns `python` for `conor-is-my-name/n8n-autoscaling`.

## How it's applied

`deploy.sh` applies `0001` automatically on every `--apply` / interactive deploy, and
on demand via `./deploy.sh --patch-harness`. The applier is idempotent and safe:
- already fixed  -> no-op (`HARNESS_ALREADY_FIXED`)
- unrecognized harness version (no buggy marker) -> refuses to touch it
- otherwise -> backs up `environment.py` first, `git apply`s the patch, then `py_compile`s.

Override the harness location with `RAT_ROOT_BOX=/path ./deploy.sh --patch-harness`.

Patches `0002`-`0005` were applied on the VM at `/opt/runanything/src` with `patch -p1`
after backing up each touched file to `<file>.bak-metricfix` (fixed suffix), then
`py_compile`d. They apply cleanly against the reference baseline (`git apply --check`
passes for all four). To run the paper-faithful baseline with these files present, set
`RAT_PYTEST_TIMEOUT=180` (restores 0002's behavior); 0003-0005 only change cases that the
baseline scored as artifacts (phantom timeouts, subdir-results misses, mislabeled-node
Python repos), so they do not alter the 0.7229 reconciliation for the 36 non-phantom
executed repos — they correct the 9 phantoms and the markitdown/n8n false zeros. The
original numbers in `rat_run_rat_fixed/` are untouched; the `.bak-metricfix` backups make
the baseline fully restorable.
