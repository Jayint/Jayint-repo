"""Per-stage construction tracer — watch a real repo flow through every boundary.

The construction pipeline (``graph.python.pipeline``) is a linear sequence of pure
``graph -> graph`` transforms with explicit numbered stages (Stage 1 scan, Phase-A
resolve, Stage 4a relink, Stage 4.5 ldd, ...).  This module wraps each stage WHERE
IT IS LOOKED UP (a module-level name in ``pipeline`` / ``orchestrate``), captures
its return value in call order, and diffs successive graph snapshots so a real run
shows exactly what each boundary ADDED and which node STATES / ``unresolved`` flags
it FLIPPED.

Design constraints honoured:

* **Zero production edit.**  Wrapping is done by (temporarily) rebinding the
  module-level name the pipeline calls, so ``pipeline.py`` / ``orchestrate.py`` are
  untouched — the byte-identical / purity guarantees the build path is obsessive
  about are preserved.  The tracer OBSERVES the real pipeline; it never
  reimplements the stage order (a reimplementation would silently drift).
* **Fail loud on drift.**  If a stage name this tracer wants to wrap no longer
  exists (renamed / removed), :class:`StageTracer` raises instead of silently
  capturing fewer stages.

Nothing here is imported by the build path; it is a debug tool driven by
``scripts/dump_stages.py``.
"""

from __future__ import annotations

import functools
import importlib
from dataclasses import dataclass, field

from graph.model import DepGraph

# --------------------------------------------------------------------------- #
# The numbered construction stages, in pipeline order.  Each entry is
# (module_qualname, attr_name, human_label).  All live on ``graph.python.pipeline``
# (where the phase bodies call them) except host certification, which the
# orchestrator calls on ``graph.core.orchestrate``.  Keep this list aligned with
# the numbered stage comments in pipeline.py / orchestrate.py.
# --------------------------------------------------------------------------- #
DEFAULT_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("graph.python.pipeline", "scan_to_nodes",            "01 P1.1   scan -> Import/Test"),
    ("graph.python.pipeline", "detect_target_env",        "02 P1.1.5 detect target-env"),
    ("graph.python.pipeline", "select_roots",             "03 P1.2   declared roots"),
    ("graph.python.pipeline", "_phase_a_fixpoint",        "04 P1.A   resolve+install+repair"),
    ("graph.python.pipeline", "_add_project_node",        "05 P1.3a  project node"),
    ("graph.python.pipeline", "add_subprocess_tool_nodes","06 P1.3a  subprocess tools"),
    ("graph.python.pipeline", "seed_runtime_tools",       "06b P1.3a runtime-tool prior"),
    ("graph.python.pipeline", "seed_wheel_oracle_prior",  "07 P1.3b  wheel-oracle prior"),
    ("graph.python.pipeline", "wheel_preflight_probe",    "08 P1.3b' wheel preflight (host)"),
    ("graph.python.pipeline", "seed_build_deps",          "09 P1.3b\" sdist build-deps"),
    ("graph.python.pipeline", "project_native_obligations","10 P1.3b* project native"),
    ("graph.python.pipeline", "certified_import_links",   "11 P2.4a  relink (module map)"),
    ("graph.python.pipeline", "ldd_probe",                "12 P2.4.5 ldd (DT_NEEDED)"),
    ("graph.python.pipeline", "import_probe",             "13 P2     import-probe (dlopen)"),
    ("graph.python.pipeline", "add_ctypes_runtime_libs",  "13b P2    ctypes-scan (dlopen)"),
    ("graph.python.pipeline", "reconcile_apt_names",      "14 P2.4b  apt reconcile"),
    ("graph.core.orchestrate", "certify_all",             "15 P5     host certify"),
)


@dataclass
class StageRecord:
    """One captured stage call: its label, the wrapped fn name, a coarse ``kind``
    (``graph`` | ``roots`` | ``meta``), and the raw return ``value``."""

    label: str
    fn: str
    kind: str
    value: object


def _classify(result: object) -> str:
    if isinstance(result, DepGraph):
        return "graph"
    if isinstance(result, list):
        return "roots"
    return "meta"


# --------------------------------------------------------------------------- #
# Pure snapshot + diff core (unit-tested; no I/O, no container).
# --------------------------------------------------------------------------- #
def snapshot_graph(graph: DepGraph) -> dict:
    """A compact, diffable snapshot of ``graph``: per-node key fields (including the
    ``discovered_cycle`` stage stamp and the relink ``unresolved`` flag), plus
    counts by type/state and the edge list."""
    nodes: dict[str, dict] = {}
    by_type: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for n in graph.nodes:
        unresolved = n.data.get("unresolved") if "unresolved" in n.data else None
        nodes[n.id] = {
            "id": n.id,
            "type": n.type.value,
            "name": n.name,
            "layer": n.layer.value,
            "state": n.state.value,
            "discovered_by": n.discovered_by.value,
            "cycle": n.discovered_cycle,
            "version": n.version,
            "build_from_source": n.build_from_source,
            "unresolved": unresolved,
        }
        by_type[n.type.value] = by_type.get(n.type.value, 0) + 1
        by_state[n.state.value] = by_state.get(n.state.value, 0) + 1
    edges = [[e.src, e.dst, e.relation.value] for e in graph.edges]
    return {
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "by_type": by_type,
        "by_state": by_state,
        "nodes": nodes,
        "edges": edges,
    }


def diff_snapshots(prev: dict, curr: dict) -> dict:
    """What changed from ``prev`` to ``curr``: nodes added/removed, ``state``
    transitions, ``unresolved``-flag flips, and edges added.  Node order follows
    ``curr`` insertion order (i.e. graph node order) so output is deterministic."""
    prev_nodes = prev["nodes"]
    curr_nodes = curr["nodes"]

    added = [
        {"id": i, "type": curr_nodes[i]["type"], "name": curr_nodes[i]["name"]}
        for i in curr_nodes
        if i not in prev_nodes
    ]
    removed = [
        {"id": i, "type": prev_nodes[i]["type"], "name": prev_nodes[i]["name"]}
        for i in prev_nodes
        if i not in curr_nodes
    ]

    state_changes: list[dict] = []
    unresolved_changes: list[dict] = []
    for i in curr_nodes:
        if i not in prev_nodes:
            continue
        p, c = prev_nodes[i], curr_nodes[i]
        if p["state"] != c["state"]:
            state_changes.append({"id": i, "from": p["state"], "to": c["state"]})
        if p["unresolved"] != c["unresolved"]:
            unresolved_changes.append(
                {"id": i, "from": p["unresolved"], "to": c["unresolved"]}
            )

    prev_edges = {tuple(e) for e in prev["edges"]}
    added_edges = [e for e in curr["edges"] if tuple(e) not in prev_edges]

    return {
        "added": added,
        "removed": removed,
        "state_changes": state_changes,
        "unresolved_changes": unresolved_changes,
        "added_edges": added_edges,
    }


def _roots_to_list(roots: list) -> list:
    """Normalize a ``select_roots`` return (list of ``(import_id, dist)`` tuples)
    into JSON-friendly ``[import_id, dist]`` pairs."""
    out = []
    for item in roots:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            out.append([item[0], item[1]])
        else:
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# The tracer: wrap -> capture -> restore.
# --------------------------------------------------------------------------- #
class StageTracer:
    """Context manager that wraps each stage in ``targets`` (default: the real
    pipeline stage list) to capture its return value in call order.

    On ``__enter__`` every target's module-level name is rebound to a transparent
    wrapper; on ``__exit__`` the originals are restored.  A target whose name is
    absent raises ``AttributeError`` (drift guard) after undoing any partial
    patches.  ``.records`` holds the captured :class:`StageRecord`s; ``build_trace``
    turns them into per-stage snapshot+diff dicts.
    """

    def __init__(self, targets: "tuple | list | None" = None) -> None:
        self.targets = DEFAULT_TARGETS if targets is None else targets
        self.records: list[StageRecord] = []
        self._saved: list[tuple[object, str, object]] = []

    def _wrap(self, orig, fn: str, label: str):
        records = self.records

        @functools.wraps(orig)
        def wrapper(*args, **kwargs):
            result = orig(*args, **kwargs)
            records.append(
                StageRecord(label=label, fn=fn, kind=_classify(result), value=result)
            )
            return result

        return wrapper

    def __enter__(self) -> "StageTracer":
        for module_qual, attr, label in self.targets:
            mod = importlib.import_module(module_qual)
            if not hasattr(mod, attr):
                self._restore()
                raise AttributeError(
                    f"stage target {module_qual}.{attr} not found "
                    "(construction pipeline drifted — update DEFAULT_TARGETS)"
                )
            orig = getattr(mod, attr)
            self._saved.append((mod, attr, orig))
            setattr(mod, attr, self._wrap(orig, attr, label))
        return self

    def __exit__(self, *exc) -> bool:
        self._restore()
        return False

    def _restore(self) -> None:
        for mod, attr, orig in reversed(self._saved):
            setattr(mod, attr, orig)
        self._saved = []

    def build_trace(self) -> list[dict]:
        """Per-stage view: each ``graph`` record carries its snapshot + a diff vs
        the previous graph (baselined against the empty graph); ``roots``/``meta``
        records carry their own payload."""
        out: list[dict] = []
        prev = snapshot_graph(DepGraph())
        for rec in self.records:
            if rec.kind == "graph":
                snap = snapshot_graph(rec.value)
                out.append(
                    {
                        "label": rec.label,
                        "fn": rec.fn,
                        "kind": "graph",
                        "snapshot": snap,
                        "diff": diff_snapshots(prev, snap),
                    }
                )
                prev = snap
            elif rec.kind == "roots":
                out.append(
                    {
                        "label": rec.label,
                        "fn": rec.fn,
                        "kind": "roots",
                        "roots": _roots_to_list(rec.value),
                    }
                )
            else:
                out.append(
                    {
                        "label": rec.label,
                        "fn": rec.fn,
                        "kind": "meta",
                        "repr": repr(rec.value)[:200],
                    }
                )
        return out
