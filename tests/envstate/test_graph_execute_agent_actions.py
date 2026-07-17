import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for _path in (str(_ROOT), str(_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from src.envstate.agent_action import (  # noqa: E402
    AbstainAction,
    ProbeAction,
    ProposePatchAction,
    parse_agent_action,
    validate_probe_command,
)
from src.envstate.repair_scope import RepairScope  # noqa: E402
from src.envstate.v3_build_agent import GraphExecuteAgent  # noqa: E402


class _Msg:
    def __init__(self, content):
        self.content = content
        self.reasoning = None
        self.model_extra = {}


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = None


class _Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.chat = self
        self.calls = []

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self.responses.pop(0))


def _scope():
    return RepairScope(
        "syslib:libdemo",
        "apt-get install -y bad-demo",
        "header missing",
        (),
        (),
        (),
        frozenset({"ev.demo"}),
    )


def _patch_action():
    return json.dumps({
        "type": "propose_patch",
        "target_node": "syslib:libdemo",
        "rationale": {"why": "the development package provides the header"},
        "patch": {
            "add_providers": [{
                "id": "apt:libdemo-dev",
                "kind": "apt",
                "command": "apt-get install -y libdemo-dev",
                "provides": ["syslib:libdemo"],
                "override": True,
            }],
        },
    })


def test_three_structured_action_types_parse_and_patch_keeps_full_schema():
    probe = parse_agent_action({
        "type": "probe",
        "target_node": "pkg:demo",
        "purpose": "inspect metadata",
        "command": "python -m pip show demo",
    })
    assert isinstance(probe, ProbeAction)

    proposed = parse_agent_action({
        "type": "propose_patch",
        "target_node": "import:demo",
        "rationale": "runtime evidence identifies the package",
        "patch": {
            "add_requirements": [{
                "id": "pkg:demo",
                "type": "Package",
                "name": "demo",
                "version": "1.2.3",
                "layer": "pip",
                "check_command": "python -m pip show demo",
                "evidence_ref": "ev.demo",
            }],
            "add_providers": [{
                "id": "pip:demo",
                "kind": "pip",
                "command": "python -m pip install demo==1.2.3",
                "provides": ["pkg:demo"],
            }],
            "add_edges": [{
                "source": "import:demo",
                "target": "pkg:demo",
                "relation": "requires",
                "hard": True,
            }],
            "script_patches": [{
                "op": "add_block",
                "block_id": "naming.demo",
                "wave": "naming",
                "commands": ["python -c 'import demo'"],
                "target_node_ids": ["import:demo"],
                "checks": ["python -c 'import demo'"],
                "evidence_ref": "ev.demo",
            }],
        },
    })
    assert isinstance(proposed, ProposePatchAction)
    assert proposed.proposal.add_requirements
    assert proposed.proposal.add_providers
    assert proposed.proposal.add_edges
    assert proposed.proposal.script_patches

    abstain = parse_agent_action({
        "type": "abstain",
        "classification": "non_environment",
        "reason": "repository syntax error",
        "evidence_refs": ["ev.syntax"],
    })
    assert isinstance(abstain, AbstainAction)


def test_structured_action_prompt_exposes_complete_script_patch_contract():
    from src.envstate.repair_scope import render_repair_scope

    prompt = render_repair_scope(_scope(), structured_actions=True)
    assert '"target_node_ids":["<failed node id>"]' in prompt
    assert '"evidence_ref":"<ev.id>"' in prompt
    assert "interpreter, system, toolchain, pip, naming" in prompt
    assert "changing repository imports alone does not satisfy" in prompt
    assert "Provider ids such as pip:<name> are actions, not graph nodes" in prompt
    assert "import:<module> -> pkg:<dist>" in prompt


def test_probe_validator_allows_open_readonly_pipeline_and_rejects_mutations():
    assert validate_probe_command(
        "pkg-config --cflags tesseract | grep -q tesseract"
    ).allowed
    assert validate_probe_command("find /usr/include -name baseapi.h").allowed
    assert validate_probe_command("python -c 'import sys; print(sys.version)'").allowed
    for command in (
        "apt-get install -y redis",
        "rm -rf /tmp/demo",
        "cat /etc/os-release > /tmp/copy",
        "chmod 777 /app",
        "service redis-server start",
        "python -c \"open('/tmp/x', 'w').write('x')\"",
    ):
        validation = validate_probe_command(command)
        assert not validation.allowed, command


def test_legal_probe_runs_then_complete_patch_is_returned():
    probe = json.dumps({
        "type": "probe",
        "target_node": "syslib:libdemo",
        "purpose": "check target package metadata",
        "command": "apt-cache search libdemo",
    })
    commands = []
    events = []
    agent = GraphExecuteAgent(_Client(probe, _patch_action()), "fake")
    proposal = agent.propose(
        _scope(),
        exec_readonly=lambda command: (commands.append(command) or (0, "libdemo-dev")),
        action_observer=events.append,
    )
    assert commands == ["apt-cache search libdemo"]
    assert proposal.add_providers[0].id == "apt:libdemo-dev"
    assert [event["action_type"] for event in events] == ["probe", "propose_patch"]


def test_mutation_probe_is_rejected_and_never_reaches_sandbox():
    mutation = json.dumps({
        "type": "probe",
        "target_node": "syslib:libdemo",
        "purpose": "try package",
        "command": "apt-get install -y libdemo-dev",
    })
    executed = []
    events = []
    agent = GraphExecuteAgent(_Client(mutation, _patch_action()), "fake")
    proposal = agent.propose(
        _scope(),
        exec_readonly=lambda command: (executed.append(command) or (0, "")),
        action_observer=events.append,
    )
    assert executed == []
    assert proposal.add_providers
    assert events[0]["action_type"] == "probe"
    assert events[0]["validated"] is False
    assert "mutation" in events[0]["rejection"]


def test_malformed_output_gets_structured_retry():
    client = _Client("not-json", _patch_action())
    agent = GraphExecuteAgent(client, "fake")
    proposal = agent.propose(_scope(), exec_readonly=lambda command: (0, ""))
    assert proposal.add_providers
    retry_message = client.calls[1]["messages"][-1]["content"]
    assert json.loads(retry_message)["type"] == "action_rejected"


def test_abstain_is_returned_as_advice_for_host_review():
    payload = json.dumps({
        "type": "abstain",
        "classification": "non_environment",
        "reason": "the traceback points to repository syntax",
        "evidence_refs": ["ev.demo"],
    })
    action = GraphExecuteAgent(_Client(payload), "fake").propose(
        _scope(), exec_readonly=lambda command: (0, "")
    )
    assert isinstance(action, AbstainAction)
