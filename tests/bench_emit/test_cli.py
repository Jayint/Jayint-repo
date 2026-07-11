# tests/bench_emit/test_cli.py
import json

import pytest

from src.bench_emit.__main__ import main


def _rat_run(tmp_path):
    root = tmp_path / "ratrun"
    repo = root / "output" / "fastapi" / "typer"
    repo.mkdir(parents=True)
    (repo / "case_study.json").write_text(json.dumps(
        {"environment": {"base_image": "python:3.10-slim", "recipe_commands": ["pip install -e ."]}}))
    return str(root)


def test_cli_emits_tree_and_returns_zero(tmp_path, capsys):
    run_root = _rat_run(tmp_path)
    dest = tmp_path / "harvest"
    rc = main(["--run", run_root, "--agent", "rat", "--dest", str(dest)])
    assert rc == 0
    df = (dest / "fastapi" / "typer" / "Dockerfile").read_text()
    assert df.startswith("FROM python:3.10-slim")
    assert (dest / "fastapi" / "typer" / "bench_meta.json").is_file()
    assert "1/1 ok" in capsys.readouterr().out


def _claude_run(tmp_path):
    root = tmp_path / "clauderun"
    repo = root / "output" / "pallets" / "click"
    eb = repo / "eval_build"
    eb.mkdir(parents=True)
    (eb / "Dockerfile").write_text(
        "FROM python:3.11\nRUN git clone --depth=1 https://github.com/pallets/click /testbed\nWORKDIR /testbed\n")
    (repo / "_meta.json").write_text(json.dumps({"duration_s": 3.0, "agent_cost_usd": 0.1}))
    return str(root)


def test_cli_accepts_claude_agent(tmp_path, capsys):
    run_root = _claude_run(tmp_path)
    dest = tmp_path / "harvest"
    rc = main(["--run", run_root, "--agent", "claude", "--dest", str(dest)])
    assert rc == 0
    df = (dest / "pallets" / "click" / "Dockerfile").read_text()
    assert df.startswith("FROM python:3.11")
    assert (dest / "pallets" / "click" / "bench_meta.json").is_file()
    assert "1/1 ok" in capsys.readouterr().out


def test_cli_rejects_unknown_agent(tmp_path):
    with pytest.raises(SystemExit):
        main(["--run", str(tmp_path), "--agent", "bogus", "--dest", str(tmp_path / "o")])
