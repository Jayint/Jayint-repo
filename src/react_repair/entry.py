"""Production entry for the react arm (spec §14). Builds the arm's OWN docker adapters over the
shared Sandbox and assembles planner + loop. `certify` runs install-tier layers only (no TESTS
layer) so the suite is not run twice; `run_tests` is the single authoritative pytest run fed
through the 80% verdict. No arm-C imports."""
from __future__ import annotations

import os
from typing import Any

from python_deps.depgraph.certify import EXECUTION_LAYER_ORDER, certify_all
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.schema import Layer
from src.envstate.constants import VERIFY_TEST_CMD           # shared canonical "python -m pytest -q"
from src.react_repair.gate import test_verdict
from src.react_repair.history import History
from src.react_repair.log import ReactLog
from src.react_repair.loop import RunResult, run_react
from src.react_repair.planner import ReactPlanner

# Install-tier layers only — drop TESTS so certify never re-runs the suite (spec §5).
_INSTALL_LAYERS = tuple(l for l in EXECUTION_LAYER_ORDER if l is not Layer.TESTS)

# Hard cap on a single pytest run so a hanging suite can't stall the whole benchmark.
_TEST_TIMEOUT_S = int(os.getenv("REACT_TEST_TIMEOUT", "600"))


class _ExecAdapter:
    """certify_all wants an executor.run(cmd) -> CommandResult; wrap the sandbox's (rc,out)."""
    def __init__(self, exec_readonly):
        self._e = exec_readonly

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        rc, out = self._e(command)
        return CommandResult(command, rc, out if rc == 0 else "", "" if rc == 0 else out)


def docker_adapters(sandbox, test_threshold: float = 0.9):
    def reset():
        sandbox.reset_to_base()

    def run_script(script: str) -> RunResult:
        r = sandbox.run_install_script(script)
        return RunResult(ok=(r.rc == 0), failing_command=r.failing_command, output=r.stderr or "")

    def certify(graph):
        return certify_all(graph, _ExecAdapter(sandbox.exec_readonly), layer_order=_INSTALL_LAYERS)

    def exec_readonly(cmd):
        return sandbox.exec_readonly(cmd)

    def run_tests():
        # Bound the suite with coreutils `timeout` (SIGTERM at the cap, SIGKILL 10s later) so a
        # hanging test can't stall the run; fall back to unbounded only where `timeout` is absent.
        cmd = (f"if command -v timeout >/dev/null 2>&1; then "
               f"timeout -k 10 {_TEST_TIMEOUT_S} {VERIFY_TEST_CMD}; else {VERIFY_TEST_CMD}; fi")
        rc, out = sandbox.exec_readonly(cmd)
        if rc == 124:                      # timeout killed pytest — surface it as a repair signal
            out = f"{out or ''}\n[react] TIMEOUT: pytest exceeded {_TEST_TIMEOUT_S}s and was killed."
        return test_verdict(out, threshold=test_threshold)

    return reset, run_script, certify, exec_readonly, run_tests


def _make_compressor(client: Any, model: str):
    """Tier-2 reflective compressor: summarize an old observation via the LLM."""
    from src.envstate.llm_response import complete_with_retry

    def compress(target, _context) -> str:
        messages = [
            {"role": "system", "content": "Summarize this build/test output in 2-3 lines, keeping "
                                          "the exact error and any missing package/library names."},
            {"role": "user", "content": target.observation_raw[:8000]},
        ]
        text, _usage, _raw = complete_with_retry(client, model, messages, temperature=0)
        return f"[summary of step {target.step_id}] {text.strip()}"

    return compress


def run_react_arm(graph, *, sandbox, client, model, repo_path=None,
                  graph_context: bool = False, trace_out=None, log=None, max_steps: int = 30,
                  test_threshold: float = 0.9):
    owns_log = log is None
    log = log or ReactLog(trace_path=trace_out)
    reset, run_script, certify, exec_readonly, run_tests = docker_adapters(sandbox, test_threshold)
    ctx = None                     # graph-guided variant (Task-future): build a graph_context fn
    planner = ReactPlanner(client, model, graph_context=(ctx if graph_context else None), log=log)
    history = History(compressor=_make_compressor(client, model), log=log)
    try:
        return run_react(graph, reset=reset, run_script=run_script, certify=certify,
                         exec_readonly=exec_readonly, run_tests=run_tests, planner=planner,
                         history=history, log=log, max_steps=max_steps)
    finally:
        if owns_log:
            log.close()
