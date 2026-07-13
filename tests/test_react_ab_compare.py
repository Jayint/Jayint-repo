"""Parser tests for the react A/B compare tool — grounded in the real trace schema
({"phase":"end","outcome","steps"}) and the real token line ([Tokens] Input/Output/Total)."""
import json
import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import react_ab_compare as ab


def _write_trace(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_parse_trace_reads_end_outcome_and_steps(tmp_path):
    t = tmp_path / "typer.jsonl"
    _write_trace(t, [{"phase": "run", "ok": False}, {"phase": "plan"}, {"phase": "plan"},
                     {"phase": "end", "outcome": "DONE", "steps": 4}])
    row = ab.parse_trace(str(t))
    assert row["outcome"] == "DONE" and row["steps"] == 4 and row["llm_calls"] == 2


def test_parse_trace_missing_end_is_visible_not_dropped(tmp_path):
    t = tmp_path / "crashed.jsonl"
    _write_trace(t, [{"phase": "run", "ok": False}, {"phase": "plan"}])
    row = ab.parse_trace(str(t))
    assert row["outcome"] == "NO_END" and row["steps"] is None and row["llm_calls"] == 1


def test_parse_trace_tolerates_corrupt_lines(tmp_path):
    t = tmp_path / "partial.jsonl"
    t.write_text('{"phase":"plan"}\nnot json at all\n{"phase":"end","outcome":"GIVEUP","steps":30}\n',
                 encoding="utf-8")
    row = ab.parse_trace(str(t))
    assert row["outcome"] == "GIVEUP" and row["steps"] == 30


def test_parse_tokens_sums_all_llm_calls(tmp_path):
    log = tmp_path / "typer.log"
    log.write_text("boot\n[Tokens] Input: 100, Output: 20, Total: 120\n"
                   "some stdout\n[Tokens] Input: 200, Output: 30, Total: 230\n", encoding="utf-8")
    tok = ab.parse_tokens(str(log))
    assert tok == {"input": 300, "output": 50, "total": 350}


def test_parse_tokens_absent_log_is_none(tmp_path):
    assert ab.parse_tokens(str(tmp_path / "nope.log")) == {"input": None, "output": None, "total": None}


def test_collect_run_pairs_trace_and_log_by_stem(tmp_path):
    _write_trace(tmp_path / "click.jsonl", [{"phase": "end", "outcome": "DONE", "steps": 2}])
    (tmp_path / "click.log").write_text("[Tokens] Input: 10, Output: 5, Total: 15\n", encoding="utf-8")
    run = ab.collect_run(str(tmp_path))
    assert run["click"]["outcome"] == "DONE" and run["click"]["tokens"]["total"] == 15


def test_render_shows_both_arms_and_aggregate(tmp_path):
    blob = {"typer": {"outcome": "GIVEUP", "steps": 30, "llm_calls": 30, "tokens": {"total": 500_000}}}
    msgs = {"typer": {"outcome": "DONE", "steps": 6, "llm_calls": 6, "tokens": {"total": 90_000}}}
    out = ab.render(blob, msgs, ("blob", "messages"))
    assert "typer" in out and "DONE" in out and "GIVEUP" in out
    assert "DONE=0" in out and "DONE=1" in out            # aggregate DONE counts per arm
    assert "turns-to-DONE=6.0" in out                     # messages arm's economy surfaced
