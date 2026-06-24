"""The v1 three-role loop executes commands inside the BuildAgent, recording them
only into self.action_ledger; it never calls _record_successful_action (the arm0
live-capture path). So self.successful_actions stayed empty and the Dockerfile
synthesizer had no trajectory to replay — every working v1 environment was lost
at synthesis (observed e2e: a repo built a 96-pkg env in-sandbox but its eval
Dockerfile reconstructed only `git`).

_backfill_successful_actions_from_ledger() derives the records post-hoc from the
authoritative ledger, mirroring the arm0 record shape and re-deriving the
classifier flags via the synthesizer. Honesty: rc==0 only; classify-don't-filter
(read-only/test commands kept and tagged); collect-only stays
is_effective_test_run=False; unterminated heredoc openers (A1 truncation) dropped.
"""
import agent as agent_mod
from src.envstate.ledger import ActionEvent, ActionLedger
from src.synthesizer import Synthesizer


def _agent_with_events(events):
    a = agent_mod.DockerAgent.__new__(agent_mod.DockerAgent)
    a.successful_actions = []
    a._environment_revision = 0
    a.synthesizer = Synthesizer()
    a.action_ledger = ActionLedger()
    for ev in events:
        a.action_ledger.append(ev)
    return a


def _by_cmd(records):
    return {r["command"]: r for r in records}


def test_backfill_captures_rc0_and_excludes_failures_and_heredocs():
    a = _agent_with_events([
        ActionEvent(step=1, cmd="pip install flask", rc=0,
                    stdout="Successfully installed flask-3.0.0"),
        ActionEvent(step=2, cmd="ls -la", rc=0, stdout="total 0"),
        ActionEvent(step=3, cmd="pip install does-not-exist", rc=1,
                    stdout="ERROR: No matching distribution"),
        ActionEvent(step=4, cmd="pytest --collect-only", rc=0,
                    stdout="collected 10 items"),
        ActionEvent(step=5, cmd="python -m pytest -q", rc=0,
                    stdout="10 passed in 1.20s"),
        ActionEvent(step=6, cmd="cat > /app/conftest.py << 'EOF'", rc=0, stdout=""),
    ])

    a._backfill_successful_actions_from_ledger()
    rec = _by_cmd(a.successful_actions)

    # rc != 0 excluded; unterminated heredoc opener excluded.
    assert "pip install does-not-exist" not in rec
    assert "cat > /app/conftest.py << 'EOF'" not in rec
    # every other rc=0 command captured, in ledger order.
    assert [r["command"] for r in a.successful_actions] == [
        "pip install flask", "ls -la", "pytest --collect-only", "python -m pytest -q",
    ]
    assert [r["step_index"] for r in a.successful_actions] == [1, 2, 4, 5]


def test_backfill_classifies_without_filtering():
    a = _agent_with_events([
        ActionEvent(step=1, cmd="pip install flask", rc=0,
                    stdout="Successfully installed flask-3.0.0"),
        ActionEvent(step=2, cmd="ls -la", rc=0, stdout="total 0"),
    ])
    a._backfill_successful_actions_from_ledger()
    rec = _by_cmd(a.successful_actions)

    assert rec["pip install flask"]["mutates_environment"] is True
    assert rec["pip install flask"]["is_readonly"] is False
    assert rec["ls -la"]["is_readonly"] is True
    assert rec["ls -la"]["mutates_environment"] is False
    # full arm0 record shape present on every record.
    for r in a.successful_actions:
        for key in (
            "step_index", "command", "observation", "environment_revision",
            "mutates_environment", "is_readonly", "is_runtime_service",
            "is_runtime_healthcheck", "observed_test_signal", "test_analysis",
        ):
            assert key in r


def test_backfill_does_not_fabricate_a_verified_pass_and_tags_test_commands():
    """Honesty guarantees that actually live in this fix's blast radius:
    (1) test commands are tagged is_test_command so the synthesizer excludes them
        from Dockerfile RUN lines (collect-only can never become a build step);
    (2) the backfill records the BUILD trajectory only and must NOT fabricate the
        honest pass signal — verified_test_command stays owned by the done-gate
        (_resolve_v1_verified_test_run), untouched here.
    (Whether analyze_test_run calls collect-only "effective" is a pre-existing
    synthesizer behavior, identical on the arm0 path; not this fix's concern.)"""
    a = _agent_with_events([
        ActionEvent(step=1, cmd="pytest --collect-only", rc=0,
                    stdout="collected 10 items"),
        ActionEvent(step=2, cmd="python -m pytest -q", rc=0,
                    stdout="10 passed in 1.20s"),
    ])
    a._backfill_successful_actions_from_ledger()
    rec = _by_cmd(a.successful_actions)

    assert rec["pytest --collect-only"]["test_analysis"]["is_test_command"] is True
    assert rec["python -m pytest -q"]["test_analysis"]["is_test_command"] is True
    # the backfill must not touch the honest done-gate's verified-test state.
    assert getattr(a, "verified_test_commands", None) is None
    assert getattr(a, "verified_test_command", None) is None


def test_backfill_is_idempotent_and_does_not_clobber():
    a = _agent_with_events([
        ActionEvent(step=1, cmd="pip install flask", rc=0, stdout="Successfully installed flask-3.0.0"),
    ])
    a._backfill_successful_actions_from_ledger()
    n = len(a.successful_actions)
    a._backfill_successful_actions_from_ledger()
    assert len(a.successful_actions) == n  # no duplication on a second call
