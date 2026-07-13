import sys, pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.runner import (REPO_HINTS, ClaudeRunner, FakeRunner, TASK_PROMPT,
                                          prompt_for)


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


def test_task_prompt_states_the_gate_clauses_the_agent_cannot_infer():
    # The three clauses that rejected real repos: exit 0 on BOTH runs (one bad module sinks
    # the repo), a stable node-id SET, and the sandbox's no-network/4GB caps.
    assert "exit 0 on BOTH runs" in TASK_PROMPT
    assert "IDENTICAL ACROSS THE TWO RUNS" in TASK_PROMPT
    assert "PYTHONHASHSEED=0" in TASK_PROMPT
    assert "--network none" in TASK_PROMPT and "4 GB" in TASK_PROMPT


def test_prompt_for_returns_bare_prompt_when_no_hint():
    assert prompt_for("https://github.com/some/unhinted-repo") == TASK_PROMPT


@pytest.mark.parametrize("url", [
    "https://github.com/mlflow/mlflow",
    "https://github.com/mlflow/mlflow.git",       # corpus rows carry a .git suffix
    "https://github.com/mlflow/mlflow/",          # ...and sometimes a trailing slash
    "https://github.com/MLFLOW/MLFLOW",           # match must be case-insensitive
])
def test_prompt_for_appends_hint_despite_url_shape(url):
    p = prompt_for(url)
    assert p.startswith(TASK_PROMPT) and len(p) > len(TASK_PROMPT)
    assert "REPO-SPECIFIC" in p


def test_every_hint_is_reachable_by_its_key():
    # A typo'd key would silently degrade to the bare prompt -- the hint would just never fire.
    for full_name in REPO_HINTS:
        assert prompt_for(f"https://github.com/{full_name}") != TASK_PROMPT


def test_hints_add_information_but_never_relax_the_rules():
    # A hint must not become a loophole: the Dockerfile-only rule and the no-hiding rule are
    # part of TASK_PROMPT, so they survive in every hinted prompt.
    for full_name in REPO_HINTS:
        p = prompt_for(f"https://github.com/{full_name}")
        assert "Edit ONLY the `Dockerfile`" in p
        assert "Do NOT fake a clean collection by hiding tests" in p


def test_default_run_soft_fails_on_timeout(monkeypatch):
    import subprocess
    from src.manifest_builder import runner as R

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1,
                                        output="partial stdout", stderr="partial stderr")

    monkeypatch.setattr(subprocess, "run", boom)
    rc, out = R._default_run(["claude", "-p", "x"], timeout=1, cwd=None)
    assert rc == 124
    assert "partial stdout" in out and "timed out" in out
