# src/envstate/maintainer.py
"""EnvState v1 — Maintainer: the single writer of the WorldModelMap.

The Maintainer receives a TaskReport from the BuildAgent and produces a new
WorldModelMap.  It calls the LLM once per cycle, parses the reply, and applies
the grounding rule before updating the map.

Grounding rule (§2 of the design doc):
    A Fact is added to `installed` only if the fact's name appears (case-
    insensitive substring) in at least one command's output within the report.
    This prevents the LLM from inventing facts that the command output did not
    demonstrate.

done_flag rule (§5):
    `done_flag` is set to True if any CommandRecord in the report has
    ``--collect-only`` in the cmd AND rc == 0.  Once True it stays True.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from src.envstate.llm_response import complete_with_retry
from src.envstate.world_model import (
    Fact,
    OpenProblem,
    TaskReport,
    WorldModelMap,
    merge_map,
)

# ---------------------------------------------------------------------------
# COLLECT_ONLY_CMD — the canonical done-gate substring
# ---------------------------------------------------------------------------

COLLECT_ONLY_CMD: str = "--collect-only"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

MAINTAINER_SYSTEM_PROMPT = """\
You are the Maintainer for the EnvState v1 system.  After each build-agent
task you receive the TaskReport (the commands that were run, their exit codes,
and their output) and the current WorldModelMap.  Your job is to produce an
updated map.

## Ground truth rule
Record only what the command output actually demonstrates.
- Add a package to `installed` only if the command output shows it was
  successfully installed (e.g. "Successfully installed flask-3.0.0").
- Do NOT invent facts that the output did not confirm.
- Interpret failures into `open_problems` with a suspected layer.

## done_flag
Set `done_flag` to true ONLY when a `pytest --collect-only` command in the
report exited with rc 0.  This is the EBSR gate.  Do not set done_flag for
any other command.

## Output schema
Return exactly one JSON object inside a ```json fenced block with these keys:

```json
{
  "installed": [{"name": "...", "detail": "..."}],
  "open_problems": [
    {"signature": "...", "interpretation": "...", "layer": "..."}
  ],
  "progress": {
    "base": false, "system": false, "runtime": false,
    "deps": false, "build": false, "tests": false
  },
  "notes": ["..."]
}
```

Field meanings:
- `installed`      — packages/tools confirmed present by this cycle's commands
- `open_problems`  — failures with a signature and suspected layer
- `progress`       — which stack layers are now complete (set True conservatively)
- `notes`          — durable cautions to preserve across future cycles

Do not include any other keys.  Do not mention done_flag in your output — the
harness sets it from the report directly.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE
)


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Extract and parse the first JSON object from a fenced code block.

    Returns None on any parse failure.
    """
    if not text:
        return None
    m = _FENCED_JSON_RE.search(text)
    raw = m.group(1).strip() if m else text.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _collect_only_passed(report: TaskReport) -> bool:
    """Return True iff any command in the report is a collect-only run that exited 0."""
    for rec in report.commands:
        if COLLECT_ONLY_CMD in rec.cmd and rec.rc == 0:
            return True
    return False


def _all_output_text(report: TaskReport) -> str:
    """Concatenate all command outputs into a single lowercased string for grounding."""
    return "\n".join(rec.output for rec in report.commands).lower()


def _ground_installed(
    proposed: list[dict[str, Any]],
    report: TaskReport,
) -> tuple[Fact, ...]:
    """Apply the grounding rule: keep only facts whose name appears in the output."""
    output_text = _all_output_text(report)
    facts: list[Fact] = []
    for item in proposed:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        if not name:
            continue
        # Grounding check: the fact's name must appear in the combined output.
        if name.lower() in output_text:
            facts.append(Fact(name=name, detail=str(item.get("detail", ""))))
    return tuple(facts)


def _parse_open_problems(
    proposed: list[dict[str, Any]],
) -> tuple[OpenProblem, ...]:
    """Parse open_problems from LLM reply; no grounding check (failures are trusted)."""
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
                interpretation=str(item.get("interpretation", "")),
                layer=str(item.get("layer", "deps")),
                out_of_scope=bool(item.get("out_of_scope", False)),
            )
        )
    return tuple(problems)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_v1_maintainer_reply(
    text: str,
    current_map: WorldModelMap,
    report: TaskReport,
) -> WorldModelMap:
    """Parse the LLM's reply and return an updated WorldModelMap.

    Applies the grounding rule (installed facts must be evidenced in output)
    and the done_flag rule (collect-only rc 0 → done_flag = True).

    On empty or unparseable input, returns the current map unchanged.
    """
    parsed = _extract_json(text)
    if parsed is None:
        # Preserve done_flag if already True (but don't set it here).
        return current_map

    # --- installed (grounded) ---
    proposed_installed = parsed.get("installed") or []
    new_facts = _ground_installed(proposed_installed, report)
    # Merge with existing installed facts (deduplicate by name).
    existing_names = {f.name for f in current_map.installed}
    merged_installed = current_map.installed + tuple(
        f for f in new_facts if f.name not in existing_names
    )

    # --- open_problems ---
    proposed_problems = parsed.get("open_problems") or []
    new_problems = _parse_open_problems(proposed_problems)
    existing_sigs = {p.signature for p in current_map.open_problems}
    merged_problems = current_map.open_problems + tuple(
        p for p in new_problems if p.signature not in existing_sigs
    )

    # --- progress ---
    progress_from_llm = parsed.get("progress")
    if isinstance(progress_from_llm, dict):
        # Merge: once a layer is True it stays True.
        new_progress = dict(current_map.progress)
        for layer, val in progress_from_llm.items():
            if layer in new_progress:
                new_progress[layer] = new_progress[layer] or bool(val)
    else:
        new_progress = dict(current_map.progress)

    # --- notes (append, never replace) ---
    new_note_strings = parsed.get("notes") or []
    added_notes = tuple(str(n) for n in new_note_strings if str(n) not in current_map.notes)
    merged_notes = current_map.notes + added_notes

    # --- done_flag ---
    # Structural rule: set if collect-only passed; once True, stays True.
    done = current_map.done_flag or _collect_only_passed(report)

    return merge_map(
        current_map,
        installed=merged_installed,
        open_problems=merged_problems,
        progress=new_progress,
        done_flag=done,
        notes=merged_notes,
    )


class Maintainer:
    """v1 Maintainer — single writer of the WorldModelMap.

    Constructor:
        client    — OpenAI-compatible client
        model     — model slug
        on_usage  — optional callback(dict) called with token usage after each
                    LLM call; dict has keys ``input_tokens`` and ``output_tokens``
        log_path  — optional path for debug logs (not used in v1 unit tests)
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
        (except for structural done_flag / collect-only detection which still
        applies from the report itself).
        """
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
                    "open_problems": [
                        {
                            "signature": p.signature,
                            "interpretation": p.interpretation,
                            "layer": p.layer,
                        }
                        for p in current_map.open_problems
                    ],
                    "progress": dict(current_map.progress),
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

        content, usage, _response = complete_with_retry(
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

        return parse_v1_maintainer_reply(content, current_map, report)
