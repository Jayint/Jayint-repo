"""Reproduce the PoC headline numbers with the production extractor.

Usage:
    python scripts/verify_service_detection.py <repos_root>

<repos_root> holds <owner>/<repo>/ checkouts. On the VM (READ-ONLY):
    /opt/runs/baselines/rat_python50_m3nothink_corrected/input/repo
Expected (see .superpowers/sdd/service-schema-poc-findings.md):
    repos with backing services : 23
    certifiable                 : ~75%   (declared_healthcheck ~34% + tcp_port ~41%)
    rq/rq                       : service:valkey, declared_healthcheck
"""
from __future__ import annotations

import os
import sys
from collections import Counter

from python_deps.depgraph.service_construct import build_service_nodes


def main(root: str) -> int:
    checks, repos_with, total = Counter(), set(), 0
    rq_node = None
    for owner in sorted(os.listdir(root)):
        od = os.path.join(root, owner)
        if not os.path.isdir(od):
            continue
        for repo in sorted(os.listdir(od)):
            rd = os.path.join(od, repo)
            if not os.path.isdir(rd):
                continue
            nodes = build_service_nodes(rd, owner=owner)
            if nodes:
                repos_with.add(f"{owner}/{repo}")
            total += len(nodes)
            for n in nodes:
                checks[n.check.source] += 1
                if f"{owner}/{repo}" == "rq/rq":
                    rq_node = n

    certifiable = checks["declared_healthcheck"] + checks["tcp_port"]
    print(f"repos with backing services : {len(repos_with)}")
    print(f"backing services            : {total}")
    for src, n in checks.most_common():
        print(f"  {src:22s} {n:4d}  ({n / max(total, 1) * 100:.0f}%)")
    print(f"certifiable                 : {certifiable / max(total, 1) * 100:.0f}%")
    print(f"rq/rq valkey                : {rq_node.check.command if rq_node else 'NOT DETECTED'}")

    ok = (len(repos_with) >= 20 and certifiable / max(total, 1) >= 0.65
          and rq_node is not None and rq_node.check.source == "declared_healthcheck")
    print("\nVERIFY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/verify_service_detection.py <repos_root>")
    sys.exit(main(sys.argv[1]))
