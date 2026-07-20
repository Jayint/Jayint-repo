# Spec: uv-enriched dependency graph

**Date:** 2026-06-23 · **Branch:** john-planner-v3 · **Package:** `src/python_deps/depgraph/`
**Builds on:** `docs/DESIGN-static-probe-certified-dependency-graph.md` §5, the implemented Builder
(`docs/IMPL-STATUS-depgraph.md`), and the real-run findings + uv deep-dive in this session.

## Goal

Make the graph **rich enough that an agent can diagnose/debug build issues by exploring the graph
instead of re-reading logs**. Concretely: real transitive package edges, proactive native-build
risk, real conflict edges, container-accurate pins, and resilient partial graphs — driven by `uv`.

## Architecture change (load-bearing)

**Resolution moves host-side; only install/probe/certify run in the container.**

- `uv` resolution targets the container (`--python-version`, target platform) but RUNS on the host
  (cross-platform resolve needs no container interpreter — verified). This fixes the `UV_BIN`
  host-path bug and the single-executor assumption.
- `build_dep_graph(repo, container_executor, *, host_executor=LocalSubprocessExecutor(),
  target_python="3.11", target_platform=None)`. `target_platform` defaults to the container's arch
  (detect once via the container, e.g. `uname -m`/`sysconfig.get_platform()`); fall back to
  `aarch64-manylinux_2_28` on Apple-silicon Docker, `x86_64-manylinux_2_28` on amd64. Never
  `manylinux2014` (known trap — silently downgrades e.g. numpy).
- Container only needs `python` + `pip` (slim has both). No `uv` in the container.

## Root selection (fixes the Run-A collapse)

`roots.py`: **manifest-first**, scan-gap-filled, filtered.
1. Parse declared deps from manifests (reuse `python_deps.evidence.collect_python_dependency_evidence`).
2. Add mapped scanned imports (`naming.package_roots`) only for imports not already covered.
3. **Filter non-distributions** before handing to uv: drop stdlib (any Python version), known Py2
   shims (`StringIO`, `cStringIO`, `BaseHTTPServer`, `SimpleHTTPServer`, …), typing-only
   (`_typeshed`), and an explicit denylist. A name that isn't plausibly a PyPI dist never becomes a root.

## Resolver v2 (`resolve.py` rewrite)

Primary source = **`uv.lock`** (richest single artifact: nodes + versions + edges + markers + artifacts).

1. Create a throwaway uv project in a temp dir (write a minimal `pyproject.toml` with the roots, or
   `uv init` + `uv add`), then `uv lock` with `--python <target_python>`. Parse `uv.lock` with
   `tomllib`:
   - `[[package]]` → **Package node** (name, version, `source`).
   - `dependencies = [{name, marker?}]` → **Package→Package `requires` edges** (parent→child),
     with the optional `marker` carried on the edge.
   - `sdist = {...}` / `wheels = [{url, ...}]` → **native-risk**: a package with an `sdist` and **no
     wheel matching `target_platform`** ⇒ `build_from_source=True`. Capture chosen artifact + hash.
2. **Targeting/provenance:** record `resolved_python`, `resolved_platform`, `exclude_newer` (optional,
   for reproducibility), and per-package hash on the Package node.
3. **Resilience (no all-or-nothing):** if the combined lock fails, parse the error and retry without
   the offending root(s) so good roots still produce a graph; mark the dropped root.
4. **Conflict/failure → graph (stderr parse):**
   - `X was not found in the package registry` → `Package(state=missing, evidence)`.
   - `there is no version of X==Y` → missing, evidence.
   - `you require A<x and B>=x` / `P depends on D>=x` → **`conflicts_with` edge** between the
     Packages, with both version bounds carried on the edge `data`.
   - python-version incompat → conflict edge to the interpreter need with the floor.

`# via` annotation parsing is kept only as a degraded fallback if `uv.lock` is unavailable.

## Schema additions (`schema.py`)

- `Edge.marker: str | None` (conditional-dep marker on a `requires` edge).
- `Edge.data: dict` (e.g. conflict version bounds) — or typed fields `constraint_src`/`constraint_dst`.
- `Node` new fields: `build_from_source: bool | None`, `artifact: str | None` (wheel/sdist filename),
  `hash: str | None`, `resolved_python: str | None`, `resolved_platform: str | None`.
- `EDGE_RULES`: allow `conflicts_with` between `Package`↔`Package` (and Package↔interpreter where used).
- **Predicted Tool/SystemLib nodes:** emitted from native-risk at resolve time with
  `discovered_by=RESOLVER, state=UNKNOWN` (a *prediction*); the probe stage later confirms them
  (`discovered_by` stays the discovery origin; `state` flips via the certifier only). The
  RESOLVER-vs-PROBE `discovered_by` distinguishes predicted from observed.

## Predicted native nodes (`tables.py` expansion + use)

- Expand `NATIVE_LIB_TO_APT` with the real chains, incl. opencv: `libGL.so.1→libgl1`,
  `libglib-2.0.so.0/libgthread-2.0.so.0→libglib2.0-0`, `libSM.so.6→libsm6`, `libXext.so.6→libxext6`,
  `libXrender.so.1→libxrender1`, `libxcb.so.1→libxcb1`.
- Add a **package→system-deps** table for proactive prediction: `psycopg2→libpq-dev`,
  `mysqlclient→default-libmysqlclient-dev`, `lxml→libxml2-dev,libxslt1-dev`,
  `Pillow→libjpeg-dev,zlib1g-dev`, `opencv-python→libgl1,libglib2.0-0`.
- On a `build_from_source` (or known-native) Package, pre-emit the predicted `Tool`/`SystemLib` nodes
  + `requires` edges. Probe miss with no table hit → leave node `missing` with evidence (note: an
  `apt-file search` fallback is a future enhancement, not in this pass).

## Export (`export.py`)

GraphML must carry the new info so the viewer shows it: edge `marker`, node `build_from_source`,
`conflicts_with` edges (distinct relation), and predicted vs observed (via `discovered_by`). Add the
new `<key>`s; keep the existing keys/viewer compatible.

## Richness acceptance criteria (what verification must prove on REAL repos)

A graph is "rich enough" when, on real repos, ALL hold:
1. **Transitive structure:** Package→Package `requires` edges present and connected from roots
   (not a flat set); count > 0 on any repo with transitive deps.
2. **No degenerate collapse:** a normal repo never yields an empty Package layer (per-root resilience).
3. **Native-risk predicted:** sdist-only / known-native packages carry `build_from_source` and have
   predicted `Tool`/`SystemLib` nodes BEFORE the build — and the probe then confirms the real missing
   lib with a NON-EMPTY fix candidate (e.g. opencv → `libgl1`/`libxcb1`).
4. **Conflicts visible:** a conflicting input produces `conflicts_with` edges with version bounds +
   evidence, not a silent empty graph.
5. **Honest certification:** installed ≠ importable is preserved (e.g. `opencv-python` Package
   `satisfied` while `cv2` Import `missing` until the lib is present).
6. **Container-accurate:** Package versions match what the container would install (resolved for the
   target platform/python).
7. **Explorable export:** `to_graphml` renders in the viewer with edges, markers, native-risk, and
   conflicts visible.

## Still explicitly deferred (out of scope here)
Runtime nodes (env/service), LLM fallback for unmapped imports/providers, `apt-file` provider
fallback, Dockerfile finalize/clean-rebuild promotion, the agent loop.
