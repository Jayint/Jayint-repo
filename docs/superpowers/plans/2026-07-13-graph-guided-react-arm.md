# Graph-Guided React Arm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `--arm react` an optional graph layer that renders a certified, failure-anchored subgraph (with a fix-here marker) into the planner prompt, and lets the graph grow from runtime observations.

**Architecture:** Three new pure modules under `src/python_deps/depgraph/` — `graph_context.py` (edge semantics + render), `graph_enrich.py` (owner resolution + observation ingest), `discovery_expand.py` (resolve a discovery's system-tier prerequisites). The react loop already re-certifies the graph against the live container every turn (`loop.py:196`), so all three read *certified* state. The existing `graph_context` seam on the planner (`planner.py:129`, currently hardcoded `None` at `entry.py:162`) is the only integration point, which keeps the G0/G1/G2/G3 ablation clean.

**Tech Stack:** Python 3.12+, pytest, existing `python_deps.depgraph` (frozen dataclasses, immutable `DepGraph.with_node`/`with_edge`).

**Spec:** `docs/superpowers/specs/2026-07-11-graph-guided-react-arm-design.md` (Rev 3.3, commit `d03a64a`).

## Global Constraints

- **Immutability.** `DepGraph`, `Node`, `Edge` are frozen dataclasses. Never mutate — use `graph.with_node(...)` / `graph.with_edge(...)` / `dataclasses.replace(...)`, which return new objects.
- **Purity.** `graph_context.py` performs **no I/O** — no Docker, no network, no subprocess. `graph_enrich.py` is pure except for the narrow `certify_only` executor call. Only `discovery_expand.py` may hit the network/container.
- **No new deps.** Everything needed is already imported somewhere in `src/python_deps/`.
- **Test import boilerplate.** `tests/depgraph/conftest.py:17-20` **already** puts `src/` on `sys.path`, so the per-file shim in `tests/depgraph/*.py` is redundant (harmless — the `if not in sys.path` guard is idempotent — and every existing file has it, so keep it for consistency). Tests under `tests/react_repair/` need the **repo root too** (they import `from src.react_repair...`) — use the two-parent form from `tests/react_repair/test_pytest_summary.py:1-6`. Copy the form from the neighbouring file exactly.
- **Run tests with:** `python -m pytest <path> -v` from the repo root. There is no `pytest.ini`/`pyproject` pytest config; `sys.path` is assembled by `tests/conftest.py` plus per-package conftests. Verified: `python -m pytest tests/depgraph/ -q` collects 1325 tests.
- 🔴 **Line numbers in this plan were re-anchored 2026-07-13, but `loop.py`/`entry.py`/`planner.py` are under active parallel development.** `grep` for the symbol before editing; never trust a line number blindly.
- **`Cause.count` semantics are FIXED and deliberate.** `format_breakdown`'s docstring states: *"a `[collect]` row's `count` is MODULES affected, NOT tests affected … Recovering 'blocks N tests' needs the hidden gold set (final-only) or the graph arm; **do not attempt it here**."* Task 1 therefore does **not** change `count`. The tests-hidden estimate lives in the **graph arm** (Task 4), exactly as that docstring directs.
- **Do NOT call `_phase_a_fixpoint` (`build.py:336`)** from any new code. It is the full resolve→install→probe→repair fixpoint with network and container installs; it would blow the per-turn budget.
- **Append-only.** Enrichment never deletes a node or an edge.

---

## File Structure

| file | responsibility |
|---|---|
| `src/react_repair/pytest_summary.py` *(modify)* | **T1** — add `Cause.phase` (`collect`/`setup`/`call`/`teardown`), parsed from the block banner |
| `src/python_deps/failure_classifier.py` *(modify)* | **T1B** 🔴 — recognise `<tool> executable not found`. Without this the arm's headline case (`pg_config`) classifies as AMBIGUOUS and enrich appends nothing |
| `src/python_deps/depgraph/graph_enrich.py` *(new)* | **T2, T6** — `owner_node_for_command`, `enrich`, `certify_only` |
| `src/python_deps/depgraph/graph_context.py` *(new)* | **T3, T4, T5** — edge semantics (`blocks`, `in_conflict`, `verdict`), `tests_hidden`, and the render |
| `src/python_deps/depgraph/build_deps.py` *(modify)* | **T7** — extract per-node `seed_build_deps_for` out of `seed_build_deps`'s loop (**counters preserved**) |
| `src/python_deps/depgraph/discovery_expand.py` *(new)* | **T7** — `expand_discovery` |
| `src/react_repair/planner.py` *(modify)* | **T8** — widen the `graph_context` seam to 4 args, at **both** call sites |
| `src/react_repair/loop.py` *(modify)* | **T8** — capture `prev_states`, hoist `causes`, call enrich/expand/certify_only |
| `src/react_repair/entry.py` *(modify)* | **T8** — build the context fn; `REACT_GRAPH_CONTEXT` (G2) / `REACT_GRAPH_UPDATE` (G3) |
| `tests/react_repair/test_planner.py` *(modify)* | **T8** 🔴 — its 1-arg `graph_context` lambda (`:93`) `TypeError`s once the seam widens |

**NOT in this plan:** `ldd_probe.py` / `ldd_probe_for` (§7.2 Mechanism 2). It needs a container with
the package actually installed, so it cannot be unit-tested with a fake executor — and its loop is
two-level (one *batched* `ldd` per package fanned out over several sonames), so it is not the same
shape as `seed_build_deps`'s. Follow-up VM slice. See Self-Review Notes.

**Dependency order:** T1, T1B, T3, T4 are independent leaves. **T2 → T6** (owner), **T1B → T6**
(without it enrich appends nothing for the flagship case). T5 consumes T1, T3, T4. T7 consumes T6.
T8 consumes T5, T6, T7.

---

### Task 1: `Cause.phase` — parse the pytest phase from the block banner

**Why:** `pytest_summary.py:118` buckets on `title.startswith("ERROR")`, which lumps `ERROR collecting <file>` (a **collection** error — per-file, the tests never existed) together with `ERROR at setup of <test>` (a **run-phase** fixture error — per-test, the test *was* collected). `format_breakdown` then tags a setup error `[collect]`, which is wrong, and downstream (Task 5) has no way to apply the phase→graph-update gate.

**Files:**
- Modify: `src/react_repair/pytest_summary.py:41-47` (the `Cause` dataclass), `:105-124` (`summarize`), `:127-152` (`format_breakdown`)
- Test: `tests/react_repair/test_pytest_summary.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Cause(exc: str, detail: str, count: int, outcome: str, module: str, phase: str)` where `phase` is one of `"collect" | "setup" | "call" | "teardown"`. `outcome` keeps its existing `"ERROR" | "FAILED"` values and meaning (backwards compatible).

- [ ] **Step 1: Write the failing test**

Append to `tests/react_repair/test_pytest_summary.py`:

```python
# A REAL pytest run with BOTH kinds of "ERROR": a collection error (per-FILE, the tests
# inside never became items) and a setup error (per-TEST, the test WAS collected but its
# fixture blew up). Both banners start with "ERROR", which is why the old
# `title.startswith("ERROR")` bucketing conflated them.
_PHASES = """\
==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_missing.py ____________________
tests/test_missing.py:1: in <module>
    import totally_missing_pkg
E   ModuleNotFoundError: No module named 'totally_missing_pkg'
______________________ ERROR at setup of test_query ___________________________
    @pytest.fixture
    def conn():
>       raise RuntimeError("db down")
E       RuntimeError: db down

tests/test_db.py:6: RuntimeError
____________________ ERROR at teardown of test_cleanup ________________________
E       OSError: could not remove tmpdir

tests/test_db.py:20: OSError
=================================== FAILURES ===================================
__________________________________ test_math ___________________________________
    def test_math():
>       assert 1 == 2
E       assert 1 == 2

tests/test_fail.py:2: AssertionError
"""


def _by_exc(causes, exc):
    return next(c for c in causes if c.exc == exc)


def test_phase_collect_vs_setup_vs_teardown_vs_call():
    causes = summarize(_PHASES)
    assert _by_exc(causes, "ModuleNotFoundError").phase == "collect"
    assert _by_exc(causes, "RuntimeError").phase == "setup"
    assert _by_exc(causes, "OSError").phase == "teardown"
    assert _by_exc(causes, "AssertionError").phase == "call"


def test_outcome_is_unchanged_by_the_phase_split():
    # Backwards compatibility: `outcome` keeps its old values. Only `phase` is new.
    causes = summarize(_PHASES)
    assert _by_exc(causes, "ModuleNotFoundError").outcome == "ERROR"
    assert _by_exc(causes, "RuntimeError").outcome == "ERROR"
    assert _by_exc(causes, "AssertionError").outcome == "FAILED"


def test_format_breakdown_tags_setup_as_setup_not_collect():
    # The old code tagged a setup error `[collect]` because its banner starts with "ERROR".
    out = format_breakdown(summarize(_PHASES))
    assert "[setup] RuntimeError" in out
    assert "[collect] ModuleNotFoundError" in out
    assert "[collect] RuntimeError" not in out


def test_same_exception_in_different_phases_does_not_group():
    # A ModuleNotFoundError at collection and one raised inside a test body are different
    # problems (one has an env fix, one does not), so they must not share a Cause.
    out = """\
==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_a.py ____________________
E   ModuleNotFoundError: No module named 'zzz'
=================================== FAILURES ===================================
__________________________________ test_b ___________________________________
E       ModuleNotFoundError: No module named 'zzz'

tests/test_b.py:9: ModuleNotFoundError
"""
    causes = summarize(out)
    phases = sorted(c.phase for c in causes if c.exc == "ModuleNotFoundError")
    assert phases == ["call", "collect"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/react_repair/test_pytest_summary.py -v -k "phase or setup_as_setup"`
Expected: FAIL — `AttributeError: 'Cause' object has no attribute 'phase'`.

- [ ] **Step 3: Add the phase parser and thread it through**

In `src/python_deps/../react_repair/pytest_summary.py`, add after the `_PYFILE` regex (~line 38):

```python
# pytest's block banners name the phase. A COLLECTION error is per-FILE (the tests inside
# were never created as items); a setup/teardown error is per-TEST (the test WAS collected,
# its fixture broke); a failure is the test body itself. All three "ERROR" banners start with
# the same word, which is why bucketing on `startswith("ERROR")` conflated them.
_PHASE_PREFIXES = (
    ("ERROR collecting", "collect"),
    ("ERROR at setup of", "setup"),
    ("ERROR at teardown of", "teardown"),
)


def _phase_of(title: str) -> str:
    for prefix, phase in _PHASE_PREFIXES:
        if title.startswith(prefix):
            return phase
    return "call"
```

Add the field to `Cause` (keep the existing comments):

```python
@dataclass(frozen=True)
class Cause:
    exc: str          # exception type, e.g. "ModuleNotFoundError"
    detail: str       # representative RAW message (first seen), for display ("" if none)
    count: int        # blocks affected: MODULES for phase="collect", TESTS otherwise (see below)
    outcome: str      # "ERROR" (collection/setup/teardown) | "FAILED" (execution)
    module: str       # a representative file (first seen)
    phase: str = "call"   # "collect" | "setup" | "call" | "teardown" — the pytest phase
```

In `summarize`, compute the phase and put it in the grouping key:

```python
    for title, body in _blocks(output):
        exc, detail = _cause_of(body)
        if exc is None:
            continue
        phase = _phase_of(title)
        # Phase is part of the key: a ModuleNotFoundError at COLLECTION (an env problem with a
        # fix) and one raised inside a test body (residual logic) are different problems and
        # must not collapse into one Cause.
        key = (exc, _norm(detail), phase)
        g = groups.get(key)
        if g is None:
            groups[key] = {"exc": exc, "detail": detail, "count": 1,
                           "outcome": "FAILED" if phase == "call" else "ERROR",
                           "module": _module_of(title, body), "phase": phase}
        else:
            g["count"] += 1
```

In `format_breakdown`, tag from `phase` instead of `outcome`:

```python
    for c in causes[:top]:
        detail = f": {c.detail}" if c.detail else ""
        rows.append(f"  {c.count} × [{c.phase}] {c.exc}{detail}")
```

- [ ] **Step 4: Migrate the one pre-existing test that hand-builds a `Cause`**

`format_breakdown` now reads `c.phase` instead of deriving the tag from `c.outcome`. Every test
that goes through `summarize()` is fine — `summarize` computes `phase` correctly. But
`test_format_breakdown_tags_collect_and_run` (`tests/react_repair/test_pytest_summary.py:121-137`)
constructs `Cause(...)` **by hand with no `phase=`**, so those Causes silently default to
`phase="call"` and its `[collect]` assertion flips to `[call]`. Update it:

```python
def test_format_breakdown_tags_collect_and_run():
    # Each row is prefixed with the real pytest PHASE (Cause.phase), not a guess derived from
    # `outcome`: a collection error and a fixture-setup error BOTH have outcome="ERROR" but are
    # different problems (per-file vs per-test), so the tag now names the phase directly.
    causes = [
        Cause(exc="ModuleNotFoundError", detail="No module named 'psycopg2'",
              count=3, outcome="ERROR", module="tests/test_db.py", phase="collect"),
        Cause(exc="AssertionError", detail="", count=5, outcome="FAILED",
              module="tests/test_logic.py", phase="call"),
        Cause(exc="RuntimeError", detail="db down", count=2, outcome="ERROR",
              module="tests/test_db.py", phase="setup"),
    ]
    out = format_breakdown(causes)
    assert "3 × [collect] ModuleNotFoundError: No module named 'psycopg2'" in out
    assert "5 × [call] AssertionError" in out
    # A SETUP error is an ERROR outcome but a RUN-phase failure — the old `outcome`-derived tag
    # called this [collect], which was wrong.
    assert "2 × [setup] RuntimeError" in out
```

Delete the old test's trailing `weird` / `outcome="whatever"` block — `phase` is now the tag's sole
source and it has a safe default, so the "unexpected outcome degrades to [run]" case is gone.

- [ ] **Step 5: Run the whole file to verify nothing regressed**

Run: `python -m pytest tests/react_repair/test_pytest_summary.py -v`
Expected: PASS, including every pre-existing test.

> Do **not** change any assertion about `count`. Its semantics are deliberately unchanged (see
> Global Constraints).

> **Fixture honesty:** the `_PHASES` string in Step 1 is **hand-typed**, whereas every other fixture
> in this file is captured verbatim from a real `pytest` run — that is the file's stated convention
> and the reason its parser is trustworthy. Before committing, generate the real thing: create a
> throwaway repo with one un-importable module, one fixture that raises in setup, one that raises in
> teardown, and one failing assertion; run
> `python -m pytest -q --continue-on-collection-errors`; and paste the **actual** output in place of
> `_PHASES`. The banner wording is confirmed correct at the pytest-source level
> (`_pytest/terminal.py:1201-1213` builds `"ERROR collecting "` and `f"ERROR at {rep.when} of "`),
> but a captured fixture is what protects the parser from a future pytest reformat.

- [ ] **Step 6: Commit**

```bash
git add src/react_repair/pytest_summary.py tests/react_repair/test_pytest_summary.py
git commit -m "fix(pytest_summary): parse the pytest PHASE; stop tagging setup errors as [collect]

`startswith(\"ERROR\")` lumped `ERROR collecting <file>` (per-FILE, tests never existed) with
`ERROR at setup of <test>` (per-TEST, fixture broke). Adds Cause.phase and keys grouping on it,
so a ModuleNotFoundError at collection never collapses into one with a body-raised one."
```

---

### Task 1B: 🔴 `classify_tool_error` — the flagship error shape does not classify

**Why — this is a design-level hole, found by verification, not a test typo.** The entire arm is
motivated by `pip install psycopg2` → `pg_config` → `apt-get install libpq-dev`. **That error does
not classify today**, so `enrich` would produce **no node and no edge** for it and Mechanism 1
would be a no-op on its own headline case. Verified live against the current tree:

| log text | `diagnose()` result |
|---|---|
| `pg_config: not found` | ✅ `environment`, discovery=`pg_config` |
| `sh: 1: pg_config: not found` | ✅ `environment`, discovery=`pg_config` |
| **`Error: pg_config executable not found`** ← *what psycopg2 actually prints* | ❌ **`ambiguous`, discovery=`None`** |

The cause: `_TOOL_COMMAND_NOT_FOUND_RE` (`src/python_deps/failure_classifier.py:246-249`) requires a
**colon** — `<name>:\s+(command not found|not found)`. setuptools/psycopg2 emit
`<name> executable not found`, with no colon and the word "executable" in between.

**Files:**
- Modify: `src/python_deps/failure_classifier.py:246-266`
- Test: `tests/test_failure_classifier.py` (find the existing file with
  `grep -rln classify_tool_error tests/`)

**Interfaces:**
- Consumes: nothing.
- Produces: `classify_tool_error(command, output)` now also recognises the
  `<name> executable not found` shape. Signature unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_tool_error_recognises_the_executable_not_found_shape():
    # The REAL psycopg2 / setuptools wording. No colon, and the word "executable" in between —
    # which is why _TOOL_COMMAND_NOT_FOUND_RE (which requires "<name>: not found") missed it.
    assert classify_tool_error("pip install psycopg2==2.9.12",
                               "Error: pg_config executable not found.") == "pg_config"


def test_tool_error_executable_shape_is_case_insensitive():
    assert classify_tool_error("", "error: PG_CONFIG executable not found") == "PG_CONFIG"


def test_tool_error_still_recognises_the_colon_shape():
    # Regression: the pre-existing shape must keep working.
    assert classify_tool_error("", "sh: 1: pg_config: not found") == "pg_config"


def test_tool_error_does_not_fire_on_unrelated_not_found_text():
    assert classify_tool_error("", "404 Not Found") is None
    assert classify_tool_error("", "No module named 'yaml'") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_failure_classifier.py -v -k executable`
Expected: FAIL — returns `None`, not `"pg_config"`.

- [ ] **Step 3: Add the third regex**

In `src/python_deps/failure_classifier.py`, beside the two existing tool regexes:

```python
# setuptools/autoconf-style: "Error: pg_config executable not found." — NO colon, and the word
# "executable" between the name and "not found", so _TOOL_COMMAND_NOT_FOUND_RE (which requires
# "<name>: not found") never matched it. This is the SHAPE THE psycopg2 BUILD ACTUALLY EMITS,
# and without it the graph arm's headline case produces no node at all.
_TOOL_EXECUTABLE_NOT_FOUND_RE = re.compile(
    r"\b([A-Za-z0-9_.-]+)\s+executable\s+not\s+found",
    re.IGNORECASE,
)
```

and add it to `classify_tool_error`, **after** the colon shape (which is more specific) and before
the `FileNotFoundError` fallback:

```python
def classify_tool_error(command: str, output: str) -> str | None:
    """Return the tool name if ``output`` looks like a missing executable, else None."""
    text = output or ""
    m = _TOOL_COMMAND_NOT_FOUND_RE.search(text)
    if m:
        return m.group(1)
    m = _TOOL_EXECUTABLE_NOT_FOUND_RE.search(text)      # ADD
    if m:                                               # ADD
        return m.group(1)                               # ADD
    m = _TOOL_FILENOTFOUNDERROR_RE.search(text)
    if m:
        return m.group(1)
    return None
```

- [ ] **Step 4: Verify the flagship case now classifies end-to-end**

Run:
```bash
python -c "
import sys; sys.path.insert(0, 'src')
from python_deps.depgraph.diagnose import diagnose, RepoContext
d = diagnose('pip install psycopg2==2.9.12', 'Error: pg_config executable not found', RepoContext())
print(d.mode.value, d.discovery.name if d.discovery else None)
"
```
Expected: `environment pg_config` (it prints `ambiguous None` today).

- [ ] **Step 5: Run the full classifier + depgraph suites**

Run: `python -m pytest tests/test_failure_classifier.py tests/depgraph/ -q`
Expected: PASS. A **widened** classifier can only turn `AMBIGUOUS` into `ENVIRONMENT`, never the
reverse — but run the whole suite anyway, because `diagnose`'s router and `runtime_ingest` both sit
downstream of it.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/failure_classifier.py tests/test_failure_classifier.py
git commit -m "fix(failure_classifier): recognise '<tool> executable not found'

_TOOL_COMMAND_NOT_FOUND_RE required a colon ('<name>: not found'), but setuptools/psycopg2 emit
'Error: pg_config executable not found' — no colon. So the single most common native-build failure
in the corpus classified as AMBIGUOUS with no Discovery, meaning runtime ingest produced NO node
and NO edge for it. Found by verifying the graph-arm plan against the tree."
```

---

### Task 2: `owner_node_for_command` — resolve a failing command to its owner NODE

**Why:** `ingest_runtime_failures` takes an `owner_node_id`, but **no caller in the repo passes one** (`orchestrator.py:209,1001`), so every discovery falls back to `TEST_NODE_ID` and hangs off the goal node as a flat star with no depth. The walk in Task 5 needs `pkg:psycopg2 → binary:pg_config`, not `test:… → binary:pg_config`. `req_slice._provider_from_command` gets as far as `"pip:psycopg2"` — a *provider* id — but nodes are keyed `pkg:psycopg2==2.9.12`. This closes that gap. **It also improves the v3 arm**, which is anchoring everything at the Test node today.

**Files:**
- Create: `src/python_deps/depgraph/graph_enrich.py`
- Test: `tests/depgraph/test_graph_enrich.py`

**Interfaces:**
- Consumes: `req_slice._provider_from_command(command) -> str | None` (`req_slice.py:38`); `normalize_package_name` — **defined** in `src/python_deps/import_mapping.py:62-64` as the literal PEP 503 formula `re.sub(r"[-_.]+", "-", name).lower()`, and **re-exported** by `depgraph/naming.py:22-26`, so `from python_deps.depgraph.naming import normalize_package_name` works.
- Produces: `owner_node_for_command(graph: DepGraph, command: str) -> str | None` — a `pkg:` node id, or `None` when the command names no single package (batch, `-r`, `-e`, apt, unparseable).

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_graph_enrich.py`:

```python
"""Tests for graph_enrich (pure; no Docker, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.graph_enrich import owner_node_for_command
from python_deps.depgraph.ids import TEST_NODE_ID, package_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)


def _pkg(name: str, version: str | None = None) -> Node:
    return Node(
        id=package_id(name, version),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        version=version,
        state=State.MISSING,
    )


def _graph() -> DepGraph:
    return (
        DepGraph()
        .with_node(Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL))
        .with_node(_pkg("psycopg2", "2.9.12"))
        .with_node(_pkg("charset-normalizer", "3.3.2"))
    )


def test_pinned_pip_install_resolves_to_the_package_node():
    assert owner_node_for_command(_graph(), "pip install psycopg2==2.9.12") == "pkg:psycopg2==2.9.12"


def test_unpinned_pip_install_still_resolves_by_name():
    # The command carries no version; the NODE does. Match on canonical name, not on the id.
    assert owner_node_for_command(_graph(), "pip install psycopg2") == "pkg:psycopg2==2.9.12"


def test_name_is_canonicalized_underscore_vs_hyphen():
    # PEP 503: charset_normalizer and charset-normalizer are the same distribution.
    got = owner_node_for_command(_graph(), "pip install charset_normalizer")
    assert got == "pkg:charset-normalizer==3.3.2"


def test_batch_install_is_not_attributable():
    assert owner_node_for_command(_graph(), "pip install psycopg2 asyncpg") is None


def test_requirements_file_install_is_not_attributable():
    assert owner_node_for_command(_graph(), "pip install -r requirements.txt") is None


def test_editable_install_is_not_attributable():
    assert owner_node_for_command(_graph(), "pip install -e .") is None


def test_apt_command_is_not_a_package_owner():
    # apt installs a system package, not a pip Package node — there is no pkg: owner.
    assert owner_node_for_command(_graph(), "apt-get install -y libpq-dev") is None


def test_unknown_package_has_no_node():
    assert owner_node_for_command(_graph(), "pip install patchright") is None


def test_empty_and_none_command_are_safe():
    assert owner_node_for_command(_graph(), "") is None
    assert owner_node_for_command(_graph(), None) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_graph_enrich.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.graph_enrich'`.

- [ ] **Step 3: Create the module**

Create `src/python_deps/depgraph/graph_enrich.py`:

```python
"""Observation-driven graph update for the react arm (spec Rev 3.3 §7).

Pure except for `certify_only`'s narrow executor call. The heavy lifting —
classify a log line into a Discovery, append-if-new / annotate-if-known, draw the
REQUIRES edge — is ALREADY DONE by `runtime_ingest.ingest_runtime_failures`; this
module supplies the one thing it has always been missing: the OWNER.
"""
from __future__ import annotations

from python_deps.depgraph.naming import normalize_package_name
from python_deps.depgraph.req_slice import _provider_from_command
from python_deps.depgraph.schema import DepGraph, NodeType


def owner_node_for_command(graph: DepGraph, command: str | None) -> str | None:
    """`pip install psycopg2==2.9.12` -> `pkg:psycopg2==2.9.12`, by canonical name.

    Returns None when the command names no single package — a batch install, a
    `-r`/`-c`/`-e` install, an apt command, or a name with no Package node. A None
    owner makes `ingest_runtime_failures` fall back to TEST_NODE_ID, which is a flat
    star with no depth; that is why the per-package-install directive (one `pip
    install` per package) is load-bearing and not merely tidy.

    NOTE the two id spaces: `_provider_from_command` returns a PROVIDER id
    (`pip:psycopg2`); graph nodes are keyed `pkg:psycopg2==2.9.12`. The version is on
    the NODE, not necessarily in the command, so we match on canonical NAME.
    """
    provider = _provider_from_command(command or "")
    if provider is None or not provider.startswith("pip:"):
        return None
    wanted = normalize_package_name(provider.split(":", 1)[1])
    for node in graph.nodes:
        if node.type is NodeType.PACKAGE and normalize_package_name(node.name) == wanted:
            return node.id
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/depgraph/test_graph_enrich.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/graph_enrich.py tests/depgraph/test_graph_enrich.py
git commit -m "feat(depgraph): owner_node_for_command — resolve a failing install to its owner node

ingest_runtime_failures has always taken an owner_node_id, and NO caller has ever passed one,
so every runtime discovery hangs off TEST_NODE_ID as a flat star. This is the missing last mile:
provider id (pip:psycopg2) -> node id (pkg:psycopg2==2.9.12), matched on canonical name."
```

---

### Task 3: `blocks` / `verdict` — one home for ALL edge semantics

**Why:** `REQUIRES` is **not** the only edge type, and not every `REQUIRES` edge is causal.
1. `CONFLICTS_WITH` (`schema.py:53`, emitted from uv's unsat core at `resolve_errors.py:303,340`) makes a node **un-installable** — `emit._is_emittable` (`emit.py:84-96`) already refuses to emit it. But "MISSING with no MISSING prerequisite" marks it **actionable**, so the agent would run `pip install X` forever. **This is the bug this task fixes.**
2. `data["hard"] is False` — `emit.py:69-70` says *"soft requires edges never block"*; the LLM's Config/Service edges are SOFT.
3. `Edge.marker` (`resolve_lock.py:446-451`) — a universal lock lists deps for the **whole** requires-python range; an edge whose marker is false for the target is not causal here.

Today those rules are smeared across `emit._toolchain_ready`, `emit._conflicted`, and `resolve_lock`'s marker pruning. The arm gets **one** copy.

**Files:**
- Create: `src/python_deps/depgraph/graph_context.py`
- Test: `tests/depgraph/test_graph_context.py`

**Interfaces:**
- Consumes: `schema.{DepGraph, Edge, Node, EdgeType, State}`.
- Produces:
  - `blocks(edge: Edge, target_env=None) -> bool` — is this edge a hard, causal prerequisite on this target?
  - `in_conflict(graph: DepGraph, node: Node) -> bool`
  - `verdict(graph: DepGraph, node: Node, target_env=None) -> str` — `"ACTIONABLE"` | `"WAITING"` | `"BLOCKED"`
  - Constants `ACTIONABLE = "ACTIONABLE"`, `WAITING = "WAITING"`, `BLOCKED = "BLOCKED"`

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_graph_context.py`:

```python
"""Tests for graph_context edge semantics (pure; no Docker, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.graph_context import (
    ACTIONABLE, BLOCKED, SATISFIED_OK, WAITING, blocks, in_conflict, verdict,
)
from python_deps.depgraph.ids import capability_id, package_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)


def _pkg(name, version="1.0", state=State.MISSING) -> Node:
    return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                version=version, state=state)


def _tool(name, state=State.MISSING) -> Node:
    return Node(id=f"binary:{name}", type=NodeType.TOOL, name=name,
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=state)


def test_actionable_when_nothing_beneath_is_missing():
    g = DepGraph().with_node(_tool("pg_config"))
    assert verdict(g, g.get("binary:pg_config")) == ACTIONABLE


def test_waiting_when_a_hard_prerequisite_is_missing():
    g = (DepGraph()
         .with_node(_pkg("psycopg2"))
         .with_node(_tool("pg_config"))
         .with_edge(Edge(src="pkg:psycopg2==1.0", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver")))
    assert verdict(g, g.get("pkg:psycopg2==1.0")) == WAITING
    assert verdict(g, g.get("binary:pg_config")) == ACTIONABLE


def test_satisfied_prerequisite_does_not_make_the_owner_wait():
    g = (DepGraph()
         .with_node(_pkg("psycopg2"))
         .with_node(_tool("pg_config", state=State.SATISFIED))
         .with_edge(Edge(src="pkg:psycopg2==1.0", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver")))
    assert verdict(g, g.get("pkg:psycopg2==1.0")) == ACTIONABLE


# ── THE BUG THIS TASK FIXES ──────────────────────────────────────────────────

def test_conflicted_node_is_BLOCKED_even_with_zero_missing_prerequisites():
    """The exact shape that fooled the old root definition.

    `pkg:pydantic` is MISSING and has NO missing prerequisite, so "MISSING with no
    MISSING prerequisite" calls it a root and tells the agent `pip install pydantic`.
    It CANNOT be installed at any version — emit._is_emittable already refuses to emit
    a conflicted node. It must be BLOCKED, never ACTIONABLE.
    """
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11"))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    node = g.get("pkg:pydantic==2.11")
    assert in_conflict(g, node) is True
    assert verdict(g, node) == BLOCKED
    assert verdict(g, node) != ACTIONABLE


def test_conflict_blocks_BOTH_endpoints():
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11"))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver")))
    assert verdict(g, g.get("pkg:fastapi==0.115")) == BLOCKED


def test_a_conflicts_edge_is_not_a_prerequisite():
    # CONFLICTS_WITH must never be traversed as a requires edge.
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.CONFLICTS_WITH,
             origin="resolver")
    assert blocks(e) is False


# ── soft edges ───────────────────────────────────────────────────────────────

def test_soft_edge_does_not_block():
    # emit.py:69-70 — "soft requires edges never block (invariant #10)".
    e = Edge(src="pkg:a==1.0", dst="config:DATABASE_URL", relation=EdgeType.REQUIRES,
             origin="llm", data={"hard": False})
    assert blocks(e) is False


def test_soft_missing_prerequisite_leaves_the_owner_actionable():
    # NOTE: DiscoveredBy has NO `LLM` member. The codebase's name for an LLM-admitted node is
    # CLASSIFIER (schema.py:64-74; see patch_gate.py:250).
    g = (DepGraph()
         .with_node(_pkg("app"))
         .with_node(Node(id="config:DATABASE_URL", type=NodeType.CONFIG,
                         name="DATABASE_URL", layer=Layer.CONFIG,
                         discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING))
         .with_edge(Edge(src="pkg:app==1.0", dst="config:DATABASE_URL",
                         relation=EdgeType.REQUIRES, origin="llm",
                         data={"hard": False})))
    assert verdict(g, g.get("pkg:app==1.0")) == ACTIONABLE


# ── a SATISFIED node is not "actionable" ─────────────────────────────────────

def test_a_satisfied_node_is_never_actionable():
    """A SATISFIED leaf has no missing prerequisites, so a verdict() that only looked at
    PREREQUISITES would call it ACTIONABLE and hand it a record — telling the agent to
    'fix' something that is already fine. verdict() must check the node's OWN state first."""
    g = DepGraph().with_node(_tool("pkg-config", state=State.SATISFIED))
    assert verdict(g, g.get("binary:pkg-config")) == SATISFIED_OK
    assert verdict(g, g.get("binary:pkg-config")) != ACTIONABLE


def test_hard_edge_is_the_default_when_the_key_is_absent():
    e = Edge(src="pkg:a==1.0", dst="binary:x", relation=EdgeType.REQUIRES, origin="resolver")
    assert blocks(e) is True


# ── markers ──────────────────────────────────────────────────────────────────

def test_marker_that_does_not_hold_is_skipped():
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.REQUIRES,
             origin="resolver", marker='python_version < "3.9"')
    assert blocks(e, target_env={"python_version": "3.12"}) is False


def test_marker_that_holds_is_traversed():
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.REQUIRES,
             origin="resolver", marker='python_version >= "3.9"')
    assert blocks(e, target_env={"python_version": "3.12"}) is True


def test_marker_is_conservatively_traversed_when_no_target_env_is_known():
    # Without a target we cannot evaluate — do NOT silently drop a real prerequisite.
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.REQUIRES,
             origin="resolver", marker='python_version < "3.9"')
    assert blocks(e, target_env=None) is True


def test_unparseable_marker_is_conservatively_traversed():
    e = Edge(src="pkg:a==1.0", dst="pkg:b==1.0", relation=EdgeType.REQUIRES,
             origin="resolver", marker="this is not a marker")
    assert blocks(e, target_env={"python_version": "3.12"}) is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_graph_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.graph_context'`.

> If `Edge` has no `marker` field, stop and check `resolve_lock.py:446-451`, which constructs
> `Edge(..., marker=edge_marker[(src, dst)])`. It exists.

- [ ] **Step 3: Create the module**

Create `src/python_deps/depgraph/graph_context.py`:

```python
"""Graph context for the react arm (spec Rev 3.3 §6). Pure — no Docker, no network.

This module owns EVERY rule about what an edge means. Nothing downstream of
`verdict()` touches an edge attribute. Today those rules are smeared across
`emit._toolchain_ready` (soft), `emit._conflicted` (conflicts), and `resolve_lock`'s
marker pruning (markers); the arm gets ONE copy, unit-tested against hand-built graphs.
"""
from __future__ import annotations

import logging

from python_deps.depgraph.schema import DepGraph, Edge, EdgeType, Node, State

logger = logging.getLogger(__name__)

ACTIONABLE = "ACTIONABLE"      # MISSING, nothing missing beneath it -> the agent acts HERE
WAITING = "WAITING"            # a hard prerequisite is missing -> fix that first
BLOCKED = "BLOCKED"            # in a version conflict -> NO install will ever work
SATISFIED_OK = "SATISFIED_OK"  # already fine -> nothing to do, and NO record


def _marker_holds(edge: Edge, target_env: dict | None) -> bool:
    """True when the edge's PEP 508 marker holds for the target (or is unevaluable).

    A universal lock lists dependencies for the WHOLE requires-python range, so an edge
    carrying `python_version < "3.9"` is not causal on a 3.12 target. When we cannot
    evaluate — no target env, or an unparseable marker — we traverse CONSERVATIVELY:
    dropping a real prerequisite is far worse than keeping a spurious one, because the
    spurious one will simply certify SATISFIED and land in the rule-out ring.
    """
    marker = getattr(edge, "marker", None)
    if not marker or target_env is None:
        return True
    try:
        from packaging.markers import Marker
        return bool(Marker(marker).evaluate(target_env))
    except Exception:                                  # noqa: BLE001 — never break the render
        logger.debug("graph_context: unevaluable marker %r; traversing", marker)
        return True


def blocks(edge: Edge, target_env: dict | None = None) -> bool:
    """Is this edge a HARD, CAUSAL prerequisite on THIS target?

    The three ways a graph edge is NOT a prerequisite:
      * it is not a REQUIRES edge at all (CONFLICTS_WITH is a constraint, not a need);
      * it is SOFT -- emit.py:69-70, "soft requires edges never block (invariant #10)";
      * its environment marker does not hold for the target (resolve_lock.py:446-451).
    """
    if edge.relation is not EdgeType.REQUIRES:
        return False
    if not (edge.data or {}).get("hard", True):
        return False
    return _marker_holds(edge, target_env)


def in_conflict(graph: DepGraph, node: Node) -> bool:
    """True when the node sits on a CONFLICTS_WITH edge (uv unsat core).

    `emit._is_emittable` (emit.py:84-96) already refuses to emit such a node: it cannot be
    installed at ANY version. Without this check the node looks like a perfectly good root
    -- MISSING, with no missing prerequisite -- and we would tell the agent to `pip install`
    it, forever.
    """
    return any(
        e.relation is EdgeType.CONFLICTS_WITH and node.id in (e.src, e.dst)
        for e in graph.edges
    )


def verdict(graph: DepGraph, node: Node, target_env: dict | None = None) -> str:
    """SATISFIED_OK | BLOCKED | WAITING | ACTIONABLE — the node's decision state.

    Order matters, and both early returns are load-bearing:

      1. A SATISFIED node is DONE. Checking prerequisites first would call a satisfied leaf
         ACTIONABLE (it has no MISSING prerequisites, after all) and hand it a "fix this"
         record — telling the agent to install something that is already installed.
      2. A CONFLICTED node cannot be installed at ANY version (emit._is_emittable already
         refuses to emit it), yet it too has no missing prerequisites and would otherwise
         look like a perfectly good root. The agent would `pip install` it forever.

    Only after both of those do prerequisites decide WAITING vs ACTIONABLE.
    """
    if node.state is State.SATISFIED:
        return SATISFIED_OK
    if in_conflict(graph, node):
        return BLOCKED
    for edge in graph.edges:
        if edge.src != node.id or not blocks(edge, target_env):
            continue
        dst = graph.get(edge.dst)
        if dst is not None and dst.state is not State.SATISFIED:
            return WAITING
    return ACTIONABLE
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/depgraph/test_graph_context.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/graph_context.py tests/depgraph/test_graph_context.py
git commit -m "feat(depgraph): blocks()/verdict() — one home for all edge semantics

Fixes the conflicted-root bug: a node on a CONFLICTS_WITH edge is MISSING with no missing
prerequisite, so 'root = MISSING with no missing prereq' calls it ACTIONABLE and the agent
runs \`pip install X\` forever — while emit._is_emittable already knows it cannot be installed
at any version. Also honours soft edges (emit.py:69-70) and env markers (resolve_lock.py:446)."
```

---

### Task 4: `tests_hidden` — the weight a collection error cannot report

**Why:** A collection error is **per-file** — the tests inside were never created as items, so pytest genuinely does not know they exist. `Cause.count` for a `[collect]` row is therefore **modules**, not tests, and ranking by it puts a 23-test `AssertionError` above an import error hiding 200 tests. `format_breakdown`'s docstring says recovering this *"needs the hidden gold set or the graph arm; do not attempt it here"* — so it goes **here**, in the graph arm, and it is rendered as an **estimate**, never as a measured count.

**Files:**
- Modify: `src/python_deps/depgraph/graph_context.py`
- Test: `tests/depgraph/test_graph_context.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `tests_hidden(repo_path: str | None, module: str) -> int | None` — a static estimate of the number of test functions in `module` (a repo-relative `.py` path), or `None` when it cannot be determined.

- [ ] **Step 1: Write the failing test**

Append to `tests/depgraph/test_graph_context.py`:

```python
from python_deps.depgraph.graph_context import tests_hidden


def test_tests_hidden_counts_sync_and_async_test_defs(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_db.py").write_text(
        "import psycopg2\n"
        "\n"
        "def test_one():\n"
        "    pass\n"
        "\n"
        "async def test_two():\n"
        "    pass\n"
        "\n"
        "def helper():\n"           # not a test
        "    pass\n"
        "\n"
        "def test_three(conn):\n"
        "    pass\n"
    )
    assert tests_hidden(str(tmp_path), "tests/test_db.py") == 3


def test_tests_hidden_counts_indented_methods_in_test_classes(tmp_path):
    (tmp_path / "t.py").write_text(
        "class TestThing:\n"
        "    def test_a(self):\n"
        "        pass\n"
        "    def test_b(self):\n"
        "        pass\n"
    )
    assert tests_hidden(str(tmp_path), "t.py") == 2


def test_tests_hidden_returns_None_for_a_missing_file(tmp_path):
    assert tests_hidden(str(tmp_path), "nope.py") is None


def test_tests_hidden_returns_None_when_the_file_has_no_tests(tmp_path):
    (tmp_path / "t.py").write_text("def helper():\n    pass\n")
    assert tests_hidden(str(tmp_path), "t.py") is None


def test_tests_hidden_returns_None_without_a_repo_path(tmp_path):
    assert tests_hidden(None, "tests/test_db.py") is None


def test_tests_hidden_never_escapes_the_repo(tmp_path):
    # `module` comes from parsed pytest output — treat it as untrusted input.
    assert tests_hidden(str(tmp_path), "../../../etc/passwd") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_graph_context.py -v -k tests_hidden`
Expected: FAIL — `ImportError: cannot import name 'tests_hidden'`.

- [ ] **Step 3: Implement**

Add to `src/python_deps/depgraph/graph_context.py` (imports first: `import re`, `from pathlib import Path`):

```python
# `def test_x(` / `async def test_x(` at any indent — so class-based test methods count too.
_TEST_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+test\w*[ \t]*\(", re.MULTILINE)


def tests_hidden(repo_path: str | None, module: str) -> int | None:
    """Static estimate of how many tests a module holds. None when undeterminable.

    A COLLECTION error is per-FILE: the tests inside were never created as items, so
    pytest cannot tell us how many it hid. `Cause.count` for such a row is MODULES, not
    tests -- rank by it and a 23-test AssertionError outranks an import error hiding 200
    tests. This is the weight that fixes the ranking.

    It is an ESTIMATE and MUST be rendered as one ("~200 tests hidden, est."). It
    under-counts `@pytest.mark.parametrize` expansion, which pytest resolves at collection
    time -- exactly the thing that did not happen.
    """
    if not repo_path or not module:
        return None
    root = Path(repo_path).resolve()
    try:
        # `module` is parsed out of pytest output -> untrusted. Never read outside the repo.
        path = (root / module).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    if not path.is_file():
        return None
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    return len(_TEST_DEF.findall(text)) or None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/depgraph/test_graph_context.py -v`
Expected: PASS (19 tests — 13 from Task 3, 6 new).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/graph_context.py tests/depgraph/test_graph_context.py
git commit -m "feat(depgraph): tests_hidden — the weight a collection error cannot report

A collection error is per-FILE; its tests never became items, so Cause.count is MODULES.
Ranking by it puts a 23-test AssertionError above an import error hiding 200 tests. Static
\`def test_\` count, rendered as an ESTIMATE. Lives in the graph arm per format_breakdown's
own docstring ('do not attempt it here')."
```

---

### Task 5: `render_graph_context` — the subgraph edge-list + per-node records

**Why:** This is what the agent actually reads. A DAG serializes natively as **edges**, not as a tree: `binary:pg_config` is required by *both* `pkg:psycopg2` and `pkg:asyncpg`, so a tree must print its record twice or invent a back-reference, whereas an edge list shows two lines converging on one node — **the collapse becomes a visible fact of the structure**.

**Files:**
- Modify: `src/python_deps/depgraph/graph_context.py`
- Test: `tests/depgraph/test_graph_context.py`

**Interfaces:**
- Consumes: `verdict`/`blocks`/`tests_hidden` (Tasks 3, 4); `Cause` (Task 1); `advise._conflict_note` (`advise.py:220`).
- Produces: `render_graph_context(graph, result, causes, prev_states, repo_path=None, target_env=None) -> str`. `result` is a `RunResult`-shaped object (`.ok`, `.failing_command`, `.output`) or `None`. `prev_states` is `dict[str, State]`. Returns `""` when there is nothing worth saying.

**Design rules being implemented (spec §6.1–6.6):**
- Two sections: `SUBGRAPH` (edges) then records.
- Each **error node** gets `REQUIRES` (down = the cause) and `REQUIRED BY` (up = the impact) as **separate** lists. `[state]` inline on every node.
- A record goes **only** to `ACTIONABLE` (`★`) and `BLOCKED` (`✖`) nodes. **No top-N cap** — actionable roots are independent, so the agent should batch them all in one patch.
- Package→Package closure is **one summary line**, never enumerated; a MISSING member is promoted to its own line.
- Fields self-select: `check`/`fix` always; `why`/`source` only when runtime-discovered; `tried` only when `attempts` is non-empty; `blocks` only when >1.
- A `call`/`teardown` phase cause **never** touches the graph.

- [ ] **Step 1: Write the failing test**

Append to `tests/depgraph/test_graph_context.py`:

```python
from python_deps.depgraph.graph_context import render_graph_context
from python_deps.depgraph.schema import Attempt, DiscoveredBy

try:
    from src.react_repair.pytest_summary import Cause
except ImportError:                       # tests/depgraph/ does not add the repo root
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from src.react_repair.pytest_summary import Cause


class _Result:
    def __init__(self, ok=True, failing_command=None, output=""):
        self.ok, self.failing_command, self.output = ok, failing_command, output


def _pg_graph() -> DepGraph:
    """psycopg2 and asyncpg both need pg_config. pkg-config is fine. THE collapse case."""
    g = (DepGraph()
         .with_node(_pkg("psycopg2", "2.9.12"))
         .with_node(_pkg("asyncpg", "0.30.0"))
         .with_node(_tool("pkg-config", state=State.SATISFIED))
         .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                         layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RUNTIME,
                         state=State.MISSING,
                         check_command="command -v pg_config",
                         chosen_fix="apt-get install -y libpq-dev",
                         evidence='pip install psycopg2: "pg_config executable not found"'))
         .with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver"))
         .with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="binary:pkg-config",
                         relation=EdgeType.REQUIRES, origin="resolver"))
         .with_edge(Edge(src="pkg:asyncpg==0.30.0", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver")))
    return g


def _collect_cause(name, count=1, module="tests/test_db.py"):
    return Cause(exc="ModuleNotFoundError", detail=f"No module named '{name}'",
                 count=count, outcome="ERROR", module=module, phase="collect")


def test_shared_root_appears_as_two_edges_but_ONE_record():
    out = render_graph_context(
        _pg_graph(), _Result(ok=True),
        [_collect_cause("psycopg2"), _collect_cause("asyncpg", module="tests/test_a.py")],
        prev_states={},
    )
    # Two edge lines converge on the same node -- that IS the collapse.
    assert out.count("--requires-->  binary:pg_config") == 2
    # ...but the record is printed exactly once.
    assert out.count("★ binary:pg_config") == 1


def test_requires_and_required_by_are_separate_sections():
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("psycopg2")],
                               prev_states={})
    assert "REQUIRES" in out
    assert "REQUIRED BY" in out
    assert out.index("REQUIRES") < out.index("REQUIRED BY")


def test_satisfied_prerequisite_is_shown_inline_and_gets_no_record():
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("psycopg2")],
                               prev_states={})
    assert "binary:pkg-config" in out          # in the edge list...
    assert "★ binary:pkg-config" not in out    # ...but never as a record


def test_waiting_node_gets_no_record():
    # psycopg2 is MISSING but WAITING on pg_config -- no action available, so no record.
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("psycopg2")],
                               prev_states={})
    assert "★ pkg:psycopg2" not in out


def test_every_actionable_root_gets_a_record_no_top_n_cap():
    """Actionable roots are INDEPENDENT -- the agent should batch all of them in one patch."""
    g = DepGraph()
    causes = []
    for i in range(6):
        g = g.with_node(Node(id=f"binary:t{i}", type=NodeType.TOOL, name=f"t{i}",
                             layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                             state=State.MISSING, check_command=f"command -v t{i}",
                             chosen_fix=f"apt-get install -y t{i}"))
        g = g.with_node(_pkg(f"p{i}"))
        g = g.with_edge(Edge(src=f"pkg:p{i}==1.0", dst=f"binary:t{i}",
                             relation=EdgeType.REQUIRES, origin="resolver"))
        causes.append(_collect_cause(f"p{i}"))
    out = render_graph_context(g, _Result(ok=True), causes, prev_states={})
    for i in range(6):
        assert f"★ binary:t{i}" in out, f"root t{i} was truncated away"


def test_conflicted_node_renders_as_BLOCKED_never_as_actionable():
    g = (DepGraph()
         .with_node(_pkg("pydantic", "2.11"))
         .with_node(_pkg("fastapi", "0.115"))
         .with_edge(Edge(src="pkg:pydantic==2.11", dst="pkg:fastapi==0.115",
                         relation=EdgeType.CONFLICTS_WITH, origin="resolver",
                         data={"summary": "pydantic>=2.11 needs typing-ext>=4.12"})))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("pydantic")], prev_states={})
    assert "✖ pkg:pydantic==2.11" in out
    assert "CANNOT INSTALL" in out
    assert "★ pkg:pydantic==2.11" not in out


def test_call_phase_failure_never_consults_the_graph():
    cause = Cause(exc="AssertionError", detail="assert 3 == 4", count=23,
                  outcome="FAILED", module="tests/test_math.py", phase="call")
    out = render_graph_context(_pg_graph(), _Result(ok=True), [cause], prev_states={})
    assert "NOT AN ENVIRONMENT FAILURE" in out
    assert "AssertionError" in out
    assert "REQUIRES" not in out          # no subgraph was walked for it


def test_cause_with_no_graph_node_says_so_instead_of_inventing_one():
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("patchright")],
                               prev_states={})
    assert "NO GRAPH EXPLANATION" in out
    assert "patchright" in out


def test_build_failure_anchors_at_the_command_owner_when_causes_is_empty():
    """loop.py:202 only runs pytest when the build is GREEN. On a build-fail turn `causes`
    is empty and the ONLY failure to anchor at is the failing command."""
    out = render_graph_context(
        _pg_graph(),
        _Result(ok=False, failing_command="pip install psycopg2==2.9.12",
                output="Error: pg_config executable not found"),
        causes=[], prev_states={},
    )
    assert "BUILD FAILED" in out
    assert "pkg:psycopg2==2.9.12" in out
    assert "★ binary:pg_config" in out
    assert "TESTS DID NOT RUN" in out


def test_state_delta_reports_a_regression_the_agent_caused():
    g = _pg_graph()
    prev = {"binary:pg_config": State.SATISFIED}
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states=prev)
    assert "SINCE YOUR LAST EDIT" in out
    assert "SATISFIED → MISSING" in out


def test_fields_self_select_resolver_node_omits_why_and_source():
    g = (DepGraph()
         .with_node(_pkg("psycopg2", "2.9.12"))
         .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                         layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                         state=State.MISSING, check_command="command -v pg_config",
                         chosen_fix="apt-get install -y libpq-dev"))
         .with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="binary:pg_config",
                         relation=EdgeType.REQUIRES, origin="resolver")))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states={})
    assert "check " in out and "fix " in out
    assert "why " not in out          # resolver-sourced -> nothing to justify
    assert "tried " not in out        # no attempts


def test_fields_self_select_contested_node_shows_why_and_tried():
    g = _pg_graph()
    node = g.get("binary:pg_config")
    from dataclasses import replace
    g = g.with_node(replace(node, attempts=(
        Attempt(command="apt-get install postgresql-dev", outcome="failed", cycle=3),)))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states={})
    assert "why " in out                              # runtime-discovered -> must justify itself
    assert "tried " in out
    assert "postgresql-dev" in out                    # the ANTI-THRASH field


def test_collect_weight_is_an_estimate_from_the_file(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_db.py").write_text(
        "".join(f"def test_{i}():\n    pass\n" for i in range(200)))
    out = render_graph_context(_pg_graph(), _Result(ok=True), [_collect_cause("psycopg2")],
                               prev_states={}, repo_path=str(tmp_path))
    assert "~200 tests hidden, est." in out


def test_pip_closure_is_summarized_not_enumerated():
    g = _pg_graph()
    for i in range(37):
        g = g.with_node(_pkg(f"dep{i}", state=State.SATISFIED))
        g = g.with_edge(Edge(src="pkg:psycopg2==2.9.12", dst=f"pkg:dep{i}==1.0",
                             relation=EdgeType.REQUIRES, origin="resolver"))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states={})
    assert "37 transitive pip deps" in out
    assert "pkg:dep0==1.0" not in out                 # never enumerated


def test_a_MISSING_pip_dep_is_promoted_out_of_the_closure_summary():
    g = _pg_graph()
    g = g.with_node(_pkg("zipp", state=State.MISSING))
    g = g.with_edge(Edge(src="pkg:psycopg2==2.9.12", dst="pkg:zipp==1.0",
                         relation=EdgeType.REQUIRES, origin="resolver"))
    out = render_graph_context(g, _Result(ok=True), [_collect_cause("psycopg2")], prev_states={})
    assert "pkg:zipp==1.0" in out                     # promoted -- it is actionable
    assert "★ pkg:zipp==1.0" in out


def test_implausible_frontier_warns_instead_of_dumping_records():
    g = DepGraph()
    causes = []
    for i in range(30):
        g = g.with_node(Node(id=f"pkg:p{i}==1.0", type=NodeType.PACKAGE, name=f"p{i}",
                             layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                             version="1.0", state=State.MISSING,
                             check_command=f"python -c 'import p{i}'",
                             chosen_fix=f"pip install p{i}"))
        causes.append(_collect_cause(f"p{i}"))
    out = render_graph_context(g, _Result(ok=True), causes, prev_states={})
    assert "ACTIONABLE" in out and "implausible" in out.lower()
    assert out.count("★ pkg:p") <= 5                 # 5 largest, NOT 30 records
    assert "treat the graph as unreliable" in out.lower()


def test_empty_graph_and_no_causes_renders_nothing():
    assert render_graph_context(DepGraph(), _Result(ok=True), [], prev_states={}) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_graph_context.py -v -k render`
Expected: FAIL — `ImportError: cannot import name 'render_graph_context'`.

- [ ] **Step 3: Implement the renderer**

Append to `src/python_deps/depgraph/graph_context.py`:

```python
_MAX_ERROR_NODES = 3          # a SUBGRAPH is expensive (two edge lists); a record is cheap
_IMPLAUSIBLE_FRONTIER = 15    # more ACTIONABLE nodes than this means OUR model is wrong
_FRONTIER_SAMPLE = 5


def _fmt_state(node: Node) -> str:
    if node.state is State.SATISFIED:
        return f"check passed: {node.check_command}" if node.check_command else "[SATISFIED]"
    if node.state is State.UNKNOWN:
        return "[UNKNOWN — no check command; state unverified]"
    return "[MISSING]"


def _anchor_for_cause(graph: DepGraph, cause) -> Node | None:
    """The graph node a pytest cause names. Matches a quoted module name against Package
    nodes by canonical name (the same normalized-name match runtime_ingest._find_existing_node
    uses -- do not reinvent it)."""
    import re as _re
    from python_deps.depgraph.naming import normalize_package_name
    m = _re.search(r"['\"]([\w.\-]+)['\"]", cause.detail or "")
    if not m:
        return None
    wanted = normalize_package_name(m.group(1).split(".", 1)[0])
    for n in graph.nodes:
        if n.type is NodeType.PACKAGE and normalize_package_name(n.name) == wanted:
            return n
    return None


def _weight(cause, repo_path: str | None) -> tuple[int, bool]:
    """(tests blocked, is_estimate). A collect-phase count is MODULES, not tests (§4.2)."""
    if cause.phase == "collect":
        est = tests_hidden(repo_path, cause.module)
        if est is not None:
            return est, True
    return cause.count, False


def _down_edges(graph: DepGraph, node: Node, target_env):
    """Prerequisite edges, with the pip closure collapsed. Returns (lines, followed_nodes)."""
    closure_sat, lines, follow = 0, [], []
    for e in graph.edges:
        if e.src != node.id:
            continue
        dst = graph.get(e.dst)
        if dst is None:
            continue
        if e.relation is EdgeType.CONFLICTS_WITH:
            lines.append(f"    {node.id}  --conflicts-->  {dst.id}  [BLOCKED]      ✖")
            continue
        if not blocks(e, target_env):
            continue
        # Package -> Package is the lockfile closure: pip re-derives it at install time, and
        # enumerating hundreds of satisfied nodes buries the handful that matter (§3.2).
        if node.type is NodeType.PACKAGE and dst.type is NodeType.PACKAGE:
            if dst.state is State.SATISFIED:
                closure_sat += 1
                continue                      # a MISSING member falls through and is PROMOTED
        mark = "  ★" if verdict(graph, dst, target_env) == ACTIONABLE else ""
        lines.append(f"    {node.id}  --requires-->  {dst.id}  {_fmt_state(dst)}{mark}")
        follow.append(dst)
    if closure_sat:
        lines.append(f"    {node.id}  --requires-->  ({closure_sat} transitive pip deps)"
                     f"  [{closure_sat} SATISFIED]")
    return lines, follow


def _up_edges(graph: DepGraph, node: Node, weight: int, est: bool) -> list[str]:
    out = []
    for e in graph.edges:
        if e.dst != node.id or e.relation is not EdgeType.REQUIRES:
            continue
        src = graph.get(e.src)
        if src is None:
            continue
        state = "" if src.type is NodeType.TEST else f"  {_fmt_state(src)}"
        out.append(f"    {src.id}  --requires-->  {node.id}{state}")
    tag = f"(~{weight} tests hidden, est.)" if est else f"({weight} tests)"
    out.append(f"    → blocks {tag}")
    return out


def _record(graph: DepGraph, node: Node, target_env, blocked_by_us: list[str]) -> list[str]:
    """A record goes ONLY where a DECISION is possible. Fields self-select on content."""
    from python_deps.depgraph.advise import _conflict_note
    v = verdict(graph, node, target_env)
    if v == BLOCKED:
        out = [f"✖ {node.id}    MISSING — CANNOT INSTALL"]
        note = _conflict_note(graph, node)
        if note:
            out.append(f"    conflict {note}")
        out.append("    note     no `pip install` fixes this — one of the two must change version")
    else:
        out = [f"★ {node.id}    {node.state.value.upper()}"]
        if node.check_command:
            out.append(f"    check    {node.check_command}")
        fix = node.chosen_fix or (node.fix_candidates[0] if node.fix_candidates else None)
        if fix:
            out.append(f"    fix      {fix}")
        for alt in node.fix_candidates[1:]:
            out.append(f"             alt: {alt}")
        # `why`/`source` only when RUNTIME-discovered: a Debian build-deps-table node need not
        # justify itself; one we appended from a log line MUST (it is how the agent audits us).
        if node.discovered_by is DiscoveredBy.RUNTIME:
            if node.evidence:
                out.append(f"    why      {node.evidence}")
            src = node.provenance or "runtime discovery"
            out.append(f"    source   {src}")
    # The ANTI-THRASH field: agents re-retry disproven fixes because their memory is lossy prose.
    for a in node.attempts:
        out.append(f"    tried    turn {a.cycle}: {a.command} → {a.outcome.upper()}")
    if len(blocked_by_us) > 1:
        out.append(f"    blocks   {' · '.join(blocked_by_us)}")
    return out


def render_graph_context(graph: DepGraph, result, causes, prev_states,
                         repo_path: str | None = None, target_env: dict | None = None) -> str:
    """The GRAPH CONTEXT block (spec §6). Pure — every state was certified by loop.py:196."""
    sub, records, notes = [], {}, []
    blocked_by: dict[str, list[str]] = {}

    # --- error nodes ---------------------------------------------------------
    # 🔴 TWO SEPARATE PASSES, and they must NOT share a cap.
    #   * SUBGRAPHS are expensive (two edge lists each) -> capped at _MAX_ERROR_NODES.
    #   * RECORDS are cheap, and ACTIONABLE roots are INDEPENDENT -> the agent should batch
    #     ALL of them in one patch, and every hidden root costs a full container rebuild (§6.4).
    # Collecting records only from the CAPPED anchors would transitively cap records too,
    # silently reintroducing the very top-N truncation §6.4 exists to forbid.
    anchors: list[tuple[Node, int, bool, str]] = []      # -> SUBGRAPH (capped)
    all_anchors: list[Node] = []                          # -> RECORDS (uncapped)

    if result is not None and not result.ok and result.failing_command:
        from python_deps.depgraph.graph_enrich import owner_node_for_command
        owner = owner_node_for_command(graph, result.failing_command)
        n = graph.get(owner) if owner else None
        if n is not None:
            anchors.append((n, 0, False, f"BUILD FAILED  {result.failing_command}"))
            all_anchors.append(n)

    ranked = sorted(causes, key=lambda c: _weight(c, repo_path)[0], reverse=True)
    seen: set[str] = set()
    for c in ranked:
        if c.phase in ("call", "teardown"):        # §4.3 — structurally NOT an env failure
            notes.append(f"NOT AN ENVIRONMENT FAILURE\n    {c.exc}: {c.detail}  "
                         f"[{c.phase}]  ({c.count} tests) — test body. No env fix exists.")
            continue
        node = _anchor_for_cause(graph, c)
        if node is None:
            notes.append(f"NO GRAPH EXPLANATION\n    {c.exc}: {c.detail}  [{c.phase}] — "
                         f"not in the model. Explore.")
            continue
        if node.id in seen:
            continue
        seen.add(node.id)
        all_anchors.append(node)                   # EVERY anchor feeds records — no cap here
        w, est = _weight(c, repo_path)
        if len(anchors) < _MAX_ERROR_NODES:        # only the top-N get a rendered SUBGRAPH
            anchors.append((node, w, est, f"ERROR NODE  {node.id}   {_fmt_state(node)}\n"
                                          f"  ← {c.exc}: {c.detail}   [{c.phase}]"))

    # --- records: from ALL anchors, uncapped (§6.4) --------------------------
    for node in all_anchors:
        _down, follow = _down_edges(graph, node, target_env)
        for cand in [node, *follow]:
            if verdict(graph, cand, target_env) in (ACTIONABLE, BLOCKED):
                records.setdefault(cand.id, cand)
                blocked_by.setdefault(cand.id, []).append(node.id)

    # --- subgraph: only the top-N anchors ------------------------------------
    for node, w, est, header in anchors:
        sub.append(header)
        down, _follow = _down_edges(graph, node, target_env)
        if down:
            sub.append("\n  REQUIRES — what it needs (the fix is in here)")
            sub.extend(down)
        sub.append("\n  REQUIRED BY — what breaks because of it (the impact)")
        sub.extend(_up_edges(graph, node, w, est))
        sub.append("")
    dropped = len(all_anchors) - len(anchors)
    if dropped > 0:                                # NEVER silently drop (§6.6)
        sub.append(f"  + {dropped} more failure class(es); their roots appear in the records below.")

    if not sub and not notes:
        return ""

    # --- frontier health (§6.4.1) -------------------------------------------
    actionable = [n for n in graph.nodes
                  if n.state is not State.SATISFIED
                  and verdict(graph, n, target_env) == ACTIONABLE]
    head = ["GRAPH CONTEXT — certified against the container your script just built", ""]
    if len(actionable) > _IMPLAUSIBLE_FRONTIER:
        # ONE line per sentence — the test asserts on substrings, and wrapping a phrase across
        # two list entries puts a "\n" in the middle of it.
        head += [
            f"⚠ {len(actionable)} nodes are ACTIONABLE. That is implausible — independent roots"
            " do not arrive in bulk.",
            "  A shared prerequisite is almost certainly MISSING FROM THE MODEL"
            " (check the runtime/venv tier).",
            f"  Showing the {_FRONTIER_SAMPLE} largest; treat the graph as unreliable this turn.",
            "",
        ]
        keep = {n.id for n in actionable[:_FRONTIER_SAMPLE]}
        records = {k: v for k, v in records.items() if k in keep}

    body = ["SUBGRAPH  (edges around this turn's failures; [state] inline)", ""] + sub
    body += ["  ★ actionable — nothing missing beneath it",
             "  ✖ blocked — no install will work", "", "─" * 74, ""]
    for nid, node in records.items():
        body += _record(graph, node, target_env, blocked_by.get(nid, [])) + [""]
    body += notes

    if result is not None and not result.ok:
        body += ["", "TESTS DID NOT RUN — the build failed. No pytest signal this turn."]

    delta = []
    for nid, was in (prev_states or {}).items():
        now = graph.get(nid)
        if now is not None and now.state is not was:
            delta.append(f"    {nid}   {was.value.upper()} → {now.state.value.upper()}")
    if delta:
        body += ["", "SINCE YOUR LAST EDIT"] + delta

    return "\n".join(head + body).rstrip() + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/depgraph/test_graph_context.py -v`
Expected: PASS (all 36).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/graph_context.py tests/depgraph/test_graph_context.py
git commit -m "feat(depgraph): render_graph_context — subgraph edge-list + per-node records

A DAG serializes natively as EDGES, not as a tree: pg_config required by both psycopg2 and
asyncpg is two lines converging on one node, so THE COLLAPSE IS A VISIBLE FACT OF THE STRUCTURE.
REQUIRES (cause) and REQUIRED BY (impact) stay separate. Records go only where a DECISION is
possible; no top-N cap (actionable roots are independent -> batch them). Fields self-select."
```

---

### Task 6: Wire enrich into the react loop

**Why:** `ingest_runtime_failures` (`runtime_ingest.py:165`) already does the whole enrich job — deterministic classifier, append-if-new / annotate-if-known, draw the edge, idempotent, ~15 tests, and it **already ships in the v3 arm** (`orchestrator.py:197-209`). It has simply never been called from `react_repair/`. Also: `certify` runs *before* enrich (`loop.py:196`), so anything we append lands `UNKNOWN` — it needs a narrow second certify over just the new ids.

**Files:**
- Modify: `src/python_deps/depgraph/graph_enrich.py`
- Test: `tests/depgraph/test_graph_enrich.py`

**Interfaces:**
- Consumes: `owner_node_for_command` (Task 2); `runtime_ingest.ingest_runtime_failures` (`runtime_ingest.py:165`); `diagnose.RepoContext` (`diagnose.py:47-65`, all fields defaulted → `RepoContext()` works); `diagnose.make_diagnostic_classifier` (**`diagnose.py:257`**); `certify.certify` (`certify.py:47`).
- Produces:
  - `enrich(graph, result, causes, ctx) -> tuple[DepGraph, list[str]]` — returns `(new_graph, new_node_ids)`.
  - `certify_only(graph, node_ids, executor, cycle=0) -> DepGraph`.

> 🔴 **Task 1B IS A HARD PREREQUISITE.** Until `classify_tool_error` recognises
> `<tool> executable not found`, the `pg_config` fixture below classifies as `AMBIGUOUS` with
> `discovery=None`, `enrich` appends **nothing**, and
> `test_build_failure_anchors_the_discovery_at_the_OWNER_not_the_test_node` fails. Do not start this
> task until Task 1B is green.

- [ ] **Step 1: Write the failing test**

Append to `tests/depgraph/test_graph_enrich.py`:

```python
from python_deps.depgraph.diagnose import RepoContext
from python_deps.depgraph.graph_enrich import certify_only, enrich
from python_deps.depgraph.schema import Edge, EdgeType


class _Result:
    def __init__(self, ok=True, failing_command=None, output=""):
        self.ok, self.failing_command, self.output = ok, failing_command, output


class _FakeExec:
    """Minimal Executor. NOTE the real CommandResult fields (executor.py:21-30) are
    `command, returncode, stdout, stderr` — there is NO `rc` field. `certify()` reads
    `.ok` (a property: returncode == 0) and `.stderr`."""
    def __init__(self, returncode=0):
        self.returncode, self.seen = returncode, []

    def run(self, command, **_kw):
        self.seen.append(command)
        from python_deps.depgraph.executor import CommandResult
        return CommandResult(command, self.returncode, "", "" if self.returncode == 0 else "boom")


def test_build_failure_anchors_the_discovery_at_the_OWNER_not_the_test_node():
    """The whole point of owner_node_for_command. Without it this edge would be
    test:repo_tests_pass -> binary:pg_config -- a flat star with no depth."""
    g = _graph()
    result = _Result(ok=False, failing_command="pip install psycopg2==2.9.12",
                     output="Error: pg_config executable not found")
    new, new_ids = enrich(g, result, causes=[], ctx=RepoContext())
    owners = {e.src for e in new.edges
              if e.relation is EdgeType.REQUIRES and "pg_config" in e.dst}
    assert owners == {"pkg:psycopg2==2.9.12"}
    assert TEST_NODE_ID not in owners
    assert any("pg_config" in nid for nid in new_ids)


def test_a_call_phase_cause_never_mutates_the_graph():
    """§4.3 — 'the test's own code decided it was wrong' IS the line between no-env-fix
    and env-fix. An AssertionError must never become a node, however its message reads."""
    from src.react_repair.pytest_summary import Cause
    cause = Cause(exc="AssertionError", detail="No module named 'ghost'", count=3,
                  outcome="FAILED", module="tests/t.py", phase="call")
    g = _graph()
    new, new_ids = enrich(g, _Result(ok=True), causes=[cause], ctx=RepoContext())
    assert new_ids == []
    assert len(new.nodes) == len(g.nodes)


def test_a_collect_phase_cause_may_append_a_node():
    from src.react_repair.pytest_summary import Cause
    cause = Cause(exc="ModuleNotFoundError", detail="No module named 'patchright'", count=1,
                  outcome="ERROR", module="tests/t.py", phase="collect")
    new, new_ids = enrich(_graph(), _Result(ok=True), causes=[cause], ctx=RepoContext())
    assert any("patchright" in nid for nid in new_ids)


def test_enrich_is_idempotent_across_turns():
    """The script re-runs from base every turn, so the SAME failure recurs. Enriching twice
    must not duplicate nodes or edges."""
    g = _graph()
    result = _Result(ok=False, failing_command="pip install psycopg2==2.9.12",
                     output="Error: pg_config executable not found")
    once, _ = enrich(g, result, [], RepoContext())
    twice, ids2 = enrich(once, result, [], RepoContext())
    assert len(twice.nodes) == len(once.nodes)
    assert len(twice.edges) == len(once.edges)
    assert ids2 == []                      # nothing NEW the second time


def test_enrich_never_raises_on_garbage_output():
    new, ids = enrich(_graph(), _Result(ok=False, failing_command="pip install x",
                                        output="\x00\xff not a real error"),
                      [], RepoContext())
    assert isinstance(ids, list)


def test_certify_only_touches_just_the_named_nodes():
    g = _graph().with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RUNTIME,
                                state=State.UNKNOWN, check_command="command -v pg_config"))
    ex = _FakeExec(returncode=0)
    new = certify_only(g, ["binary:pg_config"], ex)
    assert new.get("binary:pg_config").state is State.SATISFIED
    assert ex.seen == ["command -v pg_config"]          # NOT every node in the graph


def test_certify_only_with_no_new_ids_is_a_no_op():
    ex = _FakeExec()
    g = _graph()
    assert certify_only(g, [], ex) is g
    assert ex.seen == []
```

Also update the imports at the top of `tests/depgraph/test_graph_enrich.py`. `Cause` lives under
`src/react_repair/`, which needs the **repo root** on `sys.path`, not just `src/`:

```python
_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)
from src.react_repair.pytest_summary import Cause
```

and delete the two function-local `from src.react_repair.pytest_summary import Cause` lines in the
tests above.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_graph_enrich.py -v -k "enrich or certify_only"`
Expected: FAIL — `ImportError: cannot import name 'enrich'`.

- [ ] **Step 3: Implement**

Append to `src/python_deps/depgraph/graph_enrich.py`:

```python
from python_deps.depgraph.certify import certify
from python_deps.depgraph.diagnose import RepoContext, make_diagnostic_classifier
from python_deps.depgraph.runtime_ingest import ingest_runtime_failures

# Only these pytest phases may touch the graph (spec §4.3). "The test's own code decided it
# was wrong" IS the line between "no env fix exists" and "an env fix exists": a fixture raising
# ConnectionRefused is a Service node; an AssertionError in a test body is NEVER a node,
# however its message reads. This is structural — not an LLM judgement call.
_ENV_PHASES = frozenset({"collect", "setup"})


def enrich(graph: DepGraph, result, causes, ctx: RepoContext) -> tuple[DepGraph, list[str]]:
    """Append/annotate nodes from this turn's observations. Returns (graph, new_node_ids).

    Two streams, and they NEVER overlap: loop.py:202 runs pytest only when the build is green,
    so a turn is either build-stream (causes empty) or pytest-stream.

      * build stdout  -> owner is EXACT (owner_node_for_command) -> this is where DEPTH comes from
      * pytest output -> owner is TEST_NODE_ID, which is CORRECT: a test-file import genuinely
                         IS a direct dependency of the test goal. This is where BREADTH comes from.

    The heavy lifting is `ingest_runtime_failures` — already idempotent, already never-raises,
    already shipping in the v3 arm. We only supply the observations and the owner.
    """
    before = {n.id for n in graph.nodes}
    new = graph
    classifier = make_diagnostic_classifier(ctx)

    if result is not None and not result.ok and result.failing_command:
        owner = owner_node_for_command(new, result.failing_command)
        new, _ = ingest_runtime_failures(
            new, [(result.failing_command, result.output or "")],
            classifiers=[classifier], owner_node_id=owner,
        )

    obs = [("pytest", f"{c.exc}: {c.detail}") for c in (causes or [])
           if getattr(c, "phase", "call") in _ENV_PHASES]
    if obs:
        new, _ = ingest_runtime_failures(new, obs, classifiers=[classifier])

    return new, [n.id for n in new.nodes if n.id not in before]


def certify_only(graph: DepGraph, node_ids, executor, cycle: int = 0) -> DepGraph:
    """Certify JUST the named nodes against the live container.

    `certify` runs BEFORE enrich (loop.py:196), so anything we appended has never been checked
    and would land UNKNOWN — an untested check_command and an unverified fix. This is the narrow
    second pass that keeps "the agent is never shown a claim we have not verified" honest.
    Cost is O(new_ids), which is normally zero and occasionally three.
    """
    new = graph
    for node_id in node_ids or ():
        if new.get(node_id) is not None:
            new = certify(new, node_id, executor, cycle=cycle)
    return new
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/depgraph/test_graph_enrich.py -v`
Expected: PASS (17 tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/graph_enrich.py tests/depgraph/test_graph_enrich.py
git commit -m "feat(depgraph): enrich() + certify_only() — wire observation ingest for the react arm

enrich reuses ingest_runtime_failures (already idempotent, already never-raises, already
shipping in the v3 arm) and supplies the one thing it never had: the OWNER. Gates on the pytest
PHASE — a call-phase failure never mutates the graph. certify_only closes the ordering hole:
certify runs BEFORE enrich, so appended nodes would otherwise land UNKNOWN."
```

---

### Task 7: Discovery expansion — per-node bodies + `expand_discovery`

**Why:** Each turn is a **full container rebuild**. A serial discovery chain (`turn 2: psycopg2 fails → turn 3: learn about pg_config`) costs one rebuild per hop. Resolving a discovered node's system-tier prerequisites *at discovery time* collapses that chain. `build_dep_prior` is **already per-package** (`build_deps.py:167`); only its caller `seed_build_deps` (`:286`, loop body `:307-355`) is graph-level.

**Files:**
- Modify: `src/python_deps/depgraph/build_deps.py:286-357`
- Create: `src/python_deps/depgraph/discovery_expand.py`
- Test: `tests/depgraph/test_discovery_expand.py`

**Interfaces:**
- Consumes: `build_deps.build_dep_prior`, `os_resolver.capability_id`.
- Produces:
  - `build_deps.seed_build_deps_for(graph, pkg, executor) -> DepGraph` (extracted; `seed_build_deps` becomes its loop).
  - `discovery_expand.expand_discovery(graph, node_ids, executor, expanded=None) -> tuple[DepGraph, set[str]]`.

- [ ] **Step 1: Refactor `seed_build_deps` — extract the loop body (behaviour-preserving)**

In `src/python_deps/depgraph/build_deps.py`, replace `seed_build_deps`'s body. The per-package
code is the **existing loop body verbatim** — do not rewrite it, move it:

```python
def _eligible_for_build_deps(graph: DepGraph):
    """Source-built packages with a version — the exact filter the loop used inline."""
    return [n for n in graph.nodes
            if n.type is NodeType.PACKAGE and n.version and n.build_from_source is not False]


def seed_build_deps_for(graph: DepGraph, pkg: Node, executor: Executor) -> tuple[DepGraph, int, int, int]:
    """Seed ONE package's build-time prior. The verbatim body of seed_build_deps' loop.

    Returns (graph, pkgs_delta, cap_nodes_delta, aptdep_nodes_delta).

    🔴 THE COUNTERS ARE NOT DECORATION. `tests/depgraph/test_build_deps.py:307-316`
    (`test_seed_build_deps_logs_aggregate`) asserts the log line contains `pkgs=1`,
    `cap_nodes=1` AND `aptdep_nodes=2`. Drop them and that test fails. Note `pkgs` counts only
    packages with a NON-EMPTY plan (the original increments it AFTER the early-continue), while
    cap/aptdep count nodes actually INSERTED.

    Extracted so the react arm can expand a single runtime-discovered node without re-running
    the whole graph-level pass (which would re-hit the network for all ~200 seeded packages).
    """
    new = graph
    cap_nodes = aptdep_nodes = 0
    pc_id = capability_id(_PKG_CONFIG_NEED)

    # B3: baseline pkg-config for EVERY source-built package (Debian omits it; slim images lack it).
    if new.get(pc_id) is None:
        new = new.with_node(_capability_node(_PKG_CONFIG_NEED, executor))
        cap_nodes += 1
    new = new.with_edge(
        Edge(src=pkg.id, dst=pc_id, relation=EdgeType.REQUIRES, origin="resolver")
    )

    plan = build_dep_prior(pkg.name, pkg.version, executor)
    if not (plan.capability_needs or plan.apt_directives):
        return new, 0, cap_nodes, 0          # pkgs is NOT incremented — matches the original

    env = build_env_for(pkg.name)
    if env:
        current = new.get(pkg.id)
        new = new.with_node(replace(current, data={**current.data, "build_env": env}))

    for need in plan.capability_needs:
        node_id = capability_id(need)
        if new.get(node_id) is None:
            new = new.with_node(_capability_node(need, executor))
            cap_nodes += 1
        new = new.with_edge(
            Edge(src=pkg.id, dst=node_id, relation=EdgeType.REQUIRES, origin="resolver")
        )

    for name in plan.apt_directives:
        node_id = apt_build_id(name)
        if new.get(node_id) is None:
            new = new.with_node(_apt_build_node(name))
            aptdep_nodes += 1
        new = new.with_edge(
            Edge(src=pkg.id, dst=node_id, relation=EdgeType.REQUIRES, origin="resolver")
        )
    return new, 1, cap_nodes, aptdep_nodes


def seed_build_deps(graph: DepGraph, executor: Executor) -> DepGraph:
    """Graph-level pass — now just the loop over seed_build_deps_for. Behaviour UNCHANGED,
    including the three-counter log line the existing test asserts on."""
    new = graph
    pkgs = cap_nodes = aptdep_nodes = 0
    for pkg in _eligible_for_build_deps(graph):
        new, dp, dc, da = seed_build_deps_for(new, pkg, executor)
        pkgs += dp
        cap_nodes += dc
        aptdep_nodes += da
    logger.info(
        "seed_build_deps: pkgs=%d cap_nodes=%d aptdep_nodes=%d",
        pkgs, cap_nodes, aptdep_nodes,
    )
    return new
```

> **`discovery_expand` must unpack the 4-tuple:**
> `new, _dp, _dc, _da = seed_build_deps_for(new, node, executor)` (Task 7 Step 5 shows this).
> If you would rather keep `seed_build_deps_for` returning a bare `DepGraph`, you must instead
> recompute the three counters inside `seed_build_deps` — but do **not** simply drop them.

- [ ] **Step 2: Prove the refactor is behaviour-preserving**

Run: `python -m pytest tests/depgraph/test_build_deps.py -v`
Expected: PASS — **every** pre-existing test, unchanged. Pay particular attention to
`test_seed_build_deps_logs_aggregate` (`:307-316`), which asserts the log line still contains
`pkgs=`, `cap_nodes=` **and** `aptdep_nodes=`. If it fails, you dropped a counter.

Then the full construction suite:

Run: `python -m pytest tests/depgraph/ -q`
Expected: PASS, same count as before the refactor.

> If any test fails, the extraction changed behaviour. Diff the moved code against
> `git show HEAD:src/python_deps/depgraph/build_deps.py` lines 307–355 and make it verbatim.

- [ ] **Step 3: Write the failing test for `expand_discovery`**

Create `tests/depgraph/test_discovery_expand.py`:

```python
"""Tests for discovery_expand (no network — build_dep_prior is stubbed)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import python_deps.depgraph.discovery_expand as dx
from python_deps.depgraph.ids import package_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, EdgeType, Layer, Node, NodeType, State,
)


class _Exec:
    """CommandResult fields are (command, returncode, stdout, stderr) — there is NO `rc`."""
    def run(self, command, **_kw):
        from python_deps.depgraph.executor import CommandResult
        return CommandResult(command, 0, "", "")


def _pkg(name, version, discovered_by=DiscoveredBy.RUNTIME) -> Node:
    return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=discovered_by, version=version,
                state=State.MISSING)


def test_expands_a_versioned_discovery_through_the_real_oracle(monkeypatch):
    """The oracle (build_dep_prior, via seed_build_deps_for) is stubbed — we assert that
    expand_discovery CALLS it with the right node and grafts what it returns."""
    from python_deps.depgraph.schema import Edge
    calls = []

    def _fake_seed_for(graph, pkg, executor):
        calls.append(pkg.name)
        g = (graph
             .with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                             layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                             state=State.MISSING))
             .with_edge(Edge(src=pkg.id, dst="binary:pg_config",
                             relation=EdgeType.REQUIRES, origin="resolver")))
        return g, 1, 1, 0                       # (graph, pkgs, cap_nodes, aptdep_nodes)

    monkeypatch.setattr(dx, "seed_build_deps_for", _fake_seed_for)
    g = DepGraph().with_node(_pkg("psycopg2", "2.9.12"))
    new, expanded = dx.expand_discovery(g, ["pkg:psycopg2==2.9.12"], _Exec())
    assert calls == ["psycopg2"]
    assert expanded == {"pkg:psycopg2==2.9.12"}
    assert new.get("binary:pg_config") is not None
    assert any(e.src == "pkg:psycopg2==2.9.12" and e.dst == "binary:pg_config"
               for e in new.edges)


def test_a_versionless_discovery_is_NOT_expanded(monkeypatch):
    """build_dep_prior needs a version (build_deps.py:308 skips versionless packages).
    Without one we mark it unresolved and expand NOTHING — expansion propagates a bad
    anchor's wrongness through a whole fabricated subtree (the 6->0 property)."""
    called = []
    monkeypatch.setattr(dx, "seed_build_deps_for",
                        lambda g, p, e: (called.append(p.name), (g, 1, 0, 0))[1])
    g = DepGraph().with_node(_pkg("patchright", None))
    new, expanded = dx.expand_discovery(g, ["pkg:patchright"], _Exec())
    assert called == []
    assert expanded == set()


def test_a_non_package_node_is_not_expanded(monkeypatch):
    called = []
    monkeypatch.setattr(dx, "seed_build_deps_for",
                        lambda g, p, e: (called.append(p.name), (g, 1, 0, 0))[1])
    g = DepGraph().with_node(Node(id="binary:pg_config", type=NodeType.TOOL, name="pg_config",
                                  layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RUNTIME,
                                  state=State.MISSING))
    _new, expanded = dx.expand_discovery(g, ["binary:pg_config"], _Exec())
    assert called == []
    assert expanded == set()


def test_a_node_is_expanded_at_most_ONCE_across_turns(monkeypatch):
    """The script re-runs from base every turn, so the same failure recurs. Without the
    `expanded` set we would re-hit the network with build_dep_prior every single turn."""
    called = []
    monkeypatch.setattr(dx, "seed_build_deps_for",
                        lambda g, p, e: (called.append(p.name), (g, 1, 0, 0))[1])
    g = DepGraph().with_node(_pkg("psycopg2", "2.9.12"))
    _g1, exp1 = dx.expand_discovery(g, ["pkg:psycopg2==2.9.12"], _Exec())
    _g2, exp2 = dx.expand_discovery(g, ["pkg:psycopg2==2.9.12"], _Exec(), expanded=exp1)
    assert called == ["psycopg2"]                     # ONCE, not twice
    assert exp2 == exp1


def test_expansion_never_raises(monkeypatch):
    def _boom(graph, pkg, executor):
        raise RuntimeError("network down")
    monkeypatch.setattr(dx, "seed_build_deps_for", _boom)
    g = DepGraph().with_node(_pkg("psycopg2", "2.9.12"))
    new, expanded = dx.expand_discovery(g, ["pkg:psycopg2==2.9.12"], _Exec())
    assert new is not None                            # the run must never break (spec §11)
```

- [ ] **Step 4: Run to verify it fails**

Run: `python -m pytest tests/depgraph/test_discovery_expand.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.discovery_expand'`.

- [ ] **Step 5: Implement**

Create `src/python_deps/depgraph/discovery_expand.py`:

```python
"""Expand a runtime discovery through the SAME oracles that built the graph (spec §7.2).

The governing principle, and the line this project has already paid to learn: the deleted
import->dist identity fallback took wrong-guesses from 6 to 0 by replacing INFERENCE with a
typed `unresolved`. We do NOT reintroduce guessing.

    A runtime discovery is a new DECLARED ROOT. Feed it back through construction.

We never guess what a discovered node needs — we RESOLVE it, with `build_dep_prior` (the
Debian build-deps table + PEP 725 + curated priors). That is a resolver, not a guesser.

WHY it pays: every turn is a FULL CONTAINER REBUILD. A serial discovery chain (turn 2:
psycopg2 fails -> turn 3: learn pg_config) costs one rebuild per hop. Resolving the
prerequisites at discovery time collapses the chain into one turn.
"""
from __future__ import annotations

import logging

from python_deps.depgraph.build_deps import seed_build_deps_for
from python_deps.depgraph.schema import DepGraph, NodeType

logger = logging.getLogger(__name__)


def expand_discovery(graph: DepGraph, node_ids, executor, expanded: set[str] | None = None):
    """Resolve the system-tier prerequisites of newly discovered PACKAGE nodes.

    Returns (new_graph, expanded_ids). Pass the previous `expanded` back in each turn: the
    script re-runs from base every turn so the same failure recurs, and without it we would
    re-hit the network with build_dep_prior on every turn for the same node.

    GATED: only a package with a VERSION is expanded. `build_dep_prior` needs one
    (build_deps.py:308 skips versionless packages), and expanding an unresolved name would
    hang a whole fabricated subtree off a bad anchor. No version -> expand NOTHING.
    """
    done = set(expanded or ())
    new = graph
    for node_id in node_ids or ():
        if node_id in done:
            continue
        node = new.get(node_id)
        if node is None or node.type is not NodeType.PACKAGE or not node.version:
            continue
        try:
            # seed_build_deps_for returns (graph, pkgs, cap_nodes, aptdep_nodes) — the counters
            # exist because seed_build_deps' log line is asserted on by an existing test.
            new, _dp, _dc, _da = seed_build_deps_for(new, node, executor)
            done.add(node_id)
        except Exception as exc:               # noqa: BLE001 — must never break the run
            logger.warning("expand_discovery: %s skipped: %s", node_id, exc)
    return new, done
```

- [ ] **Step 6: Run to verify it passes**

Run: `python -m pytest tests/depgraph/test_discovery_expand.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add src/python_deps/depgraph/build_deps.py src/python_deps/depgraph/discovery_expand.py tests/depgraph/test_discovery_expand.py
git commit -m "feat(depgraph): expand_discovery + per-node seed_build_deps_for

A runtime discovery is a new DECLARED ROOT — feed it back through the SAME oracles that built
the graph (build_dep_prior), never through inference. Gated on the name having a version, so a
bad anchor can't hang a fabricated subtree (preserves the identity-fallback 6->0 property).
Expanded-once set: the script re-runs from base each turn, so without it we'd re-hit the network
every turn. seed_build_deps is now just the loop over the extracted body — behaviour unchanged."
```

---

### Task 8: Wire the arm — planner seam, loop, and the G2/G3 flags

**Why:** The seam already exists and `run_react_arm` already takes a `graph_context: bool` — `entry.py` just hardcodes `ctx = None`. Two flags, not one, so **read-only graph (G2)** and **growing graph (G3)** are separate ablation rungs and a G3 lift is never misattributed to the renderer.

> 🔴 **Line numbers in this task were re-anchored on 2026-07-13 against the current tree.** `loop.py`, `entry.py`, and `planner.py` all grew from a parallel session (error histogram, per-test timeout, the self-install anti-cheat gate). **Re-verify with `grep` before editing** — do not trust any line number here blindly.

**Files:**
- Modify: `src/react_repair/planner.py` — `_render` (`:142`, direct call at `:153-156`), `_graph_text` (`:162-167`), `_messages` (`:169+`), `plan` (`:193-195`)
- Modify: `src/react_repair/loop.py` — `build_and_test` (`:257-274`), rebind sites (`:294`, `:348`, `:363`), `run_react` signature (`:249-251`)
- Modify: `src/react_repair/entry.py` — `ctx = None` (**`:193`**, not 162), `run_react_arm` (`:187-189`)
- Test: `tests/react_repair/test_loop.py`, **`tests/react_repair/test_planner.py`** ← *the plan originally missed this file; it contains the one existing test that exercises the seam*

**Interfaces:**
- Consumes: `render_graph_context` (Task 5), `enrich`/`certify_only` (Task 6), `expand_discovery` (Task 7).
- Produces: the `graph_context` callable now takes **4 args** — `(graph, result, causes, prev_states)`.

#### 🔴 Four traps, all confirmed against the current tree

1. **`_render` calls `self.graph_context(graph)` DIRECTLY** (`planner.py:153-156`) — it does **not** go through `_graph_text`. There are **TWO** call sites to widen, not one.
2. **`tests/react_repair/test_planner.py:93`** does `graph_context=lambda g: "libpq: MISSING"` — a **1-arg** lambda. The moment the seam takes 4 args this raises `TypeError: <lambda>() takes 1 positional argument but 4 were given`. **It must be migrated in this task.**
3. **`run_react` already has a `project_name` kwarg** (`loop.py:251`, feeding the self-install anti-cheat gate at `:320`). **Preserve it** when widening the signature.
4. **`build_and_test` is full of `log.d`/`log.trace` calls.** Step 4 below is a **surgical insert**, NOT a replace-paste. Dropping the logs breaks `test_green_first_pass_is_done` (`test_loop.py:170-172` asserts `log.count("TEST_GATE") >= 1`) and destroys the JSONL trace records other tooling reads.

- [ ] **Step 1: Migrate the existing planner test to the 4-arg seam**

In `tests/react_repair/test_planner.py:92-96`, widen the lambda and prove the extra args arrive:

```python
def test_graph_context_injected_when_provided(monkeypatch):
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    got = {}

    def _ctx(graph, result, causes, prev_states):
        got.update(result=result, causes=causes, prev_states=prev_states)
        return "libpq: MISSING"

    p = ReactPlanner(client=object(), model="m", graph_context=_ctx)
    p.plan(History(), "script", "obs", graph=object(),
           result=None, causes=[], prev_states={})
    assert "GRAPH CONTEXT" in seen["user"] and "libpq: MISSING" in seen["user"]
    assert got == {"result": None, "causes": [], "prev_states": {}}


def test_graph_context_is_absent_for_the_baseline(monkeypatch):
    """G0/G1 ablation invariant: graph_context=None -> no GRAPH CONTEXT block at all."""
    seen, fn = _capture(); monkeypatch.setattr(planner_mod, "complete_with_tools", fn)
    ReactPlanner(client=object(), model="m", graph_context=None).plan(
        History(), "script", "obs", graph=object())
    assert "GRAPH CONTEXT" not in seen["user"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/react_repair/test_planner.py -v -k graph_context`
Expected: FAIL — `TypeError: plan() got an unexpected keyword argument 'result'`.

- [ ] **Step 3: Widen BOTH planner call sites**

In `src/react_repair/planner.py`, thread the three new args through `plan` → `_messages` → both
`_render` **and** `_graph_text`. All default to `None`, so the baseline arm is untouched.

```python
    def _render(self, history, script: str, observation: str, graph,
                fail_lineno: int | None = None,
                turn: int | None = None, max_turns: int | None = None,
                rejection: str | None = None,
                result=None, causes=None, prev_states=None) -> str:
        parts = [
            ("CURRENT setup.sh (line numbers are for Edit refs and match the build failure's "
             "\"line N\" — the \"n| \" prefix is NOT part of the script):\n"
             + _numbered(script, fail_lineno)),
            "LAST RUN OBSERVATION:\n" + (observation or ""),
            render_history(history.steps),
        ]
        # Reuse _graph_text — do NOT re-implement the call here. Before this change `_render`
        # called self.graph_context(graph) directly, which is why widening _graph_text alone
        # would have silently left the blob prompt style on the old 1-arg seam.
        ctx = self._graph_text(graph, result, causes, prev_states)
        if ctx:
            parts.append("GRAPH CONTEXT (certified state):\n" + ctx)
        if rejection:
            parts.append("YOUR LAST TOOL CALL WAS REJECTED — fix it and try again: " + rejection)
        parts.append(_closing_line(turn, max_turns))
        return "\n\n".join(parts)

    def _graph_text(self, graph, result=None, causes=None, prev_states=None) -> "str | None":
        """The certified-state block for the graph variant (None for the baseline)."""
        if self.graph_context is None:
            return None
        ctx = self.graph_context(graph, result, causes or [], prev_states or {}) or ""
        return ctx if ctx.strip() else None
```

Then `_messages` — note the **default (`messages`) path** feeds `build_messages`, whose
`graph_context_text=` kwarg already exists (`message_view.py:392-395`) and takes a **rendered
string**, so `message_view` itself needs no change:

```python
    def _messages(self, history, script, observation, graph, fail_lineno, turn, max_turns,
                  rejection, rejected=None, result=None, causes=None, prev_states=None):
        if os.getenv("REACT_PROMPT_STYLE", "messages").lower() != "blob":
            return build_messages(
                history.steps, system_prompt=self.system_prompt,
                numbered_script=_numbered(script, fail_lineno),
                closing_line=_closing_line(turn, max_turns),
                graph_context_text=self._graph_text(graph, result, causes, prev_states),
                rejection=rejection, rejected=rejected)
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user",
             "content": self._render(history, script, observation, graph, fail_lineno,
                                     turn, max_turns, rejection, result, causes, prev_states)},
        ]
```

And `plan` — **keep every existing param**, append the three:

```python
    def plan(self, history, script: str, observation: str, graph, fail_lineno: int | None = None,
             turn: int | None = None, max_turns: int | None = None, rejection: str | None = None,
             rejected: "dict | None" = None,
             result=None, causes=None, prev_states=None):
        messages = self._messages(history, script, observation, graph, fail_lineno,
                                  turn, max_turns, rejection, rejected,
                                  result, causes, prev_states)
        # ...rest of plan() UNCHANGED...
```

- [ ] **Step 4: Run the planner tests**

Run: `python -m pytest tests/react_repair/test_planner.py tests/react_repair/test_message_view.py -v`
Expected: PASS — including every pre-existing test.

- [ ] **Step 5: Wire the loop — a SURGICAL INSERT into `build_and_test`**

🔴 **Do not replace `build_and_test`.** Keep every `log.d` and `log.trace` call exactly as it is
(`test_green_first_pass_is_done` asserts `log.count("TEST_GATE") >= 1`, and the `"run"`/`"test"`
trace records are consumed by downstream tooling). Add only the marked lines:

```python
    expanded: set[str] = set()                              # ADD (above build_and_test)

    def build_and_test():
        """Reset → run the WHOLE current script fresh from base → certify (install-tier) → and,
        if the build is green, run the suite once. Returns (result, graph, test|None, causes, prev)."""
        nonlocal expanded                                   # ADD
        reset()
        prev_states = {n.id: n.state for n in graph.nodes}  # ADD — for the SINCE-YOUR-LAST-EDIT delta
        log.d("RUN", f"running {len(script.splitlines())}-line build script from base")
        r = run_script(script)
        g = certify(graph)
        log.d("CERTIFY", "install-tier node states refreshed" if r.ok else f"build failed: {r.failing_command}")
        log.trace("run", script_len=len(script.splitlines()), ok=r.ok,
                  failing_command=r.failing_command, lineno=r.lineno,
                  output_tail=(r.output or "")[-500:])
        t = None
        if r.ok:
            t = run_tests()
            log.d("TEST_GATE", f"{t.passed}/{t.executed} passed → {'ok' if t.ok else 'below threshold'}")
            log.trace("test", passed=t.passed, executed=t.executed, ok=t.ok,
                      output_tail=(t.output or "")[-500:])
        causes = summarize(t.output) if (t is not None and t.output) else []   # ADD
        if enrich_fn is not None:                                              # ADD — G3 only
            g, new_ids = enrich_fn(g, r, causes)                               # ADD
            g, expanded = expand_fn(g, new_ids, expanded)                      # ADD
            g = certify_new_fn(g, new_ids)                                     # ADD
            if new_ids:                                                        # ADD
                log.d("ENRICH", f"discovered {len(new_ids)}: {', '.join(new_ids[:3])}")
        return r, g, t, causes, prev_states                                    # CHANGED (was 3-tuple)
```

`summarize` is **already imported** at `loop.py:16` — no new import needed.

Widen `run_react`'s signature, **keeping `project_name`**:

```python
def run_react(graph, *, reset, run_script, certify, exec_readonly, run_tests, planner,
              history, log, max_steps: int = 30, _initial_script: str | None = None,
              project_name: "str | None" = None,
              enrich_fn=None, expand_fn=None, certify_new_fn=None):
```

Update the **three** rebind sites (`:294`, `:348`, `:363`) to unpack five values:

```python
    result, graph, test, causes, prev_states = build_and_test()
```

and pass the new args to the planner:

```python
            thought, action, usage = planner.plan(history, script, observation, graph,
                                                  fail_lineno=result.lineno, turn=step + 1,
                                                  max_turns=max_steps, rejection=rejection,
                                                  result=result, causes=causes,
                                                  prev_states=prev_states)
```

- [ ] **Step 6: Wire `entry.py`**

Replace `entry.py:193` (`ctx = None`). Add these imports at the top:

```python
from python_deps.depgraph import repo_modules
from python_deps.depgraph.diagnose import RepoContext
from python_deps.depgraph.discovery_expand import expand_discovery
from python_deps.depgraph.graph_context import render_graph_context
from python_deps.depgraph.graph_enrich import certify_only, enrich
```

(`import os` is already there at `:7`.) Then:

```python
    # G2 = render only (frozen topology). G3 = render + observation-driven growth (§7).
    # Two flags, not one, so a G3 lift is never misattributed to the renderer.
    want_ctx = bool(graph_context) or os.getenv("REACT_GRAPH_CONTEXT") == "1"
    want_update = os.getenv("REACT_GRAPH_UPDATE") == "1"

    # RepoContext MUST come from repo_modules.top_level_names — the sys.path-accurate set.
    # diagnose.py:48-52 explicitly warns NOT to use scan.local_module_names, which is
    # deliberately over-broad (it harvests every .py stem) and makes the router give up
    # silently on azure/traitlets/jinja2. Mirrors orchestrator.py:740-751.
    repo_ctx = RepoContext(
        local_names=repo_modules.top_level_names(repo_path) if repo_path else frozenset(),
        invalid_names=frozenset(),
        collisions=repo_modules.stem_collisions(repo_path) if repo_path else {},
    )

    ctx = None
    if want_ctx:
        def ctx(graph, result, causes, prev_states):
            return render_graph_context(graph, result, causes, prev_states, repo_path=repo_path)

    enrich_fn = expand_fn = certify_new_fn = None
    if want_update:
        _ex = _ExecAdapter(sandbox.exec_readonly)      # already defined at entry.py:56-63

        def enrich_fn(g, r, causes):
            return enrich(g, r, causes, repo_ctx)

        def expand_fn(g, new_ids, already):
            return expand_discovery(g, new_ids, _ex, already)

        def certify_new_fn(g, new_ids):
            return certify_only(g, new_ids, _ex)
```

Pass `graph_context=(ctx if want_ctx else None)` to `ReactPlanner`, and thread
`enrich_fn`/`expand_fn`/`certify_new_fn` into the existing `run_react(...)` call.

- [ ] **Step 7: Write the loop-level test**

`tests/react_repair/test_loop.py` has a real fake harness — **read it first and reuse it**:
`_adapters(installed_needs, tests_need, script_box)` (`:139-156`) builds the five callables;
`_run(moves, ...)` (`:159-167`) drives `run_react`; `_ScriptedPlanner` (`:131-136`) pops a queue of
`Action`s. **There is no `_FakePlanner` and nothing records rendered prompts today** — add a minimal
recorder rather than inventing a second harness:

```python
class _RecordingPlanner(_ScriptedPlanner):
    """_ScriptedPlanner + it records what the graph_context seam was called with."""
    def __init__(self, moves, graph_context=None):
        super().__init__(moves)
        self.graph_context = graph_context
        self.calls = []

    def plan(self, history, script, observation, graph, **kw):
        if self.graph_context is not None:
            self.calls.append(self.graph_context(graph, kw.get("result"),
                                                 kw.get("causes"), kw.get("prev_states")))
        return super().plan(history, script, observation, graph, **kw)


def test_graph_context_gets_result_causes_and_prev_states_on_a_FAILED_build():
    """`result` is REQUIRED: loop.py only runs pytest on a GREEN build, so a build-fail turn has
    EMPTY causes and the only anchor is the failing command. A renderer keyed on `causes` alone
    emits nothing on exactly the turns where the install tier is broken."""
    got = {}

    def _ctx(graph, result, causes, prev_states):
        got.update(result=result, causes=causes, prev_states=prev_states)
        return "GRAPH!"

    # Drive one turn with a script that FAILS to build (use _adapters' existing failure path —
    # an unsatisfied `installed_needs` token makes run_script return ok=False).
    # Assert:
    assert got["result"].ok is False
    assert got["causes"] == []                     # pytest never ran
    assert isinstance(got["prev_states"], dict)
```

> **Implementer:** adapt the body to `_adapters`/`_run`'s actual failure path — read `:139-167`
> and drive a failing build through it. Do not hand-roll a second set of fakes.

- [ ] **Step 8: Run the full react + depgraph suites**

Run: `python -m pytest tests/react_repair/ tests/depgraph/ -q`
Expected: PASS. The baseline (G0/G1) behaviour must be **unchanged** — if any pre-existing test
changed behaviour, the ablation invariant is broken.

- [ ] **Step 9: Commit**

```bash
git add src/react_repair/planner.py src/react_repair/loop.py src/react_repair/entry.py \
        tests/react_repair/test_loop.py tests/react_repair/test_planner.py
git commit -m "feat(react): wire the graph arm — REACT_GRAPH_CONTEXT (G2) / REACT_GRAPH_UPDATE (G3)

Fills the graph_context seam that entry.py has hardcoded to None. Two flags, not one, so
read-only graph (G2) and growing graph (G3) are separate ablation rungs and a G3 lift is never
credited to the renderer. The seam takes (graph, result, causes, prev_states): \`result\` is
REQUIRED because pytest only runs on a green build, so a build-fail turn has EMPTY causes and the
only anchor is the failing command.

BOTH planner call sites widened — _render called self.graph_context(graph) directly, bypassing
_graph_text, so widening _graph_text alone would have left the blob prompt style on the old seam.
test_planner.py's 1-arg lambda migrated. build_and_test's log.d/log.trace calls preserved."
```

## Self-Review + Verification Notes

**Verified 2026-07-13 by four parallel subagents against the live tree.** Every `file:line` and
signature below was checked, not assumed. Nine real defects were found and fixed in-place; they are
listed here because each one would have cost the implementer a confusing failure.

### 🔴 Defects verification found (all now fixed in the plan)

1. **The flagship case did not classify.** `Error: pg_config executable not found` → `diagnose()`
   returns `AMBIGUOUS`/`discovery=None`, because `_TOOL_COMMAND_NOT_FOUND_RE`
   (`failure_classifier.py:246`) requires a **colon** (`<name>: not found`). Enrich would have
   appended **nothing** for the exact case the whole arm is motivated by. → **new Task 1B**.
2. **`verdict()` never checked the node's OWN state**, so a SATISFIED leaf (no missing
   prerequisites) came back `ACTIONABLE` and got a "fix this" record for something already
   installed. → `SATISFIED_OK` early return (Task 3).
3. **`_MAX_ERROR_NODES` transitively capped RECORDS**, because records were collected only from the
   capped anchor list — silently reintroducing the top-N truncation §6.4 exists to forbid. → records
   now collected from **all** anchors, subgraphs from the top-N (Task 5).
4. **`_render` calls `self.graph_context(graph)` DIRECTLY** (`planner.py:153-156`), bypassing
   `_graph_text`. Widening `_graph_text` alone would have left the `blob` prompt style on the old
   1-arg seam. → **both** call sites widened (Task 8).
5. **`tests/react_repair/test_planner.py:93`** passes a **1-arg** `graph_context` lambda — it
   `TypeError`s the moment the seam widens, and the file was not in the original task's file list.
   → migrated in Task 8 Step 1.
6. **`build_and_test`'s `log.d`/`log.trace` calls** were dropped by the original replace-paste
   snippet, which would have failed `test_green_first_pass_is_done` (asserts
   `log.count("TEST_GATE") >= 1`) and destroyed the JSONL trace. → Task 8 Step 5 is now a **surgical
   insert**.
7. **`CommandResult` has no `rc` field** — it is `(command, returncode, stdout, stderr)` with an
   `.ok` property. The `_FakeExec` would have raised `TypeError`. → fixed (Task 6).
8. **Dropping `cap_nodes`/`aptdep_nodes` from `seed_build_deps`'s log line** breaks the existing
   `test_seed_build_deps_logs_aggregate` (`test_build_deps.py:307-316`), contradicting the plan's own
   "PASS, unchanged" claim. → `seed_build_deps_for` now returns the counter deltas (Task 7).
9. **`DiscoveredBy` has no `LLM` member** (it is `CLASSIFIER`); `run_react` already carries a
   `project_name` kwarg the plan never mentioned; `make_diagnostic_classifier` is at `diagnose.py:257`;
   the second orchestrator ingest call is at `:1026`. → all corrected.

### Confirmed sound (spot-checked, no change needed)

`packaging` **is** a dependency (`requirements.txt:6`), so `blocks()`'s marker evaluation is real, not
a silent no-op. `EDGE_RULES` validates by **node type**, and every edge the plan's tests build
(Package→Tool, Package→Config, Package→Package for conflicts) is legal — none raise. `with_edge`
dedupes on `(src, dst, relation)` and `with_node` replaces by id, so `ingest_runtime_failures` is
genuinely idempotent. `normalize_package_name` is the literal PEP 503 formula, so `charset_normalizer`
really does match a `charset-normalizer` node. `graph_enrich` creates no import cycle. The pytest
banner wording is confirmed at the **pytest-source** level (`_pytest/terminal.py:1201-1213` builds
`"ERROR collecting "` and `f"ERROR at {rep.when} of "`).

### Spec coverage

§4.1/§4.2 → T1 (phase) + T4 (weight). §4.3 → T6 (`_ENV_PHASES`). §6.1–6.2 → T5. §6.3 → T3.
§6.4/§6.4.1/§6.5 → T5. §6.7 → T8. §6.8 (delta) → T5 + T8. §7.0.1 → T2 + T6. §7.1 → T6.
§7.2/§7.4 → T7. §2 (rungs) → T8. **§7.2 Mechanism 1 additionally required T1B**, which the spec did
not anticipate — the spec assumed the classifier already recognised the tool-not-found shape it uses
as its running example.

### Deferred, deliberately — recorded so they are not silently lost

- **§7.2 Mechanism 2** (`ldd_probe_for` on newly-satisfied packages). Needs a container with the
  package actually installed, so it cannot be unit-tested with a fake executor. Its loop is also
  **two-level** — one *batched* `ldd` per package, fanned out over several sonames — so it is **not**
  the same shape as `seed_build_deps`'s and the extraction is not mechanical. Follow-up VM slice.
- **§8 REPO_INTERNAL_REF → Project-node routing** — the antidote to the self-install false-green
  vector (agent runs `pip install <reponame>` from PyPI instead of `pip install -e .`). Real win,
  orthogonal to the graph arm, deserves its own plan.

### Known spec drift to fix in the spec itself

The spec's §2 rung table says `_OBS_MODE` takes `raw`/`histogram`. **`raw` does not exist.** The real
lever is `REACT_OBS_MODE` (`loop.py:62`), values `compress` (**default**) / `histogram`. So the G0
rung is `compress`, not `raw`. Correct the spec before running the ablation.
