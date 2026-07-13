# Execution spec: enriched failure histogram — `[collect]`/`[run]` tags

**Status:** ready to execute. Self-contained; assumes NO prior conversation context.
**Effort:** ~4 lines of production code + 2 tests. Near-zero risk (display-only, binary field).
**Branch:** `john-v3-multi-lang`. All file:line refs verified against the tree on 2026-07-12.

---

## Why (context for a fresh session)

The script-only "react" repair agent (`src/react_repair/`) shows the model a ranked histogram
of pytest failure causes each turn (`loop.py:_observation` → `pytest_summary.summarize` →
`pytest_summary.format_breakdown`). Today every cause row looks the same:

```
40 × ModuleNotFoundError: No module named 'psycopg2'
23 × AssertionError
```

The agent can't tell an **import/collection failure** (fix the build: add an install) from an
**execution failure** (provision a service/config, or it's a residual the environment can't
fix). The `Cause` dataclass already records which it is (`Cause.outcome`), but the renderer
drops it. This change surfaces it as a `[collect]` / `[run]` tag:

```
40 × [collect] ModuleNotFoundError: No module named 'psycopg2'
23 × [run]     AssertionError
```

This is change #1 of a larger plan; #2 turns out to already be satisfied (see below). Later,
separate changes (#3 "run-all-record build") will let the agent see build+collection+run in one
turn — **out of scope here.**

---

## Scope

- **IN:** render `Cause.outcome` as a `[collect]`/`[run]` tag in `format_breakdown`; add a
  regression test that `VERIFY_TEST_CMD` keeps `--continue-on-collection-errors`.
- **OUT (do NOT touch):** `run_script`/`build_and_test` (the run-all-record build change),
  adding a separate `pytest --collect-only` command, any `Gref`/gold-set scoring, keep-best,
  the gate/DONE condition, the `_observation` header. Those are separate specs.

---

## Change #1 — tag each cause `[collect]` or `[run]`

**File:** `src/react_repair/pytest_summary.py`
**Function:** `format_breakdown` (currently lines ~127-140).

### Facts (verified)
- `Cause.outcome` (dataclass field, `pytest_summary.py:45`) is exactly `"ERROR"` (collection)
  or `"FAILED"` (execution). Set at `pytest_summary.py:118`
  (`"ERROR" if title.startswith("ERROR") else "FAILED"`). No third value is possible.
- `format_breakdown` output is **display-only** — it is fed to the LLM observation
  (`loop.py:_observation`, ~line 86-92). Grep confirms nothing parses it programmatically.

### Edit
Current loop body inside `format_breakdown`:
```python
    for c in causes[:top]:
        detail = f": {c.detail}" if c.detail else ""
        rows.append(f"  {c.count} × {c.exc}{detail}")
```
Replace with:
```python
    for c in causes[:top]:
        detail = f": {c.detail}" if c.detail else ""
        tag = "collect" if c.outcome == "ERROR" else "run"
        rows.append(f"  {c.count} × [{tag}] {c.exc}{detail}")
```

- Map `ERROR` → `collect`, everything else → `run`. No `else`/error branch: an unexpected
  value degrades to `run` (an execution failure), never mislabeled as a build fix.
- Leave the `…and N more cause(s)` rollup line (the block right after this loop) **untagged** —
  it aggregates possibly-mixed causes.

### Docstring note (add to `format_breakdown`)
Add a sentence documenting the known limitation so nobody "fixes" it later by accident:
> A `[collect]` row's `count` is MODULES affected, not tests affected — an unimportable module
> emits one collection-error block regardless of how many tests it hides, so `[collect]` rows
> under-rank. Recovering "blocks N tests" needs the hidden gold set (final-only) or the graph
> arm; do not attempt it here. The tag is diagnostic value independent of the count.

---

## Change #2 — `--continue-on-collection-errors` (already satisfied; just lock it)

### Facts (verified)
`src/envstate/constants.py:17`:
```python
VERIFY_TEST_CMD: str = "python -m pytest -q --continue-on-collection-errors"
```
The react full run uses this (`entry.py:81 run_tests` → `_test_command` @ `entry.py:40-53` →
`f"{VERIFY_TEST_CMD} $F"`). So a repo with broken modules still RUNS the tests that collected,
and the single run's output already contains BOTH collection-`ERROR` and execution-`FAILED`
blocks — which is why `summarize()` already produces a mixed histogram today. **No production
change needed.** `VERIFY_TEST_CMD` is shared by ~5 consumers (`envstate/gates.py:50`,
`graph_scheduler.py:64`, `orchestrator.py`), which is why the flag lives on the shared constant.

### Edit
Add a regression lock so a future refactor can't silently drop the flag.
**File:** `tests/test_constants_single_source.py` (add one test function):
```python
def test_verify_test_cmd_continues_on_collection_errors():
    """A broken module must not abort the whole run (rc=2) — the react histogram
    depends on collection errors AND execution failures appearing in one run."""
    from src.envstate.constants import VERIFY_TEST_CMD
    assert "--continue-on-collection-errors" in VERIFY_TEST_CMD
```

---

## Tests

**File:** `tests/react_repair/test_pytest_summary.py` — add a test for the tag. Match the
existing test style in that file (check how `Cause`/`format_breakdown` are imported there).
Illustrative:
```python
from src.react_repair.pytest_summary import Cause, format_breakdown

def test_format_breakdown_tags_collect_and_run():
    causes = [
        Cause(exc="ModuleNotFoundError", detail="No module named 'psycopg2'",
              count=3, outcome="ERROR", module="tests/test_db.py"),
        Cause(exc="AssertionError", detail="", count=5, outcome="FAILED",
              module="tests/test_logic.py"),
    ]
    out = format_breakdown(causes)
    assert "[collect] ModuleNotFoundError" in out
    assert "[run] AssertionError" in out
    # counts / exc / detail unchanged
    assert "3 × [collect] ModuleNotFoundError: No module named 'psycopg2'" in out
    assert "5 × [run] AssertionError" in out
```
(Confirm the exact `Cause(...)` field order/keywords against `pytest_summary.py:40-47` before
writing — use keyword args as above to be order-independent.)

Plus the `VERIFY_TEST_CMD` lock test above.

---

## Verification

```bash
cd /Users/john/john-v3-multi-lang
python -m pytest tests/react_repair/test_pytest_summary.py tests/test_constants_single_source.py -q
# then confirm nothing else broke that renders the histogram:
python -m pytest tests/react_repair/test_observation.py tests/react_repair/test_loop.py -q
```

---

## Acceptance criteria

- [ ] `format_breakdown` prefixes each cause row with `[collect]` (for `outcome=="ERROR"`) or
      `[run]` (otherwise); counts, exception, detail, ordering, and the `…and N more` rollup are
      unchanged.
- [ ] Docstring limitation note added to `format_breakdown`.
- [ ] New tag test passes in `tests/react_repair/test_pytest_summary.py`.
- [ ] New `VERIFY_TEST_CMD` regression test passes and would FAIL if the flag were removed.
- [ ] `test_observation.py` and `test_loop.py` still pass (histogram consumers unaffected).
- [ ] Production diff touches ONLY `src/react_repair/pytest_summary.py` (~4 lines + docstring).
      No change to `run_script`, `build_and_test`, `_observation`, gates, or any `VERIFY_TEST_CMD`
      consumer.

---

## What this deliberately does NOT do (next specs)

- `#3` run-all-record build (stop halting at the first failing install line, show per-line
  status) — the substantive change that lets build-failure turns still show collection+run.
- Separate `pytest --collect-only` command (only needed for final `Gref` scoring + a cost-gate
  on huge repos, NOT per-turn).
- Any gold-set (`Gref`) recall scoring — that is final-only, never in the loop.
