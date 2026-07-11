import json

from src.bench_emit.agents import repo2run


def _make_repo2run(tmp_path):
    repo = tmp_path / "output" / "psf" / "requests"
    repo.mkdir(parents=True)
    (repo / "Dockerfile").write_text(
        "FROM python:3.9-slim\n"
        "RUN git clone --depth=1 https://github.com/psf/requests /repo\n"
        "WORKDIR /repo\n"
        "RUN pip install -e .\n"
    )
    (repo / "_meta.json").write_text(json.dumps({"duration_s": 240.0}))
    return str(repo)


def test_repo2run_appends_testbed_link_and_parses_base(tmp_path):
    env = repo2run.adapt(_make_repo2run(tmp_path))
    assert env.dockerfile.rstrip().endswith("RUN ln -sfn /repo /testbed")
    assert env.meta["agent"] == "repo2run"
    assert env.meta["base_image"] == "python:3.9-slim"
    assert env.meta["produce_s"] == 240.0
    assert env.meta["dockerfile_source"] == "repo2run_normalized"


def test_repo2run_missing_dockerfile_is_anti_vanish(tmp_path):
    repo = tmp_path / "output" / "o" / "r"
    repo.mkdir(parents=True)
    env = repo2run.adapt(str(repo))
    assert env.dockerfile is None
    assert env.meta["agent"] == "repo2run"
    assert "base_image" not in env.meta
