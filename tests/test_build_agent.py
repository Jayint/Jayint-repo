"""tests/test_build_agent.py — TDD for src/envstate/build_agent.py (v1 BuildAgent).

Run with:
    .venv/bin/python -m pytest tests/test_build_agent.py -q
"""
import unittest
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers shared across all test classes
# ---------------------------------------------------------------------------

def _make_task(
    goal="install project deps",
    done_when="pip install exits 0",
    layer="deps",
    facts=("base_image=python:3.12",),
):
    """Build a Task dataclass from world_model.py."""
    from src.envstate.world_model import Task
    return Task(goal=goal, done_when=done_when, layer=layer, facts=facts)


def _make_ledger():
    from src.envstate.ledger import ActionLedger
    return ActionLedger()


def _fake_response(content: str):
    """Return a minimal OpenAI-compatible response object."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _fake_client_seq(contents):
    """Client whose .chat.completions.create pops from a sequence of content strings."""
    contents = list(contents)

    class _FakeCompletions:
        def create(self, **kwargs):
            return _fake_response(contents.pop(0))

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    return _FakeClient()


# ---------------------------------------------------------------------------
# 1. Module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants(unittest.TestCase):
    def test_local_budget_default_is_8(self):
        from src.envstate import build_agent
        self.assertEqual(build_agent.LOCAL_BUDGET, 8)

    def test_max_empty_responses_default_is_2(self):
        from src.envstate import build_agent
        self.assertEqual(build_agent.MAX_EMPTY_RESPONSES, 2)


# ---------------------------------------------------------------------------
# 2. Action-parsing helpers (ported from worker.py)
# ---------------------------------------------------------------------------

class TestExtractWorkerAction(unittest.TestCase):
    def _extract(self, content: str) -> str:
        from src.envstate.build_agent import _extract_worker_action
        return _extract_worker_action(content)

    def test_plain_action_line(self):
        out = self._extract("Thought: install\nAction: apt-get install -y libpq-dev")
        self.assertEqual(out, "apt-get install -y libpq-dev")

    def test_strips_backtick_fencing(self):
        out = self._extract("Thought: ok\nAction: ```bash\npip install flask\n```")
        self.assertEqual(out, "pip install flask")

    def test_toolcall_xml_format(self):
        content = (
            "<invoke>\n"
            '<parameter name="command">pip install psycopg2</parameter>\n'
            "</invoke>"
        )
        out = self._extract(content)
        self.assertEqual(out, "pip install psycopg2")

    def test_empty_content_returns_empty_string(self):
        self.assertEqual(self._extract(""), "")

    def test_none_returns_empty_string(self):
        from src.envstate.build_agent import _extract_worker_action
        self.assertEqual(_extract_worker_action(None), "")

    def test_multiline_action_takes_first_line(self):
        out = self._extract("Action: echo hello\nworld")
        self.assertEqual(out, "echo hello")


class TestIsWorkerFinished(unittest.TestCase):
    def _finished(self, content: str) -> bool:
        from src.envstate.build_agent import _is_worker_finished
        return _is_worker_finished(content)

    def test_final_answer_success(self):
        self.assertTrue(self._finished("Thought: done\nFinal Answer: Success"))

    def test_final_answer_case_insensitive(self):
        self.assertTrue(self._finished("Final answer: success"))

    def test_not_finished_on_action_line(self):
        self.assertFalse(self._finished("Thought: ok\nAction: ls"))

    def test_empty_returns_false(self):
        self.assertFalse(self._finished(""))

    def test_final_answer_failure_not_finished(self):
        # "Final Answer: Failure" must NOT be treated as completion
        self.assertFalse(self._finished("Final Answer: Failure"))


# ---------------------------------------------------------------------------
# 3. Fixed stuck guard (_is_stuck) — spec §6
# ---------------------------------------------------------------------------

class TestIsStuck(unittest.TestCase):
    """The guard must fire only when ≥2 real mutating failures share identical output.
    Preflight rejections must be ignored entirely.
    One self-correction attempt must be allowed before the guard fires (≥2 real
    failures required, not 1).
    """

    def _stuck(
        self,
        history: list,
        action: str = "pip install x",
        is_preflight: bool = False,
    ) -> bool:
        from src.envstate.build_agent import _is_stuck, CommandRecord
        # history items are (cmd, rc, output) tuples for brevity
        records = [CommandRecord(cmd=c, rc=r, output=o) for c, r, o in history]
        return _is_stuck(records, action, is_preflight)

    def test_two_identical_real_failures_fires(self):
        """Two consecutive identical-output real failures → stuck."""
        err = "ERROR: Could not find a version that satisfies psycopg2"
        hist = [
            ("pip install psycopg2", 1, err),
            ("pip install psycopg2==2.8", 1, err),
        ]
        self.assertTrue(self._stuck(hist))

    def test_two_different_failures_does_not_fire(self):
        hist = [
            ("pip install psycopg2", 1, "ERROR: pg_config not found"),
            ("pip install psycopg2", 1, "ERROR: different error"),
        ]
        self.assertFalse(self._stuck(hist))

    def test_only_one_failure_does_not_fire(self):
        hist = [("pip install psycopg2", 1, "ERROR: pg_config not found")]
        self.assertFalse(self._stuck(hist))

    def test_empty_history_does_not_fire(self):
        self.assertFalse(self._stuck([]))

    def test_two_successes_does_not_fire(self):
        hist = [
            ("pip install flask", 0, "Successfully installed flask"),
            ("pip install flask", 0, "Successfully installed flask"),
        ]
        self.assertFalse(self._stuck(hist))

    def test_preflight_rejection_ignored_entirely(self):
        """Preflight rejection must NOT count toward the stuck counter."""
        rejection = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup commands must not pipe"
        hist = [
            ("pip install x | head", 1, rejection),
            ("pip install x | head", 1, rejection),
        ]
        # Both are preflight rejections in history; is_preflight=True for
        # the current action too.
        self.assertFalse(self._stuck(hist, is_preflight=True))

    def test_preflight_rejection_in_history_not_counted(self):
        """A preflight rejection in history must NOT count as a real failure.

        Scenario: action 1 is a preflight rejection (never executed), action 2
        is a real failure.  The guard must NOT fire after only 1 real failure.
        """
        rejection = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup"
        real_err = "ERROR: some real failure"
        hist = [
            ("pip install x | head", 1, rejection),   # preflight — does not count
            ("pip install x", 1, real_err),            # real failure (count=1)
        ]
        self.assertFalse(self._stuck(hist))

    def test_one_self_correction_allowed_before_firing(self):
        """Guard must NOT fire after just 1 real failure; one self-correction is
        allowed before the guard triggers (spec §6: ≥2 real failures required)."""
        err = "ERROR: pg_config not found"
        hist = [("pip install psycopg2", 1, err)]  # only 1 real failure
        self.assertFalse(self._stuck(hist))

    def test_mixed_preflight_and_real_fires_only_after_two_real(self):
        """Two real identical failures with a preflight in between still fires."""
        rejection = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: compound"
        real_err = "ERROR: pg_config not found"
        hist = [
            ("pip install psycopg2", 1, real_err),       # real failure 1
            ("pip install x | head", 1, rejection),       # preflight (ignored)
            ("pip install psycopg2", 1, real_err),       # real failure 2 (same)
        ]
        self.assertTrue(self._stuck(hist))

    def test_is_preflight_true_bypasses_counter(self):
        """When the CURRENT action is a preflight rejection, guard must return False."""
        err = "ERROR: pg_config not found"
        hist = [
            ("pip install psycopg2", 1, err),
            ("pip install psycopg2", 1, err),
        ]
        # Even though history has 2 identical real failures, if the NEW action
        # is a preflight rejection we skip the guard.
        self.assertFalse(self._stuck(hist, is_preflight=True))


# ---------------------------------------------------------------------------
# 4. BuildAgent.run — success / "done" path
# ---------------------------------------------------------------------------

class _FakeSynthesizer:
    """Minimal Synthesizer stand-in: all commands mutate, class='other_mutation'."""
    def command_mutates_environment(self, command: str) -> bool:
        return True
    def classify_mutation(self, command: str) -> str:
        return "other_mutation"


def _make_agent(client, container_id="ctr-test"):
    from src.envstate.build_agent import BuildAgent
    return BuildAgent(
        client=client,
        model="test-model",
        synthesizer=_FakeSynthesizer(),
        container_id=container_id,
    )


class TestBuildAgentRunDone(unittest.TestCase):
    """BuildAgent.run returns TaskReport(status='done') when LLM emits Final Answer: Success."""

    def test_returns_done_when_llm_signals_finished_immediately(self):
        """LLM says 'Final Answer: Success' on the very first step — no sandbox calls needed."""
        client = _fake_client_seq(["Thought: done\nFinal Answer: Success"])
        sandbox_calls = []

        def sandbox(cmd):
            sandbox_calls.append(cmd)
            return True, "ok"

        task = _make_task()
        ledger = _make_ledger()
        agent = _make_agent(client)
        report = agent.run(task, sandbox, ledger)

        self.assertEqual(report.status, "done")
        self.assertEqual(report.task_goal, task.goal)
        self.assertEqual(len(sandbox_calls), 0, "No sandbox call when done immediately")

    def test_returns_done_after_one_successful_action(self):
        """LLM executes one command then signals done."""
        client = _fake_client_seq([
            "Thought: install\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, f"Installed {cmd}")

        task = _make_task()
        ledger = _make_ledger()
        agent = _make_agent(client)
        report = agent.run(task, sandbox, ledger)

        self.assertEqual(report.status, "done")
        self.assertEqual(len(report.commands), 1)
        self.assertEqual(report.commands[0].cmd, "pip install flask")
        self.assertEqual(report.commands[0].rc, 0)

    def test_done_report_contains_learning(self):
        client = _fake_client_seq([
            "Thought: ok\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, "ok")

        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertIsInstance(report.learning, str)
        self.assertGreater(len(report.learning), 0)

    def test_done_report_task_goal_matches_task(self):
        task = _make_task(goal="install flask and psycopg2")
        client = _fake_client_seq(["Thought: done\nFinal Answer: Success"])
        report = _make_agent(client).run(task, lambda cmd: (True, "ok"), _make_ledger())
        self.assertEqual(report.task_goal, "install flask and psycopg2")

    def test_commands_tuple_is_frozen(self):
        """TaskReport.commands must be a tuple (frozen), not a list."""
        client = _fake_client_seq([
            "Thought: x\nAction: ls",
            "Thought: done\nFinal Answer: Success",
        ])
        report = _make_agent(client).run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())
        self.assertIsInstance(report.commands, tuple)


# ---------------------------------------------------------------------------
# 5. BuildAgent.run — budget exhaustion path (returns "blocked")
# ---------------------------------------------------------------------------

class TestBuildAgentRunBlocked(unittest.TestCase):

    def test_blocked_at_local_budget(self):
        """After LOCAL_BUDGET actions without 'Final Answer', status must be 'blocked'."""
        from src.envstate.build_agent import LOCAL_BUDGET
        # Provide one more LLM response than the budget so the loop always has a response.
        contents = [f"Thought: step {i}\nAction: pip install pkg{i}" for i in range(LOCAL_BUDGET + 1)]
        client = _fake_client_seq(contents)
        sandbox_calls = []

        def sandbox(cmd):
            sandbox_calls.append(cmd)
            return False, f"ERROR: install failed for {cmd}"

        task = _make_task()
        ledger = _make_ledger()
        report = _make_agent(client).run(task, sandbox, ledger)

        self.assertEqual(report.status, "blocked")
        self.assertLessEqual(len(sandbox_calls), LOCAL_BUDGET)

    def test_blocked_report_contains_commands(self):
        """commands tuple must contain all executed actions."""
        from src.envstate.build_agent import LOCAL_BUDGET
        contents = [f"Thought: x\nAction: cmd{i}" for i in range(LOCAL_BUDGET + 1)]
        client = _fake_client_seq(contents)
        sandbox = lambda cmd: (False, "ERROR: failure")
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertIsInstance(report.commands, tuple)
        self.assertGreater(len(report.commands), 0)

    def test_blocked_after_too_many_empty_responses(self):
        """MAX_EMPTY_RESPONSES consecutive empty responses → status 'blocked'."""
        from src.envstate.build_agent import MAX_EMPTY_RESPONSES
        # All responses are empty/unparseable (no Action line, no Final Answer)
        contents = ["Thought: hmm" for _ in range(MAX_EMPTY_RESPONSES + 2)]
        client = _fake_client_seq(contents)
        sandbox_calls = []

        def sandbox(cmd):
            sandbox_calls.append(cmd)
            return True, "ok"

        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertEqual(report.status, "blocked")
        # No sandbox calls because empty responses never produce an action
        self.assertEqual(sandbox_calls, [])

    def test_empty_response_counter_resets_on_real_action(self):
        """One empty response followed by a real action must NOT trigger the guard."""
        from src.envstate.build_agent import MAX_EMPTY_RESPONSES
        contents = [
            "Thought: hmm",                            # empty — counter = 1
            "Thought: ok\nAction: pip install flask",  # real action — counter resets
            "Thought: done\nFinal Answer: Success",    # done
        ]
        client = _fake_client_seq(contents)
        sandbox = lambda cmd: (True, "ok")
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        # Must NOT be blocked — empty counter reset after real action
        self.assertEqual(report.status, "done")

    def test_blocked_learning_mentions_budget(self):
        from src.envstate.build_agent import LOCAL_BUDGET
        contents = [f"Thought: x\nAction: cmd{i}" for i in range(LOCAL_BUDGET + 1)]
        client = _fake_client_seq(contents)
        sandbox = lambda cmd: (False, "fail")
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertIn("budget", report.learning.lower())


# ---------------------------------------------------------------------------
# 6. BuildAgent.run — stuck guard integration
# ---------------------------------------------------------------------------

class TestBuildAgentStuckGuardIntegration(unittest.TestCase):
    """The stuck guard inside BuildAgent.run must fire correctly."""

    def test_stuck_fires_on_two_identical_real_failures(self):
        """Two actions with the same failure output → status 'blocked'."""
        err = "ERROR: Could not find a version that satisfies psycopg2"
        client = _fake_client_seq([
            "Thought: try 1\nAction: pip install psycopg2",
            "Thought: try 2\nAction: pip install psycopg2",
            "Thought: try 3\nAction: pip install psycopg2",
            "Thought: done\nFinal Answer: Success",  # should not reach here
        ])
        sandbox = lambda cmd: (False, err)
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        self.assertEqual(report.status, "blocked")
        self.assertIn("stuck", report.learning.lower())

    def test_stuck_does_not_fire_on_different_errors(self):
        """Two failures with different error text — guard must NOT fire."""
        errors = iter(["ERROR: pg_config not found", "ERROR: different error"])
        client = _fake_client_seq([
            "Thought: try 1\nAction: pip install psycopg2",
            "Thought: try 2\nAction: apt-get install libpq-dev",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (False, next(errors))
        # With different errors the guard must not fire — loop must reach Final Answer
        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        # The loop ran both failures then got Final Answer → done
        self.assertEqual(report.status, "done")

    def test_preflight_rejection_does_not_trigger_stuck(self):
        """Preflight rejections must not count toward the stuck counter."""
        preflight = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup commands must not pipe"
        client = _fake_client_seq([
            "Thought: bad1\nAction: pip install x | head",
            "Thought: bad2\nAction: pip install x | head",
            "Thought: ok\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox_calls = []

        def sandbox(cmd):
            sandbox_calls.append(cmd)
            if "| head" in cmd:
                return False, preflight
            return True, "Installed flask"

        report = _make_agent(client).run(_make_task(), sandbox, _make_ledger())
        # Two identical preflight rejections must NOT trigger stuck
        # → loop continues to the real action and then Final Answer
        self.assertEqual(report.status, "done")
        self.assertEqual(len(report.commands), 3)


# ---------------------------------------------------------------------------
# 7. ActionLedger appends — each executed action is recorded
# ---------------------------------------------------------------------------

class TestBuildAgentLedgerAppends(unittest.TestCase):
    """Each shell-executed action must be appended to the ActionLedger."""

    def test_successful_action_appended_with_rc_0(self):
        client = _fake_client_seq([
            "Thought: install\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, "Successfully installed flask")
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].cmd, "pip install flask")
        self.assertEqual(events[0].rc, 0)

    def test_failed_action_appended_with_rc_1(self):
        client = _fake_client_seq([
            "Thought: try\nAction: pip install psycopg2",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (False, "ERROR: pg_config not found")
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].rc, 1)

    def test_multiple_actions_all_appended_in_order(self):
        client = _fake_client_seq([
            "Thought: step1\nAction: apt-get install -y libpq-dev",
            "Thought: step2\nAction: pip install psycopg2",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, f"ok: {cmd}")
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].cmd, "apt-get install -y libpq-dev")
        self.assertEqual(events[1].cmd, "pip install psycopg2")

    def test_preflight_rejected_action_still_appended(self):
        """Preflight rejections ARE appended to the ledger (rc=1, mutation_class=None)."""
        preflight = "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup"
        client = _fake_client_seq([
            "Thought: bad\nAction: pip install x | head",
            "Thought: ok\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])

        def sandbox(cmd):
            if "| head" in cmd:
                return False, preflight
            return True, "ok"

        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertEqual(len(events), 2)
        # Preflight rejection: rc=1, mutation_class=None
        self.assertEqual(events[0].rc, 1)
        self.assertIsNone(events[0].mutation_class)
        # Real successful action: rc=0
        self.assertEqual(events[1].rc, 0)

    def test_mutating_command_sets_mutation_class(self):
        """Successful mutating command must carry a non-None mutation_class."""
        client = _fake_client_seq([
            "Thought: install\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, "ok")
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), sandbox, ledger)

        events = ledger.events()
        # _FakeSynthesizer marks all commands as mutating → mutation_class set
        self.assertIsNotNone(events[0].mutation_class)

    def test_non_mutating_command_has_null_mutation_class(self):
        """Read-only commands (synthesizer returns False) must have mutation_class=None."""
        class _ReadOnlySynthesizer:
            def command_mutates_environment(self, cmd): return False
            def classify_mutation(self, cmd): return "other_mutation"

        client = _fake_client_seq([
            "Thought: read\nAction: cat requirements.txt",
            "Thought: done\nFinal Answer: Success",
        ])
        sandbox = lambda cmd: (True, "flask==2.3.0")
        ledger = _make_ledger()
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_ReadOnlySynthesizer(), container_id="c"
        )
        agent.run(_make_task(), sandbox, ledger)

        events = ledger.events()
        self.assertIsNone(events[0].mutation_class)

    def test_ledger_event_has_correct_container_id(self):
        client = _fake_client_seq([
            "Thought: x\nAction: ls",
            "Thought: done\nFinal Answer: Success",
        ])
        ledger = _make_ledger()
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_FakeSynthesizer(), container_id="my-container-123"
        )
        agent.run(_make_task(), lambda cmd: (True, "ok"), ledger)

        events = ledger.events()
        self.assertEqual(events[0].container_id, "my-container-123")

    def test_ledger_event_step_increments(self):
        """step field must increment across actions."""
        client = _fake_client_seq([
            "Thought: a\nAction: cmd1",
            "Thought: b\nAction: cmd2",
            "Thought: done\nFinal Answer: Success",
        ])
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), lambda cmd: (True, "ok"), ledger)

        events = ledger.events()
        self.assertEqual(len(events), 2)
        self.assertLess(events[0].step, events[1].step)

    def test_step_offset_shifts_step_numbers(self):
        """step_offset shifts all step numbers for correct multi-task ledger alignment."""
        client = _fake_client_seq([
            "Thought: x\nAction: ls",
            "Thought: done\nFinal Answer: Success",
        ])
        ledger = _make_ledger()
        _make_agent(client).run(_make_task(), lambda cmd: (True, "ok"), ledger, step_offset=10)

        events = ledger.events()
        self.assertGreaterEqual(events[0].step, 10)


# ---------------------------------------------------------------------------
# 8. on_usage callback
# ---------------------------------------------------------------------------

class TestBuildAgentOnUsage(unittest.TestCase):

    def test_on_usage_called_once_per_llm_step(self):
        """on_usage must be called once for each LLM call made by the agent."""
        client = _fake_client_seq([
            "Thought: step1\nAction: pip install flask",
            "Thought: done\nFinal Answer: Success",
        ])
        seen = []
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_FakeSynthesizer(), container_id="c",
            on_usage=seen.append,
        )
        agent.run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())
        # 2 LLM calls → 2 on_usage invocations
        self.assertEqual(len(seen), 2)

    def test_on_usage_receives_token_counts(self):
        """Each on_usage dict must have input_tokens, output_tokens, total_tokens."""
        client = _fake_client_seq(["Thought: done\nFinal Answer: Success"])
        seen = []
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_FakeSynthesizer(), container_id="c",
            on_usage=seen.append,
        )
        agent.run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())
        self.assertEqual(len(seen), 1)
        usage = seen[0]
        self.assertIn("input_tokens", usage)
        self.assertIn("output_tokens", usage)
        self.assertIn("total_tokens", usage)

    def test_on_usage_none_does_not_crash(self):
        """on_usage=None (default) must not raise."""
        client = _fake_client_seq(["Thought: done\nFinal Answer: Success"])
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=client, model="m",
            synthesizer=_FakeSynthesizer(), container_id="c",
            on_usage=None,
        )
        # Should not raise
        agent.run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())

    def test_log_path_stored_on_agent(self):
        """log_path kwarg must be stored as self.log_path (canonical __init__ contract)."""
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=_fake_client_seq([]),
            model="m",
            synthesizer=_FakeSynthesizer(),
            container_id="c",
            log_path="/tmp/build_agent_test.log",
        )
        self.assertEqual(agent.log_path, "/tmp/build_agent_test.log")

    def test_log_path_defaults_to_none(self):
        """log_path must default to None when not supplied."""
        from src.envstate.build_agent import BuildAgent
        agent = BuildAgent(
            client=_fake_client_seq([]),
            model="m",
            synthesizer=_FakeSynthesizer(),
            container_id="c",
        )
        self.assertIsNone(agent.log_path)


# ---------------------------------------------------------------------------
# 9. System prompt and task-message content
# ---------------------------------------------------------------------------

class TestBuildAgentSystemPrompt(unittest.TestCase):

    def test_system_prompt_exists_and_is_string(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT
        self.assertIsInstance(BUILD_AGENT_SYSTEM_PROMPT, str)
        self.assertGreater(len(BUILD_AGENT_SYSTEM_PROMPT), 100)

    def test_system_prompt_mentions_all_rca_layers(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT
        p = BUILD_AGENT_SYSTEM_PROMPT.lower()
        for layer in ("base", "system", "runtime", "deps", "build", "tests"):
            self.assertIn(layer, p, f"Layer '{layer}' missing from BUILD_AGENT_SYSTEM_PROMPT")

    def test_system_prompt_has_final_answer_success(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT
        self.assertIn("Final Answer: Success", BUILD_AGENT_SYSTEM_PROMPT)

    def test_system_prompt_has_action_format(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT
        self.assertIn("Action:", BUILD_AGENT_SYSTEM_PROMPT)
        self.assertIn("Thought:", BUILD_AGENT_SYSTEM_PROMPT)

    def test_llm_receives_system_prompt_as_first_message(self):
        """The first message sent to the LLM must be role=system with BUILD_AGENT_SYSTEM_PROMPT."""
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT, BuildAgent
        captured = []

        def fake_create(**kwargs):
            captured.append(kwargs["messages"])
            return _fake_response("Thought: done\nFinal Answer: Success")

        class _FakeCompletions:
            def create(self, **kwargs): return fake_create(**kwargs)
        class _FakeChat:
            completions = _FakeCompletions()
        class _FakeClient:
            chat = _FakeChat()

        agent = BuildAgent(
            client=_FakeClient(), model="m",
            synthesizer=_FakeSynthesizer(), container_id="c"
        )
        agent.run(_make_task(), lambda cmd: (True, "ok"), _make_ledger())

        self.assertGreater(len(captured), 0)
        sys_msgs = [m for m in captured[0] if m["role"] == "system"]
        self.assertEqual(len(sys_msgs), 1)
        self.assertEqual(sys_msgs[0]["content"], BUILD_AGENT_SYSTEM_PROMPT)

    def test_task_message_contains_goal_done_when_layer_facts(self):
        """The user message must contain goal, done_when, layer, and facts."""
        from src.envstate.build_agent import BuildAgent
        captured = []

        def fake_create(**kwargs):
            captured.append(kwargs["messages"])
            return _fake_response("Thought: done\nFinal Answer: Success")

        class _FakeCompletions:
            def create(self, **kwargs): return fake_create(**kwargs)
        class _FakeChat:
            completions = _FakeCompletions()
        class _FakeClient:
            chat = _FakeChat()

        task = _make_task(
            goal="install edsl package",
            done_when="python -c 'import edsl' exits 0",
            layer="deps",
            facts=("build_system=pip", "python=3.12"),
        )
        agent = BuildAgent(
            client=_FakeClient(), model="m",
            synthesizer=_FakeSynthesizer(), container_id="c"
        )
        agent.run(task, lambda cmd: (True, "ok"), _make_ledger())

        user_msgs = [m for m in captured[0] if m["role"] == "user"]
        self.assertGreater(len(user_msgs), 0)
        content = user_msgs[0]["content"]
        self.assertIn("install edsl package", content)
        self.assertIn("python -c 'import edsl' exits 0", content)
        self.assertIn("deps", content)
        self.assertIn("build_system=pip", content)
        self.assertIn("python=3.12", content)


class TestBuildAgentPromptContract(unittest.TestCase):
    """The Repo2Run-style BuildAgent prompt must stay parser-compatible and keep the
    task-scoping, integrity rules, and static (non-countdown) budget signal."""

    def setUp(self):
        from src.envstate.build_agent import BUILD_AGENT_SYSTEM_PROMPT, LOCAL_BUDGET
        self.prompt = BUILD_AGENT_SYSTEM_PROMPT
        self.budget = LOCAL_BUDGET

    def test_format_is_parser_compatible(self):
        # Must instruct the exact tokens the parser keys on, and NOT the "### Action"
        # markdown header (which would not match _ACTION_RE).
        self.assertIn("Action:", self.prompt)
        self.assertIn("Final Answer: Success", self.prompt)
        self.assertNotIn("### Action", self.prompt)

    def test_references_planner_task_fields(self):
        for label in ("Task goal", "Done when", "Layer", "Relevant facts"):
            self.assertIn(label, self.prompt)

    def test_one_line_chaining_rule_present(self):
        self.assertIn("&&", self.prompt)
        self.assertIn("ONE line", self.prompt)

    def test_static_budget_interpolated_no_countdown(self):
        # The "up to N commands" line must track LOCAL_BUDGET (no drift); there is no
        # live remaining-steps countdown by design (avoids rush-to-fake-success).
        self.assertIn(f"up to {self.budget} commands", self.prompt)

    def test_integrity_rules_present(self):
        low = self.prompt.lower()
        self.assertIn("do not make extensive changes", low)
        self.assertIn("modifying or deleting test functions", self.prompt)

    def test_role_is_env_config_expert(self):
        self.assertIn("environment configuration", self.prompt)

    def test_role_boundary_no_plan_no_certify(self):
        low = self.prompt.lower()
        self.assertIn("do not plan", low)
        self.assertIn("do not certify", low)
