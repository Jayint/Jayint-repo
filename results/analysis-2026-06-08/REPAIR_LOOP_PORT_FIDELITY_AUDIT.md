# Port Fidelity Audit: Repo2Run Repair Loop → RATBench Runner

**Audit date:** 2026-06-08  
**Auditor:** Synthesis pass over six dimension-specific audit reports

---

## Verdict

**The port is NOT 100% faithful to the source or to the plan.**

The recipe-repair *primitives* (`src/recipe_repair.py`, `src/artifact_verify.py`) are a competent recipe-level reimplementation of the source's Dockerfile-text-level deterministic and LLM repair tiers. Core properties — identical missing-module regex, same three-tier requirement resolution, token-level deduplication, immutability, range(N+1) loop semantics, deterministic-before-LLM ordering, bounded/never-raises contract — are faithfully reproduced and in some cases improved.

However, the plan's three "winning properties" that were the stated reason for porting are all absent from the RATBench scoring path. The runner-side `_repair_and_rescore` loop was never built. The port instead left the agent's internal self-verify as the sole repair mechanism, with two structural defects the plan explicitly named as dealbreakers: adopt-only-on-resolved adoption gating, and a clean-room image that diverges from what the scorer builds.

---

## Winning Properties Table

| Property | Status | Evidence |
|---|---|---|
| (1) Score the LAST repaired artifact UNCONDITIONALLY | **LOST** | `agent.py:1202` gates on `status=='resolved' AND changed`. Unresolved repairs are discarded. `_repair_and_rescore` was never implemented in `run_rat_benchmark.py`. |
| (2) TRAJECTORY-aware LLM repair that restores dropped `pip install -e .` | **PARTIAL** | `excluded_commands` does reach the LLM via `current_recipe` at `recipe_repair.py:313`. But `successful_actions` is never forwarded — no equivalent of source Rule 5 ("restore omitted successful setup commands from trajectory"). `_self_verify_and_repair` at `agent.py:1181` does not pass `self.successful_actions` despite `_build_run_summary` at `agent.py:1934` having it. |
| (3) Validate the SHIPPED artifact | **LOST** | `render_verification_dockerfile` (`artifact_verify.py:234`) builds a clean-room image that differs from the adapter's scored image: WORKDIR (`synthesizer.workdir` vs `/testbed` at `multi_docker_eval_adapter.py:514`), missing `_build_eval_dependency_bootstrap_instructions` (`adapter:493-496`), missing `_build_eval_post_setup_instructions` (`adapter:502-506`). GAP-2 is not fixed. |

---

## Confirmed Gaps — Prioritized

### Critical / High

**H1 — Runner-side `_repair_and_rescore` loop absent from `run_rat_benchmark.py`**  
`run_rat_benchmark.py:146-225` (`_run_one`) calls `model.predict()` at line 190 and immediately feeds to scorers at lines 207-210 with no intervening repair. No `_repair_and_rescore` function exists anywhere in the file. This is the root cause of all three winning properties being absent from scoring.  
*Fix:* Implement per `PORT_REPAIR_LOOP_PLAN.md §4` pseudocode.

**H2 — Adopt-only-on-resolved adoption gate discards partially-repaired artifacts (`agent.py:1202`)**  
`if result.get('status') == 'resolved' and result.get('changed'):` — a recipe that repairs from hollow to partially-working is thrown away.  
*Fix:* Change to `if result.get('changed'):`. Record resolved/unresolved in metadata, not as an adoption gate.

**H3 — `successful_actions` never passed to LLM repair**  
`build_recipe_repair_input` at `recipe_repair.py:303-324` receives `{repo_url, current_recipe, verified_test_commands, project_config_context, failure}`. No `successful_actions`, no trajectory field. `_self_verify_and_repair` at `agent.py:1181` does not forward `self.successful_actions`.  
*Fix:* Thread `successful_actions` through `_self_verify_and_repair` → `verify_and_repair_recipe` → `_apply_repair` → `repair_recipe_with_llm` → `build_recipe_repair_input`. Add source Rule 5 equivalent to `RECIPE_REPAIR_SYSTEM_PROMPT`.

**H4 — GAP-2: clean-room verify image differs from scored eval image**  
`render_verification_dockerfile` (`artifact_verify.py:234`) uses `synthesizer.workdir` (may not be `/testbed`), omits the adapter's conditional bootstrap and plugin-cleanup blocks. A clean-room pass does not guarantee a scored-image pass.  
*Fix:* Implement runner-side loop that operates directly on `eval_build/Dockerfile`. No other fix completely closes this gap.

**H5 — System prompt lacks Rule 5 equivalent (restore omitted trajectory commands)**  
`RECIPE_REPAIR_SYSTEM_PROMPT` (`recipe_repair.py:41-68`) has no rule instructing the LLM to inspect `successful_actions` for dropped commands. Rule 10 only says "preserve what is already there"; Rule 14 is a pattern-match heuristic.  
*Fix:* Add: "If successful_actions lists a command not in current_recipe.build_commands, restore it in trajectory order."

### Medium

**M1 — System prompt lacks Rule 11 equivalent (authoritative replay order, exact command text)**  
No rule prohibiting the LLM from reordering, merging, or aesthetically rewriting existing commands. Rule 10's "necessary" qualifier gives the LLM latitude.  
*Fix:* Add: "Treat current_recipe.build_commands as the authoritative replay order. Preserve exact command text unless logs prove a specific command is wrong."

**M2 — JSON extraction: no in-string/escape tracking — shell commands with `{}` corrupt brace-balance counter**  
`_extract_json_object` at `recipe_repair.py:335-346` has no `in_string`/`escape` state. A LLM response with a shell command like `if [ ]; then { }; fi` in a JSON string will produce a false `None` return.  
*Fix:* Add string/escape state tracking matching `run_repo2run_benchmark.py:2922-2935`.

**M3 — JSON extraction: single-candidate only, no multi-candidate fallback**  
If LLM prefixes the JSON with a small reasoning object, the extraction fails and the entire repair round is wasted.  
*Fix:* Iterate over all `{...}` regions, return first valid recipe, matching source `extract_dockerfile_repair_json` at `run_repo2run_benchmark.py:2952-2969`.

**M4 — Infra short-circuit absent — dead Docker daemon burns all LLM rounds**  
No equivalent of `docker_build_failed_due_to_unavailable_daemon` (`run_repo2run_benchmark.py:245-257`) in `artifact_verify.py`.  
*Fix:* Check for 'cannot connect to the docker daemon' in build stderr; break loop before `_apply_repair`.

**M5 — Log truncation is head-only at 6000 chars — discards the tail where errors appear**  
`_truncate` at `recipe_repair.py:296-300` keeps only the head. Build/test failures typically appear at the end of logs.  
*Fix:* Head+tail strategy: `text[:limit//2] + '\n...truncated...\n' + text[-(limit//2):]`. Raise limit to 12000 to match source.

**M6 — `--repair-mode` and `--repair-rounds` CLI flags absent from `run_rat_benchmark.py`**  
The argparse block at lines 668-712 has no repair-mode or repair-rounds arguments. No A/B comparison is possible without them.  
*Fix:* Add both flags; wire to `DOCKERAGENT_REPAIR_MODE` env var and `_repair_and_rescore` gate.

**M7 — `ImportError: cannot import name` pattern is unanchored — may misclassify third-party errors**  
`artifact_verify.py:115-117` pattern has no path requirement; source requires '/app/' path group.  
*Fix:* Tighten or document.

**M8 — `compute_essr.py` missing repaired-vs-raw column**  
No sidecar or `repair_rounds` field detection in `scripts/compute_essr.py`.  
*Fix:* Implement per `PORT_REPAIR_LOOP_PLAN.md §6 step 7`.

### Low

**L1 — `--collect-only` stripping not implemented in `_self_verify_and_repair`**  
Specified in `PORT_REPAIR_LOOP_PLAN.md §3 item 2`. If a `--collect-only` command is ever recorded as `verified_test_command`, all repair rounds fire needlessly before 'unresolved'.

**L2 — `DOCKERAGENT_REPAIR_MODE` not threaded to `multi_docker_eval_adapter.py:764`**  
`DockerAgent` always constructed without `enable_post_synthesis_repair`, so self-verify is permanently ON with no toggle from the runner CLI. Severity is low because the plan's default was `selfverify` mode anyway.

**L3 — Deprecation banners absent from `artifact_verify.py` module docstring and `agent.py:_self_verify_and_repair`**  
Hygiene gap; no functional impact.

**L4 — Deterministic repair emits one `pip install` per requirement; source batches into one command**  
Minor: separate lines produce more Docker layers and miss joint dependency resolution.

**L5 — `_recipe_already_installs` does not check version specifiers**  
`django==4.0` would be wrongly considered satisfied by an existing `django==3.2`. Low risk in practice.

**L6 — Test coverage gaps** (all test gaps; no behavioral bugs)  
- LLM repair path never exercised at orchestrator level (all `TestOrchestrator` tests use `client=None`)
- `timed_out` classify path has no test  
- Multi-round loop (max_rounds > 1, multiple consecutive repairs) has no test  
- Build-failure-then-repair continuation path unreachable (existing test uses `max_rounds=0`)  
- `poetry.lock` version resolution regex (`recipe_repair.py:146-151`) has no test  
- `collection_or_env_error` and `invocation_error` classify branches have no tests  
- LLM payload shape test (`test_build_repair_input_shape`) only asserts two scalar fields  

---

## What Was Faithfully Ported

- Missing-module regex: identical to source (`recipe_repair.py:74-76` vs `run_repo2run_benchmark.py:2584-2611`)
- Three-tier requirement resolution: workspace lookup → known-fallback table → bare-name (bit-for-bit identical fallback table)
- `_find_declared_requirement_in_workspace`: same file types, same budget limits; port adds a dot-normalization fix the source is missing
- Token-level `_recipe_already_installs`: functionally equivalent to source's shlex-based check
- Immutability: `repair_recipe_for_missing_modules` opens with `copy.deepcopy`; never mutates input
- Loop semantics: `range(max_rounds + 1)` — N+1 build/test cycles, exact match
- Deterministic-before-LLM ordering within each round
- Build-failure handling: test skipped, build error fed to repair, `missing_modules=[]` so deterministic repair skips
- Bounded/never-raises contract: stricter than source (multiple layers of try/except)
- Collect-only tightening: rc=0 without real test summary is now non-effective (correct improvement)
- `tests_ran_with_failures` recognized as effective (correct improvement)
- Rule 14 explicit editable-install protection (improvement over source's implicit Rule 11 coverage)
- Error capture: exceptions caught and surfaced in record, never raised — equivalent to source
- `normalize_repaired_recipe` guard: never lets LLM drop the test command

---

## Prioritized TODO

1. Implement `_repair_and_rescore` in `run_rat_benchmark.py` (H1, H3, H4 — closes all three winning properties at once if trajectory-aware LLM is included)
2. Change `agent.py:1202` adoption gate from `status=='resolved' AND changed` to `changed` alone (H2 — one-line fix, immediate improvement to existing self-verify)
3. Thread `successful_actions` into `build_recipe_repair_input` and add Rule 5 equivalent to system prompt (H3, H5)
4. Add in-string/escape tracking and multi-candidate fallback to `_extract_json_object` (M2, M3)
5. Add infra short-circuit (M4) and head+tail log truncation (M5)
6. Add `--repair-mode` / `--repair-rounds` CLI flags (M6)
7. Fix test coverage gaps, especially: orchestrator LLM path, multi-round scenario, poetry.lock resolution (L6)
