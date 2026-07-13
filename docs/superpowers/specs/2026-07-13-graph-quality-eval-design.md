# Graph Quality Eval — Design (Rev 1)

**Goal.** Measure three separable claims the graph-guided react arm makes, on real data, mostly
offline:

1. **enrich** — from a past run's error text, does the graph learn the right thing?
2. **patch** — is the subgraph we show the agent anchored on the *right* root?
3. **block** — does `blocks`/`verdict` mean what the rest of the system means by "blocked"?

These are three different claims with three different notions of correctness, and they need three
different methods. Conflating them is how a feature ends up with one headline number that hides the
only case anybody cares about.

**Status.** Design. The arm itself is BUILT and green (2009 tests, HEAD `d69c5bc`); see
`docs/superpowers/specs/2026-07-11-graph-guided-react-arm-design.md`.

---

## 1. Why this eval exists

The arm shipped with 2009 passing tests and *still* had two defects that a five-minute
`FakeSandbox` demo found immediately — a capability node with no `fix`, and a `fix` field that
rendered an internal provider id the agent could not run. Both survived because **every unit test
hand-builds its own graph**, and a hand-built graph is a statement of what we *believe* the system
produces.

This eval exists to stop asserting and start measuring. Its inputs are real error logs from real
runs; its labels are what the environment actually turned out to need.

**Non-goal:** measuring the *lift* (does the graph make an LLM agent succeed more often). That is
the G1→G2→G3 ablation, it needs Docker and a live model, and it is worth nothing until we know the
graph is right. This spec is about whether the graph is right.

---

## 2. The corpus — what actually exists, verified

Everything below was confirmed on disk, not assumed.

`outputs/repo2run_benchmark/` (git-tracked, 587 MB):

| artifact | count | what it is |
|---|---|---|
| `results/<repo>.json` | 420 repos | the full run record |
| `…dockerfile_validation_attempts[i].docker_build.stderr` | **460 blobs, 349 repos** | 🔴 **raw buildkit stderr** — the real failure text (up to 58 KB) |
| `…dockerfile_repair_rounds[i].dockerfile_text` | **111** | the Dockerfile written to fix attempt `i` — **the label** |
| `eval_artifacts/<repo>/dockerfile_repair_round_N.md` | 203 | the LLM INPUT prompt: holds the **before** Dockerfile *and* the stderr |
| `eval_artifacts/<repo>/Dockerfile.eval` | 377 | the final, working Dockerfile — a weak label for "what this repo needed" |

**The alignment is exact and was verified:** `validation_attempts[i].docker_build.stderr` is the
failure; `repair_rounds[i]` (`round == i+1`) is the Dockerfile written in response. The `before`
Dockerfile for round *i* is `repair_rounds[i-1].dockerfile_text`, and for round 0 it is the
`"dockerfile"` key of the LLM-input JSON inside `dockerfile_repair_round_1.md`.

### 2.1 🔴 Two traps in this corpus. Both would have silently produced a wrong number.

**`run_summary.failed_actions[].observation_summary` is NOT raw stderr.** It is repo2run's own
wrapper prose — `[SYSTEM] COMMAND REJECTED BEFORE EXECUTION…`, `[SYSTEM] Per the No Excuses
Rule…`. Running our classifier on it yields a 73.6% miss rate that measures *their harness's
message templates*, not our classifier. **Only `docker_build.stderr` and the `.md` prompts carry
real build output.**

**The denominator is not "all failures."** Of 4,598 failed actions, most are `cat`, `ls`, and
rejected commands, which *should* classify as AMBIGUOUS. A coverage number over that population is
meaningless. The denominator must be **environment-fixable install failures**, established from
the label (§3.2), never from the command string alone.

### 2.2 🔴 Not every fix is an environment fix

This corpus is heavy on ML repos, and a large share of repair rounds fix things like *"write a
`tests/conftest.py` that mocks the triton driver"* or *"clone mamba and pip install it with
`--no-build-isolation`"*. Those are **source patches and bespoke build recipes, not environment
facts**, and the graph is *right* to model none of them.

So the label must itself be classified before anything is scored. A round whose fix adds no
`apt-get install` / `pip install` of a *named package* is **out of scope** — it lands in a
reported `not-an-env-fix` bucket and is excluded from the enrichment denominator. Silently
counting it as a miss would punish the graph for correctly declining to hallucinate.

**This bucket's size is itself a finding** and must be reported, not hidden: it bounds how much of
real-world environment repair the graph tier can ever address.

---

## 3. Enrich — replay past errors, score against the fix that followed

Pure offline. No Docker, no network, no LLM.

### 3.1 Method

For each of the 460 raw stderr blobs: run the real `enrich()` (the same function the arm calls),
seeded with a graph built from the *before* Dockerfile's declared packages, and record every node
and edge it produces.

### 3.2 Ground truth — the agent's own repair is the label

Diff `before` → `after` Dockerfile. The `apt-get install` / `pip install` **package names added**
are, by construction, what that failure actually required. This is a free, real, supervised signal:
we are not guessing what the error meant, we are reading what fixed it.

Classify each label:
- `apt:<pkg>` added → an **OS-package** fix (the graph should find a capability or syslib node)
- `pip:<pkg>` added → a **Python-package** fix (the graph should find a Package node)
- neither → **not-an-env-fix** (§2.2), excluded from the denominator, counted and reported

### 3.3 Metrics

**Headline — pre-emption rate.** Of the in-scope (error → fix) pairs, in how many does `enrich`
name, *from the error text alone*, a node whose fix matches what the repair actually added?

This is the arm's entire economic claim made falsifiable. Every react turn is a full container
rebuild, so a pre-empted discovery hop is a rebuild saved. A pre-emption rate near zero means the
graph learns nothing the agent could not already read off the error itself, and the feature does
not pay for itself.

**Attribution coverage.** Fraction of in-scope failures that produce *any* discovery. This is the
metric on which the `pg_config` classifier hole scored **0%** while every unit test passed.

**Hallucination rate.** Discovered nodes whose fix appears in *no* working Dockerfile for that
repo. The counterweight to coverage: a classifier that fires on everything scores 100% coverage
and is useless.

**Owner-anchoring rate.** Fraction of discoveries anchored at a `pkg:` owner rather than falling
back to `TEST_NODE_ID`. This is the depth property `owner_node_for_command` exists for; a flat star
has no causal chain to render.

### 3.4 Invariants — violations are bugs, not scores

- **Negative control.** Call-phase assertion failures and non-env failures (`cat: no such file`)
  must yield **zero** nodes. Any node here is a false positive, and the phase gate is supposed to
  make it structurally impossible.
- **Idempotence.** Replaying a blob twice must not duplicate a node or an edge.
- **Reconciliation.** A tool discovery must **annotate** construction's `binary:pg_config`, never
  append a twin. (This is the node fracture fixed in `3fc71e8`/`870eb5c`; the eval is where a
  regression would show up.)

### 3.5 Slicing

Every metric is reported **per failure class** (missing Python package / missing syslib / missing
tool / version conflict / not-an-env-fix). An aggregate is not reported without its slices. A 90%
average would have concealed `pg_config` at 0% — the exact case the whole arm is built around.

---

## 4. Patch — is the ★ we show the agent the right node?

### 4.1 Reuse, do not rebuild

`src/eval/graph_repair_ablation/` **already does the hard half**: a hand-written oracle of injected
faults across five failure classes (`SYSLIB_MISSING`, `COMPILER_ABSENT`, `VERSION_CONFLICT`,
`OVERINCLUDE`, `TOOL_ABSENT`), each carrying a `correct_action` ground truth, plus a pure text
mutator that strips a line from the rendered `setup.sh`. Its `Injection`/`correct_action` tables
are exactly the labels we need.

### 4.2 The change: grade the RENDER, not the agent

Today that harness runs a live LLM in Docker and grades what the *model* did. We instead grade the
graph directly:

> injected fault → the resulting error → `enrich` → `render_graph_context` → extract the ★ set →
> compare to `correct_action.target`.

Deterministic. No LLM, so no model noise in the measurement. **This isolates the graph from the
model**, and that is the point: if the ★ is on the wrong node, no amount of model quality repairs
it, and we learn that for near-zero cost.

### 4.3 Metrics

- **root-hit@1** — is `correct_action.target` in the ★ set?
- **star precision** — `|★ ∩ {true root}| / |★|`. Load-bearing: a graph that stars twelve nodes and
  happens to include the right one scores 100% on root-hit and is nearly worthless in a prompt.
  Root-hit alone is a *dishonest* metric; it must always be reported with precision.
- **collapse rate** — when several failures share one root, exactly **one** ★ record must appear.
  This is the arm's headline structural claim (§6 of the arm spec) and nothing currently measures it.
- **mislocalisation** — a ★ on a node that is not the root. Specifically catches the conflicted-root
  case: a `CONFLICTS_WITH` node must render `✖`, never `★`.

The existing C0/C1 agent ablation stays available for the end-to-end lift, but it costs Docker and
a live model and is **only worth spending once the deterministic grader is green**.

---

## 5. Block — does `blocks`/`verdict` mean the right thing?

No labels needed, no LLM, and — after one caching pass — no Docker.

### 5.1 🔴 The feasibility constraint that shapes this

`build_graph_construction_only` (`src/eval/language_package_eval/coverage.py:545`) opens a
`DockerExecutor`. **Real graphs cannot be built offline.** A plan that assumed "construct graphs for
349 repos and sweep them" would not run.

So: **one Docker pass mints a graph corpus and caches it** (`DepGraph.to_dict()` → committed
JSON), and every check below then runs offline forever against the cache. This mirrors what
`package_installability` already does with its committed `answer_keys.json`.

The corpus is the **16 repos already cloned** under `outputs/build_script_eval/_smoke/` — and they
are the right ones: `psycopg2`, `pygraphviz`, `lxml`, `pillow`, `cryptography`. Plus every graph
produced during the enrich replay (§3), which are free.

### 5.2 Differential parity against `emit`, at scale

`emit._is_emittable` / `_toolchain_ready` is the **incumbent authority** on what can be installed.
For every node of every cached graph, assert `verdict()` agrees with `emit` wherever they overlap.
Any divergence is a bug *by definition*: it means we send the agent to fix something the renderer
would have installed anyway, and every wasted turn is a container rebuild.

This is the scaled version of the hand-built parity tests — and it is exactly the check that caught
the known-wheel/missing-tool bug (`0d3542c`), on a graph I had built by hand. At scale it would have
caught it sooner.

### 5.3 Metamorphic properties — the cheapest bugs per line

No ground truth required. Mutate a real graph and assert the verdict moves correctly:

| mutation | required consequence |
|---|---|
| mark the true root SATISFIED | the ★ moves **up** to its parent (or vanishes) |
| add a `CONFLICTS_WITH` edge to a ★ node | it becomes **✖**, never ★ |
| set `build_from_source=False` (a known wheel) | a missing **Tool** below it stops blocking |
| set a node's state to UNKNOWN | it is **never** ★ (spec §6.4: "UNKNOWN never masquerades as MISSING") |
| soften an edge (`data["hard"]=False`) | its child loses its ★ |

These encode the spec's rules directly, need no corpus, and each one maps to a bug we have actually
shipped.

### 5.4 A brute-force reference oracle

Write a deliberately dumb, obviously-correct, slow `verdict_ref()` straight from the spec's prose,
and cross-check it against the real `verdict()` on every cached graph. This catches the "clever
implementation quietly diverged from its own definition" class — which is precisely what happened
to `blocks()` when it could not express the wheel-vs-sdist rule.

---

## 6. What would falsify the feature

Stated up front so the eval can actually come back negative:

- **pre-emption rate ≈ 0** → the graph learns nothing from errors that the agent could not already
  read off the error text. The enrichment tier does not pay for its complexity.
- **star precision low while root-hit is high** → the graph knows the answer but buries it in noise.
  The render is the product; a noisy render is a failed product.
- **`verdict` diverges from `emit` on real graphs** → the arm's model of "blocked" is not the
  system's model of "blocked", and the agent is being misdirected.
- **not-an-env-fix bucket dominates** → most real environment repair is source patches and bespoke
  recipes, and the graph tier addresses a small slice of the problem. This would be a genuinely
  important negative result and must not be buried.

---

## 7. Deliverables

- `src/eval/graph_quality/` — three graders (`enrich_replay`, `patch_localize`, `block_parity`),
  one CLI, following the established `src/eval/<name>/__main__.py` convention.
- `outputs/graph_quality/{results.json,report.md}` — per-slice tables, never a bare aggregate.
- A committed graph-corpus cache so the block grader stays Docker-free after the first run.
