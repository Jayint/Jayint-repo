import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.schema import DiscoveredBy, Layer, Node, NodeType, State
from src.envstate.check_quality import rewrite_syslib_check, check_can_detect_absence


def _syslib(check):
    return Node(id="syslib:libglib2.0-0", type=NodeType.SYSTEM_LIB, name="libglib2.0-0",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                check_command=check, chosen_fix="apt:libglib2.0-0")


def test_rewrite_dpkg_s_to_capability_check():
    out = rewrite_syslib_check(_syslib("dpkg -s libglib2.0-0"))
    assert out is not None and "dpkg -s" not in out
    assert "ldconfig" in out or "command -v" in out


def test_rewrite_returns_none_for_already_capability_check():
    assert rewrite_syslib_check(_syslib("ldconfig -p | grep -q libglib")) is None


def test_can_detect_absence_true_for_real_check():
    assert check_can_detect_absence("dpkg -s libgl1") is True
    assert check_can_detect_absence("python -c 'import cv2'") is True


def test_can_detect_absence_false_for_trivial():
    assert check_can_detect_absence("true") is False
    assert check_can_detect_absence("echo ok") is False
    assert check_can_detect_absence("ls /") is False
