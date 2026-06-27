# Services Tier — Advisory/Discovery Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the *graph representation* of services (tier 5): discover service needs from a repo, append confidence-annotated `SERVICE` nodes to the depgraph, and render them in the advisory — entirely inside `python_deps/depgraph/`, mirroring the Config slice. No emit, no orchestration, no live certification.

**Architecture:** Additive schema (`Node.data` bag + `Layer.SERVICES` enum member + `service_id`), a curated `package→service` table, a pure `service_scan` stage (CI/compose YAML parsers + URL-scheme mapper + a failure-error classifier primitive + the `scan_services` orchestrator), wired into `build_dep_graph` after `scan_config`, plus a SERVICES block in the advisory render. Service nodes are discovered as `state=UNKNOWN` and deliberately **not** certified (a `certify` skip-guard keeps `nc -z`/`pg_isready` from running in the sidecar-less scratch container).

**Tech Stack:** Python 3.11, `pytest`, stdlib `ast`/`os`/`re`/`urllib.parse`, `yaml` (PyYAML 6.x, available), frozen `dataclasses`.

## Global Constraints

- **Immutability:** every "mutation" returns a NEW `DepGraph`/`Node` (frozen dataclasses; `with_node`/`with_edge`/`replace`). Verbatim from the spec/`schema.py`.
- **Certification invariant:** a node's `state` is flipped ONLY by its `check_command`; discovery never sets `state` beyond `UNKNOWN`. SERVICE nodes are **not certified in this slice** — they stay `UNKNOWN`.
- **Default-safe / off-state byte-identical:** all new code runs only inside `build_dep_graph` (gated upstream by the dep-graph feature). No new flag.
- **Pure discovery stages take NO executor** — `service_scan` reads the repo on disk + the in-progress graph only.
- **Structural `requires` edges require evidence:** only **confirmed** services (CI/compose) get a `Package→Service` edge; **inferred** services (package/URL guesses) are nodes only — no `requires` edge (no false necessary conditions).
- **Graceful:** YAML parse failures / missing files degrade to "fewer nodes", never a crash.
- **Target:** Python 3.11. **TDD:** failing test first, watch it fail, implement minimally, watch it pass.

**In scope:** schema additions + services discovery (all 5 sources) + advisory render.
**Out of scope (separate runner-level spec, design §8):** emit (run-before-tests sidecar), escalation routing, live reachability certification, `Service→Service` ordering, in-image mode.

---

### Task 1: Schema — `Node.data`, `Layer.SERVICES`, `service_id`

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (`Node` dataclass, `Layer` enum)
- Modify: `src/python_deps/depgraph/ids.py`
- Test: `tests/depgraph/test_schema.py`

**Interfaces:**
- Produces: `Node.data: dict` (frozen to a `MappingProxyType`, default empty, included in `to_dict`); `Layer.SERVICES` (`"services"`, NOT added to `certify._LAYER_ORDER`); `ids.service_id(name) -> "service:<name>"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schema.py`:

```python
def test_service_layer_and_id():
    from python_deps.depgraph.schema import Layer
    assert Layer.SERVICES.value == "services"
    assert ids.service_id("postgres") == "service:postgres"


def test_node_data_defaults_empty_and_frozen():
    n = make_node("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES)
    assert dict(n.data) == {}
    import types as _t
    assert isinstance(n.data, _t.MappingProxyType)


def test_node_data_roundtrips_and_serializes():
    from python_deps.depgraph.schema import Node
    n = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
             layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
             data={"service_confidence": "confirmed", "port": 5432})
    assert n.data["service_confidence"] == "confirmed"
    assert n.to_dict()["data"] == {"service_confidence": "confirmed", "port": 5432}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_schema.py -k "service_layer or node_data" -v`
Expected: FAIL — `AttributeError: SERVICES` / `service_id` / `Node.__init__() got an unexpected keyword argument 'data'`.

- [ ] **Step 3: Write minimal implementation**

In `schema.py`, add `SERVICES` to the `Layer` enum (after `CONFIG`):

```python
class Layer(enum.Enum):
    INTERPRETER = "interpreter"
    SYSTEM = "system"
    TOOLCHAIN = "toolchain"
    PIP = "pip"
    NAMING = "naming"
    RUNTIME = "runtime"
    TESTS = "tests"
    CONFIG = "config"
    SERVICES = "services"
```

In `schema.py`, add a `data` field to `Node` (place it right after `exclude_newer`, the last existing field; `field` and `types` are already imported, used by `Edge`):

```python
    data: dict = field(default_factory=dict)  # general per-node metadata bag
```

Extend `Node.__post_init__` to also freeze `data` (mirror `Edge.__post_init__`), keeping the existing tier-derivation:

```python
    def __post_init__(self) -> None:
        if self.tier == 0:
            object.__setattr__(self, "tier", tier_for_type(self.type))
        if not isinstance(self.data, types.MappingProxyType):
            object.__setattr__(self, "data", types.MappingProxyType(dict(self.data)))
```

In `Node.to_dict`, add `"data": dict(self.data),` (e.g. after the `"exclude_newer"` line).

In `ids.py`, add:

```python
def service_id(name: str) -> str:
    return f"service:{name}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_schema.py -v`
Expected: PASS (all schema tests; `data` is default-safe).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schema.py src/python_deps/depgraph/ids.py tests/depgraph/test_schema.py
git commit -m "feat(depgraph): add Node.data bag, Layer.SERVICES, service_id"
```

---

### Task 2: Certify skip-guard for SERVICE nodes

**Files:**
- Modify: `src/python_deps/depgraph/certify.py` (`certify`)
- Test: `tests/depgraph/test_certify.py`

**Interfaces:**
- Consumes: `NodeType.SERVICE`.
- Produces: `certify` leaves a SERVICE node `UNKNOWN` even if it has a `check_command` (live certification is the future action layer; never run `nc -z` in the scratch container).

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_certify.py`:

```python
def test_certify_skips_service_nodes():
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.certify import certify

    class FakeResult:
        def __init__(self, ok): self.ok = ok; self.stdout = ""; self.stderr = ""
    class FakeExecutor:
        def __init__(self): self.calls = []
        def run(self, cmd): self.calls.append(cmd); return FakeResult(ok=True)

    svc = Node(id="service:postgres", type=NodeType.SERVICE, name="postgres",
               layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
               check_command="pg_isready -h postgres -p 5432")
    g = DepGraph().with_node(svc)
    ex = FakeExecutor()
    out = certify(g, "service:postgres", ex)
    assert out.get("service:postgres").state is State.UNKNOWN  # never certified
    assert ex.calls == []  # the probe was never run in the scratch container
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_certify.py::test_certify_skips_service_nodes -v`
Expected: FAIL — `certify` runs the check_command, flipping the node to `SATISFIED` and recording a call.

- [ ] **Step 3: Write minimal implementation**

In `certify.py`, add an early-return in `certify` for SERVICE nodes (after the existing `node is None or not node.check_command` guard). Add the import `from python_deps.depgraph.schema import DepGraph, Layer, State, NodeType` (extend the existing schema import with `NodeType`):

```python
    node = graph.get(node_id)
    if node is None or not node.check_command:
        return graph
    # Services are certified by reachability against a RUNNING instance, which the
    # single scratch container cannot provide (design §3.3). Live certification is
    # the future runner-level action layer; here they stay UNKNOWN.
    if node.type is NodeType.SERVICE:
        return graph
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_certify.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/certify.py tests/depgraph/test_certify.py
git commit -m "feat(depgraph): certify skip-guard for Service nodes (stay UNKNOWN)"
```

---

### Task 3: `package → service` table

**Files:**
- Create: `src/python_deps/depgraph/service_tables.py`
- Test: `tests/depgraph/test_service_tables.py`

**Interfaces:**
- Produces:
  - `services_for_package(name: str) -> list[str]` — service kinds a distribution implies (normalized lookup, fresh list, `[]` if unknown).
  - `service_defaults(kind: str) -> tuple[str, int]` — `(image, port)` for a kind (e.g. `("postgres:16", 5432)`); raises `KeyError` for an unknown kind.
  - `KNOWN_SERVICE_KINDS: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_service_tables.py`:

```python
from python_deps.depgraph.service_tables import (
    services_for_package, service_defaults, KNOWN_SERVICE_KINDS,
)


def test_psycopg2_implies_postgres():
    assert "postgres" in services_for_package("psycopg2")


def test_lookup_normalized_and_fresh():
    a = services_for_package("Psycopg2")
    assert a == services_for_package("psycopg2")
    a.append("x")
    assert services_for_package("psycopg2") != a


def test_unknown_package_empty():
    assert services_for_package("requests") == []


def test_service_defaults():
    assert service_defaults("postgres") == ("postgres:16", 5432)
    assert service_defaults("redis")[1] == 6379
    assert set(KNOWN_SERVICE_KINDS) >= {"postgres", "redis", "mongo", "rabbitmq"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_tables.py -v`
Expected: FAIL — `ModuleNotFoundError: service_tables`.

- [ ] **Step 3: Write minimal implementation**

Create `src/python_deps/depgraph/service_tables.py`:

```python
"""Curated `package -> service` table (tier-5 analogue of
``config_tables.PACKAGE_TO_CONFIG``).  A driver distribution implies a server it
talks to: ``psycopg2`` -> postgres, ``redis`` -> redis, ``pymongo`` -> mongo.

These are INFERRED signals (the suite may mock the DB), so callers must NOT turn
them into structural ``requires`` edges without corroborating evidence (design
§4).  ``celery``/``kombu`` map to a generic ``broker`` kind, not a specific one.
"""

from __future__ import annotations

from python_deps.import_mapping import normalize_package_name

# service kind -> (default image, default port)
SERVICE_DEFAULTS: dict[str, tuple[str, int]] = {
    "postgres": ("postgres:16", 5432),
    "mysql": ("mysql:8", 3306),
    "redis": ("redis:7", 6379),
    "mongo": ("mongo:7", 27017),
    "rabbitmq": ("rabbitmq:3", 5672),
    "broker": ("redis:7", 6379),       # generic broker; default to redis
    "elasticsearch": ("elasticsearch:8", 9200),
}

KNOWN_SERVICE_KINDS: frozenset[str] = frozenset(SERVICE_DEFAULTS)

PACKAGE_TO_SERVICE: dict[str, list[str]] = {
    "psycopg2": ["postgres"],
    "psycopg2-binary": ["postgres"],
    "psycopg": ["postgres"],
    "asyncpg": ["postgres"],
    "mysqlclient": ["mysql"],
    "pymysql": ["mysql"],
    "redis": ["redis"],
    "pymongo": ["mongo"],
    "mongoengine": ["mongo"],
    "pika": ["rabbitmq"],
    "kombu": ["broker"],
    "celery": ["broker"],
    "elasticsearch": ["elasticsearch"],
}

_NORMALIZED: dict[str, list[str]] = {
    normalize_package_name(name): kinds for name, kinds in PACKAGE_TO_SERVICE.items()
}


def services_for_package(name: str) -> list[str]:
    """Service kinds a distribution implies, or ``[]`` (fresh list)."""
    return list(_NORMALIZED.get(normalize_package_name(name), ()))


def service_defaults(kind: str) -> tuple[str, int]:
    """`(image, port)` for a known service kind; raises KeyError if unknown."""
    return SERVICE_DEFAULTS[kind]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_tables.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_tables.py tests/depgraph/test_service_tables.py
git commit -m "feat(depgraph): add package->service table + service defaults"
```

---

### Task 4: URL-scheme → service mapper

**Files:**
- Create: `src/python_deps/depgraph/service_scan.py`
- Test: `tests/depgraph/test_service_scan.py`

**Interfaces:**
- Produces: `service_from_url(value: str) -> tuple[str, str | None, int | None] | None` — maps a URL/scheme to `(kind, host, port)`; `None` if the scheme is unknown. Host/port are parsed when present, else `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_service_scan.py`:

```python
from python_deps.depgraph.service_scan import service_from_url


def test_postgres_url_full():
    assert service_from_url("postgres://u:p@db:5432/app") == ("postgres", "db", 5432)


def test_scheme_aliases():
    assert service_from_url("postgresql://x")[0] == "postgres"
    assert service_from_url("redis://cache:6379/0") == ("redis", "cache", 6379)
    assert service_from_url("mongodb://m/db")[0] == "mongo"
    assert service_from_url("amqp://broker")[0] == "rabbitmq"


def test_sqlite_and_unknown_return_none():
    assert service_from_url("sqlite:///db.sqlite3") is None
    assert service_from_url("not-a-url") is None
    assert service_from_url("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_scan.py -k url -v`
Expected: FAIL — `ModuleNotFoundError: service_scan`.

- [ ] **Step 3: Write minimal implementation**

Create `src/python_deps/depgraph/service_scan.py`:

```python
"""Static Service-tier discovery (design 2026-06-25-services-tier-design.md).

Pure (no Executor, no network): reads the repo on disk + the in-progress graph,
and appends confidence-annotated ``SERVICE`` nodes. Sources: CI ``services:`` /
compose ``services:`` (confirmed), ``*_URL`` config schemes + ``package->service``
table (inferred). Structural ``Package->Service`` edges are emitted only for
confirmed services (no false necessary conditions).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

try:  # PyYAML is available; degrade gracefully if ever absent.
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# URL scheme -> canonical service kind.
_SCHEME_TO_KIND: dict[str, str] = {
    "postgres": "postgres", "postgresql": "postgres", "postgresql+psycopg2": "postgres",
    "mysql": "mysql", "mysql+pymysql": "mysql",
    "redis": "redis", "rediss": "redis",
    "mongodb": "mongo", "mongodb+srv": "mongo",
    "amqp": "rabbitmq", "amqps": "rabbitmq",
    "elasticsearch": "elasticsearch",
}


def service_from_url(value: str) -> tuple[str, str | None, int | None] | None:
    """`(kind, host, port)` for a service URL/scheme, or None if unknown."""
    if not value or "://" not in value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    kind = _SCHEME_TO_KIND.get(parsed.scheme.lower())
    if kind is None:
        return None
    host = parsed.hostname
    port = parsed.port if _has_port(parsed) else None
    return kind, host, port


def _has_port(parsed) -> bool:
    try:
        return parsed.port is not None
    except ValueError:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_scan.py -k url -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py tests/depgraph/test_service_scan.py
git commit -m "feat(depgraph): URL-scheme -> service kind mapper"
```

---

### Task 5: CI + compose `services:` parsers (confirmed)

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py`
- Test: `tests/depgraph/test_service_scan.py`

**Interfaces:**
- Produces:
  - `scan_compose_services(repo_path) -> dict[str, dict]` — `{kind: {"image": str, "port": int|None, "source": str}}` from `docker-compose*.yml` `services:`.
  - `scan_ci_services(repo_path) -> tuple[dict[str, dict], bool]` — same map from `.github/workflows/*.yml` job `services:`, plus a `ci_services_block_present` bool (True if any workflow had a `services:` block, used for authoritative suppression).
  - Both best-effort: a kind is recognized by matching the service name OR its image against `KNOWN_SERVICE_KINDS`.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_service_scan.py` (reuse a local `_write` like the config tests):

```python
import textwrap
from python_deps.depgraph.service_scan import scan_compose_services, scan_ci_services


def _w(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_scan_compose_services(tmp_path):
    _w(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:15
            ports: ["5432:5432"]
          cache:
            image: redis:7
    """)
    found = scan_compose_services(str(tmp_path))
    assert found["postgres"]["image"] == "postgres:15"
    assert "redis" in found


def test_scan_ci_services_and_presence(tmp_path):
    _w(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          test:
            services:
              postgres:
                image: postgres:14
    """)
    found, present = scan_ci_services(str(tmp_path))
    assert present is True
    assert "postgres" in found


def test_scan_ci_no_services_block(tmp_path):
    _w(tmp_path, ".github/workflows/ci.yml", "jobs:\n  test:\n    steps: []\n")
    found, present = scan_ci_services(str(tmp_path))
    assert found == {} and present is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_scan.py -k "compose or ci_services or ci_no" -v`
Expected: FAIL — `ImportError: cannot import name 'scan_compose_services'`.

- [ ] **Step 3: Write minimal implementation**

Add to `service_scan.py` (uses `service_tables`):

```python
from python_deps.depgraph.service_tables import KNOWN_SERVICE_KINDS


def _kind_of(service_name: str, image: str | None) -> str | None:
    """Recognize a service kind from its compose/CI name or image."""
    name = (service_name or "").lower()
    for kind in KNOWN_SERVICE_KINDS:
        if kind in name:
            return kind
    img = (image or "").lower()
    for kind in KNOWN_SERVICE_KINDS:
        if kind in img:
            return kind
    # common image aliases not equal to the kind token
    if "postgres" in img or "postgis" in img:
        return "postgres"
    return None


def _port_of(entry: dict) -> int | None:
    ports = entry.get("ports")
    if isinstance(ports, list) and ports:
        first = str(ports[0])
        tail = first.split(":")[-1].split("/")[0]
        return int(tail) if tail.isdigit() else None
    return None


def _services_from_yaml_doc(doc, source: str, out: dict[str, dict]) -> None:
    """Merge a parsed YAML doc's `services:` blocks into `out` (first kind wins)."""
    if not isinstance(doc, dict):
        return
    blocks = []
    if isinstance(doc.get("services"), dict):
        blocks.append(doc["services"])           # compose top-level
    for job in (doc.get("jobs") or {}).values() if isinstance(doc.get("jobs"), dict) else []:
        if isinstance(job, dict) and isinstance(job.get("services"), dict):
            blocks.append(job["services"])       # GitHub Actions job.services
    for block in blocks:
        for svc_name, entry in block.items():
            entry = entry if isinstance(entry, dict) else {}
            image = entry.get("image")
            kind = _kind_of(svc_name, image)
            if kind and kind not in out:
                out[kind] = {"image": image or "", "port": _port_of(entry), "source": source}


def _load_yaml(path: str):
    if yaml is None:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def scan_compose_services(repo_path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for fname in os.listdir(repo_path) if os.path.isdir(repo_path) else []:
        low = fname.lower()
        if low.startswith("docker-compose") and low.endswith((".yml", ".yaml")):
            doc = _load_yaml(os.path.join(repo_path, fname))
            _services_from_yaml_doc(doc, f"docker-compose: {fname}", out)
    return out


def scan_ci_services(repo_path: str) -> tuple[dict[str, dict], bool]:
    out: dict[str, dict] = {}
    present = False
    wf_dir = os.path.join(repo_path, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return out, present
    for fname in os.listdir(wf_dir):
        if not fname.lower().endswith((".yml", ".yaml")):
            continue
        doc = _load_yaml(os.path.join(wf_dir, fname))
        if isinstance(doc, dict) and isinstance(doc.get("jobs"), dict):
            for job in doc["jobs"].values():
                if isinstance(job, dict) and isinstance(job.get("services"), dict) and job["services"]:
                    present = True
        _services_from_yaml_doc(doc, f".github/workflows/{fname}", out)
    return out, present
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py tests/depgraph/test_service_scan.py
git commit -m "feat(depgraph): CI + compose services: parsers (confirmed discovery)"
```

---

### Task 6: Failure-error → service classifier (primitive)

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py`
- Test: `tests/depgraph/test_service_scan.py`

**Interfaces:**
- Produces: `classify_service_error(text: str) -> str | None` — maps a connection-failure log to a service kind, or `None`. **Standalone primitive** for the future failure-driven/repair path; NOT invoked by the static `scan_services` (no failure text at build time).

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_service_scan.py`:

```python
from python_deps.depgraph.service_scan import classify_service_error


def test_classify_service_errors():
    assert classify_service_error("psycopg2.OperationalError: could not connect to server") == "postgres"
    assert classify_service_error("redis.exceptions.ConnectionError: Error 111 connecting") == "redis"
    assert classify_service_error("pymongo.errors.ServerSelectionTimeoutError: ...") == "mongo"
    assert classify_service_error("ImportError: no module named foo") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_scan.py -k classify -v`
Expected: FAIL — `ImportError: cannot import name 'classify_service_error'`.

- [ ] **Step 3: Write minimal implementation**

Add to `service_scan.py`:

```python
import re as _re

# Ordered (signature regex -> kind). First match wins.
_ERROR_SIGNATURES: tuple[tuple[object, str], ...] = (
    (_re.compile(r"could not connect to server|psycopg2\.OperationalError|connection to server at", _re.I), "postgres"),
    (_re.compile(r"redis(\.exceptions)?\.ConnectionError|Error \d+ connecting to .*redis", _re.I), "redis"),
    (_re.compile(r"pymongo\.errors\.(ServerSelectionTimeoutError|ConnectionFailure)", _re.I), "mongo"),
    (_re.compile(r"amqp|pika\.exceptions|AMQPConnectionError|rabbitmq", _re.I), "rabbitmq"),
    (_re.compile(r"OperationalError.*MySQL|Can't connect to MySQL server", _re.I), "mysql"),
)


def classify_service_error(text: str) -> str | None:
    """Map a connection-failure log to a service kind (failure-driven primitive)."""
    if not text:
        return None
    for pattern, kind in _ERROR_SIGNATURES:
        if pattern.search(text):
            return kind
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py tests/depgraph/test_service_scan.py
git commit -m "feat(depgraph): connection-error -> service classifier primitive"
```

---

### Task 7: `scan_services` orchestrator

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py`
- Test: `tests/depgraph/test_service_scan.py`

**Interfaces:**
- Consumes: `scan_ci_services`, `scan_compose_services`, `service_from_url` (Tasks 4–5); `services_for_package`, `service_defaults` (Task 3); `service_id` (Task 1); schema types.
- Produces: `scan_services(repo_path: str, graph: DepGraph) -> DepGraph` — appends `SERVICE` nodes. **Confirmed** (CI/compose) carry `data["service_confidence"]="confirmed"` and a `Package→Service` `requires` edge from each in-graph package that maps to the kind (else no edge). **Inferred** (URL scheme on a Config node, or `package→service`) carry `"inferred"`, an `inducing_package`/`bound_config` in `data`, and **no** `requires` edge. If a CI `services:` block is present, inferred kinds absent from CI are suppressed. Returns a NEW graph.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_service_scan.py`:

```python
from python_deps.depgraph.service_scan import scan_services
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, DiscoveredBy, EdgeType,
)
from python_deps.depgraph.ids import service_id, package_id, project_id, config_id


def _graph(pkgs=("psycopg2",), configs=()):
    g = DepGraph().with_node(Node(id=project_id("app"), type=NodeType.PROJECT,
        name="app", layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN))
    for p in pkgs:
        g = g.with_node(Node(id=package_id(p, "1.0"), type=NodeType.PACKAGE, name=p,
            layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="1.0"))
    for var, fix in configs:
        g = g.with_node(Node(id=config_id(var), type=NodeType.CONFIG, name=var,
            layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN,
            fix_candidates=(fix,)))
    return g


def test_confirmed_ci_service_gets_node_and_package_edge(tmp_path):
    _w(tmp_path, ".github/workflows/ci.yml",
       "jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:14\n")
    g = scan_services(str(tmp_path), _graph(pkgs=("psycopg2",)))
    node = g.get(service_id("postgres"))
    assert node is not None and node.type is NodeType.SERVICE and node.tier == 5
    assert node.data["service_confidence"] == "confirmed"
    assert any(e.src == package_id("psycopg2", "1.0") and e.dst == service_id("postgres")
               and e.relation is EdgeType.REQUIRES for e in g.edges)


def test_inferred_package_service_has_no_requires_edge(tmp_path):
    g = scan_services(str(tmp_path), _graph(pkgs=("psycopg2",)))   # no CI/compose
    node = g.get(service_id("postgres"))
    assert node is not None and node.data["service_confidence"] == "inferred"
    assert node.data.get("inducing_package") == "psycopg2"
    assert not any(e.dst == service_id("postgres") for e in g.edges)  # no structural edge


def test_inferred_suppressed_when_ci_block_present_without_it(tmp_path):
    # CI declares redis only; psycopg2-inferred postgres must be suppressed.
    _w(tmp_path, ".github/workflows/ci.yml",
       "jobs:\n  test:\n    services:\n      redis:\n        image: redis:7\n")
    g = scan_services(str(tmp_path), _graph(pkgs=("psycopg2",)))
    assert g.get(service_id("postgres")) is None
    assert g.get(service_id("redis")) is not None


def test_inferred_from_config_url(tmp_path):
    g = scan_services(str(tmp_path),
        _graph(pkgs=(), configs=[("DATABASE_URL", "env:DATABASE_URL=postgres://db:5432/x")]))
    node = g.get(service_id("postgres"))
    assert node.data["service_confidence"] == "inferred"
    assert node.data.get("bound_config") == "DATABASE_URL"
    assert node.data.get("port") == 5432
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_scan.py -k "confirmed or inferred" -v`
Expected: FAIL — `ImportError: cannot import name 'scan_services'`.

- [ ] **Step 3: Write minimal implementation**

Add to `service_scan.py` (extend imports at top: `from python_deps.depgraph.ids import service_id`; `from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType`; `from python_deps.depgraph.service_tables import services_for_package, service_defaults`):

```python
def _service_node(kind: str, *, confidence: str, discovered_by: DiscoveredBy,
                  evidence: str, extra: dict) -> Node:
    image, port = service_defaults(kind) if kind in KNOWN_SERVICE_KINDS else (f"{kind}:latest", None)
    data = {"service_confidence": confidence, "image": extra.get("image") or image,
            "host": extra.get("host") or kind, "port": extra.get("port") or port}
    data.update({k: v for k, v in extra.items() if k in ("bound_config", "inducing_package")})
    p = data["port"]
    return Node(
        id=service_id(kind), type=NodeType.SERVICE, name=kind, layer=Layer.SERVICES,
        discovered_by=discovered_by, state=State.UNKNOWN,
        check_command=f"pg_isready -h {data['host']} -p {p}" if kind == "postgres"
                      else f"nc -z {data['host']} {p}" if p else None,
        fix_candidates=(f"service:{data['image']}",), chosen_fix=f"service:{data['image']}",
        evidence=evidence, provenance="service scan", data=data,
    )


def scan_services(repo_path: str, graph: DepGraph) -> DepGraph:
    """Append confidence-annotated SERVICE nodes (design 2026-06-25 §5). NEW graph."""
    compose = scan_compose_services(repo_path)
    ci, ci_present = scan_ci_services(repo_path)
    confirmed: dict[str, dict] = {**compose, **ci}           # CI wins on conflict

    packages = [n for n in graph.nodes if n.type is NodeType.PACKAGE]
    project = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
    test = next((n for n in graph.nodes if n.type is NodeType.TEST), None)
    anchor = project or test

    # Inferred: package->service, and *_URL config schemes.
    inferred: dict[str, dict] = {}
    for pkg in packages:
        for kind in services_for_package(pkg.name):
            inferred.setdefault(kind, {"inducing_package": pkg.name})
    for cfg in [n for n in graph.nodes if n.type is NodeType.CONFIG]:
        for fix in cfg.fix_candidates:
            url = fix.split("=", 1)[1] if "=" in fix else ""
            hit = service_from_url(url)
            if hit:
                kind, host, port = hit
                inferred.setdefault(kind, {})
                inferred[kind].update({"bound_config": cfg.name, "host": host, "port": port})

    new = graph

    # Confirmed nodes (+ Package->Service edges where a package maps to the kind).
    for kind, meta in confirmed.items():
        node = _service_node(kind, confidence="confirmed",
                             discovered_by=DiscoveredBy.STATIC_SCAN,
                             evidence=meta.get("source", "ci/compose"), extra=meta)
        new = new.with_node(node)
        owners = [p for p in packages if kind in services_for_package(p.name)]
        for owner in owners:
            new = new.with_edge(Edge(src=owner.id, dst=node.id,
                                     relation=EdgeType.REQUIRES, origin="service"))
        if not owners and anchor is not None:
            new = new.with_edge(Edge(src=anchor.id, dst=node.id,
                                     relation=EdgeType.REQUIRES, origin="service"))

    # Inferred nodes (no requires edge; suppressed when CI is authoritative & absent it).
    for kind, meta in inferred.items():
        if kind in confirmed:
            continue
        if ci_present and kind not in ci:
            continue   # CI services: is authoritative — a guess it omits is dropped
        ev = (f"inferred from package {meta['inducing_package']}" if "inducing_package" in meta
              else f"inferred from {meta.get('bound_config', 'config')} URL")
        new = new.with_node(_service_node(kind, confidence="inferred",
                            discovered_by=DiscoveredBy.RESOLVER, evidence=ev, extra=meta))

    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_scan.py -v`
Expected: PASS (all service_scan tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py tests/depgraph/test_service_scan.py
git commit -m "feat(depgraph): scan_services orchestrator (confirmed/inferred nodes)"
```

---

### Task 8: Wire `scan_services` into the build pipeline

**Files:**
- Modify: `src/python_deps/depgraph/build.py`
- Test: `tests/depgraph/test_build.py`

**Interfaces:**
- Consumes: `scan_services` (Task 7).
- Produces: `build_dep_graph` graphs contain SERVICE nodes when the repo declares/implies a service; stamped with the resolver cycle.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_build.py` (use the `FakeExecutor` pattern the file already uses — `from conftest import FakeExecutor`, `_r`, the module helpers — same as the Config test):

```python
def test_build_includes_service_nodes(tmp_path):
    from conftest import FakeExecutor  # type: ignore
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "0"\n')
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "db.py").write_text("import psycopg2\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:14\n")

    from python_deps.depgraph.build import build_dep_graph
    from python_deps.depgraph.schema import NodeType
    ex = FakeExecutor(default=_r(returncode=1, stderr="x"))
    graph = build_dep_graph(str(tmp_path), ex, host_executor=ex, target_python="3.11")
    assert "postgres" in {n.name for n in graph.nodes if n.type is NodeType.SERVICE}
```

> If `psycopg2` is not discovered as a package by the fake-executor resolve, the confirmed CI postgres node still appears (CI discovery is independent of the package layer) — the assertion only requires the SERVICE node, which the CI `services:` block guarantees.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_build.py::test_build_includes_service_nodes -v`
Expected: FAIL — no SERVICE node (scan_services not wired).

- [ ] **Step 3: Write minimal implementation**

In `build.py`, add the import next to the `scan_config` import:

```python
from python_deps.depgraph.service_scan import scan_services
```

Insert the stage immediately after the existing `scan_config` call and before the `resolver_ids` computation:

```python
    graph = scan_config(repo_path, graph)
    # Stage 3d — Service tier (tier 5): confidence-annotated SERVICE nodes appended
    # to the same graph (design 2026-06-25-services-tier-design.md). Discovery only;
    # services are NOT certified here (certify skip-guard keeps them UNKNOWN).
    graph = scan_services(repo_path, graph)
    resolver_ids = {n.id for n in graph.nodes} - pre_resolve_ids
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_build.py -v`
Expected: PASS (new test + no regression).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_build.py
git commit -m "feat(depgraph): wire scan_services into the build pipeline (tier 5)"
```

---

### Task 9: Render the SERVICES advisory block

**Files:**
- Modify: `src/python_deps/depgraph/advise.py` (`render_dep_graph_advisory`)
- Test: `tests/depgraph/test_advise.py`

**Interfaces:**
- Consumes: `NodeType.SERVICE` nodes with `data["service_confidence"]`, `data.get("bound_config")`, `fix_candidates`.
- Produces: a `SERVICES (declared — reachability NOT certified here):` block listing each service with its confidence label, fix-candidate, `addresses:` (bound config), and a `(may be mocked — agent's call)` marker for inferred services.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_advise.py`:

```python
def _svc(name, confidence, **data):
    from python_deps.depgraph.schema import Node, NodeType, Layer, DiscoveredBy, State
    d = {"service_confidence": confidence}; d.update(data)
    return Node(id=f"service:{name}", type=NodeType.SERVICE, name=name, layer=Layer.SERVICES,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
                fix_candidates=(f"service:{name}:16",), data=d)


def test_advisory_renders_services_block():
    from python_deps.depgraph.schema import DepGraph
    from python_deps.depgraph.advise import render_dep_graph_advisory
    g = (DepGraph()
         .with_node(_svc("postgres", "confirmed", bound_config="DATABASE_URL"))
         .with_node(_svc("redis", "inferred", inducing_package="celery")))
    out = render_dep_graph_advisory(g)
    assert "SERVICES" in out
    assert "postgres" in out and "confirmed" in out
    assert "addresses: DATABASE_URL" in out
    assert "redis" in out and "may be mocked" in out


def test_advisory_no_services_block_when_none():
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.advise import render_dep_graph_advisory
    pkg = Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests", layer=Layer.PIP,
               discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED)
    out = render_dep_graph_advisory(DepGraph().with_node(pkg))
    assert "SERVICES" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_advise.py -k services -v`
Expected: FAIL — no SERVICES block in the render.

- [ ] **Step 3: Write minimal implementation**

In `advise.py`, in `render_dep_graph_advisory`, after the SATISFIED-summary block and before the `if len(lines) == 1` guard, add:

```python
    services = sorted(
        (n for n in graph.nodes if n.type is NodeType.SERVICE), key=lambda n: n.name
    )
    if services:
        lines.append("")
        lines.append("SERVICES (declared — reachability NOT certified here):")
        for n in services:
            conf = n.data.get("service_confidence", "inferred")
            fix = n.fix_candidates[0] if n.fix_candidates else "?"
            line = f"  {n.name:10} [{conf}]   fix: {fix}"
            bound = n.data.get("bound_config")
            if bound:
                line += f"   addresses: {bound}"
            if conf == "inferred":
                line += "   (may be mocked — agent's call)"
            lines.append(line)
```

(`NodeType` is already imported in `advise.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_advise.py -v`
Expected: PASS (new tests + no regression).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/advise.py tests/depgraph/test_advise.py
git commit -m "feat(depgraph): render SERVICES advisory block (confidence-labelled)"
```

---

### Task 10: Full-suite regression + off-state check

**Files:** the whole `tests/depgraph/` suite (no new production code).

- [ ] **Step 1: Run the full depgraph suite**

Run: `pytest tests/depgraph/ -q`
Expected: PASS — all prior tests (345 baseline) + the new service tests.

- [ ] **Step 2: Confirm Services are never certified / never started**

Run: `grep -rn "scan_services" src/ | grep -v "def scan_services" | grep -v service_scan.py`
Expected: exactly one hit — the `build.py` call site.
Run: `grep -rn "Layer.SERVICES" src/python_deps/depgraph/certify.py`
Expected: no output (SERVICES is intentionally absent from `_LAYER_ORDER`).

- [ ] **Step 3: Commit (only if lint/format changes were needed)**

```bash
git add -p
git commit -m "test(depgraph): green full suite for services advisory slice"
```

> If Steps 1–2 are clean with nothing to stage, skip — the slice is already committed task-by-task.

---

## Self-Review

**Spec coverage (against `2026-06-25-services-tier-design.md`):**
- §3 schema (`service_id`, `Node.data`/annotation not state, `Layer.SERVICES` not in `_LAYER_ORDER`, certify skip-guard, Config binding as metadata) → Tasks 1, 2, 7. ✅
- §4 edge model (`Package→Service` evidence-only; inferred = node-only) → Task 7. ✅
- §5 discovery (all 5 sources, ranking/suppression, confidence label) → Tasks 3–7 (failure-driven as a standalone primitive, Task 6, per §5's "post-hoc" note). ✅
- §6 advisory render (confidence-labelled SERVICES block, "may be mocked") → Task 9. ✅
- §9 phasing (discovery + advisory, in-module) → Tasks 8–9. ✅
- **Deferred (noted, not gaps):** emit/escalate/live-cert/runner-integration, `Service→Service`, in-image — design §8/§12.

**Placeholder scan:** none — every code/test step has literal code.

**Type consistency:** `service_id` → `service:<kind>` used identically across Tasks 1/7/9. `services_for_package -> list[str]` and `service_defaults -> (str,int)` consumed exactly so in Task 7. `service_from_url -> (kind,host,port)|None` consumed in Task 7. `scan_ci_services -> (dict, bool)` / `scan_compose_services -> dict` consumed in Task 7. `Node.data` (Task 1) read in Tasks 7/9. Confidence strings `"confirmed"`/`"inferred"` consistent across Tasks 7 and 9.

**Open question carried (spec §13):** inferred nodes intentionally carry no `requires` edge (honest-necessity); they render from the node list + `data` (Task 9).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-26-services-tier-advisory-slice.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh Sonnet subagent per task group, review between, final broad review (same as the Config slice).
2. **Inline Execution** — execute in this session via executing-plans, batched with checkpoints.

Which approach?
