"""Stage 4 — probing: discover ``SystemLib`` / ``Tool`` nodes.

This realizes design section 4.4 (probe native and system needs).  Two functions,
both pure with respect to their inputs (every "mutation" returns a NEW
``DepGraph``; the originals are never changed — repo immutability rule):

* :func:`install_closure` runs ONE ``pip install`` of the resolved closure and
  parses stderr for *build-time* gaps (compiler / ``*_config`` / headers).  Each
  recognised gap becomes a ``Tool`` node (``layer=TOOLCHAIN``,
  ``discovered_by=PROBE``, ``state=MISSING``) with a ``requires`` edge from the
  owning ``Package``.
* :func:`import_probe` runs ``python -c "import X"`` for every ``Import`` node and
  every native-risk ``Package`` whose name is a valid module name, parsing stderr
  for *run-time* gaps (missing shared libraries).  Each gap becomes a
  ``SystemLib`` node (``layer=SYSTEM``) with a ``requires`` edge from the owning
  ``Package``.

This is discovery only: nodes are surfaced ``MISSING`` with evidence and an
``Attempt`` record.  The host certifies the fix later (Task 8); the apply /
re-probe remediation loop is the agent loop, out of scope here.

The certification invariant (design 3.1) holds: nothing here flips a node to
``SATISFIED``.  A discovered need is ``MISSING`` until a host-run check passes.
"""

from __future__ import annotations

import re
from dataclasses import replace

from python_deps.failure_classifier import NATIVE_LIBRARY_RE

from python_deps.depgraph.executor import Executor
from python_deps.depgraph.ids import syslib_id, tool_id
from python_deps.depgraph.schema import (
    Attempt,
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from python_deps.depgraph.tables import (
    NATIVE_RISK_PACKAGES,
    TOOL_TO_APT,
    apt_for_soname,
    apt_for_tool,
)
from python_deps.depgraph.apt_resolve import resolve_soname_apt

# Timeout (seconds) for the one bulk closure install. A cold install of a large
# closure (downloads + any from-source build) routinely exceeds the executor's
# 300s default; a false timeout would mark the install failed and cascade the
# whole graph to MISSING at certification, so give it generous headroom.
INSTALL_TIMEOUT = 900

# A wheel-build failure prints the distribution being built; used to attribute a
# build-time toolchain gap to the package that triggered it.
_WHEEL_FOR_RE = re.compile(r"[Bb]uilding wheel for ([A-Za-z0-9_.][A-Za-z0-9_.-]*)")
# A legal Python module name (so we never shell ``import opencv-python``).
_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def install_closure(graph: DepGraph, executor: Executor) -> DepGraph:
    """Install the resolved closure once; surface build-time toolchain gaps.

    Returns a new graph with a ``Tool`` node + ``requires`` edge for every
    recognised build-time gap, and an install ``Attempt`` recorded on every
    installed ``Package`` node.

    Resolver-diagnosed ``MISSING`` packages (unresolvable / conflict placeholders
    with no real version) are excluded from the bulk install: adding one makes the
    single ``pip install`` fail and poisons the whole closure (every good package
    would then certify ``MISSING``), defeating per-root resilience.
    """
    packages = [
        n
        for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.state is not State.MISSING
    ]
    if not packages:
        return graph

    command = "python -m pip install " + " ".join(_spec(p) for p in _sorted(packages))
    result = executor.run(command, timeout=INSTALL_TIMEOUT)
    outcome = "succeeded" if result.ok else "failed"

    new = graph
    install_attempt = Attempt(command=command, outcome=outcome)
    for pkg in packages:
        node = new.get(pkg.id)
        new = new.with_node(node.with_attempt(install_attempt))

    if result.ok:
        return new

    stderr = result.stderr or ""
    owners = _build_owners(packages, stderr)
    for tool in _tool_gaps(stderr):
        check = _tool_check(tool)
        evidence = _first_line_with(stderr, tool)
        apt = apt_for_tool(tool)
        predicted_id = tool_id(apt) if apt else None
        reconciled = (
            _reconcile_predicted(
                new, predicted_id, check=check, evidence=evidence, command=command
            )
            if predicted_id
            else None
        )
        if reconciled is not None:
            node_id = reconciled.id
            new = new.with_node(reconciled)
        else:
            node = _make_tool_node(tool, stderr, command)
            node_id = node.id
            new = new.with_node(node)
        for src in owners:
            new = new.with_edge(
                Edge(src=src, dst=node_id, relation=EdgeType.REQUIRES, origin="probe")
            )
    return new


def import_probe(graph: DepGraph, executor: Executor) -> DepGraph:
    """Import-probe every Import / native-risk Package; surface run-time gaps.

    For each probe target, runs ``python -c "import X"`` once, records an
    ``Attempt`` on the affected node(s), and — on an ``ImportError: lib*.so`` —
    creates a ``SystemLib`` node with a ``requires`` edge from the owning
    ``Package`` (or the ``Import`` itself when no package is linked).
    """
    targets = _probe_targets(graph)

    new = graph
    for name, target in targets.items():
        command = f'python -c "import {name}"'
        result = executor.run(command)
        outcome = "succeeded" if result.ok else "failed"
        attempt = Attempt(command=command, outcome=outcome)
        for node_id in target["attempt_nodes"]:
            node = new.get(node_id)
            if node is not None:
                new = new.with_node(node.with_attempt(attempt))

        if result.ok:
            continue
        match = NATIVE_LIBRARY_RE.search(result.stderr or "")
        if not match:
            continue

        soname = match.group("library")
        stderr = result.stderr or ""
        check = f"ldconfig -p | grep {soname}"
        evidence = _first_line_with(stderr, soname)
        apt, _apt_source = resolve_soname_apt(soname, executor)
        predicted_id = syslib_id(apt) if apt else None
        reconciled = (
            _reconcile_predicted(
                new, predicted_id, check=check, evidence=evidence, command=command
            )
            if predicted_id
            else None
        )
        if reconciled is not None:
            node_id = reconciled.id
            new = new.with_node(reconciled)
        else:
            node = _make_syslib_node(soname, stderr, command, apt=apt)
            node_id = node.id
            new = new.with_node(node)
        for src in _edge_sources(target):
            new = new.with_edge(
                Edge(src=src, dst=node_id, relation=EdgeType.REQUIRES, origin="probe")
            )
    return new


# --------------------------------------------------------------------------- #
# Prediction reconciliation                                                    #
# --------------------------------------------------------------------------- #
def _reconcile_predicted(
    graph: DepGraph,
    predicted_id: str,
    *,
    check: str,
    evidence: str,
    command: str,
) -> Node | None:
    """Reconcile an observed gap with a resolver *prediction* of the same id.

    When the resolver pre-emitted a predicted ``Tool``/``SystemLib`` (seed stage)
    for the apt package that provides this observed gap, return a NEW node that
    keeps the predicted node's id + discovery origin (``discovered_by`` stays
    RESOLVER per the spec) but adopts the real observed ``check_command`` /
    ``evidence`` and records the failing probe attempt.  ``state`` is left for the
    host certifier to flip — discovery never certifies (design 3.1).

    Returns ``None`` when there is no matching prediction (caller then creates a
    fresh probe-discovered node), so existing observed-only behavior is preserved.
    """
    predicted = graph.get(predicted_id)
    if predicted is None or predicted.discovered_by is not DiscoveredBy.RESOLVER:
        return None
    return replace(predicted, check_command=check, evidence=evidence).with_attempt(
        Attempt(command=command, outcome="failed", check=check)
    )


# --------------------------------------------------------------------------- #
# Node builders                                                                #
# --------------------------------------------------------------------------- #
def _make_tool_node(tool: str, stderr: str, command: str) -> Node:
    apt = apt_for_tool(tool)
    check = _tool_check(tool)
    node = Node(
        id=tool_id(tool),
        type=NodeType.TOOL,
        name=tool,
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        check_command=check,
        evidence=_first_line_with(stderr, tool),
        fix_candidates=(f"apt:{apt}",) if apt else (),
    )
    return node.with_attempt(
        Attempt(command=command, outcome="failed", check=check)
    )


def _make_syslib_node(soname: str, stderr: str, command: str, apt: str | None = None) -> Node:
    if apt is None:
        apt = apt_for_soname(soname)
    check = f"ldconfig -p | grep {soname}"
    node = Node(
        id=syslib_id(soname),
        type=NodeType.SYSTEM_LIB,
        name=soname,
        layer=Layer.SYSTEM,
        discovered_by=DiscoveredBy.PROBE,
        state=State.MISSING,
        check_command=check,
        evidence=_first_line_with(stderr, soname),
        fix_candidates=(f"apt:{apt}",) if apt else (),
    )
    return node.with_attempt(
        Attempt(command=command, outcome="failed", check=check)
    )


# --------------------------------------------------------------------------- #
# Target selection / attribution                                              #
# --------------------------------------------------------------------------- #
def _probe_targets(graph: DepGraph) -> dict[str, dict]:
    """import-name -> {owners, attempt_nodes, fallback_src}.

    Aggregates each ``Import`` (owner = the ``Package`` it requires) and each
    native-risk ``Package`` whose name is a valid module name (owner = itself).
    Deduped by import name so each name is probed exactly once.
    """
    targets: dict[str, dict] = {}

    for imp in (n for n in graph.nodes if n.type is NodeType.IMPORT):
        entry = targets.setdefault(
            imp.name, {"owners": set(), "attempt_nodes": set(), "fallback_src": None}
        )
        entry["attempt_nodes"].add(imp.id)
        entry["fallback_src"] = imp.id
        entry["owners"].update(
            p.id for p in graph.requires_of(imp.id) if p.type is NodeType.PACKAGE
        )

    for pkg in (n for n in graph.nodes if n.type is NodeType.PACKAGE):
        if pkg.name not in NATIVE_RISK_PACKAGES:
            continue
        if not _MODULE_NAME_RE.match(pkg.name):
            continue
        entry = targets.setdefault(
            pkg.name, {"owners": set(), "attempt_nodes": set(), "fallback_src": None}
        )
        entry["owners"].add(pkg.id)
        entry["attempt_nodes"].add(pkg.id)

    return targets


def _edge_sources(target: dict) -> set[str]:
    """Owning packages for a discovered SystemLib, else the Import node itself."""
    owners = target["owners"]
    if owners:
        return owners
    fallback = target["fallback_src"]
    return {fallback} if fallback else set()


def _build_owners(packages: list[Node], stderr: str) -> set[str]:
    """Packages a build-time gap is attributable to.

    Prefer the distribution named in a "Building wheel for X" line; otherwise
    fall back to the native-risk packages present in the closure (the gap came
    from *some* compiled build, and those are the ones that compile).
    """
    by_name = {p.name: p.id for p in packages}
    owners = {
        by_name[m.group(1)]
        for m in _WHEEL_FOR_RE.finditer(stderr)
        if m.group(1) in by_name
    }
    if owners:
        return owners
    return {p.id for p in packages if p.name in NATIVE_RISK_PACKAGES}


# --------------------------------------------------------------------------- #
# Small pure helpers                                                          #
# --------------------------------------------------------------------------- #
def _tool_gaps(stderr: str):
    """Yield each curated tool/header whose name appears (word-bounded) in stderr."""
    for tool in TOOL_TO_APT:
        if re.search(r"\b" + re.escape(tool) + r"\b", stderr):
            yield tool


def _tool_check(tool: str) -> str:
    """Deterministic check_command for a toolchain need (design 4.4)."""
    if tool.endswith(".h"):
        return (
            "python -c \"import sysconfig, pathlib; "
            f"print(pathlib.Path(sysconfig.get_paths()['include'], '{tool}').exists())\""
        )
    return f"command -v {tool}"


def _spec(pkg: Node) -> str:
    return f"{pkg.name}=={pkg.version}" if pkg.version else pkg.name


def _sorted(packages: list[Node]) -> list[Node]:
    return sorted(packages, key=lambda n: n.name)


def _first_line_with(text: str, needle: str, max_chars: int = 500) -> str:
    for line in (text or "").splitlines():
        if needle in line:
            return line.strip()[:max_chars]
    return (text or "").strip()[:max_chars]
