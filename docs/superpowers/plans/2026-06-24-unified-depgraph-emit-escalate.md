# Unified Dependency Graph — Emit + Escalate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dependency graph *drive* environment construction — deterministically emit the host-certified closure (topo-ordered) into the live container and escalate only the uncertain frontier to the planner LLM with a rich diagnostic packet.

**Architecture:** A new pure module `src/python_deps/depgraph/emit.py` classifies nodes (certified / emittable / frontier), topologically orders the emittable set, and builds a neutral recipe. An envstate glue module `src/envstate/depgraph_live.py` re-certifies the graph against the *live* agent container (reusing `certify_all`) and runs a drain loop that emits → executes via the existing `build_agent.run_recipe` → re-certifies until the emittable set is empty. The orchestrator calls this before `planner.decide` each cycle and re-renders the live graph into the existing `dep_advisory` slot, so `planner.py` is unchanged.

**Tech Stack:** Python 3.10+, frozen dataclasses, pytest. Reuses `python_deps/depgraph/{schema,certify,executor}.py` and `src/envstate/{world_model,build_agent,orchestrator}.py`.

## Global Constraints

- **Immutability:** every graph "mutation" returns a NEW `DepGraph` (repo rule; `schema.py` docstring). Frozen dataclasses only.
- **Layering:** `src/python_deps/depgraph/` must NOT import from `src.envstate` (`world_model.py:22`). `emit.py` returns a neutral `EmitStep`; conversion to `RecipeStep` happens in `src/envstate/depgraph_live.py`.
- **Host owns truth:** a node's `state` is flipped ONLY by `certify`/`certify_all` running its `check_command` (`certify.py` docstring, `schema.py:35-41`). Emit never sets `SATISFIED`; mutations go through `run_recipe`, certification through the read-only executor.
- **Purity of `depgraph/`:** `emit.py` does no Docker, no network, no subprocess — pure functions over `DepGraph` (mirrors `probe.py`/`advise.py`).
- **Off-state byte-identical:** with the new `enable_dep_emit` flag off, every prompt and code path is byte-for-byte unchanged from today (the existing invariant for `enable_dep_graph`/`enable_contract_graph`).
- **Reuse, no new executor:** emitted recipes run through the existing `build_agent.run_recipe`; re-certification reuses `certify_all` with a thin adapter over the orchestrator's `exec_readonly`.
- **No git in this run:** implementers MUST NOT run any `git` command (the branch carries unrelated uncommitted WIP in files this plan touches; the user commits afterward). Run only the `pytest` commands shown. Do NOT use worktree isolation.
- **Verify line numbers:** all `file:line` citations are approximate — the implementer MUST re-read the file and locate the real anchor before editing.

---

## Review Resolutions (MANDATORY — apply alongside the referenced task)

These fixes came from an adversarial Opus/Sonnet review and OVERRIDE the task text where they conflict. Each task's implementer must apply the resolutions keyed to that task.

**R1 (Task 6) — `EdgeType` import + frontier scoping + precedence header.**
- Add `EdgeType` to the schema import in `src/python_deps/depgraph/advise.py` (the line currently `from python_deps.depgraph.schema import DepGraph, Layer, Node, NodeType, State`).
- In `render_depgraph_planner`, base the FRONTIER list on the emit classifier, NOT on "all MISSING non-TEST nodes" — so only actionable installable nodes appear (un-resolvable Import/Runtime nodes must not show as frontier). Add `from python_deps.depgraph.emit import partition` and use `partition(graph).frontier`:
  ```python
  frontier = sorted(
      partition(graph).frontier,
      key=lambda n: (_LAYER_RANK.get(n.layer, 9), n.name),
  )
  ```
- Change the header to declare precedence (resolves root cause #4 dilution — the depgraph owns deps/system, the contract graph keeps build/tests/config):
  ```python
  _PLANNER_HEADER = ("[DEPENDENCY GRAPH - unified * host-certified in live container]"
                     "  (authoritative for deps/system/toolchain; build/tests/config -> open_problems)")
  ```
- Add a `test_frontier_excludes_non_installable_imports` case to `tests/depgraph/test_advise_planner_packet.py`: a MISSING `Import` node must NOT appear in the rendered FRONTIER.

**R2 (Task 7) — fake build agent must execute.** `_FakeBuildAgent.run_recipe` must call `sandbox_execute(s.command)` for every step (the install side effects drive the `installed`/`done` sets the test's `exec_readonly` reads):
```python
def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
    self.recipes.append(recipe)
    cmds = []
    for s in recipe.steps:
        sandbox_execute(s.command)          # propagate install side effects
        cmds.append(CommandRecord(s.command, 0, "ok"))
    return TaskReport("emit", "done", tuple(cmds), "ok", completed_steps=len(recipe.steps))
```

**R3 (Task 8) — fake fix + the CRITICAL synthesis-payoff fix + exec guard + new test.**
- (a) Apply the same `_FakeBuildAgent` fix (R2) in `tests/test_run_v1_dep_emit.py`.
- (b) **Synthesis payoff (critical):** emit-certified packages must reach the Dockerfile synthesizer, which reads `self._final_installed = final_map.installed`. The Maintainer is NOT called for emit reports, so `_dep_emit_phase` must fold emit-certified `SATISFIED` Package nodes into `installed` itself. Final `_dep_emit_phase`:
  ```python
  def _dep_emit_phase(cycle: int) -> None:
      nonlocal current_map, global_step
      if not enable_dep_emit or current_map.dep_graph is None:
          return
      if exec_readonly is None:                      # R3(c): no certify path -> no emit
          return
      from python_deps.depgraph.schema import NodeType, State
      from src.envstate.world_model import Fact
      graph = certify_refresh(current_map.dep_graph, exec_readonly, cycle)
      graph, _reports, steps = emit_drain(
          graph, build_agent, sandbox_execute, ledger, exec_readonly,
          step_offset=global_step, cycle=cycle,
      )
      global_step += steps
      # Fold emit-certified packages into installed so the synthesizer's closure
      # recipe includes them even when the planner finalizes immediately.
      sat = tuple(Fact(n.name, n.version or "") for n in graph.nodes
                  if n.type is NodeType.PACKAGE and n.state is State.SATISFIED)
      have = {f.name for f in current_map.installed}
      installed = current_map.installed + tuple(f for f in sat if f.name not in have)
      advisory = render_depgraph_planner(graph)
      current_map = merge_map(
          current_map, dep_graph=graph, dep_advisory=advisory, installed=installed,
      )
  ```
- (c) exec_readonly guard: the `if exec_readonly is None: return` shown above.
- (d) Add `test_emit_certified_packages_land_in_installed` to `tests/test_run_v1_dep_emit.py`: after a run where emit certifies `flask`, assert `Fact("flask", ...)` is present in `final.installed` (so the closure recipe will include it).

**R4 (Task 9) — correct the flag wiring (the task's locations are wrong).**
- `agent.py`: add `or enable_dep_emit` to the `self.enable_v1 = enable_v1 or ...` OR-chain AND set `self.enable_dep_graph = True` when emit is on, BOTH placed BEFORE `self.enable_v1` is computed. Also add the constructor `__init__` param `enable_dep_emit: bool = False`, the argparse flag `--enable-dep-emit` (next to `--enable-dep-graph`), the `enable_dep_emit=args.enable_dep_emit` in the CLI→constructor call, and `enable_dep_emit=self.enable_dep_emit` in the `run_v1(...)` call.
- `run_repo2run_benchmark.py`: there is NO `parser.add_argument("--enable-dep-graph")` — the flag flows via `_ARM_PRESETS` + the command builder. Add `"enable_dep_emit": True` to a new arm preset `v1gde` (clone `v1gd`) and forward it in the command-builder where `enable_dep_graph` is forwarded (`command.append("--enable-dep-emit")`).
- `run_rat_benchmark.py`: add a `v1gde` arm that mirrors `v1gd` and additionally sets `DOCKERAGENT_ENABLE_DEP_EMIT=1` (read the file to match its arm→env-var mechanism). This is required to run the spec's A/B.
- `multi_docker_eval_adapter.py`: add the `DOCKERAGENT_ENABLE_DEP_EMIT` env bridge and pass `enable_dep_emit=` into the `DockerAgent(...)` call (mirror `DOCKERAGENT_ENABLE_DEP_GRAPH`).

**R5 (Task 10) — fix the transposed args.** In BOTH `emit_drain(...)` calls the order must be `(graph, build_agent, sandbox_execute, ledger, exec_readonly, ...)`:
```python
emit_drain(
    g, ba,
    lambda c: (ex.run(c).ok, ""),                         # sandbox_execute
    ActionLedger(),                                       # ledger
    lambda c: (ex.run(c).returncode, ex.run(c).stdout),  # exec_readonly
    step_offset=0, cycle=1,
)
```
Delete the misleading "Note" paragraph at the end of Task 10.

**R6 (Task 3) — drop the dead-path test.** Remove `test_build_recipe_unversioned_package_uses_bare_name` (a `version=None` package is FRONTIER and can never reach `build_recipe`). Instead add `test_partition_unversioned_package_is_frontier` to `tests/depgraph/test_emit_partition.py` asserting a `version=None` PACKAGE lands in `partition().frontier`. Keep `build_recipe`'s defensive bare-name handling (no code change there).

---

### Task 1: `partition()` — classify installable nodes

**Files:**
- Create: `src/python_deps/depgraph/emit.py`
- Test: `tests/depgraph/test_emit_partition.py`

**Interfaces:**
- Consumes: `DepGraph`, `Node`, `NodeType`, `State`, `EdgeType` from `python_deps.depgraph.schema`; `DepGraph.requires_of` (`schema.py:257`).
- Produces:
  - `@dataclass(frozen=True) class Partition: certified: tuple[Node, ...]; emittable: tuple[Node, ...]; frontier: tuple[Node, ...]`
  - `def partition(graph: DepGraph) -> Partition`
  - helpers `_conflicted_ids(graph) -> set[str]`, `_toolchain_ready(graph, pkg: Node) -> bool`, `_is_emittable(graph, node: Node, conflicted: set[str]) -> bool`, and constant `_INSTALLABLE: tuple[NodeType, ...]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_emit_partition.py
from python_deps.depgraph.emit import partition
from python_deps.depgraph.schema import (
    DepGraph, Edge, EdgeType, Layer, Node, NodeType, State, DiscoveredBy,
)


def _pkg(name, *, state=State.MISSING, version="1.0", bfs=None):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=state, version=version,
                check_command=f'python -c "import {name}"', build_from_source=bfs)


def _tool(name, *, state=State.MISSING, apt="build-essential"):
    return Node(id=f"tool:{name}", type=NodeType.TOOL, name=name, layer=Layer.TOOLCHAIN,
                discovered_by=DiscoveredBy.PROBE, state=state,
                check_command=f"command -v {name}",
                fix_candidates=(f"apt:{apt}",), chosen_fix=f"apt:{apt}")


def test_partition_buckets_basic():
    g = DepGraph(nodes=(
        _pkg("flask", state=State.SATISFIED),     # certified
        _pkg("numpy"),                            # emittable (resolved, has version)
        _pkg("ghost", version=None),              # frontier (unresolved)
        _tool("gcc"),                             # emittable (single apt fix)
    ))
    p = partition(g)
    assert {n.name for n in p.certified} == {"flask"}
    assert {n.name for n in p.emittable} == {"numpy", "gcc"}
    assert {n.name for n in p.frontier} == {"ghost"}


def test_partition_conflict_pair_is_frontier():
    g = DepGraph(
        nodes=(_pkg("fastavro"), _pkg("avro")),
        edges=(Edge(src="pkg:fastavro", dst="pkg:avro", relation=EdgeType.CONFLICTS_WITH),),
    )
    p = partition(g)
    assert {n.name for n in p.frontier} == {"fastavro", "avro"}
    assert p.emittable == ()


def test_partition_build_from_source_waits_for_toolchain():
    lxml = _pkg("lxml", bfs=True)
    libxml = Node(id="syslib:libxml2", type=NodeType.SYSTEM_LIB, name="libxml2.so.2",
                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                  check_command="ldconfig -p | grep libxml2",
                  fix_candidates=("apt:libxml2-dev",), chosen_fix="apt:libxml2-dev")
    g = DepGraph(
        nodes=(lxml, libxml),
        edges=(Edge(src="pkg:lxml", dst="syslib:libxml2", relation=EdgeType.REQUIRES),),
    )
    # toolchain MISSING -> lxml is frontier, libxml is emittable
    p = partition(g)
    assert {n.name for n in p.emittable} == {"libxml2.so.2"}
    assert {n.name for n in p.frontier} == {"lxml"}
    # toolchain SATISFIED -> lxml becomes emittable
    g2 = g.with_node(libxml.with_state(State.SATISFIED))
    p2 = partition(g2)
    assert "lxml" in {n.name for n in p2.emittable}


def test_partition_ignores_non_installable_types():
    g = DepGraph(nodes=(
        Node(id="test:goal", type=NodeType.TEST, name="repo_tests_pass", layer=Layer.TESTS,
             discovered_by=DiscoveredBy.GOAL, state=State.MISSING),
        Node(id="imp:foo", type=NodeType.IMPORT, name="foo", layer=Layer.NAMING,
             discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING),
    ))
    p = partition(g)
    assert p.certified == () and p.emittable == () and p.frontier == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_emit_partition.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.emit'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/emit.py
"""Pure emit core: classify the graph and turn the certified closure into a recipe.

This module is the deterministic counterpart to the LLM recipe loop: it decides
which MISSING nodes the host can install without judgement (EMITTABLE), which
require the LLM (FRONTIER), and emits an ordered, layer-correct recipe for the
emittable set. Pure with respect to its inputs — no Docker, no network, no
subprocess (mirrors probe.py / advise.py). Returns neutral EmitStep objects so
this package keeps its zero dependency on src.envstate (world_model.py:22).
"""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.schema import (
    DepGraph,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)

# Node types the host can directly install. Import/Test/Project/Runtime are
# structural — satisfied via their Package (naming relink) or out of scope here.
_INSTALLABLE: tuple[NodeType, ...] = (
    NodeType.PACKAGE,
    NodeType.SYSTEM_LIB,
    NodeType.TOOL,
)


@dataclass(frozen=True)
class Partition:
    certified: tuple[Node, ...]
    emittable: tuple[Node, ...]
    frontier: tuple[Node, ...]


def _conflicted_ids(graph: DepGraph) -> set[str]:
    """Node ids touched by a conflicts_with edge (uv unsat core) — never emit."""
    ids: set[str] = set()
    for e in graph.edges:
        if e.relation is EdgeType.CONFLICTS_WITH:
            ids.add(e.src)
            ids.add(e.dst)
    return ids


def _toolchain_ready(graph: DepGraph, pkg: Node) -> bool:
    """True when every SystemLib/Tool this package requires is already SATISFIED."""
    for dep in graph.requires_of(pkg.id):
        if dep.type in (NodeType.SYSTEM_LIB, NodeType.TOOL) and dep.state is not State.SATISFIED:
            return False
    return True


def _is_emittable(graph: DepGraph, node: Node, conflicted: set[str]) -> bool:
    if node.state is not State.MISSING:
        return False
    if node.id in conflicted:
        return False
    if node.type is NodeType.PACKAGE:
        if not node.version:           # unresolved -> the LLM's call
            return False
        if node.build_from_source and not _toolchain_ready(graph, node):
            return False               # wait for its toolchain to certify
        return True
    if node.type in (NodeType.SYSTEM_LIB, NodeType.TOOL):
        return bool(node.chosen_fix and node.chosen_fix.startswith("apt:"))
    return False


def partition(graph: DepGraph) -> Partition:
    """Classify installable nodes into certified / emittable / frontier."""
    conflicted = _conflicted_ids(graph)
    certified: list[Node] = []
    emittable: list[Node] = []
    frontier: list[Node] = []
    for n in graph.nodes:
        if n.type not in _INSTALLABLE:
            continue
        if n.state is State.SATISFIED:
            certified.append(n)
        elif _is_emittable(graph, n, conflicted):
            emittable.append(n)
        elif n.state is State.MISSING:
            frontier.append(n)
        # UNKNOWN with no decision: neither emitted nor escalated.
    return Partition(tuple(certified), tuple(emittable), tuple(frontier))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_emit_partition.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/emit.py tests/depgraph/test_emit_partition.py
git commit -m "feat(depgraph): partition nodes into certified/emittable/frontier"
```

---

### Task 2: `topo_order()` — layer-correct, cycle-safe ordering

**Files:**
- Modify: `src/python_deps/depgraph/emit.py`
- Test: `tests/depgraph/test_emit_topo.py`

**Interfaces:**
- Consumes: `Partition.emittable` (Task 1), `DepGraph.edges`, `EdgeType.REQUIRES`, `Layer`.
- Produces: `def topo_order(graph: DepGraph, nodes: tuple[Node, ...]) -> tuple[Node, ...]` — dependency-first order (a required node precedes the node that requires it), `Layer` rank then name as tie-break, deterministic, never raises on a cycle.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_emit_topo.py
from python_deps.depgraph.emit import topo_order
from python_deps.depgraph.schema import (
    DepGraph, Edge, EdgeType, Layer, Node, NodeType, State, DiscoveredBy,
)


def _n(nid, name, layer, ntype=NodeType.PACKAGE):
    return Node(id=nid, type=ntype, name=name, layer=layer,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="1.0")


def test_topo_dependency_before_dependent():
    a = _n("pkg:a", "a", Layer.PIP)
    b = _n("pkg:b", "b", Layer.PIP)
    # a requires b  =>  b must come before a
    g = DepGraph(nodes=(a, b),
                 edges=(Edge(src="pkg:a", dst="pkg:b", relation=EdgeType.REQUIRES),))
    order = [n.name for n in topo_order(g, (a, b))]
    assert order.index("b") < order.index("a")


def test_topo_layer_rank_tiebreak():
    tool = _n("tool:gcc", "gcc", Layer.TOOLCHAIN, NodeType.TOOL)
    pkg = _n("pkg:z", "z", Layer.PIP)
    g = DepGraph(nodes=(pkg, tool))  # no edges -> pure layer-rank order
    order = [n.name for n in topo_order(g, (pkg, tool))]
    assert order == ["gcc", "z"]  # TOOLCHAIN(2) before PIP(3)


def test_topo_cycle_is_deterministic_not_crash():
    a = _n("pkg:a", "a", Layer.PIP)
    b = _n("pkg:b", "b", Layer.PIP)
    g = DepGraph(nodes=(a, b), edges=(
        Edge(src="pkg:a", dst="pkg:b", relation=EdgeType.REQUIRES),
        Edge(src="pkg:b", dst="pkg:a", relation=EdgeType.REQUIRES),
    ))
    order = [n.name for n in topo_order(g, (a, b))]
    assert sorted(order) == ["a", "b"]  # all present, no crash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_emit_topo.py -v`
Expected: FAIL with `ImportError: cannot import name 'topo_order'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/python_deps/depgraph/emit.py`:

```python
# Bottom-up execution rank (matches certify._LAYER_ORDER / advise._LAYER_RANK).
_LAYER_RANK: dict[Layer, int] = {
    Layer.INTERPRETER: 0,
    Layer.SYSTEM: 1,
    Layer.TOOLCHAIN: 2,
    Layer.PIP: 3,
    Layer.NAMING: 4,
    Layer.RUNTIME: 5,
    Layer.TESTS: 6,
}


def topo_order(graph: DepGraph, nodes: tuple[Node, ...]) -> tuple[Node, ...]:
    """Order ``nodes`` dependency-first (a required node before its dependent).

    Kahn's algorithm over ``requires`` edges restricted to the node set; ties
    broken by (layer rank, name) for reproducibility. On a cycle (should not
    happen for a resolved closure) the remaining nodes are emitted in
    layer-rank+name order rather than raising — emit must never crash a run.
    """
    ids = {n.id for n in nodes}
    by_id = {n.id: n for n in nodes}
    deps: dict[str, set[str]] = {nid: set() for nid in ids}
    for e in graph.edges:
        if e.relation is EdgeType.REQUIRES and e.src in ids and e.dst in ids:
            deps[e.src].add(e.dst)

    ordered: list[Node] = []
    placed: set[str] = set()
    remaining = set(ids)
    while remaining:
        ready = [nid for nid in remaining if deps[nid] <= placed]
        if not ready:                      # cycle — emit the rest deterministically
            ready = list(remaining)
        ready.sort(key=lambda nid: (_LAYER_RANK.get(by_id[nid].layer, 9), by_id[nid].name))
        nxt = ready[0]
        ordered.append(by_id[nxt])
        placed.add(nxt)
        remaining.discard(nxt)
    return tuple(ordered)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_emit_topo.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/emit.py tests/depgraph/test_emit_topo.py
git commit -m "feat(depgraph): topo-order emittable nodes (layer-rank, cycle-safe)"
```

---

### Task 3: `build_recipe()` — emittable nodes → neutral recipe steps

**Files:**
- Modify: `src/python_deps/depgraph/emit.py`
- Test: `tests/depgraph/test_emit_build_recipe.py`

**Interfaces:**
- Consumes: ordered `tuple[Node, ...]` from `topo_order` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class EmitStep: kind: str; command: str; target_node_ids: tuple[str, ...]` (`kind` is an `AttemptKind` value string: `"system_install"` / `"python_install"`).
  - `def build_recipe(graph: DepGraph, ordered: tuple[Node, ...]) -> tuple[EmitStep, ...]` — at most one apt step (SystemLib/Tool) then at most one pinned-closure pip step (D2).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_emit_build_recipe.py
from python_deps.depgraph.emit import build_recipe, EmitStep
from python_deps.depgraph.schema import (
    DepGraph, Layer, Node, NodeType, State, DiscoveredBy,
)


def _pkg(name, version="1.0"):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=version)


def _tool(name, apt):
    return Node(id=f"tool:{name}", type=NodeType.TOOL, name=name, layer=Layer.TOOLCHAIN,
                discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                fix_candidates=(f"apt:{apt}",), chosen_fix=f"apt:{apt}")


def test_build_recipe_apt_then_pip_pinned():
    g = DepGraph()
    ordered = (_tool("gcc", "build-essential"), _pkg("numpy", "1.26.4"), _pkg("lxml", "5.1.0"))
    steps = build_recipe(g, ordered)
    assert [s.kind for s in steps] == ["system_install", "python_install"]
    assert steps[0].command == "apt-get update && apt-get install -y build-essential"
    assert steps[1].command == "python -m pip install numpy==1.26.4 lxml==5.1.0"
    assert steps[1].target_node_ids == ("pkg:numpy", "pkg:lxml")


def test_build_recipe_dedupes_apt_names():
    g = DepGraph()
    ordered = (_tool("gcc", "build-essential"), _tool("g++", "build-essential"))
    steps = build_recipe(g, ordered)
    assert steps[0].command == "apt-get update && apt-get install -y build-essential"


def test_build_recipe_unversioned_package_uses_bare_name():
    g = DepGraph()
    steps = build_recipe(g, (_pkg("requests", version=None),))
    assert steps[0].command == "python -m pip install requests"


def test_build_recipe_empty_when_nothing_emittable():
    assert build_recipe(DepGraph(), ()) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_emit_build_recipe.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_recipe'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/python_deps/depgraph/emit.py`:

```python
_APT_PREFIX = "apt:"


@dataclass(frozen=True)
class EmitStep:
    kind: str                      # AttemptKind value: system_install | python_install
    command: str
    target_node_ids: tuple[str, ...]


def _apt_name(node: Node) -> str | None:
    if node.chosen_fix and node.chosen_fix.startswith(_APT_PREFIX):
        return node.chosen_fix[len(_APT_PREFIX):]
    return None


def _pip_spec(node: Node) -> str:
    return f"{node.name}=={node.version}" if node.version else node.name


def build_recipe(graph: DepGraph, ordered: tuple[Node, ...]) -> tuple[EmitStep, ...]:
    """Turn the topo-ordered emittable set into at most two steps (D2):

    1. one apt step for all SystemLib/Tool nodes (dedup apt names), and
    2. one pinned-closure pip step for all Package nodes (resolver-consistent).

    Cross-layer / build-from-source ordering is handled by the drain loop across
    iterations, so a single pass needs only apt-before-pip.
    """
    syslibs = [n for n in ordered if n.type in (NodeType.SYSTEM_LIB, NodeType.TOOL)]
    packages = [n for n in ordered if n.type is NodeType.PACKAGE]
    steps: list[EmitStep] = []

    if syslibs:
        names: list[str] = []
        for n in syslibs:
            apt = _apt_name(n)
            if apt and apt not in names:
                names.append(apt)
        if names:
            steps.append(EmitStep(
                kind="system_install",
                command="apt-get update && apt-get install -y " + " ".join(names),
                target_node_ids=tuple(n.id for n in syslibs),
            ))

    if packages:
        specs = " ".join(_pip_spec(n) for n in packages)
        steps.append(EmitStep(
            kind="python_install",
            command="python -m pip install " + specs,
            target_node_ids=tuple(n.id for n in packages),
        ))
    return tuple(steps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_emit_build_recipe.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/emit.py tests/depgraph/test_emit_build_recipe.py
git commit -m "feat(depgraph): build neutral apt+pip recipe from emittable closure"
```

---

### Task 4: Carry the live graph on the map — `merge_map(dep_graph=...)`

**Files:**
- Modify: `src/envstate/world_model.py:207-248` (the `merge_map` signature + body)
- Test: `tests/test_world_model_merge_dep_graph.py`

**Interfaces:**
- Consumes: existing `merge_map` (`world_model.py:207`) and `WorldModelMap.dep_graph` (`world_model.py:100`).
- Produces: `merge_map(..., dep_graph: "DepGraph | None" = None)` — when a graph is passed it replaces `current.dep_graph`; when omitted (`None`) the field is unchanged (same convention as every other kwarg). This is what lets CERTIFY/EMIT write the evolving graph back each cycle (today it is fixed at `initial_map`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_world_model_merge_dep_graph.py
from src.envstate.world_model import merge_map, initial_map
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy


def _graph(state):
    return DepGraph(nodes=(Node(id="pkg:flask", type=NodeType.PACKAGE, name="flask",
                                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                                state=state, version="3.0.0"),))


def test_merge_map_replaces_dep_graph_when_passed():
    m = initial_map("img", "/app", "python 3.12", "pip", (), dep_graph=_graph(State.MISSING))
    m2 = merge_map(m, dep_graph=_graph(State.SATISFIED))
    assert m2.dep_graph.get("pkg:flask").state is State.SATISFIED
    assert m.dep_graph.get("pkg:flask").state is State.MISSING  # original untouched


def test_merge_map_leaves_dep_graph_when_omitted():
    g = _graph(State.MISSING)
    m = initial_map("img", "/app", "python 3.12", "pip", (), dep_graph=g)
    m2 = merge_map(m, done_flag=True)
    assert m2.dep_graph is g
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_world_model_merge_dep_graph.py -v`
Expected: FAIL with `TypeError: merge_map() got an unexpected keyword argument 'dep_graph'`

- [ ] **Step 3: Write minimal implementation**

In `src/envstate/world_model.py`, add the parameter to `merge_map`'s signature (after `dep_advisory: str | None = None,` at line 224):

```python
    dep_advisory: str | None = None,
    dep_graph: "DepGraph | None" = None,
) -> WorldModelMap:
```

And add the matching line inside the `dataclasses.replace(...)` call (after the `dep_advisory=...` line at 247):

```python
        dep_advisory=dep_advisory if dep_advisory is not None else current.dep_advisory,
        dep_graph=dep_graph if dep_graph is not None else current.dep_graph,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_world_model_merge_dep_graph.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/envstate/world_model.py tests/test_world_model_merge_dep_graph.py
git commit -m "feat(envstate): let merge_map carry the evolving dep_graph"
```

---

### Task 5: `certify_refresh()` — re-certify the graph against the LIVE container

**Files:**
- Create: `src/envstate/depgraph_live.py`
- Test: `tests/test_depgraph_live_certify.py`

**Interfaces:**
- Consumes: `certify_all(graph, executor, cycle)` (`certify.py:73`); `CommandResult` (`executor.py:21`); the orchestrator's `exec_readonly` callable `(cmd) -> (rc: int, out: str)` (`orchestrator.py:81`).
- Produces:
  - `class _ReadonlyExecAdapter` implementing the `Executor` protocol (`run(command, *, timeout=300) -> CommandResult`) over an `exec_readonly` callable.
  - `def certify_refresh(graph: "DepGraph | None", exec_readonly, cycle: int) -> "DepGraph | None"` — returns the graph with states re-flipped by host checks; a no-op (returns input) when graph/exec_readonly is falsy.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_depgraph_live_certify.py
from src.envstate.depgraph_live import certify_refresh
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy


def _pkg(name):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="1.0",
                check_command=f'python -c "import {name}"')


def test_certify_refresh_flips_state_from_live_checks():
    g = DepGraph(nodes=(_pkg("flask"), _pkg("ghost")))

    def exec_readonly(cmd):
        # flask import succeeds (rc 0); ghost fails (rc 1)
        return (0, "") if "import flask" in cmd else (1, "ModuleNotFoundError: ghost")

    out = certify_refresh(g, exec_readonly, cycle=3)
    assert out.get("pkg:flask").state is State.SATISFIED
    assert out.get("pkg:flask").certified_cycle == 3
    assert out.get("pkg:ghost").state is State.MISSING


def test_certify_refresh_noop_when_disabled_or_empty():
    g = DepGraph(nodes=(_pkg("flask"),))
    assert certify_refresh(g, None, cycle=0) is g          # no executor
    assert certify_refresh(None, lambda c: (0, ""), 0) is None  # no graph
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_depgraph_live_certify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.depgraph_live'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/envstate/depgraph_live.py
"""Live integration glue: drive the dependency graph against the running agent
container. Re-certify each node via host checks (CERTIFY) and run the emit drain
loop (EMIT). Mutations go through build_agent.run_recipe; certification through a
read-only executor — keeping the host-owns-truth invariant (certify.py).

This is the ONLY module allowed to bridge python_deps.depgraph (pure) and
src.envstate (the agent loop).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.executor import CommandResult

if TYPE_CHECKING:
    from python_deps.depgraph.schema import DepGraph


class _ReadonlyExecAdapter:
    """Adapt the orchestrator's ``exec_readonly`` callable to the Executor protocol.

    ``certify_all`` only needs ``run(cmd).ok`` and ``.stderr``; check_commands are
    read-only presence checks (``command -v`` / ``ldconfig -p | grep`` /
    ``python -c import``), so the read-only path is the correct executor.
    """

    def __init__(self, exec_readonly: Callable[[str], tuple[int, str]]) -> None:
        self._f = exec_readonly

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        rc, out = self._f(command)
        return CommandResult(command=command, returncode=rc, stdout=out, stderr=out)


def certify_refresh(graph, exec_readonly, cycle: int):
    """Re-flip every node's state via a host check in the live container.

    No-op (returns the input) when the graph is empty/None or no read-only
    executor is available — so the feature degrades gracefully.
    """
    if graph is None or not graph.nodes or exec_readonly is None:
        return graph
    return certify_all(graph, _ReadonlyExecAdapter(exec_readonly), cycle=cycle)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_depgraph_live_certify.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/envstate/depgraph_live.py tests/test_depgraph_live_certify.py
git commit -m "feat(envstate): certify_refresh — re-certify dep graph in live container"
```

---

### Task 6: `render_depgraph_planner()` — unified view + frontier diagnostic packet

**Files:**
- Modify: `src/python_deps/depgraph/advise.py` (add the new render + helpers; keep `render_dep_graph_advisory` for back-compat)
- Test: `tests/depgraph/test_advise_planner_packet.py`

**Interfaces:**
- Consumes: `DepGraph`, `Node`, `State`, `NodeType`, `EdgeType`; existing helpers `_best_evidence_line`, `_LAYER_RANK` (`advise.py`); `DepGraph.required_by`/`requires_of`.
- Produces: `def render_depgraph_planner(graph: DepGraph, changed_ids: frozenset[str] = frozenset()) -> str` — a relevance-gated string: GOAL line, CERTIFIED counts, then a rich packet per FRONTIER node (causal chain, conflict bounds, platform mismatch, full attempts, "(re-checked this cycle)" marker for `changed_ids`). Returns `""` for an empty graph.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_advise_planner_packet.py
from python_deps.depgraph.advise import render_depgraph_planner
from python_deps.depgraph.schema import (
    DepGraph, Edge, EdgeType, Layer, Node, NodeType, State, DiscoveredBy, Attempt,
)


def test_packet_has_chain_attempts_and_conflict():
    goal = Node(id="test:goal", type=NodeType.TEST, name="repo_tests_pass",
                layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL, state=State.MISSING)
    proj = Node(id="proj:app", type=NodeType.PROJECT, name="app", layer=Layer.PIP,
                discovered_by=DiscoveredBy.GOAL, state=State.MISSING)
    lxml = Node(id="pkg:lxml", type=NodeType.PACKAGE, name="lxml", layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=None,
                attempts=(Attempt(command="pip install lxml", outcome="failed", cycle=2),))
    g = DepGraph(
        nodes=(goal, proj, lxml),
        edges=(
            Edge(src="test:goal", dst="proj:app", relation=EdgeType.REQUIRES),
            Edge(src="proj:app", dst="pkg:lxml", relation=EdgeType.REQUIRES),
        ),
    )
    out = render_depgraph_planner(g)
    assert "FRONTIER" in out
    assert "lxml" in out
    assert "chain: lxml <- app <- repo_tests_pass" in out
    assert "pip install lxml -> failed" in out


def test_packet_conflict_bounds_rendered():
    a = Node(id="pkg:fastavro", type=NodeType.PACKAGE, name="fastavro", layer=Layer.PIP,
             discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=None)
    b = Node(id="pkg:avro", type=NodeType.PACKAGE, name="avro", layer=Layer.PIP,
             discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=None)
    g = DepGraph(nodes=(a, b), edges=(
        Edge(src="pkg:fastavro", dst="pkg:avro", relation=EdgeType.CONFLICTS_WITH,
             data={"summary": "fastavro needs X>=2, avro needs X<2"}),
    ))
    out = render_depgraph_planner(g)
    assert "conflict" in out.lower()
    assert "fastavro needs X>=2, avro needs X<2" in out


def test_certified_collapses_to_counts_and_empty_graph_blank():
    sat = Node(id="pkg:flask", type=NodeType.PACKAGE, name="flask", layer=Layer.PIP,
               discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED, version="3.0.0")
    out = render_depgraph_planner(DepGraph(nodes=(sat,)))
    assert "CERTIFIED" in out and "pip 1" in out
    assert "flask" not in out          # certified nodes are counts, not lines
    assert render_depgraph_planner(DepGraph()) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_advise_planner_packet.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_depgraph_planner'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/python_deps/depgraph/advise.py`:

```python
_PLANNER_HEADER = "[DEPENDENCY GRAPH - unified * host-certified in live container]"


def _chain_to_goal(graph: DepGraph, node: Node, limit: int = 6) -> str:
    """Render the transitive required_by chain up to a Project/Test root.

    'lxml <- app <- repo_tests_pass'. Picks one predecessor per hop (the first by
    name) — enough to show the planner why the node matters. Cycle-guarded.
    """
    chain = [node.name]
    seen = {node.id}
    cur = node
    for _ in range(limit):
        preds = [p for p in graph.required_by(cur.id) if p.id not in seen]
        if not preds:
            break
        cur = sorted(preds, key=lambda p: p.name)[0]
        chain.append(cur.name)
        seen.add(cur.id)
        if cur.type in (NodeType.PROJECT, NodeType.TEST):
            break
    return " <- ".join(chain)


def _conflict_note(graph: DepGraph, node: Node) -> str | None:
    for e in graph.edges:
        if e.relation is EdgeType.CONFLICTS_WITH and node.id in (e.src, e.dst):
            other = e.dst if e.src == node.id else e.src
            other_node = graph.get(other)
            other_name = other_node.name if other_node else other
            summary = e.data.get("summary") if e.data else None
            detail = f" ({summary})" if summary else ""
            return f"conflict: {node.name} vs {other_name}{detail}"
    return None


def _platform_note(node: Node) -> str | None:
    if node.resolved_python or node.resolved_platform:
        return f"resolved for: {node.resolved_python or '?'} / {node.resolved_platform or '?'}"
    return None


def render_depgraph_planner(
    graph: DepGraph, changed_ids: frozenset[str] = frozenset()
) -> str:
    """Unified planner-facing render: certified counts + a rich frontier packet."""
    if not graph.nodes:
        return ""

    lines = [_PLANNER_HEADER]

    goal = next((n for n in graph.nodes if n.type is NodeType.TEST), None)
    if goal is not None:
        lines.append(f"GOAL     {goal.name:24} {goal.state.value}")

    satisfied = [n for n in graph.nodes if n.state is State.SATISFIED]
    if satisfied:
        counts: dict[str, int] = {}
        for n in satisfied:
            counts[n.layer.value] = counts.get(n.layer.value, 0) + 1
        summary = " * ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        lines.append(f"CERTIFIED  {summary}   (host-checked in live container)")

    frontier = sorted(
        (n for n in graph.nodes
         if n.state is State.MISSING and n.type is not NodeType.TEST),
        key=lambda n: (_LAYER_RANK.get(n.layer, 9), n.name),
    )
    if frontier:
        lines.append("")
        lines.append("FRONTIER (graph could not auto-resolve - your call):")
        for n in frontier:
            mark = "  (re-checked this cycle)" if n.id in changed_ids else ""
            lines.append(f"  {n.layer.value.upper():9} {n.name}   [{n.type.value}]  MISSING{mark}")
            ev = _best_evidence_line(n.evidence)
            if ev:
                lines.append(f"            evidence: {ev}")
            lines.append(f"            chain: {_chain_to_goal(graph, n)}")
            conflict = _conflict_note(graph, n)
            if conflict:
                lines.append(f"            {conflict}")
            plat = _platform_note(n)
            if plat:
                lines.append(f"            {plat}")
            if n.attempts:
                hist = "; ".join(f"{a.command} -> {a.outcome}" for a in n.attempts[-4:])
                lines.append(f"            attempts: {hist}")

    if len(lines) == 1:
        return ""
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_advise_planner_packet.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/advise.py tests/depgraph/test_advise_planner_packet.py
git commit -m "feat(depgraph): unified planner render with frontier diagnostic packet"
```

---

### Task 7: `emit_drain()` — the drain loop

**Files:**
- Modify: `src/envstate/depgraph_live.py`
- Test: `tests/test_depgraph_live_emit_drain.py`

**Interfaces:**
- Consumes: `partition`/`topo_order`/`build_recipe`/`EmitStep` (Tasks 1-3); `certify_refresh` (Task 5); `RecipeStep`/`RecipePatch`/`TaskReport` (`world_model.py:56-66,146`); `build_agent.run_recipe(recipe, sandbox_execute, ledger, step_offset)` (`build_agent.py:697`); `Attempt` (`schema.py:75`).
- Produces: `def emit_drain(graph, build_agent, sandbox_execute, ledger, exec_readonly, *, step_offset: int, cycle: int, max_drain: int = 4) -> tuple["DepGraph", list[TaskReport], int]` — returns `(new_graph, reports, steps_consumed)`; records an emit `Attempt` per target node and re-certifies after each pass.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_depgraph_live_emit_drain.py
from src.envstate.depgraph_live import emit_drain
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import TaskReport, CommandRecord
from python_deps.depgraph.schema import (
    DepGraph, Edge, EdgeType, Layer, Node, NodeType, State, DiscoveredBy,
)


class _FakeBuildAgent:
    def __init__(self):
        self.recipes = []

    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        self.recipes.append(recipe)
        cmds = tuple(CommandRecord(s.command, 0, "ok") for s in recipe.steps)
        return TaskReport("emit", "done", cmds, "ok", completed_steps=len(recipe.steps))


def _pkg(name, *, state=State.MISSING):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=state, version="1.0",
                check_command=f'python -c "import {name}"')


def test_emit_drain_installs_then_certifies():
    g = DepGraph(nodes=(_pkg("flask"), _pkg("click")))
    ba = _FakeBuildAgent()
    installed = set()

    def sandbox_execute(cmd):
        for name in ("flask", "click"):
            if name in cmd:
                installed.add(name)
        return True, "Successfully installed"

    def exec_readonly(cmd):
        return (0, "") if any(n in cmd and n in installed for n in ("flask", "click")) else (1, "no")

    new, reports, steps = emit_drain(
        g, ba, sandbox_execute, ActionLedger(), exec_readonly,
        step_offset=0, cycle=1,
    )
    assert new.get("pkg:flask").state is State.SATISFIED
    assert new.get("pkg:click").state is State.SATISFIED
    assert len(ba.recipes) == 1            # one pip step, drained in one pass
    assert new.get("pkg:flask").attempts   # emit attempt recorded


def test_emit_drain_unlocks_build_from_source_across_passes():
    lxml = Node(id="pkg:lxml", type=NodeType.PACKAGE, name="lxml", layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="5.0",
                build_from_source=True, check_command='python -c "import lxml"')
    libxml = Node(id="syslib:libxml2", type=NodeType.SYSTEM_LIB, name="libxml2.so.2",
                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                  check_command="ldconfig -p | grep libxml2",
                  fix_candidates=("apt:libxml2-dev",), chosen_fix="apt:libxml2-dev")
    g = DepGraph(nodes=(lxml, libxml),
                 edges=(Edge(src="pkg:lxml", dst="syslib:libxml2", relation=EdgeType.REQUIRES),))
    ba = _FakeBuildAgent()
    done = set()

    def sandbox_execute(cmd):
        if "libxml2-dev" in cmd:
            done.add("libxml2")
        if "lxml==" in cmd or "lxml" in cmd and "pip install" in cmd:
            done.add("lxml")
        return True, "ok"

    def exec_readonly(cmd):
        if "libxml2" in cmd:
            return (0, "") if "libxml2" in done else (1, "")
        if "import lxml" in cmd:
            return (0, "") if "lxml" in done else (1, "")
        return (1, "")

    new, reports, steps = emit_drain(
        g, ba, sandbox_execute, ActionLedger(), exec_readonly, step_offset=0, cycle=1,
    )
    assert new.get("syslib:libxml2").state is State.SATISFIED
    assert new.get("pkg:lxml").state is State.SATISFIED
    assert len(ba.recipes) == 2            # pass 1: apt; pass 2: pip (after toolchain certified)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_depgraph_live_emit_drain.py -v`
Expected: FAIL with `ImportError: cannot import name 'emit_drain'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/envstate/depgraph_live.py` (and add the imports shown at top):

```python
# --- add to the imports at the top of the file ---
from python_deps.depgraph.emit import build_recipe, partition, topo_order
from python_deps.depgraph.schema import Attempt
from src.envstate.world_model import RecipePatch, RecipeStep
```

```python
def emit_drain(
    graph,
    build_agent,
    sandbox_execute,
    ledger,
    exec_readonly,
    *,
    step_offset: int,
    cycle: int,
    max_drain: int = 4,
):
    """Drain the certifiable closure: emit -> run -> re-certify, repeat.

    Each pass emits the current emittable set (apt then pip), runs it through the
    real ``build_agent.run_recipe`` (D4: repair is a free safety layer), records
    an emit Attempt per target node, then re-certifies against the live container.
    Certifying a toolchain unlocks the build-from-source package that needs it, so
    the next pass picks it up (D5). Bounded by ``max_drain``.

    Returns ``(new_graph, reports, steps_consumed)``.
    """
    reports: list = []
    steps_consumed = 0
    new = graph
    if new is None or not new.nodes:
        return new, reports, steps_consumed

    for _ in range(max_drain):
        part = partition(new)
        if not part.emittable:
            break
        ordered = topo_order(new, part.emittable)
        emit_steps = build_recipe(new, ordered)
        if not emit_steps:
            break

        recipe = RecipePatch(steps=tuple(
            RecipeStep(
                id=f"emit-{cycle}-{i}",
                kind=s.kind,
                command=s.command,
                target_node_ids=s.target_node_ids,
            )
            for i, s in enumerate(emit_steps)
        ))
        report = build_agent.run_recipe(
            recipe, sandbox_execute, ledger, step_offset=step_offset + steps_consumed
        )
        reports.append(report)
        steps_consumed += len(report.commands)

        outcome = "succeeded" if report.status == "done" else "failed"
        for s in emit_steps:
            for nid in s.target_node_ids:
                node = new.get(nid)
                if node is not None:
                    new = new.with_node(
                        node.with_attempt(Attempt(command=s.command, outcome=outcome, cycle=cycle))
                    )

        new = certify_refresh(new, exec_readonly, cycle)

    return new, reports, steps_consumed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_depgraph_live_emit_drain.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/envstate/depgraph_live.py tests/test_depgraph_live_emit_drain.py
git commit -m "feat(envstate): emit_drain loop — emit, execute, re-certify until drained"
```

---

### Task 8: Wire CERTIFY + EMIT into `run_v1`

**Files:**
- Modify: `src/envstate/orchestrator.py` (`run_v1` signature + a `_dep_emit_phase` helper called before `planner.decide`)
- Test: `tests/test_run_v1_dep_emit.py`

**Interfaces:**
- Consumes: `certify_refresh`/`emit_drain` (Tasks 5,7); `render_depgraph_planner` (Task 6); `merge_map(dep_graph=, dep_advisory=)` (Task 4).
- Produces: `run_v1(..., enable_dep_emit: bool = False)`. When on AND `current_map.dep_graph is not None`: before each `planner.decide`, run `certify_refresh` → `emit_drain` → re-render into `dep_advisory`, and persist the evolving graph via `merge_map`. When off, the function is byte-for-byte unchanged (new branch is fully guarded).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_v1_dep_emit.py
from src.envstate.orchestrator import run_v1
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import initial_map, PlannerDecision, TaskReport, CommandRecord
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)


class _GiveupPlanner:
    def decide(self, world_map):
        return PlannerDecision(action="giveup", reason="stop")


class _FakeBuildAgent:
    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        cmds = tuple(CommandRecord(s.command, 0, "ok") for s in recipe.steps)
        return TaskReport("emit", "done", cmds, "ok", completed_steps=len(recipe.steps))


class _NoopMaintainer:
    def update(self, world_map, report):
        return world_map


def _pkg(name):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="1.0",
                check_command=f'python -c "import {name}"')


def test_run_v1_emits_and_certifies_before_planner():
    g = DepGraph(nodes=(_pkg("flask"),))
    m = initial_map("img", "/app", "python 3.12", "pip", (), dep_graph=g)
    installed = set()

    def sandbox_execute(cmd):
        if "flask" in cmd:
            installed.add("flask")
        return True, "ok"

    def exec_readonly(cmd):
        return (0, "") if ("flask" in installed and "import flask" in cmd) else (1, "no")

    final, reason = run_v1(
        _GiveupPlanner(), _FakeBuildAgent(), _NoopMaintainer(), m, ActionLedger(),
        sandbox_execute, max_cycles=1, exec_readonly=exec_readonly, enable_dep_emit=True,
    )
    # emit ran before the planner gave up, so flask is certified in the carried graph
    assert final.dep_graph.get("pkg:flask").state is State.SATISFIED
    assert "CERTIFIED" in final.dep_advisory


def test_run_v1_off_state_does_not_touch_graph():
    g = DepGraph(nodes=(_pkg("flask"),))
    m = initial_map("img", "/app", "python 3.12", "pip", (), dep_graph=g)
    final, reason = run_v1(
        _GiveupPlanner(), _FakeBuildAgent(), _NoopMaintainer(), m, ActionLedger(),
        lambda c: (True, "ok"), max_cycles=1, enable_dep_emit=False,
    )
    assert final.dep_graph.get("pkg:flask").state is State.MISSING  # untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_v1_dep_emit.py -v`
Expected: FAIL with `TypeError: run_v1() got an unexpected keyword argument 'enable_dep_emit'`

- [ ] **Step 3: Write minimal implementation**

In `src/envstate/orchestrator.py`, add the import near the top:

```python
from src.envstate.depgraph_live import certify_refresh, emit_drain
from python_deps.depgraph.advise import render_depgraph_planner
```

Add the parameter to `run_v1` (after `enable_contract_graph: bool = False,` at line 65):

```python
    enable_contract_graph: bool = False,
    enable_dep_emit: bool = False,
):
```

Add a helper inside `run_v1` next to `_host_refresh` (after line 101). It uses the existing `global_step` via `nonlocal`:

```python
    def _dep_emit_phase(cycle: int) -> None:
        nonlocal current_map, global_step
        if not enable_dep_emit or current_map.dep_graph is None:
            return
        graph = certify_refresh(current_map.dep_graph, exec_readonly, cycle)
        graph, _reports, steps = emit_drain(
            graph, build_agent, sandbox_execute, ledger, exec_readonly,
            step_offset=global_step, cycle=cycle,
        )
        global_step += steps
        advisory = render_depgraph_planner(graph)
        current_map = merge_map(current_map, dep_graph=graph, dep_advisory=advisory)
```

Call it at the top of the cycle loop, immediately before `planner.decide` (line 109):

```python
    for cycle in range(1, max_cycles + 1):
        # ── 0. Graph-first: certify + emit the certified closure ────────────
        _dep_emit_phase(cycle)
        # ── 1. Planner decides what to do next ──────────────────────────────
        decision: PlannerDecision = planner.decide(current_map)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_run_v1_dep_emit.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the existing orchestrator suite to confirm off-state is intact**

Run: `pytest tests/ -k "orchestrator or run_v1" -q`
Expected: PASS (no regressions; the new branch is fully guarded by `enable_dep_emit`)

- [ ] **Step 6: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_run_v1_dep_emit.py
git commit -m "feat(envstate): graph-first certify+emit phase in run_v1 (enable_dep_emit)"
```

---

### Task 9: Flag wiring (`enable_dep_emit`) + off-state byte-identical guard

**Files:**
- Modify: `agent.py` (the `_run_v1` call that passes `enable_contract_graph` into `run_v1`; the flag default near `agent.py:235-236`)
- Modify: `multi_docker_eval_adapter.py` (env-var bridge near `:776`, mirroring `DOCKERAGENT_ENABLE_DEP_GRAPH`)
- Modify: `run_repo2run_benchmark.py` (CLI flag near `:3169`, mirroring `--enable-dep-graph`)
- Test: `tests/test_dep_emit_flag_wiring.py`

**Interfaces:**
- Consumes: `run_v1(..., enable_dep_emit=...)` (Task 8).
- Produces: a `--enable-dep-emit` CLI flag, a `DOCKERAGENT_ENABLE_DEP_EMIT` env var, and an `agent.py` constructor flag `self.enable_dep_emit` threaded into the `run_v1` call. Emit implies the dep graph is built: when `enable_dep_emit` is on, force `enable_dep_graph` on too (emit needs `dep_graph` populated).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dep_emit_flag_wiring.py
import inspect
from src.envstate.orchestrator import run_v1


def test_run_v1_exposes_enable_dep_emit():
    sig = inspect.signature(run_v1)
    assert "enable_dep_emit" in sig.parameters
    assert sig.parameters["enable_dep_emit"].default is False


def test_env_var_bridge_present():
    import multi_docker_eval_adapter as ad
    src = inspect.getsource(ad)
    assert "DOCKERAGENT_ENABLE_DEP_EMIT" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dep_emit_flag_wiring.py -v`
Expected: FAIL on `test_env_var_bridge_present` (env var not yet referenced)

- [ ] **Step 3: Write minimal implementation**

In `agent.py`, near the other enable flags (`agent.py:235-236`), add the constructor default:

```python
        self.enable_dep_emit: bool = bool(enable_dep_emit)
        # emit needs the graph built; turning emit on implies dep_graph on.
        if self.enable_dep_emit:
            self.enable_dep_graph = True
```

In the `run_v1(...)` call inside `_run_v1` (where `enable_contract_graph=self.enable_contract_graph` is passed), add:

```python
            enable_contract_graph=self.enable_contract_graph,
            enable_dep_emit=self.enable_dep_emit,
```

In `multi_docker_eval_adapter.py`, near the `DOCKERAGENT_ENABLE_DEP_GRAPH` bridge (`:776`):

```python
    enable_dep_emit = os.environ.get("DOCKERAGENT_ENABLE_DEP_EMIT", "").lower() in ("1", "true", "yes")
```
and pass `enable_dep_emit=enable_dep_emit` into the `DockerAgent(...)` constructor call alongside the existing `enable_dep_graph=...`.

In `run_repo2run_benchmark.py`, near the `--enable-dep-graph` definition (`:3169`):

```python
    parser.add_argument("--enable-dep-emit", action="store_true",
                        help="Graph-first: emit the certified closure + escalate the frontier (implies --enable-dep-graph).")
```
and thread `enable_dep_emit=args.enable_dep_emit` into the agent construction the same way `enable_dep_graph` is threaded.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dep_emit_flag_wiring.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Off-state byte-identical guard**

Run: `pytest tests/ -q`
Expected: PASS — full suite green; the emit path is reachable only with the flag on, so every existing test (flag off) is unchanged.

- [ ] **Step 6: Commit**

```bash
git add agent.py multi_docker_eval_adapter.py run_repo2run_benchmark.py tests/test_dep_emit_flag_wiring.py
git commit -m "feat: wire enable_dep_emit flag (CLI + env var + agent), implies dep_graph"
```

---

### Task 10: Docker integration test (opt-in) — emit certifies; wrong emit self-escalates

**Files:**
- Create: `tests/depgraph/test_emit_drain_docker.py` (gated like `tests/depgraph/test_ldd_probe_docker.py`)

**Interfaces:**
- Consumes: `emit_drain` (Task 7) with a real `DockerExecutor`-backed `sandbox_execute`/`exec_readonly` pair over a `python:3.11-slim` container.
- Produces: end-to-end proof that (a) a real emit flips a node to `SATISFIED`, and (b) a deliberately-wrong apt name leaves its node `MISSING` (falls back to FRONTIER — the safety valve, Section 9 of the spec).

- [ ] **Step 1: Write the test (gated; skips when Docker absent)**

```python
# tests/depgraph/test_emit_drain_docker.py
import shutil
import pytest

from src.envstate.depgraph_live import emit_drain
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import TaskReport, CommandRecord
from python_deps.depgraph.executor import DockerExecutor
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker required")


class _DirectBuildAgent:
    """Stand-in build agent that runs each emitted command verbatim (no LLM)."""
    def __init__(self, ex):
        self.ex = ex

    def run_recipe(self, recipe, sandbox_execute, ledger, step_offset=0):
        cmds = []
        ok_all = True
        done = 0
        for s in recipe.steps:
            r = self.ex.run(s.command, timeout=600)
            cmds.append(CommandRecord(s.command, r.returncode, (r.stdout + r.stderr)[-500:]))
            if r.ok:
                done += 1
            else:
                ok_all = False
                break
        return TaskReport("emit", "done" if ok_all else "blocked",
                          tuple(cmds), "ok" if ok_all else "fail", completed_steps=done)


def _pkg(name, version):
    return Node(id=f"pkg:{name}", type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version=version,
                check_command=f'python -c "import {name}"')


def test_real_emit_certifies_resolved_package():
    with DockerExecutor("python:3.11-slim") as ex:
        g = DepGraph(nodes=(_pkg("click", "8.1.7"),))
        ba = _DirectBuildAgent(ex)
        new, reports, steps = emit_drain(
            g, ba, lambda c: (ex.run(c).ok, ""), lambda c: (ex.run(c).returncode, ex.run(c).stdout),
            ActionLedger(), step_offset=0, cycle=1,
        )
        assert new.get("pkg:click").state is State.SATISFIED


def test_wrong_apt_name_self_escalates_to_frontier():
    with DockerExecutor("python:3.11-slim") as ex:
        bad = Node(id="tool:nope", type=NodeType.TOOL, name="nope", layer=Layer.TOOLCHAIN,
                   discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                   check_command="command -v nope",
                   fix_candidates=("apt:this-apt-pkg-does-not-exist",),
                   chosen_fix="apt:this-apt-pkg-does-not-exist")
        g = DepGraph(nodes=(bad,))
        ba = _DirectBuildAgent(ex)
        new, reports, steps = emit_drain(
            g, ba, lambda c: (ex.run(c).ok, ""), lambda c: (ex.run(c).returncode, ex.run(c).stdout),
            ActionLedger(), step_offset=0, cycle=1,
        )
        # emit failed in-container; the node stays MISSING -> escalates to the LLM
        assert new.get("tool:nope").state is State.MISSING
```

> Note: `emit_drain`'s positional order is `(graph, build_agent, sandbox_execute, ledger, exec_readonly, ...)`. The test above passes `sandbox_execute` and `exec_readonly` as the 3rd and 5th args with `ledger` 4th — keep that order when wiring the lambdas.

- [ ] **Step 2: Run the gated test**

Run: `pytest tests/depgraph/test_emit_drain_docker.py -v`
Expected: PASS (2 tests) when Docker is present; SKIPPED otherwise.

- [ ] **Step 3: Commit**

```bash
git add tests/depgraph/test_emit_drain_docker.py
git commit -m "test(depgraph): docker e2e — emit certifies; wrong emit self-escalates"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| §3 graph-first cycle (CERTIFY/EMIT/ESCALATE) | 5 (certify), 7 (emit drain), 8 (wiring) |
| §3 drain loop unlocks build-from-source | 7 (`test_emit_drain_unlocks_build_from_source_across_passes`) |
| §3.1 BuildAgent unchanged / second caller | 7 (`run_recipe` reused via fake), 10 (real exec) |
| §3.1 Maintainer unchanged for MVP | 8 (maintainer still called; emit phase is additive) |
| §5 partition (certified/emittable/frontier) | 1 |
| §6 topo-sort + apt-then-pip pinned emit (D2) | 2, 3 |
| §7 reuse run_recipe; pure emit core; EmitStep neutrality | 1-3 (pure), 7 (conversion) |
| §8 unified render + frontier diagnostic packet | 6 |
| §9 safety valve (wrong emit → frontier) | 10 (`test_wrong_apt_name_self_escalates_to_frontier`) |
| §10 synthesis payoff | emergent: graph carried + certified via Task 4 (dep_graph on map) |
| §13 graceful degradation | 5 (`certify_refresh` no-op), 8 (guarded branch) |
| §14 D1 control inversion | 8 (`_dep_emit_phase` before `planner.decide`) |
| §14 D2 pip whole-closure | 3 |
| §14 D3 staged contract-graph retirement | deferred — NOT in this plan (follow-up; see note below) |
| §14 D4 emit through run_recipe | 7 |
| §14 D5 drain vs one-batch | 7 (`max_drain` loop) |
| off-state byte-identical | 8 (guarded), 9 (full-suite green) |

**Note on D3:** the spec scopes contract-graph *retirement* to a follow-up after a proven benchmark run. This plan therefore leaves `src/envstate/contracts/` untouched and consumes the unified render through the existing `dep_advisory` slot (so `planner.py` is unchanged). The "two surfaces" collapse is a separate plan, gated on A/B results — consistent with §4/§14 D3.

**2. Placeholder scan:** none — every code step contains complete, runnable code and exact commands.

**3. Type consistency:** `EmitStep(kind, command, target_node_ids)` (Task 3) consumed verbatim in `emit_drain` (Task 7); `Partition.emittable` (Task 1) → `topo_order` (Task 2) → `build_recipe` (Task 3); `certify_refresh(graph, exec_readonly, cycle)` (Task 5) called identically in `emit_drain` (Task 7) and `_dep_emit_phase` (Task 8); `render_depgraph_planner(graph, changed_ids=frozenset())` (Task 6) called with one arg in Task 8; `merge_map(dep_graph=, dep_advisory=)` (Task 4) used in Task 8. `run_recipe(recipe, sandbox_execute, ledger, step_offset=)` signature matches `build_agent.py:697` in Tasks 7/10. Consistent.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-unified-depgraph-emit-escalate.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
