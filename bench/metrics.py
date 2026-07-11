# bench/metrics.py
from __future__ import annotations

from bench.schema import MeasureRow


def _r(x: float) -> float:
    return round(x, 4)


def _div(num: float, den: float) -> float:
    return _r(num / den) if den else 0.0


def _mean_opt(vals: list) -> float | None:
    xs = [v for v in vals if v is not None]
    return _r(sum(xs) / len(xs)) if xs else None


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
    if gold:
        gold_scores = []
        for r in rows:
            g = gold.get(r.repo)
            if not g:
                continue
            gset = set(g)
            gold_scores.append(len(set(r.passed_node_ids) & gset) / len(gset))
        out["n_gold"] = len(gold_scores)
        out["gold_ESSR"] = _div(sum(gold_scores), len(gold_scores))

    tok_rows = [r for r in rows if r.tokens_in is not None and r.tokens_out is not None]
    tok_total = sum(r.tokens_in + r.tokens_out for r in tok_rows)
    n_build_ok = sum(1 for r in rows if r.build_ok)
    n_unreplayed = sum(1 for r in rows if r.meta.get("unreplayed"))

    out.update({
        "mean_image_delta_mb": _mean_opt([r.image_delta_mb for r in rows]),
        "mean_installed_pkgs": _mean_opt([r.installed_pkg_count for r in rows]),
        "mean_tokens": _r(tok_total / len(tok_rows)) if tok_rows else None,
        "mean_tokens_out": _mean_opt([r.tokens_out for r in tok_rows]) if tok_rows else None,
        "tokens_per_ebsr": _r(tok_total / n_ebsr) if (tok_rows and n_ebsr) else None,
        "tokens_per_real_success": _r(tok_total / n_real) if (tok_rows and n_real) else None,
        "mean_turns": _mean_opt([r.turns_used for r in rows]),
        "mean_produce_s": _mean_opt([r.produce_s for r in rows]),
        "wall_s_per_real_success": (
            _r(sum((r.produce_s or 0) + (r.build_s or 0) + (r.test_s or 0) for r in rows) / n_real)
            if n_real else None),
        "n_token_reporting": len(tok_rows),
        "rebuild_ok_rate": _div(n_build_ok, n),
        "unreplayed_rate": _div(n_unreplayed, n),
    })
    return out
