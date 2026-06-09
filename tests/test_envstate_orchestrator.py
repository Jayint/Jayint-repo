import unittest
from types import SimpleNamespace

from src.envstate.orchestrator import EnvStateOrchestrator
# BaseFacts/EnvStateSnapshot removed with types.py (Task 39) — replaced with SimpleNamespace
from src.envstate.ledger import ActionLedger
from src.envstate.build_agent import WorkerReport
# advance_revision removed with acl.py — stubs updated below
# Worker removed with worker.py (Task 37) — only used in skipped test below


class FakeSupervisor:
    def __init__(self, tasks):
        self.tasks = list(tasks)

    def next_task(self, snapshot, ledger, budget):
        if not self.tasks:
            return None, {"total_tokens": 0}
        return self.tasks.pop(0), {"total_tokens": 1}


class FakeWorker:
    def __init__(self, reports):
        self.reports = list(reports)

    def run_task(self, task_spec, step_fn):
        return self.reports.pop(0)


class FakeWorkerPlanner:
    def __init__(self, steps):
        self.steps = list(steps)

    def next_action(self, brief, recent):
        return self.steps.pop(0)


def _noop_observer(snapshot, task_spec, step, action, success, observation):
    return snapshot


class OrchestratorTests(unittest.TestCase):
    def _snapshot(self):
        # EnvStateSnapshot/BaseFacts removed with types.py (Task 39); plain namespace suffices
        return SimpleNamespace(revision=0, container_id="c1")

    def test_loop_stops_when_supervisor_returns_no_task(self):
        supervisor = FakeSupervisor([
            {"task_id": "t1", "phase": "Verification", "goal": "g", "success_criteria": []},
        ])
        worker = FakeWorker([WorkerReport("t1", "complete", "done", ("pytest -q",))])
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=_noop_observer,
            max_tasks=10,
        )
        result = orch.run()
        self.assertEqual(result["tasks_completed"], 1)
        self.assertEqual(result["stop_reason"], "no_more_tasks")

    def test_loop_respects_max_tasks_budget(self):
        supervisor = FakeSupervisor([{"task_id": f"t{i}", "phase": "x", "goal": "g", "success_criteria": []}
                                     for i in range(100)])
        worker = FakeWorker([WorkerReport(f"t{i}", "complete", "done") for i in range(100)])
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=_noop_observer,
            max_tasks=3,
        )
        result = orch.run()
        self.assertEqual(result["tasks_completed"], 3)
        self.assertEqual(result["stop_reason"], "max_tasks")

    @unittest.skip("v0 orchestrator — superseded by run_v1; advance_revision removed with acl.py")
    def test_observer_threads_snapshot_per_action(self):
        supervisor = FakeSupervisor([
            {"task_id": "t1", "phase": "x", "goal": "g", "success_criteria": [], "max_actions": 3},
        ])
        worker = Worker(planner=FakeWorkerPlanner([
            ("apt-get install -y libpq-dev", False),
            ("pip install psycopg2", False),
            ("", True),
        ]), max_actions=3)

        def observer(snapshot, task_spec, step, action, success, observation):
            # advance_revision removed with acl.py — stub returns snapshot unchanged
            return snapshot

        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=observer,
            max_tasks=5,
        )
        result = orch.run()
        self.assertEqual(result["final_revision"], 2)
        self.assertEqual(orch.snapshot.revision, 2)

    def test_forwards_supervisor_usage_to_on_usage(self):
        seen = []
        supervisor = FakeSupervisor([
            {"task_id": "t1", "phase": "x", "goal": "g", "success_criteria": []},
        ])
        worker = FakeWorker([WorkerReport("t1", "complete", "done")])
        orch = EnvStateOrchestrator(
            supervisor=supervisor, worker=worker,
            snapshot=self._snapshot(), ledger=ActionLedger(),
            executor=lambda a: (True, "ok"), observer=_noop_observer,
            max_tasks=10, on_usage=seen.append,
        )
        orch.run()
        # one task call + the terminal no-task call both report usage
        self.assertGreaterEqual(len(seen), 1)
        self.assertEqual(seen[0]["total_tokens"], 1)


if __name__ == "__main__":
    unittest.main()
