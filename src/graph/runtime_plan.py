"""RuntimePlan — the Service + Config construction artifact and serialization
boundary (Task 4 / review C2).

Construction (``classify_services_clean``) no longer admits Service/Config nodes
into the constructed DepGraph. It returns a ``RuntimePlan`` carrying:

  * ``service_obligations`` — the gate-validated SERVICE ``Node`` objects (the
    EXISTING ServiceNode/``data['setup']`` shapes). Reused for BOTH ends:
      - the renderers (setup.sh service installs + the ENTRYPOINT start script)
        read them, and
      - the v3-arm loop ADMITS them back into its working graph at loop start
        (the ADMISSION RULE — ADD-IF-ABSENT: a plan service is admitted only when
        no node with its id already exists, so a runtime-discovered / certified /
        demoted / repaired same-id service is left untouched). Because the services
        are back in the graph at runtime, certify (incl. the demote counter),
        ``scheduler_frontier``, the hollow-pass guard, advise's service listing,
        and DSN repoint all keep reading SERVICE nodes from the graph, UNCHANGED.

  * ``config_obligations`` — ``(var, value, provenance, bake_eligible)`` records
    for the advisory Config tier (never scheduled). Consumed ONLY by the render's
    ``#@config-env`` marker block. ``bake_eligible`` is the fail-closed provenance
    gate (:func:`config_bake_eligible`), the SAME rule Task 3 introduced.

The GRAPH stays the sole runtime state store; the plan is a construction
snapshot, serialized beside ``dep_graph.json`` as ``runtime_plan.json``.
Pure: no Docker/network/LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.model import DepGraph, Node
from graph.python.config_scan import (
    _ENV_EXAMPLE_FILES,
    _SOURCE_AUTHORITATIVE,
    _SOURCE_SETDEFAULT,
)

# The FULL (rung, source) pairs that may bake — RELOCATED verbatim from
# ``build_script._BAKE_ELIGIBLE_SOURCES_BY_RUNG`` (Task 4) so BOTH the plan
# builder (classify) and the renderer share one source of truth. Grounded in
# config_scan's provenance vocabulary (never a duplicated magic string): rung 1
# is exactly ``authoritative_config``; rung 2 is exactly the ``.env.*`` template
# filenames ``parse_env_example_provenance`` can win from; rung 3's ONLY
# bake-eligible source is ``code_scan_setdefault`` (3a). Rung-3b
# ``code_scan_fallback`` is deliberately absent. There is no open-vocabulary rung.
_BAKE_ELIGIBLE_SOURCES_BY_RUNG: dict[int, frozenset[str]] = {
    1: frozenset({_SOURCE_AUTHORITATIVE}),
    2: frozenset(_ENV_EXAMPLE_FILES),
    3: frozenset({_SOURCE_SETDEFAULT}),
}


def config_bake_eligible(provenance: dict | None) -> bool:
    """Bake-eligibility keyed on a config value's ``{rung, source}`` provenance
    (B1 residual c / Task 3). FAILS CLOSED on the FULL pair: baking a Dockerfile
    ENV is safety-sensitive (a wrong value silently shadows the real config), so a
    value bakes ONLY when its provenance is an exactly-recognized (rung, source)
    pair in ``_BAKE_ELIGIBLE_SOURCES_BY_RUNG``:

      * rung 1 with source ``authoritative_config``,
      * rung 2 with source == the winning ``.env.*`` template filename,
      * rung 3 with source ``code_scan_setdefault`` (the env-writing
        ``os.environ.setdefault`` entrypoint idiom — the DJANGO_SETTINGS_MODULE
        payoff).

    Everything else is advisory-only and NEVER bakes: ABSENT provenance (a legacy
    serialized or hand-built value-only obligation), a non-int / bool / float
    ``rung`` (``type(rung) is int`` explicitly rejects ``bool``, an ``int``
    subclass), an unknown ``rung``, a missing / empty / non-``str`` / unrecognized
    ``source``, and rung-3b ``code_scan_fallback``."""
    if not isinstance(provenance, dict):
        return False
    rung = provenance.get("rung")
    if type(rung) is not int or rung not in _BAKE_ELIGIBLE_SOURCES_BY_RUNG:
        return False                                  # rejects bool/float/unknown-rung
    source = provenance.get("source")
    return isinstance(source, str) and source in _BAKE_ELIGIBLE_SOURCES_BY_RUNG[rung]


@dataclass(frozen=True)
class ConfigObligation:
    """One advisory Config-tier record: an env var the tests read, its statically
    resolved value (``None`` when none was discovered, or when a DSN-shaped value
    was deliberately withheld from bake — it already has a certified bind path),
    the structured ``{rung, source}`` provenance, the fail-closed ``bake_eligible``
    flag (computed from provenance via :func:`config_bake_eligible`), and an
    ``evidence`` anchor ``{"file", "kind"}`` naming the source that actually WON the
    value (Task 3 binding deliverable — ``provenance.source`` records only the
    category, e.g. ``authoritative_config``, and cannot say WHICH of
    tox.ini/pytest.ini/setup.cfg/pyproject.toml won)."""

    var: str
    value: str | None = None
    provenance: dict | None = None
    bake_eligible: bool = False
    evidence: dict | None = None

    @classmethod
    def create(cls, var: str, value: str | None, provenance: dict | None,
               evidence: dict | None = None) -> "ConfigObligation":
        """Build an obligation, computing ``bake_eligible`` from ``provenance`` with
        the SAME fail-closed rule the renderer trusts — the single derivation site."""
        return cls(var=var, value=value, provenance=provenance,
                   bake_eligible=config_bake_eligible(provenance), evidence=evidence)

    def to_dict(self) -> dict:
        return {"var": self.var, "value": self.value, "provenance": self.provenance,
                "bake_eligible": self.bake_eligible, "evidence": self.evidence}

    @classmethod
    def from_dict(cls, d: dict) -> "ConfigObligation":
        """Fail-closed at the trust boundary: ``bake_eligible`` is RECOMPUTED from
        ``provenance`` via the fail-closed gate — the serialized ``bake_eligible`` flag
        is NEVER trusted. A hand-edited (or tampered) ``runtime_plan.json`` carrying
        ``bake_eligible: true`` beside a rung-99 / bogus-source provenance therefore
        renders NO marker; only a genuinely recognized (rung, source) pair bakes."""
        return cls(var=d["var"], value=d.get("value"),
                   provenance=d.get("provenance"),
                   bake_eligible=config_bake_eligible(d.get("provenance")),
                   evidence=d.get("evidence"))


@dataclass(frozen=True)
class RuntimePlan:
    """Construction snapshot of the Service + Config tiers (see module docstring)."""

    service_obligations: tuple[Node, ...] = ()
    config_obligations: tuple[ConfigObligation, ...] = ()

    def is_empty(self) -> bool:
        return not (self.service_obligations or self.config_obligations)

    def get_service(self, node_id: str) -> Node | None:
        for n in self.service_obligations:
            if n.id == node_id:
                return n
        return None

    def get_config(self, var: str) -> ConfigObligation | None:
        for c in self.config_obligations:
            if c.var == var:
                return c
        return None

    def admit_services(self, graph: DepGraph) -> DepGraph:
        """Admit every service obligation ADD-IF-ABSENT — the ADMISSION RULE in ONE
        place, reused by the v3 loop at loop start AND by the renderers.

        A plan service is added ONLY when no node with its id already exists
        (``graph.get(id) is None``). An id that already exists — a runtime-discovered,
        certified, demoted (``certify_fail_count``), or agent-repaired service — is left
        BYTE-UNTOUCHED. The GRAPH stays the sole runtime state store: admission never
        resurrects a demoted service to MISSING nor clobbers a repaired ``data['setup']``
        with the pristine construction snapshot. A None/empty plan or an all-present set
        is a strict no-op."""
        for node in self.service_obligations:
            if graph.get(node.id) is None:
                graph = graph.with_node(node)
        return graph

    def to_dict(self) -> dict:
        return {
            "service_obligations": [n.to_dict() for n in self.service_obligations],
            "config_obligations": [c.to_dict() for c in self.config_obligations],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RuntimePlan":
        d = d or {}
        return cls(
            service_obligations=tuple(
                Node.from_dict(n) for n in d.get("service_obligations", ())),
            config_obligations=tuple(
                ConfigObligation.from_dict(c) for c in d.get("config_obligations", ())),
        )


# The zero-obligation plan (a repo with no services and no read config vars — the
# common case). A module-level singleton so best-effort/None paths share one value.
EMPTY_PLAN = RuntimePlan()
