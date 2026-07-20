from __future__ import annotations


def provisional_installs(graph: dict) -> list[dict]:
    """Extract the certified-with-provisional install set from a serialized
    ``DepGraph`` (``DepGraph.to_dict()``).

    A fallthrough install (Stage C Task 3) is a ``Package`` node whose ``data``
    carries a ``provisional`` payload: a local-collision name that did NOT import
    under the cure and was installed from PyPI anyway. Surfacing it in
    ``bench_meta.json`` lets an eval bucket the run as "certified-with-provisional"
    instead of scoring the fallthrough install as a clean pass (the named-owner the
    spec demands). Returns ``[{name, reason, cure_rung}]`` sorted by name; ``[]`` when
    the graph carries none (the overwhelmingly common case) or is empty/malformed.
    """
    out: list[dict] = []
    for node in graph.get("nodes", ()) or ():
        if not isinstance(node, dict) or node.get("type") != "Package":
            continue
        prov = (node.get("data") or {}).get("provisional")
        if not isinstance(prov, dict):
            continue
        out.append(
            {
                "name": prov.get("name") or node.get("name"),
                "reason": prov.get("reason"),
                "cure_rung": prov.get("cure_rung"),
            }
        )
    return sorted(out, key=lambda p: p.get("name") or "")
