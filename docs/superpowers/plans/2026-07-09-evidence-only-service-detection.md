# Evidence-Only Service Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace kind-tables and construction-time LLM recipe generation with an evidence-only service-detection pipeline that reads what the repo declared (compose + CI) and emits a typed `ServiceNode` carrying exactly one executable string — the readiness check.

**Architecture:** A five-stage pipeline — DISCOVER (pluggable source adapters) → SCOPE (relevance by reference-reachability, not paths) → FUSE (per-field ladders across sources) → CLASSIFY (app vs backing) → CERTIFY (check ladder sets state). All new modules are pure functions over parsed YAML: no Docker, no LLM, no network. The result is attached to the graph node as `data["service"]`, plus a **derived compat view** `data["setup"]` so the seven existing consumers keep working unchanged.

**Tech Stack:** Python 3.11+, `PyYAML`, `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-09-service-obligations-design.md`
**Validated prototype:** `.superpowers/sdd/service_schema_poc.py` (already run against all 50 python50 repos; findings in `.superpowers/sdd/service-schema-poc-findings.md`). Lift logic from it — it is correct and corpus-tested.

## Scope

**In scope:** detection + node construction + deleting the construction-time LLM.

**Out of scope (separate plan):** the react-arm `activate` step, probe verdicts as observations, `graph_context` population, and the cold-start obligation-comment renderer. This plan deliberately keeps `data["setup"]` as a compat view so no renderer or loop change is needed to land it.

## Global Constraints

- **No service-specific table anywhere** — not for install, not for start, not for the readiness probe. No `kind` field. Verbatim from spec §2.
- **Parsing ≠ mapping.** `postgres:16 → (repo="postgres", tag="16")` is allowed (lexical). `valkey → redis` is forbidden (semantic lookup).
- **The node contains exactly one executable string: `check.command`.** Everything else is declarative evidence. Spec §3.0.2.
- **Degrade the field, never the node.** A templated *field* is nulled and appended to `unresolved`; a templated *image name* drops the node. Spec §3.0.2 invariant 4.
- **Every derived field records its rung**: `port_source`, `check.source`, `relevance`.
- **The TCP check must be the Python one-liner** — `nc` is absent from slim images; Python is guaranteed present. Spec §3.1.
- Fixed enums (spec §3.0):
  - `PortSource = "ports" | "expose" | "env_dsn" | "sibling_dsn" | "none"`
  - `CheckSource = "declared_healthcheck" | "tcp_port" | "none"`
  - `Relevance = "ci_service" | "ci_referenced_compose" | "root_compose" | "unreferenced_compose"`
  - `State = "certifiable_obligation" | "declared_unverifiable"`
- **`check.source == "none"` ⇒ admit and surface, never drop.** (Today `classify_services_clean.py:115` silently drops probe-less services.)
- Python style: PEP 8, type annotations on all signatures, `from __future__ import annotations`, `@dataclass(frozen=True)` for value types, files < 400 lines.
- Unit tests must be **Docker-free and LLM-free** — pure functions over dicts/tmp_path.
- **SERVICE/CONFIG nodes stay excluded from the graph-hash.** `V3_INCLUDE_SERVICES` remains default-OFF. The byte-identical baseline must not change.
- **BRANCH HYGIENE (shared branch `john-v3-multi-lang`):** commit locally, never push/rebase/reset. `git add` **only the files your task names** — never `-A`, `.`, or `-u`. Never stage this pre-existing WIP: `.context/codex-session-id`, `src/python_deps/depgraph/{emit,resolve_link,resolve_lock,wheel_oracle}.py`, `tests/depgraph/test_{resolve,wheel_oracle,uninstallable_gate}.py`.
  - **Exception:** Task 9 legitimately modifies `emit.py`. Stage *only* the `_is_service_reciped` hunk; leave the other session's hunks unstaged (`git add -p`).

---

## File Structure

| File | Responsibility |
|---|---|
| **Create** `src/python_deps/depgraph/service_evidence.py` | Value types + fixed enums. No logic. |
| **Create** `src/python_deps/depgraph/service_parse.py` | Field parsers + the port and check ladders. Pure. |
| **Create** `src/python_deps/depgraph/service_sources.py` | `ServiceEvidenceSource` protocol; `ComposeSource`, `GithubActionsSource`. |
| **Create** `src/python_deps/depgraph/service_relevance.py` | Reachability-based scoping (which declaration is the *test* env). |
| **Create** `src/python_deps/depgraph/service_construct.py` | The pipeline: fuse → classify → certify → `build_service_nodes`. |
| **Modify** `src/envstate/classify_services_clean.py` | Build nodes from `build_service_nodes`; drop `translate_service`. |
| **Modify** `src/python_deps/depgraph/repoint.py:36` | `render_bind_steps` matches by declared hostname, not `kind`. |
| **Modify** `src/python_deps/depgraph/emit.py:150` | `_is_service_reciped` → state-based. |
| **Modify** `src/python_deps/depgraph/patch.py:11` | Add `data: dict \| None = None` to `NodeSpec` (it has no such field today). |
| **Modify** `src/python_deps/depgraph/patch_gate.py:118,233` | Allow an empty `setup['start']`; merge `NodeSpec.data` into `Node.data`. |
| **Delete** `src/envstate/service_translate.py` | Construction-time LLM recipes. |
| **Delete** `src/python_deps/depgraph/provisioning_spec.py` | Superseded by the sources + parse modules. |
| **Modify** `src/python_deps/depgraph/service_scan.py` | Remove `_kind_of` **only**. Keep `service_bind_url`, `service_from_url`, `scan_ci_services`, `scan_compose_services`, `classify_service_error` — used by `repoint`, `static_collect`, `oracle`, `runtime_classify`. |
| **Modify** `src/python_deps/depgraph/service_recipes.py` | Remove `KindBase`, `_KIND_BASE`, `render_setup` **only**. Keep `render_probe_poll` (used by `patch_gate.py:21`). |

**Verified facts this structure depends on** (do not re-derive):
- `render_setup` references in `build_script.py`, `emit.py`, `repoint.py` are **docstring mentions only**. `script.render_setup_sh` is an unrelated function.
- `data["setup"]` has **seven** consumers: `emit.py:150`, `build_script.py:377`, `populate.py:61`, `certify.py:87`, `schedule.py:40,104`, `patch_gate.py:237`, `advise.py:166`. The compat view (Task 9) keeps all of them working.
- `build_script._service_start_block` (line 368) already no-ops on an empty `start` (`if start:`), so a compat setup with `start=""` renders only the probe wait.
- `render_bind_steps` lives in `repoint.py:36` (not `service_recipes.py`) and reads `s.kind`.

---

## Task 1: Value types and fixed enums

**Files:**
- Create: `src/python_deps/depgraph/service_evidence.py`
- Test: `tests/depgraph/test_service_evidence.py`

**Interfaces:**
- Produces: `Port`, `Mount`, `Check`, `Source`, `ServiceNode` (all `@dataclass(frozen=True)`); constants `PORT_SOURCES`, `CHECK_SOURCES`, `RELEVANCES`, `STATES`.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_service_evidence.py
import dataclasses
import pytest

from python_deps.depgraph.service_evidence import (
    CHECK_SOURCES, PORT_SOURCES, RELEVANCES, STATES,
    Check, Mount, Port, ServiceNode, Source,
)


def test_enums_are_exactly_the_spec_values():
    assert PORT_SOURCES == ("ports", "expose", "env_dsn", "sibling_dsn", "none")
    assert CHECK_SOURCES == ("declared_healthcheck", "tcp_port", "none")
    assert RELEVANCES == ("ci_service", "ci_referenced_compose",
                          "root_compose", "unreferenced_compose")
    assert STATES == ("certifiable_obligation", "declared_unverifiable")


def test_service_node_is_frozen_and_carries_one_executable_string():
    node = ServiceNode(
        id="service:db", name="db", image="postgres:16",
        image_repo="postgres", image_tag="16",
        ports=(Port(container=5432, host=5432),), port=5432, port_source="ports",
        endpoint="localhost:5432", env={"POSTGRES_DB": "app"},
        command=None, entrypoint=None, volumes=(), seed=(),
        check=Check(command="pg_isready", source="declared_healthcheck"),
        depends_on=(), relevance="ci_service",
        provenance=(Source(file="ci.yml", locator="jobs.t.services.db", kind="ci"),),
        raw={"ci": {"image": "postgres:16"}},
        state="certifiable_obligation", unresolved=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.name = "other"          # type: ignore[misc]
    assert node.check.command == "pg_isready"


def test_check_defaults_are_none_shaped():
    c = Check(command=None, source="none")
    assert c.interval_s is None and c.retries is None and c.timeout_s is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_evidence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.service_evidence'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/service_evidence.py
"""Typed evidence for one declared backing service (spec §3).

Pure value types. No parsing, no I/O, no service-specific knowledge — there is
no ``kind`` field and no recipe table anywhere in this package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PORT_SOURCES = ("ports", "expose", "env_dsn", "sibling_dsn", "none")
CHECK_SOURCES = ("declared_healthcheck", "tcp_port", "none")
RELEVANCES = ("ci_service", "ci_referenced_compose", "root_compose", "unreferenced_compose")
STATES = ("certifiable_obligation", "declared_unverifiable")

PortSource = Literal["ports", "expose", "env_dsn", "sibling_dsn", "none"]
CheckSource = Literal["declared_healthcheck", "tcp_port", "none"]
Relevance = Literal["ci_service", "ci_referenced_compose", "root_compose", "unreferenced_compose"]
State = Literal["certifiable_obligation", "declared_unverifiable"]


@dataclass(frozen=True)
class Port:
    container: int
    host: int | None = None


@dataclass(frozen=True)
class Mount:
    host: str | None
    container: str | None


@dataclass(frozen=True)
class Source:
    file: str        # repo-relative path
    locator: str     # "services.db" | "jobs.<job>.services.<name>"
    kind: str        # "compose" | "ci"


@dataclass(frozen=True)
class Check:
    """The ONLY executable string in the whole node."""
    command: str | None
    source: CheckSource
    interval_s: str | None = None
    retries: str | None = None
    timeout_s: str | None = None


@dataclass(frozen=True)
class ServiceNode:
    id: str
    name: str                      # declaration key; ALSO its declared hostname
    image: str                     # verbatim; may contain templates
    image_repo: str                # lexical parse (registry/org/name)
    image_tag: str | None

    ports: tuple[Port, ...]
    port: int | None
    port_source: PortSource
    endpoint: str | None

    env: dict[str, str]
    command: str | None
    entrypoint: str | None
    volumes: tuple[Mount, ...]
    seed: tuple[Mount, ...]

    check: Check
    depends_on: tuple[str, ...]

    relevance: Relevance
    provenance: tuple[Source, ...]
    raw: dict[str, dict]

    state: State
    unresolved: tuple[str, ...] = field(default_factory=tuple)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_evidence.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_evidence.py tests/depgraph/test_service_evidence.py
git commit -m "feat(depgraph): typed service evidence value types + fixed enums"
```

---

## Task 2: Image parse and field parsers

**Files:**
- Create: `src/python_deps/depgraph/service_parse.py`
- Test: `tests/depgraph/test_service_parse.py`

**Interfaces:**
- Consumes: `Port`, `Mount` from Task 1.
- Produces: `parse_image(image) -> tuple[str, str | None]`; `parse_ports(entry) -> tuple[Port, ...]`; `parse_expose(entry) -> tuple[int, ...]`; `parse_env(entry) -> dict[str, str]`; `parse_command(entry) -> str | None`; `parse_entrypoint(entry) -> str | None`; `parse_volumes(entry) -> tuple[Mount, ...]`; `parse_depends_on(entry) -> tuple[str, ...]`; `seed_mounts(volumes) -> tuple[Mount, ...]`; `is_templated(s) -> bool`.

**Why this matters:** the PoC's first version deleted rq's entire service because its *tag* was `${{ matrix.valkey-version }}`. The invariant is: a templated **tag** nulls the tag; only a templated **image name** drops the node.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_service_parse.py
from python_deps.depgraph.service_evidence import Mount, Port
from python_deps.depgraph.service_parse import (
    is_templated, parse_command, parse_depends_on, parse_entrypoint, parse_env,
    parse_expose, parse_image, parse_ports, parse_volumes, seed_mounts,
)


def test_parse_image_lexical_only():
    assert parse_image("postgres:16") == ("postgres", "16")
    assert parse_image("ghcr.io/o/i:v1") == ("ghcr.io/o/i", "v1")
    assert parse_image("redis") == ("redis", None)
    assert parse_image("img@sha256:abc") == ("img", None)      # digest dropped


def test_templated_TAG_keeps_the_repo_and_nulls_the_tag():
    # rq/rq: the service must survive; only the tag is unknown.
    assert parse_image("valkey/valkey:${{ matrix.valkey-version }}") == ("valkey/valkey", None)


def test_templated_IMAGE_NAME_drops_the_node():
    # PostHog: "$REGISTRY_URL:$POSTHOG_APP_TAG" — nothing usable.
    assert parse_image("$REGISTRY_URL:$POSTHOG_APP_TAG") == ("", None)


def test_is_templated_catches_bare_dollar_var():
    assert is_templated("${{ matrix.x }}") and is_templated("$REGISTRY_URL")
    assert not is_templated("postgres")


def test_parse_ports_handles_ranges_and_long_syntax_and_templates():
    assert parse_ports({"ports": ["6379:6379"]}) == (Port(6379, 6379),)
    assert parse_ports({"ports": ["127.0.0.1:5432:5432"]}) == (Port(5432, 5432),)
    assert parse_ports({"ports": ["5432"]}) == (Port(5432, None),)
    assert parse_ports({"ports": ["8080:80/tcp"]}) == (Port(80, 8080),)
    # a published RANGE must not raise (real: baserow)
    assert parse_ports({"ports": [{"target": 80, "published": "5000-5999"}]}) == (Port(80, None),)
    # templated host port: keep the container port
    assert parse_ports({"ports": ["${PORT}:5432"]}) == (Port(5432, None),)
    assert parse_ports({"ports": "not-a-list"}) == ()


def test_parse_env_accepts_dict_list_and_ci_env_key():
    assert parse_env({"environment": {"A": "1"}}) == {"A": "1"}
    assert parse_env({"environment": ["A=1", "B=2"]}) == {"A": "1", "B": "2"}
    assert parse_env({"env": {"A": "1"}}) == {"A": "1"}      # GH Actions uses `env:`
    assert parse_env({}) == {}


def test_parse_command_and_entrypoint_join_lists():
    assert parse_command({"command": ["postgres", "-c", "x=1"]}) == "postgres -c x=1"
    assert parse_command({"command": "redis-server --appendonly yes"}) == "redis-server --appendonly yes"
    assert parse_command({}) is None
    assert parse_entrypoint({"entrypoint": ["/bin/sh", "-c"]}) == "/bin/sh -c"


def test_parse_volumes_and_seed_subset():
    vols = parse_volumes({"volumes": [
        "./init.sql:/docker-entrypoint-initdb.d/init.sql",
        "data:/var/lib/postgresql/data",
        {"source": "./x", "target": "/initdb/x"},
    ]})
    assert vols[0] == Mount("./init.sql", "/docker-entrypoint-initdb.d/init.sql")
    assert seed_mounts(vols) == (vols[0], vols[2])           # initdb.d + /initdb only
    assert parse_volumes({"volumes": "nope"}) == ()


def test_parse_depends_on_list_and_mapping():
    assert parse_depends_on({"depends_on": ["a", "b"]}) == ("a", "b")
    assert parse_depends_on({"depends_on": {"a": {"condition": "x"}}}) == ("a",)
    assert parse_depends_on({}) == ()


def test_parse_expose():
    assert parse_expose({"expose": [5432, "6379/tcp"]}) == (5432, 6379)
    assert parse_expose({}) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.service_parse'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/service_parse.py
"""Field parsers and derivation ladders for declared services (spec §3.1, §3.2).

Pure functions over an already-parsed YAML mapping. Every function obeys the
invariant: **degrade the field, never the node**. No service-specific knowledge.
"""
from __future__ import annotations

from python_deps.depgraph.service_evidence import Mount, Port

_SEED_MARKERS = ("docker-entrypoint-initdb.d", "/initdb")


def is_templated(s: str) -> bool:
    """`${VAR}`, `$(cmd)`, `${{ gha }}`, and bare `$VAR` are all unresolved."""
    return "$" in s


def _int_or_none(v: object) -> int | None:
    """Ports in the wild: 5432, '5432', '5000-5999' (range), '${PORT}'."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def parse_image(image: str) -> tuple[str, str | None]:
    """`postgres:16` -> ("postgres", "16"). Lexical split only, never a lookup.

    A templated TAG keeps the repo and nulls the tag (rq's
    `valkey/valkey:${{ matrix.valkey-version }}`). A templated image NAME yields
    ("", None) so the caller drops the node — there is no usable evidence.
    """
    if not image:
        return "", None
    img = image.split("@", 1)[0]                       # drop digest
    head, _, last = img.rpartition("/")
    name, sep, tag = last.partition(":")
    if is_templated(name):
        return "", None
    repo = f"{head}/{name}" if head else name
    if not sep or is_templated(tag):
        return repo, None
    return repo, tag


def parse_ports(entry: dict) -> tuple[Port, ...]:
    raw = entry.get("ports")
    if not isinstance(raw, list):
        return ()
    out: list[Port] = []
    for p in raw:
        if isinstance(p, dict):                        # long syntax
            tgt = _int_or_none(p.get("target"))
            if tgt:
                out.append(Port(container=tgt, host=_int_or_none(p.get("published"))))
            continue
        parts = str(p).split("/")[0].split(":")        # strip /tcp
        if len(parts) == 1:
            c = _int_or_none(parts[0])
            if c:
                out.append(Port(container=c, host=None))
        else:
            c = _int_or_none(parts[-1])
            if c:                                      # "${PORT}:5432" -> host unknown
                out.append(Port(container=c, host=_int_or_none(parts[-2])))
    return tuple(out)


def parse_expose(entry: dict) -> tuple[int, ...]:
    raw = entry.get("expose")
    if not isinstance(raw, list):
        return ()
    return tuple(p for p in (_int_or_none(str(e).split("/")[0]) for e in raw) if p)


def parse_env(entry: dict) -> dict[str, str]:
    env = entry.get("environment")
    if env is None:
        env = entry.get("env")                         # GH Actions services use `env:`
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    out: dict[str, str] = {}
    if isinstance(env, list):
        for item in env:
            k, _, v = str(item).partition("=")
            out[k.strip()] = v.strip()
    return out


def _join(v: object) -> str | None:
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v) if v else None


def parse_command(entry: dict) -> str | None:
    return _join(entry.get("command"))


def parse_entrypoint(entry: dict) -> str | None:
    return _join(entry.get("entrypoint"))


def parse_volumes(entry: dict) -> tuple[Mount, ...]:
    raw = entry.get("volumes")
    if not isinstance(raw, list):
        return ()
    out: list[Mount] = []
    for v in raw:
        if isinstance(v, dict):
            out.append(Mount(host=v.get("source"), container=v.get("target")))
        else:
            parts = str(v).split(":")
            if len(parts) >= 2:
                out.append(Mount(host=parts[0], container=parts[1]))
    return tuple(out)


def seed_mounts(volumes: tuple[Mount, ...]) -> tuple[Mount, ...]:
    """Mounts that seed schema — they need a RUNNING daemon, so they belong to
    ACTIVATE, never PROVISION (spec §4.2)."""
    return tuple(m for m in volumes
                 if m.container and any(k in m.container for k in _SEED_MARKERS))


def parse_depends_on(entry: dict) -> tuple[str, ...]:
    d = entry.get("depends_on")
    if isinstance(d, dict):
        return tuple(str(k) for k in d)
    return tuple(str(x) for x in d) if isinstance(d, list) else ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_parse.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_parse.py tests/depgraph/test_service_parse.py
git commit -m "feat(depgraph): evidence field parsers; templated tag degrades field not node"
```

---

## Task 3: The port ladder (including sibling-DSN rescue)

**Files:**
- Modify: `src/python_deps/depgraph/service_parse.py` (append)
- Test: `tests/depgraph/test_service_parse.py` (append)

**Interfaces:**
- Consumes: `Port` (Task 1), `parse_ports`/`parse_expose` (Task 2).
- Produces: `derive_port(ports, expose, env, name, sibling_env_blob) -> tuple[int | None, PortSource]`.

**Why:** `Spoolman/db` (`postgres:11-alpine`) declares no `ports:` and no healthcheck — but the app declares `DATABASE_URL=...@db:5432/...`. The port is evidence; it just lives in a *sibling*. This rung rescued 9 services in the PoC.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/depgraph/test_service_parse.py
from python_deps.depgraph.service_evidence import Port
from python_deps.depgraph.service_parse import derive_port


def test_port_ladder_prefers_declared_ports():
    got = derive_port((Port(5432, 5432),), (6379,), {"URL": "x://h:1234"}, "db", "")
    assert got == (5432, "ports")


def test_port_ladder_falls_back_to_expose():
    assert derive_port((), (6379,), {}, "cache", "") == (6379, "expose")


def test_port_ladder_falls_back_to_own_env_dsn():
    env = {"DATABASE_URL": "postgres://u:p@db:5432/app"}
    assert derive_port((), (), env, "db", "") == (5432, "env_dsn")


def test_port_ladder_rescues_from_sibling_dsn():
    # `db` declares nothing; the APP declares the DSN naming `db:5432`.
    blob = "postgres://u:p@db:5432/app redis://cache:6379/0"
    assert derive_port((), (), {}, "db", blob) == (5432, "sibling_dsn")
    assert derive_port((), (), {}, "cache", blob) == (6379, "sibling_dsn")


def test_sibling_rescue_requires_a_name_boundary():
    # must not match "mydb:5432" when looking for service "db"
    assert derive_port((), (), {}, "db", "postgres://u@mydb:5432/x") == (None, "none")


def test_port_ladder_gives_up_cleanly():
    assert derive_port((), (), {}, "svc", "") == (None, "none")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_parse.py -k port_ladder -v`
Expected: FAIL — `ImportError: cannot import name 'derive_port'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/python_deps/depgraph/service_parse.py
import re

from python_deps.depgraph.service_evidence import PortSource

_DSN_PORT = re.compile(r"://[^/\s]*?:(\d{2,5})")


def _rescue_from_siblings(name: str, sibling_env_blob: str) -> int | None:
    """`db` declares no ports, but the app declares `DATABASE_URL=...@db:5432/x`.
    The port is still evidence — it just lives in a sibling service's env.

    The `\\b` boundaries stop "db" from matching inside "mydb:5432".
    """
    m = re.search(rf"\b{re.escape(name)}:(\d{{2,5}})\b", sibling_env_blob)
    return int(m.group(1)) if m else None


def derive_port(ports: tuple[Port, ...], expose: tuple[int, ...],
                env: dict[str, str], name: str,
                sibling_env_blob: str) -> tuple[int | None, PortSource]:
    """ports: -> expose: -> own-env DSN -> sibling-env DSN -> unknown. Evidence-only."""
    for p in ports:
        if p.container:
            return p.container, "ports"
    if expose:
        return expose[0], "expose"
    for v in env.values():
        m = _DSN_PORT.search(v)
        if m:
            return int(m.group(1)), "env_dsn"
    rescued = _rescue_from_siblings(name, sibling_env_blob)
    if rescued:
        return rescued, "sibling_dsn"
    return None, "none"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_parse.py -v`
Expected: PASS (15 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_parse.py tests/depgraph/test_service_parse.py
git commit -m "feat(depgraph): port ladder with sibling-DSN rescue"
```

---

## Task 4: The check ladder (declared healthcheck → TCP → none)

**Files:**
- Modify: `src/python_deps/depgraph/service_parse.py` (append)
- Test: `tests/depgraph/test_service_parse.py` (append)

**Interfaces:**
- Consumes: `Check` (Task 1).
- Produces: `compose_healthcheck(entry) -> tuple[str | None, dict]`; `ci_healthcheck(entry) -> tuple[str | None, dict]`; `tcp_check(port) -> str`; `derive_check(hc_cmd, timing, port) -> Check`.

**Why the Python one-liner:** `nc` is absent from slim images; `bash </dev/tcp/...` needs bash. Python is guaranteed present in a Python repo's environment.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/depgraph/test_service_parse.py
from python_deps.depgraph.service_parse import (
    ci_healthcheck, compose_healthcheck, derive_check, tcp_check,
)


def test_compose_healthcheck_strips_CMD_and_CMD_SHELL():
    assert compose_healthcheck({"healthcheck": {"test": ["CMD", "pg_isready", "-U", "u"]}})[0] \
        == "pg_isready -U u"
    assert compose_healthcheck({"healthcheck": {"test": ["CMD-SHELL", "redis-cli ping"]}})[0] \
        == "redis-cli ping"
    assert compose_healthcheck({"healthcheck": {"test": "curl -f http://x"}})[0] == "curl -f http://x"


def test_compose_healthcheck_NONE_disables_and_timing_is_captured():
    assert compose_healthcheck({"healthcheck": {"test": ["NONE"]}}) == (None, {})
    _cmd, timing = compose_healthcheck(
        {"healthcheck": {"test": ["CMD", "x"], "interval": "10s", "retries": 5}})
    assert timing == {"interval": "10s", "retries": 5}


def test_ci_healthcheck_parses_health_cmd_from_options():
    # rq/rq's real workflow, folded-block `options:`
    entry = {"options": '--health-cmd "valkey-cli ping" --health-interval 10s '
                        '--health-timeout 5s --health-retries 5'}
    cmd, timing = ci_healthcheck(entry)
    assert cmd == "valkey-cli ping"
    assert timing == {"interval": "10s", "timeout": "5s", "retries": "5"}


def test_ci_healthcheck_absent_options():
    assert ci_healthcheck({"image": "redis"}) == (None, {})


def test_tcp_check_is_the_portable_python_one_liner():
    cmd = tcp_check(5432)
    assert cmd.startswith("python -c")
    assert "socket.create_connection" in cmd and "5432" in cmd
    assert "nc " not in cmd and "/dev/tcp" not in cmd


def test_check_ladder_precedence():
    declared = derive_check("pg_isready", {"interval": "10s"}, 5432)
    assert declared.source == "declared_healthcheck" and declared.command == "pg_isready"
    assert declared.interval_s == "10s"

    derived = derive_check(None, {}, 6379)
    assert derived.source == "tcp_port" and "6379" in derived.command

    nothing = derive_check(None, {}, None)
    assert nothing.source == "none" and nothing.command is None


def test_a_non_read_only_healthcheck_falls_through_to_tcp():
    """The check runs inside certification, so it must not mutate. `curl -f ...`
    fails patch_gate.is_read_only -> fall down the ladder, do NOT drop the node.
    Real: PostHog elasticsearch, mlflow storage, gitingest minio (11/54 corpus)."""
    c = derive_check("curl -f http://localhost:9200/_cluster/health", {}, 9200)
    assert c.source == "tcp_port" and "9200" in c.command


def test_a_non_read_only_healthcheck_with_no_port_becomes_none():
    c = derive_check("wget -q --spider http://localhost:8123/ping", {}, None)
    assert c.source == "none" and c.command is None


def test_the_tcp_check_itself_is_read_only():
    from python_deps.depgraph.patch_gate import is_read_only
    assert is_read_only(tcp_check(5432))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_parse.py -k check -v`
Expected: FAIL — `ImportError: cannot import name 'derive_check'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/python_deps/depgraph/service_parse.py
import shlex

from python_deps.depgraph.service_evidence import Check

TCP_CHECK = ("python -c \"import socket; "
             "socket.create_connection(('127.0.0.1', {port}), 1).close()\"")


def tcp_check(port: int) -> str:
    """Universal, service-agnostic liveness check derived from the declared port.

    Python, not `nc` (absent from slim images) and not `bash </dev/tcp/...`.
    """
    return TCP_CHECK.format(port=port)


def compose_healthcheck(entry: dict) -> tuple[str | None, dict]:
    hc = entry.get("healthcheck")
    if not isinstance(hc, dict):
        return None, {}
    test = hc.get("test")
    cmd: str | None = None
    if isinstance(test, list):
        parts = [str(x) for x in test]
        if parts and parts[0] == "NONE":
            return None, {}
        if parts and parts[0] in ("CMD", "CMD-SHELL"):
            parts = parts[1:]
        cmd = " ".join(parts) or None
    elif isinstance(test, str):
        cmd = test or None
    timing = {k: hc.get(k) for k in ("interval", "timeout", "retries") if hc.get(k)}
    return cmd, timing


def ci_healthcheck(entry: dict) -> tuple[str | None, dict]:
    """GH Actions: `options: --health-cmd "pg_isready" --health-interval 10s ...`"""
    opts = entry.get("options")
    if not isinstance(opts, str):
        return None, {}
    try:
        toks = shlex.split(opts)
    except ValueError:
        return None, {}
    cmd: str | None = None
    timing: dict = {}
    keys = {"--health-interval": "interval", "--health-timeout": "timeout",
            "--health-retries": "retries"}
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if not nxt:
            continue
        if t == "--health-cmd":
            cmd = nxt
        elif t in keys:
            timing[keys[t]] = nxt
    return cmd, timing


def derive_check(hc_cmd: str | None, timing: dict, port: int | None) -> Check:
    """declared healthcheck -> TCP on the declared port -> none. No table.

    Every rung must pass ``is_read_only``: the check runs inside certification and
    must never mutate the container. A `curl`/`wget` healthcheck fails that gate and
    falls THROUGH to the TCP rung — it never disqualifies the service.
    """
    from python_deps.depgraph.patch_gate import is_read_only   # local: avoids a cycle

    if hc_cmd and is_read_only(hc_cmd):
        return Check(command=hc_cmd, source="declared_healthcheck",
                     interval_s=timing.get("interval"), retries=timing.get("retries"),
                     timeout_s=timing.get("timeout"))
    if port:
        return Check(command=tcp_check(port), source="tcp_port")
    return Check(command=None, source="none")
```

> **Verified:** `is_read_only(tcp_check(5432))` is `True`; `pg_isready`, `redis-cli ping`,
> `valkey-cli ping`, `mysqladmin ping --silent` all pass. `curl -f …` and `wget --spider …` fail.
> On the corpus this moves 9 nodes from `declared_healthcheck` to `tcp_port` and 2 to `none`
> (certifiable 75% → 73%).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_parse.py -v`
Expected: PASS (21 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_parse.py tests/depgraph/test_service_parse.py
git commit -m "feat(depgraph): check ladder — declared healthcheck, TCP fallback, none"
```

---

## Task 5: Source adapters (DISCOVER)

**Files:**
- Create: `src/python_deps/depgraph/service_sources.py`
- Test: `tests/depgraph/test_service_sources.py`

**Interfaces:**
- Produces: `RawDeclaration` (frozen dataclass: `name: str`, `entry: dict`, `file: str`, `locator: str`, `kind: str`, `doc_env_blob: str`); `ServiceEvidenceSource` Protocol with `discover(repo: str) -> Iterator[RawDeclaration]`; `ComposeSource`, `GithubActionsSource`; `DEFAULT_SOURCES: tuple[ServiceEvidenceSource, ...]`; `discover_all(repo, sources=DEFAULT_SOURCES) -> list[RawDeclaration]`.

`doc_env_blob` is the concatenation of *all* env values in the same compose document — the input to the sibling-DSN rung. CI declarations carry `""`.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_service_sources.py
import textwrap

from python_deps.depgraph.service_sources import (
    ComposeSource, GithubActionsSource, discover_all,
)


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))
    return p


def test_compose_source_yields_each_service_with_locator_and_blob(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            image: myapp:1
            environment:
              DATABASE_URL: postgres://u:p@db:5432/app
          db:
            image: postgres:16
    """)
    decls = list(ComposeSource().discover(str(tmp_path)))
    assert {d.name for d in decls} == {"web", "db"}
    db = next(d for d in decls if d.name == "db")
    assert db.kind == "compose"
    assert db.locator == "services.db"
    assert db.file == "docker-compose.yml"
    assert "db:5432" in db.doc_env_blob            # sibling evidence is carried along


def test_ci_source_reads_jobs_services_and_requires_an_image(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          test:
            services:
              valkey:
                image: valkey/valkey:8
              disabled: "not-a-mapping"
              imageless:
                ports: ["1:1"]
    """)
    decls = list(GithubActionsSource().discover(str(tmp_path)))
    assert [d.name for d in decls] == ["valkey"]
    assert decls[0].kind == "ci"
    assert decls[0].locator == "jobs.test.services.valkey"
    assert decls[0].doc_env_blob == ""


def test_sources_never_raise_on_malformed_yaml(tmp_path):
    _write(tmp_path, "docker-compose.yml", "services: [redis: image: redis:7\n")
    _write(tmp_path, ".github/workflows/x.yml", "jobs:\n  t:\n    services: 'nope'\n")
    assert discover_all(str(tmp_path)) == []


def test_discover_all_returns_compose_then_ci(tmp_path):
    _write(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres:16\n")
    _write(tmp_path, ".github/workflows/ci.yml",
           "jobs:\n  t:\n    services:\n      redis:\n        image: redis:7\n")
    kinds = [d.kind for d in discover_all(str(tmp_path))]
    assert kinds == ["compose", "ci"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.service_sources'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/service_sources.py
"""DISCOVER: pluggable evidence sources (spec §10.1).

Each source yields RawDeclarations. Nothing downstream knows which source a
declaration came from, so adding one (GitLab CI, k8s, devcontainer) never
touches the schema or its consumers.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import yaml

from python_deps.depgraph.service_parse import parse_env


@dataclass(frozen=True)
class RawDeclaration:
    name: str
    entry: dict
    file: str            # repo-relative
    locator: str
    kind: str            # "compose" | "ci"
    doc_env_blob: str    # all env values in the same document (sibling-DSN input)


class ServiceEvidenceSource(Protocol):
    def discover(self, repo: str) -> Iterator[RawDeclaration]: ...


def _load(path: str) -> object | None:
    try:
        with open(path, errors="replace") as fh:
            return yaml.safe_load(fh)
    except Exception:                      # noqa: BLE001 - a bad file skips itself
        return None


def _walk(repo: str) -> Iterator[tuple[str, str]]:
    for root, _dirs, files in os.walk(repo):
        for fn in files:
            path = os.path.join(root, fn)
            yield path, os.path.relpath(path, repo)


class ComposeSource:
    def discover(self, repo: str) -> Iterator[RawDeclaration]:
        for path, rel in _walk(repo):
            low = os.path.basename(path).lower()
            if "compose" not in low or not low.endswith((".yml", ".yaml")):
                continue
            doc = _load(path)
            svcs = doc.get("services") if isinstance(doc, dict) else None
            if not isinstance(svcs, dict):
                continue
            blob = " ".join(v for e in svcs.values() if isinstance(e, dict)
                            for v in parse_env(e).values())
            for name, entry in svcs.items():
                if isinstance(entry, dict):
                    yield RawDeclaration(str(name), entry, rel, f"services.{name}",
                                         "compose", blob)


class GithubActionsSource:
    def discover(self, repo: str) -> Iterator[RawDeclaration]:
        wf = os.path.join(repo, ".github", "workflows")
        if not os.path.isdir(wf):
            return
        for fname in sorted(os.listdir(wf)):
            if not fname.lower().endswith((".yml", ".yaml")):
                continue
            path = os.path.join(wf, fname)
            doc = _load(path)
            jobs = doc.get("jobs") if isinstance(doc, dict) else None
            if not isinstance(jobs, dict):
                continue
            rel = os.path.relpath(path, repo)
            for job, jb in jobs.items():
                if not isinstance(jb, dict):
                    continue
                svcs = jb.get("services")
                if not isinstance(svcs, dict):
                    continue
                for name, entry in svcs.items():
                    # A real GH-Actions service container ALWAYS declares `image:`.
                    if isinstance(entry, dict) and entry.get("image"):
                        yield RawDeclaration(str(name), entry, rel,
                                             f"jobs.{job}.services.{name}", "ci", "")


DEFAULT_SOURCES: tuple[ServiceEvidenceSource, ...] = (ComposeSource(), GithubActionsSource())


def discover_all(repo: str,
                 sources: tuple[ServiceEvidenceSource, ...] = DEFAULT_SOURCES,
                 ) -> list[RawDeclaration]:
    return [d for s in sources for d in s.discover(repo)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_sources.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_sources.py tests/depgraph/test_service_sources.py
git commit -m "feat(depgraph): pluggable service evidence sources (compose + GH Actions)"
```

---

## Task 6: Relevance by reachability (SCOPE)

**Files:**
- Create: `src/python_deps/depgraph/service_relevance.py`
- Test: `tests/depgraph/test_service_relevance.py`

**Interfaces:**
- Consumes: `RawDeclaration` (Task 5).
- Produces: `ci_referenced_compose_files(repo) -> frozenset[str]`; `compute_relevance(decl, ci_refs) -> Relevance`.

**Why:** the PoC proved path filtering is unsound *in both directions* — `mlflow/tests/db/compose.yml` **is** the test environment, while `testcontainers/tests/core/compose_fixtures/*` is the library's own fixture data. No filename rule separates them. But CI **names** the compose file it brings up.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_service_relevance.py
import textwrap

from python_deps.depgraph.service_relevance import (
    ci_referenced_compose_files, compute_relevance,
)
from python_deps.depgraph.service_sources import RawDeclaration


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def _decl(file, kind="compose"):
    return RawDeclaration("db", {"image": "postgres:16"}, file, "services.db", kind, "")


def test_ci_referenced_compose_files_finds_both_spellings(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f tests/db/compose.yml up -d
              - run: docker-compose --file docker-compose.dev.yml up
    """)
    refs = ci_referenced_compose_files(str(tmp_path))
    assert refs == frozenset({"tests/db/compose.yml", "docker-compose.dev.yml"})


def test_ci_service_declarations_are_intrinsically_relevant(tmp_path):
    d = _decl(".github/workflows/ci.yml", kind="ci")
    assert compute_relevance(d, frozenset()) == "ci_service"


def test_a_tests_dir_compose_named_by_CI_is_the_test_environment(tmp_path):
    # mlflow/tests/db/compose.yml — a path heuristic would WRONGLY drop this.
    d = _decl("tests/db/compose.yml")
    assert compute_relevance(d, frozenset({"tests/db/compose.yml"})) == "ci_referenced_compose"


def test_root_compose_unreferenced_is_ambiguous():
    assert compute_relevance(_decl("docker-compose.yml"), frozenset()) == "root_compose"
    assert compute_relevance(_decl("compose.yaml"), frozenset()) == "root_compose"


def test_nested_unreferenced_compose_is_lowest_confidence():
    # testcontainers/tests/core/compose_fixtures/basic/docker-compose.yaml
    d = _decl("tests/core/compose_fixtures/basic/docker-compose.yaml")
    assert compute_relevance(d, frozenset()) == "unreferenced_compose"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_relevance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.service_relevance'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/service_relevance.py
"""SCOPE: which declaration describes the TEST environment (spec §10.2).

Relevance is reachability, not a path pattern: a declaration is test-relevant
if something that runs the tests references it. Path filtering is unsound in
both directions -- `tests/db/compose.yml` IS an environment; a library's
`compose_fixtures/` is not.
"""
from __future__ import annotations

import os
import re

from python_deps.depgraph.service_evidence import Relevance
from python_deps.depgraph.service_sources import RawDeclaration

# `docker compose -f X`, `docker-compose --file X`
_COMPOSE_REF = re.compile(r"docker[-\s]compose[^\n;|&]*?(?:-f|--file)\s+(\S+)")

_ROOT_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def ci_referenced_compose_files(repo: str) -> frozenset[str]:
    """Compose paths that a workflow explicitly brings up. This is the edge from
    'the thing that runs the tests' to 'the environment it needs'."""
    wf = os.path.join(repo, ".github", "workflows")
    if not os.path.isdir(wf):
        return frozenset()
    found: set[str] = set()
    for fname in sorted(os.listdir(wf)):
        if not fname.lower().endswith((".yml", ".yaml")):
            continue
        try:
            with open(os.path.join(wf, fname), errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in _COMPOSE_REF.finditer(text):
            found.add(m.group(1).strip("'\"").lstrip("./"))
    return frozenset(found)


def compute_relevance(decl: RawDeclaration, ci_refs: frozenset[str]) -> Relevance:
    if decl.kind == "ci":
        return "ci_service"                       # the job IS the test
    norm = decl.file.replace(os.sep, "/").lstrip("./")
    if norm in ci_refs:
        return "ci_referenced_compose"
    if "/" not in norm and norm.lower() in _ROOT_NAMES:
        return "root_compose"
    return "unreferenced_compose"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_relevance.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_relevance.py tests/depgraph/test_service_relevance.py
git commit -m "feat(depgraph): relevance by CI reference-reachability, not path heuristics"
```

---

## Task 7: The pipeline — FUSE, CLASSIFY, CERTIFY

**Files:**
- Create: `src/python_deps/depgraph/service_construct.py`
- Test: `tests/depgraph/test_service_construct.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: `build_service_nodes(repo: str, owner: str = "") -> list[ServiceNode]`.

**Key behaviours:**
- **FUSE, not dedup.** A service named in *both* compose and CI merges field-by-field (today's `iter_provisioning_specs` takes compose-first and throws the CI healthcheck away).
- **CLASSIFY** app vs backing: `build:` → app; image named after the repo → app; first-party image with no port *and* no healthcheck → app. Prefer the structural signal: anything with `depends_on` in-degree > 0 is backing.
- **CERTIFY**: `check.source != "none"` ⇒ `certifiable_obligation`, else `declared_unverifiable` (admitted, surfaced, never enforced).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_service_construct.py
import textwrap

from python_deps.depgraph.service_construct import build_service_nodes


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_backing_service_is_certifiable_and_records_its_rungs(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:16
            ports: ["5432:5432"]
            environment: {POSTGRES_DB: app}
            command: postgres -c max_connections=200
            volumes: ["./init.sql:/docker-entrypoint-initdb.d/init.sql"]
            healthcheck:
              test: ["CMD", "pg_isready", "-U", "u"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.id == "service:db" and node.image_repo == "postgres" and node.image_tag == "16"
    assert node.port == 5432 and node.port_source == "ports"
    assert node.endpoint == "localhost:5432"
    assert node.check.source == "declared_healthcheck" and node.check.command == "pg_isready -U u"
    assert node.state == "certifiable_obligation"
    assert node.command == "postgres -c max_connections=200"
    assert len(node.seed) == 1
    assert node.relevance == "root_compose"


def test_app_services_are_excluded(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            build: .
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    assert [n.name for n in build_service_nodes(str(tmp_path))] == ["db"]


def test_first_party_image_without_port_or_healthcheck_is_the_app(tmp_path):
    # OpenCTI: `opencti/connector-x` in owner OpenCTI-Platform, no ports, no healthcheck.
    _write(tmp_path, "docker-compose.yml", """
        services:
          connector:
            image: opencti/connector-rst:1.0
            environment: {OPENCTI_URL: http://external:8080}
          minio:
            image: minio/minio:latest
            ports: ["9000:9000"]
    """)
    names = [n.name for n in build_service_nodes(str(tmp_path), owner="OpenCTI-Platform")]
    assert names == ["minio"]


def test_first_party_image_WITH_a_port_stays_a_backing_service(tmp_path):
    # supabase/postgres in owner supabase — first-party, but exposes a port.
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: supabase/postgres:15.1
            ports: ["5432:5432"]
    """)
    assert [n.name for n in build_service_nodes(str(tmp_path), owner="supabase")] == ["db"]


def test_fuse_merges_compose_and_ci_evidence_for_the_same_name(tmp_path):
    # compose has volumes but no healthcheck; CI has the --health-cmd. Keep BOTH.
    _write(tmp_path, "docker-compose.yml", """
        services:
          redis:
            image: redis:7
            volumes: ["./r.conf:/etc/redis.conf"]
    """)
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            services:
              redis:
                image: redis:7
                ports: ["6379:6379"]
                options: --health-cmd "redis-cli ping" --health-retries 5
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.check.source == "declared_healthcheck"     # from CI
    assert node.check.command == "redis-cli ping"
    assert len(node.volumes) == 1                          # from compose
    assert node.port == 6379
    assert {p.kind for p in node.provenance} == {"compose", "ci"}
    assert set(node.raw) == {"compose", "ci"}


def test_no_healthcheck_but_a_port_yields_a_tcp_check(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           "services:\n  cache:\n    image: redis:7\n    ports: ['6379:6379']\n")
    (node,) = build_service_nodes(str(tmp_path))
    assert node.check.source == "tcp_port" and node.state == "certifiable_obligation"


def test_no_healthcheck_and_no_port_is_admitted_but_unverifiable(tmp_path):
    # Spoolman-style: real backing service, no evidence for a check.
    _write(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres:11-alpine\n")
    (node,) = build_service_nodes(str(tmp_path))
    assert node.check.source == "none"
    assert node.state == "declared_unverifiable"           # surfaced, NOT dropped


def test_sibling_dsn_rescues_the_port(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            image: myapp:1
            environment: {DATABASE_URL: "postgres://u:p@db:5432/app"}
          db:
            image: postgres:16
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.name == "db" and node.port == 5432 and node.port_source == "sibling_dsn"
    assert node.state == "certifiable_obligation"


def test_templated_image_name_drops_node_templated_tag_does_not(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            services:
              valkey:
                image: valkey/valkey:${{ matrix.v }}
                ports: ["6379:6379"]
              broken:
                image: $REGISTRY_URL:$TAG
    """)
    nodes = build_service_nodes(str(tmp_path))
    assert [n.name for n in nodes] == ["valkey"]
    (n,) = nodes
    assert n.image_repo == "valkey/valkey" and n.image_tag is None
    assert "image_tag" in n.unresolved


def test_depends_on_in_degree_marks_backing_even_for_first_party(tmp_path):
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            image: acme/web:1
            depends_on: [cache]
          cache:
            image: acme/cache:1
    """)
    # `cache` has in-degree 1 -> backing, despite being first-party with no port.
    assert [n.name for n in build_service_nodes(str(tmp_path), owner="acme")] == ["cache"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_construct.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.service_construct'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/service_construct.py
"""The evidence-fusion pipeline (spec §10).

DISCOVER -> SCOPE -> FUSE -> CLASSIFY -> CERTIFY.
No service-specific knowledge lives here or anywhere below it.
"""
from __future__ import annotations

import re

from python_deps.depgraph.service_evidence import (
    Check, Port, ServiceNode, Source,
)
from python_deps.depgraph.service_parse import (
    ci_healthcheck, compose_healthcheck, derive_check, derive_port, parse_command,
    parse_depends_on, parse_entrypoint, parse_env, parse_expose, parse_image,
    parse_ports, parse_volumes, seed_mounts,
)
from python_deps.depgraph.service_relevance import (
    ci_referenced_compose_files, compute_relevance,
)
from python_deps.depgraph.service_sources import (
    DEFAULT_SOURCES, RawDeclaration, discover_all,
)

_RELEVANCE_RANK = {"ci_service": 0, "ci_referenced_compose": 1,
                   "root_compose": 2, "unreferenced_compose": 3}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _healthcheck(decl: RawDeclaration) -> tuple[str | None, dict]:
    return ci_healthcheck(decl.entry) if decl.kind == "ci" else compose_healthcheck(decl.entry)


def _fuse(name: str, decls: list[RawDeclaration], owner: str,
          ci_refs: frozenset[str]) -> ServiceNode | None:
    """Merge declarations of one service across sources, best-rung-wins per field."""
    # Highest-relevance declaration wins identity; all contribute evidence.
    ordered = sorted(decls, key=lambda d: _RELEVANCE_RANK[compute_relevance(d, ci_refs)])
    primary = ordered[0]

    image = next((d.entry.get("image") for d in ordered if d.entry.get("image")), "") or ""
    image_repo, image_tag = parse_image(image)
    if not image_repo:
        return None                              # templated image NAME: no evidence

    unresolved: list[str] = []
    if image and ":" in image.rsplit("/", 1)[-1] and image_tag is None:
        unresolved.append("image_tag")

    ports: tuple[Port, ...] = ()
    expose: tuple[int, ...] = ()
    env: dict[str, str] = {}
    command = entrypoint = None
    volumes: tuple = ()
    depends_on: tuple[str, ...] = ()
    hc_cmd, timing = None, {}
    blob = ""
    for d in ordered:
        ports = ports or parse_ports(d.entry)
        expose = expose or parse_expose(d.entry)
        env = {**parse_env(d.entry), **env}
        command = command or parse_command(d.entry)
        entrypoint = entrypoint or parse_entrypoint(d.entry)
        volumes = volumes or parse_volumes(d.entry)
        depends_on = depends_on or parse_depends_on(d.entry)
        blob = blob or d.doc_env_blob
        if hc_cmd is None:
            hc_cmd, timing = _healthcheck(d)

    unresolved += [f"env.{k}" for k, v in env.items() if "$" in v]
    port, port_source = derive_port(ports, expose, env, name, blob)
    check: Check = derive_check(hc_cmd, timing, port)

    return ServiceNode(
        id=f"service:{name}", name=name, image=image,
        image_repo=image_repo, image_tag=image_tag,
        ports=ports, port=port, port_source=port_source,
        endpoint=(f"localhost:{port}" if port else None),
        env=env, command=command, entrypoint=entrypoint,
        volumes=volumes, seed=seed_mounts(volumes),
        check=check, depends_on=depends_on,
        relevance=compute_relevance(primary, ci_refs),
        provenance=tuple(Source(d.file, d.locator, d.kind) for d in ordered),
        raw={d.kind: d.entry for d in ordered},
        state=("certifiable_obligation" if check.source != "none"
               else "declared_unverifiable"),
        unresolved=tuple(unresolved),
    )


def _is_app(name: str, decls: list[RawDeclaration], owner: str,
            backing_names: frozenset[str]) -> bool:
    """App-vs-backing from evidence only (spec §10.4)."""
    if name in backing_names:                    # structural: someone depends_on it
        return False
    for d in decls:
        if d.kind == "ci":
            return False                         # CI service containers are never the app
        if d.entry.get("build"):
            return True
    entry = decls[0].entry
    image = entry.get("image") or ""
    repo, _tag = parse_image(image)
    short = repo.rsplit("/", 1)[-1].lower()
    org = _norm(repo.split("/")[0]) if "/" in repo else ""
    own = _norm(owner)
    first_party = bool(org and own and (org in own or own in org))
    hc, _t = compose_healthcheck(entry)
    no_surface = not parse_ports(entry) and not parse_expose(entry) and not hc
    return bool(first_party and no_surface)


def build_service_nodes(repo: str, owner: str = "",
                        sources=DEFAULT_SOURCES) -> list[ServiceNode]:
    decls = discover_all(repo, sources)
    if not decls:
        return []
    ci_refs = ci_referenced_compose_files(repo)

    grouped: dict[str, list[RawDeclaration]] = {}
    for d in decls:
        grouped.setdefault(d.name, []).append(d)

    backing = frozenset(
        dep for d in decls for dep in parse_depends_on(d.entry))

    nodes: list[ServiceNode] = []
    for name, group in grouped.items():
        if _is_app(name, group, owner, backing):
            continue
        node = _fuse(name, group, owner, ci_refs)
        if node is not None:
            nodes.append(node)
    return nodes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_construct.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_construct.py tests/depgraph/test_service_construct.py
git commit -m "feat(depgraph): evidence-fusion pipeline — fuse, classify, certify"
```

---

## Task 8: Real-world fixture regression (the three cases the PoC found)

**Files:**
- Create: `tests/depgraph/fixtures/services/rq_valkey.yml`
- Create: `tests/depgraph/test_service_real_world.py`

**Interfaces:**
- Consumes: `build_service_nodes` (Task 7).

These three fixtures are verbatim reductions of real corpus repos. They lock in the bugs the PoC caught.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/fixtures/services/rq_valkey.yml   (verbatim shape from rq/rq)
```

```yaml
name: Valkey Test
jobs:
  valkey-test:
    runs-on: ubuntu-latest
    services:
      valkey:
        image: valkey/valkey:${{ matrix.valkey-version }}
        ports:
          - 6379:6379
        options: >-
          --health-cmd "valkey-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
```

```python
# tests/depgraph/test_service_real_world.py
"""Regression fixtures from the 50-repo corpus (see
.superpowers/sdd/service-schema-poc-findings.md)."""
import shutil
import textwrap
from pathlib import Path

from python_deps.depgraph.service_construct import build_service_nodes

FIXTURES = Path(__file__).parent / "fixtures" / "services"


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_rq_valkey_is_detected_with_zero_service_knowledge(tmp_path):
    """rq/rq has NO compose file. Its only signal is a CI workflow whose image
    tag is a matrix expression. No `kind`, no valkey->redis alias."""
    dest = tmp_path / ".github" / "workflows" / "valkey.yml"
    dest.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "rq_valkey.yml", dest)

    (node,) = build_service_nodes(str(tmp_path), owner="rq")
    assert node.id == "service:valkey"
    assert node.image_repo == "valkey/valkey"
    assert node.image_tag is None and "image_tag" in node.unresolved
    assert node.endpoint == "localhost:6379" and node.port_source == "ports"
    assert node.check.command == "valkey-cli ping"
    assert node.check.source == "declared_healthcheck"
    assert node.check.retries == "5" and node.check.interval_s == "10s"
    assert node.relevance == "ci_service"
    assert node.state == "certifiable_obligation"
    assert node.provenance[0].locator == "jobs.valkey-test.services.valkey"


def test_mlflow_style_tests_dir_compose_named_by_ci_is_kept(tmp_path):
    """A path heuristic would drop tests/db/compose.yml. CI names it, so it stays."""
    _write(tmp_path, "tests/db/compose.yml",
           "services:\n  postgres:\n    image: postgres:16\n    ports: ['5432:5432']\n")
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f tests/db/compose.yml up -d
              - run: pytest
    """)
    (node,) = build_service_nodes(str(tmp_path), owner="mlflow")
    assert node.name == "postgres"
    assert node.relevance == "ci_referenced_compose"


def test_testcontainers_style_fixture_compose_is_lowest_confidence(tmp_path):
    """The library's own compose fixtures are NOT its test environment."""
    _write(tmp_path, "tests/core/compose_fixtures/basic/docker-compose.yaml",
           "services:\n  alpine:\n    image: alpine:latest\n    ports: ['8080:80']\n")
    (node,) = build_service_nodes(str(tmp_path), owner="testcontainers")
    assert node.relevance == "unreferenced_compose"     # surfaced, but lowest confidence
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_real_world.py -v`
Expected: FAIL — `FileNotFoundError` on the fixture (before you create it), then assertion failures if any parser regressed.

- [ ] **Step 3: Write minimal implementation**

No production code. Create the fixture file exactly as shown in Step 1, then run. If any assertion fails, the bug is in Tasks 2–7 — fix it there, not here.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_service_real_world.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/depgraph/test_service_real_world.py tests/depgraph/fixtures/services/rq_valkey.yml
git commit -m "test(depgraph): real-world service fixtures (rq valkey, ci-named compose, fixture compose)"
```

---

## Task 9: Rewire construction; delete the construction-time LLM

**Files:**
- Modify: `src/envstate/classify_services_clean.py` (replace `_service_nodes`)
- Modify: `src/python_deps/depgraph/patch.py:11` (add `NodeSpec.data`)
- Modify: `src/python_deps/depgraph/patch_gate.py:118` (empty `start` allowed) and `:233` (merge `r.data`)
- Modify: `src/python_deps/depgraph/repoint.py:36` (`render_bind_steps` matches by hostname, not `kind`)
- Modify: `src/python_deps/depgraph/emit.py:150` (`_is_service_reciped` → state-based)
- Test: `tests/test_classify_services_clean.py` (update), `tests/depgraph/test_repoint.py` (update), `tests/depgraph/test_patch_gate_admit_clean.py` (add empty-start case)

**Interfaces:**
- Consumes: `build_service_nodes` (Task 7).
- Produces: graph nodes carrying `data["service"]` (canonical, `dataclasses.asdict(ServiceNode)`) **and** `data["setup"]` (derived compat view).

**The compat view is the whole trick.** `data["setup"]` has seven consumers (`emit`, `build_script`, `populate`, `certify`, `schedule`, `patch_gate`, `advise`). Emit it *derived* so none of them change:

```python
{"install": [], "start": "", "probe": check.command, "createdb": None,
 "post": [], "bind": bind_steps}
```

`install: []` and `start: ""` are **correct** under the new design — construction emits no commands. `build_script._service_start_block` (line 368) already no-ops on empty `start`. Emit `setup` **only** when `state == "certifiable_obligation"`, which makes `_is_service_reciped` mean "certifiable" for free.

**`render_bind_steps` must stop using `kind`.** It currently matches a config DSN to a service by `kind` (`repoint.py:44`, `declared_kinds = {s.kind for s in specs if s.kind}`). Match by **declared hostname** instead: the DSN's host equals the service `name`. This is evidence-based *and* strictly more precise — it disambiguates a repo with two postgres services, which `kind` cannot.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_repoint.py  — replace the kind-matching tests with these
from python_deps.depgraph.repoint import render_bind_steps


def test_bind_matches_by_declared_hostname_not_kind():
    steps = render_bind_steps(
        service_names=("db",),
        configs=[("DATABASE_URL", "postgres://u:p@db:5432/app")],
    )
    assert steps == ["export DATABASE_URL=postgres://u:p@localhost:5432/app"]


def test_bind_skips_a_dsn_whose_host_is_not_a_declared_service():
    steps = render_bind_steps(
        service_names=("db",),
        configs=[("EXTERNAL", "postgres://u:p@rds.amazonaws.com:5432/app")],
    )
    assert steps == []


def test_bind_disambiguates_two_services_of_the_same_kind():
    steps = render_bind_steps(
        service_names=("primary", "replica"),
        configs=[("A", "postgres://u@primary:5432/x"), ("B", "postgres://u@replica:5433/x")],
    )
    assert len(steps) == 2 and "5432" in steps[0] and "5433" in steps[1]


def test_bind_skips_non_dsn_values():
    assert render_bind_steps(service_names=("db",), configs=[("X", "hello")]) == []
```

```python
# tests/test_classify_services_clean.py  — replace the translate_service tests
import dataclasses
import textwrap

from python_deps.depgraph.emit import _is_service_reciped
from src.envstate.classify_services_clean import classify_services_clean
from python_deps.depgraph.schema import DepGraph


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_construction_makes_no_llm_call(tmp_path, monkeypatch):
    """A client that raises proves construction never calls the model."""
    _write(tmp_path, "docker-compose.yml",
           "services:\n  clickhouse:\n    image: clickhouse/clickhouse-server:24\n"
           "    ports: ['8123:8123']\n")

    class Boom:
        def __getattr__(self, _n):
            raise AssertionError("construction must not call the LLM")

    graph = classify_services_clean(DepGraph(), str(tmp_path), client=Boom(), model="x")
    node = next(n for n in graph.nodes if n.id == "service:clickhouse")
    assert node.data["service"]["check"]["source"] == "tcp_port"


def test_certifiable_node_gets_a_compat_setup_view(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:16\n    ports: ['5432:5432']\n"
           "    healthcheck:\n      test: ['CMD', 'pg_isready']\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    node = next(n for n in graph.nodes if n.id == "service:db")
    assert node.data["setup"]["probe"] == "pg_isready"
    assert node.data["setup"]["install"] == [] and node.data["setup"]["start"] == ""
    assert _is_service_reciped(node)                    # certifiable -> reciped


def test_unverifiable_node_is_admitted_but_not_reciped(tmp_path):
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:11-alpine\n")
    graph = classify_services_clean(DepGraph(), str(tmp_path))
    node = next(n for n in graph.nodes if n.id == "service:db")
    assert node.data["service"]["state"] == "declared_unverifiable"
    assert "setup" not in node.data                     # nothing for the host to run
    assert not _is_service_reciped(node)                # surfaced, never enforced
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_repoint.py tests/test_classify_services_clean.py -v`
Expected: FAIL — `TypeError: render_bind_steps() got an unexpected keyword argument 'service_names'`, and `KeyError: 'service'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/repoint.py — replace render_bind_steps (line 36)
def render_bind_steps(
    service_names: Iterable[str],
    configs: Iterable[tuple[str, str]],
) -> list[str]:
    """``export <VAR>=<loopback DSN>`` for configs pointing at a declared service.

    Matched by the service's DECLARED HOSTNAME (its compose/CI key), not by a
    service ``kind``: the DSN `postgres://u@db:5432/x` binds to the service named
    `db`. This is evidence-based and disambiguates two services of the same kind,
    which a kind-match cannot.
    """
    names = {n for n in service_names if n}
    steps: list[str] = []
    for var, value in configs:
        parsed = service_from_url(value)
        if parsed is None:
            continue
        host = _host_of(value)
        if host not in names:
            continue
        steps.append(f"export {var}={_repoint_host(value)}")
    return steps
```

Add the `_host_of` helper next to `_repoint_host` in the same module:

```python
def _host_of(dsn: str) -> str | None:
    """The hostname component of a DSN: postgres://u:p@db:5432/x -> 'db'."""
    from urllib.parse import urlsplit
    try:
        return urlsplit(dsn).hostname
    except ValueError:
        return None
```

```python
# src/python_deps/depgraph/emit.py — replace _is_service_reciped (line 150)
def _is_service_reciped(node: Node) -> bool:
    """A SERVICE node the host can certify — i.e. one whose evidence yielded a
    readiness check (``state == "certifiable_obligation"``). Gated behind
    ``V3_INCLUDE_SERVICES`` at the orchestration boundary, never here.

    Backward-compatible: construction emits ``data['setup']`` only for certifiable
    nodes, so the legacy ``bool(data['setup'])`` predicate and the state check agree.
    """
    if node.type is not NodeType.SERVICE:
        return False
    service = node.data.get("service") or {}
    if service:
        return service.get("state") == "certifiable_obligation"
    return bool(node.data.get("setup"))          # pre-migration graphs
```

**`NodeSpec` has no `data=` field.** Verified: it lives at `patch.py:11` with fields
`id, type, name, layer, check_command, evidence_ref, promotion, service_kind, service_params, setup`.
The payload reaches `Node.data` only through `patch_gate` (line 233-241). So add one field:

```python
# src/python_deps/depgraph/patch.py — add to NodeSpec (after `setup`)
    data: dict | None = None      # extra node payload merged into Node.data
                                  # (evidence-only Service nodes put ServiceNode here)
```

```python
# src/python_deps/depgraph/patch_gate.py — in the node build (~line 233), merge it
        if r.setup is not None and nt is NodeType.SERVICE:
            data["setup"] = r.setup
            if r.service_kind:
                data["service_kind"] = r.service_kind
            check_command = render_probe_poll(r.setup["probe"])
        if r.data:
            data.update(r.data)          # NEW: evidence payload (data["service"])
```

**`patch_gate._requirement_errors` rejects an empty `start`** (line 118: *"must have a start
command"*). Under evidence-only, construction emits **no** start command — the agent writes it at
repair. Relax that one check to "must be a string"; the empty-**probe** guard (line 111-114) is about
`render_probe_poll("")` producing a broken shell and stays exactly as-is.

```python
# src/python_deps/depgraph/patch_gate.py — replace the start check (line 118)
            # Evidence-only Service nodes carry NO start command: construction emits
            # only a readiness check, and the agent writes `start` at repair time
            # (spec §3.0.2 invariant 1). The non-empty PROBE guard above is what keeps
            # render_probe_poll from emitting a broken shell -- `start` needs no such guard.
            if not isinstance(r.setup.get("start"), str):
                errs.append(f"setup for {r.id} must have a start command (string, may be empty)")
```

```python
# src/envstate/classify_services_clean.py — replace _service_nodes and its imports
import dataclasses
import os

from python_deps.depgraph.repoint import render_bind_steps
from python_deps.depgraph.service_construct import build_service_nodes
# DELETE: from src.envstate.service_translate import translate_service
# DELETE: from python_deps.depgraph.provisioning_spec import iter_provisioning_specs


def _compat_setup(node, bind_steps: list[str]) -> dict:
    """Derived view of the evidence node in the legacy ``data['setup']`` shape.

    Construction emits NO commands (spec §3.0.2 invariant 1), so `install` and
    `start` are empty by design -- the agent writes them at repair time. `probe`
    is the evidence-derived check. Seven consumers read this key
    (emit/build_script/populate/certify/schedule/patch_gate/advise); emitting a
    derived view keeps them all working unchanged.
    """
    return {"install": [], "start": "", "probe": node.check.command,
            "createdb": None, "post": [], "bind": bind_steps}


def _service_nodes(repo_path, arch, client, model, hits, configs) -> list[NodeSpec]:
    """One NodeSpec per declared BACKING service, built from evidence only.

    No LLM. No kind table. Probe-less services are admitted as
    ``declared_unverifiable`` (surfaced to the agent) rather than dropped.
    """
    owner = os.path.basename(os.path.dirname(os.path.abspath(repo_path)))
    services = build_service_nodes(repo_path, owner=owner)
    names = tuple(s.name for s in services)
    specs: list[NodeSpec] = []
    for svc in services:
        setup = (_compat_setup(svc, render_bind_steps(names, configs))
                 if svc.state == "certifiable_obligation" else None)
        specs.append(NodeSpec(
            id=svc.id, type="Service", name=svc.name, layer="services",
            setup=setup,                       # None for declared_unverifiable
            service_kind=None,                 # there is no kind, by design
            data={"service": dataclasses.asdict(svc)},
            evidence_ref=_service_evidence(hits, svc.name, svc.image_repo),
        ))
    return specs
```

> `service_kind=None` is safe: `_requirement_errors` only validates it against
> `KNOWN_SERVICE_KINDS` when it is **not** None (line 93).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_repoint.py tests/test_classify_services_clean.py tests/depgraph/ -v`
Expected: PASS. `test_construction_makes_no_llm_call` proves the LLM is gone.

- [ ] **Step 5: Commit**

Stage `emit.py` with `-p` (another session has unrelated hunks in that file):

```bash
git add src/envstate/classify_services_clean.py src/python_deps/depgraph/repoint.py \
        src/python_deps/depgraph/patch.py src/python_deps/depgraph/patch_gate.py
git add -p src/python_deps/depgraph/emit.py     # ONLY the _is_service_reciped hunk
git add tests/test_classify_services_clean.py tests/depgraph/test_repoint.py \
        tests/depgraph/test_patch_gate_admit_clean.py
git commit -m "feat: evidence-only service construction; delete construction-time LLM"
```

---

## Task 10: Delete the dead code

**Files:**
- Delete: `src/envstate/service_translate.py`
- Delete: `src/python_deps/depgraph/provisioning_spec.py`
- Delete: `tests/test_service_translate.py`, `tests/evals/test_stage_translate.py`, `tests/depgraph/test_provisioning_spec.py`
- Modify: `src/python_deps/depgraph/service_scan.py` (remove `_kind_of` **only**)
- Modify: `src/python_deps/depgraph/service_recipes.py` (remove `KindBase`, `_KIND_BASE`, `render_setup` **only**)
- Modify: `tests/evals/test_stage_parse_admit.py` (port off `parse_provisioning_spec`)

**Do NOT delete** `service_scan.py` or `service_recipes.py`. Verified consumers that must keep working:
- `service_scan.service_bind_url` → `service_recipes.py:14`
- `service_scan.service_from_url` → `repoint.py:21`, `classify_services_clean.py:33`
- `service_scan.scan_ci_services` / `scan_compose_services` → `static_collect.py:14`, `src/eval/language_package_eval/oracle.py:55`
- `service_scan.classify_service_error` → `runtime_classify.py:119`
- `service_recipes.render_probe_poll` → `patch_gate.py:21`

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_no_service_tables.py
"""The governing constraint (spec §2): no service-specific table anywhere."""
import importlib

import pytest


def test_kind_of_is_gone():
    mod = importlib.import_module("python_deps.depgraph.service_scan")
    assert not hasattr(mod, "_kind_of")


def test_recipe_table_is_gone():
    mod = importlib.import_module("python_deps.depgraph.service_recipes")
    assert not hasattr(mod, "_KIND_BASE")
    assert not hasattr(mod, "render_setup")
    assert not hasattr(mod, "KindBase")


def test_surviving_service_scan_exports_still_import():
    mod = importlib.import_module("python_deps.depgraph.service_scan")
    for sym in ("service_bind_url", "service_from_url",
                "scan_ci_services", "scan_compose_services", "classify_service_error"):
        assert hasattr(mod, sym), sym


def test_render_probe_poll_survives_for_patch_gate():
    mod = importlib.import_module("python_deps.depgraph.service_recipes")
    assert hasattr(mod, "render_probe_poll")


def test_deleted_modules_are_gone():
    for name in ("src.envstate.service_translate",
                 "python_deps.depgraph.provisioning_spec"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_no_service_tables.py -v`
Expected: FAIL — `_kind_of` still present; `service_translate` still importable.

- [ ] **Step 3: Write minimal implementation**

```bash
git rm src/envstate/service_translate.py src/python_deps/depgraph/provisioning_spec.py
git rm tests/test_service_translate.py tests/evals/test_stage_translate.py \
       tests/depgraph/test_provisioning_spec.py
```

In `src/python_deps/depgraph/service_scan.py`: delete the `_kind_of` function and its `KNOWN_SERVICE_KINDS` import **if that import becomes unused**. Keep everything else.

In `src/python_deps/depgraph/service_recipes.py`: delete `KindBase`, `_KIND_BASE`, `render_setup`, and the now-unused `service_bind_url` import **only if unused**. Keep `render_probe_poll` and `render_bind_steps` re-exports if present.

In `tests/evals/test_stage_parse_admit.py`: replace `parse_provisioning_spec(name, entry)` with the equivalent evidence call:

```python
from python_deps.depgraph.service_construct import build_service_nodes
# Build a one-service compose in tmp_path, then assert on the returned ServiceNode
# (image_repo / port / port_source / check.source) instead of the old spec fields.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ -q`
Expected: full suite PASS. No import errors anywhere.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py src/python_deps/depgraph/service_recipes.py \
        tests/depgraph/test_no_service_tables.py tests/evals/test_stage_parse_admit.py
git commit -m "refactor: delete kind table, recipe table, and construction-time translate_service"
```

---

## Task 11: Corpus verification against the real 50 repos

**Files:**
- Create: `scripts/verify_service_detection.py`

**Interfaces:**
- Consumes: `build_service_nodes` (Task 7).

This reproduces the PoC's headline numbers using the *production* code path, against the real checkouts. It is a manual verification script, not a unit test (it needs the corpus).

- [ ] **Step 1: Write the failing test**

```python
# scripts/verify_service_detection.py
"""Reproduce the PoC headline numbers with the production extractor.

Usage:
    python scripts/verify_service_detection.py <repos_root>

<repos_root> holds <owner>/<repo>/ checkouts. On the VM (READ-ONLY):
    /opt/runs/baselines/rat_python50_m3nothink_corrected/input/repo
Expected (see .superpowers/sdd/service-schema-poc-findings.md):
    repos with backing services : 23
    certifiable                 : ~75%   (declared_healthcheck ~34% + tcp_port ~41%)
    rq/rq                       : service:valkey, declared_healthcheck
"""
from __future__ import annotations

import os
import sys
from collections import Counter

from python_deps.depgraph.service_construct import build_service_nodes


def main(root: str) -> int:
    checks, repos_with, total = Counter(), set(), 0
    rq_node = None
    for owner in sorted(os.listdir(root)):
        od = os.path.join(root, owner)
        if not os.path.isdir(od):
            continue
        for repo in sorted(os.listdir(od)):
            rd = os.path.join(od, repo)
            if not os.path.isdir(rd):
                continue
            nodes = build_service_nodes(rd, owner=owner)
            if nodes:
                repos_with.add(f"{owner}/{repo}")
            total += len(nodes)
            for n in nodes:
                checks[n.check.source] += 1
                if f"{owner}/{repo}" == "rq/rq":
                    rq_node = n

    certifiable = checks["declared_healthcheck"] + checks["tcp_port"]
    print(f"repos with backing services : {len(repos_with)}")
    print(f"backing services            : {total}")
    for src, n in checks.most_common():
        print(f"  {src:22s} {n:4d}  ({n / max(total, 1) * 100:.0f}%)")
    print(f"certifiable                 : {certifiable / max(total, 1) * 100:.0f}%")
    print(f"rq/rq valkey                : {rq_node.check.command if rq_node else 'NOT DETECTED'}")

    ok = (len(repos_with) >= 20 and certifiable / max(total, 1) >= 0.65
          and rq_node is not None and rq_node.check.source == "declared_healthcheck")
    print("\nVERIFY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 scripts/verify_service_detection.py /nonexistent`
Expected: FAIL — `FileNotFoundError`. (Confirms the script actually reads the corpus.)

- [ ] **Step 3: Write minimal implementation**

Pull the corpus locally (READ-ONLY on the VM — do not write there):

```bash
SCRATCH=$(mktemp -d)
ssh -o BatchMode=yes root@167.233.64.96 \
  'cd /opt/runs/baselines/rat_python50_m3nothink_corrected/input/repo && \
   find . -type f \( -iname "*compose*.yml" -o -iname "*compose*.yaml" \
     -o -path "*/.github/workflows/*.yml" -o -path "*/.github/workflows/*.yaml" \) \
     -print0 | tar czf - --null -T -' > "$SCRATCH/svc.tgz"
mkdir -p "$SCRATCH/repos" && tar xzf "$SCRATCH/svc.tgz" -C "$SCRATCH/repos"
echo "$SCRATCH/repos"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 scripts/verify_service_detection.py "$SCRATCH/repos"`
Expected output (±1 on counts; the gate is the `VERIFY: PASS` line):

```
repos with backing services : 23
backing services            : 158
  tcp_port                  64  (41%)
  declared_healthcheck      54  (34%)
  none                      40  (25%)
certifiable                 : 75%
rq/rq valkey                : valkey-cli ping

VERIFY: PASS
```

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_service_detection.py
git commit -m "test: corpus verification for evidence-only service detection"
```

---

## Self-Review

**1. Spec coverage.**

| Spec section | Task |
|---|---|
| §2 no tables; §2.1 parsing ≠ mapping | 2 (`parse_image`), 10 (`test_no_service_tables`) |
| §3 normative schema + enums | 1 |
| §3.0.2 inv. 1 (one executable string) | 1 (test), 9 (`_compat_setup` emits no commands) |
| §3.0.2 inv. 4 (degrade field, not node) | 2, 7, 8 (rq fixture) |
| §3.1 check ladder + Python TCP one-liner | 4 |
| §3.2 port ladder incl. sibling-DSN | 3 |
| §4 two states; admit-and-surface | 7, 9 |
| §4.2 seed belongs to ACTIVATE | 2 (`seed_mounts`) |
| §10.1 DISCOVER adapters | 5 |
| §10.2 SCOPE by reachability | 6, 8 |
| §10.3 FUSE not dedup | 7 |
| §10.4 CLASSIFY app vs backing + DAG | 7 |
| §10.5 CERTIFY sets state | 7 |
| §10.6 remove kind/recipe/translate | 9, 10 |
| §10.8 corpus numbers | 11 |

Not covered **by design** (out of scope, stated above): §4.3 cold-start block renderer, §5 script contract, §6 channels, §7 loop integration, §8 guardrails, §12 ablation. These need the react-arm plan.

**2. Placeholder scan.** Clean — no TBDs, and the one former soft spot is resolved. `NodeSpec` was located at `patch.py:11` and verified to have **no `data=` field**, so Task 9 now adds it explicitly. Two blockers were found by executing the gate rather than assuming it:

- `patch_gate._requirement_errors:118` rejects an empty `setup["start"]` → Task 9 relaxes it to "must be a string" (the non-empty **probe** guard is untouched).
- `patch_gate.is_read_only` rejects `curl`/`wget` healthchecks (11/54 in the corpus) → Task 4's ladder now requires read-only on every rung and **falls through** instead of dropping the node. Measured: 9 rescue to `tcp_port`, 2 to `none`; certifiable 75% → 73%. `is_read_only(tcp_check(5432))` is `True`, so the Python one-liner survives.

**3. Type consistency.** `ServiceNode` field names are identical across Tasks 1, 7, 8, 9, 11. `derive_port` returns `(int | None, PortSource)` in Tasks 3 and 7. `derive_check(hc_cmd, timing, port) -> Check` in Tasks 4 and 7. `render_bind_steps(service_names, configs)` in Tasks 9 (impl + test). `build_service_nodes(repo, owner="", sources=DEFAULT_SOURCES)` in Tasks 7, 8, 9, 11. `RawDeclaration` fields match between Tasks 5, 6, 7.

**Risk register.**
- Task 9 is the only task that touches shared files (`emit.py`). Stage with `git add -p`.
- Task 9 changes `patch_gate` validation. Run `pytest tests/depgraph/test_patch_gate_admit_clean.py -v` before and after; the empty-**probe** guard must still reject `probe=""`.
- Task 10 will surface any consumer of `provisioning_spec` we missed. `pytest tests/ -q` is the gate; fix forward rather than restoring the module.
- Tasks 1–8 are pure and land with zero behaviour change. If the plan must stop early, stopping after Task 8 leaves the repo green with a fully tested, unused module.
