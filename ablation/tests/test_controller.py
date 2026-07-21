from __future__ import annotations

from collections import deque

from ablation.controller import ExecuteOnlyController, _test_failure_route
from ablation.execute_agent import AgentExhausted
from ablation.models import (
    AbstainAction,
    CheckResult,
    EvidenceBundle,
    EvidenceItem,
    FlatBlock,
    FlatPatch,
    FlatPlan,
    InitialPlanResult,
    PatchAction,
    RepairResult,
    SetupResult,
    TestResult as RuntimeTestResult,
)


def evidence() -> EvidenceBundle:
    return EvidenceBundle(
        (
            EvidenceItem("host.base_image", "host", "python:3.11-slim"),
            EvidenceItem("host.test_commands", "host", "python -m pytest -q"),
            EvidenceItem("file:pyproject.toml", "file", "[project]"),
        )
    )


def plan(command: str) -> FlatPlan:
    return FlatPlan(
        (
            FlatBlock(
                "b01",
                (command,),
                (),
                ("file:pyproject.toml",),
            ),
        )
    )


class FakeAgent:
    def __init__(self, initial: FlatPlan, repairs):
        self.initial = initial
        self.repairs = deque(repairs)
        self.repair_calls = 0

    def generate_initial(self, evidence, _exec_readonly, **_kwargs):
        return InitialPlanResult(self.initial, evidence, 1, {"total_tokens": 10})

    def repair(self, _packet, evidence, _exec_readonly, **_kwargs):
        self.repair_calls += 1
        action = self.repairs.popleft()
        return RepairResult(action, evidence, 1, {"total_tokens": 10})


class FakeHost:
    def __init__(self, tests):
        self.tests = deque(tests)
        self.events: list[str] = []

    def exec_readonly(self, _command):
        return 0, "ok"

    def reset_to_base(self):
        self.events.append("reset")

    def run_setup(self, rendered):
        if "bad-command" in rendered.text:
            self.events.append("setup_bad")
            line = next(
                item.line for item in rendered.commands if item.command == "bad-command"
            )
            return SetupResult(
                1,
                "__ABLATION_BLOCK__:b01\nboom",
                "bad-command",
                line,
            )
        self.events.append("setup_fixed")
        return SetupResult(0, "ok")

    def run_checks(self, _plan):
        self.events.append("checks")
        return CheckResult(True)

    def run_tests(self, _commands):
        index = sum(event.startswith("test_") for event in self.events) + 1
        self.events.append(f"test_{index}")
        return self.tests.popleft()


def passed_test() -> RuntimeTestResult:
    return RuntimeTestResult(True, "python -m pytest -q", 0, "2 passed")


def controller(agent, host, **kwargs):
    return ExecuteOnlyController(
        agent=agent,
        host=host,
        evidence=evidence(),
        base_image="python:3.11-slim",
        languages=("python",),
        test_commands=("python -m pytest -q",),
        max_cycles=kwargs.get("max_cycles", 5),
        max_agent_calls=kwargs.get("max_agent_calls", 10),
        max_turns_per_decision=2,
        completion_policy=kwargs.get("completion_policy", "all_tests_pass"),
    )


def test_controller_repairs_one_block_and_requires_terminal_fresh_replay():
    replacement = FlatBlock(
        "b01",
        ("fixed-command",),
        (),
        ("file:pyproject.toml",),
    )
    action = PatchAction(
        "replace failing command",
        FlatPatch("replace_block", "b01", replacement),
    )
    agent = FakeAgent(plan("bad-command"), [action])
    host = FakeHost([passed_test(), passed_test()])
    result = controller(agent, host).run()

    assert result.status == "success"
    assert result.stop_reason == "terminal_fresh_replay_passed"
    assert result.plan.blocks[0].commands == ("fixed-command",)
    assert host.events == [
        "reset",
        "setup_bad",
        "reset",
        "setup_fixed",
        "checks",
        "test_1",
        "reset",
        "setup_fixed",
        "checks",
        "test_2",
    ]


def test_terminal_failure_cannot_be_reported_as_success():
    agent = FakeAgent(
        plan("fixed-command"),
        [
            AbstainAction(
                "non_environment",
                "cannot repair",
                ("runtime:terminal:1:terminal_test",),
            )
        ],
    )
    host = FakeHost(
        [
            passed_test(),
            RuntimeTestResult(
                False,
                "python -m pytest -q",
                1,
                "ModuleNotFoundError: No module named 'x'",
            ),
        ]
    )
    result = controller(agent, host).run()
    assert result.status == "failed"
    assert result.stop_reason == "execute_agent_abstained_non_environment"
    assert host.events.count("reset") == 2


def test_non_environment_assertion_failure_does_not_call_repair_agent():
    agent = FakeAgent(plan("fixed-command"), [])
    host = FakeHost(
        [
            RuntimeTestResult(
                False,
                "python -m pytest -q",
                1,
                "FAILED tests/test_logic.py::test_value\nAssertionError",
            )
        ]
    )
    result = controller(agent, host).run()
    assert result.stop_reason == "host_classified_non_environment_test_failure"
    assert agent.repair_calls == 0


def test_environment_ready_policy_terminal_replays_concrete_test_failure():
    agent = FakeAgent(plan("fixed-command"), [])
    failure = RuntimeTestResult(
        False,
        "python -m pytest -q",
        1,
        "FAILED tests/test_logic.py::test_value\nAssertionError",
    )
    host = FakeHost([failure, failure])

    result = controller(
        agent,
        host,
        completion_policy="environment_ready",
    ).run()

    assert result.status == "success"
    assert result.stop_reason == "terminal_fresh_replay_environment_ready"
    assert result.test_result is not None
    assert not result.test_result.passed
    assert host.events.count("reset") == 2
    assert agent.repair_calls == 0


def test_assertion_and_zero_test_signals_are_not_overridden_by_error_words():
    assert _test_failure_route(
        "FAILED tests/test_logic.py::test_message\n"
        "AssertionError: expected 'ModuleNotFoundError'"
    ) == "non_environment"
    assert _test_failure_route("no tests ran in 0.01s") == "non_environment"
    assert _test_failure_route(
        "ERROR collecting tests/test_app.py\n"
        "ModuleNotFoundError: No module named 'missing'"
    ) == "environment"
    assert _test_failure_route(
        "ERROR collecting lib/pkg/test/version.py\n"
        "import file mismatch: imported module came from site-packages\n"
        "Interrupted: 49 errors during collection"
    ) == "environment"


def test_environment_ready_repairs_collection_failure_before_exporting_plan():
    replacement = FlatBlock(
        "b01",
        ("repaired-command",),
        (),
        ("file:pyproject.toml",),
    )
    action = PatchAction(
        "repair collection environment",
        FlatPatch("replace_block", "b01", replacement),
    )
    collection_failure = RuntimeTestResult(
        False,
        "python -m pytest -q",
        1,
        "ERROR collecting lib/pkg/test/version.py\n"
        "import file mismatch\n"
        "Interrupted: 49 errors during collection",
    )
    agent = FakeAgent(plan("fixed-command"), [action])
    host = FakeHost([collection_failure, passed_test(), passed_test()])

    result = controller(
        agent,
        host,
        completion_policy="environment_ready",
    ).run()

    assert result.status == "success"
    assert result.stop_reason == "terminal_fresh_replay_passed"
    assert result.plan.blocks[0].commands == ("repaired-command",)
    assert agent.repair_calls == 1
    assert host.events.count("reset") == 3


def test_rejected_patch_does_not_execute_an_unchanged_candidate():
    invalid = PatchAction(
        "append after failure",
        FlatPatch(
            "insert_after",
            "b01",
            FlatBlock(
                "b02",
                ("fixed-command",),
                (),
                ("file:pyproject.toml",),
            ),
        ),
    )
    abstain = AbstainAction(
        "non_environment",
        "no valid patch",
        ("runtime:search:1:setup",),
    )
    agent = FakeAgent(plan("bad-command"), [invalid, abstain])
    host = FakeHost([])
    result = controller(agent, host).run()
    assert result.status == "failed"
    assert host.events == ["reset", "setup_bad"]
    assert agent.repair_calls == 2


def test_max_cycles_stops_before_running_another_candidate():
    original = plan("bad-command")
    replacement = FlatBlock(
        "b01",
        ("fixed-command",),
        (),
        ("file:pyproject.toml",),
    )
    action = PatchAction(
        "replace",
        FlatPatch("replace_block", "b01", replacement),
    )
    agent = FakeAgent(original, [action])
    host = FakeHost([])
    result = controller(agent, host, max_cycles=1).run()

    assert result.stop_reason == "max_cycles"
    assert host.events == ["reset", "setup_bad"]
    assert agent.repair_calls == 0
    assert result.plan == original
    assert "bad-command" in result.setup_sh
    assert "fixed-command" not in result.setup_sh
    assert result.final_failure is not None
    assert result.final_failure.plan == original


class SequencedHost:
    def __init__(self, setup_outcomes, tests):
        self.setup_outcomes = deque(setup_outcomes)
        self.tests = deque(tests)
        self.events: list[str] = []
        self.setup_calls = 0
        self.test_calls = 0

    def exec_readonly(self, _command):
        return 0, "ok"

    def reset_to_base(self):
        self.events.append("reset")

    def run_setup(self, rendered):
        self.setup_calls += 1
        self.events.append(f"setup_{self.setup_calls}")
        outcome = self.setup_outcomes.popleft()
        if outcome == "ok":
            return SetupResult(0, "ok")
        assert outcome == "fail"
        command = rendered.commands[0]
        return SetupResult(
            1,
            f"__ABLATION_BLOCK__:{command.block_id}\nterminal setup failed",
            command.command,
            command.line,
        )

    def run_checks(self, _plan):
        self.events.append("checks")
        return CheckResult(True)

    def run_tests(self, _commands):
        self.test_calls += 1
        self.events.append(f"test_{self.test_calls}")
        return self.tests.popleft()


def test_terminal_setup_failure_is_repaired_then_search_and_terminal_replay_again():
    replacement = FlatBlock(
        "b01",
        ("fixed-command",),
        (),
        ("file:pyproject.toml",),
    )
    agent = FakeAgent(
        plan("old-command"),
        [
            PatchAction(
                "repair terminal setup failure",
                FlatPatch("replace_block", "b01", replacement),
            )
        ],
    )
    host = SequencedHost(
        ["ok", "fail", "ok", "ok"],
        [
            RuntimeTestResult(
                True, "python -m pytest -q", 0, "search pass"
            ),
            RuntimeTestResult(
                True, "python -m pytest -q", 0, "repaired search pass"
            ),
            RuntimeTestResult(
                True, "python -m pytest -q", 0, "terminal certificate"
            ),
        ],
    )

    result = controller(agent, host, max_cycles=2).run()

    assert result.status == "success"
    assert result.cycles == 2
    assert result.test_result is not None
    assert result.test_result.output == "terminal certificate"
    assert result.plan.blocks[0] == replacement
    assert agent.repair_calls == 1
    assert host.events == [
        "reset",
        "setup_1",
        "checks",
        "test_1",
        "reset",
        "setup_2",
        "reset",
        "setup_3",
        "checks",
        "test_2",
        "reset",
        "setup_4",
        "checks",
        "test_3",
    ]


class InitialExhaustedAgent:
    def generate_initial(self, *_args, **_kwargs):
        raise AgentExhausted(
            "initial exhausted",
            llm_calls=2,
            usage={
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
            },
        )


def test_initial_agent_exhaustion_is_included_in_call_and_token_accounting():
    result = controller(
        InitialExhaustedAgent(),
        FakeHost([]),
        max_agent_calls=2,
    ).run()

    assert result.stop_reason == "initial_agent_exhausted: initial exhausted"
    assert result.llm_calls == 2
    assert result.usage == {
        "input_tokens": 20,
        "output_tokens": 10,
        "total_tokens": 30,
    }


class RepairExhaustedAgent(FakeAgent):
    def repair(self, *_args, **_kwargs):
        self.repair_calls += 1
        raise AgentExhausted(
            "repair exhausted",
            llm_calls=2,
            usage={
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
            },
        )


def test_repair_agent_exhaustion_is_added_to_initial_call_accounting():
    agent = RepairExhaustedAgent(plan("bad-command"), [])
    result = controller(agent, FakeHost([]), max_agent_calls=3).run()

    assert result.stop_reason == "repair_agent_exhausted: repair exhausted"
    assert result.llm_calls == 3
    assert result.usage["total_tokens"] == 40
    assert agent.repair_calls == 1
