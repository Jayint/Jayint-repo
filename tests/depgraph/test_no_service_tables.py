"""The governing constraint (spec §2): no service-specific table anywhere."""
import importlib

import pytest


def test_kind_of_is_gone():
    mod = importlib.import_module("python_deps.depgraph.service_scan")
    assert not hasattr(mod, "_kind_of")


def test_recipe_table_is_gone():
    mod = importlib.import_module("python_deps.depgraph.service_recipes")
    assert not hasattr(mod, "_KIND_BASE")
    assert not hasattr(mod, "render_setup")
    assert not hasattr(mod, "KindBase")
    assert not hasattr(mod, "RECIPE_KINDS")


def test_surviving_service_scan_exports_still_import():
    mod = importlib.import_module("python_deps.depgraph.service_scan")
    for sym in ("service_bind_url", "service_from_url",
                "scan_ci_services", "scan_compose_services", "classify_service_error"):
        assert hasattr(mod, sym), sym


def test_render_probe_poll_survives_for_patch_gate():
    mod = importlib.import_module("python_deps.depgraph.service_recipes")
    assert hasattr(mod, "render_probe_poll")


def test_deleted_modules_are_gone():
    for name in ("src.envstate.service_translate",
                 "python_deps.depgraph.provisioning_spec"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
