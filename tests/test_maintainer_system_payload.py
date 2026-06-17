# tests/test_maintainer_system_payload.py
"""Task 6: Maintainer surfaces compact system facts (build_tools/os_release/system_tools)
without dumping the bulky system_installed list wholesale.
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


def _map_with_system():
    """A WorldModelMap with mixed system_installed facts and compact env keys."""
    base = initial_map(
        base_image="python:3.12", workdir="/app", language="Python 3.12.1",
        build_system="pip", repo_layout=("requirements.txt",),
    )
    return merge_map(
        base,
        system_installed=(
            Fact("pg_config", "tool"),           # curated tool — should appear in system_tools
            Fact("libxml-2.0", "pkgconfig"),     # pkg-config module — should appear in system_tools
            Fact("libpq-dev", "dpkg"),           # bulky apt entry — should NOT appear in system_tools
        ),
        env={
            "python_version": "Python 3.12.1",
            "os_release": "debian:bookworm",
            "build_tools": "gcc,pg_config",
        },
    )


def _report():
    return TaskReport(
        "install deps", "blocked",
        (CommandRecord("pip install psycopg2-binary", 1, "pg_config not found"),),
        "blocked on system header",
    )


def _current_map(captured):
    """Extract the serialized current_map from the captured LLM messages."""
    return json.loads(captured["messages"][1]["content"])["current_map"]


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def test_payload_includes_build_tools():
    """build_tools from env is surfaced in the payload current_map."""
    captured = {}
    Maintainer(_capturing_client(captured), "m").update(_map_with_system(), _report())
    cm = _current_map(captured)
    assert cm["build_tools"] == "gcc,pg_config"


def test_payload_includes_os_release():
    """os_release from env is surfaced in the payload current_map."""
    captured = {}
    Maintainer(_capturing_client(captured), "m").update(_map_with_system(), _report())
    cm = _current_map(captured)
    assert "debian" in cm["os_release"]


def test_payload_system_tools_includes_tool_and_pkgconfig_but_not_dpkg():
    """system_tools includes tool + pkgconfig names, excludes dpkg-detail facts."""
    captured = {}
    Maintainer(_capturing_client(captured), "m").update(_map_with_system(), _report())
    cm = _current_map(captured)
    assert set(cm["system_tools"]) == {"pg_config", "libxml-2.0"}


def test_payload_does_not_dump_system_installed_wholesale():
    """The bulky system_installed list is NOT present as a top-level key in current_map."""
    captured = {}
    Maintainer(_capturing_client(captured), "m").update(_map_with_system(), _report())
    cm = _current_map(captured)
    assert "system_installed" not in cm


