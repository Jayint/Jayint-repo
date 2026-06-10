# DockerAgent Run Sanity Audit — 2026-06-06

> **ORCHESTRATOR VERIFICATION (2026-06-07): repair loop confirmed ACTIVE+WORKING; baseline 0.3729 is SOUND.**
> All 5 Haiku-flagged anomalies were re-checked first-hand against `_result_row.json` + run.log; none
> affect the DockerAgent ESSR or the RAT head-to-head:
> - `pal-mcp-server`: **FALSE anomaly** — recorded 0.9786 = 870/(905−16 skipped) is the correct
>   skip-adjusted rate (scorers.py:108-111). The Haiku check wrongly divided by 905.
> - `karaoke-gen`, `mcpo`, `mcp-atlassian`: recorded **0.0 is CORRECT** — genuine DockerAgent env-setup
>   failures (run.log shows 0 real tests passed: "1 skipped/1 error", "2 errors", "2 errors"). pass_rate
>   unaffected by the cosmetic quirks (status=success flag; error-count folded into total_tests).
> - `sirfz/tesserocr`: 0.0 is the **5th-class native-crash→0 undercount**, but RAT also scored 0.0 on it
>   → symmetric, no head-to-head bias.
> Net: 1 false positive + 3 genuine 0.0 failures + 1 symmetric known-bug. The DockerAgent 0.3729 vs RAT
> 0.6775 comparison stands. (Counts below have minor Haiku inconsistencies: 41 repos fired the loop;
> 5 items were flagged, of which only cosmetic/known issues remain after verification.)

## Summary

**Total Repos**: 50  
**Repair Loop Fired**: 41 repos (82%)  
**Repair Loop Resolved/Adopted**: 28 repos (68% of fired, 56% of total)  
**Normal Records**: 46 / 50 (92%)  
**Anomalies Found**: 4 / 50 (8%)

---

## Repair Loop Verdict

**ACTIVE AND WORKING** — The repair loop mechanism was engaged across the run:

- **41 of 50 repos** (82%) triggered the repair loop
- **28 of those 41** (68%) reached resolution/adoption status
- Remaining 13 that fired (32%) persisted unresolved but were logged correctly
- Repair loop demonstrated functional smoke test across representative repos:
  - EnableSecurity/wafw00f: 2 rounds → resolved + adopted
  - Tecnativa/docker-socket-proxy: 2 rounds → resolved + adopted
  - docling-project/docling: 2 rounds → resolved + adopted
  - swar/nba_api: 2 rounds → resolved + adopted

The loop is **not blocked**, **logs correctly**, and **adopts working recipes when repairs succeed**.

---

## Anomalies Found (4)

### 1. BeehiveInnovations/pal-mcp-server
**Status**: success-with-anomaly  
**Issue**: Recorded pytest pass_rate (0.9786) contradicts calculated rate from logs (870/905 = 0.9613)  
**Root Cause**: Scoring inconsistency — likely from mismatched test count aggregation  
**Self-Verify**: Round 0 resolved without repair (tests passed on first attempt)  
**Severity**: MEDIUM — Data inconsistency, not functional failure

### 2. nomadkaraoke/karaoke-gen
**Status**: success  
**Issue**: Log shows `--collect-only` (collection, not execution) but recorded `pytest_executed=True`  
**Root Cause**: Repair loop Round 0 completed with `tests_passed` status, but actual pytest invocation was collection-only  
**Tests Collected**: Log claims 4999/5000; actual pytest output shows 0 collected  
**Recorded**: pytest_executed=True, total=0, passed=0 (0.0%), status=success  
**Severity**: HIGH — Misleading execution flag; agent assertion contradicts pytest output

### 3. open-webui/mcpo
**Status**: success  
**Issue**: Recorded `pytest_total_tests=2` conflates error count (2 import errors) with test count (0 collected)  
**Root Cause**: Fallback scoring counted collection errors as tests  
**Repair Loop**: 3 rounds → unresolved (missing typer, dotenv, fastapi, pydantic)  
**Recorded**: total=2, passed=0 (0.0%), status=success  
**Severity**: MEDIUM — Semantic conflation (errors ≠ tests); status=success is borderline for unresolved loop

### 4. sirfz/tesserocr
**Status**: success  
**Issue**: JUnit XML parsing failed; recorded `pytest_executed=true` but `pytest_total_tests=0`  
**Root Cause**: Test execution occurred (4 PASSED lines in log, 24 items collected) but XML report missing → fallback parser reported 0 tests  
**Repair Loop**: 2 rounds → resolved/adopted  
**Recorded**: pytest_executed=True, total=0, passed=0 (0.0%), status=success  
**Severity**: MEDIUM — Execution occurred but scorer lost XML; contradiction masks partial success

---

## Log Consistency Summary

- **YES (consistent)**: 46 records
- **NO (contradictory)**: 4 records (BeehiveInnovations/pal-mcp-server)
- **PARTIAL (execution vs. reporting mismatch)**: 3 records (nomadkaraoke/karaoke-gen, sirfz/tesserocr, sooperset/mcp-atlassian)

---

## Overall Verdict

**ANOMALIES_FOUND** (4 out of 50)

### Breakdown
- **Pure normal**: 46 repos
- **Anomalies**:
  1. **BeehiveInnovations/pal-mcp-server** — Scoring inconsistency (0.9786 vs. 0.9613)
  2. **nomadkaraoke/karaoke-gen** — pytest_executed=True but log shows --collect-only; agent claim vs. reality mismatch
  3. **open-webui/mcpo** — Error count conflated as test count; status=success despite unresolved repair
  4. **sirfz/tesserocr** — JUnit XML missing; execution occurred but scorer reported 0 tests

### Root Causes
- **nomadkaraoke/karaoke-gen**: Agent miscommunication (claims 4999/5000 tests but actual pytest shows 0)
- **open-webui/mcpo**: Fallback scorer treating errors as test count
- **sirfz/tesserocr**: XML report parsing failure → blind fallback
- **BeehiveInnovations/pal-mcp-server**: Numeric aggregation drift

### Risk Level
- **Critical**: nomadkaraoke/karaoke-gen (misleading execution status)
- **High**: open-webui/mcpo (false success on unresolved repair)
- **Medium**: sirfz/tesserocr, BeehiveInnovations/pal-mcp-server (reporting issues, not functional failure)

---

## Conclusion

The **repair loop is active and working correctly** across the 50-repo run:
- 82% of repos triggered it
- 68% of triggered repos resolved successfully
- Unresolved cases logged correctly (13 repos with persistent errors)

However, **4 anomalies in scoring/reporting** indicate downstream issues in test result aggregation and XML parsing, independent of the repair loop mechanism itself. These are measurement artifacts, not repair failures.

**Recommendation**: Investigate scorer fallback logic (error-count-as-test-count) and XML parsing robustness; repair loop itself is solid.
