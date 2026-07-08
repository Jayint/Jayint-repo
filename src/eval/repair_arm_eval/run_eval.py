"""Runner for the arm-C mechanics eval. Exercises the REAL ``run_repair_arm`` against the
FakeWorld, prints the design-point log, and reports design-coverage. Run:

    python3 -m src.eval.repair_arm_eval.run_eval
"""
from __future__ import annotations

import sys
from pathlib import Path

# CLI bootstrap: put <repo>/src on the path so `python_deps` resolves when this module is
# run via `python3 -m src.eval.repair_arm_eval.run_eval` (tests do their own path setup).
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.envstate.repair_arm import run_repair_arm  # noqa: E402
from src.eval.repair_arm_eval.design_log import DesignLog, DESIGN
from src.eval.repair_arm_eval.scenarios import (
    scenario_simple, scenario_chain, scenario_stall, scenario_hidden_gap,
)


def run_one(name, expect, factory, silent=False):
    graph, world, agent = factory()
    log = DesignLog(silent=silent)
    outcome, graph = run_repair_arm(
        graph, replay=lambda gr, mb=(): world.replay_from_base(gr),
        certify=world.certify, readonly=world.readonly, agent=agent, log=log)
    fired = {t for t, _ in log.events}
    if not silent:
        print(f"\n  RESULT: {outcome}  ({'PASS' if outcome == expect else 'FAIL — expected ' + expect})")
        for n in graph.nodes:
            if n.attempts:
                print(f"  node {n.id}.attempts = {[(a.outcome, a.check) for a in n.attempts]}")
    return (outcome == expect), fired


def main():
    cases = [
        ("simple (1 syslib)", "DONE", scenario_simple),
        ("chain (libpq→pg_config, follows fwd)", "DONE", scenario_chain),
        ("stall (unfixable, honest give-up)", "GIVEUP", scenario_stall),
        ("hidden gap (green replay hides an unrendered node)", "DONE", scenario_hidden_gap),
    ]
    results, all_fired = [], set()
    for name, expect, factory in cases:
        print("\n" + "=" * 80)
        print(f"SCENARIO: {name}   (expect: {expect})")
        print("=" * 80)
        ok, fired = run_one(name, expect, factory)
        results.append((name, ok))
        all_fired |= fired

    print("\n" + "=" * 80 + "\nSUMMARY\n" + "=" * 80)
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'} {name}")
    missing = sorted(set(DESIGN) - all_fired)
    print(f"\n  design-points NEVER exercised: {missing or 'none — full coverage'}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
