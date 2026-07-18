"""run_react — the flat ReAct loop (spec §2). Reset → run whole script → certify (install-tier)
→ if green + tests ≥80%, DONE. Else the planner emits ONE move: EXPLORE (read-only, a free turn,
no re-run) or PATCH (replace the script, reset + re-run). All adapters injected → Docker-free."""
from __future__ import annotations

import os
from dataclasses import dataclass

from graph.emit.build_script import render_build_script
from graph.mutate.patch_gate import is_read_only
from src.constants import VERIFY_TEST_CMD          # the canonical `python -m pytest -q` (neutral leaf)
from src.agent.actions import apply_edit
from src.agent.gate import added_self_install_reason, narrowing_reason
from src.agent.observe import safety_compress_observation, strip_pip_progress
from src.agent.observe import compact_pytest_blocks
from src.agent.observe import format_breakdown, summarize
from src.agent.actions.script import strip_graph_framing
from src.agent.history import agentic

# Shown to the agent when a move is rejected (a non-read-only "explore", or an otherwise unusable
# action). Tool-calling aware — the old text referenced the retired `Action:`/`Script:` free-text
# syntax, which gave the agent no usable direction (azure kept trying `pip install` inside explore).
_FORMAT_REMINDER = ("Call exactly one tool — explore or edit. explore is READ-ONLY (ls/cat/find/"
                    "pip show); a package you install inside explore is gone next turn and won't "
                    "persist. To install a dependency or change the environment, add the line to "
                    "setup.sh with edit() instead.")
_OUT_OF_RANGE_HINT = "edit line out of range — check the numbered setup.sh and retry"


def _gaming_hint(reason: str) -> str:
    """Rejection message for an edit that would shrink what pytest collects. Names the specific
    narrowing so the agent redirects to a REAL fix instead of hiding the failing tests."""
    return (f"that edit hides tests instead of fixing the environment ({reason}) — rejected. "
            "Don't narrow what pytest collects: no --ignore/testpaths/--deselect/-k, no conftest "
            "skips, no removing test files. Fix the real cause so the tests can RUN; if a dependency "
            "or service genuinely can't be provided, leave those tests failing.")


def _self_install_hint(reason: str) -> str:
    """Rejection message for installing the project under test from a package index. Names the real
    fix (install the checkout) so the agent redirects instead of re-trying the same shortcut."""
    return (f"that edit {reason} — rejected. The published package would SHADOW the source in this "
            "repo, so the suite would pass against code that isn't here — a fake pass. Install the "
            "local checkout instead: `pip install -e .` (add the repo's extras if it declares them, "
            "e.g. `pip install -e .[test]`), and install the repo's OTHER dependencies normally.")

# A tool misuse (non-read-only explore, out-of-range edit, unusable action) is a harness error, not a
# repair step: re-prompt with the reason IN PLACE (no turn spent, nothing written to the agent-facing
# history) up to this many times. Only if the agent still can't produce a valid call do we fall back
# to recording one invalid step and moving on. Every misuse is still traced for the misuse-rate metric.
_INVALID_RETRIES = int(os.getenv("REACT_INVALID_RETRIES", "2"))

# Cap the build/test log shown to the planner so a repo with a huge failure dump can't bloat
# (or overflow) the prompt every turn. safety_compress KEEPS error blocks + status lines and drops
# download/apt noise within this budget — a signal-dense cut, not a blind head+tail truncation.
_OBS_MAX_CHARS = int(os.getenv("REACT_OBS_MAX_CHARS", "8000"))

# Observation presentation mode (ablation lever, orthogonal to the graph axis). `compress` (DEFAULT):
# the REAL pytest output, safety_compress'd — honest stdout/stderr with noise stripped, no synthesized
# lines. `histogram` (opt-in): prepend the ranked pytest error-type breakdown (a synthesized cause
# aggregate) before the compressed output — kept so the histogram can be A/B'd as its own rung.
_OBS_MODE = os.getenv("REACT_OBS_MODE", "compress").lower()


def _obs_body(text: str, cap: "int | None" = None) -> str:
    """The real build/test output, safety_compress'd to *cap* chars (default _OBS_MAX_CHARS): drops
    download/apt progress noise, keeps error blocks + status/summary lines + head/tail. This is the
    single compaction — no synthesized histogram. message_view re-compresses this stored body per the
    recency tier (immediate near-full, history leaner), all from the one raw.

    Under the agentic bundle, pip chatter and duplicate pytest error blocks are removed BEFORE the
    compress — see style.py: doing it after would let 12 copies of one cause crowd out a different
    cause that then never reaches the prompt at all."""
    body = text or ""
    if agentic():
        body = compact_pytest_blocks(strip_pip_progress(body))
    body, _ = safety_compress_observation(
        body, threshold_chars=(cap or _OBS_MAX_CHARS), target_chars=(cap or _OBS_MAX_CHARS))
    return body


def _test_header(test) -> str:
    """`BUILD OK. TESTS p/e passed[, C collected].` — the build-phase signal (not in pytest output),
    the gate denominator (executed = passed+failed+errors), and the collected population when it
    differs (a collected≫executed gap = silently skipped tests). Also the signature anchor parsed by
    history_view.extract_blocker, so the `BUILD OK. TESTS p/e` prefix is kept exact."""
    base = f"BUILD OK. TESTS {test.passed}/{test.executed} passed"
    collected = getattr(test, "collected", 0) or 0
    # Show collected only when it DIFFERS from executed — a collected>executed gap means tests were
    # skipped/deselected (the signal); when equal, the executed denominator already says it, so no clutter.
    return f"{base} ({collected} collected)." if collected and collected != test.executed else f"{base}."

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
        # Build failing → per-LINE localization (the halt marker rides in the numbered script); the
        # body is the real stderr, safety_compress'd (noise stripped, error blocks kept).
        loc = f" (line {result.lineno})" if result.lineno else ""
        return f"BUILD FAILED at `{result.failing_command}`{loc}:\n{_obs_body(result.output)}"
    # Build green → the fault is in the tests. DEFAULT: the REAL pytest output, safety_compress'd —
    # honest stdout/stderr, no synthesized lines. The ranked cause histogram is an OPT-IN lens
    # (REACT_OBS_MODE=histogram): it dedups+ranks all causes (surfacing a buried cause a head/tail
    # cut would drop) but is a derived aggregate, so it is not the default. Both keep the raw body.
    header = _test_header(test)
    if _OBS_MODE == "histogram":
        causes = summarize(test.output or "")
        if causes:
            hist = format_breakdown(causes)
            tail_budget = max(1000, _OBS_MAX_CHARS - len(hist) - 200)
            return (f"{header}\nTop failure causes (by tests affected):\n{hist}\n"
                    f"--- pytest output (tail) ---\n{_obs_body(test.output, tail_budget)}")
    return f"{header}\n{_obs_body(test.output)}"


def _outcome(result: RunResult, test) -> dict:
    """The build/test result as STRUCTURED facts, stored on the Step beside the observation text.

    The agentic view renders its `$ cmd → exit N` envelope from this rather than re-parsing the
    observation — the stored body is already safety_compress'd, so pytest's summary line is not
    guaranteed to have survived, and a render layer must never depend on that. Keeps the raw counts
    apart (passed/failed/errors/skipped) instead of only the fused gate denominator."""
    o: dict = {"build_ok": bool(result.ok), "failing_command": result.failing_command,
               "lineno": result.lineno, "ran_tests": test is not None,
               "test_command": VERIFY_TEST_CMD}
    if test is not None:
        o.update(passed=test.passed, executed=test.executed,
                 failed=getattr(test, "failed", 0), errors=getattr(test, "errors", 0),
                 skipped=getattr(test, "skipped", 0), collected=getattr(test, "collected", 0))
    return o


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


def _cheat_reject(old: str, new: str, project_name: "str | None") -> "str | None":
    """The two host anti-gaming gates, as ONE verdict on a candidate script: (a) it must not shrink
    what pytest collects, and (b) it must not install the project under test from a package index
    instead of the checkout. Returns the agent-facing rejection hint, or None if the edit is clean."""
    reason = narrowing_reason(old, new)                 # gate A: collection narrowing (Repo2Run lever)
    if reason:
        return _gaming_hint(reason)
    reason = added_self_install_reason(old, new, project_name)   # gate B: self-install (false green)
    if reason:
        return _self_install_hint(reason)
    return None


def _action_struct(action) -> "dict | None":
    """A planned action as the structured dict the message view renders byte-exact — the same shape
    the accepted path stores on Step.action. Used for a REJECTED call, so the agentic view can show
    the refusal as that call's own tool result instead of a footer bolted to the bottom of the
    workbench, three blocks away from the action it is about."""
    if action is None:
        return None
    if action.kind == "explore":
        return {"kind": "explore", "command": action.command or ""}
    if action.kind == "edit" and action.edit is not None:
        e = action.edit
        return {"kind": "edit", "verb": e.verb, "start": e.start, "end": e.end, "content": e.content}
    if action.kind == "patch":
        return {"kind": "patch", "content": action.new_script or ""}
    return None


def _classify_action(action, script: str, project_name: "str | None" = None) -> tuple[str, str | None]:
    """Map a planned action to how the loop should dispatch it: ("explore", command) /
    ("edit", new_script) / ("patch", new_script) for a usable move, or ("invalid", hint) for a tool
    misuse. Edit validity requires computing the splice, so the new script rides back in the payload."""
    if action.kind == "explore":
        if action.command and is_read_only(action.command):
            return "explore", action.command
        return "invalid", _FORMAT_REMINDER              # non-read-only or empty command
    if action.kind == "edit" and action.edit is not None:
        new = apply_edit(script, action.edit)
        if new is None:
            return "invalid", _OUT_OF_RANGE_HINT
        hint = _cheat_reject(script, new, project_name)
        return ("invalid", hint) if hint else ("edit", new)
    if action.kind == "patch" and action.new_script:
        hint = _cheat_reject(script, action.new_script, project_name)
        return ("invalid", hint) if hint else ("patch", action.new_script)
    return "invalid", _FORMAT_REMINDER                  # unusable / unparseable move


def run_react(graph, *, reset, run_script, certify, exec_readonly, run_tests, planner,
              history, log, max_steps: int = 30, _initial_script: str | None = None,
              project_name: "str | None" = None,
              enrich_fn=None, expand_fn=None, certify_new_fn=None):
    # Seed from the graph, but strip the graph-primary framing: the react agent edits this
    # script, so it must not carry "DO NOT EDIT / edit the graph and re-render" (spec §9/§14).
    script = (_initial_script if _initial_script is not None
              else strip_graph_framing(render_build_script(graph)))

    expanded: set[str] = set()   # discoveries already expanded (§7.2) — persists ACROSS turns so a
                                 # re-run of the same failure doesn't re-hit the network every turn

    def build_and_test():
        """Reset → run the WHOLE current script fresh from base → certify (install-tier) → and,
        if the build is green, run the suite once. Then (G3 only) enrich the graph from this
        turn's observations and certify what enrich appended. Returns
        (result, graph, test|None, causes, prev_states)."""
        nonlocal expanded
        reset()
        # The PREVIOUS turn's certified graph is simply `graph` as closed-over here, before this
        # call's `certify()` rebinds the local `g` — capture its states now for the "SINCE YOUR
        # LAST EDIT" delta the renderer computes next turn. `getattr(..., ())` degrades to an
        # empty delta for the G0/G1 test harness, which stands in a bare `object()` for `graph`
        # since the script-only arms never read it — the baseline must stay byte-identical.
        prev_states = {n.id: n.state for n in getattr(graph, "nodes", ())}
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
        # `causes` is empty on a build-fail turn — pytest never ran (spec §7.1). Computed here
        # (not just inside the histogram observation path) so it can anchor the graph render too.
        causes = summarize(t.output) if (t is not None and t.output) else []
        if enrich_fn is not None:                     # G3 only — read-only graphs never enter here
            # ORDER: enrich AFTER certify/run_tests, on what this turn just observed. Anything
            # appended here was never checked by the certify() call above, so it renders UNKNOWN
            # until certify_new_fn verifies it — hence the narrow second pass below.
            #
            # Certify everything BOTH steps added, not just enrich's. `expand_discovery` runs the
            # Debian build-deps prior over each new package and appends its own capability/apt
            # nodes (`binary:pg_config` and friends) — also after the certify pass. Certifying only
            # enrich's ids left exactly those nodes UNCERTIFIED, so the renderer would show the
            # agent a `★ binary:pg_config` root whose state we never actually checked. The whole
            # premise is that the agent is never shown a claim we have not verified.
            before_ids = {n.id for n in g.nodes}
            g, new_ids = enrich_fn(g, r, causes)
            g, expanded = expand_fn(g, new_ids, expanded)
            appended = [n.id for n in g.nodes if n.id not in before_ids]
            g = certify_new_fn(g, appended)
            if appended:
                log.d("ENRICH", f"discovered {len(appended)}: {', '.join(appended[:3])}")
        return r, g, t, causes, prev_states

    best_key: tuple[bool, int, int] = (False, -1, -1)   # (built_ok, passed, executed): green > failed;
    best_script = script                                 # among passed-ties, MORE tests collected wins

    def register(r, t) -> None:
        """Fold a fresh build into the best-so-far. Tracks the best SCRIPT (not just its pass count)
        so a later regressing patch can never be what we ship: every non-DONE exit returns
        `best_script`, making repair structurally incapable of doing worse than the seed. Ranks by
        (built, passed, executed): a green build beats a failed one, then more passing tests, then —
        crucially, when passed ties (often at 0) — more tests EXECUTED. That last term keeps a fix
        that unblocks collection (fewer collection errors → more tests runnable) even before any test
        passes, instead of discarding it as no-gain (the M3 baserow regression)."""
        nonlocal best_key, best_script
        passed_now = t.passed if (r.ok and t is not None) else 0
        executed_now = t.executed if (r.ok and t is not None) else 0
        key = (bool(r.ok), passed_now, executed_now)
        if key > best_key:
            best_key, best_script = key, script         # `script` = the one just built

    result, graph, test, causes, prev_states = build_and_test()
    register(result, test)                              # fold baseline into best-so-far
    # Seed history with the baseline outcome (v0) so later patches have something to compare
    # against; the verdict rides in the (never-truncated) bracket, the detail in the body.
    history.record(0, "", f"baseline → {_verdict(result, test)}", _observation(result, test),
                   outcome=_outcome(result, test))
    version = 0
    for step in range(max_steps):
        if result.ok and test is not None and test.ok:
            log.d("DONE", "build green AND tests pass the gate — host-verified")
            log.trace("end", outcome="DONE", steps=step + 1); log.summary()
            return "DONE", script, graph
        observation = _observation(result, test)

        # Resolve a DISPATCHABLE action. A tool misuse is re-prompted IN PLACE (fail_lineno anchors
        # "← BUILD HALTED HERE"; the rejection reason rides in the next prompt) without spending the
        # turn or writing to the agent-facing history — bounded by _INVALID_RETRIES. Only if the agent
        # can't produce a valid call do we fall back to recording one invalid step and moving on.
        rejection = None
        rejected = None                                 # the refused call itself (agentic render)
        for attempt in range(_INVALID_RETRIES + 1):
            thought, action, usage = planner.plan(history, script, observation, graph,
                                                  fail_lineno=result.lineno, turn=step + 1,
                                                  max_turns=max_steps, rejection=rejection,
                                                  rejected=rejected, result=result, causes=causes,
                                                  prev_states=prev_states)
            _emit_tokens(usage)
            kind, payload = _classify_action(action, script, project_name)
            if kind != "invalid":
                break
            rejection = payload
            rejected = {"thought": thought, "action": _action_struct(action), "reason": payload}
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
            history.record(step + 1, thought, f"explore: {payload}", out,
                           action={"kind": "explore", "command": payload})
            log.d("EXPLORE", f"{payload} → rc{rc} (read-only)")
            continue                                    # free turn — no rebuild
        if kind == "edit":
            # A line-anchored edit (pure splice, validated in _classify_action). Same rebuild+register
            # +record path as a patch, so keep-best guards a bad edit exactly as it guards a bad patch.
            old_script, script = script, payload
            e = action.edit
            change = _edit_summary(e)             # op-based: verb@span + preview (captures deletes too)
            log.d("EDIT", f"{change}; re-running fresh")
            result, graph, test, causes, prev_states = build_and_test()
            register(result, test)
            version += 1
            verdict = _verdict(result, test)
            # Store the FULL op (not the 60-char preview) so the message-list arm renders the edit
            # byte-exact; the action_summary preview stays for the compact blob/history view.
            history.record(step + 1, thought, f"edit v{version} ({change}) → {verdict}",
                           _observation(result, test),
                           action={"kind": "edit", "verb": e.verb, "start": e.start,
                                   "end": e.end, "content": e.content},
                           outcome=_outcome(result, test))
            continue
        # kind == "patch"
        old_script, script = script, payload
        log.d("PATCH", "agent replaced setup.sh; re-running fresh")
        result, graph, test, causes, prev_states = build_and_test()
        register(result, test)
        version += 1
        # Record the patch's ReAct pair in the (never-truncated) bracket: WHAT it changed
        # (order-free set diff) → the REAL build/test outcome. Both survive compaction.
        change = _added_lines(old_script, script)
        verdict = _verdict(result, test)
        summary = f"patch v{version} ({change}) → {verdict}" if change else f"patch v{version} → {verdict}"
        # Whole-script fallback: the added-lines set-diff is the faithful change (the full new script
        # would bloat the assistant turn), so that's what the message-list arm renders.
        history.record(step + 1, thought, summary, _observation(result, test),
                       action={"kind": "patch", "content": change},
                       outcome=_outcome(result, test))

    log.d("GIVEUP", f"max_steps {max_steps} hit (best {best_key[1]}) — returning best script")
    log.trace("end", outcome="GIVEUP", steps=max_steps, best_passed=best_key[1]); log.summary()
    return "GIVEUP", best_script, graph
