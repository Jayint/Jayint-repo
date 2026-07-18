"""The governing constraint (spec §2): no service-specific table anywhere."""
import importlib
import importlib.util


def test_kind_of_is_gone():
    mod = importlib.import_module("graph.python.services.service_scan")
    assert not hasattr(mod, "_kind_of")


def test_recipe_table_is_gone():
    mod = importlib.import_module("graph.python.services.service_recipes")
    assert not hasattr(mod, "_KIND_BASE")
    assert not hasattr(mod, "render_setup")
    assert not hasattr(mod, "KindBase")
    assert not hasattr(mod, "RECIPE_KINDS")


def test_surviving_service_scan_exports_still_import():
    mod = importlib.import_module("graph.python.services.service_scan")
    for sym in ("service_bind_url", "service_from_url",
                "scan_ci_services", "scan_compose_services", "classify_service_error"):
        assert hasattr(mod, sym), sym


def test_render_probe_poll_survives_for_patch_gate():
    mod = importlib.import_module("graph.python.services.service_recipes")
    assert hasattr(mod, "render_probe_poll")


def test_deleted_modules_are_gone():
    """Strict absence. A ``pytest.raises(ModuleNotFoundError)`` around
    ``import_module`` would ALSO pass if the module came back with a broken
    transitive import -- proving only that *some* import in the chain failed, not
    that the target is gone. ``find_spec`` resolves the parent package and returns
    ``None`` for an absent submodule *without executing it*, so it proves the module
    itself is absent (the real guard against resurrecting the construction-time LLM).
    """
    for name in ("src.envstate.service_translate",
                 "graph.provisioning_spec"):
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError as exc:
            # find_spec imports parent packages. A missing ANCESTOR of the target
            # PROVES the target is absent (you cannot have src.envstate.service_translate
            # if src.envstate itself is gone -- envstate was deleted wholesale in the
            # Phase 2 stage-refactor). Only an unrelated missing module would be
            # suspicious, but find_spec only imports ancestors, so name it and require
            # it to be one; then count it as absence.
            assert exc.name and (name == exc.name or name.startswith(exc.name + ".")), (
                f"cannot prove {name!r} absent: unexpected missing module {exc.name!r}"
            )
            continue
        assert spec is None, f"{name} is back"
