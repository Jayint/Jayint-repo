from agent import DockerAgent

def _agent_at_finalize(tmp_path):
    a = DockerAgent.__new__(DockerAgent)
    a.workplace = str(tmp_path)
    a.verification_bundle = {"test_commands": ["python -m pytest -q"]}
    a.verified_test_commands = ["python -m pytest -q"]
    return a

def test_synthesis_failure_does_not_void_passed_gate(tmp_path):
    a = _agent_at_finalize(tmp_path)
    def boom(*_a, **_k):
        raise RuntimeError("synth exploded")
    a._finalize_supervisor_artifacts = boom
    cs = DockerAgent._v1_finalize_and_keep_success(a, gate_passed=True)
    assert cs is True

def test_finalize_returning_false_does_not_void_passed_gate(tmp_path):
    a = _agent_at_finalize(tmp_path)
    a._finalize_supervisor_artifacts = lambda *_a, **_k: False
    cs = DockerAgent._v1_finalize_and_keep_success(a, gate_passed=True)
    assert cs is True

def test_gate_not_passed_stays_false(tmp_path):
    a = _agent_at_finalize(tmp_path)
    a._finalize_supervisor_artifacts = lambda *_a, **_k: True
    cs = DockerAgent._v1_finalize_and_keep_success(a, gate_passed=False)
    assert cs is False
