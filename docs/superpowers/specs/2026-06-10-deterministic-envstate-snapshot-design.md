# Deterministic EnvState Snapshot — Facts by Probe, Insight by LLM

**Date:** 2026-06-10
**Status:** Design approved, ready for implementation plan
**Branch:** john-planner-v1
**Goal:** Make the EnvState v1 Maintainer's world model *state-grounded* instead of *output-grounded*. A cheap deterministic probe owns the facts (what's installed, the build system, declared requirements, the interpreter); the LLM is narrowed to interpretation (failures → `open_problems`, durable `notes`). This removes hallucination risk, fills the permanently-blank `build_system`/`required` fields, and fixes cycle-1 blindness — without reintroducing v0's probe/ACL machinery.

---

## 1. The problem

The current v1 Maintainer (`src/envstate/maintainer.py`) grounds `installed` with `_ground_installed`: it keeps an LLM-proposed package only if its name appears as a substring in the text the BuildAgent happened to print **this cycle**. That grounding is:

- **Lossy** — only sees what scrolled past in the current cycle's commands. Base-image preinstalled packages, anything installed earlier and not re-printed, and transitive deps are invisible.
- **Version-blind** — `"flask"` matches; `3.0.0` is not reliably captured.
- **A proxy, not truth** — "name appears in output" cannot distinguish *installed-now* from *already-present* from *merely-mentioned*.
- **Silent on real failure causes** — which `python`/venv an install landed in, `$PATH`, system packages, tool presence. None are represented, yet wrong-interpreter installs are a top failure mode.

Two fields that are trivially derivable are permanently empty:

- `build_system` is **always `"unknown"`** — the synthesizer never sets that attribute, and the field is frozen after `initial_map`.
- `required` is **always `()`** — `initial_map` is called without it; no manifest is ever parsed.

So on cycle 1 the Planner plans from base image + repo-layout sketch alone, with no notion of the build system or declared dependencies.

This design keeps the v1 three-role shape and the structural `done_flag` gate. It changes **who fills the facts**: a deterministic snapshot, not the LLM.

---

## 2. Decisions (settled in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Language scope | **Python-first behind a pluggable seam.** Implement Python now; leave an interface for JS/others. Deep parse deferred. |
| 2 | Cycle-0 fill | **Full grounding at init** — probe + manifest parse before the first Planner call. |
| 3 | Field semantics | **Snapshot replaces** deterministic fields; **LLM fields append** (`notes`, surviving `open_problems`); `progress` recomputed. |
| 4 | Maintainer LLM cadence | **Every cycle, narrowed scope** — facts pre-filled; LLM emits `open_problems` + `notes` only. |
| 5 | `required` derivation | **Shallow** (declared names only). Advisory hint for the Planner, **never** a checklist or done-signal. Deep resolution is a future enhancement. |
| 6 | Placement | **Approach A** — orchestrator-owned snapshot; the Maintainer stays a pure-LLM unit with no execution surface. |

Three refinements accepted on top:

- **① `env` field** — a new `dict[str,str]` on `WorldModelMap` for scalar probe facts (`python_version`, `which_python`, `venv`, `pip_version`, `arch`, `os_release`).
- **② Deterministic `progress`** — computed from facts in `apply_deterministic`; the Maintainer no longer emits `progress`.
- **③ Auto-resolve `open_problems`** — a problem whose package is now in `installed` is dropped deterministically (fixes the current append-only never-removed staleness bug).

**Non-negotiable invariant:** the success gate is unchanged. Done = `pytest --collect-only` exits 0 → host sets `done_flag`. `required − installed` is *guidance only*; it never terminates the run (both false-positive and false-negative directions exist).

---

## 3. Architecture & module boundaries (Approach A)

```
            (host, deterministic — NO LLM)
  ┌───────────────────────────────────────────────────────┐
  │ manifest.parse_manifests(workplace)  → build_system, required   (host FS read) │
  │ snapshot.probe_env(exec_readonly)    → installed, env           (read-only probe)│
  └───────────────────────────────────────────────────────┘
                         │ world_model.apply_deterministic(map, snap, man)
                         ▼   (replace facts; auto-resolve problems; recompute progress)
              ┌─────────────────────────────────────────┐
              │ Maintainer.update(map, report)           │  ← pure LLM, no sandbox
              │   emits: open_problems, notes            │
              └─────────────────────────────────────────┘
```

| Module | Disposition | Responsibility |
|---|---|---|
| `src/envstate/manifest.py` | **new** | Host-side read of `self.workplace` manifests → `ManifestResult(build_system, required)`. No container access. Never raises. |
| `src/envstate/snapshot.py` | **new** | Read-only container probe via `exec_readonly` (wraps `extractor.run_extractor`) → `EnvSnapshot(installed, env)`. Never raises. |
| `src/envstate/world_model.py` | **changed** | New `env` field; `merge_map(env=…)`; pure `apply_deterministic()`; `_auto_resolve_problems()`; `_derive_progress()`. |
| `src/envstate/maintainer.py` | **changed** | Delete `_ground_installed`; narrow prompt + output schema to `open_problems` + `notes`; consume pre-filled facts. |
| `src/envstate/orchestrator.py` | **changed** | `run_v1` takes a `probe` callable + parsed `manifest`; applies deterministic facts at cycle 0 and each cycle *before* `maintainer.update`. |
| `agent.py` `_run_v1` | **changed** | Parse manifest once from `self.workplace`; build `exec_readonly` probe; pass both into `run_v1`. |
| `src/envstate/extractor.py` | **un-orphaned** | Wired in (currently dead code). |

---

## 4. Component interfaces

### 4.1 `manifest.py` (pure, host-side, never raises)

```python
@dataclass(frozen=True)
class ManifestResult:
    build_system: str             # poetry | pip | setuptools | hatchling | flit | pipenv | unknown
    required: tuple[Fact, ...]    # Fact(name, detail=version-specifier); declared names only

def parse_manifests(workplace: str) -> ManifestResult: ...
```

- **build_system detection precedence:** `poetry.lock` or `[tool.poetry]` → poetry; `pyproject [build-system].build-backend` → setuptools/hatchling/flit; `Pipfile` → pipenv; `requirements*.txt` → pip; `setup.py`/`setup.cfg` only → setuptools; none → `unknown`.
- **required sources (shallow, names only):** `pyproject [project.dependencies]`; `requirements*.txt` (resolve `-r <file>` includes; strip version specifiers/markers into `detail`); `poetry [tool.poetry.dependencies]` (skip `python`); `Pipfile [packages]`.
- Reads files with `open()` from `workplace`. Missing/malformed files are skipped; the function returns best-effort and never raises.
- **Future (deferred):** deep resolution from lock files / resolver behind the same interface.

### 4.2 `snapshot.py` (read-only probe, never raises)

```python
@dataclass(frozen=True)
class EnvSnapshot:
    installed: tuple[Fact, ...]   # from `pip freeze` → Fact(name, detail=version)
    env: dict[str, str]           # python_version, pip_version, which_python, venv, arch, os_release

def probe_env(exec_readonly: Callable[[str], tuple[int, str]]) -> EnvSnapshot: ...
```

- Wraps `extractor.run_extractor(exec_readonly, LIGHTWEIGHT_FIELDS + ("which_python","venv"))`. `exec_readonly`'s `(int, str)` return type already matches `extractor.ProbeExecutor`.
- Parses `installed_pip` (`name==version` lines) into `Fact` tuples. Scalar fields populate `env`.
- On any probe error returns an empty snapshot; the caller keeps the prior cycle's facts.

### 4.3 `world_model.py` changes

```python
@dataclass(frozen=True)
class WorldModelMap:
    ...
    env: dict[str, str] = field(default_factory=dict)   # NEW (modeled like progress)

def merge_map(current, *, ..., env: dict[str,str] | None = None) -> WorldModelMap: ...
    # env defensively copied, like progress

def apply_deterministic(current: WorldModelMap,
                        snap: EnvSnapshot,
                        man: ManifestResult) -> WorldModelMap:
    """Fold deterministic facts into the map. Pure; never raises.
    REPLACES: installed (snap), env (snap), build_system (man),
              required (man), language (from snap.env['python_version'] if present).
    AUTO-RESOLVES: open_problems whose package name is now in installed (③).
    RECOMPUTES: progress deterministically (②).
    LEAVES UNTOUCHED: notes, surviving open_problems, base_image, workdir,
                      repo_layout, done_flag.
    Empty/failed snapshot → keep current installed/env (degrade, don't wipe)."""
```

`_derive_progress(prev, map) -> dict[str,bool]` (②): **fully deterministic — the Maintainer no longer emits progress.** Layers with a clean signal: `base`=`base_image` set; `runtime`=`env['python_version']` present; `deps`=`required` known and `required − installed` empty (by name); `tests`=`done_flag`. Layers without a positive probe signal (`system`, `build`): complete **unless** an unresolved `open_problem` targets that layer. Progress is monotonic: OR-merged against `prev` so a layer never flips True→False within a run.

`_auto_resolve_problems(open_problems, installed) -> tuple[OpenProblem,...]` (③): drop a problem whose `signature` references a package name now present in `installed` (e.g. `ModuleNotFoundError: psycopg2` resolves when `psycopg2`/`psycopg2-binary` ∈ installed). Conservative substring/name match; when unsure, keep the problem.

### 4.4 `maintainer.py` changes

- Delete `_ground_installed`.
- Output schema and `MAINTAINER_SYSTEM_PROMPT` narrowed to **`open_problems` + `notes`** (no `installed`, no `progress`). The prompt states facts are authoritative/pre-filled and the LLM's only job is interpreting failures and recording durable cautions.
- `Maintainer.update(map, report)` signature unchanged; it now receives a map whose facts are already filled, and merges only `open_problems` (append, dedup) + `notes` (append). `done_flag` structural rule stays.
- The user payload includes the pre-filled facts + the `required − installed` diff so the LLM can attribute failures to a layer.

### 4.5 `orchestrator.py` changes

```python
def run_v1(planner, build_agent, maintainer, initial_world_map, ledger,
           sandbox_execute, *, probe, manifest,
           max_cycles=12, local_budget=8, on_cycle=None) -> tuple[WorldModelMap, str]:
    current = apply_deterministic(initial_world_map, probe(), manifest)   # CYCLE 0
    for cycle in range(1, max_cycles+1):
        decision = planner.decide(current)
        if decision.action in ("done","giveup"): return current, f"planner_{decision.action}"
        report  = build_agent.run(decision.task, sandbox_execute, ledger,
                                  step_offset=(cycle-1)*local_budget)
        current = apply_deterministic(current, probe(), manifest)         # facts, off-ledger
        current = maintainer.update(current, report)                      # LLM: problems+notes
        if on_cycle: on_cycle(cycle, current, decision, report)
        if current.done_flag: return current, "done_flag"
    return current, "max_cycles"
```

- `probe: Callable[[], EnvSnapshot]` and `manifest: ManifestResult` are injected.
- Manifest is parsed once (host files rarely change mid-run); re-parse is a future option if the agent edits a manifest.

### 4.6 `agent.py` `_run_v1` changes

```python
from src.envstate.manifest import parse_manifests
from src.envstate.snapshot import probe_env

man   = parse_manifests(self.workplace)                      # host FS, cycle 0
probe = lambda: probe_env(self.sandbox.exec_readonly)        # read-only, off-ledger
final_map, stop_reason = run_v1(..., probe=probe, manifest=man)
```

`initial_map(...)` still seeds `base_image`/`workdir`/`repo_layout`/`language`; `run_v1` applies the deterministic fold at cycle 0 before the first Planner call.

---

## 5. Data flow

```
CYCLE 0 (run_v1, before first Planner):
  man  = parse_manifests(self.workplace)         # required, build_system  (host read)
  snap = probe_env(exec_readonly)                # base-image pip freeze → installed baseline + env
  map  = apply_deterministic(initial_map, snap, man)
        → Planner sees real build_system, declared required, base installed,
          and required−installed = the install TODO.

STEADY CYCLE:
  Planner.decide(map) → task
  BuildAgent.run(task) → report                  # real installs appended to ActionLedger
  snap = probe_env(exec_readonly)                # authoritative state, OFF the ledger
  map  = apply_deterministic(map, snap, man)     # replace facts; auto-resolve; recompute progress
  map  = Maintainer.update(map, report)          # LLM: open_problems + notes only
  if map.done_flag: finalize                     # unchanged structural gate
```

**Cycle-0 nuance (verified):** a cycle-0 `pip freeze` reflects only the **base image's** preinstalled packages, not project deps. That is the correct starting `installed`. The install TODO is `required (manifest) − installed (baseline)`. Keeping the baseline also enables post-hoc "what the loop installed vs what the base image provided" verification.

---

## 6. Field semantics

| Field | Source | Reconciliation |
|---|---|---|
| `installed` | `pip freeze` (snapshot) | **replace** each cycle (authoritative; reflects uninstalls) |
| `env` | snapshot scalars | **replace** |
| `build_system` | manifest | **replace** (stable) |
| `required` | manifest | **replace** (stable) |
| `language` | `env['python_version']` | **replace** when present, else keep |
| `progress` | derived (②) | recomputed, monotonic |
| `open_problems` | LLM + auto-resolve (③) | **append** (dedup by signature), minus auto-resolved |
| `notes` | LLM | **append** |
| `done_flag` | host structural | unchanged (collect-only rc 0) |
| `base_image`, `workdir`, `repo_layout` | init | immutable |

`_ground_installed` is deleted — there is nothing to ground when facts come from `pip freeze`.

---

## 7. Trust boundary

This *strengthens* grounding versus both v0 and current v1, while keeping v1's simplicity:

- **vs current v1:** facts are now authoritative container state, not substring matches on incidental output. The LLM can no longer hallucinate an install — it doesn't propose facts at all.
- **vs v0:** no probe-request channel, no certify/Evidence/ACL, no revision staleness, no `name`-key contract bug. Just one read-only snapshot folded by a pure function. The discipline that prevents a v0 relapse: **one dumb read-only probe, off the ledger, no ACL.**

---

## 8. Error handling (degrade, never throw)

- `parse_manifests` / `probe_env` are internally guarded; they never raise into the loop.
- **Probe failure** (no `pip`, container hiccup): empty snapshot → `apply_deterministic` keeps the prior cycle's `installed`/`env` and adds a `note`. The loop continues.
- **Malformed manifest:** `build_system="unknown"`, `required=()`.
- **Non-Python repo:** Python probe/parse yield little → behaves like today (reactive, `ModuleNotFoundError`-driven). No special-casing.
- **Shallow-parse gaps** (`-r` includes handled; dynamic `setup.py` deps not): covered by the reactive failure path — the BuildAgent hits the import error, the Maintainer records an `open_problem`, the Planner installs it.

---

## 9. Ledger / Dockerfile isolation (verified)

- Probes use `sandbox.exec_readonly()` (`sandbox.py:164–184`): no ledger append, no snapshot/commit, no retries, no preflight, no runtime-service tracking.
- Dockerfile synthesis reads **exclusively** from `ActionLedger.events()` filtered by `rc==0 AND mutation_class != None` (`synthesis.py:7–31`). `runtime_replay_commands` is ephemeral sandbox-rollback state and is **not** a synthesis source. Therefore a read-only probe cannot leak into the Dockerfile.

---

## 10. Testing (≥80% on new/changed code)

Reuse established seams: `exec_readonly` faked as `lambda cmd: (0, "...")`; `_fake_client(content)` SimpleNamespace for the LLM; frozen-map equality/containment assertions; helpers inline (no new conftest).

- **Unit:** `parse_manifests` (each manifest type, `-r` includes, malformed, build_system precedence); `probe_env` (parse `pip freeze`, missing fields, probe error → empty); `apply_deterministic` (replace semantics, auto-resolve, derived progress, empty-snapshot degrade); narrowed Maintainer parse (problems/notes only; ignores any stray `installed`/`progress` keys).
- **Integration:** cycle-0 fill yields a grounded initial map (build_system + required populated); one full cycle pre-fills facts then the Maintainer adds only problems/notes; probe-failure cycle degrades without wiping facts; an `open_problem` auto-resolves once its package appears in `installed`.

---

## 11. Blast radius

- **New:** `src/envstate/manifest.py`, `src/envstate/snapshot.py`, plus tests.
- **Changed:** `world_model.py` (`env` field, `merge_map`, `apply_deterministic`, helpers, `map_to_dict`/`map_from_dict`), `maintainer.py` (drop `_ground_installed`, narrow prompt/schema), `orchestrator.py` (`run_v1` params + per-cycle fold), `agent.py` `_run_v1` (manifest parse + probe wiring).
- **Un-orphaned:** `extractor.py`.
- **Unchanged:** the three-role shape, `done_flag` gate, ActionLedger → synthesis → Dockerfile spine, cleanroom.

---

## 12. Non-goals (this iteration)

- **Deep / transitive dependency resolution** for `required` (lock files, resolver) — interface is left open; deferred.
- **Non-Python language providers** — the seam exists; only Python is implemented.
- **`required` as a done-signal** — explicitly rejected; the collect-only gate is the only stop condition.
- **System-package (apt/dpkg) install planning** — `dpkg` may be captured into `env` for diagnostics, but apt-layer planning stays the LLM/reactive path for now.
- **Re-parsing manifests every cycle** — parsed once unless we later detect agent edits.
