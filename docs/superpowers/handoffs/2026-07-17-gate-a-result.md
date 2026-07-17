# Gate A — cure-recovery result (config-lane go/no-go) — DEFERRED

**Status: measurement intentionally NOT run.** Per the user's directive on
2026-07-17 (no Docker, no LLM, no network fetch), the Gate A cure-recovery
measurement was **not** executed. There is therefore **no empirical go/no-go
number yet** — this is a placeholder that the harness overwrites with the real
verdict once it is run.

## What is ready
The harness `scripts/gate_a_cure_recovery.py` is built and unit-tested (pure
logic only — `_strip_editable`, `_aggregate`, `measure` table/aggregate shape,
corpus loading; all infra mocked). It reuses existing infrastructure only:
`run_v3_e2e.py` renders the config-lane `setup.sh` and `run_replay_ladder`
(`src/eval/build_script_eval/replay.py`) runs each arm in a fresh mounted
container and reports `LadderResult.collect_ok`. No new container harness.

## Commands to run it later
```bash
# provision only (fetch + reset each pilot repo to its pinned commit):
python scripts/gate_a_cure_recovery.py --corpus datasets/pilot.json --provision-only

# pilot measurement (3 repos):
python scripts/gate_a_cure_recovery.py --corpus datasets/pilot.json \
       --base-image <arm64-python-image>

# 50-repo corpus:
python scripts/gate_a_cure_recovery.py \
       --corpus datasets/rat_python50_pinned_m3nothink.json \
       --base-image <arm64-python-image>
```
On a full run the script prints the two-arm table + aggregate lift and rewrites
this file with the measured verdict (listing every excluded/errored repo — no
silent caps).

## Go/no-go criterion (verbatim, plan Step 5)
GO if the config-lane arm materially lifts collect-clean over baseline on the
corpus (target: recovers a meaningful share of the 34-build->14-collect gap —
the project-namespace ModuleNotFoundError class). NO-GO if editable-install +
rootdir does not move collection — then the config lane is not worth building
and Stage B is cancelled.

**Until this measurement is run, Tasks 2-3 and all of Stage B remain gated —
do not treat the config lane as GO.**
