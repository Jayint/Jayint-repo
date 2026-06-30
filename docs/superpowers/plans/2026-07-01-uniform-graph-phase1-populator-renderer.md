# Uniform Graph — Phase 1: setup_commands Populator + Single-Path Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the graph node the single source of its install command — a deterministic `populate_setup_commands` pass fills a new `Node.setup_commands` field for the reciped tiers, the renderer derives commands ONLY from that field (the old `_install_command` derivation is deleted, not kept as a fallback), and the rendered `setup.sh` is proven byte-identical to today.

**Architecture:** Paper-first, single-path. New `Node` fields (`setup_commands`, `strength`, `phase`). One command producer — `populate_setup_commands` — owns all install-command derivation. `render_build_script` calls it internally (single call site, terminal, after every `chosen_fix` mutation), so there is no separate pipeline wiring, no fallback, and no race with the apt-name reconciler. A short legibility pass de-jargons the two names the readability review flagged as the worst comprehension barriers. This is the foundation Phases 2–4 build on; those are separate plans with explicit entry conditions recorded in the specs' Review Corrections.

**Tech Stack:** Python 3 (frozen dataclasses), pytest. Pure `python_deps/depgraph` code — no Docker, no network, no LLM, no `src.envstate` imports.

## Global Constraints

- **Target branch / worktree:** `/Users/john/john-planner-v3-core` (branch `v3-core`). All commands and commits run there.
- **Paper-first cleanliness (overrides migration-safety):** prefer ONE clean path over transitional scaffolding. No fallbacks, no flag-gated compat branches, delete the old path in the same change that adds the new one. (See memory: core branch is the paper's reference impl.)
- **Immutability:** every node "mutation" returns a NEW object via `dataclasses.replace`. `Node.data` is a frozen `MappingProxyType`.
- **`python_deps/depgraph` stays LLM-free and envstate-free.** `populate.py` imports only `python_deps.depgraph.*` + stdlib.
- **Byte-identical guarantee:** the rendered `setup.sh` after this change MUST be byte-for-byte identical to before for the reciped tiers (Package with `version`; SystemLib/Tool with an `apt:` `chosen_fix`). The pinned `pip install --break-system-packages --no-deps <name>==<version>` form and the single hoisted `apt-get update` are preserved exactly. This is proven by a test, not by keeping old code.
- **Single command producer:** after this phase, `populate._command_for` is the ONLY function in the static path that derives an install command from node fields. `build_script._install_command` is deleted. (`emit.build_recipe` and `block._command_for` — the live paths — are addressed in Phase 2; see the alignment spec's Phase-2 entry conditions.)
- **No new dependencies.** Standard library only.
- **Run unit tests** with the system interpreter (no Docker): `python3 -m pytest tests/depgraph/ -q`.

---

## File Structure

- `src/python_deps/depgraph/schema.py` — **modify**: add `Strength` + `Phase` enums; add `setup_commands`/`strength`/`phase` to `Node` (with group comments); extend `Node.to_dict`.
- `src/python_deps/depgraph/populate.py` — **create**: the single command producer `populate_setup_commands(graph)` + private `_command_for`.
- `src/python_deps/depgraph/build_script.py` — **modify**: `render_build_script` calls `populate_setup_commands` internally; `_node_block` emits `node.setup_commands`; **delete `_install_command`**.
- `src/python_deps/depgraph/advise.py` — **modify**: rename the `render_dep_graph_advisory` local `frontier` → `unsatisfied_nodes` (de-conflate from `emit.Partition.frontier`).
- `tests/depgraph/test_schema_setup_fields.py` — **create**.
- `tests/depgraph/test_populate_setup_commands.py` — **create**.
- `tests/depgraph/test_build_script_setup_commands.py` — **create**.

**NOT wired here (and why):** `populate_setup_commands` is NOT called from `advise.build_advisory_for_repo` or `run_v3_e2e.py`. The code-review found that calling it in `advise.py` (before the container-stage `reconcile_apt_names` rewrites `chosen_fix` to t64 names) writes stale commands the idempotency guard can never correct — regenerating the Stage 2.5 bug. The single safe call site is inside `render_build_script`, which always runs after every `chosen_fix` mutation.

---

### Task 1: Schema — `Strength`/`Phase` enums + grouped `Node` fields

**Files:**
- Modify: `src/python_deps/depgraph/schema.py`
- Test: `tests/depgraph/test_schema_setup_fields.py`

**Interfaces:**
- Produces: `Strength` enum (`SOFT="soft"`, `HARD="hard"`); `Phase` enum (`SETUP="setup"`, `RUNTIME="runtime"`, `TEST="test"`, `GATE="gate"`); `Node.setup_commands: tuple[str, ...]` (default `()`), `Node.strength: Strength` (default `Strength.SOFT`), `Node.phase: Phase` (default `Phase.SETUP`); `Node.to_dict()` includes `"setup_commands"` (list), `"strength"` (str), `"phase"` (str).

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_schema_setup_fields.py`:

```python
from python_deps.depgraph.schema import (
    DiscoveredBy, Layer, Node, NodeType, Phase, Strength,
)


def _pkg(**kw):
    base = dict(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER)
    base.update(kw)
    return Node(**base)


def test_new_fields_default_inert():
    n = _pkg()
    assert n.setup_commands == ()
    assert n.strength is Strength.SOFT
    assert n.phase is Phase.SETUP


def test_to_dict_includes_new_fields():
    n = _pkg(
        setup_commands=("python3 -m pip install --break-system-packages --no-deps requests==2.0",),
        strength=Strength.HARD,
        phase=Phase.SETUP,
    )
    d = n.to_dict()
    assert d["setup_commands"] == [
        "python3 -m pip install --break-system-packages --no-deps requests==2.0"
    ]
    assert d["strength"] == "hard"
    assert d["phase"] == "setup"


def test_enum_values():
    assert {s.value for s in Strength} == {"soft", "hard"}
    assert {p.value for p in Phase} == {"setup", "runtime", "test", "gate"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_schema_setup_fields.py -q`
Expected: FAIL with `ImportError: cannot import name 'Phase'`.

- [ ] **Step 3: Add the enums**

In `src/python_deps/depgraph/schema.py`, immediately after the `class Layer(enum.Enum):` block, add:

```python
class Strength(enum.Enum):
    """Blocking semantics. SOFT = hint/candidate (does not block dependents or
    gates); HARD = required obligation. The populator sets HARD on reciped tiers;
    static/LLM discovery stays SOFT."""

    SOFT = "soft"
    HARD = "hard"


class Phase(enum.Enum):
    """Where a node's commands belong in the final artifact (distinct from Layer,
    which drives topological order)."""

    SETUP = "setup"
    RUNTIME = "runtime"
    TEST = "test"
    GATE = "gate"
```

- [ ] **Step 4: Add the `Node` fields with a group comment**

In `src/python_deps/depgraph/schema.py`, in the `Node` dataclass, immediately after the `data: dict = field(default_factory=dict)` line, add:

```python
    # --- install-command generation (uniform-graph) ---
    # setup_commands is the node's canonical "how" (the only command source the
    # renderer reads); strength is blocking semantics; phase is artifact placement.
    setup_commands: tuple[str, ...] = ()
    strength: Strength = Strength.SOFT
    phase: Phase = Phase.SETUP
```

- [ ] **Step 5: Extend `Node.to_dict`**

In `Node.to_dict`, add these entries right before `"data": dict(self.data),`:

```python
            "setup_commands": list(self.setup_commands),
            "strength": self.strength.value,
            "phase": self.phase.value,
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_schema_setup_fields.py -q`
Expected: PASS (3 passed).

- [ ] **Step 7: Run the full depgraph suite (additive fields, no regressions)**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the baseline count.

- [ ] **Step 8: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema_setup_fields.py
git commit -m "feat(depgraph): add Strength/Phase enums + setup_commands/strength/phase Node fields"
```

---

### Task 2: `populate_setup_commands` — the single command producer

**Files:**
- Create: `src/python_deps/depgraph/populate.py`
- Test: `tests/depgraph/test_populate_setup_commands.py`

**Interfaces:**
- Consumes: `Node.setup_commands`/`strength` (Task 1); `emit._apt_name`, `emit._pip_spec`, `emit._is_reciped` (existing).
- Produces: `populate_setup_commands(graph: DepGraph) -> DepGraph` — returns a NEW graph in which every reciped node lacking `setup_commands` now has `setup_commands=(cmd,)` and `strength=Strength.HARD`. `_command_for(node) -> str` is the ONLY install-command derivation in the static path (the relocated, now-sole copy of the logic deleted from `build_script` in Task 3).

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_populate_setup_commands.py`:

```python
from python_deps.depgraph.populate import populate_setup_commands
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State, Strength,
)


def _pkg():
    return Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                version="2.0", state=State.MISSING, chosen_fix="pip:requests")


def _syslib():
    return Node(id="syslib:libpq", type=NodeType.SYSTEM_LIB, name="libpq",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                state=State.MISSING, chosen_fix="apt:libpq-dev")


def _service():
    return Node(id="service:redis", type=NodeType.SERVICE, name="redis",
                layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.MISSING)


def test_fills_reciped_package_with_pinned_no_deps_pip():
    n = populate_setup_commands(DepGraph(nodes=(_pkg(),))).get("pkg:requests")
    assert n.setup_commands == (
        "python3 -m pip install --break-system-packages --no-deps requests==2.0",
    )
    assert n.strength is Strength.HARD


def test_fills_reciped_syslib_with_apt():
    n = populate_setup_commands(DepGraph(nodes=(_syslib(),))).get("syslib:libpq")
    assert n.setup_commands == ("apt-get install -y --no-install-recommends libpq-dev",)
    assert n.strength is Strength.HARD


def test_leaves_non_reciped_service_untouched():
    n = populate_setup_commands(DepGraph(nodes=(_service(),))).get("service:redis")
    assert n.setup_commands == ()
    assert n.strength is Strength.SOFT


def test_idempotent_does_not_overwrite_existing():
    g = DepGraph(nodes=(_pkg(),))
    once = populate_setup_commands(g)
    twice = populate_setup_commands(once)
    assert once.get("pkg:requests").setup_commands == twice.get("pkg:requests").setup_commands
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_populate_setup_commands.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.populate'`.

- [ ] **Step 3: Create the populator (the sole command producer)**

Create `src/python_deps/depgraph/populate.py`:

```python
"""The single producer of node install commands for the static path.

Pure: no Docker, no network, no LLM, no src.envstate. populate_setup_commands
fills node.setup_commands for the reciped tiers (Package/SystemLib/Tool) so the
renderer can be a dumb emitter. _command_for here is the ONLY copy of the
per-node install-command logic in the static path — build_script._install_command
is deleted in favour of it.
"""
from __future__ import annotations

from dataclasses import replace

from python_deps.depgraph.emit import _apt_name, _is_reciped, _pip_spec
from python_deps.depgraph.schema import DepGraph, NodeType, Strength


def _command_for(node) -> str:
    """The install command for a reciped node (apt for SystemLib/Tool, pinned
    --no-deps pip for Package). The single source of this derivation."""
    apt = _apt_name(node)
    if apt is not None:
        return f"apt-get install -y --no-install-recommends {apt}"
    if node.type is NodeType.PACKAGE:
        return f"python3 -m pip install --break-system-packages --no-deps {_pip_spec(node)}"
    return node.chosen_fix or ""  # defensive; reciped syslib/tool are always apt


def populate_setup_commands(graph: DepGraph) -> DepGraph:
    """Return a NEW graph in which every reciped node lacking setup_commands gets
    its install command + strength=HARD. Idempotent; leaves Service/Config/
    DataAsset and already-populated nodes untouched."""
    new = graph
    for node in graph.nodes:
        if node.setup_commands:
            continue
        if not _is_reciped(node):
            continue
        cmd = _command_for(node)
        if not cmd:
            continue
        new = new.with_node(replace(node, setup_commands=(cmd,), strength=Strength.HARD))
    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_populate_setup_commands.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/populate.py tests/depgraph/test_populate_setup_commands.py
git commit -m "feat(depgraph): add populate_setup_commands — single install-command producer"
```

---

### Task 3: Renderer derives commands ONLY from `setup_commands`; delete `_install_command`

**Files:**
- Modify: `src/python_deps/depgraph/build_script.py` (`render_build_script`, `_node_block`; delete `_install_command`)
- Test: `tests/depgraph/test_build_script_setup_commands.py`

**Interfaces:**
- Consumes: `populate_setup_commands` (Task 2); `Node.setup_commands` (Task 1).
- Produces: `render_build_script` populates internally then emits `node.setup_commands`; `_install_command` no longer exists.

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_build_script_setup_commands.py`:

```python
import python_deps.depgraph.build_script as bs
from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.populate import populate_setup_commands
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


def _pkg(setup_commands=()):
    return Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                version="2.0", state=State.MISSING, chosen_fix="pip:requests",
                setup_commands=setup_commands)


def test_install_command_is_deleted():
    # The single-producer rule: build_script no longer derives commands.
    assert not hasattr(bs, "_install_command")


def test_renderer_emits_setup_commands_verbatim():
    # A node whose setup_commands differ from any derivation proves the renderer
    # reads the field. (No populate call — the field is already set.)
    g = DepGraph(nodes=(_pkg(setup_commands=("echo CUSTOM_INSTALL",)),))
    script = render_build_script(g)
    assert "echo CUSTOM_INSTALL" in script
    assert "pip install" not in script


def test_render_auto_populates_reciped_nodes():
    # No setup_commands on input -> render populates internally -> pinned pip line.
    g = DepGraph(nodes=(_pkg(),))
    script = render_build_script(g)
    assert "python3 -m pip install --break-system-packages --no-deps requests==2.0" in script


def test_render_is_byte_identical_to_explicit_populate():
    syslib = Node(id="syslib:libpq", type=NodeType.SYSTEM_LIB, name="libpq",
                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                  state=State.MISSING, chosen_fix="apt:libpq-dev")
    g = DepGraph(nodes=(_pkg(), syslib))
    assert render_build_script(g) == render_build_script(populate_setup_commands(g))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_build_script_setup_commands.py -q`
Expected: FAIL — `test_install_command_is_deleted` fails (it still exists) and `test_renderer_emits_setup_commands_verbatim` fails (current `_node_block` calls `_install_command`, so `"pip install"` is present).

- [ ] **Step 3: Make `render_build_script` populate internally**

In `src/python_deps/depgraph/build_script.py`, add the import near the other `python_deps.depgraph` imports at the top:

```python
from python_deps.depgraph.populate import populate_setup_commands
```

Then in `render_build_script`, change the opening:

```python
def render_build_script(graph: DepGraph | None, manual_blocks: tuple[Block, ...] = ()) -> str:
    if graph is None:
        graph = DepGraph()
    parts: list[str] = _manifest(graph, manual_blocks) + ["set -Eeuo pipefail"]
```

to:

```python
def render_build_script(graph: DepGraph | None, manual_blocks: tuple[Block, ...] = ()) -> str:
    if graph is None:
        graph = DepGraph()
    graph = populate_setup_commands(graph)  # single call site: derive commands, then emit
    parts: list[str] = _manifest(graph, manual_blocks) + ["set -Eeuo pipefail"]
```

- [ ] **Step 4: Emit `setup_commands` and delete `_install_command`**

In `_node_block`, change the line:

```python
    out.append(_install_command(node))
```

to:

```python
    out += list(node.setup_commands)
```

Then delete the entire `_install_command` function:

```python
def _install_command(node: Node) -> str:
    apt = _apt_name(node)
    if apt is not None:
        return f"apt-get install -y --no-install-recommends {apt}"
    if node.type is NodeType.PACKAGE:
        return f"python3 -m pip install --break-system-packages --no-deps {_pip_spec(node)}"
    return node.chosen_fix or ""  # defensive; reciped syslib/tool are always apt
```

(`_apt_name` is still imported and used by `_node_block` for the hoisted `apt-get update`; `_pip_spec` is no longer used in this file — remove it from the `from python_deps.depgraph.emit import ...` line if it is now unused.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_build_script_setup_commands.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Run the full depgraph suite (byte-identical: existing render goldens unchanged)**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the baseline count. Existing render tests build graphs without `setup_commands`; `render_build_script` now populates them internally and emits the same commands `_install_command` produced, so the goldens are unchanged.

- [ ] **Step 7: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/build_script.py tests/depgraph/test_build_script_setup_commands.py
git commit -m "refactor(depgraph): render from node.setup_commands; delete _install_command (single command path)"
```

---

### Task 4: Legibility pass — de-conflate `frontier`

**Files:**
- Modify: `src/python_deps/depgraph/advise.py` (`render_dep_graph_advisory` local variable + section label)

**Interfaces:** none changed — internal rename only.

The readability review's #1 comprehension barrier: `emit.Partition.frontier` means "MISSING nodes the deterministic layer canNOT install (LLM's job)", but `render_dep_graph_advisory` uses a local named `frontier` for *all* MISSING non-Test nodes (a superset), and the planner render in the same file uses the strict `partition().frontier`. Same word, two meanings, one file.

- [ ] **Step 1: Rename the advisory local and its label**

In `src/python_deps/depgraph/advise.py`, in `render_dep_graph_advisory`, rename the local variable `frontier` to `unsatisfied_nodes` (every occurrence within that function only — do NOT touch `emit.Partition.frontier` or `render_depgraph_planner`'s use of `partition(graph).frontier`), and change the rendered section header from `"FRONTIER (unsatisfied - act here):"` to `"UNSATISFIED (act here):"`.

- [ ] **Step 2: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at baseline. If any test asserts the literal `"FRONTIER (unsatisfied - act here):"` header, update that expected string to `"UNSATISFIED (act here):"` in the test — that is the intended legibility change.

- [ ] **Step 3: Run the full suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest -q`
Expected: PASS at the known baseline (1276 passed, 32 skipped, 2 pre-existing PDF-dataset failures — see the branch manifest §7).

- [ ] **Step 4: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/advise.py
git commit -m "refactor(depgraph): rename advisory 'frontier' -> 'unsatisfied_nodes' (de-conflate from Partition.frontier)"
```

---

## Deferred to later phases (recorded as entry conditions in the specs)

These reviewer findings are NOT in Phase 1 — they belong to the phase that introduces the feature they guard, and are recorded in the specs' "Review Corrections" so they are spec'd before that code is written:
- **`emit.build_recipe` / `block._command_for` migration to `setup_commands`** + the decision "does `setup_commands` carry the self-sufficient `apt-get update &&` form, or does each renderer inject it?" — Phase 2 opening decision (alignment spec).
- **`ProviderSpec` removal atomic with `NodeSpec.setup_commands` + `replace_commands`** — Phase 2 entry condition (alignment spec).
- **`normalize_emittability(graph)`** (derive `chosen_fix=apt:<id-suffix>` for LLM-proposed soft SystemLib/Tool) — Phase 2 entry condition, before the classifier widening (construction spec).
- **Extract shared predicates (`_is_reciped`→`is_auto_installable`, `_apt_name`, `_pip_spec`) to `node_predicates.py`; move `_best_evidence_line` out of `advise.py`; trim `resolve.py`'s private re-exports** — Phase 2 legibility refactor (low risk, deferred only to keep Phase 1's diff minimal and byte-identical).
- **Typed `node.data` (`NodeEnrichment` / key constants) and `_graph_hash` including `setup_commands`** — Phase 3 entry conditions, when LLM patches write arbitrary `setup_commands` (construction spec).

---

## Self-Review

**1. Spec coverage (Phase 1 slice of the alignment spec):**
- Add `setup_commands`/`strength`/`phase` to `Node` → Task 1. ✓
- Single deterministic command producer; renderer is a dumb emitter → Tasks 2–3. ✓
- `strength` defaults SOFT; populator sets HARD on reciped tiers (Review Correction D) → Task 1 default + Task 2. ✓
- Single command path, no fallback, `_install_command` deleted (paper-first cleanliness) → Task 3. ✓
- Single populate call site, terminal, no `advise`/`run_v3_e2e` wiring (avoids the t64 stale-command race) → Task 3 + the "NOT wired here" note. ✓
- De-conflate `frontier` (readability #1) → Task 4. ✓
- Deferred items each mapped to a later-phase entry condition → "Deferred" section. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step shows complete code. ✓

**3. Type consistency:** `populate_setup_commands(graph) -> DepGraph` used identically in Tasks 2/3. `_command_for` is the single derivation (apt → `apt-get install -y --no-install-recommends <name>`; Package → pinned `--no-deps` pip; else `chosen_fix`). `Strength.HARD`/`SOFT`, `Phase.SETUP` match Task 1. `setup_commands` is `tuple[str, ...]` everywhere. After Task 3 no symbol references the deleted `_install_command`. ✓

**Note on byte-identical:** `render_build_script` now calls `populate_setup_commands` itself, so every existing caller (tests included) gets populated nodes and the same emitted commands — the change is invisible to output while removing the duplicate derivation. `test_render_is_byte_identical_to_explicit_populate` plus the unchanged existing render goldens prove it.
