#!/usr/bin/env python3
"""Deterministic ESSR recompute — faithful to the official RATBench code.

Recomputes per-repo pass-rate FROM RAW ``run_pytest_results.json`` (no LLM, no trust
in the stored scorer), exactly mirroring the official:
  - eval/common/scorers.py :: pytest_pass_rate_scorer   (per-repo)
  - eval/report/generate_latex_report.py                (aggregate avg_pass_rate)

Official definitions (verified against the RunAnyThing_Anonymous source):
  effective_total = total_tests - skipped
  pass_rate       = passed / effective_total            (if effective_total > 0)
                  = 1.0   if total_tests==0 and error_breakdown == {"TimeoutError": n}
                  = 0.0   otherwise
  pass_rate_exclude_code_issues = passed / (passed + ModuleNotFoundError + ImportError)
  pytest_executed = True  iff run_pytest_results.json EXISTS and parses
  avg_pass_rate (== "ESSR", paper macro) = sum(pass_rate over executed) / executed_count

We also report the metrics the headline hides:
  coverage           = executed / total
  pass_rate_over_all = sum(pass_rate) / total            (coverage-penalized macro)
  micro_pooled       = sum(passed) / sum(effective_total)  over executed

Usage:
  python scripts/compute_essr.py \
    dockeragent=results/dockeragent/2026-06-07-baseline \
    rat=results/rat/2026-06-07-corrected \
    repo2run=results/repo2run/2026-06-07-repo2run
"""
from __future__ import annotations

import json
import os
import sys
from glob import glob
from typing import Any, Dict, Optional, Tuple


def official_pass_rate(results: Dict[str, Any]) -> Tuple[float, float, int, int]:
    """Return (pass_rate, pass_rate_exclude_code_issues, passed, effective_total)
    exactly per eval/common/scorers.py::pytest_pass_rate_scorer."""
    summary = results.get("summary", {}) or {}
    error_breakdown = results.get("error_breakdown", {}) or {}
    # Do NOT coerce null→0 here: a present-but-null summary field must raise TypeError so the
    # caller marks the repo not-executed, exactly as the official scorer does (null - null →
    # TypeError → default_result, pytest_executed=False). Coercing would wrongly count it executed.
    total_tests = summary.get("total_tests", 0)
    passed = summary.get("passed", 0)
    skipped = summary.get("skipped", 0)

    # NOTE: the OFFICIAL scorer credits an all-timeout repo (0 tests, only TimeoutError) as
    # pass_rate = 1.0 — a phantom "assume pass". Your honest scorer (metric patch 0003) and the
    # paper (Npass=0 → ESSR=0) both reject this. We follow the honest/paper-faithful rule: 0.0.
    effective_total = total_tests - skipped
    pass_rate = passed / effective_total if effective_total > 0 else 0.0

    code_issue_count = error_breakdown.get("ModuleNotFoundError", 0) + error_breakdown.get(
        "ImportError", 0
    )
    eff_excl = passed + code_issue_count
    pr_excl = passed / eff_excl if eff_excl > 0 else 0.0

    return round(pass_rate, 4), round(pr_excl, 4), passed, max(effective_total, 0)


def score_agent(root_path: str) -> Dict[str, Any]:
    """Recompute everything from raw, and cross-check the stored _result_row values."""
    repo_dirs = sorted(
        os.path.dirname(p) for p in glob(os.path.join(root_path, "output", "**", "_result_row.json"), recursive=True)
    )
    rows = []
    mismatches = []
    collect_mismatches = []
    for d in repo_dirs:
        full_name = "/".join(d.split(os.sep)[-2:])
        results_path = os.path.join(d, "run_pytest_results.json")
        executed = os.path.exists(results_path)
        if executed:
            try:
                results = json.load(open(results_path))
                pr, pr_excl, passed, eff_total = official_pass_rate(results)
            except (json.JSONDecodeError, KeyError, TypeError):
                executed, pr, pr_excl, passed, eff_total = False, 0.0, 0.0, 0, 0
        else:
            pr, pr_excl, passed, eff_total = 0.0, 0.0, 0, 0

        # Collect-only metric (official pytest_collect_scorer = run_pytest_collect_results.json["success"]).
        collect_path = os.path.join(d, "run_pytest_collect_results.json")
        collect_attempted = os.path.exists(collect_path)
        collect_success = False
        if collect_attempted:
            try:
                collect_success = bool(json.load(open(collect_path)).get("success", False))
            except (json.JSONDecodeError, KeyError, TypeError):
                collect_success = False

        # cross-check vs stored scorer output
        stored = {}
        try:
            stored = json.load(open(os.path.join(d, "_result_row.json")))
        except Exception:
            pass
        if stored:
            s_pr = stored.get("pytest_pass_rate", 0.0)
            s_exec = bool(stored.get("pytest_executed"))
            if abs((s_pr or 0.0) - pr) > 0.0011 or s_exec != executed:
                mismatches.append((full_name, s_pr, pr, s_exec, executed))
            s_collect = bool(stored.get("pytest_collect_success"))
            if s_collect != collect_success:
                collect_mismatches.append((full_name, s_collect, collect_success))

        # Repair sidecar detection: repair_artifacts/repair_meta.json written by
        # _repair_and_rescore in repo2run_repair_port.py.  Never-raises: any
        # missing or malformed sidecar is treated as "not repaired".
        repair_rounds_val: Optional[int] = None
        repair_meta_path = os.path.join(d, "repair_artifacts", "repair_meta.json")
        if os.path.exists(repair_meta_path):
            try:
                repair_meta = json.load(open(repair_meta_path))
                rr = repair_meta.get("repair_rounds")
                if isinstance(rr, int):
                    repair_rounds_val = rr
            except Exception:
                pass  # corrupted sidecar → treat as not repaired

        rows.append({
            "full_name": full_name, "executed": executed,
            "pass_rate": pr, "pass_rate_excl": pr_excl,
            "passed": passed, "eff_total": eff_total,
            "collect_attempted": collect_attempted, "collect_success": collect_success,
            "repair_rounds": repair_rounds_val,  # None when no sidecar, int otherwise
        })

    n = len(rows)
    ex = [r for r in rows if r["executed"]]
    n_exec = len(ex)
    avg_pass_rate = round(sum(r["pass_rate"] for r in ex) / n_exec, 4) if n_exec else 0.0
    avg_pass_excl = round(sum(r["pass_rate_excl"] for r in ex) / n_exec, 4) if n_exec else 0.0
    pass_over_all = round(sum(r["pass_rate"] for r in rows) / n, 4) if n else 0.0
    micro_passed = sum(r["passed"] for r in ex)
    micro_total = sum(r["eff_total"] for r in ex)
    micro = round(micro_passed / micro_total, 4) if micro_total else 0.0
    full_pass = sum(1 for r in ex if r["pass_rate"] >= 0.999)

    # Collect-only metrics (binary per-repo collection success).
    n_collect = sum(1 for r in rows if r["collect_success"])
    collect_all = round(n_collect / n, 4) if n else 0.0
    collect_exec = round(n_collect / n_exec, 4) if n_exec else 0.0
    # "Hollow": collection succeeded but the real run barely/never passed (< 0.5).
    hollow = [r["full_name"] for r in rows if r["collect_success"] and r["pass_rate"] < 0.5]

    # Repair lift metrics: repos that went through >= 1 repair round.
    repaired_rows = [r for r in rows if isinstance(r.get("repair_rounds"), int) and r["repair_rounds"] > 0]
    repaired_count = len(repaired_rows)
    repaired_pass_rate = (
        round(sum(r["pass_rate"] for r in repaired_rows) / repaired_count, 4)
        if repaired_count else 0.0
    )

    return {
        "n": n, "n_exec": n_exec, "coverage": round(n_exec / n, 4) if n else 0.0,
        "ESSR_avg_pass_rate_official": avg_pass_rate,   # paper headline (÷ executed)
        "ESSR_excl_code_issues": avg_pass_excl,         # ÷ executed, lenient variant
        "pass_rate_over_all": pass_over_all,            # coverage-penalized (÷ total)
        "micro_pooled": micro,                          # Σpassed / Σeffective
        "full_pass_repos": full_pass,
        "collect_success_all": collect_all,             # collection success ÷ all repos
        "collect_success_exec": collect_exec,           # collection success ÷ executed
        "n_collect_success": n_collect,
        "hollow_collect_not_pass": hollow,              # collect OK but pass_rate < 0.5
        "repaired_count": repaired_count,               # repos with repair_rounds > 0
        "repaired_pass_rate": repaired_pass_rate,       # avg pass_rate over repaired repos
        "mismatches": mismatches,
        "collect_mismatches": collect_mismatches,
        "rows": rows,
    }


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    specs = []
    for a in argv[1:]:
        if "=" not in a:
            print(f"bad arg (need name=path): {a}")
            return 1
        name, path = a.split("=", 1)
        specs.append((name, path))

    print(f"{'agent':<12} {'n':>3} {'exec':>5} {'cover':>6} {'ESSR(÷exec)':>12} {'÷all':>7} {'micro':>7} "
          f"{'collect÷all':>11} {'collect÷exe':>11} {'hollow':>7} {'fullpass':>9}")
    print("-" * 104)
    all_res = {}
    for name, path in specs:
        r = score_agent(path)
        all_res[name] = r
        print(f"{name:<12} {r['n']:>3} {r['n_exec']:>5} {r['coverage']:>6} "
              f"{r['ESSR_avg_pass_rate_official']:>12} {r['pass_rate_over_all']:>7} {r['micro_pooled']:>7} "
              f"{r['collect_success_all']:>11} {r['collect_success_exec']:>11} "
              f"{len(r['hollow_collect_not_pass']):>7} {r['full_pass_repos']:>9}")

    print("\nLegend: ÷all = paper-faithful pass-rate (failures=0). collect÷all = collection-success rate")
    print("        (pytest --collect-only succeeded). hollow = repos that COLLECT but pass_rate < 0.5.")

    print("\nCross-check vs stored _result_row.json (recomputed-from-raw should match):")
    for name, r in all_res.items():
        notes = []
        if r["mismatches"]:
            notes.append(f"{len(r['mismatches'])} pass_rate")
        if r["collect_mismatches"]:
            notes.append(f"{len(r['collect_mismatches'])} collect")
        if notes:
            print(f"  [{name}] MISMATCH: {', '.join(notes)}")
            for fn, s_pr, c_pr, s_ex, c_ex in r["mismatches"][:20]:
                print(f"      pass  {fn:42} stored={s_pr} raw={c_pr} stored_exec={s_ex} raw_exec={c_ex}")
            for fn, s_c, c_c in r["collect_mismatches"][:20]:
                print(f"      coll  {fn:42} stored={s_c} raw={c_c}")
        else:
            print(f"  [{name}] OK — all {r['n']} repos match stored scorer (pass-rate + collect).")

    out = os.path.join(os.path.dirname(__file__) or ".", "..", "results", "analysis-2026-06-07", "essr_recompute.json")
    out = os.path.normpath(out)
    json.dump({k: {kk: vv for kk, vv in v.items() if kk != "rows"} | {"rows": v["rows"]} for k, v in all_res.items()},
              open(out, "w"), indent=1)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
