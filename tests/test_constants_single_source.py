"""VERIFY_TEST_CMD has a single source of truth in src.envstate.constants.

Before the refactor, ``graph_scheduler`` lazy-imported the constant from
``orchestrator`` inside ``_discover_task`` purely to dodge an import cycle, so
``from src.envstate.graph_scheduler import VERIFY_TEST_CMD`` raised ImportError.
After moving the constant to the dependency-free ``constants`` leaf module, all
three modules reference the same object and the cycle is gone.
"""
import sys


def test_verify_test_cmd_is_single_object_across_modules():
    from src.envstate.constants import VERIFY_TEST_CMD as from_constants
    from src.envstate.orchestrator import VERIFY_TEST_CMD as from_orchestrator
    from src.envstate.graph_scheduler import VERIFY_TEST_CMD as from_scheduler

    assert from_constants is from_orchestrator is from_scheduler
    # the canonical bare-interpreter gate; lenient (matches the ratbench scorer) + -ra for the
    # react error breakdown. Assert the invariant parts, not every flag, so it isn't brittle.
    assert from_constants.startswith("python -m pytest")
    assert "--continue-on-collection-errors" in from_constants


def test_graph_scheduler_does_not_import_orchestrator_at_module_load():
    # Importing the scheduler must not drag in the orchestrator (cycle broken).
    # Save + restore the popped modules: without this, a later test that imported `orchestrator`
    # at its own module top keeps a stale reference while this test leaves a DIFFERENT (freshly
    # re-imported) orchestrator in sys.modules, whose TerminationReason enum identity diverges —
    # cross-file pollution that flipped v3 stop-reasons (planner_done -> max_cycles).
    saved = {k: sys.modules.get(k)
             for k in ("src.envstate.graph_scheduler", "src.envstate.orchestrator")}
    try:
        sys.modules.pop("src.envstate.graph_scheduler", None)
        sys.modules.pop("src.envstate.orchestrator", None)
        import src.envstate.graph_scheduler  # noqa: F401

        assert "src.envstate.orchestrator" not in sys.modules
    finally:
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v
