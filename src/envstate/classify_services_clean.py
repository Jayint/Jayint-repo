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

from python_deps.depgraph.config_scan import (
    parse_env_example,
    scan_env_defaults,
    scan_env_reads,
    scan_framework_config_reads,
)
from python_deps.depgraph.patch import NodeSpec, PatchProposal
from python_deps.depgraph.patch_gate import admit_proposal
from python_deps.depgraph.repoint import render_bind_steps
from python_deps.depgraph.service_construct import build_service_nodes
from python_deps.depgraph.service_scan import service_from_url
from python_deps.depgraph.static_collect import collect_static_evidence

logger = logging.getLogger(__name__)

# Evidence kinds that name a concrete service/kind — preferred anchor for a Service node.
_STRONG_SERVICE_KINDS = frozenset({"compose_service", "service_binding", "ci_service"})
# Evidence kinds that name a read/declared env var — preferred anchor for a Config node.
_CONFIG_EVIDENCE_KINDS = frozenset({"env_read", "env_var"})


def _dsn_configs(repo_path: str) -> list[tuple[str, str]]:
    """`(var, dsn)` pairs from env defaults + `.env.example` whose value is a service DSN.

    The repoint source: only values ``service_from_url`` recognizes as a DSN are kept.
    Order is deterministic (defaults first, then example) and each var appears once.
    """
    configs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in (scan_env_defaults(repo_path), parse_env_example(repo_path)):
        for var, value in source.items():
            if var in seen or service_from_url(value) is None:
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
    """One advisory Config hint NodeSpec per env var the tests read (never scheduled)."""
    read_vars = {**scan_env_reads(repo_path), **scan_framework_config_reads(repo_path)}
    return [
        NodeSpec(
            id=f"config:{var}", type="Config", name=var, layer="config",
            promotion="hint", check_command=None, evidence_ref=_config_evidence(hits, var),
        )
        for var in sorted(read_vars)
    ]


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
