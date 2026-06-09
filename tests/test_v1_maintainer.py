# tests/test_v1_maintainer.py
"""Unit tests for the v1 Maintainer rewrite.

Covers:
- parse_v1_maintainer_reply: valid JSON → WorldModelMap fields
- Grounding rule: a Fact not demonstrated in command output is not added to installed
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
# The rewritten maintainer.py does not exist yet — imports below will fail.
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
# parse_v1_maintainer_reply
# ---------------------------------------------------------------------------

class TestParseV1MaintainerReply(unittest.TestCase):
    """parse_v1_maintainer_reply(text, current_map, report) -> WorldModelMap."""

    def _llm_json(self, **fields) -> str:
        return "```json\n" + json.dumps(fields) + "\n```"

    def test_installed_fact_recorded_when_output_confirms_it(self):
        base = _base_map()
        report = _make_report(
            [("pip install flask==3.0.0", 0, "Successfully installed flask-3.0.0")]
        )
        reply = self._llm_json(
            installed=[{"name": "flask", "detail": "3.0.0"}],
            open_problems=[],
            progress={"base": True, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        names = [f.name for f in new_map.installed]
        self.assertIn("flask", names)

    def test_invented_fact_not_added_when_absent_from_output(self):
        """Grounding rule: LLM proposes 'numpy' but the output never mentions it."""
        base = _base_map()
        report = _make_report(
            [("pip install flask==3.0.0", 0, "Successfully installed flask-3.0.0")]
        )
        reply = self._llm_json(
            # LLM invents numpy even though command output says nothing about it
            installed=[
                {"name": "flask", "detail": "3.0.0"},
                {"name": "numpy", "detail": "1.26"},
            ],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        names = [f.name for f in new_map.installed]
        self.assertNotIn("numpy", names)
        self.assertIn("flask", names)

    def test_open_problem_recorded_from_failed_command(self):
        base = _base_map()
        report = _make_report(
            [("pip install psycopg2==2.8.6", 1,
              "error: pg_config executable not found")]
        )
        reply = self._llm_json(
            installed=[],
            open_problems=[
                {
                    "signature": "ModuleNotFoundError: psycopg2",
                    "interpretation": "needs libpq-dev",
                    "layer": "system",
                }
            ],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        sigs = [p.signature for p in new_map.open_problems]
        self.assertIn("ModuleNotFoundError: psycopg2", sigs)

    def test_progress_updated_from_llm_reply(self):
        base = _base_map()
        report = _make_report(
            [("apt-get install -y python3-dev", 0, "Setting up python3-dev")]
        )
        reply = self._llm_json(
            installed=[{"name": "python3-dev", "detail": ""}],
            open_problems=[],
            progress={"base": True, "system": True, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.progress["system"])
        self.assertTrue(new_map.progress["base"])
        self.assertFalse(new_map.progress["deps"])

    def test_notes_appended_not_replaced(self):
        base = merge_map(_base_map(), notes=("existing note",))
        report = _make_report([("pip install x", 0, "ok")])
        reply = self._llm_json(
            installed=[{"name": "x", "detail": ""}],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=["new caution"],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertIn("existing note", new_map.notes)
        self.assertIn("new caution", new_map.notes)

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


# ---------------------------------------------------------------------------
# done_flag detection
# ---------------------------------------------------------------------------

class TestDoneFlag(unittest.TestCase):
    """done_flag is set iff a pytest --collect-only command exited 0."""

    def test_done_flag_set_on_collect_only_rc0(self):
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 0,
              "collected 12 items")]
        )
        # LLM reply says tests layer is done
        reply = (
            '```json\n{"installed": [], "open_problems": [],'
            ' "progress": {"base": true, "system": true, "runtime": true,'
            ' "deps": true, "build": true, "tests": true}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.done_flag)

    def test_done_flag_set_for_poetry_run_collect_only(self):
        base = _base_map()
        report = _make_report(
            [("poetry run pytest --collect-only -q --disable-warnings", 0,
              "collected 5 items")]
        )
        reply = (
            '```json\n{"installed": [], "open_problems": [],'
            ' "progress": {"base": true, "system": true, "runtime": true,'
            ' "deps": true, "build": true, "tests": true}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertTrue(new_map.done_flag)

    def test_done_flag_not_set_when_collect_only_fails(self):
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 1,
              "ERROR: ModuleNotFoundError: edsl")]
        )
        reply = (
            '```json\n{"installed": [], "open_problems": [],'
            ' "progress": {"base": true, "system": false, "runtime": false,'
            ' "deps": false, "build": false, "tests": false}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    def test_done_flag_not_set_for_unrelated_rc0_command(self):
        base = _base_map()
        report = _make_report(
            [("pip install flask", 0, "Successfully installed flask-3.0.0")]
        )
        reply = (
            '```json\n{"installed": [{"name": "flask", "detail": "3.0.0"}],'
            ' "open_problems": [],'
            ' "progress": {"base": true, "system": false, "runtime": false,'
            ' "deps": true, "build": false, "tests": false}, "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    def test_done_flag_preserved_when_already_true(self):
        """If done_flag is somehow already True, update must keep it True."""
        base = merge_map(_base_map(), done_flag=True)
        report = _make_report([("ls", 0, "")])
        reply = (
            '```json\n{"installed": [], "open_problems": [],'
            ' "progress": {"base": true, "system": true, "runtime": true,'
            ' "deps": true, "build": true, "tests": true}, "notes": []}\n```'
        )
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
                    installed=[],
                    open_problems=[],
                    progress={"base": False, "system": False, "runtime": False,
                               "deps": False, "build": False, "tests": False},
                    notes=[],
                )
            ),
            model="test-model",
        )
        base = _base_map()
        report = _make_report([("ls", 0, "pyproject.toml")])
        result = maintainer.update(base, report)
        self.assertIsInstance(result, WorldModelMap)

    def test_update_sets_done_flag_on_collect_only_rc0(self):
        reply = self._reply_json(
            installed=[],
            open_problems=[],
            progress={"base": True, "system": True, "runtime": True,
                      "deps": True, "build": True, "tests": True},
            notes=[],
        )
        maintainer = Maintainer(client=_fake_client(reply), model="test-model")
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 0,
              "collected 7 items")]
        )
        result = maintainer.update(base, report)
        self.assertTrue(result.done_flag)

    def test_update_records_open_problem_on_install_failure(self):
        reply = self._reply_json(
            installed=[],
            open_problems=[
                {
                    "signature": "ImportError: cannot import name 'edsl'",
                    "interpretation": "package not installed",
                    "layer": "deps",
                }
            ],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
            notes=[],
        )
        maintainer = Maintainer(client=_fake_client(reply), model="test-model")
        base = _base_map()
        report = _make_report(
            [("pip install edsl", 1, "ERROR: Could not build edsl")],
            status="blocked",
            learning="edsl build fails",
        )
        result = maintainer.update(base, report)
        sigs = [p.signature for p in result.open_problems]
        self.assertIn("ImportError: cannot import name 'edsl'", sigs)

    def test_update_does_not_mutate_input_map(self):
        """WorldModelMap is frozen — update must return a new object."""
        reply = self._reply_json(
            installed=[{"name": "flask", "detail": "3.0.0"}],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
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
        # The new map has the installed fact.
        self.assertTrue(any(f.name == "flask" for f in result.installed))

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
            installed=[],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
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
            installed=[],
            open_problems=[],
            progress={"base": False, "system": False, "runtime": False,
                      "deps": False, "build": False, "tests": False},
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
        self.assertIn("collect-only", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("done_flag", MAINTAINER_SYSTEM_PROMPT)

    def test_prompt_mentions_single_output_shape(self):
        """The prompt must reference the four output keys of the v1 schema."""
        for key in ("installed", "open_problems", "progress", "notes"):
            self.assertIn(key, MAINTAINER_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
