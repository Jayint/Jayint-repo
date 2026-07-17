"""Task 4 — Executor: CommandResult, LocalSubprocessExecutor, FakeExecutor."""

from __future__ import annotations

import sys

from conftest import FakeExecutor, make_result

from python_deps.depgraph.executor import CommandResult, Executor, LocalSubprocessExecutor


def test_command_result_ok_property():
    assert CommandResult("c", 0, "", "").ok is True
    assert CommandResult("c", 3, "", "boom").ok is False


def test_local_executor_runs_real_command():
    ex = LocalSubprocessExecutor()
    res = ex.run(f'{sys.executable} -c "print(1)"')
    assert res.ok
    assert res.returncode == 0
    assert res.stdout.strip() == "1"


def test_local_executor_captures_nonzero_and_stderr():
    ex = LocalSubprocessExecutor()
    res = ex.run(
        f'{sys.executable} -c "import sys; sys.stderr.write(\'boom\'); sys.exit(3)"'
    )
    assert not res.ok
    assert res.returncode == 3
    assert "boom" in res.stderr


def test_local_executor_satisfies_protocol():
    assert isinstance(LocalSubprocessExecutor(), Executor)


def test_local_executor_timeout_returns_nonzero():
    ex = LocalSubprocessExecutor()
    res = ex.run(f'{sys.executable} -c "import time; time.sleep(5)"', timeout=1)
    assert not res.ok
    assert "timeout" in res.stderr.lower()


def test_fake_executor_substring_longest_match_wins():
    fake = FakeExecutor(
        responses={
            "pip": make_result(stdout="generic-pip"),
            "pip install numpy": make_result(stdout="numpy-specific"),
        }
    )
    res = fake.run("python -m pip install numpy==1.26.4")
    assert res.stdout == "numpy-specific"  # longest matching key wins


def test_fake_executor_default_fallback():
    fake = FakeExecutor(default=make_result(stdout="fallback", returncode=0))
    res = fake.run("anything at all")
    assert res.stdout == "fallback"


def test_fake_executor_no_match_returns_127():
    fake = FakeExecutor()
    res = fake.run("uv pip compile")
    assert res.returncode == 127
    assert res.stderr == "no fake response"


def test_fake_executor_records_calls():
    fake = FakeExecutor()
    fake.run("a")
    fake.run("b")
    assert fake.calls == ["a", "b"]


def test_fake_executor_satisfies_protocol():
    assert isinstance(FakeExecutor(), Executor)
