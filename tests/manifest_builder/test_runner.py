import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.runner import ClaudeRunner, FakeRunner, TASK_PROMPT


def test_claude_argv_autonomous_headless(tmp_path):
    calls = []

    def fake_run(argv, timeout=None, cwd=None):
        calls.append((argv, cwd))
        return 0, '{"type":"result"}\n'

    r = ClaudeRunner(run=fake_run)
    res = r.run(cwd=str(tmp_path), prompt="do it", autonomous=True)
    argv, cwd = calls[0]
    assert argv[0] == "claude"
    assert "-p" in argv and "do it" in argv
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--output-format" in argv and "stream-json" in argv and "--verbose" in argv
    assert cwd == str(tmp_path)                       # runs IN the workspace (no --cwd flag)
    assert res.claimed_done is True
    assert res.transcript_path and pathlib.Path(res.transcript_path).exists()


def test_fake_runner_applies_edit(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n")

    def edit(cwd):
        p = pathlib.Path(cwd) / "Dockerfile"
        p.write_text(p.read_text() + "RUN pip install pytest\n")

    res = FakeRunner(edit_fn=edit).run(cwd=str(tmp_path), prompt="x", autonomous=True)
    assert res.claimed_done is True
    assert "RUN pip install pytest" in (tmp_path / "Dockerfile").read_text()


def test_task_prompt_states_dockerfile_only_and_maximize():
    assert "Dockerfile" in TASK_PROMPT and "verify" in TASK_PROMPT.lower()
    assert "maxim" in TASK_PROMPT.lower()
    assert "service" in TASK_PROMPT.lower() and "client library" in TASK_PROMPT.lower()
    assert "import_skipped" in TASK_PROMPT
