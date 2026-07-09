"""The evidence-fusion pipeline (spec §10).

DISCOVER -> SCOPE -> FUSE -> CLASSIFY -> CERTIFY.
No service-specific knowledge lives here or anywhere below it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from python_deps.depgraph.service_evidence import (
    Check, Mount, Port, ServiceNode, Source,
)
from python_deps.depgraph.service_parse import (
    ci_healthcheck, compose_healthcheck, derive_check, derive_port, parse_command,
    parse_depends_on, parse_entrypoint, parse_env, parse_expose, parse_image,
    parse_ports, parse_volumes, seed_mounts,
)
from python_deps.depgraph.service_relevance import (
    ci_referenced_compose_files, compute_relevance,
)
from python_deps.depgraph.service_sources import (
    DEFAULT_SOURCES, RawDeclaration, discover_all,
)

_RELEVANCE_RANK = {"ci_service": 0, "ci_referenced_compose": 1,
                   "root_compose": 2, "unreferenced_compose": 3}


def _slug(s: str) -> str:
    """Lowercase alphanumeric squash, for comparing an image org to a repo owner.
    Named `_slug`, not `_norm`: `service_relevance._norm` is a PATH normalizer."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _healthcheck(decl: RawDeclaration) -> tuple[str | None, dict]:
    return ci_healthcheck(decl.entry) if decl.kind == "ci" else compose_healthcheck(decl.entry)


@dataclass(frozen=True)
class _Merged:
    """Best-rung-wins merge of one service's fields across its declarations."""
    ports: tuple[Port, ...]
    expose: tuple[int, ...]
    env: dict[str, str]
    command: str | None
    entrypoint: str | None
    volumes: tuple[Mount, ...]
    depends_on: tuple[str, ...]
    hc_cmd: str | None
    timing: dict
    sibling_values: tuple[str, ...]


def _merge(ordered: list[RawDeclaration]) -> _Merged:
    """First non-empty wins, walking declarations from highest relevance down.

    `env` merges the other way (`{**new, **acc}`) so the highest-relevance value of a
    duplicated key survives.
    """
    ports: tuple[Port, ...] = ()
    expose: tuple[int, ...] = ()
    env: dict[str, str] = {}
    command = entrypoint = None
    volumes: tuple[Mount, ...] = ()
    depends_on: tuple[str, ...] = ()
    hc_cmd, timing = None, {}
    sibling_values: tuple[str, ...] = ()          # NOT `blob` — Task 3 takes a tuple
    for d in ordered:
        ports = ports or parse_ports(d.entry)
        expose = expose or parse_expose(d.entry)
        env = {**parse_env(d.entry), **env}
        command = command or parse_command(d.entry)
        entrypoint = entrypoint or parse_entrypoint(d.entry)
        volumes = volumes or parse_volumes(d.entry)
        depends_on = depends_on or parse_depends_on(d.entry)
        sibling_values = sibling_values or d.doc_env_values
        if hc_cmd is None:
            hc_cmd, timing = _healthcheck(d)
    return _Merged(ports, expose, env, command, entrypoint, volumes,
                   depends_on, hc_cmd, timing, sibling_values)


def _unresolved_fields(image: str, image_tag: str | None, env: dict[str, str]) -> tuple[str, ...]:
    out: list[str] = []
    last = image.rsplit("/", 1)[-1]
    # `repo@sha256:...` legitimately has no tag; only a `:tag` that vanished is unresolved.
    if "@" not in last and ":" in last and image_tag is None:
        out.append("image_tag")
    out += [f"env.{k}" for k, v in env.items() if "$" in v]
    return tuple(out)


def _fuse(name: str, decls: list[RawDeclaration],
          ci_refs: frozenset[str]) -> ServiceNode | None:
    """Merge declarations of one service across sources, best-rung-wins per field."""
    # Highest-relevance declaration wins identity; all contribute evidence.
    ordered = sorted(decls, key=lambda d: _RELEVANCE_RANK[compute_relevance(d, ci_refs)])
    primary = ordered[0]

    image = next((d.entry.get("image") for d in ordered if d.entry.get("image")), "") or ""
    image_repo, image_tag = parse_image(image)
    if not image_repo:
        return None                              # templated image NAME: no evidence

    m = _merge(ordered)
    port, port_source = derive_port(m.ports, m.expose, m.env, name, m.sibling_values)
    check: Check = derive_check(m.hc_cmd, m.timing, port)

    raw: dict[str, dict] = {}
    for d in ordered:
        raw.setdefault(d.kind, d.entry)          # first (highest-relevance) per source kind

    return ServiceNode(
        id=f"service:{name}", name=name, image=image,
        image_repo=image_repo, image_tag=image_tag,
        ports=m.ports, port=port, port_source=port_source,
        endpoint=(f"localhost:{port}" if port else None),
        env=m.env, command=m.command, entrypoint=m.entrypoint,
        volumes=m.volumes, seed=seed_mounts(m.volumes),
        check=check, depends_on=m.depends_on,
        relevance=compute_relevance(primary, ci_refs),
        provenance=tuple(Source(d.file, d.locator, d.kind) for d in ordered),
        raw=raw,
        state=("certifiable_obligation" if check.source != "none"
               else "declared_unverifiable"),
        unresolved=_unresolved_fields(image, image_tag, m.env),
    )


def _is_app(name: str, decls: list[RawDeclaration], owner: str, repo_name: str,
            backing_names: frozenset[str]) -> bool:
    """App-vs-backing from evidence only (spec §10.4). Three rules, no service knowledge.

    Took over-detection from 594 nodes to 158 on the 50-repo corpus.
    """
    if name in backing_names:                    # structural: someone depends_on it
        return False
    if any(d.kind == "ci" for d in decls):
        return False                             # CI service containers are never the app
    if any(d.entry.get("build") for d in decls):
        return True                              # rule 1: locally built

    entry = decls[0].entry
    image = entry.get("image") or ""
    repo, _tag = parse_image(image)
    short = repo.rsplit("/", 1)[-1].lower()

    # rule 2: the image is named after the repo. DIRECTION MATTERS — the repo name is a
    # substring of the image name (`podman-compose` in `podman-compose-test`), not the reverse.
    if repo_name and repo_name.lower() in short:
        return True

    # rule 3: a first-party image that publishes no port and declares no healthcheck is the
    # app being deployed. Kills OpenCTI's 271 `opencti/connector-*`, keeps `supabase/postgres`
    # (first-party, but exposes a port).
    org = _slug(repo.split("/")[0]) if "/" in repo else ""
    own = _slug(owner)
    first_party = bool(org and own and (org in own or own in org))
    hc, _t = compose_healthcheck(entry)
    no_surface = not parse_ports(entry) and not parse_expose(entry) and not hc
    return bool(first_party and no_surface)


def build_service_nodes(repo: str, owner: str = "",
                        sources=DEFAULT_SOURCES) -> list[ServiceNode]:
    decls = discover_all(repo, sources)
    if not decls:
        return []
    ci_refs = ci_referenced_compose_files(repo)

    grouped: dict[str, list[RawDeclaration]] = {}
    for d in decls:
        grouped.setdefault(d.name, []).append(d)

    backing = frozenset(
        dep for d in decls for dep in parse_depends_on(d.entry))

    repo_name = os.path.basename(os.path.normpath(repo))

    nodes: list[ServiceNode] = []
    for name, group in grouped.items():
        if _is_app(name, group, owner, repo_name, backing):
            continue
        node = _fuse(name, group, ci_refs)
        if node is not None:
            nodes.append(node)
    return nodes
