"""Agentic observation envelope — command → result, the unit of a real agent transcript.

The classic observation opens with a synthesized verdict:

    BUILD OK. TESTS 0/5 passed.

Two things are wrong with that line, and this module fixes both.

**It is not a tool result, it is a narrator.** An agent transcript's atom is `command → output`;
ours shipped output with no command attached. The models are post-trained on the former. Worse, the
single strangest fact about this arm — that the WHOLE script re-runs from a clean container every
turn — had to be explained in three sentences of system prose because the transcript never showed
it. Print the command and it explains itself:

    $ bash setup.sh                      # fresh container from the base image — EVERY turn
    exit 0

**And "0/5 passed" is false.** That repo has 297 tests. Zero of them ran. The "5" is five *modules*
that failed to import. `executed = passed + failed + errors` is the right GATE denominator (see
gate.py) but rendering it as a ratio tells the model five tests exist and none passed, which is not
a thing that happened. Report what pytest reported:

    $ python -m pytest -q
    0 passed, 0 failed, 5 collection errors — no tests ran

Pure render layer. The canonical `Step.observation_raw` keeps its legacy header (extract_blocker and
the do-not-retry ledger parse it, and the blob path still renders it), so this strips that header off
and re-frames the same bytes from the structured outcome recorded alongside it.
"""
from __future__ import annotations

import re

# The reset is the whole game and it was invisible. Say it where it happens, every time.
_RESET_NOTE = "# fresh container from the base image — EVERY turn"
_BUILD_CMD = "bash setup.sh"

# The legacy headers _observation() prepends to the stored body. Stripped before re-framing so the
# same fact isn't stated twice, in two voices.
_LEGACY_OK = re.compile(r"^BUILD OK\. TESTS \d+/\d+ passed[^\n]*\n?")
_LEGACY_FAIL = re.compile(r"^BUILD FAILED at `[^`]*`(?: \(line \d+\))?:\n?")


def strip_legacy_header(observation: "str | None") -> str:
    """The stored observation minus its synthesized `BUILD OK …` / `BUILD FAILED at …` header line."""
    text = observation or ""
    text = _LEGACY_OK.sub("", text, count=1)
    text = _LEGACY_FAIL.sub("", text, count=1)
    return text.lstrip("\n")


def _pytest_counts(o: dict) -> str:
    """What pytest actually reported — its own counts, never fused into a ratio.

    No exit code is printed: we never captured pytest's rc (TestOutcome.ok is the HOST's 80% gate
    verdict, not pytest's return value), and inventing one would be the same sin as the fake ratio."""
    passed, failed = int(o.get("passed", 0)), int(o.get("failed", 0))
    errors, skipped = int(o.get("errors", 0)), int(o.get("skipped", 0))
    collected = int(o.get("collected", 0))
    parts = [f"{passed} passed", f"{failed} failed"]
    if errors:
        parts.append(f"{errors} collection error{'' if errors == 1 else 's'}")
    if skipped:
        parts.append(f"{skipped} skipped")
    line = ", ".join(parts)
    if passed + failed == 0:
        return line + " — no tests ran"
    if collected > passed + failed:               # the silent-skip gap the old header tried to show
        line += f" — {collected} tests collected"
    return line


def run_envelope(outcome: "dict | None") -> str:
    """The `$ command → result` header for a build/test observation, from the structured outcome.

    Build failed  → the script's exit + the line it halted on (the numbered script marks the same line).
    Build green   → exit 0, then the pytest command and pytest's own counts."""
    o = outcome or {}
    head = f"$ {_BUILD_CMD}{' ' * 10}{_RESET_NOTE}"
    if not o.get("build_ok", False):
        cmd = o.get("failing_command") or "(unknown command)"
        lineno = o.get("lineno")
        where = f" at line {lineno}" if lineno else ""
        return f"{head}\nexit 1 — halted{where}: `{cmd}`"
    test_cmd = o.get("test_command") or "python -m pytest -q"
    if not o.get("ran_tests", False):
        return f"{head}\nexit 0"
    return f"{head}\nexit 0\n\n$ {test_cmd}\n{_pytest_counts(o)}"


_EDIT_ECHO_LINES = 6                                  # cap on the lines echoed back from a splice


def edit_result(action: "dict | None") -> "str | None":
    """The tool RESULT of an `edit` call — what a real file-editing tool returns: confirmation the
    splice landed, and the lines it landed on. None for non-edit actions.

    This is the only honest home for line numbers. A past assistant turn reading `edit(replace @7-8)`
    is unresolvable later — after one insert, line 7 is a different line, and only the CURRENT script
    is ever shown, so there is nothing in the prompt to resolve the number against. Here the numbers
    sit next to the content they produced, at the moment they were true."""
    if not action or action.get("kind") != "edit":
        return None
    verb, start = action.get("verb"), action.get("start")
    end = action.get("end", start)
    if verb == "delete":
        span = f"line {start}" if end == start else f"lines {start}-{end}"
        return f"setup.sh updated — deleted {span}."
    content = (action.get("content") or "").splitlines()
    if not content or not isinstance(start, int):
        return "setup.sh updated."
    shown = [f"  {start + i}| {ln}" for i, ln in enumerate(content[:_EDIT_ECHO_LINES])]
    if len(content) > _EDIT_ECHO_LINES:
        shown.append(f"  … (+{len(content) - _EDIT_ECHO_LINES} more lines)")
    return "setup.sh updated:\n" + "\n".join(shown)
