"""Patch localization -- is the ★ on the RIGHT node? (design spec §4, plan Task 4)

THE QUESTION: the graph's *render* is the product the agent actually sees. Does the
★ (ACTIONABLE marker) `render_graph_context` emits land on the node whose fix is the
one that actually matters? A graph that KNOWS the answer but buries it among twelve
★s is a failed product -- root-hit@1 alone would score that graph 100%.

🔴 STAR PRECISION IS MANDATORY ALONGSIDE root-hit. This is the whole point of the
task and it is non-negotiable: every table this module prints reports BOTH, plus the
raw `n_stars`, never root-hit in isolation.

GRADE THE RENDER, NOT AN AGENT. Deterministic, no LLM, no Docker:

    injected fault -> the resulting error -> a hand-built graph reflecting what
    construction+enrich WOULD have certified -> the REAL `render_graph_context`
    -> parse the ★ ids back OUT of the rendered text -> compare to `correct_action`.

REUSE, DO NOT REBUILD. `src/eval/graph_repair_ablation/oracle.py` already carries the
labels this grader needs: `Injection.correct_action = {"kind": "install"|"drop"|"repin",
"target": ...}` across the 5 `FAILURE_CLASSES`. That harness's OWN pipeline
(`run.py::run_one`) builds its graph via `build_graph_construction_only`, which opens a
`DockerExecutor` -- Docker is out of scope here (Global Constraints: OFFLINE). So this
module hand-builds, for each of the 5 real `PILOT_INJECTIONS`, a graph+result pair that
is a best-effort reconstruction of what that specific known failure would have produced,
built from three things that are NOT invented: (a) the oracle's own `correct_action` and
`note`, (b) `python_deps.depgraph.os_resolver.PROVIDER_TABLE` (verified on disk -- see
`_scenario_compiler_pyzmq`'s comment) for which capabilities the offline resolver
actually knows, and (c) `graph_context.py`'s own documented rules for `blocks()`/
`verdict()`. This is the SAME stance Task 2's `corpus.py` took for its parser tests:
"hand-built fixtures are right here, because we are testing the RENDER, not the whole
system." It is explicitly NOT a replacement for Task 3's `enrich_replay` (which measures
whether the discovery/resolution layer finds the right thing from raw error text) --
conflating the two is exactly the failure mode design §1 warns against ("one headline
number that hides the only case anybody cares about").

🔴 THERE ARE ONLY 5 INJECTION ENTRIES -- one per `FAILURE_CLASSES` member. Five cells is
a SMOKE TEST, not a measurement (plan's own words). Every report this module prints says
so; never read these numbers as a rate or a percentage.

A DISCOVERY MADE WHILE BUILDING THESE FIXTURES, reported (not fixed) per Global
Constraints: `os_resolver.PROVIDER_TABLE` has no entry for `("soname", "libcgraph.so.6")`
or `("binary", "git")` -- only `("binary", "gcc")` -> `build-essential` is covered among
this module's three TOOL/SystemLib scenarios. Had this grader run these two capabilities
through the REAL offline resolver (`os_resolver.resolve`, executor=None) instead of
hand-setting `chosen_fix`, both would come back UNRESOLVED (`[]`, a table miss with no
container to fall back to) -- a genuine PROVIDER_TABLE coverage gap, surfaced as a
side-effect of this work, not something this module patches.
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

from python_deps.depgraph.graph_context import in_conflict, render_graph_context  # noqa: E402
from python_deps.depgraph.ids import TEST_NODE_ID, binary_id, package_id, syslib_id  # noqa: E402
from python_deps.depgraph.naming import normalize_package_name  # noqa: E402
from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)
from python_deps.depgraph.syslib import make_syslib_node  # noqa: E402

from src.eval.graph_repair_ablation.oracle import (  # noqa: E402
    FAILURE_CLASSES, Injection, PILOT_INJECTIONS,
)
from src.react_repair.loop import RunResult  # noqa: E402
from src.react_repair.pytest_summary import Cause  # noqa: E402


@dataclass(frozen=True)
class LocalizeScore:
    root_hit: bool          # is `correct_action.target` reachable from the ★ set at all?
    star_precision: float   # |★ that hit the target| / |★| -- 0.0 when n_stars == 0
    n_stars: int            # raw count -- the denominator star_precision hides
    mislocalized: bool      # a CONFLICTS_WITH node ended up in the ★ set (never should)


# --------------------------------------------------------------------------- #
# stars() -- parse the ★ ids back OUT of the REAL rendered text.
# --------------------------------------------------------------------------- #

# Only the RECORD header line (`★ <id>    STATE`, column 0 -- see graph_context.py's
# `_record`) is matched. `_down_edges` ALSO appends an inline trailing "  ★" to a
# SUBGRAPH edge line for the same ACTIONABLE dst (`_fmt_state(dst)}{mark}`) -- that
# annotation has no leading "★ <id>" shape (the id sits earlier on the line, before
# "--requires-->") and matching it too would double-count a node that is already a
# full record. Every node that gets the inline mark also gets a full record (both come
# from the same ACTIONABLE check on the same `follow` list), so parsing only the
# records loses no information.
_STAR_LINE_RE = re.compile(r"^★ (\S+)", re.MULTILINE)


def stars(graph: DepGraph, result, causes, *, prev_states: dict | None = None,
          repo_path: str | None = None, target_env: dict | None = None) -> frozenset[str]:
    """Render via the REAL `render_graph_context` and return the set of node ids that
    got a full ★ record. `records` inside the renderer is a dict keyed by node id
    (`records.setdefault(cand.id, cand)`) -- so when two independent failures share one
    root, the render ALREADY collapses them onto one entry before this function ever
    sees the text; `stars()` does not need to (and must not) do any de-duplication of
    its own for that to hold."""
    text = render_graph_context(graph, result, causes, prev_states or {},
                                repo_path=repo_path, target_env=target_env)
    return frozenset(_STAR_LINE_RE.findall(text))


# --------------------------------------------------------------------------- #
# grade_stars() -- pure grading arithmetic, independent of the render pipeline so the
# FORMULA itself (and its conflict safety-net) is unit-testable on its own.
# --------------------------------------------------------------------------- #

def _node_hits_target(node: Node, target: str) -> bool:
    """Does `node` correspond to `correct_action["target"]`?

    An install-class target names a PROVIDER id (`"apt:libpq-dev"`) -- `node.chosen_fix`
    is the strongest signal, since it is literally the fix that node would emit. A
    repin/drop-class target names a bare PACKAGE name (`"urllib3"`) -- matched against
    the node's own canonical name for the (frequent, but not universal -- see `gcc` vs
    `build-essential`) case where a capability's own name coincides with its target.
    """
    if node.chosen_fix is not None and node.chosen_fix == target:
        return True
    bare = target.split(":", 1)[-1]
    return bool(node.name) and normalize_package_name(node.name) == normalize_package_name(bare)


def grade_stars(stars: frozenset[str], target: str, graph: DepGraph) -> LocalizeScore:
    """Score an ALREADY-EXTRACTED star set against `target`.

    `star_precision` is 0.0 (never a ZeroDivisionError, never `None`-as-silence) when
    `n_stars == 0` -- an honest reading of "the render offered no actionable signal at
    all" for a repin/drop-class injection where the render's OWN invariant (a
    CONFLICTS_WITH node renders ✖, never ★) makes an empty star set the CORRECT, not
    the failing, outcome. `root_hit` is likewise `False`, never crashes, on an empty set.
    """
    n = len(stars)
    star_nodes = [(sid, graph.get(sid)) for sid in stars]
    hits = {sid for sid, node in star_nodes if node is not None and _node_hits_target(node, target)}
    mislocalized = any(node is not None and in_conflict(graph, node) for _sid, node in star_nodes)
    return LocalizeScore(
        root_hit=bool(hits),
        star_precision=(len(hits) / n) if n else 0.0,
        n_stars=n,
        mislocalized=mislocalized,
    )


def grade(inj: Injection, graph: DepGraph, result, causes: Sequence = ()) -> LocalizeScore:
    """injected fault -> render -> ★ set -> compare to `inj.correct_action["target"]`
    (spec §4.2). `causes` is additive beyond the plan's literal 3-arg signature
    (defaulted to `()`, matching a build-stream call exactly as specified) -- two of
    the 5 real injections are genuinely pytest-stream failures (see the module
    docstring's scenario builders) and `render_graph_context` needs them passed
    through, not silently dropped."""
    star_ids = stars(graph, result, causes)
    return grade_stars(star_ids, inj.correct_action["target"], graph)


# --------------------------------------------------------------------------- #
# Hand-built (graph, result, causes) scenarios for the 5 real PILOT_INJECTIONS.
#
# Each one is derived from that injection's own `note` (graph_repair_ablation/
# oracle.py) plus verified facts about the current offline resolver -- see the
# module docstring's "DISCOVERY MADE WHILE BUILDING THESE FIXTURES" paragraph for
# where a hand-set `chosen_fix` stands in for a resolver call the offline
# PROVIDER_TABLE cannot currently answer.
# --------------------------------------------------------------------------- #

def _base_graph() -> DepGraph:
    return DepGraph().with_node(Node(
        id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL,
    ))


def _scenario_syslib_pygraphviz() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """oracle note: "strip the graphviz -dev apt line -> import fails on libcgraph.so".
    pygraphviz's own pip install succeeds (state=SATISFIED); the failure surfaces at
    IMPORT time as a pytest collection error naming the package -- exactly the
    `os_resolver.PROVIDER_TABLE` gap the module docstring flags: this repo's real
    resolver has no `("soname", "libcgraph.so.6")` entry, so `chosen_fix` here stands
    in for what a COMPLETE resolver would find, not what THIS one currently does."""
    graph = (_base_graph()
             .with_node(Node(id=package_id("pygraphviz", "1.11"), type=NodeType.PACKAGE,
                             name="pygraphviz", layer=Layer.PIP,
                             discovered_by=DiscoveredBy.STATIC_SCAN, version="1.11",
                             state=State.SATISFIED, build_from_source=True))
             .with_node(make_syslib_node(
                 "libcgraph.so.6", discovered_by=DiscoveredBy.RUNTIME, state=State.MISSING,
                 apt="libgraphviz-dev",
                 evidence="ImportError: libcgraph.so.6: cannot open shared object file: "
                          "No such file or directory",
                 provenance="runtime ingest (pytest collection)"))
             .with_edge(Edge(src=package_id("pygraphviz", "1.11"), dst=syslib_id("libcgraph.so.6"),
                             relation=EdgeType.REQUIRES, origin="resolver", data={"hard": True})))
    causes = (Cause(
        exc="ImportError",
        detail="'pygraphviz' failed: libcgraph.so.6: cannot open shared object file",
        count=6, outcome="ERROR", module="tests/test_render.py", phase="collect",
    ),)
    return graph, RunResult(ok=True), causes


def _scenario_compiler_pyzmq() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """oracle note: "strip build-essential -> native build 'gcc failed'". A genuine
    BUILD-time `pip install` failure. Unlike the other two capability scenarios,
    `os_resolver.PROVIDER_TABLE` DOES carry `("binary", "gcc") -> "build-essential"`
    (verified on disk) -- so `discovered_by=RESOLVER` (a construction-time prediction,
    matching `build_deps._capability_node`'s own convention) is not just plausible, it
    is what the REAL offline table would already have found before any failure."""
    graph = (_base_graph()
             .with_node(Node(id=package_id("pyzmq", "25.1.2"), type=NodeType.PACKAGE,
                             name="pyzmq", layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                             version="25.1.2", state=State.MISSING, build_from_source=True))
             .with_node(Node(id=binary_id("gcc"), type=NodeType.TOOL, name="gcc",
                             layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RESOLVER,
                             state=State.MISSING, check_command="command -v gcc",
                             chosen_fix="apt:build-essential", fix_candidates=("apt:build-essential",),
                             provenance="curated build-dep prediction"))
             .with_edge(Edge(src=package_id("pyzmq", "25.1.2"), dst=binary_id("gcc"),
                             relation=EdgeType.REQUIRES, origin="resolver", data={"hard": True})))
    result = RunResult(ok=False, failing_command="pip install pyzmq==25.1.2",
                       output="error: command 'gcc' failed: No such file or directory\n"
                              "/bin/sh: 1: gcc: not found\n")
    return graph, result, ()


def _scenario_conflict_requests() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """oracle note: "append an incompatible urllib3 pin -> resolver/runtime conflict".
    Modeled at RESOLVE time (a "uv unsat core", per `graph_context.py`'s own docstring
    for `in_conflict`) rather than as a successfully-installed-but-incompatible pair:
    the injected pin never resolves to an installable plan, so `urllib3==1.20` stays
    MISSING, not SATISFIED -- verdict() checks SATISFIED before it ever checks conflict,
    so a SATISFIED node here would hide the conflict entirely, which is not what a real
    unsat resolve looks like."""
    graph = (_base_graph()
             .with_node(Node(id=package_id("requests", "2.31.0"), type=NodeType.PACKAGE,
                             name="requests", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                             version="2.31.0", state=State.SATISFIED, build_from_source=False))
             .with_node(Node(id=package_id("urllib3", "1.20"), type=NodeType.PACKAGE,
                             name="urllib3", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                             version="1.20", state=State.MISSING, build_from_source=False))
             .with_edge(Edge(src=package_id("urllib3", "1.20"), dst=package_id("requests", "2.31.0"),
                             relation=EdgeType.CONFLICTS_WITH, origin="resolver",
                             data={"summary": "requests 2.31.0 requires urllib3>=1.21.1,<3"})))
    result = RunResult(
        ok=False, failing_command="pip install urllib3==1.20",
        output="ERROR: pip's dependency resolver does not currently take into account all "
               "the packages that are installed. This behaviour is the source of the "
               "following dependency conflicts.\n"
               "requests 2.31.0 requires urllib3>=1.21.1,<3, but you have urllib3 1.20 "
               "which is incompatible.\n",
    )
    return graph, result, ()


def _scenario_overinclude_dotenv() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """oracle note: "append an unbuildable OPTIONAL dep -> install fails; correct
    action is DROP". Per `inject.apply_injection`'s `add_install_pkg` op, this line is
    appended straight to the ALREADY-RENDERED script text -- it was never part of the
    graph construction ran against. So the honest graph here carries NO node for the
    phantom package at all: `owner_node_for_command` will find none, and the render's
    own graceful-degradation path ("BUILD FAILED -- NO GRAPH EXPLANATION") is what
    actually fires. This is not a gap in this fixture; it is what the real system does
    with a dependency it was never told about."""
    graph = _base_graph().with_node(Node(
        id=package_id("python-dotenv", "1.0.1"), type=NodeType.PACKAGE, name="python-dotenv",
        layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN, version="1.0.1",
        state=State.SATISFIED, build_from_source=False,
    ))
    result = RunResult(
        ok=False, failing_command="pip install this-optional-pkg-fails-to-build==0.0.0",
        output="ERROR: Could not find a version that satisfies the requirement "
               "this-optional-pkg-fails-to-build==0.0.0 (from versions: none)\n"
               "ERROR: No matching distribution found for "
               "this-optional-pkg-fails-to-build==0.0.0\n",
    )
    return graph, result, ()


def _scenario_tool_semrel() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """oracle note: "strip git -> GitPython GIT_PYTHON refresh error". GitPython's own
    wheel installs fine (state=SATISFIED); the failure is GitPython refusing to import
    without a `git` binary on PATH, surfacing as a pytest setup-phase error. Like
    `libcgraph.so.6` above, `os_resolver.PROVIDER_TABLE` has no `("binary", "git")`
    entry -- `discovered_by=RUNTIME` reflects that this fix comes from the failure
    itself, not a construction-time prediction."""
    graph = (_base_graph()
             .with_node(Node(id=package_id("GitPython", "3.1.43"), type=NodeType.PACKAGE,
                             name="GitPython", layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                             version="3.1.43", state=State.SATISFIED, build_from_source=False))
             .with_node(Node(id=binary_id("git"), type=NodeType.TOOL, name="git",
                             layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                             state=State.MISSING, check_command="command -v git",
                             chosen_fix="apt:git", fix_candidates=("apt:git",),
                             evidence="GitCommandNotFound: Bad git executable. The git "
                                      "executable must be specified in an environment "
                                      "variable, and setup in your PATH, or explicitly set "
                                      "via git.refresh().",
                             provenance="runtime ingest (pytest setup)"))
             .with_edge(Edge(src=package_id("GitPython", "3.1.43"), dst=binary_id("git"),
                             relation=EdgeType.REQUIRES, origin="resolver", data={"hard": True})))
    causes = (Cause(
        exc="GitCommandNotFound",
        detail="'GitPython' requires the git executable but none was found on PATH",
        count=8, outcome="ERROR", module="tests/test_release.py", phase="setup",
    ),)
    return graph, RunResult(ok=True), causes


_SCENARIO_BUILDERS = {
    "syslib_pygraphviz": _scenario_syslib_pygraphviz,
    "compiler_pyzmq": _scenario_compiler_pyzmq,
    "conflict_requests": _scenario_conflict_requests,
    "overinclude_dotenv": _scenario_overinclude_dotenv,
    "tool_semrel": _scenario_tool_semrel,
}


def scenario_for(inj: Injection) -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """(graph, result, causes) for `inj` -- see the builders above for provenance."""
    try:
        builder = _SCENARIO_BUILDERS[inj.injection_id]
    except KeyError as exc:
        raise ValueError(f"no hand-built scenario for injection {inj.injection_id!r}") from exc
    return builder()


# --------------------------------------------------------------------------- #
# Report -- per failure class, root_hit AND star_precision AND n_stars, always.
# 🔴 n=5: ONE per FAILURE_CLASSES member. A smoke test, never a rate.
# --------------------------------------------------------------------------- #

def build_report(injections: Sequence[Injection] = PILOT_INJECTIONS) -> list[dict]:
    rows = []
    for inj in injections:
        graph, result, causes = scenario_for(inj)
        score = grade(inj, graph, result, causes)
        rows.append({
            "injection_id": inj.injection_id,
            "failure_class": inj.failure_class,
            "kind": inj.correct_action["kind"],
            "target": inj.correct_action["target"],
            "root_hit": score.root_hit,
            "star_precision": score.star_precision,
            "n_stars": score.n_stars,
            "mislocalized": score.mislocalized,
        })
    return rows


def _print_report(rows: list[dict]) -> None:
    print(f"n={len(rows)} -- ONE injection per failure class "
          f"({len(FAILURE_CLASSES)} FAILURE_CLASSES total). This is a SMOKE TEST, not a "
          f"rate: do not read any column below as a percentage.")
    print()
    header = (f"{'failure_class':<17} {'kind':<8} {'target':<26} {'root_hit':<9} "
              f"{'star_precision':<15} {'n_stars':<8} {'mislocalized'}")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['failure_class']:<17} {row['kind']:<8} {row['target']:<26} "
              f"{str(row['root_hit']):<9} {row['star_precision']:<15.2f} "
              f"{row['n_stars']:<8} {row['mislocalized']}")


def _smoke() -> None:
    _print_report(build_report())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true",
                     help="grade the 5 hand-built injection scenarios and print the table")
    args = ap.parse_args(argv)

    if args.smoke:
        _smoke()
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
