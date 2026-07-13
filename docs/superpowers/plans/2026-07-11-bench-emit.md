# bench_emit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a **standalone** `src/bench_emit/` package that converts a completed agent run's `output/<owner>/<repo>/` tree into a **harvest-ready** tree — for each repo a Dockerfile with the repo at `/testbed` plus a `bench_meta.json` in `bench`'s key schema — so `bench` can measure v3, repo2run, and rat **uniformly**, both retroactively (the 50-repo VM runs) and going forward.

**Architecture:** Offline, out-of-place. Pure per-agent adapters (`agents/{v3,repo2run,rat}.py`, each `adapt(repo_output_dir) -> EmittedEnv`) sit behind shared normalization (`normalize.py`) and meta-building (`meta.py`) helpers; a walker (`emit.py`) dispatches per agent and writes `<dest>/<owner>/<repo>/{Dockerfile, bench_meta.json}` (+ sibling scripts for v3); a thin argparse CLI (`__main__.py`) drives it. `bench_emit` NEVER imports `bench` and NEVER mutates the source run dirs.

**Tech Stack:** Python 3 (repo standard), `pytest`, `dataclasses`, `json`, `re`, `argparse`, `glob`. No Docker in unit tests (adapters are pure text transforms over fixture dirs).

**Spec:** `docs/superpowers/specs/2026-07-11-bench-emit-design.md` (source of truth). Adapter logic is a tested port of the proven one-off `stage_validation.py`.

## Global Constraints

- Package `src/bench_emit/` (mirrors `src/manifest_builder/`); imported as `from src.bench_emit...`; CLI `python -m src.bench_emit`. `src/` is a real package; `tests/conftest.py` puts repo root on sys.path.
- Tests in `tests/bench_emit/` with **NO `__init__.py`** (a test-dir `__init__.py` shadows the source package under pytest's default import mode).
- `bench_emit` **NEVER imports `bench`** and **NEVER mutates the source run dirs** (out-of-place writes only).
- Style: `from __future__ import annotations`, type hints, `@dataclass(frozen=True)` for `EmittedEnv`. Use context managers for all file I/O (`with open(...)`).
- Anti-vanish: every repo under `output/` yields a dest dir; missing/underivable Dockerfile -> write only `bench_meta.json` -> harvest emits a "missing" row.
- `bench_meta.json`: OMIT keys whose value is unknown/None (never write `0`/`null` for cost fields).
- Use `python3` to run pytest. Commit types `feat:`/`test:`. **NEVER `git add -A`** — stage ONLY the exact files each task lists (repo has unrelated WIP under `src/python_deps/depgraph/`, `service_*`, and the user commits in parallel on this shared branch).

---

### Task 1: `EmittedEnv` dataclass + package skeleton

**Files:**
- Create: `src/bench_emit/__init__.py` (empty), `src/bench_emit/types.py`, `src/bench_emit/agents/__init__.py` (empty)
- Test: `tests/bench_emit/test_types.py`

**Interfaces:**
- Produces: `EmittedEnv(dockerfile: str | None, scripts: dict = {}, meta: dict = {})` — frozen. Lives in `types.py` so both `agents/*` and `emit.py` import it without a cycle.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench_emit/test_types.py
import dataclasses

import pytest

from src.bench_emit.types import EmittedEnv


def test_emitted_env_minimal_defaults():
    e = EmittedEnv(dockerfile=None)
    assert e.dockerfile is None
    assert e.scripts == {} and e.meta == {}


def test_emitted_env_frozen():
    e = EmittedEnv(dockerfile="FROM x", scripts={"setup.sh": "echo hi"}, meta={"agent": "v3"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.dockerfile = "FROM y"  # type: ignore[misc]


def test_emitted_env_holds_payload():
    e = EmittedEnv(dockerfile="FROM x", scripts={"setup.sh": "s"}, meta={"agent": "v3", "produce_s": 1.5})
    assert e.dockerfile == "FROM x"
    assert e.scripts["setup.sh"] == "s"
    assert e.meta["produce_s"] == 1.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bench_emit/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bench_emit'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/bench_emit/__init__.py
```

(empty file)

```python
# src/bench_emit/agents/__init__.py
```

(empty file)

```python
# src/bench_emit/types.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EmittedEnv:
    dockerfile: str | None            # None => no derivable Dockerfile (status "missing")
    scripts: dict = field(default_factory=dict)   # {name: content} sibling files the Dockerfile COPYs
    meta: dict = field(default_factory=dict)      # bench_meta.json payload (only known keys)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bench_emit/test_types.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bench_emit/__init__.py src/bench_emit/types.py src/bench_emit/agents/__init__.py tests/bench_emit/test_types.py
git commit -m "feat(bench_emit): EmittedEnv dataclass + package skeleton"
```

---

### Task 2: `normalize.py` — /repo->/testbed link, clone block, FROM parser

**Files:**
- Create: `src/bench_emit/normalize.py`
- Test: `tests/bench_emit/test_normalize.py`

**Interfaces:**
- Produces: `link_testbed(dockerfile: str, src: str = "/repo") -> str` (idempotent append of `RUN ln -sfn {src} /testbed`); `clone_lines(repo_url: str, dest: str = "/repo") -> str` (git-install + `git clone --depth=1` block); `parse_from(dockerfile: str) -> str | None` (first `FROM <tag>`).

- [ ] **Step 1: Write the failing test**

```python
# tests/bench_emit/test_normalize.py
from src.bench_emit.normalize import clone_lines, link_testbed, parse_from


def test_parse_from_returns_first_tag():
    assert parse_from("FROM python:3.10-slim\nRUN echo hi") == "python:3.10-slim"


def test_parse_from_none_when_absent():
    assert parse_from("RUN echo hi") is None


def test_link_testbed_appends_symlink():
    out = link_testbed("FROM x\nWORKDIR /repo", src="/repo")
    assert out.rstrip().endswith("RUN ln -sfn /repo /testbed")


def test_link_testbed_is_idempotent():
    once = link_testbed("FROM x", src="/repo")
    twice = link_testbed(once, src="/repo")
    assert once == twice
    assert twice.count("RUN ln -sfn /repo /testbed") == 1


def test_clone_lines_installs_git_and_clones():
    out = clone_lines("https://github.com/o/r", dest="/repo")
    assert "apt-get install -y --no-install-recommends git" in out
    assert "RUN git clone --depth=1 https://github.com/o/r /repo" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bench_emit/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bench_emit.normalize'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/bench_emit/normalize.py
from __future__ import annotations

import re

_FROM = re.compile(r"^\s*FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE)


def parse_from(dockerfile: str) -> str | None:
    """Return the tag of the first `FROM <tag>` line, or None."""
    m = _FROM.search(dockerfile or "")
    return m.group(1) if m else None


def link_testbed(dockerfile: str, src: str = "/repo") -> str:
    """Append `RUN ln -sfn {src} /testbed` so the repo lives at /testbed. Idempotent."""
    link = f"RUN ln -sfn {src} /testbed"
    if link in dockerfile:
        return dockerfile
    return dockerfile.rstrip("\n") + "\n" + link + "\n"


def clone_lines(repo_url: str, dest: str = "/repo") -> str:
    """git-install + shallow-clone block (no trailing newline; caller joins/joins-in)."""
    return (
        "RUN apt-get update && apt-get install -y --no-install-recommends git "
        "&& rm -rf /var/lib/apt/lists/*\n"
        f"RUN git clone --depth=1 {repo_url} {dest}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bench_emit/test_normalize.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bench_emit/normalize.py tests/bench_emit/test_normalize.py
git commit -m "feat(bench_emit): normalize helpers (link_testbed, clone_lines, parse_from)"
```

---

### Task 3: `meta.py` — `bench_meta` payload builder

**Files:**
- Create: `src/bench_emit/meta.py`
- Test: `tests/bench_emit/test_meta.py`

**Interfaces:**
- Produces: `bench_meta(agent: str, *, base_image=None, tokens_in=None, tokens_out=None, produce_s=None, head_sha=None, commit=None, llm_calls=None, turns_used=None, dockerfile_source=None) -> dict`. Always keeps `agent`; drops every other key whose value is `None`. A genuine `0` is kept (real data, not fabricated).

- [ ] **Step 1: Write the failing test**

```python
# tests/bench_emit/test_meta.py
from src.bench_emit.meta import bench_meta


def test_agent_always_present():
    assert bench_meta("v3") == {"agent": "v3"}


def test_none_valued_keys_dropped():
    m = bench_meta("rat", base_image="python:3.10-slim", tokens_in=None, produce_s=12.5)
    assert m == {"agent": "rat", "base_image": "python:3.10-slim", "produce_s": 12.5}
    assert "tokens_in" not in m


def test_zero_is_a_real_value_not_dropped():
    # unknown -> None -> omitted; a genuine 0 read from data is kept (never fabricated)
    m = bench_meta("rat", tokens_in=0, tokens_out=5)
    assert m["tokens_in"] == 0 and m["tokens_out"] == 5


def test_all_known_keys_map():
    m = bench_meta("v3", base_image="b", tokens_in=1, tokens_out=2, produce_s=3.0,
                   head_sha="abc", commit="def", llm_calls=4, turns_used=5,
                   dockerfile_source="v3_eval_build")
    assert m == {"agent": "v3", "base_image": "b", "tokens_in": 1, "tokens_out": 2,
                 "llm_calls": 4, "turns_used": 5, "produce_s": 3.0, "head_sha": "abc",
                 "commit": "def", "dockerfile_source": "v3_eval_build"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bench_emit/test_meta.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bench_emit.meta'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/bench_emit/meta.py
from __future__ import annotations


def bench_meta(
    agent: str,
    *,
    base_image: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    produce_s: float | None = None,
    head_sha: str | None = None,
    commit: str | None = None,
    llm_calls: int | None = None,
    turns_used: int | None = None,
    dockerfile_source: str | None = None,
) -> dict:
    """Build a bench_meta.json payload, dropping keys whose value is None."""
    payload = {
        "agent": agent,
        "base_image": base_image,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_calls": llm_calls,
        "turns_used": turns_used,
        "produce_s": produce_s,
        "head_sha": head_sha,
        "commit": commit,
        "dockerfile_source": dockerfile_source,
    }
    return {k: v for k, v in payload.items() if v is not None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bench_emit/test_meta.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bench_emit/meta.py tests/bench_emit/test_meta.py
git commit -m "feat(bench_emit): bench_meta payload builder (drops None keys)"
```

---

### Task 4: `agents/v3.py` — eval_build passthrough + `_meta.json` mapping

**Files:**
- Create: `src/bench_emit/agents/v3.py`
- Test: `tests/bench_emit/test_agent_v3.py`

**Interfaces:**
- Consumes: `EmittedEnv` (Task 1), `bench_meta` (Task 3).
- Produces: `adapt(repo_output_dir: str) -> EmittedEnv`. Reads `eval_build/Dockerfile` (already clones to `/testbed`) as passthrough + `eval_build/setup.sh` sibling into `scripts`; reads `_meta.json` and maps `duration_s -> produce_s`, `base_image`, `head_sha`; `agent="v3"`, `dockerfile_source="v3_eval_build"`. Tokens absent -> omitted. Missing Dockerfile -> `EmittedEnv(dockerfile=None)` (anti-vanish).

- [ ] **Step 1: Write the failing test**

```python
# tests/bench_emit/test_agent_v3.py
import json

from src.bench_emit.agents import v3


def _make_v3_repo(tmp_path):
    repo = tmp_path / "output" / "fastapi" / "typer"
    eb = repo / "eval_build"
    eb.mkdir(parents=True)
    (eb / "Dockerfile").write_text(
        "FROM python:3.10-slim\n"
        "RUN git clone --depth=1 https://github.com/fastapi/typer /testbed\n"
        "WORKDIR /testbed\n"
        "COPY setup.sh /tmp/setup.sh\n"
        "RUN bash /tmp/setup.sh\n"
    )
    (eb / "setup.sh").write_text("pip install -e .\n")
    (repo / "_meta.json").write_text(json.dumps(
        {"base_image": "python:3.10-slim", "duration_s": 812.4, "head_sha": "abc123"}))
    return str(repo)


def test_v3_passes_dockerfile_through_and_maps_meta(tmp_path):
    env = v3.adapt(_make_v3_repo(tmp_path))
    assert "git clone --depth=1 https://github.com/fastapi/typer /testbed" in env.dockerfile
    assert env.scripts["setup.sh"] == "pip install -e .\n"
    assert env.meta["agent"] == "v3"
    assert env.meta["base_image"] == "python:3.10-slim"
    assert env.meta["produce_s"] == 812.4
    assert env.meta["head_sha"] == "abc123"
    assert env.meta["dockerfile_source"] == "v3_eval_build"
    assert "tokens_in" not in env.meta


def test_v3_missing_eval_build_is_anti_vanish(tmp_path):
    repo = tmp_path / "output" / "o" / "r"
    repo.mkdir(parents=True)
    (repo / "_meta.json").write_text(json.dumps({"base_image": "python:3.11-slim"}))
    env = v3.adapt(str(repo))
    assert env.dockerfile is None
    assert env.meta["agent"] == "v3"
    assert env.meta["base_image"] == "python:3.11-slim"
    assert "produce_s" not in env.meta
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bench_emit/test_agent_v3.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bench_emit.agents.v3'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/bench_emit/agents/v3.py
from __future__ import annotations

import json
import os

from src.bench_emit.meta import bench_meta
from src.bench_emit.types import EmittedEnv


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def adapt(repo_output_dir: str) -> EmittedEnv:
    eval_build = os.path.join(repo_output_dir, "eval_build")
    df_path = os.path.join(eval_build, "Dockerfile")
    meta_json = _load_json(os.path.join(repo_output_dir, "_meta.json"))

    duration = meta_json.get("duration_s")
    produce_s = round(duration, 2) if isinstance(duration, (int, float)) else None

    meta = bench_meta(
        "v3",
        base_image=meta_json.get("base_image"),
        produce_s=produce_s,
        head_sha=meta_json.get("head_sha"),
        dockerfile_source="v3_eval_build",
    )

    if not os.path.isfile(df_path):
        return EmittedEnv(dockerfile=None, scripts={}, meta=meta)

    with open(df_path) as f:
        dockerfile = f.read()

    scripts: dict = {}
    setup_path = os.path.join(eval_build, "setup.sh")
    if os.path.isfile(setup_path):
        with open(setup_path) as f:
            scripts["setup.sh"] = f.read()

    return EmittedEnv(dockerfile=dockerfile, scripts=scripts, meta=meta)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bench_emit/test_agent_v3.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bench_emit/agents/v3.py tests/bench_emit/test_agent_v3.py
git commit -m "feat(bench_emit): v3 adapter (eval_build passthrough + _meta mapping)"
```

---

### Task 5: `agents/repo2run.py` — `/repo`->`/testbed` link + FROM base

**Files:**
- Create: `src/bench_emit/agents/repo2run.py`
- Test: `tests/bench_emit/test_agent_repo2run.py`

**Interfaces:**
- Consumes: `EmittedEnv` (Task 1), `bench_meta` (Task 3), `link_testbed` + `parse_from` (Task 2).
- Produces: `adapt(repo_output_dir: str) -> EmittedEnv`. Reads `Dockerfile` (repo at `/repo`), appends `RUN ln -sfn /repo /testbed` via `link_testbed`; `base_image` from `parse_from`, `produce_s <- _meta.duration_s`, `agent="repo2run"`, `dockerfile_source="repo2run_normalized"`. Missing Dockerfile -> `EmittedEnv(dockerfile=None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench_emit/test_agent_repo2run.py
import json

from src.bench_emit.agents import repo2run


def _make_repo2run(tmp_path):
    repo = tmp_path / "output" / "psf" / "requests"
    repo.mkdir(parents=True)
    (repo / "Dockerfile").write_text(
        "FROM python:3.9-slim\n"
        "RUN git clone --depth=1 https://github.com/psf/requests /repo\n"
        "WORKDIR /repo\n"
        "RUN pip install -e .\n"
    )
    (repo / "_meta.json").write_text(json.dumps({"duration_s": 240.0}))
    return str(repo)


def test_repo2run_appends_testbed_link_and_parses_base(tmp_path):
    env = repo2run.adapt(_make_repo2run(tmp_path))
    assert env.dockerfile.rstrip().endswith("RUN ln -sfn /repo /testbed")
    assert env.meta["agent"] == "repo2run"
    assert env.meta["base_image"] == "python:3.9-slim"
    assert env.meta["produce_s"] == 240.0
    assert env.meta["dockerfile_source"] == "repo2run_normalized"


def test_repo2run_missing_dockerfile_is_anti_vanish(tmp_path):
    repo = tmp_path / "output" / "o" / "r"
    repo.mkdir(parents=True)
    env = repo2run.adapt(str(repo))
    assert env.dockerfile is None
    assert env.meta["agent"] == "repo2run"
    assert "base_image" not in env.meta
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bench_emit/test_agent_repo2run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bench_emit.agents.repo2run'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/bench_emit/agents/repo2run.py
from __future__ import annotations

import json
import os

from src.bench_emit.meta import bench_meta
from src.bench_emit.normalize import link_testbed, parse_from
from src.bench_emit.types import EmittedEnv


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def adapt(repo_output_dir: str) -> EmittedEnv:
    df_path = os.path.join(repo_output_dir, "Dockerfile")
    meta_json = _load_json(os.path.join(repo_output_dir, "_meta.json"))

    duration = meta_json.get("duration_s")
    produce_s = round(duration, 2) if isinstance(duration, (int, float)) else None

    if not os.path.isfile(df_path):
        meta = bench_meta("repo2run", produce_s=produce_s, dockerfile_source="repo2run_normalized")
        return EmittedEnv(dockerfile=None, scripts={}, meta=meta)

    with open(df_path) as f:
        dockerfile = f.read()

    dockerfile = link_testbed(dockerfile, src="/repo")
    meta = bench_meta(
        "repo2run",
        base_image=parse_from(dockerfile),
        produce_s=produce_s,
        dockerfile_source="repo2run_normalized",
    )
    return EmittedEnv(dockerfile=dockerfile, scripts={}, meta=meta)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bench_emit/test_agent_repo2run.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bench_emit/agents/repo2run.py tests/bench_emit/test_agent_repo2run.py
git commit -m "feat(bench_emit): repo2run adapter (/repo->/testbed link + FROM base)"
```

---

### Task 6: `agents/rat.py` — reconstruct Dockerfile from `case_study.json`

**Files:**
- Create: `src/bench_emit/agents/rat.py`
- Test: `tests/bench_emit/test_agent_rat.py`

**Interfaces:**
- Consumes: `EmittedEnv` (Task 1), `bench_meta` (Task 3), `clone_lines` + `link_testbed` (Task 2).
- Produces: `adapt(repo_output_dir: str) -> EmittedEnv`. No Dockerfile on disk — renders one from `case_study.json["environment"]` (`base_image` default `python:3.10-slim`, `recipe_commands` verbatim in order) with a clone of `https://github.com/<owner>/<repo>` derived from the dir's two trailing path segments, then `link_testbed`. `meta`: `base_image`, `tokens_in/out` (first numeric of the cost key aliases), `produce_s <- provenance.end_ts - start_ts`, `agent="rat"`, `dockerfile_source="rat_reconstructed"`. `node:*`/`rust:*` bases render faithfully (honest misrouting). Malformed/absent `case_study.json` -> `EmittedEnv(dockerfile=None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench_emit/test_agent_rat.py
import json

from src.bench_emit.agents import rat


def _make_rat(tmp_path, environment, owner="fastapi", name="typer", cost=None, provenance=None):
    repo = tmp_path / "output" / owner / name
    repo.mkdir(parents=True)
    cs = {"environment": environment}
    if cost is not None:
        cs["cost"] = cost
    if provenance is not None:
        cs["provenance"] = provenance
    (repo / "case_study.json").write_text(json.dumps(cs))
    return str(repo)


def test_rat_reconstructs_dockerfile_from_recipe(tmp_path):
    d = _make_rat(
        tmp_path,
        {"base_image": "python:3.10-slim",
         "recipe_commands": ["apt-get update", "pip install -e ."]},
        cost={"prompt_tokens": 1200, "completion_tokens": 800},
        provenance={"start_ts": 100.0, "end_ts": 350.5},
    )
    env = rat.adapt(d)
    df = env.dockerfile
    assert df.startswith("FROM python:3.10-slim")
    assert "RUN git clone --depth=1 https://github.com/fastapi/typer /repo" in df
    assert "RUN apt-get update" in df
    assert "RUN pip install -e ." in df
    assert df.rstrip().endswith("RUN ln -sfn /repo /testbed")
    assert env.meta["agent"] == "rat"
    assert env.meta["base_image"] == "python:3.10-slim"
    assert env.meta["tokens_in"] == 1200 and env.meta["tokens_out"] == 800
    assert env.meta["produce_s"] == 250.5
    assert env.meta["dockerfile_source"] == "rat_reconstructed"


def test_rat_node_misrouting_rendered_faithfully(tmp_path):
    d = _make_rat(tmp_path, {"base_image": "node:18-slim", "recipe_commands": ["npm ci"]},
                  owner="expressjs", name="express")
    env = rat.adapt(d)
    assert env.dockerfile.startswith("FROM node:18-slim")
    assert "RUN git clone --depth=1 https://github.com/expressjs/express /repo" in env.dockerfile
    assert env.meta["base_image"] == "node:18-slim"


def test_rat_cost_and_provenance_absent_omitted(tmp_path):
    d = _make_rat(tmp_path, {"base_image": "python:3.10-slim", "recipe_commands": []})
    env = rat.adapt(d)
    assert "tokens_in" not in env.meta and "tokens_out" not in env.meta
    assert "produce_s" not in env.meta


def test_rat_malformed_case_study_is_missing(tmp_path):
    repo = tmp_path / "output" / "o" / "r"
    repo.mkdir(parents=True)
    (repo / "case_study.json").write_text("{ not valid json")
    env = rat.adapt(str(repo))
    assert env.dockerfile is None
    assert env.meta["agent"] == "rat"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bench_emit/test_agent_rat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bench_emit.agents.rat'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/bench_emit/agents/rat.py
from __future__ import annotations

import json
import os

from src.bench_emit.meta import bench_meta
from src.bench_emit.normalize import clone_lines, link_testbed
from src.bench_emit.types import EmittedEnv

_TOKENS_IN_KEYS = ("tokens_in", "prompt_tokens", "input_tokens", "total_input_tokens")
_TOKENS_OUT_KEYS = ("tokens_out", "completion_tokens", "output_tokens", "total_output_tokens")


def _first_num(d: dict, keys: tuple) -> int | float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return v
    return None


def _render(base: str, repo_url: str, recipe: list) -> str:
    lines = [
        f"FROM {base}",
        "WORKDIR /",
        clone_lines(repo_url, dest="/repo"),
        "WORKDIR /repo",
    ]
    lines += ["RUN " + c for c in recipe]
    dockerfile = "\n".join(lines) + "\n"
    return link_testbed(dockerfile, src="/repo")


def adapt(repo_output_dir: str) -> EmittedEnv:
    cs_path = os.path.join(repo_output_dir, "case_study.json")
    try:
        with open(cs_path) as f:
            cs = json.load(f)
    except (OSError, json.JSONDecodeError):
        return EmittedEnv(dockerfile=None, scripts={},
                          meta=bench_meta("rat", dockerfile_source="rat_reconstructed"))

    env = cs.get("environment", {}) or {}
    base = env.get("base_image") or "python:3.10-slim"
    recipe = env.get("recipe_commands", []) or []

    owner, name = os.path.normpath(repo_output_dir).split(os.sep)[-2:]
    repo_url = f"https://github.com/{owner}/{name}"
    dockerfile = _render(base, repo_url, recipe)

    prov = cs.get("provenance", {}) or {}
    produce_s = None
    if isinstance(prov.get("start_ts"), (int, float)) and isinstance(prov.get("end_ts"), (int, float)):
        produce_s = round(prov["end_ts"] - prov["start_ts"], 2)

    cost = cs.get("cost", {}) or {}
    meta = bench_meta(
        "rat",
        base_image=base,
        tokens_in=_first_num(cost, _TOKENS_IN_KEYS),
        tokens_out=_first_num(cost, _TOKENS_OUT_KEYS),
        produce_s=produce_s,
        dockerfile_source="rat_reconstructed",
    )
    return EmittedEnv(dockerfile=dockerfile, scripts={}, meta=meta)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bench_emit/test_agent_rat.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bench_emit/agents/rat.py tests/bench_emit/test_agent_rat.py
git commit -m "feat(bench_emit): rat adapter (reconstruct Dockerfile from case_study recipe)"
```

---

### Task 7: `emit.py` — the `emit_run` walker

**Files:**
- Create: `src/bench_emit/emit.py`
- Test: `tests/bench_emit/test_emit.py`

**Interfaces:**
- Consumes: the three adapters' `adapt` (Tasks 4–6), `EmittedEnv` (Task 1), `bench_meta` (Task 3).
- Produces: `emit_run(run_root: str, agent: str, dest: str) -> list[tuple[str, str]]`. For each `<run_root>/output/<owner>/<repo>` (sorted): dispatch by `agent` to `adapt`, then write `<dest>/<owner>/<repo>/`: `bench_meta.json` always; `Dockerfile` + sibling `scripts` only when `dockerfile is not None`. Every attempted repo yields a dest dir (anti-vanish). Returns `[(full_name, "ok"|"missing"), ...]`. Unknown `agent` -> `ValueError`. Never mutates the source tree.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench_emit/test_emit.py
import json
import os

import pytest

from src.bench_emit.emit import emit_run


def _v3_run(tmp_path):
    root = tmp_path / "v3run"
    good = root / "output" / "fastapi" / "typer" / "eval_build"
    good.mkdir(parents=True)
    (good / "Dockerfile").write_text(
        "FROM python:3.10-slim\nRUN git clone --depth=1 https://github.com/fastapi/typer /testbed\n")
    (good / "setup.sh").write_text("pip install -e .\n")
    (root / "output" / "fastapi" / "typer" / "_meta.json").write_text(
        json.dumps({"base_image": "python:3.10-slim", "duration_s": 10.0}))
    # anti-vanish: a repo with no eval_build Dockerfile
    missing = root / "output" / "o" / "r"
    missing.mkdir(parents=True)
    (missing / "_meta.json").write_text(json.dumps({"base_image": "python:3.11-slim"}))
    return str(root)


def test_emit_run_writes_tree_and_status(tmp_path):
    run_root = _v3_run(tmp_path)
    dest = tmp_path / "harvest"
    results = emit_run(run_root, "v3", str(dest))
    assert results == [("fastapi/typer", "ok"), ("o/r", "missing")]

    typer = dest / "fastapi" / "typer"
    assert (typer / "Dockerfile").is_file()
    assert (typer / "setup.sh").read_text() == "pip install -e .\n"
    meta = json.loads((typer / "bench_meta.json").read_text())
    assert meta["agent"] == "v3" and meta["produce_s"] == 10.0

    miss = dest / "o" / "r"
    assert (miss / "bench_meta.json").is_file()
    assert not (miss / "Dockerfile").exists()


def test_emit_run_never_mutates_source(tmp_path):
    run_root = _v3_run(tmp_path)
    dest = tmp_path / "harvest"
    emit_run(run_root, "v3", str(dest))
    src_repo = os.path.join(run_root, "output", "fastapi", "typer")
    assert not os.path.exists(os.path.join(src_repo, "bench_meta.json"))
    # v3's Dockerfile lives under eval_build/, never written to the repo root of the source
    assert not os.path.exists(os.path.join(src_repo, "Dockerfile"))


def test_emit_run_unknown_agent_raises(tmp_path):
    with pytest.raises(ValueError):
        emit_run(str(tmp_path), "bogus", str(tmp_path / "out"))


def test_emit_run_adapter_crash_is_visible_not_swallowed(tmp_path, monkeypatch, capsys):
    # A real adapter bug must not masquerade as an expected "no artifact" repo:
    # anti-vanish is preserved, but the failure is loud (stderr) and captured (meta.error).
    run_root = _v3_run(tmp_path)
    dest = tmp_path / "harvest"

    from src.bench_emit.agents import v3

    def _boom(_repo_dir):
        raise RuntimeError("adapter blew up")

    monkeypatch.setattr(v3, "adapt", _boom)
    results = emit_run(run_root, "v3", str(dest))

    # (a) the crashed repo still comes back with status "missing" (batch not aborted)
    assert ("fastapi/typer", "missing") in results
    # (b) a bench_meta.json was still written for it
    meta_path = dest / "fastapi" / "typer" / "bench_meta.json"
    assert meta_path.is_file()
    assert not (dest / "fastapi" / "typer" / "Dockerfile").exists()
    # (c) the error was captured, and (d) surfaced on stderr (not silently swallowed)
    meta = json.loads(meta_path.read_text())
    assert meta["agent"] == "v3"
    assert "adapter blew up" in meta["error"]
    assert "adapter error" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bench_emit/test_emit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bench_emit.emit'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/bench_emit/emit.py
from __future__ import annotations

import json
import os
import sys
from glob import glob

from src.bench_emit.agents import rat, repo2run, v3
from src.bench_emit.meta import bench_meta
from src.bench_emit.types import EmittedEnv

_ADAPTERS = {"v3": v3, "repo2run": repo2run, "rat": rat}


def _write(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)


def emit_run(run_root: str, agent: str, dest: str) -> list[tuple[str, str]]:
    if agent not in _ADAPTERS:
        raise ValueError(f"unknown agent: {agent!r} (expected one of {sorted(_ADAPTERS)})")
    adapt = _ADAPTERS[agent].adapt
    output_root = os.path.join(run_root, "output")

    results: list[tuple[str, str]] = []
    for repo_dir in sorted(glob(os.path.join(output_root, "*", "*"))):
        if not os.path.isdir(repo_dir):
            continue
        owner, name = os.path.normpath(repo_dir).split(os.sep)[-2:]
        full_name = f"{owner}/{name}"
        try:
            env = adapt(repo_dir)
        except Exception as exc:                      # noqa: BLE001 — anti-vanish: never abort the batch
            # A crashed adapter must stay visible — never silently indistinguishable
            # from an expected "no artifact" repo. Warn, then fall back to missing.
            print(f"[bench_emit] {full_name}: adapter error: {exc!r}", file=sys.stderr)
            env = EmittedEnv(dockerfile=None, scripts={}, meta={**bench_meta(agent), "error": repr(exc)})

        dest_dir = os.path.join(dest, owner, name)
        os.makedirs(dest_dir, exist_ok=True)
        _write(os.path.join(dest_dir, "bench_meta.json"), json.dumps(env.meta, indent=2))

        if env.dockerfile is not None:
            _write(os.path.join(dest_dir, "Dockerfile"), env.dockerfile)
            for fname, content in (env.scripts or {}).items():
                _write(os.path.join(dest_dir, fname), content)
            results.append((full_name, "ok"))
        else:
            results.append((full_name, "missing"))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bench_emit/test_emit.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bench_emit/emit.py tests/bench_emit/test_emit.py
git commit -m "feat(bench_emit): emit_run walker (dispatch, out-of-place write, anti-vanish)"
```

---

### Task 8: `__main__.py` — argparse CLI

**Files:**
- Create: `src/bench_emit/__main__.py`
- Test: `tests/bench_emit/test_cli.py`

**Interfaces:**
- Consumes: `emit_run` (Task 7).
- Produces: `main(argv=None) -> int` — argparse CLI `--run <run_root> --agent {v3|repo2run|rat} --dest <dir>`, delegates to `emit_run`, prints a per-repo status line + an `N/M ok` summary, returns `0`. Runnable as `python -m src.bench_emit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bench_emit/test_cli.py
import json

import pytest

from src.bench_emit.__main__ import main


def _rat_run(tmp_path):
    root = tmp_path / "ratrun"
    repo = root / "output" / "fastapi" / "typer"
    repo.mkdir(parents=True)
    (repo / "case_study.json").write_text(json.dumps(
        {"environment": {"base_image": "python:3.10-slim", "recipe_commands": ["pip install -e ."]}}))
    return str(root)


def test_cli_emits_tree_and_returns_zero(tmp_path, capsys):
    run_root = _rat_run(tmp_path)
    dest = tmp_path / "harvest"
    rc = main(["--run", run_root, "--agent", "rat", "--dest", str(dest)])
    assert rc == 0
    df = (dest / "fastapi" / "typer" / "Dockerfile").read_text()
    assert df.startswith("FROM python:3.10-slim")
    assert (dest / "fastapi" / "typer" / "bench_meta.json").is_file()
    assert "1/1 ok" in capsys.readouterr().out


def test_cli_rejects_unknown_agent(tmp_path):
    with pytest.raises(SystemExit):
        main(["--run", str(tmp_path), "--agent", "bogus", "--dest", str(tmp_path / "o")])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bench_emit/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bench_emit.__main__'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/bench_emit/__main__.py
from __future__ import annotations

import argparse

from src.bench_emit.emit import emit_run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.bench_emit")
    ap.add_argument("--run", required=True, help="run_root containing output/<owner>/<repo>")
    ap.add_argument("--agent", required=True, choices=["v3", "repo2run", "rat"])
    ap.add_argument("--dest", required=True, help="destination harvest tree root")
    a = ap.parse_args(argv)

    results = emit_run(a.run, a.agent, a.dest)
    n_ok = sum(1 for _, status in results if status == "ok")
    for full_name, status in results:
        print(f"{status:8} {full_name}")
    print(f"\n{n_ok}/{len(results)} ok  ->  {a.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bench_emit/test_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bench_emit/__main__.py tests/bench_emit/test_cli.py
git commit -m "feat(bench_emit): CLI (python -m src.bench_emit --run --agent --dest)"
```

---

### Final: run the whole `bench_emit` suite

- [ ] Run: `python3 -m pytest tests/bench_emit -v`
  Expected: PASS (26 passed — 3 types + 5 normalize + 4 meta + 2 v3 + 2 repo2run + 4 rat + 4 emit + 2 cli).
- [ ] Optional VM smoke (NOT a unit test): `python3 -m src.bench_emit --run <an existing rat run> --agent rat --dest /opt/bench/harvest-src/rat`, point `bench`'s `harvest.discover` at `<dest>`, confirm it sees N envs and measures typer for parity with the 2026-07-11 validation.

---

## Self-Review notes

- **Spec coverage map:**
  - §1 Goal / §2 Motivation -> whole plan (harvest-ready tree; port of `stage_validation.py`).
  - §3 Architecture (offline, out-of-place, `src/bench_emit/` placement, `from src.bench_emit...`, tests without `__init__.py`) -> Global Constraints + Task 1 skeleton + Task 7 out-of-place writes + `test_emit_run_never_mutates_source`.
  - §4 Module layout -> Tasks 1–8 create exactly `types.py`, `meta.py`, `normalize.py`, `agents/{v3,repo2run,rat}.py`, `emit.py`, `__main__.py`.
  - §5 Data contract (`EmittedEnv`, `adapt(repo_output_dir) -> EmittedEnv`, bench_meta keys, omit-when-unknown) -> Task 1 (`EmittedEnv`) + Task 3 (`bench_meta` key set + None-drop).
  - §6.1 v3 -> Task 4; §6.2 repo2run -> Task 5; §6.3 rat (recipe render, cost/provenance mapping, honest node misrouting) -> Task 6.
  - §7 Shared helpers (`link_testbed` idempotent, `clone_lines`, `parse_from`; `bench_meta` drop-None) -> Task 2 + Task 3.
  - §8 Walker + CLI (`emit_run` signature/return, anti-vanish, owner/repo from trailing path segments, explicit `--agent`) -> Task 7 + Task 8.
  - §9 Edge cases (missing native artifact -> `dockerfile=None`; malformed case_study/Dockerfile -> "missing"; rat misrouting) -> Task 4/5 anti-vanish tests, Task 6 `test_rat_malformed_case_study_is_missing` + `test_rat_node_misrouting_rendered_faithfully`, Task 7 anti-vanish assertions + `except -> EmittedEnv(dockerfile=None)`.
  - §10 Testing (per-adapter fixture dirs, normalize/meta focused unit tests, `emit_run` tmp_path multi-repo + one missing, None-omission, misrouting; optional VM smoke) -> every task's TDD steps + the Final section.
  - §11 Out of scope (in-pipeline auto-emit, gold node-id normalization, `bench` changes) -> intentionally no task.
- **Placeholder scan:** none. Every Step 3 contains complete, transcribable code; no "TBD"/"similar to Task N"/"add error handling later". The only deferred item is the optional VM smoke, explicitly labeled not-a-unit-test.
- **Type consistency:** `EmittedEnv(dockerfile: str | None, scripts: dict, meta: dict)` defined once (Task 1) and constructed identically in every adapter and in `emit.py`'s except-fallback. All three adapters share the signature `adapt(repo_output_dir: str) -> EmittedEnv`. `bench_meta(...) -> dict` (Task 3) is the sole meta constructor used by all adapters and the walker fallback. `emit_run(run_root, agent, dest) -> list[tuple[str, str]]` return type matches Task 7 impl and Task 8 consumption (`for full_name, status in results`). `normalize.link_testbed/clone_lines/parse_from` signatures (Task 2) match their call sites in Tasks 5 and 6.
