# Config-Binding (Service URL Binding) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** After the Services in-image provisioning slice stands a confirmed Postgres up, bind the app's DB env var to a rewritten URL that actually connects to it (Option B), so DB-gated suites run — host-certified, no hollow success.

**Architecture:** Layers onto the Services Tier feature (`d130772..86f056e`), behind the same default-off arm `v1gsps` (`DOCKERAGENT_ENABLE_SERVICE_PROVISION==1`). Discovery reads the app service's compose/CI `environment:` block for a `KEY=<db-url>` pair. We provision ONE uniform Postgres (`ALTER USER postgres PASSWORD 'postgres'`) and rewrite the app's var to `<app-scheme>://postgres:postgres@127.0.0.1:<port>/<db>`. The binding is a CONFIG obligation `requires` the SERVICE node; its `facts` hand the LLM the exact `ALTER USER` + `/etc/profile.d` write (the HOW, same LLM-run path as the service `start`); the host certifies it with `psql "<base-url>" -c 'select 1'` (the WHETHER). The rewritten value is also baked `ENV` (eval rebuild) and `export`ed into the eval test wrapper.

**Tech Stack:** Python 3, pytest, `src/python_deps/depgraph/{service_scan,schedule}.py`, `src/envstate/{graph_scheduler,synthesis}.py`, `agent.py`, `run_repo2run_benchmark.py`, `src/synthesizer.py`.

**Validation basis (real-container smoke, python:3.10/PG17, 2026-06-28):** `ALTER USER postgres PASSWORD 'postgres'` → `ALTER ROLE`; `psql "postgresql://postgres:postgres@127.0.0.1:5432/<db>" -c 'select 1'` connects with password over default pg_hba; **wrong password is rejected** (the certify is real); `/etc/profile.d/*.sh` export is visible to a fresh `sh -lc` and to Python launched from it.

## Global Constraints

Every task's requirements implicitly include these (copied verbatim for reviewers):

- **Arm gate:** all new behavior fires ONLY when `os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"` (arm `v1gsps`). No new env flag. **Off-arm == byte-identical** to today; a test must prove it.
- **Host owns truth (anti-hollow):** the binding flips `SATISFIED` ONLY by the host running its psql `check_command`. NEVER infer success from a command outcome, an LLM claim, or the emit/bake running. Do NOT weaken the done-gate or any certify to gain score — if a metric rises via a relaxed check, back it out.
- **LLM-run obligation, not host-emit:** the live `ALTER USER` + profile.d write ride the existing scheduled-obligation `facts` path (like the service `start`). Do NOT add a deterministic host-emitter for them (none exists for the service start; adding one is a forbidden new auto-fix tier).
- **Immutability:** `DepGraph`/`Node`/`Edge` are frozen (`@dataclass(frozen=True)`); every graph helper returns a NEW graph (`with_node`/`with_edge`/`dataclasses.replace`). Never mutate in place.
- **Postgres only this pass:** discovery may record other service kinds, but the URL rewrite + binding node are built for `kind=="postgres"` only. Other kinds get NO binding node (no silent half-binding).
- **Scheme fidelity:** the rewritten app value preserves the app's original scheme verbatim (e.g. `postgresql+psycopg2://`); the psql `check_command` uses the base `postgresql://` scheme (psql rejects dialect suffixes).
- **Credentials:** user/password are the literal `postgres`/`postgres`, host `127.0.0.1`. This is a throwaway dev credential for an ephemeral build/test container — config, not a secret.
- **Commit + push each task** (conventional commits; attribution is disabled globally — NO `Co-Authored-By` trailer). **`git add` ONLY the exact files this task changed** — NEVER `git add -A`, `git add .`, or `git add <dir>` (the repo carries unrelated WIP; sweeping it in corrupts scope).
- **TDD:** failing test first, watch it fail, minimal code, watch it pass, run the module suite.

## Shared interfaces (cross-task data contracts — use these names/keys verbatim)

- **Binding discovery** attaches to a confirmed postgres service node's `data` (reusing existing keys): `bound_config` = the env VAR name (e.g. `"DB_STRING"`), `bound_config_url` = the ORIGINAL url string from compose, and `db` = the database name. These already partly exist; this plan populates them from the compose `environment:` block.
- **`service_bind_url(scheme: str, port: int, db: str) -> str`** → `f"{scheme}://postgres:postgres@127.0.0.1:{port}/{db}"`.
- **Binding CONFIG node** (built in `attach_in_image_provisioning`): `id=config_id(var)`, `type=CONFIG`, `name=var`, `layer=CONFIG`, `state=UNKNOWN`, `check_command=f'psql "{probe_url}" -c "select 1"'` (probe_url uses base `postgresql` scheme), `fix_candidates=(f"env:{var}={app_url}",)`, `chosen_fix` same, `provenance="service binding"`, and `data={"binding": True, "bind_recipe": {"var": var, "url": app_url, "alter_user": ALTER_USER_CMD, "bind_profile": <profile write cmd>}}`. Edge: `Edge(src=config_id(var), dst=service_id("postgres"), relation=REQUIRES, origin="service")`.
- **Constants** (define once in `service_scan.py`, reuse): `BIND_PROFILE_PATH = "/etc/profile.d/zz_service_bind.sh"`; `ALTER_USER_CMD = "runuser -u postgres -- psql -c \"ALTER USER postgres PASSWORD 'postgres'\""`.
- **`confirmed_in_image_services`** dict gains two keys when the service has a binding: `"var": <VAR>`, `"url": <app_url>` (omitted/absent when no binding).

---

## Task 1: `service_bind_url` rewrite helper

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py` (add near `service_db_from_url`, ~line 261)
- Test: `tests/depgraph/test_service_binding.py` (create)

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `service_bind_url(scheme: str, port: int, db: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_service_binding.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from python_deps.depgraph.service_scan import service_bind_url  # noqa: E402


def test_bind_url_preserves_app_scheme_and_overrides_host_creds():
    assert service_bind_url("postgresql", 5432, "postgres") == \
        "postgresql://postgres:postgres@127.0.0.1:5432/postgres"


def test_bind_url_preserves_dialect_suffix():
    assert service_bind_url("postgresql+psycopg2", 5432, "appdb") == \
        "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/appdb"


def test_bind_url_custom_port_and_db():
    assert service_bind_url("postgresql", 5433, "mydb") == \
        "postgresql://postgres:postgres@127.0.0.1:5433/mydb"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_service_binding.py -q`
Expected: FAIL with `ImportError: cannot import name 'service_bind_url'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/python_deps/depgraph/service_scan.py` immediately after `service_db_from_url` (the function ending ~line 260):

```python
def service_bind_url(scheme: str, port: int, db: str) -> str:
    """Uniform in-image Postgres URL (Option B): our creds + loopback host, app's scheme+db.

    The app's original scheme (incl. dialect suffix like ``postgresql+psycopg2``) is
    preserved so SQLAlchemy's driver selection is unchanged; only host/credentials are
    rewritten to the in-image instance configured by the binding obligation.
    """
    return f"{scheme}://postgres:postgres@127.0.0.1:{port}/{db}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_service_binding.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py tests/depgraph/test_service_binding.py
git commit -m "feat(config-binding): service_bind_url rewrite helper (Option B)"
```

---

## Task 2: compose/CI `environment:` binding discovery

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py` (`scan_compose_services`/`scan_ci_services` env pass + `scan_services` absorption)
- Test: `tests/depgraph/test_service_binding.py` (extend)

**Interfaces:**
- Consumes: `service_from_url`, `service_db_from_url` (existing).
- Produces: `scan_env_bindings(repo_path: str) -> dict[str, dict]` returning `{kind: {"var": KEY, "url": value, "host": h, "port": p, "db": dbname}}` for each `KEY=<service-url>` found in any compose/CI service `environment:`. `scan_services` absorbs these onto the confirmed node's `data` as `bound_config`/`bound_config_url`/`db`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/depgraph/test_service_binding.py
import textwrap  # noqa: E402
from python_deps.depgraph.service_scan import scan_env_bindings  # noqa: E402


def _write(tmp_path, name, body):
    (tmp_path / name).write_text(textwrap.dedent(body), encoding="utf-8")


def test_scan_env_bindings_list_form(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        version: "3.7"
        services:
          api:
            depends_on: [db]
            environment:
              - DB_STRING=postgresql://postgres:test@db:5432/appdb
          db:
            image: postgres:14.5
    """)
    out = scan_env_bindings(str(tmp_path))
    assert "postgres" in out
    b = out["postgres"]
    assert b["var"] == "DB_STRING"
    assert b["url"] == "postgresql://postgres:test@db:5432/appdb"
    assert b["db"] == "appdb"


def test_scan_env_bindings_map_form_default_db(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            environment:
              DATABASE_URL: postgresql://postgres:test@db:5432/postgres
          db:
            image: postgres:14.5
    """)
    out = scan_env_bindings(str(tmp_path))
    assert out["postgres"]["var"] == "DATABASE_URL"
    assert out["postgres"]["db"] == "postgres"


def test_scan_env_bindings_ignores_nonservice_urls(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          api:
            environment:
              - SOME_HTTP=https://example.com/x
    """)
    assert scan_env_bindings(str(tmp_path)) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_service_binding.py -q`
Expected: FAIL with `ImportError: cannot import name 'scan_env_bindings'`.

- [ ] **Step 3: Write minimal implementation**

Add to `service_scan.py`. First a doc-level env extractor (place after `_services_from_yaml_doc`, ~line 102):

```python
def _env_pairs(entry: dict):
    """Yield (KEY, VALUE) from a compose/CI service `environment:` (list or map form)."""
    env = entry.get("environment")
    if isinstance(env, dict):
        for k, v in env.items():
            if v is not None:
                yield str(k), str(v)
    elif isinstance(env, list):
        for item in env:
            s = str(item)
            if "=" in s:
                k, v = s.split("=", 1)
                yield k.strip(), v.strip()


def _bindings_from_yaml_doc(doc, out: dict[str, dict]) -> None:
    """Merge service-URL env bindings from a parsed compose/CI doc into `out` (first wins)."""
    if not isinstance(doc, dict):
        return
    blocks = []
    if isinstance(doc.get("services"), dict):
        blocks.append(doc["services"])
    for job in (doc.get("jobs") or {}).values() if isinstance(doc.get("jobs"), dict) else []:
        if isinstance(job, dict) and isinstance(job.get("services"), dict):
            blocks.append(job["services"])
    for block in blocks:
        for _svc_name, entry in block.items():
            entry = entry if isinstance(entry, dict) else {}
            for key, value in _env_pairs(entry):
                hit = service_from_url(value)
                if not hit:
                    continue
                kind, host, port = hit
                if kind in out:
                    continue
                out[kind] = {"var": key, "url": value, "host": host, "port": port,
                             "db": service_db_from_url(value) or "postgres"}
```

Then the public scanner (place after `scan_ci_services`, ~line 141):

```python
def scan_env_bindings(repo_path: str) -> dict[str, dict]:
    """Discover `KEY=<service-url>` bindings from compose + CI `environment:` blocks."""
    out: dict[str, dict] = {}
    for fname in os.listdir(repo_path) if os.path.isdir(repo_path) else []:
        low = fname.lower()
        if (low.startswith("docker-compose") or low.startswith("compose.")) and \
                low.endswith((".yml", ".yaml")):
            _bindings_from_yaml_doc(_load_yaml(os.path.join(repo_path, fname)), out)
    wf_dir = os.path.join(repo_path, ".github", "workflows")
    if os.path.isdir(wf_dir):
        for fname in os.listdir(wf_dir):
            if fname.lower().endswith((".yml", ".yaml")):
                _bindings_from_yaml_doc(_load_yaml(os.path.join(wf_dir, fname)), out)
    return out
```

Now wire absorption into `scan_services`. Find the confirmed-node loop (lines 208-221). After computing `confirmed` (line 184) add a binding scan, and in the per-kind loop absorb it. Replace the block that builds `extra` (lines 213-218) with:

```python
        extra = dict(meta)
        binding = inferred.get(kind)
        if binding and "bound_config" in binding:
            extra["bound_config"] = binding["bound_config"]
        if binding and binding.get("bound_config_url"):
            extra["bound_config_url"] = binding["bound_config_url"]
        env_binding = env_bindings.get(kind)
        if env_binding:                          # compose `environment:` is authoritative
            extra["bound_config"] = env_binding["var"]
            extra["bound_config_url"] = env_binding["url"]
            extra["db"] = env_binding["db"]
```

And add `db` to the passthrough set in `_service_node` (line 168) so it lands in node `data`:

```python
    data.update({k: v for k, v in extra.items()
                 if k in ("bound_config", "inducing_package", "bound_config_url", "db")})
```

Finally, compute `env_bindings` once near the top of `scan_services` (after line 184 `confirmed = {**compose, **ci}`):

```python
    env_bindings = scan_env_bindings(repo_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_service_binding.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the depgraph suite (no regressions)**

Run: `python -m pytest tests/depgraph/test_probe.py tests/depgraph/test_build.py -q`
Expected: PASS (the existing service tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py tests/depgraph/test_service_binding.py
git commit -m "feat(config-binding): discover DB-url env bindings from compose/CI environment blocks"
```

---

## Task 3: attach the binding CONFIG node (+ edge, + db flow, + certify-flip)

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py` (`attach_in_image_provisioning`)
- Test: `tests/depgraph/test_service_binding.py` (extend)

**Interfaces:**
- Consumes: `service_bind_url` (Task 1), `config_id` (`from python_deps.depgraph.ids import config_id`), the absorbed `bound_config`/`bound_config_url`/`db` (Task 2).
- Produces: in the on-arm graph, a CONFIG node `config:<VAR>` with `data["binding"]=True`, a psql `check_command`, and an `Edge(config:<VAR> -> service:postgres, REQUIRES)`. Off-arm (`enabled=False`) the graph is unchanged.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/depgraph/test_service_binding.py
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State  # noqa: E402
from python_deps.depgraph.ids import service_id, config_id  # noqa: E402
from python_deps.depgraph.service_scan import attach_in_image_provisioning  # noqa: E402


def _confirmed_pg_graph():
    svc = Node(
        id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
        layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
        check_command="pg_isready -h postgres -p 5432", fix_candidates=("service:postgres:14",),
        chosen_fix="service:postgres:14", evidence="compose", provenance="service scan",
        data={"service_confidence": "confirmed", "host": "postgres", "port": 5432,
              "bound_config": "DB_STRING",
              "bound_config_url": "postgresql://postgres:test@db:5432/appdb", "db": "appdb"},
    )
    return DepGraph(nodes=(svc,), edges=())


def test_attach_adds_binding_node_and_edge_when_enabled():
    g = attach_in_image_provisioning(_confirmed_pg_graph(), enabled=True)
    bnode = g.get(config_id("DB_STRING"))
    assert bnode is not None
    assert bnode.type is NodeType.CONFIG
    assert bnode.data.get("binding") is True
    assert bnode.chosen_fix == "env:DB_STRING=postgresql://postgres:postgres@127.0.0.1:5432/appdb"
    assert bnode.check_command == \
        'psql "postgresql://postgres:postgres@127.0.0.1:5432/appdb" -c "select 1"'
    assert bnode.data["bind_recipe"]["var"] == "DB_STRING"
    assert "ALTER USER postgres PASSWORD" in bnode.data["bind_recipe"]["alter_user"]
    assert "/etc/profile.d/zz_service_bind.sh" in bnode.data["bind_recipe"]["bind_profile"]
    assert any(e.src == config_id("DB_STRING") and e.dst == service_id("postgres")
               for e in g.edges)


def test_attach_no_binding_node_when_disabled():
    g = attach_in_image_provisioning(_confirmed_pg_graph(), enabled=False)
    assert g.get(config_id("DB_STRING")) is None


def test_attach_no_binding_node_without_bound_config():
    g0 = _confirmed_pg_graph()
    svc = g0.nodes[0]
    bare = DepGraph(nodes=(svc.__class__(**{**svc.__dict__,
        "data": {"service_confidence": "confirmed", "host": "postgres", "port": 5432}}),), edges=())
    g = attach_in_image_provisioning(bare, enabled=True)
    # no bound env var discovered -> no binding node (service still provisioned)
    assert all(n.type is not NodeType.CONFIG for n in g.nodes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_service_binding.py -q`
Expected: FAIL (`bnode is None` — attach doesn't create binding nodes yet).

- [ ] **Step 3: Write minimal implementation**

In `service_scan.py`, add the module constant near the top (after imports, ~line 25):

```python
BIND_PROFILE_PATH = "/etc/profile.d/zz_service_bind.sh"
ALTER_USER_CMD = "runuser -u postgres -- psql -c \"ALTER USER postgres PASSWORD 'postgres'\""
```

Add `config_id` to the ids import (line 22): `from python_deps.depgraph.ids import service_id, syslib_id, config_id`.

In `attach_in_image_provisioning`, inside the `for svc in ...` loop, AFTER the existing `new = new.with_edge(... svc.id -> sysl_id ...)` (line 323-324), add:

```python
        var = svc.data.get("bound_config")
        if var:
            scheme = "postgresql"
            orig = svc.data.get("bound_config_url") or ""
            if "://" in orig:
                scheme = orig.split("://", 1)[0]
            db = svc.data.get("db") or service_db_from_url(orig) or "postgres"
            app_url = service_bind_url(scheme, port, db)
            probe_url = service_bind_url("postgresql", port, db)
            bind_profile = f"echo 'export {var}=\"{app_url}\"' > {BIND_PROFILE_PATH}"
            bnode = Node(
                id=config_id(var), type=NodeType.CONFIG, name=var, layer=Layer.CONFIG,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
                check_command=f'psql "{probe_url}" -c "select 1"',
                fix_candidates=(f"env:{var}={app_url}",), chosen_fix=f"env:{var}={app_url}",
                evidence=f"bind {var} to in-image postgres", provenance="service binding",
                data={"binding": True, "bind_recipe": {
                    "var": var, "url": app_url,
                    "alter_user": ALTER_USER_CMD, "bind_profile": bind_profile}},
            )
            new = new.with_node(bnode)
            new = new.with_edge(Edge(src=bnode.id, dst=svc.id,
                                     relation=EdgeType.REQUIRES, origin="service"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_service_binding.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Certify-flip test (host owns truth)**

Append a test proving the host certify flips the binding node by RUNNING its `check_command`:

```python
# append to tests/depgraph/test_service_binding.py
from python_deps.depgraph.certify import certify  # noqa: E402


class _PsqlExec:
    """Executor: psql probe rc by `ok`; everything else rc 0."""
    def __init__(self, ok): self.ok = ok
    def run(self, command):
        from python_deps.depgraph.probe import CommandResult
        rc = 0 if ("psql" in command and self.ok) else (1 if "psql" in command else 0)
        return CommandResult(command=command, returncode=rc, stdout="", stderr="")


def test_binding_certifies_satisfied_only_when_psql_connects():
    g = attach_in_image_provisioning(_confirmed_pg_graph(), enabled=True)
    bid = config_id("DB_STRING")
    sat = certify(g, bid, _PsqlExec(ok=True), 0)
    assert sat.get(bid).state is State.SATISFIED
    miss = certify(g, bid, _PsqlExec(ok=False), 0)
    assert miss.get(bid).state is not State.SATISFIED
```

Run: `python -m pytest tests/depgraph/test_service_binding.py -q`
Expected: PASS (11 passed). (If `certify` signature differs, adjust the call to match `certify(graph, node_id, executor, cycle)`; confirm `CommandResult` import path from `tests/depgraph/conftest.py`.)

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py tests/depgraph/test_service_binding.py
git commit -m "feat(config-binding): attach binding CONFIG node (psql check, alter+profile recipe) to confirmed postgres"
```

---

## Task 4: relax the CONFIG frontier exclusion for binding nodes

**Files:**
- Modify: `src/python_deps/depgraph/schedule.py` (the `node.type is not NodeType.CONFIG` exclusion, ~line 39)
- Test: `tests/depgraph/test_schedule.py` or `tests/depgraph/test_advise.py` — use the existing schedule test module; if none, create `tests/depgraph/test_schedule_binding.py`

**Interfaces:**
- Consumes: `scheduler_frontier(graph, *, allow_services=False)` (existing), binding nodes with `data["binding"]`.
- Produces: a binding CONFIG node is frontier-eligible when `allow_services` AND its required service is SATISFIED; normal `printenv` CONFIG nodes remain excluded; off-arm (`allow_services=False`) binding nodes are excluded.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_schedule_binding.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType  # noqa: E402
from python_deps.depgraph.ids import service_id, config_id  # noqa: E402
from python_deps.depgraph.schedule import scheduler_frontier  # noqa: E402


def _graph(service_state):
    svc = Node(id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=service_state,
               check_command="pg_isready", fix_candidates=("service:postgres:14",),
               chosen_fix="service:postgres:14", evidence="x", provenance="x",
               data={"service_confidence": "confirmed", "start_recipe": {"start": "x"}})
    binding = Node(id=config_id("DB_STRING"), type=NodeType.CONFIG, name="DB_STRING",
                   layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
                   check_command='psql "u" -c "select 1"', fix_candidates=("env:DB_STRING=u",),
                   chosen_fix="env:DB_STRING=u", evidence="x", provenance="service binding",
                   data={"binding": True})
    edge = Edge(src=binding.id, dst=svc.id, relation=EdgeType.REQUIRES, origin="service")
    return DepGraph(nodes=(svc, binding), edges=(edge,))


def test_binding_in_frontier_when_service_satisfied_and_allowed():
    ids = {n.id for n in scheduler_frontier(_graph(State.SATISFIED), allow_services=True)}
    assert config_id("DB_STRING") in ids


def test_binding_excluded_when_service_unsatisfied():
    ids = {n.id for n in scheduler_frontier(_graph(State.MISSING), allow_services=True)}
    assert config_id("DB_STRING") not in ids


def test_binding_excluded_off_arm():
    ids = {n.id for n in scheduler_frontier(_graph(State.SATISFIED), allow_services=False)}
    assert config_id("DB_STRING") not in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_schedule_binding.py -q`
Expected: FAIL — `test_binding_in_frontier...` fails (CONFIG is excluded today).

- [ ] **Step 3: Write minimal implementation**

In `schedule.py`, locate `_is_actionable` (the predicate `scheduler_frontier` filters on, ~line 30-45). The current CONFIG exclusion clause reads `and node.type is not NodeType.CONFIG`. Replace it so a binding node is allowed through when services are allowed:

```python
        and (node.type is not NodeType.CONFIG
             or (allow_services and bool(node.data.get("binding"))))
```

Ensure `allow_services` is in scope in `_is_actionable` (it already threads to `scheduler_frontier`; pass it through — match the existing `allow_services` parameter the services feature added). If `_is_actionable` does not yet take `allow_services`, add it (`def _is_actionable(graph, node, *, allow_services=False)`) and pass it from `scheduler_frontier`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_schedule_binding.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the schedule suite (no regressions)**

Run: `python -m pytest tests/depgraph/ -k "schedule or advise" -q`
Expected: PASS (existing frontier/partition tests still green — esp. that normal CONFIG nodes stay excluded).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/schedule.py tests/depgraph/test_schedule_binding.py
git commit -m "feat(config-binding): schedule binding CONFIG nodes (relax printenv-CONFIG exclusion, arm-gated)"
```

---

## Task 5: render the binding obligation's facts in the packet

**Files:**
- Modify: `src/python_deps/depgraph/schedule.py` (`frame_obligation` / `ObligationPacket` — add `bind_recipe`)
- Modify: `src/envstate/graph_scheduler.py` (`packet_to_task` — render bind facts)
- Test: `tests/envstate/test_graph_scheduler.py` (extend) — match the file the services feature used for `packet_to_task` tests

**Interfaces:**
- Consumes: a binding node's `data["bind_recipe"]` (Task 3).
- Produces: `ObligationPacket.bind_recipe: dict | None`; `packet_to_task` appends facts instructing the LLM to run `alter_user` then `bind_profile`, naming the host re-check.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/envstate/test_graph_scheduler.py (use the module that already tests packet_to_task)
def test_packet_to_task_renders_binding_facts():
    from src.envstate.graph_scheduler import packet_to_task
    from python_deps.depgraph.schedule import ObligationPacket
    packet = ObligationPacket(
        node_id="config:DB_STRING", title="bind DB_STRING", layer="config",
        chosen_fix="env:DB_STRING=postgresql://postgres:postgres@127.0.0.1:5432/appdb",
        check_command='psql "postgresql://postgres:postgres@127.0.0.1:5432/appdb" -c "select 1"',
        bind_recipe={"var": "DB_STRING",
                     "url": "postgresql://postgres:postgres@127.0.0.1:5432/appdb",
                     "alter_user": "runuser -u postgres -- psql -c \"ALTER USER postgres PASSWORD 'postgres'\"",
                     "bind_profile": "echo 'export DB_STRING=...' > /etc/profile.d/zz_service_bind.sh"})
    task = packet_to_task(packet)
    joined = "\n".join(task.facts)
    assert "ALTER USER postgres PASSWORD" in joined
    assert "/etc/profile.d/zz_service_bind.sh" in joined
```

(Construct `ObligationPacket` with the SAME required positional/keyword fields the existing constructor uses — read its definition first; the test above lists the fields the services feature added. Adjust to match.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/envstate/test_graph_scheduler.py -k binding -q`
Expected: FAIL — `ObligationPacket` has no `bind_recipe` (TypeError) or facts lack the strings.

- [ ] **Step 3: Write minimal implementation**

In `schedule.py`, add `bind_recipe` to `ObligationPacket` (it's a frozen dataclass — add a field with default `None`):

```python
    bind_recipe: dict | None = None
```

In `frame_obligation` (where `start_recipe=node.data.get("start_recipe")` is set, ~line 99), add alongside it:

```python
        bind_recipe=node.data.get("bind_recipe"),
```

In `src/envstate/graph_scheduler.py` `packet_to_task` (after the `start_recipe` facts block, ~line 32), add:

```python
    if packet.bind_recipe:
        br = packet.bind_recipe
        facts.append("set the in-image DB credential, then re-check "
                     f"`{packet.check_command}`: {br['alter_user']}")
        facts.append("persist the app's DB env var for every shell "
                     f"(login shells source it): {br['bind_profile']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/envstate/test_graph_scheduler.py -k binding -q`
Expected: PASS.

- [ ] **Step 5: Run the scheduler suite**

Run: `python -m pytest tests/envstate/test_graph_scheduler.py -q`
Expected: PASS (existing packet/start-recipe tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/schedule.py src/envstate/graph_scheduler.py tests/envstate/test_graph_scheduler.py
git commit -m "feat(config-binding): render alter-user + profile bind facts in the obligation packet"
```

---

## Task 6: extend `confirmed_in_image_services` with `var` + `url`

**Files:**
- Modify: `agent.py` (`_collect_confirmed_in_image_services`, ~line 3129-3154)
- Test: `tests/test_confirmed_in_image_services.py` (extend if it exists, else create)

**Interfaces:**
- Consumes: the final graph's binding CONFIG node (SATISFIED) keyed off the service's `bound_config` var.
- Produces: each service dict gains `"var"` + `"url"` when a SATISFIED binding node exists for it; absent otherwise. Arm-gated (existing gate at line 3135).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_confirmed_in_image_services.py  (create if absent)
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))

from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State  # noqa: E402
from python_deps.depgraph.ids import service_id, config_id  # noqa: E402


def _graph():
    svc = Node(id=service_id("postgres"), type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
               check_command="pg_isready -h 127.0.0.1 -p 5432", fix_candidates=("service:postgres:14",),
               chosen_fix="service:postgres:14", evidence="x", provenance="x",
               data={"service_confidence": "confirmed", "port": 5432, "db": "appdb",
                     "bound_config": "DB_STRING",
                     "start_recipe": {"start": "pg_ctlcluster ...", "wait": "w",
                                      "createdb": "runuser -u postgres -- createdb appdb",
                                      "certify": "pg_isready -h 127.0.0.1 -p 5432", "port": 5432, "db": "appdb"}})
    binding = Node(id=config_id("DB_STRING"), type=NodeType.CONFIG, name="DB_STRING",
                   layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
                   check_command='psql "u" -c "select 1"',
                   fix_candidates=("env:DB_STRING=postgresql://postgres:postgres@127.0.0.1:5432/appdb",),
                   chosen_fix="env:DB_STRING=postgresql://postgres:postgres@127.0.0.1:5432/appdb",
                   evidence="x", provenance="service binding",
                   data={"binding": True, "bind_recipe": {"var": "DB_STRING",
                         "url": "postgresql://postgres:postgres@127.0.0.1:5432/appdb"}})
    return DepGraph(nodes=(svc, binding), edges=())


def test_confirmed_services_includes_var_and_url(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    import agent as agent_mod
    da = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    da._final_dep_graph = _graph()
    out = da._collect_confirmed_in_image_services()
    assert out and out[0]["var"] == "DB_STRING"
    assert out[0]["url"] == "postgresql://postgres:postgres@127.0.0.1:5432/appdb"
```

(Read `_collect_confirmed_in_image_services` first to mirror how it reads `self._final_dep_graph` and the arm gate; adjust the `__new__`/attribute setup so the method's preconditions are met.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_confirmed_in_image_services.py -q`
Expected: FAIL — `out[0]` has no `"var"` key.

- [ ] **Step 3: Write minimal implementation**

In `agent.py` `_collect_confirmed_in_image_services`, inside the per-service loop where `out.append({...})` builds the dict (line 3149), look up the binding node and add the keys:

```python
                entry = {
                    "kind": n.name, "port": recipe.get("port"), "db": recipe.get("db"),
                    "start": recipe.get("start"), "wait": recipe.get("wait"),
                    "createdb": recipe.get("createdb"), "certify": recipe.get("certify"),
                }
                var = n.data.get("bound_config")
                if var:
                    from python_deps.depgraph.ids import config_id
                    bnode = self._final_dep_graph.get(config_id(var))
                    if bnode is not None and bnode.state is State.SATISFIED:
                        entry["var"] = var
                        entry["url"] = bnode.data.get("bind_recipe", {}).get("url")
                out.append(entry)
```

(Use the `State` import already present in the method's scope; if not imported, add `from python_deps.depgraph.schema import State` locally as the method already does for graph types.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_confirmed_in_image_services.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_confirmed_in_image_services.py
git commit -m "feat(config-binding): hand off bound var+url in confirmed_in_image_services"
```

---

## Task 7: binding bake precedence (`ENV` for the eval rebuild)

**Files:**
- Modify: `agent.py` (`_bake_test_env_vars`, ~line 1741-1767)
- Test: `tests/test_agent_config_bake_wiring.py` (extend) or `tests/test_binding_bake.py` (create)

**Interfaces:**
- Consumes: `confirmed_in_image_services` entries with `var`/`url` (Task 6) OR the final graph's SATISFIED binding nodes.
- Produces: after the ledger + config bake passes, `add_env_instruction(var, url)` is called for each bound service var, so its `ENV` line wins (last-call precedence). Arm-gated.

- [ ] **Step 1: Write the failing test**

A source-and-behavior guard: assert the bake calls `add_env_instruction` for the bound var AFTER the ledger/config passes. Use a fake synthesizer recording calls.

```python
# tests/test_binding_bake.py
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))


class _RecSynth:
    def __init__(self): self.calls = []
    def add_env_instruction(self, name, value): self.calls.append((name, value))


def test_binding_value_baked_last(monkeypatch):
    monkeypatch.setenv("DOCKERAGENT_ENABLE_SERVICE_PROVISION", "1")
    import agent as agent_mod
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.ids import config_id
    binding = Node(id=config_id("DB_STRING"), type=NodeType.CONFIG, name="DB_STRING",
                   layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN, state=State.SATISFIED,
                   check_command="psql", fix_candidates=("env:DB_STRING=URL",), chosen_fix="env:DB_STRING=URL",
                   evidence="x", provenance="service binding",
                   data={"binding": True, "bind_recipe": {"var": "DB_STRING", "url": "URL_BOUND"}})
    da = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    da.synthesizer = _RecSynth(); da.action_ledger = None
    da._final_dep_graph = DepGraph(nodes=(binding,), edges=())
    da._bake_test_env_vars()
    assert ("DB_STRING", "URL_BOUND") in da.synthesizer.calls
    # binding is the LAST writer for DB_STRING
    db_calls = [v for (n, v) in da.synthesizer.calls if n == "DB_STRING"]
    assert db_calls[-1] == "URL_BOUND"
```

(Read `_bake_test_env_vars` first; set up the minimal attributes it touches so it runs without a real synthesizer/container. If it references other `self.` attributes, set them to inert values in the test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_binding_bake.py -q`
Expected: FAIL — no `("DB_STRING", "URL_BOUND")` call.

- [ ] **Step 3: Write minimal implementation**

In `agent.py` `_bake_test_env_vars`, AFTER the `bakeable_config_env` loop (line ~1767), add the binding pass:

```python
            if graph is not None and os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1":
                from python_deps.depgraph.schema import NodeType, State
                for n in graph.nodes:
                    if (n.type is NodeType.CONFIG and n.data.get("binding")
                            and n.state is State.SATISFIED):
                        br = n.data.get("bind_recipe", {})
                        if br.get("var") and br.get("url"):
                            self.synthesizer.add_env_instruction(br["var"], br["url"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_binding_bake.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_binding_bake.py
git commit -m "feat(config-binding): bake bound DB url as ENV with last-writer precedence"
```

---

## Task 8: eval test wrapper `export`

**Files:**
- Modify: `run_repo2run_benchmark.py` (`compose_in_image_service_commands`, ~line 2430-2447)
- Test: `tests/test_service_provision_off_state.py` (extend) or `tests/test_eval_service_compose.py` (create)

**Interfaces:**
- Consumes: `confirmed_in_image_services` entries with `var`/`url`.
- Produces: an `export <VAR>=<shell-quoted url>` line emitted in the runtime commands (so both collect + verification wrappers carry it), placed AFTER start/wait/createdb so the DB is up first. No line when `var` absent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_service_compose.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import run_repo2run_benchmark as R  # noqa: E402


def test_compose_emits_export_when_var_present():
    rs = {"confirmed_in_image_services": [{
        "kind": "postgres", "start": "S", "wait": "W", "createdb": "C",
        "var": "DB_STRING", "url": "postgresql://postgres:postgres@127.0.0.1:5432/appdb"}]}
    cmds = R.compose_in_image_service_commands(rs)
    assert any(c.startswith("export DB_STRING=") for c in cmds)
    # export comes after createdb
    assert cmds.index("C") < next(i for i, c in enumerate(cmds) if c.startswith("export DB_STRING="))


def test_compose_no_export_without_var():
    rs = {"confirmed_in_image_services": [{"kind": "postgres", "start": "S", "wait": "W"}]}
    cmds = R.compose_in_image_service_commands(rs)
    assert not any(c.startswith("export ") for c in cmds)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_eval_service_compose.py -q`
Expected: FAIL — no `export` line.

- [ ] **Step 3: Write minimal implementation**

In `run_repo2run_benchmark.py`, ensure `import shlex` is present at top (add if missing). In `compose_in_image_service_commands`, inside the per-service loop, after appending `createdb`, add:

```python
        if svc.get("var") and svc.get("url"):
            out.append(f"export {svc['var']}={shlex.quote(svc['url'])}")
```

(Match the actual accumulator name in the function — the recon shows it appends `svc["start"]`, `svc["wait"]`, `svc["createdb"]`; append the export to the same list, guarded so off-state stays `[]`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_eval_service_compose.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add run_repo2run_benchmark.py tests/test_eval_service_compose.py
git commit -m "feat(config-binding): export bound DB url in the eval test wrapper"
```

---

## Task 9: synthesizer drops binding runtime commands from the baked build

**Files:**
- Modify: `src/synthesizer.py` (`_is_runtime_service_segment`, the matcher the services feature extended)
- Test: the synthesizer test module the services feature used (search `_is_runtime_service_segment`); extend it

**Interfaces:**
- Consumes: ledger command strings.
- Produces: `ALTER USER ... PASSWORD` (incl. `runuser`/`su` wrapped) and the `/etc/profile.d/zz_service_bind.sh` write and a bare `export <VAR>=postgresql://...` are classified as runtime service segments → dropped from `build_commands` (they must not be baked as `RUN`; the `ENV` bake + wrapper export carry them). Legitimate `pip install`/build lines still survive.

- [ ] **Step 1: Write the failing test**

```python
# add to the existing synthesizer runtime-segment test module
def test_drops_alter_user_and_profile_bind():
    assert _is_runtime_service_segment(
        "runuser -u postgres -- psql -c \"ALTER USER postgres PASSWORD 'postgres'\"")
    assert _is_runtime_service_segment(
        "echo 'export DB_STRING=\"postgresql://postgres:postgres@127.0.0.1:5432/appdb\"' > /etc/profile.d/zz_service_bind.sh")
    # a normal build line is NOT a runtime service segment
    assert not _is_runtime_service_segment("pip install poetry")
```

(Import `_is_runtime_service_segment` the same way the existing tests in that module do.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest <that_module> -k "alter_user or runtime_service" -q`
Expected: FAIL — `ALTER USER` / profile.d lines not yet matched.

- [ ] **Step 3: Write minimal implementation**

In `src/synthesizer.py` `_is_runtime_service_segment`, add patterns to the existing match set (it already matches `pg_ctlcluster.*start`, `createdb`, `createuser`):

```python
        r"ALTER\s+USER\s+\w+\s+(WITH\s+)?PASSWORD",
        r"/etc/profile\.d/zz_service_bind\.sh",
```

(Add them to the same compiled-regex tuple / `any(re.search(...))` the function already uses; keep `re.I` consistent with the existing entries.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest <that_module> -q`
Expected: PASS (existing service-drop tests still green; positive build-survivor assertions intact).

- [ ] **Step 5: Commit**

```bash
git add src/synthesizer.py <that_test_module>
git commit -m "feat(config-binding): drop alter-user + profile-bind runtime commands from baked build"
```

---

## Task 10: off-state byte-identity + full regression

**Files:**
- Modify: `tests/test_service_provision_off_state.py` (add binding off-state tests)
- Test: same file

**Interfaces:** none new — this task only adds tests proving off-arm byte-identity across the new paths and runs the whole suite.

- [ ] **Step 1: Write the failing-then-green off-state tests**

Add tests asserting:
1. `attach_in_image_provisioning(graph_with_confirmed_pg_and_bound_config, enabled=False).to_dict() == graph.to_dict()` (no binding CONFIG node added off-arm).
2. `scheduler_frontier(graph_with_binding_and_satisfied_service, allow_services=False)` excludes the binding node id.
3. `compose_in_image_service_commands({"verified_test_commands": ["pytest"]}) == []` (already covered — keep) and a service dict WITHOUT `var` yields no `export`.

```python
# append to tests/test_service_provision_off_state.py
def test_binding_off_state_byte_identical():
    from python_deps.depgraph.service_scan import attach_in_image_provisioning
    from python_deps.depgraph.ids import config_id
    g = _confirmed_pg_graph_with_binding()   # build a confirmed pg node w/ bound_config in data
    assert attach_in_image_provisioning(g, enabled=False).to_dict() == g.to_dict()
    assert attach_in_image_provisioning(g, enabled=True).get(config_id("DB_STRING")) is not None
```

(Reuse/adapt the helper from `tests/depgraph/test_service_binding.py`; the `enabled=True` arm of the assertion proves the off-state test is non-vacuous — the trigger genuinely fires on-arm.)

- [ ] **Step 2: Run the new tests**

Run: `python -m pytest tests/test_service_provision_off_state.py -q`
Expected: PASS.

- [ ] **Step 3: Full regression**

Run: `python -m pytest tests/ -q` (or the project's standard suite invocation)
Expected: PASS with no new failures vs the documented pre-existing baseline (the 4 known-pre-existing failures from the handoff are allowed; nothing else).

- [ ] **Step 4: Commit**

```bash
git add tests/test_service_provision_off_state.py
git commit -m "test(config-binding): off-state byte-identity for binding paths + full regression"
```

---

## Self-review (controller, before dispatch)

- **Spec coverage:** Task 1 (rewrite §3.2), Task 2 (discovery §3.1), Task 3 (binding node + ALTER USER §3.2/§3.5), Tasks 4–5 (scheduler/frontier §3.5), Task 6 (handoff §3.3 eval), Task 7 (ENV bake §3.3), Task 8 (wrapper export §3.3), Task 9 (synthesizer drop §3.3), Task 10 (off-state §4/§6). Host certify (§3.4) = Task 3 Step 5 (existing CONFIG-layer walk; no code change).
- **Type consistency:** `bound_config`/`bound_config_url`/`db` (node data); `bind_recipe={var,url,alter_user,bind_profile}`; `config_id(var)`; `var`/`url` (handoff dict + ObligationPacket.bind_recipe). Used identically across tasks.
- **Anti-hollow:** binding flips only via the psql `check_command` (Task 3 Step 5); done-gate unchanged; off-arm byte-identical (Task 10).
