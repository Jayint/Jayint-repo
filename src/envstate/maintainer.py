# src/envstate/maintainer.py
"""EnvState v1 — Maintainer: interpreter of the WorldModelMap.

The Maintainer receives a TaskReport from the BuildAgent and produces a new
WorldModelMap.  It calls the LLM once per cycle, parses the reply, and updates
open_problems / resolved / notes only.

Narrowed contract (Task 7):
    Facts (installed/progress/build_system/required/env) are owned by
    apply_deterministic and are NOT touched here.

done_flag rule (§5):
    `done_flag` is set to True if any CommandRecord in the report has
    ``--collect-only`` in the cmd AND rc == 0.  Once True it stays True.
"""
from __future__ import annotations

import json
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
# COLLECT_ONLY_CMD — the canonical done-gate substring
# ---------------------------------------------------------------------------

COLLECT_ONLY_CMD: str = "--collect-only"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

MAINTAINER_SYSTEM_PROMPT = """\
You are the Maintainer for the EnvState v1 system.  Installed packages, the
build system, declared requirements, and the interpreter are ALREADY filled in
by the host (authoritative) — do NOT report them.  Your only job is to interpret
this cycle's TaskReport: record new failures, clear fixed ones, and keep notes.

## Ground truth rule
Record only what the command output actually demonstrates.
- Interpret failures into `open_problems` with a suspected layer.
- List in `resolved` the `signature` of any existing problem the output shows is now fixed.

## done_flag (informational — you never set this)
The harness sets done_flag when a `pytest --collect-only` command exits 0.

## Output schema
Return exactly one JSON object inside a ```json fenced block with these keys:

```json
{
  "open_problems": [{"signature": "...", "interpretation": "...", "layer": "..."}],
  "resolved": ["<signature of a now-fixed problem>"],
  "notes": ["..."]
}
```

Do not include any other keys.  Do not report installed packages or progress.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_only_passed(report: TaskReport) -> bool:
    """Return True iff any command in the report is a collect-only run that exited 0."""
    for rec in report.commands:
        if COLLECT_ONLY_CMD in rec.cmd and rec.rc == 0:
            return True
    return False


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
    """Parse the narrowed Maintainer reply: open_problems + resolved + notes.

    Facts (installed/progress/build_system/required/env) are owned by
    apply_deterministic and are NOT touched here. done_flag is structural
    (collect-only rc 0 in the report). On empty/unparseable input, only the
    structural done_flag rule applies.
    """
    parsed = extract_json_object(text) if text else None
    if not parsed:
        new_done = current_map.done_flag or _collect_only_passed(report)
        if new_done != current_map.done_flag:
            return merge_map(current_map, done_flag=new_done)
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

    # notes: append (never replace)
    added_notes = tuple(
        str(n) for n in (parsed.get("notes") or []) if str(n) not in current_map.notes
    )
    merged_notes = current_map.notes + added_notes

    done = current_map.done_flag or _collect_only_passed(report)
    return merge_map(
        current_map,
        open_problems=merged,
        notes=merged_notes,
        done_flag=done,
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
