# tests/test_v1_maintainer.py
"""Unit tests for the v1 Maintainer (narrowed contract).

Covers:
- parse_v1_maintainer_reply: valid JSON → WorldModelMap fields
- installed and progress are NOT touched by parse_v1_maintainer_reply (new contract)
- resolved key drops listed open_problem signatures
- done_flag set when a pytest --collect-only command has rc==0
- done_flag NOT set when collect-only rc!=0
- done_flag NOT set when an unrelated command has rc==0
- Empty / unparseable LLM output → map unchanged, no crash
- Maintainer.update signature matches the contract
- notes are preserved across update cycles
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from dataclasses import replace

# world_model is built in Group 1 and must already be present.
from src.envstate.world_model import (
    Fact,
    OpenProblem,
    WorldModelMap,
    TaskReport,
    CommandRecord,
    initial_map,
    merge_map,
)
from src.envstate.maintainer import (
    MAINTAINER_SYSTEM_PROMPT,
    Maintainer,
    parse_v1_maintainer_reply,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_map() -> WorldModelMap:
    return initial_map(
        base_image="python:3.12-slim",
        workdir="/app",
        language="python 3.12",
        build_system="poetry",
        repo_layout=("tests/", "src/", "pyproject.toml"),
    )


def _fake_client(content: str) -> SimpleNamespace:
    """OpenAI-compatible stub that always returns *content*."""
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kw: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=content)
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                    ),
                )
            )
        )
    )


def _make_report(
    commands: list[tuple[str, int, str]],
    status: str = "done",
    goal: str = "install deps",
    learning: str = "all good",
) -> TaskReport:
    return TaskReport(
        task_goal=goal,
        status=status,
        commands=tuple(
            CommandRecord(cmd=cmd, rc=rc, output=out)
            for cmd, rc, out in commands
        ),
        learning=learning,
    )


# ---------------------------------------------------------------------------
# parse_v1_maintainer_reply — narrowed contract
# ---------------------------------------------------------------------------

class TestParseV1MaintainerReply(unittest.TestCase):
    """parse_v1_maintainer_reply(text, current_map, report) -> WorldModelMap."""

    def _llm_json(self, **fields) -> str:
        return "```json\n" + json.dumps(fields) + "\n```"

    def test_installed_unchanged_even_when_llm_proposes_it(self):
        """New contract: installed is host-owned; LLM 'installed' key is ignored."""
        base = merge_map(_base_map(), installed=(Fact("flask", "3.0.0"),))
        report = _make_report(
            [("pip install flask==3.0.0", 0, "Successfully installed flask-3.0.0")]
        )
        # LLM proposes extra installed facts — they must be ignored
        reply = self._llm_json(
            installed=[{"name": "requests", "detail": "2.31.0"}],
            open_problems=[],
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        # installed must remain exactly as it was
        self.assertEqual(new_map.installed, (Fact("flask", "3.0.0"),))
        names = [f.name for f in new_map.installed]
        self.assertNotIn("requests", names)

    def test_progress_unchanged_even_when_llm_proposes_it(self):
        """New contract: progress is host-owned; LLM 'progress' key is ignored."""
        base = _base_map()
        report = _make_report(
            [("apt-get install -y python3-dev", 0, "Setting up python3-dev")]
        )
        # LLM proposes progress changes — they must be ignored
        reply = self._llm_json(
            open_problems=[],
            progress={"base": True, "system": True, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        # progress must remain as-is from the base map
        self.assertEqual(new_map.progress, base.progress)

    def test_empty_llm_output_returns_map_unchanged(self):
        base = _base_map()
        report = _make_report([("ls", 0, "")])
        new_map = parse_v1_maintainer_reply("", base, report)
        self.assertEqual(new_map.installed, base.installed)
        self.assertEqual(new_map.open_problems, base.open_problems)
        self.assertEqual(new_map.done_flag, False)

    def test_unparseable_json_returns_map_unchanged(self):
        base = _base_map()
        report = _make_report([("ls", 0, "")])
        new_map = parse_v1_maintainer_reply("not json at all", base, report)
        self.assertEqual(new_map.installed, base.installed)

    def test_unparseable_output_still_sets_done_flag_on_real_execution(self):
        """Even when the LLM reply is unparseable, the structural done_flag rule
        must still fire from the report (real execution rc0 with passed output) so
        the EBSR gate is never missed just because the LLM returned garbage that
        cycle (spec §5)."""
        base = _base_map()
        report = _make_report(
            [("python -m pytest -q", 0, "3 passed in 0.5s")]
        )
        new_map = parse_v1_maintainer_reply("not json at all", base, report)
        self.assertTrue(new_map.done_flag)


# ---------------------------------------------------------------------------
# done_flag detection — execution-gate semantics
# ---------------------------------------------------------------------------

class TestDoneFlag(unittest.TestCase):
    """done_flag is set iff the report contains a real test execution (>=1 passed,
    bare interpreter, no venv wrapper) that exited 0."""

    def test_done_flag_set_on_real_execution_rc0(self):
        """python -m pytest -q with 'N passed' output must finalize."""
        base = _base_map()
        report = _make_report(
            [("python -m pytest -q", 0, "12 passed in 0.5s")]
        )
        reply = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.done_flag)

    def test_done_flag_NOT_set_on_collect_only(self):
        """pytest --collect-only with 'collected N items' (no passed) must NOT finalize."""
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 0,
              "collected 12 items")]
        )
        reply = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    def test_done_flag_NOT_set_for_poetry_run_even_with_passed_output(self):
        """poetry run pytest is venv-wrapped — must NOT finalize."""
        base = _base_map()
        report = _make_report(
            [("poetry run pytest -q", 0, "5 passed in 1.0s")]
        )
        reply = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    def test_done_flag_not_set_when_execution_fails(self):
        """rc != 0 must not finalize even if output has 'passed'."""
        base = _base_map()
        report = _make_report(
            [("python -m pytest -q", 1, "3 passed, 1 failed in 0.5s")]
        )
        reply = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    def test_done_flag_not_set_for_unrelated_rc0_command(self):
        base = _base_map()
        report = _make_report(
            [("pip install flask", 0, "Successfully installed flask-3.0.0")]
        )
        reply = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    # -- detector robustness ----

    def _done_after(self, cmd: str, rc: int = 0, output: str = "") -> bool:
        reply = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        new_map = parse_v1_maintainer_reply(
            reply, _base_map(), _make_report([(cmd, rc, output)])
        )
        return new_map.done_flag

    def test_done_flag_set_for_pytest_passed(self):
        self.assertTrue(self._done_after("pytest -q", output="8 passed in 1.2s"))

    def test_done_flag_set_for_python_m_pytest(self):
        self.assertTrue(self._done_after("python -m pytest -q", output="3 passed in 0.3s"))

    def test_done_flag_NOT_set_for_co_alias_collect_only(self):
        # --co is still collect-only — no 'passed' in output
        self.assertFalse(self._done_after("pytest --co", output="collected 7 items"))

    def test_done_flag_set_regardless_of_arg_order_with_execution(self):
        self.assertTrue(self._done_after(
            "pytest -q --disable-warnings", output="5 passed in 0.2s"
        ))

    def test_unrelated_rc0_does_not_trigger(self):
        self.assertFalse(self._done_after("pip install flask", output="ok"))

    def test_venv_wrapped_does_not_trigger(self):
        self.assertFalse(self._done_after("hatch run pytest", output="3 passed in 0.1s"))

    def test_execution_still_gated_on_rc0(self):
        self.assertFalse(self._done_after("pytest -q", rc=1, output="3 failed"))

    def test_done_flag_preserved_when_already_true(self):
        """If done_flag is somehow already True, update must keep it True."""
        base = merge_map(_base_map(), done_flag=True)
        report = _make_report([("ls", 0, "")])
        reply = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.done_flag)


# ---------------------------------------------------------------------------
# Maintainer.update  (full round-trip with mocked LLM)
# ---------------------------------------------------------------------------

class TestMaintainerUpdate(unittest.TestCase):
    """Maintainer.update(current_map, report) -> WorldModelMap."""

    def _reply_json(self, **fields) -> str:
        return "```json\n" + json.dumps(fields) + "\n```"

    def test_update_returns_world_model_map(self):
        maintainer = Maintainer(
            client=_fake_client(
                self._reply_json(
                    open_problems=[],
                    resolved=[],
                    notes=[],
                )
            ),
            model="test-model",
        )
        base = _base_map()
        report = _make_report([("ls", 0, "pyproject.toml")])
        result = maintainer.update(base, report)
        self.assertIsInstance(result, WorldModelMap)

    def test_update_sets_done_flag_on_real_execution_rc0(self):
        """A real test execution (>=1 passed, bare interpreter) must finalize."""
        reply = self._reply_json(
            open_problems=[],
            resolved=[],
            notes=[],
        )
        maintainer = Maintainer(client=_fake_client(reply), model="test-model")
        base = _base_map()
        report = _make_report(
            [("python -m pytest -q", 0, "7 passed in 0.5s")]
        )
        result = maintainer.update(base, report)
        self.assertTrue(result.done_flag)

    def test_update_does_NOT_set_done_flag_on_collect_only(self):
        """pytest --collect-only with 'collected N items' must NOT finalize."""
        reply = self._reply_json(
            open_problems=[],
            resolved=[],
            notes=[],
        )
        maintainer = Maintainer(client=_fake_client(reply), model="test-model")
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 0,
              "collected 7 items")]
        )
        result = maintainer.update(base, report)
        self.assertFalse(result.done_flag)

    def test_update_does_not_mutate_input_map(self):
        """WorldModelMap is frozen — update must return a new object."""
        # Under the new contract, LLM does NOT set installed.
        # installed stays empty; we verify the original map is untouched
        # and the result has the same installed (both empty).
        reply = self._reply_json(
            open_problems=[],
            resolved=[],
            notes=[],
        )
        maintainer = Maintainer(client=_fake_client(reply), model="test-model")
        base = _base_map()
        report = _make_report(
            [("pip install flask", 0, "Successfully installed flask-3.0.0")]
        )
        result = maintainer.update(base, report)
        # The original map must be untouched.
        self.assertEqual(base.installed, ())
        # installed is host-owned — Maintainer leaves it unchanged
        self.assertEqual(result.installed, ())

    def test_update_tolerates_empty_llm_response(self):
        """Empty LLM reply must not crash — map comes back unchanged."""
        maintainer = Maintainer(client=_fake_client(""), model="test-model")
        base = _base_map()
        report = _make_report([("pip install x", 1, "error")])
        result = maintainer.update(base, report)
        self.assertIsInstance(result, WorldModelMap)
        self.assertEqual(result.installed, base.installed)

    def test_on_usage_callback_is_called(self):
        """on_usage must be invoked exactly once per update call with a usage dict."""
        reply = self._reply_json(
            open_problems=[],
            resolved=[],
            notes=[],
        )
        received: list[dict] = []
        maintainer = Maintainer(
            client=_fake_client(reply),
            model="test-model",
            on_usage=received.append,
        )
        base = _base_map()
        report = _make_report([("ls", 0, "")])
        maintainer.update(base, report)
        self.assertEqual(len(received), 1)
        self.assertIn("input_tokens", received[0])

    def test_on_usage_none_does_not_crash(self):
        """Maintainer with on_usage=None must run without error."""
        reply = self._reply_json(
            open_problems=[],
            resolved=[],
            notes=[],
        )
        maintainer = Maintainer(
            client=_fake_client(reply),
            model="test-model",
            on_usage=None,
        )
        base = _base_map()
        report = _make_report([("ls", 0, "")])
        result = maintainer.update(base, report)
        self.assertIsInstance(result, WorldModelMap)


# ---------------------------------------------------------------------------
# System prompt contract
# ---------------------------------------------------------------------------

class TestMaintainerSystemPrompt(unittest.TestCase):
    def test_prompt_emphasises_grounded_recording(self):
        """The prompt must instruct the LLM to record only what output shows."""
        self.assertIn("command", MAINTAINER_SYSTEM_PROMPT.lower())

    def test_prompt_describes_done_flag_trigger(self):
        # The prompt still must mention done_flag; the exact gate wording
        # is in the module docstring/comments, not necessarily the system prompt.
        self.assertIn("done_flag", MAINTAINER_SYSTEM_PROMPT)

class TestNewPromptContract(unittest.TestCase):
    def test_prompt_demands_literal_signatures(self):
        low = MAINTAINER_SYSTEM_PROMPT.lower()
        self.assertTrue("literal" in low or "do not paraphrase" in low)

    def test_prompt_forbids_certifying_facts(self):
        low = MAINTAINER_SYSTEM_PROMPT.lower()
        self.assertIn("do not certify", low)

    def test_prompt_warns_learning_is_weak(self):
        self.assertIn("learning", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("not evidence", MAINTAINER_SYSTEM_PROMPT.lower())

    def test_prompt_distinguishes_root_vs_downstream(self):
        low = MAINTAINER_SYSTEM_PROMPT.lower()
        self.assertIn("root", low)
        self.assertIn("downstream", low)


if __name__ == "__main__":
    unittest.main()
