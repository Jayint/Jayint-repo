import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.observation import safety_compress_observation


def test_below_threshold_returned_verbatim():
    # Small output (a typical file cat / version check) must reach the agent UNCHANGED — the whole
    # point of replacing the 200-char cap.
    text = "line one\nline two\nline three"
    out, applied = safety_compress_observation(text, threshold_chars=1000, target_chars=1000)
    assert out == text and applied is False

def test_empty_and_none():
    assert safety_compress_observation("", threshold_chars=100, target_chars=100) == ("", False)
    assert safety_compress_observation(None, threshold_chars=100, target_chars=100) == ("", False)

def test_large_output_capped_to_target():
    text = "\n".join(f"filler content line number {i} here" for i in range(2000))
    out, applied = safety_compress_observation(text, threshold_chars=200, target_chars=1500)
    assert applied is True and len(out) <= 1500

def test_drops_download_noise_keeps_signal():
    # A pip/apt install dump: hundreds of "Downloading from" noise lines around the real signal.
    noise = "\n".join(f"Downloading from mirror package-{i}" for i in range(300))
    text = noise + "\nSuccessfully installed psycopg2-2.9.9\nUNIQUE_SIGNAL_LINE done\n"
    out, applied = safety_compress_observation(text, threshold_chars=200, target_chars=2000)
    assert applied is True
    assert "UNIQUE_SIGNAL_LINE" in out
    assert "Successfully installed psycopg2-2.9.9" in out           # status line preserved
    assert out.count("Downloading from mirror") < 30                # most noise dropped

def test_keeps_head_and_tail():
    lines = ["FIRST_MEANINGFUL_HEAD"] + [f"middle filler line {i}" for i in range(500)] + ["LAST_MEANINGFUL_TAIL"]
    text = "\n".join(lines)
    out, applied = safety_compress_observation(text, threshold_chars=200, target_chars=3000)
    assert applied is True
    assert "FIRST_MEANINGFUL_HEAD" in out and "LAST_MEANINGFUL_TAIL" in out
