# Pinned Seed Dockerfile — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (sonnet implementers) to execute task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the v1 agent emit a single, faithful seed Dockerfile from the messy container, by (A) decoupling run-success from artifact synthesis and (B) appending a deterministic dependency-closure pin sourced from the already-computed `final_map.installed`. **No change to the agent's in-container build logic** — only how history is recorded and how the seed is synthesized.

**Architecture:** Staged, smallest-first. Change A is a standalone ~5-line gate-decouple (independently recovers 11-15 repos per `outputs/v1_coverage_loss_report.md`). Change B threads the live closure to finalize + serializes it (recording). Change C appends the pin (synthesis). The benchmark runner stays untouched; the pin avoids the runner's `-r` parse bug by using a distinct file basename.

**Tech Stack:** Python, pytest, Docker. Source of truth corrected by the 2026-06-18 sonnet review (see `docs/dockerfile-seed-iteration-log.md`).

**Design spec:** `docs/superpowers/specs/2026-06-18-pinned-seed-dockerfile-design.md` (review corrections folded in here; this plan is authoritative).

**Grounded facts (verified):**
- v1 success/finalize: `agent.py:1188-1227`. Gate-flip sites: `1193-1196` (normal) and `1209-1216` (transient). Container alive through finalize (`sandbox.close` at `1225`).
- `final_map.installed: tuple[Fact, ...]` (`world_model.py:76`) is the live closure at loop end, refreshed by `apply_deterministic(..., probe(), ...)` (`orchestrator.py:190`). `Fact(name, detail=version)` (`snapshot.py:41`). `_parse_installed` already drops `-e`/VCS/no-`==` lines → only a non-editable project install can leak.
- Deterministic synthesis: `build_commands_from_ledger` (`src/envstate/synthesis.py`). Recipe → `apply_build_recipe` (`synthesizer.py:2715`) → `self.instructions` → `generate_dockerfile` (`synthesizer.py:3986`) → `_render_instruction_for_dockerfile` (`synthesizer.py:3134`) which retry-wraps any RUN matching anchored `_looks_like_pip_install_command` (`synthesizer.py:3948`).
- Runner `_PIP_INSTALL_OPTION_VALUE_FLAGS` (`run_repo2run_benchmark.py:1014`) lacks `-r` → `pip install -r <path>` misparses `<path>` as a local project. Mitigation: pin file basename `jayint-pinned-closure.txt` (no real package normalizes to it).

---

## File Structure

- `agent.py` — gate-decouple at the two call sites (A); store `self._final_installed` after the loop + thread to finalize + serialize to summary (B); call the pin appender inside `_finalize_supervisor_artifacts` (C).
- `src/envstate/synthesis.py` — new `build_pin_instructions(...)` (C). Pure, unit-testable.
- `tests/test_gate_decouple_v1.py` — new (A).
- `tests/test_build_pin_instructions.py` — new (C).
- `tests/test_seed_pin_integration.py` — new, `@pytest.mark.docker` opt-in (D).

---

### Change A: Decouple run-success from artifact synthesis (ship first, standalone)

**Why:** `agent.py:1193-1196` overwrites a host-certified `configuration_success=True` with the return of `_finalize_supervisor_artifacts`, which returns `False` on any synthesis hiccup (`agent.py:1344`). The transient path `1209-1216` sets `configuration_success=False` on any synthesis exception. Either silently voids a genuine test pass.

**Files:** Modify `agent.py` (call sites only — `_finalize` internals untouched in this change). Test `tests/test_gate_decouple_v1.py`.

- [ ] **Step 1 — failing test** `tests/test_gate_decouple_v1.py`:
```python
from unittest.mock import patch
from agent import DockerAgent

def _agent_at_finalize(tmp_path):
    a = DockerAgent.__new__(DockerAgent)
    a.workplace = str(tmp_path)
    a.verification_bundle = {"test_commands": ["python -m pytest -q"]}
    a.verified_test_commands = ["python -m pytest -q"]
    return a

def test_synthesis_failure_does_not_void_passed_gate(tmp_path):
    a = _agent_at_finalize(tmp_path)
    # gate passed:
    a._auto_finalize_from_verified_tests = lambda source: True
    # synthesis blows up:
    def boom(*_a, **_k): raise RuntimeError("synth exploded")
    a._finalize_supervisor_artifacts = boom
    cs = DockerAgent._v1_finalize_and_keep_success(a, gate_passed=True)
    assert cs is True   # success preserved despite synth failure
```
(The helper `_v1_finalize_and_keep_success` is introduced in Step 3 to make both call sites testable and identical.)

- [ ] **Step 2 — run, expect FAIL** (`_v1_finalize_and_keep_success` undefined): `python3 -m pytest tests/test_gate_decouple_v1.py -q`

- [ ] **Step 3 — implement.** Add the helper and route BOTH call sites through it.
```python
def _v1_finalize_and_keep_success(self, gate_passed):
    """Run the artifact step for its side effects (Dockerfile, memories) but NEVER
    let it change run-success. Success is the host-certified test gate alone."""
    if not gate_passed:
        return False
    try:
        ok = self._finalize_supervisor_artifacts(gate_passed)
        if not ok:
            print("[v1] artifact synthesis returned False; run still counts as success (test gate passed).")
    except Exception as synth_exc:
        print(f"[v1 Warning] artifact synthesis raised; run still counts as success: {synth_exc}")
    return True
```
Replace `agent.py:1193-1196` with:
```python
            if configuration_success:
                configuration_success = self._v1_finalize_and_keep_success(configuration_success)
```
Replace the transient block `agent.py:1209-1216` with:
```python
                configuration_success = self._v1_finalize_and_keep_success(configuration_success)
```
(Drops the `except: configuration_success = False`. Append `run_error` context inside the helper if desired.)

- [ ] **Step 4 — run, expect PASS.** Also run `python3 -m pytest tests/test_agent_v1_glue.py -q` and fix any now-stale assertion that expected synthesis failure to fail the run.
- [ ] **Step 5 — log** to `docs/dockerfile-seed-iteration-log.md` (why/fix/results) and **commit** `agent.py tests/test_gate_decouple_v1.py`.

---

### Change B: Record the live closure (thread `final_map.installed` + serialize)

**Why:** the closure needed for the pin is already computed (`final_map.installed`) but discarded; `agent_run_summary.json` has `has_installed=false`. We retain it (for synthesis) and serialize it (for inspection + future runner-side reuse). No probe added (avoids an extra `exec_readonly` round-trip + timing hazard).

**Files:** Modify `agent.py`.

- [ ] **Step 1 — store after the loop.** Init `self._final_installed = ()` at `_run_v1` start. Immediately after `final_map, stop_reason = _run_v1_loop(...)` (`agent.py:1174`), add:
```python
            self._final_installed = tuple(getattr(final_map, "installed", ()) or ())
```
- [ ] **Step 2 — serialize to the run summary.** In the run-summary builder (`_build_run_summary` / `_write_run_summary`), add:
```python
            "installed": [f"{f.name}=={f.detail}" for f in getattr(self, "_final_installed", ()) if f.name and f.detail],
```
- [ ] **Step 3 — test:** a `_run_v1` unit (or a re-run) asserts `agent_run_summary.json` now has a non-empty `installed` for a successful run. Confirm via the next e2e run (`has_installed=true`).
- [ ] **Step 4 — log + commit** `agent.py`.

---

### Change C: Append the dependency-closure pin (synthesis)

**Why:** the deterministic recipe replays `pip install -e .` which re-resolves deps fresh at build time (drift / dropped transitive deps). A `pip install -r <frozen closure>` locks exact versions AND backfills any structurally-dropped package. Pin is LAST, two separate RUNs (so the pip install gets the retry wrapper), inline-materialized (survives the runner's `git clean -fdx`), project package excluded.

**Files:** `src/envstate/synthesis.py` (new fn), `agent.py` (call it in `_finalize_supervisor_artifacts`). Test `tests/test_build_pin_instructions.py`.

- [ ] **Step 1 — failing test** `tests/test_build_pin_instructions.py`:
```python
from src.envstate.world_model import Fact
from src.envstate.synthesis import build_pin_instructions

def F(n, v): return Fact(name=n, detail=v)

def test_emits_two_commands_printf_then_pip_r():
    cmds = build_pin_instructions((F("requests","2.31.0"), F("urllib3","2.0.0")))
    assert len(cmds) == 2
    assert cmds[0].startswith("printf ") and ">" in cmds[0]
    assert cmds[1].startswith("pip install -r ") and "&&" not in cmds[1]
    assert "requests==2.31.0" in cmds[0] and "urllib3==2.0.0" in cmds[0]

def test_excludes_project_by_normalized_name():
    cmds = build_pin_instructions((F("My_Pkg","0.1.0"), F("requests","2.31.0")), project_name="my-pkg")
    assert "requests==2.31.0" in cmds[0]
    assert "my_pkg" not in cmds[0].lower() and "my-pkg" not in cmds[0].lower()

def test_empty_or_no_versions_returns_empty():
    assert build_pin_instructions(()) == []
    assert build_pin_instructions((Fact(name="x", detail=""),)) == []
```
- [ ] **Step 2 — run, expect FAIL** (`build_pin_instructions` undefined).
- [ ] **Step 3 — implement** in `src/envstate/synthesis.py`:
```python
PIN_PATH = "/tmp/jayint-pinned-closure.txt"  # distinct basename: dodges runner's -r misparse

def _norm(s):
    return (s or "").strip().lower().replace("_", "-")

def build_pin_instructions(installed, *, project_name=None, pin_path=PIN_PATH):
    """Two RUN-body strings [printf_write, pip_install_r] pinning the exact closure,
    or [] if nothing to pin. Excludes the project's own package by normalized name.
    Inputs are Fact(name, detail=version); editable/VCS are already absent (no '==')."""
    proj = _norm(project_name)
    specs = []
    for f in installed or ():
        name = getattr(f, "name", "") or ""
        ver = getattr(f, "detail", "") or ""
        if not name or not ver:
            continue
        if proj and _norm(name) == proj:
            continue
        specs.append(f"{name}=={ver}")
    if not specs:
        return []
    quoted = " ".join("'" + s + "'" for s in specs)  # name==version is shell-safe
    return [f"printf '%s\\n' {quoted} > {pin_path}", f"pip install -r {pin_path}"]
```
- [ ] **Step 4 — wire into finalize.** In `_finalize_supervisor_artifacts` (`agent.py:1339`), AFTER the recipe is applied but BEFORE `generate_dockerfile()`, append the pin to the synthesizer's instruction list so `generate_dockerfile` renders it (and the `pip install -r` gets the retry wrapper). Resolve `project_name` from `pyproject.toml`/`setup.cfg`/`setup.py` in `self.workplace`. Make the whole block exception-safe (best-effort; Change A guarantees a raise can't fail the run, but keep finalize internally defensive):
```python
        try:
            from src.envstate.synthesis import build_pin_instructions
            pin = build_pin_instructions(
                getattr(self, "_final_installed", ()),
                project_name=self._resolve_project_name(),  # small helper reading pyproject/setup.*
            )
            for cmd in pin:
                self.synthesizer.add_build_instruction(cmd)  # exact append API per apply_build_recipe format
        except Exception as exc:
            print(f"[v1] pin layer skipped: {exc}")
```
**Implementer note:** read `apply_build_recipe` (`synthesizer.py:2715`) to use the correct append API/format so the two commands land as the LAST instructions and render through `_render_instruction_for_dockerfile`. If no clean append API exists, append the two commands to the recipe's `build_commands` list before `apply_build_recipe` instead.
- [ ] **Step 5 — run** the unit tests (PASS), **log + commit**.

---

### Change D: Seed-quality rubric as automatable tests

**Why:** the empirical loop needs an objective pass/fail. Encode the review's 6-check rubric.

- [ ] `tests/test_seed_pin_integration.py` (`@pytest.mark.docker`, opt-in). On a requests-only fixture repo seed:
  1. `grep -E 'COPY.*(pinned|requirements)' Dockerfile` is empty (inline RUN materialization).
  2. Dockerfile has a `RUN printf ... > /tmp/jayint-pinned-closure.txt` followed by a SEPARATE `RUN ... pip install -r /tmp/jayint-pinned-closure.txt` (not joined by `&&`).
  3. The pin `pip install -r` is the LAST RUN in the file.
  4. `docker run <img> grep -i <project> /tmp/jayint-pinned-closure.txt` exits non-zero (project excluded).
  5. `docker run <img> pip show certifi` exits 0 (transitive closure captured; structural-only would fail).
  6. With `_finalize_supervisor_artifacts` mocked to raise, `_run_v1` returns `configuration_success=True` (gate decoupled).
- [ ] Non-docker unit assertions for checks 1-3, 6 (parse a generated Dockerfile string). Checks 4-5 are docker-gated.

---

### Change E: Empirical e2e loop (sonnet workflow)

**Why:** the goal — a method that consistently produces the best seed in one shot. Iterate recording/synthesis against real captured + fresh sessions, judged by the rubric.

- [ ] After A-C land, re-run the **microsearch control** + **burr complex** + 1-2 harder repos via `outputs/e2e_v1g/run_one.py`. For each: capture the seed, score against the rubric (sonnet analyst), clean `docker build` the produced seed.
- [ ] Compare before/after (pin present? closure complete? builds clean? project excluded?). Log every iteration in `docs/dockerfile-seed-iteration-log.md` (why/fix/results).
- [ ] If a repo still produces a weak seed, root-cause it (selection? mutation_class misclass? closure?) and make the next concise recording/synthesis fix. Repeat until the rubric passes consistently across the repo set.

---

## Risks / decisions (from review)

- **Runner `-r` parse bug:** mitigated agent-side via the distinct pin basename. The proper one-line fix (add `-r`/`--requirement` to `_PIP_INSTALL_OPTION_VALUE_FLAGS:1014`) touches the runner → leave for user approval; note in the log.
- **PEP 668 / externally-managed:** the official `python:3.x` bases used here do NOT enforce it (pip works), so plain `pip install -r` matches the structural style. If a base image ever enforces it, the pin must inherit the structural `--break-system-packages`/venv prefix. Logged as a watch item, not implemented now.
- **Exact-version pin regression:** a frozen version with no platform wheel + a missing compiler in the structural apt layer can fail where unpinned would have resolved a newer wheel. Narrow (scipy/numpy on arm64). Surface in build logs; the runner repair loop is the recovery.
- **VCS/local-path deps:** absent from both `installed` and the pin (no `==`). If structural replay also dropped them, only the repair loop recovers. Log a warning when `dep_tree` shows direct-URL installs absent from the pin.
