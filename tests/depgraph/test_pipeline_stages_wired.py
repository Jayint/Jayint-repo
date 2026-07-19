"""Task C1 — both native runtime-dependency stages are WIRED into the live
pipeline + the stage tracer.

Two new stages ship as standalone, already-tested modules
(``graph.python.native.runtime_tools.seed_runtime_tools`` — Phase 1 curated
runtime-executable prior — and ``graph.python.native.ctypes_scan.
add_ctypes_runtime_libs`` — Phase 2 dlopen/ctypes scan), but a standalone
module is inert until the pipeline actually calls it. This file proves three
things, matching the shape of the existing wiring proofs
(``tests/graph/test_stage_trace.py::test_default_targets_all_resolve_against_
the_real_pipeline``, ``tests/depgraph/test_build_native_prepass.py``):

1. Both functions are reachable as module-level attributes on
   ``graph.python.pipeline`` — the exact lookup the tracer uses
   (``getattr(importlib.import_module(module_qual), attr)``), so importing
   them into pipeline's namespace is both necessary and sufficient for the
   tracer to see them.
2. Both are registered in ``graph.debug.stage_trace.DEFAULT_TARGETS`` against
   ``graph.python.pipeline`` (the drift guard the tracer itself enforces at
   ``StageTracer.__enter__``).
3. Phase 2 (``_python_native_obligations``) actually CALLS
   ``add_ctypes_runtime_libs`` on the converged closure. Every executor-
   calling stage in Phase 2 (``certified_import_links``, ``ldd_probe``,
   ``import_probe``, ``add_ctypes_runtime_libs``, ``reconcile_apt_names``) is
   monkeypatched to a pure stub, and the executor passed in raises if its
   ``.run`` is ever invoked directly — proving the call reaches
   ``add_ctypes_runtime_libs`` through the pipeline itself, not by accident
   through some other stage that happens to shell out.
"""

from __future__ import annotations

from graph.model import DepGraph
from graph.python import pipeline
from graph.python.native.ctypes_scan import add_ctypes_runtime_libs
from graph.python.native.runtime_tools import seed_runtime_tools


def test_both_new_stages_are_importable_on_pipeline_module():
    # This is the exact lookup graph.debug.stage_trace.StageTracer performs
    # (getattr(module, attr)) -- if these names are not bound in pipeline's
    # own namespace, the tracer's drift guard raises AttributeError.
    assert pipeline.seed_runtime_tools is seed_runtime_tools
    assert pipeline.add_ctypes_runtime_libs is add_ctypes_runtime_libs


def test_both_new_stages_are_registered_in_default_targets():
    from graph.debug.stage_trace import DEFAULT_TARGETS

    targets = {(mod, attr) for mod, attr, _label in DEFAULT_TARGETS}
    assert ("graph.python.pipeline", "seed_runtime_tools") in targets
    assert ("graph.python.pipeline", "add_ctypes_runtime_libs") in targets


class _RaisingExecutor:
    """An Executor whose .run() must never be called directly by Phase 2 in
    this test -- every stage that would otherwise shell out is stubbed below,
    so a real .run() call here means some stage escaped the stub (a false
    proof of wiring)."""

    def run(self, command: str, *, timeout: int = 300):
        raise AssertionError(
            f"unexpected direct executor.run() call: {command!r} -- a Phase 2 "
            "stage escaped its monkeypatch stub"
        )


def test_phase2_native_obligations_calls_ctypes_scan(monkeypatch):
    calls = []

    monkeypatch.setattr(pipeline, "certified_import_links", lambda g, ex: g)
    monkeypatch.setattr(pipeline, "ldd_probe", lambda g, ex: g)
    monkeypatch.setattr(pipeline, "import_probe", lambda g, ex: g)
    monkeypatch.setattr(pipeline, "reconcile_apt_names", lambda g, ex: g)

    def fake_ctypes_scan(g, ex):
        calls.append((g, ex))
        return g

    monkeypatch.setattr(pipeline, "add_ctypes_runtime_libs", fake_ctypes_scan)

    graph = DepGraph()
    ex = _RaisingExecutor()
    out = pipeline._python_native_obligations(graph, ex)

    assert len(calls) == 1
    assert calls[0] == (graph, ex)
    assert isinstance(out, DepGraph)
