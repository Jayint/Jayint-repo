from typing import Dict, List, Tuple

from src.planning.base_image_planner import BaseImageDecision
from src.planning.repository_evidence import RepositoryEvidence
from src.planning.schemas import TaskNode


class GenericHeuristicPlanner:
    def build(
        self,
        evidence: RepositoryEvidence,
        base_image_decision: BaseImageDecision,
    ) -> Tuple[List[TaskNode], list, Dict[str, object], List[str], List[dict], List[str]]:
        runtime_node = TaskNode(
            id=base_image_decision.selected_image,
            type="runtime",
            label=f"{base_image_decision.detected_language} runtime",
            command_hint=f"FROM {base_image_decision.selected_image}",
            evidence=base_image_decision.evidence or evidence.relevant_files[:1],
            confidence=0.75,
            scope="test",
            metadata=base_image_decision.to_runtime_metadata(),
        )
        repo_summary = {
            **base_image_decision.to_repo_summary_fields(),
            "package_manager": None,
            "dependency_files": [],
            "test_framework": None,
            "entrypoints": [],
        }
        unresolved_questions = [
            "Typed task graph heuristics are implemented for Python first; this non-Python repository needs language-specific planning support."
        ]
        return [runtime_node], [], repo_summary, [], [], unresolved_questions
