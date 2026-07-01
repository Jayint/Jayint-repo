# V3-Core Canonical Loop Refactor — Implementation Plan (v3 — Model B: fresh-replay executor)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **v3 (Model B) supersedes v2.** v2 committed to incremental block-emit + a terminal replay proof; after design discussion the user chose **Model B** — fresh full-script replay as the SOLE executor (graph = source of truth; every certification is from-scratch). **Phases 1–3 (PatchGate hardening, `manual_blocks` persistence, diagnosis router) were already executed and are executor-independent** (commits through `5183f24`); **Phases 4–9 below are rewritten for the fresh-replay executor.** (v2 itself superseded the v1 strategy draft after three independent reviews, resolving every deferred decision to a concrete signature and folding in the e2e proof layer.)

**Goal:** Make `run_v3` read as one canonical paper loop — `build graph → render setup.sh → fresh-replay from base → host certify → (on failure) diagnose → typed LLM patch → PatchGate → graph/manual-block update` — where **fresh replay is the sole executor**, every legacy/ablation branch is removed from the method, and its absence is proven by an e2e trace.

**Architecture (Model B — settled 2026-07-01):** The canonical loop has exactly **one execution strategy**: fresh full-script replay from base every cycle — `reset_to_base()` → `run_install_script(render_build_script(graph, manual_blocks))` → host-certify against the fresh container. The graph/script is the source of truth; the container carries **no cross-cycle state**. Invariant: **env = f(base image, requirement graph, governed manual blocks)**, so `SATISFIED` means "satisfied after replay from scratch" and the emitted `setup.sh` *is* exactly what ran — there is no separate terminal proof, the latest cycle's replay is the proof, and the installability gate is binding by construction. Incremental `block_emit`, legacy `emit_drain`/`repair_failed_nodes`, and the free-text `build_agent.run` fallback are all removed from `run_v3` and kept only as named ablation / `run_v1` baselines. A pure diagnosis router classifies each failure before repair so the graph never learns a fake environment requirement (e.g. a repo-local import). A `RunTracer` records which path actually executed so tests can *prove* the ablation/legacy paths are dead in the method.

**Future optimization (recorded, NOT built — see end):** replace `reset_to_base + bash setup.sh` with rendered-Dockerfile + `docker build` for layer-cached replays — preserves the invariant, dissolves the per-repair-attempt full-reinstall cost. Deferred; the executor here is `reset_to_base + run_install_script`.

**Tech Stack:** Python 3, stdlib `dataclasses`/`enum`/`re`, pytest. No new dependencies.

---

## Canonical Model — the one decision that shapes every phase

The v1 draft left this implicit; v2 commits to it. A paper reader opening `run_v3` must see **one** loop.

```text
EACH CYCLE — a single execution strategy (fresh replay is the ONLY executor):
    script = render_build_script(graph, manual_blocks)     # whole setup.sh
    reset_to_base(); run_install_script(script)            # fresh from base, every cycle
    host-certify every node against the FRESH container    # sole SATISFIED writer
    if setup rc==0 AND gate/tests pass -> return done      # installability BINDING by construction
    else:
        diagnose the failing command -> route
        ENVIRONMENT -> typed PatchProposal -> PatchGate -> graph/manual-block update
        (inner repair: re-render -> fresh replay -> re-check, per attempt)
    scheduler next_decision: task | done | giveup

INVARIANT: env = f(base image, requirement graph, governed manual blocks).
No hidden live-container state; SATISFIED == satisfied after replay from scratch;
the emitted setup.sh IS exactly what ran. There is NO separate terminal proof —
the latest cycle's replay is the proof.
```

**What this replaces:** today `_dep_emit_phase` has three in-loop branches — fresh replay (`enable_binding_install`, `orchestrator.py:468`), incremental block-emit (`:516`), and legacy `emit_drain` (`:540`). Model B makes the **fresh-replay branch the sole executor** and deletes the other two from `run_v3`. This is the user-chosen canonical model: the reproducibility invariant holds *per certification*, not just at the end, and there is no dirty-container drift. Cost is the per-repair-attempt full reinstall — bounded by (a) rendering-hash memoization that skips a replay when `(graph, manual_blocks)` is unchanged since the last replay, and (b) the recorded future Dockerfile-cache optimization. `block_emit` survives ONLY as a named fast ablation (Phase 9), never inside the method.

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
| 4 | Make fresh replay the sole executor (drop block_emit + emit_drain from `run_v3`) | one execution strategy; graph = source of truth | — |
| 5 | Single task-dispatch branch (gate-evidence / typed-repair / give-up) | no free-text mutation in `run_v3` | 3, 4 |
| 6 | Unify repair entry + wire diagnosis routing through all sites | every repair is mode-routed identically | 3, 5 |
| 7 | Installability gate binding from the per-cycle replay (no provisional path) | binding gate by construction | 4 |
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

## Phase 4: Make fresh replay the sole canonical executor

Collapse `_dep_emit_phase`'s three-branch structure to one: **fresh full-script replay from base, every cycle.** The `if ... enable_binding_install:` fresh-replay branch (`orchestrator.py:468-515`) already renders → `reset_to_base` → `run_install_script` → `certify_reciped_only` → repair via `run_structured_repair` with `_binding_emit`. Make it unconditional; delete the incremental `block_emit` branch (`:516-539`) and the legacy `emit_drain` branch (`:540-559`) from `run_v3`. `block_emit`/`emit_drain` survive only as ablation / `run_v1` baselines (Phase 9).

**Files:**
- Modify: `src/envstate/orchestrator.py` (`_dep_emit_phase` `:431-580`; keep the `_binding_emit` local `:448-466` — it is the canonical emit closure)
- Modify: tests listed below

**Steps:**

- [ ] **Step 1: Make the fresh-replay body unconditional.** In `_dep_emit_phase`, remove the branch selection so the body is always: start-of-cycle `certify_refresh` → `_binding_emit`-style render/reset/install/certify → on failure `run_structured_repair(..., emit=lambda g, mb: _binding_emit(g, mb, cycle))` (Phase 6 replaces this call with `_repair_or_route`). Delete the `elif enable_script_materialization: block_emit(...)` block and the `else: emit_drain(...) + repair_failed_nodes(...)` block entirely, plus their now-unused imports inside this function.
  - **Hoist `_binding_emit`** out of `_dep_emit_phase` up to `run_v3` scope (a sibling closure over `exec_readonly`/`reset_to_base`/`run_install_script`/`sandbox_execute`), signature `(graph, manual_blocks, cycle) -> (graph, evidence_bundle_or_None, failed_node)`. Both `_dep_emit_phase` and Phase 6's `_repair_or_route` must call the SAME replay emit; if it stays nested, `_repair_or_route` can't see it.

- [ ] **Step 2: Every cycle is a real fresh replay — NO memoization.** `_binding_emit` always `reset_to_base` + `run_install_script` + certify, on every call. (An earlier draft memoized a skip when the render-hash was unchanged; it was **removed** after review: a memoized-skip cycle returned `bundle=None` for a still-unsatisfied node, starving `run_structured_repair` of fresh install-failure stderr on exactly the nodes that survive past one cycle. The "redundant" replay is not wasteful — it is what *produces* the current evidence bundle the repair loop needs, and "every cycle is a fresh replay from base, no exceptions" is the purest Model-B invariant. The genuine cost optimization is the recorded cached-`docker build` future work, not a skip.)

- [ ] **Step 3: Require the executor callables.** `reset_to_base` and `run_install_script` are no longer optional — the canonical executor needs them. Near the top of `run_v3` (replacing the old `enable_binding_install`/`enable_script_materialization` contradiction guard at `:385-387`):

```python
    if reset_to_base is None or run_install_script is None:
        raise ValueError("run_v3 is fresh-replay-only: reset_to_base and run_install_script are required "
                         "(use the block_emit ablation or run_v1 for incremental/legacy execution)")
```

Make `enable_script_materialization`/`enable_binding_install` deprecated no-op-or-raise: if either is passed `False`, raise the same ValueError. (Phase 9 deletes the parameters.) Remove `_repaired_ids` (`:397`) — only `repair_failed_nodes` read it.

- [ ] **Step 4: Update tests** (grounding-verified targets):
  - `tests/test_v3_block_emit_wiring.py` — `test_toggle_off_uses_emit_drain_and_repair` (`:88`) → `test_toggle_off_now_raises` (asserting `pytest.raises(ValueError)`); any assertion that `block_emit` runs inside canonical `run_v3` → move to the ablation test (Phase 9) OR replace with the replay-executor assertion below.
  - `tests/test_graph_scheduler_wiring.py` — **split** `test_drain_runs_under_flag_as_prefix` (`:170-215`, ONE function): drop the v3 half (`:189-200`), keep the v1 half (`:202-215`) as `test_v1_drain_runs_as_prefix`.
  - `tests/test_run_v1_turn_budget.py` — invert the source-level `emit_drain`-in-`run_v3` assertion (now must be ABSENT).
  - **New** `tests/test_v3_replay_executor.py::test_run_v3_uses_fresh_replay_each_cycle` — with a fake sandbox recording calls, assert `reset_to_base` + `run_install_script` are invoked (and `block_emit`/`emit_drain` are NOT) on a cycle where the render hash changed; assert the replay is SKIPPED when the render hash is unchanged.
  - `emit_drain()` / `repair_failed_nodes()` / `block_emit()` stay in their modules for `run_v1`/ablation + their direct tests — untouched.

- [ ] **Step 5: Run + Commit.** `python3 -m pytest tests/test_v3_block_emit_wiring.py tests/test_graph_scheduler_wiring.py tests/test_run_v1_turn_budget.py tests/test_v3_replay_executor.py -q`; `git commit -m "refactor(orchestrator): fresh full-script replay is the sole run_v3 executor (drop block_emit + emit_drain from the method)"`

---

## Phase 5: Single task-dispatch branch — gate evidence / typed repair / explicit give-up

Replace the free-text `build_agent.run` fallback (`orchestrator.py:792-796`) with one legible branch. Split into three independently-testable tasks.

**Model-B note:** the task-branch's typed-repair `_emit` closure changes from `block_emit(...)` to the **replay emit** `lambda g, mb: _binding_emit(g, mb, cycle)`, so `block_emit` is fully gone from `run_v3` after this phase (Phase 6 then factors the two replay-repair calls into one router). The discover gate (Task 5b) runs `VERIFY_TEST_CMD` via `sandbox_execute`, which — because the sandbox is a single container object that `_dep_emit_phase` just `reset_to_base`+replayed at the top of the cycle — automatically executes against the **fresh post-replay container**; no separate replay is needed in the discover path.

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

## Phase 6: Unify the repair entry + route diagnosis through both repair sites

**Problem:** post-Phase-4/5 there are two `run_structured_repair` call sites — the main-loop replay repair (inside `_dep_emit_phase`) and the task-branch replay repair. Both now use the replay emit `_binding_emit`, but diagnosis routing must wire into BOTH identically or the method is inconsistent. Collapse them into one helper that diagnoses first.

**Files:** `src/envstate/orchestrator.py`, `tests/envstate/test_repair_routing.py`.

**Build `RepoContext` correctly (Task-3 reviewer Minors #1/#2 folded in):** `RepoContext.invalid_names` normalization is a CALLER contract — `diagnose.RepoContext` does not self-normalize. The orchestrator MUST normalize disproven names before constructing it, using the SAME normalizer the mapping layer uses: `python_deps.import_mapping.normalize_package_name` (`re.sub(r"[-_.]+","-",name).lower()`, which folds `.` — stronger than `diagnose._norm`). Build the local-names set once at loop start from `scan.local_module_names(repo_path)`; rebuild the context whenever a disproven name is added.

**Add a single in-`run_v3` helper** (closure over loop state), emit = replay:

```python
    from python_deps.depgraph.diagnose import RepoContext, Mode, diagnose_all
    from python_deps.depgraph import scan
    from python_deps.import_mapping import normalize_package_name
    _local_names = frozenset(scan.local_module_names(repo_path)) if repo_path else frozenset()
    _invalid_names: set[str] = set()
    def _repo_ctx() -> RepoContext:
        return RepoContext(local_names=_local_names, invalid_names=frozenset(_invalid_names))

    def _repair_or_route(graph, failed_id, bundle, cycle, *, target_hint=None, cap_failed_id=False):
        """Diagnose the failure that produced `bundle` BEFORE typed repair.
        ENVIRONMENT / AMBIGUOUS -> run_structured_repair (AMBIGUOUS uses propose's read-only turns).
        REPO_INTERNAL_REF / RESIDUAL -> non-environment: return graph unchanged (no repair).
        INVALID_ATTEMPT (and nothing environment-shaped) -> record normalized disproven name; no repair.
        """
        nonlocal _manual_blocks, _known_invalid, _repair_turns, _budget_exhausted
        diags = diagnose_all(tuple((c.cmd, c.output) for c in bundle.commands), _repo_ctx()) if bundle else ()
        modes = {d.mode for d in diags}
        if Mode.REPO_INTERNAL_REF in modes or Mode.RESIDUAL in modes:
            return graph
        if Mode.INVALID_ATTEMPT in modes and not (modes & {Mode.ENVIRONMENT, Mode.AMBIGUOUS}):
            for d in diags:
                if d.mode is Mode.INVALID_ATTEMPT and d.discovery is not None:
                    _invalid_names.add(normalize_package_name(d.discovery.name))
            return graph
        _out = run_structured_repair(
            graph, failed_id, bundle, cycle,
            propose=lambda s, **k: build_agent.propose(s, exec_readonly, **k),
            emit=lambda g, mb: _binding_emit(g, mb, cycle),   # REPLAY emit (Model B)
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

Replace both `run_structured_repair(...)` call sites with `_repair_or_route(...)`. `bundle.commands` carry `(cmd, rc, output)` — confirm `EvidenceBundle`'s command record exposes `.cmd`/`.output` and adapt the accessor if the field names differ. Thread `repo_path` into `run_v3` (add param; the driver passes the repo dir — `run_v3_e2e.py` has `args.repo`).

- [ ] **Tests** (`tests/envstate/test_repair_routing.py`):

```python
def test_repo_internal_ref_bundle_skips_repair():
    # bundle whose only failure is ModuleNotFoundError: docs_src (docs_src local)
    # -> _repair_or_route returns graph unchanged, propose never called.
    ...
def test_invalid_attempt_records_normalized_name_no_repair():
    # bundle: "No matching distribution found for Frobnicate_9000"
    # -> propose NOT called; "frobnicate-9000" now in the next RepoContext.invalid_names.
    ...
def test_environment_bundle_invokes_typed_repair_with_replay_emit():
    # bundle with ModuleNotFoundError: requests -> propose IS called.
    ...
def test_single_repair_call_site_and_no_block_emit_in_source():
    import inspect, src.envstate.orchestrator as o
    src = inspect.getsource(o.run_v3)
    assert src.count("run_structured_repair(") == 1   # only inside _repair_or_route
    assert "block_emit(" not in src                   # replay is the only executor
```

- [ ] Implement, run `tests/envstate -q`, commit. `git commit -m "refactor(orchestrator): single diagnosis-routed replay repair entry (_repair_or_route) for both sites"`

---

## Phase 6b: Wire the local-import guard at the ingest path (completes the guard end-to-end)

**Gap found during Phase 8 (must-fix):** the local-import guard was wired at REPAIR (`_repair_or_route` runs `diagnose_all`) but NOT at INGEST. `_runtime_ingest_phase` used raw `classify_observation`, so a repo-local import (`docs_src`) failing via the discover→ingest path got a bogus `pkg:docs-src` node ADDED (then failing every replay forever). This completes the companion-plan Task-3 intent (wire `make_diagnostic_classifier` into ingest), which was deferred and never done — the guard is the design's single highest-value guard, so it must apply end-to-end, not just at repair.

**Files:** `src/envstate/orchestrator.py` (`_runtime_ingest_phase` classifier construction), `tests/envstate/test_ingest_local_import_guard.py`.

- [ ] Replace the deterministic tier with `classifiers = (make_diagnostic_classifier(_repo_ctx()),)` (applies the guard at ingest: local-import → None; invalid/residual/ambiguous → None; ENVIRONMENT → Discovery). `_repo_ctx()` is the Phase-6 `run_v3`-scope closure; `_runtime_ingest_phase` is a sibling closure that can call it.
- [ ] Guard the LLM tier: wrap `_bounded_llm` in `_guarded_llm` that drops a `Discovery` whose `disc.data["import_name"]` (or `disc.name`) satisfies `is_local_import(...)`.
- [ ] Tests (discover→ingest path): `ModuleNotFoundError: docs_src` (docs_src local) → NO `pkg:docs-src` node; `ModuleNotFoundError: requests` (external) → `pkg:requests` IS added (guard doesn't over-block).
- [ ] Commit. `git commit -m "fix(orchestrator): wire local-import guard at ingest (make_diagnostic_classifier in _runtime_ingest_phase)"`

---

## Phase 7: Installability gate binding by construction (from the per-cycle replay)

Under Model B the executor is already fresh replay (Phase 4), so there is **no separate terminal-replay step** — the latest cycle's replay result *is* the installability proof. This phase makes the gate read that result and drops the provisional graph-heuristic from the canonical path.

**Files:** `src/envstate/orchestrator.py` (carry the latest replay `InstallResult`), `src/envstate/gates.py`, `scripts/run_v3_e2e.py`, `tests/envstate/test_gates.py`.

- [ ] **Step 1: Carry the latest replay result.** In `run_v3`, keep `nonlocal _last_replay_result` (an `InstallResult | None`). `_dep_emit_phase` sets it after each replay — and since every cycle replays (Phase 4, no memoization), it always reflects the latest from-base build. It answers "does the current graph+blocks build from base."

- [ ] **Step 2: Make the installability gate binding.** Extend `evaluate_installability_gate` to accept the real replay result:

```python
def evaluate_installability_gate(graph, replay=None) -> GateResult:
    if replay is not None:
        return GateResult(name="installability", passed=(replay.rc == 0),
            command="fresh-from-base setup.sh replay",
            provisional=False,
            evidence=("fresh replay rc=0" if replay.rc == 0
                      else f"fresh replay failed: {replay.failing_command}")[:_EVIDENCE_CAP])
    # ... existing provisional graph-frontier path unchanged (used only by the block_emit ablation) ...
```

Thread `_last_replay_result` into `evaluate_gates(graph, run_tests_verified, replay=_last_replay_result)` inside `_finish` (`orchestrator.py:423-427`). On the canonical path `replay` is always non-None (Phase 4 guarantees the executor ran), so the gate is always binding.

- [ ] **Step 3: EVERY success door requires a green replay (authoritative).** Add a `_finalize_if_replayed(reason)` helper: return `GIVEUP_REPLAY` if `_last_replay_result is None or _last_replay_result.rc != 0`, else `_finish(reason)`. Route ALL success-returning paths through it — not only the scheduler's `decision.action == "done"`, but also the maintainer-driven `done_flag` path (`TerminationReason.DONE_FLAG`) and any `planner_done` return. (`run_v3_e2e` treats `done`/`done_flag`/`planner_done` as equally successful, so a single unguarded door reintroduces hollow success — this was a review finding.) Never report a success on a build that didn't reproduce from base. Cover both the `rc != 0` and the `is None` branches with tests.

- [ ] **Step 4: Driver.** `scripts/run_v3_e2e.py`: drop `--no-binding-install` (replay is unconditional). The final `render_build_script(dep_graph, final_map.manual_blocks)` (Phase 2) is the artifact and equals what ran.

- [ ] **Step 5: Tests** (`tests/envstate/test_gates.py`):

```python
def test_installability_gate_binding_on_real_replay():
    class _R: rc = 0; failing_command = None
    g = evaluate_installability_gate(None, replay=_R())
    assert g.passed and not g.provisional and "fresh replay rc=0" in g.evidence

def test_installability_gate_binding_fail():
    class _R: rc = 1; failing_command = "apt-get install -y libpq-dev"
    g = evaluate_installability_gate(None, replay=_R())
    assert not g.passed and not g.provisional and "libpq-dev" in g.evidence

def test_done_reports_binding_gate_not_provisional():
    trace = _run_v3_to_done_with_fake_sandbox()   # fake replay returns rc=0
    assert trace.gates["installability"]["provisional"] is False
```

- [ ] **Step 6: Run + commit.** `git commit -m "feat(gates): installability binding from the per-cycle fresh replay (no provisional path in the method)"`

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
    used_block_emit: bool = False          # block_emit lives only in the ablation; MUST be False in the method
    patchgate: tuple[PatchGateRecord, ...] = ()
    discover: tuple[DiscoverRecord, ...] = ()
    replays: tuple[FreshReplayRecord, ...] = ()   # one per cycle that actually replayed (Model B)
    manual_block_ids: tuple[str, ...] = ()
    stop_reason: str = ""
    gates: dict = field(default_factory=dict)

    @property
    def last_replay(self) -> "FreshReplayRecord | None":
        return self.replays[-1] if self.replays else None

    def to_dict(self) -> dict: ...   # dataclasses.asdict + serialize replays

class RunTracer:
    """Append-only host-owned recorder (same mutability exception as ActionLedger)."""
    def __init__(self, repo: str = "") -> None:
        self._repo = repo
        self._used_emit_drain = False
        self._used_repair_failed_nodes = False
        self._used_build_agent_run = False
        self._used_block_emit = False
        self._patchgate: list[PatchGateRecord] = []
        self._discover: list[DiscoverRecord] = []
        self._replays: list[FreshReplayRecord] = []
        self._manual_block_ids: tuple[str, ...] = ()
    def mark_emit_drain(self) -> None: self._used_emit_drain = True
    def mark_repair_failed_nodes(self) -> None: self._used_repair_failed_nodes = True
    def mark_build_agent_run(self) -> None: self._used_build_agent_run = True
    def mark_block_emit(self) -> None: self._used_block_emit = True
    def record_patchgate(self, r: PatchGateRecord) -> None: self._patchgate.append(r)
    def record_discover(self, r: DiscoverRecord) -> None: self._discover.append(r)
    def record_replay(self, r: FreshReplayRecord) -> None: self._replays.append(r)
    def set_manual_blocks(self, ids: tuple[str, ...]) -> None: self._manual_block_ids = tuple(ids)
    def snapshot(self, *, stop_reason: str, gates: dict) -> RunTrace:
        return RunTrace(repo=self._repo, used_emit_drain=self._used_emit_drain,
            used_repair_failed_nodes=self._used_repair_failed_nodes,
            used_build_agent_run=self._used_build_agent_run, used_block_emit=self._used_block_emit,
            patchgate=tuple(self._patchgate), discover=tuple(self._discover),
            replays=tuple(self._replays), manual_block_ids=self._manual_block_ids,
            stop_reason=stop_reason, gates=gates)
```

**Wire (all guarded by `if tracer is not None:` → byte-identical when off):**
- `run_v3(..., tracer: RunTracer | None = None)`.
- In `_repair_or_route`: after `run_structured_repair`, `tracer.record_patchgate(PatchGateRecord(...))` from `_out` (accepted node/block ids, errors).
- In `_run_discover_gate` + next-cycle ingest: `tracer.record_discover(DiscoverRecord(cycle, VERIFY_TEST_CMD, used_llm_mutation=False, new_node_ids=..., diagnosis_modes=[d.mode.value for d in diags]))`.
- In `_dep_emit_phase`, after each replay (every cycle replays — Phase 4): `tracer.record_replay(FreshReplayRecord(ran=True, setup_rc=result.rc, failing_command=..., certified_node_ids=..., unsatisfied_node_ids=..., test_rc=..., test_summary=...))`. One record per cycle (Model B).
- On exit in `_finish`: `tracer.set_manual_blocks(tuple(b.block_id for b in _manual_blocks))`.
- The `mark_emit_drain`/`mark_repair_failed_nodes`/`mark_build_agent_run`/`mark_block_emit` hooks stay wired at those (now-removed-from-`run_v3`) call sites in `run_v1`/ablation code, so a regression that re-introduces any of them into `run_v3` trips the verifier.

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
    if t.used_block_emit:            errs.append("block_emit ablation executed inside the method")
    if t.loop_mode != "v3_graph_typed_repair": errs.append(f"non-canonical loop_mode {t.loop_mode!r}")
    if not t.replays:                errs.append("no fresh replay ran (fresh replay is the sole executor)")
    if t.stop_reason in ("done", "planner_done", "done_flag"):
        last = t.last_replay
        if last is None or not last.ran:
            errs.append("done reached without a fresh replay")
        elif last.setup_rc != 0:
            errs.append(f"done reached but latest fresh replay failed: {last.failing_command}")
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
| `test_manual_block_artifact_preserved` | force an LLM `ScriptPatch` | `manual_block_ids` non-empty; block present in the per-cycle replay script AND in the final `render_build_script(graph, manual_blocks)` |

- [ ] Write each scenario as a failing test, run against the (now real) canonical loop, commit per scenario.

### Task 8d: Proof harness + report table

`scripts/run_v3_proof.py` runs the real-container e2e over the benchmark set, writes one JSON trace per repo, and emits:

```text
repo | result | legacy_used | graph_nodes_added | patchgate_accepts | manual_blocks | fresh_replay | tests_pass | residual_reason
```

plus aggregate: `canonical_loop_runs`, `legacy_path_violations` (MUST be 0), `fresh_replay_pass_rate`, `manual_block_artifact_mismatches` (MUST be 0), `local_import_false_package_attempts` (MUST be 0). The **composite success** predicate (strongest proof):

```python
def canonical_success(trace, script_text) -> bool:
    last = trace.last_replay
    return (trace.stop_reason in ("done", "planner_done", "done_flag")
        and last and last.setup_rc == 0
        and last.test_rc == 0
        and not verify_canonical_trace(trace)                       # no legacy/ablation path
        and not verify_artifact_consistency(script_text, trace.manual_block_ids)  # artifact complete
        and not last.unsatisfied_node_ids)                          # host certifiers all satisfied
```

- [ ] Wire `scripts/run_v3_e2e.py` to build a `RunTracer`, pass it, and on exit write `trace.to_dict()` to `--trace-out` and print `verify_canonical_trace`/`verify_artifact_consistency` results. Commit. (`run_v3_proof.py` itself is a thin loop over `run_v3_e2e`; no unit test — it's the reporting driver.)

---

## Phase 9: Quarantine ablation/legacy paths into named entrypoints

Only after Phases 1–8 are green and a benchmark sanity run shows `legacy_path_violations: 0` and `used_block_emit == False` across method runs.

- [ ] Remove the `enable_script_materialization` and `enable_binding_install` parameters from `run_v3` entirely (fresh replay is the only path); delete the interim `ValueError` guards from Phase 4.
- [ ] Expose incremental `block_emit` as an explicitly named fast **ablation** entrypoint — e.g. `run_v3_block_emit_ablation(...)` (or a thin `variant=` wrapper), NOT a hidden flag inside `run_v3`. It calls `tracer.mark_block_emit()` so ablation runs are self-identifying and can never be confused with the method.
- [ ] Keep `emit_drain`/`repair_failed_nodes` for `run_v1` only (already in `depgraph_live.py`); ensure no `run_v3`-adjacent code imports them.
- [ ] Rename per the concept map: `v1_emit_drain_baseline`, `react_build_agent_baseline`, `block_emit_ablation`; the canonical fresh-replay path is just `run_v3`.
- [ ] Commit. `git commit -m "refactor: quarantine block_emit/emit_drain/react as named ablations; run_v3 is fresh-replay single-path"`

---

## Future Work (recorded, NOT in scope of this plan): cached `docker build` executor

**Decision (2026-07-01):** do **A** now (`reset_to_base + run_install_script`); save **B** for later. The reset+bash executor gets zero build-cache benefit, so every repair attempt reinstalls apt+pip from base — slow. Escape hatch, to be scoped as its own plan if benchmark throughput bites:

- Render the graph+manual-blocks as a **Dockerfile** (layer boundaries at wave granularity) and execute via `docker build` instead of `run_install_script(bash)`. Docker's layer cache is provably equivalent to re-running from scratch, so the **invariant is preserved** (`env = f(base, graph, blocks)`, still from-scratch-certified) while a repair attempt that only changes a late line re-runs only that line onward — near-incremental cost.
- Host certification stays honest: `docker build` (cached) → `docker run` the image → exec host certifiers + gate in the fresh container.
- This is the repo2docker / Repo2Run pattern. It swaps only the executor mechanism (the `_binding_emit` body) — no change to the diagnosis router, PatchGate, repair loop, or trace layer. `block_emit` could then be retired entirely (its only advantage was speed).

---

## Tests Known To Need Changes (grounding-verified)

- `tests/test_v3_block_emit_wiring.py` — replace `test_toggle_off_uses_emit_drain_and_repair` (`:88`) → `test_toggle_off_now_raises`.
- `tests/test_graph_scheduler_wiring.py` — **split** `test_drain_runs_under_flag_as_prefix` (`:170-215`, ONE function): drop v3 half (`:189-200`), keep v1 half (`:202-215`) as `test_v1_drain_runs_as_prefix`.
- `tests/test_run_v1_turn_budget.py` — invert the source-level `emit_drain`-in-`run_v3` assertion (now must be ABSENT).
- `tests/envstate/test_v3_task_branch.py` — replace `test_discover_task_uses_run` (`:236`) → gate/evidence; `test_b3_ablation_does_not_use_propose` (`:244`) → deprecated-flag-raises; `test_obligation_task_without_exec_readonly_falls_to_freetext` (`:257`) → clean give-up.
- `tests/depgraph/test_patch_gate_validate.py` — add the four script-patch validation cases (Phase 1).
- `tests/depgraph/test_build_script.py` — replace `test_block_with_unknown_wave_lands_in_catch_all` (`:185`) → `test_block_with_unknown_wave_raises`.
- `tests/envstate/test_gates.py` — add binding-replay gate cases (Phase 7).

New test files: `test_manual_blocks_persist.py` (done), `test_diagnose_*` (done, companion), `test_v3_replay_executor.py` (Phase 4), `test_repair_routing.py` (Phase 6), `test_run_trace.py` + `test_trace_verify.py` (Phase 8), `scenarios/*` (Phase 8).

---

## Self-Review

**Spec/review coverage:** single-loop claim → Canonical Model + Phase 7 (arch MAJOR 1). Diagnosis-router ordering/scope → Phase 3 (companion Phase 1) + Phase 6 (companion Phase 2, all repair sites) (arch MAJOR 2/3, exec BLOCKER 3). `known_invalid` key-space conflict → Phase 3 reconciliation #3 (arch MAJOR 4). `manual_blocks` persistence signature → Phase 2 decided (arch MEDIUM 7, exec BLOCKER 2). Phase renumbering → the Phase↔Order map + Phase 9 last (exec BLOCKER 1). Silent-no-op flag → Phase 4 raises then Phase 9 removes (arch MEDIUM 5). Provisional→binding gate → Phase 7 required (arch MEDIUM 6, grounding nit). Phase 4/5 combined branch → Phase 5 literal 3-way (exec BLOCKER 4). Prose→pytest → every phase has real test code. E2e proof → Phase 8 (user request, full spec).

**Placeholder scan:** signatures given for every new symbol (`WorldModelMap.manual_blocks`, `diagnose_all`, `_repair_or_route`, hoisted `_binding_emit`, `RunTracer`/`RunTrace`/`FreshReplayRecord`, `verify_*`, per-cycle replay records). Two spots intentionally reference existing code to copy rather than re-derive: the typed-repair block body (Phase 5a, `orchestrator.py:760-791` unchanged) and companion Tasks 1–3 (DRY — do not duplicate). `EvidenceBundle`'s command-record field names (`.cmd`/`.output`) must be confirmed on first touch in Phase 6 (noted inline).

**Type consistency:** `Block` fields (`block_id/wave/commands/target_node_ids/provider_ids/check_commands/evidence_refs`) match `patch_gate._script_patch_to_block`. `Discovery | None` is the ingest seam type throughout. `GateResult` gains a `replay=` param, not a new field. `make_action_event` keyword signature matches `ledger.py:24-49`. `RunTracer` mirrors `ActionLedger`'s append-only shape.

**Canonical Model — SETTLED (Model B, user-chosen 2026-07-01):** fresh full-script replay is the SOLE executor; `block_emit` is a named ablation; there is NO separate terminal replay (the per-cycle replay is the proof); installability is binding by construction. Executor mechanism = `reset_to_base + run_install_script` (option A); cached `docker build` (option B) is recorded as Future Work, not built. Phases 4–9 reflect Model B; Phases 1–3 were executor-independent and are already done.

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per phase, two-stage review between phases. Best for a 9-phase refactor touching the core loop.
2. **Inline Execution** — batch with checkpoints in this session.

Recommended split point: land **Phases 1–3** first (independent, low-risk, each green on its own), checkpoint, then **4–7** (the loop restructure) as one reviewed block, then **8–9** (proof + quarantine).
