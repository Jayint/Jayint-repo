# Plan: Coverage Fixes 1 & 2 (verified against live code)

Branch `john-planner-v1` · 2026-06-12 · recovers the self-inflicted coverage regressions
from the P1/P4 gate changes (see `outputs/v1_coverage_loss_report.md`).

Two read-only Sonnet investigators traced the real code paths. **Corrections to the
original (digest-based) claims are noted inline.**

> **Reviewed 2026-06-12** against live code — all line numbers / imports / mechanisms verified.
> Key review change: **Fix 1's primary edit is now the test-name-aware "smart variant", NOT a flat
> `[:100]` cap** (the depth-first `os.walk` ordering makes a flat cap unreliable — see below).
> Plus a robust test-entry predicate (avoids `latest`/`fastest` false matches) and Fix-2
> tail-robustness + scope notes.

---

## FIX 1 — repo_layout truncation → false `no_real_test_suite` giveup (7 repos)

### Verified root cause
`agent.py:895–898` (`_run_v1`) builds the layout the Planner sees:
```python
# Derive repo_layout tuple from the first non-empty lines of structure.txt.
_repo_layout: tuple = tuple(
    ln.strip() for ln in _repo_structure.splitlines()[:20] if ln.strip()
)
```
The `[:20]` cap keeps the sentinel header + `repo/` + ~18 root files, so any `tests/`
beyond line 20 is invisible. The Planner correctly applies the P4 rule on what it sees →
false `giveup(no_real_test_suite)`. **Confirmed from traces:** LibreTranslate's `repo_layout`
is exactly 20 entries ending at `pyproject.toml`; `tests/` is at line 21+. Same for DDNS,
wafw00f. Flows: `agent.py:913 initial_map(repo_layout=...)` → `WorldModelMap.repo_layout` →
`planner.render_planning_view` (no further truncation) → P4 prompt rule.

**Correction A:** the "markitdown structure rooted at workplace not /testbed" claim is **FALSE**
— structure IS generated from the cloned repo root (`self.workplace`).
**Correction B (new finding):** `src/image_selector.py` creates `logs/image_selector_logs/`
*before* `os.walk` generates structure.txt, so a runtime `logs/` dir pollutes the layout and
consumes slot 20 (this is markitdown's actual extra problem).

**⚠ Review correction C (why a flat cap is unreliable):** `image_selector._generate_repo_structure`
(line 350-373) is a **depth-first `os.walk` in sorted order**. It emits the root dir + root *files*,
then **fully recurses each subdir alphabetically before reaching the next**. So a top-level `tests/`
directory line appears only **after every alphabetically-earlier subdir's entire subtree** (`.github/`,
`docs/`, `src/`…). A repo with a large `docs/` or `.github/` can push `tests/` well past line 100 even
though it is top-level. Therefore a flat `[:N]` cap is a gamble. **Fix 1 must explicitly pull
test-named entries regardless of position** (the smart variant below). The earlier "tests/ within the
first 40-60 lines" claim was NOT verified against the full structure.txt (the cycles only show the
already-`[:20]`-cut layout).

### Edits
**Edit 1 (PRIMARY) — `agent.py:895–898`:** smart variant — first 60 context lines + every test-named
entry from the rest, sentinels stripped. Requires no new import (`re` already imported at `agent.py:2`).
```python
_LAYOUT_SENTINELS = frozenset({
    "------ begin repository structure ------",
    "------ end repository structure ------",
})

def _is_test_entry(entry: str) -> bool:
    """A structure line that signals a test suite exists (robust, no false matches
    like 'latest'/'fastest'/'contest')."""
    base = entry.rstrip("/").rsplit("/", 1)[-1].lower()
    return (
        base in ("test", "tests", "conftest.py")
        or (base.startswith(("test_", "tests_")) and base.endswith(".py"))
        or base.endswith(("_test.py", "_tests.py"))
    )

_layout_lines = [
    ln.strip()
    for ln in _repo_structure.splitlines()
    if ln.strip() and ln.strip().lower() not in _LAYOUT_SENTINELS
]
# 60 lines of context + any later test-named entries (depth-first walk can bury tests/ deep)
_extra_test_lines = [ln for ln in _layout_lines[60:] if _is_test_entry(ln)]
_repo_layout: tuple = tuple(dict.fromkeys(_layout_lines[:60] + _extra_test_lines[:30]))
```
Note: `ln.strip()` flattens the tree (loses indentation/hierarchy) — that is fine and pre-existing;
the Planner only needs to *see* a `tests/`/`test_*.py` entry exists. `_is_test_entry` is defined as a
module-level helper in `agent.py` (or inline). **Do NOT use the flat `[:100]` or a bare `\btest` regex**
(`\btest` misses `conftest.py`/`pytest.ini` and the flat cap misses deep `tests/`).

**Edit 2 — `src/image_selector.py` `SKIP_DIRS` (line 355):** add `'logs'` so the runtime log
dir never pollutes structure.txt (also fixes markitdown's slot-20 displacement). Note this also skips
any *real* repo `logs/` dir — acceptable (irrelevant to test setup). Cleaner-but-larger alternative
(flag, don't do now): reorder so `_generate_repo_structure` runs before `_init_log_dir`.

**Optional Edit 3 (defense-in-depth) — `planner.py` P4 prompt:** before `giveup(no_real_test_suite)`,
require a `pytest --collect-only -q` probe; only give up if collection finds nothing. Backstops
any residual truncation for deep monorepos.

### Risks / tests
- Token cost: ~60 context lines + ≤30 test lines per cycle ≈ +300–500 tokens. Negligible.
- The smart variant surfaces `tests/` even when buried deep (deep monorepos covered); Edit 3 is then optional belt-and-suspenders.
- Tests (new, `tests/test_v1_repo_layout_cap.py`, asserting the exact Edit-1 logic): (a) `_is_test_entry` True for `tests/`,`test/`,`test_x.py`,`x_test.py`,`conftest.py`; False for `latest.py`,`fastest.py`,`contest.py`,`manifest.in`; (b) a `tests/` dir at structure line 150 (after a big `docs/` subtree) STILL appears in the layout; (c) sentinels excluded; (d) ≤90 entries total. E2E re-validate: wafw00f, LibreTranslate, DDNS, markitdown (markitdown needs both edits).

---

## FIX 2 — done-gate misses the pytest pass summary (head-truncation + ANSI) (2+ repos)

### Verified root causes
**A (PRIMARY) — HEAD truncation, `src/envstate/build_agent.py:532`:**
```python
record = CommandRecord(cmd=action, rc=rc, output=output[:2000])
```
`output[:2000]` keeps the FIRST 2000 chars; the pytest summary (`"544 passed"`) is at the END
(after ~110k chars of per-test noise for django-oauth) → discarded → `_shows_execution`
returns False → `done_flag` never fires despite rc=0 + 544 passing.
**Correction:** the claimed `agent.py:966 text[:400]` is a **red herring** — it only truncates
the diagnostic JSONL log, never the gate input.

**B (SECONDARY) — ANSI breaks the regex, `src/envstate/maintainer.py:89,93–107`:**
```python
_RE_N_PASSED = re.compile(r"\b([1-9]\d*)\s+passed\b", re.IGNORECASE)
```
`\x1b[1m5 passed` → the `m` (word char) before `5` (word char) means `\b` does NOT match →
no match. Demonstrated: `_RE_N_PASSED.search("\x1b[1m5 passed\x1b[0m")` → None. pytest emits
`\x1b[1m`/`\x1b[32m` around the count by default. (`analyze_test_run`/condition-4 already
strips ANSI via the synthesizer; only `_shows_execution`/condition-6 is broken — but both must
pass, so the gate is blocked.)

### Edits
**Edit A — `build_agent.py:532`:** head+tail hybrid (preserve tracebacks at head, summary at tail).
Define `_truncate_output` as a module-level helper near the top (e.g. right after `LOCAL_BUDGET` at
line 20; `build_agent.py` already imports nothing new — pure slicing):
```python
_OUTPUT_HEAD, _OUTPUT_TAIL = 1500, 800   # tail raised to 800 (see risk: pytest-cov/warnings can trail the summary)
_OUTPUT_LIMIT = _OUTPUT_HEAD + _OUTPUT_TAIL
def _truncate_output(output: str) -> str:
    if len(output) <= _OUTPUT_LIMIT:
        return output
    return output[:_OUTPUT_HEAD].rstrip() + "\n...[output truncated]...\n" + output[-_OUTPUT_TAIL:].lstrip()
# line 532:
record = CommandRecord(cmd=action, rc=rc, output=_truncate_output(output))
```

**Edit B — `maintainer.py` `_shows_execution`:** strip ANSI before matching:
```python
_RE_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
def _shows_execution(output: str) -> bool:
    if not output:
        return False
    clean = _RE_ANSI.sub("", output)
    return bool(_RE_N_PASSED.search(clean) or _RE_RAN_N_TESTS.search(clean))
```
(Same ANSI regex already used by `synthesizer._normalize_observation_text`. Covers
`_RE_RAN_N_TESTS` too.)

### Risks / tests
- Maintainer LLM reads failure signatures from output → tracebacks are at the HEAD (preserved by 1500); failure *summaries* are at the END (now preserved by the 800 tail — an improvement over head-only). `_is_stuck` equality still holds (same deterministic truncation both sides). No regression.
- **Tail-robustness (review):** the final `=== N passed ===` line is normally the last line, captured by the 800 tail. But `pytest --cov` prints a coverage table *after* the summary, and trailing warnings can too — if that trailer exceeds the tail, `N passed` falls outside it. `VERIFY_TEST_CMD` (no `--cov`) is safe; BuildAgent commands aren't guaranteed. 800 covers the common cases; if a repo still fails to finalize, raise the tail or (more robust, optional) have `_truncate_output` also append the last line matching `_RE_N_PASSED`/`_RE_RAN_N_TESTS`.
- Tests (`tests/test_maintainer_narrowed.py` + `tests/test_build_agent.py`): `_shows_execution("\x1b[1m5 passed\x1b[0m")`→True; `\x1b[32m182 passed\x1b[0m`→True; `Ran 5 tests`→True; `collected 5 items`→False; `0 passed`→False; `""`→False; `_truncate_output` keeps head+tail markers of a 100k string and total ≤ `_OUTPUT_LIMIT`+30; gate fires when `N passed` is only in the tail of a 5000-char output (incl. an ANSI-wrapped variant).

### ⚠ Scope interaction (review) — Fix 2 only lands CLEANLY-passing repos
The done-gate also requires `analyze_test_run → is_effective_test_run`, which rejects **any** failure
signal. Fix 2 recovers a repo only if its in-loop run was 0-failure (django-oauth/darts are listed as
clean passes). **If the run has even one failing test, Fix 2 alone won't finalize it — it needs Fix 3
(soften the gate to ≥1 passed).** During implementation, confirm the actual pass/fail counts for
django-oauth & darts; if they carry failures, the "+2 repos" lands only after Fix 3.

### Secondary (flag, not in scope)
- S1: `_resolve_v1_verified_test_run` step-1 ledger scan reads `ev.stdout` which `BuildAgent._append_ledger_event` never sets (always `""`) → step 1 always fails. Moot once Edit A makes the in-loop gate fire, but worth a follow-up (store stdout or use summary).
- S2: `VERIFY_TEST_CMD = "python -m pytest -q"` lacks `DJANGO_SETTINGS_MODULE` → the active-verify fallback fails for Django repos. Separate correctness issue.

---

## Sequencing
Both fixes are independent, small, low-risk, TDD. Fix 1 = `agent.py` + `src/image_selector.py`;
Fix 2 = `src/envstate/build_agent.py` + `src/envstate/maintainer.py` — **four disjoint files**, so
the two fixes can be implemented by two parallel Sonnet subagents. Commit each fix separately.

## Verification (re-run the failed repos)
After both fixes are committed and the local suite is green:
1. **Deploy:** `DEPLOY_SRC=/Users/john/john-planner-v1 DEPLOY_BRANCH=john-planner-v1 ./deploy.sh --apply`
   then confirm `.deployed_commit` and the new markers on the VM.
2. **Build the targeted subset** (9 repos Fix 1+2 should recover) — on the VM:
   `cd /opt/rat-bench-integration && python3 - < outputs/make_covfix_subset.py`
   (creates `datasets/coverage_fix_targets.json`).
3. **Run it** (fresh root-path, detached tmux):
   `tmux new-session -d -s covfix 'cd /opt/rat-bench-integration && export RAT_ROOT=/opt/runanything/src DOCKERAGENT_ROOT=/opt/rat-bench-integration RAT_PYTEST_TIMEOUT=1800 DOCKERAGENT_ENABLE_V1=1 && /opt/rat_venv/bin/python run_rat_benchmark.py --tier all --arm v1 --llm deepseek/deepseek-v4-flash --concurrency 4 --num-turn 20 --repos-json datasets/coverage_fix_targets.json --model dockeragent --repair-mode selfverify --repair-rounds 2 --root-path ./rat_run_v1_covfix > rat_run_v1_covfix.console.log 2>&1'`
4. **Compare before/after** (per repo, did it flip from no_dockerfile/build_failed → executing?):
   `cd /opt/rat-bench-integration && python3 - < outputs/compare_covfix.py`
   Success = the Fix-1 repos no longer `no_dockerfile`/`giveup(no_real_test_suite)`, and the genuine
   ones (LibreTranslate, DDNS, wafw00f, markitdown) reach `success` with a real pass-rate; the Fix-2
   repos (django-oauth, darts) finalize via `done_flag` with `N passed`.
5. Watch the OpenRouter balance (deepseek-flash is cheap, ~$0.01–0.02/repo; 9 repos ≪ $1). Then
   optionally re-run the full 50 for the headline ESSR vs arm0 (0.239).

(Scripts `outputs/make_covfix_subset.py` and `outputs/compare_covfix.py` already exist locally and are
deployed with the tree; both are piped via stdin to avoid SSH quote issues.)
