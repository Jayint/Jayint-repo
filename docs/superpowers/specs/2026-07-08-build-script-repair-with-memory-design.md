# Build-Script Repair with a Memory — Design Spec

**Date:** 2026-07-08
**Status:** Draft (for review)
**Scope:** Replace the v3 repair loop. Keep the graph-centric, build-script-repair paradigm. Fix the two problems that make it confusing and weak: the three-level loop nesting, and the amnesiac (context-free) repair agent.

---

## 1. Summary

The v3 system builds an executable environment by rendering a build script (`setup.sh`) from a certified dependency graph, running it from a clean base image, and repairing failures until install + `pytest --collect` succeed. This is **build-script repair**: the agent edits the *graph* (which renders the script); the *host* runs the script and owns truth.

Today's repair machinery has two problems:

1. **It is confusing.** Three nested loops (per-cycle → per-repair-attempt → per-diagnostic-probe), four separate convergence counters, a double repair budget, and per-turn container resets. No one can hold it in their head.
2. **The agent is amnesiac.** Each repair attempt is a fresh LLM conversation. The only memory carried across attempts is a string blocklist (`known_invalid`, rendered "DO NOT propose these"). The agent never sees its own prior patches or what they did. So the ≤5 attempts do not compound — they are five cold one-shots, not a debugging session. Hard, interdependent failures (native lib → build tool → base-image chain) are structurally under-served.

This spec keeps the paradigm **unchanged** and makes exactly one behavioral change: **give the repair agent one sustained, compounding conversation per error** ("a notebook"), and collapse the three-level nesting into one clean loop over errors. The agent stays a *graph editor* — it never runs mutating commands in the container. The host still renders the whole script and replays it from a clean base; certification is still host-only.

**Recipe-editor, not cook.** The agent edits the recipe (graph → build script). The host cooks the whole recipe from a clean kitchen and reports how it went. The agent never touches the kitchen. The only change from today: the recipe editor gets a notebook instead of amnesia.

---

## 2. Goals & Non-Goals

### Goals
- **G1** One clean control structure: a loop over errors, and within each error, one continuous agent session. Two levels, not three.
- **G2** The repair agent carries sustained memory per error: every patch it proposed, the gate verdict, and the *real* clean-replay result, all in its context, so reasoning compounds.
- **G3** Preserve the graph-centric guarantees verbatim: host-certified (revocable) state, the typed-patch gate as the trust boundary, per-node failure localization, the graph as the audited record, and "the artifact you ship is exactly the artifact you verify."
- **G4** Replace the four convergence counters + the two turn caps with **one** structured progress rule + **one** per-error budget.

### Non-Goals (explicitly out of scope)
- **N1** No live container mutation by the agent. The agent does NOT run `apt-get`/`pip install`/file edits. That is repo2run, not build-script repair. (Read-only investigation probes are allowed — see §5.3.)
- **N2** No incremental execution. Every patch is verified by a full replay from a clean base (Model B). The incremental "run only the new block" optimization is deliberately excluded to keep one simple execution model.
- **N3** No deletion of the ablation/proof apparatus (`run_v1`, `emit_drain`, `repair_failed_nodes`, `block_emit`, the `trace_verify` negative spec). These are load-bearing research arms, not dead code.
- **N4** No change to the construction pipeline (scan/resolve/probe/populate) or to `render_build_script`, `partition`/`emit`, `certify`, or the gate's validation rules. This spec replaces the *repair control flow* only.

---

## 3. Design Principle

Every durable change flows through the graph. The agent's only mutating action is a **typed graph patch** validated by the existing `PatchGate`. The container is only ever mutated by the host replaying the full rendered script from a clean base. Certification (`State.SATISFIED`) is written only by a host-run `check_command`. The agent proposes; the host certifies; the graph records.

The single new idea: **a repair session is scoped to one error and persists across patches**, so the agent reasons over its own history instead of restarting cold each attempt.

---

## 4. Architecture Overview

```
# ── TOP LEVEL: loop over errors ──
def repair_until_green(graph):
    while budget_left():
        script  = render_build_script(graph)        # graph → setup.sh   (unchanged)
        result  = replay_from_base(script)           # clean base + full script (Model B, unchanged)
        graph   = certify_all(graph, host_check)     # host checks flip state (unchanged)

        if result.ok:
            test = run_test_gate(graph)              # pytest --collect
            if test.ok:
                return DONE
            graph = ingest_test_failures(graph, test)  # tests → new obligations (unchanged)
            continue

        error = localize(result)                     # first failing node + command + output (unchanged)
        route = diagnose(error, repo_ctx)            # DiagnosisRouter (unchanged)
        if route in {REPO_INTERNAL_REF, RESIDUAL, INVALID_ATTEMPT}:
            record_out_of_scope(error, route)        # never env-repair these (unchanged policy)
            continue

        graph = fix_one_error(error, graph)          # ← the sustained session (NEW)

        if no_global_progress():                     # same error keeps recurring, unrepaired
            return GIVEUP
    return GIVEUP_BUDGET
```

Two levels: the error loop above, and the per-error session below. The old third level (the per-diagnostic-probe sub-loop) is absorbed into the session as ordinary turns.

---

## 5. Components

### 5.1 The error loop (top-level control)
Owns the render → replay → certify → localize → route → repair cadence and global termination. Replaces the `run_v3` per-cycle loop and, critically, the **scheduler**: the build script's own execution order *is* the schedule, so "what to repair next" = "the first command that failed in the replay." There is no separate `next_decision`/`PlannerDecision` handout.

**Global termination (replaces 4 counters):**
- `DONE` — replay `rc == 0` AND test gate collects (host-verified).
- `GIVEUP` — a session gave up on an error, and re-localizing yields the *same* unresolved error with no other progress (we are stuck).
- `GIVEUP_BUDGET` — global token/turn budget exhausted.

### 5.2 The `RepairSession` (the notebook)
One session per error. Holds the agent's full conversation for that error plus a structured history. Immutable-append.

```
RepairSession:
    error_key        # the session's initial identity: (localized_node_id, normalized_error_class).
                     # DECIDED (§13.1): the session FOLLOWS forward progress on that causal chain
                     # even as the failing node moves (fixing A reveals A's dependency B). The key
                     # is the seed identity, not a per-turn gate — one session spans the chain.
    seed             # failing node, failing command, failing output, graph neighborhood of the node
    steps: [Step]    # ordered history (the notebook)

Step (one per agent action):
    kind             # "probe" | "patch"
    action           # the read-only command, OR the PatchProposal
    result:          # what the HOST observed
        # for probe:  rc, output
        # for patch:  gate_accepted, gate_errors,
        #             replay_rc, new_failing_command, new_failing_output,
        #             certified_delta (node ids that reached SATISFIED),
        #             resolved (bool)
    progress         # derived: did this step advance the error? (see §5.4)
```

The session is rendered into the agent's prompt each turn as a running log:
> *Attempt 1 — you added `syslib:libpq` (provider `apt:libpq-dev`). Clean replay → still failed, now at `pip install psycopg2`, error: `pg_config not found`. Attempt 2 — …*

This is the entire fix for the amnesia problem: the agent sees exactly what it tried and exactly what the clean replay did.

**On session end (resolved OR giveup) the structured `steps` history is persisted onto the target node's `attempts` axis** (DECIDED §13.2) — each PATCH step becomes an `Attempt(command, outcome, check, cycle)` (extended with a patch-shaped payload) on the node it targeted. This makes "what did repair try, and what happened" durable, queryable graph state (`to_dict`-serialized) instead of ephemeral loop state, and finally uses the `attempts` axis that the repair path leaves empty today.

### 5.3 The agent contract
- **Input:** the `RepairSession` (seed + full step history), rendered as one conversation.
- **Two actions only:**
  1. **PROBE** — run a **read-only** command to investigate (host runs via the read-only executor; result appended to the session). This is investigation, not mutation. It is validated read-only by the same `is_read_only` check the gate uses on `check_command`s.
  2. **PATCH** — emit a typed `PatchProposal` (add nodes/providers/edges/script-blocks, each node carrying a read-only `check_command`). This is the only mutating action, and it goes through the existing `PatchGate` unchanged.
- **Output per turn:** one action. The session loops until resolved / stalled / over budget.

The agent has no verb that mutates the container. A fix becomes truth automatically: a PATCH adds nodes with `check_command`s → the host re-renders and replays the full script → `certify_all` runs those checks → passing checks flip the nodes to `SATISFIED`. There is no agent-owned "declare success" verb.

### 5.4 Progress & termination (replaces the counters and turn caps)
A single structured rule. After a PATCH's clean replay, the step **made progress** iff any of:
- a node reached `SATISFIED` (`certified_delta` non-empty), or
- the localized failing command changed, or
- the normalized error class changed (e.g. `missing-header` → `undefined-symbol` → `version-conflict`), or
- the failing command moved to a later block in the script.

Otherwise the step is **no-progress**. "Normalized error class" reuses the existing failure classifier (`failure_classifier.py` / `runtime_classify.py`) that the `DiagnosisRouter` already wraps — no new classifier is introduced.

**Session termination:**
- **Resolved** → the original `error_key`'s failure is gone on the clean replay (its node certified, or its command now passes). Success; return the updated graph.
- **Stalled** → `STALL_LIMIT` (default **2**) consecutive no-progress PATCHes. Give up on this error, record the transcript.
- **Budget** → per-error turn/token cap hit. Give up, record.

This one rule subsumes: `max_repairs=5`, `max_diag_turns`, the identical-command guard, `_gate_stall`, `_residual_stall`, `_sched_stuck`, and the double budget. Probes are cheap and not budget-charged toward `STALL_LIMIT` (only PATCH results move the progress signal); a **per-error hard turn cap of 15** (DECIDED §13.3) bounds runaway probing regardless of progress.

---

## 6. Data Flow — one error, walked through

Scenario: repo needs `psycopg2`; its build fails because `libpq` is missing, then because `pg_config` is missing.

1. Error loop renders the script, replays from base, localizes the first failure: `pip install psycopg2` fails, `libpq` missing. `diagnose` → `ENVIRONMENT`. Start a session.
2. **Session turn 1 (PROBE):** agent runs `ldconfig -p | grep -q libpq` → rc 1 (absent). Appended to notebook.
3. **Session turn 2 (PATCH):** agent adds `syslib:libpq` + provider `apt:libpq-dev` + edge `pkg:psycopg2 → syslib:libpq`, with `check_command="ldconfig -p | grep -q libpq"`. Gate accepts. Host renders + replays from base + certifies. `syslib:libpq` → SATISFIED, but replay now fails at psycopg2's compile: `pg_config not found`. **Progress** (error class changed). Notebook records patch + this result.
4. **Session turn 3 (PATCH):** agent — *seeing that its libpq patch worked and the failure moved* — adds `tool:pg_config` + provider `apt:libpq-dev` (or `postgresql-server-dev`), check `command -v pg_config`. Replay → green for psycopg2. Original `error_key` resolved. Session returns.
5. Error loop re-renders, replays: install now `rc 0`. Runs test gate. If green → `DONE`.

Contrast with today: at step 4 the agent would have started cold, unaware that step 3 fixed libpq or that the failure had moved.

---

## 7. What's preserved (graph-centric mapping)

| Guarantee | Mechanism (unchanged) |
|---|---|
| Host-certified, revocable truth | `certify` / `certify_all` — only a host check flips `state` |
| Typed-patch trust boundary | `PatchGate.validate → apply → compose`; agent can't write SATISFIED |
| Failure localization | `localize_install_failure` → one node per error; session scoped to it |
| Never repair repo-local imports / residual bugs | `DiagnosisRouter` routes those out before a session starts |
| Graph = the artifact | `render_build_script(graph)`; final green replay from base proves it |
| Deterministic install of the easy majority | `partition`/`emit`/`compile_blocks` still render the unambiguous nodes |
| Audited record | **Improved** — the session transcript + certified nodes attach to the node's `attempts` axis (today unused by repair) |

---

## 8. What's removed / changed

- **Three-level loop nesting → two levels** (error loop + per-error session). The per-diagnostic-probe sub-loop becomes ordinary session turns.
- **The scheduler's task-handout** (`graph_scheduler.next_decision`, `PlannerDecision`) → the script's execution order *is* the schedule; repair targets the first replay failure.
- **Four convergence counters + two turn caps + double budget → one progress rule + one per-error budget** (§5.4).
- **`known_invalid` string blocklist → the structured session notebook.** The agent's memory is its real patch history, not a "do not repeat" list.
- **The cold `V3BuildAgent.propose` (fresh conversation per attempt) → a sustained per-error session.**

Unchanged and reused as interfaces: `render_build_script`, `reset_to_base`/`run_install_script` (Model-B replay), `certify_all`, `patch_gate.*`, `diagnose`, `localize_install_failure`, `partition`/`emit`, `ingest_runtime_failures` (test gate). The `run_v1`/ablation/proof apparatus is untouched (N3).

---

## 9. Error handling & edge cases

- **Gate rejects a PATCH:** the reject + errors are appended to the notebook (the agent sees *why*), and it retries within the same session. A reject is not a progress step; repeated rejects count toward `STALL_LIMIT`.
- **A different error surfaces after a patch:** if the localized node changed, that is *progress* on this session, and the session continues on the new failure (still one session, because the build moved forward). A brand-new unrelated error class after resolution starts a fresh session via the error loop.
- **Session gives up (stalled/budget):** the error is recorded as unresolved with its transcript. The error loop re-localizes; if it is the same unresolved error, `no_global_progress()` trips → `GIVEUP`. Honest, bounded, with a readable transcript.
- **Install green but tests fail:** `ingest_test_failures` mints new obligations; loop continues (unchanged behavior).
- **Certification revocation:** a later patch breaks an earlier node → `certify_all` flips it back to MISSING → it becomes the next localized error. Handled naturally by the loop.

---

## 10. Testing strategy

- **Mock-graph unit tests (no Docker/LLM)** — extend `scratchpad/mock_graph_probe.py` into `tests/depgraph/`: assert apply → MISSING, certify flips/revokes, partition backoff. Already prototyped and passing.
- **Session memory threading** — a fake agent that emits a scripted sequence of PATCHes; assert the rendered prompt at turn N contains turns 1..N-1's patches and results.
- **Progress rule** — table-driven tests for `made_progress`: certified-delta, command-moved, error-class-changed, unchanged-signature → correct progress/no-progress verdict; `STALL_LIMIT` triggers giveup.
- **Error loop control flow** — fake replay/certify executors driving DONE / GIVEUP / GIVEUP_BUDGET paths; assert termination reasons.
- **Golden end-to-end** — the psycopg2 scenario (§6) with a fake executor, asserting the two-patch resolution and that turn 3's prompt shows turn 2's result.
- **Regression parity** — the 26 existing `run_v3` tests inform which behaviors must be preserved; new loop must satisfy the invariants they encode (host-only certify, no legacy path execution via `trace_verify`).

---

## 11. Migration plan

1. **Tag `arm-b-baseline`** at current HEAD before touching the loop, so the old structured loop stays runnable for A/B.
2. Build the new loop as a **new module** (`src/envstate/repair_session.py` + a slim error-loop driver) beside the current orchestrator; wire it behind the existing `run_v3` entry once at parity.
3. Reuse every preserved interface (§8) directly — no forking of `render`/`replay`/`certify`/`gate`/`diagnose`.
4. Port `V3BuildAgent` to the session contract (§5.3): same typed-patch output, same read-only probe, but conversation persists across the session instead of resetting per patch.
5. Retire `run_structured_repair` + the orchestrator's repair phases (`_repair_or_route`, the double call sites, the counters) once the new loop passes the ported test suite. Keep `run_v1`/ablations/proof (N3).

---

## 12. Risks & tradeoffs

- **Token cost rises per error** — a real conversation vs. cold one-shots. Accepted: we spend more to actually solve hard errors instead of cheaply failing. Bounded by the per-error budget.
- **Full replay per patch stays expensive** — inherent to clean-room build-script repair (N2). This is the price of "verify exactly what you ship." Flagged, not optimized.
- **`made_progress` calibration is the one delicate piece** — too loose and a session wanders; too tight and it quits on a hard-but-progressing chain. Mitigation: start strict (`STALL_LIMIT=2`), make it a single well-tested pure function, tune against the tagged baseline.
- **Sustained context reduces reproducibility** — a growing transcript is less deterministic than today's curated one-shot prompt. Accepted as the cost of compounding reasoning; the notebook is structured (not raw scrollback) to keep it auditable.

---

## 13. Resolved decisions

- **§13.1 — Session grain: follow the failure forward.** One session spans an evolving failure: when fixing node A reveals its dependency B, the *same* session continues on B. `error_key` is the seed identity, and a moved failing node counts as *progress* (§5.4), not a new session. This preserves the cross-node debugging context (the agent knows it just fixed A) that a fresh-per-node session would throw away.
- **§13.2 — Transcript home: the `attempts` axis.** On session end (resolved or giveup), the structured `steps` history is persisted onto the target node's `attempts` axis — durable, `to_dict`-serialized, queryable graph state, and the schema-native home the repair path leaves empty today. The `RunTrace` still records the coarse `PatchGateRecord` for run-level observability; the two are complementary.
- **§13.3 — Per-error hard turn cap: 15.** Bounds probe/patch spirals within a single session regardless of the progress signal. Tunable; revisit against real repos once measured.
```
