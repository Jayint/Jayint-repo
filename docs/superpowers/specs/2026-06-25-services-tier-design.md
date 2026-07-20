# Services Tier (Tier 5) — Design Spec

**Date:** 2026-06-25
**Status:** Design v3 — **advisory/discovery-only** (build the graph now; action layer deferred). See §14 for the v1→v2→v3 history.
**Branch:** john-planner-v3
**Extends:** `docs/superpowers/specs/2026-06-25-six-tier-environment-world-model-design.md` (Services is tier 5). The Config slice already landed the shared schema scaffolding (`NodeType.SERVICE`, `tier=5`, `requires → Service` in `EDGE_RULES`).

> **Scope of this spec.** Build the *graph representation* of services — discover service needs from a repo, append `SERVICE` nodes to the depgraph, and render them in the advisory — **entirely inside `python_deps/depgraph/`**, mirroring the Config slice. **No emit, no runner orchestration, no live reachability certification.** Those (the "action layer") are deferred to a separate, runner-level spec (§8), because they cannot live inside `build_dep_graph` (the scratch probe container is destroyed before the test-run container exists — review **F1**). This slice keeps the model fixes the review surfaced, because those are about the graph being *correct*, not about acting on it.

---

## 1. Problem

The depgraph models the installable environment but has no representation of the **services** a test suite connects to (Postgres, Redis, brokers). A missing service surfaces to the agent as a raw `OperationalError` traceback, with no structured, cross-tier signal that a service is needed, why, and how it's addressed. This slice gives the graph that representation.

We deliberately stop at the representation. *Certifying* a service requires it to be running and reachable — which the single `sleep infinity` scratch executor cannot provide, and which must be orchestrated at the runner level (above `build_dep_graph`), not inside the pipeline. So service nodes here are **discovered, not certified**; the action layer that runs/certifies them is a separate future spec (§8).

---

## 2. Thesis — client/server split; discover, don't (yet) act

**The client/server split (the sound core).** A service is two things:

- **The server** (`postgres:16`) — runs in its *own* image, self-contained, a closure **sink**: it does **not** consume our pip/apt closure.
- **The client access path** — to *talk* to it, our code needs a driver `Package` (`psycopg2`) needing a `SystemLib` (`libpq`). This chain **already exists** in the graph (tier 4→2) and sits **beside** the service, both converging on the consumer.

So a Service does not connect *downward* into packages/system; the package/system wiring is the *client driver's* chain.

**This slice builds the representation.** Discovery (§5) appends `SERVICE` nodes (confidence-annotated: **confirmed** vs **inferred**) and renders them in the advisory (§6). The graph thereby gains the cross-tier picture — `Package(psycopg2) … Service(postgres) … Config(DATABASE_URL)` — that a flat resolver never had. **Acting** on it (emit a sidecar, certify reachability) is the deferred action layer (§8).

---

## 3. Schema

Already present (Config slice Phase 0): `NodeType.SERVICE`, `TYPE_TO_TIER[SERVICE]=5`, `Service` in the `requires` destination set.

This spec adds:

1. **`ids.service_id(name) -> "service:<name>"`** (e.g. `service:postgres`).
2. **A confidence annotation, NOT a new `State`** (review **M3/F4**). The 3-valued `state` invariant — flipped only by a `check_command` — is preserved. A discovered service is **`state=UNKNOWN`** (honest: not host-certified here) plus `data["service_confidence"] = "confirmed" | "inferred"`. No `DECLARED` state member.
3. **Services are NOT certified in this slice.** Add an early-return in `certify` for `NodeType.SERVICE` (live certification only) so they stay `UNKNOWN`, and do **not** add `Layer.SERVICES` to `_LAYER_ORDER`. This prevents `certify_all` from running `nc -z` in the sidecar-less scratch container and falsely flipping them `MISSING` (review **F4**).
4. **The Config binding is node metadata, not a `requires` edge** (review **M2**): a Service carries `data["bound_config"] = "DATABASE_URL"` plus the `host`/`port` parsed from that Config node's discovered URL. We do **not** draw `Config → Service` as `requires` (that forces an addressing relation onto a dependency edge).

**Node shape:**
```
id            = service:<kind>            e.g. service:postgres
type          = NodeType.SERVICE,  tier = 5
name          = <kind>                    "postgres" | "redis" | "mongo" | "rabbitmq"
state         = UNKNOWN                    (discovered; not certified in this slice)
check_command = "pg_isready -h <host> -p <port>"   (recorded for the future action layer; not run here)
fix_candidates= ("service:postgres:16",)  # image+ports, OR a compose service ref
evidence      = ".github/workflows/ci.yml: services.postgres"
discovered_by = STATIC_SCAN | RESOLVER (package-induced) | RUNTIME (failure-driven)
data          = {service_confidence: "confirmed"|"inferred", bound_config: "DATABASE_URL",
                 image: "postgres:16", host: "postgres", port: 5432,
                 inducing_package: "psycopg2"?, compose_service: <ref?>}
```

---

## 4. Edge model

Incoming `requires` only (reuse `requires`, `origin="service"`), and **structural edges require evidence** (review **M4**):

- **`Package → Service` is emitted ONLY for a *confirmed* service** (the package's matching service also appears in CI/compose). A `package→service` table hit with no corroborating evidence is **inferred** and gets **no structural `requires` edge** — encoding the guess as `requires` would plant a false necessary condition (`psycopg2` imports fine with no Postgres; the suite may mock it). The inferred node still exists, carries `data["inducing_package"]`, and is surfaced in the advisory (§6) as a candidate — but it is not a hard dependency in the DAG.
- **`Test/Project → Service`** transitively for confirmed services.
- **Config↔Service** is metadata (§3.4), not an edge.

**Run model / downward edges:**
- **Sidecar / compose (default, for the future action layer):** no downward edges; fix `service:<image>`; pure sink.
- **In-image mode: cut** (review **F2**) — `apt install postgresql; service start` doesn't work in a slim init-less scratch container. Non-goal (§12).

`Service → Service` edges (kafka→zookeeper, flower→broker) are **permitted by the schema** (Service as both `requires` source and destination) though multi-service ordering is deferred (review **M1**) — the model must not forbid a topology the action layer will need.

---

## 5. Discovery — all sources, ranked, confidence-labelled

| Rank | Source | Confidence | Yields |
|---|---|---|---|
| 1 | `.github/workflows/*.yml` **`services:`** | **confirmed** | what maintainers run *for CI tests* (image, ports, env) |
| 2 | `docker-compose.yml` / `*.test.yml` `services:` | **confirmed** | declared topology, images, ports, bound env vars |
| 3 | **failure-driven** (connection errors) | **confirmed** (post-hoc) | `OperationalError: could not connect`, `redis…ConnectionError`, `ServerSelectionTimeoutError`, `ConnectionRefused` on a known port |
| 4 | `DATABASE_URL`/`*_URL` Config scheme | **inferred** | scheme → service kind + host/port |
| 5 | curated **`package → service`** table | **inferred** | `psycopg2`/`django`→postgres, `redis`/`celery`→broker, `pymongo`→mongo |

**Ranking + override (review F3):** higher rank wins. If CI `services:` exists and lists no Postgres, an inferred `psycopg2→postgres` candidate is **suppressed** (the maintainers' CI is authoritative). Parsing is **best-effort**: matrix jobs, compose `profiles`/`depends_on`, and `${VAR}` interpolation are parsed where possible, else the node is emitted with `evidence="<source>: partially parsed"` + defaults — never silent over-claiming.

**`celery`/`kombu` caveat:** these map to *a broker*, not specifically `rabbitmq` (Redis is the plurality broker). So `celery` alone is **inferred**; the broker kind is **confirmed** only by a compose/CI service or a `CELERY_BROKER_URL` scheme.

---

## 6. Advisory render — how Services appear

Because service nodes are `UNKNOWN` (uncertified), they do **not** belong in the certified-MISSING frontier. Render them in a **dedicated advisory block** instead — in `render_dep_graph_advisory` (the path the Config tier uses), mirroring how Config nodes were wired:

```
SERVICES (declared — reachability NOT certified here):
  postgres   [confirmed: .github/workflows/ci.yml services.postgres]   fix: service:postgres:16   addresses: DATABASE_URL
  redis      [inferred:  package celery]                               fix: service:redis:7       (may be mocked — agent's call)
```

The block always carries the **confidence label** (`confirmed`/`inferred`) and, for inferred nodes, an explicit "may be mocked" honesty marker so the planner does not treat a guess as a hard need. For confirmed services, the `Package→Service` requires edge also lets the existing `needed by` / chain rendering show the cross-tier link.

---

## 7. Necessary-vs-sufficient (recorded for the action layer)

Even though we don't certify here, the model records the right semantics for later: a service's `check_command` (`pg_isready`) would certify **reachability** — *necessary*, not *sufficient* (a reachable-but-unmigrated DB still fails tests; the suite is the sufficiency oracle). This matches the Config tier's presence-vs-value discipline and is why §8's certification, when built, only flips `SATISFIED` on a real probe.

---

## 8. Deferred — the action layer (separate, runner-level spec)

Out of scope for this build; recorded so the representation is forward-compatible:

- **Emit** a "run-the-service-before-tests" sidecar for *confirmed* services (compose-up / `docker run` + network-attach + teardown).
- **Escalate** *inferred* services to the planner (it rules on mocked/sqlite cases).
- **Live reachability certification** (`pg_isready`, condition-based) flipping `UNKNOWN → SATISFIED/MISSING`; adds `Layer.SERVICES` + removes the §3.3 skip-guard for the live path.

All three live in the **agent runner** (DockerAgent / RATBench), above `build_dep_graph` — the one place that owns both the service and the test-run container and their shared network (review F1). This slice's node shape (image/ports/host/bound_config/check_command in `data`) is designed to feed that layer directly.

---

## 9. Phasing

| Phase | Scope |
|---|---|
| **This slice — discovery + advisory (in `python_deps/depgraph/`)** | `service_id`; `package→service` table; ranked discovery (CI/compose/failure/URL/package); `scan_services(repo, graph)` building confidence-annotated `SERVICE` nodes (+ `Package→Service` edges only on evidence); `certify` skip-guard; advisory render block. **Builds the graph; ships in-module like Config.** |
| **Action layer (later, runner-level spec)** | emit / escalate / live certification (§8). |

---

## 10. Novelty — scoped honestly (review N1)

Services is the **most-solved** tier: standing up + health-probing a service ≈ testcontainers, the `package→service` table ≈ DockerizeMe, the service/config/app triangle is what a `docker-compose.yml` already encodes. What **survives** as the defensible contribution — and what to foreground: **agent-side inference of a service requirement from an unknown repo with *no* compose/CI/fixture** (`psycopg2` in the closure + `postgres://` scheme ⇒ Postgres needed), represented as a certified cross-tier node the agent can reason over. Testcontainers/compose need a human-written fixture/file; this *derives* the need. Narrow but real. (Acting on it — emit — is where it becomes valuable; that's the deferred layer, and the reviewers were right that representation alone doesn't move the agent.)

---

## 11. Testing strategy (TDD)

- **Unit (pure, no Docker):** the `package→service` table; CI-`services:` / compose `services:` parsers (incl. best-effort/partial paths); URL-scheme→service mapper; source ranking + suppression; `scan_services(repo, graph)` (confirmed vs inferred nodes, structural-edge-only-on-evidence, dedup, `bound_config`/`inducing_package` metadata, `service_confidence` label).
- **Advisory render:** the SERVICES block renders confirmed + inferred with correct labels and the "may be mocked" marker; an empty service set renders nothing.
- **Certify skip:** `certify_all` leaves SERVICE nodes `UNKNOWN` (never runs `nc -z`/`pg_isready`).
- **Off-state invariant:** feature off ⇒ byte-identical; no service is ever started or probed.

---

## 12. Non-goals (YAGNI)

- **No emit / no runner orchestration / no live certification** in this slice — the action layer is §8 (a separate, later spec).
- **No in-image service mode** (init-less slim container; review F2).
- **No structural `requires` edge for an inferred service** — evidence-backed (confirmed) edges only.
- **No `Config → Service` `requires` edge** — binding is node metadata (a `binds_to` edge is §13 Q1).
- **No service *correctness*** (migrations, credentials, SSL) — the test suite's job.
- **No `Service → Service` ordering** in this slice (schema permits it; discovery deferred).
- **No runtime config-value switching** — §5 reads only the URL *scheme*, at discovery.
- **No new `State` value** (`DECLARED` rejected — annotation instead).

---

## 13. Open questions

1. **`binds_to` edge type for Config↔Service.** This slice uses node metadata (no edge). A first-class `binds_to` edge would let the `Package→Service←Config` chain render as graph structure, but adds an `EdgeType` the parent spec's "no new edge type" non-goal resisted. Adopt only if the render needs it.
2. **Where `scan_services` runs in `build.py`** — after `scan_config` (reads the URL scheme) and after the resolver (package candidates). Deterministic, same as `scan_config`.
3. **Confirmed-vs-inferred representation** — `data["service_confidence"]` flag vs a typed field. Lean on `data` until a consumer needs it typed.
4. **Inferred-node connectivity** — inferred services have no `requires` edge (kept honest). Are they rendered purely from the node list, or should they carry a soft, non-`requires` link to the inducing package for the chain render? Provisionally: node-list + `data["inducing_package"]`, no edge.

---

## 14. Adversarial review history (v1→v2→v3)

- **v1** (discovery + "declared" advisory, with an in-pipeline live-cert mode + a `DECLARED` state).
- **v2** (post 4-agent review): reframed to discover→emit/escalate at the runner level; fixed the edge model (`Package→Service` evidence-only; Config-binding metadata), dropped `DECLARED` for an annotation, cut in-image mode, moved orchestration above `build_dep_graph` (F1), narrowed the control-plane, reframed novelty.
- **v3** (this version): user scoped down to **build the depgraph now** — reverted the emit/escalate **action layer** to a deferred, separate runner-level spec (§8), and made this slice **discovery + advisory only**, entirely in-module like Config. **All v2 model-correctness fixes retained** (client/server split, evidence-only structural edges, metadata binding, annotation-not-state, in-image cut, honest confidence labels). The reviewers' "advisory alone won't move the agent" finding is acknowledged and addressed by §8 being the explicit next step — this slice's goal is the *representation*, not yet the action.
