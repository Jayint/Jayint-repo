# Graph-guided react arm — failure-anchored graph context (design spec)

**Status:** Rev 2 (2026-07-13). Design approved, not yet implemented.
**Arm:** the graph-guided variant of `--arm react` (today a `future flag`; see
[[react-script-repair-arm-design]]). Ablation partner of the script-only react agent.
**Every claim below is grounded in current code (file:line inline).**

**What changed in Rev 2.** Rev 1 shipped a false premise (§3: "there are no `pkg→pkg`
edges" — there are, in bulk) and a renderer that handed the agent a bare root-cause
verdict. Rev 2 fixes the premise, and replaces the verdict with a **failure-anchored graph
patch**: the agent sees the edges and the certified states around each failure, *and* we
mark which node is actionable. It also adds two signals Rev 1 missed — the per-turn state
delta and the no-explanation coverage flag.

---

## 1. Goal

Give the react repair agent a **graph layer** that turns a wall of pytest failures into
local, certified causal structure: for each failure, what the failing thing requires (with
live states), what requires it (blast radius), which node beneath it is actually missing,
and — when the graph has no explanation at all — an explicit signal to stop trusting the
graph and go explore.

pytest gives the failure classes and the test counts. The graph gives the edges, the
certified states on those edges, and the reachability the agent would otherwise have to
recompute by hand across every failure block.

---

## 2. Ablation invariant (unchanged from Rev 1)

The **only** delta between the script-only agent and the graph arm is the `graph_context`
slot on the planner (`planner.py:129,150-152`). The seed script, the loop, the gate, the
history, the tools — byte-identical. Today `entry.py:162` hardcodes `ctx = None`; the arm
populates it. `run_react_arm` already takes the `graph_context: bool` flag (`entry.py:157`).

Two of the three rungs **already ship**, via the orthogonal `_OBS_MODE` lever (`loop.py:46`):

| rung | `_OBS_MODE` | `graph_context` | status |
|---|---|---|---|
| **G0** raw reactive floor | `raw` (pass-count + tail) | `None` | ships |
| **G1** presentation control | `histogram` (ranked causes, no graph) | `None` | ships |
| **G2** graph context | `histogram` | `render_graph_context` | **this spec** |

G1 is the control that matters: the agent **already sees the ranked pytest failure classes
with test counts** in G1. So G1→G2 isolates *what the graph adds on top of the histogram the
agent already has* — topology and certified state — rather than re-testing presentation.

---

## 3. Verified primitives (already in the tree)

| primitive | location | role |
|---|---|---|
| `graph_context` seam | `planner.py:129,150-152`; `entry.py:162` | integration point (currently `None`) |
| `Cause(exc, detail, count, outcome, module)` + `summarize()` | `pytest_summary.py:41,105` | the N failure classes **with test counts** |
| `diagnose(cmd, out, ctx)` → `Mode` + `Discovery` | `diagnose.py:86` | classify a cause into 5 modes |
| `requires_of` / `required_by` | `schema.py:348,359` | the down-walk / the up-walk (blast radius) |
| `certify_all(graph, executor, layer_order)` | `certify.py:118`; wired `entry.py:75-76` | refreshes node **states** against the live container |
| `State` ∈ `{UNKNOWN, MISSING, SATISFIED}` | `schema.py:56-61` | the certified state on every node |
| `node.check_command` | `schema.py` | the *existence* probe (see §6 — **not** a correctness probe) |

### 3.1 The graph is re-certified every turn — states are never stale

`loop.py:190-207` (`build_and_test`): **reset → run the whole script fresh from base →
`g = certify(graph)` → run tests.** The planner is then handed that graph (`loop.py:246`).
`certify` is `certify_all(graph, _ExecAdapter(sandbox.exec_readonly), layer_order=_INSTALL_LAYERS)`
(`entry.py:75-76`).

Two consequences, both load-bearing:

- **State staleness is a non-problem.** The graph the agent sees was certified against the
  container its own script just built, this turn, before it acts.
- **Script drift is captured for free.** If the agent's edit drops `faker` from the install
  list, the rebuilt container won't have it and `certify` marks `pkg:faker` MISSING by
  itself. We never diff the script against the graph — the *container* is the ground truth,
  and certification reads it.

What certification **cannot** do is **add nodes**. It runs `check_command` per *known* node.
A requirement the graph never modeled has no node and no check, and stays invisible no
matter how many times we certify. That is the graph's real blind spot, and §6 makes it an
explicit computed signal rather than a silent trap.

### 3.2 `pkg→pkg` edges exist, in bulk (Rev 1 was wrong)

Rev 1 claimed "there are no `pkg→pkg` edges … the same-type-edge rule is not a filter, it's
a cheap assertion." **False.** `resolve_lock.py:425-451` emits
`Edge(src=<Package>, dst=<Package>, REQUIRES, origin="resolver")` for every entry in each
locked package's `dependencies` list, and `resolve.py:415-437` does the same over the
`uv pip compile` closure. A real repo's pip closure is hundreds of such edges.

The correct rule is neither "don't traverse them" nor "they don't exist":

> **Traverse them; never *render* them individually.**

Emit one summary line per anchor — `+ 37 transitive pip deps: 37 satisfied` — and recurse
only into the MISSING ones. The closure came verbatim from the lockfile and pip re-derives
it at install time; enumerating it buries the handful of edges that carry information.

**Bound the walk by edge *type*, not hop radius.** Four hops down the system tier
(`Test → Import → Package → Tool → AptDep`) is five nodes and pure signal. Two hops through
the pip closure is hundreds of nodes and pure noise. The information-dense edges are the
**cross-tier** ones — `Package → SystemLib` (probe, `ldd_probe.py:211`),
`Package → Tool/AptDep/capability` (resolver, `build_deps.py:319,343,353`) — precisely where
an LLM's prior is weakest and pytest is blind. Render those in full, always.

---

## 4. The two-tier triage (unchanged from Rev 1 — still correct)

Empirically verified (VM, pytest 9.0.3, through the repo's own `summarize()`):

- **`pytest --collect-only` surfaces ALL collection/import errors at once**, in the same
  `___ ERROR collecting X.py ___` blocks a full run emits; `summarize()` parses them
  **byte-identically**.
- **Scope differs, not format.** collect-only shows only import/collection-time failures
  (the environment class); execution failures (`AssertionError`) appear only in a full run.
- **Exit-code landmine.** `--collect-only` alone → **exit 2**; with
  `--continue-on-collection-errors` → **exit 1**. The error blocks are identical either way.
  Gate on **blocks, not on `rc`**. (Same landmine as [[essr-denominator-is-agent-chosen]].)

**Tier 1 — collection triage (cheap, no execution).** This is where the graph pays most, and
it is the *denominator-restoring* repair: an unimportable module is exactly what silently
hides tests, so fixing collection heals the env **and** un-hides tests.
**Tier 2 — execution triage (full run, once collection is clean).** Mostly yields "not an
environment problem," or a `CONFIG`/`SERVICE` hit.

The renderer takes a `phase` so the "this is not an environment problem" verdict is only
asserted after a full run — a `RESIDUAL` under collect-only just means the body never ran.

---

## 5. The renderer — what the agent actually sees

One block per failure class, anchored at the graph node the failure names. Edges in **both**
directions, certified state on every one, the actionable node marked.

```
FAILURE  ModuleNotFoundError: No module named 'psycopg2'   (40 tests)
  ↓ maps to   pkg:psycopg2==2.9.12          MISSING

  what it requires:
    binary:pg_config        MISSING     ← nothing below this is missing. fix here.
        fix    apt-get install -y libpq-dev
        check  command -v pg_config
    binary:pkg-config       check passed: command -v pkg-config
    tool:build-essential    check passed: dpkg -s build-essential
    + 37 transitive pip deps: 37 satisfied

  what requires it:
    import:psycopg2 → 40 tests
    also blocks pkg:asyncpg (12 tests) — same root

FAILURE  AssertionError in test_totals   (23 tests)
  ↓ maps to   — no graph node —
  NOT AN ENVIRONMENT FAILURE (residual). No env fix exists.

FAILURE  ModuleNotFoundError: No module named 'patchright'   (8 tests)
  ↓ maps to   — no graph node —
  THE GRAPH HAS NO EXPLANATION for this failure. The requirement is outside the
  model. Do not look for it below — explore.

SINCE YOUR LAST EDIT
  pkg:lxml   SATISFIED → MISSING   (your patch dropped it)
  3 nodes    MISSING → SATISFIED
```

**Why the `← fix here` marker is not "handing over a verdict."** The edges and states above
it are the reasoning material; the marker only answers a question the agent would otherwise
have to compute by hand across every failure block — *which of these missing nodes has
nothing missing beneath it*. That is a deterministic BFS over states we certified against
the live container this turn. It is correct given the graph, it is twenty unit-testable
lines, and making the model re-derive topological reachability in token space is both
expensive and error-prone. The agent can still audit the chain and disagree; nothing is
hidden from it.

### 5.1 Signature

```python
def render_graph_context(graph, causes, prev_states, ctx, phase) -> str:
    """Pure. causes = summarize(output); prev_states = {node_id: State} from last turn;
    ctx = RepoContext(local_names, invalid_names); phase in {"collect", "run"}."""
```

`ctx` is per-run constant, so `entry.py` binds it by closure and the **planner seam carries
four args** — `graph_context(graph, causes, prev_states, phase)`. Pure function, no I/O: every
state it reads was already certified by `loop.py:196`.

- **Anchor** each `Cause` via `diagnose(cmd, f"{c.exc}: {c.detail}", ctx)` → `Mode` +
  `Discovery`, then match the Discovery to an existing node by normalized name
  (`runtime_ingest.py:86-90` — reuse, do not reimplement). Anchor the **top 3 causes by
  `c.count`**; the rest render as one-line tallies.
- **Down-walk** `requires_of`, expanding only non-SATISFIED nodes, unbounded depth (real
  MISSING chains are short). Around each MISSING node, list its immediate SATISFIED siblings
  as a compressed rule-out ring (name + `check passed: <cmd>`, no fix detail). **Never expand
  a SATISFIED node's children.** Collapse `pkg→pkg` per §3.2.
- **Roots** = MISSING/UNKNOWN nodes with no MISSING/UNKNOWN prerequisite. Mark with
  `← nothing below this is missing. fix here.`
- **Up-walk** `required_by`, one hop, aggregated to counts; list individually only if ≤3.
- **Regime guard.** If MISSING count exceeds ~15 (e.g. the venv never materialized and the
  whole closure flipped MISSING), enumeration explodes and is useless. Collapse hard to roots
  only: *"212 packages MISSING, all downstream of: python venv creation failed."*

### 5.2 The state delta (§5's `SINCE YOUR LAST EDIT`)

Because `certify` runs every turn against the container the agent's own script just built,
the graph is a **regression attributor**: it can tell the agent *when its own last edit broke
something*. The pytest histogram carries this only noisily, through shifting counts.

Feasible in three lines: `build_and_test` (`loop.py:190`) closes over `graph`, and
`loop.py:227,277,291` rebind it — so the previous turn's certified graph is simply the value
of `graph` before the call. Capture `{n.id: n.state for n in graph.nodes}` before, diff after.

---

## 6. Two honesty rules (new in Rev 2)

**`SATISFIED` means the check exited 0 — not that the node is correct.** `command -v pg_config`
is an *existence* probe, not a version/ABI probe. A node can certify SATISFIED and still be
the root cause (wrong libssl, python floor-trap, stale wheel). A confident verdict would
silently *exclude* it — the worst failure mode, because the agent stops looking. So we render
`check passed: command -v pg_config`, never `OK`. The agent can question the check itself,
and `check_command` is an affordance it can extend in an `explore` move.

**The blind spot is a computed flag, not a disclaimer.** The dominant real-world failure is
the unmodeled requirement (§3.1): no node, invisible forever. Every rendering risks teaching
the agent that the answer must be *inside* the graph. So when a failure class anchors to a
node whose entire modeled closure is SATISFIED — or anchors to nothing at all — we say so out
loud: **"the graph has no explanation for this failure; the cause is outside the model —
explore."** That converts incompleteness from a silent trap into a routing signal. Note this
flag is only computable *because* we do the walk.

---

## 7. Build surface

| change | file | size |
|---|---|---|
| `render_graph_context` + `_anchor`/`_down_walk`/`_roots`/`_delta`/`_format` | **new** `depgraph/graph_context.py` | the only real logic |
| widen seam `graph_context(graph)` → `(graph, causes, prev_states, phase)`; thread through `plan()` | `planner.py:129,150-152`, `plan()` sig | ~5 lines |
| hoist `causes` out of `_observation`; capture `prev_states` before `certify`; thread both + `phase` to `plan()` | `loop.py:86,190-207,246` | ~6 lines |
| Tier-1 collect-only pass (`--collect-only --continue-on-collection-errors`) before the full run | `loop.py` (`build_and_test`) | small |
| build `RepoContext` from `repo_path`; wire renderer; env flag `REACT_GRAPH_CONTEXT` | `entry.py:156-164` | ~8 lines |

`repo_path` already reaches `run_react_arm` (`entry.py:156`, used `:150/:163`), so
`RepoContext` is constructible with no new plumbing. Reuse `req_slice.build_requirement_slice`
(`req_slice.py:135`) where it already projects deps+states+`unblocks`+`chain_to_goal` — do not
reimplement those.

---

## 8. Metrics (trace-computable, no pass-rate wait)

- **anchor hit rate** — fraction of failure classes that mapped to an existing node. This
  **upper-bounds everything else**; if it's low, the graph is not being consulted, it's being
  ignored.
- **collapse ratio** `N causes / K roots` — how much structure the graph recovered.
- **first-patch-targets-root** — did the agent act on a marked root, or on a symptom?
- **no-explanation rate** — how often the graph honestly had nothing (§6). A *feature*, not a
  failure: it should correlate with the agent choosing `explore`.
- **delta-attributed regressions** — how often `SINCE YOUR LAST EDIT` caught the agent
  breaking something, and whether it then reverted.

**Honest expectation:** a `#@node` census suggested roughly half of Package nodes are leaves
(`requires=-`), so for them `K == N` and collapse does nothing. The technique buys the
syslib/tool-chain half (ingestr's `pg_config`, etc.) plus the blast-radius and delta signals,
which apply everywhere. **Measure the collapse ratio per repo before promising anything.**
⚠ That census was measured on the *emitted* `#@node` view, which may filter the requires list
— re-derive it against the live graph before quoting it.

---

## 9. Open question (deliberately not resolved here)

**Does the graph's topology stay frozen at construction, or do observations mutate it
mid-loop?** Today: frozen topology, fresh states (§3.1). `ingest_runtime_failures`
(`runtime_ingest.py:165`) and `diverged_node_ids` (`:94`) exist for the mutate path and are
**not wired into `react_repair/`**. Mutating would let a discovered requirement (`pkg:patchright`)
become a real node, enabling later-turn recognition and REGRESSED detection — but it makes the
graph a function of the agent's trajectory, which quietly breaks the clean G0/G1/G2 ablation
(G2's graph would diverge from G0's). Rev 2 ships **frozen topology**; the no-explanation flag
(§6) is the honest degradation. Revisit only if the no-explanation rate is high.

---

## 10. Acceptance

1. G2 renders a failure-anchored block for a known multi-cause repo (ingestr: `pg_config`
   marked as the root under both `psycopg2` and `asyncpg`) with correct test counts and states.
2. `pkg→pkg` edges are traversed but never enumerated: the render shows the one-line closure
   summary, and expands a transitive pip dep only when it is MISSING.
3. Collect-only tier produces the same anchors and roots as the full-run tier for the
   collection subset (verified against the §4 probe).
4. A failure with no graph node renders the **no-explanation** flag, not a fabricated cause.
5. A patch that drops an installed package produces a `SATISFIED → MISSING` line in
   `SINCE YOUR LAST EDIT` on the next turn.
6. Ablation invariant held: G0 seed script byte-identical to G2 seed script; only
   `graph_context` differs.

Links: [[react-script-repair-arm-design]] · [[graph-build-script-renderer-plan]] ·
[[diagnosis-first-loop-adoption-direction]] · [[essr-denominator-is-agent-chosen]] ·
[[react-message-list-prompt-style-landed]]
