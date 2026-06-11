# tests/test_envstate_planner.py
"""Unit tests for src/envstate/planner.py.

Covers:
  - PLANNER_SYSTEM_PROMPT content invariants
  - parse_planner_decision: task / done / giveup / missing keys / empty
  - Planner.decide: emits task for unmet layer; returns done when done_flag True;
    routes around an out_of_scope open_problem; returns giveup when no path

All LLM calls are mocked via a fake client.
"""
from __future__ import annotations
import json
import unittest
from types import SimpleNamespace

from src.envstate.world_model import (
    Fact,
    OpenProblem,
    PlannerDecision,
    Task,
    WorldModelMap,
    initial_map,
    merge_map,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_client(content: str) -> SimpleNamespace:
    """Fake OpenAI-compatible client returning *content* on every call."""
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
                        prompt_tokens=20, completion_tokens=10, total_tokens=30
                    ),
                )
            )
        )
    )


def _sequential_client(responses: list[str]) -> tuple[SimpleNamespace, list]:
    """Fake client returning responses in sequence; call_log tracks each call."""
    call_log: list[str] = []
    it = iter(responses)

    def _create(**_kw):
        content = next(it)
        call_log.append(content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )

    return (
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create))),
        call_log,
    )


def _base_map(**kwargs) -> WorldModelMap:
    """Return a fresh map with all layers unmet and done_flag=False."""
    return initial_map(
        base_image="python:3.12-slim",
        workdir="/app",
        language="python 3.12",
        build_system="poetry",
        repo_layout=("tests/", "src/", "pyproject.toml"),
        **kwargs,
    )


def _task_json(
    goal: str = "install project deps",
    done_when: str = "pip install exits 0",
    layer: str = "deps",
    facts: list[str] | None = None,
) -> str:
    """Return a JSON string the LLM would emit for a 'task' decision."""
    return json.dumps({
        "action": "task",
        "goal": goal,
        "done_when": done_when,
        "layer": layer,
        "facts": facts or [],
    })


def _done_json(reason: str = "all layers satisfied") -> str:
    return json.dumps({"action": "done", "reason": reason})


def _giveup_json(reason: str = "unsolvable conflict") -> str:
    return json.dumps({"action": "giveup", "reason": reason})


# ---------------------------------------------------------------------------
# 1. Prompt invariants
# ---------------------------------------------------------------------------

class PlannerSystemPromptTests(unittest.TestCase):
    def test_prompt_mentions_execution_objective(self):
        """Phase 1: objective changed from collect-only to real execution pass."""
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        # The new objective uses a bare interpreter execution, not --collect-only.
        self.assertIn("python -m pytest", PLANNER_SYSTEM_PROMPT)
        # Must NOT instruct the model to use collect-only as the success target.
        self.assertNotIn("--collect-only", PLANNER_SYSTEM_PROMPT)

    def test_prompt_mentions_all_layers(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        for layer in ("base", "system", "runtime", "deps", "build", "tests"):
            self.assertIn(layer, PLANNER_SYSTEM_PROMPT,
                          f"Prompt must mention layer '{layer}'")

    def test_prompt_offers_task_and_giveup(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        self.assertIn('"action": "task"', PLANNER_SYSTEM_PROMPT)
        self.assertIn('"action": "giveup"', PLANNER_SYSTEM_PROMPT)

    def test_prompt_does_not_offer_done_action(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        self.assertNotIn('"action": "done"', PLANNER_SYSTEM_PROMPT)
        self.assertNotIn('"action":"done"', PLANNER_SYSTEM_PROMPT)

    def test_prompt_names_environment_state_map(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        self.assertIn("Environment State Map", PLANNER_SYSTEM_PROMPT)

    def test_prompt_mentions_json_fields(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        for field in ("action", "goal", "done_when", "layer", "facts", "reason"):
            self.assertIn(field, PLANNER_SYSTEM_PROMPT,
                          f"Prompt must mention JSON field '{field}'")

    def test_prompt_forbids_shell_commands(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        # Must not encourage running shell; must say "do not run" or "never run"
        lower = PLANNER_SYSTEM_PROMPT.lower()
        self.assertTrue(
            "never run" in lower or "do not run" in lower or "no shell" in lower,
            "Prompt must forbid the planner from running shell commands",
        )


# ---------------------------------------------------------------------------
# 2. parse_planner_decision
# ---------------------------------------------------------------------------

class ParsePlannerDecisionTests(unittest.TestCase):
    def test_parses_task_action(self):
        from src.envstate.planner import parse_planner_decision
        raw = _task_json(goal="install deps", done_when="exit 0", layer="deps",
                         facts=["flask in pyproject.toml"])
        decision = parse_planner_decision(raw)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "task")
        self.assertIsNotNone(decision.task)
        self.assertEqual(decision.task.goal, "install deps")
        self.assertEqual(decision.task.done_when, "exit 0")
        self.assertEqual(decision.task.layer, "deps")
        self.assertEqual(decision.task.facts, ("flask in pyproject.toml",))

    def test_done_action_is_rejected(self):
        from src.envstate.planner import parse_planner_decision
        # `done` is no longer a valid action — only the structural done_flag
        # may declare success, so a self-declared done parses to None.
        self.assertIsNone(parse_planner_decision(_done_json("tests passing")))

    def test_parses_giveup_action(self):
        from src.envstate.planner import parse_planner_decision
        decision = parse_planner_decision(_giveup_json("unsolvable conflict"))
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "giveup")
        self.assertEqual(decision.reason, "unsolvable conflict")

    def test_returns_none_on_empty_string(self):
        from src.envstate.planner import parse_planner_decision
        self.assertIsNone(parse_planner_decision(""))

    def test_returns_none_on_none(self):
        from src.envstate.planner import parse_planner_decision
        self.assertIsNone(parse_planner_decision(None))

    def test_returns_none_on_no_json(self):
        from src.envstate.planner import parse_planner_decision
        self.assertIsNone(parse_planner_decision("Here is my plan."))

    def test_returns_none_on_missing_action_key(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"goal": "install deps", "layer": "deps"})
        self.assertIsNone(parse_planner_decision(bad))

    def test_returns_none_on_unknown_action_value(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"action": "skip", "goal": "x", "layer": "deps",
                          "done_when": "y", "facts": []})
        self.assertIsNone(parse_planner_decision(bad))

    def test_task_with_empty_facts_list(self):
        from src.envstate.planner import parse_planner_decision
        raw = _task_json(goal="install system deps", done_when="exit 0",
                         layer="system", facts=[])
        decision = parse_planner_decision(raw)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.task.facts, ())

    def test_task_action_missing_goal_returns_none(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"action": "task", "done_when": "exit 0",
                          "layer": "deps", "facts": []})
        self.assertIsNone(parse_planner_decision(bad))

    def test_task_action_missing_layer_returns_none(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"action": "task", "goal": "x",
                          "done_when": "exit 0", "facts": []})
        self.assertIsNone(parse_planner_decision(bad))

    def test_task_action_missing_done_when_returns_none(self):
        from src.envstate.planner import parse_planner_decision
        bad = json.dumps({"action": "task", "goal": "x",
                          "layer": "deps", "facts": []})
        self.assertIsNone(parse_planner_decision(bad))

    def test_parses_json_inside_fenced_block(self):
        from src.envstate.planner import parse_planner_decision
        fenced = (
            "Here is my decision.\n```json\n"
            + _task_json(goal="run poetry install", done_when="exit 0",
                         layer="deps", facts=[])
            + "\n```"
        )
        decision = parse_planner_decision(fenced)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "task")


# ---------------------------------------------------------------------------
# 3. Planner.decide — happy paths
# ---------------------------------------------------------------------------

class PlannerDecideTests(unittest.TestCase):
    def test_decide_returns_task_for_unmet_layer(self):
        """With an unmet deps layer the planner emits action='task'."""
        from src.envstate.planner import Planner
        content = _task_json(
            goal="install project deps via poetry",
            done_when="poetry install exits 0 and python -c 'import edsl' works",
            layer="deps",
            facts=["build_system=poetry", "pyproject.toml present"],
        )
        planner = Planner(client=_fake_client(content), model="test-model")
        m = _base_map()
        decision = planner.decide(m)
        self.assertIsInstance(decision, PlannerDecision)
        self.assertEqual(decision.action, "task")
        self.assertIsNotNone(decision.task)
        self.assertIsInstance(decision.task, Task)
        self.assertEqual(decision.task.layer, "deps")

    def test_decide_rejects_self_declared_done(self):
        """A self-declared 'done' is invalid; after retries decide falls back to giveup."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_done_json("collect-only passed")),
                          model="test-model")
        m = merge_map(_base_map(), done_flag=True)
        decision = planner.decide(m)
        self.assertEqual(decision.action, "giveup")

    def test_decide_returns_giveup_on_no_path(self):
        """When the LLM emits giveup, decide returns PlannerDecision(action='giveup')."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_giveup_json("unsolvable")),
                          model="test-model")
        m = _base_map()
        decision = planner.decide(m)
        self.assertEqual(decision.action, "giveup")
        self.assertEqual(decision.reason, "unsolvable")

    def test_decide_usage_dict_returned(self):
        """decide must return the PlannerDecision without exposing usage; usage is internal."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_task_json()), model="test-model")
        m = _base_map()
        decision = planner.decide(m)
        # decide() returns only PlannerDecision; caller accesses tokens via last_usage
        self.assertIsInstance(decision, PlannerDecision)

    def test_decide_exposes_last_usage(self):
        """After decide(), planner.last_usage has input_tokens/output_tokens/total_tokens."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_task_json()), model="test-model")
        planner.decide(_base_map())
        self.assertIn("total_tokens", planner.last_usage)
        self.assertEqual(planner.last_usage["total_tokens"], 30)

    def test_on_usage_callback_called_after_decide(self):
        """on_usage callback must be called with the usage dict after each LLM completion."""
        from src.envstate.planner import Planner
        received: list[dict] = []
        planner = Planner(
            client=_fake_client(_task_json()),
            model="test-model",
            on_usage=lambda u: received.append(u),
        )
        planner.decide(_base_map())
        self.assertEqual(len(received), 1)
        self.assertIn("total_tokens", received[0])

    def test_on_usage_none_does_not_raise(self):
        """Planner constructed without on_usage must not raise during decide."""
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_task_json()), model="test-model")
        # Should not raise
        planner.decide(_base_map())


# ---------------------------------------------------------------------------
# 4. Planner.decide — out_of_scope routing
# ---------------------------------------------------------------------------

class PlannerOutOfScopeTests(unittest.TestCase):
    """The planner can mark an OpenProblem out_of_scope and still emit a task."""

    def _map_with_runtime_only_problem(self) -> WorldModelMap:
        """Map with a runtime-only open problem (e.g. swift not installed)."""
        m = _base_map()
        op = OpenProblem(
            signature="swift: command not found",
            interpretation="swift runtime not available; runtime-only dep",
            layer="runtime",
            out_of_scope=False,
        )
        return merge_map(m, open_problems=(op,))

    def test_planner_routes_around_out_of_scope_problem_and_emits_task(self):
        """Planner marks a runtime-only problem out_of_scope and emits a deps task anyway."""
        from src.envstate.planner import Planner
        # LLM returns a 'task' decision targeting a different layer, ignoring the swift problem
        content = _task_json(
            goal="install Python deps",
            done_when="poetry install exits 0",
            layer="deps",
            facts=["swift: command not found marked out_of_scope"],
        )
        planner = Planner(client=_fake_client(content), model="test-model")
        m = self._map_with_runtime_only_problem()
        decision = planner.decide(m)
        self.assertEqual(decision.action, "task")
        self.assertEqual(decision.task.layer, "deps")

    def test_planner_receives_map_in_prompt_including_open_problems(self):
        """The rendered planning view passed to the LLM includes the open_problems."""
        from src.envstate.planner import Planner, render_planning_view
        m = self._map_with_runtime_only_problem()
        view = render_planning_view(m, budget={"cycles_remaining": 10})
        self.assertIn("swift: command not found", view)
        self.assertIn("runtime", view)

    def test_done_flag_true_in_map_triggers_done_response(self):
        """If map.done_flag is already True the planner should receive that in its view."""
        from src.envstate.planner import render_planning_view
        m = merge_map(_base_map(), done_flag=True)
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        self.assertIn("done_flag", view)
        self.assertIn("True", view)


# ---------------------------------------------------------------------------
# 5. Planner.decide — retry behaviour
# ---------------------------------------------------------------------------

class PlannerRetryTests(unittest.TestCase):
    """Planner retries when LLM returns empty or unparseable JSON."""

    def test_empty_then_valid_retries_and_returns_task(self):
        """Two-attempt sequence: empty first, valid task second."""
        from src.envstate.planner import Planner
        client, call_log = _sequential_client(["", _task_json()])
        planner = Planner(client=client, model="test-model")
        decision = planner.decide(_base_map())
        self.assertEqual(decision.action, "task")
        self.assertEqual(len(call_log), 2, "must have retried once")

    def test_bad_json_then_valid_retries_and_returns_task(self):
        """Unparseable JSON on attempt 1, valid on attempt 2."""
        from src.envstate.planner import Planner
        client, call_log = _sequential_client([
            '{"action": "skip"}',  # unknown action → parse returns None
            _task_json(),
        ])
        planner = Planner(client=client, model="test-model")
        decision = planner.decide(_base_map())
        self.assertEqual(decision.action, "task")
        self.assertEqual(len(call_log), 2)

    def test_all_attempts_fail_returns_giveup_fallback(self):
        """If every attempt fails to parse, decide returns a giveup PlannerDecision."""
        from src.envstate.planner import Planner
        client, _ = _sequential_client(["", "", ""])
        planner = Planner(client=client, model="test-model")
        decision = planner.decide(_base_map())
        self.assertEqual(decision.action, "giveup")
        self.assertIn("empty", decision.reason.lower())


# ---------------------------------------------------------------------------
# 6. render_planning_view content
# ---------------------------------------------------------------------------

class RenderPlanningViewTests(unittest.TestCase):
    def test_view_includes_base_image_and_build_system(self):
        from src.envstate.planner import render_planning_view
        m = _base_map()
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        self.assertIn("python:3.12-slim", view)
        self.assertIn("poetry", view)

    def test_view_includes_progress_layers(self):
        from src.envstate.planner import render_planning_view
        m = _base_map()
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        for layer in ("base", "system", "runtime", "deps", "build", "tests"):
            self.assertIn(layer, view)

    def test_view_includes_open_problems(self):
        from src.envstate.planner import render_planning_view
        op = OpenProblem(
            signature="ModuleNotFoundError: psycopg2",
            interpretation="missing C extension",
            layer="deps",
        )
        m = merge_map(_base_map(), open_problems=(op,))
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        self.assertIn("ModuleNotFoundError: psycopg2", view)

    def test_view_includes_installed_facts(self):
        from src.envstate.planner import render_planning_view
        m = merge_map(_base_map(), installed=(Fact(name="libpq-dev", detail="14"),))
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        self.assertIn("libpq-dev", view)

    def test_view_includes_notes(self):
        from src.envstate.planner import render_planning_view
        m = merge_map(_base_map(), notes=("do not use psycopg2-binary",))
        view = render_planning_view(m, budget={"cycles_remaining": 8})
        self.assertIn("do not use psycopg2-binary", view)

    def test_view_includes_cycles_remaining(self):
        from src.envstate.planner import render_planning_view
        view = render_planning_view(_base_map(), budget={"cycles_remaining": 7})
        self.assertIn("7", view)

    def test_view_includes_required_facts(self):
        from src.envstate.planner import render_planning_view
        m = initial_map(
            base_image="python:3.12-slim",
            workdir="/app",
            language="python 3.12",
            build_system="pip",
            repo_layout=(),
            required=(Fact(name="flask", detail=">=2.0"),),
        )
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        self.assertIn("flask", view)


# -- append to tests/test_envstate_planner.py --

class PlannerPromptIncludesMapFieldsTests(unittest.TestCase):
    """The rendered view must surface every WorldModelMap field the Planner needs."""

    def test_view_includes_language(self):
        from src.envstate.planner import render_planning_view
        m = _base_map()
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        self.assertIn("python 3.12", view)

    def test_view_includes_workdir(self):
        from src.envstate.planner import render_planning_view
        view = render_planning_view(_base_map(), budget={"cycles_remaining": 5})
        self.assertIn("/app", view)

    def test_view_marks_completed_layers_with_checkmark(self):
        from src.envstate.planner import render_planning_view
        m = merge_map(_base_map(), progress={
            "base": True, "system": True, "runtime": False,
            "deps": False, "build": False, "tests": False,
        })
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        # Both symbols must appear for done vs not-done
        self.assertIn("✓", view)
        self.assertIn("✗", view)

    def test_view_shows_out_of_scope_marker(self):
        from src.envstate.planner import render_planning_view
        op = OpenProblem(signature="swift not found", interpretation="runtime-only",
                         layer="runtime", out_of_scope=True)
        m = merge_map(_base_map(), open_problems=(op,))
        view = render_planning_view(m, budget={"cycles_remaining": 5})
        self.assertIn("out_of_scope", view)


class PlannerNoSelfDeclaredDoneTests(unittest.TestCase):
    """The Planner has no `done` action; only the structural done_flag (checked
    by the orchestrator) may declare success. A model that says done is ignored
    and the planner falls back to giveup after retries.
    """

    def test_self_declared_done_falls_back_to_giveup(self):
        from src.envstate.planner import Planner
        planner = Planner(client=_fake_client(_done_json("collect-only passed")),
                          model="m")
        d = planner.decide(merge_map(_base_map(), done_flag=True))
        self.assertEqual(d.action, "giveup")


class PlannerFactsPassedDownTests(unittest.TestCase):
    """Facts extracted from the map must be included in the task handed to BuildAgent."""

    def test_task_facts_are_strings(self):
        from src.envstate.planner import Planner
        content = _task_json(facts=["flask>=2.0 in pyproject.toml", "build_system=poetry"])
        planner = Planner(client=_fake_client(content), model="m")
        d = planner.decide(_base_map())
        for fact in d.task.facts:
            self.assertIsInstance(fact, str)

    def test_task_layer_is_a_known_layer(self):
        from src.envstate.planner import Planner
        known_layers = {"base", "system", "runtime", "deps", "build", "tests"}
        content = _task_json(layer="deps")
        planner = Planner(client=_fake_client(content), model="m")
        d = planner.decide(_base_map())
        self.assertIn(d.task.layer, known_layers)


# ---------------------------------------------------------------------------
# 7. Principled prompt contract (the single Planner prompt; flag removed)
# ---------------------------------------------------------------------------

class PrincipledPromptContractTests(unittest.TestCase):
    """PLANNER_SYSTEM_PROMPT is now the single, principled prompt — there is no
    variant flag, no PRINCIPLED_ alias, no allow_done / prompt_variant params."""

    def test_prompt_drops_dead_out_of_scope_instructions(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        self.assertNotIn("out_of_scope", PLANNER_SYSTEM_PROMPT)

    def test_prompt_is_mechanism_grounded(self):
        from src.envstate.planner import PLANNER_SYSTEM_PROMPT
        self.assertIn("root cause", PLANNER_SYSTEM_PROMPT.lower())

    def test_no_principled_alias_constant(self):
        import src.envstate.planner as planner_mod
        self.assertFalse(
            hasattr(planner_mod, "PRINCIPLED_PLANNER_SYSTEM_PROMPT"),
            "the PRINCIPLED_ alias should be folded into PLANNER_SYSTEM_PROMPT",
        )

    def test_render_header_is_environment_state_map(self):
        from src.envstate.planner import render_planning_view
        view = render_planning_view(_base_map(), budget={"cycles_remaining": 5})
        self.assertIn("# Environment State Map", view)
        self.assertNotIn("# WorldModelMap", view)

    def test_parser_has_no_allow_done_param(self):
        import inspect
        from src.envstate.planner import parse_planner_decision
        self.assertNotIn(
            "allow_done", inspect.signature(parse_planner_decision).parameters
        )

    def test_planner_has_no_prompt_variant_param(self):
        import inspect
        from src.envstate.planner import Planner
        self.assertNotIn(
            "prompt_variant", inspect.signature(Planner.__init__).parameters
        )

    def test_valid_actions_excludes_done(self):
        from src.envstate.planner import _VALID_ACTIONS
        self.assertNotIn("done", _VALID_ACTIONS)
        self.assertEqual(_VALID_ACTIONS, frozenset({"task", "giveup"}))


if __name__ == "__main__":
    unittest.main()
