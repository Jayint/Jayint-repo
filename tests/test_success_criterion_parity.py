"""test_success_criterion_parity.py — TDD parity tests (RED phase).

Asserts that all three EnvState prompts contain the same success criterion
goalpost as the legacy bare-ReAct arm (src/planner.py):
  - "pytest --collect-only -q --disable-warnings" (standard)
  - "poetry run pytest --collect-only -q --disable-warnings" (Poetry variant)

Also asserts that existing critical structural phrases are preserved.

Conventions: unittest.TestCase (NO pytest fixtures/markers).
Run via: .venv/bin/python -m pytest tests/test_success_criterion_parity.py -q
"""
import unittest


class SupervisorSuccessCriterionTests(unittest.TestCase):
    """SUPERVISOR_SYSTEM_PROMPT must contain the Repo2Run goalpost."""

    def setUp(self):
        from src.envstate.supervisor import SUPERVISOR_SYSTEM_PROMPT
        self.prompt = SUPERVISOR_SYSTEM_PROMPT

    def test_contains_collect_only_command(self):
        self.assertIn("pytest --collect-only", self.prompt)

    def test_contains_poetry_collect_only_variant(self):
        self.assertIn("poetry run pytest --collect-only", self.prompt)

    def test_contains_disable_warnings_flag(self):
        self.assertIn("--disable-warnings", self.prompt)

    def test_contains_collection_success_criterion(self):
        # The prompt must communicate that collection success is the target
        p = self.prompt.lower()
        self.assertIn("collection", p)

    def test_verification_phase_must_assign_collection_task(self):
        # Supervisor must be told that Verification phase MUST run the collection command
        p = self.prompt.lower()
        self.assertIn("verification", p)

    # --- existing critical phrases preserved ---

    def test_taskspec_json_instruction_preserved(self):
        self.assertIn("TaskSpec", self.prompt)

    def test_json_fenced_block_instruction_preserved(self):
        # The supervisor must still emit exactly one TaskSpec JSON in a fenced block
        self.assertIn("```json", self.prompt)

    def test_forbidden_certify_preserved(self):
        self.assertIn("do not certify", self.prompt.lower())

    def test_task_id_key_preserved(self):
        self.assertIn("task_id", self.prompt)


class WorkerSuccessCriterionTests(unittest.TestCase):
    """WORKER_SYSTEM_PROMPT must contain the Repo2Run goalpost."""

    def setUp(self):
        from src.envstate.worker import WORKER_SYSTEM_PROMPT
        self.prompt = WORKER_SYSTEM_PROMPT

    def test_contains_collect_only_command(self):
        self.assertIn("pytest --collect-only", self.prompt)

    def test_contains_poetry_collect_only_variant(self):
        self.assertIn("poetry run pytest --collect-only", self.prompt)

    def test_contains_disable_warnings_flag(self):
        self.assertIn("--disable-warnings", self.prompt)

    def test_contains_collection_success_concept(self):
        p = self.prompt.lower()
        self.assertIn("collection", p)

    # --- existing critical phrases preserved ---

    def test_thought_action_format_preserved(self):
        self.assertIn("Thought:", self.prompt)
        self.assertIn("Action:", self.prompt)

    def test_final_answer_format_preserved(self):
        self.assertIn("Final Answer: Success", self.prompt)

    def test_one_bounded_task_framing_preserved(self):
        p = self.prompt.lower()
        self.assertIn("one bounded", p)

    def test_no_certify_preserved(self):
        # Worker does not certify environment facts
        self.assertIn("not certify", self.prompt.lower())


class FullstateWorkerSuccessCriterionTests(unittest.TestCase):
    """FULLSTATE_WORKER_SYSTEM_PROMPT must contain the Repo2Run goalpost."""

    def setUp(self):
        from src.envstate.fullstate_worker import FULLSTATE_WORKER_SYSTEM_PROMPT
        self.prompt = FULLSTATE_WORKER_SYSTEM_PROMPT

    def test_contains_collect_only_command(self):
        self.assertIn("pytest --collect-only", self.prompt)

    def test_contains_poetry_collect_only_variant(self):
        self.assertIn("poetry run pytest --collect-only", self.prompt)

    def test_contains_disable_warnings_flag(self):
        self.assertIn("--disable-warnings", self.prompt)

    def test_contains_collection_success_concept(self):
        # The goalpost phrase "collect-only" is the collection indicator
        self.assertIn("collect-only", self.prompt)

    def test_run_it_before_final_answer(self):
        # Must tell the worker to run the collection command before responding Final Answer
        p = self.prompt.lower()
        # "run" and "final answer" or "before" and "final answer" in proximity
        self.assertIn("final answer", p)
        # The instruction to verify before responding must be present
        self.assertIn("verify", p)

    # --- existing critical phrases preserved ---

    def test_layered_rca_tests_layer_preserved(self):
        p = self.prompt.lower()
        self.assertIn("tests", p)

    def test_no_xml_instruction_preserved(self):
        # Must still have the "do NOT use tool-call, function-call, or XML" instruction
        p = self.prompt.lower()
        self.assertIn("xml", p)
        # "Action:" appears in the response-format section
        self.assertIn("Action:", self.prompt)

    def test_thought_action_format_preserved(self):
        self.assertIn("Thought:", self.prompt)
        self.assertIn("Action:", self.prompt)

    def test_final_answer_success_preserved(self):
        self.assertIn("Final Answer: Success", self.prompt)

    def test_certified_probe_trust_rule_preserved(self):
        self.assertIn("CERTIFIED-PROBE", self.prompt)

    def test_paper_over_warning_preserved(self):
        p = self.prompt.lower()
        self.assertIn("paper over", p)


if __name__ == "__main__":
    unittest.main()
