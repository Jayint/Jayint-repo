"""GraphML export — render a ``DepGraph`` for the existing HTML viewer.

Emits the SAME key schema as ``docs/sample-dependency-graph.graphml`` so the
output drops straight into ``docs/sample-dependency-graph-visualization.html``:

    node keys: label, type, layer, state, discovered_by, check, fix, evidence
    edge key : relation

The ``fix`` value prefers ``chosen_fix`` and falls back to the joined
``fix_candidates`` (the viewer shows a single provider string).  Empty fields are
omitted (matching the sample).  All values are XML-escaped.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from python_deps.depgraph.schema import DepGraph, Node, NodeType

# (key-id, attr.name) — ids/order mirror docs/sample-dependency-graph.graphml.
_NODE_KEYS: tuple[tuple[str, str], ...] = (
    ("d0", "label"),
    ("d1", "type"),
    ("d2", "layer"),
    ("d3", "state"),
    ("d4", "discovered_by"),
    ("d5", "check"),
    ("d6", "fix"),
    ("d7", "evidence"),
)
_EDGE_KEY: tuple[str, str] = ("e0", "relation")


def _label(node: Node) -> str:
    """Viewer label.  Package nodes pin the version (``name==version``) to match
    the design-doc sample render (e.g. ``opencv-python==4.9.0.80``); every other
    node type uses its bare name."""
    if node.type is NodeType.PACKAGE and node.version:
        return f"{node.name}=={node.version}"
    return node.name


def _fix_value(node: Node) -> str:
    if node.chosen_fix:
        return node.chosen_fix
    if node.fix_candidates:
        return "; ".join(node.fix_candidates)
    return ""


def _node_data(node: Node) -> dict[str, str]:
    return {
        "d0": _label(node),
        "d1": node.type.value,
        "d2": node.layer.value,
        "d3": node.state.value,
        "d4": node.discovered_by.value,
        "d5": node.check_command or "",
        "d6": _fix_value(node),
        "d7": node.evidence or "",
    }


def to_graphml(graph: DepGraph) -> str:
    """Serialize ``graph`` to a GraphML document string (viewer-compatible)."""
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
    ]
    for key_id, attr_name in _NODE_KEYS:
        lines.append(
            f'  <key id="{key_id}" for="node" '
            f'attr.name="{attr_name}" attr.type="string"/>'
        )
    lines.append(
        f'  <key id="{_EDGE_KEY[0]}" for="edge" '
        f'attr.name="{_EDGE_KEY[1]}" attr.type="string"/>'
    )
    lines.append('  <graph edgedefault="directed">')

    for node in graph.nodes:
        lines.append(f"    <node id={quoteattr(node.id)}>")
        data = _node_data(node)
        for key_id, _ in _NODE_KEYS:
            value = data[key_id]
            if value:
                lines.append(f'      <data key="{key_id}">{escape(value)}</data>')
        lines.append("    </node>")

    for edge in graph.edges:
        lines.append(
            f"    <edge source={quoteattr(edge.src)} target={quoteattr(edge.dst)}>"
            f'<data key="{_EDGE_KEY[0]}">{escape(edge.relation.value)}</data></edge>'
        )

    lines.append("  </graph>")
    lines.append("</graphml>")
    return "\n".join(lines) + "\n"
