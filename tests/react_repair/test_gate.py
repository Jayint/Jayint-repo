import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.gate import test_verdict


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
