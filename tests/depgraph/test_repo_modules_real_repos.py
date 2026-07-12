"""Regression tests against REAL repo checkouts.

Each case is a bug a prior design shipped or nearly shipped. They skip when the
checkout is absent so a bare clone still passes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.repo_modules import stem_collisions, top_level_names
from python_deps.depgraph.scan import local_module_names

_SERVICES = Path("outputs/graph_fidelity/_smoke_services")
_LIBS = Path("outputs/build_script_eval/_smoke")


def _repo(base: Path, name: str) -> str:
    path = base / name
    if not path.is_dir():
        pytest.skip(f"{path} not checked out")
    return str(path)


def test_wagtail_azure_is_a_collision_not_a_repo_module():
    """THE bug: azure-mgmt-cdn is extras-gated and never installed, and the old
    broad rule called `azure` repo-local -> silent give-up, no repair."""
    repo = _repo(_SERVICES, "wagtail")
    assert "azure" not in top_level_names(repo)
    assert "azure" in stem_collisions(repo)
    assert "wagtail" in top_level_names(repo)


def test_typer_items_is_a_collision_not_an_external():
    """The inverse bug: `items` IS a real PyPI dist. Classifying it external
    lets Phase-A install it. The repo's own oracle
    (src/eval/graph_fidelity/ab_gold_labels.py:59-62) labels it "local"."""
    repo = _repo(_LIBS, "typer")
    collisions = stem_collisions(repo)
    for name in ("items", "lands", "reigns", "towns", "users"):
        assert name not in top_level_names(repo), f"{name} must not be a top-level"
        assert name in collisions, f"{name} must be a COLLISION, never a plain external"


def test_jupyterhub_traitlets_is_not_a_repo_module():
    """jupyterhub/traitlets.py is `jupyterhub.traitlets`; bare `import traitlets`
    is the PyPI package (declared, 24 importers)."""
    repo = _repo(_SERVICES, "jupyterhub")
    assert "traitlets" not in top_level_names(repo)
    assert "jupyterhub" in top_level_names(repo)


def test_netbox_core_apps_stay_local():
    """netbox has 1,184 .py files. An import-capped walk drops `extras` from the
    set -> classified external -> Phase-A installs the REAL PyPI `extras`.
    The walk must be uncapped."""
    repo = _repo(_SERVICES, "netbox")
    tops = top_level_names(repo)
    for app in ("extras", "dcim", "utilities", "circuits", "ipam"):
        assert app in tops, f"{app} is bare-importable under netbox/ and must be LOCAL"
    for collision in ("jinja2", "mptt", "markdown", "jsonschema"):
        assert collision not in tops, f"{collision} is a declared PyPI dist, not a top-level"


def test_precise_set_is_a_subset_of_the_broad_set():
    """The safety invariant: the new set is strictly NARROWER, so nothing that is
    local today becomes external tomorrow by accident.

    NOTE the non-emptiness assertion. `frozenset() <= anything` is trivially True,
    so a bug that made top_level_names() always return an empty set (e.g. broken
    repo-path plumbing) would pass a bare subset check on every repo with zero
    signal. Assert the set is populated AND that a known top-level is in it.
    """
    expected = {"wagtail": "wagtail", "netbox": "dcim", "flask": "flask", "typer": "typer"}
    for base, name in ((_SERVICES, "wagtail"), (_SERVICES, "netbox"),
                       (_LIBS, "flask"), (_LIBS, "typer")):
        repo = _repo(base, name)
        precise = top_level_names(repo)
        assert precise, f"{name}: top_level_names is EMPTY — subset check would be vacuous"
        assert expected[name] in precise, f"{name}: missing known top-level"
        assert precise <= local_module_names(repo), name


def test_at_least_one_real_repo_checkout_is_present():
    """Fail loudly rather than skipping the entire real-repo regression suite.

    If this fails, the checkouts are missing and the four regressions this file
    exists for did NOT run. Populate outputs/ or accept the gap knowingly --
    do not let it pass silently.
    """
    present = [
        p.name
        for base in (_SERVICES, _LIBS)
        if base.is_dir()
        for p in base.iterdir()
        if p.is_dir()
    ]
    assert present, (
        "no real repo checkouts under outputs/ — the wagtail/azure, typer/items, "
        "jupyterhub/traitlets and netbox/extras regressions did NOT run. These are "
        "the cases every prior design got wrong."
    )
