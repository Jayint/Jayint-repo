from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, List

from src.planning.errors import PlanningValidationError
from src.planning.schemas import (
    VALID_EDGE_STRENGTHS,
    VALID_EDGE_TYPES,
    VALID_NODE_TYPES,
    TaskEdge,
    TaskNode,
)


@dataclass
class GraphValidationResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class GraphValidator:
    def validate(
        self,
        nodes: Iterable[TaskNode],
        edges: Iterable[TaskEdge],
        raise_on_error: bool = True,
    ) -> GraphValidationResult:
        node_list = list(nodes)
        edge_list = list(edges)
        result = GraphValidationResult()

        node_ids = []
        node_map = {}
        for index, node in enumerate(node_list):
            if not node.id:
                result.errors.append(f"Node at index {index} is missing id.")
            if not node.type:
                result.errors.append(f"Node {node.id or index} is missing type.")
            if node.type and node.type not in VALID_NODE_TYPES:
                result.warnings.append(f"Unknown node type: {node.type} ({node.id}).")
            if not node.evidence:
                result.warnings.append(f"Node has empty evidence: {node.id}.")
            if not 0 <= float(node.confidence) <= 1:
                result.warnings.append(f"Node confidence outside [0, 1]: {node.id}.")
            node_ids.append(node.id)
            if node.id:
                node_map[node.id] = node

        if len(node_map) != len([node_id for node_id in node_ids if node_id]):
            seen = set()
            for node_id in node_ids:
                if not node_id:
                    continue
                if node_id in seen:
                    result.errors.append(f"Duplicate node id: {node_id}.")
                    break
                seen.add(node_id)

        for index, edge in enumerate(edge_list):
            label = f"edge at index {index}"
            if not edge.from_id:
                result.errors.append(f"{label} is missing from.")
            if not edge.to_id:
                result.errors.append(f"{label} is missing to.")
            if not edge.type:
                result.errors.append(f"{label} is missing type.")
            if edge.type and edge.type not in VALID_EDGE_TYPES:
                result.warnings.append(f"Unknown edge type: {edge.type}.")
            if edge.strength not in VALID_EDGE_STRENGTHS:
                result.errors.append(
                    f"Invalid edge strength: {edge.strength} ({edge.from_id} -> {edge.to_id})."
                )
            if edge.from_id and edge.from_id not in node_map:
                result.errors.append(f"Edge source does not exist: {edge.from_id}.")
            if edge.to_id and edge.to_id not in node_map:
                result.errors.append(f"Edge target does not exist: {edge.to_id}.")
            if not edge.evidence:
                result.warnings.append(
                    f"Edge has empty evidence: {edge.from_id} -> {edge.to_id}."
                )
            if not 0 <= float(edge.confidence) <= 1:
                result.warnings.append(
                    f"Edge confidence outside [0, 1]: {edge.from_id} -> {edge.to_id}."
                )

        if not result.errors:
            cycle = self._find_hard_edge_cycle(node_list, edge_list)
            if cycle:
                result.errors.append("Hard-edge graph has a cycle: " + " -> ".join(cycle))

        if result.errors and raise_on_error:
            raise PlanningValidationError(result.errors)

        return result

    def _find_hard_edge_cycle(self, nodes: List[TaskNode], edges: List[TaskEdge]) -> List[str]:
        graph = defaultdict(list)
        indegree = {node.id: 0 for node in nodes}

        for edge in edges:
            if edge.strength != "hard":
                continue
            graph[edge.from_id].append(edge.to_id)
            indegree[edge.to_id] += 1

        ready = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        visited = []
        while ready:
            current = ready.popleft()
            visited.append(current)
            for next_id in graph[current]:
                indegree[next_id] -= 1
                if indegree[next_id] == 0:
                    ready.append(next_id)

        if len(visited) == len(indegree):
            return []

        return [node_id for node_id, degree in indegree.items() if degree > 0]
