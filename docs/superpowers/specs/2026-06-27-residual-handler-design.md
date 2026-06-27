# Residual Handler — Topological-Wave Executor Delta

**Date:** 2026-06-27
**Status:** Design — converged via design review; **assumes `2026-06-27-topological-wave-executor.md` is fully implemented.**
**Lineage:** `2026-06-26-graph-scheduled-agent-architecture-design.md` → *unified executor loop delta* → `2026-06-27-topological-wave-executor` → **THIS (residual handler)**
**Extends, does not supersede:** adds the mechanism for handling *graph inaccuracy* to the implemented executor. Nothing in the wave executor changes; this defines what happens after a wave fails or the frontier exhausts with tests still red.

---

## 1. Problem: the graph is not fully accurate

The implemented executor terminates on a two-oracle rule:

```
DONE  ⇔  deterministic frontier exhausted   ∧   test gate green
         (graph certifies NECESSARY)             (tests certify SUFFICIENT)
```

The graph is built up-front from static scan + uv closure + native probe + ldd (NECESSARY-by-construction). That model is **structurally incomplete** in two ways the executor will hit at runtime:

1. **Wave-failure residual** — a reciped node the batch wave could not certify (`rc ≠ 0`). The graph knew the *package* (psycopg2) but not its *cross-layer dependency* (libpq) — the bare-import/ldd probe didn't surface it (design `2026-06-27 §9`: the test-exercised native tail).
2. **Sufficiency residual** — the frontier is exhausted, every node is SATISFIED, but `pytest` is red because a requirement was **never modeled at all** (e.g. `pytest-asyncio`, never a graph node).

Both are *residuals*: the gap between the graph's model and observed reality. The implemented executor (§4) has **two separate repair paths** for these — `repair(culprit)` and `repair_sufficiency(context)`. This spec **folds them into one residual handler** and specifies exactly how a residual becomes graph state (or an honest give-up).

> **Vocabulary note.** "Residual" already has a precise meaning in the codebase: what `schedule._is_actionable` surfaces *after excluding the emittable frontier* (executor §7 — "excludes emittable so the LLM sees only the residual"). This spec uses "residual" in the same sense, extended to the two failure sources above.

---

## 2. Thesis: one handler — *certified delta* or *honest give-up*

> **OBSERVE converts every residual into either (a) a host-certifiable graph delta (node + edge, `DiscoveredBy.RUNTIME`, state MISSING/UNKNOWN) that the deterministic wave installs and the host certifies, or (b) an honest give-up (`done_flag` stays false) when the residual is not an actionable environment obligation. The loop then resumes and re-derives the frontier.**

This is the already-landed runtime-feedback loop (`runtime_classify.py` + `runtime_ingest.py`) **promoted from a side-channel to *the* residual handler** — one mechanism replacing both §4 repair branches, routed through the single OBSERVE writer (executor §4/§8).

The LLM's job on a residual is **diagnosis that yields a delta**, never the install and never a fact. The host installs (deterministic wave) and certifies (`certify_refresh`). This keeps the executor's authority table intact: *Graph = what-next, Agent = how (diagnose), Host = whether.*

---

## 3. The three guardrails

"Simply add each error to the graph and resume" is the right instinct but unsound without three constraints. These are the spec's core requirements.

### G1 — Add a *delta*, not an error

The handler's product is a **typed, host-certifiable `Node` + `Edge`**, not a raw error string and not a live install. Example: `pytest-asyncio` missing →

```python
Node(id="pkg:pytest-asyncio", type=PACKAGE, layer=PIP,
     discovered_by=RUNTIME, state=MISSING,
     check_command="python3 -c 'import pytest_asyncio'")
```

The node is **not trusted until the host's `check_command` certifies it** — this is the existing invariant "no node exists on LLM authority alone," now load-bearing: a hallucinated node simply fails to certify and never goes SATISFIED. The host is the backstop, so the diagnosis layer (regex or LLM) can be as general as it likes.

The wave-failure case is the one exception to "don't install live": the fix there is genuinely experimental (`apt-get install -y libpq-dev`, re-check, maybe retry), so the host-first repair loop (executor §5) runs live — and the discovered node + edge is **recorded afterward** as the byproduct (see G2 + §7). Same OBSERVE write, different routing.

### G2 — Prefer the *edge*

The value of `psycopg2: pg_config not found` is **not** "libpq is a node" — it's the **edge** `pkg:psycopg2 --REQUIRES--> syslib:libpq`. Only the edge carries ordering power: `partition()` will not surface psycopg2 as emittable until libpq is SATISFIED, so the *next* run installs libpq first, deterministically, 0 LLM tokens. A floating node gives no ordering and discards the cross-layer chain that is the project's contribution. **A residual handler that adds only nodes builds a bag; one that adds edges builds the DAG.** See §7 for how attribution makes the correct edge possible.

### G3 — The honest give-up branch

Not every residual is an environment obligation. A real assertion bug, a flaky network test, or a config/service issue produces a residual that **no node fixes**. Blindly adding it either (a) lets the diagnosis layer hallucinate an env node to "explain" a repo bug — graph pollution + burned budget — or (b) loops forever re-adding something that won't certify.

The classifier therefore sorts **every** residual into one of three buckets, and only the first becomes a node:

| Bucket | Examples | Action |
|---|---|---|
| **Actionable env obligation** | `ModuleNotFoundError`, missing `lib*.so` | classify → node + edge → wave/LLM installs, host certifies |
| **Env-but-advisory** | missing env var, DB not running | CONFIG/SERVICE → reactive sufficiency-repair only; never auto-installed |
| **Non-env** | assertion bug, flaky network, logic error | `REPO_BUG`/`FLAKY`/`UNKNOWN` → **honest give-up**; no node |

**Requirement:** if a residual does not map to an actionable, host-certifiable environment obligation, the handler **records it as diagnosed-out-of-scope and gives up honestly** (`done_flag` stays false; reason carries the diagnosis). Adding-to-the-graph is the path for environment residuals *only*; everything else exits.

**A non-env residual is the scope boundary, not a failure.** The agent's job is environment *setup*. When the frontier is exhausted, every node is certified, and the test gate is *still* red because of a repo bug, the truthful outcome is "the environment is built; the remaining failure is not environmental" — `done_flag` false, reported honestly. The two-oracle gate already encodes this: SUFFICIENT-red ∧ frontier-clean ∧ no-addable-node **is** an honest not-done, and it aligns with the honest-success metric (`ebsr ∧ pass_rate ≥ 0.8`). This is the direct descendant of the collect-only / hollow-success line of work — the handler must never convert "I can't fix this" into a fake success.

**Tunable — which way to lean on the borderline.** Because the host `check_command` makes a wrongly-added env node *inert* (it can't certify → the divergence stop catches it, §8), a false "it's env" is cheap, whereas a false "it's a repo bug" is a *missed fix*. The borderline call (an env gap I don't recognize vs. a repo bug) thus has an asymmetric cost. The §6 prompt currently leans conservative ("no real check → `UNKNOWN`"); on RAT/repo2run-style suites the unmodeled tail is mostly real env gaps, so leaning slightly toward *try-a-node-and-let-the-host-reject-it* — bounded by the divergence stop and turn budget — likely recovers more, at the cost of a few wasted certifies.

---

## 4. The unified residual loop

```
loop until done_flag, or turn_budget == 0:

  ── SELECT + EXECUTE (unchanged from the implemented executor) ──────────────
  wave = next_deterministic_wave(graph)
  if wave not empty:
      result = run_wave(wave)                 # deterministic · no turn · ~0 tokens
      if not result.ok:
          culprit = isolate(wave, result)     # §7 — per-node re-run → single owner + clean error
          result  = repair(culprit, max=5)    # host-first; turn_budget -= 1
  elif test_gate_passes():
      done_flag = True; break
  else:
      result = repair_sufficiency(context, max=5)   # host-first diagnosis; turn_budget -= 1

  ── OBSERVE (single writer · the RESIDUAL HANDLER) ─────────────────────────
  certify(graph, result)                      # HOST flips state of touched nodes
  for residual in residuals_of(result):       # wave-failure error OR sufficiency-red signal
      delta = classify(residual)              # §6 — regex cascade → temp-0 LLM → typed Discovery
      graph = apply(graph, delta)             # §3 G1/G2 — node + OWNER edge, or …
      # … if delta is REPO_BUG / FLAKY / UNKNOWN or has no check_command:
      #        record out-of-scope; do NOT add a node; allow honest give-up
  maintainer.observe(graph, result, ledger)   # classify-then-fold; done_flag iff verified green gate
```

The only new behavior versus the implemented executor is the inner `for residual …` block inside OBSERVE. Everything else (`next_deterministic_wave`, `run_wave`, `isolate`, host-first `repair`, the verified test gate, `certify_refresh`) is reused as-is.

---

## 5. The auto-install capability predicate (routing)

A residual's *type* decides where it goes, but the gate is best expressed as a **capability, not a type whitelist**:

> A node is **host-auto-installable** ⇔ it has a **deterministic install recipe** (a parameterizable command template) **AND** a host **`check_command`** to certify it.

Type is the current proxy for that capability:

| Type | Auto-install? | Recipe | Check | Why |
|---|---|---|---|---|
| PACKAGE (pip) | ✅ | `pip install name==ver` | `python3 -c 'import name'` | both exist |
| SYSTEM_LIB / TOOL | ✅ | `apt-get install -y <apt:pkg>` | `command -v` / `ldconfig -p` | both exist (needs `chosen_fix="apt:…"`) |
| CONFIG (env var) | ❌ | — (value not knowable) | `printenv X` unsatisfiable in a fresh-shell exec | no recipe, no real check |
| SERVICE (db/redis) | ❌ | — (provision / ports / creds / lifecycle) | — | no deterministic recipe |

This predicate already exists as `emit._is_reciped` / `failed_reciped_nodes`. The residual handler **reuses the same gate to route residuals**:

- residual → PACKAGE / SYSTEM_LIB / TOOL **with** a `check_command` → reciped → **deterministic wave installs it** (next pass, 0 tokens).
- residual → CONFIG / SERVICE → **not reciped** → handled only reactively via the sufficiency-repair path (or honest give-up); never proactively provisioned. Same exclusion that stopped the env-var thrash (executor §9: CONFIG thrash was 77% of the ledger / 542k tokens).
- residual → REPO_BUG / FLAKY / UNKNOWN → **honest give-up** (§3 G3).

One predicate does double duty: it decides what the wave auto-installs **and** where a residual is routed. **Capability-over-type pays off** the day a conda/npm layer or deterministic SERVICE provisioning (executor §10 deferred) lands — a node becomes auto-installable the moment it gains `(recipe, check)`, with no whitelist edit.

---

## 6. The error classifier (regex cascade → temp-0 LLM)

`classify(residual)` is a **cascade**, not a single model call:

1. **Deterministic first** — the existing `classify_observation` (`runtime_classify.py`): free, reproducible, covers the common shapes (`ModuleNotFoundError` → PACKAGE, native-lib `*.so` → SYSTEM_LIB, `command not found` → TOOL, connection-refused → SERVICE, missing-var → CONFIG). Keeps the common path at 0 tokens.
2. **LLM only on the misses** — when the regex returns `None`, escalate to a temperature-0, schema-constrained LLM classifier. This is where the LLM earns its keep: weird linker errors, version conflicts, and the judgment call "this is a repo bug, not an env gap" that regex cannot make.

**Temperature 0** — classification has one right answer; same-error→same-node gives reproducible A/B and a clean paper number. (Schema-constrained decoding matters even more than temperature.)

**Schema** (forced JSON / tool call):

```python
CLASSIFY_SCHEMA = {
  "type": "object",
  "required": ["kind", "check_command", "confidence", "rationale"],
  "properties": {
    "kind": {"enum": ["PACKAGE","SYSTEM_LIB","TOOL",   # reciped → wave installs
                      "CONFIG","SERVICE",               # advisory → sufficiency-repair only
                      "REPO_BUG","FLAKY","UNKNOWN"]},     # escape hatch → honest give-up
    "name":          {"type": "string"},                # "pytest_asyncio" / "libpq-dev"
    "layer":         {"enum": ["pip","apt","none"]},
    "install_hint":  {"type": "string"},                # "pip install pytest-asyncio"
    "check_command": {"type": "string"},                # "python3 -c 'import pytest_asyncio'" | "" if not checkable
    "requires_of":   {"type": "string"},                # owner node id this is a dep OF → emits the §7 edge
    "confidence":    {"type": "number"},
    "rationale":     {"type": "string"}
  }
}
```

**Routing** (same gate as §5):
- `kind ∈ {PACKAGE, SYSTEM_LIB, TOOL}` **and** non-empty `check_command` → add node + owner edge (RUNTIME-tagged), deterministic wave installs it.
- `kind ∈ {CONFIG, SERVICE}` → advisory; sufficiency-repair only.
- `kind ∈ {REPO_BUG, FLAKY, UNKNOWN}` **or** empty `check_command` → honest give-up; no node added.

**The `check_command` requirement is the safety backstop.** Because every emitted node must carry a host check, a hallucinated node fails to certify and is harmless — the LLM cannot flip state. The two non-negotiable system-prompt rules that make this hold:

> "Every environment obligation MUST include a `check_command` that proves its presence (an import, `command -v`, `ldconfig -p`). If you cannot give a real check, you do not know — classify `UNKNOWN`."
>
> "If the error is not an environment/dependency gap (assertion failure, logic bug, network timeout), classify `REPO_BUG`/`FLAKY`. Do NOT invent a package to explain it."

**Input hygiene** — feed the **tail** of stderr (the error is at the bottom; *not* a head-truncation — `output[:N]` head-trunc bit us before), plus the node under repair and the list of already-SATISFIED nodes so the classifier does not re-propose them.

**Instrumentation** — record which classifier fired (regex vs LLM) per discovery. That is a clean paper number: the LLM's *marginal* coverage over the deterministic baseline.

---

## 7. Attribution: one-command-at-a-time → edge on the culprit

G2 ("prefer the edge") requires knowing **which node** a discovered dep belongs to. Batch execution loses that; `isolate` recovers it.

**What batch loses.** `build_recipe` collapses the whole pip layer into one command (`emit.py:226-234`): `python3 -m pip install … a==1 b==2 psycopg2==2.9 c==3`. That is **one ledger event, one interleaved output blob** over the whole closure. On failure, `emit_drain` records a *failed* attempt against **every** target node (`depgraph_live.py:124-135`) and relies on `certify_refresh` to re-probe each node. So batch tells you *which nodes are still MISSING* — but **the error text is owned by no single node.**

**Why that degrades the appended requirement.** Where the discovered edge lands today (`runtime_ingest.py:115-118`):

```python
edge = Edge(src=TEST_NODE_ID, dst=target_id, relation=REQUIRES, origin="runtime")
```

Every runtime discovery hangs off the **global Test node** — `Test --REQUIRES--> syslib:libpq` — because the batch observation has no owning package: the classifier sees `pg_config not found` in an 87-package blob and can only conclude "*something* needs libpq." That is a **flat fact with no ordering power**.

**What `isolate` recovers.** Re-running `pip install psycopg2` *alone*:
- the command is scoped to one node → **the culprit identity is known** (psycopg2);
- the output is **clean** — just psycopg2's `pg_config not found`, no interleaving, no joint-resolver noise.

Now the handler can hang the edge on the culprit — `pkg:psycopg2 --REQUIRES--> syslib:libpq` — the actual cross-layer chain that carries ordering. Note the **static probe path already does this correctly** (`probe.py:281-284`: `Edge(src=owning_pkg, dst=syslib, REQUIRES, origin="probe")`) precisely because it probes one import at a time; the batch *runtime* path is the only one that falls back to `Test`.

**The concrete change.** `ingest_runtime_failures` currently takes `(command, output)` tuples with no owner field, so it can only reach `TEST_NODE_ID`. The fix rides the per-node repair path (executor Task 4 `repair_failed_nodes`), which already holds `node.id`:

1. `isolate` / per-node repair gives each observation a single owner.
2. Thread that `owner_node_id` through to the `Discovery` (the `requires_of` field in §6).
3. In `_annotate_or_append`, set `src=owner_node_id`, **falling back to `TEST_NODE_ID` only when there is no owner.**

The rule lines up cleanly:

| Residual source | Owner known? | Edge |
|---|---|---|
| Wave-failure (isolate) | yes — the culprit package | `culprit --REQUIRES--> dep` (cross-layer chain) |
| Sufficiency (frontier clean, tests red) | no — not any one package | `Test --REQUIRES--> dep` (correct: `pytest-asyncio` *is* a test-suite dep) |

Two payoffs: you pay the per-node tax **only on the failed wave**, so batch stays the fast path (executor §4: "batch failure degrades to per-node, not the reverse"); and the clean single-package error makes the temp-0 LLM classifier far more reliable than the interleaved 87-package blob — precise attribution helps both the regex and the LLM.

### 7.1 How the error reaches the handler — the ledger `(command, output)` pairing

The handler never sees a "failed command" in isolation; it sees ledger **events**. Each executed command is recorded as an event carrying both the command and its combined stdout/stderr, and OBSERVE taps them as `(e.cmd, e.stdout)` pairs (`orchestrator.py:196-201`):

```python
events = ledger.events()
new_events = events[_rt_mark:]
obs = [(e.cmd, e.stdout) for e in new_events]      # (command, combined output)
new_graph, found = ingest_runtime_failures(current_map.dep_graph, obs)
```

Three properties of this pairing shape the design:

1. **The error is folded into the command that *surfaced* it — usually a check/test, not the install of the missing thing.** `ModuleNotFoundError: pytest_asyncio` rides the `pytest` test-gate event (`VERIFY_TEST_CMD`, `orchestrator.py:263`), not a `pip install`; `libpq.so.5: cannot open shared object` rides the certify check `python3 -c "import psycopg2"`, not the psycopg2 install. So `e.cmd` is mostly *context*; the obligation is recovered from the **output text** (`e.stdout`), where the named token (module, soname) lives.
2. **This is *why* attribution defaults to `Test` (§7).** Because `e.cmd` is typically a generic `pytest` / bare-import, it does not structurally name the culprit package — so the edge falls back to `Test --REQUIRES--> node`. `isolate` works precisely by making `e.cmd` itself scope to one package (`pip install psycopg2` alone), so an owner becomes derivable from the command, not just the text.
3. **Every event is scanned, not just failures.** `obs` is *all* new events; `classify_observation` returns `None` for output with no recognizable error, so successes self-filter (§3 G3's three-bucket sort). And events are read from the **previous** cycle via the `_rt_mark` high-water mark (`orchestrator.py:226`) — the one-cycle reactive delay (Appendix B.3).

---

## 8. Termination & honesty

Progress is guaranteed by three rules layered on the existing `turn_budget` backstop:

1. **Monotone state.** Nodes only go UNKNOWN → {MISSING, SATISFIED}; the handler only *adds* nodes/edges, never churns. The graph is append-only within a run.
2. **Dedup before append.** `_find_existing_node` already matches PACKAGE by normalized name (`runtime_ingest.py:71-86`); the handler must not re-add a residual that maps to a node already present.
3. **Divergence stop (the principled give-up).** If a residual maps to a node that is **already SATISFIED**, then NECESSARY (graph says present) and SUFFICIENT (tests still red referencing it) have **diverged** — more nodes will not close it. Stop, do not add. This is distinct from `turn_budget == 0`: the budget is the blunt backstop; divergence is the *reasoned* stop that says "the residual is not an env gap." It routes to the §3-G3 honest give-up, not to another loop iteration.

In all give-up cases `done_flag` stays false and the run reports the diagnosis — never a fake success.

---

## 9. Invariants (preserved, unchanged)

- **Host certifies; nothing else flips `state`.** The handler proposes nodes/commands; only `certify_refresh` / the test gate flip state.
- **No node exists on LLM authority alone.** Every emitted node carries a `check_command`; un-certifiable proposals are inert (§6).
- **The LLM cannot self-declare done.** `done_flag` comes only from a host-run *verified* green gate (`_verified_test_run_passed`).
- **Single OBSERVE writer.** The residual handler lives inside OBSERVE; it is the only place runtime deltas and `done_flag` are written (executor §4/§8).
- **Config & Service stay advisory.** Excluded from the auto-install set and the proactive frontier; reached only reactively.

---

## 10. Mapping to code

**Reused as-is:**
- `runtime_classify.py` — `classify_observation` (the deterministic cascade tier).
- `runtime_ingest.py` — `ingest_runtime_failures`, `_annotate_or_append`, `_find_existing_node` (idempotent append/annotate + dedup).
- `emit.py` — `failed_reciped_nodes` / `_is_reciped` (the capability predicate, §5); `build_recipe` / `next_deterministic_wave` (the wave the install rides).
- `depgraph_live.py` — `certify_refresh` (the host backstop), `repair_failed_nodes` (the per-node owner source, §7).
- `probe.py:281-284` — the reference implementation of owner-edge attribution.

**New / changed:**
- **LLM classifier tier** — a temp-0, schema-constrained classifier (§6) appended to the `classify_observation` cascade; called only on a regex miss.
- **Owner threading** (§7) — `Discovery` / `ingest_runtime_failures` gain an `owner_node_id`; `_annotate_or_append` sets `src=owner_node_id` with `TEST_NODE_ID` fallback (`runtime_ingest.py:115-118`).
- **Divergence stop** (§8) — an already-SATISFIED-residual check in OBSERVE that routes to honest give-up.
- **Out-of-scope record** (§3 G3) — a non-env residual is logged with its diagnosis and never becomes a node; the loop is allowed to give up.

All new behavior stays gated under the graph-scheduler arm; the off path and legacy arms remain byte-identical.

---

## 11. Paper framing

> The dependency DAG is built NECESSARY-by-construction and is *structurally incomplete*; the executor closes the gap with a **residual handler** that converts every unmodeled error into one of two outcomes — a **host-certifiable graph delta** (a typed node plus a cross-layer edge attributed to its culprit) that the deterministic wave then installs and the host certifies, or an **honest give-up** when the residual is not an environment obligation. Attribution is recovered by degrading a failed batch to per-node execution (`isolate`); diagnosis is a deterministic-regex cascade with a temperature-0, schema-constrained LLM fallback whose every proposal must carry a host check — so the LLM can be general while the host remains the sole authority on truth.

Why it reads cleanly: one mechanism replaces both repair branches; the `check_command` requirement makes hallucination inert; the honest-give-up branch makes "the graph was wrong" a *reported diagnosis* rather than a faked success; and edge-on-culprit attribution turns each failure into a reusable cross-layer chain rather than a flat Test-dep.

---

## 12. Deferred

- **Learned-recipe cache** — persist confirmed `culprit REQUIRES dep` edges across runs so the second encounter never reaches the LLM (the generalization of §7).
- **CONFIG/SERVICE necessity gating** — only if a repo needs a config var or a running service *proactively* rather than reactively (executor §10).
- **LLM classifier distillation** — once the LLM's marginal-coverage log (§6) is large enough, fold its recurring patterns back into the deterministic regex tier to shrink token cost.
- **Symmetric runtime ingestion (Upgrade A/B, Appendix B)** — scoped transitive-resolve + scoped system-probe so a runtime discovery appends a *subgraph* (modeled + pinned), not a leaf. Deferred deliberately: the freeze capture already masks the reproducibility cost (Appendix B.2); the live payoff is turn-budget on deep-native repos. Gate Upgrade B to the deep-native case first.

---

## 13. Decision record

Converged via a design conversation (2026-06-27) over the implemented topological-wave executor. Decisions taken:
- **Unify the two §4 repair paths** into one OBSERVE-resident residual handler (not two mechanisms).
- **Delta-or-give-up**, never blind append (G1–G3).
- **Prefer the edge** and attribute it to the culprit via `isolate` (§7) — the static probe path is the reference.
- **Capability predicate, not type whitelist**, routes both auto-install and residuals (§5).
- **Regex-first / temp-0-LLM-fallback cascade**, `check_command`-mandatory schema, with an explicit out-of-scope escape hatch (§6).
- Open item resolved: **write the wave-repair's discovered edge back to the graph** (graph *learns* chains vs. one-shot plan).
- **Researched (3-agent deep dive, Appendix B):** the runtime path appends a single *leaf*, not a subgraph — transitive + system deps of a runtime discovery are *installed but unmodeled*. Two prior worries were re-weighted by the evidence: reproducibility is **not** harmed (freeze-masked, B.2); runtime packages are version-less → **LLM-mediated, not deterministic** (B.3, corrects Appendix A.2). Subgraph ingestion (Upgrade A/B) deferred — see Appendix B.6 / §12.

---

## Appendix A — Worked trace: a real error string → graph delta

This appendix makes §6/§7 concrete. It follows two real error strings through the *existing* deterministic machinery, then shows the *exact point* the LLM tier changes.

### A.1 The pipeline (deterministic, today)

A failed command leaves an `(command, output)` observation on the ledger. OBSERVE taps the ledger and runs each observation through three stages:

```
raw stderr ──▶ classify_observation()      ──▶  Discovery        ──▶ ingest ──▶ graph delta
              (parse: regex → typed token)      (typed obligation)     (append node + edge)
```

The three stages are **parse → structure → append**. The regexes live in `failure_classifier.py`; the structuring in `runtime_classify.py`; the append in `runtime_ingest.py`.

### A.2 Example A — `ModuleNotFoundError` (clean PACKAGE; sufficiency residual)

Raw text on the ledger (from a red `pytest` run, frontier already exhausted):

```
ModuleNotFoundError: No module named 'pytest_asyncio'
```

1. **Parse** — `classify_dependency_failure` (`failure_classifier.py:36`): `MODULE_NOT_FOUND_RE` matches, capture group = `pytest_asyncio`. Returns `DependencyFailure(failure_type="module_not_found", import_name="pytest_asyncio", message=<excerpt>)`. *Note what was extracted: one token (the import name) via a capture group — nothing else.*
2. **Structure** — `classify_observation` (`runtime_classify.py:76`) takes the `module_not_found` branch:
   - `map_import_to_package("pytest_asyncio").package_name` → `"pytest-asyncio"` (the **import→dist name** lookup table fixes the underscore/hyphen skew),
   - the `check_command` is **synthesized from a fixed template**: `f'python3 -c "import {import_name}"'`.
   - Returns `Discovery(node_type=PACKAGE, name="pytest-asyncio", layer=PIP, check_command='python3 -c "import pytest_asyncio"', data={"import_name": "pytest_asyncio"})`.
3. **Append** — `ingest_runtime_failures` → `_annotate_or_append` (`runtime_ingest.py:89`):
   - `_id_for_discovery` → `package_id("pytest-asyncio", None)` = `pkg:pytest-asyncio`; `_find_existing_node` finds nothing (incl. by normalized name) → **append**.
   - `_node_for_discovery` builds `Node(id="pkg:pytest-asyncio", type=PACKAGE, discovered_by=RUNTIME, state=UNKNOWN, check_command=…)`. **State is UNKNOWN — the host owns it, the parser never asserts presence.**
   - Edge: `Test --REQUIRES--> pkg:pytest-asyncio`, `origin="runtime"` (no owner package — this is a sufficiency residual; §7).
4. **Resume** — next OBSERVE certifies `python3 -c "import pytest_asyncio"` → still MISSING. **Caveat (corrected per Appendix B.3):** the appended node is *version-less* (`pkg:pytest-asyncio`, no `==ver`), and `_is_emittable` **requires a version** (`emit.py:78-80`) — so it is **not** picked up by the 0-token deterministic wave; it routes to the **LLM repair frontier** and costs one `_repair_turns` unit. To make it ride the deterministic wave instead, the node needs a version (Upgrade A, Appendix B.4). Either way, once installed and certified SATISFIED, the test gate re-runs green.

### A.3 Example B — native library (SYSTEM_LIB; wave-failure residual, with attribution)

Raw text from `python3 -c "import psycopg2"` after a batch install:

```
ImportError: libpq.so.5: cannot open shared object file: No such file or directory
```

1. **Parse** — `NATIVE_LIBRARY_RE` (`failure_classifier.py:26`) matches; `library = "libpq.so.5"`. Returns `failure_type="native_library_missing", details={"library": "libpq.so.5"}`.
2. **Structure** — `classify_observation` native branch (`runtime_classify.py:100`): `Discovery(node_type=SYSTEM_LIB, name="libpq.so.5", layer=SYSTEM, check_command="ldconfig -p | grep -q libpq.so.5")`.
3. **Append** — `syslib_id("libpq.so.5")` = `syslib:libpq.so.5`; appended `state=UNKNOWN`. **Edge today: `Test --REQUIRES--> syslib:libpq.so.5`** — because the runtime observation carries no owner (§7). With owner threading the edge becomes **`pkg:psycopg2 --REQUIRES--> syslib:libpq.so.5`**, which is the orderable cross-layer chain. (The static `import_probe` path already emits the owner edge, `probe.py:281-284` — the runtime path is the one being upgraded.)

### A.4 Where the LLM changes this — the cascade's `return None`

The deterministic parser is a **fixed regex table**; it only recognizes the shapes it was written for. Anything else falls through every branch to `classify_observation`'s final `return None` (`runtime_classify.py:152`) — and a `None` Discovery means the observation is **silently dropped** (`runtime_ingest.py:152`). Examples that produce `None` today:

```
fatal error: Python.h: No such file or directory          # needs python3-dev — no lib*.so, no match
/usr/bin/ld: cannot find -lpq                              # linker form, not the .so runtime form
error: command 'gcc' failed: No such file or directory     # toolchain, not "command not found"
psycopg2.errors.UndefinedFile: could not open extension…   # service/extension, no pattern
AssertionError: expected 200, got 500                      # genuinely a repo bug
```

The LLM tier (§6) attaches at **exactly that `return None`**: on a regex miss, the tail of the error + context goes to the temp-0, schema-constrained classifier, which emits **the same `Discovery` shape** (`kind/name/layer/check_command/requires_of`). It then flows through the *unchanged* `ingest_runtime_failures` append path. So the LLM changes **only the parse stage** — node construction, certification, the wave install, and the edge machinery are byte-identical.

What the LLM adds that the regex table structurally cannot:

| | Deterministic parse | LLM parse |
|---|---|---|
| **Extraction** | one capture-group token + fixed check-command template + `map_import_to_package` lookup table | reads the whole error semantically; emits all fields incl. a synthesized `check_command` and the owner (`requires_of`) directly |
| **Coverage** | only pre-written shapes; everything else → `None` (dropped) | produces a Discovery for novel shapes (`Python.h` → `python3-dev`, `check_command="test -f /usr/include/python3*/Python.h"`) |
| **Name normalization** | needs the static alias table (`cv2`→`opencv-python` only if listed) | knows common aliases without a table |
| **The `None` ambiguity** | `None` conflates "I don't recognize this" with "this isn't an env gap" | **disambiguates**: emits a node for an unrecognized *env* gap, or `REPO_BUG`/`FLAKY`/`UNKNOWN` for a non-env failure → honest give-up (§3 G3) |

That last row is the substantive change, not just coverage. The regex `None` is silent and ambiguous; the LLM turns the same miss into one of two *explicit* outcomes — a certifiable delta or a reasoned give-up — which is what lets the residual handler both fix more and fake nothing. And the `check_command` it must emit is still run by the host, so a hallucinated `python3-dev` simply fails to certify and is inert (§6 backstop).

---

## Appendix B — Static/runtime asymmetry, subgraph ingestion, and the two upgrades

Established by a 3-agent code deep-dive (2026-06-27). Records *why the residual handler appends a leaf, not a subgraph*, what that costs, and the two upgrades — plus the exact merge mechanics any subgraph ingestion must respect.

### B.1 The asymmetry

The static build (`build.py`) and the runtime path do **not** share detection machinery. `resolve_closure`, `seed_predicted_native`, `import_probe`, `ldd_probe`, `link_imports_to_packages` are wired **only in `build.py`** — they run once, up front. The live executor loop imports only `certify_refresh`, `emit_drain`, `ensure_python_shim`, and `ingest_runtime_failures`.

| | Static build (once) | Runtime discovery (in-loop) |
|---|---|---|
| Transitive closure | `resolve_closure` (uv) → full pinned tree + REQUIRES edges | none — appends **one** node |
| System-dep detection | `seed` + `import_probe` + `ldd_probe` → SystemLib/Tool + owner edges | none |
| Version pin | whole closure, resolver-consistent | the one node only — and even that is **absent** (B.3) |
| Edges | full cross-layer DAG | one edge (`Test`/owner → node) |
| State | certified | UNKNOWN → certify probes it |

A runtime PACKAGE discovery is a **bare leaf** (`_annotate_or_append`: 1 node + 1 edge, no resolve). Its transitive deps are installed by pip at wave time (`pip install` runs **without `--no-deps`**, `emit.py:233`) but are **never modeled**; `certify_refresh` only re-flips state of existing nodes — it cannot discover (`certify.py:90-93`).

### B.2 Reproducibility is NOT harmed — the freeze masks it

The worry "runtime subtrees are unpinned, so the emitted artifact under-specifies them" is **refuted** for the main (DockerAgent) emission path. That path bakes its pinned closure from a **live `pip list --format=freeze`** captured *after* the loop, not from the graph: `extractor.py:23` → `snapshot.py:55-81` → `world_model.py:402-408` (`apply_deterministic` **replaces** installed from the freeze) → `agent.py:1273-1275` → `synthesis.py:224-252` (`printf … > pin; pip install -r pin`). Graph Package nodes are merged **additively only** (`installed + tuple(f for f in sat if f.name not in have)`, `orchestrator.py:160-163`) — the freeze is the base; the graph never filters it. So every package pip installed reactively — runtime-discovered roots **and their transitive deps, at actual versions** — is in the pin regardless of graph modeling. The graph-derived `emit.py build_recipe` *would* omit runtime subtrees, but it is an **in-loop install driver, not the emitted artifact**. (Only the LLM-authored `ccdf` Dockerfile lacks this protection — an LLM-completeness gap, not the graph gap.)

### B.3 The real cost: version-less runtime packages are LLM-mediated; convergence is per-layer

A runtime PACKAGE node is appended with **no version** (`runtime_ingest.py:54` → `package_id(name, None)` → bare `pkg:<name>`). `_is_emittable` **requires `node.version`** (`emit.py:78-80`). Therefore:

> A version-less runtime package is **not emittable** → it does **not** ride the 0-token deterministic wave → it routes to the **LLM repair frontier**, costing one `_repair_turns` unit.

(This corrects Appendix A.2's "deterministic wave installs it, 0 tokens" — true only for *statically-resolved* packages, which carry versions.)

Convergence is **iterative — one OBSERVE cycle per hidden dependency layer**: a chain `pkg → libX → toolchainY` surfaces `libX` one cycle after `pkg` installs, and `toolchainY` only after `libX` installs — never one-pass. Each layer eats one `max_cycles` iteration; each LLM-mediated layer also eats one `_repair_turns`. Deep native chains are the worst case for both budgets.

### B.4 The two upgrades (append a subgraph, not a leaf)

**Upgrade A — scoped transitive resolve.** On a runtime PACKAGE discovery, call `resolve_closure([(None, newpkg)], host_executor, …)` — the API already accepts a 1-element root list, and uv resolves that root's full transitive subtree host-side. Payoff is **not** reproducibility (B.2) but: (a) it assigns a **version** → the node becomes emittable → rides the deterministic wave instead of an LLM turn (fixes B.3), and (b) models + certifies the transitive subtree (graph completeness, for the paper). Cost: **1 host `uv lock`**. Hazard: cross-closure version consistency (B.5).

**Upgrade B — scoped system-probe.** After installing a runtime package, run `import_probe`/`ldd` scoped to it to surface its native deps in **one** pass instead of one reactive LLM cycle per layer (attacks B.3 directly). The probes are already owner-scoped and reuse all builders, but need two seams that don't exist today: a **per-package install** (the current installer is whole-closure bulk, `probe.py:101`) and a **scoped entry point** to skip the one whole-graph step. Cost: container round-trips for the one package.

### B.5 How a subgraph is merged into the live graph (mechanics any upgrade must respect)

`DepGraph` is a frozen `(nodes, edges)` pair with **no bulk merge primitive**. A subgraph is folded in node-by-node then edge-by-edge (`build.py:256-260`):

```python
pre_ids = {n.id for n in graph.nodes}
for node in sub_nodes:  graph = graph.with_node(node)   # ALL nodes first
for edge in sub_edges:  graph = graph.with_edge(edge)   # THEN all edges
graph = _restamp(graph, {n.id for n in graph.nodes} - pre_ids, cycle)
```

The two primitives and their semantics:

- **`with_node` = replace-by-exact-`id`** (`schema.py:248-251`): add if new, silently replace if the id matches. **No version reconciliation.**
- **`with_edge` = dedup by `(src,dst,relation)` + validate** (`schema.py:253-283`): **raises `ValueError` if `src`/`dst` is not already present**, or if node types violate `EDGE_RULES`. → **nodes MUST precede edges.**

Because `with_node` keys on `name==version`, version skew **accumulates as duplicate nodes** rather than colliding-and-reconciling — the load-bearing hazard for runtime subgraph ingestion:

1. **Bare-vs-versioned id mismatch.** The runtime node is `pkg:psycopg2`; the scoped resolve emits `pkg:psycopg2==2.9.9`. Different ids → `with_node` **adds a second node**. Must reconcile: `without_node("pkg:psycopg2")` + rewire its `Test`/owner edge to the versioned id, reusing the normalized-name match in `_find_existing_node` (`runtime_ingest.py:71-86`) — which today reconciles only the directly-discovered node, not the subtree.
2. **Shared-dep divergence is silent.** If the subtree has `pkg:numpy==1.24` but the graph already has `pkg:numpy==1.26`, the ids differ → **both coexist**; the graph then asserts two numpy versions with nothing flagging it. This is why a scoped resolve must **re-lock against the existing closure** (reuse the stored `target_python`/`exclude_newer` from any existing node, `schema.py:140-142`) rather than resolve the root in isolation.
3. **No atomic merge.** A mid-fold `_validate_edge` raise leaves a partially-merged graph. Build into a local graph and swap only if the whole fold succeeds (the static build skips this only because its inputs are resolver-guaranteed well-formed).

### B.6 Recommendation

The case for these upgrades is **narrower than it first looks**, because B.2 removes the reproducibility argument. Decide by goal:
- **Cost on deep-native repos** (budget exhaustion via per-layer convergence) → **Upgrade B**, gated to the case where a runtime install is followed by a native-lib failure. Highest live value.
- **Paper completeness** (a *complete* certified cross-layer graph) → **Upgrade A**; otherwise the graph is honestly a partial model and the paper should *say so*.
- **Just the shipped artifact** → do nothing; the freeze already wins.

Default: do **not** rush either; ship the residual handler (§1–§10) first, add Upgrade B only when measurement shows deep-native budget exhaustion.
