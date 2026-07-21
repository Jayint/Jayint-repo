from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from ablation.run_rat_ablation import (
    _RuntimeDependencies,
    _existing_result_names,
    _select_repos,
    build_parser,
    run,
)


def test_rat_cli_defaults_to_fifty_total_agent_calls():
    assert build_parser().parse_args([]).num_turn == 50


def test_select_repos_supports_wrapped_dataset_and_slice(tmp_path):
    dataset = tmp_path / "repos.json"
    dataset.write_text(
        json.dumps(
            {
                "repos": [
                    {"full_name": "owner/zero"},
                    {"full_name": "owner/one"},
                    {"full_name": "owner/two"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert _select_repos(dataset, offset=1, limit=1) == [
        {"full_name": "owner/one"}
    ]


def test_select_repos_rejects_malformed_entries(tmp_path):
    dataset = tmp_path / "repos.json"
    dataset.write_text('[{"clone_url": "https://example.invalid/repo"}]')

    with pytest.raises(ValueError, match="full_name"):
        _select_repos(dataset, offset=0, limit=None)


def test_select_repos_rejects_duplicate_names(tmp_path):
    dataset = tmp_path / "repos.json"
    dataset.write_text(
        '[{"full_name": "owner/repo"}, {"full_name": "owner/repo"}]'
    )

    with pytest.raises(ValueError, match="duplicate"):
        _select_repos(dataset, offset=0, limit=None)


def test_existing_result_names_reads_only_rat_result_rows(tmp_path):
    output = tmp_path / "output"
    row = output / "owner" / "repo" / "_result_row.json"
    row.parent.mkdir(parents=True)
    row.write_text("{}")
    unrelated = output / "other" / "repo" / "result.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("{}")

    assert _existing_result_names(tmp_path) == {"owner/repo"}


def test_run_swaps_adapter_runs_without_outer_repair_and_writes_essr(tmp_path):
    dataset = tmp_path / "repos.json"
    dataset.write_text(
        json.dumps(
            {
                "repos": [
                    {"full_name": "owner/zero", "_category": "python_small"},
                    {"full_name": "owner/one", "_category": "python_large"},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    calls: list[tuple] = []

    class OriginalAdapter:
        pass

    class AblationAdapter:
        pass

    model_module = SimpleNamespace(RATV3Adapter=OriginalAdapter)

    class FakeModel:
        def __init__(self, **kwargs):
            self.config = kwargs

    model_module.DockerAgentModel = FakeModel

    def fake_run_one(full_name, model, root_path, category, **kwargs):
        assert model_module.RATV3Adapter is AblationAdapter
        calls.append((full_name, model, root_path, category, kwargs))
        return {"status": "success"}

    def fake_aggregate(root_path):
        assert model_module.RATV3Adapter is AblationAdapter
        calls.append(("aggregate", root_path))
        return []

    metrics = {
        "n": 1,
        "n_exec": 1,
        "coverage": 1.0,
        "pass_rate_over_all": 0.75,
        "ESSR_avg_pass_rate_official": 0.75,
        "micro_pooled": 0.8,
        "rows": [{"full_name": "owner/one", "pass_rate": 0.75}],
    }
    deps = _RuntimeDependencies(
        runner=SimpleNamespace(_run_one=fake_run_one, aggregate=fake_aggregate),
        model_module=model_module,
        adapter_class=AblationAdapter,
        score_agent=lambda root_path: metrics,
    )
    args = argparse.Namespace(
        repos_json=str(dataset),
        root_path=str(output),
        offset=1,
        limit=1,
        timeout=123,
        llm="test-model",
        num_turn=7,
        base_image="python:3.11-slim",
    )

    assert run(args, dependencies=deps) == 0
    assert model_module.RATV3Adapter is OriginalAdapter
    assert calls[0][0] == "owner/one"
    assert calls[0][3] == "python_large"
    assert calls[0][4] == {"repair_mode": "off", "repair_rounds": 0}
    assert calls[0][1].config == {
        "root_path": str(output.resolve()),
        "timeout": 123,
        "llm": "test-model",
        "num_turn": 7,
        "base_image": "python:3.11-slim",
    }

    report = json.loads((output / "ablation_essr.json").read_text())
    assert report["primary_metric"] == "pass_rate_over_all"
    assert report["primary_metric_value"] == 0.75
    assert report["ESSR"] == 0.75
    assert report["coverage"] == 1.0
    assert report["ESSR_avg_pass_rate_official"] == 0.75
    assert report["selection"]["repositories"] == ["owner/one"]


def test_run_rejects_foreign_rows_in_reused_root(tmp_path):
    dataset = tmp_path / "repos.json"
    dataset.write_text('[{"full_name": "owner/selected"}]')
    root = tmp_path / "run"
    stale = root / "output" / "owner" / "other" / "_result_row.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")

    args = argparse.Namespace(
        repos_json=str(dataset),
        root_path=str(root),
        offset=0,
        limit=1,
        timeout=1,
        llm="test",
        num_turn=1,
        base_image="auto",
    )
    deps = _RuntimeDependencies(
        runner=SimpleNamespace(),
        model_module=SimpleNamespace(),
        adapter_class=object,
        score_agent=lambda _: {},
    )

    with pytest.raises(ValueError, match="fresh --root-path"):
        run(args, dependencies=deps)


def test_run_restores_adapter_if_benchmark_raises(tmp_path):
    dataset = tmp_path / "repos.json"
    dataset.write_text('[{"full_name": "owner/repo"}]')

    class OriginalAdapter:
        pass

    class AblationAdapter:
        pass

    model_module = SimpleNamespace(RATV3Adapter=OriginalAdapter)
    model_module.DockerAgentModel = lambda **kwargs: object()

    def fail(*args, **kwargs):
        assert model_module.RATV3Adapter is AblationAdapter
        raise RuntimeError("benchmark failed")

    deps = _RuntimeDependencies(
        runner=SimpleNamespace(_run_one=fail, aggregate=lambda _: None),
        model_module=model_module,
        adapter_class=AblationAdapter,
        score_agent=lambda _: {},
    )
    args = argparse.Namespace(
        repos_json=str(dataset),
        root_path=str(tmp_path / "output"),
        offset=0,
        limit=1,
        timeout=1,
        llm="test",
        num_turn=1,
        base_image="auto",
    )

    with pytest.raises(RuntimeError, match="benchmark failed"):
        run(args, dependencies=deps)
    assert model_module.RATV3Adapter is OriginalAdapter
