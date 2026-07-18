from graph.emit.emit import (
    _FAILED_NODES_LOG,
    populate_setup_commands,
)
from graph.model import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State, Strength,
)


def _pkg():
    return Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests",
                layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                version="2.0", state=State.MISSING, chosen_fix="pip:requests")


def _syslib():
    return Node(id="syslib:libpq", type=NodeType.SYSTEM_LIB, name="libpq",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                state=State.MISSING, chosen_fix="apt:libpq-dev")


def _service():
    return Node(id="service:redis", type=NodeType.SERVICE, name="redis",
                layer=Layer.SERVICES, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.MISSING)


def _tool():
    return Node(id="tool:cmake", type=NodeType.TOOL, name="cmake",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
                state=State.MISSING, chosen_fix="apt:cmake")


def _project(installable=True, **kw):
    return Node(id="project:myproj", type=NodeType.PROJECT, name="myproj",
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN, data={"installable": installable}, **kw)


def _block(cmd: str, node_id: str) -> tuple:
    """The FIX B5(c) non-fatal wrapping shape: `cmd` stays on its OWN line
    (byte-identical to the pre-fix command — see populate._non_fatal_block's
    docstring for why this is an `if`/`then`/`else`/`fi` block and not an
    inline `cmd || echo ...` suffix), with a greppable failure marker in the
    `else` branch."""
    return (
        f"if {cmd}",
        "then",
        "    :",
        "else",
        f'    echo "V3_NODE_INSTALL_FAILED {node_id}" >> {_FAILED_NODES_LOG}',
        "fi",
    )


_EDITABLE_CHAIN = (
    "python3 -m pip install --break-system-packages --no-deps -e . "
    "|| python3 -m pip install --break-system-packages --no-deps ."
)


def test_fills_reciped_package_with_pinned_no_deps_pip():
    # FIX B5(c): a per-node install line must be non-fatal — its failure is
    # recorded as a greppable marker instead of aborting the whole
    # `set -Eeuo pipefail` script (so ONE bad package can't sink a 200-package
    # build). A command inside an `if` CONDITION is exempt from errexit
    # regardless of outcome, so this can never itself trip `set -e`.
    n = populate_setup_commands(DepGraph(nodes=(_pkg(),))).get("pkg:requests")
    assert n.setup_commands == _block(
        "python3 -m pip install --break-system-packages --no-deps requests==2.0",
        "pkg:requests",
    )
    assert n.strength is Strength.HARD


def test_fills_reciped_syslib_with_apt():
    n = populate_setup_commands(DepGraph(nodes=(_syslib(),))).get("syslib:libpq")
    assert n.setup_commands == _block(
        "apt-get install -y --no-install-recommends libpq-dev", "syslib:libpq",
    )
    assert n.strength is Strength.HARD


def test_leaves_non_reciped_service_untouched():
    n = populate_setup_commands(DepGraph(nodes=(_service(),))).get("service:redis")
    assert n.setup_commands == ()
    assert n.strength is Strength.SOFT


def test_fills_reciped_tool_with_apt():
    n = populate_setup_commands(DepGraph(nodes=(_tool(),))).get("tool:cmake")
    assert n.setup_commands == _block(
        "apt-get install -y --no-install-recommends cmake", "tool:cmake",
    )
    assert n.strength is Strength.HARD


def test_fills_installable_project_with_editable_no_deps():
    n = populate_setup_commands(DepGraph(nodes=(_project(),))).get("project:myproj")
    assert n.setup_commands == _block(_EDITABLE_CHAIN, "project:myproj")
    assert n.strength is Strength.HARD


def test_leaves_non_installable_project_untouched():
    # data["installable"]=False (no build system declared) -> still no capstone,
    # exactly as before FIX B6 — this is the one B5(a) check that survives,
    # because it is a pre-existing structural fact (build.py._project_build_
    # manifest), never a prediction of whether pip will succeed.
    n = populate_setup_commands(
        DepGraph(nodes=(_project(installable=False),))
    ).get("project:myproj")
    assert n.setup_commands == ()
    assert n.strength is Strength.SOFT


def test_idempotent_does_not_overwrite_existing():
    g = DepGraph(nodes=(_pkg(),))
    once = populate_setup_commands(g)
    twice = populate_setup_commands(once)
    assert once.get("pkg:requests").setup_commands == twice.get("pkg:requests").setup_commands
    assert once.get("pkg:requests").strength == twice.get("pkg:requests").strength


# ---------------------------------------------------------------------------
# FIX B6 — ATTEMPT, don't PREDICT. B5(a)'s static installability heuristic
# (`_looks_safely_installable`) is gone: it used to skip `pip` entirely for a
# project it flagged as risky (flat-layout ambiguity, no PEP 621 version, a
# `setup.py` that does `import pip`) and leave a `#@pythonpath-env` marker
# instead. That is the exact regression this fix repairs — B5(a) predicted
# pre-commit/pre-commit would fail and suppressed a capstone that actually
# WORKS, dropping EBSR from 1.0 to 0. These fixtures are the SAME shapes B5(a)
# used to reject; the point of these tests is that the capstone renders for
# every one of them now, unconditionally.
# ---------------------------------------------------------------------------

def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _project_on_disk(tmp_path, installable=True):
    return _project(installable=installable, provenance=str(tmp_path / "pyproject.toml"))


def test_flat_layout_project_still_gets_editable_chain(tmp_path):
    # PostHog/Spoolman shape: >=2 top-level importable dirs, no src/ layout —
    # B5(a) rejected this outright. B6: attempt it anyway, non-fatally.
    _write(tmp_path, "pyproject.toml", '[project]\nname = "app"\nversion = "1.0.0"\n')
    _write(tmp_path, "ee/__init__.py", "")
    _write(tmp_path, "cli/__init__.py", "")
    node = _project_on_disk(tmp_path)
    n = populate_setup_commands(DepGraph(nodes=(node,))).get("project:myproj")
    assert n.setup_commands == _block(_EDITABLE_CHAIN, "project:myproj")


def test_missing_version_project_still_gets_editable_chain(tmp_path):
    # mozilla/addons-server shape: [project] table with no version at all.
    _write(tmp_path, "pyproject.toml", '[project]\nname = "addons-server"\n')
    node = _project_on_disk(tmp_path)
    n = populate_setup_commands(DepGraph(nodes=(node,))).get("project:myproj")
    assert n.setup_commands == _block(_EDITABLE_CHAIN, "project:myproj")


def test_setup_py_imports_pip_project_still_gets_editable_chain(tmp_path):
    # ArchipelagoMW/Archipelago shape: setup.py's own `import pip`.
    _write(tmp_path, "setup.py", "import pip\nfrom setuptools import setup\nsetup()\n")
    node = Node(
        id="project:myproj", type=NodeType.PROJECT, name="myproj", layer=Layer.PIP,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
        data={"installable": True}, provenance=str(tmp_path / "setup.py"),
    )
    n = populate_setup_commands(DepGraph(nodes=(node,))).get("project:myproj")
    assert n.setup_commands == _block(_EDITABLE_CHAIN, "project:myproj")


def test_well_formed_project_on_disk_still_gets_editable_chain(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname = "widget"\nversion = "1.0.0"\n')
    node = _project_on_disk(tmp_path)
    n = populate_setup_commands(DepGraph(nodes=(node,))).get("project:myproj")
    assert n.setup_commands == _block(_EDITABLE_CHAIN, "project:myproj")


# ---------------------------------------------------------------------------
# FIX B6(poison) — a failed capstone must poison the CERTIFICATE, not the
# build. Since this pure pass can never observe whether the attempted
# capstone (above) actually succeeds — that only happens later, inside the
# rendered bash script, on a different host — the PROJECT node's own state is
# degraded the moment its capstone is populated: `build.py._excluded_uv_
# source_node`'s shape (MISSING / no version / no check_command / marked
# uninstallable), so nothing downstream can silently treat "a command was
# emitted" as "the project is installed".
# ---------------------------------------------------------------------------

def test_installable_project_certificate_is_poisoned_on_populate():
    n = populate_setup_commands(DepGraph(nodes=(_project(),))).get("project:myproj")
    assert n.state is State.MISSING
    assert n.version is None
    assert n.check_command is None
    assert n.data.get("uninstallable") is True
    # the "installable" flag (declares a build system) is untouched — it is
    # what let this node get a capstone in the first place.
    assert n.data.get("installable") is True


def test_non_installable_project_certificate_is_not_poisoned():
    # No capstone was ever attempted -> nothing to degrade; the node is left
    # exactly as before (state=UNKNOWN, no `uninstallable` flag invented).
    n = populate_setup_commands(
        DepGraph(nodes=(_project(installable=False),))
    ).get("project:myproj")
    assert n.state is State.UNKNOWN
    assert n.data.get("uninstallable") is not True


def test_scratch_certified_project_is_not_poisoned():
    from graph.model import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from graph.emit.emit import populate_setup_commands
    # `installable=True` so this node DOES reach the capstone/poison call site
    # (a project with no build system is skipped by `_should_populate` and would
    # never be poisoned regardless, making the gate untested). `scratch_certified`
    # is the config lane's flag that the poison gate must honour: this node still
    # gets its editable capstone rendered, but its certificate is left intact.
    proj = Node(id="project:x", type=NodeType.PROJECT, name="x", layer=Layer.PIP,
                discovered_by=DiscoveredBy.GOAL,
                data={"installable": True, "scratch_certified": True})
    out = populate_setup_commands(DepGraph().with_node(proj))
    got = next(n for n in out.nodes if n.type is NodeType.PROJECT)
    # The capstone WAS populated (proving we reached the poison site)...
    assert got.setup_commands  # a project command was rendered
    # ...yet the certificate is NOT in the concrete poisoned state. The real
    # `_poison_project_certificate` sets state=MISSING, check_command=None AND
    # data['uninstallable']=True; the last is the unambiguous poison marker
    # (a non-poisoned node keeps state=UNKNOWN and never invents that flag).
    assert got.state is not State.MISSING or got.check_command is not None  # not poisoned
    assert got.data.get("uninstallable") is not True                        # poison marker absent
    assert got.state is State.UNKNOWN                                       # certificate intact
