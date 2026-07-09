"""Precision/recall of evidence-only service detection vs the known-answer oracle.

Recall is pooled over every repo. Precision is pooled over `complete` repos ONLY: a repo whose
catalog list is a known-true subset cannot tell us that an extra detection is wrong.

Usage:  PYTHONPATH=src python3 scripts/eval_service_detection_fidelity.py <repos_root> <oracle.json>
"""
from __future__ import annotations

import json
import os
import sys

from python_deps.depgraph.service_construct import build_service_nodes


def score(detected: dict[str, set[str]], oracle: dict[str, dict]) -> dict:
    tp = fp = fn = 0
    tp_complete = fp_complete = 0
    rows = []
    for full, spec in sorted(oracle.items()):
        exp = set(spec["must_detect"])
        got = detected[full]
        hit, extra, missing = got & exp, got - exp, exp - got
        tp += len(hit)
        fn += len(missing)
        if spec["complete"]:
            tp_complete += len(hit)
            fp_complete += len(extra)
            fp += len(extra)
        if extra or missing:
            rows.append((full, spec["complete"], sorted(extra), sorted(missing)))

    prec = tp_complete / (tp_complete + fp_complete) if (tp_complete + fp_complete) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
    return {"precision": prec, "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
            "tp_complete": tp_complete, "fp_complete": fp_complete, "rows": rows}


def _fmt(x: float | None) -> str:
    return "  n/a" if x is None else f"{x:.3f}"


def main(root: str, oracle_path: str) -> int:
    with open(oracle_path) as fh:
        oracle: dict[str, dict] = json.load(fh)

    detected: dict[str, set[str]] = {}
    for full in oracle:
        if "/" not in full:
            raise SystemExit(f"malformed oracle key (want '<owner>/<repo>'): {full!r}")
        owner, repo = full.split("/", 1)
        rd = os.path.join(root, owner, repo)
        if not os.path.isdir(rd):
            # A missing checkout must NEVER be scored as "detected nothing": that is
            # indistinguishable from a total recall failure and would silently fake a bad
            # number. The repos live on the VM; a layout mistake is the likely cause.
            raise SystemExit(f"repo not found: {rd}\n"
                             f"  (expected <repos_root>/<owner>/<repo>; check the root argument)")
        detected[full] = {n.name for n in build_service_nodes(rd, owner=owner)}

    r = score(detected, oracle)
    n_complete = sum(1 for v in oracle.values() if v["complete"])
    print(f"recall    {_fmt(r['recall'])}   (pooled over all {len(oracle)} repos; "
          f"tp={r['tp']} fn={r['fn']})")
    print(f"precision {_fmt(r['precision'])}   (pooled over {n_complete} `complete` repos ONLY; "
          f"tp={r['tp_complete']} fp={r['fp_complete']})")
    print(f"F1        {_fmt(r['f1'])}")
    print("\nper-repo disagreements (+extra / -missing; `~` = partial oracle, extras not scored):")
    for full, complete, extra, missing in r["rows"]:
        mark = " " if complete else "~"
        print(f" {mark}{full:40s} +{extra}  -{missing}")

    ok = (r["recall"] is not None and r["recall"] >= 0.90
          and r["precision"] is not None and r["precision"] >= 0.80)
    print("\nVERIFY:", "PASS" if ok else "FAIL", " (recall >= 0.90, precision >= 0.80)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
