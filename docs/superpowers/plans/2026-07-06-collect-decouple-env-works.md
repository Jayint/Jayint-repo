# Decouple `env_works` from collection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Stop `pytest --collect-only` framework/config errors from sinking the eval headline; a real dependency `ModuleNotFoundError` during collection still fails `env_works`.

**Architecture:** Eval-only change to `src/eval/build_script_eval/`. Add a `collect_ok` signal to `LadderResult`; on a collect failure, reuse the existing `classify_execution_failures`/`classify_tool_failures` to decide real-gap (fail env_works) vs framework-noise (keep env_works). No core pipeline, no `coverage.py`/`render_fidelity.py` edits.

**Tech Stack:** Python 3.10+, pytest, dataclasses. Design: `docs/superpowers/specs/2026-07-06-collect-decouple-env-works-design.md`.

## Global Constraints

- Branch `john-v3-multi-lang` is SHARED: commit LOCALLY only; NEVER push/rebase/reset; `git add` only the specific named files below, never `-A`.
- Do NOT modify `src/eval/language_package_eval/coverage.py` or `src/eval/graph_fidelity/render_fidelity.py` (reuse-by-import boundary).
- Headline formula `env_works_passed = install_ok AND env_works` is UNCHANGED.
- `collect_ok` MUST be defined with a default (`= None`) so existing keyword constructions and `tests/eval/build_script_eval/test_scorecard.py`'s `_ladder(**base)` helper keep working.
- Every existing test in `tests/eval/build_script_eval/test_replay_ladder.py` must still pass unchanged in intent (a few gain a `collect_ok` assertion).

---

### Task 1: Add `collect_ok` and decouple the env_works rung

**Files:**
- Modify: `src/eval/build_script_eval/scorecard.py` (add field + surface in row)
- Modify: `src/eval/build_script_eval/replay.py` (`_fail` + `run_replay_ladder` rung)
- Modify: `src/eval/build_script_eval/report.py` (funnel `_RUNGS`)
- Test: `tests/eval/build_script_eval/test_replay_ladder.py` (new + updated cases)

**Interfaces:**
- Consumes: `merge_gaps`, `classify_execution_failures`, `classify_tool_failures`, `real_first_failure` (already imported in `replay.py`).
- Produces: `LadderResult.collect_ok: bool | None`; row key `"collect_ok"`; new ladder reason string `"collect_incompatible"`.

- [ ] **Step 1: Add the failing tests** (append to `tests/eval/build_script_eval/test_replay_ladder.py`)

```python
def test_collect_framework_error_keeps_env_works_true(monkeypatch):
    # import clean, bootstrap clean, but --collect-only errors on a pytest
    # framework/config incompatibility (no real missing dependency). env_works
    # must stay True; only collect_ok flips to False.
    for stderr in (
        "tests/test_x.py: PytestRemovedIn10Warning: nose-style ...\n"
        "filterwarnings = error -> collection ERROR",
        "ImportError: cannot import name 'notset' from '_pytest.config'",
    ):
        _patch(monkeypatch, {"collect": _rc(1, stderr=stderr)})
        res = run_replay_ladder("/repo", "img", "setup", "triv")
        assert res.install_ok is True
        assert res.env_works is True
        assert res.collect_ok is False
        assert res.highest_rung == "env_works"
        assert res.reason == "collect_incompatible"
        assert res.gaps == ()
        assert res.first_failure is not None


def test_collect_real_module_gap_fails_env_works(monkeypatch):
    # a genuine missing dependency during collection IS an env gap.
    _patch(monkeypatch, {"collect": _rc(
        1, stderr="ModuleNotFoundError: No module named 'pytest_asyncio'")})
    res = run_replay_ladder("/repo", "img", "setup", "triv")
    assert res.install_ok is True
    assert res.env_works is False
    assert res.collect_ok is False
    assert res.reason == "env_broken"
    assert {(g["tier"], g["id"]) for g in res.gaps} == {("PACKAGE", "pytest_asyncio")}


def test_collect_ok_true_when_all_green(monkeypatch):
    _patch(monkeypatch, {})  # every phase rc0
    res = run_replay_ladder("/repo", "img", "setup", "triv")
    assert res.collect_ok is True


def test_collect_ok_none_when_bootstrap_fails(monkeypatch):
    _patch(monkeypatch, {"bootstrap": _rc(127, stderr="pip: command not found")})
    res = run_replay_ladder("/repo", "img", "setup", "triv")
    assert res.collect_ok is None
```

- [ ] **Step 2: Run the new tests, confirm they FAIL**

Run: `python3 -m pytest tests/eval/build_script_eval/test_replay_ladder.py -q -k "collect"`
Expected: FAIL (`LadderResult` has no `collect_ok`; rung not decoupled).

- [ ] **Step 3: Add the `collect_ok` field to `LadderResult`** (`scorecard.py`)

In the `@dataclass(frozen=True) class LadderResult`, append the field LAST (it must come after the no-default fields), with a docstring-adjacent comment:

```python
    gaps: tuple[dict, ...]        # classify_execution_failures dicts (typed)
    collect_ok: bool | None = None  # True=collected clean, False=collect failed, None=not attempted
```

- [ ] **Step 4: Surface `collect_ok` in the scorecard row** (`scorecard.py`, `_assemble_scorecard`)

Add the key right after `"env_works"`:

```python
        "install_ok": ladder.install_ok,
        "env_works": ladder.env_works,
        "collect_ok": ladder.collect_ok,
        "tests_ran": ladder.tests_ran,
```

- [ ] **Step 5: Add `collect_ok` to the report funnel** (`report.py`)

```python
_RUNGS = ("install_ok", "env_works", "collect_ok", "tests_ran", "tests_passed")
```

(The funnel counts truthy values, so `None` is 0 and existing report tests stay green.)

- [ ] **Step 6: Rewrite `_fail` and the env_works rung** (`replay.py`)

Replace `_fail` (lines 36-42) with:

```python
def _fail(rung_reached: str, reason: str, output: str, *, install_ok: bool,
          collect_ok: bool | None = None) -> LadderResult:
    return LadderResult(
        install_ok=install_ok, env_works=False, collect_ok=collect_ok,
        tests_ran=False, tests_passed=False,
        highest_rung=rung_reached, reason=reason,
        first_failure=real_first_failure(output),
        gaps=merge_gaps(classify_execution_failures(output), classify_tool_failures(output)),
    )
```

Replace the whole RUNG-2 block (current lines 59-100, from the `# RUNG 2` comment through the `pytest_unavailable` return) with:

```python
        # RUNG 2 — env_works: the repo's top-level import is the HARD gate. Test
        # COLLECTION is a separate, more-demanding signal (collect_ok): a
        # pytest-version/config incompatibility (deprecation-as-error under
        # filterwarnings=error, a dropped `_pytest` internal) must NOT sink the
        # headline; only a real, classifier-detectable env gap surfaced during
        # collection does. Import + bootstrap run while the network is still up.
        if top_import:
            imp = box.run(f"{cd} && python3 -c 'import {top_import}'", timeout=120)
            if not imp.ok:
                return _fail("install", "env_broken", imp.stdout + imp.stderr, install_ok=True)

        # Probe-only bootstrap (NOT graph-attributed): pytest is the probe's own
        # tool. A bootstrap FAILURE is probe-infra, never a coverage gap -- its
        # output is never classified.
        bootstrap_ok = box.run("pip install --no-input --quiet pytest", timeout=300).ok

        collect_ok: bool | None = None
        if bootstrap_ok:
            collected = box.run(f"{cd} && python3 -m pytest --collect-only -q", timeout=600)
            collect_ok = collected.ok
            if not collected.ok:
                collect_out = collected.stdout + collected.stderr
                collect_gaps = merge_gaps(
                    classify_execution_failures(collect_out), classify_tool_failures(collect_out),
                )
                if collect_gaps:
                    # a real missing need surfaced during collection -> env gap.
                    return _fail("install", "env_broken", collect_out,
                                 install_ok=True, collect_ok=False)
                # framework/config incompatibility -> env works, suite uncollectable.
                return LadderResult(
                    install_ok=True, env_works=True, collect_ok=False,
                    tests_ran=False, tests_passed=False,
                    highest_rung="env_works", reason="collect_incompatible",
                    first_failure=real_first_failure(collect_out), gaps=(),
                )

        # env_works has now passed (installed clean AND the repo imports, and if
        # pytest bootstrapped, tests COLLECTED clean). If pytest could not be
        # bootstrapped we cannot run the suite -- record a non-gap miss and stop.
        # EXCEPT: with no top_import AND no collect, NOTHING was verified, so
        # env_works=True would be vacuous.
        if not bootstrap_ok:
            if top_import is None:
                return LadderResult(
                    install_ok=True, env_works=False, collect_ok=None,
                    tests_ran=False, tests_passed=False,
                    highest_rung="install", reason="unverified_no_import_no_collect",
                    first_failure=None, gaps=(),
                )
            return LadderResult(
                install_ok=True, env_works=True, collect_ok=None,
                tests_ran=False, tests_passed=False,
                highest_rung="env_works", reason="pytest_unavailable",
                first_failure=None, gaps=(),
            )
```

Then in the RUNG 3/4 final return (current lines 108-116), add `collect_ok=True`:

```python
        return LadderResult(
            install_ok=True, env_works=True, collect_ok=True,
            tests_ran=tests_ran, tests_passed=tests_passed,
            highest_rung=highest, reason=reason,
            first_failure=None if tests_passed else real_first_failure(run.stdout + run.stderr),
            gaps=() if tests_ran else merge_gaps(
                classify_execution_failures(run.stdout + run.stderr),
                classify_tool_failures(run.stdout + run.stderr),
            ),
        )
```

Also update the RUNG-2 docstring/comment at the top of `run_replay_ladder` if it still says "imports + tests COLLECT" as the env_works definition — the module docstring's rung list (`install ▸ env_works ▸ tests_ran ▸ tests_passed`) is fine; the inline comment is replaced above.

- [ ] **Step 7: Update the two existing tests that now assert `collect_ok`**

In `tests/eval/build_script_eval/test_replay_ladder.py`:
- `test_full_ladder_all_green`: add `assert res.collect_ok is True`.
- `test_bootstrap_failure_never_manufactures_a_gap`: add `assert res.collect_ok is None`.

(Leave all other existing assertions untouched — they must still pass.)

- [ ] **Step 8: Run the full eval unit suite, confirm GREEN**

Run: `python3 -m pytest tests/eval/build_script_eval -q`
Expected: PASS (all existing + 4 new cases). If any existing test fails, the rung rewrite changed behavior it should not have — fix the implementation, not the test.

- [ ] **Step 9: Commit** (only the named files)

```bash
git add src/eval/build_script_eval/scorecard.py src/eval/build_script_eval/replay.py src/eval/build_script_eval/report.py tests/eval/build_script_eval/test_replay_ladder.py docs/superpowers/specs/2026-07-06-collect-decouple-env-works-design.md docs/superpowers/plans/2026-07-06-collect-decouple-env-works.md
git commit -m "fix(build_script_eval): decouple env_works from collection (collect_ok signal)"
```
