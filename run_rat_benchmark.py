#!/usr/bin/env python3
"""Offline RAT benchmark runner — plugs DockerAgentModel into the RAT eval harness
without requiring Weights & Biases or Hugging Face credentials.

Usage:
    python run_rat_benchmark.py [--repos-json PATH] [--root-path DIR] [--limit N]
        [--offset N] [--timeout SECS] [--llm MODEL] [--num-turn N]
        [--tier all|smoke|extended] [--category CAT]
"""

import argparse
import json
import os

# ── Set RAT/agent roots BEFORE importing dockeragent_model (it reads these at import time) ──
os.environ.setdefault("RAT_ROOT", "/tmp/runanything/src")
os.environ.setdefault("DOCKERAGENT_ROOT", "/Users/john/rat-bench-integration")

import sys
sys.path[:0] = [os.environ["RAT_ROOT"]]               # RAT repo: scorers + the model file
from eval.common.scorers import success_scorer, pytest_pass_rate_scorer, pytest_collect_scorer
from eval.models.dockeragent_model import DockerAgentModel   # reuse the SAME predict()


def load_repos(path):                                 # handle bare list OR our subset {"repos":[...]}
    d = json.load(open(path)); return d["repos"] if isinstance(d, dict) else d


def main(repos_json, root_path, limit=None, offset=0, timeout=7200, llm="deepseek-chat",
         num_turn=30, tier="all", category=None):
    os.makedirs(root_path, exist_ok=True)

    repos = load_repos(repos_json)

    # Filter by --tier
    if tier != "all":
        repos = [r for r in repos if r.get("_tier") == tier]

    # Filter by --category
    if category is not None:
        repos = [r for r in repos if r.get("_category") == category]

    # Slice with offset and limit
    repos = repos[offset: offset + limit if limit else None]

    if not repos:
        print(f"No repos match the selection "
              f"(tier={tier}, category={category}, offset={offset}, limit={limit}). Nothing to do.")
        return

    model = DockerAgentModel(root_path=root_path, timeout=timeout, llm=llm, num_turn=num_turn)
    rows = []
    for r in repos:
        done = f"{root_path}/output/{r['full_name']}/run_pytest_results.json"
        if os.path.exists(done):                      # resume: skip finished repos
            out = {"status": "success", "root_path": root_path, "full_name": r["full_name"]}
        else:
            out = model.predict(r["full_name"])       # writes output/{full_name}/*.json
        rows.append({**out, "_category": r.get("_category", "?"),
                     **success_scorer(out), **pytest_collect_scorer(out), **pytest_pass_rate_scorer(out)})
    n = len(rows); mean = lambda k: round(sum(x[k] for x in rows)/n, 4)
    print(f"n={n}  build_success={mean('success')}  collect_success={mean('pytest_collect_success')}")
    print(f"mean_pass_rate={mean('pytest_pass_rate')}  mean_pass_rate_excl_code={mean('pass_rate_exclude_code_issues')}")

    # Per-category breakdown
    from collections import defaultdict
    cat_rows = defaultdict(list)
    for row in rows:
        cat_rows[row["_category"]].append(row)
    print("\nPer-category breakdown:")
    print(f"  {'category':<40}  {'n':>5}  {'build_ok':>8}  {'collect_ok':>10}  {'pass_rate':>9}")
    for cat in sorted(cat_rows):
        cr = cat_rows[cat]
        cn = len(cr)
        cmean = lambda k: round(sum(x[k] for x in cr) / cn, 4)
        print(f"  {cat:<40}  {cn:>5}  {cmean('success'):>8}  {cmean('pytest_collect_success'):>10}  {cmean('pytest_pass_rate'):>9}")

    json.dump(rows, open(f"{root_path}/rat_results.json","w"), indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAT benchmark offline with DockerAgentModel.")
    parser.add_argument("--repos-json", default="/Users/john/rat-bench-integration/datasets/rat_python_hard_subset.json",
                        help="Path to repos JSON (bare list or {\"repos\":[...]} dict).")
    parser.add_argument("--root-path", default="./rat_run",
                        help="Root directory for outputs and rat_results.json.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum number of repos to evaluate (after offset and filters).")
    parser.add_argument("--offset", type=int, default=0,
                        help="Skip this many repos before starting (after tier/category filters).")
    parser.add_argument("--timeout", type=int, default=7200,
                        help="Per-repo timeout in seconds (default 7200, matching the paper).")
    parser.add_argument("--llm", default="deepseek-chat",
                        help="LLM model name passed to the DockerAgent.")
    parser.add_argument("--num-turn", type=int, default=30,
                        help="Maximum agent turns per repo.")
    parser.add_argument("--tier", choices=["all", "smoke", "extended"], default="all",
                        help="Filter repos by _tier field (default: all).")
    parser.add_argument("--category", default=None,
                        help="Filter repos by _category field (default: no filter).")
    args = parser.parse_args()
    main(
        repos_json=args.repos_json,
        root_path=args.root_path,
        limit=args.limit,
        offset=args.offset,
        timeout=args.timeout,
        llm=args.llm,
        num_turn=args.num_turn,
        tier=args.tier,
        category=args.category,
    )
