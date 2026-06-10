from typing import List, Tuple

from src.planning.repository_evidence import RepositoryEvidence
from src.planning.schemas import TaskEdge, TaskNode


SYSTEM_PACKAGE_HINTS = {
    "psycopg2": ("libpq-dev/build-essential", "apt-get install -y libpq-dev build-essential"),
    "mysqlclient": (
        "default-libmysqlclient-dev/build-essential",
        "apt-get install -y default-libmysqlclient-dev build-essential",
    ),
    "lxml": (
        "libxml2-dev/libxslt1-dev/build-essential",
        "apt-get install -y libxml2-dev libxslt1-dev build-essential",
    ),
    "opencv": ("libgl1/libglib2.0-0", "apt-get install -y libgl1 libglib2.0-0"),
    "cv2": ("libgl1/libglib2.0-0", "apt-get install -y libgl1 libglib2.0-0"),
    "cryptography": ("libssl-dev/libffi-dev", "apt-get install -y libssl-dev libffi-dev"),
    "pillow": ("libjpeg-dev/zlib1g-dev", "apt-get install -y libjpeg-dev zlib1g-dev"),
}


class FallbackGenerator:
    def generate_system_package_fallbacks(
        self,
        evidence: RepositoryEvidence,
        dependency_nodes: List[TaskNode],
    ) -> Tuple[List[TaskNode], List[TaskEdge], List[dict]]:
        nodes = []
        edges = []
        fallbacks = []
        dependency_text = "\n".join(
            text
            for path, text in evidence.files_content.items()
            if self._looks_like_dependency_file(path)
        ).lower()
        dependency_target = dependency_nodes[0].id if dependency_nodes else None
        seen_node_ids = set()

        for marker, (node_id, command_hint) in SYSTEM_PACKAGE_HINTS.items():
            if marker not in dependency_text:
                continue
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)
            marker_evidence = self._dependency_evidence_for_marker(evidence, marker)
            nodes.append(
                TaskNode(
                    id=node_id,
                    type="system_package",
                    label=f"Possible system packages for {marker}",
                    command_hint=command_hint,
                    evidence=marker_evidence,
                    confidence=0.55,
                    scope="test",
                    metadata={"trigger_package": marker},
                )
            )
            if dependency_target:
                edges.append(
                    TaskEdge(
                        from_id=node_id,
                        to_id=dependency_target,
                        type="system_required_by",
                        strength="soft",
                        evidence=marker_evidence,
                        confidence=0.55,
                    )
                )
            fallbacks.append(
                {
                    "trigger": f"Python dependency install fails while building or importing {marker}.",
                    "suggested_action": command_hint,
                    "evidence": marker_evidence,
                    "confidence": 0.55,
                    "source_node_id": node_id,
                    "target_node_id": dependency_target,
                    "edge_type": "system_required_by",
                }
            )

        return nodes, edges, fallbacks

    def _looks_like_dependency_file(self, path: str) -> bool:
        lowered = path.lower()
        return lowered.startswith("requirements") or lowered in {
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
        }

    def _dependency_evidence_for_marker(
        self,
        evidence: RepositoryEvidence,
        marker: str,
    ) -> List[str]:
        matches = [
            path
            for path, text in evidence.files_content.items()
            if self._looks_like_dependency_file(path) and marker in text.lower()
        ]
        return matches or evidence.relevant_files[:1] or ["repository structure"]
