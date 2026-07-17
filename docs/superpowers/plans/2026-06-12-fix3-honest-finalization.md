# Fix 3 — Honest Finalization (REVISED after adversarial review)

**Branch:** john-planner-v1 · 2026-06-12 · recovers Band-B "worked-then-gave-up" repos whose
correctly-built environment is scored as a total failure by the finalization gate.

> **Review status:** A first-draft plan was produced by a 5-explorer workflow and then
> adversarially reviewed (verdict: **needs_major_changes** — 3 CRITICAL honesty regressions found).
> This document is the **corrected** plan. The corrections are summarised in §0 and baked into the
> design. Do NOT implement from the first draft.

---

## 0. What the adversarial review caught (and how this version fixes it)

The naive design relaxed the gate using `synthesizer.observation_has_effective_test_signal(...)` as
the "did tests run?" signal. **That function is not a passing-tests signal** — verified at
`src/synthesizer.py:3770-3792` it returns `True` for `collected N items` (3771), `N failed` (3775),
`N skipped` (3776), and `not ok` (3791). Building the partial-pass acceptance on it would have
**re-introduced hollow passes** — the exact hardening Fix 3 must preserve.

| # | Regression the draft introduced | Correction in this plan |
|---|---|---|
| C1 | Only patched ONE of the two `_invalidate_verification_group` call sites (`agent.py:2256`); the second (`2260`, the non-test mutating branch) still wiped the verified group → the fix didn't even work for mcp-atlassian | **Guard BOTH call sites** (§3) |
| C2 | Partial-pass branch keyed on `observation_has_effective_test_signal` → a `5 failed / 0 passed` run would be accepted | Introduce a dedicated **`observation_has_passing_test_signal`** (`≥1 passed` only) and gate the partial-pass branch on it (§4) |
| C3 | Kept a `has_effective and not has_failure → accept` branch → a bare `--collect-only` run (which sets `has_effective=True`) would be accepted | **Delete that branch.** The clean-run case is already covered by `analyze_test_run.is_effective_test_run`, which correctly rejects collect-only (§4) |
| m4 | `_v1_output_is_env_defect` re-used `maintainer._COLLECTION_PATTERNS` which contains an over-broad `\bconftest\b` → normal output mentioning conftest flagged as "broken env" | **One classifier, no `_COLLECTION_PATTERNS`** — the v1 helper delegates to `synthesizer.observation_has_env_defect_signal` (§4) |
| m5 | v1 Path-3 ledger append hardcoded `rc=0` even for a partial pass → a phantom `rc=0` record could later satisfy Path-1's `rc==0` scan and fabricate a pass | **Record the real rc** (`1` for partial) — also keeps Path 1's Condition-1 (`rc==0`) honest (§4) |

Plus a behaviour change the draft itself flagged: the existing test asserting that `"32 passed, 1 error"`
is rejected must be **re-examined** — a bare generic error with 32 passes is honest, but we keep it
**conservative**: see §5.7.

---

## 0.5 Goal alignment — adopt RepoLaunch's "majority-pass" criterion

**Decision (2026-06-12):** shift the agent's *goal* (and therefore the finalize bar) from our current
**"≥1 test passes"** to **RepoLaunch's** Tier-3 criterion: *the suite executes and the **majority** of
tests pass, with only non-env failures remaining.* Evidence — RepoLaunch (arXiv 2603.05026), the
strongest peer "build & test any repo" agent (~70% build success, 9 languages): its Verify Agent commits
an image only when *"the majority of test cases pass and tests related to core functionalities, especially
integration tests, pass,"* using a per-test `{pass,fail,skip}` mapping and env-vs-test failure attribution.

This threads through **two** places, kept consistent:
1. **Finalize gate (this plan, Tier B):** a partial-pass run finalizes only when
   `pass_ratio = passed / (passed + failed + errors) >= MIN_PASS_RATIO` **and** failures are non-env.
   Clean 0-failure runs are 100% and pass trivially.
2. **Planner objective (companion edit, §8b):** change `planner.py`'s stated goal from "reach at least one
   passed test" to "execute the suite; pass the **majority**, only non-env failures remaining." So what the
   agent *aims for* equals what the gate *certifies*.

`MIN_PASS_RATIO` is a **tunable constant (default 0.5 = RepoLaunch "majority")**. The one real trade-off it
introduces is in §5.8 — decide the value before coding. "core/integration pass" cannot be cheaply detected
without test metadata, so we approximate it with the aggregate ratio (documented limitation).

---

## 1. Problem statement (verified anchors)

`src/verification_bundle.py :: _collect_effective_observed_test_commands` (`@86`) is the sole source of
`observed_test_commands`. If it returns `[]`, no agent-reported command can be supported (the empty
fallback `@66-67` is also empty) → `test_commands: []` → the run is scored `planner_giveup` despite a
correct environment. Two filters drop a genuine passing run:

**Filter 1 — revision staleness (`@100`):** `if record["environment_revision"] != final_revision: continue`.
`_final_environment_revision` (`@21-29`) = max revision over all records. Any post-test action that
`mutates_environment` (mkdir/chmod/git checkout/cp/wget) bumps `_environment_revision` in
`agent.py:_record_successful_action`, so the green run at revision R is dropped once a later action lands
at R+1. **Victim: `sooperset/mcp-atlassian` (2578 passed / 0 failed)** — green run, then post-test
housekeeping, run discarded.

**Filter 2 — zero-failure (`@108-112`):** keeps a run only if `analyze_test_run.is_effective_test_run`
**or** (`has_effective AND not has_failure AND not truncated`). `analyze_test_run` (`synthesizer.py:3090`)
short-circuits to `is_effective_test_run=False` the moment `_observation_has_test_failure_signal` fires
— **before** checking whether anything passed. The failure regex (`synthesizer.py:3749-3758`,
`\b[1-9]\d*\s+(?:failed|errors?)\b`) matches `"2 failed"` identically whether the cause is a pre-existing
`AssertionError` or an `ImportError`. **Victims: `py2many/py2many` (1601/1603, 2 pre-existing source
bugs), `swar/nba_api` (686/689, live-network).**

**Two code paths.** `_collect_effective_observed_test_commands` + `_invalidate_verification_group` are the
**arm0 / ReAct** path. In **v1** (`--arm v1`) `self.successful_actions == []`, so finalization runs
through `agent.py:_resolve_v1_verified_test_run` (`@1113`), whose Path-3 `if not ok: return None`
(`@1156`) independently blocks the same partial-pass repos. **Fix 3 must address both.**

---

## 2. Tiering — ship the safe half first

The two recovery groups carry very different honesty risk, so they are **separate, independently
shippable tiers**.

| Tier | Recovers | Touches failure/rc hardening? | Honesty risk |
|---|---|---|---|
| **A — revision relaxation** | mcp-atlassian, resend, pal-mcp-server (0-failure, rc=0 runs that **already pass all six done-gate conditions** and `analyze_test_run.is_effective_test_run`) | **No** | **~zero** — only stops a proven-green run from being discarded for staleness |
| **B — partial-pass acceptance** | py2many, nba_api, Xee (mixed pass/fail, rc≠0) | **Yes** — relaxes the zero-failure rule | **Real** — rests entirely on the env-defect classifier; gated behind an exhaustive truth-table test suite |

**Recommendation:** land Tier A, deploy, re-run, confirm the green-but-stale repos recover. Then land
Tier B behind its classifier tests. Tier A is provably honesty-neutral because every run it newly accepts
*already* satisfies the full six-condition gate — the only thing changed is "don't throw it away because
a later `mkdir` ran."

---

## 3. Tier A — revision-staleness relaxation (safe)

### 3.1 `src/verification_bundle.py` — replace Filter 1 with a mutation-aware scan
Add a module-level helper (insert before `_collect_effective_observed_test_commands`):
```python
def _has_env_mutation_after(record_pos: int, all_records: list) -> bool:
    """True iff any action AFTER record_pos (by list order) mutates the environment.
    Uses positional order, not environment_revision, so it works on both the in-agent
    records and the compacted scoring/replay records (which keep mutates_environment)."""
    for j, r in enumerate(all_records):
        if j > record_pos and isinstance(r, dict) and r.get("mutates_environment", False):
            return True
    return False
```
Use **list position**, not `step_index` (the draft assumed `step_index` is always present; it is not on
the compacted replay records — `mutates_environment` IS preserved by `_compact_action_records`
`agent.py:1958`). In `_collect_effective_observed_test_commands`, iterate with `enumerate(...)` and
replace the `environment_revision != final_revision` check (`@100`) with:
```python
if _has_env_mutation_after(idx, run_summary.get("successful_actions") or []):
    continue
```
`_final_environment_revision` (`@21-29`) and its call (`@90`) become unused — delete both (avoid a dead
local). Note: `successful_actions` records are already rc==0 (a partial-pass rc=1 run never enters this
list — that's why Tier A only sees clean runs, reinforcing its safety).

### 3.2 `agent.py` — guard BOTH `_invalidate_verification_group` call sites
`_record_successful_action` invalidates the verified group on env mutation in **two** branches. Guard
**both** so a *post-verification* mutation cannot wipe an already-proven run, while a *pre-verification*
mutation still clears it:

`@2254-2256` (mutating-test branch) and `@2258-2260` (non-test mutating branch) — wrap each
`self._invalidate_verification_group(...)` with:
```python
if not self._current_verification_group:
    self._invalidate_verification_group(<existing reason string>)
```
(Read `_invalidate_verification_group` `@2508` first — when the group is non-empty it clears both
`verified_test_commands` and the group; when empty it is a no-op. Guarding on "empty" therefore preserves
a populated group and is a no-op otherwise.) **Defense-in-depth:** even if a *real* install runs
post-verification and the guard skips invalidation, Filter 1's `_has_env_mutation_after` still drops the
stale run in the bundle — so a genuinely-mutated env is rejected by the second gate.

### 3.3 Tier A tests — `tests/test_verification_bundle_revision.py` (extend)
| Test | Scenario | Expect |
|---|---|---|
| `test_stale_benign_tail_accepted` | green pytest at pos 2; `echo ok` (mutates=False) at pos 3 | accepted |
| `test_stale_real_mutation_rejected` | green pytest at pos 2; `pip install x` (mutates=True) at pos 3 | rejected |
| `test_mcp_atlassian_benign_mkdir` | 2578 passed; post-test `mkdir /tmp/out` (mutates=False) | accepted |
| `test_invalidate_skipped_when_group_populated` | verify group set, then non-test mutating cmd | group preserved (assert both 2256+2260 guarded) |
| `test_invalidate_fires_when_group_empty` | mutating cmd before any verified test | group cleared (unchanged behaviour) |

---

## 4. Tier B — partial-pass acceptance (classifier-gated)

### 4.1 `src/synthesizer.py` — TWO new public methods

**(a) A real passing-tests signal** (the draft wrongly used `observation_has_effective_test_signal`):
```python
def observation_has_passing_test_signal(self, observation: str) -> bool:
    """True iff the output shows at least one test PASSED (not merely 'tests ran').
    Deliberately excludes the ambiguous 'ran N tests' / 'collected N' / 'N failed'
    signals that observation_has_effective_test_signal accepts."""
    if not observation:
        return False
    norm = self._normalize_observation_text(observation)
    for pat in (r"\b[1-9]\d*\s+passed\b",            # pytest
                r"\b[1-9]\d*%\s+tests\s+passed\b",    # ctest
                r"test result:\s+ok\."):              # cargo (all passed)
        if re.search(pat, norm, re.IGNORECASE | re.MULTILINE):
            return True
    return False
```
(Conservative: unittest "Ran N tests / FAILED(failures=k)" partial passes are **not** recovered — erring
toward reject. All current targets are pytest and emit `N passed`.)

**(b) The env-defect classifier** (insert after `observation_has_test_failure_signal` `@2952`):
```python
def observation_has_env_defect_signal(self, observation: str) -> bool:
    """True ONLY when failures indicate a BROKEN ENVIRONMENT (missing dep, collection
    failure, missing executable, required-service down). Does NOT match AssertionError,
    AttributeError, TypeError, or a bare 'N failed' (pre-existing source bugs)."""
    if not observation:
        return False
    norm = self._normalize_observation_text(observation)   # strips ANSI
    for pat in (
        r"ERROR collecting",
        r"ImportError while importing test module",
        r"error during collection",
        r"INTERNALERROR",
        r"(?:ModuleNotFoundError|ImportError):\s+No module named\s+['\"](?!tests?\.)",
        r"ImportError:\s+cannot import name",
        r"ConnectionRefusedError",
        r"Connection refused",
        r"(?:pytest|python|make):\s+(?:command not found|No such file)",
    ):
        if re.search(pat, norm, re.IGNORECASE | re.MULTILINE):
            return True
    # "collected 0 items" + a collection error — split (no cross-line .* ; that never matches)
    if re.search(r"collected\s+0\s+items", norm, re.IGNORECASE) and \
       re.search(r"\berror\b", norm, re.IGNORECASE):
        return True
    return False
```
Notes: the `(?!tests?\.)` lookahead excludes `No module named 'tests.test_x'` (a collection-topology
issue, not a missing dep — matches `artifact_verify._output_has_internal_repo_import_error_signal`
semantics). The `collected 0 items` case is split into two `re.search` calls (the draft's
`collected 0 items.*error` never matched across newlines without `DOTALL` — must-fix m3).

**(c) The majority-pass ratio** (RepoLaunch alignment, §0.5). Add `MIN_PASS_RATIO = 0.5` as a **class
attribute** of `Synthesizer` (so both `synthesizer.MIN_PASS_RATIO` and `Synthesizer.MIN_PASS_RATIO`
resolve) and a method:
```python
def observation_pass_ratio(self, observation: str):
    """passed / (passed + failed + errors), or None if no countable summary.
    Skipped tests are excluded (mirrors compute_essr effective_total)."""
    norm = self._normalize_observation_text(observation or "")
    def _count(word):
        vals = [int(m) for m in re.findall(r"(\d+)\s+" + word, norm, re.IGNORECASE)]
        return max(vals) if vals else 0
    passed, failed, errors = _count("passed"), _count("failed"), _count("errors?")
    denom = passed + failed + errors
    return (passed / denom) if denom > 0 else None
```

### 4.2 `src/verification_bundle.py` — corrected Filter 2
Replace `@108-112` with (note: passing signal, **not** effective signal; and **no**
`has_effective and not has_failure` branch):
```python
if synthesizer.is_truncated_test_output_command(command):
    continue                                   # truncated output can't prove anything
if analysis.get("is_effective_test_run"):
    commands.append(command); continue         # clean pass (≥1 passed, 0 fail) — also rejects collect-only
# partial pass: MAJORITY passed, failures are NOT env-defects (RepoLaunch bar, §0.5)
_ratio = synthesizer.observation_pass_ratio(observation)
if (synthesizer.observation_has_passing_test_signal(observation)
        and synthesizer.observation_has_test_failure_signal(observation)
        and not synthesizer.observation_has_env_defect_signal(observation)
        and _ratio is not None and _ratio >= synthesizer.MIN_PASS_RATIO):
    commands.append(command); continue
# everything else (0-passed, collect-only, env-broken) → dropped
```

### 4.3 `agent.py` — v1 Path-3 relaxation (`_resolve_v1_verified_test_run`)
Add a helper that **delegates to the single classifier** (no `_COLLECTION_PATTERNS`, must-fix m4):
```python
def _v1_output_is_env_defect(output: str) -> bool:
    from src.synthesizer import Synthesizer
    return Synthesizer().observation_has_env_defect_signal(output or "")
```
Replace the Path-3 `if not ok: return None` (`@1156`) with:
```python
if not ok:
    # partial-pass: accept only if MAJORITY passed AND env is not broken (RepoLaunch, §0.5)
    if not _shows_execution(out):              # 0 passed / collect-only → reject
        return None
    if _v1_output_is_env_defect(out):          # ImportError/collection/service → reject
        return None
    _ratio = Synthesizer().observation_pass_ratio(out)
    if _ratio is None or _ratio < Synthesizer.MIN_PASS_RATIO:
        return None                            # sub-majority pass-rate → reject
    print("[v1] finalize: accepting majority-pass run (non-env failures only)")
```
And in the ledger append (`@1162-1173`) record the **real** rc (must-fix m5):
```python
rc_actual = 0 if ok else 1
... ActionEvent(..., rc=rc_actual, ...)
```
(`_shows_execution` already requires `≥1 passed` and, post-Fix-2, strips ANSI — it is the v1-path
equivalent of `observation_has_passing_test_signal`; keep using it here for consistency.)

### 4.4 Tier B tests — see §6.

---

## 5. Honesty guardrails (CORRECTED)

Every class of run that MUST still be rejected, and the exact mechanism now enforcing it:

| Must reject | Enforcement (post-Fix-3) |
|---|---|
| **5.1 Zero-passed** (only errors/failures) | partial-pass branch requires `observation_has_passing_test_signal` / `_shows_execution` (`\b[1-9]\d*\s+passed\b`); both are **False** for `"0 passed"`. *(Draft used `observation_has_effective_test_signal` which is True on `N failed` — fixed, C2.)* |
| **5.2 Collect-only** | clean branch is `analyze_test_run.is_effective_test_run` (False for collect-only); the dangerous `has_effective and not has_failure` branch is **deleted** (C3); partial branch needs a real pass token. |
| **5.3 Env-broken** (ImportError / ModuleNotFoundError-dep / `ERROR collecting` / INTERNALERROR / Connection refused / command not found) | `observation_has_env_defect_signal` returns True → partial branch's `not is_env_defect` rejects; v1 path `_v1_output_is_env_defect` rejects. `(?!tests?\.)` keeps internal `tests.*` topology out of the env-defect set. |
| **5.4 Truncated / piped** (`pytest \| head`) | `is_truncated_test_output_command` checked **first** in Filter 2; v1 path reads full raw `sandbox.execute` output (not the ledger), so no truncation there. |
| **5.5 Real post-test mutation** (`pip install` after the green run) | `_has_env_mutation_after` drops the stale run (`command_mutates_environment` classifies installs as mutating); the `_invalidate_verification_group` guard is *only* skipped for already-verified groups, and the bundle filter is the backstop. |
| **5.6 Venv-wrapped / `--ignore=` gaming** | **Untouched.** The six-condition done-gate (`_verified_test_run_passed` conditions 3 & 4) is not modified by Fix 3. |
| **5.6b Sub-majority pass-rate** (e.g. 26/59 = 0.44; or 3/1000 with non-env failures) | `observation_pass_ratio < MIN_PASS_RATIO` → partial branch rejects; v1 Path-3 returns `None`. Stops low-confidence "successes" (RepoLaunch majority bar, §0.5). |

### 5.7 The `"32 passed, 1 error"` behaviour decision (must decide before coding)
A bare `"1 error"` (no `ImportError`/collection text) with 32 passes is, under Tier B, **accepted**
(`has_pass`, `has_failure`, not env-defect). The existing test
`test_rejects_agent_reported_bundle_when_prior_output_had_errors` asserts rejection.
**Decision: keep Tier B conservative — treat a bare `"N error"` as a potential env-defect and REJECT**
by adding `r"\b[1-9]\d*\s+error(?:s)?\b"` to a *separate* "ambiguous → reject" check (NOT to
`observation_has_env_defect_signal`, to keep that method's truth table clean). Rationale: pytest reports
collection/setup problems as `error` (distinct from `failed`), so `N error` is more likely an env/setup
defect than a source bug. This keeps the existing test green and avoids a speculative honesty loosening.
Revisit only if a real target repo is blocked by it.

### 5.8 The `MIN_PASS_RATIO` trade-off (DECIDE BEFORE CODING)
Default `0.5` (RepoLaunch "majority"). This is the one knob that changes *which* repos finalize:

| Repo | pass-ratio | @0.5 (RepoLaunch) | under prior ≥1-passed |
|---|---|---|---|
| py2many 1601/1603 | 1.00 | ✅ accept | ✅ |
| nba_api 686/689 | 1.00 | ✅ accept | ✅ |
| **Xee 26/59** | **0.44** | ❌ **reject** | ✅ would finalize |
| hypothetical 3/1000 (non-env) | 0.003 | ❌ reject | ✅ would finalize |

**The tension:** RATBench ESSR÷all is *pass-rate-weighted*, so finalizing Xee would contribute its honest
**0.44** to the headline; rejecting it scores **0**. A **lower** threshold maximises the ESSR headline
(every honest fraction counts); a **higher** threshold maximises per-repo confidence and avoids
low-quality "successes" (better EBSR/hollow honesty, closer to RepoLaunch's SWE-bench-grade bar).
"core/integration pass" is not cheaply detectable, so the aggregate ratio is the approximation.

**Open decision for the user:** `0.5` (RepoLaunch-faithful), `~0.3` (ESSR-leaning), or `>0` (pure
≥1-passed, the pre-RepoLaunch behaviour). Recommended: **0.5**, matching RepoLaunch and the stated goal.

---

## 6. TDD test list

**Tier A** — see §3.3.

**Tier B — `tests/test_synthesizer.py` (new `ObservationEnvDefectSignalTests` + passing-signal):**

| Input | `env_defect_signal` | `passing_test_signal` |
|---|---|---|
| `ModuleNotFoundError: No module named 'fastapi'` | True | False |
| `ModuleNotFoundError: No module named 'tests.test_x'` | **False** | — |
| `ImportError: cannot import name 'edsl'` | True | — |
| `ERROR collecting tests/foo.py` | True | — |
| `INTERNALERROR> ... conftest.py` | True | — |
| `ConnectionRefusedError: [Errno 111]` | True | — |
| `pytest: command not found` | True | — |
| `AssertionError: assert 1 == 2` | **False** | — |
| `AttributeError ...` / `TypeError ...` | **False** | — |
| `"5 failed in 2.3s"` (no import text) | **False** | False |
| `"1601 passed, 2 failed"` | False | **True** |
| `"5 failed, 0 passed"` | False | **False** (← the C2 hollow-pass guard) |
| `"collected 150 items"` (collect-only) | False | **False** (← the C3 guard) |
| `"\x1b[31mModuleNotFoundError...\x1b[0m"` (ANSI) | True | — |

**Tier B — `tests/test_verification_bundle_partialpass.py` (new):**
| Scenario | Expect |
|---|---|
| 1601 passed + 2 AssertionError-failed | accepted |
| 100 passed + 2 failed w/ `ModuleNotFoundError: No module named fastapi` | rejected |
| 50 passed + `ERROR collecting` | rejected |
| 200 passed + `ConnectionRefusedError` | rejected |
| 0 passed + only failures (no env-defect) | **rejected** (no passing signal) |
| 0 passed + collect-only | **rejected** |
| `pytest \| head` (1601 passed) | rejected (truncation first) |
| 32 passed + bare `1 error` | rejected (per §5.7) |
| **26 passed + 33 AssertionError-failed** (ratio 0.44) | **rejected** (sub-majority, §5.8) |
| **3 passed + 997 failed** (non-env) | **rejected** (sub-majority) |
| 1601 passed + 2 failed (ratio 0.999) | accepted |

Plus `observation_pass_ratio` unit tests: `"1601 passed, 2 failed"`→0.999; `"26 passed, 33 failed"`→0.44;
`"5 passed, 5 failed, 0 errors"`→0.5 (boundary, accepted at `>=`); `"10 passed, 2 skipped"`→1.0 (skips
excluded); `"collected 5 items"`→`None`.

**Tier B — `tests/test_v1_finalize_partial_pass.py` (new):** `_resolve_v1_verified_test_run` Path 3 —
partial-pass accepted; 0-passed → None; collect-only → None; ImportError → None; ConnectionRefused → None;
clean `ok=True` unchanged; **assert the appended ledger record has `rc==1` for a partial pass** (m5).

**Regression:** keep `test_rejects_agent_reported_bundle_when_prior_output_had_errors` green via §5.7.

---

## 7. Risks / interaction with Fix 2

- **No double-finalize:** `_auto_finalize_from_verified_tests` (`agent.py:2292`) is guarded by
  `if not self.verified_test_commands`. Fix 3 only changes whether `_resolve_v1_verified_test_run`
  returns a command vs `None`; downstream finalize is unchanged.
- **Fix 2 reuse, no conflict:** Fix 3's v1 path uses `_shows_execution` (ANSI-stripped by Fix 2) and reads
  raw `sandbox.execute` output (not the `_truncate_output`-shortened ledger record), so the full pytest
  summary is visible.
- **Single classifier:** both paths use `synthesizer.observation_has_env_defect_signal` — no divergent
  copies (m4). Truth table is tested once.
- **Middle-truncation caveat:** `observation_summary` on compacted records is head+tail truncated; an
  env-defect line buried in the middle of huge output could be missed → a partial-pass wrongly accepted.
  Tier B reads `observation` first (full) and falls back to `observation_summary`; document that
  mid-truncated env-defects are a known small residual (errs toward accept). The v1 Path-3 reads full raw
  output, so it is unaffected.

---

## 8. Sequencing

**Commit 0 — planner goal (prompt-only, §8b):**
0. `feat(planner): aim for RepoLaunch majority-pass goal (non-env failures tolerated)` —
   `src/envstate/planner.py` objective text + planner-schema test. Ship first; independent of the gate.

**Tier A (2 files, 1 commit):**
1. `feat(verification): accept proven-green test runs with a benign post-test tail (Fix 3 Tier A)` —
   `src/verification_bundle.py` (`_has_env_mutation_after` + Filter 1) + `agent.py` (both
   `_invalidate_verification_group` guards) + `tests/test_verification_bundle_revision.py`.

**Tier B (3 files, 2 commits):**
2. `feat(synthesizer): env-defect + passing-test classifiers (Fix 3 Tier B)` — `src/synthesizer.py`
   (`observation_has_env_defect_signal`, `observation_has_passing_test_signal`) +
   `tests/test_synthesizer.py`.
3. `fix(verification): finalize partial passes when failures are non-env (Fix 3 Tier B)` —
   `src/verification_bundle.py` Filter 2 + `agent.py` v1 Path 3 (`_v1_output_is_env_defect`, real rc) +
   `tests/test_verification_bundle_partialpass.py` + `tests/test_v1_finalize_partial_pass.py`.

After each: scoped pytest green, then full suite
`python3 -m pytest tests/ --ignore=tests/test_docker_build.py --ignore=tests/test_run_rat_benchmark.py -q`
(expect the 3 known pre-existing failures only).

---

## 8b. Companion change — planner objective (RepoLaunch goal)

Separate from the finalize gate, but the *other half* of the goal shift (§0.5): make the agent **aim**
for what the gate now **certifies**. In `src/envstate/planner.py` (~lines 58-59, 105, 129) change the
stated objective from *"reach at least one passed test"* to:

> *"Execute the full suite with the bare interpreter (`python -m pytest -q`) and get the **majority** of
> tests passing. The only acceptable remaining failures are **non-environment** failures (pre-existing
> source bugs, test-logic, or external/network). Any `ImportError` / `ModuleNotFoundError` / collection /
> missing-service failure means the environment is NOT done — keep fixing."*

Keep `done_when` a real execution (not collect-only). **Prompt-only, low-risk, its own commit** (commit 0,
before the gate changes), testable via the existing planner-schema tests. Rationale: today the planner
aims *lower* (`≥1 passed`) than the gate certifies (`majority`, post-Fix-3) — aligning them removes a
source of premature giveup (the agent stops at "one passed" and never drives the suite to majority) and
of over-driving (chasing 100% green on repos with unfixable pre-existing failures — the py2many/nba_api
"flail" pattern). The Planner should explicitly tolerate non-env failures, matching the gate.

## 9. Verification (deploy + targeted re-run)

1. **Deploy:** `DEPLOY_SRC=/Users/john/john-planner-v1 DEPLOY_BRANCH=john-planner-v1 ./deploy.sh --apply`
   — confirm `.deployed_commit`.
2. **Persist run_summary (instrumentation):** before the run, confirm the agent writes per-repo
   `successful_actions` (with `mutates_environment`) to disk so the bundle gate is sizable post-hoc — the
   full50 artifacts did NOT persist this, which is why the partial-pass counts were unverifiable. Add a
   one-line dump of the finalize decision: `print(f"[v1] finalize repo=... shows_exec=... env_defect=...")`.
3. **Build the subset** (extend `outputs/make_covfix_subset.py` TARGETS with the Fix-3 repos), then on the
   VM: `ssh root@167.233.64.96 'cd /opt/rat-bench-integration && python3 -' < outputs/make_covfix_subset.py`
   — TARGETS must include the **bundle victims** `sooperset/mcp-atlassian`,
   `BeehiveInnovations/pal-mcp-server`, `resend/resend-python`, `yihong0618/bilingual_book_maker`, the
   **partial-pass** repos `py2many/py2many`, `swar/nba_api`, `google/Xee`, plus the 9 covfix repos
   (regression check).
4. **Run** (fresh root-path, detached tmux), same harness as covfix:
   `... run_rat_benchmark.py --tier all --arm v1 --llm deepseek/deepseek-v4-flash --concurrency 4
   --num-turn 20 --repos-json datasets/coverage_fix_targets.json --model dockeragent
   --repair-mode selfverify --repair-rounds 2 --root-path ./rat_run_v1_fix3 > rat_run_v1_fix3.console.log 2>&1`
5. **Compare:** point `outputs/compare_covfix.py` AFTER root at `rat_run_v1_fix3`; success =
   mcp-atlassian/resend/pal-mcp-server/bilingual flip to `success` with their real pass-rate (Tier A), and
   py2many/nba_api/Xee finalize via `done_flag` with `N passed` (Tier B). **Verify ESSR is honest:**
   `scripts/compute_essr.py` should credit the *actual* pass-rate (py2many 1601/1603 → 0.999), not 1.0.
6. **Honesty regression check:** confirm no repo that was a true failure (0-passed / ImportError /
   collect-only) is now scored `success`. Grep the new runs for any `success` with `pytest_total_tests==0`
   or `pass_rate==0` — there must be none.

### Expected
| Repo | Tier | Before | After |
|---|---|---|---|
| mcp-atlassian | A | giveup (stale 2578/0) | success, ~1.0 |
| resend-python | A | giveup (429/0) | success, ~1.0 |
| pal-mcp-server | A | giveup (870 passed) | success |
| py2many | B | giveup (1601/1603) | success, 0.999 |
| nba_api | B | giveup (686/689) | success, 0.996 |
| Xee | B | giveup (26/59) | **threshold-dependent** — `success ~0.44` at MIN_PASS_RATIO≤0.44, else stays giveup (§5.8) |

**Honest framing:** ESSR÷all is `quality × coverage`. Tier A converts the highest-quality (full-green)
runs first; Tier B adds genuine partial-pass coverage at its real pass-rate. Neither tier may certify a
broken or empty environment — §5 is the contract.
