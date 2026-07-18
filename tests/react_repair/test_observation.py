import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.observation import safety_compress_observation


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


# --- (1) the noise strip is ALWAYS ON, not size-gated ----------------------
def test_noise_stripped_even_below_threshold():
    # The old code size-gated the noise strip, so a SMALL observation reached the model with every
    # noise line intact — proven in a live run. Dropping junk never costs signal → unconditional.
    text = ("Get:1 http://deb.debian.org bookworm InRelease\n"
            "Hit:2 http://security.debian.org bookworm-security InRelease\n"
            "REAL_ERROR: could not build wheel\n")
    out, applied = safety_compress_observation(text, threshold_chars=100_000, target_chars=100_000)
    assert applied is True                                   # far below threshold, yet noise dropped
    assert "Get:1" not in out and "Hit:2" not in out
    assert "REAL_ERROR: could not build wheel" in out         # signal survives

def test_clean_small_output_still_untouched():
    # No noise → byte-for-byte identical, applied=False (don't churn a clean observation).
    text = "line one\nline two\n"
    assert safety_compress_observation(text, threshold_chars=100_000, target_chars=100_000) == (text, False)


# --- (4) harness sentinels never reach the model ---------------------------
def test_install_fail_sentinel_never_reaches_the_model():
    # sandbox's ERR-trap marker: the HOST parses it (_parse_install_failure) but never strips it, so
    # it leaked verbatim into a live prompt. It is plumbing, not output.
    text = ("ERROR: No matching distribution found for nonexistent-pkg-xyz\n"
            "__INSTALL_FAIL__:python -m pip install nonexistent-pkg-xyz:9\n")
    out, _ = safety_compress_observation(text, threshold_chars=100_000, target_chars=100_000)
    assert "__INSTALL_FAIL__" not in out
    assert "ERROR: No matching distribution found" in out     # the real error survives


# --- (5) no compressor meta-chatter; cuts land on line boundaries ----------
def test_no_synthesized_compressor_metadata():
    text = "\n".join(f"filler line {i}" for i in range(2000))
    out, _ = safety_compress_observation(text, threshold_chars=200, target_chars=1500)
    for meta in ("[Safety Compression Applied]", "Original observation length",
                 "repetitive output omitted", "safety compression", "prompt budget"):
        assert meta not in out, f"compressor meta-chatter leaked: {meta!r}"
    assert "lines omitted" in out                             # but an HONEST elision count remains

def test_truncation_cuts_on_line_boundaries_not_mid_token():
    # The old char-splice produced garbage like "…can result in brok" + "-26.2 pluggy-1.6.0".
    lines = [f"complete-line-{i:04d}-endmarker" for i in range(600)]
    out, _ = safety_compress_observation("\n".join(lines), threshold_chars=200, target_chars=1200)
    for ln in out.splitlines():
        if ln.startswith("complete-line-"):
            assert ln.endswith("-endmarker"), f"mid-token cut: {ln!r}"


# ── pip transport chatter (opt-in strip, bundled with REACT_MSG_STYLE=agentic) ────────────────
from src.agent.observation import strip_pip_progress


def test_strip_pip_progress_drops_transport_noise():
    text = ("Collecting pip\n"
            "  Downloading pip-26.1.2-py3-none-any.whl.metadata (4.6 kB)\n"
            "   ━━━━━━━━━━━━━━━━━━ 1.8/1.8 MB 5.6 MB/s eta 0:00:00\n"
            "Installing collected packages: pip\n"
            "  Attempting uninstall: pip\n"
            "    Uninstalling pip-24.0:\n"
            "      Successfully uninstalled pip-24.0\n"
            "Successfully installed pip-26.1.2\n"
            "WARNING: Running pip as the 'root' user can result in broken permissions...\n"
            "ERROR: No matching distribution found for nonexistent-pkg\n")
    out = strip_pip_progress(text)
    assert out == ("Successfully installed pip-26.1.2\n"
                   "ERROR: No matching distribution found for nonexistent-pkg")

def test_strip_pip_progress_keeps_the_lines_that_answer_questions():
    # what version landed, and what is already present — both real answers to real questions
    text = "Requirement already satisfied: six in /usr/lib (1.16.0)\nSuccessfully installed redis-5.0.1\n"
    assert strip_pip_progress(text) == text

def test_strip_pip_progress_is_byte_identical_when_nothing_matches():
    text = "fatal error: libpq-fe.h: No such file or directory\n#include <libpq-fe.h>\n"
    assert strip_pip_progress(text) is text          # same object — no rebuild, no churn

def test_strip_pip_progress_handles_empty():
    assert strip_pip_progress("") == "" and strip_pip_progress(None) is None
