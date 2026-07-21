"""Project a certified DepGraph into one whole, install-only setup.sh artifact
(design 2026-06-29). Pure: no Docker, no network, no LLM, no src.envstate.

Distinct from script.render_setup_sh (the live block-stepped, round-trippable
format): this renderer hoists shared setup and adds tier section headers, so it
is intentionally NOT parseable back to one-block-per-node.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections import Counter
from graph.core.certify import EXECUTION_LAYER_ORDER
from graph.compile.emit import (
    _apt_name,
    _is_installable_project,
    _is_reciped,
    _is_service_reciped,
    populate_setup_commands,
    topo_order,
)
from graph.model import DepGraph, Layer, Node, NodeType, project_resolved_python
from graph.patch.block import Block  # runtime (script's render/parse folded in here, 3c-1)
from graph.python.config_scan import (  # config-provenance vocabulary (single source of truth)
    _ENV_EXAMPLE_FILES,
    _SOURCE_AUTHORITATIVE,
    _SOURCE_SETDEFAULT,
)

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
    from graph.advise import _best_evidence_line  # lazy: avoid load-order coupling
    toks = [f"#@node {node.id}"]
    if node.version:
        toks.append(f"version={node.version}")
    if _apt_name(node) is not None:
        toks.append(f"provider={node.chosen_fix}")
    reqs = [d.id for d in graph.requires_of(node.id) if _is_reciped(d)]
    if node.type is NodeType.PROJECT:
        # declared-direct deps are re-homed to node data (was Project->Package edges);
        # reconstruct them for the capstone annotation so setup.sh stays byte-honest.
        reqs += [n.id for n in graph.nodes
                 if n.type is NodeType.PACKAGE
                 and n.data.get("declared") == "direct" and _is_reciped(n)]
    toks.append("requires=" + (",".join(sorted(set(reqs))) if reqs else "-"))
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


# ---------------------------------------------------------------------------
# SOFT requirements files (design 2026-07-13) — nested requirements files the
# recursive discovery walk found OUTSIDE the hard-root allowlist (e.g.
# Archipelago's ~84 `worlds/*/requirements.txt`). evidence.py deliberately does
# NOT ingest these into `declared_dependencies` (one unified `uv lock` over 84
# independent subprojects' pins is a harder constraint problem than the
# reference solution actually solved, and a stray test-fixture requirements
# file becoming a hard resolve root would silently poison the closure).
# build.py._add_project_node carries their repo-relative paths on the PROJECT
# node's `data["soft_requirements_files"]` (a tuple — Node.data is immutable);
# this renders them as best-effort, non-blocking installs, constrained (`-c`)
# so a stale pin in a nested file can add packages but never move one the
# certified closure already pinned.
# ---------------------------------------------------------------------------

_SOFT_REQ_HEADER = (
    "# ---- Soft requirements (best-effort; may ADD packages, never MOVE a "
    "pinned one) ----"
)
_SOFT_REQ_CONSTRAINTS_PATH = "/tmp/v3_closure_constraints.txt"

# FIX 2 (post-measurement, 2026-07-14) — the exclusion gate's back door.
# `_pinned_constraint_lines` correctly OMITS `uninstallable` Package nodes
# (git/url/direct-reference/non-default-index sources -- the graph
# deliberately excluded them because installing by bare `name==version` would
# fetch the PUBLIC PyPI namesake, not the real code) from the *pinned* lines.
# But omission alone left the name completely UNCONSTRAINED: a nested SOFT
# requirements file (e.g. Archipelago's `worlds/sc2/requirements.txt`) that
# happens to list that same bare name by coincidence sailed straight through
# `pip install -r ... -c constraints.txt`, silently installing the real public
# PyPI package -- the exact dependency-confusion / false-green class Fix 1
# closed through the front door, reopened here through the back one.
#
# The fix: every excluded name is ALSO emitted into the SAME constraints
# file, pinned to a specifier that can never resolve on public PyPI. A local
# version segment (`+v3.excluded`) does it -- PEP 440 / packaging policy
# forbids local versions on public index uploads, so no genuine PyPI release
# can ever carry one, while `packaging.version.Version` still parses it as an
# ordinary, valid version (verified in test_unsatisfiable_specifier_is_valid_
# pep440). That matters: pip must fail with "no matching distribution"
# (a resolution failure) never "invalid requirement" (a parse failure) --
# the latter could abort the whole `-c` file before it even protects the
# OTHER, real packages pinned alongside it.
#
# Cost, stated plainly: pip resolves a requirements file as ONE unit, so once
# an excluded name poisons a soft file's constraint set, that file's pip
# invocation fails and NONE of its other (possibly perfectly installable)
# packages install either -- `|| true` swallows the failure and the build
# continues, so the outcome is simply "this soft file's packages are absent".
# Do not try to salvage the rest by constraining only the offending line or
# splitting the file: correctness beats completeness -- a wrong package
# silently installed is worse than a missing one loudly absent.
_EXCLUDED_CONSTRAINT_VERSION = "0.0.0+v3.excluded"
_EXCLUDED_CONSTRAINT_HEADER = (
    "# excluded (non-PyPI source): must never resolve from public PyPI"
)


def _excluded_constraint_lines(graph: DepGraph) -> tuple[str, ...]:
    """``name==0.0.0+v3.excluded`` for every PACKAGE node the graph
    deliberately excluded from the resolve closure (``data['uninstallable']``
    -- see the module-level FIX 2 comment above and
    ``_pinned_constraint_lines``'s docstring for the three producers of this
    flag). Sorted by name for determinism, same discipline as
    ``_pinned_constraint_lines``. The node's own version (real or None) is
    irrelevant here -- the pin is always the same unsatisfiable specifier,
    never the node's actual resolved/unresolved version."""
    names = sorted({
        n.name for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.data.get("uninstallable")
    })
    return tuple(f"{name}=={_EXCLUDED_CONSTRAINT_VERSION}" for name in names)


def _soft_requirements_files(graph: DepGraph) -> tuple[str, ...]:
    """Sorted, deduped union of every PROJECT node's soft requirements paths.
    Sorted (not insertion order) so the rendered script stays deterministic
    regardless of node/tuple ordering (render_fidelity's ``render(g) ==
    render(g)`` and cross-order-invariance are load-bearing here)."""
    files: set[str] = set()
    for node in graph.nodes:
        if node.type is NodeType.PROJECT:
            files.update(node.data.get("soft_requirements_files") or ())
    return tuple(sorted(files))


def _pinned_constraint_lines(graph: DepGraph) -> tuple[str, ...]:
    """``name==version`` for every PACKAGE node that has a pinned version —
    sorted by name for determinism. A package with no version is omitted
    entirely (never rendered as a bare ``name==``): the whole point of the
    constraints file is to protect versions the closure actually pinned.

    A node flagged ``data['uninstallable']`` (the Fix-A "no installable
    artifact" gate, also reused by the Gate-1 false-green fix for a package
    whose real source is git/url/directory/editable/a non-default registry —
    see resolve_lock._missing_source_node) is excluded too: pinning it here
    would let an UNRELATED soft-requirements.txt entry of the same name latch
    onto that pin and get installed from public PyPI through this
    constraints file, even though the closure never actually resolved it."""
    pkgs = sorted(
        (
            n for n in graph.nodes
            if n.type is NodeType.PACKAGE
            and n.version
            and not n.data.get("uninstallable")
        ),
        key=lambda n: n.name,
    )
    return tuple(f"{n.name}=={n.version}" for n in pkgs)


def _soft_requirements_section(graph: DepGraph) -> list[str]:
    """The soft-requirements block, or ``[]`` when there are no soft files at
    all (no header, no heredoc — a strict no-op for the common case). When
    there IS at least one soft file, the constraints heredoc is emitted even
    if there is nothing to protect (no pinned packages) — there is simply
    nothing to constrain, but the shape stays uniform."""
    files = _soft_requirements_files(graph)
    if not files:
        return []
    lines = [_SOFT_REQ_HEADER, f"cat > {_SOFT_REQ_CONSTRAINTS_PATH} <<'V3_EOF'"]
    lines.extend(_pinned_constraint_lines(graph))
    excluded = _excluded_constraint_lines(graph)
    if excluded:
        lines.append(_EXCLUDED_CONSTRAINT_HEADER)
        lines.extend(excluded)
    lines.append("V3_EOF")
    for f in files:
        # Matches the repo's existing pip invocation form
        # (`python3 -m pip install --break-system-packages`, see populate.py) —
        # no `--no-deps`: unlike a pinned Package node, a soft file may need
        # transitive deps the closure never resolved. `-c` is load-bearing (see
        # module docstring above); `|| true` mirrors the reference (gold)
        # solution's own `pip install -r "$f" || true` — independent,
        # best-effort, never fatal under `set -Eeuo pipefail`.
        lines.append(
            "python3 -m pip install --break-system-packages "
            f"-r {shlex.quote(f)} -c {_SOFT_REQ_CONSTRAINTS_PATH} || true"
        )
    return lines


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

# FIX B1 — CONFIG needs are MODELLED but were never RENDERED: a node whose
# value IS known (classify_services_clean._config_nodes' `data["config_value"]`
# convention, resolved from scan_env_defaults/.env.example) gets an additional
# machine-parseable marker line so multi_docker_eval_adapter can bake it as a
# Dockerfile ENV. setup.sh itself cannot do this: it runs inside one Docker RUN
# layer, and `export` there dies with that layer — it neither survives into
# the image nor into the later `docker exec` the eval harness runs pytest
# through (see multi_docker_eval_adapter._render_dockerfile). The marker stays
# a bare "#" comment for exactly that reason: it is a hand-off to a DIFFERENT
# artifact, never a live command in this one (test_need_stubs_are_comment_only's
# exhaustive comment-only invariant covers this line too).
_CONFIG_ENV_UNKNOWN = "?"
# Never bake a secret-shaped var name into an image layer (Dockerfile ENV
# values are visible in `docker history`/inspect) — mirrors the same-purpose
# denylist in the orchestrate/loop synthesizer, kept as its own small local copy
# so build_script.py (graph) stays decoupled from src.orchestrate (the run loop).
_CONFIG_ENV_SECRET_RE = re.compile(
    r"(SECRET|PASSWORD|PASSWD|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)",
    re.IGNORECASE,
)
_CONFIG_ENV_DENYLIST = frozenset({
    "PATH", "PYTHONPATH", "HOME", "PWD", "LD_LIBRARY_PATH", "SHELL", "USER",
})

# FIX 3 (post-B1) — a real construction run baked POSTGRES_HOST/POSTGRES_PORT/
# MYSQL_HOST/MYSQL_PORT/SALT_KEY/... alongside the intended DJANGO_SETTINGS_MODULE.
# The dangerous category is "anything naming a host, port, or credential", and no
# denylist will ever enumerate that -- the secret regex above already proved it
# (it missed generic `*_KEY`/`*_SALT`/`*_CREDENTIALS`; that is how SALT_KEY got
# through). So this is inverted to an ALLOWLIST: bake ONLY framework
# settings-module-shaped vars -- the entire evidenced payoff
# (DJANGO_SETTINGS_MODULE unlocking a whole test suite). Everything else stays an
# inert `#@need` hint, exactly as before this fix -- do not widen this set to
# "support more vars". An unverified ENV that names a host/port/credential
# silently points the app at the wrong place and turns a loud failure into a
# wrong answer; CONFIG nodes are advisory and NEVER certified (see
# classify_services_clean.py:8 — "Config nodes are advisory... never scheduled"),
# so nothing downstream would catch the mistake.
_CONFIG_ENV_ALLOWLIST = frozenset({
    "DJANGO_SETTINGS_MODULE", "SETTINGS_MODULE", "FLASK_APP", "FLASK_ENV",
    "APP_SETTINGS",
})


def _known_config_value(node: Node) -> str | None:
    """The value carried in a CONFIG node's ``data["config_value"]`` (set by
    ``classify_services_clean._config_nodes`` from a real static source --
    ``scan_env_defaults``/``.env.example`` -- never invented here), or ``None``
    when there is no real value known. Same refusals as before: a blank value,
    the ``"?"`` unknown sentinel, or anything containing a newline."""
    value = node.data.get("config_value")
    if not isinstance(value, str) or not value or value == _CONFIG_ENV_UNKNOWN or "\n" in value:
        return None
    return value


# The FULL (rung, source) pairs that may bake, grounded in config_scan's provenance
# vocabulary (never a duplicated magic string). Rung-1/2/3 sources are ALL a closed,
# enumerable set here -- rung 1 is exactly `_SOURCE_AUTHORITATIVE`, rung 2 is exactly
# the `.env.*` template filenames `parse_env_example_provenance` can win from, and
# rung 3's ONLY bake-eligible source is `_SOURCE_SETDEFAULT` (3a); rung-3b
# `_SOURCE_FALLBACK` is deliberately absent. There is no open-vocabulary rung.
_BAKE_ELIGIBLE_SOURCES_BY_RUNG: dict[int, frozenset[str]] = {
    1: frozenset({_SOURCE_AUTHORITATIVE}),
    2: frozenset(_ENV_EXAMPLE_FILES),
    3: frozenset({_SOURCE_SETDEFAULT}),
}


def _config_bake_eligible(node: Node) -> bool:
    """Bake-eligibility keyed on a CONFIG node's ``data["config_provenance"]``
    ``{rung, source}`` (B1 residual c / review #1+#2). FAILS CLOSED on the FULL
    pair: baking a Dockerfile ENV is safety-sensitive (a wrong value silently
    shadows the real config), so a value bakes ONLY when its provenance is an
    exactly-recognized (rung, source) pair in ``_BAKE_ELIGIBLE_SOURCES_BY_RUNG``:

      * rung 1 with source ``authoritative_config``.
      * rung 2 with source == the winning ``.env.*`` template filename.
      * rung 3 with source ``code_scan_setdefault`` (the env-*writing*
        ``os.environ.setdefault`` entrypoint idiom -- the DJANGO_SETTINGS_MODULE
        payoff).

    Everything else is advisory-only and NEVER bakes: ABSENT provenance (a legacy
    serialized or hand-built ``config_value``-only node), a non-int / bool / float
    ``rung`` (``type(rung) is int`` explicitly rejects ``bool``, an ``int``
    subclass), an unknown ``rung``, a missing / empty / non-``str`` / unrecognized
    ``source``, and rung-3b ``code_scan_fallback``. In production every discovered
    value is stamped by ``classify_services_clean._resolve_config_value``, so a
    missing or off-vocabulary stamp is a signal to withhold, not to trust."""
    prov = node.data.get("config_provenance")
    if not isinstance(prov, dict):
        return False
    rung = prov.get("rung")
    if type(rung) is not int or rung not in _BAKE_ELIGIBLE_SOURCES_BY_RUNG:
        return False                                  # rejects bool/float/unknown-rung
    source = prov.get("source")
    return isinstance(source, str) and source in _BAKE_ELIGIBLE_SOURCES_BY_RUNG[rung]


def _config_env_marker(node: Node) -> str | None:
    """``#@config-env VAR=value`` for a CONFIG node with a known, safe-to-bake
    value; ``None`` when no value is known, the var is not on the
    settings-module allowlist (FIX 3), its provenance is a rung-3b advisory-only
    fallback (Task 3), or -- belt-and-braces, since the allowlist should already
    make these unreachable -- it looks secret/incidental."""
    if node.type is not NodeType.CONFIG:
        return None
    if node.name not in _CONFIG_ENV_ALLOWLIST:
        return None
    if not _config_bake_eligible(node):
        return None
    value = _known_config_value(node)
    if value is None:
        return None
    if node.name in _CONFIG_ENV_DENYLIST or _CONFIG_ENV_SECRET_RE.search(node.name):
        return None
    return f"#@config-env {node.name}={value}"


def _need_block(graph: DepGraph, node: Node) -> list[str]:
    from graph.advise import _best_evidence_line  # lazy: avoid load-order coupling
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
    marker = _config_env_marker(node)
    if marker:
        out.append(marker)
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


def _interpreter_assertion_lines(graph: DepGraph) -> list[str]:
    """The first graph-derived preamble line(s): a hard interpreter-minor check
    derived from the PROJECT node's stamped ``resolved_python`` (re-homed from
    the retired RUNTIME node). Empty when nothing is stamped (old graphs render
    no assertion — no invented default). POSIX-sh-safe: the whole check is a
    single ``python3 -c`` with single-quoted Python internals, so it survives
    ``set -Eeuo pipefail`` and aborts the build loudly on a wrong-minor base
    image (``sys.exit(str)`` prints to stderr and exits non-zero)."""
    minor = project_resolved_python(graph)  # PROJECT-only; no RUNTIME fallback
    if not minor:
        return []
    maj, min_ = minor.split(".")[:2]
    return [
        f"# Assert the interpreter minor matches the graph's resolved python "
        f"{minor} (wrong base image fails loudly and early).",
        f"python3 -c \"import sys; sys.exit(0 if sys.version_info[:2]==({maj},{min_}) "
        f"else 'setup.sh: graph resolved for python {minor}, but interpreter is "
        f"%d.%d' % sys.version_info[:2])\"",
    ]


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
    # §4.4 (post-review): the instrument (python->python3 symlink + pytest floor)
    # is baked into v3-base:3.X as the PRIMARY source, but setup.sh keeps a
    # pipefail-SAFE fallback — it also runs in the in-loop run_v3 Sandbox on the
    # STOCK python:3.X-slim base (run.py renders + run_install_script's it) and in
    # eval Dockerfiles whose FROM did not map to a baked image (explicit/variant
    # bases, or the opt-in swap off). The symlink is guarded; the pytest floor ends
    # in `|| true` so a pip-less base never aborts the run (also fixes the original
    # preamble-death). No-op on v3-base (pytest present). pytest still installs from
    # its PIP node when declared (floor-not-pin). `set -Eeuo pipefail` is shell
    # safety, not instrument.
    # The interpreter-minor assertion is the FIRST graph-derived preamble line
    # (after `set -Eeuo pipefail`, before the interpreter-agnostic instrument
    # floor): a wrong-minor base image must fail before any install runs.
    assertion = _interpreter_assertion_lines(graph)
    parts: list[str] = _manifest(graph, manual_blocks, include_services=include_services) + [
        "set -Eeuo pipefail",
        "",
    ] + (assertion + [""] if assertion else []) + [
        "# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.",
        'command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python',
        "",
        "# Ensure the pytest runner (fallback; also baked into v3-base). Best-effort, never aborts.",
        'python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest || true',
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
    # SOFT requirements files render after every layer section (every pinned
    # dependency is installed above, so the constraints file has the full
    # closure to protect) and before the PROJECT capstone (order matters — see
    # _soft_requirements_section's module-level docstring).
    soft_section = _soft_requirements_section(graph)
    if soft_section:
        parts.append("")
        parts.extend(soft_section)
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

    post BEFORE createdb: a setup dict that puts a CREATE USER statement in ``post``
    and a ``createdb -O <user> ...`` (owner = that just-created user) in ``createdb``
    needs the owner to exist before ``createdb -O`` runs, or it fails. A ``createdb``
    that references no user (e.g. a plain ``CREATE DATABASE``) makes the ordering a
    no-op, so post-before-createdb is always safe."""
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


# === script.py: render annotated setup.sh from blocks + parse it back (design §3.2, §7) ===
_PREAMBLE = "#!/usr/bin/env bash\nset -Eeuo pipefail\n"


def render_setup_sh(blocks: tuple[Block, ...]) -> str:
    parts = [_PREAMBLE]
    for b in blocks:
        parts.append(f"\n#@action id={b.block_id} wave={b.wave}")
        if b.target_node_ids:
            parts.append("#@targets " + " ".join(b.target_node_ids))
        if b.provider_ids:
            parts.append("#@provides " + " ".join(b.provider_ids))
        for chk in b.check_commands:
            parts.append(f"#@check {chk}")
        parts.extend(b.commands)
    return "\n".join(parts) + "\n"


def parse_setup_sh(text: str) -> tuple[Block, ...]:
    blocks: list[Block] = []
    cur: dict | None = None
    cmds: list[str] = []

    def _flush():
        if cur is not None:
            blocks.append(Block(
                block_id=cur["id"], wave=cur["wave"], commands=tuple(cmds),
                target_node_ids=tuple(cur.get("targets", ())),
                provider_ids=tuple(cur.get("provides", ())),
                check_commands=tuple(cur.get("checks", ())),
            ))

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#@action"):
            _flush()
            cmds = []
            kv = dict(tok.split("=", 1) for tok in s[len("#@action"):].split() if "=" in tok)
            cur = {"id": kv.get("id", ""), "wave": kv.get("wave", ""),
                   "targets": (), "provides": (), "checks": []}
        elif s.startswith("#@targets") and cur is not None:
            cur["targets"] = tuple(s[len("#@targets"):].split())
        elif s.startswith("#@provides") and cur is not None:
            cur["provides"] = tuple(s[len("#@provides"):].split())
        elif s.startswith("#@check") and cur is not None:
            cur["checks"].append(s[len("#@check"):].strip())
        elif s.startswith("#!") or s.startswith("set -") or not s:
            continue
        elif cur is not None:
            cmds.append(line)
    _flush()
    return tuple(blocks)
