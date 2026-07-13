# Unified Benchmark Runner — Design Spec

- **Date:** 2026-07-11
- **Status:** Approved (design); pending implementation plan
- **Owner:** John
- **Supersedes (scoring paths of):** `run_rat_benchmark.py` + the per-model `predict()` measurement in `eval/models/{dockeragent,rat,repo2run}_model.py`
- **Architecture:** offline **harvest** (agents write Dockerfiles to disk; `bench/` measures them). Chosen over in-process import and subprocess-orchestration after a design review — see §3.1.

## 1. Problem

Today `run_rat_benchmark.py` picks a model (`dockeragent` / `rat` / `repo2run`) and calls its
`predict(repo)`. The three models do fundamentally different work **and measure themselves
differently**, so cross-agent numbers are not comparable:

- **v3 / dockeragent** — returns a Dockerfile string; the *harness* builds it fresh and runs the
  pytest tools. Measurement is harness-owned, uniform.
- **repo2run** — configures a live container, emits a Dockerfile via `integrate_dockerfile`; harness
  builds + runs the pytest tools. Mostly harness-owned.
- **rat** — runs its `CodeAgent` **inside a live container** and the *agent itself* runs pytest via an
  LLM **language-router** (npm / cargo / pytest); the harness copies out whatever the agent produced.
  Measurement is agent-owned, non-uniform.

Because environment-production and measurement are entangled, we have observed (this session, on the
`rat_python50` runs):

1. **Language misrouting** — RAT's classifier routed 11/50 "python" repos to `npm`/`cargo`, so they
   never ran pytest and silently dropped from `ESSR ÷exec`.
2. **Floating denominator** — pass-rate is `passed / (collected − skipped)`, and collection is a
   function of env completeness, so each agent is graded on a different denominator for the same repo
   (markitdown collected 50 vs 336; anthropic-sdk 4168 vs 4178).
3. **÷exec vanishing** — repos with no results file disappear from the average, and the two baselines
   lose different repos for different reasons.
4. **EBSR vs collect-clean divergence** — our `EBSR` (build + pytest-executed, collection errors
   tolerated) is looser than Repo2Run's own gate (`pytest --collect-only` rc ∈ {0,5}); adopting the
   stricter gate halves scores and flips the v3-vs-RAT ranking.

The fix is to **decouple environment-production from measurement**: agents only produce environments
(Dockerfiles on disk); one measurement tool scores all of them identically.

## 2. Goals / Non-goals

**Goals**
- One measurement path, byte-identical flags, for every agent. An agent can never run its own pytest in
  the measured path.
- No repo ever silently vanishes from the headline denominator.
- Capture enough per-env data (collected + passed node-ids) that a fixed gold denominator can be applied
  **retroactively, with no re-runs**.
- Report the full gate panel side by side: EBSR, ESSR÷all, collect-clean, real≥0.8, micro; plus economy
  metrics (image size, tokens/success, rebuild-ok-rate).
- **Preserve the "one folder = one agent branch" workflow.** `bench/` must have zero agent imports and
  its own branch/folder, so agents stay independently pull/pushable.
- Simple enough that the whole pipeline is readable in a handful of files.

**Non-goals (this version)**
- Building the per-repo gold test set (curation is its own effort — see
  `swesmith-gold-manifest-investigation`). We capture the inputs for it; we do not build it here.
- Orchestrating agent *runs*. `bench/` never invokes an agent. Agents run exactly as they do today, in
  their own folders/branches/venvs, and drop artifacts on disk.
- Changing the dataset, base-image selection, or any agent's internal logic beyond having it write a
  Dockerfile + a small meta file to its run-output dir.

## 3. Locked design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Environment artifact | **Dockerfile, rebuilt fresh** | Most inspectable; the fresh rebuild is itself a reproducibility gate that catches phantom / non-reproducible envs. |
| Denominator | **Uniform now, fixable later** | Measure identically now; capture node-id lists so a pinned gold set can be applied retroactively. |
| Production ↔ measurement coupling | **Offline harvest** | `bench/` reads Dockerfiles off disk and never imports or invokes an agent — keeps each agent independently pull/pushable and dodges 3-way dependency collisions. |

### 3.1 Why harvest (design review)

Three candidate couplings were weighed against the "one folder = one agent branch, easy pull/push"
priority:

1. **In-process import** — `bench` imports each agent. Rejected: couples `bench` to every agent branch
   and loads three agents' conflicting deps into one interpreter.
2. **Subprocess-shim orchestration** — `bench` invokes each agent from its own folder/venv via a
   `*_ROOT` env var. Avoids dep collisions, but `bench` still has to know each agent's invocation
   contract (CLI, venv path, cwd, timeouts, container cleanup) — re-centralizing orchestration the user
   doesn't need and re-introducing subprocess failure modes.
3. **Offline harvest (chosen)** — `bench` never invokes an agent. Each agent runs exactly as today and
   writes `Dockerfile` + `bench_meta.json` to its per-repo output dir; `bench` discovers and measures
   those artifacts. Zero agent imports, zero subprocess plumbing, and it can re-measure runs already on
   disk (`/opt/runs/.../eval_build/Dockerfile`) with no re-run — directly serving "control the final
   data collection fairly."

## 4. Architecture

Three phases. Phase 1 happens **inside each agent's own run** (not in `bench`); phases 2–3 are `bench`.

```
Phase 1  PRODUCE    (each agent, in its own folder/branch/venv — writes Dockerfile + bench_meta.json to disk)
Phase 2  MEASURE    harvest -> measure(env) -> MeasureRow     # bench: fresh build + identical pytest
Phase 3  AGGREGATE  compute_metrics(rows)   -> {EBSR, ESSR, ...}   # bench: pure, from rows
```

**Directory layout** — `bench/` is its own branch/folder (e.g. `/opt/bench`, branch `bench`), no agent deps:

```
bench/
  unified_bench.py     # orchestrator + CLI: harvest -> measure -> write row.json -> aggregate; resume; concurrency
  schema.py            # RepoSpec, HarvestedEnv, MeasureRow
  harvest.py           # discover(agent_roots) -> [HarvestedEnv]   (reads Dockerfile + bench_meta.json off disk)
  docker_client.py     # SubprocessDocker (build / image_size / run / exec / rm)
  measure.py           # parse_junit, parse_collect, measure(env) -> MeasureRow   (single source of truth)
  metrics.py           # compute_metrics(rows) / compare(agents)   (pure)
```

`measure()` drives `pytest` directly (per §6) and parses the JUnit itself — it does **not** depend on
RAT's `run_pytest.py`. Before collecting, it does a best-effort
`pip install --break-system-packages pytest pytest-timeout` inside the container so the measurement
tooling is present uniformly regardless of the agent's Dockerfile, and so the PEP-668
externally-managed-environment failure (observed in the old harness) cannot recur.

## 5. Phase 1 — the agent contract (file-based)

An "agent" is unchanged except that, per repo, it writes two files into its run-output dir. `bench`
never imports or invokes agent code — the contract is these files, not a Python interface.

```
<agent_run_dir>/<owner>/<repo>/
  Dockerfile            # + any COPY'd files (setup.sh, ...). Must clone the repo into /testbed.
  bench_meta.json       # {tokens_in, tokens_out, llm_calls, turns_used, produce_s,
                        #  base_image, head_sha, agent_commit, status}
```

`harvest.discover(agent_roots: dict[str, str])` walks each agent's run dir and yields:

```python
@dataclass(frozen=True)
class HarvestedEnv:
    agent: str
    repo: RepoSpec
    dockerfile: str | None          # None => no Dockerfile found (status="missing")
    setup_scripts: dict             # sibling files the Dockerfile COPYs
    base_image: str | None
    status: str                     # "ok" | "missing"
    meta: dict                      # loaded from bench_meta.json (cost keys None if absent)
```

**Contract details:**
- The Dockerfile clones the repo into `/testbed` (the fixed in-container measurement path — `measure()`
  runs every pytest command with `-w /testbed`).
- `bench_meta.json` is optional-tolerant: missing keys → `None` (never `0`), so a non-reporting agent
  can't look like a zero-cost win.
- `agent_commit` records provenance so a stale Dockerfile is attributable.

**Per-agent emitters (live in each agent's OWN repo, not in `bench`):**
- **v3** — already writes `eval_build/Dockerfile` + `_meta.json`; add a thin step to also write
  `bench_meta.json` (or point `harvest` at `_meta.json` via a small field map).
- **repo2run** — after `integrate_dockerfile`, write the Dockerfile + `bench_meta.json`.
- **rat** — RAT mutates a live container, so its emitter renders a Dockerfile by replaying RAT's
  recorded `outer_commands.json` from its base image (`render_dockerfile(base, url, commands)`), then
  writes it + `bench_meta.json`. **Consequence:** if RAT's mutations don't replay cleanly from scratch,
  that repo fails the fresh build (EBSR=False) — the reproducibility gate doing its job. If replay
  proves too lossy, a RAT-only committed-image fallback flagged `unreplayed` in `bench_meta.json` is the
  escape hatch; default is honest replay.

## 6. Phase 2 — measurement (single source of truth)

`measure(env)` is the *only* place tests ever run, with **byte-identical commands for every agent**:

```
# 0. fresh rebuild (also the reproducibility gate)
docker build -t bench-<agent>-<repo> <ctx>                 -> build_ok = (rc == 0)
docker run -d -w /testbed <img> tail -f /dev/null

# 0.5 ensure measurement tooling uniformly (dodges PEP-668; independent of the agent's Dockerfile)
python -m pip install -q --break-system-packages pytest pytest-timeout \
  || python -m pip install -q pytest pytest-timeout || true

# 1. collect-clean gate (Repo2Run's own criterion)
python -m pytest --co -q /testbed                          -> collect_rc; collect_clean = rc in {0,5}

# 2. maximal collectable node-id set (for the fixable-later denominator)
python -m pytest --co -q --continue-on-collection-errors /testbed  -> collected_node_ids

# 3. THE authoritative test run — serial, scorer-faithful flags
python -m pytest -q --continue-on-collection-errors \
       --junit-xml=/testbed/logs/junit.xml \
       [--timeout=120 --timeout-method=signal  if pytest-timeout present]
# bounded by a coarse `timeout` backstop; deliberately NOT -n auto (the scorer runs serial)

# 4. cost probes (negligible; feed §7.5)
docker image inspect <img> --format '{{.Size}}'            -> image_size_mb, image_delta_mb (vs base)
docker exec <c> python -m pip list --format=freeze | wc -l -> installed_pkg_count  (best-effort)
```

Node-ids come from parsing the JUnit XML per `<testcase>` (child `<failure>`/`<error>`/`<skipped>` →
outcome; none → passed).

### Collect return code is DATA, never a gate

A nonzero collect rc is recorded (it *is* the collect-clean signal) but **never aborts measurement**.
We always proceed to the authoritative test run; `--continue-on-collection-errors` runs everything that
collected and reports the rest as error nodes. `executed` is decided by the test run producing a
parseable JUnit — **not** by the collect rc.

| collect_rc | collect_clean | action | typical outcome |
|---|---|---|---|
| 0 | ✅ | run tests | full pass/fail data |
| 2 (collection error, some collected) | ❌ | **still run tests** | real pass/fail on the collectable set + error nodes for broken modules (anthropic, markitdown) |
| 5 (no tests) | ✅ | run tests | junit with 0 tests → `total=0` → `pass_rate=0.0` (honest rule, not phantom 1.0) |
| 4 / 3 (usage / internal error) | ❌ | still try | test run often also errors → no junit → `executed=False` |

Only the genuinely-empty case (no node-ids from either collect pass **and** no parseable junit) yields
`executed=False`; the repo is still counted in `n`. Alongside `collect_rc` we store `collect_errors`
(exception types + failing modules scraped from the collect traceback) for diagnosis and to feed the
future gold-set work.

When a gold set is later applied, a broken collection scores low automatically — fewer tests ran →
fewer `passed_node_ids` → smaller `|passed ∩ G|` — with no special-casing.

### MeasureRow schema

```python
@dataclass(frozen=True)
class MeasureRow:
    agent: str; repo: str
    env_status: str                       # HarvestedEnv.status: ok | missing
    build_ok: bool; build_log_tail: str
    collect_rc: int | None
    collect_clean: bool                   # Repo2Run gate: rc in {0,5}
    collect_errors: tuple[str, ...]       # exception types + failing modules (diagnostic)
    collected_node_ids: tuple[str, ...]   # maximal collectable (pass 2)
    executed: bool                        # junit produced & parsed
    total: int; passed: int; failed: int; errors: int; skipped: int
    passed_node_ids: tuple[str, ...]
    failed_node_ids: tuple[str, ...]
    error_node_ids:  tuple[str, ...]
    ebsr: bool                            # build_ok AND executed
    pass_rate: float                      # passed / (total − skipped)   [per-env; gold-fixable later]
    timed_out: bool
    # cost / efficiency (§7.5). None (never 0) when the agent's bench_meta did not report it.
    image_size_mb: float | None
    image_delta_mb: float | None
    installed_pkg_count: int | None
    tokens_in: int | None; tokens_out: int | None
    llm_calls: int | None; turns_used: int | None
    produce_s: float | None; build_s: float | None; test_s: float | None
    meta: dict                            # image tag, per-test-timeout used, agent_commit, ...
```

Every field except the `meta`/cost passthroughs is produced by identical commands regardless of which
agent made the Dockerfile.

## 7. Phase 3 — metrics

Pure functions over `MeasureRow`s for one agent. `n` is **always the full repo set**:

```
EBSR          = mean(build_ok AND executed)              ÷ n
collect_clean = mean(collect_clean)                      ÷ n     # Repo2Run's own gate
ESSR÷all      = mean(pass_rate)                          ÷ n     # HEADLINE (uncollected/build-fail = 0)
real_success  = mean(ebsr AND pass_rate ≥ 0.8)           ÷ n     # agent-goal bar
micro         = Σpassed / Σ(total − skipped)  over executed
ESSR÷exec     = mean(pass_rate over executed) ÷ n_exec           # reported, flagged non-comparable
gold_ESSR     = mean(|passed_node_ids ∩ G_repo| / |G_repo|) ÷ n_gold   # only when a gold manifest is supplied
```

`compare(agents)` prints them side by side.

### Anti-vanish rule (error handling)

Every repo contributes to `n`, always:

| failure | row state | contribution |
|---|---|---|
| no Dockerfile harvested | `env_status=missing`, build_ok=F, executed=F | 0 to every ÷all gate |
| `docker build` failed | build_ok=F, ebsr=F | 0 |
| collect / pytest crashed or timed out | executed=F, `timed_out=T` | 0 |

Distinct statuses are kept for diagnosis, but the headline denominator is never anything but `n`. This
structurally removes the "16 repos vanish from ÷exec" pathology.

## 7.5 Efficiency & Economy

The correctness gates (§7) say whether an env works; these say what it cost. Per-repo fields are captured
in `MeasureRow` (§6, some passed through from `bench_meta.json`); aggregated per agent below.

| metric | formula | reads as |
|---|---|---|
| `mean_image_delta_mb` | mean(`image_delta_mb`) | env weight the agent *added* (base excluded — bases differ) |
| `mean_installed_pkgs` | mean(`installed_pkg_count`) | over-install tendency |
| `mean_tokens` / `mean_tokens_out` | mean over reporting repos | raw LLM cost per repo (output is the expensive half) |
| `tokens_per_ebsr` | Σtokens / `n_ebsr` | tokens per built+ran env |
| **`tokens_per_real_success`** ⭐ | Σtokens / `n_real≥0.8` | **cost per genuine win — economy headline** |
| `mean_turns` | mean(`turns_used`) | reasoning budget consumed |
| `mean_produce_s` / `wall_s_per_real_success` | timers | speed; wall-clock per win |
| **`rebuild_ok_rate`** ⭐ | mean(`build_ok`) | **reproducibility — fraction whose Dockerfile rebuilds fresh** |
| `unreplayed_rate` | RAT fallback count / `n` | how often RAT needed the committed-image escape hatch |

Two economy headlines: **`tokens_per_real_success`** and **`rebuild_ok_rate`** (does the emitted env
actually reproduce — the fairness gate from §3, most load-bearing for RAT).

**Reading rules — enforced in `compare()` output:**
- Every `*_per_success` / `*_per_ebsr` metric divides by successes, so an agent that attempts
  fewer/easier repos looks cheaper. **Always print next to `n_real_success` and the success rate;
  never standalone.**
- Prefer `image_delta_mb` over absolute size (base fixed at `python:3.13-slim`).
- Token/turn values come from each agent's `bench_meta.json`. If absent, the value is **`None`, not
  `0`** — a non-reporting agent must not appear as a zero-cost win. Aggregates skip `None`s and note the
  reporting-repo count. v3 already emits `[Tokens]`; RAT + repo2run emitters must fill these in.

## 8. Testing (TDD — tests first)

- `metrics.py` — table-driven over synthetic rows; every gate incl. gold intersection **and the §7.5
  efficiency aggregates** (assert `*_per_success` denominators + that `None` cost values are skipped).
  Pure, trivially 100%.
- `measure.py` JUnit parser — fixtures of *real* JUnit XML (collection errors, skips, parametrized,
  timeout-failed) → asserted node-id sets + counts. The correctness core.
- collect-rc handling — fixtures for rc ∈ {0, 2, 4, 5} asserting `collect_clean` + that the test run is
  still attempted.
- `harvest.py` — a temp dir with `Dockerfile` + `bench_meta.json` (and a missing-Dockerfile case) →
  asserted `HarvestedEnv`s incl. `status="missing"` and `None` cost passthrough.
- `measure()` orchestration — injected fake `DockerClient` scripting build-fail / collect-rc2 / executed
  / timeout paths → asserted `MeasureRow`.
- e2e smoke — one tiny repo (itsdangerous) through measure on real Docker, behind a `slow` marker;
  asserts EBSR True + pass_rate in range.

## 9. Orchestration

```
# measure fresh harvests from three agent run dirs
python -m bench.unified_bench \
  --harvest v3=/opt/runs/v3_run,repo2run=/opt/runs/r2r_run,rat=/opt/runs/rat_run \
  --out /opt/bench/run_x --concurrency 4
# re-aggregate (and apply a gold set) without re-measuring
python -m bench.unified_bench --out /opt/bench/run_x --aggregate-only [--gold gold.json]
```

Output layout:

```
/opt/bench/run_x/
  <agent>/<owner>/<repo>/
    Dockerfile          # the harvested artifact (copied for provenance)
    row.json            # the MeasureRow
    build.log, junit.xml
  metrics.json          # per-agent gate + economy panel
```

Resumable (skip existing `row.json`); concurrency via thread pool. Because harvest reads Dockerfiles off
disk, `bench` can also point `--harvest` at **existing** run dirs (`/opt/runs/.../eval_build`) to score
past runs uniformly with no re-run.

## 10. Migration & rollout

- New `bench/` package on its **own branch/folder** (`/opt/bench`); no agent imports; `measure()`
  drives `pytest` directly (no dependency on RAT's `run_pytest.py`).
- Each agent's repo gets a tiny per-repo emitter (`Dockerfile` + `bench_meta.json`). v3 is nearly free
  (already writes both artifacts); repo2run + rat add a small writer; rat also adds
  `render_dockerfile`.
- The per-model `predict()` scoring paths in `run_rat_benchmark.py` are retired once a shared 50-repo
  harvest reproduces the old EBSR within noise.
- First payoff: run `bench` over the Dockerfiles already in `/opt/runs/*/eval_build/` to get uniform
  numbers on existing runs immediately.

## 11. Future (out of scope here)

- Build the pinned per-repo gold node-id set `G_repo` and pass it via `--gold`; `gold_ESSR` then becomes
  the headline. See `swesmith-gold-manifest-investigation` and `essr-denominator-is-agent-chosen`.
- Optional `--collect-clean-as-ebsr` flag to report the Repo2Run-strict gate as the primary EBSR.
