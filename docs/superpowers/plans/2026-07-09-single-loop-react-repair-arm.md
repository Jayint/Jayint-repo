# Single-Loop (ReAct) Script-Repair Arm — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `--arm react` — a single flat ReAct loop that patches a build script and re-runs it fresh each step, host-owned done gate at ≥80% tests, both observation-compression tiers — as a self-contained arm, then retire arm C.

**Architecture:** One loop over `max_steps`: reset → run whole build script → certify (install-tier only) → if green + tests ≥80% pass, DONE; else the LLM planner reads the failure + compressed history and emits ONE move — a read-only explore command, or a full replacement build script. Script is truth; graph is a certified-observability side-channel. See `docs/superpowers/specs/2026-07-09-single-loop-script-repair-agent-design.md`.

**Tech Stack:** Python; existing `Sandbox` (Docker), `render_build_script`, `certify_all`, `complete_with_retry`, `is_read_only`.

## Global Constraints

- **New package `src/react_repair/`** owns strategy only; imports shared platform, never arm C. Import boundary (spec §14): allowed — `Sandbox`, `render_build_script`, `certify_all`/`EXECUTION_LAYER_ORDER`, `CommandResult`, `DepGraph`, `complete_with_retry`, `is_read_only`. Forbidden — anything from `repair_arm*.py`, `repair_fix.py`, `repair_session.py`, `session_agent.py`, `repair_arm_entry.py`, `repair_log.py`.
- **Dependency injection**: the loop takes injected `reset`/`run_script`/`certify`/`exec_readonly`/`run_tests`/`planner`/`history`/`log`. Same loop runs in prod (docker adapters) and the offline eval (FakeSandbox). No Docker or LLM in unit tests.
- **Host owns done**: DONE iff script `rc==0` AND `test_verdict.ok` (≥80% of executed tests pass, `executed≥1`). No agent success-claim.
- **No live mutation**: explore is read-only (`is_read_only` gate); only a whole-script re-run mutates the build. No snapshots/rollback.
- **Avoid double pytest**: certify runs install-tier layers only (EXECUTION_LAYER_ORDER minus `Layer.TESTS`); the single `run_tests` call is the authoritative suite run.
- **Additive**: does not modify `run_v3`/`run_v1`/`orchestrator.py`. Arm C is deleted in Task 10 (last).
- Python: PEP 8, type annotations, `from __future__ import annotations`, `@dataclass(frozen=True)` for value types. Files < 400 lines.

## Interfaces (shared across tasks — keep these signatures exact)

```python
# gate.py
@dataclass(frozen=True)
class TestOutcome: ok: bool; passed: int; executed: int; output: str = ""
def test_verdict(output: str, *, threshold: float = 0.8) -> TestOutcome

# actions.py
@dataclass(frozen=True)
class Action: kind: str; command: str | None = None; new_script: str | None = None  # kind: "explore"|"patch"|"invalid"
def parse_action(text: str) -> Action
def extract_thought(text: str) -> str

# log.py
class ReactLog:  # .d(tag, msg); .events: list[(tag,msg)]; .count(tag)

# history.py
@dataclass
class Step: step_id: int; thought: str; action_summary: str; observation_raw: str; observation_prompt: str
def safety_truncate(text: str, *, max_chars: int, keep_tail: bool = True) -> tuple[str, bool]
class History:  # (safety_max_chars, compress_delay, compress_threshold_chars, compressor, log); .record(...)->Step; .render()->str
# compressor: Callable[[Step, list[Step]], str] | None

# planner.py
class ReactPlanner:  # (client, model, graph_context=None, log=None); .plan(history, script, observation, graph)->(thought, Action, usage)
# graph_context: Callable[[DepGraph], str] | None   (None = baseline; populated = graph-guided)

# loop.py
@dataclass(frozen=True)
class RunResult: ok: bool; failing_command: str | None = None; output: str = ""
def run_react(graph, *, reset, run_script, certify, exec_readonly, run_tests, planner, history, log,
              max_steps: int = 30) -> tuple[str, str, "DepGraph"]   # (outcome, script, graph)

# entry.py
def docker_adapters(sandbox) -> tuple[Callable, Callable, Callable, Callable, Callable]  # reset, run_script, certify, exec_readonly, run_tests
def run_react_arm(graph, *, sandbox, client, model, repo_path=None, graph_context=False, log=None, max_steps=30)
```

---

### Task 1: `gate.py` — the 80% test verdict

**Files:**
- Create: `src/react_repair/__init__.py` (empty)
- Create: `src/react_repair/gate.py`
- Test: `tests/react_repair/test_gate.py`

**Interfaces — Produces:** `TestOutcome`, `test_verdict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/react_repair/test_gate.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.gate import test_verdict


def test_all_pass_is_ok():
    r = test_verdict("5 passed in 0.3s")
    assert r.ok and r.passed == 5 and r.executed == 5

def test_ninety_percent_passes_threshold():
    assert test_verdict("9 passed, 1 failed in 1s").ok          # 0.9 >= 0.8

def test_sixty_percent_fails_threshold():
    assert not test_verdict("3 passed, 2 failed in 0.5s").ok     # 0.6 < 0.8

def test_collection_error_counts_against():
    r = test_verdict("8 passed, 2 errors in 1s")                 # 8/10 = 0.8 -> ok
    assert r.ok and r.executed == 10

def test_hollow_zero_collected_is_not_ok():
    assert not test_verdict("no tests ran in 0.01s").ok

def test_all_skipped_is_not_ok():
    assert not test_verdict("3 skipped in 0.1s").ok              # executed 0

def test_ansi_stripped():
    assert test_verdict("\x1b[32m5 passed\x1b[0m in 0.1s").ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/react_repair/test_gate.py -q`
Expected: FAIL (`ModuleNotFoundError: src.react_repair.gate`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/react_repair/gate.py
"""Gate 2 (testability) verdict for the react arm (spec §5). Count-based, ≥80% of
executed tests pass. `executed >= 1` is the whole anti-hollow guard: zero-collected
and all-skipped runs return rc 0 but have no real passes."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class TestOutcome:
    ok: bool
    passed: int
    executed: int
    output: str = ""


def _count(text: str, word: str) -> int:
    m = re.search(rf"(\d+)\s+{word}\b", text)
    return int(m.group(1)) if m else 0


def test_verdict(output: str, *, threshold: float = 0.8) -> TestOutcome:
    text = _ANSI.sub("", output or "")
    passed = _count(text, "passed")
    failed = _count(text, "failed")
    errors = _count(text, "errors?")            # "1 error" / "2 errors"
    executed = passed + failed + errors          # skipped excluded from the denominator
    ok = executed >= 1 and passed / executed >= threshold
    return TestOutcome(ok=ok, passed=passed, executed=executed, output=output or "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/react_repair/test_gate.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/react_repair/__init__.py src/react_repair/gate.py tests/react_repair/test_gate.py
git commit -m "feat(react): 80% test verdict gate"
```

---

### Task 2: `actions.py` — parse the agent's move

**Files:**
- Create: `src/react_repair/actions.py`
- Test: `tests/react_repair/test_actions.py`

**Interfaces — Produces:** `Action`, `parse_action`, `extract_thought`.

- [ ] **Step 1: Write the failing test**

```python
# tests/react_repair/test_actions.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.actions import parse_action, extract_thought


def test_parse_explore():
    a = parse_action("Thought: check libs\nAction: ldconfig -p | grep pq")
    assert a.kind == "explore" and a.command == "ldconfig -p | grep pq"

def test_parse_patch_full_script():
    text = "Thought: add libpq\nScript:\n```bash\napt-get install -y libpq-dev\npip install psycopg2\n```"
    a = parse_action(text)
    assert a.kind == "patch"
    assert "libpq-dev" in a.new_script and a.new_script.endswith("\n")

def test_patch_wins_over_action_when_both_present():
    a = parse_action("Action: ls\nScript:\n```bash\necho hi\n```")
    assert a.kind == "patch"

def test_unparseable_is_invalid():
    assert parse_action("I think we should install stuff").kind == "invalid"

def test_extract_thought():
    assert extract_thought("Thought: the header is missing\nAction: ls") == "the header is missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/react_repair/test_actions.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/react_repair/actions.py
"""The agent's move (spec §4): one read-only EXPLORE command or a full-script PATCH.
Parsing is pure; the loop enforces read-only on explore and applies the patch."""
from __future__ import annotations

import re
from dataclasses import dataclass

_SCRIPT_BLOCK = re.compile(r"Script:\s*```(?:bash|sh)?\s*\n(.*?)```", re.DOTALL)
_ACTION_LINE = re.compile(r"^Action:\s*(.+)$", re.MULTILINE)
_THOUGHT = re.compile(r"Thought:\s*(.+?)(?=\n(?:Action|Script):|$)", re.DOTALL)


@dataclass(frozen=True)
class Action:
    kind: str                       # "explore" | "patch" | "invalid"
    command: str | None = None      # explore
    new_script: str | None = None   # patch


def parse_action(text: str) -> Action:
    t = text or ""
    m = _SCRIPT_BLOCK.search(t)             # patch wins if both are present
    if m:
        return Action("patch", new_script=m.group(1).strip() + "\n")
    m = _ACTION_LINE.search(t)
    if m:
        return Action("explore", command=m.group(1).strip())
    return Action("invalid")


def extract_thought(text: str) -> str:
    m = _THOUGHT.search(text or "")
    return m.group(1).strip() if m else ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/react_repair/test_actions.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/react_repair/actions.py tests/react_repair/test_actions.py
git commit -m "feat(react): explore|patch action parsing"
```

---

### Task 3: `log.py` + `history.py` Tier 1 — records + safety truncation

**Files:**
- Create: `src/react_repair/log.py`
- Create: `src/react_repair/history.py`
- Test: `tests/react_repair/test_history.py`

**Interfaces — Produces:** `ReactLog`; `Step`, `safety_truncate`, `History` (record/render).

- [ ] **Step 1: Write the failing test**

```python
# tests/react_repair/test_history.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.history import History, safety_truncate


def test_safety_truncate_keeps_tail():
    out, applied = safety_truncate("x" * 100 + "ERROR_AT_END", max_chars=20)
    assert applied and out.endswith("ERROR_AT_END") and len(out) < 120

def test_short_observation_untouched():
    out, applied = safety_truncate("short", max_chars=20)
    assert not applied and out == "short"

def test_record_truncates_into_prompt_history():
    h = History(safety_max_chars=20)
    step = h.record(1, "t", "explore: ls", "y" * 200 + "TAIL")
    assert step.observation_raw.endswith("TAIL")
    assert len(step.observation_prompt) < 60 and step.observation_prompt.endswith("TAIL")

def test_render_includes_prior_steps():
    h = History(safety_max_chars=4000)
    h.record(1, "thought-a", "patch", "(patched)")
    h.record(2, "thought-b", "explore: ldconfig", "libpq found")
    rendered = h.render()
    assert "patch" in rendered and "explore: ldconfig" in rendered and "libpq found" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/react_repair/test_history.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/react_repair/log.py
"""Design-point logger for the react arm (fresh — NOT arm C's repair_log). Every line is
tagged with the spec guarantee it demonstrates so a later reader can grep-verify the design."""
from __future__ import annotations

DESIGN = {
    "RUN":       "§2 run the WHOLE build script fresh from base",
    "CERTIFY":   "§9 host checks flip node state (install-tier only, no double pytest)",
    "TEST_GATE": "§5 host-owned done: ≥80% of executed tests pass",
    "PLAN":      "§4 agent emits ONE move (explore|patch)",
    "EXPLORE":   "§4 read-only investigation (no container mutation)",
    "PATCH":     "§4 agent's mutation = a replacement build script; re-run fresh",
    "COMPRESS":  "§6 observation compression (per-run context management)",
    "DONE":      "§5 script green AND tests ≥80% — host-verified",
    "GIVEUP":    "§11 max_steps hit — honest stop with best-effort script",
}


class ReactLog:
    def __init__(self, silent: bool = False):
        self.events: list[tuple[str, str]] = []
        self.silent = silent

    def d(self, tag: str, msg: str) -> None:
        self.events.append((tag, msg))
        if self.silent:
            return
        print(f"  [DESIGN:{tag:<10}] {msg}")
        inv = DESIGN.get(tag, "")
        if inv:
            print(f"   {'':<12}└─ {inv}")

    def count(self, tag: str) -> int:
        return sum(1 for t, _ in self.events if t == tag)
```

```python
# src/react_repair/history.py
"""Per-run ReAct transcript + observation compression (spec §6). Tier 1 = deterministic
safety truncation (keep the tail, where errors live). Tier 2 (Task 4) = an LLM reflective
pass over old large observations. Pure except the injected compressor. No arm-C imports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Step:
    step_id: int
    thought: str
    action_summary: str
    observation_raw: str
    observation_prompt: str          # possibly truncated (Tier 1) then compressed (Tier 2)


def safety_truncate(text: str, *, max_chars: int, keep_tail: bool = True) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    if keep_tail:
        return "…[truncated]…\n" + text[-max_chars:], True
    return text[:max_chars] + "…[truncated]…", True


class History:
    def __init__(self, *, safety_max_chars: int = 4000, compress_delay: int = 2,
                 compress_threshold_chars: int = 1500,
                 compressor: "Callable[[Step, list[Step]], str] | None" = None,
                 log=None):
        self.steps: list[Step] = []
        self.safety_max_chars = safety_max_chars
        self.compress_delay = compress_delay
        self.compress_threshold_chars = compress_threshold_chars
        self.compressor = compressor
        self.log = log

    def record(self, step_id: int, thought: str, action_summary: str,
               observation_raw: str) -> Step:
        prompt_obs, _ = safety_truncate(observation_raw or "", max_chars=self.safety_max_chars)
        step = Step(step_id, thought, action_summary, observation_raw or "", prompt_obs)
        self.steps.append(step)
        self._maybe_compress()           # Tier 2 — no-op until Task 4 wires a compressor
        return step

    def _maybe_compress(self) -> None:
        return                            # implemented in Task 4

    def render(self) -> str:
        if not self.steps:
            return "(no prior steps)"
        return "\n".join(
            f"{s.step_id}. [{s.action_summary}] {s.observation_prompt}" for s in self.steps
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/react_repair/test_history.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/react_repair/log.py src/react_repair/history.py tests/react_repair/test_history.py
git commit -m "feat(react): design log + history with Tier-1 safety truncation"
```

---

### Task 4: `history.py` Tier 2 — reflective compression

**Files:**
- Modify: `src/react_repair/history.py` (`_maybe_compress`)
- Test: `tests/react_repair/test_history_compress.py`

**Interfaces — Consumes:** `History`, `Step`. **Produces:** working `_maybe_compress` driven by an injected `compressor`.

- [ ] **Step 1: Write the failing test**

```python
# tests/react_repair/test_history_compress.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.history import History


def test_old_large_observation_compressed_after_delay():
    calls = []
    def fake_compressor(target, context):
        calls.append(target.step_id)
        return f"[summary of step {target.step_id}]"
    h = History(safety_max_chars=100000, compress_delay=2,
                compress_threshold_chars=50, compressor=fake_compressor)
    h.record(1, "t", "explore", "B" * 200)     # large — a compression candidate once old enough
    h.record(2, "t", "explore", "small")
    assert calls == []                          # step 1 is only 1 behind — not yet past delay
    h.record(3, "t", "explore", "small")        # now step 1 is 2 behind → compress it
    assert calls == [1]
    assert h.steps[0].observation_prompt == "[summary of step 1]"

def test_small_old_observation_not_compressed():
    calls = []
    h = History(compress_delay=1, compress_threshold_chars=10_000,
                compressor=lambda t, c: calls.append(t.step_id) or "x")
    h.record(1, "t", "explore", "tiny")
    h.record(2, "t", "explore", "tiny")
    assert calls == []                          # below threshold → never compressed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/react_repair/test_history_compress.py -q`
Expected: FAIL (compression not applied; `_maybe_compress` is a no-op).

- [ ] **Step 3: Write minimal implementation**

Replace `_maybe_compress` in `src/react_repair/history.py`:

```python
    def _maybe_compress(self) -> None:
        if self.compressor is None:
            return
        target_idx = len(self.steps) - 1 - self.compress_delay
        if target_idx < 0:
            return
        target = self.steps[target_idx]
        already = target.observation_prompt != target.observation_raw and "[summary" in target.observation_prompt
        if already or len(target.observation_raw) < self.compress_threshold_chars:
            return
        try:
            reduced = self.compressor(target, self.steps[:target_idx])
        except Exception as exc:                                 # never break the run (spec §10)
            if self.log is not None:
                self.log.d("COMPRESS", f"compression failed, keeping raw: {exc}")
            return
        target.observation_prompt = reduced
        if self.log is not None:
            self.log.d("COMPRESS", f"step {target.step_id}: {len(target.observation_raw)} chars → summary")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/react_repair/test_history_compress.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/react_repair/history.py tests/react_repair/test_history_compress.py
git commit -m "feat(react): Tier-2 reflective observation compression"
```

---

### Task 5: `planner.py` — the ReAct planner with a graph-context slot

**Files:**
- Create: `src/react_repair/planner.py`
- Test: `tests/react_repair/test_planner.py`

**Interfaces — Consumes:** `History`, `parse_action`, `extract_thought`. **Produces:** `ReactPlanner.plan(history, script, observation, graph) -> (thought, Action, usage)`. The **`graph_context` slot** (a `Callable[[DepGraph], str] | None`) is the ONLY difference between baseline (None → empty) and the future graph-guided variant (populated).

- [ ] **Step 1: Write the failing test**

```python
# tests/react_repair/test_planner.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.react_repair.planner as planner_mod
from src.react_repair.planner import ReactPlanner
from src.react_repair.history import History


def _fake_llm(reply):
    return lambda *a, **k: (reply, {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw")


def test_plan_returns_patch(monkeypatch):
    monkeypatch.setattr(planner_mod, "complete_with_retry",
                        _fake_llm("Thought: add libpq\nScript:\n```bash\napt-get install -y libpq-dev\n```"))
    p = ReactPlanner(client=object(), model="m")
    thought, action, _ = p.plan(History(), "pip install psycopg2", "libpq.so.5 not found", graph=None)
    assert action.kind == "patch" and "libpq-dev" in action.new_script

def test_baseline_prompt_has_no_graph_context(monkeypatch):
    seen = {}
    def capture(client, model, messages, **k):
        seen["user"] = messages[-1]["content"]
        return "Action: ls", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw"
    monkeypatch.setattr(planner_mod, "complete_with_retry", capture)
    ReactPlanner(client=object(), model="m").plan(History(), "script", "obs", graph=None)
    assert "GRAPH CONTEXT" not in seen["user"]

def test_graph_context_injected_when_provided(monkeypatch):
    seen = {}
    def capture(client, model, messages, **k):
        seen["user"] = messages[-1]["content"]
        return "Action: ls", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw"
    monkeypatch.setattr(planner_mod, "complete_with_retry", capture)
    p = ReactPlanner(client=object(), model="m", graph_context=lambda g: "libpq: MISSING")
    p.plan(History(), "script", "obs", graph=object())
    assert "GRAPH CONTEXT" in seen["user"] and "libpq: MISSING" in seen["user"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/react_repair/test_planner.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/react_repair/planner.py
"""The react arm's LLM planner (spec §4). ReAct: reads the current script + latest failure +
compressed history, returns ONE move (explore|patch). The `graph_context` slot is empty for
the baseline and populated for the graph-guided variant — the ONLY difference between the two
(spec §14). No arm-C imports; uses the shared `complete_with_retry`."""
from __future__ import annotations

from typing import Any, Callable

from src.envstate.llm_response import complete_with_retry
from src.react_repair.actions import Action, extract_thought, parse_action

SYSTEM_PROMPT = """\
You are configuring a Python repo's environment by editing ONE build script (setup.sh) until
it runs green and the repo's tests pass. Each turn you see the current script, what happened
when it last ran, and your history. Respond with a Thought and exactly ONE of:
  Action: <one read-only shell command>     (investigate; you get its output next turn)
  Script: followed by one fenced ```bash block with the COMPLETE new setup.sh
Rules: read-only commands only for Action (ls, cat, ldconfig, pip show, apt-cache — never install/modify).
The ONLY way to change the build is to emit a new Script. Do not claim success; the host runs the tests."""


class ReactPlanner:
    def __init__(self, client: Any, model: str,
                 graph_context: "Callable[[Any], str] | None" = None,
                 log=None):
        self.client = client
        self.model = model
        self.graph_context = graph_context
        self.log = log

    def _render(self, history, script: str, observation: str, graph) -> str:
        parts = [
            "CURRENT setup.sh:\n```bash\n" + (script or "") + "\n```",
            "LAST RUN OBSERVATION:\n" + (observation or ""),
            "HISTORY (your prior moves and what happened):\n" + history.render(),
        ]
        if self.graph_context is not None:
            ctx = self.graph_context(graph) or ""
            if ctx.strip():
                parts.append("GRAPH CONTEXT (certified state):\n" + ctx)
        parts.append("Respond with Thought + one Action or Script.")
        return "\n\n".join(parts)

    def plan(self, history, script: str, observation: str, graph):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._render(history, script, observation, graph)},
        ]
        text, usage, _raw = complete_with_retry(self.client, self.model, messages,
                                                temperature=0, stop=["Observation:"])
        thought, action = extract_thought(text), parse_action(text)
        if self.log is not None:
            self.log.d("PLAN", f"thought={thought[:60]!r} action={action.kind}")
        return thought, action, usage
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/react_repair/test_planner.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/react_repair/planner.py tests/react_repair/test_planner.py
git commit -m "feat(react): ReAct planner with graph_context slot (baseline off)"
```

---

### Task 6: `loop.py` — the flat ReAct loop (`run_react`)

**Files:**
- Create: `src/react_repair/loop.py`
- Test: `tests/react_repair/test_loop.py`

**Interfaces — Consumes:** `ReactLog`, `History`, `TestOutcome`, `is_read_only`, `render_build_script`. **Produces:** `RunResult`, `run_react`. The loop re-runs the script only after a PATCH (explore is a free turn); the test result is cached and invalidated on patch (spec §5 "avoid double run", extended to avoid re-running an unchanged script).

- [ ] **Step 1: Write the failing test**

```python
# tests/react_repair/test_loop.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.loop import run_react, RunResult
from src.react_repair.gate import TestOutcome
from src.react_repair.history import History
from src.react_repair.log import ReactLog
from src.react_repair.actions import Action


class _ScriptedPlanner:
    """Emits a fixed queue of moves; ignores the prompt."""
    def __init__(self, moves): self.moves = list(moves)
    def plan(self, history, script, observation, graph):
        return "t", (self.moves.pop(0) if self.moves else Action("invalid")), {}


def _adapters(installed_needs, tests_need, script_box):
    """A FakeSandbox: build ok once `script` contains every token in `installed_needs`;
    tests pass once it also contains every token in `tests_need`."""
    def reset(): pass
    def run_script(script):
        script_box[0] = script
        missing = [t for t in installed_needs if t not in script]
        if missing:
            return RunResult(False, f"install {missing[0]}", f"{missing[0]}: not found")
        return RunResult(True)
    def certify(graph): return graph
    def exec_readonly(cmd): return (0, "probe-output")
    def run_tests():
        s = script_box[0]
        if all(t in s for t in tests_need):
            return TestOutcome(True, passed=5, executed=5, output="5 passed")
        return TestOutcome(False, passed=0, executed=1, output="ModuleNotFoundError: pytest_mock")
    return reset, run_script, certify, exec_readonly, run_tests


def _run(moves, installed_needs=(), tests_need=(), initial="pip install app\n"):
    box = [initial]
    reset, run_script, certify, ro, run_tests = _adapters(installed_needs, tests_need, box)
    log = ReactLog(silent=True)
    outcome, script, _ = run_react(
        object(), reset=reset, run_script=run_script, certify=certify, exec_readonly=ro,
        run_tests=run_tests, planner=_ScriptedPlanner(moves), history=History(), log=log,
        max_steps=10, _initial_script=initial)
    return outcome, script, log


def test_green_first_pass_is_done():
    outcome, _, log = _run([], tests_need=())
    assert outcome == "DONE" and log.count("TEST_GATE") >= 1

def test_build_failure_then_patch_reaches_done():
    fix = Action("patch", new_script="pip install app\napt-get install -y libpq-dev\n")
    outcome, script, _ = _run([fix], installed_needs=("libpq-dev",))
    assert outcome == "DONE" and "libpq-dev" in script

def test_tests_fail_then_patch_reaches_done():
    fix = Action("patch", new_script="pip install app\npip install pytest_mock\n")
    outcome, script, _ = _run([fix], tests_need=("pytest_mock",))
    assert outcome == "DONE" and "pytest_mock" in script

def test_explore_is_a_free_turn_no_rerun_needed():
    fix = Action("patch", new_script="pip install app\napt-get install -y libpq-dev\n")
    outcome, _, log = _run([Action("explore", command="cat setup.py"), fix],
                           installed_needs=("libpq-dev",))
    assert outcome == "DONE" and log.count("EXPLORE") == 1

def test_unfixable_gives_up():
    outcome, _, _ = _run([], installed_needs=("libunobtainium",))
    assert outcome == "GIVEUP"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/react_repair/test_loop.py -q`
Expected: FAIL (`ModuleNotFoundError: src.react_repair.loop`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/react_repair/loop.py
"""run_react — the flat ReAct loop (spec §2). Reset → run whole script → certify (install-tier)
→ if green + tests ≥80%, DONE. Else the planner emits ONE move: EXPLORE (read-only, a free turn,
no re-run) or PATCH (replace the script, reset + re-run). All adapters injected → Docker-free."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.patch_gate import is_read_only

_FORMAT_REMINDER = ("Respond with Thought + exactly one `Action: <read-only cmd>` or "
                    "`Script:` + one fenced ```bash block. No prose-only replies.")


@dataclass(frozen=True)
class RunResult:
    ok: bool
    failing_command: str | None = None
    output: str = ""


def _observation(result: RunResult, test) -> str:
    if not result.ok:
        return f"BUILD FAILED at `{result.failing_command}`:\n{result.output}"
    return f"BUILD OK. TESTS {test.passed}/{test.executed} passed:\n{test.output}"


def run_react(graph, *, reset, run_script, certify, exec_readonly, run_tests, planner,
              history, log, max_steps: int = 30, _initial_script: str | None = None):
    script = _initial_script if _initial_script is not None else render_build_script(graph)

    def rerun(s):
        reset()
        log.d("RUN", f"running {len(s.splitlines())}-line build script from base")
        r = run_script(s)
        g = certify(graph)
        log.d("CERTIFY", "install-tier node states refreshed" if r.ok else f"build failed: {r.failing_command}")
        return r, g

    result, graph = rerun(script)
    test = None
    for step in range(max_steps):
        if result.ok:
            if test is None:
                test = run_tests()
                log.d("TEST_GATE", f"{test.passed}/{test.executed} passed → {'ok' if test.ok else 'below 80%'}")
            if test.ok:
                log.d("DONE", "build green AND tests ≥80% — host-verified")
                return "DONE", script, graph
        observation = _observation(result, test)

        thought, action, _usage = planner.plan(history, script, observation, graph)

        if action.kind == "explore" and action.command and is_read_only(action.command):
            rc, out = exec_readonly(action.command)
            history.record(step, thought, f"explore: {action.command}", out)
            log.d("EXPLORE", f"{action.command} → rc{rc} (read-only)")
            continue                                    # same container/result — a free turn
        if action.kind == "patch" and action.new_script:
            script = action.new_script
            history.record(step, thought, "patch", "(replaced build script)")
            log.d("PATCH", "agent replaced setup.sh; re-running fresh")
            result, graph = rerun(script)
            test = None                                 # invalidate cached test result
            continue
        history.record(step, thought, "invalid", _FORMAT_REMINDER)   # explore-not-readonly or unparseable
        log.d("PLAN", f"invalid move ({action.kind}) — re-prompting")
    log.d("GIVEUP", f"max_steps {max_steps} hit — returning best-effort script")
    return "GIVEUP", script, graph
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/react_repair/test_loop.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/react_repair/loop.py tests/react_repair/test_loop.py
git commit -m "feat(react): flat ReAct loop (run_react) with DI"
```

---

### Task 7: `entry.py` — docker adapters + arm assembly

**Files:**
- Create: `src/react_repair/entry.py`
- Test: `tests/react_repair/test_entry.py`

**Interfaces — Consumes:** `Sandbox`, `certify_all`/`EXECUTION_LAYER_ORDER`, `CommandResult`, `render_build_script`, `test_verdict`, `ReactPlanner`, `run_react`, `History`, `ReactLog`. **Produces:** `docker_adapters(sandbox)`, `run_react_arm(...)`. This is the only file that touches Docker; it's exercised live, and a unit test covers the pure `certify`/`run_tests` mapping with a duck-typed sandbox.

- [ ] **Step 1: Write the failing test**

```python
# tests/react_repair/test_entry.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.entry import docker_adapters


class _FakeSandbox:
    def __init__(self, rc, out): self._rc, self._out = rc, out
    def reset_to_base(self): pass
    def run_install_script(self, script):
        from src.sandbox import InstallResult
        return InstallResult(rc=self._rc, failing_command=None if self._rc == 0 else "pip install x",
                             lineno=None, stderr=self._out)
    def exec_readonly(self, cmd): return (0, self._out)


def test_run_script_adapter_maps_installresult():
    _, run_script, _, _, _ = docker_adapters(_FakeSandbox(1, "boom"))
    r = run_script("pip install x")
    assert r.ok is False and r.failing_command == "pip install x" and "boom" in r.output

def test_run_tests_adapter_applies_80pct_verdict():
    _, _, _, _, run_tests = docker_adapters(_FakeSandbox(0, "9 passed, 1 failed in 1s"))
    assert run_tests().ok is True                       # 0.9 >= 0.8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/react_repair/test_entry.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/react_repair/entry.py
"""Production entry for the react arm (spec §14). Builds the arm's OWN docker adapters over the
shared Sandbox and assembles planner + loop. `certify` runs install-tier layers only (no TESTS
layer) so the suite is not run twice; `run_tests` is the single authoritative pytest run fed
through the 80% verdict. No arm-C imports."""
from __future__ import annotations

from typing import Any

from python_deps.depgraph.certify import EXECUTION_LAYER_ORDER, certify_all
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.schema import Layer
from src.envstate.constants import VERIFY_TEST_CMD           # shared canonical "python -m pytest -q"
from src.react_repair.gate import test_verdict
from src.react_repair.history import History
from src.react_repair.log import ReactLog
from src.react_repair.loop import RunResult, run_react
from src.react_repair.planner import ReactPlanner

# Install-tier layers only — drop TESTS so certify never re-runs the suite (spec §5).
_INSTALL_LAYERS = tuple(l for l in EXECUTION_LAYER_ORDER if l is not Layer.TESTS)


class _ExecAdapter:
    """certify_all wants an executor.run(cmd) -> CommandResult; wrap the sandbox's (rc,out)."""
    def __init__(self, exec_readonly):
        self._e = exec_readonly

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        rc, out = self._e(command)
        return CommandResult(command, rc, out if rc == 0 else "", "" if rc == 0 else out)


def docker_adapters(sandbox):
    def reset():
        sandbox.reset_to_base()

    def run_script(script: str) -> RunResult:
        r = sandbox.run_install_script(script)
        return RunResult(ok=(r.rc == 0), failing_command=r.failing_command, output=r.stderr or "")

    def certify(graph):
        return certify_all(graph, _ExecAdapter(sandbox.exec_readonly), layer_order=_INSTALL_LAYERS)

    def exec_readonly(cmd):
        return sandbox.exec_readonly(cmd)

    def run_tests():
        rc, out = sandbox.exec_readonly(VERIFY_TEST_CMD)
        return test_verdict(out)

    return reset, run_script, certify, exec_readonly, run_tests


def _make_compressor(client: Any, model: str):
    """Tier-2 reflective compressor: summarize an old observation via the LLM."""
    from src.envstate.llm_response import complete_with_retry

    def compress(target, _context) -> str:
        messages = [
            {"role": "system", "content": "Summarize this build/test output in 2-3 lines, keeping "
                                          "the exact error and any missing package/library names."},
            {"role": "user", "content": target.observation_raw[:8000]},
        ]
        text, _usage, _raw = complete_with_retry(client, model, messages, temperature=0)
        return f"[summary of step {target.step_id}] {text.strip()}"

    return compress


def run_react_arm(graph, *, sandbox, client, model, repo_path=None,
                  graph_context: bool = False, log=None, max_steps: int = 30):
    log = log or ReactLog()
    reset, run_script, certify, exec_readonly, run_tests = docker_adapters(sandbox)
    ctx = None                     # graph-guided variant (Task-future): build a graph_context fn
    planner = ReactPlanner(client, model, graph_context=(ctx if graph_context else None), log=log)
    history = History(compressor=_make_compressor(client, model), log=log)
    return run_react(graph, reset=reset, run_script=run_script, certify=certify,
                     exec_readonly=exec_readonly, run_tests=run_tests, planner=planner,
                     history=history, log=log, max_steps=max_steps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/react_repair/test_entry.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/react_repair/entry.py tests/react_repair/test_entry.py
git commit -m "feat(react): docker adapters + run_react_arm assembly"
```

---

### Task 8: Observability — structured trace, verbose, coverage

**Files:**
- Modify: `src/react_repair/log.py` (extend `ReactLog`: `trace`, `summary`, `close`, `REACT_VERBOSE`, `trace_path`)
- Modify: `src/react_repair/planner.py`, `history.py`, `loop.py`, `entry.py` (add trace hooks via the existing `log`)
- Test: `tests/react_repair/test_trace.py`

**Interfaces — Produces:** `ReactLog.trace(phase, **fields)`, `.summary() -> str`, `.close()`; `REACT_VERBOSE` gating; `run_react_arm(..., trace_out=None)`. Threaded through the existing `log` param — no new signatures on loop/planner/history (spec §15).

- [ ] **Step 1: Write the failing test**

```python
# tests/react_repair/test_trace.py
import sys, json, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.react_repair.planner as planner_mod
from src.react_repair.log import ReactLog
from src.react_repair.planner import ReactPlanner
from src.react_repair.history import History


def test_trace_kept_and_written(tmp_path):
    log = ReactLog(silent=True, trace_path=str(tmp_path / "t.jsonl"))
    log.d("PLAN", "x"); log.d("PLAN", "y"); log.trace("run", rc=0, ok=True)
    log.close()
    assert "PLAN×2" in log.summary()
    assert json.loads((tmp_path / "t.jsonl").read_text().strip())["phase"] == "run"

def test_planner_emits_plan_record_with_prompt(monkeypatch):
    monkeypatch.setattr(planner_mod, "complete_with_retry",
                        lambda *a, **k: ("Action: ls", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "raw"))
    log = ReactLog(silent=True)
    ReactPlanner(object(), "m", log=log).plan(History(), "script", "obs", graph=None)
    rec = next(r for r in log.records if r["phase"] == "plan")
    assert "prompt" in rec and rec["action"]["kind"] == "explore" and rec["observation"] == "obs"

def test_history_emits_compress_record():
    log = ReactLog(silent=True)
    h = History(compress_delay=1, compress_threshold_chars=10, compressor=lambda t, c: "SUM", log=log)
    h.record(1, "t", "explore", "B" * 50)
    h.record(2, "t", "explore", "small")            # step 1 now past the delay → compressed
    rec = next(r for r in log.records if r["phase"] == "compress")
    assert rec["raw_chars"] == 50 and rec["summary_chars"] == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/react_repair/test_trace.py -q`
Expected: FAIL (`ReactLog` has no `trace`).

- [ ] **Step 3: Implement — extend `ReactLog` and add hooks**

Replace the docstring + imports + class in `src/react_repair/log.py` (the `DESIGN` dict is unchanged):

```python
# src/react_repair/log.py
"""Observability sink for the react arm (spec §15; fresh — NOT arm C's repair_log). One object
threaded as `log`, three roles: (1) design-point tags to stdout [DESIGN:*] proving control
flow; (2) a structured per-step trace (`.trace`) — prompts, compaction, run/test — appended to
JSONL when `trace_path` is set and always kept in memory; (3) a run-end `.summary` coverage.
Stdout gated by REACT_VERBOSE (off → quiet)."""
from __future__ import annotations

import json
import os

DESIGN = {  # ... unchanged from Task 3 ...
}


class ReactLog:
    def __init__(self, silent: bool | None = None, trace_path: str | None = None):
        self.silent = (os.getenv("REACT_VERBOSE") != "1") if silent is None else silent
        self.events: list[tuple[str, str]] = []
        self.records: list[dict] = []
        self._fh = open(trace_path, "w") if trace_path else None

    def d(self, tag: str, msg: str) -> None:
        self.events.append((tag, msg))
        if self.silent:
            return
        print(f"  [DESIGN:{tag:<10}] {msg}")
        inv = DESIGN.get(tag, "")
        if inv:
            print(f"   {'':<12}└─ {inv}")

    def trace(self, phase: str, **fields) -> None:
        rec = {"phase": phase, **fields}
        self.records.append(rec)
        if self._fh is not None:
            self._fh.write(json.dumps(rec, default=str) + "\n")
            self._fh.flush()

    def count(self, tag: str) -> int:
        return sum(1 for t, _ in self.events if t == tag)

    def summary(self) -> str:
        line = " ".join(f"{t}×{self.count(t)}" for t in sorted({t for t, _ in self.events}))
        if not self.silent:
            print(f"  --- coverage: {line} ---")
        return line

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
```

`planner.py` — replace the `if self.log is not None:` block in `plan()`:

```python
        if self.log is not None:
            self.log.d("PLAN", f"thought={thought[:60]!r} action={action.kind}")
            self.log.trace("plan", observation=observation, prompt=messages, reply_raw=text,
                           thought=thought,
                           action={"kind": action.kind, "command": action.command,
                                   "new_script": action.new_script})
```

`history.py` — in `_maybe_compress`, after `target.observation_prompt = reduced`, extend the log block:

```python
        if self.log is not None:
            self.log.d("COMPRESS", f"step {target.step_id}: {len(target.observation_raw)} chars → summary")
            self.log.trace("compress", tier=2, target_step=target.step_id,
                           raw_chars=len(target.observation_raw), summary_chars=len(reduced),
                           summary=reduced)
```

`loop.py` — add a trace in `rerun` (after `certify`), after the test gate, and at each terminal:

```python
        # in rerun(), after `log.d("CERTIFY", ...)`:
        log.trace("run", script_len=len(s.splitlines()), ok=r.ok,
                  failing_command=r.failing_command, output_tail=(r.output or "")[-500:])
        # after `test = run_tests()` + its log.d:
                log.trace("test", passed=test.passed, executed=test.executed, ok=test.ok,
                          output_tail=(test.output or "")[-500:])
        # at the DONE return:
                log.trace("end", outcome="DONE", steps=step + 1); log.summary()
        # at the GIVEUP return:
    log.trace("end", outcome="GIVEUP", steps=max_steps); log.summary()
```

`entry.py` — `run_react_arm` gains `trace_out` and closes the log:

```python
def run_react_arm(graph, *, sandbox, client, model, repo_path=None, graph_context=False,
                  trace_out=None, log=None, max_steps=30):
    log = log or ReactLog(trace_path=trace_out)
    reset, run_script, certify, exec_readonly, run_tests = docker_adapters(sandbox)
    ctx = None
    planner = ReactPlanner(client, model, graph_context=(ctx if graph_context else None), log=log)
    history = History(compressor=_make_compressor(client, model), log=log)
    try:
        return run_react(graph, reset=reset, run_script=run_script, certify=certify,
                         exec_readonly=exec_readonly, run_tests=run_tests, planner=planner,
                         history=history, log=log, max_steps=max_steps)
    finally:
        log.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/react_repair/ -q`
Expected: PASS — new trace tests green AND all prior react tests unaffected (the added `log.trace`/`summary` calls only append records / print when non-silent).

- [ ] **Step 5: Commit**

```bash
git add src/react_repair/ tests/react_repair/test_trace.py
git commit -m "feat(react): observability — structured JSONL trace + verbose + coverage"
```

---

### Task 9: Wire `--arm react` into the e2e driver

**Files:**
- Modify: `scripts/run_v3_e2e.py` (the `--arm` argument + a `react` dispatch branch; add `--graph-context`)

**Interfaces — Consumes:** `run_react_arm`. Reuses the existing construction (base image, graph, sandbox) exactly as the `session` branch does.

- [ ] **Step 1: Add the CLI options**

In the argparse block, change the `--arm` choices and add the flag:

```python
    parser.add_argument("--arm", default="v3", choices=("v3", "session", "react"),
                        help="repair arm: v3 (graph-scheduled) | session (arm C) | react (flat ReAct script-repair)")
    parser.add_argument("--graph-context", action="store_true",
                        help="react arm only: feed certified graph state into the planner (graph-guided variant)")
    parser.add_argument("--trace-out", default=None,
                        help="react arm: write a per-step JSONL trace (prompts, compaction, run/test) to this path")
```

- [ ] **Step 2: Add the dispatch branch**

Immediately after the `if args.arm == "session":` block (which ends with `return 0 if ok else 1`), add:

```python
    if args.arm == "react":
        from src.react_repair.entry import run_react_arm
        try:
            outcome, script_text, out_graph = run_react_arm(
                graph, sandbox=sandbox, client=client, model=model, repo_path=args.repo,
                graph_context=args.graph_context, trace_out=args.trace_out)
        finally:
            try:
                if getattr(sandbox, "container", None) is not None:
                    sandbox.close()
            except Exception:
                pass
        with open(args.out, "w") as fh:
            fh.write(script_text)
        print(f"[v3] (react{'+graph' if args.graph_context else ''}) wrote setup.sh -> {args.out}")
        print(f"stop_reason={outcome}")
        ok = outcome == "DONE"                          # host-owned done (script green + tests ≥80%)
        print("V3 E2E:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
```

- [ ] **Step 3: Smoke-check the wiring compiles**

Run: `python3 -c "import ast; ast.parse(open('scripts/run_v3_e2e.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add scripts/run_v3_e2e.py
git commit -m "feat(react): --arm react dispatch + --graph-context flag"
```

---

### Task 10: Offline mechanics eval (`src/eval/react_repair_eval/`)

**Files:**
- Create: `src/eval/react_repair_eval/__init__.py` (empty)
- Create: `src/eval/react_repair_eval/fake_sandbox.py`
- Create: `src/eval/react_repair_eval/scenarios.py`
- Create: `src/eval/react_repair_eval/run_eval.py`
- Test: `tests/eval/test_react_repair_eval.py`

**Interfaces — Consumes:** `run_react`, `RunResult`, `TestOutcome`, `History`, `ReactLog`, `DESIGN`. Mirrors arm C's `FakeWorld` pattern, rewritten fresh (spec §14 salvage). Proves the loop e2e Docker-free with full design-point coverage.

- [ ] **Step 1: Write the FakeSandbox**

```python
# src/eval/react_repair_eval/fake_sandbox.py
"""Offline 'reality' for the react mechanics eval — script-based, no Docker. Build succeeds
once the script contains every `install_token`; tests pass once it also contains every
`test_token`. A read-only probe returns scripted output."""
from __future__ import annotations

from src.react_repair.gate import TestOutcome
from src.react_repair.loop import RunResult


class FakeSandbox:
    def __init__(self, install_tokens=(), test_tokens=(), probes=None):
        self.install_tokens = tuple(install_tokens)
        self.test_tokens = tuple(test_tokens)
        self.probes = probes or {}
        self._script = ""

    def reset(self): pass

    def run_script(self, script):
        self._script = script
        missing = [t for t in self.install_tokens if t not in script]
        if missing:
            return RunResult(False, f"install {missing[0]}", f"{missing[0]}: not found")
        return RunResult(True)

    def certify(self, graph):
        return graph

    def exec_readonly(self, cmd):
        for key, out in self.probes.items():
            if key in cmd:
                return (0, out)
        return (0, "")

    def run_tests(self):
        if all(t in self._script for t in self.test_tokens):
            return TestOutcome(True, passed=5, executed=5, output="5 passed in 0.1s")
        missing = [t for t in self.test_tokens if t not in self._script]
        return TestOutcome(False, passed=0, executed=1,
                           output=f"ModuleNotFoundError: No module named '{missing[0]}'")
```

- [ ] **Step 2: Write the scenarios + scripted planner**

```python
# src/eval/react_repair_eval/scenarios.py
"""Scenarios: (initial_script, FakeSandbox, ScriptedPlanner, expected_outcome)."""
from __future__ import annotations

from src.react_repair.actions import Action
from src.eval.react_repair_eval.fake_sandbox import FakeSandbox

_INIT = "pip install app\n"


class ScriptedPlanner:
    """Deterministic: maps a substring of (observation + rendered history) to a move. The probe
    output lands in HISTORY, not the observation, so it must search both. Skips an explore it
    already ran and a patch identical to the current script, so each rule fires at most once."""
    def __init__(self, rules): self.rules = rules      # list[(needle, Action)]
    def plan(self, history, script, observation, graph):
        haystack = (observation or "") + "\n" + history.render()
        for needle, move in self.rules:
            if needle not in haystack:
                continue
            if move.kind == "patch" and move.new_script == script:
                continue                                # already applied
            if move.kind == "explore" and f"explore: {move.command}" in haystack:
                continue                                # already ran this probe
            return "t", move, {}
        return "t", Action("invalid"), {}


def scenario_green():
    return _INIT, FakeSandbox(), ScriptedPlanner([]), "DONE"

def scenario_build_fail_then_patch():
    fix = Action("patch", new_script=_INIT + "apt-get install -y libpq-dev\n")
    return (_INIT, FakeSandbox(install_tokens=("libpq-dev",)),
            ScriptedPlanner([("not found", fix)]), "DONE")

def scenario_tests_fail_then_patch():
    fix = Action("patch", new_script=_INIT + "pip install pytest_mock\n")
    return (_INIT, FakeSandbox(test_tokens=("pytest_mock",)),
            ScriptedPlanner([("ModuleNotFoundError", fix)]), "DONE")

def scenario_explore_then_patch():
    fix = Action("patch", new_script=_INIT + "apt-get install -y libpq-dev\n")
    return (_INIT, FakeSandbox(install_tokens=("libpq-dev",), probes={"cat": "needs libpq"}),
            ScriptedPlanner([("not found", Action("explore", command="cat setup.py")),
                             ("needs libpq", fix)]), "DONE")   # explore output (in history) routes to the fix

def scenario_unfixable_giveup():
    return (_INIT, FakeSandbox(install_tokens=("libunobtainium",)),
            ScriptedPlanner([]), "GIVEUP")
```

Note: `scenario_explore_then_patch` chains observation→explore→(probe output)→patch, exercising the free-turn explore path.

- [ ] **Step 3: Write the runner**

```python
# src/eval/react_repair_eval/run_eval.py
"""Runs the react loop against every scenario; prints the design log + coverage. Run:
    python3 -m src.eval.react_repair_eval.run_eval"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.react_repair.history import History          # noqa: E402
from src.react_repair.log import DESIGN, ReactLog     # noqa: E402
from src.react_repair.loop import run_react           # noqa: E402
from src.eval.react_repair_eval import scenarios as S  # noqa: E402


def run_one(name, factory, silent=False):
    initial, box, planner, expect = factory()
    log = ReactLog(silent=silent)
    outcome, _script, _g = run_react(
        object(), reset=box.reset, run_script=box.run_script, certify=box.certify,
        exec_readonly=box.exec_readonly, run_tests=box.run_tests, planner=planner,
        history=History(), log=log, max_steps=12, _initial_script=initial)
    fired = {t for t, _ in log.events}
    if not silent:
        print(f"\n  RESULT: {outcome}  ({'PASS' if outcome == expect else 'FAIL — expected ' + expect})")
    return outcome == expect, fired


def main():
    cases = [
        ("green first pass", S.scenario_green),
        ("build fail → patch", S.scenario_build_fail_then_patch),
        ("tests fail → patch", S.scenario_tests_fail_then_patch),
        ("explore → patch", S.scenario_explore_then_patch),
        ("unfixable → giveup", S.scenario_unfixable_giveup),
    ]
    ok_all, fired_all = True, set()
    for name, factory in cases:
        print("\n" + "=" * 70 + f"\nSCENARIO: {name}\n" + "=" * 70)
        ok, fired = run_one(name, factory)
        ok_all &= ok
        fired_all |= fired
    missing = sorted(set(DESIGN) - fired_all)
    print(f"\n  design-points NEVER exercised: {missing or 'none — full coverage'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Write the pytest wrapper**

```python
# tests/eval/test_react_repair_eval.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.eval.react_repair_eval.run_eval import run_one
from src.eval.react_repair_eval import scenarios as S
from src.react_repair.log import DESIGN


def test_all_scenarios_pass_and_cover_design():
    fired = set()
    for factory in (S.scenario_green, S.scenario_build_fail_then_patch,
                    S.scenario_tests_fail_then_patch, S.scenario_explore_then_patch,
                    S.scenario_unfixable_giveup):
        ok, f = run_one(factory.__name__, factory, silent=True)
        assert ok, factory.__name__
        fired |= f
    # every design point exercised except the LLM-only COMPRESS tag (covered in unit tests)
    assert set(DESIGN) - fired <= {"COMPRESS"}
```

- [ ] **Step 5: Run eval + test**

Run: `python3 -m src.eval.react_repair_eval.run_eval && python3 -m pytest tests/eval/test_react_repair_eval.py -q`
Expected: eval exits 0 with `design-points NEVER exercised: ['COMPRESS']` (or none); test PASS.

- [ ] **Step 6: Commit**

```bash
git add src/eval/react_repair_eval/ tests/eval/test_react_repair_eval.py
git commit -m "test(react): offline mechanics eval (FakeSandbox + scenarios + design coverage)"
```

---

### Task 11: Retire arm C

**Files:**
- Delete: `src/envstate/repair_arm.py`, `repair_fix.py`, `repair_session.py`, `session_agent.py`, `repair_arm_entry.py`, `repair_log.py`
- Delete: `src/eval/repair_arm_eval/` (whole package)
- Delete: `tests/envstate/test_repair_*.py`, `tests/envstate/test_session_agent.py`, `tests/envstate/test_repair_arm_entry.py`, `tests/eval/test_repair_arm_eval*` (whatever exists)
- Modify: `scripts/run_v3_e2e.py` (remove the `--arm session` branch; drop `session` from `--arm` choices)
- Evaluate: `src/envstate/repair_types.py` — delete only if nothing outside arm C imports `ReplayResult`.

**Interfaces:** none produced; this removes the superseded arm.

- [ ] **Step 1: Verify nothing outside arm C imports these modules**

Run:
```bash
grep -rn "repair_arm\|repair_fix\|repair_session\|session_agent\|repair_arm_entry\|repair_log\|repair_arm_eval" \
  src/ scripts/ tests/ --include=*.py | grep -v "src/envstate/repair_\|src/eval/repair_arm_eval/\|tests/envstate/test_repair\|tests/envstate/test_session\|tests/eval/test_repair"
```
Expected: only the `--arm session` references in `scripts/run_v3_e2e.py`. If anything else appears, stop and reconcile.

- [ ] **Step 2: Check `ReplayResult` usage before deleting `repair_types.py`**

Run: `grep -rn "ReplayResult\|repair_types" src/ tests/ --include=*.py | grep -v "src/envstate/repair_\|repair_arm_eval"`
Expected: empty → safe to delete `repair_types.py`. If non-empty, keep it.

- [ ] **Step 3: Remove the `session` dispatch + choice**

In `scripts/run_v3_e2e.py`: delete the entire `if args.arm == "session":` block, and change `choices=("v3", "session", "react")` → `choices=("v3", "react")`.

- [ ] **Step 4: Delete the files**

```bash
git rm src/envstate/repair_arm.py src/envstate/repair_fix.py src/envstate/repair_session.py \
       src/envstate/session_agent.py src/envstate/repair_arm_entry.py src/envstate/repair_log.py
git rm -r src/eval/repair_arm_eval
git rm tests/envstate/test_repair_arm.py tests/envstate/test_repair_fix.py \
       tests/envstate/test_repair_session.py tests/envstate/test_session_agent.py \
       tests/envstate/test_repair_arm_entry.py tests/eval/test_repair_arm_eval.py 2>/dev/null || true
# and repair_types.py ONLY if Step 2 was empty:
# git rm src/envstate/repair_types.py
```
(Use `git rm` only on paths that exist — list actual test files first with `ls tests/envstate/test_repair_* tests/envstate/test_session_agent.py`.)

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: green — no import errors, no references to the deleted arm. Confirm the react suite (`tests/react_repair/`, `tests/eval/test_react_repair_eval.py`) still passes.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: retire arm C (superseded by --arm react)"
```

---

## Verification (whole plan)

```bash
# unit + eval, Docker-free:
python3 -m pytest tests/react_repair/ tests/eval/test_react_repair_eval.py -q
python3 -m src.eval.react_repair_eval.run_eval        # exits 0, full design coverage
python3 -m pytest tests/ -q                           # whole suite green after retirement
# live smoke (needs key): small pure-Python + one native-dep repo, with full observability
REACT_VERBOSE=1 OPENROUTER_API_KEY=<key> OPENROUTER_API_BASE=https://openrouter.ai/api/v1 \
  python3 scripts/run_v3_e2e.py <repo> --arm react --base-image python:3.11-slim \
  --out /tmp/react.sh --trace-out /tmp/react_trace.jsonl --model openai/gpt-4o
# then inspect the run: prompts, compaction, and the agent↔system round-trip
python3 -c "import json;[print(r['phase'], {k:v for k,v in r.items() if k!='prompt'}) for r in map(json.loads, open('/tmp/react_trace.jsonl'))]"
```

## Notes for the implementer

- **Import boundary is a hard rule** (spec §14): nothing in `src/react_repair/` may import from arm C's modules. If you reach for one, rewrite the piece fresh instead.
- **`graph_context` stays wired-but-off**: Task 5/7 build the slot; the baseline passes `None`. Do **not** implement the graph-guided context function in this plan — it's the next milestone (spec §12). The seam existing is what makes the future A/B a one-flag change.
- **Do Task 11 last**, after the react suite is green, so arm C stays available as a reading reference while you port its patterns.
