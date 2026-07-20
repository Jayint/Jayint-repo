# Deterministic EnvState Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the v1 Maintainer's LLM substring-grounding of `installed` with a deterministic probe (`pip list --format=freeze`) + host-side manifest parse, so the world model is state-grounded; narrow the Maintainer LLM to interpretation (`open_problems`, `resolved`, `notes`).

**Architecture:** Approach A — the orchestrator owns a read-only probe and a parsed manifest, and folds deterministic facts into the map (`apply_deterministic`) at cycle 0 and each cycle *before* the Maintainer. The Maintainer keeps no execution surface. Source of truth: `docs/superpowers/specs/2026-06-10-deterministic-envstate-snapshot-design.md`.

**Tech Stack:** Python 3.13, pytest, `tomllib` (stdlib), `packaging` (PyPA), Docker sandbox, frozen dataclasses.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/envstate/world_model.py` | modify | `env` field; `merge_map(env/build_system/language)`; `apply_deterministic`; `_derive_progress`; `_auto_resolve_problems`; serialization |
| `src/envstate/manifest.py` | create | host-side manifest parse → `ManifestResult(build_system, required)` |
| `src/envstate/snapshot.py` | create | read-only probe → `EnvSnapshot(installed, env)` |
| `src/envstate/extractor.py` | modify | `installed_pip` command → `pip list --format=freeze` |
| `src/envstate/maintainer.py` | modify | narrow prompt/schema; drop `_ground_installed`; add `resolved` |
| `src/envstate/orchestrator.py` | modify | `run_v1` gains `probe`/`manifest`; cycle-0 + per-cycle fold |
| `agent.py` | modify | `_run_v1` wires `parse_manifests` + `probe_env` |
| `requirements.txt` | modify | declare `packaging` |

**Dependency order:** world_model (foundation) → manifest + snapshot (leaves) → maintainer + orchestrator → agent. Tasks below follow this so each builds on a green predecessor.

---

## Task 1: world_model — `env` field, extended `merge_map`, serialization

**Files:**
- Modify: `src/envstate/world_model.py`
- Test: `tests/test_world_model_env.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_world_model_env.py
from src.envstate.world_model import (
    initial_map, merge_map, map_to_dict, map_from_dict, Fact,
)


def _base():
    return initial_map(
        base_image="python:3.12", workdir="/app", language="python",
        build_system="unknown", repo_layout=("pyproject.toml",),
    )


def test_initial_map_has_empty_env():
    m = _base()
    assert m.env == {}


def test_merge_map_replaces_env_and_defensive_copies():
    m = _base()
    src = {"python_version": "Python 3.12.1"}
    m2 = merge_map(m, env=src)
    assert m2.env == {"python_version": "Python 3.12.1"}
    src["python_version"] = "MUTATED"
    assert m2.env["python_version"] == "Python 3.12.1"  # not aliased


def test_merge_map_replaces_build_system_and_language():
    m = _base()
    m2 = merge_map(m, build_system="poetry", language="python 3.12.1")
    assert m2.build_system == "poetry"
    assert m2.language == "python 3.12.1"
    assert m.build_system == "unknown"  # original unchanged (frozen)


def test_env_round_trips_through_dict():
    m = merge_map(_base(), env={"arch": "x86_64"}, installed=(Fact("flask", "3.0.0"),))
    restored = map_from_dict(map_to_dict(m))
    assert restored.env == {"arch": "x86_64"}
    assert restored.installed == (Fact("flask", "3.0.0"),)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_world_model_env.py -v`
Expected: FAIL (`merge_map() got an unexpected keyword argument 'env'`)

- [ ] **Step 3: Add the `env` field**

In `src/envstate/world_model.py`, add `env` to `WorldModelMap` (after `notes`, since both have defaults):

```python
@dataclasses.dataclass(frozen=True)
class WorldModelMap:
    base_image: str
    workdir: str
    language: str
    build_system: str
    repo_layout: tuple[str, ...]
    required: tuple[Fact, ...]
    installed: tuple[Fact, ...]
    open_problems: tuple[OpenProblem, ...]
    progress: dict[str, bool]
    done_flag: bool = False
    notes: tuple[str, ...] = ()
    env: dict[str, str] = dataclasses.field(default_factory=dict)   # NEW: scalar probe facts
```

- [ ] **Step 4: Extend `merge_map`**

Replace the `merge_map` function body with one that also handles `env`, `build_system`, `language`:

```python
def merge_map(
    current: WorldModelMap,
    *,
    installed: tuple[Fact, ...] | None = None,
    open_problems: tuple[OpenProblem, ...] | None = None,
    progress: dict[str, bool] | None = None,
    done_flag: bool | None = None,
    notes: tuple[str, ...] | None = None,
    required: tuple[Fact, ...] | None = None,
    env: dict[str, str] | None = None,
    build_system: str | None = None,
    language: str | None = None,
) -> WorldModelMap:
    """Return a new WorldModelMap with only the supplied keyword fields replaced.

    progress and env are always copied so callers never share a live dict.
    Never raises.
    """
    return dataclasses.replace(
        current,
        installed=installed if installed is not None else current.installed,
        open_problems=open_problems if open_problems is not None else current.open_problems,
        progress=dict(progress) if progress is not None else dict(current.progress),
        done_flag=done_flag if done_flag is not None else current.done_flag,
        notes=notes if notes is not None else current.notes,
        required=required if required is not None else current.required,
        env=dict(env) if env is not None else dict(current.env),
        build_system=build_system if build_system is not None else current.build_system,
        language=language if language is not None else current.language,
    )
```

- [ ] **Step 5: Update serialization**

In `map_to_dict`, add `"env": dict(m.env)` to the returned dict. In `map_from_dict`, add `env=dict(d.get("env", {}))` to the `WorldModelMap(...)` constructor call.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_world_model_env.py tests/test_world_model.py -v`
Expected: PASS (new tests green; existing `test_world_model.py` still green)

- [ ] **Step 7: Commit**

```bash
git add src/envstate/world_model.py tests/test_world_model_env.py
git commit -m "feat(world-model): add env field + extend merge_map (env/build_system/language)"
```

---

## Task 2: world_model — `_derive_progress`

**Files:**
- Modify: `src/envstate/world_model.py`
- Test: `tests/test_world_model_progress.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_world_model_progress.py
from src.envstate.world_model import (
    initial_map, merge_map, _derive_progress, Fact, OpenProblem, _PROGRESS_LAYERS,
)


def _map(**kw):
    base = initial_map(
        base_image="python:3.12", workdir="/app", language="python",
        build_system="pip", repo_layout=(), )
    return merge_map(base, **kw)


def test_base_true_when_base_image_set():
    m = _map()
    p = _derive_progress(m.progress, m)
    assert p["base"] is True


def test_runtime_true_when_python_version_present():
    m = _map(env={"python_version": "Python 3.12.1"})
    assert _derive_progress(m.progress, m)["runtime"] is True


def test_deps_true_when_required_subset_of_installed():
    m = _map(required=(Fact("flask"),), installed=(Fact("flask", "3.0.0"),))
    assert _derive_progress(m.progress, m)["deps"] is True


def test_deps_false_when_required_not_satisfied():
    m = _map(required=(Fact("flask"),), installed=())
    assert _derive_progress(m.progress, m)["deps"] is False


def test_tests_true_when_done_flag():
    m = _map(done_flag=True)
    assert _derive_progress(m.progress, m)["tests"] is True


def test_system_false_when_unresolved_system_problem():
    m = _map(open_problems=(OpenProblem("libpq missing", "x", "system"),))
    assert _derive_progress(m.progress, m)["system"] is False


def test_progress_is_monotonic():
    prev = {layer: False for layer in _PROGRESS_LAYERS}
    prev["deps"] = True  # previously achieved
    m = _map(required=(Fact("flask"),), installed=())  # deps would compute False now
    assert _derive_progress(prev, m)["deps"] is True  # stays True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_world_model_progress.py -v`
Expected: FAIL (`cannot import name '_derive_progress'`)

- [ ] **Step 3: Implement `_derive_progress`**

Add to `src/envstate/world_model.py` (after `merge_map`):

```python
def _derive_progress(prev: dict[str, bool], m: WorldModelMap) -> dict[str, bool]:
    """Deterministically compute layer progress from facts; monotonic vs prev.

    Clean-signal layers: base/runtime/deps/tests. Signal-less layers
    (system/build) are complete unless an unresolved open_problem targets
    them. OR-merged with prev so a layer never flips True->False mid-run.
    """
    installed_lower = {f.name.lower() for f in m.installed}
    deps_ok = bool(m.required) and all(
        r.name.lower() in installed_lower for r in m.required
    )
    open_layers = {p.layer for p in m.open_problems if not p.out_of_scope}
    computed = {
        "base": bool(m.base_image),
        "system": "system" not in open_layers,
        "runtime": bool(m.env.get("python_version")),
        "deps": deps_ok,
        "build": "build" not in open_layers,
        "tests": bool(m.done_flag),
    }
    return {
        layer: bool(prev.get(layer, False)) or bool(computed.get(layer, False))
        for layer in _PROGRESS_LAYERS
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_world_model_progress.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/envstate/world_model.py tests/test_world_model_progress.py
git commit -m "feat(world-model): deterministic _derive_progress (monotonic)"
```

---

## Task 3: world_model — `_auto_resolve_problems`

**Files:**
- Modify: `src/envstate/world_model.py`
- Test: `tests/test_world_model_autoresolve.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_world_model_autoresolve.py
from src.envstate.world_model import _auto_resolve_problems, Fact, OpenProblem


def test_drops_problem_when_package_installed():
    problems = (OpenProblem("ModuleNotFoundError: flask", "missing flask", "deps"),)
    kept = _auto_resolve_problems(problems, (Fact("flask", "3.0.0"),))
    assert kept == ()


def test_match_is_case_insensitive():
    problems = (OpenProblem("ImportError: Flask not found", "x", "deps"),)
    kept = _auto_resolve_problems(problems, (Fact("flask"),))
    assert kept == ()


def test_keeps_unrelated_problem():
    problems = (OpenProblem("pg_config not found", "needs libpq", "system"),)
    kept = _auto_resolve_problems(problems, (Fact("flask"),))
    assert kept == problems


def test_keeps_problem_when_nothing_installed():
    problems = (OpenProblem("ModuleNotFoundError: flask", "x", "deps"),)
    assert _auto_resolve_problems(problems, ()) == problems
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_world_model_autoresolve.py -v`
Expected: FAIL (`cannot import name '_auto_resolve_problems'`)

- [ ] **Step 3: Implement `_auto_resolve_problems`**

Add to `src/envstate/world_model.py`:

```python
def _auto_resolve_problems(
    open_problems: tuple[OpenProblem, ...],
    installed: tuple[Fact, ...],
) -> tuple[OpenProblem, ...]:
    """Drop a problem whose package name appears (case-insensitive) in its
    signature once that package is in `installed`.

    Conservative exact-name match: covers pip ModuleNotFoundError cases.
    Package-vs-import-name mismatches (psycopg2 vs psycopg2-binary) are left
    for the Maintainer's `resolved` list. Never raises.
    """
    if not installed:
        return open_problems
    names = [f.name.lower() for f in installed if f.name]
    kept: list[OpenProblem] = []
    for p in open_problems:
        sig = p.signature.lower()
        if any(name in sig for name in names):
            continue  # resolved
        kept.append(p)
    return tuple(kept)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_world_model_autoresolve.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/envstate/world_model.py tests/test_world_model_autoresolve.py
git commit -m "feat(world-model): _auto_resolve_problems (pip-installed clears problem)"
```

---

## Task 4: world_model — `apply_deterministic`

**Files:**
- Modify: `src/envstate/world_model.py`
- Test: `tests/test_apply_deterministic.py`

**Note on typing:** `apply_deterministic` takes the snapshot and manifest as duck-typed `Any` (reads `.installed`, `.env`, `.build_system`, `.required`). It must NOT import `snapshot`/`manifest` — those import `world_model`, and importing back would be circular.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apply_deterministic.py
from types import SimpleNamespace
from src.envstate.world_model import (
    initial_map, merge_map, apply_deterministic, Fact, OpenProblem,
)


def _snap(installed=(), env=None):
    return SimpleNamespace(installed=installed, env=env or {})


def _man(build_system="pip", required=()):
    return SimpleNamespace(build_system=build_system, required=required)


def _base():
    return initial_map(
        base_image="python:3.12", workdir="/app", language="python",
        build_system="unknown", repo_layout=(), )


def test_replaces_facts_from_snapshot_and_manifest():
    snap = _snap(installed=(Fact("flask", "3.0.0"),), env={"arch": "x86_64", "python_version": "Python 3.12.1"})
    man = _man(build_system="poetry", required=(Fact("flask"),))
    m = apply_deterministic(_base(), snap, man)
    assert m.installed == (Fact("flask", "3.0.0"),)
    assert m.build_system == "poetry"
    assert m.required == (Fact("flask"),)
    assert m.env["arch"] == "x86_64"
    assert m.language == "Python 3.12.1"


def test_empty_env_degrades_keeps_prior_facts():
    prior = merge_map(_base(), installed=(Fact("flask", "3.0.0"),), env={"arch": "x86_64"})
    snap = _snap(installed=(), env={})   # probe failure signal
    m = apply_deterministic(prior, snap, _man())
    assert m.installed == (Fact("flask", "3.0.0"),)
    assert m.env == {"arch": "x86_64"}


def test_auto_resolves_problem_and_derives_progress():
    prior = merge_map(_base(), open_problems=(OpenProblem("ModuleNotFoundError: flask", "x", "deps"),))
    snap = _snap(installed=(Fact("flask", "3.0.0"),), env={"python_version": "Python 3.12.1"})
    man = _man(build_system="pip", required=(Fact("flask"),))
    m = apply_deterministic(prior, snap, man)
    assert m.open_problems == ()          # auto-resolved
    assert m.progress["deps"] is True     # required subset of installed
    assert m.progress["runtime"] is True  # python_version present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_apply_deterministic.py -v`
Expected: FAIL (`cannot import name 'apply_deterministic'`)

- [ ] **Step 3: Implement `apply_deterministic`**

Add to `src/envstate/world_model.py`:

```python
from typing import Any  # add to existing imports at top if not present


def apply_deterministic(
    current: WorldModelMap,
    snap: Any,   # EnvSnapshot (duck-typed: .installed, .env)
    man: Any,    # ManifestResult (duck-typed: .build_system, .required)
) -> WorldModelMap:
    """Fold deterministic facts into the map. Pure; never raises.

    REPLACES installed/env/build_system/required/language from snap+man.
    Empty snap.env => probe failed => keep prior installed/env (degrade).
    AUTO-RESOLVES pip problems; RECOMPUTES progress deterministically.
    Leaves notes/base_image/workdir/repo_layout/done_flag untouched.
    """
    if snap.env:  # non-empty env == probe succeeded (arch always reads on success)
        installed = tuple(snap.installed)
        env = dict(snap.env)
        language = snap.env.get("python_version") or current.language
    else:
        installed = current.installed
        env = dict(current.env)
        language = current.language

    build_system = (
        man.build_system
        if man.build_system and man.build_system != "unknown"
        else current.build_system
    )
    resolved = _auto_resolve_problems(current.open_problems, installed)

    interim = merge_map(
        current,
        installed=installed,
        env=env,
        required=tuple(man.required),
        build_system=build_system,
        language=language,
        open_problems=resolved,
    )
    progress = _derive_progress(current.progress, interim)
    return merge_map(interim, progress=progress)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_apply_deterministic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/envstate/world_model.py tests/test_apply_deterministic.py
git commit -m "feat(world-model): apply_deterministic fold (replace facts, resolve, derive progress)"
```

---

## Task 5: `manifest.py` — host-side manifest parse

**Files:**
- Create: `src/envstate/manifest.py`
- Test: `tests/test_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manifest.py
import os
from src.envstate.manifest import parse_manifests, ManifestResult


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return str(tmp_path)


def test_pip_requirements_and_includes(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask>=2.0\n# comment\n-r extra.txt\n")
    (tmp_path / "extra.txt").write_text("pytest\npsycopg2-binary==2.9.5\n")
    r = parse_manifests(str(tmp_path))
    assert r.build_system == "pip"
    names = {f.name.lower() for f in r.required}
    assert {"flask", "pytest", "psycopg2-binary"} <= names


def test_pyproject_pep621_and_backend(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["flask[async]>=2.0", "requests; python_version<\\"3.9\\""]\n'
        '[build-system]\nbuild-backend = "setuptools.build_meta"\n'
    )
    r = parse_manifests(str(tmp_path))
    assert r.build_system == "setuptools"
    assert "flask" in {f.name.lower() for f in r.required}
    assert "requests" in {f.name.lower() for f in r.required}


def test_poetry_detected(tmp_path):
    (tmp_path / "poetry.lock").write_text("")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = "^3.10"\nflask = "^2.0"\n'
    )
    r = parse_manifests(str(tmp_path))
    assert r.build_system == "poetry"
    assert "flask" in {f.name.lower() for f in r.required}
    assert "python" not in {f.name.lower() for f in r.required}


def test_malformed_pyproject_does_not_raise(tmp_path):
    (tmp_path / "pyproject.toml").write_text("this is not = valid toml [[[")
    r = parse_manifests(str(tmp_path))
    assert isinstance(r, ManifestResult)
    assert r.build_system in ("unknown", "setuptools", "pip")


def test_none_present_is_unknown(tmp_path):
    r = parse_manifests(str(tmp_path))
    assert r.build_system == "unknown"
    assert r.required == ()


def test_dedup_across_files(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    (tmp_path / "requirements-dev.txt").write_text("flask\npytest\n")
    r = parse_manifests(str(tmp_path))
    names = [f.name.lower() for f in r.required]
    assert names.count("flask") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_manifest.py -v`
Expected: FAIL (`No module named 'src.envstate.manifest'`)

- [ ] **Step 3: Implement `manifest.py`**

```python
# src/envstate/manifest.py
"""Host-side manifest parser (shallow). Reads declared deps + build system
from a checked-out repo. Pure, never raises. Uses tomllib + packaging.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

from packaging.requirements import Requirement

from src.envstate.world_model import Fact


@dataclass(frozen=True)
class ManifestResult:
    build_system: str             # poetry|pip|setuptools|hatchling|flit|pipenv|unknown
    required: tuple[Fact, ...]    # Fact(name, detail=specifier); declared names only


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _req_fact(spec: str) -> Fact | None:
    spec = spec.strip()
    if not spec or spec.startswith("#"):
        return None
    try:
        r = Requirement(spec)
        return Fact(name=r.name, detail=str(r.specifier))
    except Exception:
        token = spec.split(";")[0].split("#")[0].strip()
        for sep in ("==", ">=", "<=", "~=", ">", "<", "[", " "):
            token = token.split(sep)[0].strip()
        return Fact(name=token) if token else None


def _parse_requirements_txt(workplace: str, filename: str, seen: set[str]) -> list[Fact]:
    path = os.path.join(workplace, filename)
    if path in seen:
        return []
    seen.add(path)
    text = _read_text(path)
    if text is None:
        return []
    facts: list[Fact] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement"):
            parts = line.split(None, 1)
            if len(parts) == 2:
                facts.extend(_parse_requirements_txt(workplace, parts[1].strip(), seen))
            continue
        if line.startswith("-"):
            continue  # other pip flags (-e, --index-url, ...)
        f = _req_fact(line)
        if f:
            facts.append(f)
    return facts


def parse_manifests(workplace: str) -> ManifestResult:
    build_system = "unknown"
    facts: list[Fact] = []

    pyproject = None
    raw = _read_text(os.path.join(workplace, "pyproject.toml"))
    if raw is not None:
        try:
            pyproject = tomllib.loads(raw)
        except Exception:
            pyproject = None

    poetry = (pyproject or {}).get("tool", {}).get("poetry") if pyproject else None
    has_poetry_lock = os.path.exists(os.path.join(workplace, "poetry.lock"))
    has_pipfile = os.path.exists(os.path.join(workplace, "Pipfile"))
    has_setup = os.path.exists(os.path.join(workplace, "setup.py")) or \
        os.path.exists(os.path.join(workplace, "setup.cfg"))
    try:
        req_files = sorted(
            fn for fn in os.listdir(workplace)
            if fn.startswith("requirements") and fn.endswith(".txt")
        )
    except OSError:
        req_files = []

    # build_system precedence
    if has_poetry_lock or poetry:
        build_system = "poetry"
    elif pyproject and "build-system" in pyproject:
        backend = str(pyproject["build-system"].get("build-backend", ""))
        if "hatch" in backend:
            build_system = "hatchling"
        elif "flit" in backend:
            build_system = "flit"
        elif "poetry" in backend:
            build_system = "poetry"
        else:
            build_system = "setuptools"
    elif has_pipfile:
        build_system = "pipenv"
    elif req_files:
        build_system = "pip"
    elif has_setup:
        build_system = "setuptools"

    # required extraction
    if pyproject:
        for dep in (pyproject.get("project", {}).get("dependencies") or []):
            f = _req_fact(str(dep))
            if f:
                facts.append(f)
        if poetry:
            for name, val in (poetry.get("dependencies") or {}).items():
                if name.lower() == "python":
                    continue
                facts.append(Fact(name=name, detail=val if isinstance(val, str) else ""))

    seen: set[str] = set()
    for fn in req_files:
        facts.extend(_parse_requirements_txt(workplace, fn, seen))

    if has_pipfile:
        ptext = _read_text(os.path.join(workplace, "Pipfile"))
        if ptext is not None:
            try:
                pip = tomllib.loads(ptext)
                for name, val in (pip.get("packages") or {}).items():
                    detail = val if isinstance(val, str) and val != "*" else ""
                    facts.append(Fact(name=name, detail=detail))
            except Exception:
                pass

    # dedup by lowercased name (keep first)
    seen_names: set[str] = set()
    deduped: list[Fact] = []
    for f in facts:
        key = f.name.lower()
        if key and key not in seen_names:
            seen_names.add(key)
            deduped.append(f)

    return ManifestResult(build_system=build_system, required=tuple(deduped))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_manifest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/envstate/manifest.py tests/test_manifest.py
git commit -m "feat(envstate): manifest.py shallow parser (tomllib + packaging)"
```

---

## Task 6: `snapshot.py` + extractor command fix

**Files:**
- Modify: `src/envstate/extractor.py` (line 16)
- Create: `src/envstate/snapshot.py`
- Test: `tests/test_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snapshot.py
from src.envstate.snapshot import probe_env, EnvSnapshot


def _fake_exec(table):
    """Return an exec_readonly(cmd)->(rc, out) backed by a substring table."""
    def run(cmd):
        for key, (rc, out) in table.items():
            if key in cmd:
                return rc, out
        return 1, ""
    return run


def test_parses_installed_and_env():
    table = {
        "pip list --format=freeze": (0, "flask==3.0.0\nsetuptools==69.0.0\n"),
        "python --version": (0, "Python 3.12.1"),
        "uname -m": (0, "x86_64"),
    }
    snap = probe_env(_fake_exec(table))
    names = {f.name.lower(): f.detail for f in snap.installed}
    assert names["flask"] == "3.0.0"
    assert "setuptools" in names                # included via pip list --format=freeze
    assert snap.env["python_version"] == "Python 3.12.1"
    assert snap.env["arch"] == "x86_64"


def test_total_failure_returns_empty_snapshot():
    snap = probe_env(lambda cmd: (1, ""))
    assert snap == EnvSnapshot()
    assert snap.env == {}


def test_skips_malformed_freeze_lines():
    table = {"pip list --format=freeze": (0, "-e git+https://x\n\nflask==3.0.0\n"), "uname -m": (0, "x86_64")}
    snap = probe_env(_fake_exec(table))
    assert [f.name for f in snap.installed] == ["flask"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_snapshot.py -v`
Expected: FAIL (`No module named 'src.envstate.snapshot'`)

- [ ] **Step 3: Fix the extractor command**

In `src/envstate/extractor.py`, change the `installed_pip` entry (line 16):

```python
    "installed_pip": "pip list --format=freeze 2>/dev/null",
```

(was `"pip freeze 2>/dev/null"` — bare freeze excludes pip/setuptools/wheel, which causes a phantom-missing loop; see spec §4.2.)

- [ ] **Step 4: Implement `snapshot.py`**

```python
# src/envstate/snapshot.py
"""Read-only env probe -> EnvSnapshot(installed, env). Never raises.

Wraps extractor.run_extractor via sandbox.exec_readonly (no ledger / no
Dockerfile leak). env is empty ONLY on total probe failure (arch reads on
any healthy container), which apply_deterministic uses as the degrade signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.envstate.extractor import run_extractor, LIGHTWEIGHT_FIELDS
from src.envstate.world_model import Fact

_SNAPSHOT_FIELDS = LIGHTWEIGHT_FIELDS + ("which_python", "venv")


@dataclass(frozen=True)
class EnvSnapshot:
    installed: tuple[Fact, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


def _parse_installed(freeze_text: str) -> tuple[Fact, ...]:
    facts: list[Fact] = []
    for raw in freeze_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if "==" in line:
            name, _, ver = line.partition("==")
            name = name.strip()
            if name:
                facts.append(Fact(name=name, detail=ver.strip()))
    return tuple(facts)


def probe_env(exec_readonly: Callable[[str], tuple[int, str]]) -> EnvSnapshot:
    try:
        result = run_extractor(exec_readonly, _SNAPSHOT_FIELDS)
    except Exception:
        return EnvSnapshot()
    fields = result.fields  # only rc==0, non-empty entries
    installed = _parse_installed(fields.get("installed_pip", ""))
    env = {k: v for k, v in fields.items() if k != "installed_pip"}
    return EnvSnapshot(installed=installed, env=env)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_snapshot.py tests/test_envstate_extractor.py -v`
Expected: PASS (new tests green; existing extractor test still green)

- [ ] **Step 6: Commit**

```bash
git add src/envstate/snapshot.py src/envstate/extractor.py tests/test_snapshot.py
git commit -m "feat(envstate): snapshot.py probe + pip list --format=freeze (includes setuptools)"
```

---

## Task 7: Maintainer — narrow to `open_problems` + `resolved` + `notes`

**Files:**
- Modify: `src/envstate/maintainer.py`
- Test: `tests/test_maintainer_narrowed.py`
- Update: `tests/test_v1_maintainer.py` (regression — remove `_ground_installed` expectations)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_maintainer_narrowed.py
from types import SimpleNamespace
from src.envstate.maintainer import parse_v1_maintainer_reply
from src.envstate.world_model import (
    initial_map, merge_map, Fact, OpenProblem, CommandRecord, TaskReport,
)


def _map(**kw):
    base = initial_map(base_image="python:3.12", workdir="/app", language="python",
                       build_system="pip", repo_layout=())
    return merge_map(base, **kw)


def _report(cmds=(), status="done"):
    return TaskReport(task_goal="g", status=status, commands=tuple(cmds), learning="")


def test_resolved_drops_listed_problem():
    m = _map(open_problems=(OpenProblem("pg_config not found", "x", "system"),))
    text = '```json\n{"open_problems": [], "resolved": ["pg_config not found"], "notes": []}\n```'
    out = parse_v1_maintainer_reply(text, m, _report())
    assert out.open_problems == ()


def test_appends_new_problem_and_note():
    m = _map()
    text = '```json\n{"open_problems": [{"signature":"E1","interpretation":"i","layer":"deps"}], "resolved": [], "notes": ["careful"]}\n```'
    out = parse_v1_maintainer_reply(text, m, _report())
    assert out.open_problems[0].signature == "E1"
    assert "careful" in out.notes


def test_does_not_touch_installed_or_progress():
    m = _map(installed=(Fact("flask", "3.0.0"),), progress={"base": True, "system": False,
             "runtime": True, "deps": True, "build": False, "tests": False})
    text = '```json\n{"open_problems": [], "resolved": [], "notes": [], "installed": [{"name":"HACK"}], "progress": {"tests": true}}\n```'
    out = parse_v1_maintainer_reply(text, m, _report())
    assert out.installed == (Fact("flask", "3.0.0"),)   # stray installed ignored
    assert out.progress["tests"] is False                # stray progress ignored


def test_done_flag_fires_on_empty_llm_output():
    m = _map()
    report = _report(cmds=(CommandRecord("pytest --collect-only", 0, "ok"),))
    out = parse_v1_maintainer_reply("", m, report)
    assert out.done_flag is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_maintainer_narrowed.py -v`
Expected: FAIL (current `parse_v1_maintainer_reply` still grounds `installed` / merges `progress`, and ignores `resolved`)

- [ ] **Step 3: Rewrite `parse_v1_maintainer_reply` and trim helpers**

In `src/envstate/maintainer.py`: delete `_ground_installed` and `_all_output_text` (now unused). Replace `parse_v1_maintainer_reply` with:

```python
def parse_v1_maintainer_reply(
    text: str,
    current_map: WorldModelMap,
    report: TaskReport,
) -> WorldModelMap:
    """Parse the narrowed Maintainer reply: open_problems + resolved + notes.

    Facts (installed/progress/build_system/required/env) are owned by
    apply_deterministic and are NOT touched here. done_flag is structural
    (collect-only rc 0 in the report). On empty/unparseable input, only the
    structural done_flag rule applies.
    """
    parsed = extract_json_object(text) if text else None
    if not parsed:
        new_done = current_map.done_flag or _collect_only_passed(report)
        if new_done != current_map.done_flag:
            return merge_map(current_map, done_flag=new_done)
        return current_map

    # open_problems: append new (dedup by signature)
    new_problems = _parse_open_problems(parsed.get("open_problems") or [])
    existing_sigs = {p.signature for p in current_map.open_problems}
    merged = current_map.open_problems + tuple(
        p for p in new_problems if p.signature not in existing_sigs
    )

    # resolved: drop listed signatures
    resolved = {str(s) for s in (parsed.get("resolved") or [])}
    if resolved:
        merged = tuple(p for p in merged if p.signature not in resolved)

    # notes: append (never replace)
    added_notes = tuple(
        str(n) for n in (parsed.get("notes") or []) if str(n) not in current_map.notes
    )
    merged_notes = current_map.notes + added_notes

    done = current_map.done_flag or _collect_only_passed(report)
    return merge_map(
        current_map,
        open_problems=merged,
        notes=merged_notes,
        done_flag=done,
    )
```

- [ ] **Step 4: Update `MAINTAINER_SYSTEM_PROMPT`**

Replace the `## Output schema` block in `MAINTAINER_SYSTEM_PROMPT` so the schema is `open_problems` + `resolved` + `notes` only (drop `installed` and `progress`), and add a line stating facts are authoritative/pre-filled:

```python
MAINTAINER_SYSTEM_PROMPT = """\
You are the Maintainer for the EnvState v1 system.  Installed packages, the
build system, declared requirements, and the interpreter are ALREADY filled in
by the host (authoritative) — do NOT report them.  Your only job is to interpret
this cycle's TaskReport: record new failures, clear fixed ones, and keep notes.

## Ground truth rule
Record only what the command output actually demonstrates.
- Interpret failures into `open_problems` with a suspected layer.
- List in `resolved` the `signature` of any existing problem the output shows is now fixed.

## done_flag (informational — you never set this)
The harness sets done_flag when a `pytest --collect-only` command exits 0.

## Output schema
Return exactly one JSON object inside a ```json fenced block with these keys:

```json
{
  "open_problems": [{"signature": "...", "interpretation": "...", "layer": "..."}],
  "resolved": ["<signature of a now-fixed problem>"],
  "notes": ["..."]
}
```

Do not include any other keys.  Do not report installed packages or progress.
"""
```

- [ ] **Step 5: Update the regression test**

In `tests/test_v1_maintainer.py`, remove/replace any test asserting `_ground_installed` behavior or Maintainer-set `installed`/`progress`. Add the assertion that `parse_v1_maintainer_reply` leaves `current_map.installed` and `current_map.progress` unchanged (the new contract). Keep the `done_flag`-on-collect-only tests.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_maintainer_narrowed.py tests/test_v1_maintainer.py tests/test_envstate_maintainer.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/envstate/maintainer.py tests/test_maintainer_narrowed.py tests/test_v1_maintainer.py
git commit -m "feat(maintainer): narrow to open_problems+resolved+notes; drop _ground_installed"
```

---

## Task 8: orchestrator — `run_v1` folds deterministic facts

**Files:**
- Modify: `src/envstate/orchestrator.py`
- Test: `tests/test_orchestrator_v1_snapshot.py`
- Update: `tests/test_orchestrator_v1.py` (still green — `probe`/`manifest` default to None)

**Note:** `probe`/`manifest` are keyword-only with `None` defaults. When `None`, the fold is skipped and behavior is identical to today (so existing `test_orchestrator_v1.py` callers keep passing). `agent._run_v1` always passes them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_v1_snapshot.py
from types import SimpleNamespace
from src.envstate.orchestrator import run_v1
from src.envstate.world_model import initial_map, Fact, PlannerDecision, Task, TaskReport
from src.envstate.ledger import ActionLedger


def _base():
    return initial_map(base_image="python:3.12", workdir="/app", language="python",
                       build_system="unknown", repo_layout=())


class _Planner:
    def __init__(self, actions): self.actions = list(actions); self.seen = []
    def decide(self, m):
        self.seen.append(m)
        a = self.actions.pop(0)
        if a == "task":
            return PlannerDecision(action="task", task=Task("g", "d", "deps", ()))
        return PlannerDecision(action=a)


class _Build:
    def run(self, task, ex, ledger, step_offset=0):
        return TaskReport(task_goal="g", status="done", commands=(), learning="")


class _Maint:
    def update(self, m, report): return m


def test_probe_fills_facts_at_cycle0_and_each_cycle():
    calls = {"n": 0}
    def probe():
        calls["n"] += 1
        return SimpleNamespace(installed=(Fact("flask", "3.0.0"),), env={"arch": "x86_64"})
    man = SimpleNamespace(build_system="pip", required=(Fact("flask"),))
    planner = _Planner(["task", "done"])
    final, reason = run_v1(planner, _Build(), _Maint(), _base(), ActionLedger(),
                           lambda c: (True, ""), max_cycles=5, probe=probe, manifest=man)
    # cycle-0 fold + one cycle fold = 2 probe calls
    assert calls["n"] == 2
    # planner saw filled facts on its first decide
    assert planner.seen[0].installed == (Fact("flask", "3.0.0"),)
    assert planner.seen[0].build_system == "pip"


def test_probe_not_called_when_planner_gives_up_immediately():
    calls = {"n": 0}
    def probe():
        calls["n"] += 1
        return SimpleNamespace(installed=(), env={"arch": "x86_64"})
    man = SimpleNamespace(build_system="pip", required=())
    planner = _Planner(["giveup"])
    run_v1(planner, _Build(), _Maint(), _base(), ActionLedger(),
           lambda c: (True, ""), max_cycles=5, probe=probe, manifest=man)
    # only the cycle-0 fold ran; no per-cycle fold because planner gave up
    assert calls["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_orchestrator_v1_snapshot.py -v`
Expected: FAIL (`run_v1() got an unexpected keyword argument 'probe'`)

- [ ] **Step 3: Modify `run_v1`**

In `src/envstate/orchestrator.py`: add the import and the new params + folds.

Add to imports:
```python
from src.envstate.world_model import (
    PlannerDecision,
    TaskReport,
    WorldModelMap,
    apply_deterministic,   # NEW
)
```

Change the `run_v1` signature to add keyword-only `probe`/`manifest`:
```python
def run_v1(
    planner: Any,
    build_agent: Any,
    maintainer: Any,
    initial_world_map: WorldModelMap,
    ledger: ActionLedger,
    sandbox_execute: Callable[[str], tuple[bool, str]],
    max_cycles: int = MAX_CYCLES,
    local_budget: int = LOCAL_BUDGET,
    on_cycle: (
        Callable[[int, WorldModelMap, PlannerDecision, TaskReport | None], None] | None
    ) = None,
    *,
    probe: Callable[[], Any] | None = None,
    manifest: Any | None = None,
) -> tuple[WorldModelMap, str]:
```

Replace the body's first line (`current_map = initial_world_map`) with a cycle-0 fold:
```python
    current_map: WorldModelMap = initial_world_map
    if probe is not None and manifest is not None:
        current_map = apply_deterministic(current_map, probe(), manifest)
```

Inside the loop, between the BuildAgent step (`report = build_agent.run(...)`) and the Maintainer step (`current_map = maintainer.update(...)`), insert the per-cycle fold:
```python
        # ── 2b. Deterministic facts (read-only probe, OFF the ledger) ─────────
        if probe is not None and manifest is not None:
            current_map = apply_deterministic(current_map, probe(), manifest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_orchestrator_v1_snapshot.py tests/test_orchestrator_v1.py -v`
Expected: PASS (new tests green; existing orchestrator tests still green via `probe=None`)

- [ ] **Step 5: Commit**

```bash
git add src/envstate/orchestrator.py tests/test_orchestrator_v1_snapshot.py
git commit -m "feat(orchestrator): run_v1 folds deterministic facts at cycle 0 + each cycle"
```

---

## Task 9: agent — wire `parse_manifests` + `probe_env` into `_run_v1`

**Files:**
- Modify: `agent.py` (`_run_v1`, the imports block at ~line 842 and the `run_v1` call at ~line 926)
- Test: `tests/test_run_v1_snapshot_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_v1_snapshot_wiring.py
"""Verify _run_v1 parses the manifest from self.workplace and passes a probe
+ manifest into run_v1. We monkeypatch run_v1 to capture kwargs, so no Docker.
"""
import types
import agent as agent_mod


def test_run_v1_passes_probe_and_manifest(monkeypatch, tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")

    captured = {}

    def fake_run_v1(*args, **kwargs):
        captured["probe"] = kwargs.get("probe")
        captured["manifest"] = kwargs.get("manifest")
        from src.envstate.world_model import initial_map
        m = initial_map(base_image="python:3.12", workdir="/app", language="python",
                        build_system="unknown", repo_layout=())
        return m, "planner_giveup"

    monkeypatch.setattr("src.envstate.orchestrator.run_v1", fake_run_v1, raising=True)

    # Minimal DockerAgent stand-in carrying just what _run_v1 reads.
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    a.workplace = str(tmp_path)
    a.logs_dir = str(tmp_path)
    a.model = "m"
    a.client = object()
    a.synthesizer = types.SimpleNamespace(base_image="python:3.12", workdir="/app")
    a.action_ledger = __import__("src.envstate.ledger", fromlist=["ActionLedger"]).ActionLedger()
    a.sandbox = types.SimpleNamespace(
        exec_readonly=lambda cmd: (1, ""),
        execute=lambda cmd: (True, ""),
        close=lambda keep_alive=False: None,
    )
    a.verified_test_commands = []
    a.verification_bundle = None
    a.env_container_id = "x"
    # methods _run_v1 calls during finalize/teardown — stub to no-ops
    a._record_supervisor_path_usage = lambda *x, **k: None
    a._auto_finalize_from_verified_tests = lambda *x, **k: False
    a._finalize_supervisor_artifacts = lambda ok: ok
    a._write_run_summary = lambda *x, **k: None
    a._is_transient_llm_error = lambda e: False

    a._run_v1(max_cycles=1)

    assert captured["manifest"] is not None
    assert "flask" in {f.name.lower() for f in captured["manifest"].required}
    assert callable(captured["probe"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_run_v1_snapshot_wiring.py -v`
Expected: FAIL (`_run_v1` does not yet pass `probe`/`manifest`)

- [ ] **Step 3: Add imports in `_run_v1`**

In `agent.py` `_run_v1`, extend the envstate import block (near line 842) to add:

```python
        from src.envstate.manifest import parse_manifests
        from src.envstate.snapshot import probe_env
```

- [ ] **Step 4: Build the manifest + probe and pass them to `run_v1`**

In `agent.py` `_run_v1`, just before the `run_v1` call (the `final_map, stop_reason = _run_v1_loop(...)` block ~line 926), add:

```python
            # Deterministic facts: manifest (host FS) + read-only env probe.
            _manifest = parse_manifests(self.workplace)
            _probe = lambda: probe_env(self.sandbox.exec_readonly)
```

Then add `probe=_probe, manifest=_manifest,` to the `_run_v1_loop(...)` call's keyword args:

```python
            final_map, stop_reason = _run_v1_loop(
                planner=planner,
                build_agent=build_agent,
                maintainer=maintainer,
                initial_world_map=world_map,
                ledger=self.action_ledger,
                sandbox_execute=self.sandbox.execute,
                max_cycles=max_cycles,
                probe=_probe,
                manifest=_manifest,
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_run_v1_snapshot_wiring.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent.py tests/test_run_v1_snapshot_wiring.py
git commit -m "feat(agent): wire parse_manifests + probe_env into _run_v1"
```

---

## Task 10: declare `packaging` dependency

**Files:**
- Modify: `requirements.txt`
- Test: `tests/test_packaging_importable.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_packaging_importable.py
def test_manifest_deps_importable():
    import tomllib            # stdlib 3.11+
    from packaging.requirements import Requirement
    assert Requirement("flask>=2.0").name == "flask"
```

- [ ] **Step 2: Run test (passes if packaging already present transitively)**

Run: `.venv/bin/pytest tests/test_packaging_importable.py -v`
Expected: PASS now (packaging present transitively); the point is to pin it explicitly.

- [ ] **Step 3: Declare it explicitly**

Append `packaging` to `requirements.txt` (one line). `manifest.py` imports it directly, so it must be a declared dep, not a transitive accident.

- [ ] **Step 4: Verify**

Run: `.venv/bin/pip install -r requirements.txt && .venv/bin/pytest tests/test_packaging_importable.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/test_packaging_importable.py
git commit -m "chore: declare packaging dependency for manifest parser"
```

---

## Final verification

- [ ] **Run the full envstate suite + coverage**

Run: `.venv/bin/pytest tests/ -k "world_model or manifest or snapshot or maintainer or orchestrator or apply_deterministic or run_v1" --cov=src/envstate --cov-report=term-missing -q`
Expected: all green, ≥80% on `world_model.py`, `manifest.py`, `snapshot.py`, `maintainer.py`, `orchestrator.py`.

- [ ] **Smoke the v1 arm end-to-end on one repo** (manual, optional pre-benchmark)

Run the Repo2Run arm v1 on a single Python repo and confirm: cycle-0 map shows `build_system` + `required` populated; the Dockerfile still builds; no probe command appears in the ledger/Dockerfile.

- [ ] **Benchmark A/B [→EVAL]**

Run Repo2Run arm v1 before/after this branch and compare pass rate. This is the real validation of the prompt narrowing (spec §10).

---

## Self-Review (done during authoring)

- **Spec coverage:** §3 modules → Tasks 1-9; §4.1 manifest → Task 5; §4.2 snapshot + `pip list --format=freeze` → Task 6; §4.3 world_model (env/apply_deterministic/derive_progress/auto_resolve/merge_map) → Tasks 1-4; §4.4 maintainer narrowing + `resolved` → Task 7; §4.5 orchestrator fold → Task 8; §4.6 agent wiring → Task 9; §10 tests → embedded per task (incl. 4 regressions: maintainer installed-untouched + done_flag-on-empty in Task 7, run_v1 signature in Task 8, env round-trip in Task 1); packaging dep → Task 10.
- **Placeholders:** none — every code/test step has full code.
- **Type consistency:** `apply_deterministic(current, snap, man)`, `merge_map(env/build_system/language=...)`, `EnvSnapshot(installed, env)`, `ManifestResult(build_system, required)`, `probe_env(exec_readonly)->EnvSnapshot`, `parse_manifests(workplace)->ManifestResult`, `run_v1(..., *, probe, manifest)` — consistent across Tasks 1-9.
- **Deferred (not in this plan):** deep manifest resolution, multi-language providers, Planner gap-view, COLLECT_ONLY_CMD dedup, dpkg system-package planning (see `TODOS.md`).
