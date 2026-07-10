"""Offline tests for the sufficiency runner: MiniMax guard (D4), brief shape (D1),
deterministic sampling (D5/D6), and a full fake-client end-to-end pass.

ZERO network calls: every completion is served by a canned fake client.
"""
import json

import pytest

from src.eval.service_sufficiency import run
from src.eval.service_sufficiency.brief import render_brief


# ── fakes (no network, no real SDK) ──────────────────────────────────────────

class _StubClient:
    def __init__(self, base_url):
        self.base_url = base_url


class _FakeMessage:
    def __init__(self, content):
        self.content = content
        self.reasoning = None
        self.tool_calls = None
        self.model_extra = {}


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self, text, counter):
        self._text = text
        self._counter = counter

    def create(self, model, messages, **kwargs):
        self._counter.append((model, messages))
        return _FakeResp(self._text)


class _FakeChat:
    def __init__(self, text, counter):
        self.completions = _FakeCompletions(text, counter)


class _FakeClient:
    """OpenAI-compatible stub. base_url deliberately NOT MiniMax."""
    base_url = "https://api.openai.example/v1"

    def __init__(self, text):
        self.calls: list = []
        self.chat = _FakeChat(text, self.calls)


_CANNED = "apt-get install -y redis-server\nredis-server --daemonize yes --port 6379"


# ── D4: MiniMax guard ────────────────────────────────────────────────────────

def test_minimax_client_is_refused():
    with pytest.raises(SystemExit, match="MiniMax"):
        run._assert_not_minimax(_StubClient("https://api.minimaxi.com/v1"))


def test_non_minimax_client_passes_guard():
    assert run._assert_not_minimax(_StubClient("https://api.openai.example/v1")) is None
    assert run._assert_not_minimax(None) is None                # offline/CLI is fine


def test_main_refuses_minimax_before_touching_files(tmp_path):
    # Guard must fire before any node file is opened: a non-existent path still raises
    # the MiniMax refusal, not FileNotFoundError.
    with pytest.raises(SystemExit, match="MiniMax"):
        run.main(str(tmp_path / "nope.jsonl"), str(tmp_path / "out.json"),
                 client=_StubClient("https://api.minimaxi.com/v1"))


# ── D1: the install constraint rides in EVERY condition ──────────────────────

_NODE = {
    "name": "redis", "image": "redis:7", "image_repo": "library/redis",
    "port": 6379, "endpoint": "localhost:6379", "env": {"X": "1"},
    "command": "redis-server", "seed": [],
    "check": {"command": "redis-cli ping", "source": "declared_healthcheck"},
    "raw": {"compose:docker-compose.yml": {"image": "redis:7"}},
    "repo": "acme/app",
}
_CONSTRAINT_MARK = "Do not add third-party apt sources"


def test_constraint_present_in_c0():
    assert _CONSTRAINT_MARK in render_brief(_NODE, "C0")


def test_constraint_present_in_c1():
    assert _CONSTRAINT_MARK in render_brief(_NODE, "C1")


def test_c2_drops_raw_but_keeps_check():
    txt = render_brief(_NODE, "C2")
    assert "Verbatim declaration" not in txt
    assert "redis-cli ping" in txt


def test_c3_drops_check_but_keeps_raw():
    txt = render_brief(_NODE, "C3")
    assert "redis-cli ping" not in txt
    assert "Verbatim declaration" in txt


def test_c0_mentions_the_declared_port():
    assert "6379" in render_brief(_NODE, "C0")


# ── D5/D6: deterministic stratified sampling ─────────────────────────────────

def _mk(name, repo, source, image_repo):
    return {"name": name, "repo": repo, "image_repo": image_repo,
            "check": {"command": None if source == "none" else "x", "source": source},
            "image": f"{image_repo}:1", "port": 1, "endpoint": None,
            "env": {}, "command": None, "seed": [], "raw": {}}


def _corpus():
    nodes = []
    for i in range(20):
        nodes.append(_mk(f"redis{i}", f"o/r{i}", "declared_healthcheck", "library/postgres"))
    for i in range(20):
        nodes.append(_mk(f"ch{i}", f"o/e{i}", "tcp_port", "clickhouse/clickhouse"))
    for i in range(4):
        nodes.append(_mk(f"u{i}", f"o/u{i}", "none", "some/thing"))
    nodes.append(_mk("valkey", "rq/rq", "declared_healthcheck", "valkey/valkey"))
    nodes.append(_mk("valkey", "other/repo", "declared_healthcheck", "valkey/valkey"))
    return nodes


def test_sample_is_deterministic_across_two_runs():
    a = run.sample(_corpus(), per_stratum=13, seed=1234)
    b = run.sample(_corpus(), per_stratum=13, seed=1234)
    assert [(r["repo"], r["name"]) for r in a] == [(r["repo"], r["name"]) for r in b]


def _reference_stratified(nodes, per_stratum, seed):
    """sample()'s stratified core WITHOUT the valkey top-up -- for a differential test."""
    import random as _random
    rng = _random.Random(seed)
    buckets = {}
    for n in nodes:
        buckets.setdefault(run._stratum(n), []).append(n)
    out = []
    for _s, group in sorted(buckets.items()):
        rng.shuffle(group)
        out.extend(group[:per_stratum])
    return out


def test_rq_valkey_is_always_sampled():
    # The top-up guarantees inclusion even when the normal exotic draw misses it.
    for seed in range(12):
        picked = run.sample(_corpus(), per_stratum=13, seed=seed)
        ids = {(r["repo"], r["name"]) for r in picked}
        assert ("rq/rq", "valkey") in ids, seed


def test_topup_only_ever_appends_rq_rq_valkey():
    # A corpus whose only valkey is other/repo (no rq/rq): the top-up must add NOTHING,
    # so sample() is byte-identical to the plain stratified draw. This is the D6 fix --
    # the brief's `name == "valkey"` would have force-appended other/repo's valkey.
    corpus = [n for n in _corpus() if n["repo"] != "rq/rq"]
    picked = run.sample(corpus, per_stratum=13, seed=1234)
    ref = _reference_stratified(corpus, per_stratum=13, seed=1234)
    assert [(r["repo"], r["name"]) for r in picked] == [(r["repo"], r["name"]) for r in ref]


def test_budget_counts_c3_skip_for_unverifiable():
    picked = run.sample(_corpus(), per_stratum=13, seed=1234)
    with_check = sum(1 for n in picked if n["check"]["source"] != "none")
    without = sum(1 for n in picked if n["check"]["source"] == "none")
    assert run.completion_budget(picked) == with_check * 4 + without * 3


# ── full pipeline against a fake client (no network) ─────────────────────────

def _write_corpus(tmp_path):
    nodes = [
        _NODE,
        _mk("clickhouse", "acme/ch", "tcp_port", "clickhouse/clickhouse"),
        _mk("myservice", "acme/uv", "none", "some/thing"),
        # a false positive NOT in the oracle -- must be filtered out (D2)
        _mk("azure-vote-front", "acme/fp", "declared_healthcheck", "some/vote"),
    ]
    nodes_path = tmp_path / "nodes.jsonl"
    with open(nodes_path, "w") as fh:
        for n in nodes:
            fh.write(json.dumps(n) + "\n")
    oracle = {
        "acme/app": {"must_detect": ["redis"]},
        "acme/ch": {"must_detect": ["clickhouse"]},
        "acme/uv": {"must_detect": ["myservice"]},
        "acme/fp": {"must_detect": ["something-else"]},   # azure-vote-front NOT listed
    }
    oracle_path = tmp_path / "oracle.json"
    oracle_path.write_text(json.dumps(oracle))
    return nodes_path, oracle_path


def test_fake_client_end_to_end(tmp_path, capsys):
    nodes_path, oracle_path = _write_corpus(tmp_path)
    out_path = tmp_path / "out.json"
    client = _FakeClient(_CANNED)

    rc = run.main(str(nodes_path), str(out_path), client=client,
                  oracle_path=str(oracle_path), per_stratum=13, seed=1234)
    out = capsys.readouterr().out

    assert rc == 0
    # budget printed BEFORE spending: 2 with-check (redis, clickhouse) * 4 + 1 uv * 3 = 11
    assert "completion budget: 11" in out
    assert "azure-vote-front" not in out                # false positive filtered (D2)
    assert "cond" in out and "policy_viol" in out       # report table rendered

    results = json.loads(out_path.read_text())
    assert len(results) == 11                           # matches the printed budget
    # the fake client actually served every completion (no network)
    assert len(client.calls) == 11
    # canned commands grade as a clean background start, no policy violation
    assert all(r["background_start"] for r in results)
    assert not any(r["policy_violation"] for r in results)
    # unverifiable node has no C3 row
    uv_conds = {r["condition"] for r in results if r["name"] == "myservice"}
    assert uv_conds == {"C0", "C1", "C2"}
