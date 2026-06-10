# jasonxtn/Argus

- **DA pass-rate:** 0/1 (0.0%) | **RAT pass-rate:** 1/1 (100%) | **Bucket:** DA_LOSS
- **DA build_success/test_success:** True / False | **Error breakdown:** OtherError (fixture setup failure)

## Failure stage & category

**Stage:** test_execution  
**Category:** test_collection_error (missing pytest fixtures required by the test)

## Root cause (why DA lost)

DA synthesized a Docker recipe that collected tests successfully (`pytest --collect-only` passed, found 1 test), but verified only the collection step, not actual test execution. When the eval harness ran the real tests, pytest failed at the fixture setup stage because the tests require custom fixtures (`ns_host` and `domain`) defined in a `conftest.py` file that DA never created. RAT diagnosed this fixture deficiency by actually running tests in its agent loop, detected the error, and then created the missing `conftest.py` with the required fixture definitions before re-running tests successfully.

## What RAT did differently

- **Line 19 in RAT's commands:** RAT examined the failing test file `/repo/argus/modules/recursive_nameserver_leak_test.py` and identified the fixture requirement
- **Line 20:** RAT checked for existing conftest files with `find /repo -name "conftest*" -type f` (found none)
- **Lines 25–30:** RAT created `/repo/argus/modules/conftest.py` with the missing fixture implementations:
  ```python
  @pytest.fixture
  def ns_host() -> str:
      return "test_ns_host"
  
  @pytest.fixture
  def domain() -> str:
      return "example.com"
  ```
- **Lines 33–34:** RAT re-ran test collection and full test execution, which then passed

DA never looked at the actual test file contents or ran a real test execution in its agent loop. It only ran `pytest --collect-only`, which does not execute tests and thus does not reveal fixture setup failures.

## Evidence

- **DA verified commands:** `["pytest --collect-only -q --disable-warnings"]` (collection only, no execution)
- **DA test error from eval:** `fixture 'ns_host' not found` (from run_pytest_results.json)
- **RAT command sequence:** Commands 19–30 show diagnosis, fixture file creation, and re-verification
- **DA agent log markers:** No attempt to read test files or diagnose fixture issues; stopped after successful collection
- **RAT command success:** All fixture-creation commands returned rc=0, followed by successful test execution (`run-pytest` passed)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In agent.py:** After collecting tests with `pytest --collect-only`, immediately run `pytest <test_file>::<test_name> --collect-only -v` on EACH collected test to detect setup-phase fixture errors BEFORE calling the artifact final answer.

2. **In src/recipe_repair.py:** Add a new repair heuristic that intercepts pytest fixture errors (pattern: `fixture '<name>' not found`) and searches the repo for existing `conftest.py` files or test file signatures that suggest custom fixture requirements. If missing, synthesize a basic conftest with stub fixtures matching the test signature.

3. **In src/synthesizer.py:** When test_commands include only `pytest --collect-only`, flag this as an incomplete verification and inject a real test-run command (e.g., `pytest --tb=short -x`) into the verified_test_commands list so the self-verify stage actually executes tests and detects fixture/import/setup failures before finalizing the recipe.

Alternatively: Modify self-verify logic to detect when test_commands are collection-only and automatically promote them to execution commands for the self-verify round.
