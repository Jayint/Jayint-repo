# The ESSR denominator dilemma — collect-only vs test-run

**Status:** open design question. All facts below verified on VM `167.233.64.96` (read-only), 2026-07-10, across 85 `junit_report.xml` files and ~231 `run_pytest_results.json` under `/opt/runs`. Five independent adversarial verifiers (Opus) were run against the claims; corrections are recorded in the last section.

---

## 1. The circularity

RAT is an **environment-setup** benchmark: the agent must build a container where a repo's tests run. It is scored by

```
ESSR = mean over repos of ( tests passed / tests that should have run )
```

The denominator — "tests that should have run" — is only knowable **by running the tests**, which requires a working environment, **which is the thing being measured.**

RAT resolves this by taking the denominator from *the agent's own pytest run*:

```python
# eval/common/scorers.py:123-126
effective_total = total_tests - skipped     # from the agent's junit XML
pass_rate       = passed / effective_total
```

This makes the denominator a function of the agent's success. The metric therefore **rewards collecting fewer tests.**

### The paper says something different

`/opt/harness/results/RAT_BASELINE_FIDELITY_REPORT.md:114`, verified against the PDF:

> ESSR = N_pass / N … refine … ESSR = N_pass / N_verified

where `N_verified` is *"the total number of existing unit tests, which serve as the gold-standard baseline."*

**The implementation does not do this.** `total_tests - skipped` from the agent's container is not "the total number of existing unit tests." Everything below is downstream of that one substitution.

---

## 2. Evidence that the metric inverts

| repo | run A | run B | verdict |
|---|---|---|---|
| `containers/podman-compose` | 440 passed / 440 collected = **1.000** | 440 passed / 512 collected = **0.859** | identical numerator; run A excluded 74 integration modules via an agent-written `pytest.ini` |
| `unit8co/darts` | 8,357 / 8,357 = **1.000** | 9,845 / 9,863 = **0.998** | run A skipped 260 modules at import (`collection skipped`); run B ran them |
| `ArchipelagoMW/Archipelago` | broken env → **0.120** | working env → **0.017** | working env loads the 83-world registry, subtests fire, denominator inflates 55× |

Across `/opt/runs`, **31 of 44** repos measured under 2+ runs have a different `effective_total` each time. Archipelago spans 372 → 236,225.

---

## 3. Why `--collect-only` does not rescue it

**(a) Collection is itself environment-dependent.** A module that won't import isn't collected. So the collect count is a function of the environment too.

**(b) It is strictly *less* informative than the test run.** On `podman-compose`, both runs collected **440**. Identical. The full run's `total_tests` (512 vs 440) is the only artifact that records that 74 modules exist and are broken — as 72 `<error message="collection failure">` nodes.

**(c) Collect-only cannot see skips.** `@pytest.mark.skip` and `@pytest.mark.skipif` tests **are collected**; they skip at *setup*. Only `pytest.importorskip` and `pytest.skip(allow_module_level=True)` drop out during collection. So "not skipped by the author" is invisible to `--collect-only`. **`N_verified` requires a full run.**

**(d) RAT's collect tool is a boolean, not a count.** `libkit/tools/run_pytest_collect.py:109` runs `pytest --co -q` **without** `--continue-on-collection-errors`, so one broken module → `returncode=2` → `success=False`. The JSON stores `success`, `returncode`, `errors[]`, `raw_output` — **no count**. `pytest_collect_scorer` (`scorers.py:170`) reads only the boolean.

> Side effect: the `pytest.ini` that excluded `tests/integration` also flipped `pytest_collect_success` from `False` to `True`. Two reported metrics improved from one exclusion.

---

## 4. Why the full test run does not rescue it either

There are **three different counting units** live in the codebase, and the scorer mixes them.

| unit | where | what it counts |
|---|---|---|
| `<testsuite tests=...>` attribute | `run_pytest.py:218` → `total_tests` | pytest's **report counter** (`numtests = stats[passed]+failure+skipped+error − cnt_double_fail_tests`, `_pytest/junitxml.py:647-662`). Subtest reports bump `stats` but reuse one node. |
| `<testcase>` element count | `run_pytest.py:224-275` → `passed` | distinct **test functions** |
| terminal summary regex | `parse_pytest_output` (fallback) | sum of the six summary categories |

`parse_junit_xml` reads `total_tests / failed / errors / skipped` from **attributes** and derives `passed` by **counting elements**. That is the parse bug.

**Archipelago:** `<testsuite ... skipped="249" tests="236474">` containing **4,315** `<testcase>` elements.

| pairing | value |
|---|---|
| RAT: 4,026 elements ÷ 236,225 reports — **mixed units** | **0.017** |
| node-consistent: 4,026 ÷ (4,315 − 249) | **0.990** |
| report-consistent: 236,185 ÷ (236,474 − 249) | **0.9998** |

No self-consistent pairing yields 0.017. The conclusion ("Archipelago passed ~99% of what it ran") is robust to which unit you pick.

**Compression.** An unimportable module emits **one** node. Measured:

| repo | `def test_` in module(s) | nodes emitted |
|---|---|---|
| `supabase-py` | 331 | 22 |
| `connectors` | 2,003 | 327 |
| `ezdata` | 44 | 1 |
| `promnesia` (`tests/demos.py`) | 8 | 1 |

Some collection-failure nodes are *package* `__init__.py` collectors — they compress an entire directory subtree.

**Six repos are scored via `regex_fallback`.** `mlflow/mlflow`'s `junit_report.xml` is **empty**, `parse_junit_xml` raises, and `run_pytest` silently falls back to the third unit.

---

## 5. Why static counting does not rescue it

`def test_` vs collected nodes, measured across 44 checkouts:

| repo | `def test_` | nodes | ratio |
|---|---|---|---|
| `mlflow/mlflow` | 12,879 | 8 | 0.001× |
| `baserow/baserow` | 8,155 | 77 | 0.01× |
| `NewFuture/DDNS` | 912 | 912 | 1.00× |
| `crytic/slither` | 293 | 7,296 | 24.9× |
| `ArchipelagoMW/Archipelago` | 1,414 | 236,474 | **167×** |

Neither an upper nor a lower bound. Causes: `test/worlds/__init__.py` defines `load_tests()` (unittest's dynamic suite hook) over an 83-entry registry populated at import; `slither` uses `@pytest.mark.parametrize("test_item", ALL_TESTS)` where `ALL_TESTS` is built by `Path(...).rglob("*.sol")`. Conversely `subTest` and Hypothesis `@given` **collapse** many logical cases into one node, so `def test_` can over-count too.

About ten of 44 repos land within 10% of 1.00×. Grep is *sometimes exactly right*, which is worse than reliably wrong.

---

## 6. The proposal on the table

> `pass_rate = passed / (total unit tests in the repo NOT skipped by the author)`

This is the paper's `N_verified`. It draws the exclusion at **author intent** rather than **environment outcome** — so a test skipped because the agent didn't install Redis stays in the denominator and counts as a miss. That is the provisioning signal the benchmark exists to measure.

**Operationalization (no message parsing required):**

```
N_verified = (elements collected in a reference env) − (elements skipped in a reference env)
```

Justification: anything *still skipped* in a properly-provisioned reference environment is skipped for a reason no agent can influence — author `@pytest.mark.skip`, `xfail`, opt-in flag (`--integration`), wrong platform (`Test requires Windows`), absent hardware (`requires AMD device`). Anything that **runs** in the reference but is skipped in the agent's env is the agent's failure (`requires torch`, `Docker is not available`, `Oracle deps not installed`).

Skip reasons are free-text `reason=` strings with no machine-readable marker. Corpus distribution (5,613 `<skipped>` nodes): 4,878 `type="pytest.skip"`, **661 `type="pytest.xfail"`**, 74 `collection skipped`.

**Verified that the skip set really does shrink with a better env:** `feast` skipped 30 tests `'Oracle deps not installed'` in a worse run and 0 in a better one. `darts` skipped 260 modules at import in a worse run, 0 in a better one.

**Effect on the pathological cases:** podman-compose `N_verified = 698` → rescue-c4's `1.000` becomes `440/698 = 0.630`. PerfKit `N_verified = 2,552` → rescue-c4's `0.208` becomes `0.029`, and its good run becomes `0.984`.

---

## 7. Where `N_verified` comes from

**Acceptance gate for a reference run: `collection_errors == 0` AND `collection_skips == 0`.** If nothing failed to import and no module opted out at import, the element set *is* the complete node set. That's a certificate, not a guess.

By that gate, **27 of the 35 repos that ever produced a parsable JUnit already have a qualifying run on disk**:

| repo | elements | skipped | N_verified |
|---|---|---|---|
| `crytic/slither` | 7,296 | 21 | 7,275 |
| `python-semantic-release` | 5,248 | 723 | 4,525 |
| `aiidateam/aiida-core` | 3,745 | 36 | 3,709 |
| `PerfKitBenchmarker` | 2,563 | 11 | 2,552 |
| `containers/podman-compose` | 736 | 38 | 698 |

**8 repos fail the gate** and need a real reference env: `connectors` (340 collection errors), `vizro` (53), `supabase-py` (22), `feast` (22), `darts` (9 collection-skips), `Archipelago` (3), `tinygrad` (1), `anthropic-sdk` (1).

**~15 never produced a parsable JUnit**: `mlflow` (empty XML), `baserow`, `azure-cli`, `pretix`, `checkmk`, plus the language-misrouted set. No shortcut exists. Build a reference env or drop them from the scored set.

**Sources, in preference order:** repo's `.github/workflows` at the pinned SHA (≈27/45 checkouts have one that invokes pytest/tox; GitHub service containers → compose file) → `tox.ini`/`noxfile.py` → repo `Dockerfile`/`.devcontainer/` → by hand.

---

## 8. Open questions for the new agent

1. **Include tests that *fail* in the reference env?** The stated definition says yes (they aren't author-skipped) → per-repo ceiling < 1.0. SWE-bench's `PASS_TO_PASS` says no → ceiling 1.0, but service-dependent tests leave the denominator unless the reference provisions the service, killing the provisioning signal. **Unresolved.**

2. **Residual leak:** an env-caused `skipif` in the *reference* silently deflates `N_verified` while passing the collection gate (feast/Oracle). Mitigations: take `max(elements − skipped)` over gate-passing runs; read the few dozen distinct skip reasons per repo by hand once.

3. **Numerator integrity.** A fixed denominator blocks *shrinking*, not *inflating*. The agent has `edit-file` (`libkit/tool.py:133`) and demonstrably wrote a `pytest.ini` into the repo. Nothing stops it rewriting a failing test to `assert True`. **Required fix, upstream of any metric:** restore the pristine test tree (`git checkout tests/` @ pinned SHA, discard agent-written `pytest.ini`/`conftest.py`) and run pytest **from the harness at a fixed rootdir** — as `repo2run_model.py` already does (`docker exec -w /repo`) and `rat_model.py` does not. This also stabilizes node IDs, which drift only because the agent chooses the rootdir.

4. **Unit consistency.** `N_pass` and `N_verified` must both be `<testcase>` element counts. Add `-o junit_family=xunit1` so pytest also emits `file` and `line` per testcase — `record_testreport` computes them (`junitxml.py:125`) but the default `xunit2` family strips them (`L74-83, L135-144`).

5. **Pin the SHA first.** The dataset (`/opt/runs/rat_python50.json`) stores `full_name`, `clone_url`, `default_branch` — **no commit, no test count**. Seven of 44 repos have two distinct `head_sha` values across runs (`PerfKitBenchmarker`, `tinygrad`, `baserow`, `pretix`, `checkmk`, `Wegent`, `ezdata`). `PerfKit`'s `N_verified = 2,552` is currently a number for an unspecified commit.

6. **Coverage vs honesty.** 27 certifiable / 8 fixable / ~15 unknown. Does the benchmark shrink, or does it keep reporting `baserow` at 0.779 off 53 collected tests?

7. **Report a pair, not a scalar.** `(coverage, pass-yield)`. `0.847` from "ran everything, failed some" and `0.847` from "ran half, passed all" are different failures.

---

## 9. Corrections — do NOT inherit these errors

Five adversarial verifiers falsified the following, which appear in earlier notes:

- **`classname == ""` is not an invariant for collection failures.** It held on all 4,268 error nodes in the corpus, but a class-level `parametrize` mismatch produces `classname='test_classcollect'` with `message="collection failure"`. **Classify on `message`, not `classname`.**
- **`total = passed + failed + skipped + setup_errors + collection_failures` is wrong** — fails on 8/84 files. Missing: **teardown errors** (`message='failed on teardown with "..."'`, 45 in corpus), **internal errors** (`classname="pytest"`, 2), and **double-fail expansion** (call-failure + teardown-error splits into *two* elements while `tests` subtracts 1 → `anthropic-sdk` has 4,182 elements vs `tests=4179`).
- **pytest never emits `<failure>` and `<error>` on the same testcase.** But `aiida-core` has **12 testcases with two `<error>` children** (setup + teardown). `parse_junit_xml` uses `.find("error")` — **use `findall`**.
- **661 `xfail`s are folded into `skipped`** and thus deleted from the current denominator. Non-strict `xpass` is invisible (recorded as a plain pass).
- **The 236,474 attribute is not spurious.** It is a legitimate count of subtest *reports*; `stats["passed"] = 236,185` is recoverable as `236474 − 1 − 39 − 249`. Both numerator and denominator are in the wrong unit, not just the denominator.
- **"11 repos where the agent never tested" is wrong.** RAT re-infers language with an LLM at runtime; **10 Python repos were classified `node`** and tested with `npm test`, one (`Qiskit`) as `rust`. They produced `run_npm_test_results.json`, so `pytest_executed=False`, so they vanished from a Python benchmark's headline.
- **repo2run's exclusions are not mostly build failures.** Of 23, **11** are its config agent never emitting a Dockerfile; only 7 are `docker build` failures. Both baselines have agent-determined denominators.
- **A union-of-observed gold set is fatal, not a fallback.** `baserow`'s ceiling would be 53 nodes against 8,233 test functions; 4 repos never executed pytest at all; freezing locks the benchmark to its weakest historical environment, and recomputing silently lowers every past score.
- **Environment divergence between two *healthy* runs is intrinsic**, not flakiness. 12 of 26 multi-run repos diverge (`aiida-core` Jaccard 0.508, 1,604 tests differ on rabbitmq/postgres; `typer`'s `test_path_convert_failures[...]` passes as non-root and fails as root). True flakes are tiny (`Scrapling` 1 test, `sooperset` 2). Reruns fix the wrong thing.

---

## 10. Key file references

| what | where |
|---|---|
| pass_rate scorer | `/opt/runanything/src/eval/common/scorers.py:123-126` |
| the parse bug | `/opt/runanything/src/libkit/tools/run_pytest.py:218-275` |
| pytest invocation | `/opt/runanything/src/libkit/tools/run_pytest.py:456-492` |
| collect tool (boolean only) | `/opt/runanything/src/libkit/tools/run_pytest_collect.py:109` |
| pytest's own junitxml | `/opt/rat_venv/lib/python3.10/site-packages/_pytest/junitxml.py:125, 190-191, 209-228, 509-517, 647-662` |
| ESSR aggregation (÷exec) | `/opt/harness/run_rat_benchmark.py:423, 429-438` |
| harness rescorer | `/opt/harness/scripts/compute_essr.py` (`official_pass_rate`, `score_agent`) |
| rat model (agent runs pytest) | `/opt/runanything/src/eval/models/rat_model.py` |
| repo2run model (harness runs pytest) | `/opt/runanything/src/eval/models/repo2run_model.py:222-300` |
| agent's `edit-file` tool | `/opt/runanything/src/libkit/tool.py:133` |
| dataset (no SHA, no counts) | `/opt/runs/rat_python50.json` |
