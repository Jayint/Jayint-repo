import argparse
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

import src.manifest_builder.__main__ as M
from src.manifest_builder.collect import Docker
from src.manifest_builder.workspace import repo_slug


# ---- shard parsing ----

def test_parse_shard_valid():
    assert M._parse_shard("1/3") == (1, 3)
    assert M._parse_shard("3/3") == (3, 3)
    assert M._parse_shard("1/1") == (1, 1)


@pytest.mark.parametrize("bad", ["0/3", "4/3", "1/0", "-1/2", "abc", "1", "1/2/3", "", "a/2", "1/b"])
def test_parse_shard_invalid_raises(bad):
    with pytest.raises(ValueError):
        M._parse_shard(bad)


# ---- shard selection: round-robin, disjoint + complete ----

def test_select_shard_partitions_disjoint_and_complete():
    repos = [{"id": i} for i in range(10)]
    n = 3
    shards = [M._select_shard(repos, f"{i}/{n}") for i in range(1, n + 1)]
    seen = [r["id"] for s in shards for r in s]
    assert sorted(seen) == list(range(10))          # every repo covered exactly once
    sizes = sorted(len(s) for s in shards)
    assert sizes[-1] - sizes[0] <= 1                # round-robin keeps shards balanced


def test_select_shard_single_is_identity():
    repos = [{"id": i} for i in range(5)]
    assert M._select_shard(repos, "1/1") == repos


def test_select_shard_more_shards_than_repos():
    repos = [{"id": 0}, {"id": 1}]
    assert M._select_shard(repos, "3/4") == []      # empty shard is valid, not an error
    assert M._select_shard(repos, "1/4") == [{"id": 0}]


# ---- Docker.rmi ----

def test_docker_rmi_issues_force_remove():
    calls = []
    dk = Docker(run=lambda argv, timeout=None: (calls.append(argv), (0, ""))[1])
    dk.rmi("manifest-foo-bar")
    assert calls == [["docker", "rmi", "-f", "manifest-foo-bar"]]


# ---- cleanup wiring in _cmd_corpus ----

def _corpus_file(tmp_path, repos):
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps({"repos": repos}))
    return str(p)


def _args(tmp_path, corpus, **over):
    base = dict(corpus=corpus, out=str(tmp_path / "out"), attempts=1, model="sonnet",
                limit=0, force=False, shard=None, cleanup_images=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_corpus_cleanup_images_removes_each_repo_image(tmp_path, monkeypatch):
    corpus = _corpus_file(tmp_path, [
        {"full_name": "a/b", "clone_url": "https://github.com/a/b.git", "commit": "deadbeef1234"},
        {"full_name": "c/d", "clone_url": "https://github.com/c/d.git", "commit": "cafef00d5678"},
    ])
    monkeypatch.setattr(M, "build_one", lambda url, sha, out, runner, attempts: {
        "repo_url": url, "sha": sha, "status": "CERTIFIED", "manifest_size": 1,
        "artifacts_dir": str(tmp_path)})
    removed = []

    class FakeDocker:
        def rmi(self, tag):
            removed.append(tag)

    monkeypatch.setattr(M, "Docker", FakeDocker)
    M._cmd_corpus(_args(tmp_path, corpus, cleanup_images=True))
    assert removed == [f"manifest-{repo_slug('https://github.com/a/b.git')}",
                       f"manifest-{repo_slug('https://github.com/c/d.git')}"]


def test_corpus_no_cleanup_by_default(tmp_path, monkeypatch):
    corpus = _corpus_file(tmp_path, [
        {"full_name": "a/b", "clone_url": "https://github.com/a/b.git", "commit": "deadbeef1234"}])
    monkeypatch.setattr(M, "build_one", lambda url, sha, out, runner, attempts: {
        "repo_url": url, "sha": sha, "status": "CERTIFIED", "manifest_size": 1,
        "artifacts_dir": str(tmp_path)})
    removed = []

    class FakeDocker:
        def rmi(self, tag):
            removed.append(tag)

    monkeypatch.setattr(M, "Docker", FakeDocker)
    M._cmd_corpus(_args(tmp_path, corpus, cleanup_images=False))
    assert removed == []


def test_corpus_cleanup_survives_rmi_failure(tmp_path, monkeypatch):
    # A failed image removal must not abort the batch nor mark the repo failed.
    corpus = _corpus_file(tmp_path, [
        {"full_name": "a/b", "clone_url": "https://github.com/a/b.git", "commit": "deadbeef1234"}])
    monkeypatch.setattr(M, "build_one", lambda url, sha, out, runner, attempts: {
        "repo_url": url, "sha": sha, "status": "CERTIFIED", "manifest_size": 1,
        "artifacts_dir": str(tmp_path)})

    class BoomDocker:
        def rmi(self, tag):
            raise RuntimeError("no such image")

    monkeypatch.setattr(M, "Docker", BoomDocker)
    rc = M._cmd_corpus(_args(tmp_path, corpus, cleanup_images=True))
    assert rc == 0                                   # certified repo → success despite rmi boom
    summ = json.load(open(tmp_path / "out" / "corpus_summary.json"))
    assert summ["certified"] == 1


def test_corpus_shard_selects_subset(tmp_path, monkeypatch):
    repos = [{"full_name": f"o/r{i}", "clone_url": f"https://github.com/o/r{i}.git",
              "commit": f"{i:040d}"} for i in range(6)]
    corpus = _corpus_file(tmp_path, repos)
    processed = []
    monkeypatch.setattr(M, "build_one", lambda url, sha, out, runner, attempts: (
        processed.append(url) or {"repo_url": url, "sha": sha, "status": "CERTIFIED",
                                  "manifest_size": 1, "artifacts_dir": str(tmp_path)}))
    M._cmd_corpus(_args(tmp_path, corpus, shard="2/3"))
    # shard 2/3 = round-robin indices 1, 4
    assert processed == ["https://github.com/o/r1.git", "https://github.com/o/r4.git"]
