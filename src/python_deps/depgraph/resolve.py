"""Stage 3 — real resolver -> Package nodes.

Given a set of root distributions (each paired with the Import that surfaced
it), shell out to the ``uv`` binary via ``uv pip compile`` to produce a pinned
closure, then parse the pinned ``name==version`` lines into immutable
``Package`` nodes and the ``requires`` edges that join them.

Edges emitted:

* **Import -> Package** (always): each root Import requires its resolved
  Package.  This is the load-bearing link from static-scan output to the
  installable closure.
* **Package -> Package** (when derivable): ``uv pip compile`` annotates each
  pinned line with ``# via <parent>`` comments; when those annotations name a
  sibling distribution we emit the transitive ``requires`` edge.  Annotations
  that point at the input source (``# via -r <file>`` / stdin) are roots and
  produce no Package->Package edge.  If a build of ``uv`` ever omits
  annotations the closure simply degrades to a flat Package set with only the
  Import->Package edges (the documented fallback per the plan's locked
  decision 2).

The resolver is invoked *only* through the injected ``Executor`` (locked
decision 1: the ``uv`` binary, never imported as a module), so unit tests drive
it with a ``FakeExecutor`` and no real uv/network is touched.
"""

from __future__ import annotations

import re
import shutil

from python_deps.depgraph.executor import Executor
from python_deps.depgraph.ids import package_id
from python_deps.depgraph.schema import (
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
)

# Locked decision 1: the 'uv' binary, invoked (never imported) via the Executor.
# Resolve from PATH so the same command works on the macOS/Homebrew host AND in
# the python:3.11-slim Debian container (locked decision 3), where uv installs to
# /root/.local/bin or /usr/local/bin — never /opt/homebrew.  Falls back to the
# bare name so the constant is still meaningful if `which` finds nothing at
# import time (the executor's PATH resolves it at run time).
UV_BIN = shutil.which("uv") or "uv"

# Heredoc delimiter for feeding the root requirements on stdin.
_HEREDOC = "DEPGRAPH_REQS"

# A pinned line, e.g. ``opencv-python==4.9.0.80`` (optionally with markers we
# discard).  Names are PEP 508 distribution names.
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)")

# Tokens that mark an annotation source rather than a parent distribution.
_SOURCE_FLAGS = {"-r", "-c", "--requirement", "--constraint"}


def _canon(name: str) -> str:
    """PEP 503-style normalization for case/separator-insensitive matching."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _compile_command(dist_names: list[str], base_python: str) -> str:
    """Build the ``uv pip compile`` shell command (roots fed via heredoc stdin)."""
    reqs = "\n".join(dist_names)
    return (
        f"{UV_BIN} pip compile --python-version {base_python} - "
        f"<<'{_HEREDOC}'\n{reqs}\n{_HEREDOC}"
    )


def _parse_parents(text: str) -> list[str]:
    """Extract parent distribution names from a ``# via ...`` annotation body.

    Returns an empty list when the annotation names an input source
    (``-r``/``-c`` + filename, or a lone ``-`` for stdin) rather than a sibling
    distribution.
    """
    tokens = [t for t in re.split(r"[,\s]+", text.strip()) if t]
    parents: list[str] = []
    for tok in tokens:
        if tok in _SOURCE_FLAGS:
            # The remainder of this annotation is a filename/source, not a dep.
            return []
        if tok == "-" or tok.startswith("-"):
            continue
        parents.append(tok)
    return parents


def _parse_closure(stdout: str) -> tuple[list[tuple[str, str]], dict[str, set[str]]]:
    """Parse pinned lines + ``# via`` annotations.

    Returns ``(packages, via)`` where ``packages`` is an ordered list of
    ``(name, version)`` and ``via`` maps a child's canonical name to the set of
    its parents' canonical names (Package->Package candidates).
    """
    packages: list[tuple[str, str]] = []
    via: dict[str, set[str]] = {}
    current_canon: str | None = None
    in_via = False

    for raw in stdout.splitlines():
        stripped = raw.strip()
        if not stripped:
            in_via = False
            continue
        if stripped.startswith("#"):
            body = stripped[1:].strip()
            if body.startswith("via"):
                in_via = True
                for parent in _parse_parents(body[3:]):
                    if current_canon is not None:
                        via.setdefault(current_canon, set()).add(_canon(parent))
            elif in_via:
                # Continuation line of a multi-parent ``# via`` block.
                for parent in _parse_parents(body):
                    if current_canon is not None:
                        via.setdefault(current_canon, set()).add(_canon(parent))
            else:
                in_via = False
            continue

        in_via = False
        match = _PIN_RE.match(stripped)
        if match:
            name, version = match.group(1), match.group(2)
            packages.append((name, version))
            current_canon = _canon(name)
    return packages, via


def _package_node(name: str, version: str) -> Node:
    return Node(
        id=package_id(name, version),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        version=version,
        check_command=f"python -m pip show {name}",
        fix_candidates=(f"pip:{name}",),
        chosen_fix=f"pip:{name}",
        provenance="uv pip compile",
    )


def resolve_closure(
    roots: list[tuple[str, str]],
    executor: Executor,
    *,
    base_python: str = "3.11",
) -> tuple[list[Node], list[Edge]]:
    """Resolve ``roots`` to a pinned closure of Package nodes + requires edges.

    ``roots`` is a list of ``(import_id, dist_name)`` pairs (the shape produced
    by ``naming.package_roots``).  Returns ``(nodes, edges)``; on resolver
    failure (non-zero rc) returns ``([], [])`` — the caller leaves the imports
    uncertified rather than fabricating a closure.
    """
    dist_names = [dist for _import_id, dist in roots]
    if not dist_names:
        return [], []

    command = _compile_command(dist_names, base_python)
    result = executor.run(command)
    if not result.ok:
        return [], []

    packages, via = _parse_closure(result.stdout)

    nodes: list[Node] = []
    canon_to_id: dict[str, str] = {}
    for name, version in packages:
        node = _package_node(name, version)
        nodes.append(node)
        canon_to_id[_canon(name)] = node.id

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    def _add_edge(src: str, dst: str) -> None:
        key = (src, dst)
        if src == dst or key in seen:
            return
        seen.add(key)
        edges.append(Edge(src=src, dst=dst, relation=EdgeType.REQUIRES, origin="resolver"))

    # Import -> Package (always).
    for import_id, dist in roots:
        pkg_id = canon_to_id.get(_canon(dist))
        if pkg_id is not None:
            _add_edge(import_id, pkg_id)

    # Package -> Package (transitive, from uv annotations when present).
    for child_canon, parents in via.items():
        child_id = canon_to_id.get(child_canon)
        if child_id is None:
            continue
        for parent_canon in parents:
            parent_id = canon_to_id.get(parent_canon)
            if parent_id is not None:
                _add_edge(parent_id, child_id)

    return nodes, edges
