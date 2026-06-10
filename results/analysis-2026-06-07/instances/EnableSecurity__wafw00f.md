# EnableSecurity/wafw00f

- **DA pass-rate:** 48/48 (100%) | **RAT pass-rate:** 48/48 (100%) | **bucket:** BOTH_PASS (parity)
- **DA build_success/test_success:** True/False | **error_breakdown:** {} (no errors)

## Failure stage & category

**none_parity** / **parity_both_passed** — Both agents achieved 100% test pass-rate; no failure to root-cause.

## Root cause (why DA achieved parity)

DA's agent initially proposed `pip install -e ".[dev]"` but the verification pipeline rejected this compound command (as "command rejected before execution due to compound verification/setup"). DA's synthesizer then extracted only the direct dependencies (responses, requests) inferred from test imports. During self-verify round 0, tests failed with missing=['responses', 'requests']. Self-verify round 1 applied deterministic repair (installing those two packages) and tests executed successfully. Final pytest run: 48/48 passed.

## What RAT did differently

- RAT agent explicitly installed the repo as an editable package with dev extras: `pip install -q -e ".[dev]" -i https://mirrors.aliyun.com/pypi/simple --no-cache-dir`
- This single command brought in the repo's own dependencies (wafw00f package) plus dev extras (pytest, responses, requests, etc.) in one step
- RAT then ran full test suite: `python3 /home/tools/run_pytest.py`

DA extracted only direct test dependencies (responses, requests) in separate pip install commands, bypassing the `pip install -e ".[dev]"` install of the repo itself.

## Evidence

**DA result file:** `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/EnableSecurity/wafw00f/EnableSecurity__wafw00f.json`
- Line 63: `"python3 --version && pip install -e \".[dev]\"" (command rejected before execution due to compound verification/setup)`
- Line 49–50: Synthesizer extracted only: `pip install responses` and `pip install requests`
- Line 729: `[Self-Verify] Round 1: tests executed (tests_passed). Done.`
- Pass-rate: `pytest_pass_rate: 1.0, pytest_passed: 48`

**RAT result file:** `/Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/EnableSecurity/wafw00f/outer_commands.json`
- Command 10: `pip install -q -e ".[dev]" -i https://mirrors.aliyun.com/pypi/simple --no-cache-dir` (rc 0)
- Command 12: `run-pytest` (rc 0)
- Pass-rate: `pytest_pass_rate: 1.0, pytest_passed: 48`

## Fix recommendation

**Not needed for this repo.** Both agents achieved full success (48/48). However, the difference highlights a design tradeoff in DA:

1. **DA's compound-command rejection:** The synthesizer rejects compound commands like `python3 --version && pip install -e ".[dev]"` to preserve idempotency and easy troubleshooting. This caused DA to lose the explicit `pip install -e` directive.

2. **DA's workaround (self-verify repair):** DA's self-verify loop detected missing test deps and auto-repaired by installing them separately. The repair succeeded and tests passed.

3. **RAT's approach:** RAT's agent kept the compound command intact and ran it directly, achieving the same outcome in one go.

**For future improvements:** Consider whether DA's synthesizer should accept compound commands that start with environment setup (e.g., `python3 --version &&`) or version checks, since these are often safe and idempotent. Alternatively, teach the agent planning phase to prefer single-command installs (like `pip install -e ".[dev]"`) over multi-step dependency discovery to avoid the rejection path entirely.

In this case, the self-verify repair loop prevented any practical regression, so the architectural difference was transparent to the end user.
