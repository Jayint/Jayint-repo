import json
import os

import pytest

from src.bench_emit.emit import emit_run


def _v3_run(tmp_path):
    root = tmp_path / "v3run"
    good = root / "output" / "fastapi" / "typer" / "eval_build"
    good.mkdir(parents=True)
    (good / "Dockerfile").write_text(
        "FROM python:3.10-slim\nRUN git clone --depth=1 https://github.com/fastapi/typer /testbed\n")
    (good / "setup.sh").write_text("pip install -e .\n")
    (root / "output" / "fastapi" / "typer" / "_meta.json").write_text(
        json.dumps({"base_image": "python:3.10-slim", "duration_s": 10.0}))
    # anti-vanish: a repo with no eval_build Dockerfile
    missing = root / "output" / "o" / "r"
    missing.mkdir(parents=True)
    (missing / "_meta.json").write_text(json.dumps({"base_image": "python:3.11-slim"}))
    return str(root)


def test_emit_run_writes_tree_and_status(tmp_path):
    run_root = _v3_run(tmp_path)
    dest = tmp_path / "harvest"
    results = emit_run(run_root, "v3", str(dest))
    assert results == [("fastapi/typer", "ok"), ("o/r", "missing")]

    typer = dest / "fastapi" / "typer"
    assert (typer / "Dockerfile").is_file()
    assert (typer / "setup.sh").read_text() == "pip install -e .\n"
    meta = json.loads((typer / "bench_meta.json").read_text())
    assert meta["agent"] == "v3" and meta["produce_s"] == 10.0

    miss = dest / "o" / "r"
    assert (miss / "bench_meta.json").is_file()
    assert not (miss / "Dockerfile").exists()


def test_emit_run_never_mutates_source(tmp_path):
    run_root = _v3_run(tmp_path)
    dest = tmp_path / "harvest"
    emit_run(run_root, "v3", str(dest))
    src_repo = os.path.join(run_root, "output", "fastapi", "typer")
    assert not os.path.exists(os.path.join(src_repo, "bench_meta.json"))
    # v3's Dockerfile lives under eval_build/, never written to the repo root of the source
    assert not os.path.exists(os.path.join(src_repo, "Dockerfile"))


def test_emit_run_unknown_agent_raises(tmp_path):
    with pytest.raises(ValueError):
        emit_run(str(tmp_path), "bogus", str(tmp_path / "out"))


def test_emit_run_adapter_crash_is_visible_not_swallowed(tmp_path, monkeypatch, capsys):
    # A real adapter bug must not masquerade as an expected "no artifact" repo:
    # anti-vanish is preserved, but the failure is loud (stderr) and captured (meta.error).
    run_root = _v3_run(tmp_path)
    dest = tmp_path / "harvest"

    from src.bench_emit.agents import v3

    def _boom(_repo_dir):
        raise RuntimeError("adapter blew up")

    monkeypatch.setattr(v3, "adapt", _boom)
    results = emit_run(run_root, "v3", str(dest))

    # (a) the crashed repo still comes back with status "missing" (batch not aborted)
    assert ("fastapi/typer", "missing") in results
    # (b) a bench_meta.json was still written for it
    meta_path = dest / "fastapi" / "typer" / "bench_meta.json"
    assert meta_path.is_file()
    assert not (dest / "fastapi" / "typer" / "Dockerfile").exists()
    # (c) the error was captured, and (d) surfaced on stderr (not silently swallowed)
    meta = json.loads(meta_path.read_text())
    assert meta["agent"] == "v3"
    assert "adapter blew up" in meta["error"]
    assert "adapter error" in capsys.readouterr().err
