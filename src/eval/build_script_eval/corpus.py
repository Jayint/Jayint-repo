"""Committed corpus manifest for the e2e build-script eval. No held-out recipe
is required (there is no oracle) — the only membership rule is a runnable test
suite, and S_syslib rows are chosen so their tests import the native extension
(a missing .so then surfaces at the import/collect rung, service-independent).
"""
from __future__ import annotations

from dataclasses import dataclass

STRATA: frozenset[str] = frozenset({"S_control", "S_syslib"})


@dataclass(frozen=True)
class RepoSpec:
    name: str                    # unique dir name under the _smoke root
    full_name: str               # "org/repo" (display / output id)
    git_url: str
    ref: str                     # pinned tag or sha (see fetch.py; verify with git ls-remote)
    stratum: str                 # one of STRATA
    top_import: str | None = None      # import-check override; else derived
    feasible: bool = True              # False ⇒ excluded from the headline denominator
    network_in_tests: bool = False     # True ⇒ keep network during the pytest rung


# Starter corpus. Refs are concrete tags; Task 5 verifies each with `git ls-remote`
# before the first fetch and pins to a sha if desired. Keep S_control apt-empty
# (over-prediction baseline) and S_syslib native-ext-importing.
CORPUS: tuple[RepoSpec, ...] = (
    # --- S_control: pure-Python, no apt expected ---
    RepoSpec("typer", "fastapi/typer",
             "https://github.com/fastapi/typer", "0.12.5", "S_control", top_import="typer"),
    RepoSpec("python-semantic-release", "python-semantic-release/python-semantic-release",
             "https://github.com/python-semantic-release/python-semantic-release",
             "v9.8.6", "S_control", top_import="semantic_release"),
    # --- S_syslib: source-form native deps; tests import the extension ---
    RepoSpec("psycopg2", "psycopg/psycopg2",
             "https://github.com/psycopg/psycopg2", "2.9.9", "S_syslib", top_import="psycopg2"),
    RepoSpec("pygraphviz", "pygraphviz/pygraphviz",
             "https://github.com/pygraphviz/pygraphviz", "pygraphviz-1.12", "S_syslib",
             top_import="pygraphviz"),
    RepoSpec("lxml", "lxml/lxml",
             "https://github.com/lxml/lxml", "lxml-5.2.2", "S_syslib", top_import="lxml"),
)


def select(only: frozenset[str] = frozenset(),
           strata: frozenset[str] = frozenset()) -> list[RepoSpec]:
    """Filter CORPUS by repo name (`only`) and/or stratum (`strata`). Empty set =
    no filter on that axis. Raises ValueError on an unknown stratum or an unknown
    name (fail-fast on a typo)."""
    if strata - STRATA:
        raise ValueError(f"unknown stratum(s): {sorted(strata - STRATA)}; valid={sorted(STRATA)}")
    names = {r.name for r in CORPUS}
    if only - names:
        raise ValueError(f"unknown repo name(s): {sorted(only - names)}")
    return [r for r in CORPUS
            if (not only or r.name in only) and (not strata or r.stratum in strata)]
