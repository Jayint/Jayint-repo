"""Execution-evidence contract: the parsed trace + the durable observation overlay.

Pure module — no src.envstate imports. A ParsedFailure is a TRANSIENT subgraph
reconstructed from log text; the ObservationOverlay is the PERSISTENT, append-only
causality record that references the DepGraph by stable node id.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from graph.python.util.failure_classifier import classify_dependency_failure

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


_FRAME_RE = re.compile(r'^(?P<path>[^\s"][^:]*\.py):\d+: in ', re.MULTILINE)
_FRAME_TB_RE = re.compile(r'^\s*File "(?P<path>[^"]+\.py)", line \d+', re.MULTILINE)


def _walk_traceback(output: str) -> tuple[list[str], str | None]:
    """Return (ordered .py paths deepest-last, target path). Pytest and CPython
    traceback grammars only — structural, not error-vocabulary."""
    paths = [m.group("path") for m in _FRAME_RE.finditer(output)]
    if not paths:
        paths = [m.group("path") for m in _FRAME_TB_RE.finditer(output)]
    target = paths[0] if paths else None
    return paths, target


def _module_descriptor(path: str) -> str:
    stem = path.replace("\\", "/")
    if "test" in stem.rsplit("/", 1)[-1]:
        return f"target:{stem}"
    dotted = stem[:-3].replace("/", ".") if stem.endswith(".py") else stem
    return f"module:{dotted}"


def parse(command: str, output: str, phase: str, ctx) -> ParsedFailure:
    dep = classify_dependency_failure(command, output)
    ft = dep.failure_type
    if ft in ("module_not_found", "import_name_error"):
        root = f"import:{dep.import_name or ''}"
    elif ft == "native_library_missing":
        root = f"syslib:{dep.details.get('library', '')}"
    else:
        root = f"import:{dep.import_name or dep.package_name or ''}"

    paths, target = _walk_traceback(output)
    chain: list[ChainStep] = []
    descriptors = [_module_descriptor(p) for p in paths]
    if descriptors:
        for a, b in zip(descriptors, descriptors[1:]):
            chain.append((a, "imports", b))
        chain.append((descriptors[-1], "imports", root))
    else:
        chain.append((f"target:{target or 'unknown'}", "imports", root))

    blast = frozenset({target}) if target else frozenset()
    return ParsedFailure(phase=phase, failure_type=ft, terminal=root, causal=root,
                         chain=tuple(chain), blast_radius=blast,
                         raw_span=(dep.message or output)[:500])
