from src.bench_emit.meta import bench_meta


def test_agent_always_present():
    assert bench_meta("v3") == {"agent": "v3"}


def test_none_valued_keys_dropped():
    m = bench_meta("rat", base_image="python:3.10-slim", tokens_in=None, produce_s=12.5)
    assert m == {"agent": "rat", "base_image": "python:3.10-slim", "produce_s": 12.5}
    assert "tokens_in" not in m


def test_zero_is_a_real_value_not_dropped():
    # unknown -> None -> omitted; a genuine 0 read from data is kept (never fabricated)
    m = bench_meta("rat", tokens_in=0, tokens_out=5)
    assert m["tokens_in"] == 0 and m["tokens_out"] == 5


def test_all_known_keys_map():
    m = bench_meta("v3", base_image="b", tokens_in=1, tokens_out=2, produce_s=3.0,
                   head_sha="abc", commit="def", llm_calls=4, turns_used=5,
                   dockerfile_source="v3_eval_build")
    assert m == {"agent": "v3", "base_image": "b", "tokens_in": 1, "tokens_out": 2,
                 "llm_calls": 4, "turns_used": 5, "produce_s": 3.0, "head_sha": "abc",
                 "commit": "def", "dockerfile_source": "v3_eval_build"}
