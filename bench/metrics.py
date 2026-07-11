# bench/metrics.py
from __future__ import annotations

from bench.schema import MeasureRow


def _r(x: float) -> float:
    return round(x, 4)


def _div(num: float, den: float) -> float:
    return _r(num / den) if den else 0.0


def compute_metrics(rows: list[MeasureRow], gold: dict | None = None) -> dict:
    n = len(rows)
    ex = [r for r in rows if r.executed]
    n_exec = len(ex)
    n_ebsr = sum(1 for r in rows if r.ebsr)
    n_collect_clean = sum(1 for r in rows if r.collect_clean)
    n_real = sum(1 for r in rows if r.ebsr and r.pass_rate >= 0.8)
    micro_passed = sum(r.passed for r in ex)
    micro_total = sum(max(r.total - r.skipped, 0) for r in ex)

    out = {
        "n": n, "n_exec": n_exec, "n_ebsr": n_ebsr, "n_collect_clean": n_collect_clean,
        "n_real_success": n_real,
        "EBSR": _div(n_ebsr, n),
        "collect_clean_rate": _div(n_collect_clean, n),
        "ESSR_all": _div(sum(r.pass_rate for r in rows), n),
        "ESSR_exec": _div(sum(r.pass_rate for r in ex), n_exec),
        "real_success": _div(n_real, n),
        "micro": _div(micro_passed, micro_total),
        "full_pass_repos": sum(1 for r in ex if r.pass_rate >= 0.999),
        "coverage": _div(n_exec, n),
    }
    return out
