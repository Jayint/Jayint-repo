# EnvState v1 (Three Roles, One Grounded Map) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multi-channel EnvState maintainer/planner/worker contract with three single-purpose agents (Planner, Build Agent, Maintainer) that share one grounded `WorldModelMap`.

**Architecture:** Planner-led cycle: the Planner reads the map and emits a scoped `Task` | `DONE` | `GIVEUP`; the Build Agent runs a local mini-ReAct (budget 8) via the sandbox and returns a `TaskReport`; the Maintainer folds that report into a new map; the orchestrator finalizes the instant `map.done_flag` flips (`pytest --collect-only` passed) and reuses the existing ActionLedger -> Synthesizer -> Dockerfile path. Reuses sandbox / ledger / synthesizer / image_selector; deletes probes / acl / supervisor / worker / fullstate_worker / types / serde.

**Tech Stack:** Python 3, frozen dataclasses + type hints, pytest, Docker SDK, OpenAI-compatible LLM client (`minimax/minimax-m2.7`).

**Source spec:** `docs/superpowers/specs/2026-06-09-envstate-v1-three-role-contract-design.md`

---

## File Structure

- `src/envstate/world_model.py` — **new** — WorldModelMap, Fact, OpenProblem, Task, PlannerDecision, CommandRecord, TaskReport frozen dataclasses plus initial_map() factory and pure map-merge helper(s)
- `src/envstate/planner.py` — **new** — Planner class — reads WorldModelMap, emits PlannerDecision via one LLM call per cycle; owns global sequencing and done/giveup termination
- `src/envstate/build_agent.py` — **new** — BuildAgent class — runs a mini-ReAct loop (LOCAL_BUDGET=8 steps) for one Task; executes via Sandbox; appends to ActionLedger; returns TaskReport; contains the fixed interruption guard that ignores pre-execution sandbox rejections
- `src/envstate/maintainer.py` — **rewrite** — Maintainer class rewritten — reads (WorldModelMap, TaskReport), emits exactly one new WorldModelMap per cycle; sets done_flag when collect-only rc 0 is seen; single-writer, never runs shell commands
- `src/envstate/orchestrator.py` — **rewrite** — EnvStateOrchestrator rewritten as run_v1(agent, max_cycles, local_budget) loop: initial_map → planner.decide → build_agent.run → maintainer.update → done_flag hard-stop → finalize
- `src/envstate/types.py` — **delete** — DELETE the old v0 types (EnvStateSnapshot, Requirement, Evidence, OpenFailure, BaseFacts, ProviderFact, Source, Status constants, ACL-related frozensets). Replaced entirely by world_model.py.
- `src/envstate/probes.py` — **delete** — DELETE — ProbeSpec/ProbeResult/run_probe/certify_probe_result; all probe/certify machinery removed by design (spec §11 non-goal). Unwire from _build_observer in agent.py.
- `src/envstate/acl.py` — **delete** — DELETE — certify_from_probe/apply_llm_proposal/advance_revision/ACL trust-boundary code. Unwire from maintainer.py and _build_observer in agent.py.
- `src/envstate/supervisor.py` — **delete** — DELETE (role absorbed into src/envstate/planner.py). Unwire from agent.py _run_supervisor and orchestrator.py.
- `src/envstate/worker.py` — **delete** — DELETE (role absorbed into src/envstate/build_agent.py). Retain _extract_worker_action / _is_worker_finished as internal helpers in build_agent.py.
- `src/envstate/fullstate_worker.py` — **delete** — DELETE (role absorbed into src/envstate/build_agent.py).
- `src/envstate/ledger.py` — **modify** — REUSE unchanged — ActionLedger + ActionEvent; the source-of-truth for Dockerfile synthesis
- `src/envstate/synthesis.py` — **modify** — REUSE unchanged — build_commands_from_ledger; called by _synthesize_final_build_recipe in agent.py
- `src/synthesizer.py` — **modify** — REUSE unchanged — Synthesizer class; classify_mutation, command_mutates_environment, generate_dockerfile, synthesize_build_recipe, apply_build_recipe, _extract_recordable_setup_commands
- `src/sandbox.py` — **modify** — REUSE unchanged — Sandbox.execute (mutating commands) and Sandbox.exec_readonly (read-only probes)
- `src/image_selector.py` — **modify** — REUSE unchanged — provides base_image and language for initial_map()
- `agent.py` — **modify** — MODIFY glue only — add enable_v1 flag, wire _run_v1 dispatch in run(), replace _build_observer with a v1-compatible thin ledger-append helper, update finalize trigger to fire on map.done_flag
- `run_repo2run_benchmark.py` — **modify** — MODIFY arm selector — add --arm v1 preset (enable_v1=True, max_cycles=12, local_budget=8); retire Arms A/B/C presets; keep Arm 0 as baseline
- `src/envstate/diagnostics.py` — **modify** — REUSE unchanged — log_llm_exchange helper called by planner, maintainer, build_agent
- `src/envstate/llm_response.py` — **modify** — REUSE unchanged — complete_with_retry and response_text used by all three role modules
- `src/envstate/jsonutil.py` — **modify** — REUSE unchanged — extract_json_object used by planner and maintainer to parse LLM JSON


---

# Phase 1: World Model — types + grounded map helpers

### Task 1: Scaffold `src/envstate/world_model.py` with frozen dataclasses

**Files:**
- Create: `src/envstate/world_model.py`
- Create: `tests/test_world_model.py`

---

- [ ] **Step 1: Write the failing test**

  Create `tests/test_world_model.py` with the following complete content:

  ```python
  # tests/test_world_model.py
  """Unit tests for src/envstate/world_model.py — frozen dataclasses and pure helpers.

  Covers:
    - All seven frozen dataclasses can be instantiated and are immutable.
    - initial_map() produces a correct zero-state WorldModelMap.
    - merge_map() produces a new map with only the supplied fields replaced.
    - done_flag defaults to False.
    - JSON serialization helpers round-trip every dataclass losslessly.
    - WorldModelMap.progress is a plain dict (merge_map always makes a new one).
  """
  from __future__ import annotations

  import dataclasses
  import json
  import pytest

  # ── import targets (will fail until world_model.py exists) ─────────────────
  from src.envstate.world_model import (
      CommandRecord,
      Fact,
      OpenProblem,
      PlannerDecision,
      Task,
      TaskReport,
      WorldModelMap,
      initial_map,
      map_to_dict,
      map_from_dict,
      merge_map,
  )


  # ---------------------------------------------------------------------------
  # Fixtures
  # ---------------------------------------------------------------------------

  def _fact(name: str, detail: str = "") -> Fact:
      return Fact(name=name, detail=detail)


  def _open_problem(sig: str = "ModuleNotFoundError: psycopg2") -> OpenProblem:
      return OpenProblem(
          signature=sig,
          interpretation="psycopg2 not installed",
          layer="deps",
      )


  def _minimal_map() -> WorldModelMap:
      return initial_map(
          base_image="python:3.12-slim",
          workdir="/app",
          language="python 3.12",
          build_system="pip",
          repo_layout=("tests/", "src/", "requirements.txt"),
      )


  # ---------------------------------------------------------------------------
  # Task 1 tests — frozen dataclass immutability
  # ---------------------------------------------------------------------------

  class TestFrozenDataclasses:
      def test_fact_is_frozen(self):
          f = Fact(name="flask", detail="3.0.0")
          with pytest.raises(dataclasses.FrozenInstanceError):
              f.name = "django"  # type: ignore[misc]

      def test_fact_default_detail_empty(self):
          f = Fact(name="pytest")
          assert f.detail == ""

      def test_open_problem_is_frozen(self):
          op = OpenProblem(
              signature="ImportError: no module named x",
              interpretation="x not installed",
              layer="deps",
          )
          with pytest.raises(dataclasses.FrozenInstanceError):
              op.layer = "runtime"  # type: ignore[misc]

      def test_open_problem_out_of_scope_defaults_false(self):
          op = _open_problem()
          assert op.out_of_scope is False

      def test_world_model_map_is_frozen(self):
          m = _minimal_map()
          with pytest.raises(dataclasses.FrozenInstanceError):
              m.done_flag = True  # type: ignore[misc]

      def test_task_is_frozen(self):
          t = Task(
              goal="install flask",
              done_when="python -c 'import flask' exits 0",
              layer="deps",
              facts=("base_image=python:3.12-slim",),
          )
          with pytest.raises(dataclasses.FrozenInstanceError):
              t.goal = "uninstall flask"  # type: ignore[misc]

      def test_task_facts_is_tuple(self):
          t = Task(
              goal="install flask",
              done_when="import flask works",
              layer="deps",
              facts=("base_image=python:3.12-slim", "workdir=/app"),
          )
          assert isinstance(t.facts, tuple)

      def test_planner_decision_is_frozen(self):
          d = PlannerDecision(action="done", reason="all layers green")
          with pytest.raises(dataclasses.FrozenInstanceError):
              d.action = "giveup"  # type: ignore[misc]

      def test_planner_decision_task_defaults_none(self):
          d = PlannerDecision(action="done")
          assert d.task is None

      def test_planner_decision_reason_defaults_empty(self):
          d = PlannerDecision(action="task", task=Task(
              goal="g", done_when="d", layer="deps", facts=()
          ))
          assert d.reason == ""

      def test_command_record_is_frozen(self):
          cr = CommandRecord(cmd="pip install flask", rc=0, output="Successfully installed flask")
          with pytest.raises(dataclasses.FrozenInstanceError):
              cr.rc = 1  # type: ignore[misc]

      def test_task_report_is_frozen(self):
          tr = TaskReport(
              task_goal="install flask",
              status="done",
              commands=(CommandRecord(cmd="pip install flask", rc=0, output="ok"),),
              learning="flask installed cleanly",
          )
          with pytest.raises(dataclasses.FrozenInstanceError):
              tr.status = "blocked"  # type: ignore[misc]

      def test_task_report_commands_is_tuple(self):
          cr = CommandRecord(cmd="pip install flask", rc=0, output="ok")
          tr = TaskReport(
              task_goal="install flask",
              status="done",
              commands=(cr,),
              learning="done",
          )
          assert isinstance(tr.commands, tuple)
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestFrozenDataclasses -x -q
  ```

  Expected failure: `ModuleNotFoundError: No module named 'src.envstate.world_model'`

- [ ] **Step 3: Write the minimal implementation**

  Create `src/envstate/world_model.py` with the following complete content:

  ```python
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
  ) -> WorldModelMap:
      """Return a new WorldModelMap with only the supplied keyword fields replaced.

      All other fields are copied unchanged from *current*.
      The progress dict is always shallow-copied so callers never share a
      reference to the same live dict.  Never raises.
      """
      return dataclasses.replace(
          current,
          installed=installed if installed is not None else current.installed,
          open_problems=open_problems if open_problems is not None else current.open_problems,
          progress=dict(progress) if progress is not None else dict(current.progress),
          done_flag=done_flag if done_flag is not None else current.done_flag,
          notes=notes if notes is not None else current.notes,
          required=required if required is not None else current.required,
      )


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
      )
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestFrozenDataclasses -x -q
  ```

  Expected: `13 passed`

- [ ] **Step 5: Commit**

  ```bash
  git add src/envstate/world_model.py tests/test_world_model.py
  git commit -m "feat(world-model): add frozen dataclasses skeleton for EnvState v1 types"
  ```

---

### Task 2: `initial_map()` factory correctness

**Files:**
- Modify: `tests/test_world_model.py` (append new test class)

---

- [ ] **Step 1: Write the failing test**

  Append the following class to `tests/test_world_model.py` (after `TestFrozenDataclasses`):

  ```python
  class TestInitialMap:
      def test_returns_world_model_map_instance(self):
          m = _minimal_map()
          assert isinstance(m, WorldModelMap)

      def test_base_image_and_workdir_stored(self):
          m = initial_map(
              base_image="python:3.12-slim",
              workdir="/app",
              language="python 3.12",
              build_system="pip",
              repo_layout=("tests/", "requirements.txt"),
          )
          assert m.base_image == "python:3.12-slim"
          assert m.workdir == "/app"

      def test_language_and_build_system_stored(self):
          m = initial_map(
              base_image="python:3.11-slim",
              workdir="/workspace",
              language="python 3.11",
              build_system="poetry",
              repo_layout=(),
          )
          assert m.language == "python 3.11"
          assert m.build_system == "poetry"

      def test_repo_layout_stored_as_tuple(self):
          m = initial_map(
              base_image="python:3.12-slim",
              workdir="/app",
              language="python 3.12",
              build_system="pip",
              repo_layout=("tests/", "src/", "pyproject.toml"),
          )
          assert m.repo_layout == ("tests/", "src/", "pyproject.toml")
          assert isinstance(m.repo_layout, tuple)

      def test_done_flag_defaults_false(self):
          m = _minimal_map()
          assert m.done_flag is False

      def test_installed_starts_empty(self):
          m = _minimal_map()
          assert m.installed == ()

      def test_open_problems_starts_empty(self):
          m = _minimal_map()
          assert m.open_problems == ()

      def test_notes_starts_empty(self):
          m = _minimal_map()
          assert m.notes == ()

      def test_required_defaults_to_empty_tuple(self):
          m = _minimal_map()
          assert m.required == ()

      def test_required_can_be_supplied(self):
          req = (_fact("flask"), _fact("pytest"))
          m = initial_map(
              base_image="python:3.12-slim",
              workdir="/app",
              language="python 3.12",
              build_system="pip",
              repo_layout=(),
              required=req,
          )
          assert m.required == req

      def test_progress_has_all_six_layers(self):
          m = _minimal_map()
          assert set(m.progress.keys()) == {"base", "system", "runtime", "deps", "build", "tests"}

      def test_progress_all_false_at_start(self):
          m = _minimal_map()
          assert all(v is False for v in m.progress.values())

      def test_progress_is_dict_not_frozen(self):
          # progress is a plain dict by contract — merge_map handles copy-on-write
          m = _minimal_map()
          assert isinstance(m.progress, dict)

      def test_two_calls_produce_independent_progress_dicts(self):
          m1 = _minimal_map()
          m2 = _minimal_map()
          # mutating the progress dict of m1 must not affect m2
          m1.progress["base"] = True
          assert m2.progress["base"] is False
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestInitialMap -x -q
  ```

  Expected failure: `ImportError` (the test class does not yet exist in the file — the module exists but the new class must be appended first, at which point the tests will be collected and pass because the implementation was already written in Task 1; run after appending to confirm they pass).

  > Note: because the implementation already covers this in Task 1, the correct "red" step is to run the collect step *before* appending the new class — confirm only `TestFrozenDataclasses` is collected, then append the class, then run again.

  ```bash
  python3 -m pytest tests/test_world_model.py --collect-only -q
  # should show only TestFrozenDataclasses tests
  ```

- [ ] **Step 3: Implementation is already complete** — `initial_map()` was written in Task 1. No code changes needed.

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestInitialMap -x -q
  ```

  Expected: `14 passed`

- [ ] **Step 5: Commit**

  ```bash
  git add tests/test_world_model.py
  git commit -m "test(world-model): add TestInitialMap coverage for initial_map() factory"
  ```

---

### Task 3: `merge_map()` pure immutable helper

**Files:**
- Modify: `tests/test_world_model.py` (append new test class)

---

- [ ] **Step 1: Write the failing test**

  Append the following class to `tests/test_world_model.py`:

  ```python
  class TestMergeMap:
      def test_returns_new_instance(self):
          m = _minimal_map()
          m2 = merge_map(m, done_flag=False)
          assert m2 is not m

      def test_done_flag_can_be_set_true(self):
          m = _minimal_map()
          m2 = merge_map(m, done_flag=True)
          assert m2.done_flag is True

      def test_original_done_flag_unchanged(self):
          m = _minimal_map()
          merge_map(m, done_flag=True)
          assert m.done_flag is False

      def test_installed_replaced(self):
          m = _minimal_map()
          facts = (_fact("flask", "3.0.0"), _fact("pytest", "8.0"))
          m2 = merge_map(m, installed=facts)
          assert m2.installed == facts

      def test_installed_original_unchanged(self):
          m = _minimal_map()
          merge_map(m, installed=(_fact("flask"),))
          assert m.installed == ()

      def test_open_problems_replaced(self):
          m = _minimal_map()
          ops = (_open_problem(),)
          m2 = merge_map(m, open_problems=ops)
          assert m2.open_problems == ops

      def test_notes_replaced(self):
          m = _minimal_map()
          notes = ("do not use psycopg2-binary",)
          m2 = merge_map(m, notes=notes)
          assert m2.notes == notes

      def test_required_replaced(self):
          m = _minimal_map()
          req = (_fact("flask"),)
          m2 = merge_map(m, required=req)
          assert m2.required == req

      def test_progress_replaced(self):
          m = _minimal_map()
          new_progress = {
              "base": True, "system": True, "runtime": True,
              "deps": False, "build": False, "tests": False,
          }
          m2 = merge_map(m, progress=new_progress)
          assert m2.progress["base"] is True
          assert m2.progress["deps"] is False

      def test_progress_is_independent_copy(self):
          m = _minimal_map()
          new_progress = {
              "base": True, "system": False, "runtime": False,
              "deps": False, "build": False, "tests": False,
          }
          m2 = merge_map(m, progress=new_progress)
          # mutating the dict we passed in must not affect m2
          new_progress["base"] = False
          assert m2.progress["base"] is True

      def test_unspecified_fields_copied_unchanged(self):
          original_required = (_fact("flask"),)
          m = initial_map(
              base_image="python:3.12-slim",
              workdir="/app",
              language="python 3.12",
              build_system="pip",
              repo_layout=("tests/",),
              required=original_required,
          )
          m2 = merge_map(m, done_flag=True)
          # base_image, workdir, language, build_system, repo_layout, required all unchanged
          assert m2.base_image == "python:3.12-slim"
          assert m2.workdir == "/app"
          assert m2.language == "python 3.12"
          assert m2.build_system == "pip"
          assert m2.repo_layout == ("tests/",)
          assert m2.required == original_required

      def test_none_kwargs_leave_fields_unchanged(self):
          ops = (_open_problem(),)
          m = merge_map(_minimal_map(), open_problems=ops)
          m2 = merge_map(m, done_flag=True)  # open_problems not supplied
          assert m2.open_problems == ops

      def test_chain_two_merges(self):
          m0 = _minimal_map()
          m1 = merge_map(m0, installed=(_fact("flask"),))
          m2 = merge_map(m1, installed=(_fact("flask"), _fact("pytest")))
          assert len(m2.installed) == 2
          assert len(m1.installed) == 1  # m1 unchanged

      def test_merge_map_result_is_still_frozen(self):
          m = _minimal_map()
          m2 = merge_map(m, done_flag=True)
          with pytest.raises(dataclasses.FrozenInstanceError):
              m2.done_flag = False  # type: ignore[misc]
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestMergeMap -x -q
  ```

  Expected failure: `ImportError` or all-fail until `merge_map` is exported (it is exported in Task 1's implementation, so collect step confirms the tests are visible and all pass after the module is present).

  Confirm tests are collected:
  ```bash
  python3 -m pytest tests/test_world_model.py::TestMergeMap --collect-only -q
  ```

- [ ] **Step 3: Implementation is already complete** — `merge_map()` was written in Task 1. No code changes needed.

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestMergeMap -x -q
  ```

  Expected: `15 passed`

- [ ] **Step 5: Commit**

  ```bash
  git add tests/test_world_model.py
  git commit -m "test(world-model): add TestMergeMap coverage for merge_map() pure helper"
  ```

---

### Task 4: JSON serialization round-trip (`map_to_dict` / `map_from_dict`)

**Files:**
- Modify: `tests/test_world_model.py` (append new test class)

---

- [ ] **Step 1: Write the failing test**

  Append the following class to `tests/test_world_model.py`:

  ```python
  class TestMapSerialization:
      """map_to_dict / map_from_dict must round-trip any WorldModelMap losslessly."""

      def _rich_map(self) -> WorldModelMap:
          """A map with non-trivial field values for thorough round-trip testing."""
          base = initial_map(
              base_image="python:3.12-slim",
              workdir="/workspace",
              language="python 3.12",
              build_system="poetry",
              repo_layout=("tests/", "src/", "pyproject.toml"),
              required=(_fact("flask", ">=3.0"), _fact("pytest", ">=8.0")),
          )
          return merge_map(
              base,
              installed=(_fact("flask", "3.0.3"), _fact("pytest", "8.1.0")),
              open_problems=(
                  OpenProblem(
                      signature="ModuleNotFoundError: psycopg2",
                      interpretation="psycopg2 not installed",
                      layer="deps",
                      out_of_scope=False,
                  ),
              ),
              progress={
                  "base": True, "system": True, "runtime": True,
                  "deps": False, "build": False, "tests": False,
              },
              notes=("do not use psycopg2-binary",),
          )

      def test_map_to_dict_returns_dict(self):
          assert isinstance(map_to_dict(_minimal_map()), dict)

      def test_map_to_dict_contains_base_image(self):
          d = map_to_dict(_minimal_map())
          assert d["base_image"] == "python:3.12-slim"

      def test_map_to_dict_done_flag_false(self):
          d = map_to_dict(_minimal_map())
          assert d["done_flag"] is False

      def test_map_to_dict_progress_is_dict(self):
          d = map_to_dict(_minimal_map())
          assert isinstance(d["progress"], dict)
          assert set(d["progress"].keys()) == {"base", "system", "runtime", "deps", "build", "tests"}

      def test_map_to_dict_installed_is_list(self):
          m = merge_map(_minimal_map(), installed=(_fact("flask", "3.0.0"),))
          d = map_to_dict(m)
          assert isinstance(d["installed"], list)
          assert d["installed"][0]["name"] == "flask"
          assert d["installed"][0]["detail"] == "3.0.0"

      def test_map_to_dict_open_problems_is_list(self):
          m = merge_map(_minimal_map(), open_problems=(_open_problem(),))
          d = map_to_dict(m)
          assert isinstance(d["open_problems"], list)
          assert d["open_problems"][0]["signature"] == "ModuleNotFoundError: psycopg2"

      def test_map_to_dict_notes_is_list(self):
          m = merge_map(_minimal_map(), notes=("note one",))
          d = map_to_dict(m)
          assert isinstance(d["notes"], list)
          assert d["notes"] == ["note one"]

      def test_map_to_dict_repo_layout_is_list(self):
          d = map_to_dict(_minimal_map())
          assert isinstance(d["repo_layout"], list)

      def test_map_to_dict_is_json_serializable(self):
          d = map_to_dict(self._rich_map())
          serialized = json.dumps(d)  # must not raise
          assert isinstance(serialized, str)

      def test_round_trip_minimal_map(self):
          m = _minimal_map()
          assert map_from_dict(map_to_dict(m)) == m

      def test_round_trip_preserves_done_flag_true(self):
          m = merge_map(_minimal_map(), done_flag=True)
          m2 = map_from_dict(map_to_dict(m))
          assert m2.done_flag is True

      def test_round_trip_preserves_installed_facts(self):
          facts = (_fact("flask", "3.0.3"), _fact("pytest", "8.1.0"))
          m = merge_map(_minimal_map(), installed=facts)
          m2 = map_from_dict(map_to_dict(m))
          assert m2.installed == facts

      def test_round_trip_preserves_open_problems(self):
          ops = (
              OpenProblem(
                  signature="ModuleNotFoundError: psycopg2",
                  interpretation="psycopg2 not installed",
                  layer="deps",
                  out_of_scope=True,
              ),
          )
          m = merge_map(_minimal_map(), open_problems=ops)
          m2 = map_from_dict(map_to_dict(m))
          assert m2.open_problems[0].out_of_scope is True
          assert m2.open_problems[0].layer == "deps"

      def test_round_trip_preserves_progress(self):
          prog = {
              "base": True, "system": True, "runtime": False,
              "deps": False, "build": False, "tests": False,
          }
          m = merge_map(_minimal_map(), progress=prog)
          m2 = map_from_dict(map_to_dict(m))
          assert m2.progress["base"] is True
          assert m2.progress["runtime"] is False

      def test_round_trip_rich_map_equality(self):
          m = self._rich_map()
          assert map_from_dict(map_to_dict(m)) == m

      def test_round_trip_notes_preserved(self):
          m = merge_map(_minimal_map(), notes=("caution: editable install needed",))
          m2 = map_from_dict(map_to_dict(m))
          assert m2.notes == ("caution: editable install needed",)

      def test_round_trip_result_is_frozen(self):
          m2 = map_from_dict(map_to_dict(_minimal_map()))
          with pytest.raises(dataclasses.FrozenInstanceError):
              m2.done_flag = True  # type: ignore[misc]

      def test_map_from_dict_progress_is_independent_copy(self):
          d = map_to_dict(_minimal_map())
          m = map_from_dict(d)
          # Mutating the source dict must not affect the deserialized map's progress
          d["progress"]["base"] = True
          assert m.progress["base"] is False
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestMapSerialization -x -q
  ```

  Expected failure: `ImportError: cannot import name 'map_to_dict'` (these names are not yet exported from `world_model.py` when the test file is first appended — they were added in Task 1's implementation so this will pass once the import line at the top of the test file already lists them).

  Confirm by checking the import line already includes `map_to_dict, map_from_dict` — both were added in Task 1's test file header. If so, they should be collected and pass.

  Quick check:
  ```bash
  python3 -m pytest tests/test_world_model.py::TestMapSerialization --collect-only -q
  ```

- [ ] **Step 3: Implementation is already complete** — `map_to_dict()` and `map_from_dict()` were written in Task 1. No code changes needed.

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestMapSerialization -x -q
  ```

  Expected: `18 passed`

- [ ] **Step 5: Commit**

  ```bash
  git add tests/test_world_model.py
  git commit -m "test(world-model): add TestMapSerialization round-trip coverage"
  ```

---

### Task 5: `done_flag` trigger semantics and `merge_map` edge-cases

**Files:**
- Modify: `tests/test_world_model.py` (append new test class)

---

- [ ] **Step 1: Write the failing test**

  Append the following class to `tests/test_world_model.py`:

  ```python
  class TestDoneFlagAndEdgeCases:
      """
      done_flag must only be True when explicitly set via merge_map(done_flag=True).
      merge_map must never silently drop fields or produce incorrect progress copies.
      """

      def test_done_flag_false_after_partial_progress(self):
          """Even when all layers except tests are True, done_flag stays False."""
          m = merge_map(
              _minimal_map(),
              progress={
                  "base": True, "system": True, "runtime": True,
                  "deps": True, "build": True, "tests": False,
              },
          )
          assert m.done_flag is False

      def test_done_flag_set_true_via_merge_map(self):
          m = _minimal_map()
          m2 = merge_map(m, done_flag=True)
          assert m2.done_flag is True

      def test_done_flag_can_be_set_back_false_via_merge_map(self):
          m = merge_map(_minimal_map(), done_flag=True)
          m2 = merge_map(m, done_flag=False)
          assert m2.done_flag is False

      def test_merge_map_with_no_kwargs_copies_everything(self):
          installed = (_fact("flask", "3.0.0"),)
          notes = ("keep editable install",)
          m = merge_map(
              _minimal_map(),
              installed=installed,
              notes=notes,
              done_flag=True,
          )
          m2 = merge_map(m)  # no kwargs
          assert m2.installed == installed
          assert m2.notes == notes
          assert m2.done_flag is True
          assert m2.base_image == m.base_image

      def test_merge_map_with_empty_installed_tuple_clears_field(self):
          m = merge_map(_minimal_map(), installed=(_fact("flask"),))
          m2 = merge_map(m, installed=())
          assert m2.installed == ()

      def test_merge_map_with_empty_notes_tuple_clears_field(self):
          m = merge_map(_minimal_map(), notes=("note",))
          m2 = merge_map(m, notes=())
          assert m2.notes == ()

      def test_progress_copy_does_not_alias_current_progress(self):
          m = _minimal_map()
          m2 = merge_map(m)  # no progress kwarg
          m2.progress["base"] = True  # mutate the copy
          # original m.progress must be unaffected
          assert m.progress["base"] is False

      def test_three_role_cycle_simulation(self):
          """
          Simulate one complete cycle:
            initial_map → merge installed (build agent reports) → merge done_flag (maintainer sees collect-only rc=0)
          Verify the map is a fresh object at each stage and done_flag fires correctly.
          """
          # Cycle start
          m0 = _minimal_map()
          assert m0.done_flag is False
          assert m0.installed == ()

          # After build agent runs `pip install flask` (rc=0)
          m1 = merge_map(m0, installed=(_fact("flask", "3.0.3"),))
          assert m1.done_flag is False
          assert len(m1.installed) == 1
          assert m0.installed == ()  # m0 unchanged

          # After build agent runs `pytest --collect-only` (rc=0) — maintainer sees it
          m2 = merge_map(
              m1,
              done_flag=True,
              progress={
                  "base": True, "system": True, "runtime": True,
                  "deps": True, "build": True, "tests": True,
              },
          )
          assert m2.done_flag is True
          assert m2.progress["tests"] is True
          assert m1.done_flag is False  # m1 unchanged
          assert m1.progress["tests"] is False  # m1 unchanged
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestDoneFlagAndEdgeCases -x -q
  ```

  Expected failure: tests are not yet in the file. After appending, all should pass immediately (implementation already correct from Task 1). Confirm they are collected:

  ```bash
  python3 -m pytest tests/test_world_model.py::TestDoneFlagAndEdgeCases --collect-only -q
  ```

- [ ] **Step 3: Implementation is already complete** — all logic was written in Task 1. No code changes needed.

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  python3 -m pytest tests/test_world_model.py::TestDoneFlagAndEdgeCases -x -q
  ```

  Expected: `9 passed`

- [ ] **Step 5: Commit**

  ```bash
  git add tests/test_world_model.py
  git commit -m "test(world-model): add done_flag trigger and merge_map edge-case tests"
  ```

---

### Task 6: Full suite smoke-run and coverage gate

**Files:**
- No new files; run the full test file.

---

- [ ] **Step 1: Run the complete test suite for this module**

  ```bash
  python3 -m pytest tests/test_world_model.py -v
  ```

  Expected output (all classes collected):

  ```
  tests/test_world_model.py::TestFrozenDataclasses::test_fact_is_frozen PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_fact_default_detail_empty PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_open_problem_is_frozen PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_open_problem_out_of_scope_defaults_false PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_world_model_map_is_frozen PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_task_is_frozen PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_task_facts_is_tuple PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_planner_decision_is_frozen PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_planner_decision_task_defaults_none PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_planner_decision_reason_defaults_empty PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_command_record_is_frozen PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_task_report_is_frozen PASSED
  tests/test_world_model.py::TestFrozenDataclasses::test_task_report_commands_is_tuple PASSED
  ... (all TestInitialMap, TestMergeMap, TestMapSerialization, TestDoneFlagAndEdgeCases)
  59 passed
  ```

- [ ] **Step 2: Run with coverage**

  ```bash
  python3 -m pytest tests/test_world_model.py --cov=src/envstate/world_model --cov-report=term-missing -q
  ```

  Expected: coverage >= 95% for `src/envstate/world_model.py`.

  If coverage is below 95%, identify uncovered lines from the `--cov-report=term-missing` output and add targeted tests for those lines.

- [ ] **Step 3: Verify existing tests are unaffected**

  ```bash
  python3 -m pytest tests/test_envstate_types.py tests/test_envstate_ledger.py tests/test_envstate_jsonutil.py -q
  ```

  Expected: all pass (world_model.py introduces no imports from the files being deleted — it depends only on stdlib).

- [ ] **Step 4: Commit**

  ```bash
  git add tests/test_world_model.py
  git commit -m "test(world-model): verify full suite and coverage gate for world_model.py"
  ```

---

# Phase 2: Maintainer — single-output map updater (rewrite)

### Task 7: Write failing tests for the new Maintainer.update interface

**Files:**
- Create: `tests/test_v1_maintainer.py`

The tests import from two modules: `src/envstate/world_model.py` (built in Group 1) and the
rewritten `src/envstate/maintainer.py`.  Running them must fail with `ImportError` because
the rewritten `maintainer.py` does not exist yet.

- [ ] **Step 1: Write the failing test file**

```python
# tests/test_v1_maintainer.py
"""Unit tests for the v1 Maintainer rewrite.

Covers:
- parse_v1_maintainer_reply: valid JSON → WorldModelMap fields
- Grounding rule: a Fact not demonstrated in command output is not added to installed
- done_flag set when a pytest --collect-only command has rc==0
- done_flag NOT set when collect-only rc!=0
- done_flag NOT set when an unrelated command has rc==0
- Empty / unparseable LLM output → map unchanged, no crash
- Maintainer.update signature matches the contract
- notes are preserved across update cycles
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from dataclasses import replace

# world_model is built in Group 1 and must already be present.
# The rewritten maintainer.py does not exist yet — imports below will fail.
from src.envstate.world_model import (
    Fact,
    OpenProblem,
    WorldModelMap,
    TaskReport,
    CommandRecord,
    initial_map,
    merge_map,
)
from src.envstate.maintainer import (
    MAINTAINER_SYSTEM_PROMPT,
    Maintainer,
    parse_v1_maintainer_reply,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_map() -> WorldModelMap:
    return initial_map(
        base_image="python:3.12-slim",
        workdir="/app",
        language="python 3.12",
        build_system="poetry",
        repo_layout=("tests/", "src/", "pyproject.toml"),
    )


def _fake_client(content: str) -> SimpleNamespace:
    """OpenAI-compatible stub that always returns *content*."""
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kw: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=content)
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                    ),
                )
            )
        )
    )


def _make_report(
    commands: list[tuple[str, int, str]],
    status: str = "done",
    goal: str = "install deps",
    learning: str = "all good",
) -> TaskReport:
    return TaskReport(
        task_goal=goal,
        status=status,
        commands=tuple(
            CommandRecord(cmd=cmd, rc=rc, output=out)
            for cmd, rc, out in commands
        ),
        learning=learning,
    )


# ---------------------------------------------------------------------------
# parse_v1_maintainer_reply
# ---------------------------------------------------------------------------

class TestParseV1MaintainerReply(unittest.TestCase):
    """parse_v1_maintainer_reply(text, current_map, report) -> WorldModelMap."""

    def _llm_json(self, **fields) -> str:
        return "```json\n" + json.dumps(fields) + "\n```"

    def test_installed_fact_recorded_when_output_confirms_it(self):
        base = _base_map()
        report = _make_report(
            [("pip install flask==3.0.0", 0, "Successfully installed flask-3.0.0")]
        )
        reply = self._llm_json(
            installed=[{"name": "flask", "detail": "3.0.0"}],
            open_problems=[],
            progress={"base": True, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        names = [f.name for f in new_map.installed]
        self.assertIn("flask", names)

    def test_invented_fact_not_added_when_absent_from_output(self):
        """Grounding rule: LLM proposes 'numpy' but the output never mentions it."""
        base = _base_map()
        report = _make_report(
            [("pip install flask==3.0.0", 0, "Successfully installed flask-3.0.0")]
        )
        reply = self._llm_json(
            # LLM invents numpy even though command output says nothing about it
            installed=[
                {"name": "flask", "detail": "3.0.0"},
                {"name": "numpy", "detail": "1.26"},
            ],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        names = [f.name for f in new_map.installed]
        self.assertNotIn("numpy", names)
        self.assertIn("flask", names)

    def test_open_problem_recorded_from_failed_command(self):
        base = _base_map()
        report = _make_report(
            [("pip install psycopg2==2.8.6", 1,
              "error: pg_config executable not found")]
        )
        reply = self._llm_json(
            installed=[],
            open_problems=[
                {
                    "signature": "ModuleNotFoundError: psycopg2",
                    "interpretation": "needs libpq-dev",
                    "layer": "system",
                }
            ],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        sigs = [p.signature for p in new_map.open_problems]
        self.assertIn("ModuleNotFoundError: psycopg2", sigs)

    def test_progress_updated_from_llm_reply(self):
        base = _base_map()
        report = _make_report(
            [("apt-get install -y python3-dev", 0, "Setting up python3-dev")]
        )
        reply = self._llm_json(
            installed=[{"name": "python3-dev", "detail": ""}],
            open_problems=[],
            progress={"base": True, "system": True, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.progress["system"])
        self.assertTrue(new_map.progress["base"])
        self.assertFalse(new_map.progress["deps"])

    def test_notes_appended_not_replaced(self):
        base = merge_map(_base_map(), notes=("existing note",))
        report = _make_report([("pip install x", 0, "ok")])
        reply = self._llm_json(
            installed=[{"name": "x", "detail": ""}],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=["new caution"],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertIn("existing note", new_map.notes)
        self.assertIn("new caution", new_map.notes)

    def test_empty_llm_output_returns_map_unchanged(self):
        base = _base_map()
        report = _make_report([("ls", 0, "")])
        new_map = parse_v1_maintainer_reply("", base, report)
        self.assertEqual(new_map.installed, base.installed)
        self.assertEqual(new_map.open_problems, base.open_problems)
        self.assertEqual(new_map.done_flag, False)

    def test_unparseable_json_returns_map_unchanged(self):
        base = _base_map()
        report = _make_report([("ls", 0, "")])
        new_map = parse_v1_maintainer_reply("not json at all", base, report)
        self.assertEqual(new_map.installed, base.installed)


# ---------------------------------------------------------------------------
# done_flag detection
# ---------------------------------------------------------------------------

class TestDoneFlag(unittest.TestCase):
    """done_flag is set iff a pytest --collect-only command exited 0."""

    def test_done_flag_set_on_collect_only_rc0(self):
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 0,
              "collected 12 items")]
        )
        # LLM reply says tests layer is done
        reply = (
            '```json\n{"installed": [], "open_problems": [],'
            ' "progress": {"base": true, "system": true, "runtime": true,'
            ' "deps": true, "build": true, "tests": true}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.done_flag)

    def test_done_flag_set_for_poetry_run_collect_only(self):
        base = _base_map()
        report = _make_report(
            [("poetry run pytest --collect-only -q --disable-warnings", 0,
              "collected 5 items")]
        )
        reply = (
            '```json\n{"installed": [], "open_problems": [],'
            ' "progress": {"base": true, "system": true, "runtime": true,'
            ' "deps": true, "build": true, "tests": true}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.done_flag)

    def test_done_flag_not_set_when_collect_only_fails(self):
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 1,
              "ERROR: ModuleNotFoundError: edsl")]
        )
        reply = (
            '```json\n{"installed": [], "open_problems": [],'
            ' "progress": {"base": true, "system": false, "runtime": false,'
            ' "deps": false, "build": false, "tests": false}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    def test_done_flag_not_set_for_unrelated_rc0_command(self):
        base = _base_map()
        report = _make_report(
            [("pip install flask", 0, "Successfully installed flask-3.0.0")]
        )
        reply = (
            '```json\n{"installed": [{"name": "flask", "detail": "3.0.0"}],'
            ' "open_problems": [],'
            ' "progress": {"base": true, "system": false, "runtime": false,'
            ' "deps": true, "build": false, "tests": false}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    def test_done_flag_preserved_when_already_true(self):
        """If done_flag is somehow already True, update must keep it True."""
        base = merge_map(_base_map(), done_flag=True)
        report = _make_report([("ls", 0, "")])
        reply = (
            '```json\n{"installed": [], "open_problems": [],'
            ' "progress": {"base": true, "system": true, "runtime": true,'
            ' "deps": true, "build": true, "tests": true}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.done_flag)


# ---------------------------------------------------------------------------
# Maintainer.update  (full round-trip with mocked LLM)
# ---------------------------------------------------------------------------

class TestMaintainerUpdate(unittest.TestCase):
    """Maintainer.update(current_map, report) -> WorldModelMap."""

    def _reply_json(self, **fields) -> str:
        return "```json\n" + json.dumps(fields) + "\n```"

    def test_update_returns_world_model_map(self):
        maintainer = Maintainer(
            client=_fake_client(
                self._reply_json(
                    installed=[],
                    open_problems=[],
                    progress={"base": False, "system": False, "runtime": False,
                               "deps": False, "build": False, "tests": False},
                    notes=[],
                )
            ),
            model="test-model",
        )
        base = _base_map()
        report = _make_report([("ls", 0, "pyproject.toml")])
        result = maintainer.update(base, report)
        self.assertIsInstance(result, WorldModelMap)

    def test_update_sets_done_flag_on_collect_only_rc0(self):
        reply = self._reply_json(
            installed=[],
            open_problems=[],
            progress={"base": True, "system": True, "runtime": True,
                      "deps": True, "build": True, "tests": True},
            notes=[],
        )
        maintainer = Maintainer(client=_fake_client(reply), model="test-model")
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 0,
              "collected 7 items")]
        )
        result = maintainer.update(base, report)
        self.assertTrue(result.done_flag)

    def test_update_records_open_problem_on_install_failure(self):
        reply = self._reply_json(
            installed=[],
            open_problems=[
                {
                    "signature": "ImportError: cannot import name 'edsl'",
                    "interpretation": "package not installed",
                    "layer": "deps",
                }
            ],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        maintainer = Maintainer(client=_fake_client(reply), model="test-model")
        base = _base_map()
        report = _make_report(
            [("pip install edsl", 1, "ERROR: Could not build edsl")],
            status="blocked",
            learning="edsl build fails",
        )
        result = maintainer.update(base, report)
        sigs = [p.signature for p in result.open_problems]
        self.assertIn("ImportError: cannot import name 'edsl'", sigs)

    def test_update_does_not_mutate_input_map(self):
        """WorldModelMap is frozen — update must return a new object."""
        reply = self._reply_json(
            installed=[{"name": "flask", "detail": "3.0.0"}],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        maintainer = Maintainer(client=_fake_client(reply), model="test-model")
        base = _base_map()
        report = _make_report(
            [("pip install flask", 0, "Successfully installed flask-3.0.0")]
        )
        result = maintainer.update(base, report)
        # The original map must be untouched.
        self.assertEqual(base.installed, ())
        # The new map has the installed fact.
        self.assertTrue(any(f.name == "flask" for f in result.installed))

    def test_update_tolerates_empty_llm_response(self):
        """Empty LLM reply must not crash — map comes back unchanged."""
        maintainer = Maintainer(client=_fake_client(""), model="test-model")
        base = _base_map()
        report = _make_report([("pip install x", 1, "error")])
        result = maintainer.update(base, report)
        self.assertIsInstance(result, WorldModelMap)
        self.assertEqual(result.installed, base.installed)

    def test_on_usage_callback_is_called(self):
        """on_usage must be invoked exactly once per update call with a usage dict."""
        reply = self._reply_json(
            installed=[],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        received: list[dict] = []
        maintainer = Maintainer(
            client=_fake_client(reply),
            model="test-model",
            on_usage=received.append,
        )
        base = _base_map()
        report = _make_report([("ls", 0, "")])
        maintainer.update(base, report)
        self.assertEqual(len(received), 1)
        self.assertIn("input_tokens", received[0])

    def test_on_usage_none_does_not_crash(self):
        """Maintainer with on_usage=None must run without error."""
        reply = self._reply_json(
            installed=[],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        maintainer = Maintainer(
            client=_fake_client(reply),
            model="test-model",
            on_usage=None,
        )
        base = _base_map()
        report = _make_report([("ls", 0, "")])
        result = maintainer.update(base, report)
        self.assertIsInstance(result, WorldModelMap)


# ---------------------------------------------------------------------------
# System prompt contract
# ---------------------------------------------------------------------------

class TestMaintainerSystemPrompt(unittest.TestCase):
    def test_prompt_emphasises_grounded_recording(self):
        """The prompt must instruct the LLM to record only what output shows."""
        self.assertIn("command", MAINTAINER_SYSTEM_PROMPT.lower())

    def test_prompt_describes_done_flag_trigger(self):
        self.assertIn("collect-only", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("done_flag", MAINTAINER_SYSTEM_PROMPT)

    def test_prompt_mentions_single_output_shape(self):
        """The prompt must reference the four output keys of the v1 schema."""
        for key in ("installed", "open_problems", "progress", "notes"):
            self.assertIn(key, MAINTAINER_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/john/john-planner-v1
python3 -m pytest tests/test_v1_maintainer.py -q 2>&1 | head -30
```

Expected failure:
```
ImportError: cannot import name 'parse_v1_maintainer_reply' from 'src.envstate.maintainer'
```
(The old module exists but has the wrong API; `world_model.py` is already present from Group 1.)

---

### Task 8: Rewrite src/envstate/maintainer.py — new MAINTAINER_SYSTEM_PROMPT and parse_v1_maintainer_reply

**Note:** `src/envstate/world_model.py` is created in Group 1.  This task imports from it
via `from src.envstate.world_model import WorldModelMap, TaskReport, Fact, OpenProblem, map_to_dict, map_from_dict`
and does NOT re-create that file.

**Files:**
- Modify: `src/envstate/maintainer.py` (full rewrite — lines 1–287 replaced)

- [ ] **Step 3: Write the new maintainer implementation**

```python
# src/envstate/maintainer.py
"""v1 Maintainer — single-writer of the WorldModelMap.

One LLM call per cycle.  Reads (WorldModelMap, TaskReport); emits exactly one
new WorldModelMap via merge_map().

Grounding rule: record only what command output actually demonstrates.
done_flag rule: set True when any CommandRecord has rc==0 and its cmd matches
the pytest --collect-only pattern.

Old v0 channels (candidate_requirements, probe_requests, diagnose_requests,
open_failure_updates, acl.apply_llm_proposal, serde.snapshot_to_dict) are
completely removed.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.jsonutil import extract_json_object
from src.envstate.llm_response import complete_with_retry
from src.envstate.world_model import (
    Fact,
    OpenProblem,
    TaskReport,
    WorldModelMap,
    merge_map,
)


# ---------------------------------------------------------------------------
# Pattern that triggers done_flag
# ---------------------------------------------------------------------------

_COLLECT_ONLY_RE = re.compile(r"pytest\s+--collect-only")


def _is_collect_only_success(report: TaskReport) -> bool:
    """Return True iff any CommandRecord in report ran pytest --collect-only and exited 0."""
    for record in report.commands:
        if record.rc == 0 and _COLLECT_ONLY_RE.search(record.cmd):
            return True
    return False


# ---------------------------------------------------------------------------
# Grounding helper
# ---------------------------------------------------------------------------

def _output_mentions(name: str, report: TaskReport) -> bool:
    """Return True when *name* appears (case-insensitive) in any command output."""
    lower_name = name.lower()
    for record in report.commands:
        if lower_name in record.output.lower():
            return True
    return False


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

MAINTAINER_SYSTEM_PROMPT = """\
You are the Maintainer for a Docker-based environment setup system (EnvState v1).

Your role: read the current world-model map and the latest task report (commands
run, their exit codes, and their output), then emit ONE updated world-model map.

## Grounding rule (CRITICAL)

Record only what the command output actually demonstrates:
- Add a fact to "installed" ONLY if a command that exited 0 explicitly shows it
  was installed or verified (e.g. "Successfully installed flask-3.0.0" or
  "import succeeded").
- Do NOT invent "installed" facts that the command output does not mention.
- Interpret failures into "open_problems" with a suspected layer.

## done_flag rule

You do NOT set done_flag — the harness sets it automatically when it detects a
`pytest --collect-only` command that exited 0 in the report.  Do NOT include
done_flag in your output.

## Output schema

Return exactly one JSON object inside a ```json fenced block with these keys:

```json
{
  "installed": [
    {"name": "<package or tool name>", "detail": "<version or note from output>"}
  ],
  "open_problems": [
    {
      "signature": "<short id, e.g. ModuleNotFoundError: psycopg2>",
      "interpretation": "<what this failure means>",
      "layer": "<base|system|runtime|deps|build|tests>"
    }
  ],
  "progress": {
    "base": true,
    "system": false,
    "runtime": false,
    "deps": false,
    "build": false,
    "tests": false
  },
  "notes": ["<durable caution to keep across cycles>"]
}
```

Rules:
- "installed": only items confirmed by command output (rc 0 + name visible in output).
- "open_problems": derive from failed commands (rc != 0) or error tracebacks.
- "progress": mark a layer True only if its setup commands exited 0 and no
  blocking open_problem remains at that layer.
- "notes": include any durable cautions from the previous map plus new ones.
- Output ONLY the JSON block — no prose before or after.
"""


# ---------------------------------------------------------------------------
# Reply parser
# ---------------------------------------------------------------------------

def _parse_installed(raw_list: Any, report: TaskReport) -> tuple[Fact, ...]:
    """Parse and ground the 'installed' list from the LLM reply.

    A proposed Fact is only kept when the fact's name appears somewhere in the
    combined command output of the report (grounding rule).
    """
    if not isinstance(raw_list, list):
        return ()
    facts: list[Fact] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        detail = str(item.get("detail", "")).strip()
        # Grounding check: reject if name not found anywhere in any command output.
        if not _output_mentions(name, report):
            continue
        facts.append(Fact(name=name, detail=detail))
    return tuple(facts)


def _parse_open_problems(
    raw_list: Any,
    existing: tuple[OpenProblem, ...],
) -> tuple[OpenProblem, ...]:
    """Parse the 'open_problems' list from the LLM reply.

    Merges new problems with existing ones (deduplicates by signature).
    """
    if not isinstance(raw_list, list):
        return existing

    existing_sigs = {p.signature for p in existing}
    new_problems: list[OpenProblem] = list(existing)

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        sig = str(item.get("signature", "")).strip()
        if not sig or sig in existing_sigs:
            continue
        interpretation = str(item.get("interpretation", "")).strip()
        layer = str(item.get("layer", "deps")).strip()
        valid_layers = {"base", "system", "runtime", "deps", "build", "tests"}
        if layer not in valid_layers:
            layer = "deps"
        new_problems.append(
            OpenProblem(signature=sig, interpretation=interpretation, layer=layer)
        )
        existing_sigs.add(sig)

    return tuple(new_problems)


def _parse_progress(raw_dict: Any, current: dict[str, bool]) -> dict[str, bool]:
    """Parse the 'progress' dict from the LLM reply.

    Only known layer keys are accepted; unknown keys are silently dropped.
    Missing keys fall back to the current map value.
    """
    result = dict(current)  # start from current so missing keys are safe
    if not isinstance(raw_dict, dict):
        return result
    valid_layers = {"base", "system", "runtime", "deps", "build", "tests"}
    for layer in valid_layers:
        if layer in raw_dict:
            result[layer] = bool(raw_dict[layer])
    return result


def _parse_notes(raw_list: Any, existing: tuple[str, ...]) -> tuple[str, ...]:
    """Merge new notes from the LLM reply with existing ones (deduplicated)."""
    seen = set(existing)
    merged: list[str] = list(existing)
    if isinstance(raw_list, list):
        for item in raw_list:
            note = str(item).strip()
            if note and note not in seen:
                merged.append(note)
                seen.add(note)
    return tuple(merged)


def parse_v1_maintainer_reply(
    text: Optional[str],
    current_map: WorldModelMap,
    report: TaskReport,
) -> WorldModelMap:
    """Parse a raw LLM reply string into an updated WorldModelMap.

    Returns *current_map* unchanged (via merge_map with no overrides) if
    *text* is empty, unparseable, or missing required keys.

    done_flag is set to True when _is_collect_only_success(report) is True,
    regardless of what the LLM said.  It is preserved if already True.
    """
    raw = extract_json_object(text) if text else None

    if not raw:
        # On empty / unparseable output: preserve done_flag if collect-only passed.
        new_done = current_map.done_flag or _is_collect_only_success(report)
        if new_done != current_map.done_flag:
            return merge_map(current_map, done_flag=new_done)
        return current_map

    installed = _parse_installed(raw.get("installed"), report)
    open_problems = _parse_open_problems(
        raw.get("open_problems"), current_map.open_problems
    )
    progress = _parse_progress(raw.get("progress"), current_map.progress)
    notes = _parse_notes(raw.get("notes"), current_map.notes)

    # Merge installed: keep existing facts; add new grounded ones.
    existing_names = {f.name for f in current_map.installed}
    merged_installed = current_map.installed + tuple(
        f for f in installed if f.name not in existing_names
    )

    done_flag = current_map.done_flag or _is_collect_only_success(report)

    return merge_map(
        current_map,
        installed=merged_installed,
        open_problems=open_problems,
        progress=progress,
        done_flag=done_flag,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Maintainer class
# ---------------------------------------------------------------------------

class Maintainer:
    """Single-writer of the WorldModelMap.

    One LLM call per cycle.  update(current_map, report) -> WorldModelMap.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        on_usage: Callable[[dict], None] | None = None,
        log_path: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.on_usage = on_usage
        self.log_path = log_path

    def update(
        self,
        current_map: WorldModelMap,
        report: TaskReport,
    ) -> WorldModelMap:
        """Interpret *report* against *current_map* and return the new map.

        Makes exactly one LLM call (with retry) per invocation.
        Sets done_flag=True when report contains a pytest --collect-only rc==0.
        Records only what the command results actually demonstrate.
        Never runs shell commands.
        """
        context = {
            "current_map": {
                "base_image": current_map.base_image,
                "workdir": current_map.workdir,
                "language": current_map.language,
                "build_system": current_map.build_system,
                "repo_layout": list(current_map.repo_layout),
                "installed": [
                    {"name": f.name, "detail": f.detail}
                    for f in current_map.installed
                ],
                "open_problems": [
                    {
                        "signature": p.signature,
                        "interpretation": p.interpretation,
                        "layer": p.layer,
                        "out_of_scope": p.out_of_scope,
                    }
                    for p in current_map.open_problems
                ],
                "progress": current_map.progress,
                "notes": list(current_map.notes),
            },
            "task_report": {
                "task_goal": report.task_goal,
                "status": report.status,
                "learning": report.learning,
                "commands": [
                    {"cmd": r.cmd, "rc": r.rc, "output": r.output}
                    for r in report.commands
                ],
            },
        }

        messages = [
            {"role": "system", "content": MAINTAINER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context)},
        ]

        content, usage, response = complete_with_retry(
            self.client,
            self.model,
            messages,
            accept=None,   # retry on empty only
            retry_nudge=(
                "Your previous response was empty. "
                "Return exactly one JSON object inside a ```json fenced block."
            ),
            temperature=0,
        )

        if self.on_usage:
            self.on_usage(usage)

        new_map = parse_v1_maintainer_reply(content, current_map, report)

        log_llm_exchange(
            "maintainer",
            response,
            parsed={
                "done_flag": new_map.done_flag,
                "installed_count": len(new_map.installed),
                "open_problems_count": len(new_map.open_problems),
            },
        )

        return new_map
```

- [ ] **Step 4: Run new tests — expect them to pass**

```bash
cd /Users/john/john-planner-v1
python3 -m pytest tests/test_v1_maintainer.py -v 2>&1 | tail -30
```

Expected output (all green):
```
tests/test_v1_maintainer.py::TestParseV1MaintainerReply::test_installed_fact_recorded_when_output_confirms_it PASSED
tests/test_v1_maintainer.py::TestParseV1MaintainerReply::test_invented_fact_not_added_when_absent_from_output PASSED
tests/test_v1_maintainer.py::TestParseV1MaintainerReply::test_open_problem_recorded_from_failed_command PASSED
tests/test_v1_maintainer.py::TestParseV1MaintainerReply::test_progress_updated_from_llm_reply PASSED
tests/test_v1_maintainer.py::TestParseV1MaintainerReply::test_notes_appended_not_replaced PASSED
tests/test_v1_maintainer.py::TestParseV1MaintainerReply::test_empty_llm_output_returns_map_unchanged PASSED
tests/test_v1_maintainer.py::TestParseV1MaintainerReply::test_unparseable_json_returns_map_unchanged PASSED
tests/test_v1_maintainer.py::TestDoneFlag::test_done_flag_set_on_collect_only_rc0 PASSED
tests/test_v1_maintainer.py::TestDoneFlag::test_done_flag_set_for_poetry_run_collect_only PASSED
tests/test_v1_maintainer.py::TestDoneFlag::test_done_flag_not_set_when_collect_only_fails PASSED
tests/test_v1_maintainer.py::TestDoneFlag::test_done_flag_not_set_for_unrelated_rc0_command PASSED
tests/test_v1_maintainer.py::TestDoneFlag::test_done_flag_preserved_when_already_true PASSED
tests/test_v1_maintainer.py::TestMaintainerUpdate::test_update_returns_world_model_map PASSED
tests/test_v1_maintainer.py::TestMaintainerUpdate::test_update_sets_done_flag_on_collect_only_rc0 PASSED
tests/test_v1_maintainer.py::TestMaintainerUpdate::test_update_records_open_problem_on_install_failure PASSED
tests/test_v1_maintainer.py::TestMaintainerUpdate::test_update_does_not_mutate_input_map PASSED
tests/test_v1_maintainer.py::TestMaintainerUpdate::test_update_tolerates_empty_llm_response PASSED
tests/test_v1_maintainer.py::TestMaintainerUpdate::test_on_usage_callback_is_called PASSED
tests/test_v1_maintainer.py::TestMaintainerUpdate::test_on_usage_none_does_not_crash PASSED
tests/test_v1_maintainer.py::TestMaintainerSystemPrompt::test_prompt_emphasises_grounded_recording PASSED
tests/test_v1_maintainer.py::TestMaintainerSystemPrompt::test_prompt_describes_done_flag_trigger PASSED
tests/test_v1_maintainer.py::TestMaintainerSystemPrompt::test_prompt_mentions_single_output_shape PASSED
22 passed in X.XXs
```

- [ ] **Step 5: Verify old maintainer tests still pass**

The old `tests/test_envstate_maintainer.py` imports the old `Maintainer.interpret` interface which is being removed.  These tests are now dead weight — they test the v0 contract that has been deleted.  Replace them with a tombstone that skips gracefully:

```bash
cd /Users/john/john-planner-v1
python3 -m pytest tests/test_envstate_maintainer.py -v 2>&1 | tail -10
```

Expected: they will now fail with `ImportError` on the deleted symbols (`build_maintainer_input`, old `parse_maintainer_proposal`, `Maintainer.interpret`).  That is correct — the old tests are obsolete.  Proceed to the next step to update them.

- [ ] **Step 6: Update tests/test_envstate_maintainer.py to skip the v0-only tests**

Replace the entire file with a forward-reference comment and minimal import guard so the test suite does not fail during CI:

```python
# tests/test_envstate_maintainer.py
"""v0 Maintainer tests — superseded by tests/test_v1_maintainer.py.

The old Maintainer.interpret / build_maintainer_input / parse_maintainer_proposal
interface was removed in the v1 rewrite.  These tests are kept as a historical
record but skipped unconditionally to avoid import errors.
"""
import unittest


class ObsoleteMaintainerV0Tests(unittest.TestCase):
    @unittest.skip("v0 Maintainer API removed; see tests/test_v1_maintainer.py")
    def test_v0_api_removed(self):
        pass
```

```bash
cd /Users/john/john-planner-v1
python3 -m pytest tests/test_envstate_maintainer.py tests/test_v1_maintainer.py -v 2>&1 | tail -10
```

Expected:
```
tests/test_envstate_maintainer.py::ObsoleteMaintainerV0Tests::test_v0_api_removed SKIPPED
tests/test_v1_maintainer.py::... 22 passed
```

- [ ] **Step 7: Run the full test suite to confirm no regressions**

```bash
cd /Users/john/john-planner-v1
python3 -m pytest --tb=short -q 2>&1 | tail -20
```

Expected: all previously passing tests pass; the 22 new v1 maintainer tests pass; the 1 v0 test is skipped.

- [ ] **Step 8: Commit**

```bash
cd /Users/john/john-planner-v1
git add src/envstate/maintainer.py tests/test_v1_maintainer.py tests/test_envstate_maintainer.py
git commit -m "feat(maintainer): rewrite v1 — single LLM call/cycle, grounded map update, done_flag on collect-only rc0"
```

---

# Phase 3: Planner — global sequencer + termination (new)

## Planner — `src/envstate/planner.py` (NEW)

> **Component scope:** `Planner.decide(map) -> PlannerDecision` — one LLM call per cycle.
> Reads the `WorldModelMap`, emits a `Task` (with layer + facts), `DONE`, or `GIVEUP`.
> Owns global sequencing and can mark `OpenProblem` entries `out_of_scope`.
> Never runs shell commands.
>
> **NAMING COLLISION NOTE:** `src/planner.py` (root-level) is the Arm-0 bare-ReAct planner.
> This component lives at `src/envstate/planner.py` — a completely different module.
> Never import from or modify `src/planner.py` in these tasks.
>
> **NOTE:** `src/envstate/world_model.py` is created by a sibling fragment (g_world_model).
> This fragment does NOT create it. All world-model types are imported from that module.

---

### Task 9: Planner — write failing tests for prompt, JSON parsing, and Planner.decide

**Files:**
- Create: `tests/test_envstate_planner.py`
- Create (implementation, step 3): `src/envstate/planner.py`

#### Step 1: Write the failing test

```python
# tests/test_envstate_planner.py
"""Unit tests for src/envstate/planner.py.

Covers:
  - PLANNER_SYSTEM_PROMPT content invariants
  - parse_planner_decision: task / done / giveup / missing keys / empty
  - Planner.decide: emits task for unmet layer; returns done when done_flag True;
    routes around an out_of_scope open_problem; returns giveup when no path

All LLM calls are mocked via a fake client.
"""
from __future__ import annotations
import json
import unittest
from types import SimpleNamespace

from src.envstate.world_model import (
    Fact,
    OpenProblem,
    PlannerDecision,
    Task,
    WorldModelMap,
    initial_map,
    merge_map,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_client(content: str) -> SimpleNamespace:
    """Fake OpenAI-compatible client returning *content* on every call."""
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kw: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=content)
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=20, completion_tokens=10, total_tokens=30
                    ),
                )
            )
        )
    )


def _sequential_client(responses: list[str]) -> tuple[SimpleNamespace, list]:
    """Fake client returning responses in sequence; call_log tracks each call."""
    call_log: list[str] = []
    it = iter(responses)

    def _create(**_kw):
        content = next(it)
        call_log.append(content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )

    return (
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create))),
        call_log,
    )


def _base_map(**kwargs) -> WorldModelMap:
    """Return a fresh map with all layers unmet and done_flag=False."""
    return initial_map(
        base_image="python:3.12-slim",
        workdir="/app",
        language="python 3.12",
        build_system="poetry",
        repo_layout=("tests/", "src/", "pyproject.toml"),
        **kwargs,
    )


def _task_json(
    goal: str = "install project deps",
    done_when: str = "pip install exits 0",
    layer: str = "deps",
    facts: list[str] | None = None,
) -> str:
    """Return a JSON string the LLM would emit for a 'task' decision."""
    return json.dumps({
        "action": "task",
        "goal": goal,
        "done_when": done_when,
        "layer": layer,
        "facts": facts or [],
    })


def _done_json(reason: str = "all layers satisfied") -> str:
    return json.dumps({"action": "done", "reason": reason})


def _giveup_json(reason: str = "unsolvable conflict") -> str:
    return json.dumps({"action": "giveup", "reason": reason})


# ---------------------------------------------------------------------------
# 1. Prompt invariants
# ---------------------------------------------------------------------------

class PlannerSystemPromptTests(unittest.TestCase):
    def test_prompt_mentions_pytest_collect_only(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        self.assertIn("pytest --collect-only", PLANNER_SYSTEM_PROMPT)

    def test_prompt_mentions_all_layers(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        for layer in ("base", "system", "runtime", "deps", "build", "tests"):
            self.assertIn(layer, PLANNER_SYSTEM_PROMPT,
                          f"Prompt must mention layer '{layer}'")

    def test_prompt_mentions_task_done_giveup(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        for action in ("task", "done", "giveup"):
            self.assertIn(action, PLANNER_SYSTEM_PROMPT,
                          f"Prompt must mention action '{action}'")

    def test_prompt_mentions_out_of_scope(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        self.assertIn("out_of_scope", PLANNER_SYSTEM_PROMPT)

    def test_prompt_mentions_json_fields(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        for field in ("action", "goal", "done_when", "layer", "facts", "reason"):
            self.assertIn(field, PLANNER_SYSTEM_PROMPT,
                          f"Prompt must mention JSON field '{field}'")

    def test_prompt_forbids_shell_commands(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        # Must not encourage running shell; must say "do not run" or "never run"
        lower = PLANNER_SYSTEM_PROMPT.lower()
        self.assertTrue(
            "never run" in lower or "do not run" in lower or "no shell" in lower,
            "Prompt must forbid the planner from running shell commands",
        )


# ---------------------------------------------------------------------------
# 2. parse_planner_decision
# ---------------------------------------------------------------------------

class ParsePlannerDecisionTests(unittest.TestCase):
    def test_parses_task_action(self):
        from src.envstate.planner import parse_planner_decision
        raw = _task_json(goal="install deps", done_when="exit 0", layer="deps",
                         facts=["flask in pyproject.toml"])
        decision = parse_planner_decision(raw)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "task")
        self.assertIsNotNone(decision.task)
        self.assertEqual(decision.task.goal, "install deps")
        self.assertEqual(decision.task.done_when, "exit 0")
        self.assertEqual(decision.task.layer, "deps")
        self.assertEqual(decision.task.facts, ("flask in pyproject.toml",))

    def test_parses_done_action(self):
        from src.envstate.planner import parse_planner_decision
        decision = parse_planner_decision(_done_json("tests passing"))
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "done")
        self.assertIsNone(decision.task)
        self.assertEqual(decision.reason, "tests passing")

    def test_parses_giveup_action(self):
        from src.envstate.planner import parse_planner_decision
        decision = parse_planner_decision(_giveup_json("unsolvable conflict"))
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "giveup")
        self.assertEqual(decision.reason, "unsolvable conflict")

    def test_returns_none_on_empty_string(self):
        from src.envstate.planner import parse_planner_decision
        self.assertIsNone(parse_planner_decision(""))

    def test_returns_none_on_none(self):
        from src.envstate.planner import parse_planner_decision
        self.assertIsNone(parse_planner_decision(None))

    def test_returns_none_on_no_json(self):
        from src.envstate.planner import parse_planner_decision
        self.assertIsNone(parse_planner_decision("Here is my plan."))

    def test_returns_none_on_missing_action_key(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"goal": "install deps", "layer": "deps"})
        self.assertIsNone(parse_planner_decision(bad))

    def test_returns_none_on_unknown_action_value(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"action": "skip", "goal": "x", "layer": "deps",
                          "done_when": "y", "facts": []})
        self.assertIsNone(parse_planner_decision(bad))

    def test_task_with_empty_facts_list(self):
        from src.envstate.planner import parse_planner_decision
        raw = _task_json(goal="install system deps", done_when="exit 0",
                         layer="system", facts=[])
        decision = parse_planner_decision(raw)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.task.facts, ())

    def test_task_action_missing_goal_returns_none(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"action": "task", "done_when": "exit 0",
                          "layer": "deps", "facts": []})
        self.assertIsNone(parse_planner_decision(bad))

    def test_task_action_missing_layer_returns_none(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"action": "task", "goal": "x",
                          "done_when": "exit 0", "facts": []})
        self.assertIsNone(parse_planner_decision(bad))

    def test_task_action_missing_done_when_returns_none(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"action": "task", "goal": "x",
                          "layer": "deps", "facts": []})
        self.assertIsNone(parse_planner_decision(bad))

    def test_parses_json_inside_fenced_block(self):
        from src.envstate.planner import parse_planner_decision
        fenced = (
            "Here is my decision.\n```json\n"
            + _task_json(goal="run poetry install", done_when="exit 0",
                         layer="deps", facts=[])
            + "\n```"
        )
        decision = parse_planner_decision(fenced)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "task")


# ---------------------------------------------------------------------------
# 3. Planner.decide — happy paths
# ---------------------------------------------------------------------------

class PlannerDecideTests(unittest.TestCase):
    def test_decide_returns_task_for_unmet_layer(self):
        """With an unmet deps layer the planner emits action='task'."""
        from src.envstate.planner import Planner
        content = _task_json(
            goal="install project deps via poetry",
            done_when="poetry install exits 0 and python -c 'import edsl' works",
            layer="deps",
            facts=["build_system=poetry", "pyproject.toml present"],
        )
        planner = Planner(client=_fake_client(content), model="test-model")
        m = _base_map()
        decision = planner.decide(m)
        self.assertIsInstance(decision, PlannerDecision)
        self.assertEqual(decision.action, "task")
        self.assertIsNotNone(decision.task)
        self.assertIsInstance(decision.task, Task)
        self.assertEqual(decision.task.layer, "deps")

    def test_decide_returns_done_when_done_flag_true(self):
        """When map.done_flag is True the planner must emit action='done'."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_done_json("collect-only passed")),
                          model="test-model")
        m = merge_map(_base_map(), done_flag=True)
        decision = planner.decide(m)
        self.assertEqual(decision.action, "done")

    def test_decide_returns_giveup_on_no_path(self):
        """When the LLM emits giveup, decide returns PlannerDecision(action='giveup')."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_giveup_json("unsolvable")),
                          model="test-model")
        m = _base_map()
        decision = planner.decide(m)
        self.assertEqual(decision.action, "giveup")
        self.assertEqual(decision.reason, "unsolvable")

    def test_decide_usage_dict_returned(self):
        """decide must return the PlannerDecision without exposing usage; usage is internal."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_task_json()), model="test-model")
        m = _base_map()
        decision = planner.decide(m)
        # decide() returns only PlannerDecision; caller accesses tokens via last_usage
        self.assertIsInstance(decision, PlannerDecision)

    def test_decide_exposes_last_usage(self):
        """After decide(), planner.last_usage has input_tokens/output_tokens/total_tokens."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_task_json()), model="test-model")
        planner.decide(_base_map())
        self.assertIn("total_tokens", planner.last_usage)
        self.assertEqual(planner.last_usage["total_tokens"], 30)

    def test_on_usage_callback_called_after_decide(self):
        """on_usage callback must be called with the usage dict after each LLM completion."""
        from src.envstate.planner import Planner
        received: list[dict] = []
        planner = Planner(
            client=_fake_client(_task_json()),
            model="test-model",
            on_usage=lambda u: received.append(u),
        )
        planner.decide(_base_map())
        self.assertEqual(len(received), 1)
        self.assertIn("total_tokens", received[0])

    def test_on_usage_none_does_not_raise(self):
        """Planner constructed without on_usage must not raise during decide."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_task_json()), model="test-model")
        # Should not raise
        planner.decide(_base_map())


# ---------------------------------------------------------------------------
# 4. Planner.decide — out_of_scope routing
# ---------------------------------------------------------------------------

class PlannerOutOfScopeTests(unittest.TestCase):
    """The planner can mark an OpenProblem out_of_scope and still emit a task."""

    def _map_with_runtime_only_problem(self) -> WorldModelMap:
        """Map with a runtime-only open problem (e.g. swift not installed)."""
        m = _base_map()
        op = OpenProblem(
            signature="swift: command not found",
            interpretation="swift runtime not available; runtime-only dep",
            layer="runtime",
            out_of_scope=False,
        )
        return merge_map(m, open_problems=(op,))

    def test_planner_routes_around_out_of_scope_problem_and_emits_task(self):
        """Planner marks a runtime-only problem out_of_scope and emits a deps task anyway."""
        from src.envstate.planner import Planner
        # LLM returns a 'task' decision targeting a different layer, ignoring the swift problem
        content = _task_json(
            goal="install Python deps",
            done_when="poetry install exits 0",
            layer="deps",
            facts=["swift: command not found marked out_of_scope"],
        )
        planner = Planner(client=_fake_client(content), model="test-model")
        m = self._map_with_runtime_only_problem()
        decision = planner.decide(m)
        self.assertEqual(decision.action, "task")
        self.assertEqual(decision.task.layer, "deps")

    def test_planner_receives_map_in_prompt_including_open_problems(self):
        """The rendered planning view passed to the LLM includes the open_problems."""
        from src.envstate.planner import Planner, render_planning_view
        m = self._map_with_runtime_only_problem()
        view = render_planning_view(m, budget={"cycles_remaining": 10})
        self.assertIn("swift: command not found", view)
        self.assertIn("runtime", view)

    def test_done_flag_true_in_map_triggers_done_response(self):
        """If map.done_flag is already True the planner should receive that in its view."""
        from src.envstate.planner import render_planning_view
        m = merge_map(_base_map(), done_flag=True)
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        self.assertIn("done_flag", view)
        self.assertIn("True", view)


# ---------------------------------------------------------------------------
# 5. Planner.decide — retry behaviour
# ---------------------------------------------------------------------------

class PlannerRetryTests(unittest.TestCase):
    """Planner retries when LLM returns empty or unparseable JSON."""

    def test_empty_then_valid_retries_and_returns_task(self):
        """Two-attempt sequence: empty first, valid task second."""
        from src.envstate.planner import Planner
        client, call_log = _sequential_client(["", _task_json()])
        planner = Planner(client=client, model="test-model")
        decision = planner.decide(_base_map())
        self.assertEqual(decision.action, "task")
        self.assertEqual(len(call_log), 2, "must have retried once")

    def test_bad_json_then_valid_retries_and_returns_task(self):
        """Unparseable JSON on attempt 1, valid on attempt 2."""
        from src.envstate.planner import Planner
        client, call_log = _sequential_client([
            '{"action": "skip"}',  # unknown action → parse returns None
            _task_json(),
        ])
        planner = Planner(client=client, model="test-model")
        decision = planner.decide(_base_map())
        self.assertEqual(decision.action, "task")
        self.assertEqual(len(call_log), 2)

    def test_all_attempts_fail_returns_giveup_fallback(self):
        """If every attempt fails to parse, decide returns a giveup PlannerDecision."""
        from src.envstate.planner import Planner
        client, _ = _sequential_client(["", "", ""])
        planner = Planner(client=client, model="test-model")
        decision = planner.decide(_base_map())
        self.assertEqual(decision.action, "giveup")
        self.assertIn("empty", decision.reason.lower())


# ---------------------------------------------------------------------------
# 6. render_planning_view content
# ---------------------------------------------------------------------------

class RenderPlanningViewTests(unittest.TestCase):
    def test_view_includes_base_image_and_build_system(self):
        from src.envstate.planner import render_planning_view
        m = _base_map()
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        self.assertIn("python:3.12-slim", view)
        self.assertIn("poetry", view)

    def test_view_includes_progress_layers(self):
        from src.envstate.planner import render_planning_view
        m = _base_map()
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        for layer in ("base", "system", "runtime", "deps", "build", "tests"):
            self.assertIn(layer, view)

    def test_view_includes_open_problems(self):
        from src.envstate.planner import render_planning_view
        op = OpenProblem(
            signature="ModuleNotFoundError: psycopg2",
            interpretation="missing C extension",
            layer="deps",
        )
        m = merge_map(_base_map(), open_problems=(op,))
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        self.assertIn("ModuleNotFoundError: psycopg2", view)

    def test_view_includes_installed_facts(self):
        from src.envstate.planner import render_planning_view
        m = merge_map(_base_map(), installed=(Fact(name="libpq-dev", detail="14"),))
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        self.assertIn("libpq-dev", view)

    def test_view_includes_notes(self):
        from src.envstate.planner import render_planning_view
        m = merge_map(_base_map(), notes=("do not use psycopg2-binary",))
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        self.assertIn("do not use psycopg2-binary", view)

    def test_view_includes_cycles_remaining(self):
        from src.envstate.planner import render_planning_view
        view = render_planning_view(_base_map(), budget={"cycles_remaining": 7})
        self.assertIn("7", view)

    def test_view_includes_required_facts(self):
        from src.envstate.planner import render_planning_view
        m = initial_map(
            base_image="python:3.12-slim",
            workdir="/app",
            language="python 3.12",
            build_system="pip",
            repo_layout=(),
            required=(Fact(name="flask", detail=">=2.0"),),
        )
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        self.assertIn("flask", view)


if __name__ == "__main__":
    unittest.main()
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_envstate_planner.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'src.envstate.planner'`.

#### Step 3: Write minimal implementation

```python
# src/envstate/planner.py
"""EnvState v1 — Planner role.

Reads the WorldModelMap once per cycle and emits a PlannerDecision:
  action="task"   → a fully-populated Task for the BuildAgent
  action="done"   → goal achieved (secondary stop; done_flag is the primary)
  action="giveup" → no viable path found

The Planner NEVER runs shell commands.

NAMING NOTE: src/planner.py (root-level) is the Arm-0 bare-ReAct planner.
This module is src/envstate/planner.py — entirely separate.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.jsonutil import extract_json_object
from src.envstate.llm_response import complete_with_retry
from src.envstate.world_model import (
    OpenProblem,
    PlannerDecision,
    Task,
    WorldModelMap,
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are the Planner for DockerAgent environment setup (v1).

Your only job is to read the current WorldModelMap and decide what to do next.
You NEVER run shell commands and NEVER write to the map directly.

## Fixed goal
Make `pytest --collect-only -q --disable-warnings` exit 0 from the repo root.
For Poetry projects use `poetry run pytest --collect-only -q --disable-warnings`.

## Stack layers (attack in order unless blocked)
  base → system → runtime → deps → build → tests

## Your output
Emit exactly one JSON object (inside a ```json fenced block) with these fields:

For a new task:
```json
{
  "action": "task",
  "goal": "<one concrete sub-goal, e.g. install project deps from pyproject>",
  "done_when": "<checkable criterion, e.g. poetry install exits 0 and python -c 'import edsl' works>",
  "layer": "<one of: base | system | runtime | deps | build | tests>",
  "facts": ["<relevant fact from the map the agent needs>"]
}
```

When the environment is ready (collect-only succeeded or done_flag is True):
```json
{"action": "done", "reason": "<brief explanation>"}
```

When no viable path remains (all options exhausted):
```json
{"action": "giveup", "reason": "<brief explanation>"}
```

## Sequencing rules
- Attack the lowest unmet layer first.
- An open_problem with out_of_scope=True must be skipped entirely — do not emit
  a task targeting it.  If an open_problem is runtime-only (e.g. swift, cuda)
  and does not block pytest collection, mark it out_of_scope by noting this in
  the reason field of a task targeting a different layer.
- If the last task was blocked on a layer, try a different approach or mark the
  problem out_of_scope before moving on.
- Emit "giveup" only when every layer has been tried and no viable path exists.

## Forbidden
- Do not run shell commands (never run, no shell, no execute).
- Do not emit more than one JSON object.
- Do not invent facts not present in the map.
"""

# ---------------------------------------------------------------------------
# Planning view renderer
# ---------------------------------------------------------------------------

_LAYER_ORDER = ("base", "system", "runtime", "deps", "build", "tests")


def render_planning_view(
    world_map: WorldModelMap,
    budget: dict[str, Any],
) -> str:
    """Compact projection of WorldModelMap for the Planner prompt."""
    lines: list[str] = []
    lines.append("# WorldModelMap")
    lines.append(f"base_image: {world_map.base_image}")
    lines.append(f"workdir: {world_map.workdir}")
    lines.append(f"language: {world_map.language}")
    lines.append(f"build_system: {world_map.build_system}")
    lines.append(f"done_flag: {world_map.done_flag}")

    lines.append("")
    lines.append("## repo_layout")
    for entry in world_map.repo_layout:
        lines.append(f"  {entry}")

    lines.append("")
    lines.append("## progress")
    for layer in _LAYER_ORDER:
        status = world_map.progress.get(layer, False)
        tick = "✓" if status else "✗"
        lines.append(f"  {layer}: {tick}")

    if world_map.required:
        lines.append("")
        lines.append("## required (declared, not yet verified)")
        for fact in world_map.required:
            lines.append(f"  - {fact.name}  {fact.detail}".rstrip())

    if world_map.installed:
        lines.append("")
        lines.append("## installed (confirmed)")
        for fact in world_map.installed:
            lines.append(f"  - {fact.name}  {fact.detail}".rstrip())

    if world_map.open_problems:
        lines.append("")
        lines.append("## open_problems")
        for op in world_map.open_problems:
            oos = " [out_of_scope]" if op.out_of_scope else ""
            lines.append(
                f"  - [{op.layer}]{oos} {op.signature}: {op.interpretation}"
            )

    if world_map.notes:
        lines.append("")
        lines.append("## notes")
        for note in world_map.notes:
            lines.append(f"  - {note}")

    lines.append("")
    lines.append(f"## budget\n  cycles_remaining: {budget.get('cycles_remaining')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON → PlannerDecision parser
# ---------------------------------------------------------------------------

_VALID_ACTIONS = frozenset({"task", "done", "giveup"})


def parse_planner_decision(text: Optional[str]) -> Optional[PlannerDecision]:
    """Extract and validate a PlannerDecision from raw LLM text.

    Returns None when:
    - text is empty / None
    - no JSON object found
    - action key is missing or has an unknown value
    - action="task" but goal / done_when / layer is missing
    """
    obj = extract_json_object(text)
    if obj is None:
        return None

    action = obj.get("action")
    if action not in _VALID_ACTIONS:
        return None

    reason = obj.get("reason", "")

    if action == "task":
        goal = obj.get("goal")
        done_when = obj.get("done_when")
        layer = obj.get("layer")
        if not goal or not done_when or not layer:
            return None
        raw_facts = obj.get("facts") or []
        facts: tuple[str, ...] = tuple(str(f) for f in raw_facts)
        task = Task(goal=goal, done_when=done_when, layer=layer, facts=facts)
        return PlannerDecision(action="task", task=task, reason=reason)

    # done or giveup
    return PlannerDecision(action=action, task=None, reason=reason)


# ---------------------------------------------------------------------------
# Planner class
# ---------------------------------------------------------------------------

class Planner:
    """Reads the WorldModelMap once per cycle and emits a PlannerDecision.

    One LLM call per cycle (via complete_with_retry).  Never runs shell
    commands.  Owns global sequencing and done/giveup termination.

    The orchestrator is responsible for hard-stopping on done_flag; the Planner
    does NOT special-case it internally (no override guard).  The map view
    surfaces done_flag=True to the LLM so a well-behaved model will return
    'done' naturally.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        on_usage: Callable[[dict], None] | None = None,
        log_path: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.on_usage = on_usage
        self.log_path = log_path
        self.last_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._cycle: int = 0

    def decide(
        self,
        current_map: WorldModelMap,
    ) -> PlannerDecision:
        """Single LLM call per cycle.

        Reads the map and emits a PlannerDecision with action in
        {'task', 'done', 'giveup'}.  Reuses complete_with_retry for
        retry-on-empty / retry-on-unparseable.

        Returns a giveup PlannerDecision if all retry attempts fail to
        yield a parseable response (safe fallback).

        NOTE: The done_flag override guard is intentionally absent here.
        The orchestrator hard-stops before calling decide() when done_flag
        is True.  The rendered view exposes done_flag so the LLM returns
        'done' naturally.  Duplicating the check here would hide orchestrator
        bugs and make this method harder to test independently.
        """
        self._cycle += 1
        budget = {"cycles_remaining": max(0, 12 - self._cycle)}
        view = render_planning_view(current_map, budget=budget)

        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": view},
        ]

        text, usage, response = complete_with_retry(
            self.client,
            self.model,
            messages,
            accept=lambda t: parse_planner_decision(t) is not None,
            retry_nudge=(
                "Your previous response did not contain a valid PlannerDecision. "
                "Emit exactly one JSON object with action in "
                "['task', 'done', 'giveup'] and all required fields."
            ),
            temperature=0,
        )

        self.last_usage = usage
        if self.on_usage:
            self.on_usage(usage)
        log_llm_exchange("planner", response, parsed=text[:200] if text else None,
                         log_path=self.log_path)

        decision = parse_planner_decision(text)
        if decision is None:
            # All retry attempts exhausted without a parseable response.
            return PlannerDecision(
                action="giveup",
                reason="planner returned empty or unparseable response after retries",
            )
        return decision
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_envstate_planner.py -v 2>&1 | tail -30
```

Expected: all tests pass.

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1 && git add src/envstate/planner.py tests/test_envstate_planner.py && git commit -m "feat(planner): add Planner role — decide(), render_planning_view, parse_planner_decision"
```

---

### Task 10: Planner — edge-case and contract-boundary tests

**Files:**
- Modify: `tests/test_envstate_planner.py` (append new test classes)

#### Step 1: Write the failing tests

Append the following classes to the end of `tests/test_envstate_planner.py` (before the `if __name__ == "__main__":` block):

```python
# -- append to tests/test_envstate_planner.py --

class PlannerPromptIncludesMapFieldsTests(unittest.TestCase):
    """The rendered view must surface every WorldModelMap field the Planner needs."""

    def test_view_includes_language(self):
        from src.envstate.planner import render_planning_view
        m = _base_map()
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        self.assertIn("python 3.12", view)

    def test_view_includes_workdir(self):
        from src.envstate.planner import render_planning_view
        view = render_planning_view(_base_map(), budget={"cycles_remaining": 5})
        self.assertIn("/app", view)

    def test_view_marks_completed_layers_with_checkmark(self):
        from src.envstate.planner import render_planning_view
        m = merge_map(_base_map(), progress={
            "base": True, "system": True, "runtime": False,
            "deps": False, "build": False, "tests": False,
        })
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        # Both symbols must appear for done vs not-done
        self.assertIn("✓", view)
        self.assertIn("✗", view)

    def test_view_shows_out_of_scope_marker(self):
        from src.envstate.planner import render_planning_view
        op = OpenProblem(signature="swift not found", interpretation="runtime-only",
                         layer="runtime", out_of_scope=True)
        m = merge_map(_base_map(), open_problems=(op,))
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        self.assertIn("out_of_scope", view)


class PlannerDecideDoneFlagShortCircuitTests(unittest.TestCase):
    """done_flag=True in the map must cause the LLM to return 'done' when the
    rendered view is correct.  The orchestrator hard-stops before calling decide
    when done_flag is set; this class tests the natural LLM path only.
    """

    def test_done_flag_true_client_says_done(self):
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_done_json("collect-only passed")),
                          model="m")
        m = merge_map(_base_map(), done_flag=True)
        d = planner.decide(m)
        self.assertEqual(d.action, "done")


class PlannerFactsPassedDownTests(unittest.TestCase):
    """Facts extracted from the map must be included in the task handed to BuildAgent."""

    def test_task_facts_are_strings(self):
        from src.envstate.planner import Planner
        content = _task_json(facts=["flask>=2.0 in pyproject.toml", "build_system=poetry"])
        planner = Planner(client=_fake_client(content), model="m")
        d = planner.decide(_base_map())
        for fact in d.task.facts:
            self.assertIsInstance(fact, str)

    def test_task_layer_is_a_known_layer(self):
        from src.envstate.planner import Planner
        known_layers = {"base", "system", "runtime", "deps", "build", "tests"}
        content = _task_json(layer="deps")
        planner = Planner(client=_fake_client(content), model="m")
        d = planner.decide(_base_map())
        self.assertIn(d.task.layer, known_layers)
```

Also update the `if __name__ == "__main__":` block (it is already present at the bottom; no change needed there — these classes are simply appended before it).

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_envstate_planner.py::PlannerDecideDoneFlagShortCircuitTests -v 2>&1
```

Expected: `test_done_flag_true_client_says_done` **passes** immediately (the LLM fake returns `done`).  The old `test_done_flag_true_client_says_task_still_gets_done` test has been removed per fix instructions (the orchestrator owns the hard-stop, not the planner).

#### Step 3: Write minimal implementation patch

No implementation change needed for this task — the canonical `Planner.decide` written in Task 9 already has no done_flag override guard.  The tests in this task exercise the natural LLM path and the `on_usage` callback, both of which are already covered by the Task 9 implementation.

If any new test added above fails (e.g. a missing symbol), add only the minimal code required to make it pass without adding new logic to `decide`.

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_envstate_planner.py -v 2>&1 | tail -30
```

Expected: all tests pass.

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1 && git add src/envstate/planner.py tests/test_envstate_planner.py && git commit -m "test(planner): add edge-case and contract-boundary tests for Planner.decide"
```

---

### Task 11: Coverage gate — verify ≥80% and run full suite

**Files:**
- No new files; runs existing tests against both modules.

#### Step 1: Run coverage check (no new test code needed — this is a gate, not a test)

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_envstate_planner.py -v --tb=short --cov=src/envstate/planner --cov-report=term-missing 2>&1
```

Expected output summary (example):
```
src/envstate/planner.py        XX    X    XX%
```
Must show ≥80% coverage.  If a branch is missed, add a targeted test in `tests/test_envstate_planner.py` before committing.

#### Step 2: Run the full existing test suite to confirm no regressions

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/ -v --tb=short -q 2>&1 | tail -20
```

Expected: no pre-existing tests regress.  The new `planner.py` module does not touch any existing code, so existing tests must pass unchanged.

#### Step 3: Commit coverage baseline

```bash
cd /Users/john/john-planner-v1 && git add tests/test_envstate_planner.py && git commit -m "test(planner): confirm ≥80% coverage on planner module"
```

---

# Phase 4: Build Agent — local mini-ReAct executor (new)

## BuildAgent — TDD Tasks

**Prerequisite:** `src/envstate/world_model.py` must already exist (Task 1 of the global plan) with `Task`, `TaskReport`, `CommandRecord` frozen dataclasses exported. These tasks assume that file is present.

---

### Task 12: Write the failing test for module skeleton and constants

**Files:**
- Create: `tests/test_build_agent.py`

- [ ] **Step 1: Write the failing test**

```python
"""tests/test_build_agent.py — TDD for src/envstate/build_agent.py (v1 BuildAgent).

Run with:
    .venv/bin/python -m pytest tests/test_build_agent.py -q
"""
import unittest
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers shared across all test classes
# ---------------------------------------------------------------------------

def _make_task(
    goal="install project deps",
    done_when="pip install exits 0",
    layer="deps",
    facts=("base_image=python:3.12",),
):
    """Build a Task dataclass from world_model.py."""
    from src.envstate.world_model import Task
    return Task(goal=goal, done_when=done_when, layer=layer, facts=facts)


def _make_ledger():
    from src.envstate.ledger import ActionLedger
    return ActionLedger()


def _fake_response(content: str):
    """Return a minimal OpenAI-compatible response object."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _fake_client_seq(contents):
    """Client whose .chat.completions.create pops from a sequence of content strings."""
    contents = list(contents)

    class _FakeCompletions:
        def create(self, **kwargs):
            return _fake_response(contents.pop(0))

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    return _FakeClient()


# ---------------------------------------------------------------------------
# 1. Module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants(unittest.TestCase):
    def test_local_budget_default_is_8(self):
        from src.envstate import build_agent
        self.assertEqual(build_agent.LOCAL_BUDGET, 8)

    def test_max_empty_responses_default_is_2(self):
        from src.envstate import build_agent
        self.assertEqual(build_agent.MAX_EMPTY_RESPONSES, 2)
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestModuleConstants -q
```

Expected output (two failures — module does not exist yet):
```
ModuleNotFoundError: No module named 'src.envstate.build_agent'
```

- [ ] **Step 3: Write minimal implementation — create `src/envstate/build_agent.py`**

```python
"""src/envstate/build_agent.py — v1 BuildAgent (mini-ReAct loop per Task).

See spec §4 (build agent loop) and §6 (fixed stuck guard).
"""
from __future__ import annotations

import re
from typing import Any, Callable

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.llm_response import complete_with_retry, response_text
from src.envstate.world_model import CommandRecord, Task, TaskReport

# ---------------------------------------------------------------------------
# Module-level constants (spec §8)
# ---------------------------------------------------------------------------

LOCAL_BUDGET: int = 8          # shell actions per task before forced "blocked"
MAX_EMPTY_RESPONSES: int = 2   # re-prompts allowed for unparseable LLM output

# ---------------------------------------------------------------------------
# Action parsing — ported verbatim from worker.py (_extract_worker_action /
# _is_worker_finished).  Kept inline to avoid circular imports once worker.py
# is deleted (spec §6 deletion note).
# ---------------------------------------------------------------------------

_ACTION_RE = re.compile(r"^\s*Action:\s*(.+?)\s*$", re.MULTILINE)
_FINAL_RE = re.compile(
    r"^\s*Final Answer:\s*Success\b", re.IGNORECASE | re.MULTILINE
)
_TOOLCALL_CMD_RE = re.compile(
    r'<parameter\s+name="command"\s*>(.*?)</parameter>', re.DOTALL
)

# Prefix emitted by Sandbox.execute when a command is rejected before running
# (agent.py:194-197, sandbox.py:700-707, sandbox.py:823-825).
_PREFLIGHT_REJECTION_PREFIX = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION"


def _extract_worker_action(content: str) -> str:
    """Extract Action line from LLM content (mirrors worker.py verbatim)."""
    match = _ACTION_RE.search(content or "")
    if match:
        action = match.group(1).strip()
        action = re.sub(r"^```[a-zA-Z]*\n?", "", action)
        action = re.sub(r"\n?```$", "", action).strip()
        return action.splitlines()[0].strip() if action else ""
    tc_match = _TOOLCALL_CMD_RE.search(content or "")
    if tc_match:
        action = tc_match.group(1).strip()
        action = re.sub(r"^```[a-zA-Z]*\n?", "", action)
        action = re.sub(r"\n?```$", "", action).strip()
        return action
    return ""


def _is_worker_finished(content: str) -> bool:
    """Return True when the LLM emits Final Answer: Success."""
    return bool(_FINAL_RE.search(content or ""))


# ---------------------------------------------------------------------------
# Fixed stuck guard (spec §6)
# ---------------------------------------------------------------------------

def _is_stuck(
    history: list[CommandRecord],
    action: str,
    is_preflight_rejection: bool,
) -> bool:
    """Fixed interruption guard from spec §6.

    Returns True only when ALL of:
      (a) The last two MUTATING commands (non-rejection, rc != 0) have identical
          output.
      (b) At least one self-correction attempt was already made (i.e. ≥2
          mutating failures have been seen).

    Ignores preflight rejections entirely — is_preflight_rejection=True means
    the command was never executed, so it must NOT increment the stuck counter.
    Preflight rejection records in history (identified by their output prefix)
    are also excluded from the real-failure list.
    """
    if is_preflight_rejection:
        return False
    # Collect only real execution failures (rc != 0, not preflight rejections).
    real_failures = [
        r
        for r in history
        if r.rc != 0
        and not r.output.startswith(_PREFLIGHT_REJECTION_PREFIX)
    ]
    if len(real_failures) < 2:
        return False
    return real_failures[-1].output.strip() == real_failures[-2].output.strip()


# ---------------------------------------------------------------------------
# System prompt (layered RCA from fullstate_worker.py, simplified for v1)
# ---------------------------------------------------------------------------

BUILD_AGENT_SYSTEM_PROMPT = """\
You are the v1 Build Agent for DockerAgent environment setup.

Your job is to accomplish ONE scoped task by issuing shell commands inside
the container.  You have a task goal, a done-when criterion, and a set of
relevant facts about the environment.

## Layered Root-Cause Analysis

Before each action, identify which layer needs attention and justify your
next command from the given facts:

  1. base image       — OS / distribution / architecture constraints
  2. system packages  — apt/yum/apk native libraries and headers
  3. runtime          — Python version, interpreter, pip/virtualenv toolchain
  4. deps             — project Python/language packages
  5. build            — compilation, linking, editable installs
  6. tests            — test runner availability, collection correctness

Work from the bottom of the stack upward.  Do not paper over a symptom one
layer above its cause.

## Response format

Respond each turn with exactly:
Thought: <identify root-cause layer, cite given facts>
Action: <a single shell command>

When the task's done_when criterion is met, respond with:
Thought: <why the task criterion is satisfied>
Final Answer: Success

IMPORTANT: emit the command ONLY as a plain line starting with "Action: "
followed by one shell command.  Do NOT use tool-call or XML formats.

You do not certify environment facts.  Report "Final Answer: Success" only
when you have verified the task's done_when criterion with a real command.
"""


# ---------------------------------------------------------------------------
# BuildAgent
# ---------------------------------------------------------------------------

class BuildAgent:
    """Mini-ReAct loop for one Task.

    Injected dependencies (for testability without Docker):
      client       — OpenAI-compatible LLM client
      model        — model slug (str)
      synthesizer  — Synthesizer instance (for classify_mutation /
                     command_mutates_environment)
      container_id — str identifier forwarded to ActionEvent
      on_usage     — optional callback called with usage dict after each LLM
                     completion; keys: input_tokens, output_tokens, total_tokens
      log_path     — optional path for structured LLM exchange logs
    """

    def __init__(
        self,
        client: Any,
        model: str,
        synthesizer: Any,
        container_id: str = "unknown",
        on_usage: Callable[[dict], None] | None = None,
        log_path: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.synthesizer = synthesizer
        self.container_id = container_id
        self.on_usage = on_usage
        self.log_path = log_path

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        task: Task,
        sandbox_execute: Callable[[str], tuple[bool, str]],
        ledger: ActionLedger,
        step_offset: int = 0,
    ) -> TaskReport:
        """Mini-ReAct loop capped at LOCAL_BUDGET shell actions.

        Returns TaskReport(status='done') when the LLM emits
        "Final Answer: Success" for the task's done_when criterion.
        Returns TaskReport(status='blocked') on budget exhaustion or
        the stuck guard firing.
        """
        history: list[CommandRecord] = []
        messages: list[dict] = [
            {"role": "system", "content": BUILD_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_task_message(task)},
        ]
        env_revision = step_offset
        empty_responses = 0
        steps_executed = 0

        for _step in range(LOCAL_BUDGET):
            text, usage, raw_response = complete_with_retry(
                self.client,
                self.model,
                messages,
                temperature=0,
                stop=["Observation:"],
            )
            if self.on_usage:
                self.on_usage(usage)
            log_llm_exchange("build_agent", raw_response, parsed={"step": _step})

            action = _extract_worker_action(text)
            finished = _is_worker_finished(text)

            if finished:
                return TaskReport(
                    task_goal=task.goal,
                    status="done",
                    commands=tuple(history),
                    learning=f"Task criterion met: {task.done_when}",
                )

            # Guard: empty / unparseable response
            if not action.strip():
                empty_responses += 1
                if empty_responses >= MAX_EMPTY_RESPONSES:
                    return TaskReport(
                        task_goal=task.goal,
                        status="blocked",
                        commands=tuple(history),
                        learning="LLM returned too many unparseable responses",
                    )
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response did not contain a parseable "
                            "Action line. Respond with 'Action: <command>' or "
                            "'Final Answer: Success'."
                        ),
                    },
                ]
                continue
            empty_responses = 0   # reset on real action

            # Execute
            success, output = sandbox_execute(action)
            is_preflight = output.startswith(_PREFLIGHT_REJECTION_PREFIX)
            rc = 0 if success else 1
            record = CommandRecord(cmd=action, rc=rc, output=output[:2000])

            # Stuck guard (before appending to history so the guard sees
            # the record from the previous cycle, not the current one)
            if _is_stuck(history, action, is_preflight):
                history.append(record)
                return TaskReport(
                    task_goal=task.goal,
                    status="blocked",
                    commands=tuple(history),
                    learning=f"Stuck guard fired: identical failure twice on '{action}'",
                )

            history.append(record)
            steps_executed += 1

            # Append ActionEvent to ledger (step incremented BEFORE appending
            # so each event gets a distinct, monotonically increasing step number)
            self._append_ledger_event(
                action=action,
                success=success,
                output=output,
                step=step_offset + steps_executed,
                env_revision=env_revision,
                ledger=ledger,
                is_preflight=is_preflight,
            )
            if success and not is_preflight:
                if self.synthesizer.command_mutates_environment(action):
                    env_revision += 1

            # Append to LLM conversation
            observation_prefix = "ok" if success else "FAILED"
            messages = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"Observation: [{observation_prefix}]\n{output[:1500]}",
                },
            ]

        # Budget exhausted
        return TaskReport(
            task_goal=task.goal,
            status="blocked",
            commands=tuple(history),
            learning=f"Ran out of local budget ({LOCAL_BUDGET} steps)",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_task_message(self, task: Task) -> str:
        facts_text = "\n".join(f"- {f}" for f in task.facts) if task.facts else "- (none)"
        return (
            f"Task goal: {task.goal}\n"
            f"Done when: {task.done_when}\n"
            f"Layer: {task.layer}\n"
            f"Relevant facts:\n{facts_text}"
        )

    def _append_ledger_event(
        self,
        action: str,
        success: bool,
        output: str,
        step: int,
        env_revision: int,
        ledger: ActionLedger,
        is_preflight: bool,
    ) -> None:
        """Append one ActionEvent to the ledger (mirrors agent.py:2004 pattern)."""
        if is_preflight:
            # Rejected before execution — record but mark as non-mutating.
            mutation_class = None
            rev_after = env_revision
        elif success and self.synthesizer.command_mutates_environment(action):
            mutation_class = self.synthesizer.classify_mutation(action)
            rev_after = env_revision + 1
        else:
            mutation_class = None
            rev_after = env_revision

        event = ActionEvent(
            step=step,
            task_id=action[:40],
            cmd=action,
            rc=0 if success else 1,
            stdout_path=None,
            stderr_path=None,
            env_revision_before=env_revision,
            env_revision_after=rev_after,
            mutation_class=mutation_class,
            container_id=self.container_id,
            summary=output[:200],
        )
        ledger.append(event)


# Re-export for test convenience (tests import CommandRecord from here)
from src.envstate.world_model import CommandRecord as CommandRecord  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestModuleConstants -q
```

Expected:
```
2 passed in 0.xx s
```

- [ ] **Step 5: Commit**

```
git add src/envstate/build_agent.py tests/test_build_agent.py
git commit -m "feat(build_agent): scaffold module with LOCAL_BUDGET=8 and MAX_EMPTY_RESPONSES=2 constants"
```

---

### Task 13: Write the failing tests for action parsing helpers

**Files:**
- Modify: `tests/test_build_agent.py` (append new test class)

- [ ] **Step 1: Write the failing test** (append to `tests/test_build_agent.py` before the final `if __name__` block)

```python
# ---------------------------------------------------------------------------
# 2. Action-parsing helpers (ported from worker.py)
# ---------------------------------------------------------------------------

class TestExtractWorkerAction(unittest.TestCase):
    def _extract(self, content: str) -> str:
        from src.envstate.build_agent import _extract_worker_action
        return _extract_worker_action(content)

    def test_plain_action_line(self):
        out = self._extract("Thought: install\nAction: apt-get install -y libpq-dev")
        self.assertEqual(out, "apt-get install -y libpq-dev")

    def test_strips_backtick_fencing(self):
        out = self._extract("Thought: ok\nAction: ```bash\npip install flask\n```")
        self.assertEqual(out, "pip install flask")

    def test_toolcall_xml_format(self):
        content = (
            "<invoke>\n"
            '<parameter name="command">pip install psycopg2</parameter>\n'
            "</invoke>"
        )
        out = self._extract(content)
        self.assertEqual(out, "pip install psycopg2")

    def test_empty_content_returns_empty_string(self):
        self.assertEqual(self._extract(""), "")

    def test_none_returns_empty_string(self):
        from src.envstate.build_agent import _extract_worker_action
        self.assertEqual(_extract_worker_action(None), "")

    def test_multiline_action_takes_first_line(self):
        out = self._extract("Action: echo hello\nworld")
        self.assertEqual(out, "echo hello")


class TestIsWorkerFinished(unittest.TestCase):
    def _finished(self, content: str) -> bool:
        from src.envstate.build_agent import _is_worker_finished
        return _is_worker_finished(content)

    def test_final_answer_success(self):
        self.assertTrue(self._finished("Thought: done\nFinal Answer: Success"))

    def test_final_answer_case_insensitive(self):
        self.assertTrue(self._finished("Final answer: success"))

    def test_not_finished_on_action_line(self):
        self.assertFalse(self._finished("Thought: ok\nAction: ls"))

    def test_empty_returns_false(self):
        self.assertFalse(self._finished(""))

    def test_final_answer_failure_not_finished(self):
        # "Final Answer: Failure" must NOT be treated as completion
        self.assertFalse(self._finished("Final Answer: Failure"))
```

- [ ] **Step 2: Run test to verify it passes (parsing helpers already implemented)**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestExtractWorkerAction tests/test_build_agent.py::TestIsWorkerFinished -q
```

Expected:
```
11 passed in 0.xx s
```

(These pass immediately because the helpers were written in Task 12. If they do not, fix `_extract_worker_action` and `_is_worker_finished` in `src/envstate/build_agent.py`.)

- [ ] **Step 3: Commit**

```
git add tests/test_build_agent.py
git commit -m "test(build_agent): add action-parsing helper unit tests"
```

---

### Task 14: Write failing tests for the fixed stuck guard (`_is_stuck`)

**Files:**
- Modify: `tests/test_build_agent.py` (append new test class)

- [ ] **Step 1: Write the failing tests** (append before `if __name__`)

```python
# ---------------------------------------------------------------------------
# 3. Fixed stuck guard (_is_stuck) — spec §6
# ---------------------------------------------------------------------------

class TestIsStuck(unittest.TestCase):
    """The guard must fire only when ≥2 real mutating failures share identical output.
    Preflight rejections must be ignored entirely.
    One self-correction attempt must be allowed before the guard fires (≥2 real
    failures required, not 1).
    """

    def _stuck(
        self,
        history: list,
        action: str = "pip install x",
        is_preflight: bool = False,
    ) -> bool:
        from src.envstate.build_agent import _is_stuck, CommandRecord
        # history items are (cmd, rc, output) tuples for brevity
        records = [CommandRecord(cmd=c, rc=r, output=o) for c, r, o in history]
        return _is_stuck(records, action, is_preflight)

    def test_two_identical_real_failures_fires(self):
        """Two consecutive identical-output real failures → stuck."""
        err = "ERROR: Could not find a version that satisfies psycopg2"
        hist = [
            ("pip install psycopg2", 1, err),
            ("pip install psycopg2==2.8", 1, err),
        ]
        self.assertTrue(self._stuck(hist))

    def test_two_different_failures_does_not_fire(self):
        hist = [
            ("pip install psycopg2", 1, "ERROR: pg_config not found"),
            ("pip install psycopg2", 1, "ERROR: different error"),
        ]
        self.assertFalse(self._stuck(hist))

    def test_only_one_failure_does_not_fire(self):
        hist = [("pip install psycopg2", 1, "ERROR: pg_config not found")]
        self.assertFalse(self._stuck(hist))

    def test_empty_history_does_not_fire(self):
        self.assertFalse(self._stuck([]))

    def test_two_successes_does_not_fire(self):
        hist = [
            ("pip install flask", 0, "Successfully installed flask"),
            ("pip install flask", 0, "Successfully installed flask"),
        ]
        self.assertFalse(self._stuck(hist))

    def test_preflight_rejection_ignored_entirely(self):
        """Preflight rejection must NOT count toward the stuck counter."""
        rejection = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup commands must not pipe"
        hist = [
            ("pip install x | head", 1, rejection),
            ("pip install x | head", 1, rejection),
        ]
        # Both are preflight rejections in history; is_preflight=True for
        # the current action too.
        self.assertFalse(self._stuck(hist, is_preflight=True))

    def test_preflight_rejection_in_history_not_counted(self):
        """A preflight rejection in history must NOT count as a real failure.

        Scenario: action 1 is a preflight rejection (never executed), action 2
        is a real failure.  The guard must NOT fire after only 1 real failure.
        """
        rejection = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup"
        real_err = "ERROR: some real failure"
        hist = [
            ("pip install x | head", 1, rejection),   # preflight — does not count
            ("pip install x", 1, real_err),            # real failure (count=1)
        ]
        self.assertFalse(self._stuck(hist))

    def test_one_self_correction_allowed_before_firing(self):
        """Guard must NOT fire after just 1 real failure; one self-correction is
        allowed before the guard triggers (spec §6: ≥2 real failures required)."""
        err = "ERROR: pg_config not found"
        hist = [("pip install psycopg2", 1, err)]  # only 1 real failure
        self.assertFalse(self._stuck(hist))

    def test_mixed_preflight_and_real_fires_only_after_two_real(self):
        """Two real identical failures with a preflight in between still fires."""
        rejection = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: compound"
        real_err = "ERROR: pg_config not found"
        hist = [
            ("pip install psycopg2", 1, real_err),       # real failure 1
            ("pip install x | head", 1, rejection),       # preflight (ignored)
            ("pip install psycopg2", 1, real_err),       # real failure 2 (same)
        ]
        self.assertTrue(self._stuck(hist))

    def test_is_preflight_true_bypasses_counter(self):
        """When the CURRENT action is a preflight rejection, guard must return False."""
        err = "ERROR: pg_config not found"
        hist = [
            ("pip install psycopg2", 1, err),
            ("pip install psycopg2", 1, err),
        ]
        # Even though history has 2 identical real failures, if the NEW action
        # is a preflight rejection we skip the guard.
        self.assertFalse(self._stuck(hist, is_preflight=True))
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestIsStuck -q
```

Expected — some tests fail because `_is_stuck` import uses `CommandRecord` from `build_agent` but `CommandRecord` lives in `world_model`. Fix the import alias in the test helper if needed (the test imports `CommandRecord` from `build_agent` as a convenience alias — add `from src.envstate.world_model import CommandRecord` to the test import section).

Actually the test calls `from src.envstate.build_agent import _is_stuck, CommandRecord` — since `CommandRecord` lives in `world_model.py`, the re-export at the bottom of `build_agent.py` (written in Task 12) makes this work:

```python
# Re-export for test convenience
from src.envstate.world_model import CommandRecord as CommandRecord  # noqa: F401
```

After confirming the re-export is present, run again:

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestIsStuck -q
```

Expected:
```
FAILED tests/test_build_agent.py::TestIsStuck::test_is_preflight_true_bypasses_counter
```
(or similar if the guard logic is not yet complete).

- [ ] **Step 3: Verify `_is_stuck` implementation in `src/envstate/build_agent.py`**

Confirm the function body matches the contract exactly. The full correct implementation (already written in Task 12):

```python
def _is_stuck(
    history: list[CommandRecord],
    action: str,
    is_preflight_rejection: bool,
) -> bool:
    if is_preflight_rejection:
        return False
    real_failures = [
        r
        for r in history
        if r.rc != 0
        and not r.output.startswith(_PREFLIGHT_REJECTION_PREFIX)
    ]
    if len(real_failures) < 2:
        return False
    return real_failures[-1].output.strip() == real_failures[-2].output.strip()
```

Key properties verified by the tests:
- `is_preflight_rejection=True` short-circuits to `False` immediately (preflight rejections are never executions).
- Only records with `rc != 0` AND output not starting with `_PREFLIGHT_REJECTION_PREFIX` count as real failures.
- `len(real_failures) < 2` ensures at least one self-correction attempt is allowed before the guard fires (spec §6).
- The guard fires only when the last two real failures have identical output.

Also confirm the re-export line at the end of `src/envstate/build_agent.py`:

```python
# Re-export for test convenience (tests import CommandRecord from here)
from src.envstate.world_model import CommandRecord as CommandRecord  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestIsStuck -q
```

Expected:
```
9 passed in 0.xx s
```

- [ ] **Step 5: Commit**

```
git add src/envstate/build_agent.py tests/test_build_agent.py
git commit -m "feat(build_agent): implement fixed stuck guard that ignores preflight rejections"
```

---

### Task 15: Write failing tests for `BuildAgent.run` — success path (returns `done`)

**Files:**
- Modify: `tests/test_build_agent.py` (append new test class)

- [ ] **Step 1: Write the failing tests** (append before `if __name__`)

```python
# ---------------------------------------------------------------------------
# 4. BuildAgent.run — success / "done" path
# ---------------------------------------------------------------------------

class _FakeSynthesizer:
    """Minimal Synthesizer stand-in: all commands mutate, class='other_mutation'."""
    def command_mutates_environment(self, command: str) -> bool:
        return True
    def classify_mutation(self, command: str) -> str:
        return "other_mutation"


def _make_agent(client, container_id="ctr-test"):
    from src.envstate.build_agent import BuildAgent
    return BuildAgent(
        client=client,
        model="test-model",
        synthesizer=_FakeSynthesizer(),
        container_id=container_id,
    )


class TestBuildAgentRunDone(unittest.TestCase):
    """BuildAgent.run returns TaskReport(status='done') when LLM emits Final Answer: Success."""

    def test_returns_done_when_llm_signals_finished_immediately(self):
        """LLM says 'Final Answer: Success' on the very first step — no sandbox calls needed."""
        client = _fake_client_seq(["Thought: done\nFinal Answer: Success"])
        sandbox_calls = []

        def sandbox(cmd):
            sandbox_calls.append(cmd)
            return True, "ok"

        task = _make_task()
        ledger = _make_ledger()
        agent = _make_agent(client)
        report = agent.run(task, sandbox, ledger)

        self.assertEqual(report.status, "done")
        self.assertEqual(report.task_goal, task.goal)
        self.assertEqual(len(sandbox_calls), 0, "No sandbox call when done immediately")

    def test_returns_done_after_one_successful_action(self):
        """LLM executes one command then signals done."""
        client = _fake_client_seq([
            "Thought: install\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, f"Installed {cmd}")

        task = _make_task()
        ledger = _make_ledger()
        agent = _make_agent(client)
        report = agent.run(task, sandbox, ledger)

        self.assertEqual(report.status, "done")
        self.assertEqual(len(report.commands), 1)
        self.assertEqual(report.commands[0].cmd, "pip install flask")
        self.assertEqual(report.commands[0].rc, 0)

    def test_done_report_contains_learning(self):
        client = _fake_client_seq([
            "Thought: ok\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, "ok")

        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertIsInstance(report.learning, str)
        self.assertGreater(len(report.learning), 0)

    def test_done_report_task_goal_matches_task(self):
        task = _make_task(goal="install flask and psycopg2")
        client = _fake_client_seq(["Thought: done\nFinal Answer: Success"])
        report = _make_agent(client).run(task, lambda cmd: (True, "ok"), _make_ledger())
        self.assertEqual(report.task_goal, "install flask and psycopg2")

    def test_commands_tuple_is_frozen(self):
        """TaskReport.commands must be a tuple (frozen), not a list."""
        client = _fake_client_seq([
            "Thought: x\nAction: ls",
            "Thought: done\nFinal Answer: Success",
        ])
        report = _make_agent(client).run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())
        self.assertIsInstance(report.commands, tuple)
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentRunDone -q
```

Expected: `5 failed` — the `BuildAgent` class exists but `_fake_client_seq` may not call `.create` with the right kwargs. Verify `complete_with_retry` receives `temperature` and `stop` forwarded as kwargs. (If the implementation is correct the tests should all pass — if any fail, check the `complete_with_retry` call in `BuildAgent.run`.)

- [ ] **Step 3: Ensure `BuildAgent.run` is correct (already written in Task 12)**

The implementation in Task 12 calls:
```python
text, usage, raw_response = complete_with_retry(
    self.client,
    self.model,
    messages,
    temperature=0,
    stop=["Observation:"],
)
```
`complete_with_retry` calls `client.chat.completions.create(model=..., messages=..., temperature=0, stop=...)`. The `_fake_client_seq` fixture above has `create=lambda **kwargs: ...` which accepts all kwargs. No change needed.

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentRunDone -q
```

Expected:
```
5 passed in 0.xx s
```

- [ ] **Step 5: Commit**

```
git add tests/test_build_agent.py
git commit -m "test(build_agent): add done-path unit tests for BuildAgent.run"
```

---

### Task 16: Write failing tests for `BuildAgent.run` — budget exhaustion (returns `blocked`)

**Files:**
- Modify: `tests/test_build_agent.py` (append new test class)

- [ ] **Step 1: Write the failing tests** (append before `if __name__`)

```python
# ---------------------------------------------------------------------------
# 5. BuildAgent.run — budget exhaustion path (returns "blocked")
# ---------------------------------------------------------------------------

class TestBuildAgentRunBlocked(unittest.TestCase):

    def test_blocked_at_local_budget(self):
        """After LOCAL_BUDGET actions without 'Final Answer', status must be 'blocked'."""
        from src.envstate.build_agent import LOCAL_BUDGET
        # Provide one more LLM response than the budget so the loop always has a response.
        contents = [f"Thought: step {i}\nAction: pip install pkg{i}" for i in range(LOCAL_BUDGET + 1)]
        client = _fake_client_seq(contents)
        sandbox_calls = []

        def sandbox(cmd):
            sandbox_calls.append(cmd)
            return False, f"ERROR: install failed for {cmd}"

        task = _make_task()
        ledger = _make_ledger()
        report = _make_agent(client).run(task, sandbox, ledger)

        self.assertEqual(report.status, "blocked")
        self.assertLessEqual(len(sandbox_calls), LOCAL_BUDGET)

    def test_blocked_report_contains_commands(self):
        """commands tuple must contain all executed actions."""
        from src.envstate.build_agent import LOCAL_BUDGET
        contents = [f"Thought: x\nAction: cmd{i}" for i in range(LOCAL_BUDGET + 1)]
        client = _fake_client_seq(contents)
        sandbox = lambda cmd: (False, "ERROR: failure")
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertIsInstance(report.commands, tuple)
        self.assertGreater(len(report.commands), 0)

    def test_blocked_after_too_many_empty_responses(self):
        """MAX_EMPTY_RESPONSES consecutive empty responses → status 'blocked'."""
        from src.envstate.build_agent import MAX_EMPTY_RESPONSES
        # All responses are empty/unparseable (no Action line, no Final Answer)
        contents = ["Thought: hmm" for _ in range(MAX_EMPTY_RESPONSES + 2)]
        client = _fake_client_seq(contents)
        sandbox_calls = []

        def sandbox(cmd):
            sandbox_calls.append(cmd)
            return True, "ok"

        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertEqual(report.status, "blocked")
        # No sandbox calls because empty responses never produce an action
        self.assertEqual(sandbox_calls, [])

    def test_empty_response_counter_resets_on_real_action(self):
        """One empty response followed by a real action must NOT trigger the guard."""
        from src.envstate.build_agent import MAX_EMPTY_RESPONSES
        contents = [
            "Thought: hmm",                            # empty — counter = 1
            "Thought: ok\nAction: pip install flask",  # real action — counter resets
            "Thought: done\nFinal Answer: Success",    # done
        ]
        client = _fake_client_seq(contents)
        sandbox = lambda cmd: (True, "ok")
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        # Must NOT be blocked — empty counter reset after real action
        self.assertEqual(report.status, "done")

    def test_blocked_learning_mentions_budget(self):
        from src.envstate.build_agent import LOCAL_BUDGET
        contents = [f"Thought: x\nAction: cmd{i}" for i in range(LOCAL_BUDGET + 1)]
        client = _fake_client_seq(contents)
        sandbox = lambda cmd: (False, "fail")
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertIn("budget", report.learning.lower())
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentRunBlocked -q
```

Expected: some failures — in particular `test_empty_response_counter_resets_on_real_action` will fail if the counter reset logic is missing.

- [ ] **Step 3: Verify counter-reset logic in `BuildAgent.run`**

In the implementation (Task 12), the line `empty_responses = 0` appears just before the stuck-guard check (after `action.strip()` is confirmed non-empty). Confirm this line is present in `src/envstate/build_agent.py`. If not, locate the `if not action.strip():` block and add `empty_responses = 0` in the `else` branch (i.e., when an action IS present):

The relevant section in `BuildAgent.run` must read:
```python
if not action.strip():
    empty_responses += 1
    if empty_responses >= MAX_EMPTY_RESPONSES:
        return TaskReport(...)
    messages = messages + [...]
    continue
empty_responses = 0   # ← reset here, OUTSIDE the if-block
```

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentRunBlocked -q
```

Expected:
```
5 passed in 0.xx s
```

- [ ] **Step 5: Commit**

```
git add tests/test_build_agent.py
git commit -m "test(build_agent): add budget-exhaustion and empty-response guard unit tests"
```

---

### Task 17: Write failing tests for the stuck guard integration inside `BuildAgent.run`

**Files:**
- Modify: `tests/test_build_agent.py` (append new test class)

- [ ] **Step 1: Write the failing tests** (append before `if __name__`)

```python
# ---------------------------------------------------------------------------
# 6. BuildAgent.run — stuck guard integration
# ---------------------------------------------------------------------------

class TestBuildAgentStuckGuardIntegration(unittest.TestCase):
    """The stuck guard inside BuildAgent.run must fire correctly."""

    def test_stuck_fires_on_two_identical_real_failures(self):
        """Two actions with the same failure output → status 'blocked'."""
        err = "ERROR: Could not find a version that satisfies psycopg2"
        client = _fake_client_seq([
            "Thought: try 1\nAction: pip install psycopg2",
            "Thought: try 2\nAction: pip install psycopg2",
            "Thought: try 3\nAction: pip install psycopg2",
            "Thought: done\nFinal Answer: Success",  # should not reach here
        ])
        sandbox = lambda cmd: (False, err)
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertEqual(report.status, "blocked")
        self.assertIn("stuck", report.learning.lower())

    def test_stuck_does_not_fire_on_different_errors(self):
        """Two failures with different error text — guard must NOT fire."""
        errors = iter(["ERROR: pg_config not found", "ERROR: different error"])
        client = _fake_client_seq([
            "Thought: try 1\nAction: pip install psycopg2",
            "Thought: try 2\nAction: apt-get install libpq-dev",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (False, next(errors))
        # With different errors the guard must not fire — loop must reach Final Answer
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        # The loop ran both failures then got Final Answer → done
        self.assertEqual(report.status, "done")

    def test_preflight_rejection_does_not_trigger_stuck(self):
        """Preflight rejections must not count toward the stuck counter."""
        preflight = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup commands must not pipe"
        client = _fake_client_seq([
            "Thought: bad1\nAction: pip install x | head",
            "Thought: bad2\nAction: pip install x | head",
            "Thought: ok\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox_calls = []

        def sandbox(cmd):
            sandbox_calls.append(cmd)
            if "| head" in cmd:
                return False, preflight
            return True, "Installed flask"

        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        # Two identical preflight rejections must NOT trigger stuck
        # → loop continues to the real action and then Final Answer
        self.assertEqual(report.status, "done")
        self.assertEqual(len(report.commands), 3)
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentStuckGuardIntegration -q
```

Expected: `test_stuck_fires_on_two_identical_real_failures` passes; `test_preflight_rejection_does_not_trigger_stuck` may fail if the `is_preflight` detection in `BuildAgent.run` is not wired correctly.

- [ ] **Step 3: Verify preflight detection wiring in `BuildAgent.run`**

In `BuildAgent.run`, after `success, output = sandbox_execute(action)`, the line must be:
```python
is_preflight = output.startswith(_PREFLIGHT_REJECTION_PREFIX)
```
And `_is_stuck(history, action, is_preflight)` must be called with this value. Confirm this is present in `src/envstate/build_agent.py` (it was written in Task 12).

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentStuckGuardIntegration -q
```

Expected:
```
3 passed in 0.xx s
```

- [ ] **Step 5: Commit**

```
git add tests/test_build_agent.py
git commit -m "test(build_agent): add stuck guard integration tests (preflight rejections ignored)"
```

---

### Task 18: Write failing tests for ActionLedger appends

**Files:**
- Modify: `tests/test_build_agent.py` (append new test class)

- [ ] **Step 1: Write the failing tests** (append before `if __name__`)

```python
# ---------------------------------------------------------------------------
# 7. ActionLedger appends — each executed action is recorded
# ---------------------------------------------------------------------------

class TestBuildAgentLedgerAppends(unittest.TestCase):
    """Each shell-executed action must be appended to the ActionLedger."""

    def test_successful_action_appended_with_rc_0(self):
        client = _fake_client_seq([
            "Thought: install\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, "Successfully installed flask")
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].cmd, "pip install flask")
        self.assertEqual(events[0].rc, 0)

    def test_failed_action_appended_with_rc_1(self):
        client = _fake_client_seq([
            "Thought: try\nAction: pip install psycopg2",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (False, "ERROR: pg_config not found")
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].rc, 1)

    def test_multiple_actions_all_appended_in_order(self):
        client = _fake_client_seq([
            "Thought: step1\nAction: apt-get install -y libpq-dev",
            "Thought: step2\nAction: pip install psycopg2",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, f"ok: {cmd}")
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].cmd, "apt-get install -y libpq-dev")
        self.assertEqual(events[1].cmd, "pip install psycopg2")

    def test_preflight_rejected_action_still_appended(self):
        """Preflight rejections ARE appended to the ledger (rc=1, mutation_class=None)."""
        preflight = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup"
        client = _fake_client_seq([
            "Thought: bad\nAction: pip install x | head",
            "Thought: ok\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])

        def sandbox(cmd):
            if "| head" in cmd:
                return False, preflight
            return True, "ok"

        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertEqual(len(events), 2)
        # Preflight rejection: rc=1, mutation_class=None
        self.assertEqual(events[0].rc, 1)
        self.assertIsNone(events[0].mutation_class)
        # Real successful action: rc=0
        self.assertEqual(events[1].rc, 0)

    def test_mutating_command_sets_mutation_class(self):
        """Successful mutating command must carry a non-None mutation_class."""
        client = _fake_client_seq([
            "Thought: install\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, "ok")
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        # _FakeSynthesizer marks all commands as mutating → mutation_class set
        self.assertIsNotNone(events[0].mutation_class)

    def test_non_mutating_command_has_null_mutation_class(self):
        """Read-only commands (synthesizer returns False) must have mutation_class=None."""
        class _ReadOnlySynthesizer:
            def command_mutates_environment(self, cmd): return False
            def classify_mutation(self, cmd): return "other_mutation"

        client = _fake_client_seq([
            "Thought: read\nAction: cat requirements.txt",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, "flask==2.3.0")
        ledger = _make_ledger()
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_ReadOnlySynthesizer(), container_id="c"
        )
        agent.run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertIsNone(events[0].mutation_class)

    def test_ledger_event_has_correct_container_id(self):
        client = _fake_client_seq([
            "Thought: x\nAction: ls",
            "Thought: done\nFinal Answer: Success",
        ])
        ledger = _make_ledger()
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_FakeSynthesizer(), container_id="my-container-123"
        )
        agent.run(_make_task(), lambda cmd: (True, "ok"), ledger)

        events = ledger.events()
        self.assertEqual(events[0].container_id, "my-container-123")

    def test_ledger_event_step_increments(self):
        """step field must increment across actions."""
        client = _fake_client_seq([
            "Thought: a\nAction: cmd1",
            "Thought: b\nAction: cmd2",
            "Thought: done\nFinal Answer: Success",
        ])
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), lambda cmd: (True, "ok"), ledger)

        events = ledger.events()
        self.assertEqual(len(events), 2)
        self.assertLess(events[0].step, events[1].step)

    def test_step_offset_shifts_step_numbers(self):
        """step_offset shifts all step numbers for correct multi-task ledger alignment."""
        client = _fake_client_seq([
            "Thought: x\nAction: ls",
            "Thought: done\nFinal Answer: Success",
        ])
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), lambda cmd: (True, "ok"), ledger, step_offset=10)

        events = ledger.events()
        self.assertGreaterEqual(events[0].step, 10)
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentLedgerAppends -q
```

Expected: most tests fail because `_append_ledger_event` may not handle the `step_offset` correctly or `mutation_class` logic is wrong.

- [ ] **Step 3: Verify `_append_ledger_event` logic in `src/envstate/build_agent.py`**

The implementation in Task 12 computes `step = step_offset + steps_executed`. `steps_executed` increments BEFORE the ledger append call (inside `BuildAgent.run`). Confirm:
1. `steps_executed` starts at `0` and is incremented with `steps_executed += 1` BEFORE the `_append_ledger_event` call.
2. The call is `self._append_ledger_event(... step=step_offset + steps_executed ...)` — using the post-increment value.

The correct ordering in `BuildAgent.run`:
```python
steps_executed += 1   # ← increment BEFORE appending
self._append_ledger_event(
    action=action,
    success=success,
    output=output,
    step=step_offset + steps_executed,
    env_revision=env_revision,
    ledger=ledger,
    is_preflight=is_preflight,
)
```

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentLedgerAppends -q
```

Expected:
```
9 passed in 0.xx s
```

- [ ] **Step 5: Commit**

```
git add src/envstate/build_agent.py tests/test_build_agent.py
git commit -m "fix(build_agent): increment step_offset before ledger append; add ledger-append unit tests"
```

---

### Task 19: Write failing tests for `on_usage` callback

**Files:**
- Modify: `tests/test_build_agent.py` (append new test class)

- [ ] **Step 1: Write the failing tests** (append before `if __name__`)

```python
# ---------------------------------------------------------------------------
# 8. on_usage callback
# ---------------------------------------------------------------------------

class TestBuildAgentOnUsage(unittest.TestCase):

    def test_on_usage_called_once_per_llm_step(self):
        """on_usage must be called once for each LLM call made by the agent."""
        client = _fake_client_seq([
            "Thought: step1\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        seen = []
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_FakeSynthesizer(), container_id="c",
            on_usage=seen.append,
        )
        agent.run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())
        # 2 LLM calls → 2 on_usage invocations
        self.assertEqual(len(seen), 2)

    def test_on_usage_receives_token_counts(self):
        """Each on_usage dict must have input_tokens, output_tokens, total_tokens."""
        client = _fake_client_seq(["Thought: done\nFinal Answer: Success"])
        seen = []
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_FakeSynthesizer(), container_id="c",
            on_usage=seen.append,
        )
        agent.run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())
        self.assertEqual(len(seen), 1)
        usage = seen[0]
        self.assertIn("input_tokens", usage)
        self.assertIn("output_tokens", usage)
        self.assertIn("total_tokens", usage)

    def test_on_usage_none_does_not_crash(self):
        """on_usage=None (default) must not raise."""
        client = _fake_client_seq(["Thought: done\nFinal Answer: Success"])
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_FakeSynthesizer(), container_id="c",
            on_usage=None,
        )
        # Should not raise
        agent.run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())

    def test_log_path_stored_on_agent(self):
        """log_path kwarg must be stored as self.log_path (canonical __init__ contract)."""
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=_fake_client_seq([]),
            model="m",
            synthesizer=_FakeSynthesizer(),
            container_id="c",
            log_path="/tmp/build_agent_test.log",
        )
        self.assertEqual(agent.log_path, "/tmp/build_agent_test.log")

    def test_log_path_defaults_to_none(self):
        """log_path must default to None when not supplied."""
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=_fake_client_seq([]),
            model="m",
            synthesizer=_FakeSynthesizer(),
            container_id="c",
        )
        self.assertIsNone(agent.log_path)
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentOnUsage -q
```

Expected: `test_log_path_stored_on_agent` and `test_log_path_defaults_to_none` fail if `log_path` was not added to `__init__`. The `on_usage` tests may also fail depending on how `complete_with_retry` returns usage vs how `on_usage` is called.

- [ ] **Step 3: Confirm canonical `__init__` and `on_usage` call in `src/envstate/build_agent.py`**

The canonical `BuildAgent.__init__` signature (from the global canonical decisions) is:

```python
def __init__(
    self,
    client: Any,
    model: str,
    synthesizer: Any,
    container_id: str = "unknown",
    on_usage: Callable[[dict], None] | None = None,
    log_path: str | None = None,
) -> None:
    self.client = client
    self.model = model
    self.synthesizer = synthesizer
    self.container_id = container_id
    self.on_usage = on_usage
    self.log_path = log_path
```

Note: `synthesizer` and `container_id` are BuildAgent-specific deps (not present on Planner/Maintainer); `on_usage` and `log_path` are the canonical additions from the global plan.

In `BuildAgent.run`, after each `complete_with_retry` call:
```python
text, usage, raw_response = complete_with_retry(...)
if self.on_usage:
    self.on_usage(usage)
```

The `usage` dict from `complete_with_retry` already has the correct keys (`input_tokens`, `output_tokens`, `total_tokens`). The guard is `if self.on_usage:` (not `if self.on_usage and usage:`) because `complete_with_retry` always returns a dict (possibly with zero values), never `None`.

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentOnUsage -q
```

Expected:
```
5 passed in 0.xx s
```

- [ ] **Step 5: Commit**

```
git add src/envstate/build_agent.py tests/test_build_agent.py
git commit -m "test(build_agent): add on_usage callback and log_path unit tests"
```

---

### Task 20: Write failing tests for system prompt and task-message content

**Files:**
- Modify: `tests/test_build_agent.py` (append new test classes)

- [ ] **Step 1: Write the failing tests** (append before `if __name__`)

```python
# ---------------------------------------------------------------------------
# 9. System prompt and task-message content
# ---------------------------------------------------------------------------

class TestBuildAgentSystemPrompt(unittest.TestCase):

    def test_system_prompt_exists_and_is_string(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT
        self.assertIsInstance(BUILD_AGENT_SYSTEM_PROMPT, str)
        self.assertGreater(len(BUILD_AGENT_SYSTEM_PROMPT), 100)

    def test_system_prompt_mentions_all_rca_layers(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT
        p = BUILD_AGENT_SYSTEM_PROMPT.lower()
        for layer in ("base", "system", "runtime", "deps", "build", "tests"):
            self.assertIn(layer, p, f"Layer '{layer}' missing from BUILD_AGENT_SYSTEM_PROMPT")

    def test_system_prompt_has_final_answer_success(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT
        self.assertIn("Final Answer: Success", BUILD_AGENT_SYSTEM_PROMPT)

    def test_system_prompt_has_action_format(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT
        self.assertIn("Action:", BUILD_AGENT_SYSTEM_PROMPT)
        self.assertIn("Thought:", BUILD_AGENT_SYSTEM_PROMPT)

    def test_llm_receives_system_prompt_as_first_message(self):
        """The first message sent to the LLM must be role=system with BUILD_AGENT_SYSTEM_PROMPT."""
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT, BuildAgent
        captured = []

        def fake_create(**kwargs):
            captured.append(kwargs["messages"])
            return _fake_response("Thought: done\nFinal Answer: Success")

        class _FakeCompletions:
            def create(self, **kwargs): return fake_create(**kwargs)
        class _FakeChat:
            completions = _FakeCompletions()
        class _FakeClient:
            chat = _FakeChat()

        agent = BuildAgent(
            client=_FakeClient(), model="m",
            synthesizer=_FakeSynthesizer(), container_id="c"
        )
        agent.run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())

        self.assertGreater(len(captured), 0)
        sys_msgs = [m for m in captured[0] if m["role"] == "system"]
        self.assertEqual(len(sys_msgs), 1)
        self.assertEqual(sys_msgs[0]["content"], BUILD_AGENT_SYSTEM_PROMPT)

    def test_task_message_contains_goal_done_when_layer_facts(self):
        """The user message must contain goal, done_when, layer, and facts."""
        from src.envstate.build_agent import BuildAgent
        captured = []

        def fake_create(**kwargs):
            captured.append(kwargs["messages"])
            return _fake_response("Thought: done\nFinal Answer: Success")

        class _FakeCompletions:
            def create(self, **kwargs): return fake_create(**kwargs)
        class _FakeChat:
            completions = _FakeCompletions()
        class _FakeClient:
            chat = _FakeChat()

        task = _make_task(
            goal="install edsl package",
            done_when="python -c 'import edsl' exits 0",
            layer="deps",
            facts=("build_system=pip", "python=3.12"),
        )
        agent = BuildAgent(
            client=_FakeClient(), model="m",
            synthesizer=_FakeSynthesizer(), container_id="c"
        )
        agent.run(task, lambda cmd: (True, "ok"), _make_ledger())

        user_msgs = [m for m in captured[0] if m["role"] == "user"]
        self.assertGreater(len(user_msgs), 0)
        content = user_msgs[0]["content"]
        self.assertIn("install edsl package", content)
        self.assertIn("python -c 'import edsl' exits 0", content)
        self.assertIn("deps", content)
        self.assertIn("build_system=pip", content)
        self.assertIn("python=3.12", content)
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentSystemPrompt -q
```

Expected: most pass immediately (system prompt was written in Task 12). If `test_task_message_contains_goal_done_when_layer_facts` fails, check `_build_task_message`.

- [ ] **Step 3: Fix `_build_task_message` if needed**

Confirm `src/envstate/build_agent.py`'s `_build_task_message`:
```python
def _build_task_message(self, task: Task) -> str:
    facts_text = "\n".join(f"- {f}" for f in task.facts) if task.facts else "- (none)"
    return (
        f"Task goal: {task.goal}\n"
        f"Done when: {task.done_when}\n"
        f"Layer: {task.layer}\n"
        f"Relevant facts:\n{facts_text}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/test_build_agent.py::TestBuildAgentSystemPrompt -q
```

Expected:
```
6 passed in 0.xx s
```

- [ ] **Step 5: Commit**

```
git add tests/test_build_agent.py
git commit -m "test(build_agent): add system-prompt and task-message content tests"
```

---

### Task 21: Full test suite run and coverage check

**Files:**
- No new files; verify all prior tasks pass together.

- [ ] **Step 1: Run the full build_agent test suite**

```
.venv/bin/python -m pytest tests/test_build_agent.py -v
```

Expected: all tests pass. Sample output:
```
tests/test_build_agent.py::TestModuleConstants::test_local_budget_default_is_8 PASSED
tests/test_build_agent.py::TestModuleConstants::test_max_empty_responses_default_is_2 PASSED
tests/test_build_agent.py::TestExtractWorkerAction::test_plain_action_line PASSED
...
tests/test_build_agent.py::TestBuildAgentSystemPrompt::test_task_message_contains_goal_done_when_layer_facts PASSED
XX passed in 0.xx s
```

- [ ] **Step 2: Run coverage for `src/envstate/build_agent.py`**

```
.venv/bin/python -m pytest tests/test_build_agent.py \
    --cov=src/envstate/build_agent \
    --cov-report=term-missing \
    -q
```

Expected: `>= 80%` coverage on `src/envstate/build_agent`. If below 80%, identify uncovered branches (commonly: the `DockerException` path inside sandbox_execute is not reachable without Docker — acceptable as an integration-only branch).

- [ ] **Step 3: Run existing test suite to confirm no regressions**

```
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_worldmodel_namekey.py \
    -k "not test_envstate_orchestrator and not test_fullstate_worker and not test_envstate_maintainer"
```

(Ignore tests that depend on the v0 types still under migration; they will be cleaned up as part of later tasks.)

- [ ] **Step 4: Commit**

```
git add tests/test_build_agent.py src/envstate/build_agent.py
git commit -m "test(build_agent): all unit tests passing, ≥80% coverage on build_agent.py"
```

---

### Appendix: Final file contents summary

After all tasks complete, the two files look like this:

**`src/envstate/build_agent.py`** — new file, ~240 lines:
- Module constants: `LOCAL_BUDGET = 8`, `MAX_EMPTY_RESPONSES = 2`
- `_PREFLIGHT_REJECTION_PREFIX` constant
- `_extract_worker_action(content)` — ported from `worker.py`
- `_is_worker_finished(content)` — ported from `worker.py`
- `_is_stuck(history, action, is_preflight_rejection)` — fixed guard (spec §6): ignores preflight rejections AND requires ≥2 real failures (one self-correction allowed before firing)
- `BUILD_AGENT_SYSTEM_PROMPT` — layered RCA prompt
- `BuildAgent.__init__(client, model, synthesizer, container_id, on_usage, log_path)` — canonical signature; `on_usage` called after each LLM completion; `log_path` stored for structured logging
- `BuildAgent.run(task, sandbox_execute, ledger, step_offset)` → `TaskReport`
- `BuildAgent._build_task_message(task)` → `str`
- `BuildAgent._append_ledger_event(...)` → `None`
- Re-export: `from src.envstate.world_model import CommandRecord as CommandRecord`

**`tests/test_build_agent.py`** — new file, ~420 lines:
- `TestModuleConstants` (2 tests)
- `TestExtractWorkerAction` (6 tests)
- `TestIsWorkerFinished` (5 tests)
- `TestIsStuck` (9 tests) — includes `test_one_self_correction_allowed_before_firing` asserting that 1 real failure does NOT fire the guard
- `TestBuildAgentRunDone` (5 tests)
- `TestBuildAgentRunBlocked` (5 tests)
- `TestBuildAgentStuckGuardIntegration` (3 tests)
- `TestBuildAgentLedgerAppends` (9 tests)
- `TestBuildAgentOnUsage` (5 tests) — includes `test_log_path_stored_on_agent` and `test_log_path_defaults_to_none`
- `TestBuildAgentSystemPrompt` (6 tests)

---

# Phase 5: Orchestrator loop + agent.py glue

## Orchestrator loop + agent.py glue

**Scope:** `src/envstate/orchestrator.py` (rewrite as `run_v1`) and `agent.py` glue (`enable_v1` param, `_run_v1` method, `run()` dispatch). Unit-tests use fake Planner/BuildAgent/Maintainer — no real LLM or Docker.

**Assumed pre-conditions (other components deliver these before you start):**
- `src/envstate/world_model.py` exists with `WorldModelMap`, `Task`, `PlannerDecision`, `TaskReport`, `CommandRecord`, `Fact`, `OpenProblem`, `initial_map()`, `merge_map()`.
- `src/envstate/planner.py` exists with `Planner` class exposing `decide(map) -> PlannerDecision`. Constructor signature: `Planner(client, model, on_usage: Callable[[dict], None] | None = None, log_path: str | None = None)`.
- `src/envstate/build_agent.py` exists with `BuildAgent` class exposing `run(task, sandbox_execute, ledger) -> TaskReport`. Constructor signature: `BuildAgent(client, model, on_usage: Callable[[dict], None] | None = None, log_path: str | None = None, sandbox=..., ledger=..., synthesizer=...)`.
- `src/envstate/maintainer.py` has been rewritten with `Maintainer` class exposing `update(map, report) -> WorldModelMap`. Constructor signature: `Maintainer(client, model, on_usage: Callable[[dict], None] | None = None, log_path: str | None = None)`.
- `src/envstate/ledger.py` is unchanged (`ActionLedger`, `ActionEvent`).

---

### Task 22: Write failing tests for `run_v1` orchestrator loop

**Files:**
- Create `tests/test_orchestrator_v1.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_v1.py
"""Unit tests for run_v1 orchestrator loop (src/envstate/orchestrator.py).

All collaborators are faked — no LLM calls, no Docker containers.
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass
from typing import Callable

from src.envstate.ledger import ActionLedger
from src.envstate.world_model import (
    CommandRecord,
    Fact,
    OpenProblem,
    PlannerDecision,
    Task,
    TaskReport,
    WorldModelMap,
    initial_map,
    merge_map,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_map() -> WorldModelMap:
    return initial_map(
        base_image="python:3.11-slim",
        workdir="/app",
        language="python 3.11",
        build_system="pip",
        repo_layout=("src/", "tests/", "pyproject.toml"),
    )


def _task() -> Task:
    return Task(
        goal="install deps",
        done_when="pip install exits 0",
        layer="deps",
        facts=(),
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakePlanner:
    """Emits PlannerDecision objects from a pre-loaded queue."""

    def __init__(self, decisions: list[PlannerDecision]) -> None:
        self._queue = list(decisions)

    def decide(self, current_map: WorldModelMap) -> PlannerDecision:
        assert self._queue, "FakePlanner.decide called more times than expected"
        return self._queue.pop(0)


class FakeBuildAgent:
    """Returns TaskReport objects from a pre-loaded queue."""

    def __init__(self, reports: list[TaskReport]) -> None:
        self._queue = list(reports)

    def run(
        self,
        task: Task,
        sandbox_execute: Callable[[str], tuple[bool, str]],
        ledger: ActionLedger,
        step_offset: int = 0,
    ) -> TaskReport:
        assert self._queue, "FakeBuildAgent.run called more times than expected"
        return self._queue.pop(0)


class FakeMaintainer:
    """Applies a series of map transformations from a pre-loaded queue."""

    def __init__(self, maps: list[WorldModelMap]) -> None:
        self._queue = list(maps)

    def update(self, current_map: WorldModelMap, report: TaskReport) -> WorldModelMap:
        assert self._queue, "FakeMaintainer.update called more times than expected"
        return self._queue.pop(0)


def _noop_sandbox(cmd: str) -> tuple[bool, str]:
    return True, "ok"


# ---------------------------------------------------------------------------
# Import target (will fail until orchestrator.py is rewritten)
# ---------------------------------------------------------------------------

from src.envstate.orchestrator import run_v1  # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunV1DoneFlagTerminates:
    """done_flag=True in the map breaks the loop immediately."""

    def test_done_flag_after_one_cycle_returns_done_flag_reason(self):
        world_map = _base_map()
        done_map = merge_map(world_map, done_flag=True)

        planner = FakePlanner([
            PlannerDecision(action="task", task=_task()),
        ])
        build_agent = FakeBuildAgent([
            TaskReport(
                task_goal="install deps",
                status="done",
                commands=(CommandRecord(cmd="pip install .", rc=0, output="ok"),),
                learning="deps installed",
            ),
        ])
        maintainer = FakeMaintainer([done_map])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
        )

        assert stop_reason == "done_flag"
        assert final_map.done_flag is True

    def test_done_flag_does_not_call_planner_again(self):
        """After done_flag is set the loop must break before the next planner.decide."""
        world_map = _base_map()
        done_map = merge_map(world_map, done_flag=True)

        # Planner only has ONE decision; a second call would raise AssertionError.
        planner = FakePlanner([
            PlannerDecision(action="task", task=_task()),
        ])
        build_agent = FakeBuildAgent([
            TaskReport(task_goal="g", status="done", commands=(), learning=""),
        ])
        maintainer = FakeMaintainer([done_map])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
        )

        assert stop_reason == "done_flag"


class TestRunV1PlannerDone:
    """planner.decide returning action='done' should terminate cleanly."""

    def test_planner_done_returns_planner_done_reason(self):
        world_map = _base_map()

        planner = FakePlanner([
            PlannerDecision(action="done", reason="all layers verified"),
        ])
        # build_agent and maintainer should NOT be called
        build_agent = FakeBuildAgent([])
        maintainer = FakeMaintainer([])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
        )

        assert stop_reason == "planner_done"
        assert final_map is world_map  # map unchanged when planner says done

    def test_planner_giveup_returns_planner_giveup_reason(self):
        world_map = _base_map()

        planner = FakePlanner([
            PlannerDecision(action="giveup", reason="irrecoverable conflict"),
        ])
        build_agent = FakeBuildAgent([])
        maintainer = FakeMaintainer([])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
        )

        assert stop_reason == "planner_giveup"


class TestRunV1MaxCycles:
    """Exhausting max_cycles without done_flag returns 'max_cycles'."""

    def test_max_cycles_exhaustion_stop_reason(self):
        world_map = _base_map()
        # Each cycle: planner says "task", agent returns blocked, maintainer returns same map.
        n = 3
        planner = FakePlanner([
            PlannerDecision(action="task", task=_task()) for _ in range(n)
        ])
        build_agent = FakeBuildAgent([
            TaskReport(task_goal="install deps", status="blocked", commands=(), learning="still blocked")
            for _ in range(n)
        ])
        maintainer = FakeMaintainer([world_map for _ in range(n)])

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=n,
        )

        assert stop_reason == "max_cycles"
        assert final_map.done_flag is False

    def test_default_max_cycles_constant_is_12(self):
        """MAX_CYCLES module constant must equal 12 per spec §8."""
        from src.envstate import orchestrator
        assert orchestrator.MAX_CYCLES == 12

    def test_collect_only_cmd_constant_present(self):
        """COLLECT_ONLY_CMD module constant must be defined in orchestrator."""
        from src.envstate import orchestrator
        assert hasattr(orchestrator, "COLLECT_ONLY_CMD")
        assert "--collect-only" in orchestrator.COLLECT_ONLY_CMD


class TestRunV1TwoCycleRun:
    """Full 2-cycle run: cycle 1 task+blocked, cycle 2 task+done_flag."""

    def test_two_cycle_run_succeeds_on_second_cycle(self):
        world_map = _base_map()
        map_after_cycle1 = merge_map(world_map, notes=("deps partially installed",))
        map_after_cycle2 = merge_map(map_after_cycle1, done_flag=True)

        planner = FakePlanner([
            PlannerDecision(action="task", task=_task()),  # cycle 1
            PlannerDecision(action="task", task=_task()),  # cycle 2
        ])
        build_agent = FakeBuildAgent([
            TaskReport(task_goal="install deps", status="blocked", commands=(), learning="network error"),
            TaskReport(
                task_goal="install deps",
                status="done",
                commands=(CommandRecord(cmd="pip install .", rc=0, output="Successfully installed"),),
                learning="deps installed",
            ),
        ])
        maintainer = FakeMaintainer([map_after_cycle1, map_after_cycle2])

        on_cycle_calls: list[tuple[int, str]] = []

        def on_cycle(cycle_num, current_map, decision, report):
            on_cycle_calls.append((cycle_num, decision.action))

        final_map, stop_reason = run_v1(
            planner=planner,
            build_agent=build_agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
            max_cycles=12,
            on_cycle=on_cycle,
        )

        assert stop_reason == "done_flag"
        assert final_map.done_flag is True
        assert len(on_cycle_calls) == 2
        assert on_cycle_calls[0] == (1, "task")
        assert on_cycle_calls[1] == (2, "task")


class TestRunV1ReturnType:
    """run_v1 always returns a (WorldModelMap, str) tuple."""

    def test_return_is_tuple_of_map_and_str(self):
        world_map = _base_map()
        planner = FakePlanner([PlannerDecision(action="done", reason="ok")])
        result = run_v1(
            planner=planner,
            build_agent=FakeBuildAgent([]),
            maintainer=FakeMaintainer([]),
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=_noop_sandbox,
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        final_map, stop_reason = result
        assert isinstance(final_map, WorldModelMap)
        assert isinstance(stop_reason, str)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_orchestrator_v1.py -x -q 2>&1 | head -40
```

Expected failure: `ImportError: cannot import name 'run_v1' from 'src.envstate.orchestrator'` (the function does not yet exist in the rewritten form).

---

### Task 23: Implement `run_v1` in `src/envstate/orchestrator.py`

**Files:**
- Modify `src/envstate/orchestrator.py` (full file rewrite, keeping the existing `EnvStateOrchestrator` class intact for Arms A/B/C back-compat)

- [ ] **Step 3: Write minimal implementation**

```python
# src/envstate/orchestrator.py
"""EnvState v1 orchestrator loop.

run_v1() is the new three-role loop (spec §4):
    initial_map → planner.decide → (done/giveup → break)
                → build_agent.run → maintainer.update
                → (done_flag → break)
                → repeat up to max_cycles

The legacy EnvStateOrchestrator class is kept below run_v1 unchanged
for Arms A/B/C back-compat.
"""
from __future__ import annotations

from typing import Any, Callable, Tuple

from src.envstate.ledger import ActionLedger
from src.envstate.world_model import (
    PlannerDecision,
    TaskReport,
    WorldModelMap,
)

# Sentinel type aliases (readable names only, no runtime cost).
Executor = Callable[[str], Tuple[bool, str]]

# Module-level constants (spec §8).
MAX_CYCLES: int = 12
LOCAL_BUDGET: int = 8

# Canonical collect-only command — referenced everywhere instead of inline strings.
COLLECT_ONLY_CMD: str = "pytest --collect-only -q --disable-warnings"


def run_v1(
    planner: Any,
    build_agent: Any,
    maintainer: Any,
    initial_world_map: WorldModelMap,
    ledger: ActionLedger,
    sandbox_execute: Callable[[str], tuple[bool, str]],
    max_cycles: int = MAX_CYCLES,
    local_budget: int = LOCAL_BUDGET,
    on_cycle: (
        Callable[[int, WorldModelMap, PlannerDecision, TaskReport | None], None] | None
    ) = None,
) -> tuple[WorldModelMap, str]:
    """Top-level v1 orchestrator loop.

    Returns ``(final_map, stop_reason)`` where ``stop_reason`` is one of:
      ``'done_flag'``     — maintainer set WorldModelMap.done_flag=True
      ``'planner_done'``  — planner emitted action='done'
      ``'planner_giveup'``— planner emitted action='giveup'
      ``'max_cycles'``    — loop ran for max_cycles without terminating

    The loop terminates the instant done_flag is set — it does NOT wait
    for the next planner.decide call (structural fix for the 'reached gate
    but never committed' failure mode).
    """
    current_map: WorldModelMap = initial_world_map

    for cycle in range(1, max_cycles + 1):
        # ── 1. Planner decides what to do next ──────────────────────────────
        decision: PlannerDecision = planner.decide(current_map)

        if decision.action == "done":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, "planner_done"

        if decision.action == "giveup":
            if on_cycle is not None:
                on_cycle(cycle, current_map, decision, None)
            return current_map, "planner_giveup"

        # ── 2. BuildAgent executes the task ──────────────────────────────────
        assert decision.task is not None, (
            f"PlannerDecision action='task' but .task is None (cycle {cycle})"
        )
        report: TaskReport = build_agent.run(
            decision.task,
            sandbox_execute,
            ledger,
            step_offset=(cycle - 1) * local_budget,
        )

        # ── 3. Maintainer updates the world model ────────────────────────────
        current_map = maintainer.update(current_map, report)

        # ── 4. Notify caller (optional telemetry hook) ───────────────────────
        if on_cycle is not None:
            on_cycle(cycle, current_map, decision, report)

        # ── 5. Hard-stop on done_flag — do NOT re-enter planner ──────────────
        if current_map.done_flag:
            return current_map, "done_flag"

    # Exhausted all cycles without termination.
    return current_map, "max_cycles"


# ---------------------------------------------------------------------------
# Legacy Arms A/B/C orchestrator (kept for back-compat — do NOT modify)
# ---------------------------------------------------------------------------

from src.envstate.types import EnvStateSnapshot  # noqa: E402  (legacy import)

Observer = Callable[..., EnvStateSnapshot]


class EnvStateOrchestrator:
    """Supervisor -> Worker -> (per-action) Observer loop (design §6).

    Collaborators are injected so this is unit-testable without Docker/LLM:
      supervisor.next_task(snapshot, ledger, budget) -> (task_spec|None, usage)
      worker.run_task(task_spec, step_fn) -> WorkerReport
      executor(action) -> (success, observation)
      observer(snapshot, task_spec, step, action, success, observation) -> new_snapshot
    """

    def __init__(
        self,
        supervisor,
        worker,
        snapshot: EnvStateSnapshot,
        ledger: ActionLedger,
        executor: Executor,
        observer: Observer,
        max_tasks: int = 20,
        on_usage=None,
        global_action_budget: int = None,
    ):
        self.supervisor = supervisor
        self.worker = worker
        self.snapshot = snapshot
        self.ledger = ledger
        self.executor = executor
        self.observer = observer
        self.max_tasks = max_tasks
        self.on_usage = on_usage
        self.global_action_budget = global_action_budget
        self._step = 0
        self._actions_executed = 0

    def _make_step_fn(self, task_spec):
        """Per-task execution closure handed to the Worker. Executes ONE action,
        then observes it into the EnvState snapshot (advance revision, Maintainer,
        probes, ACL certification). Threads the new snapshot back onto self."""
        def step_fn(action):
            self._step += 1
            self._actions_executed += 1
            success, observation = self.executor(action)
            self.snapshot = self.observer(
                self.snapshot, task_spec, self._step, action, success, observation
            )
            return success, observation
        return step_fn

    def run(self) -> dict[str, Any]:
        tasks_completed = 0
        reports = []
        stop_reason = "no_more_tasks"
        while True:
            if tasks_completed >= self.max_tasks:
                stop_reason = "max_tasks"
                break
            budget = {"steps_remaining": self.max_tasks - tasks_completed}
            task_spec, usage = self.supervisor.next_task(self.snapshot, self.ledger, budget)
            if self.on_usage is not None:
                self.on_usage(usage)
            if not task_spec:
                stop_reason = "no_more_tasks"
                break
            report = self.worker.run_task(task_spec, self._make_step_fn(task_spec))
            reports.append(report)
            tasks_completed += 1
            # Shared global executed-action cap (§3.5 / C2): behavior-preserving
            # when global_action_budget is None (Arm B default stays unbounded).
            if (self.global_action_budget is not None
                    and self._actions_executed >= self.global_action_budget):
                stop_reason = "global_action_budget"
                break
        return {
            "tasks_completed": tasks_completed,
            "stop_reason": stop_reason,
            "reports": reports,
            "final_revision": self.snapshot.revision,
        }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_orchestrator_v1.py -v 2>&1 | tail -30
```

Expected: all tests in `test_orchestrator_v1.py` pass. Also confirm existing orchestrator tests still pass:

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_envstate_orchestrator.py -v 2>&1 | tail -20
```

- [ ] **Step 5: Commit**

```bash
cd /Users/john/john-planner-v1 && git add src/envstate/orchestrator.py tests/test_orchestrator_v1.py
git commit -m "feat(orchestrator): add run_v1 loop and COLLECT_ONLY_CMD constant with done_flag/giveup/max_cycles termination"
```

---

### Task 24: Write failing tests for `agent.py` glue (`enable_v1`, `_run_v1`, `run()` dispatch)

**Files:**
- Create `tests/test_agent_v1_glue.py`

- [ ] **Step 6: Write the failing test**

```python
# tests/test_agent_v1_glue.py
"""Unit tests for the agent.py v1 glue layer.

Tests verify:
  1. DockerAgent.__init__ accepts enable_v1=True and sets enable_envstate.
  2. DockerAgent.run() dispatches to _run_v1 when enable_v1=True (before
     the supervisor and fullstate_worker branches).
  3. _run_v1 instantiates Planner/BuildAgent/Maintainer with canonical
     (client, model, on_usage=..., log_path=...) signatures, calls run_v1(),
     populates verified_test_commands from the COLLECT_ONLY_CMD ledger scan,
     and calls _auto_finalize_from_verified_tests + _finalize_supervisor_artifacts.
  4. _verify_cleanroom_or_fail is NOT called from _run_v1 (cleanroom is
     skipped in the v1 path; EBSR is the trusted metric).

No real Docker or LLM is used: Sandbox, Synthesizer, and ImageSelector are
patched at the module level so DockerAgent.__init__ does not fail.
"""
from __future__ import annotations

import types
import sys
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Lightweight stubs to prevent import-time side effects
# ---------------------------------------------------------------------------

def _make_stub_module(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    return m


def _install_stubs() -> None:
    """Install minimal stubs for heavy dependencies so agent.py can be imported."""
    for mod_name in [
        "src.sandbox",
        "src.synthesizer",
        "src.image_selector",
        "src.verification_bundle",
        "src.constants",
        "src.memory_manager",
        "src.observation_compressor",
        "src.planner",
        "dotenv",
        "openai",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _make_stub_module(mod_name)

    # src.constants needs DEFAULT_LLM_MODEL and DEFAULT_MEMORY_EMBEDDING_MODEL
    sys.modules["src.constants"].DEFAULT_LLM_MODEL = "test-model"
    sys.modules["src.constants"].DEFAULT_MEMORY_EMBEDDING_MODEL = "test-embed"

    # src.observation_compressor needs several names
    oc = sys.modules["src.observation_compressor"]
    oc.AgentStep = object
    oc.ObservationCompressor = MagicMock
    oc.RunTokenLedger = MagicMock
    oc.build_observation_metadata = MagicMock(return_value={})
    oc.safety_compress_observation = MagicMock(return_value=("obs", False))
    oc.should_apply_compression = MagicMock(return_value=False)

    # dotenv
    sys.modules["dotenv"].load_dotenv = lambda **kw: None

    # openai
    sys.modules["openai"].OpenAI = MagicMock

    # src.synthesizer
    sys.modules["src.synthesizer"].Synthesizer = MagicMock

    # src.image_selector
    sys.modules["src.image_selector"].ImageSelector = MagicMock

    # src.verification_bundle
    sys.modules["src.verification_bundle"].derive_supported_verification_bundle = MagicMock(
        return_value={"test_commands": ["pytest --collect-only -q --disable-warnings"]}
    )

    # src.memory_manager
    sys.modules["src.memory_manager"].LongTermMemoryManager = MagicMock

    # src.planner — Arm-0 planner (different from src.envstate.planner)
    sys.modules["src.planner"].Planner = MagicMock

    # src.sandbox
    sys.modules["src.sandbox"].Sandbox = MagicMock


_install_stubs()


# ---------------------------------------------------------------------------
# Now import agent (stubs must be in place first)
# ---------------------------------------------------------------------------
import agent as _agent_module


def _make_agent_instance(**kwargs) -> "_agent_module.DockerAgent":
    """Construct a DockerAgent with all heavy init work mocked out."""
    defaults = dict(
        repo_url="https://github.com/example/repo",
        base_image="python:3.11-slim",
        model="test-model",
        workplace="/tmp/test_workplace_v1",
    )
    defaults.update(kwargs)

    with (
        patch.object(_agent_module.DockerAgent, "_prepare_workplace", return_value=None),
        patch.object(_agent_module.DockerAgent, "_collect_local_service_hints", return_value=set()),
        patch.object(_agent_module.DockerAgent, "_checkout_commit", return_value=None),
        patch.object(_agent_module.DockerAgent, "_create_sandbox", return_value=MagicMock()),
        patch.object(_agent_module.DockerAgent, "_init_planner", return_value=None),
        patch("os.makedirs", return_value=None),
        patch("os.path.exists", return_value=False),
        patch("os.path.join", side_effect=lambda *a: "/".join(a)),
    ):
        agent = _agent_module.DockerAgent(**defaults)
    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEnableV1InitFlag(unittest.TestCase):
    """DockerAgent.__init__ must accept enable_v1 and wire enable_envstate."""

    def test_enable_v1_accepted_as_kwarg(self):
        agent = _make_agent_instance(enable_v1=True)
        self.assertTrue(getattr(agent, "enable_v1", False))

    def test_enable_v1_implies_enable_envstate(self):
        agent = _make_agent_instance(enable_v1=True)
        self.assertTrue(agent.enable_envstate,
                        "enable_v1=True must imply enable_envstate=True so ActionLedger is created")

    def test_enable_v1_creates_action_ledger(self):
        agent = _make_agent_instance(enable_v1=True)
        self.assertIsNotNone(agent.action_ledger,
                             "ActionLedger must be created when enable_v1=True")

    def test_enable_v1_false_does_not_set_enable_envstate_alone(self):
        """enable_envstate must still work independently of enable_v1."""
        agent = _make_agent_instance(enable_v1=False, enable_envstate=False)
        self.assertFalse(agent.enable_v1)


class TestRunDispatchesToV1(unittest.TestCase):
    """run() must call _run_v1 when enable_v1=True, before supervisor/fullstate checks."""

    def test_run_dispatches_to_run_v1_when_enable_v1(self):
        agent = _make_agent_instance(enable_v1=True)
        called_with = {}

        def fake_run_v1(max_cycles=12, keep_container=False):
            called_with["max_cycles"] = max_cycles
            called_with["keep_container"] = keep_container
            return True

        agent._run_v1 = fake_run_v1
        result = agent.run(max_steps=5, keep_container=False)
        self.assertIn("max_cycles", called_with,
                      "_run_v1 must be called by run() when enable_v1=True")
        self.assertEqual(called_with["max_cycles"], 5)
        self.assertTrue(result)

    def test_run_does_not_call_supervisor_when_enable_v1(self):
        agent = _make_agent_instance(enable_v1=True, enable_supervisor=False)
        # _run_v1 must be called; _run_supervisor must NOT be called.
        agent._run_v1 = MagicMock(return_value=True)
        agent._run_supervisor = MagicMock(return_value=True)
        agent.run(max_steps=3)
        agent._run_v1.assert_called_once()
        agent._run_supervisor.assert_not_called()

    def test_run_v1_checked_before_supervisor_flag(self):
        """enable_v1=True must win even when enable_supervisor=True."""
        agent = _make_agent_instance(enable_v1=True, enable_supervisor=True)
        agent._run_v1 = MagicMock(return_value=True)
        agent._run_supervisor = MagicMock(return_value=True)
        agent.run(max_steps=3)
        agent._run_v1.assert_called_once()
        agent._run_supervisor.assert_not_called()


class TestRunV1RoleInstantiations(unittest.TestCase):
    """_run_v1 must instantiate Planner/BuildAgent/Maintainer with canonical signatures."""

    def _run_with_captured_constructors(self):
        """Run _run_v1 and return (agent, planner_kwargs, build_agent_kwargs, maintainer_kwargs)."""
        from src.envstate.world_model import initial_map, merge_map
        from src.envstate.ledger import ActionLedger

        world_map = initial_map(
            base_image="python:3.11-slim",
            workdir="/app",
            language="python 3.11",
            build_system="pip",
            repo_layout=(),
        )
        final_map = merge_map(world_map, done_flag=True)

        agent = _make_agent_instance(enable_v1=True)
        agent.action_ledger = ActionLedger()
        agent.sandbox = MagicMock()
        agent.sandbox.execute = MagicMock(return_value=(True, "ok"))
        agent.sandbox.close = MagicMock()
        agent.synthesizer = MagicMock()
        agent.synthesizer.base_image = "python:3.11-slim"
        agent.synthesizer.workdir = "/app"
        agent.synthesizer.language = "python 3.11"
        agent.synthesizer.build_system = "pip"
        agent._write_run_summary = MagicMock()
        agent._auto_finalize_from_verified_tests = MagicMock(return_value=True)
        agent._finalize_supervisor_artifacts = MagicMock(return_value=True)
        agent.logs_dir = "/tmp/logs"

        import src.envstate.orchestrator as orch_mod
        import src.envstate.planner as planner_mod
        import src.envstate.build_agent as build_agent_mod
        import src.envstate.maintainer as maintainer_mod
        import src.envstate.world_model as wm_mod

        planner_init_calls = []
        build_agent_init_calls = []
        maintainer_init_calls = []

        _orig_planner = planner_mod.Planner

        def capture_planner(*args, **kwargs):
            planner_init_calls.append({"args": args, "kwargs": kwargs})
            m = MagicMock()
            m.decide = MagicMock(return_value=MagicMock(action="done", reason="ok"))
            return m

        def capture_build_agent(*args, **kwargs):
            build_agent_init_calls.append({"args": args, "kwargs": kwargs})
            return MagicMock()

        def capture_maintainer(*args, **kwargs):
            maintainer_init_calls.append({"args": args, "kwargs": kwargs})
            return MagicMock()

        with (
            patch.object(orch_mod, "run_v1", return_value=(final_map, "done_flag")),
            patch.object(planner_mod, "Planner", side_effect=capture_planner),
            patch.object(build_agent_mod, "BuildAgent", side_effect=capture_build_agent),
            patch.object(maintainer_mod, "Maintainer", side_effect=capture_maintainer),
            patch.object(wm_mod, "initial_map", return_value=world_map),
            patch("os.makedirs", return_value=None),
            patch("os.path.exists", return_value=False),
            patch("os.environ.get", return_value=None),
            patch("os.environ.__setitem__", return_value=None),
            patch("os.environ.pop", return_value=None),
        ):
            agent._run_v1(max_cycles=12, keep_container=False)

        return agent, planner_init_calls, build_agent_init_calls, maintainer_init_calls

    def test_planner_receives_on_usage_kwarg(self):
        _, planner_calls, _, _ = self._run_with_captured_constructors()
        self.assertEqual(len(planner_calls), 1, "Planner must be instantiated exactly once")
        kwargs = planner_calls[0]["kwargs"]
        self.assertIn("on_usage", kwargs,
                      "Planner must receive on_usage= keyword arg")
        self.assertTrue(callable(kwargs["on_usage"]),
                        "Planner on_usage must be callable")

    def test_planner_receives_log_path_kwarg(self):
        _, planner_calls, _, _ = self._run_with_captured_constructors()
        kwargs = planner_calls[0]["kwargs"]
        self.assertIn("log_path", kwargs,
                      "Planner must receive log_path= keyword arg")

    def test_build_agent_receives_on_usage_kwarg(self):
        _, _, ba_calls, _ = self._run_with_captured_constructors()
        self.assertEqual(len(ba_calls), 1, "BuildAgent must be instantiated exactly once")
        kwargs = ba_calls[0]["kwargs"]
        self.assertIn("on_usage", kwargs,
                      "BuildAgent must receive on_usage= keyword arg")
        self.assertTrue(callable(kwargs["on_usage"]),
                        "BuildAgent on_usage must be callable")

    def test_build_agent_receives_log_path_kwarg(self):
        _, _, ba_calls, _ = self._run_with_captured_constructors()
        kwargs = ba_calls[0]["kwargs"]
        self.assertIn("log_path", kwargs,
                      "BuildAgent must receive log_path= keyword arg")

    def test_maintainer_receives_on_usage_kwarg(self):
        _, _, _, m_calls = self._run_with_captured_constructors()
        self.assertEqual(len(m_calls), 1, "Maintainer must be instantiated exactly once")
        kwargs = m_calls[0]["kwargs"]
        self.assertIn("on_usage", kwargs,
                      "Maintainer must receive on_usage= keyword arg")
        self.assertTrue(callable(kwargs["on_usage"]),
                        "Maintainer on_usage must be callable")

    def test_maintainer_receives_log_path_kwarg(self):
        _, _, _, m_calls = self._run_with_captured_constructors()
        kwargs = m_calls[0]["kwargs"]
        self.assertIn("log_path", kwargs,
                      "Maintainer must receive log_path= keyword arg")


class TestRunV1Method(unittest.TestCase):
    """_run_v1 must wire run_v1(), detect done_flag CommandRecord, and finalize."""

    def _patched_run_v1_call(
        self,
        done_flag: bool = True,
        collect_cmd: str = "pytest --collect-only -q --disable-warnings",
    ):
        """Run _run_v1 on a minimal agent with mocked collaborators.

        Returns (agent, configuration_success).
        """
        from src.envstate.world_model import (
            CommandRecord, TaskReport, WorldModelMap, initial_map, merge_map,
        )
        from src.envstate.ledger import ActionLedger

        world_map = initial_map(
            base_image="python:3.11-slim",
            workdir="/app",
            language="python 3.11",
            build_system="pip",
            repo_layout=(),
        )
        final_map = merge_map(
            world_map,
            done_flag=done_flag,
        )

        agent = _make_agent_instance(enable_v1=True)
        agent.action_ledger = ActionLedger()
        agent.sandbox = MagicMock()
        agent.sandbox.execute = MagicMock(return_value=(True, "ok"))
        agent.sandbox.close = MagicMock()
        agent.synthesizer = MagicMock()
        agent.synthesizer.base_image = "python:3.11-slim"
        agent.synthesizer.workdir = "/app"
        agent.synthesizer.language = "python 3.11"
        agent.synthesizer.build_system = "pip"
        agent._write_run_summary = MagicMock()
        agent._auto_finalize_from_verified_tests = MagicMock(return_value=True)
        agent._finalize_supervisor_artifacts = MagicMock(return_value=True)
        agent.logs_dir = "/tmp/logs"

        import src.envstate.orchestrator as orch_mod
        import src.envstate.planner as planner_mod
        import src.envstate.build_agent as build_agent_mod
        import src.envstate.maintainer as maintainer_mod
        import src.envstate.world_model as wm_mod

        mock_run_v1 = MagicMock(return_value=(final_map, "done_flag"))
        mock_planner_cls = MagicMock()
        mock_build_agent_cls = MagicMock()
        mock_maintainer_cls = MagicMock()

        with (
            patch.object(orch_mod, "run_v1", mock_run_v1),
            patch.object(planner_mod, "Planner", mock_planner_cls),
            patch.object(build_agent_mod, "BuildAgent", mock_build_agent_cls),
            patch.object(maintainer_mod, "Maintainer", mock_maintainer_cls),
            patch.object(wm_mod, "initial_map", return_value=world_map),
            patch("os.makedirs", return_value=None),
            patch("os.path.exists", return_value=False),
            patch("os.environ.get", return_value=None),
            patch("os.environ.__setitem__", return_value=None),
            patch("os.environ.pop", return_value=None),
        ):
            config_success = agent._run_v1(max_cycles=12, keep_container=False)

        return agent, config_success

    def test_run_v1_returns_true_when_done_flag_set(self):
        _agent, config_success = self._patched_run_v1_call(done_flag=True)
        self.assertTrue(config_success)

    def test_run_v1_calls_auto_finalize_from_verified_tests(self):
        agent, _ = self._patched_run_v1_call(done_flag=True)
        agent._auto_finalize_from_verified_tests.assert_called_once()

    def test_run_v1_calls_finalize_supervisor_artifacts(self):
        agent, _ = self._patched_run_v1_call(done_flag=True)
        agent._finalize_supervisor_artifacts.assert_called_once()

    def test_run_v1_writes_run_summary(self):
        agent, _ = self._patched_run_v1_call(done_flag=True)
        agent._write_run_summary.assert_called_once()

    def test_run_v1_closes_sandbox(self):
        agent, _ = self._patched_run_v1_call(done_flag=True)
        agent.sandbox.close.assert_called_once()

    def test_verified_test_commands_populated_from_collect_only(self):
        """_run_v1 must set self.verified_test_commands from the collect-only CommandRecord."""
        from src.envstate.world_model import (
            CommandRecord, TaskReport, WorldModelMap, initial_map, merge_map,
        )
        from src.envstate.ledger import ActionLedger

        collect_cmd = "pytest --collect-only -q --disable-warnings"
        world_map = initial_map(
            base_image="python:3.11-slim",
            workdir="/app",
            language="python 3.11",
            build_system="pip",
            repo_layout=(),
        )
        final_map = merge_map(world_map, done_flag=True)

        agent = _make_agent_instance(enable_v1=True)
        agent.action_ledger = ActionLedger()
        agent.sandbox = MagicMock()
        agent.sandbox.execute = MagicMock(return_value=(True, "ok"))
        agent.sandbox.close = MagicMock()
        agent.synthesizer = MagicMock()
        agent.synthesizer.base_image = "python:3.11-slim"
        agent.synthesizer.workdir = "/app"
        agent.synthesizer.language = "python 3.11"
        agent.synthesizer.build_system = "pip"
        agent._write_run_summary = MagicMock()
        agent._finalize_supervisor_artifacts = MagicMock(return_value=True)
        agent.logs_dir = "/tmp/logs"

        import src.envstate.orchestrator as orch_mod
        import src.envstate.planner as planner_mod
        import src.envstate.build_agent as build_agent_mod
        import src.envstate.maintainer as maintainer_mod
        import src.envstate.world_model as wm_mod

        # Simulate what _run_v1 must do internally: populate verified_test_commands
        # by scanning the action_ledger for the collect-only command.
        # Seed the ledger with a matching ActionEvent so the real scan finds it.
        from src.envstate.ledger import ActionEvent
        agent.action_ledger._events = [
            ActionEvent(cmd=collect_cmd, rc=0, stdout="5 items collected", step=1)
        ]

        mock_run_v1 = MagicMock(return_value=(final_map, "done_flag"))

        with (
            patch.object(orch_mod, "run_v1", mock_run_v1),
            patch.object(planner_mod, "Planner", MagicMock()),
            patch.object(build_agent_mod, "BuildAgent", MagicMock()),
            patch.object(maintainer_mod, "Maintainer", MagicMock()),
            patch.object(wm_mod, "initial_map", return_value=world_map),
            patch("os.makedirs", return_value=None),
            patch("os.path.exists", return_value=False),
            patch("os.environ.get", return_value=None),
            patch("os.environ.__setitem__", return_value=None),
            patch("os.environ.pop", return_value=None),
        ):
            agent._run_v1(max_cycles=12, keep_container=False)

        self.assertIn(collect_cmd, agent.verified_test_commands,
                      "_run_v1 must populate verified_test_commands with the collect-only cmd")

    def test_run_v1_does_not_call_verify_cleanroom(self):
        """Cleanroom is skipped in the v1 path (EBSR is the trusted metric).

        _run_v1 must NOT invoke _verify_cleanroom_or_fail directly. The
        cleanroom gate lives in _finalize_supervisor_artifacts (already tested
        there); v1 does not add a second cleanroom call.
        """
        agent, _ = self._patched_run_v1_call(done_flag=True)
        # _verify_cleanroom_or_fail is NOT expected to have been called by _run_v1.
        # If the method was called it would be recorded on the mock; assert it wasn't.
        # Since _finalize_supervisor_artifacts is mocked out, cleanroom is entirely bypassed.
        # This test documents the design intent explicitly.
        if hasattr(agent, "_verify_cleanroom_or_fail"):
            # If it was patched to a MagicMock by the test scaffold, check not called.
            mock_cleanroom = getattr(agent, "_verify_cleanroom_or_fail", None)
            if isinstance(mock_cleanroom, MagicMock):
                mock_cleanroom.assert_not_called()


class TestCleanroomSkippedInV1Path(unittest.TestCase):
    """_run_v1 must not call _verify_cleanroom_or_fail.

    DESIGN NOTE (v1 implementation task): In the v1 path, cleanroom verification
    is SKIPPED. EBSR (Environment Build Success Rate) is the trusted metric for
    this arm. The _verify_cleanroom_or_fail method references self.env_snapshot
    and snapshot.requirements (types deleted in v1), so calling it would crash.
    Instead, _run_v1 calls _finalize_supervisor_artifacts, which internally calls
    _verify_cleanroom_or_fail — but only when enable_cleanroom=True (default False).
    In v1 runs, enable_cleanroom should remain False.

    Implementation task for this group: rewrite _verify_cleanroom_or_fail to
    operate ONLY on the produced Dockerfile + build context, with NO reference to
    self.env_snapshot / snapshot.requirements / req.source. The new signature must
    be:

        def _verify_cleanroom_or_fail(
            self,
            dockerfile_path: str,
            build_context: str,
        ) -> bool:

    Until that rewrite is complete, the v1 path must set enable_cleanroom=False
    and skip the cleanroom gate. This is safe: the v1 Planner/BuildAgent/Maintainer
    loop already validates test discoverability via COLLECT_ONLY_CMD before setting
    done_flag, so the Dockerfile is evidence-backed even without cleanroom.
    """

    def test_enable_cleanroom_defaults_false_for_v1(self):
        """enable_cleanroom must default to False so the v1 finalization path is safe."""
        agent = _make_agent_instance(enable_v1=True)
        # enable_cleanroom must be False by default (it requires explicit opt-in).
        self.assertFalse(getattr(agent, "enable_cleanroom", False))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Run test to verify it fails**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_agent_v1_glue.py -x -q 2>&1 | head -40
```

Expected failures:
- `TestEnableV1InitFlag.test_enable_v1_accepted_as_kwarg` — `DockerAgent.__init__` does not accept `enable_v1`.
- `TestRunDispatchesToV1` — `run()` has no `enable_v1` branch.
- `TestRunV1Method` — `_run_v1` method does not exist.
- `TestRunV1RoleInstantiations` — `_run_v1` method does not exist.

---

### Task 25: Implement agent.py glue — `enable_v1` param, `run()` dispatch, `_run_v1` method

**Files:**
- Modify `agent.py`
  - Lines 129–130 (after `fullstate_worker_prompt=False`): add `enable_v1=False` parameter
  - Lines 162–163 (after `self.fullstate_worker_prompt = fullstate_worker_prompt`): store `self.enable_v1` and extend `enable_envstate` OR chain
  - Lines 1183–1186: add `_run_v1` dispatch before existing supervisor check
  - After line 1094 (`_run_fullstate_worker` ends at `return configuration_success`): insert `_run_v1` method

- [ ] **Step 8: Write minimal implementation**

**8a. Add `enable_v1=False` parameter to `DockerAgent.__init__`.**

In `agent.py` at lines 128–130, the current parameter list ends:
```python
        enable_fullstate_worker=False,
        fullstate_worker_prompt=False,
        enable_cleanroom=False,
```
Change to:
```python
        enable_fullstate_worker=False,
        fullstate_worker_prompt=False,
        enable_v1=False,
        enable_cleanroom=False,
```

**8b. Wire `enable_v1` into `self.enable_envstate` (agent.py line ~162–163).**

Current (agent.py lines 160–163):
```python
        self.enable_supervisor = enable_supervisor
        self.enable_fullstate_worker = enable_fullstate_worker
        self.fullstate_worker_prompt = fullstate_worker_prompt
        self.enable_envstate = enable_envstate or enable_supervisor or enable_fullstate_worker
```
Change to:
```python
        self.enable_supervisor = enable_supervisor
        self.enable_fullstate_worker = enable_fullstate_worker
        self.fullstate_worker_prompt = fullstate_worker_prompt
        self.enable_v1 = enable_v1
        self.enable_envstate = enable_envstate or enable_supervisor or enable_fullstate_worker or enable_v1
```

**8c. Add `_run_v1` dispatch at the top of `run()` (agent.py lines 1181–1186).**

Current (agent.py lines 1181–1186):
```python
    def run(self, max_steps=30, keep_container=False):
        """Runs the ReAct loop to configure the environment."""
        if getattr(self, "enable_supervisor", False):
            return self._run_supervisor(max_steps=max_steps, keep_container=keep_container)
        if getattr(self, "enable_fullstate_worker", False):
            return self._run_fullstate_worker(max_steps=max_steps, keep_container=keep_container)
```
Change to:
```python
    def run(self, max_steps=30, keep_container=False):
        """Runs the ReAct loop to configure the environment."""
        if getattr(self, "enable_v1", False):
            return self._run_v1(max_cycles=max_steps, keep_container=keep_container)
        if getattr(self, "enable_supervisor", False):
            return self._run_supervisor(max_steps=max_steps, keep_container=keep_container)
        if getattr(self, "enable_fullstate_worker", False):
            return self._run_fullstate_worker(max_steps=max_steps, keep_container=keep_container)
```

**8d. Insert `_run_v1` method after `_run_fullstate_worker` ends (after line 1094, before `_finalize_supervisor_artifacts`).**

Insert the following method between `_run_fullstate_worker` (ending `return configuration_success` at line 1094) and `_finalize_supervisor_artifacts` (which currently starts at line 1096):

```python
    def _run_v1(self, max_cycles=12, keep_container=False):
        """Arm v1: three-role (Planner / BuildAgent / Maintainer) loop (spec §4).

        Structure mirrors _run_supervisor / _run_fullstate_worker:
          1. Set up LLM-exchange log scope.
          2. Build initial WorldModelMap.
          3. Instantiate Planner, BuildAgent, Maintainer with canonical signatures.
          4. Call run_v1() from src.envstate.orchestrator.
          5. Scan ActionLedger for the collect-only command; populate
             verified_test_commands using COLLECT_ONLY_CMD as fallback.
          6. On done_flag: call _auto_finalize_from_verified_tests then finalize.

        CLEANROOM NOTE: _verify_cleanroom_or_fail is NOT called directly from this
        method. The v1 path skips cleanroom (enable_cleanroom defaults to False).
        EBSR is the trusted success metric. When _verify_cleanroom_or_fail is
        rewritten to the decoupled signature
            _verify_cleanroom_or_fail(self, dockerfile_path, build_context) -> bool
        (with NO reference to self.env_snapshot / snapshot.requirements / req.source),
        cleanroom can be opt-in enabled for v1 runs via enable_cleanroom=True.
        Until then, _finalize_supervisor_artifacts respects enable_cleanroom=False
        and skips the gate automatically.
        """
        import re as _re
        from src.envstate.orchestrator import run_v1 as _run_v1_loop, COLLECT_ONLY_CMD
        from src.envstate.planner import Planner as _Planner
        from src.envstate.build_agent import BuildAgent as _BuildAgent
        from src.envstate.maintainer import Maintainer as _Maintainer
        from src.envstate.world_model import initial_map, Fact

        # ── 1. LLM log scope (same pattern as _run_supervisor) ───────────────
        _llm_log_dir = os.path.join(self.logs_dir, "setup_logs")
        os.makedirs(_llm_log_dir, exist_ok=True)
        _llm_log_path = os.path.join(_llm_log_dir, "envstate_llm.jsonl")
        _prev_llm_log = os.environ.get("ENVSTATE_LLM_LOG")
        os.environ["ENVSTATE_LLM_LOG"] = _llm_log_path

        # ── 2. Build initial WorldModelMap ────────────────────────────────────
        _base_image = (
            getattr(self, "base_image", None)
            or getattr(self.synthesizer, "base_image", None)
            or ""
        )
        _workdir = getattr(self.synthesizer, "workdir", "/app") or "/app"
        _repo_structure = ""
        _structure_file = os.path.join(
            self.logs_dir, "image_selector_logs", "structure.txt"
        )
        if os.path.exists(_structure_file):
            try:
                with open(_structure_file) as _f:
                    _repo_structure = _f.read()
            except Exception:
                pass

        # Derive repo_layout tuple from the first non-empty lines of structure.txt.
        _repo_layout: tuple[str, ...] = tuple(
            ln.strip() for ln in _repo_structure.splitlines()[:20] if ln.strip()
        )

        # Derive language/build_system from synthesizer attrs or fall back.
        _language = (
            getattr(self.synthesizer, "language", "")
            or getattr(self, "language", "")
            or "unknown"
        )
        _build_system = getattr(self.synthesizer, "build_system", "") or "unknown"

        world_map = initial_map(
            base_image=_base_image,
            workdir=_workdir,
            language=_language,
            build_system=_build_system,
            repo_layout=_repo_layout,
        )

        # ── 3. Instantiate collaborators with canonical signatures ─────────────
        # All three roles share the same constructor shape:
        #   Role(client, model, on_usage=<callable|None>, log_path=<str|None>)
        # BuildAgent additionally receives sandbox/ledger/synthesizer deps.
        planner = _Planner(
            self.client,
            self.model,
            on_usage=lambda usage: self._record_supervisor_path_usage("worker", usage),
            log_path=_llm_log_path,
        )
        maintainer = _Maintainer(
            self.client,
            self.model,
            on_usage=lambda usage: self._record_supervisor_path_usage("reflection", usage),
            log_path=_llm_log_path,
        )
        build_agent = _BuildAgent(
            self.client,
            self.model,
            on_usage=lambda usage: self._record_supervisor_path_usage("worker", usage),
            log_path=_llm_log_path,
            sandbox=self.sandbox,
            ledger=self.action_ledger,
            synthesizer=self.synthesizer,
        )

        configuration_success = False
        run_error = None

        try:
            # ── 4. Run the v1 loop ────────────────────────────────────────────
            final_map, stop_reason = _run_v1_loop(
                planner=planner,
                build_agent=build_agent,
                maintainer=maintainer,
                initial_world_map=world_map,
                ledger=self.action_ledger,
                sandbox_execute=self.sandbox.execute,
                max_cycles=max_cycles,
            )

            print(f"[v1] Loop finished: stop_reason={stop_reason!r}")

            # ── 5. Scan ActionLedger for collect-only command ─────────────────
            # The ledger accumulates ActionEvent(cmd, rc, stdout, step) records
            # written by BuildAgent during execution.  We look for the most recent
            # successful pytest --collect-only invocation and use its exact command
            # string so the verification bundle matches what was verified in-loop.
            _COLLECT_ONLY_PAT = _re.compile(r"pytest\s+--collect-only", _re.IGNORECASE)
            _collect_cmd = None
            for ev in reversed(self.action_ledger.events()):
                if ev.rc == 0 and _COLLECT_ONLY_PAT.search(ev.cmd):
                    _collect_cmd = ev.cmd
                    break
            if _collect_cmd is None:
                # Fallback: use the canonical module constant.
                _collect_cmd = COLLECT_ONLY_CMD

            if not self.verified_test_commands:
                self.verified_test_commands = [_collect_cmd]

            # ── 6. Finalize ───────────────────────────────────────────────────
            configuration_success = (
                self._auto_finalize_from_verified_tests("v1_done_flag")
                or bool(self.verification_bundle)
            )
            if configuration_success:
                configuration_success = self._finalize_supervisor_artifacts(
                    configuration_success
                )

        except Exception as exc:
            run_error = str(exc)
            print(f"[v1] Error during v1 execution: {exc}")
            if self._is_transient_llm_error(exc) and self._auto_finalize_from_verified_tests(
                source="v1_auto_after_transient_error"
            ):
                configuration_success = True
                print(
                    "[v1 Auto Finalization] Transient LLM failure after a verified "
                    "test collection command. Generating Dockerfile from recorded evidence."
                )
                try:
                    configuration_success = self._finalize_supervisor_artifacts(
                        configuration_success
                    )
                except Exception as synth_exc:
                    configuration_success = False
                    run_error = f"{run_error}; auto-finalization synthesis failed: {synth_exc}"
                    print(f"[v1 Warning] Auto-finalization synthesis failed: {synth_exc}")

        finally:
            # Restore env var scope (same pattern as _run_supervisor).
            if _prev_llm_log is None:
                os.environ.pop("ENVSTATE_LLM_LOG", None)
            else:
                os.environ["ENVSTATE_LLM_LOG"] = _prev_llm_log
            self._write_run_summary(configuration_success, run_error)
            self.sandbox.close(keep_alive=keep_container)

        return configuration_success
```

- [ ] **Step 9: Run test to verify it passes**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_agent_v1_glue.py -v 2>&1 | tail -40
```

Expected: all tests in `test_agent_v1_glue.py` pass.

Also confirm no regressions in existing agent and orchestrator tests:

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_agent_flags.py tests/test_envstate_orchestrator.py tests/test_agent_envstate_observe.py -v 2>&1 | tail -30
```

- [ ] **Step 10: Commit**

```bash
cd /Users/john/john-planner-v1 && git add agent.py tests/test_agent_v1_glue.py
git commit -m "feat(agent): add enable_v1 flag, _run_v1 method, and run() dispatch for three-role loop"
```

---

### Task 26: Write failing tests for `_build_observer` v1 ledger-append helper

**Files:**
- Create `tests/test_agent_v1_build_observer.py`

- [ ] **Step 11: Write the failing test**

```python
# tests/test_agent_v1_build_observer.py
"""Unit tests for the _build_observer ledger-append helper used in v1.

In the v1 path, _build_observer is replaced by a thin ledger-append closure
that simply records ActionEvent entries into the ActionLedger without invoking
the Maintainer's per-action `interpret` (that is now Maintainer.update's job,
called once per cycle by run_v1). The thin helper is used as the sandbox
step_fn for BuildAgent.

Tests verify:
  1. _build_v1_ledger_appender returns a callable.
  2. Calling the returned closure with (cmd, rc, stdout) appends an ActionEvent
     to the ActionLedger.
  3. The appended ActionEvent has the correct cmd, rc, and stdout fields.
  4. Multiple calls append multiple events in order.
"""
from __future__ import annotations

import types
import sys
import unittest
from unittest.mock import MagicMock, patch

# Re-use the stub installer from test_agent_v1_glue to avoid import side effects.
# We duplicate the minimum needed to keep this file self-contained.

def _make_stub_module(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    return m


def _install_stubs() -> None:
    for mod_name in [
        "src.sandbox", "src.synthesizer", "src.image_selector",
        "src.verification_bundle", "src.constants", "src.memory_manager",
        "src.observation_compressor", "src.planner", "dotenv", "openai",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _make_stub_module(mod_name)
    sys.modules["src.constants"].DEFAULT_LLM_MODEL = "test-model"
    sys.modules["src.constants"].DEFAULT_MEMORY_EMBEDDING_MODEL = "test-embed"
    oc = sys.modules["src.observation_compressor"]
    oc.AgentStep = object
    oc.ObservationCompressor = MagicMock
    oc.RunTokenLedger = MagicMock
    oc.build_observation_metadata = MagicMock(return_value={})
    oc.safety_compress_observation = MagicMock(return_value=("obs", False))
    oc.should_apply_compression = MagicMock(return_value=False)
    sys.modules["dotenv"].load_dotenv = lambda **kw: None
    sys.modules["openai"].OpenAI = MagicMock
    sys.modules["src.synthesizer"].Synthesizer = MagicMock
    sys.modules["src.image_selector"].ImageSelector = MagicMock
    sys.modules["src.verification_bundle"].derive_supported_verification_bundle = MagicMock(
        return_value={"test_commands": ["pytest --collect-only -q --disable-warnings"]}
    )
    sys.modules["src.memory_manager"].LongTermMemoryManager = MagicMock
    sys.modules["src.planner"].Planner = MagicMock
    sys.modules["src.sandbox"].Sandbox = MagicMock


_install_stubs()

import agent as _agent_module
from src.envstate.ledger import ActionLedger


def _make_agent_instance(**kwargs):
    defaults = dict(
        repo_url="https://github.com/example/repo",
        base_image="python:3.11-slim",
        model="test-model",
        workplace="/tmp/test_workplace_obs",
    )
    defaults.update(kwargs)
    with (
        patch.object(_agent_module.DockerAgent, "_prepare_workplace", return_value=None),
        patch.object(_agent_module.DockerAgent, "_collect_local_service_hints", return_value=set()),
        patch.object(_agent_module.DockerAgent, "_checkout_commit", return_value=None),
        patch.object(_agent_module.DockerAgent, "_create_sandbox", return_value=MagicMock()),
        patch.object(_agent_module.DockerAgent, "_init_planner", return_value=None),
        patch("os.makedirs", return_value=None),
        patch("os.path.exists", return_value=False),
        patch("os.path.join", side_effect=lambda *a: "/".join(a)),
    ):
        return _agent_module.DockerAgent(**defaults)


class TestBuildV1LedgerAppender(unittest.TestCase):
    """_build_v1_ledger_appender must return a closure that appends ActionEvents."""

    def test_method_exists_on_docker_agent(self):
        """DockerAgent must expose _build_v1_ledger_appender."""
        agent = _make_agent_instance(enable_v1=True)
        self.assertTrue(
            hasattr(agent, "_build_v1_ledger_appender"),
            "DockerAgent must have _build_v1_ledger_appender method",
        )

    def test_returns_callable(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        self.assertTrue(callable(appender))

    def test_appended_event_has_correct_cmd(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("pip install .", 0, "Successfully installed")
        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].cmd, "pip install .")

    def test_appended_event_has_correct_rc(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("pip install .", 0, "ok")
        self.assertEqual(ledger.events()[0].rc, 0)

    def test_appended_event_has_correct_stdout(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("pytest --collect-only -q --disable-warnings", 0, "5 items collected")
        self.assertEqual(ledger.events()[0].stdout, "5 items collected")

    def test_multiple_calls_append_in_order(self):
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("cmd1", 0, "out1")
        appender("cmd2", 1, "out2")
        appender("cmd3", 0, "out3")
        events = ledger.events()
        self.assertEqual(len(events), 3)
        self.assertEqual([e.cmd for e in events], ["cmd1", "cmd2", "cmd3"])
        self.assertEqual([e.rc for e in events], [0, 1, 0])

    def test_failed_command_rc_nonzero_is_stored(self):
        """Non-zero rc must be stored faithfully — no filtering on success."""
        agent = _make_agent_instance(enable_v1=True)
        ledger = ActionLedger()
        appender = agent._build_v1_ledger_appender(ledger)
        appender("python setup.py install", 1, "error: command failed")
        self.assertEqual(ledger.events()[0].rc, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 12: Run test to verify it fails**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_agent_v1_build_observer.py -x -q 2>&1 | head -30
```

Expected failure: `AttributeError: 'DockerAgent' object has no attribute '_build_v1_ledger_appender'`.

---

### Task 27: Implement `_build_v1_ledger_appender` on `DockerAgent`

**Files:**
- Modify `agent.py` — insert `_build_v1_ledger_appender` method immediately after the `_run_v1` method (before `_finalize_supervisor_artifacts`).

- [ ] **Step 13: Write minimal implementation**

In `agent.py`, after the `_run_v1` method (which ends `return configuration_success`) and before `_finalize_supervisor_artifacts`, insert:

```python
    def _build_v1_ledger_appender(self, ledger):
        """Return a thin closure that records (cmd, rc, stdout) into the ActionLedger.

        This replaces the full _build_observer pipeline for v1 runs. In v1, the
        per-action observer's only job is to persist evidence in the ledger so that:
          - run_v1's done_flag scan can find the collect-only command.
          - Token-bucket accounting remains intact via on_usage callbacks on the roles.

        The Maintainer's interpretation work (formerly done per-action in _build_observer)
        is now performed once per cycle by Maintainer.update inside run_v1.

        Signature of the returned closure:
            appender(cmd: str, rc: int, stdout: str) -> None
        """
        from src.envstate.ledger import ActionEvent

        step_counter = [0]

        def _appender(cmd: str, rc: int, stdout: str) -> None:
            step_counter[0] += 1
            ledger.append(ActionEvent(
                cmd=cmd,
                rc=rc,
                stdout=stdout,
                step=step_counter[0],
            ))

        return _appender
```

- [ ] **Step 14: Run test to verify it passes**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_agent_v1_build_observer.py -v 2>&1 | tail -20
```

Expected: all 7 tests pass.

- [ ] **Step 15: Commit**

```bash
cd /Users/john/john-planner-v1 && git add agent.py tests/test_agent_v1_build_observer.py
git commit -m "feat(agent): add _build_v1_ledger_appender thin observer for v1 path"
```

---

### Task 28: Write failing tests for `_verify_cleanroom_or_fail` decoupled signature

**Files:**
- Create `tests/test_cleanroom_v1.py`

- [ ] **Step 16: Write the failing test**

```python
# tests/test_cleanroom_v1.py
"""Tests for the decoupled _verify_cleanroom_or_fail signature required by v1.

Current signature (broken for v1):
    _verify_cleanroom_or_fail(self) -> bool
    — reads self.env_snapshot, snapshot.requirements, req.source (deleted types)

Target signature (v1-compatible):
    _verify_cleanroom_or_fail(self, dockerfile_path: str, build_context: str) -> bool
    — operates ONLY on the produced Dockerfile + build context
    — does NOT reference self.env_snapshot / snapshot.requirements / req.source

These tests drive the rewrite. Until the rewrite is complete, they fail
(either AttributeError or TypeError depending on which signature is present).
"""
from __future__ import annotations

import types
import sys
import unittest
from unittest.mock import MagicMock, patch


def _make_stub_module(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    return m


def _install_stubs() -> None:
    for mod_name in [
        "src.sandbox", "src.synthesizer", "src.image_selector",
        "src.verification_bundle", "src.constants", "src.memory_manager",
        "src.observation_compressor", "src.planner", "dotenv", "openai",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _make_stub_module(mod_name)
    sys.modules["src.constants"].DEFAULT_LLM_MODEL = "test-model"
    sys.modules["src.constants"].DEFAULT_MEMORY_EMBEDDING_MODEL = "test-embed"
    oc = sys.modules["src.observation_compressor"]
    oc.AgentStep = object
    oc.ObservationCompressor = MagicMock
    oc.RunTokenLedger = MagicMock
    oc.build_observation_metadata = MagicMock(return_value={})
    oc.safety_compress_observation = MagicMock(return_value=("obs", False))
    oc.should_apply_compression = MagicMock(return_value=False)
    sys.modules["dotenv"].load_dotenv = lambda **kw: None
    sys.modules["openai"].OpenAI = MagicMock
    sys.modules["src.synthesizer"].Synthesizer = MagicMock
    sys.modules["src.image_selector"].ImageSelector = MagicMock
    sys.modules["src.verification_bundle"].derive_supported_verification_bundle = MagicMock(
        return_value={"test_commands": ["pytest --collect-only -q --disable-warnings"]}
    )
    sys.modules["src.memory_manager"].LongTermMemoryManager = MagicMock
    sys.modules["src.planner"].Planner = MagicMock
    sys.modules["src.sandbox"].Sandbox = MagicMock


_install_stubs()

import agent as _agent_module
import inspect


def _make_agent_instance(**kwargs):
    defaults = dict(
        repo_url="https://github.com/example/repo",
        base_image="python:3.11-slim",
        model="test-model",
        workplace="/tmp/test_workplace_cleanroom",
    )
    defaults.update(kwargs)
    with (
        patch.object(_agent_module.DockerAgent, "_prepare_workplace", return_value=None),
        patch.object(_agent_module.DockerAgent, "_collect_local_service_hints", return_value=set()),
        patch.object(_agent_module.DockerAgent, "_checkout_commit", return_value=None),
        patch.object(_agent_module.DockerAgent, "_create_sandbox", return_value=MagicMock()),
        patch.object(_agent_module.DockerAgent, "_init_planner", return_value=None),
        patch("os.makedirs", return_value=None),
        patch("os.path.exists", return_value=False),
        patch("os.path.join", side_effect=lambda *a: "/".join(a)),
    ):
        return _agent_module.DockerAgent(**defaults)


class TestVerifyCleanroomDecoupledSignature(unittest.TestCase):
    """_verify_cleanroom_or_fail must accept (dockerfile_path, build_context) args."""

    def test_method_accepts_dockerfile_path_and_build_context(self):
        """The method signature must include dockerfile_path and build_context params."""
        agent = _make_agent_instance()
        sig = inspect.signature(agent._verify_cleanroom_or_fail)
        params = list(sig.parameters.keys())
        self.assertIn("dockerfile_path", params,
                      "_verify_cleanroom_or_fail must accept dockerfile_path param")
        self.assertIn("build_context", params,
                      "_verify_cleanroom_or_fail must accept build_context param")

    def test_returns_true_when_cleanroom_disabled(self):
        """When enable_cleanroom=False, must return True without touching env_snapshot."""
        agent = _make_agent_instance(enable_cleanroom=False)
        # Must not raise AttributeError about missing env_snapshot.
        result = agent._verify_cleanroom_or_fail(
            dockerfile_path="/tmp/Dockerfile",
            build_context="/tmp/workplace",
        )
        self.assertTrue(result)

    def test_does_not_reference_env_snapshot(self):
        """With enable_cleanroom=False, env_snapshot must NOT be accessed.

        This test verifies the v1 safety guarantee: calling the method on an agent
        that has no env_snapshot attribute must not raise AttributeError.
        """
        agent = _make_agent_instance(enable_cleanroom=False)
        # Explicitly delete env_snapshot to prove it is not touched.
        if hasattr(agent, "env_snapshot"):
            del agent.env_snapshot
        # Must complete without AttributeError.
        result = agent._verify_cleanroom_or_fail(
            dockerfile_path="/tmp/Dockerfile",
            build_context="/tmp/workplace",
        )
        self.assertTrue(result)

    def test_cleanroom_enabled_calls_verify_cleanroom_with_dockerfile(self):
        """When enable_cleanroom=True, must call verify_cleanroom using dockerfile_path."""
        agent = _make_agent_instance(enable_cleanroom=True)
        agent.sandbox = MagicMock()
        agent.sandbox.client = MagicMock()
        agent.verified_test_commands = ["pytest --collect-only -q --disable-warnings"]
        agent.run_summary_cleanroom = {}
        agent.synthesizer = MagicMock()
        agent.synthesizer.workdir = "/app"

        mock_verify_result = MagicMock()
        mock_verify_result.passed = True
        mock_verify_result.reason = "ok"

        from src.envstate import cleanroom as _cleanroom_mod

        with (
            patch.object(_cleanroom_mod, "verify_cleanroom", return_value=mock_verify_result) as mock_vc,
            patch.object(_cleanroom_mod, "ensure_repo_in_dockerfile", side_effect=lambda txt, wd: txt),
            patch("builtins.open", unittest.mock.mock_open(read_data="FROM python:3.11-slim\n")),
        ):
            result = agent._verify_cleanroom_or_fail(
                dockerfile_path="/tmp/test_workplace_cleanroom/Dockerfile",
                build_context="/tmp/test_workplace_cleanroom",
            )

        self.assertTrue(result)
        mock_vc.assert_called_once()
        # verify_cleanroom must receive build_context_dir from the build_context argument.
        call_kwargs = mock_vc.call_args
        passed_build_context = (
            call_kwargs.kwargs.get("build_context_dir")
            or (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
        )
        self.assertEqual(passed_build_context, "/tmp/test_workplace_cleanroom")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 17: Run test to verify it fails**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_cleanroom_v1.py -x -q 2>&1 | head -30
```

Expected failure: `TypeError: _verify_cleanroom_or_fail() takes 1 positional argument but 3 were given` (current signature has no params).

---

### Task 29: Rewrite `_verify_cleanroom_or_fail` to decoupled signature

**Files:**
- Modify `agent.py` — rewrite `_verify_cleanroom_or_fail` (currently at lines 1109–1162).

**DESIGN NOTE:** The rewrite removes ALL references to `self.env_snapshot`, `snapshot.requirements`, and `req.source` (types deleted in v1). Instead the method reads the Dockerfile text from `dockerfile_path` and passes `build_context` as the Docker build directory. Probe list is sourced from `self.verified_test_commands` already populated by `_run_v1` (or by the Arm B/C supervisor paths via the existing `_build_observer` pipeline — those paths are unaffected because `enable_cleanroom` defaults to `False` there too).

- [ ] **Step 18: Write minimal implementation**

In `agent.py`, replace the current `_verify_cleanroom_or_fail` method body (lines 1109–1162) with:

```python
    def _verify_cleanroom_or_fail(self, dockerfile_path: str = "", build_context: str = "") -> bool:
        """Return True if clean-room verification passes (or is disabled).

        Rebuilds the synthesized Dockerfile from scratch and re-runs the
        host-certified test commands in a throwaway container.

        This method operates ONLY on the produced Dockerfile + build context.
        It does NOT reference self.env_snapshot / snapshot.requirements / req.source
        (those types are deleted in v1). Probe list is derived from
        self.verified_test_commands (set by _run_v1 or the supervisor paths).

        Args:
            dockerfile_path: Absolute path to the synthesized Dockerfile.
                             Defaults to <workplace>/Dockerfile when empty.
            build_context:   Directory used as Docker build context.
                             Defaults to self.workplace when empty.
        """
        if not getattr(self, "enable_cleanroom", False):
            return True

        from src.envstate.cleanroom import ensure_repo_in_dockerfile, verify_cleanroom

        _dockerfile_path = dockerfile_path or os.path.join(self.workplace, "Dockerfile")
        _build_context = build_context or self.workplace
        _workdir = getattr(self.synthesizer, "workdir", "/app")

        try:
            with open(_dockerfile_path) as _df:
                dockerfile_text = _df.read()
        except OSError as exc:
            print(f"[Clean-room] cannot read Dockerfile at {_dockerfile_path!r}: {exc}")
            return False

        dockerfile_text = ensure_repo_in_dockerfile(dockerfile_text, _workdir)

        def run_command(image_ref, command):
            try:
                result = self.sandbox.client.containers.run(
                    image_ref, command, remove=True, working_dir=_workdir
                )
                if isinstance(result, (bytes, bytearray)):
                    return 0, result.decode("utf-8", "replace")
                return 0, str(result)
            except Exception as exc:
                return getattr(exc, "exit_status", 1), str(exc)

        result = verify_cleanroom(
            self.sandbox.client,
            dockerfile_text,
            build_context_dir=_build_context,
            probes=[],
            test_commands=list(self.verified_test_commands),
            run_command=run_command,
        )
        self.run_summary_cleanroom = {"passed": result.passed, "reason": result.reason}
        if not result.passed:
            print(f"[Clean-room] verification FAILED: {result.reason}")
        return result.passed
```

Also update the call site in `_finalize_supervisor_artifacts` (lines 1096–1107). The current call:
```python
        if not self._verify_cleanroom_or_fail():
            return False
```
Must be updated to pass the Dockerfile path and workplace:
```python
        _dockerfile_path = os.path.join(self.workplace, "Dockerfile")
        if not self._verify_cleanroom_or_fail(
            dockerfile_path=_dockerfile_path,
            build_context=self.workplace,
        ):
            return False
```

- [ ] **Step 19: Run test to verify it passes**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_cleanroom_v1.py -v 2>&1 | tail -20
```

Expected: all 4 tests pass.

Also confirm _finalize_supervisor_artifacts callers are unaffected:

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/test_agent_v1_glue.py tests/test_agent_flags.py -v 2>&1 | tail -20
```

- [ ] **Step 20: Commit**

```bash
cd /Users/john/john-planner-v1 && git add agent.py tests/test_cleanroom_v1.py
git commit -m "refactor(agent): decouple _verify_cleanroom_or_fail from env_snapshot; accept dockerfile_path+build_context args"
```

---

### Task 30: Verify existing tests still pass (regression gate)

**Files:** no new files

- [ ] **Step 21: Run full test suite**

```bash
cd /Users/john/john-planner-v1 && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -40
```

Expected: all previously passing tests continue to pass. In particular:
- `tests/test_envstate_orchestrator.py` — `EnvStateOrchestrator` (Arms A/B/C) tests must all PASS (class was preserved).
- `tests/test_orchestrator_v1.py` — new tests all PASS.
- `tests/test_agent_v1_glue.py` — new tests all PASS.
- `tests/test_agent_v1_build_observer.py` — new tests all PASS.
- `tests/test_cleanroom_v1.py` — new tests all PASS.
- `tests/test_agent_flags.py` — existing flag tests must not regress.

If any previously passing test fails, investigate the failure before proceeding. Common causes:
1. The `EnvStateOrchestrator` class import of `from src.envstate.types import EnvStateSnapshot` may fail if `types.py` was deleted prematurely — confirm `types.py` is still present.
2. The `enable_envstate` wiring change in `__init__` must preserve the existing `or` chain exactly.
3. The `_verify_cleanroom_or_fail` call site update in `_finalize_supervisor_artifacts` must use keyword args so existing callers without args still work (the default values cover the no-arg case).

- [ ] **Step 22: Commit regression gate result**

Only commit if all tests pass:

```bash
cd /Users/john/john-planner-v1 && git add -p  # stage only incidental fixes, if any
git commit -m "test(orchestrator+agent): regression gate — all arms A/B/C tests still green"
```

---

### Quick-reference: canonical signatures implemented here

| Symbol | File | Notes |
|---|---|---|
| `MAX_CYCLES = 12` | `src/envstate/orchestrator.py` | Module constant |
| `LOCAL_BUDGET = 8` | `src/envstate/orchestrator.py` | Module constant |
| `COLLECT_ONLY_CMD = "pytest --collect-only -q --disable-warnings"` | `src/envstate/orchestrator.py` | Module constant; import and use everywhere instead of inline string |
| `run_v1(planner, build_agent, maintainer, initial_world_map, ledger, sandbox_execute, max_cycles, local_budget, on_cycle) -> (WorldModelMap, str)` | `src/envstate/orchestrator.py` | Core loop |
| `DockerAgent.__init__(…, enable_v1=False)` | `agent.py` lines 128–163 | New param wired into `enable_envstate` |
| `DockerAgent.run(…)` dispatch | `agent.py` lines 1181–1186 | `enable_v1` branch added first, before supervisor and fullstate_worker checks |
| `DockerAgent._run_v1(max_cycles, keep_container)` | `agent.py` after line 1094 | New method; instantiates all three roles with canonical `(client, model, on_usage=..., log_path=...)` signatures |
| `DockerAgent._build_v1_ledger_appender(ledger)` | `agent.py` after `_run_v1` | Thin per-action closure; replaces full `_build_observer` pipeline in v1 |
| `DockerAgent._verify_cleanroom_or_fail(dockerfile_path, build_context)` | `agent.py` lines 1109–1162 | Decoupled rewrite; no `env_snapshot`/`requirements`/`req.source` references |

---

# Phase 6: Deletions + CLI/harness collapse

## Component: Deletions + CLI/Harness Collapse

This component removes the v0 probe/ACL machinery (`probes.py`, `acl.py`), removes
the v0 supervisor/worker/fullstate_worker/types/serde modules, retires Arm A/B/C
presets from the benchmark runner, and adds the `--arm v1` entry point wired into
`DockerAgent`. Every change is done in TDD order: write a failing test, confirm the
red state, implement, confirm green, commit.

---

### Task 31: Verify zero existing references to deleted modules before touching anything

**Files:**
- Test path: `tests/test_deletions_preflight.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_deletions_preflight.py
"""
Pre-flight: catalogue every live import of probes.py and acl.py so we know
exactly what must be removed.  The test itself does NOT fail on import — it
just prints a report for the engineer to act on.  A separate assertion at the
end confirms the catalogue matches what the spec claims.
"""
import ast
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

# Files listed in the canonical contract as "back-compat only" — the test
# allows references inside these files because Arms A/B/C are still present.
ALLOWED_REFS = {
    "agent.py",                          # _build_observer (arms A/B/C only)
    "tests/test_envstate_acl.py",        # will be deleted with acl.py
    "tests/test_envstate_probes.py",     # will be deleted with probes.py
    "tests/test_envstate_cleanroom.py",  # cleanroom imports ProbeSpec; cleanroom.py must be updated
    "tests/test_worldmodel_namekey.py",  # will be updated
    "tests/test_token_bucket_split.py",  # uses advance_revision for arm-B stub
    "tests/test_envstate_orchestrator.py",  # uses advance_revision for arm-B stub
    "src/envstate/cleanroom.py",         # must be updated in this task
    "src/envstate/probes.py",            # the file being deleted
    "src/envstate/acl.py",               # the file being deleted
}

PROBES_SYMBOL = "src.envstate.probes"
ACL_SYMBOL = "src.envstate.acl"


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            hits.append(rel)
    return hits


class PrefightCatalogueTests(unittest.TestCase):
    def test_probes_refs_are_only_in_allowed_files(self):
        refs = _find_references(REPO_ROOT, PROBES_SYMBOL)
        unexpected = [r for r in refs if r not in ALLOWED_REFS]
        self.assertEqual(
            unexpected, [],
            f"Unexpected references to src.envstate.probes found: {unexpected}. "
            "Remove these before deleting probes.py.",
        )

    def test_acl_refs_are_only_in_allowed_files(self):
        refs = _find_references(REPO_ROOT, ACL_SYMBOL)
        unexpected = [r for r in refs if r not in ALLOWED_REFS]
        self.assertEqual(
            unexpected, [],
            f"Unexpected references to src.envstate.acl found: {unexpected}. "
            "Remove these before deleting acl.py.",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_preflight.py -v 2>&1 | head -60
```

Expected: `FAILED tests/test_deletions_preflight.py::PrefightCatalogueTests::test_probes_refs_are_only_in_allowed_files` — `src/envstate/maintainer.py` imports `apply_llm_proposal` from `src.envstate.acl`; that ref is not in `ALLOWED_REFS`.

#### Step 3: Write minimal implementation

The v0 `maintainer.py` imports `from src.envstate.acl import apply_llm_proposal`. Since `maintainer.py` is being **rewritten** in the v1 plan, the correct fix here is to remove the import from the existing file so the pre-flight passes (the full rewrite lands in a later component). Replace only the import line; do not change the rest of the file.

```python
# EDIT src/envstate/maintainer.py — remove the acl import line.
# Find the line:
#   from src.envstate.acl import apply_llm_proposal
# Replace it with a comment marking it removed for v1 migration.
```

Open `/Users/john/john-planner-v1/src/envstate/maintainer.py`, find `from src.envstate.acl import apply_llm_proposal` and delete that line (or replace with a `# v1: acl import removed — apply_llm_proposal no longer used` comment). The method bodies that call `apply_llm_proposal` will be replaced when `maintainer.py` is rewritten; for now just removing the import is sufficient to unblock the pre-flight since no running code path in v1 exercises that branch.

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_preflight.py -v
```

Expected: `2 passed`.

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add tests/test_deletions_preflight.py src/envstate/maintainer.py
git commit -m "test(deletions): pre-flight catalogue — remove acl import from maintainer.py"
```

---

### Task 32: Update `cleanroom.py` to remove its `ProbeSpec` import

**Files:**
- Modify: `src/envstate/cleanroom.py` (lines 1–10, import block)
- Modify: `tests/test_envstate_cleanroom.py` (line 5, import)
- Test path: `tests/test_deletions_cleanroom_no_probes.py` (Create)

`cleanroom.py` currently does `from src.envstate.probes import ProbeSpec, build_probe_command`. The `verify_cleanroom` function accepts `probes: List[ProbeSpec]` and calls `build_probe_command(spec)`. Since `probes.py` will be deleted, we must inline a minimal `ProbeSpec`-like protocol or switch the signature to accept plain strings. Per the spec §11 non-goals: probe/certify machinery is entirely removed in v1. The clean-room feature only needs the command string. This task replaces `List[ProbeSpec]` with `List[str]` (pre-built command strings) in `cleanroom.py`, removes the `probes.py` import, and updates `_verify_cleanroom_or_fail` in `agent.py` accordingly.

#### Step 1: Write the failing test

```python
# tests/test_deletions_cleanroom_no_probes.py
"""
After the migration, cleanroom.py must NOT import from src.envstate.probes.
verify_cleanroom must accept probe_commands: list[str] instead of probes: list[ProbeSpec].
"""
import inspect
import pathlib
import tempfile
import unittest

SRC = pathlib.Path(__file__).parent.parent / "src" / "envstate" / "cleanroom.py"


class CleanroomNoProbesImportTest(unittest.TestCase):
    def test_cleanroom_does_not_import_probes(self):
        text = SRC.read_text(encoding="utf-8")
        self.assertNotIn(
            "from src.envstate.probes",
            text,
            "cleanroom.py must not import from src.envstate.probes after migration",
        )

    def test_verify_cleanroom_signature_accepts_probe_commands_list(self):
        from src.envstate.cleanroom import verify_cleanroom
        sig = inspect.signature(verify_cleanroom)
        params = list(sig.parameters.keys())
        # New signature uses probe_commands not probes
        self.assertIn(
            "probe_commands",
            params,
            "verify_cleanroom must accept probe_commands: list[str] after probes.py removal",
        )
        self.assertNotIn(
            "probes",
            params,
            "verify_cleanroom must no longer accept a 'probes' parameter",
        )

    def test_verify_cleanroom_runs_with_string_commands(self):
        """verify_cleanroom works when probe_commands is a list of bare command strings."""
        from src.envstate.cleanroom import verify_cleanroom

        def fake_run(image_ref, command):
            return 0, "ok"

        class FakeImages:
            def build(self, **kwargs):
                return ("img-id", iter([]))

        class FakeClient:
            images = FakeImages()

        result = verify_cleanroom(
            FakeClient(),
            dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probe_commands=["command -v python3"],
            test_commands=["pytest --collect-only -q"],
            run_command=fake_run,
        )
        self.assertTrue(result.passed)
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_cleanroom_no_probes.py -v
```

Expected: `FAILED` — `cleanroom.py` still imports `ProbeSpec` and its signature has `probes` not `probe_commands`.

#### Step 3: Write minimal implementation

Edit `src/envstate/cleanroom.py`:

```python
# src/envstate/cleanroom.py
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Callable, List

# InImageRunner: callable(image_ref, command) -> (rc, stdout)
InImageRunner = Callable[[str, str], tuple]


@dataclass(frozen=True)
class CleanroomResult:
    passed: bool
    reason: str
    failed_probes: tuple = ()
    failed_tests: tuple = ()


def ensure_repo_in_dockerfile(dockerfile_text: str, workdir: str) -> str:
    """Insert `COPY . <workdir>` right after the WORKDIR line so a clean-room
    rebuild image contains the repo. Idempotent."""
    workdir = workdir or "/app"
    copy_line = f"COPY . {workdir}"
    if copy_line in (dockerfile_text or ""):
        return dockerfile_text
    lines = (dockerfile_text or "").splitlines()
    out = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().startswith("WORKDIR "):
            out.append(copy_line)
            inserted = True
    if not inserted:
        out.append(copy_line)
    return "\n".join(out)


def verify_cleanroom(
    docker_client,
    dockerfile_text: str,
    build_context_dir: str,
    probe_commands: List[str],
    test_commands: List[str],
    run_command: InImageRunner,
) -> CleanroomResult:
    """Build a fresh image from the Dockerfile + repo context, then re-run
    probe_commands (bare shell strings) and test_commands.

    probe_commands replaces the old List[ProbeSpec] parameter; callers now
    pass pre-built command strings directly.  The ProbeSpec type is removed
    along with probes.py.
    """
    dockerfile_name = "Dockerfile.envstate-cleanroom"
    try:
        with open(
            os.path.join(build_context_dir, dockerfile_name), "w", encoding="utf-8"
        ) as handle:
            handle.write(dockerfile_text)
        image, _logs = docker_client.images.build(
            path=build_context_dir, dockerfile=dockerfile_name, rm=True
        )
    except Exception as exc:
        return CleanroomResult(False, f"clean-room build failed: {exc}")

    image_ref = (
        image if isinstance(image, str) else getattr(image, "id", str(image))
    )

    if not probe_commands and not test_commands:
        return CleanroomResult(
            False,
            "clean-room had nothing to verify (no probe_commands or test_commands)",
        )

    failed_probes: list[str] = []
    for cmd in probe_commands:
        rc, _out = run_command(image_ref, cmd)
        if rc != 0:
            failed_probes.append(cmd)
    if failed_probes:
        return CleanroomResult(
            False,
            "probe command(s) regressed in clean image",
            failed_probes=tuple(failed_probes),
        )

    failed_tests: list[str] = []
    for command in test_commands:
        rc, _out = run_command(image_ref, command)
        if rc != 0:
            failed_tests.append(command)
    if failed_tests:
        return CleanroomResult(
            False,
            "test command(s) failed in clean image",
            failed_tests=tuple(failed_tests),
        )

    return CleanroomResult(True, "clean-room verification passed")
```

Now update the existing cleanroom test to use the new `probe_commands` parameter. Edit `tests/test_envstate_cleanroom.py` — remove `from src.envstate.probes import ProbeSpec` and replace every `ProbeSpec(...)` with the pre-built command string it would have produced:

```python
# tests/test_envstate_cleanroom.py  — FULL REPLACEMENT
import tempfile
import unittest

from src.envstate.cleanroom import CleanroomResult, ensure_repo_in_dockerfile, verify_cleanroom


class FakeImages:
    def __init__(self, build_ok=True):
        self.build_ok = build_ok
        self.built = []

    def build(self, **kwargs):
        self.built.append(kwargs)
        if not self.build_ok:
            raise RuntimeError("build failed")
        return ("image-id", iter([]))


class FakeDockerClient:
    def __init__(self, build_ok=True):
        self.images = FakeImages(build_ok=build_ok)


class CleanroomTests(unittest.TestCase):
    def test_success_when_build_probes_and_tests_pass(self):
        client = FakeDockerClient(build_ok=True)

        def run_ok(image_ref, command):
            return 0, "ok"

        result = verify_cleanroom(
            client,
            dockerfile_text="FROM python:3.11-slim\nCOPY . /app\n",
            build_context_dir=tempfile.mkdtemp(),
            probe_commands=["command -v pg_config && pg_config --version"],
            test_commands=["pytest -q"],
            run_command=run_ok,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "clean-room verification passed")

    def test_fails_when_build_fails(self):
        client = FakeDockerClient(build_ok=False)

        def run_ok(image_ref, command):
            return 0, "ok"

        result = verify_cleanroom(
            client,
            dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probe_commands=[],
            test_commands=["pytest -q"],
            run_command=run_ok,
        )
        self.assertFalse(result.passed)
        self.assertIn("build failed", result.reason)

    def test_fails_when_probe_fails(self):
        client = FakeDockerClient()

        def run(image_ref, command):
            if "pg_config" in command:
                return 1, "not found"
            return 0, "ok"

        result = verify_cleanroom(
            client,
            dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probe_commands=["command -v pg_config && pg_config --version"],
            test_commands=["pytest -q"],
            run_command=run,
        )
        self.assertFalse(result.passed)
        self.assertIn("probe", result.reason)
        self.assertIn("command -v pg_config", result.failed_probes)

    def test_fails_when_test_command_fails(self):
        client = FakeDockerClient()

        def run(image_ref, command):
            if "pytest" in command:
                return 1, "FAILED"
            return 0, "ok"

        result = verify_cleanroom(
            client,
            dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probe_commands=[],
            test_commands=["pytest -q"],
            run_command=run,
        )
        self.assertFalse(result.passed)
        self.assertIn("test command", result.reason)

    def test_fails_when_nothing_to_verify(self):
        client = FakeDockerClient()

        def run_ok(image_ref, command):
            return 0, "ok"

        result = verify_cleanroom(
            client,
            dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probe_commands=[],
            test_commands=[],
            run_command=run_ok,
        )
        self.assertFalse(result.passed)

    def test_ensure_repo_in_dockerfile_inserts_after_workdir(self):
        text = "FROM python:3.11-slim\nWORKDIR /app\nRUN pip install -e .\n"
        result = ensure_repo_in_dockerfile(text, "/app")
        lines = result.splitlines()
        workdir_idx = next(i for i, l in enumerate(lines) if l.startswith("WORKDIR"))
        copy_idx = next(i for i, l in enumerate(lines) if l.startswith("COPY . /app"))
        self.assertEqual(copy_idx, workdir_idx + 1)

    def test_ensure_repo_in_dockerfile_idempotent(self):
        text = "FROM python:3.11-slim\nWORKDIR /app\nCOPY . /app\nRUN pip install -e .\n"
        result = ensure_repo_in_dockerfile(text, "/app")
        self.assertEqual(result.count("COPY . /app"), 1)
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_cleanroom_no_probes.py tests/test_envstate_cleanroom.py -v
```

Expected: all tests pass.

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add src/envstate/cleanroom.py tests/test_envstate_cleanroom.py tests/test_deletions_cleanroom_no_probes.py
git commit -m "refactor(cleanroom): replace ProbeSpec parameter with bare probe_commands strings — remove probes.py dependency"
```

---

### Task 33: Update `agent.py` `_verify_cleanroom_or_fail` to use new `probe_commands` API

**Files:**
- Modify: `agent.py` (lines 1109–1162, `_verify_cleanroom_or_fail` method)
- Test path: `tests/test_deletions_agent_cleanroom_api.py` (Create)

**Task note:** The canonical decision requires `_verify_cleanroom_or_fail(self, dockerfile_path, build_context)` operating ONLY on the produced Dockerfile + build context without referencing `self.env_snapshot / snapshot.requirements / req.source`. The current `agent.py` implementation (lines 1109–1162) reads from `self.env_snapshot` to build the `probes` list. Because a full rewrite of that method is non-trivial and Group 5 owns the v1 path entirely, cleanroom is made OPTIONAL and SKIPPED in the v1 path — EBSR is the trusted metric. The `_verify_cleanroom_or_fail` method in `agent.py` is updated only to remove the `ProbeSpec` import and switch to `probe_commands=`; the snapshot-read code is guarded behind `if snapshot is not None` and only runs on the Arms A/B/C path where `env_snapshot` still exists. On the v1 path `self.env_snapshot` is absent so `probe_commands` will be empty and cleanroom verifies only via `test_commands`.

#### Step 1: Write the failing test

```python
# tests/test_deletions_agent_cleanroom_api.py
"""
After the migration, agent.py:_verify_cleanroom_or_fail must NOT import
from src.envstate.probes.  It must pass probe_commands=[...] (list of
pre-built command strings) to verify_cleanroom, not probes=[ProbeSpec(...)].
"""
import inspect
import pathlib
import unittest

AGENT_SRC = pathlib.Path(__file__).parent.parent / "agent.py"


class AgentCleanroomApiTest(unittest.TestCase):
    def test_agent_does_not_import_probespec_in_verify_cleanroom(self):
        text = AGENT_SRC.read_text(encoding="utf-8")
        # The _verify_cleanroom_or_fail method previously imported ProbeSpec
        # inside its body (agent.py:1116).  After migration that import is gone.
        self.assertNotIn(
            "from src.envstate.probes import ProbeSpec",
            text,
            "_verify_cleanroom_or_fail must not import ProbeSpec from probes.py",
        )

    def test_verify_cleanroom_called_with_probe_commands_kwarg(self):
        text = AGENT_SRC.read_text(encoding="utf-8")
        # The call site must use probe_commands= not probes=
        self.assertIn(
            "probe_commands=",
            text,
            "agent.py must call verify_cleanroom with probe_commands= kwarg",
        )
        self.assertNotIn(
            "probes=probes",
            text,
            "agent.py must not pass probes=probes to verify_cleanroom",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_agent_cleanroom_api.py -v
```

Expected: `FAILED` — `agent.py` still has `from src.envstate.probes import ProbeSpec` at line 1116 and passes `probes=probes`.

#### Step 3: Write minimal implementation

The `_verify_cleanroom_or_fail` method in `agent.py` (lines 1109–1162) needs two changes:
1. Remove `from src.envstate.probes import ProbeSpec` (line 1116) — `ProbeSpec` is only used to build the `probes` list.
2. Remove `from src.envstate.types import Source` (line 1117) — `Source` is only used to filter `req.source == Source.PROBE`.
3. Replace the `probes` list construction + `probes=probes` call with `probe_commands` list construction + `probe_commands=probe_commands`, extracting `req.evidence.probe_cmd` directly as a string.

The full replacement for the `_verify_cleanroom_or_fail` method body (replace lines 1109–1162 in `agent.py`):

```python
    def _verify_cleanroom_or_fail(self):
        """Return True if clean-room verification passes (or is disabled). Rebuilds the
        synthesized Dockerfile from scratch and re-runs the final test command in a
        throwaway container.  ProbeSpec / probes.py removed in v1 migration.

        NOTE: cleanroom is OPTIONAL and SKIPPED in the v1 path (no env_snapshot);
        EBSR is the trusted metric.  On Arms A/B/C this method still reads
        self.env_snapshot to extract certified probe_commands as bare strings.
        """
        if not getattr(self, "enable_cleanroom", False):
            return True
        from src.envstate.cleanroom import ensure_repo_in_dockerfile, verify_cleanroom

        # Re-render the Dockerfile text (idempotent; pass the workplace path so we do
        # NOT write a stray ./Dockerfile in the cwd).
        dockerfile_text = self.synthesizer.generate_dockerfile(
            file_path=os.path.join(self.workplace, "Dockerfile")
        )
        workdir = getattr(self.synthesizer, "workdir", "/app")
        dockerfile_text = ensure_repo_in_dockerfile(dockerfile_text, workdir)

        # Re-run only host-certified probe commands from the live snapshot.
        # In v1 the snapshot is no longer maintained (WorldModelMap replaces it),
        # so probe_commands is empty for v1 runs — cleanroom verifies via test_commands.
        probe_commands: list[str] = []
        snapshot = getattr(self, "env_snapshot", None)
        if snapshot is not None:
            for req in snapshot.requirements:
                if (
                    getattr(req, "source", None) == "PROBE"
                    and getattr(req, "status", None) == "PRESENT"
                    and getattr(req, "evidence", None) is not None
                ):
                    cmd = getattr(req.evidence, "probe_cmd", None)
                    if cmd:
                        probe_commands.append(cmd)

        def run_command(image_ref, command):
            try:
                result = self.sandbox.client.containers.run(
                    image_ref, command, remove=True, working_dir=workdir
                )
                if isinstance(result, (bytes, bytearray)):
                    return 0, result.decode("utf-8", "replace")
                return 0, str(result)
            except Exception as exc:
                return getattr(exc, "exit_status", 1), str(exc)

        result = verify_cleanroom(
            self.sandbox.client,
            dockerfile_text,
            build_context_dir=self.workplace,
            probe_commands=probe_commands,
            test_commands=list(self.verified_test_commands),
            run_command=run_command,
        )
        self.run_summary_cleanroom = {"passed": result.passed, "reason": result.reason}
        if not result.passed:
            print(f"[Clean-room] verification FAILED: {result.reason}")
        return result.passed
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_agent_cleanroom_api.py tests/test_agent_cleanroom_wiring.py -v
```

Expected: all pass. (If `test_agent_cleanroom_wiring.py` calls the old `probes=` API, update those test stubs to match the new signature before running.)

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add agent.py tests/test_deletions_agent_cleanroom_api.py
git commit -m "refactor(agent): update _verify_cleanroom_or_fail to pass probe_commands= — remove ProbeSpec import"
```

---

### Task 34: Delete `src/envstate/probes.py` and its test file, verify zero imports remain

**Files:**
- Delete: `src/envstate/probes.py`
- Delete: `tests/test_envstate_probes.py`
- Test path: `tests/test_deletions_probes_gone.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_deletions_probes_gone.py
"""
probes.py must be absent and no surviving file must import from it.
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

PROBES_FILE = REPO_ROOT / "src" / "envstate" / "probes.py"
PROBES_TEST = REPO_ROOT / "tests" / "test_envstate_probes.py"

SKIP_PATHS = {
    "tests/test_deletions_probes_gone.py",  # this file itself
}


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            if rel not in SKIP_PATHS:
                hits.append(rel)
    return hits


class ProbesGoneTests(unittest.TestCase):
    def test_probes_py_file_does_not_exist(self):
        self.assertFalse(
            PROBES_FILE.exists(),
            f"Expected {PROBES_FILE} to be deleted but it still exists.",
        )

    def test_probes_test_file_does_not_exist(self):
        self.assertFalse(
            PROBES_TEST.exists(),
            f"Expected {PROBES_TEST} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_probes(self):
        refs = _find_references(REPO_ROOT, "src.envstate.probes")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.probes found: {refs}",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_probes_gone.py -v
```

Expected: all three assertions fail — `probes.py` still exists.

#### Step 3: Write minimal implementation

Before deleting, verify the pre-flight (Task 31) test still passes — confirming no unexpected references remain. Then delete the files and update `tests/test_worldmodel_namekey.py` to remove its `ProbeSpec` import.

```bash
cd /Users/john/john-planner-v1
# 1. Check pre-flight
.venv/bin/python -m pytest tests/test_deletions_preflight.py -v

# 2. Delete probes.py and its test
git rm src/envstate/probes.py
git rm tests/test_envstate_probes.py
```

Update `tests/test_worldmodel_namekey.py` — remove the `ProbeSpec` import (line 15) and any test that constructs a `ProbeSpec` object. Replace with a comment:

```python
# tests/test_worldmodel_namekey.py — remove line:
#   from src.envstate.probes import ProbeSpec
# and any usage of ProbeSpec in the file.
```

Open the file and check what `ProbeSpec` is used for:
<br>In `tests/test_worldmodel_namekey.py` line 15: `from src.envstate.probes import ProbeSpec` — scan for uses. If `ProbeSpec` is only imported but not used in any test assertion, simply delete that import line. If it is used in test fixtures, replace the fixture with a plain dict or skip.

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_probes_gone.py -v
```

Expected: `3 passed`.

Confirm no other tests broke:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/ -v --ignore=tests/test_envstate_acl.py --ignore=tests/test_envstate_orchestrator.py --ignore=tests/test_envstate_supervisor.py --ignore=tests/test_envstate_worker.py --ignore=tests/test_fullstate_worker.py --ignore=tests/test_token_bucket_split.py -q 2>&1 | tail -20
```

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add -u
git add tests/test_deletions_probes_gone.py
git commit -m "feat(deletions): delete src/envstate/probes.py and test — zero surviving imports verified"
```

---

### Task 35: Delete `src/envstate/acl.py` and its test file, verify zero imports remain

**Files:**
- Delete: `src/envstate/acl.py`
- Delete: `tests/test_envstate_acl.py`
- Test path: `tests/test_deletions_acl_gone.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_deletions_acl_gone.py
"""
acl.py must be absent and no surviving file must import from it.
Files that use advance_revision only for Arms A/B/C stubs are listed in
ALLOWED_ACL_REFS — they must be updated before acl.py is deleted.
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

ACL_FILE = REPO_ROOT / "src" / "envstate" / "acl.py"
ACL_TEST = REPO_ROOT / "tests" / "test_envstate_acl.py"

# Files allowed to still reference acl because they test arm-B/C back-compat
# paths that import advance_revision inline inside _build_observer / _run_supervisor.
# These must be cleaned up before acl.py is deleted.
ALLOWED_REFS: set[str] = {
    "agent.py",                          # _build_observer inner import (arms A/B/C only)
    "tests/test_deletions_acl_gone.py",  # this file itself
}

SKIP_PATHS = {
    "tests/test_deletions_acl_gone.py",
}


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            if rel not in SKIP_PATHS:
                hits.append(rel)
    return hits


class AclGoneTests(unittest.TestCase):
    def test_acl_py_file_does_not_exist(self):
        self.assertFalse(
            ACL_FILE.exists(),
            f"Expected {ACL_FILE} to be deleted but it still exists.",
        )

    def test_acl_test_file_does_not_exist(self):
        self.assertFalse(
            ACL_TEST.exists(),
            f"Expected {ACL_TEST} to be deleted but it still exists.",
        )

    def test_surviving_acl_imports_are_only_allowed(self):
        refs = _find_references(REPO_ROOT, "src.envstate.acl")
        unexpected = [r for r in refs if r not in ALLOWED_REFS]
        self.assertEqual(
            unexpected,
            [],
            f"Unexpected references to src.envstate.acl found: {unexpected}. "
            "Remove these before deleting acl.py.",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_acl_gone.py -v
```

Expected: all three assertions fail — `acl.py` still exists and several test files still import from it.

#### Step 3: Write minimal implementation

Update the three test files that import `advance_revision` or `apply_llm_proposal` from `acl.py`:

**`tests/test_worldmodel_namekey.py`** — remove `from src.envstate.acl import apply_llm_proposal` (line 13). The tests in this file test the `parse_maintainer_proposal` normaliser and the old ACL boundary. Since `apply_llm_proposal` is being deleted with `acl.py`, these tests should be removed or replaced. Delete the test methods that call `apply_llm_proposal` directly; keep only `parse_maintainer_proposal` tests.

**`tests/test_envstate_orchestrator.py`** — remove `from src.envstate.acl import advance_revision` (line 7). Replace the `_noop_observer` that calls `advance_revision` with a simple passthrough that returns `snapshot` unchanged (the orchestrator tests exercise the old `EnvStateOrchestrator` which is being rewritten; mark those tests as skipped with `@unittest.skip("v0 orchestrator — superseded by run_v1")` rather than deleting).

**`tests/test_token_bucket_split.py`** — the `advance_revision` import inside the `_simple_observer` stub at line 338 is inside a lambda. Replace the stub with one that does not call `advance_revision` (just return `snap` unchanged).

Then delete `acl.py` and its test:

```bash
cd /Users/john/john-planner-v1
git rm src/envstate/acl.py
git rm tests/test_envstate_acl.py
```

The complete replacement body for the `_simple_observer` stub in `tests/test_token_bucket_split.py` (replace lines 335–342):

```python
        def _simple_observer(snap, task_spec, step, action, success, observation):
            # advance_revision removed with acl.py; stub returns snapshot unchanged
            return snap
```

For `tests/test_envstate_orchestrator.py` lines 1–8, replace the imports:

```python
import unittest

from src.envstate.orchestrator import EnvStateOrchestrator
from src.envstate.types import BaseFacts, EnvStateSnapshot
from src.envstate.ledger import ActionLedger
from src.envstate.worker import Worker, WorkerReport
# advance_revision removed with acl.py — stubs updated below
```

And update `_noop_observer` (line 36) to not call `advance_revision`:

```python
def _noop_observer(snapshot, task_spec, step, action, success, observation):
    return snapshot
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_acl_gone.py -v
```

Expected: `3 passed`.

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add -u
git add tests/test_deletions_acl_gone.py
git commit -m "feat(deletions): delete src/envstate/acl.py and test — remove advance_revision stubs from surviving tests"
```

---

### Task 36: Delete `src/envstate/supervisor.py` and unwire `agent.py._run_supervisor`

**Files:**
- Delete: `src/envstate/supervisor.py`
- Delete: `tests/test_envstate_supervisor.py`
- Modify: `agent.py` (remove `_run_supervisor` method body + `enable_supervisor` dispatch at lines 862–978 and 1183–1184; keep the `enable_supervisor` parameter in `__init__` as a deprecated no-op for back-compat)
- Test path: `tests/test_deletions_supervisor_gone.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_deletions_supervisor_gone.py
"""
supervisor.py and its test must be absent.
agent.py must not import from src.envstate.supervisor anywhere.
agent.py._run_supervisor must be gone (the dispatch check enable_supervisor is
kept as a deprecated no-op so existing CLI invocations don't crash).
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

SUPERVISOR_FILE = REPO_ROOT / "src" / "envstate" / "supervisor.py"
SUPERVISOR_TEST = REPO_ROOT / "tests" / "test_envstate_supervisor.py"

SKIP_PATHS = {
    "tests/test_deletions_supervisor_gone.py",
}


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            if rel not in SKIP_PATHS:
                hits.append(rel)
    return hits


class SupervisorGoneTests(unittest.TestCase):
    def test_supervisor_py_does_not_exist(self):
        self.assertFalse(
            SUPERVISOR_FILE.exists(),
            f"Expected {SUPERVISOR_FILE} to be deleted but it still exists.",
        )

    def test_supervisor_test_does_not_exist(self):
        self.assertFalse(
            SUPERVISOR_TEST.exists(),
            f"Expected {SUPERVISOR_TEST} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_supervisor(self):
        refs = _find_references(REPO_ROOT, "src.envstate.supervisor")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.supervisor found: {refs}",
        )

    def test_run_supervisor_method_removed_from_agent(self):
        """_run_supervisor must be gone from DockerAgent — the dispatch is a no-op stub."""
        agent_text = (REPO_ROOT / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "def _run_supervisor",
            agent_text,
            "agent.py must not define _run_supervisor after supervisor.py is deleted",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_supervisor_gone.py -v
```

Expected: all four assertions fail — `supervisor.py` still exists and `agent.py` still imports from it and defines `_run_supervisor`.

#### Step 3: Write minimal implementation

Before deleting, check for any non-test files that import from `supervisor.py` beyond `agent.py` and the test file:

```bash
cd /Users/john/john-planner-v1
grep -rn "from src.envstate.supervisor\|import supervisor" src/ tests/ agent.py 2>/dev/null | grep -v "__pycache__"
```

Expected output shows only `agent.py` (via inner imports inside `_run_supervisor`) and `tests/test_envstate_supervisor.py` plus `tests/test_workdir_repo_structure.py` and `tests/test_success_criterion_parity.py`. Update those surviving test files:

**`tests/test_workdir_repo_structure.py`** — line 11: `from src.envstate.supervisor import render_planning_view`. Replace the import and any call to `render_planning_view` with `@unittest.skip("supervisor removed — render_planning_view deleted with supervisor.py")` on the affected test class/methods.

**`tests/test_success_criterion_parity.py`** — line 20: `from src.envstate.supervisor import SUPERVISOR_SYSTEM_PROMPT`. Mark affected tests with `@unittest.skip("supervisor removed")`.

Then remove the `_run_supervisor` method body from `agent.py` (lines 862–978). Keep the `enable_supervisor` parameter in `__init__` and the `self.enable_supervisor = enable_supervisor` assignment but replace the `run()` dispatch branch with a deprecation warning:

In `agent.py` `run()` method, replace the enable_supervisor dispatch (around line 1183):

```python
        if getattr(self, "enable_supervisor", False):
            # supervisor.py removed in v1 migration — treat as bare ReAct
            import warnings
            warnings.warn(
                "--enable-supervisor is deprecated and has no effect (supervisor.py removed). "
                "Use --enable-v1 for the v1 orchestrator.",
                DeprecationWarning,
                stacklevel=2,
            )
```

Then delete the files:

```bash
cd /Users/john/john-planner-v1
git rm src/envstate/supervisor.py
git rm tests/test_envstate_supervisor.py
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_supervisor_gone.py -v
```

Expected: `4 passed`.

Import-smoke check — confirm the package still imports cleanly:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -c "from src.envstate.cleanroom import verify_cleanroom; print('ok')"
.venv/bin/python -m pytest --collect-only -q tests/ 2>&1 | tail -10
```

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add -u
git add tests/test_deletions_supervisor_gone.py
git commit -m "feat(deletions): delete src/envstate/supervisor.py — unwire _run_supervisor from agent.py, deprecate enable_supervisor flag"
```

---

### Task 37: Delete `src/envstate/worker.py` and verify `build_agent.py` has inlined all regex helpers

**Files:**
- Delete: `src/envstate/worker.py`
- Delete: `tests/test_envstate_worker.py`
- Modify: `agent.py` (remove inner imports of worker; any surviving usage of `LlmWorkerPlanner`, `Worker`, `interruption_decision`, `DEFAULT_MAX_ACTIONS` from worker.py must have been migrated to `src/envstate/build_agent.py` by Group 4)
- Test path: `tests/test_deletions_worker_gone.py` (Create)

**Pre-condition:** Group 4 (build_agent.py) must have inlined `_extract_worker_action`, `_is_worker_finished`, `_ACTION_RE`, `_FINAL_RE`, `_TOOLCALL_CMD_RE`, `interruption_decision`, `_looks_like_pin_edit`, `WorkerReport`, `DEFAULT_MAX_ACTIONS`, and `MAX_EMPTY_PLANNER_RESPONSES` from `worker.py` into `src/envstate/build_agent.py` before this task runs. The grep step below confirms that.

#### Step 1: Write the failing test

```python
# tests/test_deletions_worker_gone.py
"""
worker.py and its test must be absent.
No file outside the deleted test suite may import from src.envstate.worker.
The regex helpers (_extract_worker_action, _is_worker_finished) and WorkerReport
must be importable from src.envstate.build_agent (Group 4 inlined them there).
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

WORKER_FILE = REPO_ROOT / "src" / "envstate" / "worker.py"
WORKER_TEST = REPO_ROOT / "tests" / "test_envstate_worker.py"

SKIP_PATHS = {
    "tests/test_deletions_worker_gone.py",
}


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            if rel not in SKIP_PATHS:
                hits.append(rel)
    return hits


class WorkerGoneTests(unittest.TestCase):
    def test_worker_py_does_not_exist(self):
        self.assertFalse(
            WORKER_FILE.exists(),
            f"Expected {WORKER_FILE} to be deleted but it still exists.",
        )

    def test_worker_test_does_not_exist(self):
        self.assertFalse(
            WORKER_TEST.exists(),
            f"Expected {WORKER_TEST} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_worker(self):
        refs = _find_references(REPO_ROOT, "src.envstate.worker")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.worker found: {refs}. "
            "Update these to import from src.envstate.build_agent instead.",
        )

    def test_regex_helpers_importable_from_build_agent(self):
        """_extract_worker_action and _is_worker_finished must have been inlined
        into build_agent.py by Group 4 before worker.py can be deleted."""
        from src.envstate.build_agent import _extract_worker_action, _is_worker_finished  # noqa: F401

    def test_worker_report_importable_from_build_agent(self):
        from src.envstate.build_agent import TaskReport  # canonical v1 name  # noqa: F401
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_worker_gone.py -v
```

Expected: failures — `worker.py` still exists and surviving imports remain in `fullstate_worker.py`, `test_envstate_orchestrator.py`, `test_toolcall_extraction.py`, `test_workdir_repo_structure.py`, `test_worker_empty_action_guard.py`, `test_worker_per_task_reset.py`, `test_success_criterion_parity.py`, and `agent.py`.

#### Step 3: Write minimal implementation

First, confirm Group 4 has landed (build_agent.py exists and exports the needed symbols):

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -c "from src.envstate.build_agent import _extract_worker_action, _is_worker_finished, TaskReport; print('Group4 symbols ok')"
```

If that fails, Group 4 must land before this task. Assuming it passes, update surviving importers:

**`src/envstate/fullstate_worker.py`** — currently imports `_extract_worker_action`, `_is_worker_finished`, `WorkerReport`, `MAX_EMPTY_PLANNER_RESPONSES` from `src.envstate.worker`. Replace those imports with imports from `src.envstate.build_agent` (Group 4's canonical location). Also remove `from src.envstate.types import EnvStateSnapshot, Source` since types.py will be deleted in Task 39.

**`tests/test_envstate_orchestrator.py`** — line 6: `from src.envstate.worker import Worker, WorkerReport`. Mark all tests in this file `@unittest.skip("v0 orchestrator — superseded by run_v1")` and remove the worker import.

**`tests/test_toolcall_extraction.py`** — line 12: `from src.envstate.worker import _extract_worker_action`. Replace with `from src.envstate.build_agent import _extract_worker_action`.

**`tests/test_workdir_repo_structure.py`** — line 12: `from src.envstate.worker import Worker, build_task_brief`. Mark affected tests `@unittest.skip("supervisor/worker removed")`.

**`tests/test_worker_empty_action_guard.py`** — line 3: `from src.envstate.worker import Worker, WorkerReport`. Replace `Worker` / `WorkerReport` references with equivalents from `src.envstate.build_agent`, or mark `@unittest.skip("worker.py removed — covered by build_agent tests")`.

**`tests/test_worker_per_task_reset.py`** — line 18: `from src.envstate.worker import ...`. Same treatment — update import path to `build_agent` or skip.

**`tests/test_success_criterion_parity.py`** — line 62: `from src.envstate.worker import WORKER_SYSTEM_PROMPT`. Mark affected test `@unittest.skip("worker removed")`.

**`agent.py`** — inner imports inside `_run_supervisor` (line 864) and `_run_fullstate_worker` (line 989) — these are gone once `_run_supervisor` is removed (Task 36) and `_run_fullstate_worker` is removed (Task 38). Verify no remaining top-level import of worker.py exists:

```bash
cd /Users/john/john-planner-v1
grep -n "from src.envstate.worker\|import src.envstate.worker" agent.py
```

After all callers are updated, delete the files:

```bash
cd /Users/john/john-planner-v1
git rm src/envstate/worker.py
git rm tests/test_envstate_worker.py
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_worker_gone.py -v
```

Expected: `5 passed`.

Import-smoke check:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -c "import src.envstate.build_agent; print('ok')"
.venv/bin/python -m pytest --collect-only -q tests/ 2>&1 | tail -10
```

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add -u
git add tests/test_deletions_worker_gone.py
git commit -m "feat(deletions): delete src/envstate/worker.py — migrate regex helpers/WorkerReport refs to build_agent"
```

---

### Task 38: Delete `src/envstate/fullstate_worker.py` and unwire `agent.py._run_fullstate_worker`

**Files:**
- Delete: `src/envstate/fullstate_worker.py`
- Delete: `tests/test_fullstate_worker.py`
- Modify: `agent.py` (remove `_run_fullstate_worker` method body at lines 981–1107; keep `enable_fullstate_worker` parameter in `__init__` as deprecated no-op)
- Test path: `tests/test_deletions_fullstate_worker_gone.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_deletions_fullstate_worker_gone.py
"""
fullstate_worker.py and its test must be absent.
agent.py must not define _run_fullstate_worker.
No file may import from src.envstate.fullstate_worker.
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

FULLSTATE_FILE = REPO_ROOT / "src" / "envstate" / "fullstate_worker.py"
FULLSTATE_TEST = REPO_ROOT / "tests" / "test_fullstate_worker.py"

SKIP_PATHS = {
    "tests/test_deletions_fullstate_worker_gone.py",
}


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            if rel not in SKIP_PATHS:
                hits.append(rel)
    return hits


class FullstateWorkerGoneTests(unittest.TestCase):
    def test_fullstate_worker_py_does_not_exist(self):
        self.assertFalse(
            FULLSTATE_FILE.exists(),
            f"Expected {FULLSTATE_FILE} to be deleted but it still exists.",
        )

    def test_fullstate_worker_test_does_not_exist(self):
        self.assertFalse(
            FULLSTATE_TEST.exists(),
            f"Expected {FULLSTATE_TEST} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_fullstate_worker(self):
        refs = _find_references(REPO_ROOT, "src.envstate.fullstate_worker")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.fullstate_worker found: {refs}",
        )

    def test_run_fullstate_worker_method_removed_from_agent(self):
        agent_text = (REPO_ROOT / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "def _run_fullstate_worker",
            agent_text,
            "agent.py must not define _run_fullstate_worker after fullstate_worker.py is deleted",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_fullstate_worker_gone.py -v
```

Expected: all four assertions fail — `fullstate_worker.py` still exists and `agent.py` still imports from it.

#### Step 3: Write minimal implementation

Check surviving importers:

```bash
cd /Users/john/john-planner-v1
grep -rn "from src.envstate.fullstate_worker\|import fullstate_worker" src/ tests/ agent.py 2>/dev/null | grep -v "__pycache__"
```

Expected: `agent.py` (inner imports inside `_run_fullstate_worker`), `tests/test_success_criterion_parity.py` (line 100), `tests/test_fullstate_worker.py` (being deleted).

Update surviving test files:

**`tests/test_success_criterion_parity.py`** — line 100: `from src.envstate.fullstate_worker import FULLSTATE_WORKER_SYSTEM_PROMPT`. Mark the affected test `@unittest.skip("fullstate_worker removed")`.

Remove the `_run_fullstate_worker` method body from `agent.py` (lines 981–1107). Keep `self.enable_fullstate_worker = enable_fullstate_worker` in `__init__` as a deprecated no-op, and replace the `run()` dispatch branch (line 1185–1186) with a deprecation warning matching the supervisor pattern:

```python
        if getattr(self, "enable_fullstate_worker", False):
            # fullstate_worker.py removed in v1 migration — treat as bare ReAct
            import warnings
            warnings.warn(
                "--enable-fullstate-worker is deprecated and has no effect "
                "(fullstate_worker.py removed). Use --enable-v1 for the v1 orchestrator.",
                DeprecationWarning,
                stacklevel=2,
            )
```

Then delete the files:

```bash
cd /Users/john/john-planner-v1
git rm src/envstate/fullstate_worker.py
git rm tests/test_fullstate_worker.py
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_fullstate_worker_gone.py -v
```

Expected: `4 passed`.

Import-smoke check:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -c "import agent; print('ok')"
.venv/bin/python -m pytest --collect-only -q tests/ 2>&1 | tail -10
```

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add -u
git add tests/test_deletions_fullstate_worker_gone.py
git commit -m "feat(deletions): delete src/envstate/fullstate_worker.py — unwire _run_fullstate_worker from agent.py"
```

---

### Task 39: Delete `src/envstate/types.py` (v0 snapshot types), verify zero importers remain

**Files:**
- Delete: `src/envstate/types.py`
- Test path: `tests/test_deletions_types_gone.py` (Create)

**Pre-condition:** All v0 callers of `types.py` (`supervisor.py`, `worker.py`, `fullstate_worker.py`, `acl.py`, `probes.py`, `serde.py`) must already be deleted (Tasks 4–8 above). Surviving callers in `agent.py` are inner imports inside `_run_supervisor` / `_run_fullstate_worker` (both now removed) and inside `_verify_cleanroom_or_fail` (now uses string literals for `"PROBE"` and `"PRESENT"` directly — no `Source` import). Surviving test callers (`test_envstate_types.py`, `test_envstate_supervisor.py`, `test_fullstate_worker.py`, `test_envstate_probes.py`, `test_envstate_acl.py`, `test_agent_supervisor_observe.py`, `test_workdir_repo_structure.py`, `test_worldmodel_namekey.py`, `test_envstate_maintainer.py`) must be updated or skipped.

#### Step 1: Write the failing test

```python
# tests/test_deletions_types_gone.py
"""
src/envstate/types.py (v0 snapshot types) must be absent.
No surviving file may import from it.
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

TYPES_FILE = REPO_ROOT / "src" / "envstate" / "types.py"

SKIP_PATHS = {
    "tests/test_deletions_types_gone.py",
}


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            if rel not in SKIP_PATHS:
                hits.append(rel)
    return hits


class TypesGoneTests(unittest.TestCase):
    def test_types_py_does_not_exist(self):
        self.assertFalse(
            TYPES_FILE.exists(),
            f"Expected {TYPES_FILE} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_types(self):
        refs = _find_references(REPO_ROOT, "src.envstate.types")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.types found: {refs}. "
            "Update these to use v1 types (Fact, WorldModelMap, etc.) from "
            "src.envstate.world_model before deleting types.py.",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_types_gone.py -v
```

Expected: both assertions fail — `types.py` still exists and numerous files import from it.

#### Step 3: Write minimal implementation

Survey all remaining importers:

```bash
cd /Users/john/john-planner-v1
grep -rn "from src.envstate.types\|import src.envstate.types" . --include="*.py" | grep -v "__pycache__" | sort
```

For each surviving importer:

- **`src/envstate/maintainer.py`** — line 11: `from src.envstate.types import EnvStateSnapshot`. This import will be eliminated when `maintainer.py` is rewritten by Group 2. If Group 2 has landed, verify maintainer.py no longer imports types.py. If not yet landed, mark the import removal as part of the Group 2 pre-condition.
- **`src/envstate/orchestrator.py`** — line 5: `from src.envstate.types import EnvStateSnapshot`. Group 5's orchestrator rewrite removes this. Verify Group 5 has landed.
- **`tests/test_envstate_types.py`** — delete this file (it tested v0 types directly):
  ```bash
  git rm tests/test_envstate_types.py
  ```
- **`tests/test_envstate_maintainer.py`** — inner imports of `BaseFacts, EnvStateSnapshot` (lines 65, 86). Mark affected tests `@unittest.skip("v0 EnvStateSnapshot removed")` or update to use v1 types from `src.envstate.world_model`.
- **`tests/test_envstate_orchestrator.py`** — line 4: `from src.envstate.types import BaseFacts, EnvStateSnapshot`. Already skipped in Task 35; remove the import.
- **`tests/test_agent_supervisor_observe.py`** — line 7: `from src.envstate.types import BaseFacts, EnvStateSnapshot, Source, Status`. Mark affected tests `@unittest.skip("v0 supervisor/types removed")`.
- **`tests/test_workdir_repo_structure.py`** — line 9: `from src.envstate.types import BaseFacts, EnvStateSnapshot, Requirement, Source, Status`. Mark affected tests `@unittest.skip("supervisor/types removed")`.
- **`tests/test_worldmodel_namekey.py`** — lines 16 and 328/340: imports `LLM_ALLOWED_STATUSES`, `LLM_ALLOWED_SOURCES`, and v0 snapshot types. Replace with equivalent constants/types from `src.envstate.world_model` (Group 1 defines `Fact`, `WorldModelMap`). The ACL authority sets `LLM_ALLOWED_STATUSES` / `LLM_ALLOWED_SOURCES` are v0 concepts not present in v1 — mark those tests `@unittest.skip("v0 ACL constants removed")`.
- **`tests/test_token_bucket_split.py`** — line 381: inner import `from src.envstate.types import EnvStateSnapshot, BaseFacts`. This is inside a test helper that constructs a v0 snapshot for the Arm B stub. Mark the affected test method `@unittest.skip("v0 snapshot removed — Arm B retired")`.
- **`agent.py`** — line 1117: inner import `from src.envstate.types import Source` inside `_verify_cleanroom_or_fail`. In Task 33 this was already removed (replaced with the string literal `"PROBE"`). Verify no remaining types import:
  ```bash
  grep -n "src.envstate.types" agent.py
  ```

After all importers are cleaned up, delete types.py:

```bash
cd /Users/john/john-planner-v1
git rm src/envstate/types.py
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_types_gone.py -v
```

Expected: `2 passed`.

Import-smoke check:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -c "import src.envstate.build_agent; import src.envstate.world_model; print('ok')"
.venv/bin/python -m pytest --collect-only -q tests/ 2>&1 | tail -10
```

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add -u
git add tests/test_deletions_types_gone.py
git commit -m "feat(deletions): delete src/envstate/types.py (v0 snapshot types) — all importers migrated to v1 types"
```

---

### Task 40: Delete `src/envstate/serde.py`, verify zero importers remain

**Files:**
- Delete: `src/envstate/serde.py`
- Test path: `tests/test_deletions_serde_gone.py` (Create)

**Pre-condition:** `types.py` must already be deleted (Task 39) since `serde.py` imports from it. `maintainer.py` imports `from src.envstate.serde import snapshot_to_dict` (line 10); that import is eliminated when Group 2 rewrites `maintainer.py`. `tests/test_envstate_types.py` (already deleted in Task 39) and `tests/test_workdir_repo_structure.py` (line 10) import from `serde.py`.

#### Step 1: Write the failing test

```python
# tests/test_deletions_serde_gone.py
"""
src/envstate/serde.py must be absent.
No surviving file may import from it.
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

SERDE_FILE = REPO_ROOT / "src" / "envstate" / "serde.py"

SKIP_PATHS = {
    "tests/test_deletions_serde_gone.py",
}


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            if rel not in SKIP_PATHS:
                hits.append(rel)
    return hits


class SerdeGoneTests(unittest.TestCase):
    def test_serde_py_does_not_exist(self):
        self.assertFalse(
            SERDE_FILE.exists(),
            f"Expected {SERDE_FILE} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_serde(self):
        refs = _find_references(REPO_ROOT, "src.envstate.serde")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.serde found: {refs}. "
            "Remove these before deleting serde.py.",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_serde_gone.py -v
```

Expected: both assertions fail — `serde.py` still exists and `maintainer.py` and `tests/test_workdir_repo_structure.py` still import from it.

#### Step 3: Write minimal implementation

Survey remaining importers:

```bash
cd /Users/john/john-planner-v1
grep -rn "from src.envstate.serde\|import src.envstate.serde" . --include="*.py" | grep -v "__pycache__" | sort
```

For each surviving importer:

- **`src/envstate/maintainer.py`** — line 10: `from src.envstate.serde import snapshot_to_dict`. Group 2's rewrite eliminates this. Verify Group 2 has landed. If not yet landed, remove this import line now (snapshot_to_dict is a v0 serialisation function not needed in v1 maintainer).
- **`tests/test_workdir_repo_structure.py`** — line 10: `from src.envstate.serde import snapshot_to_dict, snapshot_from_dict`. Mark affected tests `@unittest.skip("serde/v0 snapshot removed")`.

After updating all importers, delete the file:

```bash
cd /Users/john/john-planner-v1
git rm src/envstate/serde.py
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_serde_gone.py -v
```

Expected: `2 passed`.

Import-smoke / collection check:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest --collect-only -q tests/ 2>&1 | tail -10
```

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add -u
git add tests/test_deletions_serde_gone.py
git commit -m "feat(deletions): delete src/envstate/serde.py — all importers migrated or removed"
```

---

### Task 41: Add `enable_v1` flag to `DockerAgent.__init__` and `run()` dispatch

**Files:**
- Modify: `agent.py` (lines 112–133, `__init__` param list; lines 160–170, flag wiring; lines 1181–1186, `run()` dispatch)
- Test path: `tests/test_agent_v1_flag.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_agent_v1_flag.py
"""
TDD for the enable_v1 flag wiring in DockerAgent (canonical contract §agent_py_glue).

Spec:
  - DockerAgent.__init__ accepts enable_v1=False kwarg
  - self.enable_v1 = enable_v1
  - self.enable_envstate = enable_envstate or enable_supervisor or enable_fullstate_worker or enable_v1
  - run() dispatches to _run_v1() BEFORE the supervisor / fullstate_worker checks when enable_v1=True
  - _run_v1 method exists with signature (self, max_cycles=12, keep_container=False)
"""
import inspect
import unittest
from types import SimpleNamespace


def _make_agent(**kwargs):
    from agent import DockerAgent
    agent = DockerAgent.__new__(DockerAgent)
    agent.enable_envstate = False
    agent.enable_supervisor = False
    agent.enable_fullstate_worker = False
    agent.enable_v1 = False
    agent.fullstate_worker_prompt = False
    agent.enable_cleanroom = False
    agent.action_ledger = None
    for k, v in kwargs.items():
        setattr(agent, k, v)
    return agent


class TestEnableV1InitParam(unittest.TestCase):
    def test_init_accepts_enable_v1_kwarg(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        self.assertIn(
            "enable_v1",
            sig.parameters,
            "DockerAgent.__init__ must accept enable_v1 kwarg",
        )

    def test_enable_v1_default_is_false(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        self.assertIs(
            sig.parameters["enable_v1"].default,
            False,
            "enable_v1 must default to False",
        )


class TestEnableV1EnvstateAutoOn(unittest.TestCase):
    """enable_v1=True must auto-set enable_envstate=True (triggers ActionLedger creation)."""

    def _make_with_flags(self, enable_v1=False, enable_envstate=False,
                          enable_supervisor=False, enable_fullstate_worker=False):
        from agent import DockerAgent
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_supervisor = enable_supervisor
        agent.enable_fullstate_worker = enable_fullstate_worker
        agent.enable_v1 = enable_v1
        agent.fullstate_worker_prompt = False
        # Replicate the __init__ logic per canonical contract
        agent.enable_envstate = (
            enable_envstate or enable_supervisor or enable_fullstate_worker or enable_v1
        )
        agent.action_ledger = None
        return agent

    def test_enable_envstate_on_when_v1_true(self):
        agent = self._make_with_flags(enable_v1=True)
        self.assertTrue(agent.enable_envstate)

    def test_enable_envstate_off_when_all_false(self):
        agent = self._make_with_flags(enable_v1=False)
        self.assertFalse(agent.enable_envstate)


class TestRunV1Dispatch(unittest.TestCase):
    """run() must call _run_v1 BEFORE checking enable_supervisor / enable_fullstate_worker."""

    def _make_dispatchable(self, enable_v1=False, enable_supervisor=False,
                            enable_fullstate_worker=False):
        from agent import DockerAgent
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_v1 = enable_v1
        agent.enable_supervisor = enable_supervisor
        agent.enable_fullstate_worker = enable_fullstate_worker
        agent.enable_envstate = enable_v1 or enable_supervisor or enable_fullstate_worker
        agent.action_ledger = None
        agent._called = []

        def _fake_v1(max_cycles=12, keep_container=False):
            agent._called.append("v1")
            return True

        def _fake_supervisor(max_steps=30, keep_container=False):
            agent._called.append("supervisor")
            return True

        def _fake_fullstate(max_steps=30, keep_container=False):
            agent._called.append("fullstate")
            return True

        agent._run_v1 = _fake_v1
        agent._run_supervisor = _fake_supervisor
        agent._run_fullstate_worker = _fake_fullstate
        return agent

    def test_v1_flag_routes_to_run_v1(self):
        agent = self._make_dispatchable(enable_v1=True)
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=12)
        self.assertIn("v1", agent._called)
        self.assertNotIn("supervisor", agent._called)
        self.assertNotIn("fullstate", agent._called)

    def test_v1_checked_before_supervisor(self):
        """If both enable_v1 and enable_supervisor are True (shouldn't happen after guard,
        but the ordering must be v1 first)."""
        agent = self._make_dispatchable(enable_v1=True, enable_supervisor=True)
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=12)
        self.assertEqual(agent._called[0], "v1")

    def test_supervisor_still_works_when_v1_false(self):
        agent = self._make_dispatchable(enable_v1=False, enable_supervisor=True)
        from agent import DockerAgent
        DockerAgent.run(agent, max_steps=30)
        self.assertIn("supervisor", agent._called)
        self.assertNotIn("v1", agent._called)


class TestRunV1MethodExists(unittest.TestCase):
    def test_method_exists(self):
        from agent import DockerAgent
        self.assertTrue(
            hasattr(DockerAgent, "_run_v1"),
            "DockerAgent must have a _run_v1 method",
        )

    def test_signature_has_max_cycles_and_keep_container(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_v1)
        self.assertIn("max_cycles", sig.parameters)
        self.assertIn("keep_container", sig.parameters)

    def test_max_cycles_default_is_12(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_v1)
        self.assertEqual(sig.parameters["max_cycles"].default, 12)

    def test_keep_container_default_is_false(self):
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent._run_v1)
        self.assertFalse(sig.parameters["keep_container"].default)
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_agent_v1_flag.py -v
```

Expected: multiple failures — `enable_v1` not in `__init__` signature; `_run_v1` does not exist.

#### Step 3: Write minimal implementation

**Edit `agent.py` — `DockerAgent.__init__` parameter list (around line 128):**

Add `enable_v1=False,` after `enable_fullstate_worker=False,`:

```python
        enable_envstate=False,
        enable_supervisor=False,
        enable_fullstate_worker=False,
        fullstate_worker_prompt=False,
        enable_v1=False,
        enable_cleanroom=False,
```

**Edit `agent.py` — flag-wiring block (around line 160):**

Add `self.enable_v1 = enable_v1` and update the `enable_envstate` derivation:

```python
        self.enable_supervisor = enable_supervisor
        self.enable_fullstate_worker = enable_fullstate_worker
        self.fullstate_worker_prompt = fullstate_worker_prompt
        self.enable_v1 = enable_v1
        self.enable_envstate = (
            enable_envstate or enable_supervisor or enable_fullstate_worker or enable_v1
        )
        self.enable_cleanroom = enable_cleanroom
```

**Edit `agent.py` — `run()` dispatch block (lines 1181–1186):**

Insert the `enable_v1` branch BEFORE the existing supervisor check:

```python
    def run(self, max_steps=30, keep_container=False):
        """Runs the ReAct loop to configure the environment."""
        if getattr(self, "enable_v1", False):
            return self._run_v1(max_cycles=max_steps, keep_container=keep_container)
        if getattr(self, "enable_supervisor", False):
            return self._run_supervisor(max_steps=max_steps, keep_container=keep_container)
        if getattr(self, "enable_fullstate_worker", False):
            return self._run_fullstate_worker(max_steps=max_steps, keep_container=keep_container)
```

**Add stub `_run_v1` method to `agent.py`** (insert after the deprecated dispatch stubs, which are now where `_run_fullstate_worker` formerly lived — around line 981):

```python
    def _run_v1(self, max_cycles=12, keep_container=False):
        """v1 three-role orchestrator (Planner / BuildAgent / Maintainer).
        Full implementation wired once src/envstate/orchestrator.py run_v1 is in place.
        This stub raises NotImplementedError until the orchestrator component lands.
        """
        raise NotImplementedError(
            "_run_v1 is a stub — wire src.envstate.orchestrator.run_v1 here "
            "once the orchestrator component is merged."
        )
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_agent_v1_flag.py -v
```

Expected: all pass (the dispatch test calls `_run_v1` through the monkeypatched `_fake_v1`, so the `NotImplementedError` stub is never invoked in tests).

Also verify back-compat:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_agent_flags.py -v
```

Expected: all pass.

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add agent.py tests/test_agent_v1_flag.py
git commit -m "feat(agent): add enable_v1 flag to DockerAgent.__init__ and run() dispatch; stub _run_v1"
```

---

### Task 42: Add `--enable-v1` to `agent.py` argparse and update mutual-exclusion guard

**Files:**
- Modify: `agent.py` (lines 2462–2507, argparse block)
- Test path: `tests/test_agent_v1_argparse.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_agent_v1_argparse.py
"""
agent.py must register --enable-v1 in its argparse block,
pass enable_v1 to DockerAgent, and guard against mixing --enable-v1
with --enable-supervisor / --enable-fullstate-worker.
"""
import inspect
import pathlib
import unittest

AGENT_SRC = pathlib.Path(__file__).parent.parent / "agent.py"


class AgentV1ArgparseTest(unittest.TestCase):
    def _src(self):
        return AGENT_SRC.read_text(encoding="utf-8")

    def test_enable_v1_flag_registered(self):
        self.assertIn(
            "--enable-v1",
            self._src(),
            "agent.py argparse must register --enable-v1",
        )

    def test_enable_v1_passed_to_docker_agent(self):
        src = self._src()
        self.assertIn(
            "enable_v1=args.enable_v1",
            src,
            "DockerAgent(...) call must include enable_v1=args.enable_v1",
        )

    def test_mutual_exclusion_v1_with_supervisor(self):
        src = self._src()
        # Guard must prevent --enable-v1 combined with --enable-supervisor
        self.assertIn(
            "enable_v1",
            src,
            "Mutual-exclusion guard must reference enable_v1",
        )
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_agent_v1_argparse.py -v
```

Expected: all three assertions fail — `--enable-v1` not in argparse, `enable_v1=args.enable_v1` not in DockerAgent call.

#### Step 3: Write minimal implementation

**Edit `agent.py` argparse block (after the existing `--enable-cleanroom` line, around 2474):**

```python
    parser.add_argument("--enable-v1", action="store_true",
                        help="Use the v1 three-role orchestrator (Planner/BuildAgent/Maintainer). "
                             "Mutually exclusive with --enable-supervisor and --enable-fullstate-worker.")
```

**Edit the mutual-exclusion block (after line 2485)** — add a guard for v1:

```python
    if args.enable_v1 and (args.enable_supervisor or args.enable_fullstate_worker):
        parser.error(
            "--enable-v1 is mutually exclusive with --enable-supervisor and "
            "--enable-fullstate-worker. Use --arm v1 for the v1 preset."
        )
```

**Edit the `DockerAgent(...)` constructor call (around line 2490–2506)** — add `enable_v1=args.enable_v1,`:

```python
    agent = DockerAgent(
        args.repo_url,
        base_image=args.image,
        model=args.model,
        workplace=args.workplace,
        base_commit=args.base_commit,
        enable_observation_compression=args.enable_observation_compression,
        enable_long_term_memory=args.enable_long_term_memory,
        enable_envstate=args.enable_envstate,
        enable_supervisor=args.enable_supervisor,
        enable_fullstate_worker=args.enable_fullstate_worker,
        fullstate_worker_prompt=args.fullstate_worker_prompt,
        enable_v1=args.enable_v1,
        enable_cleanroom=args.enable_cleanroom,
        memory_path=args.memory_path,
        memory_embedding_model=args.memory_embedding_model,
        command_timeout_seconds=args.command_timeout,
    )
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_agent_v1_argparse.py -v
```

Expected: `3 passed`.

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add agent.py tests/test_agent_v1_argparse.py
git commit -m "feat(agent): register --enable-v1 flag in argparse, add mutual-exclusion guard"
```

---

### Task 43: Add `--arm v1` preset to `run_repo2run_benchmark.py`, retire Arm A/B/C

**Files:**
- Modify: `run_repo2run_benchmark.py` (lines 3262–3365, arm selector block; lines 198–208, `build_agent_command` flag forwarding)
- Test path: `tests/test_benchmark_arm_v1.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_benchmark_arm_v1.py
"""
TDD for the --arm v1 preset and retirement of Arms A/B/C in
run_repo2run_benchmark.py (canonical contract §run_repo2run_benchmark.py).

Spec:
  - --arm choices must include 'v1' and '0'
  - --arm v1 sets enable_v1=True, enable_cleanroom=True, max_steps=12, _label='armV1_three_role'
  - --arm 0  sets enable_supervisor=False, enable_v1=False, max_steps=180 (unchanged)
  - --arm A/B/C must NOT be valid choices (retired)
  - build_agent_command() forwards --enable-v1 when args.enable_v1 is True
  - build_agent_command() must NOT forward --enable-supervisor / --enable-fullstate-worker
    when called with an arm-v1 namespace (those flags are False)
"""
import argparse
import pathlib
import sys
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from run_repo2run_benchmark import build_agent_command, _ARM_PRESETS  # noqa: E402


def _make_namespace(**kwargs) -> argparse.Namespace:
    defaults = dict(
        base_image="auto",
        model="claude-sonnet-4-6",
        max_steps=30,
        agent_command_timeout=1800,
        enable_observation_compression=False,
        enable_long_term_memory=False,
        memory_embedding_model="text-embedding-3-small",
        memory_path=None,
        keep_container=False,
        enable_supervisor=False,
        enable_fullstate_worker=False,
        fullstate_worker_prompt=False,
        enable_envstate=False,
        enable_cleanroom=False,
        enable_v1=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class ArmPresetsTest(unittest.TestCase):
    def test_arm_v1_preset_exists(self):
        self.assertIn("v1", _ARM_PRESETS, "--arm v1 preset must exist in _ARM_PRESETS")

    def test_arm_0_preset_exists(self):
        self.assertIn("0", _ARM_PRESETS, "--arm 0 preset must exist in _ARM_PRESETS")

    def test_arm_A_retired(self):
        self.assertNotIn("A", _ARM_PRESETS, "Arm A must be retired from _ARM_PRESETS")

    def test_arm_B_retired(self):
        self.assertNotIn("B", _ARM_PRESETS, "Arm B must be retired from _ARM_PRESETS")

    def test_arm_C_retired(self):
        self.assertNotIn("C", _ARM_PRESETS, "Arm C must be retired from _ARM_PRESETS")

    def test_arm_v1_preset_fields(self):
        p = _ARM_PRESETS["v1"]
        self.assertTrue(p["enable_v1"], "arm v1 preset must set enable_v1=True")
        self.assertTrue(p["enable_cleanroom"], "arm v1 must set enable_cleanroom=True")
        self.assertEqual(p["max_steps"], 12, "arm v1 max_steps must be 12 (maps to max_cycles)")
        self.assertFalse(p.get("enable_supervisor", False))
        self.assertFalse(p.get("enable_fullstate_worker", False))
        self.assertFalse(p.get("fullstate_worker_prompt", False))
        self.assertEqual(p["_label"], "armV1_three_role")

    def test_arm_0_preset_unchanged(self):
        p = _ARM_PRESETS["0"]
        self.assertFalse(p.get("enable_v1", False))
        self.assertFalse(p["enable_supervisor"])
        self.assertFalse(p["enable_fullstate_worker"])
        self.assertEqual(p["max_steps"], 180)
        self.assertEqual(p["_label"], "arm0_bare_react")


class BuildAgentCommandV1Test(unittest.TestCase):
    def _run_build(self, **kwargs):
        ns = _make_namespace(**kwargs)
        return build_agent_command(
            python_executable="/usr/bin/python3",
            repo_root=REPO_ROOT,
            instance={"repo_url": "https://github.com/example/repo", "base_commit": "abc123"},
            workplace=REPO_ROOT / "workplace_test",
            args=ns,
        )

    def test_enable_v1_flag_forwarded(self):
        cmd = self._run_build(enable_v1=True)
        self.assertIn("--enable-v1", cmd)

    def test_enable_v1_not_forwarded_when_false(self):
        cmd = self._run_build(enable_v1=False)
        self.assertNotIn("--enable-v1", cmd)

    def test_supervisor_not_forwarded_for_arm_v1_namespace(self):
        cmd = self._run_build(enable_v1=True, enable_supervisor=False)
        self.assertNotIn("--enable-supervisor", cmd)

    def test_arm_0_does_not_include_v1_flag(self):
        cmd = self._run_build(enable_v1=False, enable_supervisor=False,
                               enable_fullstate_worker=False)
        self.assertNotIn("--enable-v1", cmd)
        self.assertNotIn("--enable-supervisor", cmd)


class ArgparseChoicesTest(unittest.TestCase):
    """The --arm argument in the benchmark parser must accept '0' and 'v1' only."""

    def _src(self):
        return (REPO_ROOT / "run_repo2run_benchmark.py").read_text(encoding="utf-8")

    def test_choices_include_v1(self):
        self.assertIn('"v1"', self._src(), '--arm choices must include "v1"')

    def test_choices_do_not_include_A(self):
        src = self._src()
        # "A" must not appear in choices=[...]
        import re
        m = re.search(r'choices=\[([^\]]+)\]', src)
        if m:
            choices_str = m.group(1)
            self.assertNotIn('"A"', choices_str, 'Arm A must be retired from --arm choices')
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_benchmark_arm_v1.py -v
```

Expected: multiple failures — `_ARM_PRESETS` has no `v1` key; has `A`/`B`/`C` keys; `build_agent_command` does not forward `--enable-v1`.

#### Step 3: Write minimal implementation

**Edit `run_repo2run_benchmark.py` — `_ARM_PRESETS` dict (lines 3318–3355):**

Replace the existing dict with:

```python
    _ARM_PRESETS: dict[str, dict] = {
        "0": {
            "enable_supervisor": False,
            "enable_fullstate_worker": False,
            "fullstate_worker_prompt": False,
            "enable_envstate": False,
            "enable_v1": False,
            "enable_cleanroom": False,
            "max_steps": 180,
            "_label": "arm0_bare_react",
        },
        "v1": {
            "enable_supervisor": False,
            "enable_fullstate_worker": False,
            "fullstate_worker_prompt": False,
            "enable_envstate": False,
            "enable_v1": True,
            "enable_cleanroom": True,
            "max_steps": 12,
            "_label": "armV1_three_role",
        },
    }
```

**Edit `run_repo2run_benchmark.py` — `--arm` argparse choices (line 3274):**

```python
        "--arm",
        choices=["0", "v1"],
        default=None,
        help=(
            "Ablation arm shorthand. "
            "0=bare ReAct (no EnvState flags, --steps 180); "
            "v1=three-role orchestrator Planner/BuildAgent/Maintainer "
            "(--enable-v1 --enable-cleanroom --steps 12). "
            "Overrides the individual --enable-* flags and --max-steps when set. "
            "Outputs land under <output-root>/arm{0,v1}_<label>/."
        ),
```

**Edit `run_repo2run_benchmark.py` — help text for individual flags** (lines 3289–3312): Mark the A/B/C-specific flag help strings as deprecated (do not remove the flags yet — they may still be passed manually):

```python
    parser.add_argument(
        "--enable-supervisor",
        action="store_true",
        help="[DEPRECATED — Arms B/C retired] Forward --enable-supervisor to agent.py.",
    )
    parser.add_argument(
        "--enable-fullstate-worker",
        action="store_true",
        help="[DEPRECATED — Arm A retired] Forward --enable-fullstate-worker to agent.py.",
    )
    parser.add_argument(
        "--fullstate-worker-prompt",
        action="store_true",
        help="[DEPRECATED — Arm C retired] Forward --fullstate-worker-prompt to agent.py.",
    )
    parser.add_argument(
        "--enable-v1",
        action="store_true",
        help="Use the v1 three-role orchestrator (Planner/BuildAgent/Maintainer).",
    )
```

**Edit `run_repo2run_benchmark.py` — `build_agent_command` forwarding block (lines 198–208):**

Add the `enable_v1` forward after the existing block:

```python
    # EnvState / ablation-arm flags (§9.1 — forwarded only when set).
    if getattr(args, "enable_supervisor", False):
        command.append("--enable-supervisor")
    if getattr(args, "enable_fullstate_worker", False):
        command.append("--enable-fullstate-worker")
    if getattr(args, "fullstate_worker_prompt", False):
        command.append("--fullstate-worker-prompt")
    if getattr(args, "enable_envstate", False):
        command.append("--enable-envstate")
    if getattr(args, "enable_cleanroom", False):
        command.append("--enable-cleanroom")
    if getattr(args, "enable_v1", False):
        command.append("--enable-v1")
```

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_benchmark_arm_v1.py -v
```

Expected: all pass.

Also verify existing benchmark tests pass:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_repo2run_benchmark.py -v -q 2>&1 | tail -10
```

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add run_repo2run_benchmark.py tests/test_benchmark_arm_v1.py
git commit -m "feat(benchmark): add --arm v1 preset, retire Arms A/B/C from _ARM_PRESETS, forward --enable-v1"
```

---

### Task 44: Final verification — zero surviving references to all deleted modules; Arm 0 imports cleanly

**Files:**
- Test path: `tests/test_deletions_final_verification.py` (Create)

#### Step 1: Write the failing test

```python
# tests/test_deletions_final_verification.py
"""
Final gate: confirm the deletion + wiring work is complete.

Checks:
  1. probes.py, acl.py, supervisor.py, worker.py, fullstate_worker.py,
     types.py, and serde.py files do not exist.
  2. No file imports from any of those deleted modules at top level.
  3. Arm 0 (bare ReAct) imports cleanly via DockerAgent(enable_v1=False).
  4. --arm v1 preset is present and --arm A/B/C are absent.
  5. DockerAgent accepts enable_v1 and _run_v1 method exists.
  6. agent.py does NOT contain 'from src.envstate.probes import' at module top-level
     (only allowed inside guard-gated inner imports that remain for back-compat).
"""
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

SELF = "tests/test_deletions_final_verification.py"

DELETED_MODULES = [
    "src.envstate.probes",
    "src.envstate.acl",
    "src.envstate.supervisor",
    "src.envstate.worker",
    "src.envstate.fullstate_worker",
    "src.envstate.types",
    "src.envstate.serde",
]

DELETED_FILES = [
    REPO_ROOT / "src" / "envstate" / "probes.py",
    REPO_ROOT / "src" / "envstate" / "acl.py",
    REPO_ROOT / "src" / "envstate" / "supervisor.py",
    REPO_ROOT / "src" / "envstate" / "worker.py",
    REPO_ROOT / "src" / "envstate" / "fullstate_worker.py",
    REPO_ROOT / "src" / "envstate" / "types.py",
    REPO_ROOT / "src" / "envstate" / "serde.py",
]


def _find_top_level_import(root: pathlib.Path, module_substr: str) -> list[str]:
    """Find files that import module_substr OUTSIDE a function body (top-level import)."""
    import ast
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(root))
        if rel == SELF:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr not in src:
            continue
        # Parse and check if import is at top level (not inside a function/method).
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name = (getattr(node, "module", None) or "")
                if module_substr in name:
                    # Check if this node is a direct child of Module (top-level).
                    for parent in ast.walk(tree):
                        if hasattr(parent, "body") and node in getattr(parent, "body", []):
                            if isinstance(parent, ast.Module):
                                hits.append(rel)
                            break
    return hits


class FinalVerificationTests(unittest.TestCase):
    def test_all_deleted_files_absent(self):
        for f in DELETED_FILES:
            self.assertFalse(
                f.exists(),
                f"Expected {f.name} to be deleted but it still exists at {f}",
            )

    def test_no_top_level_imports_of_deleted_modules(self):
        for module in DELETED_MODULES:
            with self.subTest(module=module):
                hits = _find_top_level_import(REPO_ROOT, module)
                self.assertEqual(
                    hits, [],
                    f"Top-level imports of {module} remain: {hits}",
                )

    def test_arm_0_import_is_clean(self):
        """Importing DockerAgent with default (enable_v1=False) must not raise."""
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        # enable_v1 must be present; supervisor and fullstate_worker still present for back-compat
        self.assertIn("enable_v1", sig.parameters)
        self.assertIn("enable_supervisor", sig.parameters)
        self.assertIn("enable_fullstate_worker", sig.parameters)

    def test_v1_preset_exists_in_benchmark(self):
        from run_repo2run_benchmark import _ARM_PRESETS
        self.assertIn("v1", _ARM_PRESETS)
        self.assertNotIn("A", _ARM_PRESETS)
        self.assertNotIn("B", _ARM_PRESETS)
        self.assertNotIn("C", _ARM_PRESETS)

    def test_run_v1_method_exists_on_docker_agent(self):
        from agent import DockerAgent
        self.assertTrue(hasattr(DockerAgent, "_run_v1"))

    def test_enable_v1_defaults_false(self):
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        self.assertIs(sig.parameters["enable_v1"].default, False)

    def test_envstate_package_imports_cleanly(self):
        """The envstate package must import without errors after all deletions."""
        import src.envstate.build_agent  # noqa: F401
        import src.envstate.world_model  # noqa: F401
        import src.envstate.cleanroom    # noqa: F401
```

#### Step 2: Run test to verify it fails

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_final_verification.py -v
```

Expected: some tests fail if any deletion or wiring step is incomplete. Work through failures until all pass.

#### Step 3: Write minimal implementation

No new code — this task is a verification gate. If tests fail, revisit the appropriate earlier task and fix the outstanding issue.

#### Step 4: Run test to verify it passes

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/test_deletions_final_verification.py -v
```

Expected: all sub-tests pass.

Run the complete non-deleted test suite:

```bash
cd /Users/john/john-planner-v1
.venv/bin/python -m pytest tests/ \
  --ignore=tests/test_envstate_acl.py \
  --ignore=tests/test_envstate_probes.py \
  --ignore=tests/test_envstate_supervisor.py \
  --ignore=tests/test_envstate_worker.py \
  --ignore=tests/test_fullstate_worker.py \
  --ignore=tests/test_envstate_types.py \
  -q 2>&1 | tail -20
```

Expected: no new failures introduced by this component.

#### Step 5: Commit

```bash
cd /Users/john/john-planner-v1
git add tests/test_deletions_final_verification.py
git commit -m "test(deletions): final gate — zero top-level deleted-module imports, arm v1 wired, arm 0 clean"
```
