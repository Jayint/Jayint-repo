"""TDD tests for DROPPED_ENV: bake test-required env vars into Dockerfile ENV.

`RUN export X=Y` does NOT persist across Docker RUN layers or into the runtime
container, so the eval harness loses it (the django-oauth synth-gap). We capture
the explicit assignments the agent made and emit them as Dockerfile `ENV` lines.
"""
import os
import tempfile

from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.synthesis import extract_env_vars_from_ledger
from src.synthesizer import Synthesizer


def _event(step, cmd, rc, mutation_class=None):
    return ActionEvent(
        step=step,
        task_id=None,
        cmd=cmd,
        rc=rc,
        stdout="",
        stdout_path=None,
        stderr_path=None,
        env_revision_before=0,
        env_revision_after=0,
        mutation_class=mutation_class,
        container_id="c1",
        summary="",
    )


def test_extract_export_referenced():
    """`export X=Y` (rc==0) kept when later command references $X."""
    ledger = ActionLedger()
    ledger.append(_event(1, "export DJANGO_SETTINGS_MODULE=cfg.settings", 0, None))
    ledger.append(_event(2, "echo $DJANGO_SETTINGS_MODULE", 0, None))
    result = extract_env_vars_from_ledger(ledger)
    assert result == [("DJANGO_SETTINGS_MODULE", "cfg.settings")], result


def test_extract_inline_prefix():
    """Inline `NAME=VALUE cmd` prefix captured from extra_commands."""
    result = extract_env_vars_from_ledger(
        ActionLedger(),
        extra_commands=["DJANGO_SETTINGS_MODULE=cfg pytest -q"],
    )
    assert result == [("DJANGO_SETTINGS_MODULE", "cfg")], result


def test_denylist_and_unreferenced_dropped():
    """PATH is denylisted; an unreferenced throwaway export is dropped."""
    ledger = ActionLedger()
    ledger.append(_event(1, "export PATH=/x:$PATH", 0, None))   # denylisted -> drop
    ledger.append(_event(2, "export FOO=bar", 0, None))          # never referenced -> drop
    result = extract_env_vars_from_ledger(ledger)
    assert result == [], result


def test_last_assignment_wins_and_order():
    """Last value wins for a repeated var; first-appearance order preserved."""
    ledger = ActionLedger()
    ledger.append(_event(1, "export A=1", 0, None))
    ledger.append(_event(2, "export B=2", 0, None))
    ledger.append(_event(3, "export A=3", 0, None))
    ledger.append(_event(4, "echo $A $B", 0, None))
    result = extract_env_vars_from_ledger(ledger)
    assert result == [("A", "3"), ("B", "2")], result


def test_secret_named_vars_not_baked():
    """Credential-looking var names are never baked (secret-leak guard), but benign
    test config (DJANGO_SETTINGS_MODULE) and a referenced DATABASE_URL are kept."""
    result = extract_env_vars_from_ledger(
        ActionLedger(),
        extra_commands=[
            "OPENAI_API_KEY=sk-xxx pytest -q",
            "DJANGO_SETTINGS_MODULE=cfg pytest -q",
            "GITHUB_TOKEN=ghp_zzz pytest -q",
            "DB_PASSWORD=hunter2 pytest -q",
            "AWS_SECRET_ACCESS_KEY=abc/def pytest -q",
            "DATABASE_URL=postgres://localhost/db pytest -q",
        ],
    )
    names = [n for n, _ in result]
    assert "DJANGO_SETTINGS_MODULE" in names, result   # the real motivating var, kept
    assert "DATABASE_URL" in names, result             # benign + referenced, kept
    for secret in ("OPENAI_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY"):
        assert secret not in names, (secret, result)


def test_pythonpath_not_baked():
    """A stale absolute PYTHONPATH must never be baked (breaks imports in the rebuilt
    image that clones to a different path). Covered for both inline-prefix and export+ref."""
    ledger = ActionLedger()
    ledger.append(_event(1, "export PYTHONPATH=/app", 0, None))
    ledger.append(_event(2, "echo $PYTHONPATH", 0, None))
    result = extract_env_vars_from_ledger(
        ledger,
        extra_commands=["PYTHONPATH=/app pytest -q"],
    )
    names = [n for n, _ in result]
    assert "PYTHONPATH" not in names, result


def test_add_env_instruction_escapes_and_prepends():
    r"""$ -> \$, " -> \", \ -> \\ ; prepended; idempotent.

    The literal-`$` escape in a Dockerfile ENV is `\$` (verified on real Docker:
    `ENV X="a\$b"` stores `a$b`). `$$` is docker-COMPOSE syntax and would store the
    two `$`s as empty-variable expansions (`ENV X="a$$b"` stores `ab`)."""
    synth = Synthesizer()
    synth.add_env_instruction("X", 'a$b "c" \\d')
    assert synth.instructions[0] == 'ENV X="a\\$b \\"c\\" \\\\d"', synth.instructions[0]
    # idempotent: adding the identical instruction again keeps exactly one
    synth.add_env_instruction("X", 'a$b "c" \\d')
    assert synth.instructions.count('ENV X="a\\$b \\"c\\" \\\\d"') == 1, synth.instructions


def test_add_env_instruction_dollar_uses_backslash_not_double_dollar():
    """A `$`-bearing value escapes to `\\$` (Dockerfile literal), NEVER `$$`
    (docker-compose syntax that Docker stores as empty expansions)."""
    synth = Synthesizer()
    synth.add_env_instruction("Y", "a$b")
    rendered = synth.instructions[0]
    assert "$$" not in rendered, rendered
    assert "\\$" in rendered, rendered
    assert rendered == 'ENV Y="a\\$b"', rendered


class _RecordingSynth:
    """Records the (name, value) pairs passed to add_env_instruction."""

    def __init__(self):
        self.calls = []

    def add_env_instruction(self, name, value):
        self.calls.append((name, value))


def _make_bare_agent(synth, ledger, verified_cmds=None, verified_cmd=None):
    """DockerAgent via __new__ (bypass __init__), wired with the minimal attrs
    that _bake_test_env_vars reads. Mirrors the fakes in test_v1_finalize_partial_pass."""
    from agent import DockerAgent

    a = DockerAgent.__new__(DockerAgent)
    a.synthesizer = synth
    a.action_ledger = ledger
    a.verified_test_commands = verified_cmds if verified_cmds is not None else []
    a.verified_test_command = verified_cmd
    return a


def test_bake_test_env_vars_wires_extract_to_add_env():
    """_bake_test_env_vars feeds extract_env_vars_from_ledger output into the
    synthesizer's add_env_instruction (export + referenced => baked)."""
    ledger = ActionLedger()
    ledger.append(_event(1, "export DJANGO_SETTINGS_MODULE=cfg.settings", 0, None))
    ledger.append(_event(2, "echo $DJANGO_SETTINGS_MODULE", 0, None))
    synth = _RecordingSynth()
    agent = _make_bare_agent(synth, ledger)
    agent._bake_test_env_vars()
    assert synth.calls == [("DJANGO_SETTINGS_MODULE", "cfg.settings")], synth.calls


def test_bake_test_env_vars_uses_inline_prefix_from_verified_commands():
    """Inline `NAME=VALUE cmd` prefixes from verified_test_command(s) are baked."""
    synth = _RecordingSynth()
    agent = _make_bare_agent(
        synth,
        ActionLedger(),
        verified_cmds=["DJANGO_SETTINGS_MODULE=cfg pytest -q"],
        verified_cmd=None,
    )
    agent._bake_test_env_vars()
    assert synth.calls == [("DJANGO_SETTINGS_MODULE", "cfg")], synth.calls


def test_bake_test_env_vars_best_effort_never_raises():
    """A failure inside the helper degrades gracefully (no exception escapes)."""
    class _BoomSynth:
        def add_env_instruction(self, name, value):
            raise RuntimeError("boom")

    ledger = ActionLedger()
    ledger.append(_event(1, "export DJANGO_SETTINGS_MODULE=cfg.settings", 0, None))
    ledger.append(_event(2, "echo $DJANGO_SETTINGS_MODULE", 0, None))
    agent = _make_bare_agent(_BoomSynth(), ledger)
    agent._bake_test_env_vars()  # must not raise


def test_generate_dockerfile_contains_env():
    """ENV line renders verbatim and precedes the RUN that needs it."""
    synth = Synthesizer()
    synth.add_build_instruction("pytest -q")  # a RUN step that needs the env
    synth.add_env_instruction("DJANGO_SETTINGS_MODULE", "cfg")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "Dockerfile")
        text = synth.generate_dockerfile(file_path=path)
        with open(path) as f:
            text = f.read()
    assert 'ENV DJANGO_SETTINGS_MODULE="cfg"' in text, text
    env_idx = text.index('ENV DJANGO_SETTINGS_MODULE="cfg"')
    run_idx = text.index("pytest")
    assert env_idx < run_idx, (
        f"ENV must precede the RUN that needs it (env={env_idx}, run={run_idx})\n{text}"
    )
