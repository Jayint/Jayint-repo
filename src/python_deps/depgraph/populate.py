"""The single producer of node install commands for the static path.

Pure: no Docker, no network, no LLM, no src.envstate. populate_setup_commands
fills node.setup_commands for the reciped tiers (Package/SystemLib/Tool) so the
renderer can be a dumb emitter. _command_for here is the ONLY copy of the
per-node install-command logic in the static path — build_script._install_command
is deleted in favour of it.

FIX B5 (2026-07-13): a bare, unconditional ``pip install -e .`` as the LAST
line of a ``set -Eeuo pipefail`` script killed 6/12 measured build failures
AFTER 100% of the dependency closure had already installed. B5(a) tried to fix
this by PREDICTING installability from static files on disk and suppressing
the capstone entirely when the heuristic looked unsafe. B5(b)/(c) — gold's own
``-e .`` -> ``.`` fallback chain, and non-fatal wrapping for every per-node
install line — were both correct and are kept below.

FIX B6 (2026-07-14, post-B5 measurement): B5(a)'s PREDICTION was itself the
bug, proven wrong in both directions on the same benchmark run:
  * python-websockets/websockets — the heuristic correctly predicted trouble,
    but suppression was the WRONG remedy: it only masked a symptom that
    non-fatal wrapping (c) already handles for free.
  * pre-commit/pre-commit — the heuristic predicted trouble and suppressed the
    capstone, but the capstone WORKS: EBSR fell from 1.0 to 0 with nothing in
    the graph saying so, because a silently-absent project install means
    ``conftest.py`` can't import the repo and every test is lost.
``pip`` is the only real oracle for "does this install" — a static predictor
is a coin flip against it. So B6 deletes the predictor (``_looks_safely_
installable`` and its file-inspection helpers are gone; they have no
remaining caller) and ALWAYS emits the real capstone attempt, non-fatally
wrapped like every other node (b5c). Since this Python process can never
observe the outcome of a command that only runs later, inside the rendered
bash script, on a different host, ``populate_setup_commands`` no longer lets
the PROJECT node's own certificate claim success by omission either — see
``_poison_project_certificate``.
"""
from __future__ import annotations

from dataclasses import replace

from python_deps.depgraph.emit import (
    _apt_name,
    _is_installable_project,
    _is_reciped,
    _is_service_reciped,
    _pip_spec,
)
from python_deps.depgraph.schema import DepGraph, Node, NodeType, State, Strength

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
