# Scoping: `radical-synthesis-new` — strong-baseline branch for a fair contract-graph A/B

**Date:** 2026-06-19 · **Status:** SCOPING ONLY (no branch created; no code changed). Exploration requested while the
pin-vs-no-pin ablation runs.

## Goal
Equip the `radical` baseline agent with the modern agent-side Dockerfile synthesis (deterministic ledger-replay +
pip-freeze pin, changes A–F) so cross-variety comparisons isolate one variable at a time instead of conflating
architecture, the contract graph, and old-vs-new synthesis.

## Key reframing
The contract graph is a **v1-only overlay** (it attaches to the three-role loop). So:
- **"Does the contract graph help?" is cleanly isolated by `--arm v1` vs `--arm v1g`** on the existing
  `john-planner-v1` branch — identical three-role loop + identical synthesis (A–F); the ONLY difference is
  `DOCKERAGENT_ENABLE_CONTRACT_GRAPH=1` (run_rat_benchmark.py:827–828). Runnable today, zero new code.
- `radical-synthesis-new` does NOT isolate the graph better. Its value is a **strong baseline**: it removes the
  "radical uses old lossy synthesis" confound (the documented `v1-regresses-vs-arm0` effect), enabling clean
  decomposition of the *architecture* and *synthesis* contributions.

## Variant matrix
| Variant | Architecture | Graph | Synthesis |
|---|---|---|---|
| `radical` (arm0) | monolithic ReAct, no envstate | — | old LLM trajectory-replay (`synthesize_build_recipe`) |
| **`radical-synthesis-new`** | monolithic ReAct | — | modern ledger-replay + pip-freeze pin (A–F) |
| `v1` (`--arm v1`) | three-role | — | modern |
| `v1g` (`--arm v1g`) | three-role | ✓ | modern |

Comparisons: `v1` vs `v1g` → **contract graph**; `radical-synthesis-new` vs `v1` → **three-role architecture**;
`radical` vs `radical-synthesis-new` → **synthesis quality** (cleanest single-variable).

## Feasibility: moderate glue (~1 day), not a re-engineering
Most modern-synthesis pieces are path-agnostic pure functions (copy as-is): `build_commands_from_ledger`,
`_is_source_file_edit`, `build_pin_instructions`, `add_build_instruction`, `_v1_finalize_and_keep_success`,
`_resolve_project_name`, `ActionLedger`, `probe_env`.

**Biggest blocker:** the `src/envstate/` subsystem (~6,273 lines) does not exist on `origin/radical`; cherry-pick the
path-agnostic modules only. `enable_envstate` is separable from `enable_v1` — flipping it on for the monolithic loop
activates the ledger and the deterministic synthesis branch (`_synthesize_final_build_recipe`, HEAD agent.py:1782).

**Only net-new code (~3–5 lines):** radical has no Maintainer world-model, so source the closure from a one-shot
`probe_env(self.sandbox.exec_readonly)` after `configuration_success=True` instead of `final_map.installed`.

## Minimal port plan
1. `git checkout -b radical-synthesis-new origin/radical` (base on radical, NOT john-planner-v1).
2. `git checkout john-planner-v1 -- src/envstate/{__init__,ledger,synthesis,snapshot,jsonutil}.py` (skip
   contracts/, build_agent/maintainer/planner/orchestrator — three-role only).
3. agent.py `__init__`: add `enable_envstate=False`; `self.action_ledger = ActionLedger() if enable_envstate else None`.
4. Port `_append_action_event` (+ no-op guard) and its call from `_record_successful_action`.
5. Prepend the `enable_envstate` branch in `_synthesize_final_build_recipe` (routes to `build_commands_from_ledger`).
6. **NET-NEW:** after `configuration_success=True`, `snap = probe_env(self.sandbox.exec_readonly); self._final_installed
   = tuple(snap.installed)`.
7. Port the pin-layer injection in `_finalize_supervisor_artifacts` + `_v1_finalize_and_keep_success`; route the
   finalize call through it.
8. Copy `_is_source_file_edit` + `build_pin_instructions` (pure functions).
9. `MIN_PASS_RATIO 0.5→0.8` for parity (see risk 1 — no-op on arm0).
10. Adapter: read `DOCKERAGENT_ENABLE_ENVSTATE`, pass `enable_envstate=True` (parallel to the v1 plumbing).
11. (Optional) run_rat_benchmark.py `--arm radical-synth` setting the env var.

## Risks
1. **F (`MIN_PASS_RATIO=0.8`) is a no-op on radical** — only read in the v1 path. Copy for parity, don't misread as a control.
2. **Ledger-replay could REGRESS radical** on complex native builds if the monolithic ledger omits steps the LLM
   synthesis captured — a real confound; smoke-test Dockerfile completeness first.
3. **`probe_env` timing** — needs the sandbox alive + `exec_readonly` present on radical's sandbox; verify.
4. Sever any cross-imports when cherry-picking `src/envstate/` (e.g. synthesis.py importing world_model).
5. Full 50-repo runs cost OpenRouter credits — 5-repo smoke first (confirm ledger populated + non-empty Dockerfiles).

## Control-plane (rat-bench-integration) needs
Add `[variety.radical-synthesis-new] branch='radical-synthesis-new' …` to `varieties.toml`. Cleanest activation: bake
`enable_envstate=True` into the branch's adapter so no harness `--arm` plumbing is required.

## Recommendation
**Run `v1` vs `v1g` first** — cheap, today, directly answers the graph question. Build `radical-synthesis-new` only
if that's inconclusive or if review shows the three-role machinery confounds the graph signal; the port is then a
~1-day glue job, not a rewrite. Gated on: the running ablation finishing (no git mutations mid-run) + commit/push
approval (changes currently uncommitted by user policy).
