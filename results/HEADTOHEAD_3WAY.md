# 3-way agent baseline — RAT vs DockerAgent vs Repo2Run

Curated 50-repo Python "hard subset" of RunAnyThing. Same model (deepseek/deepseek-v4-flash),
same harness (`run_rat_benchmark.py`), same honest patched scorer (1800s pytest timeout, no
phantom-1.0, recursive results glob), num-turn **30** for all three. Date: 2026-06-07.

| metric | RAT | DockerAgent | Repo2Run |
|---|---:|---:|---:|
| build-success (/50) | **50** | 32 | 31 |
| executed (/50) | **46** | 32 | 31 |
| **ESSR** (macro pass-rate ÷ executed) | **0.6775** | 0.3729 | 0.6322 |
| coverage-penalized (macro ÷ 50) | **0.6233** | 0.2387 | 0.3919 |

Sources: `results/rat/2026-06-07-corrected`, `results/dockeragent/2026-06-07-baseline`,
`results/repo2run/2026-06-07-repo2run`. Per-repo matrix: `results/headtohead_3way.csv`.

## Read

**ESSR (when an env is built, do its tests pass?):** RAT 0.68 ≳ Repo2Run 0.63 ≫ DockerAgent 0.37.
Repo2Run is competitive with RAT *on the repos it manages to configure* — its environments,
when they work, pass tests at nearly RAT's rate, and clearly above DockerAgent.

**Coverage is where it separates.** RAT executes 46/50; Repo2Run and DockerAgent only ~31–32/50.
So on the coverage-penalized score (credit only for repos that actually ran tests), **RAT 0.62
dominates**, Repo2Run 0.39 is second, DockerAgent 0.24 last. RAT's edge is breadth: it sets up
far more environments successfully (build-success 50/50 vs ~31–32).

**Coverage overlap (executed repos):**
- Repo2Run executed but RAT did not (2): `bruin-data/ingestr`, `rayai-labs/agentic-ray`.
- RAT executed but Repo2Run did not (17): the heavy ML/framework repos — `Peterande/D-FINE`,
  `lyuwenyu/RT-DETR`, `aiidateam/aiida-core`, `ModelEngine-Group/nexent`, `supabase/supabase-py`,
  `scylladb/scylla-cluster-tests`, `nginx-proxy/nginx-proxy`, `swar/nba_api`, etc.

## Caveats
- **Repo2Run is a repaired fork**, not stock (public bytedance/Repo2Run is broken-as-published;
  8 bugs fixed — see `REPO2RUN_PORT_HANDOFF.md` + `patches/repo2run/`). Paper's 44.8% used a
  different setup/model, so this is not a paper-reproduction.
- Repo2Run's 50 were produced across two scheduler invocations after a disk incident (its agent
  sandboxes leak and filled disk); each repo still ran exactly once under identical config. See
  the run README for the recovery detail.
