# Services Tier — In-Image Action Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote a *confirmed* SERVICE node from passive advisory to a scheduled, host-certified, in-image-provisioned obligation (LLM starts Postgres in the agent's container; the host certifies reachability), reproduced faithfully in the scored eval, behind a default-off arm `v1gsps`.

**Architecture:** A confirmed Postgres service gains (1) a `SystemLib(postgresql)` prereq node + a `Service→SystemLib` requires edge (the apt install bakes for free via the existing emit path), (2) a loopback `check_command` rewritten at attach time, and (3) a `data["start_recipe"]` the scheduler hands the LLM. The certify and scheduler SERVICE exclusions are lifted *only* for confirmed services *only* under the arm. The agent writes a `confirmed_in_image_services` handoff field; the eval composes a root-wrapped `start; wait; createdb(fatal); pytest` sequence from that field into the test wrapper. The scheduler's done-branch refuses success unless the service is host-certified SATISFIED.

**Tech Stack:** Python 3.11, pytest, frozen dataclasses (`schema.py`), stdlib only. Docker (eval/sandbox) exercised at e2e only; unit tests use the existing `FakeExecutor`.

## Global Constraints

- **Immutability:** every "mutation" returns a NEW `DepGraph`/`Node` (frozen dataclasses; `with_node`/`with_edge`/`replace`).
- **Certification invariant:** a node's `state` is flipped ONLY by running its `check_command`; never LLM-declared, never action-implied. No new `State` value.
- **Off-state byte-identity:** every new branch is gated on `DOCKERAGENT_ENABLE_SERVICE_PROVISION` (arm `v1gsps`) via a default-`False` parameter/flag. Arm OFF ⇒ byte-identical world-model, advisory, Dockerfile, eval wrapper, certify, schedule, sandbox launch.
- **Confirmed-only:** only `data["service_confidence"] == "confirmed"` services are promoted/certified/scheduled. **Inferred** services stay UNKNOWN, advisory-only, skip-guarded (may be mocked).
- **In-image only:** no separate-container sidecar, no shared `--network`. Postgres runs in the agent's own container; the eval reproduces it in the single test container.
- **Anti-hollow strengthened:** for a repo where a confirmed service was promoted, the scheduler accepts `done` only if that service node is SATISFIED; `createdb` is FATAL in the eval wrapper (no `|| true`).
- **Arm:** `v1gsps`, default off, layered on `v1gsp` (graph-scheduler + runtime-pin + service-provision).
- **Postgres-as-non-root:** every start/createdb command runs the daemon as the `postgres` user (`runuser -u postgres --`); the container runs as uid 0.
- **TDD:** failing test first, watch it fail, implement minimally, watch it pass. Use `python3 -m pytest`.

**In scope:** schema edge legality, in-image provisioning attach (SystemLib + edge + loopback + recipe), arm-gated certify/schedule lifts, advisory render, synthesizer build/runtime split, eval wrapper composition, handoff field, sandbox alias, done-gate, off-state test.
**Out of scope (deferred — spec §12):** separate-container sidecars; `Service→Service` ordering; non-Postgres hardening; verify-sub-suite (`pytest -m unit`); role/password auth depth (spec §16 Q2).

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/python_deps/depgraph/schema.py` | `EDGE_RULES`: allow `Service` as a `requires` source | 1 |
| `src/python_deps/depgraph/service_scan.py` | `attach_in_image_provisioning` + start-recipe builder + db-name parse | 2 |
| `src/python_deps/depgraph/build.py` | thread `enable_service_provision`; call attach after `scan_services` | 3 |
| `src/python_deps/depgraph/certify.py` | `allow_service_certify` param; loopback probe for confirmed SERVICE; services layer order | 4 |
| `src/envstate/depgraph_live.py` | read arm flag; pass `allow_service_certify` + services layer order to `certify_refresh` | 4 |
| `src/python_deps/depgraph/schedule.py` | `allow_services` param in `_is_actionable`/`scheduler_frontier` | 5 |
| `src/envstate/graph_scheduler.py` | pass `allow_services`; render recipe into facts; done-branch requires certified service | 5, 6 |
| `src/python_deps/depgraph/advise.py` | SERVICES block renders prereq + start recipe when provisioning | 7 |
| `src/synthesizer.py` | `_is_runtime_service_segment` matches `pg_ctlcluster`/`createdb`/`createuser` | 8 |
| `run_repo2run_benchmark.py` | `compose_in_image_service_commands`; prepend to wrapper; field-driven `--add-host`; `v1gsps` preset | 9, 11 |
| `agent.py` | write `confirmed_in_image_services` in run summary (gated, satisfied-only) | 10 |
| `run_rat_benchmark.py` | `v1gsps` arm choice + `DOCKERAGENT_ENABLE_SERVICE_PROVISION` ladder | 11 |
| `src/sandbox.py` | `extra_hosts={"postgres":"127.0.0.1"}` at launch when arm on | 12 |

---

### Task 1: Schema — allow `Service` as a `requires` source

**Files:**
- Modify: `src/python_deps/depgraph/schema.py:88-92` (`EDGE_RULES["requires"]` source set)
- Test: `tests/depgraph/test_schema.py`

**Interfaces:**
- Produces: `Service → SystemLib` (and `Service → Tool`) `requires` edges are now legal at `DepGraph.with_edge`. Source set becomes `{"Test","Project","Import","Package","Service"}`. No other relation changes.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schema.py`:

```python
def test_service_may_require_systemlib():
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, Edge, EdgeType,
    )
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN)
    sysl = Node(id="syslib:postgresql", type=NodeType.SYSTEM_LIB, name="postgresql",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN)
    g = DepGraph().with_node(svc).with_node(sysl)
    # Service -> SystemLib (requires) must be legal now (in-image: the server
    # binary IS in our apt closure — design §3/§5).
    g2 = g.with_edge(Edge(src="service:postgres", dst="syslib:postgresql",
                          relation=EdgeType.REQUIRES, origin="service"))
    assert any(e.src == "service:postgres" and e.dst == "syslib:postgresql"
               for e in g2.edges)


def test_service_still_illegal_as_conflicts_source():
    # Only the `requires` source set is widened; conflicts_with is unchanged.
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, Edge, EdgeType,
    )
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN)
    pkg = Node(id="pkg:psycopg2", type=NodeType.PACKAGE, name="psycopg2",
               layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER)
    g = DepGraph().with_node(svc).with_node(pkg)
    import pytest
    with pytest.raises(ValueError):
        g.with_edge(Edge(src="service:postgres", dst="pkg:psycopg2",
                         relation=EdgeType.CONFLICTS_WITH))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_schema.py -k "service_may_require or conflicts_source" -v`
Expected: `test_service_may_require_systemlib` FAILS with `ValueError: illegal requires source type 'Service'`.

- [ ] **Step 3: Write minimal implementation**

In `schema.py`, change the `requires` source frozenset (line 89) to add `"Service"`:

```python
EDGE_RULES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "requires": (
        frozenset({"Test", "Project", "Import", "Package", "Service"}),
        frozenset({"Project", "Import", "Package", "SystemLib", "Tool", "Runtime",
                   "Platform", "Service", "Config", "DataAsset"}),
    ),
    "conflicts_with": (
        frozenset({"Package"}),
        frozenset({"Package"}),
    ),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_schema.py -v`
Expected: PASS (both new tests + no regression).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema.py
git commit -m "feat(depgraph): allow Service as a requires source (in-image server-binary edge)"
```

---

### Task 2: `attach_in_image_provisioning` — SystemLib prereq, loopback probe, start recipe

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py`
- Test: `tests/depgraph/test_service_scan.py`

**Interfaces:**
- Consumes: `Service` nodes from `scan_services`; `ids.syslib_id`; `Task 1` edge legality.
- Produces:
  - `service_db_from_url(value: str) -> str | None` — the database name (URL path, stripped) or None.
  - `postgres_start_recipe(port: int, db: str | None) -> dict` — `{"system_package","start","wait","createdb","certify","port","db"}`, all root-safe, version-resolved at runtime.
  - `attach_in_image_provisioning(graph: DepGraph, *, enabled: bool) -> DepGraph` — for each **confirmed** `postgres` SERVICE node: adds a `SystemLib(postgresql)` node (`chosen_fix="apt:postgresql"`, `check_command="command -v pg_ctlcluster"`), a `Service→SystemLib` requires edge, rewrites the Service `check_command` to `pg_isready -h 127.0.0.1 -p <port>`, and sets `data["start_recipe"]`. `enabled=False` ⇒ returns the graph unchanged. Returns a NEW graph.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_service_scan.py`:

```python
from python_deps.depgraph.service_scan import (
    attach_in_image_provisioning, postgres_start_recipe, service_db_from_url,
)
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, DiscoveredBy, State, EdgeType,
)
from python_deps.depgraph.ids import service_id, syslib_id


def _confirmed_pg_graph(port=5432, db=None):
    data = {"service_confidence": "confirmed", "image": "postgres:14",
            "host": "postgres", "port": port}
    if db:
        data["db"] = db
    svc = Node(id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=State.UNKNOWN,
               check_command=f"pg_isready -h postgres -p {port}",
               data=data)
    return DepGraph().with_node(svc)


def test_service_db_from_url():
    assert service_db_from_url("postgres://u:p@db:5432/appdb") == "appdb"
    assert service_db_from_url("postgresql://h/only_db") == "only_db"
    assert service_db_from_url("postgres://h:5432/") is None
    assert service_db_from_url("not-a-url") is None


def test_recipe_is_root_safe_and_version_resolved():
    r = postgres_start_recipe(5432, "appdb")
    assert "runuser -u postgres" in r["start"]
    assert "/etc/postgresql" in r["start"]            # runtime version resolution
    assert "pg_isready -h 127.0.0.1 -p 5432" == r["certify"]
    assert "createdb" in r["createdb"] and "appdb" in r["createdb"]
    assert "|| true" not in r["createdb"]             # FATAL
    r2 = postgres_start_recipe(5432, None)
    assert r2["createdb"] is None                     # no name -> no createdb line


def test_attach_disabled_is_noop():
    g = _confirmed_pg_graph()
    assert attach_in_image_provisioning(g, enabled=False) is g or \
        attach_in_image_provisioning(g, enabled=False).to_dict() == g.to_dict()


def test_attach_adds_systemlib_edge_loopback_and_recipe():
    g = attach_in_image_provisioning(_confirmed_pg_graph(db="appdb"), enabled=True)
    sysl = g.get(syslib_id("postgresql"))
    assert sysl is not None and sysl.type is NodeType.SYSTEM_LIB
    assert sysl.chosen_fix == "apt:postgresql"
    assert any(e.src == service_id("postgres") and e.dst == syslib_id("postgresql")
               and e.relation is EdgeType.REQUIRES for e in g.edges)
    svc = g.get(service_id("postgres"))
    assert svc.check_command == "pg_isready -h 127.0.0.1 -p 5432"   # loopback rewrite
    assert svc.data["start_recipe"]["system_package"] == "postgresql"
    assert "127.0.0.1" in svc.data["start_recipe"]["certify"]


def test_attach_skips_inferred_service():
    data = {"service_confidence": "inferred", "host": "postgres", "port": 5432}
    svc = Node(id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.RESOLVER,
               state=State.UNKNOWN, check_command="pg_isready -h postgres -p 5432",
               data=data)
    g = attach_in_image_provisioning(DepGraph().with_node(svc), enabled=True)
    assert g.get(syslib_id("postgresql")) is None          # inferred not promoted
    assert g.get(service_id("postgres")).check_command == "pg_isready -h postgres -p 5432"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_service_scan.py -k "attach or recipe or db_from_url" -v`
Expected: FAIL — `ImportError: cannot import name 'attach_in_image_provisioning'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/python_deps/depgraph/service_scan.py` (extend the existing imports with `syslib_id`; `urlparse` is already imported):

```python
from python_deps.depgraph.ids import service_id, syslib_id


def service_db_from_url(value: str) -> str | None:
    """The database name from a service URL path (``postgres://h/appdb`` -> ``appdb``)."""
    if not value or "://" not in value:
        return None
    try:
        path = urlparse(value).path
    except ValueError:
        return None
    name = (path or "").lstrip("/").strip()
    return name or None


def postgres_start_recipe(port: int, db: str | None) -> dict:
    """Root-safe, runtime-version-resolved in-image Postgres start recipe.

    The cluster version is resolved at runtime from ``/etc/postgresql`` so no
    static ``<ver>`` (or base-image lookup) is needed; the daemon runs as the
    ``postgres`` user (the container is uid 0 and Postgres refuses to run as root).
    """
    start = (
        'PG_VER="$(ls /etc/postgresql 2>/dev/null | head -1)"; '
        'runuser -u postgres -- pg_ctlcluster "$PG_VER" main start'
    )
    wait = (
        f"for i in $(seq 1 30); do pg_isready -h 127.0.0.1 -p {port} && break; "
        "sleep 1; done"
    )
    createdb = f"runuser -u postgres -- createdb {db}" if db else None
    return {
        "system_package": "postgresql",
        "start": start,
        "wait": wait,
        "createdb": createdb,                    # FATAL when present (no `|| true`)
        "certify": f"pg_isready -h 127.0.0.1 -p {port}",
        "port": port,
        "db": db,
    }


def attach_in_image_provisioning(graph: DepGraph, *, enabled: bool) -> DepGraph:
    """Promote each CONFIRMED postgres SERVICE to an in-image obligation.

    Adds a ``SystemLib(postgresql)`` prereq (emit bakes ``apt install postgresql``),
    a ``Service->SystemLib`` requires edge, rewrites the service ``check_command``
    to a loopback probe, and attaches ``data["start_recipe"]``. ``enabled=False``
    returns the graph unchanged (off-state byte-identity). NEW graph.
    """
    if not enabled:
        return graph
    new = graph
    for svc in [n for n in graph.nodes if n.type is NodeType.SERVICE]:
        if svc.name != "postgres" or svc.data.get("service_confidence") != "confirmed":
            continue
        port = svc.data.get("port") or 5432
        db = svc.data.get("db") or service_db_from_url(svc.data.get("bound_config_url", ""))
        recipe = postgres_start_recipe(port, db)
        sysl_id = syslib_id("postgresql")
        if new.get(sysl_id) is None:
            new = new.with_node(Node(
                id=sysl_id, type=NodeType.SYSTEM_LIB, name="postgresql",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN, check_command="command -v pg_ctlcluster",
                fix_candidates=("apt:postgresql",), chosen_fix="apt:postgresql",
                evidence="in-image server for confirmed service postgres",
                provenance="service provision",
            ))
        # Rewrite the service node: loopback probe + recipe in data.
        new_data = dict(svc.data)
        new_data["start_recipe"] = recipe
        new = new.with_node(replace(
            svc, check_command=recipe["certify"], data=new_data,
        ))
        new = new.with_edge(Edge(src=svc.id, dst=sysl_id,
                                 relation=EdgeType.REQUIRES, origin="service"))
    return new
```

Add `from dataclasses import replace` to the imports at the top of `service_scan.py`.

> Note on `bound_config_url`: the discovery slice stores only `bound_config` (the var name), not the URL. Capturing the db name from the URL is best-effort here; when absent, `db` is `None` and `createdb` is omitted (the default `postgres` DB + trust auth covers suites that create their own DB). Auth/role depth is spec §16 Q2 (out of scope).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_service_scan.py -v`
Expected: PASS (all new tests + no regression to the existing service_scan suite).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py tests/depgraph/test_service_scan.py
git commit -m "feat(depgraph): attach_in_image_provisioning (SystemLib prereq + loopback probe + start recipe)"
```

---

### Task 3: Wire attach into the build pipeline (arm-gated param)

**Files:**
- Modify: `src/python_deps/depgraph/build.py:208-216` (`build_dep_graph` signature) and `:303` (after `scan_services`)
- Test: `tests/depgraph/test_build.py`

**Interfaces:**
- Consumes: `attach_in_image_provisioning` (Task 2).
- Produces: `build_dep_graph(..., enable_service_provision: bool = False)`; when True, the graph carries the `SystemLib(postgresql)` + edge + recipe for a confirmed postgres service. Default False ⇒ byte-identical.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_build.py` (use the file's existing `FakeExecutor`/`_r` helpers, mirroring `test_build_includes_service_nodes`):

```python
def test_build_attaches_provisioning_when_enabled(tmp_path):
    from conftest import FakeExecutor  # type: ignore
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "0"\n')
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "db.py").write_text("import psycopg2\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:14\n")

    from python_deps.depgraph.build import build_dep_graph
    from python_deps.depgraph.ids import syslib_id
    ex = FakeExecutor(default=_r(returncode=1, stderr="x"))
    g_on = build_dep_graph(str(tmp_path), ex, host_executor=ex, target_python="3.11",
                           enable_service_provision=True)
    assert g_on.get(syslib_id("postgresql")) is not None      # promoted
    g_off = build_dep_graph(str(tmp_path), ex, host_executor=ex, target_python="3.11")
    assert g_off.get(syslib_id("postgresql")) is None          # default: byte-identical
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_build.py::test_build_attaches_provisioning_when_enabled -v`
Expected: FAIL — `TypeError: build_dep_graph() got an unexpected keyword argument 'enable_service_provision'`.

- [ ] **Step 3: Write minimal implementation**

In `build.py`, add the import next to the `scan_services` import (line 64):

```python
from python_deps.depgraph.service_scan import scan_services, attach_in_image_provisioning
```

Add the parameter to `build_dep_graph` (after `exclude_newer` at line 215):

```python
    exclude_newer: str | None = None,
    enable_service_provision: bool = False,
```

Insert the attach call immediately after the `scan_services` call (line 303), before the `resolver_ids` computation:

```python
    graph = scan_services(repo_path, graph)
    # Stage 3e — Service action layer (arm v1gsps): promote confirmed services to
    # in-image obligations (SystemLib prereq + loopback probe + start recipe).
    # Off-state default keeps this byte-identical (design 2026-06-27 §4/§9).
    graph = attach_in_image_provisioning(graph, enabled=enable_service_provision)
    resolver_ids = {n.id for n in graph.nodes} - pre_resolve_ids
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_build.py -v`
Expected: PASS (new test + no regression).

- [ ] **Step 5: Wire the env flag at the live-path caller**

Grep for live-path callers that must pass the flag:

Run: `grep -rn "build_dep_graph(" src/ | grep -v "def build_dep_graph"`

For each caller on the **live** path (the orchestrator / `depgraph_live` graph build — NOT the scratch advisory `build_advisory_for_repo`, which stays off), pass `enable_service_provision=os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"`. Add `import os` if absent. (The scratch advisory build leaves it default-False — services aren't certifiable in the throwaway container.)

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_build.py src/envstate/
git commit -m "feat(depgraph): wire attach_in_image_provisioning into build_dep_graph (arm-gated)"
```

---

### Task 4: Certify — loopback probe for confirmed services (arm-gated), live wiring

**Files:**
- Modify: `src/python_deps/depgraph/certify.py:25-34` (`_LAYER_ORDER`), `:37-77` (`certify`), `:80-95` (`certify_all`)
- Modify: `src/envstate/depgraph_live.py:38-47` (`certify_refresh`)
- Test: `tests/depgraph/test_certify.py`, `tests/envstate/test_depgraph_live.py` (or the existing certify_refresh test file)

**Interfaces:**
- Produces:
  - `certify(graph, node_id, executor, cycle=0, *, allow_service_certify=False)` — a **confirmed** SERVICE node is certified by running its (loopback) `check_command` when `allow_service_certify=True`; otherwise SERVICE nodes stay UNKNOWN (current behavior).
  - `_SERVICE_LAYER_ORDER` = `_LAYER_ORDER + (Layer.SERVICES,)` (SERVICES last — after PIP/SYSTEM, after TESTS is acceptable since the scheduler, not certify order, gates test execution; placing it last keeps the prereq-before-service ordering via the SATISFIED-dep check in scheduling).
  - `certify_all(graph, executor, cycle=0, *, allow_service_certify=False, layer_order=_LAYER_ORDER)`.
  - `certify_refresh(graph, exec_readonly, cycle, *, allow_service_certify=False)` reads nothing new itself; the **caller** in the orchestrator passes the arm flag.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_certify.py`:

```python
def _confirmed_service(check="pg_isready -h 127.0.0.1 -p 5432"):
    from python_deps.depgraph.schema import Node, NodeType, Layer, DiscoveredBy, State
    return Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
                layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN, check_command=check,
                data={"service_confidence": "confirmed"})


def test_confirmed_service_certified_when_allowed():
    from python_deps.depgraph.schema import DepGraph, State
    from python_deps.depgraph.certify import certify

    class R:
        def __init__(self, ok): self.ok = ok; self.stdout = ""; self.stderr = ""
    class Ex:
        def __init__(self, ok): self.ok = ok; self.calls = []
        def run(self, cmd): self.calls.append(cmd); return R(self.ok)

    g = DepGraph().with_node(_confirmed_service())
    ex = Ex(ok=True)
    out = certify(g, "service:postgres", ex, allow_service_certify=True)
    assert out.get("service:postgres").state is State.SATISFIED
    assert ex.calls == ["pg_isready -h 127.0.0.1 -p 5432"]


def test_confirmed_service_unknown_when_not_allowed():
    from python_deps.depgraph.schema import DepGraph, State
    from python_deps.depgraph.certify import certify

    class Ex:
        def __init__(self): self.calls = []
        def run(self, cmd): self.calls.append(cmd); return type("R",(),{"ok":True,"stdout":"","stderr":""})()

    g = DepGraph().with_node(_confirmed_service())
    ex = Ex()
    out = certify(g, "service:postgres", ex)         # default: not allowed
    assert out.get("service:postgres").state is State.UNKNOWN
    assert ex.calls == []                             # probe never run (off-state)


def test_inferred_service_stays_unknown_even_when_allowed():
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.certify import certify

    class Ex:
        def __init__(self): self.calls = []
        def run(self, cmd): self.calls.append(cmd); return type("R",(),{"ok":True,"stdout":"","stderr":""})()

    inferred = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
                    layer=Layer.SERVICES, discovered_by=DiscoveredBy.RESOLVER,
                    state=State.UNKNOWN, check_command="pg_isready -h 127.0.0.1 -p 5432",
                    data={"service_confidence": "inferred"})
    out = certify(DepGraph().with_node(inferred), "service:postgres", Ex(),
                  allow_service_certify=True)
    assert out.get("service:postgres").state is State.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_certify.py -k "confirmed_service or inferred_service" -v`
Expected: FAIL — `certify()` got an unexpected keyword `allow_service_certify` (and/or the SERVICE skip-guard keeps it UNKNOWN).

- [ ] **Step 3: Write minimal implementation**

In `certify.py`, add the services layer order after `_LAYER_ORDER` (line 34):

```python
# Services join the walk LAST (after the server binary/SystemLib is installed);
# only used on the live in-image path (arm v1gsps). Never used off-arm.
_SERVICE_LAYER_ORDER: tuple[Layer, ...] = _LAYER_ORDER + (Layer.SERVICES,)
```

Replace the SERVICE skip-guard (lines 59-63) and add the param to `certify`:

```python
def certify(
    graph: DepGraph,
    node_id: str,
    executor: Executor,
    cycle: int = 0,
    *,
    allow_service_certify: bool = False,
) -> DepGraph:
    ...
    node = graph.get(node_id)
    if node is None or not node.check_command:
        return graph
    # Services are reachability-certified only on the live in-image path (arm
    # v1gsps) and only when CONFIRMED. Off-arm / inferred: stay UNKNOWN (design
    # §4.3). The scratch container cannot host the daemon, so the scratch
    # certify_all call leaves allow_service_certify=False.
    if node.type is NodeType.SERVICE:
        if not (allow_service_certify
                and node.data.get("service_confidence") == "confirmed"):
            return graph
    # (existing rc-based body unchanged below)
    result = executor.run(node.check_command)
    ...
```

Add the params to `certify_all` (line 80) and pass them through:

```python
def certify_all(
    graph: DepGraph,
    executor: Executor,
    cycle: int = 0,
    *,
    allow_service_certify: bool = False,
    layer_order: tuple[Layer, ...] = _LAYER_ORDER,
) -> DepGraph:
    new = graph
    for layer in layer_order:
        node_ids = [n.id for n in new.nodes if n.layer is layer]
        for node_id in node_ids:
            new = certify(new, node_id, executor, cycle=cycle,
                          allow_service_certify=allow_service_certify)
    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_certify.py -v`
Expected: PASS.

- [ ] **Step 5: Wire `certify_refresh` (live, arm-gated)**

In `src/envstate/depgraph_live.py`, thread the flag through `certify_refresh`:

```python
def certify_refresh(graph, exec_readonly, cycle: int, *, allow_service_certify: bool = False):
    if graph is None or not graph.nodes or exec_readonly is None:
        return graph
    from python_deps.depgraph.certify import _SERVICE_LAYER_ORDER, _LAYER_ORDER
    order = _SERVICE_LAYER_ORDER if allow_service_certify else _LAYER_ORDER
    return certify_all(graph, _ReadonlyExecAdapter(exec_readonly), cycle=cycle,
                       allow_service_certify=allow_service_certify, layer_order=order)
```

Then grep the orchestrator for the `certify_refresh(` call site and pass
`allow_service_certify=os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"`:

Run: `grep -rn "certify_refresh(" src/`

- [ ] **Step 6: Add a live-wiring test**

Add to the depgraph_live test file (mirror its existing `certify_refresh` test):

```python
def test_certify_refresh_certifies_confirmed_service_when_allowed():
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from src.envstate.depgraph_live import certify_refresh
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=State.UNKNOWN, check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed"})
    g = DepGraph().with_node(svc)
    out = certify_refresh(g, lambda cmd: (0, "accepting connections"), cycle=1,
                          allow_service_certify=True)
    assert out.get("service:postgres").state is State.SATISFIED
    out_off = certify_refresh(g, lambda cmd: (0, ""), cycle=1)   # default: off
    assert out_off.get("service:postgres").state is State.UNKNOWN
```

Run: `python3 -m pytest tests/ -k "certify_refresh" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/python_deps/depgraph/certify.py src/envstate/depgraph_live.py tests/
git commit -m "feat(depgraph): certify confirmed in-image services via loopback probe (arm-gated)"
```

---

### Task 5: Schedule — confirmed services become actionable (arm-gated); render recipe

**Files:**
- Modify: `src/python_deps/depgraph/schedule.py:28-51` (`_is_actionable`, `scheduler_frontier`)
- Modify: `src/envstate/graph_scheduler.py:19-33` (`packet_to_task`), `:53-78` (`next_decision`)
- Test: `tests/depgraph/test_schedule.py`, `tests/envstate/test_graph_scheduler.py`

**Interfaces:**
- Produces:
  - `_is_actionable(graph, node, *, allow_services=False)` and `scheduler_frontier(graph, *, allow_services=False)` — a **confirmed** MISSING SERVICE with its SystemLib prereq SATISFIED is actionable when `allow_services=True`; inferred services never.
  - `ObligationPacket` gains `start_recipe: dict | None = None`; `frame_obligation` populates it from `node.data.get("start_recipe")`; `packet_to_task` renders the start command into `facts`.
  - `next_decision(..., allow_services=False)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schedule.py`:

```python
def _provisioning_graph(service_state, syslib_state):
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType,
    )
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=service_state, check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed",
                     "start_recipe": {"start": "START_CMD"}})
    sysl = Node(id="syslib:postgresql", type=NodeType.SYSTEM_LIB, name="postgresql",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=syslib_state, check_command="command -v pg_ctlcluster",
                chosen_fix="apt:postgresql")
    g = DepGraph().with_node(svc).with_node(sysl)
    return g.with_edge(Edge(src="service:postgres", dst="syslib:postgresql",
                            relation=EdgeType.REQUIRES, origin="service"))


def test_confirmed_service_actionable_only_when_allowed_and_prereq_satisfied():
    from python_deps.depgraph.schema import State
    from python_deps.depgraph.schedule import scheduler_frontier
    g = _provisioning_graph(State.MISSING, State.SATISFIED)
    assert [n.id for n in scheduler_frontier(g, allow_services=True)] == ["service:postgres"]
    assert scheduler_frontier(g) == ()                       # default off: excluded
    g2 = _provisioning_graph(State.MISSING, State.MISSING)   # prereq not installed
    assert scheduler_frontier(g2, allow_services=True) == () # blocked by SystemLib


def test_packet_renders_start_recipe():
    from python_deps.depgraph.schema import State
    from python_deps.depgraph.schedule import scheduler_frontier, frame_obligation
    from src.envstate.graph_scheduler import packet_to_task
    g = _provisioning_graph(State.MISSING, State.SATISFIED)
    node = scheduler_frontier(g, allow_services=True)[0]
    task = packet_to_task(frame_obligation(g, node))
    assert any("START_CMD" in f for f in task.facts)
```

> Note: a confirmed service is NOT emittable (`_INSTALLABLE` excludes SERVICE in `emit.py`), so the existing `not _is_emittable(...)` clause already passes it through — only the explicit `node.type is not NodeType.SERVICE` exclusion blocks it.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_schedule.py -k "actionable or start_recipe" -v`
Expected: FAIL — `scheduler_frontier()` got an unexpected keyword `allow_services` (and the SERVICE exclusion keeps the frontier empty).

- [ ] **Step 3: Write minimal implementation**

In `schedule.py`, thread `allow_services` (default False) so SERVICE is excluded only when not allowed or not confirmed:

```python
def _is_actionable(graph: DepGraph, node: Node, *, allow_services: bool = False) -> bool:
    from python_deps.depgraph.emit import _is_emittable, _conflicted_ids
    service_ok = (
        node.type is not NodeType.SERVICE
        or (allow_services and node.data.get("service_confidence") == "confirmed")
    )
    return (
        node.state is State.MISSING
        and service_ok
        and node.type is not NodeType.CONFIG
        and bool(node.check_command)
        and _dependencies_satisfied(graph, node)
        and not _is_emittable(graph, node, _conflicted_ids(graph))
    )


def scheduler_frontier(graph: DepGraph, *, allow_services: bool = False) -> tuple[Node, ...]:
    actionable = [n for n in graph.nodes if _is_actionable(graph, n, allow_services=allow_services)]
    if not actionable:
        return ()
    return tuple(topo_order(graph, tuple(actionable)))
```

Add `start_recipe` to `ObligationPacket` (after `certified_context`, line 66):

```python
    start_recipe: dict | None = None
```

Populate it in `frame_obligation` (line 83 `return ObligationPacket(...)`), adding:

```python
        start_recipe=node.data.get("start_recipe"),
```

In `src/envstate/graph_scheduler.py`, render the recipe in `packet_to_task` (after the `certified_context` facts, line 26):

```python
    if packet.start_recipe and packet.start_recipe.get("start"):
        facts.append("start the service in-image (run, then the host re-checks "
                     f"`{packet.check_command}`): {packet.start_recipe['start']}")
        if packet.start_recipe.get("createdb"):
            facts.append("then create the bound database: "
                         f"{packet.start_recipe['createdb']}")
```

Thread `allow_services` through `next_decision` (line 53) and the `scheduler_frontier` call (line 66):

```python
def next_decision(
    graph: DepGraph | None,
    run_tests: Callable[[], bool],
    handed: dict[str, int] | None = None,
    attempt_cap: int = 3,
    *,
    allow_services: bool = False,
) -> tuple[PlannerDecision, str | None]:
    handed = handed or {}
    frontier = scheduler_frontier(graph, allow_services=allow_services) if graph is not None else ()
    ...
```

Then grep the orchestrator for the `next_decision(` call site and pass
`allow_services=os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"`:

Run: `grep -rn "next_decision(" src/`

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_schedule.py tests/envstate/test_graph_scheduler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schedule.py src/envstate/graph_scheduler.py tests/
git commit -m "feat(depgraph): schedule confirmed in-image services + render start recipe (arm-gated)"
```

---

### Task 6: Done-branch requires a certified service (anti-hollow)

**Files:**
- Modify: `src/envstate/graph_scheduler.py:74-78` (the `run_tests()` done-branch in `next_decision`)
- Test: `tests/envstate/test_graph_scheduler.py`

**Interfaces:**
- Produces: in `next_decision`, when a confirmed in-image service was promoted (a SERVICE node with `data["start_recipe"]` exists) and `allow_services=True`, `done` is returned only if that service node is SATISFIED; otherwise fall through to the discover-task branch (honest non-done). For graphs with no promoted service, behavior is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/envstate/test_graph_scheduler.py`:

```python
def test_done_blocked_until_promoted_service_certified():
    from python_deps.depgraph.schema import State
    from src.envstate.graph_scheduler import next_decision
    # service MISSING but tests "pass" (the 1 pre-existing unit test) -> must NOT be done
    g = _provisioning_graph(State.MISSING, State.SATISFIED)   # helper from Task 5 test
    decision, _ = next_decision(g, run_tests=lambda: True, allow_services=True)
    assert decision.action != "done"

    g_ok = _provisioning_graph(State.SATISFIED, State.SATISFIED)
    decision_ok, _ = next_decision(g_ok, run_tests=lambda: True, allow_services=True)
    assert decision_ok.action == "done"


def test_done_unchanged_for_non_service_graph():
    from python_deps.depgraph.schema import DepGraph
    from src.envstate.graph_scheduler import next_decision
    decision, _ = next_decision(DepGraph(), run_tests=lambda: True, allow_services=True)
    assert decision.action == "done"
```

> Reuse the `_provisioning_graph` helper from Task 5's test module (import it or duplicate the small builder).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/envstate/test_graph_scheduler.py -k "done_blocked" -v`
Expected: FAIL — `next_decision` returns `done` even though the service is MISSING.

- [ ] **Step 3: Write minimal implementation**

In `next_decision`, replace the `if run_tests():` done-branch (line 74) with a guard that requires any promoted-but-uncertified service to be SATISFIED first:

```python
    from python_deps.depgraph.schema import NodeType, State
    if run_tests():
        promoted_unsatisfied = [
            n for n in (graph.nodes if graph is not None else ())
            if n.type is NodeType.SERVICE
            and n.data.get("start_recipe")
            and n.state is not State.SATISFIED
        ]
        if allow_services and promoted_unsatisfied:
            # Anti-hollow: tests "passing" while a required in-image service is not
            # host-certified up is the 1-unit-test-rides-to-0.2 trap (design §10).
            return PlannerDecision(action="task", task=_discover_task()), None
        return PlannerDecision(
            action="done", reason="graph-scheduler: frontier clean, tests pass"
        ), None
    return PlannerDecision(action="task", task=_discover_task()), None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/envstate/test_graph_scheduler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/graph_scheduler.py tests/envstate/test_graph_scheduler.py
git commit -m "feat(scheduler): refuse done until a promoted in-image service is host-certified (anti-hollow)"
```

---

### Task 7: Advisory — render the provisioning recipe

**Files:**
- Modify: `src/python_deps/depgraph/advise.py:157-172` (the SERVICES block in `render_dep_graph_advisory`)
- Test: `tests/depgraph/test_advise.py`

**Interfaces:**
- Consumes: `node.data["start_recipe"]`.
- Produces: a service node carrying a `start_recipe` renders `needs (System): <pkg>` and `start: <cmd>` lines under the SERVICES block; advisory-only services render exactly as today.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_advise.py`:

```python
def test_advisory_renders_provisioning_recipe():
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.advise import render_dep_graph_advisory
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
               fix_candidates=("service:postgres:16",),
               data={"service_confidence": "confirmed",
                     "start_recipe": {"system_package": "postgresql", "start": "START_CMD"}})
    out = render_dep_graph_advisory(DepGraph().with_node(svc))
    assert "needs (System): postgresql" in out
    assert "START_CMD" in out


def test_advisory_advisory_only_service_unchanged():
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.advise import render_dep_graph_advisory
    svc = Node(id="service:redis", type=NodeType.SERVICE, name="redis",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.RESOLVER, state=State.UNKNOWN,
               fix_candidates=("service:redis:7",),
               data={"service_confidence": "inferred"})
    out = render_dep_graph_advisory(DepGraph().with_node(svc))
    assert "needs (System)" not in out
    assert "may be mocked" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_advise.py -k "provisioning or advisory_only" -v`
Expected: FAIL — no `needs (System)` line.

- [ ] **Step 3: Write minimal implementation**

In `advise.py`, inside the SERVICES loop (after line 172, within the `for n in services:` body), append the recipe lines when present:

```python
            recipe = n.data.get("start_recipe")
            if recipe:
                lines.append(f"            needs (System): {recipe.get('system_package','')}")
                if recipe.get("start"):
                    lines.append(f"            start: {recipe['start']}")
                if recipe.get("createdb"):
                    lines.append(f"            then: {recipe['createdb']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_advise.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/advise.py tests/depgraph/test_advise.py
git commit -m "feat(depgraph): render in-image start recipe in the SERVICES advisory block"
```

---

### Task 8: Synthesizer — classify `pg_ctlcluster`/`createdb` as runtime-only (not baked)

**Files:**
- Modify: `src/synthesizer.py:3807-3817` (`_is_runtime_service_segment`)
- Test: `tests/test_synthesizer.py` (or the synthesizer test module; create a focused test)

**Interfaces:**
- Produces: `_is_runtime_service_segment` additionally matches `pg_ctlcluster ... start`, `createdb`, `createuser` (incl. `runuser -u postgres -- ...` / `su - postgres -c ...` wrappers) so these are dropped from baked build commands (a `RUN`-layer daemon dies before `CMD`; the start/createdb run in the eval wrapper instead — Task 9).

- [ ] **Step 1: Write the failing test**

Add a focused test (find the existing synthesizer test file via `ls tests/ | grep -i synth`; if none, create `tests/test_synthesizer_runtime_segments.py`):

```python
def test_pg_ctlcluster_and_createdb_are_runtime_only():
    from src.synthesizer import DockerfileSynthesizer  # match the real class name
    s = DockerfileSynthesizer.__new__(DockerfileSynthesizer)  # no __init__ needed for pure predicate
    assert s._is_runtime_service_segment('runuser -u postgres -- pg_ctlcluster 15 main start')
    assert s._is_runtime_service_segment('pg_ctlcluster 15 main start')
    assert s._is_runtime_service_segment('runuser -u postgres -- createdb appdb')
    assert s._is_runtime_service_segment('su - postgres -c "createdb appdb"')
    # apt install must remain a BUILD command (NOT runtime-only)
    assert not s._is_runtime_service_segment('apt-get install -y postgresql')
```

> Confirm the synthesizer class name with `grep -n "class .*Synthesizer" src/synthesizer.py` and use it in the test. `_is_runtime_service_segment` is a pure string predicate, so an uninitialized instance suffices.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/ -k "pg_ctlcluster_and_createdb" -v`
Expected: FAIL — `pg_ctlcluster`/`createdb` not matched.

- [ ] **Step 3: Write minimal implementation**

In `synthesizer.py`, extend the `service_patterns` tuple in `_is_runtime_service_segment` (line 3808):

```python
    def _is_runtime_service_segment(self, normalized_command):
        service_patterns = (
            r"^service\s+\S+\s+(?:start|restart|reload|stop)\b",
            r"^redis-server\b",
            r"^rabbitmq-server\b.*\b-detached\b",
            r"^memcached\b.*\b-d\b",
            r"^mongod\b.*\b--fork\b",
            r"^apache2ctl\s+start\b",
            r"^nginx\b(?:\s|$)",
            # In-image Postgres provisioning runs at RUNTIME (in the eval wrapper),
            # not baked into the image (a RUN-layer daemon dies before CMD). Match
            # the bare and the as-postgres-user wrapped forms (design §8.2).
            r"(?:^|\bpostgres\s+-c\s+\"?|--\s+)pg_ctlcluster\b.*\bstart\b",
            r"(?:^|\bpostgres\s+-c\s+\"?|--\s+)createdb\b",
            r"(?:^|\bpostgres\s+-c\s+\"?|--\s+)createuser\b",
        )
        return any(re.search(pattern, normalized_command) for pattern in service_patterns)
```

> If `_normalize_command_segment` strips the `runuser`/`su` wrapper differently, adjust the anchors to match the normalized form (verify by printing `self._normalize_command_segment(cmd)` in a scratch run). The goal: these three commands return True; `apt-get install postgresql` returns False.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/ -k "pg_ctlcluster_and_createdb" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/synthesizer.py tests/
git commit -m "fix(synthesizer): classify pg_ctlcluster/createdb as runtime-only (don't bake a dying daemon)"
```

---

### Task 9: Eval — compose the in-image start sequence into the test wrapper

**Files:**
- Modify: `run_repo2run_benchmark.py` — add `compose_in_image_service_commands`; call it where `runtime_commands`/`add_postgres_host_alias` are derived (around `derive_verification_commands:2448` and `evaluate_built_image:2850`)
- Test: `tests/test_run_repo2run_benchmark.py` (or a focused new test module)

**Interfaces:**
- Consumes: `run_summary["confirmed_in_image_services"]` (written in Task 10): a list of `{"kind","port","db","start","wait","createdb","certify"}`.
- Produces:
  - `compose_in_image_service_commands(run_summary: dict | None) -> list[str]` — for each confirmed postgres service, returns `[start, wait, createdb]` (omitting `createdb` when None). `createdb` carries NO `|| true` (fatal). Empty list when the field is absent.
  - These commands are **prepended** to `runtime_commands` so they run in the same shell before pytest; `should_add_postgres_host_alias` returns True when the field is present.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_run_repo2run_benchmark.py`:

```python
def test_compose_in_image_service_commands():
    from run_repo2run_benchmark import compose_in_image_service_commands
    rs = {"confirmed_in_image_services": [{
        "kind": "postgres", "port": 5432, "db": "appdb",
        "start": "runuser -u postgres -- pg_ctlcluster 15 main start",
        "wait": "for i in $(seq 1 30); do pg_isready -h 127.0.0.1 -p 5432 && break; sleep 1; done",
        "createdb": "runuser -u postgres -- createdb appdb",
    }]}
    cmds = compose_in_image_service_commands(rs)
    assert cmds[0].startswith("runuser -u postgres -- pg_ctlcluster")
    assert any("pg_isready" in c for c in cmds)
    assert cmds[-1] == "runuser -u postgres -- createdb appdb"
    assert all("|| true" not in c for c in cmds)         # createdb FATAL
    assert compose_in_image_service_commands({}) == []
    assert compose_in_image_service_commands(None) == []


def test_no_createdb_line_when_db_absent():
    from run_repo2run_benchmark import compose_in_image_service_commands
    rs = {"confirmed_in_image_services": [{
        "kind": "postgres", "port": 5432, "db": None,
        "start": "S", "wait": "W", "createdb": None}]}
    cmds = compose_in_image_service_commands(rs)
    assert cmds == ["S", "W"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_run_repo2run_benchmark.py -k "compose_in_image or no_createdb" -v`
Expected: FAIL — `ImportError: cannot import name 'compose_in_image_service_commands'`.

- [ ] **Step 3: Write minimal implementation**

In `run_repo2run_benchmark.py`, add the composer:

```python
def compose_in_image_service_commands(run_summary):
    """Root-wrapped start/wait/createdb(fatal) lines for confirmed in-image
    services, read from the agent's handoff field. Prepended to runtime_commands
    so they run in the same shell as the tests (design §8.3). Empty when absent."""
    if not isinstance(run_summary, dict):
        return []
    services = run_summary.get("confirmed_in_image_services") or []
    out = []
    for svc in services:
        if not isinstance(svc, dict):
            continue
        if svc.get("start"):
            out.append(svc["start"])
        if svc.get("wait"):
            out.append(svc["wait"])
        if svc.get("createdb"):                  # FATAL — never `|| true`
            out.append(svc["createdb"])
    return out
```

Wire it where the eval derives commands. In `derive_verification_commands` (line 2448), prepend the composed commands:

```python
def derive_verification_commands(run_summary):
    supported_bundle = derive_supported_verification_bundle(run_summary)
    runtime_commands = normalize_command_list(supported_bundle.get("runtime_preparation_commands"))
    runtime_commands = compose_in_image_service_commands(run_summary) + runtime_commands
    test_commands = normalize_command_list(supported_bundle.get("test_commands"))
    ...
```

Make `should_add_postgres_host_alias` fire on the field too — at its first line (line 2825), add:

```python
def should_add_postgres_host_alias(workspace_root, runtime_commands, test_commands, run_summary=None):
    if isinstance(run_summary, dict) and run_summary.get("confirmed_in_image_services"):
        return True
    combined_commands = "\n".join([*(runtime_commands or []), *(test_commands or [])]).lower()
    ...
```

(Thread `run_summary` to the `should_add_postgres_host_alias(...)` call in `evaluate_built_image` if the run summary is in scope there; otherwise the `runtime_commands` already contain `pg_isready`/`pg_ctlcluster`, so the existing regex still fires — keep the field check as the primary, the regex as fallback.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_run_repo2run_benchmark.py -k "compose_in_image or no_createdb" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add run_repo2run_benchmark.py tests/test_run_repo2run_benchmark.py
git commit -m "feat(eval): compose in-image start/wait/createdb(fatal) into the test wrapper from the handoff field"
```

---

### Task 10: Agent — write the `confirmed_in_image_services` handoff field

**Files:**
- Modify: `agent.py:3128-3220` (`_build_run_summary`)
- Test: `tests/test_agent_run_summary.py` (or a focused new test)

**Interfaces:**
- Consumes: `self._final_dep_graph` (set at finalization, `agent.py:1350`); the arm flag.
- Produces: `summary["confirmed_in_image_services"]` — a list of `{"kind","port","db","start","wait","createdb","certify"}` derived from each confirmed SERVICE node that is **SATISFIED** and carries a `start_recipe`. Present ONLY when the arm is on AND at least one such service exists; absent otherwise (off-state byte-identity).

- [ ] **Step 1: Write the failing test**

Add to a focused test module:

```python
def test_run_summary_emits_confirmed_in_image_services(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    import agent as agent_mod
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
               check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed",
                     "start_recipe": {"start": "S", "wait": "W", "createdb": "C",
                                      "certify": "pg_isready -h 127.0.0.1 -p 5432",
                                      "port": 5432, "db": "appdb"}})
    a._final_dep_graph = DepGraph().with_node(svc)
    services = a._collect_confirmed_in_image_services()
    assert services and services[0]["kind"] == "postgres"
    assert services[0]["start"] == "S" and services[0]["db"] == "appdb"


def test_no_field_when_service_not_satisfied(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    import agent as agent_mod
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
               check_command="pg_isready -h 127.0.0.1 -p 5432",
               data={"service_confidence": "confirmed", "start_recipe": {"start": "S"}})
    a._final_dep_graph = DepGraph().with_node(svc)
    assert a._collect_confirmed_in_image_services() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/ -k "confirmed_in_image_services or no_field_when" -v`
Expected: FAIL — `DockerAgent` has no `_collect_confirmed_in_image_services`.

- [ ] **Step 3: Write minimal implementation**

In `agent.py`, add the collector method (near `_build_run_summary`):

```python
    def _collect_confirmed_in_image_services(self):
        """Handoff field for the eval: confirmed services certified up in-sandbox.

        Only SATISFIED confirmed services with a start_recipe — so the scored eval
        reproduces exactly what the host certified (design §8.1). Empty off-arm."""
        import os
        if os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") != "1":
            return []
        from python_deps.depgraph.schema import NodeType, State
        graph = getattr(self, "_final_dep_graph", None)
        if graph is None:
            return []
        out = []
        for n in graph.nodes:
            if (n.type is NodeType.SERVICE
                    and n.data.get("service_confidence") == "confirmed"
                    and n.state is State.SATISFIED):
                recipe = n.data.get("start_recipe") or {}
                if not recipe.get("start"):
                    continue
                out.append({
                    "kind": n.name, "port": recipe.get("port"), "db": recipe.get("db"),
                    "start": recipe.get("start"), "wait": recipe.get("wait"),
                    "createdb": recipe.get("createdb"), "certify": recipe.get("certify"),
                })
        return out
```

In `_build_run_summary`, add the field conditionally (after the `runtime_pin` conditional block, ~line 3193):

```python
        services = self._collect_confirmed_in_image_services()
        if services:
            summary["confirmed_in_image_services"] = services
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/ -k "confirmed_in_image_services or no_field_when" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/
git commit -m "feat(agent): write confirmed_in_image_services handoff field (satisfied-only, arm-gated)"
```

---

### Task 11: Arm plumbing — `v1gsps` in both harnesses

**Files:**
- Modify: `run_rat_benchmark.py:392-407` (child arm re-detection), `:806` (`--arm` choices), `:845-851` (env ladder)
- Modify: `run_repo2run_benchmark.py:3193-3208` (`_ARM_PRESETS`) and the `--arm` choices
- Test: `tests/test_arm_plumbing.py` (focused)

**Interfaces:**
- Produces: arm `v1gsps` sets `DOCKERAGENT_ENABLE_SERVICE_PROVISION=1` (+ all `v1gsp` flags) in `run_rat_benchmark.py`; an equivalent `v1gsps` preset with `enable_service_provision: True` in `run_repo2run_benchmark.py`.

- [ ] **Step 1: Write the failing test**

Add `tests/test_arm_plumbing.py`:

```python
def test_v1gsps_sets_service_provision_env(monkeypatch):
    monkeypatch.setattr("sys.argv", ["x", "--arm", "v1gsps"])
    import importlib, run_rat_benchmark
    importlib.reload(run_rat_benchmark)
    # the function that applies the arm ladder; call the parse+apply path used in main
    args = run_rat_benchmark._parse_args()      # if no such helper, assert on the ladder dict directly
    run_rat_benchmark._apply_arm_env(args.arm)  # extract the env-setting block into this helper (see impl)
    import os
    assert os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] == "1"
    assert os.environ["DOCKERAGENT_ENABLE_GRAPH_SCHEDULER"] == "1"
    assert os.environ["DOCKERAGENT_ENABLE_RUNTIME_PIN"] == "1"
```

> The env-ladder is currently inline in `main` (`run_rat_benchmark.py:845-851`). Extract it into a small `_apply_arm_env(arm: str)` helper so it is unit-testable, then call that helper from `main`. The child re-detection block (L392-407) gets a new first branch for the new flag.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_arm_plumbing.py -v`
Expected: FAIL — `v1gsps` not a valid choice / flag not set.

- [ ] **Step 3: Write minimal implementation**

In `run_rat_benchmark.py`:
- Add `"v1gsps"` to the `--arm` `choices` list (line 806) and a help note.
- Extract the env block (L845-851) into `_apply_arm_env(arm)` and add the new flag, with `v1gsps` inheriting all `v1gsp` flags plus the new one:

```python
def _apply_arm_env(arm: str) -> None:
    os.environ["DOCKERAGENT_ENABLE_V1"] = "1" if arm in ("v1","v1g","v1gd","v1gde","v1gder","v1gs","v1gsp","v1gsps") else "0"
    os.environ["DOCKERAGENT_ENABLE_CONTRACT_GRAPH"] = "1" if arm in ("v1g","v1gd","v1gde","v1gder") else "0"
    os.environ["DOCKERAGENT_ENABLE_DEP_GRAPH"] = "1" if arm in ("v1gd","v1gde","v1gder","v1gs","v1gsp","v1gsps") else "0"
    os.environ["DOCKERAGENT_ENABLE_DEP_EMIT"] = "1" if arm in ("v1gde","v1gder","v1gs","v1gsp","v1gsps") else "0"
    os.environ["DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK"] = "1" if arm in ("v1gder","v1gs","v1gsp","v1gsps") else "0"
    os.environ["DOCKERAGENT_ENABLE_GRAPH_SCHEDULER"] = "1" if arm in ("v1gs","v1gsp","v1gsps") else "0"
    os.environ["DOCKERAGENT_ENABLE_RUNTIME_PIN"] = "1" if arm in ("v1gsp","v1gsps") else "0"
    os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] = "1" if arm == "v1gsps" else "0"
```

Add the child re-detection branch (before the `RUNTIME_PIN` branch at L392):

```python
    if os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1":
        arm = "v1gsps"
    elif os.environ.get("DOCKERAGENT_ENABLE_RUNTIME_PIN") == "1":
        arm = "v1gsp"
    ...
```

In `run_repo2run_benchmark.py`, add a `v1gsps` preset (copy `v1gsp`, add `"enable_service_provision": True`, `"_label": "armV1gsps_service_provision"`) and add `"v1gsps"` to its `--arm` choices.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_arm_plumbing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add run_rat_benchmark.py run_repo2run_benchmark.py tests/test_arm_plumbing.py
git commit -m "feat(arm): add v1gsps (service-provision) to both harnesses"
```

---

### Task 12: Sandbox — `postgres` hostname alias at container launch (arm-gated)

**Files:**
- Modify: `src/sandbox.py:48-78` (`_setup_initial_container`) and `:371-399` (`_restore_last_success_container`) `containers.run(...)` calls
- Test: `tests/test_sandbox.py` (focused; mock the docker client)

**Interfaces:**
- Produces: when `DOCKERAGENT_ENABLE_SERVICE_PROVISION == "1"`, both `containers.run` calls pass `extra_hosts={"postgres": "127.0.0.1"}` so a test connecting to the CI hostname `postgres` reaches the in-image daemon. Off-arm: no `extra_hosts` (byte-identical).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sandbox.py`:

```python
def test_extra_hosts_added_when_arm_on(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    captured = {}
    class FakeContainers:
        def run(self, image, **kwargs):
            captured.update(kwargs)
            class C:
                def exec_run(self, *a, **k): return type("R",(),{"exit_code":0,"output":b""})()
                def commit(self): return type("I",(),{"id":"sha256:abc"})()
            return C()
    class FakeClient:
        containers = FakeContainers()
        images = type("I",(),{"pull": lambda *a, **k: None})()
    import src.sandbox as sb
    s = sb.Sandbox.__new__(sb.Sandbox)
    # set the minimal attributes _setup_initial_container reads:
    s.client = FakeClient(); s.current_image = "python:3.11-slim"; s.platform = None
    s.workdir = "/app"; s.volumes = {}; s.seed_dir = None
    s._bootstrap_apt_if_supported = lambda: None
    s._register_snapshot = lambda *a: None
    s._setup_initial_container()
    assert captured.get("extra_hosts") == {"postgres": "127.0.0.1"}


def test_no_extra_hosts_off_arm(monkeypatch):
    monkeypatch.delenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", raising=False)
    # ...same harness... assert "extra_hosts" not in captured
```

> Match `Sandbox`'s real attribute names by reading `src/sandbox.py`; the test only needs the attributes `_setup_initial_container` touches. Keep the off-arm test asserting `extra_hosts` is absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sandbox.py -k "extra_hosts" -v`
Expected: FAIL — `extra_hosts` not passed.

- [ ] **Step 3: Write minimal implementation**

In `src/sandbox.py`, add a helper and pass `extra_hosts` in both `containers.run` calls:

```python
import os

def _service_extra_hosts():
    if os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1":
        return {"postgres": "127.0.0.1"}
    return None
```

In `_setup_initial_container` and `_restore_last_success_container`, change each `containers.run(...)` to include `extra_hosts=_service_extra_hosts()`. (Docker's SDK treats `extra_hosts=None` as "not set", so off-arm stays byte-identical.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sandbox.py -k "extra_hosts" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sandbox.py tests/test_sandbox.py
git commit -m "feat(sandbox): add postgres->loopback hostname alias when service-provision arm is on"
```

---

### Task 13: Off-state byte-identity + integration regression

**Files:**
- Test only: `tests/test_service_provision_off_state.py`

**Interfaces:** none (verification task).

- [ ] **Step 1: Write the off-state snapshot test**

Create `tests/test_service_provision_off_state.py`:

```python
import os


def test_build_off_state_byte_identical(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", raising=False)
    from conftest import FakeExecutor  # type: ignore
    from python_deps.depgraph.build import build_dep_graph
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "0"\n')
    (tmp_path / "app").mkdir(); (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "db.py").write_text("import psycopg2\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:14\n")
    from conftest import _r  # type: ignore
    ex = FakeExecutor(default=_r(returncode=1, stderr="x"))
    # default (no kwarg) must equal explicit enable_service_provision=False
    a = build_dep_graph(str(tmp_path), ex, host_executor=ex, target_python="3.11")
    b = build_dep_graph(str(tmp_path), ex, host_executor=ex, target_python="3.11",
                        enable_service_provision=False)
    assert a.to_dict() == b.to_dict()
    from python_deps.depgraph.ids import syslib_id
    assert a.get(syslib_id("postgresql")) is None       # no provisioning nodes off-arm


def test_eval_wrapper_unchanged_without_field():
    from run_repo2run_benchmark import compose_in_image_service_commands
    assert compose_in_image_service_commands({"verified_test_commands": ["pytest"]}) == []


def test_advisory_unchanged_for_advisory_only_service():
    # a confirmed service WITHOUT a start_recipe (arm off at build) renders the
    # legacy SERVICES block exactly (no needs/start lines).
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.advise import render_dep_graph_advisory
    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
               fix_candidates=("service:postgres:16",),
               data={"service_confidence": "confirmed"})
    out = render_dep_graph_advisory(DepGraph().with_node(svc))
    assert "needs (System)" not in out and "start:" not in out
```

- [ ] **Step 2: Run the off-state suite**

Run: `python3 -m pytest tests/test_service_provision_off_state.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full depgraph + scheduler + eval suites (regression)**

Run: `python3 -m pytest tests/depgraph/ tests/envstate/ tests/test_run_repo2run_benchmark.py -q`
Expected: PASS — all prior tests green + the new ones.

- [ ] **Step 4: Commit**

```bash
git add tests/test_service_provision_off_state.py
git commit -m "test(services): off-state byte-identity + integration regression for in-image provisioning"
```

---

### Task 14: End-to-end validation (controller-run, on the VM)

**Files:** none (validation).

This task is run by the controller (not a subagent) after Tasks 1–13 land, per the spec §13 e2e plan. Steps:

- [ ] **Step 1:** Push the branch; update the VM agent to the new HEAD (`bench` provisions `/opt/agents/john-planner-v3` via fetch + reset).
- [ ] **Step 2:** Run a 2-repo honest A/B (`fastapi-template` + one regression repo, e.g. `memU`) under `v1gsps` and `v1gsp` via `/opt/rat_venv/bin/python /opt/harness/bench`.
- [ ] **Step 3:** Score with the honest scorer `/opt/harness/scripts/compute_essr.py` (NEVER trust `rat_results.json`). Expected: `fastapi-template` 0 → pass under `v1gsps` (Postgres certified up; the fresh rebuild reproduces it via the handoff field); `memU` byte-identical/green (no confirmed service → unchanged).
- [ ] **Step 4:** Confirm in the run summary: the `service:postgres` node reached SATISFIED in-sandbox, `confirmed_in_image_services` is present, and the eval wrapper started Postgres (grep the eval log for `pg_isready` / `accepting connections`). Confirm NO hollow: the scored pass-rate comes from the integration tests actually running, not a 1-unit-test 0.2.
- [ ] **Step 5:** Record the result in `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md` (Observation→Why→What→Verification) and `.superpowers/sdd/progress.md`.

---

## Self-Review

**Spec coverage:**
- §4.1 loopback probe → Task 2 (rewrite) + Task 4 (certify uses it). ✅
- §4.2 `_LAYER_ORDER` arm-gated → Task 4 (`_SERVICE_LAYER_ORDER`, `layer_order` param). ✅
- §4.3 skip-guard confirmed-only → Task 4. ✅
- §4.4/§4.5 schedule exclusion lift + invariants → Task 5; sandbox `extra_hosts` → Task 12. ✅
- §5 EDGE_RULES + System→Service chain → Task 1 (schema) + Task 2 (SystemLib node + edge). ✅
- §6 certify in live container → Task 4 (`certify_refresh`). ✅
- §7 root-safe, version-resolved recipe in `data["start_recipe"]` → Task 2; render → Task 7. ✅
- §8.1 handoff field → Task 10; §8.2 install-bake/runtime-split → Task 8; §8.3 wrapper compose + createdb fatal → Task 9; §8.4 hostname parity → Task 12 (sandbox) + Task 9 (eval `--add-host`). ✅
- §9 arm + off-state → Task 11 + Task 13. ✅
- §10 done-gate + createdb fatal → Task 6 + Task 9. ✅
- §13 tests + e2e → every task's TDD steps + Task 14. ✅

**Placeholder scan:** code shown for every code step; the few "grep the caller and thread the flag" steps are mechanical wiring with the exact env-var literal and function name given.

**Type consistency:** `start_recipe` dict keys (`system_package/start/wait/createdb/certify/port/db`) are identical across Tasks 2, 5, 6, 7, 9, 10. `confirmed_in_image_services` item keys identical across Tasks 9 and 10. `allow_service_certify` / `allow_services` / `enable_service_provision` used consistently. `syslib_id("postgresql")` used identically in Tasks 2, 3, 13.

**Open risks carried to execution:** the exact orchestrator call sites for `build_dep_graph` / `certify_refresh` / `next_decision` are resolved by grep in Tasks 3/4/5 (the env-var literal is fixed); the synthesizer class name and `_normalize_command_segment` behavior are verified in Task 8; the `Sandbox` attribute names in Task 12.

## Execution Handoff

Plan complete and saved. Execution: **Subagent-Driven** (sonnet implementer per task + task review), then controller-run e2e (Task 14).
