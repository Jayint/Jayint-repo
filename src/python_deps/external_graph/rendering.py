"""Lossless flat renderer for ExternalDependencyGraphSlice.

The main implementation is in to_flat_hint_impl(); the method
ExternalDependencyGraphSlice.to_flat_hint() delegates here.

See docs/superpowers/specs/2026-06-07-python-envgraph-v1-design.md for the full design.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dto import ExternalDependencyGraphSlice


def to_flat_hint_impl(slice_: "ExternalDependencyGraphSlice") -> str:
    """Lossless linearization: one bullet per node, per edge, plus frontier bullets.

    The flat rendering intentionally carries the same information as to_dict():
    every node id and every edge (src, kind, dst) must appear. No JSON braces or
    nested structure; every non-empty line starts with '- '.
    """
    if slice_.is_empty():
        return ""

    lines: list[str] = []

    # --- Node bullets ---
    for node in slice_.nodes:
        node_id = node.get("id", "?")
        kind = node.get("kind", "")
        attrs: list[str] = []
        for attr_key in ("state", "specifier", "requires_python", "source", "trust",
                         "used_in_code", "declared"):
            val = node.get(attr_key)
            if val is not None and val != "":
                attrs.append(f"{attr_key}={val}")
        # Only emit known lean attributes. Internal keys (starting with _) are excluded from prose.
        attr_str = ", ".join(attrs)
        if kind:
            bullet = f"- {node_id} [{kind}]"
        else:
            bullet = f"- {node_id}"
        if attr_str:
            bullet += f" ({attr_str})"
        lines.append(bullet)

    # --- Edge bullets ---
    for edge in slice_.edges:
        src = edge.get("src", "?")
        dst = edge.get("dst", "?")
        kind = edge.get("kind", "?")
        lines.append(f"- {src} {kind} {dst}")

    # --- Frontier bullets ---
    frontier = dict(slice_.frontier)
    for key, value in sorted(frontier.items()):
        if isinstance(value, (list, tuple)):
            for item in value:
                lines.append(f"- frontier {key}: {item}")
        elif value is not None and value != "":
            lines.append(f"- frontier {key}: {value}")

    return "\n".join(lines)
