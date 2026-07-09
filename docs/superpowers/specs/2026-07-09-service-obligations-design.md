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

## 3. The service-universal node schema (normative)

One shape for every service — postgres, redis, ClickHouse, Oracle, anything. Every field is extracted
or derived from the repo's own compose / CI declarations. Validated by PoC against all 50 python50
repos (`.superpowers/sdd/service-schema-poc-findings.md`).

```python
@dataclass(frozen=True)
class ServiceNode:
    # ── identity ──────────────────────────────────────────────────────────
    id: str                    # "service:db"
    name: str                  # "db" — the declaration key; ALSO its declared
                               #        hostname (what the app's DSN says)

    # ── what the repo declared: the software's identity ────────────────────
    image: str                 # "postgres:16" — verbatim, may contain templates
    image_repo: str            # "postgres" — lexical parse (registry/org/name)
    image_tag: str | None      # "16"; None if absent or templated

    # ── how to reach it ───────────────────────────────────────────────────
    ports: list[Port]          # all declared, verbatim: [{container, host}]
    port: int | None           # the one we will use
    port_source: PortSource    # ports|expose|env_dsn|sibling_dsn|none
    endpoint: str | None       # "localhost:5432" (single-container world)

    # ── how to configure it ───────────────────────────────────────────────
    env: dict[str, str]        # from `environment:` / `env:`

    # ── declared run hints (NOT recipes) ──────────────────────────────────
    command: str | None        # declared `command:` — start args
    entrypoint: str | None
    volumes: list[Mount]       # verbatim [{host, container}]
    seed: list[Mount]          # derived subset: initdb.d-style mounts

    # ── the certificate (the host's contract) ─────────────────────────────
    check: Check               # {command, source, interval_s, retries, timeout_s}

    # ── ordering ──────────────────────────────────────────────────────────
    depends_on: list[str]      # declared; becomes AFTER edges

    # ── why we believe this, and how sure ─────────────────────────────────
    relevance: Relevance       # ci_service|ci_referenced_compose|root_compose
                               # |unreferenced_compose
    provenance: list[Source]   # ALL contributing decls [{file, locator, kind}]
    raw: dict[str, dict]       # verbatim entry, keyed "<kind>:<file>" — the agent's primary source

    # ── honesty ───────────────────────────────────────────────────────────
    state: State               # certifiable_obligation | declared_unverifiable
    unresolved: list[str]      # fields whose evidence was templated, e.g.
                               # ["image_tag", "env.POSTGRES_PASSWORD"]
```

### 3.0 Enums (fixed)

```python
PortSource = "ports" | "expose" | "env_dsn" | "sibling_dsn" | "none"
CheckSource = "declared_healthcheck" | "tcp_port" | "none"
Relevance  = "ci_service" | "ci_referenced_compose" | "root_compose" | "unreferenced_compose"
State      = "certifiable_obligation" | "declared_unverifiable"
```

### 3.0.1 Who consumes what

| Group | Consumer | Why it exists |
|---|---|---|
| identity, `image*` | **agent** | `postgres:16` is what its world knowledge keys off |
| `ports`/`port`/`endpoint` | **host + agent** | where it must answer; `port_source` says how we knew |
| `env` | **agent + CONFIG node** | credentials to configure it with |
| `command`/`volumes`/`seed` | **agent** | the repo's own start args + schema seeding |
| **`check`** | **host** | the *only* thing the host runs — the certificate |
| `depends_on` | **renderer** | ACTIVATE ordering |
| `relevance`/`provenance`/`raw` | **agent + audit** | why we believe it; primary source to reason from |
| `state`/`unresolved` | **renderer + honesty** | drives cold-start rendering; records what we could not resolve |

### 3.0.2 Invariants

1. **The node contains exactly one executable string: `check.command`.** Everything else is
   declarative evidence. There are no install/start commands at construction — the agent writes those
   into the script.
2. **No `kind`, no `fix`, no `phase`.** `kind` is a table (removed). `fix` is the agent's job. `phase`
   is constant for all services (install→PROVISION; start/seed/check→ACTIVATE), so it belongs to the
   *type*, not the node.
3. **Every derived field records its rung** (`port_source`, `check.source`, `relevance`). This is what
   makes detection measurable and debuggable: we can always answer *"why do we think redis is on
   6379?"*
4. **Degrade the field, never the node.** A templated tag nulls `image_tag` and appends to
   `unresolved`; it never deletes the service. (This bug silently dropped rq — see PoC findings.)
5. **`state` is derived, not declared.** `check.source == "none"` ⇒ `declared_unverifiable` ⇒ surfaced
   to the agent, never enforced by the host.

`name` doubles as the **declared hostname**: the app's DSN says `@db:5432` while we provision to
`localhost`, so keeping the declared host is what lets the CONFIG node rewrite the connection string.

`raw` is not decoration. **Normalized fields serve the host; raw evidence serves the agent.** The exact
image tag, `command:` flags, and env are what let a general agent reconstruct the service without any
built-in knowledge of what it is.

### 3.1 The check ladder (evidence-only, no table)

**Precondition on every rung: the check must pass `patch_gate.is_read_only`.** The check runs inside
certification, so it must never mutate the container. A declared healthcheck that fails this gate does
not disqualify the service — it **falls through to the next rung**.

1. **Declared healthcheck**, if read-only. compose `healthcheck.test` (strip `CMD` / `CMD-SHELL`), or
   CI `options: --health-cmd "..."`. Semantic and strongest. → `source: declared_healthcheck`
2. **TCP liveness on the declared port** — universal, service-agnostic, derived from `ports:`.
   → `source: tcp_port`
3. **Neither** → `source: none` → **declared, unverifiable**: surfaced to the agent, never enforced.

Rung 2 is what replaces the canonical-probe table. A listening port is a weaker certificate than
`pg_isready` (open socket ≠ semantically ready) — hence `check.source`, which records the strength of
the certificate we were given.

**Portability:** use `python3`, not `python`, `bash </dev/tcp/...`, or `nc`. `nc` is absent from slim images; `python`
is absent from any image that ships only a `python3` binary (and from plain Debian/Ubuntu with `python3`
installed). `python3`
is guaranteed present in a Python repo's environment. Verified to pass `is_read_only`:

```bash
python3 -c "import socket; socket.create_connection(('127.0.0.1', 5432), 1).close()"
```

**Measured cost of the read-only precondition** (corpus, 158 nodes): 11 of 54 declared healthchecks
fail `is_read_only` — all of them `curl`/`wget` HTTP probes (PostHog's kafka/elasticsearch/opensearch,
mlflow's storage, gitingest's minio). **9 fall back to `tcp_port`; 2 have no port and become `none`.**
Net certifiable rate 75% → **73%**. The gate costs 2 points and buys a guarantee that certification
cannot mutate the environment.

### 3.2 Port/endpoint derivation (also evidence-only)

A ladder, best-rung-wins, recording which rung fired in `port_source`:

1. **`ports:`** — the declared mapping. (55% of services)
2. **`expose:`** — container-only ports. (3%)
3. **own-env DSN** — `DATABASE_URL=postgres://db:5432/app` in the service's own env. (4%)
4. **sibling-env DSN** — the port often exists *only* in another service's DSN: `db` declares no
   ports, but the app declares `…@db:5432/…`. Search sibling env values for `\b{name}:(\d+)\b`. (6%)
5. else unknown → `port_source: none` → `check.source: none`.

Rung 4 was discovered by the PoC: `Spoolman/db (postgres:11-alpine)` and `OpenCTI/redis:8.4.0` declare
neither ports nor healthcheck, yet their ports are plainly stated in a sibling's connection string. It
rescued 9 services. Still evidence-only — no table.

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

## 10. Node construction: an evidence-fusion pipeline

```
DISCOVER  →  SCOPE  →  FUSE  →  CLASSIFY  →  CERTIFY
(sources)   (relevance) (ladders) (app?)      (check)
```

Each stage generalizes along a different axis, and none of them knows anything about a specific
service.

### 10.1 DISCOVER — sources are adapters (generalizes across *evidence*)

```python
class ServiceEvidenceSource(Protocol):
    def discover(self, repo: str) -> Iterator[RawDeclaration]:
        """RawDeclaration = {name, entry, file, locator, source_kind}"""
```

Today: `ComposeSource`, `GithubActionsSource`. Later: GitLab CI, k8s manifests, `devcontainer.json`,
tox/Makefile targets, testcontainers-in-code. **Adding a source never touches the schema or its
consumers** — the same seam pattern as `EcosystemProvider`.

### 10.2 SCOPE — relevance is *reachability*, not paths (generalizes across *build tools*)

> **A declaration is test-relevant if something that runs the tests references it.**

Path filtering is **unsound in both directions** (PoC): `mlflow/tests/db/compose.yml` and
`ezdata/tests/docker-compose.test.pg.yml` *are* the test environment, while
`testcontainers/tests/core/compose_fixtures/*` is the library's own API fixture data. No path rule
separates them. Instead, follow the edge from the test command to the declaration:

| Evidence | `relevance` |
|---|---|
| CI `services:` on a test job | `ci_service` — intrinsic; the job *is* the test |
| CI runs `docker compose -f X up` before pytest | `ci_referenced_compose` — X is the test env |
| root-level compose, unreferenced | `root_compose` — ambiguous |
| nested compose, never referenced | `unreferenced_compose` — surface, do not enforce |

This mirrors the package layer's import-reachability, applied to service declarations. Measured on the
corpus: 10 repos expose `ci_service`, 11 expose `ci_referenced_compose` (`docker-compose.dev.yml` 31×,
`docker-compose.mysql-pr.yml` 6×, `docker-compose.test.pg.yml` 4×, …) — union ≈ 20 repos of the 23 we
detect, with **no path or kind heuristic**.

### 10.3 FUSE — fields have ladders, not values (generalizes across *incomplete evidence*)

Group declarations **by service name across all sources**, then merge field-by-field, best-rung-wins,
recording which rung fired:

```
port  := ports: → expose: → own-env DSN → sibling-env DSN → unknown
check := healthcheck → CI --health-cmd → derived TCP(port) → none
config:= env → env_file → .env
start := command: → entrypoint: → unknown
```

**Fuse, do not dedup.** Today `iter_provisioning_specs` dedups by name (compose wins) and *discards*
the other source's evidence — so a compose entry with `volumes:` but no healthcheck loses the CI
entry's `--health-cmd`. Fusion recovers it. Invariant: **degrade the field, never the node** (§3.0.2).

### 10.4 CLASSIFY — app vs backing, from evidence only

1. `build:` present → locally built → the app.
2. Image named after the repo (`podman-compose` ⊂ `podman-compose-test`). *Direction matters.*
3. **First-party image** (org matches repo owner) that publishes **no port and declares no
   healthcheck** → the app being deployed. Kills OpenCTI's 271 `opencti/connector-*` composes while
   keeping `supabase/postgres` (first-party, but exposes a port).

Prefer the **structural** signal where available: `depends_on` forms a DAG — anything with in-degree
> 0 is backing; a `build:`-ed node that depends on others is the app. Rules 1–3 are the fallback for
composes with no `depends_on`. Together these took over-detection from **594 → 158**.

### 10.5 CERTIFY — the check ladder (§3.1) sets `state`

`declared_healthcheck | tcp_port` ⇒ `certifiable_obligation`. `none` ⇒ `declared_unverifiable`
(admitted and surfaced; never enforced).

### 10.6 What changes in the current code

**Add:** the source-adapter seam; reference-derived `relevance`; the sibling-DSN port rung; lossless
capture (`provenance{file,locator}`, verbatim `raw`, `command:`, `entrypoint:`, `depends_on:`,
`expose:`, healthcheck timing); fuse-not-dedup.

**Change:** admit gate — stop dropping probe-less services (`classify_services_clean.py:115`); admit
with `check.source: "none"`.

**Remove:** `kind` normalization and `_kind_of` aliasing (`service_scan.py:57`);
`service_recipes._KIND_BASE` as a construction-time recipe source; `translate_service` at construction
— **this deletes the `feasible` admit-gate bug entirely**, since the path that could emit an infeasible
plan no longer runs.

### 10.7 Free consequence: the service tier is language-agnostic

Compose and CI are language-neutral — a Go or Node repo declares its postgres identically. So the
service tier needs **no `EcosystemProvider` specialization at all**. Dropping the kind-table did not
just remove hardcoding; it made the whole tier cross-ecosystem.

### 10.8 Measured on the corpus (PoC, all 50 python50 repos)

Implemented in `.superpowers/sdd/service_schema_poc.py`; findings in
`service-schema-poc-findings.md`; 158 extracted nodes in `service_nodes_poc.jsonl`.

| | |
|---|---|
| repos with ≥1 backing service | **23** |
| backing services extracted | **158** (147 compose, 11 CI) |
| **certifiable** (`state = certifiable_obligation`) | **75%** |
| ├ `declared_healthcheck` | 34% |
| └ `tcp_port` (derived) | 41% |
| `declared_unverifiable` | 25% |

Port rungs: `ports:` 55% · `sibling_dsn` 6% · `env_dsn` 4% · `expose:` 3% · none 33%.
Fields: endpoint 67% · env 56% · `command:` 34% · volumes 46% · `depends_on` 30% · templated `env` 19%.

**The `tcp_port` rung doubles the certifiable set (34% → 75%).** Refusing a canonical-probe table costs
far less than feared.

**Acid test — rq/rq passes with zero service knowledge.** rq has *no compose file*; its only signal is
a CI workflow declaring `image: valkey/valkey:${{ matrix.valkey-version }}` with
`--health-cmd "valkey-cli ping"`. Extracted: `image_repo=valkey/valkey`, `image_tag=null`
(templated → `unresolved`), `endpoint=localhost:6379` (`port_source: ports`),
`check={valkey-cli ping, declared_healthcheck}`, `relevance=ci_service`. **No `kind`, no
`valkey→redis` alias.**

Two catalog findings **dissolve** under evidence-only detection:

- *"Aliasing is load-bearing — 7/22 repos depend on it."* → With no `kind` field there is nothing to
  alias. **The aliasing problem does not exist.**
- *"Only 4/22 (18%) repos are fully covered by KNOWN+ALIAS kinds."* → There is no table to cover with;
  coverage is uniform across all 30 kinds.

The honest residue (25% `declared_unverifiable`) splits into (a) genuine backing services whose port is
only *implied by the image* (`postgres:11-alpine` with no ports, no healthcheck, no sibling DSN) — the
real cost of refusing a table; and (b) init/sidecar/observability containers (`kafka-init`,
`otel-collector`, `jaeger`) that are not test dependencies and which `relevance` precedence drops.

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
3. ~~**`tcp_port` rescue rate.**~~ **ANSWERED by the PoC (§10.8): 34% → 75% certifiable.** The TCP
   rung doubles the certifiable set; the table costs little to remove.
4. **`relevance` precedence, implemented.** The PoC verified `ci_referenced_compose` by grep
   (workflows naming `docker compose -f X`); it is not yet wired into the extractor. Decide whether
   low-relevance nodes (`unreferenced_compose`) are emitted-but-unenforced or dropped entirely.
5. **Env resolution.** 19% of services carry unresolved `${...}` in `env`. Implement compose
   `env_file:`/`.env` resolution? Resolve GH-Actions `matrix` to its first `include` entry (would give
   rq a concrete `image_tag`)? Or leave both in `unresolved` and let the agent read `raw`?
6. **Multi-file compose merge.** `-f a.yml -f b.yml` override semantics are unimplemented; the PoC
   treats each file independently.
7. **Gating.** Does obligation context ride the existing `graph_context: bool`, or its own
   `V3_INCLUDE_SERVICES` gate? Ablation cleanliness argues for one switch.
8. **Cost of no table.** Every service — even redis — now costs agent turns. Measure turns-to-green
   and run-to-run variance on the head kinds; if the cost is material, a memoized fast path can be
   reintroduced *as an optimization behind the same schema* (never as the source of truth).
9. **Graph persistence.** The last full-50 run saved `setup.sh` per repo but **no graph**
   (`construction-python50-20260707-072356`). Post-hoc obligation forensics needs a compact
   `env_graph.json` dump. Decide whether it lands here or separately.
