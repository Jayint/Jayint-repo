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


def test_v3_no_dep_graph_omits_provisional(tmp_path):
    # Legacy run (no dep_graph.json in eval_build) -> the key is absent, unchanged.
    env = v3.adapt(_make_v3_repo(tmp_path))
    assert "provisional_installs" not in env.meta


def test_v3_surfaces_provisional_installs_from_dep_graph(tmp_path):
    # Stage C Task 3: a fallthrough (provisional) Package node in the serialized graph
    # must surface as a distinct bench_meta field so an eval never scores a
    # local-collision PyPI install as a clean pass.
    repo = _make_v3_repo(tmp_path)
    dep_graph = {
        "nodes": [
            {"type": "Package", "name": "requests", "data": {}},          # ordinary dep
            {"type": "Package", "name": "azure", "data": {"provisional": {
                "name": "azure", "reason": "local-collision fallthrough",
                "cure_rung": "isolated"}}},
        ],
        "edges": [],
    }
    (tmp_path / "output" / "fastapi" / "typer" / "eval_build" / "dep_graph.json").write_text(
        json.dumps(dep_graph)
    )
    env = v3.adapt(repo)
    assert env.meta["provisional_installs"] == [
        {"name": "azure", "reason": "local-collision fallthrough", "cure_rung": "isolated"}
    ]


class _FakeGraph:
    """Minimal stand-in with a ``to_dict`` so the construction-side ``_write_dep_graph``
    can be exercised without importing ``graph.model`` on the bench_emit test path."""

    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return self._d


def test_construction_only_persists_graph_and_adapter_surfaces_provisional(tmp_path):
    # The --construction-only path (V3_CONSTRUCTION_ONLY, the behavioural-baseline
    # mode) must persist dep_graph.json next to setup.sh via the SAME writer as normal
    # completion, so a provisional fallthrough reaches the run manifest instead of
    # being scored as a clean run. run_v3_e2e self-bootstraps ``src`` on sys.path, so
    # import it lazily here.
    import scripts.run_v3_e2e as runner

    repo = tmp_path / "output" / "o" / "r"
    eb = repo / "eval_build"
    eb.mkdir(parents=True)
    setup = eb / "setup.sh"
    setup.write_text("pip install -e .\n")
    (repo / "_meta.json").write_text(json.dumps({"base_image": "python:3.11-slim"}))
    graph_dict = {
        "nodes": [
            {"type": "Package", "name": "azure", "data": {"provisional": {
                "name": "azure", "reason": "local-collision fallthrough",
                "cure_rung": "isolated"}}},
        ],
        "edges": [],
    }

    runner._write_dep_graph(_FakeGraph(graph_dict), str(setup))   # the construction-only writer

    assert (eb / runner._DEP_GRAPH_FILENAME).is_file()           # dep_graph.json next to setup.sh
    assert runner._DEP_GRAPH_FILENAME == v3._DEP_GRAPH_FILE      # producer/consumer basename contract
    env = v3.adapt(str(repo))
    assert env.meta["provisional_installs"] == [
        {"name": "azure", "reason": "local-collision fallthrough", "cure_rung": "isolated"}
    ]


def test_write_dep_graph_is_noop_when_graph_absent(tmp_path):
    import scripts.run_v3_e2e as runner
    eb = tmp_path / "eval_build"
    eb.mkdir()
    runner._write_dep_graph(None, str(eb / "setup.sh"))          # absent graph -> nothing written
    assert not (eb / runner._DEP_GRAPH_FILENAME).exists()
