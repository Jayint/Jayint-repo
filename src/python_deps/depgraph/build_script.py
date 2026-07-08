"""Project a certified DepGraph into one whole, install-only setup.sh artifact
(design 2026-06-29). Pure: no Docker, no network, no LLM, no src.envstate.

Distinct from script.render_setup_sh (the live block-stepped, round-trippable
format): this renderer hoists shared setup and adds tier section headers, so it
is intentionally NOT parseable back to one-block-per-node.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python_deps.depgraph.block import Block

from python_deps.depgraph.certify import EXECUTION_LAYER_ORDER
from python_deps.depgraph.emit import (
    _apt_name,
    _is_installable_project,
    _is_reciped,
    _is_service_reciped,
    topo_order,
)
from python_deps.depgraph.populate import populate_setup_commands
from python_deps.depgraph.schema import DepGraph, Layer, Node, NodeType

_BANNER = (
    "#!/usr/bin/env bash",
    "#",
    "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.",
    "# Edit the graph and re-render; this file is an artifact, not a source.",
    "#",
)
# Shared with certify's execution-layer walk so the rendered artifact's section
# order never contradicts host certification order; any Layer not part of the
# certified walk (e.g. SERVICES) is appended so no section is silently dropped.
_LAYER_ORDER: tuple[Layer, ...] = EXECUTION_LAYER_ORDER + tuple(
    L for L in Layer if L not in EXECUTION_LAYER_ORDER
)


def _section_header(layer: Layer) -> str:
    label = layer.value.upper()
    return f"# ==================== {label} ===================="



def _annotation(graph: DepGraph, node: Node) -> list[str]:
    from python_deps.depgraph.advise import _best_evidence_line  # lazy: avoid load-order coupling
    toks = [f"#@node {node.id}"]
    if node.version:
        toks.append(f"version={node.version}")
    if _apt_name(node) is not None:
        toks.append(f"provider={node.chosen_fix}")
    reqs = [d.id for d in graph.requires_of(node.id) if _is_reciped(d)]
    toks.append("requires=" + (",".join(sorted(reqs)) if reqs else "-"))
    unblocks = sorted(n.id for n in graph.required_by(node.id) if _is_reciped(n))
    if unblocks:
        toks.append("unblocks=" + ",".join(unblocks))
    if node.build_from_source:
        toks.append("build-from-source")
    if node.layer is Layer.TOOLCHAIN:
        toks.append("toolchain")
    ev = _best_evidence_line(node.evidence)
    if ev:
        toks.append(f"evidence={ev}")
    out = ["  ".join(toks)]
    if node.check_command:
        out.append(f"#@check {node.check_command}")
    return out


def _node_block(graph: DepGraph, node: Node, apt_done: list[bool]) -> list[str]:
    out: list[str] = []
    if _apt_name(node) is not None and not apt_done[0]:
        out += ["export DEBIAN_FRONTEND=noninteractive", "apt-get update"]
        apt_done[0] = True
    out += _annotation(graph, node)
    out += list(node.setup_commands)
    return out


def _reciped_in_layer(graph: DepGraph, layer: Layer) -> tuple[Node, ...]:
    nodes = tuple(n for n in graph.nodes if n.layer is layer and _is_reciped(n))
    return topo_order(graph, nodes)


def _service_reciped_in_layer(graph: DepGraph, layer: Layer) -> tuple[Node, ...]:
    """V3_INCLUDE_SERVICES-gated mirror of ``_reciped_in_layer`` for SERVICE
    nodes. Only consulted by ``render_build_script`` when ``include_services``
    is True — callers must gate it explicitly (this module stays pure/env-free)."""
    nodes = tuple(n for n in graph.nodes if n.layer is layer and _is_service_reciped(n))
    return topo_order(graph, nodes)


_PROJECT_HEADER = "# ==================== PROJECT (editable) ===================="


def _installable_project(graph: DepGraph) -> Node | None:
    """The repo-under-test node whose editable install should render LAST, or
    None. Requires populated setup_commands so ``populate_setup_commands`` runs
    first (render_build_script guarantees this)."""
    for node in graph.nodes:
        if _is_installable_project(node) and node.setup_commands:
            return node
    return None


_NEED_TYPES: tuple[NodeType, ...] = (NodeType.CONFIG, NodeType.SERVICE)


def _need_block(graph: DepGraph, node: Node) -> list[str]:
    from python_deps.depgraph.advise import _best_evidence_line  # lazy: avoid load-order coupling
    reqs = [d.id for d in graph.requires_of(node.id) if _is_reciped(d)]
    head = f"#@need {node.id}  state={node.state.value}"
    if reqs:
        head += "  requires=" + ",".join(sorted(reqs))
    out = ["#", head]
    if node.check_command:
        out.append(f"#@check {node.check_command}")
    ev = _best_evidence_line(node.evidence)
    if ev:
        out.append(f"#@evidence {ev}")
    out.append("#     (no command — propose a governed block to satisfy this)")
    return out


def _need_in_layer(
    graph: DepGraph, layer: Layer, covered: set[str], *, include_services: bool = False
) -> list[Node]:
    nodes = [n for n in graph.nodes
             if n.layer is layer and n.type in _NEED_TYPES
             and not _is_reciped(n) and n.id not in covered
             # once a SERVICE node is install-active (include_services), it must
             # not ALSO render as a #@need stub — mirrors _reciped_in_layer's
             # exclusion of _is_reciped nodes above.
             and not (include_services and _is_service_reciped(n))]
    return sorted(nodes, key=lambda n: n.id)


def _block_block(block: Block) -> list[str]:
    head = f"#@block {block.block_id}  source=llm-patch"
    if block.target_node_ids:
        head += "  targets=" + ",".join(block.target_node_ids)
    if block.evidence_refs:
        head += "  evidence=" + ",".join(block.evidence_refs)
    out = [head]
    for chk in block.check_commands:
        out.append(f"#@check {chk}")
    out.extend(block.commands)
    return out


def _graph_hash(graph: DepGraph) -> str:
    reciped_ids = {n.id for n in graph.nodes if _is_reciped(n)}
    nodes_payload = sorted(
        (n.id, n.version or "", n.chosen_fix or "")
        for n in graph.nodes if _is_reciped(n)
    )
    edges_payload = sorted(
        (e.src, e.dst, e.relation.value)
        for e in graph.edges
        if e.src in reciped_ids and e.dst in reciped_ids
    )
    blob = json.dumps({"nodes": nodes_payload, "edges": edges_payload},
                      separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def _closure_meta(graph: DepGraph) -> dict[str, str]:
    meta: dict[str, str] = {}
    for n in sorted((n for n in graph.nodes if n.type is NodeType.PACKAGE),
                    key=lambda n: n.id):
        for key, attr in (("python", "resolved_python"),
                          ("platform", "resolved_platform"),
                          ("exclude-newer", "exclude_newer")):
            val = getattr(n, attr, None)
            if val and key not in meta:
                meta[key] = val
    return meta


_TYPE_WORD = {NodeType.SYSTEM_LIB: "system", NodeType.TOOL: "toolchain",
              NodeType.PACKAGE: "pip", NodeType.SERVICE: "service"}
_NEED_WORD = {NodeType.SERVICE: "service", NodeType.CONFIG: "config"}


def _manifest(graph: DepGraph, manual_blocks, *, include_services: bool = False) -> list[str]:
    reciped = [n for n in graph.nodes
               if _is_reciped(n) or (include_services and _is_service_reciped(n))]
    covered = {nid for b in manual_blocks for nid in b.target_node_ids}
    needs = [n for n in graph.nodes
             if n.type in _NEED_TYPES and not _is_reciped(n) and n.id not in covered
             and not (include_services and _is_service_reciped(n))]
    counts = Counter(_TYPE_WORD.get(n.type, n.type.value) for n in reciped)
    count_str = ", ".join(f"{counts[w]} {w}" for w in ("system", "toolchain", "pip", "service")
                          if counts.get(w))
    need_counts = Counter(_NEED_WORD.get(n.type, n.type.value) for n in needs)
    need_str = ", ".join(f"{need_counts[w]} {w}"
                         for w in ("service", "config")
                         if need_counts.get(w))
    needs_suffix = f" ({need_str})" if need_str else ""
    meta = _closure_meta(graph)
    meta_str = "   ".join(f"{k}: {v}" for k, v in meta.items())
    lines = list(_BANNER)  # full banner; _BANNER[-1] is the "#" separator (keep it)
    lines.append(f"#   nodes: {len(reciped)} reciped ({count_str or 'none'}) "
                 f"+ {len(needs)} needs{needs_suffix}")
    hash_line = f"#   graph-hash: {_graph_hash(graph)}"
    if meta_str:
        hash_line += "   " + meta_str
    lines.append(hash_line)
    lines.append("#")
    return lines


def render_build_script(
    graph: DepGraph | None,
    manual_blocks: tuple[Block, ...] = (),
    *,
    include_services: bool = False,
) -> str:
    """Project a certified DepGraph into one install-only setup.sh.

    ``include_services`` (default False — the pre-existing, byte-identical
    behavior: SERVICE nodes render as inert ``#@need`` stubs, same as CONFIG)
    gates a SECOND behavior, additive on top of the first: when True, every
    ``_is_service_reciped`` SERVICE node's ``data['setup']['install']`` commands
    (build-time-safe package installs, e.g. ``apt-get install -y postgresql``)
    become ACTIVE lines in this script, and that node no longer renders as a
    ``#@need`` stub. ``start``/``createdb``/``post`` are NEVER emitted here — a
    daemon started inside a Dockerfile ``RUN`` layer is dead by the time a later
    ``docker run`` container starts (see ``render_service_start_script``, the
    runtime counterpart rendered as a separate ENTRYPOINT-wrapper artifact)."""
    if graph is None:
        graph = DepGraph()
    # single call site: derive commands, then emit
    graph = populate_setup_commands(graph, include_services=include_services)
    parts: list[str] = _manifest(graph, manual_blocks, include_services=include_services) + [
        "set -Eeuo pipefail",
        "",
        "# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.",
        'command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python',
        "",
        # pytest is the testability gate's runner (`python -m pytest -q`) — its
        # PRECONDITION, not a prediction of the repo's deps. Ensure it (like the
        # shim) as the floor for repos that declare pytest only in tox.ini / not
        # at all and whose tests never `import pytest`. Guarded: a repo whose
        # graph already installs pytest re-runs nothing. Kept OUT of select_roots
        # so it does not bloat every graph with a pytest-closure resolve.
        "# Ensure the pytest test-runner (testability-gate precondition; not a graph node).",
        'python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest',
    ]
    covered = {nid for b in manual_blocks for nid in b.target_node_ids}
    blocks_by_wave: dict[str, list] = {}
    for b in manual_blocks:
        blocks_by_wave.setdefault(b.wave, []).append(b)
    apt_done = [False]
    for layer in _LAYER_ORDER:
        section: list[str] = []
        for node in _reciped_in_layer(graph, layer):
            section += _node_block(graph, node, apt_done)
        if include_services:
            for node in _service_reciped_in_layer(graph, layer):
                section += _node_block(graph, node, apt_done)
        for b in blocks_by_wave.get(layer.value, ()):
            section += _block_block(b)
        for node in _need_in_layer(graph, layer, covered, include_services=include_services):
            section += _need_block(graph, node)
        if section:
            parts.append("")
            parts.append(_section_header(layer))
            parts.extend(section)
    # The repo-under-test installs LAST: its editable install is the capstone that
    # every dependency section above provisions. Emitted here (not via a layer)
    # so it is unconditionally after all deps and never double-emitted by a layer
    # section — see emit._is_installable_project for why it is NOT in _is_reciped.
    proj = _installable_project(graph)
    if proj is not None:
        parts.append("")
        parts.append(_PROJECT_HEADER)
        parts += _node_block(graph, proj, apt_done)
    # Fail-fast: PatchGate (Phase 1) rejects illegal waves, so any manual block whose
    # wave is not a Layer value is a programming error, not user input — never silently
    # render it into an UNSCHEDULED section.
    known_waves = {layer.value for layer in _LAYER_ORDER}
    illegal = [b.block_id for b in manual_blocks if b.wave not in known_waves]
    if illegal:
        raise ValueError(f"render_build_script: manual blocks have illegal waves "
                         f"(not a Layer value): {illegal}")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Service start script — the RUNTIME half of the SERVICE split (design
# 2026-07-08, V3_INCLUDE_SERVICES). setup.sh runs entirely at Docker BUILD time
# (a single ``RUN bash setup.sh`` layer); any daemon started there is dead by
# the time the eval harness's ``docker run -d <image> tail -f /dev/null`` boots
# a brand-new container and later ``docker exec``s pytest into it (filesystem
# layers persist, running processes never do). This function renders a SEPARATE
# artifact meant to be baked in as a Dockerfile ``ENTRYPOINT``: it starts every
# reciped SERVICE node's daemon, blocks on its probe, runs createdb/post, and
# finally ``exec "$@"`` — so ``docker run -d ... tail -f /dev/null`` hands off to
# the foreground ``tail`` only once every service is live, well before any
# subsequent ``docker exec ... pytest`` call reaches the container.
# ---------------------------------------------------------------------------

_SERVICE_START_BANNER = (
    "#!/usr/bin/env bash",
    "#",
    "# v3_start_services.sh — COMPILED from the certified dependency graph. DO NOT EDIT.",
    "# Runtime ENTRYPOINT wrapper: starts each SERVICE node's daemon, waits for its",
    "# probe, runs createdb/post, then execs the container's original command ($@).",
    "#",
)


def _service_nodes_for_start(graph: DepGraph) -> tuple[Node, ...]:
    """Every reciped SERVICE node, dependency-ordered. Not layer-filtered (unlike
    the setup.sh walk) — this is a single flat script, not a section-by-layer
    artifact, so every SERVICE node in the graph (they all share Layer.SERVICES
    in practice) belongs in it regardless of the caller's layer loop."""
    nodes = tuple(n for n in graph.nodes if _is_service_reciped(n))
    return topo_order(graph, nodes)


_PROBE_WAIT_ATTEMPTS = 30


def _as_command_list(value) -> list[str]:
    """Coerce a setup field that may be a bare string, a list, or falsy into a
    list of command strings. Fields like ``post``/``install``/``bind`` are
    documented as "a string OR a list" — a naive ``for cmd in value`` over a
    bare string silently iterates its CHARACTERS (``list("mc mb x")`` ==
    ``['m', 'c', ' ', 'm', 'b', ...]``), which would render one command per
    letter instead of one command per line. A string is exactly ONE command."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _probe_wait_lines(node_id: str, probe: str) -> list[str]:
    """Bounded readiness wait that does NOT exit the script.

    Deliberately NOT ``service_recipes.render_probe_poll`` — that helper ends
    in ``&& exit 0 ... exit 1``, which is safe only as the LAST line of a
    throwaway check script. Inside ``v3_start_services.sh`` (a single script
    that starts EVERY service, in order, before ``exec "$@"``), an ``exit 0``
    the instant the FIRST service's probe succeeds would terminate the whole
    script before any later service's start/post/createdb or the final
    ``exec "$@"`` ever ran — the container would then have no foreground
    process and `docker run -d ... tail -f /dev/null` would exit immediately,
    leaving a dead container for any subsequent `docker exec ... pytest`.

    On timeout, a warning is printed to stderr (a trace, not a fatal) and the
    script STILL proceeds — a not-yet-ready service is recoverable; a dead
    container (never reaching ``exec "$@"``) is not."""
    return [
        f"for _i in $(seq 1 {_PROBE_WAIT_ATTEMPTS}); do {probe} && break; sleep 1; done",
        f'{probe} || echo "[v3] WARNING: {node_id} probe did not succeed within '
        f'{_PROBE_WAIT_ATTEMPTS}s: {probe}" >&2',
    ]


def _service_start_block(node: Node) -> list[str]:
    """start -> probe-wait -> post -> createdb, for one SERVICE node.

    post BEFORE createdb: the known-kind recipes put the CREATE USER statement
    in ``post`` and a ``createdb -O <user> ...`` (owner = that just-created user)
    in ``createdb`` (service_recipes.render_setup) — the owner must exist before
    ``createdb -O`` runs, or it fails. This ordering is correct for every kind in
    ``service_recipes._KIND_BASE`` (mysql's createdb doesn't reference a user at
    all, so the ordering is a no-op there)."""
    setup = node.data.get("setup") or {}
    start = setup.get("start")
    probe = setup.get("probe")
    createdb = setup.get("createdb")
    post = _as_command_list(setup.get("post"))
    out: list[str] = [f"# ---- {node.id} ----"]
    if start:
        out.append(str(start))
    if probe:
        out.extend(_probe_wait_lines(node.id, str(probe)))
    out.extend(post)
    if createdb:
        out.append(str(createdb))
    return out


def render_service_start_script(graph: DepGraph | None) -> str:
    """Bash ENTRYPOINT-wrapper script: start + probe-wait + post + createdb for
    every reciped SERVICE node (dependency-ordered), terminated with
    ``exec "$@"`` so it composes as ``ENTRYPOINT ["/bin/bash",
    "/v3_start_services.sh"]`` — the wrapped foreground command (``tail -f
    /dev/null`` in the eval harness) still runs, just after every service is
    live. Pure — no Docker, no network, no LLM.

    Empty string when the graph has no reciped SERVICE node — the Dockerfile
    side must then add NO ``ENTRYPOINT`` at all, so a repo with zero services
    (the common case) is a strict no-op end to end."""
    if graph is None:
        graph = DepGraph()
    nodes = _service_nodes_for_start(graph)
    if not nodes:
        return ""
    parts: list[str] = list(_SERVICE_START_BANNER) + ["set -Eeuo pipefail", ""]
    for node in nodes:
        parts.extend(_service_start_block(node))
        parts.append("")
    parts.append('exec "$@"')
    return "\n".join(parts) + "\n"
