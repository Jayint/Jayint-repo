# Residual Triage — RAT Corrected Run → Dataset v2 Swap Proposal

**Status:** PROPOSAL ONLY. The swap below is NOT applied. The dataset (`datasets/rat_python_hard_subset.json`, 50 repos) is unchanged.
**Run analyzed:** `rat_run_rat_corrected` (50 repos, complete). Box is free; no benchmark was run or modified during this triage.
**Author note:** Every quantitative claim below was re-verified from command output / file contents on 2026-06-07. Verified vs inferred is marked inline.

---

## 0. Baseline integrity (VERIFIED)

- ESSR (honest) = **0.6775** = macro mean of `pytest_pass_rate` over the **46** repos with `pytest_executed=True`.
  - Verified: re-computed from `results/essr_per_repo_corrected.csv` → `0.6775` exactly over 46 executed rows.
- The 7 residuals decompose as:
  - **3 executed-but-0-tests** (`pytest_executed=True`, `pytest_total_tests=0`): slurm-gcp, nginx-proxy, tesserocr. These ARE in the 46-denominator and each contribute **0.0** to ESSR.
  - **4 not-executed** (`pytest_executed=False`): n8n-autoscaling, frappe/press, ingestr, agentic-ray. These are correctly EXCLUDED from the 46-denominator (they did not run pytest at all).
  - Verified: CSV shows `executed=46`, `not-executed=4`, `exec-but-0-tests=3`.
- Implication: the 3 exec-but-0-tests residuals are a real drag on the honest ESSR (they pull the macro mean down). The 4 not-executed do not affect the 0.6775 number but represent wasted dataset slots.

---

## 1. KEEP list (residuals that remain valid data points)

These stay as legitimate measurements (two are genuine 0.0 data points; one is a harness-truncation artifact over a real, mostly-passing suite). Removing them would launder away honest difficulty signal.

### 1.1 `GoogleCloudPlatform/slurm-gcp` — KEEP (AGENT_FAILURE)
- size=small, `_category=native_runtime_stress`, Python 66.2%.
- **Why keep:** Real pytest suite (23 `def test_` functions across 5 files, confirmed by fresh clone in the triage). The 0-test outcome is a *pure agent failure*: the agent's `edit_file` wrote literal `\n` escape sequences instead of newlines while wrapping a module-level GCP auth call in try/except, producing a one-line `SyntaxError` in `scripts/util.py:2040`, which broke `conftest.py` import → 0 collected.
- **Verified on VM:** `_result_row.json` → `pytest_executed=true, pytest_total_tests=0, pytest_collect_success=false, language="python"` (correctly routed). `run.log:2037` = `SyntaxError: unexpected character after line continuation character`; `run.log:2051` = `No tests were collected`.
- This is exactly the kind of agent-skill signal the benchmark should retain. A different/better agent would plausibly score >0 here.

### 1.2 `sirfz/tesserocr` — KEEP (HARD_BUT_LEGIT)
- size=small, `_category=native_runtime_stress`, Python 82.17% + Cython 17.21%.
- **Why keep:** Env setup *succeeded* and 24 real tests collected. The full run hit a native `SIGABRT` (Tesseract C++ core dump) in `test_detect_os`, which killed pytest before JUnit XML was written → harness regex-fallback saw no summary → recorded 0. A control run with `-k "not test_detect_os"` showed **1 failed, 22 passed**. This is a legitimate native-runtime-stress data point; the recorded 0.0 is a harness-truncation artifact over a genuinely hard, mostly-passing suite.
- **Verified on VM:** `run.log:1360` `test_detect_os ... dumped core`; `run.log:1362` `EXIT CODE: 134`; `run.log:1270` `JUnit XML file not found`; `run.log:1282` `No tests were collected`; `run.log:1444` `1 failed, 22 passed, 1 deselected`. `_result_row.json` → `pytest_collect_success=true`, `pytest_total_tests=0`, `language="python"`.
- **Open caveat (see §6):** Because the harness loses the partial result on SIGABRT, this contributes 0.0 to ESSR despite 22 real passes. KEEP for difficulty fidelity, but the harness undercounts it.

> Note: nginx-proxy is the third exec-but-0-tests repo, but it is REPLACE (§2.1), not KEEP — its suite is structurally un-runnable in a plain Python container (Docker-in-Docker only).

---

## 2. REPLACE list (residuals that are not valid Python pytest targets)

All five share a root defect: the repo's *real* test suite cannot be exercised by a standard `python -m pytest` benchmark run — either it's wrong-language/infra (4) or a Docker-in-Docker harness (1). Each has a verified replacement except frappe/press.

### 2.1 `nginx-proxy/nginx-proxy` (small) → **Teemu/pytest-sugar** [VERIFIED_GOOD]
- **Reason:** Docker reverse-proxy project; primary artifact is `nginx.tmpl` (Go Template). All 93 `test_*.py` are Docker-in-Docker integration tests; `conftest.py:39` runs `docker.from_env()` at import time → crashes (no daemon in the python:3.10 container) → 0 collected. No installable Python package. Cannot yield unit-test signal in any plain-Python benchmark.
- **Verified on VM:** `_result_row.json` exec=True, total=0; `language="unknown"` (not misrouted — it genuinely tried pytest and conftest crashed on Docker).
- **Replacement (independently re-verified by me on VM, 2026-06-07):** `Teemu/pytest-sugar` — clone OK; `test_sugar.py` at root; no package.json/go.mod; `pip install -e` then `pytest --collect-only` → **30 tests collected in 0.02s, 0 errors**. Python 99.67%, requires-python `>=3.10` (venv is 3.10.12). Not in current 50.
- **Verdict: VERIFIED_GOOD.**

### 2.2 `conor-is-my-name/n8n-autoscaling` (small) → **miguelgrinberg/microdot** [VERIFIED_GOOD]
- **Reason:** Docker-Compose deployment kit. NO real pytest suite — the only `test_*.py` is `examples/test_python_packages.py`, a manual n8n workflow snippet ending in `return results` (uncollectable). Also language-misrouted to node. Even with correct routing → 0 tests.
- **Verified on VM:** `_result_row.json` → `language="node", pytest_executed=false`.
- **Replacement (triage-verified on VM):** `miguelgrinberg/microdot` — genuine Python web framework; `pytest --collect-only` → **99 tests collected, 9 errors** (errors are all optional extras: jinja2/wsgi/asgi/auth/session/csrf — not core). Top-level `tests/`, `src/microdot/`, pyproject.toml; no package.json/go.mod. Python 99.36%, requires-python `>=3.8`. Not in current 50.
- **Verdict: VERIFIED_GOOD.** (Minor caveat: the 9 optional-extra collection errors mean a bare run collects 99; a proper install collects more. Core framework tests collect clean — acceptable, and arguably a realistic "install optional deps" sub-challenge.)

### 2.3 `bruin-data/ingestr` (medium) → **fastapi/typer** [VERIFIED_GOOD]
- **Reason:** Repo is a **Go CLI** (main.go, go.mod, 30+ `*_test.go`). The 99.8% Python label comes from a pip-installable wrapper + benchmark scripts. **Zero** `test_*.py`/`conftest.py` anywhere. Also misrouted to node. No correct routing can produce pytest results.
- **Verified on VM:** `_result_row.json` → `language="node", pytest_executed=false`.
- **Replacement (triage-verified on VM):** `fastapi/typer` — pure-Python CLI lib; single root pyproject.toml (not a monorepo); `pytest --collect-only` → **1374 tests collected in 0.44s, 0 errors**; 0 .go files, 2 .js (docs only). Python 99.65%, 18,677 stars. Not in current 50.
- **Verdict: VERIFIED_GOOD.**

### 2.4 `rayai-labs/agentic-ray` (small) → **thebjorn/pydeps** [VERIFIED_GOOD]
- **Reason:** Turborepo monorepo (root package.json + bun.lock) with a real Python SDK at `packages/python-sdk/` (12 test files, ~183 tests). The unified language detector hard-routes to **node** because of the root tooling signal → pytest never invoked. The root-level JS tooling reliably defeats detection, so it is an unreliable Python target as-tagged.
- **Verified on VM:** `_result_row.json` → `language="node", pytest_executed=false`.
- **Replacement (triage-verified on VM):** `thebjorn/pydeps` — pure-Python dependency-graph tool; `pytest --collect-only` → **68 tests collected in 0.11s, 0 errors** (no install needed); top-level `tests/`, setup.py `python_requires>=3.10`; no package.json/go.mod/Dockerfile. Python 97.76%, 2048 stars. Not in current 50.
- **Verdict: VERIFIED_GOOD.**

### 2.5 `frappe/press` (large) → **NO VERIFIED REPLACEMENT** [verdict=NONE]
- **Reason:** Python-primary (61.2%) but all 289 `test_*.py` require a live Frappe ERP site (MariaDB+Redis+bench) via `FrappeTestCase`; bare pytest → `289 errors during collection, no tests collected`. Also misrouted to node (dashboard package.json) and the python:3.10-slim Dockerfile build timed out → fell back to node:18-slim. Framework-integration repo, not a standalone pytest target.
- **Verified on VM:** `_result_row.json` → `language="node", pytest_executed=false`.
- **Replacement search outcome:** The large-bucket Python>=95% candidate pool has **only 7 entries** (verified: `replacement_candidates.json` `_counts.by_size.large=7`). Six were cloned+collected and all rejected:
  - `huggingface/transformers` — 977 test files, est. >10,000 tests → blows the 1800s/repo budget.
  - `Azure/azure-cli` — 491 test files buried at `src/azure-cli/.../tests/latest/` (deep monorepo) + need live Azure creds.
  - `OpenCTI-Platform/connectors` — top-level `tests/` collects 1516, but all are parametrized manifest-metadata checks (hollow); real tests need `pycti` + live OpenCTI.
  - `ai-dynamo/aiperf` — correct top-level layout but heavy ML/NVIDIA stack (pyzmq/transformers/nvidia-ml-py); collect blocked by missing zmq; only 100 stars.
  - `GoogleCloudPlatform/ramble` — only 16 test files; needs full Spack (`llnl`); a Spack wrapper, not a library.
  - `red-hat-storage/cephci` — 546 collection errors; needs a live Ceph cluster (same failure class as frappe/press); 28 stars.
  - The 7th, `NikolasMarkou/dl_techniques` (14 stars, ML), was not cloned but is a tiny-traction DL repo — not a credible large-bucket replacement.
- **Recommendation:** **REPLACE frappe/press but DEFER the substitute to manual selection.** No verified large candidate exists in the current Python>=95% pool. Options for a future pass:
  1. Relax the large-bucket purity floor (e.g. Python>=80%) and re-run the clone+collect verification, OR
  2. Demote the freed large slot to medium/small (changes size strata — see §3), OR
  3. Hand-pick a large pure-Python library with a self-contained suite (e.g. a well-known framework with top-level `tests/` runnable on bare pytest) and verify it the same way.

---

## 3. Swap proposal (v2)

**4 verified swaps + 1 deferred = up to 5 repos change. Recommended concrete change now: 4-out / 4-in (size-balanced), with frappe/press flagged for manual large-bucket selection.**

| Out (residual) | size | In (replacement) | size | Verdict |
|---|---|---|---|---|
| nginx-proxy/nginx-proxy | small | Teemu/pytest-sugar | small | VERIFIED_GOOD |
| conor-is-my-name/n8n-autoscaling | small | miguelgrinberg/microdot | small | VERIFIED_GOOD |
| bruin-data/ingestr | medium | fastapi/typer | medium | VERIFIED_GOOD |
| rayai-labs/agentic-ray | small | thebjorn/pydeps | small | VERIFIED_GOOD |
| frappe/press | large | **(deferred — manual)** | large | NONE |

KEEP (no change): GoogleCloudPlatform/slurm-gcp, sirfz/tesserocr.

**Size strata are preserved** for the 4 verified swaps (3 small→small, 1 medium→medium). frappe/press (large) should be replaced by another large repo to hold the 9-large stratum; if no large candidate is found, the alternative is to drop to 8-large + 1 extra small/medium, which shifts strata (flagged in §6).

---

## 4. Dataset impact (current 50 → proposed v2)

Computed against the current 50 (verified): size = {small:23, medium:18, large:9}; Python>=95% = 30/50; mean Python% = 90.04.

- **Size distribution:** With the 4 verified swaps, strata are unchanged (small 23, medium 18, large 9). If frappe/press's large slot is also swapped same-size, the full 5-swap keeps {23/18/9}. If frappe/press is *demoted* (no large found), it becomes e.g. small 24 / medium 18 / large 8 — a strata shift to disclose.
- **Python purity (rises):** Four impure/misleading repos leave — n8n-autoscaling (84.0%, actually IaC), ingestr (99.8% *label* but actually a Go CLI), agentic-ray (95.1% but JS-tooling-dominated), nginx-proxy (67.0%, Go Template). They are replaced by genuinely pure-Python libs: pytest-sugar 99.67%, microdot 99.36%, typer 99.65%, pydeps 97.76%. Mean Python% rises and — more importantly — the *labels become honest* (typer/pydeps are real Python pytest targets, unlike ingestr's misleading 99.8%). Replacing frappe/press (61.2%) with a pure large lib would raise the floor further.
- **Scenario mix (intended consequence):** This removes four "structurally un-measurable" slots and one Docker-in-Docker slot, replacing them with cleanly-collectable libraries. The benchmark trades *un-runnable noise* for *runnable signal*. Trade-off: the removed repos were tagged `repo2run_weak_*` / `documented_rat_failure` / `easy_control` — i.e. v2 loses some "wrong-language trap" and "weak-CI" difficulty texture. The KEEP set (slurm-gcp, tesserocr) preserves the genuine `native_runtime_stress` hard-failure signal. Net: v2 is a *cleaner* benchmark (more repos produce a real number) but slightly *easier-on-average* and less adversarial about language-detection.

---

## 5. Head-to-head note (CRITICAL)

**The swap is NOT applied. Do not apply it before the DockerAgent baseline runs.**

- The pending **DockerAgent baseline MUST run on the current v1 50** so it is directly comparable to **RAT's honest ESSR = 0.6775**, which was measured on those same 50.
- A v2 dataset would invalidate the head-to-head: **changing the repo set requires re-running BOTH agents** (RAT and DockerAgent) on the new 50 to produce comparable numbers. You cannot compare DockerAgent-on-v2 to RAT-on-v1.
- Therefore: (1) finish the DockerAgent baseline on v1 first; (2) treat this document as the v2 design once the v1 head-to-head is banked; (3) when adopting v2, re-run RAT and DockerAgent together.

---

## 6. Open questions

1. **frappe/press large replacement** — the Python>=95% large pool (7 repos) is exhausted with no viable candidate. Relax purity to >=80% and re-verify, hand-pick a known large pure-Python lib, or accept a strata shift to 8-large? This is the one unresolved swap.
2. **tesserocr harness undercount** — SIGABRT discards the partial JUnit, so 22 real passes are recorded as 0.0. Should the harness capture pre-crash results (e.g. incremental JUnit or `--last-failed` style salvage)? If fixed, tesserocr would contribute ~0.96 instead of 0.0, *raising* ESSR. Keeping it at 0.0 is conservative but understates difficulty handling.
3. **Optional-deps policy for microdot** — bare collect yields 99 tests + 9 optional-extra errors. Decide whether v2 expects the agent to install optional extras (jinja2/asgi/etc.) — this affects the denominator and how "success" is judged for that repo.
4. **Difficulty texture loss** — removing 4 `repo2run_weak_*`/wrong-language repos reduces the benchmark's language-detection adversarialness. Is preserving some "trap" repos a design goal, or is a clean runnable set preferred? If traps matter, intentionally retain 1–2.
5. **Replacement difficulty calibration** — pytest-sugar (30), pydeps (68), microdot (~99), typer (1374) all collect cleanly with light/no install. They may be *easier* than the residuals they replace (which were 0.0 traps). Confirm v2 still has enough hard data points to discriminate agents (the connection_error_stress + winnable_large + native_runtime_stress keeps should carry most of the difficulty).
6. **agentic-ray is recoverable in principle** — its Python SDK (~183 tests) is real; only the unified detector defeats it. If the harness's language router is fixed to honor an explicit per-repo language override, agentic-ray could become a valid KEEP rather than a REPLACE. Decide whether to fix routing vs. swap the repo.
