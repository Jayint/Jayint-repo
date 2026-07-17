# Services Tier — In-Image Action Layer (LLM-driven, host-certified)

**Date:** 2026-06-27
**Status:** Design **v2** — **in-image provisioning slice** (the deferred §8 action layer, in-image first). Sidecars stay deferred (§12).
**Branch:** john-planner-v3
**Arm:** new sub-arm **`v1gsps`** (service-provision), default **off**, layered on **`v1gsp`** (which already includes `v1gs` graph-scheduler + runtime-pin — so the feature is tested on the validated arm). The name reads `v1gsp` + `s` and is consistent with that layering.
**Extends:**
- `docs/superpowers/specs/2026-06-25-services-tier-design.md` — the discovery/advisory slice (landed in `15799d5`). This spec builds its deferred §8 action layer.
- `docs/superpowers/specs/2026-06-25-six-tier-environment-world-model-design.md` — Services is tier 5; this is the first tier-5 *certification* (not just representation).

> **v2 changelog (after a 4-reviewer adversarial pass).** v1 was philosophically sound but under-specified against the real code. v2 closes the code-verified blockers: the **second** SERVICE exclusion in `schedule.py` (not just the certify skip-guard); the `pg_isready` **loopback** probe site; **`EDGE_RULES` legality** of the cross-tier edge; **Postgres-as-root** rejection; the **graph→eval handoff channel** (the md2pdf/pyads divergence); **`createdb`-fatal + done-branch-requires-certified-service** (closes a hollow-0.2 path); **arm-gated `_LAYER_ORDER`** (off-state byte-identity); the **`chosen_fix` field collision**; the **`_is_runtime_service_segment` recognizer gap** (`pg_ctlcluster` unmatched); and a **Phase D split**. Sections changed: §3, §4 (+ new §4.5), §5, §7, §8, §9, §10, §13, §14, §16.

> **Scope of this spec.** Promote a **confirmed** SERVICE node from passive advisory (UNKNOWN, certify-skip-guarded, scheduler-excluded, off-frontier) to a **scheduled, host-certified obligation** the LLM satisfies **in-image** (a daemon started inside the agent's own container), certified by an in-container reachability probe against **loopback**, and reproduced in the scored eval by composing the start into the test-execution wrapper via an **explicit handoff field** (never a text heuristic). **In-image only. No separate-container sidecar, no shared `--network`.** Validated on **Postgres** (the `fastapi-template` case); the mechanism is kind-generic but only Postgres is hardened in this slice.

---

## 1. Problem

The discovery slice reads CI/compose intently — `scan_ci_services` (`.github/workflows/*.yml` `services:`), `scan_compose_services` (`docker-compose*.yml`), `service_from_url`, and the `package→service` table — and surfaces a confidence-annotated SERVICE node plus an advisory block (`advise.py:157-172`). But the block self-declares *"reachability NOT certified here"* and is read-only prompt text. There is **no path that starts a service**, and there are now **three** independent gates proving it:

- `certify.py:62-63` skip-guards SERVICE nodes (`if node.type is NodeType.SERVICE: return graph`) — they stay UNKNOWN.
- `certify.py:25-34` — `Layer.SERVICES` is absent from `_LAYER_ORDER`, so `certify_all` never probes them.
- `schedule.py:34` — `_is_actionable` returns `False` for `NodeType.SERVICE` (`and node.type is not NodeType.SERVICE  # services flow through the sufficiency branch in v1`), so the scheduler frontier never surfaces one.
- `emit.py:120-121` excludes SERVICE from the emittable set.

Concrete cost (10-repo honest A/B): `fastapi-template` has 1 DB-free unit test + 4 Postgres-requiring integration tests. `v1gsp` ran the full suite → 3 ERROR (no DB) → correctly refused (honest 0.0). `radical` "passed" it **hollowly** via collect-only (real run 0.2), which the honest scorer zeroes. So neither arm clears it, and the gap is a **missing feature** (provisioning), not a regression. This slice builds that feature the philosophy-consistent way.

---

## 2. Thesis — promote, don't re-architect

The separation of powers is unchanged:

- **Graph = WHAT/WHEN** — a confirmed service is a tier-5 obligation; the scheduler puts it on the frontier (MISSING) once its system prereq is installed.
- **LLM = HOW** — the LLM starts the daemon, guided by a per-base-image **start recipe** carried in node `data` (not `chosen_fix` — §7). It cannot self-finalize.
- **HOST = WHETHER** — only an in-container `pg_isready` against a **live** instance flips Service → SATISFIED. The test suite remains the sufficiency oracle.

The *only* change from the discovery slice: a confirmed service stops being inert. We do **not** add an LLM-declared or action-implied success path, do **not** weaken any done-gate, and do **not** re-implement compose in a deterministic emitter (that would collapse HOW into the graph and re-build the most-solved part of the tier — see §10/§15).

**Why LLM-driven start over a deterministic emit recipe** (the decision this spec encodes):
1. Service-start is genuinely heterogeneous across base images / versions (`service postgresql start` vs `pg_ctlcluster <ver> main start` vs `pg_ctl -D … start`, plus `initdb`, `createdb`, auth, env, **and the as-`postgres`-user requirement** — §7). A fixed recipe is brittle; a host-certified LLM loop adapts.
2. The defensible novelty (design §10/§15) is *inferring the need* and certifying it as a cross-tier node — not orchestrating containers.
3. **Falsifiable beats brittle:** an LLM start that the host *certifies* turns the spec's "in-image is fragile" objection into a self-correcting loop — `pg_isready` fails → obligation stays MISSING → bounded retry → honest give-up.

---

## 3. Scope

**In scope:**
- Certify confirmed in-image services (real `pg_isready`/`nc -z` probe **against loopback**, run in the live agent container).
- Schedule a confirmed service as a frontier obligation, gated behind its System prereq (`apt install postgresql`).
- A per-base-image **start recipe** (root-aware, version-resolved) rendered into the advisory and attached to the service node's `data["start_recipe"]`.
- Eval reproduction via an explicit **handoff field** (§8): install baked into the Dockerfile; start + wait + `createdb` composed into the test-execution wrapper.
- Validated on Postgres + `fastapi-template`.

**Out of scope (deferred — §12):** separate-container sidecars / shared `--network`; multi-service ordering (`Service→Service`); non-Postgres kinds hardened (mechanism admits them; only Postgres proven); the verify-sub-suite fallback (`pytest -m unit`) — see §11.

**In-image departs from the discovery slice's closure model — state it explicitly.** The discovery spec (§2) treated the server as a *closure sink* that "runs in its own image, does not consume our pip/apt closure" — that was the **sidecar** assumption. **In-image inverts it:** we `apt install postgresql`, so the server binary **does** consume our apt closure and becomes a real **System-tier obligation** in our graph. §5's cross-tier edge follows directly from this departure.

---

## 4. The promotion — mechanics

All gated behind `v1gsps` (§9); off ⇒ byte-identical.

**4.1 Real `check_command` that runs, against loopback.** A confirmed Postgres service is certified by `pg_isready -h 127.0.0.1 -p <port>` (other kinds: `nc -z 127.0.0.1 <port>`). **The loopback override has a named site:** the discovery slice's `_service_node` stores `data["host"]` = the CI service name (e.g. `"postgres"`) and bakes it into `check_command`. The certify path (§6) **rewrites the probe host to `127.0.0.1` at certification time** — it does *not* use the stored `check_command` verbatim, and does *not* mutate `data["host"]` (which stays `"postgres"` for advisory/handoff fidelity). Concretely: the SERVICE branch of `certify` builds its probe string from `(kind, port)` with host pinned to loopback, because in-image the daemon shares the container's loopback. This is why §1's stored `pg_isready -h postgres …` never runs as-is.

**4.2 `Layer.SERVICES` enters `_LAYER_ORDER` — arm-gated, not module-level.** `_LAYER_ORDER` is a module-level tuple in `certify.py`; adding SERVICES to it unconditionally would make *every* arm (`v1gsp`, `v1gs`) iterate SERVICE nodes and break off-state byte-identity (Red-team A8). Instead, `certify_all` takes the layer order as a **parameter** (or reads an arm-gated constant): under `v1gsps` the order includes `Layer.SERVICES` (positioned **after** PIP/SYSTEM/TOOLCHAIN, before TESTS — a service is probed only once its client driver + server binary exist, and before tests run); off-arm it is the existing tuple, unchanged.

**4.3 Lift the certify skip-guard for *confirmed* services only.** `certify.py:62-63` becomes conditional: the skip-guard **remains** for `data["service_confidence"] == "inferred"` (still UNKNOWN — may be mocked) and for any arm other than `v1gsps`; a **confirmed** in-image service under `v1gsps` is certified by running its loopback probe (§4.1).

**4.4 Scheduling — lift the SERVICE exclusion too.** Certification alone is insufficient: `schedule.py:34`'s `_is_actionable` independently excludes every SERVICE node. Under `v1gsps`, that exclusion is relaxed to skip **only inferred** services: a **confirmed** service with `state=MISSING` and its System prereq SATISFIED (§5) becomes frontier-eligible, carrying `data["start_recipe"]` (§7) as the HOW. Off-arm, the exclusion is unchanged. See §4.5.

**4.5 Existing blocking invariants to lift (enumerated so no phase rediscovers them mid-task).**
- **`schedule.py:34`** — `_is_actionable`'s `and node.type is not NodeType.SERVICE`. Relax to confirmed-only **under the arm** (Phase B). Off-arm byte-identical.
- **`certify.py:25-34`** — `_LAYER_ORDER` excludes SERVICES; parameterize/arm-gate (Phase A, §4.2).
- **`certify.py:62-63`** — universal SERVICE skip-guard; make confirmed-only under the arm (Phase A, §4.3).
- **`sandbox.py` container launch** — no `extra_hosts`. The agent's live container needs `postgres → 127.0.0.1` so the *tests'* configured hostname resolves to the in-image daemon (the probe itself uses loopback directly — §4.1). `extra_hosts` must be set at `containers.run(...)` time, so the arm flag threads into `Sandbox.__init__`/`_setup_initial_container` (Phase E). Off-arm: never injected.

---

## 5. Cross-tier chain — the necessary structure (and its schema change)

The slice introduces a real tier-2 → tier-5 → tier-6 causal chain in one graph:

```
SystemLib(postgresql: apt install)   ◀── requires ──  Service(postgres: start + pg_isready)   ◀── requires ──  Test
   (tier 2, certified by `dpkg -s`/`command -v pg_ctl`)        (tier 5, certified by loopback pg_isready)         (tier 6)
```

**EDGE_RULES legality — the schema change this requires.** Today `schema.py` `EDGE_RULES["requires"]` allows sources `{Test, Project, Import, Package}` only; `Service` is **not** a permitted `requires` source, so `Service → SystemLib` would raise `ValueError` at `with_edge`/`_validate_edge`. **Decision (Model A):** add `Service` to the `requires` **source** set, making `Service → SystemLib`/`Service → Tool` legal. This is semantically justified by the in-image closure departure (§3): in-image, a service genuinely *requires* its server binary installed in our closure. The change is one line in `EDGE_RULES` + a schema test; it does not loosen any other relation (Service is added only as a *source*, only for `requires`).

- The confirmed Postgres service **requires** the `SystemLib(postgresql)` node (server binary). That node certifies via the existing System-tier presence check and installs via normal emit (baked into the Dockerfile — §8).
- `_dependencies_satisfied` (`schedule.py`) walks `requires`; with the edge present, the Service obligation is **not** frontier-eligible until `SystemLib(postgresql)` is SATISFIED. This is the honest topological gate (Red-team A3: without the edge, the gate silently doesn't exist and Postgres could be scheduled before `pg_ctl`).
- The Test is **blocked-by** the Service via the discovery slice's existing `Package→Service` / `anchor→Service` edge consumed transitively. **Caveat (Red-team A7):** this edge must **not** convert the scheduler's `run_tests()` done-check into a hard graph gate — `run_tests()` is the sufficiency oracle; a suite that passes while a confirmed service is UNSATISFIED (it mocked the DB) is a valid result. The scheduler promotes a confirmed service to the frontier **only when `run_tests()` fails AND the service is MISSING** (§10), never speculatively.

*(Model B considered — no schema change; instead draw the legal `Test → SystemLib(postgresql)` and rely on `_LAYER_ORDER` tier ordering for sequencing. Rejected: tier order governs certify order, not the scheduler frontier, so it gives no edge-level gate that the service-start waits for the install. Model A's explicit edge is the clean guarantee.)*

---

## 6. Certification timing — in the live agent container (F1 resolved)

The discovery spec deferred certification because the `sleep infinity` **scratch probe** container used by `build_dep_graph` is destroyed before any test-run container exists (review F1).

In-image dissolves this: the **build-agent runs its action loop in a persistent container** (the live executor behind `depgraph_live`/`build_agent`). The LLM starts the daemon there; the host certifies `pg_isready` **in that same container, on a later cycle** (the existing re-certify-on-next-cycle pattern — `_dep_emit_phase` → `certify_refresh` runs before the executor each cycle, so the flip to SATISFIED lands the cycle *after* the LLM starts it). Verified feasibility points:

- A daemon started by a mutating `sandbox_execute` call **persists** across subsequent `exec_run`/`exec_readonly` calls — they exec into the *same* container PID namespace, so the read-only `pg_isready` probe reaches the daemon.
- `build_dep_graph` stays pure: it never starts or certifies a service. All actuation + certification live in the agent loop's persistent container.
- **Rollback caveat:** `_restore_last_success_container` replaces the container and replays runtime commands; the replayed start must use the as-`postgres`-user form (§7) or it fails silently as root. The replay recognizer must also match `pg_ctlcluster` (§8, Feasibility I3).

---

## 7. The "how to start" recipe (root-aware, version-resolved; in `data`, not `chosen_fix`)

For the detected base-image family and detected service, a **start recipe** is rendered into the advisory and stored on the service node as **`data["start_recipe"]`** — *not* `chosen_fix`. (`chosen_fix` carries the `service:<image>` string parsed by `_is_emittable`/`_is_reciped`; overwriting it with shell text breaks those — Architecture I4. The scheduler's `packet_to_task` renders `data["start_recipe"]` into the obligation's facts.)

For Postgres on a debian-slim base (the common case):

```
SERVICES (provision in-image — host certifies reachability):
  postgres  [confirmed: .github/workflows/ci.yml services.postgres]  port 5432  addresses: DATABASE_URL
    needs (System): postgresql                       # tier-2 prereq, scheduled first (§5 requires edge)
    start (candidates, agent adapts — MUST run as the postgres user):
      runuser -u postgres -- pg_ctlcluster <ver> main start     # init-less slim; preferred
      su - postgres -c "pg_ctlcluster <ver> main start"          # equivalent
      service postgresql start                                   # sysv path (still drops to postgres internally)
    prepare:  runuser -u postgres -- createdb <db>               # <db> from DATABASE_URL path; FATAL on failure
    certify (host runs):  pg_isready -h 127.0.0.1 -p 5432
```

Three hard requirements the recipe encodes (each a code-verified review finding):

- **Run as the `postgres` user (Feasibility C1).** The sandbox runs as uid 0 (`sandbox.py` launches with no `user=`); Postgres refuses to start as root. Every start/`createdb`/`psql` candidate is wrapped in `runuser -u postgres --` / `su - postgres -c`.
- **Resolve `<ver>` (Feasibility C2).** `pg_ctlcluster` needs the major version (15 on bookworm-slim, 13 on bullseye). Resolve from the Runtime-tier base image (`runtime_base`/`ImageSelector` choice — §16 Q1 is now closed as a lookup, not a design question); if the base family is unknown, fall back to `service postgresql start` (which discovers the cluster) + the probe, and let the LLM adapt.
- **Preflight: separate actions (Feasibility I5).** The agent issues **start**, **wait/verify**, and **createdb** as *distinct* obligations/actions — the sandbox's compound-setup preflight rejects a single command that bundles multiple setup mutations. (The eval *wrapper*, §8, is outside preflight and may chain them.)

The recipe is a **hint**: the host certifies the outcome, not the command. This preserves LLM = HOW.

**Connectability vs correctness.** In scope: server reachable (`pg_isready`), the **bound database exists** (`createdb`, fatal — §8), and local trust/peer auth lets the configured user connect. Out of scope (the suite's job): schema migrations, seed data, credentials/SSL. The Service certifies *reachable + connectable*; the tests certify *correct*.

---

## 8. Eval reproduction — explicit handoff field, install baked, start composed into the wrapper

A daemon started in a Dockerfile `RUN` layer dies before `CMD`. So provisioning splits across the two places the scored eval actually runs code, **driven by an explicit field — never a text heuristic** (this is the highest-danger gap, Red-team A2):

**8.1 The handoff field (closes the graph→eval divergence).** At finalization the agent writes to `run_summary`:

```json
"confirmed_in_image_services": [
  {"kind": "postgres", "port": 5432, "db": "<from DATABASE_URL>", "ver": "15",
   "start_cmd": "runuser -u postgres -- pg_ctlcluster 15 main start"}
]
```

The eval harness reads **this field** (not `should_add_postgres_host_alias`'s regex) to decide what to inject. The field is written **only** when the world-model carries a confirmed service that was **certified SATISFIED** in-sandbox (the agent actually stood it up). Absent the field, the eval is byte-identical to today.

**8.2 Install → Dockerfile (persistent).** `apt-get install -y postgresql` (+ client) bakes in via the **normal System-tier emit path** for the `SystemLib(postgresql)` node — *not* via the postgres coalescer. (Clarification vs v1, Feasibility I3 / Red-team A5: `_coalesce_postgres_build_configuration_commands` is for build-time cluster setup we are **not** doing; **no daemon and no `createdb` at build time** — they'd die between layers. The apt install is the only Dockerfile-baked piece.)

**8.3 Start + wait + createdb → the test-execution wrapper (live during the scored run).** Read from the handoff field, the eval composes, in the *same* bash session as pytest (extending `build_test_execution_script`'s `runtime_commands` and `TEST_EXECUTION_SHELL_WRAPPER`, `run_repo2run_benchmark.py`):

```sh
runuser -u postgres -- pg_ctlcluster 15 main start      # from start_cmd; runs as postgres
for i in $(seq 1 30); do pg_isready -h 127.0.0.1 -p 5432 && break; sleep 1; done
runuser -u postgres -- createdb <db>                    # FATAL — no `|| true` (Red-team A1)
python -m pytest -q                                     # the scored command
```

- **`createdb` is fatal** (no `|| true`): a missing-DB failure must make the wrapper exit non-zero so the eval counts a failure, never a silent green. `<db>` is inferred from the bound `DATABASE_URL` path component (recorded in the handoff field), so wrapper-`<db>` and app-`<db>` match.
- The eval also adds `--add-host postgres:127.0.0.1` (the existing `run_repo2run_benchmark.py:2876` mechanism), **driven by the handoff field** so the tests' `postgres` hostname resolves to the in-image daemon. (`_is_runtime_service_segment` must be **extended to match `pg_ctlcluster`** — Feasibility I3 — so the start command is recognized as a runtime, not build-time, segment.)

**8.4 Hostname parity (in-sandbox ↔ eval).** A repo's `DATABASE_URL` typically names the CI hostname (`postgres:5432`), not `127.0.0.1`. Both containers carry the alias: the agent's live sandbox via `extra_hosts={"postgres":"127.0.0.1"}` at launch (§4.5, Phase E); the eval via `--add-host` (§8.3). The certify probe targets `127.0.0.1` directly (§4.1); the alias only exists so the *tests'* configured hostname resolves in both runs.

This is what closes the synthesizer-fidelity gap for service repos (the class that lost md2pdf/pyads).

---

## 9. Off-state byte-identity + the arm

- New env flag `DOCKERAGENT_ENABLE_SERVICE_PROVISION` → arm label **`v1gsps`**, default **off**, layered on `v1gsp`. Add it to the arm ladder and the child-process arm-detection block (`run_rat_benchmark.py`) as a Phase-E task. Distinct sub-arm (not folded into `v1gsp`) so it A/B's cleanly against `v1gsp`.
- **Off ⇒ byte-identical across every touch-point** (enumerated so the snapshot test covers them all):
  - `certify.py` — `_LAYER_ORDER` excludes SERVICES off-arm (§4.2); skip-guard universal off-arm (§4.3).
  - `schedule.py` — SERVICE exclusion intact off-arm (§4.4).
  - `advise.py` — render unchanged (the discovery advisory block is the same; no start-recipe lines off-arm).
  - `sandbox.py` — no `extra_hosts` off-arm (§4.5).
  - `run_repo2run_benchmark.py` — no handoff field is written off-arm, so the wrapper/`--add-host` path is unchanged (§8). The pre-existing `should_add_postgres_host_alias` heuristic is **left as-is** off-arm (it already fires today on text; this slice does not change it — it adds a *field-driven* path that supersedes it on-arm).
  - `synthesizer.py` — the `_is_runtime_service_segment` extension is inert unless a `pg_ctlcluster` command is present (only on-arm provisioning emits one).
- **On:** §4–§8 active.

---

## 10. Necessary-vs-sufficient & anti-hollow (the safeguard, hardened in v2)

- `pg_isready` certifies **reachability + connectability** — *necessary*, not *sufficient*. The suite is the sufficiency oracle. Only the host probe flips state; no action "looking like" a started DB implies success.
- **Done-branch requires the certified service (new in v2, closes Red-team A1/A6).** For a repo where a confirmed in-image service was **promoted**, the scheduler may accept `done` only if that service is **certified SATISFIED** (host-probed reachable) — not on `run_tests()` alone. This converts the dangerous "postgres never came up, but the 1 pre-existing unit test passed → live, non-zeroed 0.2" into an **honest give-up**. (For repos where no service was promoted — e.g. an inferred/mocked DB the suite stubs — `run_tests()` remains the sole oracle, §5 caveat.)
- **`createdb` fatal (§8.3)** removes the `|| true` path that could let DB-dependent tests skip into a false green.
- **Acknowledged out-of-scope gap:** the honest scorer does not zero a *mixed* "1 passed + N skipped" run (only all-skip/collect-only). This slice does **not** fix that scorer gap; it *avoids triggering it* via the certify-gate above (we don't claim done unless the service is genuinely up) — so a real service-repo can't ride its one pre-existing unit test to a hollow number.
- **Falsifiable backoff:** a confirmed service that can't be certified after the bounded executor's attempts leaves the frontier non-empty; the existing host-grounded give-up gate fires an **honest failure** — no hollow success, no collect-only laundering.

---

## 11. Why the verify-sub-suite is NOT bundled

`pytest -m unit` was floated as a cheaper way to bank `fastapi-template`'s single unit test *without* a database. **Out of scope here:** if this slice stands Postgres up, **all 5** tests pass — strictly better than 1. The sub-suite fallback is for the genuinely-unprovisionable case (a service only a real sidecar can supply) and is a separate slice (it also belongs to the verify-command-discovery work in the multi-language provider-seam handoff). Keeping them separate keeps this spec to one implementation plan.

---

## 12. Non-goals (YAGNI)

- **No separate-container sidecar / shared `--network`** — needs eval-harness rework; the design's §8 "compose-up / docker run + network-attach" stays deferred.
- **No build-time daemon or build-time `createdb`** — a `RUN`-layer daemon dies before `CMD`; all start/createdb happens at runtime in the wrapper (§8.2).
- **No `Service→Service` ordering** (kafka→zookeeper) — schema permits it; discovery/scheduling deferred.
- **No non-Postgres hardening** — redis/mongo fall out of the kind-generic mechanism but are not proven in slice 1.
- **No service correctness** (migrations, seed data, credentials, SSL) — the suite's job. Only reachable + bound-DB-exists + connectable auth.
- **No verify-sub-suite** (§11).
- **No new `State` value, no LLM-declared success, no done-gate weakening** — the certification invariant is preserved verbatim (and §10 *strengthens* the service-repo done-gate).

---

## 13. Testing strategy (TDD)

**Unit (pure, no Docker):**
- **Certify:** a **confirmed** Postgres service's certify builds a **loopback** probe (`-h 127.0.0.1`) regardless of `data["host"]="postgres"`, and flips SATISFIED on probe-ok / MISSING on probe-fail; **inferred** services stay UNKNOWN (skip-guard retained); off-arm, all SERVICE nodes stay UNKNOWN and `_LAYER_ORDER` excludes SERVICES.
- **Schema:** `EDGE_RULES` now admits `Service → SystemLib` (`requires`); `Service` as a `requires` source does not enable any other illegal relation.
- **Scheduler:** with the §4.4 lift, a confirmed MISSING service appears on the frontier **only after** `SystemLib(postgresql)` is SATISFIED (the §5 edge), and **only when `run_tests()` failed**; inferred services never appear; off-arm the frontier is unchanged (SERVICE excluded).
- **Recipe:** render is root-wrapped (`runuser -u postgres`), `<ver>` resolved from the base family with a generic fallback, stored in `data["start_recipe"]` (not `chosen_fix`); inferred services keep "may be mocked" and get no recipe.
- **Synthesizer:** `_is_runtime_service_segment` matches `pg_ctlcluster … start`; `apt install postgresql` bakes into build commands; no daemon/`createdb` is baked.
- **Eval wrapper:** when `run_summary["confirmed_in_image_services"]` is present, the start/wait/`createdb` block (root-wrapped, `createdb` fatal — no `|| true`) is prepended in the same shell as pytest and `--add-host` is added; absent the field, the wrapper + `--add-host` path is byte-identical.
- **Anti-hollow done-gate:** for a promoted-service repo, `done` is rejected unless the service node is SATISFIED; a non-service repo's done-gate is unchanged.

**Off-state invariant:** `v1gsps` off ⇒ byte-identical across all §9 touch-points (snapshot test spanning world-model, advisory, Dockerfile, eval wrapper, certify, schedule, sandbox launch).

**e2e validation:** `fastapi-template` on `v1gsps` — full suite goes 0 → pass once Postgres is certified up; the honest scorer (fresh rebuild) reproduces it via the handoff field (proves §8 fidelity). Regression: `v1gsp` repos (wafw00f, memU, duckdb, pyads, looplive) stay green and byte-identical under `v1gsps`-off, and unchanged under `v1gsps`-on for repos with no confirmed service (esp. an *inferred/mocked* DB repo must NOT gain a blocking obligation).

---

## 14. Phasing (for the implementation plan)

| Phase | Scope | Files |
|---|---|---|
| **A — certify** | loopback-rewrite probe (§4.1); `_LAYER_ORDER` arm-gated/parameterized (§4.2); skip-guard confirmed-only (§4.3); **`EDGE_RULES` add `Service` source + draw `Service→SystemLib` edge** (§5). | `certify.py`, `schema.py`, `service_scan.py` |
| **B — schedule** | lift `schedule.py:34` SERVICE exclusion (confirmed-only, arm-gated, prereq-blocked, only-when-tests-fail) (§4.4, §5); `data["start_recipe"]` → obligation facts. | `schedule.py`, `graph_scheduler.py` |
| **C — recipe render** | root-wrapped, `<ver>`-resolved, base-family start recipe + advisory block; generic fallback (§7). | `advise.py`, `service_scan.py`/recipe module |
| **D1 — synthesizer** | `apt install postgresql` → build commands; extend `_is_runtime_service_segment` for `pg_ctlcluster`; ensure start/`createdb` are runtime-only (§8.2). | `synthesizer.py` |
| **D2 — eval harness** | read `run_summary["confirmed_in_image_services"]`; compose start/wait/`createdb` (root-wrapped, `createdb` fatal) into the wrapper; field-driven `--add-host` (§8.1/§8.3). | `run_repo2run_benchmark.py`, `multi_docker_eval_adapter.py` |
| **E — arm + sandbox + off-state** | `DOCKERAGENT_ENABLE_SERVICE_PROVISION`/`v1gsps` (+ arm ladder + child-proc detection); `sandbox.py` `extra_hosts` arm-gated; write the handoff field at finalization; off-state byte-identity snapshot (§9). | `agent.py`, `run_rat_benchmark.py`, `sandbox.py`, `multi_docker_eval_adapter.py` |
| **F — e2e** | fastapi-template validation + done-branch-requires-certified-service check + v1gsp regression (§13). | VM run |

A–C are pure-unit; D1/D2 are split because they live in different files with different reviewers/failure-modes (Scope I4); E threads the arm flag + sandbox + handoff write; F is the VM run.

---

## 15. Novelty — scoped honestly

Standing up + health-probing a service ≈ testcontainers/compose; this slice does **not** claim that. The contribution: an agent that **infers a service requirement, represents it as a certified cross-tier obligation, drives an LLM to satisfy it in-image, and lets the host falsify the result** — with no human-written fixture/compose file, and with anti-hollow-success preserved end-to-end (and *strengthened* for service repos, §10). Actuation is where representation becomes value; this is that step, kept inside the separation of powers.

---

## 16. Open questions

1. ~~Base-family recipe source~~ **Resolved** (was an open question, is a lookup): derive `<ver>` + family from the Runtime-tier base image (`runtime_base`/`ImageSelector`); generic `service postgresql start` + probe fallback when unknown. (§7.)
2. **`createdb`/auth depth.** Resolved for connectability: `createdb <db>` (fatal) + local trust/peer auth for the configured user; migrations/seed are the suite's job. **Still open:** if a repo's `DATABASE_URL` names a non-default role/password, do we `CREATE ROLE`, or rely on trust auth + the suite's own fixture? Lean: trust auth + `createdb`; create a role only when a confirmed CI `services.env` declares it.
3. **Multi-service repos in-image.** Slice 1 starts each confirmed service as an independent obligation (no ordering). Confirm independent-start is acceptable for the proof (ordered topologies stay deferred, §12).
4. **EDGE_RULES change acceptance.** §5 adds `Service` as a `requires` source (Model A). Confirm this is the preferred model over Model B (legal `Test→SystemLib` + tier ordering, no schema change but a weaker scheduling guarantee).
