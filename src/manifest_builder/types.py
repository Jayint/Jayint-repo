from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CollectionResult:
    exit_code: int
    collected: tuple[str, ...] = ()
    collect_errors: tuple[str, ...] = ()
    skipped_modules: tuple[str, ...] = ()
    deselected: tuple[str, ...] = ()

    @property
    def collected_count(self) -> int:
        return len(self.collected)


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reasons: tuple[str, ...]
    manifest: tuple[str, ...] | None
    collected_count: int
