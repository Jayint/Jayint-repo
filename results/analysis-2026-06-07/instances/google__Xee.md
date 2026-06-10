# google/Xee

- DA pass-rate: 44.07% (26/59) | RAT pass-rate: 44.07% (26/59) | bucket: BOTH_PASS (parity)
- DA build_success: N/A | test_success: False | RAT test_success: False
- error_breakdown: identical — 33 OtherError (EEException), 26 passed (non-integration tests)

## Failure stage & category

test_execution / dataset_hard_rat_also_failed

## Root cause (why this is parity, not DA loss)

Both agents achieved identical test results (44.07% pass rate, 26/59 passed). The 33 failures are all OtherError with the same root cause: `ee.ee_exception.EEException: Please authorize access to your Earth Engine account`. Tests requiring live Earth Engine API authentication fail in both environments because the tests expect `ee.Authenticate()` or the `earthengine authenticate` CLI command to succeed interactively, which is impossible in a sandbox environment without credentials.

The 26 passing tests are unit tests (e.g., `EEStoreTest`, `ParseEEInitKwargsTest`, `GridHelpersTest`) that do not require authentication. The 33 failing tests are integration tests (e.g., `EEBackendArrayTest`, `EEBackendEntrypointTest`, `ReadmeCodeTest`) that call Earth Engine APIs.

## What RAT did differently

- RAT ran the same test command structure: `pip install -e "/repo[tests]"` + `run-pytest`
- Both agents verified identical test collection: `pytest --collect-only -q --disable-warnings`
- No observable difference in setup, environment preparation, or test execution strategy

## Evidence

- **DA run.log tail (-200 lines, first 100)**: All 33 failures are `ee.ee_exception.EEException: Please authorize access...`
- **RAT run.log tail (-200 lines, first 100)**: Identical error messages in identical tests
- **_result_row.json** (both):
  - pytest_pass_rate: 0.4407
  - pytest_passed: 26
  - pytest_failed: 33
  - error_breakdown: {'OtherError': 33}
- **Passing tests**: xee/ext_test.py unit tests (26 tests, all PASSED)
- **Failing tests**: xee/ext_integration_test.py integration tests (33 tests, all require Earth Engine auth)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

No fix needed for DA. This is a **dataset-hard issue**, not a DA-specific deficiency. Both agents achieved parity (BOTH_PASS), which indicates:

1. **Build and installation were correct** in both cases (no ModuleNotFoundError, no pip install failures)
2. **Test collection was correct** in both cases (59 tests collected, executed)
3. **The test failures are environment/data-driven**, not agent-driven (EEException requires live Google Cloud credentials that no sandbox has)

For benchmarks involving external authentication (Cloud APIs, private services, credentials), consider:
- Marking tests with `@pytest.mark.skip` or `@pytest.mark.xfail` if credentials are unavailable
- Providing mock credentials or a mock backend for integration tests
- Documenting that some test suites cannot pass in sandboxed/credential-less environments

This is expected behavior; no code changes to DA needed.
