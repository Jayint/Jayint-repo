"""Tests for §3.7 mandatory token-bucket split.

TDD — failing before the split; green after.

Tests:
1. RunTokenLedger has 'supervisor' and 'worker' buckets (§3.7).
2. Legacy 'planner' bucket still present (back-compat, Arm 0).
3. add('supervisor', ...) routes correctly and updates total.
4. add('worker', ...) routes correctly and updates total.
5. add('planner', ...) still works (back-compat).
6. _build_run_summary emits supervisor/worker keys in token_usage.
7. _record_supervisor_path_usage('supervisor', ...) / ('worker', ...) land in
   the right buckets.
8. _run_supervisor routes supervisor on_usage -> 'supervisor' and
   worker on_usage -> 'worker' (source-code check).

Conventions:
- unittest.TestCase, no pytest fixtures/markers
- SimpleNamespace fakes
- __new__-bypass where needed
"""
import unittest
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ledger():
    from src.observation_compressor import RunTokenLedger
    return RunTokenLedger()


def _fake_usage(inp=10, out=5):
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}


# ---------------------------------------------------------------------------
# 1–5. RunTokenLedger bucket tests
# ---------------------------------------------------------------------------

class TestRunTokenLedgerBuckets(unittest.TestCase):
    """RunTokenLedger must expose 'supervisor' and 'worker' buckets."""

    def test_supervisor_bucket_exists(self):
        ledger = _make_ledger()
        self.assertTrue(
            hasattr(ledger, "supervisor"),
            "RunTokenLedger must have a 'supervisor' bucket",
        )

    def test_worker_bucket_exists(self):
        ledger = _make_ledger()
        self.assertTrue(
            hasattr(ledger, "worker"),
            "RunTokenLedger must have a 'worker' bucket",
        )

    def test_planner_bucket_still_exists(self):
        ledger = _make_ledger()
        self.assertTrue(
            hasattr(ledger, "planner"),
            "Legacy 'planner' bucket must still exist for Arm-0 back-compat",
        )

    def test_supervisor_routing(self):
        ledger = _make_ledger()
        ledger.add("supervisor", input_tokens=10, output_tokens=5)
        self.assertEqual(ledger.supervisor.input_tokens, 10)
        self.assertEqual(ledger.supervisor.output_tokens, 5)
        self.assertEqual(ledger.supervisor.total_tokens, 15)

    def test_worker_routing(self):
        ledger = _make_ledger()
        ledger.add("worker", input_tokens=20, output_tokens=7)
        self.assertEqual(ledger.worker.input_tokens, 20)
        self.assertEqual(ledger.worker.output_tokens, 7)
        self.assertEqual(ledger.worker.total_tokens, 27)

    def test_supervisor_updates_total(self):
        ledger = _make_ledger()
        ledger.add("supervisor", input_tokens=10, output_tokens=5)
        self.assertEqual(ledger.total.total_tokens, 15)

    def test_worker_updates_total(self):
        ledger = _make_ledger()
        ledger.add("worker", input_tokens=20, output_tokens=7)
        self.assertEqual(ledger.total.total_tokens, 27)

    def test_planner_still_routes_correctly(self):
        ledger = _make_ledger()
        ledger.add("planner", input_tokens=3, output_tokens=2)
        self.assertEqual(ledger.planner.input_tokens, 3)
        self.assertEqual(ledger.planner.output_tokens, 2)
        self.assertEqual(ledger.planner.total_tokens, 5)

    def test_supervisor_and_worker_independent(self):
        ledger = _make_ledger()
        ledger.add("supervisor", input_tokens=10, output_tokens=0)
        ledger.add("worker", input_tokens=5, output_tokens=0)
        self.assertEqual(ledger.supervisor.input_tokens, 10)
        self.assertEqual(ledger.worker.input_tokens, 5)
        self.assertEqual(ledger.total.input_tokens, 15)


# ---------------------------------------------------------------------------
# 6. _build_run_summary emits supervisor/worker in token_usage
# ---------------------------------------------------------------------------

class TestBuildRunSummaryBuckets(unittest.TestCase):
    """_build_run_summary must emit 'supervisor' and 'worker' keys in token_usage."""

    def _make_agent(self):
        from agent import DockerAgent
        from src.observation_compressor import RunTokenLedger
        agent = DockerAgent.__new__(DockerAgent)
        agent.repo_url = "https://github.com/example/repo"
        agent.synthesizer = SimpleNamespace(base_image=None, workdir="/app")
        agent.platform_override = None
        agent.verified_test_command = None
        agent.verified_test_commands = []
        agent.verified_runtime_preparation_commands = []
        agent.successful_test_commands = []
        agent.test_run_attempts = []
        agent.successful_actions = []
        agent.failed_actions = []
        agent.verification_source = None
        agent.verification_bundle = None
        agent.benchmark_evaluation_target = {}
        agent.build_recipe = None
        agent.build_recipe_source = None
        agent.build_recipe_error = None
        agent.required_local_services = set()
        agent.enable_observation_compression = False
        agent.enable_long_term_memory = False
        agent.enable_envstate = False
        agent.enable_supervisor = False
        agent.enable_fullstate_worker = False
        agent.compression_stats = {"candidate_steps": 0, "compressed_steps": 0, "saved_tokens_est": 0}
        agent.memory_manager = None
        agent.memory_path = None
        agent.memory_embedding_model = "test"
        agent.command_timeout_seconds = 1800
        agent.memory_stats = {"enabled": False, "retrievals": 0, "retrieval_hits": 0,
                              "generation_attempted": False, "candidate_memories": 0,
                              "written_memories": 0, "skipped_duplicates": 0,
                              "skipped_invalid": 0, "relation_judged_pairs": 0,
                              "relation_links_accepted": 0, "relation_links_rejected": 0,
                              "relation_duplicates_rejected": 0, "errors": []}
        agent.agent_steps = []
        agent.run_token_ledger = RunTokenLedger()
        agent.action_ledger = None
        return agent

    def test_summary_has_supervisor_key(self):
        agent = self._make_agent()
        summary = agent._build_run_summary(True)
        token_usage = summary["token_usage"]
        self.assertIn(
            "supervisor", token_usage,
            "_build_run_summary token_usage must include 'supervisor' key (§3.7)",
        )

    def test_summary_has_worker_key(self):
        agent = self._make_agent()
        summary = agent._build_run_summary(True)
        token_usage = summary["token_usage"]
        self.assertIn(
            "worker", token_usage,
            "_build_run_summary token_usage must include 'worker' key (§3.7)",
        )

    def test_summary_still_has_planner_key(self):
        agent = self._make_agent()
        summary = agent._build_run_summary(True)
        token_usage = summary["token_usage"]
        self.assertIn(
            "planner", token_usage,
            "_build_run_summary token_usage must still include legacy 'planner' key",
        )

    def test_supervisor_tokens_reflected_in_summary(self):
        agent = self._make_agent()
        agent.run_token_ledger.add("supervisor", input_tokens=42, output_tokens=8)
        summary = agent._build_run_summary(True)
        sup = summary["token_usage"]["supervisor"]
        self.assertEqual(sup["input_tokens"], 42)
        self.assertEqual(sup["output_tokens"], 8)
        self.assertEqual(sup["total_tokens"], 50)

    def test_worker_tokens_reflected_in_summary(self):
        agent = self._make_agent()
        agent.run_token_ledger.add("worker", input_tokens=100, output_tokens=20)
        summary = agent._build_run_summary(True)
        wrk = summary["token_usage"]["worker"]
        self.assertEqual(wrk["input_tokens"], 100)
        self.assertEqual(wrk["output_tokens"], 20)
        self.assertEqual(wrk["total_tokens"], 120)


# ---------------------------------------------------------------------------
# 7. _record_supervisor_path_usage routing
# ---------------------------------------------------------------------------

class TestRecordSupervisorPathUsage(unittest.TestCase):
    """_record_supervisor_path_usage('supervisor', ...) / ('worker', ...) land in
    the right buckets on the ledger."""

    def _make_agent(self):
        from agent import DockerAgent
        from src.observation_compressor import RunTokenLedger
        agent = DockerAgent.__new__(DockerAgent)
        agent.run_token_ledger = RunTokenLedger()
        return agent

    def test_supervisor_bucket_receives_usage(self):
        agent = self._make_agent()
        agent._record_supervisor_path_usage("supervisor", _fake_usage(10, 5))
        self.assertEqual(agent.run_token_ledger.supervisor.input_tokens, 10)

    def test_worker_bucket_receives_usage(self):
        agent = self._make_agent()
        agent._record_supervisor_path_usage("worker", _fake_usage(20, 7))
        self.assertEqual(agent.run_token_ledger.worker.input_tokens, 20)

    def test_none_usage_is_noop(self):
        agent = self._make_agent()
        agent._record_supervisor_path_usage("supervisor", None)
        self.assertEqual(agent.run_token_ledger.supervisor.input_tokens, 0)


# ---------------------------------------------------------------------------
# 8. Source-code check: _run_supervisor routes on_usage correctly
# ---------------------------------------------------------------------------

@unittest.skip("supervisor removed — _run_supervisor deleted with supervisor.py (Task 36)")
class TestRunSupervisorUsageRouting(unittest.TestCase):
    """_run_supervisor must route supervisor on_usage -> 'supervisor',
    worker on_usage -> 'worker' (§3.7/§3.8)."""

    def test_run_supervisor_routes_supervisor_usage(self):
        import agent as agent_module
        import inspect
        source = inspect.getsource(agent_module._run_supervisor if
                                   hasattr(agent_module, "_run_supervisor")
                                   else agent_module.DockerAgent._run_supervisor)
        self.assertIn(
            '"supervisor"',
            source,
            "_run_supervisor must route supervisor on_usage to 'supervisor' bucket",
        )

    def test_run_supervisor_routes_worker_usage(self):
        import agent as agent_module
        import inspect
        source = inspect.getsource(agent_module.DockerAgent._run_supervisor)
        self.assertIn(
            '"worker"',
            source,
            "_run_supervisor must route worker on_usage to 'worker' bucket",
        )

    def test_run_supervisor_arm_c_selector_present(self):
        """_run_supervisor must contain the fullstate_worker_prompt selector (§3.8)."""
        import agent as agent_module
        import inspect
        source = inspect.getsource(agent_module.DockerAgent._run_supervisor)
        self.assertIn(
            "fullstate_worker_prompt",
            source,
            "_run_supervisor must have the Arm-C worker-planner selector",
        )


# ---------------------------------------------------------------------------
# 9. Runtime: Arm C env_snapshot threading (C1 fix — §2.3 / C1 review finding)
# ---------------------------------------------------------------------------

@unittest.skip("supervisor removed — _run_supervisor deleted with supervisor.py (Task 36)")
class TestArmCSnapshotThreading(unittest.TestCase):
    """RUNTIME test: _run_supervisor with fullstate_worker_prompt=True must seed
    self.env_snapshot BEFORE constructing FullStateWorkerPlanner, and must thread
    the live snapshot onto self.env_snapshot as the orchestrator advances it so
    that get_snapshot() always returns the CURRENT revision, not the stale rev-0.

    Strategy: construct a DockerAgent via __new__-bypass, patch the minimal
    collaborators, and drive _run_supervisor directly.  We intercept the
    observer closure and simulate snapshot advancement so we can assert that
    self.env_snapshot tracks the advancing revision at each step.
    """

    def _make_minimal_agent(self):
        """Build a DockerAgent stub with only the attrs _run_supervisor needs."""
        from agent import DockerAgent
        from types import SimpleNamespace
        agent = DockerAgent.__new__(DockerAgent)
        agent.enable_supervisor = True
        agent.enable_fullstate_worker = False
        agent.fullstate_worker_prompt = True
        agent.enable_cleanroom = False
        agent.enable_envstate = True
        agent.model = "test-model"
        agent.logs_dir = "/tmp"
        agent.env_container_id = "ctr-test"
        # synthesizer stub
        agent.synthesizer = SimpleNamespace(
            base_image="python:3.11-slim",
            workdir="/repo",
            command_mutates_environment=lambda a: False,
            classify_mutation=lambda a: None,
        )
        # sandbox stub — close() needed by _run_supervisor's finally block
        agent.sandbox = SimpleNamespace(
            execute=lambda action: (True, "ok"),
            exec_readonly=lambda action: (True, ""),
            close=lambda keep_alive=False: None,
        )
        # client stub — returns a valid Thought/Action response
        agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **_k: SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="Thought: ok\nAction: ls /repo"))],
                usage=SimpleNamespace(
                    prompt_tokens=5, completion_tokens=3, total_tokens=8),
            ))))
        # ledger
        from src.envstate.ledger import ActionLedger
        agent.action_ledger = ActionLedger()
        # Stub the callbacks _run_supervisor calls after orchestrator.run()
        agent._auto_finalize_from_verified_tests = lambda *a, **k: False
        agent.verification_bundle = None
        agent._is_transient_llm_error = lambda e: False
        agent._record_successful_action = lambda *a, **k: None
        agent._record_failed_action = lambda *a, **k: None
        agent._record_supervisor_path_usage = lambda *a, **k: None
        # Stub _build_observer with a minimal observer; advance_revision removed
        # with acl.py — stub returns snapshot unchanged.
        def _simple_observer(snap, task_spec, step, action, success, observation):
            # advance_revision removed with acl.py; stub returns snapshot unchanged
            return snap
        agent._build_observer = lambda maintainer: _simple_observer
        # Stub the finally-block helpers so _run_supervisor can complete
        # without needing the full DockerAgent attribute set.
        agent._write_run_summary = lambda *a, **k: None
        return agent

    def test_env_snapshot_seeded_before_planner_construction(self):
        """self.env_snapshot must be set (revision 0) BEFORE _run_supervisor
        instantiates FullStateWorkerPlanner so get_snapshot() never raises
        AttributeError on the first next_action call (C1 fix)."""
        from types import SimpleNamespace
        agent = self._make_minimal_agent()

        snapshot_revisions_seen = []

        # Intercept FullStateWorkerPlanner construction to record get_snapshot result
        import src.envstate.fullstate_worker as fw_module
        _orig_planner_cls = fw_module.FullStateWorkerPlanner

        class _SpyPlanner(_orig_planner_cls):
            def __init__(self_inner, client, model, get_snapshot, on_usage=None):
                # Record what get_snapshot() returns at construction time
                snap = get_snapshot()
                snapshot_revisions_seen.append(("at_construction", snap.revision))
                super().__init__(client, model, get_snapshot=get_snapshot, on_usage=on_usage)

            def next_action(self_inner, brief, recent_obs):
                # Record current snapshot revision during each call
                snap = self_inner.get_snapshot()
                snapshot_revisions_seen.append(("during_next_action", snap.revision))
                # Return Final Answer to terminate the loop quickly
                return ("", True)

        fw_module.FullStateWorkerPlanner = _SpyPlanner

        # Patch Supervisor and EnvStateOrchestrator minimally so _run_supervisor
        # completes without Docker: make the supervisor emit one task, then done.
        # supervisor.py deleted in Task 36; import removed from this skipped class
        import src.envstate.orchestrator as orch_module
        from src.envstate.types import EnvStateSnapshot, BaseFacts
        from src.envstate.ledger import ActionLedger

        _orig_supervisor = None  # supervisor.py removed in Task 36
        _orig_orchestrator = orch_module.EnvStateOrchestrator

        call_count = [0]
        advancing_snapshot = [None]  # mutated inside FakeOrchestrator.run

        class FakeSupervisor:
            def __init__(self_inner, client, model):
                pass
            def next_task(self_inner, snapshot, ledger, budget):
                call_count[0] += 1
                if call_count[0] == 1:
                    return ({"task_id": "t1", "phase": "x", "goal": "g",
                             "success_criteria": []}, None)
                return (None, None)

        class FakeOrchestrator:
            def __init__(self_inner, supervisor, worker, snapshot, ledger,
                         executor, observer, max_tasks, on_usage=None,
                         global_action_budget=None):
                self_inner._observer = observer
                self_inner._snapshot = snapshot
                self_inner._worker = worker
                advancing_snapshot[0] = snapshot

            def run(self_inner):
                # Simulate 2 actions via the threaded observer so agent.env_snapshot
                # gets updated.  We only need to verify construction-time seeding here.
                task_spec = {"task_id": "t1", "phase": "x", "goal": "g",
                             "success_criteria": []}
                new_snap1 = self_inner._observer(
                    self_inner._snapshot, task_spec, 1, "apt-get install -y lib", True, "ok"
                )
                advancing_snapshot[0] = new_snap1
                new_snap2 = self_inner._observer(
                    new_snap1, task_spec, 2, "pip install req", True, "done"
                )
                advancing_snapshot[0] = new_snap2
                return {
                    "tasks_completed": 1,
                    "stop_reason": "no_more_tasks",
                    "final_revision": new_snap2.revision,
                }
            @property
            def snapshot(self_inner):
                return advancing_snapshot[0]

        # sup_module.Supervisor removed with supervisor.py (Task 36)
        orch_module.EnvStateOrchestrator = FakeOrchestrator

        try:
            # _run_supervisor should complete without AttributeError
            agent._run_supervisor(max_steps=2)
        except AttributeError as exc:
            self.fail(f"AttributeError raised — env_snapshot not seeded: {exc}")
        finally:
            fw_module.FullStateWorkerPlanner = _orig_planner_cls
            # sup_module.Supervisor restore removed — supervisor.py deleted in Task 36
            orch_module.EnvStateOrchestrator = _orig_orchestrator

        # Verify get_snapshot() was callable at construction time (not AttributeError)
        self.assertTrue(
            any(label == "at_construction" for label, _ in snapshot_revisions_seen),
            "FullStateWorkerPlanner was never constructed — test setup issue",
        )
        # Verify env_snapshot was set to rev-0 before planner was constructed
        construction_revs = [r for label, r in snapshot_revisions_seen
                             if label == "at_construction"]
        self.assertEqual(construction_revs[0], 0,
                         "env_snapshot at planner construction must be revision 0")

    def test_env_snapshot_updated_to_live_revision_during_run(self):
        """After the observer advances the snapshot (new revision), self.env_snapshot
        must reflect the new revision, not the stale rev-0 seed (C1 live-threading fix)."""
        agent = self._make_minimal_agent()
        revisions_after_each_step = []

        # supervisor.py deleted in Task 36; import removed from this skipped class
        import src.envstate.orchestrator as orch_module
        import src.envstate.fullstate_worker as fw_module

        _orig_supervisor = None  # supervisor.py removed
        _orig_orchestrator = orch_module.EnvStateOrchestrator
        _orig_planner_cls = fw_module.FullStateWorkerPlanner

        class _NopPlanner(_orig_planner_cls):
            def next_action(self_inner, brief, recent_obs):
                return ("", True)  # immediate Final Answer

        advancing_snapshot = [None]

        class FakeSupervisor:
            def __init__(self_inner, *a, **k): pass
            def next_task(self_inner, snapshot, ledger, budget):
                return (None, None)  # immediately no_more_tasks

        class FakeOrchestrator:
            def __init__(self_inner, supervisor, worker, snapshot, ledger,
                         executor, observer, max_tasks, on_usage=None,
                         global_action_budget=None):
                self_inner._observer = observer
                self_inner._initial_snapshot = snapshot
                advancing_snapshot[0] = snapshot

            def run(self_inner):
                task_spec = {"task_id": "t1", "phase": "x", "goal": "g",
                             "success_criteria": []}
                snap = self_inner._initial_snapshot
                # Simulate 3 mutating steps, each advancing the revision
                for i in range(1, 4):
                    snap = self_inner._observer(
                        snap, task_spec, i, f"apt-get install -y pkg{i}", True, "ok"
                    )
                    revisions_after_each_step.append(snap.revision)
                advancing_snapshot[0] = snap
                return {"tasks_completed": 1, "stop_reason": "no_more_tasks",
                        "final_revision": snap.revision}

            @property
            def snapshot(self_inner):
                return advancing_snapshot[0]

        # sup_module.Supervisor removed — supervisor.py deleted in Task 36
        orch_module.EnvStateOrchestrator = FakeOrchestrator
        fw_module.FullStateWorkerPlanner = _NopPlanner

        try:
            agent._run_supervisor(max_steps=2)
        finally:
            # sup_module.Supervisor restore removed — supervisor.py deleted in Task 36
            orch_module.EnvStateOrchestrator = _orig_orchestrator
            fw_module.FullStateWorkerPlanner = _orig_planner_cls

        # The observer advances the snapshot revision on each mutating step.
        # self.env_snapshot must track the advancing revision after each step.
        # We verify: the final agent.env_snapshot revision matches the last
        # revision returned by the observer (i.e., threading worked).
        if revisions_after_each_step:
            final_rev = revisions_after_each_step[-1]
            self.assertEqual(
                agent.env_snapshot.revision, final_rev,
                "self.env_snapshot must track the live orchestrator snapshot revision "
                f"(expected {final_rev}, got {agent.env_snapshot.revision}). "
                "The _threading_observer wrapper is missing or broken.",
            )


if __name__ == "__main__":
    unittest.main()
