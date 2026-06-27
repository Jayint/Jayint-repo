# Graph-Scheduled Agent Architecture — Design

**Date:** 2026-06-26
**Status:** Approved design (brainstorm), pending spec review → implementation plan
**Branch:** `john-planner-v3`
**Builds on:** the static-probe-certified dependency graph (`src/python_deps/depgraph/`), the emit/escalate drain, and the runtime-feedback loop.

---

## 1. Summary

Re-spine the env-construction agent so the **dependency graph schedules a bounded
LLM executor**, instead of an LLM planner driving a loop that consults the graph.

Authority is split three ways with no party holding more than one kind:

- **Graph** decides **what & when** — it decomposes failures into typed
  obligations, orders them topologically, and frames the next one.
- **Agent** (`BuildAgent.work`) decides **how** — it is the only component that
  chooses commands, and only ever for a single framed obligation.
- **Host** decides **whether** — it runs the obligation's `check_command` (and
  the test gate) and is the only component that flips graph state.

The **Maintainer** is the single write path: it turns every command result into
certified-or-`UNKNOWN` graph nodes.

The defining consequence: **there is no LLM strategy layer.** Strategy *is* the
graph's topology. The LLM survives only inside `work`, as a tactical executor of
one obligation at a time, and **nothing enters the world model without a host
check.**

---

## 2. Motivation

The dominant failure mode of LLM-driven env agents is **hollow success**: the
LLM both *acts* and *judges* "is it done?", so it finalizes on a weak signal
(this codebase has already diagnosed the `pytest --collect-only` done-gate that
masks every execution-time failure). The standard fixes — better prompts, a
stricter self-check — leave the LLM holding the judging authority, so they leak.

The structural fix is to take judging away from the actor. The dependency graph
already does this for *necessity*: a node's `state` flips only when the host runs
its `check_command`. This design extends that discipline to the whole control
loop. The LLM never judges and never roams: the graph hands it a typed, framed
obligation, and the host certifies the result. The LLM does all the *doing* and
none of the *judging*.

This also matches what the graph is uniquely good at. Env construction is mostly
a dependency-closure problem — install/provide the frontier, certify, repeat —
with a hard tail of genuinely ambiguous steps (which service, what config). The
graph is the right engine for the closure; the LLM is the right engine for the
tail. Putting the LLM in charge of the whole thing wastes the graph and reopens
the hollow-success door.

---

## 3. Architecture: separation of powers

| Role | Authority | Component | Responsibility |
|---|---|---|---|
| **Graph** | **what & when** | scheduler (over `DepGraph`) | decompose failure → typed obligations; topo-order; frame the next obligation |
| **Agent** | **how** | `BuildAgent.work` | issue commands to satisfy one framed obligation; bounded by the host check as stop condition |
| **Host** | **whether** | certify (`refresh_host_graph` / test gate) | run `check_command`; flip `state`; run the sufficiency oracle (tests) |
| **Maintainer** | — (write path) | `deterministic_maintainer` + runtime classifier | classify results → append certified-or-`UNKNOWN` nodes/edges via `validate_patch(scope="host")` |

No component holds more than one of {what, how, whether}. That separation is what
makes "hand every error to the agent" safe — in a vanilla ReAct agent, routing
everything to the LLM *is* the hollow-success failure mode; here the LLM is
sandwiched between a deterministic **framing** layer (graph decomposition) and a
deterministic **judging** layer (host certification).

---

## 4. The control loop

Every error the graph encounters is handed to the agent. There is no deterministic
auto-fix tier in v1 (see §10 — it returns later as a learned cache, not a
hand-authored library).

```
loop:
  frontier = actionable obligations (state=UNKNOWN, dependencies SATISFIED),
             topologically ordered (deps before dependents)

  if frontier non-empty:
      ob     = next(frontier)                         # GRAPH: what & when
      report = BuildAgent.work(ob, context, ob.check) # AGENT: how (host check = stop condition)
      observe(report)                                 # MAINTAINER: append revealed obligations
      certify(ob)                                      # HOST: run ob.check, flip state

  else:                                                # frontier clean → check sufficiency
      if test_gate green:  return DONE
      else:                                            # sufficiency-stuck
          report = BuildAgent.work(discover_task, context, test_gate)
          observe(report)   # runtime classifier appends new obligations → back to top
          if no progress after budget:  return STUCK   # escalate / give up
```

```
        ┌─────────── GRAPH (world model + certified work-queue) ───────────┐
        │  detect → decompose → topo-order → frame obligation               │
        └───────────────────────────┬──────────────────────────────────────┘
                                     │ one typed obligation + evidence + check
                                     ▼
                            AGENT · work()        ← issues commands (the only "how")
                                     │ result → action ledger
                    ┌────────────────┴────────────────┐
                    ▼                                  ▼
            HOST · certify()                  MAINTAINER · observe()
         run check_command,                 classify ledger → append
         flip SATISFIED                      UNKNOWN obligations
                    └────────────────┬────────────────┘
                                     ▼
                          back to GRAPH (re-schedule)
```

The agent is invoked **per obligation**, host-checked each time, and the graph
re-schedules every cycle.

---

## 5. The obligation framing packet

When the scheduler hands an obligation to `work`, it passes a frame derived
entirely from the graph — the graph is the agent's problem statement, not the raw
repo:

- **id / type / tier / layer** — e.g. `syslib:libpq`, `SystemLib`, tier 2,
  `Layer.SYSTEM`.
- **goal** — human-readable obligation ("a working `libpq` shared library must be
  present").
- **evidence** — the captured failure that revealed the need (the `ldd`/import
  error, the connection-refused, the config `KeyError`).
- **check_command** — the host probe that certifies satisfaction; also the agent's
  **stop condition**. The agent may *run* this read-only check to know when to
  stop, but it never writes `SATISFIED`.
- **blocked_by / blocks** — the dependency edges, so the agent sees what this
  obligation gates and what it depends on.
- **certified context** — the already-`SATISFIED` nodes, so the agent knows what
  it can rely on.

All of this exists on the graph today (typed nodes, `check_command`, `requires`
edges, `state`); the new artifact is the assembled packet, not new schema.

---

## 6. The two stuck signals

Both route to the agent; they differ only in framing.

- **Necessity-stuck** — a frontier obligation won't certify even after `work`
  exhausts its budget on it (host `check_command` still red). Framing:
  *"satisfy this specific, typed obligation."* Tactical, narrow target.
- **Sufficiency-stuck** — the frontier is empty, every necessary node is
  `SATISFIED`, but the host **test gate** is still red. The graph believes
  everything required is present, yet the repo doesn't work. Framing:
  *"tests fail and the graph is clean — discover what the running code actually
  wants that nobody declared."* This is the runtime-feedback loop's home turf:
  the agent's commands land in the action ledger, the runtime classifier turns
  failures into new `UNKNOWN` obligations, and the loop resumes from the top.

The two oracles are distinct on purpose: the graph certifies **necessary**
(presence, via host probes); the test suite certifies **sufficient** (test-pass).
The sufficiency oracle is host-run and the agent never owns it — so even when the
LLM is driving the residual, it cannot self-declare done.

---

## 7. Write-back discipline — experiments, not facts

This is the invariant that keeps the architecture honest:

- The agent emits **commands**; their results land in the same action ledger as
  the deterministic path.
- OBSERVE (Maintainer + runtime classifier) turns those results into graph
  mutations through the single `validate_patch(scope="host")` write path.
- The agent **never** writes `SATISFIED`, **never** appends a certified node,
  **never** touches the test gate.
- The agent may *propose experiments* (commands to try, including proactively for
  an ambiguous obligation), but it never *proposes facts*. Every node still earns
  its state from a host check.

Consequence — the paper's purity claim: **no node in the graph ever exists on LLM
authority alone.** Every node is host-certifiable, whether it was statically
extracted, deterministically emitted, or runtime-observed from an agent command.
This kills the "LLM hallucinated a dependency" failure mode entirely, and it
survives the handoff because the Maintainer remains the single graph-writer.

---

## 8. Scheduling and oscillation

- **Topological order** — obligations are handed out deps-before-dependents (fix
  `libpq` before `psycopg2`), so the agent never works a dependent while its
  dependency is still red.
- **Never re-hand a `SATISFIED` node** — the certify gate is also the
  de-duplication guard; a certified obligation is never re-scheduled.
- **Oscillation guard** — with every fix going to a per-obligation agent, watch
  for cross-obligation interference (fix A breaks B, fix B re-breaks A). Topo
  ordering plus "never re-hand `SATISFIED`" damp it; add a per-obligation re-hand
  cap so a node that flips back to red more than *k* times is escalated rather
  than retried indefinitely.

---

## 9. Mapping onto existing code

This is a re-spine of existing machinery, not a greenfield rewrite.

| Existing | Fate | Notes |
|---|---|---|
| `depgraph` (`src/python_deps/depgraph/`) | **reuse** | already the world model + certified work-queue |
| emit/escalate drain | **repurpose** | becomes the scheduler's "hand next obligation to the agent" loop |
| runtime-feedback loop | **reuse** | the OBSERVE channel for the sufficiency-stuck branch |
| `deterministic_maintainer` | **reuse** | OBSERVE writer; single graph-writer preserved |
| host certify (`refresh_host_graph` / test gate) | **reuse** | CERTIFY, unchanged |
| `BuildAgent` `run` / `run_recipe` + stuck guard | **repurpose** | becomes `work` — per-obligation agentic execution bounded by the host check |
| **strategic LLM Planner** | **deprecate (retain)** | marked dormant, **not invoked** on the new path; kept in tree for possible reuse (ambiguous-frontier ordering, whole-repo give-up). **Not deleted.** |

---

## 10. Scope

**In scope (v1):**
- The graph-scheduler control loop (§4), behind a flag, default off, byte-identical
  when off, A/B-able against the current arm.
- Per-obligation framing packet (§5).
- Both stuck signals routing to `work` (§6).
- Write-back discipline enforced (§7).
- Topological scheduling + oscillation guard (§8).
- The strategic LLM Planner **deprecated and routed around** (§9), not removed.

**Deferred (future work):**
- **Learned-recipe cache.** When the host *certifies* an agent fix, memoize the
  `(obligation signature → command that worked → certified)` triple; on the next
  occurrence the scheduler short-circuits the agent and replays the cached
  command. The deterministic auto-fix tier thus returns as something the graph
  **learns** from certified agent-fixes — not a hand-authored library. Keeping it
  out of v1 makes the first cut simple and the ablation honest (every fix is the
  agent's; the graph's value is purely structural).
- **Full removal of the strategic LLM Planner.**
- **Multi-extract** per observation; finer per-module attribution.

---

## 11. Novelty / contribution

1. **No LLM strategy layer.** Strategy is deterministic graph topology; the LLM is
   a bounded tactical executor. Most agentic systems (ReAct, plan-and-execute) put
   the LLM in charge of strategy and let it self-judge. This inverts both.
2. **Certified blackboard.** The graph is simultaneously world model, coordination
   substrate (roles read/write the graph, never call each other), and trust
   ledger (discover ≠ certify; only the host check flips state).
3. **Dual oracle.** Necessity is certified by host probes; sufficiency by the test
   suite — two different oracles, enabling precise failure attribution.
4. **Observed > declared necessity.** The runtime-feedback channel records what the
   running code actually demanded (dynamic imports, dlopen, runtime env vars,
   service reachability) — ground truth no static analyzer can produce.
5. **Emergent recipe cache (future).** The graph distills deterministic recipes
   from certified agent-fixes over time, rather than shipping a hand-tuned table.

One-liner: *a host-certified dependency graph that schedules a bounded LLM
executor — strategy is graph topology, the LLM acts only tactically, and nothing
enters the world model without a host check.*

---

## 12. Testing strategy

- **Unit** — scheduler (topological frontier ordering; `SATISFIED` never
  re-handed; oscillation cap); framing-packet assembly from a `DepGraph`;
  write-back purity (an agent result is appended as `UNKNOWN`, never `SATISFIED`;
  the agent path never calls a certify/write API directly).
- **Integration** — one full loop cycle on a synthetic graph: frontier →
  `work` (stubbed) → observe → certify → re-schedule; the sufficiency-stuck
  branch feeding a runtime-discovered obligation back in.
- **E2E** — real Docker, flagged on, A/B against the current arm; assert
  byte-identical behavior when the flag is off; assert the host test gate (not the
  agent) is what finalizes success.

---

## 13. Risks and open questions

- **LLM cost on trivial fixes.** Every fix is an LLM call, even ones a lookup
  table would nail. Accepted for v1; the learned-recipe cache (§10) is the
  reclamation path.
- **Reproducibility.** Agent fixes vary run-to-run; for benchmarking, report
  across seeds and lean on the certify gate for outcome stability.
- **Oscillation.** Cross-obligation interference (§8); mitigated by topo order +
  re-hand cap, but to be watched on real repos.
- **Frontier richness.** The scheduler is only as good as the graph's
  decomposition; obligations the graph can't express fall to the sufficiency-stuck
  branch, which must not become a catch-all that swallows everything.

---

## 14. Rollout

Follow the established depgraph-feature pattern: shadow → wired → A/B, behind a
flag, default off, byte-identical when off. The new arm is A/B-compared against the
current emit arm so the structural change is isolated on an identical baseline.
