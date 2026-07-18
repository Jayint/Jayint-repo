from graph.model import (
    DepGraph, DiscoveredBy, EdgeType, Layer, Node, NodeType, State,
    package_id, project_id, syslib_id,
)
from graph.python.native.ctypes_scan import (
    CTYPES_GREP_CMD, LibHit, add_ctypes_runtime_libs, canonical_soname,
    parse_ctypes_grep,
)
from graph.python.native.system_libs import make_syslib_node
from conftest import FakeExecutor, make_result  # type: ignore

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


def test_rejects_identifier_embedded_and_partial_calls():
    grep = (
        "/x/site-packages/a/x.py:1:    NotCDLL('libfake.so')\n"
        "/x/site-packages/a/y.py:2:    lib = CDLL('lib' + suffix)\n"
    )
    assert parse_ctypes_grep(grep) == []


def test_matches_all_loader_call_shapes():
    grep = (
        "/x/site-packages/a/a.py:1:    cdll.LoadLibrary('libfoo.so')\n"
        "/x/site-packages/a/b.py:2:    windll.LoadLibrary('libbar.so')\n"
        "/x/site-packages/a/c.py:3:    LoadLibrary('libbaz.so')\n"
        "/x/site-packages/a/d.py:4:    dlopen('libqux.so')\n"
    )
    assert {h.lib for h in parse_ctypes_grep(grep)} == {
        "libfoo.so", "libbar.so", "libbaz.so", "libqux.so"
    }


def test_matches_raw_string_and_keyword_literal():
    grep = (
        "/x/site-packages/a/e.py:1:    CDLL(r'/usr/lib/libreal.so')\n"
        "/x/site-packages/a/f.py:2:    find_library(name='crypto')\n"
    )
    libs = {h.lib for h in parse_ctypes_grep(grep)}
    assert "/usr/lib/libreal.so" in libs
    assert "crypto" in libs
    assert canonical_soname("/usr/lib/libreal.so") == "libreal.so"


def test_dedups_same_lib_same_location():
    grep = "/x/site-packages/a/g.py:9:    CDLL('libz.so'); CDLL('libz.so')\n"
    assert len(parse_ctypes_grep(grep)) == 1


def test_canonical_soname_anchors_dot_so_suffix():
    # ".so" only counts as a suffix, not any substring
    assert canonical_soname("foo.something") == "libfoo.something.so"
    assert canonical_soname("libX.so") == "libX.so"


def test_comment_only_line_is_not_a_hit():
    assert parse_ctypes_grep(
        "/x/site-packages/a/x.py:3:    # CDLL(\"libcomment.so\")\n"
    ) == []


def test_inline_comment_call_is_not_a_hit():
    assert parse_ctypes_grep(
        "/x/site-packages/a/y.py:4:    x = 1  # CDLL('libc.so')\n"
    ) == []


def test_hash_inside_string_literal_does_not_suppress_real_call():
    # an earlier string containing '#' must not truncate a real call later on the line
    hits = parse_ctypes_grep(
        "/x/site-packages/a/z.py:5:    label = \"a#b\"; CDLL('libz.so')\n"
    )
    assert {h.lib for h in hits} == {"libz.so"}


def test_escaped_quote_in_string_does_not_suppress_real_call():
    # An escaped quote inside a string must not be treated as the string's end,
    # so a '#' still inside that string doesn't truncate the later real call.
    hits = parse_ctypes_grep(
        '/x/site-packages/a/w.py:6:    label = "a\\"#b"; CDLL("libz.so")\n'
    )
    assert {h.lib for h in hits} == {"libz.so"}


def test_call_name_inside_string_literal_is_not_a_hit():
    grep = (
        "/x/site-packages/a/s.py:1:    example = \"CDLL('libfake.so')\"\n"
        "/x/site-packages/a/t.py:2:    msg = 'use find_library(\"magic\")'\n"
    )
    assert parse_ctypes_grep(grep) == []


def test_single_line_triple_quoted_call_is_not_a_hit():
    assert parse_ctypes_grep(
        '/x/site-packages/a/u.py:3:    doc = """CDLL(\'libfake.so\')"""\n'
    ) == []


def test_real_call_after_earlier_string_on_same_line_is_a_hit():
    hits = parse_ctypes_grep(
        "/x/site-packages/a/v.py:4:    path = \"/x\"; lib = CDLL(\"libz.so\")\n"
    )
    assert {h.lib for h in hits} == {"libz.so"}


def _graph_with_project_and_pkg():
    proj = Node(id=project_id("app"), type=NodeType.PROJECT, name="app",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN, data={"installable": True})
    pkg = Node(id=package_id("python-magic", "0.4.27"), type=NodeType.PACKAGE,
               name="python-magic", layer=Layer.PIP,
               discovered_by=DiscoveredBy.RESOLVER, version="0.4.27")
    return DepGraph(nodes=(proj, pkg))


_GREP_OUT = (
    "/usr/local/lib/python3.11/site-packages/magic/__init__.py:44:"
    "    _lib = ctypes.util.find_library('magic')\n"
)


def test_mints_syslib_node_with_apt_fix_and_anchor_edge():
    ex = FakeExecutor(responses={"grep -rInE": make_result(stdout=_GREP_OUT)})
    out = add_ctypes_runtime_libs(_graph_with_project_and_pkg(), ex)
    node = out.get(syslib_id("libmagic.so"))
    assert node is not None
    assert node.type is NodeType.SYSTEM_LIB
    assert node.layer is Layer.SYSTEM
    assert node.discovered_by is DiscoveredBy.STATIC_SCAN
    assert node.state is State.UNKNOWN
    assert node.chosen_fix == "apt:libmagic1"
    assert node.check_command == "ldconfig -p | grep libmagic.so"
    assert "magic/__init__.py:44" in (node.evidence or "")
    assert any(
        e.src == project_id("app") and e.dst == syslib_id("libmagic.so")
        and e.relation is EdgeType.REQUIRES for e in out.edges
    )


def test_noop_when_no_ctypes_literals():
    ex = FakeExecutor(responses={"grep -rInE": make_result(stdout="")})
    g = _graph_with_project_and_pkg()
    assert add_ctypes_runtime_libs(g, ex) is g


def test_does_not_overwrite_existing_syslib_node():
    # An ldd-discovered PROBE node for the same soname must survive; only the
    # anchor edge is added.
    g = _graph_with_project_and_pkg()
    existing = make_syslib_node(
        "libmagic.so", discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
        apt="libmagic1", provenance="ldd (observed)",
    )
    g = g.with_node(existing)
    ex = FakeExecutor(responses={"grep -rInE": make_result(stdout=_GREP_OUT)})
    out = add_ctypes_runtime_libs(g, ex)
    assert out.get(syslib_id("libmagic.so")).discovered_by is DiscoveredBy.PROBE
