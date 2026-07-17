"""Block parity — does `blocks()`/`verdict()` mean what `emit` means? (design spec §5,
plan Task 6). Three INDEPENDENT checks, all offline, over the graphs `graph_cache.py`
minted under Docker once (T5).

WHY: `emit._is_emittable` / `emit._toolchain_ready` is the INCUMBENT authority on what
can actually be installed — the build-script renderer already decides this, and if the
react arm's `verdict()` reached a different conclusion we would send the agent to fix
something the renderer would have installed anyway. Every wasted turn is a full
container rebuild. This module is the scaled version of the hand-built parity tests
that already caught a real bug this way (`0d3542c`: a known wheel does not need a
compiler, and `blocks()` once thought otherwise).

🔴 ZERO DIVERGENCES ON CHECK 1 (EMIT PARITY) IS THE PASS BAR. If this module finds a
divergence there, that is a bug IN `src/python_deps/` — report it, do not fix it (this
package is off-limits) and do not narrow the check to make it disappear.

THE THREE CHECKS:

1. `emit_parity_report` — for every node of every cached graph, does `verdict()` agree
   with `emit._is_emittable` / `_toolchain_ready` wherever the two overlap? "Overlap" is
   worked out below (`_emit_parity_for_graph`'s docstring), not guessed: `emit` has
   dimensions `verdict()` structurally cannot see (a resolved `chosen_fix`, a resolved
   `version`, the `MAX_EMIT_ATTEMPTS` backoff), and comparing those would invent a
   disagreement neither function claims to adjudicate. The genuine overlap is (a) the
   CONFLICTS_WITH membership test, computed twice by two independent pieces of code, and
   (b) the toolchain-readiness predicate for PACKAGE nodes, which `blocks()`'s own
   docstring says explicitly mirrors `emit._toolchain_ready`.

2. `check_*` (metamorphic) — mutate one real cached graph and assert `verdict()` (or,
   for the one property that is fundamentally a RENDER decision, the actual rendered
   ★/✖ text) moves the way the spec says it must. No ground truth needed; each property
   maps to a rule already written down in `graph_context.py`.

3. `verdict_ref` / `reference_oracle_report` — a deliberately dumb, independent
   reimplementation of `verdict()` transcribed straight from the design spec's own
   prose/pseudocode (`docs/superpowers/specs/2026-07-11-graph-guided-react-arm-design.md`
   §6.3/§6.4/§6.6), NOT from reading `verdict()`'s body. Cross-checked against the real
   `verdict()` on every node of every cached graph.

   🔴 WHAT IT FOUND, ON THE REAL CORPUS: the shipped `blocks()` is strictly MORE
   PERMISSIVE than the spec that defines it. It blocks ONLY on a SystemLib dependency
   (always) or a Tool dependency (source builds / unknown build mode). EVERY other
   dependency type -- Package, Import, Project, Runtime -- is silently non-blocking
   whatever its state, because they all fall off the end of the function into a bare
   `return False`. The spec's §6.3 pseudocode blocks on ANY hard, marker-true REQUIRES
   edge to an unsatisfied dep.

   For Package->Package this narrowing is deliberate and documented in `blocks()`'s own
   docstring (pip resolves its own dependency tree, and `emit` does not gate on those
   either). For Import/Project/Runtime dependencies it is UNDOCUMENTED, and it has a
   visible consequence in every graph in the corpus: the TEST goal node itself, which
   hard-requires the repo's own `project:` node, comes out ACTIONABLE while that Project
   node is still UNKNOWN. Both are surfaced by `CAUSE_NOTES` and counted per cause --
   NEITHER IS FIXED HERE (`src/python_deps/` is the code under test; a divergence is a
   finding to report).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.emit import (  # noqa: E402
    _conflicted_ids, _is_emittable, _toolchain_ready,
)
from graph.graph_context import (  # noqa: E402
    ACTIONABLE, BLOCKED, SATISFIED_OK, UNCERTIFIED, WAITING, in_conflict, verdict,
)
from graph.ids import binary_id, package_id  # noqa: E402
from graph.schema import (  # noqa: E402
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)

from src.eval.graph_quality.graph_cache import load_graphs  # noqa: E402
from src.eval.graph_quality.patch_localize import stars  # noqa: E402
from src.react_repair.loop import RunResult  # noqa: E402

# --------------------------------------------------------------------------- #
# Check 1 — EMIT PARITY
# --------------------------------------------------------------------------- #

# The only node types `emit._is_emittable` (and hence `partition()`) ever classifies;
# everything else (Test/Project/Import/Runtime/Platform/Service/Config) is structurally
# out of `emit`'s scope, so comparing verdict() to emit there would not be a parity
# check at all -- there is nothing on emit's side to agree or disagree with.
_EMIT_SCOPED_TYPES = (NodeType.PACKAGE, NodeType.SYSTEM_LIB, NodeType.TOOL)


@dataclass(frozen=True)
class Divergence:
    repo: str
    node_id: str
    node_type: str
    rule: str
    verdict_value: str
    detail: str


def _emit_parity_for_graph(repo: str, graph: DepGraph) -> list[Divergence]:
    """The genuine overlap between `verdict()` and `emit`, checked node by node:

    Rule A (`conflict_set_agreement`) -- `emit._conflicted_ids(graph)` and
    `graph_context.in_conflict(graph, node)` are two INDEPENDENT implementations of the
    identical fact ("is this node touched by a CONFLICTS_WITH edge"). They must never
    disagree; if they do, one of the two copies has drifted from the other.

    Rule B (`emittable_implies_actionable`) -- whenever `emit._is_emittable` says a node
    is ready to install RIGHT NOW, `verdict()` must agree it is the actionable frontier
    (★). This is the direction that matters operationally: if emit would silently
    install something the render told the agent was still WAITING/BLOCKED/UNCERTIFIED,
    the two subsystems disagree about the state of the world. The REVERSE (verdict==
    ACTIONABLE but emit refuses) is NOT a divergence -- it is emit's own FRONTIER bucket
    by design (`partition()`: a MISSING, unblocked node that lacks a resolved version or
    a resolved `chosen_fix` is exactly what `partition()` hands to the LLM instead of the
    host). Asserting that direction too would flag emit's routine "this needs the LLM"
    decision as a bug, which it is not.

    Rule C (`toolchain_readiness_parity`) -- restricted to PACKAGE nodes with
    `state is MISSING` and not conflicted (the only population either function's
    toolchain check is defined over in production): `verdict()==WAITING` must be the
    exact negation of `emit._toolchain_ready(graph, node)`. `blocks()`'s own docstring
    says it "mirrors emit._toolchain_ready (emit.py:63-81)" -- this is that claim,
    checked, not assumed.

    Deliberately NOT compared: `node.version` being resolved, `chosen_fix` being set to
    an `apt:`-prefixed string, and the `MAX_EMIT_ATTEMPTS` backoff counter. `verdict()`
    has no notion of any of the three -- they are `emit`'s own mechanical readiness
    gates, not part of the graph's BLOCKING model `verdict()` claims to answer. Folding
    them in would manufacture "divergences" that are not disagreements about what is
    blocked; they are disagreements about whether the HOST or the LLM does the fix,
    which both `emit.py`'s own module docstring and `graph_context.py` treat as a
    downstream routing decision, not part of this contract.

    `target_env` is passed as `None` on BOTH sides: `emit._toolchain_ready` never takes
    one at all (structurally marker-blind), so evaluating `verdict()` with a target_env
    while `emit` has none would compare two different questions and manufacture a
    divergence out of a feature only one side has -- not a real overlap violation.
    """
    out: list[Divergence] = []
    conflicted = _conflicted_ids(graph)
    for node in graph.nodes:
        if node.type not in _EMIT_SCOPED_TYPES:
            continue
        v = verdict(graph, node, None)

        a = node.id in conflicted
        b = in_conflict(graph, node)
        if a != b:
            out.append(Divergence(
                repo, node.id, node.type.value, "conflict_set_agreement", v,
                f"emit._conflicted_ids membership={a} but graph_context.in_conflict={b}",
            ))

        if _is_emittable(graph, node, conflicted) and v != ACTIONABLE:
            out.append(Divergence(
                repo, node.id, node.type.value, "emittable_implies_actionable", v,
                f"emit._is_emittable=True (host would install this NOW) but verdict={v}, "
                "not ACTIONABLE",
            ))

        if node.type is NodeType.PACKAGE and node.state is State.MISSING and not b:
            ready = _toolchain_ready(graph, node)
            waiting = v == WAITING
            if waiting == ready:
                out.append(Divergence(
                    repo, node.id, node.type.value, "toolchain_readiness_parity", v,
                    f"emit._toolchain_ready={ready} but verdict={v} "
                    "(WAITING must be the exact negation of toolchain-ready)",
                ))
    return out


def _rule_coverage(graphs: dict[str, DepGraph]) -> dict:
    """Which blocking RULES did this corpus actually exercise?

    🔴 THIS IS THE MOST IMPORTANT NUMBER IN THE FILE, and it exists because a review caught the
    sweep reporting a proud "0 divergences" that no possible bug could have made nonzero.

    "0 divergences" and "the check examined nothing that could diverge" produce the identical
    output. A parity sweep is only worth its pass bar over the node SHAPES that exercise the
    rules; a corpus with no CONFLICTS_WITH edge cannot possibly catch a conflict bug, however
    many thousands of clean nodes it sweeps. So we count the shapes, publish them next to the
    zero, and name the rules the corpus NEVER TOUCHED -- so a green result can never be read as
    stronger evidence than it is.
    """
    counts = {
        "conflicts_with_edges": 0,
        "missing_syslib_dep": 0,           # a missing Package requiring a missing SystemLib
        "missing_tool_dep_source_build": 0,  # ...requiring a missing Tool, built from source
        "missing_tool_dep_known_wheel": 0,   # ...requiring a missing Tool, but a KNOWN WHEEL
    }
    for g in graphs.values():
        counts["conflicts_with_edges"] += sum(
            1 for e in g.edges if e.relation is EdgeType.CONFLICTS_WITH)
        for node in g.nodes:
            if node.type is not NodeType.PACKAGE or node.state is not State.MISSING:
                continue
            for dep in g.requires_of(node.id):
                if dep.state is not State.MISSING:
                    continue
                if dep.type is NodeType.SYSTEM_LIB:
                    counts["missing_syslib_dep"] += 1
                elif dep.type is NodeType.TOOL:
                    if node.build_from_source is False:
                        counts["missing_tool_dep_known_wheel"] += 1
                    else:
                        counts["missing_tool_dep_source_build"] += 1

    never = sorted(rule for rule, n in counts.items() if n == 0)
    return {
        "shapes_present": counts,
        "rules_NEVER_exercised_by_this_corpus": never,
        "warning": (
            "0 divergences is only as strong as the shapes above. The rules listed in "
            "`rules_NEVER_exercised_by_this_corpus` have NO instance in these 16 graphs: a bug "
            "in them could not have been caught here, no matter how many nodes were swept. "
            "Those rules are covered ONLY by the (synthetic) metamorphic checks."
        ) if never else "every blocking rule has at least one real instance in this corpus.",
    }


def emit_parity_report(graphs: dict[str, DepGraph]) -> dict:
    """Sweep every node of every cached graph. NEVER a bare aggregate: grouped by
    (node_type, rule) so a divergence hiding in one slice cannot be averaged away by
    the other fifteen repos being clean -- AND reported next to `coverage`, which says which
    rules this corpus could even have caught a bug in (see `_rule_coverage`)."""
    all_divs: list[Divergence] = []
    for repo in sorted(graphs):
        all_divs.extend(_emit_parity_for_graph(repo, graphs[repo]))

    by_slice: dict[str, dict] = {}
    for d in all_divs:
        key = f"{d.node_type}/{d.rule}"
        slot = by_slice.setdefault(key, {"count": 0, "nodes": []})
        slot["count"] += 1
        slot["nodes"].append({"repo": d.repo, "node_id": d.node_id, "verdict": d.verdict_value,
                               "detail": d.detail})

    checked_by_type: dict[str, int] = {}
    for g in graphs.values():
        for n in g.nodes:
            if n.type in _EMIT_SCOPED_TYPES:
                checked_by_type[n.type.value] = checked_by_type.get(n.type.value, 0) + 1

    return {
        "total_nodes_checked": sum(checked_by_type.values()),
        "checked_by_node_type": checked_by_type,
        "total_divergences": len(all_divs),
        "by_slice": by_slice,
        "coverage": _rule_coverage(graphs),
    }


# --------------------------------------------------------------------------- #
# Check 2 — METAMORPHIC PROPERTIES
# --------------------------------------------------------------------------- #
# Each `check_*` function MUTATES one real cached graph (never builds one from
# scratch) by layering clearly-synthetic, uniquely-prefixed nodes/edges on top via
# the schema's own immutable `with_node`/`with_edge` -- the base graph's genuine
# nodes/edges are untouched and still present. Only the property under test needs
# controlled preconditions; the corpus's own natural state cannot be relied on to
# contain, say, a MISSING Tool below a MISSING Package in every one of the 16 repos.

_PREFIX = "blockparity-synthetic"


def _pick_base_graph(graphs: dict[str, DepGraph]) -> DepGraph:
    """One real cached graph, deterministically chosen (alphabetically first repo),
    to layer every metamorphic scenario on top of."""
    if not graphs:
        raise ValueError("no cached graphs to mutate -- run graph_cache.mint() first")
    return graphs[sorted(graphs)[0]]


def check_satisfying_root_moves_star_up(base: DepGraph) -> dict:
    """mark the true root SATISFIED -> the ★ moves UP to the next unsatisfied thing.

    parent (Package, MISSING) --requires(hard)--> child (Tool, MISSING). Before: child
    is the root (★, ACTIONABLE), parent WAITS on it. After certifying child SATISFIED:
    child drops off (SATISFIED_OK, no ★), and — nothing else blocking it — parent
    becomes the new ★ (ACTIONABLE). The star moved up one level, exactly as the spec
    requires; it did not just vanish.
    """
    parent_id = package_id(f"{_PREFIX}-root-parent", "1.0")
    child_id = binary_id(f"{_PREFIX}-root-child")
    graph = (
        base
        .with_node(Node(id=parent_id, type=NodeType.PACKAGE, name=f"{_PREFIX}-root-parent",
                         layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                         version="1.0", state=State.MISSING, build_from_source=True))
        .with_node(Node(id=child_id, type=NodeType.TOOL, name=f"{_PREFIX}-root-child",
                         layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                         state=State.MISSING))
        .with_edge(Edge(src=parent_id, dst=child_id, relation=EdgeType.REQUIRES,
                         origin="metamorphic-test", data={"hard": True}))
    )
    before_child = verdict(graph, graph.get(child_id))
    before_parent = verdict(graph, graph.get(parent_id))

    satisfied = graph.with_node(graph.get(child_id).with_state(State.SATISFIED))
    after_child = verdict(satisfied, satisfied.get(child_id))
    after_parent = verdict(satisfied, satisfied.get(parent_id))

    holds = (
        before_child == ACTIONABLE and before_parent == WAITING
        and after_child == SATISFIED_OK and after_parent == ACTIONABLE
    )
    return {
        "property": "satisfying_root_moves_star_up", "holds": holds,
        "before": {"child": before_child, "parent": before_parent},
        "after": {"child": after_child, "parent": after_parent},
    }


def check_conflict_edge_blocks_never_stars(base: DepGraph) -> dict:
    """add a CONFLICTS_WITH edge -> ✖ BLOCKED, never ★.

    Two Package nodes (the only legal CONFLICTS_WITH endpoints per `EDGE_RULES`), both
    MISSING, no edge between them yet: node A is a plain root, ACTIONABLE. Add a
    CONFLICTS_WITH edge and A must become BLOCKED -- never ACTIONABLE, no matter what
    else is true about it.
    """
    a_id = package_id(f"{_PREFIX}-conflict-a", "1.0")
    b_id = package_id(f"{_PREFIX}-conflict-b", "1.0")
    graph = (
        base
        .with_node(Node(id=a_id, type=NodeType.PACKAGE, name=f"{_PREFIX}-conflict-a",
                         layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                         version="1.0", state=State.MISSING, build_from_source=False))
        .with_node(Node(id=b_id, type=NodeType.PACKAGE, name=f"{_PREFIX}-conflict-b",
                         layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                         version="1.0", state=State.MISSING, build_from_source=False))
    )
    before = verdict(graph, graph.get(a_id))

    conflicted = graph.with_edge(Edge(src=a_id, dst=b_id, relation=EdgeType.CONFLICTS_WITH,
                                       origin="metamorphic-test",
                                       data={"summary": "synthetic version conflict"}))
    after = verdict(conflicted, conflicted.get(a_id))

    holds = before == ACTIONABLE and after == BLOCKED
    return {
        "property": "conflict_edge_blocks_never_stars", "holds": holds,
        "before": before, "after": after,
    }


def check_known_wheel_ignores_missing_tool(base: DepGraph) -> dict:
    """set `build_from_source=False` (a known wheel) -> a missing build TOOL below it
    STOPS blocking it.

    parent (Package, MISSING, build_from_source=True) --requires(hard)--> child (Tool,
    MISSING): parent WAITS (a source build needs the compiler). Flip parent to a known
    wheel (`build_from_source=False`) and it must become ACTIONABLE — a wheel dlopens a
    .so but never invokes a compiler, so there is nothing left to wait on. This is
    exactly `emit._toolchain_ready`'s own rule, checked directly against `verdict()`.
    """
    pkg_id = package_id(f"{_PREFIX}-wheel-pkg", "2.0")
    tool_node_id = binary_id(f"{_PREFIX}-wheel-tool")
    graph = (
        base
        .with_node(Node(id=pkg_id, type=NodeType.PACKAGE, name=f"{_PREFIX}-wheel-pkg",
                         layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                         version="2.0", state=State.MISSING, build_from_source=True))
        .with_node(Node(id=tool_node_id, type=NodeType.TOOL, name=f"{_PREFIX}-wheel-tool",
                         layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                         state=State.MISSING))
        .with_edge(Edge(src=pkg_id, dst=tool_node_id, relation=EdgeType.REQUIRES,
                         origin="metamorphic-test", data={"hard": True}))
    )
    before = verdict(graph, graph.get(pkg_id))

    wheel = graph.with_node(replace(graph.get(pkg_id), build_from_source=False))
    after = verdict(wheel, wheel.get(pkg_id))

    holds = before == WAITING and after == ACTIONABLE
    return {
        "property": "known_wheel_ignores_missing_tool", "holds": holds,
        "before": before, "after": after,
    }


def check_unknown_state_never_actionable(base: DepGraph) -> dict:
    """set a node's state to UNKNOWN -> it is never ★ (spec §6.6: "UNKNOWN never
    masquerades as MISSING").

    A plain MISSING, unblocked Package is ACTIONABLE. Recertifying it to UNKNOWN (it
    was never actually checked against the container) must drop it to UNCERTIFIED — a
    root CANDIDATE, never a confirmed one — regardless of anything else about it.
    """
    node_id = package_id(f"{_PREFIX}-unknown-pkg", "1.0")
    graph = base.with_node(Node(
        id=node_id, type=NodeType.PACKAGE, name=f"{_PREFIX}-unknown-pkg",
        layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
        version="1.0", state=State.MISSING, build_from_source=False,
    ))
    before = verdict(graph, graph.get(node_id))

    unknown = graph.with_node(replace(graph.get(node_id), state=State.UNKNOWN))
    after = verdict(unknown, unknown.get(node_id))

    holds = before == ACTIONABLE and after == UNCERTIFIED
    return {
        "property": "unknown_state_never_actionable", "holds": holds,
        "before": before, "after": after,
    }


def check_softened_edge_child_loses_star(base: DepGraph) -> dict:
    """soften an edge (`data["hard"]=False`) -> its child loses its ★.

    This is the one property that is fundamentally a RENDER decision, not a `verdict()`
    one: `verdict(child)` never changes (a node's own verdict depends only on ITS
    outbound edges, never on how an inbound edge is flagged) — it is `_down_edges`
    (`graph_context.py`) that stops marking AND stops following a soft edge's
    destination, so the child never enters the rendered records at all. Reuses
    `patch_localize.stars()` (the real render + the real ★-id extraction) rather than
    re-deriving either.
    """
    parent_id = package_id(f"{_PREFIX}-soften-parent", "3.0")
    child_id = binary_id(f"{_PREFIX}-soften-child")
    graph = (
        base
        .with_node(Node(id=parent_id, type=NodeType.PACKAGE, name=f"{_PREFIX}-soften-parent",
                         layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                         version="3.0", state=State.MISSING, build_from_source=True))
        .with_node(Node(id=child_id, type=NodeType.TOOL, name=f"{_PREFIX}-soften-child",
                         layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                         state=State.MISSING))
        .with_edge(Edge(src=parent_id, dst=child_id, relation=EdgeType.REQUIRES,
                         origin="metamorphic-test", data={"hard": True}))
    )
    result = RunResult(ok=False, failing_command=f"pip install {_PREFIX}-soften-parent==3.0")

    hard_stars = stars(graph, result, ())

    softened_edges = tuple(
        replace(e, data={**dict(e.data), "hard": False}) if (e.src, e.dst) == (parent_id, child_id)
        else e
        for e in graph.edges
    )
    softened = replace(graph, edges=softened_edges)
    soft_stars = stars(softened, result, ())

    holds = (child_id in hard_stars) and (child_id not in soft_stars)
    return {
        "property": "softened_edge_child_loses_star", "holds": holds,
        "before_stars_contains_child": child_id in hard_stars,
        "after_stars_contains_child": child_id in soft_stars,
    }


_METAMORPHIC_CHECKS = (
    check_satisfying_root_moves_star_up,
    check_conflict_edge_blocks_never_stars,
    check_known_wheel_ignores_missing_tool,
    check_unknown_state_never_actionable,
    check_softened_edge_child_loses_star,
)


def run_metamorphic_suite(graphs: dict[str, DepGraph]) -> list[dict]:
    base = _pick_base_graph(graphs)
    return [check(base) for check in _METAMORPHIC_CHECKS]


# --------------------------------------------------------------------------- #
# Check 3 — REFERENCE ORACLE
# --------------------------------------------------------------------------- #

def _marker_holds_ref(edge: Edge, target_env: dict | None) -> bool:
    """Independent, deliberately simple re-derivation: an edge with no marker (or no
    target to evaluate against) is always causal; an unevaluable marker traverses
    conservatively (dropping a real prerequisite is worse than keeping a spurious
    one — the spurious one simply certifies SATISFIED later)."""
    marker = getattr(edge, "marker", None)
    if not marker or target_env is None:
        return True
    try:
        from packaging.markers import Marker
        return bool(Marker(marker).evaluate(target_env))
    except Exception:  # noqa: BLE001 — never crash the oracle; traverse conservatively
        return True


def _blocks_ref(edge: Edge, target_env: dict | None = None) -> bool:
    """Straight transcription of design spec §6.3's `blocks(graph, edge)` pseudocode
    (`docs/superpowers/specs/2026-07-11-graph-guided-react-arm-design.md`):

        if edge.relation is not EdgeType.REQUIRES:  return False   # conflicts aren't prereqs
        if not edge.data.get("hard", True):         return False   # soft = advisory
        if marker_false_for_target(edge):           return False   # not required here
        return True

    Deliberately does NOT special-case the DEPENDENCY's node type (SystemLib / Tool /
    Package). The spec's own §6.3 text does not encode that distinction inside
    `blocks()` at all — only `emit.py`'s and the real `blocks()`'s comments do, as a
    later, undocumented-in-this-spec refinement. If the real `blocks()` disagrees with
    this transcription because of that refinement, that is exactly the class of
    disagreement this oracle exists to surface, not something to fold in here to make
    the two agree.
    """
    if edge.relation is not EdgeType.REQUIRES:
        return False
    if not (edge.data or {}).get("hard", True):
        return False
    if not _marker_holds_ref(edge, target_env):
        return False
    return True


def _in_conflict_ref(graph: DepGraph, node: Node) -> bool:
    return any(
        e.relation is EdgeType.CONFLICTS_WITH and node.id in (e.src, e.dst)
        for e in graph.edges
    )


def verdict_ref(graph: DepGraph, node: Node, target_env: dict | None = None) -> str:
    """A deliberately dumb, slow, independent `verdict()`, transcribed straight from
    the design spec's PROSE:

      * §6.4 ("state is SATISFIED -> NO record") -> the SATISFIED_OK early return.
      * §6.3's `verdict(graph, node)` pseudocode -> the conflict check, then the
        blocking-edge walk.
      * §6.6 ("UNKNOWN never masquerades as MISSING... it may be an ACTIONABLE
        CANDIDATE; it is never presented as a confirmed one") -> the UNCERTIFIED
        branch for any non-MISSING, non-SATISFIED state.

    NOT derived by reading `verdict()`'s or `blocks()`'s actual bodies in
    `graph_context.py` — doing that would just re-encode whatever bug they might
    already share. See `_blocks_ref`'s docstring for the one place this is known, up
    front, to diverge from the shipped implementation, and why that is reported rather
    than silently matched.
    """
    if node.state is State.SATISFIED:
        return SATISFIED_OK
    if _in_conflict_ref(graph, node):
        return BLOCKED
    if node.state is not State.MISSING:
        return UNCERTIFIED
    for edge in graph.edges:
        if edge.src != node.id:
            continue
        if not _blocks_ref(edge, target_env):
            continue
        dep = graph.get(edge.dst)
        if dep is not None and dep.state is not State.SATISFIED:
            return WAITING
    return ACTIONABLE


@dataclass(frozen=True)
class OracleDisagreement:
    repo: str
    node_id: str
    node_type: str
    verdict_value: str
    verdict_ref_value: str
    cause: str


# The two MECHANISMS by which the shipped `blocks()` narrows the spec's §6.3 definition.
# Both are REAL, both were found by running this oracle over the real cached corpus, and
# neither is fixed here (`src/python_deps/` is the code under test).
CAUSE_NON_CAPABILITY_DEP = "non_capability_dependency_exemption"
CAUSE_KNOWN_WHEEL_TOOL = "known_wheel_tool_exemption"
CAUSE_UNATTRIBUTED = "unattributed"

CAUSE_NOTES: dict[str, str] = {
    CAUSE_NON_CAPABILITY_DEP: (
        "the shipped blocks() only ever blocks on a SystemLib (always) or a Tool "
        "(source builds / unknown build mode) dependency. EVERY other dependency type -- "
        "Package, Import, Project, Runtime -- is silently non-blocking, whatever its "
        "state. The design spec's §6.3 pseudocode has NO such narrowing: it blocks on any "
        "hard, marker-true REQUIRES edge to an unsatisfied dep. For Package->Package the "
        "narrowing is deliberate and documented in blocks()'s own docstring (pip resolves "
        "its own tree); for Import/Project/Runtime deps it is UNDOCUMENTED and falls out "
        "of the same `return False` tail."
    ),
    CAUSE_KNOWN_WHEEL_TOOL: (
        "the shipped blocks() exempts a Tool dependency when the owner's "
        "build_from_source is False (a known wheel needs no compiler -- the 0d3542c rule, "
        "and emit._toolchain_ready agrees). The spec's §6.3 pseudocode never reads "
        "build_from_source at all."
    ),
}


def _attribute_cause(graph: DepGraph, node: Node) -> str:
    """MECHANICAL attribution for why `verdict_ref` disagrees with `verdict` on this
    node — a structural read of the node's own outbound edges, not a judgement call.

    A disagreement can only ever be WAITING(ref) vs not-WAITING(real): the two functions
    are identical on the SATISFIED / conflict / UNKNOWN branches (verified by the unit
    tests), so the ONLY place they can part company is the blocking-edge walk, and the
    real one is strictly the more permissive of the two. So: find the hard, unsatisfied
    REQUIRES dep that `_blocks_ref` blocked on and the real `blocks()` waved through, and
    name WHICH of the real function's two narrowings did the waving.

    `"unattributed"` means neither known mechanism explains it — a THIRD, undiscovered
    divergence class. That is worth surfacing loudly rather than papering over with a
    guess, so it gets its own bucket and its own assertion in the test module.
    """
    for edge in graph.edges:
        if edge.src != node.id or edge.relation is not EdgeType.REQUIRES:
            continue
        if not (edge.data or {}).get("hard", True):
            continue
        if not _marker_holds_ref(edge, None):
            continue
        dep = graph.get(edge.dst)
        if dep is None or dep.state is State.SATISFIED:
            continue
        if dep.type is NodeType.SYSTEM_LIB:
            continue                       # both functions block here -- not the culprit
        if dep.type is NodeType.TOOL:
            if node.build_from_source is False:
                return CAUSE_KNOWN_WHEEL_TOOL
            continue                       # both functions block here -- not the culprit
        return CAUSE_NON_CAPABILITY_DEP    # Package / Import / Project / Runtime dep
    return CAUSE_UNATTRIBUTED


def reference_oracle_report(graphs: dict[str, DepGraph]) -> dict:
    """Cross-check `verdict_ref` against the real `verdict()` on every node of every
    cached graph. NEVER a bare aggregate: grouped by cause and by node type."""
    disagreements: list[OracleDisagreement] = []
    total = 0
    for repo in sorted(graphs):
        graph = graphs[repo]
        for node in graph.nodes:
            total += 1
            v = verdict(graph, node, None)
            vref = verdict_ref(graph, node, None)
            if v != vref:
                disagreements.append(OracleDisagreement(
                    repo, node.id, node.type.value, v, vref, _attribute_cause(graph, node),
                ))

    by_cause: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for d in disagreements:
        by_cause[d.cause] = by_cause.get(d.cause, 0) + 1
        by_type[d.node_type] = by_type.get(d.node_type, 0) + 1

    return {
        "total_nodes_checked": total,
        "total_disagreements": len(disagreements),
        "by_cause": by_cause,
        "by_node_type": by_type,
        "cause_notes": {c: CAUSE_NOTES.get(c, "UNEXPLAINED") for c in by_cause},
        "sample": [
            {"repo": d.repo, "node_id": d.node_id, "node_type": d.node_type,
             "verdict": d.verdict_value, "verdict_ref": d.verdict_ref_value, "cause": d.cause}
            for d in disagreements[:40]
        ],
    }


# --------------------------------------------------------------------------- #
# CLI — per-slice tables, never a bare aggregate.
# --------------------------------------------------------------------------- #

def _print_emit_parity(report: dict) -> None:
    print(f"EMIT PARITY -- nodes checked: {report['total_nodes_checked']}, "
          f"divergences: {report['total_divergences']}  (pass bar: 0)")
    if not report["by_slice"]:
        print("  (zero divergences)")
        return
    for slice_key, slot in sorted(report["by_slice"].items()):
        print(f"  {slice_key}: {slot['count']}")
        for n in slot["nodes"][:10]:
            print(f"      {n['repo']}  {n['node_id']}  verdict={n['verdict']}  {n['detail']}")


def _print_metamorphic(rows: list[dict]) -> None:
    print("METAMORPHIC PROPERTIES")
    for row in rows:
        status = "HELD" if row["holds"] else "FAILED"
        print(f"  [{status}] {row['property']}")


def _print_reference_oracle(report: dict) -> None:
    print(f"REFERENCE ORACLE -- nodes checked: {report['total_nodes_checked']}, "
          f"disagreements: {report['total_disagreements']}")
    print(f"  by_node_type: {report['by_node_type']}")
    for cause, count in sorted(report["by_cause"].items()):
        print(f"  cause {cause}: {count}")
        print(f"      {report['cause_notes'].get(cause, 'UNEXPLAINED')}")
    for row in report["sample"][:10]:
        print(f"      {row['repo']}  {row['node_id']}  verdict={row['verdict']} "
              f"verdict_ref={row['verdict_ref']}  cause={row['cause']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit-parity", action="store_true")
    ap.add_argument("--metamorphic", action="store_true")
    ap.add_argument("--reference-oracle", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    graphs = load_graphs()
    if not graphs:
        print("no cached graphs found -- run `python3 -m src.eval.graph_quality.graph_cache "
              "--mint` first")
        return 1

    ran_anything = False
    if args.emit_parity or args.all:
        _print_emit_parity(emit_parity_report(graphs))
        ran_anything = True
    if args.metamorphic or args.all:
        _print_metamorphic(run_metamorphic_suite(graphs))
        ran_anything = True
    if args.reference_oracle or args.all:
        _print_reference_oracle(reference_oracle_report(graphs))
        ran_anything = True

    if not ran_anything:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
