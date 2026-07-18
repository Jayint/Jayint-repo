"""Shared kit for the react-arm golden-master tests (Phase 0, spec §9 Regime 1).

These goldens snapshot the react arm's PURE text outputs — the prompt messages, both
history render styles, the observation text, the gate verdicts — captured on the frozen
tree as the byte-identity oracle for the future `agent/` merges (spec §9). A merge is
proven "same as the react arm" iff it reproduces these bytes exactly.

Everything here is deterministic and hermetic:
  * `pin_defaults()` pins every REACT_* lever (env-read per-call AND module-level
    constants captured at import) to its shipping default, so a golden does not silently
    drift when an ambient env var or a changed default moves an unpinned knob.
  * The scenario observations are kept short (well under the compress/truncate caps), so
    the size-gated compaction paths do not fire and the goldens stay stable regardless of
    the cap levers — the caps are pinned anyway, belt-and-suspenders.

Same-process generation and verification both go through `pin_defaults`, so the fixtures
are reproducible byte-for-byte.
"""
from __future__ import annotations

import pathlib
import sys

# Repo root + src on sys.path (mirrors tests/react_repair/test_planner.py's convention).
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import src.agent.history_view as history_view  # noqa: E402
import src.agent.message_view as message_view  # noqa: E402
import src.agent.planner as planner_mod  # noqa: E402
from src.agent.repair_scope import RepairScope  # noqa: E402
from src.agent.history import History  # noqa: E402
from src.agent.planner import ReactPlanner  # noqa: E402

GOLDEN_DIR = pathlib.Path(__file__).resolve().parent


# --------------------------------------------------------------------------------------
# Hermetic lever pinning
# --------------------------------------------------------------------------------------
# Per-call env levers (read fresh inside a function each call).
_ENV_DEFAULTS = {
    "REACT_PROMPT_STYLE": "messages",   # planner._messages: messages (default) | blob
    "REACT_MSG_STYLE": "classic",       # style.agentic(): classic (default) | agentic
    "REACT_MSG_KEEP_LAST_OBS": "3",     # message_view._keep_last_obs
    "REACT_MSG_IMMEDIATE_CAP": "8000",  # message_view._immediate_cap
}
# Module-level constants (bound at import — must be set as attributes, not env). The two
# render modules each hold their OWN copy of the history_view caps (message_view imports
# them by value), so both are pinned.
_ATTR_DEFAULTS = [
    (planner_mod, "_LOW_BUDGET_TURNS", 5),
    (history_view, "_HISTORY_MODE", "flat"),
    (history_view, "_OBS_COMPRESS_CAP", 1500),
    (history_view, "_EXPLORE_FULL_CAP", 6000),
    (history_view, "_STUCK_THRESHOLD", 3),
    (history_view, "_STUCK_MODE", "neutral"),
    (history_view, "_THOUGHT_CAP", 180),
    (message_view, "_OBS_COMPRESS_CAP", 1500),
    (message_view, "_EXPLORE_FULL_CAP", 6000),
    (message_view, "_STUCK_THRESHOLD", 3),
    (message_view, "_STUCK_MODE", "neutral"),
]


def pin_defaults(monkeypatch) -> None:
    """Pin every REACT_* lever to its shipping default on the given monkeypatch-like object
    (pytest's `monkeypatch`, or a bare `_pytest.monkeypatch.MonkeyPatch` in the generator)."""
    for name, value in _ENV_DEFAULTS.items():
        monkeypatch.setenv(name, value)
    for obj, name, value in _ATTR_DEFAULTS:
        monkeypatch.setattr(obj, name, value, raising=False)


# --------------------------------------------------------------------------------------
# Serialization (faithful + human-diffable)
# --------------------------------------------------------------------------------------
def serialize_messages(messages) -> str:
    """A `list[{role, content}]` message list → a delimiter-framed dump. Faithful (content
    verbatim) and readable, so a non-zero diff reads as a caught regression. The frame line
    cannot collide with prompt content."""
    blocks = []
    for i, m in enumerate(messages):
        blocks.append(f"===== message[{i}] role={m['role']} =====\n{m['content']}")
    return "\n".join(blocks) + "\n"


# --------------------------------------------------------------------------------------
# Canonical transcripts (short observations → cap-independent)
# --------------------------------------------------------------------------------------
_SEED_SCRIPT = (
    "#!/usr/bin/env bash\n"
    "set -e\n"
    "apt-get update\n"
    "pip install -e .\n"
    "pip install psycopg2\n"
)

_BASELINE_OBS = (
    "BUILD FAILED at `pip install psycopg2` (line 5):\n"
    "    src/psycopg2/_psycopg.c:36:10: fatal error: libpq-fe.h: No such file or directory\n"
)
_STILL_FAILING_OBS = (
    "BUILD FAILED at `pip install psycopg2` (line 5):\n"
    "    src/psycopg2/_psycopg.c:36:10: fatal error: libpq-fe.h: No such file or directory\n"
)
_EXPLORE_OBS = "psycopg2-binary is NOT declared; setup.py links against libpq.\n"
_TESTFAIL_OBS = (
    "BUILD OK. TESTS 3/5 passed\n"
    "    E   ModuleNotFoundError: No module named 'redis'\n"
    "    2 failed, 3 passed in 0.41s\n"
)


def install_failure_history() -> History:
    """baseline build failure → one edit that did not clear the same blocker."""
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", _BASELINE_OBS)
    h.record(
        1, "libpq headers are missing; add the -dev package",
        "edit v1 (insert@4 +apt-get install -y libpq-dev) → BUILD FAILED",
        _STILL_FAILING_OBS,
        action={"kind": "edit", "verb": "insert", "start": 4, "end": 4,
                "content": "apt-get install -y libpq-dev"},
        outcome={"build_ok": False, "failing_command": "pip install psycopg2",
                 "lineno": 5, "passed": 0, "failed": 0, "errors": 0, "collected": 0},
    )
    return h


def test_failure_history() -> History:
    """baseline fail → explore → edit clears the build but the suite still fails."""
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", _BASELINE_OBS)
    h.record(1, "check whether the binary wheel is an option",
             "explore: pip show psycopg2-binary", _EXPLORE_OBS)
    h.record(
        2, "install the libpq dev headers so the source build links",
        "edit v1 (insert@4 +apt-get install -y libpq-dev) → BUILD OK",
        _TESTFAIL_OBS,
        action={"kind": "edit", "verb": "insert", "start": 4, "end": 4,
                "content": "apt-get install -y libpq-dev"},
        outcome={"build_ok": True, "failing_command": None, "lineno": None,
                 "passed": 3, "failed": 2, "errors": 0, "collected": 5},
    )
    return h


# Planner prompt scenarios: (history, script, observation, fail_lineno, turn, max_turns).
# Chosen to cover the three _closing_line branches (no-turn / normal / low-budget), an empty
# history, an install failure, and a test failure.
def prompt_scenarios() -> dict:
    return {
        "empty_first_turn": (History(), _SEED_SCRIPT, _BASELINE_OBS, 5, None, None),
        "install_failure": (install_failure_history(), _SEED_SCRIPT, _STILL_FAILING_OBS, 5, 3, 30),
        "test_failure": (test_failure_history(), _SEED_SCRIPT, _TESTFAIL_OBS, None, 6, 30),
        "low_budget": (install_failure_history(), _SEED_SCRIPT, _STILL_FAILING_OBS, 5, 29, 30),
    }


def build_prompt_messages(scenario_key: str):
    """Rebuild the exact `messages` list the planner would send for a scenario, via the
    PUBLIC `_messages` seam (the arg to complete_with_tools) — never an internal patched
    symbol, so a re-export shim in a future merge cannot silently no-op the check (§9 Trap).
    The client is a bare object(): `_messages` is pure and never calls the LLM."""
    history, script, observation, fail_lineno, turn, max_turns = prompt_scenarios()[scenario_key]
    planner = ReactPlanner(client=object(), model="golden-model")
    return planner._messages(
        history, script, observation, None, fail_lineno, turn, max_turns, None)


def repair_scope_cases() -> dict:
    """RepairScope inputs for `render_repair_scope` (T1 merge partner: agent/prompt.py <-
    planner + repair_scope). `minimal` exercises the all-empty render (schema hint only);
    `full` exercises every field + the sorted evidence-id list."""
    return {
        "repair_scope_minimal": RepairScope(
            target_node_id=None, failed_command=None, failed_output="",
            slice_lines=(), known_invalid=(), constraints=(), known_evidence_ids=frozenset()),
        "repair_scope_full": RepairScope(
            target_node_id="syslib:libpq", failed_command="pip install psycopg2",
            failed_output="fatal error: libpq-fe.h: No such file or directory",
            slice_lines=(), known_invalid=("apt-get install -y libpq5",),
            constraints=(("python", "3.11"),),
            known_evidence_ids=frozenset({"ev.install.psycopg2", "ev.probe.libpq"})),
    }


# P0-b: the graph-context BLOCK and the rejection FOOTER — two prompt branches the base scenarios
# above never light up (all pass graph=None, rejection=None). Both are captured through the PUBLIC
# planner._messages seam in BOTH prompt styles, so they pin the blob path (planner._render, folded
# by 3a-4) AND the messages path (message_view._scaffold, folded by 3a-5). R10.
_GRAPH_CTX_STUB = (
    "NEEDS (certified, still unmet):\n"
    "  - syslib:libpq — `pip install psycopg2` fails: fatal error: libpq-fe.h (ev.install.psycopg2)\n"
    "PROVIDERS (candidate): apt:libpq-dev")
_REJECTION_REASON = ("explore is read-only — `apt-get install -y libpq-dev` modifies the system. "
                     "Put the fix in setup.sh with edit() instead.")


def _extra_planner(with_graph: bool) -> "ReactPlanner":
    """A hermetic planner (bare object() client — _messages is pure). `with_graph` wires a
    graph_context callable returning a FIXED certified-state block, so the GRAPH CONTEXT branch
    fires deterministically without a real graph."""
    gc = (lambda graph, result, causes, prev: _GRAPH_CTX_STUB) if with_graph else None
    return ReactPlanner(client=object(), model="golden-model", graph_context=gc)


def prompt_extra_scenarios() -> tuple:
    return ("graph_context", "rejection_footer")


def build_prompt_extra(scenario_key: str):
    """Rebuild the `messages` list for a graph-context / rejection scenario via planner._messages
    (style from REACT_PROMPT_STYLE). Same install-failure transcript as the base scenarios so the
    only delta vs `install_failure.<style>` is the extra block/footer."""
    history = install_failure_history()
    script, obs, fail_lineno, turn, max_turns = _SEED_SCRIPT, _STILL_FAILING_OBS, 5, 3, 30
    if scenario_key == "graph_context":
        planner = _extra_planner(with_graph=True)
        return planner._messages(history, script, obs, object(), fail_lineno, turn, max_turns, None)
    if scenario_key == "rejection_footer":
        planner = _extra_planner(with_graph=False)
        return planner._messages(history, script, obs, None, fail_lineno, turn, max_turns,
                                 _REJECTION_REASON)
    raise KeyError(scenario_key)


# --------------------------------------------------------------------------------------
# T2: history render — one rich transcript exercised through every render branch.
# --------------------------------------------------------------------------------------
def history_transcript() -> History:
    """explore → edit → run → gate. Six steps chosen to light up every distinctive branch of
    the four render modules with SHORT observations (so the size caps never fire):
      * an explore card (full-stdout path),
      * three edits against the SAME libpq blocker with byte-identical output — the do-not-retry
        ledger accumulates to the STUCK threshold (grouped) and the observation-dedup
        ("output unchanged from vN") fires,
      * a confident signature change (build-fail → test-fail) that opens a new BLOCKER, and
      * the final mutation whose body is withheld (shown up top under LAST RUN OBSERVATION),
        plus enough observations that message-list elision collapses the stale middle."""
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", _BASELINE_OBS)
    h.record(1, "is a prebuilt binary wheel available?",
             "explore: pip show psycopg2-binary", _EXPLORE_OBS)
    for ver, note in ((1, "add the -dev headers"), (2, "try the runtime lib"),
                      (3, "reinstall the -dev headers")):
        h.record(
            ver + 1, note,
            f"edit v{ver} (insert@4 +apt-get install -y libpq-dev) → BUILD FAILED",
            _STILL_FAILING_OBS,
            action={"kind": "edit", "verb": "insert", "start": 4, "end": 4,
                    "content": "apt-get install -y libpq-dev"},
            outcome={"build_ok": False, "failing_command": "pip install psycopg2",
                     "lineno": 5, "passed": 0, "failed": 0, "errors": 0, "collected": 0},
        )
    h.record(
        5, "swap to the binary wheel to skip the source link",
        "edit v4 (replace@5 +pip install psycopg2-binary) → BUILD OK", _TESTFAIL_OBS,
        action={"kind": "edit", "verb": "replace", "start": 5, "end": 5,
                "content": "pip install psycopg2-binary"},
        outcome={"build_ok": True, "failing_command": None, "lineno": None,
                 "passed": 3, "failed": 2, "errors": 0, "collected": 5},
    )
    return h


def stuck_transcript() -> History:
    """Three edits against the SAME still-open blocker, with DISTINCT deltas — ends open, so
    the do-not-retry ledger lists all three and the STUCK escalation trips (3 >= threshold).
    Complements `history_transcript`, whose blocker resolves (closing the ledger)."""
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", _BASELINE_OBS)
    for ver, delta in ((1, "+apt-get install -y libpq-dev"),
                       (2, "+apt-get install -y libpq5"),
                       (3, "+apt-get install -y libpq")):
        h.record(
            ver, f"attempt {ver} at the libpq headers",
            f"edit v{ver} (insert@4 {delta}) → BUILD FAILED", _STILL_FAILING_OBS,
            action={"kind": "edit", "verb": "insert", "start": 4, "end": 4,
                    "content": delta.lstrip("+")},
            outcome={"build_ok": False, "failing_command": "pip install psycopg2",
                     "lineno": 5, "passed": 0, "failed": 0, "errors": 0, "collected": 0},
        )
    return h


# P0-c: a transcript whose observations EXCEED the size caps, so the compress/truncate paths the
# short resolve/stuck transcripts deliberately avoid get pinned too. Fires all three caps: a
# build/test obs > _OBS_COMPRESS_CAP (1500) → safety_compress SELECTION pass; an explore obs >
# _EXPLORE_FULL_CAP (6000) → head/tail safety_truncate; a thought > _THOUGHT_CAP (180) → capped
# think line. These are the exact duplicated cap constants R4 collapses across history_view /
# message_view, so a bad dedup would surface as a byte diff here. Content is range-built → fully
# deterministic (no timestamps/paths that would drift between runs).
_LONG_BUILD_OBS = "BUILD FAILED at `pip install psycopg2` (line 5):\n" + "".join(
    f"    src/psycopg2/_psycopg.c:{i}:10: error: symbol 'PQ_{i}' used but libpq-fe.h is missing\n"
    for i in range(1, 41))                    # ~40 lines, well over 1500 chars, no noise → selection fires
# A DISTINCT >1500 build failure (different text, same blocker) for the v1 edit card, so a
# NON-current mutation renders its compressed body through history_view._observe_body — pinning that
# path directly, not only message_view._obs_compressed. Both read the same _OBS_COMPRESS_CAP (the R4
# collapse target), so this catches a dedup that breaks EITHER copy.
_LONG_BUILD_OBS2 = "BUILD FAILED at `pip install psycopg2` (line 5):\n" + "".join(
    f"    /usr/bin/ld: undefined reference to `PQconnectdb_{i}' (link needs -lpq / libpq-dev)\n"
    for i in range(1, 41))
_LONG_EXPLORE_OBS = "\n".join(
    f"{i:3d}  psycopg2-binary=={i}.0.0    # candidate wheel row {i} from the resolver cache dump"
    for i in range(1, 121))                   # ~120 lines, well over 6000 chars → head/tail truncate fires
_LONG_THOUGHT = (
    "the failing link names libpq-fe.h so the obvious fix is the -dev headers, but the resolver cache "
    "lists a prebuilt binary wheel that would skip the source link entirely, so let me read the full "
    "candidate dump before choosing between apt and the wheel")   # > 180 chars → thought cap fires

_CAPS_FAIL_OUTCOME = {"build_ok": False, "failing_command": "pip install psycopg2",
                      "lineno": 5, "passed": 0, "failed": 0, "errors": 0, "collected": 0}


def capsfire_transcript() -> History:
    """baseline (long build fail) → explore (long thought + long cache dump) → edit v1 (still fails,
    distinct long output) → edit v2 (current, withheld). Every observation is over a cap, so the
    compaction the short resolve/stuck transcripts skip is exercised: the v1 card fires
    history_view._observe_body (>1500 build compress), the explore fires _explore_full (>6000
    truncate), the long thought fires the _THOUGHT_CAP, and the message styles fire _obs_compressed."""
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", _LONG_BUILD_OBS)
    h.record(1, _LONG_THOUGHT, "explore: cat .resolver_cache.txt", _LONG_EXPLORE_OBS)
    h.record(
        2, "add the -dev headers so the source build links",
        "edit v1 (insert@4 +apt-get install -y libpq-dev) → BUILD FAILED", _LONG_BUILD_OBS2,
        action={"kind": "edit", "verb": "insert", "start": 4, "end": 4,
                "content": "apt-get install -y libpq-dev"},
        outcome=_CAPS_FAIL_OUTCOME,
    )
    h.record(
        3, "the link still fails; pin the runtime lib package too",
        "edit v2 (insert@5 +apt-get install -y libpq5) → BUILD FAILED", _LONG_BUILD_OBS2,
        action={"kind": "edit", "verb": "insert", "start": 5, "end": 5,
                "content": "apt-get install -y libpq5"},
        outcome=_CAPS_FAIL_OUTCOME,
    )
    return h


def history_transcripts() -> dict:
    """The named T2 transcripts, each captured through all four render styles."""
    return {"resolve": history_transcript(), "stuck": stuck_transcript(),
            "capsfire": capsfire_transcript()}


# Fixed scaffold inputs for the message-list render (T2 focuses on the transcript, so the
# planner-owned scaffold pieces are simple constants).
_T2_SYSTEM_PROMPT = "SYSTEM PROMPT (fixed for the history golden)."
_T2_CLOSING_LINE = "Turn 6/30 (24 left). Reason briefly, then call one tool — explore or edit."


def _t2_numbered_script() -> str:
    return planner_mod._numbered(_SEED_SCRIPT, fail_lineno=None)


def build_history_message_list(steps):
    """`message_view.build_messages` over the T2 transcript (style set by REACT_MSG_STYLE)."""
    return message_view.build_messages(
        steps, system_prompt=_T2_SYSTEM_PROMPT, numbered_script=_t2_numbered_script(),
        closing_line=_T2_CLOSING_LINE, graph_context_text=None, rejection=None, rejected=None)


# --------------------------------------------------------------------------------------
# T3: OBSERVE ("what the agent sees") + GATE (verdict). These transforms are env-independent
# (no REACT_* lever), so the tables need no pinning — they are naturally hermetic.
# --------------------------------------------------------------------------------------
def serialize_table(cases: dict) -> str:
    """A `{case_name: rendered_text}` table → a delimiter-framed dump (readable + faithful),
    ordered by the dict's insertion order so the fixture is stable."""
    blocks = []
    for name, text in cases.items():
        blocks.append(f"########## CASE: {name} ##########\n{text}")
    return "\n\n".join(blocks) + "\n"


# Raw pytest fragments used by several observe/gate cases.
_PYTEST_SAME_CAUSE = (
    "==================================== ERRORS ====================================\n"
    "_______________ ERROR collecting tests/test_a.py _______________\n"
    "ImportError while importing test module 'tests/test_a.py'.\n"
    "Hint: make sure your test modules/packages have valid Python names.\n"
    "Traceback:\n"
    "/usr/lib/python3.11/importlib/__init__.py:126: in import_module\n"
    "    return _bootstrap._gcd_import(name[level:], package, level)\n"
    "tests/test_a.py:3: in <module>\n"
    "    import redis\n"
    "E   ModuleNotFoundError: No module named 'redis'\n"
    "_______________ ERROR collecting tests/test_b.py _______________\n"
    "ImportError while importing test module 'tests/test_b.py'.\n"
    "Hint: make sure your test modules/packages have valid Python names.\n"
    "Traceback:\n"
    "/usr/lib/python3.11/importlib/__init__.py:126: in import_module\n"
    "    return _bootstrap._gcd_import(name[level:], package, level)\n"
    "tests/test_b.py:5: in <module>\n"
    "    import redis\n"
    "E   ModuleNotFoundError: No module named 'redis'\n"
    "=========================== short test summary info ============================\n"
    "ERROR tests/test_a.py\n"
    "ERROR tests/test_b.py\n"
)
_PYTEST_MIXED = (
    "=================================== FAILURES ===================================\n"
    "_______________________ test_encode ________________________\n"
    "    def test_encode():\n"
    ">       assert encode(3) == 4\n"
    "E       assert 3 == 4\n"
    "tests/test_enc.py:12: AssertionError\n"
    "_______________________ test_decode ________________________\n"
    "    def test_decode():\n"
    ">       import redis\n"
    "E       ModuleNotFoundError: No module named 'redis'\n"
    "tests/test_dec.py:4: ModuleNotFoundError\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_enc.py::test_encode\n"
    "FAILED tests/test_dec.py::test_decode\n"
)
_PIP_NOISY = (
    "Collecting psycopg2\n"
    "  Downloading psycopg2-2.9.9.tar.gz (384 kB)\n"
    "     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 384.0/384.0 kB 5.2 MB/s\n"
    "  Preparing metadata (setup.py): started\n"
    "WARNING: Running pip as the 'root' user can result in broken permissions.\n"
    "    Error: pg_config executable not found.\n"
    "Successfully installed psycopg2-2.9.9\n"
)
# apt/maven-style TRANSPORT noise — matches SAFETY_NOISE_PATTERNS (the always-on pass-1
# strip in safety_compress_observation), unlike pip's chatter above.
_APT_NOISY = (
    "Get:1 http://deb.debian.org/debian bookworm/main amd64 libpq-dev\n"
    "Hit:2 http://deb.debian.org/debian bookworm InRelease\n"
    "Downloading from central: https://repo1.maven.org/foo.jar\n"
    "Progress (1): 2.2 MB\n"
    "Setting up libpq-dev (15.4) ...\n"
    "Successfully installed psycopg2-2.9.9\n"
)


# A LARGE observation (> 8000 chars) that fires safety_compress's SELECTION pass — the path
# loop._obs_body leans on (threshold = target = _OBS_MAX_CHARS = 8000), unpinned until now (the
# noise_strip case below only exercises the always-on pass-1 strip). Deterministic: range-built pip
# noise (dropped by pass 1) + ~60 identical collection-error blocks + a summary tail, so after the
# strip the text is still over 8000 and pass 2 selects head/tail/status/error-blocks, elides the
# middle, then hard-caps to 8000 on line boundaries.
_LARGE_OBS = (
    "Collecting psycopg2\n"
    + "".join(f"  Downloading dep_{i:02d}-1.0.0-py3-none-any.whl (512 kB)\n" for i in range(1, 25))
    + "".join(
        f"_______________ ERROR collecting tests/test_mod_{i:02d}.py _______________\n"
        f"tests/test_mod_{i:02d}.py:7: in <module>\n"
        f"    import redis\n"
        f"E   ModuleNotFoundError: No module named 'redis'\n"
        for i in range(1, 61))
    + "=========================== short test summary info ============================\n"
    + "".join(f"ERROR tests/test_mod_{i:02d}.py\n" for i in range(1, 61))
    + "60 errors in 4.12s\n")


def observe_cases() -> dict:
    """The observe cluster's pure renders: the `$ cmd -> result` envelope, edit tool-results,
    the ranked pytest cause histogram, block dedup, and the noise-strip + selection compression."""
    from src.agent.observe import edit_result, run_envelope
    from src.agent.observe import safety_compress_observation, strip_pip_progress
    from src.agent.observe import compact_pytest_blocks
    from src.agent.observe import format_breakdown, summarize

    outcomes = {
        "envelope/build_fail": {"build_ok": False, "failing_command": "pip install psycopg2", "lineno": 5},
        "envelope/build_ok_no_tests": {"build_ok": True, "ran_tests": False},
        "envelope/tests_all_pass": {"build_ok": True, "ran_tests": True, "passed": 10, "failed": 0},
        "envelope/tests_collection_errors": {"build_ok": True, "ran_tests": True, "passed": 0,
                                             "failed": 0, "errors": 5, "collected": 0},
        "envelope/tests_partial": {"build_ok": True, "ran_tests": True, "passed": 3, "failed": 2,
                                   "collected": 5},
        "envelope/tests_silent_skip": {"build_ok": True, "ran_tests": True, "passed": 8, "failed": 2,
                                       "collected": 200},
    }
    edits = {
        "edit_result/insert_single": {"kind": "edit", "verb": "insert", "start": 4,
                                      "content": "apt-get install -y libpq-dev"},
        "edit_result/insert_multi": {"kind": "edit", "verb": "insert", "start": 4,
                                     "content": "apt-get update\napt-get install -y libpq-dev gcc"},
        "edit_result/delete_span": {"kind": "edit", "verb": "delete", "start": 4, "end": 6},
        "edit_result/replace_single": {"kind": "edit", "verb": "replace", "start": 5, "end": 5,
                                       "content": "pip install psycopg2-binary"},
        "edit_result/non_edit": {"kind": "explore", "command": "ls /app"},
    }
    cases = {}
    for name, o in outcomes.items():
        cases[name] = run_envelope(o)
    for name, a in edits.items():
        cases[name] = str(edit_result(a))          # None -> "None" for the non-edit case
    cases["pytest_summary/same_cause"] = format_breakdown(summarize(_PYTEST_SAME_CAUSE))
    cases["pytest_summary/mixed_causes"] = format_breakdown(summarize(_PYTEST_MIXED))
    cases["pytest_blocks/dedup"] = compact_pytest_blocks(_PYTEST_SAME_CAUSE)
    cases["compression/strip_pip_progress"] = strip_pip_progress(_PIP_NOISY)
    cases["compression/noise_strip"] = safety_compress_observation(_APT_NOISY)[0]
    # SELECTION pass at the loop._obs_body budget (8000): pass-1 strips the pip noise, then head/tail/
    # status/error-block selection + a hard cap to 8000 — the >8000 path the observe merge (3a-3) owns.
    cases["compression/large_selection"] = safety_compress_observation(
        _LARGE_OBS, threshold_chars=8000, target_chars=8000)[0]
    return cases


def gate_cases() -> dict:
    """The gate verdict across the >=80% boundary + the anti-hollow guards, and the anti-gaming
    detectors (test-collection narrowing + self-install-from-index), incl. must-NOT-trip cases."""
    from src.agent.gate import (
        added_self_install_reason, detect_test_narrowing, narrowing_reason, self_install_reason)
    from src.agent.gate import test_verdict

    verdict_inputs = {
        "verdict/boundary_exactly_80": "8 passed, 2 failed",
        "verdict/just_below_80": "7 passed, 3 failed",
        "verdict/small_denominator_80": "4 passed, 1 failed",
        "verdict/all_pass": "10 passed",
        "verdict/half_pass": "5 passed, 3 failed, 2 errors",
        "verdict/hollow_all_collection_errors": "0 passed, 5 errors",
        "verdict/hollow_zero_collected": "0 passed",
        "verdict/hollow_all_skipped": "5 skipped",
        "verdict/collected_gt_executed": "8 passed, 2 failed collected 200 items",
        "verdict/ansi_stripped": "\x1b[32m8 passed\x1b[0m, \x1b[31m2 failed\x1b[0m",
    }
    cases = {}
    for name, out in verdict_inputs.items():
        v = test_verdict(out)
        cases[name] = (f"ok={v.ok} passed={v.passed} executed={v.executed} collected={v.collected} "
                       f"failed={v.failed} errors={v.errors} skipped={v.skipped}")

    # Anti-gaming: (old_script, new_script[, project]) -> reason or None. Includes the legitimate
    # edits that must NOT trip (pip --ignore-installed, installing a *different* dep, removing a cheat).
    _SEED = "set -e\npip install -e .\n"
    narrowing = {
        "narrowing/add_ignore_tests": (_SEED, _SEED + "pytest --ignore=tests/test_slow.py\n"),
        "narrowing/add_testpaths_ini": (_SEED, _SEED + "printf 'testpaths = fast\\n' > pytest.ini\n"),
        "narrowing/add_collect_ignore": (_SEED, _SEED + "echo 'collect_ignore = [\"test_x.py\"]' > conftest.py\n"),
        "narrowing/rm_test_file": (_SEED, _SEED + "rm tests/test_broken.py\n"),
        "narrowing/legit_pip_ignore_installed": (_SEED, _SEED + "pip install --ignore-installed six\n"),
        "narrowing/legit_no_change": (_SEED, _SEED),
    }
    for name, (old, new) in narrowing.items():
        cases[name] = str(narrowing_reason(old, new))
    cases["detect/deselect_flag"] = str(detect_test_narrowing("pytest --deselect tests/test_a.py::t"))
    cases["detect/clean_setup"] = str(detect_test_narrowing("pip install -e .[test]"))

    self_install = {
        "self_install/index_project": (_SEED, _SEED + "pip install itsdangerous\n", "itsdangerous"),
        "self_install/editable_ok": (_SEED, _SEED + "pip install -e .\n", "itsdangerous"),
        "self_install/other_dep_ok": (_SEED, _SEED + "pip install redis\n", "itsdangerous"),
    }
    for name, (old, new, proj) in self_install.items():
        cases[name] = str(added_self_install_reason(old, new, proj))
    # PEP 503: `Flask_Foo` and `flask-foo` normalize to the same distribution -> must trip.
    cases["self_install/normalized_match"] = str(
        self_install_reason("pip install Flask_Foo", "flask-foo"))
    return cases


# --------------------------------------------------------------------------------------
# P0-a (actions): the agent's MOVE parse + apply. Pins the pure functions the future
# `agent/actions/` SPLIT (actions + script_prep + v3_build_agent -> actions/{base,script,
# graph}.py, R9) must preserve byte-for-byte: the native tool-call path
# (`action_from_tool_call`), the text fallback (`parse_action`, incl. the mis-wrapped-explore
# recovery), the line-splice (`apply_edit`), and the thought/reasoning extractors. Parsing is
# env-independent (no REACT_* lever), so the table is naturally hermetic — no pinning.
# --------------------------------------------------------------------------------------
_ACTIONS_SCRIPT = (
    "#!/usr/bin/env bash\n"
    "set -e\n"
    "apt-get update\n"
    "pip install -e .\n"
    "pip install psycopg2\n"
)


def _fmt_action(a) -> str:
    """A frozen Action -> a stable, faithful one-block string (every field shown)."""
    e = a.edit
    edit = (f"EditOp(verb={e.verb!r} start={e.start} end={e.end} content={e.content!r})"
            if e is not None else "None")
    return (f"kind={a.kind!r}\ncommand={a.command!r}\nnew_script={a.new_script!r}\nedit={edit}")


def actions_cases() -> dict:
    """The move-parse table: raw model output / tool call -> parsed Action, and EditOp -> spliced
    script. Ordered by family so the fixture is stable and a diff is legible."""
    from src.agent.actions import (
        EditOp, action_from_tool_call, apply_edit, extract_reasoning, extract_thought, parse_action)

    cases: dict = {}

    # -- parse_action: the TEXT fallback (no native tool call this turn) --
    _EDIT_REPLACE = "Thought: swap to the binary wheel\nEdit: replace 5\n```bash\npip install psycopg2-binary\n```"
    _EDIT_INSERT = "Edit: insert after 4\n```bash\napt-get install -y libpq-dev\n```"
    _EDIT_DELETE = "Edit: delete 3-4"
    _EDIT_MARKDOWN = "**Edit:** replace 5\n```\npip install psycopg2-binary\n```"
    _EDIT_NO_BLOCK = "Edit: replace 5"                       # replace w/o a following fence -> invalid
    _EXPLORE_ACTION = "Action: cat pyproject.toml"
    _EXPLORE_FENCE = "```bash\nfind . -name '*.cfg' | head\n```"     # mis-wrapped read probe -> explore
    _EXPLORE_COMPOUND = "```bash\ncd /app && cat setup.py\n```"      # readonly compound -> explore
    _INSTALL_FENCE = "```bash\npip install psycopg2\n```"           # install, not read-only -> invalid
    _PROSE = "I think the libpq headers are missing; we should add the -dev package."
    parse_inputs = {
        "parse/edit_replace_with_thought": _EDIT_REPLACE,
        "parse/edit_insert_after": _EDIT_INSERT,
        "parse/edit_delete_range": _EDIT_DELETE,
        "parse/edit_markdown_label": _EDIT_MARKDOWN,
        "parse/edit_replace_no_block_invalid": _EDIT_NO_BLOCK,
        "parse/explore_action_line": _EXPLORE_ACTION,
        "parse/explore_recovered_fence": _EXPLORE_FENCE,
        "parse/explore_recovered_compound": _EXPLORE_COMPOUND,
        "parse/install_fence_invalid": _INSTALL_FENCE,
        "parse/prose_invalid": _PROSE,
    }
    for name, text in parse_inputs.items():
        cases[name] = _fmt_action(parse_action(text))

    # -- action_from_tool_call: the PRIMARY native path (JSON args) --
    tool_inputs = {
        "tool/explore": ("explore", '{"command": "ls -la /app"}'),
        "tool/edit_replace": ("edit", '{"verb":"replace","start":5,"end":5,"content":"pip install psycopg2-binary"}'),
        "tool/edit_insert_no_end": ("edit", '{"verb":"insert","start":4,"content":"apt-get install -y libpq-dev"}'),
        "tool/edit_delete_span": ("edit", '{"verb":"delete","start":3,"end":4}'),
        "tool/edit_missing_content_invalid": ("edit", '{"verb":"replace","start":5}'),
        "tool/edit_bad_json_invalid": ("edit", "{not valid json"),
        "tool/unknown_fn_invalid": ("frobnicate", "{}"),
    }
    for name, (fn, args) in tool_inputs.items():
        cases[name] = _fmt_action(action_from_tool_call(fn, args))

    # -- apply_edit: the pure line-splice (result script text, or "None" out of range) --
    apply_inputs = {
        "apply/insert_top": EditOp("insert", 0, 0, "set -x"),
        "apply/insert_after_line": EditOp("insert", 4, 4, "apt-get install -y libpq-dev"),
        "apply/replace_single": EditOp("replace", 5, 5, "pip install psycopg2-binary"),
        "apply/replace_span_multiline": EditOp("replace", 3, 4, "apt-get update\napt-get install -y libpq-dev"),
        "apply/delete_span": EditOp("delete", 3, 4, ""),
        "apply/out_of_range_none": EditOp("replace", 99, 99, "x"),
    }
    for name, op in apply_inputs.items():
        cases[name] = str(apply_edit(_ACTIONS_SCRIPT, op))

    # -- extract_thought / extract_reasoning --
    cases["thought/labeled"] = extract_thought("Thought: the libpq headers are missing\nAction: ls")
    cases["thought/leading_prose"] = extract_thought(
        "The headers are missing.\nEdit: replace 4\n```\napt-get install -y libpq-dev\n```")
    cases["thought/bare_directive_empty"] = repr(extract_thought("Edit: delete 3"))
    cases["reasoning/think_block"] = extract_reasoning("<think>weighing the binary wheel</think>")
    cases["reasoning/plain_content"] = extract_reasoning("just fix the headers")
    return cases
