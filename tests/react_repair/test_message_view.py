"""The message-list prompt builder (REACT_PROMPT_STYLE=messages): reconstructs the growing
assistant/user conversation from the flat Step list, instead of the single re-rendered user blob.
The win being locked in: the model's own thought+action are real assistant messages, the current
observation is simply the last user message (no LAST RUN OBSERVATION slot, no withhold/dedup)."""
import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.history import History
from src.agent.history import build_messages

_SCRIPT = "1| #!/usr/bin/env bash\n2| apt-get install -y libpq-dev"
_CLOSE = "Turn 6/25 (19 left). Reason briefly, then call one tool — explore or edit."


def _redis_history() -> History:
    """baseline build-fail → explore → v1 (fixes build, redis refused) → v2 → v3 (current, still refused).
    Mirrors the real trajectory: one blocker (libpq) then a second (redis connection refused)."""
    h = History()
    h.record(0, "", "baseline → BUILD FAILED",
             "BUILD FAILED at `pip install -e .` (line 6):\n"
             "  psycopg/psycopgmodule.c:36:10: fatal error: libpq-fe.h: No such file or directory\n"
             "  ERROR: Failed building wheel for psycopg2\n")
    h.record(1, "which apt package actually ships libpq-fe.h?", "explore: apt-file search libpq-fe.h",
             "libpq-dev: /usr/include/postgresql/libpq-fe.h\n")
    _REDIS = ("BUILD OK. TESTS 41/50 passed.\n"
              "Top failure causes (by tests affected):\n"
              "  6 × [run] redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. "
              "Connection refused.\n"
              "--- pytest output (tail) ---\n"
              "=================== 9 failed, 41 passed in 18.31s ===================\n")
    h.record(2, "install the -dev headers, not the runtime lib",
             "edit v1 (insert@1 +apt-get install -y libpq-dev) → 41/50", _REDIS)
    h.record(3, "tests need a redis on 6379 — install the server",
             "edit v2 (insert@2 +apt-get install -y redis-server) → 41/50", _REDIS)
    h.record(4, "no init system in the container — launch redis directly",
             "edit v3 (insert@3 +redis-server --daemonize yes) → 41/50", _REDIS)
    return h


def _build(h, **kw):
    return build_messages(h.steps, system_prompt="SYS", numbered_script=_SCRIPT,
                          closing_line=_CLOSE, **kw)


def _roles(msgs):
    return [m["role"] for m in msgs]


def _joined(msgs):
    return "\n".join(m["content"] for m in msgs)


# --- structure -------------------------------------------------------------
def test_starts_with_system_message():
    msgs = _build(_redis_history())
    assert msgs[0] == {"role": "system", "content": "SYS"}


def test_reconstructs_growing_assistant_user_pairs():
    msgs = _build(_redis_history())
    roles = _roles(msgs)
    # system, baseline user, then (assistant action, user observation) per acting step.
    assert roles[0] == "system"
    assert roles[1] == "user"                       # baseline observation
    assert "assistant" in roles                      # the model's own moves are real assistant turns
    # never two assistant turns in a row (each action is followed by its observation)
    for a, b in zip(roles, roles[1:]):
        assert not (a == "assistant" and b == "assistant")


def test_model_thought_is_a_real_assistant_message():
    msgs = _build(_redis_history())
    asst = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert any("install the -dev headers, not the runtime lib" in c for c in asst)


def test_edit_action_shown_faithfully_in_assistant_message():
    msgs = _build(_redis_history())
    asst = "\n".join(m["content"] for m in msgs if m["role"] == "assistant")
    assert "apt-get install -y libpq-dev" in asst and "insert@1" in asst


# --- the key win: current observation is just the last user message --------
def test_no_last_run_observation_slot_and_no_withhold():
    out = _joined(_build(_redis_history()))
    assert "LAST RUN OBSERVATION" not in out                 # no dedicated top slot
    assert "full output above" not in out                    # no withhold placeholder
    assert "output unchanged from" not in out                # no dedup collapse


def test_current_observation_appears_as_a_user_message():
    # v3 is the current turn; its observation body must be present verbatim (not withheld).
    msgs = _build(_redis_history())
    users = "\n".join(m["content"] for m in msgs if m["role"] == "user")
    assert "9 failed, 41 passed in 18.31s" in users


def test_flat_by_construction_no_blocker_headers():
    out = _joined(_build(_redis_history()))
    assert "### BLOCKER" not in out                          # message mode is chronological, ungrouped


# --- scaffold on the last (live) user message ------------------------------
def test_numbered_script_in_last_user_message():
    msgs = _build(_redis_history())
    assert msgs[-1]["role"] == "user"
    assert "CURRENT setup.sh" in msgs[-1]["content"]
    assert "1| #!/usr/bin/env bash" in msgs[-1]["content"]


def test_closing_line_in_last_user_message():
    msgs = _build(_redis_history())
    assert _CLOSE in msgs[-1]["content"]


def test_do_not_retry_ledger_present_for_repeated_blocker():
    out = msgs = _joined(_build(_redis_history()))
    assert "already tried" in out
    # both redis edits fought the SAME connection-refused blocker → both listed
    assert "redis-server" in out


def test_graph_context_injected_when_provided():
    out = _joined(_build(_redis_history(), graph_context_text="redis:6379 → NOT LISTENING"))
    assert "GRAPH CONTEXT" in out and "NOT LISTENING" in out


def test_rejection_injected_when_provided():
    msgs = _build(_redis_history(), rejection="explore is READ-ONLY, use edit() instead")
    assert "REJECTED" in msgs[-1]["content"] and "use edit()" in msgs[-1]["content"]


# --- explore fidelity ------------------------------------------------------
def test_explore_shows_command_and_full_stdout():
    # A RECENT explore (short history → within the keep window) shows the command + full stdout.
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `pip install -e .` (line 6):\nboom\n")
    h.record(1, "which apt package ships libpq-fe.h?", "explore: apt-file search libpq-fe.h",
             "libpq-dev: /usr/include/postgresql/libpq-fe.h\n")
    msgs = _build(h)
    asst = "\n".join(m["content"] for m in msgs if m["role"] == "assistant")
    users = "\n".join(m["content"] for m in msgs if m["role"] == "user")
    assert "apt-file search libpq-fe.h" in asst                       # the model's real probe
    assert "/usr/include/postgresql/libpq-fe.h" in users              # full explore stdout


# --- defaults: message list + flat blob ------------------------------------
def test_history_view_defaults_to_flat():
    # The blob's history default flipped to flat (message list is the default arm; blob+flat fallback).
    import os
    import src.agent.history as HV
    if "REACT_HISTORY" not in os.environ:
        assert HV._HISTORY_MODE == "flat"


# --- edge: empty history ---------------------------------------------------
def test_empty_history_yields_system_plus_scaffold_user():
    msgs = build_messages([], system_prompt="SYS", numbered_script=_SCRIPT, closing_line=_CLOSE)
    assert _roles(msgs) == ["system", "user"]
    assert "CURRENT setup.sh" in msgs[1]["content"] and _CLOSE in msgs[1]["content"]


# --- recency-tier: immediate run shown full + labeled, recent runs compressed ---
def test_immediate_run_labeled_last_run_full():
    msgs = _build(_redis_history())
    joined = "\n".join(m["content"] for m in msgs if m["role"] == "user")
    assert "LAST RUN (full" in joined                     # the immediate run is labeled…
    assert joined.count("LAST RUN (full") == 1            # …and ONLY the immediate one


def test_script_delimiter_on_live_turn():
    msgs = _build(_redis_history())
    assert "── CURRENT setup.sh" in msgs[-1]["content"]    # workbench delimiter on the live user turn


def test_immediate_run_shown_fuller_than_recent_runs():
    # Same stored raw, different display by RECENCY: a middle line survives in the immediate (big cap,
    # verbatim) but is compressed out of the recent runs (1500 cap). Proves the age-by-position render.
    lines = [f"ctx line {i}" for i in range(200)]
    lines[60] = "UNIQUE_MID_MARKER_7"                      # a plain middle line (no error token)
    big = "BUILD OK. TESTS 3/5 passed.\n" + "\n".join(lines) + "\n=== 2 failed, 3 passed ===\n"
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `x` (line 1):\nboom\n")
    for v in (1, 2, 3):
        h.record(v, f"try {v}", f"edit v{v} (insert@1 +pip install p{v}) → 3/5", big)
    users = "\n".join(m["content"] for m in _build(h) if m["role"] == "user")
    assert users.count("UNIQUE_MID_MARKER_7") == 1        # kept only in the immediate (full) run


def test_immediate_cap_lever_widens_the_full_view(monkeypatch):
    # REACT_MSG_IMMEDIATE_CAP controls how much of the current run is shown in full.
    import src.agent.history as MV
    assert MV._immediate_cap() >= MV._OBS_COMPRESS_CAP     # never below the recent-tier cap
    monkeypatch.setenv("REACT_MSG_IMMEDIATE_CAP", "12000")
    assert MV._immediate_cap() == 12000


# --- byte-exact assistant turns from the structured tool call --------------
def test_structured_edit_renders_full_content_not_the_60char_preview():
    # A single-line edit whose content exceeds the action_summary's ~60-char preview must still show
    # in FULL when the structured op is threaded onto the Step (the byte-exact win).
    long = "apt-get install -y libpq-dev build-essential pkg-config libssl-dev zlib1g-dev libffi-dev"
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `pip install -e .` (line 5):\nboom\n")
    h.record(1, "install the full build toolchain", f"edit v1 (insert@4 +{long[:60]}) → BUILD FAILED",
             "BUILD FAILED at `pip install -e .` (line 5):\nstill boom\n",
             action={"kind": "edit", "verb": "insert", "start": 4, "end": 4, "content": long})
    asst = "\n".join(m["content"] for m in _build(h) if m["role"] == "assistant")
    assert long in asst                                  # full content, no truncation
    assert "libffi-dev" in asst                          # the tail that the 60-char preview dropped


def test_structured_multiline_edit_renders_each_line():
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `x` (line 1):\nboom\n")
    h.record(1, "start redis and wait for it", "edit v1 (insert@6 +redis-server --daemonize yes) → 41/50",
             "BUILD OK. TESTS 41/50 passed.\n9 failed, 41 passed\n",
             action={"kind": "edit", "verb": "insert", "start": 6, "end": 6,
                     "content": "redis-server --daemonize yes\nredis-cli ping"})
    asst = "\n".join(m["content"] for m in _build(h) if m["role"] == "assistant")
    assert "redis-server --daemonize yes" in asst and "redis-cli ping" in asst
    assert "edit(insert @6)" in asst


def test_structured_delete_shows_no_content():
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `x` (line 3):\nboom\n")
    h.record(1, "drop the broken pin", "edit v1 (delete@3) → BUILD FAILED",
             "BUILD FAILED at `x` (line 3):\nboom\n",
             action={"kind": "edit", "verb": "delete", "start": 3, "end": 3, "content": ""})
    asst = "\n".join(m["content"] for m in _build(h) if m["role"] == "assistant")
    assert "edit(delete @3)" in asst


def test_falls_back_to_summary_when_no_structured_action():
    # A Step with no structured action (baseline/invalid, or a pre-existing history) still renders.
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `x` (line 1):\nboom\n")
    h.record(1, "probe it", "explore: ls /app", "a.py\nb.py\n")   # no action= → fallback path
    asst = "\n".join(m["content"] for m in _build(h) if m["role"] == "assistant")
    assert "explore: ls /app" in asst


# --- last-N observation elision (ported from SWE-agent's LastNObservations) ---
def test_old_observations_elided_keeping_first_and_last_n():
    # 5 observations (baseline, explore, v1, v2, v3). keep_last_obs=2 → keep baseline + v2 + v3,
    # elide the two in the middle (explore result + v1). Reset-each-turn produces repetitive dumps,
    # so this is where the win is: the stale middle collapses instead of repeating verbatim.
    msgs = _build(_redis_history(), keep_last_obs=2)
    users = "\n".join(m["content"] for m in msgs if m["role"] == "user")
    assert "elided" in users                                          # some middle obs collapsed
    assert "/usr/include/postgresql/libpq-fe.h" not in users          # old explore result elided


def test_elision_keeps_the_first_observation_verbatim():
    msgs = _build(_redis_history(), keep_last_obs=2)
    users = "\n".join(m["content"] for m in msgs if m["role"] == "user")
    assert "fatal error: libpq-fe.h: No such file" in users           # baseline (first obs) kept whole


def test_elision_never_touches_the_current_observation_or_scaffold():
    msgs = _build(_redis_history(), keep_last_obs=1)                   # aggressive: keep only baseline + v3
    assert "CURRENT setup.sh" in msgs[-1]["content"]                   # live scaffold intact
    assert "9 failed, 41 passed in 18.31s" in msgs[-1]["content"]      # current obs body intact


def test_elision_never_touches_assistant_reasoning():
    msgs = _build(_redis_history(), keep_last_obs=1)
    asst = "\n".join(m["content"] for m in msgs if m["role"] == "assistant")
    assert "install the -dev headers, not the runtime lib" in asst    # reasoning survives even if its obs elided
    assert "tests need a redis on 6379" in asst


def test_elision_placeholder_reports_line_count():
    import re
    msgs = _build(_redis_history(), keep_last_obs=2)
    users = "\n".join(m["content"] for m in msgs if m["role"] == "user")
    assert re.search(r"\(\d+ lines? elided\)", users)                 # honest count, not a bare marker


def test_short_history_not_elided_at_default():
    # baseline + one edit = 2 observations; default keep-last (3) → nothing to elide.
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `x` (line 1):\nboom detail\n")
    h.record(1, "fix it", "edit v1 (insert@1 +pip install foo) → 2/2",
             "BUILD OK. TESTS 2/2 passed.\n2 passed in 1s\n")
    out = "\n".join(m["content"] for m in _build(h))
    assert "elided" not in out


# --- env-state block: inserted between the do-not-retry ledger and GRAPH CONTEXT ---
def test_build_messages_includes_env_state_on_live_turn():
    msgs = build_messages(
        [], system_prompt="SYS", numbered_script="1| pip install app",
        closing_line="Turn 1/30.",
        env_state_text="ENVIRONMENT STATE (after this turn's setup.sh)\n  pip installed (1): flask 3.0.0")
    last = msgs[-1]["content"]
    assert "ENVIRONMENT STATE (after this turn's setup.sh)" in last
    assert "pip installed (1): flask 3.0.0" in last


def test_build_messages_no_env_state_line_when_absent():
    msgs = build_messages([], system_prompt="SYS", numbered_script="1| x", closing_line="C")
    assert "ENVIRONMENT STATE" not in msgs[-1]["content"]
