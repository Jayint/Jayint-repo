"""run_v3_session assembly: wires run_repair_arm, tags loop_mode, resolves the chain."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate.repair_arm_entry import run_v3_session, LOOP_MODE  # noqa: E402
from src.envstate.repair_log import DesignLog  # noqa: E402
from src.eval.repair_arm_eval.scenarios import scenario_chain  # noqa: E402


def test_run_v3_session_resolves_and_tags_loop_mode():
    g, world, agent = scenario_chain()          # inject the scripted agent + FakeWorld adapters
    seen = []
    outcome, _ = run_v3_session(
        g, replay=lambda gr, mb=(): world.replay_from_base(gr),
        certify=world.certify, readonly=world.readonly, agent=agent,
        log=DesignLog(silent=True), loop_mode_sink=seen.append)
    assert outcome == "DONE"
    assert seen == [LOOP_MODE]
