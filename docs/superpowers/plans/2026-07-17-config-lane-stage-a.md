# Config Lane — Stage A (Prove the Bet + Foundations) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer the config lane's go/no-go — *does editable-install + rootdir actually clear the collect cliff on the pilot repos?* — using existing infrastructure, and lay the two trivial foundations, before any heavy cure/arbitration machinery is built.

**Architecture:** Code-mapping (three opus surveys) showed Gate A is measurable **today**: the rendered v3 `setup.sh` already editable-installs the project (capstone, `emit.py:156`), and `run_replay_ladder` (`src/eval/build_script_eval/replay.py:47-131`) already runs it and then `pytest --collect-only -q` in a *mounted* container, while `bench/` produces the `EBSR` collect-cliff number. So Stage A is a **measurement spike + two cheap foundations**. The in-construction cure runner (which needs a net-new repo-mount primitive on `DockerExecutor`, since the scratch container mounts no repo — `executor.py:103-112`) is **deferred to Stage B**, where route-not-drop actually requires the cure to run during construction.

**Tech Stack:** Python 3.11+, pytest, Docker (for the measurement), the existing `bench/` + `src/eval/build_script_eval/` + `src/manifest_builder/` harnesses.

## Global Constraints

- **`python_deps/*` stays LLM-free.**
- **Scoped commits ONLY** (shared branch, parallel commits): `git add <exact paths>`; `-m` before `--`; never `git add -A`. No `Co-Authored-By` trailer.
- **The go/no-go is the point of this stage.** Task 1 gates Tasks 2–3 and all of Stage B: if editable-install + rootdir does **not** materially lift collection on the pilots, STOP and report — do not build the config lane.
- **Reuse, don't rebuild.** Gate A must reuse `run_replay_ladder` / `bench` / `manifest_builder`; do not write a new container harness.
- Depends on nothing in the user's in-flight install-lane work; touches no shared fixpoint code (that is Stage B).

---

### Task 1: Gate A — cure-recovery measurement (THE GO/NO-GO)

Measure whether `pip install -e .` + rootdir lifts `pytest --collect-only` clean, on the pilots, against a no-editable baseline. This is the whole config lane's bet; everything downstream is gated on it.

**Files:**
- Create: `scripts/gate_a_cure_recovery.py` (thin driver over existing harnesses)
- Reuse (read only): `src/eval/build_script_eval/replay.py` (`run_replay_ladder:47`, `LadderResult.collect_ok`), `scripts/run_v3_e2e.py` (renders `setup.sh`, capstone `-e .`), `bench/measure.py` (`parse_collect`, `EBSR`), `datasets/pilot.json` (3 pinned repos), `datasets/rat_python50_pinned_m3nothink.json` (the 50-repo corpus behind "14/50 collect")

**Interfaces:**
- Consumes: `run_replay_ladder(repo_dir, image, setup_script) -> LadderResult` (`.collect_ok`); a per-repo rendered `setup.sh` from `run_v3_e2e`.
- Produces: a two-arm table `{repo: {configlane: collect_ok, baseline: collect_ok}}` + the aggregate lift.

- [ ] **Step 1: Provision the 3 pilot repos at their pinned SHAs**

Use the existing provisioning path (fetch + reset to `sha`/`base_commit` from `datasets/pilot.json`). Confirm each repo tree is present locally before rendering.

Run: `python scripts/gate_a_cure_recovery.py --corpus datasets/pilot.json --provision-only`
Expected: 3 repo dirs materialized at the pinned commits.

- [ ] **Step 2: Render the two arms per pilot**

For each pilot: render the config-lane arm with `run_v3_e2e` (setup.sh with the `-e .` capstone), and derive the **baseline** arm by copying that setup.sh with the editable-install capstone line removed (isolates the editable install's contribution). The driver does this:

```python
# scripts/gate_a_cure_recovery.py (core)
import json, subprocess, sys
from pathlib import Path
from src.eval.build_script_eval.replay import run_replay_ladder

_EDITABLE_MARKERS = ("pip install", "-e .")  # the capstone line to strip for baseline

def _render(repo_dir: Path, image: str, out: Path) -> str:
    subprocess.run([sys.executable, "scripts/run_v3_e2e.py", str(repo_dir),
                    "--out", str(out), "--base-image", image], check=True)
    return out.read_text()

def _strip_editable(setup: str) -> str:
    return "\n".join(l for l in setup.splitlines()
                     if not (all(m in l for m in _EDITABLE_MARKERS)))

def measure(corpus: str, image: str) -> dict:
    rows = {}
    for entry in json.loads(Path(corpus).read_text()):
        name = entry["full_name"]; repo_dir = Path("work") / name.replace("/", "__")
        cfg = _render(repo_dir, image, repo_dir / "setup.cfg.sh")
        base = _strip_editable(cfg)
        rows[name] = {
            "configlane": run_replay_ladder(str(repo_dir), image, cfg).collect_ok,
            "baseline":   run_replay_ladder(str(repo_dir), image, base).collect_ok,
        }
    return rows
```

- [ ] **Step 3: Run the pilot measurement and read the lift**

Run: `python scripts/gate_a_cure_recovery.py --corpus datasets/pilot.json --base-image <arm64-python-image>`
Expected: a table + aggregate, e.g. `configlane 3/3 collect-clean vs baseline 1/3` — i.e. the editable install is what turns collection green. The `srclayout_editable` known-answer case (`src/eval/graph_fidelity/edge_cases/srclayout_editable/`, `editable_required: true`) is the canonical positive.

- [ ] **Step 4: Scale to the 50-repo corpus**

Run: `python scripts/gate_a_cure_recovery.py --corpus datasets/rat_python50_pinned_m3nothink.json --base-image <img>`
Record the config-lane vs baseline collect-clean counts.

- [ ] **Step 5: Apply the go/no-go criterion (and write it down)**

**GO** if the config-lane arm materially lifts collect-clean over baseline on the corpus (target: recovers a meaningful share of the 34-build→14-collect gap — the project-namespace `ModuleNotFoundError` class). **NO-GO** if editable-install + rootdir does not move collection — then the config lane is not worth building and Stage B is cancelled. Write the numbers + verdict to `docs/superpowers/handoffs/2026-07-17-gate-a-result.md` either way (no silent caps: list every repo excluded/errored).

- [ ] **Step 6: Commit**

```bash
git add scripts/gate_a_cure_recovery.py docs/superpowers/handoffs/2026-07-17-gate-a-result.md
git commit -m "feat(eval): Gate A cure-recovery harness + result (config-lane go/no-go)" -- scripts/gate_a_cure_recovery.py docs/superpowers/handoffs/2026-07-17-gate-a-result.md
```

**If NO-GO: stop here. Do not implement Tasks 2–3.**

---

### Task 2: Rename `NodeType.FILE` → `MODULE`

Trivial foundation (proceed only on a GO). The member is inert scaffolding; the survey verified all traps clean (the `envstate/contracts` enum has no `FILE`; the eval `NodeType`-set assertions are dynamic).

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (`:30`, `:94`, `:96`)
- Modify: `tests/depgraph/test_schema_roundtrip.py` (`:221`)

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schema_roundtrip.py`:

```python
def test_module_node_type_exists_and_file_is_gone():
    from python_deps.depgraph.schema import NodeType
    assert NodeType.MODULE.value == "Module"
    assert not hasattr(NodeType, "FILE")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_schema_roundtrip.py::test_module_node_type_exists_and_file_is_gone -v`
Expected: FAIL — `NodeType` has `FILE`, not `MODULE`.

- [ ] **Step 3: Rename member + value + edge rules**

- `schema.py:30`: `FILE = "File"` → `MODULE = "Module"`.
- `schema.py:94` (allowed `requires` **src** frozenset): `"File"` → `"Module"`.
- `schema.py:96` (allowed `requires` **dst** frozenset): `"File"` → `"Module"`.

- [ ] **Step 4: Update the existing scaffold test**

In `tests/depgraph/test_schema_roundtrip.py:221`, change `type=NodeType.FILE` → `type=NodeType.MODULE` in `test_file_node_is_legal_requires_src_and_dst` (rename the test to `test_module_node_is_legal_requires_src_and_dst`; the `id="file:..."` strings are a node-id convention, cosmetic — may stay or become `module:`).

- [ ] **Step 5: Run the depgraph suite**

Run: `python -m pytest tests/depgraph/ -q`
Expected: PASS (the eval `NodeType`-set tests follow dynamically — no edit needed).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema_roundtrip.py
git commit -m "refactor(depgraph): rename NodeType.FILE -> MODULE (local-module lane)" -- src/python_deps/depgraph/schema.py tests/depgraph/test_schema_roundtrip.py
```

---

### Task 3: Extend `TestEnvPlan` with `cwd` + `env` and reconcile the config readers

The canonical collection invocation must carry cwd and env-vars so the Stage-B collect-gate and per-name probe cannot diverge. The dataclass and its pure `resolve()` already exist; this adds the two missing fields and wires the (already-built) env-var discovery in.

**Files:**
- Modify: `src/python_deps/depgraph/invocation_resolver.py` (`TestEnvPlan` `:90-110`; `resolve` `:113`; assembly `:143-145`)
- Reuse (read): `src/python_deps/depgraph/config_scan.py` (`scan_authoritative_config:482`, `authoritative_ambiguous_vars:505`)
- Test: `tests/depgraph/test_invocation_resolver.py` (create if absent)

**Interfaces:**
- Produces: `TestEnvPlan` gains `cwd: str` (default = `rootdir`) and `env: tuple[tuple[str, str], ...]` (sorted `(var, value)` of unambiguous authoritative vars; ambiguous vars dropped).

- [ ] **Step 1: Write the failing test**

```python
def test_testenvplan_carries_cwd_and_unambiguous_env(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "tox.ini").write_text(
        "[testenv]\nsetenv =\n    DJANGO_SETTINGS_MODULE=app.settings\n"
    )
    from python_deps.depgraph.invocation_resolver import resolve
    plan = resolve(str(tmp_path))
    assert plan.cwd == plan.rootdir            # default cwd = rootdir
    assert ("DJANGO_SETTINGS_MODULE", "app.settings") in plan.env
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_invocation_resolver.py::test_testenvplan_carries_cwd_and_unambiguous_env -v`
Expected: FAIL — `TestEnvPlan` has no `cwd`/`env`.

- [ ] **Step 3: Add the fields + wire discovery**

In `invocation_resolver.py`, add to the `TestEnvPlan` dataclass (`:90-110`):

```python
    cwd: str = "."
    env: tuple[tuple[str, str], ...] = ()
```

In `resolve()` where the plan is constructed (`:143-145`), populate them:

```python
    from python_deps.depgraph.config_scan import (
        scan_authoritative_config, authoritative_ambiguous_vars,
    )
    ambiguous = authoritative_ambiguous_vars(repo_path)
    env = tuple(sorted(
        (k, v) for k, v in scan_authoritative_config(repo_path).items()
        if k not in ambiguous
    ))
    # cwd defaults to the discovered rootdir (absolute materialization is the
    # cure-runner's job in Stage B; here it mirrors rootdir).
    cwd = rootdir
```

Pass `cwd=cwd, env=env` into the `TestEnvPlan(...)` construction.

- [ ] **Step 4: Note the reader search-scope gap (do NOT fix here)**

Add a `# TODO(stage-b):` comment where env is wired: `config_scan`'s env readers search the **repo root only**, while `_discover_pytest_config` searches `["."] + project_dirs`; a feast-style `sdk/python/tox.ini` `setenv` is missed. Reconciling the two readers' search scope is Stage B (the cure-runner needs the materialized plan); Stage A only surfaces the unambiguous root-level vars. Leaving this as a TODO keeps Task 3 pure and additive.

- [ ] **Step 5: Run the test + the depgraph suite**

Run: `python -m pytest tests/depgraph/test_invocation_resolver.py tests/depgraph/ -q`
Expected: PASS. (Adding defaulted fields is additive; existing `resolve()` callers are unaffected.)

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/invocation_resolver.py tests/depgraph/test_invocation_resolver.py
git commit -m "feat(depgraph): TestEnvPlan carries cwd + unambiguous env for the canonical collect invocation" -- src/python_deps/depgraph/invocation_resolver.py tests/depgraph/test_invocation_resolver.py
```

---

## Deferred to Stage B (not in this plan)

- **The in-construction cure runner** — the repo-mount primitive on `DockerExecutor` (or `docker cp`; precedent `_MountedContainer`, `coverage.py:555-586`), the editable-install build-isolation fallback chain, the in-container collect-gate, and stamping scratch-certified state at the `_python_package_obligations` tail (`build.py:1008-1015`).
- **The `populate` poison reconciliation** — the one-line gate on `data["scratch_certified"]` at `populate.py:224-225`.
- **The config-reader search-scope reconciliation** (Task 3 Step 4 TODO).
These are needed only when route-not-drop makes the cure run *during* construction; Gate A does not need them.

## Self-review

- **Spec coverage:** implements the Stage-A slice of `2026-07-17-config-lane-module-spine-and-collision-arbitration-design.md` (the `TestEnvPlan` + the `MODULE` rename) plus the go/no-go the strategy calls for; the cure runner is explicitly deferred with the exact Stage-B anchors, so nothing is silently dropped.
- **Placeholder scan:** none — Task 1's harness code, the rename edit sites, and the `TestEnvPlan` field additions are all concrete with file:line anchors from the surveys.
- **Type consistency:** `TestEnvPlan` gains `cwd: str` / `env: tuple[tuple[str,str],...]`, defaulted (additive); `run_replay_ladder(repo_dir, image, setup_script) -> LadderResult.collect_ok` is used as its real signature; `NodeType.MODULE` value `"Module"` is consistent across schema + edge rules + tests.
