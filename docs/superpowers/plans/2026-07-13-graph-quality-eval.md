# Graph Quality Eval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure — on real data, mostly offline — whether the graph (1) **enriches** correctly from past run errors, (2) **patches** the agent's prompt with the right root, and (3) **blocks** what the rest of the system considers blocked.

**Architecture:** A new `src/eval/graph_quality/` package following the established `src/eval/<name>/__main__.py` convention. Three independent graders, each usable alone. The enrich grader replays 460 raw stderr blobs from `outputs/repo2run_benchmark/` (already on disk, git-tracked) against labels derived from the Dockerfile the agent actually wrote next. The patch grader reuses `src/eval/graph_repair_ablation/`'s existing injection oracle but grades the *render* deterministically instead of grading an LLM. The block grader needs real graphs, which require Docker to build — so one caching pass mints them and every check afterwards is offline.

**Tech Stack:** Python 3.12+, pytest, the existing `python_deps.depgraph`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-13-graph-quality-eval-design.md` (Rev 1).

## Global Constraints

- **`python` is NOT on PATH. Use `python3`.** Run everything from the repo root.
- **Offline by default.** `enrich_replay` and `block_parity` (post-cache) must not touch Docker, the network, or an LLM. Only the one-off graph-cache mint may use Docker.
- **Never edit the code under test.** This is the repo's standing eval convention: graders import `enrich`, `render_graph_context`, `verdict` and observe them. If a grader needs a behaviour change to pass, that is a FINDING, not a licence to edit `src/python_deps/`.
- 🔴 **`observation_summary` is NOT raw stderr.** It is repo2run's own `[SYSTEM] …` wrapper prose. Reading it produces a number that measures *their* message templates. The only real build output is `dockerfile_validation_attempts[i].docker_build.stderr` and the `.md` prompts. Any task that reads `observation_summary` for error text is wrong.
- 🔴 **Report per-slice, never a bare aggregate.** A 90% average would have concealed the `pg_config` case at 0%, which is the case the whole arm exists for.
- **Immutability.** `DepGraph`/`Node`/`Edge` are frozen. Use `with_node`/`with_edge`/`replace`.

---

## File Structure

| file | responsibility |
|---|---|
| `src/python_deps/depgraph/schema.py` *(modify)* | **T1** — `DepGraph.from_dict` / `Node.from_dict` / `Edge.from_dict`. `to_dict` exists; **nothing reads it back**. Without this the graph cache cannot exist. |
| `src/eval/graph_quality/corpus.py` *(new)* | **T2** — parse `outputs/repo2run_benchmark/` into `(stderr, before_dockerfile, after_dockerfile)` triples; derive + classify the label |
| `src/eval/graph_quality/enrich_replay.py` *(new)* | **T3** — replay each stderr through the real `enrich()`; score |
| `src/eval/graph_quality/patch_localize.py` *(new)* | **T4** — inject a fault, render, grade the ★ set against `correct_action` |
| `src/eval/graph_quality/graph_cache.py` *(new)* | **T5** — mint (Docker, once) + load (offline) the real-graph corpus |
| `src/eval/graph_quality/block_parity.py` *(new)* | **T6** — emit-parity sweep + metamorphic properties + reference oracle |
| `src/eval/graph_quality/__main__.py` *(new)* | **T7** — CLI + `outputs/graph_quality/{results.json,report.md}` |

**Dependency order:** T1 → T5 → T6. T2 → T3. T4 is independent. T7 last.

---

### Task 1: `DepGraph.from_dict` — make the graph corpus possible at all

**Why:** `DepGraph.to_dict()` exists (`schema.py:368`) and **nothing in the repo reads it back** (verified: the only `from_dict` anywhere is `evidence_log.py:30`). The block grader's whole design rests on minting real graphs once under Docker and then checking them offline forever. Without a deserializer there is no cache, and the block eval would need Docker on every run — which it will not get.

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (add `from_dict` beside each existing `to_dict`)
- Test: `tests/depgraph/test_schema_roundtrip.py` *(new)*

**Interfaces:**
- Consumes: the existing `to_dict` on `Node`, `Edge`, `DepGraph`.
- Produces: `Node.from_dict(d) -> Node`, `Edge.from_dict(d) -> Edge`, `DepGraph.from_dict(d) -> DepGraph`.

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_schema_roundtrip.py`. The test that matters is **round-trip identity on a
graph that uses every field we care about** — enums, the frozen `data` mapping, `attempts`, `marker`,
`build_from_source` (whose `False` vs `None` distinction is load-bearing for `blocks()`).

```python
"""to_dict -> from_dict must be lossless. The graph cache depends on it."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)


def _rich_graph() -> DepGraph:
    return (DepGraph()
            .with_node(Node(id="pkg:psycopg2==2.9.12", type=NodeType.PACKAGE, name="psycopg2",
                            layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                            version="2.9.12", state=State.MISSING,
                            build_from_source=True))
            .with_node(Node(id="pkg:Pillow==10.3", type=NodeType.PACKAGE, name="Pillow",
                            layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                            version="10.3", state=State.SATISFIED,
                            build_from_source=False))       # False, NOT None -- blocks() reads this
            .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                            layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RUNTIME,
                            state=State.MISSING, check_command="command -v pg_config",
                            chosen_fix="apt:libpq-dev",
                            fix_candidates=("apt:libpq-dev", "apt:postgresql-server-dev-all"),
                            evidence='Error: pg_config executable not found.',
                            provenance="runtime ingest",
                            data={"runtime_confidence": "runtime-deterministic"}))
            .with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="binary:pg_config",
                            relation=EdgeType.REQUIRES, origin="resolver",
                            marker='python_version >= "3.9"', data={"hard": True})))


def test_roundtrip_is_lossless():
    g = _rich_graph()
    back = DepGraph.from_dict(g.to_dict())
    assert back.to_dict() == g.to_dict()


def test_roundtrip_preserves_ENUMS_not_their_string_values():
    back = DepGraph.from_dict(_rich_graph().to_dict())
    n = back.get("binary:pg_config")
    assert n.type is NodeType.TOOL          # `is`, not `==` -- a str would pass ==
    assert n.state is State.MISSING
    assert n.discovered_by is DiscoveredBy.RUNTIME
    e = back.edges[0]
    assert e.relation is EdgeType.REQUIRES


def test_roundtrip_preserves_build_from_source_FALSE_distinctly_from_NONE():
    """`False` (a known wheel) and `None` (build mode unknown) mean different things to
    `blocks()`: a missing build TOOL blocks the second and not the first. A deserializer that
    collapses them silently flips every wheel's verdict in the cached corpus."""
    back = DepGraph.from_dict(_rich_graph().to_dict())
    assert back.get("pkg:Pillow==10.3").build_from_source is False
    assert back.get("pkg:psycopg2==2.9.12").build_from_source is True
    assert back.get("binary:pg_config").build_from_source is None


def test_roundtrip_preserves_edge_marker_and_data():
    back = DepGraph.from_dict(_rich_graph().to_dict())
    e = back.edges[0]
    assert e.marker == 'python_version >= "3.9"'
    assert e.data.get("hard") is True


def test_roundtrip_survives_JSON():
    import json
    g = _rich_graph()
    back = DepGraph.from_dict(json.loads(json.dumps(g.to_dict())))
    assert back.to_dict() == g.to_dict()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_schema_roundtrip.py -v`
Expected: FAIL — `AttributeError: type object 'DepGraph' has no attribute 'from_dict'`.

- [ ] **Step 3: Implement**

Read each existing `to_dict` **first** and mirror it exactly — do not guess the key names. Add a
`from_dict` classmethod beside each. Enum fields must be reconstructed via the enum
(`NodeType(d["type"])`), and any tuple field (`fix_candidates`, `attempts`) rebuilt as a tuple.
`data` must come back as whatever `__post_init__` expects (it wraps in `MappingProxyType`).

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_schema_roundtrip.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Guard against silent field drift**

A new field added to `Node` later would be silently dropped by `from_dict` and nobody would notice.
Add:

```python
def test_from_dict_covers_every_serialized_field():
    """If someone adds a field to Node.to_dict and forgets from_dict, the cache silently loses it
    and the block grader quietly measures the wrong graph. Fail loudly instead."""
    g = _rich_graph()
    d = g.to_dict()
    back = DepGraph.from_dict(d).to_dict()
    for node in d["nodes"]:
        assert node in back["nodes"], f"field dropped in round-trip: {node}"
```

- [ ] **Step 6: Run the depgraph suite + commit**

Run: `python3 -m pytest tests/depgraph/ -q` (baseline: green)

```bash
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema_roundtrip.py
git commit -m "feat(depgraph): DepGraph.from_dict — make the graph serializable both ways

to_dict has existed since the beginning and NOTHING ever read it back. The graph-quality
eval mints real graphs once under Docker and checks them offline forever after; without a
deserializer that cache cannot exist. Round-trip is asserted lossless, including
build_from_source False-vs-None, which blocks() reads to tell a wheel from a source build."
```

---

### Task 2: `corpus.py` — turn 420 run records into labelled (error → fix) pairs

**Why:** This is the whole supervised signal, and it is easy to get wrong in a way that produces a
confident, meaningless number. Two traps, both verified on disk (spec §2.1, §2.2).

**Files:**
- Create: `src/eval/graph_quality/corpus.py`, `src/eval/graph_quality/__init__.py`
- Test: `tests/eval/graph_quality/test_corpus.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Pair(repo: str, round_index: int, stderr: str, before: str, after: str, label: Label)`
  - `@dataclass(frozen=True) Label(apt: frozenset[str], pip: frozenset[str], kind: str)` where `kind ∈ {"os-package", "python-package", "not-an-env-fix"}`
  - `load_pairs(results_dir: str, artifacts_dir: str) -> list[Pair]`
  - `dockerfile_installs(text: str) -> tuple[frozenset[str], frozenset[str]]` — `(apt_names, pip_names)`

**Verified corpus facts (do not re-derive, but DO spot-check):**
- `results/<repo>.json` → `dockerfile_validation_attempts[i].docker_build.stderr` is the raw failure (460 blobs, 349 repos).
- `dockerfile_repair_rounds[i]` (`round == i+1`) is the Dockerfile written to fix attempt `i` (111 rounds).
- `before(round i) = repair_rounds[i-1].dockerfile_text`; `before(round 0)` = the `"dockerfile"` key of the LLM-input JSON embedded in `eval_artifacts/<repo>/dockerfile_repair_round_1.md`.

- [ ] **Step 1: Write the failing test**

Build the fixtures **by hand** here — this is the one place hand-built fixtures are right, because we
are testing the parser, not the system.

```python
def test_dockerfile_installs_extracts_apt_and_pip_names():
    text = (
        "FROM python:3.10\n"
        "RUN apt-get update && apt-get install -y libpq-dev pkg-config\n"
        "RUN pip install psycopg2==2.9.12 asyncpg\n"
    )
    apt, pip = dockerfile_installs(text)
    assert apt == {"libpq-dev", "pkg-config"}
    assert pip == {"psycopg2", "asyncpg"}


def test_dockerfile_installs_ignores_apt_get_update_and_flags():
    apt, _ = dockerfile_installs("RUN apt-get update && apt-get install -y --no-install-recommends git\n")
    assert apt == {"git"}          # not {"update", "-y", "--no-install-recommends"}


def test_dockerfile_installs_survives_the_real_corpus_retry_wrapper():
    # The corpus wraps every install in a JAYINT_PIP_ATTEMPT retry loop with the real command
    # buried inside `/bin/sh -lc '...'`. A naive line parser finds NOTHING here, and the eval
    # would score 0% pre-emption while reporting itself healthy.
    text = (
        "RUN JAYINT_PIP_ATTEMPT=1; while [ ... ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc "
        "'pip install torch==2.7.0 einops omegaconf' && JAYINT_PIP_STATUS=0 && break; done\n"
    )
    _apt, pip = dockerfile_installs(text)
    assert pip == {"torch", "einops", "omegaconf"}


def test_label_of_a_source_patch_is_NOT_AN_ENV_FIX():
    # Much of this corpus is heavy-ML repos whose fixes are `write a conftest.py that mocks the
    # triton driver` or `git clone mamba && pip install .`. Those are source patches, not
    # environment facts, and the graph is RIGHT to model none of them. Counting them as misses
    # would punish it for correctly declining to hallucinate.
    before = "FROM python:3.10\nRUN pip install torch\n"
    after = before + "RUN printf '%s' 'BASE64==' | base64 -d > tests/conftest.py\n"
    assert label_for(before, after).kind == "not-an-env-fix"


def test_label_of_an_added_apt_package_is_OS_PACKAGE():
    before = "FROM python:3.10\nRUN pip install psycopg2\n"
    after = "FROM python:3.10\nRUN apt-get install -y libpq-dev\nRUN pip install psycopg2\n"
    lab = label_for(before, after)
    assert lab.kind == "os-package"
    assert lab.apt == {"libpq-dev"}


def test_label_ignores_packages_that_were_ALREADY_there():
    # Only ADDITIONS are the label. A package present before and after did not fix anything.
    before = "RUN pip install torch\n"
    after = "RUN pip install torch\nRUN pip install einops\n"
    assert label_for(before, after).pip == {"einops"}
```

- [ ] **Step 2: Run to verify it fails, then implement `corpus.py`**

Run: `python3 -m pytest tests/eval/graph_quality/test_corpus.py -v` → FAIL (module missing).

Implement. The retry-wrapper case is the one that matters: extract the payload of
`/bin/sh -lc '<cmd>'` before parsing, and parse `pip install` / `apt-get install` out of *that*.

- [ ] **Step 3: Smoke the parser against the REAL corpus and report**

Do not trust the unit tests alone — run it over all 420 records and print what you got:

```bash
python3 -m src.eval.graph_quality.corpus --smoke
```
It must print: total pairs, and the breakdown by label kind. **Expected roughly 111 pairs.** If
`not-an-env-fix` is the large majority, that is the spec's §2.2 finding and is a RESULT, not a bug —
report the number.

- [ ] **Step 4: Commit**

```bash
git add src/eval/graph_quality/ tests/eval/graph_quality/test_corpus.py
git commit -m "feat(eval): graph_quality corpus — labelled (error -> fix) pairs from past runs

The label is the agent's OWN repair: diff the Dockerfile before/after a failure and the
apt/pip packages it ADDED are, by construction, what that failure actually required. Free,
real, supervised signal.

Two traps this parser is built around, both verified on disk:
  * observation_summary is NOT raw stderr -- it is repo2run's own [SYSTEM] wrapper prose.
    Only docker_build.stderr carries real build output.
  * every install is buried in a JAYINT_PIP_ATTEMPT retry loop inside /bin/sh -lc '...'.
    A naive line parser finds nothing and the eval scores 0% while reporting itself healthy."
```

---

### Task 3: `enrich_replay.py` — the headline metric

**Why:** This answers "how well does the graph learn from past run errors" with a number that is
falsifiable. The headline is **pre-emption rate**: of the in-scope pairs, in how many does `enrich`
name — from the error text alone — a node whose fix matches what the repair actually added. Every
react turn is a full container rebuild, so a pre-empted discovery hop is a rebuild saved. Near zero
means the enrichment tier does not pay for itself.

**Files:**
- Create: `src/eval/graph_quality/enrich_replay.py`
- Test: `tests/eval/graph_quality/test_enrich_replay.py`

**Interfaces:**
- Consumes: `corpus.Pair` (T2); the REAL `python_deps.depgraph.graph_enrich.enrich`.
- Produces: `score_pair(pair) -> PairScore`, `aggregate(scores) -> dict` (sliced by label kind).
- `PairScore(repo, kind, discovered: frozenset[str], preempted: bool, hallucinated: frozenset[str], owner_anchored: bool)`

- [ ] **Step 1: Write the failing test**

```python
def test_a_pg_config_failure_PREEMPTS_the_libpq_dev_repair():
    """THE case. The error names pg_config; the repair the agent actually wrote added
    `apt-get install -y libpq-dev`. enrich must connect the two WITHOUT being told the answer.

    Before Task 1B of the arm plan, `Error: pg_config executable not found` classified as
    AMBIGUOUS and this scored ZERO while every unit test in the repo passed."""
    pair = Pair(repo="x", round_index=0,
                stderr="#10 12.3 Error: pg_config executable not found.\n",
                before="FROM python:3.10\nRUN pip install psycopg2==2.9.12\n",
                after="FROM python:3.10\nRUN apt-get install -y libpq-dev\n"
                      "RUN pip install psycopg2==2.9.12\n",
                label=Label(apt=frozenset({"libpq-dev"}), pip=frozenset(), kind="os-package"))
    s = score_pair(pair)
    assert s.preempted is True
    assert "binary:pg_config" in s.discovered


def test_a_missing_python_module_PREEMPTS_the_pip_repair():
    pair = Pair(repo="x", round_index=0,
                stderr="ModuleNotFoundError: No module named 'yaml'\n",
                before="FROM python:3.10\n",
                after="FROM python:3.10\nRUN pip install PyYAML\n",
                label=Label(apt=frozenset(), pip=frozenset({"PyYAML"}), kind="python-package"))
    assert score_pair(pair).preempted is True


def test_the_NEGATIVE_CONTROL_produces_no_nodes_at_all():
    """A test-body assertion failure has NO environment fix. The phase gate is supposed to make
    a node here structurally impossible. Any node is a false positive."""
    pair = Pair(repo="x", round_index=0,
                stderr="E   AssertionError: assert 3 == 4\n",
                before="FROM python:3.10\n", after="FROM python:3.10\n",
                label=Label(frozenset(), frozenset(), "not-an-env-fix"))
    s = score_pair(pair)
    assert s.discovered == frozenset()


def test_replaying_the_same_error_twice_is_idempotent():
    pair = ...  # the pg_config pair above
    once = score_pair(pair)
    twice = score_pair(pair)
    assert once.discovered == twice.discovered


def test_aggregate_reports_PER_SLICE_and_refuses_a_bare_aggregate():
    # A 90% average would have concealed pg_config at 0% -- the case the arm exists for.
    out = aggregate([...])
    assert set(out["by_kind"]) >= {"os-package", "python-package"}
    assert "preemption_rate" in out["by_kind"]["os-package"]
```

- [ ] **Step 2: Run to verify it fails, then implement**

`score_pair` must:
1. seed a `DepGraph` from `before`'s declared pip packages (so `owner_node_for_command` has an owner to find);
2. call the REAL `enrich(graph, result, causes=[], ctx=RepoContext())` with `result` shaped as a build failure carrying `pair.stderr`;
3. `preempted` = any discovered node whose `chosen_fix` (after `expand_discovery` resolves it) strips to a name in `label.apt`, OR any discovered Package node whose canonical name is in `label.pip`;
4. `hallucinated` = discovered nodes matching nothing in the repo's final `Dockerfile.eval`;
5. `owner_anchored` = the discovery's edge source is a `pkg:` node, not `TEST_NODE_ID`.

- [ ] **Step 3: Run it over the REAL corpus and report the numbers**

```bash
python3 -m src.eval.graph_quality --enrich
```
Report, per slice: pre-emption rate, attribution coverage, hallucination rate, owner-anchoring rate,
and the `not-an-env-fix` count. **Paste the real table into the commit message.** A bad number here
is a finding, not a failure — do not tune the graph to make it look better.

- [ ] **Step 4: Commit** (message must contain the real measured table)

---

### Task 4: `patch_localize.py` — is the ★ on the right node?

**Why:** The render is the product. A graph that knows the answer but buries it among twelve ★s is a
failed product, and `root-hit@1` alone would score it 100% — which is why **star precision is
mandatory alongside it**.

**Files:**
- Create: `src/eval/graph_quality/patch_localize.py`
- Test: `tests/eval/graph_quality/test_patch_localize.py`

**Reuse — do not rebuild:** `src/eval/graph_repair_ablation/oracle.py` already has the labels:
`Injection(injection_id, repo, base_image, failure_class, mutation, correct_action)` and
`FAILURE_CLASSES = {SYSLIB_MISSING, COMPILER_ABSENT, VERSION_CONFLICT, OVERINCLUDE, TOOL_ABSENT}`,
with `correct_action = {"kind": "install"|"drop", "target": "apt:libX-dev"}`. `inject.apply_injection(script, inj)` is the pure text mutator.
🔴 **There are only 5 `Injection` entries — one per class.** Say so in the report. Five cells is a
smoke test, not a measurement; the plan does not pretend otherwise.

**Interfaces:**
- Produces: `stars(graph, result, causes) -> frozenset[str]` (parse the ★ ids back out of the rendered block), `grade(inj, graph, result) -> LocalizeScore(root_hit, star_precision, n_stars, mislocalized)`.

- [ ] **Step 1: Write the failing test**

```python
def test_star_precision_punishes_a_graph_that_stars_everything():
    """root-hit@1 alone is a DISHONEST metric: a graph that stars 12 nodes and happens to include
    the right one scores 100%. Precision is what makes the metric mean something."""
    score = grade_stars(stars=frozenset({"binary:pg_config", *(f"pkg:noise{i}" for i in range(11))}),
                        target="apt:libpq-dev", graph=...)
    assert score.root_hit is True
    assert score.star_precision < 0.1        # and THAT is the number we report


def test_a_conflicted_root_is_MISLOCALIZED_if_starred():
    # emit._is_emittable already refuses to emit a CONFLICTS_WITH node: no install works. A ★ on
    # it tells the agent to `pip install X` forever.
    ...
    assert score.mislocalized is True


def test_the_COLLAPSE_shows_exactly_one_star_when_two_failures_share_a_root():
    # The arm's headline structural claim, and nothing else measures it: psycopg2 AND asyncpg both
    # need pg_config, so the render must converge on ONE ★ record, not two.
    ...
    assert score.n_stars == 1
```

- [ ] **Step 2–4:** implement, run against the 5 injections, report per failure class, commit with the real table.

---

### Task 5: `graph_cache.py` — mint real graphs ONCE under Docker

**Why:** 🔴 `build_graph_construction_only` (`src/eval/language_package_eval/coverage.py:545`) opens a
`DockerExecutor`. **Real graphs cannot be built offline.** A block eval that assumed otherwise would
simply not run. So one pass mints them and commits the JSON; every check afterwards is offline
forever. This mirrors `package_installability`'s committed `answer_keys.json`.

**Corpus:** the **16 repos already cloned** under `outputs/build_script_eval/_smoke/` — and they are
the right ones (`psycopg2`, `pygraphviz`, `lxml`, `pillow`, `cryptography`). Do not clone 349 repos.

**Files:**
- Create: `src/eval/graph_quality/graph_cache.py`; output `src/eval/graph_quality/graphs/<repo>.json`
- Test: `tests/eval/graph_quality/test_graph_cache.py` (offline: asserts `load_graphs()` round-trips committed fixtures; the mint path is marked `@pytest.mark.docker`)

**Interfaces:** `mint(smoke_root, out_dir)` *(Docker)*; `load_graphs(dir) -> dict[str, DepGraph]` *(offline, uses `DepGraph.from_dict` from T1)*.

- [ ] **Steps:** write the offline loader test first (commit 2–3 small real graph fixtures by hand-running the mint), implement, mark the mint `@pytest.mark.docker`, commit the minted JSON.

---

### Task 6: `block_parity.py` — differential + metamorphic + reference oracle

**Why:** `blocks()`/`verdict()` must mean what `emit` means. A divergence sends the agent to fix
something the renderer would have installed anyway — a wasted container rebuild. This is the scaled
version of the hand-built parity tests that caught the known-wheel/missing-tool bug (`0d3542c`).

**Files:**
- Create: `src/eval/graph_quality/block_parity.py`
- Test: `tests/eval/graph_quality/test_block_parity.py`

**Three independent checks, all offline:**

1. **Emit parity.** For every node of every cached graph: `verdict()` must agree with
   `emit._is_emittable` / `_toolchain_ready` wherever they overlap. Report every divergence with the
   node, both verdicts, and the rule that differs. **Zero divergences is the pass bar.**
2. **Metamorphic properties.** Mutate a real graph, assert the verdict moves correctly:
   mark the true root SATISFIED → ★ moves *up*; add a conflict → ✖ not ★; set `build_from_source=False`
   → a missing Tool below stops blocking; set state UNKNOWN → never ★; soften an edge → child loses ★.
3. **Reference oracle.** A deliberately dumb, slow `verdict_ref()` written straight from the spec's
   prose, cross-checked against the real `verdict()` on every cached graph. Catches the "clever
   implementation quietly diverged from its own definition" class — exactly what happened to `blocks()`.

- [ ] **Steps:** TDD each check, run over the cached corpus, commit with the divergence count (which must be 0).

---

### Task 7: `__main__.py` — CLI and report

- [ ] `python3 -m src.eval.graph_quality [--enrich] [--patch] [--block] [--all]` → `outputs/graph_quality/{results.json,report.md}`.
- [ ] The report renders **per-slice tables only**. Assert in a test that a bare aggregate is never emitted without its slices.
- [ ] Commit.

---

## Self-Review

**Spec coverage.** §3 enrich → T2+T3. §4 patch → T4. §5 block → T5+T6. §2's two corpus traps → T2's
tests and Global Constraints. §5.1's Docker constraint → T5. §6 falsification criteria → the metrics
in T3/T4/T6.

**The one thing I could not verify and a future implementer must:** the exact key names inside each
`to_dict` (T1). Read them; do not guess.

**Known bound, stated honestly:** the patch grader has **5 injection cells**. That is a smoke test.
Growing that table is the obvious follow-up, and the report must not present five cells as a rate.
