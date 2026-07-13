# Unified Benchmark Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `bench/` package that measures agent-produced Docker environments uniformly — it **harvests** Dockerfiles the agents already wrote to disk (never imports or invokes an agent), does a fresh `docker build`, runs an identical pytest command set, and reports correctness + economy metrics.

**Architecture:** Offline harvest. Phase 1 (produce) happens inside each agent's own run and writes `Dockerfile` + `bench_meta.json` to disk. `bench` does phase 2 (`harvest -> measure`) and phase 3 (`compute_metrics`). `bench` has zero agent imports and lives on its own branch/folder, so agents stay independently pull/pushable.

**Tech Stack:** Python 3 (repo standard), `pytest`, `dataclasses`, `subprocess` + Docker CLI, JUnit XML via `xml.etree.ElementTree`.

**Spec:** `docs/superpowers/specs/2026-07-11-unified-benchmark-runner-design.md`

## Global Constraints

- Package root: `bench/` at repo root. Tests: `tests/bench/`. `bench` never imports agent code.
- Fixed in-container measurement path: **`/testbed`**. Every pytest command runs with `-w /testbed`.
- Measurement commands are **byte-identical for every agent** (spec §6).
- Test run flags (verbatim): `python -m pytest -q --continue-on-collection-errors --junit-xml=/testbed/logs/junit.xml` plus `--timeout=120 --timeout-method=signal` **only if** `pytest-timeout` imports. **Never** `-n auto`.
- Collect-clean gate: `collect_clean = collect_rc in {0, 5}`. A nonzero collect rc is **data, never a control-flow gate** — always proceed to the test run.
- `pass_rate = passed / (total - skipped)` when `total - skipped > 0`, else `0.0`.
- Headline denominator is **always `n`** (full repo set). No repo ever silently drops.
- Cost fields are `None` (never `0`) when `bench_meta.json` did not report them.
- Repo Python style: `from __future__ import annotations`, type hints, `@dataclass(frozen=True)` for data.
- Commit types: `feat:` / `test:`. Do **not** `git add -A` — add only the exact files listed (repo has unrelated unstaged WIP under `src/python_deps/depgraph/` and `service_*`).

---

### Task 1: Data schema

**Files:**
- Create: `bench/__init__.py` (empty), `bench/schema.py`, `tests/bench/__init__.py` (empty)
- Test: `tests/bench/test_schema.py`

**Interfaces:**
- Produces: `RepoSpec(full_name, repo_url, language="python")`; `HarvestedEnv(agent, repo, dockerfile, setup_scripts={}, base_image=None, status="ok", meta={})`; `MeasureRow(...)` (cost fields + node-id tuples default so tests build minimal rows).

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_schema.py
import dataclasses
import pytest
from bench.schema import RepoSpec, HarvestedEnv, MeasureRow


def test_repospec_defaults_and_frozen():
    r = RepoSpec(full_name="owner/repo", repo_url="https://github.com/owner/repo")
    assert r.language == "python"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.language = "node"  # type: ignore[misc]


def test_harvested_env_minimal():
    e = HarvestedEnv(agent="v3", repo=RepoSpec("o/r", "https://github.com/o/r"), dockerfile="FROM x")
    assert e.status == "ok" and e.setup_scripts == {} and e.meta == {}


def test_measurerow_minimal_uses_defaults():
    row = MeasureRow(agent="v3", repo="o/r", env_status="ok", build_ok=True)
    assert row.collected_node_ids == () and row.passed_node_ids == ()
    assert row.tokens_in is None and row.image_delta_mb is None
    assert row.ebsr is False and row.pass_rate == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/schema.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RepoSpec:
    full_name: str            # "owner/repo"
    repo_url: str             # https://github.com/owner/repo
    language: str = "python"


@dataclass(frozen=True)
class HarvestedEnv:
    agent: str
    repo: RepoSpec
    dockerfile: str | None            # None => no Dockerfile found (status="missing")
    setup_scripts: dict = field(default_factory=dict)   # sibling files the Dockerfile COPYs
    base_image: str | None = None
    status: str = "ok"                # "ok" | "missing"
    meta: dict = field(default_factory=dict)   # from bench_meta.json (cost keys None if absent)


@dataclass(frozen=True)
class MeasureRow:
    agent: str
    repo: str
    env_status: str                   # "ok" | "missing"
    build_ok: bool
    build_log_tail: str = ""
    collect_rc: int | None = None
    collect_clean: bool = False
    collect_errors: tuple = ()
    collected_node_ids: tuple = ()
    executed: bool = False
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    passed_node_ids: tuple = ()
    failed_node_ids: tuple = ()
    error_node_ids: tuple = ()
    ebsr: bool = False
    pass_rate: float = 0.0
    timed_out: bool = False
    image_size_mb: float | None = None
    image_delta_mb: float | None = None
    installed_pkg_count: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    llm_calls: int | None = None
    turns_used: int | None = None
    produce_s: float | None = None
    build_s: float | None = None
    test_s: float | None = None
    meta: dict = field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_schema.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/__init__.py bench/schema.py tests/bench/__init__.py tests/bench/test_schema.py
git commit -m "feat(bench): data schema (RepoSpec, HarvestedEnv, MeasureRow)"
```

---

### Task 2: Metrics — correctness gates

**Files:**
- Create: `bench/metrics.py`
- Test: `tests/bench/test_metrics_gates.py`

**Interfaces:**
- Consumes: `MeasureRow` (Task 1).
- Produces: `compute_metrics(rows: list[MeasureRow], gold: dict | None = None) -> dict` with keys `n, n_exec, n_ebsr, n_collect_clean, n_real_success, EBSR, collect_clean_rate, ESSR_all, ESSR_exec, real_success, micro, full_pass_repos, coverage`. (Gold + efficiency added in Tasks 3–4.)

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_metrics_gates.py
from bench.schema import MeasureRow
from bench.metrics import compute_metrics


def _row(**kw):
    base = dict(agent="a", repo="r", env_status="ok", build_ok=True, executed=True)
    base.update(kw)
    return MeasureRow(**base)


def test_gates_over_full_denominator():
    rows = [
        _row(repo="r1", ebsr=True, pass_rate=1.0, collect_clean=True, total=10, passed=10),
        _row(repo="r2", ebsr=True, pass_rate=0.5, collect_clean=False, total=10, passed=5),
        _row(repo="r3", build_ok=False, executed=False, ebsr=False, pass_rate=0.0),
    ]
    m = compute_metrics(rows)
    assert m["n"] == 3
    assert m["n_ebsr"] == 2 and m["EBSR"] == round(2 / 3, 4)
    assert m["n_collect_clean"] == 1 and m["collect_clean_rate"] == round(1 / 3, 4)
    assert m["ESSR_all"] == round((1.0 + 0.5 + 0.0) / 3, 4)
    assert m["ESSR_exec"] == round((1.0 + 0.5) / 2, 4)
    assert m["coverage"] == round(2 / 3, 4)


def test_real_success_requires_ebsr_and_pass_ge_080():
    rows = [_row(repo="r1", ebsr=True, pass_rate=0.80), _row(repo="r2", ebsr=True, pass_rate=0.79),
            _row(repo="r3", ebsr=False, pass_rate=1.0)]
    m = compute_metrics(rows)
    assert m["n_real_success"] == 1 and m["real_success"] == round(1 / 3, 4)


def test_micro_is_test_weighted_over_executed():
    rows = [_row(repo="r1", total=100, skipped=0, passed=90), _row(repo="r2", total=10, skipped=2, passed=8)]
    m = compute_metrics(rows)
    assert m["micro"] == round((90 + 8) / (100 + 8), 4)


def test_empty_rows_safe():
    m = compute_metrics([])
    assert m["n"] == 0 and m["EBSR"] == 0.0 and m["ESSR_all"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_metrics_gates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.metrics'`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/metrics.py
from __future__ import annotations

from bench.schema import MeasureRow


def _r(x: float) -> float:
    return round(x, 4)


def _div(num: float, den: float) -> float:
    return _r(num / den) if den else 0.0


def compute_metrics(rows: list[MeasureRow], gold: dict | None = None) -> dict:
    n = len(rows)
    ex = [r for r in rows if r.executed]
    n_exec = len(ex)
    n_ebsr = sum(1 for r in rows if r.ebsr)
    n_collect_clean = sum(1 for r in rows if r.collect_clean)
    n_real = sum(1 for r in rows if r.ebsr and r.pass_rate >= 0.8)
    micro_passed = sum(r.passed for r in ex)
    micro_total = sum(max(r.total - r.skipped, 0) for r in ex)

    out = {
        "n": n, "n_exec": n_exec, "n_ebsr": n_ebsr, "n_collect_clean": n_collect_clean,
        "n_real_success": n_real,
        "EBSR": _div(n_ebsr, n),
        "collect_clean_rate": _div(n_collect_clean, n),
        "ESSR_all": _div(sum(r.pass_rate for r in rows), n),
        "ESSR_exec": _div(sum(r.pass_rate for r in ex), n_exec),
        "real_success": _div(n_real, n),
        "micro": _div(micro_passed, micro_total),
        "full_pass_repos": sum(1 for r in ex if r.pass_rate >= 0.999),
        "coverage": _div(n_exec, n),
    }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_metrics_gates.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/metrics.py tests/bench/test_metrics_gates.py
git commit -m "feat(bench): correctness-gate metrics (EBSR, ESSR, collect-clean, real>=.8)"
```

---

### Task 3: Metrics — gold ESSR (retroactive fixed denominator)

**Files:**
- Modify: `bench/metrics.py`
- Test: `tests/bench/test_metrics_gold.py`

**Interfaces:**
- Consumes: `gold: dict[str, list[str]]` mapping `repo -> [node_id, ...]`.
- Produces: adds keys `gold_ESSR`, `n_gold` to `compute_metrics` (only when `gold` given).

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_metrics_gold.py
from bench.schema import MeasureRow
from bench.metrics import compute_metrics


def _row(repo, passed_ids, **kw):
    base = dict(agent="a", repo=repo, env_status="ok", build_ok=True, executed=True,
                ebsr=True, passed_node_ids=tuple(passed_ids))
    base.update(kw)
    return MeasureRow(**base)


def test_gold_essr_scores_intersection_over_fixed_denominator():
    gold = {"r1": ["t::a", "t::b", "t::c", "t::d"], "r2": ["t::x", "t::y"]}
    rows = [_row("r1", ["t::a", "t::b"]), _row("r2", ["t::x", "t::y", "t::z"])]
    m = compute_metrics(rows, gold=gold)
    assert m["n_gold"] == 2 and m["gold_ESSR"] == round((0.5 + 1.0) / 2, 4)


def test_gold_absent_repo_excluded():
    gold = {"r1": ["t::a", "t::b"]}
    rows = [_row("r1", ["t::a"]), _row("r99", ["t::q"])]
    m = compute_metrics(rows, gold=gold)
    assert m["n_gold"] == 1 and m["gold_ESSR"] == 0.5


def test_no_gold_arg_omits_gold_keys():
    m = compute_metrics([_row("r1", ["t::a"])])
    assert "gold_ESSR" not in m
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_metrics_gold.py -v`
Expected: FAIL with `KeyError: 'n_gold'`

- [ ] **Step 3: Write minimal implementation**

Add to `compute_metrics`, just before `return out`:

```python
    if gold:
        gold_scores = []
        for r in rows:
            g = gold.get(r.repo)
            if not g:
                continue
            gset = set(g)
            gold_scores.append(len(set(r.passed_node_ids) & gset) / len(gset))
        out["n_gold"] = len(gold_scores)
        out["gold_ESSR"] = _div(sum(gold_scores), len(gold_scores))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_metrics_gold.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/metrics.py tests/bench/test_metrics_gold.py
git commit -m "feat(bench): retroactive gold-set ESSR (fixed denominator)"
```

---

### Task 4: Metrics — efficiency & economy aggregates

**Files:**
- Modify: `bench/metrics.py`
- Test: `tests/bench/test_metrics_efficiency.py`

**Interfaces:**
- Produces: adds `mean_image_delta_mb, mean_installed_pkgs, mean_tokens, mean_tokens_out, tokens_per_ebsr, tokens_per_real_success, mean_turns, mean_produce_s, wall_s_per_real_success, rebuild_ok_rate, unreplayed_rate, n_token_reporting`. Means skip `None`; per-success sums use only reporting rows.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_metrics_efficiency.py
from bench.schema import MeasureRow
from bench.metrics import compute_metrics


def _row(**kw):
    base = dict(agent="a", repo="r", env_status="ok", build_ok=True, executed=True)
    base.update(kw)
    return MeasureRow(**base)


def test_efficiency_means_skip_none():
    rows = [
        _row(repo="r1", ebsr=True, pass_rate=1.0, image_delta_mb=100.0,
             tokens_in=10, tokens_out=90, turns_used=5, produce_s=30.0, build_s=20.0, test_s=10.0),
        _row(repo="r2", ebsr=True, pass_rate=0.9, image_delta_mb=None, tokens_in=None, tokens_out=None),
    ]
    m = compute_metrics(rows)
    assert m["mean_image_delta_mb"] == 100.0
    assert m["n_token_reporting"] == 1 and m["mean_tokens"] == 100.0 and m["mean_tokens_out"] == 90.0


def test_tokens_per_success_use_success_denominators():
    rows = [_row(repo="r1", ebsr=True, pass_rate=1.0, tokens_in=50, tokens_out=150),
            _row(repo="r2", ebsr=True, pass_rate=0.5, tokens_in=100, tokens_out=100)]
    m = compute_metrics(rows)
    assert m["tokens_per_ebsr"] == round(400 / 2, 4)
    assert m["tokens_per_real_success"] == round(400 / 1, 4)


def test_rebuild_and_unreplayed_rates():
    rows = [_row(repo="r1", build_ok=True, meta={}), _row(repo="r2", build_ok=True, meta={"unreplayed": True}),
            _row(repo="r3", build_ok=False, meta={})]
    m = compute_metrics(rows)
    assert m["rebuild_ok_rate"] == round(2 / 3, 4) and m["unreplayed_rate"] == round(1 / 3, 4)


def test_no_token_reporting_gives_none_not_zero():
    m = compute_metrics([_row(repo="r1", ebsr=True, pass_rate=1.0)])
    assert m["mean_tokens"] is None and m["tokens_per_real_success"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_metrics_efficiency.py -v`
Expected: FAIL with `KeyError: 'mean_image_delta_mb'`

- [ ] **Step 3: Write minimal implementation**

Add helper below `_div`:

```python
def _mean_opt(vals: list) -> float | None:
    xs = [v for v in vals if v is not None]
    return _r(sum(xs) / len(xs)) if xs else None
```

Add to `compute_metrics`, before `return out`:

```python
    tok_rows = [r for r in rows if r.tokens_in is not None and r.tokens_out is not None]
    tok_total = sum(r.tokens_in + r.tokens_out for r in tok_rows)
    n_build_ok = sum(1 for r in rows if r.build_ok)
    n_unreplayed = sum(1 for r in rows if r.meta.get("unreplayed"))

    out.update({
        "mean_image_delta_mb": _mean_opt([r.image_delta_mb for r in rows]),
        "mean_installed_pkgs": _mean_opt([r.installed_pkg_count for r in rows]),
        "mean_tokens": _r(tok_total / len(tok_rows)) if tok_rows else None,
        "mean_tokens_out": _mean_opt([r.tokens_out for r in tok_rows]) if tok_rows else None,
        "tokens_per_ebsr": _r(tok_total / n_ebsr) if (tok_rows and n_ebsr) else None,
        "tokens_per_real_success": _r(tok_total / n_real) if (tok_rows and n_real) else None,
        "mean_turns": _mean_opt([r.turns_used for r in rows]),
        "mean_produce_s": _mean_opt([r.produce_s for r in rows]),
        "wall_s_per_real_success": (
            _r(sum((r.produce_s or 0) + (r.build_s or 0) + (r.test_s or 0) for r in rows) / n_real)
            if n_real else None),
        "n_token_reporting": len(tok_rows),
        "rebuild_ok_rate": _div(n_build_ok, n),
        "unreplayed_rate": _div(n_unreplayed, n),
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_metrics_efficiency.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/metrics.py tests/bench/test_metrics_efficiency.py
git commit -m "feat(bench): efficiency & economy aggregates (tokens/win, rebuild-ok, image delta)"
```

---

### Task 5: Measure — JUnit parser

**Files:**
- Create: `bench/measure.py`
- Test: `tests/bench/test_junit_parser.py`

**Interfaces:**
- Produces: `parse_junit(xml_text: str) -> dict` with `total, passed, failed, errors, skipped, passed_node_ids, failed_node_ids, error_node_ids`. Node-id = `f"{classname}::{name}"` if classname else `name`; outcome from child tag.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_junit_parser.py
from bench.measure import parse_junit

_XML = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
  <testcase classname="tests.test_a" name="test_ok"/>
  <testcase classname="tests.test_a" name="test_bad"><failure message="x">boom</failure></testcase>
  <testcase classname="tests.test_b" name="test_err"><error message="y">nope</error></testcase>
  <testcase classname="tests.test_b" name="test_skip"><skipped/></testcase>
</testsuite></testsuites>"""


def test_counts_and_outcomes():
    r = parse_junit(_XML)
    assert (r["total"], r["passed"], r["failed"], r["errors"], r["skipped"]) == (4, 1, 1, 1, 1)


def test_node_ids_by_outcome():
    r = parse_junit(_XML)
    assert r["passed_node_ids"] == ("tests.test_a::test_ok",)
    assert r["failed_node_ids"] == ("tests.test_a::test_bad",)
    assert r["error_node_ids"] == ("tests.test_b::test_err",)


def test_empty_or_garbage_returns_zeroed():
    assert parse_junit("")["total"] == 0 and parse_junit("")["passed_node_ids"] == ()
    assert parse_junit("<not-xml")["total"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_junit_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.measure'`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/measure.py
from __future__ import annotations

import xml.etree.ElementTree as ET


def _node_id(tc: ET.Element) -> str:
    cls = tc.get("classname") or ""
    name = tc.get("name") or ""
    return f"{cls}::{name}" if cls else name


def parse_junit(xml_text: str) -> dict:
    passed, failed, errors, skipped = [], [], [], []
    try:
        root = ET.fromstring(xml_text) if xml_text.strip() else None
    except ET.ParseError:
        root = None
    if root is not None:
        for tc in root.iter("testcase"):
            nid = _node_id(tc)
            if tc.find("failure") is not None:
                failed.append(nid)
            elif tc.find("error") is not None:
                errors.append(nid)
            elif tc.find("skipped") is not None:
                skipped.append(nid)
            else:
                passed.append(nid)
    return {
        "total": len(passed) + len(failed) + len(errors) + len(skipped),
        "passed": len(passed), "failed": len(failed), "errors": len(errors), "skipped": len(skipped),
        "passed_node_ids": tuple(passed), "failed_node_ids": tuple(failed),
        "error_node_ids": tuple(errors),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_junit_parser.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/measure.py tests/bench/test_junit_parser.py
git commit -m "feat(bench): JUnit XML parser (per-node outcomes + node-ids)"
```

---

### Task 6: Measure — collect-output parser

**Files:**
- Modify: `bench/measure.py`
- Test: `tests/bench/test_collect_parser.py`

**Interfaces:**
- Produces: `parse_collect(rc: int, stdout: str) -> dict` (`collect_clean` = `rc in {0,5}`, `collect_errors`); `parse_collected_node_ids(stdout) -> tuple` (`::` lines).

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_collect_parser.py
import pytest
from bench.measure import parse_collect, parse_collected_node_ids

_OUT = """tests/test_a.py::test_ok
tests/test_a.py::test_two
_______ ERROR collecting tests/test_missing.py _______
E   ModuleNotFoundError: No module named 'foo'
2 tests collected, 1 error
"""


@pytest.mark.parametrize("rc,clean", [(0, True), (5, True), (2, False), (4, False), (3, False)])
def test_collect_clean_only_for_0_and_5(rc, clean):
    assert parse_collect(rc, "")["collect_clean"] is clean


def test_collect_errors_scraped():
    assert any("ModuleNotFoundError" in e for e in parse_collect(2, _OUT)["collect_errors"])


def test_collected_node_ids_are_double_colon_lines():
    assert parse_collected_node_ids(_OUT) == ("tests/test_a.py::test_ok", "tests/test_a.py::test_two")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_collect_parser.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_collect'`

- [ ] **Step 3: Write minimal implementation**

Add to `bench/measure.py`:

```python
import re

_COLLECT_ERR = re.compile(r"((?:[A-Za-z_][\w.]*)?(?:Error|Exception|Warning)):")


def parse_collected_node_ids(stdout: str) -> tuple:
    return tuple(ln.strip() for ln in (stdout or "").splitlines() if "::" in ln)


def parse_collect(rc: int, stdout: str) -> dict:
    errs = []
    for ln in (stdout or "").splitlines():
        if _COLLECT_ERR.search(ln) and "::" not in ln:
            errs.append(ln.strip()[:200])
    return {"collect_clean": rc in (0, 5), "collect_errors": tuple(errs)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_collect_parser.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/measure.py tests/bench/test_collect_parser.py
git commit -m "feat(bench): collect-output parser (collect_clean gate + node-ids + errors)"
```

---

### Task 7: Measure — the `measure()` orchestration

**Files:**
- Modify: `bench/measure.py`
- Test: `tests/bench/test_measure.py`

**Interfaces:**
- Consumes: `HarvestedEnv`, `RepoSpec`, `MeasureRow` (Task 1); `parse_junit`, `parse_collect`, `parse_collected_node_ids` (Tasks 5–6).
- Produces: `measure(env: HarvestedEnv, *, docker, build_timeout=3600, test_timeout=1800) -> MeasureRow`. `docker` (a `DockerClient`) has: `build(tag,ctx)->(rc,log)`, `image_size_mb(tag)->float|None`, `run_detached(tag,name,workdir)->None`, `exec(name,argv,timeout=None)->(rc,out,timed_out)`, `rm(name,tag)->None`. Reads `env.agent`, `env.repo`, `env.meta`. Collect rc never gates the test run; `executed` is decided by JUnit presence.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_measure.py
from bench.schema import HarvestedEnv, RepoSpec
from bench.measure import measure

_JUNIT_OK = """<testsuites><testsuite tests="2">
  <testcase classname="t" name="a"/><testcase classname="t" name="b"/></testsuite></testsuites>"""

REPO = RepoSpec("o/r", "https://github.com/o/r")


def _env(**kw):
    base = dict(agent="v3", repo=REPO, dockerfile="FROM x", base_image="python:3.13-slim",
                status="ok", meta={"tokens_in": 5, "tokens_out": 15})
    base.update(kw)
    return HarvestedEnv(**base)


class FakeDocker:
    def __init__(self, build_rc=0, size_mb=250.0, script=None, junit=_JUNIT_OK):
        self.build_rc, self.size_mb, self.script, self.junit = build_rc, size_mb, script or {}, junit

    def build(self, tag, ctx):
        return self.build_rc, "build log"

    def image_size_mb(self, tag):
        return self.size_mb

    def run_detached(self, tag, name, workdir):
        pass

    def exec(self, name, argv, timeout=None):
        cmd = " ".join(argv)
        if "cat" in cmd and "junit.xml" in cmd:
            return 0, self.junit, False
        for needle, resp in self.script.items():
            if needle in cmd:
                return resp
        return 0, "", False

    def rm(self, name, tag):
        pass


def test_build_failure_non_ebsr_still_a_row():
    row = measure(_env(), docker=FakeDocker(build_rc=1))
    assert row.build_ok is False and row.ebsr is False and row.executed is False
    assert row.env_status == "ok"


def test_collect_rc2_does_not_block_test_run():
    script = {"--co -q /testbed": (2, "tests/x.py::a\n1 error", False)}
    row = measure(_env(), docker=FakeDocker(script=script))
    assert row.collect_clean is False and row.executed is True and row.ebsr is True
    assert row.total == 2 and row.passed == 2 and row.pass_rate == 1.0


def test_env_missing_short_circuits():
    row = measure(_env(dockerfile=None, status="missing"), docker=FakeDocker())
    assert row.env_status == "missing" and row.build_ok is False and row.executed is False


def test_tokens_propagated_from_meta():
    row = measure(_env(), docker=FakeDocker())
    assert row.tokens_in == 5 and row.tokens_out == 15


def test_image_delta_uses_base_size():
    d = FakeDocker(size_mb=250.0)
    sizes = {"python:3.13-slim": 200.0}
    d.image_size_mb = lambda tag: sizes.get(tag, 250.0)
    row = measure(_env(), docker=d)
    assert row.image_size_mb == 250.0 and row.image_delta_mb == 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_measure.py -v`
Expected: FAIL with `ImportError: cannot import name 'measure'`

- [ ] **Step 3: Write minimal implementation**

Add to `bench/measure.py` (top imports: `import os, tempfile, time`; `from bench.schema import HarvestedEnv, MeasureRow`):

```python
W = "/testbed"
_ENSURE = ("python -m pip install -q --break-system-packages pytest pytest-timeout "
           "|| python -m pip install -q pytest pytest-timeout || true")
_TIMEOUT_GUARD = ('F=""; python -c "import pytest_timeout" >/dev/null 2>&1 && '
                  'F="--timeout=120 --timeout-method=signal"')


def _sh(cmd: str) -> list:
    return ["bash", "-lc", cmd]


def measure(env: HarvestedEnv, *, docker, build_timeout: int = 3600, test_timeout: int = 1800) -> MeasureRow:
    agent, repo, m = env.agent, env.repo.full_name, env.meta
    slug = f"{agent}-{repo}".lower().replace("/", "-")
    base_row = dict(agent=agent, repo=repo, env_status=env.status,
                    tokens_in=m.get("tokens_in"), tokens_out=m.get("tokens_out"),
                    llm_calls=m.get("llm_calls"), turns_used=m.get("turns_used"),
                    produce_s=m.get("produce_s"), meta=dict(m))

    if env.status != "ok" or not env.dockerfile:
        return MeasureRow(build_ok=False, executed=False, ebsr=False, **base_row)

    tag = name = f"bench-{slug}"
    ctx = tempfile.mkdtemp(prefix="benchctx-")
    with open(os.path.join(ctx, "Dockerfile"), "w") as f:
        f.write(env.dockerfile)
    for fname, content in (env.setup_scripts or {}).items():
        with open(os.path.join(ctx, fname), "w") as f:
            f.write(content)

    t0 = time.time()
    build_rc, build_log = docker.build(tag, ctx)
    build_s = round(time.time() - t0, 2)
    if build_rc != 0:
        return MeasureRow(build_ok=False, build_log_tail=build_log[-2000:], build_s=build_s,
                          executed=False, ebsr=False, **base_row)

    img_mb = docker.image_size_mb(tag)
    base_mb = docker.image_size_mb(env.base_image) if env.base_image else None
    delta_mb = round(img_mb - base_mb, 2) if (img_mb is not None and base_mb is not None) else None

    try:
        docker.run_detached(tag, name, W)
        docker.exec(name, _sh(f"mkdir -p {W}/logs"))
        docker.exec(name, _sh(_ENSURE))
        crc, cout, _ = docker.exec(name, _sh(f"python -m pytest --co -q {W}; exit ${{PIPESTATUS[0]:-$?}}"))
        collect = parse_collect(crc, cout)
        _, cout2, _ = docker.exec(name, _sh(
            f"python -m pytest --co -q --continue-on-collection-errors {W} 2>&1 || true"))
        collected = parse_collected_node_ids(cout2)
        run = (f"{_TIMEOUT_GUARD}; python -m pytest -q --continue-on-collection-errors "
               f"--junit-xml={W}/logs/junit.xml $F || true")
        t1 = time.time()
        _, _, timed_out = docker.exec(name, _sh(run), timeout=test_timeout)
        test_s = round(time.time() - t1, 2)
        _, junit_xml, _ = docker.exec(name, _sh(f"cat {W}/logs/junit.xml 2>/dev/null || true"))
        _, pkgs_out, _ = docker.exec(name, _sh("python -m pip list --format=freeze 2>/dev/null | wc -l"))
    finally:
        docker.rm(name, tag)

    j = parse_junit(junit_xml)
    executed = bool(junit_xml.strip()) and (j["total"] > 0 or "testsuite" in junit_xml)
    eff = max(j["total"] - j["skipped"], 0)
    pass_rate = round(j["passed"] / eff, 4) if eff > 0 else 0.0
    try:
        pkg_count = int(pkgs_out.strip().split()[0])
    except (ValueError, IndexError):
        pkg_count = None

    return MeasureRow(
        build_ok=True, build_log_tail=build_log[-2000:], build_s=build_s, test_s=test_s,
        collect_rc=crc, collect_clean=collect["collect_clean"], collect_errors=collect["collect_errors"],
        collected_node_ids=collected, executed=executed,
        total=j["total"], passed=j["passed"], failed=j["failed"], errors=j["errors"], skipped=j["skipped"],
        passed_node_ids=j["passed_node_ids"], failed_node_ids=j["failed_node_ids"],
        error_node_ids=j["error_node_ids"], ebsr=executed, pass_rate=pass_rate, timed_out=timed_out,
        image_size_mb=img_mb, image_delta_mb=delta_mb, installed_pkg_count=pkg_count, **base_row)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_measure.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/measure.py tests/bench/test_measure.py
git commit -m "feat(bench): measure() orchestration — fresh build + uniform pytest + row assembly"
```

---

### Task 8: Docker client

**Files:**
- Create: `bench/docker_client.py`
- Test: `tests/bench/test_docker_client.py`

**Interfaces:**
- Produces: `SubprocessDocker` implementing the `DockerClient` shape from Task 7 over the docker CLI. Unit test asserts argv construction via a monkeypatched `subprocess.run` (no real docker).

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_docker_client.py
import subprocess
import bench.docker_client as dc


def test_image_size_mb_parses_bytes(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("P", (), {"stdout": "524288000", "returncode": 0})())
    assert dc.SubprocessDocker().image_size_mb("img") == 500.0


def test_image_size_mb_none_on_garbage(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("P", (), {"stdout": "nope", "returncode": 1})())
    assert dc.SubprocessDocker().image_size_mb("img") is None


def test_exec_timeout_returns_124(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(subprocess, "run", boom)
    rc, out, timed = dc.SubprocessDocker().exec("c", ["echo", "hi"], timeout=1)
    assert rc == 124 and timed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_docker_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.docker_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/docker_client.py
from __future__ import annotations

import subprocess


class SubprocessDocker:
    """DockerClient over the docker CLI (the shape measure() expects)."""

    def build(self, tag: str, ctx: str) -> tuple[int, str]:
        p = subprocess.run(["docker", "build", "-t", tag, ctx], capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr)

    def image_size_mb(self, tag: str) -> float | None:
        p = subprocess.run(["docker", "image", "inspect", tag, "--format", "{{.Size}}"],
                           capture_output=True, text=True)
        try:
            return round(int(p.stdout.strip()) / (1024 * 1024), 1)
        except (ValueError, AttributeError):
            return None

    def run_detached(self, tag: str, name: str, workdir: str) -> None:
        subprocess.run(f"docker rm -f {name} >/dev/null 2>&1", shell=True)
        subprocess.run(["docker", "run", "-d", "--name", name, "-w", workdir, tag,
                        "tail", "-f", "/dev/null"], check=True, capture_output=True)

    def exec(self, name: str, argv: list, timeout: int | None = None) -> tuple[int, str, bool]:
        try:
            p = subprocess.run(["docker", "exec", name, *argv], capture_output=True, text=True,
                               timeout=timeout)
            return p.returncode, (p.stdout + p.stderr), False
        except subprocess.TimeoutExpired:
            return 124, "", True

    def rm(self, name: str, tag: str) -> None:
        subprocess.run(f"docker rm -f {name} >/dev/null 2>&1", shell=True)
        subprocess.run(f"docker rmi {tag} >/dev/null 2>&1", shell=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_docker_client.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/docker_client.py tests/bench/test_docker_client.py
git commit -m "feat(bench): SubprocessDocker client (build/exec/size/rm)"
```

---

### Task 9: Harvest — discover Dockerfiles + meta off disk

**Files:**
- Create: `bench/harvest.py`
- Test: `tests/bench/test_harvest.py`

**Interfaces:**
- Consumes: `HarvestedEnv`, `RepoSpec` (Task 1).
- Produces: `discover(agent_roots: dict[str, str]) -> list[HarvestedEnv]`. For each agent, walks `<root>/<owner>/<repo>/`; finds the Dockerfile at `<repo_dir>/Dockerfile` **or** `<repo_dir>/eval_build/Dockerfile` (v3 legacy); loads meta from `bench_meta.json` **or** `_meta.json` (mapped); reads sibling files named in `COPY` lines. Missing Dockerfile → `status="missing"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_harvest.py
import json
from bench.harvest import discover


def _write(base, owner_repo, dockerfile=None, meta=None, subdir="", scripts=None):
    d = base / owner_repo
    (d / subdir).mkdir(parents=True, exist_ok=True) if subdir else d.mkdir(parents=True, exist_ok=True)
    tgt = d / subdir if subdir else d
    if dockerfile is not None:
        (tgt / "Dockerfile").write_text(dockerfile)
    for name, content in (scripts or {}).items():
        (tgt / name).write_text(content)
    if meta is not None:
        (d / "bench_meta.json").write_text(json.dumps(meta))


def test_discovers_dockerfile_and_meta(tmp_path):
    root = tmp_path / "v3run"
    _write(root, "o/r1", dockerfile="FROM x\nCOPY setup.sh /tmp/s\nRUN bash /tmp/s",
           meta={"tokens_in": 10, "tokens_out": 20, "base_image": "python:3.13-slim"},
           scripts={"setup.sh": "echo hi"})
    envs = discover({"v3": str(root)})
    assert len(envs) == 1
    e = envs[0]
    assert e.agent == "v3" and e.repo.full_name == "o/r1" and e.status == "ok"
    assert e.dockerfile.startswith("FROM x") and e.setup_scripts["setup.sh"] == "echo hi"
    assert e.base_image == "python:3.13-slim" and e.meta["tokens_in"] == 10


def test_eval_build_subdir_layout(tmp_path):
    root = tmp_path / "v3run"
    _write(root, "o/r2", dockerfile="FROM y", subdir="eval_build",
           meta={"base_image": "python:3.12-slim"})
    envs = discover({"v3": str(root)})
    assert len(envs) == 1 and envs[0].dockerfile == "FROM y" and envs[0].status == "ok"


def test_missing_dockerfile_is_status_missing(tmp_path):
    root = tmp_path / "v3run"
    _write(root, "o/r3", dockerfile=None, meta={"tokens_in": 5})
    envs = discover({"v3": str(root)})
    assert len(envs) == 1 and envs[0].status == "missing" and envs[0].dockerfile is None
    assert envs[0].meta["tokens_in"] == 5


def test_no_meta_gives_empty_meta(tmp_path):
    root = tmp_path / "v3run"
    _write(root, "o/r4", dockerfile="FROM z", meta=None)
    envs = discover({"v3": str(root)})
    assert envs[0].meta == {} and envs[0].status == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_harvest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.harvest'`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/harvest.py
from __future__ import annotations

import json
import os
import re
from glob import glob

from bench.schema import HarvestedEnv, RepoSpec

_COPY = re.compile(r"^\s*COPY\s+(\S+)", re.MULTILINE)
# v3 legacy _meta.json uses the same key names we need; only base_image differs in nesting.
_META_NAMES = ("bench_meta.json", "_meta.json")


def _find_dockerfile(repo_dir: str) -> str | None:
    for cand in (os.path.join(repo_dir, "Dockerfile"), os.path.join(repo_dir, "eval_build", "Dockerfile")):
        if os.path.isfile(cand):
            return cand
    return None


def _load_meta(repo_dir: str) -> dict:
    for name in _META_NAMES:
        p = os.path.join(repo_dir, name)
        if os.path.isfile(p):
            try:
                return json.load(open(p))
            except (json.JSONDecodeError, OSError):
                return {}
    return {}


def _sibling_scripts(df_dir: str, dockerfile: str) -> dict:
    out = {}
    for src in _COPY.findall(dockerfile):
        p = os.path.join(df_dir, os.path.basename(src))
        if os.path.isfile(p):
            out[os.path.basename(src)] = open(p).read()
    return out


def discover(agent_roots: dict) -> list:
    envs = []
    for agent, root in agent_roots.items():
        for repo_dir in sorted(glob(os.path.join(root, "*", "*"))):
            if not os.path.isdir(repo_dir):
                continue
            full_name = "/".join(repo_dir.split(os.sep)[-2:])
            repo = RepoSpec(full_name, f"https://github.com/{full_name}")
            meta = _load_meta(repo_dir)
            df_path = _find_dockerfile(repo_dir)
            if df_path is None:
                envs.append(HarvestedEnv(agent, repo, None, {}, meta.get("base_image"), "missing", meta))
                continue
            df = open(df_path).read()
            scripts = _sibling_scripts(os.path.dirname(df_path), df)
            envs.append(HarvestedEnv(agent, repo, df, scripts, meta.get("base_image"), "ok", meta))
    return envs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_harvest.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/harvest.py tests/bench/test_harvest.py
git commit -m "feat(bench): harvest Dockerfiles + bench_meta off disk (eval_build + legacy _meta fallback)"
```

---

### Task 10: Orchestrator + CLI

**Files:**
- Create: `bench/unified_bench.py`
- Test: `tests/bench/test_orchestrator.py`

**Interfaces:**
- Consumes: `discover` (Task 9), `measure` (Task 7), `compute_metrics` (Tasks 2–4), `SubprocessDocker` (Task 8).
- Produces: `run_one(env, out_root, *, docker) -> str` (writes `<out_root>/<agent>/<owner>/<repo>/row.json`, skips if exists = resume); `aggregate(out_root, gold=None) -> dict`; `main(argv)` CLI with `--harvest a=path,b=path --out DIR [--aggregate-only] [--gold F] [--concurrency N]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_orchestrator.py
import json
from dataclasses import asdict
from bench.schema import HarvestedEnv, RepoSpec, MeasureRow
from bench import unified_bench as ub


def _env(agent="v3", repo="o/r"):
    return HarvestedEnv(agent, RepoSpec(repo, f"https://github.com/{repo}"), "FROM x",
                        base_image="python:3.13-slim", meta={"tokens_in": 1, "tokens_out": 2})


def _fake_measure(env, *, docker, **kw):
    return MeasureRow(agent=env.agent, repo=env.repo.full_name, env_status="ok", build_ok=True,
                      executed=True, ebsr=True, pass_rate=1.0, total=3, passed=3, collect_clean=True)


def test_run_one_writes_row_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(ub, "measure", _fake_measure)
    p1 = ub.run_one(_env(), str(tmp_path), docker=object())
    assert json.load(open(p1))["ebsr"] is True
    monkeypatch.setattr(ub, "measure", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-ran")))
    p2 = ub.run_one(_env(), str(tmp_path), docker=object())
    assert p2 == p1


def test_aggregate_globs_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(ub, "measure", _fake_measure)
    ub.run_one(_env(agent="v3", repo="o/r"), str(tmp_path), docker=object())
    out = ub.aggregate(str(tmp_path))
    assert out["v3"]["n"] == 1 and out["v3"]["EBSR"] == 1.0 and out["v3"]["ESSR_all"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bench/test_orchestrator.py -v`
Expected: FAIL with `AttributeError: module 'bench.unified_bench' has no attribute 'run_one'`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/unified_bench.py
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from glob import glob

from bench.docker_client import SubprocessDocker
from bench.harvest import discover
from bench.measure import measure
from bench.metrics import compute_metrics
from bench.schema import MeasureRow


def _row_path(out_root: str, agent: str, repo: str) -> str:
    return os.path.join(out_root, agent, *repo.split("/"), "row.json")


def run_one(env, out_root: str, *, docker) -> str:
    out = _row_path(out_root, env.agent, env.repo.full_name)
    if os.path.exists(out):
        return out                                     # resume
    os.makedirs(os.path.dirname(out), exist_ok=True)
    row = measure(env, docker=docker)
    with open(out, "w") as f:
        json.dump(asdict(row), f, indent=2, default=list)
    return out


def aggregate(out_root: str, gold: dict | None = None) -> dict:
    by_agent: dict = {}
    for p in glob(os.path.join(out_root, "*", "**", "row.json"), recursive=True):
        d = json.load(open(p))
        agent = os.path.relpath(p, out_root).split(os.sep)[0]
        d.pop("agent", None)
        row = MeasureRow(agent=agent, **{k: (tuple(v) if isinstance(v, list) else v)
                                         for k, v in d.items()})
        by_agent.setdefault(agent, []).append(row)
    return {a: compute_metrics(rows, gold=gold) for a, rows in by_agent.items()}


def _parse_harvest(arg: str) -> dict:
    out = {}
    for pair in arg.split(","):
        name, _, path = pair.partition("=")
        out[name.strip()] = path.strip()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harvest", help="agent=run_dir,agent2=run_dir2")
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--aggregate-only", action="store_true")
    ap.add_argument("--gold")
    a = ap.parse_args(argv)

    if not a.aggregate_only:
        envs = discover(_parse_harvest(a.harvest))
        docker = SubprocessDocker()
        with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            list(ex.map(lambda e: run_one(e, a.out, docker=docker), envs))

    gold = json.load(open(a.gold)) if a.gold else None
    out = aggregate(a.out, gold=gold)
    with open(os.path.join(a.out, "metrics.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bench/test_orchestrator.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add bench/unified_bench.py tests/bench/test_orchestrator.py
git commit -m "feat(bench): orchestrator + CLI (harvest -> measure -> aggregate; resume)"
```

---

### Task 11: End-to-end smoke (real Docker, `slow` marker)

**Files:**
- Create: `tests/bench/test_e2e_smoke.py`
- Modify: `pyproject.toml` (register `slow` marker if absent) — check first: `grep -rn "markers" pyproject.toml pytest.ini setup.cfg 2>/dev/null`.

**Interfaces:**
- Consumes: `measure` + `SubprocessDocker` on a real hand-written `HarvestedEnv` (no agent), proving the measurement path end-to-end.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_e2e_smoke.py
import shutil
import pytest
from bench.schema import HarvestedEnv, RepoSpec
from bench.measure import measure
from bench.docker_client import SubprocessDocker

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")

_DF = """FROM python:3.13-slim
RUN pip install --no-cache-dir itsdangerous pytest
RUN git clone --depth=1 https://github.com/pallets/itsdangerous /testbed
WORKDIR /testbed
"""


@pytest.mark.slow
def test_itsdangerous_measures_green():
    env = HarvestedEnv("smoke", RepoSpec("pallets/itsdangerous", "https://github.com/pallets/itsdangerous"),
                       _DF, base_image="python:3.13-slim", meta={"tokens_in": 0, "tokens_out": 0})
    row = measure(env, docker=SubprocessDocker())
    assert row.build_ok is True and row.executed is True and row.ebsr is True
    assert row.total > 0 and row.pass_rate > 0.9 and row.image_size_mb is not None
```

- [ ] **Step 2: Run it (on a Docker host — the VM)**

Run: `python -m pytest tests/bench/test_e2e_smoke.py -m slow -v`
Expected: builds the image, runs itsdangerous's suite, asserts green. Skipped without Docker. This is the integration checkpoint.

- [ ] **Step 3: Register the marker if missing**

If `grep` showed none, add under `[tool.pytest.ini_options]` in `pyproject.toml`:

```toml
markers = ["slow: end-to-end tests that build real Docker images"]
```

- [ ] **Step 4: Run the full bench unit suite (excluding slow)**

Run: `python -m pytest tests/bench -v -m "not slow"`
Expected: PASS (all unit tests green).

- [ ] **Step 5: Commit**

```bash
git add tests/bench/test_e2e_smoke.py pyproject.toml
git commit -m "test(bench): e2e smoke on itsdangerous (real docker, slow marker)"
```

---

## Post-plan: agent-side emitters + VM validation (separate, not `bench` unit tests)

These live in each agent's OWN repo/branch (never in `bench`) and are validated by real runs, not unit
tests:

- **v3 emitter** — write `bench_meta.json` next to the existing `eval_build/Dockerfile` (`tokens_in/out`
  from `[Tokens]`, `produce_s`, `base_image`, `head_sha`, `agent_commit`). v3 already writes the
  Dockerfile + `_meta.json`, so harvest works on existing v3 runs today via the `_meta.json` fallback.
- **repo2run emitter** — after `integrate_dockerfile`, copy the Dockerfile + write `bench_meta.json`.
- **rat emitter** — `render_dockerfile(base, url, outer_commands)` from `outer_commands.json`, write it +
  `bench_meta.json`; optional `docker commit` `unreplayed` fallback.

### Task V: Deploy `bench/` to the VM and validate (run after Task 11)

`bench/` has **no agent deps** — it only needs Python 3 + `pytest` + the VM's Docker. It is its own
folder (`/opt/bench`), independent of `/opt/agents/*`. VM: `root@167.233.64.96`. The VM is fetch-only,
so ship with `scp`/`rsync` from local (no branch entanglement); do **not** touch
`/opt/agents/john-planner-v3`. Reminders: `pkill -f` over SSH self-kills (use `[r]un…` regex); do not
`docker --prune` (containerd commit race).

- [ ] **Step 1: Ship the package (from local, after the unit suite is green)**

```bash
ssh root@167.233.64.96 'mkdir -p /opt/bench'
rsync -az --delete bench/ root@167.233.64.96:/opt/bench/bench/
rsync -az tests/bench/ root@167.233.64.96:/opt/bench/tests/bench/
```

- [ ] **Step 2: Confirm the unit suite passes identically on the VM**

Run: `ssh root@167.233.64.96 'cd /opt/bench && python3 -m pip install -q pytest && python3 -m pytest tests/bench -q -m "not slow"'`
Expected: same green counts as local (proves no local-only assumptions).

- [ ] **Step 3: Run the e2e smoke on real Docker (Task 11, VM-only)**

Run: `ssh root@167.233.64.96 'cd /opt/bench && python3 -m pytest tests/bench/test_e2e_smoke.py -m slow -v'`
Expected: builds the `itsdangerous` image, runs its suite, asserts EBSR True + pass_rate > 0.9.

- [ ] **Step 4: First payoff — harvest Dockerfiles already on disk (no new agent runs)**

Point `--harvest` at an existing v3 construction run (harvest reads its `eval_build/Dockerfile` +
`_meta.json` via the legacy fallback):

```bash
ssh root@167.233.64.96 'cd /opt/bench && python3 -m bench.unified_bench \
  --harvest v3=/opt/runs/john-planner-v3/construction-python50-20260707-072356/output \
  --out /opt/bench/runs/existing-v3 --concurrency 4'
```
Expected: `/opt/bench/runs/existing-v3/metrics.json` with EBSR / ESSR÷all / collect-clean / economy over
the 50 repos, computed uniformly — with zero re-runs of the agent.

- [ ] **Step 5: Parity gate before retiring the old path**

One shared 50-repo harvest across all three agents; confirm `bench`'s EBSR reproduces
`run_rat_benchmark.py`'s within noise. Only then deprecate the per-model `predict()` scoring in
`run_rat_benchmark.py`.

## Self-Review notes

- **Spec coverage:** §5 agent file-contract + harvest → Task 9; §6 measurement + collect-rc table → Tasks 5–7; §7 gates → Task 2; gold → Task 3; §7.5 efficiency → Task 4; anti-vanish (missing/build-fail still a row) → Task 7 tests; §9 orchestration/CLI/resume → Task 10; docker → Task 8; testing §8 → every task + Task 11. Agent emitters (§5, §10) → post-plan (they live in agent repos).
- **Types:** `HarvestedEnv`/`MeasureRow`/`RepoSpec` defined once (Task 1), referenced verbatim; `discover` returns `HarvestedEnv`s consumed by `measure` and `run_one`; `compute_metrics` keys added additively (2→3→4); `DockerClient` shape defined in Task 7, implemented Task 8.
- **Placeholders:** none — every code step is complete; agent emitters are explicitly deferred to the post-plan section (harvest already works on v3's existing `_meta.json`), not left as in-task TODOs.
