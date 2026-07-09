import sys, json, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.react_repair.planner as planner_mod
from src.react_repair.log import ReactLog
from src.react_repair.planner import ReactPlanner
from src.react_repair.history import History


def test_trace_kept_and_written(tmp_path):
    log = ReactLog(silent=True, trace_path=str(tmp_path / "t.jsonl"))
    log.d("PLAN", "x"); log.d("PLAN", "y"); log.trace("run", rc=0, ok=True)
    log.close()
    assert "PLAN×2" in log.summary()
    assert json.loads((tmp_path / "t.jsonl").read_text().strip())["phase"] == "run"

def test_planner_emits_plan_record_with_prompt(monkeypatch):
    monkeypatch.setattr(planner_mod, "complete_with_tools",
                        lambda *a, **k: ([("explore", '{"command":"ls"}')], "reasoning",
                                         {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "resp"))
    log = ReactLog(silent=True)
    ReactPlanner(object(), "m", log=log).plan(History(), "script", "obs", graph=None)
    rec = next(r for r in log.records if r["phase"] == "plan")
    assert "prompt" in rec and rec["action"]["kind"] == "explore" and rec["observation"] == "obs"

def test_history_emits_compress_record():
    log = ReactLog(silent=True)
    h = History(compress_delay=1, compress_threshold_chars=10, compressor=lambda t, c: "SUM", log=log)
    h.record(1, "t", "explore", "B" * 50)
    h.record(2, "t", "explore", "small")            # step 1 now past the delay → compressed
    rec = next(r for r in log.records if r["phase"] == "compress")
    assert rec["raw_chars"] == 50 and rec["summary_chars"] == 3
