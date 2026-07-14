# Error Nodes, Anchored Grounding, and the Unfusing of "Check Failed" from "Is Required"

**Date:** 2026-07-14
**Status:** Proposed
**Supersedes:** the grounding half of `2026-07-14-minimal-certify-and-error-grounding.md` (whose diagnosis is right and whose Part 2 re-derives `relink.py`)

---

## 0. The evidence this is built on

Everything below is measured on this repo, this week. Numbers with a source are
verified; anything unmeasured says so.

**The corpus** (111 labelled repair rounds, `src/eval/graph_quality/corpus.py`):

| class | count |
|---|---|
| source patch (no env fix exists) | 72 |
| env-var | 25 |
| python package | 11 |
| os package | 3 |

**The pytest stream** (52 test-stream pairs):

* `summarize()` returns **nothing at all on 10 of 52 (19%)** — enrich learns zero.
* 58 causes, **all `phase=collect`**. Our `{collect,setup}` gate drops 0% here, so
  this corpus *cannot validate the gate*. Do not claim it works.
* Groundable from the `exc + detail` we actually pass: **32/58 (~55%)**.
* Blast radius (`Cause.count`): min 1, **median 1**, max 19.
* **107 import-family blocks; 100% carry a multi-frame traceback** naming the
  first-party importer. We discard all of it.
* Of 104 `No module named` blocks, **28 (27%) name the project's OWN package**
  (`rerankers`, `alpha_codium`, `deepfuze`). For these `pip install <name>` is the
  wrong fix, and sometimes an actively dangerous one: a PyPI namesake exists and
  installing it goes false-green.

**The phantom** (`src/eval/graph_quality/graphs/pillow.json`, real minted graph):

* 10 `SystemLib` nodes, all `pyarrow`'s bundled libs (`libarrow.so.2400`, …).
* Every one: `state=missing`, `discovered_by=resolver`, `chosen_fix=None`,
  check `ldconfig -p | grep libarrow.so.2400` — which can never pass, because
  pyarrow ships those `.so` files inside its own wheel and resolves them by RPATH.
* Their owner `pkg:pyarrow==24.0.0` is **SATISFIED**. `import:pyarrow` is **SATISFIED**.

**What the phantom costs** (verified by executing `emit.py` against that graph):

| | `_toolchain_ready` | `_is_emittable` | emittable | frontier (escalated to the LLM) |
|---|---|---|---|---|
| today, fresh build | **False** | **False** | 11 | 11 |
| with the rule in §3 | True | True | 12 | **0** |

The graph can **never** emit `pip install pyarrow`. `_toolchain_ready` (`emit.py:63`)
returns False when any hard-required `SystemLib` is not SATISFIED, and these ten
cannot become SATISFIED. They also fill the escalation frontier with ten fabricated
missing libraries for the agent to chase.

---

## 1. The thesis

> **A failed check is an observation, not a demand.**

Today the two are fused: `rc != 0` → `State.MISSING` → `emit._is_emittable` accepts it
→ an install action. Three steps, no gap. That fusion is what turns a wrong check into
a wrong *command*.

This spec inserts a gap. Failures become **Error nodes** — first-class, anchored,
persistent observations that carry evidence and history but assert no requirement.
A separate, explicit rule promotes an observation to a requirement, and that rule can
be **refuted** by evidence already in the graph.

Corollary, unchanged from the prior handoff: **the graph should LOCALIZE; the model
should RESOLVE.** An Error node is pure localization. It never names an apt package.

---

## 2. The Error node

### 2.1 Identity

Key on the capability when one can be extracted, and on a normalized signature when
one cannot. Never on raw text — line numbers and temp paths would mint a new node
every turn and dedup would die.

```
error:import:PIL
error:syslib:libGL.so.1
error:binary:pg_config
error:check:syslib:libarrow.so.2400        # a failed certify check (§3)
error:unbound:9f2a1c                       # normalized-text hash — TODAY THESE VANISH
```

Normalization for the hash strips line numbers, absolute paths, hex addresses,
timestamps, and ANSI codes.

The `unbound` bucket is the point. It is the honest home for the 25 env-var pairs, the
long tail of `OSError`/`AttributeError`/`RuntimeError` causes, and everything else we
cannot classify. **It must never be dropped.**

### 2.2 The fields — and what we stop discarding

```
id                 error:import:PIL
kind               import | syslib | binary | check | unbound
key                PIL
anchored_to        <node id>  |  null
first_seen_cycle   1
sightings          [1, 2, 3]
seen_this_cycle    true
blast_radius       19                    <- Cause.count. We compute this and drop it.
subject            tests/test_image.py   <- Cause.module
phase              collect               <- Cause.phase
log_span           <the RAW block>       <- the traceback. 100% of import blocks have
                                            a multi-frame chain. We currently keep only
                                            "ModuleNotFoundError: No module named 'PIL'".
attempts           [...]                 <- Node.with_attempt, which today has ZERO
                                            call sites in the react loop.
refuted_by         import:pyarrow | null
```

Three of these are pure *stop-throwing-it-away* changes. `graph_enrich.py:120` builds
the observation as `(f"pytest: {c.module}", f"{c.exc}: {c.detail}")` — a lossy funnel
that discards the traceback and the blast radius before grounding ever runs. The star
precision of 0.25 is self-inflicted.

### 2.3 Anchoring

In descending order of confidence. **Failure to anchor never deletes the node.**

1. **The project's own package → the `PROJECT` node.** If the missing module is the
   repo's own top-level package, this is an editable-install failure, not a missing
   dependency. **27% of `No module named` blocks.** This rung must come first, because
   it is the one where the log alone is actively misleading.
2. **The owner package**, via the existing `owner_node_for_command`
   (`graph_enrich.py:31`) — already handles pinned/unpinned/ambiguous and returns
   `None` rather than guessing.
3. **An existing `import:` node** from the static scan.
4. **`TEST_NODE_ID`** — honest, but flat.
5. **Unanchored** — still a node, still rendered, marked as such.

### 2.4 Lifecycle

Do not write a clever clear-rule. The container rebuilds from base every turn, so:

> **Presence is re-derived each turn. History accumulates.**

Re-ground from this turn's log; carry forward only `first_seen_cycle`, `sightings`, and
`attempts`, keyed by id. Staleness becomes structurally impossible rather than a rule
we have to get right. An error that does not reappear is distinguishable from one that
was never reached by asking whether its producing command ran at all this turn —
`not observed`, not `cleared`.

### 2.5 Certification

An Error node has **no `check_command`** — it is an observation, not an obligation.
`certify.py:79` already returns unchanged for any node without one, so it falls out of
the certify walk **with no change to certify at all.**

---

## 3. certify appends an error; it does not assert a requirement

### 3.1 What certify does today

`rc != 0` → `node.with_state(State.MISSING, evidence=...)`. And the code already knows
this is thin — `certify.py:100`:

> *"A presence check like `ldconfig -p | grep` or `command -v` prints nothing on failure,
> so writing its empty stderr would otherwise clobber the diagnostic line that explains
> WHY the need exists."*

certify works around an uninformative failure by preserving the *older discovery
evidence*. The Error node is the principled version of that hack.

### 3.2 Phase 1 — additive, zero behaviour change

certify's failure path **also** appends `error:check:<node_id>`, carrying the check
command, rc, output, cycle, and a `check_kind: presence|functional` tag. No state
semantics change. Nothing downstream moves. This is pure truth-capture at zero
regression risk, and it gives `certified_cycle` its first consumer — today it is
stamped `0` forever in the react arm and read by nobody.

### 3.3 Phase 2 — the refutation rule

**The defect is the EDGE, not the node state.** This was established by experiment, and
it refuted the first version of this rule:

* Parking the phantom node to `UNKNOWN` clears the escalation frontier (11 → 1) but
  **does not unblock emission**. `_toolchain_ready` skips a dep only when it is
  `SATISFIED`; `UNKNOWN` still returns False.
* `pkg:pyarrow --requires(hard)--> syslib:libarrow.so.2400` is a **false edge**. pyarrow
  does not require libarrow *from the system*; it ships it. The `DT_NEEDED` scan produced
  a true fact ("this ELF links libarrow") and the graph mis-modelled it as an external
  requirement.

The rule, scoped to `SystemLib` only:

> When a hard-required `SystemLib`'s **presence** check fails while a node that requires
> it has a **passing functional** check, the edge is **refuted**: the library is provided
> from inside the distribution. Demote the edge to soft (`hard=False`,
> `refuted_by=<the functional node>`) and park the node `UNKNOWN`.

`_toolchain_ready` skips soft edges (invariant #10), so the owner package emits again.
**Verified: emittable 11 → 12, frontier 11 → 0, `pip install pyarrow` unblocked.**

The refutation evidence — `import:pyarrow` SATISFIED — is **already sitting in the graph,
unused.**

And it renders as the first concrete instance of the graph proving a negative:

```
syslib:libarrow.so.2400     check failed on turns 1,2,3
  REFUTED by import:pyarrow (SATISFIED) — pyarrow ships this library inside its wheel.
  NOT a requirement. Do not apt-install it.
```

A log structurally cannot say that.

### 3.4 What this rule does NOT do

It does not generalize to Package nodes. A Package's check is `pip show <name>`, and its
failing is precisely how emit decides to install anything at all. Demote that and the
build path stops emitting installs. **Scope to `SystemLib`. Do not generalize without a
measurement.**

**False-negative risk, stated honestly:** a library that a package genuinely needs at
*call* time (`import cv2` succeeds, `cv2.imshow()` needs libGL) would be refuted and
hidden. But it does not disappear — it fails at runtime and comes back through the front
door as an Error node with real error text and a real owner. We stop *predicting* it and
start *observing* it. Given `wheel_preflight`'s measured 10/10 false-positive rate,
observing is strictly better.

---

## 4. Stop discarding

Three changes, all deletions of loss:

1. **The log span.** `graph_enrich.py:120` passes `f"{c.exc}: {c.detail}"`. Pass the raw
   block. 100% of import-family blocks carry a multi-frame chain naming the first-party
   importer; that chain is the anchor.
2. **The blast radius.** `Cause.count` is computed by `summarize`, rendered in the G1
   histogram, and dropped before the graph sees it. Carry it onto the Error node.
3. **The unresolvable observation.** `runtime_ingest.py:124` — `if d.name is None:
   return graph`. Delete the drop; route to `error:unbound:<hash>`. A graph that deletes
   evidence against itself can never support a negative verdict.

---

## 5. Order, and the gate

| phase | change | risk |
|---|---|---|
| 1 | Error node + never-discard + log span + blast radius + project anchor | low — additive |
| 2 | certify **also** appends `error:check:*` | none — additive |
| 3 | wire `with_attempt` onto Error nodes from `_classify_action` | low |
| 4 | **the refutation rule** (§3.3) | **high — changes emit** |
| 5 | wire Phase B (`certified_import_links`, `ldd_probe`) into the loop | medium |

**Phase 4 does not merge without a regression sweep over the repos that currently PASS.**
The last sweep: 8 graph fixes, 1637 tests green, 5 reviews — and **3 of 33 already-working
repos destroyed** (pre-commit 1.0→0, aiida-core 0.9995→0). Every one of those fixes was
validated only on already-broken repos, where the only direction was up. This is exactly
that class of change.

---

## 6. Evals

1. **Conservation = 1.0.** Every error block in → an Error node out. Mutation-test it:
   re-add the `name is None` drop and confirm the eval goes red. An eval that cannot fail
   is measuring nothing (we shipped two of those: T6/T7 reported "0 divergences" on a
   corpus exercising 1 of 4 rules).
2. **Misclassification rate on the 72.** An Error node on a source-patch failure is fine
   and honest. What is not fine is it landing as `import:`/`syslib:` instead of `unbound:`.
3. **Anchor rate.** Fraction anchored to something other than `TEST_NODE_ID`. This is the
   "is it a graph or just a log with extra steps" test. Prior: 0.25.
4. **Phantom rate.** MISSING syslibs whose owner's functional check passes. Today: **10/10**.
   Target: 0.
5. **Project-anchor precision.** The 27% case: never `pip install <the project's own name>`.
6. **Anti-thrash.** Does the attempt history stop the agent re-trying a disproven fix?
   This is the actual value hypothesis and it is measurable in shadow mode.

**Standing rules.** Run on the 16 real minted graphs, never hand-built fixtures (T4's metric
was circular because it graded fixtures containing `chosen_fix` values the real resolver
cannot produce). Grade capability and provider separately. Never edit `src/python_deps/` to
make an eval pass. Always publish the denominator.

---

## 7. What this does not fix

* **Reach.** The 72 source-patch repairs are untouched. This spec makes the graph *honest*;
  it does not make it *useful*. Those are different goals.
* **The negative verdict** ("the environment is certified — this is not an env failure"),
  which targets those 72, remains **unmeasured**. But §4.3 — never discard an observation —
  is its precondition. You cannot claim the environment is certified while silently deleting
  the evidence against you.
