# Graph-Governed Script-Materialized Agent — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone, host-certified graph→annotated-script→block-runner→per-node-certify→evidence core, with no LLM and no Docker in the unit tests.

**Architecture:** A certified `DepGraph` (existing engine) compiles to an *annotated* one-action-per-block `setup.sh`; a block runner executes blocks under strict shell, logs typed per-block `Evidence`, and certifies each block's target nodes via the existing host-check path (`certify_refresh`). Block exit-0 never certifies a node — only a passing host check does. Phase 1 is greenfield modules on top of the pure `python_deps.depgraph` engine; it touches neither `run_v1` nor `run_v3`.

**Tech Stack:** Python 3 (this repo runs under `python3`), `pytest`, frozen `@dataclass` immutables, the existing `src/python_deps/depgraph/` engine (`emit`, `schema`, `certify`) and `src/envstate/depgraph_live.py` bridge.

**Source design:** `docs/superpowers/specs/2026-06-28-graph-governed-script-materialized-agent-design.md` (§3.2, §3.3, §5.2, §6, §7) and its §18 Planning Decisions.
**Grounding:** `.superpowers/sdd/newdesign-{1-reuse-gap,2-plan,3-risks-decisions}.md`.
**Reviewed:** `.superpowers/sdd/review-{1-alignment,2-implementation,3-complexity}.md` (sonnet, 2026-06-28).

> **Review corrections applied (2026-06-28), all verified against the live schema:**
> (1) `Layer.PIP` is the Python-package layer — there is NO `Layer.PYTHON`.
> (2) `Node` has a REQUIRED `discovered_by: DiscoveredBy` field (no default) — every `Node(...)` passes `discovered_by=DiscoveredBy.RESOLVER`.
> (3) Node lookup is `DepGraph.get(id) -> Node | None` — there is NO `graph.node()`.
> (4) `scan_ci_services`/`scan_compose_services` meta dicts carry `image/host/port`, NOT a file path — the static-collect adapter uses generic file labels.
> (5) Truncation is extracted to a pure `src/envstate/text_util.py` so the runner does not import the LLM-agent stack.
> Alignment review: Phase 1 faithfully matches §18 and correctly defers gates/PatchGate/platform/causal. Complexity review: boundaries clean, no new cycles; the Phase-2 "Architectural musts" below capture its findings.

## Global Constraints

- **No behaviour change to v1 or v3.** Phase 1 adds new modules only; it imports nothing into the existing loops. The full suite must stay green except the 4 known pre-existing failures (`test_adapter_logic` nested_pytester, `test_repo2run_dataset` ×2, `test_runtime_pin_seam` floor-trap).
- **Immutability:** every new dataclass is `@dataclass(frozen=True)`; "mutation" returns a new copy (matches `schema.Node`/`DepGraph`).
- **State authority (invariant #2/#3/#4):** a block exiting 0 must NOT set any node `SATISFIED`. Only the host check (via `certify_refresh`) writes state. Tests must assert this explicitly.
- **State enum unchanged:** do NOT add values to `State`; it stays `{UNKNOWN, MISSING, SATISFIED}`. Hint/Candidate/Active is `Node.data["promotion"]` + `Edge.data["hard"]`, never a `state`.
- **File size:** keep each new file < 400 lines; pure modules carry no Docker/network/LLM imports.
- **Naming:** the evidence module is `evidence_log.py` (NOT `evidence.py` — `src/python_deps/evidence.py` already exists).
- **Reuse, don't reimplement:** `emit.partition`, `emit.topo_order`, `emit._apt_name`, `emit._pip_spec`, `depgraph_live.certify_refresh`, `depgraph_live.ensure_python_shim`, `depgraph_live._ReadonlyExecAdapter`, `config_scan.scan_env_reads`/`parse_env_example`, `service_scan.scan_ci_services`/`scan_compose_services`. (Truncation is extracted to a new pure `text_util.truncate_output` — see Task 5 — so the runner stays off the LLM stack.)
- **Git hygiene:** `git add` only the exact files each task creates/modifies — NEVER `git add -A`/`.`/`<dir>` (the repo has unrelated untracked WIP). Conventional commit messages with an Observation/Why/What/Verification body. **No `Co-Authored-By` trailer.** Do not push.

### Verified reuse signatures (use these exactly)

```python
# src/python_deps/depgraph/emit.py
@dataclass(frozen=True)
class Partition:
    certified: tuple[Node, ...]
    emittable: tuple[Node, ...]
    frontier:  tuple[Node, ...]
def partition(graph: DepGraph) -> Partition: ...
def topo_order(graph: DepGraph, nodes: tuple[Node, ...]) -> tuple[Node, ...]: ...
def _apt_name(node: Node) -> str | None: ...      # node.chosen_fix "apt:NAME" -> "NAME" else None
def _pip_spec(node: Node) -> str: ...             # "name==ver" or "name"

# src/python_deps/depgraph/schema.py  (Node — frozen)
#   id:str  type:NodeType  name:str  layer:Layer  discovered_by:DiscoveredBy  (REQUIRED, no default)
#   state:State=UNKNOWN  version:str|None  check_command:str|None  chosen_fix:str|None  data:dict
#   NodeType.{PACKAGE, SYSTEM_LIB, TOOL, ...}; State.{UNKNOWN, MISSING, SATISFIED}
#   Layer.{SYSTEM, PIP, RUNTIME, TESTS, CONFIG, SERVICES, ...}  (NOTE: PIP, not "PYTHON")
#   DiscoveredBy.{GOAL, STATIC_SCAN, RESOLVER, PROBE, RUNTIME}
# DepGraph node lookup: graph.get(node_id) -> Node | None   (there is NO graph.node())

# src/envstate/depgraph_live.py
def certify_refresh(graph, exec_readonly, cycle: int, *, allow_service_certify: bool|None=None): ...
def ensure_python_shim(sandbox_execute) -> None: ...
class _ReadonlyExecAdapter:  # __init__(self, exec_readonly: Callable[[str], tuple[int,str]])

# src/python_deps/depgraph/config_scan.py / service_scan.py
def scan_env_reads(repo_path: str) -> dict[str, str]: ...        # VAR -> file
def parse_env_example(repo_path: str) -> dict[str, str]: ...     # VAR -> default
def scan_ci_services(repo_path: str) -> tuple[dict[str, dict], bool]: ...  # {name: {...}}, has_ci
def scan_compose_services(repo_path: str) -> dict[str, dict]: ...          # {name: {...}}

# src/envstate/build_agent.py  (reference only — DO NOT import from the runner)
def _truncate_output(output: str) -> str: ...    # head+tail truncation; Task 5 re-homes this
                                                 # logic into the pure src/envstate/text_util.py
```

The runner's executor callables (match the existing orchestrator contract):
- `sandbox_execute: Callable[[str], tuple[bool, str]]` — MUTATING exec, returns `(ok, output)`.
- `exec_readonly:   Callable[[str], tuple[int, str]]` — read-only exec, returns `(rc, output)`.

---

### Task 1: `Block` dataclass + `compile_blocks(graph)`

**Files:**
- Create: `src/python_deps/depgraph/block.py`
- Test: `tests/depgraph/test_block_compile.py`

**Interfaces:**
- Consumes: `emit.partition`, `emit.topo_order`, `emit._apt_name`, `emit._pip_spec`, `schema.{Node,NodeType,DepGraph}`.
- Produces: `Block` (frozen dataclass, fields below) and `compile_blocks(graph: DepGraph) -> tuple[Block, ...]` — one block per emittable node, in topo order (§6 "one provider/action → one block", v1 `can_batch=False`).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_block_compile.py
from python_deps.depgraph.block import Block, compile_blocks
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy


def test_block_fields_default():
    b = Block(block_id="sys.libpq", wave="system", commands=("apt-get install -y libpq-dev",),
              target_node_ids=("syslib:libpq",))
    assert b.can_batch is False
    assert b.mutates_env is True
    assert b.provider_ids == () and b.check_commands == ()


def _g():
    g = DepGraph()
    g = g.with_node(Node(id="syslib:libpq", type=NodeType.SYSTEM_LIB, name="libpq.so",
                         layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                         check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev"))
    g = g.with_node(Node(id="pkg:psycopg2", type=NodeType.PACKAGE, name="psycopg2",
                         layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                         state=State.MISSING, version="2.9.9",
                         check_command="python -m pip show psycopg2"))
    return g


def test_one_block_per_node_topo_order():
    blocks = compile_blocks(_g())
    assert len(blocks) == 2
    # system wave before python wave
    assert blocks[0].target_node_ids == ("syslib:libpq",)
    assert blocks[1].target_node_ids == ("pkg:psycopg2",)
    # command + annotations populated from the node
    assert "libpq-dev" in blocks[0].commands[0]
    assert blocks[0].provider_ids == ("apt:libpq-dev",)
    assert blocks[0].check_commands == ("ldconfig -p | grep -q libpq",)
    assert "psycopg2==2.9.9" in blocks[1].commands[0]
    assert blocks[1].check_commands == ("python -m pip show psycopg2",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_block_compile.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.block'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/block.py
"""Compile a certified DepGraph's emittable wave into annotated, one-action-per-block
script blocks (design §6). Pure: no Docker, no network, no LLM."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.emit import partition, topo_order, _apt_name, _pip_spec
from python_deps.depgraph.schema import DepGraph, Node, NodeType


@dataclass(frozen=True)
class Block:
    block_id: str
    wave: str                              # node.layer.value
    commands: tuple[str, ...]
    target_node_ids: tuple[str, ...]
    provider_ids: tuple[str, ...] = ()
    check_commands: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    mutates_env: bool = True
    can_batch: bool = False                # v1: one action per block (defer batching to v2)


def _command_for(node: Node) -> str:
    apt = _apt_name(node)
    if apt is not None:
        return f"apt-get install -y --no-install-recommends {apt}"
    if node.type is NodeType.PACKAGE:
        return f"python3 -m pip install --break-system-packages {_pip_spec(node)}"
    # Fallback: a node with an explicit chosen_fix that is not apt: (e.g. a shell recipe).
    return node.chosen_fix or ""


def _block_id_for(node: Node) -> str:
    short = node.id.split(":", 1)[-1]
    return f"{node.layer.value}.{short}"


def compile_blocks(graph: DepGraph) -> tuple[Block, ...]:
    if graph is None:
        return ()
    ready = topo_order(graph, partition(graph).emittable)
    blocks: list[Block] = []
    for n in ready:
        cmd = _command_for(n)
        if not cmd:
            continue
        apt = _apt_name(n)
        blocks.append(Block(
            block_id=_block_id_for(n),
            wave=n.layer.value,
            commands=(cmd,),
            target_node_ids=(n.id,),
            provider_ids=(n.chosen_fix,) if apt is not None else (),
            check_commands=(n.check_command,) if n.check_command else (),
        ))
    return tuple(blocks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_block_compile.py -q`
Expected: PASS (2 tests). If topo order differs, confirm `Layer.SYSTEM` orders before `Layer.PIP` in the engine; adjust the fixture, not the order logic.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/block.py tests/depgraph/test_block_compile.py
git commit -m "feat(depgraph): Block + compile_blocks (one annotated block per emittable node)"
```

---

### Task 2: Annotated `render_setup_sh` + `parse_setup_sh`

**Files:**
- Create: `src/python_deps/depgraph/script.py`
- Test: `tests/depgraph/test_script_render.py`

**Interfaces:**
- Consumes: `block.Block`.
- Produces: `render_setup_sh(blocks: tuple[Block, ...]) -> str` (emits the §3.2 annotation format with a `set -Eeuo pipefail` preamble, §7) and `parse_setup_sh(text: str) -> tuple[Block, ...]` (round-trips the annotations).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_script_render.py
from python_deps.depgraph.block import Block
from python_deps.depgraph.script import render_setup_sh, parse_setup_sh

_B = (
    Block(block_id="system.libpq", wave="system",
          commands=("apt-get install -y --no-install-recommends libpq-dev",),
          target_node_ids=("syslib:libpq",), provider_ids=("apt:libpq-dev",),
          check_commands=("ldconfig -p | grep -q libpq",)),
    Block(block_id="python.psycopg2", wave="python",
          commands=("python3 -m pip install --break-system-packages psycopg2==2.9.9",),
          target_node_ids=("pkg:psycopg2",),
          check_commands=("python -m pip show psycopg2",)),
)


def test_headers_and_strict_mode():
    out = render_setup_sh(_B)
    assert out.splitlines()[0].startswith("#!")          # shebang
    assert "set -Eeuo pipefail" in out
    assert out.count("#@action") == 2
    assert "#@targets syslib:libpq" in out
    assert "#@provides apt:libpq-dev" in out
    assert "#@check ldconfig -p | grep -q libpq" in out


def test_render_parse_roundtrip():
    assert parse_setup_sh(render_setup_sh(_B)) == _B
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_script_render.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.script'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/script.py
"""Render annotated setup.sh from blocks and parse it back (design §3.2, §7). Pure."""
from __future__ import annotations

from python_deps.depgraph.block import Block

_PREAMBLE = "#!/usr/bin/env bash\nset -Eeuo pipefail\n"


def render_setup_sh(blocks: tuple[Block, ...]) -> str:
    parts = [_PREAMBLE]
    for b in blocks:
        parts.append(f"\n#@action id={b.block_id} wave={b.wave}")
        if b.target_node_ids:
            parts.append("#@targets " + " ".join(b.target_node_ids))
        if b.provider_ids:
            parts.append("#@provides " + " ".join(b.provider_ids))
        for chk in b.check_commands:
            parts.append(f"#@check {chk}")
        parts.extend(b.commands)
    return "\n".join(parts) + "\n"


def parse_setup_sh(text: str) -> tuple[Block, ...]:
    blocks: list[Block] = []
    cur: dict | None = None
    cmds: list[str] = []

    def _flush():
        if cur is not None:
            blocks.append(Block(
                block_id=cur["id"], wave=cur["wave"], commands=tuple(cmds),
                target_node_ids=tuple(cur.get("targets", ())),
                provider_ids=tuple(cur.get("provides", ())),
                check_commands=tuple(cur.get("checks", ())),
            ))

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#@action"):
            _flush()
            cmds = []
            kv = dict(tok.split("=", 1) for tok in s[len("#@action"):].split() if "=" in tok)
            cur = {"id": kv.get("id", ""), "wave": kv.get("wave", ""),
                   "targets": (), "provides": (), "checks": []}
        elif s.startswith("#@targets") and cur is not None:
            cur["targets"] = tuple(s[len("#@targets"):].split())
        elif s.startswith("#@provides") and cur is not None:
            cur["provides"] = tuple(s[len("#@provides"):].split())
        elif s.startswith("#@check") and cur is not None:
            cur["checks"].append(s[len("#@check"):].strip())
        elif s.startswith("#!") or s.startswith("set -") or not s:
            continue
        elif cur is not None:
            cmds.append(line)
    _flush()
    return tuple(blocks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_script_render.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/script.py tests/depgraph/test_script_render.py
git commit -m "feat(depgraph): annotated setup.sh render + round-trip parse"
```

---

### Task 3: `Evidence` + `EvidenceBundle`

**Files:**
- Create: `src/python_deps/depgraph/evidence_log.py`
- Test: `tests/depgraph/test_evidence_bundle.py`

**Interfaces:**
- Produces: `Evidence` (frozen), `EvidenceBundle` (frozen, immutable append via `with_item`), `write_jsonl(bundle, path)`. Used by Task 5 and (later) Phases 2/5/6.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_evidence_bundle.py
import json
from python_deps.depgraph.evidence_log import Evidence, EvidenceBundle, write_jsonl


def _ev(i=0):
    return Evidence(evidence_id=f"ev.{i}", container_kind="canonical",
                    command="apt-get install -y libpq-dev", rc=0,
                    output_excerpt="ok", cycle=1, block_id="system.libpq",
                    node_id="syslib:libpq")


def test_evidence_roundtrip():
    ev = _ev()
    assert Evidence.from_dict(ev.to_dict()) == ev


def test_bundle_immutable_append():
    b0 = EvidenceBundle()
    b1 = b0.with_item(_ev(1))
    assert b0.items == () and len(b1.items) == 1     # b0 unchanged


def test_write_jsonl_lines(tmp_path):
    b = EvidenceBundle().with_item(_ev(1)).with_item(_ev(2))
    p = tmp_path / "evidence.jsonl"
    write_jsonl(b, str(p))
    lines = p.read_text().splitlines()
    assert len(lines) == 2 and all(json.loads(ln)["container_kind"] == "canonical" for ln in lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_evidence_bundle.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.evidence_log'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/evidence_log.py
"""Typed evidence ledger (design §3.3). Orthogonal to ledger.ActionEvent. Pure."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

_CONTAINER_KINDS = ("canonical", "lab", "fresh_replay")


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    container_kind: str
    command: str
    rc: int
    output_excerpt: str
    cycle: int
    block_id: str | None = None
    node_id: str | None = None
    gate_id: str | None = None

    def __post_init__(self):
        if self.container_kind not in _CONTAINER_KINDS:
            raise ValueError(f"container_kind must be one of {_CONTAINER_KINDS}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        return cls(**d)


@dataclass(frozen=True)
class EvidenceBundle:
    items: tuple[Evidence, ...] = ()

    def with_item(self, ev: Evidence) -> "EvidenceBundle":
        return EvidenceBundle(items=self.items + (ev,))


def write_jsonl(bundle: EvidenceBundle, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for ev in bundle.items:
            fh.write(json.dumps(ev.to_dict()) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_evidence_bundle.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/evidence_log.py tests/depgraph/test_evidence_bundle.py
git commit -m "feat(depgraph): typed Evidence + EvidenceBundle (container_kind, jsonl)"
```

---

### Task 4: `DeterministicHit` + `collect_static_evidence` + compact bundle

**Files:**
- Create: `src/python_deps/depgraph/static_collect.py`
- Test: `tests/depgraph/test_static_collect_bundle.py`

**Interfaces:**
- Consumes: `config_scan.scan_env_reads`/`parse_env_example`, `service_scan.scan_ci_services`/`scan_compose_services`.
- Produces: `DeterministicHit` (frozen), `collect_static_evidence(repo_path) -> tuple[DeterministicHit, ...]`, `compact_bundle_json(hits, goal=...) -> str` (the §5.2 LLM bundle shape). Pure (filesystem reads only).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_static_collect_bundle.py
import json
from python_deps.depgraph.static_collect import (
    DeterministicHit, collect_static_evidence, compact_bundle_json,
)


def _repo(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "test.yml").write_text(
        "jobs:\n  t:\n    services:\n      postgres:\n        image: postgres:15\n"
        "        ports: ['5432:5432']\n")
    (tmp_path / ".env.example").write_text("DATABASE_URL=postgres://localhost/db\n")
    return str(tmp_path)


def test_ci_postgres_and_env_var_hits(tmp_path):
    hits = collect_static_evidence(_repo(tmp_path))
    kinds = {h.kind for h in hits}
    assert "ci_service" in kinds                       # postgres from CI
    assert any(h.kind == "env_var" and h.name == "DATABASE_URL" for h in hits)
    # every hit has a stable evidence_id and a file
    assert all(h.evidence_id and h.file for h in hits)


def test_compact_bundle_json_shape(tmp_path):
    hits = collect_static_evidence(_repo(tmp_path))
    bundle = json.loads(compact_bundle_json(hits))
    assert "goal" in bundle and isinstance(bundle["deterministic_hits"], list)
    assert {"evidence_id", "file", "kind"} <= set(bundle["deterministic_hits"][0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.static_collect'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/static_collect.py
"""Deterministic static evidence collectors → compact LLM bundle (design §5.2). Pure.

Thin adapter that RESHAPES the existing config/service scanners' output into the
§5.2 bundle rows. It does NOT scan the repo blindly and does NOT create graph truth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from python_deps.depgraph.config_scan import scan_env_reads, parse_env_example
from python_deps.depgraph.service_scan import scan_ci_services, scan_compose_services

_GOAL = ("Infer local install/test/run environment requirements, not deployment "
         "requirements.")


@dataclass(frozen=True)
class DeterministicHit:
    evidence_id: str
    file: str
    kind: str                     # ci_service | compose_service | env_var | env_read
    snippet: str = ""
    name: str | None = None


def collect_static_evidence(repo_path: str) -> tuple[DeterministicHit, ...]:
    hits: list[DeterministicHit] = []
    n = 0

    def _add(file, kind, *, name=None, snippet=""):
        nonlocal n
        prefix = {"ci_service": "ci", "compose_service": "svc",
                  "env_var": "env", "env_read": "code"}.get(kind, "ev")
        hits.append(DeterministicHit(f"{prefix}.{n:02d}", file, kind,
                                     snippet=snippet, name=name))
        n += 1

    ci_services, _has_ci = scan_ci_services(repo_path)
    for svc, meta in sorted(ci_services.items()):
        # VERIFIED: service meta carries image/host/port — NOT a file path; use a generic label.
        _add(".github/workflows", "ci_service", name=svc, snippet=str(meta.get("image", svc)))
    for svc, meta in sorted(scan_compose_services(repo_path).items()):
        _add("docker-compose.yml", "compose_service", name=svc, snippet=str(meta.get("image", svc)))
    for var, default in sorted(parse_env_example(repo_path).items()):
        _add(".env.example", "env_var", name=var, snippet=str(default))
    for var, file in sorted(scan_env_reads(repo_path).items()):
        _add(file, "env_read", name=var)
    return tuple(hits)


def compact_bundle_json(hits: tuple[DeterministicHit, ...], goal: str = _GOAL) -> str:
    rows = []
    for h in hits:
        row = {"evidence_id": h.evidence_id, "file": h.file, "kind": h.kind}
        if h.name is not None:
            row["name"] = h.name
        if h.snippet:
            row["snippet"] = h.snippet
        rows.append(row)
    return json.dumps({"goal": goal, "deterministic_hits": rows}, indent=2)
```

> **Implementer note (VERIFIED 2026-06-28):** `scan_ci_services(repo)->(dict[name,meta], has_ci)` and `scan_compose_services(repo)->dict[name,meta]`; each `meta` carries `image/host/port/service_confidence` and **no file-path key** — so the adapter uses generic file labels (above), not `meta.get("file")`/`"source"`. `scan_env_reads(repo)->{VAR: file}` (value IS the file) and `parse_env_example(repo)->{VAR: default}`. Do not change the scanners; only the adapter. The test asserts kind+name (reliable), not exact file paths.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/static_collect.py tests/depgraph/test_static_collect_bundle.py
git commit -m "feat(depgraph): static evidence collectors + compact LLM bundle (§5.2)"
```

---

### Task 5: Block runner — `run_blocks` (the §7 runner)

**Files:**
- Create: `src/envstate/text_util.py` (extract the pure `truncate_output` helper so the runner does NOT import the LLM-agent stack via `build_agent`).
- Create: `src/envstate/script_runner.py`
- Test: `tests/envstate/test_script_runner.py`

**Interfaces:**
- Consumes: `block.Block`, `evidence_log.{Evidence,EvidenceBundle}`, `depgraph_live.{certify_refresh,ensure_python_shim}`, `text_util.truncate_output`.
- Produces: `run_blocks(blocks, sandbox_execute, exec_readonly, graph, cycle, *, container_kind="canonical") -> tuple[DepGraph, EvidenceBundle, str | None]` — returns `(certified_graph, evidence, failed_block_id_or_None)`. Executes each block under the mutating executor, stops on first failed block (§7), logs one `Evidence` per block, and certifies via `certify_refresh` (block rc=0 ≠ node truth — invariant #2/#3).

- [ ] **Step 1: Write the failing test**

```python
# tests/envstate/test_script_runner.py
from python_deps.depgraph.block import Block
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from src.envstate.script_runner import run_blocks


def _graph():
    g = DepGraph()
    g = g.with_node(Node(id="syslib:libpq", type=NodeType.SYSTEM_LIB, name="libpq.so",
                         layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                         check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev"))
    return g


_BLOCKS = (
    Block(block_id="system.libpq", wave="system",
          commands=("apt-get install -y --no-install-recommends libpq-dev",),
          target_node_ids=("syslib:libpq",), check_commands=("ldconfig -p | grep -q libpq",)),
    Block(block_id="system.second", wave="system", commands=("echo two",),
          target_node_ids=("syslib:libpq",)),
)


def _exec_ok(cmd):                       # mutating exec: (ok, output)
    return True, "installed"


def test_one_evidence_per_block_and_certify():
    # read-only exec: the check passes -> node becomes SATISFIED
    def ro(cmd):
        return (0, "libpq found") if "ldconfig" in cmd else (1, "")
    graph, bundle, failed = run_blocks(_BLOCKS, _exec_ok, ro, _graph(), cycle=1)
    assert failed is None
    assert len(bundle.items) == 2 and all(e.container_kind == "canonical" for e in bundle.items)
    assert graph.get("syslib:libpq").state is State.SATISFIED


def test_stops_on_first_failed_block():
    calls = []
    def exec_fail_first(cmd):
        calls.append(cmd)
        return (False, "E: package not found") if "libpq-dev" in cmd else (True, "")
    def ro(cmd):
        return (1, "")                  # never satisfied
    graph, bundle, failed = run_blocks(_BLOCKS, exec_fail_first, ro, _graph(), cycle=1)
    assert failed == "system.libpq"
    assert not any("echo two" in c for c in calls)        # block 2 never ran
    assert bundle.items[-1].rc != 0


def test_block_rc0_does_not_certify_without_check_pass():
    # block succeeds (rc 0) but the host check fails -> node stays MISSING (invariant #2/#3)
    def ro(cmd):
        return (1, "not found")
    graph, bundle, failed = run_blocks(_BLOCKS, _exec_ok, ro, _graph(), cycle=1)
    assert graph.get("syslib:libpq").state is not State.SATISFIED
```

> **Implementer note (VERIFIED):** node lookup is `graph.get(node_id) -> Node | None` (there is NO `graph.node()`). `Node` requires `discovered_by=DiscoveredBy.RESOLVER` (no default). `Layer.PIP` is the Python-package layer (there is no `Layer.PYTHON`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/envstate/test_script_runner.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.script_runner'`.

- [ ] **Step 3a: Extract the pure truncation helper**

Create `src/envstate/text_util.py` so `script_runner` stays off the LLM-agent stack
(`build_agent` imports `llm_response` etc.). This is additive — leave
`build_agent._truncate_output` in place for Phase 1 (Phase 2 can re-point it to
delegate here; the brief duplication is acceptable and noted).

```python
# src/envstate/text_util.py
"""Pure text helpers shared across envstate (no LLM/Docker imports)."""
from __future__ import annotations

_HEAD = 1500
_TAIL = 1500


def truncate_output(output: str, head: int = _HEAD, tail: int = _TAIL) -> str:
    """Head+tail truncation preserving the start and the tail (traceback/pytest summary)."""
    s = output or ""
    if len(s) <= head + tail:
        return s
    return s[:head] + "\n...[truncated]...\n" + s[-tail:]
```
> The implementer SHOULD mirror the head/tail sizes of the existing
> `build_agent._truncate_output` so excerpts look identical; read that function and copy its constants.

- [ ] **Step 3b: Write the runner**

```python
# src/envstate/script_runner.py
"""Strict-shell block runner (design §7): execute annotated blocks, log typed
Evidence, certify target nodes via the host-check path. The v3 analog of
depgraph_live.emit_drain, but runs raw block commands with NO LLM seeding.

Invariant #2/#3: a block exiting 0 never certifies a node — only certify_refresh
(a real host check) writes SATISFIED.
"""
from __future__ import annotations

from typing import Callable

from python_deps.depgraph.block import Block
from python_deps.depgraph.evidence_log import Evidence, EvidenceBundle
from src.envstate.depgraph_live import certify_refresh, ensure_python_shim
from src.envstate.text_util import truncate_output


def run_blocks(
    blocks: tuple[Block, ...],
    sandbox_execute: Callable[[str], tuple[bool, str]],
    exec_readonly: Callable[[str], tuple[int, str]],
    graph,
    cycle: int,
    *,
    container_kind: str = "canonical",
) -> tuple[object, EvidenceBundle, str | None]:
    ensure_python_shim(sandbox_execute)
    bundle = EvidenceBundle()
    failed_block_id: str | None = None
    ev_n = 0
    for block in blocks:
        ok = True
        out = ""
        for cmd in block.commands:
            ok, out = sandbox_execute(cmd)
            ev = Evidence(
                evidence_id=f"ev.{cycle}.{ev_n}", container_kind=container_kind,
                command=cmd, rc=0 if ok else 1,
                output_excerpt=truncate_output(out or ""), cycle=cycle,
                block_id=block.block_id,
                node_id=block.target_node_ids[0] if block.target_node_ids else None,
            )
            bundle = bundle.with_item(ev)
            ev_n += 1
            if not ok:
                failed_block_id = block.block_id
                break
        if not ok:
            break
        # block rc==0: certify the WHOLE graph via host checks (SATISFIED only on check pass)
        graph = certify_refresh(graph, exec_readonly, cycle)
    return graph, bundle, failed_block_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/envstate/test_script_runner.py -q`
Expected: PASS (3 tests). If `certify_refresh` certifies more/less than expected, verify the fixture's `check_command` matches the read-only stub's matched substring.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/text_util.py src/envstate/script_runner.py tests/envstate/test_script_runner.py
git commit -m "feat(envstate): strict-shell block runner with per-block evidence + host certify"
```

---

### Task 6: Phase-1 invariant suite + full-suite regression gate

**Files:**
- Create: `tests/depgraph/test_gsm_invariants_phase1.py`
- Test: (this task is the test)

**Interfaces:**
- Consumes: everything from Tasks 1–5. Encodes the design §16 invariants that Phase 1 can already assert, as the paper's contribution surface.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_gsm_invariants_phase1.py
"""Design §16 invariants assertable in Phase 1."""
from python_deps.depgraph.block import compile_blocks
from python_deps.depgraph.script import render_setup_sh, parse_setup_sh
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy
from src.envstate.script_runner import run_blocks


def _g():
    g = DepGraph()
    return g.with_node(Node(id="syslib:libpq", type=NodeType.SYSTEM_LIB, name="libpq.so",
                            layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                            check_command="ldconfig -p | grep -q libpq",
                            chosen_fix="apt:libpq-dev"))


def test_invariant2_block_success_is_not_node_truth():
    # block rc=0, host check fails -> node not SATISFIED
    blocks = compile_blocks(_g())
    g, _bundle, failed = run_blocks(blocks, lambda c: (True, "ok"),
                                    lambda c: (1, "absent"), _g(), cycle=1)
    assert failed is None
    assert g.node("syslib:libpq").state is not State.SATISFIED


def test_invariant_script_is_compiled_artifact_not_state():
    # the script is a pure projection of the graph; round-trips with no state inside it
    blocks = compile_blocks(_g())
    text = render_setup_sh(blocks)
    assert "SATISFIED" not in text and "state" not in text.lower()
    assert parse_setup_sh(text) == blocks
```

- [ ] **Step 2: Run test to verify it fails (then passes once 1–5 are in)**

Run: `python3 -m pytest tests/depgraph/test_gsm_invariants_phase1.py -q`
Expected: PASS once Tasks 1–5 are merged (these are characterization assertions over the finished core). If either fails, the corresponding task has an invariant bug — fix the implementation, not the test.

- [ ] **Step 3: Full-suite regression gate**

Run: `python3 -m pytest tests -q -p no:cacheprovider`
Expected: only the 4 known pre-existing failures remain (see Global Constraints). Any NEW failure means a Phase-1 module leaked into an existing import path — investigate before committing.

- [ ] **Step 4: Commit**

```bash
git add tests/depgraph/test_gsm_invariants_phase1.py
git commit -m "test(depgraph): Phase-1 GSM invariants (block-rc != node-truth; script is projection)"
```

---

## Phase 1 done-definition

- `compile_blocks` → `render_setup_sh`/`parse_setup_sh` → `run_blocks` → `certify_refresh` works end-to-end against a `FakeExecutor`, with typed `Evidence` and the §16 invariants asserted.
- `static_collect` emits the §5.2 compact bundle from the existing scanners.
- Neither `run_v1` nor `run_v3` changed; full suite green except the 4 known pre-existing failures.

## Next phases (separate plans — do NOT start here)

- **Phase 2 (the integration; applies §18 decisions):** rewrite `run_v3` to drive `compile_blocks → run_blocks → certify_refresh`; add `PatchProposal`/`PatchGate` (port `contracts/{patch,validation,apply}`); switch the final Dockerfile/fresh-replay source to the compiled `setup.sh` (supersede `synthesis.py` ledger-replay for v3); keep `_verified_test_run_passed` as the binding done-gate; expose an internal graph-only toggle for the §14 B3 ablation. **Re-baseline v3 after.**
  - **Architectural musts surfaced by review (do not skip in Phase 2):**
    - **Retire `RecipeStep`/`RecipePatch` from the v3 execution path.** After the rewrite, `run_blocks` is the SOLE v3 execution engine; `emit_drain`/`run_recipe`/`RecipePatch` become v1-only. Leaving both creates two competing execution engines feeding two incompatible evidence histories — directly undermining invariants #1/#2. (review-3 #2, the biggest structural risk.)
    - **Recompile the script after EVERY graph mutation.** `compile_blocks(graph)` is pure; the persisted `setup.sh` is only a *projection* of the graph. A PatchGate apply that adds an ordering edge is invisible to fresh replay unless the script is recompiled. "The script is a compiled proof attempt from the graph" only holds if compile is always re-run. (review-1, failure-class-4.)
    - **PatchGate needs a provider action-class taxonomy.** §10's "provider command matches allowed action class" has no existing implementation — define it at Phase 2 start (`kind="apt"`→`^apt-get install`, `kind="pip"`→`^python3 -m pip install`, …); the §14 "wrong apt package name" case is its regression test. (review-1.)
    - **Re-point `build_agent._truncate_output` → `text_util.truncate_output`** to remove the brief duplication left by Phase 1.
    - **Reframe the config/service detectors (spec §5.2.1).** The pure detectors (`config_scan.{scan_env_reads,parse_env_example,scan_framework_config_reads,scan_env_defaults}`, `service_scan.{scan_compose_services,scan_ci_services,scan_env_bindings,service_from_url}`, curated `config_obligations_for_package`/`services_for_package`) are KEPT and already wrapped read-only by Phase 1's `static_collect`. The graph-mutating wrappers `config_scan.scan_config`/`service_scan.scan_services` (called in `build.py::build_dep_graph:300,306`) stay for v1 but in v3 must NOT inject nodes directly — route their signal through deterministic-evidence → LLM classifier → SOFT hint nodes (`Node.data["promotion"]`), promoted to hard only on runtime/gate failure. Split by strength: curated package-induced + explicit CI services → deterministic CANDIDATE; weak single-source reads → HINT. Idempotent via canonical `config_id`/`service_id` + PatchGate dedupe.
    - **Generalize the scheduler carve-out.** Retire `schedule._is_actionable`'s hard-coded "CONFIG advisory-only except service-binding; SERVICE only if confirmed+armed" special case into the single soft/hard rule (`_dependencies_satisfied` respects `Edge.data["hard"]`). Do this together with the soft/hard-edge seam below.
- **Soft/hard edges (Phase 5 / §5.2 promotion ladder):** when the FIRST soft edge is introduced, add `Edge.data["hard"]` (default `True`) and make `schedule._dependencies_satisfied` filter `and e.data.get("hard", True)` so soft edges never block scheduling (invariant #10). Not needed in Phase 1 (all build edges are hard `requires`), but it is the exact seam — do not invent a parallel advisory path; generalize the existing Config/Service carve-out in `schedule._is_actionable` into this model. (review-1, review-3 #1/#4.)
- **Phase 3** maturity gates (advisory/scheduling only). **Phase 4** platform profiles + Platform node. **Phase 5** causal overlay + lab containers (the B4→B5 delta). **Phase 6** fresh-replay runner + baseline arms + controlled suite + metrics.
