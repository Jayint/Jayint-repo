#!/usr/bin/env python3
"""Score the agent's IN-SANDBOX (live-container) test result, RAT-style.

This measures the BUILD-AGENT environment success rate (BA-ESR): the pass-rate the
agent achieved INSIDE its live sandbox, BEFORE the Dockerfile is synthesized and
rebuilt. RAT scores the live container too, so this number is RAT-comparable.

It pairs with ``scripts/compute_essr.py`` (which scores the CLEANROOM rebuild):

    in-build success (this script)  -  cleanroom success (compute_essr)  =  synthesis loss

The pass-rate is computed by the SAME formula as compute_essr (``official_pass_rate``,
copied verbatim below), so the two numbers are directly comparable.

Source of truth: ``best_in_sandbox_test_result`` (a ``run_pytest_results.json``-shaped
object), emitted by ``agent.py`` and ``multi_docker_eval_adapter.py``. It is read from,
in priority order:

    1. <instance>.json  ->  logs.best_in_sandbox_test_result   (co-located with the
       RATBench output that compute_essr reads; preferred for a same-denominator A/B)
    2. agent_run_summary.json  ->  best_in_sandbox_test_result  (always written by the
       agent to its workplace; fallback when not run through the RATBench harness)

Usage:
    python scripts/score_in_sandbox.py <root_dir>
    python scripts/score_in_sandbox.py radical=<root_dir> other=<root_dir2>
    python scripts/score_in_sandbox.py <root_dir> --json out.json
"""
from __future__ import annotations

import json
import os
import sys
from glob import glob
from typing import Any, Dict, List, Optional, Tuple

IN_BUILD_BAR = 0.8  # real-success bar, identical to compute_essr's passes_agent_goal


def official_pass_rate(results: Dict[str, Any]) -> Tuple[float, int, int]:
    """pass_rate per compute_essr.official_pass_rate (copied verbatim, trimmed to what
    we need): passed / (total_tests - skipped), 0.0 when no effective tests."""
    summary = results.get("summary", {}) or {}
    total_tests = summary.get("total_tests", 0)
    passed = summary.get("passed", 0)
    skipped = summary.get("skipped", 0)
    effective_total = total_tests - skipped
    pass_rate = passed / effective_total if effective_total > 0 else 0.0
    return round(pass_rate, 4), passed, max(effective_total, 0)


def _best_result_from_instance_json(repo_dir: str, full_name: str) -> Optional[Dict[str, Any]]:
    instance_id = full_name.replace("/", "__")
    path = os.path.join(repo_dir, f"{instance_id}.json")
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path))
    except (json.JSONDecodeError, OSError):
        return None
    logs = data.get("logs") or {}
    return logs.get("best_in_sandbox_test_result")


def _best_result_from_run_summary(path: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.load(open(path))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("best_in_sandbox_test_result")


def _extract_result_payload(best: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """best_in_sandbox_test_result is an attempt {step_index, command, pass_rate, result};
    return the inner run_pytest_results.json-shaped ``result`` (or the object itself if it
    already has a ``summary``)."""
    if not isinstance(best, dict):
        return None
    if isinstance(best.get("result"), dict):
        return best["result"]
    if "summary" in best:
        return best
    return None


def _enumerate_repos(root: str) -> List[Tuple[str, str, Optional[str]]]:
    """Return (full_name, repo_dir, run_summary_path) for each repo found under root.

    Prefers the RATBench layout (output/**/_result_row.json) so the denominator matches
    compute_essr; falls back to globbing agent_run_summary.json anywhere under root.
    """
    repos: List[Tuple[str, str, Optional[str]]] = []
    result_rows = sorted(glob(os.path.join(root, "output", "**", "_result_row.json"), recursive=True))
    if result_rows:
        for p in result_rows:
            d = os.path.dirname(p)
            full_name = "/".join(d.split(os.sep)[-2:])
            rs = os.path.join(d, "agent_run_summary.json")
            repos.append((full_name, d, rs if os.path.exists(rs) else None))
        return repos

    for p in sorted(glob(os.path.join(root, "**", "agent_run_summary.json"), recursive=True)):
        d = os.path.dirname(p)
        full_name = "/".join(d.split(os.sep)[-2:])
        repos.append((full_name, d, p))
    return repos


def score_in_sandbox(root: str) -> Dict[str, Any]:
    rows = []
    for full_name, repo_dir, run_summary_path in _enumerate_repos(root):
        best = _best_result_from_instance_json(repo_dir, full_name)
        if best is None and run_summary_path:
            best = _best_result_from_run_summary(run_summary_path)
        payload = _extract_result_payload(best)

        if payload is None:
            rows.append({"full_name": full_name, "executed": False, "pass_rate": 0.0,
                         "passed": 0, "eff_total": 0, "in_build_success": False})
            continue

        pr, passed, eff_total = official_pass_rate(payload)
        rows.append({
            "full_name": full_name,
            "executed": True,
            "pass_rate": pr,
            "passed": passed,
            "eff_total": eff_total,
            "in_build_success": bool(pr >= IN_BUILD_BAR),
            "command": (best or {}).get("command"),
        })

    n = len(rows)
    ex = [r for r in rows if r["executed"]]
    n_exec = len(ex)
    in_build_success = [r for r in rows if r["in_build_success"]]
    partial = [r for r in ex if not r["in_build_success"]]  # executed but < 0.8
    micro_passed = sum(r["passed"] for r in ex)
    micro_total = sum(r["eff_total"] for r in ex)

    return {
        "root": root,
        "n": n,
        "executed": n_exec,
        "coverage": round(n_exec / n, 4) if n else 0.0,                       # ran tests at all
        "in_build_success": len(in_build_success),
        "in_build_success_rate": round(len(in_build_success) / n, 4) if n else 0.0,  # >=0.8 (real)
        "essr_div_exec": round(sum(r["pass_rate"] for r in ex) / n_exec, 4) if n_exec else 0.0,
        "essr_div_all": round(sum(r["pass_rate"] for r in rows) / n, 4) if n else 0.0,
        "micro_pooled": round(micro_passed / micro_total, 4) if micro_total else 0.0,
        "full_pass": sum(1 for r in ex if r["pass_rate"] >= 0.999),
        "in_build_success_repos": sorted(r["full_name"] for r in in_build_success),
        "partial_repos": sorted((r["full_name"], r["pass_rate"]) for r in partial),
        "not_executed_repos": sorted(r["full_name"] for r in rows if not r["executed"]),
        "rows": rows,
    }


def _print_report(label: str, agg: Dict[str, Any]) -> None:
    print(f"\n=== IN-SANDBOX (build-agent) score: {label}  [{agg['root']}] ===")
    print(f"  repos                 : {agg['n']}")
    print(f"  ran tests (coverage)  : {agg['executed']}/{agg['n']}  ({agg['coverage']})")
    print(f"  IN-BUILD SUCCESS >=0.8: {agg['in_build_success']}/{agg['n']}  ({agg['in_build_success_rate']})")
    print(f"  full-pass (~100%)     : {agg['full_pass']}")
    print(f"  ESSR div_exec (RAT)   : {agg['essr_div_exec']}")
    print(f"  ESSR div_all          : {agg['essr_div_all']}")
    print(f"  micro pooled          : {agg['micro_pooled']}")
    if agg["partial_repos"]:
        print("  -- executed but <0.8 (partial credit) --")
        for name, pr in agg["partial_repos"]:
            print(f"       {pr:>6}  {name}")
    if agg["not_executed_repos"]:
        print(f"  -- no in-sandbox test captured ({len(agg['not_executed_repos'])}) --")
        for name in agg["not_executed_repos"]:
            print(f"       {name}")


def main(argv: List[str]) -> int:
    json_out = None
    roots: List[Tuple[str, str]] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            json_out = argv[i + 1]
            i += 2
            continue
        if "=" in a:
            label, path = a.split("=", 1)
        else:
            label, path = os.path.basename(os.path.normpath(a)) or a, a
        roots.append((label, path))
        i += 1

    if not roots:
        print(__doc__)
        return 2

    out = {}
    for label, path in roots:
        agg = score_in_sandbox(path)
        out[label] = agg
        _print_report(label, agg)

    if json_out:
        with open(json_out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote {json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
