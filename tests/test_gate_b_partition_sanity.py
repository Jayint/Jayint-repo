"""Unit tests for the Gate B partition-sanity harness — PURE LOGIC ONLY.

No Docker, no git, no network, no LLM: only ``aggregate_shadow_records`` (the pure
reduction of parsed shadow records) and the ``_read_records_since`` offset-read file
boundary are exercised, on hand-built dicts / a tmp file. The construction path
(``build_dep_graph`` + ``DockerExecutor``) is never touched here.
"""
from __future__ import annotations

import json
import os
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
# _read_records_since — the offset-read I/O boundary (tmp file only, no infra)
# ---------------------------------------------------------------------------
def test_read_records_since_offset_zero_parses_skips_blank_and_malformed(tmp_path, capsys):
    # offset 0 reads the whole file: blank + malformed lines skipped, rest parsed
    p = tmp_path / "shadow.jsonl"
    p.write_text(
        json.dumps(_R_CLEAN) + "\n"
        "\n"                       # blank line skipped
        "{ not json }\n"           # malformed line skipped (surfaced on stderr)
        + json.dumps(_R_PROVISIONAL) + "\n"
    )
    records = gate._read_records_since(str(p), 0)
    assert [r["repo"] for r in records] == ["work/org__a", "work/org__b"]
    assert "SKIP malformed JSONL line 3" in capsys.readouterr().err


def test_read_records_since_missing_file_returns_empty(tmp_path):
    records = gate._read_records_since(str(tmp_path / "nope.jsonl"), 0)
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


# ---------------------------------------------------------------------------
# _make_scratch_executor — the cure container MUST bind-mount the repo (P1)
# ---------------------------------------------------------------------------
def test_make_scratch_executor_mounts_repo_with_install_kwargs(monkeypatch):
    """The scratch container the shadow pass cures inside MUST mount the provisioned
    repo (the cure ``cd``s into ``repo_mount_dir`` and runs ``pip install -e .``),
    with network + cache so a real editable install genuinely resolves deps.

    Regression guard: if a refactor drops ``mount_repo`` the cure would run against
    an empty ``/workspace/repo`` and silently record ``cure_ok=False`` — this test
    fails (KeyError/assert) the moment any of these kwargs go missing."""
    captured: dict = {}

    class _FakeExecutor:  # constructing it starts NO container — pure capture
        def __init__(self, image, **kwargs):
            captured["image"] = image
            captured.update(kwargs)

    monkeypatch.setattr(gate, "DockerExecutor", _FakeExecutor)

    repo_dir = "/work/org__repo"
    ex = gate._make_scratch_executor(repo_dir, "python:3.11-arm64")

    assert isinstance(ex, _FakeExecutor)
    assert captured["image"] == "python:3.11-arm64"
    assert captured["mount_repo"] == repo_dir     # THE must-fix: repo is mounted
    assert captured["network"] is True            # pip install -e . needs PyPI
    assert captured["cache_volumes"] is True       # reuse pip/uv cache volumes
    assert captured["bootstrap_uv"] is True        # uv for the closure/cure install


# ---------------------------------------------------------------------------
# _read_records_since — each run aggregates only ITS OWN records, and NOTHING is
# ever truncated/erased (P2 fixed by offset; destructive-file-risk fixed by read-only)
# ---------------------------------------------------------------------------
def test_read_records_since_excludes_stale_and_leaves_file_intact(tmp_path):
    """A prior run's records must not survive into a re-run's aggregate — AND the
    harness must never erase the record file. The driver captures the file's byte
    size BEFORE construction; the shadow pass then APPENDS this run's records; the
    driver reads only from that offset to EOF.

    Regression guard for BOTH failure modes: the stale prior-run line below is
    excluded from what is read (offset skips it), while the file on disk still
    CONTAINS that stale line (read-only — nothing is truncated or overwritten)."""
    p = tmp_path / "shadow.jsonl"
    stale_line = json.dumps(_R_CLEAN) + "\n"
    p.write_text(stale_line)                           # a stale prior-run record

    # snapshot taken BEFORE this run appends (mirrors main()'s pre-construction capture)
    start_offset = os.path.getsize(str(p))

    # ...this run APPENDS two fresh records (mirrors the shadow pass opening in "a")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_R_PROVISIONAL) + "\n")
        fh.write(json.dumps(_R_CURE_FAILED) + "\n")

    fresh = gate._read_records_since(str(p), start_offset)

    # ONLY the two fresh records are returned — the stale prior-run line is excluded
    assert [r["repo"] for r in fresh] == ["work/org__b", "work/org__c"]
    # NON-DESTRUCTIVE: the file on disk is untouched — the stale line still survives
    assert p.read_text().startswith(stale_line)
    assert json.dumps(_R_CLEAN) in p.read_text()


def test_read_records_since_offset_zero_reads_this_run_records_for_new_file(tmp_path):
    """When the record path does NOT pre-exist, main() computes ``start_offset`` as
    ``os.path.getsize(path) if os.path.exists(path) else 0`` -> 0. The file is then
    created during the run (the shadow pass appends), and reading from offset 0 must
    return exactly this run's records — no truncation was ever needed."""
    p = tmp_path / "fresh.jsonl"
    assert not p.exists()                              # path does not pre-exist

    # main()'s offset capture on a non-existent path -> 0
    start_offset = os.path.getsize(str(p)) if os.path.exists(str(p)) else 0
    assert start_offset == 0

    # the run creates the file and appends its records
    p.write_text(json.dumps(_R_CLEAN) + "\n" + json.dumps(_R_PROVISIONAL) + "\n")

    records = gate._read_records_since(str(p), start_offset)
    assert [r["repo"] for r in records] == ["work/org__a", "work/org__b"]
