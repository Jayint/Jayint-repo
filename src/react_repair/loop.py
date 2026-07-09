"""run_react — the flat ReAct loop (spec §2). Reset → run whole script → certify (install-tier)
→ if green + tests ≥80%, DONE. Else the planner emits ONE move: EXPLORE (read-only, a free turn,
no re-run) or PATCH (replace the script, reset + re-run). All adapters injected → Docker-free."""
from __future__ import annotations

import os
from dataclasses import dataclass

from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.patch_gate import is_read_only
from src.react_repair.history import safety_truncate
from src.react_repair.script_prep import strip_graph_framing

_FORMAT_REMINDER = ("Respond with Thought + exactly one `Action: <read-only cmd>` or "
                    "`Script:` + one fenced ```bash block. No prose-only replies.")

# Cap the build/test log shown to the planner so a repo with a huge failure dump can't bloat
# (or overflow) the prompt every turn. Keep the TAIL — pytest's summary + last failures live
# there, which is what the model needs to diagnose.
_OBS_MAX_CHARS = int(os.getenv("REACT_OBS_MAX_CHARS", "8000"))

# Cost lever: stop early once repair stops helping. After this many CONSECUTIVE patches that
# add no net-new passing tests, we've hit the achievable ceiling for this env — repairing
# further just burns steps/LLM cost, so stop and report the best achieved (PLATEAU). Lets the
# pass threshold be strict without paying to chase an unreachable ceiling.
_PLATEAU_PATIENCE = int(os.getenv("REACT_PLATEAU_PATIENCE", "2"))


@dataclass(frozen=True)
class RunResult:
    ok: bool
    failing_command: str | None = None
    output: str = ""


def _observation(result: RunResult, test) -> str:
    if not result.ok:
        body, _ = safety_truncate(result.output or "", max_chars=_OBS_MAX_CHARS)
        return f"BUILD FAILED at `{result.failing_command}`:\n{body}"
    body, _ = safety_truncate(test.output or "", max_chars=_OBS_MAX_CHARS)
    return f"BUILD OK. TESTS {test.passed}/{test.executed} passed:\n{body}"


def _verdict(result: RunResult, test) -> str:
    """The attempt's score/verdict for the history bracket — the ONE signal the agent compares
    attempts by. Lives in `action_summary` (never truncated/compressed) so Tier-1 truncation or
    the LLM compressor can't eat it, even when the observation body is squeezed to nothing."""
    if not result.ok:
        return "BUILD FAILED"
    if test is None:
        return "BUILD OK"
    return f"{test.passed}/{test.executed}"


def _added_lines(old: str, new: str, cap: int = 3) -> str:
    """What a patch changed, for the history bracket — the ReAct 'action' half. A SET difference
    (order-free, no line numbers) so it can't go stale as the script is rewritten: patches are
    whole-script siblings from base, not a continuous chain. Blank/comment lines are ignored; a
    large rewrite collapses to a `+N/-M lines` count so the bracket stays compact."""
    def meaningful(s: str) -> list[str]:
        return [ln.strip() for ln in s.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
    old_set = set(meaningful(old))
    new_meaningful = meaningful(new)
    added = [ln for ln in new_meaningful if ln not in old_set]
    if not added:
        return ""
    if len(added) > cap:
        new_set = set(new_meaningful)
        removed = sum(1 for ln in meaningful(old) if ln not in new_set)
        return f"rewrote +{len(added)}/-{removed} lines"
    return "; ".join("+" + ln for ln in added)


def run_react(graph, *, reset, run_script, certify, exec_readonly, run_tests, planner,
              history, log, max_steps: int = 30, _initial_script: str | None = None):
    # Seed from the graph, but strip the graph-primary framing: the react agent edits this
    # script, so it must not carry "DO NOT EDIT / edit the graph and re-render" (spec §9/§14).
    script = (_initial_script if _initial_script is not None
              else strip_graph_framing(render_build_script(graph)))

    def build_and_test():
        """Reset → run the WHOLE current script fresh from base → certify (install-tier) → and,
        if the build is green, run the suite once. Returns (result, graph, test|None)."""
        reset()
        log.d("RUN", f"running {len(script.splitlines())}-line build script from base")
        r = run_script(script)
        g = certify(graph)
        log.d("CERTIFY", "install-tier node states refreshed" if r.ok else f"build failed: {r.failing_command}")
        log.trace("run", script_len=len(script.splitlines()), ok=r.ok,
                  failing_command=r.failing_command, output_tail=(r.output or "")[-500:])
        t = None
        if r.ok:
            t = run_tests()
            log.d("TEST_GATE", f"{t.passed}/{t.executed} passed → {'ok' if t.ok else 'below threshold'}")
            log.trace("test", passed=t.passed, executed=t.executed, ok=t.ok,
                      output_tail=(t.output or "")[-500:])
        return r, g, t

    best_passed = -1
    stall = 0

    def register(r, t) -> bool:
        """Fold a fresh build into the plateau counters. Returns True once repair has stalled —
        no net-new passing tests for `_PLATEAU_PATIENCE` consecutive builds."""
        nonlocal best_passed, stall
        passed_now = t.passed if (r.ok and t is not None) else 0
        if passed_now > best_passed:
            best_passed, stall = passed_now, 0
            return False
        stall += 1
        return stall >= _PLATEAU_PATIENCE

    result, graph, test = build_and_test()
    plateaued = register(result, test)                  # baseline: first build never plateaus
    # Seed history with the baseline outcome (v0) so later patches have something to compare
    # against; the verdict rides in the (never-truncated) bracket, the detail in the body.
    history.record(0, "", f"baseline → {_verdict(result, test)}", _observation(result, test))
    version = 0
    for step in range(max_steps):
        if result.ok and test is not None and test.ok:
            log.d("DONE", "build green AND tests pass the gate — host-verified")
            log.trace("end", outcome="DONE", steps=step + 1); log.summary()
            return "DONE", script, graph
        if plateaued:
            log.d("PLATEAU", f"no new tests passing in {_PLATEAU_PATIENCE} repairs "
                             f"(best {best_passed}) — stopping early")
            log.trace("end", outcome="PLATEAU", steps=step + 1, best_passed=best_passed); log.summary()
            return "PLATEAU", script, graph
        observation = _observation(result, test)

        thought, action, _usage = planner.plan(history, script, observation, graph)

        if action.kind == "explore" and action.command and is_read_only(action.command):
            rc, out = exec_readonly(action.command)
            history.record(step + 1, thought, f"explore: {action.command}", out)
            log.d("EXPLORE", f"{action.command} → rc{rc} (read-only)")
            continue                                    # free turn — no rebuild, plateau unchanged
        if action.kind == "patch" and action.new_script:
            old_script, script = script, action.new_script
            log.d("PATCH", "agent replaced setup.sh; re-running fresh")
            result, graph, test = build_and_test()
            plateaued = register(result, test)
            version += 1
            # Record the patch's ReAct pair in the (never-truncated) bracket: WHAT it changed
            # (order-free set diff) → the REAL build/test outcome. Both survive compaction.
            change = _added_lines(old_script, script)
            verdict = _verdict(result, test)
            summary = f"patch v{version} ({change}) → {verdict}" if change else f"patch v{version} → {verdict}"
            history.record(step + 1, thought, summary, _observation(result, test))
            continue
        history.record(step + 1, thought, "invalid", _FORMAT_REMINDER)  # explore-not-readonly or unparseable
        log.d("PLAN", f"invalid move ({action.kind}) — re-prompting")

    outcome = "PLATEAU" if plateaued else "GIVEUP"      # plateau on the final step lands here
    log.d(outcome, f"stopped at max_steps {max_steps} (best {best_passed}) — best-effort script")
    log.trace("end", outcome=outcome, steps=max_steps, best_passed=best_passed); log.summary()
    return outcome, script, graph
