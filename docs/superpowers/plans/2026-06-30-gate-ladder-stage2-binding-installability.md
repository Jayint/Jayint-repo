# Gate-Ladder Stage 2 — Binding Dep-Spine Installability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a flag-gated install path that compiles the graph with `render_build_script`, runs it from a clean base container, and host-certifies every reciped node — a *binding dep-spine `ebsr`* gate — feeding localized failures into the existing repair loop. Default off ⇒ byte-identical to today.

**Architecture:** A new control surface on `Sandbox` (`reset_to_base`, `run_install_script`) + a pure localizer module + a flag-gated branch in `run_v3._dep_emit_phase` that does render → reset-to-base → install → certify-reciped, with the existing `run_structured_repair` driving fixes (one small additive param). Everything is injected as optional callables (Stage-1 `enable_gate_observability` precedent); unit tests are Docker-free (pure helpers + fake callables); the real-container path is exercised by the l2 smoke driver.

**Tech Stack:** Python 3.10+, pytest, `docker` SDK (`Sandbox`), existing `DepGraph`/`render_build_script`/`certify_refresh`/`run_structured_repair`.

**Spec:** `docs/superpowers/specs/2026-06-30-gate-ladder-stage2-binding-installability-design.md` (read §1.5, §3, §4, §6, §7, §12).

## Global Constraints

- `enable_binding_install` defaults **False** ⇒ behavior BYTE-IDENTICAL to Stage 1 (this is a required test in T3).
- `run_v1` and the B3 ablation (`enable_script_materialization=False`) are UNTOUCHED.
- `render_build_script` (`src/python_deps/depgraph/build_script.py`) is **reuse-only — never modify it** (handoff invariant).
- **Binding dep-spine installability ⇔** install rc 0 **AND** every `_is_reciped` node that has a `check_command` certifies `State.SATISFIED` **AND** no `_is_reciped` node lacks a `check_command` (the consumer raises at render time if one does).
- `run_install_script` MUST bypass `Sandbox.execute()` (its `_get_invalid_compound_setup_prefix` preflight rejects multi-step scripts) — call `container.exec_run` directly, like `exec_readonly` (sandbox.py:172-192). It never `commit()`s a snapshot; `reset_to_base()` always precedes it.
- `reset_to_base()` ALWAYS recreates from `self.base_image` (distinct from `rollback()`/`_restore_last_success_container`, which use `last_success_image`).
- `certify_refresh(graph, exec_readonly, cycle)` — `cycle` is a required positional arg.
- **Dep-spine scope only.** Project install (`pip install -e .`), `#@need`/`#@block` certification, and service/config are **Stage 2.5** — do NOT implement them here.
- Unit tests are Docker-free (pure helpers + injected fakes). Do NOT add tests that require a live Docker daemon to the default suite; the real-container path is covered by `scripts/l2_repair_loop_smoke.py`.
- Anti-hollow: node/gate state is written only by host certification; the LLM only proposes typed patches. Checks prefer importability over metadata; a PatchGate guard rejects a check that cannot detect absence.

## File Structure

- **Modify** `src/sandbox.py` — `InstallResult`, pure helpers `_wrap_with_err_trap`/`_parse_install_failure`, `reset_to_base()`, `run_install_script()`, optional cache-volume mount.
- **Create** `src/envstate/install_localizer.py` — `localize_install_failure`, `certify_reciped_only`, `assemble_install_debug_bundle`.
- **Modify** `src/envstate/orchestrator.py` — `run_v3` kwargs + `_dep_emit_phase` flag-gated branch.
- **Modify** `src/envstate/repair_loop.py` — `cap_failed_id` param.
- **Modify** `agent.py`, `src/envstate/_loop_common.py`, `scripts/l2_repair_loop_smoke.py` — thread the new callables/flag.
- **Modify** `src/python_deps/depgraph/patch_gate.py` (+ a small check-rewrite helper) — anti-weakening guard + deterministic SystemLib check rewrite.

**Task order (dependencies):** (T1 ∥ T2) → T3 → (T4 ∥ T5); T6 independent.

---

### Task 1: Sandbox control surface — `InstallResult`, `reset_to_base`, `run_install_script`, cache volume

**Files:**
- Modify: `src/sandbox.py`
- Test: `tests/test_sandbox_install_helpers.py` (Docker-free — tests the pure helpers only)

**Interfaces:**
- Produces: `@dataclass(frozen=True) InstallResult(rc: int, failing_command: str | None, lineno: int | None, stderr: str)`; `_wrap_with_err_trap(script: str) -> str`; `_parse_install_failure(output: str) -> tuple[str | None, int | None]`; `Sandbox.reset_to_base() -> None`; `Sandbox.run_install_script(script: str) -> InstallResult`; `Sandbox.__init__(..., enable_cache_volume: bool = False)`.
- Consumes: existing `self.container`, `self.client`, `self.base_image`, `self.workdir`, `self.volumes`, `self.platform`, `_service_extra_hosts()` (sandbox.py).

- [ ] **Step 1: Write the failing test** — `tests/test_sandbox_install_helpers.py`:

```python
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.sandbox import InstallResult, _wrap_with_err_trap, _parse_install_failure


def test_wrap_prepends_err_trap_and_keeps_script():
    wrapped = _wrap_with_err_trap("apt-get install -y libgl1\n")
    assert "trap " in wrapped and "ERR" in wrapped
    assert "$BASH_COMMAND" in wrapped and "$LINENO" in wrapped
    assert "apt-get install -y libgl1" in wrapped  # original body preserved


def test_parse_failure_extracts_command_and_lineno():
    out = "some log\n__INSTALL_FAIL__:apt-get install -y libgl1:42\nmore log\n"
    cmd, lineno = _parse_install_failure(out)
    assert cmd == "apt-get install -y libgl1"
    assert lineno == 42


def test_parse_failure_none_when_no_marker():
    cmd, lineno = _parse_install_failure("clean run, no failures\n")
    assert cmd is None and lineno is None


def test_parse_failure_takes_first_marker():
    out = "__INSTALL_FAIL__:cmdA:1\n__INSTALL_FAIL__:cmdB:2\n"
    cmd, lineno = _parse_install_failure(out)
    assert cmd == "cmdA" and lineno == 1


def test_install_result_is_frozen():
    import dataclasses
    r = InstallResult(rc=0, failing_command=None, lineno=None, stderr="")
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        r.rc = 1  # type: ignore[misc]
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `pytest tests/test_sandbox_install_helpers.py -v`
Expected: `ImportError: cannot import name 'InstallResult' from 'src.sandbox'`

- [ ] **Step 3: Implement** — in `src/sandbox.py`, add near the top (after the existing imports, before `class Sandbox`):

```python
from dataclasses import dataclass

_INSTALL_FAIL_MARKER = "__INSTALL_FAIL__"


@dataclass(frozen=True)
class InstallResult:
    rc: int
    failing_command: str | None   # $BASH_COMMAND captured by the ERR trap; None on success
    lineno: int | None
    stderr: str


def _wrap_with_err_trap(script: str) -> str:
    """Prepend an ERR trap so a `set -e` abort prints the failing command + line.

    render_build_script is reuse-only (its preamble already sets `set -Eeuo pipefail`);
    we add ONLY the trap line, immediately after the shebang/preamble is irrelevant —
    bash applies the most recent trap, and `-E` makes the trap inherit into functions.
    """
    trap = (
        "trap 'rc=$?; echo "
        f"\"{_INSTALL_FAIL_MARKER}:$BASH_COMMAND:$LINENO\" >&2; exit $rc' ERR\n"
    )
    return trap + script


def _parse_install_failure(output: str) -> tuple[str | None, int | None]:
    """Return (failing_command, lineno) from the FIRST install-fail marker, else (None, None)."""
    for line in (output or "").splitlines():
        if line.startswith(_INSTALL_FAIL_MARKER + ":"):
            rest = line[len(_INSTALL_FAIL_MARKER) + 1:]
            cmd, _, lineno_s = rest.rpartition(":")
            try:
                return (cmd or None), int(lineno_s)
            except ValueError:
                return (cmd or None), None
    return None, None
```

Then add these methods to `class Sandbox` (after `execute`, near `rollback`):

```python
    def reset_to_base(self) -> None:
        """Recreate the container fresh from base_image (NOT last_success_image).

        Distinct from rollback()/_restore_last_success_container, which restore the
        last good snapshot. Used by the Stage-2 binding-install gate so every install
        attempt runs from clean. Does NOT replay runtime services (install-only path).
        """
        if self.container is not None:
            try:
                self.container.stop()
            except docker.errors.DockerException:
                pass
            try:
                self.container.remove()
            except docker.errors.DockerException:
                pass
        _extra_hosts = _service_extra_hosts()
        self.container = self.client.containers.run(
            self.base_image, detach=True, tty=True, working_dir=self.workdir,
            command="/bin/bash", volumes=self.volumes, platform=self.platform,
            **({} if _extra_hosts is None else {"extra_hosts": _extra_hosts}),
        )
        self.container.exec_run(f"mkdir -p {self.workdir}")
        self._bootstrap_apt_if_supported()
        if self.seed_dir:
            self._seed_workdir_from_host()
        self.current_image = self.base_image

    def run_install_script(self, script: str) -> InstallResult:
        """Run an install-only setup.sh in the CURRENT container, bypassing execute()'s
        preflight (which rejects multi-step scripts). Returns InstallResult with the
        ERR-trap-localized failing command on rc!=0.

        Invariant: this does NOT commit a snapshot; the Stage-2 gate always calls
        reset_to_base() before this, so last_success_image is never relied upon here.
        """
        wrapped = _wrap_with_err_trap(script)
        result = self.container.exec_run(["/bin/bash", "-c", wrapped], workdir=self.workdir)
        output = result.output
        if isinstance(output, (bytes, bytearray)):
            output = output.decode("utf-8", errors="replace")
        rc = result.exit_code if result.exit_code is not None else -1
        failing_command, lineno = _parse_install_failure(output or "")
        return InstallResult(rc=rc, failing_command=failing_command, lineno=lineno,
                             stderr=output or "")
```

Finally, add the cache-volume option to `__init__` — add the parameter `enable_cache_volume: bool = False,` to the signature, and right before `self._setup_initial_container()` insert:

```python
        if enable_cache_volume:
            cache = dict(self.volumes or {})
            cache.setdefault("jayint_pip_cache", {"bind": "/root/.cache/pip", "mode": "rw"})
            cache.setdefault("jayint_apt_cache", {"bind": "/var/cache/apt/archives", "mode": "rw"})
            self.volumes = cache
```

- [ ] **Step 4: Run it — expect PASS**

Run: `pytest tests/test_sandbox_install_helpers.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sandbox.py tests/test_sandbox_install_helpers.py
git commit -m "feat(stage2): Sandbox.reset_to_base + run_install_script + InstallResult + cache volume"
```

> NOTE: `reset_to_base`/`run_install_script` are Docker-bound and covered by the l2 smoke (T5), not the default suite.

---

### Task 2: Install localizer + reciped-only certify + debug bundle

**Files:**
- Create: `src/envstate/install_localizer.py`
- Test: `tests/envstate/test_install_localizer.py` (Docker-free)

**Interfaces:**
- Produces:
  - `localize_install_failure(script: str, failing_command: str | None) -> LocalizedFailure` where `@dataclass(frozen=True) LocalizedFailure(node_id: str | None, block_lines: tuple[str, ...])`
  - `certify_reciped_only(graph, exec_readonly, cycle: int) -> tuple[object, tuple[str, ...]]` — runs `certify_refresh` then returns `(graph, unsatisfied_reciped_ids)` where unsatisfied = `_is_reciped` nodes whose state is not `State.SATISFIED`
  - `assemble_install_debug_bundle(localized: LocalizedFailure, stderr: str, repair_scope_text: str, window: tuple[str, ...]) -> str`
- Consumes: `certify_refresh` (`src/envstate/depgraph_live.py`), `_is_reciped` (`python_deps.depgraph.emit`), `State` (`python_deps.depgraph.schema`).

The renderer annotates each install line with a preceding `#@node <id> ...` (or `#@block <id> ...`) line (`build_script.py:_annotation`). Localization scans for the most recent `#@node`/`#@block` id at or before the failing command.

- [ ] **Step 1: Write the failing test** — `tests/envstate/test_install_localizer.py`:

```python
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)
from src.envstate.install_localizer import (
    LocalizedFailure, localize_install_failure, certify_reciped_only,
    assemble_install_debug_bundle,
)

_SCRIPT = """#!/usr/bin/env bash
set -Eeuo pipefail
# ==================== SYSTEM ====================
apt-get update
#@node syslib:libgl1  provider=apt:libgl1  requires=-
#@check dpkg -s libgl1
apt-get install -y --no-install-recommends libgl1
# ==================== PIP ====================
#@node pkg:numpy==2.4.6  version=2.4.6  requires=-
#@check python -m pip show numpy
python3 -m pip install --break-system-packages --no-deps numpy==2.4.6
"""


def test_localize_maps_failing_command_to_node():
    loc = localize_install_failure(_SCRIPT, "apt-get install -y --no-install-recommends libgl1")
    assert loc.node_id == "syslib:libgl1"
    assert any("apt-get install -y --no-install-recommends libgl1" in l for l in loc.block_lines)


def test_localize_maps_pip_line_to_pip_node():
    loc = localize_install_failure(_SCRIPT, "python3 -m pip install --break-system-packages --no-deps numpy==2.4.6")
    assert loc.node_id == "pkg:numpy==2.4.6"


def test_localize_none_command_returns_no_node():
    loc = localize_install_failure(_SCRIPT, None)
    assert loc.node_id is None


def _syslib(state: State) -> Node:
    return Node(id="syslib:libgl1", type=NodeType.SYSTEM_LIB, name="libgl1",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=state,
                check_command="dpkg -s libgl1", chosen_fix="apt:libgl1")


def test_certify_reciped_only_flags_unsatisfied_reciped(monkeypatch):
    g = DepGraph().with_node(_syslib(State.MISSING))
    # Inject a fake certify_refresh that leaves the node MISSING (install "succeeded" but check fails).
    import src.envstate.install_localizer as mod
    monkeypatch.setattr(mod, "certify_refresh", lambda graph, ro, cycle: graph)
    out_graph, unsat = certify_reciped_only(g, lambda cmd: (1, ""), cycle=1)
    assert "syslib:libgl1" in unsat


def test_certify_reciped_only_clean_when_satisfied(monkeypatch):
    g = DepGraph().with_node(_syslib(State.SATISFIED))
    import src.envstate.install_localizer as mod
    monkeypatch.setattr(mod, "certify_refresh", lambda graph, ro, cycle: graph)
    out_graph, unsat = certify_reciped_only(g, lambda cmd: (0, "ok"), cycle=1)
    assert unsat == ()


def test_assemble_bundle_contains_all_three_parts():
    loc = LocalizedFailure(node_id="syslib:libgl1", block_lines=("#@node syslib:libgl1", "apt-get install -y libgl1"))
    bundle = assemble_install_debug_bundle(loc, "E: Unable to locate package",
                                           "RepairScope: providers=[apt:libgl1]", ("ctx line",))
    assert "syslib:libgl1" in bundle
    assert "Unable to locate package" in bundle
    assert "RepairScope" in bundle
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `pytest tests/envstate/test_install_localizer.py -v`
Expected: `ModuleNotFoundError: No module named 'src.envstate.install_localizer'`

- [ ] **Step 3: Implement** — create `src/envstate/install_localizer.py`:

```python
"""Stage 2 install-failure localization + reciped-only certify + debug-bundle assembly.

Pure / read-only except certify_reciped_only, which delegates state writes to the host
certify pass. No Docker/LLM imports at module level.
"""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.emit import _is_reciped
from python_deps.depgraph.schema import State
from src.envstate.depgraph_live import certify_refresh

_EVIDENCE_CAP = 500
_WINDOW = 3  # annotated lines kept above/below the failing line


@dataclass(frozen=True)
class LocalizedFailure:
    node_id: str | None
    block_lines: tuple[str, ...]


def _node_id_of(line: str) -> str | None:
    s = line.strip()
    for prefix in ("#@node ", "#@block "):
        if s.startswith(prefix):
            return s[len(prefix):].split()[0]
    return None


def localize_install_failure(script: str, failing_command: str | None) -> LocalizedFailure:
    """Map the failing command to the most recent #@node/#@block id at/above it,
    returning that id plus a bounded window of surrounding lines."""
    if not failing_command:
        return LocalizedFailure(node_id=None, block_lines=())
    lines = script.splitlines()
    fail_idx = next((i for i, l in enumerate(lines) if failing_command in l), None)
    if fail_idx is None:
        return LocalizedFailure(node_id=None, block_lines=())
    node_id = None
    for i in range(fail_idx, -1, -1):
        nid = _node_id_of(lines[i])
        if nid is not None:
            node_id = nid
            break
    lo = max(0, fail_idx - _WINDOW)
    hi = min(len(lines), fail_idx + _WINDOW + 1)
    return LocalizedFailure(node_id=node_id, block_lines=tuple(lines[lo:hi]))


def certify_reciped_only(graph, exec_readonly, cycle: int):
    """Run the host certify pass, then return (graph, unsatisfied_reciped_ids).

    The binding gate is evaluated ONLY over _is_reciped nodes — #@need stubs
    (CONFIG/SERVICE/DATA_ASSET) are excluded (they are Stage-2.5)."""
    graph = certify_refresh(graph, exec_readonly, cycle)
    unsat = tuple(
        n.id for n in graph.nodes
        if _is_reciped(n) and n.state is not State.SATISFIED
    )
    return graph, unsat


def assemble_install_debug_bundle(localized: LocalizedFailure, stderr: str,
                                  repair_scope_text: str, window: tuple[str, ...]) -> str:
    """Three-part bundle: localized failure (node + block) + RepairScope slice + script window."""
    parts = [
        f"## Failing node: {localized.node_id or '(unmapped)'}",
        "### Failing block",
        "\n".join(localized.block_lines),
        "### stderr",
        (stderr or "")[-_EVIDENCE_CAP:],
        "### Graph slice (RepairScope)",
        repair_scope_text,
    ]
    if window:
        parts += ["### Script context", "\n".join(window)]
    return "\n".join(parts)
```

- [ ] **Step 4: Run it — expect PASS**

Run: `pytest tests/envstate/test_install_localizer.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/install_localizer.py tests/envstate/test_install_localizer.py
git commit -m "feat(stage2): install localizer + certify_reciped_only + debug-bundle"
```

---

### Task 3: Flag-gated binding-install branch in `run_v3` / `_dep_emit_phase`

**Files:**
- Modify: `src/envstate/orchestrator.py` (`run_v3` signature ~319-337; `_dep_emit_phase` ~399-466)
- Test: `tests/test_binding_install_wiring.py` (Docker-free — injected fakes)

**Interfaces:**
- Consumes: `InstallResult` (T1, via the injected `run_install_script`); `localize_install_failure`/`certify_reciped_only`/`assemble_install_debug_bundle` (T2); `render_build_script` (`python_deps.depgraph.build_script`).
- Produces: `run_v3(..., enable_binding_install: bool = False, reset_to_base=None, run_install_script=None)`; a `_binding_install_phase` behavior inside `_dep_emit_phase`.

- [ ] **Step 1: Write the failing test** — `tests/test_binding_install_wiring.py`:

```python
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate import orchestrator
from src.envstate.ledger import ActionLedger
from src.envstate.world_model import TaskReport, initial_map, merge_map
from src.sandbox import InstallResult
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


def _agent():
    class _A:
        client = None
        def run(self, *a, **k): return TaskReport("t", "blocked", (), "")
        def propose(self, *a, **k): return None
        def run_recipe(self, *a, **k): return TaskReport("r", "done", (), "")
    return _A()


def _maint():
    class _M:
        def update(self, wm, *a, **k): return wm
    return _M()


def _map():
    syslib = Node(id="syslib:libgl1", type=NodeType.SYSTEM_LIB, name="libgl1",
                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.SATISFIED,
                  check_command="dpkg -s libgl1", chosen_fix="apt:libgl1")
    base = initial_map(base_image="python:3.11", workdir="/repo", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, dep_graph=DepGraph().with_node(syslib))


def _ro(cmd):
    return (0, "ok") if "libgl1" in cmd else (1, "")


def _run(**kw):
    return orchestrator.run_v3(
        _agent(), _maint(), _map(), ActionLedger(), lambda c: (False, ""),
        max_cycles=1, exec_readonly=_ro, enable_dep_emit=True,
        enable_script_materialization=True, **kw,
    )


def test_flag_off_byte_identical():
    base_map, base_reason = _run()
    off_map, off_reason = _run(enable_binding_install=False,
                               reset_to_base=lambda: None,
                               run_install_script=lambda s: InstallResult(0, None, None, ""))
    assert base_reason == off_reason
    assert base_map.dep_graph == off_map.dep_graph


def test_flag_on_runs_install_then_certifies():
    calls = []
    def reset(): calls.append("reset")
    def install(script):
        calls.append("install")
        assert "#@node" in script  # render_build_script output was passed
        return InstallResult(0, None, None, "")
    _run(enable_binding_install=True, reset_to_base=reset, run_install_script=install)
    assert calls == ["reset", "install"]   # binding path used, block_emit not


def test_flag_on_install_failure_does_not_crash():
    def install(script):
        return InstallResult(1, "apt-get install -y libgl1", 5, "E: not found")
    # build_agent.client is None → no repair; the phase must complete without raising
    _run(enable_binding_install=True, reset_to_base=lambda: None, run_install_script=install)
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `pytest tests/test_binding_install_wiring.py::test_flag_on_runs_install_then_certifies -v`
Expected: `TypeError: run_v3() got an unexpected keyword argument 'enable_binding_install'`

- [ ] **Step 3: Implement** — two edits to `src/envstate/orchestrator.py`:

**Edit A** — add three kwargs after `gate_observer=None,` (line 336):

```python
    enable_binding_install: bool = False,      # Stage 2 — binding dep-spine install; byte-identical off
    reset_to_base=None,                        # Callable[[], None] | None  (Sandbox.reset_to_base)
    run_install_script=None,                   # Callable[[str], InstallResult] | None
```

**Edit B** — inside `_dep_emit_phase`, replace the `if enable_script_materialization:` block's body (lines 416-439) so the binding path takes over when its flag is on. The new structure:

```python
        if enable_script_materialization and enable_binding_install:
            # Stage 2: binding dep-spine install — render whole script, reset to base,
            # install, certify reciped nodes. Repair reuses run_structured_repair.
            from python_deps.depgraph.build_script import render_build_script
            from python_deps.depgraph.emit import _is_reciped
            from src.envstate.install_localizer import (
                localize_install_failure, certify_reciped_only, assemble_install_debug_bundle,
            )
            # Consumer fail-fast: a reciped node with no check_command cannot be certified.
            missing_check = [n.id for n in graph.nodes if _is_reciped(n) and not n.check_command]
            if missing_check:
                raise ValueError(
                    f"binding-install: reciped nodes lack a check_command: {missing_check}")
            script = render_build_script(graph, _manual_blocks)
            if reset_to_base is not None:
                reset_to_base()
            result = run_install_script(script) if run_install_script is not None else None
            graph, _unsat = certify_reciped_only(graph, exec_readonly, cycle)
            install_ok = result is not None and result.rc == 0
            _failed_node = None
            if not install_ok and result is not None:
                _failed_node = localize_install_failure(script, result.failing_command).node_id
            elif _unsat:
                _failed_node = _unsat[0]
            if _failed_node is not None and getattr(build_agent, "client", None) is not None:
                _out = run_structured_repair(
                    graph, _failed_node, None, cycle,
                    propose=lambda s, **k: build_agent.propose(s, exec_readonly, **k),
                    emit=lambda g, mb: _binding_emit(g, mb, cycle),
                    manual_blocks=_manual_blocks, known_invalid=_known_invalid,
                    max_repairs=MAX_REPAIRS_PER_BLOCK, repair_budget=_repair_turns,
                    cap_failed_id=True)
                graph = _out.graph
                _manual_blocks = _out.manual_blocks
                _known_invalid = set(_out.known_invalid)
                _repair_turns -= _out.turns_spent
                if _out.budget_exhausted or _repair_turns <= 0:
                    _budget_exhausted = True
        elif enable_script_materialization:
            # ... EXISTING block_emit body (lines 422-439) UNCHANGED ...
        else:
            # ... EXISTING emit_drain / B3 body UNCHANGED ...
```

Add a small nonlocal helper `_binding_emit` near `_dep_emit_phase` (or inline as a closure) that re-renders, resets, installs, and certifies — returning `(graph, None, failed_node_id)` to match the `emit` contract `run_structured_repair` expects:

```python
    def _binding_emit(graph, manual_blocks, cycle):
        from python_deps.depgraph.build_script import render_build_script
        from src.envstate.install_localizer import localize_install_failure, certify_reciped_only
        script = render_build_script(graph, manual_blocks)
        if reset_to_base is not None:
            reset_to_base()
        result = run_install_script(script) if run_install_script is not None else None
        graph, unsat = certify_reciped_only(graph, exec_readonly, cycle)
        if result is not None and result.rc != 0:
            return graph, None, localize_install_failure(script, result.failing_command).node_id
        return graph, None, (unsat[0] if unsat else None)
```

(The final `certify_refresh` at line 466 and the installed-fold block remain unchanged — they run after either path.)

- [ ] **Step 4: Run it — expect PASS**

Run: `pytest tests/test_binding_install_wiring.py -v`
Expected: 3 passed.
Then regression: `pytest tests/test_graph_scheduler_wiring.py tests/envstate/test_v3_task_branch.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_binding_install_wiring.py
git commit -m "feat(stage2): flag-gated binding-install branch in run_v3 (_dep_emit_phase)"
```

---

### Task 4: `cap_failed_id` param on `run_structured_repair`

**Files:**
- Modify: `src/envstate/repair_loop.py` (lines 23-71)
- Test: `tests/envstate/test_repair_loop_cap.py` (Docker-free)

**Interfaces:**
- Produces: `run_structured_repair(..., cap_failed_id: bool = False)`. When `True`, if `emit` returns a *different* failing id than the original, the loop stops and returns the ORIGINAL `failed_id` (no silent pivot). When `False` (default), behavior is unchanged.

- [ ] **Step 1: Write the failing test** — `tests/envstate/test_repair_loop_cap.py`:

```python
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate.repair_loop import run_structured_repair, RepairOutcome


class _Res:
    accepted = True
    errors = ()
    def __init__(self, graph): self.graph = graph; self.manual_blocks = ()


def _fixture(monkeypatch, emit_returns_id):
    import src.envstate.repair_loop as rl
    # admit always accepts; compose_replay_script returns one block matching failed id.
    class _Block:
        def __init__(self, bid): self.block_id = bid; self.target_node_ids = (bid,)
    monkeypatch.setattr(rl, "compose_replay_script", lambda g, mb: (_Block("nodeX"),))
    monkeypatch.setattr(rl, "admit_proposal", lambda g, p, **k: _Res(g))
    class _Scope:
        failed_command = "cmd"; known_evidence_ids = frozenset()
    monkeypatch.setattr(rl, "build_repair_scope", lambda *a, **k: _Scope())
    return emit_returns_id


def test_cap_true_stops_on_pivot_returns_original(monkeypatch):
    _fixture(monkeypatch, "nodeY")
    out = run_structured_repair(
        object(), "nodeX", None, 1,
        propose=lambda s, **k: {"p": 1},
        emit=lambda g, mb: (g, None, "nodeY"),   # pivots to a different node
        cap_failed_id=True, max_repairs=3)
    assert out.still_failing_id == "nodeX"       # capped to original, not nodeY


def test_cap_false_allows_pivot(monkeypatch):
    _fixture(monkeypatch, "nodeY")
    out = run_structured_repair(
        object(), "nodeX", None, 1,
        propose=lambda s, **k: {"p": 1},
        emit=lambda g, mb: (g, None, "nodeY"),
        cap_failed_id=False, max_repairs=1)
    # default behavior: it chases nodeY (loops/continues), not the original
    assert out.still_failing_id in ("nodeY", None)


def test_cap_true_success_when_emit_clears(monkeypatch):
    _fixture(monkeypatch, None)
    out = run_structured_repair(
        object(), "nodeX", None, 1,
        propose=lambda s, **k: {"p": 1},
        emit=lambda g, mb: (g, None, None),      # fixed
        cap_failed_id=True, max_repairs=3)
    assert out.still_failing_id is None
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `pytest tests/envstate/test_repair_loop_cap.py::test_cap_true_stops_on_pivot_returns_original -v`
Expected: `TypeError: run_structured_repair() got an unexpected keyword argument 'cap_failed_id'`

- [ ] **Step 3: Implement** — edit `src/envstate/repair_loop.py`:

Add `cap_failed_id: bool = False,` to the signature (after `scope_builder=build_repair_scope,`). Then change the post-emit handling (lines 67-70) from:

```python
        graph, bundle, failed_id = emit(graph, mb)
        if failed_id is None:
            return RepairOutcome(graph, None, mb, frozenset(ki), turns, False)
```

to:

```python
        graph, bundle, new_failed_id = emit(graph, mb)
        if new_failed_id is None:
            return RepairOutcome(graph, None, mb, frozenset(ki), turns, False)
        if cap_failed_id and new_failed_id != failed_id:
            # Stage 2: the original node was fixed but a different node now fails;
            # return the original id and let the outer loop handle the new failure
            # on a fresh from-base re-verify (no silent in-loop pivot).
            return RepairOutcome(graph, failed_id, mb, frozenset(ki), turns, False)
        failed_id = new_failed_id
```

- [ ] **Step 4: Run it — expect PASS**

Run: `pytest tests/envstate/test_repair_loop_cap.py -v`
Expected: 3 passed.
Regression: `pytest tests/envstate/test_repair_loop.py -q` → all pass (existing call sites default `cap_failed_id=False`).

- [ ] **Step 5: Commit**

```bash
git add src/envstate/repair_loop.py tests/envstate/test_repair_loop_cap.py
git commit -m "feat(stage2): run_structured_repair cap_failed_id param (no silent pivot)"
```

---

### Task 5: Thread the new callables/flag through the drivers

**Files:**
- Modify: `src/envstate/_loop_common.py` (the `_run_v3_loop` wrapper — read it first; it forwards to `run_v3`)
- Modify: `agent.py` (the `_run_v3_loop` call ~1360-1375; add an `enable_binding_install` flag to `BuildAgent.__init__`/config where the other `enable_*` flags live)
- Modify: `scripts/l2_repair_loop_smoke.py` (the `run_v3` call ~137-147; add a `--enable-binding-install` CLI flag)
- Test: `tests/test_binding_install_driver_wiring.py` (Docker-free — assert `_run_v3_loop` forwards the kwargs)

**Interfaces:**
- Consumes: `run_v3(..., enable_binding_install, reset_to_base, run_install_script)` (T3); `Sandbox.reset_to_base`/`run_install_script` (T1).

- [ ] **Step 1: Read `src/envstate/_loop_common.py`** to confirm `_run_v3_loop`'s signature and how it forwards `**kwargs`/explicit params to `run_v3`. The new params must be added to its signature and forwarded.

- [ ] **Step 2: Write the failing test** — `tests/test_binding_install_driver_wiring.py`:

```python
import sys, inspect
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate._loop_common import _run_v3_loop


def test_loop_common_accepts_binding_install_kwargs():
    params = inspect.signature(_run_v3_loop).parameters
    assert "enable_binding_install" in params
    assert "reset_to_base" in params
    assert "run_install_script" in params
```

- [ ] **Step 3: Run it — expect FAIL**

Run: `pytest tests/test_binding_install_driver_wiring.py -v`
Expected: FAIL (params absent).

- [ ] **Step 4: Implement**

In `src/envstate/_loop_common.py`, add `enable_binding_install: bool = False`, `reset_to_base=None`, `run_install_script=None` to `_run_v3_loop`'s signature and forward them in its `run_v3(...)` call.

In `agent.py`, add to the `_run_v3_loop(...)` call (after `enable_script_materialization=...`):

```python
                    enable_binding_install=getattr(self, "enable_binding_install", False),
                    reset_to_base=self.sandbox.reset_to_base,
                    run_install_script=self.sandbox.run_install_script,
```

and initialize `self.enable_binding_install = False` alongside the other `enable_*` flags in `BuildAgent.__init__` (so it defaults off and is configurable).

In `scripts/l2_repair_loop_smoke.py`, add a `--enable-binding-install` argparse flag (default False) and pass to the `run_v3(...)` call:

```python
            enable_binding_install=args.enable_binding_install,
            reset_to_base=sandbox.reset_to_base,
            run_install_script=sandbox.run_install_script,
```

- [ ] **Step 5: Run it — expect PASS**

Run: `pytest tests/test_binding_install_driver_wiring.py -v`
Expected: 1 passed.
Regression: `pytest tests/test_orchestrator_v1.py -q` → all pass (defaults off).

- [ ] **Step 6: Commit**

```bash
git add src/envstate/_loop_common.py agent.py scripts/l2_repair_loop_smoke.py tests/test_binding_install_driver_wiring.py
git commit -m "feat(stage2): thread enable_binding_install + sandbox callables through drivers"
```

---

### Task 6: Check-quality hardening — deterministic SystemLib check rewrite + PatchGate anti-weakening guard

**Files:**
- Create: `src/envstate/check_quality.py` (pure)
- Modify: `src/python_deps/depgraph/patch_gate.py` (admit path — reject structurally-incapable checks)
- Test: `tests/envstate/test_check_quality.py` + `tests/depgraph/test_patch_gate_check_guard.py` (Docker-free)

**Interfaces:**
- Produces: `rewrite_syslib_check(node) -> str | None` (returns a capability check, e.g. `ldconfig -p | grep -q <soname>` / `command -v`, for a SystemLib whose check is a brittle `dpkg -s <name>`; else None); `check_can_detect_absence(check_command: str) -> bool` (False for structurally-trivial checks).
- Consumes: `NodeType` (`schema`).

- [ ] **Step 1: Write the failing test** — `tests/envstate/test_check_quality.py`:

```python
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.schema import DiscoveredBy, Layer, Node, NodeType, State
from src.envstate.check_quality import rewrite_syslib_check, check_can_detect_absence


def _syslib(check):
    return Node(id="syslib:libglib2.0-0", type=NodeType.SYSTEM_LIB, name="libglib2.0-0",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING,
                check_command=check, chosen_fix="apt:libglib2.0-0")


def test_rewrite_dpkg_s_to_capability_check():
    out = rewrite_syslib_check(_syslib("dpkg -s libglib2.0-0"))
    assert out is not None and "dpkg -s" not in out
    assert "ldconfig" in out or "command -v" in out


def test_rewrite_returns_none_for_already_capability_check():
    assert rewrite_syslib_check(_syslib("ldconfig -p | grep -q libglib")) is None


def test_can_detect_absence_true_for_real_check():
    assert check_can_detect_absence("dpkg -s libgl1") is True
    assert check_can_detect_absence("python -c 'import cv2'") is True


def test_can_detect_absence_false_for_trivial():
    assert check_can_detect_absence("true") is False
    assert check_can_detect_absence("echo ok") is False
    assert check_can_detect_absence("ls /") is False
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `pytest tests/envstate/test_check_quality.py -v`
Expected: `ModuleNotFoundError: No module named 'src.envstate.check_quality'`

- [ ] **Step 3: Implement** — create `src/envstate/check_quality.py`:

```python
"""Deterministic check-quality fixes (Stage 2): rewrite brittle SystemLib checks and
reject checks structurally incapable of detecting absence. Pure; no Docker/LLM."""
from __future__ import annotations

import re

from python_deps.depgraph.schema import NodeType

# Commands that pass on essentially any container → cannot detect a missing dependency.
_TRIVIAL_HEADS = {"true", ":", "echo", "ls", "pwd", "cd", "printf", "test"}


def rewrite_syslib_check(node) -> str | None:
    """For a SystemLib whose check is a brittle exact `dpkg -s <name>`, return a
    capability check that survives Debian renames (t64); else None."""
    if node.type is not NodeType.SYSTEM_LIB or not node.check_command:
        return None
    m = re.match(r"^\s*dpkg\s+-s\s+(\S+)\s*$", node.check_command)
    if not m:
        return None
    name = m.group(1)
    soname = name.split(":")[0]
    return f"ldconfig -p | grep -q {soname} || command -v {soname}"


def check_can_detect_absence(check_command: str) -> bool:
    """False when the check is structurally trivial (would pass without the install)."""
    cmd = (check_command or "").strip()
    if not cmd:
        return False
    head = cmd.split()[0]
    if head in _TRIVIAL_HEADS:
        # `test`/`[` with a real predicate is fine; bare echo/true/ls are not.
        return False
    return True
```

- [ ] **Step 4: Run it — expect PASS**

Run: `pytest tests/envstate/test_check_quality.py -v`
Expected: 4 passed.

- [ ] **Step 5: Write the PatchGate guard test** — `tests/depgraph/test_patch_gate_check_guard.py`:

```python
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.patch import NodeSpec, PatchProposal
from python_deps.depgraph.patch_gate import validate_proposal
from python_deps.depgraph.schema import DepGraph


def test_proposal_with_trivial_check_is_rejected():
    proposal = PatchProposal(add_requirements=(NodeSpec(
        id="syslib:libgl1", type="SystemLib", name="libgl1", layer="system",
        check_command="true", evidence_ref="ev-1"),))
    errs = validate_proposal(DepGraph(), proposal, known_evidence_ids=frozenset({"ev-1"}))
    assert any("check" in e.lower() and "libgl1" in e for e in errs)
```

- [ ] **Step 6: Implement the guard** — in `src/python_deps/depgraph/patch_gate.py`, in `validate_proposal`, for each `NodeSpec` with a `check_command`, append an error when `not check_can_detect_absence(check_command)`. Import lazily to keep `python_deps` envstate-free:

```python
        if spec.check_command:
            from src.envstate.check_quality import check_can_detect_absence
            if not check_can_detect_absence(spec.check_command):
                errors.append(
                    f"{spec.id}: proposed check_command cannot detect absence "
                    f"(structurally trivial): {spec.check_command!r}")
```

> NOTE: this introduces a `python_deps → src.envstate` import. If the project forbids that direction, instead place `check_can_detect_absence` in `python_deps/depgraph/` and import it there. Confirm the allowed import direction before implementing; prefer moving the helper if `python_deps` must stay envstate-free.

- [ ] **Step 7: Run — expect PASS**

Run: `pytest tests/envstate/test_check_quality.py tests/depgraph/test_patch_gate_check_guard.py -v`
Expected: 5 passed.
Regression: `pytest tests/depgraph/test_patch_gate.py -q` → all pass.

- [ ] **Step 8: Commit**

```bash
git add src/envstate/check_quality.py src/python_deps/depgraph/patch_gate.py tests/envstate/test_check_quality.py tests/depgraph/test_patch_gate_check_guard.py
git commit -m "feat(stage2): check-quality hardening — syslib check rewrite + PatchGate anti-weakening guard"
```

---

## Self-Review (run after writing; fix inline)

- **Spec coverage:** §1.5 binding conditions → T3 (fail-fast) + T2 (certify_reciped_only) + T6 (check quality); §2 reset-to-base → T1+T3; §4 localization → T2; §5 debug bundle → T2; §6 seam → T1+T3+T5 (incl. `InstallResult`, `certify_refresh` cycle arg, `execute()` bypass); §7 components + task order → all tasks; §8 byte-identical → T3 test; §9 cache keying → T1 (`enable_cache_volume`, index-level). Deferred (project install, `#@need`/`#@block` certify, tier-commit R2) correctly absent.
- **Placeholder scan:** no TBD/"similar to"/"add error handling"; every code step has concrete code.
- **Type consistency:** `InstallResult(rc, failing_command, lineno, stderr)` identical across T1/T3; `run_install_script: (str)->InstallResult` and `reset_to_base: ()->None` identical across T1/T3/T5; `certify_reciped_only(graph, exec_readonly, cycle)->(graph, unsat_ids)` consistent T2/T3; `cap_failed_id` default False everywhere except T3's binding call (True).

## Open items for the implementer / final review
- **T6 import direction:** confirm whether `python_deps` may import `src.envstate` (the guard). If not, relocate `check_can_detect_absence` into `python_deps/depgraph/`. Flagged in T6 Step 6.
- **T5 `_run_v3_loop`:** the wrapper in `_loop_common.py` must forward the new kwargs (read it first — T5 Step 1).
- **Docker integration:** T1's `reset_to_base`/`run_install_script` and the end-to-end binding loop are exercised only by `scripts/l2_repair_loop_smoke.py --enable-binding-install` (real container), not the default suite. Run it manually once after T5 to validate the real path (and watch for the cv2/t64 mode-B case).
