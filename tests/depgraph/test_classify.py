from python_deps.depgraph.classify import classify
from python_deps.depgraph.repo_modules import stem_collisions
from python_deps.depgraph.schema import NodeType


def test_ladder_partitions_internal_external_collision(tmp_path):
    # A local package `mypkg` (sys.path-accurate top-level), a clear external
    # `requests`, and a collision `items` that is BOTH a repo stem AND a real
    # PyPI dist. To be a genuine collision it must be a broad-walk stem whose
    # importable top-level is something else: `mypkg/tutorial001/items.py`
    # harvests the stem "items" but its dotted name is "mypkg.tutorial001.items"
    # (top-level "mypkg"), so "items" lands in `stem_collisions`, not
    # `top_level_names`. (A root-level `items.py` would instead BE a top-level
    # named "items" and wrongly route internal.)
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "app.py").write_text(
        "import requests\nimport items\nfrom mypkg import helpers\n"
    )
    (tmp_path / "mypkg" / "helpers.py").write_text("")
    (tmp_path / "mypkg" / "tutorial001").mkdir()
    (tmp_path / "mypkg" / "tutorial001" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "tutorial001" / "items.py").write_text("")

    # Guard: the collision assertion below is only meaningful if "items" really
    # is in the collision zone (a broad-walk stem that is not a top-level).
    assert "items" in frozenset(stem_collisions(str(tmp_path)))

    routing = classify(str(tmp_path), target_stdlib=frozenset({"os", "sys"}), declared=frozenset())
    internal_names = {name for name, _dotted in routing.internal}
    assert "mypkg" in internal_names            # sys.path-accurate top-level → internal
    assert "requests" in routing.external       # not local, not stdlib → external
    assert "items" in routing.deferred          # repo stem AND PyPI dist → deferred
    assert {n.type for n in routing.modules} == {NodeType.MODULE}


def test_declared_name_never_internal(tmp_path):
    # A repo package `requests` shadowing a declared dep name, actually imported
    # somewhere so a finding exists (an unused directory yields NO finding and
    # nothing to route). Because `requests` is a repo top-level, the tops rung
    # (rung 3) would route it internal — the declared rung (rung 1, checked
    # first) is what flips it to external. That flip is the invariant under test.
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / "__init__.py").write_text("")
    (tmp_path / "app.py").write_text("import requests\n")

    routing = classify(str(tmp_path), target_stdlib=frozenset(), declared=frozenset({"requests"}))
    assert "requests" in routing.external       # declared wins rung 1 → external, never internal
    assert "requests" not in {n for n, _ in routing.internal}
