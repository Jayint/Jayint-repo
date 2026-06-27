# Findings: making Config / Service edges dynamic (vs. curated)

**Date:** 2026-06-26
**Status:** Findings + recommendation (pre-brainstorm). UNCOMMITTED working notes.
**Trigger:** a real-repo end-to-end run (`build_dep_graph` on `testdrivenio/fastapi-celery-project`, Docker `python:3.11-slim`) raised the question: are the cross-tier edges (Package→Service, Package→Config) **autonomously/dynamically driven** or **hardcoded**?

This note records (1) what is hardcoded vs. dynamic today, (2) concrete levers to make the config/service edges dynamic, (3) the governing pattern, and (4) a prioritization recommendation.

---

## 1. Current state — hardcoded vs. dynamic, per edge

One graph, one `requires` edge type (needer → needed); demand nodes (Test/Project/Import) vs. provider tiers (Packages/Services/Config/System). For each cross-tier connection, three things can be hardcoded or dynamic independently: the **mapping** (which→which), the **edge existence/details**, and the **state** (truth).

| Connection | Mapping (which→which) | Edge existence & details | State (truth) |
|---|---|---|---|
| **Package→Service** (`scan_services`) | **Hardcoded** — `PACKAGE_TO_SERVICE` (`service_tables.py`, ~13 rows: psycopg2→postgres, redis→redis, celery→broker) | **Dynamic** — edge emitted only if the repo's compose/CI declares the service; image/port/host parsed from the YAML | not certified (services tier is discovery/advisory-only) |
| **Package→Package** (resolver, ~80 edges) | **Fully dynamic** — `uv` resolves the real closure | dynamic | **Dynamic** — `pip show` in container |
| **Project→Config** | **Fully dynamic** — AST scan of repo for `os.environ.get(...)` (`config_scan.scan_env_reads`); value from the code default (`scan_env_defaults`) | dynamic | **Dynamic** — `printenv` |
| **Package→Config** | **Hardcoded** — `config_obligations_for_package` (`config_tables.py`: django→DJANGO_SETTINGS_MODULE, celery→CELERY_BROKER_URL, boto3→AWS_*) | dynamic gating | **Dynamic** — `printenv` |
| **Import→Package** | **Mostly dynamic** — manifest match + post-install `packages_distributions()` (`relink.certified_import_links`); curated collision table (`import_mapping.CURATED_IMPORT_TO_PACKAGE`) is a pre-install fallback for ~12 known-ambiguous names | dynamic | **Dynamic** |
| **Package→Tool/SystemLib** | curated seed = **proactive hint** (`seed_predicted_native`); authoritative source is **dynamic** — parse real `gcc` build error + `ldd` on actual `.so` (`probe.ldd_probe`) | dynamic | **Dynamic** |

**Summary:** structure existence and *all* certification are dynamic. Hardcoding is confined to a few **implication hypotheses not discoverable from the repo pre-execution** ("a postgres *driver* implies a postgres *server*"; "celery implies an env var"). Package→Service is the most table-driven edge; the services tier is the weakest-novelty tier.

---

## 2. Three levers to make Config/Service edges dynamic

Principle: **move curation off the volatile "package-name" layer — either down to a fundamental layer already observed dynamically, or forward to execution evidence.**

### Lever 1 — Introspect the *installed dependencies* (post-install AST) → Package→Config dynamic
Replace `config_obligations_for_package` with discovery: after the closure installs, run the **existing** env-var scanner (`config_scan.scan_env_reads` / `_settings_fields`) over each installed package's source in site-packages, attributing each `os.environ.get(...)` / pydantic `BaseSettings` field to its owning distribution via `packages_distributions()`.
- Reuses: AST scanner + `packages_distributions` (already used for relink).
- Generalizes to *every* package, not the ~dozen curated.
- Trade-off: noisier (optional vs. required reads); rank/label by confidence rather than treat as hard.

### Lever 2 — Derive the service from the *native client library* (ldd) → Package→Service dynamic
`psycopg2` is a postgres client because its `.so` links **`libpq.so`** — which `ldd_probe` already discovers (DT_NEEDED, execution-certified). Chain:
```
pkg:psycopg2  --(ldd, dynamic)-->  SystemLib:libpq.so  --(tiny stable map)-->  service:postgres
```
Moves curation from the *volatile* package→kind table to the *fundamental* C-client-lib→server map (libpq→postgres, libmysqlclient→mysql, libmongoc→mongo — small, ABI-stable). The `pkg→lib` half is fully dynamic.
- Reuses: `ldd_probe`.
- **Coverage gap (honest):** fires only for drivers with a *native* client lib. Pure-Python clients (`redis-py`, `pymongo`, `asyncpg`) link nothing → not covered; fall back to Lever 1/3 or the URL signal.
- This is the cheapest, lowest-risk lever and the clearest "this is dynamic now" demonstration.

### Lever 3 — Failure-driven certification → BOTH dynamic (the autonomous endgame)
Primitive already exists: `service_scan.classify_service_error` maps a real failure (`psycopg2.OperationalError: could not connect`, `redis ConnectionError: Error 111`) to a kind from runtime evidence — no package table; the only map is error-signature→kind, grounded in observed behavior. Then parse host:port from the config URL, spin the service up, re-run; if tests pass, the need is **certified sufficient** (not just hypothesized). Same for config: a runtime `KeyError: 'DATABASE_URL'` certifies the var is *actually* required vs. statically-read-but-optional.
- Deferred to the runner-level action layer (no failure text at static build time).
- Only lever that certifies **sufficiency**; the others certify presence/necessity.

### Config→Service is already the most dynamic
The Config→Service binding already reads the **actual connection URL** the repo uses (`service_from_url` on `DATABASE_URL=postgres://…`). Residue: `_SCHEME_TO_KIND` — an IANA-style fixed scheme set, about as fundamental/stable as a map gets.

---

## 3. Governing pattern & irreducible curation

The architecture already runs this elsewhere: **curated = proactive hypothesis (fallback); dynamic = authoritative (supersedes); execution = certification.** ldd supersedes the predicted-native seed; `packages_distributions()` supersedes the import collision table. Making config/service edges dynamic = applying the same pattern.

Not all curation should die. `postgres://→postgres` (scheme→kind) and `libpq→postgres` (lib→server) are **protocol/ABI facts** — tiny, stable, authoritative. The goal is not "zero tables"; it is **moving curation down to fundamental invariants and forward to execution evidence**, so the volatile package-name lists disappear.

---

## 4. Recommendation — solve the runtime-feedback loop first

Between "make the edges dynamic" (Levers 1–2) and "append live runtime feedback as requirements to the graph," **prioritize the runtime feedback loop.**

1. **It subsumes most of Levers 1–2.** The most reliable signal that psycopg2 *actually* needs a running postgres (not a mock) is that the test failed connecting to it; that `DATABASE_URL` is *required* is that the code raised `KeyError`. Failure feedback IS the dynamic discovery for services/config, and it observes *real need*, not declared need.
2. **It delivers the missing half of the spine.** The thesis is "graph certifies NECESSARY, tests certify SUFFICIENT." Today only the necessary half exists (`repo_tests_pass` is always `missing` — tests never run). Static discovery = what the repo *declares*; runtime = what it *needs to pass*. The declared→actual gap is where env-construction agents fail (mocked-vs-real services, collect-only false passes, dynamic imports, dlopen libs).
3. **It answers the prior "inert advisory" result.** That result: information without action/authority is inert — "an authority problem, not an information problem." Levers 1–2 produce *more information*; the runtime loop produces *action + verification* (observe failure → add requirement → satisfy → re-run → certify). That is the structural fix.

**Honest dependency:** runtime feedback is chicken-and-egg (need *some* env to run tests). Shape: **static discovery bootstraps → run tests → feed failures back → repair → re-run.** Static discovery (and Lever accuracy) remains the bootstrap; the *bottleneck* is the open arc (runtime → graph), not the curated tables.

### Minimal first slice (close the arc; skip the full repair loop)
1. After static build + install-certify, **run the test suite once** in the container.
2. Parse output into requirement classes (reuse `failure_classifier`, `classify_service_error`):
   - `ModuleNotFoundError: X` → package/import requirement (catches dynamic imports static scan missed)
   - `KeyError`/pydantic `ValidationError` on an env var → that Config node becomes **certified-required**
   - `could not connect` / `Connection refused :PORT` → Service requirement with the **real host:port** from the failure
3. For each, append a node/edge with `provenance="runtime"` + the failure as evidence — an execution-certified requirement no static analysis could produce.
4. Surface as the next frontier. (Full repair-and-re-certify loop is the next step.)

**On Lever 2:** do it only as a cheap parallel win for momentum (reuses `ldd_probe`, deterministic, low-risk). It should not lead — it polishes hypotheses nothing yet verifies; marginal value real but modest, the loop's value is structural.

---

## Code references
- `src/python_deps/depgraph/service_tables.py` — `PACKAGE_TO_SERVICE`, `SERVICE_DEFAULTS`, `BROKER_CAPABLE_KINDS`
- `src/python_deps/depgraph/service_scan.py` — `scan_services`, `service_from_url`, `_SCHEME_TO_KIND`, `classify_service_error`
- `src/python_deps/depgraph/config_scan.py` — `scan_env_reads`, `scan_env_defaults`, `_settings_fields`; `config_tables.config_obligations_for_package`
- `src/python_deps/depgraph/probe.py` — `install_closure`, `ldd_probe`, `import_probe`
- `src/python_deps/depgraph/relink.py` — `certified_import_links` (`packages_distributions`)
- `src/python_deps/import_mapping.py` — `CURATED_IMPORT_TO_PACKAGE`
- `src/python_deps/failure_classifier.py` — runtime failure signatures
