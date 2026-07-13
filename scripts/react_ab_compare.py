#!/usr/bin/env python3
"""Compare two react-arm runs (A/B on REACT_PROMPT_STYLE=blob vs messages).

Consumes the artifacts a react run already writes — no new instrumentation:
  - the ReactLog trace JSONL (``--trace-out`` of scripts/run_v3_e2e.py): the run-end record
    ``{"phase":"end","outcome":"DONE"|"GIVEUP","steps":N,...}`` gives outcome + turns.
  - the run's captured stdout (``> <repo>.log``): ``[Tokens] Input: I, Output: O, Total: T`` lines
    (one per LLM call) give token economy.

Layout (per arm dir): ``<dir>/<repo>.jsonl`` (trace, required) and optional ``<dir>/<repo>.log``
(stdout with [Tokens]). Repos are matched across arms by the file stem.

Usage:
    python scripts/react_ab_compare.py <blob_dir> <messages_dir> [--labels blob,messages]

Emits a per-repo side-by-side table + an aggregate (DONE rate, turns-to-DONE, tokens).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

_TOKENS_RE = re.compile(r"\[Tokens\]\s*Input:\s*(\d+),\s*Output:\s*(\d+),\s*Total:\s*(\d+)")


def parse_trace(path: str) -> dict:
    """The run-end outcome + turn count from a react trace JSONL (last ``phase=="end"`` record).
    Missing/partial/corrupt trace → outcome 'NO_END' so a crashed run is visible, not dropped."""
    end = None
    llm_calls = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("phase") == "plan":
                    llm_calls += 1
                if rec.get("phase") == "end":
                    end = rec
    except OSError:
        return {"outcome": "NO_TRACE", "steps": None, "llm_calls": 0, "best_passed": None}
    if end is None:
        return {"outcome": "NO_END", "steps": None, "llm_calls": llm_calls, "best_passed": None}
    return {"outcome": end.get("outcome", "?"), "steps": end.get("steps"),
            "llm_calls": llm_calls, "best_passed": end.get("best_passed")}


def parse_tokens(path: str) -> dict:
    """Sum input/output/total tokens over every [Tokens] line in a captured stdout log."""
    tin = tout = ttot = 0
    if not path or not os.path.exists(path):
        return {"input": None, "output": None, "total": None}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _TOKENS_RE.search(line)
            if m:
                tin += int(m.group(1)); tout += int(m.group(2)); ttot += int(m.group(3))
    return {"input": tin, "output": tout, "total": ttot}


def collect_run(run_dir: str) -> dict:
    """Map repo-stem → {outcome, steps, llm_calls, tokens} for every trace JSONL under *run_dir*."""
    out = {}
    for trace in sorted(glob.glob(os.path.join(run_dir, "*.jsonl"))):
        stem = os.path.splitext(os.path.basename(trace))[0]
        row = parse_trace(trace)
        row["tokens"] = parse_tokens(os.path.join(run_dir, stem + ".log"))
        out[stem] = row
    return out


def _fmt_cell(row: dict) -> str:
    steps = row["steps"] if row["steps"] is not None else "—"
    tot = row["tokens"]["total"]
    tok = f"{tot // 1000}k" if isinstance(tot, int) and tot else "—"
    return f"{row['outcome']:<7} steps={steps:<3} tok={tok}"


def _aggregate(run: dict, label: str) -> str:
    n = len(run)
    done = [r for r in run.values() if r["outcome"] == "DONE"]
    done_steps = [r["steps"] for r in done if isinstance(r["steps"], int)]
    toks = [r["tokens"]["total"] for r in run.values() if isinstance(r["tokens"]["total"], int)]
    avg_steps = f"{sum(done_steps) / len(done_steps):.1f}" if done_steps else "—"
    avg_tok = f"{sum(toks) // max(1, len(toks)) // 1000}k" if toks else "—"
    return (f"{label:<9} repos={n:<3} DONE={len(done):<3} ({100 * len(done) / max(1, n):.0f}%)  "
            f"avg turns-to-DONE={avg_steps:<5} avg tokens/repo={avg_tok}")


def render(blob: dict, messages: dict, labels: "tuple[str, str]") -> str:
    la, lb = labels
    repos = sorted(set(blob) | set(messages))
    lines = [f"react A/B — {la} vs {lb}  ({len(repos)} repos)", "=" * 92,
             f"{'repo':<28} {la + ' (blob)':<30} {lb + ' (messages)':<30}", "-" * 92]
    for r in repos:
        a = blob.get(r, {"outcome": "MISSING", "steps": None, "tokens": {"total": None}})
        b = messages.get(r, {"outcome": "MISSING", "steps": None, "tokens": {"total": None}})
        lines.append(f"{r[:27]:<28} {_fmt_cell(a):<30} {_fmt_cell(b):<30}")
    lines += ["-" * 92, _aggregate(blob, la), _aggregate(messages, lb), "=" * 92,
              "note: turns-to-DONE is over DONE repos only; a lower value at equal DONE-rate = better "
              "turn economy. Compare pass-rate/EBSR/ESSR from the benchmark aggregator separately —"
              " this tool reads only the react trace + token log."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare two react-arm runs (blob vs messages).")
    ap.add_argument("blob_dir", help="dir of the REACT_PROMPT_STYLE=blob run (per-repo *.jsonl [+ *.log])")
    ap.add_argument("messages_dir", help="dir of the REACT_PROMPT_STYLE=messages run")
    ap.add_argument("--labels", default="blob,messages", help="comma-separated labels for the two arms")
    args = ap.parse_args(argv)
    labels = tuple((args.labels.split(",") + ["a", "b"])[:2])
    print(render(collect_run(args.blob_dir), collect_run(args.messages_dir), labels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
