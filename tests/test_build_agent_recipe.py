"""tests/test_build_agent_recipe.py — TDD for BuildAgent.run_recipe and recipe_budget.

Run with:
    .venv/bin/python -m pytest tests/test_build_agent_recipe.py -q
"""
import types

from src.envstate.build_agent import BuildAgent, recipe_budget
from src.envstate.world_model import RecipePatch, RecipeStep
from src.envstate.ledger import ActionLedger


def _stub_response(content: str) -> types.SimpleNamespace:
    """Return a minimal OpenAI-compatible response object."""
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content),
            finish_reason="stop",
        )],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class _StubClient:
    """SimpleNamespace-based stub that drives the mini-ReAct loop to execute each
    step's command verbatim, then emits Final Answer: Success (for successes) or
    repeats the command until the stuck guard fires (for failures).

    Sequence for the recipe test (step1=success, step2=fail, step3=skipped):
      [1] "Action: apt-get install -y libgl1"   ← step 1 runs the command
      [2] "Final Answer: Success"                ← step 1 completes
      [3] "Action: fail-here"                    ← step 2 attempt 1
      [4] "Action: fail-here"                    ← step 2 attempt 2
      [5] "Action: fail-here"                    ← step 2 attempt 3 → stuck guard fires
    """

    def __init__(self) -> None:
        _responses = [
            # Step 1: system install → success
            "Thought: run step 1\nAction: apt-get install -y libgl1",
            "Thought: done\nFinal Answer: Success",
            # Step 2: fail-here × 3 → stuck guard fires (2 identical failures in history)
            "Thought: run step 2\nAction: fail-here",
            "Thought: retry\nAction: fail-here",
            "Thought: retry again\nAction: fail-here",
        ]

        def _create(**kwargs: object) -> types.SimpleNamespace:
            return _stub_response(_responses.pop(0))

        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=_create)
        )


def test_recipe_budget_scales_with_steps() -> None:
    assert recipe_budget(1) < recipe_budget(5) <= recipe_budget(50)  # monotone, capped


def test_run_recipe_executes_steps_in_order_and_stops_on_failure() -> None:
    calls: list[str] = []

    def sandbox_execute(cmd: str) -> tuple[bool, str]:
        calls.append(cmd)
        return (False, "E: boom") if "fail" in cmd else (True, "ok")

    rp = RecipePatch(steps=(
        RecipeStep("s1", "system_install", "apt-get install -y libgl1", ("contract:system_library:libGL.so.1",)),
        RecipeStep("s2", "system_install", "fail-here", ("contract:x",)),
        RecipeStep("s3", "validation", "python -c 'import cv2'", ("contract:python_import:cv2",))))
    ba = BuildAgent(client=_StubClient(), model="m", synthesizer=None, container_id="c")
    report = ba.run_recipe(rp, sandbox_execute, ActionLedger(), step_offset=0)
    assert "apt-get install -y libgl1" in calls          # step 1 ran
    assert report.status == "blocked"                    # stopped on s2
    assert "python -c 'import cv2'" not in calls          # s3 (after failure) did not run
