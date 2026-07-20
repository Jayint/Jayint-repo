"""Stage C Task 3 — collision-zone arbitration runs LIVE after the cure, and
genuine fallthroughs re-enter the install lane under a provisional flag.

The arbiter (:func:`graph.python.route.arbitrate.arbitrate`) is cure-gated and
exception-aware; this task WIRES it into construction: reading Task 1's
``routing_deferred`` stamp, reconstructing Task 2's cure outcome, recording the
three verdict partitions as Project-node graph data, and re-entering the Phase-A
fixpoint (threaded ``resume_pkg_ids`` -> no duplicate pkg nodes) for every
fallthrough name, each flagged ``data["provisional"]``.

Pre-flip reality (route-not-drop is Task 4): the scan still DROPS first-party/
local names, so collision names have NO Import nodes today -- the ``resolves_local``
verdict is recorded but has no Import node to route, and the relink guard is a
no-op until the flip. Only the fallthrough install-lane re-entry has a live effect.
"""

from __future__ import annotations

import json

from conftest import SequencedFakeExecutor  # type: ignore

from graph.contracts.executor import CommandResult
from graph.core.orchestrate import build_dep_graph
from graph.model import (
    DepGraph,
    DiscoveredBy,
    Layer,
    Node,
    NodeType,
    State,
    import_id,
    package_id,
)
from graph.python.pipeline import (
    _apply_live_arbitration,
    _cure_from_project,
    _stamp_provisional,
)
from graph.python.lanes.config.cure import CureResult
from graph.python.lanes.install.link import import_to_package_edges


def _r(returncode=0, stdout="", stderr=""):
    return CommandResult(command="", returncode=returncode, stdout=stdout, stderr=stderr)


def _project(**data) -> Node:
    return Node(
        id="project:p", type=NodeType.PROJECT, name="p", layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN, data=data,
    )


def _pkg(name, version="1.0", **data) -> Node:
    return Node(
        id=package_id(name, version), type=NodeType.PACKAGE, name=name, layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER, version=version, data=data,
    )


def _imp(name) -> Node:
    return Node(
        id=import_id(name), type=NodeType.IMPORT, name=name, layer=Layer.NAMING,
        discovered_by=DiscoveredBy.STATIC_SCAN,
    )


class _RecordingExec:
    """A container executor for ``arbitrate``'s per-name ``python3 -c 'import X'``
    probe: canned by (substring, rc, stderr); records every command in ``.calls``."""

    def __init__(self, table=None, mount="/workspace/repo"):
        self.repo_mount_dir = mount
        self.table = table or []
        self.calls: list[str] = []

    def run(self, cmd, *, timeout=300):
        self.calls.append(cmd)
        for key, rc, err in self.table:
            if key in cmd:
                return CommandResult(command=cmd, returncode=rc, stdout="", stderr=err)
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="")


class _ReenterSpy:
    """Stands in for the Phase-A fixpoint re-entry: records the extra roots and the
    threaded ``resume_pkg_ids``, and mints one Package node per fallthrough root."""

    def __init__(self):
        self.calls: list[tuple[list, set]] = []

    def __call__(self, graph, extra_roots, resume_pkg_ids):
        self.calls.append((list(extra_roots), set(resume_pkg_ids)))
        new = graph
        for _imp_id, dist in extra_roots:
            new = new.with_node(_pkg(dist))
        return new


# --- _apply_live_arbitration: verdict classes ------------------------------- #


def test_empty_deferred_makes_zero_probes(tmp_path):
    # The common case: no collision zone -> return immediately, ZERO container calls.
    ex = _RecordingExec()
    spy = _ReenterSpy()
    graph = DepGraph().with_node(_project())        # no routing_deferred stamped
    out = _apply_live_arbitration(graph, str(tmp_path), ex, reenter=spy)
    assert out is graph          # untouched
    assert ex.calls == []        # zero probes -> zero extra container calls
    assert spy.calls == []       # no arbitration, no re-entry


def test_cure_fail_all_unresolved_zero_installs(tmp_path):
    # Deferred names present but the project was never scratch-certified (cure
    # failed/absent) -> arbitrate short-circuits: everything unresolved, ZERO probes,
    # ZERO installs, no re-entry (the fable §1 blocker).
    ex = _RecordingExec()
    spy = _ReenterSpy()
    graph = DepGraph().with_node(_project(routing_deferred=("a", "b")))
    out = _apply_live_arbitration(graph, str(tmp_path), ex, reenter=spy)
    proj = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    assert proj.data["routing_unresolved"] == ("a", "b")
    assert proj.data["routing_fallthrough"] == ()
    assert proj.data["routing_arbitrated_local"] == ()
    assert ex.calls == []        # cure-fail -> no probe ever issued
    assert spy.calls == []       # no fallthrough -> no install-lane re-entry


def test_local_verdicts_recorded_no_reentry(tmp_path):
    # Cure ok + every deferred name imports cleanly under the plan -> all local.
    ex = _RecordingExec()        # default rc0 -> "import X" succeeds -> local
    spy = _ReenterSpy()
    graph = DepGraph().with_node(
        _project(routing_deferred=("items", "azure"), scratch_certified=True,
                 cure_rung="isolated", cure_collect_ok=True)
    )
    out = _apply_live_arbitration(graph, str(tmp_path), ex, reenter=spy)
    proj = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    assert proj.data["routing_arbitrated_local"] == ("azure", "items")   # sorted tuple
    assert proj.data["routing_fallthrough"] == ()
    assert proj.data["routing_unresolved"] == ()
    assert spy.calls == []                                    # local -> no re-entry
    assert any("import items" in c for c in ex.calls)         # it DID probe (cure ok)


def test_fallthrough_mints_provisional_root_and_threads_resume(tmp_path):
    # azure genuinely absent under the cure -> fallthrough -> install-lane re-entry.
    ex = _RecordingExec(
        table=[("import azure", 1, "ModuleNotFoundError: No module named 'azure'")]
    )
    spy = _ReenterSpy()
    existing = _pkg("requests", "2.0")
    graph = (
        DepGraph()
        .with_node(_project(routing_deferred=("azure",), scratch_certified=True,
                            cure_rung="isolated", cure_collect_ok=True))
        .with_node(existing)
    )
    out = _apply_live_arbitration(graph, str(tmp_path), ex, reenter=spy)
    proj = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    assert proj.data["routing_fallthrough"] == ("azure",)
    # re-entry invoked with the fallthrough install-lane root AND the current
    # Package-node ids threaded in (resume state -> no duplicate pkg nodes).
    assert spy.calls == [([(None, "azure")], {existing.id})]
    az = out.get(package_id("azure", "1.0"))
    assert az is not None
    assert az.data["provisional"]["name"] == "azure"
    assert az.data["provisional"]["cure_rung"] == "isolated"
    assert "fallthrough" in az.data["provisional"]["reason"]
    # No duplicate Package nodes: only the pre-existing one + the minted fallthrough.
    pkg_ids = sorted(n.id for n in out.nodes if n.type is NodeType.PACKAGE)
    assert pkg_ids == sorted({existing.id, package_id("azure", "1.0")})


def test_arbitration_is_fail_open_on_executor_error(tmp_path):
    class _Boom:
        repo_mount_dir = "/workspace/repo"
        def run(self, *_a, **_k):
            raise RuntimeError("container gone")

    def _boom_reenter(*_a, **_k):
        raise AssertionError("re-entry must not run when arbitration failed")

    graph = DepGraph().with_node(_project(routing_deferred=("azure",), scratch_certified=True))
    out = _apply_live_arbitration(graph, str(tmp_path), _Boom(), reenter=_boom_reenter)
    assert out is graph          # any exception swallowed; construction untouched


# --- _cure_from_project: reconstruct only what arbitrate reads --------------- #


def test_cure_from_project_reconstructs_ok_from_stamp():
    proj = _project(scratch_certified=True, cure_rung="no_build_isolation", cure_collect_ok=True)
    cure = _cure_from_project(proj)
    assert cure.ok is True and cure.rung == "no_build_isolation" and cure.collect_ok is True


def test_cure_from_project_unstamped_is_not_ok():
    assert _cure_from_project(_project()).ok is False              # never cured
    assert _cure_from_project(_project(scratch_certified=False)).ok is False


# --- provisional flag survives serialization -------------------------------- #


def test_provisional_survives_serialization_round_trip():
    node = _pkg("util", provisional={"name": "util", "reason": "x", "cure_rung": "isolated"})
    back = Node.from_dict(json.loads(json.dumps(node.to_dict())))
    assert back.data["provisional"] == {"name": "util", "reason": "x", "cure_rung": "isolated"}


def test_stamp_provisional_only_flags_matching_pkg_by_canon():
    # A transitive dep in the closure must NOT be flagged -- only the fallthrough
    # root itself (matched by canonical dist name).
    graph = DepGraph().with_node(_pkg("Azure")).with_node(_pkg("transitive-dep"))
    out = _stamp_provisional(graph, frozenset({"azure"}), CureResult(True, "isolated", True, ""))
    assert out.get(package_id("Azure", "1.0")).data.get("provisional") is not None  # canon match
    assert out.get(package_id("transitive-dep", "1.0")).data.get("provisional") is None


# --- Phase-A fixpoint resume threading (no duplicate pkg nodes) -------------- #


def _fixpoint_env(monkeypatch, new_pkg):
    import graph.python.fixpoint as fx
    monkeypatch.setattr(fx, "resolve_closure", lambda *a, **k: ([new_pkg], []))
    monkeypatch.setattr(fx, "install_closure", lambda g, ex: g)
    monkeypatch.setattr(fx, "resolved_record_coverage", lambda nodes, prov: frozenset())
    return fx


def test_fixpoint_resume_drops_stale_no_duplicate(monkeypatch):
    old = _pkg("foo", "1.0")
    new = _pkg("foo", "2.0")
    fx = _fixpoint_env(monkeypatch, new)
    ex = object()
    out = fx._phase_a_fixpoint(
        DepGraph().with_node(old), [(None, "foo")], ex, ex, (lambda d: None),
        target_env=None, exclude_newer=None, needed_extras=frozenset(),
        resume_pkg_ids=frozenset({old.id}),
    )
    assert out.get(package_id("foo", "2.0")) is not None
    assert out.get(package_id("foo", "1.0")) is None        # stale dropped via resume


def test_fixpoint_without_resume_keeps_from_scratch_behavior(monkeypatch):
    # The load-bearing counter-proof: WITHOUT threaded resume state, the empty
    # prev-set drops nothing, so a fresh unthreaded re-entry would leave BOTH the
    # old and new pkg node -- exactly the duplicate the resume param prevents.
    old = _pkg("foo", "1.0")
    new = _pkg("foo", "2.0")
    fx = _fixpoint_env(monkeypatch, new)
    ex = object()
    out = fx._phase_a_fixpoint(
        DepGraph().with_node(old), [(None, "foo")], ex, ex, (lambda d: None),
        target_env=None, exclude_newer=None, needed_extras=frozenset(),
    )
    assert out.get(package_id("foo", "1.0")) is not None    # from-scratch: not dropped
    assert out.get(package_id("foo", "2.0")) is not None


# --- relink guard: never launder a provisional collision -------------------- #


def test_relink_skips_module_routed_collision():
    proj = _project(routing_arbitrated_local=("util",), routing_fallthrough=())
    graph = DepGraph().with_node(proj).with_node(_imp("util")).with_node(_pkg("util"))
    edges = import_to_package_edges(graph, {"util": ["util"]})
    assert edges == []          # module-routed collision: relink refuses to launder a PyPI dist


def test_relink_keeps_fallthrough_edge_and_preserves_provisional():
    proj = _project(routing_arbitrated_local=(), routing_fallthrough=("util",))
    pkg = _pkg("util", provisional={"name": "util", "reason": "x", "cure_rung": "isolated"})
    graph = DepGraph().with_node(proj).with_node(_imp("util")).with_node(pkg)
    edges = import_to_package_edges(graph, {"util": ["util"]})
    assert len(edges) == 1 and edges[0].src == import_id("util") and edges[0].dst == pkg.id
    assert graph.get(pkg.id).data["provisional"]["name"] == "util"   # marker untouched


def test_relink_byte_identical_without_arbitration_stamps():
    # No Project arbitration data -> the guard is a hard no-op; edge drawn as today.
    graph = DepGraph().with_node(_imp("requests")).with_node(_pkg("requests", "2.0"))
    edges = import_to_package_edges(graph, {"requests": ["requests"]})
    assert len(edges) == 1


# --- end-to-end: arbitration wired live into build_dep_graph ---------------- #


def _collision_repo(tmp_path):
    """A minimal INSTALLABLE repo carrying a real stem collision: ``azure`` is a
    broad-walk name (``mypkg/azure.py``) that is NOT an importable top-level, so
    ``classify`` routes the ``import azure`` finding to the deferred collision zone."""
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\n[project]\nname='mypkg'\nversion='0.0.0'\n"
    )
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "azure.py").write_text("")
    (tmp_path / "mypkg" / "uses.py").write_text("import azure\n")
    return str(tmp_path)


def test_arbitration_wired_into_build_dep_graph_local_verdict(tmp_path):
    """Full construction over the collision fixture: classify -> cure -> arbitrate
    all run live. The all-rc0 stub cures the project and every probe imports
    cleanly, so ``azure`` is recorded as a LOCAL arbitration verdict (no install)."""
    repo = _collision_repo(tmp_path)
    ex = SequencedFakeExecutor(
        responses={"stdlib_module_names": [_r(0, stdout=json.dumps(["os", "sys"]))]},
        default=_r(0),
    )
    graph = build_dep_graph(repo, ex, host_executor=ex)
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert "azure" in proj.data.get("routing_deferred", ())          # Task 1 stamp
    assert proj.data.get("routing_arbitrated_local") == ("azure",)   # probe rc0 -> local
    assert proj.data.get("routing_fallthrough") == ()


def test_no_collision_repo_records_empty_arbitration(tmp_path):
    """A repo with no collision zone: arbitration is a no-op -- deferred empty, so
    no verdict tuples are stamped and construction is unaffected."""
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\n[project]\nname='clean'\nversion='0.0.0'\n"
    )
    (tmp_path / "clean").mkdir()
    (tmp_path / "clean" / "__init__.py").write_text("")
    ex = SequencedFakeExecutor(
        responses={"stdlib_module_names": [_r(0, stdout=json.dumps(["os", "sys"]))]},
        default=_r(0),
    )
    graph = build_dep_graph(str(tmp_path), ex, host_executor=ex)
    proj = next(n for n in graph.nodes if n.type is NodeType.PROJECT)
    assert proj.data.get("routing_deferred", ()) == ()
    assert "routing_fallthrough" not in proj.data      # arbitration skipped -> no stamp
