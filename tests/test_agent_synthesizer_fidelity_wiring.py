"""Agent wiring: the v1g finalize path drops replay, captures FINAL file content, and
emits the pinned closure + one project install (Tier-1 synthesizer fidelity)."""
import base64
import os
import tempfile

from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.world_model import Fact
from src.synthesizer import Synthesizer


def _ev(cmd, rc=0, mc=None, step=1):
    return ActionEvent(
        step=step, task_id=None, cmd=cmd, rc=rc, stdout="", stdout_path=None,
        stderr_path=None, env_revision_before=0, env_revision_after=0,
        mutation_class=mc, container_id="c1", summary="",
    )


class _FakeSandbox:
    """Fakes the lossless, non-login byte read used by Tier-1 file capture.

    ``files`` maps path -> bytes (or str, encoded as UTF-8). A path mapped to the
    sentinel ``OVERSIZE`` returns more than max_bytes so capture treats it as a miss.
    """

    workdir = "/app"
    OVERSIZE = object()

    def __init__(self, files):
        self._files = files

    def read_file_bytes(self, path, max_bytes):
        if path not in self._files:
            return 1, b""                       # unreadable -> miss -> replay fallback
        value = self._files[path]
        if value is self.OVERSIZE:
            return 0, b"A" * (int(max_bytes) + 10)
        if isinstance(value, str):
            value = value.encode("utf-8")
        return 0, value


def _make_agent(ledger, installed, files, workplace):
    from agent import DockerAgent
    agent = DockerAgent.__new__(DockerAgent)
    agent.synthesizer = Synthesizer(base_image="python:3.11-slim", workdir="/app")
    agent.enable_envstate = True
    agent.action_ledger = ledger
    agent._final_installed = installed
    agent.sandbox = _FakeSandbox(files)
    agent.workplace = workplace
    agent.repo_url = "https://github.com/x/proj.git"
    return agent


def test_v1g_finalize_assembles_from_state(tmp_path):
    # packaging metadata only (no [project] name) -> name resolves from repo basename "proj"
    (tmp_path / "setup.py").write_text("from setuptools import setup; setup(name='proj')")

    led = ActionLedger()
    led.append(_ev("apt-get install -y libpq-dev", mc="system_package_install"))
    led.append(_ev("sed -i 's/a/b/' conf.py"))
    led.append(_ev("sed -i 's/x/y/' /app/pkg/m.py"))
    led.append(_ev("pip install -e .", mc="language_package_install"))
    led.append(_ev("pip install proj", mc="language_package_install"))  # overwrite bug
    led.append(_ev("python -m pytest -q", mc=None))

    installed = (Fact("requests", "2.31.0"), Fact("proj", "0.1.0"))
    files = {"conf.py": "FINAL=1\n", "/app/pkg/m.py": "M=2\n"}

    agent = _make_agent(led, installed, files, str(tmp_path))

    assert agent._synthesize_final_build_recipe(drop_replayed_state=True) is True
    agent._emit_interleaved_state_recipe()
    agent._emit_closure_recipe()

    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as tmp:
        path = tmp.name
    try:
        df = agent.synthesizer.generate_dockerfile(file_path=path)
    finally:
        os.unlink(path)

    # Fix 1 — no replayed edits; final content baked once per file, paths translated.
    assert "sed -i" not in df
    assert df.count("base64 -d > 'conf.py'") == 1
    assert df.count("base64 -d > 'pkg/m.py'") == 1          # /app/ stripped
    assert base64.b64encode(b"FINAL=1\n").decode() in df
    assert base64.b64encode(b"M=2\n").decode() in df

    # Fix 2 — no replayed installs; pinned closure + exactly one project install.
    assert "pip install proj" not in df
    assert df.count("pip install -e .") == 1               # the --no-deps project install
    assert "--no-deps" in df
    assert "requests==2.31.0" in df
    assert "proj==0.1.0" not in df                         # project excluded from pin

    # Irreducible system step kept; no test command leaked.
    assert "libpq-dev" in df
    assert "pytest" not in df


def _emit_only(agent):
    agent._synthesize_final_build_recipe(drop_replayed_state=True)
    agent._emit_interleaved_state_recipe()


def test_capture_miss_falls_back_to_replaying_edit(tmp_path):
    """HIGH 2 core safety net: a file we cannot read must NOT be silently dropped —
    its original edit command is replayed instead."""
    (tmp_path / "setup.py").write_text("from setuptools import setup; setup(name='proj')")
    led = ActionLedger()
    led.append(_ev("sed -i 's/a/b/' readable.py"))
    led.append(_ev("sed -i 's/c/d/' unreadable.py"))   # read fails -> replay
    files = {"readable.py": "OK\n"}                    # unreadable.py absent
    agent = _make_agent(led, (Fact("requests", "2.31.0"),), files, str(tmp_path))
    _emit_only(agent)
    df = "\n".join(agent.synthesizer.instructions)
    assert "base64 -d > 'readable.py'" in df           # captured
    assert "sed -i 's/c/d/' unreadable.py" in df        # replayed, not lost
    assert "base64 -d > 'unreadable.py'" not in df


def test_oversize_falls_back_to_replaying_edit(tmp_path):
    """HIGH 2: an oversize file is a capture miss -> replay the edit, never drop it."""
    (tmp_path / "setup.py").write_text("from setuptools import setup; setup(name='proj')")
    led = ActionLedger()
    led.append(_ev("sed -i 's/a/b/' big.py"))
    files = {"big.py": _FakeSandbox.OVERSIZE}
    agent = _make_agent(led, (Fact("requests", "2.31.0"),), files, str(tmp_path))
    _emit_only(agent)
    df = "\n".join(agent.synthesizer.instructions)
    assert "base64 -d > 'big.py'" not in df             # not captured
    assert "sed -i 's/a/b/' big.py" in df               # replayed


def test_non_utf8_byte_captured_losslessly(tmp_path):
    """HIGH 2: a mostly-UTF-8 file with an odd byte is captured byte-exact."""
    import base64 as _b64
    (tmp_path / "setup.py").write_text("from setuptools import setup; setup(name='proj')")
    raw = b"name = caf\xe9\nval = 1\n"                   # 0xe9 is not valid UTF-8 alone
    led = ActionLedger()
    led.append(_ev("sed -i 's/a/b/' conf.cfg"))
    files = {"conf.cfg": raw}
    agent = _make_agent(led, (Fact("requests", "2.31.0"),), files, str(tmp_path))
    _emit_only(agent)
    df = "\n".join(agent.synthesizer.instructions)
    assert _b64.b64encode(raw).decode() in df           # exact bytes baked, not corrupted
