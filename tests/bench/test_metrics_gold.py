# tests/bench/test_metrics_gold.py
from bench.schema import MeasureRow
from bench.metrics import compute_metrics


def _row(repo, passed_ids, **kw):
    base = dict(agent="a", repo=repo, env_status="ok", build_ok=True, executed=True,
                ebsr=True, passed_node_ids=tuple(passed_ids))
    base.update(kw)
    return MeasureRow(**base)


def test_gold_essr_scores_intersection_over_fixed_denominator():
    gold = {"r1": ["t::a", "t::b", "t::c", "t::d"], "r2": ["t::x", "t::y"]}
    rows = [_row("r1", ["t::a", "t::b"]), _row("r2", ["t::x", "t::y", "t::z"])]
    m = compute_metrics(rows, gold=gold)
    assert m["n_gold"] == 2 and m["gold_ESSR"] == round((0.5 + 1.0) / 2, 4)


def test_gold_absent_repo_excluded():
    gold = {"r1": ["t::a", "t::b"]}
    rows = [_row("r1", ["t::a"]), _row("r99", ["t::q"])]
    m = compute_metrics(rows, gold=gold)
    assert m["n_gold"] == 1 and m["gold_ESSR"] == 0.5


def test_no_gold_arg_omits_gold_keys():
    m = compute_metrics([_row("r1", ["t::a"])])
    assert "gold_ESSR" not in m
