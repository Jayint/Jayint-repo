"""Evidence-only Service + Config classifier (clean tier). The construction default.

The LLM-free, table-free replacement for the env-classifier's Service+Config output. It
reads the repo, builds one evidence-only ServiceNode per declared backing service (via
:func:`service_construct.build_service_nodes` — no model, no service ``kind`` table),
attaches a derived ``data["setup"]`` compat view ONLY to certifiable services, repoints
config DSNs into ``setup["bind"]``, and emits advisory Config hint nodes — all admitted
through the pure :func:`patch_gate.admit_proposal`. A ``declared_unverifiable`` service
(no probe) is still admitted and surfaced to the agent, but carries no ``setup``.

Every graph node gets a canonical ``data["service"]`` (``dataclasses.asdict(ServiceNode)``)
plus, when certifiable, the derived ``data["setup"]`` view — so the eight existing readers
of ``data["setup"]`` keep working unchanged, and ``emit._is_service_reciped``
(``bool(data.get("setup"))``) means "certifiable" for free.

Kept in its OWN module; ``make_construction_classifier`` (below) is the entrypoint
``run_v3_e2e`` / ``build_advisory_for_repo`` call.

Best-effort: the whole body is wrapped in a try/except that returns the input ``graph``
on any error — this NEVER raises into the build. Every admitted ``NodeSpec.evidence_ref``
is a real ``collect_static_evidence`` bundle id (else ``validate_proposal`` would reject
the batch).
"""
from __future__ import annotations

import dataclasses
import logging
import os
from urllib.parse import urlsplit

from graph.config_scan import (
    authoritative_ambiguous_vars,
    parse_env_example,
    scan_authoritative_config,
    scan_env_defaults,
    scan_env_reads,
    scan_framework_config_reads,
)
from graph.patch import NodeSpec, PatchProposal
from graph.patch_gate import admit_proposal
from graph.repoint import render_bind_steps
from graph.service_construct import build_service_nodes
from graph.static_collect import collect_static_evidence

logger = logging.getLogger(__name__)

# Evidence kinds that name a concrete service/kind — preferred anchor for a Service node.
_STRONG_SERVICE_KINDS = frozenset({"compose_service", "service_binding", "ci_service"})
# Evidence kinds that name a read/declared env var — preferred anchor for a Config node.
_CONFIG_EVIDENCE_KINDS = frozenset({"env_read", "env_var"})


def _looks_like_dsn(value: str) -> bool:
    """A value we can repoint: it parses as a URL with a scheme and a host.

    NOT ``service_from_url``: that is gated by a hardcoded scheme->kind map, so it drops
    ``clickhouse://`` and ``valkey://`` before ``render_bind_steps`` can match them by
    hostname. Here any URL-with-a-host is a repoint candidate; the kind is irrelevant.
    """
    if "://" not in value:
        return False
    try:
        u = urlsplit(value)
    except ValueError:
        return False
    return bool(u.scheme) and bool(u.hostname)


def _dsn_configs(repo_path: str) -> list[tuple[str, str]]:
    """`(var, dsn)` pairs from env defaults + `.env.example` whose value is a service DSN.

    The repoint source: any value that parses as a URL with a host is kept (scheme-agnostic
    — exotic services are NOT dropped). Order is deterministic (defaults first, then example)
    and each var appears once. A DSN with an unparseable port is still kept here; the
    downstream ``render_bind_steps`` validates and skips it (never crashes).
    """
    configs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in (scan_env_defaults(repo_path), parse_env_example(repo_path)):
        for var, value in source.items():
            if var in seen or not _looks_like_dsn(value):
                continue
            configs.append((var, value))
            seen.add(var)
    return configs


def _service_evidence(hits, service_name: str, kind: str | None) -> str:
    """A bundle id anchoring this service: a strong hit naming it, else any bundle id."""
    for h in hits:
        if h.kind in _STRONG_SERVICE_KINDS and h.name in (service_name, kind):
            return h.evidence_id
    return hits[0].evidence_id


def _config_evidence(hits, var: str) -> str:
    """A bundle id anchoring this config var: a hit naming it, else any bundle id."""
    for h in hits:
        if h.kind in _CONFIG_EVIDENCE_KINDS and h.name == var:
            return h.evidence_id
    return hits[0].evidence_id


def _compat_setup(node, bind_steps: list[str]) -> dict:
    """Derived view of the evidence node in the legacy ``data['setup']`` shape.

    Construction emits NO commands (spec §3.0.2 invariant 1), so ``install`` and
    ``start`` are empty BY DESIGN — the agent writes them at repair time. ``probe`` is
    the evidence-derived readiness check. Eight consumers read this key (emit /
    build_script / populate / certify / schedule / advise / patch_gate / graph_scheduler);
    emitting a derived view keeps them all working unchanged.
    """
    return {"install": [], "start": "", "probe": node.check.command,
            "createdb": None, "post": [], "bind": bind_steps}


def _service_nodes(repo_path, arch, client, model, hits, configs) -> list[NodeSpec]:
    """One NodeSpec per declared BACKING service, built from evidence only.

    No LLM (``arch``/``client``/``model`` are accepted for signature parity but never
    used). No kind table. App-vs-backing detection lives inside ``build_service_nodes``.
    A probe-less service is admitted as ``declared_unverifiable`` (surfaced to the
    agent, no ``setup``) rather than dropped; only a ``certifiable_obligation`` service
    gets the derived compat ``setup`` view.
    """
    owner = os.path.basename(os.path.dirname(os.path.abspath(repo_path)))
    services = build_service_nodes(repo_path, owner=owner)
    names = tuple(s.name for s in services)
    specs: list[NodeSpec] = []
    for svc in services:
        setup = (_compat_setup(svc, render_bind_steps(names, configs))
                 if svc.state == "certifiable_obligation" else None)
        specs.append(NodeSpec(
            id=svc.id, type="Service", name=svc.name, layer="services",
            setup=setup,                       # None for declared_unverifiable
            service_kind=None,                 # there is no kind, by design
            data={"service": dataclasses.asdict(svc)},
            evidence_ref=_service_evidence(hits, svc.name, svc.image_repo),
        ))
    return specs


def _config_nodes(repo_path, hits) -> list[NodeSpec]:
    """One advisory Config hint NodeSpec per env var the tests read (never scheduled).

    FIX B1 + MEASURED REGRESSION FIX (django-oauth-toolkit): when a value for
    that var is statically known, it is attached as ``data["config_value"]`` so
    ``build_script._config_env_marker`` can bake it into the image as a
    Dockerfile ``ENV`` at render time. A var with no discoverable value is left
    exactly as before: a nameless advisory hint with no value payload.

    Value PROVENANCE precedence (highest first) -- this is the fix, not just
    the category-safe allowlist that predates it:

      1. ``scan_authoritative_config`` -- tox.ini ``[testenv] setenv``,
         pytest.ini ``[pytest]``, setup.cfg ``[tool:pytest]``, pyproject.toml
         ``[tool.pytest.ini_options]``. The project's OWN declared test config;
         a var ambiguous ACROSS these (``authoritative_ambiguous_vars``) is
         skipped outright -- never baked, never falls through to a lower rung.
      2. ``.env.example`` -- a curated value hint, still first-party.
      3. ``scan_env_defaults`` -- a LAST-RESORT ``.py`` code-scan fallback,
         used only when neither of the above names the var. It already
         excludes vendored/example/fixture paths (``tests/app/``,
         ``examples/``, ...) -- see its docstring -- because a bundled example
         app's own default is real code but never the project's configuration.
         (The real-world failure this fixes: django-oauth-toolkit's
         `tests/app/idp/manage.py` -- a vendored example Django app INSIDE the
         test suite -- set `DJANGO_SETTINGS_MODULE=idp.settings`, which the old
         code-scan-first order baked straight into the image, overriding the
         repo's real `tox.ini` value (`tests.settings`) and breaking
         `import idp` at test time; pytest-django reads the env var in
         preference to its own ini config, so the wrong ENV shadowed a setting
         the repo already had correct.)
    """
    read_vars = {**scan_env_reads(repo_path), **scan_framework_config_reads(repo_path)}
    authoritative = scan_authoritative_config(repo_path)
    ambiguous = authoritative_ambiguous_vars(repo_path)
    example = parse_env_example(repo_path)
    defaults = scan_env_defaults(repo_path)
    specs: list[NodeSpec] = []
    for var in sorted(read_vars):
        value = None if var in ambiguous else (
            authoritative.get(var) or example.get(var) or defaults.get(var)
        )
        data = None
        if value is not None and not _looks_like_dsn(value):
            # DSN-SHAPED VALUES ARE DELIBERATELY EXCLUDED HERE -- this is a
            # false-green guard, not an optimisation. Config nodes are
            # advisory by design (no probe, never certified -- see module
            # docstring above). A stale `.env.example` DSN (e.g.
            # DATABASE_URL=postgres://localhost/db) baked straight into the
            # image as `ENV` would let the app import cleanly while silently
            # pointing at the WRONG host, masking a real failure instead of
            # surfacing it -- exactly the false-green class this codebase has
            # been burned by before. A DSN-shaped value already has a
            # CERTIFIED binding path (`_dsn_configs` -> `render_bind_steps` ->
            # `setup["bind"]`); the uncertified bake here must not compete
            # with it. Do NOT remove this exclusion to "support more vars" --
            # scope this feature to non-DSN scalars (DJANGO_SETTINGS_MODULE,
            # ENVIRONMENT, ...).
            data = {"config_value": value}
        specs.append(NodeSpec(
            id=f"config:{var}", type="Config", name=var, layer="config",
            promotion="hint", check_command=None, evidence_ref=_config_evidence(hits, var),
            data=data,
        ))
    return specs


def classify_services_clean(graph, repo_path: str, client=None, model: str = "",
                            arch: dict | None = None):
    """Admit deterministic Service + Config nodes for ``repo_path`` into ``graph``.

    Returns a NEW graph with the admitted nodes, or the input ``graph`` unchanged on a
    rejected proposal or ANY error (best-effort — never crashes the build).
    """
    try:
        arch = arch or {}
        hits = collect_static_evidence(repo_path, graph)
        if not hits:
            return graph
        bundle_ids = frozenset(h.evidence_id for h in hits)

        configs = _dsn_configs(repo_path)
        service_nodes = _service_nodes(repo_path, arch, client, model, hits, configs)
        config_nodes = _config_nodes(repo_path, hits)

        proposal = PatchProposal(
            add_requirements=tuple(service_nodes + config_nodes), add_edges=())
        if proposal.is_empty():
            return graph
        result = admit_proposal(graph, proposal, known_evidence_ids=bundle_ids)
        if not result.accepted:
            logger.warning("clean service/config proposal rejected: %s", result.errors)
            return graph
        return result.graph
    except Exception as exc:                     # best-effort: never crash the build
        logger.warning("clean service/config classify skipped: %s", exc)
        return graph


def make_construction_classifier(client=None, model: str = "", arch: dict | None = None):
    """Return classify(graph, repo_path) -> graph: the evidence-only construction-time
    Service+Config classifier (replaces the deleted LLM env_classifier). No model is
    ever called; ``client``/``model``/``arch`` are accepted for call-site parity only."""
    def classify(graph, repo_path: str):
        return classify_services_clean(graph, repo_path, client=client, model=model, arch=arch)
    return classify
