import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.actions import action_from_tool_call, extract_reasoning, EditOp, TOOLS_SCHEMA


# --- explore ---------------------------------------------------------------
def test_explore_tool_call():
    a = action_from_tool_call("explore", '{"command": "ls /app"}')
    assert a.kind == "explore" and a.command == "ls /app"

def test_explore_empty_command_is_invalid():
    assert action_from_tool_call("explore", '{"command": ""}').kind == "invalid"


# --- edit ------------------------------------------------------------------
def test_edit_insert_tool_call():
    a = action_from_tool_call("edit", '{"verb": "insert", "start": 8, "content": "apt-get install -y git"}')
    assert a.kind == "edit" and a.edit == EditOp("insert", 8, 8, "apt-get install -y git")

def test_edit_replace_range_tool_call():
    a = action_from_tool_call("edit", '{"verb": "replace", "start": 3, "end": 5, "content": "pip install x"}')
    assert a.kind == "edit" and a.edit.verb == "replace" and (a.edit.start, a.edit.end) == (3, 5)

def test_edit_delete_needs_no_content():
    a = action_from_tool_call("edit", '{"verb": "delete", "start": 7}')
    assert a.kind == "edit" and a.edit == EditOp("delete", 7, 7, "")

def test_edit_insert_after_verb_is_normalized():
    a = action_from_tool_call("edit", '{"verb": "insert after", "start": 2, "content": "x"}')
    assert a.kind == "edit" and a.edit.verb == "insert" and a.edit.start == 2

def test_edit_replace_without_content_is_invalid():
    assert action_from_tool_call("edit", '{"verb": "replace", "start": 3}').kind == "invalid"

def test_edit_string_line_number_is_coerced():
    # some models emit the integer as a JSON string; tolerate it rather than reject the whole edit.
    a = action_from_tool_call("edit", '{"verb": "delete", "start": "9"}')
    assert a.kind == "edit" and a.edit.start == 9

def test_edit_unknown_verb_is_invalid():
    assert action_from_tool_call("edit", '{"verb": "frobnicate", "start": 1, "content": "x"}').kind == "invalid"


# --- robustness ------------------------------------------------------------
def test_malformed_json_arguments_is_invalid():
    assert action_from_tool_call("edit", "{not json").kind == "invalid"

def test_unknown_tool_name_is_invalid():
    assert action_from_tool_call("summon_daemon", "{}").kind == "invalid"


# --- reasoning (<think>) extraction ---------------------------------------
def test_extract_reasoning_from_think_block():
    assert extract_reasoning("<think>\ngit CLI is missing\n</think>") == "git CLI is missing"

def test_extract_reasoning_plain_content_when_no_think():
    assert extract_reasoning("just some reasoning text") == "just some reasoning text"

def test_extract_reasoning_empty_for_no_content():
    assert extract_reasoning("") == "" and extract_reasoning(None) == ""


# --- schema shape ----------------------------------------------------------
def test_tools_schema_has_explore_and_edit_functions():
    names = {t["function"]["name"] for t in TOOLS_SCHEMA}
    assert names == {"explore", "edit"}
    edit = next(t for t in TOOLS_SCHEMA if t["function"]["name"] == "edit")
    props = edit["function"]["parameters"]["properties"]
    assert {"verb", "start", "end", "content"} <= set(props)
