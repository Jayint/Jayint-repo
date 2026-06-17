"""BUG-10: BuildAgent.run_recipe must report completed_steps on every return path.

  - all steps complete  -> completed_steps == n_steps
  - empty recipe        -> completed_steps == 0
  - blocks at step k    -> completed_steps == (k - 1)  [steps with idx < k-1 done]

Stub-client pattern mirrors tests/test_build_agent_recipe.py.
"""
import types

from src.envstate.build_agent import BuildAgent
from src.envstate.world_model import RecipePatch, RecipeStep
from src.envstate.ledger import ActionLedger


def _stub_response(content: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content),
            finish_reason="stop",
        )],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class _ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

        def _create(**kwargs: object) -> types.SimpleNamespace:
            return _stub_response(self._responses.pop(0))

        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=_create)
        )


def test_empty_recipe_completed_steps_zero() -> None:
    ba = BuildAgent(client=_ScriptedClient([]), model="m", synthesizer=None, container_id="c")
    report = ba.run_recipe(RecipePatch(steps=()), lambda cmd: (True, "ok"), ActionLedger())
    assert report.status == "done"
    assert report.completed_steps == 0


def test_all_steps_done_completed_steps_equals_n() -> None:
    rp = RecipePatch(steps=(
        RecipeStep("s1", "system_install", "apt-get install -y libgl1", ()),
        RecipeStep("s2", "validation", "python -c 'import cv2'", ()),
    ))
    client = _ScriptedClient([
        "Final Answer: Success",  # step 1 done
        "Final Answer: Success",  # step 2 done -> all complete
    ])
    ba = BuildAgent(client=client, model="m", synthesizer=None, container_id="c")
    report = ba.run_recipe(rp, lambda cmd: (True, "ok"), ActionLedger())
    assert report.status == "done"
    assert report.completed_steps == 2


def test_block_at_step_two_completed_steps_one() -> None:
    def sandbox_execute(cmd: str) -> tuple[bool, str]:
        return (False, "E: boom") if "fail" in cmd else (True, "ok")

    rp = RecipePatch(steps=(
        RecipeStep("s1", "system_install", "apt-get install -y libgl1", ()),
        RecipeStep("s2", "system_install", "fail-here", ()),
        RecipeStep("s3", "validation", "python -c 'import cv2'", ()),
    ))
    client = _ScriptedClient([
        "Action: apt-get install -y libgl1",  # step 1 runs
        "Final Answer: Success",              # step 1 done -> idx=1
        "Action: fail-here",                  # step 2 attempt 1
        "Action: fail-here",                  # step 2 attempt 2
        "Action: fail-here",                  # step 2 attempt 3 -> stuck guard
    ])
    ba = BuildAgent(client=client, model="m", synthesizer=None, container_id="c")
    report = ba.run_recipe(rp, sandbox_execute, ActionLedger(), step_offset=0)
    assert report.status == "blocked"
    assert report.completed_steps == 1  # step 0 done; step 1 (idx) failed
