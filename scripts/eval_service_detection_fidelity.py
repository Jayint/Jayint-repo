"""Precision/recall of evidence-only service detection vs the known-answer oracle.

Usage:  PYTHONPATH=src python3 scripts/eval_service_detection_fidelity.py <repos_root> <oracle.json>

The oracle (``tests/eval/fixtures/service_oracle.json``) is a human reading of the
real repos; it is ground truth and is never derived from the detector. This script
runs ``build_service_nodes`` over each repo and scores the detected service-name set
against the oracle's, pooled across repos.

``score`` is factored out of ``main`` so the arithmetic can be unit-tested without the
real corpus (which lives on the VM): see ``tests/eval/test_service_detection_fidelity.py``.
"""
from __future__ import annotations

import json
import os
import sys
from typing import NamedTuple

from python_deps.depgraph.service_construct import build_service_nodes

# Acceptance thresholds for the real run. Asymmetric on purpose: a missed backing
# service blinds the agent (recall is the costly axis); a false positive only spends
# turns and is contained by fail-soft, so precision is allowed more slack.
RECALL_FLOOR = 0.90
PRECISION_FLOOR = 0.80


class Fidelity(NamedTuple):
    """Pooled scoring result plus the per-repo disagreements for logging."""
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int
    rows: list[tuple[str, list[str], list[str]]]   # (repo, extra, missing)


def score(root: str, oracle: dict[str, list[str]]) -> Fidelity:
    """Pool tp/fp/fn of detected service names vs the oracle over every repo.

    A missing checkout raises ``SystemExit`` rather than scoring as "detected
    nothing": an empty detection for an absent directory is indistinguishable from a
    catastrophic recall failure and would silently fake a bad number. The repos live
    on the VM; a layout mistake is the likely cause.
    """
    tp = fp = fn = 0
    rows: list[tuple[str, list[str], list[str]]] = []
    for full, expected in sorted(oracle.items()):
        owner, repo = full.split("/", 1)
        rd = os.path.join(root, owner, repo)
        if not os.path.isdir(rd):
            raise SystemExit(f"repo not found: {rd}\n"
                             f"  (expected <repos_root>/<owner>/<repo>; check --root)")
        got = {n.name for n in build_service_nodes(rd, owner=owner)}
        exp = set(expected)
        t, f, m = len(got & exp), len(got - exp), len(exp - got)
        tp, fp, fn = tp + t, fp + f, fn + m
        if f or m:
            rows.append((full, sorted(got - exp), sorted(exp - got)))

    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return Fidelity(prec, rec, f1, tp, fp, fn, rows)


def main(root: str, oracle_path: str) -> int:
    with open(oracle_path) as fh:
        oracle: dict[str, list[str]] = json.load(fh)
    r = score(root, oracle)
    print(f"pooled precision {r.precision:.3f}  recall {r.recall:.3f}  F1 {r.f1:.3f}"
          f"   (tp={r.tp} fp={r.fp} fn={r.fn})")
    print("\nper-repo disagreements (extra / missing):")
    for full, extra, missing in r.rows:
        print(f"  {full:40s} +{extra}  -{missing}")
    ok = r.precision >= PRECISION_FLOOR and r.recall >= RECALL_FLOOR
    print("\nVERIFY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
