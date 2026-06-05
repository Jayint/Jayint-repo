# TODOS


## [ ] Tiered concurrency for benchmark runner (heavy solo / light high-K)
**What:** Scheduler runs heavy repos at low K (or a solo lane), light repos at high K.
**Why:** Heavy repos (darts, LibreTranslate, OpenManus, markitdown, memU-server, Scrapling) can exceed the 7200s per-repo budget under contention and flip to a false status:timeout. Tiering maximizes throughput while protecting fidelity.
**Pros:** Best wall-clock without contention-induced false timeouts.
**Cons:** More scheduler logic; needs a heavy/light tag per repo (the `size` field already exists in the subset).
**Context:** Eng review 2026-06-05 chose "start K=3, measure first." Once real per-repo timings at K=3 exist, tiering is the throughput-optimal end state. Lives in run_rat_benchmark.py scheduler.
**Depends on:** the K=3 measurement pass.
**Date:** 2026-06-05 | Source: /plan-eng-review
