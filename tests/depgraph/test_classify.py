from graph.python.route.classify import classify
from graph.python.read.repo_modules import stem_collisions
from graph.schema import NodeType


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


def test_pep420_namespace_root_routes_to_collision(tmp_path):
    # src/mycompany/pkga/__init__.py with NO mycompany/__init__.py: a PEP 420
    # namespace. The real import is mycompany.pkga; the naive climb mints `pkga`.
    pkga = tmp_path / "src" / "mycompany" / "pkga"
    pkga.mkdir(parents=True)
    (pkga / "__init__.py").write_text("")
    (pkga / "mod.py").write_text("import pkga.mod\n")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.setuptools.packages.find]\nwhere=['src']\n"  # namespace-declaring
    )

    # Premise guard: without the fix the naive climb really mints `pkga` as a
    # false sys.path-accurate top-level (it stops one dir too low under the PEP
    # 420 namespace `mycompany`), so the collision-zone assertion is not vacuous.
    from graph.python.read.repo_modules import top_level_names
    assert "pkga" in top_level_names(str(tmp_path))

    from graph.python.route.classify import classify
    routing = classify(str(tmp_path), target_stdlib=frozenset(), declared=frozenset())
    assert "pkga" in routing.deferred                      # namespace-suspect → collision zone
    assert "pkga" not in {n for n, _ in routing.internal}  # NOT a trusted top-level
