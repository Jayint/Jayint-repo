"""Deterministic static evidence collectors → compact LLM bundle (design §5.2). Pure.

Thin adapter that RESHAPES the existing config/service scanners' output into the
§5.2 bundle rows. It does NOT scan the repo blindly and does NOT create graph truth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from python_deps.depgraph.config_scan import scan_env_reads, parse_env_example
from python_deps.depgraph.service_scan import scan_ci_services, scan_compose_services

_GOAL = ("Infer local install/test/run environment requirements, not deployment "
         "requirements.")


@dataclass(frozen=True)
class DeterministicHit:
    evidence_id: str
    file: str
    kind: str                     # ci_service | compose_service | env_var | env_read
    snippet: str = ""
    name: str | None = None


def collect_static_evidence(repo_path: str) -> tuple[DeterministicHit, ...]:
    hits: list[DeterministicHit] = []
    n = 0

    def _add(file, kind, *, name=None, snippet=""):
        nonlocal n
        prefix = {"ci_service": "ci", "compose_service": "svc",
                  "env_var": "env", "env_read": "code"}.get(kind, "ev")
        hits.append(DeterministicHit(f"{prefix}.{n:02d}", file, kind,
                                     snippet=snippet, name=name))
        n += 1

    ci_services, _has_ci = scan_ci_services(repo_path)
    for svc, meta in sorted(ci_services.items()):
        # VERIFIED: service meta carries image/host/port — NOT a file path; use a generic label.
        _add(".github/workflows", "ci_service", name=svc, snippet=str(meta.get("image", svc)))
    for svc, meta in sorted(scan_compose_services(repo_path).items()):
        _add("docker-compose.yml", "compose_service", name=svc, snippet=str(meta.get("image", svc)))
    for var, default in sorted(parse_env_example(repo_path).items()):
        _add(".env.example", "env_var", name=var, snippet=str(default))
    for var, file in sorted(scan_env_reads(repo_path).items()):
        _add(file, "env_read", name=var)
    return tuple(hits)


def compact_bundle_json(hits: tuple[DeterministicHit, ...], goal: str = _GOAL) -> str:
    rows = []
    for h in hits:
        row = {"evidence_id": h.evidence_id, "file": h.file, "kind": h.kind}
        if h.name is not None:
            row["name"] = h.name
        if h.snippet:
            row["snippet"] = h.snippet
        rows.append(row)
    return json.dumps({"goal": goal, "deterministic_hits": rows}, indent=2)
