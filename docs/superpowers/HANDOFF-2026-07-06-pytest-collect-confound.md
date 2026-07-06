# Handoff — fix the pytest-collect confound in build_script_eval (+ robustness roadmap tracker)

**Branch:** `john-v3-multi-lang` (SHARED — commit LOCALLY, **NEVER push/rebase/reset**; append-only).
**Date raised:** 2026-07-06.

## Where things stand
- **Eval:** `src/eval/build_script_eval/` — a Python-only, execution-only e2e build-script eval. Per repo it runs construction-only `build_dep_graph` → `render_build_script` → fresh-Docker replay ladder `install → env_works(import + pytest --collect-only) → tests_ran → tests_passed`. Headline = first-pass `env_works` (= `install_ok AND env_works`). CLI: `python3 -m src.eval.build_script_eval --run [--only a,b] [--stratum S_syslib]`; corpus is committed (16 repos, `corpus.py`); repos fetch to gitignored `outputs/build_script_eval/_smoke/`.
- **Latest full 16-repo metric (post-R1):** `first_pass_env_works` = **11/16 raw**, **13/16 adjusted**. 11 genuine passes; **3 real failures** (lxml→R2-A, cryptography→R1-Rust-gap, semantic-release→R4); **2 eval-artifact false-negatives (click, flask)** — THIS handoff's task.
- Research + roadmap: `docs/superpowers/research/{R1..R5-*,*-SUMMARY,*-diagnostic}.md`. Memory: `build-script-eval-and-robustness-roadmap`. SDD ledger (gitignored scratch): `.superpowers/sdd/progress.md`.

## ✅ RESOLVED (2026-07-06, commits d95fb56 + 671e70b)
Decouple-from-collect fix LANDED + real-corpus gate PASSED. `env_works` = `install_ok AND
top-import`; test collection is now a separate `collect_ok` signal. On a collect failure the
code reuses `merge_gaps(classify_execution_failures, classify_tool_failures)`: any gap ⇒ real
env gap (env_works=False, as before); zero gaps ⇒ pytest framework/config incompat
(env_works=True, collect_ok=False, reason `collect_incompatible`, stop at env_works). Zero new
heuristics. Spec/plan: `docs/superpowers/specs|plans/2026-07-06-collect-decouple-env-works*`.
**Result: click + flask flipped to env_works=True; controls unchanged; lxml/semantic-release
still fail at install (correct). Full 16-repo headline 11/16 → 13/16 (S_control 8/9, S_syslib
5/7).** Truthful+complete (fix monotonic-up; 3 remaining failures all fail pre-collect →
fix-immune): semantic-release→R4(git), lxml→R2-A, cryptography→R1-Rust-gap. Everything below is
the original task write-up (kept for provenance) + the still-live roadmap tracker.

## THE TASK (option a): fix the pytest-collect confound
**Symptom:** click and flask score `env_works=False` even though their env installs cleanly AND the repo imports. Cause: the env_works rung bootstraps the LATEST pytest and runs `pytest --collect-only -q`, which errors for **pytest-version/test-config** reasons that are NOT env gaps:
- **click**: `pytest.PytestRemovedIn10Warning` (a deprecated parametrize idiom) becomes a collection ERROR because click's own config sets `filterwarnings = error`.
- **flask**: `ImportError: cannot import name 'notset'` — flask's test/conftest imports a pytest internal the bootstrapped version dropped.

Both installed + imported fine (click even collected 1593 tests before the error). So **`env_works`-via-`--collect-only` is confounded by test-suite/pytest compatibility, not "does the env work."**

**Code locus:** `src/eval/build_script_eval/replay.py` — the env_works rung (the `pip install --no-input --quiet pytest` bootstrap + `pytest --collect-only -q` call); `scorecard.py` (`LadderResult`, `env_works_passed`); `tests/eval/build_script_eval/test_replay_ladder.py` (the Docker-free `_FakeBox` harness). Reuse-boundary constraint: do NOT modify `coverage.py`/`render_fidelity.py`.

**Recommended fix (confirm via a short brainstorm/spec first — it's an eval-design change):**
- **Decouple `env_works` from collect.** Redefine `env_works = install_ok AND top-level import OK`. Move `--collect-only` to a NEW separate ladder signal (`collect_ok`, sitting between env_works and tests_ran) so a collection failure is recorded as a diagnostic but does NOT sink the headline. Principled: env_works should mean "the env is set up and the repo imports," not "the test suite is pytest-compatible." This flips click/flask to `env_works=True`.
- **Alternatives to weigh:** (i) classify the collect failure — a `ModuleNotFoundError`/dependency import error is a REAL env gap (keep failing env_works), but a pytest-framework/config error (`PytestRemovedIn10*`, `cannot import name '…' from _pytest`, usage/collection-config errors) is NOT (don't fail env_works); more surgical, preserves collect's import-coverage value. (ii) pin/bootstrap a compatible pytest or prefer the repo's own pytest — messier, no universal version.

**Verification (the gate):**
1. Re-run the affected + control repos: `python3 -m src.eval.build_script_eval --run --only click,flask,jinja,requests,httpx,dotenv` — **click/flask must flip to `env_works=True`**; jinja/requests/httpx/dotenv stay pass (no regression).
2. Re-run a real-failure repo to confirm it STILL fails for the right reason: `--only lxml,semantic-release` — lxml still fails at install (bs pin), semantic-release still fails on git. A genuine import/dependency collect error must still fail env_works.
3. Target: clean headline ≈ **13/16**. Add a Docker-free `_FakeBox` test pinning: a pytest-framework collect error → `env_works=True, collect_ok=False`; a `ModuleNotFoundError` collect error → `env_works=False`.
4. Full unit suite green: `python3 -m pytest tests/eval/build_script_eval -q`.

## ROADMAP TRACKER — all previously-identified issues (keep in sync)
Verified against real code/repos (see `docs/superpowers/research/`). Order = user's chosen priority: R1 first, then R2-Fix-A; gate each against the diagnostic sweep AND the `package_installability` 0.9143/apt=0 eval where the fix touches the core pipeline.

| # | Root cause | Fix | Fixes | Effort | STATUS |
|---|---|---|---|---|---|
| **R2-B** | Unknown build-mode treated as source-built (`build_deps.py:309` gates on `is False`) | tighten to `is not True` | typer 31→~1 apt | 1 line | ❌ **REJECTED** — conflicts with intentional recall-first unknown-seeding (`test_seed.py:68` + `predict.py` conservative-`None` + the 0.9143 gate all rely on unknown→seed). Use R2-A instead. |
| **R1** | Repo-under-test (`NodeType.PROJECT`) excluded from every build-dep stage | include project node + own-name Debian Build-Depends + AST-scan `setup.py` `Extension(libraries=)` → existing `os_resolver` | pygraphviz, lxml (+pillow/pyyaml/pyzmq generalize) | medium — main robustness win | ✅ **DONE + replay-verified** (commits `4e93049` scanner, `be95b04` stage, `1e41866` gate+apt-guard fix). pygraphviz gcc-fail→tests_passed; 5 native repos gained correct build-deps; 0 control regression. Gate §2.3 on `has_native_build_signal` (a sweep caught a click over-prediction regression). **Deferred: Rust/CGo scanner** (cryptography still fails — Rust backend not detected). |
| **R4** | Runtime-tool detection misses AND false-positives `git` | `LIBRARY_REQUIRES_BINARY` table (GitPython⇒git) keyed on resolved dep identity + 2 scope guards (`config_scan.py` missing `"tools"` in excluded-segments; `_program_from_call` matching bare `run(` without checking callee is `subprocess`) | semantic-release gains git; cryptography/pyzmq drop false git | small | ⏳ PENDING. Design: `docs/superpowers/research/R4-runtime-tool-detection.md`. |
| **R3** | Debian-source imprecision (`click`→Ubuntu-Click; bare `postgresql`) | drop `<!nocheck>`/`<!nodoc>`-tagged deps (`parse_build_depends` discards the tags today) + tiered source-accept gate (existing plan `docs/superpowers/plans/2026-07-06-debian-source-disambiguation.md`) + flip `is_system_lib` denylist→allowlist of `-dev`/`lib*` shapes | typer's Vala/GLib set; psycopg2 drops `postgresql`, keeps `libpq-dev` | medium | ⏳ PENDING. Design: `R3-debian-source-precision.md`. |
| **R2-A** | Resolution fails: an incidental dev-tool pin (`ruff==0.2.0`) anchors `exclude_newer` → `httpx` unsatisfiable → error unparsed → fallback never stamps build-mode → all-unknown → apt dump | 1 resolver-error regex (`resolve_errors.py`) + fix CI-file scoping (`evidence.py`: `requirements-github-actions.txt` defaults to `kind="dependency"`) + dev-pin `exclude_newer` scoping (`pins.py`) | typer unk 67→0 (the real fix; flips lxml too) | medium | ⏳ PENDING — the NEXT core fix. **Needs the `package_installability` 0.9143/apt=0 gate (~30-min Docker) as its regression guard.** Design: `R2-buildmode-resolution.md`. |
| **R5** | Cross-cutting: keep fixes ecosystem-agnostic + eval as guardrail | `EcosystemProvider` detection hooks + `ReplayProfile` to parameterize the ladder probe + repo-type corpus strata | generalization to Node/Go; regression gate | — | ⏳ PENDING (land alongside 1–5). Design: `R5-architecture-generalization.md`. |

Also open (small, R3/R4-adjacent): httpx predicts a stray `black`; cryptography/pyzmq a false `git` (R4).

## Process
- SDD discipline (superpowers): brainstorm → spec → plan → subagent-driven execution with per-task review, sonnet implementers/reviewers. Gate core-pipeline fixes against BOTH the diagnostic replay AND the `package_installability` 0.9143/apt=0 eval. This pytest-collect fix is **eval-only** (no core pipeline) → gate = the build_script_eval replay/unit suite only.
- The diagnostic sweep IS the guardrail: "Moves N (target repos flip) AND Regresses 0 (others unchanged)". A regression that passes unit tests + review can still be caught by the real-corpus replay (it happened on R1) — always re-run the corpus.
