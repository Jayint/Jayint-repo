from __future__ import annotations

import pytest

from ablation.models import FlatBlock, FlatPatch, FlatPlan, PatchAction, ProbeAction
from ablation.policy import (
    FlatPlanGate,
    PolicyError,
    parse_agent_action,
    parse_initial_plan,
    validate_probe_command,
)


EVIDENCE = frozenset(
    {
        "host.base_image",
        "host.test_commands",
        "file:pyproject.toml",
        "runtime:search:1:setup",
    }
)


def block(block_id: str, command: str = "python -m pip install -e .") -> FlatBlock:
    return FlatBlock(
        block_id,
        (command,),
        ("python -m pip check",),
        ("file:pyproject.toml",),
    )


def test_parse_initial_plan_preserves_order_and_fields():
    plan = parse_initial_plan(
        {
            "type": "initial_plan",
            "blocks": [
                {
                    "block_id": "b01-system",
                    "commands": ["apt-get update && apt-get install -y git"],
                    "checks": ["which git"],
                    "evidence_refs": ["host.base_image"],
                },
                {
                    "block_id": "b02-project",
                    "commands": ["python -m pip install -e ."],
                    "checks": ["python -m pip check"],
                    "evidence_refs": ["file:pyproject.toml"],
                },
            ],
        }
    )
    assert [item.block_id for item in plan.blocks] == [
        "b01-system",
        "b02-project",
    ]
    assert plan.blocks[1].checks == ("python -m pip check",)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "initial_plan",
            "nodes": [],
            "blocks": [],
        },
        {
            "type": "initial_plan",
            "blocks": [
                {
                    "block_id": "b01",
                    "commands": ["python -m pip install ."],
                    "evidence_refs": ["file:pyproject.toml"],
                    "target_node_ids": ["pkg:x"],
                }
            ],
        },
        {
            "type": "propose_patch",
            "rationale": "x",
            "patch": {
                "op": "append_block",
                "providers": [],
                "block": {
                    "block_id": "b02",
                    "commands": ["python -m pip install x"],
                    "evidence_refs": ["file:pyproject.toml"],
                },
            },
        },
    ],
)
def test_parser_rejects_graph_contract_fields(payload):
    with pytest.raises(PolicyError, match="graph field"):
        if payload["type"] == "initial_plan":
            parse_initial_plan(payload)
        else:
            parse_agent_action(payload)


def test_parse_probe_and_patch_actions():
    probe = parse_agent_action(
        {"type": "probe", "purpose": "inspect metadata", "command": "cat pyproject.toml"}
    )
    assert isinstance(probe, ProbeAction)

    action = parse_agent_action(
        {
            "type": "propose_patch",
            "rationale": "install the missing test extra",
            "patch": {
                "op": "replace_block",
                "target_block_id": "b01",
                "block": {
                    "block_id": "b01",
                    "commands": ["python -m pip install -e '.[test]'"],
                    "checks": ["python -m pip check"],
                    "evidence_refs": ["file:pyproject.toml"],
                },
            },
        }
    )
    assert isinstance(action, PatchAction)
    assert action.patch.target_block_id == "b01"


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q",
        "set +e; python -m pip install x",
        "python -m pip install x || true",
        "sed -i 's/a/b/' src/app.py",
        "echo x > tests/config.py",
        "docker run alpine true",
        "service redis-server start",
        "systemctl start postgresql",
    ],
)
def test_gate_rejects_test_masking_source_edits_and_container_control(command):
    gate = FlatPlanGate()
    plan = FlatPlan(
        (
            FlatBlock(
                "b01",
                (command,),
                (),
                ("file:pyproject.toml",),
            ),
        )
    )
    result = gate.validate_plan(plan, EVIDENCE)
    assert not result.allowed


def test_gate_allows_normal_environment_install_commands():
    gate = FlatPlanGate()
    plan = FlatPlan(
        (
            FlatBlock(
                "b01-system",
                ("apt-get update && apt-get install -y --no-install-recommends git",),
                ("which git",),
                ("host.base_image",),
            ),
            block("b02-project"),
        )
    )
    result = gate.validate_plan(plan, EVIDENCE)
    assert result.allowed, result.errors


def test_patch_replace_is_atomic_and_preserves_position():
    gate = FlatPlanGate()
    original = FlatPlan((block("b01"), block("b02", "python -m pip install old")))
    replacement = block("b02", "python -m pip install new")
    updated = gate.apply_patch(
        original,
        FlatPatch("replace_block", "b02", replacement),
        EVIDENCE,
        failed_block_id="b02",
        failure_kind="setup",
    )
    assert updated.blocks[0] is original.blocks[0]
    assert updated.blocks[1] == replacement
    assert original.blocks[1].commands == ("python -m pip install old",)


def test_patch_insert_before_failed_block_and_reject_after():
    gate = FlatPlanGate()
    original = FlatPlan((block("b01"), block("b02")))
    prerequisite = FlatBlock(
        "b01a-native",
        ("apt-get update && apt-get install -y libpq-dev",),
        ("pkg-config --exists libpq",),
        ("runtime:search:1:setup",),
    )
    updated = gate.apply_patch(
        original,
        FlatPatch("insert_before", "b02", prerequisite),
        EVIDENCE,
        failed_block_id="b02",
        failure_kind="setup",
    )
    assert [item.block_id for item in updated.blocks] == [
        "b01",
        "b01a-native",
        "b02",
    ]

    with pytest.raises(PolicyError, match="cannot be repaired only after"):
        gate.apply_patch(
            original,
            FlatPatch("insert_after", "b02", prerequisite),
            EVIDENCE,
            failed_block_id="b02",
            failure_kind="setup",
        )


def test_unmapped_setup_failure_still_rejects_append_only_repair():
    gate = FlatPlanGate()
    original = FlatPlan((block("b01"),))
    appended = FlatBlock(
        "b02",
        ("python -m pip install x",),
        (),
        ("runtime:search:1:setup",),
    )
    with pytest.raises(PolicyError, match="cannot be repaired only after"):
        gate.apply_patch(
            original,
            FlatPatch("append_block", None, appended),
            EVIDENCE,
            failed_block_id=None,
            failure_kind="setup",
        )


def test_patch_rejects_unknown_evidence_without_changing_plan():
    gate = FlatPlanGate()
    original = FlatPlan((block("b01"),))
    replacement = FlatBlock(
        "b01",
        ("python -m pip install x",),
        (),
        ("invented:evidence",),
    )
    digest = original.digest()
    with pytest.raises(PolicyError, match="unknown evidence"):
        gate.apply_patch(
            original,
            FlatPatch("replace_block", "b01", replacement),
            EVIDENCE,
        )
    assert original.digest() == digest


def test_probe_validator_allows_reads_and_rejects_mutation():
    assert validate_probe_command("cat pyproject.toml").allowed
    assert validate_probe_command("python -c 'import json; print(json.__name__)'").allowed
    assert not validate_probe_command("apt-get install -y git").allowed
    assert not validate_probe_command("python -c 'open(\"x\", \"w\").write(\"x\")'").allowed


@pytest.mark.parametrize(
    "command",
    [
        "python -m pip --version",
        "python -m pipx --version",
        "python -m poetry --version",
    ],
)
def test_probe_validator_allows_curated_python_module_versions(command):
    result = validate_probe_command(command)
    assert result.allowed, result.errors


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest --version",
        "python -m arbitrary_module --version",
        "python -m poetry install",
        "python -m pipx install poetry",
    ],
)
def test_probe_validator_rejects_unknown_or_mutating_python_modules(command):
    assert not validate_probe_command(command).allowed


@pytest.mark.parametrize(
    "command",
    [
        "command apt-get install -y git",
        "env apt-get install -y git",
        "ldconfig",
        "X=1 cat pyproject.toml",
        "sort -o /tmp/pwn /etc/hosts",
        "sort --output=/tmp/pwn /etc/hosts",
        "unapproved-tool --version",
        "python -c \"getattr(__import__('os'), 'system')('touch /tmp/pwn')\"",
    ],
)
def test_probe_validator_rejects_dispatch_and_write_bypasses(command):
    result = validate_probe_command(command)
    assert not result.allowed, command


@pytest.mark.parametrize(
    "command",
    [
        "command -v git",
        "env",
        "ldconfig -p",
        "sort -u requirements.txt",
        "go list -mod=readonly ./...",
        "cargo metadata --locked --offline",
    ],
)
def test_probe_validator_keeps_exact_read_only_forms(command):
    result = validate_probe_command(command)
    assert result.allowed, result.errors


@pytest.mark.parametrize(
    "command",
    [
        "cp /tmp/replacement tests/test_api.py",
        "printf x | tee tests/test_api.py",
        "find tests -delete",
        "chmod -x tests/test_api.py",
        "bash -lc 'python -m pytest -q'",
        "env COVERAGE_FILE=/tmp/.coverage pytest -q",
        "timeout 60 pytest -q",
        "sudo -u root pytest -q",
        "poetry run pytest -q",
    ],
)
def test_gate_rejects_repo_mutation_and_wrapped_test_commands(command):
    plan = FlatPlan(
        (
            FlatBlock(
                "b01",
                (command,),
                (),
                ("file:pyproject.toml",),
            ),
        )
    )
    result = FlatPlanGate().validate_plan(plan, EVIDENCE)
    assert not result.allowed, command


def test_gate_still_allows_installing_a_tool_outside_the_repository():
    plan = FlatPlan(
        (
            FlatBlock(
                "b01",
                ("install -m 0755 /tmp/tool /usr/local/bin/tool",),
                (),
                ("host.base_image",),
            ),
        )
    )
    result = FlatPlanGate().validate_plan(plan, EVIDENCE)
    assert result.allowed, result.errors
