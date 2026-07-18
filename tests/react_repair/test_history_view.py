import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import src.agent.history as HV
from src.agent.history import Step
from src.agent.history import extract_blocker, render_history


@pytest.fixture(autouse=True)
def _grouped_by_default(monkeypatch):
    # render_history's real default is now `flat` (the message list is the default arm; blob+flat is
    # the fallback — see message_view / planner). This file exercises the GROUPED feature (blocker
    # headers, do-not-retry ledger, STUCK), which is now opt-in, so default these tests to grouped.
    # The explicit flat test overrides via its own monkeypatch; the flat-default is asserted elsewhere.
    monkeypatch.setattr(HV, "_HISTORY_MODE", "grouped")


# ───────────────────────── extract_blocker (structured signature) ─────────────────────────

def test_extract_blocker_build_failure_uses_command_and_fatal_error():
    obs = ("BUILD FAILED at `pip install lxml`:\nCollecting lxml\n"
           "src/lxml/etree.c:96: fatal error: libxml/xmlversion.h: No such file or directory\n")
    sig = extract_blocker(obs)
    assert "pip install lxml" in sig and "libxml/xmlversion.h" in sig

def test_extract_blocker_test_failure_uses_missing_module():
    obs = "BUILD OK. TESTS 1/2 passed:\n.F\nModuleNotFoundError: No module named 'toml'\n"
    assert extract_blocker(obs) == "tests: No module named 'toml'"

def test_extract_blocker_all_executed_pass_is_none():
    assert extract_blocker("BUILD OK. TESTS 6/6 passed:\n6 passed") is None

def test_extract_blocker_nonbuild_output_is_none():
    assert extract_blocker("probe-output\nsome dir listing") is None

def test_extract_blocker_distinguishes_six_from_toml():
    a = extract_blocker("BUILD OK. TESTS 1/2 passed:\nNo module named 'six'")
    b = extract_blocker("BUILD OK. TESTS 1/2 passed:\nNo module named 'toml'")
    assert a != b


# widened vocabulary (borrowed from radical's SAFETY_ERROR_PATTERNS / select_failure_lines):
# service/tool/permission/timeout failures must get a real signature, not the weak fallback.
def test_extract_blocker_connection_refused_surfaces_host_port():
    obs = ("BUILD OK. TESTS 1/3 passed:\nredis.exceptions.ConnectionError: "
           "Error 111 connecting to localhost:6379. Connection refused.")
    sig = extract_blocker(obs)
    assert "connection refused" in sig.lower() and "localhost:6379" in sig

def test_extract_blocker_command_not_found():
    sig = extract_blocker("BUILD FAILED at `bash setup.sh`:\n/bin/sh: 1: pg_config: command not found")
    assert "command not found" in sig.lower() and "pg_config" in sig

def test_extract_blocker_permission_denied():
    sig = extract_blocker("BUILD FAILED at `pip install x`:\n"
                          "PermissionError: [Errno 13] Permission denied: '/usr/lib/python3'")
    assert "permission denied" in sig.lower()

def test_extract_blocker_no_such_file():
    sig = extract_blocker("BUILD FAILED at `make`:\n"
                          "FileNotFoundError: [Errno 2] No such file or directory: 'config.h'")
    assert "no such file" in sig.lower()

def test_extract_blocker_timeout():
    sig = extract_blocker("BUILD OK. TESTS 2/5 passed:\nTimeoutError: operation timed out after 30s")
    assert "timed out" in sig.lower()

def test_extract_blocker_generic_python_exception():
    sig = extract_blocker("BUILD OK. TESTS 3/4 passed:\nE   RuntimeError: database schema mismatch")
    assert "RuntimeError" in sig

def test_extract_blocker_widened_sigs_are_specific_enough_to_split():
    # a module blocker → a connection-refused blocker is a CONFIDENT change (both specific): 2 blocks.
    from src.agent.history import render_history
    from src.agent.history import Step
    steps = [
        Step(0, "", "baseline → 1/3", "BUILD OK. TESTS 1/3 passed:\nNo module named 'redis'", ""),
        Step(1, "", "patch v1 (+pip install redis) → 1/3",
             "BUILD OK. TESTS 1/3 passed:\nredis.exceptions.ConnectionError: connecting to localhost:6379. Connection refused.", ""),
    ]
    out = render_history(steps)
    assert out.count("### BLOCKER") == 2 and "connection refused" in out.lower()


# ───────────────────────── render_history (grouped, chronological) ─────────────────────────

def _base(score, obs): return Step(0, "", f"baseline → {score}", obs, obs)
def _patch(v, change, score, obs):
    summ = f"patch v{v} ({change}) → {score}" if change else f"patch v{v} → {score}"
    return Step(v, "", summ, obs, obs)
def _explore(cmd, out): return Step(99, "", f"explore: {cmd}", out, out)
def _edit(v, change, score, obs):
    summ = f"edit v{v} ({change}) → {score}" if change else f"edit v{v} → {score}"
    return Step(v, "", summ, obs, obs)

_SIX  = "BUILD OK. TESTS 1/2 passed:\n.F\nModuleNotFoundError: No module named 'six'"
_TOML = "BUILD OK. TESTS 1/2 passed:\n.F\nModuleNotFoundError: No module named 'toml'"
_PASS = "BUILD OK. TESTS 2/2 passed:\n2 passed"
_LXML_HDR = "BUILD FAILED at `pip install lxml`:\nfatal error: libxml/xmlversion.h: No such file"


def test_render_groups_hidden_import_chain_with_headers_at_transitions():
    steps = [_base("1/2", _SIX),
             _patch(1, "+pip install six", "1/2", _TOML),
             _patch(2, "+pip install toml", "2/2", _PASS)]
    out = render_history(steps)
    assert out.count("### BLOCKER") == 2                       # two distinct blockers
    assert "No module named 'six'" in out and "No module named 'toml'" in out
    assert out.index("v1 ") < out.index("toml")             # chronological: fix precedes revealed blocker
    assert "CLEARED" not in out                             # no causal overclaim
    # the observe half is the REAL stderr now (v1 revealed the toml error) — not a synthesized verdict
    assert "ModuleNotFoundError: No module named 'toml'" in out

def test_render_stubborn_blocker_lists_do_not_retry_on_open_block():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _patch(1, "+pip install lxml2", "BUILD FAILED", _LXML_HDR),
             _patch(2, "+apt-get install libxml", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert out.count("### BLOCKER") == 1                       # same blocker throughout → one block
    assert "already tried" in out.lower()
    assert "+pip install lxml2" in out and "+apt-get install libxml" in out

def test_render_resolved_run_has_no_do_not_retry_line():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _patch(1, "+apt-get install -y libxml2-dev", "6/6", _PASS)]
    out = render_history(steps)
    assert "already tried" not in out.lower()               # resolved → no dangling do-not-retry

def test_render_uncertain_transition_does_not_split():
    # specific module blocker -> a build failure with only a command (weak) signature: don't split.
    weak = "BUILD FAILED at `bash setup.sh`:\nunexpected end of file near line 12"
    steps = [_base("1/3", _SIX),
             _patch(1, "+pip install six", "0/3", weak),
             _patch(2, "+noop", "0/3", weak)]              # a 2nd step so patch1 isn't the withheld current
    out = render_history(steps)
    assert out.count("### BLOCKER") == 1                       # conservative: no confident split

def test_render_explore_nests_without_changing_blocker():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("apt-cache search libxml2", "libxml2-dev - ..."),
             _patch(1, "+apt-get install -y libxml2-dev", "6/6", _PASS)]
    out = render_history(steps)
    assert out.count("### BLOCKER") == 1
    assert "apt-cache search libxml2" in out                 # command still shown (do-not-retry)

def test_render_explore_carries_compact_finding():
    # The explore's OUTPUT (not just the command) is the point of exploring — carry it forward as a
    # compact fact so the agent doesn't re-probe or reason blind (knowledge ledger).
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("cat pyproject.toml", "[build-system]\nrequires = ['hatchling']")]
    out = render_history(steps)
    assert "explored `cat pyproject.toml`" in out
    assert "hatchling" in out                                # finding surfaced, not dropped

def test_render_explores_show_full_stdout_regardless_of_age():
    # User directive: explore/cat probes show FULL stdout (reading the file IS the point), even for
    # OLD probes — no aging digest. Only a head+tail hard cap guards a pathological dump.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("find /app", "x" * 5000),          # old probe — still shown in full
             _explore("e2", "y"), _explore("e3", "z"), _explore("e4", "w"),
             _patch(1, "+apt-get install -y libxml2-dev", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert "x" * 4000 in out                                 # full body shown, not a 200-char digest


# ───────────────────────── explore recency window (last K explores full, in-block) ───────────
def test_render_recent_explores_show_full_body_even_after_edit():
    # The last K explores render FULL output in-block, beside the patch they informed — even when the
    # final step is an EDIT (not the explore). Here the explore before v1 must still show full.
    big = "line0: head\n" + "\n".join(f"line{i}: filler content" for i in range(1, 60)) + "\nMARKER_TAIL"
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("cat pyproject.toml", big),
             _edit(1, "insert@2 +apt-get install -y libxml2-dev", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert "MARKER_TAIL" in out             # full body (tail survives), not a 200-char head digest

def test_render_recent_explore_body_is_indented_under_the_command():
    # The full explore body nests under its `explored` line (indented), matching the patch-body style.
    steps = [_base("BUILD FAILED", _LXML_HDR), _explore("cat x", "alpha\nbeta")]
    out = render_history(steps)
    assert "\n        alpha" in out and "\n        beta" in out

def test_render_all_explores_full_including_oldest():
    # Every explore shows full stdout now — the oldest is NOT decayed to a digest.
    def big(tag): return "head\n" + "\n".join("x" * 40 for _ in range(12)) + "\n" + tag + "_TAIL"
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("e1", big("OLDEST")), _explore("e2", big("E2")),
             _explore("e3", big("E3")), _explore("e4", big("E4"))]
    out = render_history(steps)
    assert all(t in out for t in ("E4_TAIL", "E3_TAIL", "E2_TAIL", "OLDEST_TAIL"))   # all full


def test_render_latest_explore_shows_real_output():
    # The just-run explore (LAST step) must reach the agent content-aware — the whole file, not a
    # 200-char stub — so it can act instead of re-probing. This reverses the old cap-everything rule.
    big = "\n".join(f"line {i}: pyproject key = value here" for i in range(200))
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("cat /app/pyproject.toml", big)]
    out = render_history(steps)
    assert "line 199:" in out                                # the TAIL survives → not a 200-char head
    assert len(out) > 1000

def test_render_latest_small_explore_is_verbatim():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("git --version", "git version 2.39.2")]
    out = render_history(steps)
    assert "git version 2.39.2" in out

def test_render_explore_no_output_shows_command_only():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("ls /app", "")]
    out = render_history(steps)
    assert "explored `ls /app`" in out
    assert "→" not in [ln for ln in out.splitlines() if "explored" in ln][0]

def test_render_empty_is_safe():
    assert isinstance(render_history([]), str)


# ───────────── REACT_HISTORY=flat — SWE-agent shape: no headers/grouping/ledger/STUCK ─────────
def _stubborn_three():
    return [_base("BUILD FAILED", _LXML_HDR),
            _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR),
            _edit(2, "insert@6 +pip install lxml3", "BUILD FAILED", _LXML_HDR),
            _edit(3, "insert@7 +pip install lxml4", "BUILD FAILED", _LXML_HDR)]

def test_render_flat_mode_drops_blocker_headers_and_ledger(monkeypatch):
    import src.agent.history as HV
    monkeypatch.setattr(HV, "_HISTORY_MODE", "flat")
    out = render_history(_stubborn_three())
    assert "### BLOCKER" not in out                     # no grouping headers
    assert "already tried" not in out                   # no do-not-retry ledger
    assert "same-shaped edits" not in out               # no STUCK
    assert "← current turn" not in out                  # nothing to mark
    # but the cards themselves still render (action + observe; helpers carry no thought)
    assert "- v1 · insert@5 +pip install lxml2" in out and "observe:" in out

def test_render_grouped_mode_opt_in_keeps_headers(monkeypatch):
    # grouped is now OPT-IN (REACT_HISTORY=grouped); it keeps the blocker headers + ledger.
    monkeypatch.setattr(HV, "_HISTORY_MODE", "grouped")
    out = render_history(_stubborn_three())
    assert "### BLOCKER 1" in out and "already tried" in out


# ───────────── trimmed synthesized lines: no action verdict (3), no test header (4b), no weak prose (2) ──
def test_render_action_line_has_no_verdict_suffix():
    # (3) the `→ BUILD FAILED` / `→ 44/50` verdict was dropped — it duplicated the observe header.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +apt-get install -y libxml2-dev", "BUILD FAILED", _LXML_HDR),
             _edit(2, "noop", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    v1 = [ln for ln in out.splitlines() if ln.startswith("- v1 ")][0]
    assert v1 == "- v1 · insert@5 +apt-get install -y libxml2-dev"    # no " → BUILD FAILED" suffix
    assert "→ BUILD FAILED" not in out

def test_render_test_observation_drops_build_ok_header_keeps_histogram():
    # (4b) the `BUILD OK. TESTS p/e passed` line is stripped from the display body (dup of the verdict);
    # the ranked histogram (the valuable part) stays.
    test_obs = ("BUILD OK. TESTS 41/50 passed.\nTop failure causes (by tests affected):\n"
                "  6 × [run] redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379.\n"
                "--- pytest output (tail) ---\nE   redis.exceptions.ConnectionError\n")
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "+apt-get install -y libxml2-dev", "41/50", test_obs),   # non-current → body shows
             _edit(2, "noop", "41/50", test_obs)]
    out = render_history(steps)
    assert "BUILD OK. TESTS 41/50 passed" not in out                 # header stripped from display
    assert "Top failure causes" in out and "[run] redis" in out      # histogram kept

def test_render_build_failure_keeps_line_number_header():
    # (4a) the `BUILD FAILED at cmd (line N)` header is KEPT — the line number is load-bearing.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "c1", "BUILD FAILED", "BUILD FAILED at `pip install x` (line 12):\nfatal error: z.h"),
             _edit(2, "noop", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert "BUILD FAILED at `pip install x` (line 12):" in out

def test_render_weak_build_header_shows_command_not_prose():
    # (2) a tokenless build failure header shows the REAL failing command, not `build failed: <cmd>`.
    weak = "BUILD FAILED at `bash scripts/setup_env.sh`:\nsyntax error near unexpected token `fi'"
    out = render_history([_base("BUILD FAILED", weak)])
    assert "### BLOCKER 1 — bash scripts/setup_env.sh" in out
    assert "build failed:" not in out

def test_render_gate_and_weak_test_get_bare_header_no_prose():
    # (2) a green baseline / tokenless test failure no longer emits the prose fallbacks.
    green = render_history([_base("12/12", "BUILD OK. TESTS 12/12 passed.\n12 passed in 3s")])
    assert "build meets the gate" not in green
    assert green.count("### BLOCKER 1") == 1                          # bare header still opens the block
    weak_test = "BUILD OK. TESTS 3/8 passed.\n5 failed, 3 passed in 2s"
    out = render_history([_base("3/8", weak_test)])
    assert "tests failing (" not in out


# ───────────────────────── think → action → observe card (thought threaded in) ───────────────
def test_render_threads_step_thought_as_think_line():
    # The model's reasoning (Step.thought) is now shown as the `think` half of the card, so the agent
    # doesn't re-reason a path it already rejected. Old view dropped it (showed only action+outcome).
    steps = [_base("BUILD FAILED", _LXML_HDR),
             Step(1, "install the -dev headers, not the runtime lib",
                  "edit v1 (insert@5 +apt-get install -y libxml2-dev) → BUILD FAILED", _LXML_HDR, _LXML_HDR)]
    out = render_history(steps)
    assert "think:" in out and "install the -dev headers" in out       # reasoning surfaced
    assert "- v1 · insert@5 +apt-get install -y libxml2-dev" in out    # action half
    assert "observe:" in out                                           # observe half

def test_render_empty_thought_renders_no_think_line():
    # A step with no thought must not emit a dangling `think:` line (keeps baseline/legacy compact).
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR)]
    assert "think:" not in render_history(steps)

def test_render_thought_is_length_capped():
    long = "because " * 60                                              # >180 chars
    steps = [_base("BUILD FAILED", _LXML_HDR),
             Step(1, long, "edit v1 (c1) → BUILD FAILED", _LXML_HDR, _LXML_HDR)]
    think_line = [ln for ln in render_history(steps).splitlines() if "think:" in ln][0]
    assert len(think_line) < 220 and think_line.rstrip().endswith("…")

def test_render_marks_the_open_blocker_as_current():
    # The last, still-OPEN blocker (the one the agent is fighting now) is tagged so it's unambiguous.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "c1", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert "← current turn" in out
    marked = [ln for ln in out.splitlines() if "← current turn" in ln][0]
    assert marked.startswith("### BLOCKER")                            # the tag rides the header

def test_render_resolved_last_block_not_marked_current():
    # If the final step reached the gate (no open blocker), nothing is tagged current.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "+apt-get install -y libxml2-dev", "6/6", _PASS)]
    assert "← current turn" not in render_history(steps)


# ───────────────────────── edit moves render like patches (native tool-calling arm) ──────────
def test_render_handles_edit_moves_like_patches():
    # The native-tool-calling arm records repairs as `edit v..`, NOT `patch v..` — render_history
    # must treat them identically, or every real edit shows as "(invalid move — re-prompted)" and
    # the blocker/ledger tracking (which lives in that branch) never runs for the current arm.
    steps = [_base("1/2", _SIX),
             _edit(1, "insert@40 +pip install six", "2/2", _PASS)]
    out = render_history(steps)
    assert "invalid move" not in out
    assert "v1" in out and "insert@40 +pip install six" in out
    assert "No module named 'six'" in out              # blocker tracking runs for edits too (six block opened)

def test_render_edit_populates_do_not_retry_ledger():
    # The anti-repeat "already tried (didn't help)" ledger must accumulate EDIT deltas.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR),
             _edit(2, "insert@6 +apt-get install libxml", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert out.count("### BLOCKER") == 1
    assert "already tried" in out.lower()
    assert "+pip install lxml2" in out and "+apt-get install libxml" in out


# ───────────────────────── anti-fixation stuck signal (factual ledger + gated escalation) ────
def _three_failed_same_blocker():
    return [_base("BUILD FAILED", _LXML_HDR),
            _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR),
            _edit(2, "insert@6 +pip install lxml3", "BUILD FAILED", _LXML_HDR),
            _edit(3, "insert@7 +pip install lxml4", "BUILD FAILED", _LXML_HDR)]

def test_render_neutral_stuck_is_a_fact_not_coaching_by_default():
    # Default (_STUCK_MODE=neutral): past the threshold, a NEUTRAL fact — NOT the prescriptive
    # "change your approach / is a service the real gap?" coaching (which can misfire and pre-leaks
    # the graph arm's diagnosis into the baseline).
    out = render_history(_three_failed_same_blocker()); low = out.lower()
    assert "already tried" in low                           # factual ledger always present
    assert "same-shaped edits" in low                       # neutral stuck fact fired (>=3)
    assert "change your approach" not in low                # no prescriptive coaching by default
    assert "is a service" not in low                        # no canned hypotheses
    assert "+pip install lxml2" in out and "+pip install lxml4" in out

def test_render_stuck_below_threshold_is_ledger_only():
    # TWO failed edits is normal iteration, not fixation — ledger only, no escalation line (threshold=3).
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR),
             _edit(2, "insert@6 +pip install lxml3", "BUILD FAILED", _LXML_HDR)]
    low = render_history(steps).lower()
    assert "already tried" in low
    assert "same-shaped edits" not in low and "change your approach" not in low

def test_render_single_failed_edit_stays_a_passive_note():
    # ONE miss keeps the gentle ledger, no escalation.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR)]
    low = render_history(steps).lower()
    assert "already tried" in low
    assert "same-shaped edits" not in low and "stuck" not in low

def test_render_stuck_directive_mode_opt_in(monkeypatch):
    # The old prescriptive coaching is available behind REACT_STUCK_MODE=directive for A/B.
    import src.agent.history as HV
    monkeypatch.setattr(HV, "_STUCK_MODE", "directive")
    low = render_history(_three_failed_same_blocker()).lower()
    assert "change your approach" in low and "stuck" in low

def test_render_stuck_off_mode_ledger_only(monkeypatch):
    # REACT_STUCK_MODE=off → the factual ledger stays, but no escalation line at all.
    import src.agent.history as HV
    monkeypatch.setattr(HV, "_STUCK_MODE", "off")
    low = render_history(_three_failed_same_blocker()).lower()
    assert "already tried" in low
    assert "same-shaped edits" not in low and "change your approach" not in low


# ───────────────────────── recency-window build/test bodies ──────────────────────────────────
def test_render_recent_prior_mutation_shows_compressed_body():
    # A recent PRIOR attempt that CHANGED the error carries its compressed body (detail the
    # signature collapses); the current attempt is excluded (shown in full under LAST RUN OBSERVATION).
    prior = "BUILD FAILED at `pip install -e .` (line 45):\nERROR: egg-info conflict\nHINT_ALPHA_marker"
    curr  = "BUILD FAILED at `pip install -e .` (line 45):\nERROR: egg-info conflict\nHINT_BRAVO_marker"
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +apt-get install -y libxml2-dev", "BUILD FAILED", prior),
             _edit(2, "replace@45 +pip install -e . --no-build-isolation", "BUILD FAILED", curr)]
    out = render_history(steps)
    assert "HINT_ALPHA_marker" in out         # prior mutation body carried
    assert "HINT_BRAVO_marker" not in out      # current excluded (shown separately in full)

def test_render_mutation_body_dropped_beyond_recency_window():
    o2 = "BUILD FAILED at `cmd2`:\nfatal error: bbb.h: No such file\nMID_MARKER"
    o3 = "BUILD FAILED at `cmd3`:\nfatal error: ccc.h: No such file\nNEAR_MARKER"
    o4 = "BUILD FAILED at `cmd4`:\nfatal error: ddd.h: No such file\nCURR_MARKER"
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "c1", "BUILD FAILED", o2),
             _edit(2, "c2", "BUILD FAILED", o3),
             _edit(3, "c3", "BUILD FAILED", o4)]
    out = render_history(steps)   # K=2 → edit1,edit2 (priors) get bodies; edit3 is current
    assert "MID_MARKER" in out and "NEAR_MARKER" in out
    assert "CURR_MARKER" not in out

def test_render_dedupes_byte_identical_repeat_output():
    # The real output is shown, but a byte-identical repeat is collapsed to "(output unchanged from
    # vN)" so a fixation chain doesn't reprint the same stderr every attempt.
    same = "BUILD FAILED at `pip install -e .`:\nERROR: egg-info conflict\nREDUNDANT_MARKER"
    steps = [_base("BUILD FAILED", same),
             _edit(1, "c1", "BUILD FAILED", same),      # first occurrence → real body shown once
             _edit(2, "c2", "BUILD FAILED", same),      # identical → collapsed
             _edit(3, "c3", "BUILD FAILED", same)]      # current → withheld (in LAST RUN OBSERVATION)
    out = render_history(steps)
    assert out.count("REDUNDANT_MARKER") == 1           # shown once, not reprinted every attempt
    assert "output unchanged from v1" in out            # the dedup collapse fired

def test_render_prior_mutation_shows_real_output_dropping_noise():
    # A non-current mutation's observe is the REAL safety-compressed stdout/stderr: the error line is
    # kept verbatim, transport noise (download progress) is dropped — not a synthesized "still blocked".
    noise = "\n".join(f"Downloading from https://pypi.org/pkg{i}" for i in range(120))   # >1500 → compresses
    noisy = ("BUILD FAILED at `pip install psycopg2`:\n" + noise +
             "\npsycopg.h:36:10: fatal error: libpq-fe.h: No such file or directory\n"
             "ERROR: Failed building wheel for psycopg2")
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +pip install psycopg2", "BUILD FAILED", noisy),
             _edit(2, "noop", "BUILD FAILED", _LXML_HDR)]   # edit2 = withheld current; edit1 shows body
    out = render_history(steps)
    assert "fatal error: libpq-fe.h: No such file or directory" in out   # real stderr kept
    assert "pkg50" not in out                                            # mid-stream transport noise dropped

def test_render_withholds_current_mutation_body_and_points_to_top():
    # The just-run mutation's full output is already at the top (LAST RUN OBSERVATION); history points
    # there instead of duplicating it.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "c1", "BUILD FAILED", "BUILD FAILED at `x`:\nSECRET_CURRENT_OUTPUT")]
    out = render_history(steps)
    assert "SECRET_CURRENT_OUTPUT" not in out            # withheld (not duplicated)
    assert "LAST RUN OBSERVATION" in out                 # pointer to where the full output lives


# ───────────────────────── invalid-move guidance is visible ──────────────────────────────────
def test_render_latest_invalid_shows_the_guidance():
    # The reminder for the JUST-MADE invalid move must reach the agent (it's about to retry) — not be
    # swallowed into a bare "(invalid move)". Without this the rejection feedback was dead (azure's
    # rejected pip-install-via-explore left the agent with zero direction).
    steps = [_base("BUILD FAILED", _LXML_HDR),
             Step(1, "", "invalid", "explore is READ-ONLY — use edit() to install", "x")]
    out = render_history(steps)
    assert "use edit() to install" in out

def test_render_aged_invalid_stays_terse():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             Step(1, "", "invalid", "SOME_OLD_REMINDER", "x"),
             _edit(1, "c1", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert "SOME_OLD_REMINDER" not in out          # aged invalid → terse, no reminder pile-up
    assert "invalid move" in out.lower()


def test_grouped_view_stays_compact_regardless_of_observation_size():
    # The grouped view IS the compaction: 8 KB observations still render tiny (blocker + score only,
    # never the body). Locks that nobody re-introduces per-step body rendering or an LLM compressor.
    big = ("BUILD OK. TESTS 1/2 passed:\n" + ("noise line of build output\n" * 800)
           + "\nModuleNotFoundError: No module named 'toml'")
    steps = [Step(0, "", "baseline → 1/2", big, big[:4000]),
             Step(1, "", "patch v1 (+pip install toml) → 2/2",
                  "BUILD OK. TESTS 2/2 passed:\n" + "x" * 8000, "")]
    out = render_history(steps)
    assert len(big) > 6000                       # the raw observation really is large
    assert len(out) < 1000                       # but the grouped view is tiny
    assert "No module named 'toml'" in out       # blocker signature preserved
    assert "noise line" not in out               # body noise never rendered
