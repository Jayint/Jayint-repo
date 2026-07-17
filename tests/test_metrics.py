# tests/test_metrics.py
def test_provisional_flag_downgrades_the_clean_bucket():
    from bench.schema import MeasureRow
    from bench.metrics import compute_metrics
    rows = [
        MeasureRow(agent="v3", repo="a", env_status="ok", build_ok=True, collect_clean=True),
        MeasureRow(agent="v3", repo="b", env_status="ok", build_ok=True, collect_clean=True,
                   provisional_flags=("items",)),
    ]
    m = compute_metrics(rows)
    assert m["certified_with_provisional"] == 1
    assert m["EBSR_clean"] == 0.5     # only 'a' is a clean pass; 'b' is provisional
    assert m["EBSR"] == 1.0           # raw EBSR unchanged (both are collect-clean)
