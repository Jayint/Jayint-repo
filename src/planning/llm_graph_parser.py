import json
import re
from typing import Dict, List, Optional

from src.planning.graph_validator import GraphValidator
from src.planning.schemas import EnvironmentBuildPlan, TaskEdge, TaskNode
from src.planning.todo_generator import TodoListGenerator
from src.planning.topo_sorter import TopologicalSorter


class LLMGraphParser:
    def __init__(self):
        self.graph_validator = GraphValidator()
        self.topo_sorter = TopologicalSorter()
        self.todo_generator = TodoListGenerator()

    def parse_plan_text(
        self,
        text: str,
        base_repo_summary: Optional[Dict[str, object]] = None,
        plan_source: str = "llm",
    ) -> EnvironmentBuildPlan:
        payload = self.extract_json_object(text)
        return self.parse_payload(
            payload,
            base_repo_summary=base_repo_summary,
            plan_source=plan_source,
        )

    def parse_payload(
        self,
        payload: Dict[str, object],
        base_repo_summary: Optional[Dict[str, object]] = None,
        plan_source: str = "llm",
    ) -> EnvironmentBuildPlan:
        graph = payload.get("typed_task_graph", {}) or {}
        nodes = [TaskNode.from_dict(item) for item in graph.get("nodes", []) or []]
        edges = [TaskEdge.from_dict(item) for item in graph.get("edges", []) or []]
        repo_summary = dict(base_repo_summary or {})
        repo_summary.update(dict(payload.get("repo_summary", {}) or {}))

        validator_result = self.graph_validator.validate(nodes, edges, raise_on_error=False)
        ordered_todo_list: List[dict] = []
        if validator_result.ok:
            ordered_ids = self.topo_sorter.sort(nodes, edges)
            ordered_todo_list = self.todo_generator.generate(ordered_ids, nodes)

        warnings = [str(item) for item in payload.get("validator_warnings", []) or []]
        warnings.extend(validator_result.warnings)
        warnings.extend(
            f"Planning graph validation error: {error}"
            for error in validator_result.errors
        )

        return EnvironmentBuildPlan(
            plan_source=plan_source,
            repo_summary=repo_summary,
            nodes=nodes,
            edges=edges,
            ordered_todo_list=ordered_todo_list,
            risk_notes=[str(item) for item in payload.get("risk_notes", []) or []],
            fallback_plan=list(payload.get("fallback_plan", []) or []),
            unresolved_questions=[
                str(item) for item in payload.get("unresolved_questions", []) or []
            ],
            validator_warnings=warnings,
        )

    def extract_json_object(self, text: str) -> Dict[str, object]:
        if not text:
            raise ValueError("LLM graph response is empty.")

        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            return json.loads(fenced.group(1))

        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object found in LLM graph response.")

        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : index + 1])

        raise ValueError("Unterminated JSON object in LLM graph response.")
