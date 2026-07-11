import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.runner import GrokRunner, FakeRunner, TASK_PROMPT


def test_grok_argv_targets_grok45_medium_autonomous(tmp_path):
    calls = []

    def fake_run(argv, timeout=None):
        calls.append(argv)
        return 0, '{"event":"done"}\n'

    r = GrokRunner(run=fake_run)
    res = r.run(cwd=str(tmp_path), prompt="do it", autonomous=True)
    argv = calls[0]
    assert argv[0] == "grok"
    assert "-p" in argv and "do it" in argv
    assert "--always-approve" in argv and "--no-auto-update" in argv
    assert argv[argv.index("-m") + 1] == "grok-4.5"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--cwd") + 1] == str(tmp_path)
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
