"""Execution-evidence contract: the parsed trace + the durable observation overlay.

Pure module — no src.envstate imports. A ParsedFailure is a TRANSIENT subgraph
reconstructed from log text; the ObservationOverlay is the PERSISTENT, append-only
causality record that references the DepGraph by stable node id.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

# (owner_descriptor, relation, target_descriptor); descriptors are "kind:name" strings.
ChainStep = tuple[str, str, str]


def stable_failure_id(failure_type: str, causal: str, phase: str) -> str:
    """Volatile-free identity: failure kind + causal anchor + phase.  No paths/linenos."""
    key = f"{failure_type}|{causal}|{phase}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()[:12]


@dataclass(frozen=True)
class ParsedFailure:
    phase: str                       # "build" | "collection" | "runtime"
    failure_type: str                # from classify_dependency_failure
    terminal: str                    # surface descriptor, e.g. "import:psycopg2"
    causal: str                      # deepest env-relevant descriptor (may == terminal)
    chain: tuple[ChainStep, ...]     # execution flow, deepest LAST
    blast_radius: frozenset[str] = frozenset()
    probe: tuple[str, str] | None = None   # (command, result) if a stage-3 probe ran
    raw_span: str = ""
    confidence: str = "runtime-deterministic"

    @property
    def stable_id(self) -> str:
        return stable_failure_id(self.failure_type, self.causal, self.phase)


@dataclass(frozen=True)
class Observation:
    stable_id: str
    anchor: str                      # DepGraph node id (or import:/error: id) this grounds to
    chain: tuple[ChainStep, ...]
    blast_radius: frozenset[str]
    phase: str
    raw_span: str
    sightings: int = 1
    seen_this_cycle: bool = True
    refuted_by: str | None = None
    resolved_by: str | None = None


@dataclass(frozen=True)
class ObservationOverlay:
    observations: tuple[Observation, ...] = ()

    def get(self, stable_id: str) -> Observation | None:
        for o in self.observations:
            if o.stable_id == stable_id:
                return o
        return None

    def with_observation(self, obs: Observation) -> "ObservationOverlay":
        existing = self.get(obs.stable_id)
        if existing is None:
            return replace(self, observations=self.observations + (obs,))
        merged = replace(existing, sightings=existing.sightings + 1, seen_this_cycle=True,
                         raw_span=obs.raw_span or existing.raw_span)
        kept = tuple(o for o in self.observations if o.stable_id != obs.stable_id)
        return replace(self, observations=kept + (merged,))


def parse(command: str, output: str, phase: str, ctx) -> ParsedFailure:
    raise NotImplementedError("implemented in Task 5")
