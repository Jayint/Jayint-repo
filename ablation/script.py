"""Deterministic rendering and failure localization for a flat build plan."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import FlatPlan


BLOCK_MARKER_PREFIX = "__ABLATION_BLOCK__:"
_MARKER_RE = re.compile(r"__ABLATION_BLOCK__:([a-z][a-z0-9._-]{0,63})")


@dataclass(frozen=True)
class RenderedCommand:
    line: int
    block_id: str
    command: str


@dataclass(frozen=True)
class RenderedScript:
    text: str
    commands: tuple[RenderedCommand, ...]


def render_plan(plan: FlatPlan) -> RenderedScript:
    lines = ["#!/usr/bin/env bash", "set -Eeuo pipefail"]
    rendered_commands: list[RenderedCommand] = []

    for block in plan.blocks:
        lines.append("")
        lines.append(f"#@block id={block.block_id}")
        lines.append(
            f"printf '%s\\n' '{BLOCK_MARKER_PREFIX}{block.block_id}' >&2"
        )
        for command in block.commands:
            lines.append(command)
            rendered_commands.append(
                RenderedCommand(len(lines), block.block_id, command)
            )

    return RenderedScript(
        text="\n".join(lines) + "\n",
        commands=tuple(rendered_commands),
    )


def locate_failed_block(
    rendered: RenderedScript,
    *,
    output: str,
    failing_command: str | None,
    lineno: int | None,
) -> str | None:
    """Locate a failed block without inventing a match.

    Line metadata is preferred because it disambiguates identical commands.
    Block markers are the next strongest signal; command text is used only when
    it uniquely identifies one block.
    """

    if lineno is not None:
        for candidate_line in (lineno, lineno - 1):
            matches = [
                item.block_id
                for item in rendered.commands
                if item.line == candidate_line
            ]
            if len(set(matches)) == 1:
                return matches[0]

    markers = _MARKER_RE.findall(output or "")
    valid_ids = {item.block_id for item in rendered.commands}
    for marker in reversed(markers):
        if marker in valid_ids:
            return marker

    needle = (failing_command or "").strip()
    if needle:
        matches = {
            item.block_id
            for item in rendered.commands
            if item.command.strip() == needle
        }
        if len(matches) == 1:
            return next(iter(matches))
    return None
