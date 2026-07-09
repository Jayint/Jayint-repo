# Service Obligations: evidence-only detection, agent-discharged setup

**Date:** 2026-07-09
**Status:** Design — approved in discussion, not yet planned/implemented
**Branch:** john-v3-multi-lang (shared; local commits only)
**Supersedes:** the eager exotic-LLM service-recipe path (`service_translate` at construction), the
per-kind recipe table (`service_recipes._KIND_BASE`), and `kind` normalization/aliasing.

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

Our own weakness was the mirror image: service recipes were hardcoded per kind, and unknown kinds fell
to an eager LLM translation at construction — which hallucinated `packages.valkey.io`, aborted
`setup.sh` under `set -e`, and **regressed rq from 1/470 to `build_failed`**.

## 2. Governing principle: repo knowledge vs world knowledge

> **"This repo needs a postgres 16 on :5432, ready when `pg_isready` returns 0."**
> → *repo knowledge*. Extractable from the repo's files, verifiable, measurable. **The graph's job.**
>
> **"To get postgres on Debian: `apt install postgresql`, start with `service postgresql start`."**
> → *world knowledge*. Not in the repo. Not our contribution. **The agent's job.**

A hardcoded `kind → commands` table is a bad home for world knowledge: a stale, incomplete snapshot
that does not generalize past the handful of kinds someone wrote down. So:

**The graph carries only what the repo declared. The agent supplies how to install it. The host runs
the check.** There is **no service-specific table anywhere in the system** — not for install, not for
start, not for the readiness probe.

### 2.1 Parsing ≠ mapping

- `postgres:16` → `{image_repo: "postgres", image_tag: "16"}` — a **lexical parse**. Allowed.
- `valkey → redis` — a **semantic lookup table**. Removed.

**There is no `kind` field.** The image string is the service's identity.

### 2.2 Three roles

> **The graph asserts obligations. The host certifies them. The agent discharges them.**

Extends the existing project invariants (graph certifies *necessary*, tests certify *sufficient*, host
owns *done*).

## 3. The service-universal node schema

One shape for every service — postgres, redis, ClickHouse, Oracle, anything. All fields are extracted
or derived from the repo's own compose / CI declarations.

```python
ServiceNode {
  id:         "service:db"
  name:       "db"                       # the declaration key (= its hostname in compose/CI)

  image:      "postgres:16"              # verbatim, the identity
  image_repo: "postgres"                 # lexical parse only
  image_tag:  "16"

  ports:      [{container: 5432, host: 5432}]
  env:        {POSTGRES_DB: "app", POSTGRES_USER: "u", POSTGRES_PASSWORD: "p"}
  command:    "postgres -c max_connections=200" | null   # declared start args
  entrypoint: null
  volumes:    [{host: "./init.sql", container: "/docker-entrypoint-initdb.d/init.sql"}]
  depends_on: ["cache"]

  endpoint:   "localhost:5432"           # derived (see §3.2)

  check: {                               # derived, never mapped (see §3.1)
    command:   "pg_isready -U u"
    source:    "declared_healthcheck" | "tcp_port" | "none"
    interval_s: 10, retries: 5, timeout_s: 30
  }

  provenance: [{file: ".github/workflows/ci.yml",
                locator: "jobs.test.services.postgres"}]
  raw:        {...}                      # verbatim YAML entry — the agent's primary source
}
```

`raw` is not decoration. **Normalized fields serve the host; raw evidence serves the agent.** The exact
image tag, `command:` flags, and env are what let a general agent reconstruct the service without any
built-in knowledge of what it is.

### 3.1 The check ladder (evidence-only, no table)

1. **Declared healthcheck.** compose `healthcheck.test` (strip `CMD` / `CMD-SHELL`), or CI
   `options: --health-cmd "..."`. Semantic and strongest. → `source: declared_healthcheck`
   (**66%** of declarations, per the catalog).
2. **TCP liveness on the declared port** — universal, service-agnostic, derived from `ports:`.
   → `source: tcp_port`
3. **Neither** → `source: none` → **declared, unverifiable**: surfaced to the agent, never enforced.

Rung 2 is what replaces the canonical-probe table. A listening port is a weaker certificate than
`pg_isready` (open socket ≠ semantically ready) — hence `check.source`, which records the strength of
the certificate we were given.

**Portability:** use Python, not `bash </dev/tcp/...` or `nc`. `nc` is absent from slim images; Python
is guaranteed present in a Python repo's environment.

```bash
python -c "import socket; socket.create_connection(('127.0.0.1', 5432), 1).close()"
```

### 3.2 Port/endpoint derivation (also evidence-only)

`ports:` → else `expose:` → else parse the port out of a DSN in `env`
(`DATABASE_URL=postgres://db:5432/app`) → else unknown (`check.source: none`).

Since we provision into the same container (§5), the endpoint is `localhost:<container_port>`.

## 4. Node states

There are only two, because there is no recipe table and therefore no "pre-resolved" state.

| `check.source` | State | Cold-start rendering |
|---|---|---|
| `declared_healthcheck` \| `tcp_port` | **certifiable obligation** | comment block + single-shot check |
| `none` | **declared, unverifiable** | comment block only; never enforced |

Note the change from today: a probe-less service is **no longer dropped**
(`classify_services_clean.py:115` currently skips it). It is admitted, surfaced to the agent, and left
uncertified. More information reaches the agent; the host still refuses to enforce what it cannot
measure.

### 4.1 Emitting a check without a start command

We do not need to know how to *start* a service to know how to *check* it. For every certifiable
obligation we emit the check with **no start command**; it fails, and that red verdict *is* the host
certifying "obligation unsatisfied." This is what lets *graph asserts / host certifies* hold for
services we have no recipe for — which, under this design, is **all of them**.

Practical rule: **single-shot when nothing was started** (we are measuring, not waiting). The bounded,
non-exiting retry loop (`for i in $(seq 1 N); do <check> && break; sleep 1; done` — see the
`render_probe_poll` exit-0 bug fixed in cc873a3) applies only once a daemon has actually been launched
by the agent, using the declared `interval_s`/`retries` as its budget.

### 4.2 Ordering and seeding

ACTIVATE renders obligations in topological order of `depends_on:`, so a service starts only after its
dependencies are check-green. Ordering is derived from the declaration, never hardcoded.

`volumes:` seed steps (e.g. `docker-entrypoint-initdb.d`) run **inside ACTIVATE, after that service's
check goes green** — never in PROVISION. Schema seeding needs a *running* daemon; it is categorically
runtime work. A repo whose `init.sql` ran at build time against a dead postgres has an empty database
at test time.

### 4.3 The cold-start block (normative format)

Identical shape for every service:

```bash
# ── [service:db] OBLIGATION ──────────────────────────────────────
# Declared: image postgres:16
#   (.github/workflows/ci.yml → jobs.test.services.postgres)
# Env:        POSTGRES_DB=app POSTGRES_USER=u POSTGRES_PASSWORD=p
# Start args: postgres -c max_connections=200      (declared `command:`)
# Seed:       ./init.sql → /docker-entrypoint-initdb.d/init.sql
# Must answer at: localhost:5432
# Ready when:     pg_isready -U u                  (declared healthcheck)
# TODO: install (PROVISION) + start here, then this check must pass.
# ─────────────────────────────────────────────────────────────────
pg_isready -U u    # single-shot: reports the obligation
```

The agent reads `postgres:16` and its own world knowledge does the rest.

## 5. The script contract: one source, two phases, two artifacts

| Layer | What exists | Why |
|---|---|---|
| **Source** (agent-editable) | **ONE** `setup.sh` with `# --- PROVISION ---` and `# --- ACTIVATE ---` sections | Preserves the arm's single-artifact `patch` contract; install and start are coupled reasoning |
| **Repair-time execution** | Host splits on the delimiter, runs the halves as **two steps** → two verdicts | A dead service becomes a *red check*, not an aborted build (localization + fail-soft) |
| **Grade-time materialization** | **TWO** artifacts: PROVISION → build `RUN`; ACTIVATE → `services_start.sh` as `ENTRYPOINT` | Docker build layers do not preserve running processes |

**Invariant:** the *same* ACTIVATE text is exercised at repair time and baked as the graded
`ENTRYPOINT`. Repair-time green therefore implies grade-time green.

Repair-time works in one live container because the arm's `Sandbox` is persistent — verified:
`entry.py:33-51` closes `reset`/`run_script`/`certify`/`exec_readonly`/`run_tests` over **one**
`sandbox`, and `run_tests()` is `sandbox.exec_readonly(VERIFY_TEST_CMD)`. A daemon started by ACTIVATE
is alive when `run_tests` fires.

**Fail-soft by construction:** the renderer wraps the service sub-region of PROVISION in
`set +e` / `set -e`. Any install command *the agent writes there* therefore cannot abort the build —
monotonicity does not depend on the agent remembering `|| true`.

## 6. Three channels carry an obligation to the agent

| Channel | Job | Agent can lose it? |
|---|---|---|
| **`graph_context`** (planner prompt) | **Authoritative** — regenerated from the graph every turn | No — host-owned |
| **The check** (in ACTIVATE) | **Measurement** — reports the obligation unsatisfied | No — host runs it |
| **Comment block** (in the script) | **Anchor** — where the fix goes, evidence inline | Yes — it is only text |

The comment alone is insufficient: `patch` rewrites the whole script, so the agent could drop it.
`graph_context` is regenerated from the graph each turn and cannot be forgotten.

**`graph_context` is a projection of the node**, not a hand-authored prompt:
`{image, env, command, endpoint, check, provenance, raw}` for every unsatisfied service. Schema
fidelity therefore directly determines agent capability.

## 7. Loop integration

Current (verified `loop.py:32-47`): `reset → run_script → certify → run_tests → gate`.

New — **one injected step**:

```
reset
→ run_script(PROVISION)     # service sub-region is fail-soft
→ activate(ACTIVATE)        # NEW: start daemons (agent-written) + check → verdict per obligation
→ certify(install layers)   # unchanged: EXECUTION_LAYER_ORDER minus Layer.TESTS
→ run_tests                 # unchanged: single authoritative run
→ gate                      # UNCHANGED: rc==0 AND tests ≥80%
```

By file:

- **`loop.py`** — add `activate` to the injected callables (line 28). In `rerun(s)`:
  `verdicts = activate() if r.ok else None`; return `(r, verdicts, g)`. Extend
  `_observation(result, test, verdicts)` (line 22) to render per-service check verdicts.
- **`entry.py`** — `docker_adapters(sandbox)` (line 33) gains `activate()` over the *same* sandbox.
  Replace `ctx = None` (line 75) with `service_graph_context(graph) -> str` — exactly the
  `Callable[[Any], str]` shape `ReactPlanner` expects (`planner.py:24, 37-40`).
- **`gate.py`** — **unchanged.** The check is an *observation*, never a success criterion.
- **`actions.py`** — **unchanged.** No new action kind; `patch` already replaces the one script.
- **`build_script.py`** — fail-soft wrap on the PROVISION service sub-region; factor
  `render_service_start_script`'s `[start+check]` apart from its trailing `exec "$@"`.

Diagnosis routing is *context, not control flow*:

- check **red** → patch ACTIVATE (daemon not up)
- check **green** + tests still connection-refused → patch **config** (wrong endpoint)
- ordinary build failure → patch PROVISION

## 8. Guardrails

Construction emits **zero service commands** — only comments and checks. There is nothing to
policy-gate, because nothing is generated. All commands originate at repair, where two things contain
a wrong guess:

1. **Fail-soft** — service steps cannot abort the build (§5).
2. **The check** — ground truth; a bad guess simply stays red.

This is the principled form of "the LLM never writes an unverified command." At construction nothing
verifies, so we emit nothing. At repair the check verifies, so the agent is free to experiment —
including third-party installs, which are safe precisely because they are measured.

## 9. Guarantees

1. **Fail-soft.** Service steps never abort the build; a broken service leaves the repo at baseline.
2. **Monotonicity (Pareto-safe).** Enabling services can only raise or preserve pass-rate — the
   property the valkey regression violated.
3. **Gate unchanged.** DONE = build rc 0 **and** tests ≥80%. Checks are observations.
4. **Bounded worst case.** Out of steps → `GIVEUP` → best-effort script. Never below baseline.
5. **Separable overlay.** SERVICE/CONFIG nodes stay excluded from the graph-hash; the byte-identical
   core is unperturbed. All behind `V3_INCLUDE_SERVICES=1` (default off;
   `multi_docker_eval_adapter.py:164`, `render_build_script(..., include_services=False)`).

## 10. Detection changes

Detection is already strong: `iter_provisioning_specs` parses compose **and** GitHub-Actions
`jobs.<job>.services:`, and `env_classifier.py` is deleted (structured-only). Changes:

**Add — lossless evidence capture.** `ProvisioningSpec` normalizes to
`(service_name, kind, image, params, init_files, probe, port, build)` and **discards** everything else.
Capture instead: `provenance{file, locator}`, the verbatim `raw` entry, `command:`, `entrypoint:`,
`depends_on:`, `expose:`, and healthcheck timing (`interval`/`retries`/`timeout`).

**Add — the check ladder + `check.source`** (§3.1), and the port/endpoint derivation ladder (§3.2).

**Change — admit gate.** Stop dropping probe-less services (`classify_services_clean.py:115`). Admit
with `check.source: "none"`; surface, do not enforce.

**Remove:**
- `kind` normalization and `_kind_of` aliasing (`service_scan.py:57`).
- `service_recipes._KIND_BASE` as a construction-time recipe source.
- `translate_service` at construction. **This deletes the `feasible` admit-gate bug entirely** — the
  code path that could emit an infeasible plan no longer runs.

### 10.1 What the corpus says (`.superpowers/sdd/ratbench-service-catalog.md`, 50 repos)

- **22/50** repos declare ≥1 genuine backing service; **30 distinct kinds**; a short head
  (`postgres 16, redis 11, mysql 8, minio 6, elasticsearch 4`) and ~20 singleton exotics.
- **CI parsing is load-bearing:** rq, pretix, frappe/press declare their service **only** in CI.
- **66%** (132/199) of declarations carry a healthcheck → a free semantic check.

Two of the catalog's most alarming numbers **dissolve** under evidence-only detection:

- *"Aliasing is load-bearing — 7/22 repos depend on it; rq shows zero services without valkey→redis."*
  → With no `kind` field, there is nothing to alias. rq's service is simply `image: valkey/valkey:8`,
  and the agent reads that. **The aliasing problem does not exist.**
- *"Only 4/22 (18%) repos are fully covered by KNOWN+ALIAS kinds."* → There is no table to cover with;
  coverage is uniform across all 30 kinds.

The remaining honest gap: **10 exotic kinds ship no healthcheck anywhere.** Under the check ladder
these are rescued by `tcp_port` *if they declare a port* — an unmeasured fraction (see §12).

## 11. Deliberately excluded (YAGNI)

- **No service-specific tables of any kind** — not install, not start, not probe. World knowledge
  belongs to the agent.
- **No daemons in the build layer.** PROVISION at build, ACTIVATE at runtime. Full stop.
- **No service state in the reproducibility hash.** The overlay stays separable.
- **No sibling-container orchestration.** Running each declared image as a harness-orchestrated sibling
  on a network is strictly more faithful (real `postgres:16`, no translation at all) but moves
  network/lifecycle/teardown into the harness. **Out of scope**; recorded as the fidelity play if
  apt-mismatch (e.g. `pgvector`) or agent variance becomes the measured bottleneck.

## 12. Research framing

The contribution is **analysis**, not provisioning. `graph_context` is already wired-but-off
(`entry.py:75`, baseline passes `None`), which makes it a clean **ablation switch**:

- **obligations ON** → the agent knows what is required before anything fails (graph-guided)
- **obligations OFF** → same agent, same loop, same gate, reacting to crashes (structurally RAT)

One variable: *did static analysis of the repo's files tell the agent what was needed?* This isolates
comprehension as the causal factor — precisely what the baseline forensics identified (RAT ran the
correct redis commands; it just never knew to).

**Removing the recipe table strengthens the claim.** With no hand-curated service knowledge anywhere,
the ablation has no confound: the difference cannot be attributed to "they hardcoded redis." The result
generalizes to any service in any ecosystem.

Three metrics, all about analysis fidelity:

1. **Obligation fidelity** — recall/precision of detected service/config obligations vs the catalog
   oracle (same shape as the existing package-closure metrics).
2. **Certificate strength** — the distribution of `check.source`
   (`declared_healthcheck` / `tcp_port` / `none`): how strong a certificate the repo handed us.
3. **Discharge rate under ablation** — how much a general repair agent's success depends on being
   handed correct obligations.

Service provisioning is the *instrument* that measures graph quality, not the contribution.

## 13. Open questions (resolve before planning)

1. **Delimiter + splitting.** Exact marker format, and whether the host splits the script text or the
   renderer emits two strings the agent sees concatenated. Affects `patch` round-tripping.
2. **Check-verdict struct.** Fields (`service_id`, `ok`, `command`, `source`, `output`, `attempts`) and
   the single-shot vs bounded-wait selection rule.
3. **`tcp_port` rescue rate.** Of the 10 exotic kinds with no healthcheck, how many declare a port
   (`ports:`/`expose:`/DSN-in-env)? Cheap read-only measurement over the catalog; determines how much
   of the tail becomes certifiable.
4. **Gating.** Does obligation context ride the existing `graph_context: bool`, or its own
   `V3_INCLUDE_SERVICES` gate? Ablation cleanliness argues for one switch.
5. **Cost of no table.** Every service — even redis — now costs agent turns. Measure turns-to-green
   and run-to-run variance on the head kinds; if the cost is material, a memoized fast path can be
   reintroduced *as an optimization behind the same schema* (never as the source of truth).
6. **Graph persistence.** The last full-50 run saved `setup.sh` per repo but **no graph**
   (`construction-python50-20260707-072356`). Post-hoc obligation forensics needs a compact
   `env_graph.json` dump. Decide whether it lands here or separately.
