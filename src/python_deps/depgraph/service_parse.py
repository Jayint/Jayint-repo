"""Field parsers and derivation ladders for declared services (spec §3.1, §3.2).

Pure functions over an already-parsed YAML mapping. Every function obeys the
invariant: **degrade the field, never the node**. No service-specific knowledge.
"""
from __future__ import annotations

import re

from python_deps.depgraph.service_evidence import Mount, Port, PortSource

_SEED_MARKERS = ("docker-entrypoint-initdb.d", "/initdb")


def is_templated(s: str) -> bool:
    """`${VAR}`, `$(cmd)`, `${{ gha }}`, and bare `$VAR` are all unresolved."""
    return "$" in s


def _int_or_none(v: object) -> int | None:
    """Ports in the wild: 5432, '5432', '5000-5999' (range), '${PORT}'."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def parse_image(image: str) -> tuple[str, str | None]:
    """`postgres:16` -> ("postgres", "16"). Lexical split only, never a lookup.

    A templated TAG keeps the repo and nulls the tag (rq's
    `valkey/valkey:${{ matrix.valkey-version }}`). A templated image NAME yields
    ("", None) so the caller drops the node — there is no usable evidence.
    """
    if not image:
        return "", None
    img = image.split("@", 1)[0]                       # drop digest
    head, _, last = img.rpartition("/")
    name, sep, tag = last.partition(":")
    if is_templated(name):
        return "", None
    repo = f"{head}/{name}" if head else name
    if not sep or is_templated(tag):
        return repo, None
    return repo, tag


def parse_ports(entry: dict) -> tuple[Port, ...]:
    raw = entry.get("ports")
    if not isinstance(raw, list):
        return ()
    out: list[Port] = []
    for p in raw:
        if isinstance(p, dict):                        # long syntax
            tgt = _int_or_none(p.get("target"))
            if tgt:
                out.append(Port(container=tgt, host=_int_or_none(p.get("published"))))
            continue
        parts = str(p).split("/")[0].split(":")        # strip /tcp
        if len(parts) == 1:
            c = _int_or_none(parts[0])
            if c:
                out.append(Port(container=c, host=None))
        else:
            c = _int_or_none(parts[-1])
            if c:                                      # "${PORT}:5432" -> host unknown
                out.append(Port(container=c, host=_int_or_none(parts[-2])))
    return tuple(out)


def parse_expose(entry: dict) -> tuple[int, ...]:
    raw = entry.get("expose")
    if not isinstance(raw, list):
        return ()
    return tuple(p for p in (_int_or_none(str(e).split("/")[0]) for e in raw) if p)


def parse_env(entry: dict) -> dict[str, str]:
    env = entry.get("environment")
    if env is None:
        env = entry.get("env")                         # GH Actions services use `env:`
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    out: dict[str, str] = {}
    if isinstance(env, list):
        for item in env:
            k, _, v = str(item).partition("=")
            out[k.strip()] = v.strip()
    return out


def _join(v: object) -> str | None:
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v) if v else None


def parse_command(entry: dict) -> str | None:
    return _join(entry.get("command"))


def parse_entrypoint(entry: dict) -> str | None:
    return _join(entry.get("entrypoint"))


def parse_volumes(entry: dict) -> tuple[Mount, ...]:
    raw = entry.get("volumes")
    if not isinstance(raw, list):
        return ()
    out: list[Mount] = []
    for v in raw:
        if isinstance(v, dict):
            out.append(Mount(host=v.get("source"), container=v.get("target")))
        else:
            parts = str(v).split(":")
            if len(parts) >= 2:
                out.append(Mount(host=parts[0], container=parts[1]))
    return tuple(out)


def seed_mounts(volumes: tuple[Mount, ...]) -> tuple[Mount, ...]:
    """Mounts that seed schema — they need a RUNNING daemon, so they belong to
    ACTIVATE, never PROVISION (spec §4.2)."""
    return tuple(m for m in volumes
                 if m.container and any(k in m.container for k in _SEED_MARKERS))


def parse_depends_on(entry: dict) -> tuple[str, ...]:
    d = entry.get("depends_on")
    if isinstance(d, dict):
        return tuple(str(k) for k in d)
    return tuple(str(x) for x in d) if isinstance(d, list) else ()


_DSN_PORT = re.compile(r"://[^/\s]*?:(\d{2,5})")


def _rescue_from_siblings(name: str, sibling_env_blob: str) -> int | None:
    """`db` declares no ports, but the app declares `DATABASE_URL=...@db:5432/x`.
    The port is still evidence — it just lives in a sibling service's env.

    The `\\b` boundaries stop "db" from matching inside "mydb:5432".
    """
    m = re.search(rf"\b{re.escape(name)}:(\d{{2,5}})\b", sibling_env_blob)
    return int(m.group(1)) if m else None


def derive_port(ports: tuple[Port, ...], expose: tuple[int, ...],
                env: dict[str, str], name: str,
                sibling_env_blob: str) -> tuple[int | None, PortSource]:
    """ports: -> expose: -> own-env DSN -> sibling-env DSN -> unknown. Evidence-only."""
    for p in ports:
        if p.container:
            return p.container, "ports"
    if expose:
        return expose[0], "expose"
    for v in env.values():
        m = _DSN_PORT.search(v)
        if m:
            return int(m.group(1)), "env_dsn"
    rescued = _rescue_from_siblings(name, sibling_env_blob)
    if rescued:
        return rescued, "sibling_dsn"
    return None, "none"
