from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class ActionEvent:
    step: int
    task_id: Optional[str]
    cmd: str
    rc: int
    stdout_path: Optional[str]
    stderr_path: Optional[str]
    env_revision_before: int
    env_revision_after: int
    mutation_class: Optional[str]
    container_id: str
    summary: str


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
