"""Production entry for the react arm (spec §14). Builds the arm's OWN docker adapters over the
shared Sandbox and assembles planner + loop. `certify` runs install-tier layers only (no TESTS
layer) so the suite is not run twice; `run_tests` is the single authoritative pytest run fed
through the 80% verdict. No arm-C imports."""
from __future__ import annotations

import os

from python_deps.depgraph.certify import EXECUTION_LAYER_ORDER, certify_all
from python_deps.depgraph.executor import CommandResult
from python_deps.depgraph.schema import Layer
from src.envstate.constants import VERIFY_TEST_CMD           # shared canonical "python -m pytest -q"
from src.react_repair.gate import test_verdict
from src.react_repair.history import History
from src.react_repair.log import ReactLog
from src.react_repair.loop import RunResult, run_react
from src.react_repair.planner import ReactPlanner
from src.react_repair.script_prep import strip_graph_framing

# Install-tier layers only — drop TESTS so certify never re-runs the suite (spec §5).
_INSTALL_LAYERS = tuple(l for l in EXECUTION_LAYER_ORDER if l is not Layer.TESTS)

# Hard cap on a single pytest run so a hanging suite can't stall the whole benchmark. Matches the
# eval harness cap (1800s) so a WORKING-but-slow seed isn't read as false-0 by a harsher internal
# cap than the eval applies — that false-0 was what drove the agent to gut a working closure (darts).
# Override with REACT_TEST_TIMEOUT.
_TEST_TIMEOUT_S = int(os.getenv("REACT_TEST_TIMEOUT", "1800"))


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
        return RunResult(ok=(r.rc == 0), failing_command=r.failing_command, output=r.stderr or "",
                         lineno=r.lineno)

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
        if rc in (124, 137):               # 124 = SIGTERM at the cap, 137 = SIGKILL 10s later
            # Surface the timeout as a DISTINCT signal + an explicit anti-strip hint: a timeout means
            # the env may be fine but the suite is slow, so removing installs to "fix" it only makes
            # things worse (the darts failure). Appended last so it survives tail-truncation.
            out = (f"{out or ''}\n[react] TIMEOUT: pytest exceeded {_TEST_TIMEOUT_S}s and was killed. "
                   f"The environment may be correctly set up but the suite is just too slow to finish "
                   f"in the cap — do NOT remove installs to make it faster (a smaller env passes fewer "
                   f"tests, not more).")
        return test_verdict(out, threshold=test_threshold)

    return reset, run_script, certify, exec_readonly, run_tests


def run_react_arm(graph, *, sandbox, client, model, repo_path=None,
                  graph_context: bool = False, trace_out=None, log=None, max_steps: int = 30,
                  test_threshold: float = 0.9, initial_script: str | None = None):
    owns_log = log is None
    log = log or ReactLog(trace_path=trace_out)
    reset, run_script, certify, exec_readonly, run_tests = docker_adapters(sandbox, test_threshold)
    ctx = None                     # graph-guided variant (Task-future): build a graph_context fn
    planner = ReactPlanner(client, model, graph_context=(ctx if graph_context else None), log=log)
    # No LLM observation compressor: the grouped blocker history view (render_history) is the
    # compaction now — it distills each step to a blocker signature + score, and never renders the
    # per-step body, so a Tier-2 summariser would only fire wasted LLM calls whose output is unused.
    history = History(log=log)
    # Seed step-0 from a pre-generated setup.sh (repair-only ablation); strip the graph-primary
    # header so a copy from a prior v3 run doesn't carry "DO NOT EDIT / edit the graph" contradictions.
    seed = strip_graph_framing(initial_script) if initial_script is not None else None
    try:
        return run_react(graph, reset=reset, run_script=run_script, certify=certify,
                         exec_readonly=exec_readonly, run_tests=run_tests, planner=planner,
                         history=history, log=log, max_steps=max_steps, _initial_script=seed)
    finally:
        if owns_log:
            log.close()
