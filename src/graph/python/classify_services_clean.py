"""Evidence-only Service + Config classifier (clean tier). The construction default.

The LLM-free, table-free replacement for the env-classifier's Service+Config output. It
reads the repo, builds one evidence-only ServiceNode per declared backing service (via
:func:`service_construct.build_service_nodes` — no model, no service ``kind`` table),
attaches a derived ``data["setup"]`` compat view ONLY to certifiable services, repoints
config DSNs into ``setup["bind"]``, and derives advisory Config obligations. A
``declared_unverifiable`` service (no probe) is still surfaced to the agent, but carries
no ``setup``.

Task 4 — the classifier NO LONGER admits Service/Config nodes into the constructed
graph. It returns a :class:`RuntimePlan` (the construction artifact + serialization
boundary): SERVICE ``Node`` objects go in ``service_obligations`` (still built through
the pure :func:`patch_gate.admit_proposal` so the setup-shape / probe / evidence
validation is unchanged — they are extracted from the admitted throwaway graph), and the
Config tier goes in ``config_obligations`` as ``(var, value, provenance, bake_eligible)``.
The v3-arm loop later re-admits ``service_obligations`` into its working graph (same ids
→ ``with_node`` idempotency), so certify/frontier/hollow-pass/advise/repoint keep reading
SERVICE nodes from the graph unchanged.

Every service Node still gets a canonical ``data["service"]`` (``asdict(ServiceNode)``)
plus, when certifiable, the derived ``data["setup"]`` view.

Kept in its OWN module; ``make_construction_classifier`` (below) is the entrypoint
``run_v3_e2e`` / ``build_advisory_for_repo`` call.

Best-effort: the whole body is wrapped in a try/except that returns an EMPTY plan on any
error — this NEVER raises into the build.
"""
from __future__ import annotations

import dataclasses
import logging
import os
from urllib.parse import urlsplit

from graph.python.config_scan import (
    _SOURCE_AUTHORITATIVE,
    authoritative_ambiguous_vars,
    parse_env_example,
    parse_env_example_provenance,
    scan_authoritative_config,
    scan_env_defaults,
    scan_env_defaults_provenance,
    scan_env_reads,
    scan_framework_config_reads,
)
from graph.patch.proposal import NodeSpec, PatchProposal
from graph.patch.gate import admit_proposal
from graph.python.services.repoint import render_bind_steps
from graph.python.services.service_construct import build_service_nodes
from graph.python.read.static_collect import collect_static_evidence
from graph.runtime_plan import ConfigObligation, RuntimePlan, EMPTY_PLAN

logger = logging.getLogger(__name__)

# Evidence kinds that name a concrete service/kind — preferred anchor for a Service node.
_STRONG_SERVICE_KINDS = frozenset({"compose_service", "service_binding", "ci_service"})
# Evidence kinds that name a read/declared env var — fallback anchor for a Config obligation.
_CONFIG_EVIDENCE_KINDS = frozenset({"env_read", "env_var"})

# The evidence kind that anchors each provenance rung to its WINNING source: rung 1
# (authoritative config) -> the tox.ini/pytest.ini/... config_file row, rung 2
# (.env.example) -> the env_var row, rung 3 (code scan) -> the code-read row. B1
# residual (b) / Task 3 binding deliverable: the obligation must show the file that
# actually WON the value, not merely its provenance CATEGORY (`authoritative_config`
# cannot say which of four config files won; `code_scan_setdefault` names no site).
_RUNG_EVIDENCE_KIND: dict[int, str] = {1: "config_file", 2: "env_var", 3: "env_read"}


def _config_evidence_anchor(hits, var: str, provenance: dict | None) -> dict | None:
    """``{"file", "kind"}`` of the source that WON ``var``'s value (per
    ``provenance['rung']``) when such a hit exists, else any hit naming the var, else
    ``None``. Anchors to the WINNING file (tox.ini vs pytest.ini vs the code-read site),
    the concrete provenance the plain ``provenance.source`` category cannot carry."""
    preferred = _RUNG_EVIDENCE_KIND.get(provenance["rung"]) if isinstance(provenance, dict) else None
    if preferred is not None:
        for h in hits:
            if h.kind == preferred and h.name == var:
                return {"file": h.file, "kind": h.kind}
    for h in hits:
        if h.kind in _CONFIG_EVIDENCE_KINDS and h.name == var:
            return {"file": h.file, "kind": h.kind}
    return None


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


def _resolve_config_value(var, ambiguous, authoritative, example_prov, defaults_prov
                          ) -> tuple[str | None, dict | None]:
    """The winning value for ``var`` AND its structured provenance ``{rung, source}``
    (Task 3 / B1 residual a), resolved by the precedence chain in ``_config_nodes``'s
    docstring. Preserves the original truthiness-based fall-through (an empty value
    falls to the next rung). Provenance is JSON-able and carries eligibility on its
    own: rung 1/2 and rung-3a ``code_scan_setdefault`` bake; rung-3b
    ``code_scan_fallback`` is advisory-only. Rung-2 ``source`` is the ACTUAL
    ``.env.*`` file the value won from (B1 review #2). An ambiguous or absent var
    -> both None."""
    if var in ambiguous:
        return None, None
    av = authoritative.get(var)
    if av:
        return av, {"rung": 1, "source": _SOURCE_AUTHORITATIVE}
    ev = example_prov.get(var)           # (value, ".env.example" | ".env.sample" | ".env.template")
    if ev and ev[0]:
        return ev[0], {"rung": 2, "source": ev[1]}
    dv = defaults_prov.get(var)          # (value, "code_scan_setdefault" | "code_scan_fallback")
    if dv and dv[0]:
        return dv[0], {"rung": 3, "source": dv[1]}
    return None, None


def _config_obligations(repo_path, hits) -> list[ConfigObligation]:
    """One advisory :class:`ConfigObligation` per env var the tests read (never
    scheduled). Task 4 — the Config tier is plan-only now (no graph node, no gate,
    no evidence anchor): the obligation carries ``(var, value, provenance,
    bake_eligible)``, consumed ONLY by the render's ``#@config-env`` marker block.

    FIX B1 + MEASURED REGRESSION FIX (django-oauth-toolkit): when a value for
    that var is statically known it is carried as ``value`` so the renderer can
    bake it into the image as a Dockerfile ``ENV``. A var with no discoverable
    value stays a nameless advisory hint (``value=None``).

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
    example = parse_env_example_provenance(repo_path)
    defaults = scan_env_defaults_provenance(repo_path)
    obs: list[ConfigObligation] = []
    for var in sorted(read_vars):
        value, provenance = _resolve_config_value(var, ambiguous, authoritative, example, defaults)
        # DSN-SHAPED VALUES ARE DELIBERATELY WITHHELD FROM BAKE -- this is a
        # false-green guard, not an optimisation. Config obligations are advisory
        # by design (never certified). A stale `.env.example` DSN (e.g.
        # DATABASE_URL=postgres://localhost/db) baked straight into the image as
        # `ENV` would let the app import cleanly while silently pointing at the
        # WRONG host, masking a real failure -- exactly the false-green class this
        # codebase has been burned by before. A DSN-shaped value already has a
        # CERTIFIED binding path (`_dsn_configs` -> `render_bind_steps` ->
        # `setup["bind"]`); the uncertified bake must not compete with it. Do NOT
        # remove this exclusion to "support more vars" -- scope this feature to
        # non-DSN scalars (DJANGO_SETTINGS_MODULE, ...). Provenance is still
        # recorded on the obligation (a DSN is a discovered value too); only the
        # bakeable value is withheld.
        bake_value = value if (value is not None and not _looks_like_dsn(value)) else None
        evidence = _config_evidence_anchor(hits, var, provenance)
        obs.append(ConfigObligation.create(var, bake_value, provenance, evidence))
    return obs


def _service_obligations(graph, repo_path: str, arch, client, model, hits) -> tuple:
    """The gate-validated SERVICE ``Node`` objects for the plan.

    Services are still built as ``NodeSpec``s and run through the pure
    :func:`patch_gate.admit_proposal` (against a THROWAWAY copy of ``graph``) so the
    setup-shape / non-empty-probe / read-only / evidence validation is UNCHANGED; the
    admitted SERVICE nodes are then extracted from the throwaway result and handed to
    the plan. The input ``graph`` is never mutated. Empty tuple when the repo declares
    no service (or the batch is rejected)."""
    if not hits:
        return ()
    bundle_ids = frozenset(h.evidence_id for h in hits)
    configs = _dsn_configs(repo_path)
    service_nodes = _service_nodes(repo_path, arch, client, model, hits, configs)
    if not service_nodes:
        return ()
    proposal = PatchProposal(add_requirements=tuple(service_nodes), add_edges=())
    result = admit_proposal(graph, proposal, known_evidence_ids=bundle_ids)
    if not result.accepted:
        logger.warning("clean service proposal rejected: %s", result.errors)
        return ()
    return tuple(n for spec in service_nodes if (n := result.graph.get(spec.id)) is not None)


def classify_services_clean(graph, repo_path: str, client=None, model: str = "",
                            arch: dict | None = None) -> RuntimePlan:
    """Build the Service + Config :class:`RuntimePlan` for ``repo_path``.

    Returns a ``RuntimePlan`` (the construction artifact); the input ``graph`` is NEVER
    modified — Task 4 moves Service/Config out of the constructed graph. On ANY error the
    ``EMPTY_PLAN`` is returned (best-effort — never crashes the build)."""
    try:
        arch = arch or {}
        hits = collect_static_evidence(repo_path, graph)   # ONE collection, shared by both tiers
        service_obs = _service_obligations(graph, repo_path, arch, client, model, hits)
        config_obs = tuple(_config_obligations(repo_path, hits))
        return RuntimePlan(service_obligations=service_obs, config_obligations=config_obs)
    except Exception as exc:                     # best-effort: never crash the build
        logger.warning("clean service/config classify skipped: %s", exc)
        return EMPTY_PLAN


def make_construction_classifier(client=None, model: str = "", arch: dict | None = None):
    """Return classify(graph, repo_path) -> RuntimePlan: the evidence-only
    construction-time Service+Config classifier (replaces the deleted LLM
    env_classifier). No model is ever called; ``client``/``model``/``arch`` are
    accepted for call-site parity only."""
    def classify(graph, repo_path: str) -> RuntimePlan:
        return classify_services_clean(graph, repo_path, client=client, model=model, arch=arch)
    return classify
