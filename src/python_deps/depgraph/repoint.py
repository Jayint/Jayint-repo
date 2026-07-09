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
from urllib.parse import urlsplit, urlunsplit

_LOCALHOST = "127.0.0.1"


def _repoint_host(value: str) -> str:
    """Return ``value`` with its host rewritten to loopback, all else preserved."""
    u = urlsplit(value)
    userinfo = ""
    if u.username:
        userinfo = u.username + (f":{u.password}" if u.password else "") + "@"
    netloc = f"{userinfo}{_LOCALHOST}" + (f":{u.port}" if u.port else "")
    return urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))


def _host_of(dsn: str) -> str | None:
    """The hostname component of a DSN: ``postgres://u:p@db:5432/x`` -> ``'db'``.

    Uses ``urlsplit`` (no kind table), so an exotic scheme is parsed too; a non-URL
    value yields ``None`` and is skipped by the caller.
    """
    try:
        return urlsplit(dsn).hostname
    except ValueError:
        return None


def render_bind_steps(
    service_names: Iterable[str],
    configs: Iterable[tuple[str, str]],
) -> list[str]:
    """``export <VAR>=<loopback DSN>`` for configs pointing at a declared service.

    Matched by the service's DECLARED HOSTNAME (its compose/CI key), not by a service
    ``kind``: the DSN ``postgres://u@db:5432/x`` binds to the service named ``db``.
    Order is preserved over ``configs``; a config whose value is not a DSN, or whose
    host is not a declared service, is skipped.
    """
    names = {n for n in service_names if n}
    steps: list[str] = []
    for var, value in configs:
        host = _host_of(value)
        if host is None or host not in names:
            continue
        steps.append(f"export {var}={_repoint_host(value)}")
    return steps
