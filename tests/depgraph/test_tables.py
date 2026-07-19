"""Task 6 — curated native provider tables.

Post-1.2d: the soname / build-tool / header -> apt tables were unified into
``os_resolver.PROVIDER_TABLE`` (covered by ``test_os_resolver.py``). This module
now keeps only the runtime-CLI authority (``CLI_TOOL_TO_APT``) and the native-risk
gate (``NATIVE_RISK_PACKAGES``), which the resolver does not supersede.
"""

from __future__ import annotations

from graph.python.native.tables import (
    CLI_TOOL_TO_APT,
    NATIVE_RISK_PACKAGES,
    PACKAGE_TO_RUNTIME_TOOLS,
    apt_for_cli_tool,
    runtime_tools_for,
)


def test_native_risk_packages_membership():
    for pkg in ("opencv-python", "psycopg2", "lxml", "mysqlclient"):
        assert pkg in NATIVE_RISK_PACKAGES


def test_tables_are_nonempty_dicts():
    assert isinstance(CLI_TOOL_TO_APT, dict) and CLI_TOOL_TO_APT
    assert isinstance(NATIVE_RISK_PACKAGES, frozenset) and NATIVE_RISK_PACKAGES


def test_runtime_tools_every_tool_is_a_cli_tool_to_apt_key():
    # Invariant that keeps minting offline-deterministic (apt_for_cli_tool must
    # resolve every tool). If this fails, add the tool to CLI_TOOL_TO_APT first.
    for pkg, tools in PACKAGE_TO_RUNTIME_TOOLS.items():
        for tool in tools:
            assert tool in CLI_TOOL_TO_APT, f"{pkg}->{tool} not in CLI_TOOL_TO_APT"


def test_runtime_tools_for_normalizes_dist_name():
    assert runtime_tools_for("GitPython") == ("git",)   # canonicalized
    assert runtime_tools_for("gitpython") == ("git",)
    assert runtime_tools_for("pypandoc") == ("pandoc",)
    assert runtime_tools_for("not-a-real-pkg") == ()
    assert runtime_tools_for("pdf2image") == ("pdftoppm", "pdfinfo")
    assert runtime_tools_for("dvc") == ()   # intentionally dropped (redundant via gitpython)


def test_runtime_tools_map_is_exactly_the_curated_set():
    assert PACKAGE_TO_RUNTIME_TOOLS == {
        "gitpython": ("git",),
        "pre-commit": ("git",),
        "copier": ("git",),
        "pypandoc": ("pandoc",),
        "graphviz": ("dot",),
        "pdf2image": ("pdftoppm", "pdfinfo"),
    }


def test_runtime_tool_apt_providers_are_correct():
    assert apt_for_cli_tool("dot") == "graphviz"
    assert apt_for_cli_tool("pdftoppm") == "poppler-utils"
    assert apt_for_cli_tool("pdfinfo") == "poppler-utils"
