"""C0/C1/C2/C3 sufficiency ablation over a stratified sample of ServiceNodes.

Answers what name-recall (Task 12) cannot: is a ``ServiceNode`` enough for an agent
to actually provision the service?

Two entry points, both offline-safe until a real client is passed in:

    # 1. dump production nodes (deterministic, sorted owner/repo order)
    PYTHONPATH=src python3 -m src.eval.service_sufficiency.run dump <corpus_root> <nodes.jsonl>

    # 2. run the ablation (caller supplies the LLM client; see main())
    #    from src.eval.service_sufficiency.run import main
    #    main("<nodes.jsonl>", "<out.json>", client=my_client, model="sonnet")

Corrections applied (see .superpowers/sdd/task-13-corrections.md):
  D1  the install constraint rides in EVERY condition (brief.py), not just C1.
  D2  sample only oracle-confirmed nodes (name in the repo's must_detect list).
  D3  nodes come from production ``build_service_nodes`` -- never the stale PoC file.
  D4  a MiniMax-routed client is refused before any completion is issued.
  D5  per_stratum=13; the completion budget is COMPUTED and printed before spending.
  D6  the valkey top-up matches rq/rq specifically and dedups without O(n^2) surprises.
"""
from __future__ import annotations

import dataclasses
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

from src.envstate.llm_response import complete_with_retry
from src.eval.service_sufficiency.brief import render_brief
from src.eval.service_sufficiency.graders import grade

CONDITIONS = ("C0", "C1", "C2", "C3")
HEAD = ("postgres", "redis", "mysql")

# Identical across all conditions (D1): what varies must be evidence, and only evidence.
GEN_SYSTEM = (
    "You are configuring a Debian-based container. Output ONLY shell commands, no prose.\n"
    "If the information given is insufficient to install and start the service, output exactly:\n"
    "INSUFFICIENT: <the single missing piece of information>")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ORACLE = _REPO_ROOT / "tests" / "eval" / "fixtures" / "service_oracle.json"


class _Node:
    """Attribute view of a node dict, for the deterministic grader."""

    def __init__(self, d: dict):
        self.port = d.get("port")
        self.image_repo = d.get("image_repo", "")


# ── node source (D3) ─────────────────────────────────────────────────────────

def dump_service_nodes(corpus_root: str, out_path: str) -> int:
    """Dump every production ``ServiceNode`` in the corpus to a JSONL file.

    Iterates ``<owner>/<repo>`` in ``sorted()`` order so the dump -- and therefore
    the downstream sample -- is deterministic. Each row is
    ``dataclasses.asdict(node) | {"repo": "<owner>/<repo>"}`` (the dataclass has no
    ``repo`` field of its own, and ``run`` reads ``n["repo"]``). Returns the row count.

    ``build_service_nodes`` is imported lazily so importing this module for the
    offline ablation/guard tests never requires ``python_deps`` on the path.
    """
    from python_deps.depgraph.service_construct import build_service_nodes

    rows: list[dict] = []
    for owner in sorted(os.listdir(corpus_root)):
        odir = os.path.join(corpus_root, owner)
        if not os.path.isdir(odir):
            continue
        for repo in sorted(os.listdir(odir)):
            rd = os.path.join(odir, repo)
            if not os.path.isdir(rd):
                continue
            for node in build_service_nodes(rd, owner=owner):
                rows.append(dataclasses.asdict(node) | {"repo": f"{owner}/{repo}"})

    with open(out_path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return len(rows)


# ── stratification + sampling (D2, D5, D6) ───────────────────────────────────

def _stratum(n: dict) -> str:
    if n["check"]["source"] == "none":
        return "unverifiable"
    short = n["image_repo"].rsplit("/", 1)[-1]
    return "head" if short in HEAD else "exotic"


def oracle_confirmed(nodes: list[dict], oracle_path: str | os.PathLike) -> list[dict]:
    """Keep only nodes whose ``name`` is in that repo's ``must_detect`` list (D2).

    Order-preserving, so the JSONL's deterministic sorted order carries through to
    the sample. Everything else is a known false positive (compose test fixtures such
    as ``azure-vote-front``) whose provisioning cost is a *different* eval.
    """
    with open(oracle_path) as fh:
        oracle = json.load(fh)
    out: list[dict] = []
    for n in nodes:
        entry = oracle.get(n.get("repo", ""))
        if entry and n["name"] in entry["must_detect"]:
            out.append(n)
    return out


def sample(nodes: list[dict], per_stratum: int, seed: int) -> list[dict]:
    """Stratified draw across {head, exotic, unverifiable}, then top up rq's valkey.

    The valkey top-up (D6) matches ``rq/rq`` specifically -- not any repo that happens
    to declare a ``valkey`` service -- and appends only if not already drawn.
    """
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = {}
    for n in nodes:
        buckets.setdefault(_stratum(n), []).append(n)
    out: list[dict] = []
    for _s, group in sorted(buckets.items()):
        rng.shuffle(group)
        out.extend(group[:per_stratum])
    drawn = {(r["repo"], r["name"]) for r in out}
    out.extend(n for n in nodes
               if n["name"] == "valkey" and n.get("repo") == "rq/rq"
               and (n["repo"], n["name"]) not in drawn)
    return out


def completion_budget(picked: list[dict]) -> int:
    """Number of completions the ablation will spend: 4 conditions per node, minus
    the C3 that is skipped for every ``unverifiable`` node (no check to remove)."""
    return sum(4 if n["check"]["source"] != "none" else 3 for n in picked)


# ── safety precondition (D4) ─────────────────────────────────────────────────

def _assert_not_minimax(client) -> None:
    """Refuse a MiniMax-routed client BEFORE any completion is issued (D4).

    Keyed on the endpoint (``base_url`` contains ``minimaxi``) exactly like
    ``llm_response.apply_minimax_thinking``. A ``None`` client (offline/CLI) has no
    ``base_url`` and passes -- the guard only fires on a real MiniMax endpoint.
    """
    base_url = str(getattr(client, "base_url", "") or "")
    if "minimaxi" in base_url:
        raise SystemExit("refusing to run: client is routed to MiniMax")


# ── the ablation ─────────────────────────────────────────────────────────────

def main(nodes_path: str, out_path: str, client=None, model: str = "sonnet",
         oracle_path: str | os.PathLike = _DEFAULT_ORACLE,
         per_stratum: int = 13, seed: int = 1234) -> int:
    _assert_not_minimax(client)                 # D4: precondition, before any I/O or spend

    nodes = [json.loads(line) for line in open(nodes_path) if line.strip()]
    confirmed = oracle_confirmed(nodes, oracle_path)
    picked = sample(confirmed, per_stratum=per_stratum, seed=seed)

    budget = completion_budget(picked)
    strata = dict(Counter(_stratum(n) for n in picked))
    print(f"completion budget: {budget} "
          f"(nodes={len(picked)}, strata={strata}, conditions={CONDITIONS})")

    results = []
    for n in picked:
        for cond in CONDITIONS:
            if cond == "C3" and n["check"]["source"] == "none":
                continue                        # no check to remove
            msgs = [{"role": "system", "content": GEN_SYSTEM},
                    {"role": "user", "content": render_brief(n, cond)}]
            text, _usage, _raw = complete_with_retry(client, model, msgs, temperature=0)
            g = grade(text, _Node(n))
            results.append({"repo": n["repo"], "name": n["name"],
                            "stratum": _stratum(n), "condition": cond,
                            "commands": text, **g.__dict__})

    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=1)

    _print_report(results)
    return 0


def _print_report(results: list[dict]) -> None:
    print(f"\n{'cond':5s} {'n':>3s} {'ok':>5s} {'policy_viol':>12s} {'no_start':>9s} {'INSUFF':>7s}")
    for cond in CONDITIONS:
        rows = [r for r in results if r["condition"] == cond]
        if not rows:
            continue
        ok = sum(1 for r in rows
                 if not r["policy_violation"] and r["background_start"] and not r["insufficient"])
        print(f"{cond:5s} {len(rows):3d} {ok / len(rows):5.0%} "
              f"{sum(r['policy_violation'] for r in rows):12d} "
              f"{sum(not r['background_start'] and not r['insufficient'] for r in rows):9d} "
              f"{sum(r['insufficient'] for r in rows):7d}")

    print("\nby stratum (C0 -> C1 delta is the paper's claim):")
    for s in sorted({r["stratum"] for r in results}):
        for cond in ("C0", "C1"):
            rows = [r for r in results if r["stratum"] == s and r["condition"] == cond]
            if rows:
                ok = sum(1 for r in rows if not r["policy_violation"] and r["background_start"])
                print(f"  {s:14s} {cond}  {ok}/{len(rows)}")

    print("\nunverifiable stratum: a correct INSUFFICIENT refusal is a PASS")
    unv = [r for r in results if r["stratum"] == "unverifiable" and r["condition"] == "C1"]
    if unv:
        print(f"  refused correctly: {sum(r['insufficient'] for r in unv)}/{len(unv)}")
    print("\npolicy violations by stratum:",
          dict(Counter(r["stratum"] for r in results if r["policy_violation"])))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "dump":
        count = dump_service_nodes(sys.argv[2], sys.argv[3])
        print(f"dumped {count} nodes -> {sys.argv[3]}")
        sys.exit(0)
    # Ablation path: a real client must be supplied by importing main(); the bare CLI
    # never constructs one (it would need an API key). client=None prints the budget
    # then fails loudly at the first completion rather than spending anything.
    sys.exit(main(sys.argv[1], sys.argv[2]))
