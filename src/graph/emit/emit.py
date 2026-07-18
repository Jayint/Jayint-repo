"""Pure emit core: classify the graph and turn the certified closure into a recipe.

This module is the deterministic counterpart to the LLM recipe loop: it decides
which MISSING nodes the host can install without judgement (EMITTABLE), which
require the LLM (FRONTIER), and emits an ordered, layer-correct recipe for the
emittable set. Pure with respect to its inputs — no Docker, no network, no
subprocess (mirrors probe.py / advise.py). Returns neutral EmitStep objects so
this package keeps its zero dependency on src.envstate (world_model.py:22).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from graph.model import (
    DepGraph,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
    Strength,
)

# Node types the host can directly install. Import/Test/Project/Runtime are
# structural — satisfied via their Package (naming relink) or out of scope here.
_INSTALLABLE: tuple[NodeType, ...] = (
    NodeType.PACKAGE,
    NodeType.SYSTEM_LIB,
    NodeType.TOOL,
)

# Emit attempts are tagged with this sentinel in Attempt.check (set by
# depgraph_live.emit_drain). A node that fails to emit this many times stops being
# emittable and is escalated to the FRONTIER for the LLM — without this, the drain
# re-emits the same doomed closure every cycle (observed: 12 identical attempts).
EMIT_ATTEMPT_TAG = "emit"
MAX_EMIT_ATTEMPTS = 2


def _failed_emit_attempts(node: Node) -> int:
    return sum(
        1 for a in node.attempts
        if a.check == EMIT_ATTEMPT_TAG and a.outcome == "failed"
    )


@dataclass(frozen=True)
class Partition:
    certified: tuple[Node, ...]
    emittable: tuple[Node, ...]
    frontier: tuple[Node, ...]


def _conflicted_ids(graph: DepGraph) -> set[str]:
    """Node ids touched by a conflicts_with edge (uv unsat core) — never emit."""
    ids: set[str] = set()
    for e in graph.edges:
        if e.relation is EdgeType.CONFLICTS_WITH:
            ids.add(e.src)
            ids.add(e.dst)
    return ids


def _toolchain_ready(graph: DepGraph, pkg: Node) -> bool:
    """Native readiness gate. Runtime SystemLib deps must be SATISFIED before ANY
    package emits (a wheel dlopens them too). Build-time Tool deps gate source
    builds and unknown build mode; only a known wheel (build_from_source is False)
    skips them. Soft requires edges never block (invariant #10; mirrors
    schedule._dependencies_satisfied)."""
    for edge in graph.edges:
        if not (edge.src == pkg.id and edge.relation is EdgeType.REQUIRES
                and edge.data.get("hard", True)):
            continue
        dep = graph.get(edge.dst)
        if dep is None or dep.state is State.SATISFIED:
            continue
        if dep.type is NodeType.SYSTEM_LIB:
            return False                               # runtime lib: wheel & sdist
        if dep.type is NodeType.TOOL and pkg.build_from_source is not False:
            return False       # build tool blocks source builds AND unknown build mode;
                               # only a KNOWN wheel (build_from_source is False) skips it
    return True


def _is_emittable(graph: DepGraph, node: Node, conflicted: set[str]) -> bool:
    if node.state is not State.MISSING:
        return False
    if node.id in conflicted:
        return False
    if _failed_emit_attempts(node) >= MAX_EMIT_ATTEMPTS:
        return False               # repeatedly failed to emit -> the LLM's call
    if node.type is NodeType.PACKAGE:
        if not node.version:           # unresolved -> the LLM's call
            return False
        if not _toolchain_ready(graph, node):
            return False               # native/runtime-link deps must certify first
                                        # (wheel OR sdist — a wheel still dlopens libs)
        return True
    if node.type in (NodeType.SYSTEM_LIB, NodeType.TOOL):
        return bool(node.chosen_fix and node.chosen_fix.startswith("apt:"))
    return False


def partition(graph: DepGraph) -> Partition:
    """Classify installable nodes into certified / emittable / frontier."""
    conflicted = _conflicted_ids(graph)
    certified: list[Node] = []
    emittable: list[Node] = []
    frontier: list[Node] = []
    for n in graph.nodes:
        if n.type not in _INSTALLABLE:
            continue
        if n.state is State.SATISFIED:
            certified.append(n)
        elif _is_emittable(graph, n, conflicted):
            emittable.append(n)
        elif n.state is State.MISSING:
            frontier.append(n)
        # UNKNOWN with no decision: neither emitted nor escalated.
    return Partition(tuple(certified), tuple(emittable), tuple(frontier))


def failed_reciped_nodes(graph: DepGraph) -> tuple[Node, ...]:
    """Reciped, host-checkable nodes still MISSING after a drain whose deps are
    SATISFIED — the spec's `isolate`. Excludes CONFIG/SERVICE (advisory) and
    nodes with no check_command (no host stop condition)."""
    from graph.schedule import _dependencies_satisfied
    out = []
    for n in graph.nodes:
        if n.state is not State.MISSING:
            continue
        if not n.check_command:
            continue
        if not _is_reciped(n):
            continue
        if not _dependencies_satisfied(graph, n):
            continue
        out.append(n)
    return tuple(out)


# Bottom-up execution rank (matches certify._LAYER_ORDER / advise._LAYER_RANK).
_LAYER_RANK: dict[Layer, int] = {
    Layer.INTERPRETER: 0,
    Layer.SYSTEM: 1,
    Layer.TOOLCHAIN: 2,
    Layer.PIP: 3,
    Layer.NAMING: 4,
    Layer.RUNTIME: 5,
    Layer.TESTS: 6,
}


def topo_order(graph: DepGraph, nodes: tuple[Node, ...]) -> tuple[Node, ...]:
    """Order ``nodes`` dependency-first (a required node before its dependent).

    Kahn's algorithm over ``requires`` edges restricted to the node set; ties
    broken by (layer rank, name) for reproducibility. On a cycle (should not
    happen for a resolved closure) the remaining nodes are emitted in
    layer-rank+name order rather than raising — emit must never crash a run.
    """
    ids = {n.id for n in nodes}
    by_id = {n.id: n for n in nodes}
    deps: dict[str, set[str]] = {nid: set() for nid in ids}
    for e in graph.edges:
        if e.relation is EdgeType.REQUIRES and e.src in ids and e.dst in ids:
            deps[e.src].add(e.dst)

    ordered: list[Node] = []
    placed: set[str] = set()
    remaining = set(ids)
    while remaining:
        ready = [nid for nid in remaining if deps[nid] <= placed]
        if not ready:                      # cycle — emit the rest deterministically
            ready = list(remaining)
        ready.sort(key=lambda nid: (_LAYER_RANK.get(by_id[nid].layer, 9), by_id[nid].name))
        nxt = ready[0]
        ordered.append(by_id[nxt])
        placed.add(nxt)
        remaining.discard(nxt)
    return tuple(ordered)


@dataclass(frozen=True)
class EmitStep:
    kind: str                      # AttemptKind value: system_install | python_install
    command: str
    target_node_ids: tuple[str, ...]


def build_recipe(graph: DepGraph, ordered: tuple[Node, ...]) -> tuple[EmitStep, ...]:
    """Turn the topo-ordered emittable set into at most two steps (D2):

    1. one apt step for all SystemLib/Tool nodes (dedup apt names), and
    2. one pinned-closure pip step for all Package nodes (resolver-consistent).

    Cross-layer / build-from-source ordering is handled by the drain loop across
    iterations, so a single pass needs only apt-before-pip.
    """
    syslibs = [n for n in ordered if n.type in (NodeType.SYSTEM_LIB, NodeType.TOOL)]
    packages = [n for n in ordered if n.type is NodeType.PACKAGE]
    steps: list[EmitStep] = []

    if syslibs:
        names: list[str] = []
        for n in syslibs:
            apt = _apt_name(n)
            if apt and apt not in names:
                names.append(apt)
        if names:
            steps.append(EmitStep(
                kind="system_install",
                command="apt-get update && apt-get install -y " + " ".join(names),
                target_node_ids=tuple(n.id for n in syslibs),
            ))

    if packages:
        specs = " ".join(_pip_spec(n) for n in packages)
        # python3 (not `python`): real agent bases often ship only python3 (a bare
        # `python` exits 127). --break-system-packages: those bases are usually
        # PEP-668 externally-managed, which otherwise blocks a system pip install.
        # Both are no-ops on a clean python image, so this stays portable.
        steps.append(EmitStep(
            kind="python_install",
            command="python3 -m pip install --break-system-packages " + specs,
            target_node_ids=tuple(n.id for n in packages),
        ))
    return tuple(steps)


def next_deterministic_wave(graph: DepGraph) -> tuple[EmitStep, ...]:
    """The current topological wave: the emittable frontier collapsed to ≤2 batch
    EmitSteps (apt + pip), deps-before-dependents. Empty when nothing is emittable.

    A batch IS a wave: partition() only surfaces nodes whose REQUIRES-deps
    are already SATISFIED, and already excludes backoff-capped / conflicting nodes.
    """
    part = partition(graph)
    if not part.emittable:
        return ()
    ordered = topo_order(graph, part.emittable)
    return build_recipe(graph, ordered)


# === node_recipes.py: low shared node-recipe predicates (classification + pip/apt string builders) ===
_APT_PREFIX = "apt:"


def _is_reciped(node: Node) -> bool:
    """A node the deterministic recipe layer can install (mirrors _is_emittable's
    type/fix test, minus the attempt cap — a backed-off node is still 'reciped').

    A Package flagged ``data['uninstallable']`` (Fix A: the resolved version has
    no installable artifact for the target interpreter) is NOT reciped — the
    renderer must not emit a ``pip install name==version`` that can only fail."""
    if node.type is NodeType.PACKAGE:
        return bool(node.version) and not node.data.get("uninstallable")
    if node.type in (NodeType.SYSTEM_LIB, NodeType.TOOL):
        return bool(node.chosen_fix) and node.chosen_fix.startswith("apt:")
    return False


def _is_service_reciped(node: Node) -> bool:
    """A SERVICE node the deterministic recipe layer can provision — gated behind
    ``V3_INCLUDE_SERVICES`` at the impure orchestration boundary (run_v3_e2e.py),
    never here: this module stays pure (no env reads), so the predicate is purely
    data-driven and the caller decides whether to consult it.

    Deliberately a SEPARATE predicate from ``_is_reciped`` (never folded in) so
    every existing caller of ``_is_reciped`` (annotation ``requires=``/``unblocks=``,
    ``failed_reciped_nodes``, etc.) is unaffected unless it explicitly opts in —
    this must never silently change PACKAGE/SYSTEM_LIB/TOOL semantics.

    A node qualifies once ``classify_services_clean``/``patch_gate`` have admitted
    it with a well-formed ``data['setup']`` dict (``install``/``start``/``probe``
    at minimum; see service_recipes.render_setup / patch_gate._requirement_errors)."""
    return node.type is NodeType.SERVICE and bool(node.data.get("setup"))


def _is_installable_project(node: Node) -> bool:
    """The repo under test, when it declares a build system (pyproject/setup.py,
    recorded as ``data['installable']`` at construction). Rendered as the FINAL
    editable install — the capstone after every dependency — so it is deliberately
    NOT part of ``_is_reciped`` (the third-party recipe set): keeping it separate
    is what lets the renderer emit it once, last, without a per-layer double-emit."""
    return node.type is NodeType.PROJECT and bool(node.data.get("installable"))


def _apt_name(node: Node) -> str | None:
    if node.chosen_fix and node.chosen_fix.startswith(_APT_PREFIX):
        return node.chosen_fix[len(_APT_PREFIX):]
    return None


def _pip_spec(node: Node) -> str:
    return f"{node.name}=={node.version}" if node.version else node.name


# === populate.py: the single producer of node install commands for the static path ===
# The repo under test, installed editable as the capstone AFTER its dependencies.
# --no-deps: the pinned closure emitted above already provides every dependency,
# so pip must not re-resolve them (one uniform pip policy, matching the Package
# lines). Build isolation is left ON (no --no-build-isolation) on purpose: the
# PEP 517 backend (setuptools/hatchling/poetry-core…) is usually absent from the
# resolved closure, so pip fetches it into an isolated build env; the container
# already has network for the closure install, so this never fails for a
# missing backend — whereas --no-build-isolation would.
_EDITABLE_INSTALL = "python3 -m pip install --break-system-packages --no-deps -e ."
# Gold's own fallback for a repo that IS installable but whose editable install
# hits some OTHER problem (e.g. a build backend quirk): try a plain (non-
# editable) install before giving up. Still wrapped non-fatally by
# ``_non_fatal_block`` below — this alone is not the B5(c) failure-marker mechanism.
_PLAIN_INSTALL = "python3 -m pip install --break-system-packages --no-deps ."

# FIX B5(c): every per-node install line is wrapped so ONE node's failure can
# never trip `set -Eeuo pipefail` and abort the whole script. The log is a
# plain, greppable text file — no JSON/locking — so it stays a one-liner even
# under concurrent node installs.
_FAILED_NODES_LOG = "/tmp/v3_failed_nodes.log"


def _non_fatal_block(cmd: str, node_id: str) -> tuple[str, ...]:
    """Wrap ``cmd`` (one node's install command, possibly itself a
    ``cmd1 || cmd2`` fallback chain — see ``_project_command``) in an
    ``if ... then : else <marker> fi`` block, so its failure is recorded as a
    greppable marker instead of propagating (FIX B5c).

    Deliberately NOT an inline ``cmd || echo ... >> log`` suffix: a command in
    an ``if``/``then``/``else`` CONDITION is exempt from ``errexit`` REGARDLESS
    of outcome (same guarantee as the inline form), but ``cmd`` stays on its
    OWN line, byte-identical to the un-wrapped command — other modules' tests
    (and any future ones) grep for the raw install line as the last thing
    before a newline (e.g. a bare package name); an inline suffix would
    silently break every one of them. ``if``/``then`` on separate lines (no
    ``;``) is valid bash — a newline is as good a statement terminator here as
    a semicolon."""
    return (
        f"if {cmd}",
        "then",
        "    :",
        "else",
        f'    echo "V3_NODE_INSTALL_FAILED {node_id}" >> {_FAILED_NODES_LOG}',
        "fi",
    )


# ---------------------------------------------------------------------------
# FIX B6 — ATTEMPT, don't PREDICT. B5(a)'s file-inspection installability
# predictor lived here and is gone: it had exactly one caller
# (``_project_command``, below) and, once that caller stopped consulting it,
# no remaining reason to exist — see the module docstring for the measured
# regression (pre-commit/pre-commit, EBSR 1.0 -> 0) that made "predict, then
# suppress" the wrong shape for this decision.
# ---------------------------------------------------------------------------


def _project_command(node: Node) -> tuple[str, ...]:
    """FIX B6: ALWAYS attempt the capstone — gold's own fallback chain
    (``-e .`` -> plain ``.``), wrapped non-fatally with the same per-node
    failure marker as every other reciped node (b5c), never a bare command
    that can abort the build. No predictor decides whether to try this
    anymore; ``pip`` is the only oracle for whether a repo installs, and a
    failure here is harmless under the wrapper — see
    ``_poison_project_certificate`` for how a failure is still recorded
    (in the CERTIFICATE, not the build)."""
    return _non_fatal_block(f"{_EDITABLE_INSTALL} || {_PLAIN_INSTALL}", node.id)


def _poison_project_certificate(node: Node) -> Node:
    """FIX B6: the capstone now always renders as a non-fatal ATTEMPT, so
    unlike every other reciped node, this pure Python pass can never observe
    whether it actually succeeded — that only happens later, inside the
    rendered bash script, on a different host. Claiming the project is fine
    would be a guess this module cannot back up, and a silently-optimistic
    graph is exactly how pre-commit/pre-commit went from EBSR 1.0 to 0 with
    nothing in the graph saying so (B5's failure mode, see module docstring).

    So the PROJECT node's own certificate defaults to DEGRADED the moment its
    capstone is populated — the same shape ``build.py._excluded_uv_source_node``
    already uses for "this graph does not vouch for this node":
      * ``state=State.MISSING`` — never SATISFIED by assumption.
      * ``version=None`` / ``check_command=None`` — the ONLY guaranteed
        immunity against a later false-positive: ``certify()`` flips
        MISSING -> SATISFIED on any rc-0 check_command, and
        ``emit._is_emittable`` requires a version before it will touch a
        node at all. Leaving both unset means nothing downstream can
        accidentally re-certify this node SATISFIED on a route that was
        never told the truth about what actually happened.
      * ``data['uninstallable']=True`` — inert for a PROJECT node today
        (``build_script``'s constraint-file exclusion and ``emit._is_reciped``
        both scope this flag to PACKAGE nodes only), kept anyway for the same
        "no static claim of success" convention every other such node in this
        codebase carries.

    The rendered script still attempts the real install optimistically — this
    function only degrades the GRAPH's own record of it, never the command
    text ``_project_command`` already produced."""
    return replace(
        node,
        state=State.MISSING,
        version=None,
        check_command=None,
        data={**node.data, "uninstallable": True},
    )


def _command_for(node: Node) -> tuple[str, ...]:
    """The install command(s) for a populatable node: apt for SystemLib/Tool,
    pinned --no-deps pip for Package, editable install for the installable
    Project. The single source of this derivation. Every apt/pip command is
    wrapped non-fatally (FIX B5c) so one node's failure cannot abort the whole
    script — hence a tuple of lines, not one bare command string."""
    apt = _apt_name(node)
    if apt is not None:
        return _non_fatal_block(f"apt-get install -y --no-install-recommends {apt}", node.id)
    if node.type is NodeType.PACKAGE:
        return _non_fatal_block(
            f"python3 -m pip install --break-system-packages --no-deps {_pip_spec(node)}",
            node.id,
        )
    if node.type is NodeType.PROJECT:
        return _project_command(node)
    return (node.chosen_fix,) if node.chosen_fix else ()  # defensive; reciped syslib/tool are always apt


def _should_populate(node: Node) -> bool:
    """Nodes whose install command the renderer emits: the reciped third-party set
    plus the installable Project (its editable capstone install)."""
    return _is_reciped(node) or _is_installable_project(node)


def _service_install_commands(node: Node) -> tuple[str, ...]:
    """Build-time-safe commands for a reciped SERVICE node: ONLY ``data['setup']
    ['install']`` (e.g. ``apt-get install -y postgresql``) — a package install is
    idempotent and safe to bake into a Docker ``RUN`` layer. ``start``/``createdb``/
    ``post`` start a DAEMON PROCESS, which does not survive the build-time layer
    into the later ``docker run`` container (see build_script.render_service_start_
    script's module docstring for the runtime half of this split) — they must
    NEVER be returned here."""
    install = (node.data.get("setup") or {}).get("install") or []
    return tuple(str(c) for c in install)


def populate_setup_commands(graph: DepGraph, *, include_services: bool = False) -> DepGraph:
    """Return a NEW graph in which every populatable node lacking setup_commands
    gets its install command + strength=HARD. Idempotent; leaves Service/Config,
    non-installable projects, and already-populated nodes untouched.

    ``include_services`` (default False, keeps this byte-identical to before):
    when True, every ``_is_service_reciped`` SERVICE node also gets its
    ``data['setup']['install']`` commands populated (build-time apt installs
    only — never start/createdb/post, which are runtime-only; see
    ``_service_install_commands``).

    FIX B6: a PROJECT node's own certificate is poisoned (see
    ``_poison_project_certificate``) in the SAME pass that populates its
    capstone command — the graph never has a moment where the capstone is
    attempted but the node still looks like a clean, unclaimed UNKNOWN."""
    new = graph
    for node in graph.nodes:
        if node.setup_commands:
            continue
        if include_services and _is_service_reciped(node):
            cmds = _service_install_commands(node)
            if not cmds:
                continue
            new = new.with_node(replace(node, setup_commands=cmds, strength=Strength.HARD))
            continue
        if not _should_populate(node):
            continue
        cmds = _command_for(node)
        if not cmds:
            continue
        updated = replace(node, setup_commands=cmds, strength=Strength.HARD)
        if node.type is NodeType.PROJECT and not node.data.get("scratch_certified"):
            updated = _poison_project_certificate(updated)
        new = new.with_node(updated)
    return new
