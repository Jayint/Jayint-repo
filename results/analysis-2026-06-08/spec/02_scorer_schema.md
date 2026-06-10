# 02 — Scorer Schema: `run_pytest_results.json` Contract

**Role:** R2 — Scorer Schema  
**Date:** 2026-06-08  
**Source of truth:** `/tmp/runanything/src/eval/common/scorers.py`, `/tmp/runanything/src/libkit/tools/run_pytest.py`

---

## 1. Exact JSON Schema of `run_pytest_results.json`

The file lives at `{root_path}/output/{full_name}/run_pytest_results.json`
(scorers.py line 88 constructs this path exactly).

```
{
  "summary": {
    "total_tests":  <int>,   // scorers.py:104 reads this key
    "passed":       <int>,   // scorers.py:103
    "failed":       <int>,   // scorers.py:104
    "skipped":      <int>,   // scorers.py:106
    "errors":       <int>,   // scorers.py:105
    "xfailed":      <int>,   // produced by run_pytest.py; NOT read by scorers
    "xpassed":      <int>    // produced by run_pytest.py; NOT read by scorers
  },
  "error_breakdown": {       // scorers.py:100 reads this directly
    "<ErrorBucketName>": <int>,
    ...
  },
  "failed_tests": [          // present in all real files; NOT read by scorers
    {
      "test_id":       "<str>",   // e.g. "tests.foo::test_bar"
      "error_type":    "<str>",   // one of the bucket names below
      "error_message": "<str>"    // truncated to 200 chars by run_pytest.py:229
    },
    ...
  ],
  "error_tests": [           // present in real files; NOT read by scorers
    { "test_id": ..., "error_type": ..., "error_message": ... },
    ...
  ],
  "raw_output":    "<str>",  // pytest stdout+stderr; NOT read by scorers
  "returncode":    <int>,    // NOT read by scorers; internal bookkeeping
  "parse_method":  "<str>"   // "junit_xml" | "regex_fallback"; NOT read by scorers
}
```

**Keys actually read by scorers:**
- `summary.total_tests` (line 104), `summary.passed` (103), `summary.failed` (104), `summary.errors` (105), `summary.skipped` (106)
- `error_breakdown` dict (line 100), specifically `error_breakdown["ModuleNotFoundError"]` (126) and `error_breakdown["ImportError"]` (127)

All other top-level keys (`failed_tests`, `error_tests`, `raw_output`, `returncode`, `parse_method`) are IGNORED by the scorer.  They must be present in the schema but their values do not affect scoring.

**Minimum viable emit (keys the scorer will crash without):**
```json
{
  "summary":        { "total_tests": N, "passed": N, "failed": N, "errors": N, "skipped": N },
  "error_breakdown": {}
}
```

---

## 2. `error_breakdown` Derivation Rule

### How the framework derives bucket names

Source: `/tmp/runanything/src/libkit/tools/run_pytest.py` lines 110–150 (`categorize_error`).

The function applies a **first-match regex scan** of the combined `<failure message> + "\n" + <failure text>` (from JUnit XML) or `<FAILED/ERROR line> + "\n" + <detailed failure block>` (from regex fallback):

| Regex pattern           | Bucket name           |
|-------------------------|-----------------------|
| `ModuleNotFoundError:`  | `ModuleNotFoundError` |
| `ImportError:`          | `ImportError`         |
| `AttributeError:`       | `AttributeError`      |
| `AssertionError`        | `AssertionError`      |
| `TypeError:`            | `TypeError`           |
| `ValueError:`           | `ValueError`          |
| `KeyError:`             | `KeyError`            |
| `IndexError:`           | `IndexError`          |
| `NameError:`            | `NameError`           |
| `FileNotFoundError:`    | `FileNotFoundError`   |
| `RuntimeError:`         | `RuntimeError`        |
| `OSError:`              | `OSError`             |
| `IOError:`              | `IOError`             |
| `ZeroDivisionError:`    | `ZeroDivisionError`   |
| `SyntaxError:`          | `SyntaxError`         |
| `IndentationError:`     | `IndentationError`    |
| `MemoryError:`          | `MemoryError`         |
| `RecursionError:`       | `RecursionError`      |
| `TimeoutError:`         | `TimeoutError`        |
| `ConnectionError:`      | `ConnectionError`     |
| `PermissionError:`      | `PermissionError`     |
| *(none matched)*        | `OtherError`          |

**Full bucket vocabulary observed in real 2026-06-07 baseline runs:**
`AssertionError`, `ConnectionError`, `ModuleNotFoundError`, `NameError`, `OtherError`, `TimeoutError`

**Special cases injected directly by `run_pytest.py` (not from test failure text):**
- `TimeoutError` (line 517): emitted when the subprocess.TimeoutExpired path is hit; `total_tests=0`.
- `ExecutionError` (line 540): emitted on unexpected exception in `run_pytest()`; `total_tests=0`.

**Why `OtherError` appears:** Any failure/error whose combined text does not contain any of the 21 listed exception class names followed by `:` (with `AssertionError` being the only exception — it lacks the `:`) falls through to the catch-all on line 150: `return "OtherError"`.  The resend-python sample (`run_pytest_results.json` lines 11–13) shows 172 `OtherError` entries because pytest's "async def functions are not natively supported" message matches no pattern.

---

## 3. Effective/Pass-Rate Computation (How "Resolved" Is Determined)

Source: `scorers.py` lines 108–143 (`pytest_pass_rate_scorer`).

### Primary pass rate (`pytest_pass_rate`)

```
effective_total = total_tests - skipped           # line 109
if effective_total > 0:
    pass_rate = passed / effective_total          # line 111
elif effective_total == 0 and total_tests == 0
     and len(error_breakdown) == 1
     and "TimeoutError" in error_breakdown:
    pass_rate = 1.0                               # line 118-119  (timeout = full run = treated as pass)
else:
    pass_rate = 0.0                               # line 121
```

Rounded to 4 decimal places (line 146).

### Secondary pass rate (`pass_rate_exclude_code_issues`)

Only `ModuleNotFoundError` and `ImportError` counts are treated as "dependency/environment issues" (lines 125-128):

```
code_issue_count = error_breakdown.get("ModuleNotFoundError", 0)
                 + error_breakdown.get("ImportError", 0)           # lines 126-128
effective_tests_excluding = passed + code_issue_count             # line 130
if effective_tests_excluding > 0:
    pass_rate_excl = passed / effective_tests_excluding           # line 132
```

### What counts as "resolved" for A/B comparison

A repair run is scored identically to a first-run if the written JSON has:
- `summary.passed` increased (or `summary.failed`/`summary.errors` decreased) compared to pre-repair
- The scorer reads only the final on-disk JSON; there is no "before vs after" in the scorer itself

A repo is considered **passing** when `success_scorer` returns `{"success": True}`, which requires `output["status"] == "success"` (scorers.py line 44) — that is set by `dockeragent_model.py` line 104, not by the JSON content.  The test-quality metrics (`pytest_pass_rate`, `pass_rate_exclude_code_issues`) are separate scorer outputs layered on top.

---

## 4. Do the Scorers Read JUnit XML Directly?

**No.** The scorers read **only `run_pytest_results.json`** (and `run_pytest_collect_results.json` for the collect scorer).

JUnit XML (`/testbed/logs/junit_report.xml`) is parsed **inside the container** by `run_pytest.py` (lines 482-499) and its parsed output is written as JSON.  The XML file is never copied out of the container (only the two JSON files are copied: `dockeragent_model.py` lines 99-103).  The scorers therefore never touch XML.

---

## 5. Worked Example

**Given:** pytest produces the following stdout (2 tests; 1 passed, 1 ModuleNotFoundError):

```
============================= test session info ==============================
...
PASSED tests/test_basic.py::test_one
FAILED tests/test_import.py::test_two - ModuleNotFoundError: No module named 'foo'
======================== 1 passed, 1 failed in 0.5s =========================
```

And the corresponding JUnit XML contains one `<failure>` element with message `"ModuleNotFoundError: No module named 'foo'"`.

**`run_pytest.py` `parse_junit_xml` produces (then writes to JSON):**

- `categorize_error("ModuleNotFoundError: No module named 'foo'")` → matches `r"ModuleNotFoundError:"` at line 122 → bucket `ModuleNotFoundError`
- `summary.total_tests = 2`, `passed = 1`, `failed = 1`, `errors = 0`, `skipped = 0`
- `error_breakdown = {"ModuleNotFoundError": 1}`

**Exact JSON to emit:**

```json
{
  "summary": {
    "total_tests": 2,
    "passed": 1,
    "failed": 1,
    "skipped": 0,
    "errors": 0,
    "xfailed": 0,
    "xpassed": 0
  },
  "error_breakdown": {
    "ModuleNotFoundError": 1
  },
  "failed_tests": [
    {
      "test_id": "tests.test_import::test_two",
      "error_type": "ModuleNotFoundError",
      "error_message": "ModuleNotFoundError: No module named 'foo'"
    }
  ],
  "error_tests": [],
  "raw_output": "... (full stdout+stderr) ...",
  "returncode": 1,
  "parse_method": "junit_xml"
}
```

**Scorer evaluation of this JSON:**
- `effective_total = 2 - 0 = 2`
- `pass_rate = 1/2 = 0.5`
- `code_issue_count = 1 (ModuleNotFoundError) + 0 (ImportError) = 1`
- `effective_tests_excluding = 1 + 1 = 2`
- `pass_rate_exclude_code_issues = 1/2 = 0.5`

After a successful repair that installs `foo` and all 2 tests pass, the JSON should emit:
```json
{ "summary": { "total_tests": 2, "passed": 2, "failed": 0, "errors": 0, "skipped": 0, ... },
  "error_breakdown": {} }
```
→ `pass_rate = 1.0`, `pass_rate_exclude_code_issues = 0.0` (numerator 2, but denominator `passed + 0 = 2` → 1.0), `pytest_executed = True`.

---

## 6-Line Summary

1. The scorer reads **only** `run_pytest_results.json` (never JUnit XML) via path `{root_path}/output/{full_name}/run_pytest_results.json` (scorers.py:88); `run_pytest_collect_results.json` is separate and used only by `pytest_collect_scorer`.
2. Required keys consumed by scorers: `summary.{total_tests,passed,failed,errors,skipped}` and `error_breakdown`; all other keys (`failed_tests`, `error_tests`, `raw_output`, `returncode`, `parse_method`) are present in real files but ignored by scorers.
3. `error_breakdown` buckets are derived by `categorize_error()` (run_pytest.py:110-150) via first-match regex on the combined failure/error text; the 21-bucket vocabulary ends with catch-all `OtherError`; observed in-the-wild buckets on 2026-06-07 baseline: `AssertionError`, `ConnectionError`, `ModuleNotFoundError`, `NameError`, `OtherError`, `TimeoutError`.
4. Primary pass rate = `passed / (total_tests - skipped)` (scorers.py:108-121); secondary `pass_rate_exclude_code_issues` treats only `ModuleNotFoundError` and `ImportError` counts as excluded denominator (lines 126-143); a fully-resolved repo emits `error_breakdown: {}` and `pass_rate = 1.0`.
5. `success_scorer` does NOT read the JSON at all — it reads `output["status"] == "success"` from the predict() return dict (scorers.py:44); the pytest JSON drives quality metrics only, not the top-level success flag.
6. `junit_to_pytest_results` in `repo2run_repair_port.py` must emit the full schema (all 8 top-level keys) to match real framework output, but only `summary` + `error_breakdown` are load-bearing for scorer correctness; the minimum viable emit is those two keys.
