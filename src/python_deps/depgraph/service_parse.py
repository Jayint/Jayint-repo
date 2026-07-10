"""Field parsers and derivation ladders for declared services (spec §3.1, §3.2).

Pure functions over an already-parsed YAML mapping. Every function obeys the
invariant: **degrade the field, never the node**. No service-specific knowledge.
"""
from __future__ import annotations

import re
import shlex
from urllib.parse import urlparse

from python_deps.depgraph.service_evidence import Check, Mount, Port, PortSource

_SEED_MARKERS = ("docker-entrypoint-initdb.d", "/initdb")


def is_templated(s: str) -> bool:
    """`${VAR}`, `$(cmd)`, `${{ gha }}`, and bare `$VAR` are all unresolved."""
    return "$" in s


_OPAQUE = "\x00"      # a span whose value is not declared anywhere in the file

# `${VAR:-default}` and `${VAR-default}`: name, then `:?-`, then the default (group 2).
_DEFAULT_SPAN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(:?-)(.*)", re.DOTALL)
_BARE_VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def expand_declared_defaults(s: str) -> str | None:
    """Resolve Compose interpolation to what the file DECLARES, lexically.

    `${VAR:-default}` and `${VAR-default}` carry a default the repo wrote down; that
    literal is evidence and is substituted. Every other span (`${VAR}`, `${VAR:?err}`,
    bare `$VAR`, and GitHub Actions `${{ expr }}`) has no declared value and becomes
    `_OPAQUE` — a sentinel that can never be mistaken for a name, a registry, or a tag.

    Reading a default is PARSING (the bytes are in the file), not MAPPING (`valkey`
    -> `redis` needs knowledge the file does not contain).

    Returns None if a `${` span is never closed — malformed, therefore no evidence.
    """
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "$" and i + 1 < n and s[i + 1] == "{":
            depth, j, close = 0, i + 1, -1
            while j < n:                       # match `}` to its `${`, counting nesting
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        close = j
                        break
                j += 1
            if close == -1:
                return None                    # unterminated span: malformed, no evidence
            inner = s[i + 1 + 1:close]         # text between `${` and its matching `}`
            m = _DEFAULT_SPAN.fullmatch(inner)
            if m:
                nested = expand_declared_defaults(m.group(2))
                if nested is None:
                    return None
                out.append(nested)             # the declared default, itself expanded
            else:
                out.append(_OPAQUE)            # `${VAR}`, `${VAR:?e}`, `${{ gha }}`: no value
            i = close + 1
        elif c == "$":
            m = _BARE_VAR.match(s, i + 1)
            if m:
                out.append(_OPAQUE)            # bare `$VAR`: no declared value
                i = m.end()
            else:
                out.append(c)                  # a lone `$` is just a byte
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _int_or_none(v: object) -> int | None:
    """Ports in the wild: 5432, '5432', '5000-5999' (range), '${PORT}'."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def parse_image(image: str) -> tuple[str, str | None]:
    """`postgres:16` -> ("postgres", "16"). Lexical split only, never a lookup.

    Compose interpolates BEFORE it parses: `/` and `:` inside a `${...}` span are
    template syntax, not reference delimiters. So expand the declared defaults first,
    then apply the reference grammar to the result.

    A templated TAG keeps the repo and nulls the tag (rq's
    `valkey/valkey:${{ matrix.valkey-version }}`). A templated image NAME or REGISTRY
    yields ("", None) so the caller drops the node — there is no usable evidence.
    """
    if not image:
        return "", None
    expanded = expand_declared_defaults(image)
    if expanded is None:
        return "", None
    img = expanded.split("@", 1)[0]                    # drop digest
    head, _, last = img.rpartition("/")
    name, sep, tag = last.partition(":")
    if _OPAQUE in head or _OPAQUE in name:             # the NAME is unknown: no evidence
        return "", None
    repo = f"{head}/{name}" if head else name
    if not sep or _OPAQUE in tag:
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


_DSN_PORT = re.compile(r"://[^/\s]*?:(\d{2,5})")   # own-env rung (host position)


def _url_host_port(value: str) -> tuple[str, int | None] | None:
    """`(host, port)` for a URL value, or None if it is not a usable URL.

    `postgres://db:5432@other/app` has USERNAME `db`, PASSWORD `5432`, HOST `other`.
    A bare token regex misreads that as "db is on 5432". urlparse cannot.

    `.port` raises ValueError on a templated port (`redis://db:$PORT`); the HOST is
    still authoritative there, so we keep the host and drop only the port.
    """
    try:
        parsed = urlparse(value)
        if not parsed.scheme:
            return None
        host = parsed.hostname
    except ValueError:                     # e.g. `redis://[db:6379` -> Invalid IPv6 URL
        return None
    if host is None:
        return None
    try:
        port = parsed.port
    except ValueError:                     # templated port: degrade the field, keep host
        port = None
    return host, port


def _rescue_from_siblings(name: str, sibling_values: tuple[str, ...]) -> int | None:
    """`db` declares no ports, but the app declares `DATABASE_URL=...@db:5432/x`.
    The port is still evidence — it just lives in a sibling service's env.

    Two rungs, because real repos use both forms (measured on the 50-repo corpus):
      * URL values (`postgres://u:p@db:5432/app`) — decided by urlparse HOST equality.
      * bare tokens (`KAFKA_HOSTS=kafka:9092`) — 8 of the PoC's 9 rescues. `\b` bounds
        stop `db` matching inside `mydb:5432`.

    A value containing `://` is decided by urlparse ALONE and never reaches the regex —
    not even when urlparse fails. Falling back to the regex there would re-open the
    userinfo hole (`postgres://[db:5432@other/app` is unparseable, yet the regex would
    happily rescue 5432 for `db`). Unparseable evidence yields no evidence.
    """
    for value in sibling_values:
        if "://" in value:
            hp = _url_host_port(value)
            if hp is not None and hp[0] == name and hp[1]:
                return hp[1]
            continue                       # URL values NEVER reach the regex
        m = re.search(rf"\b{re.escape(name)}:(\d{{2,5}})\b", value)
        if m:
            return int(m.group(1))
    return None


def derive_port(ports: tuple[Port, ...], expose: tuple[int, ...],
                env: dict[str, str], name: str,
                sibling_values: tuple[str, ...]) -> tuple[int | None, PortSource]:
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
    rescued = _rescue_from_siblings(name, sibling_values)
    if rescued:
        return rescued, "sibling_dsn"
    return None, "none"


TCP_CHECK = ("python3 -c \"import socket; "
             "socket.create_connection(('127.0.0.1', {port}), 1).close()\"")


def tcp_check(port: int) -> str:
    """Universal, service-agnostic liveness check derived from the declared port.

    `python3`, not `python` (absent from python3-only images and plain Debian/Ubuntu
    with only python3 installed), not `nc` (absent from slim images) and not
    `bash </dev/tcp/...`.
    """
    return TCP_CHECK.format(port=port)


def compose_healthcheck(entry: dict) -> tuple[str | None, dict]:
    hc = entry.get("healthcheck")
    if not isinstance(hc, dict):
        return None, {}
    test = hc.get("test")
    cmd: str | None = None
    if isinstance(test, list):
        parts = [str(x) for x in test]
        if parts and parts[0] == "NONE":
            return None, {}
        if parts and parts[0] in ("CMD", "CMD-SHELL"):
            parts = parts[1:]
        cmd = " ".join(parts) or None
    elif isinstance(test, str):
        cmd = test or None
    timing = {k: hc.get(k) for k in ("interval", "timeout", "retries") if hc.get(k)}
    return cmd, timing


def ci_healthcheck(entry: dict) -> tuple[str | None, dict]:
    """GH Actions: `options: --health-cmd "pg_isready" --health-interval 10s ...`

    Both the space-separated (`--health-cmd X`) and the equals (`--health-cmd=X`) forms
    are valid docker flags and appear in real workflows. After `shlex.split`, the equals
    form arrives as ONE token (`--health-cmd=pg_isready`), so split each token on its first
    `=` and fall back to the next token when there is no inline value.
    """
    opts = entry.get("options")
    if not isinstance(opts, str):
        return None, {}
    try:
        toks = shlex.split(opts)
    except ValueError:
        return None, {}
    cmd: str | None = None
    timing: dict = {}
    keys = {"--health-interval": "interval", "--health-timeout": "timeout",
            "--health-retries": "retries"}
    for i, t in enumerate(toks):
        flag, sep, inline = t.partition("=")
        val = inline if sep else (toks[i + 1] if i + 1 < len(toks) else None)
        if not val:
            continue
        if flag == "--health-cmd":
            cmd = val
        elif flag in keys:
            timing[keys[flag]] = val
    return cmd, timing


def derive_check(hc_cmd: str | None, timing: dict, port: int | None) -> Check:
    """declared healthcheck -> TCP on the declared port -> none. No table.

    Every rung must pass ``is_read_only``: the check runs inside certification and
    must never mutate the container. A `curl`/`wget` healthcheck fails that gate and
    falls THROUGH to the TCP rung — it never disqualifies the service.
    """
    from python_deps.depgraph.patch_gate import is_read_only   # local: avoids a cycle

    # A whitespace-only command (`test: "   "`) carries no content; strip before the
    # truthiness test so it falls THROUGH to the TCP rung rather than being admitted as a
    # declared check the host would then execute as blank whitespace.
    if hc_cmd and hc_cmd.strip() and is_read_only(hc_cmd):
        return Check(command=hc_cmd, source="declared_healthcheck",
                     interval_s=timing.get("interval"), retries=timing.get("retries"),
                     timeout_s=timing.get("timeout"))
    if port:
        return Check(command=tcp_check(port), source="tcp_port")
    return Check(command=None, source="none")
