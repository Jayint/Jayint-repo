# Synthesizer Fidelity (Tier 1) — Design Spec

- **Date:** 2026-06-20
- **Branch:** john-planner-v1
- **Status:** Draft for review
- **Scope:** Root-cause fixes to the v1g Dockerfile synthesizer so that environments v1g *already builds successfully in-sandbox* survive the rebuild and reproduce. Real-success-moving, artifact-preserving.

---

## 1. Problem

When v1g genuinely solves an env in its live sandbox (reaches `done_flag`, pytest passes), the **rebuilt** Dockerfile frequently fails to build or produces a broken env — so a real in-sandbox win is scored as a failure. This is the dominant *recoverable* defect: it loses real-success (the metric that matters), not just coverage.

Observed symptom families (each previously treated as a separate repo bug):
- Replayed `sed`/`echo` edits to a config file accumulate and corrupt it (duplicate `[project]` → invalid TOML).
- Multi-line file writes (heredocs) are recorded truncated and then dropped entirely, so the file edit is lost.
- A later `pip install <pypi-name>` overwrites an earlier editable install → `ModuleNotFoundError` at test time.
- A pinned dependency closure is emitted *alongside* the replayed install commands → resolver conflict.
- Generated `RUN` lines are malformed (a literal `RUN` token inside another `RUN`'s body) → `/bin/sh: RUN: not found`.

## 2. Root cause (the single underlying mechanism)

**The synthesizer reconstructs the environment by *replaying the agent's command trajectory* (an imperative path) instead of *capturing the achieved end state* (a declarative snapshot).**

This is literal in the code: `build_commands_from_ledger` (`src/envstate/synthesis.py:78`) iterates ledger events and re-emits the `rc==0` mutating commands — including file-edit commands (`sed -i`, `printf > f`, `python -c …open`) and `pip install` commands — in trajectory order, preserving duplicates by design. The pinned closure (`build_pin_instructions`) is then appended *in addition* (`agent.py:1459-1465`).

Every symptom above is the same failure: **the replayed path diverges from the state the agent actually achieved.**
- Files: replaying edit *commands* re-runs superseded edits, and any command lossily recorded (truncated heredoc) corrupts or loses the file. The achieved *file content* is never consulted.
- Packages: replaying install *commands* re-runs steps that overwrite/undo each other, and competes with the pin. The achieved *package closure* is captured (`_final_installed`) but used only as an add-on, not as the source of truth.
- Emission: translating arbitrary command *text* into `RUN` lines is unsafe.

A working env decomposes into: **base image + system packages + python-package closure + edited files + env vars.** v1g *already captures* the closure (`probe_env → _final_installed`) and env vars (`extract_env_vars_from_ledger`). It does **not** capture the achieved file state, and it does not treat the closure as authoritative. Tier 1 closes that gap.

> **Design principle:** *Synthesize from achieved state wherever the state is capturable; replay only the irreducible, through a validated emitter.*

This is deliberately **not** the per-repo patch approach (fix jhao104's TOML, fix nba_api's overwrite). Those are five symptoms of one mechanism; we change the mechanism.

## 3. Goals / Non-goals

**Goals**
- Environments that pass in-sandbox reproduce on rebuild (convert existing in-sandbox wins → real-success + working artifact).
- Eliminate, by construction, the *classes*: superseded-edit replay, truncated-edit loss, install-overwrite, pin-vs-replay conflict, malformed `RUN`.
- Preserve the reproducible-Dockerfile guarantee and the honest `done_flag` (untouched).

**Non-goals (explicitly deferred)**
- Best-of-N partial-artifact / always-emit-something (separate, previously rejected design).
- Reducing planner giveup frequency / preflight relaxation (Tier 2).
- Base-image selection and "dead-end command" pruning (e.g. a `meson setup` that succeeded but wasn't on the working path) — noted as residual §6.
- The RAT-clone (score the live sandbox). Out of scope: we keep the rebuild.
- Synth-codegen for *non-Python* package managers beyond what the closure already covers.

## 4. The three root-cause fixes

### Fix 1 — Files: capture final content, stop replaying edits
**Root cause:** file state is reconstructed by replaying edit commands.
**Change:** at synthesis, for every file the agent demonstrably edited, read that file's **final bytes from the achieved state** and emit a single deterministic write of that content; never replay the edit commands.
- *Which files:* extract target paths from the rc==0 edit events the existing detector already identifies (`_is_source_file_edit` in `synthesis.py`) — i.e. the `sed -i <file>`, `printf/echo/cat/tee > <file>`, `python -c …open('<file>')` targets. (Bounded and generalizes to any edited file; no per-repo logic.)
- *How:* read each file's final content from the live sandbox container (which holds the achieved state; `sandbox.exec_readonly("cat <path>")`, snapshot = `last_success_image`).
- *Emit:* one Dockerfile write of the captured content via the existing heredoc-safe emitter (`_consume_heredoc_body`/`_extract_heredoc_descriptor` already exist).
**Eliminates:** superseded-edit replay (the file is written once, final), truncated-heredoc loss (we read the real file, not the recorded command), `sed`-text corruption.
**Edge cases to handle in plan:** binary/large files (size cap + skip-with-log), files outside the repo tree, path translation between sandbox path (`/app`) and rebuild path (`/testbed`/repo root), a file edited then deleted (capture only files present in final state).

### Fix 2 — Packages: the captured closure is the single source of truth
**Root cause:** dependency state is reconstructed by replaying install commands AND bolting on a pin → two sources, order-dependent overwrites, conflicts.
**Change:** make the captured closure authoritative. **Stop emitting replayed package-install commands** (`pip install …`, `poetry install`, `uv …`, etc.) from the ledger recipe; instead emit exactly:
1. the **frozen closure** for dependencies (the existing `build_pin_instructions` from `_final_installed`), and
2. **one** explicit project install for the repo's own package (editable), derived from state — because the pin excludes the project by name (no `==`).
**Eliminates:** PyPI-overwrites-editable (no second install to clobber), pin-vs-replayed-install conflict (only one source), superseded/iterative installs. Works regardless of how packages were installed in-sandbox (poetry/uv resolve into site-packages; the freeze captures the result), which also removes the poetry-vs-pin class.
**Edge cases to handle in plan:** packages that build from source on rebuild (C extensions) need their system build deps — those come from apt commands (still replayed; see Fix 3) and are partly the deferred system-dep work; the editable project install must be emitted exactly once and must match the detected build backend; ensure `_final_installed` reflects the *scored* state (for `done_flag` repos this is end-of-loop = the working state, which is correct for Tier 1).

### Fix 3 — Emitter: one command per RUN, validated before emit
**Root cause:** translating arbitrary replayed command text into `RUN` lines is unsafe (the resilient pip/apt wrappers and the record path can concatenate, producing a literal `RUN` inside a `RUN` body).
**Change:** route every remaining *irreducible* replayed command (system installs, build steps that survive Fix 1/2) through a single hardened emitter that guarantees **one logical command per `RUN`**, proper multi-line/quoting via the existing heredoc machinery, and adds a **Dockerfile validation pass** in `generate_dockerfile` that rejects/repairs any `RUN` whose body contains a bare Dockerfile directive token (`RUN`/`FROM`/`COPY`/…) and (if available) runs a cheap parse check.
**Eliminates:** malformed-`RUN` / no-op-recipe.
**Note:** Fixes 1 and 2 remove most of the commands that were being mistranslated (no replayed `sed`, no replayed `pip install`), so Fix 3 mainly hardens the residual apt/build-step path and acts as a final safety gate.

## 5. Data-flow change

Today: `ledger → build_commands_from_ledger (replay edits + installs) → apply_build_recipe → +pin → generate_dockerfile`.

After: `ledger + final container state →`
- file edits → **captured final content** (Fix 1),
- dependencies → **frozen closure + one project install** (Fix 2),
- residual irreducible commands (apt, build steps, env vars) → **validated emitter** (Fix 3),
`→ generate_dockerfile (with validation pass)`.

`done_flag` / real-success determination is unchanged.

## 6. Residual / known-not-fixed (documented, not silently dropped)
- **Dead-end successful commands** on a non-working path (e.g. pyads `meson setup build` that succeeded partially but wasn't needed). Requires minimal-necessary-command selection; harder, deferred. Fix 1/2 reduce but don't eliminate this.
- **System/native build deps** for source-built packages (compilers, `-dev` headers). Partly the separate system-dep grounding work.
- **Base-image mismatch** (e.g. EOL Python, oversized CUDA image). Image-selection, separate.

## 7. Verification strategy (anti-overfitting)

The fixes are validated by *symptom class* and on *held-out* repos, not by making the three named repos pass.

1. **Unit tests** on the new isolated units (see §8): file-capture path extraction + content emit; closure-as-sole-source recipe builder (asserts no replayed `pip install` survives, exactly one project install, no pin/install duplication); emitter validation pass (rejects a `RUN` containing a bare `RUN`).
2. **Mechanism tests** (synthetic, repo-agnostic): a ledger that edits the same file 3×, that installs then overwrites a package, that contains a truncated heredoc, that contains a compound `install && pytest` — assert the emitted Dockerfile reflects final state with no replay artifacts.
3. **Cleanroom rebuild check** on the affected in-sandbox-success repos (jhao104, epam, nba_api + the true `build_failed` set): emitted Dockerfile must build AND reproduce the in-sandbox pass-rate.
4. **Regression guard (critical):** the set of repos that are *currently* real-success must stay real-success — Tier 1 must not lose a single working artifact. Run on the full 50; compare real-success and hollow before/after via `compute_essr.score_agent`.
5. **Honesty guard:** hollow count must not increase; `done_flag` semantics unchanged.

Success = real-success strictly increases, hollow does not increase, no regression on currently-passing repos.

## 8. Components / boundaries

New, small, independently testable units (favor new modules over growing `synthesizer.py`):
- `FileStateCapturer` — given the ledger + a read-from-container callable, returns `{path: final_content}` for edited files. Pure given the callable; mockable.
- closure-authoritative recipe builder — replaces the package-install portion of `build_commands_from_ledger`; takes `_final_installed` + project descriptor → `[pin RUNs, one project-install RUN]`.
- `DockerfileEmitter`/validator — one-command-per-RUN emit + the validation pass; reused by `generate_dockerfile`.

`build_commands_from_ledger` is refactored to emit only *irreducible* commands (not file edits, not package installs); the file and package dimensions move to the two new units.

## 9. Risks / open questions (resolve in the plan)
- **File capture cost/safety:** reading N files from the container per repo — bounded by edited-file count; cap size; handle binaries.
- **Closure completeness for source-built deps:** does pinning a version that needs a C build succeed on rebuild without the apt build deps? (Interacts with deferred system-dep work — measure on the regression set.)
- **Exactly-once project install** detection (editable vs build-backend) — must be robust across setuptools/poetry/uv/flit/hatch.
- **`_final_installed` timing:** correct for `done_flag` repos (end-of-loop = working state); not extended to best-of-N (out of scope).
- **Validation pass false-positives:** ensure legitimate heredoc bodies containing the word `RUN` aren't rejected.
