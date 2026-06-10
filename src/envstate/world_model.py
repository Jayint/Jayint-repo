# src/envstate/world_model.py
"""EnvState v1 — shared world-model types and pure map helpers.

All types are frozen dataclasses (immutable).  The only mutable container is
WorldModelMap.progress (a plain dict), but merge_map() always copies it so
callers never receive an alias to a live dict.

Serialization helpers (map_to_dict / map_from_dict) let the maintainer and
planner embed the map as JSON in LLM messages.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any


# ---------------------------------------------------------------------------
# Primitive facts
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Fact:
    name: str               # e.g. "flask", "pytest", "libpq-dev"
    detail: str = ""        # version / note, taken from real command output


@dataclasses.dataclass(frozen=True)
class OpenProblem:
    signature: str          # short id, e.g. "ModuleNotFoundError: psycopg2"
    interpretation: str     # maintainer's interpretation of the failure
    layer: str              # "base" | "system" | "runtime" | "deps" | "build" | "tests"
    out_of_scope: bool = False  # set by the planner when routing around it


# ---------------------------------------------------------------------------
# The shared map (single writer: Maintainer)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class WorldModelMap:
    base_image: str
    workdir: str
    language: str                        # e.g. "python 3.12"
    build_system: str                    # "poetry" | "pip" | "hatchling" | "unknown"
    repo_layout: tuple[str, ...]         # key dirs/files: ("tests/", "src/", "pyproject.toml")
    required: tuple[Fact, ...]           # declared by manifests (not yet verified)
    installed: tuple[Fact, ...]          # confirmed present from real command results
    open_problems: tuple[OpenProblem, ...]
    progress: dict[str, bool]            # keys: base, system, runtime, deps, build, tests
    done_flag: bool = False              # True once pytest --collect-only exited 0
    notes: tuple[str, ...] = ()          # durable cautions the maintainer wants kept
    env: dict[str, str] = dataclasses.field(default_factory=dict)   # NEW: scalar probe facts


# ---------------------------------------------------------------------------
# Planner → BuildAgent message
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Task:
    goal: str               # one concrete sub-goal: "install project deps from pyproject"
    done_when: str          # checkable criterion: "pip install exits 0 and import works"
    layer: str              # which stack layer this targets
    facts: tuple[str, ...]  # relevant map facts handed down (so the agent does not re-discover)


@dataclasses.dataclass(frozen=True)
class PlannerDecision:
    action: str             # "task" | "done" | "giveup"
    task: Task | None = None
    reason: str = ""        # explanation for done/giveup


# ---------------------------------------------------------------------------
# BuildAgent → Maintainer message
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class CommandRecord:
    cmd: str
    rc: int                 # 0 = success, non-zero = failure (proxy, same as ActionLedger)
    output: str             # truncated salient output


@dataclasses.dataclass(frozen=True)
class TaskReport:
    task_goal: str
    status: str             # "done" | "blocked"
    commands: tuple[CommandRecord, ...]
    learning: str           # one line: what was learned / why blocked


# ---------------------------------------------------------------------------
# Factories and pure helpers
# ---------------------------------------------------------------------------

_PROGRESS_LAYERS: tuple[str, ...] = (
    "base", "system", "runtime", "deps", "build", "tests"
)


def initial_map(
    base_image: str,
    workdir: str,
    language: str,
    build_system: str,
    repo_layout: tuple[str, ...],
    required: tuple[Fact, ...] = (),
) -> WorldModelMap:
    """Return a fresh WorldModelMap at cycle 0.

    Sets done_flag=False, empty installed/open_problems/notes, and
    progress={layer: False} for all six known layers.
    Called once in run_v1() before the loop.
    base_image and workdir come from Synthesizer;
    language/build_system/repo_layout come from ImageSelector + repo tree scan.
    """
    return WorldModelMap(
        base_image=base_image,
        workdir=workdir,
        language=language,
        build_system=build_system,
        repo_layout=repo_layout,
        required=required,
        installed=(),
        open_problems=(),
        progress={layer: False for layer in _PROGRESS_LAYERS},
        done_flag=False,
        notes=(),
    )


def merge_map(
    current: WorldModelMap,
    *,
    installed: tuple[Fact, ...] | None = None,
    open_problems: tuple[OpenProblem, ...] | None = None,
    progress: dict[str, bool] | None = None,
    done_flag: bool | None = None,
    notes: tuple[str, ...] | None = None,
    required: tuple[Fact, ...] | None = None,
    env: dict[str, str] | None = None,
    build_system: str | None = None,
    language: str | None = None,
) -> WorldModelMap:
    """Return a new WorldModelMap with only the supplied keyword fields replaced.

    progress and env are always copied so callers never share a live dict.
    Never raises.
    """
    return dataclasses.replace(
        current,
        installed=installed if installed is not None else current.installed,
        open_problems=open_problems if open_problems is not None else current.open_problems,
        progress=dict(progress) if progress is not None else dict(current.progress),
        done_flag=done_flag if done_flag is not None else current.done_flag,
        notes=notes if notes is not None else current.notes,
        required=required if required is not None else current.required,
        env=dict(env) if env is not None else dict(current.env),
        build_system=build_system if build_system is not None else current.build_system,
        language=language if language is not None else current.language,
    )




def _derive_progress(prev: dict[str, bool], m: WorldModelMap) -> dict[str, bool]:
    """Deterministically compute layer progress from facts; monotonic vs prev.

    Clean-signal layers: base/runtime/deps/tests. Signal-less layers
    (system/build) are complete unless an unresolved open_problem targets
    them. OR-merged with prev so a layer never flips True->False mid-run.
    """
    installed_lower = {f.name.lower() for f in m.installed}
    deps_ok = bool(m.required) and all(
        r.name.lower() in installed_lower for r in m.required
    )
    open_layers = {p.layer for p in m.open_problems if not p.out_of_scope}
    computed = {
        "base": bool(m.base_image),
        "system": "system" not in open_layers,
        "runtime": bool(m.env.get("python_version")),
        "deps": deps_ok,
        "build": "build" not in open_layers,
        "tests": bool(m.done_flag),
    }
    return {
        layer: bool(prev.get(layer, False)) or bool(computed.get(layer, False))
        for layer in _PROGRESS_LAYERS
    }


def _auto_resolve_problems(
    open_problems: tuple[OpenProblem, ...],
    installed: tuple[Fact, ...],
) -> tuple[OpenProblem, ...]:
    """Drop a problem whose package name appears (case-insensitive) in its
    signature once that package is in `installed`.

    Conservative exact-name match: covers pip ModuleNotFoundError cases.
    Package-vs-import-name mismatches (psycopg2 vs psycopg2-binary) are left
    for the Maintainer's `resolved` list. Never raises.
    """
    if not installed:
        return open_problems
    names = [f.name.lower() for f in installed if f.name]
    kept: list[OpenProblem] = []
    for p in open_problems:
        sig = p.signature.lower()
        if any(name in sig for name in names):
            continue  # resolved
        kept.append(p)
    return tuple(kept)

# ---------------------------------------------------------------------------
# JSON serialization helpers (for LLM message embedding)
# ---------------------------------------------------------------------------

def _fact_to_dict(f: Fact) -> dict[str, Any]:
    return {"name": f.name, "detail": f.detail}


def _fact_from_dict(d: dict[str, Any]) -> Fact:
    return Fact(name=d["name"], detail=d.get("detail", ""))


def _open_problem_to_dict(op: OpenProblem) -> dict[str, Any]:
    return {
        "signature": op.signature,
        "interpretation": op.interpretation,
        "layer": op.layer,
        "out_of_scope": op.out_of_scope,
    }


def _open_problem_from_dict(d: dict[str, Any]) -> OpenProblem:
    return OpenProblem(
        signature=d["signature"],
        interpretation=d["interpretation"],
        layer=d["layer"],
        out_of_scope=bool(d.get("out_of_scope", False)),
    )


def map_to_dict(m: WorldModelMap) -> dict[str, Any]:
    """Serialize a WorldModelMap to a plain JSON-safe dict.

    Suitable for embedding in LLM messages via json.dumps().
    Tuples are serialized as lists (JSON has no tuple type).
    """
    return {
        "base_image": m.base_image,
        "workdir": m.workdir,
        "language": m.language,
        "build_system": m.build_system,
        "repo_layout": list(m.repo_layout),
        "required": [_fact_to_dict(f) for f in m.required],
        "installed": [_fact_to_dict(f) for f in m.installed],
        "open_problems": [_open_problem_to_dict(op) for op in m.open_problems],
        "progress": dict(m.progress),
        "done_flag": m.done_flag,
        "notes": list(m.notes),
        "env": dict(m.env),
    }


def map_from_dict(d: dict[str, Any]) -> WorldModelMap:
    """Deserialize a WorldModelMap from a plain dict (inverse of map_to_dict).

    Never mutates *d*.
    """
    return WorldModelMap(
        base_image=d["base_image"],
        workdir=d["workdir"],
        language=d["language"],
        build_system=d["build_system"],
        repo_layout=tuple(d.get("repo_layout", [])),
        required=tuple(_fact_from_dict(f) for f in d.get("required", [])),
        installed=tuple(_fact_from_dict(f) for f in d.get("installed", [])),
        open_problems=tuple(
            _open_problem_from_dict(op) for op in d.get("open_problems", [])
        ),
        progress=dict(d.get("progress", {layer: False for layer in _PROGRESS_LAYERS})),
        done_flag=bool(d.get("done_flag", False)),
        notes=tuple(d.get("notes", [])),
        env=dict(d.get("env", {})),
    )
