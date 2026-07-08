import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.history import History


def test_old_large_observation_compressed_after_delay():
    calls = []
    def fake_compressor(target, context):
        calls.append(target.step_id)
        return f"[summary of step {target.step_id}]"
    h = History(safety_max_chars=100000, compress_delay=2,
                compress_threshold_chars=50, compressor=fake_compressor)
    h.record(1, "t", "explore", "B" * 200)     # large — a compression candidate once old enough
    h.record(2, "t", "explore", "small")
    assert calls == []                          # step 1 is only 1 behind — not yet past delay
    h.record(3, "t", "explore", "small")        # now step 1 is 2 behind → compress it
    assert calls == [1]
    assert h.steps[0].observation_prompt == "[summary of step 1]"

def test_small_old_observation_not_compressed():
    calls = []
    h = History(compress_delay=1, compress_threshold_chars=10_000,
                compressor=lambda t, c: calls.append(t.step_id) or "x")
    h.record(1, "t", "explore", "tiny")
    h.record(2, "t", "explore", "tiny")
    assert calls == []                          # below threshold → never compressed
