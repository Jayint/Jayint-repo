"""Typed, graph-free contracts for the ExecuteAgent-only ablation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal


def _string_tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    source: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "content": self.content,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    items: tuple[EvidenceItem, ...] = ()
    max_render_chars: int = 160_000

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(item.evidence_id for item in self.items)

    def get(self, evidence_id: str) -> EvidenceItem | None:
        return next(
            (item for item in self.items if item.evidence_id == evidence_id),
            None,
        )

    def with_item(self, item: EvidenceItem) -> "EvidenceBundle":
        current = self.get(item.evidence_id)
        if current is not None:
            if current == item:
                return self
            raise ValueError(f"duplicate evidence id: {item.evidence_id}")
        return EvidenceBundle(self.items + (item,), self.max_render_chars)

    def render(self, *, max_chars: int | None = None) -> str:
        max_chars = self.max_render_chars if max_chars is None else max_chars
        sections: list[str] = []
        used = 0
        def priority(item: EvidenceItem) -> tuple[int, str]:
            if item.evidence_id.startswith("runtime:"):
                rank = 0
            elif item.evidence_id.startswith("host:") or item.evidence_id.startswith("host."):
                rank = 1 if item.evidence_id != "host.repo_tree" else 3
            else:
                rank = 2
            return rank, item.evidence_id

        for item in sorted(self.items, key=priority):
            header = f"### {item.evidence_id}\nsource: {item.source}\n"
            remaining = max_chars - used - len(header)
            if remaining <= 0:
                break
            content = item.content
            if len(content) > remaining:
                marker = "\n...[truncated]"
                if remaining <= len(marker):
                    content = marker[:remaining]
                else:
                    content = content[: remaining - len(marker)] + marker
            section = header + content
            sections.append(section)
            used += len(section)
            if used >= max_chars:
                break
        return "\n\n".join(sections)[:max_chars]

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_render_chars": self.max_render_chars,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class FlatBlock:
    """One ordered script block, with no dependency or graph semantics."""

    block_id: str
    commands: tuple[str, ...]
    checks: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "commands", _string_tuple(self.commands))
        object.__setattr__(self, "checks", _string_tuple(self.checks))
        object.__setattr__(self, "evidence_refs", _string_tuple(self.evidence_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "commands": list(self.commands),
            "checks": list(self.checks),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class FlatPlan:
    blocks: tuple[FlatBlock, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", tuple(self.blocks))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "blocks": [block.to_dict() for block in self.blocks],
        }

    def digest(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def block(self, block_id: str) -> FlatBlock | None:
        return next(
            (block for block in self.blocks if block.block_id == block_id),
            None,
        )


PatchOp = Literal[
    "replace_block",
    "insert_before",
    "insert_after",
    "append_block",
    "delete_block",
]


@dataclass(frozen=True)
class FlatPatch:
    op: PatchOp
    target_block_id: str | None = None
    block: FlatBlock | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"op": self.op}
        if self.target_block_id is not None:
            data["target_block_id"] = self.target_block_id
        if self.block is not None:
            data["block"] = self.block.to_dict()
        return data


@dataclass(frozen=True)
class ProbeAction:
    purpose: str
    command: str
    type: str = "probe"


@dataclass(frozen=True)
class PatchAction:
    rationale: str
    patch: FlatPatch
    type: str = "propose_patch"


@dataclass(frozen=True)
class AbstainAction:
    classification: str
    reason: str
    evidence_refs: tuple[str, ...]
    type: str = "abstain"


AgentAction = ProbeAction | PatchAction | AbstainAction


@dataclass(frozen=True)
class SetupResult:
    rc: int
    output: str
    failing_command: str | None = None
    lineno: int | None = None


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    block_id: str | None = None
    command: str | None = None
    rc: int = 0
    output: str = ""


@dataclass(frozen=True)
class TestResult:
    passed: bool
    command: str
    rc: int
    output: str


FailureKind = Literal["setup", "check", "test", "terminal_setup", "terminal_check", "terminal_test"]


@dataclass(frozen=True)
class FailurePacket:
    kind: FailureKind
    cycle: int
    command: str
    rc: int
    output: str
    failed_block_id: str | None
    plan: FlatPlan
    evidence_id: str
    known_invalid: tuple[str, ...] = ()
    rejection_errors: tuple[str, ...] = ()

    def to_dict(self, *, output_limit: int = 4_000) -> dict[str, Any]:
        output = self.output
        if len(output) > output_limit:
            head = output_limit * 2 // 3
            tail = output_limit - head
            output = output[:head] + "\n...[truncated]...\n" + output[-tail:]
        return {
            "kind": self.kind,
            "cycle": self.cycle,
            "command": self.command,
            "rc": self.rc,
            "output": output,
            "failed_block_id": self.failed_block_id,
            "evidence_id": self.evidence_id,
            "known_invalid": list(self.known_invalid),
            "rejection_errors": list(self.rejection_errors),
            "current_plan": self.plan.to_dict(),
        }


@dataclass(frozen=True)
class InitialPlanResult:
    plan: FlatPlan
    evidence: EvidenceBundle
    llm_calls: int
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RepairResult:
    action: PatchAction | AbstainAction
    evidence: EvidenceBundle
    llm_calls: int
    usage: dict[str, int] = field(default_factory=dict)


def merge_usage(
    left: dict[str, int] | None,
    right: dict[str, int] | None,
) -> dict[str, int]:
    keys = {"input_tokens", "output_tokens", "total_tokens"}
    return {
        key: int((left or {}).get(key, 0) or 0)
        + int((right or {}).get(key, 0) or 0)
        for key in keys
    }


@dataclass(frozen=True)
class RunResult:
    status: Literal["success", "failed"]
    stop_reason: str
    plan: FlatPlan
    setup_sh: str
    cycles: int
    llm_calls: int
    usage: dict[str, int]
    test_result: TestResult | None = None
    final_failure: FailurePacket | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": "w/o_depgraph_execute_agent_only",
            "status": self.status,
            "stop_reason": self.stop_reason,
            "cycles": self.cycles,
            "llm_calls": self.llm_calls,
            "usage": dict(self.usage),
            "plan_digest": self.plan.digest(),
            "blocks": [block.to_dict() for block in self.plan.blocks],
            "test_result": (
                {
                    "passed": self.test_result.passed,
                    "command": self.test_result.command,
                    "rc": self.test_result.rc,
                    "output": self.test_result.output[-4_000:],
                }
                if self.test_result is not None
                else None
            ),
            "final_failure": (
                self.final_failure.to_dict()
                if self.final_failure is not None
                else None
            ),
        }
