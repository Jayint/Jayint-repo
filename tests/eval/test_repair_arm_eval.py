"""End-to-end mechanics eval: all scenarios reach their expected outcome, and every key
design point is exercised across the scenario set."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.eval.repair_arm_eval.run_eval import run_one  # noqa: E402
from src.eval.repair_arm_eval.scenarios import (  # noqa: E402
    scenario_simple, scenario_chain, scenario_stall, scenario_hidden_gap,
)
from src.eval.repair_arm_eval.design_log import DESIGN  # noqa: E402


def test_all_scenarios_reach_expected_outcome():
    assert run_one("simple", "DONE", scenario_simple, silent=True)[0]
    assert run_one("chain", "DONE", scenario_chain, silent=True)[0]
    assert run_one("stall", "GIVEUP", scenario_stall, silent=True)[0]
    assert run_one("hidden_gap", "DONE", scenario_hidden_gap, silent=True)[0]


def test_full_design_point_coverage():
    fired = set()
    for exp, fac in [("DONE", scenario_simple), ("DONE", scenario_chain),
                     ("GIVEUP", scenario_stall), ("DONE", scenario_hidden_gap)]:
        fired |= run_one("s", exp, fac, silent=True)[1]
    missing = set(DESIGN) - fired
    assert not missing, f"design-points never exercised: {sorted(missing)}"
