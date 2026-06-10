# RAT Benchmark — Baseline Fidelity Report

**Date:** 2026-06-07
**Scope:** Does our VM + runner collect RAT benchmark data the *same way* as the paper's
published reference code? Is our baseline method valid? What is the canonical baseline
number to aim toward? What did we fix and why?

**Subject run:** `/opt/rat-bench-integration/rat_run_rat_fixed/output/<org>/<repo>/` —
the fixed 50-repo re-run on our curated hard Python subset
(`datasets/rat_python_hard_subset.json`), driven by `run_rat_benchmark.py --model rat`.

---

## 1. Verdict: FAITHFUL, WITH KNOWN CAVEATS

**Our data collection is faithful to the paper's published implementation.** The
metric-bearing code we run is byte-identical to the reference repo, with exactly one
intentional correctness fix. The differences between our reported headline number and a
naive read of the paper come from (a) a *different aggregation denominator* in our own
runner, and (b) two *measurement artifacts that live inside the paper's own code* (a
hardcoded 180 s pytest timeout and a "timeout-as-pass" heuristic). All three are
identified, quantified, and addressed below.

### 1.1 Byte-identity finding (front and center)

Every metric-collection file on the VM harness (`/opt/runanything/src`) is byte-identical
to the paper's reference repo, **except `libkit/environment.py`** (our `/repo`-population
fix). I verified this directly via MD5 (independently re-checked this session):

| File | Reference MD5 (`/tmp/ratref`) | VM original (`.bak-metricfix`) | Identical? |
|---|---|---|---|
| `eval/common/scorers.py` | `430915b36d6075243ecb12814fe2101f` | `430915b36d6075243ecb12814fe2101f` | YES |
| `eval/common/utils.py` | `906ddac625e52ee80c69dae1b48ad03a` | (= live VM `906ddac6…`) | YES |
| `libkit/tools/run_pytest.py` | `cb5e39bf80618fb0fca63d71a57a6967` | `cb5e39bf80618fb0fca63d71a57a6967` | YES |
| `libkit/language_config.py` | `5a20614c8a5feac82e8f07ad14c289af` | (= live VM `5a20614c…`) | YES |
| `libkit/utils/language_detector.py` | `b65bac468de62b6d12009824c269b1dd` | (live VM `7266c145…` = patch 0005) | YES (pre-patch) |
| `libkit/codeagent.py` | `9f85e50884b166720e79d1cbc559e5ae` | (live VM differs = patch 0004) | YES (pre-patch) |

Notes:
- `eval/common/utils.py` and `libkit/language_config.py` are still byte-identical *live*
  on the VM (we never patched them): live VM MD5 = reference MD5.
- `scorers.py`, `run_pytest.py`, `language_detector.py`, `codeagent.py` are **currently
  patched live** on the VM (the metric-fix variant, §4). Their **pre-patch state is
  preserved** as `.bak-metricfix` backups, and those backups' MD5s match the reference
  exactly (verified this session for `scorers.py` and `run_pytest.py`). So the
  *as-measured* fixed run (`rat_run_rat_fixed`) was produced by the **unmodified paper
  scorer/run_pytest** — the patches were authored *after* that run, as variants.
- The only file whose *measured-run* bytes differed from reference is
  `libkit/environment.py`, lines 750 and 1434. Confirmed live on VM:
  - L750: `docker cp {self.root_path}/input/repo/{self.full_name}/. ...:/repo`
  - L1434: `docker cp {self.root_path}/input/repo/{self.full_name} ...:/repo`
  - Reference used `{project_directory}` instead of `{self.root_path}`. Under our
    orchestration that path resolved empty, leaving container `/repo` unpopulated — the
    root cause of the prior *invalid* baseline (hollow placeholder passes on empty repos).
    This is a correctness fix, not a metric change: it does **not** touch the
    test-results copy-out path or any scorer.

**Confidence: HIGH.** MD5-level verification; the scorer/run_pytest that produced the
numbers is provably the paper's own code.

### 1.2 How we drive the harness (orchestration divergence)

We do **not** call the reference `env_main_batch.py` / `eval_runner.py` / Weave / W&B
stack. `run_rat_benchmark.py` imports the harness scorer callables directly
(`run_rat_benchmark.py:72`) and the model class (`:73`), prepends the RAT root to
`sys.path` (`:71`), runs each repo via our own `_run_one()` (`:146-225`), and scores with
the imported scorers at `:204-209`:

```python
row = {**out, "_category": category,
       **success_scorer(out), **pytest_collect_scorer(out), **pytest_pass_rate_scorer(out)}
```

The **scorer bytes are identical**; only the call site, scheduling
(`ThreadPoolExecutor` + subprocess fan-out), and persistence (`_result_row.json` instead
of W&B Weave) differ. This is a **medium-severity** divergence: it changes *where results
are stored and how repos are scheduled*, not *how any metric is computed*. Each repo is
scored by the exact same function the paper used.

---

## 2. The Baseline Numbers

All numbers below are **re-derived independently this session** from the 50
`_result_row.json` files in `rat_run_rat_fixed`, and match
`PAPER_CODE_ESSR_FOR_OUR_RUN` and `results/essr_per_repo_paper_method.csv`. Mirror of
`generate_latex_report.py:282-284` (accumulate `pytest_pass_rate` when
`pytest_executed==True`) and `:326` (`avg_pass_rate = total / pytest_executed_count`).

| # | Number | Value | Macro/Micro | Denominator | Precise meaning |
|---|---|---|---|---|---|
| (a) | **Paper-faithful ESSR, our run** | **0.7229 (72.3%)** | **Macro** | ÷45 (pytest_executed=True) | The headline ESSR the paper's *own aggregation* produces on our 50-repo run. Mean of per-repo `pytest_pass_rate` over the 45 repos where pytest executed. Includes the 9 timeout-phantom 1.0s. **This is the number directly comparable to the paper's method.** |
| (a′) | Paper-faithful, exclude-code-issues | 0.7658 (76.6%) | Macro | ÷45 | Same aggregation but using `pass_rate_exclude_code_issues` (the S2-style denom). **Not** the headline — the paper's headline uses `pytest_pass_rate`. Shown for completeness. |
| (b) | **Honest / corrected ESSR** | **0.5229 (52.3%)** | **Macro** | ÷45 | (a) with the 9 timeout-phantom repos re-scored 0.0 (they ran zero verified tests). Removes the single largest source of optimism. **−0.20 absolute, ≈ −28% relative vs (a).** |
| (c) | Paper's reported RAT (reference only) | 63.2 | Macro | full Python set | The paper's Table 2 RAT (DeepSeek-V3) Python-overall number on the **full** RATBench Python set — a *different, larger, un-curated* repo population and a *different agent* (RAT, not our DockerAgent in the original design). **Not expected to equal our number.** |
| (d) | Our runner's printed mean | 0.6506 (65.1%) | Macro | ÷50 (ALL rows) | What `aggregate()` (`run_rat_benchmark.py:254-255`) prints: divides by **all 50** repos, counting the 5 not-executed repos as 0.0. **This is the wrong denominator vs the paper** (see §3 / divergence MAJOR). |
| (e) | Micro pooled pass rate | 0.9617 (96.2%) | **Micro** | pooled tests | `sum(passed)/sum(passed+failed+errors)` over executed repos = 11112/(11112+314+128). A *per-test* rate, **not** the paper's metric. High because repos that actually finished tests pass almost all of them; it ignores per-repo weighting and the timeout/not-executed repos. Shown only as a cross-check — do **not** report this as ESSR. |

### 2.1 Why these differ

- **(a) 0.7229 vs (d) 0.6506** — *denominator only*. Paper divides by the 45
  pytest-executed repos; our `aggregate()` divides by all 50 (the 5 not-executed repos
  contribute 0.0). Same field, same per-repo values — **purely an aggregation bug in our
  runner**, not a measurement difference. Fixing the denominator reproduces the paper
  method exactly.
- **(a) 0.7229 vs (b) 0.5229** — *the timeout-as-pass heuristic*. 9 of the 45 executed
  repos hit the 180 s pytest timeout, recorded `total_tests=0, error_breakdown={TimeoutError:1}`,
  and the scorer awarded them `pass_rate=1.0`. Re-scoring those 0.0 drops the macro by
  0.20. This heuristic is in the paper's own code (so it is *faithful*), but it is
  methodologically indefensible as a headline (§4-B).
- **(a) 0.7229 vs (c) 63.2** — *different population and system*. Our 50 is a curated
  *hard* subset over-weighted toward Repo2Run/RAT failure cases; 63.2 is the full Python
  set. The fidelity goal is **methodological identity**, not number-matching. The paper
  formula (page 5, verified in PDF: "ESSR = N_pass/N … refine … ESSR = N_pass/N_verified")
  is implemented identically; the populations are deliberately different.
- **(e) micro 0.9617** is far above the macro numbers because micro pools all tests and
  thus is dominated by a few large suites that pass nearly everything, while macro weights
  every repo equally (and the timeout/not-executed repos drag macro down). They answer
  different questions; the paper reports **macro**.

**Confidence: HIGH.** All 50 rows parsed; sum cross-check `sum(pytest_pass_rate)=32.53 /
45 = 0.7229`; micro `11112 / 11554 = 0.9617`; phantom set and not-executed set enumerated
below.

---

## 3. The Four Behaviors

For each: is it in the reference? was it active when the paper's numbers were produced?
effect on the number? our decision. **Fixing any of A/B/C/D-aggregation diverges from the
paper's published headline — that is the point, and it is called out explicitly.**

| # | Behavior | In reference? | Active in paper's numbers? | Effect on the number | Our decision |
|---|---|---|---|---|---|
| **A** | **180 s hardcoded pytest subprocess timeout** (`run_pytest.py` main(), reference L635 `timeout = 180`, overriding the `run_pytest()` signature default of 600 s; paper *text* says 600 s) | YES | YES — byte-identical code produced 63.2. Paper text says 600 s but published code uses 180 s. | Inflationary *via* its interaction with B: large suites (darts 8553 tests, aiida-core, nexent, copier, websockets…) get killed at 180 s and become phantom passes. 8 of our 9 phantoms hit exactly 180 s; aiida-core hit 600 s (the agent had bumped it). | **FIX AS VARIANT.** Patch 0002 makes it `RAT_PYTEST_TIMEOUT` (default 1800 s; `=180` reproduces the paper). **Preserve 180 s for the comparability baseline; raise it for honest measurement.** |
| **B** | **TimeoutError-only → `pass_rate=1.0` heuristic** (`scorers.py:112-120`, and again `:135-141` for exclude-code-issues): `total_tests==0 AND error_breakdown=={TimeoutError:1}` ⇒ 1.0 | YES | YES — present in the byte-identical scorer that generated the tables. **Not described in the paper**, so anyone reproducing from the text alone would *not* implement it and would get a lower number. | **Strongly inflationary — the single largest source of optimism.** 9/45 executed repos are phantom 1.0. Removing it: **0.7229 → 0.5229 (−0.20 absolute, ≈ −28% relative).** | **FIX AS VARIANT.** Patch 0003 scores these 0.0 in *both* branches and adds `pytest_timeout_unverified: true`. **Preserve in the paper-faithful baseline (required to reconcile with the paper); the corrected variant is the honest headline.** |
| **C** | **Results-file copy-out uses a fixed `/repo/logs/` path** (`codeagent._copy_test_results_from_container`, ref L243-273; scorer reads `{root}/output/{full_name}/run_pytest_results.json`, `scorers.py:88`). If the agent ran pytest from a subdirectory, the copy silently finds nothing. | YES | YES | **Deflationary (understates).** On our run, `microsoft/markitdown` wrote results to `/repo/packages/markitdown/logs/…`; the fixed-path copy missed them, so it recorded `pytest_executed=False, pass_rate=0.0` despite **332 passed, 4 skipped** in its run.log (true ≈ 1.0). At least 1 repo wrongly scored 0/excluded. | **FIX AS VARIANT.** Patch 0004 adds a recursive `find /repo -path '*/logs/<file>'` fallback after the fixed path. Recovers markitdown's 332/336. Does **not** affect the paper-faithful baseline (that run used the unmodified copy-out). |
| **D-detect** | **Language detection** (`language_detector.py`: GitHub-API majority-bytes → name_map; fallback local scan; default `python`). Mislabels mixed repos. | YES | YES | Causes repos to skip pytest entirely (recorded `pytest_executed=False`). On our run, `bruin-data/ingestr` (actually Go) and `conor-is-my-name/n8n-autoscaling` (Node) detected as node → ran `npm test` ("echo ok") → no pytest. Excluded from paper denom; counted as 0 in our ÷50. | **FIX AS VARIANT.** Patch 0005 cross-checks a "node" verdict against the local tree, overriding to python only when no `package.json` anywhere AND substantial Python present (conservative; genuine polyglot stays node). |
| **D-agg** | **Aggregation denominator** (our `aggregate()` divides by all rows; reference divides by `pytest_executed_count`) | NO — this is **ours**, not the reference | N/A | **0.6506 (÷50) vs 0.7229 (÷45).** Wrong denominator, not a different metric. | **FIX.** Report the reference macro (÷ pytest_executed) as the canonical headline; keep ÷50 only as a "coverage-penalized" secondary view, clearly labeled. |

**Key honesty statement:** Behaviors A and B are *in the paper's own published code* and
*were active when 63.2 was produced*. Therefore **63.2 itself is inflated by the same
180 s-timeout + timeout-as-pass mechanism.** Our corrected number (b) 0.5229 is **not**
comparable to 63.2 — it is comparable to a hypothetical "corrected 63.2" that the paper
does not report. When we publish a corrected baseline we are deliberately diverging from
the paper's headline methodology, and we say so.

---

## 4. Empirical Validation (recorded vs independent ground truth)

The empirical audit re-derived per-repo truth from `junit_report.xml`, `run.log` partial
runs, and `run_pytest_results.json`, independently of the recorded `pytest_pass_rate`.
I spot-verified the headline cases this session via direct junit parsing on the VM.

**Agreement rate:** the recorded scorer output is a *correct reflection of what the
scorer computed* in 100% of rows (the scorer is deterministic and byte-identical). But
**recorded value == independent ground-truth** fails for the timeout-phantom and
subdir-miss repos. Of the audit sample, the **worst offenders**:

| Repo | Recorded | Independent ground truth | True rate | Cause | Direction |
|---|---|---|---|---|---|
| `unit8co/darts` | 1.0 | 8553 tests, timed out at 180 s, 0 completed (junit absent) | ~0.0 (unverified) | timeout→1.0 | inflated |
| `aiidateam/aiida-core` | 1.0 | 3748 collectible, timed out (600 s); partial run showed "1 failed, 173 passed" → real failures exist | ~0.0 (unverified) | timeout→1.0 | inflated |
| `ModelEngine-Group/nexent` | 1.0 | 4385 tests/112 errors collected, 0 completed (junit absent) | ~0.0 (unverified) | timeout→1.0 | inflated |
| `scylladb/scylla-cluster-tests` | 1.0 | collection FAILED (returncode 2), 0 completed | ~0.0 (unverified) | timeout→1.0 | inflated |
| `copier-org/copier` | 1.0 | **junit: 1104 tests, 1 failed, 1093 passed** (verified this session) → 0.9991 | 0.999 | timeout→1.0 | *coincidentally ≈ correct* |
| `python-websockets/websockets` | 1.0 | partial runs show large majority pass; 14 HTTPProxy failures are env-specific; full suite hangs | ~0.85 | timeout→1.0 | inflated |
| `microsoft/markitdown` | **0.0 / not-executed** | **332 passed, 4 skipped** (run.log; junit written to subdir) | ~1.0 | subdir copy-miss (C) | **understated** |
| `bruin-data/ingestr` | 0.0 / not-executed | Go repo; `npm test`="echo ok"; no pytest | n/a (mislabeled) | lang-detect (D) | n/a |

**Findings:**
- **9/45 executed repos are phantom timeout passes** (verified this session):
  `nexent, verifiers, aiida-core, copier, les-emplois, karaoke-gen, websockets,
  scylla-cluster-tests, darts`. Of these, only `copier` is genuinely ≈1.0; the rest ran
  **zero verified tests**. junit_report.xml is **absent** for the phantoms (confirmed for
  darts, websockets, nexent), present only where pytest truly finished.
- **5 repos recorded `pytest_executed=False`** (excluded from paper denom, counted 0 in
  ÷50): `bruin-data/ingestr, conor-is-my-name/n8n-autoscaling, frappe/press,
  microsoft/markitdown, rayai-labs/agentic-ray`. At least `markitdown` (332 passing) and
  `agentic-ray` (15 passing) had real test data lost to the subdir / language-detect bugs.
- **Net effect:** the timeout heuristic (B) inflates by ≈ +0.20; the copy-miss (C) and
  language-detect (D) bugs *deflate* by excluding real passes. (a) 0.7229 nets these; the
  honest corrected (b) 0.5229 removes the B inflation but does **not** add back the C/D
  losses (those repos remain not-executed/0 pending the patched re-run).

**Confidence: HIGH** for the phantom set and the copier/markitdown ground truth
(directly verified this session). **MEDIUM** for the precise "true" rates of the
unfinished suites (darts, aiida-core, nexent) — they never completed, so true pass rate
is genuinely unknown; the audit's 0.0 is a conservative lower bound flagging "no verified
passing tests," not a measured value.

---

## 5. S1 / S2 / S3 Scenario Assignment

**The reference code does not implement scenario branching anywhere.** There is no
conditional on S1/S2/S3 in any staged or VM file. The paper (p.5, verified in PDF)
*defines* the scenarios by repo content:
- **S1 (Artifact-guided):** has unit tests AND functional containerization artifacts;
  `N_verified` = all existing tests (gold standard).
- **S2 (Artifact-free):** has tests but no containerization scripts; `N_verified` excludes
  inherent-code-defect failures.
- **S3 (Test-deficient):** neither tests nor scripts; `N_verified` = synthesized smoke
  tests / entry points.

**Which scenario is our run?** Our run is effectively **S1-style measurement** for the
headline: it uses `pytest_pass_rate = passed/(total_tests − skipped)`, i.e. the full
executed-test denominator (the S1 gold-standard denom), **not** the S2 exclude-defects
denom. The harness clones the repo Dockerfile but does **not** use it
(`rat_model` hardcodes `download_repo(use_repo_dockerfile=False)`) and never deletes it —
so it is not a clean S2 "artifact-free" setup either; it is an S1-style *scoring* applied
regardless of artifact availability.

**Is the assignment correct?** There is **no per-repo S1/S2/S3 assignment to be correct
about** — neither the reference nor our pipeline assigns scenarios. Our dataset has
`_category/_tier/_why` curation tags (e.g. `repo2run_weak_ci_service(S2)`) but these are
**heuristic curation labels, not harness-derived availability classifications**, and no
code reads them as scenarios. We **cannot reproduce the paper's Table 3 three-way split**
without independently classifying each repo by (tests present? artifacts present?). The
single headline number we produce corresponds to the paper's **Table 2 Python-overall
(S1-denominator)** metric, not the Table 3 per-scenario split.

**Confidence: HIGH** that no scenario logic exists in code; **HIGH** that our headline
uses the S1-style denominator.

---

## 6. Recommended Canonical Baseline

For all future RAT work on this subset, report **three** numbers, in this order:

1. **CANONICAL HEADLINE — Honest macro ESSR = 0.5229 (52.3%)**
   - Macro mean of per-repo `pytest_pass_rate` over `pytest_executed==True` repos
     (denominator = pytest_executed_count, the paper's denominator),
   - **with the timeout-as-pass heuristic disabled** (timeout-only repos scored 0.0 and
     flagged `pytest_timeout_unverified`), per patch 0003,
   - **and** the copy-out / language-detect fixes (patches 0004/0005) applied so real
     passes are not silently dropped — which will *raise* this number on a re-run by
     recovering markitdown/agentic-ray etc.
   - Rationale: this is the only number where every component is a *verified* test result.

2. **PAPER-FAITHFUL COMPARABILITY — Macro ESSR = 0.7229 (72.3%)**
   - The exact paper aggregation on the unmodified scorer/run_pytest (180 s + timeout
     heuristic), denominator ÷45. Report this **only** for apples-to-apples comparison with
     the paper's method; always annotate that it includes 9 unverified phantom passes.

3. **REFERENCE POINT — Paper RAT 63.2** (full Python set, different population/system).
   Never compare our absolute number to this as if same population; it is a methodological
   anchor only.

**Always also report:** `n_executed / n_total` (coverage, e.g. 45/50), the phantom-timeout
count, and the not-executed list, so the headline cannot be read in isolation.

**Fix the runner aggregation bug** (`run_rat_benchmark.py:254-255`): switch the headline
mean to divide by `pytest_executed_count`, and report the ÷n_total number separately as a
"coverage-penalized" view rather than as the headline.

---

## 7. Remaining Risks

1. **The corrected 0.5229 still has unrecovered understatement.** The C/D bugs were
   patched *after* the measured run, so markitdown (~1.0), agentic-ray (~15 passing), and
   any other subdir/mislabel victims still sit at 0/not-executed in the underlying rows. A
   patched re-run is needed to land the true corrected number — it will be **higher** than
   0.5229.
2. **Unfinished large suites have genuinely unknown true rates.** darts/aiida-core/nexent
   never completed; 0.0 is a conservative flag, not a measurement. Raising the pytest
   timeout (patch 0002, 1800 s) is the only way to learn their real rates, and even then
   some may exceed any practical budget.
3. **Patched re-run not yet executed end-to-end.** Patches are applied live on the VM and
   `py_compile`-verified; A/B/C were proven first-hand on real recorded inputs (old
   scorer→1.0 vs new→0.0; recursive find recovers markitdown). But a full `--model rat`
   re-run was deliberately **not** run (hours-long; protected dirs). The corrected headline
   is therefore *projected from per-repo evidence*, not yet measured on a clean re-run.
4. **Orchestration divergence is unaudited for tracking side effects.** We bypass Weave/W&B;
   the scorer math is identical, but cost/latency/token fields the paper's report aggregates
   are not captured the same way (not part of ESSR, but worth noting if those metrics are
   ever compared).
5. **Scenario split is unreproducible** without an independent (tests? artifacts?)
   classification pass over the 50 repos. Any S1/S2/S3 claim on our data would be our own
   labeling, not the harness's.
6. **`pass_rate_exclude_code_issues` also fires the timeout heuristic** (scorers.py:135-141).
   The 0.7658 exclude-code number is inflated by the same 9 phantoms; patch 0003 fixes both
   branches.

---

## Appendix: Re-derivation command provenance

All §2 numbers were re-derived this session from
`/opt/rat-bench-integration/rat_run_rat_fixed/output/*/*/_result_row.json` (50 files) on
the VM, mirroring `generate_latex_report.py:282-284,326`. Per-repo detail:
`/opt/rat-bench-integration/results/essr_per_repo_paper_method.csv`. MD5 byte-identity and
junit ground-truth (copier 1093/1, markitdown 332 in subdir, darts/websockets junit
absent) verified live via SSH.
