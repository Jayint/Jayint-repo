import unittest

from src.envstate.worker import Worker, WorkerReport, should_interrupt


class FakeWorkerPlanner:
    """Returns a queued list of (action, is_finished) per step."""
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def next_action(self, task_brief, recent_observations):
        self.calls.append((task_brief, list(recent_observations)))
        return self.steps.pop(0)


class FakeExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        return self.results.pop(0)  # (success: bool, observation: str)


class InterruptionPolicyTests(unittest.TestCase):
    def test_interrupts_on_repeated_failure_signature(self):
        task = {"stop_conditions": ["same error twice"]}
        observations = [
            (False, "Error: pg_config executable not found"),
            (False, "Error: pg_config executable not found"),
        ]
        self.assertTrue(should_interrupt(task, observations, action="pip install x", actions_used=2))

    def test_interrupts_when_action_budget_exhausted(self):
        task = {"max_actions": 4}
        self.assertTrue(should_interrupt(task, [], action="pip install x", actions_used=4))

    def test_interrupts_on_dependency_pin_change_attempt(self):
        task = {"constraints": ["Do not edit requirements.txt"]}
        self.assertTrue(should_interrupt(
            task, [], action="sed -i 's/2.8.6/2.9/' requirements.txt", actions_used=1))

    def test_no_interruption_for_normal_action(self):
        self.assertFalse(should_interrupt({"max_actions": 4}, [], action="apt-get install -y libpq-dev", actions_used=1))


class WorkerRunTests(unittest.TestCase):
    def test_completes_when_planner_signals_finished(self):
        planner = FakeWorkerPlanner([
            ("apt-get install -y libpq-dev", False),
            ("pip install psycopg2==2.8.6", False),
            ("", True),
        ])
        executor = FakeExecutor([(True, "installed libpq-dev"), (True, "Successfully installed psycopg2")])
        worker = Worker(planner=planner, max_actions=4)
        report = worker.run_task({"task_id": "task-004", "goal": "x", "max_actions": 4}, executor)
        self.assertEqual(report.status, "complete")
        # NOTE: commands_attempted is a TUPLE (frozen dataclass), so compare to a tuple.
        self.assertEqual(report.commands_attempted,
                         ("apt-get install -y libpq-dev", "pip install psycopg2==2.8.6"))

    def test_blocks_when_action_budget_exhausted(self):
        planner = FakeWorkerPlanner([("pip install x", False)] * 5)
        executor = FakeExecutor([(False, "boom")] * 5)
        worker = Worker(planner=planner, max_actions=2)
        report = worker.run_task({"task_id": "t", "goal": "x", "max_actions": 2}, executor)
        self.assertIn(report.status, ("blocked", "interrupted"))
        self.assertLessEqual(len(report.commands_attempted), 2)


from src.envstate.worker import LlmWorkerPlanner


def _fake_client(content):
    from types import SimpleNamespace
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_k: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        ))))


class LlmWorkerPlannerTests(unittest.TestCase):
    def test_returns_action_when_not_finished(self):
        planner = LlmWorkerPlanner(_fake_client("Thought: install\nAction: apt-get install -y libpq-dev"), "m")
        action, finished = planner.next_action("brief", [])
        self.assertEqual(action, "apt-get install -y libpq-dev")
        self.assertFalse(finished)

    def test_signals_finished_on_final_answer(self):
        planner = LlmWorkerPlanner(_fake_client("Thought: done\nFinal Answer: Success"), "m")
        action, finished = planner.next_action("brief", [])
        self.assertEqual(action, "")
        self.assertTrue(finished)


if __name__ == "__main__":
    unittest.main()
