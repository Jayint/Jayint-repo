# repo2run run — 2026-06-07-repo2run

- Source: `root@167.233.64.96:/opt/rat-bench-integration/rat_run_repo2run`
- Saved: 2026-06-08 · rows: 50 · smoke: no
- **ESSR (paper macro, ÷ executed): 0.6322 (executed 31/50)**
- coverage-penalized (÷ 50): 0.3919 · build-success: 31/50 · 31 success / 19 error

## What this is
**Repaired Repo2Run fork** (bytedance/Repo2Run), driven on **deepseek/deepseek-v4-flash**
via `run_rat_benchmark.py --model repo2run`, num-turn **30** (parity with the RAT and
DockerAgent baselines), honest patched scorer (1800s pytest timeout, no phantom 1.0,
recursive results glob). **Not stock** — the public bytedance/Repo2Run is non-functional
as-published; 8 bugs were fixed (see `patches/repo2run/0001-0005` and
`results/REPO2RUN_PORT_HANDOFF.md`). The paper's reported 44.8% used a different setup/model.

## Run provenance (operational note)
The 50 were produced across the same harness/settings but two scheduler invocations after a
disk incident — methodologically each repo ran exactly once under identical config:
- Initial run scored 41/50, then **stalled**: Repo2Run leaks its agent sandbox containers
  (never torn down), which filled disk to 9.8 GB; the scheduler's refill-on-completion logic
  then froze with one long repo (supabase) holding the last slot.
- Recovery: killed only the leaked containers (traced the live agent's `docker exec` to spare
  the active one), reclaimed ~76 GB, then relaunched the **9 unscored** repos in parallel.
- `feast-dev/feast` + `frappe/press` had 0-byte `_result_row.json` (row-write truncated during
  the disk crisis) → re-run fresh. feast → success (0.986); frappe → error.
- Final: 50/50 rows, all parseable, 0 corrupt.

Contents: `output/<org>/<repo>/` per-repo (_result_row.json, run.log, Dockerfile, junit, …),
`rat_results.json` (aggregate), scheduler log.
