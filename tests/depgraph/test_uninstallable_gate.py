"""Fix A wiring: an `installable=False` risk entry must turn a resolved Package
node into an UNRESOLVED node (no pip fix) that the renderer never emits."""
from python_deps.depgraph.emit import _is_reciped
from python_deps.depgraph.resolve_lock import _package_node
from python_deps.depgraph.resolve_link import _stamp

LINUX_X86 = "x86_64-manylinux_2_28"


def _risk(installable, build=False):
    return {"build_from_source": build, "artifact": None, "hash": None, "installable": installable}


def test_stamp_marks_uninstallable_and_drops_fix():
    node = _package_node("onnxruntime", "1.24.3")  # resolvable=True by default
    assert node.chosen_fix == "pip:onnxruntime"
    assert _is_reciped(node) is True  # before the gate: would be emitted

    stamped = _stamp(node, {"onnxruntime": _risk(installable=False)}, "3.10", LINUX_X86)

    assert stamped.data.get("uninstallable") is True
    assert stamped.chosen_fix is None
    assert stamped.fix_candidates == ()
    assert "no installable artifact" in (stamped.evidence or "")
    # The renderer's gate now drops it -> no `pip install onnxruntime==1.24.3`.
    assert _is_reciped(stamped) is False


def test_stamp_keeps_installable_node_reciped():
    node = _package_node("numpy", "2.2.6")
    stamped = _stamp(node, {"numpy": _risk(installable=True)}, "3.10", LINUX_X86)
    assert stamped.data.get("uninstallable") is not True
    assert stamped.chosen_fix == "pip:numpy"
    assert _is_reciped(stamped) is True


def test_stamp_missing_installable_key_is_noop():
    # Risk dicts from other/older code paths omit `installable` -> no marking.
    node = _package_node("requests", "2.34.2")
    stamped = _stamp(node, {"requests": {"build_from_source": False}}, "3.11", LINUX_X86)
    assert stamped.data.get("uninstallable") is not True
    assert _is_reciped(stamped) is True
