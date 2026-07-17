"""Low shared node-recipe predicates: classification + pip/apt string builders.

These decide, from a Node alone, whether the deterministic recipe layer can
install it (``_is_reciped``/``_is_service_reciped``/``_is_installable_project``)
and how to render its install string (``_apt_name``/``_pip_spec``). They are the
pip/apt-string half of the emit concern, factored OUT of ``emit.py`` so both the
emit walk AND the mutate side (``block.py``) import them *downward* — this is what
dissolves the ``block(mutate) -> emit`` inversion and the future
``emit <-> commands`` cycle. Pure: depends only on ``schema`` (Node/NodeType);
no graph walk, no Docker, no network, no env reads.
"""
from __future__ import annotations

from python_deps.depgraph.schema import Node, NodeType

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
