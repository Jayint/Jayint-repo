"""Block-level parse fidelity, inherited from the retired stage_parse_admit eval.

The old eval asked "does the parser recover (kind, params) for a known kind?". The new
design has no kinds, so we ask the evidence-only question instead: is every declared
service ADMITTED, and are its lexical fields recovered? The corpus is unchanged ground
truth (`provision_corpus.PROVISION_CASES`, 18 cases incl. 4 adversarial).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from evals.service_config_detection.provision_corpus import PROVISION_CASES  # noqa: E402
from graph.python.services.service_parse import (  # noqa: E402
    compose_healthcheck, derive_check, derive_port, parse_env, parse_expose,
    parse_image, parse_ports,
)


@pytest.mark.parametrize("case", PROVISION_CASES, ids=lambda c: c.name)
def test_every_declared_service_is_admitted(case):
    """No case is dropped. The old kind-keyed path dropped every exotic service."""
    entry = yaml.safe_load(case.compose_entry)
    entry = entry if isinstance(entry, dict) else {}
    image = entry.get("image") or ""
    repo, _tag = parse_image(image)
    assert repo, f"{case.name}: image {image!r} yielded no repo — service would be dropped"


@pytest.mark.parametrize("case", PROVISION_CASES, ids=lambda c: c.name)
def test_check_ladder_never_raises_and_records_its_rung(case):
    entry = yaml.safe_load(case.compose_entry)
    entry = entry if isinstance(entry, dict) else {}
    ports = parse_ports(entry)
    expose = parse_expose(entry)
    env = parse_env(entry)
    port, port_source = derive_port(ports, expose, env, case.name, ())
    hc_cmd, timing = compose_healthcheck(entry)
    check = derive_check(hc_cmd, timing, port)
    assert check.source in ("declared_healthcheck", "tcp_port", "none")
    assert port_source in ("ports", "expose", "env_dsn", "sibling_dsn", "none")
    if check.source == "none":
        assert check.command is None      # Task 1: Check.command is `str | None`
    else:
        assert check.command


def test_corpus_admits_the_exotic_tail():
    """The whole point: kinds outside any table still produce a node."""
    exotic = [c for c in PROVISION_CASES if c.kind in ("weaviate", "milvus", "qdrant")]
    assert exotic, "corpus lost its exotic cases"
    for case in exotic:
        entry = yaml.safe_load(case.compose_entry) or {}
        repo, _ = parse_image(entry.get("image") or "")
        assert repo, f"{case.name} dropped"
