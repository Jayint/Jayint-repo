# Diagnosis Router & Guards — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the diagnosis-first *routing layer* and the local-import guard from the `diagnosis-first-graph-seeded-script-repair-loop` design onto v3-core's already-working typed loop, so a failure is classified into a mode {environment / repo-internal-reference / residual / invalid-attempt / ambiguous} and a repo-local import is never mis-ingested as a PyPI package.

**Architecture:** v3-core already implements ~11 of the doc's 13 loop stages (diagnosis ReAct in `v3_build_agent.propose`, host-only `certify`, regenerate-from-graph each cycle, soft-hints-unexecuted). The genuine gap is a *typed router* in front of the runtime classifier. We add one pure module, `python_deps/depgraph/diagnose.py`, that wraps the existing pure `classify_observation` with repository context (local module names, disproven names) and returns a `Diagnosis(mode, discovery, reason)`. We keep the typed `PatchProposal` inner loop untouched (per decision: "keep typed, add the doc's guards"). Phase 1 changes exactly one runtime behavior — dropping repo-local imports — via the existing `ingest_runtime_failures(classifiers=...)` seam; the mode metadata is produced now and consumed by the orchestrator in Phase 2.

**Tech Stack:** Python 3, stdlib `enum`/`dataclasses`, pytest. No new dependencies.

## Global Constraints

- **Pure module, no `src.envstate` imports** in `diagnose.py` — same rule as its siblings `runtime_classify.py` / `runtime_ingest.py` (unit-testable with plain strings). Copy verbatim from `runtime_classify.py:2-3`.
- **Immutability:** all new dataclasses are `@dataclass(frozen=True)`; functions return new objects, never mutate (`rules/python/coding-style.md`).
- **Do NOT add a new `State` value.** `State` stays `{UNKNOWN, MISSING, SATISFIED}` and remains host-certification truth (`certify.py:81` is the sole `SATISFIED` writer). The trust ladder is *derived* from existing fields (`Strength`, `promotion`) + the new `Mode`; `invalid` is carried as a name-set, not a node state, in Phase 1.
- **Reuse, do not duplicate:** import `_local_module_names` logic from `scan.py` and the mapping/classification from `classify_observation` — never re-implement import→package mapping.
- **Never raise into the loop:** `ingest_runtime_failures` already wraps each observation in try/except (`runtime_ingest.py:185`); classifiers must return `None`, not raise, on a non-match.
- **Type annotations on every signature; `from __future__ import annotations` at top of every file.**

---

### Task 1: Diagnosis types + local-import predicate

**Files:**
- Modify: `src/python_deps/depgraph/scan.py` (expose the existing private helper)
- Create: `src/python_deps/depgraph/diagnose.py`
- Test: `tests/depgraph/test_diagnose_types.py`

**Interfaces:**
- Consumes: `python_deps.depgraph.scan._local_module_names` (existing, `scan.py:74`).
- Produces:
  - `scan.local_module_names(repo_path: str) -> frozenset[str]` (public alias).
  - `diagnose.Mode` — `enum.Enum` with members `ENVIRONMENT`, `REPO_INTERNAL_REF`, `RESIDUAL`, `INVALID_ATTEMPT`, `AMBIGUOUS`.
  - `diagnose.RepoContext` — frozen dataclass `{local_names: frozenset[str] = frozenset(), invalid_names: frozenset[str] = frozenset()}`.
  - `diagnose.Diagnosis` — frozen dataclass `{mode: Mode, discovery: Discovery | None, reason: str}` (`Discovery` from `runtime_classify`).
  - `diagnose.is_local_import(import_name: str, local_names: frozenset[str]) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_diagnose_types.py
"""Tests for diagnose types + is_local_import (pure, no Docker)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.diagnose import (
    Diagnosis, Mode, RepoContext, is_local_import,
)
from python_deps.depgraph.scan import local_module_names


def test_repo_context_defaults_are_empty():
    ctx = RepoContext()
    assert ctx.local_names == frozenset()
    assert ctx.invalid_names == frozenset()


def test_is_local_import_matches_top_level_segment():
    local = frozenset({"docs_src", "myapp"})
    assert is_local_import("docs_src", local) is True
    assert is_local_import("docs_src.helpers", local) is True   # dotted -> top segment
    assert is_local_import("requests", local) is False
    assert is_local_import("", local) is False


def test_public_local_module_names_delegates(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "solo.py").write_text("x = 1")
    names = local_module_names(str(tmp_path))
    assert "pkg" in names
    assert "solo" in names


def test_diagnosis_is_frozen():
    d = Diagnosis(mode=Mode.AMBIGUOUS, discovery=None, reason="x")
    import dataclasses
    assert dataclasses.is_dataclass(d)
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.reason = "y"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python -m pytest tests/depgraph/test_diagnose_types.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.diagnose'` (and `ImportError` for `local_module_names`).

- [ ] **Step 3a: Expose the public alias in `scan.py`**

Add immediately after `_local_module_names` (after `scan.py:93`):

```python
def local_module_names(repo_path: str) -> frozenset[str]:
    """Public alias for :func:`_local_module_names` (used by the diagnosis router)."""
    return _local_module_names(repo_path)
```

- [ ] **Step 3b: Create `diagnose.py` with the types + predicate**

```python
# src/python_deps/depgraph/diagnose.py
"""Diagnosis router (design 2026-07-01 diagnosis-first loop).

Pure module — no src.envstate imports. Unit-testable with plain strings.

Wraps the pure runtime classifier with repository context so the loop can
DISTINGUISH an environment requirement from a repo-internal reference, a
residual bug, or a disproven attempt — and so a repo-local import is never
mis-added as a PyPI package (the design's single highest-value guard).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from python_deps.depgraph.runtime_classify import Discovery


class Mode(enum.Enum):
    ENVIRONMENT = "environment"                    # real env requirement -> ingest + repair
    REPO_INTERNAL_REF = "repo_internal_reference"  # local import/path -> out of scope
    RESIDUAL = "residual"                          # assertion/logic bug -> non-env give-up
    INVALID_ATTEMPT = "invalid_attempt"            # pip disproved this name -> do not retry
    AMBIGUOUS = "ambiguous"                        # unclear -> probe then reclassify


@dataclass(frozen=True)
class RepoContext:
    local_names: frozenset[str] = field(default_factory=frozenset)
    invalid_names: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Diagnosis:
    mode: Mode
    discovery: Discovery | None   # populated only when mode is ENVIRONMENT
    reason: str


def is_local_import(import_name: str, local_names: frozenset[str]) -> bool:
    """True when ``import_name`` (or its top-level package) is defined in the repo.

    ``local_names`` are basenames from ``scan.local_module_names`` (dirs with
    ``__init__.py`` and top-level ``*.py`` stems), so compare the first dotted
    segment: ``docs_src.helpers`` is local iff ``docs_src`` is local.
    """
    if not import_name:
        return False
    return import_name.split(".", 1)[0] in local_names
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python -m pytest tests/depgraph/test_diagnose_types.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/scan.py src/python_deps/depgraph/diagnose.py tests/depgraph/test_diagnose_types.py
git commit -m "feat(depgraph): diagnosis Mode/RepoContext/Diagnosis types + is_local_import guard"
```

---

### Task 2: `diagnose()` router core

**Files:**
- Modify: `src/python_deps/depgraph/diagnose.py`
- Test: `tests/depgraph/test_diagnose_router.py`

**Interfaces:**
- Consumes:
  - `python_deps.failure_classifier.classify_dependency_failure(command, observation) -> DependencyFailure` (fields used: `.failure_type`, `.import_name`, `.package_name`; `failure_type` values: `module_not_found`, `import_name_error`, `no_matching_distribution`, `native_library_missing`, `dependency_conflict`, `syntax_requires_newer_python`, `not_dependency_related`).
  - `python_deps.depgraph.runtime_classify.classify_observation(command, output) -> Discovery | None` (owns import→package mapping and native/service/config/tool routing).
  - `Mode`, `RepoContext`, `Diagnosis`, `is_local_import` from Task 1.
- Produces: `diagnose.diagnose(command: str, output: str, ctx: RepoContext) -> Diagnosis`.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_diagnose_router.py
"""Tests for diagnose.diagnose routing (pure, no Docker)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.diagnose import Mode, RepoContext, diagnose
from python_deps.depgraph.schema import NodeType


def test_external_import_routes_environment_with_discovery():
    d = diagnose("python app.py",
                 "ModuleNotFoundError: No module named 'requests'",
                 RepoContext())
    assert d.mode is Mode.ENVIRONMENT
    assert d.discovery is not None
    assert d.discovery.node_type is NodeType.PACKAGE


def test_local_import_routes_repo_internal_ref_no_discovery():
    ctx = RepoContext(local_names=frozenset({"docs_src"}))
    d = diagnose("python -m docs_src.build",
                 "ModuleNotFoundError: No module named 'docs_src'",
                 ctx)
    assert d.mode is Mode.REPO_INTERNAL_REF
    assert d.discovery is None


def test_no_matching_distribution_routes_invalid_attempt():
    d = diagnose("pip install frobnicate9000",
                 "ERROR: No matching distribution found for frobnicate9000",
                 RepoContext())
    assert d.mode is Mode.INVALID_ATTEMPT
    assert d.discovery is None


def test_previously_invalid_name_routes_invalid_attempt():
    # An external import whose mapped package was already disproven.
    ctx = RepoContext(invalid_names=frozenset({"requests"}))
    d = diagnose("python app.py",
                 "ModuleNotFoundError: No module named 'requests'",
                 ctx)
    assert d.mode is Mode.INVALID_ATTEMPT
    assert d.discovery is None


def test_native_lib_routes_environment_systemlib():
    d = diagnose("python app.py",
                 "ImportError: libGL.so.1: cannot open shared object file",
                 RepoContext())
    assert d.mode is Mode.ENVIRONMENT
    assert d.discovery is not None
    assert d.discovery.node_type is NodeType.SYSTEM_LIB


def test_assertion_routes_residual():
    d = diagnose("python -m pytest -q",
                 "E       assert 1 == 2\nAssertionError",
                 RepoContext())
    assert d.mode is Mode.RESIDUAL
    assert d.discovery is None


def test_unclassified_routes_ambiguous():
    d = diagnose("python app.py", "Segmentation fault (core dumped)", RepoContext())
    assert d.mode is Mode.AMBIGUOUS
    assert d.discovery is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python -m pytest tests/depgraph/test_diagnose_router.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'diagnose'` / `ImportError`.

- [ ] **Step 3: Implement `diagnose()`**

Append to `src/python_deps/depgraph/diagnose.py` (add `import re` to the top-of-file imports, and the new import line for `classify_dependency_failure`):

```python
import re  # add to the existing import block at top of file

from python_deps.failure_classifier import classify_dependency_failure
from python_deps.depgraph.runtime_classify import classify_observation

# An assertion / logic failure is a residual (non-environment) bug: the graph
# cannot close it by adding a node. Conservative — anything else stays AMBIGUOUS.
_RESIDUAL_RE = re.compile(r"\bAssertionError\b")

# failure_type values the router treats as import-shaped (candidate packages).
_IMPORT_FAILURE_TYPES = frozenset({"module_not_found", "import_name_error"})


def diagnose(command: str, output: str, ctx: RepoContext) -> Diagnosis:
    """Classify one (command, output) failure into a routing Mode.

    Only ``Mode.ENVIRONMENT`` carries a ``Discovery`` (produced by the existing
    ``classify_observation``, which owns import->package mapping). Every other
    mode carries ``discovery=None`` and a human-readable ``reason``.
    """
    text = output or ""
    dep = classify_dependency_failure(command, text)

    # pip already proved this distribution does not exist -> never retry the name.
    if dep.failure_type == "no_matching_distribution":
        name = dep.package_name or ""
        return Diagnosis(Mode.INVALID_ATTEMPT, None,
                         f"pip found no matching distribution for {name!r}")

    # Import failures split three ways: repo-local (out of scope), previously
    # disproven (invalid), or a genuine external package requirement.
    if dep.failure_type in _IMPORT_FAILURE_TYPES:
        import_name = dep.import_name or ""
        if is_local_import(import_name, ctx.local_names):
            return Diagnosis(Mode.REPO_INTERNAL_REF, None,
                             f"{import_name!r} resolves to a repo-local module")
        disc = classify_observation(command, text)
        if disc is None:
            return Diagnosis(Mode.AMBIGUOUS, None,
                             f"import {import_name!r} had no package mapping")
        if disc.name in ctx.invalid_names:
            return Diagnosis(Mode.INVALID_ATTEMPT, None,
                             f"package {disc.name!r} was previously disproven")
        return Diagnosis(Mode.ENVIRONMENT, disc,
                         f"external import {import_name!r} -> package requirement")

    # Native lib / service / config / tool: reuse the classifier verbatim.
    disc = classify_observation(command, text)
    if disc is not None:
        return Diagnosis(Mode.ENVIRONMENT, disc,
                         f"{disc.node_type.value.lower()} requirement")

    # Nothing environment-shaped matched. Distinguish residual from ambiguous.
    if _RESIDUAL_RE.search(text):
        return Diagnosis(Mode.RESIDUAL, None, "assertion failure — non-environment residual")
    return Diagnosis(Mode.AMBIGUOUS, None, "unclassified failure — probe before repair")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python -m pytest tests/depgraph/test_diagnose_router.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/diagnose.py tests/depgraph/test_diagnose_router.py
git commit -m "feat(depgraph): diagnose() router — env/local/invalid/residual/ambiguous modes"
```

---

### Task 3: Wire the guard into `ingest_runtime_failures` via the classifiers seam

**Files:**
- Modify: `src/python_deps/depgraph/diagnose.py`
- Test: `tests/depgraph/test_diagnose_ingest_guard.py`

**Interfaces:**
- Consumes: `diagnose()`, `RepoContext`, `Mode` (Task 2); `python_deps.depgraph.runtime_ingest.ingest_runtime_failures(graph, observations, classifiers=..., owner_node_id=None)` (existing seam — `classifiers: Sequence[Callable[[str, str], Discovery | None]]`).
- Produces: `diagnose.make_diagnostic_classifier(ctx: RepoContext) -> Callable[[str, str], Discovery | None]` — a classifier that yields a `Discovery` only for `Mode.ENVIRONMENT`, else `None`.

This is the one behavior change Phase 1 lands in the runtime path: a repo-local import (`Mode.REPO_INTERNAL_REF`) now returns `None`, so `ingest_runtime_failures` appends no bogus package node. Genuine external imports are unaffected.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_diagnose_ingest_guard.py
"""The diagnostic classifier drops repo-local imports at ingest (pure, no Docker)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.diagnose import RepoContext, make_diagnostic_classifier
from python_deps.depgraph.ids import TEST_NODE_ID, package_id
from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType,
)


def _base_graph() -> DepGraph:
    return DepGraph().with_node(
        Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
             layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL)
    )


def test_local_import_is_not_ingested_as_package():
    ctx = RepoContext(local_names=frozenset({"docs_src"}))
    classifier = make_diagnostic_classifier(ctx)
    obs = [("python -m docs_src.build",
            "ModuleNotFoundError: No module named 'docs_src'")]
    new_graph, found = ingest_runtime_failures(_base_graph(), obs, classifiers=(classifier,))
    assert new_graph.get(package_id("docs_src", None)) is None
    assert found == []


def test_external_import_is_still_ingested():
    ctx = RepoContext(local_names=frozenset({"docs_src"}))
    classifier = make_diagnostic_classifier(ctx)
    obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
    new_graph, found = ingest_runtime_failures(_base_graph(), obs, classifiers=(classifier,))
    assert new_graph.get(package_id("requests", None)) is not None
    assert len(found) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python -m pytest tests/depgraph/test_diagnose_ingest_guard.py -q`
Expected: FAIL with `ImportError: cannot import name 'make_diagnostic_classifier'`.

- [ ] **Step 3: Implement the factory**

Append to `src/python_deps/depgraph/diagnose.py` (add `Callable` import):

```python
from collections.abc import Callable  # add to the existing import block


def make_diagnostic_classifier(ctx: RepoContext) -> Callable[[str, str], Discovery | None]:
    """Adapt :func:`diagnose` to the ``ingest_runtime_failures`` classifiers seam.

    Returns a Discovery only for ``Mode.ENVIRONMENT``; every other mode
    (repo-internal-reference, residual, invalid-attempt, ambiguous) returns
    ``None`` so no node is appended. The router's mode/reason are consumed by
    the orchestrator in Phase 2; Phase 1 uses only the ENVIRONMENT/else split.
    """
    def _classify(command: str, output: str) -> Discovery | None:
        return diagnose(command, output, ctx).discovery
    return _classify
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python -m pytest tests/depgraph/test_diagnose_ingest_guard.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full runtime-path suite to confirm no regression**

Run: `cd /Users/john/john-planner-v3-core && python -m pytest tests/depgraph/ -q`
Expected: PASS (existing runtime_ingest / runtime_classify tests unaffected — the default `classifiers=(classify_observation,)` path is unchanged; the guard is opt-in via `make_diagnostic_classifier`).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/diagnose.py tests/depgraph/test_diagnose_ingest_guard.py
git commit -m "feat(depgraph): make_diagnostic_classifier — drop repo-local imports at ingest"
```

---

## Phase 2+ (outline — not yet detailed; each needs its own plan)

Do not implement below without a follow-up detailed plan. Entry condition for Phase 2: Phase 1 merged and green.

- **Orchestrator mode-routing.** Feed `RepoContext` (from `scan.local_module_names(repo_path)` + accumulated disproven names) into the loop and consume `Diagnosis.mode` at the failure hand-off. Insertion point identified: after `block_emit()` returns `(graph, _bundle, _failed)` at `src/envstate/orchestrator.py:523`, before `run_structured_repair()` (~line 527). Route `ENVIRONMENT` → existing typed repair; `REPO_INTERNAL_REF`/`RESIDUAL` → honest give-up / out-of-scope record (do not loop); `INVALID_ATTEMPT` → add name to the existing `known_invalid` frozenset (`repair_loop.py:18,26,32`); `AMBIGUOUS` → the existing read-only probe turns in `v3_build_agent.propose`.
- **Persistent `invalid` + trust-level render enforcement.** Enforce "only active/certified nodes alter setup.sh" at render (`emit`/`build_script`) by filtering on `strength`/`promotion`; persist disproven attempts so they survive script regeneration. Reconcile the doc's 5-level ladder onto existing `State`+`Strength`+`promotion` rather than a new enum.
- **Disposable candidate script checkpoint.** Discard-and-regenerate-from-certified-graph when the candidate script becomes contradictory.
- **Final fresh replay.** Wire the gate-ladder stage-2.5 fresh-from-base replay as the terminal certification (see `specs/2026-06-30-gate-ladder-stage2.5-syslib-repair-evidence-problem.md`).

---

## Self-Review

- **Spec coverage (design doc → task):** local-import guard (design "Simple V1 Grounding", "single most valuable guard") → Task 1+3. DiagnosisRouter modes (design "Why Diagnose Before Agent Repair") → Task 2. Policy for "No matching distribution → mark attempt invalid, don't add" (design "V1 policy") → Task 2 (`INVALID_ATTEMPT`). Residual vs env split → Task 2. Remaining design sections (ScriptGate, CommitGate, disposable script, fresh replay, trust-level enforcement) are explicitly deferred to Phase 2+ with entry conditions — not silently dropped.
- **Placeholder scan:** none — every step has runnable code and exact commands.
- **Type consistency:** `Discovery` is the same type throughout (from `runtime_classify`); `make_diagnostic_classifier` returns `Callable[[str,str], Discovery | None]`, matching the `ingest_runtime_failures` `classifiers` seam signature; `DependencyFailure` fields referenced (`failure_type`, `import_name`, `package_name`) are exactly those set in `failure_classifier.py:33-121`.
- **Behavioral honesty:** Phase 1 changes exactly one runtime behavior (dropping repo-local imports); `no_matching_distribution`/assertion inputs already return `None` from `classify_observation` today, so the router adds *mode metadata* for Phase 2 without changing their current drop behavior. Stated plainly so a reviewer isn't misled into expecting more.
