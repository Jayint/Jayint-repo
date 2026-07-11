"""Own pytest collection plugin — structured node-IDs via hooks, no stdout regex.

Register with:  pytest -p manifest_collect_plugin   (this file's dir on PYTHONPATH)
Writes JSON to $MANIFEST_COLLECT_OUT (default /tmp/manifest_collect.json).
Collection-only: no runtest hooks.
"""
import json
import os

_state = {"collected": [], "collect_errors": [], "skipped_modules": [], "deselected": [],
          "exit_status": None}


def pytest_collection_finish(session):
    _state["collected"] = [it.nodeid for it in session.items]


def pytest_collectreport(report):
    if report.outcome == "failed":
        _state["collect_errors"].append(report.nodeid)
    elif report.outcome == "skipped":
        _state["skipped_modules"].append(report.nodeid)


def pytest_deselected(items):
    _state["deselected"].extend(getattr(it, "nodeid", str(it)) for it in items)


def pytest_sessionfinish(session, exitstatus):
    _state["exit_status"] = int(exitstatus)
    result = {
        "exit_status": _state["exit_status"],
        "collected": _state["collected"],
        "collected_count": len(_state["collected"]),
        "collect_errors": _state["collect_errors"],
        "skipped_modules": _state["skipped_modules"],
        "deselected": _state["deselected"],
    }
    out = os.environ.get("MANIFEST_COLLECT_OUT", "/tmp/manifest_collect.json")
    with open(out, "w") as fh:
        json.dump(result, fh)
