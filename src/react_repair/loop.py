"""run_react — the flat ReAct loop (spec §2). Reset → run whole script → certify (install-tier)
→ if green + tests ≥80%, DONE. Else the planner emits ONE move: EXPLORE (read-only, a free turn,
no re-run) or PATCH (replace the script, reset + re-run). All adapters injected → Docker-free."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.patch_gate import is_read_only
from src.react_repair.script_prep import strip_graph_framing

_FORMAT_REMINDER = ("Respond with Thought + exactly one `Action: <read-only cmd>` or "
                    "`Script:` + one fenced ```bash block. No prose-only replies.")


@dataclass(frozen=True)
class RunResult:
    ok: bool
    failing_command: str | None = None
    output: str = ""


def _observation(result: RunResult, test) -> str:
    if not result.ok:
        return f"BUILD FAILED at `{result.failing_command}`:\n{result.output}"
    return f"BUILD OK. TESTS {test.passed}/{test.executed} passed:\n{test.output}"


def run_react(graph, *, reset, run_script, certify, exec_readonly, run_tests, planner,
              history, log, max_steps: int = 30, _initial_script: str | None = None):
    # Seed from the graph, but strip the graph-primary framing: the react agent edits this
    # script, so it must not carry "DO NOT EDIT / edit the graph and re-render" (spec §9/§14).
    script = (_initial_script if _initial_script is not None
              else strip_graph_framing(render_build_script(graph)))

    def rerun(s):
        reset()
        log.d("RUN", f"running {len(s.splitlines())}-line build script from base")
        r = run_script(s)
        g = certify(graph)
        log.d("CERTIFY", "install-tier node states refreshed" if r.ok else f"build failed: {r.failing_command}")
        log.trace("run", script_len=len(s.splitlines()), ok=r.ok,
                  failing_command=r.failing_command, output_tail=(r.output or "")[-500:])
        return r, g

    result, graph = rerun(script)
    test = None
    for step in range(max_steps):
        if result.ok:
            if test is None:
                test = run_tests()
                log.d("TEST_GATE", f"{test.passed}/{test.executed} passed → {'ok' if test.ok else 'below 80%'}")
                log.trace("test", passed=test.passed, executed=test.executed, ok=test.ok,
                          output_tail=(test.output or "")[-500:])
            if test.ok:
                log.d("DONE", "build green AND tests ≥80% — host-verified")
                log.trace("end", outcome="DONE", steps=step + 1); log.summary()
                return "DONE", script, graph
        observation = _observation(result, test)

        thought, action, _usage = planner.plan(history, script, observation, graph)

        if action.kind == "explore" and action.command and is_read_only(action.command):
            rc, out = exec_readonly(action.command)
            history.record(step, thought, f"explore: {action.command}", out)
            log.d("EXPLORE", f"{action.command} → rc{rc} (read-only)")
            continue                                    # same container/result — a free turn
        if action.kind == "patch" and action.new_script:
            script = action.new_script
            history.record(step, thought, "patch", "(replaced build script)")
            log.d("PATCH", "agent replaced setup.sh; re-running fresh")
            result, graph = rerun(script)
            test = None                                 # invalidate cached test result
            continue
        history.record(step, thought, "invalid", _FORMAT_REMINDER)   # explore-not-readonly or unparseable
        log.d("PLAN", f"invalid move ({action.kind}) — re-prompting")
    log.d("GIVEUP", f"max_steps {max_steps} hit — returning best-effort script")
    log.trace("end", outcome="GIVEUP", steps=max_steps); log.summary()
    return "GIVEUP", script, graph
