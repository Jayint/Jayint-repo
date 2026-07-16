"""Harness-owned pytest plugin for the SWE-smith pristine-image audit.
Emits ONE structured JSON via pytest hooks — no reliance on terminal-summary
regex, junit tests="N" attributes, or static counts. Per-node outcomes are
mutually exclusive. Set AUDIT_OUT to the output path.

Registered with:  pytest -p swesmith_audit_plugin   (PYTHONPATH must include this dir)
"""
import json, os

_state = {
    "collected": [],            # nodeids from pytest_collection_finish (session.items)
    "collect_reports": [],      # {nodeid, outcome} for every collectreport (incl. errors)
    "collect_errors": [],       # {nodeid, longrepr} for FAILED collectreports
    "deselected": [],           # nodeids pytest_deselected
    "phase": {},                # nodeid -> {setup,call,teardown,wasxfail}
    "exitstatus": None,
    "collected_count": None,
}


def pytest_collection_finish(session):
    _state["collected_count"] = len(session.items)
    _state["collected"] = [it.nodeid for it in session.items]


def pytest_collectreport(report):
    _state["collect_reports"].append({"nodeid": report.nodeid, "outcome": report.outcome})
    if report.outcome == "failed":
        _state["collect_errors"].append({
            "nodeid": report.nodeid,
            "longrepr": str(report.longrepr)[:800],
        })


def pytest_deselected(items):
    _state["deselected"].extend(getattr(it, "nodeid", str(it)) for it in items)


def pytest_runtest_logreport(report):
    ph = _state["phase"].setdefault(report.nodeid, {"setup": None, "call": None,
                                                    "teardown": None, "wasxfail": False})
    ph[report.when] = report.outcome
    if hasattr(report, "wasxfail"):
        ph["wasxfail"] = True


def _classify(ph):
    """Mutually-exclusive per-node outcome, mirroring pytest's own categorization."""
    setup, call, teardown = ph["setup"], ph["call"], ph["teardown"]
    wasxfail = ph["wasxfail"]
    # setup/teardown errors take precedence (pytest reports these as 'error')
    if setup == "failed":
        return "error_setup"
    if teardown == "failed":
        return "error_teardown"
    if setup == "skipped":
        return "xfailed" if wasxfail else "skipped"
    if call is None:
        # no call phase recorded (e.g. skipped at setup already handled) -> treat as error
        return "error_nocall"
    if call == "skipped":
        return "xfailed" if wasxfail else "skipped"
    if call == "passed":
        return "xpassed" if wasxfail else "passed"
    if call == "failed":
        return "failed"
    return "unknown"


def pytest_sessionfinish(session, exitstatus):
    _state["exitstatus"] = int(exitstatus)
    outcomes = {nid: _classify(ph) for nid, ph in _state["phase"].items()}
    tally = {}
    for o in outcomes.values():
        tally[o] = tally.get(o, 0) + 1
    result = {
        "exitstatus": _state["exitstatus"],
        "collected_count": _state["collected_count"],
        "collected": _state["collected"],
        "n_collect_reports": len(_state["collect_reports"]),
        "collect_errors": _state["collect_errors"],
        "n_collect_errors": len(_state["collect_errors"]),
        "deselected": _state["deselected"],
        "n_deselected": len(_state["deselected"]),
        "per_node_outcome": outcomes,
        "outcome_tally": tally,
        "passed_ids": sorted(n for n, o in outcomes.items() if o in ("passed", "xpassed")),
    }
    out = os.environ.get("AUDIT_OUT", "/tmp/audit_result.json")
    with open(out, "w") as fh:
        json.dump(result, fh)
    # brief line to stderr so we can see it in docker logs
    print("[AUDIT] wrote %s exit=%s collected=%s tally=%s"
          % (out, _state["exitstatus"], _state["collected_count"], tally))
