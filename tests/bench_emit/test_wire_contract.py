from __future__ import annotations

import json
import os

from src.bench_emit.emit import emit_run
from bench.harvest import discover


def _write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(s)


def test_emit_output_is_harvestable_by_bench(tmp_path):
    # a minimal v3 run: eval_build/Dockerfile that clones to /testbed + setup.sh + _meta.json
    repo = str(tmp_path / "run" / "output" / "pallets" / "click")
    _write(os.path.join(repo, "eval_build", "Dockerfile"),
           "FROM python:3.11-slim\nRUN git clone --depth=1 https://github.com/pallets/click /testbed\nWORKDIR /testbed\nCOPY setup.sh /tmp/s\nRUN bash /tmp/s\n")
    _write(os.path.join(repo, "eval_build", "setup.sh"), "pip install -e .\n")
    _write(os.path.join(repo, "_meta.json"),
           json.dumps({"base_image": "python:3.11-slim", "duration_s": 100.0, "head_sha": "abc123"}))

    dest = str(tmp_path / "harvest")
    emit_run(str(tmp_path / "run"), "v3", dest)

    envs = discover({"v3": dest})
    assert len(envs) == 1
    e = envs[0]
    assert e.status == "ok" and e.repo.full_name == "pallets/click"
    assert "/testbed" in e.dockerfile and e.setup_scripts.get("setup.sh") == "pip install -e .\n"
    assert e.base_image == "python:3.11-slim" and e.meta.get("produce_s") == 100.0
