# tests/bench/test_orchestrator.py
import json
from dataclasses import asdict
from bench.schema import HarvestedEnv, RepoSpec, MeasureRow
from bench import unified_bench as ub


def _env(agent="v3", repo="o/r"):
    return HarvestedEnv(agent, RepoSpec(repo, f"https://github.com/{repo}"), "FROM x",
                        base_image="python:3.13-slim", meta={"tokens_in": 1, "tokens_out": 2})


def _fake_measure(env, *, docker, **kw):
    return MeasureRow(agent=env.agent, repo=env.repo.full_name, env_status="ok", build_ok=True,
                      executed=True, ebsr=True, pass_rate=1.0, total=3, passed=3, collect_clean=True)


def test_run_one_writes_row_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(ub, "measure", _fake_measure)
    p1 = ub.run_one(_env(), str(tmp_path), docker=object())
    assert json.load(open(p1))["ebsr"] is True
    monkeypatch.setattr(ub, "measure", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-ran")))
    p2 = ub.run_one(_env(), str(tmp_path), docker=object())
    assert p2 == p1


def test_aggregate_globs_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(ub, "measure", _fake_measure)
    ub.run_one(_env(agent="v3", repo="o/r"), str(tmp_path), docker=object())
    out = ub.aggregate(str(tmp_path))
    assert out["v3"]["n"] == 1 and out["v3"]["EBSR"] == 1.0 and out["v3"]["ESSR_all"] == 1.0
