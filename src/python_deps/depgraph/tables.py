"""Curated Debian/Ubuntu provider tables for native/system needs.

Seeded from ``docs/DESIGN-static-probe-certified-dependency-graph.md`` section 11.
This module EXTENDS (does not import) ``failure_classifier``: the classifier
recognizes the *shape* of a native failure; these tables map a concrete soname /
tool / header onto the apt package that provides it.

Targeting Debian/Ubuntu only in V1 (design 10.5).  An unknown soname/tool maps to
``None`` (no LLM fallback in this plan; the node stays ``missing`` with evidence).
"""

from __future__ import annotations

# .so soname -> apt package.
NATIVE_LIB_TO_APT: dict[str, str] = {
    "libGL.so.1": "libgl1",
    "libgthread-2.0.so.0": "libglib2.0-0",
    "libglib-2.0.so.0": "libglib2.0-0",
    "libpq.so.5": "libpq5",
    # common companions of the above (still single-target, curated not learned)
    "libSM.so.6": "libsm6",
    "libXext.so.6": "libxext6",
    "libXrender.so.1": "libxrender1",
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
