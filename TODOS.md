# TODOS


## [ ] Tiered concurrency for benchmark runner (heavy solo / light high-K)
**What:** Scheduler runs heavy repos at low K (or a solo lane), light repos at high K.
**Why:** Heavy repos (darts, LibreTranslate, OpenManus, markitdown, memU-server, Scrapling) can exceed the 7200s per-repo budget under contention and flip to a false status:timeout. Tiering maximizes throughput while protecting fidelity.
**Pros:** Best wall-clock without contention-induced false timeouts.
**Cons:** More scheduler logic; needs a heavy/light tag per repo (the `size` field already exists in the subset).
**Context:** Eng review 2026-06-05 chose "start K=3, measure first." Once real per-repo timings at K=3 exist, tiering is the throughput-optimal end state. Lives in run_rat_benchmark.py scheduler.
**Depends on:** the K=3 measurement pass.
**Date:** 2026-06-05 | Source: /plan-eng-review

## [ ] Decide: pin repo SHAs for the RAT benchmark (fidelity vs RAT-parity)
**What:** Clone each benchmark repo at a pinned commit SHA instead of default-branch HEAD.
**Why:** Codex eng-review (2026-06-05) flagged unpinned HEAD as the single biggest source of invalid run-to-run comparison — bigger than any parallelization detail. A repo's code (and tests) can change between runs, so a pass-rate delta may reflect upstream drift, not the agent.
**Pros:** Reproducible, comparable runs; isolates agent quality from upstream churn.
**Cons:** Diverges from how RAT itself runs (its dataset carries no SHA), so head-to-head vs released RAT/Repo2Run baselines would no longer be apples-to-apples. Need to pick + store SHAs.
**Context:** Surfaced during /plan-eng-review outside voice on the parallel-runner spec. This is a benchmark-semantics decision, not a perf detail — keep it out of the parallel-runner scope. Record chosen SHAs in the subset JSON if adopted.
**Depends on:** none (orthogonal to the scheduler).
**Date:** 2026-06-05 | Source: /plan-eng-review (codex outside voice)
