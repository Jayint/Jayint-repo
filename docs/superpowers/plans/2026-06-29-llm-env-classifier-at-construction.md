# LLM Environment Classifier at Construction (Slice C) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the deterministic Config/Service node-creators with an LLM semantic classifier: the deterministic file parsers feed a compact evidence bundle, the LLM classifies it into Hint/Candidate Service/Config/DataAsset nodes with all-soft edges, applied through the existing `patch_gate`.

**Architecture:** A new envstate module `env_classifier.py` builds a classifier callback (`graph, repo_path → graph`) that does `collect_static_evidence → compact_bundle_json → complete_fn → normalize → parse_patch_proposal → sanitize → admit_proposal`. The callback is **injected into `build_advisory_for_repo` (advise.py)** between `build_dep_graph` and `render_dep_graph_advisory`, so both the scheduler graph and the advisory include the LLM nodes. `agent.py` builds the callback from `self.client` and passes it; `python_deps/depgraph` stays LLM-free (it only invokes an opaque `Callable`). The deterministic node-creators (`scan_config`/`scan_services`/Stages 3c/3d/3e) are then deleted; the file *parsers* are kept as evidence feeders.

**Tech Stack:** Python 3, `pytest`, the `python_deps/depgraph` engine + `src/envstate` orchestration + an OpenAI-compatible client.

**Source design:** `docs/superpowers/specs/2026-06-29-llm-env-classifier-at-construction-design.md` (the spec says "run_v3 phase"; this plan refines placement to `build_advisory_for_repo` — the seam where build+render already co-occur and `repo_path` exists, so the advisory isn't left stale).

## Global Constraints

- **`python_deps/depgraph` stays LLM-free and envstate-free.** The LLM call lives only in `src/envstate/env_classifier.py`. `advise.py` gains a `classify: Callable | None` param it merely *invokes* — it must NOT import `src.envstate`. `env_classifier.py` may import `python_deps.depgraph.*` (envstate→depgraph is allowed) and `src.envstate.{llm_response,jsonutil}`.
- **All construction edges are soft.** The classifier forces `hard=False` on every emitted edge regardless of what the LLM returns. Active (hard) promotion stays the EXISTING runtime/gate path — not in scope.
- **Trust boundary.** The LLM emits a `PatchProposal`; the gate (`validate_proposal`/`admit_proposal`) never writes `SATISFIED`, validates `promotion ∈ {hint,candidate}`, validates `evidence_ref ∈ known_evidence_ids` (the bundle ids), and validates id-prefix/type/edge legality. A hallucinated or ungrounded entry is dropped by sanitize or rejected by the gate.
- **Default behavior / blast radius (accepted, spec §5):** the classifier runs when `enable_dep_graph` is on AND `self.client` is present AND not disabled — i.e. wherever the deleted scanners used to run. So every dep-graph arm with a client now performs ONE LLM call at construction, and off-state is **no longer byte-identical** (no deterministic fallback; no client → Config/Service/DataAsset tiers absent). A `--disable-llm-env-classifier` escape exists.
- **Never crash the build.** The classifier is best-effort: any LLM/parse/JSON failure returns the input graph unchanged (mirrors `build_advisory_for_repo`'s existing `except Exception` wrapper).
- **Git hygiene:** `git add` only the exact files each task creates/modifies — NEVER `git add -A`/`.`/`<dir>` (the repo has unrelated untracked WIP). Conventional commits with an Observation/Why/What/Verification body. **No `Co-Authored-By` trailer.** Do not push.

### Verified integration points (grounded 2026-06-29)

```python
# src/python_deps/depgraph/patch.py
@dataclass(frozen=True) NodeSpec: id type name layer check_command=None evidence_ref=None promotion=None
@dataclass(frozen=True) EdgeSpec: source target relation="requires" hard=True
def parse_patch_proposal(d: dict) -> PatchProposal     # reads d.get("patch", d).get("add_requirements"/"add_edges");
                                                       # promotion falls back to r.get("state"); raises PatchParseError
class PatchParseError(ValueError)
# src/python_deps/depgraph/patch_gate.py
_KIND_PREFIX: dict[NodeType,str] = {PACKAGE:"pkg:", SYSTEM_LIB:"syslib:", TOOL:"tool:", CONFIG:"config:",
                                    SERVICE:"service:", RUNTIME:"runtime:", IMPORT:"import:", PROJECT:"project:"}  # :22
def admit_proposal(graph, proposal, *, manual_blocks=(), known_evidence_ids: frozenset[str]) -> AdmitResult  # :214
#   AdmitResult(accepted: bool, errors: tuple, graph: DepGraph, blocks, manual_blocks)  — all-or-nothing
# src/python_deps/depgraph/static_collect.py
@dataclass(frozen=True) DeterministicHit: evidence_id file kind snippet="" name=None
def collect_static_evidence(repo_path: str) -> tuple[DeterministicHit, ...]   # :27  (parsers only today)
def compact_bundle_json(hits, goal=_GOAL) -> str                              # :52
# src/python_deps/depgraph/ids.py
def config_id(name)->"config:{name}"  def service_id(name)->"service:{name}"   # model data_asset_id after these
# src/python_deps/depgraph/schema.py
NodeType.DATA_ASSET="DataAsset" (tier 6); Layer.CONFIG="config" (no Layer.DATA — DataAsset reuses Layer.CONFIG)
# src/envstate/llm_response.py
def complete_with_retry(client, model, messages, accept=None, max_attempts=3, ...) -> (text, usage, resp)   # :159
# src/envstate/jsonutil.py: extract_json_object(text) -> dict | None
# src/python_deps/depgraph/advise.py  build_advisory_for_repo(repo_path, base_image, *, host_executor=None,
#   target_python=None, enable_service_provision=False) -> (advisory_str, DepGraph|None)   # :303
#   body: with DockerExecutor(base_image) as scratch: graph = build_dep_graph(...);  return render_dep_graph_advisory(graph), graph
# src/python_deps/depgraph/build.py  build_dep_graph(...): Stages 3c/3d/3e at :297-310 (scan_config/scan_services/
#   attach_in_image_provisioning); param enable_service_provision (:216); imports :63-64
# src/envstate ... agent.py: self.client/self.model from __init__ (:434); build_advisory_for_repo called :1160;
#   flag cascade :334-360; argparse :3422-3430; DockerAgent ctor :3476
```

---

### Task 1: DataAsset support in patch_gate + ids

**Files:**
- Modify: `src/python_deps/depgraph/patch_gate.py` (`_KIND_PREFIX` `:22`)
- Modify: `src/python_deps/depgraph/ids.py`
- Test: `tests/depgraph/test_patch_gate.py` (add) and `tests/depgraph/test_ids.py` (add, or create if absent)

**Interfaces:**
- Produces: `ids.data_asset_id(name) -> "data:{name}"`; `NodeType.DATA_ASSET` admitted by `patch_gate` with the `data:` id prefix. Consumed by Tasks 3/5 + the LLM contract.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/depgraph/test_patch_gate.py  (merge imports into the top block)
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from python_deps.depgraph.schema import DepGraph, NodeType
from python_deps.depgraph.ids import data_asset_id
from python_deps.depgraph.patch import PatchProposal, NodeSpec
from python_deps.depgraph.patch_gate import admit_proposal


def test_data_asset_id_prefix():
    assert data_asset_id("fixtures.db") == "data:fixtures.db"


def test_patch_gate_admits_data_asset_node():
    ev = frozenset({"env.00"})
    prop = PatchProposal(add_requirements=(NodeSpec(
        id="data:fixtures.db", type="DataAsset", name="fixtures.db", layer="config",
        check_command="test -f fixtures.db", evidence_ref="env.00", promotion="hint"),))
    res = admit_proposal(DepGraph(), prop, known_evidence_ids=ev)
    assert res.accepted, res.errors
    node = res.graph.get("data:fixtures.db")
    assert node is not None and node.type is NodeType.DATA_ASSET
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/depgraph/test_patch_gate.py::test_patch_gate_admits_data_asset_node tests/depgraph/test_patch_gate.py::test_data_asset_id_prefix -q`
Expected: FAIL — `ImportError: cannot import name 'data_asset_id'` (and, once that's added, the admit test fails because `DataAsset` id prefix isn't in `_KIND_PREFIX`, so `validate_proposal` rejects `data:` for type `DataAsset`).

- [ ] **Step 3: Implement**

In `src/python_deps/depgraph/ids.py`, add after `service_id`:

```python
def data_asset_id(name: str) -> str:
    return f"data:{name}"
```

In `src/python_deps/depgraph/patch_gate.py`, add the `DATA_ASSET` entry to `_KIND_PREFIX`:

```python
_KIND_PREFIX: dict[NodeType, str] = {
    NodeType.PACKAGE: "pkg:", NodeType.SYSTEM_LIB: "syslib:", NodeType.TOOL: "tool:",
    NodeType.CONFIG: "config:", NodeType.SERVICE: "service:", NodeType.RUNTIME: "runtime:",
    NodeType.IMPORT: "import:", NodeType.PROJECT: "project:", NodeType.DATA_ASSET: "data:",
}
```

(DataAsset reuses `Layer.CONFIG` = `"config"`, the existing tier-6 layer — no `Layer` enum change, so the wave/certify ordering maps are untouched.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_patch_gate.py tests/depgraph/test_ids.py -q`
Expected: PASS (new tests green; existing patch_gate tests unaffected — additive map entry).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/patch_gate.py src/python_deps/depgraph/ids.py tests/depgraph/test_patch_gate.py tests/depgraph/test_ids.py
git commit -m "feat(env-classifier): DataAsset support in patch_gate (_KIND_PREFIX) + ids.data_asset_id"
```

---

### Task 2: `package` evidence hit in `collect_static_evidence`

**Files:**
- Modify: `src/python_deps/depgraph/static_collect.py` (`collect_static_evidence` `:27`)
- Test: `tests/depgraph/test_static_collect_bundle.py` (add)

**Interfaces:**
- Consumes: nothing new.
- Produces: `collect_static_evidence(repo_path, graph=None)` — now accepts an optional `graph`; when given, appends `kind="package"` hits (one per `NodeType.PACKAGE` node) so the LLM can do dep-induced service inference (`psycopg2 → postgres`). Back-compat: `graph=None` → identical to today. Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_static_collect_bundle.py  (merge imports into the top block)
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy
from python_deps.depgraph.ids import package_id
from python_deps.depgraph.static_collect import collect_static_evidence


def test_package_hits_added_when_graph_given(tmp_path):
    g = DepGraph().with_node(Node(id=package_id("psycopg2", "2.9.9"), type=NodeType.PACKAGE,
        name="psycopg2", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="2.9.9"))
    hits = collect_static_evidence(str(tmp_path), g)
    pkg = [h for h in hits if h.kind == "package"]
    assert any(h.name == "psycopg2" for h in pkg)
    assert all(h.evidence_id.startswith("pkg.") for h in pkg)


def test_no_package_hits_without_graph(tmp_path):
    # back-compat: existing call sites pass no graph -> no package hits, no crash
    hits = collect_static_evidence(str(tmp_path))
    assert all(h.kind != "package" for h in hits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q -k package`
Expected: FAIL — `TypeError: collect_static_evidence() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Implement**

In `src/python_deps/depgraph/static_collect.py`, change the signature and append package hits. The `_add` helper's prefix map gains `"package": "pkg"`:

```python
def collect_static_evidence(repo_path: str, graph=None) -> tuple[DeterministicHit, ...]:
    hits: list[DeterministicHit] = []
    n = 0

    def _add(file, kind, *, name=None, snippet=""):
        nonlocal n
        prefix = {"ci_service": "ci", "compose_service": "svc",
                  "env_var": "env", "env_read": "code", "package": "pkg"}.get(kind, "ev")
        hits.append(DeterministicHit(f"{prefix}.{n:02d}", file, kind,
                                     snippet=snippet, name=name))
        n += 1

    ci_services, _has_ci = scan_ci_services(repo_path)
    for svc, meta in sorted(ci_services.items()):
        _add(".github/workflows", "ci_service", name=svc, snippet=str(meta.get("image", svc)))
    for svc, meta in sorted(scan_compose_services(repo_path).items()):
        _add("docker-compose.yml", "compose_service", name=svc, snippet=str(meta.get("image", svc)))
    for var, default in sorted(parse_env_example(repo_path).items()):
        _add(".env.example", "env_var", name=var, snippet=str(default))
    for var, file in sorted(scan_env_reads(repo_path).items()):
        _add(file, "env_read", name=var)
    if graph is not None:
        from python_deps.depgraph.schema import NodeType
        for node in sorted((n_ for n_ in graph.nodes if n_.type is NodeType.PACKAGE),
                           key=lambda x: x.name):
            _add("manifest", "package", name=node.name,
                 snippet=node.version or "")
    return tuple(hits)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q`
Expected: PASS — package hits present with graph, absent without; existing bundle tests still green (the four parser kinds unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/static_collect.py tests/depgraph/test_static_collect_bundle.py
git commit -m "feat(env-classifier): collect_static_evidence emits package hits when a graph is supplied"
```

---

### Task 3: `env_classifier.py` — the LLM classifier callback

**Files:**
- Create: `src/envstate/env_classifier.py`
- Test: `tests/test_env_classifier.py`

**Interfaces:**
- Consumes: `collect_static_evidence`/`compact_bundle_json` (Task 2), `parse_patch_proposal`/`PatchParseError` (patch.py), `admit_proposal` (patch_gate.py), `extract_json_object` (jsonutil), DataAsset support (Task 1).
- Produces: `make_construction_classifier(complete_fn) -> Callable[[DepGraph, str], DepGraph]`. The returned `classify(graph, repo_path)` runs the bundle→LLM→PatchProposal→sanitize→admit pipeline and returns the enriched graph (or the input graph on any failure / empty result). Consumed by Task 4.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_env_classifier.py
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import json
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
from python_deps.depgraph.ids import package_id
from src.envstate.env_classifier import make_construction_classifier, _normalize


def _graph_with_pkg():
    return DepGraph().with_node(Node(id=package_id("psycopg2", "2.9.9"), type=NodeType.PACKAGE,
        name="psycopg2", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="2.9.9"))


def test_normalize_maps_recalled_shape():
    out = _normalize({"requirements": [
        {"id": "service:postgres", "type": "Service", "layer": "services",
         "state": "HINT", "check_command": None, "evidence_refs": ["pkg.00"]}]})
    req = out["patch"]["add_requirements"][0]
    assert req["promotion"] == "hint"            # state HINT -> promotion hint (lowercased)
    assert req["evidence_ref"] == "pkg.00"       # evidence_refs[0] -> evidence_ref


def test_classifier_appends_soft_service_node():
    g = _graph_with_pkg()
    # the bundle for this graph contains a package hit "pkg.00" (psycopg2)
    llm_json = json.dumps({"requirements": [
        {"id": "service:postgres", "type": "Service", "name": "postgres", "layer": "services",
         "state": "candidate", "check_command": None, "evidence_refs": ["pkg.00"],
         "rationale": "psycopg2 implies postgres"}],
        "add_edges": [{"source": package_id("psycopg2", "2.9.9"), "target": "service:postgres",
                       "relation": "requires", "hard": True}]})   # LLM says hard; classifier forces soft
    classify = make_construction_classifier(lambda messages: llm_json)
    out = classify(g, "/nonexistent-repo")
    svc = out.get("service:postgres")
    assert svc is not None and svc.type is NodeType.SERVICE and svc.state is State.MISSING
    assert svc.data.get("promotion") == "candidate"
    # the edge is SOFT despite the LLM asking for hard
    edge = next(e for e in out.edges if e.src == package_id("psycopg2", "2.9.9")
                and e.dst == "service:postgres")
    assert edge.data.get("hard") is False


def test_classifier_drops_ungrounded_requirement():
    g = _graph_with_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "service:redis", "type": "Service", "name": "redis", "layer": "services",
         "state": "hint", "evidence_refs": ["does.not.exist"]}]})   # ungrounded -> dropped
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    assert out.get("service:redis") is None


def test_classifier_returns_graph_unchanged_on_junk():
    g = _graph_with_pkg()
    out = make_construction_classifier(lambda m: "not json")(g, "/nonexistent-repo")
    assert out is g                                   # best-effort: junk -> unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_env_classifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.envstate.env_classifier'`.

- [ ] **Step 3: Implement**

```python
# src/envstate/env_classifier.py
"""Construction-time LLM environment classifier (design 2026-06-29, Slice C). The allowed
LLM bridge: python_deps/depgraph stays LLM-free; this envstate module calls the model and
feeds the result through the pure patch_gate. Best-effort: never raises into the build."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

logger = logging.getLogger(__name__)

_GOAL = ("Infer LOCAL install/test/run environment requirements (not deployment). For each "
         "need cite >=1 evidence_id from the bundle. Deployment-only / release / secret-store / "
         "cache / optional-matrix signals -> promotion 'hint' only. Every requirement needs a real "
         "check_command or null (a hint).")
_SYSTEM_PROMPT = (
    "You classify a compact evidence bundle into environment obligations for running a repo's "
    "tests locally. Output ONLY a JSON object: {\"add_requirements\":[{id,type,name,layer,"
    "check_command,promotion,evidence_ref}], \"add_edges\":[{source,target,relation,hard}]}.\n"
    "type in {Service,Config,DataAsset}; id is 'service:<name>' / 'config:<VAR>' / 'data:<name>'; "
    "layer in {services,config}; promotion in {hint,candidate} (NEVER active); evidence_ref MUST be "
    "an evidence_id from the bundle. Edges connect an existing node (e.g. a pkg: or project: id from "
    "the bundle) to your new node. " + _GOAL
)


def _normalize(d: dict) -> dict:
    """Map the recalled output shape onto what parse_patch_proposal expects:
    requirements->add_requirements, state->promotion (lowercased), evidence_refs->evidence_ref."""
    if not isinstance(d, dict):
        return {}
    patch = dict(d.get("patch", d))
    if "requirements" in patch and "add_requirements" not in patch:
        patch["add_requirements"] = patch.get("requirements")
    norm_reqs = []
    for r in (patch.get("add_requirements") or []):
        if not isinstance(r, dict):
            continue
        r = dict(r)
        prom = r.get("promotion") or r.get("state")
        if isinstance(prom, str):
            r["promotion"] = prom.strip().lower()
        if "evidence_ref" not in r:
            refs = r.get("evidence_refs")
            if isinstance(refs, (list, tuple)) and refs:
                r["evidence_ref"] = refs[0]
        norm_reqs.append(r)
    patch["add_requirements"] = norm_reqs
    return {"patch": patch}


def _sanitize(proposal, bundle_ids, graph):
    """Drop ungrounded/illegal requirements; force ALL edges soft; keep only edges whose
    endpoints exist (after the kept new nodes are accounted for)."""
    from python_deps.depgraph.patch import PatchProposal
    from python_deps.depgraph.patch_gate import _KIND_PREFIX
    from python_deps.depgraph.schema import NodeType

    def _ok(r):
        if r.evidence_ref not in bundle_ids:
            return False
        try:
            nt = NodeType(r.type)
        except ValueError:
            return False
        prefix = _KIND_PREFIX.get(nt)
        return bool(prefix) and isinstance(r.id, str) and r.id.startswith(prefix)

    good_reqs = tuple(r for r in proposal.add_requirements if _ok(r))
    known = {r.id for r in good_reqs} | {n.id for n in graph.nodes}
    good_edges = tuple(replace(e, hard=False) for e in proposal.add_edges
                       if e.source in known and e.target in known)
    return PatchProposal(add_requirements=good_reqs, add_edges=good_edges)


def make_construction_classifier(complete_fn: Callable[[list[dict]], str]):
    """Return classify(graph, repo_path) -> graph. complete_fn(messages)->text (temp-0, JSON)."""
    def classify(graph, repo_path: str):
        try:
            from python_deps.depgraph.static_collect import (
                collect_static_evidence, compact_bundle_json)
            from python_deps.depgraph.patch import parse_patch_proposal
            from python_deps.depgraph.patch_gate import admit_proposal
            from src.envstate.jsonutil import extract_json_object

            hits = collect_static_evidence(repo_path, graph)
            if not hits:
                return graph
            bundle_ids = frozenset(h.evidence_id for h in hits)
            messages = [{"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": compact_bundle_json(hits, _GOAL)}]
            obj = extract_json_object(complete_fn(messages))
            if obj is None:
                return graph
            proposal = _sanitize(parse_patch_proposal(_normalize(obj)), bundle_ids, graph)
            if proposal.is_empty():
                return graph
            result = admit_proposal(graph, proposal, known_evidence_ids=bundle_ids)
            if not result.accepted:
                logger.warning("env classifier proposal rejected: %s", result.errors)
                return graph
            return result.graph
        except Exception as exc:                       # best-effort: never crash the build
            logger.warning("env classifier skipped: %s", exc)
            return graph
    return classify
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_env_classifier.py -q`
Expected: PASS (4 tests). Note `test_classifier_appends_soft_service_node` proves the LLM's `hard:true` edge is forced to `hard:false`.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/env_classifier.py tests/test_env_classifier.py
git commit -m "feat(env-classifier): env_classifier module (bundle->LLM->normalize->sanitize->admit, all-soft)"
```

---

### Task 4: inject the classifier into `build_advisory_for_repo` + wire `agent.py`

**Files:**
- Modify: `src/python_deps/depgraph/advise.py` (`build_advisory_for_repo` `:303`)
- Modify: `agent.py` (flag cascade `:334-360`, `build_advisory_for_repo` call `:1160`, argparse `:3422-3430`, DockerAgent ctor + `__init__` param)
- Test: `tests/depgraph/test_advise_classify_hook.py` (new), `tests/test_env_classifier_wiring.py` (new, source-inspection of agent.py)

**Interfaces:**
- Consumes: `make_construction_classifier` (Task 3).
- Produces: `build_advisory_for_repo(..., classify: Callable | None = None)` — when given, `graph = classify(graph, repo_path)` runs between `build_dep_graph` and `render_dep_graph_advisory`. `agent.py` builds the callback from `self.client`/`self.model` under `enable_llm_env_classifier` + client-present, and passes it. The deterministic scanners still run here (deleted in Task 5) — additive for this task.

- [ ] **Step 1: Write the failing tests**

```python
# tests/depgraph/test_advise_classify_hook.py
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import python_deps.depgraph.advise as advise
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State


def test_classify_hook_invoked_and_graph_returned(monkeypatch):
    base = DepGraph()

    class _FakeScratch:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(advise, "DockerExecutor", lambda *a, **k: _FakeScratch())
    monkeypatch.setattr(advise, "build_dep_graph", lambda *a, **k: base)
    monkeypatch.setattr(advise, "render_dep_graph_advisory", lambda g: "ADV")

    tag = Node(id="service:tagged", type=NodeType.SERVICE, name="tagged", layer=Layer.SERVICES,
               discovered_by=DiscoveredBy.RUNTIME, state=State.MISSING)
    def _classify(graph, repo_path):
        return graph.with_node(tag)

    adv, graph = advise.build_advisory_for_repo("/repo", "python:3.11-slim", classify=_classify)
    assert adv == "ADV"
    assert graph.get("service:tagged") is not None       # classify ran on the built graph


def test_classify_none_is_passthrough(monkeypatch):
    base = DepGraph()
    class _FakeScratch:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(advise, "DockerExecutor", lambda *a, **k: _FakeScratch())
    monkeypatch.setattr(advise, "build_dep_graph", lambda *a, **k: base)
    monkeypatch.setattr(advise, "render_dep_graph_advisory", lambda g: "ADV")
    adv, graph = advise.build_advisory_for_repo("/repo", "python:3.11-slim")   # classify defaults None
    assert graph is base                                  # unchanged
```

```python
# tests/test_env_classifier_wiring.py — source-inspection (DockerAgent is too heavy to instantiate)
import inspect, re
import agent as agent_mod


def test_agent_builds_classifier_under_flag_and_client():
    src = inspect.getsource(agent_mod.DockerAgent)
    assert "make_construction_classifier" in src
    # gated on the flag AND a client; passed into build_advisory_for_repo as classify=
    assert "enable_llm_env_classifier" in src
    assert re.search(r"classify\s*=", src)


def test_disable_flag_exists():
    src = inspect.getsource(agent_mod)
    assert "--disable-llm-env-classifier" in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/depgraph/test_advise_classify_hook.py tests/test_env_classifier_wiring.py -q`
Expected: FAIL — `build_advisory_for_repo() got an unexpected keyword argument 'classify'`; and the agent source assertions fail (no `make_construction_classifier`/flag yet).

- [ ] **Step 3: Implement**

**(a) `src/python_deps/depgraph/advise.py`** — add the param + the hook. Change the signature of `build_advisory_for_repo` to add `classify: Callable | None = None` (add `from collections.abc import Callable` at top if absent), and insert the hook after the `with` block:

```python
        with DockerExecutor(base_image) as scratch:
            graph = build_dep_graph(
                repo_path, scratch, host_executor=host, target_python=target_python,
                enable_service_provision=enable_service_provision,
            )
        if classify is not None:
            graph = classify(graph, repo_path)        # LLM env classifier (envstate-injected; pure call here)
        return render_dep_graph_advisory(graph), graph
```

**(b) `agent.py`** — four edits:

1. `__init__` param (in the flag group ~`:277-281`): add `enable_llm_env_classifier=True,`.
2. Flag cascade (~`:334-360`): add
```python
        # Construction-time LLM env classifier (Slice C). Runs wherever the dep graph is
        # built and a client exists, unless explicitly disabled. Replaces the deleted
        # deterministic scan_config/scan_services. No deterministic fallback (spec §5).
        self.enable_llm_env_classifier: bool = bool(enable_llm_env_classifier)
```
3. The `build_advisory_for_repo` call (~`:1160`): build the callback and pass it:
```python
                _classify = None
                if getattr(self, "enable_llm_env_classifier", False) and getattr(self, "client", None) is not None:
                    from src.envstate.env_classifier import make_construction_classifier
                    from src.envstate.llm_response import complete_with_retry
                    from src.envstate.jsonutil import extract_json_object

                    def _env_clf_complete(messages):
                        text, _u, _r = complete_with_retry(
                            self.client, self.model, messages,
                            accept=lambda t: extract_json_object(t) is not None,
                            temperature=0, max_attempts=2,
                        )
                        return text

                    _classify = make_construction_classifier(_env_clf_complete)
                _dep_advisory, _dep_graph = build_advisory_for_repo(
                    self.workplace, _base_image, target_python=_req_minor,
                    enable_service_provision=os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1",
                    classify=_classify,
                )
```
(Adapt to the exact existing call — only ADD the `classify=_classify` kwarg and the preceding block; keep the other args verbatim.)
4. argparse (~`:3422-3430`) + DockerAgent ctor (~`:3476`):
```python
    parser.add_argument("--disable-llm-env-classifier", action="store_true",
                        help="Disable the construction-time LLM Config/Service/DataAsset classifier "
                             "(default on when a dep-graph arm runs with an LLM client).")
```
and in the ctor kwargs:
```python
        enable_llm_env_classifier=not args.disable_llm_env_classifier,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_advise_classify_hook.py tests/test_env_classifier_wiring.py -q`
Expected: PASS. Then a focused regression: `python3 -m pytest tests/depgraph/test_advise.py tests/depgraph/test_advise_planner_packet.py -q` — existing advise tests unaffected (the new param defaults None).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/advise.py agent.py tests/depgraph/test_advise_classify_hook.py tests/test_env_classifier_wiring.py
git commit -m "feat(env-classifier): inject classifier into build_advisory_for_repo + agent wiring (flag, complete_fn)"
```

---

### Task 5: hard-delete the deterministic node-creators + refactor to parser-only

**Files:**
- Modify: `src/python_deps/depgraph/build.py` (delete Stages 3c/3d/3e `:297-310`, imports `:63-64`, param `:216`)
- Modify: `src/python_deps/depgraph/config_scan.py` (delete `scan_config`, `_config_node`)
- Modify: `src/python_deps/depgraph/service_scan.py` (delete `scan_services`, `_service_node`, `attach_in_image_provisioning`, `postgres_start_recipe`, `scan_env_bindings`; KEEP parsers `scan_ci_services`/`scan_compose_services`/`service_from_url`/`_services_from_yaml_doc`/`classify_service_error`/`service_db_from_url`/`service_bind_url`)
- Modify/Delete: `tests/depgraph/test_config_scan.py` (delete the `scan_config` block, keep parser tests), `tests/depgraph/test_service_scan.py` (delete `scan_services` block, keep parser tests), `tests/depgraph/test_service_binding.py` (delete), `tests/depgraph/test_build.py` (delete `test_build_includes_config_nodes`/`test_build_includes_service_nodes`/`test_build_attaches_provisioning_when_enabled`), `tests/test_service_provision_off_state.py` (delete)

**Interfaces:**
- Consumes: nothing (deletion). After this task, Config/Service/DataAsset nodes come ONLY from the Task-4 classifier.
- Produces: `build_dep_graph` no longer emits Config/Service nodes and no longer takes `enable_service_provision`; `config_scan`/`service_scan` are parser-only modules (still exporting the parsers `static_collect` + `runtime_classify` import).

- [ ] **Step 1: Establish the baseline (RED is "old tests still assert deleted behavior")**

Run: `python3 -m pytest tests/depgraph/test_build.py -q -k "config or service or provision"`
Expected: currently PASS (they assert the deterministic nodes). After deletion they must be REMOVED (not left failing). This step records what exists before deleting.

- [ ] **Step 2: Delete the build stages + scanners**

In `src/python_deps/depgraph/build.py`:
- Delete imports `:63-64` (`from ...config_scan import scan_config`; `from ...service_scan import scan_services, attach_in_image_provisioning`).
- Delete the `enable_service_provision: bool = False,` param `:216`.
- Delete the Stage 3c/3d/3e block `:297-310` (the three `graph = scan_config(...)` / `graph = scan_services(...)` / `graph = attach_in_image_provisioning(...)` assignments and their comments). KEEP `:311-312` (`resolver_ids = ...` / `_restamp`).

In `src/python_deps/depgraph/config_scan.py`: delete `scan_config` and `_config_node` (and the now-unused imports they alone used — e.g. `config_obligations_for_package`, `Node`/`Edge`/`EdgeType`/`State` if no longer referenced; keep whatever the parsers use). KEEP `scan_env_reads`, `scan_env_defaults`, `scan_framework_config_reads`, `parse_env_example`, `configured_vars`.

In `src/python_deps/depgraph/service_scan.py`: delete `_service_node`, `scan_services`, `attach_in_image_provisioning`, `postgres_start_recipe`, `scan_env_bindings` (and now-unused imports). KEEP `service_from_url`, `_services_from_yaml_doc`, `scan_compose_services`, `scan_ci_services`, `classify_service_error`, `service_db_from_url`, `service_bind_url`.

Also remove the `enable_service_provision=` argument from the `build_dep_graph` call in `advise.py` (it was passed at the call site; now the param is gone) — update `build_advisory_for_repo` to drop `enable_service_provision` from both its own signature and the `build_dep_graph` call, and drop the `enable_service_provision=` kwarg at the agent.py call site (Task 4 added `classify=`; this removes the now-invalid kwarg).

- [ ] **Step 3: Delete/trim the broken tests**

- `tests/depgraph/test_config_scan.py`: delete the `scan_config` import and every test from the `scan_config` block (the node-creator tests at `:84+`). KEEP the parser tests (`scan_env_reads`/`parse_env_example`/`scan_env_defaults`/`scan_framework_config_reads`/`configured_vars`).
- `tests/depgraph/test_service_scan.py`: delete the `scan_services` import and its tests. KEEP the parser tests (`scan_ci_services`/`scan_compose_services`/`classify_service_error`/`service_from_url`).
- Delete `tests/depgraph/test_service_binding.py` and `tests/test_service_provision_off_state.py` entirely.
- `tests/depgraph/test_build.py`: delete `test_build_includes_config_nodes`, `test_build_includes_service_nodes`, `test_build_attaches_provisioning_when_enabled`.

- [ ] **Step 4: Verify the parsers + the rest still pass**

Run: `python3 -m pytest tests/depgraph/test_config_scan.py tests/depgraph/test_service_scan.py tests/depgraph/test_static_collect_bundle.py tests/depgraph/test_build.py tests/depgraph/test_advise_classify_hook.py -q`
Expected: PASS — parser tests green; `build_dep_graph` no longer references the deleted functions; `static_collect` (which imports only the kept parsers) green; `runtime_classify` still imports `classify_service_error` (kept).
Also: `python3 -m pytest tests/depgraph/test_runtime_classify.py -q` — confirms the kept `classify_service_error` import is intact.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build.py src/python_deps/depgraph/config_scan.py src/python_deps/depgraph/service_scan.py src/python_deps/depgraph/advise.py agent.py tests/depgraph/test_config_scan.py tests/depgraph/test_service_scan.py tests/depgraph/test_build.py
git rm tests/depgraph/test_service_binding.py tests/test_service_provision_off_state.py
git commit -m "refactor(env-classifier): delete deterministic scan_config/scan_services + build Stages 3c/3d/3e (parser-only)"
```

---

### Task 6: full-suite regression + manual real-LLM smoke

**Files:** (gates only; no new files)

- [ ] **Step 1: Full-suite regression**

Run: `python3 -m pytest tests -q -p no:cacheprovider`
Expected: the 4 known pre-existing failures (`test_adapter_logic` nested_pytester, `test_repo2run_dataset` ×2, `test_runtime_pin_seam` floor-trap) — and **the config/service node-creation tests this plan intentionally deleted must be GONE, not failing**. Any OTHER new failure → investigate. In particular, grep the failures for residual references to `scan_config`/`scan_services`/`attach_in_image_provisioning`/`enable_service_provision` — each indicates a missed call site to update.

- [ ] **Step 2: Prove the engine + v1 path still build a graph (without config/service tiers)**

Run: `python3 -m pytest tests/depgraph -q` and `python3 -m pytest tests/test_orchestrator_v1.py tests/test_orchestrator_v1_snapshot.py tests/test_graph_scheduler_wiring.py -q`
Expected: green — `build_dep_graph` returns a valid graph (now without Config/Service nodes); the scheduler/v1 wiring unaffected.

- [ ] **Step 3: Manual real-LLM smoke (record, do not gate CI)**

On the cloned `full-stack-fastapi-template` (root `compose.yml` `db: postgres:18`, pydantic settings, `psycopg`), run a graph build with a configured client (e.g. gemini via `OPENROUTER_PROVIDER="Google AI Studio,Google"`) and inspect: the LLM emitted `service:postgres` (+ maybe `config:*`/`data:*`) Hint/Candidate nodes, the `pkg:psycopg → service:postgres` **soft** edge, and that none block `scheduler_frontier`. Append the observed nodes/edges to the run report. Note: this exercises the real `complete_fn`; a `404`/provider error means the model/provider env is wrong, not a code fault.

- [ ] **Step 4: Record the result**

Append the pass/fail tally + the exact failing-test names to the run report. If only the 4 known failures remain (and the deleted tests are absent), the slice is regression-clean.

---

## Done-definition

- `build_dep_graph` no longer creates Config/Service nodes; the deterministic node-creators are deleted; the file parsers survive and feed `static_collect`.
- The construction LLM classifier (envstate, injected into `build_advisory_for_repo`) is the sole Config/Service/DataAsset source, emitting Hint/Candidate nodes + all-soft edges via `PatchProposal → admit_proposal`, grounded on the bundle's `evidence_id`s.
- `python_deps/depgraph` stays LLM-free (advise.py only invokes an injected callable); the trust boundary holds (gate never writes SATISFIED).
- Full suite green except the 4 known pre-existing failures; the intentionally-deleted config/service node tests are removed.

## After this plan (separate work — do NOT start here)

- **Re-home the armed service action layer** (`attach_in_image_provisioning` / start_recipe / binding-config) onto the LLM's service nodes — the one place a *hard* gate (binding-config waits for service) belongs.
- **Bundle source expansion** to the full recalled list (README/docs, Makefile/scripts, `.devcontainer`, `Dockerfile`, `.gitlab-ci`, pydantic `BaseSettings` in `env_read`, conftest/fixtures).
- **Re-baseline** v3 with the classifier on vs a git-reverted deterministic build (the flag-based A/B is gone by design).
