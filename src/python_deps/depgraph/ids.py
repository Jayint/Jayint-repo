"""Stable node-id constructors for the concrete dependency graph.

Ids are deterministic strings of the form ``<kind>:<name>`` so the same
entity discovered by different stages collapses onto a single node.
"""

from __future__ import annotations

import re

# Characters that are safe to keep verbatim inside an id segment.  Package
# names are case-sensitive (``Pillow`` != ``pillow``) and may legitimately
# contain ``.``, ``-``, ``_`` and ``==`` (version pin), so we preserve those.
_UNSAFE = re.compile(r"[^A-Za-z0-9._=+-]+")

TEST_NODE_ID = "test:repo_tests_pass"


def slug(text: str) -> str:
    """Sanitize free text into an id-safe segment (whitespace/odd chars -> '-')."""
    cleaned = _UNSAFE.sub("-", text.strip())
    return cleaned.strip("-")


def import_id(name: str) -> str:
    return f"import:{name}"


def package_id(name: str, version: str | None) -> str:
    if version:
        return f"pkg:{name}=={version}"
    return f"pkg:{name}"


def syslib_id(soname: str) -> str:
    return f"syslib:{soname}"


def tool_id(tool: str) -> str:
    return f"tool:{tool}"
