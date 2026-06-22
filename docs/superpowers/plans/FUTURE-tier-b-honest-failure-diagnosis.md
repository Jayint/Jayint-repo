# FUTURE — Tier B honest failure-cause diagnosis

**Status:** deferred (decision 2026-06-12). Tier B currently finalizes a partial run on the
**majority-pass** bar alone (`pass_ratio >= MIN_PASS_RATIO`, default 0.5), with **no**
failure-cause diagnosis. This doc captures the upgrade that makes Tier B *honest* and the
adversarial-audit cases it must close, so the work can be picked up later without re-deriving it.

## Why this exists

Tier B accepts a run where the **majority** of tests passed and some fail. The honesty question
is: **are the failing tests failing because of the repo's own code (acceptable) or because the
environment is broken (must reject)?**

- `AssertionError`, repo source bugs, external-network flakiness → **non-env** → fine to accept.
- `ImportError: libGL.so.1: cannot open shared object file`, `pg_config: command not found`,
  `could not connect to server`, numpy ABI mismatch → **environment is broken** → must reject.

Today (majority-pass only) the second class is **accepted** when the pass-ratio is high
(e.g. numpy ABI break at 988 passed / 1 failed → ratio 0.999 → finalized green). That injects
**hollow successes** into the benchmark — a Dockerfile that replays a broken native env but is
scored as a verified success. This is the exact failure mode the project's research thesis (honest
environment construction) is meant to avoid, so it must be fixed before Tier B numbers are trusted
for the system/native-dependency story.

**Conscious trade-off accepted for now:** simplicity + maximal coverage recovery (incl. repos
whose few failures are network/source, e.g. `swar/nba_api` 686/689). The hollow-success risk is
documented and pinned by `*_accepted_until_diagnosis_gate` tests.

## The fix (Option A — honest-by-construction)

Two adversarial honesty audits (2026-06-12, 6 lenses each, empirically run against the real code)
proved that a **denylist** of "known env-broken phrasings" is open-ended whack-a-mole: round 1
found 12 leaks, round 2 (after hardening) found 7 more — all new env-defect phrasings the list
didn't yet know. **Do not ship a pure denylist.** Instead invert to a positive signal:

> A partial-pass finalizes only when the failing tests fail with a **recognizably benign**
> cause — i.e. the output visibly shows `AssertionError` (or pytest's `E   assert ...` rewrite) —
> **AND** no env-defect signal is present. Any failure of an *other* class (ImportError / OSError /
> RuntimeError / Connection* / OperationalError / DLL / `.so` / `command not found` / …) → reject,
> *even if its specific phrasing is unknown*, because it is not an AssertionError.

This ends the whack-a-mole: unknown env-defect phrasings auto-reject (they aren't assertions). The
denylist (`observation_has_env_defect_signal`) stays as a **secondary** belt for mixed outputs.

### Gate (both finalize paths) — what the condition should become

```
partial_pass_accepted =
      observation_has_passing_test_signal(out)            # >=1 real pass
  and observation_has_test_failure_signal(out)            # it is a partial, not clean
  and observation_has_assertion_failure_signal(out)       # NEW positive: failures are AssertionError
  and not observation_has_env_defect_signal(out)          # belt (hardened denylist)
  and not observation_has_ambiguous_error_signal(out)     # 'N error' (collection/setup) -> reject
  and pass_ratio is not None and pass_ratio >= MIN_PASS_RATIO
```

`observation_has_assertion_failure_signal` (to add): matches `\bAssertionError\b` and pytest's
`^E\s+assert\b` rewrite line; conservative (returns False when the failure detail is truncated out,
so a bare "1601 passed, 2 failed" with no visible cause is rejected — we can't confirm it is benign).

### Wiring points (already structured for this)

- **v1 (benchmarked):** `agent.py :: _resolve_v1_verified_test_run` Path 3, the `if not ok:` branch
  (~line 1162). Re-add the rejects before the `ratio` check.
- **bundle/arm0:** `src/verification_bundle.py :: _collect_effective_observed_test_commands`, the
  partial-pass branch (~line 120). Re-add `and not env_defect and not ambiguous and <assertion>`.

### Dormant infrastructure already built and TESTED (just re-wire)

These were implemented + hardened against the audits in commits `267064d` and `bec7167`, then
**unwired** when Tier B was simplified. They still exist and pass their unit tests:

- `Synthesizer.observation_has_env_defect_signal` — hardened denylist (missing `.so` /
  `command not found` for any binary / DB-down phrasings / GLIBC / linker). **Dormant** (unwired
  when Tier B was simplified). Tests:
  `tests/test_synthesizer.py::ObservationEnvDefectSignalTests` + `…AuditHardeningTests`.

Still **wired** today (do NOT treat as future work):
- `Synthesizer.observation_has_ambiguous_error_signal` — `N error` (pytest collection/setup category).
  The current gate already rejects on this (keeps the pre-existing
  `test_rejects_agent_reported_bundle_when_prior_output_had_errors` green).
- `Synthesizer.observation_pass_ratio` — hardened against comma thousands separators and cross-session
  count cross-multiplication.
- `Synthesizer.MIN_PASS_RATIO = 0.5` — the majority knob.

Only `observation_has_assertion_failure_signal` is net-new; re-wiring `observation_has_env_defect_signal`
is the rest.

## The 19 audit cases the diagnosis gate MUST reject (regression spec)

Round 1 (12) + round 2 (7). Each is a broken environment that majority-pass alone accepts:

1. Missing system shared library — `OSError: libGL.so.1: cannot open shared object file`
2. DB down, no literal "Connection refused" — `OperationalError: could not connect to server`
3. MySQL/host-name DB down — `Can't connect to MySQL server (2003)`, `could not translate host name`
4. Missing shared library — `ImportError: libopenblas.so.0: cannot open shared object file`
5. Missing build toolchain/linker — `gcc: command not found`, `cannot find -lpq`
6. Thousands-separated failure count — `1,000 failed` read as 0 (now fixed in `observation_pass_ratio`)
7. ctest `N tests failed` phrasing read as clean (now fixed in failure-signal)
8. `observation_pass_ratio` cross-session `max()` cross-multiplication (now fixed)
9. Missing system binary `pg_config: command not found`
10. Missing system binary `ffmpeg: command not found`
11. Bundle stale-green after `xargs pip install` / `env pip install` (pre-existing arm0; `command_mutates_environment` gap)
12. Bundle lossy `| cut`/`sed`/`awk` truncation (pre-existing arm0; truncation guard only knows head/tail/grep)
13. numpy C-extension ABI — `RuntimeError: module compiled against API version ...`
14. numpy import — `ImportError: numpy.core.multiarray failed to import`
15. numpy import — `ImportError: numpy.core._multiarray_umath failed to import`
16. Windows native ext — `ImportError: DLL load failed while importing _ssl`
17–19. Further numpy/native variants (see audit transcript).

Cases 6, 7, 8 are **already fixed** (they were `observation_pass_ratio` / failure-signal bugs, not
diagnosis). Cases 11, 12 are **pre-existing arm0/bundle** issues (inert for the benchmarked v1 path)
— fix opportunistically. The rest (1–5, 9–10, 13–19) are what the assertion-positive inversion closes.

## Re-validation

After wiring: flip the `*_accepted_until_diagnosis_gate` tests in
`tests/test_v1_finalize_partial_pass.py` and `tests/test_verification_bundle_partialpass.py` from
"accepted" to "rejected", add an `observation_has_assertion_failure_signal` truth table to
`tests/test_synthesizer.py`, and re-run the adversarial audit workflow
(`fix3-honesty-audit`) — target verdict `CLEAN` (or only `PRE_EXISTING_BY_DESIGN` bundle nits).
