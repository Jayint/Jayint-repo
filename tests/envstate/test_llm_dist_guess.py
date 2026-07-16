from src.envstate.llm_dist_guess import make_dist_guesser


def test_parses_distributions_from_json():
    def complete_fn(messages):
        return '{"distributions": ["opencv-python"]}'
    guess = make_dist_guesser(complete_fn)
    assert guess("cv2", ("imread",)) == ["opencv-python"]


def test_symbols_are_in_the_prompt():
    captured = {}
    def complete_fn(messages):
        captured["user"] = messages[-1]["content"]
        return '{"distributions": []}'
    make_dist_guesser(complete_fn)("cv2", ("imread", "VideoCapture"))
    assert "imread" in captured["user"] and "VideoCapture" in captured["user"]


def test_cache_avoids_second_call():
    calls = {"n": 0}
    def complete_fn(messages):
        calls["n"] += 1
        return '{"distributions": ["x"]}'
    guess = make_dist_guesser(complete_fn)
    guess("cv2", ("imread",))
    guess("cv2", ("imread",))
    assert calls["n"] == 1


def test_malformed_response_returns_empty():
    guess = make_dist_guesser(lambda m: "not json at all")
    assert guess("cv2", ()) == []
