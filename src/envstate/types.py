# src/envstate/types.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple


# --- Vocabularies (plain string constants; repo uses strings, not enum.Enum) ---
class Source:
    STATIC_SCAN = "STATIC_SCAN"
    PROBE = "PROBE"
    DIAGNOSE = "DIAGNOSE"
    MEMORY = "MEMORY"
    LLM_GUESS = "LLM_GUESS"


class Status:
    REQUIRED = "REQUIRED"
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


# ACL authority sets — the heart of the trust boundary.
PRESENCE_STATUSES = frozenset({Status.PRESENT, Status.MISSING})
HOST_ONLY_SOURCES = frozenset({Source.PROBE, Source.DIAGNOSE})
LLM_ALLOWED_STATUSES = frozenset({Status.REQUIRED, Status.UNKNOWN})
LLM_ALLOWED_SOURCES = frozenset({Source.LLM_GUESS, Source.MEMORY, Source.STATIC_SCAN})


@dataclass(frozen=True)
class Evidence:
    probe_cmd: str
    rc: int
    stdout_predicate: str
    env_revision: int
    container_id: str


@dataclass(frozen=True)
class Requirement:
    id: str
    name: str
    kind: str            # "LanguagePackage" | "Tool" | "Header" | "SharedLibrary" | "PkgConfig"
    status: str          # one of Status.*
    source: str          # one of Source.*
    specifier: Optional[str] = None
    required_by: Tuple[str, ...] = ()
    provides: Tuple[str, ...] = ()
    suspected_provides: Tuple[str, ...] = ()
    evidence: Optional[Evidence] = None


@dataclass(frozen=True)
class ProviderFact:
    provider: str
    provides: Tuple[str, ...]
    source: str          # DIAGNOSE typically
    diagnose_cmd: Optional[str] = None


@dataclass(frozen=True)
class OpenFailure:
    signature: str
    first_seen_revision: int
    last_seen_revision: int
    hypothesis: Optional[str] = None
    already_tried: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BaseFacts:
    image: str
    distro: Optional[str] = None
    distro_version: Optional[str] = None
    arch: Optional[str] = None
    python: Optional[str] = None


@dataclass(frozen=True)
class EnvStateSnapshot:
    revision: int
    container_id: str
    base: BaseFacts
    requirements: Tuple[Requirement, ...] = ()
    provider_facts: Tuple[ProviderFact, ...] = ()
    open_failures: Tuple[OpenFailure, ...] = ()
    stale_evidence: Tuple[Requirement, ...] = ()
    plan_notes: Tuple[str, ...] = ()
