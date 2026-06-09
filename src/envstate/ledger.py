from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class ActionEvent:
    step: int
    cmd: str
    rc: int
    # Optional fields — v1 ledger-appender path stores inline stdout; legacy
    # path stores file paths (stdout_path/stderr_path) and richer metadata.
    task_id: Optional[str] = None
    stdout: str = ""
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None
    env_revision_before: int = 0
    env_revision_after: int = 0
    mutation_class: Optional[str] = None
    container_id: str = ""
    summary: str = ""


class ActionLedger:
    """Append-only host-generated command/event history (the one mutable container)."""

    def __init__(self) -> None:
        self._events: List[ActionEvent] = []

    def append(self, event: ActionEvent) -> None:
        self._events.append(event)

    def events(self) -> Tuple[ActionEvent, ...]:
        return tuple(self._events)

    def to_list(self) -> list[dict[str, Any]]:
        return [asdict(event) for event in self._events]
