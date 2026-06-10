from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


NodeType = Literal[
    "runtime",
    "system_package",
    "package_manager",
    "install_strategy",
    "language_dependency",
    "project_build",
    "service",
    "env_var",
    "asset",
    "runtime_command",
    "verification",
]

VALID_NODE_TYPES = {
    "runtime",
    "system_package",
    "package_manager",
    "install_strategy",
    "language_dependency",
    "project_build",
    "service",
    "env_var",
    "asset",
    "runtime_command",
    "verification",
}

EdgeStrength = Literal["hard", "soft"]
VALID_EDGE_STRENGTHS = {"hard", "soft"}

VALID_EDGE_TYPES = {
    "requires_runtime",
    "uses_package_manager",
    "strategy_after_package_manager",
    "strategy_before_dependency",
    "strategy_before_build",
    "dependency_before_build",
    "dependency_before_test_dependency",
    "test_dependency_before_verify",
    "build_before_verify",
    "light_verify_before_full_verify",
    "service_provides_endpoint",
    "service_required_by",
    "env_required_by",
    "runtime_preparation_before_verify",
    "system_required_by",
    "verify_after_runtime",
}


@dataclass
class TaskNode:
    id: str
    type: str
    evidence: List[str]
    confidence: float
    label: Optional[str] = None
    command_hint: Optional[str] = None
    scope: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TaskNode":
        return cls(
            id=str(payload.get("id", "")),
            type=str(payload.get("type", "")),
            label=payload.get("label"),
            command_hint=payload.get("command_hint"),
            evidence=[str(item) for item in payload.get("evidence", []) or []],
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            scope=payload.get("scope"),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "command_hint": self.command_hint,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "scope": self.scope,
            "metadata": dict(self.metadata),
        }


@dataclass
class TaskEdge:
    from_id: str
    to_id: str
    type: str
    strength: str
    evidence: List[str]
    confidence: float

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TaskEdge":
        return cls(
            from_id=str(payload.get("from_id", payload.get("from", ""))),
            to_id=str(payload.get("to_id", payload.get("to", ""))),
            type=str(payload.get("type", "")),
            strength=str(payload.get("strength", "")),
            evidence=[str(item) for item in payload.get("evidence", []) or []],
            confidence=float(payload.get("confidence", 0.0) or 0.0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.type,
            "strength": self.strength,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
        }


@dataclass
class EnvironmentBuildPlan:
    repo_summary: Dict[str, Any]
    nodes: List[TaskNode]
    edges: List[TaskEdge]
    ordered_todo_list: List[Dict[str, Any]]
    risk_notes: List[str]
    fallback_plan: List[Dict[str, Any]]
    unresolved_questions: List[str]
    validator_warnings: List[str] = field(default_factory=list)
    plan_source: str = "heuristic"

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "EnvironmentBuildPlan":
        graph = payload.get("typed_task_graph", {}) or {}
        return cls(
            plan_source=str(payload.get("plan_source", "heuristic")),
            repo_summary=dict(payload.get("repo_summary", {}) or {}),
            nodes=[TaskNode.from_dict(item) for item in graph.get("nodes", []) or []],
            edges=[TaskEdge.from_dict(item) for item in graph.get("edges", []) or []],
            ordered_todo_list=list(payload.get("ordered_todo_list", []) or []),
            risk_notes=[str(item) for item in payload.get("risk_notes", []) or []],
            fallback_plan=list(payload.get("fallback_plan", []) or []),
            unresolved_questions=[
                str(item) for item in payload.get("unresolved_questions", []) or []
            ],
            validator_warnings=[
                str(item) for item in payload.get("validator_warnings", []) or []
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_source": self.plan_source,
            "repo_summary": dict(self.repo_summary),
            "typed_task_graph": {
                "nodes": [node.to_dict() for node in self.nodes],
                "edges": [edge.to_dict() for edge in self.edges],
            },
            "ordered_todo_list": list(self.ordered_todo_list),
            "risk_notes": list(self.risk_notes),
            "fallback_plan": list(self.fallback_plan),
            "unresolved_questions": list(self.unresolved_questions),
            "validator_warnings": list(self.validator_warnings),
        }
