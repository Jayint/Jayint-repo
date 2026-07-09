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
  - **No exceptions.** No task in this plan touches `emit.py`. (`git add -p` is interactive and unavailable in this environment; Task 9 was amended so the need never arises.)

---

## Pre-Flight Resolutions (BINDING — supersede any conflicting task text below)

A pre-flight consistency review (2026-07-09, before Task 1) found four plan-vs-reality
blockers. These resolutions are authoritative. Where a task section below still carries the
old text, **this section wins**.

**R1 — `_kind_of` is NOT dead code; the evidence layer is kind-gated.**
`service_scan._services_from_yaml_doc` (line ~109) calls `_kind_of` *and keys its output dict
by the returned kind*. Unknown kinds are therefore **silently dropped before any consumer sees
them** — rq's `valkey` never reaches `static_collect`. Two kept consumers depend on this:
`static_collect.py:43,47` and `src/eval/language_package_eval/oracle.py:127` (which reads
`.keys()`, i.e. *kinds*, not service names).
**RESOLUTION (user-approved): rekey by service name.** Rewrite `_services_from_yaml_doc` to key
by the declared service name and return `image`/`port`/`healthcheck`/`source` per service, then
delete `_kind_of` and the now-unused `KNOWN_SERVICE_KINDS` import. Update `static_collect` and
`oracle` for the new key semantics, and their tests. This removes the last kind table *and*
fixes the exotic-drop bug. Now **Task 10a**.

**R2 — `provisioning_spec.py` has importers the plan never listed.**
Real importers: `repoint.py:20`, `classify_services_clean.py:31`,
`evals/service_config_detection/stage_parse_admit.py:30`,
`evals/service_config_detection/stage_translate.py:42,113`,
`tests/depgraph/test_provisioning_spec.py`, `tests/depgraph/test_repoint.py:3`,
`tests/test_service_translate.py:21`.
**RESOLUTION:** Task 9 must also delete the module-level import at `repoint.py:20`. Task 10's
delete set expands (below).

**R3 — deleting `_KIND_BASE`/`render_setup` breaks a surviving test, and `RECIPE_KINDS` too.**
`tests/depgraph/test_service_recipes_clean.py:5` imports `_KIND_BASE, render_setup`;
`evals/service_config_detection/stage_translate.py:43` imports `RECIPE_KINDS`
(defined at `service_recipes.py:33`). The plan's own new `test_no_service_tables.py` asserts
these are gone — the two tests cannot both pass.
**RESOLUTION:** Task 10 deletes `tests/depgraph/test_service_recipes_clean.py` and removes
`RECIPE_KINDS` alongside `KindBase`/`_KIND_BASE`/`render_setup`. Keep `render_probe_poll` and
`normalize_probe` (used by `patch_gate.py:21` and surviving tests).

**R4 — the "seven consumers of `data["setup"]`" count is wrong.**
Real **readers** (8): `advise.py:166`, `build_script.py:377`, `certify.py:87`, `emit.py:150`,
`populate.py:61`, `schedule.py:40`, `schedule.py:104`, **`src/envstate/graph_scheduler.py:91`**.
`patch_gate.py:237` is the **writer**, not a reader. The compat view keeps all eight working.
**Note the behavior shift, and state it in the Task 9 report:** because Task 9 emits `setup`
only for `state == "certifiable_obligation"`, `graph_scheduler.unsatisfied_provisionable_services`
now gates `done` on *certifiable* services only. This is intended (a `declared_unverifiable`
service has no probe, so it can never be host-certified and must not block `done`), and it is
behind `allow_services`, default-OFF, so the byte-identical baseline is unaffected.

**R5 — fate of `evals/service_config_detection/` (user-approved: retire translate, port parse).**
`provision_corpus.py`, `provision_certify.py`, `level3_labels.py` are **pure data/subprocess
modules with no pipeline imports** — they survive untouched, as do
`tests/evals/test_provision_corpus.py` and `tests/evals/test_provision_certify.py`.
Only two files import the doomed modules:
- **DELETE** `evals/service_config_detection/stage_translate.py` + `tests/evals/test_stage_translate.py`
  — they measure the construction-time LLM stage this plan removes; obsolete by construction.
- **DELETE** `evals/service_config_detection/stage_parse_admit.py` + `tests/evals/test_stage_parse_admit.py`
  — its role passes to **Task 12**, whose known-answer oracle is now
  `provision_corpus.PROVISION_CASES` (18 verbatim-labeled compose blocks, ground truth, already
  green) rather than a hand transcription of `ratbench-service-catalog.md`.

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
| ~~Modify~~ `src/python_deps/depgraph/emit.py` | **Untouched.** `_is_service_reciped` already reads `bool(data["setup"])`, and the compat view emits `setup` only for certifiable nodes — so the existing predicate already means "certifiable". Pinned by Task 9's tests. |
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
- Produces: `derive_port(ports, expose, env, name, sibling_values) -> tuple[int | None, PortSource]`, where `sibling_values: tuple[str, ...]`.

**Why:** `Spoolman/db` (`postgres:11-alpine`) declares no `ports:` and no healthcheck — but the app declares `DATABASE_URL=...@db:5432/...`. The port is evidence; it just lives in a *sibling*. This rung rescued 9 services in the PoC.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/depgraph/test_service_parse.py
from python_deps.depgraph.service_evidence import Port
from python_deps.depgraph.service_parse import derive_port


def test_port_ladder_prefers_declared_ports():
    got = derive_port((Port(5432, 5432),), (6379,), {"URL": "x://h:1234"}, "db", ())
    assert got == (5432, "ports")


def test_port_ladder_falls_back_to_expose():
    assert derive_port((), (6379,), {}, "cache", ()) == (6379, "expose")


def test_expose_beats_own_env_dsn():
    """Pins the MIDDLE of the ladder, not just its ends."""
    env = {"DATABASE_URL": "postgres://u:p@db:5432/app"}
    assert derive_port((), (6379,), env, "db", ()) == (6379, "expose")


def test_port_ladder_falls_back_to_own_env_dsn():
    env = {"DATABASE_URL": "postgres://u:p@db:5432/app"}
    assert derive_port((), (), env, "db", ()) == (5432, "env_dsn")


def test_own_env_dsn_beats_sibling_dsn():
    """Pins the MIDDLE of the ladder: own evidence wins over a sibling's."""
    env = {"URL": "postgres://u:p@db:5432/app"}
    siblings = ("postgres://u:p@db:9999/app",)
    assert derive_port((), (), env, "db", siblings) == (5432, "env_dsn")


def test_port_ladder_rescues_from_sibling_url_dsn():
    # `db` declares nothing; the APP declares the DSN naming host `db`.
    siblings = ("postgres://u:p@db:5432/app", "redis://cache:6379/0")
    assert derive_port((), (), {}, "db", siblings) == (5432, "sibling_dsn")
    assert derive_port((), (), {}, "cache", siblings) == (6379, "sibling_dsn")


def test_port_ladder_rescues_from_sibling_bare_token():
    """8 of the PoC's 9 real rescues are bare `host:port` tokens, not URLs:
    KAFKA_HOSTS=kafka:9092, TEMPORAL_ADDRESS=temporal:7233, MEMCACHE_LOCATION=memcached:11211.
    The regex rung must survive."""
    assert derive_port((), (), {}, "kafka", ("kafka:9092",)) == (9092, "sibling_dsn")
    assert derive_port((), (), {}, "redis", ("local:redis:6379",)) == (6379, "sibling_dsn")


def test_sibling_url_must_match_the_HOST_not_the_userinfo():
    """THE CRITICAL CASE. In `postgres://db:5432@other/app`, `db` is the USERNAME and
    `5432` the PASSWORD; the real host is `other`. A bare `\bdb:5432\b` regex wrongly
    rescues 5432 for service `db`. A value containing `://` MUST be decided by urlparse."""
    assert derive_port((), (), {}, "db", ("postgres://db:5432@other/app",)) == (None, "none")


def test_sibling_url_attributes_the_port_to_the_real_host():
    siblings = ("postgres://db:5432@other:6543/app",)
    assert derive_port((), (), {}, "other", siblings) == (6543, "sibling_dsn")
    assert derive_port((), (), {}, "db", siblings) == (None, "none")


def test_sibling_rescue_requires_a_name_boundary():
    # must not match "mydb:5432" when looking for service "db"
    assert derive_port((), (), {}, "db", ("mydb:5432",)) == (None, "none")


def test_sibling_url_with_no_port_yields_nothing():
    assert derive_port((), (), {}, "db", ("postgres://u:p@db/app",)) == (None, "none")


def test_sibling_url_with_templated_port_does_not_raise():
    assert derive_port((), (), {}, "db", ("redis://db:$PORT",)) == (None, "none")


def test_sibling_url_with_templated_host_does_not_raise():
    assert derive_port((), (), {}, "db", ("redis://$HOST:6379",)) == (None, "none")


def test_malformed_url_never_raises():
    """`urlparse("redis://[db:6379")` raises ValueError: Invalid IPv6 URL.
    derive_port must degrade the field, never explode."""
    assert derive_port((), (), {}, "db", ("redis://[db:6379",)) == (None, "none")


def test_malformed_url_does_not_fall_back_to_the_regex():
    """A `://` value is decided by urlparse ALONE. Falling back to the token regex
    when urlparse fails would re-open the userinfo hole this rung exists to close."""
    assert derive_port((), (), {}, "db", ("postgres://[db:5432@other/app",)) == (None, "none")


def test_port_ladder_gives_up_cleanly():
    assert derive_port((), (), {}, "svc", ()) == (None, "none")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_service_parse.py -k port_ladder -v`
Expected: FAIL — `ImportError: cannot import name 'derive_port'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/python_deps/depgraph/service_parse.py
import re

from python_deps.depgraph.service_evidence import PortSource

_DSN_PORT = re.compile(r"://[^/\s]*?:(\d{2,5})")   # own-env rung (host position)


def _url_host_port(value: str) -> tuple[str, int | None] | None:
    """`(host, port)` for a URL value, or None if it is not a usable URL.

    `postgres://db:5432@other/app` has USERNAME `db`, PASSWORD `5432`, HOST `other`.
    A bare token regex misreads that as "db is on 5432". urlparse cannot.

    `.port` raises ValueError on a templated port (`redis://db:$PORT`); the HOST is
    still authoritative there, so we keep the host and drop only the port.
    """
    try:
        parsed = urlparse(value)
        if not parsed.scheme:
            return None
        host = parsed.hostname
    except ValueError:                     # e.g. `redis://[db:6379` -> Invalid IPv6 URL
        return None
    if host is None:
        return None
    try:
        port = parsed.port
    except ValueError:                     # templated port: degrade the field, keep host
        port = None
    return host, port


def _rescue_from_siblings(name: str, sibling_values: tuple[str, ...]) -> int | None:
    """`db` declares no ports, but the app declares `DATABASE_URL=...@db:5432/x`.
    The port is still evidence — it just lives in a sibling service's env.

    Two rungs, because real repos use both forms (measured on the 50-repo corpus):
      * URL values (`postgres://u:p@db:5432/app`) — decided by urlparse HOST equality.
      * bare tokens (`KAFKA_HOSTS=kafka:9092`) — 8 of the PoC's 9 rescues. `\b` bounds
        stop `db` matching inside `mydb:5432`.

    A value containing `://` is decided by urlparse ALONE and never reaches the regex —
    not even when urlparse fails. Falling back to the regex there would re-open the
    userinfo hole (`postgres://[db:5432@other/app` is unparseable, yet the regex would
    happily rescue 5432 for `db`). Unparseable evidence yields no evidence.
    """
    for value in sibling_values:
        if "://" in value:
            hp = _url_host_port(value)
            if hp is not None and hp[0] == name and hp[1]:
                return hp[1]
            continue                       # URL values NEVER reach the regex
        m = re.search(rf"\b{re.escape(name)}:(\d{{2,5}})\b", value)
        if m:
            return int(m.group(1))
    return None


def derive_port(ports: tuple[Port, ...], expose: tuple[int, ...],
                env: dict[str, str], name: str,
                sibling_values: tuple[str, ...]) -> tuple[int | None, PortSource]:
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
    rescued = _rescue_from_siblings(name, sibling_values)
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
    assert cmd.startswith("python3 -c")
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

TCP_CHECK = ("python3 -c \"import socket; "
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
- Produces: `RawDeclaration` (frozen dataclass: `name: str`, `entry: dict`, `file: str`, `locator: str`, `kind: str`, `doc_env_values: tuple[str, ...]`); `ServiceEvidenceSource` Protocol with `discover(repo: str) -> Iterator[RawDeclaration]`; `ComposeSource`, `GithubActionsSource`; `DEFAULT_SOURCES: tuple[ServiceEvidenceSource, ...]`; `discover_all(repo, sources=DEFAULT_SOURCES) -> list[RawDeclaration]`.

`doc_env_values` is the tuple of *all* env values declared in the same compose document — the input to the sibling-DSN rung. It is a **tuple of individual values, not a concatenated blob**: Task 3's rescue must `urlparse` each value on its own to check its hostname, which a blob makes impossible. CI declarations carry `()`.

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
    assert any("db:5432" in v for v in db.doc_env_values)   # sibling evidence carried along
    assert isinstance(db.doc_env_values, tuple)             # values, not a blob


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
    assert decls[0].doc_env_values == ()


def test_vendored_trees_are_pruned(tmp_path):
    """A compose file under node_modules/ belongs to a dependency, not this repo."""
    _write(tmp_path, "node_modules/pkg/docker-compose.yml", """
        services:
          vendored:
            image: postgres:16
    """)
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:16
    """)
    assert [d.name for d in ComposeSource().discover(str(tmp_path))] == ["db"]


def test_list_form_environment_reaches_doc_env_values(tmp_path):
    """compose allows `environment:` as a LIST of `K=V` strings, not only a mapping."""
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            image: app:1
            environment:
              - DATABASE_URL=postgres://u:p@db:5432/app
          db:
            image: postgres:16
    """)
    db = next(d for d in ComposeSource().discover(str(tmp_path)) if d.name == "db")
    assert any("db:5432" in v for v in db.doc_env_values)


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
    doc_env_values: tuple[str, ...]   # each env value in the same document, separately
                                     # (sibling-DSN input; NOT joined — Task 3 urlparses each)


class ServiceEvidenceSource(Protocol):
    def discover(self, repo: str) -> Iterator[RawDeclaration]: ...


# Vendored/derived trees. A compose file under `node_modules/` or `site-packages/`
# belongs to a dependency, not to this repo, and the 50-repo corpus has thousands of them.
_PRUNED_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "site-packages",
    ".tox", "__pycache__", ".mypy_cache", ".pytest_cache",
})


def _load(path: str) -> object | None:
    """A bad file skips itself. Narrow: an unexpected exception is a bug, not a bad file.

    Matches the convention already landed in `service_scan._load_yaml`.
    """
    try:
        with open(path, errors="replace") as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def _walk(repo: str) -> Iterator[tuple[str, str]]:
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _PRUNED_DIRS]   # prune in place
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
            # A TUPLE of individual values, never a joined blob: Task 3's rescue
            # urlparses each value to compare its HOSTNAME against a service name.
            values = tuple(v for e in svcs.values() if isinstance(e, dict)
                           for v in parse_env(e).values())
            for name, entry in svcs.items():
                if isinstance(entry, dict):
                    yield RawDeclaration(str(name), entry, rel, f"services.{name}",
                                         "compose", values)


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
- Modify: `src/python_deps/depgraph/service_sources.py` — rename the private `_load` to a public `load_yaml` (same body) and update its one caller. Both modules must parse YAML the same guarded way; duplicating a loader is how the two drift apart.
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


def test_reference_paths_are_normalized_not_char_stripped(tmp_path):
    """`lstrip("./")` is character stripping. `../compose.yml` must NOT match a root
    declaration, and `deploy/../compose.yml` MUST normalize to `compose.yml`."""
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f ./deploy/../docker-compose.yml up
    """)
    refs = ci_referenced_compose_files(str(tmp_path))
    assert refs == frozenset({"docker-compose.yml"})
    assert compute_relevance(_decl("docker-compose.yml"), refs) == "ci_referenced_compose"


def test_norm_rejects_absolute_and_degenerate_paths():
    """`_norm`'s contract: a repo-relative POSIX path, or None. `C:\\x` becomes `C:/x`
    after backslash conversion — absolute, and never a repo declaration."""
    from python_deps.depgraph.service_relevance import _norm
    for bad in ("/abs/x.yml", "//x.yml", "C:\\x.yml", "c:/x.yml", "..", "../", "a/../..", "", "  ", "."):
        assert _norm(bad) is None, bad
    assert _norm("./deploy/../docker-compose.yml") == "docker-compose.yml"
    assert _norm("dir with space/b.yml") == "dir with space/b.yml"


def test_a_reference_escaping_the_repo_is_not_a_reference(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f ../outside/docker-compose.yml up
    """)
    assert ci_referenced_compose_files(str(tmp_path)) == frozenset()


def test_only_run_step_bodies_are_scanned(tmp_path):
    """Scanning raw file text would turn a step NAME into a compose reference."""
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - name: docker compose -f decoy.yml up
                uses: actions/checkout@v4
    """)
    assert ci_referenced_compose_files(str(tmp_path)) == frozenset()


def test_equals_form_and_quoted_paths(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose --file=a.yml up
              - run: docker compose -f "dir with space/b.yml" up
    """)
    refs = ci_referenced_compose_files(str(tmp_path))
    assert refs == frozenset({"a.yml", "dir with space/b.yml"})


def test_root_override_compose_is_the_default_environment():
    """`docker compose up` with no -f auto-loads docker-compose.override.yml."""
    assert compute_relevance(_decl("docker-compose.override.yml"), frozenset()) == "root_compose"


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
import posixpath
import re
from typing import Iterator

from python_deps.depgraph.service_evidence import Relevance
from python_deps.depgraph.service_sources import RawDeclaration

# `docker compose -f X`, `docker-compose --file X`
# Two levels, not one: a single regex captures only the FIRST -f, silently dropping
# the second file of `docker compose -f a.yml -f b.yml up`.
_COMPOSE_CMD = re.compile(r"docker[-\s]compose[^\n;|&]*")
_FILE_FLAG = re.compile(r"""(?:-f|--file)(?:\s+|=)("[^"]+"|'[^']+'|\S+)""")
_DRIVE = re.compile(r"^[A-Za-z]:")   # `C:\x` -> `C:/x` is absolute, not repo-relative

# Compose auto-loads an `override` file alongside its base when invoked with no `-f`,
# so a root override IS part of the repo's default environment.
_ROOT_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
               "docker-compose.override.yml", "docker-compose.override.yaml",
               "compose.override.yml", "compose.override.yaml")


def _norm(path: str) -> str | None:
    """A declaration's repo-relative POSIX path, or None if it cannot be one.

    `lstrip("./")` is NOT path normalization — it strips leading `.` and `/` CHARACTERS,
    so `../compose.yml` becomes `compose.yml` and falsely matches a root declaration,
    while `deploy/../compose.yml` is left unnormalized and matches nothing.
    """
    raw = path.strip().strip("'\"").replace("\\", "/")
    if not raw:
        return None
    norm = posixpath.normpath(raw)
    if (posixpath.isabs(norm) or _DRIVE.match(norm)
            or norm in (".", "..") or norm.startswith("../")):
        return None                       # absolute, or escapes the repo: not a declaration
    return norm


def _run_bodies(doc: object) -> Iterator[str]:
    """Every `jobs.<job>.steps[].run` string. Scanning raw file TEXT instead would
    manufacture a reference out of a comment or a `name:` field."""
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return
    for job in jobs.values():
        steps = job.get("steps") if isinstance(job, dict) else None
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                yield step["run"]


def ci_referenced_compose_files(repo: str) -> frozenset[str]:
    """Compose paths that a workflow explicitly brings up. This is the edge from
    'the thing that runs the tests' to 'the environment it needs'.

    Known limitation: a `run:` body that merely *quotes* the command
    (`echo "docker compose -f fake.yml"`) still registers. Accepted — a repo that
    echoes the command almost always runs it too, and a false `ci_referenced_compose`
    only promotes a node we would have surfaced anyway.
    """
    wf = os.path.join(repo, ".github", "workflows")
    if not os.path.isdir(wf):
        return frozenset()
    found: set[str] = set()
    for fname in sorted(os.listdir(wf)):
        if not fname.lower().endswith((".yml", ".yaml")):
            continue
        for body in _run_bodies(load_yaml(os.path.join(wf, fname))):
            for cmd in _COMPOSE_CMD.finditer(body):
                for m in _FILE_FLAG.finditer(cmd.group(0)):
                    ref = _norm(m.group(1))
                    if ref:
                        found.add(ref)
    return frozenset(found)


def compute_relevance(decl: RawDeclaration, ci_refs: frozenset[str]) -> Relevance:
    if decl.kind == "ci":
        return "ci_service"                       # the job IS the test
    norm = _norm(decl.file)
    if norm is None:
        return "unreferenced_compose"
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
- Produces: `build_service_nodes(repo: str, owner: str = "", sources=DEFAULT_SOURCES) -> list[ServiceNode]`.

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


def test_image_named_after_the_repo_is_the_app(tmp_path):
    """CLASSIFY rule 2. Direction matters: the REPO name is a substring of the IMAGE
    name (`podman-compose` in `podman-compose-test`), not the reverse."""
    root = tmp_path / "podman-compose"
    root.mkdir()
    _write(root, "docker-compose.yml", """
        services:
          app:
            image: containers/podman-compose-test:1
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    assert [n.name for n in build_service_nodes(str(root))] == ["db"]


def test_a_digest_pinned_image_does_not_report_an_unresolved_tag(tmp_path):
    """`repo@sha256:...` legitimately has no tag. Only a `:tag` that vanished to a
    template is `unresolved`."""
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres@sha256:abcdef0123456789
            ports: ["5432:5432"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.image_tag is None
    assert "image_tag" not in node.unresolved


def test_a_templated_tag_does_report_an_unresolved_tag(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            services:
              valkey:
                image: valkey/valkey:${{ matrix.valkey-version }}
                ports: ["6379:6379"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.image_repo == "valkey/valkey" and node.image_tag is None
    assert "image_tag" in node.unresolved


def test_depends_on_in_an_unreferenced_compose_does_not_reclassify_the_app(tmp_path):
    """`depends_on` is a WITHIN-DOCUMENT relation. Unioning it repo-wide let an unrelated
    compose force the repo's own app (build: + image:) to be surfaced as a backing service."""
    root = tmp_path / "myrepo"
    root.mkdir()
    _write(root, "docker-compose.yml", """
        services:
          app:
            build: .
            image: myorg/app:dev
            ports: ["8000:8000"]
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    _write(root, "sub/docker-compose.yml", """
        services:
          other:
            image: nginx:1
            depends_on: [app]
    """)
    names = sorted(n.name for n in build_service_nodes(str(root)))
    assert "app" not in names          # rule 1 (build:) still fires
    assert names == ["db", "other"]


def test_same_name_different_images_is_flagged_not_silently_merged(tmp_path):
    """mlflow ships a postgres variant and a mysql variant, both naming the service `db`.
    Keep the highest-relevance image, but SAY the image is ambiguous, and keep both raws."""
    _write(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:16
            ports: ["5432:5432"]
    """)
    _write(tmp_path, "docker-compose.mysql.yml", """
        services:
          db:
            image: mysql:8
            ports: ["3306:3306"]
    """)
    (node,) = build_service_nodes(str(tmp_path))
    assert node.image == "postgres:16"                 # root_compose outranks unreferenced
    assert "image" in node.unresolved                  # ...but the ambiguity is surfaced
    assert set(node.raw) == {"compose:docker-compose.yml",
                             "compose:docker-compose.mysql.yml"}
    assert node.raw["compose:docker-compose.mysql.yml"]["image"] == "mysql:8"


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
    # `raw` is keyed "<kind>:<file>" uniformly, so a second file declaring the same
    # service name cannot silently overwrite the first.
    assert set(node.raw) == {"compose:docker-compose.yml",
                             "ci:.github/workflows/ci.yml"}


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
    # `web` carries `build:` so CLASSIFY rule 1 drops it, exactly as Spoolman declares it.
    # Its env still feeds `doc_env_values`, which is per-DOCUMENT, so `db` is still rescued.
    # (A bare `image: myapp:1` app with no port and no healthcheck would be SURFACED as a
    # backing service under `owner=""` — the documented limit of evidence-only CLASSIFY,
    # not something a fourth rule should paper over.)
    _write(tmp_path, "docker-compose.yml", """
        services:
          web:
            build: .
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

import os
import re
from dataclasses import dataclass

from python_deps.depgraph.service_evidence import (
    Check, Mount, Port, ServiceNode, Source,
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


def _slug(s: str) -> str:
    """Lowercase alphanumeric squash, for comparing an image org to a repo owner.
    Named `_slug`, not `_norm`: `service_relevance._norm` is a PATH normalizer."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _healthcheck(decl: RawDeclaration) -> tuple[str | None, dict]:
    return ci_healthcheck(decl.entry) if decl.kind == "ci" else compose_healthcheck(decl.entry)


@dataclass(frozen=True)
class _Merged:
    """Best-rung-wins merge of one service's fields across its declarations."""
    ports: tuple[Port, ...]
    expose: tuple[int, ...]
    env: dict[str, str]
    command: str | None
    entrypoint: str | None
    volumes: tuple[Mount, ...]
    depends_on: tuple[str, ...]
    hc_cmd: str | None
    timing: dict
    sibling_values: tuple[str, ...]


def _merge(ordered: list[RawDeclaration]) -> _Merged:
    """First non-empty wins, walking declarations from highest relevance down.

    `env` merges the other way (`{**new, **acc}`) so the highest-relevance value of a
    duplicated key survives.
    """
    ports: tuple[Port, ...] = ()
    expose: tuple[int, ...] = ()
    env: dict[str, str] = {}
    command = entrypoint = None
    volumes: tuple[Mount, ...] = ()
    depends_on: tuple[str, ...] = ()
    hc_cmd, timing = None, {}
    sibling_values: tuple[str, ...] = ()          # NOT `blob` — Task 3 takes a tuple
    for d in ordered:
        ports = ports or parse_ports(d.entry)
        expose = expose or parse_expose(d.entry)
        env = {**parse_env(d.entry), **env}
        command = command or parse_command(d.entry)
        entrypoint = entrypoint or parse_entrypoint(d.entry)
        volumes = volumes or parse_volumes(d.entry)
        depends_on = depends_on or parse_depends_on(d.entry)
        sibling_values = sibling_values or d.doc_env_values
        if hc_cmd is None:
            hc_cmd, timing = _healthcheck(d)
    return _Merged(ports, expose, env, command, entrypoint, volumes,
                   depends_on, hc_cmd, timing, sibling_values)


def _image_conflict(ordered: list[RawDeclaration]) -> bool:
    """Two declarations of one name naming different images. We keep the highest-relevance
    one, but the node must SAY SO — silent selection is how the mysql variant disappears."""
    repos = {parse_image(d.entry.get("image") or "")[0]
             for d in ordered if d.entry.get("image")}
    return len(repos) > 1


def _unresolved_fields(image: str, image_tag: str | None, env: dict[str, str],
                       image_conflict: bool = False) -> tuple[str, ...]:
    out: list[str] = []
    last = image.rsplit("/", 1)[-1]
    # `repo@sha256:...` legitimately has no tag; only a `:tag` that vanished is unresolved.
    if "@" not in last and ":" in last and image_tag is None:
        out.append("image_tag")
    if image_conflict:
        out.append("image")
    out += [f"env.{k}" for k, v in env.items() if "$" in v]
    return tuple(out)


def _fuse(name: str, decls: list[RawDeclaration],
          ci_refs: frozenset[str]) -> ServiceNode | None:
    """Merge declarations of one service across sources, best-rung-wins per field."""
    # Highest-relevance declaration wins identity; all contribute evidence.
    ordered = sorted(decls, key=lambda d: _RELEVANCE_RANK[compute_relevance(d, ci_refs)])
    primary = ordered[0]

    image = next((d.entry.get("image") for d in ordered if d.entry.get("image")), "") or ""
    image_repo, image_tag = parse_image(image)
    if not image_repo:
        return None                              # templated image NAME: no evidence

    m = _merge(ordered)
    port, port_source = derive_port(m.ports, m.expose, m.env, name, m.sibling_values)
    check: Check = derive_check(m.hc_cmd, m.timing, port)

    # Keyed by kind AND file: two compose files may both declare `db` (mlflow ships a
    # postgres variant and a mysql variant). Keying by kind alone silently drops one,
    # and `raw` is the agent's primary evidence.
    raw = {f"{d.kind}:{d.file}": d.entry for d in ordered}

    return ServiceNode(
        id=f"service:{name}", name=name, image=image,
        image_repo=image_repo, image_tag=image_tag,
        ports=m.ports, port=port, port_source=port_source,
        endpoint=(f"localhost:{port}" if port else None),
        env=m.env, command=m.command, entrypoint=m.entrypoint,
        volumes=m.volumes, seed=seed_mounts(m.volumes),
        check=check, depends_on=m.depends_on,
        relevance=compute_relevance(primary, ci_refs),
        provenance=tuple(Source(d.file, d.locator, d.kind) for d in ordered),
        raw=raw,
        state=("certifiable_obligation" if check.source != "none"
               else "declared_unverifiable"),
        unresolved=_unresolved_fields(image, image_tag, m.env, _image_conflict(ordered)),
    )


def _is_app(name: str, decls: list[RawDeclaration], owner: str, repo_name: str,
            backing_names: frozenset[str]) -> bool:
    """App-vs-backing from evidence only (spec §10.4). Three rules, no service knowledge.

    Took over-detection from 594 nodes to 158 on the 50-repo corpus.
    """
    if name in backing_names:                    # structural: someone depends_on it
        return False
    if any(d.kind == "ci" for d in decls):
        return False                             # CI service containers are never the app
    if any(d.entry.get("build") for d in decls):
        return True                              # rule 1: locally built

    entry = decls[0].entry
    image = entry.get("image") or ""
    repo, _tag = parse_image(image)
    short = repo.rsplit("/", 1)[-1].lower()

    # rule 2: the image is named after the repo. DIRECTION MATTERS — the repo name is a
    # substring of the image name (`podman-compose` in `podman-compose-test`), not the reverse.
    if repo_name and repo_name.lower() in short:
        return True

    # rule 3: a first-party image that publishes no port and declares no healthcheck is the
    # app being deployed. Kills OpenCTI's 271 `opencti/connector-*`, keeps `supabase/postgres`
    # (first-party, but exposes a port).
    org = _slug(repo.split("/")[0]) if "/" in repo else ""
    own = _slug(owner)
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

    # `depends_on` names siblings in the SAME document. Unioning it across the whole repo
    # lets a `depends_on: [app]` in an unreferenced compose reclassify the repo's own app
    # as a backing service, defeating CLASSIFY rules 1-3.
    declared_in: dict[str, set[str]] = {}
    for d in decls:
        declared_in.setdefault(d.file, set()).add(d.name)
    backing = frozenset(
        dep for d in decls for dep in parse_depends_on(d.entry)
        if dep in declared_in.get(d.file, ()))

    repo_name = os.path.basename(os.path.normpath(repo))

    nodes: list[ServiceNode] = []
    for name, group in grouped.items():
        if _is_app(name, group, owner, repo_name, backing):
            continue
        node = _fuse(name, group, ci_refs)
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

> **PREREQUISITE: Task 10a MUST land before this task.** `classify_services_clean` anchors each
> node to an `evidence_ref` produced by `static_collect.collect_static_evidence` →
> `service_scan.scan_compose_services`, which is **kind-table-gated**: it keys its output dict by
> `_kind_of(...)`, so an exotic service (clickhouse, valkey, weaviate) yields no evidence at all and
> `classify` bails at `if not hits: return graph`. `build_service_nodes` discovers it fine; the
> legacy collector cannot anchor it. Task 10a rekeys the scanners by declared service name and
> deletes `_kind_of`, which is the fix. Running Task 9 first makes its own exotic-service acceptance
> test (`test_construction_makes_no_llm_call`, clickhouse) unpassable.
> *Execution order for this plan is therefore: 1-8, **10a**, 9, 10, 11-14.*


**Files:**
- Modify: `src/envstate/classify_services_clean.py` (replace `_service_nodes`)
- Modify: `src/python_deps/depgraph/patch.py:11` (add `NodeSpec.data`)
- Modify: `src/python_deps/depgraph/patch_gate.py:118` (empty `start` allowed) and `:233` (merge `r.data`)
- Modify: `src/python_deps/depgraph/repoint.py:36` (`render_bind_steps` matches by hostname, not `kind`) **and delete the module-level import at `repoint.py:20`** (`from python_deps.depgraph.provisioning_spec import ProvisioningSpec`) — pre-flight resolution R2. Task 10 deletes that module; if this import survives, importing `repoint` (hence `classify_services_clean`, hence the whole construction path) raises `ModuleNotFoundError`.
- **`src/python_deps/depgraph/emit.py` — DO NOT TOUCH.** `_is_service_reciped` already reads
  `bool(node.data.get("setup"))`, and the compat view emits `setup` *only* for certifiable nodes,
  so the existing predicate already means "certifiable". Editing it would be a behavioural no-op
  that (a) buys nothing and (b) forces an interactive `git add -p` into a file another session is
  actively editing. Two tests below pin the invariant *through the existing predicate*, which is a
  stronger guarantee than rewriting it. This also removes the only `git add -p` in the plan.
- Test: `tests/test_classify_services_clean.py` (update), `tests/depgraph/test_repoint.py` (**rewrite** — all 7 existing tests construct `ProvisioningSpec(...)` and call the old two-arg signature; both are gone. Remove them all, do not append), `tests/depgraph/test_patch_gate_admit_clean.py` (add empty-start case)

**Interfaces:**
- Consumes: `build_service_nodes` (Task 7).
- Produces: graph nodes carrying `data["service"]` (canonical, `dataclasses.asdict(ServiceNode)`) **and** `data["setup"]` (derived compat view).

**The compat view is the whole trick.** `data["setup"]` has **eight readers** — pre-flight
resolution R4 corrects the plan's earlier count of seven: `advise.py:166`, `build_script.py:377`,
`certify.py:87`, `emit.py:150`, `populate.py:61`, `schedule.py:40`, `schedule.py:104`, and
**`src/envstate/graph_scheduler.py:91`**. (`patch_gate.py:237` is the *writer*, not a reader.)
Emit it *derived* so none of them change:

```python
{"install": [], "start": "", "probe": check.command, "createdb": None,
 "post": [], "bind": bind_steps}
```

`install: []` and `start: ""` are **correct** under the new design — construction emits no commands. `build_script._service_start_block` (line 383) already no-ops on empty `start`. Emit `setup` **only** when `state == "certifiable_obligation"`, which makes `_is_service_reciped` mean "certifiable" for free.

**Intended behavior shift, state it in your report (R4).** Because `setup` is emitted only for
certifiable services, `graph_scheduler.unsatisfied_provisionable_services` (line 91) now gates
`done` on certifiable services only. This is correct: a `declared_unverifiable` service has no
probe, so it can never be host-certified and must not block `done`. It sits behind
`allow_services` (default-OFF), so the byte-identical baseline is unaffected.

**`render_bind_steps` must stop using `kind`.** It currently matches a config DSN to a service by `kind` (`repoint.py:47`, `declared_kinds = {s.kind for s in specs if s.kind}`). Match by **declared hostname** instead: the DSN's host equals the service `name`. This is evidence-based *and* strictly more precise — it disambiguates a repo with two postgres services, which `kind` cannot.

**Use `urllib.parse.urlparse` directly, not `service_scan.service_from_url`.** The latter returns
`None` for any scheme outside its `_SCHEME_TO_KIND` map, which would silently drop a valid DSN
pointing at an exotic service. You need only `parsed.hostname` and `parsed.port` — no kind.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_repoint.py  — replace the kind-matching tests with these
from python_deps.depgraph.repoint import render_bind_steps


def test_bind_matches_by_declared_hostname_not_kind():
    steps = render_bind_steps(
        service_names=("db",),
        configs=[("DATABASE_URL", "postgres://u:p@db:5432/app")],
    )
    # `_repoint_host` rewrites the host to `_LOCALHOST`, which is "127.0.0.1".
    assert steps == ["export DATABASE_URL=postgres://u:p@127.0.0.1:5432/app"]


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
        # NO `service_from_url` gate: it returns None for any scheme outside its
        # hardcoded _SCHEME_TO_KIND map, silently dropping a valid DSN that points at
        # an exotic service. The hostname is all the evidence we need.
        host = _host_of(value)
        if host is None or host not in names:
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

No `git add -p` is needed: this task does not touch `emit.py`.

```bash
git add src/envstate/classify_services_clean.py src/python_deps/depgraph/repoint.py \
        src/python_deps/depgraph/patch.py src/python_deps/depgraph/patch_gate.py
git add tests/test_classify_services_clean.py tests/depgraph/test_repoint.py \
        tests/depgraph/test_patch_gate_admit_clean.py
git commit -m "feat: evidence-only service construction; delete construction-time LLM"
```

---

## Task 10a: Rekey the evidence scanners by service name; delete `_kind_of`

> **Pre-flight resolution R1.** The original Task 10 said "remove `_kind_of` **only**". That is
> impossible: `_services_from_yaml_doc` *calls* `_kind_of` and **keys its output dict by the
> returned kind**, so an unknown kind is dropped entirely. `scan_compose_services` and
> `scan_ci_services` — both kept — are therefore blind to every exotic service (rq's `valkey`
> among them). This task removes the table by rekeying on the declared service name, which is
> what the evidence actually says.

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py` (rewrite `_services_from_yaml_doc`; delete `_kind_of`; drop the now-unused `KNOWN_SERVICE_KINDS` import)
- Modify: `src/eval/language_package_eval/oracle.py:127` (keys are now service names)
- Test: `tests/depgraph/test_service_scan.py` (update key expectations), `tests/eval/language_package_eval/test_oracle.py` (verify)

**Interfaces:**
- Produces: `scan_compose_services(repo_path) -> dict[str, dict]` and
  `scan_ci_services(repo_path) -> tuple[dict[str, dict], bool]`, both keyed by **declared
  service name**. Each value keeps its existing shape:
  `{"image": str, "port": int | None, "healthcheck": str, "source": str}`.
- Consumers unchanged otherwise: `static_collect.py:43,47` (uses `svc` as `name=`, `meta` for the
  snippet), `oracle.py:127`.

**Do NOT touch** `service_from_url` / `_SCHEME_TO_KIND` in this task. That map is a *lexical*
URL-scheme parse (`postgresql+psycopg2://` → the `postgres` scheme family), it is on the config
path, not the ServiceNode path, and `classify_services_clean.py:33` + `runtime_classify` depend
on it. Task 9 removes `repoint`'s dependence on its `kind` return value.

- [ ] **Step 1: Write the failing test**

Replace `test_scan_compose_services` and `test_scan_ci_services_and_presence` in
`tests/depgraph/test_service_scan.py` with these, and add the exotic-service regression:

```python
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
    assert set(found) == {"db", "cache"}          # keyed by DECLARED NAME, not kind
    assert found["db"]["image"] == "postgres:15"
    assert found["db"]["port"] == 5432
    assert found["cache"]["image"] == "redis:7"


def test_scan_compose_services_keeps_exotic_services(tmp_path):
    """The bug this task fixes: an unknown kind used to be dropped silently."""
    _w(tmp_path, "docker-compose.yml", """
        services:
          valkey:
            image: valkey/valkey:8
            ports: ["6379:6379"]
          weaviate:
            image: semitechnologies/weaviate:1.25.0
    """)
    found = scan_compose_services(str(tmp_path))
    assert set(found) == {"valkey", "weaviate"}
    assert found["valkey"]["image"] == "valkey/valkey:8"


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
    assert found["postgres"]["image"] == "postgres:14"


def test_scan_ci_services_keeps_exotic_service(tmp_path):
    _w(tmp_path, ".github/workflows/valkey.yml", """
        jobs:
          valkey-test:
            services:
              valkey:
                image: valkey/valkey:8
    """)
    found, present = scan_ci_services(str(tmp_path))
    assert present is True
    assert "valkey" in found


def test_first_declaration_of_a_name_wins(tmp_path):
    _w(tmp_path, "docker-compose.yml", """
        services:
          db:
            image: postgres:15
    """)
    _w(tmp_path, "docker-compose.override.yml", """
        services:
          db:
            image: postgres:16
    """)
    found = scan_compose_services(str(tmp_path))
    assert found["db"]["image"] == "postgres:15"
```

Keep `test_scan_compose_services_modern_compose_yaml`,
`test_scan_compose_services_override_variant`, `test_scan_compose_services_ignores_lookalike`,
`test_compose_meta_captures_healthcheck`, and `test_compose_meta_healthcheck_absent_returns_empty`
— but update any assertion that indexes by kind so it indexes by the declared service name.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_service_scan.py -v`
Expected: FAIL — `set(found) == {"db", "cache"}` gets `{"postgres", "redis"}`; the exotic tests
get an empty dict.

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/depgraph/service_scan.py`, delete `_kind_of` entirely, delete the
`from python_deps.depgraph.service_tables import KNOWN_SERVICE_KINDS` import (verify with
`grep -n KNOWN_SERVICE_KINDS src/python_deps/depgraph/service_scan.py` that nothing else in the
file uses it), and replace `_services_from_yaml_doc` with:

```python
def _services_from_yaml_doc(doc, source: str, out: dict[str, dict]) -> None:
    """Merge a parsed YAML doc's `services:` blocks into `out`, keyed by the DECLARED
    service name (first declaration of a name wins).

    Evidence-only: no kind recognition. An unknown image is still a declared service —
    keying by kind used to drop it silently (spec §2, pre-flight resolution R1).
    """
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
            if not svc_name or svc_name in out:
                continue
            entry = entry if isinstance(entry, dict) else {}
            out[str(svc_name)] = {
                "image": entry.get("image") or "",
                "port": _port_of(entry),
                "healthcheck": _healthcheck_of(entry),
                "source": source,
            }
```

Then update the docstring at the top of the module: the `package->service` table sentence is
about `static_collect`, not this module — leave it, but change "first kind wins" wording
anywhere it appears to "first declaration of a name wins".

In `src/eval/language_package_eval/oracle.py`, line 127 currently reads:

```python
        services |= set(scan_compose_services(str(repo_dir)).keys())
```

The keys are now declared service names rather than kinds. That is the correct oracle semantics
(the oracle declares what the repo declares). Leave the line as-is, but update the comment
above it if it says "kind". Then run `tests/eval/language_package_eval/test_oracle.py` — the
`compose_postgres` fixture declares a service *named* `postgres`, so
`declared_by_tier["SERVICE"] == ("postgres",)` still holds. **If any fixture names a service
differently from its kind, update the expectation to the declared name and say so in your
report** — do not rename the fixture to make the old assertion pass.

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
python3 -m pytest tests/depgraph/test_service_scan.py tests/eval/language_package_eval/test_oracle.py \
    tests/depgraph/test_static_collect.py -q
```
Expected: PASS. Then confirm the table is gone:
```bash
grep -n "_kind_of\|KNOWN_SERVICE_KINDS" src/python_deps/depgraph/service_scan.py
```
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py src/eval/language_package_eval/oracle.py \
        tests/depgraph/test_service_scan.py tests/eval/language_package_eval/test_oracle.py
git commit -m "refactor(service_scan): key evidence by declared service name, delete _kind_of

The kind table was silently dropping every exotic service (valkey, weaviate)
before static_collect or the oracle could see it. Evidence is the declared name."
```

---

## Task 10: Delete the dead code

> **Pre-flight resolutions R2/R3/R5 apply.** The delete set below is the verified one. The
> original plan's set left three dangling importers that turn `pytest tests/ -q` red.

**Files:**
- Delete: `src/envstate/service_translate.py`
- Delete: `src/python_deps/depgraph/provisioning_spec.py`
- Delete: `evals/service_config_detection/stage_translate.py`
- Delete: `evals/service_config_detection/stage_parse_admit.py`
- Delete: `tests/test_service_translate.py`
- Delete: `tests/evals/test_stage_translate.py`
- Delete: `tests/evals/test_stage_parse_admit.py`
- Delete: `tests/depgraph/test_provisioning_spec.py`
- Delete: `tests/depgraph/test_service_recipes_clean.py`
- Modify: `src/python_deps/depgraph/service_recipes.py` (remove `KindBase`, `_KIND_BASE`, `render_setup`, `RECIPE_KINDS`)
- Create: `tests/depgraph/test_no_service_tables.py`

**Why these evals go (R5).** `stage_translate.py` measures the construction-time LLM recipe
stage that this plan removes — it is obsolete by construction. `stage_parse_admit.py` measures
parsing/admission, whose role passes to **Task 12**; Task 12's oracle is
`evals/service_config_detection/provision_corpus.PROVISION_CASES` (18 verbatim-labeled compose
blocks). `provision_corpus.py`, `provision_certify.py`, `level3_labels.py` are pure data /
subprocess modules with **no pipeline imports** — they and their tests
(`tests/evals/test_provision_corpus.py`, `tests/evals/test_provision_certify.py`) survive
untouched. Verify with `grep -rn "provisioning_spec\|service_translate\|service_recipes" evals/`
before you commit: the only hits must be in files you deleted.

**Do NOT delete** `service_scan.py` or `service_recipes.py`. Verified consumers that must keep
working:
- `service_scan.service_bind_url` → `service_recipes.py:14`
- `service_scan.service_from_url` → `repoint.py:21`, `classify_services_clean.py:33`
- `service_scan.scan_ci_services` / `scan_compose_services` → `static_collect.py:14`, `src/eval/language_package_eval/oracle.py:55`
- `service_scan.classify_service_error` → `runtime_classify.py:119`
- `service_recipes.render_probe_poll` → `patch_gate.py:21`
- `service_recipes.normalize_probe` → keep (used by surviving tests and `render_probe_poll`)

**Precondition:** Task 9 already removed `from python_deps.depgraph.provisioning_spec import
ProvisioningSpec` at `repoint.py:20`. If that import is still present, stop and report — Task 9
was not completed.

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
    assert not hasattr(mod, "RECIPE_KINDS")


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

Run: `python3 -m pytest tests/depgraph/test_no_service_tables.py -v`
Expected: FAIL — `_KIND_BASE`/`render_setup`/`RECIPE_KINDS` still present; `service_translate`
still importable. (`test_kind_of_is_gone` already passes: Task 10a removed it.)

- [ ] **Step 3: Write minimal implementation**

```bash
git rm src/envstate/service_translate.py \
       src/python_deps/depgraph/provisioning_spec.py \
       evals/service_config_detection/stage_translate.py \
       evals/service_config_detection/stage_parse_admit.py \
       tests/test_service_translate.py \
       tests/evals/test_stage_translate.py \
       tests/evals/test_stage_parse_admit.py \
       tests/depgraph/test_provisioning_spec.py \
       tests/depgraph/test_service_recipes_clean.py
```

In `src/python_deps/depgraph/service_recipes.py`: delete `KindBase`, `_KIND_BASE`,
`render_setup`, `RECIPE_KINDS` (line 33), and the `_START` dict that `RECIPE_KINDS` is built
from — plus any import that becomes unused (check `service_bind_url` at line 14: keep it only if
something still calls it in this module). Keep `render_probe_poll` and `normalize_probe`.

Then remove the now-stale docstring references to `render_setup` — these are prose, not code, so
they will not break anything, but leaving them is a lie in the source:
`build_script.py:373,375`, `repoint.py:7`, `emit.py:149`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/ -q`
Expected: full suite PASS, **no collection errors**. Then prove no dangling references:
```bash
grep -rn "provisioning_spec\|service_translate\|render_setup\|_KIND_BASE\|RECIPE_KINDS" \
     src/ tests/ evals/ scripts/
```
Expected: no output. (Any hit is a dangling reference — fix it before committing.)

> **Baseline note for your report:** this branch carries pre-existing failures in
> `tests/depgraph/*` from another session's WIP (`resolve_*.py`, `wheel_oracle.py`,
> `emit.py`). Record the failure count from `git stash list`-free baseline
> (`python3 -m pytest tests/ -q` on the commit *before* your change) and confirm your change
> adds none. Do not attempt to fix them; they are not yours.

- [ ] **Step 5: Commit**

Stage **only** the files this task names. Never `git add -A`.

```bash
git add src/python_deps/depgraph/service_recipes.py \
        src/python_deps/depgraph/build_script.py \
        src/python_deps/depgraph/repoint.py \
        tests/depgraph/test_no_service_tables.py
git commit -m "refactor: delete recipe table and construction-time translate_service

Retires the obsolete stage_translate/stage_parse_admit evals with them; their
ground-truth corpus (provision_corpus.PROVISION_CASES) survives and becomes the
Task 12 detection-fidelity oracle."
```

(The `git rm` in Step 3 already staged the deletions.)

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

---

# Evaluation Tasks (12–14)

> **These can run BEFORE Tasks 1–11.** The PoC (`.superpowers/sdd/service_schema_poc.py`) already
> emits the identical schema to `.superpowers/sdd/service_nodes_poc.jsonl` (158 nodes). Point Task 13
> at that file to learn whether the evidence is sufficient *before* committing to the build. If C1
> does not beat C0 convincingly, the schema — not the implementation — is what needs changing.

**Two evals, deliberately separate:**
- **Task 12 — detection fidelity:** *did we find the right services?* Deterministic, no LLM.
- **Task 13 — sufficiency:** *is a `ServiceNode` enough for a ReAct agent to provision the service?*
  Behavioural, not an opinion poll.

Standing directive (memory: `eval-grade-qualitatively-not-just-numbers`): pair the pass-rate with a
cheap LLM judge over a known-answer corpus; **the judge is diagnostic, never the headline.** Here the
headline is deterministic (policy violations, and in Task 14 a Docker-verified check); the judge only
explains *why*.

---

## Task 12: Detection fidelity against a known-answer oracle

**Files:**
- Create: `tests/depgraph/test_service_parse_fidelity.py` (block-level — inherits the retired `stage_parse_admit` signal)
- Create: `tests/eval/fixtures/service_oracle.json` (repo-level)
- Create: `scripts/eval_service_detection_fidelity.py` (repo-level)

**Interfaces:**
- Consumes: `build_service_nodes(repo, owner)` (Task 7); `parse_image`, `derive_port`, `derive_check` (Tasks 2–4).
- Produces: pooled precision / recall / F1 of detected backing services vs the oracle.

**Why an oracle:** Task 11 reports what we *extracted*; it cannot tell us whether that set is *right*.
`.superpowers/sdd/ratbench-service-catalog.md` is a careful reader's ground truth (it applied semantic
judgment: app-vs-backing, fixture-vs-real). Scoring against it measures whether our evidence-only
heuristics reproduce that judgment.

**Two oracles, two levels (pre-flight resolution R5).** Task 10 deletes
`evals/service_config_detection/stage_parse_admit.py`. Its measurement does not die with it — its
ground-truth corpus is a **pure data module with no pipeline imports**, so it survives and moves here.

- [ ] **Step 0: Block-level parse fidelity (inherit the retired eval's corpus)**

`evals/service_config_detection/provision_corpus.py` holds `PROVISION_CASES`: 18 `ServiceCase`
records, each pairing a verbatim compose-service YAML block with transcribed ground truth
(`name`, `kind`, `compose_entry`, `expect`, `expected_probe_family`, `known_failure`). It stays
green and untouched. Score the **new** parser against it — but score only what evidence-only
detection *claims* to know. Do **not** assert on `case.kind` or `case.expected_probe_family`:
those are exactly the semantic lookups this design refuses to perform. Assert instead that every
declared service is *admitted* (the exotic ones especially) and that its lexical fields parse.

```python
# tests/depgraph/test_service_parse_fidelity.py
"""Block-level parse fidelity, inherited from the retired stage_parse_admit eval.

The old eval asked "does the parser recover (kind, params) for a known kind?". The new
design has no kinds, so we ask the evidence-only question instead: is every declared
service ADMITTED, and are its lexical fields recovered? The corpus is unchanged ground
truth (`provision_corpus.PROVISION_CASES`, 18 cases incl. 4 adversarial).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from evals.service_config_detection.provision_corpus import PROVISION_CASES  # noqa: E402
from python_deps.depgraph.service_parse import (  # noqa: E402
    compose_healthcheck, derive_check, derive_port, parse_env, parse_expose,
    parse_image, parse_ports,
)


@pytest.mark.parametrize("case", PROVISION_CASES, ids=lambda c: c.name)
def test_every_declared_service_is_admitted(case):
    """No case is dropped. The old kind-keyed path dropped every exotic service."""
    entry = yaml.safe_load(case.compose_entry)
    entry = entry if isinstance(entry, dict) else {}
    image = entry.get("image") or ""
    repo, _tag = parse_image(image)
    assert repo, f"{case.name}: image {image!r} yielded no repo — service would be dropped"


@pytest.mark.parametrize("case", PROVISION_CASES, ids=lambda c: c.name)
def test_check_ladder_never_raises_and_records_its_rung(case):
    entry = yaml.safe_load(case.compose_entry)
    entry = entry if isinstance(entry, dict) else {}
    ports = parse_ports(entry)
    expose = parse_expose(entry)
    env = parse_env(entry)
    port, port_source = derive_port(ports, expose, env, case.name, ())
    hc_cmd, timing = compose_healthcheck(entry)
    check = derive_check(hc_cmd, timing, port)
    assert check.source in ("declared_healthcheck", "tcp_port", "none")
    assert port_source in ("ports", "expose", "env_dsn", "sibling_dsn", "none")
    if check.source == "none":
        assert check.command is None      # Task 1: Check.command is `str | None`
    else:
        assert check.command


def test_corpus_admits_the_exotic_tail():
    """The whole point: kinds outside any table still produce a node."""
    exotic = [c for c in PROVISION_CASES if c.kind in ("weaviate", "milvus", "qdrant")]
    assert exotic, "corpus lost its exotic cases"
    for case in exotic:
        entry = yaml.safe_load(case.compose_entry) or {}
        repo, _ = parse_image(entry.get("image") or "")
        assert repo, f"{case.name} dropped"
```

Run: `python3 -m pytest tests/depgraph/test_service_parse_fidelity.py -q` → all 18 cases admitted.
**If a case is dropped, that is a real detection bug — fix the parser, not the test.**

- [ ] **Step 1: Build the repo-level oracle**

Transcribe the per-repo backing-service names from `ratbench-service-catalog.md` into this exact
shape. One entry per repo that declares ≥1 genuine backing service (the catalog says 22). Repos with
no backing service are represented by an empty list so false positives are scored.

```json
{
  "rq/rq":            ["valkey"],
  "mlflow/mlflow":    ["postgres", "mysql", "storage"],
  "Cloud-CV/EvalAI":  ["db", "redis", "sqs"],
  "testcontainers/testcontainers-python": [],
  "containers/podman-compose": []
}
```

- [ ] **Step 2: Write the scorer and run it to see it fail**

```python
# scripts/eval_service_detection_fidelity.py
"""Precision/recall of evidence-only service detection vs the known-answer oracle.

Usage:  PYTHONPATH=src python3 scripts/eval_service_detection_fidelity.py <repos_root> <oracle.json>
"""
from __future__ import annotations

import json
import os
import sys

from python_deps.depgraph.service_construct import build_service_nodes


def main(root: str, oracle_path: str) -> int:
    oracle: dict[str, list[str]] = json.load(open(oracle_path))
    tp = fp = fn = 0
    rows = []
    for full, expected in sorted(oracle.items()):
        owner, repo = full.split("/", 1)
        rd = os.path.join(root, owner, repo)
        got = {n.name for n in build_service_nodes(rd, owner=owner)} if os.path.isdir(rd) else set()
        exp = set(expected)
        t, f, m = len(got & exp), len(got - exp), len(exp - got)
        tp, fp, fn = tp + t, fp + f, fn + m
        if f or m:
            rows.append((full, sorted(got - exp), sorted(exp - got)))

    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    print(f"pooled precision {prec:.3f}  recall {rec:.3f}  F1 {f1:.3f}   (tp={tp} fp={fp} fn={fn})")
    print("\nper-repo disagreements (extra / missing):")
    for full, extra, missing in rows:
        print(f"  {full:40s} +{extra}  -{missing}")
    ok = prec >= 0.80 and rec >= 0.90
    print("\nVERIFY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
```

Run: `PYTHONPATH=src python3 scripts/eval_service_detection_fidelity.py "$SCRATCH/repos" tests/eval/fixtures/service_oracle.json`
Expected before the oracle exists: `FileNotFoundError`.

- [ ] **Step 3: Run against the corpus and record the disagreements**

Expected: **recall ≥ 0.90** (missing a declared service is the costly error — the agent would be
blind to it). **Precision ≥ 0.80** (a false positive costs the agent turns, but fail-soft contains it,
so we tolerate more of them). Every disagreement is a real finding: log it, and only then decide
whether to tune `_is_app` or amend the oracle. **Do not tune the extractor until the oracle disagrees
with a human reading of the repo.**

- [ ] **Step 4: Commit**

```bash
git add scripts/eval_service_detection_fidelity.py tests/eval/fixtures/service_oracle.json
git commit -m "eval: detection fidelity vs known-answer service oracle"
```

---

## Task 13: Sufficiency — can a ReAct agent provision from a ServiceNode?

**Files:**
- Create: `src/eval/service_sufficiency/__init__.py`
- Create: `src/eval/service_sufficiency/brief.py`
- Create: `src/eval/service_sufficiency/graders.py`
- Create: `src/eval/service_sufficiency/run.py`
- Test: `tests/eval/test_service_sufficiency_graders.py`

**Interfaces:**
- Consumes: `ServiceNode` (Task 1), or a `service_nodes.jsonl` row (identical shape).
- Produces: `render_brief(node, condition) -> str`; `grade(commands, node) -> Grade`; a per-condition report.

`brief.py` is **not throwaway** — it is the same projection that becomes `service_graph_context(graph)`
in the react-arm plan (`entry.py:75`). Write it to be reused.

### Conditions (the ablation that makes this mean something)

| Condition | What the generator sees | Tests |
|---|---|---|
| **C0** reactive baseline | only a pytest failure line, e.g. `redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379` | what a RAT-style agent has |
| **C1** obligation | the full brief (image, env, command, endpoint, check, raw, provenance) | the graph's contribution |
| **C2** C1 minus `raw` | normalized fields only | is verbatim evidence load-bearing? |
| **C3** C1 minus `check` | no success criterion | is the certificate load-bearing? |

**Stratify the sample** — a random draw is all postgres/redis and scores ~100% everywhere, teaching
nothing. Sample ~32 nodes across four strata:
1. **head** (`postgres`, `redis`, `mysql`) — C0 may also succeed here
2. **exotic tail** (`clickhouse`, `milvus`, `redpanda`, `elasticmq`, `minio`) — where world knowledge thins
3. **`declared_unverifiable`** — we *expect* `INSUFFICIENT`; a correct refusal is a **pass**, not a failure
4. **rq's `valkey`** — templated tag, redis-compatible fork

- [ ] **Step 1: Write the failing grader test**

```python
# tests/eval/test_service_sufficiency_graders.py
from src.eval.service_sufficiency.graders import grade


class _N:                       # minimal node stand-in
    port = 6379
    image_repo = "valkey/valkey"


def test_flags_the_valkey_failure_mode_third_party_repo():
    cmds = ("curl -fsSL https://packages.valkey.io/valkey.gpg | gpg --dearmor -o /k.gpg\n"
            "echo 'deb https://packages.valkey.io/debian bookworm main' > /etc/apt/sources.list.d/v.list\n"
            "apt-get install -y valkey-server")
    g = grade(cmds, _N())
    assert g.policy_violation is True


def test_local_curl_healthcheck_is_not_a_policy_violation():
    g = grade("apt-get install -y redis-server\nredis-server --daemonize yes\n"
              "curl -f http://localhost:6379/", _N())
    assert g.policy_violation is False


def test_detects_background_start_and_declared_port():
    g = grade("apt-get install -y redis-server\nredis-server --daemonize yes --port 6379", _N())
    assert g.background_start is True and g.uses_declared_port is True


def test_service_start_counts_as_background_start():
    g = grade("apt-get install -y postgresql\nservice postgresql start", _N())
    assert g.background_start is True


def test_parses_an_insufficient_refusal():
    g = grade("INSUFFICIENT: no port and no healthcheck; cannot verify readiness", _N())
    assert g.insufficient is True and "port" in g.insufficient_reason


def test_missing_start_is_caught():
    g = grade("apt-get install -y redis-server", _N())
    assert g.background_start is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/eval/test_service_sufficiency_graders.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.eval.service_sufficiency'`

- [ ] **Step 3: Implement the graders (deterministic — this is the headline)**

```python
# src/eval/service_sufficiency/graders.py
"""Deterministic grading of an agent's provisioning commands.

The headline metrics are mechanical. An LLM judge (run.py) only explains WHY a
node failed; it never decides whether it passed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Fetching a NON-LOCAL url, or bolting on a third-party apt source. This is exactly
# the `packages.valkey.io` recipe that regressed rq from 1/470 to build_failed.
_REMOTE_FETCH = re.compile(
    r"(?:curl|wget)\s+[^\n|]*https?://(?!localhost|127\.0\.0\.1)", re.I)
_APT_SOURCE = re.compile(
    r"add-apt-repository|sources\.list|apt-key\s+add|gpg\s+--dearmor", re.I)

_BACKGROUND = re.compile(
    r"--daemonize|\bnohup\b|&\s*$|service\s+\S+\s+start|/etc/init\.d/\S+\s+start"
    r"|systemctl\s+start|\bsupervisord\b", re.I | re.M)

_INSUFFICIENT = re.compile(r"^\s*INSUFFICIENT\s*:?\s*(.*)$", re.I | re.M)


@dataclass(frozen=True)
class Grade:
    policy_violation: bool
    background_start: bool
    uses_declared_port: bool
    insufficient: bool
    insufficient_reason: str


def grade(commands: str, node) -> Grade:
    m = _INSUFFICIENT.search(commands)
    if m:
        return Grade(False, False, False, True, m.group(1).strip())
    port = getattr(node, "port", None)
    return Grade(
        policy_violation=bool(_REMOTE_FETCH.search(commands) or _APT_SOURCE.search(commands)),
        background_start=bool(_BACKGROUND.search(commands)),
        uses_declared_port=bool(port and str(port) in commands),
        insufficient=False,
        insufficient_reason="",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/eval/test_service_sufficiency_graders.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Implement the brief renderer**

```python
# src/eval/service_sufficiency/brief.py
"""Project a ServiceNode into the agent-facing brief (spec §4.3 / §6).

This is the same projection that becomes `service_graph_context(graph)` in the
react-arm plan -- keep it reusable.
"""
from __future__ import annotations


def render_brief(n: dict, condition: str) -> str:
    if condition == "C0":                       # what a reactive agent actually sees
        port = n.get("port") or "?"
        return (f"The repo's tests fail with:\n"
                f"  ConnectionError: [Errno 111] connecting to localhost:{port}\n"
                f"Provision whatever is needed so the tests can run.")

    lines = [f"Service `{n['name']}` is required by this repo's tests.",
             f"Declared image: {n['image']}"]
    if n.get("endpoint"):
        lines.append(f"It must answer at: {n['endpoint']}")
    if n.get("env"):
        kv = " ".join(f"{k}={v}" for k, v in list(n["env"].items())[:6])
        lines.append(f"Declared config: {kv}")
    if n.get("command"):
        lines.append(f"Declared start args: {n['command']}")
    if n.get("seed"):
        lines.append(f"Seed mounts: {n['seed']}")
    if condition != "C3" and n["check"]["command"]:
        lines.append(f"You will know it is up when this returns 0: {n['check']['command']}")
    if condition != "C2":
        lines.append(f"Verbatim declaration: {n['raw']}")
    lines.append("Constraint: install from the base distro's package manager. "
                 "Do not add third-party apt sources and do not download from URLs.")
    return "\n".join(lines)
```

- [ ] **Step 6: Implement the runner (generator + blind judge)**

```python
# src/eval/service_sufficiency/run.py
"""C0/C1/C2/C3 sufficiency ablation over a stratified sample of ServiceNodes.

Usage: PYTHONPATH=src python3 -m src.eval.service_sufficiency.run <nodes.jsonl> <out.json>

Works directly on .superpowers/sdd/service_nodes_poc.jsonl (same schema), so this
can run BEFORE Tasks 1-11 land.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter

from src.envstate.llm_response import complete_with_retry
from src.eval.service_sufficiency.brief import render_brief
from src.eval.service_sufficiency.graders import grade

CONDITIONS = ("C0", "C1", "C2", "C3")
HEAD = ("postgres", "redis", "mysql")

GEN_SYSTEM = (
    "You are configuring a Debian-based container. Output ONLY shell commands, no prose.\n"
    "If the information given is insufficient to install and start the service, output exactly:\n"
    "INSUFFICIENT: <the single missing piece of information>")


class _Node:
    def __init__(self, d: dict):
        self.port = d.get("port")
        self.image_repo = d.get("image_repo", "")


def _stratum(n: dict) -> str:
    if n["check"]["source"] == "none":
        return "unverifiable"
    short = n["image_repo"].rsplit("/", 1)[-1]
    return "head" if short in HEAD else "exotic"


def sample(nodes: list[dict], per_stratum: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = {}
    for n in nodes:
        buckets.setdefault(_stratum(n), []).append(n)
    out: list[dict] = []
    for _s, group in sorted(buckets.items()):
        rng.shuffle(group)
        out.extend(group[:per_stratum])
    out.extend(n for n in nodes if n["name"] == "valkey" and n not in out)
    return out


def main(nodes_path: str, out_path: str, client=None, model: str = "sonnet") -> int:
    nodes = [json.loads(l) for l in open(nodes_path)]
    picked = sample(nodes, per_stratum=10, seed=1234)
    results = []
    for n in picked:
        for cond in CONDITIONS:
            if cond == "C3" and n["check"]["source"] == "none":
                continue                       # no check to remove
            msgs = [{"role": "system", "content": GEN_SYSTEM},
                    {"role": "user", "content": render_brief(n, cond)}]
            text, _usage, _raw = complete_with_retry(client, model, msgs, temperature=0)
            g = grade(text, _Node(n))
            results.append({"repo": n["repo"], "name": n["name"],
                            "stratum": _stratum(n), "condition": cond,
                            "commands": text, **g.__dict__})

    json.dump(results, open(out_path, "w"), indent=1)

    print(f"{'cond':5s} {'n':>3s} {'ok':>5s} {'policy_viol':>12s} {'no_start':>9s} {'INSUFF':>7s}")
    for cond in CONDITIONS:
        rows = [r for r in results if r["condition"] == cond]
        if not rows:
            continue
        ok = sum(1 for r in rows
                 if not r["policy_violation"] and r["background_start"] and not r["insufficient"])
        print(f"{cond:5s} {len(rows):3d} {ok / len(rows):5.0%} "
              f"{sum(r['policy_violation'] for r in rows):12d} "
              f"{sum(not r['background_start'] and not r['insufficient'] for r in rows):9d} "
              f"{sum(r['insufficient'] for r in rows):7d}")

    print("\nby stratum (C0 -> C1 delta is the paper's claim):")
    for s in sorted({r["stratum"] for r in results}):
        for cond in ("C0", "C1"):
            rows = [r for r in results if r["stratum"] == s and r["condition"] == cond]
            if rows:
                ok = sum(1 for r in rows if not r["policy_violation"] and r["background_start"])
                print(f"  {s:14s} {cond}  {ok}/{len(rows)}")

    print("\nunverifiable stratum: a correct INSUFFICIENT refusal is a PASS")
    unv = [r for r in results if r["stratum"] == "unverifiable" and r["condition"] == "C1"]
    print(f"  refused correctly: {sum(r['insufficient'] for r in unv)}/{len(unv)}")
    print("\npolicy violations by stratum:",
          dict(Counter(r["stratum"] for r in results if r["policy_violation"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
```

- [ ] **Step 7: Run the ablation and record the result**

Run (works today against the PoC output):

```bash
PYTHONPATH=src python3 -m src.eval.service_sufficiency.run \
    .superpowers/sdd/service_nodes_poc.jsonl /tmp/sufficiency.json
```

What each outcome means — **write the interpretation down before looking**:
- **C1 ≫ C0 on the exotic stratum** → the graph's evidence is doing the work. The design's core claim.
- **C1 ≈ C0 on the head stratum** → expected and fine; an LLM installs redis from the word "redis".
- **C0 shows policy violations, C1 does not** → the brief's constraint + declared image suppress the
  URL-hallucination failure mode. This is the rq/valkey regression, measured.
- **`unverifiable` refuses with INSUFFICIENT under C1** → the schema correctly signals its own limits.
- **C2 ≈ C1** → `raw` is not load-bearing; consider dropping it (a real schema finding).
- **C3 ≪ C1** → the `check` certificate is load-bearing, as designed.

Grade **blind to condition**: when an LLM judge is used to explain failures, strip the `condition`
field from what it sees, so it cannot flatter the design.

- [ ] **Step 8: Commit**

```bash
git add src/eval/service_sufficiency/ tests/eval/test_service_sufficiency_graders.py
git commit -m "eval: ServiceNode sufficiency ablation (C0 reactive vs C1 obligation)"
```

---

## Task 14 (stretch): Docker-verified headline

**Files:**
- Create: `scripts/verify_service_sufficiency_live.py`

**Why:** Task 13's headline is deterministic but *static* — it grades command text. The strongest
possible signal removes the judge entirely: **run the generated commands and see whether
`check.command` returns 0.** That is the host certifying, exactly as the design intends.

- [ ] **Step 1: For each C1 result from Task 13, execute in a throwaway container**

```python
# scripts/verify_service_sufficiency_live.py  (sketch of the loop; use DockerExecutor)
# For each result row with condition == "C1" and not insufficient:
#   with DockerExecutor("python:3.11-slim") as ex:
#       ex.run("bash -lc " + shlex.quote(row["commands"]))    # PROVISION + ACTIVATE
#       rc, _out = ex.run(node["check"]["command"])           # the certificate
#   row["live_check_passed"] = (rc == 0)
```

- [ ] **Step 2: Report `live_check_passed` per stratum**

This is the number to put in the paper: *given only what our graph extracted from the repo's own
files, a general agent brought the service up and the repo's own healthcheck went green, N/M times.*

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_service_sufficiency_live.py
git commit -m "eval: live Docker verification of ServiceNode sufficiency"
```

**Cost note:** one container per node per condition. Restrict to C1 (and C0 for the delta) on the
stratified sample — roughly 60 short-lived containers, not 600.
