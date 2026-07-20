# Bundle-Starvation Fix (Slice C follow-up) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop the construction-time LLM classifier from being starved: feed it the BaseSettings config it already parses, the compose port/healthcheck it already (partly) parses, and canonical node ids so it can anchor edges — and make `_sanitize` drop (not void-the-batch-on) invalid-relation edges.

**Architecture:** Three of the four changes are at the **bundle-assembly seam** (`static_collect.collect_static_evidence` / `DeterministicHit` / `compact_bundle_json`) plus one compose-parser enrichment (`service_scan`). One change is the classifier prompt + sanitize hardening (`env_classifier`). No graph/schema/gate change. depgraph stays LLM-free.

**Tech Stack:** Python 3, pytest, the `python_deps/depgraph` parsers + `src/envstate` classifier.

**Why (verified in code, 2026-06-29):** `collect_static_evidence` calls only `scan_env_reads`+`parse_env_example`+compose+ci (`static_collect.py:39-54`); it never calls `scan_framework_config_reads` which reads pydantic BaseSettings fields (`config_scan.py:201-202`). `scan_compose_services` meta = `{image,port,source}` (`service_scan.py:98`) but the bundle forwards only `image`; healthcheck is never parsed. `DeterministicHit` carries no node id (`static_collect.py:18-24`), so the LLM can't anchor edges. `_sanitize` filters edges only by endpoint and never checks the relation (`env_classifier.py:81`), so an invalid relation (e.g. `depends_on`, not a valid `EdgeType` per `schema.py:52-56`) survives sanitize and then voids the whole batch at the all-or-nothing gate (`patch_gate.py:113-116,237-240`).

## Global Constraints

- **`python_deps/depgraph` stays LLM-free and envstate-free.** `static_collect.py`/`service_scan.py`/`config_scan.py` import only `python_deps.depgraph.*` + stdlib. No `src.envstate` import, no LLM.
- **Back-compat:** `collect_static_evidence(repo_path)` with no graph still works; all changes are additive (new optional field defaults None; new evidence appended; existing kinds unchanged).
- **Bundle hits stay deduped:** framework-config vars already covered by an `env_var`/`env_read` hit are NOT re-emitted.
- **`_sanitize` philosophy = drop, never void:** an invalid-relation edge is dropped (like M1 dropped illegal-promotion reqs), never allowed to fail the all-or-nothing admit.
- **Git hygiene:** `git add` ONLY the exact files each task touches — NEVER `git add -A`/`.`/a directory, and NEVER run `git checkout`/`git restore`/`git stash`/`git reset`/`git clean` (the working tree holds unrelated uncommitted WIP that a broad git op would destroy). Conventional commits with an Observation/Why/What/Verification body. **No `Co-Authored-By` trailer.** Do not push.
- Target files (`static_collect.py`, `service_scan.py`, `env_classifier.py`) are currently CLEAN (no WIP) → `git add <file>` is safe.

### Verified integration points

```python
# src/python_deps/depgraph/static_collect.py
@dataclass(frozen=True) DeterministicHit: evidence_id file kind snippet="" name=None    # :18-24
def collect_static_evidence(repo_path, graph=None) -> tuple[DeterministicHit,...]        # :27 ; _add helper :31
def compact_bundle_json(hits, goal=_GOAL) -> str                                          # :59 ; per-hit row :61-66
# src/python_deps/depgraph/config_scan.py
def scan_framework_config_reads(repo_path) -> dict[str,str]   # {VAR: "rel  (BaseSettings field)"}  :176-202
def scan_env_reads(repo_path) -> dict[str,str]                # {VAR: file}
# src/python_deps/depgraph/service_scan.py
def scan_compose_services(repo_path) -> dict[str,dict]        # meta = {"image","port","source"}  (build line :98)
def _services_from_yaml_doc(doc, source, out)                 # builds each meta from the service entry dict
# src/python_deps/depgraph/ids.py: package_id(name,ver)->"pkg:NAME" ; project_id(name)->"project:slug"
# src/python_deps/depgraph/schema.py: NodeType.PACKAGE/PROJECT ; EdgeType {requires,alternative_to,conflicts_with}
# src/envstate/env_classifier.py: _SYSTEM_PROMPT (str) ; _sanitize(proposal,bundle_ids,graph) edge line :81
```

---

### Task 1: `node_id` on the bundle (so the LLM can anchor edges)

**Files:**
- Modify: `src/python_deps/depgraph/static_collect.py`
- Test: `tests/depgraph/test_static_collect_bundle.py` (add)

**Interfaces:**
- Produces: `DeterministicHit.node_id: str | None = None`; `collect_static_evidence(repo, graph)` emits `node_id` on PACKAGE hits (= `node.id`) and emits one hit per PROJECT node (kind `"project"`, `node_id = node.id`); `compact_bundle_json` includes `node_id` when present. Consumed by Task 4 (prompt) + the LLM edge anchoring.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_static_collect_bundle.py (merge imports into the top block)
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy
from python_deps.depgraph.ids import package_id, project_id
from python_deps.depgraph.static_collect import collect_static_evidence, compact_bundle_json
import json as _json


def test_package_hit_carries_node_id(tmp_path):
    g = DepGraph().with_node(Node(id=package_id("psycopg", None), type=NodeType.PACKAGE,
        name="psycopg", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER))
    hits = collect_static_evidence(str(tmp_path), g)
    pkg = [h for h in hits if h.kind == "package"]
    assert pkg and pkg[0].node_id == "pkg:psycopg"
    # and it is serialized into the bundle JSON
    row = next(r for r in _json.loads(compact_bundle_json(hits))["deterministic_hits"]
               if r.get("node_id") == "pkg:psycopg")
    assert row["name"] == "psycopg"


def test_project_node_emitted_with_node_id(tmp_path):
    g = DepGraph().with_node(Node(id=project_id("myrepo"), type=NodeType.PROJECT,
        name="myrepo", layer=Layer.PROJECT, discovered_by=DiscoveredBy.STATIC_SCAN))
    hits = collect_static_evidence(str(tmp_path), g)
    proj = [h for h in hits if h.kind == "project"]
    assert proj and proj[0].node_id == project_id("myrepo")
```

(If `Layer.PROJECT` does not exist, read `schema.py` and use the project tier's actual `Layer` member.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q -k "node_id or project_node"`
Expected: FAIL — `DeterministicHit` has no `node_id` / no `project` hit emitted.

- [ ] **Step 3: Implement**

In `static_collect.py`: add the field, thread `node_id` through `_add`, set it on package hits, and emit project-node hits.

```python
@dataclass(frozen=True)
class DeterministicHit:
    evidence_id: str
    file: str
    kind: str                     # ci_service | compose_service | env_var | env_read | package | project
    snippet: str = ""
    name: str | None = None
    node_id: str | None = None
```

```python
    def _add(file, kind, *, name=None, snippet="", node_id=None):
        nonlocal n
        prefix = {"ci_service": "ci", "compose_service": "svc", "env_var": "env",
                  "env_read": "code", "package": "pkg", "project": "proj"}.get(kind, "ev")
        hits.append(DeterministicHit(f"{prefix}.{n:02d}", file, kind,
                                     snippet=snippet, name=name, node_id=node_id))
        n += 1
```

In the `graph is not None` block, set `node_id` on package hits and add a project loop:

```python
    if graph is not None:
        from python_deps.depgraph.schema import NodeType
        for node in sorted((n_ for n_ in graph.nodes if n_.type is NodeType.PACKAGE),
                           key=lambda x: x.name):
            _add("manifest", "package", name=node.name,
                 snippet=node.version or "", node_id=node.id)
        for node in sorted((n_ for n_ in graph.nodes if n_.type is NodeType.PROJECT),
                           key=lambda x: x.id):
            _add("manifest", "project", name=node.name, node_id=node.id)
```

In `compact_bundle_json`, add `node_id` to the row when present:

```python
        if h.node_id is not None:
            row["node_id"] = h.node_id
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q`
Expected: PASS (new tests green; existing bundle tests unaffected — additive).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/static_collect.py tests/depgraph/test_static_collect_bundle.py
git commit -m "fix(bundle): emit canonical node_id (package + project hits) so the LLM can anchor edges"
```

---

### Task 2: wire `scan_framework_config_reads` into the bundle (BaseSettings config evidence)

**Files:**
- Modify: `src/python_deps/depgraph/static_collect.py` (import + new evidence loop)
- Test: `tests/depgraph/test_static_collect_bundle.py` (add)

**Interfaces:**
- Consumes: `config_scan.scan_framework_config_reads`.
- Produces: `collect_static_evidence` appends `kind="env_var"` hits for pydantic BaseSettings / framework-config vars NOT already covered by an `env_var`/`env_read` hit (deduped by var name). Snippet labels them as a settings field.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_static_collect_bundle.py
def test_basesettings_fields_feed_the_bundle(tmp_path):
    (tmp_path / "config.py").write_text(
        "from pydantic_settings import BaseSettings\n"
        "class Settings(BaseSettings):\n"
        "    POSTGRES_SERVER: str\n"
        "    POSTGRES_PORT: int = 5432\n"
        "    SECRET_KEY: str\n")
    hits = collect_static_evidence(str(tmp_path))
    names = {h.name for h in hits if h.kind == "env_var"}
    assert {"POSTGRES_SERVER", "POSTGRES_PORT", "SECRET_KEY"} <= names


def test_framework_config_deduped_against_env_read(tmp_path):
    # a var seen via os.environ must not be double-emitted by the framework-config source
    (tmp_path / "a.py").write_text("import os\nX = os.environ['SHARED_VAR']\n")
    (tmp_path / "config.py").write_text(
        "from pydantic_settings import BaseSettings\n"
        "class Settings(BaseSettings):\n    SHARED_VAR: str\n")
    hits = collect_static_evidence(str(tmp_path))
    shared = [h for h in hits if h.name == "SHARED_VAR"]
    assert len(shared) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q -k "basesettings or deduped"`
Expected: FAIL — BaseSettings fields absent from the bundle.

- [ ] **Step 3: Implement**

In `static_collect.py`, add the import and a deduped framework-config loop after the existing `env_read` loop (before the `graph is not None` block):

```python
from python_deps.depgraph.config_scan import (
    scan_env_reads, parse_env_example, scan_framework_config_reads,
)
```

```python
    seen_vars = {h.name for h in hits if h.kind in ("env_var", "env_read")}
    for var, src in sorted(scan_framework_config_reads(repo_path).items()):
        if var in seen_vars:
            continue
        seen_vars.add(var)
        _add(str(src), "env_var", name=var, snippet="settings/framework config field")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q`
Expected: PASS — BaseSettings fields present, no duplicate for the shared var; existing tests green.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/static_collect.py tests/depgraph/test_static_collect_bundle.py
git commit -m "fix(bundle): wire scan_framework_config_reads so BaseSettings config reaches the LLM"
```

---

### Task 3: forward compose port + capture healthcheck into the compose hit

**Files:**
- Modify: `src/python_deps/depgraph/service_scan.py` (capture healthcheck in the service meta)
- Modify: `src/python_deps/depgraph/static_collect.py` (richer compose snippet)
- Test: `tests/depgraph/test_service_scan.py` (add) and `tests/depgraph/test_static_collect_bundle.py` (add)

**Interfaces:**
- Produces: `scan_compose_services` meta gains `"healthcheck"` (the compose `healthcheck.test` as a string, or `""`); the bundle's `compose_service` snippet becomes `"<image> port=<port> healthcheck=<hc>"` so the LLM reads the real check instead of guessing.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/depgraph/test_service_scan.py (merge imports)
from python_deps.depgraph.service_scan import scan_compose_services


def test_compose_meta_captures_healthcheck(tmp_path):
    (tmp_path / "compose.yml").write_text(
        "services:\n"
        "  db:\n"
        "    image: postgres:16\n"
        "    ports: ['5432:5432']\n"
        "    healthcheck:\n"
        "      test: ['CMD-SHELL', 'pg_isready -U postgres']\n")
    meta = scan_compose_services(str(tmp_path))
    pg = meta.get("postgres") or next(iter(meta.values()))
    assert "pg_isready" in str(pg.get("healthcheck", ""))
```

```python
# add to tests/depgraph/test_static_collect_bundle.py
def test_compose_snippet_includes_port_and_healthcheck(tmp_path):
    (tmp_path / "compose.yml").write_text(
        "services:\n"
        "  db:\n"
        "    image: postgres:16\n"
        "    ports: ['5432:5432']\n"
        "    healthcheck:\n"
        "      test: ['CMD-SHELL', 'pg_isready -U postgres']\n")
    hits = collect_static_evidence(str(tmp_path))
    svc = next(h for h in hits if h.kind == "compose_service")
    assert "5432" in svc.snippet and "pg_isready" in svc.snippet
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/depgraph/test_service_scan.py tests/depgraph/test_static_collect_bundle.py -q -k "healthcheck or port_and_healthcheck"`
Expected: FAIL — meta has no `healthcheck`; snippet has only the image.

- [ ] **Step 3: Implement**

In `service_scan.py`, add a helper and include healthcheck in the meta (read the existing `_services_from_yaml_doc` and the line that builds `out[kind] = {...}` — add the key there). Read the actual service entry dict for `healthcheck.test`:

```python
def _healthcheck_of(entry) -> str:
    if not isinstance(entry, dict):
        return ""
    hc = entry.get("healthcheck")
    if isinstance(hc, dict):
        test = hc.get("test")
        if isinstance(test, (list, tuple)):
            return " ".join(str(t) for t in test)
        if test:
            return str(test)
    return ""
```

and in the meta-building line add `"healthcheck": _healthcheck_of(entry)` (use the entry variable that line already has in scope).

In `static_collect.py`, build the richer compose snippet:

```python
    for svc, meta in sorted(scan_compose_services(repo_path).items()):
        _snip = str(meta.get("image", svc))
        if meta.get("port"):
            _snip += f" port={meta['port']}"
        if meta.get("healthcheck"):
            _snip += f" healthcheck={meta['healthcheck']}"
        _add("compose.yml", "compose_service", name=svc, snippet=_snip)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_service_scan.py tests/depgraph/test_static_collect_bundle.py -q`
Expected: PASS — healthcheck captured, snippet carries port + healthcheck; existing compose/parser tests green.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/service_scan.py src/python_deps/depgraph/static_collect.py tests/depgraph/test_service_scan.py tests/depgraph/test_static_collect_bundle.py
git commit -m "fix(bundle): capture compose healthcheck + forward port/healthcheck into the service hit"
```

---

### Task 4: prompt hook for node-id edge anchoring + `_sanitize` drops invalid-relation edges

**Files:**
- Modify: `src/envstate/env_classifier.py` (`_SYSTEM_PROMPT`, `_sanitize`)
- Test: `tests/test_env_classifier.py` (add)

**Interfaces:**
- Produces: `_SYSTEM_PROMPT` instructs the LLM to anchor edges on the bundle's `node_id` and to use only valid relations. `_sanitize` keeps an edge ONLY if both endpoints exist AND the relation is a valid `EdgeType` — an invalid relation drops that edge (does not void the batch). Still forces `hard=False`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_env_classifier.py
import json
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy
from python_deps.depgraph.ids import package_id
from src.envstate.env_classifier import _SYSTEM_PROMPT, make_construction_classifier


def _graph_with_pkg():
    return DepGraph().with_node(Node(id=package_id("psycopg", "3.1"), type=NodeType.PACKAGE,
        name="psycopg", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="3.1"))


def test_prompt_mentions_node_id_anchoring():
    assert "node_id" in _SYSTEM_PROMPT


def test_invalid_relation_edge_dropped_not_voiding():
    # an edge with an invalid relation must be dropped, leaving the valid node admitted
    g = _graph_with_pkg()
    llm = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "candidate", "evidence_refs": ["pkg.00"]}],
        "add_edges": [{"source": package_id("psycopg", "3.1"), "target": "service:postgres",
                       "relation": "depends_on", "hard": True}]})   # bad relation
    out = make_construction_classifier(lambda m: llm)(g, "/nonexistent-repo")
    assert out.get("service:postgres") is not None         # node still admitted (batch not voided)
    assert all(e.relation.value == "requires" or e.dst != "service:postgres"
               for e in out.edges) or not any(e.dst == "service:postgres" for e in out.edges)


def test_valid_relation_edge_survives_soft():
    g = _graph_with_pkg()
    llm = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "candidate", "evidence_refs": ["pkg.00"]}],
        "add_edges": [{"source": package_id("psycopg", "3.1"), "target": "service:postgres",
                       "relation": "requires", "hard": True}]})
    out = make_construction_classifier(lambda m: llm)(g, "/nonexistent-repo")
    e = next(e for e in out.edges if e.dst == "service:postgres")
    assert e.relation.value == "requires" and e.data.get("hard") is False
```

(The `pkg.00` evidence id assumes the seeded psycopg package is the only graph node → its package hit is `pkg.00`. Confirm by reading the bundle if the test is flaky.)

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_env_classifier.py -q -k "node_id or invalid_relation or valid_relation"`
Expected: FAIL — prompt lacks `node_id`; the `depends_on` edge currently survives sanitize and voids the batch (so `service:postgres` is absent).

- [ ] **Step 3: Implement**

In `env_classifier.py`, extend `_SYSTEM_PROMPT` (append to the existing string) with an explicit edge-anchoring instruction, e.g.:

```python
_SYSTEM_PROMPT = (
    ...existing text...
    " Some bundle hits include a \"node_id\" (e.g. \"pkg:psycopg\", \"project:foo\"). To link a "
    "new node to an existing one, add an edge whose source/target are those exact node_id values. "
    "Valid edge relations are ONLY: requires, alternative_to, conflicts_with (default requires). "
    "Do NOT invent other relations, and do NOT create a node per package."
)
```

In `_sanitize`, add a valid-relation filter to the edge comprehension (import the valid set):

```python
    from python_deps.depgraph.schema import EdgeType
    _valid_relations = {e.value for e in EdgeType}
    good_edges = tuple(replace(e, hard=False) for e in proposal.add_edges
                       if e.source in known and e.target in known
                       and e.relation in _valid_relations)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_env_classifier.py -q`
Expected: PASS — prompt mentions node_id; invalid-relation edge dropped (node still admitted); valid-relation edge survives soft.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/env_classifier.py tests/test_env_classifier.py
git commit -m "fix(env-classifier): prompt edge-anchoring on node_id + drop invalid-relation edges (no batch void)"
```

---

### Task 5 (controller, no commit): re-run the real-LLM harness on fsfastapi

Re-run `scratchpad/clf_construct.py` (after seeding a project node + with the enriched bundle) on the cloned full-stack-fastapi-template with a real OpenRouter model, and report the before/after: does config evidence now appear, does the LLM emit real config nodes (POSTGRES_*/SECRET_KEY/SMTP_*) instead of per-package junk, does a SOFT `pkg:psycopg → service:postgres` edge now form, and is the service check read (not guessed)? This isolates how much of the original failure was starvation vs. prompt. Record the result; do not gate CI on it.

## Done-definition

- The bundle carries: BaseSettings/framework config vars (deduped), compose port + healthcheck, and canonical `node_id`s on package/project hits.
- `_sanitize` drops invalid-relation edges instead of letting them void the admit batch; the prompt tells the LLM to anchor edges on `node_id` with valid relations.
- depgraph stays LLM-free; all changes additive/back-compatible; full suite green except the 4 known pre-existing failures.
