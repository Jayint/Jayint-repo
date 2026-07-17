import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import python_deps.depgraph.advise as advise
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State


def test_classify_hook_invoked_and_graph_returned(monkeypatch):
    base = DepGraph()

    class _FakeScratch:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(advise, "DockerExecutor", lambda *a, **k: _FakeScratch())
    monkeypatch.setattr(advise, "build_dep_graph", lambda *a, **k: base)
    monkeypatch.setattr(advise, "render_dep_graph_advisory", lambda g: "ADV")

    tag = Node(id="service:tagged", type=NodeType.SERVICE, name="tagged", layer=Layer.SERVICES,
               discovered_by=DiscoveredBy.RUNTIME, state=State.MISSING)
    def _classify(graph, repo_path):
        return graph.with_node(tag)

    adv, graph = advise.build_advisory_for_repo("/repo", "python:3.11-slim", classify=_classify)
    assert adv == "ADV"
    assert graph.get("service:tagged") is not None       # classify ran on the built graph


def test_classify_none_is_passthrough(monkeypatch):
    base = DepGraph()
    class _FakeScratch:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(advise, "DockerExecutor", lambda *a, **k: _FakeScratch())
    monkeypatch.setattr(advise, "build_dep_graph", lambda *a, **k: base)
    monkeypatch.setattr(advise, "render_dep_graph_advisory", lambda g: "ADV")
    adv, graph = advise.build_advisory_for_repo("/repo", "python:3.11-slim")   # classify defaults None
    assert graph is base                                  # unchanged


def test_needed_extras_forwarded_to_build(monkeypatch):
    base = DepGraph()
    seen = {}

    class _FakeScratch:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _build(*args, **kwargs):
        seen["needed_extras"] = kwargs.get("needed_extras")
        return base

    monkeypatch.setattr(advise, "DockerExecutor", lambda *a, **k: _FakeScratch())
    monkeypatch.setattr(advise, "build_dep_graph", _build)
    monkeypatch.setattr(advise, "render_dep_graph_advisory", lambda g: "ADV")

    advise.build_advisory_for_repo(
        "/repo", "python:3.11-slim", needed_extras=frozenset({"test", "dev"})
    )
    assert seen["needed_extras"] == frozenset({"test", "dev"})


def test_platform_and_stable_image_forwarded_to_scratch_executor(monkeypatch):
    seen = {}

    class _FakeScratch:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _executor(image, **kwargs):
        seen["image"] = image
        seen["platform"] = kwargs.get("platform")
        return _FakeScratch()

    monkeypatch.setattr(advise, "DockerExecutor", _executor)
    monkeypatch.setattr(advise, "build_dep_graph", lambda *a, **k: DepGraph())
    monkeypatch.setattr(advise, "render_dep_graph_advisory", lambda _g: "")

    advise.build_advisory_for_repo(
        "/repo", "sha256:arm64-image", platform="linux/arm64"
    )

    assert seen == {"image": "sha256:arm64-image", "platform": "linux/arm64"}
