# `bench_emit` — Post-run Harvest Emitter — Design

**Date:** 2026-07-11
**Status:** approved (brainstormed + rat contract verified against all 50 rat envs)
**Related:** `docs/superpowers/specs/2026-07-11-unified-benchmark-runner-design.md` (the `bench` package this feeds)

## 1. Goal

A **standalone** module (`src/bench_emit/`) that converts a completed agent run's `output/` tree
into a **harvest-ready** tree — for each repo, a Dockerfile with the repo at `/testbed` plus a
`bench_meta.json` in `bench`'s key schema — so `bench` can measure any method **uniformly**. Works
**retroactively** (the 50-repo runs already on the VM) and going forward (as a post-run step).

**Hard constraint:** `bench_emit` NEVER imports `bench` and NEVER mutates the source run dirs.

## 2. Motivation (why this exists)

`bench` harvests a Dockerfile an agent left on disk, but the three methods write **incompatible
shapes**:

| method | on disk | repo location | Dockerfile? |
| --- | --- | --- | --- |
| v3 | `eval_build/Dockerfile` + `_meta.json` | `/testbed` | yes, harvestable |
| repo2run | `Dockerfile` + `_meta.json` | **`/repo`** | yes, but wrong workdir |
| rat | `case_study.json` + `outer_commands.json` | **`/repo`** | **no** — env is a mutated live container |

The typer validation (2026-07-11) proved that once each is staged into harvest shape and measured
identically, all three **converge to the same ground truth** (1344/1379, pass_rate 0.9963) — the
differences are economy, not correctness. `bench_emit` formalizes that one-off `stage_validation.py`
staging into a tested module.

## 3. Architecture — offline, out-of-place

- Reads `<run_root>/output/<owner>/<repo>/`.
- Writes `<dest>/<owner>/<repo>/{Dockerfile, bench_meta.json}` (+ sibling scripts for v3).
- **Never mutates** the source run dirs; **zero change to `bench`** (its `harvest.discover` already
  reads `<repo_dir>/Dockerfile` and `bench_meta.json`).
- Pure per-agent adapters + shared normalization/meta helpers + a walker + a CLI.
- **Placement/imports:** package at `src/bench_emit/` (mirrors the standalone `src/manifest_builder/`
  precedent), imported as `from src.bench_emit...`, CLI `python -m src.bench_emit`. `src/` is a real
  package (`src/__init__.py` exists); `tests/conftest.py` puts the repo root on `sys.path`. Tests live
  in `tests/bench_emit/` with **no `__init__.py`**.

## 4. Module layout

```
src/bench_emit/
  __init__.py
  meta.py          # bench_meta.json key-mapping helpers (per agent)
  normalize.py     # /repo->/testbed link, clone header, FROM parser
  agents/
    __init__.py
    v3.py          # eval_build/Dockerfile (already /testbed) + _meta -> EmittedEnv
    repo2run.py    # Dockerfile (repo @ /repo) + append `ln -s /repo /testbed`
    rat.py         # render from case_study.environment.recipe_commands + clone + /testbed
  emit.py          # emit_run(run_root, agent, dest) -> walk output/, dispatch, write tree
  __main__.py      # CLI: python -m bench_emit --run DIR --agent v3|repo2run|rat --dest DIR
```

## 5. Data contract

```python
@dataclass(frozen=True)
class EmittedEnv:
    dockerfile: str | None          # None => no derivable Dockerfile (status "missing")
    scripts: dict                   # {name: content} sibling files the Dockerfile COPYs
    meta: dict                      # bench_meta.json payload (only keys that are known)
```

Adapter signature (each agent module): `adapt(repo_output_dir: str) -> EmittedEnv`.

`bench_meta.json` keys (all optional; **omit** when unknown — `bench` reads absent cost fields as
`None`, never `0`): `agent`, `base_image`, `tokens_in`, `tokens_out`, `llm_calls`, `turns_used`,
`produce_s`, `head_sha`, `commit`, `dockerfile_source`.

## 6. Per-agent adapters

### 6.1 v3 (`agents/v3.py`)
- Read `eval_build/Dockerfile` (already `git clone -> /testbed`) + its `setup.sh` sibling; read `_meta.json`.
- `dockerfile` = passthrough; `scripts = {"setup.sh": ...}` (from the `COPY` in the Dockerfile).
- `meta`: `base_image` (from `_meta.json`), `produce_s <- duration_s`, `head_sha` (from `_meta.json`),
  `agent="v3"`, `dockerfile_source="v3_eval_build"`. Tokens usually absent in `_meta.json` -> omit.

### 6.2 repo2run (`agents/repo2run.py`)
- Read `Dockerfile` (repo cloned to `/repo`). Append `\nRUN ln -sfn /repo /testbed\n`.
- `meta`: `base_image` = parsed `FROM` tag, `produce_s <- _meta.duration_s`, `agent="repo2run"`,
  `dockerfile_source="repo2run_normalized"`.

### 6.3 rat (`agents/rat.py`)
- No Dockerfile on disk. Read `case_study.json["environment"]`: `base_image`, `recipe_commands`.
- **`recipe_commands` is the complete, ordered mutating sequence** — verified across all 50 rat
  envs to include BOTH apt and pip steps (`apt_installs`/`pip_installs` are projections of it;
  `final_dockerfile` = the recipe rendered but clone-less).
- Render:
  ```
  FROM {base_image}
  WORKDIR /
  RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
  RUN git clone --depth=1 https://github.com/{owner}/{repo} /repo
  WORKDIR /repo
  RUN <each recipe_command>       # verbatim, in order
  RUN ln -sfn /repo /testbed
  ```
- `meta`: `base_image`, `tokens_in/out` from `case_study["cost"]` (first matching of
  `tokens_in`/`prompt_tokens`/`input_tokens`; likewise out), `produce_s` from
  `case_study["provenance"]` (`end_ts - start_ts`), `agent="rat"`, `dockerfile_source="rat_reconstructed"`.
- **Honest misrouting:** `base_image` such as `node:18-slim` marks a language-misrouted repo; the
  adapter renders it faithfully and `bench` will correctly report `executed=False` (no pytest env).
  Not an emitter defect.

## 7. Shared helpers

`normalize.py`:
- `link_testbed(dockerfile: str, src="/repo") -> str` — append `RUN ln -sfn {src} /testbed` (idempotent — no-op if already present).
- `clone_lines(repo_url: str, dest="/repo") -> str` — git-install + `git clone --depth=1` block.
- `parse_from(dockerfile: str) -> str | None` — first `FROM <tag>`.

`meta.py`:
- `bench_meta(agent, *, base_image=None, tokens_in=None, tokens_out=None, produce_s=None, head_sha=None, commit=None, llm_calls=None, turns_used=None, dockerfile_source=None) -> dict` — builds the payload, **dropping keys whose value is `None`**.

## 8. Walker + CLI (`emit.py`, `__main__.py`)

- `emit_run(run_root: str, agent: str, dest: str) -> list[tuple[str, str]]` — for each
  `<run_root>/output/<owner>/<repo>`: call the agent's `adapt`, then write
  `<dest>/<owner>/<repo>/`: `bench_meta.json` always; `Dockerfile` + sibling scripts when
  `dockerfile is not None`. **Every attempted repo yields a dest dir** (anti-vanish parity). Returns
  `[(full_name, "ok"|"missing"), ...]`.
- CLI: `python -m src.bench_emit --run <run_root> --agent {v3|repo2run|rat} --dest <dir>`.
  `--agent` is explicit (a run doesn't reliably self-identify its method).
- **`owner/repo` derivation:** each adapter takes only `repo_output_dir`; it derives `full_name` /
  the clone URL (needed by rat's `git clone`) from the two trailing path segments of that dir
  (`.../output/<owner>/<repo>`), exactly as `bench.harvest` derives `full_name`.

## 9. Edge cases

- Missing/empty native artifact (e.g. repo2run's ~16 no-container repos) -> `EmittedEnv(dockerfile=None)`
  -> only `bench_meta.json` written -> `bench` harvest emits a `status="missing"` row. Nothing vanishes.
- Malformed `case_study.json` / Dockerfile -> caught -> treated as `"missing"`, recorded in the return list.
- rat language misrouting -> rendered honestly (see 6.3).

## 10. Testing (TDD, no Docker in unit tests)

- Per-adapter: build a tiny fixture repo dir (`eval_build/Dockerfile`; a `/repo` Dockerfile; a
  `case_study.json`) and assert the emitted Dockerfile is `/testbed`-shaped and the meta keys map
  correctly (incl. `None`-omission and the misrouting case for rat).
- `normalize.py` / `meta.py`: focused unit tests (idempotent link, FROM parse, key drop).
- `emit_run`: `tmp_path`, multi-repo, one missing-artifact repo -> assert dest tree + status list.
- Optional VM smoke (NOT a unit test): `emit_run` an existing rat run -> point `bench` harvest at
  `<dest>` -> assert it sees N envs; measure one (typer) for parity with the validation.

## 11. Out of scope

- In-pipeline auto-emit (defer — a one-line post-step in `run_rat_benchmark._run_one` later, once the
  metrics/harness churn settles).
- Gold-set node-id normalization (the junit-classname-vs-pytest-path landmine — separate work).
- Any change to the `bench` package or the shared-metrics calculations (in flux).
