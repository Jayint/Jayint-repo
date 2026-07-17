"""Unit tests for the Gate A cure-recovery harness — PURE LOGIC ONLY.

No Docker, no git, no network, no LLM: provisioning + rendering + the replay
ladder are monkeypatched. Covers ``_strip_editable`` (the baseline-arm derivation)
and the ``measure`` table/aggregate shape.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

# repo root on path so `scripts.gate_a_cure_recovery` (a namespace-package module)
# resolves; tests/conftest.py already does this, but be explicit for isolation.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.gate_a_cure_recovery as gate  # noqa: E402


# ---------------------------------------------------------------------------
# _strip_editable — the baseline-arm derivation
# ---------------------------------------------------------------------------
def test_strip_editable_removes_the_capstone_line():
    setup = "set -e\npip install numpy\npip install -e .\necho done"
    assert gate._strip_editable(setup) == "set -e\npip install numpy\necho done"


def test_strip_editable_keeps_lines_with_only_one_marker():
    # a bare `pip install foo` (only "pip install") and a comment mentioning
    # "-e ." (only "-e .") must BOTH survive — only lines with BOTH markers go.
    setup = (
        "pip install foo\n"
        "# dev tip: use -e . for an editable install\n"
        "pip install -e .\n"
        "pip install bar"
    )
    out = gate._strip_editable(setup)
    assert "pip install foo" in out
    assert "# dev tip: use -e . for an editable install" in out
    assert "pip install bar" in out
    assert "pip install -e ." not in out


def test_strip_editable_preserves_order():
    setup = "a\npip install -e .\nb\nc"
    assert gate._strip_editable(setup) == "a\nb\nc"


def test_strip_editable_is_a_noop_without_a_capstone():
    setup = "set -e\npip install numpy\napt-get install -y libpq-dev"
    assert gate._strip_editable(setup) == setup


def test_strip_editable_removes_every_capstone_occurrence():
    setup = "pip install -e .\nkeep me\npip install -e . --no-build-isolation"
    assert gate._strip_editable(setup) == "keep me"


# ---------------------------------------------------------------------------
# _aggregate — tri-state (True/False/None) collect-clean counting
# ---------------------------------------------------------------------------
def test_aggregate_counts_only_true_as_collect_clean():
    rows = {
        "a/x": {"configlane": True, "baseline": False},
        "a/y": {"configlane": True, "baseline": None},
        "a/z": {"configlane": False, "baseline": False},
    }
    agg = gate._aggregate(rows)
    assert agg == {
        "total": 3,
        "configlane_collect_clean": 2,
        "baseline_collect_clean": 0,
        "lift": 2,
    }


# ---------------------------------------------------------------------------
# measure — table + aggregate shape, with ALL infra mocked
# ---------------------------------------------------------------------------
# A rendered config-lane script whose ONLY "-e ." occurrence is the capstone, so
# stripping it yields a baseline with no editable marker.
_FAKE_CFG_SCRIPT = "set -e\npip install numpy\npip install -e .\n"


def _fake_ladder(repo_dir, image, setup_script, top_import, **_kw):
    # collect is clean iff the editable capstone is still present -> config-lane
    # True, stripped baseline False. Verifies the two arms are wired to the right
    # scripts (config-lane gets the capstone, baseline gets the stripped copy).
    has_editable = all(m in setup_script for m in gate._EDITABLE_MARKERS)
    return SimpleNamespace(collect_ok=has_editable)


def _write_corpus(tmp_path: Path, names: list[str]) -> str:
    entries = [
        {"full_name": n, "clone_url": f"https://example.test/{n}.git", "commit": "deadbee"}
        for n in names
    ]
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(entries))
    return str(p)


def _patch_infra(monkeypatch, *, render=None, ladder=_fake_ladder):
    """Neutralize every side-effecting boundary: provision (git/network), render
    (run_v3_e2e subprocess), the top-import scan, and the Docker replay ladder."""
    monkeypatch.setattr(gate, "_provision",
                        lambda entry, work_root=gate._WORK_ROOT:
                        work_root / entry["full_name"].replace("/", "__"))
    monkeypatch.setattr(gate, "top_level_import_name", lambda repo_dir: "pkg")
    monkeypatch.setattr(gate, "_render",
                        render or (lambda repo_dir, image, out: _FAKE_CFG_SCRIPT))
    monkeypatch.setattr(gate, "run_replay_ladder", ladder)


def test_measure_table_and_aggregate_shape(tmp_path, monkeypatch):
    _patch_infra(monkeypatch)
    corpus = _write_corpus(tmp_path, ["org/a", "org/b", "org/c"])

    result = gate.measure(corpus, "python:3.11-slim", work_root=tmp_path / "work")

    assert set(result) == {"rows", "errors", "aggregate"}
    assert result["errors"] == {}
    assert result["rows"] == {
        "org/a": {"configlane": True, "baseline": False},
        "org/b": {"configlane": True, "baseline": False},
        "org/c": {"configlane": True, "baseline": False},
    }
    assert result["aggregate"] == {
        "total": 3,
        "configlane_collect_clean": 3,
        "baseline_collect_clean": 0,
        "lift": 3,
    }


def test_measure_records_errors_without_silent_caps(tmp_path, monkeypatch):
    def _render_but_one_fails(repo_dir, image, out):
        if "bad" in str(repo_dir):
            raise RuntimeError("render blew up")
        return _FAKE_CFG_SCRIPT

    _patch_infra(monkeypatch, render=_render_but_one_fails)
    corpus = _write_corpus(tmp_path, ["org/good", "org/bad"])

    result = gate.measure(corpus, "python:3.11-slim", work_root=tmp_path / "work")

    # the good repo still measured; the bad one is surfaced, not dropped silently
    assert "org/good" in result["rows"]
    assert "org/bad" not in result["rows"]
    assert "org/bad" in result["errors"]
    assert "render blew up" in result["errors"]["org/bad"]
    assert result["aggregate"]["total"] == 1


def test_measure_passes_top_import_positionally_to_the_ladder(tmp_path, monkeypatch):
    # Guards the plan-sketch fix: run_replay_ladder's real signature REQUIRES a
    # 4th positional `top_import`; assert the harness actually passes it.
    seen = []

    def _recording_ladder(repo_dir, image, setup_script, top_import, **_kw):
        seen.append(top_import)
        return SimpleNamespace(collect_ok=True)

    _patch_infra(monkeypatch, ladder=_recording_ladder)
    corpus = _write_corpus(tmp_path, ["org/a"])

    gate.measure(corpus, "img", work_root=tmp_path / "work")
    assert seen == ["pkg", "pkg"]  # both arms receive the resolved top-import


# ---------------------------------------------------------------------------
# corpus loading — both supported shapes
# ---------------------------------------------------------------------------
def test_load_entries_accepts_list_shape(tmp_path):
    p = tmp_path / "list.json"
    p.write_text(json.dumps([{"full_name": "a/b"}]))
    assert gate._load_entries(str(p)) == [{"full_name": "a/b"}]


def test_load_entries_accepts_provenance_repos_shape(tmp_path):
    p = tmp_path / "rat.json"
    p.write_text(json.dumps({
        "_provenance": {"note": "ignore me"},
        "repos": [{"full_name": "a/b"}, {"full_name": "c/d"}],
    }))
    assert gate._load_entries(str(p)) == [{"full_name": "a/b"}, {"full_name": "c/d"}]
