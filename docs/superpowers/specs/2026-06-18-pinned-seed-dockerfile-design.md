# Pinned Seed Dockerfile (v1 agent) — Design Spec

**Date:** 2026-06-18
**Branch:** john-planner-v1
**Status:** Design — awaiting user review before writing the implementation plan.
**Supersedes:** `docs/superpowers/plans/2026-06-18-move-synthesis-runner-side.md` (the "agent writes nothing, runner resynthesizes" direction — rejected; see Background).

---

## Summary

The v1 agent already generates a full `workplace/Dockerfile` that the benchmark runner consumes, normalizes, and runs through its build → test → repair loop. The runner side is good and stays untouched. The one thing the agent controls is the **quality of the seed Dockerfile** that loop starts from. Today that seed is **lossy trajectory replay**. This spec changes the seed's *content* (not the plumbing) to a **pinned seed**: the agent's recorded structural commands plus a dependency-closure pin sourced from a live `pip freeze` of the container it just built. The pin layer is **inline-materialized in a `RUN`** (not `COPY`) because the runner's eval build context is rebuilt with `git clean -fdx`, which deletes untracked files. Seed generation is also **decoupled from the run's success gate**: success means the host-certified test run passed, never "a Dockerfile was written."

---

## Background — why this, and why not the alternatives

The runner's `build → test → repair` loop (`run_repo2run_benchmark.py`, and the RATBench adapter) is bounded (default `--dockerfile-repair-rounds 2`). It is a **fixer, not a generator**: a seed that is one missing `pip install` from green converges; a hollow seed exhausts the budget and a genuinely-working environment is scored as a failure. So the seed's fidelity directly determines outcomes, and the seed is the only lever the agent owns.

Three ways the agent could produce the seed:

1. **In-agent lossy replay** (radical baseline + current v1): LLM reconstructs the Dockerfile from the recorded trajectory. Lossy (drops transitive/side-effect installs → "hollow" Dockerfiles). No upside over option 2.
2. **Runner-side lossy replay** ("Option 2 pure", rejected): agent writes nothing; the runner calls `resynthesize_dockerfile_from_existing_workplace`. Same lossy algorithm, just relocated. DRY-er, but produces an equivalent weak seed and **forecloses pinning forever** (the runner has no live container).
3. **In-agent pinned seed** (THIS SPEC): the agent, while the container is still alive, pins the *actual achieved dependency closure* via `pip freeze`. This is the only seed that is strictly better than what the runner could already make by itself, and it is the only place a pin can be taken (the runner is post-hoc, container gone).

The runner *does* already attempt a pin — `collect_observed_pip_install_constraints` (`run_repo2run_benchmark.py:1114`) scrapes `name==version` pairs from logged install output and injects them as a `-c` **constraints** file on bare pip installs. Two structural limits make it insufficient: (a) `-c` only pins *versions of packages that some command already installs* — it can never **add** a dropped package, so it does not fix hollow seeds; (b) it only sees what printed in the (truncated) logs. A `pip freeze` `-r` **install list** forces the entire closure present (transitive deps included) and therefore subsumes and strengthens the runner's `-c`. The runner's `-c` mechanism stays in place as harmless belt-and-suspenders.

Decision already taken with the user: the agent emits a **full seed Dockerfile** (it owns the seed's rendering), not loose pin artifacts for the runner to assemble.

---

## Goal

Replace the v1 agent's lossy-replay seed Dockerfile with a **pinned seed** = recorded structural commands + an inline-materialized `pip install -r <freeze>` closure pin, and decouple seed generation from the run's success gate.

## Non-Goals

- **The runner's build/test/repair loop is unchanged.** No edits to the repair rounds, `evaluate_built_image`, `render_eval_dockerfile`, or `normalize_eval_dockerfile_for_replay`.
- **Synthesis stays in the agent.** We are not moving it to the runner (that was the rejected Option 2).
- **Cleanroom is not part of this change.** `_verify_cleanroom_or_fail` stays off by default (`agent.py:1373`). Turning it on is separate, later work (the reliability ladder's "certify" rung).
- **System services (P3) and non-Python state** are out of scope; the structural commands + repair loop handle what they can.
- Arm0 / legacy `run()` behavior is not modified.

---

## Design

### The seed contract (what the agent writes to `workplace/Dockerfile`)

```dockerfile
FROM <base image>
# --- structural layer: recorded successful commands (the ledger) ---
RUN apt-get update && apt-get install -y <system deps>   # buildability: headers, toolchains
COPY . /app                                              # the repo IS the build context
WORKDIR /app
RUN pip install -e .                                     # the project + its declared deps
# --- PIN layer: the live pip-freeze closure, inline-materialized, ALWAYS LAST ---
RUN printf '%s\n' \
      'package-a==1.2.3' \
      'package-b==4.5.6' \
      ... \
    > /tmp/pinned-requirements.txt \
 && pip install -r /tmp/pinned-requirements.txt
```

This is **purely additive** to today's synthesizer. The structural layer is the *existing* trajectory-replay output, unchanged — the replay still sources the `apt-get`, project install, and build commands. The only new thing is the appended pin layer. The replay's known weakness (dropping transitive/side-effect installs) is exactly what the pin compensates for: replay SOURCES the structure, the freeze PINS the closure. We do not rewrite or remove replay.

Properties that make this the contract:

- **Structural layer first, pin layer last.** Structural commands establish buildability (system headers so wheels build) and install the project. The pin layer runs **last** so the exact frozen versions win over anything the structural installs resolved loosely.
- **Pin is inline-materialized, never `COPY`.** The eval build context is rebuilt by `prepare_eval_build_context` (`run_repo2run_benchmark.py:316`) via `git clone` + `git checkout --force <base_commit>` + **`git clean -fdx`**. `git clean -fdx` deletes all untracked files, so any pin file written into the workplace would be gone before `docker build`. Writing the freeze inside a `RUN` (the same `printf '%s\n' ... > path` pattern the runner already uses at `_render_observed_pip_constraints_instruction`) makes the pin independent of the build context.
- **The seed Dockerfile text survives** because the runner reads `workplace/Dockerfile` into memory (`raw_agent_dockerfile_text`) *before* preparing the eval context. Only files the Dockerfile tries to `COPY` from the context are at risk — and we `COPY` only `.` (the repo), which the context always contains.

### Where the pin comes from

`probe_env(exec_readonly)` (`src/envstate/snapshot.py:55`) already returns `EnvSnapshot(installed=…)`, where `installed` is the parsed output of `pip freeze` (`_parse_installed`, `snapshot.py:31,61`). `_run_v1` already probes during the loop (`agent.py:1065,1172`). The sandbox/container is still alive at finalize time — `sandbox.close()` runs at `agent.py:1225`, *after* `_finalize_supervisor_artifacts`. So at synthesis time the agent takes one **final** `probe_env(self.sandbox.exec_readonly)` to capture the closure of the final, verified-good state, and renders `installed` into the pin layer.

### Decouple the success gate

Today `_finalize_supervisor_artifacts` (`agent.py:1339`) returns `False` if synthesis fails, and the v1 finalize block lets that flip `configuration_success`. Change: **the run's success is the host-certified verified test run** (`_resolve_v1_verified_test_run` / `_auto_finalize_from_verified_tests`), full stop. Seed generation is a downstream artifact step that **cannot fail the run**. If synthesis or the freeze probe fails, log it, emit the best seed available (structural-only), and keep `configuration_success` as decided by the test gate. (This also fixes radical's inversion at `radical:agent.py:947`.)

### Components / touch-points (current branch)

| File | Symbol (line) | Change |
|---|---|---|
| `agent.py` | `_finalize_supervisor_artifacts` (1339) | Take a final `probe_env`; pass the freeze into rendering; never return `False` in a way that fails the run (decouple gate). |
| `agent.py` | `_run_v1` finalize block (~1188-1216) | Success = test gate only; artifact step is best-effort. |
| `src/synthesizer.py` | `apply_build_recipe` (2715) / `generate_dockerfile` (3986) | Append the inline-materialized pin layer as the final build command(s); render via the existing `_render_instruction_for_dockerfile` path (one renderer, no parallel renderer). |
| `src/synthesizer.py` | new helper | `build_pin_instruction(freeze: tuple[Fact,...]) -> str | None`: filter the project + editable/VCS/local entries, emit the `printf … && pip install -r` instruction; return `None` if the freeze is empty. |
| `run_repo2run_benchmark.py` | — | **No change.** Consumes the seed exactly as it consumes radical's today. `collect_observed_pip_install_constraints` becomes redundant-but-harmless. |
| `multi_docker_eval_adapter.py` | — | **No change.** Reads `workplace/Dockerfile` as today. |

### Data flow (unchanged plumbing, changed seed content)

```
AGENT _run_v1 (container ALIVE)
  build env → host-certified verified test run? ──no──► run fails (no seed needed)
                         │ yes
                         ▼  configuration_success = True   (gate = tests, decoupled)
            final probe_env() → EnvSnapshot.installed (pip freeze)
                         ▼
   synthesize structural commands (ledger)  +  build_pin_instruction(freeze)
                         ▼
            generate_dockerfile() → workplace/Dockerfile   (FULL pinned seed)
                         ▼  sandbox.close()  (container gone)
   ─────────────────────────────────────────────────────────────────────────
RUNNER  (UNCHANGED)
   read workplace/Dockerfile text → render_eval_dockerfile + normalize
   prepare_eval_build_context (git clone + checkout base + git clean -fdx)
   build → test → two-tier repair (bounded)  → success / needs_repair
```

---

## Error handling & edge cases

- **Empty / failed freeze probe:** `build_pin_instruction` returns `None`; emit the structural-only seed. Never fail the run. Log a warning.
- **Project appears in the freeze** (`-e /app`, `<pkg> @ file://…`, VCS URLs, `… @ <local path>`): filter these out of the pin so they don't fight `pip install -e .` or pin an unresolvable path. The structural `pip install -e .` owns the project.
- **Pin vs structural version conflict:** pin layer is **last**, so the frozen version wins deterministically.
- **Wheel needs a missing system header:** the pin locks versions, not buildability. The structural `apt-get` commands (from the ledger) and the runner's repair loop cover this; the pin does not regress it.
- **Very long freeze:** the inline `printf … > /tmp/pinned-requirements.txt` line is long but valid; matches the runner's existing constraint-materialization pattern.
- **Garbage-in (false success / collect-only):** out of scope here — the test gate (`_resolve_v1_verified_test_run`, ≥1 passed, collect-only excluded) is the guard, and it already exists.

---

## Testing strategy

Unit (pytest), no Docker required for the core:

1. **`build_pin_instruction`**
   - Given a freeze with normal `name==version` entries → emits a single `RUN printf … && pip install -r /tmp/pinned-requirements.txt` containing exactly those pins.
   - Filters the project / `-e` / VCS / local-path entries out of the pin.
   - Empty freeze → returns `None`.
2. **Seed assembly**
   - Structural commands precede the pin layer; the pin layer is the **last** build command.
   - The seed uses `RUN`-materialization, contains **no `COPY pinned-requirements.txt`** (regression guard for the `git clean -fdx` trap).
3. **Success decoupling**
   - A run whose test gate passed but whose freeze probe raised still returns `configuration_success = True` and writes a structural-only seed (no exception escapes finalize).
   - Synthesis/seed failure never flips a passed test gate to failure.
4. **Integration (1 repo, opt-in / live):** one repo through the repo2run runner end-to-end; assert the eval `docker build` does not fail on a missing pin file, and the pinned closure is present in the built image (`pip show` a transitive dep that the lossy replay would have dropped).

---

## Success criteria

- The v1 agent writes a full seed Dockerfile whose dependency layer is a `pip freeze` closure pin, inline-materialized in a `RUN`.
- Seed/synthesis failure can no longer turn a host-certified test pass into a run failure.
- The runner and adapter are byte-for-byte unchanged and consume the new seed without modification.
- On a repo where lossy replay produced a hollow Dockerfile, the pinned seed builds with the dropped dependency present (fewer or zero repair rounds needed).

---

## Open questions for the user

1. **Pin file path inside the image:** `/tmp/pinned-requirements.txt` (ephemeral) vs `/app/.pinned-requirements.txt` (kept in the image for debuggability). Default: `/tmp`. OK?
2. **Keep the runner's `-c` observed-constraints mechanism?** It becomes redundant under the freeze `-r`. Recommendation: leave it (harmless, and it still helps any non-frozen install the repair loop adds). Agree?
3. **Editable project install order:** keep `pip install -e .` in the structural layer (before the pin) so the project's own metadata resolves, then let the pin lock the deps. Any repo where `-e .` must come *after* the pin? (Not aware of one; flag if you are.)
```
