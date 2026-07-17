# Phase 0 — Dep Graph as Advisory Slice in the Planner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing, real-repo-verified `src/python_deps/depgraph/` engine into the v1/v1g agent so the **planner LLM receives a host-certified dependency-graph advisory section** in its prompt each cycle. **Advisory only** — the depgraph informs the planner; it changes nothing about how the agent decides, acts, certifies done, or synthesizes the Dockerfile. The single behavioral change is one new, clearly-labeled section in the planner prompt. Default off, behind a flag, with graceful degradation (a failed build never breaks a run).

This realizes the "shadow" stage of the shadow → advisory → emitter rollout in `docs/DESIGN-tiered-layer-execution-strategy.md` §7 and the "graph = state machine, not the agent" framing: the graph holds the certified state; the planner reads a curated projection of it.

> **STATUS (2026-06-23): Tasks 1–5 LANDED + verified (uncommitted).** 389 tests pass;
> end-to-end proven on a real repo in real Docker (`scratchpad/integration_phase0.py`:
> real build → `map.dep_advisory` → planner prompt). Code-reviewed (0 CRITICAL/0 HIGH;
> MEDIUM-1 clobber + LOW-1/2 applied). **Task 6 (flag-on vs flag-off A/B) PENDING** — needs
> a benchmark run on a VM (`--arm v1gd` vs `--arm v1g`), scored with `compute_essr`.

**Evidence it's ready:** the engine is proven end-to-end in real Docker (`scratchpad/e2e_phase0.py`, 5/5 probing checks): `opencv-python` certified `satisfied` while `cv2` import certified `missing` (installed ≠ importable), `libgl1`/`libglib2.0-0` discovered by probe with apt fixes, `psycopg2` build-time gap → `libpq-dev` Tool. Artifacts: `docs/verify-e2e_cv2.graphml`, `docs/verify-e2e_psycopg2.graphml`. The planner-facing render is prototyped (`render_dep_graph_advisory` in that script) and its output is the spec for Task 1.

---

## Scope

**IN:**
- A render module that turns a `DepGraph` into the planner-facing advisory string (frontier detail + satisfied summary).
- A graceful, gated **build-once-at-start** step that runs the engine in a **separate scratch container** (the existing `DockerExecutor` on the agent's base image) + host `uv`, producing the advisory string and a GraphML artifact.
- Carrying that string on `WorldModelMap` and splicing it into the planner prompt.
- A `--enable-dep-graph` flag + `DOCKERAGENT_ENABLE_DEP_GRAPH` env + runner/arm plumbing, default off.
- A flag-on vs flag-off A/B on a sample, scored with the honest scorer.

**OUT (explicit non-goals, deferred to Phase 1/2):**
- Any change to loop control. The planner still drives every action (`planner.decide` → `PlannerDecision`); no host `next_action`, no layer-ordered planner, no layer→actuator dispatch.
- Any change to the done-gate (`_verified_test_run_passed` + `goal_ready` stay as-is) or the synthesizer (`build_commands_from_ledger` stays as-is). No `emit_setup`.
- The **projection bridge** (depgraph → `ContractGraph` `GraphPatch`). Phase 0 renders a *separate* advisory section beside the contract-graph section; it does **not** patch the contract graph. The depgraph never writes anywhere.
- **Per-cycle re-probe / update.** The advisory is built **once** at start and is static for the run. (Re-certification per cycle is Phase 1.)
- **Remediation.** The advisory predicts fixes (`apt:libgl1`); it does not apply-and-re-probe to prove them.
- The `SandboxExecutorAdapter` over the live container — not needed here, because Phase 0 probes in its own throwaway `DockerExecutor` container, leaving the agent's live `Sandbox` byte-for-byte untouched (keeps the A/B honest: we measure "did the advice help," not "did pre-installing deps help").

**Forbidden files (do not modify):** `models.py`, `graph.py`, `external_graph/*`, `resolver.py`, `z3_adapter.py`, `pypi_metadata.py`. None of this plan touches them.

---

## Architecture

```
_run_v1 (agent.py)
  ├─ (flag on) build_advisory_for_repo(repo_path, base_image, host)         ← NEW, once, before loop
  │     └─ DockerExecutor(base_image) scratch container  +  host uv
  │           └─ build_dep_graph(...)  →  DepGraph  →  render_dep_graph_advisory()  →  str
  │     └─ on ANY exception: log, return ("", None)   ← graceful degradation
  ├─ initial_map(..., dep_advisory=<str>)                                   ← carried on the map
  └─ run_v1 loop  (UNCHANGED)
        └─ planner.decide(map)
              └─ render_planning_view(map, budget)
                    ├─ render_graph_for_planner(contract_graph, ...)        ← unchanged
                    └─ + map.dep_advisory   (new labeled section, if non-empty)   ← NEW splice
```

Two graphs are shown to the planner side by side: the reactive contract graph (today) and the proactive dependency graph (new, advisory). They do not interact in Phase 0.

**Tech stack:** existing engine (`python_deps.depgraph`), `DockerExecutor` (already shipped), host `uv 0.10.4`. Tests: `.venv/bin/python -m pytest tests/depgraph/ tests/envstate/ -q`. Conventional commits; suite green at the end of each task.

**Testability:** the render module is pure (`DepGraph` in → `str` out) and unit-tested with a hand-built graph — no Docker. The build step is wrapped so unit tests of `_run_v1` paths never spawn a container; exactly one `@pytest.mark.docker` integration test exercises the real build (reuse `scratchpad/e2e_phase0.py`).

---

## Shared Interfaces (keystone)

### `src/python_deps/depgraph/advise.py` — NEW
```python
def render_dep_graph_advisory(graph: DepGraph) -> str:
    """Planner-facing advisory render: GOAL/PROJECT header, a FRONTIER section
    (one block per MISSING non-Test node: layer, type, raw evidence line,
    'needed by' from requires edges, fix-candidate, recent attempts), and a
    one-line SATISFIED summary (counts per layer, NOT one line per node).
    Returns '' for an all-unknown/empty graph."""

def build_advisory_for_repo(
    repo_path: str,
    base_image: str,
    *,
    host_executor: Executor | None = None,
    timeout_s: int = 1200,
) -> tuple[str, "DepGraph | None"]:
    """Build the dep graph in a fresh scratch DockerExecutor(base_image) +
    host uv, render the advisory, and return (advisory_str, graph). On ANY
    failure (Docker down, resolve error, timeout) log and return ('', None) —
    the caller must proceed exactly as if the feature were off."""
```

### `src/envstate/world_model.py` — EDIT
```python
@dataclass(frozen=True)
class WorldModelMap:
    ...
    dep_advisory: str = ""   # NEW: static Phase-0 advisory; '' when feature off/failed
```
Thread through `initial_map(...)`, `merge_map(...)`, `map_to_dict` / `map_from_dict` (plain string — trivial round-trip).

### `agent.py` — EDIT
- argparse: `--enable-dep-graph` (implies v1, like `--enable-contract-graph`).
- `DockerAgent.__init__`: accept `enable_dep_graph: bool = False`.
- `_run_v1`: build advisory (gated) before the loop; pass `dep_advisory=` into `initial_map`; export `<workplace>/dep_graph.graphml` artifact when a graph was produced.

---

## Tasks

### Task 1 — Advisory render module (`advise.py`) + unit tests
- [ ] Move + harden `render_dep_graph_advisory` from `scratchpad/e2e_phase0.py` into `src/python_deps/depgraph/advise.py`.
- [ ] **Render polish (from the real e2e output):** (a) evidence line = the last meaningful stderr line (the `ImportError: …` / `Error:` line), **not** the traceback header `Traceback (most recent call last):`; (b) suppress native-risk **self-edges** so a package isn't listed as "needed by" itself.
- [ ] Add `build_advisory_for_repo(...)` wrapping `build_dep_graph` + render in try/except → `('', None)` on failure.
- [ ] `tests/depgraph/test_advise.py`: hand-build a `DepGraph` (Test/Project/Package satisfied + SystemLib missing w/ fix + Import missing); assert the FRONTIER block contains name/layer/evidence/needed-by/fix; assert SATISFIED summary is counts-per-layer not per-node; assert empty graph → `''`. No Docker.
- **Acceptance:** `pytest tests/depgraph/test_advise.py -q` green; render of the saved `verify-e2e_cv2.graphml` matches the prototype's two-tier shape with polished evidence.

### Task 2 — `WorldModelMap.dep_advisory` carrier + serialization
- [ ] Add `dep_advisory: str = ""` to `WorldModelMap`; thread `initial_map`, `merge_map`, `map_to_dict`, `map_from_dict`.
- [ ] `tests/envstate/`: round-trip a map with `dep_advisory` set; assert default `""` and back-compat (old dicts without the key load fine).
- **Acceptance:** envstate suite green; serialization round-trips the field.

### Task 3 — Splice advisory into the planner prompt
- [ ] In `render_planning_view` (`planner.py`), append `world_map.dep_advisory` as a clearly-labeled section **only when non-empty**, placed after the contract-graph section.
- [ ] Test: a map with a non-empty `dep_advisory` produces a rendered view containing the advisory header + content; empty `dep_advisory` adds nothing (byte-identical to today).
- **Acceptance:** planner-render test green; off-state output unchanged.

### Task 4 — Flag + gated build-once hook in `_run_v1`
- [ ] argparse `--enable-dep-graph` + `DOCKERAGENT_ENABLE_DEP_GRAPH` env; `DockerAgent.__init__(enable_dep_graph=...)`; mutual-exclusion/`implies v1` guard mirroring `--enable-contract-graph`.
- [ ] In `_run_v1`, when enabled: call `build_advisory_for_repo(repo_path, self.base_image)` once before the loop; set `dep_advisory=` on the initial map; on a produced graph, write `<workplace>/dep_graph.graphml`. Wrap so any exception → advisory `""`, run proceeds.
- [ ] Log a one-line summary (`dep-graph advisory: N frontier / M satisfied` or `dep-graph advisory: unavailable (<reason>)`).
- **Acceptance:** with flag off, `_run_v1` path is unchanged (no container spawned); with flag on against a real repo, the planner prompt contains the advisory and `dep_graph.graphml` is written. Graceful-degradation unit test: a `build_advisory_for_repo` that raises leaves the run identical to flag-off.

### Task 5 — Runner / arm plumbing (default off)
- [ ] Forward `DOCKERAGENT_ENABLE_DEP_GRAPH` in `run_rat_benchmark.py`, `run_repo2run_benchmark.py`, `multi_docker_eval_adapter.py` (mirror the contract-graph env plumbing).
- [ ] Add an arm preset that enables it on top of v1g (e.g. `v1gd`) so the A/B is one `--arm` flip; keep `v1g` and defaults unchanged.
- **Acceptance:** `--arm v1gd` sets the env and reaches the `DockerAgent` constructor; existing arms byte-identical.

### Task 6 — Flag-on vs flag-off A/B (measurement, the point of Phase 0)
- [ ] Run v1g vs v1gd on a sample (subset of the 50-set first), score with `compute_essr.score_agent` (honest: ESSR÷all + full-pass + hollow count). Never trust `rat_results.json`.
- [ ] Report: convergence cycles, wasted-actuator loops (pip-for-a-syslib), system-dep discovery rate, and net ESSR delta. Produce raw artifacts (per [[analysis-via-subagents]]) — no inline analysis.
- **Acceptance:** a side-by-side scorecard answering "does the certified advisory improve planner choices?" → decide go/no-go on Phase 1.

---

## Risks & mitigations
- **Extra start-up latency / second container.** ~50 s for a cv2-sized closure. Gated by flag; `timeout_s` cap; failure degrades to off. (Optional 0a dial: host-only scan+resolve, no scratch container — predicted-not-probed states — if latency matters.)
- **Advisory drifts from reality** (static, built once; the agent then mutates its own live container). Acceptable for shadow/advisory; explicitly labeled "host-certified in scratch container" so the planner treats it as a starting map, and Phase 1 adds per-cycle refresh.
- **Prompt bloat.** The render is relevance-gated (frontier detail + satisfied counts); respects the planner's existing `budget`.
- **A/B confound.** Probing in a *separate* container (not the live `Sandbox`) is mandatory — otherwise we'd measure pre-installed deps, not advice quality.

## One-line summary
Build the proven depgraph once in a scratch container at agent start, render a host-certified frontier+summary advisory, carry it on the map, and splice it into the planner prompt — behind an off-by-default flag, changing nothing else — then A/B whether the certified view improves the planner.
