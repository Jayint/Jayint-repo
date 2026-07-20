# EnvState v1 — Three Roles, One Grounded Map

**Date:** 2026-06-09
**Status:** Design approved, ready for implementation plan
**Goal:** Replace the confusing multi-channel maintainer/planner/worker contract with a simple, understandable system: three single-purpose agents that share one grounded world-model map.

---

## 1. The picture

```
            ┌───────────────────────── WORLD-MODEL MAP ─────────────────────────┐
            │  base: python:3.12, workdir /app        build_system: poetry      │
            │  required:  [deps/tools from manifests]  installed: [confirmed]    │
            │  repo_layout: tests/, src/, edsl/        open_problems: [...]      │
            │  progress: base✓ runtime✓ deps… tests✗   done_flag: false          │
            └───────────▲───────────────────────────────────────────▲───────────┘
                        │ (writes — single writer)         (reads)   │ (reads)
                        │                                            │
                  ┌─────┴──────┐                              ┌──────┴───────┐
                  │ MAINTAINER │                              │   PLANNER    │
                  │  (LLM ×1   │                              │  (LLM ×1     │
                  │ per cycle) │                              │ per cycle)   │
                  └─────▲──────┘                              └──────┬───────┘
                        │ task_report                                │ scoped task
                        │ (cmds+rc+output)                           │ + done-criteria
                        │                                            ▼
                        │                                   ┌─────────────────┐
                        └───────────────────────────────────│   BUILD AGENT   │
                                                            │ (LLM mini-ReAct │
                                                            │  local budget)  │
                                                            └────────┬────────┘
                                                                     │ shell
                                                                     ▼
                                                            ┌─────────────────┐
                                                            │ SANDBOX (Docker)│ → ActionLedger
                                                            └─────────────────┘

  one cycle:  PLANNER reads map → emits task | DONE | GIVEUP
              └─task→ BUILD AGENT runs local loop (fix process bugs) → task_report
                       └→ MAINTAINER folds report into new map → back to PLANNER
              DONE (map.done_flag, i.e. `pytest --collect-only` passed)
                       └→ HARNESS finalizes → Synthesizer replays ledger → Dockerfile → EBSR
```

---

## 2. The three roles (plain English)

Each role does **one** thing and has **one** output. Nothing else.

### PLANNER — thinks globally, owns "stop"
- **Reads:** the map + the fixed goal ("make `pytest --collect-only` pass").
- **Emits one of:** a scoped **Task** for the build agent, or **DONE**, or **GIVEUP**.
- **Owns:** sequencing, scope, and termination. It decides when an `open_problem` is *out of scope* (e.g. a runtime-only test dependency like `swift` that does not block collection) and routes around it instead of chasing it.
- **Never** runs shell commands.

### BUILD AGENT — thinks locally, fixes process-level bugs
- **Reads:** one Task (sub-goal + done-criteria + the facts the planner hands down).
- **Does:** a small ReAct loop (its own local budget) running real shell commands through the sandbox to accomplish the task — fixing local/process problems (a failed install, a missing flag, a wrong path).
- **Emits:** a **task_report** = the commands it ran (+ rc + key output), a final status (`done` | `blocked`), and one line of "what I learned / why blocked".
- **Never** decides global strategy or termination. If it cannot finish locally within its budget, it reports `blocked` and escalates upward — it does not wander.

### MAINTAINER — keeps the map honest
- **Reads:** the current map + the build agent's last task_report.
- **Emits one thing:** the **new map**.
- **Rule that keeps it grounded:** it records only what the command results actually demonstrate (an install that exited 0, an import that worked, a failure traceback). It interprets failures into `open_problems` with a suspected layer. It **does not** invent `installed`/`required` facts that the output did not show. It sets `done_flag = true` when a `pytest --collect-only` command in the report exited 0.
- **Single writer:** only the maintainer writes the map; planner and build agent read it.

---

## 3. The data shapes (the whole contract)

This is the entire interface between the three roles. Everything else is internal. Types are frozen dataclasses (immutable; a new map is produced each cycle).

### The map (shared state, written only by the maintainer)

```python
@dataclass(frozen=True)
class Fact:
    name: str               # "flask", "pytest", "libpq-dev"
    detail: str = ""        # version / note, taken from real output

@dataclass(frozen=True)
class OpenProblem:
    signature: str          # short id, e.g. "ModuleNotFoundError: psycopg2"
    interpretation: str     # what the maintainer thinks it means
    layer: str              # base | system | runtime | deps | build | tests
    out_of_scope: bool = False   # set by the planner when it routes around it

@dataclass(frozen=True)
class WorldModelMap:
    base_image: str
    workdir: str
    language: str                       # "python 3.12"
    build_system: str                   # "poetry" | "pip" | "hatchling" | "unknown"
    repo_layout: tuple[str, ...]        # key dirs/files (tests/, src/, pyproject.toml)
    required: tuple[Fact, ...]          # declared by manifests (not yet verified)
    installed: tuple[Fact, ...]         # confirmed present from real command results
    open_problems: tuple[OpenProblem, ...]
    progress: dict[str, bool]           # {base, system, runtime, deps, build, tests}
    done_flag: bool = False             # True once pytest --collect-only passed
    notes: tuple[str, ...] = ()         # durable cautions the maintainer wants kept
```

### The task (planner → build agent)

```python
@dataclass(frozen=True)
class Task:
    goal: str               # one concrete sub-goal: "install project deps from pyproject"
    done_when: str          # checkable: "pip install exits 0 and `python -c import edsl` works"
    layer: str              # which stack layer this targets
    facts: tuple[str, ...]  # relevant map facts handed down (so the agent doesn't re-discover)

@dataclass(frozen=True)
class PlannerDecision:
    action: str             # "task" | "done" | "giveup"
    task: Task | None = None
    reason: str = ""        # why, for done/giveup
```

### The task report (build agent → maintainer)

```python
@dataclass(frozen=True)
class CommandRecord:
    cmd: str
    rc: int
    output: str             # truncated salient output

@dataclass(frozen=True)
class TaskReport:
    task_goal: str
    status: str             # "done" | "blocked"
    commands: tuple[CommandRecord, ...]
    learning: str           # one line: what was learned / why blocked
```

That's it. No `probe_requests`, no `diagnose_requests`, no `candidate_requirements`, no `open_failure_updates`, no certify/Evidence/ACL. Three messages: **Task**, **TaskReport**, **WorldModelMap**.

---

## 4. The loop

### Top-level (orchestrator)

```python
map = initial_map(base_image, repo_structure)     # from ImageSelector + repo tree, done_flag=False
for cycle in range(MAX_CYCLES):                    # default 12
    decision = planner.decide(map)                 # 1 LLM call
    if decision.action in ("done", "giveup"):
        break
    report = build_agent.run(decision.task)        # mini-ReAct; executes via sandbox; appends to ActionLedger
    map = maintainer.update(map, report)           # 1 LLM call → new map
    if map.done_flag:                              # ← hard stop the instant the gate is met
        break
finalize(map)                                      # reuse existing build/finalize path
```

**Termination guarantee.** The orchestrator finalizes the instant `map.done_flag` becomes true (right after the maintainer update) — it does **not** wait for any agent to remember to say "Final Answer." This is the structural fix for the Arm-A "safari reached the gate but never committed" failure. The planner's `done` is a secondary stop (when it judges the goal met).

### Build agent (the mini-ReAct)

```python
history = []
for step in range(LOCAL_BUDGET):                   # default 8
    thought, action, finished = build_agent_llm(task, task.facts, history)
    if finished:                                   # agent believes the task's done_when is met
        return TaskReport(task.goal, "done", history, learning)
    success, output = sandbox.execute(action)      # reuse Sandbox + preflight guardrails
    action_ledger.append(action, rc, output)       # global ledger = Dockerfile source of truth
    history.append(CommandRecord(action, rc, output))
    if stuck(history):                             # FIXED guard — see §6
        return TaskReport(task.goal, "blocked", history, learning)
return TaskReport(task.goal, "blocked", history, "ran out of local budget")
```

The build agent only judges its **own task's** `done_when`. Global "are we finished?" is always the planner/`done_flag`'s decision, never the build agent's.

---

## 5. Termination & build (reused, unchanged)

The proven "execute → record → Dockerfile" spine is kept as-is:

- **ActionLedger** records every executed command — the source of truth for the Dockerfile.
- **Done condition:** `pytest --collect-only -q --disable-warnings` exits 0 (the Repo2Run / EBSR gate). The maintainer sets `done_flag` when it sees this in a report.
- **Dockerfile synthesis:** the existing Synthesizer replays the mutating ledger steps into retry-wrapped `RUN` layers (it already builds clean — verified on microsearch). No change.
- **Finalize/verify path** (`_auto_finalize_from_verified_tests`, recipe synthesis, Dockerfile write, cleanroom): reused. The only rewire is the **trigger** — it fires on `map.done_flag` rather than a worker's "Final Answer."

---

## 6. What changes in the codebase (blast radius)

Concept is new; code is contained. Keep the proven body, rewrite the orchestration brains, delete the confusing surface.

| Disposition | Files | Notes |
|---|---|---|
| **Reuse ~unchanged** | `src/sandbox.py`, `src/image_selector.py`, `src/envstate/ledger.py`, `src/synthesizer.py`, `src/envstate/synthesis.py`, `src/verification_bundle.py`, `src/envstate/cleanroom.py`, `src/envstate/llm_response.py` | The execute → record → replay-ledger → Dockerfile → finalize spine. Untouched. |
| **Rewrite** | `maintainer.py` (5 channels → 1 map output), `supervisor.py` → **Planner**, `worker.py` + `fullstate_worker.py` → **Build agent**, `orchestrator.py` (new loop), `types.py` (snapshot → flat `WorldModelMap`) | Small files; salvage the layered prompt, action-parsing, and a fixed interruption guard. |
| **Modify (glue in `agent.py`)** | `run()` dispatch → single `_run_v1`; `_build_observer` → "fold task_report into map"; finalize **trigger** → `map.done_flag` | ~3 methods, not the whole file. |
| **Delete** | `src/envstate/probes.py`, `src/envstate/acl.py` | All probe/certify/Evidence/ACL machinery + the `name`-key bug. |
| **New** | `WorldModelMap`/`Task`/`TaskReport` types; a small map-update helper; the build-agent module (mostly a merge of the two worker files) | A few hundred lines of new contract code. |
| **Harness** | `run_repo2run_benchmark.py` arm selector | The 4 arms collapse: **v1 is "the system"; Arm 0 (bare ReAct) stays as the baseline.** Arms A/B/C retire. |

**Two known failures fixed for free:** safari's "reached the gate but never committed" (now `done_flag` finalizes structurally) and edsl's "killed by a procedural rejection" (the fixed guard in §6 below).

### The one carried-over fix
The build agent's "stuck" guard must **not** count non-mutating preflight *rejections* (commands the sandbox refused before execution — they change nothing) as failures, and should allow one self-correction before firing. This is what prematurely killed edsl at action 3. Applies wherever the build agent's local loop runs.

---

## 7. Comparison to the original (Arm 0, bare ReAct)

| | Arm 0 (original) | v1 |
|---|---|---|
| Agents | 1 LLM (planner-in-disguise) | 3 single-purpose LLMs |
| State | raw chat history | one compact grounded map (~20 lines) |
| Loop | flat 180-step ReAct | planner cycles (default 12) × build-agent local budget (default 8) |
| Global vs local | same agent does both | planner = global, build agent = local |
| "Done" | agent emits "Final Answer" | property of the map (`done_flag`), finalized by the orchestrator |
| Failure modes | loses the thread over long runs; no global re-plan | planner re-plans each cycle; can't wander globally |

Arm 0 stays as the comparison baseline. v1 replaces Arms A/B/C.

---

## 8. Defaults & knobs

| Knob | Default | Meaning |
|---|---|---|
| `LOCAL_BUDGET` | 8 | build-agent shell actions per task before it must report `blocked` |
| `MAX_CYCLES` | 12 | planner cycles per run (≈ ≤96 total actions — cheaper than Arm-0's 180) |
| done condition | `pytest --collect-only -q --disable-warnings` rc 0 | the EBSR gate |
| model | `minimax/minimax-m2.7` for all three roles | one model for v1; can specialize later |

---

## 9. Error handling

- **LLM empty / unparseable output:** retry once (reuse `complete_with_retry`); on second failure, treat as a no-op cycle and let the planner re-decide.
- **Build agent `blocked`:** maintainer records an `open_problem`; planner re-plans next cycle (mark `out_of_scope`, try a different task, or `giveup`).
- **Sandbox command failure:** an ordinary observation the build agent handles locally within its task.
- **`MAX_CYCLES` exhausted without `done_flag`:** fall back to `_auto_finalize_from_verified_tests` (in case a collect-only passed but the flag was missed); otherwise `dockerfile_missing`.

---

## 10. Testing (≥80% coverage)

- **Unit:** map-update merge logic (facts grounded to outputs, `done_flag` set on collect-only rc 0); planner decision parsing (task/done/giveup); build-agent action parsing + `finished` detection; the fixed "stuck" guard ignores non-mutating rejections.
- **Integration:** one full run on a clean repo (microsearch-like) produces a Dockerfile; a `blocked`-task → planner re-plan path; an out-of-scope problem (a `swift`-style runtime-only dep) → planner marks it out_of_scope and still finalizes on collect-only.
- **E2E:** the existing Repo2Run benchmark harness, v1 vs Arm 0.

---

## 11. Non-goals (v1 / YAGNI)

- No host-verified probes / certified-fact ACL (the LLM maintainer is trusted, grounded by "record only what the output shows").
- No revision-scoped staleness, no Evidence objects.
- No long-term memory or observation-compression coupling (separate, optional).
- No per-command maintainer calls (cost was the Arm-A problem — once per cycle only).
- Specializing models per role, parallel build agents, or a blackboard-style leaderless loop — possible v2, explicitly out of scope now.
