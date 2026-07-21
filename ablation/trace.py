"""Small JSONL-compatible trace recorder for the ablation."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceRecorder:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        record = dict(event)
        record.setdefault(
            "timestamp",
            datetime.now(timezone.utc).isoformat(),
        )
        self._events.append(record)

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(event) for event in self._events)

    def write_jsonl(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as handle:
            for event in self._events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def emit(
    sink,
    event_type: str,
    **payload: Any,
) -> None:
    if sink is None:
        return
    sink({"event": event_type, **payload})
