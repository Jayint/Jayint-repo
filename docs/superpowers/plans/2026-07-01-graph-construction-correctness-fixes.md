# Graph Construction Correctness Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed correctness bugs in the depgraph construction pipeline (`src/python_deps/depgraph/`) so the constructed graph faithfully reflects the *install ground truth* on the *target* platform, without rewriting the (sound) pipeline skeleton.

**Architecture:** The construction pipeline already runs the correct stages (scan → roots → resolve → install/probe → ldd → relink → certify). We do NOT restructure it. Track 1 is surgical in-place fixes (each independently shippable). Track 2 introduces the two abstractions whose fix *is* the clean structure: a single `TargetEnv` object threaded into the resolve, and a targeted-extras model. Track 3 (canonical syslib identity) is the largest structural fix. Every task is TDD and closes a currently-untested gap.

**Tech Stack:** Python 3, `pytest`, `uv` (host-side resolver), `packaging` (markers/versions), Docker executor abstraction (`Executor`). No new dependencies.

**Execution Environment:** Run this plan in the **v3-core worktree** — `/Users/john/john-planner-v3-core` (branch `v3-core`). All `pytest`, `git add`, and `git commit` commands run from that worktree root, and this plan file lives there (`docs/superpowers/plans/`). Validated against v3-core @ `31cc5e2` (2026-07-01): every edit target is present. Where v3-core has advanced past where the plan was first drafted — it added `Strength`/`Phase` enums + `setup_commands` Node fields, and a new `populate.py` that is now the single per-node install-command producer feeding `build_script.render_build_script` — it is called out inline in the affected tasks (Tasks 3, 6, 7 and the `--no-deps` follow-up). Do NOT execute on the `john-planner-v3` worktree.

## Global Constraints

- **Immutability:** every graph mutation returns a NEW `DepGraph` via `dataclasses.replace`; never mutate a node/edge/graph in place. (Holds today — do not break it.)
- **Single SATISFIED writer:** only `certify.certify` may write `State.SATISFIED`, gated on a real `check_command` rc. No task may add another SATISFIED writer.
- **No silent shrink / no false pass:** a fix must never make the closure smaller or a check pass without the underlying condition being true. Prefer honest-MISSING over false-SATISFIED.
- **Run the full suite after each task:** `pytest tests/depgraph -q` must stay green (**577 passing baseline on v3-core @ 31cc5e2** — 11 more than the 566 the plan was drafted against; v3-core added `test_build_script_setup_commands.py`, `test_populate_setup_commands.py`, `test_schema_setup_fields.py`) plus the new test.
- **Target, not host:** any platform/interpreter fact used by the resolve must come from the target container (or its base image), never from the host process running the resolve.

---

## File Structure

Files touched, by track. Each is an existing file unless marked *Create*.

| File | Responsibility | Track |
|---|---|---|
| `src/python_deps/depgraph/probe.py` | header-tool check must be rc-discriminating | 1 |
| `src/python_deps/import_mapping.py` | remove wrong `image`→Pillow mapping | 1 |
| `src/python_deps/depgraph/schema.py` | validate node-existence for ALL edge relations | 1 |
| `src/python_deps/depgraph/resolve_errors.py` | conflict drop-set must exclude imposers | 1 |
| `src/python_deps/depgraph/emit.py` | cross-tier gate applies to ALL packages | 1 |
| `src/python_deps/depgraph/build_script.py` | section order = shared execution order | 1 |
| `src/python_deps/depgraph/target_env.py` *(Create)* | one `TargetEnv` object + detection | 2 |
| `src/python_deps/depgraph/resolve_lock.py` | marker eval vs full `TargetEnv`, not host | 2 |
| `src/python_deps/depgraph/resolve.py` | pass `--python-platform`; accept extras | 2 |
| `src/python_deps/depgraph/build.py` | build `TargetEnv`; thread extras | 2 |
| `src/python_deps/evidence.py` | carry per-dep extras; tag optional groups | 2 |
| `src/python_deps/depgraph/roots.py` | filter/select extras; carry `pkg[extra]` | 2 |
| `src/python_deps/depgraph/seed.py` | predict soname-canonical native nodes | 3 |
| `src/python_deps/depgraph/ldd_probe.py` | reconcile onto canonical soname node | 3 |

---

## TRACK 1 — Surgical fixes (independent, do first)

### Task 1: Header-tool check must fail rc≠0 when the header is absent (false-SATISFIED)

**Files:**
- Modify: `src/python_deps/depgraph/probe.py:441-448` (`_tool_check`)
- Test: `tests/depgraph/test_probe.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_tool_check(tool: str) -> str` returns a command that exits non-zero when a `.h` header is absent (unchanged for non-header tools: `command -v <tool>`).

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_probe.py
import subprocess, sys
from python_deps.depgraph.probe import _tool_check

def test_header_check_exits_nonzero_when_absent():
    cmd = _tool_check("definitely_absent_xyz.h")
    # the check runs `python -c "..."`; run it on the host and confirm rc != 0
    cmd = cmd.replace("python ", sys.executable + " ", 1)
    rc = subprocess.run(cmd, shell=True).returncode
    assert rc != 0, "absent header must NOT certify (was rc 0 via print())"

def test_header_check_uses_sys_exit_not_print():
    assert "sys.exit" in _tool_check("Python.h")
    assert "print(" not in _tool_check("Python.h")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_probe.py::test_header_check_uses_sys_exit_not_print -v`
Expected: FAIL (current command uses `print(...exists())`, exits rc 0).

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/probe.py  (replace _tool_check)
def _tool_check(tool: str) -> str:
    """Deterministic check_command for a toolchain need (design 4.4)."""
    if tool.endswith(".h"):
        return (
            "python -c \"import sysconfig, pathlib, sys; "
            f"sys.exit(0 if pathlib.Path(sysconfig.get_paths()['include'], '{tool}').exists() else 1)\""
        )
    return f"command -v {tool}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/depgraph/test_probe.py -v && pytest tests/depgraph -q`
Expected: PASS, suite green.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/probe.py tests/depgraph/test_probe.py
git commit -m "fix(depgraph): header-tool check exits rc1 on absent header (no false SATISFIED)"
```

---

### Task 2: Remove wrong `image`→Pillow import mapping

**Files:**
- Modify: `src/python_deps/import_mapping.py:13` (delete the `"image": "Pillow"` row)
- Test: `tests/test_import_mapping.py` (confirmed location of the `map_import_to_package` tests)

**Interfaces:**
- Consumes: `map_import_to_package(import_name, declared_package_names=None) -> MappingResult`.
- Produces: `map_import_to_package("image").package_name != "Pillow"`; `map_import_to_package("pil").package_name == "Pillow"` unchanged.

- [ ] **Step 1: Write the failing test**

```python
from python_deps.import_mapping import map_import_to_package

def test_image_does_not_map_to_pillow():
    # the `image` PyPI distribution is NOT Pillow; Pillow is `PIL`/`pil`
    assert map_import_to_package("image").package_name != "Pillow"

def test_pil_still_maps_to_pillow():
    assert map_import_to_package("pil").package_name == "Pillow"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_import_mapping.py::test_image_does_not_map_to_pillow -v`
Expected: FAIL (currently returns "Pillow").

- [ ] **Step 3: Write minimal implementation**

Delete this single line from `CURATED_IMPORT_TO_PACKAGE` in `src/python_deps/import_mapping.py`:

```python
    "image": "Pillow",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_import_mapping.py -v && pytest tests/depgraph -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/import_mapping.py tests/test_import_mapping.py
git commit -m "fix(depgraph): drop wrong image->Pillow mapping (image PyPI dist != Pillow)"
```

---

### Task 3: Validate node existence for ALL edge relations (no dangling edges)

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` `_validate_edge` (**v3-core: ~lines 288-317**, shifted down from the drafted ~260 because v3-core inserted the `Strength`/`Phase` enums above it; the current `destination`-type error message is at ~line 309 — keep it verbatim)
- Test: `tests/depgraph/test_schema.py`

**Interfaces:**
- Consumes: `DepGraph.with_edge(edge)`, `EdgeType`.
- Produces: `with_edge` raises `ValueError` when `edge.src`/`edge.dst` is not present, for EVERY relation (including `alternative_to`), before the type-rule check.

- [ ] **Step 1: Write the failing test**

```python
from python_deps.depgraph.schema import DepGraph, Edge, EdgeType
import pytest

def test_alternative_to_edge_rejects_unknown_nodes():
    g = DepGraph()
    with pytest.raises(ValueError):
        g.with_edge(Edge(src="import:fake1", dst="import:fake2",
                         relation=EdgeType.ALTERNATIVE_TO))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_schema.py::test_alternative_to_edge_rejects_unknown_nodes -v`
Expected: FAIL (currently returns early for relations absent from `EDGE_RULES`).

- [ ] **Step 3: Write minimal implementation**

Reorder `_validate_edge` so node-existence is checked FIRST, for every relation:

```python
# src/python_deps/depgraph/schema.py  (replace _validate_edge body)
def _validate_edge(self, edge: Edge) -> None:
    src_node = self.get(edge.src)
    dst_node = self.get(edge.dst)
    if src_node is None or dst_node is None:
        raise ValueError(
            f"edge {edge.relation.value} references unknown node(s): "
            f"{edge.src!r} -> {edge.dst!r}"
        )
    rule = EDGE_RULES.get(edge.relation.value)
    if rule is None:
        # Reserved relations (e.g. alternative_to) carry no type rule, but
        # endpoints must still exist.
        return
    allowed_src, allowed_dst = rule
    if src_node.type.value not in allowed_src:
        raise ValueError(
            f"illegal {edge.relation.value} source type "
            f"{src_node.type.value!r} ({edge.src!r})"
        )
    if dst_node.type.value not in allowed_dst:
        raise ValueError(
            f"illegal {edge.relation.value} destination type "
            f"{dst_node.type.value!r} ({edge.dst!r})"
        )
```

(Keep the exact existing wording of the two type-error messages; only the ordering and the unknown-node guard move.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/depgraph/test_schema.py -v && pytest tests/depgraph -q`
Expected: PASS. If any existing test added an `alternative_to`/reserved edge before its nodes, that test was relying on the bug — fix the test to add nodes first.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema.py
git commit -m "fix(depgraph): reject dangling edges for all relations, not just typed ones"
```

---

### Task 4: Conflict drop-retry must drop only the shared package, not the imposing roots

**Files:**
- Modify: `src/python_deps/depgraph/resolve_errors.py:354-362` (`_offending_root_names`)
- Test: `tests/depgraph/test_resolve.py` (or `test_resolve_errors.py` if present)

**Interfaces:**
- Consumes: `ResolverDiagnosis` with `.missing` (each has `.name`) and `.conflicts` (each has `.package`, `.left.imposed_by`, `.right.imposed_by`); helper `_canon`, `_real_imposer`.
- Produces: `_offending_root_names(diag)` returns `{missing names} ∪ {conflict.package}` and NO imposer names.

- [ ] **Step 1: Write the failing test**

```python
from types import SimpleNamespace
from python_deps.depgraph.resolve_errors import _offending_root_names

def test_conflict_drops_shared_package_not_imposers():
    # project pins a<2.0 ; package-b requires a>=2.0  -> shared package = "a"
    conflict = SimpleNamespace(
        package="a",
        left=SimpleNamespace(imposed_by="project"),
        right=SimpleNamespace(imposed_by="package-b"),
    )
    diag = SimpleNamespace(missing=[], conflicts=[conflict])
    names = _offending_root_names(diag)
    assert "a" in names                 # the pin/shared root is dropped and retried
    assert "package-b" not in names     # the imposing root must be KEPT
```

(If the real `Conflict`/`ResolverDiagnosis` dataclasses require constructors, instantiate them instead of `SimpleNamespace` — field names are `package`, `left.imposed_by`, `right.imposed_by`, `missing[].name`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_resolve.py::test_conflict_drops_shared_package_not_imposers -v`
Expected: FAIL (`package-b` currently included via the imposer loop).

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/resolve_errors.py  (replace _offending_root_names)
def _offending_root_names(diag: ResolverDiagnosis) -> set[str]:
    """Canonical names of ROOTS to drop for a retry: missing packages and the
    shared/conflicted package itself. Imposers are NOT dropped — dropping the
    pin root and retrying lets uv pull a consistent version transitively, and
    the conflict is recorded as an advisory edge rather than collapsing both
    subtrees."""
    names: set[str] = {_canon(m.name) for m in diag.missing}
    for c in diag.conflicts:
        names.add(_canon(c.package))
    return names
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/depgraph/test_resolve.py -v && pytest tests/depgraph -q`
Expected: PASS. Confirm the existing conflict test (`flask`-style, non-implicated root) still passes.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/resolve_errors.py tests/depgraph/test_resolve.py
git commit -m "fix(depgraph): conflict retry drops only the shared root, keeps imposers"
```

---

### Task 5: Cross-tier readiness gate applies to ALL packages, not just build-from-source

**Files:**
- Modify: `src/python_deps/depgraph/emit.py:78-86` (`_is_emittable`, PACKAGE branch)
- Test: `tests/depgraph/test_emit_partition.py`

**Interfaces:**
- Consumes: `_toolchain_ready(graph, node)` (already checks required SystemLib/Tool deps are SATISFIED), `partition(graph)`.
- Produces: a `Package` with a MISSING required SystemLib/Tool is NOT in `partition(graph).emittable`, regardless of `build_from_source`.

- [ ] **Step 1: Write the failing test**

```python
from python_deps.depgraph.emit import partition
from python_deps.depgraph.schema import (
    DepGraph, Node, Edge, NodeType, EdgeType, Layer, State, DiscoveredBy)

def _pkg(name, ver, bfs):
    return Node(id=f"pkg:{name}=={ver}", type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                state=State.MISSING, version=ver, build_from_source=bfs,
                check_command=f"python -c 'import {name}'")

def _syslib(soname):
    return Node(id=f"syslib:{soname}", type=NodeType.SYSTEM_LIB, name=soname,
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE,
                state=State.MISSING, check_command=f"ldconfig -p | grep {soname}")

def test_wheel_package_waits_for_its_runtime_syslib():
    g = DepGraph()
    g = g.with_node(_pkg("opencv-python", "4.9.0.80", bfs=False))  # a WHEEL
    g = g.with_node(_syslib("libGL.so.1"))
    g = g.with_edge(Edge(src="pkg:opencv-python==4.9.0.80",
                         dst="syslib:libGL.so.1", relation=EdgeType.REQUIRES))
    part = partition(g)
    emittable_ids = {n.id for n in part.emittable}
    assert "pkg:opencv-python==4.9.0.80" not in emittable_ids  # BUG today: it IS emittable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_emit_partition.py::test_wheel_package_waits_for_its_runtime_syslib -v`
Expected: FAIL (non-BFS package emitted despite MISSING syslib).

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/emit.py  (replace the PACKAGE branch of _is_emittable)
    if node.type is NodeType.PACKAGE:
        if not node.version:           # unresolved -> the LLM's call
            return False
        if not _toolchain_ready(graph, node):
            return False               # native/runtime-link deps must certify first
                                       # (wheel OR sdist — a wheel still dlopens libs)
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/depgraph/test_emit_partition.py -v && pytest tests/depgraph -q`
Expected: PASS. Note: full effect depends on Task 9 (canonical syslib id) so the package's REQUIRES edge points at the node the syslib actually certifies; this gate is correct on whatever node the edge references.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/emit.py tests/depgraph/test_emit_partition.py
git commit -m "fix(depgraph): gate every package on native readiness, not only build-from-source"
```

---

### Task 6: One shared execution-layer order for both `certify` and the artifact renderer

**Files:**
- Modify: `src/python_deps/depgraph/certify.py:25-34` (rename `_LAYER_ORDER` → public `EXECUTION_LAYER_ORDER`, keep `_LAYER_ORDER` as an alias) and `src/python_deps/depgraph/build_script.py:29` (consume it — v3-core line 29, the `_LAYER_ORDER: tuple[Layer, ...] = tuple(Layer)  # enum order == rank order`)
- Test: `tests/depgraph/test_build_script.py`

**v3-core note (why the test asserts on the constant, not on rendered sections):**
- On v3-core `render_build_script` (build_script.py:178) internally calls `populate_setup_commands(graph)`, and section headers are the **Layer value upper-cased** (`_section_header` → `# ==== PIP ====`, `CONFIG`, `RUNTIME`, …) — there is NO `PACKAGES` header, so the drafted `script.index("PACKAGES")` assertion would raise `ValueError`.
- RUNTIME- and TESTS-layer nodes are neither `_is_reciped` nor in `_NEED_TYPES`, so they do not render from trivial fixtures — the RUNTIME-before-PIP and CONFIG-before-TESTS bugs cannot be observed at the rendered-section level with simple nodes.
- Therefore this task asserts directly on the `_LAYER_ORDER` **constant** (exactly what the fix changes). Confirmed values on v3-core: `certify._LAYER_ORDER = (RUNTIME, INTERPRETER, SYSTEM, TOOLCHAIN, PIP, NAMING, CONFIG, TESTS)`; `build_script._LAYER_ORDER = tuple(Layer) = (INTERPRETER, SYSTEM, TOOLCHAIN, PIP, NAMING, RUNTIME, TESTS, CONFIG, SERVICES)` — so today RUNTIME (enum pos 5) sorts AFTER PIP (pos 3) and CONFIG (pos 7) AFTER TESTS (pos 6).

**Interfaces:**
- Consumes: nothing new.
- Produces: `certify.EXECUTION_LAYER_ORDER: tuple[Layer, ...]` (the existing `_LAYER_ORDER` value, re-exported under a public name), consumed by `build_script` for section ordering.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_build_script.py
# Assert on the ordering CONSTANT (what the fix changes). Top-level imports use
# only names that exist today; EXECUTION_LAYER_ORDER is imported lazily inside the
# third test so the module still collects at RED (before Step 3 creates it).
from python_deps.depgraph.build_script import _LAYER_ORDER
from python_deps.depgraph.schema import Layer

def test_runtime_and_interpreter_precede_pip():
    # base python (RUNTIME) + interpreter floor must be laid down BEFORE pip installs
    assert _LAYER_ORDER.index(Layer.RUNTIME) < _LAYER_ORDER.index(Layer.PIP)
    assert _LAYER_ORDER.index(Layer.INTERPRETER) < _LAYER_ORDER.index(Layer.PIP)

def test_config_precedes_tests():
    # env/config tier must be set up before the test tier runs
    assert _LAYER_ORDER.index(Layer.CONFIG) < _LAYER_ORDER.index(Layer.TESTS)

def test_build_script_order_agrees_with_certify_on_shared_tiers():
    from python_deps.depgraph.certify import EXECUTION_LAYER_ORDER  # created in Step 3
    positions = [_LAYER_ORDER.index(L) for L in EXECUTION_LAYER_ORDER]
    assert positions == sorted(positions), (
        "build_script section order must not contradict certify execution order")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_build_script.py::test_runtime_and_interpreter_precede_pip -v`
Expected: FAIL — under v3-core's current `_LAYER_ORDER = tuple(Layer)`, `Layer.RUNTIME` is at enum position 5 and `Layer.PIP` at position 3, so `5 < 3` is False. `test_config_precedes_tests` fails for the same reason (CONFIG pos 7 sorts after TESTS pos 6). `test_build_script_order_agrees...` errors on the missing `EXECUTION_LAYER_ORDER` import until Step 3.

- [ ] **Step 3: Write minimal implementation**

In `certify.py`, rename/alias the constant to a public name and keep `_LAYER_ORDER` as an alias:

```python
# src/python_deps/depgraph/certify.py
EXECUTION_LAYER_ORDER: tuple[Layer, ...] = (
    Layer.RUNTIME, Layer.INTERPRETER, Layer.SYSTEM, Layer.TOOLCHAIN,
    Layer.PIP, Layer.NAMING, Layer.CONFIG, Layer.TESTS,
)
_LAYER_ORDER = EXECUTION_LAYER_ORDER  # backwards-compat alias
```

In `build_script.py`, replace `_LAYER_ORDER: tuple[Layer, ...] = tuple(Layer)` (v3-core line 29) with the shared order plus any Layer values not in it (so no section is silently dropped). Verified safe on v3-core: `certify` imports neither `build_script` nor `populate`, so this module-level import introduces no cycle.

```python
# src/python_deps/depgraph/build_script.py
from python_deps.depgraph.certify import EXECUTION_LAYER_ORDER
_LAYER_ORDER: tuple[Layer, ...] = EXECUTION_LAYER_ORDER + tuple(
    L for L in Layer if L not in EXECUTION_LAYER_ORDER
)   # SERVICES (and any future Layer) render after the certified execution tiers
```

On v3-core this makes `_LAYER_ORDER = (RUNTIME, INTERPRETER, SYSTEM, TOOLCHAIN, PIP, NAMING, CONFIG, TESTS, SERVICES)` — RUNTIME/INTERPRETER now precede PIP and CONFIG precedes TESTS, and no Layer is dropped (`SERVICES` is appended).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/depgraph/test_build_script.py -v && pytest tests/depgraph -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/certify.py src/python_deps/depgraph/build_script.py tests/depgraph/test_build_script.py
git commit -m "fix(depgraph): unify execution-layer order across certify and artifact renderer"
```

---

## TRACK 2 — Structural fixes (the two that create the clean pipeline)

> These are larger than a single 5-minute edit; each is written as one task with a full test contract and a concrete implementation approach. At execution, expand each into the standard write-test → fail → implement → pass → commit micro-cycle per sub-file. Do them in order.

### Task 7: `TargetEnv` — resolve for the target box, not the host

**Files:**
- Create: `src/python_deps/depgraph/target_env.py`
- Modify: `src/python_deps/depgraph/resolve_lock.py` (`_python_marker_env` → accept a full env), `src/python_deps/depgraph/resolve.py` (pass `--python-platform`; thread env into `parse_uv_lock`), `src/python_deps/depgraph/build.py:244-273` (build `TargetEnv`, pass it down)
- Test: `tests/depgraph/test_target_env.py` (Create), plus additions to `tests/depgraph/test_resolve.py`

**v3-core scaffolding already present (widen, don't build from scratch):** on v3-core `resolve.resolve_closure(...)` already accepts a `target_platform` argument (resolve.py:180) and `_write_pyproject(...)` already takes `target_python` (resolve.py:136-153) — so `TargetEnv` threads INTO existing seams rather than adding platform plumbing. The real gaps are: (a) `_lock_command` (resolve.py:156-164) has `--python` but NO `--python-platform` — add it from `target_env.python_platform_tag`; (b) `resolve_lock._python_marker_env` returns only the two python keys, leaking `sys_platform`/`platform_machine`/`os_name` from the host — replace with the full `TargetEnv.marker_env()`.

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) TargetEnv{ python_full: str, python_version: str, platform_machine: str, sys_platform: str, os_name: str, platform_system: str, python_platform_tag: str }`
  - `TargetEnv.marker_env(self) -> dict[str, str]` returning ALL PEP 508 fields (not just python_version).
  - `detect_target_env(container_executor) -> TargetEnv` (runs `python3 -c "import platform,sys,os; ..."` + `uname` inside the container).
  - `resolve_lock._python_marker_env` becomes `_marker_env(target: TargetEnv) -> dict[str,str]` (or takes the dict) so `sys_platform`/`platform_machine`/`os_name` no longer leak from the host `default_environment()`.
  - `resolve.resolve_closure(..., target_env: TargetEnv)`; the `uv lock` command gains `--python-platform {target_env.python_platform_tag}`.

- [ ] **Test contract (write these first, watch them fail):**

```python
# tests/depgraph/test_target_env.py
from python_deps.depgraph.target_env import TargetEnv

def test_marker_env_has_all_platform_fields():
    t = TargetEnv(python_full="3.11.0", python_version="3.11",
                  platform_machine="x86_64", sys_platform="linux",
                  os_name="posix", platform_system="Linux",
                  python_platform_tag="x86_64-manylinux_2_28")
    env = t.marker_env()
    assert env["platform_machine"] == "x86_64"
    assert env["sys_platform"] == "linux"
    assert env["os_name"] == "posix"

# tests/depgraph/test_resolve.py  (marker pruning must honor TARGET, not host)
from python_deps.depgraph.resolve_lock import _marker_env  # new signature
from python_deps.depgraph.target_env import TargetEnv

def test_x86_gated_dep_kept_when_target_is_x86_even_on_arm_host():
    target = TargetEnv(python_full="3.11.0", python_version="3.11",
                       platform_machine="x86_64", sys_platform="linux",
                       os_name="posix", platform_system="Linux",
                       python_platform_tag="x86_64-manylinux_2_28")
    from python_deps.depgraph.resolve_lock import _marker_applies
    assert _marker_applies("platform_machine == 'x86_64'", _marker_env(target)) is True
    assert _marker_applies("sys_platform == 'win32'", _marker_env(target)) is False
```

- [ ] **Implementation approach:**
  1. Create `TargetEnv` + `marker_env()` (returns the 6 fields + both python keys) + `detect_target_env(executor)` that runs one probe command in the container:
     `python3 -c "import platform,sys,os,sysconfig; print(sys.version.split()[0], os.name, sys.platform, platform.machine(), platform.system())"` and derives `python_platform_tag` (machine + a libc guess: `manylinux_2_28` for glibc, `musllinux_1_2` for musl — detect via `ldd --version`/`/etc/os-release` or default glibc). Degrade to sensible defaults on any failure (never crash the resolve).
  2. In `resolve_lock.py`, replace `_python_marker_env(target_python)` with `_marker_env(target: TargetEnv)` returning `target.marker_env()`. Update `_marker_applies` call sites to pass the full env. **Critically**, ensure the dict passed to `Marker.evaluate()` contains all platform keys so `packaging` does NOT fall back to the host's `default_environment()` for them.
  3. In `resolve.py`, add `--python-platform {target_env.python_platform_tag}` to the `uv lock` command; thread `target_env` into `parse_uv_lock(...)` and `native_risk_from_lock(...)` wherever `target_python` currently flows.
  4. In `build.py`, replace the separate `_detect_target_python` / `_detect_target_platform` calls with one `detect_target_env(container_executor)` (keep the `target_python`/`target_platform` params as optional overrides that construct/patch the `TargetEnv`). Pass `target_env` to `resolve_closure`.
  5. Update the stdlib filter (`roots.py:_is_non_distribution`) to accept the target python's stdlib set if available (follow-up-acceptable: leave a `# TODO(target-stdlib)` only if the target set isn't readily available — do NOT silently keep using host `sys.stdlib_module_names` without a note).

- [ ] **Verify:** `pytest tests/depgraph -q` green; add a test asserting the `uv lock` command string built by `resolve.py` contains `--python-platform`.

- [ ] **Commit** per sub-file (`feat(depgraph): TargetEnv`, `fix(depgraph): marker eval vs target not host`, `fix(depgraph): uv lock --python-platform`).

---

### Task 8: Targeted extras — resolve the extras the tests need, not all groups (and not stripped)

**Files:**
- Modify: `src/python_deps/models.py:7-17` (add `extras: tuple[str, ...] = ()` to `PythonRequirement`), `src/python_deps/evidence.py:273-287` (`_parse_requirement_line` returns a 3-tuple `(name, specifier, marker)` today → make it a 4-tuple carrying `tuple(requirement.extras)`; `_add_requirement_line` stores it; the optional-group tag already exists as `kind="optional_dependency"`), `src/python_deps/depgraph/roots.py` (accept a `needed_extras` set; filter `kind`; carry `pkg[extra]` tokens), `src/python_deps/depgraph/resolve.py` (accept extras; write them into the temp pyproject), `src/python_deps/depgraph/build.py` (source the needed-extras set)
- Test: Modify `tests/depgraph/test_roots.py` (exists); Create `tests/depgraph/test_evidence.py` (evidence-parsing tests currently live only in `test_evidence_bundle.py`)

**Interfaces:**
- Produces:
  - `select_roots(repo_path, graph, needed_extras: frozenset[str] = frozenset())` — includes a `kind=="optional_dependency"` root ONLY if its group ∈ `needed_extras`; runtime deps always included.
  - Per-dep extras preserved: a declared `uvicorn[standard]` yields a root token `uvicorn[standard]` (not bare `uvicorn`), so uv resolves the extra's transitive deps.
  - `resolve_closure(..., extras: frozenset[str] = frozenset())` writes `[project.optional-dependencies]` (or a root spec `.[extra]`) into the temp pyproject so the resolve pins the extra's deps.

- [ ] **Test contract (write first):**

```python
# tests/depgraph/test_roots.py
from python_deps.depgraph.roots import select_roots
from python_deps.depgraph.schema import DepGraph
# fixture repo with pyproject: dependencies=[requests], optional-deps: {test:[pytest], docs:[sphinx]}

def test_only_needed_extra_group_becomes_a_root(tmp_repo):
    roots = select_roots(tmp_repo, DepGraph(), needed_extras=frozenset({"test"}))
    names = {tok for _, tok in roots}
    assert any(t.startswith("requests") for t in names)   # runtime always
    assert any(t.startswith("pytest") for t in names)     # needed extra
    assert not any(t.startswith("sphinx") for t in names) # unneeded group excluded

def test_per_dep_extra_specifier_is_preserved(tmp_repo_uvicorn):
    # pyproject dependencies = ["uvicorn[standard]>=0.20"]
    roots = select_roots(tmp_repo_uvicorn, DepGraph())
    names = {tok for _, tok in roots}
    assert any("uvicorn[standard]" in t for t in names)   # extra NOT stripped
```

- [ ] **Implementation approach:**
  1. `models.py` + `evidence.py`: add `extras: tuple[str, ...] = ()` to `PythonRequirement` (`models.py:7`); update `_parse_requirement_line` (`evidence.py:273`) to also return `tuple(requirement.extras)` (3-tuple → 4-tuple) and `_add_requirement_line` to store it; keep the existing `kind`/group tag on optional-dependency entries.
  2. `roots.py`: add `needed_extras` param; in the manifest loop, skip a `kind=="optional_dependency"` requirement whose group ∉ `needed_extras`. In `_manifest_root_token`, emit `f"{req.name}[{','.join(req.extras)}]{spec}"` when `req.extras` is non-empty. Also honor `req.marker` (skip a dep whose marker is False for the target — needs the `TargetEnv` from Task 7).
  3. `resolve.py`: accept `extras`, and when writing the temp pyproject either add the chosen groups under `[project.optional-dependencies]` or feed root specs of the form `project[extra]`.
  4. `build.py`: compute `needed_extras`. **Decision (pick and document):** default source = parse CI/tox/Makefile for `pip install -e .[...]` / `extras=` (cluster-1 enrichment); fallback = `frozenset()` (runtime only). Do NOT union all groups. Log which extras were chosen and why.

- [ ] **Verify:** `pytest tests/depgraph -q` green; add a resolve-level test that a `.[test]` closure contains the test group's transitive deps and a no-extras closure does not.

- [ ] **Commit** per sub-file.

---

## TRACK 3 — Canonical native identity (largest structural fix)

### Task 9: One canonical SystemLib identity so prediction and `ldd` observation reconcile

**Files:**
- Modify: `src/python_deps/depgraph/seed.py:53-74` (`_predicted_node`), `src/python_deps/depgraph/ldd_probe.py:160-205` (reconciliation), possibly `src/python_deps/depgraph/apt_resolve.py`
- Test: `tests/depgraph/test_ldd_probe.py`, `tests/depgraph/test_seed.py`

**Problem:** `seed.py` ids a predicted native by **apt name** (`syslib_id(apt)` → `syslib:libgl1`); `ldd_probe.py` ids an observed native by **soname** (`syslib_id(soname)` → `syslib:libGL.so.1`). When soname→apt resolution fails they coexist as two nodes for one library, and neither installs.

**Canonical rule:** the **soname is the identity** (it is the observable, install-ground-truth key from `ldd`); the apt package is a **`chosen_fix` attribute**, not the id.

**Interfaces:**
- Produces: for a given library there is exactly ONE `SystemLib` node id across the seed and ldd paths. Seed predictions attach to (or create) the soname-keyed node; the apt name lives in `chosen_fix`/`fix_candidates`.

- [ ] **Test contract (write first):**

```python
# tests/depgraph/test_ldd_probe.py
def test_seed_and_ldd_produce_one_node_for_same_lib(fake_container):
    # 1) seed predicts opencv needs libGL (canonical soname node, apt in chosen_fix)
    # 2) ldd observes 'libGL.so.1 => not found'
    # After both stages there is exactly ONE syslib node for libGL, and the
    # requiring package's REQUIRES edge points at it.
    g = run_seed_then_ldd(fake_container)   # helper builds the two-stage graph
    syslibs = [n for n in g.nodes if n.type.value == "SystemLib" and "GL" in n.id]
    assert len(syslibs) == 1
    edges = [e for e in g.edges if e.dst == syslibs[0].id and e.relation.value == "requires"]
    assert edges, "the requiring package must point at the single canonical node"
```

- [ ] **Implementation approach (choose the lighter viable option, in order):**
  - **Option A (preferred, cleanest):** make the curated native table map `package -> soname` (not `package -> apt`). Then `seed._predicted_node` builds a **soname-keyed** node with `check_command = "ldconfig -p | grep <soname>"` and `chosen_fix = apt:<resolve_soname_apt(soname)>` (or `None`, later filled). ldd's observed node then lands on the SAME id — no split. Requires migrating the `PACKAGE_TO_SYSTEM_DEPS` table (`tables.py:67-73`, currently apt-keyed) entries from apt names to sonames; the soname→apt map `NATIVE_LIB_TO_APT` (`tables.py:18-31`) already exists to fill `chosen_fix` (data change + table test).
  - **Option B (if the table can't be migrated now):** keep seed apt-keyed, but in `ldd_probe`, when a soname resolves to an apt name, reconcile onto the apt-keyed node (already done); when it does NOT resolve, check whether the requiring package already has a predicted apt-keyed SystemLib node and **merge the observed soname into that node's `data`** (record the soname, keep the single node) instead of creating a rival. This is a heuristic bridge, not a true canonicalization — document it as interim.
  - Either way: after reconciliation, ensure the requiring package has exactly one REQUIRES edge to the surviving node (drop the superseded edge via `without_node`/edge cleanup, mirroring `relink._drop_superseded_ghosts`).

- [ ] **Verify:** `pytest tests/depgraph -q` green; add a regression test for the unresolved-soname path (apt-file absent) asserting a single node. This is the exact opencv/libGL production case.

- [ ] **Commit:** `fix(depgraph): canonical soname identity for native libs (reconcile seed+ldd)`.

---

## Follow-ups (lower severity — batch after the above, each its own small TDD task)

- **Functional Package certification (MEDIUM):** transitive packages with no Import node are certified by `pip show` (metadata only). Strengthen: reuse the Import node's `import X` check as the package's authority where one exists; for import-less transitive packages, add an importability check via `top_level.txt`. Design-bearing (needs package→module resolution) — spec separately.
- **Non-build install failure salvage (MEDIUM):** `probe._failed_build_packages` only parses wheel-build summaries; a resolution-conflict / "No matching distribution" collapses the whole closure to MISSING. Add parsing for those error classes so survivors still install.
- **python-incompat imposer never drop-retried (MEDIUM):** `_offending_root_names` ignores `diag.python_incompat`; add it to the drop set.
- **`TYPE_CHECKING`-only imports become roots (LOW):** skip imports inside `if TYPE_CHECKING:` blocks in the scan.
- **Silent 1000-file scan truncation (LOW):** append a truncation notice to `scan_imports().errors` when `MAX_PYTHON_FILES` is hit.
- **Declared-name can't override curated alias (LOW):** let a manifest-declared distribution outrank the curated table for the same import.
- **Single global `exclude_newer` (LOW):** consider per-root era anchoring so a newer co-pin doesn't defeat an older pin's cutoff.
- **`--no-deps` on the live block path (from the install-vs-pip report, adjacent) — PARTIALLY already fixed on v3-core:** the STATIC whole-script path is already correct here: `render_build_script` → `populate.populate_setup_commands` → `populate._command_for` (populate.py:24) emits `python3 -m pip install --break-system-packages --no-deps {pip_spec}`. The remaining gap is the **live / round-trippable** path: `block.py:33` (`_command_for`, feeding `script.render_setup_sh`) still emits `python3 -m pip install --break-system-packages {pip_spec}` WITHOUT `--no-deps`. Add `--no-deps` there so both renderers agree (they are separate copies of the derivation — `block.py` is IDENTICAL between main and v3-core). Not a construction bug; fix when touching the artifact path.

---

## Self-Review

- **Spec coverage:** every HIGH bug from the audit maps to a task — platform markers → Task 7; opencv cluster → Tasks 5 + 9 (gate + canonical id); header false-SATISFIED → Task 1; conflict retry → Task 4; extras → Task 8; image→Pillow → Task 2; dangling edges → Task 3; layer divergence → Task 6. MEDIUM/LOW → Follow-ups.
- **Placeholder scan:** surgical tasks (1–6) carry complete code. Structural tasks (7–9) carry complete TEST code + a concrete implementation approach naming the real target functions/lines; they are explicitly larger-than-one-edit and expand into micro-cycles at execution.
- **Type consistency:** `TargetEnv` fields used in Task 8 (`req.marker` gating needs the target env) match Task 7's `TargetEnv.marker_env()`; `_toolchain_ready` (Task 5) is the same predicate used in `emit.py`; canonical soname id (Task 9) is what Task 5's gate resolves against.
- **Ordering rationale:** Tasks 1–6 are independent and each shippable alone (start here for momentum + coverage). Task 7 (TargetEnv) is the biggest *silent*-bug win. Task 8 (extras) depends on Task 7 for marker gating. Task 9 (canonical id) is last and completes Task 5's full benefit.
