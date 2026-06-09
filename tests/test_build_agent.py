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
