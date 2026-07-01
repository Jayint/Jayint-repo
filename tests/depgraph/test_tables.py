"""Task 6 — curated native provider tables."""

from __future__ import annotations

from python_deps.depgraph.tables import (
    NATIVE_LIB_TO_APT,
    NATIVE_RISK_PACKAGES,
    TOOL_TO_APT,
    apt_for_soname,
    apt_for_tool,
)


def test_apt_for_soname_known():
    assert apt_for_soname("libGL.so.1") == "libgl1"
    assert apt_for_soname("libpq.so.5") == "libpq5"
    assert apt_for_soname("libglib-2.0.so.0") == "libglib2.0-0"


def test_apt_for_soname_unknown_is_none():
    assert apt_for_soname("libdoesnotexist.so.9") is None


def test_apt_for_tool_known():
    assert apt_for_tool("pg_config") == "libpq-dev"
    assert apt_for_tool("gcc") == "build-essential"
    assert apt_for_tool("Python.h") == "python3-dev"
    assert apt_for_tool("mysql_config") == "default-libmysqlclient-dev"


def test_apt_for_tool_unknown_is_none():
    assert apt_for_tool("totally_unknown_tool") is None


def test_native_risk_packages_membership():
    for pkg in ("opencv-python", "psycopg2", "lxml", "mysqlclient"):
        assert pkg in NATIVE_RISK_PACKAGES


def test_tables_are_nonempty_dicts():
    assert isinstance(NATIVE_LIB_TO_APT, dict) and NATIVE_LIB_TO_APT
    assert isinstance(TOOL_TO_APT, dict) and TOOL_TO_APT
    assert isinstance(NATIVE_RISK_PACKAGES, frozenset) and NATIVE_RISK_PACKAGES


# --- opencv soname chain ---


def test_opencv_soname_chain():
    assert apt_for_soname("libGL.so.1") == "libgl1"
    assert apt_for_soname("libglib-2.0.so.0") == "libglib2.0-0"
    assert apt_for_soname("libgthread-2.0.so.0") == "libglib2.0-0"
    assert apt_for_soname("libSM.so.6") == "libsm6"
    assert apt_for_soname("libXext.so.6") == "libxext6"
    assert apt_for_soname("libXrender.so.1") == "libxrender1"
    assert apt_for_soname("libxcb.so.1") == "libxcb1"
