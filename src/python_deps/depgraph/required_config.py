"""Evidence-gated config-template providers.

Some repos keep runtime config in an ignored TOML file and document a local
``cp example.toml config.toml`` setup step.  This module promotes that recipe
only when runtime collection has already found a missing Config node and the
repository itself supplies a safe template-copy instruction.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import fnmatch
import re
import shlex
import tomllib

from python_deps.depgraph.schema import DepGraph, NodeType, Strength


_CP_RE = re.compile(
    r"\bcp\s+(?P<src>(?:\.?/)?[A-Za-z0-9_./-]*example[A-Za-z0-9_./-]*\.toml)"
    r"\s+(?P<dst>(?:\.?/)?[A-Za-z0-9_./-]*\.toml)\b"
)
_EVIDENCE_NAMES = (
    "README.md",
    "README.rst",
    "README.txt",
    "INSTALL.md",
    "docs/installation.md",
    "docs/configuration.md",
)
_SENSITIVE_PARTS = ("api_key", "apikey", "token", "secret", "password", "credential")


def _norm_rel(value: str) -> str:
    return value.strip().lstrip("./")


def _inside(root: Path, rel: str) -> Path | None:
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _gitignore_patterns(root: Path) -> tuple[str, ...]:
    path = root / ".gitignore"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()
    out = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        out.append(line.lstrip("/"))
    return tuple(out)


def _ignored(patterns: tuple[str, ...], rel: str) -> bool:
    rel = rel.strip("/")
    for pattern in patterns:
        pat = pattern.rstrip("/")
        if not pat:
            continue
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, f"{pat}/*"):
            return True
        if "/" not in pat and fnmatch.fnmatch(Path(rel).name, pat):
            return True
    return False


def _cp_recipes(root: Path) -> tuple[tuple[str, str, str, int], ...]:
    recipes: list[tuple[str, str, str, int]] = []
    for rel in _EVIDENCE_NAMES:
        path = root / rel
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _CP_RE.finditer(line):
                recipes.append((
                    _norm_rel(match.group("src")),
                    _norm_rel(match.group("dst")),
                    rel,
                    lineno,
                ))
    return tuple(recipes)


def _toml_paths(value, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], object]:
    out: dict[tuple[str, ...], object] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            out.update(_toml_paths(child, prefix + (str(key),)))
    else:
        out[prefix] = value
    return out


def _safe_template_match(src_path: Path, field: str) -> tuple[str, ...] | None:
    try:
        data = tomllib.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    field_lower = field.lower()
    for path, value in _toml_paths(data).items():
        if not path:
            continue
        if path[-1].lower() != field_lower:
            continue
        if any(part in field_lower for part in _SENSITIVE_PARTS) and value != "":
            return None
        return path
    return None


def _toml_check_command(dst: str, path: tuple[str, ...]) -> str:
    access = "data"
    for part in path:
        access += f".get({part!r}, {{}})"
    predicate = (
        f"{access} == ''"
        if any(part in path[-1].lower() for part in _SENSITIVE_PARTS)
        else f"{access} != {{}}"
    )
    code = (
        "import pathlib,tomllib; "
        f"data=tomllib.loads(pathlib.Path({dst!r}).read_text()); "
        f"raise SystemExit(0 if {predicate} else 1)"
    )
    return "python3 -c " + shlex.quote(code)


def enrich_required_config_templates(graph: DepGraph, repo_path: str | Path) -> DepGraph:
    root = Path(repo_path)
    patterns = _gitignore_patterns(root)
    if not patterns:
        return graph
    recipes = _cp_recipes(root)
    if not recipes:
        return graph

    enriched = graph
    for node in graph.nodes:
        if node.type is not NodeType.CONFIG:
            continue
        field = (node.name or node.id.split(":", 1)[-1]).strip()
        if not field:
            continue
        for src, dst, evidence_file, lineno in recipes:
            src_path = _inside(root, src)
            dst_path = _inside(root, dst)
            if src_path is None or dst_path is None:
                continue
            if not src_path.is_file() or src_path.is_symlink():
                continue
            if dst_path.exists() or dst_path.is_symlink():
                continue
            if not dst_path.parent.is_dir() or not _ignored(patterns, dst):
                continue
            field_path = _safe_template_match(src_path, field)
            if field_path is None:
                continue
            command = f"test -e {shlex.quote(dst)} || cp -- {shlex.quote(src)} {shlex.quote(dst)}"
            fix = f"config-template:{src}->{dst}"
            data = {
                **dict(node.data),
                "provider_backed": True,
                "asset_kind": "config_template",
                "template_source": src,
                "template_target": dst,
                "field_path": ".".join(field_path),
            }
            enriched = enriched.with_node(replace(
                node,
                check_command=_toml_check_command(dst, field_path),
                evidence=f"{evidence_file}:{lineno}: cp {src} {dst}",
                chosen_fix=fix,
                fix_candidates=(fix,),
                setup_commands=(command,),
                strength=Strength.HARD,
                data=data,
            ))
            break
    return enriched
