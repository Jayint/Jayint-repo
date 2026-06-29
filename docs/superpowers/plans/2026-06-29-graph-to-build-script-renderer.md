# Graph → Whole Build Script Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure `render_build_script(graph, manual_blocks)` that compiles a `DepGraph` into one complete, structured, byte-reproducible `setup.sh` (deterministic system+pip core, comment-only `#@need` stubs for service/config, governed `#@block` lines for LLM patches).

**Architecture:** A new pure module `python_deps/depgraph/build_script.py` that projects the graph into a sectioned bash script — hard tier sections ordered by `Layer`, topologically ordered within each tier, with hoisted `apt-get update` and `--no-deps`-pinned pip installs. It reuses existing graph logic (`topo_order`, `_is_reciped`, `_apt_name`, `_pip_spec` from `emit.py`; `Block` from `block.py`; `_best_evidence_line` from `advise.py`) and adds **no** new graph traversal semantics. It coexists with `render_setup_sh`/`parse_setup_sh` (the live-loop format) which stay untouched.

**Tech Stack:** Python 3 (stdlib only: `hashlib`, `json`, `collections.Counter`), pytest.

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-06-29-graph-to-build-script-renderer-design.md`. Every task implicitly includes these:

- **Pure function.** No Docker, no network, no LLM, no `import` from `src.envstate`. (The `python_deps.depgraph` package keeps its zero dependency on `src.envstate`.)
- **Never writes `node.state`.** The renderer projects; it does not certify.
- **Byte-reproducible.** No timestamps, no randomness. Same graph ⇒ byte-identical script; output invariant to node insertion order.
- **Coexist, don't modify.** Do not change `script.py`, `block.py`, `emit.py`, `patch_gate.py`, or `block_emit.py`. `emit.py` may gain at most a tiny read-only helper if needed, but prefer importing existing functions.
- **Deterministic scope = `_is_reciped`.** Only `PACKAGE` (with version) and `SYSTEM_LIB`/`TOOL` (with `apt:` `chosen_fix`) get real install commands. `CONFIG`/`SERVICE`/`DATA_ASSET` get `#@need` stubs (unless covered by a `manual_block`). Goal/Platform/Runtime/interpreter/naming nodes are omitted.
- **Pip installs use `--no-deps`** (the closure is transitive-complete and pinned). **`apt-get update` is hoisted once**, before the first apt install.

---

## File Structure

- **Create:** `src/python_deps/depgraph/build_script.py` — the renderer and its private helpers. One responsibility: project a graph to a whole `setup.sh` string.
- **Create:** `tests/depgraph/test_build_script.py` — unit + property + golden-snapshot tests.

No other files are created or modified.

### Shared interface (defined here, used by all tasks)

```python
# Public
def render_build_script(graph: DepGraph, manual_blocks: tuple[Block, ...] = ()) -> str

# Private helpers (introduced across tasks; names are stable contracts)
_LAYER_ORDER: tuple[Layer, ...]                 # = tuple(Layer)  (enum order = rank order)
_NEED_TYPES: tuple[NodeType, ...]               # = (CONFIG, SERVICE, DATA_ASSET)
def _install_command(node: Node) -> str
def _node_block(graph: DepGraph, node: Node, apt_done: list[bool]) -> list[str]
def _need_block(graph: DepGraph, node: Node) -> list[str]
def _block_block(block: Block) -> list[str]
def _section_header(layer: Layer) -> str
def _reciped_in_layer(graph: DepGraph, layer: Layer) -> tuple[Node, ...]
def _need_in_layer(graph: DepGraph, layer: Layer, covered: set[str]) -> list[Node]
def _graph_hash(graph: DepGraph) -> str
def _closure_meta(graph: DepGraph) -> dict[str, str]
def _manifest(graph: DepGraph, manual_blocks: tuple[Block, ...]) -> list[str]
```

---

## Task 1: Module skeleton + preamble

**Files:**
- Create: `src/python_deps/depgraph/build_script.py`
- Test: `tests/depgraph/test_build_script.py`

**Interfaces:**
- Consumes: `DepGraph`, `Block` (schema.py / block.py).
- Produces: `render_build_script(graph, manual_blocks=()) -> str` returning at least the shebang banner + `set -Eeuo pipefail`. (The manifest counts arrive in Task 5; the body in Tasks 2–4.)

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_build_script.py
from python_deps.depgraph.schema import DepGraph
from python_deps.depgraph.build_script import render_build_script


def test_empty_graph_emits_preamble():
    out = render_build_script(DepGraph())
    lines = out.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT." in lines
    assert "set -Eeuo pipefail" in lines
    assert out.endswith("\n")


def test_none_graph_is_safe():
    assert render_build_script(None).splitlines()[0] == "#!/usr/bin/env bash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_build_script.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.build_script'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/build_script.py
"""Project a certified DepGraph into one whole, install-only setup.sh artifact
(design 2026-06-29). Pure: no Docker, no network, no LLM, no src.envstate.

Distinct from script.render_setup_sh (the live block-stepped, round-trippable
format): this renderer hoists shared setup and adds tier section headers, so it
is intentionally NOT parseable back to one-block-per-node.
"""
from __future__ import annotations

from python_deps.depgraph.schema import DepGraph

_BANNER = (
    "#!/usr/bin/env bash",
    "#",
    "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.",
    "# Edit the graph and re-render; this file is an artifact, not a source.",
    "#",
)


def render_build_script(graph, manual_blocks=()) -> str:
    if graph is None:
        graph = DepGraph()
    parts: list[str] = list(_BANNER) + ["set -Eeuo pipefail"]
    return "\n".join(parts) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_build_script.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build_script.py tests/depgraph/test_build_script.py
git commit -m "feat(build-script): module skeleton + preamble for graph->setup.sh renderer"
```

---

## Task 2: Deterministic `#@node` core (tier sections, topo, hoisted apt, --no-deps)

**Files:**
- Modify: `src/python_deps/depgraph/build_script.py`
- Test: `tests/depgraph/test_build_script.py`

**Interfaces:**
- Consumes: `topo_order`, `_is_reciped`, `_apt_name`, `_pip_spec` (emit.py); `_best_evidence_line` (advise.py, imported lazily to avoid load-order coupling); `graph.requires_of`, `graph.required_by` (schema.py).
- Produces: `_install_command`, `_node_block`, `_section_header`, `_reciped_in_layer`, `_LAYER_ORDER`; `render_build_script` now emits tier sections of `#@node` lines for reciped nodes.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_build_script.py
from python_deps.depgraph.schema import (
    DepGraph, Node, Edge, NodeType, Layer, State, DiscoveredBy, EdgeType,
)


def _pkg(id_, name, version, layer=Layer.PIP, **kw):
    return Node(id=id_, type=NodeType.PACKAGE, name=name, layer=layer,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                version=version, **kw)


def _apt(id_, name, fix, type_=NodeType.SYSTEM_LIB, layer=Layer.SYSTEM, **kw):
    return Node(id=id_, type=type_, name=name, layer=layer,
                discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                chosen_fix=fix, **kw)


def test_deterministic_core_sections_and_commands():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9", evidence="ev:import:psycopg2"),
    ))
    g = g.with_edge(Edge(src="pkg:psycopg2", dst="syslib:libpq-dev",
                         relation=EdgeType.REQUIRES))
    out = render_build_script(g)

    # one hoisted apt-get update, exactly once
    assert out.count("apt-get update") == 1
    # section headers present and SYSTEM precedes PIP
    assert out.index("==== SYSTEM ====") < out.index("==== PIP ====")
    # the real commands
    assert "apt-get install -y --no-install-recommends libpq-dev" in out
    assert ("python3 -m pip install --break-system-packages --no-deps "
            "psycopg2==2.9.9") in out
    # annotation provenance is present
    assert "#@node pkg:psycopg2  version=2.9.9  requires=syslib:libpq-dev" in out
    assert "evidence=ev:import:psycopg2" in out
    # libpq-dev install line appears before psycopg2 install line (topo)
    assert out.index("libpq-dev\n") < out.index("psycopg2==2.9.9")


def test_no_apt_update_when_no_system_nodes():
    g = DepGraph(nodes=(_pkg("pkg:requests", "requests", "2.31.0"),))
    assert "apt-get update" not in render_build_script(g)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_build_script.py -k core_or_apt -q`
(Or run the file; the new tests fail.)
Expected: FAIL — `AssertionError` (no sections/commands emitted yet).

- [ ] **Step 3: Write minimal implementation**

Replace the body of `build_script.py` with:

```python
from __future__ import annotations

from python_deps.depgraph.emit import _is_reciped, _apt_name, _pip_spec, topo_order
from python_deps.depgraph.schema import DepGraph, Layer, Node, NodeType

_BANNER = (
    "#!/usr/bin/env bash",
    "#",
    "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.",
    "# Edit the graph and re-render; this file is an artifact, not a source.",
    "#",
)
_LAYER_ORDER: tuple[Layer, ...] = tuple(Layer)  # enum order == rank order


def _section_header(layer: Layer) -> str:
    label = layer.value.upper()
    return f"# ==================== {label} ===================="


def _install_command(node: Node) -> str:
    apt = _apt_name(node)
    if apt is not None:
        return f"apt-get install -y --no-install-recommends {apt}"
    if node.type is NodeType.PACKAGE:
        return f"python3 -m pip install --break-system-packages --no-deps {_pip_spec(node)}"
    return node.chosen_fix or ""  # defensive; reciped syslib/tool are always apt


def _annotation(graph: DepGraph, node: Node) -> list[str]:
    from python_deps.depgraph.advise import _best_evidence_line  # lazy: avoid load-order coupling
    toks = [f"#@node {node.id}"]
    if node.version:
        toks.append(f"version={node.version}")
    if _apt_name(node) is not None:
        toks.append(f"provider={node.chosen_fix}")
    reqs = [d.id for d in graph.requires_of(node.id) if _is_reciped(d)]
    toks.append("requires=" + (",".join(sorted(reqs)) if reqs else "-"))
    unblocks = sorted(n.id for n in graph.required_by(node.id) if _is_reciped(n))
    if unblocks:
        toks.append("unblocks=" + ",".join(unblocks))
    if node.build_from_source:
        toks.append("build-from-source")
    if node.layer is Layer.TOOLCHAIN:
        toks.append("toolchain")
    ev = _best_evidence_line(node.evidence)
    if ev:
        toks.append(f"evidence={ev}")
    out = ["  ".join(toks)]
    if node.check_command:
        out.append(f"#@check {node.check_command}")
    return out


def _node_block(graph: DepGraph, node: Node, apt_done: list[bool]) -> list[str]:
    out: list[str] = []
    if _apt_name(node) is not None and not apt_done[0]:
        out += ["export DEBIAN_FRONTEND=noninteractive", "apt-get update"]
        apt_done[0] = True
    out += _annotation(graph, node)
    out.append(_install_command(node))
    return out


def _reciped_in_layer(graph: DepGraph, layer: Layer) -> tuple[Node, ...]:
    nodes = tuple(n for n in graph.nodes if n.layer is layer and _is_reciped(n))
    return topo_order(graph, nodes)


def render_build_script(graph, manual_blocks=()) -> str:
    if graph is None:
        graph = DepGraph()
    parts: list[str] = list(_BANNER) + ["set -Eeuo pipefail"]
    apt_done = [False]
    for layer in _LAYER_ORDER:
        section: list[str] = []
        for node in _reciped_in_layer(graph, layer):
            section += _node_block(graph, node, apt_done)
        if section:
            parts.append("")
            parts.append(_section_header(layer))
            parts.extend(section)
    return "\n".join(parts) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_build_script.py -q`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build_script.py tests/depgraph/test_build_script.py
git commit -m "feat(build-script): deterministic #@node core (tier sections, topo, hoisted apt, --no-deps)"
```

---

## Task 3: `#@need` stubs for non-reciped service/config nodes

**Files:**
- Modify: `src/python_deps/depgraph/build_script.py`
- Test: `tests/depgraph/test_build_script.py`

**Interfaces:**
- Consumes: `_is_reciped`, `graph.requires_of`, `_best_evidence_line`.
- Produces: `_NEED_TYPES`, `_need_block`, `_need_in_layer`; `render_build_script` now appends comment-only `#@need` stubs (no install command) for `CONFIG`/`SERVICE`/`DATA_ASSET` nodes.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_build_script.py
def _need(id_, type_, name, layer, **kw):
    return Node(id=id_, type=type_, name=name, layer=layer,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING, **kw)


def test_need_stubs_are_comment_only():
    g = DepGraph(nodes=(
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9"),
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES,
              check_command="pg_isready -q", evidence="ev:readme:db"),
        _need("config:DATABASE_URL", NodeType.CONFIG, "DATABASE_URL", Layer.CONFIG,
              evidence="ev:settings:DATABASE_URL"),
    ))
    out = render_build_script(g)
    assert "#@need service:postgres  state=missing" in out
    assert "#@check pg_isready -q" in out
    assert "#@need config:DATABASE_URL  state=missing" in out
    # a need carries NO install command — the next non-comment line after the
    # service stub must not be an install (it's the no-command marker comment)
    svc_idx = out.index("#@need service:postgres")
    tail = out[svc_idx:].splitlines()
    assert any("(no command" in ln for ln in tail[:5])
    # services/config render AFTER pip (highest layer rank)
    assert out.index("psycopg2==2.9.9") < out.index("#@need service:postgres")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_build_script.py -k need -q`
Expected: FAIL — `#@need` lines not emitted.

- [ ] **Step 3: Write minimal implementation**

Add `NodeType` need set + helpers, and extend the loop. In `build_script.py`:

```python
_NEED_TYPES: tuple[NodeType, ...] = (NodeType.CONFIG, NodeType.SERVICE, NodeType.DATA_ASSET)


def _need_block(graph: DepGraph, node: Node) -> list[str]:
    from python_deps.depgraph.advise import _best_evidence_line  # lazy
    reqs = [d.id for d in graph.requires_of(node.id) if _is_reciped(d)]
    head = f"#@need {node.id}  state={node.state.value}"
    if reqs:
        head += "  requires=" + ",".join(sorted(reqs))
    out = ["#", head]
    if node.check_command:
        out.append(f"#@check {node.check_command}")
    ev = _best_evidence_line(node.evidence)
    if ev:
        out.append(f"#@evidence {ev}")
    out.append("#     (no command — propose a governed block to satisfy this)")
    return out


def _need_in_layer(graph: DepGraph, layer: Layer, covered: set[str]) -> list[Node]:
    nodes = [n for n in graph.nodes
             if n.layer is layer and n.type in _NEED_TYPES
             and not _is_reciped(n) and n.id not in covered]
    return sorted(nodes, key=lambda n: n.id)
```

Then extend the loop inside `render_build_script` (after the reciped-node loop, before the `if section:` check):

```python
        for node in _need_in_layer(graph, layer, covered=set()):
            section += _need_block(graph, node)
```

(`covered` is an empty set for now; Task 4 wires real coverage from `manual_blocks`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_build_script.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build_script.py tests/depgraph/test_build_script.py
git commit -m "feat(build-script): comment-only #@need stubs for service/config nodes"
```

---

## Task 4: `#@block` rendering for governed manual_blocks + coverage

**Files:**
- Modify: `src/python_deps/depgraph/build_script.py`
- Test: `tests/depgraph/test_build_script.py`

**Interfaces:**
- Consumes: `Block` (block.py) fields `block_id`, `wave`, `commands`, `target_node_ids`, `check_commands`, `evidence_refs`.
- Produces: `_block_block`; `render_build_script` now emits `#@block` lines for `manual_blocks` (grouped by `wave`), and a node covered by a block's `target_node_ids` is NOT also emitted as a `#@need`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_build_script.py
from python_deps.depgraph.block import Block


def test_manual_block_renders_and_suppresses_its_need():
    g = DepGraph(nodes=(
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES,
              check_command="pg_isready -q"),
    ))
    blk = Block(
        block_id="svc:postgres-init", wave="services",
        commands=("pg_ctl init && pg_ctl start",),
        target_node_ids=("service:postgres",),
        check_commands=("pg_isready -q",),
        evidence_refs=("ev:readme:db",),
    )
    out = render_build_script(g, manual_blocks=(blk,))
    # the LLM block is rendered with provenance
    assert ("#@block svc:postgres-init  source=llm-patch  "
            "targets=service:postgres") in out
    assert "pg_ctl init && pg_ctl start" in out
    # the covered node is NOT also a #@need
    assert "#@need service:postgres" not in out


def test_uncovered_need_still_stubbed_with_block_present():
    g = DepGraph(nodes=(
        _need("config:DATABASE_URL", NodeType.CONFIG, "DATABASE_URL", Layer.CONFIG),
    ))
    blk = Block(block_id="svc:x", wave="services", commands=("true",),
                target_node_ids=("service:other",))
    out = render_build_script(g, manual_blocks=(blk,))
    assert "#@need config:DATABASE_URL" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_build_script.py -k block -q`
Expected: FAIL — `#@block` not emitted / need not suppressed.

- [ ] **Step 3: Write minimal implementation**

Add `_block_block` and wire coverage + per-wave grouping into the loop:

```python
def _block_block(block) -> list[str]:
    head = f"#@block {block.block_id}  source=llm-patch"
    if block.target_node_ids:
        head += "  targets=" + ",".join(block.target_node_ids)
    if block.evidence_refs:
        head += "  evidence=" + ",".join(block.evidence_refs)
    out = [head]
    for chk in block.check_commands:
        out.append(f"#@check {chk}")
    out.extend(block.commands)
    return out
```

Update `render_build_script` to compute coverage and group blocks by wave:

```python
def render_build_script(graph, manual_blocks=()) -> str:
    if graph is None:
        graph = DepGraph()
    parts: list[str] = list(_BANNER) + ["set -Eeuo pipefail"]
    covered = {nid for b in manual_blocks for nid in b.target_node_ids}
    blocks_by_wave: dict[str, list] = {}
    for b in manual_blocks:
        blocks_by_wave.setdefault(b.wave, []).append(b)
    apt_done = [False]
    for layer in _LAYER_ORDER:
        section: list[str] = []
        for node in _reciped_in_layer(graph, layer):
            section += _node_block(graph, node, apt_done)
        for b in blocks_by_wave.get(layer.value, ()):
            section += _block_block(b)
        for node in _need_in_layer(graph, layer, covered):
            section += _need_block(graph, node)
        if section:
            parts.append("")
            parts.append(_section_header(layer))
            parts.extend(section)
    return "\n".join(parts) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_build_script.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build_script.py tests/depgraph/test_build_script.py
git commit -m "feat(build-script): #@block rendering for governed manual_blocks + need coverage"
```

---

## Task 5: Manifest header (counts + graph-hash + closure meta)

**Files:**
- Modify: `src/python_deps/depgraph/build_script.py`
- Test: `tests/depgraph/test_build_script.py`

**Interfaces:**
- Consumes: `_is_reciped`; `Node` fields `resolved_python`, `resolved_platform`, `exclude_newer`, `version`, `chosen_fix`.
- Produces: `_graph_hash`, `_closure_meta`, `_manifest`; `render_build_script` now inserts a manifest block between the banner and `set -Eeuo pipefail`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_build_script.py
def test_manifest_counts_hash_and_meta():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9",
             resolved_python="3.11", resolved_platform="linux/amd64",
             exclude_newer="2026-06-01"),
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES),
    ))
    out = render_build_script(g)
    assert "#   nodes: 2 reciped (1 system, 1 pip) + 1 needs" in out
    assert "#   graph-hash: sha256:" in out
    assert "python: 3.11" in out and "platform: linux/amd64" in out
    assert "exclude-newer: 2026-06-01" in out


def test_graph_hash_is_deterministic_and_timestamp_free():
    g = DepGraph(nodes=(_pkg("pkg:a", "a", "1.0"), _pkg("pkg:b", "b", "2.0")))
    g_shuffled = DepGraph(nodes=(_pkg("pkg:b", "b", "2.0"), _pkg("pkg:a", "a", "1.0")))
    assert render_build_script(g) == render_build_script(g_shuffled)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_build_script.py -k manifest_or_hash -q`
Expected: FAIL — manifest lines absent.

- [ ] **Step 3: Write minimal implementation**

Add imports at top: `import hashlib`, `import json`, `from collections import Counter`. Add helpers and call `_manifest` in `render_build_script`:

```python
def _graph_hash(graph: DepGraph) -> str:
    payload = sorted(
        (n.id, n.version or "", n.chosen_fix or "")
        for n in graph.nodes if _is_reciped(n)
    )
    blob = json.dumps(payload, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def _closure_meta(graph: DepGraph) -> dict[str, str]:
    meta: dict[str, str] = {}
    for n in graph.nodes:
        if n.type is not NodeType.PACKAGE:
            continue
        for key, attr in (("python", "resolved_python"),
                          ("platform", "resolved_platform"),
                          ("exclude-newer", "exclude_newer")):
            val = getattr(n, attr, None)
            if val and key not in meta:
                meta[key] = val
    return meta


_TYPE_WORD = {NodeType.SYSTEM_LIB: "system", NodeType.TOOL: "toolchain",
              NodeType.PACKAGE: "pip"}


def _manifest(graph: DepGraph, manual_blocks) -> list[str]:
    reciped = [n for n in graph.nodes if _is_reciped(n)]
    covered = {nid for b in manual_blocks for nid in b.target_node_ids}
    needs = [n for n in graph.nodes
             if n.type in _NEED_TYPES and not _is_reciped(n) and n.id not in covered]
    counts = Counter(_TYPE_WORD.get(n.type, n.type.value) for n in reciped)
    count_str = ", ".join(f"{counts[w]} {w}" for w in ("system", "toolchain", "pip")
                          if counts.get(w))
    meta = _closure_meta(graph)
    meta_str = "   ".join(f"{k}: {v}" for k, v in meta.items())
    lines = list(_BANNER[:-1])  # banner minus its trailing "#"
    lines.append(f"#   nodes: {len(reciped)} reciped ({count_str or 'none'}) "
                 f"+ {len(needs)} needs")
    hash_line = f"#   graph-hash: {_graph_hash(graph)}"
    if meta_str:
        hash_line += "   " + meta_str
    lines.append(hash_line)
    lines.append("#")
    return lines
```

Replace the preamble assembly line in `render_build_script`:

```python
    parts: list[str] = _manifest(graph, manual_blocks) + ["set -Eeuo pipefail"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_build_script.py -q`
Expected: PASS. (Note: Task 1's `test_empty_graph_emits_preamble` still passes — it asserts the banner line and `set -Eeuo pipefail` are present, both of which survive.)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build_script.py tests/depgraph/test_build_script.py
git commit -m "feat(build-script): manifest header (counts, deterministic graph-hash, closure meta)"
```

---

## Task 6: Property suite + golden snapshot

**Files:**
- Modify: `tests/depgraph/test_build_script.py`

**Interfaces:**
- Consumes: `render_build_script`; `compile_replay_blocks` (block.py) for the parity property; `_is_reciped` (emit.py).
- Produces: the byte-exact golden + invariants (§10 of the spec).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/depgraph/test_build_script.py
from python_deps.depgraph.block import compile_replay_blocks
from python_deps.depgraph.emit import _is_reciped


def _rich_graph():
    g = DepGraph(nodes=(
        _apt("syslib:libpq-dev", "libpq-dev", "apt:libpq-dev"),
        _apt("tool:gcc", "gcc", "apt:gcc", type_=NodeType.TOOL, layer=Layer.TOOLCHAIN,
             evidence="ev:build:psycopg2"),
        _pkg("pkg:typing-extensions", "typing-extensions", "4.11.0",
             evidence="ev:resolver"),
        _pkg("pkg:psycopg2", "psycopg2", "2.9.9", build_from_source=True,
             evidence="ev:import:psycopg2"),
        _need("service:postgres", NodeType.SERVICE, "postgres", Layer.SERVICES,
              check_command="pg_isready -q", evidence="ev:readme:db"),
        _need("config:DATABASE_URL", NodeType.CONFIG, "DATABASE_URL", Layer.CONFIG,
              evidence="ev:settings:DATABASE_URL"),
    ))
    for src, dst in (("pkg:psycopg2", "syslib:libpq-dev"),
                     ("pkg:psycopg2", "tool:gcc")):
        g = g.with_edge(Edge(src=src, dst=dst, relation=EdgeType.REQUIRES))
    return g


def test_every_reciped_node_installed_exactly_once():
    g = _rich_graph()
    out = render_build_script(g)
    for n in g.nodes:
        if _is_reciped(n):
            name = n.name if n.type is not NodeType.PACKAGE else f"{n.name}=={n.version}"
            assert out.count(name) >= 1


def test_requires_edge_orders_lines():
    out = render_build_script(_rich_graph())
    assert out.index("libpq-dev\n") < out.index("psycopg2==2.9.9")
    assert out.index("gcc\n") < out.index("psycopg2==2.9.9")


def test_install_target_parity_with_compile_replay_blocks():
    g = _rich_graph()
    replay_targets = {nid for b in compile_replay_blocks(g) for nid in b.target_node_ids}
    out = render_build_script(g)
    # every replay target appears as a #@node annotation in the artifact
    for nid in replay_targets:
        assert f"#@node {nid}" in out
    # and the artifact introduces no extra #@node beyond the reciped set
    node_ids = {ln.split()[1] for ln in out.splitlines() if ln.startswith("#@node ")}
    assert node_ids == replay_targets


def test_golden_snapshot():
    out = render_build_script(_rich_graph())
    expected = (
        "#!/usr/bin/env bash\n"
        "#\n"
        "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.\n"
        "# Edit the graph and re-render; this file is an artifact, not a source.\n"
        "#\n"
        "#   nodes: 4 reciped (1 system, 1 toolchain, 2 pip) + 2 needs\n"
    )
    assert out.startswith(expected)
    # exactly one bootstrap, full structure
    assert out.count("apt-get update") == 1
    assert out.count("export DEBIAN_FRONTEND=noninteractive") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_build_script.py -k "parity or golden or reciped or orders" -q`
Expected: First run may FAIL if any format detail differs (e.g. count words). This is the point — adjust the implementation's wording, NOT the invariants, until green. Most likely all pass if Tasks 2–5 were faithful.

- [ ] **Step 3: Reconcile implementation if needed**

If `test_install_target_parity_with_compile_replay_blocks` fails, the cause is a divergence between `_reciped_in_layer`'s union and `compile_replay_blocks`'s node set — both must derive from `_is_reciped`. Verify no layer is skipped in `_LAYER_ORDER` (it is `tuple(Layer)`, which includes every layer). Do not weaken the test.

- [ ] **Step 4: Run the full module + a regression sweep**

Run: `python -m pytest tests/depgraph/test_build_script.py -q`
Expected: PASS (all)

Run: `python -m pytest tests/depgraph/ -q`
Expected: PASS — confirms the new module didn't perturb `test_compose_script.py` or other depgraph tests (the live-loop path is untouched).

- [ ] **Step 5: Commit**

```bash
git add tests/depgraph/test_build_script.py
git commit -m "test(build-script): golden snapshot + determinism/ordering/parity properties"
```

---

## Self-Review (completed during authoring)

**1. Spec coverage:** §3 function/coexistence → Task 1–2 (new module, untouched siblings). §4 scope split + lifecycle → Tasks 2 (`#@node`), 3 (`#@need`), 4 (`#@block`+coverage); omitted structural nodes → covered by `_reciped_in_layer`/`_need_in_layer` filters. §5 ordering → Task 2 (`_LAYER_ORDER` hard sections + `topo_order` intra-tier). §6 output (hoisted apt, `--no-deps`, headers) → Task 2. §7 three annotation kinds + fields → Tasks 2–4. §8 invariants → Task 6 (determinism, parity, single update, fail-fast via `set -e`, pure projection: no `with_state` call anywhere). §9 manifest → Task 5. §10 tests → Task 6. §11 non-goals → respected (no loop/cert/docker code; siblings untouched). §12 future (self-cert, `--require-hashes`) → intentionally not implemented.

**2. Placeholder scan:** No TBD/TODO; every code step is complete; no "similar to Task N".

**3. Type consistency:** `render_build_script(graph, manual_blocks=()) -> str` stable across all tasks. Helper names (`_node_block`, `_need_block`, `_block_block`, `_reciped_in_layer`, `_need_in_layer`, `_graph_hash`, `_closure_meta`, `_manifest`, `_LAYER_ORDER`, `_NEED_TYPES`) match the File Structure interface block. `apt_done: list[bool]` (mutable flag) is consistent between `_node_block` and `render_build_script`. `covered: set[str]` flows from `manual_blocks` in both `render_build_script` and `_manifest`.

---

## Execution Handoff

See the project's chosen execution path (the requester has asked for a plan review first).
