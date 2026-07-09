"""Unit test for the detection-fidelity scorer itself.

The 50 corpus repos live on the VM, so the scorer cannot be exercised against the
real corpus from here. That is exactly why its arithmetic needs its own test: a
precision/recall bug would otherwise surface for the first time on real data as a
confident, wrong headline number.

`score(detected, oracle)` is pure — it takes the already-computed detection sets — so
the split-pooling arithmetic is tested directly from dicts. `main()` (which walks the
filesystem and calls `build_service_nodes`) is exercised over tiny synthetic repos for
its fail-loud paths and threshold exit code.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import eval_service_detection_fidelity as scorer  # noqa: E402


def _complete(names: list[str]) -> dict:
    return {"must_detect": names, "complete": True}


def _partial(names: list[str]) -> dict:
    return {"must_detect": names, "complete": False}


# --------------------------------------------------------------------------- #
# score(): pure split-pooling arithmetic                                       #
# --------------------------------------------------------------------------- #

def test_perfect_match_on_complete_repos_scores_one() -> None:
    detected = {"o/a": {"db", "cache"}, "o/empty": set()}
    oracle = {"o/a": _complete(["db", "cache"]), "o/empty": _complete([])}

    r = scorer.score(detected, oracle)

    assert (r["precision"], r["recall"], r["f1"]) == (1.0, 1.0, 1.0)
    assert (r["tp"], r["fp"], r["fn"]) == (2, 0, 0)


def test_extra_detection_in_complete_repo_lowers_precision_only() -> None:
    detected = {"o/a": {"db", "cache"}}          # cache is unlisted
    oracle = {"o/a": _complete(["db"])}

    r = scorer.score(detected, oracle)

    assert r["recall"] == 1.0                     # nothing declared was missed
    assert r["precision"] == 0.5                   # the extra costs precision (1/2)
    assert (r["tp"], r["fp"], r["fn"]) == (1, 1, 0)


def test_missed_service_lowers_recall_only() -> None:
    detected = {"o/a": {"db", "cache"}}
    oracle = {"o/a": _complete(["db", "cache", "queue"])}   # queue undetected

    r = scorer.score(detected, oracle)

    assert r["precision"] == 1.0                   # nothing extra detected
    assert r["recall"] == pytest.approx(2 / 3)     # the miss costs recall
    assert (r["tp"], r["fp"], r["fn"]) == (2, 0, 1)


def test_extra_in_partial_repo_does_not_lower_precision() -> None:
    # The whole point of `complete: false`: an extra detection in a partial repo is not
    # counted against precision, because its catalog list is a known-true subset.
    detected = {"o/c": {"a"}, "o/p": {"b", "spurious"}}
    oracle = {"o/c": _complete(["a"]), "o/p": _partial(["b"])}

    r = scorer.score(detected, oracle)

    assert r["precision"] == 1.0                   # `spurious` never reaches fp_complete
    assert r["fp_complete"] == 0
    assert r["recall"] == 1.0                       # a and b both found


def test_missing_in_partial_repo_still_lowers_recall() -> None:
    # ...but a *missing* known-true positive in that same partial repo MUST lower recall.
    detected = {"o/p": {"b"}}
    oracle = {"o/p": _partial(["b", "c"])}          # c is a known-true miss

    r = scorer.score(detected, oracle)

    assert r["recall"] == 0.5                        # tp=1, fn=1
    assert r["precision"] is None                    # no `complete` repo to pool precision


def test_recall_is_micro_pooled_not_macro_averaged() -> None:
    # Two repos: one 1/1 correct, one 1/3 correct. Micro pooling -> 2/4 = 0.5.
    # A macro (per-repo mean) bug would report mean(1.0, 0.333) = 0.667.
    detected = {"o/full": {"x"}, "o/third": {"a"}}
    oracle = {"o/full": _complete(["x"]), "o/third": _complete(["a", "b", "c"])}

    r = scorer.score(detected, oracle)

    assert r["recall"] == 0.5
    assert r["recall"] != pytest.approx((1.0 + 1 / 3) / 2)


def test_all_empty_oracle_yields_na_not_zero() -> None:
    # No positives anywhere: reporting 0.000 would read as a real bad result.
    detected = {"o/e": set()}
    oracle = {"o/e": _complete([])}

    r = scorer.score(detected, oracle)

    assert r["precision"] is None
    assert r["recall"] is None
    assert r["f1"] is None
    assert scorer._fmt(None) == "  n/a"


# --------------------------------------------------------------------------- #
# main(): filesystem walk, fail-loud paths, threshold exit code               #
# --------------------------------------------------------------------------- #

_COMPOSE = """\
services:
  db:
    image: postgres:16
    ports: ["5432:5432"]
  cache:
    image: redis:7
    ports: ["6379:6379"]
"""


def _repo(root: Path, owner: str, name: str, compose: str | None) -> None:
    rd = root / owner / name
    rd.mkdir(parents=True)
    if compose is not None:
        (rd / "docker-compose.yml").write_text(compose)


def _write_oracle(tmp_path: Path, oracle: dict) -> str:
    p = tmp_path / "oracle.json"
    p.write_text(json.dumps(oracle))
    return str(p)


def test_main_passes_and_returns_zero_when_above_thresholds(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    _repo(repos, "acme", "app", _COMPOSE)            # detects {db, cache}
    oracle_path = _write_oracle(tmp_path, {"acme/app": _complete(["db", "cache"])})

    assert scorer.main(str(repos), oracle_path) == 0


def test_main_returns_nonzero_when_recall_below_bar(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    _repo(repos, "acme", "app", _COMPOSE)            # detects {db, cache}
    # ghost is declared-but-undetected -> recall 2/3 < 0.90.
    oracle_path = _write_oracle(tmp_path, {"acme/app": _complete(["db", "cache", "ghost"])})

    assert scorer.main(str(repos), oracle_path) == 1


def test_main_returns_nonzero_when_precision_below_bar(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    _repo(repos, "acme", "app", _COMPOSE)            # detects {db, cache}
    # cache is an unlisted extra in a complete repo -> precision 1/2 < 0.80.
    oracle_path = _write_oracle(tmp_path, {"acme/app": _complete(["db"])})

    assert scorer.main(str(repos), oracle_path) == 1


def test_main_absent_repo_directory_raises_system_exit(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    repos.mkdir()
    oracle_path = _write_oracle(tmp_path, {"acme/ghost": _complete(["db"])})

    with pytest.raises(SystemExit) as exc:
        scorer.main(str(repos), oracle_path)
    assert "acme/ghost" in str(exc.value)


def test_main_malformed_key_without_slash_raises_system_exit(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    repos.mkdir()
    oracle_path = _write_oracle(tmp_path, {"noslash": _complete([])})

    with pytest.raises(SystemExit) as exc:
        scorer.main(str(repos), oracle_path)
    assert "malformed oracle key" in str(exc.value)
    assert "noslash" in str(exc.value)
