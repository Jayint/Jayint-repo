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

    def test_open_problem_recorded_from_failed_command(self):
        base = _base_map()
        report = _make_report(
            [("pip install psycopg2==2.8.6", 1,
              "error: pg_config executable not found")]
        )
        reply = self._llm_json(
            open_problems=[
                {
                    "signature": "ModuleNotFoundError: psycopg2",
                    "interpretation": "needs libpq-dev",
                    "layer": "system",
                }
            ],
            resolved=[],
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        sigs = [p.signature for p in new_map.open_problems]
        self.assertIn("ModuleNotFoundError: psycopg2", sigs)

    def test_resolved_drops_problem(self):
        """resolved key removes existing open_problem by signature."""
        base = merge_map(_base_map(), open_problems=(
            OpenProblem("pg_config not found", "needs libpq-dev", "system"),
        ))
        report = _make_report([("apt-get install -y libpq-dev", 0, "ok")])
        reply = self._llm_json(
            open_problems=[],
            resolved=["pg_config not found"],
            notes=[],
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertEqual(new_map.open_problems, ())

    def test_notes_appended_not_replaced(self):
        base = merge_map(_base_map(), notes=("existing note",))
        report = _make_report([("pip install x", 0, "ok")])
        reply = self._llm_json(
            open_problems=[],
            resolved=[],
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

    def test_unparseable_output_still_sets_done_flag_on_collect_only(self):
        """Even when the LLM reply is unparseable, the structural done_flag rule
        must still fire from the report (collect-only rc0) so the EBSR gate is
        never missed just because the LLM returned garbage that cycle (spec §5)."""
        base = _base_map()
        report = _make_report(
            [("pytest --collect-only -q --disable-warnings", 0, "collected 3 items")]
        )
        new_map = parse_v1_maintainer_reply("not json at all", base, report)
        self.assertTrue(new_map.done_flag)


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
        reply = (
            '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
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
            '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
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
            '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    def test_done_flag_not_set_for_unrelated_rc0_command(self):
        base = _base_map()
        report = _make_report(
            [("pip install flask", 0, "Successfully installed flask-3.0.0")]
        )
        reply = (
            '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        )
        new_map = parse_v1_maintainer_reply(reply, base, report)
        self.assertFalse(new_map.done_flag)

    # -- detector robustness (hardening after dropping self-declared done) ----

    def _done_after(self, cmd: str, rc: int = 0) -> bool:
        reply = '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
        new_map = parse_v1_maintainer_reply(reply, _base_map(), _make_report([(cmd, rc, "out")]))
        return new_map.done_flag

    def test_done_flag_set_for_co_alias(self):
        # pytest registers `--co` as an explicit alias for `--collect-only`.
        self.assertTrue(self._done_after("pytest --co"))

    def test_done_flag_set_regardless_of_arg_order(self):
        self.assertTrue(self._done_after("pytest -q --collect-only --disable-warnings"))

    def test_done_flag_set_for_python_m_pytest(self):
        self.assertTrue(self._done_after("python -m pytest --collect-only"))

    def test_done_flag_set_for_path_pytest(self):
        self.assertTrue(self._done_after("/venv/bin/pytest --co -q"))

    def test_cov_flag_does_not_trigger(self):
        # --cov (pytest-cov) shares the '--co' prefix but is NOT collect-only.
        self.assertFalse(self._done_after("pytest --cov=src tests/"))

    def test_color_flag_does_not_trigger(self):
        self.assertFalse(self._done_after("pytest --color=yes -q"))

    def test_non_pytest_tool_with_co_does_not_trigger(self):
        self.assertFalse(self._done_after("sometool --co --collect-only"))

    def test_co_alias_still_gated_on_rc0(self):
        self.assertFalse(self._done_after("pytest --co", rc=1))

    def test_malformed_quoting_does_not_crash(self):
        # Unbalanced quote: must not raise, and still detects via the fallback split.
        self.assertTrue(self._done_after('pytest --collect-only "oops -q'))

    def test_done_flag_preserved_when_already_true(self):
        """If done_flag is somehow already True, update must keep it True."""
        base = merge_map(_base_map(), done_flag=True)
        report = _make_report([("ls", 0, "")])
        reply = (
            '```json\n{"open_problems": [], "resolved": [], "notes": []}\n```'
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

    def test_update_sets_done_flag_on_collect_only_rc0(self):
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
        self.assertTrue(result.done_flag)

    def test_update_records_open_problem_on_install_failure(self):
        reply = self._reply_json(
            open_problems=[
                {
                    "signature": "ImportError: cannot import name 'edsl'",
                    "interpretation": "package not installed",
                    "layer": "deps",
                }
            ],
            resolved=[],
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
        self.assertIn("collect-only", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("done_flag", MAINTAINER_SYSTEM_PROMPT)

    def test_prompt_mentions_single_output_shape(self):
        """The prompt must reference the three output keys of the narrowed v1 schema."""
        for key in ("open_problems", "resolved", "notes"):
            self.assertIn(key, MAINTAINER_SYSTEM_PROMPT)

    def test_prompt_states_facts_are_host_authoritative(self):
        """The prompt must inform the LLM that installed facts are host-owned."""
        prompt_lower = MAINTAINER_SYSTEM_PROMPT.lower()
        self.assertTrue(
            "authoritative" in prompt_lower or "already filled" in prompt_lower,
            "Prompt should state that installed/facts are host-authoritative",
        )


# ---------------------------------------------------------------------------
# New schema: kind -> layer, hypothesis -> interpretation, planner_notes -> notes
# ---------------------------------------------------------------------------

class TestNewSchemaParsing(unittest.TestCase):
    def _parse(self, problems=None, resolved=None, planner_notes=None, notes=None):
        obj = {"open_problems": problems or []}
        if resolved is not None:
            obj["resolved"] = resolved
        if planner_notes is not None:
            obj["planner_notes"] = planner_notes
        if notes is not None:
            obj["notes"] = notes
        text = "```json\n" + json.dumps(obj) + "\n```"
        return parse_v1_maintainer_reply(text, _base_map(), _make_report([("noop", 0, "")]))

    def test_system_kind_maps_to_system_layer(self):
        # This is the whole point: a native-tool failure must land on layer 'system'
        # so the host's _auto_resolve_system_problems can engage.
        out = self._parse(problems=[{
            "signature": "pg_config: command not found",
            "kind": "system_tool_missing",
            "hypothesis": "psycopg2 build needs libpq",
            "root_or_downstream": "root",
        }])
        op = out.open_problems[0]
        self.assertEqual(op.signature, "pg_config: command not found")
        self.assertEqual(op.layer, "system")

    def test_header_and_native_kinds_map_to_system(self):
        for kind in ("native_build_dependency", "header_missing"):
            out = self._parse(problems=[{"signature": f"sig-{kind}", "kind": kind,
                                         "hypothesis": "h"}])
            self.assertEqual(out.open_problems[0].layer, "system", kind)

    def test_package_kinds_map_to_deps(self):
        for kind in ("language_package_missing", "import_failure"):
            out = self._parse(problems=[{"signature": f"sig-{kind}", "kind": kind}])
            self.assertEqual(out.open_problems[0].layer, "deps", kind)

    def test_test_failure_maps_to_tests_layer(self):
        out = self._parse(problems=[{"signature": "collection error", "kind": "test_failure"}])
        self.assertEqual(out.open_problems[0].layer, "tests")

    def test_unknown_kind_defaults_to_deps(self):
        out = self._parse(problems=[{"signature": "weird", "kind": "totally_unknown"}])
        self.assertEqual(out.open_problems[0].layer, "deps")

    def test_hypothesis_becomes_interpretation_with_root_tag(self):
        out = self._parse(problems=[{
            "signature": "No module named 'psycopg2'",
            "kind": "import_failure",
            "hypothesis": "downstream of the pg_config wall",
            "root_or_downstream": "downstream",
        }])
        interp = out.open_problems[0].interpretation
        self.assertIn("downstream", interp)
        self.assertIn("pg_config wall", interp)

    def test_planner_notes_become_notes(self):
        out = self._parse(problems=[], planner_notes=["log was truncated; request full log"])
        self.assertIn("log was truncated; request full log", out.notes)

    def test_verbatim_signature_preserved_exactly(self):
        sig = "fatal error: libpq-fe.h: No such file or directory"
        out = self._parse(problems=[{"signature": sig, "kind": "header_missing"}])
        self.assertEqual(out.open_problems[0].signature, sig)

    # -- back-compat: old replies must still parse --------------------------------

    def test_legacy_layer_and_interpretation_still_parse(self):
        out = self._parse(problems=[{"signature": "E1", "interpretation": "i", "layer": "runtime"}],
                          notes=["careful"])
        op = out.open_problems[0]
        self.assertEqual(op.layer, "runtime")
        self.assertEqual(op.interpretation, "i")
        self.assertIn("careful", out.notes)

    def test_resolved_still_drops_signatures(self):
        base = merge_map(
            _base_map(),
            open_problems=(OpenProblem("old sig", "x", "deps"),),
        )
        text = '```json\n{"open_problems": [], "resolved": ["old sig"], "planner_notes": []}\n```'
        out = parse_v1_maintainer_reply(text, base, _make_report([("noop", 0, "")]))
        self.assertEqual(out.open_problems, ())


class TestNewPromptContract(unittest.TestCase):
    def test_prompt_demands_literal_signatures(self):
        low = MAINTAINER_SYSTEM_PROMPT.lower()
        self.assertTrue("literal" in low or "do not paraphrase" in low)

    def test_prompt_defines_kind_taxonomy(self):
        for kind in ("native_build_dependency", "system_tool_missing", "header_missing",
                     "language_package_missing", "import_failure", "test_failure"):
            self.assertIn(kind, MAINTAINER_SYSTEM_PROMPT)

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
