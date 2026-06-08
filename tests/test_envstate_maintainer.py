import unittest
from types import SimpleNamespace

from src.envstate.maintainer import (
    MAINTAINER_SYSTEM_PROMPT,
    Maintainer,
    build_maintainer_input,
    parse_maintainer_proposal,
)
from src.envstate.ledger import ActionEvent


def _fake_client(content):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_k: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                )
            )
        )
    )


class MaintainerInputTests(unittest.TestCase):
    def test_input_contains_residual_spans_not_full_log(self):
        event = ActionEvent(
            step=7, task_id="task-004", cmd="pip install psycopg2==2.8.6", rc=1,
            stdout_path=None, stderr_path=None, env_revision_before=7, env_revision_after=7,
            mutation_class=None, container_id="abc123", summary="failed",
        )
        full_log = "\n".join(["noise"] * 500 + ["Error: pg_config executable not found"])
        payload = build_maintainer_input({}, {"task_id": "task-004"}, event, full_log)
        self.assertIn("pg_config executable not found", payload["residual_spans"])
        self.assertLess(len(payload["residual_spans"]), len(full_log))


class MaintainerParseTests(unittest.TestCase):
    def test_parses_well_formed_proposal(self):
        content = (
            'Here is my analysis.\n'
            '```json\n'
            '{"candidate_requirements": [{"id": "tool:pg_config", "name": "pg_config", '
            '"kind": "Tool", "status": "REQUIRED", "source": "LLM_GUESS", '
            '"required_by": ["psycopg2==2.8.6"]}], '
            '"open_failure_updates": [{"signature": "pg_config executable not found", '
            '"hypothesis": "source build needs PostgreSQL dev tooling"}], '
            '"diagnose_requests": [{"kind": "apt_provider", "capability": "pg_config"}], '
            '"probe_requests": [{"kind": "cli", "name": "pg_config", "predicate": "path exists"}], '
            '"plan_notes": ["Do not substitute psycopg2-binary."]}\n'
            '```\n'
        )
        proposal = parse_maintainer_proposal(content)
        self.assertEqual(proposal["candidate_requirements"][0]["name"], "pg_config")
        self.assertEqual(proposal["probe_requests"][0]["name"], "pg_config")

    def test_returns_empty_proposal_on_unparseable_content(self):
        self.assertEqual(parse_maintainer_proposal("no json here"), {})
        self.assertEqual(parse_maintainer_proposal(None), {})


class MaintainerInterpretTests(unittest.TestCase):
    def test_interpret_applies_acl_and_reports_rejections(self):
        from src.envstate.types import BaseFacts, EnvStateSnapshot
        snap = EnvStateSnapshot(revision=7, container_id="abc123", base=BaseFacts(image="python:3.11-slim"))
        # LLM tries to smuggle a PRESENT fact — ACL must drop it.
        content = (
            '```json\n{"candidate_requirements": ['
            '{"id": "tool:pg_config", "name": "pg_config", "kind": "Tool", "status": "REQUIRED", "source": "LLM_GUESS"},'
            '{"id": "tool:sneaky", "name": "sneaky", "kind": "Tool", "status": "PRESENT", "source": "LLM_GUESS"}'
            ']}\n```'
        )
        maintainer = Maintainer(client=_fake_client(content), model="test-model")
        event = ActionEvent(step=7, task_id=None, cmd="pip install x", rc=1, stdout_path=None,
                            stderr_path=None, env_revision_before=7, env_revision_after=7,
                            mutation_class=None, container_id="abc123", summary="failed")
        updated, proposal, rejected, usage = maintainer.interpret(snap, {}, event, "Error: pg_config not found")
        ids = [r.id for r in updated.requirements]
        self.assertIn("tool:pg_config", ids)
        self.assertNotIn("tool:sneaky", ids)
        self.assertEqual(len(rejected), 1)


    def test_interpret_tolerates_empty_content(self):
        from src.envstate.types import BaseFacts, EnvStateSnapshot
        snap = EnvStateSnapshot(revision=1, container_id="c1", base=BaseFacts(image="python:3.11-slim"))
        maintainer = Maintainer(client=_fake_client(""), model="m")
        event = ActionEvent(step=1, task_id=None, cmd="x", rc=1, stdout_path=None, stderr_path=None,
                            env_revision_before=1, env_revision_after=1, mutation_class=None,
                            container_id="c1", summary="")
        updated, proposal, rejected, usage = maintainer.interpret(snap, {}, event, "no errors")
        self.assertEqual(proposal, {})
        self.assertEqual(updated.requirements, ())


class MaintainerContractTests(unittest.TestCase):
    def test_system_prompt_forbids_presence_and_evidence(self):
        self.assertIn("PRESENT", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("MISSING", MAINTAINER_SYSTEM_PROMPT)
        self.assertIn("Evidence", MAINTAINER_SYSTEM_PROMPT)
