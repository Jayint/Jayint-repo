"""Reconciliation tests amending the diagnosis-router companion plan
(.superpowers/sdd/task-3-brief.md):

  2. PEP-503-normalize ``RepoContext.invalid_names`` comparisons.
  4. Conservative ``import_name_error`` routing (weaker evidence than
     ``module_not_found`` — only trust the package guess when the failed
     "from" target is itself the bare top-level name; a dotted/nested
     "from" stays AMBIGUOUS rather than minting a bogus package node).
  5. ``diagnose_all`` — pure batch helper for Phase 6's orchestrator routing.

Pure, no Docker.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.diagnose import (
    Diagnosis, Mode, RepoContext, _norm, diagnose, diagnose_all,
)
from python_deps.depgraph.schema import NodeType


# ---------------------------------------------------------------------------
# Reconciliation 2: normalized invalid_names comparison (PEP 503)
# ---------------------------------------------------------------------------

def test_norm_helper_lowercases_strips_and_dashes_underscores():
    assert _norm("Frobnicate_9000") == "frobnicate-9000"
    assert _norm("  Already-Dashed  ") == "already-dashed"
    assert _norm(None) == ""  # type: ignore[arg-type]
    assert _norm("") == ""


def test_previously_invalid_name_matches_normalized_form():
    # ctx stores the pre-normalized form; the traceback's raw casing/underscores
    # must still match via _norm on both sides.
    ctx = RepoContext(invalid_names=frozenset({"frobnicate-9000"}))
    d = diagnose("python app.py",
                 "ModuleNotFoundError: No module named 'Frobnicate_9000'",
                 ctx)
    assert d.mode is Mode.INVALID_ATTEMPT
    assert d.discovery is None


# ---------------------------------------------------------------------------
# Reconciliation 4: conservative import_name_error
# ---------------------------------------------------------------------------

def test_import_name_error_dotted_from_routes_ambiguous():
    # "cannot import name 'helper' from 'mypkg.utils'" — the failed reference is
    # a nested submodule, not a bare top-level name. classify_observation would
    # guess a package named after the top-level segment ("mypkg") with no real
    # evidence it's the missing distribution (Y may be installed-but-outdated,
    # mid circular-import, etc). Stay AMBIGUOUS rather than mint that guess.
    d = diagnose("python app.py",
                 "ImportError: cannot import name 'helper' from 'mypkg.utils'",
                 RepoContext())
    assert d.mode is Mode.AMBIGUOUS
    assert d.discovery is None


def test_import_name_error_flat_from_routes_environment():
    # A bare top-level "from" target is exactly what classify_observation
    # mapped -> trust it (module_not_found keeps its existing handling; this
    # confirms the flat import_name_error case is not regressed to AMBIGUOUS).
    d = diagnose("python app.py",
                 "ImportError: cannot import name 'get' from 'requests'",
                 RepoContext())
    assert d.mode is Mode.ENVIRONMENT
    assert d.discovery is not None
    assert d.discovery.node_type is NodeType.PACKAGE


def test_import_name_error_local_dotted_from_routes_repo_internal_ref():
    ctx = RepoContext(local_names=frozenset({"mypkg"}))
    d = diagnose("python app.py",
                 "ImportError: cannot import name 'helper' from 'mypkg.utils'",
                 ctx)
    assert d.mode is Mode.REPO_INTERNAL_REF
    assert d.discovery is None


# ---------------------------------------------------------------------------
# Reconciliation 5: diagnose_all
# ---------------------------------------------------------------------------

def test_diagnose_all_maps_each_observation_in_order():
    obs = (
        ("python app.py", "ModuleNotFoundError: No module named 'requests'"),
        ("python -m pytest -q", "E       assert 1 == 2\nAssertionError"),
    )
    results = diagnose_all(obs, RepoContext())
    assert isinstance(results, tuple)
    assert len(results) == 2
    assert all(isinstance(r, Diagnosis) for r in results)
    assert results[0].mode is Mode.ENVIRONMENT
    assert results[1].mode is Mode.RESIDUAL


def test_diagnose_all_empty_observations_returns_empty_tuple():
    assert diagnose_all((), RepoContext()) == ()
