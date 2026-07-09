# Service Obligations: graph-asserted, host-certified, agent-discharged

**Date:** 2026-07-09
**Status:** Design — approved in discussion, not yet planned/implemented
**Branch:** john-v3-multi-lang (shared; local commits only)
**Supersedes:** the eager exotic-LLM service-recipe path (`service_translate` at construction)

---

## 1. Motivation

Repos need live backing services (postgres/redis/etc.) to run their tests. Our forensic study of the
two newest MiniMax-M3 full-50 baseline runs (`.superpowers/sdd/baseline-service-failure-analysis.md`)
found that baseline agents fail at this for **structural, not capability** reasons:

- Both RAT and repo2run independently produced the *correct* redis recipe on rq/rq
  (`apt install redis-server` → `--daemonize yes` → `redis-cli ping` → PONG). The capability exists.
- **RAT** is blocked *upstream*: base-image/language misclassification steered 7/16 service repos onto
  a Node track; the service was never discovered.
- **repo2run** is blocked *downstream*: its Dockerfile is a 1:1 bash transcript, so **0/34 images
  contain any `ENTRYPOINT`/`CMD`** — a daemon started in a `RUN` layer is dead at test time.

v3's edge is therefore **comprehension**, not orchestration: the graph *knows* the service from the
repo's own files before anything fails. Proven: rq/rq **1/470 → 683 passed** with a live redis.

Our own current weakness is the mirror image: service *recipes* are hardcoded per kind, and unknown
kinds fall to an eager LLM translation at construction time — which hallucinated
`packages.valkey.io`, aborted `setup.sh` under `set -e`, and **regressed rq from 1/470 to
`build_failed`**.

This spec removes that failure class by changing *what the graph emits*.

## 2. Core design: the graph emits **obligations**, not recipes

Static analysis can recover everything about a service *except* the shell commands that install and
start it in a particular base image — because the repo declares an **image**, never an apt recipe.
That single missing fact is the entire source of the hardcoded table.

So the graph stops emitting commands and emits a typed, verifiable **obligation**:

```
ServiceObligation {
  id:         "service:clickhouse"
  image:      "clickhouse/clickhouse-server:24"   # declared (compose / CI)
  kind:       "clickhouse" | null                 # normalized; null when unrecognized
  endpoint:   "localhost:8123"                    # from ports:
  config:     {user, db, password}                # from environment: / env:
  seed:       ["init.sql"]                        # from volumes: (initdb.d)
  ready_when: "wget -q --spider http://localhost:8123/ping"   # THE CERTIFICATE
  phase:      {provision: "build", activate: "runtime"}
  after:      ["service:postgres"]                # from depends_on:
}
```

No shell. It is **a goal plus a test for that goal**, derived entirely from the repo's own files.

### 2.1 Three roles

> **The graph asserts obligations. The host certifies them. The agent discharges them.**

This extends the existing project invariants (graph certifies *necessary*, tests certify *sufficient*,
host owns *done*).

### 2.2 Two-rung resolver

Obligations are turned into commands by a resolver with two rungs. **Both rungs emit the same
artifact shape**, so downstream never learns which rung fired.

- **Rung A — curated recipe** (`service_recipes._KIND_BASE`): head kinds only. Deterministic, no LLM,
  correct on the first pass.
- **Rung B — the agent**: an unresolved obligation is rendered as a hint brief and discharged by the
  react arm at repair time, verified by `ready_when`.

**The curated table is a memoization, not architecture.** Delete it and the system still works — the
agent solves every obligation from the hint, just slower. That is the test this design must pass.

## 3. The script contract: one source, two phases, two artifacts

| Layer | What exists | Why |
|---|---|---|
| **Source** (agent-editable) | **ONE** `setup.sh` with `# --- PROVISION ---` and `# --- ACTIVATE ---` sections | Preserves the arm's single-artifact `patch` contract; install and start are coupled reasoning |
| **Repair-time execution** | Host splits on the delimiter, runs the halves as **two steps** → two verdicts | A dead service becomes a *red probe*, not an aborted build (localization + fail-soft) |
| **Grade-time materialization** | **TWO** artifacts: PROVISION → build `RUN`; ACTIVATE → `services_start.sh` as `ENTRYPOINT` | Docker build layers do not preserve running processes |

**Invariant:** the *same* ACTIVATE text is exercised at repair time and baked as the graded
`ENTRYPOINT`. Repair-time green therefore implies grade-time green.

Repair-time works with one live container because the arm's `Sandbox` is persistent — verified:
`entry.py:33-51` closes `reset`/`run_script`/`certify`/`exec_readonly`/`run_tests` over **one**
`sandbox`, and `run_tests()` is `sandbox.exec_readonly(VERIFY_TEST_CMD)`. A daemon started by
ACTIVATE is alive when `run_tests` fires.

## 4. Cold-start rendering

Each obligation renders according to what we know about it. **The active lines of a build script
contain only what we are confident in; everything else is a comment plus an obligation.**

| Obligation state | PROVISION | ACTIVATE | `graph_context` | Observation |
|---|---|---|---|---|
| **Resolved** (Rung A) | install, fail-soft | start + probe (bounded wait) | listed | probe verdict |
| **Unresolved, certifiable** (no recipe, has `ready_when`) | commented hint | **probe only** (single-shot) + commented stub | listed **UNSATISFIED** + hint brief | probe **RED** |
| **Unresolved, uncertifiable** (no `ready_when`) | commented hint | *nothing* | listed **DECLARED, UNVERIFIABLE** | — |

### 4.1 Emitting a probe without a start command

We do **not** need to know how to *start* a service to know how to *check* it — `ready_when` came from
the repo's own healthcheck. For an unresolved-but-certifiable obligation we emit the probe with **no
start command**; it fails, and that red verdict *is* the host certifying "obligation unsatisfied."

This is what lets "graph asserts / host certifies" hold even for services we cannot provision.

Practical detail: **single-shot probe when nothing was started** (we are measuring, not waiting). The
bounded retry loop (`for i in $(seq 1 N); do <probe> && break; sleep 1; done`, non-exiting — see the
`render_probe_poll` exit-0 bug fixed in cc873a3) applies only when a daemon was actually launched.

### 4.2 Ordering and seeding (consuming `after:` and `seed:`)

ACTIVATE renders obligations in topological order of their `after:` edges (from compose
`depends_on:`), so a service starts only once its dependencies are probe-green. Ordering is derived
from the declaration — never hardcoded.

`seed:` steps (e.g. `docker-entrypoint-initdb.d` SQL) run **inside ACTIVATE, after that service's
probe goes green** — never in PROVISION. Schema seeding requires a *running* daemon, so it is
categorically runtime work. This is the trap that makes "install ≠ ready" concrete: a repo whose
`init.sql` ran at build time against a dead postgres has an empty database at test time.

### 4.3 The cold-start stub (normative format)

```bash
# ── [service:clickhouse] UNRESOLVED ─────────────────────────────
# Declared: image clickhouse/clickhouse-server:24
# Must answer at: localhost:8123
# Ready when:     wget -q --spider http://localhost:8123/ping
# Constraint:     no third-party apt repos, no URL downloads
# TODO: install (in setup.sh PROVISION) + start here, then the probe below must pass.
# ────────────────────────────────────────────────────────────────
wget -q --spider http://localhost:8123/ping   # single-shot: reports the obligation
```

## 5. Three channels carry an obligation to the agent

| Channel | Job | Agent can lose it? |
|---|---|---|
| **`graph_context`** (planner prompt) | **Authoritative** — regenerated from the graph every turn | No — host-owned |
| **The probe** (in ACTIVATE) | **Measurement** — reports the obligation unsatisfied | No — host runs it |
| **Commented stub** (in the script) | **Anchor** — shows where the fix goes, hint inline | Yes — it is only text |

The stub alone is insufficient: `patch` rewrites the whole script, so the agent could drop the
comment. `graph_context` is regenerated from the graph each turn and cannot be forgotten.

### 5.1 The hint brief (normative content)

Rendered from the obligation into `graph_context`:

> This repo needs a service equivalent to **`clickhouse/clickhouse-server:24`**, answering at
> **localhost:8123**, configured with **{user, db, password}**. You will know it is up when
> **`wget -q --spider http://localhost:8123/ping`** returns 0. Install it (PROVISION) and start it as
> a background daemon (ACTIVATE).

## 6. Loop integration

Current (verified `loop.py:32-47`): `reset → run_script → certify → run_tests → gate`.

New — **one injected step**:

```
reset
→ run_script(PROVISION)     # best-effort install, fail-soft
→ activate(ACTIVATE)        # NEW: start daemons + probe → probe_verdict per obligation
→ certify(install layers)   # unchanged: EXECUTION_LAYER_ORDER minus Layer.TESTS
→ run_tests                 # unchanged: single authoritative run
→ gate                      # UNCHANGED: rc==0 AND tests ≥80%
```

Changes, by file:

- **`loop.py`** — add `activate` to the injected callables in `run_react(...)` (line 28). Inside
  `rerun(s)`: `probe = activate() if r.ok else None`; return `(r, probe, g)`. Extend
  `_observation(result, test, probe)` (line 22) to render per-service probe verdicts.
- **`entry.py`** — `docker_adapters(sandbox)` (line 33) gains `activate()` over the *same* sandbox.
  Replace `ctx = None` (line 75) with a `service_graph_context(graph) -> str` helper — exactly the
  `Callable[[Any], str]` shape `ReactPlanner` expects (`planner.py:24, 37-40`).
- **`gate.py`** — **unchanged.** The probe is an *observation*, never a success criterion. The agent
  cannot declare victory.
- **`actions.py`** — **unchanged.** No new action kind; `patch` already replaces the one script.
- **`build_script.py`** — PROVISION service section wrapped fail-soft; factor
  `render_service_start_script`'s `[start+probe]` apart from its trailing `exec "$@"`.

Diagnosis routing is *context, not control flow*:

- probe **red** → patch the ACTIVATE section (daemon not up)
- probe **green** + tests still connection-refused → patch the **config** (wrong endpoint)
- normal build failure → patch PROVISION

## 7. Guardrails are phase-dependent

| | Construction (cold start) | Repair (the agent) |
|---|---|---|
| Who writes the commands | graph, via Rung A | the agent |
| What verifies them | **nothing yet** | **the probe** |
| Third-party repos / URL fetches | **forbidden** (deterministic policy gate) | **allowed** |

At construction an unverified recipe is baked in blind — that is exactly how the hallucinated
`packages.valkey.io` line regressed rq. At repair the same attempt is safe, because service steps are
**fail-soft** and the **probe** is ground truth. Strict where nothing checks; permissive where
something does.

## 8. Guarantees

1. **Fail-soft.** Service steps never abort the build. A broken service leaves the repo at baseline.
2. **Monotonicity (Pareto-safe).** Enabling services can only raise or preserve pass-rate. This is
   the property the valkey regression violated.
3. **Gate unchanged.** DONE = build rc 0 **and** tests ≥80%. Probes are observations.
4. **Bounded worst case.** Out of steps → `GIVEUP` → best-effort script. Never below baseline.
5. **Separable overlay.** SERVICE/CONFIG nodes stay excluded from the graph-hash; the byte-identical
   core is unperturbed. Everything remains behind `V3_INCLUDE_SERVICES=1` (default off;
   `multi_docker_eval_adapter.py:164`, `render_build_script(..., include_services=False)`).

## 9. Detection changes (small, empirically justified)

Detection is already ~90% right: `iter_provisioning_specs` parses compose **and** GitHub-Actions
`jobs.<job>.services:`; probe-less setups are already skipped (`classify_services_clean.py:115`);
`env_classifier.py` is deleted, so detection is structured-only. Deltas:

1. **Family aliasing** in `_kind_of` (`service_scan.py:57`, today only `postgis→postgres`):
   `valkey|keydb|dragonfly → redis`, `mariadb|percona → mysql`, `pgvector|timescaledb|bitnami-postgresql → postgres`.
2. **Honor `feasible`** at the admit gate — today `classify_services_clean.py:115` checks only
   `setup is None or not probe`, admitting setups that `verify_plan` marked infeasible.
3. **Deterministic policy gate** rejecting recipes that add apt sources / `curl|gpg` / edit
   `sources.list` — at construction only (see §7).
4. **Demote `service_translate`** from construction to repair (Rung B).

Empirical justification (`.superpowers/sdd/ratbench-service-catalog.md`, 50-repo corpus):

- **22/50** repos declare ≥1 genuine backing service; 30 distinct kinds.
- Histogram: `postgres 16, redis 11, mysql 8, minio 6, elasticsearch 4`, then ~20 singleton exotics.
- **Aliasing is load-bearing:** 7/22 (32%) repos depend on an alias. **rq shows zero services without
  `valkey→redis`.**
- **CI parsing is load-bearing:** rq, pretix, frappe/press have service signal **only** in CI.
- **Skip-and-defer is correct:** 10 exotic kinds ship **no healthcheck anywhere** → uncertifiable.
- **66%** (132/199) of service declarations carry a healthcheck → a free probe.
- Only 4/22 (18%) repos are fully covered by KNOWN+ALIAS — confirming a short head + bespoke tail, and
  that table breadth is the wrong investment.

**Scoped table additions:** `minio` (+ S3-compatible aliases) — 6/22 repos; `elasticsearch` — 4/22.
Nothing else in the tail generalizes.

## 10. Deliberately excluded (YAGNI)

- **No daemons in the build layer.** PROVISION at build, ACTIVATE at runtime. Full stop.
- **No service state in the reproducibility hash.** The overlay stays separable.
- **No exotic-service zoo.** Curate the head; defer the tail to the agent.
- **No sibling-container orchestration.** Running each declared image as a harness-orchestrated
  sibling container on a network is strictly more faithful (real `postgres:16`, no apt translation,
  covers the whole tail) — but it moves network/lifecycle/teardown complexity into the harness and is
  **out of scope**. Recorded as the fidelity play if apt-mismatch (e.g. `pgvector`) or LLM variance
  becomes the measured bottleneck.

## 11. Research framing

The contribution is **analysis**, not provisioning. `graph_context` is **already wired-but-off**
(`entry.py:75`, baseline passes `None`) — which makes it a clean **ablation switch**:

- **obligations ON** → the agent knows what is required before anything fails (graph-guided)
- **obligations OFF** → same agent, same loop, same gate, reacting to crashes (structurally RAT)

One variable: *did static analysis of the repo's files tell it what was needed?* This isolates
comprehension as the causal factor — precisely what the baseline forensics identified (RAT ran the
correct redis commands; it just never knew to).

Two metrics, both about analysis fidelity:

1. **Obligation fidelity** — recall/precision of detected service/config obligations against the
   catalog oracle (same shape as the existing package-closure metrics).
2. **Discharge rate under ablation** — how much a general repair agent's success depends on being
   handed correct obligations.

Service provisioning becomes the *instrument* that measures graph quality, not the contribution.

## 12. Open questions (resolve before planning)

1. **Delimiter + splitting.** Exact marker format, and whether the host splits the script text or the
   renderer emits two strings the agent sees concatenated. (Affects `patch` round-tripping.)
2. **`feasible` plumbing.** Confirm `translate_service`'s return surfaces `feasible` at the
   `classify_services_clean.py:115` admit site; `verify_plan` sets it, but the path is untraced.
3. **Probe-verdict struct.** Fields (`obligation_id`, `ok`, `command`, `output`, `attempts`) and the
   single-shot vs bounded-wait selection rule.
4. **Gating.** Does obligation context ride the existing `graph_context: bool` flag, or its own
   `V3_INCLUDE_SERVICES` gate? (Ablation cleanliness argues for keeping `graph_context` as the single
   switch.)
5. **Graph persistence.** The last full-50 run saved `setup.sh` per repo but **no graph**
   (`construction-python50-20260707-072356`). Post-hoc obligation forensics needs a compact
   `env_graph.json` dump. Small additive change; decide whether it lands here or separately.
