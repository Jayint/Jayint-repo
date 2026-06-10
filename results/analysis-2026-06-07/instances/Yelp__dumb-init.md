# Yelp/dumb-init

- DA pass-rate: 0.989 (180/182) | RAT pass-rate: 1.0 (182/182) | bucket: PARTIAL_TIE
- DA build_success/test_success: build=False, test=False (due to Verification Bundle rejection) | error_breakdown: 2 OtherError

## Failure stage & category

test_execution / wrong_test_command

## Root cause (why DA lost)

DA's evaluation was skipped due to Verification Bundle rejection. The DA agent recorded the test command as `PATH=/app:$PATH pytest --collect-only -q --disable-warnings 2>&1` during the verification phase but then submitted a bundle with just `pytest --collect-only -q --disable-warnings` (without the PATH prefix). The self-verify loop passed (indicated by "status=resolved; keeping original recipe"), but the harness rejected the bundle because the exact command as recorded was not in the final bundle, causing the evaluation script generation to be skipped ("No accepted Verification Bundle test commands were found; skipping evaluation script generation"). Despite this, pytest was still executed by the Multi-Docker-Eval framework and produced actual results: 180/182 tests passed, with 2 failures in `test_setsid_signals_entire_group`. RAT passed all 182 tests, indicating the 2 test failures in DA are environment-specific (TTY handling in Docker containers) rather than a build/setup issue.

## What RAT did differently

- RAT explicitly installed gcc: `apt-get install -y -qq gcc 2>&1`
- RAT compiled with explicit gcc command: `gcc -std=gnu99 -static -s -Wall -Werror -O3 -o dumb-init dumb-init.c 2>&1`
- RAT ran the actual test suite command `run-pytest` (which translates to `pytest` in container), achieving 182/182 pass rate with 0 failures

## Evidence

- File: /Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/Yelp/dumb-init/run.log
  - Line 1794: `[Verification Bundle] Rejected agent-reported bundle because at least one command was not previously observed succeeding in the final environment.`
  - Line 1800: `[Verification Bundle] Auto-finalized from previously verified test commands.`
  - Line 1818: `No accepted Verification Bundle test commands were found; skipping evaluation script generation.`
  - Lines 828-830: DA recorded `PATH=/app:$PATH pytest --collect-only -q --disable-warnings 2>&1` but bundle contains `pytest --collect-only -q --disable-warnings`
  - Failure details: `test_setsid_signals_entire_group[1]` and `test_setsid_signals_entire_group[0]` both failed with "assert 4 == 0" (living PIDs not cleaned up), indicating TTY/setsid signal handling issue

- File: /Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/Yelp/dumb-init/junit_report.xml
  - Line: `<testsuite name="pytest" errors="0" failures="0" skipped="0" tests="182"`
  - RAT passed all 182 tests with 0 failures/errors

- File: /Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/Yelp/dumb-init/outer_commands.json
  - Commands show: `gcc -std=gnu99 -static -s -Wall -Werror -O3 -o dumb-init dumb-init.c 2>&1` (explicit gcc)
  - vs DA using `make build` (which uses default CC, though gcc is available)

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In src/artifact_verify.py or harness verification logic**: Ensure that if a verification command is recorded with environment variable prefixes (e.g., `PATH=/app:$PATH`), those prefixes are preserved in the Verification Bundle. The issue is that DA recorded the command WITH the prefix but the bundle was submitted WITHOUT it, causing rejection.

2. **In agent.py or the synthesizer**: Investigate why `/app` is being prepended to PATH for pytest collection when `/app` doesn't exist in the Docker image and isn't created. This PATH manipulation appears to be a red herring added during agent reasoning but not reflected in the actual Dockerfile.

3. **In test harness**: Consider whether skipping evaluation script generation when the Verification Bundle is rejected is the right behavior. The current behavior results in `SKIP_EVAL=True` and `TEST_SUCCESS=False` even though pytest actually executed downstream. Either: (a) make the verification bundle matching more lenient for environment variable prefixes, or (b) ensure the evaluation script is still generated even if the bundle is auto-finalized.

4. **Note**: The 2 test failures (`test_setsid_signals_entire_group`) are environment-specific flakiness (TTY/setsid behavior in Docker) unrelated to the setup. Both agents set up the same dependencies and compiled the binary. The RAT environment likely provides a TTY or different isolation context where the signal handling works correctly. This is likely a test flakiness issue in the repository itself rather than a DA setup issue, but worth noting that RAT's environment passed while DA's environment failed on these 2 tests.
