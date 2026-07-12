# Graph-guided react arm — failure-anchored context + observation-driven graph update (design spec)

**Status:** Rev 3 (2026-07-13). Design approved, not yet implemented.
**Arm:** the graph-guided variant of `--arm react` (today a `future flag`; see
[[react-script-repair-arm-design]]). Ablation partner of the script-only react agent.
**Every claim below is grounded in current code (file:line inline).**

**Revision history.**
- **Rev 1** — collapsed-roots verdict. Rested on a false premise (`pkg→pkg` edges don't
  exist — they do, in bulk) and handed the agent a bare answer with no structure.
- **Rev 2** — fixed the premise; replaced the verdict with a *failure-anchored graph patch*
  (edges + certified states, both directions, root marked); added the per-turn state delta
  and the no-explanation flag.
- **Rev 3 (this)** — the graph stops being read-only. Adds the pytest **phase model** (§4)
  as the structural gate on what may touch the graph, corrects a **unit-mixing bug** in the
  cause parser that inverts our ranking (§4.2), and specifies **observation-driven graph
  update** (§7): two ingest streams and three expansion mechanisms, ordered by evidence
  strength. Reverses Rev 2 §9 — mid-loop mutation is *safe*, and the earlier objection to it
  was wrong.

---

## 1. Goal

Give the react repair agent a **graph layer** that (a) turns a wall of pytest failures into
local, certified causal structure, and (b) **grows with what the run observes**, so a
requirement static construction missed becomes a real node the moment the container reveals
it.

pytest gives the failure classes and the counts. The graph gives the edges, the certified
states on those edges, and the reachability the agent would otherwise recompute by hand.

---

## 2. Ablation invariant

The **only** delta between the script-only agent and the graph arm is the `graph_context`
slot on the planner (`planner.py:129,150-152`). Seed script, loop, gate, history, tools —
byte-identical. Today `entry.py:162` hardcodes `ctx = None`; the arm populates it.
`run_react_arm` already takes the `graph_context: bool` flag (`entry.py:157`).

| rung | `_OBS_MODE` (`loop.py:46`) | `graph_context` | graph update (§7) | status |
|---|---|---|---|---|
| **G0** raw reactive floor | `raw` (pass-count + tail) | `None` | — | ships |
| **G1** presentation control | `histogram` (ranked causes) | `None` | — | ships |
| **G2** graph, read-only | `histogram` | `render_graph_context` | frozen topology | **this spec** |
| **G3** graph, growing | `histogram` | `render_graph_context` | **§7 on** | **this spec** |

Two comparisons, each isolating one claim:

- **G1→G2** — what does the *graph* add on top of the ranked histogram the agent **already
  sees in G1**? (topology + certified state). This is why G1, not G0, is the control that
  matters: G0→G2 would conflate presentation with structure.
- **G2→G3** — what does letting the graph **grow from observations** add on top of a
  read-only graph? This is the §7 claim, and it needs its own rung or a G3 lift would be
  wrongly credited to the renderer.

Hence two flags (§9), not one: `REACT_GRAPH_CONTEXT` (G2) and `REACT_GRAPH_UPDATE` (G3).

### 2.1 Why mid-loop mutation does NOT break this (reverses Rev 2 §9)

Rev 2 refused to mutate the graph mid-loop, on the grounds that a trajectory-dependent graph
would break the byte-identical-seed invariant. **That was wrong.** `loop.py:187-188` renders
the seed script from the graph exactly **once**, before turn 1; from then on the agent edits
script *text* and nothing ever re-renders from the graph. The mutated graph's only consumer
is `graph_context`, which G0 and G1 do not read. Mutation therefore cannot leak into the
control arms, and the invariant holds trivially. Rev 3 ships mid-loop update.

---

## 3. Verified primitives (already in the tree)

| primitive | location | role |
|---|---|---|
| `graph_context` seam | `planner.py:129,150-152`; `entry.py:162` | integration point (currently `None`) |
| `Cause(exc, detail, count, outcome, module)` + `summarize()` | `pytest_summary.py:41,105` | the failure classes (see §4.2 — **count is buggy**) |
| `diagnose(cmd, out, ctx)` → `Mode` + `Discovery` | `diagnose.py:86` | route a cause into 5 modes |
| `make_diagnostic_classifier(ctx)` | `diagnose.py:183` | ENVIRONMENT-only ingest seam |
| `ingest_runtime_failures(graph, obs, clfs, owner)` | `runtime_ingest.py:165` | append/annotate; **not wired into `react_repair/`** |
| `_provider_from_command(cmd)` | `req_slice.py:38` | reverse-parse `pip install psycopg2==2.9.12` → `pip:psycopg2` |
| `build_dep_prior(name, version, executor)` | `build_deps.py:167` | **already per-package**: Debian build-deps + capability needs |
| `seed_build_deps(graph, executor)` | `build_deps.py:286` (loop body `:307-355`) | graph-level caller of the above |
| `ldd_probe` | `ldd_probe.py:211` | real `DT_NEEDED` → SystemLib (needs the pkg **installed**) |
| `wheel_preflight_probe` | `wheel_preflight.py:50` | pre-install soname prior from the target wheel |
| `requires_of` / `required_by` | `schema.py:348,359` | down-walk / up-walk (blast radius) |
| `certify_all(graph, executor, layer_order)` | `certify.py:118`; wired `entry.py:75-76` | refreshes node **states** against the live container |
| `State` ∈ `{UNKNOWN, MISSING, SATISFIED}` | `schema.py:56-61` | certified state on every node |

### 3.1 The graph is re-certified every turn — states are never stale

`loop.py:190-207` (`build_and_test`): **reset → run the whole script fresh from base →
`g = certify(graph)` → run tests.** The planner is then handed that graph (`loop.py:246`).

- **State staleness is a non-problem.** The graph the agent sees was certified against the
  container its own script just built, this turn, before it acts.
- **Script drift is captured for free.** If the agent's edit drops `faker`, the rebuilt
  container won't have it and `certify` marks `pkg:faker` MISSING by itself. We never diff
  the script against the graph — the **container** is ground truth and certification reads it.
- **Certification is also what makes §7's speculation safe.** A node we add on a *prior*
  rather than on evidence is certified against the real container before the agent ever sees
  it: a wrong prior comes back SATISFIED and renders harmlessly in a rule-out ring. The agent
  is never shown a claim we have not verified.

What certification **cannot** do is **add nodes**. It runs `check_command` per *known* node.
That is the entire subject of §7.

### 3.2 `pkg→pkg` edges exist, in bulk (Rev 1 was wrong)

`resolve_lock.py:425-451` emits `Edge(src=<Package>, dst=<Package>, REQUIRES, origin="resolver")`
for every entry in each locked package's `dependencies` list; `resolve.py:415-437` does the
same over the `uv pip compile` closure. A real repo's pip closure is hundreds of such edges.

> **Traverse them; never *render* them individually.**

One summary line per anchor — `+ 37 transitive pip deps: 37 satisfied` — recursing only into
MISSING ones. The closure came verbatim from the lockfile and pip re-derives it at install
time; enumerating it buries the handful of edges that carry information.

**Bound the walk by edge *type*, not hop radius.** Four hops down the system tier
(`Test → Import → Package → Tool → AptDep`) is five nodes of pure signal; two hops through
the pip closure is hundreds of nodes of noise. The dense edges are the **cross-tier** ones —
`Package → SystemLib` (`ldd_probe.py:211`), `Package → Tool/AptDep/capability`
(`build_deps.py:319,343,353`) — exactly where an LLM's prior is weakest and pytest is blind.

---

## 4. The pytest error model — the structural gate (new in Rev 3)

pytest has **two phases with different error models and different granularity**. This is not
a detail; it is the cleanest signal we have about whether an observation may touch the graph.

### 4.1 The four outcomes

| outcome | when | granularity | meaning |
|---|---|---|---|
| **collection error** | import/module load, before any test runs | **per FILE** | the file could not be imported — its tests *never existed as items* |
| **setup error** | fixture setup, per collected test | per test | the test exists; its *scaffolding* broke |
| **call failure** | the test body | per test | the test ran and **its own code decided it was wrong** |
| **teardown error** | fixture cleanup | per test | cleanup broke, on top of whatever `call` did |

A module with 200 tests that fails to import produces **one** collection error, not 200.
Default behavior on any collection error is `raise session.Interrupted` → **exit 2, zero
tests run**; `--continue-on-collection-errors` (→ exit 1) is what keeps the rest countable.
Gate on **error blocks, not on `rc`** (same landmine as [[essr-denominator-is-agent-chosen]]).

### 4.2 🔴 Bug: `summarize()` mixes files and tests in one integer

`pytest_summary.py:118`:

```python
"outcome": "ERROR" if title.startswith("ERROR") else "FAILED"
```

pytest emits **both** `___ ERROR collecting tests/test_math.py ___` and `___ ERROR at setup
of test_query ___`. Both start with `ERROR`, so both land in one bucket, and
`format_breakdown` then tags a *run-phase* setup error as `[collect]`.

The damaging consequence is `Cause.count`. For a **collection** error one block = one
**file**; for a setup error or a failure one block = one **test**. So `count` sums *files and
tests into the same integer* — and we rank by it (`pytest_summary.py:123`). A collection
error hiding 200 tests in one file counts as **1**; an `AssertionError` across 23 tests counts
as **23**. **We rank the assertion above the import error — exactly backwards**, and it is
the same unit-mixing class of bug as the junit attr-vs-element one in the ESSR work.

**Fixes, both required:**

1. **Parse the phase from the banner**, not from a `startswith`. Add `phase ∈ {collect,
   setup, call, teardown}` to `Cause`; derive `outcome` from it. `ERROR collecting <file>` →
   `collect`; `ERROR at setup of <test>` → `setup`; `ERROR at teardown of <test>` →
   `teardown`; otherwise → `call`.
2. **Weight collection errors by tests hidden, not files hit.** pytest *cannot* tell us — the
   tests were never discovered. Take the weight from outside pytest:
   - **default:** static count of `def test_` / `async def test_` in the named file
     (`Cause.module`). Cheap, always available, honest. Under-counts `parametrize` expansion —
     **render it as an estimate (`~200 tests`), never as a measured count.**
   - **override:** the per-file node-id count from the gold manifest where we have one
     (eval only; see [[collection-manifest-builder-design]]). Exact.

Until (2) lands, `tests blocked` in §6 is not a number we can honestly produce for the
dominant failure class.

### 4.3 The phase → graph-update permission table

The phase tells us *structurally* — with no LLM judgment — whether an observation is allowed
to touch the graph:

| phase | may mutate the graph? | what it may add |
|---|---|---|
| **collection error** | **yes** | Package / Import — a **direct test dependency** |
| **setup error** | **yes** | Service / Config / DataAsset (a fixture needing a live dep) |
| **call failure** | **never** | nothing — this is residual logic |
| **teardown error** | **never** | nothing |

*"The test's own code decided it was wrong"* is precisely the line between **no env fix
exists** and **an env fix exists**. A fixture raising `ConnectionRefusedError` on
`localhost:5432` is a Service node. An `AssertionError` in a test body must **never** mutate
the graph, however its message reads. `diagnose()` currently has to infer this from message
text; the phase gives it away for free and we are throwing it away in the parser.

---

## 5. The two-tier triage

- **Tier 1 — collection triage** (`pytest --collect-only --continue-on-collection-errors`).
  No execution. `summarize()` parses the `ERROR collecting` blocks **byte-identically** to a
  full run. This is where the graph pays most, and it is the *denominator-restoring* repair:
  an unimportable module is exactly what silently hides tests, so fixing collection heals the
  env **and** un-hides tests.
- **Tier 2 — execution triage** (full run, once collection is clean). Surfaces setup errors
  (env-relevant) and call failures (residual).

The renderer takes a `phase` so "this is not an environment problem" is only asserted after a
full run — a residual seen under `--collect-only` merely means the body never ran.

---

## 6. The renderer — what the agent sees

One block per failure class, anchored at the graph node the failure names. Edges in **both**
directions, certified state on every one, the actionable node marked.

```
FAILURE  ModuleNotFoundError: No module named 'psycopg2'   [collect]  (~200 tests hidden, est.)
  ↓ maps to   pkg:psycopg2==2.9.12          MISSING

  what it requires:
    binary:pg_config        MISSING     ← nothing below this is missing. fix here.
        fix    apt-get install -y libpq-dev
        check  command -v pg_config
    binary:pkg-config       check passed: command -v pkg-config
    tool:build-essential    check passed: dpkg -s build-essential
    + 37 transitive pip deps: 37 satisfied

  what requires it:
    import:psycopg2 → tests/test_db.py (est. 200 hidden)
    also blocks pkg:asyncpg — same root

FAILURE  AssertionError in test_totals   [call]  (23 tests)
  ↓ NOT AN ENVIRONMENT FAILURE (test body). No env fix exists. Graph not consulted.

FAILURE  ModuleNotFoundError: No module named 'patchright'  [collect]  (~8 tests hidden, est.)
  ↓ THE GRAPH HAS NO EXPLANATION. Requirement is outside the model — explore.

SINCE YOUR LAST EDIT
  pkg:lxml   SATISFIED → MISSING   (your patch dropped it)
  3 nodes    MISSING → SATISFIED
  + discovered this turn: binary:pg_config (from your failing pip install)
```

**Why `← fix here` is not "a verdict instead of structure."** The edges and states above it
*are* the reasoning material. The marker only answers a question the agent would otherwise
recompute by hand across every block — *which of these missing nodes has nothing missing
beneath it*. That is a deterministic BFS over states certified against the live container
this turn: correct given the graph, twenty unit-testable lines, and far cheaper than making
the model re-derive topological reachability in token space. Nothing is hidden; the agent can
audit the chain and disagree.

### 6.1 Signature

```python
def render_graph_context(graph, causes, prev_states, ctx, phase) -> str:
    """Pure. causes = summarize(output); prev_states = {node_id: State} from last turn;
    ctx = RepoContext(local_names, invalid_names); phase in {"collect", "run"}."""
```

`ctx` is per-run constant, so `entry.py` binds it by closure and the **planner seam carries
four args** — `graph_context(graph, causes, prev_states, phase)`. Pure, no I/O: every state it
reads was certified by `loop.py:196`; every node it reads was added by §7 *before* the render.

- **Anchor** the top 3 causes by weighted tests-blocked (§4.2); the rest render as one-line
  tallies. Match a Discovery to an existing node by normalized name (`runtime_ingest.py:86-90`
  — reuse, do not reimplement).
- **Down-walk** `requires_of`, expanding only non-SATISFIED nodes, unbounded depth (real
  MISSING chains are short). Around each MISSING node list its immediate SATISFIED siblings as
  a compressed rule-out ring (name + `check passed: <cmd>`, no fix detail). **Never expand a
  SATISFIED node's children.** Collapse `pkg→pkg` per §3.2.
- **Roots** = MISSING/UNKNOWN nodes with no MISSING/UNKNOWN prerequisite.
- **Up-walk** `required_by`, one hop, aggregated; list individually only if ≤3.
- **Regime guard.** If MISSING exceeds ~15 (e.g. the venv never materialized and the whole
  closure flipped MISSING), enumeration is useless — collapse hard to roots:
  *"212 packages MISSING, all downstream of: python venv creation failed."*

### 6.2 The state delta

`build_and_test` (`loop.py:190`) closes over `graph`, and `loop.py:227,277,291` rebind it — so
the previous turn's certified graph is simply the value of `graph` before the call. Capture
`{n.id: n.state for n in graph.nodes}` before, diff after. Three lines. It makes the graph a
**regression attributor** for the agent's own edits, a signal the pytest histogram carries only
noisily through shifting counts — and it is also the **trigger for §7 Mechanism 2**.

---

## 7. Observation-driven graph update (the core of Rev 3)

`certify` refreshes **states**; it can never add **topology**. A requirement construction
never modeled has no node and no check and stays invisible forever. §7 fixes that.

**The governing principle — and the line this project has already paid to learn.** The
deleted import→dist identity fallback took wrong-guesses from 6 → 0 by replacing *inference*
with a typed `unresolved` ([[phase2-identity-fallback-deletion-landed]]). Rev 3 does **not**
reintroduce guessing. The reframe:

> **A runtime discovery is a new declared root. Feed it back through construction.**

We never *guess* what a discovered node needs. We **resolve** it, with the same oracles that
built the graph — `build_dep_prior`, the wheel/ldd soname probes, `os_resolver`. Those are
resolvers, not guessers.

### 7.1 Two ingest streams, two anchors

Splitting the streams dissolves the "anchor quality bounds everything" worry from Rev 1:

| stream | source | owner anchor | why it's right |
|---|---|---|---|
| **build stdout** | `setup.sh` run — `result.failing_command` + output | `_provider_from_command(cmd)` → `pip:psycopg2` (`req_slice.py:38`) | **exact.** The per-package-install directive ([[per-package-install-no-batch]]) guarantees the failing command names exactly one package. This is where transitive **depth** comes from. |
| **pytest output** | collection / setup errors | `TEST_NODE_ID` | **correct by construction.** A test-file import genuinely *is* a direct dependency of the test goal — not a degenerate star. This is where **breadth** comes from. |

A batched `pip install a b c` makes `_provider_from_command` return `None` and the node falls
back to the Test anchor — which is why the per-package directive is load-bearing here, not
merely tidy.

### 7.2 Three mechanisms, ordered by evidence strength

Take the strongest available; fall back to speculation last.

**Mechanism 1 — Ingest (pure evidence, no speculation).** The error *names* the requirement.
`pip install psycopg2==2.9.12` failing with `pg_config: not found` is a fact, and the command
names its owner. Fires on every turn's build output and pytest output, gated by §4.3.

```python
# loop.py, after build_and_test()
owner = _provider_from_command(result.failing_command)          # req_slice.py:38
graph, discoveries = ingest_runtime_failures(                   # runtime_ingest.py:165
    graph, [(result.failing_command, result.output)],
    classifiers=[make_diagnostic_classifier(ctx)],              # diagnose.py:183
    owner_node_id=owner,
)
```

Fully built; **simply not wired into `react_repair/`**.

**Mechanism 2 — Probe (evidence from the container).** The moment a package installs, its
`.so` files are in the container and `ldd` reports real `DT_NEEDED` — no guessing. The §6.2
state delta tells us exactly which packages just installed:

```python
newly_satisfied = [nid for nid, st in prev_states.items()
                   if st is not State.SATISFIED and graph.get(nid).state is State.SATISFIED]
for nid in newly_satisfied:
    graph = ldd_probe_for(graph, graph.get(nid), executor)      # real DT_NEEDED → SystemLib
```

Pre-empts the *runtime* import failure (`libpq.so.5: cannot open shared object file`) one turn
before it happens. Cost: one `ldd` per newly-installed package.

**Mechanism 3 — Prior (speculation, gated).** Pre-empts a *build* failure, which Mechanism 2
structurally cannot — the package never installed, so there is no `.so` to probe.

```python
plan = build_dep_prior(pkg.name, pkg.version, executor)         # build_deps.py:167
# → capability_needs (binary:pg_config) + apt_directives (libpq-dev)
graph = seed_build_deps_for(graph, pkg, executor)
```

**Gate:** expand **only a discovery that actually resolved.** If the name does not resolve
against a real oracle, mark it `unresolved` and expand **nothing** — expansion propagates a bad
anchor's wrongness through a whole fabricated subtree. This is the 6→0 property.

### 7.3 Why this is the turn-economy win

Each turn costs a **full container rebuild** (`reset → run whole script → certify → test`).
Every hop the agent must discover *serially* costs one:

```
without expansion            with expansion
turn 2  pip psycopg2 → fails   turn 2  discover pkg:psycopg2 → resolve its build-deps NOW
        "pg_config not found"          agent sees pkg:psycopg2 MISSING *and*
turn 3  apt libpq-dev; retry           binary:pg_config MISSING (fix: libpq-dev)
                                       → patches both in ONE turn
```

In a 30-turn budget, collapsing the serial discovery chain is plausibly worth more than the
root-cause ranking is.

### 7.4 Refactor required — expose the per-node bodies

`build_dep_prior` is **already per-package**. What is graph-level is only its *caller*. Both
passes have the same shape; split the body out and let the existing pass become the loop:

```python
def seed_build_deps_for(graph, pkg, executor) -> DepGraph:   # NEW — the old loop body verbatim
def seed_build_deps(graph, executor) -> DepGraph:            # now just the loop
    for pkg in _eligible(graph):
        graph = seed_build_deps_for(graph, pkg, executor)
```

Zero behavior change to construction — same code, same order, same dedup — and the react arm
gets a per-node entry point. Same shape for `ldd_probe`. New module
`depgraph/discovery_expand.py` sequences the three mechanisms.

**🔴 Do NOT re-run `_phase_a_fixpoint` (`build.py:336`).** It is the whole resolve → install →
probe → repair fixpoint with network calls and real container installs; it re-resolves the
entire closure and would blow the turn budget. The **per-node oracles** are what we want; the
fixpoint is not.

### 7.5 Limits, stated plainly

- `build_dep_prior` needs a **version**; a node discovered from `ModuleNotFoundError:
  patchright` has none, and `seed_build_deps` explicitly skips versionless packages
  (`build_deps.py:308`). Mechanism 3 therefore needs a name→version resolve step first (one
  `uv` call). If it does not resolve → `unresolved`, expand nothing (§7.2 gate).
- `ldd_probe` needs the package **installed**. It can never run speculatively. That is exactly
  why Mechanism 3 exists.
- **Append-only. Never delete.** A pip-disproven name goes into `RepoContext.invalid_names`
  (diagnose's `INVALID_ATTEMPT`) so it is *marked*, not removed.
- Per-turn cost is `O(discoveries + newly_satisfied)`, never `O(graph)` — we only ever expand
  nodes that **changed this turn**.

### 7.6 Free result: discoveries are a construction-coverage oracle

Every runtime discovery is, definitionally, **something static construction missed**. If the
constructor were perfect the react arm would discover nothing. So `discoveries per run` points
directly at where `scan`/`resolve`/`build_deps` are blind, per repo — the repair loop measuring
the constructor. Log every discovery with its stream and mechanism.

---

## 8. Two honesty rules

**`SATISFIED` means the check exited 0 — not that the node is correct.** `command -v pg_config`
is an *existence* probe, not a version/ABI probe. A node can certify SATISFIED and still be the
root cause (wrong libssl, python floor-trap, stale wheel). A confident verdict would silently
*exclude* it — the worst failure mode, because the agent stops looking. Render `check passed:
command -v pg_config`, **never** `OK`. `check_command` is an affordance the agent may extend in
an `explore` move.

**The blind spot is a computed flag, not a disclaimer.** When a failure class anchors to nothing,
or to a node whose entire modeled closure is SATISFIED, say so out loud: **"the graph has no
explanation for this failure; the cause is outside the model — explore."** That converts
incompleteness from a silent trap into a routing signal — and it is only computable *because* we
do the walk.

---

## 9. Build surface

| change | file | size |
|---|---|---|
| `phase` on `Cause`; parse it from the banner; derive `outcome` from `phase` | `pytest_summary.py:41,118` | small — **fixes §4.2** |
| tests-hidden weight for collect-phase causes (static `def test_` count; gold-manifest override) | `pytest_summary.py` + new helper | small |
| `render_graph_context` + `_anchor`/`_down_walk`/`_roots`/`_delta`/`_format` | **new** `depgraph/graph_context.py` | the render logic |
| `expand_discovery` sequencing Mechanisms 1–3 | **new** `depgraph/discovery_expand.py` | the update logic |
| extract per-node bodies: `seed_build_deps_for`, `ldd_probe_for` | `build_deps.py:286`, `ldd_probe.py` | pure refactor, **no behavior change** |
| widen seam `graph_context(graph)` → `(graph, causes, prev_states, phase)` | `planner.py:129,150-152`, `plan()` sig | ~5 lines |
| hoist `causes` out of `_observation`; capture `prev_states` before `certify`; call `expand_discovery`; thread to `plan()` | `loop.py:86,190-207,246` | ~12 lines |
| Tier-1 collect-only pass before the full run | `loop.py` (`build_and_test`) | small |
| build `RepoContext` from `repo_path`; wire renderer; env flags `REACT_GRAPH_CONTEXT`, `REACT_GRAPH_UPDATE` | `entry.py:156-164` | ~10 lines |

`repo_path` already reaches `run_react_arm` (`entry.py:156`), so `RepoContext` needs no new
plumbing. Reuse `req_slice.build_requirement_slice` (`req_slice.py:135`) where it already
projects deps+states+`unblocks`+`chain_to_goal` — do not reimplement.

Two flags, not one: `REACT_GRAPH_CONTEXT` (render only, frozen topology) and
`REACT_GRAPH_UPDATE` (render + §7). That makes **read-only graph** vs **growing graph** its own
ablation rung, so we can attribute any lift to the right half.

---

## 10. Metrics (trace-computable, no pass-rate wait)

- **anchor hit rate** — fraction of failure classes that mapped to a node. Upper-bounds
  everything else; if low, the graph is being ignored, not consulted.
- **collapse ratio** `N causes / K roots` — structure recovered.
- **first-patch-targets-root** — did the agent act on a marked root, or on a symptom?
- **no-explanation rate** — how often the graph honestly had nothing (§8). A *feature*: it
  should correlate with the agent choosing `explore`.
- **delta-attributed regressions** — how often `SINCE YOUR LAST EDIT` caught the agent breaking
  something, and whether it then reverted.
- **discoveries per run**, by stream and mechanism (§7.6) — the construction-coverage oracle.
- **turns saved by expansion** — count of turns where a Mechanism-2/3 node was MISSING *and* the
  agent fixed it in the same turn as its owner. This is §7.3's claim, measured.

**Honest expectation:** a `#@node` census suggested roughly half of Package nodes are leaves
(`requires=-`), so for them `K == N` and collapse does nothing. The technique buys the
syslib/tool-chain half, plus blast-radius, the delta, and §7's growth — which apply everywhere.
⚠ That census was measured on the *emitted* `#@node` view, which may filter the requires list —
**re-derive it against the live graph before quoting it.**

---

## 11. Acceptance

1. **§4.2 fixed:** a suite with one un-importable 200-test module and one 23-test
   `AssertionError` ranks the import error **first**, and renders its weight as an estimate.
2. `ERROR at setup of …` is tagged `[setup]`, not `[collect]`.
3. A **call-phase failure never mutates the graph**, regardless of message text (§4.3).
4. G2 renders a failure-anchored block for ingestr: `binary:pg_config` marked as the root under
   both `psycopg2` and `asyncpg`, with correct states.
5. `pkg→pkg` edges are traversed but never enumerated: the one-line closure summary appears, and
   a transitive pip dep is expanded only when MISSING.
6. A failure with no graph node renders the **no-explanation** flag, not a fabricated cause.
7. A patch dropping an installed package produces a `SATISFIED → MISSING` line in
   `SINCE YOUR LAST EDIT` next turn.
8. **Mechanism 1:** a failing `pip install psycopg2` whose output names `pg_config` adds
   `binary:pg_config` anchored to `pkg:psycopg2` — **not** to the Test node.
9. **Mechanism 3 gate:** a discovery whose name does not resolve adds **no** subtree; the node is
   marked `unresolved`.
10. `seed_build_deps` / `ldd_probe` refactor is behavior-preserving: full construction suite green,
    graphs byte-identical before/after on the eval corpus.
11. Ablation invariant held: G0 seed script byte-identical to G2 seed script; only `graph_context`
    (and, under `REACT_GRAPH_UPDATE`, the post-seed graph) differs.

Links: [[react-script-repair-arm-design]] · [[graph-build-script-renderer-plan]] ·
[[diagnosis-first-loop-adoption-direction]] · [[essr-denominator-is-agent-chosen]] ·
[[phase2-identity-fallback-deletion-landed]] · [[per-package-install-no-batch]] ·
[[collection-manifest-builder-design]] · [[react-message-list-prompt-style-landed]]
