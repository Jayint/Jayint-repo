import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.sandbox import InstallResult, _wrap_with_err_trap, _parse_install_failure


def test_wrap_prepends_err_trap_and_keeps_script():
    wrapped = _wrap_with_err_trap("apt-get install -y libgl1\n")
    assert "trap " in wrapped and "ERR" in wrapped
    assert "$BASH_COMMAND" in wrapped and "$LINENO" in wrapped
    assert "apt-get install -y libgl1" in wrapped  # original body preserved


def test_parse_failure_extracts_command_and_lineno():
    out = "some log\n__INSTALL_FAIL__:apt-get install -y libgl1:42\nmore log\n"
    cmd, lineno = _parse_install_failure(out)
    assert cmd == "apt-get install -y libgl1"
    assert lineno == 42


def test_parse_failure_none_when_no_marker():
    cmd, lineno = _parse_install_failure("clean run, no failures\n")
    assert cmd is None and lineno is None


def test_parse_failure_takes_first_marker():
    out = "__INSTALL_FAIL__:cmdA:1\n__INSTALL_FAIL__:cmdB:2\n"
    cmd, lineno = _parse_install_failure(out)
    assert cmd == "cmdA" and lineno == 1


def test_install_result_is_frozen():
    import dataclasses
    r = InstallResult(rc=0, failing_command=None, lineno=None, stderr="")
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        r.rc = 1  # type: ignore[misc]
