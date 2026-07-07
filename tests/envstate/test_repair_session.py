"""Tests for the RepairSession notebook, the progress rule, and attempts-axis persistence."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from src.envstate.repair_session import (  # noqa: E402
    RepairSession, Step, made_progress, persist_session_to_attempts,
)
from src.envstate.repair_types import ReplayResult  # noqa: E402


def test_render_shows_full_history():
    s = RepairSession("pkg:psycopg2", "libpq")
    s.steps.append(Step("patch", "add:['syslib:libpq']", cap="libpq",
                        replay=ReplayResult(False, "pkg:psycopg2", "pg_config", "pip install", "")))
    rendered = s.render_for_agent()
    assert "syslib:libpq" in rendered and "pg_config" in rendered


def test_probed_tracks_per_cap():
    s = RepairSession("pkg:x", "libx")
    assert not s.probed("libx")
    s.steps.append(Step("probe", "probe:ldconfig", cap="libx"))
    assert s.probed("libx") and not s.probed("liby")


def test_progress_true_when_missing_cap_changes():
    s = RepairSession("pkg:p", "libpq")
    s.steps.append(Step("patch", "add libpq", cap="libpq",
                        replay=ReplayResult(False, "pkg:p", "libpq", "c", "")))
    assert made_progress(s, ReplayResult(False, "pkg:p", "pg_config", "c", "")) is True


def test_progress_false_when_signature_unchanged():
    s = RepairSession("pkg:p", "libx")
    s.steps.append(Step("patch", "add dummy", cap="libx",
                        replay=ReplayResult(False, "pkg:p", "libx", "c", "")))
    assert made_progress(s, ReplayResult(False, "pkg:p", "libx", "c", "")) is False


def test_progress_true_on_resolution():
    s = RepairSession("pkg:p", "libx")
    assert made_progress(s, ReplayResult(True)) is True


def test_patch_steps_land_on_attempts_axis():
    g = DepGraph().with_node(Node(id="pkg:p", type=NodeType.PACKAGE, name="p", layer=Layer.PIP,
                                  discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
                                  version="1"))
    s = RepairSession("pkg:p", "libx")
    s.steps.append(Step("probe", "probe:x", cap="libx"))
    s.steps.append(Step("patch", "add libx", cap="libx", accepted=True, replay=ReplayResult(True)))
    g2 = persist_session_to_attempts(g, s, "pkg:p")
    attempts = g2.get("pkg:p").attempts
    assert len(attempts) == 1 and attempts[0].outcome == "succeeded"
    assert attempts[0].check == "repair_session"
