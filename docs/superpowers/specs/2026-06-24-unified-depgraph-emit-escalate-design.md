# Unified Dependency Graph — Emit the Certified Closure, Escalate the Frontier

**Date:** 2026-06-24
**Status:** Design (awaiting review)
**Branch:** john-planner-v3
**Supersedes the integration stance of:** `docs/DESIGN-graph-utilization-architecture.md` (Certify-then-*Advise*). This document extends that principle from *advising* to *emitting*, and unifies the two graph surfaces.

---

## 1. Problem — why the depgraph isn't working

The dependency graph (`src/python_deps/depgraph/`) carries the richest data in the
system but reaches the agent through the weakest channel. The 2026-06-24 trajectory
analysis (`outputs/depgraph-trajectory-analysis-2026-06-24.md`) is a **negative
result for "the depgraph steers the agent"**: across 5 repos the advisory sat inert
while an LLM-authored recipe-patch loop did the work, and the agent repeatedly
ignored or overrode the graph (Argus ignored a printed `apt:build-essential`
fix-candidate for 11 cycles; bilingual finalized while the graph still read MISSING).

Four root causes, each grounded in the code:

1. **It never produces executable output.** Recipe `command` strings are authored
   entirely by the planner LLM (`src/envstate/planner.py:391`); `BuildAgent.run_recipe`
   (`src/envstate/build_agent.py:697`) consults neither graph. The depgraph's unique
   asset — a resolved, pinned, conflict-checked closure with `version`, `hash`,
   `build_from_source`, and `requires`/`conflicts_with` edges — is never turned into
   install commands. Working envs are rebuilt by LLM trial-and-error and **lost at
   synthesis** (the `successful_actions` saga).

2. **It is advisory and static.** Built once in a scratch container, rendered as
   read-only text (`render_dep_graph_advisory`, `src/python_deps/depgraph/advise.py:92`),
   never updated. The FRONTIER is byte-identical every cycle. An LLM is structurally
   free to ignore read-only text, and does. This is an **authority problem, not an
   information problem** — more formatting cannot fix it.

3. **Both interfaces are lossy and destroy the topology.** The advisory render shows
   only the MISSING frontier plus per-layer *counts*. The adapter
   `seed_contracts_from_depgraph` (`src/envstate/contracts/depgraph_seed.py:24-28`) is
   worse: it collapses 7 node types to 3 and **drops every edge and the `state`
   axis** — annihilating exactly the structure we want to topologically sort.

4. **Two competing graph surfaces dilute the signal.** The planner sees both a
   `## dependency_graph` advisory and the contract graph's
   `## Next Target / Repair Map / Repair Frontier` (`planner.py:301-311`). They
   overlap and disagree; the per-cycle contract graph won the few causal decisions
   and the static depgraph lost by default.

**Diagnosis:** the depgraph is not wrong, it is *disenfranchised* — richest data,
weakest channel (static, lossy, advisory, non-executable, and competing with a
livelier graph).

---

## 2. Thesis

The depgraph is **strictly richer** than the contract graph and can represent
everything the contract graph does (a MISSING node + `evidence` ≈ a Blocker;
`Node.attempts` ≈ Attempt; `Node.layer` ≈ layer; the MISSING frontier ≈
`open_problems`) **plus** what the contract graph fundamentally cannot: the resolved
closure, `requires`/`conflicts_with` edges, and therefore **install order**.

So unify *downward* onto the depgraph as the single substrate, and change its role
from *advising* to *driving*:

> **Emit the part the host has certified; escalate the part it cannot.**

- **Emit** — topologically sort the certified-closure subset and emit it as a
  deterministic, host-re-certified recipe. Removes the easy cases from the LLM
  entirely; makes the env reproducible by construction.
- **Escalate** — hand the genuinely-uncertain frontier to the LLM, but armed with a
  rich **diagnostic packet** (causal chain, conflict bounds, platform mismatch,
  cross-cycle attempt history, live state-deltas) the current renders throw away.

Two-pronged value: **emit removes the easy cases; the diagnostic packet sharpens the
LLM on the hard ones.**

---

## 3. Architecture — graph-first cycle

The orchestrator loop changes from *LLM decides → maybe consults graph* to
*graph emits what it is certain of → LLM handles only what is left*. Today the cycle
is `planner.decide` → dispatch → `run_recipe` → `apply_deterministic` +
`_host_refresh` → `maintainer.update` → done-gate (`orchestrator.py:107-247`). The
change inserts a deterministic **EMIT phase before `planner.decide`** and upgrades the
existing `_host_refresh` into the **CERTIFY** phase. Each cycle:

```
CERTIFY   _host_refresh, extended to re-run each depgraph node's check_command
          against the LIVE container → flip states          (host; orchestrator.py:93-101)

EMIT      drain loop (graph-first, NEW):
  repeat:
    emittable = partition(graph).emittable                  # pure classifier (§5)
    if not emittable: break
    recipe   = build_recipe(topo_order(emittable))          # pure → list[RecipeStep] (§6)
    report   = build_agent.run_recipe(recipe, ...)          # SAME call as orchestrator.py:180
    apply_deterministic + CERTIFY                           # re-certify what was emitted
  (bounded by a max-drain count)

ESCALATE  decision = planner.decide(current_map)            # orchestrator.py:109, unchanged call
          → sees mostly-satisfied graph + frontier(+diagnostics §8)
          → apply_recipe_patch flows through the EXISTING path (orchestrator.py:152-247)
```

- **CERTIFY** already exists conceptually as the contract graph's
  `refresh_host_graph` (`src/envstate/contracts/projection.py:115`) +
  `host_satisfied`; we re-point it at depgraph nodes. The `State` axis is already
  designed for exactly this — "only a host-run `check_command` flips this"
  (`schema.py:35-41`). This is what makes the graph **live**, fixing root cause #2.
- **EMIT** is new (Sections 5–7). Fixes root cause #1.
- **ESCALATE** is the existing planner, but it now wakes to a graph that already did
  the unambiguous work and presents one unified surface (Section 8).

**The drain loop is where topo-sort earns its keep across iterations.** Emitting
`libxml2-dev` certifies it, which moves `lxml` from FRONTIER (toolchain-not-ready)
into EMITTABLE, so the next drain pass emits `lxml`. The graph resolves the DAG
layer-by-layer deterministically until only genuinely-uncertain nodes remain — *then*
it wakes the planner. The planner is consulted on the residue, not the whole problem.

**Decision D1 (control inversion):** the orchestrator emits *before* the planner
each cycle (graph-first), rather than adding a planner action the LLM may decline to
call. Rationale: a planner-chosen emit reintroduces the very authority problem this
design exists to remove. *Recommended; folded in as the default.*

### 3.1 Role impact — interfaces unchanged; one new phase does the work

| Role | Changes? | Detail |
|---|---|---|
| **BuildAgent** | **Unchanged; gains a second caller.** | EMIT calls the *same* `build_agent.run_recipe` (`orchestrator.py:180`) with a graph-authored recipe instead of a planner-authored one. Its within-step local repair becomes an **extra safety layer** for emitted commands — a slightly-wrong apt name may be fixed in place; if not, the step fails and CERTIFY drops the node back to FRONTIER. Emit failures are caught twice. |
| **Maintainer** | **Unchanged for the MVP; role narrows over time.** | Still called after every recipe (`orchestrator.py:235`) and still feeds the host-owned done-gate (`maintainer._verified_test_run_passed`, `_gate_passed`). But the dep/system truth it used to *infer from report text* — unreliably; the source of the collect-only / auto-resolve scars — is now certified directly by CERTIFY running real `check_command`s. The `MAINTAINER_FORBIDDEN_FIELDS={status,outcome,active}` invariant gets *stronger*: graph state is set by a host check, not by folding LLM-proposed facts. A follow-up (with D3) can retire the parts the graph now owns. |
| **Planner** | **Same interface; biggest behavioral shift.** | Same `decide()` call (`orchestrator.py:109`), same action verbs (`task/giveup/done/apply_recipe_patch`), and its frontier recipes still flow through the existing `apply_recipe_patch` machinery. What changes: *what it sees* (unified render + diagnostic packet, §8) and *when* (after the graph has emitted), so it focuses on the frontier instead of re-deriving the whole env. |
| **Host / orchestrator** | **Where the new code lives.** | Extend `_host_refresh` to re-certify depgraph nodes (CERTIFY); add the EMIT drain phase before `planner.decide`. Everything downstream (`run_recipe`, `apply_deterministic`, `maintainer.update`, the done-gate) is reused as-is. |

---

## 4. The single substrate — retire the parallel contract-graph store

The contract graph stops being a parallel data model and becomes a **view** over
depgraph nodes. We keep the two things it actually contributes and drop the rest:

| Keep | Drop |
|---|---|
| Per-cycle host-certification cadence (`refresh_host_graph`) → re-point at depgraph nodes | The separate Contract/Blocker/Attempt node store |
| The render *shape* the planner is trained on (`render_graph_for_planner`) → render from depgraph | The lossy adapter `seed_contracts_from_depgraph` |

**Decision D3 (staged retirement):** `src/envstate/contracts/` is **not** deleted on
day one. `projection.refresh_host_graph` and `render.render_graph_for_planner` are
re-pointed at depgraph nodes behind the existing flags; once the unified path is
proven on a benchmark run, the dead Contract/Blocker store and the adapter are
removed in a follow-up. *Recommended; folded in as the default.*

---

## 5. The certify / emit / escalate partition

Each cycle every node falls into exactly one bucket, computed **deterministically
from fields that already exist** on `Node` (`schema.py:91-163`) — no new fields:

- **CERTIFIED** — `state == SATISFIED` (host check passed in the live container).
  Render as a count; do nothing.
- **EMITTABLE** — `state == MISSING` **and** the graph is confident:
  - in the resolved closure (`version` present, plus `artifact`/`hash` from uv resolve), and
  - install method unambiguous (`Package` → pip; `SystemLib`/`Tool` with exactly one `fix_candidate` → apt), and
  - not in a `conflicts_with` unsat-core pair, and
  - if `build_from_source`, its toolchain prerequisites are already CERTIFIED.
  → goes to EMIT.
- **FRONTIER** — `state == MISSING` and **not** confident: unresolved (no `version`),
  conflicting (`conflicts_with` edge), ambiguous/absent apt name, or
  `build_from_source` whose toolchain isn't yet certified.
  → goes to ESCALATE (the LLM), with the diagnostic packet (Section 8).

This is a pure classifier over the existing schema; it is unit-testable in isolation.

---

## 6. Topo-sort + emit

Topological sort over `requires` edges (`schema.py:257-275` already gives
`requires_of`/`required_by`), restricted to EMITTABLE, with the layer rank as a
tie-break (`interpreter → system → toolchain → pip → naming → runtime`) and
`conflicts_with` pairs never co-emitted.

**Refinement — what topo-order is actually for.** Order matters *across* layers, not
*within* the pip layer:

- *Across layers:* apt/`SystemLib` before the pip package that needs it; a
  toolchain (`build-essential`, `-dev` headers) before a `build_from_source` package.
  This is where the edges earn their keep.
- *Within pip:* uv already resolved the whole closure to a mutually-consistent pinned
  set. Installing it one-package-at-a-time is **less** reliable than handing pip the
  whole pinned set at once (pip sees all constraints together).

**Decision D2 (pip emit shape):** emit the pip layer as **one resolved, pinned
closure** (a constraints/requirements file installed in a single step), reserving
topo-order for cross-layer ordering and build-from-source prerequisites.
*Recommended; folded in as the default.*

Emitted recipe shape (illustrative):

```
RUN apt-get install -y libxml2-dev libpq-dev          # SystemLib/Tool, one layer, topo-before-pip
RUN pip install --no-deps -r /tmp/closure.txt          # full pinned closure, resolver-consistent
# a build_from_source pkg lands here only after its toolchain node is CERTIFIED
```

Each emitted `RecipeStep` carries `target_node_ids` (the existing recipe-grounding
field, `planner.py:388`) so outcome write-back knows which nodes it certifies.

---

## 7. Integration — reuse the existing recipe machinery

The emitted recipe is a `list[RecipeStep]` — the **same type the planner emits
today** — so it runs through the **existing** `build_agent.run_recipe`
(`build_agent.py:697`) and the **existing** outcome write-back in the orchestrator's
recipe handler (`src/envstate/orchestrator.py:152-247`). No new executor, no new
dispatch path.

New code is confined to three pure, independently-testable units plus the drain
driver:

1. `partition(graph) -> (certified, emittable, frontier)` — the Section 5 classifier.
2. `topo_order(graph, emittable) -> ordered nodes` — Section 6 sort.
3. `build_recipe(ordered nodes) -> list[RecipeStep]` — graph → recipe steps
   (apt step, pinned-closure step, per-build-from-source steps).
4. EMIT drain driver (in `orchestrator.py`) — loops 1→2→3→`run_recipe`→CERTIFY until
   `emittable` is empty, bounded by a max-drain count, then falls through to the
   planner.

The drain driver runs the result through the path that already exists; the agents'
interfaces do not change (Section 3.1).

**Decision D4 (emit execution path):** the emitted recipe runs *through*
`build_agent.run_recipe` (LLM within-step repair available), not a bare executor.
Rationale: reuse, and the repair is a free safety layer since failures self-escalate
via CERTIFY anyway. Cost: the certified command captured for synthesis may be the
*repaired* one — acceptable, we record what actually ran. *Recommended.*

**Decision D5 (drain vs one-batch):** EMIT drains the whole certifiable DAG (repeat
until `emittable` empty) before consulting the planner, rather than emitting one batch
per cycle. Rationale: resolving a toolchain node unlocks the build-from-source node
that depends on it within the *same* cycle (Section 3), so the planner is consulted
only on the true residue. Bounded by a max-drain count to stay safe. *Recommended.*

---

## 8. The unified planner surface + the frontier diagnostic packet

One section replaces both current ones (`planner.py:301-311`), killing root causes #3
and #4. It is **relevance-gated**: full detail only for FRONTIER nodes; everything
certified collapses to counts (the philosophy already in `advise.py`).

```
## environment_graph (unified · host-certified live · cycle N)
GOAL     repo_tests_pass            missing
CERTIFIED  pip 38 · system 2        (host-checked in live container)
EMITTED THIS CYCLE (graph, deterministic):
  apt: libxml2-dev, libpq-dev      → certified ✓
  pip: <38-pkg pinned closure>     → certified ✓
FRONTIER (graph could not auto-resolve — your call):
  PIP  lxml   build_from_source; libxml2-dev MISSING→SATISFIED this cycle → retry build, or pin manylinux wheel
       chain: lxml ← project ← repo_tests_pass
       attempts: pip install lxml → failed (c2); pip install lxml==4.9 → failed (c4)
  PKG  fastavro conflicts_with avro (uv unsat core: fastavro needs X>=2, avro needs X<2) → pick one
```

**Frontier diagnostic packet** (per FRONTIER node) — deepens the current "best
evidence line + needed-by + last-2-attempts" to:

1. **Causal chain** — transitive `required_by` walk to the GOAL (why this matters,
   what unblocks if fixed). Impossible in the flat contract graph (edges dropped).
2. **Conflict unsat core with version bounds** — from `conflicts_with` edges and
   `Edge.data` bounds; turns an opaque pip `ResolutionImpossible` into a one-liner.
3. **Platform/interpreter mismatch** — `resolved_python` / `resolved_platform`
   (`schema.py:113-114`) vs the live image; explains cryptic "no matching
   distribution" / unexpected build-from-source.
4. **Full cross-cycle attempt history** — `Node.attempts` (`schema.py:104`); directly
   attacks the thrashing failure mode (weibo-crawler cycles 4–6; Argus's 11 repeats).
5. **Live state-delta** — "MISSING→SATISFIED this cycle"; tells the LLM the world
   changed so a retry is now sensible.

**Why this dodges the authority problem.** On the frontier the LLM is *already* the
decision-maker (we escalated precisely because the graph could not auto-resolve), so
the graph is the LLM's **evidence base**, not a competing recommendation it can
override. The advisory failed as "advice to obey"; the packet succeeds as "evidence
to reason over."

---

## 9. Safety — why "drive" is bounded, not reckless

Emit is **re-certified against the live container** at the next cycle's Phase 1 via
each node's `check_command`. If a scratch-built closure does not apply cleanly in the
eval image (wrong apt name, missing transitive native dep, manylinux/musl mismatch),
**certification fails, the node falls back to FRONTIER, and it escalates to the LLM
automatically.** A wrong emit is self-correcting, not catastrophic.

This preserves the contract graph's "host owns truth, never trust a claim"
invariant — extended from "never trust the LLM" to "never trust the emit either."
Determinism is bounded to the host-certifiable subset; everything uncertain is the
LLM's call.

---

## 10. Synthesis payoff

Because the env is now built from an emitted, certified, topo-ordered recipe, the
final Dockerfile **is that recipe** — synthesis becomes "replay the certified emit,"
not "LLM reconstructs from a lossy trajectory." The "working env lost at synthesis"
failure class (the entire `successful_actions` backfill saga,
`agent.py:_backfill_successful_actions_from_ledger`) **disappears by construction:**
you cannot lose a closure you deterministically emitted and certified.

---

## 11. Components & boundaries

New/changed units, each with one purpose and a testable interface:

| Unit | Location (proposed) | Purpose | Depends on |
|---|---|---|---|
| `partition` | `src/python_deps/depgraph/emit.py` | classify nodes → certified/emittable/frontier | schema only (pure) |
| `topo_order` | `src/python_deps/depgraph/emit.py` | layer-respecting topo sort of emittable, conflict-aware | schema only (pure) |
| `build_recipe` | `src/python_deps/depgraph/emit.py` | emittable nodes → `list[RecipeStep]` | schema, RecipeStep |
| frontier render + packet | `src/python_deps/depgraph/advise.py` (extend) | unified planner section, relevance-gated, diagnostic packet | schema |
| live re-certify | `src/envstate/contracts/projection.py` (re-point) | per-cycle `check_command` against live container | depgraph, executor |
| EMIT drain phase | `src/envstate/orchestrator.py` | loop partition→topo→build_recipe, run via `run_recipe`, write back + re-certify until drained | emit.py, build_agent |

`emit.py` is pure (no Docker, no network), mirroring the rest of `depgraph/`; all of
its logic is unit-testable from hand-built `DepGraph` fixtures.

---

## 12. Testing strategy

- **Unit (pure):** `partition` bucket boundaries (resolved vs unresolved, single vs
  multiple fix-candidates, conflict pairs, build-from-source with/without certified
  toolchain); `topo_order` (cross-layer ordering, conflict exclusion, cycle safety);
  `build_recipe` (apt dedup, pinned-closure single step, build-from-source ordering).
- **Render:** frontier packet contains chain/conflict/platform/attempts/state-delta;
  certified collapses to counts; empty graph → empty section (byte-identical-off
  invariant preserved).
- **Integration (Docker, opt-in like existing `test_ldd_probe_docker.py`):** emit a
  known-good closure into a scratch container, re-certify, assert states flip; emit a
  deliberately-wrong apt name, assert the node falls back to FRONTIER (safety valve).
- **Regression:** with both flags off, the planner prompt is byte-identical to
  baseline (the existing off-state invariant).

---

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Scratch closure ≠ eval image | Re-certify in the live container; failures self-escalate (Section 9). |
| Emit "wall of text" overload | Relevance-gate: detail only for FRONTIER; certified as counts (Section 8). |
| build-from-source emitted before toolchain ready | Partition gates it on CERTIFIED toolchain; otherwise it stays FRONTIER (Section 5). |
| Re-certify cost per cycle | `check_command`s are cheap (import/`command -v`); reuse the existing refresh cadence, no extra container. |
| Behavioural regression vs v1g | Land behind the existing dual flag; A/B v1gd-unified vs v1g on the benchmark before retiring the contract store (D3). |

---

## 14. Open decisions (confirm at review)

- **D1 control inversion** — orchestrator emits before planner (graph-first).
  *Recommended.*
- **D2 pip emit shape** — whole pinned closure atomically; topo-order for cross-layer
  + build-from-source only. *Recommended.*
- **D3 contract-graph retirement** — re-point refresh/render at depgraph nodes now;
  delete the parallel store after a proven benchmark run. *Recommended.*
- **D4 emit execution path** — emit runs through `build_agent.run_recipe` (repair as a
  free safety layer), not a bare executor. *Recommended.*
- **D5 drain vs one-batch** — EMIT drains the whole certifiable DAG before consulting
  the planner, bounded by a max-drain count. *Recommended.*

If all five stand as recommended, the next step is an implementation plan
(writing-plans) sequencing: emit.py (pure: partition → topo_order → build_recipe) →
live re-certify (CERTIFY) → orchestrator EMIT drain phase → unified render/packet →
flag wiring → A/B.
