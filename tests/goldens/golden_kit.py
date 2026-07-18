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


def history_transcripts() -> dict:
    """The named T2 transcripts, each captured through all four render styles."""
    return {"resolve": history_transcript(), "stuck": stuck_transcript()}


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


def observe_cases() -> dict:
    """The observe cluster's pure renders: the `$ cmd -> result` envelope, edit tool-results,
    the ranked pytest cause histogram, block dedup, and the noise-strip compression."""
    from src.agent.envelope import edit_result, run_envelope
    from src.agent.observation import safety_compress_observation, strip_pip_progress
    from src.agent.pytest_blocks import compact_pytest_blocks
    from src.agent.pytest_summary import format_breakdown, summarize

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
    return cases


def gate_cases() -> dict:
    """The gate verdict across the >=80% boundary + the anti-hollow guards, and the anti-gaming
    detectors (test-collection narrowing + self-install-from-index), incl. must-NOT-trip cases."""
    from src.agent.anti_cheat import (
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
