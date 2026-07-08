"""Runs the react loop against every scenario; prints the design log + coverage. Run:
    python3 -m src.eval.react_repair_eval.run_eval"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.react_repair.history import History          # noqa: E402
from src.react_repair.log import DESIGN, ReactLog     # noqa: E402
from src.react_repair.loop import run_react           # noqa: E402
from src.eval.react_repair_eval import scenarios as S  # noqa: E402


def run_one(name, factory, silent=False):
    initial, box, planner, expect = factory()
    log = ReactLog(silent=silent)
    outcome, _script, _g = run_react(
        object(), reset=box.reset, run_script=box.run_script, certify=box.certify,
        exec_readonly=box.exec_readonly, run_tests=box.run_tests, planner=planner,
        history=History(), log=log, max_steps=12, _initial_script=initial)
    fired = {t for t, _ in log.events}
    if not silent:
        print(f"\n  RESULT: {outcome}  ({'PASS' if outcome == expect else 'FAIL — expected ' + expect})")
    return outcome == expect, fired


def main():
    cases = [
        ("green first pass", S.scenario_green),
        ("build fail → patch", S.scenario_build_fail_then_patch),
        ("tests fail → patch", S.scenario_tests_fail_then_patch),
        ("explore → patch", S.scenario_explore_then_patch),
        ("unfixable → giveup", S.scenario_unfixable_giveup),
    ]
    ok_all, fired_all = True, set()
    for name, factory in cases:
        print("\n" + "=" * 70 + f"\nSCENARIO: {name}\n" + "=" * 70)
        ok, fired = run_one(name, factory)
        ok_all &= ok
        fired_all |= fired
    missing = sorted(set(DESIGN) - fired_all)
    print(f"\n  design-points NEVER exercised: {missing or 'none — full coverage'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
