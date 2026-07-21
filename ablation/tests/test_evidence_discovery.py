from __future__ import annotations

import json

from ablation.discovery import discover_test_commands, validate_fixed_test_commands
from ablation.evidence import add_runtime_evidence, collect_repository_evidence


def test_evidence_collection_is_bounded_and_skips_secret_files(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
    (tmp_path / "requirements-test.txt").write_text("pytest\n", encoding="utf-8")
    bundle = collect_repository_evidence(tmp_path, max_total_chars=2_000)
    assert "file:pyproject.toml" in bundle.ids
    assert "file:requirements-test.txt" in bundle.ids
    assert "file:.env" not in bundle.ids
    assert "SECRET=do-not-read" not in bundle.render()


def test_node_test_discovery_without_ecosystem_registry(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9", encoding="utf-8")
    assert discover_test_commands(tmp_path, ("typescript",)) == ("pnpm test",)


def test_primary_language_hint_excludes_auxiliary_test_ecosystems(tmp_path):
    assert discover_test_commands(
        tmp_path,
        ("python", "rust", "c#"),
        primary_language="Python",
    ) == ("python -m pytest -q",)


def test_fixed_test_command_policy_rejects_exclusions_and_filters():
    assert not validate_fixed_test_commands(("python -m pytest -q",))
    assert validate_fixed_test_commands(("pytest --ignore=examples",))
    assert validate_fixed_test_commands(("pytest | head",))


def test_runtime_evidence_is_prioritized_inside_render_bound(tmp_path):
    (tmp_path / "README.md").write_text("x" * 10_000, encoding="utf-8")
    bundle = collect_repository_evidence(tmp_path, max_total_chars=1_000)
    bundle = add_runtime_evidence(
        bundle,
        evidence_id="runtime:search:1:test",
        source="test failure",
        content="ModuleNotFoundError: x",
    )
    rendered = bundle.render()
    assert len(rendered) <= 1_000
    assert "runtime:search:1:test" in rendered
    assert "ModuleNotFoundError: x" in rendered
