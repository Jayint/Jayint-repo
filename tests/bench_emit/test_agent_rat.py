import json

from src.bench_emit.agents import rat


def _make_rat(tmp_path, environment, owner="fastapi", name="typer", cost=None, provenance=None):
    repo = tmp_path / "output" / owner / name
    repo.mkdir(parents=True)
    cs = {"environment": environment}
    if cost is not None:
        cs["cost"] = cost
    if provenance is not None:
        cs["provenance"] = provenance
    (repo / "case_study.json").write_text(json.dumps(cs))
    return str(repo)


def test_rat_reconstructs_dockerfile_from_recipe(tmp_path):
    d = _make_rat(
        tmp_path,
        {"base_image": "python:3.10-slim",
         "recipe_commands": ["apt-get update", "pip install -e ."]},
        cost={"prompt_tokens": 1200, "completion_tokens": 800},
        provenance={"start_ts": 100.0, "end_ts": 350.5},
    )
    env = rat.adapt(d)
    df = env.dockerfile
    assert df.startswith("FROM python:3.10-slim")
    assert "RUN git clone --depth=1 https://github.com/fastapi/typer /repo" in df
    assert "RUN apt-get update" in df
    # Standalone-line check: proves the recipe entry became its own RUN, and can't
    # collide with the `RUN apt-get update && ... git ...` clone boilerplate line.
    assert "RUN pip install -e ." in df.splitlines()
    assert df.rstrip().endswith("RUN ln -sfn /repo /testbed")
    assert env.meta["agent"] == "rat"
    assert env.meta["base_image"] == "python:3.10-slim"
    assert env.meta["tokens_in"] == 1200 and env.meta["tokens_out"] == 800
    assert env.meta["produce_s"] == 250.5
    assert env.meta["dockerfile_source"] == "rat_reconstructed"


def test_rat_node_misrouting_rendered_faithfully(tmp_path):
    d = _make_rat(tmp_path, {"base_image": "node:18-slim", "recipe_commands": ["npm ci"]},
                  owner="expressjs", name="express")
    env = rat.adapt(d)
    assert env.dockerfile.startswith("FROM node:18-slim")
    assert "RUN git clone --depth=1 https://github.com/expressjs/express /repo" in env.dockerfile
    assert env.meta["base_image"] == "node:18-slim"


def test_rat_cost_and_provenance_absent_omitted(tmp_path):
    # Non-empty recipe so the render+meta path runs (an empty recipe short-circuits
    # to dockerfile=None before cost/provenance are read).
    d = _make_rat(tmp_path, {"base_image": "python:3.10-slim",
                             "recipe_commands": ["pip install -e ."]})
    env = rat.adapt(d)
    assert env.dockerfile is not None
    assert "tokens_in" not in env.meta and "tokens_out" not in env.meta
    assert "produce_s" not in env.meta


def test_rat_empty_recipe_is_missing(tmp_path):
    # Empty native artifact -> EmittedEnv(dockerfile=None) so the anti-vanish "missing"
    # row records what rat targeted (spec §9).
    d = _make_rat(tmp_path, {"base_image": "python:3.11-slim", "recipe_commands": []})
    env = rat.adapt(d)
    assert env.dockerfile is None
    assert env.meta["agent"] == "rat"
    assert env.meta.get("base_image") == "python:3.11-slim"


def test_rat_malformed_case_study_is_missing(tmp_path):
    repo = tmp_path / "output" / "o" / "r"
    repo.mkdir(parents=True)
    (repo / "case_study.json").write_text("{ not valid json")
    env = rat.adapt(str(repo))
    assert env.dockerfile is None
    assert env.meta["agent"] == "rat"
