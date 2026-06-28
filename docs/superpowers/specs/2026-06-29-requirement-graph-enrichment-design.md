# Requirement-Graph Enrichment (Phase 1) — Design

**Date:** 2026-06-29
**Branch:** `john-planner-v3`
**Status:** approved (brainstorming, 2026-06-29)
**Lineage:** graph-governed/script-materialized agent → two-plane model (Requirement plane / Reasoning plane / Evidence ledger) → **this = Phase 1 of the Requirement plane: enrich what each node exposes + deliver it to the agent as a structured slice.**

**One line.** Today the v3 agent reasons over a flat string-fact packet built from one node; this enriches that into a derived, structured `RequirementSlice` (providers, structural context, layer cohort, active gate, platform + evidence as text) so the LLM reasons over a real model — **without** adding any node type, edge, or persisted reasoning plane.

## 1. Context and why

The audit + real-repo runs established that the v3 requirement graph is **well-built but thin in what it exposes to the agent**. `frame_obligation` (`schedule.py:86`) → `packet_to_task` (`graph_scheduler.py:20`) → `Task` → `build_agent._build_task_message` (`build_agent.py:953`) hands the LLM only: goal, check, layer, and a flat `facts` list (`evidence` inline string + `depends_on` ids + a flat dump of **all** satisfied node ids). The graph already *knows* far more — providers, the chain-to-goal, what's been tried and failed, the layer cohort — but `packet_to_task` drops it (`blocks` is computed at `schedule.py:91` then discarded) or never reads it (the scheduler has **zero provider visibility**; `chosen_fix`/`fix_candidates` are never passed).

This is the cheap, high-leverage half of escaping ReAct: give the LLM the structure the graph already holds. It is also the seam the Slice-B typed-patch loop will consume.

**Per the 2026-06-29 decision, this is sequenced.** Phase 1 (here) enriches what nodes *expose* via pure read-time derivation + a structured slice. Phase 2 (later, demand-pulled) promotes Provider/Gate/ActionBlock to real node types if metrics or the B5 ablation require it.

## 2. Scope

**In scope (the approved A/B/C/E-full, D/F-surfaced refinement):**
- **A — structured providers** (candidates / chosen / tried-and-failed), derived at read-time.
- **B — structural context** (deps-with-states, chain-to-goal / unblocks, layer cohort, conflict).
- **C — active gate** (synthesized from the single `TEST` goal; no gate node).
- **D — platform context as TEXT** (the chosen interpreter + `resolved_python`); **not** a `constrains` edge.
- **E — the typed `RequirementSlice`** + its delivery seam.
- **F — evidence as TEXT** (the best evidence line); **not** a graph-wide evidence-id scheme.

**Out of scope (deferred, with reason):**
- No new node types (Provider, Gate, ActionBlock, BuildArtifact) and no new edges (`provides`, `targets`, `blocked_by`, `constrains`) — **Phase 2**, demand-pulled.
- No `constrains` edge / Platform-node materialization — the `PLATFORM` type is uninhabited and would need `platform_id()` + an `EDGE_RULES` change; **deferred to the dedicated platform work.**
- No graph-wide evidence-id (`ev.N.M`) scheme — the build pipeline writes raw-stderr `node.evidence` with no id, and the only consumer of `evidence_ref` (`validate_proposal`) is **production-dead**; **deferred to Slice B** (where PatchGate is wired).
- No reasoning/causal plane (Hypothesis/ErrorClass nodes, `caused_by`/`invalidated_by` edges) — **Slice B+ / Phase 5**.
- No change to `run_v1`, the free-text BuildAgent's behaviour, certification, or the done-gate.

## 3. Design

### 3.1 The deliverable: `RequirementSlice` (typed)

A frozen dataclass built by a pure function from `(graph, node)`. It is the structured view of the obligation the agent is being asked to satisfy. New module `src/python_deps/depgraph/req_slice.py`:

```python
@dataclass(frozen=True)
class ProviderView:
    candidates: tuple[ProviderCand, ...]     # known ways to satisfy this node
    chosen: str | None                       # node.chosen_fix
    tried_failed: tuple[TriedProvider, ...]   # derived from failed node.attempts

@dataclass(frozen=True)
class ProviderCand:
    id: str            # e.g. "apt:libplacebo-dev" / "pip:lxml"
    action_class: str  # "apt" | "pip" | "npm" | "shell" (action_class.py) | "" if undeterminable

@dataclass(frozen=True)
class TriedProvider:
    command: str       # the attempt command that failed (the agent's "don't repeat this")
    outcome: str       # "failed"
    provider_id: str | None   # best-effort reverse-parse of command -> provider id

@dataclass(frozen=True)
class DepView:
    id: str
    state: str         # "satisfied" | "missing" | "unknown"

@dataclass(frozen=True)
class RequirementSlice:
    # the target obligation
    node_id: str
    kind: str          # node.type.value
    layer: str
    state: str
    check: str         # node.check_command
    evidence: str      # F-as-text: best evidence line (advise._best_evidence_line)
    # B — structural context
    deps: tuple[DepView, ...]          # requires_of(node), each with its state
    chain_to_goal: str                 # advise._chain_to_goal (why this matters)
    unblocks: tuple[str, ...]          # reverse-REQUIRES (the dropped `blocks`)
    layer_cohort_satisfied: tuple[str, ...]
    layer_cohort_missing: tuple[str, ...]
    conflict: str | None               # advise._conflict_note
    # A — providers
    providers: ProviderView
    # C — active gate (synthesized)
    active_gate: str                   # the TEST goal's check (or VERIFY_TEST_CMD)
    # D — platform as text
    platform: str | None               # "resolved for: <resolved_python> / <resolved_platform>"
```

### 3.2 Construction: `build_requirement_slice(graph, node) -> RequirementSlice`

Pure, in `req_slice.py`. Every field is **derived from data already on the graph** — reusing existing helpers; no construction-site changes:

| Field | Source / reuse |
|---|---|
| node_id/kind/layer/state/check | the `Node` |
| evidence | `advise._best_evidence_line(node.evidence)` (`advise.py:50`) |
| deps | `graph.requires_of(node.id)` (`schema.py:296`) → `DepView(n.id, n.state.value)` |
| chain_to_goal | `advise._chain_to_goal(graph, node)` (`advise.py:192`) |
| unblocks | reverse REQUIRES — `graph.required_by(node.id)` (`schema.py:306`) (recovers the `blocks` dropped at `packet_to_task`) |
| layer_cohort_* | filter `graph.nodes` by `n.layer == node.layer`, split by `state` |
| conflict | `advise._conflict_note(graph, node)` (`advise.py:213`) |
| providers | `providers_view(node)` (§3.3) |
| active_gate | `next((n for n in graph.nodes if n.type is NodeType.TEST), None).check_command` (fallback `VERIFY_TEST_CMD`) |
| platform | `advise._platform_note(node)` (`advise.py:225`) when `node.resolved_python` is set |

### 3.3 Provider derivation: `providers_view(node) -> ProviderView`

Pure, read-time (NOT stored — `node.data` is a `MappingProxyType` set at `__post_init__`; all inputs are already on the node):

- **candidates**: `node.fix_candidates` ∪ (`node.chosen_fix` if set). For each id, `action_class` = prefix map (`"apt:"→"apt"`, `"pip:"→"pip"`, else best-effort via `action_class.matches_action_class` / `""`). Provider ids set at `resolve_lock.py:278` (`pip:`), `seed.py:71` / `probe.py:334,356` / `ldd_probe.py:228` (`apt:`), `config_scan.py:278` (`env:`), `service_scan.py:236` (`service:`).
- **chosen**: `node.chosen_fix`.
- **tried_failed**: `node.attempts` (`schema.py:102` — `Attempt{command, outcome, check, cycle}`) filtered to `outcome == "failed"`. Each → `TriedProvider(command=a.command, outcome="failed", provider_id=_reverse_parse(a.command))`. `_reverse_parse` is best-effort (`apt-get install … X` → `apt:X`, `pip install X==v` → `pip:X`); `None` when the command is a batch/unparseable. This is the cross-attempt "don't retry the broken provider" signal — the single thing that most distinguishes graph-memory from ReAct — and it is FREE: the data is already on `node.attempts` (the emit path records failed attempts at `depgraph_live.py:148`, `probe.py`, `ldd_probe.py`).

`platform_ok` from the original sketch is **dropped** — per-provider platform validity is unmodeled (would need `apt-cache`/distro lookup); it lands with the Phase-2 platform work.

### 3.4 Delivery: the seam (E)

Minimal blast radius — `Task` and `build_agent.py` are **unchanged**:

1. `ObligationPacket` (`schedule.py:69`, frozen) gains `requirement_slice: RequirementSlice | None = None` (defaulted → all existing construction safe).
2. `frame_obligation` (`schedule.py:86`) calls `build_requirement_slice(graph, node)` and sets it on the packet. This is the natural home (it already has `graph` + `node` and already computes `blocks`/`certified_context`).
3. `packet_to_task` (`graph_scheduler.py:20`) renders the slice via `render_requirement_slice(slice) -> tuple[str]` and uses those lines as `Task.facts`, **replacing** the now-redundant flat `evidence`/`depends_on`/`certified_context` facts. The service-recipe facts (`start_recipe`/`bind_recipe`) and the `_discover_task` path (`facts=()`) are unchanged.
4. `build_agent._build_task_message` is **unchanged** — it renders `Task.facts` as bullets exactly as today; the bullets are now the rendered slice.

The **typed** `RequirementSlice` lives on `ObligationPacket` for Slice B to consume (or Slice B re-derives via the same pure `build_requirement_slice`); Phase 1's free-text agent consumes only the rendered text.

### 3.5 Rendering: `render_requirement_slice(slice) -> tuple[str]`

Pure, in `req_slice.py`. Produces compact, agent-readable fact lines, e.g.:

```
target: req pkgconfig:libplacebo  (SystemLib, system, MISSING)
why:    libplacebo <- app <- repo_tests_pass    [active gate: python -m pytest -q]
check:  pkg-config --exists libplacebo
deps:   pkg-config=SATISFIED
providers: candidates=[apt:libplacebo-dev]  chosen=apt:libplacebo-dev
          tried & FAILED: apt-get install -y libplacebodev  (=> avoid apt:libplacebodev)
layer (system): satisfied=[pkg-config, libavcodec]  missing=[libplacebo]
platform: resolved for: 3.10 / manylinux_2_17_x86_64
evidence: Dependency "libplacebo" not found, tried pkgconfig
```

## 4. Backward compatibility

- `run_v1` and v1's planner path: untouched (they don't call `frame_obligation`/`next_decision`).
- The free-text BuildAgent: behaviour unchanged — it still receives `Task.facts` bullets; only the *content* is richer.
- `_discover_task` (no target node): `requirement_slice=None`, `facts=()` → renders nothing new.
- Frozen-dataclass safety: `ObligationPacket` gains one defaulted field; tests that construct it by keyword/positional-prefix are unaffected (`test_obligation_framing.py`, `test_graph_scheduler_decision.py`, `test_build_agent_task_message.py`).

## 5. Testing (TDD)

- `req_slice` unit tests: `providers_view` (candidates incl chosen; action_class prefix mapping; tried_failed derived from failed attempts; reverse-parse apt/pip; batch command → `provider_id=None`); `build_requirement_slice` (deps carry states; `unblocks` recovers reverse-REQUIRES; layer cohort split; conflict surfaced; active_gate from TEST node; platform from resolved_python); `render_requirement_slice` (stable, contains the tried-failed avoidance line, no crash on empty/None fields).
- `frame_obligation`: the packet now carries a populated `requirement_slice` for a frontier node.
- `packet_to_task`: `Task.facts` is the rendered slice (+ retained service-recipe facts); the discover-task path stays `facts=()`.
- Regression: full suite green except the 4 known pre-existing failures; v1 + the v3 wiring tests unchanged.

## 6. File structure / integration points (grounded 2026-06-29)

```text
src/python_deps/depgraph/req_slice.py    NEW — ProviderView/ProviderCand/TriedProvider/DepView/
                                          RequirementSlice + providers_view + build_requirement_slice
                                          + render_requirement_slice  (pure; imports schema, advise, emit, action_class)
src/python_deps/depgraph/schedule.py     ObligationPacket (:69) += requirement_slice;
                                          frame_obligation (:86) populates it (blocks already computed here :91)
src/envstate/graph_scheduler.py          packet_to_task (:20) renders slice -> Task.facts (replaces flat facts;
                                          keeps start_recipe/bind_recipe facts; _discover_task unchanged)
# UNCHANGED: world_model.Task, build_agent._build_task_message, certify, run_v1, the done-gate
```

Reuse (do not reimplement): `advise.{_chain_to_goal,_conflict_note,_best_evidence_line,_platform_note}`,
`emit.partition`, `schema.DepGraph.{requires_of,required_by,get}`, `action_class.matches_action_class`,
`Node.attempts` + `emit.EMIT_ATTEMPT_TAG`.

## 7. Risks

1. **Redundancy/noise in the prompt.** The slice is richer than the old flat facts; if verbose it could bury signal. Mitigation: `render_requirement_slice` is compact and omits empty sections; layer_cohort replaces the flat dump of *all* satisfied ids (net reduction).
2. **Reverse-parse provider mapping is fuzzy.** Batch/probe attempt commands cover many packages. Mitigation: `provider_id` is best-effort/`None`; the always-correct signal is the failed *command* itself (`tried_failed.command`), which is what the agent needs regardless.
3. **`_chain_to_goal`/helpers are `advise.py` internals (underscore).** Importing them couples `req_slice` to `advise`. Acceptable (same package); if they churn, the slice degrades gracefully (None/empty), never crashes.
4. **Coupling `graph_scheduler` to a depgraph type.** `ObligationPacket` already lives in depgraph and `graph_scheduler` already imports it; adding the slice type is within the existing dependency direction (envstate → depgraph).

## 8. Decisions log

- Sequenced two-plane build; this is **Phase 1 of the Requirement plane** (user, 2026-06-29).
- **A/B/C/E full; D/F surfaced-as-text** — defer the `constrains` edge (D-structural) and the evidence-id scheme (F-structural) because their consumers (Platform node, wired PatchGate) don't exist yet (user, 2026-06-29).
- `RequirementSlice` is **derived at read-time**, not stored on `node.data` (`node.data` is `MappingProxyType`; all inputs already on the node).
- Delivery via `ObligationPacket.requirement_slice` + render-into-`Task.facts`; `Task`/`build_agent` unchanged (lowest blast radius); the typed object is the Slice-B seam.
- `platform_ok` dropped (unmodeled); `invalidated` realised as `tried_failed` from `node.attempts` (no reasoning plane).
