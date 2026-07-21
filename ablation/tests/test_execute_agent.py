from __future__ import annotations

import json
from types import SimpleNamespace

from ablation.evidence import add_runtime_evidence
from ablation.execute_agent import AgentExhausted, ScriptExecuteAgent, _extract_agent_object
from ablation.models import EvidenceBundle, EvidenceItem, FailurePacket, FlatBlock, FlatPlan, PatchAction


class FakeResponse:
    def __init__(self, content: str):
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content, reasoning=None, model_extra={}),
                finish_reason="stop",
            )
        ]
        self.usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )


class FakeCompletions:
    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return FakeResponse(self.outputs.pop(0))


class FakeClient:
    def __init__(self, outputs: list[str]):
        self.chat = SimpleNamespace(completions=FakeCompletions(outputs))


def base_evidence() -> EvidenceBundle:
    return EvidenceBundle(
        (
            EvidenceItem("host.base_image", "host", "python:3.11-slim"),
            EvidenceItem("host.test_commands", "host", "python -m pytest -q"),
            EvidenceItem("file:pyproject.toml", "pyproject.toml", "[project]"),
        )
    )


def test_initial_agent_can_probe_then_emit_grounded_plan():
    outputs = [
        json.dumps(
            {
                "type": "probe",
                "purpose": "inspect metadata",
                "command": "cat pyproject.toml",
            }
        ),
        json.dumps(
            {
                "type": "initial_plan",
                "blocks": [
                    {
                        "block_id": "b01",
                        "commands": ["python -m pip install -e ."],
                        "checks": ["python -m pip check"],
                        "evidence_refs": ["runtime:initial:probe:1"],
                    }
                ],
            }
        ),
    ]
    client = FakeClient(outputs)
    probes: list[str] = []
    agent = ScriptExecuteAgent(client, "fake")
    result = agent.generate_initial(
        base_evidence(),
        lambda command: (probes.append(command) or 0, "[project]"),
        base_image="python:3.11-slim",
        languages=("python",),
        test_commands=("python -m pytest -q",),
        max_turns=2,
    )
    assert probes == ["cat pyproject.toml"]
    assert result.plan.blocks[0].block_id == "b01"
    assert "runtime:initial:probe:1" in result.evidence.ids
    assert result.llm_calls == 2
    assert result.usage["total_tokens"] == 30


def test_initial_agent_rejects_malformed_outer_json_instead_of_nested_block():
    malformed = (
        '{"type":"initial_plan","blocks":[{'
        '"block_id":"b01","commands":["python -m pip install -e ."],'
        '"checks":["python -c \\\\"import package\\\\""],'
        '"evidence_refs":["file:pyproject.toml"]}]}'
    )
    valid = json.dumps(
        {
            "type": "initial_plan",
            "blocks": [
                {
                    "block_id": "b01",
                    "commands": ["python -m pip install -e ."],
                    "checks": ["python -c 'import package'"],
                    "evidence_refs": ["file:pyproject.toml"],
                }
            ],
        }
    )
    parsed, errors = _extract_agent_object(malformed)
    assert parsed is None
    assert errors and errors[0].startswith("invalid top-level JSON object:")
    client = FakeClient([malformed, valid])
    result = ScriptExecuteAgent(client, "fake").generate_initial(
        base_evidence(),
        lambda _command: (0, ""),
        base_image="python:3.11-slim",
        languages=("python",),
        test_commands=("python -m pytest -q",),
        max_turns=2,
    )
    assert result.plan.blocks[0].block_id == "b01"
    assert result.llm_calls == 2


def test_repair_rejects_mutating_probe_without_execution_then_returns_patch():
    plan = FlatPlan(
        (
            FlatBlock(
                "b01",
                ("python -m pip install old",),
                (),
                ("file:pyproject.toml",),
            ),
        )
    )
    evidence = add_runtime_evidence(
        base_evidence(),
        evidence_id="runtime:search:1:setup",
        source="setup failure",
        content="old failed",
    )
    packet = FailurePacket(
        kind="setup",
        cycle=1,
        command="python -m pip install old",
        rc=1,
        output="failed",
        failed_block_id="b01",
        plan=plan,
        evidence_id="runtime:search:1:setup",
    )
    outputs = [
        json.dumps(
            {
                "type": "probe",
                "purpose": "mutate",
                "command": "apt-get install -y git",
            }
        ),
        json.dumps(
            {
                "type": "propose_patch",
                "rationale": "use declared project metadata",
                "patch": {
                    "op": "replace_block",
                    "target_block_id": "b01",
                    "block": {
                        "block_id": "b01",
                        "commands": ["python -m pip install -e ."],
                        "checks": ["python -m pip check"],
                        "evidence_refs": ["runtime:search:1:setup"],
                    },
                },
            }
        ),
    ]
    client = FakeClient(outputs)
    executed: list[str] = []
    result = ScriptExecuteAgent(client, "fake").repair(
        packet,
        evidence,
        lambda command: (executed.append(command) or 0, "ok"),
        max_turns=2,
    )
    assert executed == []
    assert isinstance(result.action, PatchAction)
    assert result.action.patch.target_block_id == "b01"


def test_malformed_repair_response_is_bounded_and_can_recover():
    plan = FlatPlan()
    evidence = add_runtime_evidence(
        base_evidence(),
        evidence_id="runtime:search:1:test",
        source="test",
        content="ambiguous",
    )
    packet = FailurePacket(
        kind="test",
        cycle=1,
        command="python -m pytest -q",
        rc=1,
        output="ambiguous",
        failed_block_id=None,
        plan=plan,
        evidence_id="runtime:search:1:test",
    )
    client = FakeClient(
        [
            "not json",
            json.dumps(
                {
                    "type": "abstain",
                    "classification": "non_environment",
                    "reason": "not environment-shaped",
                    "evidence_refs": ["runtime:search:1:test"],
                }
            ),
        ]
    )
    result = ScriptExecuteAgent(client, "fake").repair(
        packet,
        evidence,
        lambda _command: (0, ""),
        max_turns=2,
    )
    assert result.action.type == "abstain"
    assert result.llm_calls == 2


def test_repair_rejects_abstain_after_repository_path_probe_proves_fix():
    plan = FlatPlan(
        (
            FlatBlock(
                "b01",
                ("python -m pip install -e .",),
                ("python -c 'import package'",),
                ("file:pyproject.toml",),
            ),
        )
    )
    evidence = add_runtime_evidence(
        base_evidence(),
        evidence_id="runtime:search:1:check",
        source="python -c 'import package'",
        content="rc=1\nModuleNotFoundError: No module named 'package'",
    )
    packet = FailurePacket(
        kind="check",
        cycle=1,
        command="python -c 'import package'",
        rc=1,
        output="ModuleNotFoundError: No module named 'package'",
        failed_block_id="b01",
        plan=plan,
        evidence_id="runtime:search:1:check",
    )
    outputs = [
        json.dumps(
            {
                "type": "probe",
                "purpose": "verify repository source layout",
                "command": (
                    "python -c 'import sys; sys.path.insert(0, \"/app/lib/package\"); "
                    "import package; print(package.__file__)'"
                ),
            }
        ),
        json.dumps(
            {
                "type": "abstain",
                "classification": "non_environment",
                "reason": "the package metadata does not expose the import",
                "evidence_refs": ["runtime:repair:1:probe:1"],
            }
        ),
        json.dumps(
            {
                "type": "propose_patch",
                "rationale": "persist the repository-local package path",
                "patch": {
                    "op": "replace_block",
                    "target_block_id": "b01",
                    "block": {
                        "block_id": "b01",
                        "commands": [
                            "printf '%s\\n' /app/lib/package > "
                            "/usr/local/lib/python3.11/site-packages/package-local.pth"
                        ],
                        "checks": ["python -c 'import package'"],
                        "evidence_refs": ["runtime:repair:1:probe:1"],
                    },
                },
            }
        ),
    ]
    client = FakeClient(outputs)
    result = ScriptExecuteAgent(client, "fake").repair(
        packet,
        evidence,
        lambda _command: (0, "/app/lib/package/package/__init__.py"),
        max_turns=3,
    )
    assert isinstance(result.action, PatchAction)
    assert result.llm_calls == 3
    assert result.action.patch.block is not None
    assert ".pth" in result.action.patch.block.commands[0]


def test_repair_probe_ids_remain_unique_across_reprompts_for_same_cycle():
    plan = FlatPlan()
    evidence = add_runtime_evidence(
        base_evidence(),
        evidence_id="runtime:search:1:test",
        source="test",
        content="ambiguous",
    )
    evidence = add_runtime_evidence(
        evidence,
        evidence_id="runtime:repair:1:probe:1",
        source="old probe",
        content="rc=0\nold",
    )
    packet = FailurePacket(
        kind="test",
        cycle=1,
        command="python -m pytest -q",
        rc=1,
        output="ambiguous",
        failed_block_id=None,
        plan=plan,
        evidence_id="runtime:search:1:test",
    )
    client = FakeClient(
        [
            json.dumps(
                {
                    "type": "probe",
                    "purpose": "inspect interpreter",
                    "command": "python --version",
                }
            ),
            json.dumps(
                {
                    "type": "abstain",
                    "classification": "non_environment",
                    "reason": "no environment signal",
                    "evidence_refs": ["runtime:repair:1:probe:2"],
                }
            ),
        ]
    )
    result = ScriptExecuteAgent(client, "fake").repair(
        packet,
        evidence,
        lambda _command: (0, "Python 3.11"),
        max_turns=2,
    )
    assert "runtime:repair:1:probe:2" in result.evidence.ids


def test_exhaustion_reports_consumed_calls_and_usage():
    client = FakeClient(["not json"])
    agent = ScriptExecuteAgent(client, "fake")
    try:
        agent.generate_initial(
            base_evidence(),
            lambda _command: (0, ""),
            base_image="python:3.11-slim",
            languages=("python",),
            test_commands=("python -m pytest -q",),
            max_turns=1,
        )
    except AgentExhausted as exc:
        assert exc.llm_calls == 1
        assert exc.usage["total_tokens"] == 15
    else:
        raise AssertionError("expected AgentExhausted")
