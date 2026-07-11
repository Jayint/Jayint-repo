from src.bench_emit.normalize import clone_lines, link_testbed, parse_from


def test_parse_from_returns_first_tag():
    assert parse_from("FROM python:3.10-slim\nRUN echo hi") == "python:3.10-slim"


def test_parse_from_none_when_absent():
    assert parse_from("RUN echo hi") is None


def test_link_testbed_appends_symlink():
    out = link_testbed("FROM x\nWORKDIR /repo", src="/repo")
    assert out.rstrip().endswith("RUN ln -sfn /repo /testbed")


def test_link_testbed_is_idempotent():
    once = link_testbed("FROM x", src="/repo")
    twice = link_testbed(once, src="/repo")
    assert once == twice
    assert twice.count("RUN ln -sfn /repo /testbed") == 1


def test_clone_lines_installs_git_and_clones():
    out = clone_lines("https://github.com/o/r", dest="/repo")
    assert "apt-get install -y --no-install-recommends git" in out
    assert "RUN git clone --depth=1 https://github.com/o/r /repo" in out
