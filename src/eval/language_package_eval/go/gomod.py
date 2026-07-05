"""Offline parsers for Go module manifests + the ``module_closure`` authority
ladder. Pure text/JSON — no ``go`` toolchain, no network. Analog of
``node/lockfile.py``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_BLOCK_OPEN = re.compile(r"^(require|replace|exclude)\s*\($")
_SINGLE = re.compile(r"^(require|replace|exclude)\s+(.*)$")


@dataclass(frozen=True)
class Require:
    path: str
    version: str
    indirect: bool = False


@dataclass(frozen=True)
class Replace:
    old_path: str
    old_version: str | None
    new_path: str
    new_version: str | None  # None => local filesystem target


@dataclass(frozen=True)
class Exclude:
    path: str
    version: str


@dataclass(frozen=True)
class GoMod:
    module_path: str
    go_version: str
    toolchain: str | None = None
    requires: tuple[Require, ...] = ()
    replaces: tuple[Replace, ...] = ()
    excludes: tuple[Exclude, ...] = ()


def _strip_comment(line: str) -> tuple[str, str]:
    """Split at the first ``//``; returns (code, comment-without-slashes)."""
    idx = line.find("//")
    if idx == -1:
        return line, ""
    return line[:idx], line[idx + 2 :]


def _go_version_tuple(go_version: str) -> tuple[int, ...]:
    """``"1.21"`` -> ``(1, 21)``; ``"go1.21.0"`` -> ``(1, 21, 0)``; ``""`` -> ``()``."""
    cleaned = go_version.lstrip("go")
    return tuple(int(p) for p in cleaned.split(".") if p.isdigit())


def _consume(directive, rest, comment, requires, replaces, excludes) -> None:
    parts = rest.split()
    if directive == "require" and len(parts) >= 2:
        requires.append(Require(parts[0], parts[1], "indirect" in comment.split()))
    elif directive == "exclude" and len(parts) >= 2:
        excludes.append(Exclude(parts[0], parts[1]))
    elif directive == "replace" and "=>" in parts:
        i = parts.index("=>")
        left, right = parts[:i], parts[i + 1 :]
        if not left or not right:
            return
        replaces.append(
            Replace(
                old_path=left[0],
                old_version=left[1] if len(left) > 1 else None,
                new_path=right[0],
                new_version=right[1] if len(right) > 1 else None,
            )
        )


def parse_go_mod(path: str | Path) -> GoMod:
    text = Path(path).read_text()
    module_path = go_version = ""
    toolchain: str | None = None
    requires: list[Require] = []
    replaces: list[Replace] = []
    excludes: list[Exclude] = []
    block: str | None = None

    for raw in text.splitlines():
        code, comment = _strip_comment(raw)
        s = code.strip()
        if not s:
            continue
        if s == ")":
            block = None
            continue
        m = _BLOCK_OPEN.match(s)
        if m:
            block = m.group(1)
            continue
        if block is not None:
            _consume(block, s, comment, requires, replaces, excludes)
            continue
        m = _SINGLE.match(s)
        if m:
            _consume(m.group(1), m.group(2), comment, requires, replaces, excludes)
            continue
        parts = s.split()
        if parts[0] == "module":
            module_path = parts[1]
        elif parts[0] == "go":
            go_version = parts[1]
        elif parts[0] == "toolchain":
            toolchain = parts[1]

    return GoMod(
        module_path=module_path,
        go_version=go_version,
        toolchain=toolchain,
        requires=tuple(requires),
        replaces=tuple(replaces),
        excludes=tuple(excludes),
    )
