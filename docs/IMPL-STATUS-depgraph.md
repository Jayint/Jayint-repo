# Implementation Status — Static-Probe Certified Dependency Graph

Status as of 2026-06-23. Package: `src/python_deps/depgraph/`. Tests: `tests/depgraph/`.
Realizes the concrete model in `docs/DESIGN-static-probe-certified-dependency-graph.md`
(section 5) and the "Shared Interfaces (keystone)" in
`docs/superpowers/plans/2026-06-23-repo-to-depgraph-and-probing.md`.

## Suite

- 87 tests, all passing.
- Run:
  ```bash
  cd /Users/john/john-planner-v3 && \
  /Users/john/john-planner-v1/.venv/bin/python -m pytest tests/depgraph/ -q
  ```
- Unit tests use the in-memory `FakeExecutor` from `tests/depgraph/conftest.py`
  (no Docker / network / uv). A `src`-on-`sys.path` shim lives in that conftest.

## Implemented

### Modules

| Module | Role |
|---|---|
| `schema.py` | Frozen `Node`/`Edge`/`DepGraph` + enums (`NodeType`, `EdgeType`, `State`, `DiscoveredBy`, `Layer`), `Attempt`. Every mutation returns a NEW object; `with_edge` validates `EDGE_RULES`. |
| `executor.py` | `Executor` protocol + `CommandResult`. Real impls inject a `DockerExecutor` (out of scope for unit tests). |
| `scan.py` | Stage 1 — static import scan -> `Test` + `Import` nodes (reuses `python_deps.import_graph.scan_imports`; stdlib excluded). |
| `naming.py` | Stage 2 — `Import` -> PyPI distribution roots (curated table in `import_mapping.py` + identity fallback). |
| `tables.py` | Curated Debian/Ubuntu provider tables for native/system needs. |
| `ids.py` | Stable node-id constructors (`TEST_NODE_ID`, `import_id`, `package_id`, `syslib_id`, `tool_id`). |
| `resolve.py` | Stage 3 — `uv pip compile` closure (shelled through Executor) -> `Package` nodes + `requires` edges, incl. Package->Package edges parsed from uv `# via` annotations. |
| `probe.py` | Stage 4 — install closure (build-time gaps -> `Tool`) then import-probe (run-time gaps -> `SystemLib`). |
| `certify.py` | Stage 5 — host `check_command` certification, layer-ordered; host flips `state` only. |
| `export.py` | GraphML export matching the viewer key schema (`d0..d7`, `e0`). |
| `build.py` | `build_dep_graph(repo_path, executor, *, base_python="3.11")` — wires stages 1->5, restamps `discovered_cycle` per stage. |

### Node types (5 of 6)

`Test`, `Import`, `Package`, `SystemLib`, `Tool` — all built end-to-end and
host-certified. `Runtime` is modeled in the schema enum but not emitted (deferred).

### Edges

`requires` only (Test->Import, Import->Package, Package->Package transitive,
Package->SystemLib, Package->Tool). `alternative_to` and `conflicts_with` exist in
the `EdgeType` enum but are reserved/not emitted in this plan.

### Stages / certification axis

All five pipeline stages run in order; discovery order differs from execution
order (probe discovers a SystemLib after install, certification runs in layer
order). `state` (`unknown`/`missing`/`satisfied`) is flipped ONLY by a host-run
`check_command`, kept separate from the `attempts` action axis (design 3.1).

## Locked decisions honored

1. Resolver = `uv` binary via `uv pip compile`, shelled through the Executor (never imported).
2. Flat `Package` set + Package->Package edges parsed from uv `# via` annotation comments; Import->Package `requires` always emitted.
3. Default base image `python:3.11-slim` (`base_python="3.11"`).

## End-to-end / viewer compatibility

`build_dep_graph` -> `to_graphml` was exercised on a tiny fixture repo (imports
`cv2`/`PIL`/`psycopg2`) producing all 5 implemented node types. The GraphML:
parses (xml.dom.minidom), its key schema is byte-identical to
`docs/sample-dependency-graph.graphml` (`d0..d7` + `e0`), every `<data key>`
references a declared key — so it drops straight into
`docs/sample-dependency-graph-visualization.html`.

## Explicitly deferred

- **Runtime nodes** (env-var needs, e.g. `DATABASE_URL`) — schema-modeled, not emitted.
- **`conflicts_with` / `alternative_to` edges** — reserved enum members; no resolver-conflict or alternative-provider synthesis yet.
- **Docker e2e gated test** — real `DockerExecutor` + `python:3.11-slim` run is not part of the unit suite (all units use `FakeExecutor`).
- **LLM fallback** for unmapped imports / unknown providers — naming is curated-table + identity only.
- **Dockerfile finalize** — emitting a concrete Dockerfile / apt+pip install plan from the certified graph is out of scope for this plan.
