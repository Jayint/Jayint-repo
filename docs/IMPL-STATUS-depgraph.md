# Implementation Status — Static-Probe Certified Dependency Graph

Status as of 2026-06-23. Package: `src/python_deps/depgraph/`. Tests: `tests/depgraph/`.
Realizes the concrete model in `docs/DESIGN-static-probe-certified-dependency-graph.md`
(section 5), the "Shared Interfaces (keystone)" in
`docs/superpowers/plans/2026-06-23-repo-to-depgraph-and-probing.md`, and the
**uv-enrichment spec** `docs/superpowers/specs/2026-06-23-uv-enriched-depgraph.md` (now fully
implemented — see "uv enrichment" below).

## Suite

- 182 tests, all passing.
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
| `roots.py` | Manifest-first root selection (reuses `python_deps.evidence`), scan-gap-filled, filtered (stdlib / Py2 shims / typing-only / denylist dropped; version specifiers carried). |
| `resolve.py` | Stage 3 — **uv.lock-driven, host-side**. Pure `parse_uv_lock` / `native_risk_from_lock` / `parse_resolver_error` + `resolve_closure` orchestrator: `Package` nodes (version, source, hash, artifact), Package->Package `requires` edges with markers, native-risk (`build_from_source`), forked-version selection by `target_python`, per-root resilience, conflict/missing diagnosis -> graph. `# via` parse kept only as degraded fallback. |
| `seed.py` | Pre-emits predicted `Tool`/`SystemLib` nodes (+`requires` edges) from native-risk via `tables.py` (`discovered_by=resolver`, `state=unknown`). |
| `probe.py` | Stage 4 — install closure (build-time gaps -> `Tool`) then import-probe (run-time gaps -> `SystemLib`); reconciles probe hits onto predicted nodes; excludes resolver-`MISSING` placeholders from the bulk install; `INSTALL_TIMEOUT=900s`. |
| `certify.py` | Stage 5 — host `check_command` certification, layer-ordered; host flips `state` only. |
| `export.py` | GraphML export matching the viewer key schema (`d0..d8`, `e0..e2`) incl. `build_from_source`, edge `marker`, conflict `constraint`. |
| `build.py` | `build_dep_graph(repo, container_executor, *, host_executor=LocalSubprocessExecutor(), target_python="3.11", target_platform=None)` — host/container split; resolution runs host-side targeted at the container, platform auto-detected via `uname -m` (fallback `aarch64`/`x86_64`-manylinux_2_28, never manylinux2014). Wires stages 1->5. |

### Node types (5 of 6)

`Test`, `Import`, `Package`, `SystemLib`, `Tool` — all built end-to-end and
host-certified. `Runtime` is modeled in the schema enum but not emitted (deferred).

### Edges

`requires` (Test->Import, Import->Package, Package->Package transitive,
Package->SystemLib, Package->Tool) — carry an optional `marker` on conditional deps.
**`conflicts_with`** (Package<->Package) is now emitted from uv resolver diagnosis with both
version bounds + evidence carried on `Edge.data`. `alternative_to` remains reserved.

### uv enrichment (spec `2026-06-23-uv-enriched-depgraph.md`, fully implemented)

- **Host/container split:** resolution runs host-side (host `uv` 0.10.4) targeted at the container
  (`--python-version` + linux platform); install/probe/certify run in the container.
- **uv.lock as primary source:** versions, transitive `requires` edges + markers, sdist/wheel
  artifacts. New `Node` fields: `build_from_source`, `artifact`, `hash`, `resolved_python`,
  `resolved_platform`, `exclude_newer`. New `Edge` fields: `marker`, read-only `data`.
- **Predicted native nodes:** `Tool`/`SystemLib` pre-emitted from native-risk + `tables.py`
  (expanded apt chains, e.g. `libGL.so.1->libgl1`, `libxcb.so.1->libxcb1`; `PACKAGE_TO_SYSTEM_DEPS`
  e.g. `psycopg2->libpq-dev`, `opencv-python->libgl1,libglib2.0-0`) with `discovered_by=resolver`;
  probe confirms/reconciles them.
- **Resilience:** per-root retry on combined-lock failure; bad roots isolated as `MISSING` with
  evidence; good roots still produce a connected graph (Run-A collapse fixed).
- **Forked versions:** `_select_applicable_packages` picks the version whose `resolution-markers`
  match `target_python` (fixes numpy 2.4.6/2.5.0 fork mis-install).

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

## Real-repo verification (2026-06-23)

All 7 richness acceptance criteria PASS on real repos — see
`docs/VERIFY-depgraph-richness.md`. Method: host `uv` 0.10.4 + real
`DockerExecutor("python:3.11-slim")`, target aarch64-manylinux_2_28/py3.11. Proof repos:
`requests` (pure: 37/37 certified, 32 pkg edges, depth 4), `opencv-python`/`cv2` (predicted
`libgl1`/`libglib2.0-0` + probe-confirmed `libxcb1`; honest installed!=importable), `psycopg2`
(`build_from_source`, predicted+reconciled `libpq-dev`), direct conflict (`requests->urllib3`
`conflicts_with` with bounds), resilience (bad root isolated, 8 good nodes salvaged). GraphML
artifacts `docs/verify-*.graphml` all parse and carry the enrichment keys. Known limitation
(out of scope, not a criterion failure): the static scan walks `examples/`/`docs/`, so flask/click
pull non-project / non-PyPI roots — next enhancement is to scope the scan to project source + tests.

## Explicitly deferred

- **Runtime nodes** (env-var needs, e.g. `DATABASE_URL`) — schema-modeled, not emitted.
- **`alternative_to` edges** — reserved enum member; no alternative-provider synthesis yet.
- **`apt-file` provider fallback** for native libs with no table hit — future enhancement.
- **LLM fallback** for unmapped imports / unknown providers — naming is curated-table + identity only.
- **Dockerfile finalize** — emitting a concrete Dockerfile / apt+pip install plan from the certified graph is out of scope for this plan.
- **Scan scoping** — exclude `examples/`/`docs/`/`build/` from the static scan (see verification limitation).
