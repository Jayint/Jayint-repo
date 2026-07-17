# Gate B — partition-sanity result (config-lane go/no-go)

> **DEFERRED (2026-07-17):** The Gate B live measurement was **intentionally NOT run**
> per user directive. There is **no measured partition-sanity number** in this note yet.
> The harness (`scripts/gate_b_partition_sanity.py`) is **built and ready**; running it
> (below) overwrites this file with the measured aggregate + verdict.

## Status

- Harness: **built + unit-tested** (`scripts/gate_b_partition_sanity.py`,
  `tests/test_gate_b_partition_sanity.py`). The pure aggregation
  (`aggregate_shadow_records`) is covered; construction/provisioning are not exercised
  in tests (no Docker/git/network/LLM).
- Wiring: construction goes through `build_dep_graph(..., shadow_config_lane=True)`
  (the verified flag entrypoint — `build.py:1089`), inside a fresh scratch
  `DockerExecutor`, mirroring `advise.build_advisory_for_repo`. The advisory wrapper
  does **not** thread `shadow_config_lane`, so the harness calls `build_dep_graph`
  directly. The shadow pass appends one `ShadowRecord` per repo to
  `V3_SHADOW_RECORD_PATH` and discards its graph effect (real construction unchanged).
- Measurement: **not run** — no container spawned, no repo fetched, no LLM called.

## Run commands (when the measurement is executed later)

Pilot sweep (must stay green — real graph unchanged):

```bash
V3_SHADOW_RECORD_PATH=/tmp/shadow.jsonl \
    python scripts/gate_b_partition_sanity.py \
           --corpus datasets/pilot.json --base-image <arm64-img>
```

50-repo corpus:

```bash
V3_SHADOW_RECORD_PATH=/tmp/shadow50.jsonl \
    python scripts/gate_b_partition_sanity.py \
           --corpus datasets/rat_python50_pinned_m3nothink.json --base-image <arm64-img>
```

Provision-only (fetch + reset each repo to its pinned commit, then stop):

```bash
python scripts/gate_b_partition_sanity.py --corpus datasets/pilot.json --provision-only
```

The aggregate reports the partition-size distribution (n_internal/n_external/n_deferred),
collision-zone frequency, cure-recovery rate (cure_ok / collect_ok), fallthrough count,
provisional-flag (false-green) rate, and every errored/excluded repo (no silent caps).

## Go/no-go criterion (plan Step 3, verbatim)

GO if: the collision zone is a small minority of imports; the classifier's
internal/external split matches expectation on spot-checked repos; cure-recovery tracks
Gate A; and the provisional-flag (false-green) rate is low enough to report honestly.
NO-GO if the collision zone is huge, the classifier misroutes, or false-greens are
common — then rethink before the flip.

## Verdict

- **DEFERRED** — measurement not run; verdict pending the run above.
