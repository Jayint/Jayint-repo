# Handoff — Bring arm C to run_v3 parity (test gate + service/config)

**Date:** 2026-07-08 · **Branch:** john-v3-multi-lang (LOCAL, not pushed)

You are continuing work on **arm C** — the redesigned build-script repair loop for this v3 environment-builder. Arm C is **built, unit-tested, and its pipeline runs live end-to-end**, but it is **not yet at feature parity with the existing `run_v3` loop**, so it FAILs any repo that needs services/config/tests. Your job is **task A: close the two parity gaps** below. Read `docs/superpowers/specs/2026-07-08-build-script-repair-with-memory-design.md` (the design) and `docs/superpowers/plans/2026-07-08-arm-c-repair-loop-and-eval.md` (the build plan) first.

## Design philosophy (non-negotiable)
- **Graph-centric**: certified dependency graph = source of truth; only a **host check** flips a node to `SATISFIED` (revocable); the agent proposes typed `PatchProposal`s through `patch_gate`, never mutates the container, never declares success.
- **SIMPLE, robust, ONE clean path.** No fallback bloat, no flag-gated alternatives, no dead code. Prefer reusing existing helpers over new machinery.
- **New arm, NO cutover.** Do not touch `run_v1`/`run_v3`/`orchestrator.py`/`trace_verify.py`/construction/`patch_gate`/`certify`/`schema`/`sandbox.py`. Arm C is additive.

## What exists (arm C today)
- Core loop (`src/envstate/`): `repair_types.py` (`ReplayResult`), `repair_session.py` (notebook + `made_progress` + `persist_session_to_attempts`), `repair_fix.py` (`fix_one_error` — the sustained per-error session), `repair_arm.py` (`run_repair_arm` — error loop), `session_agent.py` (`SessionAgent` — LLM port), `repair_arm_entry.py` (`run_v3_session` + `docker_adapters`), `repair_log.py` (`DesignLog`).
- Written with **dependency injection**: `run_repair_arm(graph, *, replay, certify, agent, log, readonly, known_evidence_ids, ...)`. The SAME loop runs in prod (Docker adapters) and the offline eval (FakeWorld). Keep this — add gates as more injected callables, not hardcoded Docker.
- Offline eval: `src/eval/repair_arm_eval/` (mock_world, scenarios, run_eval). ~24 tests.
- Wired into `scripts/run_v3_e2e.py` via `--arm session` (reuses the SAME construction: base image, dep-graph, sandbox — only the repair loop differs).
- Commits: a7baaad, 4d8c649, f9dd732, 96d0fbd, c9e6930.

## Live run results (the evidence for the gaps)
`python3 scripts/run_v3_e2e.py <repo> --arm session --base-image python:3.11-slim --model openai/gpt-4o` (needs `OPENROUTER_API_KEY` + `OPENROUTER_API_BASE=https://openrouter.ai/api/v1`):
- **itsdangerous** → V3 E2E **PASS** (pure-Python, install-closure DONE, no repair needed).
- **psycopg2** → install-closure DONE but V3 E2E **FAIL** on 18 unresolved: `service:postgresql`, `config:PG*` (14 env vars), `import:eventlet`, **`test:repo_tests_pass`**.
- The SessionAgent's **LLM-repair path never fired** on either (both installed first-pass — construction front-loaded well). Closing gap 1 (test gate) is the most likely way to finally exercise it on a real repo (a missing test dep is exactly what it should repair).

---

## GAP 1 — no test gate (HIGH priority, do first)

**What's wrong:** `run_repair_arm` (`src/envstate/repair_arm.py`) declares DONE when install closure is green (`_first_unmet_required_node(graph) is None`, which only inspects *installable* node types via `emit.partition`). It **never runs pytest**, so the `test:repo_tests_pass` node (a `NodeType.TEST` node whose `check_command` is a `pytest --collect`-style command) is never certified. The design is a TWO-gate ladder (installability + testability) — arm C only does gate 1. Spec §4 explicitly had `if result.ok: test = run_test_gate(graph); if test.ok: return DONE; else: graph = ingest_test_failures(graph, test); continue`.

**What to build:**
1. Add an injected `run_tests` callable to `run_repair_arm` (and thread it, like `replay`/`certify`). Signature suggestion: `run_tests(graph) -> TestResult(ok: bool, failures: ...)`. Production adapter (in `repair_arm_entry.docker_adapters`) runs the repo's `pytest --collect-only` in the sandbox; the offline eval injects a fake.
2. In the loop, when install closure is green (the current DONE point): call `run_tests`. If it collects → certify `test:repo_tests_pass` (host check) and return real DONE. If it fails → **ingest the failure into new graph obligations** (reuse `src/python_deps/depgraph/runtime_ingest.py::ingest_runtime_failures` + `runtime_classify.py`, exactly as `run_v3`'s `_runtime_ingest_phase` does — see orchestrator.py) so the missing test deps become MISSING nodes the repair loop then fixes. Continue the loop.
3. Reuse, don't reinvent: `src/envstate/gates.py::evaluate_testability_gate` and orchestrator's `_run_tests_verified` / `_run_discover_gate` show how `run_v3` runs and verifies the pytest gate. Match that behavior.
4. Tests: extend the offline eval with a scenario where install is green but a test-collect fails on a missing dep → the loop ingests it → repairs → tests pass → DONE. Keep all existing arm-C tests green.

## GAP 2 — no service/config tier (do second)

**What's wrong:** psycopg2 needs a running Postgres daemon (`service:postgresql`) and env vars (`config:PG*`). `run_v3` provisions services via an env-gated phase (`V3_INCLUDE_SERVICES`, `provider.service_obligations`, ENTRYPOINT-started daemons — see `[[service-provisioning-unlocks-tests]]` memory + `service_recipes.py`/`service_tables.py`) and handles config via the env classifier. Arm C's loop ignores both, so those nodes stay MISSING and the e2e counts them as unresolved → FAIL.

**What to build (decide the approach first):**
- **Reuse, gated:** wire arm C's `certify` adapter to certify services the same way `run_v3` does on the live path — `certify_refresh(graph, exec_readonly, allow_service_certify=True, layer_order=_SERVICE_LAYER_ORDER)` — and provision via the existing `V3_INCLUDE_SERVICES` mechanism. When that flag is OFF (default), service/config nodes must NOT count as arm-C failures (mirror run_v3's off-arm behavior), so a library repo passes and a service repo passes only with the flag on.
- Keep it SIMPLE: reuse `run_v3`'s service phase wholesale rather than reimplementing; the only arm-C-specific work is calling it from `run_repair_arm`/`docker_adapters` and adjusting the DONE/unresolved criterion.

---

## How to verify
```bash
# offline (no Docker/LLM):
python3 -m pytest tests/envstate/test_repair_*.py tests/envstate/test_session_agent.py \
        tests/envstate/test_repair_arm_entry.py tests/eval/test_*.py -q
python3 -m src.eval.repair_arm_eval.run_eval            # must exit 0, full design coverage
# live (needs key):
OPENROUTER_API_KEY=<key> OPENROUTER_API_BASE=https://openrouter.ai/api/v1 \
  python3 scripts/run_v3_e2e.py outputs/graph_fidelity/_smoke_large/psycopg2 \
  --arm session --base-image python:3.11-slim --out /tmp/arm_c.sh --model openai/gpt-4o
# success target: V3 E2E PASS (install + tests collect; services only with V3_INCLUDE_SERVICES=1)
```

## Other known follow-ups (lower priority)
- **Synthetic evidence id**: `run_v3_session` cites a constant `EVIDENCE` ("ev.1") so the gate accepts patches; should derive from the real failure so the node's stored evidence ties to the actual diagnostic (`repair_arm_entry.py`).
- **`manual_blocks` not threaded**: an accepted `script_patch` fix is verified inside a session then dropped from the top-level replay (`fix_one_error`/`run_repair_arm` return only the graph). Latent (no agent emits script_patches yet); needs widening the return signatures.

## First move
Implement GAP 1 (test gate) end-to-end with an offline eval scenario, keep all tests green, then re-run the psycopg2 live command and confirm it gets past install into the test gate. THEN tackle GAP 2. Do not `git commit` until asked; leave changes staged for review.
