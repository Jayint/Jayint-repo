"""Typed evidence for one declared backing service (spec §3).

Pure value types. No parsing, no I/O, no service-specific knowledge — there is
no ``kind`` field and no recipe table anywhere in this package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PORT_SOURCES = ("ports", "expose", "env_dsn", "sibling_dsn", "none")
CHECK_SOURCES = ("declared_healthcheck", "tcp_port", "none")
RELEVANCES = ("ci_service", "ci_referenced_compose", "root_compose", "unreferenced_compose")
STATES = ("certifiable_obligation", "declared_unverifiable")

PortSource = Literal["ports", "expose", "env_dsn", "sibling_dsn", "none"]
CheckSource = Literal["declared_healthcheck", "tcp_port", "none"]
Relevance = Literal["ci_service", "ci_referenced_compose", "root_compose", "unreferenced_compose"]
State = Literal["certifiable_obligation", "declared_unverifiable"]


@dataclass(frozen=True)
class Port:
    container: int
    host: int | None = None


@dataclass(frozen=True)
class Mount:
    host: str | None
    container: str | None


@dataclass(frozen=True)
class Source:
    file: str        # repo-relative path
    locator: str     # "services.db" | "jobs.<job>.services.<name>"
    kind: str        # "compose" | "ci"


@dataclass(frozen=True)
class Check:
    """The ONLY executable string in the whole node."""
    command: str | None
    source: CheckSource
    interval_s: str | None = None
    retries: str | None = None
    timeout_s: str | None = None


@dataclass(frozen=True)
class ServiceNode:
    id: str
    name: str                      # declaration key; ALSO its declared hostname
    image: str                     # verbatim; may contain templates
    image_repo: str                # lexical parse (registry/org/name)
    image_tag: str | None

    ports: tuple[Port, ...]
    port: int | None
    port_source: PortSource
    endpoint: str | None

    env: dict[str, str]
    command: str | None
    entrypoint: str | None
    volumes: tuple[Mount, ...]
    seed: tuple[Mount, ...]

    check: Check
    depends_on: tuple[str, ...]

    relevance: Relevance
    provenance: tuple[Source, ...]
    raw: dict[str, dict]

    state: State
    unresolved: tuple[str, ...] = field(default_factory=tuple)
