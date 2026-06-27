# Services Tier — In-Image Action Layer (LLM-driven, host-certified)

**Date:** 2026-06-27
**Status:** Design v1 — **in-image provisioning slice** (the deferred §8 action layer, in-image first). Sidecars stay deferred (§12).
**Branch:** john-planner-v3
**Arm:** new sub-arm **`v1gsps`** (graph-scheduler + service-provision), default **off**, built on `v1gs`.
**Extends:**
- `docs/superpowers/specs/2026-06-25-services-tier-design.md` — the discovery/advisory slice (already landed in `15799d5`). This spec builds its deferred §8 action layer.
- `docs/superpowers/specs/2026-06-25-six-tier-environment-world-model-design.md` — Services is tier 5; this is the first tier-5 *certification* (not just representation).

> **Scope of this spec.** Promote a **confirmed** SERVICE node from passive advisory (UNKNOWN, certify-skip-guarded, off-frontier) to a **scheduled, host-certified obligation** the LLM satisfies **in-image** (a daemon started inside the agent's own container), certified by an in-container reachability probe, and reproduced in the scored eval by composing the start into the test-execution wrapper. **In-image only. No separate-container sidecar, no shared `--network` — those need eval-harness rework and stay deferred (§12).** Validated on **Postgres** (the `fastapi-template` case); the mechanism is kind-generic but only Postgres is hardened in this slice.

---

## 1. Problem

The discovery slice reads CI/compose intently — `scan_ci_services` (`.github/workflows/*.yml` `services:`), `scan_compose_services` (`docker-compose*.yml`), `service_from_url`, and the `package→service` table — and surfaces a confidence-annotated SERVICE node plus an advisory block (`advise.py:157-172`). But the block self-declares *"reachability NOT certified here"* and is read-only prompt text. There is **no path that starts a service**:

- `certify.py:62-63` skip-guards SERVICE nodes (`if node.type is NodeType.SERVICE: return graph`) — they stay UNKNOWN.
- `certify.py:25-34` — `Layer.SERVICES` is absent from `_LAYER_ORDER`, so `certify_all` never probes them.
- `emit.py:120-121` excludes SERVICE from the emittable set.

Concrete cost (10-repo honest A/B): `fastapi-template` has 1 DB-free unit test + 4 Postgres-requiring integration tests. `v1gsp` ran the full suite → 3 ERROR (no DB) → correctly refused (honest 0.0). `radical` "passed" it **hollowly** via collect-only (real run 0.2), which the honest scorer zeroes. So neither arm clears it, and the gap is a **missing feature** (provisioning), not a regression. This slice builds that feature the philosophy-consistent way.

---

## 2. Thesis — promote, don't re-architect

The separation of powers is unchanged:

- **Graph = WHAT/WHEN** — a confirmed service is a tier-5 obligation; the scheduler puts it on the frontier (MISSING) once its system prereq is installed.
- **LLM = HOW** — the LLM starts the daemon, guided by a per-base-image **start recipe** carried in the advisory/fix-candidate. It cannot self-finalize.
- **Host = WHETHER** — only an in-container `pg_isready` against a **live** instance flips Service → SATISFIED. The test suite remains the sufficiency oracle.

The *only* change from the discovery slice: a confirmed service stops being inert. We do **not** add an LLM-declared or action-implied success path, do **not** weaken any done-gate, and do **not** re-implement compose in a deterministic emitter (that would collapse HOW into the graph and re-build the most-solved part of the tier — see §10).

**Why LLM-driven start over a deterministic emit recipe** (the decision this spec encodes):
1. Service-start is genuinely heterogeneous across base images / versions (`service postgresql start` vs `pg_ctlcluster <ver> main start` vs `pg_ctl -D … start`, plus `initdb`, `createdb`, auth, env). A fixed recipe is brittle; a host-certified LLM loop adapts.
2. The defensible novelty (design §10) is *inferring the need* and certifying it as a cross-tier node — not orchestrating containers. Deterministic emit spends effort on the commodity part.
3. **Falsifiable beats brittle:** an LLM start that the host *certifies* turns the spec's "in-image is fragile" objection into a self-correcting loop — `pg_isready` fails → obligation stays MISSING → bounded retry → honest give-up.

---

## 3. Scope

**In scope:**
- Certify confirmed in-image services (real `pg_isready`/`nc -z` probe, run in the live agent container).
- Schedule a confirmed service as a frontier obligation, gated behind its System prereq (`apt install postgresql`).
- A per-base-image **start recipe** rendered into the advisory and attached as the service node's fix-candidate.
- Eval reproduction: install baked into the Dockerfile; start + wait + `createdb` composed into the test-execution wrapper.
- Validated on Postgres + `fastapi-template`.

**Out of scope (deferred — §12):** separate-container sidecars / shared `--network`; multi-service ordering (`Service→Service`); non-Postgres kinds hardened (mechanism admits them; only Postgres proven); the verify-sub-suite fallback (`pytest -m unit`) — see §11.

---

## 4. The promotion — four mechanics

All four are gated behind `v1gsps` (§9); off ⇒ byte-identical.

**4.1 Real `check_command` that runs.** A confirmed Postgres service's `check_command` is `pg_isready -h 127.0.0.1 -p <port>` (other kinds: `nc -z 127.0.0.1 <port>`). Host = 127.0.0.1 because this is **in-image** — the daemon runs in the same container; the discovery slice's `host=<kind>` (e.g. `postgres`) is the *sidecar* addressing and is overridden to loopback for in-image certification, matching the eval's `--add-host postgres:127.0.0.1`.

**4.2 `Layer.SERVICES` enters `_LAYER_ORDER`** (`certify.py:25-34`), positioned **after** PIP/SYSTEM/TOOLCHAIN and before TESTS — a service can only be probed once its client driver + server binary are installed, and tests run after it's up.

**4.3 Lift the certify skip-guard for *confirmed* services only.** `certify.py:62-63` becomes: skip-guard remains for `service_confidence == "inferred"` (still UNKNOWN — may be mocked), but a **confirmed** in-image service is certified by running its probe. Certification happens **in the live agent container** (see §6) — this resolves the original F1 objection (the scratch *probe* container is destroyed before tests, but the build-agent's persistent container is exactly where start + probe co-exist).

**4.4 Scheduling.** Once promoted, a confirmed service that is not yet SATISFIED is an actionable MISSING node on the scheduler frontier (`schedule.py:scheduler_frontier` / `graph_scheduler.py`), **requires-blocked** by its System prereq node (§5). The frontier carries the start recipe (§7) so the bounded LLM executor has the HOW.

---

## 5. Cross-tier chain — the necessary structure

The slice introduces a real tier-2→tier-5→tier-6 causal chain in one graph:

```
System(postgresql-server: apt install postgresql)   ── requires ──▶  Service(postgres: start + pg_isready)   ── requires ──▶  Test
            (tier 2, certified by `dpkg -s` / `command -v pg_ctl`)        (tier 5, certified by pg_isready)             (tier 6)
```

- A confirmed Postgres service **requires** a System/Tool node for the server binary (`postgresql` apt package). That node is certified by the existing System-tier path (presence check), and its install is emitted/baked normally.
- The Service obligation is **blocked-by** the System node: the scheduler will not surface "start postgres" until `pg_ctl`/`pg_ctlcluster` exists. This is honest topological ordering, not a heuristic.
- The Test is **blocked-by** the Service (a confirmed service already carries the `Package→Service` / `anchor→Service` requires edge from the discovery slice; the Test/Project consumes it transitively).

This is the six-tier "tier-agnostic certification across cross-tier chains" claim made concrete — a tier-2 obligation gating a tier-5 obligation gating the tests, all certified by `check_command`.

---

## 6. Certification timing — in the live agent container (F1 resolved)

The discovery spec deferred certification because the `sleep infinity` **scratch probe** container used by `build_dep_graph` is destroyed before any test-run container exists (review F1) — you cannot certify reachability against a service that nothing has started in a throwaway container.

In-image dissolves this: the **build-agent runs its action loop in a persistent container** (the live executor behind `depgraph_live` / `build_agent`). The LLM starts the daemon there; the host certifies `pg_isready` **in that same container, in a later cycle**, exactly like any other runtime-feedback re-certification. No new container, no network. Concretely:

- Certification of SERVICE nodes runs on the **live/host executor** path (the same executor that re-certifies after the agent acts), **not** inside `build_dep_graph`'s scratch resolve. `build_dep_graph` keeps SERVICE nodes UNKNOWN; the live certify cycle flips confirmed ones once started.
- This keeps `build_dep_graph` pure and side-effect-free (it never starts a service), and localizes all actuation to the agent loop where the persistent container lives.

---

## 7. The "how to start" recipe (advisory → fix-candidate, not a committed action)

For the detected base-image family and the detected service, the advisory renders a **start recipe** and the same recipe is attached as the service node's `chosen_fix`/fix-candidate so the bounded executor sees it as the HOW for the obligation. For Postgres on a debian-slim base (the common case):

```
SERVICES (provision in-image — host certifies reachability):
  postgres  [confirmed: .github/workflows/ci.yml services.postgres]  port 5432  addresses: DATABASE_URL
    needs (System): postgresql            # tier-2 prereq, scheduled first
    start (candidates, agent adapts):
      service postgresql start            # sysv path
      pg_ctlcluster <ver> main start      # cluster path (init-less slim)
    prepare: createdb <db>  ; ensure trust/peer auth for the configured user
    certify (host runs): pg_isready -h 127.0.0.1 -p 5432
```

The recipe is a **hint**: the LLM picks/adapts (base images differ; some need `initdb` first, some ship a cluster). The host certifies the outcome, not the command. This preserves LLM = HOW and avoids hard-coding a brittle canonical recipe. The recipe text is derived from the detected base family (from the Runtime tier / `ImageSelector` choice) + the service kind; unknown families fall back to the generic `service <kind> start` + probe.

**Connectability vs correctness.** In scope for "the service is usable": server reachable (`pg_isready`), the **bound database exists** (`createdb`), and auth lets the configured user connect (trust/peer for local). Out of scope (the suite's job): schema migrations, seed data, credentials/SSL correctness. The Service certifies *reachable + connectable*; the tests certify *correct* (§ necessary-vs-sufficient).

---

## 8. Eval reproduction — install baked, start composed into the test wrapper

A daemon started in a Dockerfile `RUN` layer dies before `CMD`; each `RUN` is its own process. So slice 1 splits provisioning across the two places the scored eval actually runs code:

1. **Install → Dockerfile (persistent).** `apt-get install -y postgresql` (+ client) bakes into the synthesized Dockerfile as a normal System-tier install layer. Reuse the existing `_coalesce_postgres_build_configuration_commands` (`synthesizer.py:1282-1327`) and `_is_runtime_service_segment` (`synthesizer.py:3807-3817`) recognizers so the install/setup commands the agent ran are coalesced into the image build.
2. **Start + wait + createdb → the test-execution wrapper (live during the scored run).** The start/`pg_isready`/`createdb` sequence is prepended to the test command in the **same shell** as pytest, extending the existing `TEST_EXECUTION_SHELL_WRAPPER` and the `--add-host postgres:127.0.0.1` alias (`run_repo2run_benchmark.py:2872-2884`, `should_add_postgres_host_alias` at `:2820-2847`). Shape:

```sh
# (eval test wrapper, when a confirmed in-image service is present)
service postgresql start || pg_ctlcluster <ver> main start
for i in $(seq 1 30); do pg_isready -h 127.0.0.1 -p 5432 && break; sleep 1; done
createdb <db> 2>/dev/null || true
python -m pytest -q        # the scored command
```

This is what closes the synthesizer-fidelity gap for service repos — the same class of gap that lost md2pdf/pyads (configured-in-sandbox but not reproduced in the fresh eval). The in-sandbox host-certified result and the scored eval now run the *same* start sequence.

The wrapper augmentation fires **only** when the world-model/graph carries a confirmed, SATISFIED in-image service (i.e. the agent actually stood it up and the host certified it) — never speculatively, so non-service repos are byte-identical.

**Hostname parity (in-sandbox ↔ eval).** A repo's `DATABASE_URL` typically names the CI service hostname (e.g. `postgres:5432`), not `127.0.0.1`. The eval already aliases it via `--add-host postgres:127.0.0.1`. For the agent's in-sandbox run to behave identically, the **agent's live container must carry the same alias** (an `/etc/hosts` entry `127.0.0.1 postgres`, added when a confirmed in-image service is present). The certify probe still targets `127.0.0.1` directly (§4.1); the alias only exists so the *tests'* configured hostname resolves to the in-image daemon in both runs.

---

## 9. Off-state byte-identity + the arm

- New env flag `DOCKERAGENT_ENABLE_SERVICE_PROVISION` → arm label **`v1gsps`**, default **off**, layered on `v1gs` (obligations only exist when the graph drives construction). Distinct sub-arm (not folded into `v1gsp`) so it A/B's cleanly against `v1gsp`.
- **Off ⇒ byte-identical:** no SERVICE node is ever promoted, certified, scheduled, or started; `_LAYER_ORDER` excludes SERVICES; the skip-guard stays universal; the eval wrapper is unaugmented. Verified by an off-state snapshot test (the project's standing discipline).
- **On:** §4–§8 active.

---

## 10. Necessary-vs-sufficient & anti-hollow (the safeguard)

- `pg_isready` certifies **reachability + connectability** — *necessary*, not *sufficient*. A reachable-but-unmigrated DB still fails tests; the suite is the sufficiency oracle. This matches the Config tier's presence-vs-value discipline.
- **Only the host probe flips state.** The LLM proposing/running a start command never sets SATISFIED; an action that "looks like" it started a DB never implies success. If the probe fails, the node stays MISSING. This is the anti-hollow invariant, unchanged.
- **Falsifiable backoff:** a confirmed service that can't be certified after the bounded executor's attempts leaves the frontier non-empty; the existing host-grounded give-up gate (host-grounded `diverged` + no actionable frontier + nothing emittable) fires an **honest failure** — no hollow success, no collect-only laundering. In-image genuinely impossible (truly needs a separate container) ⇒ honest refusal, exactly as today, plus the advisory recorded *why*.

---

## 11. Why the verify-sub-suite is NOT bundled

`pytest -m unit` (run only DB-free tests) was floated as a cheaper way to bank `fastapi-template`'s single unit test *without* a database. It is **out of scope here**: if this slice stands Postgres up, **all 5** tests pass — strictly better than 1. The sub-suite fallback is for the genuinely-unprovisionable case (e.g. a service that only a real sidecar can supply) and is a separate slice (it also belongs to the verify-command-discovery work flagged in the multi-language provider-seam handoff). Keeping them separate keeps this spec to one implementation plan.

---

## 12. Non-goals (YAGNI)

- **No separate-container sidecar / shared `--network`** — needs eval-harness rework; the design's §8 "compose-up / docker run + network-attach" stays deferred. In-image is the slice.
- **No `Service→Service` ordering** (kafka→zookeeper) — schema permits it; discovery/scheduling deferred.
- **No non-Postgres hardening** — redis/mongo fall out of the kind-generic mechanism but are not proven in slice 1.
- **No service correctness** (migrations, seed data, credentials, SSL) — the suite's job. Only reachable + bound-DB-exists + connectable auth.
- **No verify-sub-suite** (§11).
- **No new `State` value, no LLM-declared success, no done-gate weakening** — the certification invariant is preserved verbatim.

---

## 13. Testing strategy (TDD)

**Unit (pure, no Docker):**
- `certify` runs the probe and flips a **confirmed** in-image Postgres service to SATISFIED on probe-ok / MISSING on probe-fail; **inferred** services stay UNKNOWN (skip-guard retained). `Layer.SERVICES` present in `_LAYER_ORDER` only affects confirmed nodes.
- Scheduler frontier includes a confirmed MISSING service **only after** its System prereq is SATISFIED (cross-tier blocking, §5); excludes it while the prereq is MISSING.
- Start-recipe render: confirmed service renders the `needs (System)` + `start (candidates)` + `certify` lines for the detected base family; generic fallback for unknown families; inferred services keep the "may be mocked" advisory and get **no** start recipe.
- Synthesizer composes the start/wait/createdb sequence into the test-execution wrapper **only** when a confirmed SATISFIED in-image service is present; absent otherwise.
- Eval wrapper construction (`run_repo2run_benchmark.py`): start+probe prepended in the same shell as pytest; `--add-host` alias present; non-service repo wrapper unchanged.

**Off-state invariant:** `v1gsps` off ⇒ byte-identical world-model + advisory + Dockerfile + eval wrapper (snapshot test).

**e2e validation:** `fastapi-template` on `v1gsps` — full suite goes 0 → pass once Postgres is certified up; the honest scorer (fresh rebuild) reproduces it (proves §8 fidelity). Regression check: `v1gsp` repos (wafw00f, memU, duckdb, pyads, looplive) stay green and byte-identical under `v1gsps`-off; and unchanged under `v1gsps`-on for repos with no confirmed service.

---

## 14. Phasing (for the implementation plan)

| Phase | Scope |
|---|---|
| **A — certify** | `Layer.SERVICES` in `_LAYER_ORDER`; confirmed-only skip-guard lift; in-image loopback host; live-executor certification wiring (§4, §6). |
| **B — schedule** | System-prereq `requires` edge for confirmed services; frontier surfaces the blocked obligation; start recipe as fix-candidate (§5, §7). |
| **C — recipe render** | advisory start-recipe block (base-family aware, generic fallback) (§7). |
| **D — eval fidelity** | synthesizer install-bake reuse + test-wrapper start composition; eval `--add-host` + start prepend (§8). |
| **E — arm + off-state** | `DOCKERAGENT_ENABLE_SERVICE_PROVISION` / `v1gsps`; off-state byte-identity snapshot (§9). |
| **F — e2e** | fastapi-template validation + v1gsp regression (§13). |

Each phase ends with an independently testable deliverable; A–C are pure-unit, D touches synthesizer+eval, E is the flag, F is the VM run.

---

## 15. Novelty — scoped honestly

Standing up + health-probing a service ≈ testcontainers/compose; this slice does **not** claim that. What survives as the contribution: an agent that **infers a service requirement, represents it as a certified cross-tier obligation, drives an LLM to satisfy it in-image, and lets the host falsify the result** — with no human-written fixture/compose file, and with anti-hollow-success preserved end-to-end. The actuation is where representation becomes value (the discovery slice's reviewers were right that advisory alone doesn't move the agent); this is that step, kept inside the separation of powers.

---

## 16. Open questions

1. **Recipe source for the base family.** The start recipe needs the base-image family (debian/alpine/rhel) to pick `service` vs `pg_ctlcluster` vs `initdb`. Provisionally derive it from the Runtime-tier base image (`ImageSelector` choice / `runtime_base`); if unavailable, emit the generic `service <kind> start` + probe and let the LLM adapt. Confirm this is enough vs. detecting the family explicitly.
2. **`createdb`/auth depth.** Provisionally: ensure server reachable + bound DB exists + trust/peer auth for the local user. If a repo's `DATABASE_URL` names a specific user/password, do we create that role, or rely on trust auth + the test's own fixture? Lean: trust auth + `createdb <db>`; roles are the suite's fixture unless a confirmed CI `services.env` declares them.
3. **Multi-service repos in-image.** If a confirmed repo needs Postgres *and* Redis in-image, slice 1 starts both as independent obligations (no ordering). Confirm independent-start is acceptable for the proof (ingestr-style ordered topologies stay deferred, §12).
