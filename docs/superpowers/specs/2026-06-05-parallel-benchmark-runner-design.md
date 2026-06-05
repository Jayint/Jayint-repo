# Design: Parallel, fidelity-preserving RAT benchmark runner (local Mac)

- **Date:** 2026-06-05
- **Status:** Approved (design) — pending spec review → implementation plan
- **Topic:** Make the 50-repo RAT Python benchmark run faster on our own machines, without changing what the agent does or what gets scored.
- **Owner:** John
- **Related:** `docs/RAT_BENCHMARK_INTEGRATION_PLAN.md`, `run_rat_benchmark.py`, `eval/models/dockeragent_model.py` (RAT tree)

## 1. Problem

Running our DockerAgent over the 50-repo Python subset is sequential today: `run_rat_benchmark.py` loops over repos one at a time. Task 0 (`resend/resend-python`) took **~8.8 min/repo**, so 50 repos ≈ **~7 hours**. We want this dramatically faster **without** altering the agent's behavior or the scored results (it's a measurement harness — speed must not contaminate fidelity).

## 2. Goals / Non-goals

**Goals**
- Cut wall-clock for the 50-repo run by **3–4×** on the local Mac (target ~1.5–2 h).
- Keep the run **result-neutral**: same agent activities, same scored outputs (within the benchmark's existing run-to-run noise).
- Provide a concurrency knob so the same runner scales if we later move to bigger hardware.

**Non-goals (explicitly out of scope)**
- Moving to a remote Linux box / cloud VM (deferred — decided to use our own machines for now).
- Running the Repo2Run / RAT **baselines** (separate effort).
- Any optimization that changes results: **forcing `-slim` base images**, **lowering `num_turn`**, or otherwise altering agent decisions. These are deliberately excluded.

## 3. Constraints (measured)

| Resource | Value | Implication |
|---|---|---|
| Host | 10 cores (6P+4E), 16 GB RAM, macOS, **arm64** | CPU plentiful; arm64 build platform |
| Docker Desktop VM | 10 CPU, **7.65 GB RAM** | real ceiling for concurrent builds/containers |
| Free disk | **57 GB / 460 GB** | bounds how many images can coexist |
| Per-repo | ~8.8 min; ~2 GB eval image; base images ~1.6–3.2 GB | parallelism + warm cache = the wins |
| multiprocessing | macOS default start method = **spawn** | workers re-import the module; must construct their own model |

## 4. Design

Only **scheduling** and **cache warming** change. The agent, the Dockerfile it produces, the scorers, and per-repo isolation are untouched.

### 4.1 Concurrent executor (`run_rat_benchmark.py`)
- Add `--concurrency K` (default **4**). Replace the sequential loop with a `concurrent.futures.ProcessPoolExecutor(max_workers=K)`.
- A **module-level worker function** takes `(repo_dict, config)`, constructs its **own** `DockerAgentModel` (do not pickle the weave.Model across the spawn boundary), runs `predict()` + the three scorers, writes that repo's JSONs, and returns the result row.
- Under `spawn`, each worker re-imports `run_rat_benchmark` → the top-of-file `os.environ.setdefault("RAT_ROOT"/"DOCKERAGENT_ROOT", …)` re-runs, so env wiring holds per worker. Verify `weave.op`/offline behavior is process-safe (we never `weave.init()`).
- **Resume-skip** preserved (skip repos whose `run_pytest_results.json` already exists). Aggregation + the per-category report run **after** all workers finish (unchanged math). The empty-selection guard stays.

### 4.2 Pre-pull base images (warming, not forcing)
- Before launching workers, `docker pull` the candidate base-image set the agent's `ImageSelector` chooses from — i.e. the Python `language_handler.base_images(platform)` list (the LLM still picks one via `<image>` tags; we only ensure they're already local).
- **Result-neutral:** the agent's choice is unchanged; we only remove multi-GB pulls from the hot path.

### 4.3 Shared layer cache + deferred, scoped cleanup
- One Docker daemon ⇒ builds **automatically reuse** each other's `FROM`/apt/common layers as the run warms — free cross-repo speedup, no config.
- Cleanup stays **per-repo-scoped**: each `predict()`'s `finally` already `rmi`s its own eval image + removes its container; the agent already cleans its sandbox snapshot. **No global prune mid-run** (it would delete layers/images a concurrent build is using).
- Run a single `docker image prune -f` + `docker builder prune -f` **only at the very end** of the batch.

### 4.4 Resource headroom
- Raise Docker Desktop VM RAM to **~12 GB** (host has 16) and confirm disk headroom before a full run. This lifts the safe concurrency ceiling.
- Keep the agent's `command_timeout_seconds` generous so contention cannot trip a timeout that a solo run wouldn't.

### 4.5 Concurrency tuned to the contention risk
- Start **K=4**; observe build/test durations vs the solo baseline. The single fidelity risk is **timeout-under-load** (a build/test that's slow enough under contention to trip a per-command timeout it would pass solo). K is a tunable knob, not "max out."

## 5. Fidelity guarantees

**Preserved identical:** the agent and its prompts, `temperature=0`, the agent's own base-image choice, `num_turn=30`, the eval Dockerfile content, the three scorers, and per-repo isolation (unnamed sandbox container → random unique name; per-slug eval image/container; per-`instance_id` workplace; no global docker cleanup in our path).

**The only behavioral risk** is contention-induced timeouts (§4.5), mitigated by the K cap + VM RAM bump + generous command timeout.

**Pre-existing variance (independent of this change):** the LLM is not bit-deterministic even at temp 0, and repos are cloned at **default-branch HEAD (no pinned SHA)**, so the code itself can drift between runs. Parallelism adds no new variance beyond the contention risk.

**Validation of result-neutrality:** re-run a small sample (e.g. 3–5 repos) sequentially vs. at K=4 and confirm `build_success` / `pytest_collect_success` match and `pytest_pass_rate` agrees within expected LLM/HEAD noise. Any systematic divergence ⇒ lower K or raise timeouts.

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Contention timeouts flip a result | K cap (start 4), bump VM RAM to ~12 GB, generous command timeout, validation sample |
| Disk ceiling (57 GB) under K parallel ~2 GB images | per-repo `rmi` (already present) + final prune; pre-pull shares base layers; monitor `docker system df` |
| OpenRouter rate-limiting under K concurrent agents | K is low (4); add bounded retry/backoff if 429s appear (no result change) |
| `spawn` re-import / pickling weave.Model | worker constructs its own model; no model pickled across processes |
| Teammate in China running on his own machine | his laptop needs a proxy/VPN for OpenRouter + GitHub + Docker Hub; speedup logic itself is identical |

## 7. Success criteria

- 50-repo run completes in **~1.5–2 h** on the Mac (3–4× vs ~7 h) at K=4.
- Validation sample shows **no systematic result change** vs sequential.
- Disk stays under the 57 GB ceiling throughout (verified via `docker system df`).
- `--concurrency` knob works; the runner remains resume-safe and produces the same `rat_results.json` + per-category report shape.

## 8. Open items for the implementation plan

- Exact `ProcessPoolExecutor` wiring + module-level worker signature; progress/logging under concurrency (interleaved stdout — consider per-repo log files).
- Concrete enumeration of the Python `base_images(platform)` candidate set for the pre-pull step.
- Where to bump Docker VM RAM (manual Docker Desktop setting vs documented prerequisite).
- Failure handling: a worker that crashes drops that repo to a recorded `status:error` row (not the whole batch).
- Optional later: persistent pip wheel cache (needs care to stay result-neutral — deferred).
