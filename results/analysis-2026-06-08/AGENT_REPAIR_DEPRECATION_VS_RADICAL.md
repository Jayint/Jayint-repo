# Agent Repair vs Radical Baseline: Deprecation and OFF-Mode Verification Report

**Date:** 2026-06-08
**Branch:** rat-bench-integration HEAD vs radical (184a9e3)
**Scope:** Is the agent with repair=OFF byte-for-byte functionally identical to radical? Are repair additions fully gated and deprecation banners in place?

---

## Executive Summary

The repair additions are correctly gated for the benchmark A/B path. When `--repair-mode off` or `--repair-mode runner` is passed through `run_rat_benchmark.py`, `enable_post_synthesis_repair=False` is reliably delivered to `DockerAgent` and the guard at `agent.py:1180` makes `_self_verify_and_repair` a no-op. All seven core agent dependency modules (`synthesizer.py`, `planner.py`, `sandbox.py`, `image_selector.py`, `language_handlers.py`, `memory_manager.py`, `observation_compressor.py`, `verification_bundle.py`) are byte-for-byte identical to radical.

**However, the agent with repair=OFF is NOT identical to radical** due to two confirmed non-repair deltas introduced in the same branch: the OpenRouter LLM migration and an additive `self_verify_result: null` field in `agent_run_summary.json`. The OpenRouter delta is the dominant concern: if `OPENROUTER_API_KEY` is set in the run environment (which the updated `.env.example` advertises as the primary key), every LLM call in the agent is routed through OpenRouter with Alibaba provider pinning — behavior that is entirely absent from radical.

**Verdict: `agent_equals_radical_when_off = false`** — the A/B is comparing against a different LLM routing stack, not a strict radical baseline. The repair logic itself is clean; the confound is the OpenRouter migration.

---

## Confirmed Leaks and Hazards

### HAZARD-1: Constructor default `enable_post_synthesis_repair=True` (low severity for current path, latent)

**Location:** `agent.py:131`

The constructor default is `True`, not `False`. The benchmark path is safe only because `run_rat_benchmark.py:774` sets `DOCKERAGENT_REPAIR_MODE` before the adapter reads it, and the adapter explicitly passes `enable_post_synthesis_repair=False` to the constructor. But any direct instantiation of `DockerAgent()` without the kwarg silently defaults repair ON. The adapter's own env fallback (`os.environ.get("DOCKERAGENT_REPAIR_MODE", "selfverify")` at `multi_docker_eval_adapter.py:767`) also defaults to `"selfverify"` (repair ON), not `"off"`.

**Fix:** Change `agent.py:131` to `enable_post_synthesis_repair=False`. Change the adapter fallback from `"selfverify"` to `"off"` or require the kwarg to always be passed explicitly.

### SCHEMA-DELTA-1: `self_verify_result: null` always emitted in `agent_run_summary.json` (low)

**Location:** `agent.py:138` (init), `agent.py:1949` (serialization)

The field `self.self_verify_result = None` is set unconditionally in `__init__`. The summary builder at line 1949 always serializes `"self_verify_result": null` even when repair is OFF. Radical never emitted this key. No downstream consumer reads or branches on it (confirmed by exhaustive grep), so this is an inert schema delta, not a behavioral leak. Strict key-set comparisons of JSON output to radical will flag it.

**Fix (low priority):** Gate the key: `**({"self_verify_result": self.self_verify_result} if self.enable_post_synthesis_repair else {})` at `agent.py:1949`.

---

## Non-Repair Deltas vs Radical (OFF mode still differs)

### DELTA-1: OpenRouter LLM client migration — HIGH impact on A/B validity

**Locations:** `agent.py:214-240`, `src/workplace_replay.py:71-79`

In radical, `api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY")`. In HEAD, `OPENROUTER_API_KEY` is checked first. Additionally, when `_is_openrouter` is true (base_url contains `"openrouter"` OR `LLM_API_PROVIDER == "openrouter"`), `self.client.chat.completions.create` is monkey-patched with `_routed_create`, which injects `extra_body.provider = {"order": ["Alibaba"], "allow_fallbacks": False}` into every LLM call — planner, synthesizer, image selector, reflection. This block runs unconditionally at `__init__` time, with no relation to `enable_post_synthesis_repair`.

Because the updated `.env.example` sets `OPENROUTER_API_KEY`, `OPENROUTER_API_BASE=https://openrouter.ai/api/v1`, and `LLM_API_PROVIDER=openrouter` as the primary config, any environment built from it will trigger provider pinning on every LLM call. This is not present in radical at all.

**Impact for A/B:** With `OPENROUTER_API_KEY` set, HEAD+off-mode routes all LLM traffic to OpenRouter/Alibaba; radical (run with the same env) would raise `ValueError: No LLM API key found` unless `MINIMAX_API_KEY` or `OPENAI_API_KEY` is also set. The two branches cannot be run head-to-head against the same `.env` and compared as identical-LLM-routing baselines. This is a migration confound, not a repair gate failure.

**Fix / Mitigation:** Document that the OFF baseline uses a different LLM provider stack than radical. For a strict radical-equivalent comparison, unset `OPENROUTER_API_KEY` / `OPENROUTER_API_BASE` / `LLM_API_PROVIDER=openrouter` and fall back to `MINIMAX_API_KEY`/`OPENAI_API_KEY`. Alternatively, cherry-pick the OpenRouter migration into radical as a separate pre-repair commit so both branches share the same LLM routing.

`src/workplace_replay.py:71-79` carries the same OPENROUTER key-precedence change. This function is not called by `DockerAgent` directly; it is used by `run_repo2run_benchmark.py` and resynthesis tooling. Zero behavioral impact on the RAT A/B run.

---

## Deprecation Completeness

### PASS: `src/artifact_verify.py`

Module docstring (`artifact_verify.py:1-2`) reads:
```
**DEPRECATED (2026-06-08): Superseded by repo2run_repair_port.py / run_rat_benchmark.py:_repair_and_rescore.
Retained and toggled via enable_post_synthesis_repair / --repair-mode. Do not extend.**
```
Correctly wired.

### PASS: `agent.py:_self_verify_and_repair`

Docstring at `agent.py:1168-1171` reads:
```
DEPRECATED (2026-06-08): superseded by the runner-side repair loop
(run_rat_benchmark.py:_repair_and_rescore via repo2run_repair_port.py).
Retained and toggleable via enable_post_synthesis_repair / --repair-mode.
Do not extend — port improvements to the runner loop instead.
```
Correctly wired.

### FAIL: `src/recipe_repair.py` — deprecation banner MISSING

`PORT_REPAIR_LOOP_PLAN.md:117` explicitly lists `src/recipe_repair.py:repair_recipe_with_llm` as deprecated-in-place. A `grep` for `deprecated`, `DEPRECATED`, or `superseded` in `src/recipe_repair.py` returns empty. The module docstring describes the two tiers and their purpose but contains zero deprecation language. A contributor reading only this file has no signal that it is deprecated and must not be extended.

**Fix:** Prepend to the `src/recipe_repair.py` module docstring:
```
**DEPRECATED (2026-06-08): repair_recipe_with_llm is superseded by the runner-side
trajectory-aware LLM repair (repo2run_repair_port.py). These primitives are retained
for the toggled-on legacy self-verify path only. Do not extend.**
```
Also add a one-line comment at `repair_recipe_with_llm`: `# DEPRECATED: use runner-side LLM repair instead; see run_rat_benchmark.py:_repair_and_rescore.`

### TOGGLE WIRING: Correctly off in benchmark path

`run_rat_benchmark.py:774` sets `os.environ["DOCKERAGENT_REPAIR_MODE"] = args.repair_mode` in `__main__`. `multi_docker_eval_adapter.py:767-768` reads it; for `"off"` or `"runner"`, `_enable_agent_repair = False`. Line 784 passes `enable_post_synthesis_repair=False` to `DockerAgent`. Guard at `agent.py:1180` fires immediately — no repair code runs.

The adapter's env-var default is `"selfverify"` (repair ON), which is a latent hazard for non-`__main__` callers, but does not affect the CLI A/B path.

---

## Repair Gate Verification

All repair-mutating code paths are unreachable when `enable_post_synthesis_repair=False`:

| Location | What it does | Gated? |
|---|---|---|
| `agent.py:1180` | Guard: early return in `_self_verify_and_repair` | YES — first statement |
| `agent.py:968` | Call site 1: post-Dockerfile synthesis | YES — calls gated method |
| `agent.py:992` | Call site 2: auto-finalization branch | YES — calls gated method |
| `agent.py:1186` | `verify_and_repair_recipe(...)` | YES — after guard |
| `agent.py:1208-1210` | `apply_build_recipe` + Dockerfile regen | YES — after guard |
| `agent.py:1203,1206` | `self.self_verify_result` mutation | YES — after guard |

Core dependencies unchanged vs radical (confirmed via `git diff radical...HEAD`):
`src/synthesizer.py`, `src/planner.py`, `src/sandbox.py`, `src/image_selector.py`, `src/language_handlers.py`, `src/memory_manager.py`, `src/observation_compressor.py`, `src/verification_bundle.py` — all empty diffs.

---

## Recommended Hardening (Smallest Changes to Guarantee OFF==Radical)

1. **Flip constructor default** (`agent.py:131`): `enable_post_synthesis_repair=False`. Makes the safe baseline the default; any caller that wants repair must opt in. Update `agent.py:2039` CLI flag accordingly (flip `--disable-` to `--enable-` or adjust the `not args.disable_...` logic).

2. **Flip adapter env fallback** (`multi_docker_eval_adapter.py:767`): change `os.environ.get("DOCKERAGENT_REPAIR_MODE", "selfverify")` to `os.environ.get("DOCKERAGENT_REPAIR_MODE", "off")`. Removes silent repair activation for any adapter call that doesn't come through the full runner stack.

3. **Add deprecation banner to `src/recipe_repair.py`** (see wording above). Completes the three-file deprecation sweep.

4. **Gate `self_verify_result` key** in `_build_run_summary` (`agent.py:1949`): emit only when `self.enable_post_synthesis_repair` is True, to achieve exact JSON parity with radical in off mode (cosmetic, low priority).

5. **Document the OpenRouter confound** in the A/B runbook: OFF mode is repair-clean but LLM-routing-different from radical when `OPENROUTER_API_KEY` is set. The comparison is valid for measuring repair delta only if both runs share the same LLM endpoint config.
