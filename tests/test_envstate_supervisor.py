import unittest
from types import SimpleNamespace

from src.envstate.supervisor import (
    SUPERVISOR_SYSTEM_PROMPT,
    Supervisor,
    parse_task_spec,
    render_planning_view,
)
from src.envstate.types import (
    BaseFacts,
    EnvStateSnapshot,
    OpenFailure,
    Requirement,
    Source,
    Status,
)
from src.envstate.ledger import ActionLedger


def _fake_client(content):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_k: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30),
                )
            )
        )
    )


def _snapshot():
    return EnvStateSnapshot(
        revision=7, container_id="abc123",
        base=BaseFacts(image="python:3.11-slim", python="3.11.9"),
        requirements=(
            Requirement(id="lang:psycopg2==2.8.6", name="psycopg2", kind="LanguagePackage",
                        status=Status.REQUIRED, source=Source.STATIC_SCAN, specifier="==2.8.6"),
            Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                        status=Status.REQUIRED, source=Source.LLM_GUESS),
        ),
        open_failures=(OpenFailure(signature="pg_config executable not found",
                                   first_seen_revision=7, last_seen_revision=7,
                                   hypothesis="psycopg2 source build needs PostgreSQL dev tooling"),),
        plan_notes=("Do not substitute psycopg2-binary for pinned psycopg2.",),
    )


class PlanningViewTests(unittest.TestCase):
    def test_view_includes_open_failures_requirements_and_notes(self):
        view = render_planning_view(_snapshot(), ActionLedger(), budget={"steps_remaining": 20})
        self.assertIn("psycopg2", view)
        self.assertIn("pg_config executable not found", view)
        self.assertIn("Do not substitute psycopg2-binary", view)
        self.assertIn("revision 7", view)


class TaskSpecParseTests(unittest.TestCase):
    def test_parses_task_spec_json(self):
        content = (
            '```json\n{"task_id": "task-004", "phase": "Native/System Dependency Resolution", '
            '"goal": "Resolve missing pg_config", "relevant_state": ["pip install failed"], '
            '"constraints": ["Do not edit requirements.txt"], "allowed_actions": ["install system packages"], '
            '"success_criteria": ["pg_config probe passes"], "stop_conditions": ["more than 4 actions"], '
            '"suggested_tactics": ["apt-get install -y libpq-dev"]}\n```'
        )
        spec = parse_task_spec(content)
        self.assertEqual(spec["task_id"], "task-004")
        self.assertEqual(spec["phase"], "Native/System Dependency Resolution")

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_task_spec("no json"))


class SupervisorTests(unittest.TestCase):
    def test_next_task_returns_parsed_taskspec(self):
        content = ('```json\n{"task_id": "task-001", "phase": "Repository Analysis", '
                   '"goal": "Identify dependency strategy", "relevant_state": [], "constraints": [], '
                   '"allowed_actions": ["inspect files"], "success_criteria": ["strategy known"], '
                   '"stop_conditions": ["budget"], "suggested_tactics": []}\n```')
        sup = Supervisor(client=_fake_client(content), model="test-model")
        spec, usage = sup.next_task(_snapshot(), ActionLedger(), budget={"steps_remaining": 30})
        self.assertEqual(spec["task_id"], "task-001")
        self.assertEqual(usage["total_tokens"], 30)


class SupervisorContractTests(unittest.TestCase):
    def test_prompt_forbids_certifying_presence(self):
        self.assertIn("do not certify", SUPERVISOR_SYSTEM_PROMPT.lower())
        self.assertIn("TaskSpec", SUPERVISOR_SYSTEM_PROMPT)
