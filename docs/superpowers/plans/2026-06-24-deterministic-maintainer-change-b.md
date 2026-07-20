# Deterministic Maintainer (Change B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the LLM Maintainer with a deterministic host module that extracts blockers from command output using verbatim signatures + correct layers, so the *existing* auto-resolve machinery (currently dormant due to LLM paraphrasing) fires and retires blockers after a correct fix.

**Architecture:** A new `maintain(current_map, report) -> WorldModelMap` that is a drop-in for `Maintainer.update()`. It does exactly two things: (1) build a `scope="host"` GraphPatch of Blocker + Contract + `violates` nodes from the report's command output (the new part), and (2) compute the done-gate and return `merge_map(...)` (reuse). **Attempts and their outcomes are NOT touched** — the orchestrator already commits them (`orchestrator.py:167-177`) and classifies outcomes (`orchestrator.py:199-232`) before `update()`/`maintain()` is called. A duck-typed adapter swaps the LLM Maintainer for the deterministic one behind a flag; the orchestrator needs zero changes.

**Tech Stack:** Python 3.11, frozen dataclasses, pytest. No new deps. No Docker/network in any unit test.

## Global Constraints

- **Drop-in contract:** `maintain(current_map: WorldModelMap, report: TaskReport) -> WorldModelMap`. Output is `merge_map(current_map, done_flag=..., progress=..., contract_graph=...)` — matching `Maintainer.update()` (`maintainer.py:665`). It MUST NOT touch `installed`, `required`, `env`, `system_installed`, `base_image`, `workdir`, `language`, `build_system`, `repo_layout`, `dep_advisory`, `dep_graph` (owned by `apply_deterministic`, run by the orchestrator before the call).
- **maintain() does NOT handle attempts.** Attempt nodes + outcome classification are the orchestrator's job and already deterministic. Do not call `commit_attempt`/`derive_attempt_outcome` from `maintain()`.
- **BLOCKER/CONTRACT FIELD SCHEMA is the correctness heart** (this is *the* change). For every extracted failure:
  - **Contract** `data`: `subject` = **verbatim** regex capture (e.g. `"pg_config"`, never paraphrased); `layer` from the fixed map `{python_import→deps, binary→system, system_library→system}`; `kind` = contract kind; `level="atomic"`; `check=""`; `source_refs=[f"signature:{sig[:60]}"]`; `evidence_refs=[]`; `description`; `metadata={}`. Id = `ids.contract_id(contract_kind, subject)`.
  - **Blocker** `data`: `signature` = **verbatim** matched line (must match `_SYS_ARTIFACT_PATTERNS`); `layer` = same `"system"`/`"deps"` (**explicit — never rely on the `"deps"` default**); `active=True`; `kind` = blocker kind; `subject`; `summary=f"{kind}: {subject}"`; `root_or_downstream="root"`; `evidence_refs=[]`; `metadata={}`. Id = `ids.blocker_id(signature)`.
  - **Edge**: `Edge(blocker_id, "violates", contract_id)`.
- **Validation scope:** validate with `scope="host"` (`contracts/validation.py`). `scope="maintainer"` rejects host blockers (empty `evidence_refs`). Apply with `apply_patch` (which does not validate).
- **Idempotent:** dedupe by `graph.has_node(id)` + a `seen` set; `apply_patch` setdefaults dup ids. Re-emitting the same signature across cycles is a no-op.
- **Behind a flag:** `DOCKERAGENT_DETERMINISTIC_MAINTAINER` (default off). Off → the LLM `Maintainer` runs exactly as today (off-state byte-identical).
- **Preserve diagnosis:** put `report.learning` into the patch's `diagnostic_notes` (rendered for the Planner; no scope restriction).
- **Do NOT** change `derive_attempt_outcome`'s no-target default in this plan (keep the A/B clean — see Deferred).
- **Python style / tests:** PEP 8, type annotations, `from __future__ import annotations`. Place tests at `tests/` root with the `sys.path` shim used by `tests/depgraph/conftest.py` (no `tests/envstate/` package exists). Do NOT run import-pruning autofix between commits.

## File Structure

- **Create:** `src/envstate/deterministic_maintainer.py` — `build_blocker_patch`, `maintain`, and the `DeterministicMaintainer` adapter class.
- **Create:** `tests/test_deterministic_maintainer.py`.
- **Modify:** `agent.py` — flag plumbing (4 sites) + the adapter branch at the Maintainer construction (~line 1109).
- **Modify:** `multi_docker_eval_adapter.py` — env-var pickup (~line 776) + pass-through.
- **Test:** the above test file covers all three tasks.

---

## Task 1: Deterministic blocker extraction (pure)

**Files:**
- Create: `src/envstate/deterministic_maintainer.py` (the `build_blocker_patch` function + its private helpers)
- Test: `tests/test_deterministic_maintainer.py`

**Interfaces:**
- Consumes: `extract_blocker_subject` (`contracts/extract.py`), `Node`/`Edge` (`contracts/nodes.py`), `ids` (`contracts/ids.py`), `GraphPatch` (`contracts/patch.py`), `ContractGraph.has_node` (`contracts/graph.py`), `TaskReport`/`CommandRecord` (`world_model.py`).
- Produces: `build_blocker_patch(graph: ContractGraph, report: TaskReport) -> GraphPatch`.

- [ ] **Step 1: Write the failing tests** (the field schema + the two auto-resolve correctness tests — the whole point of Change B)

```python
# tests/test_deterministic_maintainer.py
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))  # shim: import python_deps/src.envstate

from src.envstate.deterministic_maintainer import build_blocker_patch
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.apply import apply_patch
from src.envstate.contracts.ids import contract_id, blocker_id
from src.envstate.world_model import TaskReport, CommandRecord, derive_open_problems
from src.envstate.contracts.projection import _auto_resolve_blockers


def _report(cmd, rc, output, learning=""):
    return TaskReport("t", "blocked", (CommandRecord(cmd, rc, output),), learning)


def test_pg_config_failure_builds_system_layer_blocker_and_contract():
    report = _report("pip install psycopg2", 1, "Error: pg_config: command not found")
    patch = build_blocker_patch(ContractGraph.empty(), report)
    g = apply_patch(ContractGraph.empty(), patch)

    c = g.node(contract_id("binary", "pg_config"))
    assert c is not None
    assert c.data["subject"] == "pg_config"      # verbatim, not paraphrased
    assert c.data["layer"] == "system"
    assert c.data["level"] == "atomic"

    b = g.node(blocker_id("pg_config: command not found"))
    assert b is not None
    assert b.data["layer"] == "system"           # explicit — the bug was "deps"
    assert b.data["active"] is True
    assert "command not found" in b.data["signature"]   # verbatim


def test_emitted_blocker_retires_via_existing_auto_resolve():
    # THE correctness test: after apt install lands pg_config in `present`,
    # the existing _auto_resolve_blockers must retire the blocker.
    report = _report("pip install psycopg2", 1, "Error: pg_config: command not found")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    updated, satisfied = _auto_resolve_blockers(g, present={"pg-config"}, collection_ok=False)
    assert contract_id("binary", "pg_config") in satisfied   # contract now satisfied
    assert any(not n.data.get("active", True) for n in updated)  # blocker retired


def test_emitted_blocker_populates_open_problems_with_system_layer():
    report = _report("pip install psycopg2", 1, "Error: pg_config: command not found")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    problems = derive_open_problems(g)
    assert any(p.layer == "system" and "command not found" in p.signature for p in problems)


def test_soname_failure_is_system_layer():
    report = _report("python -c 'import cv2'", 1,
                     "ImportError: libGL.so.1: cannot open shared object file")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    c = g.node(contract_id("system_library", "libGL.so.1"))
    assert c is not None and c.data["layer"] == "system"


def test_module_not_found_is_deps_layer():
    report = _report("pytest", 1, "ModuleNotFoundError: No module named 'requests'")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    c = g.node(contract_id("python_import", "requests"))
    assert c is not None and c.data["layer"] == "deps"   # deps is correct for pip imports


def test_idempotent_existing_nodes_skipped():
    report = _report("x", 1, "pg_config: command not found")
    g = apply_patch(ContractGraph.empty(), build_blocker_patch(ContractGraph.empty(), report))
    patch2 = build_blocker_patch(g, report)   # same failure, graph already has the nodes
    assert patch2.add_contracts == () and patch2.add_blockers == ()


def test_learning_preserved_as_diagnostic_note():
    report = _report("x", 1, "pg_config: command not found", learning="psycopg2 needs libpq-dev")
    patch = build_blocker_patch(ContractGraph.empty(), report)
    assert any("psycopg2 needs libpq-dev" in n for n in patch.diagnostic_notes)


def test_no_signature_no_blockers():
    report = _report("echo ok", 0, "all good")
    patch = build_blocker_patch(ContractGraph.empty(), report)
    assert patch.add_blockers == () and patch.add_contracts == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_deterministic_maintainer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.envstate.deterministic_maintainer'`.

- [ ] **Step 3: Implement `build_blocker_patch`**

```python
# src/envstate/deterministic_maintainer.py
"""Deterministic Maintainer (Change B): blocker extraction + done-gate, no LLM.

Replaces the LLM Maintainer's graph-patch step with verbatim-signature blockers
carrying the correct layer, so the existing auto-resolve machinery
(_auto_resolve_blockers / _auto_resolve_system_problems) fires after a fix.
Attempts/outcomes are NOT handled here — the orchestrator already does that.
"""
from __future__ import annotations

from .contracts import ids
from .contracts.extract import extract_blocker_subject
from .contracts.graph import ContractGraph
from .contracts.nodes import Edge, Node
from .contracts.patch import GraphPatch
from .world_model import TaskReport

# blocker kind (from extract._RULES) -> contract kind
_CONTRACT_KIND = {
    "module_not_found": "python_import",
    "missing_binary": "binary",
    "missing_system_library": "system_library",
}
# contract kind -> obligation layer (mirrors extract.py:48)
_LAYER = {"python_import": "deps", "binary": "system", "system_library": "system"}


def build_blocker_patch(graph: ContractGraph, report: TaskReport) -> GraphPatch:
    """A scope='host' patch of Contract + Blocker + violates for each failure
    signature in the report's command output. Idempotent vs the graph."""
    contracts: list[Node] = []
    blockers: list[Node] = []
    edges: list[Edge] = []
    seen: set[str] = set()

    for rec in report.commands:
        for raw in (rec.output or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            subject, bkind = extract_blocker_subject(line)
            ckind = _CONTRACT_KIND.get(bkind)
            if subject is None or ckind is None:
                continue
            layer = _LAYER[ckind]
            cid = ids.contract_id(ckind, subject)
            bid = ids.blocker_id(line)
            if cid not in seen and not graph.has_node(cid):
                contracts.append(Node(cid, "Contract", {
                    "level": "atomic", "kind": ckind, "subject": subject, "layer": layer,
                    "check": "", "source_refs": [f"signature:{line[:60]}"],
                    "evidence_refs": [], "description": f"{ckind} obligation: {subject}.",
                    "metadata": {},
                }))
            if bid not in seen and not graph.has_node(bid):
                blockers.append(Node(bid, "Blocker", {
                    "signature": line, "kind": bkind, "layer": layer, "subject": subject,
                    "summary": f"{bkind}: {subject}", "root_or_downstream": "root",
                    "active": True, "evidence_refs": [], "metadata": {},
                }))
                edges.append(Edge(bid, "violates", cid))
            seen.add(cid)
            seen.add(bid)

    notes = (report.learning,) if (report.learning or "").strip() else ()
    return GraphPatch(
        add_contracts=tuple(contracts),
        add_blockers=tuple(blockers),
        add_edges=tuple(edges),
        diagnostic_notes=notes,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_deterministic_maintainer.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/deterministic_maintainer.py tests/test_deterministic_maintainer.py
git commit -m "feat(envstate): deterministic blocker extraction (verbatim sig + system layer)"
```

---

## Task 2: `maintain()` wrapper — done-gate + merge_map (drop-in for `Maintainer.update`)

**Files:**
- Modify: `src/envstate/deterministic_maintainer.py` (add `maintain` + `DeterministicMaintainer`)
- Test: `tests/test_deterministic_maintainer.py` (append)

**Interfaces:**
- Consumes: `build_blocker_patch` (Task 1); `_verified_test_run_passed`, `_progress_synced_with_done` (pure helpers in `maintainer.py`); `apply_patch` (`contracts/apply.py`); `validate_patch` (`contracts/validation.py`); `merge_map` (`world_model.py`).
- Produces: `maintain(current_map, report) -> WorldModelMap`; `DeterministicMaintainer.update(current_map, report) -> WorldModelMap`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_deterministic_maintainer.py
from src.envstate.deterministic_maintainer import maintain, DeterministicMaintainer
from src.envstate.world_model import initial_map


def _base_map():
    return initial_map(base_image="python:3.11", workdir="/repo", language="python 3.11",
                       build_system="pip", repo_layout=("tests/",))


def test_maintain_adds_blocker_to_contract_graph():
    m = _base_map()
    out = maintain(m, _report("x", 1, "pg_config: command not found"))
    assert out.contract_graph.node(contract_id("binary", "pg_config")) is not None


def test_maintain_passes_through_done_gate():
    # A real passing pytest run flips done_flag via _verified_test_run_passed.
    m = _base_map()
    passing = TaskReport("t", "done",
        (CommandRecord("python -m pytest -q", 0, "5 passed in 0.1s"),), "")
    out = maintain(m, passing)
    assert out.done_flag is True


def test_maintain_does_not_touch_owned_fields():
    m = _base_map()
    out = maintain(m, _report("x", 1, "pg_config: command not found"))
    for f in ("installed", "required", "env", "system_installed", "base_image",
              "workdir", "language", "build_system", "repo_layout", "dep_advisory"):
        assert getattr(out, f) == getattr(m, f)


def test_adapter_update_is_drop_in():
    m = _base_map()
    out = DeterministicMaintainer().update(m, _report("x", 1, "pg_config: command not found"))
    assert out.contract_graph.node(contract_id("binary", "pg_config")) is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_deterministic_maintainer.py -v -k "maintain or adapter"`
Expected: FAIL — `ImportError: cannot import name 'maintain'`.

- [ ] **Step 3: Implement `maintain` + the adapter**

Add to `src/envstate/deterministic_maintainer.py`:

```python
from .contracts.apply import apply_patch
from .contracts.validation import validate_patch
from .maintainer import _progress_synced_with_done, _verified_test_run_passed
from .world_model import WorldModelMap, merge_map


def maintain(current_map: WorldModelMap, report: TaskReport) -> WorldModelMap:
    """Deterministic drop-in for Maintainer.update: extract blockers + done-gate.

    Attempts/outcomes are handled by the orchestrator, not here.
    """
    done = current_map.done_flag or _verified_test_run_passed(report)
    graph = current_map.contract_graph
    patch = build_blocker_patch(graph, report)
    if not patch.is_empty():
        errors = validate_patch(graph, patch, scope="host")
        if not errors:
            graph = apply_patch(graph, patch)
    return merge_map(
        current_map,
        done_flag=done,
        progress=_progress_synced_with_done(current_map, done),
        contract_graph=graph,
    )


class DeterministicMaintainer:
    """Duck-typed stand-in for Maintainer (exposes .update)."""

    def update(self, current_map: WorldModelMap, report: TaskReport) -> WorldModelMap:
        return maintain(current_map, report)
```

> NOTE: confirm the real names/signatures of `_progress_synced_with_done` and `_verified_test_run_passed` in `maintainer.py` and `validate_patch` in `contracts/validation.py`; adjust the import/call if they differ. If `_progress_synced_with_done` is not module-level-importable, inline its one-line behavior (`{**current_map.progress, "tests": True}` when `done and not progress["tests"]`).

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_deterministic_maintainer.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/deterministic_maintainer.py tests/test_deterministic_maintainer.py
git commit -m "feat(envstate): maintain() drop-in for Maintainer.update (done-gate + merge_map)"
```

---

## Task 3: Flag plumbing + adapter branch (wiring)

**Files:**
- Modify: `agent.py` — argparse, constructor default + stored attr, instantiation, and the Maintainer-construction branch (~line 1109)
- Modify: `multi_docker_eval_adapter.py` — env-var pickup + pass-through (~line 776)

**Interfaces:**
- Consumes: `DeterministicMaintainer` (Task 2).
- Produces: `DOCKERAGENT_DETERMINISTIC_MAINTAINER` / `--enable-deterministic-maintainer` gate; the orchestrator receives a `DeterministicMaintainer` instead of `_Maintainer` when on.

- [ ] **Step 1: Write the failing test** (flag → adapter selection; avoids spinning the whole agent)

```python
# append to tests/test_deterministic_maintainer.py
def test_env_flag_recognized():
    import os
    from src.envstate.deterministic_maintainer import DeterministicMaintainer
    # The wiring contract: when the flag is on, the Maintainer object exposes the
    # deterministic .update. We assert the adapter is usable as a Maintainer stand-in.
    assert hasattr(DeterministicMaintainer(), "update")
```

> The full flag→object wiring is integration-level; this test pins the adapter shape. Verify the agent-side branch manually in Step 4 by grepping the four touch points.

- [ ] **Step 2: Run to verify the suite is green before wiring**

Run: `pytest tests/test_deterministic_maintainer.py -v`
Expected: PASS.

- [ ] **Step 3: Add the flag (mirror `enable_dep_graph` exactly)**

In `agent.py`:
- argparse (near the `--enable-dep-graph` arg, ~line 3109):
```python
parser.add_argument("--enable-deterministic-maintainer", action="store_true",
                    help="Replace the LLM Maintainer with a deterministic host module "
                         "(verbatim-signature blockers + correct layers; implies --enable-v1/contract-graph).")
```
- constructor default (~line 236): add `enable_deterministic_maintainer=False,`
- stored attr (~line 283): add `self.enable_deterministic_maintainer = enable_deterministic_maintainer`
- instantiation (~line 3160): add `enable_deterministic_maintainer=args.enable_deterministic_maintainer,`

In `multi_docker_eval_adapter.py` (~line 776, beside `_enable_dep_graph`):
```python
_enable_det_maint = os.environ.get("DOCKERAGENT_DETERMINISTIC_MAINTAINER", "").lower() in ("1", "true", "yes", "on")
```
…and pass `enable_deterministic_maintainer=_enable_det_maint,` into the `DockerAgent(...)` construction (beside `enable_dep_graph=...`).

- [ ] **Step 4: Wire the adapter branch**

In `agent.py` at the Maintainer construction (~lines 1109-1113), wrap the existing `_Maintainer(...)` build:
```python
if getattr(self, "enable_deterministic_maintainer", False):
    from src.envstate.deterministic_maintainer import DeterministicMaintainer
    maintainer = DeterministicMaintainer()
else:
    maintainer = _Maintainer(
        self.client, self.model,
        on_usage=lambda usage: self._record_supervisor_path_usage("reflection", usage),
        log_path=_llm_log_path,
    )
```
> Confirm the exact existing `_Maintainer(...)` call and variable name (`maintainer`) before editing; keep the `else` branch byte-identical to today. The orchestrator (`run_v1`) consumes `maintainer.update(...)` at three call sites and needs no change.

- [ ] **Step 5: Verify the suite + off-state**

Run: `pytest tests/test_deterministic_maintainer.py -q` (PASS) and `pytest tests/ -q -k "maintainer or envstate or contract"` (no regressions).
Manually confirm the 4 `agent.py` touch points + 1 `multi_docker_eval_adapter.py` touch point match the `enable_dep_graph` pattern, and that the `else` branch is unchanged (off-state byte-identical).

- [ ] **Step 6: Commit**

```bash
git add agent.py multi_docker_eval_adapter.py tests/test_deterministic_maintainer.py
git commit -m "feat(agent): gate deterministic maintainer behind DOCKERAGENT_DETERMINISTIC_MAINTAINER"
```

---

## Deferred / Notes (NOT in this plan)

1. **No-target attempt outcome default.** `derive_attempt_outcome` returns `"ok"` for steps with no `target_node_ids` (`validators.py:142`). It's the orchestrator's path (not `maintain()`), affects both LLM and deterministic arms, and changing it would confound the Change-B A/B. Fix it as a separate change applied to both arms.
2. **Backbone `depends_on` for emitted contracts.** Like the existing reactive `promote_atomic_contracts`, emitted contracts are not attached to the goal backbone via `depends_on` (parity with today). The Planner sees them via `open_problems` (from the blockers). Adding `depends_on` for frontier navigation is an optional enhancement, not parity.
3. **Inter-cycle enrichment lost.** The LLM's `update_blocker_classification` / `update_contract_description` (sharpening root/downstream across cycles) are dropped. Accepted trade-off; the BuildAgent `learning` note carries semantic color.
4. **Regex long-tail.** The 5 rules miss CMake/version-conflict/generic build errors (Open Q4 in the spec). Measure miss rate before considering a constrained LLM classifier.
5. **Arm preset / measurement.** Optionally add a `v1gdb`-style arm in `run_rat_benchmark.py` that sets `DOCKERAGENT_DETERMINISTIC_MAINTAINER=1`. A/B: `v1g` vs `v1g+B`, scored with `compute_essr`, on the system-dep subset (per the design spec §9).

## Self-Review Notes

- **Spec coverage:** Task 1 (blocker extraction + the auto-resolve-fires correctness test), Task 2 (`maintain` drop-in), Task 3 (flag + adapter) cover the full Change B. Attempts/outcomes correctly excluded (orchestrator owns them).
- **Type consistency:** `build_blocker_patch(graph, report) -> GraphPatch`; `maintain(current_map, report) -> WorldModelMap`; `DeterministicMaintainer.update` matches `Maintainer.update`'s signature.
- **Correctness heart:** the two tests `test_emitted_blocker_retires_via_existing_auto_resolve` and `test_emitted_blocker_populates_open_problems_with_system_layer` prove the dormant auto-resolve fires — exactly what the LLM path fails to do.
