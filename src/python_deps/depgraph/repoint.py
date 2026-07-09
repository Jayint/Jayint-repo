"""Generic compose-host -> loopback DSN rewrite (evidence-only tier).

Pure, deterministic. For each declared-service DSN, emit an ``export <VAR>=<dsn>``
step with the host rewritten to ``127.0.0.1`` and everything else — scheme (incl.
dialect suffix), userinfo/creds, port, path, query, fragment — preserved. The daemon
the agent starts binds on loopback, so a pure host-swap keeps the app's own declared
creds consistent (no credential injection here). Works for redis/mysql/mongo/postgres/…
alike.

A config binds to a service by its DECLARED HOSTNAME (the service's compose/CI key),
matched with ``urllib.parse.urlsplit`` directly — NOT by a service ``kind`` via
``service_scan.service_from_url``. Hostname matching is strictly more precise (it
disambiguates two services of the same kind) and does not silently drop a valid DSN
that points at an exotic-scheme service.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

_LOCALHOST = "127.0.0.1"


def _safe_split(dsn: str) -> SplitResult | None:
    """A ``urlsplit`` result whose ``.hostname`` and ``.port`` are both safe to read,
    or ``None`` if the value cannot be fully parsed.

    ``urlsplit`` itself raises on e.g. ``redis://[db:6379``, and ``.port`` raises on
    ``postgres://db:bad/app`` — a value ``urlsplit`` otherwise parses happily. Both
    are forced here so a caller never trips over them later. A value that fails
    validation is SKIPPED, never silently rewritten with the bad component dropped.
    """
    try:
        u = urlsplit(dsn)
        _ = u.hostname, u.port          # force both to validate now
    except ValueError:
        return None
    return u


def _repoint_host(u: SplitResult) -> str:
    """Rewrite an already-validated ``SplitResult``'s host to loopback, all else preserved."""
    userinfo = ""
    if u.username:
        userinfo = u.username + (f":{u.password}" if u.password else "") + "@"
    netloc = f"{userinfo}{_LOCALHOST}" + (f":{u.port}" if u.port else "")
    return urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))


def _host_of(dsn: str) -> str | None:
    """The hostname component of a DSN: ``postgres://u:p@db:5432/x`` -> ``'db'``.

    Goes through ``_safe_split`` (no kind table), so an exotic scheme is parsed too and
    an unparseable value yields ``None``.
    """
    u = _safe_split(dsn)
    return u.hostname if u is not None else None


def render_bind_steps(
    service_names: Iterable[str],
    configs: Iterable[tuple[str, str]],
) -> list[str]:
    """``export <VAR>=<loopback DSN>`` for configs pointing at a declared service.

    Matched by the service's DECLARED HOSTNAME (its compose/CI key), not by a service
    ``kind``: the DSN ``postgres://u@db:5432/x`` binds to the service named ``db``.
    Order is preserved over ``configs``; a config whose value cannot be fully parsed (bad
    port), is not a DSN, or whose host is not a declared service, is skipped — never a crash.
    """
    names = {n for n in service_names if n}
    steps: list[str] = []
    for var, value in configs:
        u = _safe_split(value)
        if u is None or u.hostname is None or u.hostname not in names:
            continue
        steps.append(f"export {var}={_repoint_host(u)}")
    return steps
