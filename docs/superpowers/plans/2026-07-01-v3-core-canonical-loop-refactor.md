# V3-Core Canonical Loop Refactor — Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **v2 supersedes the v1 strategy draft.** v1 was a directionally-correct strategy doc; three independent reviews (code-grounding / architecture / executability) found it accurate on facts but deferring the real decisions. v2 resolves every deferred decision to a concrete signature, renumbers phases to execution order, and folds in the e2e proof layer.

**Goal:** Make `run_v3` read as one canonical paper loop — `build graph → render/emit from graph → diagnose failure → typed LLM patch → PatchGate → graph/manual-block update → host certify → gate → terminal fresh replay` — with every legacy branch removed from the method and proven-absent by an e2e trace.

**Architecture:** The canonical loop has exactly **one in-loop execution strategy** (incremental block-emit onto the live container) and **one terminal proof** (a mandatory fresh-from-base replay of the rendered `setup.sh`, run once at convergence, that flips the installability gate from provisional to binding). The legacy `emit_drain` + `repair_failed_nodes` branch and the free-text `build_agent.run` fallback are removed from `run_v3` (kept only as named `run_v1`/baseline code). A pure diagnosis router classifies each failure before repair so the graph never learns a fake environment requirement (e.g. a repo-local import). A `RunTracer` records which path actually executed so tests can *prove* the legacy paths are dead.

**Tech Stack:** Python 3, stdlib `dataclasses`/`enum`/`re`, pytest. No new dependencies.

---

## Canonical Model — the one decision that shapes every phase

The v1 draft left this implicit; v2 commits to it. A paper reader opening `run_v3` must see **one** loop.

```text
IN-LOOP (repeated each cycle) — single execution strategy:
    certify graph (host, sole SATISFIED writer)
    emit: block_emit(...) incrementally on the LIVE container
    on failure: diagnose -> route -> typed PatchProposal -> PatchGate -> graph/manual-block update
    scheduler next_decision: task | done | giveup

TERMINAL (once, when the scheduler says done) — single proof:
    render_build_script(graph, manual_blocks)      # full setup.sh
    reset_to_base()                                # fresh base container
    run_install_script(setup.sh)                   # from scratch
    host-certify every node against the fresh container
    evaluate installability (now BINDING) + testability gates
    all pass  -> return done
    any fail  -> the fresh-replay failure is authoritative -> return non-done + record failing command
```

**What this replaces:** today `enable_binding_install=True` runs a fresh replay *every cycle* as an alternate loop mode (`orchestrator.py:468`), competing with the incremental block-emit mode (`:516`) and the legacy `emit_drain` mode (`:540`). v2 demotes fresh-replay from "alternate loop mode" to "terminal proof", so the in-loop path is singular. This is **lower blast-radius** than the v1 draft's "flip `enable_binding_install` default" (which would make every cycle pay a from-base rebuild) and it gives a crisp "converge incrementally, then prove from scratch" narrative.

> **Reversible alternative (if you reject the above):** keep the two in-loop emit strategies but name them explicitly (`# canonical: incremental block-emit` / `# ablation: per-cycle fresh replay`) and still make a single terminal replay mandatory. This preserves the fork but labels it. v2 assumes the collapse (Phase 7); if the collapse is rejected, Phase 7 degrades to "label + mandatory terminal replay" and Phase 6's single repair-entry still applies.

---

## Global Constraints

Every task implicitly includes these (copied verbatim from the interpretability directive + the pure-module discipline):

- **One canonical path, no hidden flag branches.** Legacy behavior lives in named `run_v1`/baseline/ablation code, never as a boolean branch inside `run_v3`.
- **Host is the sole `SATISFIED` writer** (`certify.py:81`). No phase writes node state from an LLM path. PatchGate `apply_proposal` NEVER writes `SATISFIED`.
- **Immutability:** new dataclasses are `@dataclass(frozen=True)`; functions return new objects. The only mutable collectors are append-only host-owned recorders (`ActionLedger`, the new `RunTracer`) — same exception the ledger already is.
- **Pure `depgraph` modules import no `src.envstate`.** `diagnose.py` follows its siblings `runtime_classify.py`/`runtime_ingest.py`.
- **Never raise into the loop from ingest/diagnosis.** Classifiers return `None` on non-match; `ingest_runtime_failures` already wraps each obs in try/except.
- **Type annotations on every signature; `from __future__ import annotations` at top of every file.**
- **TDD:** failing test first, minimal impl, passing test, commit. Frequent commits.
- **Keep the typed `PatchProposal` inner loop.** Do NOT swap to raw-shell edits (settled decision: "keep typed, add the doc's guards").

---

## Phase ↔ Execution-Order map

Phases below are numbered **in execution order** (v1's mismatch between headings and "Recommended Order" is fixed). Each phase is an independently reviewable, independently testable deliverable.

| Order | Phase | Deliverable | Depends on |
|------|-------|-------------|-----------|
| 1 | Tighten PatchGate + forbid unscheduled blocks | bad blocks can't enter the artifact | — |
| 2 | Persist `manual_blocks` in final artifact | exported `setup.sh` includes governed blocks | 1 |
| 3 | Diagnosis router (companion Phase 1) | repo-local imports never ingested as packages | — |
| 4 | Remove legacy `emit_drain`/`repair_failed_nodes` from `run_v3` | one less in-loop branch | — |
| 5 | Single task-dispatch branch (gate-evidence / typed-repair / give-up) | no free-text mutation in `run_v3` | 3, 4 |
| 6 | Unify repair entry + wire diagnosis routing through all sites | every repair is mode-routed identically | 3, 5 |
| 7 | Collapse fork: block-emit in-loop + mandatory terminal fresh replay | one in-loop path; binding installability gate | 2, 4, 6 |
| 8 | Instrumentation + e2e proof harness | trace proves canonical path, no legacy | 1–7 |
| 9 | Quarantine legacy into named baseline/ablation modules | codebase reads singular | 4, 5, 7, green benchmarks |

---

## Phase 1: Tighten PatchGate for script patches, then forbid unscheduled rendering

**Why first (verified):** `Block` objects reach `render_build_script`'s `manual_blocks` only via `admit_proposal` (PatchGate-validated) or `compile_blocks` (graph-derived, always a legal `Layer`). Tightening the gate first closes the bad-input path *before* Phase 2/7 make those blocks part of the persisted artifact, so no illegal wave can reach the renderer.

**Files:**
- Modify: `src/python_deps/depgraph/patch_gate.py` (`validate_proposal`, `patch_gate.py:103-116`)
- Modify: `src/python_deps/depgraph/build_script.py` (`render_build_script`, `build_script.py:200-208`)
- Test: `tests/depgraph/test_patch_gate_validate.py`, `tests/depgraph/test_build_script.py`

**Interfaces:**
- Consumes: `ScriptPatch{block_id, wave, commands, target_node_ids, checks, provides, evidence_ref}` (`patch.py:38-47`); `Layer` enum (`schema.py`).
- Produces: no new symbols — only stricter validation in `validate_proposal` and a fail-fast in `render_build_script`.

**Rules to add to `validate_proposal`'s `for s in proposal.script_patches:` block** (currently checks evidence + non-empty `target_node_ids` + known targets + read-only/absence checks; add):
1. `if not s.commands:` → `errs.append(f"script block {s.block_id} has empty commands")`
2. `if any(not c.strip() for c in s.commands):` → `errs.append(f"script block {s.block_id} has a blank/whitespace-only command")`
3. `try: Layer(s.wave) except ValueError: errs.append(f"script block {s.block_id} has illegal wave {s.wave!r} (must be a Layer value)")`
4. `provides` semantics: `ScriptPatch.provides` feeds `Block.provider_ids` (via `_script_patch_to_block`, `patch_gate.py:153-159`). Apply the **same rule** `ProviderSpec.provides` already uses (`patch_gate.py:99-101`): every id in `s.provides` must be in `known_after` (`existing_ids | proposed_node_ids`). Add: `for nid in s.provides: if nid not in known_after: errs.append(f"script block {s.block_id} provides unknown node {nid!r}")`.

- [ ] **Step 1: Write failing tests** (`tests/depgraph/test_patch_gate_validate.py`)

```python
from python_deps.depgraph.patch import PatchProposal, ScriptPatch
from python_deps.depgraph.patch_gate import validate_proposal
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State

def _graph_with(nid: str) -> DepGraph:
    return DepGraph().with_node(Node(id=nid, type=NodeType.SYSTEM_LIB, name="x",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING))

def _sp(**kw) -> ScriptPatch:
    base = dict(block_id="blk:1", wave="system", commands=("apt-get install -y libx",),
                target_node_ids=("syslib:x",), checks=("dpkg -s libx",), evidence_ref="ev:1")
    base.update(kw); return ScriptPatch(**base)

_EV = frozenset({"ev:1"})

def test_rejects_empty_commands():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(commands=()),)), known_evidence_ids=_EV)
    assert any("empty commands" in e for e in errs)

def test_rejects_blank_command():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(commands=("apt-get install -y libx", "   "),)),),
        known_evidence_ids=_EV)
    assert any("blank" in e for e in errs)

def test_rejects_illegal_wave():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(wave="post-install"),)), known_evidence_ids=_EV)
    assert any("illegal wave" in e for e in errs)

def test_rejects_provides_unknown_node():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(provides=("syslib:ghost",)),)), known_evidence_ids=_EV)
    assert any("provides unknown node" in e for e in errs)

def test_accepts_legal_script_patch():
    errs = validate_proposal(_graph_with("syslib:x"),
        PatchProposal(script_patches=(_sp(),)), known_evidence_ids=_EV)
    assert errs == []
```

- [ ] **Step 2: Run — expect FAIL** (accept case passes, reject cases fail because rules absent)

`cd /Users/john/john-planner-v3-core && python -m pytest tests/depgraph/test_patch_gate_validate.py -q`

- [ ] **Step 3: Add the four rules** to `validate_proposal`'s `for s in proposal.script_patches:` loop (see Rules above). Add `from python_deps.depgraph.schema import Layer` is already imported (`patch_gate.py:18`).

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Replace the unscheduled-block renderer test with a guard test.** In `build_script.py`, change `render_build_script` so an unknown wave fails fast instead of landing in the catch-all. Replace lines `200-207` (the `# Catch-all` block) with:

```python
    # Fail-fast: PatchGate (Phase 1) rejects illegal waves, so any manual block whose
    # wave is not a Layer value is a programming error, not user input — never silently
    # render it into an UNSCHEDULED section.
    known_waves = {layer.value for layer in _LAYER_ORDER}
    illegal = [b.block_id for b in manual_blocks if b.wave not in known_waves]
    if illegal:
        raise ValueError(f"render_build_script: manual blocks have illegal waves "
                         f"(not a Layer value): {illegal}")
```

In `tests/depgraph/test_build_script.py`, replace `test_block_with_unknown_wave_lands_in_catch_all` (`:185`) with:

```python
def test_block_with_unknown_wave_raises():
    import pytest
    from python_deps.depgraph.block import Block
    blk = Block(block_id="blk:x", wave="post-install", commands=("echo hi",),
                target_node_ids=(), provider_ids=(), check_commands=(), evidence_refs=())
    with pytest.raises(ValueError, match="illegal waves"):
        render_build_script(DepGraph(), (blk,))
```

- [ ] **Step 6: Run both suites — expect PASS.** `python -m pytest tests/depgraph/test_patch_gate_validate.py tests/depgraph/test_build_script.py -q`

- [ ] **Step 7: Commit.** `git commit -m "feat(depgraph): PatchGate rejects empty/blank/illegal-wave/unknown-provides script patches; renderer fails fast on illegal waves"`

---

## Phase 2: Persist `manual_blocks` in the final artifact

**Decision (was 3 options in v1):** carry `manual_blocks` on `WorldModelMap` as a new **unserialized in-process field**, mirroring the existing `dep_graph` field precedent (`world_model.py:100`, "Not serialized … consumed in-process only"). This threads through `merge_map` and every `run_v3` return with zero call-site signature churn, and it's the object `render_build_script` already accepts.

**Files:**
- Modify: `src/envstate/world_model.py` (`WorldModelMap`, add field after `dep_graph`, `world_model.py:100`)
- Modify: `src/envstate/orchestrator.py` (`_dep_emit_phase` merge + task-branch merge — write `_manual_blocks` into the map)
- Modify: `scripts/run_v3_e2e.py:144` (pass manual blocks to the final render)
- Test: `tests/envstate/test_manual_blocks_persist.py`

**Interfaces:**
- Produces: `WorldModelMap.manual_blocks: tuple[Block, ...] = ()`. `merge_map(map, ..., manual_blocks=...)` accepts it (extend the existing keyword pass-through). Final render call becomes `render_build_script(dep_graph, getattr(final_map, "manual_blocks", ()))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/envstate/test_manual_blocks_persist.py
from src.envstate.world_model import WorldModelMap, initial_map, merge_map
from python_deps.depgraph.block import Block

def _blk() -> Block:
    return Block(block_id="blk:1", wave="system", commands=("apt-get install -y libx",),
                 target_node_ids=("syslib:x",), provider_ids=(), check_commands=(), evidence_refs=())

def test_world_map_defaults_manual_blocks_empty():
    m = initial_map(base_image="python:3.12", workdir="/app", language="unknown",
                    build_system="pip", repo_layout=())
    assert m.manual_blocks == ()

def test_merge_map_carries_manual_blocks():
    m = initial_map(base_image="python:3.12", workdir="/app", language="unknown",
                    build_system="pip", repo_layout=())
    m2 = merge_map(m, manual_blocks=(_blk(),))
    assert len(m2.manual_blocks) == 1 and m2.manual_blocks[0].block_id == "blk:1"
    # immutability: original unchanged
    assert m.manual_blocks == ()
```

- [ ] **Step 2: Run — expect FAIL** (`TypeError: __init__() got an unexpected keyword argument 'manual_blocks'` or `AttributeError`).

- [ ] **Step 3: Add the field** to `WorldModelMap` (after `world_model.py:100`), mirroring the `dep_graph` comment:

```python
    # Governed manual blocks (LLM-admitted ScriptPatches) carried in-process so the
    # FINAL rendered setup.sh includes them. Not serialized (map_to_dict/from_dict) —
    # consumed in-process only, same as dep_graph above.
    manual_blocks: tuple = ()
```

Ensure `merge_map` forwards `manual_blocks` (it forwards known fields via `dataclasses.replace`; add `manual_blocks` to its accepted-kwargs pass-through if it enumerates fields explicitly — grep `def merge_map` and add the keyword like `dep_graph`).

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Write `_manual_blocks` into the map in `orchestrator.py`.** At the end of `_dep_emit_phase` where it builds the final `merge_map(...)` (`orchestrator.py:578`), add `manual_blocks=_manual_blocks`. In the task branch's `merge_map(current_map, dep_graph=_g)` (`orchestrator.py:790`), add `manual_blocks=_manual_blocks`.

- [ ] **Step 6: Fix the driver.** In `scripts/run_v3_e2e.py:144` change `fh.write(render_build_script(dep_graph))` to:

```python
            fh.write(render_build_script(dep_graph, getattr(final_map, "manual_blocks", ())))
```

- [ ] **Step 7: Add an integration assertion** — run a loop path that admits a manual block (reuse a fixture from `tests/envstate/test_v3_repair_wiring.py`) and assert the block id appears in `render_build_script(final_map.dep_graph, final_map.manual_blocks)`. (Concrete test code lands with the fixture in that file.)

- [ ] **Step 8: Run + commit.** `git commit -m "feat(envstate): persist manual_blocks on WorldModelMap so exported setup.sh includes governed blocks"`

---

## Phase 3: Diagnosis router (companion Phase 1) + reconciliations

**Execute the companion plan** `docs/superpowers/plans/2026-07-01-diagnosis-router-and-guards-phase1.md` (Tasks 1–3: `diagnose.py` with `Mode`/`RepoContext`/`Diagnosis`/`is_local_import`/`diagnose()`/`make_diagnostic_classifier`, wired via `ingest_runtime_failures(classifiers=...)`). Do not duplicate it here (DRY). Apply these **reconciliations** while executing (they amend the companion plan; the v1 draft listed them but got two wrong):

1. **DROP companion revision "add local-import guard" as already-delivered** — it *is* the companion plan (Tasks 1 & 3). Not a separate change.
2. **Normalize invalid names (PEP 503).** `RepoContext.invalid_names` and the `disc.name in ctx.invalid_names` check (companion Task 2, `diagnose()` line ~313) must compare **normalized** names so `Frobnicate_9000` and `frobnicate-9000` match. Add a module-level helper in `diagnose.py` and apply it on both sides:

```python
def _norm(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")
```

Store `invalid_names` pre-normalized; compare `_norm(disc.name) in ctx.invalid_names`.
3. **Do NOT merge invalid names into `known_invalid`.** The companion plan's Phase-2 outline said "add name to the existing `known_invalid` frozenset". That set is a **heterogeneous key space** of raw failed commands + node/block ids (`repair_loop.py` populates it from `scope.failed_command or failed_id`). Mixing normalized package names in would corrupt equality lookups. Instead carry disproven package names in a **separate** `invalid_names` set (fed into `RepoContext`). This supersedes that companion line — strike it in the companion doc when you touch Phase 2.
4. **Conservative `import_name_error` (real latent bug).** Today `runtime_classify.py:89-100` handles `import_name_error` byte-identically to `module_not_found`. In `diagnose()`, only route `import_name_error` to `ENVIRONMENT` when `classify_observation` returns a `Discovery` whose `import_name` (in `disc.data`) equals the failed import AND the name is not repo-local; otherwise return `AMBIGUOUS` (probe before repair). Add a test: `import_name_error` for a name that maps to no package → `Mode.AMBIGUOUS`, not a bogus package node.
5. **Expose mode metadata for Phase 6.** Keep `make_diagnostic_classifier` returning `Discovery | None` (the ingest seam is fixed), but ALSO add a sibling `diagnose_all(observations, ctx) -> tuple[Diagnosis, ...]` pure helper so Phase 6's orchestrator routing can read `mode`/`reason` without re-running classification. Signature:

```python
def diagnose_all(observations: tuple[tuple[str, str], ...], ctx: RepoContext) -> tuple[Diagnosis, ...]:
    return tuple(diagnose(cmd, out, ctx) for cmd, out in observations)
```

- [ ] Execute companion Tasks 1–3 with reconciliations 2–5 applied. Commit per the companion plan's commit steps, plus one commit for `diagnose_all` + `_norm` + the conservative-`import_name_error` test.

---

## Phase 4: Remove legacy `emit_drain`/`repair_failed_nodes` from `run_v3`

Make block-emit the only non-terminal emit path. (Terminal fresh-replay arrives in Phase 7.)

**Files:**
- Modify: `src/envstate/orchestrator.py` (`_dep_emit_phase`, delete the `else:` legacy branch `:540-559`; keep the `elif enable_script_materialization:` block-emit branch as the sole in-loop path — see Phase 7 for removing the `if ... enable_binding_install:` branch)
- Modify: tests listed below
- Test: existing suites updated

**Steps:**

- [ ] **Step 1:** In `_dep_emit_phase`, the current structure is `if binding: ... elif materialization: block_emit ... else: emit_drain + repair_failed_nodes`. Delete the `else:` branch (`orchestrator.py:540-559`) and the now-unused `from src.envstate.depgraph_live import ... emit_drain` / `repair_failed_nodes` imports inside this function. Because `enable_script_materialization` defaults `True` and only tests pass `False` (verified: no production caller passes `False`), the block-emit branch becomes the sole in-loop path once Phase 7 removes the binding branch. **Interim:** make `enable_script_materialization=False` raise (consistent with the existing `ValueError` precedent at `orchestrator.py:385-387`) rather than silently no-op:

```python
    if not enable_script_materialization:
        raise ValueError("enable_script_materialization=False is no longer supported in run_v3 "
                         "(legacy emit_drain path removed); use run_v1 for the deterministic baseline")
```

(Phase 9 removes the parameter entirely once all callers stop passing it.)

- [ ] **Step 2:** Remove `_repaired_ids` from `run_v3` (`orchestrator.py:397`) — it was only read by `repair_failed_nodes`.

- [ ] **Step 3: Update tests** (grounding-verified exact targets):
  - `tests/test_v3_block_emit_wiring.py` — remove/replace `test_toggle_off_uses_emit_drain_and_repair` (`:88`) with `test_toggle_off_now_raises` asserting `pytest.raises(ValueError)` when `enable_script_materialization=False`.
  - `tests/test_graph_scheduler_wiring.py` — `test_drain_runs_under_flag_as_prefix` (`:170-215`) is **one** function containing BOTH a v3 assertion (`:189-200`, `enable_script_materialization=False` runs `emit_drain`) and a v1 assertion (`:202-215`). **Split it:** delete the v3 half; keep the v1 half as `test_v1_drain_runs_as_prefix`. (v1 correction: this is a split, not a delete-one/keep-other.)
  - `tests/test_run_v1_turn_budget.py` — the source-level assertion searching for `"graph, _reports, steps = emit_drain"` inside `run_v3` will break; update it to assert `emit_drain` is **absent** from `run_v3`'s source (invert the check).
  - `emit_drain()` / `repair_failed_nodes()` themselves stay in `depgraph_live.py` for `run_v1` + their direct tests — untouched.

- [ ] **Step 4: Run** `python -m pytest tests/test_v3_block_emit_wiring.py tests/test_graph_scheduler_wiring.py tests/test_run_v1_turn_budget.py -q` — expect PASS.

- [ ] **Step 5: Commit.** `git commit -m "refactor(orchestrator): remove legacy emit_drain/repair_failed_nodes branch from run_v3 (block-emit is the sole in-loop path)"`

---

## Phase 5: Single task-dispatch branch — gate evidence / typed repair / explicit give-up

Replace the free-text `build_agent.run` fallback (`orchestrator.py:792-796`) with one legible branch. Split into three independently-testable tasks.

**Files:** `src/envstate/orchestrator.py` (task branch `:756-801`), `tests/envstate/test_v3_task_branch.py`.

### Task 5a: One consolidated dispatch branch (no free-text mutation)

The current condition is `if (_targets and enable_script_materialization and exec_readonly is not None and getattr(build_agent, "client", None) is not None): <typed repair> else: build_agent.run(...)`. Post-Phase-4, `enable_script_materialization` is always effectively true. Replace with an explicit 3-way:

```python
    task = decision.task
    _targets = getattr(task, "target_node_ids", ()) or ()
    if _targets:
        if exec_readonly is None or getattr(build_agent, "client", None) is None:
            # Canonical v3 cannot do typed repair without a read-only executor + client.
            # Do NOT silently downgrade to free-text mutation — give up honestly.
            return _finish(TerminationReason.GIVEUP_CONFIG)
        # <existing typed-repair block, orchestrator.py:760-791, unchanged>
        report = TaskReport(task.goal, "done", (), "structured-repair task")
    else:
        # Discover task (empty target_node_ids): run the deterministic gate, record
        # evidence; next cycle's _runtime_ingest_phase turns it into obligations.
        report = _run_discover_gate(task, cycle)   # Task 5b
```

Add `GIVEUP_CONFIG` to `TerminationReason` (maps to `planner_giveup`). Remove the `build_agent.run(...)` call and its `_repair_turns -= 1` from `run_v3` (it stays available for `run_v1`).

- [ ] Write failing tests replacing `test_b3_ablation_does_not_use_propose` (`:244`) and `test_obligation_task_without_exec_readonly_falls_to_freetext` (`:257`):

```python
def test_obligation_task_without_exec_readonly_gives_up():
    # exec_readonly=None must NOT downgrade to build_agent.run; it gives up cleanly.
    # (build a run_v3 harness with exec_readonly=None and a target-bearing task)
    final_map, stop = _run_v3_once(exec_readonly=None, task_targets=("pkg:requests",))
    assert stop == "planner_giveup"

def test_no_free_text_build_agent_run_in_run_v3_source():
    import inspect, src.envstate.orchestrator as o
    src = inspect.getsource(o.run_v3)
    assert "build_agent.run(" not in src
```

- [ ] Implement, run, commit. `git commit -m "refactor(orchestrator): explicit give-up instead of free-text fallback in run_v3 task branch"`

### Task 5b: Discover task runs the deterministic gate + records ledger evidence

For discover tasks (empty `target_node_ids`), run exactly `VERIFY_TEST_CMD` and append one ledger event; do NOT ingest this cycle (the next cycle's `_runtime_ingest_phase` consumes it — reuse of the existing seam, `orchestrator.py:582`).

```python
    def _run_discover_gate(task, cycle: int) -> TaskReport:
        # done_when must be the canonical gate; discover tasks are built with
        # done_when=VERIFY_TEST_CMD (graph_scheduler._discover_task), so normalize
        # defensively rather than trusting a divergent value.
        cmd = VERIFY_TEST_CMD
        ok, out = sandbox_execute(cmd)
        from src.envstate.ledger import make_action_event
        ledger.append(make_action_event(
            step=global_step, cmd=cmd, success=ok, stdout=(out or ""),
            env_revision_before=global_step, env_revision_after=global_step,  # discover mutates nothing
            mutation_class=None, container_id=getattr(build_agent, "container_id", ""),
        ))
        return TaskReport(task.goal, "done" if ok else "blocked",
                          (CommandRecord(cmd, 0 if ok else 1, (out or "")[-2000:]),),
                          "deterministic discover gate")
```

- [ ] Write failing tests replacing `test_discover_task_uses_run` (`:236`):

```python
def test_discover_task_runs_gate_not_agent():
    events, used_run = _run_v3_discover_once(preseed_fail=False)
    assert not used_run                       # build_agent.run never called
    assert any(e.cmd == "python -m pytest -q" for e in events)

def test_discover_gate_failure_becomes_obligation_next_cycle():
    # Preseed a failed VERIFY_TEST_CMD event with ModuleNotFoundError: requests;
    # assert next-cycle runtime_ingest appends a pkg:requests obligation.
    graph = _run_v3_discover_ingest("ModuleNotFoundError: No module named 'requests'")
    assert graph.get(package_id("requests", None)) is not None

def test_local_import_discover_adds_no_package():
    # With RepoContext(local_names={'docs_src'}) wired (Phase 6), a docs_src failure
    # from the discover gate must NOT create pkg:docs-src.
    graph = _run_v3_discover_ingest("ModuleNotFoundError: No module named 'docs_src'",
                                    local_names={"docs_src"})
    assert graph.get(package_id("docs_src", None)) is None
```

- [ ] Implement, run, commit. `git commit -m "feat(orchestrator): discover task runs deterministic VERIFY_TEST_CMD gate + records ledger evidence"`

### Task 5c: Bounded stuck give-up (already free — assert it)

The existing `_sched_stuck` counter (`orchestrator.py:732-739`) already returns `GIVEUP_STUCK` after 2 consecutive discover rounds with no new nodes. No new machinery — add a regression test proving repeated unclassified `VERIFY_TEST_CMD` failures exit via `planner_giveup` (not an infinite loop), and a one-line comment noting the bound is intentional.

- [ ] Add `test_repeated_unclassified_discover_gives_up`; run; commit.

---

## Phase 6: Unify the repair entry + route diagnosis through every repair site

**Problem (arch MAJOR 3):** post-Phase-4/5 there are still two `run_structured_repair` call sites — the in-loop block-emit repair (`orchestrator.py:526-539`) and the task-branch repair (`:770-782`). If diagnosis routing wires into only one, the method is inconsistent again. Collapse both into one helper that diagnoses first.

**Files:** `src/envstate/orchestrator.py`, `src/envstate/repair_loop.py` (read `known_invalid`), `tests/envstate/test_repair_routing.py`.

**Add a single in-`run_v3` helper** (closure over the loop state) used by both sites:

```python
    _repo_ctx_holder = {"ctx": RepoContext()}   # rebuilt when invalid_names grows

    def _repair_or_route(graph, failed_id, bundle, cycle, *, target_hint=None, cap_failed_id=False):
        """Diagnose the failure that produced `bundle` BEFORE typed repair.
        ENVIRONMENT -> run_structured_repair (typed patch).
        REPO_INTERNAL_REF / RESIDUAL -> record out-of-scope; no repair (returns graph unchanged).
        INVALID_ATTEMPT -> add the disproven name to invalid_names; no repair.
        AMBIGUOUS -> allow the read-only probe turns already inside v3_build_agent.propose.
        """
        nonlocal _manual_blocks, _known_invalid, _repair_turns, _budget_exhausted
        ctx = _repo_ctx_holder["ctx"]
        diags = diagnose_all(tuple((c.cmd, c.output) for c in bundle.commands), ctx) if bundle else ()
        modes = {d.mode for d in diags}
        if Mode.REPO_INTERNAL_REF in modes or Mode.RESIDUAL in modes:
            # non-environment residual: do not mutate the graph via repair
            return graph
        if Mode.INVALID_ATTEMPT in modes:
            for d in diags:
                if d.mode is Mode.INVALID_ATTEMPT and d.discovery is None:
                    pass  # name already carried via classify; add to invalid_names below
            # (record disproven names into ctx for next cycle)
        # ENVIRONMENT (or AMBIGUOUS -> propose's own read-only turns) -> typed repair
        _out = run_structured_repair(
            graph, failed_id, bundle, cycle,
            propose=lambda s, **k: build_agent.propose(s, exec_readonly, **k),
            emit=lambda g, mb: block_emit(g, sandbox_execute, exec_readonly, ledger, cycle, manual_blocks=mb),
            manual_blocks=_manual_blocks, known_invalid=_known_invalid,
            max_repairs=MAX_REPAIRS_PER_BLOCK, repair_budget=_repair_turns,
            target_hint=target_hint, cap_failed_id=cap_failed_id)
        _manual_blocks = _out.manual_blocks
        _known_invalid = set(_out.known_invalid)
        _repair_turns -= _out.turns_spent
        if _out.budget_exhausted or _repair_turns <= 0:
            _budget_exhausted = True
        return _out.graph
```

Replace both call sites (the block-emit branch repair and the task-branch repair) with `_repair_or_route(...)`. The `bundle` is the `EvidenceBundle`/`_bundle` each site already has (its `.commands` carry `(cmd, rc, output)` — confirm `EvidenceBundle`'s command record exposes `.cmd`/`.output`; adapt the tuple accessor if the field names differ). Build `RepoContext` once from `scan.local_module_names(repo_path)` at loop start and rebuild when `invalid_names` grows.

- [ ] **Tests** (`tests/envstate/test_repair_routing.py`):

```python
def test_repo_internal_ref_bundle_skips_repair():
    # bundle whose only failure is ModuleNotFoundError: docs_src (docs_src local)
    # -> _repair_or_route returns graph unchanged, propose never called.
    ...
def test_environment_bundle_invokes_typed_repair():
    # bundle with ModuleNotFoundError: requests -> propose IS called.
    ...
def test_both_repair_sites_use_the_same_router():
    import inspect, src.envstate.orchestrator as o
    src = inspect.getsource(o.run_v3)
    assert src.count("run_structured_repair(") == 1   # only inside _repair_or_route
```

- [ ] Implement, run full `tests/envstate/ -q`, commit. `git commit -m "refactor(orchestrator): single diagnosis-routed repair entry (_repair_or_route) for all repair sites"`

---

## Phase 7: Collapse the fork — block-emit in-loop + mandatory terminal fresh replay

This is the keystone that makes "one loop" true and turns the installability gate binding.

**Files:** `src/envstate/orchestrator.py` (remove the `if ... enable_binding_install:` in-loop branch `:468-515`; add a terminal replay step after the loop), `src/envstate/gates.py` (`evaluate_installability_gate` accepts a real replay result), `scripts/run_v3_e2e.py` (always terminal-replay), `tests/envstate/test_terminal_replay.py`, `tests/envstate/test_gates.py`.

- [ ] **Step 1: Remove the in-loop binding branch.** Delete `orchestrator.py:468-515` (the `if enable_script_materialization and enable_binding_install:` branch and its `_binding_emit` local at `:448-466`). The block-emit branch (`elif` → now the sole branch) stays. `reset_to_base`/`run_install_script` are no longer used in-loop; they move to the terminal step.

- [ ] **Step 2: Add a terminal replay function** run once when the scheduler returns `done` (inside `_finish`, or just before returning `DONE`):

```python
    def _terminal_fresh_replay():
        """Render full setup.sh, replay from a fresh base, host-certify, return an InstallResult+certs.
        This is the BINDING installability proof (not the provisional graph heuristic)."""
        from python_deps.depgraph.build_script import render_build_script
        from src.envstate.install_localizer import certify_reciped_only, localize_install_failure
        script = render_build_script(current_map.dep_graph, _manual_blocks)
        reset_to_base()
        result = run_install_script(script)
        graph2, unsat = certify_reciped_only(current_map.dep_graph, exec_readonly, 10_000)
        failing = None if result.rc == 0 else (
            localize_install_failure(script, result.failing_command).node_id or (unsat[0] if unsat else None))
        return result, graph2, unsat, failing
```

Make it **mandatory**: when `reset_to_base`/`run_install_script` are provided, run it on the DONE path; fold its result into the gates and into the `RunTracer` (Phase 8). If the replay fails, return a non-done stop reason (`GIVEUP_REPLAY`) with the failing command recorded — the fresh replay is authoritative.

- [ ] **Step 3: Make the installability gate binding.** Extend `evaluate_installability_gate` to accept an optional real replay result:

```python
def evaluate_installability_gate(graph, replay=None) -> GateResult:
    if replay is not None:
        return GateResult(name="installability", passed=(replay.rc == 0),
            command="fresh-from-base setup.sh replay",
            provisional=False,
            evidence=("fresh replay rc=0" if replay.rc == 0
                      else f"fresh replay failed: {replay.failing_command}")[:_EVIDENCE_CAP])
    # ... existing provisional path unchanged ...
```

Thread the terminal `result` into `evaluate_gates(graph, run_tests_verified, replay=result)` on the DONE path.

- [ ] **Step 4: Driver.** In `scripts/run_v3_e2e.py`, stop advertising `--no-binding-install` as an ablation; pass replay unconditionally (keep the flag only as a deprecated hidden no-op if any caller depends on it). The final `render_build_script(dep_graph, final_map.manual_blocks)` (Phase 2) is already the artifact.

- [ ] **Step 5: Tests.**

```python
# tests/envstate/test_gates.py
def test_installability_gate_binding_on_real_replay():
    class _R: rc = 0; failing_command = None
    g = evaluate_installability_gate(None, replay=_R())
    assert g.passed and not g.provisional and "fresh replay rc=0" in g.evidence

def test_installability_gate_binding_fail():
    class _R: rc = 1; failing_command = "apt-get install -y libpq-dev"
    g = evaluate_installability_gate(None, replay=_R())
    assert not g.passed and not g.provisional and "libpq-dev" in g.evidence

# tests/envstate/test_terminal_replay.py
def test_done_path_runs_terminal_replay_and_reports_binding_gate():
    trace = _run_v3_to_done_with_fake_sandbox()
    assert trace.fresh_replay is not None and trace.fresh_replay.ran
    assert trace.gates["installability"]["provisional"] is False
```

- [ ] **Step 6: Run + commit.** `git commit -m "feat(orchestrator): collapse binding/block-emit fork — block-emit in-loop, mandatory terminal fresh-replay as binding installability gate"`

---

## Phase 8: Instrumentation + e2e proof harness

Prove three claims for every run: (1) the canonical loop was used; (2) the graph/script/fresh-replay contract holds; (3) the legacy paths did no work. Design credited to the e2e-proof spec.

**Files:**
- Create: `src/envstate/run_trace.py` (recorder + immutable snapshot)
- Create: `src/envstate/trace_verify.py` (assertion functions)
- Modify: `src/envstate/orchestrator.py` (accept optional `tracer`, record at key points)
- Modify: `scripts/run_v3_e2e.py` (emit the trace JSON + run verifiers)
- Create: `tests/envstate/test_run_trace.py`, `tests/envstate/scenarios/` fixtures
- Create: `scripts/run_v3_proof.py` (per-repo table + aggregate)

### Task 8a: `RunTracer` + `RunTrace` (append-only recorder → frozen snapshot)

```python
# src/envstate/run_trace.py
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class PatchGateRecord:
    cycle: int
    failed_block_id: str | None
    evidence_ref: str | None
    accepted: bool
    accepted_node_ids: tuple[str, ...]
    accepted_block_ids: tuple[str, ...]
    errors: tuple[str, ...]

@dataclass(frozen=True)
class DiscoverRecord:
    cycle: int
    command: str
    used_llm_mutation: bool
    new_node_ids: tuple[str, ...]
    diagnosis_modes: tuple[str, ...]

@dataclass(frozen=True)
class FreshReplayRecord:
    ran: bool
    setup_rc: int | None
    failing_command: str | None
    certified_node_ids: tuple[str, ...]
    unsatisfied_node_ids: tuple[str, ...]
    test_rc: int | None
    test_summary: str

@dataclass(frozen=True)
class RunTrace:
    repo: str = ""
    loop_mode: str = "v3_graph_typed_repair"
    used_emit_drain: bool = False
    used_repair_failed_nodes: bool = False
    used_build_agent_run: bool = False
    patchgate: tuple[PatchGateRecord, ...] = ()
    discover: tuple[DiscoverRecord, ...] = ()
    fresh_replay: FreshReplayRecord | None = None
    manual_block_ids: tuple[str, ...] = ()
    stop_reason: str = ""
    gates: dict = field(default_factory=dict)

    def to_dict(self) -> dict: ...   # dataclasses.asdict + serialize fresh_replay

class RunTracer:
    """Append-only host-owned recorder (same mutability exception as ActionLedger)."""
    def __init__(self, repo: str = "") -> None:
        self._repo = repo
        self._used_emit_drain = False
        self._used_repair_failed_nodes = False
        self._used_build_agent_run = False
        self._patchgate: list[PatchGateRecord] = []
        self._discover: list[DiscoverRecord] = []
        self._fresh_replay: FreshReplayRecord | None = None
        self._manual_block_ids: tuple[str, ...] = ()
    def mark_emit_drain(self) -> None: self._used_emit_drain = True
    def mark_repair_failed_nodes(self) -> None: self._used_repair_failed_nodes = True
    def mark_build_agent_run(self) -> None: self._used_build_agent_run = True
    def record_patchgate(self, r: PatchGateRecord) -> None: self._patchgate.append(r)
    def record_discover(self, r: DiscoverRecord) -> None: self._discover.append(r)
    def record_fresh_replay(self, r: FreshReplayRecord) -> None: self._fresh_replay = r
    def set_manual_blocks(self, ids: tuple[str, ...]) -> None: self._manual_block_ids = tuple(ids)
    def snapshot(self, *, stop_reason: str, gates: dict) -> RunTrace:
        return RunTrace(repo=self._repo, used_emit_drain=self._used_emit_drain,
            used_repair_failed_nodes=self._used_repair_failed_nodes,
            used_build_agent_run=self._used_build_agent_run,
            patchgate=tuple(self._patchgate), discover=tuple(self._discover),
            fresh_replay=self._fresh_replay, manual_block_ids=self._manual_block_ids,
            stop_reason=stop_reason, gates=gates)
```

**Wire (all guarded by `if tracer is not None:` → byte-identical when off):**
- `run_v3(..., tracer: RunTracer | None = None)`.
- In `_repair_or_route`: after `admit_proposal`/`run_structured_repair`, `tracer.record_patchgate(PatchGateRecord(...))` from `_out` (accepted node/block ids, errors).
- In `_run_discover_gate` + next-cycle ingest: `tracer.record_discover(DiscoverRecord(cycle, VERIFY_TEST_CMD, used_llm_mutation=False, new_node_ids=..., diagnosis_modes=[d.mode.value for d in diags]))`.
- In `_terminal_fresh_replay`: `tracer.record_fresh_replay(FreshReplayRecord(ran=True, setup_rc=result.rc, ...))`.
- On exit in `_finish`: `tracer.set_manual_blocks(tuple(b.block_id for b in _manual_blocks))`.
- The `mark_emit_drain`/`mark_repair_failed_nodes`/`mark_build_agent_run` hooks stay wired at those (now-removed-from-`run_v3`) call sites in `run_v1`/baseline code, so a regression that re-introduces them into `run_v3` trips the verifier.

- [ ] TDD: `test_run_trace.py` — construct a `RunTracer`, record each kind, assert `snapshot()` is frozen and `to_dict()` round-trips. Commit.

### Task 8b: `trace_verify.py` — the canonical-run assertions

```python
# src/envstate/trace_verify.py
from __future__ import annotations
from src.envstate.run_trace import RunTrace

def verify_canonical_trace(t: RunTrace) -> list[str]:
    errs: list[str] = []
    if t.used_emit_drain:            errs.append("legacy emit_drain executed in canonical run")
    if t.used_repair_failed_nodes:   errs.append("legacy repair_failed_nodes executed")
    if t.used_build_agent_run:       errs.append("free-text build_agent.run executed")
    if t.loop_mode != "v3_graph_typed_repair": errs.append(f"non-canonical loop_mode {t.loop_mode!r}")
    if t.stop_reason in ("done", "planner_done", "done_flag"):
        if t.fresh_replay is None or not t.fresh_replay.ran:
            errs.append("done reached without a terminal fresh replay")
        elif t.fresh_replay.setup_rc != 0:
            errs.append(f"done reached but fresh replay failed: {t.fresh_replay.failing_command}")
        if t.gates.get("installability", {}).get("provisional", True):
            errs.append("installability gate still provisional on a done run")
    for d in t.discover:
        if d.used_llm_mutation: errs.append(f"discover cycle {d.cycle} used LLM mutation, not the deterministic gate")
    return errs

def verify_artifact_consistency(script_text: str, manual_block_ids: tuple[str, ...]) -> list[str]:
    errs: list[str] = []
    if "(UNSCHEDULED BLOCKS)" in script_text:
        errs.append("rendered setup.sh contains an UNSCHEDULED BLOCKS section")
    for bid in manual_block_ids:
        if bid not in script_text:
            errs.append(f"governed manual block {bid} missing from final setup.sh")
    return errs

def verify_local_import_guard(t: RunTrace) -> list[str]:
    # No discover cycle produced a package node for a REPO_INTERNAL_REF diagnosis.
    errs: list[str] = []
    for d in t.discover:
        if "repo_internal_reference" in d.diagnosis_modes:
            if any(nid.startswith("pkg:") for nid in d.new_node_ids):
                errs.append(f"discover cycle {d.cycle} added a package node for a repo-local import")
    return errs
```

- [ ] TDD: `test_trace_verify.py` — a clean canonical trace → `[]`; a trace with `used_emit_drain=True` → one error; a done trace with failed replay → error; an UNSCHEDULED script → error. Commit.

### Task 8c: Scenario fixtures (fast, fake-sandbox — no real Docker)

Each fixture drives `run_v3` with a scripted fake `sandbox_execute`/`exec_readonly` (reuse patterns from `tests/envstate/test_v3_repair_wiring.py`) and asserts the expected trace. `tests/envstate/scenarios/`:

| Scenario | Injected failure | Assert |
|---|---|---|
| `test_missing_native_lib` | `pip`/import fails → `libGL.so.1 cannot open` | diagnosis SYSTEM_LIB → apt/manual block → node SATISFIED → `verify_canonical_trace == []` |
| `test_missing_external_pkg` | `ModuleNotFoundError: requests` from the discover gate | next-cycle ingest adds `pkg:requests`; typed repair installs; host-certifies |
| `test_repo_local_import_guard` | `ModuleNotFoundError: docs_src` (docs_src local) | `Mode.REPO_INTERNAL_REF`; NO `pkg:docs-src`; NO `pip install docs-src`; `verify_local_import_guard == []` |
| `test_bad_provider_not_retried` | `apt-get install libplacebodev` → no candidate; `libplacebo-dev` valid | invalid name recorded in `invalid_names`, not retried; replacement accepted |
| `test_manual_block_artifact_preserved` | force an LLM `ScriptPatch` | `manual_block_ids` non-empty; block executed in-loop; block present in final `render_build_script`; present in the terminal replay script |

- [ ] Write each scenario as a failing test, run against the (now real) canonical loop, commit per scenario.

### Task 8d: Proof harness + report table

`scripts/run_v3_proof.py` runs the real-container e2e over the benchmark set, writes one JSON trace per repo, and emits:

```text
repo | result | legacy_used | graph_nodes_added | patchgate_accepts | manual_blocks | fresh_replay | tests_pass | residual_reason
```

plus aggregate: `canonical_loop_runs`, `legacy_path_violations` (MUST be 0), `fresh_replay_pass_rate`, `manual_block_artifact_mismatches` (MUST be 0), `local_import_false_package_attempts` (MUST be 0). The **composite success** predicate (strongest proof):

```python
def canonical_success(trace, script_text) -> bool:
    return (trace.stop_reason in ("done", "planner_done", "done_flag")
        and trace.fresh_replay and trace.fresh_replay.setup_rc == 0
        and trace.fresh_replay.test_rc == 0
        and not verify_canonical_trace(trace)                       # no legacy path
        and not verify_artifact_consistency(script_text, trace.manual_block_ids)  # artifact complete
        and not trace.fresh_replay.unsatisfied_node_ids)            # host certifiers all satisfied
```

- [ ] Wire `scripts/run_v3_e2e.py` to build a `RunTracer`, pass it, and on exit write `trace.to_dict()` to `--trace-out` and print `verify_canonical_trace`/`verify_artifact_consistency` results. Commit. (`run_v3_proof.py` itself is a thin loop over `run_v3_e2e`; no unit test — it's the reporting driver.)

---

## Phase 9: Quarantine legacy paths into named baseline/ablation modules

Only after Phases 1–8 are green and a benchmark sanity run shows `legacy_path_violations: 0`.

- [ ] Remove the `enable_script_materialization` parameter from `run_v3` entirely (all callers now rely on the canonical path); delete the interim `ValueError` guard from Phase 4.
- [ ] Move `emit_drain`/`repair_failed_nodes` usage into an explicitly named `run_v1` baseline surface (they already live in `depgraph_live.py`; ensure no `run_v3`-adjacent code imports them).
- [ ] Rename per the concept map: `v1_emit_drain_baseline`, `react_build_agent_baseline`, `v3_graph_typed_repair` (the canonical path is just `run_v3`).
- [ ] Commit. `git commit -m "refactor: quarantine legacy emit/react paths as named baselines; run_v3 is single-path"`

---

## Tests Known To Need Changes (grounding-verified)

- `tests/test_v3_block_emit_wiring.py` — replace `test_toggle_off_uses_emit_drain_and_repair` (`:88`) → `test_toggle_off_now_raises`.
- `tests/test_graph_scheduler_wiring.py` — **split** `test_drain_runs_under_flag_as_prefix` (`:170-215`, ONE function): drop v3 half (`:189-200`), keep v1 half (`:202-215`) as `test_v1_drain_runs_as_prefix`.
- `tests/test_run_v1_turn_budget.py` — invert the source-level `emit_drain`-in-`run_v3` assertion (now must be ABSENT).
- `tests/envstate/test_v3_task_branch.py` — replace `test_discover_task_uses_run` (`:236`) → gate/evidence; `test_b3_ablation_does_not_use_propose` (`:244`) → deprecated-flag-raises; `test_obligation_task_without_exec_readonly_falls_to_freetext` (`:257`) → clean give-up.
- `tests/depgraph/test_patch_gate_validate.py` — add the four script-patch validation cases (Phase 1).
- `tests/depgraph/test_build_script.py` — replace `test_block_with_unknown_wave_lands_in_catch_all` (`:185`) → `test_block_with_unknown_wave_raises`.
- `tests/envstate/test_gates.py` — add binding-replay gate cases (Phase 7).

New test files: `test_manual_blocks_persist.py`, `test_diagnose_*` (companion), `test_repair_routing.py`, `test_terminal_replay.py`, `test_run_trace.py`, `test_trace_verify.py`, `scenarios/*`.

---

## Self-Review

**Spec/review coverage:** single-loop claim → Canonical Model + Phase 7 (arch MAJOR 1). Diagnosis-router ordering/scope → Phase 3 (companion Phase 1) + Phase 6 (companion Phase 2, all repair sites) (arch MAJOR 2/3, exec BLOCKER 3). `known_invalid` key-space conflict → Phase 3 reconciliation #3 (arch MAJOR 4). `manual_blocks` persistence signature → Phase 2 decided (arch MEDIUM 7, exec BLOCKER 2). Phase renumbering → the Phase↔Order map + Phase 9 last (exec BLOCKER 1). Silent-no-op flag → Phase 4 raises then Phase 9 removes (arch MEDIUM 5). Provisional→binding gate → Phase 7 required (arch MEDIUM 6, grounding nit). Phase 4/5 combined branch → Phase 5 literal 3-way (exec BLOCKER 4). Prose→pytest → every phase has real test code. E2e proof → Phase 8 (user request, full spec).

**Placeholder scan:** signatures given for every new symbol (`WorldModelMap.manual_blocks`, `diagnose_all`, `_repair_or_route`, `RunTracer`/`RunTrace`/`FreshReplayRecord`, `verify_*`, terminal replay). Two spots intentionally reference existing code to copy rather than re-derive: the typed-repair block body (Phase 5a, `orchestrator.py:760-791` unchanged) and companion Tasks 1–3 (DRY — do not duplicate). `EvidenceBundle`'s command-record field names (`.cmd`/`.output`) must be confirmed on first touch in Phase 6 (noted inline).

**Type consistency:** `Block` fields (`block_id/wave/commands/target_node_ids/provider_ids/check_commands/evidence_refs`) match `patch_gate._script_patch_to_block`. `Discovery | None` is the ingest seam type throughout. `GateResult` gains a `replay=` param, not a new field. `make_action_event` keyword signature matches `ledger.py:24-49`. `RunTracer` mirrors `ActionLedger`'s append-only shape.

**Open decision flagged to the user:** the Canonical Model (block-emit in-loop + terminal replay) is a committed architectural choice with a stated reversible alternative. If rejected, Phase 7 degrades to "label the fork + mandatory terminal replay" and Phases 1–6/8 are unaffected.

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per phase, two-stage review between phases. Best for a 9-phase refactor touching the core loop.
2. **Inline Execution** — batch with checkpoints in this session.

Recommended split point: land **Phases 1–3** first (independent, low-risk, each green on its own), checkpoint, then **4–7** (the loop restructure) as one reviewed block, then **8–9** (proof + quarantine).
