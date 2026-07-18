from graph.python.native.ctypes_scan import (
    LibHit, canonical_soname, parse_ctypes_grep,
)

_GREP = (
    "/usr/local/lib/python3.11/site-packages/magic/__init__.py:44:"
    "    _lib = ctypes.util.find_library('magic')\n"
    "/usr/local/lib/python3.11/site-packages/usb/backend/libusb1.py:80:"
    "    _lib = find_library('usb-1.0')\n"
    "/usr/local/lib/python3.11/site-packages/pymediainfo/__init__.py:11:"
    '    self.lib = CDLL("libmediainfo.so.0")\n'
    "/usr/local/lib/python3.11/site-packages/cairocffi/__init__.py:20:"
    "    cairo = ffi.dlopen('libcairo.so.2')\n"
    "/usr/local/lib/python3.11/site-packages/foo/util.py:9:"
    "    lib = CDLL(some_variable)\n"          # no literal -> no hit
)


def test_extracts_literals_from_all_call_shapes():
    hits = parse_ctypes_grep(_GREP)
    libs = {h.lib for h in hits}
    assert libs == {"magic", "usb-1.0", "libmediainfo.so.0", "libcairo.so.2"}


def test_evidence_carries_path_and_line():
    hits = {h.lib: h for h in parse_ctypes_grep(_GREP)}
    assert hits["magic"].evidence.startswith(
        "site-packages/magic/__init__.py:44"
    ) or "magic/__init__.py:44" in hits["magic"].evidence


def test_variable_argument_is_not_a_hit():
    assert parse_ctypes_grep(
        "/x/site-packages/foo/util.py:9:    lib = CDLL(some_variable)\n"
    ) == []


def test_canonical_soname_normalizes_base_and_passes_soname_through():
    assert canonical_soname("magic") == "libmagic.so"
    assert canonical_soname("usb-1.0") == "libusb-1.0.so"
    assert canonical_soname("libmediainfo.so.0") == "libmediainfo.so.0"
    assert canonical_soname("/usr/lib/libcairo.so.2") == "libcairo.so.2"
    assert canonical_soname("cairo") == "libcairo.so"
