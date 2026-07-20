"""THE FLIP — spine shape on flat/src/namespace layouts, and the hard guarantee
that a first-party name never reaches the Phase-A dist-guesser.

The goal spine is ``project -> module -> import -> {module|package}``. These tests
pin its SHAPE across the three layout classes the classifier must top, and prove
end-to-end that the route-not-drop flip keeps a collision name out of the repair
bound (it would otherwise install a wrong PyPI namesake).
"""
import json

from conftest import SequencedFakeExecutor  # type: ignore

from graph.contracts.executor import CommandResult
from graph.core.orchestrate import build_dep_graph
from graph.model import EdgeType, NodeType, import_id, project_id
from graph.python.read.scan import scan_to_nodes
from graph.python.route.classify import classify, module_id, wire_spine, apply_routing
from graph.python.skeleton import _add_project_node


def _spine(repo):
    """scan -> route -> add_project -> wire_spine, returning the finalized graph."""
    stdlib = frozenset({"os", "sys"})
    routing = classify(repo, target_stdlib=stdlib, declared=frozenset())
    graph = apply_routing(scan_to_nodes(repo), routing)
    graph = _add_project_node(graph, repo)
    return wire_spine(graph, routing), routing


def _requires(graph):
    return {(e.src, e.dst, e.origin) for e in graph.edges if e.relation is EdgeType.REQUIRES}


def test_spine_shape_flat_layout(tmp_path):
    """Flat: a root-level module (``app.py``) is a top-level first-party module even
    though it is never itself imported — it must anchor its external imports."""
    (tmp_path / "app.py").write_text("import requests\nimport os\n")
    graph, _routing = _spine(str(tmp_path))
    pid = project_id(tmp_path.name)
    req = _requires(graph)
    assert graph.get(module_id("app")) is not None
    assert (pid, module_id("app"), "contains") in req
    assert (module_id("app"), import_id("requests"), "imports") in req
    # os is stdlib -> no import node, no spine edge.
    assert graph.get(import_id("os")) is None


def test_spine_shape_src_layout(tmp_path):
    """Src-layout: ``src/mypkg/core.py`` -> top-level module ``mypkg`` (the src dir
    is the sys.path root, not a module segment)."""
    (tmp_path / "src" / "mypkg").mkdir(parents=True)
    (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "mypkg" / "core.py").write_text("import requests\n")
    graph, _routing = _spine(str(tmp_path))
    pid = project_id(tmp_path.name)
    req = _requires(graph)
    assert graph.get(module_id("mypkg")) is not None
    assert graph.get(module_id("core")) is None          # NOT a top-level (mypkg.core)
    assert (pid, module_id("mypkg"), "contains") in req
    assert (module_id("mypkg"), import_id("requests"), "imports") in req


def test_spine_shape_namespace_routes_to_collision_not_module(tmp_path):
    """PEP 420 namespace (``src/mycompany/pkga`` with no ``mycompany/__init__.py``):
    the naive climb mints ``pkga``, but it is namespace-suspect -> routed to the
    collision zone (deferred), NOT minted as a trusted first-party Module. So there
    is NO ``module:pkga`` and NO ``project->module`` edge for it."""
    pkga = tmp_path / "src" / "mycompany" / "pkga"
    pkga.mkdir(parents=True)
    (pkga / "__init__.py").write_text("")
    # `import pkga.mod` makes `pkga` a scanned finding so the namespace-suspect
    # ladder can route it; the naive climb mints `pkga` as a false top-level.
    (pkga / "mod.py").write_text("import requests\nimport pkga.mod\n")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='ns'\nversion='0'\n"
        "[tool.setuptools.packages.find]\nwhere=['src']\n"
    )
    graph, routing = _spine(str(tmp_path))
    assert "pkga" in routing.deferred                    # namespace-suspect -> collision zone
    assert "pkga" not in {t for t, _ in routing.internal}  # never a trusted top-level
    assert graph.get(module_id("pkga")) is None          # NOT a trusted Module node
    assert not any(
        e.dst == module_id("pkga") for e in graph.edges
    )


def _seq_exec():
    def _r(rc=0, stdout="", stderr=""):
        return CommandResult(command="", returncode=rc, stdout=stdout, stderr=stderr)
    return SequencedFakeExecutor(
        responses={"stdlib_module_names": [_r(0, stdout=json.dumps(["os", "sys"]))]},
        default=_r(0),
    )


def test_first_party_names_never_reach_the_dist_guesser(tmp_path, monkeypatch):
    """THE hard guarantee: a collision name (``items``, a repo stem that is ALSO a
    real PyPI dist) minted as an Import node by the route-not-drop scan must NEVER
    reach Phase-A's candidate generation — otherwise the repair ladder installs the
    wrong PyPI package. A genuine external (``requests``) MUST reach it (proving the
    spy is live and the assertion non-vacuous)."""
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "app.py").write_text("import requests\nimport items\n")
    (tmp_path / "mypkg" / "tutorial001").mkdir()
    (tmp_path / "mypkg" / "tutorial001" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "tutorial001" / "items.py").write_text("")

    seen: list[str] = []
    import graph.python.fixpoint as fx
    real = fx.generate_candidates

    def _spy(import_name, *a, **k):
        seen.append(import_name)
        return real(import_name, *a, **k)

    monkeypatch.setattr(fx, "generate_candidates", _spy)

    ex = _seq_exec()
    build_dep_graph(str(tmp_path), ex, host_executor=ex)

    assert "requests" in seen, "external import must reach candidate generation (non-vacuous)"
    assert "items" not in seen, "collision name reached the dist-guesser — wrong-install vector re-opened"
    assert "mypkg" not in seen, "first-party module name reached the dist-guesser"


def test_host_only_stdlib_name_routes_external_not_dropped(tmp_path):
    """F4/§17: a name in the HOST stdlib but NOT the TARGET stdlib is a real external
    for the target and must route external — NEVER silently dropped by a host backstop.
    ``graphlib`` is host-stdlib (3.9+) but absent from a target that lacks it."""
    import sys as _sys
    host_stdlib = getattr(_sys, "stdlib_module_names", frozenset())
    assert "graphlib" in host_stdlib   # premise: the runner's host has graphlib
    (tmp_path / "app.py").write_text("import graphlib\nimport requests\n")

    from graph.python.route.classify import classify
    routing = classify(str(tmp_path), target_stdlib=frozenset({"os"}), declared=frozenset())
    assert "graphlib" in routing.external   # host-only-stdlib -> real target external
    assert "requests" in routing.external


def test_first_party_top_colliding_with_host_stdlib_routes_internal(tmp_path):
    """F4: a repo module whose name collides with a HOST-stdlib name (and which
    ``scan_imports`` therefore mis-classifies stdlib because it is not a plain
    root/src child) still routes INTERNAL — the sys.path-accurate tops rung catches it
    once the forbidden host backstop is gone. ``pkgs/graphlib`` is a repo top-level."""
    (tmp_path / "pkgs" / "graphlib").mkdir(parents=True)
    (tmp_path / "pkgs" / "graphlib" / "__init__.py").write_text("")
    (tmp_path / "app.py").write_text("import graphlib\n")

    from graph.python.read.repo_modules import top_level_names
    assert "graphlib" in top_level_names(str(tmp_path))   # premise: a real repo top

    from graph.python.route.classify import classify
    routing = classify(str(tmp_path), target_stdlib=frozenset({"os"}), declared=frozenset())
    assert "graphlib" in {t for t, _ in routing.internal}  # internal, NOT dropped
    assert "graphlib" not in routing.external


def test_probe_returns_none_when_unavailable():
    """F4: ``probe_target_stdlib`` returns None (not an empty frozenset) when the
    target cannot answer, so the caller can distinguish 'unavailable' and fail CLOSED
    instead of proceeding with an empty stdlib set."""
    from graph.python.route.classify import probe_target_stdlib

    class _NotOk:
        def run(self, *_a, **_k):
            class R:
                returncode, stdout, stderr = 1, "", "boom"
                @property
                def ok(self): return False
            return R()

    assert probe_target_stdlib(_NotOk()) is None


def test_unavailable_target_stdlib_fails_closed(tmp_path):
    """F4: when the stdlib probe is unavailable (returns non-ok, so None), the lane
    fails CLOSED — first-party/collision names are dropped, ``routing=None`` — rather
    than classifying against an empty stdlib set (which would misroute)."""
    from graph.python.pipeline import _route_config_lane
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "app.py").write_text("import requests\nimport items\n")
    (tmp_path / "mypkg" / "tutorial001").mkdir()
    (tmp_path / "mypkg" / "tutorial001" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "tutorial001" / "items.py").write_text("")

    class _NoStdlib:
        def run(self, *_a, **_k):   # never answers the stdlib probe
            class R:
                returncode, stdout, stderr = 1, "", ""
                @property
                def ok(self): return False
            return R()

    graph = scan_to_nodes(str(tmp_path))
    out, routing = _route_config_lane(graph, str(tmp_path), _NoStdlib(), declared=frozenset())
    names = {n.name for n in out.nodes if n.type is NodeType.IMPORT}
    assert routing is None                       # unavailable -> fail closed
    assert "requests" in names                   # clear-external kept
    assert "items" not in names and "mypkg" not in names  # first-party/collision dropped


def test_internal_cross_module_import_gets_a_resolves_edge(tmp_path):
    """F3: an internal import of ANOTHER module draws ``import(X) --resolves--> module(X)``
    (a non-self resolution edge). ``foo`` imports ``bar`` (both repo top-levels)."""
    (tmp_path / "bar.py").write_text("VALUE = 1\n")
    (tmp_path / "foo.py").write_text("import bar\n")
    graph, routing = _spine(str(tmp_path))
    req = _requires(graph)
    assert "bar" in routing.resolution
    assert (module_id("foo"), import_id("bar"), "imports") in req     # foo imports bar
    assert (import_id("bar"), module_id("bar"), "resolves") in req    # bar resolves to module(bar)


def test_relink_skips_module_routed_imports(tmp_path):
    """F3: relink must NOT draw an Import->Package edge for an import stamped
    ``routed_provider='module'`` — it is provided by a local Module, not a PyPI dist,
    so a same-named wheel must never be laundered onto it."""
    from graph.model import (DepGraph, DiscoveredBy, Layer, Node, NodeType,
                             package_id)
    from graph.python.lanes.install.link import import_to_package_edges

    imp = Node(id=import_id("items"), type=NodeType.IMPORT, name="items",
               layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN
               ).with_data(routed_provider="module")
    pkg = Node(id=package_id("items", "1.0"), type=NodeType.PACKAGE, name="items",
               version="1.0", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER)
    g = DepGraph().with_node(imp).with_node(pkg)
    edges = import_to_package_edges(g, {"items": ["items"]})
    assert edges == []   # module-routed import: no Import->Package edge drawn

    # control: an ordinary (non-module-routed) import DOES get the edge.
    imp2 = Node(id=import_id("requests"), type=NodeType.IMPORT, name="requests",
                layer=Layer.NAMING, discovered_by=DiscoveredBy.STATIC_SCAN)
    pkg2 = Node(id=package_id("requests", "2.0"), type=NodeType.PACKAGE, name="requests",
                version="2.0", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER)
    g2 = DepGraph().with_node(imp2).with_node(pkg2)
    assert len(import_to_package_edges(g2, {"requests": ["requests"]})) == 1


def test_classify_failure_fails_closed_end_to_end(tmp_path, monkeypatch):
    """F1: post-flip the classifier is load-bearing for SAFETY. If it RAISES,
    construction must fail CLOSED — the collision name (``items``) must NOT reach the
    Phase-A missing set / dist-guesser, and the Project node must carry
    ``routing_failed=True`` for attribution. (A fail-OPEN here would erase routing
    protection and install the wrong PyPI namesake — the false-green vector.)"""
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "app.py").write_text("import requests\nimport items\n")
    (tmp_path / "mypkg" / "tutorial001").mkdir()
    (tmp_path / "mypkg" / "tutorial001" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "tutorial001" / "items.py").write_text("")

    # Force classification to blow up AFTER scan minted the raw first-party nodes.
    import graph.python.pipeline as pl

    def _boom(*_a, **_k):
        raise RuntimeError("classify exploded")

    monkeypatch.setattr(pl, "classify", _boom)

    seen: list[str] = []
    import graph.python.fixpoint as fx
    real = fx.generate_candidates
    monkeypatch.setattr(fx, "generate_candidates",
                        lambda name, *a, **k: (seen.append(name), real(name, *a, **k))[1])

    ex = _seq_exec()
    graph = build_dep_graph(str(tmp_path), ex, host_executor=ex)

    assert "items" not in seen, "fail-OPEN hole: collision name reached the dist-guesser"
    assert "mypkg" not in seen, "fail-OPEN hole: first-party name reached the dist-guesser"
    assert "requests" in seen, "clear-external must still reach the guesser (non-vacuous)"
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert proj.data.get("routing_failed") is True, "failed lane must be attributable"
    assert graph.get(module_id("mypkg")) is None, "fail-closed drops first-party Module nodes"


def test_construction_assertion_trips_on_a_causal_recipe():
    """The construction-time invariant guard must be able to FAIL: a MODULE/IMPORT
    node that somehow carried install commands is a violation. (A guard that can
    never fail is worthless — same discipline as the boundary tripwire.)"""
    import pytest
    from graph.model import DepGraph, DiscoveredBy, Layer, Node, NodeType
    from graph.python.pipeline import _assert_no_causal_recipe

    clean = Node(id=module_id("app"), type=NodeType.MODULE, name="app",
                 layer=Layer.NAMING, discovered_by=DiscoveredBy.CLASSIFIER)
    _assert_no_causal_recipe(DepGraph().with_node(clean))   # no recipe -> passes

    offender = clean.with_data()  # start from clean, add a recipe
    offender = Node(id=module_id("app"), type=NodeType.MODULE, name="app",
                    layer=Layer.NAMING, discovered_by=DiscoveredBy.CLASSIFIER,
                    setup_commands=("pip install app",))
    with pytest.raises(AssertionError):
        _assert_no_causal_recipe(DepGraph().with_node(offender))
