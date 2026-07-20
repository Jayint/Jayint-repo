# Config-Binding (Service URL Binding) — Design Spec

> Status: approved design (2026-06-28). Layers on the Services Tier in-image action
> layer (`docs/superpowers/specs/2026-06-27-services-tier-in-image-action-layer-design.md`).
> Extends arm **`v1gsps`** (default-off). Win the right way: host owns truth, no hollow success.

## 1. Motivation & evidence

The Services in-image provisioning slice (landed `d130772..86f056e`) stands a confirmed
Postgres up in-image and host-certifies it live (`pg_isready`). The `v1gsps` e2e
(`/opt/runs/john-planner-v3/svc-e2e-v1gsps-20260627-151745/`) proved that path works
end-to-end — but `fastapi-template` still scored honest **0.0**, because **provisioning a
service is necessary but not sufficient**. The app reads its connection target from an
env var:

```
DB_STRING=postgresql://postgres:test@db:5432/postgres   # repo docker-compose.yml
```

Three binding gaps remained, in order of bite:
1. **`DB_STRING` is never set** → pydantic-settings validation fails at *import* → `pytest`
   exits 2 at **collection**, before any DB connection is attempted. (Dominant blocker.)
2. **Host `db` doesn't resolve** — provisioning aliases `postgres:127.0.0.1`, the wrong name.
3. **Credentials `postgres:test`** don't match the in-image cluster's default auth.

The anti-hollow gates held (agent `planner_giveup` → finalize rejected → honest 0, no false
pass) and memU (no confirmed service) was unaffected. So the foundation is correct; this spec
adds the missing layer: **bind the app's DB env var to a URL that actually connects to our
provisioned instance.**

## 2. Approach — Option B (rewrite the app's URL to match what we provision)

Two mirror-image strategies exist. **Option A** bends Postgres to satisfy each repo's declared
URL (per-repo user/password/host/pg_hba juggling). **Option B** stands up one *uniform*
Postgres and rewrites the app's env var to point at it. **We choose B** (user-approved):
uniform, far fewer per-repo failure modes, we control both ends. Option A's targeted matching
is a documented fallback for any repo where B proves insufficient — not built now (YAGNI).

### Recipe validation basis (real-container smoke, python:3.10 / PG17, 2026-06-28)
Every uncertain mechanic was verified before this spec was written:
- `runuser -u postgres -- psql -c "ALTER USER postgres PASSWORD 'postgres'"` → `ALTER ROLE`.
- TCP connect **with password** over the package-default `pg_hba.conf` (scram/md5 on
  `127.0.0.1/32`): `psql "postgresql://postgres:postgres@127.0.0.1:5432/postgres" -c 'select 1'`
  → succeeds; same for a `createdb`'d `appdb`.
- **Negative control:** wrong password is **rejected** → the psql certify genuinely
  distinguishes a working binding from a broken one (anti-hollow at the mechanism level).
- `export DB_STRING=...` written to `/etc/profile.d/zz_service_bind.sh` is visible to a fresh
  `sh -lc` (the sandbox's exec form) and to a Python process launched from it — durable across
  the env-var-thrash boundary that defeats plain `export`.

## 3. The five components

### 3.1 Discover the binding (new)
The var name + db name live in the compose **app service's `environment:` block**, which the
current scan ignores (`_services_from_yaml_doc` reads only `services:` images/ports). Add a
compose-environment pass: for each `KEY=<value>` in any service's `environment:` (list or map
form) where `service_from_url(value)` matches a confirmed service kind, record a binding
`(var_name=KEY, kind, dbname=<URL path>)`. CI compose (`.ci/`, `.github/workflows`) is included;
CI wins on conflict (consistent with `scan_services`). For fastapi-template →
`(DB_STRING, postgres, dbname=postgres)`.

This also closes the `db: null` createdb gap observed in the e2e: the dbname now flows into the
start recipe's `createdb`.

### 3.2 Uniform instance + URL rewrite
Provisioning brings up one standard Postgres and makes it deterministically connectable. The
**start recipe gains a password step** (after start, before certify):
`runuser -u postgres -- psql -c "ALTER USER postgres PASSWORD 'postgres'"`.
The **rewritten URL** is constructed host-side:
`<app-scheme>://postgres:postgres@127.0.0.1:<port>/<dbname>` — our credentials + loopback host,
the app's db name. The rewrite **preserves the app's original scheme verbatim** (incl. dialect
suffixes like `postgresql+psycopg2`, which SQLAlchemy needs); only host/credentials/dbname
change. The §3.4 psql certify uses the base `postgresql://` scheme (psql doesn't understand
dialect suffixes). Non-default `<dbname>` is `createdb`'d by the existing recipe step.

### 3.3 Dual injection (live container + eval rebuild)
- **Live container** (so the agent's in-build pytest passes): the binding is a **scheduled
  obligation handed to the LLM** — exactly the mechanism the service `start` uses today
  (`start_recipe['start']` → `facts` in `packet_to_task` → LLM runs it → host checks). Its facts
  hand the LLM two exact, host-derived commands: `ALTER USER postgres PASSWORD 'postgres'` and
  `echo 'export <VAR>="<url>"' > /etc/profile.d/zz_service_bind.sh`. The profile.d write is durable
  because the sandbox runs every command via `exec_run(["/bin/sh","-lc", ...])` (login shell
  re-sources profile.d). The **host certifies** the result (§3.4) — the LLM cannot self-finalize.
  We do **not** build a new host-emit path: none exists for the service start (only the SystemLib
  `apt install` is host-emit), and adding a deterministic binding-emitter would be a new auto-fix
  tier the guardrails forbid ("no deterministic tier beyond the emit-drain"). The e2e showed the
  LLM faithfully runs exact recipe commands; if it doesn't, the psql cert stays MISSING and the
  done-gate blocks (honest fail, never hollow).
- **Eval rebuild** (so the fresh scored image reproduces it): reuse the existing
  `Synthesizer.add_env_instruction` → `ENV <VAR>="<url>"`, baked in a binding pass that runs
  **after** the ledger/config bake passes so the service-bound value takes **precedence** over any
  original-URL value `bakeable_config_env`/the ledger would otherwise bake (the binding is the
  corrected value; a stale `.env.example`-derived URL must not win). The eval test wrapper also
  re-`export`s the var (`compose_in_image_service_commands`) so it's present at test time.

### 3.4 Host certifies the binding (anti-hollow)
A new CONFIG obligation `env:<VAR>` **requires** the SERVICE node. Its `check_command` is a
real connection probe with the bound URL:
`psql "postgresql://postgres:postgres@127.0.0.1:<port>/<dbname>" -c 'select 1'`.
It flips `SATISFIED` **only** when the app's configured URL genuinely connects + authenticates.
This is a *distinct* certification from the service's `pg_isready` (service up) — *binding works*
is strictly stronger and is what the suite actually needs. The negative-control smoke proves a
wrong/absent binding fails this probe, so it cannot certify hollowly.

### 3.5 Graph fit & separation of powers
- The binding is a **CONFIG node** `env:<VAR>` with a `requires` edge **to the SERVICE node**, so
  the scheduler certifies the service first; it becomes **actionable** only once the service is
  `SATISFIED`. `ALTER USER` is part of the *binding* obligation (it's what makes the bound
  credentials valid); `createdb` stays with the *service* (db existence) — the binding's psql cert
  is the real verifier of both.
- **Relax the CONFIG scheduler-exclusion** (`schedule.py:39`, `node.type is not NodeType.CONFIG`)
  for binding nodes only: a binding node carries `data["binding"]=True` and a real psql
  `check_command`, unlike the unsatisfiable `printenv X` that exclusion was written for. Gate the
  relaxation on `allow_services` (same arm), so off-arm the frontier is unchanged.
- The binding's **`facts` hand the LLM the exact host-derived commands** (the HOW — `ALTER USER`
  + profile.d write, with the URL the host computed); the LLM runs them; the **host certifies**
  via the psql probe (the WHETHER). `certify` flips state only by running the `check_command` —
  never inferred from a command outcome or an LLM claim. This is the *same* separation-of-powers
  as the service `start`, not a new tier.
- **Certify reuses the existing CONFIG-layer walk** (`certify.py` — CONFIG is already in
  `_LAYER_ORDER`): the psql `check_command` flips the node with no new certify hook. CONFIG is
  ordered before SERVICES in `_SERVICE_LAYER_ORDER`, so the binding certifies on the *next* cycle
  after the service is up — fine, state is revocable and the orchestrator runs multiple cycles.
- The **done-gate already refuses `done`** until promoted obligations are `SATISFIED`; a binding
  that cannot connect blocks finalization → honest 0, never hollow.
- Maintainer remains the sole graph writer; `DepGraph`/`Node` stay frozen.

## 4. Arm & gating
Extend **`v1gsps`** (= v1gsp + service-provision + **service-binding**). Binding is useless
without provisioning and provisioning is incomplete without it — one conceptual capability,
one arm. Default-off; **off-state byte-identical** (every new code path guarded by the same arm
predicate the provisioning slice already reads:
`os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"`). No new env flag.

## 5. File structure (anticipated; the plan finalizes exact lines)
- `src/python_deps/depgraph/service_scan.py` — compose/CI-`environment:` binding discovery
  (`KEY=<db-url>` per app service); `service_bind_url(scheme, port, dbname)` rewrite helper;
  in `attach_in_image_provisioning`, attach a `Config(env:<VAR>)` node (`data["binding"]=True`,
  psql `check_command`, `chosen_fix=env:<VAR>=<url>`, facts carrying `ALTER USER` + profile.d
  write) with an `Edge(src=config_id, dst=service_id, REQUIRES)`.
- `src/python_deps/depgraph/schedule.py` — relax the `node.type is not NodeType.CONFIG`
  frontier exclusion for binding nodes (`data["binding"]`), gated on `allow_services`;
  surface the binding recipe in the obligation packet.
- `src/envstate/graph_scheduler.py` — render the binding obligation's `ALTER USER` + profile.d
  facts in `packet_to_task` (same shape as the service `start` facts).
- (Certify needs **no** change — the existing CONFIG-layer walk in `certify.py` runs the binding's
  psql `check_command`; CONFIG is already in `_LAYER_ORDER`. Confirm the binding node isn't
  short-circuited.)
- `src/envstate/synthesis.py` / `agent.py` — a binding bake pass in `_bake_test_env_vars` after
  the ledger/config passes (`add_env_instruction(var, url)`) for `ENV` precedence; extend
  `_collect_confirmed_in_image_services` with the bound `var`/`url` keys.
- `run_repo2run_benchmark.py` — emit `export <VAR>=<url>` in `compose_in_image_service_commands`
  from the handoff field (lands in both collect + verification wrappers via the shared call).
- Tests: extend `tests/test_service_provision_off_state.py` (off-state byte-identity covers the
  new paths) + new unit tests per task (discovery, rewrite, frontier relaxation, bake precedence,
  binding certify, eval export, handoff keys).

## 6. Anti-hollow guarantees (the bar every task must hold)
1. Binding `SATISFIED` only via the psql `check_command` run by the host — never emit-implied,
   never LLM-claimed.
2. The psql probe authenticates (negative control proves wrong creds fail) — "postgres up" is
   not accepted as "binding works".
3. Done-gate unchanged in spirit: a non-connecting binding blocks `done`.
4. Off-arm: zero behavior change (byte-identical), enforced by a test.
5. No gate/certify is weakened to gain score; if a metric rises via a relaxed check, back it out.

## 7. Open items resolved / non-goals
- **Dialect suffix** (`postgresql+psycopg2://`): preserve the original scheme verbatim in the
  rewrite (only host/credentials/dbname change); psql itself probes with the base scheme. The
  plan adds a test for a `+psycopg2` URL.
- **Multiple bound vars / multiple services**: discovery yields a list; one binding node per
  `(VAR, service)`. First pass targets Postgres (the e2e target); the structure generalizes but
  only Postgres rewrite is implemented now (other kinds: discovery records them, rewrite is a
  documented follow-up — no silent half-binding).
- **Schema/migrations** (alembic): out of scope — the suite owns schema creation against the
  now-reachable DB; we only guarantee connect+auth (the §3.4 probe), matching the services
  spec's §7/§12 boundary.
- **Secrets**: a DB URL value is config, not a secret; but the existing `_RE_SECRET_NAME`
  denylist still applies to *other* vars — we only force-bind the discovered DB-URL var.
- **Option A fallback**: documented, not built.

## 8. Validation target
Re-run `v1gsps` vs `v1gsp` on `services_e2e.json` (fastapi-template + memU), honest scorer:
- fastapi-template: honest 0 → **genuine pass** (Postgres up + password set, `DB_STRING` bound,
  `psql select 1` certified, integration tests actually run — pass-rate from real tests, not a
  collect-only 0.2).
- memU: **unchanged** (no confirmed service; identical both arms).
- No new Bucket-C / `test_success` via a weakened gate; philosophy confirmed at every step.
