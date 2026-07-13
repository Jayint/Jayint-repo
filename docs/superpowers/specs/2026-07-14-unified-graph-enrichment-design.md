# Unified Graph Enrichment — Design

**Date:** 2026-07-14
**Status:** Design, not yet implemented.
**Context:** Follows the graph-quality eval (`docs/superpowers/specs/2026-07-13-graph-quality-eval-design.md`,
commits `c1afd0f`..`6b30b16`), which measured the graph-guided react arm and found enrichment
pre-emption at **2/14**, patch root-hit at **1/3**, and a negative control producing 4/72 false
positives.

---

## 1. The problem, in one sentence

The react arm enriches its graph from **error text only** — the least reliable of the three
available sources — while the source its own docstring calls *"the primary authoritative source
for run-time native-lib nodes"* (`ldd_probe`) is **built, tested, and never called by the arm**.

Verified:

```
src/python_deps/depgraph/ldd_probe.py:1
    "Stage 4.5 — ldd-based run-time native library discovery. After install_closure installs
     the resolved closure, this stage runs `ldd` on each package's compiled extension .so
     files and collects shared libraries the dynamic linker reports as `=> not found`. Each
     missing soname becomes a SystemLib node with a requires edge from the owning Package.
     This is the *primary authoritative* source for run-time native-lib nodes."

Call sites:  src/python_deps/depgraph/build.py:1016   (construction, once)
             src/react_repair/                          (ZERO)
```

---

## 2. The governing insight

> **A successful build can still be a broken environment.**

`pip install psycopg2` returns 0. `import psycopg2` then dies on
`ImportError: libpq.so.5: cannot open shared object file`.

Error-driven enrichment **cannot** learn this, because *nothing errored during the build*.
There is no failing command, no stderr, no observation for the classifier to parse. The fact is
sitting in the filesystem — in the `DT_NEEDED` header of an installed `.so` — and only reading
the artifact recovers it.

This is not a corner case. It is exactly the `pygraphviz` / `libcgraph.so.6` failure the eval
measured as a miss, and it is the OS-capability tier where the graph's value is concentrated
(measured: os-package pre-emption 1/3 vs python-package 1/11 — the graph is *relatively better*
exactly where the LLM's own knowledge is weakest).

---

## 3. Structure has three sources. Rank them.

| # | source | mechanism | owner attribution | can hallucinate? |
|---|---|---|---|---|
| **1** | **Artifact** | `ldd` / `DT_NEEDED` on installed `.so`s, attributed via the dist's `RECORD` | **exact** — the `.so` lives *inside* the package's files | no |
| **2** | **Controlled experiment** | `python -c "import X"` in an isolated subprocess | **exact by construction** — one hypothesis per subprocess | no |
| **3** | **Error text** | the regex classifier (`failure_classifier.py`) | **fragile** — needs `owner_node_for_command` to resolve a single-package command | **yes** |

**Each covers what the others structurally cannot:**

- **Build-time tool** (`pg_config`): the build *fails*, so no artifact exists to inspect.
  → **only source 3 can see it.**
- **Runtime shared library** (`libpq.so.5`, `libcgraph.so.6`): the package installed fine, the
  build was GREEN, nothing errored. → **only source 1 can see it.**
- **Missing pure-Python module**: → **source 2.**

The arm today runs on **source 3 alone**. That is the central finding of this design, and the
most likely explanation for the 2/14.

### 3.1 Why source 3 is the one that lies

The single confirmed hallucination in the eval (`line__lighthouse`) came from this line:

```
#10 5.496   libplacebo349 libpocketsphinx3 libpostproc58 libpulse0 librabbitmq4
```

That is **apt's own progress output**, listing packages it is *successfully installing*.
`service_scan` substring-matched `rabbitmq` inside `librabbitmq4` and invented a message broker.
Sources 1 and 2 cannot do this: they read artifacts and run experiments, they do not
pattern-match prose.

---

## 4. The two axes, and their one legal source each

This is the discipline the whole design rests on. Most reconciliation bugs come from letting one
kind of evidence touch the wrong axis.

| axis | what it is | its ONLY legal source |
|---|---|---|
| **STATE** (`SATISFIED` / `MISSING` / `UNKNOWN`) | what the container currently contains | the **census** (presence) × what the script **attempted** |
| **STRUCTURE** (nodes, edges, fix candidates) | what this repo *needs* | the **artifact probe**, the **import probe**, and (last) the **failing region** |
| **ATTEMPTS** (what the agent tried, what happened) | the experiment log | the script × the run result |

**Structure accumulates monotonically. State is re-derived every turn and never carried
forward. Attempts accumulate and gate the recommendation.**

State must be re-derived because the script re-runs **from base** every turn — so a node's state
is a pure function of the *current script*, not of history. If the agent deletes an install line,
the node must flip back to `MISSING`. Carrying state forward is the staleness bug.

---

## 5. The unified observation

**One container round-trip per turn. Two typed outputs. The function observes; the caller decides
what structure to grow.**

```python
def observe_and_certify(
    graph: DepGraph,
    executor: Executor,
    *,
    script: str | None = None,          # what was ATTEMPTED
    failed_lineno: int | None = None,   # where the script died
    cycle: int = 0,
) -> tuple[DepGraph, list[tuple[str, str]]]:
    """ONE exec. Replaces certify_all's ~936.

    Returns (graph_with_states_written, observations)
      - graph        : the STATE axis
      - observations : (command, output) pairs ready for ingest_runtime_failures.
                       The STRUCTURE axis. NEVER applied here.
    """
```

### 5.1 The four reads

```
A. PRESENCE CENSUS  (state — Package / Tool / SystemLib / apt)
     python -m pip list --format=freeze
     dpkg-query -W -f='${Package}\n'
     ldconfig -p
     ls -1 $(echo "$PATH" | tr ':' ' ')

B. FUNCTIONAL PROBE (state — Import; plus error text with EXACT owner attribution)
     for m in <every Import node name>:
         python -c "import $m"          # ISOLATED subprocess, see 5.3

C. ARTIFACT PROBE   (STRUCTURE — authoritative; this is `ldd_probe`, already written)
     for each installed dist D:
         for each .so in RECORD(D):
             for each soname in DT_NEEDED(.so):
                 node  syslib:<soname>                       (append if new)
                 edge  pkg:D --requires--> syslib:<soname>   (append if new)  ← EXACT owner
                 resolved on the loader path ?  SATISFIED : MISSING

D. PROVIDER LEARNING (structure — closes the PROVIDER_TABLE gap empirically)
     dpkg -S <resolved path of the soname>   →   syslib:libpq.so.5 → apt:libpq5
```

### 5.2 The state truth table

A census answers *present / absent*. It **cannot** distinguish "we installed it and the install
failed" from "the script died at line 12 and never reached it". Both look absent. So state is a
function of **two** inputs:

```
   present?     attempted?        →   state
   ────────────────────────────────────────────
   yes          —                 →   SATISFIED
   no           yes               →   MISSING     (we tried; it isn't there)
   no           no                →   UNKNOWN     (the build died before we got here)
```

`attempted` is derived from the script and `result.lineno`. **A census alone is not sufficient**
— an earlier draft of this design claimed it was, and that was wrong.

### 5.3 Why the import probe must use isolated subprocesses

The tempting implementation — one Python process, `importlib.import_module` in a loop — is a trap:

- **a segfault kills the whole probe** (one bad native extension loses all N results);
- **`sys.modules` pollution masks failures**: if `A` imports `B`, then `B` later tests as
  importable even when it is not independently. That is a **false SATISFIED** — the worst error
  this system can make;
- **import side effects** — modules that spawn threads, open sockets, or call `sys.exit`.

So: **one exec, N isolated subprocesses inside it.** ~50 ms each. Still 1 round-trip instead of N.

---

## 6. What `certify_all` becomes

Not deleted — **demoted to the exception path.**

Verified `check_command` shapes in the 16 real minted graphs:

| tier | count | current check | census? |
|---|---:|---|---|
| Package | 815 | `python -m pip show <X>` | ✅ presence → `pip list` (1 exec) |
| SystemLib | 10 | `ldconfig -p \| grep <soname>` | ✅ presence → `ldconfig -p` (1 exec) |
| Tool | 111 | `dpkg -s <X>` / `command -v <X>` | ✅ presence → `dpkg-query` + PATH (2 execs) |
| **Import** | **164** | `python -c "import X"` | ❌ **FUNCTION, not presence** — batch it, don't drop it |
| Runtime | 16 | version assert | trivial |
| **Project** | **16** | **NONE** | 🔴 see §8.1 |
| Service / Config | few | bespoke probes | ❌ keep `certify` |

**936 of the checks are pure presence** → the census collapses them from 936 execs to ~4.
**`Import` is a functional check and must survive** — presence ≠ function, and function is exactly
where native-dependency bugs live.

Keep certify's *semantics* either way: `certified_cycle`, evidence preservation (discovery
evidence beats check stderr), the Service anti-deadlock demote counter. The census is a cheaper
**source** for the same rules, not a different rulebook.

---

## 7. What an error actually means

A failure is evidence about **which of three things the graph is short of**. Conflating them is a
bug:

| the graph has… | the error means | action |
|---|---|---|
| node **and** edge | **state gap** — the graph was RIGHT, the env is behind | set MISSING; install it. **No structure change.** |
| node, but no edge from this package | **a missed requirement** | **add the edge** |
| no node at all | **under-prediction** | **add the node + edge** |

The isolated import probe is what lets you tell them apart without guessing, because the
experiment is controlled: `python -c "import psycopg2"` failed, therefore whatever is missing is
needed *by psycopg2*. That attribution comes from the **experimental design**, not from anything
the graph happened to contain.

---

## 8. Corrections to earlier claims (recorded so they are not re-made)

### 8.1 The `import:X --requires--> pkg:X` edge is NOT guaranteed

An earlier draft asserted the graph "already knows" the owner of a failing import via this edge.
**Measured across the 16 real graphs: it exists 113/164 times (69%).** It is *missing* for
`psycopg2` — and `pkg:psycopg2==2.9.12` is one of 8 packages in the corpus with **no inbound edge
at all**.

Consequences:
- The owner of a failing import must be recovered by a **function** (`map_import_to_package`),
  not by assuming an edge. Where the mapping fails, fall back to `TEST_NODE_ID` — never guess.
- `tests/react_repair/test_graph_arm_e2e.py` **hand-builds this edge in its seed graph.** That is
  the same unrealistic-fixture trap the eval's own review caught in `patch_localize`. The demo's
  "G3 solves in one turn vs G1 gives up after ten" result is therefore weaker than it looked and
  should be re-run against a construction-derived seed.
- `project:<repo>` has **no `check_command` at all** → `UNKNOWN` in 16/16 graphs → the Test goal
  node comes out ACTIONABLE while its own project is uncertified. Giving Project a check
  (`pip show <project>`) closes a bug measured in every single repo.

### 8.2 `owner_anchored = 0/14` is largely a CORPUS artifact

That number was measured against **repo2run's** Dockerfiles, which batch
(`pip install -r requirements.txt`). Our arm renders its own `setup.sh` with **one `pip install`
per package**, so *our* failing commands do name a single package. The 0/14 measures repo2run's
script style, not our grounding capability.

### 8.3 Recovering the owner from a batched install: only the STRICT rule is sound

Tempting heuristic: scan backwards from the error for the last `Collecting X`. It recovers a name
in 19/59 build failures — **and the names are frequently wrong**: `pip`, `setuptools`, `mdurl`,
`typing-inspection` — i.e. whatever pip happened to be downloading when an unrelated (usually
SSL/network) failure hit. It would hang a `pg_config` discovery off `pkg:pip`.

**Sound rule, and the only one permitted:** attribute *only* from pip's own subprocess-error block,
which explicitly names the package it was building (`Building wheel for X … error`). Otherwise
fall back to the Test node. This upholds `owner_node_for_command`'s existing doctrine:

> *"Losing depth is recoverable; attaching a discovery to the wrong package version is not."*

### 8.4 Capability nodes are not deduplicated by ACTION

Construction mints capability nodes in **five** id spaces (`binary:` 10, `aptdep:` 86, `tool:` 11,
`syslib:` 10, `linker:` 4). They alias each other. Real example — `pygraphviz` predicts **four**
separate MISSING nodes that one `apt-get install libgraphviz-dev` satisfies:

```
linker:gvc · linker:cgraph · linker:cdt · aptdep:libgraphviz-dev   →   all apt:libgraphviz-dev
```

The renderer stars all four. **This is why star precision measured 0.25** — it is not a ranking
weakness in the renderer; construction hands it duplicates. The modelling error is conflating a
**requirement** (`linker:cgraph`) with a **provider** (`aptdep:libgraphviz-dev`); the provider
should be the `chosen_fix` *on* the requirement, not a node beside it.

**Fix:** key the actionable frontier by the install **action**, not by the symptom name.

```
★ apt:libgraphviz-dev                              MISSING
    satisfies  linker:gvc · linker:cgraph · linker:cdt
    why        pkg:pygraphviz --requires--> all three
    fix        apt-get install -y libgraphviz-dev
```

This is the same class as the `tool:`/`binary:` fracture fixed at `b5a9f65` — but that fix only
reconciled the **runtime** producers. **Construction still fractures.**

---

## 9. Non-goals

- **Pruning over-prediction.** Execution corrects under-prediction cheaply (a failure names the
  missing thing) and over-prediction only at ruinous cost (remove X, rebuild, see if it still
  works — one full container rebuild per hypothesis). The package layer already over-includes
  (precision ~0.70). **Grow aggressively; prune essentially never.** An over-install costs a wheel
  download; an under-install costs a rebuild.
- **Retraction of wrong nodes.** Prevent at the source (§3.1: never classify output from a command
  that *succeeded*) rather than un-learn. Where a node's fix candidates are all exhausted, mark it
  **fix-exhausted** — it stays true that something is missing; the graph simply admits it does not
  know how to fix it. Do **not** add speculative decay heuristics ("drop nodes unused for K turns")
  — that trades a known failure mode for an unknown one, with no evidence to tune it.
- **Making the graph answer source-level failures.** ~65% of real repair rounds in the corpus are
  source patches (a `conftest.py` stub, a mocked module, a circular import). The graph is *right*
  to model none of them. See §10.

---

## 10. The ceiling, stated honestly

Of 111 real repair rounds mined from `outputs/repo2run_benchmark/`:

| kind | n | can the graph address it? |
|---|---:|---|
| os-package (apt) | 3 | **yes — its core competency** |
| python-package (pip) | 11 | yes, but the LLM already knows most of these |
| env-var (Config) | 25 | yes (Config nodes) — currently unscored |
| **source patch** | **72** | **no — structurally outside the model** |

Even a perfect graph pre-empts a minority of real repair work. This is a **ceiling**, not a bug
list. It means:

1. The graph's realistic contribution is concentrated in the **OS/capability tier**, where the
   model's own world knowledge is weakest (`pg_config → libpq-dev` is a Debian packaging fact, not
   a Python fact — there is no string transformation from one to the other).
2. On the other 65%, the highest-value thing the graph can emit is the **negative verdict** —
   *"the environment is fully certified; stop looking here"* — which prevents the agent from
   burning rebuilds chasing a phantom package. `ModuleNotFoundError: No module named 'comfy'`
   *looks* exactly like a missing dependency and is not one. **This is unmeasured and is the
   biggest untested upside in the design.**

---

## 11. Falsification criteria

This design is wrong if:

- Wiring `ldd_probe` into the per-turn loop does **not** move enrich pre-emption above 2/14. That
  would mean the missing-runtime-`.so` class is rarer than §2 claims, and error-text enrichment was
  not the bottleneck.
- The artifact probe produces **false** SystemLib edges (a `DT_NEEDED` entry that is not really a
  requirement). Measure: hallucination rate on the negative-control slice must stay at 0.
- The census-based state derivation disagrees with `certify_all` on any node of the 16 cached
  graphs. **Zero divergences is the pass bar** — reuse `src/eval/graph_quality/block_parity.py`'s
  differential harness to check it.

**A bad measured number is a result, not a failure to tune away.**

---

## 12. Implementation order

Ranked by *"what currently makes the graph worse than no graph"*, because those defects would
poison a G1/G2/G3 ablation and get misattributed to "graph guidance doesn't work":

1. **Attempt reconciliation + fix ladder.** The renderer's anti-thrash field
   (`graph_context.py:384`, *"agents re-retry disproven fixes because their memory is lossy prose"*)
   is fed by **nothing** — `with_attempt` is called only from `probe.py` / `ldd_probe.py` /
   `envstate/`, never from the react loop. So the graph re-proposes a fix the agent already tried
   and watched fail. This is the only defect that produces a **deadlock** rather than a wasted turn.
2. **Fix-keyed collapse** (§8.4) — restores the arm's headline structural claim.
3. **Wire `ldd_probe` into the per-turn observe step** (§5.1 C) — the single highest-leverage
   change; a call site, not a subsystem.
4. **Failing-region-only enrichment** (§3.1) — kills the hallucination class.
5. **`lineno`-aware UNKNOWN** (§5.2) — stops starring packages the aborted script never reached.
6. **Census-backed state** (§5.1 A/B, §6) — makes 1–5 affordable (936 execs → ~4).

*Then* the ablation is valid. Run G1 vs G3 **sliced by OS-tier vs Python-tier failures** — pooling
them will wash out the effect, because the theory predicts the entire lift lives in the OS tier.

---

## 13. Provenance of every claim in this document

Measured or read from source during the 2026-07-13/14 session — not inferred:

- `ldd_probe` docstring + its single call site: `ldd_probe.py:1`, `build.py:1016`, zero in `src/react_repair/`.
- `check_command` shapes per tier: read from the 16 committed graphs in `src/eval/graph_quality/graphs/`.
- `import:X → pkg:X` edge coverage 113/164; 8 parentless packages: same corpus.
- Capability id-space census (`binary` 10 / `aptdep` 86 / `tool` 11 / `syslib` 10 / `linker` 4) and
  the `pygraphviz` 4-nodes-one-action case: same corpus.
- Corpus label distribution (3 / 11 / 25 / 72 of 111) and pre-emption 2/14: `src/eval/graph_quality/`.
- The `librabbitmq4` hallucination: `line__lighthouse` round 1, real stderr.
- The "last `Collecting X`" heuristic returning `pip`, `setuptools`, `mdurl`: measured over 59
  build-stream pairs.
