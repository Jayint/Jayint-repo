import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.history import Step
from src.react_repair.history_view import extract_blocker, render_history


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
    from src.react_repair.history_view import render_history
    from src.react_repair.history import Step
    steps = [
        Step(0, "", "baseline → 1/3", "BUILD OK. TESTS 1/3 passed:\nNo module named 'redis'", ""),
        Step(1, "", "patch v1 (+pip install redis) → 1/3",
             "BUILD OK. TESTS 1/3 passed:\nredis.exceptions.ConnectionError: connecting to localhost:6379. Connection refused.", ""),
    ]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 2 and "connection refused" in out.lower()


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
    assert out.count("BLOCKER:") == 2                       # two distinct blockers
    assert "No module named 'six'" in out and "No module named 'toml'" in out
    assert out.index("v1 ") < out.index("toml")             # chronological: fix precedes revealed blocker
    assert "CLEARED" not in out                             # no causal overclaim
    assert "no longer present" in out.lower()               # hedged language instead

def test_render_stubborn_blocker_lists_do_not_retry_on_open_block():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _patch(1, "+pip install lxml2", "BUILD FAILED", _LXML_HDR),
             _patch(2, "+apt-get install libxml", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 1                       # same blocker throughout → one block
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
             _patch(1, "+pip install six", "0/3", weak)]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 1                       # conservative: no confident split
    assert "uncertain" in out.lower()

def test_render_explore_nests_without_changing_blocker():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("apt-cache search libxml2", "libxml2-dev - ..."),
             _patch(1, "+apt-get install -y libxml2-dev", "6/6", _PASS)]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 1
    assert "apt-cache search libxml2" in out                 # command still shown (do-not-retry)

def test_render_explore_carries_compact_finding():
    # The explore's OUTPUT (not just the command) is the point of exploring — carry it forward as a
    # compact fact so the agent doesn't re-probe or reason blind (knowledge ledger).
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("cat pyproject.toml", "[build-system]\nrequires = ['hatchling']")]
    out = render_history(steps)
    assert "explored `cat pyproject.toml`" in out
    assert "hatchling" in out                                # finding surfaced, not dropped

def test_render_aged_explore_finding_is_capped():
    # An AGED explore (a NEWER step follows it) stays a compact digest — no file dump for old probes.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("find /app", "x" * 5000),
             _patch(1, "+apt-get install -y libxml2-dev", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    explore_line = [ln for ln in out.splitlines() if "explored `find /app`" in ln][0]
    assert len(explore_line) < 300                           # aged probe hard-capped
    assert "…" in explore_line


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
    assert "no longer present" in out.lower()          # blocker tracking runs for edits too

def test_render_edit_populates_do_not_retry_ledger():
    # The anti-repeat "already tried (didn't help)" ledger must accumulate EDIT deltas.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR),
             _edit(2, "insert@6 +apt-get install libxml", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 1
    assert "already tried" in out.lower()
    assert "+pip install lxml2" in out and "+apt-get install libxml" in out


# ───────────────────────── anti-repeat reframe (HerAgent "Historical Reflection") ────────────
def test_render_escalates_to_directive_after_two_failed_edits_on_same_blocker():
    # When the SAME blocker survives >=2 edits, the passive ledger becomes an ACTIVE directive to
    # change approach — the anti-fixation lever (the addons 3x setuptools re-pin).
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR),
             _edit(2, "insert@6 +pip install lxml3", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps); low = out.lower()
    assert "change your approach" in low                    # imperative pivot, not a passive note
    assert "stuck" in low or "stop repeating" in low
    assert "already tried" in low                           # factual ledger still present
    assert "+pip install lxml2" in out and "+pip install lxml3" in out

def test_render_single_failed_edit_stays_a_passive_note():
    # ONE miss is normal progress, not fixation — keep the gentle ledger, don't nag on the first try.
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _edit(1, "insert@5 +pip install lxml2", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps); low = out.lower()
    assert "already tried" in low
    assert "change your approach" not in low and "stuck" not in low


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

def test_render_still_blocked_prior_omits_redundant_body():
    # A prior attempt hitting the SAME blocker just restates the signature — no body (avoids bloating
    # a fixation chain where the anti-repeat directive already fires).
    same = "BUILD FAILED at `pip install -e .`:\nERROR: egg-info conflict\nREDUNDANT_MARKER"
    steps = [_base("BUILD FAILED", same),
             _edit(1, "c1", "BUILD FAILED", same),
             _edit(2, "c2", "BUILD FAILED", same)]
    out = render_history(steps)
    assert "REDUNDANT_MARKER" not in out


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
