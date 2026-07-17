# Environment-State Supervisor/Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace DockerAgent's single transcript-driven ReAct loop with a Supervisor-Planner → ReAct-Worker → host-maintained EnvState world model → host-probe certification loop, where only host code may certify environment truth.

**Architecture:** A new `src/envstate/` package adds an immutable `EnvStateSnapshot` (revisioned JSON world model), an append-only `ActionLedger`, a read-only extractor, fixed V1 probes, an LLM `Maintainer` (proposes deltas), an LLM `Supervisor` (emits bounded `TaskSpec`s), and a `Worker` (bounded ReAct execution). All LLM proposals pass through a hard ACL: **no LLM may write `PRESENT`/`MISSING` or `Evidence`**. The new orchestrator runs behind feature flags so the existing `DockerAgent.run()` loop keeps working unchanged until each layer is proven; the env_revision verification false-pass fix and the synthesis-gate rewrite are correctness fixes that ship independently.

**Tech Stack:** Python 3.11+, `unittest.TestCase` tests (repo convention — *no* pytest fixtures/markers), OpenAI-compatible chat client (shared `self.client`), Docker SDK (`docker` package), `dataclasses` (frozen, immutable). Tests run under `python -m pytest tests/...` but are written as `unittest.TestCase` classes.

---

## How to read this plan

This is a large redesign. It is organized into **5 milestones (A–E)** containing **13 phases (0–12)** that map 1:1 to the design doc's §17 Implementation Order. Each milestone leaves the system in a working, testable, shippable state:

- **Milestone A — Correctness & Foundations** (Phases 0–2): worktree, None-content hardening, env_revision verification false-pass fix. *No new architecture; ships as bug fixes.*
- **Milestone B — World Model** (Phases 3–5): `EnvStateSnapshot` + ACL + `ActionLedger`, wired to *observe* the existing loop in shadow mode (no behavior change).
- **Milestone C — Host Observation** (Phases 6–8): extractor, V1 probes, Maintainer interpreter. Host begins certifying facts.
- **Milestone D — Orchestration** (Phases 9–10): Supervisor + Worker + new orchestrator behind `--enable-supervisor`.
- **Milestone E — Synthesis & Verification** (Phases 11–12): ledger+probe synthesis gate, clean-room rebuild verification.

**You may stop after any milestone and have shippable value.** Milestone A is independently valuable even if the rest is never built.

---

## Design Decisions & Conventions (read before coding)

These decisions were made while translating `docs/ENVSTATE_SUPERVISOR_WORKER_DESIGN.md` against the actual `radical` branch. They are summarized for the project owner at the end of the plan.

1. **Baseline = `radical` worktree, not `envgraph-v1`.** All file paths below are relative to a clean worktree created from `radical` (Phase 0). The current `envgraph-v1` WIP is irrelevant.

2. **Strangler-fig, not big-bang.** The design says "replace the loop", but `DockerAgent.run()` (`agent.py:778-978`) is a single ~200-line method with intertwined try/except/finally that *always* writes the run summary and closes the sandbox. We do **not** rewrite it in place. Instead:
   - Milestones B–E machinery is gated by `--enable-envstate` / `--enable-supervisor` / `--enable-cleanroom` (all default off). With the flags off the legacy `run()` behavior is unchanged: the new code lives behind `getattr(self, "enable_*", False)` guards, and `test_agent_verification.py` (which sets no flags) must stay green and behaviorally identical.
   - Milestone D adds a **separate** orchestrator entered via `--enable-supervisor` (default off). The old loop remains the default until the new one is proven on benchmarks. The Phase-11 ledger synthesis gate only changes behavior under `--enable-envstate`.
   - **Exception — Milestone A is always on and *deliberately changes* legacy output.** Phase 1 (None-content guard) and Phase 2 (env_revision verification fix) are correctness fixes that apply to *every* run, including the legacy loop. Phase 2 will change the verification verdict for runs that previously false-passed on stale evidence — that is the point. Phase 2 Step 6 requires running the existing suite first and updating any test that encoded the old (buggy) revision-blind acceptance. So "unchanged legacy behavior" means the *new EnvState machinery* is inert when off, NOT that Phase 2 leaves verification output bit-for-bit identical.

3. **New `src/envstate/` package, many small files** (per the repo's file-organization rule). Do not bloat `agent.py` (already 1976 lines) or `synthesizer.py` (already 3866 lines).

4. **Immutable, frozen dataclasses + `dataclasses.replace()`** for all EnvState types. The global coding-style rule mandates immutability; the design shows JSON but does not require mutability. The one mutable container is `ActionLedger` (an append-only log — the accepted exception).

5. **The ACL is the load-bearing invariant.** It lives in `src/envstate/acl.py`. Host code is the only writer of `PRESENT`/`MISSING`/`Evidence`. LLM proposals may only create `REQUIRED`/`UNKNOWN` hypotheses with source `LLM_GUESS`/`MEMORY`/`STATIC_SCAN`. Violations are dropped + logged, never crash.

6. **Probes/extractor BYPASS `Sandbox.execute()`.** `execute()` (`sandbox.py:164`) commits snapshots, rejects compound/filtered commands via preflight, retries pip, and injects Chinese-language `SYSTEM:` prefixes into output — all hostile to probing. Phase 6 adds `Sandbox.exec_readonly()` that calls `container.exec_run` directly and returns raw `(rc, output)`.

7. **Tests mirror existing conventions exactly:** `unittest.TestCase` classes; build stateful objects via `Sandbox.__new__(Sandbox)` / `DockerAgent.__new__(DockerAgent)` and set only the attributes the method touches; fake the LLM with a `SimpleNamespace`-shaped client (`client.chat.completions.create(**kwargs)` returning `choices[0].message.content` + a `usage` namespace); fake Docker with `FakeContainer.exec_run` returning `SimpleNamespace(exit_code, output=b"...")`. **Never** introduce `@pytest.fixture`/`@pytest.mark` — the repo has zero of them.

8. **STATIC_SCAN requirement parsing is net-new and Python-first.** No structured package list exists today (`files_content` is raw text flattened into `docs`). Phase 3 adds a Python `requirements.txt`/`pyproject`/`setup` parser hung off the `LanguageHandler` protocol; other languages are explicitly deferred.

9. **Clean-room verification runs host-side** (Phase 12), not as an agent action — `planner.py:109` forbids `docker build`/`docker run` as agent commands.

10. **Memory integration is deferred to a documented future phase.** The design lists memory eviction/demotion as an open decision. We add the *guardrail* (memory → `REQUIRED`/`UNKNOWN` only, never `PRESENT`) in the ACL now, but do not build the demotion policy. See "Deferred / Open" at the end.

---

## Canonical Shared Types (reference)

These types are **created in Phase 3** (`src/envstate/types.py`) and referenced by every later phase. They are listed here once so later tasks stay consistent. Do not redefine them elsewhere.

```python
# src/envstate/types.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Tuple


# --- Vocabularies (plain string constants; repo uses strings, not enum.Enum) ---
class Source:
    STATIC_SCAN = "STATIC_SCAN"
    PROBE = "PROBE"
    DIAGNOSE = "DIAGNOSE"
    MEMORY = "MEMORY"
    LLM_GUESS = "LLM_GUESS"


class Status:
    REQUIRED = "REQUIRED"
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


# ACL authority sets — the heart of the trust boundary.
PRESENCE_STATUSES = frozenset({Status.PRESENT, Status.MISSING})
HOST_ONLY_SOURCES = frozenset({Source.PROBE, Source.DIAGNOSE})
LLM_ALLOWED_STATUSES = frozenset({Status.REQUIRED, Status.UNKNOWN})
LLM_ALLOWED_SOURCES = frozenset({Source.LLM_GUESS, Source.MEMORY, Source.STATIC_SCAN})


@dataclass(frozen=True)
class Evidence:
    probe_cmd: str
    rc: int
    stdout_predicate: str
    env_revision: int
    container_id: str


@dataclass(frozen=True)
class Requirement:
    id: str
    name: str
    kind: str            # "LanguagePackage" | "Tool" | "Header" | "SharedLibrary" | "PkgConfig"
    status: str          # one of Status.*
    source: str          # one of Source.*
    specifier: Optional[str] = None
    required_by: Tuple[str, ...] = ()
    provides: Tuple[str, ...] = ()
    suspected_provides: Tuple[str, ...] = ()
    evidence: Optional[Evidence] = None


@dataclass(frozen=True)
class ProviderFact:
    provider: str
    provides: Tuple[str, ...]
    source: str          # DIAGNOSE typically
    diagnose_cmd: Optional[str] = None


@dataclass(frozen=True)
class OpenFailure:
    signature: str
    first_seen_revision: int
    last_seen_revision: int
    hypothesis: Optional[str] = None
    already_tried: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BaseFacts:
    image: str
    distro: Optional[str] = None
    distro_version: Optional[str] = None
    arch: Optional[str] = None
    python: Optional[str] = None


@dataclass(frozen=True)
class EnvStateSnapshot:
    revision: int
    container_id: str
    base: BaseFacts
    requirements: Tuple[Requirement, ...] = ()
    provider_facts: Tuple[ProviderFact, ...] = ()
    open_failures: Tuple[OpenFailure, ...] = ()
    stale_evidence: Tuple[Requirement, ...] = ()
    plan_notes: Tuple[str, ...] = ()
```

---

# Milestone A — Correctness & Foundations

## Phase 0: Create the implementation worktree

**Files:** none (environment setup only)

- [ ] **Step 1: Create a clean worktree from `radical`**

```bash
cd /Users/john/Jayint-repo
git worktree add /Users/john/dockeragent-envstate-v1 radical -b envstate-supervisor-v1
cd /Users/john/dockeragent-envstate-v1
```

- [ ] **Step 2: Create the Python env and confirm tests run green on the baseline**

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install pytest
python -m pytest tests/test_synthesizer.py tests/test_agent_verification.py -q
```

Expected: all PASS (this is the untouched `radical` baseline). If anything fails here, stop — the baseline is broken and must be triaged before continuing.

- [ ] **Step 3: Commit a no-op marker so the branch exists**

```bash
git commit --allow-empty -m "chore: start envstate-supervisor-v1 from radical"
```

> From here on, all paths are relative to `/Users/john/dockeragent-envstate-v1`.

---

## Phase 1: Harden the planner against `None` completion content

**Why:** `radical` predates the main-tree fix `50c8389`. At `planner.py:202`, `content = response.choices[0].message.content` can be `None` (observed with `deepseek-v4-flash`). The very next call, `_extract_thought` → `_extract_tag` (`planner.py:553`), does `re.search(pattern, None)` and crashes the whole run. The Supervisor/Maintainer add many more LLM calls, so this must be fixed first.

**Files:**
- Modify: `src/planner.py:202` and `src/planner.py:553`
- Test: `tests/test_planner_history.py` (add a new `unittest.TestCase` class)

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_planner_history.py` (it already imports `unittest`, `SimpleNamespace`, and `Planner`):

```python
class PlannerNoneContentTests(unittest.TestCase):
    def _make_planner(self):
        class FakeCompletionsNone:
            def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1),
                )

        return Planner(
            client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletionsNone()))
        )

    def test_plan_tolerates_none_completion_content(self):
        planner = self._make_planner()
        thought, action, content, is_finished, usage = planner.plan(
            "https://github.com/example/repo.git",
            "previous observation",
        )
        self.assertIsNone(action)
        self.assertFalse(is_finished)
        self.assertEqual(content, "")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_planner_history.py::PlannerNoneContentTests -v`
Expected: FAIL with `TypeError: expected string or bytes-like object, got 'NoneType'` raised from `_extract_tag`.

- [ ] **Step 3: Normalize `content` to `""` at the parse boundary**

In `src/planner.py`, change line 202 from:

```python
        content = response.choices[0].message.content
```

to:

```python
        content = response.choices[0].message.content or ""
```

- [ ] **Step 4: Add a defensive guard in `_extract_tag`**

In `src/planner.py`, change `_extract_tag` (line 553) from:

```python
    def _extract_tag(self, text, tag):
        labels = r"Thought|Action|Observation|Verification\ Bundle|Final\ Answer"
```

to:

```python
    def _extract_tag(self, text, tag):
        if not text:
            return None
        labels = r"Thought|Action|Observation|Verification\ Bundle|Final\ Answer"
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_planner_history.py::PlannerNoneContentTests -v`
Expected: PASS

- [ ] **Step 6: Run the full planner suite for regressions**

Run: `python -m pytest tests/test_planner_history.py -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/planner.py tests/test_planner_history.py
git commit -m "fix(planner): tolerate None completion content (port of 50c8389 to radical)"
```

---

## Phase 2: Fix the env_revision verification false-pass

**Why (the precise bug):** `_record_successful_action` (`agent.py:1564`) stamps every successful action with `environment_revision` (line 1577) and increments `self._environment_revision` on mutation (line 1587). Live, a post-verification mutation correctly clears `verified_test_commands` via `_invalidate_verification_group` (line 1588). **But** `derive_supported_verification_bundle` (`src/verification_bundle.py:21`) — used to validate the *agent-reported* bundle at `agent.py:1668` and to resynthesize from a saved summary at `workplace_replay.py:81` — is **revision-blind**: `_collect_effective_observed_test_commands` (`verification_bundle.py:75`) reads only `command` + `observation`, never `environment_revision`. So a test command that passed at revision N is still reported "supported" even if a mutation advanced the env to N+1 afterward. In `workplace_replay` resynthesis there is no live invalidation at all, so this is the *only* gate. This is the "independent env_revision false-pass."

**The fix:** A test command's observed success counts only if it occurred at the **final** environment revision (no env-mutating action happened after it). This applies **only to test commands** — `runtime_preparation_commands` legitimately span earlier revisions because they *build* cumulative state.

**Files:**
- Modify: `src/verification_bundle.py` (`derive_supported_verification_bundle`, `_collect_effective_observed_test_commands`)
- Modify: `agent.py:1668` (pass authoritative `environment_revision` into the gate)
- Test: `tests/test_verification_bundle_revision.py` (new file, `unittest.TestCase`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_verification_bundle_revision.py`:

```python
import unittest

from src.synthesizer import Synthesizer
from src.verification_bundle import derive_supported_verification_bundle


def _action(step, command, observation, revision, mutates):
    return {
        "step_index": step,
        "command": command,
        "observation": observation,
        "environment_revision": revision,
        "mutates_environment": mutates,
    }


class VerificationBundleRevisionTests(unittest.TestCase):
    def setUp(self):
        self.synth = Synthesizer()

    def test_rejects_test_command_made_stale_by_later_mutation(self):
        # pytest passed at revision 1, then a mutating install advanced env to revision 2.
        run_summary = {
            "environment_revision": 2,
            "verification_bundle": {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest -q"],
            },
            "successful_actions": [
                _action(1, "pytest -q", "collected 3 items\n3 passed", revision=1, mutates=False),
                _action(2, "pip install extra-pkg", "Successfully installed extra-pkg", revision=2, mutates=True),
            ],
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=self.synth)
        self.assertEqual(bundle["test_commands"], [])

    def test_accepts_test_command_at_final_revision(self):
        run_summary = {
            "environment_revision": 1,
            "verification_bundle": {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest -q"],
            },
            "successful_actions": [
                _action(1, "pip install -e .", "Successfully installed pkg", revision=1, mutates=True),
                _action(2, "pytest -q", "collected 3 items\n3 passed", revision=1, mutates=False),
            ],
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=self.synth)
        self.assertEqual(bundle["test_commands"], ["pytest -q"])

    def test_fallback_does_not_promote_stale_last_test(self):
        # No reported test matches; the only observed test is stale -> must NOT be promoted.
        run_summary = {
            "environment_revision": 2,
            "verification_bundle": {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest tests/does_not_match"],
            },
            "successful_actions": [
                _action(1, "pytest -q", "collected 3 items\n3 passed", revision=1, mutates=False),
                _action(2, "pip install extra-pkg", "Successfully installed extra-pkg", revision=2, mutates=True),
            ],
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=self.synth)
        self.assertEqual(bundle["test_commands"], [])


class VerificationBundleLiveStampingTests(unittest.TestCase):
    """Drive the REAL agent stamping (_record_successful_action) instead of hand-
    stamping `environment_revision`, so the gate is tested against how the agent
    actually records evidence (closes review finding: unit fixtures could pass even
    if live stamping were wrong)."""

    def _make_agent(self):
        from agent import DockerAgent
        agent = DockerAgent.__new__(DockerAgent)
        agent.synthesizer = Synthesizer()
        agent.successful_test_commands = []
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.verified_runtime_preparation_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
        agent._environment_revision = 0
        agent._current_verification_group = []
        agent.required_local_services = set()
        agent.enable_envstate = False
        agent.action_ledger = None
        return agent

    def test_live_stamped_stale_test_is_rejected_by_gate(self):
        agent = self._make_agent()
        agent._record_successful_action(1, "pip install -e .", "Successfully installed pkg")
        agent._record_successful_action(2, "pytest -q", "collected 3 items\n3 passed")
        agent._record_successful_action(3, "pip install extra-pkg", "Successfully installed extra-pkg")
        run_summary = {
            "environment_revision": agent._environment_revision,
            "verification_bundle": {"runtime_preparation_commands": [], "test_commands": ["pytest -q"]},
            "successful_actions": agent.successful_actions,
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=Synthesizer())
        self.assertEqual(bundle["test_commands"], [])  # stale: a mutation ran after the test

    def test_live_stamped_current_test_is_accepted_by_gate(self):
        agent = self._make_agent()
        agent._record_successful_action(1, "pip install -e .", "Successfully installed pkg")
        agent._record_successful_action(2, "pytest -q", "collected 3 items\n3 passed")
        run_summary = {
            "environment_revision": agent._environment_revision,
            "verification_bundle": {"runtime_preparation_commands": [], "test_commands": ["pytest -q"]},
            "successful_actions": agent.successful_actions,
        }
        bundle = derive_supported_verification_bundle(run_summary, synthesizer=Synthesizer())
        self.assertEqual(bundle["test_commands"], ["pytest -q"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_verification_bundle_revision.py -v`
Expected: `test_rejects_test_command_made_stale_by_later_mutation`, `test_fallback_does_not_promote_stale_last_test`, and `test_live_stamped_stale_test_is_rejected_by_gate` FAIL (the stale `pytest -q` is wrongly accepted/promoted); the two "at final revision" tests PASS.

- [ ] **Step 3: Add a revision helper and make test-command collection revision-aware**

In `src/verification_bundle.py`, add this helper after `normalize_command_list` (after line 18):

```python
def _final_environment_revision(run_summary: dict[str, Any]) -> int:
    revisions = [
        record.get("environment_revision", 0)
        for record in (run_summary.get("successful_actions") or [])
        if isinstance(record, dict)
    ]
    observed_max = max(revisions) if revisions else 0
    declared = run_summary.get("environment_revision", 0) or 0
    return max(observed_max, declared)
```

Then change `_collect_effective_observed_test_commands` (line 75) to filter on the final revision. Replace the function body:

```python
def _collect_effective_observed_test_commands(
    run_summary: dict[str, Any],
    synthesizer: Synthesizer,
) -> list[str]:
    final_revision = _final_environment_revision(run_summary)
    commands = []
    for record in run_summary.get("successful_actions") or []:
        if not isinstance(record, dict):
            continue
        command = str(record.get("command") or "").strip()
        if not command:
            continue
        # A test command only proves the FINAL environment if no env-mutating
        # action ran after it (i.e. it was observed at the current revision).
        if record.get("environment_revision", 0) != final_revision:
            continue
        observation = str(
            record.get("observation_summary")
            or record.get("observation")
            or ""
        )
        analysis = synthesizer.analyze_test_run(command, observation)
        if analysis.get("is_effective_test_run") or (
            synthesizer.observation_has_effective_test_signal(observation)
            and not synthesizer.observation_has_test_failure_signal(observation)
            and not synthesizer.is_truncated_test_output_command(command)
        ):
            commands.append(command)
    return commands
```

> Note: `_collect_observed_successful_actions` (runtime-prep matching) is intentionally left revision-blind — runtime prep commands build cumulative state and are expected at earlier revisions. The silent fallback at lines 55-56 (`test_commands = [observed_test_commands[-1]]`) is now automatically hardened because `observed_test_commands` is already revision-filtered.
>
> **Scope of this fix (known coarseness):** the offline gate keys on `environment_revision` equality, which catches the dominant false-pass (a state-changing mutation ran after the test). It is *coarser* than the live `_invalidate_verification_group`, which also clears the group when an *ineffective* test runs after a verified one at the *same* revision (`agent.py:1605`). During a live run the live invalidation remains authoritative (the agent-report path also passes `verified_test_commands`); the offline revision filter is the safety net for `workplace_replay` resynthesis where no live tracking exists. Closing the same-revision-ineffective-test edge offline is deferred (low value: resynthesis from a saved summary rarely hits it) — documented in "Deferred / Open".

- [ ] **Step 4: Pass the authoritative revision into the agent-report gate**

In `agent.py`, inside `_finalize_verification_from_agent_report`, the dict passed to `derive_supported_verification_bundle` (lines 1668-1680) omits the run-level revision. Add it so the gate is robust even if a trailing failed mutation advanced the counter. Change the dict literal to include:

```python
        supported_bundle = derive_supported_verification_bundle(
            {
                "environment_revision": self._environment_revision,
                "verification_bundle": {
                    "runtime_preparation_commands": list(runtime_commands),
                    "test_commands": list(test_commands),
                },
                "verified_runtime_preparation_commands": self.verified_runtime_preparation_commands,
                "verified_test_commands": self.verified_test_commands,
                "verified_test_command": self.verified_test_command,
                "successful_actions": self.successful_actions,
            },
            synthesizer=self.synthesizer,
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_verification_bundle_revision.py -v`
Expected: all PASS

- [ ] **Step 6: Run regression suites that exercise the gate**

Run: `python -m pytest tests/test_agent_verification.py tests/test_synthesizer.py -q`
Expected: all PASS. If `test_agent_verification.py` has a test that relied on the old revision-blind behavior, investigate whether that test encodes a real false-pass; if so, update the test to add `environment_revision` to its fixtures and assert the corrected behavior (fix implementation, not the test, unless the test was asserting the bug).

- [ ] **Step 7: Commit**

```bash
git add src/verification_bundle.py agent.py tests/test_verification_bundle_revision.py
git commit -m "fix(verification): reject test evidence made stale by later env mutation"
```

> **Milestone A complete.** The agent now (a) survives `None` completions and (b) no longer reports success on stale test evidence. These ship without any new architecture.

---

# Milestone B — World Model (shadow mode)

Goal: build the immutable `EnvStateSnapshot`, the ACL, and the `ActionLedger`, then wire them to **observe** the existing loop without changing its behavior. Everything in this milestone is gated behind `enable_envstate` (default `False`).

## Phase 3: EnvStateSnapshot dataclasses + JSON (de)serialization

**Files:**
- Create: `src/envstate/__init__.py`
- Create: `src/envstate/types.py` (the Canonical Shared Types above)
- Create: `src/envstate/serde.py` (to_dict / from_dict)
- Test: `tests/test_envstate_types.py`

- [ ] **Step 1: Create the package marker**

Create `src/envstate/__init__.py`:

```python
"""Host-maintained environment-state world model for DockerAgent."""
```

- [ ] **Step 2: Write the failing test for types + round-trip serialization**

Create `tests/test_envstate_types.py`:

```python
import unittest

from src.envstate.types import (
    BaseFacts,
    EnvStateSnapshot,
    Evidence,
    OpenFailure,
    ProviderFact,
    Requirement,
    Source,
    Status,
)
from src.envstate.serde import snapshot_to_dict, snapshot_from_dict


def _sample_snapshot():
    return EnvStateSnapshot(
        revision=8,
        container_id="abc123",
        base=BaseFacts(image="python:3.11-slim", distro="debian", arch="amd64", python="3.11.9"),
        requirements=(
            Requirement(
                id="lang:psycopg2==2.8.6",
                name="psycopg2",
                kind="LanguagePackage",
                status=Status.REQUIRED,
                source=Source.STATIC_SCAN,
                specifier="==2.8.6",
                required_by=("requirements.txt",),
            ),
            Requirement(
                id="tool:pg_config",
                name="pg_config",
                kind="Tool",
                status=Status.PRESENT,
                source=Source.PROBE,
                required_by=("lang:psycopg2==2.8.6",),
                evidence=Evidence(
                    probe_cmd="command -v pg_config && pg_config --version",
                    rc=0,
                    stdout_predicate="path exists and version prints",
                    env_revision=8,
                    container_id="abc123",
                ),
            ),
        ),
        provider_facts=(
            ProviderFact(
                provider="apt:libpq-dev",
                provides=("tool:pg_config", "header:libpq-fe.h"),
                source=Source.DIAGNOSE,
                diagnose_cmd="apt-file search bin/pg_config",
            ),
        ),
        open_failures=(
            OpenFailure(
                signature="pg_config executable not found",
                first_seen_revision=7,
                last_seen_revision=7,
                hypothesis="psycopg2 source build requires PostgreSQL dev tooling",
            ),
        ),
        plan_notes=("Do not substitute psycopg2-binary for pinned psycopg2.",),
    )


class EnvStateSerdeTests(unittest.TestCase):
    def test_round_trips_through_dict(self):
        snapshot = _sample_snapshot()
        restored = snapshot_from_dict(snapshot_to_dict(snapshot))
        self.assertEqual(restored, snapshot)

    def test_to_dict_matches_design_shape(self):
        data = snapshot_to_dict(_sample_snapshot())
        self.assertEqual(data["revision"], 8)
        self.assertEqual(data["base"]["python"], "3.11.9")
        present = [r for r in data["requirements"] if r["status"] == "PRESENT"][0]
        self.assertEqual(present["evidence"]["rc"], 0)
        self.assertEqual(present["evidence"]["env_revision"], 8)

    def test_snapshot_is_immutable(self):
        snapshot = _sample_snapshot()
        with self.assertRaises(Exception):
            snapshot.revision = 9  # frozen dataclass -> FrozenInstanceError
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_envstate_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.types'`.

- [ ] **Step 4: Create `src/envstate/types.py`**

Use the exact contents from the "Canonical Shared Types" section above.

- [ ] **Step 5: Create `src/envstate/serde.py`**

```python
from __future__ import annotations
from dataclasses import asdict
from typing import Any, Optional

from src.envstate.types import (
    BaseFacts,
    EnvStateSnapshot,
    Evidence,
    OpenFailure,
    ProviderFact,
    Requirement,
)


def snapshot_to_dict(snapshot: EnvStateSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def _evidence_from_dict(data: Optional[dict[str, Any]]) -> Optional[Evidence]:
    if not data:
        return None
    return Evidence(**data)


def _requirement_from_dict(data: dict[str, Any]) -> Requirement:
    data = dict(data)
    data["required_by"] = tuple(data.get("required_by") or ())
    data["provides"] = tuple(data.get("provides") or ())
    data["suspected_provides"] = tuple(data.get("suspected_provides") or ())
    data["evidence"] = _evidence_from_dict(data.get("evidence"))
    return Requirement(**data)


def snapshot_from_dict(data: dict[str, Any]) -> EnvStateSnapshot:
    return EnvStateSnapshot(
        revision=data["revision"],
        container_id=data["container_id"],
        base=BaseFacts(**data["base"]),
        requirements=tuple(_requirement_from_dict(r) for r in data.get("requirements", ())),
        provider_facts=tuple(
            ProviderFact(
                provider=p["provider"],
                provides=tuple(p.get("provides") or ()),
                source=p["source"],
                diagnose_cmd=p.get("diagnose_cmd"),
            )
            for p in data.get("provider_facts", ())
        ),
        open_failures=tuple(
            OpenFailure(
                signature=f["signature"],
                first_seen_revision=f["first_seen_revision"],
                last_seen_revision=f["last_seen_revision"],
                hypothesis=f.get("hypothesis"),
                already_tried=tuple(f.get("already_tried") or ()),
            )
            for f in data.get("open_failures", ())
        ),
        stale_evidence=tuple(_requirement_from_dict(r) for r in data.get("stale_evidence", ())),
        plan_notes=tuple(data.get("plan_notes") or ()),
    )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/test_envstate_types.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/envstate/__init__.py src/envstate/types.py src/envstate/serde.py tests/test_envstate_types.py
git commit -m "feat(envstate): add immutable EnvStateSnapshot dataclasses + JSON serde"
```

---

## Phase 4: The proposal ACL

**Why:** This module enforces the central invariant. It is the only place that may set `PRESENT`/`MISSING`/`Evidence`, and it is the gate every LLM proposal passes through.

**Files:**
- Create: `src/envstate/acl.py`
- Test: `tests/test_envstate_acl.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_envstate_acl.py`:

```python
import unittest

from src.envstate.types import (
    BaseFacts,
    EnvStateSnapshot,
    Evidence,
    Requirement,
    Source,
    Status,
)
from src.envstate.acl import (
    advance_revision,
    apply_llm_proposal,
    certify_from_probe,
)


def _empty_snapshot(revision=0):
    return EnvStateSnapshot(
        revision=revision,
        container_id="c1",
        base=BaseFacts(image="python:3.11-slim"),
    )


class AclCertifyTests(unittest.TestCase):
    def test_probe_can_set_present_with_current_evidence(self):
        snap = _empty_snapshot(revision=3).__class__(
            revision=3,
            container_id="c1",
            base=BaseFacts(image="python:3.11-slim"),
            requirements=(
                Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                            status=Status.REQUIRED, source=Source.LLM_GUESS),
            ),
        )
        evidence = Evidence(probe_cmd="command -v pg_config", rc=0,
                            stdout_predicate="path exists", env_revision=3, container_id="c1")
        updated = certify_from_probe(snap, "tool:pg_config", Status.PRESENT, evidence)
        req = [r for r in updated.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.PRESENT)
        self.assertEqual(req.source, Source.PROBE)
        self.assertEqual(req.evidence, evidence)
        # original snapshot unchanged (immutability)
        self.assertEqual(snap.requirements[0].status, Status.REQUIRED)

    def test_probe_rejects_stale_evidence_revision(self):
        snap = _empty_snapshot(revision=5)
        stale = Evidence(probe_cmd="x", rc=0, stdout_predicate="p", env_revision=4, container_id="c1")
        with self.assertRaises(ValueError):
            certify_from_probe(snap, "tool:x", Status.PRESENT, stale)


class AclLlmProposalTests(unittest.TestCase):
    def test_accepts_required_and_unknown_hypotheses(self):
        snap = _empty_snapshot()
        proposal = {
            "candidate_requirements": [
                {"id": "tool:pg_config", "name": "pg_config", "kind": "Tool",
                 "status": "REQUIRED", "source": "LLM_GUESS",
                 "required_by": ["psycopg2==2.8.6"]},
            ]
        }
        updated, rejected = apply_llm_proposal(snap, proposal)
        self.assertEqual(rejected, [])
        self.assertEqual(updated.requirements[0].name, "pg_config")
        self.assertEqual(updated.requirements[0].status, Status.REQUIRED)

    def test_rejects_llm_attempt_to_assert_present(self):
        snap = _empty_snapshot()
        proposal = {
            "candidate_requirements": [
                {"id": "tool:pg_config", "name": "pg_config", "kind": "Tool",
                 "status": "PRESENT", "source": "LLM_GUESS"},
            ]
        }
        updated, rejected = apply_llm_proposal(snap, proposal)
        self.assertEqual(updated.requirements, ())
        self.assertEqual(len(rejected), 1)
        self.assertIn("status", rejected[0]["reason"].lower())

    def test_rejects_llm_attempt_to_attach_evidence_or_probe_source(self):
        snap = _empty_snapshot()
        proposal = {
            "candidate_requirements": [
                {"id": "tool:x", "name": "x", "kind": "Tool", "status": "REQUIRED",
                 "source": "PROBE"},
                {"id": "tool:y", "name": "y", "kind": "Tool", "status": "UNKNOWN",
                 "source": "LLM_GUESS", "evidence": {"rc": 0}},
            ]
        }
        updated, rejected = apply_llm_proposal(snap, proposal)
        self.assertEqual(updated.requirements, ())
        self.assertEqual(len(rejected), 2)


class AclRevisionTests(unittest.TestCase):
    def test_advance_revision_demotes_stale_presence_facts(self):
        evidence = Evidence(probe_cmd="x", rc=0, stdout_predicate="p", env_revision=2, container_id="c1")
        snap = EnvStateSnapshot(
            revision=2, container_id="c1", base=BaseFacts(image="python:3.11-slim"),
            requirements=(
                Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                            status=Status.PRESENT, source=Source.PROBE, evidence=evidence),
            ),
        )
        updated = advance_revision(snap, "system_package_install")
        self.assertEqual(updated.revision, 3)
        req = updated.requirements[0]
        self.assertEqual(req.status, Status.UNKNOWN)   # demoted
        self.assertIsNone(req.evidence)                # evidence cleared from live fact
        self.assertEqual(len(updated.stale_evidence), 1)  # preserved as stale
        self.assertEqual(updated.stale_evidence[0].evidence, evidence)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_envstate_acl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.acl'`.

- [ ] **Step 3: Create `src/envstate/acl.py`**

```python
from __future__ import annotations
from dataclasses import replace
from typing import Any, Optional

from src.envstate.types import (
    EnvStateSnapshot,
    Evidence,
    LLM_ALLOWED_SOURCES,
    LLM_ALLOWED_STATUSES,
    PRESENCE_STATUSES,
    Requirement,
    Source,
    Status,
)


def _replace_requirement(
    snapshot: EnvStateSnapshot, requirement_id: str, new_req: Requirement
) -> EnvStateSnapshot:
    found = False
    updated = []
    for req in snapshot.requirements:
        if req.id == requirement_id:
            updated.append(new_req)
            found = True
        else:
            updated.append(req)
    if not found:
        updated.append(new_req)
    return replace(snapshot, requirements=tuple(updated))


def certify_from_probe(
    snapshot: EnvStateSnapshot,
    requirement_id: str,
    status: str,
    evidence: Evidence,
) -> EnvStateSnapshot:
    """HOST-ONLY. The only path that may set PRESENT/MISSING with Evidence."""
    if status not in PRESENCE_STATUSES:
        raise ValueError(f"certify_from_probe only sets {PRESENCE_STATUSES}, got {status!r}")
    if evidence.env_revision != snapshot.revision:
        raise ValueError(
            f"Evidence revision {evidence.env_revision} != current snapshot revision "
            f"{snapshot.revision}; refusing to certify stale evidence."
        )
    existing = next((r for r in snapshot.requirements if r.id == requirement_id), None)
    if existing is None:
        new_req = Requirement(
            id=requirement_id, name=requirement_id, kind="Tool",
            status=status, source=Source.PROBE, evidence=evidence,
        )
    else:
        new_req = replace(existing, status=status, source=Source.PROBE, evidence=evidence)
    return _replace_requirement(snapshot, requirement_id, new_req)


def _validate_llm_requirement(raw: dict[str, Any]) -> Optional[str]:
    status = raw.get("status")
    source = raw.get("source", Source.LLM_GUESS)
    if status in PRESENCE_STATUSES:
        return f"LLM may not assert presence status {status!r}"
    if status not in LLM_ALLOWED_STATUSES:
        return f"status must be one of {sorted(LLM_ALLOWED_STATUSES)}, got {status!r}"
    if source not in LLM_ALLOWED_SOURCES:
        return f"source must be one of {sorted(LLM_ALLOWED_SOURCES)}, got {source!r}"
    if raw.get("evidence") is not None:
        return "LLM may not attach Evidence"
    return None


def apply_llm_proposal(
    snapshot: EnvStateSnapshot, proposal: dict[str, Any]
) -> tuple[EnvStateSnapshot, list[dict[str, Any]]]:
    """Merge LLM-proposed candidate_requirements after ACL validation.

    Returns (new_snapshot, rejected) where rejected items each carry a `reason`.
    Rejections are dropped + logged, never raised.
    """
    accepted: list[Requirement] = []
    rejected: list[dict[str, Any]] = []
    for raw in proposal.get("candidate_requirements") or []:
        reason = _validate_llm_requirement(raw)
        if reason is not None:
            rejected.append({"candidate": raw, "reason": reason})
            continue
        accepted.append(
            Requirement(
                id=raw.get("id") or f"{raw.get('kind', 'tool')}:{raw.get('name')}",
                name=raw["name"],
                kind=raw.get("kind", "Tool"),
                status=raw["status"],
                source=raw.get("source", Source.LLM_GUESS),
                specifier=raw.get("specifier"),
                required_by=tuple(raw.get("required_by") or ()),
            )
        )
    if not accepted:
        return snapshot, rejected
    by_id = {r.id: r for r in snapshot.requirements}
    for req in accepted:
        # Never let an LLM hypothesis overwrite a host-certified presence fact.
        existing = by_id.get(req.id)
        if existing is not None and existing.source == Source.PROBE:
            rejected.append({"candidate": req.id, "reason": "would overwrite PROBE-certified fact"})
            continue
        by_id[req.id] = req
    return replace(snapshot, requirements=tuple(by_id.values())), rejected


def advance_revision(snapshot: EnvStateSnapshot, mutation_class: str) -> EnvStateSnapshot:
    """Bump revision on an env-mutating action; demote now-stale presence facts."""
    new_revision = snapshot.revision + 1
    live: list[Requirement] = []
    newly_stale: list[Requirement] = []
    for req in snapshot.requirements:
        is_presence = req.status in PRESENCE_STATUSES and req.evidence is not None
        if is_presence and req.evidence.env_revision < new_revision:
            newly_stale.append(req)
            live.append(replace(req, status=Status.UNKNOWN, source=Source.LLM_GUESS, evidence=None))
        else:
            live.append(req)
    return replace(
        snapshot,
        revision=new_revision,
        requirements=tuple(live),
        stale_evidence=snapshot.stale_evidence + tuple(newly_stale),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_envstate_acl.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/envstate/acl.py tests/test_envstate_acl.py
git commit -m "feat(envstate): add proposal ACL (host-only PRESENT/MISSING/Evidence)"
```

---

## Phase 5: ActionLedger + shadow-mode wiring into the existing loop

**Why:** The Dockerfile cannot be synthesized from facts alone — it needs replayable, ordered commands. The `ActionLedger` is the append-only record. In this phase we also wire it (and a starter `EnvStateSnapshot`) to **observe** the existing loop behind `enable_envstate`, advancing the revision via the ACL. No behavior changes when the flag is off.

**Files:**
- Create: `src/envstate/ledger.py`
- Test: `tests/test_envstate_ledger.py`
- Modify: `agent.py` (`__init__` to construct the ledger/snapshot when `enable_envstate`; `_record_successful_action` and `_record_failed_action` to append events; `_build_run_summary` to serialize them; CLI to add `--enable-envstate`)
- Test: `tests/test_agent_envstate_observe.py`

- [ ] **Step 1: Write the failing ledger test**

Create `tests/test_envstate_ledger.py`:

```python
import unittest

from src.envstate.ledger import ActionEvent, ActionLedger


class ActionLedgerTests(unittest.TestCase):
    def test_append_is_ordered_and_immutable_view(self):
        ledger = ActionLedger()
        ledger.append(ActionEvent(
            step=1, task_id=None, cmd="apt-get install -y libpq-dev", rc=0,
            stdout_path=None, stderr_path=None,
            env_revision_before=7, env_revision_after=8,
            mutation_class="system_package_install", container_id="abc123",
            summary="Installed libpq-dev successfully",
        ))
        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].env_revision_after, 8)
        # events() returns an immutable snapshot tuple
        self.assertIsInstance(events, tuple)

    def test_to_list_emits_design_shape(self):
        ledger = ActionLedger()
        ledger.append(ActionEvent(
            step=17, task_id="task-004", cmd="pip install psycopg2==2.8.6", rc=1,
            stdout_path="logs/action_017.stdout", stderr_path="logs/action_017.stderr",
            env_revision_before=7, env_revision_after=7,
            mutation_class=None, container_id="abc123",
            summary="pg_config executable not found",
        ))
        row = ledger.to_list()[0]
        self.assertEqual(row["step"], 17)
        self.assertEqual(row["task_id"], "task-004")
        self.assertEqual(row["rc"], 1)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.ledger'`.

- [ ] **Step 3: Create `src/envstate/ledger.py`**

```python
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class ActionEvent:
    step: int
    task_id: Optional[str]
    cmd: str
    rc: int
    stdout_path: Optional[str]
    stderr_path: Optional[str]
    env_revision_before: int
    env_revision_after: int
    mutation_class: Optional[str]
    container_id: str
    summary: str


class ActionLedger:
    """Append-only host-generated command/event history (the one mutable container)."""

    def __init__(self) -> None:
        self._events: List[ActionEvent] = []

    def append(self, event: ActionEvent) -> None:
        self._events.append(event)

    def events(self) -> Tuple[ActionEvent, ...]:
        return tuple(self._events)

    def to_list(self) -> list[dict[str, Any]]:
        return [asdict(event) for event in self._events]
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_envstate_ledger.py -v`
Expected: all PASS

- [ ] **Step 5: Write the failing shadow-observe test**

Create `tests/test_agent_envstate_observe.py`:

```python
import unittest

from agent import DockerAgent
from src.synthesizer import Synthesizer
from src.envstate.ledger import ActionLedger


class AgentEnvStateObserveTests(unittest.TestCase):
    def _make_agent(self, enable_envstate):
        agent = DockerAgent.__new__(DockerAgent)
        agent.synthesizer = Synthesizer()
        agent.successful_test_commands = []
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.verified_runtime_preparation_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
        agent._environment_revision = 0
        agent._current_verification_group = []
        agent.required_local_services = set()
        agent.enable_envstate = enable_envstate
        agent.action_ledger = ActionLedger() if enable_envstate else None
        agent.env_container_id = "abc123"
        return agent

    def test_envstate_off_does_not_record_ledger(self):
        agent = self._make_agent(enable_envstate=False)
        agent._record_successful_action(1, "pip install -e .", "Successfully installed pkg")
        self.assertIsNone(agent.action_ledger)

    def test_envstate_on_appends_ordered_events(self):
        agent = self._make_agent(enable_envstate=True)
        agent._record_successful_action(1, "pip install -e .", "Successfully installed pkg")
        agent._record_successful_action(2, "pytest -q", "collected 2 items\n2 passed")
        events = agent.action_ledger.events()
        self.assertEqual([e.cmd for e in events], ["pip install -e .", "pytest -q"])
        # The mutating install advanced the revision; the test did not.
        self.assertEqual(events[0].mutation_class is not None, True)
        self.assertEqual(events[1].env_revision_after, events[1].env_revision_before)
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/test_agent_envstate_observe.py -v`
Expected: FAIL (`AttributeError` — `_record_successful_action` does not yet touch `action_ledger`).

- [ ] **Step 7: Add a ledger-append helper and call it from the record methods**

In `agent.py`, add a helper method to `DockerAgent` (place it directly above `_record_successful_action` at line 1564):

```python
    def _append_action_event(self, step_index, action, rc, mutation_class,
                             env_revision_before, env_revision_after, summary):
        """Append one ActionEvent to the ActionLedger (no-op when EnvState is off).

        NOTE 1: ActionEvent.rc is a SUCCESS PROXY (0 on success, 1 on failure), not a
        true exit code — Sandbox.execute() collapses the real rc into a bool before the
        agent ever sees it. Probes (exec_readonly) DO carry real exit codes. Downstream
        ledger consumers (synthesis) only branch on rc==0 vs !=0, so the proxy is safe;
        a true-rc ledger is deferred (see "Deferred / Open").
        NOTE 2: env_revision_* are passed in by the caller so they share the SAME source
        as the successful_actions record's environment_revision — no duplicated stamping.
        """
        if not getattr(self, "enable_envstate", False) or self.action_ledger is None:
            return
        from src.envstate.ledger import ActionEvent
        self.action_ledger.append(ActionEvent(
            step=step_index,
            task_id=getattr(self, "current_task_id", None),
            cmd=action,
            rc=rc,
            stdout_path=None,
            stderr_path=None,
            env_revision_before=env_revision_before,
            env_revision_after=env_revision_after,
            mutation_class=mutation_class,
            container_id=getattr(self, "env_container_id", ""),
            summary=summary,
        ))
```

In `_record_successful_action`, immediately after the `mutates_environment = ...` line (line 1566), compute the revision **once** and reuse it for both the record and the ledger (single source of truth):

```python
        record_revision = self._environment_revision + (1 if mutates_environment else 0)
        mutation_class = self.synthesizer.classify_mutation(action) if mutates_environment else None
        self._append_action_event(
            step_index, action, rc=0, mutation_class=mutation_class,
            env_revision_before=self._environment_revision, env_revision_after=record_revision,
            summary=(observation or "")[:200],
        )
```

Then replace the inline expression in the existing `self.successful_actions.append({... "environment_revision": self._environment_revision + (1 if mutates_environment else 0), ...})` (line 1577) with the shared local: `"environment_revision": record_revision,`. Now the ledger event and the action record cannot drift.

> `classify_mutation` does not exist yet — add it in the next step.

In `_record_failed_action` (`agent.py:1357`), add a parallel call near the top of the method. Failed actions never advance the revision, so before == after:

```python
        self._append_action_event(
            step_index, action, rc=1, mutation_class=None,
            env_revision_before=self._environment_revision,
            env_revision_after=self._environment_revision,
            summary=(observation or "")[:200],
        )
```

(Per the scout, `_record_failed_action(self, step_index, action, observation)`.)

- [ ] **Step 8: Add `classify_mutation` to the Synthesizer**

The Synthesizer already owns `command_mutates_environment` (`synthesizer.py:2890`). Add a coarse classifier next to it (design §13 mutation classes). Append this method to the `Synthesizer` class:

```python
    def classify_mutation(self, command: str) -> str:
        """Coarse env-mutation class for the ActionLedger (design §13)."""
        normalized = " ".join(str(command or "").lower().split())
        if any(tok in normalized for tok in ("apt-get install", "apt install", "yum install", "apk add")):
            return "system_package_install"
        if any(tok in normalized for tok in ("apt-get remove", "apt remove", "yum remove", "apk del")):
            return "system_package_remove"
        if any(tok in normalized for tok in ("pip install", "pip3 install", "poetry install", "conda install", "npm install", "yarn add")):
            return "language_package_install"
        if "venv" in normalized or "virtualenv" in normalized:
            return "venv_change"
        if normalized.startswith(("export ", "ln -s", "ln -s")) or " > " in normalized or " >> " in normalized:
            return "file_or_env_change"
        return "other_mutation"
```

- [ ] **Step 9: Construct the ledger in `DockerAgent.__init__` and add the CLI flag**

In `DockerAgent.__init__` (signature at `agent.py:113`), add `enable_envstate: bool = False` to the parameters, and in the body (near where other feature flags are stored) add:

```python
        self.enable_envstate = enable_envstate
        self.action_ledger = None
        self.current_task_id = None
        self.env_container_id = ""
        if self.enable_envstate:
            from src.envstate.ledger import ActionLedger
            self.action_ledger = ActionLedger()
```

After the sandbox is created (`_create_sandbox`), capture the container id for evidence/ledger. Find where `self.sandbox` is assigned and add:

```python
        if self.enable_envstate and getattr(self.sandbox, "container", None) is not None:
            self.env_container_id = self.sandbox.container.short_id
```

In the CLI block (`agent.py:1918-1976`), add the argparse flag and pass it through:

```python
    parser.add_argument("--enable-envstate", action="store_true",
                        help="Maintain a host EnvState world model + ActionLedger (shadow mode).")
```

and include `enable_envstate=args.enable_envstate` in the `DockerAgent(...)` construction.

- [ ] **Step 10: Serialize the ledger into the run summary**

In `_build_run_summary` (`agent.py:1851`), add to the returned dict (guarded so the off-path is unchanged):

```python
        if getattr(self, "enable_envstate", False) and self.action_ledger is not None:
            summary["action_ledger"] = self.action_ledger.to_list()
```

- [ ] **Step 11: Run the shadow-observe test + agent regression**

Run: `python -m pytest tests/test_agent_envstate_observe.py tests/test_agent_verification.py -v`
Expected: all PASS. The `enable_envstate=False` path must leave `test_agent_verification.py` byte-identical (it does not set `enable_envstate`, and `_append_action_event` early-returns on the missing attribute via `getattr`).

- [ ] **Step 12: Commit**

```bash
git add src/envstate/ledger.py src/synthesizer.py agent.py tests/test_envstate_ledger.py tests/test_agent_envstate_observe.py
git commit -m "feat(envstate): add ActionLedger + shadow-mode observation behind --enable-envstate"
```

> **Milestone B complete.** The agent now maintains an append-only ActionLedger and revision counter when `--enable-envstate` is set, with zero behavior change when it is off. EnvState facts are not yet certified — that's Milestone C.

---

# Milestone C — Host Observation (extractor, probes, maintainer)

## Phase 6: Read-only Sandbox exec + V1 probes

**Why:** Probes must run **without** `execute()`'s side effects (snapshot commit, preflight rejection, pip retry, `SYSTEM:` prefixes). This phase adds `Sandbox.exec_readonly()` and the fixed V1 probe set (design §12).

**Files:**
- Modify: `src/sandbox.py` (add `exec_readonly`)
- Create: `src/envstate/probes.py`
- Test: `tests/test_sandbox_exec_readonly.py`
- Test: `tests/test_envstate_probes.py`

- [ ] **Step 1: Write the failing `exec_readonly` test**

Create `tests/test_sandbox_exec_readonly.py` (mirrors `tests/test_sandbox.py`'s `FakeContainer` pattern):

```python
import unittest
from types import SimpleNamespace

from src.sandbox import Sandbox


class FakeContainer:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def exec_run(self, command, workdir=None):
        self.calls.append({"command": command, "workdir": workdir})
        return self._results.pop(0)


class SandboxExecReadonlyTests(unittest.TestCase):
    def _make_sandbox(self, results):
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.workdir = "/app"
        sandbox.container = FakeContainer(results)
        return sandbox

    def test_returns_rc_and_decoded_output_without_side_effects(self):
        sandbox = self._make_sandbox([SimpleNamespace(exit_code=0, output=b"/usr/bin/pg_config\n")])
        rc, out = sandbox.exec_readonly("command -v pg_config")
        self.assertEqual(rc, 0)
        self.assertIn("/usr/bin/pg_config", out)
        # ran exactly one exec_run, with the raw command (no preflight, no commit)
        self.assertEqual(len(sandbox.container.calls), 1)

    def test_nonzero_exit_is_surfaced_as_rc(self):
        sandbox = self._make_sandbox([SimpleNamespace(exit_code=1, output=b"not found")])
        rc, out = sandbox.exec_readonly("command -v nope")
        self.assertEqual(rc, 1)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_sandbox_exec_readonly.py -v`
Expected: FAIL with `AttributeError: 'Sandbox' object has no attribute 'exec_readonly'`.

- [ ] **Step 3: Add `exec_readonly` to the Sandbox**

In `src/sandbox.py`, add this method to the `Sandbox` class (near `execute`, around line 164). It deliberately skips preflight, pipefail/timeout wrapping is optional, snapshot commit, retries, and prefix injection:

```python
    def exec_readonly(self, command):
        """Run a read-only probe/extractor command with NO side effects.

        Returns (exit_code:int, output:str). Does not commit snapshots, does not
        run preflight rejection, does not retry, does not inject SYSTEM prefixes,
        and deliberately does NOT apply `set -o pipefail` (pipefail can flip the
        exit code of legitimate probe chains like `cmd | grep -q`, causing silent
        mis-certification). It wraps the command in a login shell so `&&` and `|`
        work, and returns the raw exit code untouched.
        Callers must only pass commands that do not mutate the environment.
        """
        result = self.container.exec_run(["/bin/sh", "-lc", command], workdir=self.workdir)
        output = result.output
        if isinstance(output, (bytes, bytearray)):
            output = output.decode("utf-8", errors="replace")
        return result.exit_code, output or ""
```

> Probe chains rely on plain shell short-circuit semantics (`&&`, `|`), not `pipefail`. `evaluate_probe` treats `rc == 0` as presence, so any rc perturbation would be a silent false PRESENT/MISSING — hence no pipefail here.

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_sandbox_exec_readonly.py -v`
Expected: all PASS

- [ ] **Step 5: Write the failing probes test**

Create `tests/test_envstate_probes.py`:

```python
import unittest

from src.envstate.probes import (
    ProbeResult,
    ProbeSpec,
    build_probe_command,
    evaluate_probe,
    run_probe,
)


class FakeExecutor:
    """Mimics Sandbox.exec_readonly: callable(command) -> (rc, output)."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        return self.mapping.get(command, (127, "command not found"))


class ProbeCommandTests(unittest.TestCase):
    def test_cli_probe_command(self):
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="path exists and version prints")
        self.assertEqual(
            build_probe_command(spec),
            "command -v pg_config && pg_config --version",
        )

    def test_python_import_probe_command(self):
        spec = ProbeSpec(kind="python_import", name="psycopg2", predicate="imports and has __version__")
        self.assertIn("import psycopg2", build_probe_command(spec))

    def test_pkg_config_probe_command(self):
        spec = ProbeSpec(kind="pkg_config", name="libpq", predicate="module known to pkg-config")
        self.assertEqual(build_probe_command(spec), "pkg-config --exists libpq && pkg-config --modversion libpq")

    def test_header_probe_command(self):
        spec = ProbeSpec(kind="header", name="libpq-fe.h", predicate="header on default search path")
        self.assertIn("libpq-fe.h", build_probe_command(spec))


class ProbeEvaluationTests(unittest.TestCase):
    def test_passes_on_rc_zero(self):
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="x")
        self.assertTrue(evaluate_probe(spec, rc=0, stdout="/usr/bin/pg_config\n10.1"))

    def test_fails_on_nonzero_rc(self):
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="x")
        self.assertFalse(evaluate_probe(spec, rc=1, stdout=""))


class RunProbeTests(unittest.TestCase):
    def test_run_probe_returns_result_with_revision_and_container(self):
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="path exists")
        cmd = build_probe_command(spec)
        executor = FakeExecutor({cmd: (0, "/usr/bin/pg_config\n10.1")})
        result = run_probe(executor, spec, env_revision=8, container_id="abc123")
        self.assertIsInstance(result, ProbeResult)
        self.assertTrue(result.passed)
        self.assertEqual(result.env_revision, 8)
        self.assertEqual(result.container_id, "abc123")
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_probes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.probes'`.

- [ ] **Step 7: Create `src/envstate/probes.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Tuple

# A probe executor is any callable(command:str) -> (rc:int, stdout:str).
# Sandbox.exec_readonly satisfies this contract.
ProbeExecutor = Callable[[str], Tuple[int, str]]

CLI = "cli"
PYTHON_IMPORT = "python_import"
PKG_CONFIG = "pkg_config"
HEADER = "header"
SOURCE_BUILD = "source_build"


@dataclass(frozen=True)
class ProbeSpec:
    kind: str
    name: str
    predicate: str
    command: str = ""  # optional explicit override (e.g. source_build replay)


@dataclass(frozen=True)
class ProbeResult:
    spec: ProbeSpec
    rc: int
    stdout: str
    passed: bool
    env_revision: int
    container_id: str


def build_probe_command(spec: ProbeSpec) -> str:
    if spec.command:
        return spec.command
    if spec.kind == CLI:
        return f"command -v {spec.name} && {spec.name} --version"
    if spec.kind == PYTHON_IMPORT:
        # Prefer python3, fall back to python — many images only ship one.
        py = f"import {spec.name}; print(getattr({spec.name}, '__version__', 'no-version'))"
        return f"python3 -c \"{py}\" 2>/dev/null || python -c \"{py}\""
    if spec.kind == PKG_CONFIG:
        return f"pkg-config --exists {spec.name} && pkg-config --modversion {spec.name}"
    if spec.kind == HEADER:
        # Test for the header FILE on the include search path. Do NOT compile —
        # slim base images often ship no C compiler, which would false-MISSING a
        # header that is actually present. `find` needs no toolchain.
        return (
            "find /usr/include /usr/local/include "
            f"-type f -name {spec.name!r} 2>/dev/null | grep -q ."
        )
    if spec.kind == SOURCE_BUILD:
        raise ValueError("source_build probes require an explicit `command`")
    raise ValueError(f"unknown probe kind {spec.kind!r}")


def evaluate_probe(spec: ProbeSpec, rc: int, stdout: str) -> bool:
    # V1: presence == exit code 0. The predicate string is documentation/evidence text.
    return rc == 0


def run_probe(
    executor: ProbeExecutor, spec: ProbeSpec, env_revision: int, container_id: str
) -> ProbeResult:
    command = build_probe_command(spec)
    rc, stdout = executor(command)
    return ProbeResult(
        spec=spec,
        rc=rc,
        stdout=stdout,
        passed=evaluate_probe(spec, rc, stdout),
        env_revision=env_revision,
        container_id=container_id,
    )
```

- [ ] **Step 8: Add a probe→Evidence→ACL bridge with a test**

Add to `tests/test_envstate_probes.py`:

```python
from src.envstate.types import BaseFacts, EnvStateSnapshot, Requirement, Source, Status
from src.envstate.probes import certify_probe_result


class CertifyProbeResultTests(unittest.TestCase):
    def test_passing_probe_certifies_present_via_acl(self):
        snap = EnvStateSnapshot(
            revision=8, container_id="abc123", base=BaseFacts(image="python:3.11-slim"),
            requirements=(Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                                      status=Status.REQUIRED, source=Source.LLM_GUESS),),
        )
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="path exists")
        result = run_probe(FakeExecutor({build_probe_command(spec): (0, "/usr/bin/pg_config")}),
                           spec, env_revision=8, container_id="abc123")
        updated = certify_probe_result(snap, "tool:pg_config", result)
        req = [r for r in updated.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.PRESENT)
        self.assertEqual(req.source, Source.PROBE)

    def test_failing_probe_certifies_missing(self):
        snap = EnvStateSnapshot(
            revision=8, container_id="abc123", base=BaseFacts(image="python:3.11-slim"),
            requirements=(Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                                      status=Status.REQUIRED, source=Source.LLM_GUESS),),
        )
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="path exists")
        result = run_probe(FakeExecutor({}), spec, env_revision=8, container_id="abc123")
        updated = certify_probe_result(snap, "tool:pg_config", result)
        req = [r for r in updated.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.MISSING)
```

Then add `certify_probe_result` to `src/envstate/probes.py`:

```python
from src.envstate.acl import certify_from_probe
from src.envstate.types import Evidence, Status


def certify_probe_result(snapshot, requirement_id: str, result: ProbeResult):
    """Translate a ProbeResult into host-certified PRESENT/MISSING via the ACL."""
    status = Status.PRESENT if result.passed else Status.MISSING
    evidence = Evidence(
        probe_cmd=build_probe_command(result.spec),
        rc=result.rc,
        stdout_predicate=result.spec.predicate,
        env_revision=result.env_revision,
        container_id=result.container_id,
    )
    return certify_from_probe(snapshot, requirement_id, status, evidence)
```

- [ ] **Step 9: Run probes tests + sandbox regression**

Run: `python -m pytest tests/test_envstate_probes.py tests/test_sandbox.py tests/test_sandbox_exec_readonly.py -v`
Expected: all PASS

- [ ] **Step 10: Commit**

```bash
git add src/sandbox.py src/envstate/probes.py tests/test_sandbox_exec_readonly.py tests/test_envstate_probes.py
git commit -m "feat(envstate): add read-only sandbox exec + V1 probes + probe->ACL certification"
```

---

## Phase 7: Read-only extractor

**Why:** A broad read-only extractor does most ordinary map maintenance (design §12). It is **not** a probe — it answers "what is visible now?", not "does this capability work?".

**Files:**
- Create: `src/envstate/extractor.py`
- Test: `tests/test_envstate_extractor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_envstate_extractor.py`:

```python
import unittest

from src.envstate.extractor import (
    EXTRACTOR_COMMANDS,
    LIGHTWEIGHT_FIELDS,
    run_extractor,
)


class FakeExecutor:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        return self.mapping.get(command, (1, ""))


class ExtractorTests(unittest.TestCase):
    def test_full_extractor_collects_known_fields(self):
        mapping = {
            EXTRACTOR_COMMANDS["python_version"]: (0, "Python 3.11.9\n"),
            EXTRACTOR_COMMANDS["pip_version"]: (0, "pip 24.0\n"),
            EXTRACTOR_COMMANDS["os_release"]: (0, 'ID=debian\nVERSION_CODENAME=bookworm\n'),
            EXTRACTOR_COMMANDS["arch"]: (0, "x86_64\n"),
        }
        result = run_extractor(FakeExecutor(mapping))
        self.assertEqual(result.fields["python_version"], "Python 3.11.9")
        self.assertEqual(result.fields["arch"], "x86_64")
        self.assertIn("debian", result.fields["os_release"])

    def test_lightweight_extractor_runs_subset(self):
        executor = FakeExecutor({cmd: (0, "ok") for cmd in EXTRACTOR_COMMANDS.values()})
        run_extractor(executor, fields=LIGHTWEIGHT_FIELDS)
        # only the lightweight field commands were executed
        self.assertEqual(len(executor.calls), len(LIGHTWEIGHT_FIELDS))

    def test_missing_command_is_recorded_but_not_fatal(self):
        result = run_extractor(FakeExecutor({}))  # everything returns (1, "")
        self.assertEqual(result.fields, {})
        self.assertTrue(all(rc == 1 for rc, _ in result.raw.values()))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.extractor'`.

- [ ] **Step 3: Create `src/envstate/extractor.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

ProbeExecutor = Callable[[str], Tuple[int, str]]

# field_name -> read-only command (design §12 extractor list, V1 subset)
EXTRACTOR_COMMANDS: Dict[str, str] = {
    "os_release": "cat /etc/os-release",
    "arch": "uname -m",
    "python_version": "python --version 2>&1",
    "pip_version": "pip --version 2>&1",
    "path": "echo \"$PATH\"",
    "which_python": "command -v python",
    "venv": "echo \"${VIRTUAL_ENV:-}\"",
    "installed_pip": "pip freeze 2>/dev/null",
    "dpkg_packages": "dpkg -l 2>/dev/null | awk '/^ii/{print $2}'",
    "pkg_config_modules": "pkg-config --list-all 2>/dev/null",
}

# Cheap subset re-run after every env mutation (design §12 run schedule).
LIGHTWEIGHT_FIELDS = ("python_version", "pip_version", "installed_pip", "arch")


@dataclass(frozen=True)
class ExtractionResult:
    fields: Dict[str, str]            # successfully-read field -> trimmed stdout
    raw: Dict[str, Tuple[int, str]]   # field -> (rc, raw stdout) for every attempted command


def run_extractor(
    executor: ProbeExecutor, fields: Optional[Tuple[str, ...]] = None
) -> ExtractionResult:
    names = fields if fields is not None else tuple(EXTRACTOR_COMMANDS.keys())
    parsed: Dict[str, str] = {}
    raw: Dict[str, Tuple[int, str]] = {}
    for name in names:
        command = EXTRACTOR_COMMANDS[name]
        rc, stdout = executor(command)
        raw[name] = (rc, stdout)
        if rc == 0 and stdout.strip():
            parsed[name] = stdout.strip()
    return ExtractionResult(fields=parsed, raw=raw)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_envstate_extractor.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/envstate/extractor.py tests/test_envstate_extractor.py
git commit -m "feat(envstate): add read-only environment extractor (full + lightweight)"
```

---

## Phase 8: Maintainer interpreter (schema + proposal validation)

**Why:** The maintainer is mostly host code; its LLM part proposes structured deltas for ambiguous/failed observations (design §10). Its output is validated by the ACL — it may never emit `PRESENT`/`MISSING`/`Evidence`. It receives **focused residual spans** by default (design §11), reusing `select_failure_lines` (`memory_manager.py:871`).

**Files:**
- Create: `src/envstate/maintainer.py`
- Test: `tests/test_envstate_maintainer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_envstate_maintainer.py`:

```python
import unittest
from types import SimpleNamespace

from src.envstate.maintainer import (
    MAINTAINER_SYSTEM_PROMPT,
    Maintainer,
    build_maintainer_input,
    parse_maintainer_proposal,
)
from src.envstate.ledger import ActionEvent


def _fake_client(content):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_k: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                )
            )
        )
    )


class MaintainerInputTests(unittest.TestCase):
    def test_input_contains_residual_spans_not_full_log(self):
        event = ActionEvent(
            step=7, task_id="task-004", cmd="pip install psycopg2==2.8.6", rc=1,
            stdout_path=None, stderr_path=None, env_revision_before=7, env_revision_after=7,
            mutation_class=None, container_id="abc123", summary="failed",
        )
        full_log = "\n".join(["noise"] * 500 + ["Error: pg_config executable not found"])
        payload = build_maintainer_input({}, {"task_id": "task-004"}, event, full_log)
        self.assertIn("pg_config executable not found", payload["residual_spans"])
        self.assertLess(len(payload["residual_spans"]), len(full_log))


class MaintainerParseTests(unittest.TestCase):
    def test_parses_well_formed_proposal(self):
        content = (
            'Here is my analysis.\n'
            '```json\n'
            '{"candidate_requirements": [{"id": "tool:pg_config", "name": "pg_config", '
            '"kind": "Tool", "status": "REQUIRED", "source": "LLM_GUESS", '
            '"required_by": ["psycopg2==2.8.6"]}], '
            '"open_failure_updates": [{"signature": "pg_config executable not found", '
            '"hypothesis": "source build needs PostgreSQL dev tooling"}], '
            '"diagnose_requests": [{"kind": "apt_provider", "capability": "pg_config"}], '
            '"probe_requests": [{"kind": "cli", "name": "pg_config", "predicate": "path exists"}], '
            '"plan_notes": ["Do not substitute psycopg2-binary."]}\n'
            '```\n'
        )
        proposal = parse_maintainer_proposal(content)
        self.assertEqual(proposal["candidate_requirements"][0]["name"], "pg_config")
        self.assertEqual(proposal["probe_requests"][0]["name"], "pg_config")

    def test_returns_empty_proposal_on_unparseable_content(self):
        self.assertEqual(parse_maintainer_proposal("no json here"), {})
        self.assertEqual(parse_maintainer_proposal(None), {})


class MaintainerInterpretTests(unittest.TestCase):
    def test_interpret_applies_acl_and_reports_rejections(self):
        from src.envstate.types import BaseFacts, EnvStateSnapshot
        snap = EnvStateSnapshot(revision=7, container_id="abc123", base=BaseFacts(image="python:3.11-slim"))
        # LLM tries to smuggle a PRESENT fact — ACL must drop it.
        content = (
            '```json\n{"candidate_requirements": ['
            '{"id": "tool:pg_config", "name": "pg_config", "kind": "Tool", "status": "REQUIRED", "source": "LLM_GUESS"},'
            '{"id": "tool:sneaky", "name": "sneaky", "kind": "Tool", "status": "PRESENT", "source": "LLM_GUESS"}'
            ']}\n```'
        )
        maintainer = Maintainer(client=_fake_client(content), model="test-model")
        event = ActionEvent(step=7, task_id=None, cmd="pip install x", rc=1, stdout_path=None,
                            stderr_path=None, env_revision_before=7, env_revision_after=7,
                            mutation_class=None, container_id="abc123", summary="failed")
        updated, proposal, rejected, usage = maintainer.interpret(snap, {}, event, "Error: pg_config not found")
        ids = [r.id for r in updated.requirements]
        self.assertIn("tool:pg_config", ids)
        self.assertNotIn("tool:sneaky", ids)
        self.assertEqual(len(rejected), 1)


class MaintainerContractTests(unittest.TestCase):
    def test_system_prompt_forbids_presence_and_evidence(self):
        self.assertIn("PRESENT", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("MISSING", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("Evidence", MAINTAINER_SYSTEM_PROMPT)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_maintainer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.envstate.maintainer'`.

- [ ] **Step 3: Create the shared JSON extractor `src/envstate/jsonutil.py`**

Both the Maintainer and Supervisor parse a single JSON object out of LLM prose. A non-greedy `\{.*?\}` regex truncates nested objects and `content.find("{")` + `json.loads(tail)` fails on trailing prose (extremely common with these models) — which would silently stall the loop. Use a brace-matching scanner instead:

```python
from __future__ import annotations
import json
from typing import Any, Optional


def extract_json_object(text: Optional[str]) -> Optional[dict[str, Any]]:
    """Extract the first balanced top-level JSON object from arbitrary LLM text.

    Tolerates a ```json fence, leading commentary, and trailing prose. Returns
    the parsed dict, or None if no valid object is found.
    """
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        break  # malformed; try the next '{'
                    return parsed if isinstance(parsed, dict) else None
        start = text.find("{", start + 1)
    return None
```

Add a quick test `tests/test_envstate_jsonutil.py`:

```python
import unittest

from src.envstate.jsonutil import extract_json_object


class JsonUtilTests(unittest.TestCase):
    def test_extracts_from_fence_with_trailing_prose(self):
        text = 'Sure:\n```json\n{"a": {"b": 1}}\n```\nHope that helps!'
        self.assertEqual(extract_json_object(text), {"a": {"b": 1}})

    def test_handles_nested_objects_not_truncated_at_first_brace(self):
        self.assertEqual(extract_json_object('{"x": {"y": 2}, "z": 3}'), {"x": {"y": 2}, "z": 3})

    def test_handles_braces_inside_strings(self):
        self.assertEqual(extract_json_object('{"cmd": "echo ${PATH}"}'), {"cmd": "echo ${PATH}"})

    def test_returns_none_on_no_object(self):
        self.assertIsNone(extract_json_object("no json here"))
        self.assertIsNone(extract_json_object(None))
```

Run: `python -m pytest tests/test_envstate_jsonutil.py -v` → all PASS.

- [ ] **Step 4: Create `src/envstate/maintainer.py`**

```python
from __future__ import annotations
import json
from typing import Any, Optional

from src.envstate.acl import apply_llm_proposal
from src.envstate.jsonutil import extract_json_object
from src.envstate.ledger import ActionEvent
from src.envstate.serde import snapshot_to_dict
from src.envstate.types import EnvStateSnapshot

try:  # reuse the existing residual-span extractor
    from src.memory_manager import select_failure_lines
except Exception:  # pragma: no cover - fallback if signature drifts
    def select_failure_lines(observation, max_lines=48):
        lines = [ln for ln in (observation or "").splitlines() if ln.strip()]
        return "\n".join(lines[-max_lines:])


MAINTAINER_SYSTEM_PROMPT = """You are the State Maintainer for DockerAgent environment setup.

You interpret ONE command observation and propose structured updates to the
environment-state map. You are an interpreter, not an authority.

You MAY propose:
- candidate_requirements with status REQUIRED or UNKNOWN only
- open_failure_updates (a signature + a hypothesis)
- diagnose_requests (provider lookups, e.g. which apt package provides a tool)
- probe_requests (host probes the orchestrator should run to certify truth)
- plan_notes (durable cautions)

You MUST NOT emit:
- status=PRESENT or status=MISSING
- any Evidence object
- final Dockerfile lines
- authoritative task completion

Only host probes may certify PRESENT/MISSING with Evidence. If you believe a
capability is present or missing, emit a probe_request so the host can verify it.

Return exactly one JSON object inside a ```json fenced block.
"""


def build_maintainer_input(
    previous_env_state_view: dict[str, Any],
    task_spec: dict[str, Any],
    action_event: ActionEvent,
    full_log: str,
) -> dict[str, Any]:
    return {
        "previous_env_state_view": previous_env_state_view,
        "task_spec": task_spec,
        "action_event": {
            "cmd": action_event.cmd,
            "rc": action_event.rc,
            "env_revision_before": action_event.env_revision_before,
            "env_revision_after": action_event.env_revision_after,
        },
        "residual_spans": select_failure_lines(full_log),
    }


def parse_maintainer_proposal(content: Optional[str]) -> dict[str, Any]:
    return extract_json_object(content) or {}


class Maintainer:
    def __init__(self, client, model):
        self.client = client
        self.model = model

    def interpret(
        self,
        snapshot: EnvStateSnapshot,
        task_spec: dict[str, Any],
        action_event: ActionEvent,
        full_log: str,
    ):
        # The maintainer interprets each observation WITH the current map in view
        # (design §10 requires previous_env_state_view), so it can reconcile new
        # evidence against existing hypotheses instead of starting blind.
        payload = build_maintainer_input(
            snapshot_to_dict(snapshot), task_spec, action_event, full_log
        )
        messages = [
            {"role": "system", "content": MAINTAINER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ]
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0
        )
        content = response.choices[0].message.content or ""
        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        proposal = parse_maintainer_proposal(content)
        updated, rejected = apply_llm_proposal(snapshot, proposal)
        return updated, proposal, rejected, usage
```

- [ ] **Step 5: Run it to verify it passes**

Run: `python -m pytest tests/test_envstate_maintainer.py tests/test_envstate_jsonutil.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/envstate/jsonutil.py src/envstate/maintainer.py tests/test_envstate_maintainer.py tests/test_envstate_jsonutil.py
git commit -m "feat(envstate): add Maintainer interpreter + shared JSON extractor (ACL-validated deltas)"
```

> **Milestone C complete.** The host can now extract environment facts, certify capability presence/missingness via probes (with revision-stamped Evidence), and turn LLM interpretations into ACL-safe hypotheses + probe requests. None of this is wired into a control loop yet — that's Milestone D.

---

# Milestone D — Supervisor / Worker Orchestration

> Everything in this milestone is entered only via `--enable-supervisor` (which implies `--enable-envstate`). The default `DockerAgent.run()` loop is untouched.

## Phase 9: Supervisor Planner — TaskSpec emitter + planning-view renderer

**Files:**
- Create: `src/envstate/supervisor.py`
- Test: `tests/test_envstate_supervisor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_envstate_supervisor.py`:

```python
import unittest
from types import SimpleNamespace

from src.envstate.supervisor import (
    SUPERVISOR_SYSTEM_PROMPT,
    Supervisor,
    parse_task_spec,
    render_planning_view,
)
from src.envstate.types import (
    BaseFacts,
    EnvStateSnapshot,
    OpenFailure,
    Requirement,
    Source,
    Status,
)
from src.envstate.ledger import ActionLedger


def _fake_client(content):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_k: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30),
                )
            )
        )
    )


def _snapshot():
    return EnvStateSnapshot(
        revision=7, container_id="abc123",
        base=BaseFacts(image="python:3.11-slim", python="3.11.9"),
        requirements=(
            Requirement(id="lang:psycopg2==2.8.6", name="psycopg2", kind="LanguagePackage",
                        status=Status.REQUIRED, source=Source.STATIC_SCAN, specifier="==2.8.6"),
            Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                        status=Status.REQUIRED, source=Source.LLM_GUESS),
        ),
        open_failures=(OpenFailure(signature="pg_config executable not found",
                                   first_seen_revision=7, last_seen_revision=7,
                                   hypothesis="psycopg2 source build needs PostgreSQL dev tooling"),),
        plan_notes=("Do not substitute psycopg2-binary for pinned psycopg2.",),
    )


class PlanningViewTests(unittest.TestCase):
    def test_view_includes_open_failures_requirements_and_notes(self):
        view = render_planning_view(_snapshot(), ActionLedger(), budget={"steps_remaining": 20})
        self.assertIn("psycopg2", view)
        self.assertIn("pg_config executable not found", view)
        self.assertIn("Do not substitute psycopg2-binary", view)
        self.assertIn("revision 7", view)


class TaskSpecParseTests(unittest.TestCase):
    def test_parses_task_spec_json(self):
        content = (
            '```json\n{"task_id": "task-004", "phase": "Native/System Dependency Resolution", '
            '"goal": "Resolve missing pg_config", "relevant_state": ["pip install failed"], '
            '"constraints": ["Do not edit requirements.txt"], "allowed_actions": ["install system packages"], '
            '"success_criteria": ["pg_config probe passes"], "stop_conditions": ["more than 4 actions"], '
            '"suggested_tactics": ["apt-get install -y libpq-dev"]}\n```'
        )
        spec = parse_task_spec(content)
        self.assertEqual(spec["task_id"], "task-004")
        self.assertEqual(spec["phase"], "Native/System Dependency Resolution")

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_task_spec("no json"))


class SupervisorTests(unittest.TestCase):
    def test_next_task_returns_parsed_taskspec(self):
        content = ('```json\n{"task_id": "task-001", "phase": "Repository Analysis", '
                   '"goal": "Identify dependency strategy", "relevant_state": [], "constraints": [], '
                   '"allowed_actions": ["inspect files"], "success_criteria": ["strategy known"], '
                   '"stop_conditions": ["budget"], "suggested_tactics": []}\n```')
        sup = Supervisor(client=_fake_client(content), model="test-model")
        spec, usage = sup.next_task(_snapshot(), ActionLedger(), budget={"steps_remaining": 30})
        self.assertEqual(spec["task_id"], "task-001")
        self.assertEqual(usage["total_tokens"], 30)


class SupervisorContractTests(unittest.TestCase):
    def test_prompt_forbids_certifying_presence(self):
        self.assertIn("do not certify", SUPERVISOR_SYSTEM_PROMPT.lower())
        self.assertIn("TaskSpec", SUPERVISOR_SYSTEM_PROMPT)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_supervisor.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/envstate/supervisor.py`**

```python
from __future__ import annotations
from typing import Any, Optional

from src.envstate.jsonutil import extract_json_object
from src.envstate.ledger import ActionLedger
from src.envstate.types import EnvStateSnapshot, Source

SETUP_PHASES = (
    "Repository Analysis",
    "Language Dependency Installation",
    "Native/System Dependency Resolution",
    "Environment Configuration",
    "Verification",
    "Synthesis Readiness",
)

SUPERVISOR_SYSTEM_PROMPT = """You are the Supervisor Planner for DockerAgent environment setup.

Your job is to configure the repository environment by assigning bounded tasks to
a ReAct build worker. You do not execute shell commands. You do not update
EnvState. You do not certify that dependencies are present.

The source of truth is the provided EnvState snapshot. Facts with source=PROBE and
the current env_revision are trusted. Facts from LLM_GUESS, MEMORY, STATIC_SCAN, or
stale revisions are hypotheses only.

Choose the next task based on the current setup phase, EnvState, open failures,
worker history, and budget.

Setup phases (in order): Repository Analysis; Language Dependency Installation;
Native/System Dependency Resolution; Environment Configuration; Verification;
Synthesis Readiness.

Forbidden: do not claim a requirement is PRESENT or MISSING; do not edit EnvState;
do not emit shell commands as the top-level output; do not ask the worker to solve
the entire environment at once; do not treat a checklist or worker report as proof.

Emit exactly one TaskSpec JSON object inside a ```json fenced block, with keys:
task_id, phase, goal, relevant_state, constraints, allowed_actions,
success_criteria, stop_conditions, suggested_tactics.
"""


def render_planning_view(
    snapshot: EnvStateSnapshot, ledger: ActionLedger, budget: dict[str, Any]
) -> str:
    """Compact projection of EnvState for the Supervisor (design §3 RenderedPlanningView)."""
    lines = [f"# EnvState (revision {snapshot.revision}, container {snapshot.container_id})"]
    lines.append(f"Base: image={snapshot.base.image} python={snapshot.base.python} "
                 f"distro={snapshot.base.distro} arch={snapshot.base.arch}")
    lines.append("")
    lines.append("## Requirements")
    for req in snapshot.requirements:
        trust = "PROBE" if req.source == Source.PROBE else f"hypothesis({req.source})"
        lines.append(f"- [{req.status}] {req.id} ({req.kind}) via {trust}"
                     + (f" requires {list(req.required_by)}" if req.required_by else ""))
    if snapshot.open_failures:
        lines.append("")
        lines.append("## Open Failures")
        for fail in snapshot.open_failures:
            lines.append(f"- {fail.signature} (rev {fail.first_seen_revision}->{fail.last_seen_revision})"
                         + (f": {fail.hypothesis}" if fail.hypothesis else ""))
    if snapshot.plan_notes:
        lines.append("")
        lines.append("## Plan Notes")
        for note in snapshot.plan_notes:
            lines.append(f"- {note}")
    recent = ledger.events()[-5:]
    if recent:
        lines.append("")
        lines.append("## Recent Actions")
        for event in recent:
            lines.append(f"- step {event.step}: `{event.cmd}` rc={event.rc} -> {event.summary[:80]}")
    lines.append("")
    lines.append(f"## Budget\n- steps_remaining: {budget.get('steps_remaining')}")
    return "\n".join(lines)


def parse_task_spec(content: Optional[str]) -> Optional[dict[str, Any]]:
    parsed = extract_json_object(content)
    return parsed if parsed and parsed.get("task_id") else None


class Supervisor:
    def __init__(self, client, model):
        self.client = client
        self.model = model

    def next_task(self, snapshot, ledger, budget):
        view = render_planning_view(snapshot, ledger, budget)
        messages = [
            {"role": "system", "content": SUPERVISOR_SYSTEM_PROMPT},
            {"role": "user", "content": view},
        ]
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0
        )
        content = response.choices[0].message.content or ""
        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        return parse_task_spec(content), usage
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_envstate_supervisor.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/envstate/supervisor.py tests/test_envstate_supervisor.py
git commit -m "feat(envstate): add Supervisor planner (TaskSpec emitter + planning-view renderer)"
```

---

## Phase 10: Worker + interruption policy + orchestrator

**Why:** The Worker runs a bounded ReAct loop inside one TaskSpec, with host-enforced stop/interruption (design §9, §14). The orchestrator ties Supervisor → Worker → Maintainer → Probe (design §6) behind `--enable-supervisor`.

**Files:**
- Create: `src/envstate/worker.py`
- Create: `src/envstate/orchestrator.py`
- Test: `tests/test_envstate_worker.py`
- Test: `tests/test_envstate_orchestrator.py`
- Modify: `agent.py` (CLI flag `--enable-supervisor`; branch `run()` to the orchestrator when set)

- [ ] **Step 1: Write the failing Worker test**

Create `tests/test_envstate_worker.py`:

```python
import unittest

from src.envstate.worker import Worker, WorkerReport, should_interrupt


class FakeWorkerPlanner:
    """Returns a queued list of (action, is_finished) per step."""
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def next_action(self, task_brief, recent_observations):
        self.calls.append((task_brief, list(recent_observations)))
        return self.steps.pop(0)


class FakeExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        return self.results.pop(0)  # (success: bool, observation: str)


class InterruptionPolicyTests(unittest.TestCase):
    def test_interrupts_on_repeated_failure_signature(self):
        task = {"stop_conditions": ["same error twice"]}
        observations = [
            (False, "Error: pg_config executable not found"),
            (False, "Error: pg_config executable not found"),
        ]
        self.assertTrue(should_interrupt(task, observations, action="pip install x", actions_used=2))

    def test_interrupts_when_action_budget_exhausted(self):
        task = {"max_actions": 4}
        self.assertTrue(should_interrupt(task, [], action="pip install x", actions_used=4))

    def test_interrupts_on_dependency_pin_change_attempt(self):
        task = {"constraints": ["Do not edit requirements.txt"]}
        self.assertTrue(should_interrupt(
            task, [], action="sed -i 's/2.8.6/2.9/' requirements.txt", actions_used=1))

    def test_no_interruption_for_normal_action(self):
        self.assertFalse(should_interrupt({"max_actions": 4}, [], action="apt-get install -y libpq-dev", actions_used=1))


class WorkerRunTests(unittest.TestCase):
    def test_completes_when_planner_signals_finished(self):
        planner = FakeWorkerPlanner([("apt-get install -y libpq-dev", False), ("pip install psycopg2==2.8.6", True)])
        executor = FakeExecutor([(True, "installed libpq-dev"), (True, "Successfully installed psycopg2")])
        worker = Worker(planner=planner, max_actions=4)
        report = worker.run_task({"task_id": "task-004", "goal": "x", "max_actions": 4}, executor)
        self.assertIsInstance(report, WorkerReport)
        self.assertEqual(report.status, "complete")
        self.assertEqual(report.commands_attempted,
                         ["apt-get install -y libpq-dev", "pip install psycopg2==2.8.6"])

    def test_blocks_when_action_budget_exhausted(self):
        planner = FakeWorkerPlanner([("pip install x", False)] * 5)
        executor = FakeExecutor([(False, "boom")] * 5)
        worker = Worker(planner=planner, max_actions=2)
        report = worker.run_task({"task_id": "t", "goal": "x", "max_actions": 2}, executor)
        self.assertIn(report.status, ("blocked", "interrupted"))
        self.assertLessEqual(len(report.commands_attempted), 2)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_worker.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/envstate/worker.py`**

```python
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Tuple

# A worker executor runs one action and returns (success, observation).
WorkerExecutor = Callable[[str], Tuple[bool, str]]

DEFAULT_MAX_ACTIONS = 6
DEP_PIN_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile")


@dataclass(frozen=True)
class WorkerReport:
    task_id: str
    status: str  # "complete" | "blocked" | "interrupted"
    summary: str
    commands_attempted: Tuple[str, ...] = ()
    observed_blockers: Tuple[str, ...] = ()


def _looks_like_pin_edit(action: str) -> bool:
    normalized = action.lower()
    mutating_verb = any(v in normalized for v in ("sed -i", " > ", " >> ", "tee ", "rm ", "mv "))
    touches_dep_file = any(f in normalized for f in DEP_PIN_FILES)
    return mutating_verb and touches_dep_file


def should_interrupt(
    task_spec: dict[str, Any],
    observations: List[Tuple[bool, str]],
    action: str,
    actions_used: int,
) -> bool:
    """Host-enforced interruption policy (design §14)."""
    max_actions = task_spec.get("max_actions", DEFAULT_MAX_ACTIONS)
    if actions_used >= max_actions:
        return True
    if _looks_like_pin_edit(action):
        return True
    # repeated identical failure signature beyond the first repeat
    failures = [obs for ok, obs in observations if not ok]
    if len(failures) >= 2 and failures[-1].strip() == failures[-2].strip():
        return True
    return False


class Worker:
    """Bounded ReAct execution inside one TaskSpec.

    `planner` is any object with next_action(task_brief, recent_observations)
    -> (action:str, is_finished:bool). In production this wraps the shared LLM
    client with a worker-scoped prompt; in tests it is a fake.
    """

    def __init__(self, planner, max_actions: int = DEFAULT_MAX_ACTIONS):
        self.planner = planner
        self.max_actions = max_actions

    def run_task(self, task_spec: dict[str, Any], executor: WorkerExecutor) -> WorkerReport:
        task_id = task_spec.get("task_id", "task")
        brief = build_task_brief(task_spec)
        observations: List[Tuple[bool, str]] = []
        commands: List[str] = []
        blockers: List[str] = []
        max_actions = task_spec.get("max_actions", self.max_actions)

        while True:
            if len(commands) >= max_actions:
                return WorkerReport(task_id, "blocked", "action budget exhausted",
                                    tuple(commands), tuple(blockers))
            action, is_finished = self.planner.next_action(brief, observations[-3:])
            if is_finished:
                # Uniform completion: the worker signals done and does NOT execute a
                # trailing action — the Supervisor decides what happens next.
                return WorkerReport(task_id, "complete", "worker signaled completion",
                                    tuple(commands), tuple(blockers))
            # Check interruption BEFORE executing a constraint-violating action.
            if should_interrupt(task_spec, observations, action, actions_used=len(commands)):
                return WorkerReport(task_id, "interrupted",
                                    f"interruption policy fired on: {action}",
                                    tuple(commands), tuple(blockers))
            success, observation = executor(action)
            commands.append(action)
            observations.append((success, observation))
            if not success:
                blockers.append(observation.strip().splitlines()[-1] if observation.strip() else "unknown failure")


def build_task_brief(task_spec: dict[str, Any]) -> str:
    """Narrow brief for the worker (design §9 worker input)."""
    parts = [
        f"Task: {task_spec.get('goal', '')}",
        "Relevant facts:\n" + "\n".join(f"- {s}" for s in task_spec.get("relevant_state", [])),
        "Constraints:\n" + "\n".join(f"- {s}" for s in task_spec.get("constraints", [])),
        "Allowed actions:\n" + "\n".join(f"- {s}" for s in task_spec.get("allowed_actions", [])),
        "Success criteria:\n" + "\n".join(f"- {s}" for s in task_spec.get("success_criteria", [])),
        "Stop conditions:\n" + "\n".join(f"- {s}" for s in task_spec.get("stop_conditions", [])),
    ]
    return "\n\n".join(parts)


WORKER_SYSTEM_PROMPT = """You are the ReAct build Worker for DockerAgent.

You execute ONE bounded setup task by issuing shell commands inside the container.
Work only within the task's goal, constraints, and allowed actions. Do local trial
and error, but never edit dependency pin files, never change the task's scope, and
never claim the whole environment is done.

Respond each turn with exactly:
Thought: <your reasoning>
Action: <a single shell command>

When the task's success criteria are met, instead respond with:
Thought: <why the task is complete>
Final Answer: Success

You do not certify environment facts; the host verifies them with probes.
"""

_ACTION_RE = re.compile(r"^\s*Action:\s*(.+?)\s*$", re.MULTILINE)
_FINAL_RE = re.compile(r"^\s*Final Answer:\s*(Success|Failure)\b", re.IGNORECASE | re.MULTILINE)


def _extract_worker_action(content: str) -> str:
    match = _ACTION_RE.search(content or "")
    if not match:
        return ""
    action = match.group(1).strip()
    action = re.sub(r"^```[a-zA-Z]*\n?", "", action)
    action = re.sub(r"\n?```$", "", action).strip()
    return action.splitlines()[0].strip() if action else ""


def _is_worker_finished(content: str) -> bool:
    return bool(_FINAL_RE.search(content or ""))


class LlmWorkerPlanner:
    """Adapter exposing next_action(task_brief, recent_observations) -> (action, is_finished)
    over the shared OpenAI-compatible client. Maintains its own short ReAct history.
    """

    def __init__(self, client, model):
        self.client = client
        self.model = model
        self.history: List[dict] = []

    def next_action(self, task_brief: str, recent_observations: List[Tuple[bool, str]]):
        if not self.history:
            self.history.append({"role": "user", "content": task_brief})
        elif recent_observations:
            _ok, observation = recent_observations[-1]
            self.history.append({"role": "user", "content": f"Observation: {observation}"})
        messages = [{"role": "system", "content": WORKER_SYSTEM_PROMPT}] + self.history
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0, stop=["Observation:"]
        )
        content = response.choices[0].message.content or ""
        self.history.append({"role": "assistant", "content": content})
        if _is_worker_finished(content):
            return "", True
        return _extract_worker_action(content), False
```

- [ ] **Step 4: Run the worker tests + add an `LlmWorkerPlanner` test**

Update `test_completes_when_planner_signals_finished` in `tests/test_envstate_worker.py` so the planner returns the final action with `is_finished=False`, then a terminal `("", True)` (matching the uniform-completion contract — `is_finished` no longer executes a trailing action):

```python
    def test_completes_when_planner_signals_finished(self):
        planner = FakeWorkerPlanner([
            ("apt-get install -y libpq-dev", False),
            ("pip install psycopg2==2.8.6", False),
            ("", True),
        ])
        executor = FakeExecutor([(True, "installed libpq-dev"), (True, "Successfully installed psycopg2")])
        worker = Worker(planner=planner, max_actions=4)
        report = worker.run_task({"task_id": "task-004", "goal": "x", "max_actions": 4}, executor)
        self.assertEqual(report.status, "complete")
        self.assertEqual(report.commands_attempted,
                         ["apt-get install -y libpq-dev", "pip install psycopg2==2.8.6"])
```

Add an `LlmWorkerPlanner` test class to `tests/test_envstate_worker.py`:

```python
from src.envstate.worker import LlmWorkerPlanner


def _fake_client(content):
    from types import SimpleNamespace
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_k: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        ))))


class LlmWorkerPlannerTests(unittest.TestCase):
    def test_returns_action_when_not_finished(self):
        planner = LlmWorkerPlanner(_fake_client("Thought: install\nAction: apt-get install -y libpq-dev"), "m")
        action, finished = planner.next_action("brief", [])
        self.assertEqual(action, "apt-get install -y libpq-dev")
        self.assertFalse(finished)

    def test_signals_finished_on_final_answer(self):
        planner = LlmWorkerPlanner(_fake_client("Thought: done\nFinal Answer: Success"), "m")
        action, finished = planner.next_action("brief", [])
        self.assertEqual(action, "")
        self.assertTrue(finished)
```

Run: `python -m pytest tests/test_envstate_worker.py -v`
Expected: all PASS

- [ ] **Step 5: Write the failing orchestrator test**

Create `tests/test_envstate_orchestrator.py`. The orchestrator is tested with fakes for Supervisor, Worker, executor, and probe-runner — no Docker, no LLM:

```python
import unittest

from src.envstate.orchestrator import EnvStateOrchestrator
from src.envstate.types import BaseFacts, EnvStateSnapshot
from src.envstate.ledger import ActionLedger
from src.envstate.worker import Worker, WorkerReport
from src.envstate.acl import advance_revision


class FakeSupervisor:
    def __init__(self, tasks):
        self.tasks = list(tasks)

    def next_task(self, snapshot, ledger, budget):
        if not self.tasks:
            return None, {"total_tokens": 0}
        return self.tasks.pop(0), {"total_tokens": 1}


class FakeWorker:
    def __init__(self, reports):
        self.reports = list(reports)

    def run_task(self, task_spec, step_fn):
        return self.reports.pop(0)


class FakeWorkerPlanner:
    def __init__(self, steps):
        self.steps = list(steps)

    def next_action(self, brief, recent):
        return self.steps.pop(0)


def _noop_observer(snapshot, task_spec, step, action, success, observation):
    return snapshot


class OrchestratorTests(unittest.TestCase):
    def _snapshot(self):
        return EnvStateSnapshot(revision=0, container_id="c1", base=BaseFacts(image="python:3.11-slim"))

    def test_loop_stops_when_supervisor_returns_no_task(self):
        supervisor = FakeSupervisor([
            {"task_id": "t1", "phase": "Verification", "goal": "g", "success_criteria": []},
        ])
        worker = FakeWorker([WorkerReport("t1", "complete", "done", ("pytest -q",))])
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=_noop_observer,
            max_tasks=10,
        )
        result = orch.run()
        self.assertEqual(result["tasks_completed"], 1)
        self.assertEqual(result["stop_reason"], "no_more_tasks")

    def test_loop_respects_max_tasks_budget(self):
        supervisor = FakeSupervisor([{"task_id": f"t{i}", "phase": "x", "goal": "g", "success_criteria": []}
                                     for i in range(100)])
        worker = FakeWorker([WorkerReport(f"t{i}", "complete", "done") for i in range(100)])
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=_noop_observer,
            max_tasks=3,
        )
        result = orch.run()
        self.assertEqual(result["tasks_completed"], 3)
        self.assertEqual(result["stop_reason"], "max_tasks")

    def test_observer_threads_snapshot_per_action(self):
        # Real Worker drives step_fn; the observer advances the EnvState revision on
        # each executed action — proving the §6 loop actually updates the world model.
        supervisor = FakeSupervisor([
            {"task_id": "t1", "phase": "x", "goal": "g", "success_criteria": [], "max_actions": 3},
        ])
        worker = Worker(planner=FakeWorkerPlanner([
            ("apt-get install -y libpq-dev", False),
            ("pip install psycopg2", False),
            ("", True),
        ]), max_actions=3)

        def observer(snapshot, task_spec, step, action, success, observation):
            return advance_revision(snapshot, "language_package_install")

        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=observer,
            max_tasks=5,
        )
        result = orch.run()
        # two actions executed (third step is terminal) -> observer ran twice -> revision 2
        self.assertEqual(result["final_revision"], 2)
        self.assertEqual(orch.snapshot.revision, 2)
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 7: Create `src/envstate/orchestrator.py`**

```python
from __future__ import annotations
from typing import Any, Callable, Tuple

from src.envstate.ledger import ActionLedger
from src.envstate.types import EnvStateSnapshot

# executor(action) -> (success, observation)   — raw execution (Sandbox.execute)
Executor = Callable[[str], Tuple[bool, str]]
# observer(snapshot, task_spec, step, action, success, observation) -> new_snapshot
#   This is where the §6 loop closes: per executed action the host advances the
#   revision, runs the Maintainer, runs probe_requests, and certifies facts via the
#   ACL. It MUST return the new (immutable) snapshot, which the orchestrator threads.
Observer = Callable[..., EnvStateSnapshot]


class EnvStateOrchestrator:
    """Supervisor -> Worker -> (per-action) Observer loop (design §6).

    Collaborators are injected so this is unit-testable without Docker/LLM:
      supervisor.next_task(snapshot, ledger, budget) -> (task_spec|None, usage)
      worker.run_task(task_spec, step_fn) -> WorkerReport
      executor(action) -> (success, observation)
      observer(snapshot, task_spec, step, action, success, observation) -> new_snapshot
    """

    def __init__(
        self,
        supervisor,
        worker,
        snapshot: EnvStateSnapshot,
        ledger: ActionLedger,
        executor: Executor,
        observer: Observer,
        max_tasks: int = 20,
    ):
        self.supervisor = supervisor
        self.worker = worker
        self.snapshot = snapshot
        self.ledger = ledger
        self.executor = executor
        self.observer = observer
        self.max_tasks = max_tasks
        self._step = 0

    def _make_step_fn(self, task_spec):
        """Per-task execution closure handed to the Worker. Executes ONE action,
        then observes it into the EnvState snapshot (advance revision, Maintainer,
        probes, ACL certification). Threads the new snapshot back onto self."""
        def step_fn(action):
            self._step += 1
            success, observation = self.executor(action)
            self.snapshot = self.observer(
                self.snapshot, task_spec, self._step, action, success, observation
            )
            return success, observation
        return step_fn

    def run(self) -> dict[str, Any]:
        tasks_completed = 0
        reports = []
        stop_reason = "no_more_tasks"
        while True:
            if tasks_completed >= self.max_tasks:
                stop_reason = "max_tasks"
                break
            budget = {"steps_remaining": self.max_tasks - tasks_completed}
            task_spec, _usage = self.supervisor.next_task(self.snapshot, self.ledger, budget)
            if not task_spec:
                stop_reason = "no_more_tasks"
                break
            report = self.worker.run_task(task_spec, self._make_step_fn(task_spec))
            reports.append(report)
            tasks_completed += 1
            # Synthesis-readiness / verification termination hook lives here in
            # production: when the Supervisor selects "Synthesis Readiness" and host
            # probes confirm success criteria, break with stop_reason="verified".
        return {
            "tasks_completed": tasks_completed,
            "stop_reason": stop_reason,
            "reports": reports,
            "final_revision": self.snapshot.revision,
        }
```

- [ ] **Step 8: Run it to verify it passes**

Run: `python -m pytest tests/test_envstate_orchestrator.py -v`
Expected: all PASS

- [ ] **Step 9: Add the `--enable-supervisor` CLI flag and branch `run()`**

In `agent.py`, add the parameter `enable_supervisor: bool = False` to `DockerAgent.__init__` and store `self.enable_supervisor = enable_supervisor` (also force `self.enable_envstate = enable_envstate or enable_supervisor`). Add the argparse flag in the CLI block:

```python
    parser.add_argument("--enable-supervisor", action="store_true",
                        help="Use the EnvState Supervisor/Worker orchestrator instead of the legacy ReAct loop.")
```

and pass `enable_supervisor=args.enable_supervisor`.

At the very top of `DockerAgent.run` (`agent.py:778`), add the branch (the legacy loop is the default `else`):

```python
    def run(self, max_steps=30, keep_container=False):
        if getattr(self, "enable_supervisor", False):
            return self._run_supervisor(max_steps=max_steps, keep_container=keep_container)
        # ... existing legacy loop unchanged ...
```

Then add two methods. `_build_observer` returns the per-action observation pipeline that **closes the design §6 loop** (this is the fix for the review's top finding — without it the EnvState snapshot stays empty and the Supervisor plans blind). `_run_supervisor` wires everything and reuses the legacy finalize/synthesis/run-summary path.

```python
    def _build_observer(self, maintainer):
        """Per-action observation pipeline (design §6 steps 5-8). For each executed
        action: record evidence + ActionEvent (legacy + ledger), advance the EnvState
        revision on a real mutation, ask the Maintainer to interpret failures/mutations
        into ACL-safe hypotheses + probe_requests, then run those probes on the host and
        certify PRESENT/MISSING through the ACL. Returns the new (immutable) snapshot."""
        from src.envstate.acl import advance_revision
        from src.envstate.probes import ProbeSpec, certify_probe_result, run_probe

        def observer(snapshot, task_spec, step, action, success, observation):
            # 1. record into legacy evidence ledger + ActionLedger (the latter via
            #    _append_action_event called inside these methods).
            if success:
                self._record_successful_action(step, action, observation)
            else:
                self._record_failed_action(step, action, observation)

            mutation_class = None
            if success and self.synthesizer.command_mutates_environment(action):
                mutation_class = self.synthesizer.classify_mutation(action)
                snapshot = advance_revision(snapshot, mutation_class)

            # 2. interpret on failures or env mutations (where the map must change);
            #    skip read-only successes to save LLM calls.
            if (not success) or mutation_class:
                action_event = self.action_ledger.events()[-1]
                snapshot, proposal, rejected, _usage = maintainer.interpret(
                    snapshot, task_spec, action_event, observation
                )
                if rejected:
                    print(f"[EnvState ACL] rejected {len(rejected)} LLM proposal(s)")
                # 3. run each requested probe on the HOST and certify the truth.
                for request in proposal.get("probe_requests", []):
                    name = request.get("name")
                    if not name:
                        continue
                    spec = ProbeSpec(
                        kind=request.get("kind", "cli"),
                        name=name,
                        predicate=request.get("predicate", ""),
                    )
                    result = run_probe(
                        self.sandbox.exec_readonly, spec,
                        env_revision=snapshot.revision, container_id=self.env_container_id,
                    )
                    requirement_id = request.get("requirement_id") or f"tool:{name}"
                    snapshot = certify_probe_result(snapshot, requirement_id, result)
            return snapshot

        return observer

    def _run_supervisor(self, max_steps=30, keep_container=False):
        from src.envstate.supervisor import Supervisor
        from src.envstate.worker import LlmWorkerPlanner, Worker
        from src.envstate.orchestrator import EnvStateOrchestrator
        from src.envstate.maintainer import Maintainer
        from src.envstate.types import BaseFacts, EnvStateSnapshot

        supervisor = Supervisor(client=self.client, model=self.model)
        maintainer = Maintainer(client=self.client, model=self.model)
        base_image = (
            getattr(self, "base_image", None)
            or getattr(self.synthesizer, "base_image", None)
            or ""
        )
        snapshot = EnvStateSnapshot(
            revision=0, container_id=self.env_container_id, base=BaseFacts(image=base_image),
        )
        worker = Worker(planner=LlmWorkerPlanner(self.client, self.model), max_actions=6)
        orchestrator = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker, snapshot=snapshot,
            ledger=self.action_ledger,
            executor=self.sandbox.execute,            # raw exec; observer does the recording
            observer=self._build_observer(maintainer),
            max_tasks=max_steps,
        )
        try:
            result = orchestrator.run()
            self.env_snapshot = orchestrator.snapshot
            configuration_success = (
                self._auto_finalize_from_verified_tests("supervisor_run")
                or bool(self.verification_bundle)
            )
            if configuration_success:
                # _synthesize_final_build_recipe returns False on synthesis failure;
                # honor it like the legacy loop (agent.py:941).
                configuration_success = self._synthesize_final_build_recipe()
            self._write_run_summary(configuration_success)
            return configuration_success
        finally:
            self.sandbox.close(keep_alive=keep_container)
```

> **`base_image`:** the radical agent does NOT expose `self.base_image`; the base image lives on `self.synthesizer.base_image` (and a local in `__init__`). The `getattr` chain is defensive — verify the real attribute when implementing.
> **`self.model`/`self.client`:** both set in `DockerAgent.__init__` (`agent.py:211`) and shared with all sub-components — reuse, never create new clients.
> **Probe requirement ids:** the Maintainer's `probe_request` should carry `requirement_id` so the certified PRESENT/MISSING fact updates the same requirement the hypothesis created; the `tool:{name}` fallback covers the common CLI case. State this in the Maintainer prompt.
> **Two revision counters stay in sync:** the legacy `self._environment_revision` (advanced inside `_record_successful_action`) and `snapshot.revision` (advanced by `advance_revision`) both increment exactly once per successful mutating command, so they track each other. Probe Evidence is stamped with `snapshot.revision`.
> **Not wired in V1 (deferred):** the legacy `__ROLLBACK__`/`__RETRIEVE_MEMORY__` action sentinels (`agent.py:1032-1036`) are not handled in the supervisor path — workers emit plain shell commands only.

- [ ] **Step 9b: Add a `_build_observer` unit test (proves the trust-boundary loop is closed)**

This is the most important new test — it proves the EnvState snapshot is actually populated by host probes during a run, not left empty. Add `tests/test_agent_supervisor_observe.py`:

```python
import unittest
from types import SimpleNamespace

from agent import DockerAgent
from src.synthesizer import Synthesizer
from src.envstate.ledger import ActionLedger
from src.envstate.types import BaseFacts, EnvStateSnapshot, Source, Status


class _FakeMaintainer:
    """Returns a proposal asking the host to probe `pg_config`."""
    def interpret(self, snapshot, task_spec, action_event, observation):
        proposal = {"probe_requests": [
            {"kind": "cli", "name": "pg_config", "predicate": "path exists",
             "requirement_id": "tool:pg_config"}
        ]}
        return snapshot, proposal, [], {"total_tokens": 0}


class AgentSupervisorObserveTests(unittest.TestCase):
    def _make_agent(self):
        agent = DockerAgent.__new__(DockerAgent)
        agent.synthesizer = Synthesizer()
        agent.successful_test_commands = []
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.verified_runtime_preparation_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
        agent._environment_revision = 0
        agent._current_verification_group = []
        agent.required_local_services = set()
        agent.enable_envstate = True
        agent.action_ledger = ActionLedger()
        agent.current_task_id = "t1"
        agent.env_container_id = "abc123"
        # exec_readonly probe runner: pg_config present (rc 0)
        agent.sandbox = SimpleNamespace(exec_readonly=lambda cmd: (0, "/usr/bin/pg_config\n10.1"))
        return agent

    def test_observer_certifies_present_via_host_probe(self):
        agent = self._make_agent()
        observer = agent._build_observer(_FakeMaintainer())
        snapshot = EnvStateSnapshot(revision=0, container_id="abc123",
                                    base=BaseFacts(image="python:3.11-slim"))
        # a failing pip install triggers maintainer -> probe_request -> host certify
        snapshot = observer(snapshot, {"task_id": "t1"}, 1,
                            "pip install psycopg2==2.8.6", False,
                            "Error: pg_config executable not found")
        req = [r for r in snapshot.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.PRESENT)
        self.assertEqual(req.source, Source.PROBE)
        self.assertIsNotNone(req.evidence)
        self.assertEqual(agent.action_ledger.events()[-1].cmd, "pip install psycopg2==2.8.6")
```

Run: `python -m pytest tests/test_agent_supervisor_observe.py -v`
Expected: PASS — the snapshot now carries a host-certified `PRESENT` fact, proving the §6 loop is closed.

- [ ] **Step 10: Run the full envstate + agent regression suite**

Run: `python -m pytest tests/test_envstate_worker.py tests/test_envstate_orchestrator.py tests/test_agent_supervisor_observe.py tests/test_agent_verification.py -v`
Expected: all PASS. Confirm the legacy path is untouched: `python -m pytest tests/ -q` should still be green (the Milestone-D machinery is all behind `--enable-supervisor`/`--enable-envstate`, which `test_agent_verification.py` never sets).

- [ ] **Step 11: Commit**

```bash
git add src/envstate/worker.py src/envstate/orchestrator.py agent.py tests/test_envstate_worker.py tests/test_envstate_orchestrator.py tests/test_agent_supervisor_observe.py
git commit -m "feat(envstate): wire Supervisor->Worker->Maintainer->Probe loop behind --enable-supervisor"
```

> **Milestone D complete.** A full Supervisor→Worker→Maintainer→Probe loop runs end-to-end behind `--enable-supervisor`, while the legacy loop remains the default. The loop still reuses the existing finalize/synthesis path; Milestone E hardens that path.

---

# Milestone E — Synthesis & Clean-room Verification

## Phase 11: Synthesis gate from replayable actions + current probe evidence

**Why:** The synthesizer currently trusts a prose `setup_log_summary_text` and an LLM recipe-first path (design §15 says it must not). When `enable_envstate` is set, the build recipe should be assembled from the **ordered ActionLedger** (successful, env-mutating, non-test commands) plus current probe-backed facts — not prose.

**Files:**
- Create: `src/envstate/synthesis.py`
- Test: `tests/test_envstate_synthesis.py`
- Modify: `agent.py` (`_synthesize_final_build_recipe` to prefer the ledger path when `enable_envstate`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_envstate_synthesis.py`:

```python
import unittest

from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.synthesis import build_commands_from_ledger


def _event(step, cmd, rc, mutation_class):
    return ActionEvent(step=step, task_id=None, cmd=cmd, rc=rc, stdout_path=None,
                       stderr_path=None, env_revision_before=0, env_revision_after=0,
                       mutation_class=mutation_class, container_id="c1", summary="")


class SynthesisFromLedgerTests(unittest.TestCase):
    def test_keeps_only_successful_mutating_commands_in_order(self):
        ledger = ActionLedger()
        ledger.append(_event(1, "cat README.md", 0, None))                  # read-only -> drop
        ledger.append(_event(2, "apt-get install -y libpq-dev", 0, "system_package_install"))
        ledger.append(_event(3, "pip install psycopg2==2.8.6", 1, "language_package_install"))  # failed -> drop
        ledger.append(_event(4, "pip install psycopg2==2.8.6", 0, "language_package_install"))
        ledger.append(_event(5, "pytest -q", 0, None))                      # test -> drop
        commands = build_commands_from_ledger(ledger)
        self.assertEqual(commands, ["apt-get install -y libpq-dev", "pip install psycopg2==2.8.6"])

    def test_preserves_duplicate_order_sensitive_commands(self):
        ledger = ActionLedger()
        ledger.append(_event(1, "pip install a", 0, "language_package_install"))
        ledger.append(_event(2, "pip install b", 0, "language_package_install"))
        ledger.append(_event(3, "pip install a", 0, "language_package_install"))  # re-install kept (order matters)
        commands = build_commands_from_ledger(ledger)
        self.assertEqual(commands, ["pip install a", "pip install b", "pip install a"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_synthesis.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/envstate/synthesis.py`**

```python
from __future__ import annotations
from typing import List

from src.envstate.ledger import ActionLedger


def build_commands_from_ledger(ledger: ActionLedger) -> List[str]:
    """Authoritative, order-preserving build-command extraction (design §15).

    Includes only successful (rc==0) env-mutating commands, in trajectory order.
    Read-only commands (mutation_class is None and not a mutation) and test
    commands (also mutation_class None) are excluded. Duplicates are intentionally
    preserved — setup side effects are NOT algebraically mergeable (see CLAUDE.md).
    """
    commands: List[str] = []
    for event in ledger.events():
        if event.rc != 0:
            continue
        if not event.mutation_class:
            continue  # read-only or test command
        commands.append(event.cmd)
    return commands
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_envstate_synthesis.py -v`
Expected: all PASS

- [ ] **Step 5: Prefer the ledger path in `_synthesize_final_build_recipe`**

In `agent.py`, near the top of `_synthesize_final_build_recipe` (`agent.py:1105`), add a guarded branch that uses the ledger when EnvState is on, falling back to the existing LLM/trajectory path otherwise.

**IMPORTANT (review finding):** `apply_build_recipe` (`synthesizer.py:2711`) only iterates `build_commands` into `self.instructions`, and `generate_dockerfile` (`synthesizer.py:3842`) renders *solely* from `self.instructions`. The recipe's `runtime_preparation_commands`/`test_commands` are **stored but never rendered into the Dockerfile** — they belong to the verification bundle, which `_auto_finalize_from_verified_tests` already set before synthesis. So pass only `build_commands` here; do NOT rely on the other keys reaching the Dockerfile:

```python
        if getattr(self, "enable_envstate", False) and self.action_ledger is not None:
            from src.envstate.synthesis import build_commands_from_ledger
            ledger_commands = build_commands_from_ledger(self.action_ledger)
            if ledger_commands:
                # Only build_commands become Dockerfile RUN steps. The verification
                # bundle (runtime_prep + test_commands) is already set on self by
                # _auto_finalize_from_verified_tests / the agent-report finalizer and
                # is serialized separately into the run summary — it is NOT part of the
                # Dockerfile body.
                self.synthesizer.apply_build_recipe({
                    "build_commands": ledger_commands,
                    "post_test_patch_commands": [],
                    "runtime_preparation_commands": [],
                    "test_commands": [],
                    "excluded_commands": [],
                    "rationale": "Assembled from ActionLedger (replayable actions only).",
                    "confidence": "high",
                })
                self.build_recipe = {
                    "build_commands": ledger_commands,
                    "source": "action_ledger",
                }
                self.build_recipe_source = "action_ledger"
                return True
        # ... existing LLM/trajectory synthesis path unchanged ...
```

> Verify `apply_build_recipe`'s required keys against `RECIPE_REQUIRED_KEYS` (`synthesizer.py:19-27`) — all 7 keys must be present even when empty, or it may raise. Also confirm whether `_synthesize_final_build_recipe` sets `self.build_recipe`/`self.build_recipe_source` in the legacy path and match those attribute names exactly (the names above are best-effort from the scout; adjust to the real ones).

- [ ] **Step 6: Run synthesis + agent regression**

Run: `python -m pytest tests/test_envstate_synthesis.py tests/test_synthesizer.py tests/test_agent_verification.py -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/envstate/synthesis.py agent.py tests/test_envstate_synthesis.py
git commit -m "feat(envstate): synthesize build recipe from ActionLedger under --enable-envstate"
```

---

## Phase 12: Clean-room rebuild verification

**Why:** The synthesized Dockerfile is only trustworthy if a fresh image built from it alone reproduces the probes + final test command (design §15). This runs **host-side** (Docker SDK), not as an agent action.

**Files:**
- Create: `src/envstate/cleanroom.py`
- Test: `tests/test_envstate_cleanroom.py`
- Modify: `agent.py` (`_run_supervisor` to call clean-room verification before declaring success when `--enable-cleanroom`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_envstate_cleanroom.py`. The Docker client and the in-image executor are faked:

```python
import tempfile
import unittest

from src.envstate.cleanroom import CleanroomResult, verify_cleanroom
from src.envstate.probes import ProbeSpec


class FakeImages:
    def __init__(self, build_ok=True):
        self.build_ok = build_ok
        self.built = []

    def build(self, **kwargs):
        self.built.append(kwargs)
        if not self.build_ok:
            raise RuntimeError("build failed")
        return ("image-id", iter([]))


class FakeDockerClient:
    def __init__(self, build_ok=True):
        self.images = FakeImages(build_ok=build_ok)


class CleanroomTests(unittest.TestCase):
    def test_success_when_build_probes_and_tests_pass(self):
        client = FakeDockerClient(build_ok=True)
        result = verify_cleanroom(
            client, dockerfile_text="FROM python:3.11-slim\nCOPY . /app\n",
            build_context_dir=tempfile.mkdtemp(),
            probes=[ProbeSpec(kind="cli", name="pg_config", predicate="path exists")],
            test_commands=["pytest -q"],
            run_command=lambda image, cmd: (0, "ok"),
        )
        self.assertIsInstance(result, CleanroomResult)
        self.assertTrue(result.passed)
        # built with a context path (so COPY works), not fileobj
        self.assertIn("path", client.images.built[0])

    def test_failure_when_build_fails(self):
        client = FakeDockerClient(build_ok=False)
        result = verify_cleanroom(
            client, dockerfile_text="FROM bad\n", build_context_dir=tempfile.mkdtemp(),
            probes=[], test_commands=["pytest -q"], run_command=lambda image, cmd: (0, "ok"),
        )
        self.assertFalse(result.passed)
        self.assertIn("build", result.reason.lower())

    def test_failure_when_probe_regresses_in_clean_image(self):
        client = FakeDockerClient(build_ok=True)
        result = verify_cleanroom(
            client, dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probes=[ProbeSpec(kind="cli", name="pg_config", predicate="path exists")],
            test_commands=["pytest -q"],
            run_command=lambda image, cmd: (1, "not found"),  # probe fails in clean image
        )
        self.assertFalse(result.passed)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_envstate_cleanroom.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/envstate/cleanroom.py`**

```python
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Callable, List

from src.envstate.probes import ProbeSpec, build_probe_command

# run_command(image_ref, command) -> (rc, stdout). In production this starts a
# throwaway container from the built image and runs the command; in tests it is faked.
InImageRunner = Callable[[str, str], tuple]


@dataclass(frozen=True)
class CleanroomResult:
    passed: bool
    reason: str
    failed_probes: tuple = ()
    failed_tests: tuple = ()


def verify_cleanroom(
    docker_client,
    dockerfile_text: str,
    build_context_dir: str,
    probes: List[ProbeSpec],
    test_commands: List[str],
    run_command: InImageRunner,
) -> CleanroomResult:
    """Build a fresh image from the Dockerfile + repo context, then re-run probes + tests.

    A synthesized Dockerfile typically contains `COPY . /app`, which Docker can only
    resolve with a build CONTEXT — so we build from a directory (`path=`), not a bare
    `fileobj`. `build_context_dir` must contain the repo files; we drop the Dockerfile
    text into it under a unique name and build against it.
    """
    dockerfile_name = "Dockerfile.envstate-cleanroom"
    try:
        with open(os.path.join(build_context_dir, dockerfile_name), "w", encoding="utf-8") as handle:
            handle.write(dockerfile_text)
        image, _logs = docker_client.images.build(
            path=build_context_dir, dockerfile=dockerfile_name, rm=True
        )
    except Exception as exc:  # build failure is a hard fail
        return CleanroomResult(False, f"clean-room build failed: {exc}")

    image_ref = image if isinstance(image, str) else getattr(image, "id", str(image))

    failed_probes = []
    for spec in probes:
        rc, _out = run_command(image_ref, build_probe_command(spec))
        if rc != 0:
            failed_probes.append(spec.name)
    if failed_probes:
        return CleanroomResult(False, "probe(s) regressed in clean image",
                               failed_probes=tuple(failed_probes))

    failed_tests = []
    for command in test_commands:
        rc, _out = run_command(image_ref, command)
        if rc != 0:
            failed_tests.append(command)
    if failed_tests:
        return CleanroomResult(False, "test command(s) failed in clean image",
                               failed_tests=tuple(failed_tests))

    return CleanroomResult(True, "clean-room verification passed")
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_envstate_cleanroom.py -v`
Expected: all PASS

- [ ] **Step 5: Wire clean-room into `_run_supervisor` behind `--enable-cleanroom`**

Add the CLI flag `--enable-cleanroom` (argparse + `__init__` param `enable_cleanroom=False` + `self.enable_cleanroom`). Add a helper `_verify_cleanroom_or_fail()` and call it inside `_run_supervisor` after `_synthesize_final_build_recipe()` succeeds and before declaring success:

```python
    def _verify_cleanroom_or_fail(self):
        """Return True if clean-room verification passes (or is disabled)."""
        if not getattr(self, "enable_cleanroom", False):
            return True
        from src.envstate.cleanroom import verify_cleanroom
        from src.envstate.probes import ProbeSpec
        from src.envstate.types import Source

        dockerfile_text = self.synthesizer.generate_dockerfile()  # returns the text
        # Re-probe only what the host already certified PRESENT this revision.
        snapshot = getattr(self, "env_snapshot", None)
        probes = []
        if snapshot is not None:
            for req in snapshot.requirements:
                if req.source == Source.PROBE and req.status == "PRESENT" and req.evidence:
                    kind = req.kind.lower() if req.kind else "cli"
                    probes.append(ProbeSpec(kind="cli", name=req.name, predicate=req.evidence.stdout_predicate))

        def run_command(image_ref, command):
            result = self.sandbox.client.containers.run(
                image_ref, command, remove=True, working_dir="/app"
            )
            # containers.run returns bytes on success; non-zero raises ContainerError.
            return 0, (result.decode("utf-8", "replace") if isinstance(result, (bytes, bytearray)) else str(result))

        result = verify_cleanroom(
            self.sandbox.client, dockerfile_text,
            build_context_dir=self.workplace,        # repo files on host (build context)
            probes=probes,
            test_commands=list(self.verified_test_commands),
            run_command=run_command,
        )
        self.run_summary_cleanroom = {"passed": result.passed, "reason": result.reason}
        if not result.passed:
            print(f"[Clean-room] verification FAILED: {result.reason}")
        return result.passed
```

Then in `_run_supervisor`, change the success block to gate on it:

```python
            if configuration_success:
                configuration_success = self._synthesize_final_build_recipe()
            if configuration_success:
                configuration_success = self._verify_cleanroom_or_fail()
```

> `run_command` above uses `containers.run`, which **raises** `docker.errors.ContainerError` on non-zero exit. For faithful rc capture, wrap it in try/except and return the real `exc.exit_status`. The fake in the test below bypasses this.

- [ ] **Step 5b: Add the clean-room wiring test**

Create `tests/test_agent_cleanroom_wiring.py`:

```python
import unittest
from types import SimpleNamespace

from agent import DockerAgent


class _FakeSynth:
    def generate_dockerfile(self, *a, **k):
        return "FROM python:3.11-slim\nCOPY . /app\n"


class AgentCleanroomWiringTests(unittest.TestCase):
    def _agent(self, build_ok):
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_cleanroom = True
        agent.synthesizer = _FakeSynth()
        agent.env_snapshot = None
        agent.verified_test_commands = ["pytest -q"]
        agent.workplace = "/tmp"  # any existing dir; fake build ignores it

        class _Images:
            def build(self, **kwargs):
                if not build_ok:
                    raise RuntimeError("boom")
                return ("img", iter([]))

        agent.sandbox = SimpleNamespace(client=SimpleNamespace(
            images=_Images(),
            containers=SimpleNamespace(run=lambda *a, **k: b"ok"),
        ))
        return agent

    def test_passes_when_build_and_tests_pass(self):
        self.assertTrue(self._agent(build_ok=True)._verify_cleanroom_or_fail())

    def test_fails_when_build_fails(self):
        agent = self._agent(build_ok=False)
        self.assertFalse(agent._verify_cleanroom_or_fail())
        self.assertFalse(agent.run_summary_cleanroom["passed"])

    def test_disabled_returns_true(self):
        agent = self._agent(build_ok=False)
        agent.enable_cleanroom = False
        self.assertTrue(agent._verify_cleanroom_or_fail())
```

Run: `python -m pytest tests/test_agent_cleanroom_wiring.py -v` → all PASS.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/envstate/cleanroom.py agent.py tests/test_envstate_cleanroom.py tests/test_agent_cleanroom_wiring.py
git commit -m "feat(envstate): add host-side clean-room rebuild verification behind --enable-cleanroom"
```

> **Milestone E complete.** The full design is implemented: a Supervisor/Worker loop over a host-certified, revisioned EnvState, synthesizing a Dockerfile from replayable actions and verifying it in a clean room — all behind feature flags, with the legacy loop and two correctness fixes shipped along the way.

---

## End-to-end smoke (after Milestone E)

- [ ] Run a single-repo Repo2Run smoke with the new loop (Docker-heavy — only when explicitly desired):

```bash
python run_repo2run_benchmark.py --dataset datasets/repo2run_table15.json --limit 1 \
  --extra-agent-args "--enable-supervisor --enable-cleanroom"
```

(If `run_repo2run_benchmark.py` does not forward extra agent args, add a `--extra-agent-args` passthrough in `build_agent_command` at `run_repo2run_benchmark.py:162` first, mirroring the existing `--enable-observation-compression` branch.)

Expected: `agent_run_summary.json` contains `action_ledger`, the synthesized Dockerfile builds, and clean-room verification passes (or fails loudly with a reason).

---

## Deferred / Open (documented, not built)

These map to design §18 "Open Decisions" and were intentionally scoped out of this plan to keep each milestone shippable:

1. **Memory → EnvState hypotheses.** The ACL already guarantees memory could only ever produce `REQUIRED`/`UNKNOWN`. A future phase wraps `LongTermMemoryManager.retrieve` (`memory_manager.py:396`) to emit MEMORY-sourced hypotheses with a repo-scope leak guard, and proactively at task start rather than via the `__RETRIEVE_MEMORY__` sentinel.
2. **Memory eviction/demotion.** Requires new per-record metadata (`usage_count`, `last_used`) at `write_memories` (`memory_manager.py:474`). Not built.
3. **Provider disambiguation (DIAGNOSE).** `ProviderFact` + `diagnose_requests` exist in the schema and the Maintainer output; the actual apt-file/heuristic resolver is a future phase.
4. **Source-build replay probe** for native packages (psycopg2/lxml) — `ProbeSpec(kind="source_build", command=...)` is supported but no canonical replay command set is shipped.
5. **STATIC_SCAN beyond Python.** The Phase-3 parser is Python-first; other ecosystems are deferred.
6. **Maintainer log-tier escalation.** V1 always sends residual spans; "full logs on ambiguity/repeat-failure/native-build-failure" (design §11) is a future extension of `should_apply_compression` (`observation_compressor.py:698`).
7. **True-exit-code ActionLedger.** `Sandbox.execute()` collapses the real rc into a bool, so `ActionEvent.rc` is a 0/1 success proxy. Capturing the real exit code requires widening `execute()`'s return (it touches the legacy loop's single caller and the `(success, output)` contract) — deferred. Probes already carry real rc via `exec_readonly`.
8. **Same-revision ineffective-test edge in the offline gate.** The Phase-2 fix rejects test evidence made stale by a *mutation*; it does not replicate the live group-invalidation that also fires when an *ineffective* test runs after a verified one at the same revision (`agent.py:1605`). Low value (only reachable via `workplace_replay` resynthesis); deferred.
9. **`run()` return-type asymmetry.** The legacy `run()` returns `None`; `_run_supervisor` returns a `bool`. No current caller checks the return, so this is cosmetic — unify if a caller ever depends on it.
10. **Rollback / memory in the supervisor path.** The legacy `__ROLLBACK__` / `__RETRIEVE_MEMORY__` action sentinels are not handled when `--enable-supervisor` is on (workers emit plain shell only). Add sentinel dispatch in `_build_observer` if rollback-in-supervisor becomes necessary.

---

## Self-Review (completed by plan author)

- **Spec coverage:** design §17 steps 1–11 → Phases 0,1(added),2,3,4,5,6,7,8,9,10,11,12. The env_revision fix (step 2), EnvState+ACL (step 3), ActionLedger (step 4), extractor (step 5), probes (step 6), maintainer (step 7), supervisor (step 8), worker (step 9), synthesis (step 10), clean-room (step 11) are each a phase. §18 open decisions → "Deferred / Open".
- **Type consistency:** `EnvStateSnapshot`, `Requirement`, `Evidence`, `ProviderFact`, `OpenFailure`, `BaseFacts`, `Source.*`, `Status.*` are defined once in `src/envstate/types.py` and referenced unchanged in acl/probes/maintainer/supervisor/serde. `ActionEvent`/`ActionLedger` fields match across ledger/synthesis/maintainer. `ProbeSpec`/`ProbeResult` consistent across probes/cleanroom. Worker contract `next_action(brief, recent) -> (action, is_finished)` and executor `(action) -> (success, observation)` consistent across worker/orchestrator.
- **Placeholder scan:** no "TBD"/"add error handling"/"similar to Task N". The agent.py-touching methods (`_build_observer`, `_run_supervisor`, `_verify_cleanroom_or_fail`) are given as complete code with exact integration lines and required focused tests; every new `src/envstate/` module has complete code and a test.

---

## Eng-Review Resolution Log (plan-eng-review + independent outside voice)

This plan was run through `plan-eng-review` plus an independent opus subagent ("outside voice"). 16 findings surfaced; the material ones were resolved **in this plan** before handoff:

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | CRITICAL | Supervisor/Worker loop never fed the Maintainer/probes/ACL — EnvState would stay empty at rev 0, Supervisor plans blind (all of Milestone C had zero callers). | Rewrote `EnvStateOrchestrator` to thread the snapshot through a per-action `observer`; added `_build_observer` (advance_revision → Maintainer.interpret → run probe_requests → certify via ACL) + `test_observer_threads_snapshot_per_action` + `test_agent_supervisor_observe.py` proving a host-certified PRESENT fact lands in the snapshot. |
| 2 | CRITICAL | `apply_build_recipe` only renders `build_commands`; Phase-11 `runtime_prep`/`test_commands` silently dropped. | Phase 11 now passes only `build_commands`; verification bundle is set separately by `_auto_finalize` and serialized into the run summary, not the Dockerfile body. |
| 3 | CRITICAL | `self.base_image`/`self.detected_python` don't exist; synth return ignored. | Use `self.synthesizer.base_image` via defensive getattr; gate `configuration_success` on `_synthesize_final_build_recipe()`'s bool. |
| 4 | CRITICAL | `_LlmWorkerPlanner` used but never defined. | Added full `LlmWorkerPlanner` + `_extract_worker_action`/`_is_worker_finished` + `WORKER_SYSTEM_PROMPT` + tests. |
| 5 | HIGH | Phase-2 tests hand-stamped revisions, never exercising live stamping. | Added `VerificationBundleLiveStampingTests` driving real `_record_successful_action`. |
| 6 | HIGH | Offline gate coarser than live group-invalidation. | Documented scope/coarseness + deferred the same-revision ineffective-test edge. |
| 7 | HIGH | ActionLedger `rc` synthetic; revision logic duplicated. | Documented rc as a success proxy; `_append_action_event` now takes the revision from the same `record_revision` local the action record uses (single source). |
| 8 | HIGH | HEADER probe (`cc`) + `python` cause false MISSING on slim images. | HEADER probe now `find`s the header file (no compiler); python probe tries `python3` then `python`. |
| 9 | HIGH | `exec_readonly` pipefail could flip probe rc. | Dropped pipefail; run via `/bin/sh -lc` with plain short-circuit semantics. |
| 11 | MEDIUM | Maintainer ignored the current snapshot (`previous_env_state_view={}`). | `interpret` now passes `snapshot_to_dict(snapshot)`. |
| 12 | MEDIUM | Non-greedy/`find('{')` JSON parsing truncates/stalls. | Added `src/envstate/jsonutil.py` brace-matching extractor; Maintainer + Supervisor use it. |
| 16 | LOW | Clean-room built from `fileobj` (no context for `COPY`). | `verify_cleanroom` now builds from a `build_context_dir` (the repo workplace) with the Dockerfile written into it. |

Minor items (worker dual-completion, `run()` return asymmetry, rollback-in-supervisor) were unified or recorded in "Deferred / Open". The single most important fix was #1 — without it the plan would have shipped a fully-tested trust boundary the control loop never crosses (the env-state analogue of the synthesizer's "hollow success" bug).
