# Graph construction — full state, measured results, and the restart plan

**Date:** 2026-07-14 · **Branch:** `john-v3-multi-lang` (SHARED — never `git add -A`)
**HEAD:** `3a86707` · 🔴 **NOTHING FROM THE LAST TWO SESSIONS IS COMMITTED.**
**Working tree:** 2,833 insertions across 13 files.
**Backup (non-destructive):** `…/scratchpad/backup/worktree-1416.patch` + full `src/` snapshot (263 files).
**Local tests:** 1,637 passing (`pytest tests/depgraph/ tests/test_classify_services_clean.py`).
⚠️ `pytest tests/` is broken repo-wide (pre-existing conftest shadowing). Use per-directory runs.

---

## 1. 🔴 READ THIS FIRST — the tree is in a mixed, uncommitted state

Three layers are interleaved in the working tree with **no bisectable history**:
1. **Pre-existing uncommitted work that is NOT ours** — `emit.py`, `resolve_link.py`, `resolve_lock.py`,
   `wheel_oracle.py`, `tests/depgraph/test_resolve.py`, `test_wheel_oracle.py`. **Do not blanket-revert these.**
2. The **previous session's** A1/B2/B5/A2-B4/C1/B6/B3/B1.
3. **This session's** fixes on top.

A `git checkout --` on a shared file destroys (1). This is why nothing has been reverted.

---

## 2. Every change, and what it is actually WORTH (measured on the VM, not asserted)

| fix | what it does | status | **MEASURED** |
|---|---|---|---|
| **B5** capstone gate + non-fatal installs | suppresses the unconditional `pip install -e .` when the project isn't safely installable | ✅ KEEP | 🟢 **websockets 0 → 2,112 tests (EBSR 0.9395)**. Also got Qiskit to build. |
| **Fix 1** PEP 508 direct refs | `kivymd @ git+…` excluded from roots → emitted as un-installable MISSING node | ✅ KEEP | 🟢 **Archipelago closure 0 → 74 pkgs → 4,227 tests (EBSR 0.2018)** |
| **B1 + provenance** config → Dockerfile `ENV` | value read from `tox.ini [testenv] setenv` (authoritative), NOT a `.py` scan | ✅ KEEP | 🟢 **django-oauth-toolkit 557/557** |
| **A2/B4** hard/soft requirements split | nested `worlds/*/requirements.txt` → best-effort `pip install -r … -c closure.txt \|\| true` | ✅ KEEP | part of the Archipelago win; 10/10 files found |
| **Fix 2** soft-file laundering guard | excluded pkgs pinned `name==0.0.0+v3.excluded` (PEP 440 local segment — PyPI can never serve it) | ✅ KEEP | renders correctly; prevents public-namesake install |
| **Fix 3** B1 allowlist | only `DJANGO_SETTINGS_MODULE`-shaped vars bake; hosts/ports/secrets never | ✅ KEEP | django config-env 11 → 1 |
| **A1** `os.environ.setdefault` detection | — | ✅ KEEP | — |
| **B2** test extras default-included | `_TEST_SCOPE_EXTRA_ALLOWLIST` = {test, tests, dev, lint, typing, mypy, qa, ci, …} makes those extras root-eligible | 🔴 **REVERT** | 🔴 **SUSPECTED CAUSE of the aiida-core collapse.** No measured win. |
| **B6** resolve wall-clock budget | per-attempt timeout, `fallback_roots = current`, budget-exhausted node | 🔴 **REVERT** | 🔴 changed ladder behaviour. **Budget hypothesis REFUTED** (3600s → still 2 pins). No measured win. |
| **C1** `[tool.uv.sources]` | behind `V3_UV_SOURCES`, **default OFF** (excludes non-PyPI deps) | 🔴 **REVERT** | 🔴 posthog closure STILL 0 with it. No measured win. |
| **B3** declared repair rung | — | ⚫ DEAD | proven **NO-OP**: `declared_candidates` only fires on exact-name equality, which `normalize` already produces. Only action: stale comment at `build.py:424-428`. |

**Net measured gain: ≈6,900 gold tests that were ZERO.** (websockets 2,112 + Archipelago 4,227 + django-oauth 557)

---

## 3. 🔴 THE REGRESSIONS — this is why the 50-repo run must NOT start

A **regression sweep** (33 repos that ALREADY WORKED; `/opt/regression_sweep.py`, results
`/opt/tier2_rerun/regression_sweep.json`) found, at 13/33:

| repo | before | after |
|---|---|---|
| **aiidateam/aiida-core** | **EBSR 0.9995** | **COLLECT_CRASHED** — closure **216 → 2 packages** |
| **anthropics/anthropic-sdk-python** | **EBSR 0.9884** | **COLLECT_CRASHED** — closure 57 → 56, one root lost |

**~15–20% of healthy repos destroyed.** That would wipe out the ~6,900-test gain and more.

### The mechanism (three failures stacked)

1. **Over-inclusion of roots.** aiida-core has **30 runtime deps**; the ladder log shows **48 and 63 roots**.
   B2's allowlist pulls in `dev`/`lint`/`typing`/`qa` extras, whose pins make `uv lock` unsatisfiable.
2. 🔴 **The ladder amputates the WRONG roots.** It dropped
   `['paramiko','plumpy','psutil','pytz','pyyaml','sqlalchemy','tabulate']` — the repo's **runtime spine** —
   to satisfy constraints that a **lint extra we added ourselves** introduced.
3. 🔴 **The degraded `uv pip compile` fallback is worthless.** Handed **41 surviving roots**, it recovered
   **1 package**. There is no safety net.

### ⚠️ Honest uncertainty — do NOT inherit this as settled

- The **budget hypothesis was REFUTED** by experiment (`DEPGRAPH_RESOLVE_LADDER_BUDGET_S=3600` → still 2 pins).
- The **B2 bisect was NOT completed.** To finish it: neutralise `_TEST_SCOPE_EXTRA_ALLOWLIST` (`roots.py:187`)
  in the **VM copy** `/opt/v3_rerun` (NOT the local tree — the sweep runs from the deployed copy and is
  independent), then re-run construction on aiida-core and compare pin count to 216.
- **anthropic-sdk does NOT fit the over-inclusion story** — its closure did not inflate, and its dev deps were
  already pinned in the original run. It lost exactly one root (`http-snapshot[httpx]==0.1.8`, a test dep whose
  `conftest.py` imports it → one root = 100% of tests). **Cause unknown.** There may be two distinct bugs.

---

## 4. THE RESTART PLAN

**Revert to HEAD (`3a86707`) for exactly these:**
- `src/python_deps/depgraph/roots.py` (B2)
- `src/python_deps/depgraph/resolve.py` (B6 ladder + C1 source emission)
- `src/python_deps/depgraph/resolve_lock.py` — ⚠️ **selective**, it carries pre-existing non-ours changes
- `src/python_deps/depgraph/repair.py` (B3, a no-op)

**KEEP everything else** — the wins and the regressions do not overlap:
`build_script.py`, `populate.py` (B5) · `config_scan.py`, `classify_services_clean.py` (B1) ·
`evidence.py`, `models.py` (A2/B4 + Fix 1).
⚠️ `build.py` is **MIXED** — it holds Fix 1's direct-ref exclusion (a win) *and* C1's uv-source exclusion.
Hunk-level review required; do not blanket-revert.

**Then re-land as SEPARATE, NAMED COMMITS** (never `git add -A`), each gated by the regression sweep:
1. B5 capstone gate (+2,112)
2. Fix 1 direct refs (+4,227)
3. B1 + provenance (+557)

**Do NOT re-land B2, B6, or C1** without a measured win and a clean regression sweep.

---

## 5. Still-open bugs (measured, unexplained — do NOT theorise, measure)

- **posthog (77,642 gold)** — closure STILL **0** even with all 3 uv-source deps correctly excluded. Exclusion is
  necessary, NOT sufficient. Second blocker unknown.
- **Archipelago's 80% gap** — 20,383 of 20,943 gold live under `worlds/<world>/test/`; we collect 4,227 with only
  **5** collect errors → most worlds **silently skipped**. Diff collected node-ids against gold per-world.
- **Qiskit (64,558)** — builds now, collects **0**, 358 import errors.
- **addons-server (9,098)** — `addopts = --reuse-db` ⇒ needs **pytest-django**, not installed → dies at *argparse*.
  **UNDER-install.**
- **slither (7,296)** — we install `pytest_insta`, which needs `config.cache`; gold's `-p no:cacheprovider` kills it
  → `INTERNALERROR`. **OVER-install.**
- **polar / explainshell** — still BUILD_FAILED; logs at `/opt/tier2_rerun/<slug>/build.log`.

🔴 **Our dependency selection is wrong in BOTH directions simultaneously** (slither over, addons-server under).
That is the headline finding.

---

## 6. VM assets

- `/opt/v3_rerun` — deployed code copy (independent of the local tree; safe to patch for bisects)
- `/opt/v3_rerun_venv` — Python **3.10** + full `requirements.txt`. 🔴 `tomli` is MANDATORY: without it
  `tomllib is None` and the **entire declared closure silently vanishes** (`evidence.py:28`).
- `/opt/construct_rerun.py` — construction only (no Docker, no disk)
- `/opt/tier2_rerun.py` — build + gold's collect (captures the real error when pytest dies pre-plugin)
- `/opt/regression_sweep.py` — **the standing gate.** Concurrent construct→build→collect on repos that ALREADY WORK.
- `/opt/before_closures.py` — 🔴 **RUN THIS BEFORE DESIGNING ANY EXPERIMENT.** Only **2 of 20** tier-2 failures
  ever had an empty closure; the other 6 had 51–126 pinned packages and failed anyway.

---

## 7. Landmines

1. **A passing test proves nothing about reachability.** Four features shipped this session passing their own
   tests while never firing in production.
2. **My narratives were less reliable than the measurements.** Every symptom I explained without running it, I got
   wrong (the `kivymd` closure story; the `hogli` reachability claim; the budget hypothesis).
3. 🔴 **Every fix was validated against ALREADY-BROKEN repos, where the only direction was up. Nobody measured the
   repos that worked.** A 20% regression rate was invisible to 1,637 passing tests and four codex reviews.
   **The regression sweep must be a standing gate.**
4. `codex exec` hangs on stdin → `< /dev/null`; long runs get killed → `nohup … &`.
5. `python3 -u` when redirecting, or the log looks like a hang.
6. Never run two implementer agents on the same file.
