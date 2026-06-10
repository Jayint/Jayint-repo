# LibreTranslate/LibreTranslate

- DA pass-rate: 100% (15/15) | RAT pass-rate: 100% (15/15) | bucket: BOTH_PASS
- DA build_success/test_success: true/false | error_breakdown: none
- Mismatch note: DA's internal `test_success` flag is false (agent reported only collect command), but harness executed full pytest run independently, yielding 15/15 passes

## Failure stage & category
none_parity / parity_both_passed

## Root cause (why DA lost)
No loss occurred. Both DA and RAT achieved 100% test pass-rate (15/15 tests). The repo is not a DA failure case.

The `test_success: false` flag in DA's internal JSON is a measurement artifact: DA's agent reported `pytest --collect-only` as the test command (which is collection only), so the agent's own test_success flag is false. However, the evaluation harness ran full pytest execution independently and both agents passed all 15 tests.

## What RAT did differently
No meaningful difference. RAT followed the same sequence:
- `pip install -q -e ".[test]"` (install with test extras)
- `python3 /home/tools/run_pytest_collect.py` (test collection)
- `python3 /home/tools/run_pytest.py` (actual test execution)

DA produced equivalent results via:
- `pip install ".[test]"` (with retry wrapper in Dockerfile)
- `python scripts/compile_locales.py` (required setup step)
- Harness ran independent full pytest execution

Both agents correctly identified the need to install with `[test]` extras. DA additionally ran locale compilation (a real build step), RAT did not (not needed for these specific tests).

## Evidence
- DA result: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/LibreTranslate/LibreTranslate/LibreTranslate__LibreTranslate.json` shows `"build_success": true, "test_success": false` but `run_pytest_results.json` shows 15/15 passed
- DA pytest results: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/LibreTranslate/LibreTranslate/run_pytest_results.json` shows all 15 tests PASSED with coverage summary
- RAT trajectory: shows both collection and full pytest execution completed successfully
- RAT result: `/Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/LibreTranslate/LibreTranslate/_result_row.json` shows `"pytest_pass_rate": 1.0, "pytest_passed": 15`

## Fix recommendation
No fix needed for this instance. This is parity, not a failure. If anything, DA's agent should ideally report the full `pytest` command (not just `--collect-only`) to make the internal `test_success` flag align with the harness results, but this is a minor instrumentation issue, not a functional problem.
