"""Unit tests for dynamic soname->apt resolution (no Docker/network)."""

from __future__ import annotations

from python_deps.depgraph.apt_resolve import parse_apt_file_search


def test_parse_filters_to_exact_multiarch_path():
    stdout = (
        "libgl1: /usr/lib/x86_64-linux-gnu/libGL.so.1\n"
        "primus-libs: /usr/lib/primus/libGL.so.1\n"
        "libgl1-mesa-dev: /usr/lib/x86_64-linux-gnu/libGL.so\n"
    )
    assert parse_apt_file_search(stdout, "libGL.so.1", "x86_64-linux-gnu") == "libgl1"


def test_parse_rejects_cross_compile_and_picks_runtime_over_dev():
    stdout = (
        "libgomp1: /usr/lib/x86_64-linux-gnu/libgomp.so.1\n"
        "libgomp1-amd64-cross: /usr/x86_64-linux-gnu/lib/libgomp.so.1\n"
        "libgomp1-dev: /usr/lib/x86_64-linux-gnu/libgomp.so.1\n"
    )
    assert parse_apt_file_search(stdout, "libgomp.so.1", "x86_64-linux-gnu") == "libgomp1"


def test_parse_no_triplet_accepts_single_multiarch_dir():
    stdout = "libpq5: /usr/lib/aarch64-linux-gnu/libpq.so.5\n"
    assert parse_apt_file_search(stdout, "libpq.so.5", None) == "libpq5"


def test_parse_returns_none_when_no_match():
    assert parse_apt_file_search("", "libGL.so.1", "x86_64-linux-gnu") is None


from python_deps.depgraph.apt_resolve import multiarch_triplet, resolve_soname_apt


def test_resolve_known_soname_uses_table_without_executor(fake_executor):
    # libGL.so.1 is in the curated table -> resolve must NOT touch the executor.
    pkg, source = resolve_soname_apt("libGL.so.1", fake_executor)
    assert (pkg, source) == ("libgl1", "table")
    assert fake_executor.calls == []


def test_resolve_unknown_soname_falls_back_to_apt_file(fake_executor, make_result_fixture):
    fake_executor.responses = {
        "sysconfig": make_result_fixture(stdout="x86_64-linux-gnu\n"),
        "apt-file search": make_result_fixture(
            stdout="libfoo7: /usr/lib/x86_64-linux-gnu/libfoo.so.7\n"
        ),
    }
    pkg, source = resolve_soname_apt("libfoo.so.7", fake_executor)
    assert (pkg, source) == ("libfoo7", "apt-file")


def test_resolve_unknown_soname_unresolved_when_apt_file_missing(fake_executor):
    # Empty FakeExecutor -> apt-file search returns rc 127 (not ok) -> unresolved.
    pkg, source = resolve_soname_apt("libbar.so.9", fake_executor)
    assert pkg is None
    assert source == "unresolved"


def test_multiarch_triplet_none_when_probe_fails(fake_executor):
    assert multiarch_triplet(fake_executor) is None
