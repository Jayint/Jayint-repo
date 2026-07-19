"""Native blind-spot eval harness.

Structural (default): construct + score + FP guard, LLM-free, Docker-free.
Behavioral (``--docker``): builds the env from the graph's own rendered
setup.sh in a scratch container, flips each culprit's runtime trigger
before/after (proves even lazy-dlopen cases), records the collect-gate rc,
and runs a counterfactual (strip the emitted apt fix, rebuild, confirm the
trigger re-breaks) — see ``behavioral``/``_counterfactual`` below.

  python3 -m src.eval.native_blindspot --repos-root DIR [--out report.json]
  python3 -m src.eval.native_blindspot --repos-root DIR --docker [--out report.json]
  python3 -m src.eval.native_blindspot --compare BEFORE.json AFTER.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.advise import build_advisory_for_repo  # noqa: E402
from graph.compile.build_script import render_build_script  # noqa: E402
from src.eval.native_blindspot.oracle import load_oracle, load_triggers  # noqa: E402
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


# ---------------------------------------------------------------------------
# Behavioral pass (--docker) — construction alone only proves the graph EMITS
# the right apt fix; it never proves the fix actually satisfies the runtime
# import (a lazy dlopen inside a C extension can stay silent at import time
# and only blow up on first use). This section builds the real env from the
# graph's own rendered setup.sh in a scratch container, flips each culprit's
# trigger before/after, records the collect-gate rc, and — via
# ``_counterfactual`` — proves the flip was actually CAUSED by our apt fix
# (strip it back out and confirm the trigger re-breaks), not by something
# already present on the base image.
#
# Self-contained on purpose: mirrors scripts/construction_gate.py's container
# pattern (docker run/cp/exec, `nbs_<uuid8>` container ids) rather than
# importing that untracked script, so this module has no hidden dependency
# outside the tracked tree.
# ---------------------------------------------------------------------------


def _sh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _trigger_rc(cid: str, snippet: str) -> int:
    return _sh(["docker", "exec", "-w", "/src", cid, "python", "-c", snippet]).returncode


def behavioral(repo: str, graph, culprits: list[str], base_image: str) -> dict:
    """Build ``repo``'s env from ``graph``'s rendered setup.sh in a scratch
    container and record, per culprit trigger: rc on the bare ``base_image``
    (BEFORE — expect != 0, the lib is genuinely missing) and rc after
    setup.sh runs (AFTER — expect 0). Also records the collect-gate rc
    (``pytest --collect-only``) post-setup. The container is ALWAYS removed,
    even on exception — no leaked containers on a failed run."""
    triggers = load_triggers()
    setup_sh = render_build_script(graph, (), include_services=False)
    snippets = [triggers[c] for c in culprits if c in triggers]
    cid = f"nbs_{uuid.uuid4().hex[:8]}"
    try:
        _sh(["docker", "run", "-d", "--name", cid, base_image, "sleep", "infinity"])
        _sh(["docker", "cp", f"{repo}/.", f"{cid}:/src"])
        before = {s: _trigger_rc(cid, s) for s in snippets}          # base image: expect != 0
        with open(f"/tmp/{cid}.sh", "w") as fh:
            fh.write(setup_sh)
        _sh(["docker", "cp", f"/tmp/{cid}.sh", f"{cid}:/tmp/setup.sh"])
        _sh(["docker", "exec", "-w", "/src", cid, "bash", "/tmp/setup.sh"])
        after = {s: _trigger_rc(cid, s) for s in snippets}           # fixed env: expect 0
        col = _sh(["docker", "exec", "-w", "/src", cid, "python", "-m", "pytest",
                   "--collect-only", "-q", "-p", "no:cacheprovider"])
    finally:
        _sh(["docker", "rm", "-f", cid])
    return {
        "repo": repo,
        "trigger_flips": {s: {"before": before[s], "after": after[s]} for s in snippets},
        "collect_rc": col.returncode,
    }


# The exact per-node apt install line ``graph.compile.emit._command_for``
# renders (``if <cmd>`` half of ``_non_fatal_block``): anchored to the WHOLE
# line so this never also matches the `#@node ... provider=apt:<pkg>`
# annotation comment build_script.py emits for the same node.
_APT_INSTALL_LINE_RE = re.compile(
    r"^if apt-get install -y --no-install-recommends (\S+)$", re.MULTILINE
)


def _strip_apt_lines(setup_sh: str, apts: list[str]) -> str:
    """Neuter (not delete) the per-node apt install line for every package in
    ``apts``: ``if apt-get install ... <pkg>`` becomes ``if false ...``, so
    the wrapping ``then``/``else``/``fi`` (``_non_fatal_block``'s shape)
    stays syntactically valid bash — the package is simply never installed,
    same observable effect as deleting the line, without leaving a dangling
    block behind."""
    names = set(apts)

    def _neuter(m: re.Match) -> str:
        pkg = m.group(1)
        if pkg not in names:
            return m.group(0)
        return f"if false  # v3-counterfactual: stripped apt install for {pkg}"

    return _APT_INSTALL_LINE_RE.sub(_neuter, setup_sh)


def _counterfactual(
    repo: str, setup_sh: str, culprits: list[str], base_image: str, emitted_apts: list[str],
) -> dict:
    """Rebuild in a FRESH container from ``setup_sh`` with the graph's own
    emitted apt fix(es) (``emitted_apts``) regex-stripped out, then re-run
    each culprit's trigger — this is the causal check: if ``behavioral``'s
    AFTER flip was really caused by our apt fix (not by something already on
    ``base_image``), removing the fix must make the trigger RE-break
    (expect rc != 0 again). Recorded raw, no massaging. Container always
    removed, even on exception."""
    triggers = load_triggers()
    snippets = [triggers[c] for c in culprits if c in triggers]
    stripped_sh = _strip_apt_lines(setup_sh, emitted_apts)
    cid = f"nbs_{uuid.uuid4().hex[:8]}"
    try:
        _sh(["docker", "run", "-d", "--name", cid, base_image, "sleep", "infinity"])
        _sh(["docker", "cp", f"{repo}/.", f"{cid}:/src"])
        with open(f"/tmp/{cid}.sh", "w") as fh:
            fh.write(stripped_sh)
        _sh(["docker", "cp", f"/tmp/{cid}.sh", f"{cid}:/tmp/setup.sh"])
        _sh(["docker", "exec", "-w", "/src", cid, "bash", "/tmp/setup.sh"])
        rebroken = {s: _trigger_rc(cid, s) for s in snippets}        # fix stripped: expect != 0
    finally:
        _sh(["docker", "rm", "-f", cid])
    return {"repo": repo, "counterfactual_trigger_rc": rebroken}


def _docker_pass(repos_root: str, base_image: str) -> dict:
    """``--docker`` entry point: for every IN-SCOPE oracle repo present under
    ``repos_root``, build its graph fresh (independent of ``run()``'s
    structural pass — a ``--docker`` invocation never depends on ``run()``
    having executed first), then run ``behavioral`` + ``_counterfactual``."""
    oracle = load_oracle()
    out: dict[str, dict] = {}
    for name in sorted(os.listdir(repos_root)):
        repo = os.path.join(repos_root, name)
        if not os.path.isdir(repo):
            continue
        exp = oracle.get(name)
        if exp is None or not exp.in_scope:
            continue
        _adv, graph = build_advisory_for_repo(repo, base_image)
        if graph is None:
            continue
        culprits = exp.culprit.split(", ")
        setup_sh = render_build_script(graph, (), include_services=False)
        emitted_apts = [e.apt for e in extract_emitted_apt(graph)]
        out[name] = {
            "behavioral": behavioral(repo, graph, culprits, base_image),
            "counterfactual": _counterfactual(
                repo, setup_sh, culprits, base_image, emitted_apts
            ),
        }
    return out


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
    ap.add_argument("--docker", action="store_true",
                     help="behavioral pass (Docker required): build the env from the "
                          "rendered setup.sh, flip each culprit's runtime trigger "
                          "before/after, record the collect-gate rc, and run a "
                          "counterfactual (strip our apt fix, confirm the trigger "
                          "re-breaks). Attached to the report under 'behavioral'.")
    args = ap.parse_args()

    if args.compare:
        return _compare(*args.compare)

    if not args.repos_root:
        ap.error("--repos-root is required unless --compare is given")

    report = run(args.repos_root, base_image=args.base_image)
    if args.docker:
        report["behavioral"] = _docker_pass(args.repos_root, args.base_image)
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
