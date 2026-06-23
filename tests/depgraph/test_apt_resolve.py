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
