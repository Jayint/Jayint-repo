import json

from src.bench_emit.agents import v3


def _make_v3_repo(tmp_path):
    repo = tmp_path / "output" / "fastapi" / "typer"
    eb = repo / "eval_build"
    eb.mkdir(parents=True)
    (eb / "Dockerfile").write_text(
        "FROM python:3.10-slim\n"
        "RUN git clone --depth=1 https://github.com/fastapi/typer /testbed\n"
        "WORKDIR /testbed\n"
        "COPY setup.sh /tmp/setup.sh\n"
        "RUN bash /tmp/setup.sh\n"
    )
    (eb / "setup.sh").write_text("pip install -e .\n")
    (repo / "_meta.json").write_text(json.dumps(
        {"base_image": "python:3.10-slim", "duration_s": 812.4, "head_sha": "abc123"}))
    return str(repo)


def test_v3_passes_dockerfile_through_and_maps_meta(tmp_path):
    env = v3.adapt(_make_v3_repo(tmp_path))
    assert "git clone --depth=1 https://github.com/fastapi/typer /testbed" in env.dockerfile
    assert env.scripts["setup.sh"] == "pip install -e .\n"
    assert env.meta["agent"] == "v3"
    assert env.meta["base_image"] == "python:3.10-slim"
    assert env.meta["produce_s"] == 812.4
    assert env.meta["head_sha"] == "abc123"
    assert env.meta["dockerfile_source"] == "v3_eval_build"
    assert "tokens_in" not in env.meta


def test_v3_empty_head_sha_is_omitted(tmp_path):
    # Upstream serializes an absent commit as "" -> it must be dropped, not emitted
    # as "head_sha": "" (an empty value is not a real sha).
    repo = tmp_path / "output" / "fastapi" / "typer"
    eb = repo / "eval_build"
    eb.mkdir(parents=True)
    (eb / "Dockerfile").write_text("FROM python:3.10-slim\n")
    (repo / "_meta.json").write_text(json.dumps(
        {"base_image": "python:3.10-slim", "duration_s": 5.0, "head_sha": ""}))
    env = v3.adapt(str(repo))
    assert "head_sha" not in env.meta


def test_v3_missing_eval_build_is_anti_vanish(tmp_path):
    repo = tmp_path / "output" / "o" / "r"
    repo.mkdir(parents=True)
    (repo / "_meta.json").write_text(json.dumps({"base_image": "python:3.11-slim"}))
    env = v3.adapt(str(repo))
    assert env.dockerfile is None
    assert env.meta["agent"] == "v3"
    assert env.meta["base_image"] == "python:3.11-slim"
    assert "produce_s" not in env.meta
