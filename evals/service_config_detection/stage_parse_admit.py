"""Eval stage 2 (parse-exactness) + stage 4 (probe-admissibility firewall).

Phase 1 of the service-provisioning eval. Binds to the SHIPPED clean-core and the E2
corpus to measure two things — NEVER re-implementing the logic under test:

  * stage 2 — does `parse_provisioning_spec` extract the right structured fields
    (kind / port / probe) from the real compose blocks?
  * stage 4 — does `normalize_probe` guarantee that EVERY probe that could reach node
    admission becomes read-only (`patch_gate.is_read_only`)? This is the admissibility
    firewall; its target is 1.0. The 9 exotic curl probes the LLM classifier path emits
    must all collapse to `nc -z 127.0.0.1 <port>`.

Pure/CI: no Docker, no model. The value of this stage is precisely that it exercises the
shipped functions — so it imports them, and imports nothing from any scratchpad PoC.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for _p in (str(_ROOT), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from python_deps.depgraph.provisioning_spec import parse_provisioning_spec  # noqa: E402
from python_deps.depgraph.service_recipes import (  # noqa: E402
    RECIPE_KINDS,
    normalize_probe,
)
from python_deps.depgraph.patch_gate import is_read_only  # noqa: E402
from evals.service_config_detection.provision_corpus import (  # noqa: E402
    CURL_PROBE_CASE,
    PROVISION_CASES,
)

# The 9 exotic curl-based readiness probes the LLM classifier path produces for
# non-recipe images (couchdb, weaviate, meilisearch, opensearch, chroma, vault,
# mailpit, localstack, ...). Verbatim strings + ports — each MUST rewrite to `nc -z`.
NINE_CURL_PROBES: tuple[tuple[str, int], ...] = (
    ("curl -f http://localhost:5984/", 5984),
    ("curl -sf http://localhost:8080/health?ready=1", 8080),
    ("curl -f http://localhost:7700/health", 7700),
    ("curl -sf http://localhost:9200", 9200),
    ("curl -sf 'http://localhost:8080/v1/.well-known/ready'", 8080),
    ("curl -f http://localhost:8000/api/v1/heartbeat", 8000),
    ("curl -sf http://127.0.0.1:8200/v1/sys/health", 8200),
    ("curl -sf http://localhost:8025/api/v1/messages", 8025),
    ("curl -f http://localhost:4566/_localstack/health", 4566),
)

_ARTIFACT = _ROOT / "evals" / "service_config_detection" / "artifacts" / "stage_parse_admit.json"

# Cases whose compose block carries a `healthcheck:` — the ONLY cases for which the
# parser should populate `spec.probe`. Exotic-image cases (keydb/couchdb/...) parse to
# kind=None with no probe, and that is correct, not a miss.
_HEALTHCHECK_CASES = frozenset({"cockroachdb", "cassandra", "mariadb"})


def measure_parse(cases=PROVISION_CASES) -> dict:
    """Stage 2: run the shipped parser over the corpus and report the honest contract.

    Never asserts `spec.kind == case.kind`: `_kind_of` returns None for non-standard
    images (keydb/couchdb/...), which is correct. We only pin the fields the parser is
    genuinely responsible for: it never raises, it extracts a port whenever `ports` is
    declared, it populates a probe exactly when a `healthcheck` exists, and it kinds the
    standard images it recognizes.
    """
    per_case: list[dict] = []
    parsed_ok = 0
    specs: dict[str, object] = {}
    for case in cases:
        try:
            entry = yaml.safe_load(case.compose_entry)
            spec = parse_provisioning_spec(case.name, entry)
        except Exception as exc:  # noqa: BLE001 — record, don't crash the measurement
            per_case.append({"name": case.name, "error": repr(exc)})
            continue
        parsed_ok += 1
        specs[case.name] = spec
        per_case.append(
            {
                "name": case.name,
                "parsed_kind": spec.kind,
                "port": spec.port,
                "probe": spec.probe,
                "has_healthcheck": spec.probe is not None,
            }
        )

    port_extracted = sum(1 for c in per_case if c.get("port") is not None)
    probe_cases = frozenset(c["name"] for c in per_case if c.get("probe") is not None)
    redis = specs.get("redis")
    return {
        "per_case": per_case,
        "n_cases": len(cases),
        "parsed_ok": parsed_ok,
        "port_extracted": port_extracted,
        "probe_cases": sorted(probe_cases),
        "probe_when_healthcheck": probe_cases == _HEALTHCHECK_CASES,
        "redis_image_kinded": bool(redis is not None and redis.kind == "redis"),
    }


def _admission_probes(cases) -> list[tuple[str, str | None, int | None, str | None]]:
    """Every `(source, probe, port, kind)` that could reach node admission.

    Per case: the parsed compose probe (if any) at the case's port with kind=None (the
    raw-probe firewall path), AND — for recipe kinds only — the deterministic known-kind
    default via `normalize_probe(None, port, kind)`. Plus the 9 exotic curl fixtures and
    the corpus's own CURL_PROBE_CASE.
    """
    probes: list[tuple[str, str | None, int | None, str | None]] = []
    for case in cases:
        entry = yaml.safe_load(case.compose_entry)
        spec = parse_provisioning_spec(case.name, entry)
        if spec.probe is not None:
            probes.append((f"{case.name}:compose", spec.probe, spec.port, None))
        if spec.kind in RECIPE_KINDS:
            probes.append((f"{case.name}:recipe", None, spec.port, spec.kind))
    for probe, port in NINE_CURL_PROBES:
        probes.append(("curl_fixture", probe, port, None))
    probes.append(("curl_corpus_case", CURL_PROBE_CASE["raw_probe"], CURL_PROBE_CASE["port"], None))
    return probes


def measure_probe_admissibility(cases=PROVISION_CASES) -> dict:
    """Stage 4 (THE FIREWALL): fraction of admission probes that `normalize_probe`
    renders read-only. Target 1.0. Any probe whose normalized form is neither `""` nor
    `is_read_only` is a real firewall breach and is reported in `non_admissible`.
    """
    probes = _admission_probes(cases)
    admissible = 0
    non_admissible: list[dict] = []
    for source, probe, port, kind in probes:
        out = normalize_probe(probe, port, kind)
        if out == "" or is_read_only(out):
            admissible += 1
        else:
            non_admissible.append(
                {"source": source, "probe": probe, "port": port, "kind": kind, "normalized": out}
            )

    total = len(probes)
    curl_rewrites_ok = all(
        (out := normalize_probe(probe, port)) == f"nc -z 127.0.0.1 {port}" and is_read_only(out)
        for probe, port in NINE_CURL_PROBES
    )
    return {
        "total": total,
        "admissible": admissible,
        "admissibility_rate": (admissible / total) if total else 0.0,
        "curl_rewrites_ok": curl_rewrites_ok,
        "non_admissible": non_admissible,
    }


def main() -> None:
    parse = measure_parse()
    admit = measure_probe_admissibility()
    _ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    _ARTIFACT.write_text(
        json.dumps({"parse": parse, "admissibility": admit}, indent=2, sort_keys=True)
    )
    print(
        f"[stage_parse_admit] parsed_ok={parse['parsed_ok']}/{parse['n_cases']} "
        f"port_extracted={parse['port_extracted']} "
        f"probe_when_healthcheck={parse['probe_when_healthcheck']} "
        f"redis_kinded={parse['redis_image_kinded']} | "
        f"admissibility_rate={admit['admissibility_rate']:.3f} "
        f"({admit['admissible']}/{admit['total']}) "
        f"curl_rewrites_ok={admit['curl_rewrites_ok']} "
        f"non_admissible={len(admit['non_admissible'])}"
    )


if __name__ == "__main__":
    main()
