"""Tests for src/envstate/fullstate_worker.py — written first (RED phase, TDD).

Conventions:
- unittest.TestCase (no pytest fixtures/markers/pytest.*)
- SimpleNamespace fakes
- __new__-bypass where needed
- run via: .venv/bin/python -m pytest tests/test_fullstate_worker.py -q
"""
import unittest
from types import SimpleNamespace

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot(**kwargs):
    """Build a minimal EnvStateSnapshot, overridable via kwargs."""
    defaults = dict(
        revision=3,
        container_id="ctr-abc",
        base=BaseFacts(
            image="python:3.11-slim",
            distro="debian",
            arch="amd64",
            python="3.11.9",
            workdir="/repo",
        ),
        requirements=(),
        provider_facts=(),
        open_failures=(),
        stale_evidence=(),
        plan_notes=(),
        repo_structure="",
    )
    defaults.update(kwargs)
    return EnvStateSnapshot(**defaults)


def _req(id="pkg-a", source=Source.PROBE, status=Status.PRESENT, kind="LanguagePackage",
         specifier=">=1.0", required_by=("test-runner",), evidence=None):
    return Requirement(
        id=id, name=id, kind=kind, status=status, source=source,
        specifier=specifier, required_by=required_by, evidence=evidence,
    )


def _fake_client(content):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_k: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        ))))


# ---------------------------------------------------------------------------
# 1. render_fullstate_view
# ---------------------------------------------------------------------------

class RenderFullstateViewTests(unittest.TestCase):
    """§3.3 — render ALL EnvStateSnapshot fields."""

    def _render(self, snapshot, recent_obs=()):
        from src.envstate.fullstate_worker import render_fullstate_view
        return render_fullstate_view(snapshot, recent_obs)

    def test_includes_revision_and_container_id(self):
        snap = _snapshot(revision=7, container_id="ctr-xyz")
        out = self._render(snap)
        self.assertIn("revision 7", out)
        self.assertIn("ctr-xyz", out)

    def test_includes_base_facts(self):
        snap = _snapshot()
        out = self._render(snap)
        self.assertIn("python:3.11-slim", out)
        self.assertIn("3.11.9", out)
        self.assertIn("debian", out)
        self.assertIn("amd64", out)
        self.assertIn("/repo", out)

    def test_certified_probe_tag_for_probe_source_at_current_revision(self):
        req = _req(source=Source.PROBE, status=Status.PRESENT)
        snap = _snapshot(requirements=(req,))
        out = self._render(snap)
        self.assertIn("CERTIFIED-PROBE", out)
        # hypothesis tag must NOT appear for certified
        self.assertNotIn("hypothesis(PROBE)", out)

    def test_hypothesis_tag_for_non_probe_source(self):
        req = _req(id="guess-pkg", source=Source.LLM_GUESS, status=Status.REQUIRED)
        snap = _snapshot(requirements=(req,))
        out = self._render(snap)
        self.assertIn("hypothesis(LLM_GUESS)", out)
        self.assertNotIn("CERTIFIED-PROBE", out)

    def test_hypothesis_tag_for_static_scan_source(self):
        req = _req(id="static-pkg", source=Source.STATIC_SCAN, status=Status.MISSING)
        snap = _snapshot(requirements=(req,))
        out = self._render(snap)
        self.assertIn("hypothesis(STATIC_SCAN)", out)

    def test_requirement_status_in_output(self):
        req = _req(id="my-pkg", source=Source.PROBE, status=Status.PRESENT)
        snap = _snapshot(requirements=(req,))
        out = self._render(snap)
        self.assertIn("PRESENT", out)
        self.assertIn("my-pkg", out)

    def test_requirement_specifier_in_output(self):
        req = _req(id="versioned", source=Source.LLM_GUESS, status=Status.REQUIRED, specifier=">=2.0")
        snap = _snapshot(requirements=(req,))
        out = self._render(snap)
        self.assertIn(">=2.0", out)

    def test_requirement_required_by_in_output(self):
        req = _req(id="dep", source=Source.PROBE, status=Status.PRESENT,
                   required_by=("pytest", "myapp"))
        snap = _snapshot(requirements=(req,))
        out = self._render(snap)
        self.assertIn("pytest", out)
        self.assertIn("myapp", out)

    def test_provider_facts_included(self):
        pf = ProviderFact(provider="libpq-dev", provides=("psycopg2",), source=Source.DIAGNOSE)
        snap = _snapshot(provider_facts=(pf,))
        out = self._render(snap)
        self.assertIn("libpq-dev", out)
        self.assertIn("psycopg2", out)

    def test_open_failures_included(self):
        fail = OpenFailure(
            signature="ModuleNotFoundError: numpy",
            first_seen_revision=1, last_seen_revision=3,
            hypothesis="numpy not installed",
            already_tried=("pip install numpy",),
        )
        snap = _snapshot(open_failures=(fail,))
        out = self._render(snap)
        self.assertIn("ModuleNotFoundError: numpy", out)
        self.assertIn("numpy not installed", out)
        self.assertIn("pip install numpy", out)

    def test_stale_evidence_section_present_when_nonempty(self):
        req = _req(id="stale-req", source=Source.PROBE, status=Status.PRESENT)
        snap = _snapshot(stale_evidence=(req,))
        out = self._render(snap)
        # stale_evidence section should be rendered
        self.assertIn("stale-req", out)

    def test_plan_notes_included(self):
        snap = _snapshot(plan_notes=("Try apt first", "Then pip"))
        out = self._render(snap)
        self.assertIn("Try apt first", out)
        self.assertIn("Then pip", out)

    def test_repo_structure_included_and_truncated(self):
        # 200 lines — should be truncated to ~120
        lines = [f"file_{i}.py" for i in range(200)]
        snap = _snapshot(repo_structure="\n".join(lines))
        out = self._render(snap)
        # First file visible
        self.assertIn("file_0.py", out)
        # Line beyond cap should not appear
        self.assertNotIn("file_199.py", out)
        # Truncation marker present
        self.assertIn("truncated", out)

    def test_repo_structure_omitted_when_empty(self):
        snap = _snapshot(repo_structure="")
        out = self._render(snap)
        self.assertNotIn("Repository Layout", out)

    def test_last_3_observations_included(self):
        obs = [
            (True, "installed ok"),
            (False, "error: missing lib"),
            (True, "probe passed"),
        ]
        snap = _snapshot()
        out = self._render(snap, recent_obs=obs)
        self.assertIn("installed ok", out)
        self.assertIn("error: missing lib", out)
        self.assertIn("probe passed", out)

    def test_only_last_3_observations_rendered(self):
        # 5 observations — only last 3 should appear
        obs = [
            (True, "obs-1-should-not-appear"),
            (True, "obs-2-should-not-appear"),
            (True, "obs-3-yes"),
            (False, "obs-4-yes"),
            (True, "obs-5-yes"),
        ]
        snap = _snapshot()
        out = self._render(snap, recent_obs=obs)
        self.assertNotIn("obs-1-should-not-appear", out)
        self.assertNotIn("obs-2-should-not-appear", out)
        self.assertIn("obs-3-yes", out)
        self.assertIn("obs-4-yes", out)
        self.assertIn("obs-5-yes", out)

    def test_empty_sections_omitted_cleanly(self):
        snap = _snapshot()  # no requirements, no failures, etc.
        out = self._render(snap)
        # Should render without error; check minimal structure
        self.assertIn("revision", out)
        self.assertIn("python:3.11-slim", out)

    def test_evidence_detail_in_requirement_when_present(self):
        ev = Evidence(probe_cmd="python -c 'import numpy'", rc=0,
                      stdout_predicate="", env_revision=3, container_id="ctr-abc")
        req = _req(id="numpy", source=Source.PROBE, status=Status.PRESENT, evidence=ev)
        snap = _snapshot(requirements=(req,))
        out = self._render(snap)
        # The requirement should still show CERTIFIED-PROBE with evidence
        self.assertIn("CERTIFIED-PROBE", out)
        self.assertIn("numpy", out)


# ---------------------------------------------------------------------------
# 2. FULLSTATE_WORKER_SYSTEM_PROMPT content checks
# ---------------------------------------------------------------------------

class FullstateWorkerPromptTests(unittest.TestCase):
    def test_prompt_exists_and_is_string(self):
        from src.envstate.fullstate_worker import FULLSTATE_WORKER_SYSTEM_PROMPT
        self.assertIsInstance(FULLSTATE_WORKER_SYSTEM_PROMPT, str)
        self.assertGreater(len(FULLSTATE_WORKER_SYSTEM_PROMPT), 50)

    def test_prompt_has_layered_rca_layers(self):
        from src.envstate.fullstate_worker import FULLSTATE_WORKER_SYSTEM_PROMPT
        p = FULLSTATE_WORKER_SYSTEM_PROMPT.lower()
        # Must mention layered RCA layers (§3.4)
        for layer in ("base", "system", "runtime", "deps", "build", "tests"):
            self.assertIn(layer, p, f"Layer '{layer}' missing from FULLSTATE_WORKER_SYSTEM_PROMPT")

    def test_prompt_has_trust_rules(self):
        from src.envstate.fullstate_worker import FULLSTATE_WORKER_SYSTEM_PROMPT
        p = FULLSTATE_WORKER_SYSTEM_PROMPT
        # Trust rules from §3.4: CERTIFIED-PROBE is TRUE, do not re-install PRESENT
        self.assertIn("CERTIFIED-PROBE", p)

    def test_prompt_has_final_answer_completion_contract(self):
        from src.envstate.fullstate_worker import FULLSTATE_WORKER_SYSTEM_PROMPT
        p = FULLSTATE_WORKER_SYSTEM_PROMPT
        self.assertIn("Final Answer: Success", p)
        self.assertIn("Thought:", p)
        self.assertIn("Action:", p)

    @unittest.skip("worker.py removed — WORKER_SYSTEM_PROMPT deleted in Task 37")
    def test_prompt_does_not_mutate_worker_prompt(self):
        raise NotImplementedError("worker.py deleted")

    def test_prompt_mentions_do_not_paper_over(self):
        from src.envstate.fullstate_worker import FULLSTATE_WORKER_SYSTEM_PROMPT
        p = FULLSTATE_WORKER_SYSTEM_PROMPT.lower()
        self.assertIn("paper over", p)


# ---------------------------------------------------------------------------
# 3. FullStateWorkerPlanner
# ---------------------------------------------------------------------------

class FullStateWorkerPlannerTests(unittest.TestCase):
    def _planner(self, content="Thought: ok\nAction: ls", get_snapshot=None):
        from src.envstate.fullstate_worker import FullStateWorkerPlanner
        client = _fake_client(content)
        snap = _snapshot()
        return FullStateWorkerPlanner(
            client=client,
            model="test-model",
            get_snapshot=get_snapshot or (lambda: snap),
        )

    def test_next_action_returns_action_and_not_finished(self):
        planner = self._planner("Thought: look\nAction: ls /repo")
        action, finished = planner.next_action("brief: install deps", [])
        self.assertEqual(action, "ls /repo")
        self.assertFalse(finished)

    def test_next_action_returns_finished_on_final_answer(self):
        planner = self._planner("Thought: done\nFinal Answer: Success")
        action, finished = planner.next_action("brief", [])
        self.assertEqual(action, "")
        self.assertTrue(finished)

    def test_next_action_user_message_contains_rendered_snapshot(self):
        """The user message must contain the rendered fullstate view (§3.3/§3.8)."""
        captured = []

        def fake_create(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="Thought: x\nAction: echo hi"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        from src.envstate.fullstate_worker import FullStateWorkerPlanner
        snap = _snapshot(
            revision=9,
            requirements=(_req(id="numpy", source=Source.PROBE, status=Status.PRESENT),),
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=fake_create)))
        planner = FullStateWorkerPlanner(client=client, model="m", get_snapshot=lambda: snap)
        planner.next_action("task: install numpy", [])

        self.assertEqual(len(captured), 1)
        msgs = captured[0]["messages"]
        # Find user message
        user_msgs = [m for m in msgs if m["role"] == "user"]
        self.assertGreater(len(user_msgs), 0)
        user_content = user_msgs[0]["content"]
        # Must contain snapshot render (revision) AND task brief
        self.assertIn("revision 9", user_content)
        self.assertIn("task: install numpy", user_content)

    def test_next_action_user_message_contains_task_brief(self):
        captured = []

        def fake_create(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="Thought: x\nAction: ls"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        from src.envstate.fullstate_worker import FullStateWorkerPlanner
        snap = _snapshot()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=fake_create)))
        planner = FullStateWorkerPlanner(client=client, model="m", get_snapshot=lambda: snap)
        planner.next_action("UNIQUE_TASK_BRIEF_HERE", [])

        user_msgs = [m for m in captured[0]["messages"] if m["role"] == "user"]
        self.assertIn("UNIQUE_TASK_BRIEF_HERE", user_msgs[0]["content"])

    def test_reset_clears_history(self):
        planner = self._planner("Thought: x\nAction: ls")
        planner.next_action("brief", [])
        self.assertGreater(len(planner.history), 0)
        planner.reset()
        self.assertEqual(planner.history, [])

    def test_empty_content_returns_empty_action(self):
        """Empty/unparseable response yields empty action, not finished (§3.9)."""
        planner = self._planner("")
        action, finished = planner.next_action("brief", [])
        self.assertEqual(action, "")
        self.assertFalse(finished)

    def test_on_usage_callback_called(self):
        seen = []
        from src.envstate.fullstate_worker import FullStateWorkerPlanner
        snap = _snapshot()
        planner = FullStateWorkerPlanner(
            client=_fake_client("Thought: x\nAction: ls"),
            model="m",
            get_snapshot=lambda: snap,
            on_usage=seen.append,
        )
        planner.next_action("brief", [])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["total_tokens"], 8)

    def test_system_prompt_is_fullstate_prompt(self):
        """FullStateWorkerPlanner must use FULLSTATE_WORKER_SYSTEM_PROMPT."""
        captured = []

        def fake_create(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Thought: x\nAction: ls"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        from src.envstate.fullstate_worker import FullStateWorkerPlanner, FULLSTATE_WORKER_SYSTEM_PROMPT
        snap = _snapshot()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
        planner = FullStateWorkerPlanner(client=client, model="m", get_snapshot=lambda: snap)
        planner.next_action("brief", [])

        sys_msgs = [m for m in captured[0]["messages"] if m["role"] == "system"]
        self.assertEqual(len(sys_msgs), 1)
        self.assertEqual(sys_msgs[0]["content"], FULLSTATE_WORKER_SYSTEM_PROMPT)
        # WORKER_SYSTEM_PROMPT comparison removed — worker.py deleted in Task 37


# ---------------------------------------------------------------------------
# 4. run_fullstate_loop
# ---------------------------------------------------------------------------

class RunFullstateLoopTests(unittest.TestCase):
    """§3.5 + §3.9: single ReAct loop, global action budget, Final Answer stop."""

    def _make_planner(self, steps):
        """Fake planner: steps is list of (action, is_finished)."""
        class _FakePlanner:
            def __init__(self, steps):
                self.steps = list(steps)
                self.calls = []

            def reset(self):
                pass

            def next_action(self, brief, recent_obs):
                call = (brief, list(recent_obs))
                self.calls.append(call)
                return self.steps.pop(0)

        return _FakePlanner(steps)

    def _make_step_fn(self, results):
        """Fake step_fn: pops (success, observation) from results."""
        results = list(results)
        calls = []

        def step_fn(action):
            calls.append(action)
            return results.pop(0)

        step_fn.calls = calls
        return step_fn

    def test_stops_on_final_answer(self):
        from src.envstate.fullstate_worker import run_fullstate_loop
        from src.envstate.build_agent import interruption_decision
        planner = self._make_planner([
            ("ls /repo", False),
            ("", True),  # Final Answer
        ])
        step_fn = self._make_step_fn([(True, "ok")])
        snap = _snapshot()
        report = run_fullstate_loop(
            planner=planner,
            get_snapshot=lambda: snap,
            step_fn=step_fn,
            global_action_budget=180,
            interruption_decision=interruption_decision,
        )
        self.assertEqual(report.status, "complete")
        self.assertEqual(len(step_fn.calls), 1)

    def test_stops_at_global_action_budget(self):
        from src.envstate.fullstate_worker import run_fullstate_loop
        from src.envstate.build_agent import interruption_decision
        # 10 actions provided, budget = 3 → stops after 3
        planner = self._make_planner([("cmd", False)] * 10)
        step_fn = self._make_step_fn([(True, f"obs-{i}") for i in range(10)])
        snap = _snapshot()
        report = run_fullstate_loop(
            planner=planner,
            get_snapshot=lambda: snap,
            step_fn=step_fn,
            global_action_budget=3,
            interruption_decision=interruption_decision,
        )
        self.assertLessEqual(len(step_fn.calls), 3)
        self.assertIn(report.status, ("blocked", "complete"))

    def test_never_executes_empty_action(self):
        from src.envstate.fullstate_worker import run_fullstate_loop
        from src.envstate.build_agent import interruption_decision, MAX_EMPTY_PLANNER_RESPONSES
        # planner returns one empty then real action then finish
        # (one re-prompt is allowed; the real-cmd must execute, not the empty)
        planner = self._make_planner([
            ("", False),           # empty — re-prompt, not executed
            ("real-cmd", False),   # first real action
            ("", True),            # Final Answer
        ])
        step_fn = self._make_step_fn([(True, "ok")])
        snap = _snapshot()
        report = run_fullstate_loop(
            planner=planner,
            get_snapshot=lambda: snap,
            step_fn=step_fn,
            global_action_budget=180,
            interruption_decision=interruption_decision,
        )
        # The empty string must NOT have been executed
        self.assertNotIn("", step_fn.calls)
        # The real command must have been executed
        self.assertEqual(step_fn.calls, ["real-cmd"])

    def test_blocks_after_too_many_empty_responses(self):
        from src.envstate.fullstate_worker import run_fullstate_loop
        from src.envstate.build_agent import interruption_decision, MAX_EMPTY_PLANNER_RESPONSES
        # All empty responses
        planner = self._make_planner([("", False)] * 10)
        step_fn = self._make_step_fn([])
        snap = _snapshot()
        report = run_fullstate_loop(
            planner=planner,
            get_snapshot=lambda: snap,
            step_fn=step_fn,
            global_action_budget=180,
            interruption_decision=interruption_decision,
        )
        self.assertEqual(report.status, "blocked")
        self.assertEqual(len(step_fn.calls), 0)

    def test_returns_worker_report(self):
        from src.envstate.fullstate_worker import run_fullstate_loop
        from src.envstate.build_agent import WorkerReport, interruption_decision
        planner = self._make_planner([("echo hi", False), ("", True)])
        step_fn = self._make_step_fn([(True, "hi")])
        snap = _snapshot()
        report = run_fullstate_loop(
            planner=planner,
            get_snapshot=lambda: snap,
            step_fn=step_fn,
            global_action_budget=180,
            interruption_decision=interruption_decision,
        )
        self.assertIsInstance(report, WorkerReport)

    def test_interruption_decision_called_and_respected(self):
        """interruption_decision fires → loop stops without executing the action."""
        from src.envstate.fullstate_worker import run_fullstate_loop

        interrupt_calls = []

        def always_interrupt(recent_window, action):
            interrupt_calls.append((recent_window, action))
            return True

        planner = self._make_planner([("dangerous-cmd", False)] * 5)
        step_fn = self._make_step_fn([(True, "ok")] * 5)
        snap = _snapshot()
        report = run_fullstate_loop(
            planner=planner,
            get_snapshot=lambda: snap,
            step_fn=step_fn,
            global_action_budget=180,
            interruption_decision=always_interrupt,
        )
        # Should not have executed the command
        self.assertEqual(step_fn.calls, [])
        self.assertEqual(report.status, "interrupted")
        self.assertGreater(len(interrupt_calls), 0)

    def test_recent_observations_passed_as_rolling_last_3(self):
        """Planner receives rolling last-3 obs; loop tracks them correctly."""
        from src.envstate.fullstate_worker import run_fullstate_loop
        from src.envstate.build_agent import interruption_decision

        observation_windows = []

        class _RecordingPlanner:
            def __init__(self):
                self._steps = [("cmd1", False), ("cmd2", False), ("cmd3", False), ("", True)]

            def reset(self):
                pass

            def next_action(self, brief, recent_obs):
                observation_windows.append(list(recent_obs))
                return self._steps.pop(0)

        step_fn = self._make_step_fn([(True, f"obs-{i}") for i in range(3)])
        snap = _snapshot()
        report = run_fullstate_loop(
            planner=_RecordingPlanner(),
            get_snapshot=lambda: snap,
            step_fn=step_fn,
            global_action_budget=180,
            interruption_decision=interruption_decision,
        )
        # Last call before "Final Answer" should have seen at most 3 obs
        for window in observation_windows:
            self.assertLessEqual(len(window), 3)


# ---------------------------------------------------------------------------
# 5. interruption_decision — extracted shared function in worker.py
# ---------------------------------------------------------------------------

class InterruptionDecisionTests(unittest.TestCase):
    """Parity: interruption_decision(recent_window, action) -> bool.
    Must match should_interrupt's repeated-failure window logic exactly.
    """

    def test_fires_on_repeated_identical_failure(self):
        from src.envstate.build_agent import interruption_decision
        recent = [
            (False, "Error: pg_config executable not found"),
            (False, "Error: pg_config executable not found"),
        ]
        self.assertTrue(interruption_decision(recent, "pip install psycopg2"))

    def test_no_fire_when_failures_differ(self):
        from src.envstate.build_agent import interruption_decision
        recent = [
            (False, "Error: pg_config not found"),
            (False, "Error: different error"),
        ]
        self.assertFalse(interruption_decision(recent, "apt-get install -y libpq-dev"))

    def test_no_fire_when_less_than_2_failures(self):
        from src.envstate.build_agent import interruption_decision
        recent = [(False, "Error: something")]
        self.assertFalse(interruption_decision(recent, "apt-get install -y libpq-dev"))

    def test_no_fire_when_observations_empty(self):
        from src.envstate.build_agent import interruption_decision
        self.assertFalse(interruption_decision([], "any-action"))

    def test_no_fire_when_last_two_successes(self):
        from src.envstate.build_agent import interruption_decision
        recent = [(True, "ok"), (True, "ok")]
        self.assertFalse(interruption_decision(recent, "ls"))

    def test_pin_edit_is_NOT_detected_by_interruption_decision(self):
        """interruption_decision only handles the repeated-failure guard.
        Pin-edit detection stays in should_interrupt (via _looks_like_pin_edit).
        """
        from src.envstate.build_agent import interruption_decision
        # repeated-failure rule does NOT apply here since observations are empty
        result = interruption_decision([], "sed -i 's/1.0/2.0/' requirements.txt")
        self.assertFalse(result)

    @unittest.skip("worker.py removed — should_interrupt deleted in Task 37")
    def test_should_interrupt_still_uses_interruption_decision_logic(self):
        raise NotImplementedError("worker.py deleted")

    @unittest.skip("worker.py removed — should_interrupt deleted in Task 37")
    def test_should_interrupt_budget_unchanged(self):
        raise NotImplementedError("worker.py deleted")

    @unittest.skip("worker.py removed — should_interrupt deleted in Task 37")
    def test_should_interrupt_pin_edit_unchanged(self):
        raise NotImplementedError("worker.py deleted")

    @unittest.skip("worker.py removed — should_interrupt deleted in Task 37")
    def test_should_interrupt_normal_action_unchanged(self):
        raise NotImplementedError("worker.py deleted")


# ---------------------------------------------------------------------------
# 6. orchestrator global_action_budget
# ---------------------------------------------------------------------------

class OrchestratorGlobalBudgetTests(unittest.TestCase):

    def _snapshot(self):
        return EnvStateSnapshot(revision=0, container_id="c1",
                                base=BaseFacts(image="python:3.11-slim"))

    def _noop_observer(self, snapshot, task_spec, step, action, success, observation):
        return snapshot

    def test_global_action_budget_none_is_noop(self):
        """Existing behavior: no budget → runs until no_more_tasks."""
        from src.envstate.orchestrator import EnvStateOrchestrator
        from src.envstate.build_agent import WorkerReport
        from test_envstate_orchestrator import FakeSupervisor, FakeWorker

        supervisor = FakeSupervisor([
            {"task_id": "t1", "phase": "x", "goal": "g", "success_criteria": []},
        ])
        worker = FakeWorker([WorkerReport("t1", "complete", "done")])
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=__import__("src.envstate.ledger", fromlist=["ActionLedger"]).ActionLedger(),
            executor=lambda a: (True, "ok"), observer=self._noop_observer,
            max_tasks=10, global_action_budget=None,
        )
        result = orch.run()
        self.assertEqual(result["tasks_completed"], 1)
        self.assertEqual(result["stop_reason"], "no_more_tasks")

    def test_global_action_budget_breaks_at_cap(self):
        """When the action counter reaches the budget, the loop breaks."""
        from src.envstate.orchestrator import EnvStateOrchestrator
        from src.envstate.build_agent import WorkerReport

        # Each worker call executes exactly 1 action (FakeWorkerExecutesActions).
        # We supply many tasks but set budget=2 → should stop after 2 actions.
        exec_count = [0]

        class CountingWorker:
            def run_task(self, task_spec, step_fn):
                # Execute exactly 1 action per task
                step_fn("echo hi")
                exec_count[0] += 1
                return WorkerReport(task_spec["task_id"], "complete", "done")

        tasks = [{"task_id": f"t{i}", "phase": "x", "goal": "g", "success_criteria": []}
                 for i in range(20)]

        from test_envstate_orchestrator import FakeSupervisor
        from src.envstate.ledger import ActionLedger

        supervisor = FakeSupervisor(tasks)
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=CountingWorker(),
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=self._noop_observer,
            max_tasks=20, global_action_budget=2,
        )
        result = orch.run()
        # The loop should have stopped after budget exhausted
        self.assertLessEqual(exec_count[0], 3)  # at most one extra task starts
        # stop_reason should reflect budget cap
        self.assertIn(result["stop_reason"], ("global_action_budget", "max_tasks", "no_more_tasks"))

    def test_global_action_budget_breaks_stop_reason(self):
        """stop_reason is 'global_action_budget' when that fires."""
        from src.envstate.orchestrator import EnvStateOrchestrator
        from src.envstate.build_agent import WorkerReport
        from test_envstate_orchestrator import FakeSupervisor
        from src.envstate.ledger import ActionLedger

        class OneActionWorker:
            def run_task(self, task_spec, step_fn):
                step_fn("echo hi")
                return WorkerReport(task_spec["task_id"], "complete", "done")

        tasks = [{"task_id": f"t{i}", "phase": "x", "goal": "g", "success_criteria": []}
                 for i in range(20)]
        supervisor = FakeSupervisor(tasks)
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=OneActionWorker(),
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=self._noop_observer,
            max_tasks=20, global_action_budget=1,
        )
        result = orch.run()
        self.assertEqual(result["stop_reason"], "global_action_budget")

    def test_existing_orchestrator_tests_still_pass(self):
        """Regression: original tests unaffected (no global_action_budget kwarg)."""
        from src.envstate.orchestrator import EnvStateOrchestrator
        from src.envstate.build_agent import WorkerReport
        from test_envstate_orchestrator import FakeSupervisor, FakeWorker
        from src.envstate.ledger import ActionLedger

        supervisor = FakeSupervisor([
            {"task_id": "t1", "phase": "x", "goal": "g", "success_criteria": []},
        ])
        worker = FakeWorker([WorkerReport("t1", "complete", "done")])
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=self._noop_observer,
            max_tasks=10,  # no global_action_budget → default None
        )
        result = orch.run()
        self.assertEqual(result["tasks_completed"], 1)


if __name__ == "__main__":
    unittest.main()
