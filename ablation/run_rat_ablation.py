#!/usr/bin/env python3
"""Sequential RATBench entrypoint for the ExecuteAgent-only ablation.

The benchmark harness remains authoritative for test execution and scoring.  This
module only swaps its setup-script adapter in the current process, so the normal
checkout, Docker evaluation, result-row, and aggregation paths are reused without
adding an ablation arm to the parent project's runner.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (_PROJECT_ROOT, _PROJECT_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@dataclass(frozen=True)
class _RuntimeDependencies:
    runner: Any
    model_module: Any
    adapter_class: type
    score_agent: Callable[[str], dict[str, Any]]


def _runtime_dependencies() -> _RuntimeDependencies:
    """Load the RAT harness lazily so parser/tests do not require its runtime."""
    runner = importlib.import_module("run_rat_benchmark")
    model_module = importlib.import_module("eval.models.dockeragent_model")
    adapter_module = importlib.import_module("ablation.rat_adapter")
    metric_module = importlib.import_module("scripts.compute_essr")
    return _RuntimeDependencies(
        runner=runner,
        model_module=model_module,
        adapter_class=adapter_module.RATAblationAdapter,
        score_agent=metric_module.score_agent,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the ExecuteAgent-only (w/o DepGraph) arm through the official "
            "RAT Docker evaluator and report ESSR."
        )
    )
    parser.add_argument(
        "--repos-json",
        default=str(_PROJECT_ROOT / "datasets" / "rat_python_easy_stratified_50.json"),
        help='Dataset path: either a bare list or an object containing a "repos" list.',
    )
    parser.add_argument(
        "--root-path",
        default=str(Path(__file__).resolve().parent / "output" / "rat_ablation"),
        help="Benchmark output root containing output/, rat_results.json, and ablation_essr.json.",
    )
    parser.add_argument("--offset", type=_nonnegative_int, default=0)
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="Maximum repositories after offset; omit to run the remainder.",
    )
    parser.add_argument("--timeout", type=_positive_int, default=7200)
    parser.add_argument(
        "--llm",
        default=os.getenv("LLM_MODEL", "MiniMax-M3"),
        help="OpenAI-compatible model name used by ExecuteAgent.",
    )
    parser.add_argument(
        "--num-turn",
        type=_positive_int,
        default=50,
        help=(
            "Maximum ExecuteAgent cycles and total agent-call budget exposed "
            "through the RAT model."
        ),
    )
    parser.add_argument(
        "--base-image",
        default="auto",
        help='Base image passed to the ablation adapter; default "auto".',
    )
    return parser


def _select_repos(path: Path, *, offset: int, limit: int | None) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    repos = payload.get("repos") if isinstance(payload, dict) else payload
    if not isinstance(repos, list):
        raise ValueError('repos JSON must be a list or an object containing a "repos" list')

    stop = offset + limit if limit is not None else None
    selected = repos[offset:stop]
    seen: set[str] = set()
    for index, repo in enumerate(selected, start=offset):
        if not isinstance(repo, dict) or not isinstance(repo.get("full_name"), str):
            raise ValueError(f"repository entry {index} is missing string field full_name")
        full_name = repo["full_name"]
        if full_name in seen:
            raise ValueError(f"repository selection contains duplicate {full_name!r}")
        seen.add(full_name)
    return selected


def _existing_result_names(root_path: Path) -> set[str]:
    """Return repository names already contributing rows under ``root_path``."""
    output_root = root_path / "output"
    names: set[str] = set()
    for row_path in output_root.glob("*/*/_result_row.json"):
        relative = row_path.relative_to(output_root)
        names.add(f"{relative.parts[0]}/{relative.parts[1]}")
    return names


def _write_essr(path: Path, metrics: dict[str, Any]) -> dict[str, Any]:
    """Persist the all-repository macro as primary while retaining diagnostics."""
    essr = float(metrics.get("pass_rate_over_all", 0.0))
    payload = {
        "arm": "w/o_depgraph_execute_agent_only",
        "primary_metric": "pass_rate_over_all",
        "primary_metric_value": essr,
        "ESSR": essr,
        "primary_metric_definition": (
            "macro mean test pass rate over all selected repositories; "
            "setup failures and unexecuted repositories contribute zero"
        ),
        **metrics,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload


def run(
    args: argparse.Namespace,
    *,
    dependencies: _RuntimeDependencies | None = None,
) -> int:
    deps = dependencies or _runtime_dependencies()
    repos_path = Path(args.repos_json).expanduser().resolve()
    root_path = Path(args.root_path).expanduser().resolve()
    selected = _select_repos(repos_path, offset=args.offset, limit=args.limit)
    if not selected:
        raise ValueError(
            f"no repositories selected (offset={args.offset}, limit={args.limit})"
        )

    selected_names = {repo["full_name"] for repo in selected}
    foreign_rows = _existing_result_names(root_path) - selected_names
    if foreign_rows:
        names = ", ".join(sorted(foreign_rows))
        raise ValueError(
            "root path already contains result rows outside this selection: "
            f"{names}; use a fresh --root-path"
        )

    root_path.mkdir(parents=True, exist_ok=True)
    # DockerAgentModel.predict resolves this module-global at call time.  Keep the
    # substitution active for the complete sequential run, then restore it for
    # callers that imported this entrypoint as a library.
    original_adapter = deps.model_module.RATV3Adapter
    deps.model_module.RATV3Adapter = deps.adapter_class
    try:
        model = deps.model_module.DockerAgentModel(
            root_path=str(root_path),
            timeout=args.timeout,
            llm=args.llm,
            num_turn=args.num_turn,
            base_image=args.base_image,
        )
        for repo in selected:
            deps.runner._run_one(
                repo["full_name"],
                model,
                str(root_path),
                repo.get("_category", "?"),
                repair_mode="off",
                repair_rounds=0,
            )
        deps.runner.aggregate(str(root_path))
        metrics = deps.score_agent(str(root_path))
    finally:
        deps.model_module.RATV3Adapter = original_adapter

    scored_names = {
        row.get("full_name")
        for row in metrics.get("rows", [])
        if isinstance(row, dict) and isinstance(row.get("full_name"), str)
    }
    if scored_names != selected_names or int(metrics.get("n", 0)) != len(selected):
        raise ValueError(
            "ESSR rows do not exactly match the selected repositories; "
            "use a fresh --root-path and rerun"
        )
    metrics = {
        **metrics,
        "selection": {
            "repos_json": str(repos_path),
            "offset": args.offset,
            "limit": args.limit,
            "repositories": sorted(selected_names),
        },
    }
    essr_path = root_path / "ablation_essr.json"
    report = _write_essr(essr_path, metrics)
    n = int(report.get("n", 0))
    n_exec = int(report.get("n_exec", 0))
    print(
        "\n[ablation/ESSR] "
        f"pass_rate_over_all={report['primary_metric_value']:.4f} "
        f"coverage={float(report.get('coverage', 0.0)):.4f} "
        f"({n_exec}/{n}) "
        "pass_rate_over_executed="
        f"{float(report.get('ESSR_avg_pass_rate_official', 0.0)):.4f}"
    )
    print(f"[ablation/ESSR] report -> {essr_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
