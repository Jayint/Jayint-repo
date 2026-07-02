"""Curated Debian/Ubuntu provider tables for native/system needs.

Seeded from ``docs/DESIGN-static-probe-certified-dependency-graph.md`` section 11.
This module EXTENDS (does not import) ``failure_classifier``: the classifier
recognizes the *shape* of a native failure; these tables map a concrete soname /
tool / header onto the apt package that provides it.

Targeting Debian/Ubuntu only in V1 (design 10.5).  An unknown soname/tool maps to
``None`` (no LLM fallback in this plan; the node stays ``missing`` with evidence).
PACKAGE_TO_SYSTEM_DEPS (the curated package->syslib prediction table) was deleted 2026-07-01 — see construction-enrichment cluster 1a; the one remaining pre-install native prediction derives only from the resolver's wheel/sdist signal (seed.py).
"""

from __future__ import annotations

# Fast offline cache: known soname -> apt package.  apt-file fills misses at runtime
# (option B lazy install).  Do NOT delete entries — they short-circuit executor calls.
NATIVE_LIB_TO_APT: dict[str, str] = {
    # opencv runtime chain (the canonical cv2 import-time native gap).
    "libGL.so.1": "libgl1",
    "libgthread-2.0.so.0": "libglib2.0-0",
    "libglib-2.0.so.0": "libglib2.0-0",
    "libSM.so.6": "libsm6",
    "libXext.so.6": "libxext6",
    "libXrender.so.1": "libxrender1",
    "libxcb.so.1": "libxcb1",
    "libpq.so.5": "libpq5",
    # common companions of the above (still single-target, curated not learned)
    "libgomp.so.1": "libgomp1",
    "libGLU.so.1": "libglu1-mesa",
}

# build tool / config helper / header -> apt package.
TOOL_TO_APT: dict[str, str] = {
    "pg_config": "libpq-dev",
    "mysql_config": "default-libmysqlclient-dev",
    "gcc": "build-essential",
    "g++": "build-essential",
    "make": "build-essential",
    "cc": "build-essential",
    "Python.h": "python3-dev",
}

# Runtime CLI binaries a repo's OWN code shells out to (subprocess/os.system) ->
# apt package. Distinct from TOOL_TO_APT (pip *build* tools): these are external
# programs the code invokes at run time, which ldd/apt-on-build never surface.
# Deliberately SMALL and curated: only unambiguous, well-known external tools go
# here so the subprocess scanner (subprocess_scan.py) is a strict allowlist and
# never flags shell builtins, coreutils, or project-local scripts. Keep DISJOINT
# from TOOL_TO_APT so the two tool sources cannot mint two nodes for one apt pkg.
CLI_TOOL_TO_APT: dict[str, str] = {
    "git": "git",
    "adb": "adb",
    "sqlite3": "sqlite3",
    "java": "default-jre-headless",
    "ffmpeg": "ffmpeg",
    "pandoc": "pandoc",
    "curl": "curl",
    "wget": "wget",
    "unzip": "unzip",
    "gpg": "gnupg",
    "openssl": "openssl",
}

# Distributions whose import may need a system lib (whom to deep-probe).
NATIVE_RISK_PACKAGES: frozenset[str] = frozenset(
    {
        "opencv-python",
        "opencv-python-headless",
        "psycopg2",
        "mysqlclient",
        "lxml",
        "cryptography",
        "numpy",
        "scipy",
        "pandas",
        "torch",
        "tensorflow",
        "playwright",
        "selenium",
    }
)


def apt_for_soname(soname: str) -> str | None:
    """Apt package providing ``soname`` (e.g. ``libGL.so.1`` -> ``libgl1``)."""
    return NATIVE_LIB_TO_APT.get(soname)


def apt_for_tool(tool: str) -> str | None:
    """Apt package providing a build tool/header (e.g. ``pg_config`` -> ``libpq-dev``)."""
    return TOOL_TO_APT.get(tool)


def apt_for_cli_tool(tool: str) -> str | None:
    """Apt package providing a runtime CLI binary (e.g. ``adb`` -> ``adb``); None
    when ``tool`` is not on the curated subprocess allowlist."""
    return CLI_TOOL_TO_APT.get(tool)
