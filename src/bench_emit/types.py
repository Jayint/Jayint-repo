from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EmittedEnv:
    dockerfile: str | None            # None => no derivable Dockerfile (status "missing")
    scripts: dict = field(default_factory=dict)   # {name: content} sibling files the Dockerfile COPYs
    meta: dict = field(default_factory=dict)      # bench_meta.json payload (only known keys)
