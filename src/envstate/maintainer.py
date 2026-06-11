# src/envstate/maintainer.py
"""EnvState v1 — Maintainer: interpreter of the WorldModelMap.

The Maintainer receives a TaskReport from the BuildAgent and produces a new
WorldModelMap.  It calls the LLM once per cycle, parses the reply, and updates
open_problems / resolved / notes only.

Narrowed contract (Task 7):
    Facts (installed/progress/build_system/required/env) are owned by
    apply_deterministic and are NOT touched here.

done_flag rule (§5, Phase 1 — execution-aware gate):
    `done_flag` is set to True when any CommandRecord in the report satisfies
    ALL of the following:
      1. rc == 0
      2. is_test_command(cmd) is True  (Synthesizer classifier)
      3. NOT venv-wrapped (not run under poetry/pipenv/hatch/conda/pdm)
      4. analyze_test_run(cmd, output)["is_effective_test_run"] is True
      5. _shows_execution(output) is True — output contains an execution
         summary (e.g. "N passed" or "Ran N tests"), NOT merely
         "collected N items" from a pure --collect-only run.
    Once True it stays True.
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Any, Callable, Optional

from src.envstate.diagnostics import log_llm_exchange
from src.envstate.jsonutil import extract_json_object
from src.envstate.llm_response import complete_with_retry
from src.envstate.world_model import (
    Fact,
    OpenProblem,
    TaskReport,
    WorldModelMap,
    merge_map,
)

# ---------------------------------------------------------------------------
# Execution-aware done-gate helpers
# ---------------------------------------------------------------------------

# Module-level detector singleton — lazy-imported to avoid circular imports.
# Use _get_detector() instead of _DETECTOR directly in case synthesizer has
# not yet been imported at module load time.
_DETECTOR = None


def _get_detector():
    """Return the module-level Synthesizer singleton, creating it on first call."""
    global _DETECTOR
    if _DETECTOR is None:
        from src.synthesizer import Synthesizer  # lazy to avoid circular import
        _DETECTOR = Synthesizer()
    return _DETECTOR


# Venv runner prefixes that wrap the test command — the grader uses the bare
# system interpreter, so commands run under these wrappers do NOT count.
_VENV_WRAPPERS: frozenset[str] = frozenset({
    "poetry", "pipenv", "hatch", "conda", "pdm",
})


def _is_venv_wrapped(cmd: str) -> bool:
    """Return True iff *cmd* is run under a venv manager (poetry/pipenv/hatch/conda/pdm).

    Detects the pattern ``<wrapper> run <...>`` at the start of the command
    (after optional path prefix). Case-insensitive on the wrapper name.
    Never raises.
    """
    try:
        tokens = shlex.split(cmd.strip())
    except ValueError:
        tokens = cmd.strip().split()
    if len(tokens) < 2:
        return False
    # The first token may be a full path, e.g. /usr/local/bin/poetry.
    first = tokens[0].split("/")[-1].lower()
    # Pattern: <wrapper> run ...
    return first in _VENV_WRAPPERS and len(tokens) >= 2 and tokens[1] == "run"


# Regexes for execution summary detection (condition 5 of the gate).
# Matches "N passed" (pytest) and "Ran N tests" (unittest) with N >= 1.
_RE_N_PASSED = re.compile(r"\b([1-9]\d*)\s+passed\b", re.IGNORECASE)
_RE_RAN_N_TESTS = re.compile(r"\bran\s+([1-9]\d*)\s+tests?\b", re.IGNORECASE)


def _shows_execution(output: str) -> bool:
    """Return True iff *output* shows a genuine test execution summary.

    Accepts:
      - "N passed ..."  (pytest) with N >= 1
      - "Ran N tests ..." (unittest) with N >= 1

    Rejects:
      - "collected N items"  (pure --collect-only, the hole we are closing)
      - ""  (no output)
      - "0 passed"  (zero tests pass does not satisfy >=1 requirement)
    """
    if not output:
        return False
    return bool(_RE_N_PASSED.search(output) or _RE_RAN_N_TESTS.search(output))


# Exclusion flags that hide pre-existing test paths and can manufacture a
# spurious green run (Phase 4 anti-gaming check).
_EXCLUSION_FLAG_PREFIXES: tuple[str, ...] = (
    "--ignore=",
    "--ignore-glob=",
    "--ignore-glob",
    "--ignore",
    "--deselect",
)


def _uses_test_exclusion(cmd: str) -> bool:
    """Return True iff *cmd* contains a pytest exclusion flag that skips test paths.

    Flags detected: ``--ignore``, ``--ignore=<value>``, ``--ignore-glob``,
    ``--ignore-glob=<value>``, ``--deselect``.

    Matching is done on whole tokens produced by ``shlex.split``.  A token
    matches when it equals a bare flag (e.g. ``--ignore``) or starts with the
    flag followed by ``=`` (e.g. ``--ignore=examples``).  This avoids false
    positives from paths that merely contain the substring "ignore".

    Falls back to whitespace split on ``ValueError`` (malformed shell quoting),
    mirroring ``_is_collect_only_cmd``.  Never raises.
    """
    try:
        tokens = shlex.split(cmd.strip())
    except ValueError:
        tokens = cmd.strip().split()
    _bare_flags: frozenset[str] = frozenset({"--ignore", "--ignore-glob", "--deselect"})
    _eq_prefixes: tuple[str, ...] = ("--ignore=", "--ignore-glob=", "--deselect=")
    for tok in tokens:
        if tok in _bare_flags:
            return True
        if any(tok.startswith(p) for p in _eq_prefixes):
            return True
    return False


def _verified_test_run_passed(
    report: TaskReport,
    detector=None,
) -> bool:
    """Return True iff any command in *report* is a verified passing test execution.

    Gate (all six conditions must hold):
      1. rec.rc == 0
      2. detector.is_test_command(rec.cmd)
      3. NOT _is_venv_wrapped(rec.cmd)
      4. NOT _uses_test_exclusion(rec.cmd)  — Phase 4 anti-gaming guard:
         a verification that deliberately ignores/deselects pre-existing test
         paths is not a trustworthy success signal.  The n8n-autoscaling case
         used ``--ignore=examples`` to hide a real SyntaxError in examples/;
         accepting that as a pass would produce a false success.  Deliberate
         strictness: the only trustworthy verification is a full, unfiltered run.
      5. detector.analyze_test_run(rec.cmd, rec.output)["is_effective_test_run"]
      6. _shows_execution(rec.output)
    """
    if detector is None:
        detector = _get_detector()
    for rec in report.commands:
        if rec.rc != 0:
            continue
        if not detector.is_test_command(rec.cmd):
            continue
        if _is_venv_wrapped(rec.cmd):
            continue
        if _uses_test_exclusion(rec.cmd):
            continue
        output = rec.output or ""
        if not detector.analyze_test_run(rec.cmd, output).get("is_effective_test_run", False):
            continue
        if not _shows_execution(output):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Back-compat: keep the collect-only helpers for orchestrator.py import
# ---------------------------------------------------------------------------

COLLECT_ONLY_CMD: str = "--collect-only"
_COLLECT_ONLY_TOKENS: frozenset[str] = frozenset({COLLECT_ONLY_CMD, "--co"})


def _looks_like_pytest(tok: str) -> bool:
    """True if a command token is a pytest entry point (pytest / py.test / .../pytest)."""
    return tok in ("pytest", "py.test") or tok.endswith("/pytest")


def _is_collect_only_cmd(cmd: str) -> bool:
    """Return True iff *cmd* is a pytest collection run (back-compat; not used by gate)."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    has_pytest = any(_looks_like_pytest(t) for t in tokens)
    has_collect_only = any(t in _COLLECT_ONLY_TOKENS for t in tokens)
    return has_pytest and has_collect_only

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

MAINTAINER_SYSTEM_PROMPT = """\
You are the State Maintainer for a Docker environment setup agent. You read the
current Environment State Map and the latest BuildAgent TaskReport (both given as
JSON in the next message) and propose structured updates to the map.

You are NOT the build agent. You do NOT run shell commands. You do NOT certify
that packages, tools, headers, imports, or services are present or missing — only
deterministic host scripts and probes certify facts. You do NOT set done_flag (the
host sets it when a bare `python -m pytest -q` run exits 0 with at least one passed
test — execution-aware gate, not merely pytest --collect-only).

## What you are given (read-only — never restate it as output)
- current_map: host-certified, authoritative facts — `installed`, `required`, `missing`
  (`required - installed`), `env` (interpreter / venv), `system_tools`,
  `build_tools`, `os_release`, plus existing `open_problems` and `notes`.
- task_report: this cycle's transcript — `commands` (each `{cmd, rc, output}`),
  `status`, and `learning`.

## How to read the transcript
- Failure signatures live in `commands[*].output`. Prefer commands where `rc != 0`.
- `learning` is a weak one-line summary, NOT evidence — never infer a fact from it.
- `status` is task-level (done/blocked), not proof the environment works.

## Your job
1. Copy each failure signature LITERALLY from the command output — do not paraphrase.
   Good: "pg_config executable not found"
   Bad:  "Postgres config is missing"
   Put your reasoning in `hypothesis`; keep `signature` verbatim.
2. Classify each problem's `kind` (the host uses this to route deterministic
   resolution, so getting it right is what lets the host auto-clear the problem):
     native_build_dependency | system_tool_missing | header_missing
        -> a missing native tool / library / header
     language_package_missing | import_failure
        -> a missing language package / failed import
     test_failure   -> a pytest collection or run error
     config_missing -> a missing config the build/run needs
     unknown        -> cannot tell from the output
3. Mark each problem `root` or `downstream`. Example: if `pip install` fails because
   `pg_config` is missing, then "No module named 'psycopg2'" is `downstream`.
4. In `resolved`, list a signature ONLY when THIS cycle's output shows that exact
   failure no longer occurs (you are reporting the transcript, not certifying the
   environment — the host re-probes to confirm fixes).
5. If output is truncated or ambiguous, say so in `planner_notes` and do not invent
   missing details.

## Output
Return exactly one JSON object inside a ```json fenced block — nothing else:

```json
{
  "open_problems": [
    {
      "signature": "literal error text from output",
      "kind": "native_build_dependency | system_tool_missing | header_missing | language_package_missing | import_failure | test_failure | config_missing | unknown",
      "hypothesis": "short explanation",
      "root_or_downstream": "root | downstream | unknown"
    }
  ],
  "resolved": ["<signature the transcript shows no longer occurs>"],
  "planner_notes": ["short note for the next planning step"]
}
```

## Hard safety rule
If you are tempted to write present, missing, installed, fixed, verified, or
working as a fact, do not — just record the literal signature and let the host's
probes certify reality.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_only_passed(report: TaskReport) -> bool:
    """Return True iff any command in the report is a collect-only run that exited 0.

    Kept for back-compat — NOT used by the done-gate (which uses
    _verified_test_run_passed instead). May be removed when no longer referenced.
    """
    for rec in report.commands:
        if rec.rc == 0 and _is_collect_only_cmd(rec.cmd):
            return True
    return False


# Map the Maintainer's concrete failure `kind` onto the coarse stack `layer` the
# deterministic resolver keys on. The model reasons in error-kinds (what it can
# see in the output); the host derives the layer (what auto_resolve needs).
# Crucially the three native kinds map to "system", so _auto_resolve_system_problems
# (which fires only on layer=="system") can finally engage.
_KIND_TO_LAYER: dict[str, str] = {
    "native_build_dependency": "system",
    "system_tool_missing": "system",
    "header_missing": "system",
    "language_package_missing": "deps",
    "import_failure": "deps",
    "test_failure": "tests",
    "config_missing": "build",
    "unknown": "deps",
}


def _layer_for(item: dict[str, Any]) -> str:
    """Derive the stack layer from the LLM's `kind` (new schema), falling back to an
    explicit `layer` for back-compat with older replies / serialized maps."""
    kind = item.get("kind")
    if kind is not None:
        return _KIND_TO_LAYER.get(str(kind), "deps")
    return str(item.get("layer", "deps"))


def _interpretation_for(item: dict[str, Any]) -> str:
    """Compose the stored interpretation from `hypothesis` (new schema; back-compat
    `interpretation`), tagged with root/downstream when the model supplied it."""
    text = str(item.get("hypothesis", item.get("interpretation", "")))
    rod = str(item.get("root_or_downstream", "")).strip().lower()
    if rod in ("root", "downstream"):
        return f"[{rod}] {text}".strip()
    return text


def _parse_open_problems(
    proposed: list[dict[str, Any]],
) -> tuple[OpenProblem, ...]:
    """Parse open_problems from an LLM reply (no grounding check; failures trusted).

    New schema item: {signature, kind, hypothesis, root_or_downstream}. `kind` is
    mapped to the resolver's `layer`; `hypothesis` (+ a root/downstream tag) becomes
    the stored interpretation. Old {signature, interpretation, layer} items still parse.
    """
    problems: list[OpenProblem] = []
    for item in proposed:
        if not isinstance(item, dict):
            continue
        sig = item.get("signature", "")
        if not sig:
            continue
        problems.append(
            OpenProblem(
                signature=sig,
                interpretation=_interpretation_for(item),
                layer=_layer_for(item),
                out_of_scope=bool(item.get("out_of_scope", False)),
            )
        )
    return tuple(problems)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _progress_synced_with_done(
    current_map: WorldModelMap, done: bool
) -> dict[str, bool] | None:
    """Keep the derived ``progress['tests']`` consistent with the structural
    ``done_flag`` (§4.3: tests == done_flag).

    apply_deterministic derives progress *before* the Maintainer runs, so in the
    terminal cycle ``tests`` would otherwise lag one step (done_flag True but
    progress.tests False, with no next fold to catch up). Returns ``None`` when no
    change is needed so merge_map keeps the current dict untouched.
    """
    if done and not current_map.progress.get("tests", False):
        return {**current_map.progress, "tests": True}
    return None


def parse_v1_maintainer_reply(
    text: str,
    current_map: WorldModelMap,
    report: TaskReport,
) -> WorldModelMap:
    """Parse the narrowed Maintainer reply: open_problems + resolved + notes.

    Facts (installed/build_system/required/env and the derived progress layers)
    are owned by apply_deterministic. The only progress layer touched here is
    ``tests``, kept consistent with the structural ``done_flag`` (execution-aware
    gate: a real test run with >=1 passed, bare interpreter, no venv wrapper).
    On empty/unparseable input, only the structural done_flag rule (and its tests
    sync) applies.
    """
    parsed = extract_json_object(text) if text else None
    if not parsed:
        new_done = current_map.done_flag or _verified_test_run_passed(report)
        if new_done != current_map.done_flag:
            return merge_map(
                current_map,
                done_flag=new_done,
                progress=_progress_synced_with_done(current_map, new_done),
            )
        return current_map

    # open_problems: append new (dedup by signature)
    new_problems = _parse_open_problems(parsed.get("open_problems") or [])
    existing_sigs = {p.signature for p in current_map.open_problems}
    merged = current_map.open_problems + tuple(
        p for p in new_problems if p.signature not in existing_sigs
    )

    # resolved: drop listed signatures
    resolved = {str(s) for s in (parsed.get("resolved") or [])}
    if resolved:
        merged = tuple(p for p in merged if p.signature not in resolved)

    # planner_notes (new schema) → notes; back-compat with legacy "notes". Append.
    _incoming_notes = parsed.get("planner_notes")
    if _incoming_notes is None:
        _incoming_notes = parsed.get("notes") or []
    added_notes = tuple(
        str(n) for n in _incoming_notes if str(n) not in current_map.notes
    )
    merged_notes = current_map.notes + added_notes

    done = current_map.done_flag or _verified_test_run_passed(report)
    return merge_map(
        current_map,
        open_problems=merged,
        notes=merged_notes,
        done_flag=done,
        progress=_progress_synced_with_done(current_map, done),
    )


class Maintainer:
    """v1 Maintainer — single writer of the WorldModelMap.

    Constructor:
        client    — OpenAI-compatible client
        model     — model slug
        on_usage  — optional callback(dict) called with token usage after each
                    LLM call; dict has keys ``input_tokens`` and ``output_tokens``
        log_path  — optional path for the diagnostic LLM-exchange log
                    (falls back to the ENVSTATE_LLM_LOG env var)
    """

    def __init__(
        self,
        client: Any,
        model: str,
        on_usage: Optional[Callable[[dict[str, Any]], None]] = None,
        log_path: Optional[str] = None,
    ) -> None:
        self._client = client
        self._model = model
        self._on_usage = on_usage
        self._log_path = log_path

    def update(
        self,
        current_map: WorldModelMap,
        report: TaskReport,
    ) -> WorldModelMap:
        """Call the LLM once and return an updated WorldModelMap.

        Never mutates *current_map* (WorldModelMap is frozen).
        On empty or unparseable LLM output, returns *current_map* unchanged
        (except for the structural done_flag / execution-gate check which still
        applies from the report itself).
        """
        # Pre-filled authoritative facts + the required-installed gap (design §4.4),
        # so the LLM can attribute a failure to a layer and judge `resolved`.
        _installed_names = {f.name.lower() for f in current_map.installed}
        _missing = [f.name for f in current_map.required if f.name.lower() not in _installed_names]
        _sys_tools = [f.name for f in current_map.system_installed if f.detail in ("tool", "pkgconfig")]

        # Build the user message: current map + task report.
        user_content = json.dumps(
            {
                "current_map": {
                    "base_image": current_map.base_image,
                    "workdir": current_map.workdir,
                    "language": current_map.language,
                    "build_system": current_map.build_system,
                    "repo_layout": list(current_map.repo_layout),
                    "installed": [
                        {"name": f.name, "detail": f.detail}
                        for f in current_map.installed
                    ],
                    "required": [
                        {"name": f.name, "detail": f.detail}
                        for f in current_map.required
                    ],
                    "missing": _missing,
                    "env": dict(current_map.env),
                    "system_tools": _sys_tools[:40],
                    "os_release": current_map.env.get("os_release", ""),
                    "build_tools": current_map.env.get("build_tools", ""),
                    "open_problems": [
                        {
                            "signature": p.signature,
                            "interpretation": p.interpretation,
                            "layer": p.layer,
                        }
                        for p in current_map.open_problems
                    ],
                    "done_flag": current_map.done_flag,
                    "notes": list(current_map.notes),
                },
                "task_report": {
                    "task_goal": report.task_goal,
                    "status": report.status,
                    "commands": [
                        {"cmd": r.cmd, "rc": r.rc, "output": r.output}
                        for r in report.commands
                    ],
                    "learning": report.learning,
                },
            }
        )

        messages = [
            {"role": "system", "content": MAINTAINER_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        content, usage, response = complete_with_retry(
            self._client,
            self._model,
            messages,
            accept=None,  # retry on empty only
            retry_nudge=(
                "Your previous response was empty. "
                "Return exactly one JSON object inside a ```json fenced block."
            ),
            temperature=0,
        )

        # Fire usage callback if provided.
        if self._on_usage is not None:
            self._on_usage(usage)

        new_map = parse_v1_maintainer_reply(content, current_map, report)

        # Diagnostic trace (no-op unless a log path / ENVSTATE_LLM_LOG is set).
        log_llm_exchange(
            "maintainer",
            response,
            parsed={
                "done_flag": new_map.done_flag,
                "open_problems_count": len(new_map.open_problems),
            },
            log_path=self._log_path,
        )
        return new_map
