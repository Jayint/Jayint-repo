# Runtime-Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the open arc test-execution → graph by classifying per-cycle ledger failures deterministically and appending the revealed requirement as a `DiscoveredBy.RUNTIME` node + edge to the live `WorldModelMap.dep_graph`.

**Architecture:** Two new pure modules (`runtime_classify.py`, `runtime_ingest.py`) carry all classification and graph-mutation logic with zero `src/envstate` imports, keeping them unit-testable without Docker. One wiring site in `orchestrator.run_v1` reads the ledger slice added since the previous cycle, calls `ingest_runtime_failures`, and re-renders the advisory via `merge_map`; the entire path is gated on `enable_runtime_feedback` (default off) so flag-off behavior is byte-identical to today.

**Tech Stack:** Python 3.11, pytest, the existing `src/python_deps/depgraph` package + `src/envstate` agent loop.

## Global Constraints
- **NO COMMITS. NO `git add`. Leave the working tree dirty — standing project rule. Do not stage or commit any file.**
- Interpreter is `python3` (no bare `python`); run tests with `python3 -m pytest`.
- New modules are PURE (no `src/envstate` imports) and must be unit-testable without Docker.
- The feature is gated on `enable_runtime_feedback` (env `DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK`), default OFF; flag-off behavior must be byte-identical to today.
- Runtime DISCOVERS (append node + evidence + check_command); only `certify` flips `state` (do not flip state in ingest). Services are advisory (no check_command flip).
- Immutability: every graph "mutation" returns a NEW `DepGraph`; frozen dataclasses throughout.

---

## Interfaces reference (extracted from source)

### Ledger event fields (confirmed from `src/envstate/ledger.py`)
`ActionEvent` fields: `.step: int`, `.cmd: str`, `.rc: int`, `.stdout: str` (inline truncated text stored by `_append_ledger_event` at line 972 via `make_action_event(stdout=_truncate_output(output))`). The spec's assumption of `.cmd` and `.stdout` is **correct**. Note the field is `.cmd` not `.command`.

### `DepGraph` mutation API (`src/python_deps/depgraph/schema.py`)
- `DepGraph.with_node(node: Node) -> DepGraph` — replaces any existing node with the same id; returns new graph.
- `DepGraph.with_edge(edge: Edge) -> DepGraph` — dedupes by `(src, dst, relation)`, validates `EDGE_RULES`; returns new graph. Raises `ValueError` if either node is absent.
- `DepGraph.get(node_id: str) -> Node | None`

### Node ids (`src/python_deps/depgraph/ids.py`)
- `package_id(name, version=None) -> str` — `f"pkg:{name}"` (no version for runtime discovery)
- `syslib_id(soname) -> str` — `f"syslib:{soname}"`
- `tool_id(tool) -> str` — `f"tool:{tool}"`
- `config_id(name) -> str` — `f"config:{name}"`
- `service_id(name) -> str` — `f"service:{name}"`
- `TEST_NODE_ID = "test:repo_tests_pass"`

### `EDGE_RULES` (confirmed from `schema.py:87-98`)
`"requires"` src set: `{"Test", "Project", "Import", "Package"}` — **Test is a legal source**. All five target types (Package, SystemLib, Tool, Config, Service) are legal destinations. Edge dedup key is `(src, dst, relation.value)`.

### `classify_dependency_failure` return (`src/python_deps/models.py:52-58`)
Returns `DependencyFailure(failure_type, command, import_name, package_name, message, details)`. Fields `.import_name` (str|None) and `.details["library"]` (soname for `native_library_missing`).

### `classify_service_error` (`src/python_deps/depgraph/service_scan.py:152`)
`classify_service_error(text: str) -> str | None` — returns a service kind string or `None`.

### `map_import_to_package` (`src/python_deps/import_mapping.py:48`)
`map_import_to_package(import_name: str, declared_package_names: set[str] | None = None) -> MappingResult` where `MappingResult.package_name: str`.

### `merge_map` (`src/envstate/world_model.py:~228`)
`merge_map(current, *, dep_graph=None, dep_advisory=None, installed=None, ...) -> WorldModelMap` — returns a new map with only the provided fields replaced.

### Per-cycle render function
`render_depgraph_planner` from `src/python_deps/depgraph/advise.py` (line 224) — already imported in `_dep_emit_phase` at `orchestrator.py:113`. **This is the render function wired in Task 5.**

### `_dep_emit_phase` integration site (`orchestrator.py:104-132`)
The per-cycle function already imports `render_depgraph_planner` and calls `merge_map(current_map, dep_graph=graph, dep_advisory=advisory, ...)`. The runtime-feedback ingest runs **once per cycle at the top of the loop body** — right after `_dep_emit_phase(cycle)` and before `planner.decide(...)`. Placing it there (rather than inside each branch) guarantees it fires on EVERY path, including the planner-"done" branch, which `return`s early when `enable_contract_graph=False`. It reads the ledger slice appended since the previous ingest, so cycle N's commands are ingested at the start of cycle N+1.

---

## Task 1 — `classify_config_error(command, output) -> str | None`

**Adds `classify_config_error` and `classify_tool_error` to `src/python_deps/failure_classifier.py`.**

**Files**
- Modify: `src/python_deps/failure_classifier.py` (append to the end of the file)
- Create: `tests/depgraph/test_runtime_parsers.py`

**Interfaces**
- Consumes: `command: str`, `output: str`
- Produces: `str` (env-var name) or `None`; `str` (tool name) or `None`

### Steps

- [ ] **1a. Write the failing test.**

  Create `tests/depgraph/test_runtime_parsers.py`:

  ```python
  """Tests for the two new runtime sub-parsers in failure_classifier.py."""
  from __future__ import annotations

  import sys
  from pathlib import Path

  _SRC = Path(__file__).resolve().parents[2] / "src"
  if str(_SRC) not in sys.path:
      sys.path.insert(0, str(_SRC))

  import pytest
  from python_deps.failure_classifier import classify_config_error, classify_tool_error


  # ── classify_config_error ────────────────────────────────────────────────────

  def test_config_keyerror_single_quotes():
      assert classify_config_error("python app.py", "KeyError: 'DATABASE_URL'") == "DATABASE_URL"

  def test_config_keyerror_double_quotes():
      assert classify_config_error("python app.py", 'KeyError: "SECRET_KEY"') == "SECRET_KEY"

  def test_config_pydantic_field_required():
      output = (
          "pydantic.error_wrappers.ValidationError: 1 validation error for Settings\n"
          "REDIS_URL\n"
          "  field required (type=value_error.missing)"
      )
      assert classify_config_error("python -c 'from app.config import settings'", output) == "REDIS_URL"

  def test_config_pydantic_v2_field_required():
      output = (
          "pydantic_core._pydantic_core.ValidationError: 1 validation error for Config\n"
          "API_KEY\n"
          "  Field required [type=missing, input_url=https://errors.pydantic.dev/2.0/v/missing]"
      )
      assert classify_config_error("python app.py", output) == "API_KEY"

  def test_config_no_match_returns_none():
      assert classify_config_error("pip install flask", "No module named 'flask'") is None

  def test_config_empty_output_returns_none():
      assert classify_config_error("python app.py", "") is None

  def test_config_non_env_keyerror_returns_none():
      # lowercase key — not an env-var pattern (all-caps or mixed with underscores)
      assert classify_config_error("python app.py", "KeyError: 'some_dict_key_lowercase'") is None


  # ── classify_tool_error ──────────────────────────────────────────────────────

  def test_tool_command_not_found():
      assert classify_tool_error("ffmpeg -i input.mp4 out.webm", "ffmpeg: command not found") == "ffmpeg"

  def test_tool_command_not_found_sh_prefix():
      assert classify_tool_error("make all", "/bin/sh: 1: make: not found") == "make"

  def test_tool_filenotfounderror():
      output = "FileNotFoundError: [Errno 2] No such file or directory: 'pandoc'"
      assert classify_tool_error("subprocess.run(['pandoc', '--version'])", output) == "pandoc"

  def test_tool_no_match_returns_none():
      assert classify_tool_error("python app.py", "KeyError: 'DATABASE_URL'") is None

  def test_tool_empty_output_returns_none():
      assert classify_tool_error("ls /tmp", "") is None
  ```

- [ ] **1b. Run it, expect FAIL.**

  ```
  python3 -m pytest tests/depgraph/test_runtime_parsers.py -q
  ```

  Expected: `ImportError: cannot import name 'classify_config_error' from 'python_deps.failure_classifier'`

- [ ] **1c. Write the minimal implementation.**

  Append the following to the end of `src/python_deps/failure_classifier.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Runtime sub-parsers (Task 1 & 2 — runtime_classify.py consumers)
  # ---------------------------------------------------------------------------

  # Env-var names are ALL_CAPS or UPPER_with_underscores (at least one uppercase
  # letter; no purely lowercase keys to avoid false-positive dict lookups).
  _CONFIG_KEYERROR_RE = re.compile(
      r"""KeyError:\s*['"]([A-Z][A-Z0-9_]*)['"]"""
  )

  # pydantic v1: field name on its own line before "field required"
  # pydantic v2: field name on its own line before "Field required [type=missing"
  _CONFIG_PYDANTIC_RE = re.compile(
      r"^([A-Z][A-Z0-9_]+)\n\s+(?:field required|Field required)",
      re.MULTILINE,
  )


  def classify_config_error(command: str, output: str) -> str | None:
      """Return the env-var name if ``output`` looks like a missing config error, else None."""
      text = output or ""
      m = _CONFIG_KEYERROR_RE.search(text)
      if m:
          return m.group(1)
      m = _CONFIG_PYDANTIC_RE.search(text)
      if m:
          return m.group(1)
      return None


  _TOOL_COMMAND_NOT_FOUND_RE = re.compile(
      r"(?:^|[\s/])([A-Za-z0-9_.-]+):\s+(?:command not found|not found)",
      re.MULTILINE,
  )
  _TOOL_FILENOTFOUNDERROR_RE = re.compile(
      r"FileNotFoundError:.*?['\"]([A-Za-z0-9_.-]+)['\"]"
  )


  def classify_tool_error(command: str, output: str) -> str | None:
      """Return the tool name if ``output`` looks like a missing executable, else None."""
      text = output or ""
      m = _TOOL_COMMAND_NOT_FOUND_RE.search(text)
      if m:
          return m.group(1)
      m = _TOOL_FILENOTFOUNDERROR_RE.search(text)
      if m:
          return m.group(1)
      return None
  ```

- [ ] **1d. Run tests, expect PASS.**

  ```
  python3 -m pytest tests/depgraph/test_runtime_parsers.py -q
  ```

  Expected: `12 passed`

- [ ] **1e. Run the full relevant suite to confirm no regressions.**

  ```
  python3 -m pytest tests/depgraph/ -q
  ```

  Do NOT commit, do NOT `git add` — leave the tree dirty.

---

## Task 2 — `Observation`, `Discovery` dataclasses + `classify_observation` dispatch

**Creates `src/python_deps/depgraph/runtime_classify.py` with the two frozen dataclasses and the priority-ordered dispatcher.**

**Files**
- Create: `src/python_deps/depgraph/runtime_classify.py`
- Modify: `tests/depgraph/test_runtime_parsers.py` (append new test class)

**Interfaces**
- Consumes: `command: str`, `output: str`
- Produces: `Discovery | None`

### Steps

- [ ] **2a. Write the failing test.**

  Append the following to `tests/depgraph/test_runtime_parsers.py`:

  ```python
  # ── classify_observation dispatch ────────────────────────────────────────────

  from python_deps.depgraph.runtime_classify import classify_observation, Discovery
  from python_deps.depgraph.schema import NodeType, Layer


  def test_dispatch_module_not_found_returns_package_discovery():
      d = classify_observation("python app.py", "ModuleNotFoundError: No module named 'cv2'")
      assert d is not None
      assert d.node_type is NodeType.PACKAGE
      assert d.name == "opencv-python"          # curated mapping from import_mapping.py
      assert d.layer is Layer.PIP
      assert d.check_command == "python3 -c \"import cv2\""
      assert d.confidence == "runtime-deterministic"


  def test_dispatch_module_not_found_unknown_import():
      d = classify_observation("python app.py", "ModuleNotFoundError: No module named 'mylib'")
      assert d is not None
      assert d.node_type is NodeType.PACKAGE
      assert d.name == "mylib"
      assert d.layer is Layer.PIP


  def test_dispatch_native_library_returns_syslib():
      d = classify_observation(
          "python app.py",
          "ImportError: libGL.so.1: cannot open shared object file: No such file or directory",
      )
      assert d is not None
      assert d.node_type is NodeType.SYSTEM_LIB
      assert d.name == "libGL.so.1"
      assert d.layer is Layer.SYSTEM
      assert d.check_command == "ldconfig -p | grep -q libGL.so.1"


  def test_dispatch_service_error_returns_service_discovery():
      d = classify_observation(
          "python manage.py migrate",
          "psycopg2.OperationalError: could not connect to server: Connection refused",
      )
      assert d is not None
      assert d.node_type is NodeType.SERVICE
      assert d.name == "postgres"
      assert d.layer is Layer.SERVICES
      assert d.check_command is None          # services are advisory


  def test_dispatch_config_error_returns_config_discovery():
      d = classify_observation("python app.py", "KeyError: 'DATABASE_URL'")
      assert d is not None
      assert d.node_type is NodeType.CONFIG
      assert d.name == "DATABASE_URL"
      assert d.layer is Layer.CONFIG
      assert d.check_command == "printenv DATABASE_URL"


  def test_dispatch_tool_error_returns_tool_discovery():
      d = classify_observation("make all", "make: command not found")
      assert d is not None
      assert d.node_type is NodeType.TOOL
      assert d.name == "make"
      assert d.layer is Layer.TOOLCHAIN
      assert d.check_command == "command -v make"


  def test_dispatch_ignored_build_time_failure_returns_none():
      # no_matching_distribution is a build-time install failure — not a runtime requirement
      d = classify_observation(
          "pip install flask",
          "No matching distribution found for flask==99.0",
      )
      assert d is None


  def test_dispatch_not_dependency_related_returns_none():
      d = classify_observation("python app.py", "AssertionError: expected True to be False")
      assert d is None


  def test_dispatch_priority_module_before_service():
      # Output has both a ModuleNotFoundError AND a service-style error:
      # module classification wins (priority 1 before priority 2).
      output = (
          "ModuleNotFoundError: No module named 'psycopg2'\n"
          "psycopg2.OperationalError: could not connect to server"
      )
      d = classify_observation("python app.py", output)
      assert d is not None
      assert d.node_type is NodeType.PACKAGE


  def test_dispatch_import_name_error_returns_package():
      d = classify_observation(
          "python app.py",
          "ImportError: cannot import name 'current_app' from 'flask'",
      )
      assert d is not None
      assert d.node_type is NodeType.PACKAGE
      assert d.name == "flask"
  ```

- [ ] **2b. Run it, expect FAIL.**

  ```
  python3 -m pytest tests/depgraph/test_runtime_parsers.py::test_dispatch_module_not_found_returns_package_discovery -q
  ```

  Expected: `ModuleNotFoundError: No module named 'python_deps.depgraph.runtime_classify'`

- [ ] **2c. Write the minimal implementation.**

  Create `src/python_deps/depgraph/runtime_classify.py`:

  ```python
  """Runtime-feedback classifier (design 2026-06-26 §5, §6, §10).

  Pure module — no src.envstate imports. Unit-testable with plain strings.

  ``classify_observation(command, output) -> Discovery | None``

  Tries four sub-classifiers in priority order (spec §6) and returns the first
  non-None hit.  Returns None for ignored observations (build-time install
  failures, assertion errors, and anything not requirement-bearing).
  """
  from __future__ import annotations

  from dataclasses import dataclass, field

  from python_deps.depgraph.schema import Layer, NodeType
  from python_deps.failure_classifier import classify_dependency_failure


  # ---------------------------------------------------------------------------
  # Data structures (spec §10)
  # ---------------------------------------------------------------------------

  @dataclass(frozen=True)
  class Observation:
      command: str
      output: str   # combined stdout/stderr text from the ledger event


  @dataclass(frozen=True)
  class Discovery:
      node_type: NodeType           # PACKAGE | SYSTEM_LIB | TOOL | CONFIG | SERVICE
      name: str                     # dist / soname / tool / VAR / service-kind
      layer: Layer
      evidence: str                 # failure excerpt that revealed the requirement
      check_command: str | None     # None only for SERVICE (advisory)
      confidence: str = "runtime-deterministic"
      data: dict = field(default_factory=dict)


  # ---------------------------------------------------------------------------
  # Ignored failure_type values from classify_dependency_failure (spec §6).
  #
  # These are genuine build-time / unrelated DEP failures owned elsewhere.
  # CRITICAL: "not_dependency_related" is deliberately NOT in this set.
  # classify_dependency_failure returns "not_dependency_related" for ANY non-dep
  # failure — including the very connection-refused / KeyError / command-not-found
  # shapes the Service/Config/Tool classifiers handle. If we short-circuited on it,
  # priorities 2/3/4 would never run and 3 of the 5 classes would be silently
  # dropped. It therefore FALLS THROUGH to the service→config→tool chain; the
  # dispatcher returns None only at the very end, when nothing matched.
  # ---------------------------------------------------------------------------
  _IGNORED_FAILURE_TYPES: frozenset[str] = frozenset({
      "no_matching_distribution",
      "dependency_conflict",
      "syntax_requires_newer_python",
  })


  # ---------------------------------------------------------------------------
  # Public dispatcher
  # ---------------------------------------------------------------------------

  def classify_observation(command: str, output: str) -> Discovery | None:
      """Classify one (command, output) observation.  Returns Discovery or None.

      Priority order (spec §6):
        1. classify_dependency_failure  — Package (module/import) or SystemLib
        2. classify_service_error       — Service
        3. classify_config_error        — Config
        4. classify_tool_error          — Tool
      """
      text = output or ""

      # ── Priority 1: python import / native-lib failures ──────────────────
      dep = classify_dependency_failure(command, text)
      if dep.failure_type == "module_not_found":
          from python_deps.import_mapping import map_import_to_package
          import_name = dep.import_name or ""
          pkg_name = map_import_to_package(import_name).package_name
          return Discovery(
              node_type=NodeType.PACKAGE,
              name=pkg_name,
              layer=Layer.PIP,
              evidence=dep.message[:500],
              check_command=f'python3 -c "import {import_name}"',
              data={"import_name": import_name},
          )
      if dep.failure_type == "import_name_error":
          from python_deps.import_mapping import map_import_to_package
          import_name = dep.import_name or ""
          pkg_name = map_import_to_package(import_name).package_name
          return Discovery(
              node_type=NodeType.PACKAGE,
              name=pkg_name,
              layer=Layer.PIP,
              evidence=dep.message[:500],
              check_command=f'python3 -c "import {import_name}"',
              data={"import_name": import_name},
          )
      if dep.failure_type == "native_library_missing":
          soname = dep.details.get("library", "")
          return Discovery(
              node_type=NodeType.SYSTEM_LIB,
              name=soname,
              layer=Layer.SYSTEM,
              evidence=dep.message[:500],
              check_command=f"ldconfig -p | grep -q {soname}",
          )
      if dep.failure_type in _IGNORED_FAILURE_TYPES:
          return None

      # dep.failure_type == "not_dependency_related" is NOT ignored — it only means
      # the dep classifier did not match, so we FALL THROUGH to the service→config→
      # tool classifiers below. Returning None here would silently drop 3 of 5 classes.

      # ── Priority 2: service connection failures ───────────────────────────
      from python_deps.depgraph.service_scan import classify_service_error
      svc_kind = classify_service_error(text)
      if svc_kind is not None:
          return Discovery(
              node_type=NodeType.SERVICE,
              name=svc_kind,
              layer=Layer.SERVICES,
              evidence=text[:500],
              check_command=None,   # services are advisory; certify skip-guards them
          )

      # ── Priority 3: missing config / env-var ─────────────────────────────
      from python_deps.failure_classifier import classify_config_error
      var_name = classify_config_error(command, text)
      if var_name is not None:
          return Discovery(
              node_type=NodeType.CONFIG,
              name=var_name,
              layer=Layer.CONFIG,
              evidence=text[:500],
              check_command=f"printenv {var_name}",
          )

      # ── Priority 4: missing tool / executable ────────────────────────────
      from python_deps.failure_classifier import classify_tool_error
      tool_name = classify_tool_error(command, text)
      if tool_name is not None:
          return Discovery(
              node_type=NodeType.TOOL,
              name=tool_name,
              layer=Layer.TOOLCHAIN,
              evidence=text[:500],
              check_command=f"command -v {tool_name}",
          )

      return None
  ```

- [ ] **2d. Run tests, expect PASS.**

  ```
  python3 -m pytest tests/depgraph/test_runtime_parsers.py -q
  ```

  Expected: `22 passed` (12 from Task 1 + 10 new dispatch tests)

- [ ] **2e. Run the full relevant suite to confirm no regressions.**

  ```
  python3 -m pytest tests/depgraph/ -q
  ```

  Do NOT commit, do NOT `git add` — leave the tree dirty.

---

## Task 3 — `ingest_runtime_failures` in `runtime_ingest.py`

**Creates `src/python_deps/depgraph/runtime_ingest.py` with idempotent annotate-or-append logic.**

**Files**
- Create: `src/python_deps/depgraph/runtime_ingest.py`
- Create: `tests/depgraph/test_runtime_ingest.py`

**Interfaces**
- Consumes: `graph: DepGraph`, `observations: list[tuple[str, str]]`, `classifiers: Sequence[Callable]`
- Produces: `tuple[DepGraph, list[Discovery]]`

### Steps

- [ ] **3a. Write the failing test.**

  Create `tests/depgraph/test_runtime_ingest.py`:

  ```python
  """Tests for runtime_ingest.ingest_runtime_failures (pure, no Docker)."""
  from __future__ import annotations

  import sys
  from pathlib import Path

  _SRC = Path(__file__).resolve().parents[2] / "src"
  if str(_SRC) not in sys.path:
      sys.path.insert(0, str(_SRC))

  import pytest
  from python_deps.depgraph.ids import (
      TEST_NODE_ID, config_id, package_id, service_id, syslib_id, tool_id,
  )
  from python_deps.depgraph.runtime_classify import Discovery
  from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
  from python_deps.depgraph.schema import (
      DepGraph, DiscoveredBy, EdgeType, Layer, Node, NodeType, State,
  )


  def _test_node() -> Node:
      return Node(
          id=TEST_NODE_ID,
          type=NodeType.TEST,
          name="repo_tests_pass",
          layer=Layer.TESTS,
          discovered_by=DiscoveredBy.GOAL,
      )


  def _base_graph() -> DepGraph:
      return DepGraph().with_node(_test_node())


  # ── append-new ───────────────────────────────────────────────────────────────

  def test_append_new_package_node():
      graph = _base_graph()
      obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
      new_graph, discoveries = ingest_runtime_failures(graph, obs)

      node = new_graph.get(package_id("requests", None))
      assert node is not None
      assert node.type is NodeType.PACKAGE
      assert node.discovered_by is DiscoveredBy.RUNTIME
      assert node.check_command == 'python3 -c "import requests"'
      assert node.data.get("runtime_confidence") == "runtime-deterministic"
      assert len(discoveries) == 1


  def test_append_new_syslib_node():
      graph = _base_graph()
      obs = [("python app.py", "ImportError: libGL.so.1: cannot open shared object file")]
      new_graph, discoveries = ingest_runtime_failures(graph, obs)

      node = new_graph.get(syslib_id("libGL.so.1"))
      assert node is not None
      assert node.type is NodeType.SYSTEM_LIB
      assert node.discovered_by is DiscoveredBy.RUNTIME
      assert node.check_command == "ldconfig -p | grep -q libGL.so.1"


  def test_append_new_tool_node():
      graph = _base_graph()
      obs = [("make all", "make: command not found")]
      new_graph, discoveries = ingest_runtime_failures(graph, obs)

      node = new_graph.get(tool_id("make"))
      assert node is not None
      assert node.type is NodeType.TOOL
      assert node.check_command == "command -v make"


  def test_append_new_config_node():
      graph = _base_graph()
      obs = [("python app.py", "KeyError: 'DATABASE_URL'")]
      new_graph, discoveries = ingest_runtime_failures(graph, obs)

      node = new_graph.get(config_id("DATABASE_URL"))
      assert node is not None
      assert node.type is NodeType.CONFIG
      assert node.check_command == "printenv DATABASE_URL"


  # ── service advisory (no check_command flip) ─────────────────────────────────

  def test_append_service_node_advisory():
      graph = _base_graph()
      obs = [("python manage.py migrate", "psycopg2.OperationalError: could not connect to server")]
      new_graph, discoveries = ingest_runtime_failures(graph, obs)

      node = new_graph.get(service_id("postgres"))
      assert node is not None
      assert node.type is NodeType.SERVICE
      assert node.discovered_by is DiscoveredBy.RUNTIME
      # Services are advisory — check_command stays None (certify skip-guards them)
      assert node.check_command is None
      assert node.state is State.UNKNOWN


  # ── edge attribution (Test --requires--> node, origin="runtime") ─────────────

  def test_runtime_edge_hangs_off_test_node():
      graph = _base_graph()
      obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
      new_graph, _ = ingest_runtime_failures(graph, obs)

      edges = [e for e in new_graph.edges
               if e.src == TEST_NODE_ID and e.relation is EdgeType.REQUIRES
               and e.origin == "runtime"]
      assert len(edges) == 1
      assert edges[0].dst == package_id("requests", None)


  # ── annotate-existing (idempotent across two passes) ─────────────────────────

  def test_annotate_existing_node_is_idempotent():
      graph = _base_graph()
      obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]

      graph1, discoveries1 = ingest_runtime_failures(graph, obs)
      graph2, discoveries2 = ingest_runtime_failures(graph1, obs)

      # Same number of nodes both times — no duplicate appended
      assert len(graph2.nodes) == len(graph1.nodes)
      # Edge deduped — still exactly one runtime edge to the package
      runtime_edges = [e for e in graph2.edges
                       if e.src == TEST_NODE_ID and e.origin == "runtime"]
      assert len(runtime_edges) == 1
      # Both passes returned a discovery
      assert len(discoveries1) == 1
      assert len(discoveries2) == 1


  def test_annotate_existing_sets_runtime_confidence():
      """A package already in the graph (static) gets runtime_confidence annotated."""
      existing = Node(
          id=package_id("requests", None),
          type=NodeType.PACKAGE,
          name="requests",
          layer=Layer.PIP,
          discovered_by=DiscoveredBy.STATIC_SCAN,
      )
      graph = _base_graph().with_node(existing)
      obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
      new_graph, discoveries = ingest_runtime_failures(graph, obs)

      node = new_graph.get(package_id("requests", None))
      assert node.data.get("runtime_confidence") == "runtime-deterministic"
      # discovered_by must not be silently downgraded
      # (runtime evidence is stronger; annotated node picks up RUNTIME provenance)
      assert node.discovered_by is DiscoveredBy.RUNTIME
      assert len(discoveries) == 1


  # ── ignore-set produces no mutation ──────────────────────────────────────────

  def test_no_matching_distribution_ignored():
      graph = _base_graph()
      obs = [("pip install flask", "No matching distribution found for flask==99.0")]
      new_graph, discoveries = ingest_runtime_failures(graph, obs)

      assert new_graph is graph or len(new_graph.nodes) == len(graph.nodes)
      assert discoveries == []


  def test_assertion_error_ignored():
      graph = _base_graph()
      obs = [("python -m pytest", "AssertionError: assert 1 == 2")]
      new_graph, discoveries = ingest_runtime_failures(graph, obs)
      assert discoveries == []


  # ── original graph never mutated (immutability) ──────────────────────────────

  def test_original_graph_unchanged():
      graph = _base_graph()
      original_node_count = len(graph.nodes)
      obs = [("python app.py", "ModuleNotFoundError: No module named 'requests'")]
      ingest_runtime_failures(graph, obs)
      assert len(graph.nodes) == original_node_count
  ```

- [ ] **3b. Run it, expect FAIL.**

  ```
  python3 -m pytest tests/depgraph/test_runtime_ingest.py -q
  ```

  Expected: `ModuleNotFoundError: No module named 'python_deps.depgraph.runtime_ingest'`

- [ ] **3c. Write the minimal implementation.**

  Create `src/python_deps/depgraph/runtime_ingest.py`:

  ```python
  """Runtime-feedback graph ingestion (design 2026-06-26 §5, §9).

  Pure module — no src.envstate imports. Unit-testable with plain data.

  ``ingest_runtime_failures(graph, observations, classifiers) -> (DepGraph, list[Discovery])``

  Maps each non-None Discovery to an idempotent graph mutation:
    * id absent  -> append new node
    * id present -> annotate: merge runtime evidence + set runtime_confidence
  Hangs a Test --requires--> node edge with origin="runtime" (deduped by DepGraph).
  Returns a NEW DepGraph every time (immutability) and the list of Discoveries found.
  """
  from __future__ import annotations

  import logging
  from collections.abc import Callable, Sequence
  from dataclasses import replace

  from python_deps.depgraph.ids import (
      TEST_NODE_ID, config_id, package_id, service_id, syslib_id, tool_id,
  )
  from python_deps.depgraph.runtime_classify import Discovery, classify_observation
  from python_deps.depgraph.schema import (
      DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
  )

  logger = logging.getLogger(__name__)


  # ---------------------------------------------------------------------------
  # Discovery -> Node constructor
  # ---------------------------------------------------------------------------

  def _node_for_discovery(d: Discovery) -> Node:
      """Build a fresh Node from a Discovery.  State is UNKNOWN — certify owns it."""
      node_id = _id_for_discovery(d)
      return Node(
          id=node_id,
          type=d.node_type,
          name=d.name,
          layer=d.layer,
          discovered_by=DiscoveredBy.RUNTIME,
          state=State.UNKNOWN,
          check_command=d.check_command,
          evidence=d.evidence,
          provenance="runtime ingest",
          data={"runtime_confidence": d.confidence, **d.data},
      )


  def _id_for_discovery(d: Discovery) -> str:
      if d.node_type is NodeType.PACKAGE:
          return package_id(d.name, None)
      if d.node_type is NodeType.SYSTEM_LIB:
          return syslib_id(d.name)
      if d.node_type is NodeType.TOOL:
          return tool_id(d.name)
      if d.node_type is NodeType.CONFIG:
          return config_id(d.name)
      if d.node_type is NodeType.SERVICE:
          return service_id(d.name)
      # Should never be reached for the five discovery types.
      raise ValueError(f"Unsupported discovery node_type: {d.node_type}")


  # ---------------------------------------------------------------------------
  # Annotate-or-append (spec §9)
  # ---------------------------------------------------------------------------

  def _annotate_or_append(graph: DepGraph, d: Discovery) -> DepGraph:
      """Apply one Discovery to the graph idempotently.  Returns a NEW graph."""
      node_id = _id_for_discovery(d)
      existing = graph.get(node_id)

      if existing is None:
          # Append: brand-new requirement discovered at runtime.
          new_node = _node_for_discovery(d)
      else:
          # Annotate: merge stronger runtime evidence onto the existing node.
          # runtime evidence is strictly stronger than static (spec §8);
          # update discovered_by + evidence + runtime_confidence.
          new_data = {**dict(existing.data), "runtime_confidence": d.confidence}
          if d.data:
              new_data.update(d.data)
          new_node = replace(
              existing,
              discovered_by=DiscoveredBy.RUNTIME,
              evidence=d.evidence,
              data=new_data,
          )

      new_graph = graph.with_node(new_node)

      # Hang edge Test --requires--> node with origin="runtime".
      # with_edge is idempotent (deduped by (src, dst, relation) key).
      test_node = new_graph.get(TEST_NODE_ID)
      if test_node is not None:
          edge = Edge(src=TEST_NODE_ID, dst=node_id, relation=EdgeType.REQUIRES, origin="runtime")
          new_graph = new_graph.with_edge(edge)

      return new_graph


  # ---------------------------------------------------------------------------
  # Public entrypoint
  # ---------------------------------------------------------------------------

  def ingest_runtime_failures(
      graph: DepGraph,
      observations: list[tuple[str, str]],
      classifiers: Sequence[Callable] = (classify_observation,),
  ) -> tuple[DepGraph, list[Discovery]]:
      """Map observations to Discoveries, apply them idempotently to ``graph``.

      ``observations`` is a list of ``(command, output)`` tuples (one per ledger
      event since the last ingest).  ``classifiers`` is tried in order; the first
      non-None result wins per observation.

      Returns ``(new_graph, found)`` where ``found`` is the list of all non-None
      Discoveries (for logging / advisory re-render).  Never raises — any
      per-observation exception logs a warning and skips that observation.
      """
      new = graph
      found: list[Discovery] = []

      for cmd, out in observations:
          try:
              d: Discovery | None = None
              for classifier in classifiers:
                  d = classifier(cmd, out)
                  if d is not None:
                      break
              if d is None:
                  continue
              new = _annotate_or_append(new, d)
              found.append(d)
          except Exception as exc:  # noqa: BLE001 — must never break the run (spec §11)
              logger.warning("runtime_ingest: skipped observation (%r): %s", cmd[:60], exc)

      return new, found
  ```

- [ ] **3d. Run tests, expect PASS.**

  ```
  python3 -m pytest tests/depgraph/test_runtime_ingest.py -q
  ```

  Expected: `11 passed`

- [ ] **3e. Run the full relevant suite to confirm no regressions.**

  ```
  python3 -m pytest tests/depgraph/ -q
  ```

  Do NOT commit, do NOT `git add` — leave the tree dirty.

---

## Task 4 — Orchestrator wiring (`enable_runtime_feedback` in `run_v1`)

**Wires the per-cycle ingest into `orchestrator.run_v1` behind the new flag.**

**Files**
- Modify: `src/envstate/orchestrator.py` — add `enable_runtime_feedback: bool = False` param and `_runtime_ingest_phase` helper; call it at end of each cycle.
- Create: `tests/test_runtime_feedback_wiring.py`

**Interfaces**
- Consumes: `current_map.dep_graph: DepGraph`, `ledger.events(): tuple[ActionEvent, ...]`, high-water mark `_rt_mark: int`
- Produces: updated `current_map` via `merge_map(current_map, dep_graph=new_graph, dep_advisory=new_advisory)`

### Steps

- [ ] **4a. Write the failing test.**

  Create `tests/test_runtime_feedback_wiring.py`:

  ```python
  """Integration-style test: verify runtime_feedback wiring in orchestrator.run_v1.

  Uses synthetic planner/build_agent/maintainer stubs (no Docker/LLM).
  Confirms:
    - flag OFF  -> no runtime nodes appended (flag-off byte-identical)
    - flag ON   -> runtime PACKAGE node appears after a ledger event with ModuleNotFoundError
    - flag ON   -> exception in ingest does NOT crash the loop
  """
  from __future__ import annotations

  import sys
  from pathlib import Path

  _ROOT = Path(__file__).resolve().parents[1]          # repo root (this file is at tests/)
  if str(_ROOT) not in sys.path:
      sys.path.insert(0, str(_ROOT))                   # for `from src.envstate...`
  _SRC = _ROOT / "src"
  if str(_SRC) not in sys.path:
      sys.path.insert(0, str(_SRC))                    # for `from python_deps...`

  import pytest
  from src.envstate.ledger import ActionLedger, make_action_event
  from src.envstate.orchestrator import run_v1
  from src.envstate.world_model import (
      PlannerDecision,
      Task,
      TaskReport,
      WorldModelMap,
      initial_map,
      merge_map,
  )
  from python_deps.depgraph.ids import TEST_NODE_ID, package_id
  from python_deps.depgraph.schema import (
      DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
  )


  # ── Minimal stubs ────────────────────────────────────────────────────────────

  class _QueuePlanner:
      """Emits PlannerDecision objects from a pre-loaded queue (mirrors the
      FakePlanner convention in tests/test_orchestrator_v1.py)."""
      def __init__(self, decisions):
          self._queue = list(decisions)
      def decide(self, world_map):
          assert self._queue, "_QueuePlanner.decide called more times than expected"
          return self._queue.pop(0)


  def _task() -> Task:
      return Task(goal="install deps", done_when="pip exits 0", layer="deps", facts=())


  class _StubBuildAgent:
      def run(self, task, sandbox_execute, ledger, step_offset=0):
          return TaskReport("task", "done", (), "ok")
      def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
          return TaskReport("recipe", "done", (), "ok")


  class _NoopMaintainer:
      """Does not set done_flag, so the loop runs the queued 'task' cycle, then the
      planner's queued 'done' terminates it. (The pre-seeded ledger event is ingested
      at the top of cycle 1, before the planner is consulted.)"""
      def update(self, world_map, report):
          return world_map


  def _sandbox_execute(cmd):
      return True, "ok"


  def _make_initial_map_with_graph() -> WorldModelMap:
      """A valid WorldModelMap (via initial_map) carrying a minimal DepGraph.

      MUST use initial_map(...) — direct WorldModelMap(...) construction omits
      required frozen-dataclass fields and raises TypeError (C2 fix).
      """
      test_node = Node(
          id=TEST_NODE_ID,
          type=NodeType.TEST,
          name="repo_tests_pass",
          layer=Layer.TESTS,
          discovered_by=DiscoveredBy.GOAL,
      )
      graph = DepGraph().with_node(test_node)
      base = initial_map(
          base_image="python:3.11",
          workdir="/repo",
          language="python",
          build_system="pip",
          repo_layout=(),
      )
      return merge_map(base, dep_graph=graph)


  def _ledger_with_module_error() -> ActionLedger:
      """Ledger pre-populated with one ModuleNotFoundError event."""
      ledger = ActionLedger()
      evt = make_action_event(
          step=1,
          cmd="python app.py",
          success=False,
          stdout="ModuleNotFoundError: No module named 'requests'",
          env_revision_before=0,
          env_revision_after=0,
          mutation_class=None,
          container_id="test",
      )
      ledger.append(evt)
      return ledger


  # ── flag OFF: byte-identical ──────────────────────────────────────────────────

  def test_flag_off_graph_unchanged():
      ledger = _ledger_with_module_error()
      initial = _make_initial_map_with_graph()

      # Planner: one task cycle, then done.
      planner = _QueuePlanner([
          PlannerDecision(action="task", task=_task()),
          PlannerDecision(action="done"),
      ])
      final_map, _ = run_v1(
          planner=planner,
          build_agent=_StubBuildAgent(),
          maintainer=_NoopMaintainer(),
          initial_world_map=initial,
          ledger=ledger,
          sandbox_execute=_sandbox_execute,
          max_cycles=3,
          enable_runtime_feedback=False,
      )

      # No runtime nodes added — flag OFF is byte-identical to today.
      assert final_map.dep_graph is not None
      pkg_node = final_map.dep_graph.get(package_id("requests", None))
      assert pkg_node is None, "flag OFF should not append runtime nodes"


  # ── flag ON: runtime node appears ────────────────────────────────────────────

  def test_flag_on_runtime_node_appended():
      ledger = _ledger_with_module_error()
      initial = _make_initial_map_with_graph()

      # Planner: one task cycle, then done. Ingest runs at the TOP of every cycle,
      # so the pre-seeded ModuleNotFoundError event is ingested on cycle 1 before
      # any branch returns.
      planner = _QueuePlanner([
          PlannerDecision(action="task", task=_task()),
          PlannerDecision(action="done"),
      ])
      final_map, _ = run_v1(
          planner=planner,
          build_agent=_StubBuildAgent(),
          maintainer=_NoopMaintainer(),
          initial_world_map=initial,
          ledger=ledger,
          sandbox_execute=_sandbox_execute,
          max_cycles=3,
          enable_runtime_feedback=True,
      )

      assert final_map.dep_graph is not None
      pkg_node = final_map.dep_graph.get(package_id("requests", None))
      assert pkg_node is not None, "flag ON must append the runtime PACKAGE node"
      assert pkg_node.discovered_by is DiscoveredBy.RUNTIME


  # ── flag ON: ingest exception does not crash the loop ────────────────────────

  def test_flag_on_ingest_exception_does_not_crash():
      """If ingest raises internally, the loop must still complete normally."""
      # Monkey-patch the symbol the orchestrator imports (it imports the function
      # from python_deps.depgraph.runtime_ingest INSIDE _runtime_ingest_phase, so
      # patching the module attribute is the interception point).
      import python_deps.depgraph.runtime_ingest as _m
      original = _m.ingest_runtime_failures

      def _boom(graph, observations, classifiers=(_m.classify_observation,)):
          raise RuntimeError("simulated ingest crash")

      _m.ingest_runtime_failures = _boom
      try:
          ledger = _ledger_with_module_error()
          initial = _make_initial_map_with_graph()
          planner = _QueuePlanner([
              PlannerDecision(action="task", task=_task()),
              PlannerDecision(action="done"),
          ])
          final_map, stop_reason = run_v1(
              planner=planner,
              build_agent=_StubBuildAgent(),
              maintainer=_NoopMaintainer(),
              initial_world_map=initial,
              ledger=ledger,
              sandbox_execute=_sandbox_execute,
              max_cycles=3,
              enable_runtime_feedback=True,
          )
          # Loop must complete normally despite the exception.
          assert stop_reason in ("planner_done", "done_flag", "max_cycles")
      finally:
          _m.ingest_runtime_failures = original
  ```

- [ ] **4b. Run it, expect FAIL.**

  ```
  python3 -m pytest tests/test_runtime_feedback_wiring.py -q
  ```

  Expected: `TypeError: run_v1() got an unexpected keyword argument 'enable_runtime_feedback'`

- [ ] **4c. Write the minimal implementation.**

  Modify `src/envstate/orchestrator.py` with exactly FOUR edits.

  **Edit 1 — signature.** Add `enable_runtime_feedback: bool = False,` to the `run_v1` keyword-only block, immediately after the existing `enable_dep_emit: bool = False,` (line 66):

  ```python
      enable_dep_emit: bool = False,
      enable_runtime_feedback: bool = False,
  ):
  ```

  **Edit 2 — declare the high-water mark ONCE at body scope.** Immediately after the existing `global_step: int = 0` line (line 88), add `_rt_mark` at the SAME scope (it is the only place `_rt_mark` is declared; do NOT also declare it inside or after `_dep_emit_phase`):

  ```python
      global_step: int = 0
      # Runtime-feedback high-water mark: index into ledger.events() up to which we
      # have already ingested. Starts at 0 so the first ingest captures every event
      # not yet ingested — including any written before the loop began (e.g. a
      # pre-seeded failure). _runtime_ingest_phase advances it.
      _rt_mark: int = 0
  ```

  **Edit 3 — define `_runtime_ingest_phase`.** After the closing of `_dep_emit_phase` (after line 132), before the `if probe is not None and manifest is not None:` block, add:

  ```python
      def _runtime_ingest_phase() -> None:
          nonlocal current_map, _rt_mark
          if not enable_runtime_feedback or current_map.dep_graph is None:
              return
          try:
              from python_deps.depgraph.advise import render_depgraph_planner
              from python_deps.depgraph.runtime_ingest import ingest_runtime_failures
              events = ledger.events()
              new_events = events[_rt_mark:]
              obs = [(e.cmd, e.stdout) for e in new_events]
              if not obs:
                  return
              new_graph, found = ingest_runtime_failures(current_map.dep_graph, obs)
              # Advance the mark ONLY after a successful ingest call returns — so an
              # exception mid-ingest does not permanently drop those events (they are
              # re-read next cycle). (spec §11; C4 event-loss fix.)
              _rt_mark = len(events)
              if not found:
                  return
              advisory = render_depgraph_planner(new_graph)
              current_map = merge_map(current_map, dep_graph=new_graph, dep_advisory=advisory)
          except Exception as exc:  # noqa: BLE001 — must never break the run (spec §11)
              import logging
              logging.getLogger(__name__).warning(
                  "runtime_ingest_phase: exception suppressed: %s", exc
              )
  ```

  **Edit 4 — call it ONCE per cycle, before ANY branch returns.** Place a single call at the very TOP of the cycle body, right after `_dep_emit_phase(cycle)` (line 140) and before `decision = planner.decide(current_map)` (line 142). This guarantees ingest runs every cycle on EVERY path — including the planner-"done" branch, which `return`s early before reaching the branch-internal `_host_refresh()` calls when `enable_contract_graph=False`:

  ```python
      for cycle in range(1, max_cycles + 1):
          # ── 0. Graph-first: certify + emit the certified closure ────────────
          _dep_emit_phase(cycle)
          # ── 0b. Runtime feedback: ingest ledger failures from the PREVIOUS cycle
          #        into the live dep-graph. Runs once per cycle before any branch so
          #        it fires regardless of which branch returns (I2 done-path fix).
          _runtime_ingest_phase()
          # ── 1. Planner decides what to do next ──────────────────────────────
          decision: PlannerDecision = planner.decide(current_map)
  ```

  Because the call sits at the top of the cycle, events appended by cycle N (emit + agent commands + the test gate) are ingested at the start of cycle N+1, and events present BEFORE the loop (e.g. a pre-seeded failure) are ingested on cycle 1. The wiring test below pre-seeds the ledger with one `ModuleNotFoundError` event and uses `max_cycles=3` with a planner queue of `[task, done]`; cycle 1's top-of-loop ingest captures the pre-seeded event, so the runtime node is present in the returned map regardless of which branch terminates the loop.

- [ ] **4d. Run tests, expect PASS.**

  ```
  python3 -m pytest tests/test_runtime_feedback_wiring.py -q
  ```

  Expected: `3 passed`

- [ ] **4e. Run the full orchestrator suite and depgraph suite to confirm no regressions.**

  ```
  python3 -m pytest tests/depgraph/ tests/test_runtime_feedback_wiring.py tests/test_orchestrator*.py -q 2>/dev/null || python3 -m pytest tests/depgraph/ tests/test_runtime_feedback_wiring.py -q
  ```

  Do NOT commit, do NOT `git add` — leave the tree dirty.

---

## Task 5 — Flag plumbing: `DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK` end-to-end

**Mirrors the `enable_dep_emit` plumbing exactly across all four files.**

**Files**
- Modify: `agent.py` — `__init__` param, `self.enable_runtime_feedback`, `run_v1` call, argparse + `main()` constructor
- Modify: `multi_docker_eval_adapter.py` — env-var read, `DockerAgent(...)` call
- Modify: `run_rat_benchmark.py` — new `v1gder` arm: `--arm` choices, single env-set line, `_build_worker_argv` mapping
- Modify: `run_repo2run_benchmark.py` — new `_ARM_PRESETS["v1gder"]` entry + `--arm` choices + `_build_agent_command` forwarding

**Interfaces**
- Env var: `DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK` (`"1"` → on, all else → off)
- `DockerAgent.__init__` new param: `enable_runtime_feedback: bool = False`
- `run_v1(...)` extra kwarg: `enable_runtime_feedback=getattr(self, "enable_runtime_feedback", False)`

### Steps

- [ ] **5a. Write the failing test (smoke — assert the env-var plumbing round-trips through `DockerAgent.__init__`).**

  Create `tests/test_runtime_feedback_flag.py`:

  ```python
  """Smoke-tests that DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK plumbing is present."""
  from __future__ import annotations

  import os
  import sys
  from pathlib import Path
  import inspect

  _ROOT = Path(__file__).resolve().parents[1]
  if str(_ROOT) not in sys.path:
      sys.path.insert(0, str(_ROOT))
  _SRC = _ROOT / "src"
  if str(_SRC) not in sys.path:
      sys.path.insert(0, str(_SRC))


  def test_run_v1_accepts_enable_runtime_feedback():
      from src.envstate.orchestrator import run_v1
      sig = inspect.signature(run_v1)
      assert "enable_runtime_feedback" in sig.parameters, (
          "run_v1 must accept enable_runtime_feedback kwarg"
      )
      param = sig.parameters["enable_runtime_feedback"]
      assert param.default is False


  def test_docker_agent_has_enable_runtime_feedback_param():
      """DockerAgent.__init__ must accept enable_runtime_feedback."""
      # Import is heavyweight; just check the source text if import is slow.
      agent_py = _ROOT / "agent.py"
      src = agent_py.read_text()
      assert "enable_runtime_feedback" in src, (
          "agent.py must define/accept enable_runtime_feedback"
      )


  def test_multi_docker_eval_adapter_reads_env():
      adapter_py = _ROOT / "multi_docker_eval_adapter.py"
      src = adapter_py.read_text()
      assert "DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK" in src


  def test_run_rat_benchmark_sets_env():
      rat_py = _ROOT / "run_rat_benchmark.py"
      src = rat_py.read_text()
      assert "DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK" in src


  def test_run_repo2run_sets_env_or_flag():
      r2r_py = _ROOT / "run_repo2run_benchmark.py"
      src = r2r_py.read_text()
      assert "enable_runtime_feedback" in src
  ```

- [ ] **5b. Run it, expect FAIL.**

  ```
  python3 -m pytest tests/test_runtime_feedback_flag.py -q
  ```

  Expected: multiple assertion failures — `enable_runtime_feedback` does not yet exist in any of these files.

- [ ] **5c. Write the minimal implementation.**

  Apply the following changes:

  **`agent.py`:**

  1. In `__init__` signature (around line 237), add after `enable_dep_emit=False,`:
     ```python
             enable_runtime_feedback=False,
     ```

  2. In `__init__` body (around line 285), add after `self.enable_dep_emit: bool = bool(enable_dep_emit)`:
     ```python
         self.enable_runtime_feedback: bool = bool(enable_runtime_feedback)
     ```

  3. In the `run_v1` dispatch call (around line 1253), add after `enable_dep_emit=getattr(self, "enable_dep_emit", False),`:
     ```python
                     enable_runtime_feedback=getattr(self, "enable_runtime_feedback", False),
     ```

  4. In argparse (around line 3187, after `--enable-dep-emit`), add:
     ```python
         parser.add_argument("--enable-runtime-feedback", action="store_true",
                             help="Runtime feedback: classify ledger failures and append "
                                  "discovered requirements to the live dep-graph each cycle "
                                  "(implies --enable-dep-graph and --enable-v1).")
     ```
     And in the `DockerAgent(...)` constructor call in `main()` (around line 3241), add:
     ```python
         enable_runtime_feedback=args.enable_runtime_feedback,
     ```

  **`multi_docker_eval_adapter.py`:**

  1. After the `_enable_dep_emit` line (line 778), add:
     ```python
             _enable_runtime_feedback = os.environ.get("DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK", "").lower() in ("1", "true", "yes", "on")
     ```

  2. In the `DockerAgent(...)` constructor call (after `enable_dep_emit=_enable_dep_emit,`), add:
     ```python
                 enable_runtime_feedback=_enable_runtime_feedback,
     ```

  **`run_rat_benchmark.py`** — the `enable_dep_emit` plumbing here is ARM-driven only
  (no standalone `--enable-dep-emit` flag): line 838 sets the env purely from `args.arm`,
  and `_build_worker_argv` (line 392) maps the env back to an `--arm` slug. Mirror that
  exactly by adding a new `v1gder` arm that stacks runtime feedback onto `v1gde`. Three
  edits, ALL ASCII (no Cyrillic look-alikes):

  1. In the `--arm` choices (line 800), add `"v1gder"`:
     ```python
         parser.add_argument("--arm", choices=["arm0", "v1", "v1g", "v1gd", "v1gde", "v1gder"], default="arm0",
     ```
     and extend the help string with: `"'v1gder' = v1gde + runtime feedback (sets DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK=1)."`

  2. ONE env-set line — directly after line 838 (`os.environ["DOCKERAGENT_ENABLE_DEP_EMIT"] = ...`). The `v1gder` arm implies `v1gde`, so widen the dep-emit predicate to include it, then add the single runtime-feedback line:
     ```python
         os.environ["DOCKERAGENT_ENABLE_DEP_EMIT"] = "1" if args.arm in ("v1gde", "v1gder") else "0"
         os.environ["DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK"] = "1" if args.arm == "v1gder" else "0"
     ```
     (This is the ONLY `DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK` assignment in this file.)

  3. In `_build_worker_argv` (line 392), add a `v1gder` branch as the FIRST arm check (most-specific first, mirroring the existing if/elif ladder):
     ```python
         if os.environ.get("DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK") == "1":
             arm = "v1gder"
         elif os.environ.get("DOCKERAGENT_ENABLE_DEP_EMIT") == "1":
             arm = "v1gde"
     ```
     (i.e. insert the `if DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK` branch ABOVE the existing
     `if os.environ.get("DOCKERAGENT_ENABLE_DEP_EMIT") == "1":` and change that existing
     line to `elif`.)

  **`run_repo2run_benchmark.py`** — here `enable_dep_emit` is carried as a key in the
  `_ARM_PRESETS["v1gde"]` dict and forwarded by `_build_agent_command`. Mirror that exactly:

  1. Add a `"v1gder"` entry to `_ARM_PRESETS` (after the `"v1gde"` entry at line 3174),
     copying `"v1gde"` and adding `"enable_runtime_feedback": True`:
     ```python
         "v1gder": {
             "enable_supervisor": False, "enable_fullstate_worker": False, "fullstate_worker_prompt": False,
             "enable_envstate": False, "enable_v1": True, "enable_contract_graph": True,
             "enable_dep_graph": True, "enable_dep_emit": True, "enable_runtime_feedback": True,
             "enable_cleanroom": True,
             "max_steps": 12, "_label": "armV1gder_runtime_feedback",
         },
     ```
     and add `"v1gder"` to the `--arm` `choices` list (line 3339):
     ```python
         choices=["0", "v1", "v1g", "v1gd", "v1gde", "v1gder"],
     ```

  2. In `_build_agent_command` (line 216, after the `if getattr(args, "enable_dep_emit", False):` block), add:
     ```python
         if getattr(args, "enable_runtime_feedback", False):
             command.append("--enable-runtime-feedback")
     ```

- [ ] **5d. Run tests, expect PASS.**

  ```
  python3 -m pytest tests/test_runtime_feedback_flag.py -q
  ```

  Expected: `5 passed`

- [ ] **5e. Run the full suite to confirm no regressions.**

  ```
  python3 -m pytest tests/depgraph/ tests/test_runtime_feedback_wiring.py tests/test_runtime_feedback_flag.py -q
  ```

  Do NOT commit, do NOT `git add` — leave the tree dirty.

---

## Self-review

### Spec coverage
- §3 Scope: Tasks 1-2 (classifiers), Task 3 (ingest), Task 4 (orchestrator wiring), Task 5 (flag plumbing) — all in-scope items covered.
- §6 Priority dispatch: Task 2 implements 1→2→3→4 order; only genuine build-time/unrelated dep types are in `_IGNORED_FAILURE_TYPES` (`no_matching_distribution`, `dependency_conflict`, `syntax_requires_newer_python`). `not_dependency_related` deliberately FALLS THROUGH to the service→config→tool chain so all five classes are reachable; the dispatcher returns `None` only at the end.
- §7 Taxonomy: all five rows (Package/SystemLib/Tool/Config/Service) covered with correct ids, layers, and check_commands.
- §8 Trust / state: `ingest` sets `state=UNKNOWN` only; certify owns flips; tested in Task 3.
- §9 Annotate-or-append: tested idempotent across two passes in Task 3.
- §11 Error handling: try/except at both ingest call sites (Task 3 module + Task 4 orchestrator). `_rt_mark` advances ONLY after a successful `ingest_runtime_failures` call returns, so a mid-ingest exception re-reads (never drops) those events next cycle. Tested in Task 4.
- §12 Flag plumbing: Task 5 mirrors the `enable_dep_emit` mechanism per-file — arm-driven (`v1gder`) in `run_rat_benchmark.py`, preset-dict-driven in `run_repo2run_benchmark.py`, constructor-param in `agent.py`/`multi_docker_eval_adapter.py`.
- §13 TDD: every task follows RED→GREEN.
- Wiring placement (I2): `_runtime_ingest_phase()` is called ONCE at the top of the cycle body (after `_dep_emit_phase`, before `planner.decide`), so it fires on every branch including the early-returning planner-"done" path.

### Placeholder scan
No "TBD", "TODO", "similar to", or "..." in code blocks.

### Type consistency
- `classify_observation` → `Discovery | None` (Task 2) consumed by `ingest_runtime_failures` (Task 3).
- `ingest_runtime_failures` → `tuple[DepGraph, list[Discovery]]` consumed by `_runtime_ingest_phase` (Task 4).
- `ActionEvent.cmd` + `ActionEvent.stdout` (confirmed from `ledger.py`) used in Task 4's `obs = [(e.cmd, e.stdout) for e in new_events]`.
- `render_depgraph_planner(graph)` (confirmed from `advise.py:224`) used in Task 4.
- All node ids use the exact `ids.py` constructors confirmed above.
