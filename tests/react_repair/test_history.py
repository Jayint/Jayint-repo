import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.history import History, safety_truncate


def test_safety_truncate_keeps_tail():
    out, applied = safety_truncate("x" * 100 + "ERROR_AT_END", max_chars=20)
    assert applied and out.endswith("ERROR_AT_END") and len(out) < 120

def test_short_observation_untouched():
    out, applied = safety_truncate("short", max_chars=20)
    assert not applied and out == "short"

def test_safety_truncate_keeps_head_and_tail():
    # head carries the build header / failing command; tail carries the pytest summary / final error.
    out, applied = safety_truncate("HEAD_START" + "x" * 200 + "ERROR_AT_END", max_chars=40)
    assert applied
    assert out.startswith("HEAD_START")             # header survives (was lost under tail-only)
    assert out.rstrip().endswith("ERROR_AT_END")    # final error still survives
    assert "omitted" in out                         # middle elided

def test_record_truncates_into_prompt_history():
    h = History(safety_max_chars=20)
    step = h.record(1, "t", "explore: ls", "y" * 200 + "TAIL")
    assert step.observation_raw.endswith("TAIL")
    assert len(step.observation_prompt) < 60 and step.observation_prompt.endswith("TAIL")

def test_render_includes_prior_steps():
    h = History(safety_max_chars=4000)
    h.record(1, "thought-a", "patch", "(patched)")
    h.record(2, "thought-b", "explore: ldconfig", "libpq found")
    rendered = h.render()
    assert "patch" in rendered and "explore: ldconfig" in rendered and "libpq found" in rendered
