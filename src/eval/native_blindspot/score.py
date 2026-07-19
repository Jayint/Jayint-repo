"""Pure scoring for the native blind-spot eval. No Docker, no construction."""

from __future__ import annotations

from dataclasses import dataclass

from graph.model import DepGraph, NodeType
from src.eval.native_blindspot.oracle import RepoExpectation

# apt name -> capability key: -dev and runtime variants of one library collapse,
# so "installed a -dev that pulls the runtime" counts as covered. A miss falls
# back to the apt name itself. Extend as the oracle grows.
_APT_CAPABILITY: dict[str, str] = {
    "libmagic1": "magic", "libmagic-dev": "magic",
    "libusb-1.0-0": "usb-1.0", "libusb-1.0-0-dev": "usb-1.0",
    "libmediainfo0v5": "mediainfo", "libmediainfo-dev": "mediainfo",
    "libcairo2": "cairo", "libcairo2-dev": "cairo",
    "libpango-1.0-0": "pango", "libpango1.0-dev": "pango",
    "libgl1": "gl", "libgl1-mesa-glx": "gl", "libgl1-mesa-dev": "gl",
}

# provenance strings minted by the two new mechanisms (attribution).
_GENERAL_PROVENANCE = "ctypes-scan (installed source)"
_CURATED_PROVENANCE = "runtime-tool prior"

# NodeType member -> the lowercase label the eval/report vocabulary expects.
# Keyed by enum MEMBER (not n.type.value, which is "Tool"/"SystemLib" — see
# graph/model.py) so this survives the enum's display-string casing.
_NODE_TYPE_LABEL = {NodeType.TOOL: "tool", NodeType.SYSTEM_LIB: "system_lib"}


def capability_key(apt_name: str) -> str:
    return _APT_CAPABILITY.get(apt_name, apt_name)


@dataclass(frozen=True)
class EmittedApt:
    apt: str
    capability: str
    node_type: str      # "tool" | "system_lib"
    provenance: str


def extract_emitted_apt(graph: DepGraph) -> list[EmittedApt]:
    """apt fixes ACTUALLY emitted by TOOL / SYSTEM_LIB nodes, tagged with
    provenance. Scores the node's ``chosen_fix`` (the single fix the compiler
    installs — see graph.compile.emit), NOT every ``fix_candidates`` alternative,
    so recall reflects what setup.sh really runs."""
    out: list[EmittedApt] = []
    for n in graph.nodes:
        label = _NODE_TYPE_LABEL.get(n.type)
        if label is None:
            continue
        fix = n.chosen_fix
        if fix and fix.startswith("apt:"):
            apt = fix[len("apt:"):]
            out.append(EmittedApt(apt, capability_key(apt), label, n.provenance or ""))
    return out


@dataclass(frozen=True)
class RepoScore:
    repo: str
    expected: frozenset[str]
    covered: frozenset[str]
    missed: frozenset[str]
    by_provenance: dict[str, frozenset[str]]
    expectation: RepoExpectation


def score_repo(repo: str, graph: DepGraph, exp: RepoExpectation) -> RepoScore:
    expected = frozenset(capability_key(a) for a in exp.expected_apt)
    emitted = extract_emitted_apt(graph)
    emitted_caps = frozenset(e.capability for e in emitted)
    covered = expected & emitted_caps
    by_prov: dict[str, set[str]] = {}
    for e in emitted:
        if e.capability in covered:
            by_prov.setdefault(e.provenance, set()).add(e.capability)
    return RepoScore(
        repo=repo, expected=expected, covered=covered, missed=expected - emitted_caps,
        by_provenance={k: frozenset(v) for k, v in by_prov.items()}, expectation=exp,
    )


def aggregate(scores: list[RepoScore]) -> dict:
    """Roll up recall over IN-SCOPE repos, split by class and by mechanism."""
    in_scope = [s for s in scores if s.expectation.in_scope]

    def _recall(pick) -> float | None:
        exp = sum(len(frozenset(capability_key(a) for a in pick(s.expectation))) for s in in_scope)
        cov = sum(len(frozenset(capability_key(a) for a in pick(s.expectation)) & s.covered)
                  for s in in_scope)
        return cov / exp if exp else None

    covered_by_general = sum(
        len(s.by_provenance.get(_GENERAL_PROVENANCE, frozenset())) for s in in_scope
    )
    covered_by_curated = sum(
        len(s.by_provenance.get(_CURATED_PROVENANCE, frozenset())) for s in in_scope
    )
    repos_fully = [s.repo for s in in_scope if not s.missed]
    repos_missed = {s.repo: sorted(s.missed) for s in in_scope if s.missed}
    return {
        "repos_in_scope": len(in_scope),
        "repos_fully_covered": len(repos_fully),
        "package_recall": _recall(lambda e: e.expected_apt),
        "cli_recall": _recall(lambda e: e.cli),
        "dlopen_recall": _recall(lambda e: e.dlopen),
        "covered_by_general": covered_by_general,
        "covered_by_curated": covered_by_curated,
        "residual": repos_missed,
    }
