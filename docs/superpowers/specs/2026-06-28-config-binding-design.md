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
- **Live container** (so the agent's in-build pytest passes): the binding writes
  `export <VAR>="<url>"` into `/etc/profile.d/zz_service_bind.sh`. Durable because the sandbox
  runs every command via `exec_run(["/bin/sh","-lc", ...])` (login shell re-sources profile.d).
  This is a deterministic emit (within the allowed emit-drain tier), executed as part of the
  service provisioning that the recipe already drives in the live container.
- **Eval rebuild** (so the fresh scored image reproduces it): reuse the existing
  `Synthesizer.add_env_instruction` → `ENV <VAR>="<url>"`. The service-bound value takes
  **precedence** over any original-URL value `bakeable_config_env` would otherwise bake (the
  binding is the corrected value; a stale `.env.example`-derived URL must not win).

### 3.4 Host certifies the binding (anti-hollow)
A new CONFIG obligation `env:<VAR>` **requires** the SERVICE node. Its `check_command` is a
real connection probe with the bound URL:
`psql "postgresql://postgres:postgres@127.0.0.1:<port>/<dbname>" -c 'select 1'`.
It flips `SATISFIED` **only** when the app's configured URL genuinely connects + authenticates.
This is a *distinct* certification from the service's `pg_isready` (service up) — *binding works*
is strictly stronger and is what the suite actually needs. The negative-control smoke proves a
wrong/absent binding fails this probe, so it cannot certify hollowly.

### 3.5 Graph fit & separation of powers
- Service `SATISFIED` (up + password set) → binding node becomes **actionable**.
- The binding value is host-derivable, so its fix is **emitted** (deterministic, like the
  closure emit / config-bake) — the LLM is not asked to guess a URL it demonstrably flails on.
- The **host certifies** via the psql probe. `certify` flips state only by running the
  `check_command` — never inferred from the emit running.
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
- `src/python_deps/depgraph/service_scan.py` — compose-`environment:` binding discovery;
  `service_bind_url(kind, port, dbname)` rewrite helper; start-recipe `alter_password` step;
  attach a `Config(env:<VAR>)` node `requires` the service with the psql `check_command`.
- `src/python_deps/depgraph/certify.py` / `src/envstate/depgraph_live.py` — certify the binding
  CONFIG node live (arm-gated), same `allow_service_certify` gating already added for services.
- `src/envstate/synthesis.py` — service-bound env value precedence in the bake (the binding
  value overrides `bakeable_config_env`/ledger for that VAR).
- `agent.py` — emit the live-container `profile.d` bind + password step alongside the existing
  provisioning execution; extend `confirmed_in_image_services` with the bound `var`/`url`.
- `run_repo2run_benchmark.py` — compose the bind `export`/`ENV` into the eval test wrapper from
  the handoff field (alongside the existing start/wait/createdb composition).
- Tests: extend `tests/test_service_provision_off_state.py` (off-state byte-identity covers the
  new paths) + new unit tests per task (discovery, rewrite, precedence, certify gating).

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
