# Running the RAT benchmark

How to run `run_rat_benchmark.py` (DockerAgent + RAT/Repo2Run baselines) and score the results.

## Prerequisites

- **Docker** running (the eval builds real images and runs pytest in containers).
- **Python env** with deps: `uv venv && source .venv/bin/activate && uv pip install -r requirements.txt`.
- **`.env`** with the LLM gateway creds (loaded automatically via `dotenv`):
  ```
  OPENROUTER_API_KEY=sk-or-...
  OPENROUTER_API_BASE=https://openrouter.ai/api/v1
  OPENROUTER_PROVIDER=Alibaba          # provider pin for deterministic routing
  LLM_API_PROVIDER=openrouter
  ```
- **RAT harness** (scorers + model file) present at `<repo>/runanything/src` (auto-detected).
  Otherwise set `RAT_ROOT=/path/to/runanything/src` explicitly.

## Run

```bash
export RAT_PYTEST_TIMEOUT=1800            # per-repo pytest timeout inside the eval container
python run_rat_benchmark.py \
  --model dockeragent \                   # dockeragent (ours) | rat | repo2run (baselines)
  --repos-json datasets/rat_python_hard_subset.json \
  --root-path ./rat_run_myrun \           # all outputs + rat_results.json go here
  --llm deepseek/deepseek-v4-flash \
  --concurrency 12 \                      # run N repos in parallel (scheduler mode)
  --num-turn 30 \                         # max agent turns per repo
  --timeout 7800 \                        # per-repo wall-clock timeout (s)
  --repair-mode runner --repair-rounds 2  # runner-side repair loop (see below)
```

**`--repair-mode`**: `runner` (Repo2Run-style runner loop, agent self-verify off) ·
`selfverify` (agent self-verify, default) · `both` (debug) · `off` (clean baseline).

### Useful flags

| flag | effect |
|---|---|
| `--only owner/repo` | run **one** repo and exit (worker mode; no `rat_results.json`) |
| `--tier smoke\|extended` · `--category <cat>` | filter the dataset |
| `--limit N` · `--offset N` | run a slice |
| `--aggregate-only` | re-build `rat_results.json` from existing rows, no runs |
| `--prune` | delete `dockeragent-eval-*` images and exit |

### Output layout

```
rat_run_myrun/
├── rat_results.json                       # aggregate {runner_commit, rows[]}
└── output/<owner>/<repo>/
    ├── run.log                            # full agent (ReAct) trace
    ├── _result_row.json                   # status, pytest_pass_rate, pytest_executed, ...
    ├── run_pytest_results.json            # raw pytest result (parse_method, summary)
    ├── eval_build/Dockerfile              # the environment that was scored
    └── <owner>__<repo>.json               # synthesized recipe (build_commands, test cmds)
```

## Score

```bash
python scripts/compute_essr.py \
  dockeragent=rat_run_myrun \
  rat=rat_run_rat_corrected \
  repo2run=rat_run_repo2run
```

Prints a comparison table and writes `results/essr_recompute.json`:

```
agent          n  EBSR (build+exec)  ESSR ÷all  ESSR ÷exec  coverage      full_pass  hollow   micro
dockeragent   50  25/50 = 0.50          0.2048      0.2498  41/50 = 0.82          4       7  0.7962
rat           50  46/50 = 0.92          0.6233      0.6775  46/50 = 0.92         16       6  0.8152
repo2run      50  31/50 = 0.62          0.3919      0.6322  31/50 = 0.62          5      10  0.7315
```

| metric | meaning |
|---|---|
| **EBSR (build+exec)** | Repo2Run-bench metric: Dockerfile built **and** pytest executed — **pass not required**. Excludes `build_failed` stubs. |
| **ESSR ÷all** | RATBench headline: mean per-repo **pass-rate** over **all** repos (setup-failure = 0). |
| **ESSR ÷exec** | mean pass-rate over **executed** repos only (÷exec deviation — flatters low coverage). |
| **coverage** | repos that produced a results file ÷ all (**includes** `build_failed` stubs, so ≥ EBSR). |
| **full_pass** | repos passing ~100% of tests (pass_rate ≥ 0.999). |
| **hollow** | repos that pytest-**collected** OK but pass_rate < 0.5 (hollow env / false-pass). |
| **micro** | Σ passed / Σ effective tests over executed (test-weighted). |

> **EBSR vs ESSR:** EBSR asks *"did the env build and tests run?"*; ESSR asks *"what fraction of tests pass?"*.
> The same hollow environment can score EBSR-success and ESSR≈0 — use **ESSR ÷all + coverage** as the headline.

## Running on the VM (operational note)

The benchmark is long-running. Launch it **detached via a script file on the box** (`setsid … & ; echo $! > pidfile`),
not as an inline SSH command. **Never** run `pkill -f run_rat_benchmark` over SSH — it matches the SSH session's own
argv and kills your connection mid-command. Put any kill inside a script file (whose name doesn't contain that string).
