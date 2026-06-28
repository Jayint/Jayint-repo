"""Unit tests for src/envstate/_loop_common.py.

Tests each pure helper function in isolation with minimal fixtures — no Docker,
no LLM, no real containers.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.envstate._loop_common import current_revision, host_refresh_facts
from src.envstate.ledger import ActionLedger, make_action_event
from src.envstate.world_model import WorldModelMap, Fact, initial_map, merge_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_ledger() -> ActionLedger:
    return ActionLedger()


def _ledger_with_events(*revisions_after: int) -> ActionLedger:
    """Build a ledger with one event per supplied revision_after value."""
    ledger = ActionLedger()
    for i, rev in enumerate(revisions_after, start=1):
        evt = make_action_event(
            step=i,
            cmd=f"echo {i}",
            success=True,
            stdout="ok",
            env_revision_before=rev - 1,
            env_revision_after=rev,
            mutation_class=None,
            container_id="test",
        )
        ledger.append(evt)
    return ledger


def _base_map() -> WorldModelMap:
    return initial_map(
        base_image="python:3.11",
        workdir="/repo",
        language="python",
        build_system="pip",
        repo_layout=(),
    )


# ---------------------------------------------------------------------------
# current_revision
# ---------------------------------------------------------------------------

def test_current_revision_returns_zero_for_empty_ledger():
    assert current_revision(_empty_ledger()) == 0


def test_current_revision_returns_last_revision_after():
    ledger = _ledger_with_events(1, 2, 5)
    assert current_revision(ledger) == 5


def test_current_revision_single_event():
    ledger = _ledger_with_events(7)
    assert current_revision(ledger) == 7


def test_current_revision_does_not_mutate_ledger():
    ledger = _ledger_with_events(3)
    _ = current_revision(ledger)
    assert len(ledger.events()) == 1, "current_revision must not modify the ledger"


# ---------------------------------------------------------------------------
# host_refresh_facts
# ---------------------------------------------------------------------------

class _FakeManifest:
    """Minimal manifest-like object (apply_deterministic inspects .required + .build_system)."""
    required = (Fact("flask", "3.0.0"),)
    build_system = "pip"


def test_host_refresh_facts_no_op_when_probe_none():
    m = _base_map()
    result = host_refresh_facts(m, None, _FakeManifest())
    assert result is m, "probe=None must return the same object unchanged"


def test_host_refresh_facts_no_op_when_manifest_none():
    m = _base_map()
    probe_called = {"n": 0}

    def _probe():
        probe_called["n"] += 1
        return {}

    result = host_refresh_facts(m, _probe, None)
    assert result is m, "manifest=None must return the same object unchanged"
    assert probe_called["n"] == 0, "probe must not be called when manifest is None"


def test_host_refresh_facts_both_none_returns_unchanged():
    m = _base_map()
    result = host_refresh_facts(m, None, None)
    assert result is m


def test_host_refresh_facts_calls_probe_and_returns_updated_map():
    """When both probe and manifest are provided the map is a WorldModelMap."""
    m = _base_map()

    # apply_deterministic degrades gracefully when snap.env is empty (probe
    # "failed" from the host's perspective) and still returns a WorldModelMap.
    import types
    snapshot = types.SimpleNamespace(installed=(), env={}, system_installed=())

    def _probe():
        return snapshot

    manifest = _FakeManifest()
    result = host_refresh_facts(m, _probe, manifest)
    assert isinstance(result, WorldModelMap), (
        "host_refresh_facts must return a WorldModelMap"
    )


def test_host_refresh_facts_probe_called_exactly_once():
    """probe() must be invoked exactly once per call."""
    m = _base_map()
    calls = {"n": 0}

    import types

    def _probe():
        calls["n"] += 1
        return types.SimpleNamespace(installed=(), env={}, system_installed=())

    host_refresh_facts(m, _probe, _FakeManifest())
    assert calls["n"] == 1, "probe must be called exactly once"
