from collections import defaultdict
from typing import Iterable, List

from src.planning.errors import PlanningValidationError
from src.planning.schemas import TaskEdge, TaskNode


TYPE_PRIORITY = {
    "runtime": 0,
    "system_package": 1,
    "package_manager": 2,
    "install_strategy": 3,
    "language_dependency": 4,
    "project_build": 5,
    "service": 6,
    "env_var": 7,
    "asset": 8,
    "runtime_command": 9,
    "verification": 10,
}


class TopologicalSorter:
    def sort(self, nodes: Iterable[TaskNode], edges: Iterable[TaskEdge]) -> List[str]:
        node_list = list(nodes)
        edge_list = list(edges)
        node_map = {node.id: node for node in node_list}
        if len(node_map) != len(node_list):
            raise PlanningValidationError(["Duplicate node id found."])

        graph = defaultdict(list)
        indegree = {node.id: 0 for node in node_list}

        for edge in edge_list:
            if edge.strength != "hard":
                continue
            if edge.from_id not in node_map:
                raise PlanningValidationError([f"Edge source does not exist: {edge.from_id}."])
            if edge.to_id not in node_map:
                raise PlanningValidationError([f"Edge target does not exist: {edge.to_id}."])
            graph[edge.from_id].append(edge.to_id)
            indegree[edge.to_id] += 1

        ready = [node_id for node_id, degree in indegree.items() if degree == 0]
        result = []

        while ready:
            ready.sort(
                key=lambda node_id: (
                    TYPE_PRIORITY.get(node_map[node_id].type, 99),
                    node_id,
                )
            )
            current = ready.pop(0)
            result.append(current)
            for next_id in sorted(graph[current]):
                indegree[next_id] -= 1
                if indegree[next_id] == 0:
                    ready.append(next_id)

        if len(result) != len(node_list):
            raise PlanningValidationError(["Hard-edge graph has a cycle."])

        return result
