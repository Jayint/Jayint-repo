"""Graph-quality eval CLI (plan Task 7).

    python3 -m src.eval.graph_quality --all
    python3 -m src.eval.graph_quality --enrich          # the headline number
    python3 -m src.eval.graph_quality --patch
    python3 -m src.eval.graph_quality --block

Writes `outputs/graph_quality/results.json` (machine-readable) and `report.md` (the table a
human reads). Three independent graders, each usable alone:

  enrich -- does the graph LEARN from a past run's error text? (replays 111 real historical
            failures; headline = pre-emption)
  patch  -- does the render put the ★ on the RIGHT node? (5 injection cells; root-hit AND
            star precision, because root-hit alone is a dishonest metric)
  block  -- does blocks()/verdict() mean what emit means? (needs the T5 graph cache; degrades
            with an explicit SKIPPED note if the cache has not been minted, rather than
            silently reporting nothing)

🔴 THIS REPORT NEVER PRINTS A BARE AGGREGATE. Every number carries its denominator, and every
slice is shown separately. A 90% average would have concealed the pg_config case at 0% -- the
case the whole arm exists for. `test_report.py` asserts this property rather than trusting it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.eval.graph_quality.corpus import load_pairs  # noqa: E402
from src.eval.graph_quality.enrich_replay import aggregate, score_pair  # noqa: E402
from src.eval.graph_quality.patch_localize import build_report  # noqa: E402

_DEFAULT_RESULTS_DIR = "outputs/repo2run_benchmark/results"
_DEFAULT_ARTIFACTS_DIR = "outputs/repo2run_benchmark/eval_artifacts"
_OUT_DIR = "outputs/graph_quality"


def run_enrich(results_dir: str, artifacts_dir: str) -> dict:
    pairs = load_pairs(results_dir, artifacts_dir)
    scores = [score_pair(p, artifacts_dir=artifacts_dir) for p in pairs]
    return aggregate(scores)


def run_patch() -> dict:
    rows = build_report()
    valid = [r for r in rows if not r["not_applicable"]]
    hits = sum(1 for r in valid if r["root_hit"])
    return {
        "rows": rows,
        "n_injections": len(rows),
        "root_hit": f"{hits}/{len(valid)}" if valid else "0/0",
        "n_not_applicable": len(rows) - len(valid),
        "caveat": (
            f"{len(rows)} injection cells is a SMOKE TEST, not a rate. Root-hit is reported over "
            f"the {len(valid)} structurally-scorable cells; the other {len(rows) - len(valid)} "
            "(repin/drop kinds) can never be starred by construction and are excluded rather "
            "than counted as misses."
        ),
    }


def run_block() -> dict:
    """The block grader needs the T5 graph cache (minted once under Docker).

    If the cache is absent we say SKIPPED, loudly. The alternative -- reporting an empty sweep
    as "0 divergences" -- would print a perfect score over zero nodes, which is the single most
    dangerous thing an eval can do.
    """
    try:
        from src.eval.graph_quality import block_parity, graph_cache  # noqa: PLC0415
    except ImportError as exc:
        return {"status": "SKIPPED", "reason": f"block grader not available: {exc}"}
    missing = [fn for fn in ("emit_parity_report", "run_metamorphic_suite",
                             "reference_oracle_report")
               if not hasattr(block_parity, fn)]
    if missing:
        return {"status": "SKIPPED", "reason": f"block_parity is missing {', '.join(missing)}"}
    try:
        graphs = graph_cache.load_graphs()
    except (FileNotFoundError, OSError) as exc:
        return {"status": "SKIPPED",
                "reason": f"graph cache not minted (graph_cache.mint needs Docker): {exc}"}
    if not graphs:
        return {"status": "SKIPPED", "reason": "graph cache is empty — nothing to check"}

    parity = block_parity.emit_parity_report(graphs)
    oracle = block_parity.reference_oracle_report(graphs)
    metamorphic = block_parity.run_metamorphic_suite(graphs)
    return {
        "n_graphs": len(graphs),
        "nodes_checked": parity["total_nodes_checked"],
        "divergences": parity["total_divergences"],
        "metamorphic_all_hold": all(m["holds"] for m in metamorphic),
        "oracle_disagreements": oracle["total_disagreements"],
        "oracle_by_cause": oracle["by_cause"],
        "emit_parity": parity,
        "metamorphic": metamorphic,
        "reference_oracle": oracle,
    }


def _fmt_enrich(agg: dict) -> list[str]:
    out = ["## enrich — does the graph learn from a past run's error text?", ""]
    out.append(f"- pairs replayed: **{agg['n_total']}**")
    out.append(f"- in-scope (an apt/pip label the matcher can score): **{agg['n_in_scope']}**")
    out.append(f"- **PRE-EMPTION: {agg['preemption_rate_in_scope']}**")
    out.append(f"- {agg['denominator_warning']}")
    out.append("")
    out.append("| slice | n | attribution | pre-emption | hallucination | cap-unresolved |")
    out.append("|---|---|---|---|---|---|")
    for kind, e in agg["by_kind"].items():
        pre = e["preemption_rate"] or "n/a"
        out.append(f"| {kind} | {e['n']} | {e['attribution_coverage']} | {pre} | "
                   f"{e['hallucination_count']} | {e['capability_unresolved_count']} |")
    nc = agg["negative_control"]
    out += ["", f"**Negative control** (source-patch repairs, n={nc['n']}): "
                f"{nc['produced_a_node']} produced a node — these are false positives.", ""]
    return out


def _fmt_patch(res: dict) -> list[str]:
    out = ["## patch — is the ★ on the right node?", ""]
    out.append(f"- **root-hit: {res['root_hit']}** over the scorable cells")
    out.append(f"- {res['caveat']}")
    out.append("")
    out.append("| failure class | target | root hit | star precision | n stars | n/a | unresolved |")
    out.append("|---|---|---|---|---|---|---|")
    for r in res["rows"]:
        out.append(
            f"| {r['failure_class']} | {r['target']} | {r['root_hit']} | "
            f"{r['star_precision']:.2f} | {r['n_stars']} | {r['not_applicable']} | "
            f"{r.get('unresolved_capability', False)} |")
    out.append("")
    return out


def _fmt_block(res: dict) -> list[str]:
    out = ["## block — does blocks()/verdict() mean what emit means?", ""]
    if res.get("status") == "SKIPPED":
        out += [f"**SKIPPED** — {res['reason']}", "",
                "This is not a pass. An un-minted cache means the check did not run.", ""]
        return out
    out.append(f"- graphs checked: **{res['n_graphs']}** (real, minted under Docker), "
               f"{res['nodes_checked']} nodes in the emit-parity overlap")
    out.append(f"- **emit-parity divergences: {res['divergences']}** (zero is the pass bar)")
    out.append(f"- metamorphic properties all hold: **{res['metamorphic_all_hold']}**")
    for m in res["metamorphic"]:
        out.append(f"  - `{m['property']}` → {m['holds']}")
    out.append(f"- **reference-oracle disagreements: {res['oracle_disagreements']}**")
    for cause, n in res["oracle_by_cause"].items():
        out.append(f"  - `{cause}`: {n}")
    notes = res["reference_oracle"].get("cause_notes", {})
    for cause, note in notes.items():
        out += ["", f"> **{cause}** — {note}"]
    out.append("")
    return out


def render_markdown(report: dict) -> str:
    lines = ["# Graph quality — enrich / patch / block", "",
             "Measured against real historical failures and the real graph code. "
             "Every number carries its denominator; no slice is averaged away.", ""]
    if "enrich" in report:
        lines += _fmt_enrich(report["enrich"])
    if "patch" in report:
        lines += _fmt_patch(report["patch"])
    if "block" in report:
        lines += _fmt_block(report["block"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m src.eval.graph_quality",
                                 description=__doc__)
    ap.add_argument("--enrich", action="store_true")
    ap.add_argument("--patch", action="store_true")
    ap.add_argument("--block", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--results-dir", default=_DEFAULT_RESULTS_DIR)
    ap.add_argument("--artifacts-dir", default=_DEFAULT_ARTIFACTS_DIR)
    ap.add_argument("--out-dir", default=_OUT_DIR)
    args = ap.parse_args(argv)

    want_enrich = args.enrich or args.all
    want_patch = args.patch or args.all
    want_block = args.block or args.all
    if not (want_enrich or want_patch or want_block):
        ap.print_help()
        return 2

    report: dict = {}
    if want_enrich:
        report["enrich"] = run_enrich(args.results_dir, args.artifacts_dir)
    if want_patch:
        report["patch"] = run_patch()
    if want_block:
        report["block"] = run_block()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(report, indent=2, default=str))
    markdown = render_markdown(report)
    (out_dir / "report.md").write_text(markdown)

    print(markdown)
    print(f"wrote {out_dir}/results.json and {out_dir}/report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
