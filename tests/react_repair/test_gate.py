import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.gate import test_verdict


def test_all_pass_is_ok():
    r = test_verdict("5 passed in 0.3s")
    assert r.ok and r.passed == 5 and r.executed == 5

def test_ninety_percent_passes_threshold():
    assert test_verdict("9 passed, 1 failed in 1s").ok          # 0.9 >= 0.8

def test_sixty_percent_fails_threshold():
    assert not test_verdict("3 passed, 2 failed in 0.5s").ok     # 0.6 < 0.8

def test_collection_error_counts_against():
    r = test_verdict("8 passed, 2 errors in 1s")                 # 8/10 = 0.8 -> ok
    assert r.ok and r.executed == 10

def test_hollow_zero_collected_is_not_ok():
    assert not test_verdict("no tests ran in 0.01s").ok

def test_all_skipped_is_not_ok():
    assert not test_verdict("3 skipped in 0.1s").ok              # executed 0

def test_ansi_stripped():
    assert test_verdict("\x1b[32m5 passed\x1b[0m in 0.1s").ok


# ── `collected` under `pytest -q` — the field that was dead on arrival ────────────────────────
# VERIFY_TEST_CMD is `python -m pytest -q`, and `-q` NEVER prints "collected N items". So the
# _COLLECTED regex never matched, TestOutcome.collected was permanently 0, and the `(C collected)`
# header suffix it feeds could not fire even once. Verified against a real pytest -q run.
def test_collected_is_derived_when_pytest_q_prints_no_collected_line():
    v = test_verdict("1 failed, 199 passed, 50 skipped in 3s")
    assert v.collected == 250            # 199 + 1 + 50 — the population, derived from the summary
    assert v.executed == 200             # the GATE denominator still excludes skips

def test_collection_errors_are_not_counted_as_collected():
    # A module that failed to IMPORT was never collected — that is the whole point of the distinction.
    v = test_verdict("5 errors in 0.06s")
    assert v.collected == 0 and v.executed == 5 and v.errors == 5

def test_explicit_collected_line_still_wins_when_present():
    v = test_verdict("collected 200 items\n40 passed, 160 failed in 5s")
    assert v.collected == 200

def test_component_counts_are_kept_apart_from_the_gate_denominator():
    v = test_verdict("41 passed, 9 failed, 2 skipped in 1s")
    assert (v.passed, v.failed, v.errors, v.skipped) == (41, 9, 0, 2)
    assert v.executed == 50              # passed + failed + errors; skips excluded

def test_xfail_counts_toward_the_collected_population():
    v = test_verdict("10 passed, 2 xfailed, 1 xpassed in 1s")
    assert v.collected == 13
