# Runtime-Tier Base Python Pin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Revised 2026-06-27** after a 4-agent Sonnet review (grounding / architecture / TDD / measurement). Changes folded in: `DiscoveredBy.STATIC_SCAN` (not `STATIC`); the run-summary now reads the **live** final graph, not the tautological scratch graph; the seam stores a `RuntimeBaseDecision` on `self` (no double-derivation); new **Phase 0.5 pre-screen gate**; `base_changed`/`original_base`/`reason` instrumentation so a null can't hide a bug; hardened tests (forwarding, `self.base_image`, exception path, project `FakeExecutor`); corrected line anchors.

**Goal:** Let the static dep-graph choose the **Python version** of the agent's base image — derive `python:3.X` from the project's declared `requires-python`, build the live container on it, and certify the choice against the *running* container — gated behind a default-off arm, instrumented so its effect (or genuine null) is measurable.

**Architecture:** Pre-build (no container yet), read the repo's declared python constraint → pick one concrete minor → rewrite the base-image tag. The live sandbox is built on the pinned base; the Synthesizer (constructed right after the seam) inherits it, so the emitted Dockerfile matches; the same pinned base feeds the scratch dep-graph build. A Runtime-tier node certifies — **against the live container, via the final loop graph** — that the running interpreter satisfies the required minor. Flow: **propose (static) → verify (host certify on the live container) → falsify** (a `MISSING` Runtime node is a visible obligation the runtime-feedback loop can act on). Nothing changes when the arm is off.

**Tech Stack:** Python 3.10+ (3.10 on the benchmark VM, 3.14 locally), `packaging` (SpecifierSet/Version), `tomllib`/`tomli`, pytest. Docker only at e2e time.

## Global Constraints

- **Default off, byte-identical when off.** Every change gates on `enable_runtime_pin`. With the flag off: no image differs, no graph node is added, no `sys.path` changes, and the run-summary JSON has no `runtime_pin` key. (Project invariant; mirrors every prior arm.)
- **Pure functions never raise.** `read_requires_python`, `choose_python_minor`, `pin_base_python`, `resolve_runtime_base`, `screen_runtime_pin` return fallbacks on malformed input; never throw.
- **Layering.** `python_deps.depgraph.*` must not import `src.envstate.*`. The Runtime decision is computed in the `agent.py` / `src.envstate` layer and passed *down* into the pure depgraph as a plain `target_python` value. `depgraph_live.py` stays the only bridge.
- **Host owns truth, on the LIVE container.** A statically-chosen minor is a *proposal*. Only a check run in the **live agent container** (via `certify_refresh` → the final loop graph) may set the Runtime node `SATISFIED`/`MISSING`. The scratch-container certify is NOT the reported signal — it would be tautological (the scratch container *is* the pinned base).
- **Conservative pinning.** Non-`python:<version>` base, or undeclared/unparseable/out-of-range constraint → base image unchanged.
- **Policy v1 (locked):** lowest supported minor satisfying the floor. Supported: `("3.9","3.10","3.11","3.12","3.13")`. Default when undecidable: `"3.11"`.
- **Measurability is a requirement, not an afterthought.** The team has shipped advisory features that landed inert with no null-detector. This feature MUST emit `base_changed` + `original_base` + `reason` + live `certified` so that "inert (pin no-op)", "genuine null (declared==default)", and "silently broken (infeasible pin)" are distinguishable. Phase 0.5 gates the whole effort on whether the benchmark set can even produce signal.

---

## File Structure

| File | Responsibility | Phase |
|------|----------------|-------|
| `src/envstate/manifest.py` | `read_requires_python()` | 0 ✅ |
| `src/envstate/runtime_base.py` | `choose_python_minor`, `pin_base_python`, `resolve_runtime_base`, `RuntimeBaseDecision` | 0 ✅ |
| `src/envstate/runtime_base.py` | `screen_runtime_pin()` — pure pre-screen | 0.5 |
| `scripts/screen_runtime_pin.py` | CLI: count `base_would_change` over a dir of cloned repos (go/no-go gate) | 0.5 |
| `agent.py` | `enable_runtime_pin` flag + base-override **seam at line ~462** + `_apply_runtime_pin` helper + argparse/forward | 1 |
| `multi_docker_eval_adapter.py` | read `DOCKERAGENT_ENABLE_RUNTIME_PIN`, pass to `DockerAgent` | 1 |
| `run_rat_benchmark.py` | `v1gsp` arm ↔ env mapping | 1 |
| `run_repo2run_benchmark.py` | `v1gsp` preset + `--enable-runtime-pin` forward | 1 |
| `src/python_deps/depgraph/ids.py` | `runtime_id()` | 2 |
| `src/python_deps/depgraph/build.py` | add the Runtime node from `target_python` | 2 |
| `src/python_deps/depgraph/certify.py` | include `Layer.RUNTIME` in the certify walk | 2 |
| `src/python_deps/depgraph/advise.py` | forward `target_python` into `build_dep_graph` | 3 |
| `agent.py` (`_build_run_summary`, line 3055) | emit the `runtime_pin` record (live certify + base_changed) | 3 |

---

## Phase 0 — Pure core ✅ COMPLETED

Implemented test-first this session (34 tests green). Listed so an executor doesn't redo it and later tasks know exact signatures.

**Produces:**
- `read_requires_python(workplace: str) -> str | None` (`manifest.py`)
- `choose_python_minor(requires_python: str | None, default: str = "3.11") -> tuple[str, str]` → `(minor, reason)`
- `pin_base_python(base_image: str, minor: str) -> str`
- `RuntimeBaseDecision(minor: str, base_image: str, reason: str, requires_python: str | None)` (frozen)
- `resolve_runtime_base(repo_path: str, base_image: str, *, default: str = "3.11") -> RuntimeBaseDecision`

- [x] Implemented + tested: `tests/test_manifest_requires_python.py`, `tests/test_runtime_base.py`
- [x] Verify still green:

```bash
python3 -m pytest tests/test_manifest_requires_python.py tests/test_runtime_base.py tests/test_manifest.py -q
# Expected: 34 passed
```

---

## Phase 0.5 — Pre-screen gate (pure, no Docker) — RUN THIS FIRST

The cheapest possible measurement. Before building any integration, answer: **on the benchmark set, how many repos would the pin actually change the base for?** If too few, the `v1gsp` vs `v1gs` A/B is structurally incapable of producing signal and the feature would land inert-and-undetected — stop and re-scope. Pure file reads; no container, no LLM.

### Task 0.5: `screen_runtime_pin` + CLI

**Files:**
- Modify: `src/envstate/runtime_base.py` (add `screen_runtime_pin`)
- Create: `scripts/screen_runtime_pin.py`
- Test: `tests/test_runtime_screen.py`

**Interfaces:**
- Consumes: `read_requires_python`, `choose_python_minor`, `DEFAULT_MINOR` (Phase 0).
- Produces: `screen_runtime_pin(repo_path: str, default: str = "3.11") -> dict` with keys `requires_python`, `would_pin_to`, `reason`, `base_would_change`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runtime_screen.py
from src.envstate.runtime_base import screen_runtime_pin


def test_declared_low_floor_would_change(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.9"\n')
    r = screen_runtime_pin(str(tmp_path))
    assert r["would_pin_to"] == "3.9"
    assert r["base_would_change"] is True


def test_undeclared_no_change(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    r = screen_runtime_pin(str(tmp_path))
    assert r["would_pin_to"] == "3.11"
    assert r["requires_python"] is None
    assert r["base_would_change"] is False


def test_declared_equals_default_no_change(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n')
    r = screen_runtime_pin(str(tmp_path))
    assert r["would_pin_to"] == "3.11"
    assert r["base_would_change"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_runtime_screen.py -q`
Expected: FAIL — `ImportError: cannot import name 'screen_runtime_pin'`.

- [ ] **Step 3: Implement** — append to `src/envstate/runtime_base.py`:

```python
def screen_runtime_pin(repo_path: str, default: str = DEFAULT_MINOR) -> dict:
    """Pure pre-screen: what would the pin do for this repo, no container needed.

    ``base_would_change`` approximates "the live base would differ from the
    selector's usual default" as ``declared AND chosen != default``. It is a
    go/no-go signal for the A/B, not ground truth (the real selector may pick a
    non-default minor); e2e confirms.
    """
    requires_python = read_requires_python(repo_path)
    minor, reason = choose_python_minor(requires_python, default=default)
    return {
        "requires_python": requires_python,
        "would_pin_to": minor,
        "reason": reason,
        "base_would_change": requires_python is not None and minor != default,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_runtime_screen.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Add the CLI script** (`scripts/screen_runtime_pin.py`)

```python
#!/usr/bin/env python3
"""Pre-screen (no Docker): how many repos would the runtime pin actually change?
If fewer than 5 show base_would_change, the v1gsp A/B cannot produce signal on
this set — do NOT spend an e2e run on it.

Usage: python3 scripts/screen_runtime_pin.py <dir-of-cloned-repos>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.envstate.runtime_base import screen_runtime_pin  # noqa: E402


def main(root: str) -> None:
    rows = []
    for name in sorted(os.listdir(root)):
        repo = os.path.join(root, name)
        if os.path.isdir(repo):
            rows.append((name, screen_runtime_pin(repo)))
    changed = sum(1 for _, r in rows if r["base_would_change"])
    for name, r in rows:
        print(f"{name:40} requires={str(r['requires_python']):22} "
              f"-> {r['would_pin_to']:6} change={r['base_would_change']}")
    verdict = "OK to A/B" if changed >= 5 else "TOO FEW — A/B cannot produce signal; do not e2e"
    print(f"\n{changed}/{len(rows)} repos would get a different base. {verdict}")


if __name__ == "__main__":
    main(sys.argv[1])
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_runtime_screen.py src/envstate/runtime_base.py scripts/screen_runtime_pin.py
git commit -m "feat(depgraph): runtime-pin pre-screen (pure go/no-go gate for the A/B)"
```

- [ ] **Step 7: RUN THE GATE** (manual, against the cloned benchmark set on the VM/box)

Run: `python3 scripts/screen_runtime_pin.py <path-to-cloned-benchmark-repos>`
**Decision:** if `< 5` repos show `base_would_change`, STOP — record the number and re-scope (the pin can't move the metric on this set). Otherwise note the discriminating subset (the `change=True` rows, especially `would_pin_to` 3.9/3.10) for Phase 4.

---

## Phase 1 — Arm-gated base override (the feature)

Makes the **live container** build on the pinned base. Seam: `agent.py` line ~462, after `base_image` is finalized (both the `"auto"`/`ImageSelector` branch and the explicit-`python:` branch at line 459) and **before** `self.sandbox = self._create_sandbox(base_image=...)` at line 464. Verified facts the seam relies on: `self.base_image` is never assigned (so the read-back at line 1020 falls through to `self.synthesizer.base_image`); the Synthesizer is constructed at line 511 (**after** the seam) so it inherits the pinned base and its emitted Dockerfile matches the live container.

### Task 1.1: `enable_runtime_pin` flag plumbing

**Files:**
- Modify: `agent.py` (ctor param `enable_graph_scheduler=False,` is line **239**; derived-flag block ends ~304; argparse ~3227; forward ~3281)
- Modify: `multi_docker_eval_adapter.py:782` (env read) and `:805` (construct)
- Modify: `run_rat_benchmark.py:392` (env→arm) and `:843-848` (arm→env) + `--arm choices`
- Modify: `run_repo2run_benchmark.py:220` (forward), preset block (after line **3198**, before `}`), `:3358` (choices)
- Test: `tests/test_runtime_pin_flag.py`

**Interfaces:**
- Produces: `DockerAgent(enable_runtime_pin: bool = False)`; arm `"v1gsp"`; env `DOCKERAGENT_ENABLE_RUNTIME_PIN`; CLI `--enable-runtime-pin`.

- [ ] **Step 1: Write the failing test** (mirrors `tests/test_graph_scheduler_flag.py` — `__init__` is too heavy to construct, so assert on source text)

```python
# tests/test_runtime_pin_flag.py
from __future__ import annotations
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_docker_agent_has_enable_runtime_pin_param():
    src = (_ROOT / "agent.py").read_text()
    assert "enable_runtime_pin=False" in src
    assert "self.enable_runtime_pin: bool = bool(enable_runtime_pin)" in src


def test_runtime_pin_is_independent_no_implications():
    # must NOT imply dep_graph/v1 — measured as an orthogonal toggle on any arm
    src = (_ROOT / "agent.py").read_text()
    assert "or self.enable_runtime_pin" not in src


def test_argparse_exposes_enable_runtime_pin():
    src = (_ROOT / "agent.py").read_text()
    assert '"--enable-runtime-pin"' in src
    assert "enable_runtime_pin=args.enable_runtime_pin" in src


def test_adapter_reads_env():
    src = (_ROOT / "multi_docker_eval_adapter.py").read_text()
    assert "DOCKERAGENT_ENABLE_RUNTIME_PIN" in src
    assert "enable_runtime_pin=_enable_runtime_pin" in src


def test_rat_benchmark_sets_env_and_arm():
    src = (_ROOT / "run_rat_benchmark.py").read_text()
    assert "DOCKERAGENT_ENABLE_RUNTIME_PIN" in src
    assert '"v1gsp"' in src


def test_repo2run_has_v1gsp_preset_and_forward():
    src = (_ROOT / "run_repo2run_benchmark.py").read_text()
    assert '"v1gsp"' in src
    assert '"enable_runtime_pin": True' in src
    assert "--enable-runtime-pin" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_runtime_pin_flag.py -q`
Expected: FAIL (6 failed).

- [ ] **Step 3: Implement the plumbing** (each edit mirrors the `enable_graph_scheduler` line found by grep)

`agent.py` ctor param — add after `enable_graph_scheduler=False,` (line 239):
```python
        enable_runtime_pin=False,
```
`agent.py` derived flags — add after the `enable_deterministic_maintainer` derivation (~line 304), with NO implications:
```python
        # Runtime-tier base pin: orthogonal to the graph arms — rewrites the base
        # image's python BEFORE the sandbox is built. Independent toggle (no implies).
        self.enable_runtime_pin: bool = bool(enable_runtime_pin)
```
`agent.py` argparse — add after the `--enable-graph-scheduler` block (~3227):
```python
    parser.add_argument("--enable-runtime-pin", action="store_true",
                        help="Pin the base image's python to the project's requires-python "
                             "before building the container (Runtime tier; default off).")
```
`agent.py` forward — add after `enable_graph_scheduler=args.enable_graph_scheduler,` (~3281):
```python
        enable_runtime_pin=args.enable_runtime_pin,
```
`multi_docker_eval_adapter.py` — add after the `_enable_graph_scheduler` env read (~782):
```python
            _enable_runtime_pin = os.environ.get("DOCKERAGENT_ENABLE_RUNTIME_PIN", "").lower() in ("1", "true", "yes", "on")
```
and to the `DockerAgent(...)` kwargs after `enable_graph_scheduler=_enable_graph_scheduler,` (~805):
```python
                enable_runtime_pin=_enable_runtime_pin,
```
`run_rat_benchmark.py` env→arm — add as the FIRST branch (~391, so v1gsp wins over v1gs since v1gsp also sets GRAPH_SCHEDULER=1):
```python
    if os.environ.get("DOCKERAGENT_ENABLE_RUNTIME_PIN") == "1":
        arm = "v1gsp"
    elif os.environ.get("DOCKERAGENT_ENABLE_GRAPH_SCHEDULER") == "1":
        arm = "v1gs"
```
`run_rat_benchmark.py` arm→env (~843-848) — add `"v1gsp"` to each tuple v1gs is in, add the new line:
```python
    os.environ["DOCKERAGENT_ENABLE_V1"] = "1" if args.arm in ("v1", "v1g", "v1gd", "v1gde", "v1gder", "v1gs", "v1gsp") else "0"
    os.environ["DOCKERAGENT_ENABLE_DEP_GRAPH"] = "1" if args.arm in ("v1gd", "v1gde", "v1gder", "v1gs", "v1gsp") else "0"
    os.environ["DOCKERAGENT_ENABLE_DEP_EMIT"] = "1" if args.arm in ("v1gde", "v1gder", "v1gs", "v1gsp") else "0"
    os.environ["DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK"] = "1" if args.arm in ("v1gder", "v1gs", "v1gsp") else "0"
    os.environ["DOCKERAGENT_ENABLE_GRAPH_SCHEDULER"] = "1" if args.arm in ("v1gs", "v1gsp") else "0"
    os.environ["DOCKERAGENT_ENABLE_RUNTIME_PIN"] = "1" if args.arm == "v1gsp" else "0"
```
(leave the `ENABLE_CONTRACT_GRAPH` line unchanged) and add `"v1gsp"` to the `--arm` `choices=[...]` list (grep the existing `choices=[...]` containing `"v1gs"`).

`run_repo2run_benchmark.py` — add a `"v1gsp"` preset after the close of the `v1gs` value dict (after line 3198, before the `}` that ends `_ARM_PRESETS`):
```python
    "v1gsp": {
        "enable_supervisor": False, "enable_fullstate_worker": False, "fullstate_worker_prompt": False,
        "enable_envstate": False, "enable_v1": True, "enable_contract_graph": False,
        "enable_dep_graph": True, "enable_dep_emit": True, "enable_graph_scheduler": True,
        "enable_runtime_feedback": True, "enable_runtime_pin": True,
        "enable_cleanroom": True,
        "max_steps": 12, "_label": "armV1gsp_runtime_pin",
    },
```
forward after the `enable_graph_scheduler` forward (~221):
```python
    if getattr(args, "enable_runtime_pin", False):
        command.append("--enable-runtime-pin")
```
and add `"v1gsp"` to the repo2run `--arm` `choices=[...]` (~3358).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_runtime_pin_flag.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_runtime_pin_flag.py agent.py multi_docker_eval_adapter.py run_rat_benchmark.py run_repo2run_benchmark.py
git commit -m "feat(depgraph): enable_runtime_pin flag + v1gsp arm plumbing"
```

### Task 1.2: The base-override seam

**Files:**
- Modify: `agent.py` — module-level helper near the top imports; the seam between line 461 and 463; a gated ordering guard after the Synthesizer init (line 511).
- Test: `tests/test_runtime_pin_seam.py`

**Interfaces:**
- Consumes: `resolve_runtime_base` (Phase 0), `self.enable_runtime_pin` (Task 1.1).
- Produces: module-level `_apply_runtime_pin(enable_runtime_pin, workplace, base_image) -> RuntimeBaseDecision | None`; the seam stores `self._runtime_pin_decision` (the decision or None) and `self._runtime_pin_original_base` (pre-pin base) — consumed by Tasks 3.1 and 3.2.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runtime_pin_seam.py
"""_apply_runtime_pin returns the RuntimeBaseDecision (or None when off/unusable).
Extracted from DockerAgent._run so it is unit-testable without an OpenAI client
or Docker."""
import src.envstate.runtime_base as rb
from agent import _apply_runtime_pin


def test_disabled_returns_none(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10,<3.13"\n')
    assert _apply_runtime_pin(False, str(tmp_path), "python:3.11-slim") is None


def test_enabled_pins_to_floor(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10,<3.13"\n')
    d = _apply_runtime_pin(True, str(tmp_path), "python:3.11-slim")
    assert d is not None
    assert d.base_image == "python:3.10-slim"
    assert d.minor == "3.10"


def test_enabled_undeclared_leaves_base(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    d = _apply_runtime_pin(True, str(tmp_path), "python:3.11-slim")
    assert d is not None
    assert d.base_image == "python:3.11-slim"   # unchanged
    assert d.minor == "3.11"


def test_enabled_missing_workplace_is_none():
    assert _apply_runtime_pin(True, "/nonexistent/repo", "python:3.11-slim") is None


def test_enabled_resolve_raises_returns_none(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10"\n')

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(rb, "resolve_runtime_base", _boom)
    assert _apply_runtime_pin(True, str(tmp_path), "python:3.11-slim") is None


def test_seam_assigns_self_base_image_and_stores_decision():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agent.py").read_text()
    # the seam must persist the pinned base for the scratch build at line ~1020 ...
    assert "self.base_image = base_image" in src
    # ... and store the decision so _build_run_summary can emit the metric.
    assert "self._runtime_pin_decision = _apply_runtime_pin(" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_runtime_pin_seam.py -q`
Expected: FAIL with `ImportError: cannot import name '_apply_runtime_pin' from 'agent'`.

- [ ] **Step 3: Implement** — module-level helper (after the imports in `agent.py`):

```python
def _apply_runtime_pin(enable_runtime_pin, workplace, base_image):
    """Return the RuntimeBaseDecision for this repo, or None when the pin is off,
    the base/workplace are unusable, or resolution raises. The decision carries the
    pinned base (.base_image), chosen minor (.minor), and provenance (.reason). The
    run must proceed exactly as if off on any failure — never raises."""
    import os
    if not enable_runtime_pin or not base_image or not os.path.isdir(workplace or ""):
        return None
    try:
        from src.envstate.runtime_base import resolve_runtime_base
        return resolve_runtime_base(workplace, base_image)
    except Exception as exc:  # noqa: BLE001 — pin must never break a run
        print(f"[runtime-pin] unavailable ({exc}); keeping {base_image}")
        return None
```

Seam — insert before line 463 (`# 5. Setup Sandbox ...`):
```python
        # 4b. Runtime-tier pin (gated): rewrite the base image's python to the
        # project's requires-python BEFORE the sandbox is built. self.base_image is
        # read back at the scratch-graph build (line ~1020); the decision + the pre-pin
        # base are stored for the run-summary metric (Task 3.2).
        self._runtime_pin_decision = _apply_runtime_pin(
            getattr(self, "enable_runtime_pin", False), self.workplace, base_image
        )
        if self._runtime_pin_decision is not None:
            self._runtime_pin_original_base = base_image
            if self._runtime_pin_decision.base_image != base_image:
                print(f"[runtime-pin] base {base_image} -> "
                      f"{self._runtime_pin_decision.base_image} "
                      f"({self._runtime_pin_decision.reason})")
            base_image = self._runtime_pin_decision.base_image
        self.base_image = base_image
```

Ordering guard — add immediately after `self.synthesizer = Synthesizer(base_image=base_image)` (line 511):
```python
        # Guard the seam's ordering assumption: the Synthesizer (just constructed)
        # must inherit the pinned base, or the emitted Dockerfile diverges from the
        # live container. Warn loudly if a future refactor reorders this.
        if getattr(self, "_runtime_pin_decision", None) is not None and \
                getattr(self.synthesizer, "base_image", None) != base_image:
            print("[runtime-pin] WARNING: synthesizer.base_image != pinned base; "
                  "emitted Dockerfile may diverge from the live container")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_runtime_pin_seam.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the full suite — prove no regression / byte-identical off-state**

Run: `python3 -m pytest -q`
Expected: PASS. Off-state note: when the pin is off, `_apply_runtime_pin` returns `None`, `base_image` is the synthesizer's value unchanged, and `self.base_image = base_image` sets it to exactly that value (previously unset → line 1020 fell through to `self.synthesizer.base_image`, which is the same image). `self._runtime_pin_decision is None` ⇒ no `runtime_pin` summary key. If any existing test asserts `self.base_image` is unset, gate the assignment inside `if self._runtime_pin_decision is not None:` and leave the off path untouched.

- [ ] **Step 6: Commit**

```bash
git add tests/test_runtime_pin_seam.py agent.py
git commit -m "feat(depgraph): pin live-container base python at the pre-sandbox seam"
```

---

## Phase 2 — Runtime node + host certify (propose → verify)

The pin is a *proposal*. The graph carries a **Runtime obligation** whose state is flipped only by a check; a wrong/un-pinnable base surfaces as `MISSING` instead of passing silently. (Reported signal comes from the LIVE graph — Phase 3.2 — not the scratch certify here.)

### Task 2.1: `runtime_id` + Runtime node builder

**Files:**
- Modify: `src/python_deps/depgraph/ids.py` (after `service_id`, line ~51)
- Modify: `src/python_deps/depgraph/build.py` (after `platform = ...`, line ~248, where `target_python` is non-None)
- Test: `tests/depgraph/test_runtime_node.py`

**Interfaces:**
- Consumes: `build_dep_graph(... target_python: str | None)` (existing); `NodeType.RUNTIME`, `Layer.RUNTIME`, `State.UNKNOWN`, `DiscoveredBy.STATIC_SCAN` (all in `schema.py`, all imported at the top of `build.py:53-62`).
- Produces: `ids.runtime_id(minor: str) -> str` = `"runtime:python-<minor>"`; a single `Node(type=RUNTIME, layer=Layer.RUNTIME, version=<minor>, check_command=<version assertion>)` in graphs where `target_python` resolves.

- [ ] **Step 1: Write the failing test** (uses the project `FakeExecutor` from `tests/depgraph/conftest.py` — `rc 0` default — not an ad-hoc stub; `target_python` is passed explicitly so the empty fake closure is fine)

```python
# tests/depgraph/test_runtime_node.py
from conftest import FakeExecutor
from python_deps.depgraph.build import build_dep_graph
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.ids import runtime_id
from python_deps.depgraph.schema import NodeType, Layer


def test_runtime_id_is_stable():
    assert runtime_id("3.10") == "runtime:python-3.10"


def test_build_adds_a_runtime_node(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nrequires-python = ">=3.10"\n')
    ex = FakeExecutor(default=CommandResult(command="", returncode=0, stdout="", stderr=""))
    g = build_dep_graph(str(tmp_path), ex, host_executor=ex, target_python="3.10")
    rt = [n for n in g.nodes if n.type is NodeType.RUNTIME]
    assert len(rt) == 1
    assert rt[0].id == "runtime:python-3.10"
    assert rt[0].layer is Layer.RUNTIME
    assert rt[0].version == "3.10"
    assert "sys.version_info[:2]==(3,10)" in rt[0].check_command.replace(" ", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_runtime_node.py -q`
Expected: FAIL — `ImportError: cannot import name 'runtime_id'`.

- [ ] **Step 3: Implement**

`ids.py`:
```python
def runtime_id(minor: str) -> str:
    return f"runtime:python-{minor}"
```
`build.py` — after the `platform = target_platform or _detect_target_platform(...)` line (~248):
```python
    # Runtime-tier obligation: the container must run the targeted python minor.
    # Certified later by a host check (rc 0 iff sys.version_info matches); discovery
    # here never implies SATISFIED.
    from python_deps.depgraph.ids import runtime_id as _runtime_id
    _maj, _min = target_python.split(".")[:2]
    _rt_check = f'python3 -c "import sys; sys.exit(0 if sys.version_info[:2]==({_maj},{_min}) else 1)"'
    graph = graph.with_node(
        Node(
            id=_runtime_id(target_python),
            type=NodeType.RUNTIME,
            name=f"python {target_python}",
            layer=Layer.RUNTIME,
            discovered_by=DiscoveredBy.STATIC_SCAN,
            state=State.UNKNOWN,
            version=target_python,
            check_command=_rt_check,
            resolved_python=target_python,
        )
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_runtime_node.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the depgraph suite** (confirmed: `test_build.py` has NO exact node-count assertion — it checks by node id / type list — so the +1 Runtime node breaks nothing; still run to be sure)

Run: `python3 -m pytest tests/depgraph -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/depgraph/test_runtime_node.py src/python_deps/depgraph/ids.py src/python_deps/depgraph/build.py
git commit -m "feat(depgraph): add Runtime-tier node from target_python"
```

### Task 2.2: Certify the Runtime layer

**Files:**
- Modify: `src/python_deps/depgraph/certify.py:23-33` (add `Layer.RUNTIME` to `_LAYER_ORDER`, update the comment)
- Test: `tests/depgraph/test_certify_runtime.py`

**Interfaces:**
- Consumes: the Runtime node (Task 2.1); `certify_all` (existing).
- Produces: a Runtime node whose `state` is `SATISFIED` when the container python matches (rc 0), `MISSING` otherwise.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_certify_runtime.py
from python_deps.depgraph.certify import certify_all
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.ids import runtime_id
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)


def _runtime_graph(minor):
    check = f'python3 -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,{minor.split(".")[1]}) else 1)"'
    n = Node(id=runtime_id(minor), type=NodeType.RUNTIME, name=f"python {minor}",
             layer=Layer.RUNTIME, discovered_by=DiscoveredBy.STATIC_SCAN,
             state=State.UNKNOWN, version=minor, check_command=check)
    return DepGraph(nodes=(n,), edges=())


class _Ex:
    def __init__(self, rc): self.rc = rc
    def run(self, command, *, timeout=300):
        return CommandResult(command=command, returncode=self.rc, stdout="", stderr="x")


def test_runtime_certifies_satisfied_on_rc0():
    g = certify_all(_runtime_graph("3.10"), _Ex(0), cycle=1)
    assert g.get(runtime_id("3.10")).state is State.SATISFIED


def test_runtime_certifies_missing_on_rc1():
    g = certify_all(_runtime_graph("3.10"), _Ex(1), cycle=1)
    assert g.get(runtime_id("3.10")).state is State.MISSING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_certify_runtime.py -q`
Expected: FAIL with `AssertionError` (node stays `UNKNOWN` — `Layer.RUNTIME` isn't in `_LAYER_ORDER`, so `certify_all` never runs its check). NOTE: if you instead see `AttributeError: DiscoveredBy.STATIC`, you mistyped the member — it is `STATIC_SCAN`.

- [ ] **Step 3: Implement** — `certify.py`:

```python
# Execution layer priority (design section 6). Runtime joins the walk first: the
# interpreter minor is the platform floor every later layer assumes.
_LAYER_ORDER: tuple[Layer, ...] = (
    Layer.RUNTIME,
    Layer.INTERPRETER,
    Layer.SYSTEM,
    Layer.TOOLCHAIN,
    Layer.PIP,
    Layer.NAMING,
    Layer.CONFIG,
    Layer.TESTS,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_certify_runtime.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the depgraph + done-gate suite**

Run: `python3 -m pytest tests/depgraph tests/test_progress_done_consistency.py -q`
Expected: PASS. A `MISSING` Runtime node *should* count toward the frontier — that is the intended behavior. If a done-gate test breaks because it didn't expect a Runtime node, that's a real interaction to reconcile (the Runtime obligation is legitimately part of "is the env ready").

- [ ] **Step 6: Commit**

```bash
git add tests/depgraph/test_certify_runtime.py src/python_deps/depgraph/certify.py
git commit -m "feat(depgraph): certify the Runtime layer (host-verified python minor)"
```

---

## Phase 3 — Assert the *required* minor + emit the A/B metric

Forward the **required** minor into the scratch build so the Runtime node asserts what the project NEEDS (not the tautological detected value), then emit a `runtime_pin` record sourced from the **LIVE** final graph — instrumented so a null can't hide a bug.

### Task 3.1: Forward `target_python` through the advisory

**Files:**
- Modify: `src/python_deps/depgraph/advise.py:296-322`
- Modify: `agent.py:1079-1081` (use the stored decision — no re-derivation)
- Test: `tests/depgraph/test_advise_target_python.py`

**Interfaces:**
- Consumes: `build_dep_graph(... target_python=...)`; `self._runtime_pin_decision` (Task 1.2).
- Produces: `build_advisory_for_repo(repo_path, base_image, *, host_executor=None, target_python: str | None = None)` that forwards `target_python` to `build_dep_graph`.

- [ ] **Step 1: Write the failing test** — TWO tests: the signature AND that it actually forwards (a signature that silently ignores `target_python` would reintroduce the tautology Phase 3 exists to remove)

```python
# tests/depgraph/test_advise_target_python.py
import inspect
from python_deps.depgraph.advise import build_advisory_for_repo


def test_advisory_accepts_target_python():
    sig = inspect.signature(build_advisory_for_repo)
    assert "target_python" in sig.parameters
    assert sig.parameters["target_python"].default is None


def test_advisory_forwards_target_python_to_build():
    # guards against accepting the param but ignoring it
    src = inspect.getsource(build_advisory_for_repo)
    assert "target_python=target_python" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/depgraph/test_advise_target_python.py -q`
Expected: FAIL — `target_python` not in signature.

- [ ] **Step 3: Implement** — `advise.py` signature + forward:

```python
def build_advisory_for_repo(
    repo_path: str,
    base_image: str,
    *,
    host_executor: Executor | None = None,
    target_python: str | None = None,
) -> tuple[str, DepGraph | None]:
```
```python
            graph = build_dep_graph(
                repo_path, scratch, host_executor=host, target_python=target_python
            )
```
`agent.py:1079` — use the stored decision (no second `resolve_runtime_base` call):
```python
                _req_minor = (
                    self._runtime_pin_decision.minor
                    if getattr(self, "_runtime_pin_decision", None) is not None
                    else None
                )
                _dep_advisory, _dep_graph = build_advisory_for_repo(
                    self.workplace, _base_image, target_python=_req_minor
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/depgraph/test_advise_target_python.py tests/depgraph/test_advise.py -q`
Expected: PASS (existing advise tests still green; `target_python` defaults to `None`, preserving detection when the pin is off).

- [ ] **Step 5: Commit**

```bash
git add tests/depgraph/test_advise_target_python.py src/python_deps/depgraph/advise.py agent.py
git commit -m "feat(depgraph): forward required python minor into the scratch graph build"
```

### Task 3.2: Emit the `runtime_pin` record (live certify + base_changed)

**Files:**
- Modify: `agent.py` — module-level `_runtime_pin_summary` helper; emit it inside `_build_run_summary` (line 3055)
- Test: `tests/test_runtime_pin_summary.py`

**Interfaces:**
- Consumes: `self._final_dep_graph` (the LIVE loop's final graph, set at line 1277), `self._runtime_pin_decision` + `self._runtime_pin_original_base` (Task 1.2), `runtime_id` (Task 2.1).
- Produces: a `runtime_pin` block: `{required, reason, original_base, pinned_base, base_changed, certified}`. `certified` comes from the LIVE graph (`"satisfied"|"missing"|"unknown"`), `None` if never certified there.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runtime_pin_summary.py
from agent import _runtime_pin_summary
from src.envstate.runtime_base import RuntimeBaseDecision
from python_deps.depgraph.ids import runtime_id
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)


def _live_graph(state):
    n = Node(id=runtime_id("3.10"), type=NodeType.RUNTIME, name="python 3.10",
             layer=Layer.RUNTIME, discovered_by=DiscoveredBy.STATIC_SCAN, state=state,
             version="3.10", check_command="x")
    return DepGraph(nodes=(n,), edges=())


def _decision(base="python:3.10-slim"):
    return RuntimeBaseDecision(minor="3.10", base_image=base, reason="floor",
                              requires_python=">=3.10")


def test_none_decision_returns_none():
    assert _runtime_pin_summary(_live_graph(State.SATISFIED), None, None) is None


def test_reports_certified_from_live_graph_and_base_changed():
    s = _runtime_pin_summary(_live_graph(State.SATISFIED), _decision(), "python:3.11-slim")
    assert s == {
        "required": "3.10", "reason": "floor",
        "original_base": "python:3.11-slim", "pinned_base": "python:3.10-slim",
        "base_changed": True, "certified": "satisfied",
    }


def test_reports_missing_and_base_unchanged():
    s = _runtime_pin_summary(_live_graph(State.MISSING), _decision(base="python:3.11-slim"),
                             "python:3.11-slim")
    assert s["certified"] == "missing"
    assert s["base_changed"] is False


def test_no_live_graph_certified_none():
    s = _runtime_pin_summary(None, _decision(), "python:3.11-slim")
    assert s["certified"] is None
    assert s["base_changed"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_runtime_pin_summary.py -q`
Expected: FAIL — `_runtime_pin_summary` undefined.

- [ ] **Step 3: Implement** — module-level helper in `agent.py`:

```python
def _runtime_pin_summary(final_dep_graph, decision, original_base):
    """A/B record for the run summary. `certified` is read from the LIVE final
    graph (certify_refresh ran it in the agent container) — NOT the scratch graph,
    whose certify would be tautological. None-safe; returns None when the pin was
    off (no decision)."""
    if decision is None:
        return None
    certified = None
    if final_dep_graph is not None:
        from python_deps.depgraph.ids import runtime_id
        node = final_dep_graph.get(runtime_id(decision.minor))
        if node is not None:
            certified = node.state.value  # "satisfied" | "missing" | "unknown"
    return {
        "required": decision.minor,
        "reason": decision.reason,
        "original_base": original_base,
        "pinned_base": decision.base_image,
        "base_changed": decision.base_image != original_base,
        "certified": certified,
    }
```
In `_build_run_summary` (line 3055), after the dict is built, add (only when the pin ran, so off-state JSON is unchanged):
```python
        if getattr(self, "_runtime_pin_decision", None) is not None:
            summary["runtime_pin"] = _runtime_pin_summary(
                getattr(self, "_final_dep_graph", None),
                self._runtime_pin_decision,
                getattr(self, "_runtime_pin_original_base", None),
            )
```
(Use the actual variable name `_build_run_summary` assigns the dict to — read the function and substitute it for `summary` if it differs.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_runtime_pin_summary.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Full suite**

Run: `python3 -m pytest -q`
Expected: PASS (all prior + new).

- [ ] **Step 6: Commit**

```bash
git add tests/test_runtime_pin_summary.py agent.py
git commit -m "feat(depgraph): emit runtime_pin A/B record (live-certify + base_changed)"
```

---

## Phase 4 — e2e validation (manual; not a unit task)

Unit tests can't prove the live container's `FROM` or that the pin changed an *outcome*. The signal is `test pass rate` + failure attribution, not the `certified` flag alone.

- [ ] **Gate on Phase 0.5.** Re-confirm `scripts/screen_runtime_pin.py <repos>` shows ≥5 `base_would_change`. If not, do not e2e — the set can't produce signal.
- [ ] **Select a discriminating subset** from the screen: rows with `base_would_change=True`, preferring `would_pin_to` ∈ {3.9, 3.10} (furthest from the default), ideally with old pinned closures. Random repos where `python:3.11` always works measure nothing.
- [ ] For each selected repo, run `--arm v1gs` and `--arm v1gsp`; for each run capture: (a) the `runtime_pin` block from the run summary (`base_changed`, `required`, `reason`, live `certified`); (b) the **actual** `FROM` line of the emitted Dockerfile (`<workplace>/Dockerfile`) to confirm the live build used the pinned tag; (c) the honest pass rate.
- [ ] **Outcome attribution:** for any repo where `v1gs` fails and `v1gsp` passes, grep the `v1gs` failure log for version-incompatibility signals (`requires python`, `incompatible`, ABI, version-specific wheel error). Only then credit the pin — otherwise it's run-to-run variance.
- [ ] **Drop `ensure_python_shim` as a signal** — it tracks the `python`→`python3` symlink (a base-image property), orthogonal to the version pin.
- [ ] **Expected signal:** under `v1gsp`, a declared-floor repo builds on the pinned `python:3.X` (`base_changed: true`, Dockerfile `FROM` matches), the Runtime node certifies `satisfied` against the LIVE container, and a python-version failure under `v1gs` is fixed.
- [ ] **Null/anomaly disambiguation** (the whole point of the instrumentation):
  - `base_changed: false` everywhere → structurally inert for this set (expected if the screen was weak; not a bug).
  - `base_changed: true` but `certified: missing` → pin fired but the live container lacks the minor (un-pinnable base, or an infeasible/aggressive pin) → real bug or a policy problem to fix, NOT a null.
  - `certified: null` with `reason: "...fallback_exception..."` → `resolve_runtime_base` is throwing → bug. (`reason` distinguishes this from a legitimately undeclared repo.)

---

## Self-Review

- **Spec coverage:** version derivation (Phase 0) ✓; pre-screen go/no-go gate (Phase 0.5) ✓; live container actually pinned at the line-462 seam (Phase 1) ✓; propose→verify via host certify on the LIVE graph (Phase 2 + 3.2) ✓; required-vs-detected made non-tautological + forwarding TESTED (Phase 3.1) ✓; null-can't-hide-a-bug instrumentation `base_changed`/`original_base`/`reason`/live `certified` (Phase 3.2) ✓; discriminating subset + outcome attribution (Phase 4) ✓; default-off/byte-identical (Global Constraints, gated everywhere) ✓; OS/distro + slim-vs-full explicitly out of scope.
- **Review findings folded in:** `DiscoveredBy.STATIC_SCAN` (verified in `schema.py:68`) everywhere; summary reads `_final_dep_graph` not the scratch graph; seam stores the decision on `self` (single derivation); `screen_runtime_pin` gate; `base_changed`/`reason`/`original_base`; forwarding test + `self.base_image` test + exception-path test + project `FakeExecutor`; ordering guard after the Synthesizer init; corrected anchors (ctor param 239, repo2run preset after 3198, `_build_run_summary` 3055).
- **Type consistency:** `RuntimeBaseDecision(.minor/.base_image/.reason/.requires_python)` used in Tasks 1.2, 3.1, 3.2; `runtime_id(minor)`→`"runtime:python-<minor>"` in Tasks 2.1/2.2/3.2; `build_advisory_for_repo(..., target_python=None)` defined in 3.1 matches the `agent.py` caller; `_apply_runtime_pin(...) -> RuntimeBaseDecision | None` produced in 1.2 and consumed in 1.2 seam + (via `self._runtime_pin_decision`) in 3.1/3.2.
- **One spot to confirm against source at execution time:** the local variable name `_build_run_summary` assigns its dict to (assumed `summary`) — read the function and substitute. Flagged inline in Task 3.2; not a placeholder.
```
