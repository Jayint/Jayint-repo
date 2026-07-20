# DESIGN: Environment-State Maintainer + Planner (DockerAgent)

> **Status:** design spec / handoff. Not yet implemented.
> **Audience:** a fresh agent or engineer continuing this work with no prior conversation context.
> **Target codebase:** `/Users/john/rat-bench-integration` ("DockerAgent").
> **Companion diagrams:** `docs/rat-vs-dockeragent-architecture.html`, `docs/rat-plan-loop.html`.

---

## 0. TL;DR

DockerAgent today is a **single-step ReAct loop** that repairs Docker environments by **reactive trial-and-error**: the planner sees the last raw error log, emits one bash guess, observes, guesses again. It re-derives "what is true about the environment" from the raw transcript every turn. This is the dominant failure mode in the literature (MultiDockerEval: Docker-build/env errors ≈ 36% of failures; DOCKSMITH: ~62% of repairs are heuristic log-pattern, not principled).

**The new design:** split the agent into a **planner (actor)** and an **environment-state maintainer**, sharing one **typed, declarative environment-state artifact** (`EnvState`). The maintainer is **mostly deterministic host code**; an LLM is allowed only to *propose* requirements as hypotheses. The single safety rule:

> **Only host code running a probe may write `status=PRESENT`/`MISSING` + `Evidence`. The LLM may write only `status ∈ {REQUIRED, UNKNOWN}, source=LLM_GUESS, evidence=None`.**

This makes "silent false pass" (a confident-but-wrong "it works") impossible by construction, not by prompt discipline. It is the answer to the question "how do we get principled cross-layer dependency reasoning instead of log-pattern guessing."

**The accurate name is not "actor + maintainer LLM." It is "actor + host-maintained world model with an LLM proposer."**

---

## 1. Problem statement

Environment construction (turning a bare repo into an image where `pip install` + tests pass) is the most failure-prone step in repo-evaluation agents. The hard part is not writing the test command; it is the **cross-layer dependency chain**:

```
language package (requirements.txt)
  → is there a compatible wheel for (python_version, arch)?
      no → source build
            → needs compiler + native headers/tools (e.g. pg_config, libpq-fe.h, Python.h)
                  → provided by an OS/apt package (libpq-dev, python3-dev, libffi-dev)
                        → must be present in the chosen base image
                              → installed in the correct Dockerfile layer order (apt BEFORE pip)
```

Current agents (DockerAgent, RAT, RepoLaunch) all attack this reactively: run a command, read the error, pattern-match a fix, retry. Stronger LLMs improve *script* generation but not *env* errors, because env repair needs grounded cross-layer reasoning, not code synthesis.

**Goal:** make DockerAgent reason and act at the system-dependency level — proactive where possible, grounded (lookup not guess) on failure, and verified (every "fixed" claim proven by a probe) — without reintroducing the silent-false-pass that a naive state document creates.

---

## 2. Why the obvious alternatives are wrong (read before proposing them)

1. **RAT-style `plan.md` (a self-authored checklist).** Two fatal flaws:
   - **Procedural, not declarative.** `[x] install deps` is a diary of *what the agent did*; it cannot answer "is `libpq` present and how do I know," cannot be invalidated when a later step breaks it, cannot be queried for "what's still missing."
   - **LLM-authored + unverified → silent false pass.** An `[x]` is the model *asserting* reality with nothing checking it. (MultiDockerEval names false-pass as a top failure cluster.)

2. **A free second LLM "maintainer" that updates the environment artifact** (the tempting version of this design). This is **worse** than `plan.md`: a typed `provides:[libpq]` edge *looks like data* and invites downstream trust, while a checkbox is obviously suspect. A fluent maintainer will confidently write categorically-false edges (see the psycopg2-binary trap, §9). **Never put an LLM on the write path of certified state.**

3. **A standalone `__DIAGNOSE__` debug module bolted onto the reactive loop.** Necessary but not sufficient: it only fires on failure (no proactive pre-install), and its output dies in the transcript (gets trimmed out of context) with no durable home. Diagnosis needs a *state* to write into.

The right answer composes the good parts of all three: **declarative typed state (from #1 done right) + grounded diagnosis (from #3) + a host-verified probe as the only certifier (the fix for #2).**

---

## 3. Architecture: roles and the trust boundary

### Roles (note: only TWO are LLMs)

| Role | Kind | Job |
|---|---|---|
| **Actor (planner)** | LLM | Reads `EnvState`, decides the next ONE bash action. Interface unchanged from today. |
| **Maintainer** | **host code + gated LLM proposer** | After each action: deterministic parse of the observation; on failure/ambiguity, a gated LLM call *proposes* requirements + plan edits. Owns `EnvState`. |
| **Diagnose** | **host verb** (not an agent) | Grounded lookup: `apt-file search <header>` / `dpkg -S` / `pkg-config` / wheel-probe → which package provides a missing capability. |
| **Probe** | **host verb** (not an agent) | Capability-specific check with a stdout predicate. **The SOLE writer of `PRESENT`/`MISSING`.** |
| **Recipe compiler** | **host code** | Builds the final Dockerfile from *verified facts only*, then clean-room self-verifies. |

There is **no "diagnose agent" and no "probe agent."** Those are deterministic host functions. Diagnosis's *symptom-naming* sub-step (reading the log, "missing header `libpq-fe.h`") lives in the maintainer LLM; its *resolution* (which package) and *certification* (is it present now) are host code. The trust boundary is "code, not a model," not an org chart.

### The safety invariant (the entire design in one rule)

> **Only host code running a probe may write `status=PRESENT`/`MISSING` + `Evidence`.**
> The LLM may write only `status ∈ {REQUIRED, UNKNOWN}, source=LLM_GUESS, evidence=None`.

Enforced in code, not prompt:

```python
def apply_proposal(req: Requirement) -> Requirement:
    if req.status in ("PRESENT", "MISSING") or req.evidence is not None:
        raise TrustViolation("LLM may not write verified status/evidence")
    return replace(req, source="LLM_GUESS", evidence=None)
```

`PRESENT` has **no LLM-reachable write path**. A hallucination can over-suggest work (cheap); it can never fabricate a pass (the failure we're killing).

---

## 4. The shared artifact: `EnvState` (new `src/env_state.py`)

Frozen dataclasses (immutability house rule — every transition returns a new state, so turns are diffable/replayable).

```python
class Status(Enum):  REQUIRED; PRESENT; MISSING; UNKNOWN   # PRESENT/MISSING are PROVEN; others proposed
class Source(Enum):  PROBE; DIAGNOSE; LLM_GUESS; MEMORY    # planner trusts only PROBE/DIAGNOSE as fact

@dataclass(frozen=True)
class Evidence:                 # WHY we believe it — never an LLM assertion
    probe_cmd: str              # exact command, e.g. "test -f $(pg_config --includedir)/libpq-fe.h"
    rc: int
    stdout_predicate: str       # what we matched, e.g. "exists" | "^2\\.8\\.6" | "found"
    env_revision: int           # == agent._environment_revision at proof time
    container_id: str           # which container the proof ran in

@dataclass(frozen=True)
class Requirement:
    name: str                   # "libpq" | "libpq-dev" | "psycopg2" | "pg_config" | "libpq-fe.h"
    kind: str                   # NativeLibrary|Header|Tool|SystemPackage|Wheel|LanguagePackage|Service
    status: Status
    source: Source
    required_by: tuple[str, ...]
    suspected_provides: tuple[str, ...]   # LLM-proposed edges — NOT trusted until host-confirmed
    provides: tuple[str, ...]             # host-confirmed edges only
    evidence: Evidence | None             # None ⇒ status MUST be UNKNOWN/REQUIRED (TYPE INVARIANT)
    probe_hint: str | None                # how to probe it, e.g. "import psycopg2"
    already_tried: tuple[str, ...]        # anti-thrash: dead-end fixes

@dataclass(frozen=True)
class OpenFailure:
    signature: str              # normalized, e.g. "fatal error: libpq-fe.h: No such file"
    hypothesis: str | None      # LLM-proposed; NEVER promoted without diagnose+probe
    already_tried: tuple[str, ...]

@dataclass(frozen=True)
class EnvState:
    revision: int               # == agent._environment_revision
    base_image: str
    requirements: tuple[Requirement, ...]
    open_failures: tuple[OpenFailure, ...]
    plan_notes: tuple[str, ...]            # the maintainer's free-text plan lane (hypotheses, next steps)
```

**How this differs from RAT's `plan.md`:** declarative (needed/present/missing/why) not procedural; **host-owned** and re-rendered into the planner each turn like a HUD (so it cannot drift out of the transcript — it's regenerated, not stored as a trimmable message); every `PRESENT` carries machine `Evidence`; `source` separates hypotheses from facts; `env_revision` invalidation kills the stale-`[x]` failure.

---

## 5. The loop

```
 ACTOR (src/planner.py)
   │  reads EnvState rows (REQUIRED/MISSING + plan_notes) — NOT raw logs
   │  emits ONE bash action   [interface unchanged]
   ▼
 HOST EXECUTE (agent.py ~905–945)
   │  run action in target container; if mutating → bump _environment_revision,
   │  invalidate every PRESENT whose Evidence.env_revision < current  (coarse but SOUND)
   ▼
 DETERMINISTIC PARSE (host, cheap — observation_compressor SAFETY_*_PATTERNS)
   │  "Successfully installed X", "Requirement already satisfied", "collected N items"
   │  → marks the ACTION observed-successful. Does NOT mark the CAPABILITY present.
   │  rc==0 + clean signal → DONE this turn (NO LLM)
   ▼  rc!=0  OR  error markers  OR  ambiguous (residual only)
 MAINTAINER LLM (observation_compressor → ObservationInterpreter)   ← GATED, not every turn
   │  reads residual obs + prior EnvState
   │  → plan edits {hypothesis, already_tried, probe_hint}
   │  → state proposals [Requirement(status=REQUIRED/UNKNOWN, source=LLM_GUESS, evidence=None)]
   │     (apply_proposal() REJECTS any PRESENT/MISSING/evidence write)
   ▼
 DIAGNOSE (host verb) — grounded lookup, NOT a guess
   │  apt-file search <header/tool> → pkg ; dpkg -S ; pkg-config ; wheel-availability probe
   │  → fills suspected_provides / required_by, source=DIAGNOSE
   ▼
 PROBE (host verb, new __probe__) — SOLE writer of PRESENT/MISSING
   │  capability-specific check IN the target container at current env_revision, with a PREDICATE:
   │    HEADER:    test -f $(pg_config --includedir)/libpq-fe.h
   │    IMPORT:    /repo/.venv/bin/python -c "import psycopg2; print(psycopg2.__version__)"  ⇒ ^2\.8\.6
   │    CLI:       which pg_config ; pg_config --version
   │    PKGCONFIG: pkg-config --exists libpq
   │    SHLIB:     ldconfig -p | grep libpq
   │  → writes Requirement(PRESENT/MISSING, source=PROBE, Evidence{cmd,rc,predicate,env_revision,container_id})
   ▼
 RECIPE COMPILER (src/synthesizer.py — MUST be rewritten; see §8)
   │  consumes ONLY {status==PRESENT ∧ source∈{PROBE,DIAGNOSE}} bound to final env_revision
   │  emits RUN lines in dependency order, then CLEAN-ROOM self-verify (rebuild, re-run every probe)
```

### Write-permission table (the safety argument in one grid)

| Writer | next bash action | plan lane (hypotheses, already_tried, suspected edges) | `REQUIRED`/`UNKNOWN` + `LLM_GUESS` | `PRESENT`/`MISSING` + `Evidence` | recipe RUN lines |
|---|:---:|:---:|:---:|:---:|:---:|
| **Actor LLM** | ✅ | — | — | ❌ | — |
| **Maintainer LLM** | — | ✅ | ✅ | ❌ | — |
| **Host (parse / diagnose / probe / compile)** | — | — | ✅ | ✅ **only** | ✅ **only, from verified facts** |

### Two decisions stated flat

- **Failure-gated, NOT every-turn.** The maintainer LLM fires only on the residual (`rc!=0`, traceback, `command not found`, `fatal error: …`, or a long ambiguous observation). A clean `Successfully installed … rc=0` is handled by the deterministic parser alone. This is both a cost decision and a correctness one (nothing to diagnose on a clean install; the capability still isn't `PRESENT` until a probe says so).
- **The maintainer is an UPGRADE of `src/observation_compressor.py`, not a new conversational agent.** That module already runs after each action, already has a deterministic pre-pass + an LLM pass, already gates on need. We change its output type from `compressed_str` to `{state_proposals, plan_edits}`. We are re-purposing the LLM that already fires, not adding a third.

---

## 6. Diagnosis is split (why it isn't a separate agent)

Diagnosis has three sub-steps; only one is LLM-shaped:

1. **Name the symptom** ("missing header `libpq-fe.h`") — LLM-ish; the maintainer is reading the obs anyway → **lives in the maintainer** (`probe_hint`).
2. **Resolve it** ("which package provides it?") — **host lookup**, never LLM. The LLM guesses from memory (`Python.h → python3` ✗, should be `python3-dev`); `apt-file search` queries the real index and is right every time.
3. **Certify it** ("is it present now?") — **host probe**; the sole writer of `PRESENT`.

Gray zone: when `apt-file` returns several candidate packages, a *small* LLM call may pick among the **grounded shortlist** (not invent from memory). It still never certifies — the probe does.

---

## 7. Existing code hooks (VERIFIED — with honest corrections)

These were checked against the actual code (and independently by an external Codex review). Some earlier claims were too generous; corrections noted so you don't over-assume "incremental."

| Claim | Reality |
|---|---|
| `agent.py:_finalize_verification_from_agent_report` (~1727, rejects at ~1756) rejects agent-claimed verification unless commands were observed succeeding | **True**, and it's the no-false-pass invariant in miniature — generalize it. **BUT** it does **not** itself enforce `env_revision` (see next row). |
| `src/verification_bundle.py:derive_supported_verification_bundle` | **Ignores `env_revision`** — can support a command that succeeded *before* a later mutation invalidated it. **This is a latent false-pass bug to fix independently (Codex must-fix).** |
| `agent.py:_environment_revision` (~1658) + `_invalidate_verification_group` (~1659) | **Real**, but scoped to *test verification*, not arbitrary `EnvState` facts. Reuse the mechanism; it is not yet a general stale-downgrade. |
| `src/memory_manager.py:is_valid_memory` (~950) | **Correct** — it's only a non-empty-string check, so a wrong fact ("Python.h → python3") passes and poisons cross-run RAG. |
| `src/memory_manager.py` `confidence` (~35) | Appears only in the **LLM relation prompt schema**, read nowhere as an enforced field. No `evict`/`expire`/`ttl` anywhere. "Declared but unused" is directionally right. |
| `src/observation_compressor.py` | 2-tier deterministic + LLM, **but does NOT emit typed deltas yet** — extending it is the work, not a freebie. |
| `src/recipe_repair.py` (~85 `extract_missing_modules`, ~165 mapping) | **Python-import/package only** — calling it proto-diagnose for the native/system boundary is generous; it must be extended. |
| `src/artifact_verify.py:classify_test_execution` (~121) | Real and useful, but classifies **test execution**, not package-capability probes — reuse the verdict shape, not the logic. |

**Net:** the "reuse what's there" story is real but thinner than it looks. There are genuine seeds (`_environment_revision`, the 1727 guard, the 2-tier compressor, the python-only repair) but each needs generalizing.

---

## 8. v1 build order (incremental; ship in this sequence)

> If forced to ship one thing first, ship **the synthesis gate** — without it every other piece is theater.

1. **Recipe compiler on verified facts only (`src/synthesizer.py`) — DO FIRST, non-negotiable.**
   Today the Dockerfile is built from trajectory *text* (`_build_recipe_trajectory`, `summarize_setup_log_for_recipe`, `_collect_trajectory_first_build_commands`), so the LLM can inject an unproven install line or drop a required one (this is the `dockeragent-synthesizer-drops-installs` failure). Rewrite `synthesize()` to emit a RUN line **only** for a setup command causally upstream of a `Requirement{status==PRESENT, source∈{PROBE,DIAGNOSE}}` with consistent `Evidence.env_revision`, then **clean-room self-verify** (rebuild from the Dockerfile alone, re-run every probe). This is where false-pass becomes a *type error at the compiler boundary*.

2. **Typed `EnvState` + write-ACL (`src/env_state.py`, new).** Frozen dataclasses + `apply_proposal()` (the `TrustViolation` assert). Pure, no Docker, unit-testable RED→GREEN.

3. **Promote `observation_compressor.py` → `ObservationInterpreter`.** Same gated call, new output: run-scoped `CandidateRequirement[]` (`source=LLM_GUESS`, status ∈ {REQUIRED, UNKNOWN} only, `probe_hint`, `evidence_span`) + `plan_edits`. **Run-scoped only** — never persisted as facts.

4. **Fixed 5-probe set + `__probe__` host verb (`agent.py` dispatch + a probe module).** Exactly five kinds (IMPORT / CLI / HEADER / PKGCONFIG / SHLIB), each with a stdout **predicate** (not just `rc==0`), run **in the target container** at **current `env_revision`**, tied to the failing build path. Predicates answer "exit 0 ≠ semantic proof"; `env_revision`+`container_id` answer evidence-misattachment.

5. **`env_revision` in `derive_supported_verification_bundle` + generalized invalidation (`agent.py`).** You cannot compute precise dependency edges — so **don't**. Keep the coarse, *sound* rule: any env-mutating action bumps `_environment_revision` and invalidates every `PRESENT` with stale `env_revision`, forcing a cheap re-probe (`already_tried` stops the actor thrashing while it reconfirms). **Coarse-but-sound beats precise-but-wrong.** Fix `derive_supported_verification_bundle` to enforce `env_revision` equality.

6. **Memory as proposals, not facts (`src/memory_manager.py`).** Re-inject cross-run requirements under a **tight context key** `(base_image, distro, py_version, arch, wheel_vs_source)` as `source=MEMORY, status=UNKNOWN` — must be **re-probed in the new container** before certifying. Add `source` tagging + retraction on consult-then-fail. Make the dead `confidence`/missing-TTL real.

---

## 9. Traps and guardrails

**The psycopg2-binary poison (the canonical trap).** `pip install psycopg2` fails on `pg_config not found`; the LLM "fixes" it with `pip install psycopg2-binary` (vendors libpq, installs no headers). A naive maintainer writes `Wheel(psycopg2-binary) --provides--> libpq`. That edge is *situationally true, categorically false*. On the next repo pinning source `psycopg2==2.8.6`, a state agent that trusts it **skips `apt-get install libpq-dev`** — a regression a stateless agent would never make. Guardrails: (a) only `PROBE`/`DIAGNOSE` edges are trusted, never `suspected_provides`; (b) memory keyed by `wheel_vs_source` + arch; (c) edges retractable on consult-then-fail.

**Two-LLM world-model drift + unsound under-invalidation (the trap that makes two LLMs WORSE than one).** If both the actor and an LLM maintainer hold world models, they desync and burn turns litigating reality. Worse, an LLM maintainer's "surgical" invalidation ("this pip install only touched X") is *unsound* — a `pip install` silently upgrades setuptools / shadows a binary / swaps a native lib, so it keeps a `PRESENT` the mutation broke. **Guardrail: exactly one writer of certified state (the host); invalidation stays the host's coarse, sound rule.** Neither LLM can write `PRESENT`, so they cannot drift on ground truth — only on guesses the probe refutes and drops.

**The riskiest assumption (from Codex).** That host probes can be made cheap, deterministic, and *semantically strong* enough to certify what the planner needs. `exit 0` is not proof; `pg_config --version` proves a binary exists, not that a pinned source build links. **Mitigation:** probes must be capability/version/interpreter-specific and reproduce the failing path (re-run the real `pip install` + import in the target venv), not a cheap proxy. If probes are weak, the typed state is just "false confidence with better formatting." Treat probe design as the core research risk.

---

## 10. Evaluation (tie to the literature)

Run on the RAT harness as a **three-arm comparison**, planner + sandbox held fixed:
1. transcript-only ReAct (today's DockerAgent baseline),
2. RAT-style procedural LLM-authored `plan.md`,
3. this design (declarative, host-verified).

Metrics (per MultiDockerEval / DOCKSMITH categories):
- **Docker-build / env-error failure %** (the ~36% bucket) on a native-dep slice (psycopg2, lxml, cryptography, mysqlclient, Pillow, numpy/scipy), pure-Python repos as no-regression control.
- **Env-error resolution rate** (DOCKSMITH's regressed metric) — read from probe-pass rate.
- **Principled-vs-heuristic repair ratio** — a repair is principled iff probe-passed AND provided the named capability.
- **False-pass rate** — the headline; should approach zero by construction.
- **Mean fix-attempts per env failure** — the trial-and-error counter; target ~4–6 → ~1.

**Project caveats (from prior memory):** re-run RAT only after the harness `docker cp` path fix (current baselines are hollow placeholder passes on empty repos); report **test-collection** pass, not build-success (build-success overstates — the synthesizer hollow-success problem, ~"56% vs ~16%").

Ablations: proactive-only, +verification, +diagnose/grounding, +gate, warm-vs-cold cross-run memory (transfer test on a *different* repo with the same package isolates transfer from memorization).

---

## 11. Research framing (the one defensible claim)

Not "we added a diagnose tool + verifier + graph + memory" (four ablations, no spine). The unit of contribution is:

> **The typed boundary where only the host may certify reality.** An explicit, host-verified, declarative environment-state artifact, re-rendered each turn, makes "silent false pass" a *type error* rather than a metric — recovering the env-resolution that DOCKSMITH's training couldn't, at inference time, model-agnostically (important for the OpenRouter/multi-provider setup), and making the hollow build-success-but-wrong-environment recipe representationally impossible.

---

## 12. START HERE (for the continuing agent)

1. Read this doc + the two HTML diagrams (`docs/rat-vs-dockeragent-architecture.html`, `docs/rat-plan-loop.html`).
2. Read the real code at the hooks in §7 to confirm they still match (line numbers may drift): `agent.py` (loop ~905–956; latent state ~1658–1766), `src/planner.py` (~160 system_prompt, ~410 trim), `src/observation_compressor.py`, `src/synthesizer.py`, `src/memory_manager.py` (~35, ~950), `src/artifact_verify.py` (~105–140), `src/recipe_repair.py` (~85, ~165), `src/verification_bundle.py` (~21).
3. **First PR (independent, low-risk, real bug):** make `derive_supported_verification_bundle` enforce `env_revision`. This is a latent false-pass in the current code regardless of the rest of this design.
4. **Then build in the §8 order**, TDD. Start with `src/env_state.py` + `apply_proposal` (pure, no Docker) and the `synthesizer.py` verified-facts compiler.
5. **Hold the line on the invariant:** if any change lets an LLM write `PRESENT`, you have rebuilt the thing this design exists to kill. The `apply_proposal` `TrustViolation` assert is the canary — never weaken it.

### Open questions to resolve during implementation
- What exactly is a *semantically strong* probe for the psycopg2/lxml **source-build** case (vs the cheap import proxy)? This is the riskiest assumption — design it explicitly.
- Should diagnosis's candidate-package disambiguation (when `apt-file` returns N packages) be a small gated LLM call over the grounded shortlist, or a deterministic heuristic? (Lean: gated LLM over shortlist; still never certifies.)
- Eviction policy for cross-run memory edges (TTL by run count? demote-on-contradiction?) — needed to stop one bad run poisoning future runs permanently.

---

*Provenance: synthesized from a multi-session analysis of RAT (RunAnyThing), RepoLaunch, and DockerAgent, plus an external Codex code review that verified the §7 hooks and surfaced the §9 riskiest-assumption and the recipe-compiler must-fix.*

---

## 13. Contract Graph V1 (additive, opt-in)

The v1 three-role system (Planner / BuildAgent / Maintainer) acquired a parallel **contract graph** reasoning layer in June 2026, implemented as `src/envstate/contracts/`. It is strictly additive: the existing `done_flag` gate, the `_resolve_v1_verified_test_run` final authority, and arms A0/A1 are bit-for-bit unchanged.

**What it is:** a typed, JSON-serializable graph (`Node` / `Edge` / `ContractStatusEvent`) that rides inside `WorldModelMap.contract_graph`. The host projects facts it already owns (probe results, ledger events, manifests, `open_problems`) into grounded nodes; the Maintainer LLM contributes only semantic nodes (`Contract`, `Validator`, edges) via a validated patch. The Planner sees a `## Contract Graph` section each cycle and may emit `target_node_ids` + a `transition_proposal` alongside its task, as well as an advisory `done` that the host gate must confirm before stopping.

**Why it does not violate this doc's safety invariant:** `satisfied` status events require a passing `CommandExecution` node as evidence (validated host-side before application). The Maintainer patch is validated with `validate_patch(scope="maintainer")` — on any violation the patch is silently dropped and the flat fields still apply. The only path to a `satisfied` goal contract is a passing pytest run observed by the host, exactly as before.

**Entrypoint:** select `--arm v1g` in `run_repo2run_benchmark.py` or `run_rat_benchmark.py` (or set `DOCKERAGENT_ENABLE_CONTRACT_GRAPH=1`). Telemetry is written to `setup_logs/contract_graph.jsonl`.

**Full design and per-cycle ordering:** see `docs/DESIGN-contract-graph-v1.md`.

**Implementation:** `src/envstate/contracts/` subpackage (12 modules: `schema`, `nodes`, `graph`, `ids`, `patch`, `validation`, `apply`, `projection`, `goals`, `validators`, `render`, `__init__`). Tests: `tests/test_contracts_*.py`.
