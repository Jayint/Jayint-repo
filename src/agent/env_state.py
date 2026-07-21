"""Deterministic current-environment snapshot for the react arm (Bucket A, all arms).

Two halves: the STATIC declared-manifest block (host-side, once) folded into the
ENVIRONMENT prompt, and the PER-TURN installed-state block (pip list + system-package
delta) produced by the loop each turn. Everything here is pure/deterministic — the only
side effect is reading host files; container probes run in the loop, not here — so the
parsers/renderers are unit-testable with no container.
"""
from __future__ import annotations

import configparser
import os
import pathlib
import tomllib

_FLAG = "REACT_ENV_STATE"


def env_state_enabled() -> bool:
    """On by default (Global Constraint); off ONLY when the flag is exactly "0"."""
    return os.getenv(_FLAG, "1") != "0"


def _indent(text: str) -> str:
    return "\n".join("    " + ln for ln in text.splitlines())


def _cap(text: str, cap_bytes: int) -> str:
    b = text.encode("utf-8", "replace")
    if len(b) <= cap_bytes:
        return text.rstrip()
    return b[:cap_bytes].decode("utf-8", "ignore").rstrip() + "\n… (truncated)"


def _requirements_sections(root: pathlib.Path, cap_bytes: int) -> list[str]:
    out = []
    for req in sorted(root.glob("requirements*.txt")):
        try:
            body = req.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if body.strip():
            out.append(f"  {req.name}:\n" + _indent(_cap(body, cap_bytes)))
    return out


def _pyproject_sections(root: pathlib.Path, cap_bytes: int) -> list[str]:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    proj = data.get("project") or {}
    out = []
    deps = proj.get("dependencies") or []
    if deps:
        out.append("  pyproject [project.dependencies]:\n"
                   + _indent(_cap("\n".join(map(str, deps)), cap_bytes)))
    for name, items in sorted((proj.get("optional-dependencies") or {}).items()):
        if items:
            out.append(f"  pyproject [optional-dependencies].{name}:\n"
                       + _indent(_cap(", ".join(map(str, items)), cap_bytes)))
    return out


def _setupcfg_sections(root: pathlib.Path, cap_bytes: int) -> list[str]:
    cp = configparser.ConfigParser()
    try:
        if not cp.read(root / "setup.cfg"):
            return []
    except configparser.Error:
        return []
    out = []
    ir = cp.get("options", "install_requires", fallback="").strip()
    if ir:
        out.append("  setup.cfg [options] install_requires:\n" + _indent(_cap(ir, cap_bytes)))
    if cp.has_section("options.extras_require"):
        for k, v in cp.items("options.extras_require"):
            if v.strip():
                out.append(f"  setup.cfg extras_require.{k}:\n" + _indent(_cap(v.strip(), cap_bytes)))
    return out


def _pipfile_sections(root: pathlib.Path, cap_bytes: int) -> list[str]:
    try:
        data = tomllib.loads((root / "Pipfile").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for sect in ("packages", "dev-packages"):
        items = data.get(sect) or {}
        if items:
            body = "\n".join((f"{k} {v}" if v not in ("*", "") else k) for k, v in items.items())
            out.append(f"  Pipfile [{sect}]:\n" + _indent(_cap(body, cap_bytes)))
    return out


def render_declared(repo_path, *, cap_bytes: int = 2048) -> str:
    """The STATIC declared-deps block from the repo's root manifests. Host-side, deterministic,
    identical for every arm. Returns "" for a missing/empty repo or when nothing is declared."""
    if not repo_path:
        return ""
    root = pathlib.Path(repo_path)
    if not root.is_dir():
        return ""
    sections: list[str] = []
    sections += _requirements_sections(root, cap_bytes)
    sections += _pyproject_sections(root, cap_bytes)
    sections += _setupcfg_sections(root, cap_bytes)
    sections += _pipfile_sections(root, cap_bytes)
    if not sections:
        return ""
    return "DECLARED (from the repo, static)\n" + "\n".join(sections)
