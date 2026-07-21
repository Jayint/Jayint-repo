from __future__ import annotations

import hashlib

from ablation.integrity import (
    collect_source_manifest,
    verify_source_manifest,
)
from ablation.models import FlatBlock, FlatPlan
from ablation.runtime import SandboxHost


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeSandbox:
    def __init__(self, manifest_output: str):
        self.manifest_output = manifest_output
        self.executed: list[str] = []

    def exec_readonly(self, command: str):
        self.executed.append(command)
        if command.startswith("find . "):
            return 0, self.manifest_output
        return 0, "ok"


def test_collect_manifest_protects_source_tests_and_config_but_skips_venv(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_value(): pass\n")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("generated\n")

    manifest = collect_source_manifest(tmp_path)

    assert set(manifest) == {
        "pyproject.toml",
        "src/app.py",
        "tests/test_app.py",
    }


def test_integrity_guard_detects_added_removed_and_changed_files():
    expected = {
        "src/app.py": digest("old"),
        "tests/test_app.py": digest("test"),
    }
    sandbox = FakeSandbox(
        "\n".join(
            (
                f"{digest('new')}  ./src/app.py",
                f"{digest('extra')}  ./tests/conftest.py",
            )
        )
    )

    passed, output = verify_source_manifest(sandbox, expected)

    assert not passed
    assert "changed: src/app.py" in output
    assert "added: tests/conftest.py" in output
    assert "removed: tests/test_app.py" in output


def test_sandbox_host_runs_integrity_gate_before_agent_checks():
    expected = {"tests/test_app.py": digest("test")}
    sandbox = FakeSandbox(f"{digest('changed')}  ./tests/test_app.py")
    host = SandboxHost(sandbox, source_manifest=expected)
    plan = FlatPlan(
        (
            FlatBlock(
                "b01",
                ("python -m pip install -e .",),
                ("python -m pip check",),
                ("file:pyproject.toml",),
            ),
        )
    )

    result = host.run_checks(plan)

    assert not result.passed
    assert result.command == "host:source_integrity"
    assert sandbox.executed[0].startswith("find . ")
    assert "python -m pip check" not in sandbox.executed


def test_empty_baseline_still_rejects_a_new_protected_file():
    sandbox = FakeSandbox(f"{digest('new')}  ./tests/test_new.py")
    host = SandboxHost(sandbox, source_manifest={})

    result = host.run_checks(FlatPlan())

    assert not result.passed
    assert "added: tests/test_new.py" in result.output
