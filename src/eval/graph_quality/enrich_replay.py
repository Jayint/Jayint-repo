"""Enrich replay — the HEADLINE graph-quality metric (design spec §3, plan Task 3).

Replays each real past failure's error text through the REAL
``graph.graph_enrich.enrich`` and scores whether the graph names --
from the error text alone -- a node whose fix matches what the repair actually
added. Pure offline: no Docker, no network, no LLM.

WHY this is the headline: every react turn is a full container rebuild, so a
pre-empted discovery hop is a rebuild saved. Near-zero pre-emption means the
enrichment tier does not pay for itself -- and that is a legitimate, reportable
answer, not a bug in this harness.

🔴 THE DENOMINATOR IS TINY. Of the 111 labelled pairs this corpus yields, only a
handful carry an apt/pip label the pre-emption matcher can even score (see
``_PREEMPTABLE``). The rest are ENV-var repairs (their own slice) or source
patches -- ``not-an-env-fix`` -- which are excluded from the denominator but run
through anyway as this eval's NEGATIVE CONTROL (``aggregate``'s
``negative_control`` key). Report raw counts (``k/n``), NEVER a bare rate: with an
n this small, a percentage is a lie with a decimal point in it.

🔴 THE STREAM MATTERS. `enrich` ingests a build failure and a pytest failure through
two mutually exclusive paths. Routing a pytest failure down the build path silently
skips the phase-gated cause ingestion entirely -- the graph is handed NOTHING and
scores a false miss. Roughly half this corpus fails at test time, so getting this
wrong does not add noise, it manufactures a damning result. See ``_replay_enrich``.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.diagnose import RepoContext  # noqa: E402
from graph.discovery_expand import expand_discovery  # noqa: E402
from graph.contracts.executor import CommandResult  # noqa: E402
from graph.graph_enrich import enrich  # noqa: E402
from graph.ids import TEST_NODE_ID, package_id  # noqa: E402
from graph.python.util.import_mapping import normalize_package_name  # noqa: E402
from graph.model import (  # noqa: E402
    DepGraph, DiscoveredBy, EdgeType, Layer, Node, NodeType, State,
)

from src.eval.graph_quality.corpus import (  # noqa: E402
    _APT_RE, _PIP_RE, _shell_payloads, Pair, dockerfile_installs, load_pairs,
)
from src.agent.loop import RunResult  # noqa: E402
from src.agent.pytest_summary import summarize  # noqa: E402

_DEFAULT_RESULTS_DIR = "outputs/repo2run_benchmark/results"
_DEFAULT_ARTIFACTS_DIR = "outputs/repo2run_benchmark/eval_artifacts"


@dataclass(frozen=True)
class PairScore:
    repo: str
    kind: str
    discovered: frozenset[str]
    preempted: bool
    hallucinated: frozenset[str]
    owner_anchored: bool
    # Extra bookkeeping beyond the plan's fixed interface (additive, defaulted --
    # does not disturb positional construction of the five required fields).
    # A discovered TOOL/SystemLib whose apt fix genuinely requires the apt-file
    # container fallback (a network/container call this harness refuses to fake
    # the answer to) rather than the offline PROVIDER_TABLE. Keeping this SEPARATE
    # from `preempted=False` is the point: "the graph never discovered it" and
    # "the offline resolver could not be reached" would otherwise look identical.
    capability_unresolved: frozenset[str] = frozenset()


# --------------------------------------------------------------------------- #
# Offline executor -- HARD CONSTRAINT: no Docker, no network, no LLM.
# --------------------------------------------------------------------------- #

class _OfflineExecutor:
    """Deterministic, non-networked stand-in for the `Executor` protocol.

    `os_resolver.resolve()` (and `build_deps.seed_build_deps_for`) both try a
    static, offline TABLE lookup FIRST and only fall back to a live apt-file /
    container probe on a miss. This executor makes that fallback fail HONESTLY --
    a clean, deterministic rc=127 -- rather than silently no-op or crash. That
    keeps a table-miss visibly attributable to "the resolver could not be
    reached", never conflated with "the graph never discovered the need" (see
    `PairScore.capability_unresolved`).
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        self.calls.append(command)
        return CommandResult(
            command=command, returncode=127, stdout="",
            stderr="offline replay: no live container/network available",
        )


# --------------------------------------------------------------------------- #
# Seeding + the (best-effort, never-guessed-past-ambiguity) failing command
# --------------------------------------------------------------------------- #

# Buildkit's own verdict line on a build-time RUN failure names the EXACT shell
# command that failed -- real signal, not a guess. Present only when the
# corpus's raw stderr came from `docker_build.stderr` (i.e. the Docker build
# itself failed); a pytest-phase-only failure (build green, tests red) carries
# no such marker, per corpus.py's `_failure_text`.
_PROCESS_FAILED_RE = re.compile(r'process "(.*?)" did not complete successfully')
_SH_WRAP_RE = re.compile(r"^/bin/sh\s+-l?c\s+", re.IGNORECASE)


def _extract_failing_command(stderr: str) -> str | None:
    """The literal shell command buildkit reports as the one that failed, or
    None when the corpus's stderr carries no such marker (test-phase failures)."""
    matches = _PROCESS_FAILED_RE.findall(stderr or "")
    if not matches:
        return None
    outer = _SH_WRAP_RE.sub("", matches[-1], count=1)
    # The real install may be buried one level deeper in a JAYINT retry loop's
    # `/bin/sh -lc '<cmd>'` payload -- the same shape corpus.py's own
    # `_shell_payloads` already unwraps. Prefer that inner command when present.
    for payload in _shell_payloads(outer):
        if _PIP_RE.search(payload) or _APT_RE.search(payload):
            return payload
    return outer


def _seed_graph_and_command(before: str, stderr: str) -> tuple[DepGraph, str]:
    """A graph seeded from `before`'s declared pip packages (so
    `owner_node_for_command` has an owner to anchor at -- plan Task 3 Step 2 item 1)
    plus the command to hand `enrich` as `result.failing_command`.

    The command is the REAL buildkit-reported failing command when the corpus's
    stderr carries one (see `_extract_failing_command`); otherwise, when `before`
    declares EXACTLY one pip package, a synthetic `pip install <name>` (unpinned --
    `owner_node_for_command`'s unpinned path resolves a lone name-match candidate
    without needing a version). With zero or multiple declared packages and no
    real marker, there is no honest way to guess which one command failed, so a
    neutral placeholder is used that matches neither the apt nor the pip pattern
    -- `owner_node_for_command` then legitimately returns None and the discovery
    falls back to TEST_NODE_ID, exactly as the real system does for a genuine
    pytest-stream failure (`graph_enrich.enrich`'s own docstring: "pytest output
    -> owner is TEST_NODE_ID ... which is CORRECT").
    """
    _apt_before, pip_before = dockerfile_installs(before)
    graph = DepGraph().with_node(Node(
        id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL,
    ))
    for name in sorted(pip_before):
        graph = graph.with_node(Node(
            id=package_id(name, None), type=NodeType.PACKAGE, name=name,
            layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
            state=State.MISSING, build_from_source=True,
        ))

    command = _extract_failing_command(stderr)
    if command is None:
        if len(pip_before) == 1:
            command = f"pip install {next(iter(pip_before))}"
        else:
            command = "RUN build step"  # matches neither the apt nor pip pattern
    return graph, command


# --------------------------------------------------------------------------- #
# score_pair -- the grader
# --------------------------------------------------------------------------- #

def _fix_name(node: Node) -> str:
    """Bare provider name a resolved fix would install (`apt:libpq-dev` -> `libpq-dev`)."""
    if node.chosen_fix:
        return node.chosen_fix.split(":", 1)[-1]
    return node.name or ""


def _final_dockerfile_text(pair: Pair, artifacts_dir: str) -> str:
    """The best real reference for "what did this repo actually end up needing":
    the corpus's `Dockerfile.eval` (the final, working Dockerfile -- present for
    377/420 repos) when available, else this round's own `after` (still a real,
    working Dockerfile, just not necessarily the repo's LAST one)."""
    path = Path(artifacts_dir) / pair.repo / "Dockerfile.eval"
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return pair.after


def _replay_enrich(pair: Pair, graph: DepGraph, command: str) -> tuple[DepGraph, list[str]]:
    """Drive `enrich` through the SAME stream production would have used for this failure.

    `enrich` has two mutually exclusive inputs, and its own docstring is explicit that a turn is
    "either build-stream (`causes` empty) or pytest-stream (`result.ok` true, `causes`
    populated)":

      * a BUILD failure  -> RunResult(ok=False, failing_command=...) and NO causes. The owner is
        resolved exactly from the failing command; this is where DEPTH comes from.
      * a TEST failure   -> RunResult(ok=True) and causes=summarize(pytest output). The owner is
        TEST_NODE_ID, and the causes are PHASE-GATED (only collect/setup reach the graph).

    Feeding a pytest failure down the build path -- which is what this grader did before review
    -- means the phase-gated cause ingestion at `graph_enrich.py:120` never fires at all. The
    graph is handed nothing and scores a false miss. That is not a small bias: roughly half this
    corpus fails at test time, and it under-counted at least one real pre-emption (DTrOCR's
    Pillow, which the production path discovers cleanly).
    """
    if pair.failed_at == "test":
        return enrich(graph, RunResult(ok=True), summarize(pair.stderr), RepoContext())
    result = RunResult(ok=False, failing_command=command, output=pair.stderr)
    return enrich(graph, result, [], RepoContext())


def score_pair(pair: Pair, *, artifacts_dir: str = _DEFAULT_ARTIFACTS_DIR) -> PairScore:
    """Replay `pair.stderr` through the REAL `enrich()` + `expand_discovery()` and
    grade what came out against the label (plan Task 3 Step 2)."""
    graph, command = _seed_graph_and_command(pair.before, pair.stderr)
    graph, new_ids = _replay_enrich(pair, graph, command)

    executor = _OfflineExecutor()
    graph, _expanded = expand_discovery(graph, new_ids, executor)

    discovered = frozenset(new_ids)
    label_pip_norm = {normalize_package_name(p) for p in pair.label.pip}

    preempted = False
    capability_unresolved: set[str] = set()
    for node_id in discovered:
        node = graph.get(node_id)
        if node is None:
            continue
        if node.type is NodeType.PACKAGE:
            if node.name and normalize_package_name(node.name) in label_pip_norm:
                preempted = True
        elif node.type in (NodeType.TOOL, NodeType.SYSTEM_LIB):
            if node.chosen_fix:
                if _fix_name(node) in pair.label.apt:
                    preempted = True
            else:
                capability_unresolved.add(node_id)

    owner_anchored = any(
        e.relation is EdgeType.REQUIRES and e.dst in discovered and e.src.startswith("pkg:")
        for e in graph.edges
    )

    final_text = _final_dockerfile_text(pair, artifacts_dir)
    final_apt, final_pip = dockerfile_installs(final_text)
    final_pip_norm = {normalize_package_name(p) for p in final_pip}
    hallucinated: set[str] = set()
    for node_id in discovered:
        node = graph.get(node_id)
        if node is None:
            continue
        if node.type is NodeType.PACKAGE:
            if not (node.name and normalize_package_name(node.name) in final_pip_norm):
                hallucinated.add(node_id)
        elif node.type in (NodeType.TOOL, NodeType.SYSTEM_LIB):
            # No chosen_fix -> capability_unresolved, not scored as a hallucination
            # (we don't know what it would have installed, so we can't say it was
            # never needed).
            if node.chosen_fix and _fix_name(node) not in final_apt:
                hallucinated.add(node_id)

    return PairScore(
        repo=pair.repo, kind=pair.label.kind, discovered=discovered,
        preempted=preempted, hallucinated=frozenset(hallucinated),
        owner_anchored=owner_anchored,
        capability_unresolved=frozenset(capability_unresolved),
    )


# --------------------------------------------------------------------------- #
# aggregate -- PER-SLICE only, never a bare aggregate (Global Constraints).
# --------------------------------------------------------------------------- #

# The only kinds whose LABEL is an apt/pip package name -- i.e. the only kinds the pre-emption
# matcher can even score. `env-var` repairs ARE environment fixes (the graph models them as
# Config nodes), but their label is an ENV name, not a package, so folding them into the
# pre-emption denominator would count a structurally-unscorable pair as a miss. They get their
# own slice instead. `not-an-env-fix` is the negative control.
_PREEMPTABLE = frozenset({"os-package", "python-package"})


def aggregate(scores: Sequence[PairScore]) -> dict:
    kinds = sorted({s.kind for s in scores})
    by_kind: dict[str, dict] = {}
    for kind in kinds:
        subset = [s for s in scores if s.kind == kind]
        n = len(subset)
        in_scope = kind in _PREEMPTABLE
        n_discovered = sum(1 for s in subset if s.discovered)
        n_preempted = sum(1 for s in subset if s.preempted)
        n_hallucinated = sum(1 for s in subset if s.hallucinated)
        n_owner_anchored = sum(1 for s in subset if s.owner_anchored)
        n_capability_unresolved = sum(1 for s in subset if s.capability_unresolved)
        entry = {
            "n": n,
            "attribution_coverage": f"{n_discovered}/{n}",
            "hallucination_count": f"{n_hallucinated}/{n}",
            "owner_anchoring_rate": f"{n_owner_anchored}/{n}",
            "capability_unresolved_count": f"{n_capability_unresolved}/{n}",
        }
        if in_scope:
            entry["preemption_rate"] = f"{n_preempted}/{n}"
        elif kind == "env-var":
            entry["preemption_rate"] = None
            entry["note"] = (
                "EXCLUDED from the pre-emption denominator, but NOT a miss: the repair here set "
                "an ENV var, which the graph models as a Config node, not an apt/pip fix -- the "
                "matcher is structurally unable to score it. `attribution_coverage` is the "
                "meaningful number for this slice."
            )
        else:
            entry["preemption_rate"] = None
            entry["note"] = (
                "not-an-env-fix is EXCLUDED from the pre-emption denominator; see "
                "top-level `negative_control` for its false-positive count."
            )
        by_kind[kind] = entry

    in_scope_scores = [s for s in scores if s.kind in _PREEMPTABLE]
    not_env_scores = [s for s in scores if s.kind == "not-an-env-fix"]
    n_in_scope = len(in_scope_scores)
    n_preempted_total = sum(1 for s in in_scope_scores if s.preempted)

    negative_control = {
        "n": len(not_env_scores),
        "produced_a_node": sum(1 for s in not_env_scores if s.discovered),
        "note": (
            "a node here is a FALSE POSITIVE: these repairs are source patches (a conftest.py "
            "stub, a bespoke build recipe), so there was no environment fact to find. Note this "
            "number was inflated before review, when ENV-var repairs were miscounted into this "
            "bucket and the graph's CORRECT Config discoveries were scored as false positives; "
            "they now have their own `env-var` slice."
        ),
    }

    return {
        "n_total": len(scores),
        "n_in_scope": n_in_scope,
        "preemption_rate_in_scope": f"{n_preempted_total}/{n_in_scope}" if n_in_scope else "0/0",
        "denominator_warning": (
            f"in-scope denominator is {n_in_scope} -- too small to support a rate; "
            "raw counts only, per pair."
        ),
        "by_kind": by_kind,
        "negative_control": negative_control,
    }


# --------------------------------------------------------------------------- #
# --smoke: run the grader over the REAL corpus and print the honest table.
# --------------------------------------------------------------------------- #

def _print_report(agg: dict) -> None:
    print(f"n_total={agg['n_total']}  n_in_scope={agg['n_in_scope']}")
    print(agg["denominator_warning"])
    print(f"preemption (in-scope, ALL kinds combined): {agg['preemption_rate_in_scope']}")
    print()
    for kind, entry in agg["by_kind"].items():
        print(f"-- {kind} (n={entry['n']}) --")
        print(f"   attribution_coverage:        {entry['attribution_coverage']}")
        print(f"   preemption_rate:             {entry['preemption_rate']}")
        print(f"   hallucination_count:         {entry['hallucination_count']}")
        print(f"   owner_anchoring_rate:        {entry['owner_anchoring_rate']}")
        print(f"   capability_unresolved_count: {entry['capability_unresolved_count']}")
        if entry.get("note"):
            print(f"   note: {entry['note']}")
        print()
    nc = agg["negative_control"]
    print(f"negative control (not-an-env-fix, n={nc['n']}): produced_a_node={nc['produced_a_node']}")
    print(f"   {nc['note']}")


def _smoke(results_dir: str, artifacts_dir: str) -> None:
    pairs = load_pairs(results_dir, artifacts_dir)
    scores = [score_pair(p, artifacts_dir=artifacts_dir) for p in pairs]
    agg = aggregate(scores)
    _print_report(agg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true", help="run over the real corpus and report")
    ap.add_argument("--results-dir", default=_DEFAULT_RESULTS_DIR)
    ap.add_argument("--artifacts-dir", default=_DEFAULT_ARTIFACTS_DIR)
    args = ap.parse_args(argv)

    if args.smoke:
        _smoke(args.results_dir, args.artifacts_dir)
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
