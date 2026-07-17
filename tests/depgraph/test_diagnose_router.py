"""Tests for diagnose.diagnose routing (pure, no Docker)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from graph.diagnose import Mode, RepoContext, diagnose
from graph.model import NodeType


def test_external_import_routes_environment_with_discovery():
    d = diagnose("python app.py",
                 "ModuleNotFoundError: No module named 'requests'",
                 RepoContext())
    assert d.mode is Mode.ENVIRONMENT
    assert d.discovery is not None
    assert d.discovery.node_type is NodeType.PACKAGE


def test_local_import_routes_repo_internal_ref_no_discovery():
    ctx = RepoContext(local_names=frozenset({"docs_src"}))
    d = diagnose("python -m docs_src.build",
                 "ModuleNotFoundError: No module named 'docs_src'",
                 ctx)
    assert d.mode is Mode.REPO_INTERNAL_REF
    assert d.discovery is None


def test_no_matching_distribution_routes_invalid_attempt():
    d = diagnose("pip install frobnicate9000",
                 "ERROR: No matching distribution found for frobnicate9000",
                 RepoContext())
    assert d.mode is Mode.INVALID_ATTEMPT
    assert d.discovery is None


def test_previously_invalid_name_routes_invalid_attempt():
    # An external import whose mapped package was already disproven. Use a
    # curated import ("django_filters" -> "django-filter") since an unmapped
    # import now resolves to name=None and can never match ctx.invalid_names.
    ctx = RepoContext(invalid_names=frozenset({"django-filter"}))
    d = diagnose("python app.py",
                 "ModuleNotFoundError: No module named 'django_filters'",
                 ctx)
    assert d.mode is Mode.INVALID_ATTEMPT
    assert d.discovery is None


def test_native_lib_routes_environment_systemlib():
    d = diagnose("python app.py",
                 "ImportError: libGL.so.1: cannot open shared object file",
                 RepoContext())
    assert d.mode is Mode.ENVIRONMENT
    assert d.discovery is not None
    assert d.discovery.node_type is NodeType.SYSTEM_LIB


def test_assertion_routes_residual():
    d = diagnose("python -m pytest -q",
                 "E       assert 1 == 2\nAssertionError",
                 RepoContext())
    assert d.mode is Mode.RESIDUAL
    assert d.discovery is None


def test_unclassified_routes_ambiguous():
    d = diagnose("python app.py", "Segmentation fault (core dumped)", RepoContext())
    assert d.mode is Mode.AMBIGUOUS
    assert d.discovery is None


def test_none_named_discovery_does_not_route_invalid_attempt(monkeypatch):
    # Task 4 (runtime_classify) can now emit Discovery(name=None, ...) for an
    # import that failed to map to any distribution. _norm(None) coerces to
    # "" (see test_diagnose_reconciliations.py), so if "" were ever recorded
    # as a disproven name, a None-named discovery would wrongly and
    # permanently match it as a previously-invalid attempt. A discovery with
    # no name is unnameable -- it cannot be "previously disproven" -- and
    # must route exactly as any other present-but-not-invalid name does.
    import graph.diagnose as diagnose_module
    from graph.runtime_classify import Discovery
    from graph.model import Layer

    unresolved = Discovery(
        node_type=NodeType.PACKAGE,
        name=None,
        layer=Layer.PIP,
        evidence="ModuleNotFoundError: No module named 'mystery'",
        check_command='python3 -c "import mystery"',
        data={"import_name": "mystery"},
    )
    monkeypatch.setattr(diagnose_module, "classify_observation", lambda cmd, out: unresolved)

    ctx = RepoContext(invalid_names=frozenset({""}))
    d = diagnose("python app.py", "ModuleNotFoundError: No module named 'mystery'", ctx)

    assert d.mode is not Mode.INVALID_ATTEMPT
    assert d.mode is Mode.ENVIRONMENT
    assert d.discovery is unresolved


_WAGTAIL_CTX = RepoContext(
    local_names=frozenset({"wagtail", "runtests", "setup", "tests"}),
    collisions={"azure": "wagtail.contrib.frontend_cache.backends.azure"},
)
_TYPER_CTX = RepoContext(
    local_names=frozenset({"typer", "tests"}),
    collisions={"items": "tutorial001.items"},
)


def test_stem_collision_does_not_silently_give_up():
    """wagtail `azure`: azure-mgmt-cdn is extras-gated and never installed.
    The OLD broad rule called this repo-local and returned REPO_INTERNAL_REF —
    a silent give-up with no repair attempted. It must reach the repair loop."""
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'azure'",
                 _WAGTAIL_CTX)
    assert d.mode is not Mode.REPO_INTERNAL_REF
    assert d.mode is Mode.AMBIGUOUS


def test_stem_collision_carries_the_real_module_path_as_evidence():
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'azure'",
                 _WAGTAIL_CTX)
    assert "wagtail.contrib.frontend_cache.backends.azure" in d.reason


def test_stem_collision_mints_no_discovery():
    """AMBIGUOUS carries no Discovery, so the deterministic ingest tier cannot
    auto-mint pkg:azure. The LLM must propose it against the failure text."""
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'azure'",
                 _WAGTAIL_CTX)
    assert d.discovery is None


def test_stem_collision_is_not_environment_for_a_syspath_sibling():
    """typer `items`: a REAL PyPI package, but installing it is WRONG — it is a
    sibling script reachable via sys.path[0]. Routing this to ENVIRONMENT would
    hand the deterministic tier a mapped package. It must stay AMBIGUOUS."""
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'items'",
                 _TYPER_CTX)
    assert d.mode is not Mode.ENVIRONMENT
    assert d.mode is Mode.AMBIGUOUS
    assert d.discovery is None


def test_real_repo_module_still_routes_repo_internal_ref():
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'wagtail'",
                 _WAGTAIL_CTX)
    assert d.mode is Mode.REPO_INTERNAL_REF


def test_plain_external_still_routes_environment():
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'requests'",
                 _WAGTAIL_CTX)
    assert d.mode is Mode.ENVIRONMENT
    assert d.discovery is not None


def test_collision_precedes_invalid_attempt_check():
    """A collision name already pip-disproven must NOT be retried."""
    ctx = RepoContext(
        local_names=frozenset({"wagtail"}),
        collisions={"azure": "wagtail.contrib.backends.azure"},
        invalid_names=frozenset({"azure"}),
    )
    d = diagnose("python -m pytest -q",
                 "ModuleNotFoundError: No module named 'azure'",
                 ctx)
    assert d.mode is Mode.INVALID_ATTEMPT


def test_import_name_error_stem_collision_routes_ambiguous():
    """Task 4 patches BOTH branches. The import_name_error branch needs its own
    test or a regression there ships silently."""
    d = diagnose(
        "python -m pytest -q",
        "ImportError: cannot import name 'Foo' from 'items' "
        "(docs_src/subcommands/tutorial001/items.py)",
        _TYPER_CTX,
    )
    assert d.mode is Mode.AMBIGUOUS
    assert d.discovery is None
    assert "tutorial001.items" in d.reason


def test_import_name_error_repo_module_routes_repo_internal_ref():
    d = diagnose(
        "python -m pytest -q",
        "ImportError: cannot import name 'Foo' from 'wagtail' (wagtail/__init__.py)",
        _WAGTAIL_CTX,
    )
    assert d.mode is Mode.REPO_INTERNAL_REF
