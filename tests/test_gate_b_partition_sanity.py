"""Unit tests for the Gate B partition-sanity harness — PURE LOGIC ONLY.

No Docker, no git, no network, no LLM: only ``aggregate_shadow_records`` (the pure
reduction of parsed shadow records) and the ``_read_shadow_jsonl`` file boundary are
exercised, on hand-built dicts / a tmp file. The construction path (``build_dep_graph``
+ ``DockerExecutor``) is never touched here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# repo root on path so `scripts.gate_b_partition_sanity` (a namespace-package module)
# resolves; tests/conftest.py already does this, but be explicit for isolation.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.gate_b_partition_sanity as gate  # noqa: E402


# ---------------------------------------------------------------------------
# Fabricated shadow records (each a ShadowRecord.__dict__; tuples as JSON lists)
# ---------------------------------------------------------------------------
def _rec(repo, *, n_internal=0, n_external=0, n_deferred=0,
         cure_ok=False, collect_ok=False, cure_rung="none",
         fallthrough=(), provisional_flags=()):
    return {
        "repo": repo,
        "n_internal": n_internal,
        "n_external": n_external,
        "n_deferred": n_deferred,
        "cure_ok": cure_ok,
        "cure_rung": cure_rung,
        "collect_ok": collect_ok,
        "resolves_local": [],
        "fallthrough": list(fallthrough),
        "unresolved": [],
        "provisional_flags": list(provisional_flags),
    }


# a clean pass, a provisional/fallthrough pass, a cure-failure, and one ERRORED repo
_R_CLEAN = _rec("work/org__a", n_internal=3, n_external=5, n_deferred=1,
                cure_ok=True, collect_ok=True)
_R_PROVISIONAL = _rec("work/org__b", n_internal=2, n_external=4, n_deferred=2,
                      cure_ok=True, collect_ok=True,
                      fallthrough=("items",), provisional_flags=("items",))
_R_CURE_FAILED = _rec("work/org__c", n_internal=1, n_external=2, n_deferred=0,
                      cure_ok=False, collect_ok=False)
_R_ERRORED = {"repo": "org/err", "error": "RuntimeError: docker down"}

_RECORDS = [_R_CLEAN, _R_PROVISIONAL, _R_CURE_FAILED, _R_ERRORED]


# ---------------------------------------------------------------------------
# aggregate_shadow_records — the pure reduction
# ---------------------------------------------------------------------------
def test_aggregate_partition_distribution():
    agg = gate.aggregate_shadow_records(_RECORDS)
    # errored repo is EXCLUDED from the numeric aggregate -> total counts OK repos
    assert agg["total"] == 3
    assert agg["total_internal"] == 6   # 3 + 2 + 1
    assert agg["total_external"] == 11  # 5 + 4 + 2
    assert agg["total_deferred"] == 3   # 1 + 2 + 0
    assert agg["mean_internal"] == pytest.approx(2.0)
    assert agg["mean_external"] == pytest.approx(11 / 3)
    assert agg["mean_deferred"] == pytest.approx(1.0)


def test_aggregate_collision_zone_frequency():
    agg = gate.aggregate_shadow_records(_RECORDS)
    assert agg["repos_with_collision"] == 2          # a (1) and b (2) have deferred > 0
    assert agg["collision_zone_fraction"] == pytest.approx(3 / 20)  # 3 deferred / 20 total


def test_aggregate_cure_recovery_rate():
    agg = gate.aggregate_shadow_records(_RECORDS)
    assert agg["n_cure_ok"] == 2
    assert agg["n_collect_ok"] == 2
    assert agg["n_cure_and_collect"] == 2
    assert agg["cure_recovery_rate"] == pytest.approx(1.0)  # cure_ok / collect_ok


def test_aggregate_fallthrough_and_provisional_counts():
    agg = gate.aggregate_shadow_records(_RECORDS)
    assert agg["total_fallthrough"] == 1
    assert agg["repos_with_fallthrough"] == 1
    assert agg["total_provisional_flags"] == 1
    assert agg["n_provisional_repos"] == 1
    assert agg["provisional_flag_rate"] == pytest.approx(1 / 3)  # 1 flagged / 3 OK repos


def test_aggregate_lists_errored_without_silent_caps():
    agg = gate.aggregate_shadow_records(_RECORDS)
    assert agg["n_errored"] == 1
    assert agg["errored"] == [{"repo": "org/err", "error": "RuntimeError: docker down"}]


def test_aggregate_errored_sorted_and_excluded_from_numerics():
    records = [
        _R_CLEAN,
        {"repo": "z/last", "error": "boom-z"},
        {"repo": "a/first", "error": "boom-a"},
    ]
    agg = gate.aggregate_shadow_records(records)
    assert agg["total"] == 1                       # only the one OK record counts
    assert [e["repo"] for e in agg["errored"]] == ["a/first", "z/last"]  # sorted


def test_aggregate_empty_input_is_all_zero_no_div_error():
    agg = gate.aggregate_shadow_records([])
    assert agg["total"] == 0
    assert agg["n_errored"] == 0
    # every ratio guards div-by-zero -> 0.0, never raises
    for key in ("mean_internal", "mean_external", "mean_deferred",
                "collision_zone_fraction", "cure_recovery_rate", "provisional_flag_rate"):
        assert agg[key] == 0.0


def test_aggregate_tolerates_missing_keys():
    # a malformed record missing every field must default safely, never crash
    agg = gate.aggregate_shadow_records([{"repo": "org/sparse"}])
    assert agg["total"] == 1
    assert agg["total_internal"] == 0
    assert agg["total_fallthrough"] == 0
    assert agg["n_cure_ok"] == 0
    assert agg["repos_with_collision"] == 0


# ---------------------------------------------------------------------------
# _read_shadow_jsonl — the I/O boundary (tmp file only, no infra)
# ---------------------------------------------------------------------------
def test_read_shadow_jsonl_parses_skips_blank_and_malformed(tmp_path, capsys):
    p = tmp_path / "shadow.jsonl"
    p.write_text(
        json.dumps(_R_CLEAN) + "\n"
        "\n"                       # blank line skipped
        "{ not json }\n"           # malformed line skipped (surfaced on stderr)
        + json.dumps(_R_PROVISIONAL) + "\n"
    )
    records = gate._read_shadow_jsonl(str(p))
    assert [r["repo"] for r in records] == ["work/org__a", "work/org__b"]
    assert "SKIP malformed JSONL line 3" in capsys.readouterr().err


def test_read_shadow_jsonl_missing_file_returns_empty(tmp_path):
    records = gate._read_shadow_jsonl(str(tmp_path / "nope.jsonl"))
    assert records == []


# ---------------------------------------------------------------------------
# aggregate_run — combines JSONL records + errored dict (I/O composed with pure)
# ---------------------------------------------------------------------------
def test_aggregate_run_combines_records_and_errored(tmp_path):
    p = tmp_path / "shadow.jsonl"
    p.write_text(json.dumps(_R_CLEAN) + "\n" + json.dumps(_R_PROVISIONAL) + "\n")
    agg = gate.aggregate_run(str(p), {"org/broke": "RuntimeError: nope"})
    assert agg["total"] == 2
    assert agg["n_errored"] == 1
    assert agg["errored"] == [{"repo": "org/broke", "error": "RuntimeError: nope"}]
