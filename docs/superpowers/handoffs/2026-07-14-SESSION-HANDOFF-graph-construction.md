# SESSION HANDOFF — graph construction: what changed, what it's worth, what it broke

**Date:** 2026-07-14 · **Branch:** `john-v3-multi-lang` (SHARED — **never** `git add -A` / `git stash`)
**HEAD:** `3a86707` · 🔴 **NOTHING IS COMMITTED.** 2,833 insertions across 13 files, all in the working tree.
**Backup (non-destructive):** `<scratchpad>/backup/worktree-1416.patch` + full `src/` snapshot (263 files).
**Local tests:** **1,637 passing** — `python3 -m pytest tests/depgraph/ tests/test_classify_services_clean.py -q`
⚠️ `pytest tests/` is broken repo-wide (pre-existing conftest shadowing). Per-directory runs only.

---

## 0. READ FIRST — three things that will bite you

1. 🔴 **The VM's `roots.py` is PATCHED.** `_TEST_SCOPE_EXTRA_ALLOWLIST` is `frozenset()` ("BISECT: B2 disabled")
   at `/opt/v3_rerun/src/python_deps/depgraph/roots.py:187`. Backup: `/tmp/roots.py.bak`. **Restore it before
   any further VM run**, or you are testing code that does not exist in the tree.
2. 🔴 **The working tree mixes THREE layers with no bisectable history**: (a) pre-existing uncommitted work that
   is NOT ours (`emit.py`, `resolve_link.py`, `resolve_lock.py`, `wheel_oracle.py`, `tests/depgraph/test_resolve.py`,
   `test_wheel_oracle.py`); (b) the previous session's A1/B2/B5/A2-B4/C1/B6/B3/B1; (c) this session's fixes.
   **A blanket `git checkout --` destroys (a).**
3. 🔴 **The regression-sweep harness has a bug that FABRICATES regressions.** Under 4-way concurrency
   `run_detached` sometimes fails and the harness records `COLLECT_CRASHED`. 5 of 8 "regressions" were this.
   Look for `Error response from daemon: No such container` in `collect_crash.log` before believing any row.

---

## 1. What was changed (all uncommitted)

| id | change | files | verdict |
|---|---|---|---|
| **B5** | capstone gate: suppress `pip install -e .` when `_looks_safely_installable()` says no; non-fatal install blocks | `populate.py`, `build_script.py` | 🟢 win **AND** 🔴 regression — see §3 |
| **Fix 1** | PEP 508 direct refs (`kivymd @ git+…`) excluded from roots → un-installable MISSING node | `evidence.py`, `build.py`, `models.py` | 🟢 win |
| **A2/B4** | requirements discovery: HARD (pre-walk allowlist set) vs SOFT (nested, e.g. `worlds/*/requirements.txt`) → soft render as `pip install -r … -c closure.txt \|\| true` | `evidence.py`, `build_script.py` | 🟢 win |
| **Fix 2** | soft-file laundering guard: excluded pkgs pinned `name==0.0.0+v3.excluded` (PEP 440 local segment; PyPI can never serve it) | `build_script.py` | 🟢 safe |
| **B1 + provenance** | CONFIG → Dockerfile `ENV`; value from `tox.ini [testenv] setenv` (AUTHORITATIVE), not a `.py` scan | `config_scan.py`, `classify_services_clean.py`, `build_script.py` | 🟢 win |
| **Fix 3** | B1 allowlist — only `DJANGO_SETTINGS_MODULE`-shaped vars bake; hosts/ports/secrets never | `build_script.py`, `config_scan.py` | 🟢 safe |
| **A1** | `os.environ.setdefault` detection | `config_scan.py` | 🟢 |
| **B2** | test extras default-included (`_TEST_SCOPE_EXTRA_ALLOWLIST` = test/tests/dev/lint/typing/mypy/qa/ci/…) | `roots.py` | 🔴 **SUSPECT** — see §3 |
| **B6** | resolve wall-clock budget: per-attempt timeout, `fallback_roots = current`, budget-exhausted node | `resolve.py` | ⚪ no measured win |
| **C1** | `[tool.uv.sources]` behind `V3_UV_SOURCES`, **default OFF** (excludes non-PyPI deps as MISSING nodes) | `evidence.py`, `resolve.py`, `resolve_lock.py`, `build.py` | ⚪ no measured win (posthog still 0) |
| **B3** | declared repair rung | `repair.py` | ⚫ **proven NO-OP** — `declared_candidates` only fires on exact-name equality, which `normalize` already produces. Only action: stale comment at `build.py:424-428`. |

---

## 2. 🟢 MEASURED WINS (VM, build + gold's own collect, pinned SHAs)

| repo | gold | before | after |
|---|---|---|---|
| **python-websockets/websockets** | 2,248 | BUILD_FAILED | **2,112 collected — EBSR 0.9395** |
| **ArchipelagoMW/Archipelago** | 20,943 | BUILD_FAILED, 0 pkgs | **4,227 collected — EBSR 0.2018** (closure 0 → 74) |
| **django-oauth/django-oauth-toolkit** | 557 | COLLECT_CRASHED | **557/557** (`tests.settings`, not `idp.settings`) |

**≈6,900 gold tests that were ZERO.**
- **websockets** ← B5's capstone gate (its `pip install -e .` was killing the build *after* 100% of the closure installed).
- **Archipelago** ← Fix 1 (a PEP 508 direct ref + its unsatisfiable companion `kivymd>=2.0.1.dev0` were zeroing the whole `uv lock`, which is all-or-nothing).
- **django-oauth** ← B1 provenance (`DJANGO_SETTINGS_MODULE` lives only in `tox.ini [testenv] setenv`; bare pytest never reads it).

---

## 3. 🔴 MEASURED REGRESSIONS — the 50-repo run MUST NOT START

Regression sweep over **33 repos that ALREADY WORKED** (`/opt/regression_sweep.py` → `/opt/tier2_rerun/regression_sweep.json`).

**3 real regressions (9%). 5 more were HARNESS ARTIFACTS — do not inherit the "24%" figure, it was wrong.**

| repo | before | after | cause |
|---|---|---|---|
| **pre-commit/pre-commit** | **1.0000** | 0 | ✅ **CONFIRMED — B5.** Capstone `-e .` present BEFORE, **absent NOW** → project never installed → `ImportError while loading conftest`. B5's gate suppressed a *working* capstone. |
| **aiidateam/aiida-core** | **0.9995** | 0 | ✅ closure **216 → 2 packages**. Ladder dropped `['paramiko','plumpy','psutil','pytz','pyyaml','sqlalchemy','tabulate']` — the **runtime spine**. |
| **anthropics/anthropic-sdk-python** | **0.9884** | 0 | ✅ closure 57 → 56; one root dropped (`http-snapshot[httpx]==0.1.8`, a test dep whose `conftest.py` imports it → one root = 100% of tests). |
| tinygrad, darts, ezdata, feast, pretix | — | — | ❌ **NOT regressions** — `No such container` (harness `run_detached` failure under 4 workers). **Re-run these serially.** |

### The mechanism behind the two closure regressions (three failures stacked)

1. **Over-inclusion of roots.** aiida-core has **30 runtime deps**; the ladder log shows **48 and 63 roots**.
   B2 pulls in `dev`/`lint`/`typing`/`qa` extras whose pins make `uv lock` unsatisfiable.
2. 🔴 **The ladder amputates the WRONG roots** — it dropped `sqlalchemy` (a **runtime** dep) to satisfy a
   constraint introduced by a **lint extra we added ourselves**. It has no notion of what is load-bearing.
3. 🔴 **The degraded `uv pip compile` fallback is worthless** — handed **41 surviving roots**, it recovered
   **1 package**. There is no safety net.

### ⚠️ Uncertainty — state of the bisect

- **Budget hypothesis: REFUTED by experiment.** `DEPGRAPH_RESOLVE_LADDER_BUDGET_S=3600` → aiida-core still 2 pins.
- **B2 hypothesis: NOT REFUTED, and now the leading one.** Both closure regressions ran with **B2 ON**. Every repo
  built with B2 accidentally OFF produced **no** closure regression. **The bisect was never completed — finish it:**
  restore `roots.py` on the VM, then flip `_TEST_SCOPE_EXTRA_ALLOWLIST` to `frozenset()` and re-run construction on
  **aiida-core**; if pins return to ~216, B2 is confirmed.
- **anthropic-sdk may be a DIFFERENT bug** — its closure did not inflate, and its dev deps were already pinned in the
  original run. Cause still unknown.

---

## 4. RESTART PLAN

**Revert to HEAD (`3a86707`) for:**
- `src/python_deps/depgraph/roots.py` (B2)
- `src/python_deps/depgraph/resolve.py` (B6 ladder + C1 source emission)
- `src/python_deps/depgraph/resolve_lock.py` — ⚠️ **selective**; carries pre-existing non-ours changes
- `src/python_deps/depgraph/repair.py` (B3 — a no-op)

**KEEP:** `build_script.py`, `populate.py`, `config_scan.py`, `classify_services_clean.py`, `evidence.py`, `models.py`.
⚠️ **`build.py` is MIXED** — Fix 1's direct-ref exclusion (win) *and* C1's uv-source exclusion. Hunk-level review.

**🔴 B5 needs a FIX, not a revert.** It is both a win (websockets +2,112) and a regression (pre-commit −all).
`_looks_safely_installable()` must not suppress a capstone that WORKS. The right shape: **always attempt `pip install -e .`,
but make its failure non-fatal** (`|| true` + a recorded degradation) instead of predicting installability up front.
That keeps websockets' build alive AND keeps pre-commit's project installed.

**Re-land as SEPARATE NAMED COMMITS**, each gated by the regression sweep:
1. B5 (fixed per above)  2. Fix 1  3. B1 + provenance  4. A2/B4 + Fix 2

**Do NOT re-land B2, B6, or C1** without a measured win and a clean sweep.

---

## 5. Still-open bugs (measured, unexplained — MEASURE, do not theorise)

- **posthog (77,642 gold)** — closure STILL **0** with all 3 uv-source deps correctly excluded. Exclusion is necessary,
  NOT sufficient. Second blocker unknown.
- **Archipelago's 80% gap** — 20,383 of 20,943 gold live under `worlds/<world>/test/`; we collect 4,227 with only **5**
  collect errors → most worlds **SILENTLY SKIPPED**. Diff collected node-ids vs gold per-world. *(This is the dangerous
  class: indistinguishable from a repo that simply has fewer tests.)*
- **Qiskit (64,558)** — builds now, collects **0**, 358 import errors.
- **addons-server (9,098)** — `addopts = --reuse-db` ⇒ needs **pytest-django**, not installed → dies at **argparse**.
  **UNDER-install.**
- **slither (7,296)** — we install `pytest_insta`, which needs `config.cache`; gold's `-p no:cacheprovider` kills it →
  `INTERNALERROR`. **OVER-install.**
- **polar / explainshell** — still BUILD_FAILED. Logs: `/opt/tier2_rerun/<slug>/build.log`.

🔴 **Our dependency selection is wrong in BOTH directions at once** (slither over, addons-server under). Not too greedy,
not too shy — **both**. That is the headline finding.

---

## 6. DESIGN DIRECTION (discussed, not built)

**The core flaw: one atomic resolve with no notion of what is load-bearing.** Every catastrophe today came from it —
Archipelago (one bad root → 0/20), posthog (one name → 0/~500), aiida-core (a lint extra → `sqlalchemy` dropped).

**1. Stratify roots by criticality, not by manifest section:**
```
CRITICAL   [project] dependencies              — the code imports these. NEVER droppable.
REQUIRED   the repo's DECLARED test-run deps   — tox [testenv] deps/extras, CI, addopts-implied plugins.
                                                 Droppable only with a recorded degradation.
OPTIONAL   lint, docs, typing, benchmark       — NEVER roots. Not in the closure at all.
```
**2. Resolve in LAYERS, not one atomic lock:** resolve CRITICAL alone → then CRITICAL+REQUIRED. A conflict from a test
dep then *cannot* destroy the runtime closure. (Cheaper variant: keep one lock, but make CRITICAL undroppable.)

**3. Bias root selection MINIMAL.** We have a post-install import verifier (Phase-A repair), so **under-inclusion is
cheap and recoverable; over-inclusion is catastrophic and silent.** B2 optimised the cheap error at the expense of the
expensive one — exactly inverted.

**4. Provenance is part of a package's identity** — `(name, version, source)`, with **ONE install chokepoint**
(`install_spec(node)` emitting `name==version` / `name @ git+url@rev` / `-e /abs/path` / `--index-url`). The package
layer currently installs by bare name from **six** independent sites, which is why C1 is a false-green generator.

**5. Test-harness facts are DERIVED, not discovered** — `tox.ini setenv` → `ENV`; `addopts` → plugin roots. Not a new
tier; just evidence sources feeding existing machinery. (User's explicit call: **no new graph tier.**)

**6. Every degradation poisons the CERTIFICATE, not the build.** A dropped root / unhonored source / excluded package →
environment is DEGRADED and cannot score clean.

---

## 7. VM assets (`root@167.233.64.96`)

- `/opt/v3_rerun` — deployed code copy (independent of the local tree; safe to patch for bisects). 🔴 **`roots.py` is
  currently PATCHED — restore from `/tmp/roots.py.bak`.**
- `/opt/v3_rerun_venv` — Python **3.10** + full `requirements.txt`. 🔴 **`tomli` is MANDATORY**: without it
  `tomllib is None` and the **entire declared closure silently vanishes** (`evidence.py:28`).
- `/opt/construct_rerun.py` — construction only (no Docker, no disk)
- `/opt/tier2_rerun.py` — build + gold's collect; captures the real error when pytest dies before the plugin writes
- `/opt/regression_sweep.py` — **the standing gate.** ⚠️ has the `run_detached` concurrency bug (§0.3)
- `/opt/before_closures.py` — 🔴 **RUN BEFORE DESIGNING ANY EXPERIMENT.** Only **2 of 20** tier-2 failures ever had an
  empty closure; the other 6 had 51–126 pinned packages and failed anyway.
- Gold: `/opt/manifest_out_py50/rat_python50_gold.json` · Baseline: `/opt/manifest_out_py50/tier2_v3.json`
- 🔴 **Do NOT touch `/opt/runs`** (other people's benchmark data).

---

## 8. LANDMINES (earned the hard way this session)

1. **A passing test proves nothing about reachability.** Four features shipped passing their own tests while never
   firing in production (B1 and B3 among them).
2. **Every fix was validated against ALREADY-BROKEN repos, where the only direction was up. Nobody measured the repos
   that WORKED.** A 9% regression rate was invisible to 1,637 passing tests and five codex reviews.
   **The regression sweep must be a standing gate, not an afterthought.**
3. **Do not report a number before verifying its mechanism.** I reported "24% regressions" — 5 of the 8 were my own
   harness's container failures, sitting in a log I hadn't read. I also asserted a wrong cause for Archipelago's empty
   closure, and repeated a subagent's reachability claim that was asserting the UNSAFE path as correct.
4. **Denylists have failed three times** (`_looks_like_dsn` missed bare hosts/ports; the fixture-path blocklist misses
   `tests/testapp/`, `demo_project/`, …; `_DEV_GROUP_DENYLIST`). **Prefer allowlists and authoritative declarations.**
5. `codex exec` hangs on stdin → `< /dev/null`. Long runs get killed by task supervision → `nohup … &`.
6. `python3 -u` when redirecting output, or the log stays empty and looks like a hang.
7. Never run two implementer agents on the same file. Contradictory briefs produce compensating hacks (that is how a
   scope-bypass got added to `build.py`).
8. **Codex (`gpt-5.6-terra`, effort=high) has been right essentially every time.** Re-run it on every diff.
