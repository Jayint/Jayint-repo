from __future__ import annotations

import json

from src.bench_emit.agents import claude


def _make_claude_repo(tmp_path):
    repo = tmp_path / "output" / "o" / "r"
    eb = repo / "eval_build"
    eb.mkdir(parents=True)
    (eb / "Dockerfile").write_text(
        "FROM python:3.11\n"
        "RUN git clone --depth=1 https://github.com/o/r /testbed\n"
        "WORKDIR /testbed\n"
        "RUN pip install -e .\n"
    )
    # base_image here intentionally differs from the Dockerfile FROM so the test proves
    # the FROM line is authoritative for claude (the _meta value is only a fallback).
    (repo / "_meta.json").write_text(json.dumps({
        "base_image": "python:3.11-slim",
        "duration_s": 421.37,
        "head_sha": "deadbeef",
        "turns": 7,
        "agent_cost_usd": 0.42,
    }))
    return str(repo)


def test_claude_passes_dockerfile_through_and_maps_meta(tmp_path):
    env = claude.adapt(_make_claude_repo(tmp_path))
    # Verbatim passthrough: already /testbed-shaped, no rewriting.
    assert "git clone --depth=1 https://github.com/o/r /testbed" in env.dockerfile
    assert env.dockerfile == (
        "FROM python:3.11\n"
        "RUN git clone --depth=1 https://github.com/o/r /testbed\n"
        "WORKDIR /testbed\n"
        "RUN pip install -e .\n"
    )
    # Self-contained: no sibling scripts.
    assert env.scripts == {}
    assert env.meta["agent"] == "claude"
    # FROM is authoritative for claude (not the _meta base_image).
    assert env.meta["base_image"] == "python:3.11"
    assert env.meta["produce_s"] == 421.37
    assert env.meta["turns_used"] == 7
    assert env.meta["cost_usd"] == 0.42
    assert env.meta["dockerfile_source"] == "claudecode_dockerfile"
    assert "tokens_in" not in env.meta


def test_claude_meta_fallback_keys(tmp_path):
    # turns_used / cost_usd accept the alternate key names when the primary is absent.
    repo = tmp_path / "output" / "o" / "r"
    eb = repo / "eval_build"
    eb.mkdir(parents=True)
    (eb / "Dockerfile").write_text("FROM python:3.11\n")
    (repo / "_meta.json").write_text(json.dumps({
        "duration_s": 5.0, "turns_used": 3, "cost_usd": 1.25,
    }))
    env = claude.adapt(str(repo))
    assert env.meta["turns_used"] == 3
    assert env.meta["cost_usd"] == 1.25


def test_claude_empty_head_sha_is_omitted(tmp_path):
    # An absent commit is serialized upstream as "" -> drop it, never emit "head_sha": "".
    repo = tmp_path / "output" / "o" / "r"
    eb = repo / "eval_build"
    eb.mkdir(parents=True)
    (eb / "Dockerfile").write_text("FROM python:3.11\n")
    (repo / "_meta.json").write_text(json.dumps(
        {"base_image": "python:3.11", "duration_s": 5.0, "head_sha": ""}))
    env = claude.adapt(str(repo))
    assert "head_sha" not in env.meta


def test_claude_missing_eval_build_is_anti_vanish(tmp_path):
    repo = tmp_path / "output" / "o" / "r"
    repo.mkdir(parents=True)
    (repo / "_meta.json").write_text(json.dumps({"base_image": "python:3.11-slim"}))
    env = claude.adapt(str(repo))
    assert env.dockerfile is None
    assert env.scripts == {}
    assert env.meta["agent"] == "claude"
    # With no Dockerfile there is no FROM, so base_image falls back to _meta.
    assert env.meta["base_image"] == "python:3.11-slim"
    assert env.meta["dockerfile_source"] == "claudecode_dockerfile"
    assert "produce_s" not in env.meta
