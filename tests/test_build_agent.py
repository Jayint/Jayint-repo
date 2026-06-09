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
