"""Curated Debian/Ubuntu provider tables for native/system needs.

Seeded from ``docs/DESIGN-static-probe-certified-dependency-graph.md`` section 11.
This module EXTENDS (does not import) ``failure_classifier``: the classifier
recognizes the *shape* of a native failure; these tables map a concrete soname /
tool / header onto the apt package that provides it.

Targeting Debian/Ubuntu only in V1 (design 10.5).  An unknown soname/tool maps to
``None`` (no LLM fallback in this plan; the node stays ``missing`` with evidence).
"""

from __future__ import annotations

from python_deps.import_mapping import normalize_package_name

# .so soname -> apt package.
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


# Distribution -> apt system-dev packages it needs to BUILD/RUN, for proactive
# prediction at resolve time (before the build runs).  Keyed by the PyPI
# distribution name; lookups are normalized so case/separators don't matter.
PACKAGE_TO_SYSTEM_DEPS: dict[str, list[str]] = {
    "psycopg2": ["libpq-dev"],
    "mysqlclient": ["default-libmysqlclient-dev"],
    "lxml": ["libxml2-dev", "libxslt1-dev"],
    "Pillow": ["libjpeg-dev", "zlib1g-dev"],
    "opencv-python": ["libgl1", "libglib2.0-0"],
}

# Precomputed normalized-name index for O(1), case-insensitive lookups.
_NORMALIZED_PACKAGE_SYSTEM_DEPS: dict[str, list[str]] = {
    normalize_package_name(name): deps for name, deps in PACKAGE_TO_SYSTEM_DEPS.items()
}


def apt_for_soname(soname: str) -> str | None:
    """Apt package providing ``soname`` (e.g. ``libGL.so.1`` -> ``libgl1``)."""
    return NATIVE_LIB_TO_APT.get(soname)


def apt_for_tool(tool: str) -> str | None:
    """Apt package providing a build tool/header (e.g. ``pg_config`` -> ``libpq-dev``)."""
    return TOOL_TO_APT.get(tool)


def system_deps_for_package(name: str) -> list[str]:
    """Apt system-dev packages a distribution needs, or ``[]`` if unknown.

    The lookup is name-normalized (``Pillow`` == ``pillow``) and returns a FRESH
    list each call so callers can mutate the result without corrupting the table.
    """
    return list(_NORMALIZED_PACKAGE_SYSTEM_DEPS.get(normalize_package_name(name), ()))
