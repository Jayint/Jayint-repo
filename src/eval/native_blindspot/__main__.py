"""Native blind-spot eval harness (structural: construct + score + FP guard).

  python3 -m src.eval.native_blindspot --repos-root DIR [--out report.json]
  python3 -m src.eval.native_blindspot --compare BEFORE.json AFTER.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.advise import build_advisory_for_repo  # noqa: E402
from src.eval.native_blindspot.oracle import load_oracle  # noqa: E402
from src.eval.native_blindspot.score import (  # noqa: E402
    aggregate, extract_emitted_apt, score_repo,
    _GENERAL_PROVENANCE, _CURATED_PROVENANCE,
)

# metrics worth diffing in --compare; false_positives is handled separately
# (never blended into a numeric delta — see module docstring on honesty).
_COMPARE_METRICS = (
    "package_recall", "cli_recall", "dlopen_recall",
    "covered_by_general", "covered_by_curated",
    "repos_fully_covered", "repos_in_scope",
)


def run(repos_root: str, base_image: str = "python:3.11-slim") -> dict:
    """Construct every repo under ``repos_root`` LLM-free, score the oracle
    repos, and assert the two new mechanisms (ctypes-scan, runtime-tool prior)
    emit nothing on repos NOT in the oracle (expected-negatives)."""
    oracle = load_oracle()
    scores = []
    false_positives: dict[str, list[str]] = {}
    for name in sorted(os.listdir(repos_root)):
        repo = os.path.join(repos_root, name)
        if not os.path.isdir(repo):
            continue
        _adv, graph = build_advisory_for_repo(repo, base_image)
        if graph is None:
            continue
        emitted = extract_emitted_apt(graph)
        if name in oracle:
            scores.append(score_repo(name, graph, oracle[name]))
        else:  # expected-negative: the new mechanisms must stay silent
            new = [e.apt for e in emitted
                   if e.provenance in (_GENERAL_PROVENANCE, _CURATED_PROVENANCE)]
            if new:
                false_positives[name] = new

    report = aggregate(scores)
    report["false_positives"] = false_positives  # MUST be {} on clean repos
    report["per_repo"] = {
        s.repo: {
            "covered": sorted(s.covered),
            "missed": sorted(s.missed),
            "in_scope": s.expectation.in_scope,
        }
        for s in scores
    }
    return report


def _compare(before_path: str, after_path: str) -> int:
    before = json.loads(Path(before_path).read_text())
    after = json.loads(Path(after_path).read_text())

    print(f"comparing {before_path} -> {after_path}")
    for key in _COMPARE_METRICS:
        b = before.get(key)
        a = after.get(key)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            print(f"  {key}: {b} -> {a} (delta {a - b:+g})")
        else:
            print(f"  {key}: {b} -> {a}")

    b_fp = before.get("false_positives", {})
    a_fp = after.get("false_positives", {})
    print(f"  false_positives: {len(b_fp)} repos -> {len(a_fp)} repos")
    # Diff on (repo, apt) PAIRS, not repo names: a NEW false-positive apt added
    # to an already-flagged repo must still surface (a repo-name set-diff would
    # mask it, hiding a real regression the before/after gate must catch).
    b_pairs = {(repo, apt) for repo, apts in b_fp.items() for apt in apts}
    a_pairs = {(repo, apt) for repo, apts in a_fp.items() for apt in apts}
    new_pairs = sorted(a_pairs - b_pairs)
    if new_pairs:
        print(f"    NEW false positives introduced (repo, apt): {new_pairs}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="native_blindspot")
    ap.add_argument("--repos-root", default="", help="dir of pinned-SHA repo checkouts")
    ap.add_argument("--out", default="", help="write the aggregate report JSON here")
    ap.add_argument("--base-image", default="python:3.11-slim")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE.json", "AFTER.json"),
                     help="skip construction; print per-metric deltas between two reports")
    args = ap.parse_args()

    if args.compare:
        return _compare(*args.compare)

    if not args.repos_root:
        ap.error("--repos-root is required unless --compare is given")

    report = run(args.repos_root, base_image=args.base_image)
    print(json.dumps({k: v for k, v in report.items() if k not in ("per_repo", "residual")},
                      indent=2, sort_keys=True))
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
