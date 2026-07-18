from graph.python.native.apt import ObservedNeed, resolve


def _apt(kind, name):
    cands = resolve(ObservedNeed(kind, name, context="runtime"), executor=None)
    return cands[0].package if cands else None


def test_ctypes_family_sonames_resolve_offline():
    # executor=None => table-only (no apt-file / network). Each must resolve.
    assert _apt("soname", "libmagic.so") == "libmagic1"
    assert _apt("soname", "libmagic.so.1") == "libmagic1"
    assert _apt("soname", "libusb-1.0.so") == "libusb-1.0-0"
    assert _apt("soname", "libmediainfo.so.0") == "libmediainfo0v5"
    assert _apt("soname", "libcairo.so.2") == "libcairo2"
