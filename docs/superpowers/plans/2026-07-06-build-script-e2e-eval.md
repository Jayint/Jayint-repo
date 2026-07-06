# E2E Build-Script Effectiveness Eval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python-only, execution-only e2e eval that runs construction-only `build_dep_graph` → `render_build_script` → fresh-container replay for a stratified repo corpus, and reports a first-pass `env_works` headline plus a `install→env_works→tests_ran→tests_passed` ladder, with language/system failure attribution and gap clusters.

**Architecture:** A new sibling module `src/eval/build_script_eval/` that **reuses** the proven primitives in `src/eval/language_package_eval/coverage.py` and `src/eval/graph_fidelity/render_fidelity.py` unchanged. The only new container logic is the replay **ladder** (`replay.py`); the scorer (`scorecard.py`) and aggregator (`report.py`) are **pure** and carry the bulk of the tests. **No oracle** — the fresh-container replay is the ground truth; attribution comes from execution error text; coverage diagnostics are gap counts + clusters, not a recall fraction.

**Tech Stack:** Python 3.11, pytest, Docker (`python:3.11-slim`, reused `coverage._MountedContainer`), the existing `python_deps.depgraph` pipeline.

## Global Constraints

- **Python-only.** `build_dep_graph` dispatches through `src/ecosystems/registry.py` where `PROVIDERS = (PythonProvider(),)`. Only Python produces a graph/`setup.sh`. Node/Go stay closure-only.
- **Execution-only, no oracle.** Do NOT import `oracle.parse_oracle` or `coverage.{diff_packages, diff_membership, pooled_recall_by_tier, score_repo_against_oracle}`. Ground truth = the container replay.
- **SERVICE / CONFIG excluded.** Never predicted-for, never attributed. `classify_execution_failures` SERVICE gaps are dropped. They appear only as the `tests_passed` confound caveat.
- **First-pass, no repair.** No agent, no repair loop. Construction intercepts the repo's real pytest (`coverage._ConstructionOnlyExecutor`); the replay is a separate fresh container.
- **`coverage.py` and `render_fidelity.py` are NOT modified.** Reuse by import only. Keeps the per-layer evals intact.
- **Bounded, foreground Docker.** Every container step runs foreground with a timeout. Never background a Docker/disk walk. The `pytest -q` rung gets a per-repo `test_timeout` (default 600s); a timeout is a recorded result, never a hang.
- **`tests_passed` is a caveated diagnostic** — never a gate, never the headline. Every report states the service/config confound.
- **Headline = `first_pass_env_works`** = `install_ok AND env_works`, over feasible repos, overall + per stratum.
- **Artifacts gitignored.** All run output under `outputs/build_script_eval/` (already covered by the `outputs/` gitignore rule — verified).
- **sys.path bootstrap.** Each module that imports the pipeline inserts repo-root and `src/` on `sys.path` (mirror `coverage.py` lines 44-48).

---

## Reused primitives (exact signatures — do not redefine)

From `src/eval/language_package_eval/coverage.py`:
- `base_image_for_repo(repo_dir: Path|str) -> tuple[str, str, str]`  # (image, minor, reason)
- `build_graph_construction_only(repo_dir: str, base_image: str, target_python: str) -> DepGraph`
- `class _MountedContainer(image, host_dir, container_dir="/workspace/repo")` — ctx mgr; `.run(command, *, timeout=300) -> CommandResult`; `.name`
- `_write_file(executor, path: str, content: str) -> None`
- `classify_execution_failures(output: str) -> tuple[dict, ...]`  # dicts: {"tier","id","evidence"}, tiers PACKAGE/SYSTEM_LIB/TOOL/SERVICE
- `first_failure_evidence(output: str, *, tail_lines=40) -> dict`  # {"command","stderr_tail"}
- `top_level_import_name(repo_dir: Path|str) -> str|None`
- `apt_names_in_graph(graph: DepGraph) -> frozenset[str]`
- `package_versions_in_graph(graph: DepGraph) -> dict[str, str|None]`
- `missing_node_clusters(scorecards: Sequence[Mapping]) -> tuple[dict, ...]`  # reads sc["feasible"], sc["execution_missing"], sc["repo"]; oracle branch absent ⇒ uses execution gaps only
- `canon_pip(name: str) -> str`
- `_docker_available() -> bool`

From `src/eval/graph_fidelity/render_fidelity.py`:
- `check_render(graph: DepGraph, script_text: str) -> RenderFidelity`  # fields: all_reciped_emitted, single_emit, topo_order_ok, valid_bash (bool|None), bash_error, ...

From `src/python_deps/depgraph/`:
- `build_script.render_build_script(graph: DepGraph|None, manual_blocks: tuple=()) -> str`
- `executor.CommandResult(command, returncode, stdout, stderr)` with `.ok` property; `executor.TIMEOUT_RC == 124`
- `schema.NodeType`

---

## File Structure

- Create `src/eval/build_script_eval/__init__.py` — empty package marker.
- Create `src/eval/build_script_eval/corpus.py` — `RepoSpec`, `STRATA`, `CORPUS`, `select()`.
- Create `src/eval/build_script_eval/scorecard.py` — pure analytics (`LadderResult`, `classify_pytest_result`, `env_works_passed`, `extract_gaps`, `attribute_failure`, `_assemble_scorecard`) + docker orchestration (`score_repo`).
- Create `src/eval/build_script_eval/replay.py` — `run_replay_ladder` (the only new container logic).
- Create `src/eval/build_script_eval/report.py` — `aggregate`, `render_report_md`.
- Create `src/eval/build_script_eval/fetch.py` — clone pinned refs into `_smoke`.
- Create `src/eval/build_script_eval/__main__.py` — CLI `--fetch/--run/--score` + `--only/--stratum`.
- Create `tests/eval/build_script_eval/{__init__,test_corpus,test_scorecard,test_replay_ladder,test_report}.py`.

---

## Task 0: Corpus manifest + selection (pure)

**Files:**
- Create: `src/eval/build_script_eval/__init__.py`
- Create: `src/eval/build_script_eval/corpus.py`
- Test: `tests/eval/build_script_eval/__init__.py`, `tests/eval/build_script_eval/test_corpus.py`

**Interfaces:**
- Produces: `RepoSpec(name, full_name, git_url, ref, stratum, top_import=None, feasible=True, network_in_tests=False)`; `STRATA: frozenset[str]`; `CORPUS: tuple[RepoSpec,...]`; `select(only: frozenset[str]=frozenset(), strata: frozenset[str]=frozenset()) -> list[RepoSpec]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/build_script_eval/test_corpus.py
import pytest
from src.eval.build_script_eval.corpus import CORPUS, STRATA, RepoSpec, select


def test_strata_are_control_and_syslib():
    assert STRATA == frozenset({"S_control", "S_syslib"})


def test_every_row_has_a_valid_stratum_and_unique_name():
    names = [r.name for r in CORPUS]
    assert len(names) == len(set(names)), "duplicate repo dir names"
    assert all(r.stratum in STRATA for r in CORPUS)
    assert any(r.stratum == "S_control" for r in CORPUS)
    assert any(r.stratum == "S_syslib" for r in CORPUS)


def test_select_by_stratum():
    rows = select(strata=frozenset({"S_syslib"}))
    assert rows and all(r.stratum == "S_syslib" for r in rows)


def test_select_by_name():
    one = CORPUS[0]
    assert {r.name for r in select(only=frozenset({one.name}))} == {one.name}


def test_select_empty_is_full_corpus():
    assert len(select()) == len(CORPUS)


def test_select_unknown_stratum_raises():
    with pytest.raises(ValueError):
        select(strata=frozenset({"S_bogus"}))


def test_select_unknown_name_raises():
    with pytest.raises(ValueError):
        select(only=frozenset({"no-such-repo"}))
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python3 -m pytest tests/eval/build_script_eval/test_corpus.py -q`
Expected: FAIL (`ModuleNotFoundError: src.eval.build_script_eval.corpus`).

- [ ] **Step 3: Create the package markers + corpus**

```python
# src/eval/build_script_eval/__init__.py
```
(empty file)

```python
# tests/eval/build_script_eval/__init__.py
```
(empty file)

```python
# src/eval/build_script_eval/corpus.py
"""Committed corpus manifest for the e2e build-script eval. No held-out recipe
is required (there is no oracle) — the only membership rule is a runnable test
suite, and S_syslib rows are chosen so their tests import the native extension
(a missing .so then surfaces at the import/collect rung, service-independent).
"""
from __future__ import annotations

from dataclasses import dataclass

STRATA: frozenset[str] = frozenset({"S_control", "S_syslib"})


@dataclass(frozen=True)
class RepoSpec:
    name: str                    # unique dir name under the _smoke root
    full_name: str               # "org/repo" (display / output id)
    git_url: str
    ref: str                     # pinned tag or sha (see fetch.py; verify with git ls-remote)
    stratum: str                 # one of STRATA
    top_import: str | None = None      # import-check override; else derived
    feasible: bool = True              # False ⇒ excluded from the headline denominator
    network_in_tests: bool = False     # True ⇒ keep network during the pytest rung


# Starter corpus. Refs are concrete tags; Task 5 verifies each with `git ls-remote`
# before the first fetch and pins to a sha if desired. Keep S_control apt-empty
# (over-prediction baseline) and S_syslib native-ext-importing.
CORPUS: tuple[RepoSpec, ...] = (
    # --- S_control: pure-Python, no apt expected ---
    RepoSpec("typer", "fastapi/typer",
             "https://github.com/fastapi/typer", "0.12.5", "S_control", top_import="typer"),
    RepoSpec("python-semantic-release", "python-semantic-release/python-semantic-release",
             "https://github.com/python-semantic-release/python-semantic-release",
             "v9.8.6", "S_control", top_import="semantic_release"),
    # --- S_syslib: source-form native deps; tests import the extension ---
    RepoSpec("psycopg2", "psycopg/psycopg2",
             "https://github.com/psycopg/psycopg2", "2_9_9", "S_syslib", top_import="psycopg2"),
    RepoSpec("pygraphviz", "pygraphviz/pygraphviz",
             "https://github.com/pygraphviz/pygraphviz", "pygraphviz-1.12", "S_syslib",
             top_import="pygraphviz"),
    RepoSpec("lxml", "lxml/lxml",
             "https://github.com/lxml/lxml", "lxml-5.2.2", "S_syslib", top_import="lxml"),
)


def select(only: frozenset[str] = frozenset(),
           strata: frozenset[str] = frozenset()) -> list[RepoSpec]:
    """Filter CORPUS by repo name (`only`) and/or stratum (`strata`). Empty set =
    no filter on that axis. Raises ValueError on an unknown stratum or an unknown
    name (fail-fast on a typo)."""
    if strata - STRATA:
        raise ValueError(f"unknown stratum(s): {sorted(strata - STRATA)}; valid={sorted(STRATA)}")
    names = {r.name for r in CORPUS}
    if only - names:
        raise ValueError(f"unknown repo name(s): {sorted(only - names)}")
    return [r for r in CORPUS
            if (not only or r.name in only) and (not strata or r.stratum in strata)]
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python3 -m pytest tests/eval/build_script_eval/test_corpus.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/eval/build_script_eval/__init__.py src/eval/build_script_eval/corpus.py \
        tests/eval/build_script_eval/__init__.py tests/eval/build_script_eval/test_corpus.py
git commit -m "feat(build-script-eval): corpus manifest (S_control/S_syslib strata) + select()"
```

---

## Task 1: Pure scorecard analytics (ladder result, pytest classifier, headline gate, gap split, attribution)

**Files:**
- Create: `src/eval/build_script_eval/scorecard.py` (pure core only in this task)
- Test: `tests/eval/build_script_eval/test_scorecard.py`

**Interfaces:**
- Produces:
  - `LadderResult(install_ok, env_works, tests_ran, tests_passed, highest_rung, reason, first_failure, gaps)` — frozen dataclass; `gaps: tuple[dict,...]` are `classify_execution_failures` dicts.
  - `classify_pytest_result(returncode: int) -> tuple[bool, bool, str|None]` — `(tests_ran, tests_passed, reason)`.
  - `env_works_passed(ladder: LadderResult) -> bool`.
  - `extract_gaps(gaps: tuple[dict,...]) -> tuple[tuple[dict,...], tuple[dict,...]]` — `(language_gaps, system_gaps)`; SERVICE dropped.
  - `attribute_failure(ladder: LadderResult, *, static_ok: bool, top_import: str|None, feasible: bool) -> str` — one of `pass|infeasible|render_bug|system_gap|language_gap|unknown`.
- Consumes: `coverage.canon_pip`, `executor.TIMEOUT_RC`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/eval/build_script_eval/test_scorecard.py
from src.eval.build_script_eval.scorecard import (
    LadderResult, attribute_failure, classify_pytest_result, env_works_passed, extract_gaps,
)


def _ladder(**kw):
    base = dict(install_ok=True, env_works=True, tests_ran=True, tests_passed=True,
                highest_rung="tests_passed", reason=None, first_failure=None, gaps=())
    base.update(kw)
    return LadderResult(**base)


# --- classify_pytest_result ---
def test_pytest_rc0_ran_and_passed():
    assert classify_pytest_result(0) == (True, True, None)

def test_pytest_rc1_ran_not_passed():
    assert classify_pytest_result(1) == (True, False, "tests_failed")

def test_pytest_rc5_no_tests_collected():
    assert classify_pytest_result(5) == (False, False, "no_tests_collected")

def test_pytest_rc2_collection_error():
    assert classify_pytest_result(2) == (False, False, "collection_or_usage_error")

def test_pytest_timeout():
    assert classify_pytest_result(124) == (False, False, "timeout")


# --- env_works_passed (headline gate) ---
def test_env_works_gate_true():
    assert env_works_passed(_ladder(install_ok=True, env_works=True)) is True

def test_env_works_gate_false_when_install_failed():
    assert env_works_passed(_ladder(install_ok=False, env_works=False)) is False

def test_env_works_gate_false_when_env_broken():
    assert env_works_passed(_ladder(install_ok=True, env_works=False)) is False


# --- extract_gaps ---
def test_extract_gaps_splits_language_and_system_and_drops_service():
    gaps = (
        {"tier": "PACKAGE", "id": "requests", "evidence": "..."},
        {"tier": "SYSTEM_LIB", "id": "libpq.so.5", "evidence": "..."},
        {"tier": "TOOL", "id": "pg_config", "evidence": "..."},
        {"tier": "SERVICE", "id": "unknown", "evidence": "..."},
    )
    lang, sys_ = extract_gaps(gaps)
    assert [g["id"] for g in lang] == ["requests"]
    assert {g["id"] for g in sys_} == {"libpq.so.5", "pg_config"}


# --- attribute_failure ---
def test_attribute_infeasible_shortcircuits():
    assert attribute_failure(_ladder(), static_ok=True, top_import="x", feasible=False) == "infeasible"

def test_attribute_pass_when_env_works():
    assert attribute_failure(_ladder(install_ok=True, env_works=True),
                             static_ok=True, top_import="x", feasible=True) == "pass"

def test_attribute_render_bug_when_static_fails():
    lad = _ladder(install_ok=True, env_works=False, gaps=())
    assert attribute_failure(lad, static_ok=False, top_import="x", feasible=True) == "render_bug"

def test_attribute_system_gap_wins_over_package():
    lad = _ladder(install_ok=True, env_works=False, gaps=(
        {"tier": "PACKAGE", "id": "foo", "evidence": ""},
        {"tier": "SYSTEM_LIB", "id": "libpq.so.5", "evidence": ""},
    ))
    assert attribute_failure(lad, static_ok=True, top_import="app", feasible=True) == "system_gap"

def test_attribute_own_package_is_render_bug():
    lad = _ladder(install_ok=True, env_works=False, gaps=(
        {"tier": "PACKAGE", "id": "myapp", "evidence": ""},
    ))
    assert attribute_failure(lad, static_ok=True, top_import="myapp", feasible=True) == "render_bug"

def test_attribute_language_gap_for_third_party_missing():
    lad = _ladder(install_ok=True, env_works=False, gaps=(
        {"tier": "PACKAGE", "id": "requests", "evidence": ""},
    ))
    assert attribute_failure(lad, static_ok=True, top_import="myapp", feasible=True) == "language_gap"

def test_attribute_install_failure_apt_is_system_gap():
    lad = _ladder(install_ok=False, env_works=False, gaps=(),
                  first_failure={"command": "apt-get install -y libpq-dev",
                                 "stderr_tail": "E: Unable to locate package libpq-dev"})
    assert attribute_failure(lad, static_ok=True, top_import="app", feasible=True) == "system_gap"

def test_attribute_install_failure_pip_is_language_gap():
    lad = _ladder(install_ok=False, env_works=False, gaps=(),
                  first_failure={"command": "pip install foo",
                                 "stderr_tail": "ERROR: Could not find a version that satisfies foo"})
    assert attribute_failure(lad, static_ok=True, top_import="app", feasible=True) == "language_gap"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python3 -m pytest tests/eval/build_script_eval/test_scorecard.py -q`
Expected: FAIL (`ModuleNotFoundError: src.eval.build_script_eval.scorecard`).

- [ ] **Step 3: Write the pure core**

```python
# src/eval/build_script_eval/scorecard.py
"""Execution-only scorecard analytics for the e2e build-script eval. Pure core
(this file's top half): ladder result type, pytest exit-code classifier, headline
gate, language/system gap split, and failure attribution from execution error
text. No oracle, no recall fraction. The docker orchestration (score_repo) lives
in the second half (Task 3) and reuses coverage.py primitives.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from python_deps.depgraph.executor import TIMEOUT_RC  # noqa: E402
from src.eval.language_package_eval.coverage import canon_pip  # noqa: E402

_SYSTEM_TIERS: frozenset[str] = frozenset({"SYSTEM_LIB", "TOOL"})


@dataclass(frozen=True)
class LadderResult:
    """One repo's fresh-container replay outcome, rung by rung."""

    install_ok: bool
    env_works: bool
    tests_ran: bool
    tests_passed: bool
    highest_rung: str            # none|install|env_works|tests_ran|tests_passed
    reason: str | None           # why it stopped (timeout, no_tests_collected, ...)
    first_failure: dict | None   # {"command","stderr_tail"} at the failing rung
    gaps: tuple[dict, ...]        # classify_execution_failures dicts (typed)


def classify_pytest_result(returncode: int) -> tuple[bool, bool, str | None]:
    """(tests_ran, tests_passed, reason) from a `pytest -q` exit code.

    tests_ran is True only when pytest executed tests to a pass/fail verdict
    (rc 0 or 1) — a collection/usage error (2/3/4) or an empty collection (5)
    means tests did NOT run. tests_passed is rc 0 only. A timeout (TIMEOUT_RC)
    is a non-hanging recorded miss."""
    if returncode == TIMEOUT_RC:
        return (False, False, "timeout")
    if returncode == 0:
        return (True, True, None)
    if returncode == 1:
        return (True, False, "tests_failed")
    if returncode == 5:
        return (False, False, "no_tests_collected")
    return (False, False, "collection_or_usage_error")


def env_works_passed(ladder: LadderResult) -> bool:
    """The HEADLINE gate: setup.sh installed clean AND the env imports + collects."""
    return ladder.install_ok and ladder.env_works


def extract_gaps(gaps: tuple[dict, ...]) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """Split typed execution gaps into (language, system). SERVICE dropped
    (out of scope). Language = PACKAGE; system = SYSTEM_LIB + TOOL."""
    language = tuple(g for g in gaps if g.get("tier") == "PACKAGE")
    system = tuple(g for g in gaps if g.get("tier") in _SYSTEM_TIERS)
    return language, system


def _attribute_install_failure(first_failure: dict | None) -> str:
    """apt/dpkg failure ⇒ system_gap; pip/module failure ⇒ language_gap; else
    render_bug (the setup.sh itself broke for a non-coverage reason)."""
    blob = ""
    if first_failure:
        blob = f"{first_failure.get('command', '')}\n{first_failure.get('stderr_tail', '')}".lower()
    if any(tok in blob for tok in ("apt-get", "apt ", "dpkg", "unable to locate package",
                                   "e: package", ".so", "shared object")):
        return "system_gap"
    if any(tok in blob for tok in ("pip ", "pip3", "could not find a version",
                                   "no matching distribution", "modulenotfounderror")):
        return "language_gap"
    return "render_bug"


def attribute_failure(ladder: LadderResult, *, static_ok: bool,
                      top_import: str | None, feasible: bool) -> str:
    """One label for a repo. `pass` when env_works; otherwise the blocking layer.
    Priority: infeasible ▸ static render_bug ▸ system_gap ▸ own-package render_bug
    ▸ language_gap ▸ install-failure classification ▸ unknown."""
    if not feasible:
        return "infeasible"
    if env_works_passed(ladder):
        return "pass"
    if not static_ok:
        return "render_bug"

    language, system = extract_gaps(ladder.gaps)
    if system:
        return "system_gap"
    own = canon_pip(top_import) if top_import else None
    lang_ids = {canon_pip(g["id"]) for g in language}
    if own and own in lang_ids:
        return "render_bug"   # the repo's OWN package — the PROJECT-node install gap
    if language:
        return "language_gap"
    if not ladder.install_ok:
        return _attribute_install_failure(ladder.first_failure)
    return "unknown"
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python3 -m pytest tests/eval/build_script_eval/test_scorecard.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/eval/build_script_eval/scorecard.py tests/eval/build_script_eval/test_scorecard.py
git commit -m "feat(build-script-eval): pure scorecard core (ladder result, pytest classifier, env_works gate, gap split, attribution)"
```

---

## Task 2: The replay ladder (container)

**Files:**
- Create: `src/eval/build_script_eval/replay.py`
- Test: `tests/eval/build_script_eval/test_replay_ladder.py`

**Interfaces:**
- Consumes: `LadderResult`, `classify_pytest_result` (Task 1); `coverage.{_MountedContainer, _write_file, classify_execution_failures, first_failure_evidence}`.
- Produces: `run_replay_ladder(repo_dir: str, image: str, setup_script: str, top_import: str|None, *, install_timeout=1800, test_timeout=600, isolate_network=True) -> LadderResult`.
- Produces (pure helper, unit-tested): `_disconnect_network_cmd(container_name: str) -> list[str]`.

- [ ] **Step 1: Write the failing tests (pure parts + a guarded docker smoke)**

```python
# tests/eval/build_script_eval/test_replay_ladder.py
import pytest
from src.eval.build_script_eval.replay import _disconnect_network_cmd, run_replay_ladder
from src.eval.language_package_eval.coverage import _docker_available


def test_disconnect_network_cmd_targets_bridge_and_container():
    cmd = _disconnect_network_cmd("probe-abc123")
    assert cmd[:3] == ["docker", "network", "disconnect"]
    assert "probe-abc123" in cmd


@pytest.mark.skipif(not _docker_available(), reason="docker unavailable")
def test_ladder_on_trivial_pure_python_repo(tmp_path):
    # a repo that installs cleanly, imports, and has one passing test
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n"
        "[project]\nname='triv'\nversion='0.0.0'\n"
    )
    (tmp_path / "triv").mkdir()
    (tmp_path / "triv" / "__init__.py").write_text("x = 1\n")
    (tmp_path / "test_triv.py").write_text("from triv import x\n\ndef test_x():\n    assert x == 1\n")
    setup_sh = "#!/usr/bin/env bash\nset -e\npip install -e .\n"
    res = run_replay_ladder(str(tmp_path), "python:3.11-slim", setup_sh, "triv", test_timeout=180)
    assert res.install_ok is True
    assert res.env_works is True
    assert res.tests_ran is True
    assert res.tests_passed is True
    assert res.highest_rung == "tests_passed"
```

- [ ] **Step 2: Run it, verify the pure test fails (import error) and the docker test errors/skips**

Run: `python3 -m pytest tests/eval/build_script_eval/test_replay_ladder.py::test_disconnect_network_cmd_targets_bridge_and_container -q`
Expected: FAIL (`ModuleNotFoundError: src.eval.build_script_eval.replay`).

- [ ] **Step 3: Write the ladder**

```python
# src/eval/build_script_eval/replay.py
"""The replay LADDER — the only new container logic in this eval. Runs a rendered
setup.sh in ONE fresh mounted container, then climbs
install ▸ env_works ▸ tests_ran ▸ tests_passed, recording how far it got. Reuses
coverage.py's container + classification primitives; adds only the pytest-run
rungs and optional network isolation for the test rung.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.eval.build_script_eval.scorecard import LadderResult, classify_pytest_result  # noqa: E402
from src.eval.language_package_eval.coverage import (  # noqa: E402
    _MountedContainer, _write_file, classify_execution_failures, first_failure_evidence,
)

_PYTEST_ENV = "PYTEST_ADDOPTS='-p no:cacheprovider'"


def _disconnect_network_cmd(container_name: str) -> list[str]:
    """Detach the running replay container from the default bridge network before
    the pytest rung (so tests can't silently rely on the network)."""
    return ["docker", "network", "disconnect", "bridge", container_name]


def _fail(rung_reached: str, reason: str, output: str, *, install_ok: bool) -> LadderResult:
    return LadderResult(
        install_ok=install_ok, env_works=False, tests_ran=False, tests_passed=False,
        highest_rung=rung_reached, reason=reason,
        first_failure=first_failure_evidence(output),
        gaps=classify_execution_failures(output),
    )


def run_replay_ladder(
    repo_dir: str, image: str, setup_script: str, top_import: str | None,
    *, install_timeout: int = 1800, test_timeout: int = 600, isolate_network: bool = True,
) -> LadderResult:
    """Fresh -slim replay ladder. See module docstring for the rung meanings."""
    with _MountedContainer(image, str(Path(repo_dir).resolve())) as box:
        cd = f"cd {box.container_dir}"

        # RUNG 1 — install via setup.sh (from the repo root, mirrors install docs).
        _write_file(box, "/setup.sh", setup_script)
        install = box.run(f"{cd} && bash -x /setup.sh", timeout=install_timeout)
        if not install.ok:
            return _fail("none", "install_failed", install.stdout + install.stderr, install_ok=False)

        # RUNG 2 — env_works: repo imports + tests COLLECT (no execution yet).
        probe_out: list[str] = []
        if top_import:
            imp = box.run(f"{cd} && python3 -c 'import {top_import}'", timeout=120)
            if not imp.ok:
                probe_out.append(imp.stdout + imp.stderr)
        collected = box.run(f"{cd} && python3 -m pytest --collect-only -q", timeout=600)
        if not collected.ok:
            probe_out.append(collected.stdout + collected.stderr)
        if probe_out:
            return _fail("install", "env_broken", "\n".join(probe_out), install_ok=True)

        # RUNG 3/4 — actually run the suite (bounded; network optionally cut).
        if isolate_network:
            subprocess.run(_disconnect_network_cmd(box.name), capture_output=True, text=True, timeout=60)
        run = box.run(f"{cd} && {_PYTEST_ENV} python3 -m pytest -q", timeout=test_timeout)
        tests_ran, tests_passed, reason = classify_pytest_result(run.returncode)
        highest = "tests_passed" if tests_passed else ("tests_ran" if tests_ran else "env_works")
        return LadderResult(
            install_ok=True, env_works=True, tests_ran=tests_ran, tests_passed=tests_passed,
            highest_rung=highest, reason=reason,
            first_failure=None if tests_passed else first_failure_evidence(run.stdout + run.stderr),
            gaps=() if tests_ran else classify_execution_failures(run.stdout + run.stderr),
        )
```

Note: `_MountedContainer.run` shells via `docker exec ... sh -c`, so `box.run` returns a `CommandResult` whose `.returncode` is pytest's real exit code (used by `classify_pytest_result`).

- [ ] **Step 4: Run tests, verify they pass**

Run: `python3 -m pytest tests/eval/build_script_eval/test_replay_ladder.py -q`
Expected: the pure test PASSES; the docker test PASSES if Docker is available, else SKIPS. Note in the commit which happened.

- [ ] **Step 5: Commit**

```bash
git add src/eval/build_script_eval/replay.py tests/eval/build_script_eval/test_replay_ladder.py
git commit -m "feat(build-script-eval): replay ladder (install->env_works->tests_ran->tests_passed) with network isolation"
```

---

## Task 3: Per-repo orchestration + scorecard assembly

**Files:**
- Modify: `src/eval/build_script_eval/scorecard.py` (append the orchestration half)
- Test: `tests/eval/build_script_eval/test_scorecard.py` (add assembly tests)

**Interfaces:**
- Produces:
  - `_assemble_scorecard(full_name, stratum, feasible, image, minor, graph, static_ok, top_import, ladder) -> dict` — pure; the per-repo JSON.
  - `score_repo(repo_dir: str, spec) -> dict` — docker orchestration: base image ▸ construction-only build ▸ render ▸ static gate ▸ ladder ▸ assemble.
- Consumes: `coverage.{base_image_for_repo, build_graph_construction_only, apt_names_in_graph, package_versions_in_graph}`, `render_fidelity.check_render`, `build_script.render_build_script`, `replay.run_replay_ladder`, `corpus.RepoSpec`.

- [ ] **Step 1: Write the failing assembly test**

```python
# add to tests/eval/build_script_eval/test_scorecard.py
from src.eval.build_script_eval.scorecard import _assemble_scorecard


class _FakeGraph:
    nodes = ()


def test_assemble_scorecard_pass_row(monkeypatch):
    import src.eval.build_script_eval.scorecard as sc
    monkeypatch.setattr(sc, "apt_names_in_graph", lambda g: frozenset({"libpq-dev"}))
    monkeypatch.setattr(sc, "package_versions_in_graph", lambda g: {"psycopg2": "2.9.9"})
    ladder = _ladder(install_ok=True, env_works=True, tests_ran=True, tests_passed=False,
                     highest_rung="tests_ran", reason="tests_failed")
    row = _assemble_scorecard(
        "psycopg/psycopg2", "S_syslib", True, "python:3.11-slim", "3.11",
        _FakeGraph(), True, "psycopg2", ladder,
    )
    assert row["repo"] == "psycopg/psycopg2"
    assert row["stratum"] == "S_syslib"
    assert row["first_pass_env_works"] is True          # headline gate
    assert row["attribution"] == "pass"
    assert row["highest_rung"] == "tests_ran"
    assert row["predicted_apt"] == ["libpq-dev"]
    assert row["feasible"] is True
    # coverage.missing_node_clusters reads this exact key:
    assert "execution_missing" in row


def test_assemble_scorecard_system_gap_row(monkeypatch):
    import src.eval.build_script_eval.scorecard as sc
    monkeypatch.setattr(sc, "apt_names_in_graph", lambda g: frozenset())
    monkeypatch.setattr(sc, "package_versions_in_graph", lambda g: {})
    ladder = _ladder(install_ok=True, env_works=False, tests_ran=False, tests_passed=False,
                     highest_rung="install", reason="env_broken",
                     gaps=({"tier": "SYSTEM_LIB", "id": "libpq.so.5", "evidence": "cannot open"},))
    row = _assemble_scorecard("x/y", "S_syslib", True, "python:3.11-slim", "3.11",
                              _FakeGraph(), True, "y", ladder)
    assert row["first_pass_env_works"] is False
    assert row["attribution"] == "system_gap"
    assert [g["id"] for g in row["system_gaps"]] == ["libpq.so.5"]
    assert row["execution_missing"] == list(ladder.gaps)
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python3 -m pytest tests/eval/build_script_eval/test_scorecard.py -k assemble -q`
Expected: FAIL (`_assemble_scorecard` undefined).

- [ ] **Step 3: Append the orchestration half to `scorecard.py`**

```python
# --- append to src/eval/build_script_eval/scorecard.py ---

from python_deps.depgraph.build_script import render_build_script  # noqa: E402
from src.eval.build_script_eval.replay import run_replay_ladder  # noqa: E402
from src.eval.graph_fidelity.render_fidelity import check_render  # noqa: E402
from src.eval.language_package_eval.coverage import (  # noqa: E402
    apt_names_in_graph, base_image_for_repo, build_graph_construction_only,
    package_versions_in_graph, top_level_import_name,
)


def _static_ok(fidelity) -> bool:
    """The render pre-gate: valid bash (None = no bash on host ⇒ don't penalize),
    single emit, topo order, all reciped nodes emitted."""
    return (
        fidelity.valid_bash is not False
        and fidelity.single_emit
        and fidelity.topo_order_ok
        and fidelity.all_reciped_emitted
    )


def _assemble_scorecard(full_name, stratum, feasible, image, minor, graph,
                        static_ok, top_import, ladder) -> dict:
    """Pure per-repo scorecard row. `execution_missing` is the exact key
    `coverage.missing_node_clusters` consumes (gaps that broke the env)."""
    language_gaps, system_gaps = extract_gaps(ladder.gaps)
    attribution = attribute_failure(
        ladder, static_ok=static_ok, top_import=top_import, feasible=feasible
    )
    return {
        "repo": full_name,
        "stratum": stratum,
        "feasible": feasible,
        "base_image": image,
        "target_python": minor,
        "predicted_apt": sorted(apt_names_in_graph(graph)),
        "predicted_packages": sorted(package_versions_in_graph(graph)),
        "static_render_ok": static_ok,
        "first_pass_env_works": env_works_passed(ladder),
        "install_ok": ladder.install_ok,
        "env_works": ladder.env_works,
        "tests_ran": ladder.tests_ran,
        "tests_passed": ladder.tests_passed,
        "highest_rung": ladder.highest_rung,
        "ladder_reason": ladder.reason,
        "attribution": attribution,
        "language_gaps": list(language_gaps),
        "system_gaps": list(system_gaps),
        # SERVICE gaps are out of scope, so execution_missing (what
        # coverage.missing_node_clusters reads) is the SERVICE-free union:
        "execution_missing": [*language_gaps, *system_gaps],
        "first_failure": ladder.first_failure,
    }


def score_repo(repo_dir: str, spec) -> dict:
    """Full per-repo pipeline (docker). `spec` is a corpus.RepoSpec."""
    image, minor, _reason = base_image_for_repo(repo_dir)
    graph = build_graph_construction_only(repo_dir, image, minor)
    script = render_build_script(graph, ())
    static_ok = _static_ok(check_render(graph, script))
    top_import = spec.top_import or top_level_import_name(repo_dir)
    ladder = run_replay_ladder(
        repo_dir, image, script, top_import, isolate_network=not spec.network_in_tests,
    )
    return _assemble_scorecard(
        spec.full_name, spec.stratum, spec.feasible, image, minor, graph,
        static_ok, top_import, ladder,
    )
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python3 -m pytest tests/eval/build_script_eval/test_scorecard.py -q`
Expected: PASS (all — Task 1 tests + the two assembly tests).

- [ ] **Step 5: Commit**

```bash
git add src/eval/build_script_eval/scorecard.py tests/eval/build_script_eval/test_scorecard.py
git commit -m "feat(build-script-eval): per-repo orchestration + pure scorecard assembly"
```

---

## Task 4: Stratified aggregate report

**Files:**
- Create: `src/eval/build_script_eval/report.py`
- Test: `tests/eval/build_script_eval/test_report.py`

**Interfaces:**
- Produces: `aggregate(scorecards: list[dict]) -> dict`; `render_report_md(agg: dict, scorecards: list[dict]) -> str`.
- Consumes: `coverage.missing_node_clusters`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/build_script_eval/test_report.py
from src.eval.build_script_eval.report import aggregate, render_report_md


def _row(**kw):
    base = dict(repo="o/r", stratum="S_control", feasible=True, first_pass_env_works=True,
                install_ok=True, env_works=True, tests_ran=True, tests_passed=True,
                highest_rung="tests_passed", attribution="pass", predicted_apt=[],
                execution_missing=[], language_gaps=[], system_gaps=[])
    base.update(kw)
    return base


def test_headline_rate_overall_and_per_stratum():
    cards = [
        _row(stratum="S_control", first_pass_env_works=True),
        _row(stratum="S_syslib", first_pass_env_works=True),
        _row(stratum="S_syslib", first_pass_env_works=False, attribution="system_gap",
             system_gaps=[{"tier": "SYSTEM_LIB", "id": "libpq.so.5", "evidence": ""}],
             execution_missing=[{"tier": "SYSTEM_LIB", "id": "libpq.so.5", "evidence": ""}]),
    ]
    agg = aggregate(cards)
    assert agg["headline_env_works"]["overall"] == (2, 3)          # 2 of 3
    assert agg["headline_env_works"]["S_syslib"] == (1, 2)
    assert agg["headline_env_works"]["S_control"] == (1, 1)


def test_infeasible_excluded_from_denominator():
    cards = [_row(feasible=True, first_pass_env_works=True),
             _row(feasible=False, first_pass_env_works=False, attribution="infeasible")]
    agg = aggregate(cards)
    assert agg["headline_env_works"]["overall"] == (1, 1)


def test_attribution_histogram_and_ladder_funnel():
    cards = [_row(attribution="pass", tests_passed=True),
             _row(attribution="system_gap", first_pass_env_works=False, env_works=False,
                  tests_ran=False, tests_passed=False)]
    agg = aggregate(cards)
    assert agg["attribution_histogram"]["pass"] == 1
    assert agg["attribution_histogram"]["system_gap"] == 1
    assert agg["ladder_funnel"]["install_ok"] == 2
    assert agg["ladder_funnel"]["env_works"] == 1
    assert agg["ladder_funnel"]["tests_passed"] == 1


def test_report_md_has_headline_and_caveat():
    md = render_report_md(aggregate([_row()]), [_row()])
    assert "First-pass env-works" in md
    assert "tests_passed" in md and "service" in md.lower()   # the confound caveat
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python3 -m pytest tests/eval/build_script_eval/test_report.py -q`
Expected: FAIL (`ModuleNotFoundError: src.eval.build_script_eval.report`).

- [ ] **Step 3: Write the report**

```python
# src/eval/build_script_eval/report.py
"""Stratified aggregate report for the e2e build-script eval. Pure. Headline =
first-pass env-works rate (overall + per stratum); plus the replay-ladder funnel,
attribution histogram, and gap clusters. tests_passed is reported with a loud
service/config confound caveat — never a gate.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.eval.language_package_eval.coverage import missing_node_clusters  # noqa: E402

_RUNGS = ("install_ok", "env_works", "tests_ran", "tests_passed")
_TESTS_PASSED_CAVEAT = (
    "tests_passed is a CAVEATED diagnostic, never the headline: it depends on the "
    "service/config tier (live Postgres/Redis, fixtures, network) which is OUT OF "
    "SCOPE until service detection lands, so a low tests_passed is frequently not a "
    "graph fault. tests_ran is the clean, service-independent env-quality signal."
)


def _feasible(cards):
    return [c for c in cards if c.get("feasible")]


def _rate(cards, key):
    passed = sum(1 for c in cards if c.get(key))
    return (passed, len(cards))


def aggregate(scorecards: list[dict]) -> dict:
    """Headline + funnel + histogram + clusters. Headline denominator excludes
    infeasible repos; funnel/histogram count all scored repos."""
    feasible = _feasible(scorecards)
    strata = sorted({c["stratum"] for c in scorecards})
    headline = {"overall": _rate(feasible, "first_pass_env_works")}
    for s in strata:
        headline[s] = _rate([c for c in feasible if c["stratum"] == s], "first_pass_env_works")

    funnel = {rung: sum(1 for c in scorecards if c.get(rung)) for rung in _RUNGS}
    histogram = dict(Counter(c.get("attribution", "unknown") for c in scorecards))

    apt_safety = [
        {"repo": c["repo"], "stratum": c["stratum"], "predicted_apt": c.get("predicted_apt", [])}
        for c in scorecards
        if c["stratum"] == "S_control" and c.get("predicted_apt")   # over-prediction on a control
    ]
    return {
        "headline_env_works": headline,
        "ladder_funnel": funnel,
        "attribution_histogram": histogram,
        "gap_clusters": list(missing_node_clusters(scorecards)),
        "control_overprediction": apt_safety,
        "n_scored": len(scorecards),
        "n_feasible": len(feasible),
    }


def _fmt_rate(pair) -> str:
    passed, total = pair
    return f"{passed}/{total} ({passed / total:.0%})" if total else "n/a (0 feasible)"


def render_report_md(agg: dict, scorecards: list[dict]) -> str:
    lines = ["# E2E Build-Script Effectiveness Report", ""]
    lines.append(f"Corpus: {agg['n_scored']} scored ({agg['n_feasible']} feasible).")
    lines += ["", "## First-pass env-works (HEADLINE)", "", "| Scope | Rate |", "|---|---|"]
    lines.append(f"| overall | {_fmt_rate(agg['headline_env_works']['overall'])} |")
    for s in sorted(k for k in agg["headline_env_works"] if k != "overall"):
        lines.append(f"| {s} | {_fmt_rate(agg['headline_env_works'][s])} |")

    lines += ["", "## Replay-ladder funnel", "", "| Rung | Repos |", "|---|---|"]
    for rung in _RUNGS:
        lines.append(f"| {rung} | {agg['ladder_funnel'][rung]} |")
    lines += ["", f"> {_TESTS_PASSED_CAVEAT}"]

    lines += ["", "## Failure attribution", "", "| Label | Count |", "|---|---|"]
    for label, n in sorted(agg["attribution_histogram"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {label} | {n} |")

    lines += ["", "## Gap clusters (fix-next, ranked)", ""]
    if agg["gap_clusters"]:
        for i, c in enumerate(agg["gap_clusters"], 1):
            lines.append(f"{i}. **{c['tier']}** `{c['id']}` — {c['count']} repo(s): {', '.join(c['repos'])}")
    else:
        lines.append("(none)")

    lines += ["", "## Over-prediction on control repos (apt-safety)", ""]
    if agg["control_overprediction"]:
        for c in agg["control_overprediction"]:
            lines.append(f"- **{c['repo']}** predicted apt: {', '.join(c['predicted_apt'])} (control should be empty)")
    else:
        lines.append("(none — control strata predicted no apt)")

    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python3 -m pytest tests/eval/build_script_eval/test_report.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/eval/build_script_eval/report.py tests/eval/build_script_eval/test_report.py
git commit -m "feat(build-script-eval): stratified aggregate report (headline + funnel + attribution + clusters)"
```

---

## Task 5: Fetch + CLI

**Files:**
- Create: `src/eval/build_script_eval/fetch.py`
- Create: `src/eval/build_script_eval/__main__.py`

**Interfaces:**
- Produces: `fetch.smoke_root() -> Path`; `fetch.fetch_repo(spec, *, smoke_root) -> Path`; `fetch.fetch_corpus(specs, *, smoke_root) -> list[Path]`.
- CLI: `python3 -m src.eval.build_script_eval --fetch|--run|--score [--only a,b] [--stratum S_syslib]`.

- [ ] **Step 1: Verify each corpus ref exists (do this BEFORE writing fetch, adjust corpus.py if wrong)**

```bash
# Concrete verification — not a placeholder. Fix any ref in corpus.py that this shows as absent.
while read url ref; do echo "== $url $ref =="; git ls-remote --tags --heads "$url" "$ref" || echo "  MISSING: $ref"; done <<'EOF'
https://github.com/fastapi/typer 0.12.5
https://github.com/python-semantic-release/python-semantic-release v9.8.6
https://github.com/psycopg/psycopg2 2_9_9
https://github.com/pygraphviz/pygraphviz pygraphviz-1.12
https://github.com/lxml/lxml lxml-5.2.2
EOF
```
If a ref is MISSING, run `git ls-remote --tags <url> | tail` and pin `corpus.py`'s `ref` to a real tag.

- [ ] **Step 2: Write `fetch.py`**

```python
# src/eval/build_script_eval/fetch.py
"""Clone corpus repos at their pinned ref into a gitignored _smoke root (shallow,
single-ref). Reused by the CLI's --fetch and --run."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.eval.build_script_eval.corpus import RepoSpec  # noqa: E402

_SMOKE = _REPO_ROOT / "outputs" / "build_script_eval" / "_smoke"


def smoke_root() -> Path:
    _SMOKE.mkdir(parents=True, exist_ok=True)
    return _SMOKE


def fetch_repo(spec: RepoSpec, *, smoke_root: Path | None = None) -> Path:
    """Shallow-clone one repo at its pinned ref. Idempotent: an existing non-empty
    dir is left as-is (delete to re-fetch)."""
    root = smoke_root or _SMOKE
    root.mkdir(parents=True, exist_ok=True)
    dest = root / spec.name
    if dest.exists() and any(dest.iterdir()):
        return dest
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", spec.ref, spec.git_url, str(dest)],
        check=True, capture_output=True, text=True, timeout=600,
    )
    return dest


def fetch_corpus(specs, *, smoke_root: Path | None = None) -> list[Path]:
    return [fetch_repo(s, smoke_root=smoke_root) for s in specs]
```

- [ ] **Step 3: Write `__main__.py`**

```python
# src/eval/build_script_eval/__main__.py
"""CLI for the e2e build-script eval.

  python3 -m src.eval.build_script_eval --fetch [--only a,b] [--stratum S_syslib]
  python3 -m src.eval.build_script_eval --run   [--only ...] [--stratum ...]
  python3 -m src.eval.build_script_eval --score            # re-aggregate existing scorecards
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.eval.build_script_eval.corpus import select  # noqa: E402
from src.eval.build_script_eval.fetch import fetch_corpus, smoke_root  # noqa: E402
from src.eval.build_script_eval.report import aggregate, render_report_md  # noqa: E402
from src.eval.build_script_eval.scorecard import score_repo  # noqa: E402

_OUT = _REPO_ROOT / "outputs" / "build_script_eval"


def _csv(s: str) -> frozenset[str]:
    return frozenset(tok for tok in (s or "").split(",") if tok.strip())


def _repo_id(full_name: str) -> str:
    return full_name.replace("/", "__")


def main() -> int:
    ap = argparse.ArgumentParser(prog="build_script_eval")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--score", action="store_true", help="re-aggregate existing scorecards")
    ap.add_argument("--only", default="", help="comma-sep repo names")
    ap.add_argument("--stratum", default="", help="comma-sep strata (S_control,S_syslib)")
    args = ap.parse_args()

    specs = select(only=_csv(args.only), strata=_csv(args.stratum))
    _OUT.mkdir(parents=True, exist_ok=True)
    root = smoke_root()

    if args.fetch:
        paths = fetch_corpus(specs, smoke_root=root)
        print(f"fetched {len(paths)} repos into {root}")

    if args.run:
        print(f"scoring {len(specs)} repos (selected)")
        for spec in specs:
            repo_dir = root / spec.name
            if not repo_dir.exists():
                print(f"SKIP {spec.name}: not fetched (run --fetch first)")
                continue
            try:
                card = score_repo(str(repo_dir), spec)
            except Exception as exc:  # noqa: BLE001 — one repo must not abort the corpus
                card = {"repo": spec.full_name, "stratum": spec.stratum, "feasible": spec.feasible,
                        "first_pass_env_works": False, "attribution": "unknown",
                        "execution_missing": [], "predicted_apt": [],
                        "error": f"{type(exc).__name__}: {exc}"}
            (_OUT / f"{_repo_id(spec.full_name)}.json").write_text(
                json.dumps(card, indent=2, sort_keys=True) + "\n")
            print(f"  {spec.name}: env_works={card.get('first_pass_env_works')} "
                  f"attribution={card.get('attribution')} rung={card.get('highest_rung')}")

    if args.run or args.score:
        cards = [json.loads(p.read_text()) for p in sorted(_OUT.glob("*__*.json"))]
        agg = aggregate(cards)
        (_OUT / "report.md").write_text(render_report_md(agg, cards))
        print(json.dumps(agg["headline_env_works"], indent=2))
        print(f"wrote {_OUT / 'report.md'}")

    if not (args.fetch or args.run or args.score):
        ap.error("nothing to do: pass --fetch, --run, and/or --score")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Smoke the CLI wiring (no docker, no network)**

```bash
# select-only path must not touch docker/network:
python3 -m src.eval.build_script_eval --score
# expect: writes outputs/build_script_eval/report.md over whatever scorecards exist (0 ok)
python3 -c "import sys; sys.path[:0]=['src','.']; \
from src.eval.build_script_eval.__main__ import _csv, _repo_id; \
assert _csv('a, b,') == frozenset({'a','b'}); assert _repo_id('o/r')=='o__r'; print('cli helpers OK')"
```
Expected: `report.md` written (empty/0-card aggregate is fine); `cli helpers OK`.

- [ ] **Step 5: Full eval-suite still green + commit**

```bash
python3 -m pytest tests/eval/build_script_eval -q
git add src/eval/build_script_eval/fetch.py src/eval/build_script_eval/__main__.py src/eval/build_script_eval/corpus.py
git commit -m "feat(build-script-eval): git fetch + CLI (--fetch/--run/--score, --only/--stratum)"
```

---

## Task 6: Acceptance run + validation + CHANGELOG (run-only)

**Files:**
- Run only (artifacts to gitignored `outputs/build_script_eval/`).
- Modify: `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md` (append entry).

**Interfaces:** none (integration run). Requires Docker.

- [ ] **Step 1: Fetch + run a fast spot-check first (1 control + 1 syslib)**

```bash
python3 -m src.eval.build_script_eval --fetch --only typer,psycopg2
python3 -m src.eval.build_script_eval --run   --only typer,psycopg2
```
Expected: `typer` → `first_pass_env_works=True`, `predicted_apt=[]` (control baseline). `psycopg2` → exercises the apt tier; if `env_works=False` the attribution must be `system_gap` (libpq), NOT `language_gap`.

- [ ] **Step 2: Validate the two invariants the eval exists to measure**

- **S_control over-prediction = 0:** every `S_control` row has `predicted_apt == []`. A nonzero apt tier on a control repo is an over-prediction regression (ties to the syslib plan's `apt == 0` invariant) — record it as a finding, do not silently pass.
- **S_syslib signal is real:** at least one `S_syslib` row either passes env_works *because* the graph predicted the right apt (inspect `predicted_apt`), or fails with `attribution == system_gap` (the detector under-covered) — never a silent `language_gap` masking a missing `.so`.

- [ ] **Step 3: Full corpus run**

```bash
python3 -m src.eval.build_script_eval --fetch
python3 -m src.eval.build_script_eval --run
cat outputs/build_script_eval/report.md
```
Expected: `report.md` with the headline env-works rate (overall + per stratum), the ladder funnel, attribution histogram, gap clusters, and the control over-prediction section. The `tests_passed` caveat is present.

- [ ] **Step 4: Full test suite unregressed**

```bash
python3 -m pytest tests/eval/build_script_eval -q
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
```
Expected: new module all green; the pre-existing suite count unchanged aside from the added `build_script_eval` tests (the module is additive; `coverage.py`/`render_fidelity.py` untouched).

- [ ] **Step 5: CHANGELOG + commit**

Append an Observation→Why→What→Verification entry to `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md`: the new e2e build-script eval (execution-only, no oracle), the headline env-works number per stratum, the S_control apt=0 over-prediction check, and which repos exercised the syslib tier.

```bash
git add docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md
git commit -m "docs(changelog): e2e build-script effectiveness eval — first-pass env-works + syslib-tier validation"
```

---

## Self-Review

**Spec coverage:**
- Scope Python-only / SERVICE-CONFIG excluded / execution-only — Global Constraints + Tasks 1-3 (no oracle imports; SERVICE dropped in `extract_gaps`). ✓
- Per-repo pipeline (base image ▸ construction-only ▸ render ▸ static gate ▸ replay) — Task 3 `score_repo`. ✓
- Replay ladder (install ▸ env_works ▸ tests_ran ▸ tests_passed), bounded, network-isolated — Task 2. ✓
- Headline env-works + per-stratum + ladder funnel — Task 4 `aggregate`. ✓
- Language/system attribution + gap clusters (no recall fraction) — Task 1 `attribute_failure`/`extract_gaps`, Task 4 clusters. ✓
- apt-safety / over-prediction on control — Task 4 `control_overprediction`, Task 6 Step 2. ✓
- Stratified corpus (S_control/S_syslib), no held-out recipe — Task 0. ✓
- Reuse (coverage/render_fidelity unchanged) — imports only, asserted in Global Constraints. ✓
- Outputs gitignored — verified (`outputs/` rule). ✓

**Placeholder scan:** corpus `ref`s are concrete tags with an explicit `git ls-remote` verification step (Task 5 Step 1), not TODOs. No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows full code.

**Type consistency:** `LadderResult` fields identical across Tasks 1/2/3; `run_replay_ladder` signature matches its call in `score_repo`; `_assemble_scorecard` arg order matches both tests and `score_repo`; `execution_missing` key name matches `coverage.missing_node_clusters`'s reader and Task 4's cluster call; `attribute_failure` returns the label set the report histogram displays.
