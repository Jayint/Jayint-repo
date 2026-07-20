# Move Dockerfile Synthesis to the Runner Side (remove v1 in-agent synthesis)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The v1 agent stops synthesizing/verifying the Dockerfile; ALL Dockerfile synthesis + repair happens in the benchmark runners (repo2run + RATBench), which resynthesize from the workplace.

**Architecture:** The v1 agent (`agent.py:_run_v1`) keeps building the env and emitting an honest in-sandbox success signal + `agent_run_summary.json` + setup logs. The runners reconstruct the Dockerfile via `resynthesize_dockerfile_from_existing_workplace` (workplace_replay.py). repo2run already does resynthesize-if-missing; RATBench's adapter does NOT, so we add it there. The success signal (`configuration_success`) decouples from Dockerfile synthesis.

**Tech Stack:** Python, pytest, Docker SDK. `Synthesizer` (src/synthesizer.py), `resynthesize_dockerfile_from_existing_workplace` (src/workplace_replay.py).

---

## Why this is safe (verified)

- `resynthesize_dockerfile_from_existing_workplace(workplace, …)` (`workplace_replay.py:99`) needs only `workplace/agent_run_summary.json` + `workplace/logs/setup_logs/` — both still written by the v1 run. It writes `workplace/Dockerfile`. It does NOT require a pre-existing Dockerfile.
- repo2run runner already calls it when `workplace/Dockerfile` is absent (`run_repo2run_benchmark.py:3501`). **No change needed.**
- RATBench: adapter Step 2 (`multi_docker_eval_adapter.py:808-834`) reads `workplace/Dockerfile` and errors if missing; `_repair_and_rescore` (`repo2run_repair_port.py:3213`) reads `eval_build/Dockerfile` and no-ops if missing. **Both need the seed.** Fixing the adapter (resynthesize-if-missing) seeds the whole RATBench chain in every `repair_mode`.

## File Structure

- `agent.py` — `_run_v1` finalize: drop the `_finalize_supervisor_artifacts` synthesis calls (2 sites). Method itself stays (arm0's `run()` still uses it).
- `multi_docker_eval_adapter.py` — Step 2: resynthesize `workplace/Dockerfile` when absent, before extracting instructions.
- `repo2run_repair_port.py` — (defensive) resynthesize-if-missing guard before reading `eval_build/Dockerfile`.
- `tests/test_agent_v1_no_synthesis.py` — new: v1 success without in-agent Dockerfile.
- `tests/test_adapter_resynthesize_fallback.py` — new: adapter Step 2 resynthesizes when missing.

---

### Task 1: v1 agent stops synthesizing the Dockerfile

**Files:**
- Modify: `agent.py` (the `_run_v1` finalize block, ~`:1188-1216`)
- Test: `tests/test_agent_v1_no_synthesis.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_v1_no_synthesis.py
import os
from unittest.mock import patch

def test_v1_success_does_not_call_finalize_supervisor_artifacts(tmp_path):
    """v1 success must come from the verified-test gate alone; no in-agent
    Dockerfile synthesis is invoked."""
    from agent import DockerAgent
    a = DockerAgent.__new__(DockerAgent)
    a.workplace = str(tmp_path)
    a.verified_test_commands = ["python -m pytest -q"]
    a.verification_bundle = {"test_commands": ["python -m pytest -q"]}
    # Minimal stubs for the finalize branch:
    a._auto_finalize_from_verified_tests = lambda source: True
    called = {"finalize": 0}
    a._finalize_supervisor_artifacts = lambda cs: called.__setitem__("finalize", called["finalize"] + 1) or cs
    a._maybe_generate_long_term_memories = lambda cs: None
    # Exercise just the success-finalization branch logic (extracted helper, Step 3).
    cs = a._v1_finalize_success(done_flag=True)
    assert cs is True
    assert called["finalize"] == 0          # synthesis NOT invoked
    assert not os.path.exists(os.path.join(str(tmp_path), "Dockerfile"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_agent_v1_no_synthesis.py -q`
Expected: FAIL (`_v1_finalize_success` not defined).

- [ ] **Step 3: Extract a tiny finalize helper + drop the synthesis call**

In `agent.py`, replace the inline success-finalization (`:1188-1196`) with a call to a new helper, and the helper omits synthesis:

```python
# at the call site (~:1188):
configuration_success = self._v1_finalize_success(final_map.done_flag)
```

```python
def _v1_finalize_success(self, done_flag):
    """v1 success = the verified-test gate ONLY. The Dockerfile is synthesized
    by the benchmark runners (repo2run/RATBench) from the workplace, not here."""
    source = "v1_done_flag" if done_flag else "v1_test_run_finalize"
    configuration_success = (
        self._auto_finalize_from_verified_tests(source)
        or bool(self.verification_bundle)
    )
    if configuration_success:
        # Keep memory generation; drop in-agent Dockerfile synthesis/cleanroom.
        self._maybe_generate_long_term_memories(configuration_success)
    return configuration_success
```

Also in the transient-LLM-error branch (`:1201-1216`), replace
`configuration_success = self._finalize_supervisor_artifacts(configuration_success)`
(and its surrounding try/except) with `self._maybe_generate_long_term_memories(configuration_success)`.

Leave `_finalize_supervisor_artifacts` defined (arm0's `run()` still calls it).

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_agent_v1_no_synthesis.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_agent_v1_no_synthesis.py
git commit -m "refactor(v1): stop synthesizing the Dockerfile in-agent; runners resynthesize from the workplace"
```

---

### Task 2: RATBench adapter resynthesizes the seed Dockerfile when missing

**Files:**
- Modify: `multi_docker_eval_adapter.py` (Step 2, `:806-834`)
- Test: `tests/test_adapter_resynthesize_fallback.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adapter_resynthesize_fallback.py
from pathlib import Path
from unittest.mock import patch

def test_adapter_resynthesizes_when_workplace_dockerfile_missing(tmp_path):
    """When the agent did not write workplace/Dockerfile, Step 2 must resynthesize
    it from the workplace before extracting instructions."""
    import multi_docker_eval_adapter as A
    wp = tmp_path
    (wp / "agent_run_summary.json").write_text("{}")
    (wp / "logs" / "setup_logs").mkdir(parents=True)

    def fake_resynth(workplace, **kw):
        Path(workplace, "Dockerfile").write_text("FROM python:3.12-slim\nRUN pip install -e .\n")
        return {"dockerfile_path": str(Path(workplace, "Dockerfile"))}

    with patch.object(A, "resynthesize_dockerfile_from_existing_workplace", fake_resynth, create=True):
        text = A.ensure_workplace_dockerfile(str(wp), model="m")   # new helper (Step 3)
    assert "FROM python:3.12-slim" in text
    assert Path(wp, "Dockerfile").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_adapter_resynthesize_fallback.py -q`
Expected: FAIL (`ensure_workplace_dockerfile` not defined).

- [ ] **Step 3: Add the resynthesize-if-missing helper + call it in Step 2**

Add the import near the top of `multi_docker_eval_adapter.py`:
```python
from src.workplace_replay import resynthesize_dockerfile_from_existing_workplace
```

Add a module-level helper:
```python
def ensure_workplace_dockerfile(workplace: str, model: str) -> str:
    """Return workplace/Dockerfile text, resynthesizing from the workplace if the
    v1 agent did not write one (v1 no longer synthesizes in-agent). Returns "" on failure."""
    from pathlib import Path
    p = Path(workplace) / "Dockerfile"
    if not p.exists():
        try:
            resynthesize_dockerfile_from_existing_workplace(workplace, model=model)
        except Exception as exc:
            print(f"[Adapter] resynthesis failed: {exc}")
            return ""
    return p.read_text(encoding="utf-8") if p.exists() else ""
```

In Step 2 (`:807-834`), replace the bare `dockerfile_path.exists()` read with:
```python
original_dockerfile = ensure_workplace_dockerfile(str(workplace), model=self.model)
if original_dockerfile:
    base_image_line, agent_run_instructions = self._extract_agent_dockerfile_instructions(
        original_dockerfile
    )
    # ... (unchanged extract/build path) ...
else:
    print("✗ Dockerfile not found (and resynthesis failed)")
    result["logs"]["error"] = "Dockerfile generation failed"
    result["logs"]["skip_evaluation"] = True
```
(Use whatever model attr the adapter holds; fall back to the synthesizer default if absent.)

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_adapter_resynthesize_fallback.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add multi_docker_eval_adapter.py tests/test_adapter_resynthesize_fallback.py
git commit -m "feat(ratbench-adapter): resynthesize workplace/Dockerfile when the v1 agent did not write one"
```

---

### Task 3 (defensive): repair-port resynthesize-if-missing guard

**Files:**
- Modify: `repo2run_repair_port.py` (just before `current_eval_dockerfile_text = Path(dockerfile_path).read_text(...)`, `:3212`)

- [ ] **Step 1: Add the guard** (mirrors repo2run runner `:3501`)

```python
# ── GLUE: seed the eval Dockerfile if absent (v1 no longer synthesizes in-agent) ──
if not Path(dockerfile_path).exists():
    try:
        from src.workplace_replay import resynthesize_dockerfile_from_existing_workplace
        resynthesize_dockerfile_from_existing_workplace(os.path.dirname(agent_summary_path))
        # (then re-derive eval_build/Dockerfile via the same prepare path the runner uses)
    except Exception as exc:
        print(f"[repair] resynthesis-if-missing failed: {exc}")
```

- [ ] **Step 2: Run the existing repair-port tests**

Run: `python3 -m pytest tests/test_repo2run_repair_port.py -q`
Expected: PASS (no regressions). This guard is a no-op when the adapter already seeded the Dockerfile.

- [ ] **Step 3: Commit**

```bash
git add repo2run_repair_port.py
git commit -m "fix(repair-port): defensively resynthesize the eval Dockerfile when absent"
```

---

### Task 4: Regression sweep

- [ ] Run `python3 -m pytest tests/test_repo2run_benchmark.py tests/test_run_rat_benchmark.py tests/test_repo2run_repair_port.py tests/test_build_agent.py -q` — expect no new failures (the 3 pre-existing env failures noted in `docs/dockerfile-extraction-and-contract-graph-roadmap.md` are unrelated).
- [ ] Optional live smoke: one repo each through repo2run runner and RATBench (`--repair-mode selfverify` and `--repair-mode runner`) to confirm both seed a Dockerfile from the workplace with the v1 agent no longer producing one.

## Risks / decisions

- **Memory generation in v1:** `_finalize_supervisor_artifacts` also called `_maybe_generate_long_term_memories`. The plan preserves it (Task 1, Step 3). If memory gen depends on `self.build_recipe` (now unset in v1), make it defensive or drop it — decide during Task 1.
- **`configuration_success` semantics shift:** v1 success now = "env built + tests passed," no longer gated on synthesis succeeding. This is the intended decoupling.
- **RATBench `repair_mode` is irrelevant to the seed:** the adapter fix (Task 2) seeds the Dockerfile for ALL modes; `repair_mode` only controls whether the *repair* runs.
- **Reproduction verification** now lives entirely runner-side (the roadmap's clean-rebuild gate is a runner concern, not the v1 agent's).
