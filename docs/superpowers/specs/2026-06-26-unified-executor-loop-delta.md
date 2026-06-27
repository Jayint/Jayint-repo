# Topological-Wave Executor — Design

**Date:** 2026-06-27 (rewrite of the 2026-06-26 "unified executor loop delta", same file)
**Status:** Design — converged via e2e evidence; ready for implementation plan
**Lineage:** `2026-06-26-graph-scheduled-agent-architecture-design.md` → *unified executor loop delta* → **THIS (topological-wave framing)**
**Supersedes:** the one-at-a-time "unified loop" mechanic AND the phase-based emit-prefix split — both fold into a single topological-wave executor.

---

## 1. Thesis

A host-certified **dependency DAG** drives a **topological-wave executor**. Each wave is a *deterministic batch* over the current frontier — all reciped nodes whose dependencies are already satisfied, installed in one command per layer. The **LLM is a bounded exception handler**, invoked only when (a) a wave fails or (b) the deterministic frontier is exhausted but the tests are still red. The host alone certifies state; the LLM proposes commands, never facts.

```
DONE  ⇔  deterministic frontier exhausted   ∧   test gate green
         (graph certifies NECESSARY)             (tests certify SUFFICIENT)
```

This is both the **fastest** design we have measured and the **simplest to formalize**: one loop, one decision rule, one OBSERVE writer, a two-oracle termination condition.

---

## 2. Why this framing (what changed, and the evidence)

The earlier "unified loop" handed the LLM **one obligation per cycle** and installed **one node at a time**. Three independent reviews plus three real e2e runs killed that mechanic and produced this framing:

- **One-at-a-time is slower than batch.** `emit_drain` already collapses a whole topological level into ≤1 command per layer (`pip install <all>`). Per-node installs are N round-trips for no benefit when they succeed (the common case), and they defeat pip's joint resolver.
- **The "backoff → LLM bridge" was broken as specced** (`emit._is_emittable` and `schedule._is_actionable` are independent predicates; a backed-off node with `check_command=None` fell into neither frontier and was silently dropped).
- **The resolution dissolves the false choice:** *batch is a topological wave*, and *the unified loop is the frame around it*. You keep batch's speed inside one clean loop — no trade-off.

**Empirical record** (memU-server, `deepseek/deepseek-v4-flash`, 30-step budget):

| metric | baseline (LLM-maint) | emit-prefix | **emit-prefix + config-advisory** |
|---|---|---|---|
| configuration_success | False | True | **True** |
| in_build_pass_rate | — (pytest never ran) | 0.8384 | **1.0 (83 passed)** |
| packages installed | 31 (budget died) | 75 | **87 (full closure)** |
| total ledger commands | 30 | 151 | **13** |
| config-tier env-var thrash | — | 117 (77%) | **0 (0%)** |
| total tokens | 69,259 (gave up) | 541,512 | **58,075** |
| wall-clock | ~timeout | ~56 min | **~10 min** |

The final run is the loop working end to end: 1 batched wave installed the 87-package closure deterministically (0 LLM tokens); the test gate fired once, went red on a missing test-only dep; the LLM diagnosed and installed exactly `pytest-asyncio`; the gate re-ran green (83/83). That is the topological-wave executor in miniature.

---

## 3. Core concepts

**Topological wave.** A node is *eligible* only when every node it `REQUIRES` is `SATISFIED` (`partition()` enforces this; `topo_order` ranks by layer, SYSTEM → PIP). A wave is the set of currently-eligible reciped nodes — one topological level — collapsed by `build_recipe` into one batched command per layer. Cross-layer dependencies (e.g. pip `psycopg2` → syslib `libpq`) fall into *later* waves. So **batch and topological order are the same thing**: a batch is a wave; the waves preserve the DAG's partial order.

**Two oracles.** The graph certifies **NECESSARY** (presence: each node's own `check_command`, e.g. `python -c 'import psycopg2'`). The actual test suite certifies **SUFFICIENT** (behavior: `python -m pytest -q`). Both are required for DONE. The sufficiency oracle is independent of the graph — it catches gaps the closure never modeled (the `pytest-asyncio` case).

**Three kinds of host command** (never conflate them):

| # | command | example | answers | scope | when |
|---|---|---|---|---|---|
| 1 | recipe / wave action | `pip install <wave>` | — (the move) | a wave | each deterministic wave |
| 2 | node certify | `python -c 'import psycopg2'` | "is this node present?" → flips `state` | one node | OBSERVE, after each move |
| 3 | test gate | `python -m pytest -q` | "does the repo work?" → `done_flag` | global | once, at frontier exhaustion |

**Host-first execution.** The host proposes the deterministic action; the LLM is invoked **only on failure** (`rc ≠ 0`), bounded to ≤5 turns. `rc == 0` means "skip the LLM," not "satisfied" — **certify (#2) is the authority**.

**Authority separation** (no party holds more than one power):

| party | power | mechanism |
|---|---|---|
| Graph | *what-next* (deterministic-preferred) | the topological frontier |
| Agent (LLM) | *how* (on stall only) | host-first repair, ≤5 turns, commands not facts |
| Host | *whether* | `certify_refresh` flips `state`; the test gate sets `done_flag` |
| Maintainer | *observe* (sole graph-writer) | end-of-loop classify + record + `done_flag` |

---

## 4. The executor loop

```
certify(graph)                                     # once: host probes flip UNKNOWN → {MISSING, SATISFIED}

loop until done_flag, or turn_budget == 0:

  ── SELECT + EXECUTE ──────────────────────────────────────────────────────
  wave = next_deterministic_wave(graph)            # reciped MISSING nodes whose REQUIRES-deps are
                                                   # SATISFIED — one topological level, ≤1 cmd per layer
  if wave is not empty:
      result = run_wave(wave)                      # deterministic · NO turn · ~0 tokens
      if not result.ok:                            # the batch failed →
          culprit  = isolate(wave, result)         #   per-node re-run to find the failing node
          result   = repair(culprit, max=5)        #   HOST-FIRST agent (LLM); turn_budget -= used
  elif test_gate_passes():                         # frontier exhausted → SUFFICIENCY (actual pytest, ONCE)
      done_flag = True; break
  else:                                            # frontier clean ∧ tests red → irreducible gap
      result = repair_sufficiency(context, max=5)  #   HOST-FIRST agent diagnoses (e.g. service/config)
      turn_budget -= used

  ── OBSERVE (maintainer, at the end · deterministic · 0 tokens) ────────────
  certify(graph, result)                           # HOST flips state of touched nodes (the truth)
  maintainer.observe(graph, result, ledger):       #   • classify failures → RUNTIME obligations
                                                   #   • record success facts
                                                   #   • set done_flag iff the test gate ran green
```

Read uniformly: **the host always proposes the next action** — a deterministic wave if one exists, otherwise the test gate — and **the LLM repairs only failures**. `turn_budget` decrements solely on LLM repair, so a "turn" meters reasoning, not mechanical installs.

Two properties that make it correct:

- **The test gate runs only at frontier exhaustion**, never after each wave — so you pay one pytest, not N. Its (verified) result is what OBSERVE turns into `done_flag`.
- **Batch failure degrades to per-node, not the reverse.** `isolate` runs the failed wave's nodes singly to attribute the failure, then the host-first agent repairs the one culprit with focused context (e.g. "`psycopg2` fails — it needs `libpq`"). Batch speed in the common case; per-node attribution exactly when something breaks. This replaces the broken "2-failure backoff" bridge with an explicit, immediate one.

---

## 5. The host-first build agent

This is the one component still on the old shape (LLM-first) and the core of the build. The new `BuildAgent.run` is a bounded host-first repair loop over **one** failed action:

```
repair(node_or_context, max_turns):
  for _ in range(max_turns):
      ok, _ = sandbox_execute(check)            # node's check_command (#2) — the HOST stop condition
      if ok: return done                        # the LLM cannot end this; only the host check can
      thought, cmd = complete_with_retry(client, context)   # LLM reads the LAST error, proposes a cmd
      rc, out = sandbox_execute(cmd)            # run it; append to history + ledger
      context += (cmd, rc, out)                 # the error feeds the next proposal  ← the fix loop
  return blocked                                # budget spent; node stays MISSING → OBSERVE handles it
```

Worked example (the canonical cross-layer repair):

```
wave: pip install psycopg2  → rc=1: "Error: pg_config not found"     ← host action failed
  turn 1: LLM → apt-get install -y libpq-dev
  turn 2: LLM → pip install psycopg2 → rc=0
  host re-check `python -c 'import psycopg2'` → passes → done         (≤5 turns, then OBSERVE)
```

The LLM never declares success (the finish signal is ignored while a host `check` is active); it proposes **commands** (experiments), and only `certify` flips graph state. It is the exception handler for the irreducible tail (system libs, version conflicts, services, config) — trivial reciped installs never reach it.

---

## 6. Invariants (preserved, unchanged)

- **Host certifies; nothing else flips `state`.** `certify_refresh` and the test gate are host-run.
- **The LLM cannot self-declare done.** `done_flag` comes only from a host-run, *verified* green test gate (`_verified_test_run_passed`: rc=0 ∧ a real "N passed" summary ∧ effective run ∧ not collect-only/venv-wrapped). A bare `pytest rc=0` is not enough.
- **Single graph-writer.** All mutation flows through the end-of-loop maintainer; deterministic moves come only from the host-certified reciped frontier.
- **No node exists on LLM authority alone.** Every node is host-certifiable.
- **Config is advisory.** Tier-6 CONFIG nodes are excluded from the executor frontier (their `printenv X` check is unsatisfiable in a fresh-shell exec, and a genuinely-required var is set reactively through the sufficiency path). Same exclusion as SERVICE.

---

## 7. Mapping to code

**Reused as-is** (the wave executor is mostly assembly of existing parts):
- `emit.py` — `partition().emittable`, `topo_order`, `build_recipe` (collapses a wave to ≤1 cmd/layer), `emit_drain` (the batch wave runner).
- `depgraph_live.py` — `certify_refresh` (the per-node #2 oracle).
- `schedule.py` — `_is_actionable` (excludes SERVICE **and** CONFIG; excludes emittable so the LLM sees only the residual).
- `orchestrator.py` — `_verified_test_run_passed` / `_run_tests_verified` (the hardened #3 gate), `VERIFY_TEST_CMD = "python -m pytest -q"`, the deterministic maintainer, runtime classification → `DiscoveredBy.RUNTIME`.

**New / changed:**
- `next_deterministic_wave(graph)` — thin wrapper: the current `partition().emittable` topological level → `build_recipe`. (Batch, not one-at-a-time.)
- `run_wave` + `isolate` — run the batch; on failure, per-node re-run to attribute the culprit.
- **`BuildAgent.run` reworked LLM-first → host-first** (§5): run the recipe; LLM only on `rc ≠ 0`, ≤5 turns; host check is the stop.
- **Single OBSERVE** — fold runtime-classify + certify + `done_flag` into one end-of-loop maintainer step (today they are two writers).
- **Turn accounting** — `turn_budget` decrements only on LLM repair.

**Deprecated-retained** (out of the loop, not deleted): the strategic LLM Planner, the LLM Maintainer ("reflection"), the contract graph + its done-gate.

---

## 8. What this supersedes

| prior text | replaced by |
|---|---|
| "every error → agent; no deterministic tier" | deterministic-first; the wave is the fast path, LLM is the exception handler |
| one obligation / one node per cycle | a **wave** (batch) per deterministic step; turn = LLM repair only |
| "backoff → LLM bridge" (2-failure demotion) | explicit `isolate`-on-wave-failure → host-first repair of the culprit |
| separate `_dep_emit_phase` prefix + scheduler gate + LLM-first agent | one loop: host proposes (wave \| gate), LLM repairs failures |
| two OBSERVE writers (runtime-feedback + maintainer) | one OBSERVE (the end-of-loop deterministic maintainer) |

---

## 9. Empirical validation

The three-run table in §2 is the evidence. Key reads:
- **Batch waves work:** the 87-package closure cleared in ~1 effective command, 0 LLM tokens for installs.
- **Config-advisory is decisive:** removing CONFIG from the frontier dropped total tokens 9.3× (542k → 58k, below even the baseline that *failed*) and raised the pass rate to 1.0 (the thrash had been corrupting `.env`/`/etc/environment`).
- **The two-oracle gate earns its place:** all 87 nodes certified present (NECESSARY) yet pytest was red until `pytest-asyncio` (never a graph node) was added — only the actual test gate (SUFFICIENT) surfaced it.

Next validation target after the host-first agent lands: re-run memU-server (expect the same 13-command shape with the inner agent now also host-first), then a pure-Python repo for a clean green-path proof, then a repo whose closure genuinely needs a cross-layer repair (libpq) to exercise §5 end to end.

---

## 10. Deferred (unchanged)

- Learned-recipe cache (the graph *learns* recipes for things `build_recipe` can't yet emit — the generalization of `next_deterministic_wave`).
- Full removal of the deprecated LLM Planner / LLM Maintainer / contract graph.
- SERVICE-tier deterministic provisioning (services still reach the LLM via the sufficiency repair).
- Necessity gating for CONFIG beyond advisory (proactive `.env`/profile baking with a satisfiable check) — only if a repo needs a config var proactively rather than reactively.

---

## 11. Paper framing

One-sentence contribution:

> A host-certified dependency DAG drives a **topological-wave executor**: each wave is a deterministic batch over the current frontier; the LLM is a **bounded exception handler** invoked only when a wave fails (`rc ≠ 0`) or the frontier is exhausted while tests are still red. The host alone certifies state; the LLM proposes commands, never facts; termination is the conjunction of two oracles — the graph (NECESSARY) and the test suite (SUFFICIENT).

Why it reads cleanly: one loop, one decision rule, one OBSERVE writer, an authority table with no overlap (§3), and a termination condition stated as a theorem. The novelty hook is *LLM-as-exception-handler over a certified DAG executed in topological waves* — not an LLM agent that runs the build. The empirical section (§2/§9) shows the deterministic waves carry the load and the LLM touches only the irreducible tail.
