"""Dependency-group / extra test-scope policy vocabulary (read-lane constants).

The two frozensets below classify a declared dependency GROUP (PEP 735
``[dependency-groups]`` / requirements-file group) or optional-dependency EXTRA
by whether it is needed to run the test suite. They are shared by the test-scope
reader (``invocation_resolver``) and the declared-roots builder (``roots``); they
live here — a low module neither lane owns — so the reader imports the policy
downward instead of reaching into the install lane. Pure data (no imports).
"""
from __future__ import annotations

# Dev/test groups NOT needed to run the test suite: docs builders and
# release/packaging tooling. Every OTHER dev_group (test, lint, typing, dev, ...)
# is default-included (recall-first; the testability gate + Phase-A repair back it
# up). Matched case-insensitively against the normalized group name.
_DEV_GROUP_DENYLIST: frozenset[str] = frozenset(
    {
        "docs", "doc", "documentation",
        "release", "publish", "deploy",
        "benchmark", "benchmarks", "profiling",
        "examples", "demo",
    }
)


# FIX 1 (B2) — `optional_dependency` group names (`[project.optional-dependencies]`
# / `setup.cfg` `extras_require`) that mean "tooling needed to run/lint/typecheck
# the test suite", never a mutually-exclusive runtime FEATURE. Matched
# case-insensitively against the normalized group name, same as
# `_DEV_GROUP_DENYLIST`.
#
# Rationale for an ALLOWLIST (not a denylist, unlike dev_group): unlike PEP 735
# `[dependency-groups]` / dev-requirements files — which are ALWAYS
# build/test-time tooling by construction, so denylisting the few non-test
# outliers (docs/release) is safe — `[project.optional-dependencies]` /
# `extras_require` is the SAME syntax used for genuinely-optional runtime
# features (`cpu`/`gpu`, `postgres`/`mysql`, `all`) that are often mutually
# exclusive or heavy. Defaulting those to "in" would resurrect exactly the bug
# `needed_extras`/`in_scope_extras` was built to prevent (`_in_test_scope`'s
# docstring). So only the names below — which denote test/CI/lint/type-check
# tooling in virtually every real-world manifest, and are never used to select
# between mutually-exclusive backends — are default-included; everything else
# (including `all`/`full`/`complete` bundles, which often DO pull in a heavy or
# conflicting backend) stays gated behind `in_scope_extras` as before.
#
# Verified against the gold recipes this fix targets: `-e ".[ci]"` (feast),
# `-e ".[test]"` (synthetic-data-generator), `-e ".[dev]"` (pretix),
# `-e ".[pytest,dev]"` (DDNS) — every group name in those recipes is covered.
_TEST_SCOPE_EXTRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        "test", "tests", "testing",
        "dev", "develop", "development",
        "ci",
        "lint", "linting",
        "typing", "type-check", "mypy",
        "check", "checks",
        "qa",
        "pytest",
    }
)
