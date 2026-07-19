import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.eval.native_blindspot.oracle import load_oracle, load_triggers  # noqa: E402


def test_oracle_loads_and_covers_the_affected_set():
    oracle = load_oracle()
    assert oracle["mvt"].dlopen == ("libusb-1.0-0",)
    assert oracle["mvt"].part == "A"
    assert oracle["coderamp-labs-gitingest"].cli == ("git",)
    # residuals/out-of-scope are labeled honestly, not silently dropped
    assert oracle["karlicoss-promnesia"].in_scope is False
    assert oracle["microsoft-markitdown"].in_scope is False
    in_scope = [e for e in oracle.values() if e.in_scope]
    assert len(in_scope) >= 15   # the recall-bearing positive set


def test_triggers_present_for_dlopen_culprits():
    t = load_triggers()
    assert "python-magic" in t and "pyusb" in t
