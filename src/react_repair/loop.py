"""run_react — the flat ReAct loop (spec §2). Reset → run whole script → certify (install-tier)
→ if green + tests ≥80%, DONE. Else the planner emits ONE move: EXPLORE (read-only, a free turn,
no re-run) or PATCH (replace the script, reset + re-run). All adapters injected → Docker-free."""
from __future__ import annotations

import os
from dataclasses import dataclass

from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.patch_gate import is_read_only
from src.react_repair.actions import apply_edit
from src.react_repair.history import safety_truncate
from src.react_repair.script_prep import strip_graph_framing

# Shown to the agent when a move is rejected (a non-read-only "explore", or an otherwise unusable
# action). Tool-calling aware — the old text referenced the retired `Action:`/`Script:` free-text
# syntax, which gave the agent no usable direction (azure kept trying `pip install` inside explore).
_FORMAT_REMINDER = ("Call exactly one tool — explore or edit. explore is READ-ONLY (ls/cat/find/"
                    "pip show); a package you install inside explore is gone next turn and won't "
                    "persist. To install a dependency or change the environment, add the line to "
                    "setup.sh with edit() instead.")
_OUT_OF_RANGE_HINT = "edit line out of range — check the numbered setup.sh and retry"

# A tool misuse (non-read-only explore, out-of-range edit, unusable action) is a harness error, not a
# repair step: re-prompt with the reason IN PLACE (no turn spent, nothing written to the agent-facing
# history) up to this many times. Only if the agent still can't produce a valid call do we fall back
# to recording one invalid step and moving on. Every misuse is still traced for the misuse-rate metric.
_INVALID_RETRIES = int(os.getenv("REACT_INVALID_RETRIES", "2"))

# Cap the build/test log shown to the planner so a repo with a huge failure dump can't bloat
# (or overflow) the prompt every turn. Keep the TAIL — pytest's summary + last failures live
# there, which is what the model needs to diagnose.
_OBS_MAX_CHARS = int(os.getenv("REACT_OBS_MAX_CHARS", "8000"))

# Cost lever: stop early once repair stops helping. After this many CONSECUTIVE patches that
# add no net-new passing tests, we've hit the achievable ceiling for this env — repairing
# further just burns steps/LLM cost, so stop and report the best achieved (PLATEAU). Lets the
# pass threshold be strict without paying to chase an unreachable ceiling.
# Set to 5 (was 2): now that keep-best/seed-floor makes an early stop SAFE (the loop always
# returns best_script, never worse than the seed), a genuine multi-step repair deserves more than
# two attempts before we give up — 2 cut real fixes short. A stuck repo still auto-cuts at 5 rather
# than thrashing the full step budget, so the ~service-gated repos (unrepairable by a script edit)
# don't burn 30 turns for zero gain.
_PLATEAU_PATIENCE = int(os.getenv("REACT_PLATEAU_PATIENCE", "5"))


@dataclass(frozen=True)
class RunResult:
    ok: bool
    failing_command: str | None = None
    output: str = ""
    lineno: int | None = None          # setup.sh line the ERR trap halted on (localization signal)


def _emit_tokens(usage) -> None:
    """One `[Tokens] Input: I, Output: O, Total: T` line per LLM call, in the format the ratbench
    run.log telemetry parses (unified_metrics.TOKENS_RE). The adapter relays these from the react
    loop's (otherwise swallowed) stdout into the per-repo run.log, where the arm0/dockeragent
    economy reads them. No-op when usage is absent (offline/stubbed clients emit none)."""
    if not usage:
        return
    i = int(usage.get("input_tokens", 0) or 0)
    o = int(usage.get("output_tokens", 0) or 0)
    t = int(usage.get("total_tokens", 0) or 0) or (i + o)
    if i or o or t:
        print(f"[Tokens] Input: {i}, Output: {o}, Total: {t}", flush=True)


def _observation(result: RunResult, test) -> str:
    if not result.ok:
        body, _ = safety_truncate(result.output or "", max_chars=_OBS_MAX_CHARS)
        loc = f" (line {result.lineno})" if result.lineno else ""
        return f"BUILD FAILED at `{result.failing_command}`{loc}:\n{body}"
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


def _edit_summary(op) -> str:
    """Describe a line-anchored edit for the history bracket, straight from the op (verb + line span
    + a content preview) rather than a whole-script diff. Edits form a CHAIN now (not whole-script
    siblings), and the line numbers are stable and shown, so `insert@23 +pip install X` / `delete@55`
    is more precise than a set-diff — and it captures deletes, which an additions-only diff misses."""
    span = f"{op.start}" if op.end == op.start else f"{op.start}-{op.end}"
    if op.verb == "delete":
        return f"delete@{span}"
    body = [ln for ln in (op.content or "").splitlines() if ln.strip()]
    preview = (body[0].strip()[:60] if body else "")
    extra = f" (+{len(body) - 1})" if len(body) > 1 else ""
    sign = "+" if op.verb == "insert" else ""
    return f"{op.verb}@{span} {sign}{preview}{extra}".rstrip()


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


def _classify_action(action, script: str) -> tuple[str, str | None]:
    """Map a planned action to how the loop should dispatch it: ("explore", command) /
    ("edit", new_script) / ("patch", new_script) for a usable move, or ("invalid", hint) for a tool
    misuse. Edit validity requires computing the splice, so the new script rides back in the payload."""
    if action.kind == "explore":
        if action.command and is_read_only(action.command):
            return "explore", action.command
        return "invalid", _FORMAT_REMINDER              # non-read-only or empty command
    if action.kind == "edit" and action.edit is not None:
        new = apply_edit(script, action.edit)
        return ("edit", new) if new is not None else ("invalid", _OUT_OF_RANGE_HINT)
    if action.kind == "patch" and action.new_script:
        return "patch", action.new_script
    return "invalid", _FORMAT_REMINDER                  # unusable / unparseable move


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
                  failing_command=r.failing_command, lineno=r.lineno,
                  output_tail=(r.output or "")[-500:])
        t = None
        if r.ok:
            t = run_tests()
            log.d("TEST_GATE", f"{t.passed}/{t.executed} passed → {'ok' if t.ok else 'below threshold'}")
            log.trace("test", passed=t.passed, executed=t.executed, ok=t.ok,
                      output_tail=(t.output or "")[-500:])
        return r, g, t

    best_key: tuple[bool, int, int] = (False, -1, -1)   # (built_ok, passed, executed): green > failed;
    best_script = script                                 # among passed-ties, MORE tests collected wins
    stall = 0

    def register(r, t) -> bool:
        """Fold a fresh build into the best-so-far + plateau counters. Tracks the best SCRIPT (not
        just its pass count) so a later regressing patch can never be what we ship: every non-DONE
        exit returns `best_script`, making repair structurally incapable of doing worse than the seed
        (the observed regression was PLATEAU/GIVEUP returning the last, often-broken, patch). Ranks by
        (built, passed, executed): a green build beats a failed one, then more passing tests, then —
        crucially, when passed ties (often at 0) — more tests EXECUTED. That last term keeps a fix
        that unblocks collection (fewer collection errors → more tests runnable) even before any test
        passes, instead of discarding it as no-gain (the M3 baserow regression). Returns True once
        repair has stalled — no net-new best for `_PLATEAU_PATIENCE` consecutive builds."""
        nonlocal best_key, best_script, stall
        passed_now = t.passed if (r.ok and t is not None) else 0
        executed_now = t.executed if (r.ok and t is not None) else 0
        key = (bool(r.ok), passed_now, executed_now)
        if key > best_key:
            best_key, best_script, stall = key, script, 0   # `script` = the one just built
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
                             f"(best {best_key[1]}) — stopping early, returning best script")
            log.trace("end", outcome="PLATEAU", steps=step + 1, best_passed=best_key[1]); log.summary()
            return "PLATEAU", best_script, graph
        observation = _observation(result, test)

        # Resolve a DISPATCHABLE action. A tool misuse is re-prompted IN PLACE (fail_lineno anchors
        # "← BUILD HALTED HERE"; the rejection reason rides in the next prompt) without spending the
        # turn or writing to the agent-facing history — bounded by _INVALID_RETRIES. Only if the agent
        # can't produce a valid call do we fall back to recording one invalid step and moving on.
        rejection = None
        for attempt in range(_INVALID_RETRIES + 1):
            thought, action, usage = planner.plan(history, script, observation, graph,
                                                  fail_lineno=result.lineno, turn=step + 1,
                                                  max_turns=max_steps, rejection=rejection)
            _emit_tokens(usage)
            kind, payload = _classify_action(action, script)
            if kind != "invalid":
                break
            rejection = payload
            log.d("PLAN", f"invalid move ({action.kind}) — retry {attempt + 1}/{_INVALID_RETRIES}")
            log.trace("invalid", attempt=attempt + 1, kind=action.kind, command=action.command,
                      reason=payload, thought=thought)
        else:
            # Cap exhausted — the agent keeps misusing the tools. Record ONE invalid step (visible +
            # already traced above) and consume the turn so we can't spin here forever.
            history.record(step + 1, thought, "invalid", rejection)
            log.d("PLAN", f"invalid after {_INVALID_RETRIES} retries — recording, moving on")
            continue

        if kind == "explore":
            rc, out = exec_readonly(payload)
            history.record(step + 1, thought, f"explore: {payload}", out)
            log.d("EXPLORE", f"{payload} → rc{rc} (read-only)")
            continue                                    # free turn — no rebuild, plateau unchanged
        if kind == "edit":
            # A line-anchored edit (pure splice, validated in _classify_action). Same rebuild+register
            # +record path as a patch, so keep-best guards a bad edit exactly as it guards a bad patch.
            old_script, script = script, payload
            change = _edit_summary(action.edit)   # op-based: verb@span + preview (captures deletes too)
            log.d("EDIT", f"{change}; re-running fresh")
            result, graph, test = build_and_test()
            plateaued = register(result, test)
            version += 1
            verdict = _verdict(result, test)
            history.record(step + 1, thought, f"edit v{version} ({change}) → {verdict}",
                           _observation(result, test))
            continue
        # kind == "patch"
        old_script, script = script, payload
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

    outcome = "PLATEAU" if plateaued else "GIVEUP"      # plateau on the final step lands here
    log.d(outcome, f"stopped at max_steps {max_steps} (best {best_key[1]}) — returning best script")
    log.trace("end", outcome=outcome, steps=max_steps, best_passed=best_key[1]); log.summary()
    return outcome, best_script, graph
