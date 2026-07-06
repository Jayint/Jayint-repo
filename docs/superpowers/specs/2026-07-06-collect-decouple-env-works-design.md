# Decouple `env_works` from test collection — build_script_eval design

**Date:** 2026-07-06 · **Branch:** `john-v3-multi-lang` (SHARED — commit local, never push).
**Scope:** eval-only (`src/eval/build_script_eval/`). No core pipeline touched.
**Supersedes handoff:** `docs/superpowers/HANDOFF-2026-07-06-pytest-collect-confound.md` (option a).

## Problem

The eval headline `first_pass_env_works` (= `install_ok AND env_works`) is
confounded. The `env_works` rung in `replay.py` bootstraps the latest `pytest`
and runs `pytest --collect-only -q`; a **non-zero collect exit** is treated as an
env failure. But `--collect-only` errors for reasons that are NOT env gaps:

- **click** (`8.4.2`): a `PytestRemovedIn10Warning` (deprecated parametrize
  idiom) becomes a *collection error* because click's own config sets
  `filterwarnings = error`. click even collected 1593 tests before the error.
- **flask** (`3.1.3`): `ImportError: cannot import name 'notset'` — a conftest
  imports a `_pytest` internal the bootstrapped pytest dropped.

Both install cleanly and import fine, yet score `env_works=False`. These are
**eval artifacts** (test-suite/pytest compatibility), not "does the env work."
Current metric: 11/16 raw; click + flask are the two false-negatives.

## Principle

`env_works` should mean **"the env installed and the repo imports."** Test
*collection* is a separate, more-demanding signal that also exercises importing
the test modules + conftest — where pytest-version/config incompatibilities
live. A framework/config collect failure must not sink the headline; a **real
missing dependency surfaced during collection still must** (the handoff's hard
requirement: "a real `ModuleNotFoundError` collect error must STILL fail
env_works").

## Design (chosen: decouple + reuse the existing classifiers)

Redefine the `env_works` rung:

1. **Top-level import is the hard gate** (unchanged). `top_import` fails ⇒
   `env_works=False`, `reason="env_broken"`, gaps from the import output. (This
   now returns immediately rather than also running collect — cleaner, and every
   existing test result is unchanged.)
2. **Bootstrap pytest** (probe-only, never graph-attributed — unchanged). A
   bootstrap *failure* is probe-infra, never classified.
3. **Collection becomes a separate signal `collect_ok`.** If bootstrap
   succeeded, run `--collect-only`:
   - **collect exit 0** ⇒ `collect_ok=True`; proceed to the suite rung.
   - **collect non-zero** ⇒ classify the collect output with the **same**
     `merge_gaps(classify_execution_failures(...), classify_tool_failures(...))`
     used at every other rung:
     - **any gap** (a dependency `ModuleNotFoundError`, a missing `.so`/tool) ⇒
       real env gap ⇒ `env_works=False`, `reason="env_broken"`, those gaps.
       Identical to today for real gaps.
     - **no gap** (framework/config incompatibility — deprecation-as-error, a
       dropped `_pytest` internal) ⇒ `env_works=True`, `collect_ok=False`,
       `highest_rung="env_works"`, `reason="collect_incompatible"`, no gaps, the
       collect tail recorded as `first_failure` (a diagnostic). Stop here (the
       suite would hit the same collect error → no information lost).

**Why reuse the classifiers instead of writing a framework-vs-real detector**
(the handoff's alternative (i)): the existing classifiers already turn
`ModuleNotFoundError: No module named 'X'` into a `PACKAGE` gap and recognize
missing `.so`/tools; a `PytestRemovedIn10Warning` or `cannot import name
'notset'` matches none of them → zero gaps. So "real gap" ≡ "the classifier
finds a gap." Zero new heuristics, zero new patterns to maintain, and it
generalizes: any classifier-recognized need sinks `env_works`; anything else is
framework noise. Verified against the actual regexes in
`coverage.py:290-323` and `classify.py:41-65`.

## Data model change

`LadderResult` gains one field, **defaulted so no existing construction site or
test helper breaks**:

```python
collect_ok: bool | None = None   # True=collected clean, False=collect failed, None=collect not attempted
```

- `None` for install-fail, import-fail, and bootstrap-fail paths (collect never ran).
- `False` for both collect-failure branches (real-gap and framework).
- `True` when collection succeeded.

`env_works_passed` (the headline gate) is **unchanged** — `install_ok AND
env_works` — because `env_works` itself is now correct.

## Surfacing

- `scorecard._assemble_scorecard`: add `"collect_ok": ladder.collect_ok` to the
  row (a diagnostic, next to `"env_works"`).
- `report._RUNGS`: insert `"collect_ok"` after `"env_works"` so the ladder
  funnel shows env-works→collect attrition. Funnel counts truthy only (`None`
  counts as 0), so existing report tests stay green.
- `attribute_failure` is unchanged: click/flask now have `env_works=True` ⇒
  `"pass"`.

## Non-goals / boundaries

- Do NOT modify `coverage.py` or `render_fidelity.py` (reuse-by-import boundary).
- Do NOT change the headline formula, the suite rung, or network isolation.
- Not in scope: the real failures (lxml→R2-A, cryptography→R1-Rust-gap,
  semantic-release→R4). They must keep failing for the same reasons.

## Verification (the gate)

1. **Unit (Docker-free `_FakeBox`), added:**
   - framework collect error (click's `PytestRemovedIn10Warning`; flask's
     `cannot import name 'notset'`) ⇒ `env_works=True, collect_ok=False,
     highest_rung="env_works", reason="collect_incompatible", gaps=()`.
   - `ModuleNotFoundError` collect error ⇒ `env_works=False, collect_ok=False,
     reason="env_broken"`, gap `("PACKAGE", <mod>)`.
   - `collect_ok` assertions added to the all-green and bootstrap-fail cases.
2. **Full unit suite green:** `python3 -m pytest tests/eval/build_script_eval -q`.
3. **Real-corpus replay (controller-run, Docker foreground):**
   - `--run --only click,flask,jinja,requests,httpx,dotenv` ⇒ **click/flask flip
     to `env_works=True`**; jinja/requests/httpx/dotenv unchanged (no regression).
   - `--run --only lxml,semantic-release` ⇒ still fail for the same reasons
     (lxml at install; semantic-release on git).
   - Target clean headline ≈ **13/16**.

The R1 lesson governs: unit tests + review can miss a real-corpus regression, so
the controller re-runs the corpus replay as the final gate.
