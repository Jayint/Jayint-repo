# tests/test_planner_recipe.py
from src.envstate.planner import parse_planner_decision


def _json(body: str) -> str:  # planner expects a ```json fenced object
    return "```json\n" + body + "\n```"


def test_parses_recipe_patch() -> None:
    d = parse_planner_decision(_json('''
    {"action":"apply_recipe_patch","target_node_ids":["contract:system_library:libGL.so.1"],
     "recipe_patch":{"steps":[
       {"id":"s1","kind":"system_install","command":"apt-get install -y libgl1",
        "target_node_ids":["contract:system_library:libGL.so.1"]},
       {"id":"s2","kind":"validation","command":"python -c \\"import cv2\\"",
        "target_node_ids":["contract:python_import:cv2"]}]}}'''))
    assert d is not None
    assert d.action == "apply_recipe_patch"
    assert len(d.recipe_patch.steps) == 2 and d.recipe_patch.steps[0].command.startswith("apt-get")


def test_rejects_ungrounded_step() -> None:
    d = parse_planner_decision(_json('''
    {"action":"apply_recipe_patch","recipe_patch":{"steps":[
       {"id":"s1","kind":"system_install","command":"apt-get install -y libgl1","target_node_ids":[]}]}}'''))
    assert d is None


def test_done_and_giveup_unchanged() -> None:
    assert parse_planner_decision(_json('{"action":"giveup","reason":"no tests"}')).action == "giveup"
    assert parse_planner_decision(_json(
        '{"action":"done","satisfied_goal_contract_ids":["contract:goal:repo_tests_pass"]}')).action == "done"
