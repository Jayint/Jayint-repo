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

try:  # tomllib is stdlib on 3.11+; fall back to the tomli backport on 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib

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


def _file_block(label: str, body: str, cap_bytes: int) -> str | None:
    """Render one manifest FILE's heading + capped body. The cap is applied ONCE to the
    file's complete joined body (all its groups/sections), never per-group — a single
    manifest with many groups (extras, optional-dependencies, etc.) must still obey the
    2048-byte-per-file bound, not 2048 bytes times the group count."""
    if not body.strip():
        return None
    return f"  {label}:\n" + _indent(_cap(body, cap_bytes))


def _requirements_sections(root: pathlib.Path, cap_bytes: int) -> list[str]:
    out = []
    for req in sorted(root.glob("requirements*.txt")):
        try:
            body = req.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        block = _file_block(req.name, body, cap_bytes)
        if block:
            out.append(block)
    return out


def _pyproject_body(root: pathlib.Path) -> str:
    """Uncapped combined body of pyproject.toml's [project.dependencies] and
    [project.optional-dependencies] groups. Degrades to "" (contributes nothing) on any
    syntactic OR schema mismatch instead of raising."""
    try:
        raw = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    proj = data.get("project")
    if not isinstance(proj, dict):
        return ""
    parts: list[str] = []
    deps = proj.get("dependencies")
    if isinstance(deps, list) and deps:
        parts.append("[project.dependencies]:\n" + "\n".join(map(str, deps)))
    opt = proj.get("optional-dependencies")
    if isinstance(opt, dict):
        for name, items in sorted(opt.items()):
            if isinstance(items, list) and items:
                parts.append(f"[optional-dependencies].{name}:\n" + ", ".join(map(str, items)))
    return "\n\n".join(parts)


def _setupcfg_body(root: pathlib.Path) -> str:
    """Uncapped combined body of setup.cfg's [options] install_requires and
    [options.extras_require] groups. Interpolation is disabled (setup.cfg commonly has
    stray '%' in version specs / URLs) and the whole extraction — not just cp.read — is
    guarded against configparser.Error so a malformed file contributes nothing rather than
    raising."""
    cp = configparser.ConfigParser(interpolation=None)
    try:
        if not cp.read(root / "setup.cfg"):
            return ""
        parts: list[str] = []
        ir = cp.get("options", "install_requires", fallback="").strip()
        if ir:
            parts.append("[options] install_requires:\n" + ir)
        if cp.has_section("options.extras_require"):
            for k, v in cp.items("options.extras_require"):
                if v.strip():
                    parts.append(f"extras_require.{k}:\n" + v.strip())
        return "\n\n".join(parts)
    except configparser.Error:
        return ""


def _pipfile_body(root: pathlib.Path) -> str:
    """Uncapped combined body of Pipfile's [packages] and [dev-packages] groups. Degrades
    to "" on any syntactic OR schema mismatch instead of raising."""
    try:
        raw = (root / "Pipfile").read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    for sect in ("packages", "dev-packages"):
        items = data.get(sect)
        if isinstance(items, dict) and items:
            body = "\n".join((f"{k} {v}" if v not in ("*", "") else k) for k, v in items.items())
            parts.append(f"[{sect}]:\n" + body)
    return "\n\n".join(parts)


def render_declared(repo_path: "str | os.PathLike[str] | None", *, cap_bytes: int = 2048) -> str:
    """The STATIC declared-deps block from the repo's root manifests. Host-side, deterministic,
    identical for every arm. Returns "" for a missing/empty repo or when nothing is declared."""
    if not repo_path:
        return ""
    root = pathlib.Path(repo_path)
    if not root.is_dir():
        return ""
    sections: list[str] = []
    sections += _requirements_sections(root, cap_bytes)
    for label, body in (
        ("pyproject.toml", _pyproject_body(root)),
        ("setup.cfg", _setupcfg_body(root)),
        ("Pipfile", _pipfile_body(root)),
    ):
        block = _file_block(label, body, cap_bytes)
        if block:
            sections.append(block)
    if not sections:
        return ""
    return "DECLARED (from the repo, static)\n" + "\n".join(sections)


PIP_LIST_CMD = "python -m pip list --format=freeze 2>/dev/null"


def render_pip_list(freeze_raw: str, *, cap: int = 200) -> str:
    """`pip list --format=freeze` stdout → a one-line `pip installed (N): name ver, ...`.
    Freeze format is one `name==version` per line (no columnar header to strip); editable /
    URL installs (`-e ...`) have no `==` and are kept verbatim."""
    pkgs: list[str] = []
    for line in (freeze_raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, ver = line.split("==", 1)
            pkgs.append(f"{name} {ver}")
        else:
            pkgs.append(line)
    if not pkgs:
        return ""
    n = len(pkgs)
    extra = f", +{n - cap} more" if n > cap else ""
    return f"pip installed ({n}): " + ", ".join(pkgs[:cap]) + extra


# One command that prints installed package names one-per-line on both Debian/Ubuntu
# (dpkg-query) and Alpine (apk). Only one tool exists per image, so the union is that tool's
# output; the other half prints nothing. Both halves are read-only.
SYSPKG_PROBE_CMD = (r"(dpkg-query -W -f='${Package}\n' 2>/dev/null; apk info 2>/dev/null)")


def parse_syspkgs(raw: str) -> "frozenset[str]":
    return frozenset(
        ln.strip() for ln in (raw or "").splitlines()
        if ln.strip() and not ln.strip().startswith("#"))


def render_syspkg_delta(base: "frozenset[str]", now: "frozenset[str]", *, cap: int = 60) -> str:
    """Only the packages setup.sh ADDED on top of the base image. Empty `base` means the base
    inventory could not be captured, in which case `now - base` would be the entire base (~300
    lines of noise); omit the line rather than mislead."""
    if not base:
        return ""
    added = sorted(now - base)
    if not added:
        return "system packages added on top of base: (none)"
    extra = f", +{len(added) - cap} more" if len(added) > cap else ""
    return "system packages added on top of base: " + ", ".join(added[:cap]) + extra
