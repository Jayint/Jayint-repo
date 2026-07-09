"""Unit test for the detection-fidelity scorer itself.

The 50 corpus repos live on the VM, so the scorer cannot be exercised against the
real corpus from here. That is exactly why its arithmetic needs its own test: a
precision/recall bug would otherwise surface for the first time on real data as a
confident, wrong headline number. We drive the *real* ``build_service_nodes`` over
tiny synthetic repos (Docker-free, LLM-free — it is pure file parsing) so the only
thing under test is the scorer's counting.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import eval_service_detection_fidelity as scorer  # noqa: E402

_COMPOSE = """\
services:
  db:
    image: postgres:16
    ports: ["5432:5432"]
  cache:
    image: redis:7
    ports: ["6379:6379"]
"""


def _make_repo(root: Path, owner: str, name: str, compose: str | None) -> None:
    """Create ``<root>/<owner>/<name>``; write a compose file iff ``compose``."""
    rd = root / owner / name
    rd.mkdir(parents=True)
    if compose is not None:
        (rd / "docker-compose.yml").write_text(compose)


def test_perfect_match_scores_one(tmp_path: Path) -> None:
    _make_repo(tmp_path, "acme", "app", _COMPOSE)   # detects {db, cache}
    _make_repo(tmp_path, "acme", "bare", None)       # detects {} — a scored empty
    oracle = {"acme/app": ["db", "cache"], "acme/bare": []}

    r = scorer.score(str(tmp_path), oracle)

    assert (r.precision, r.recall, r.f1) == (1.0, 1.0, 1.0)
    assert (r.tp, r.fp, r.fn) == (2, 0, 0)


def test_one_extra_detected_lowers_precision_only(tmp_path: Path) -> None:
    _make_repo(tmp_path, "acme", "app", _COMPOSE)   # detects {db, cache}
    oracle = {"acme/app": ["db"]}                    # cache is an unlisted false positive

    r = scorer.score(str(tmp_path), oracle)

    assert r.recall == 1.0                            # nothing declared was missed
    assert r.precision < 1.0                          # the extra detection costs precision
    assert (r.tp, r.fp, r.fn) == (1, 1, 0)


def test_one_missed_service_lowers_recall_only(tmp_path: Path) -> None:
    _make_repo(tmp_path, "acme", "app", _COMPOSE)   # detects {db, cache}
    oracle = {"acme/app": ["db", "cache", "queue"]}  # queue is declared-but-undetected

    r = scorer.score(str(tmp_path), oracle)

    assert r.precision == 1.0                         # nothing extra was detected
    assert r.recall < 1.0                             # the miss costs recall
    assert (r.tp, r.fp, r.fn) == (2, 0, 1)


def test_absent_repo_directory_raises_system_exit(tmp_path: Path) -> None:
    # No directory is created for acme/ghost: a missing checkout must be a loud
    # failure, never silently scored as "detected nothing" (== total recall miss).
    oracle = {"acme/ghost": ["db"]}

    with pytest.raises(SystemExit) as exc:
        scorer.score(str(tmp_path), oracle)

    assert "acme/ghost" in str(exc.value)
