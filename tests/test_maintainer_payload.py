# tests/test_maintainer_payload.py
"""Design §4.4: the Maintainer's user payload must include the pre-filled facts
(installed, required, env) + the `required - installed` diff so the LLM can
attribute a failure to a layer and judge `resolved`. Variant B = literal full
`installed` list ({name, detail}). The OUTPUT schema stays narrow (tested in
test_maintainer_narrowed.py); this only enriches what the LLM sees.
"""
import json
from types import SimpleNamespace

from src.envstate.maintainer import Maintainer, MAINTAINER_SYSTEM_PROMPT
from src.envstate.world_model import (
    initial_map, merge_map, Fact, TaskReport, CommandRecord,
)


def _capturing_client(captured):
    def create(**kw):
        captured["messages"] = kw.get("messages")
        reply = "```json\n" + json.dumps({"open_problems": [], "resolved": [], "notes": []}) + "\n```"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply, reasoning=None))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _map():
    base = initial_map(
        base_image="python:3.12", workdir="/app", language="Python 3.12.1",
        build_system="pip", repo_layout=("requirements.txt",),
        required=(Fact("flask", ">=2.0"), Fact("psycopg2-binary", "==2.9.5")),
    )
    return merge_map(
        base,
        installed=(Fact("flask", "3.0.0"), Fact("pip", "25.0")),
        env={"python_version": "Python 3.12.1",
             "which_python": "/usr/local/bin/python", "venv": ""},
    )


def _report():
    return TaskReport("install deps", "blocked",
                      (CommandRecord("pip install psycopg2-binary", 1, "pg_config not found"),),
                      "blocked on system header")


def _payload(captured):
    return json.loads(captured["messages"][1]["content"])["current_map"]


def test_payload_includes_installed_required_env():
    captured = {}
    Maintainer(_capturing_client(captured), "m").update(_map(), _report())
    cm = _payload(captured)
    assert {f["name"].lower() for f in cm["installed"]} == {"flask", "pip"}
    assert {f["name"].lower() for f in cm["required"]} == {"flask", "psycopg2-binary"}
    assert cm["env"]["which_python"] == "/usr/local/bin/python"


def test_payload_includes_required_minus_installed_diff():
    captured = {}
    Maintainer(_capturing_client(captured), "m").update(_map(), _report())
    cm = _payload(captured)
    # flask is installed; psycopg2-binary is declared-but-missing
    assert cm["missing"] == ["psycopg2-binary"]


def test_installed_is_literal_full_objects_with_detail():
    # Variant B: full {name, detail} objects, not just names
    captured = {}
    Maintainer(_capturing_client(captured), "m").update(_map(), _report())
    cm = _payload(captured)
    flask = next(f for f in cm["installed"] if f["name"].lower() == "flask")
    assert flask["detail"] == "3.0.0"


def test_prompt_directs_use_of_facts_for_attribution():
    # the system prompt must reference the `missing` diff so the LLM uses it
    assert "missing" in MAINTAINER_SYSTEM_PROMPT.lower()
