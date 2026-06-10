import re
from dataclasses import replace
from typing import Optional

from src.planning.graph_validator import GraphValidator
from src.planning.schemas import EnvironmentBuildPlan, TaskEdge
from src.planning.todo_generator import TodoListGenerator
from src.planning.topo_sorter import TopologicalSorter


class GraphUpdateManager:
    MAX_VALIDATOR_WARNINGS = 40

    def __init__(self):
        self.graph_validator = GraphValidator()
        self.topo_sorter = TopologicalSorter()
        self.todo_generator = TodoListGenerator()

    def promote_fallback_edge(
        self,
        plan: EnvironmentBuildPlan,
        fallback_index: int,
        reason: str,
    ) -> EnvironmentBuildPlan:
        if fallback_index < 0 or fallback_index >= len(plan.fallback_plan):
            raise IndexError(f"fallback_index out of range: {fallback_index}")

        fallback = plan.fallback_plan[fallback_index]
        source = fallback.get("source_node_id")
        target = fallback.get("target_node_id")
        edge_type = fallback.get("edge_type", "system_required_by")
        if not source or not target:
            raise ValueError("Fallback item does not identify source_node_id and target_node_id.")

        updated_edges = []
        changed = False
        found_existing_edge = False
        for edge in plan.edges:
            if edge.from_id == source and edge.to_id == target and edge.type == edge_type:
                found_existing_edge = True
                if edge.strength == "hard":
                    updated_edges.append(edge)
                else:
                    updated_edges.append(
                        TaskEdge(
                            from_id=edge.from_id,
                            to_id=edge.to_id,
                            type=edge.type,
                            strength="hard",
                            evidence=list(edge.evidence)
                            or [str(item) for item in fallback.get("evidence", []) or []],
                            confidence=max(
                                edge.confidence,
                                fallback.get("confidence", 0.0) or 0.0,
                            ),
                        )
                    )
                    changed = True
            else:
                updated_edges.append(edge)

        if not found_existing_edge:
            updated_edges.append(
                TaskEdge(
                    from_id=str(source),
                    to_id=str(target),
                    type=str(edge_type),
                    strength="hard",
                    evidence=[
                        str(item)
                        for item in (
                            fallback.get("evidence")
                            or fallback.get("source_evidence")
                            or ["execution feedback"]
                        )
                    ],
                    confidence=float(fallback.get("confidence", 0.7) or 0.7),
                )
            )
            changed = True

        if not changed:
            return plan

        updated_plan = replace(
            plan,
            edges=updated_edges,
            validator_warnings=self._dedupe_and_cap_warnings([
                *plan.validator_warnings,
                f"Promoted fallback edge after execution feedback: {reason}",
            ]),
        )
        return self._recompute_order(updated_plan)

    def promote_matching_fallback(
        self,
        plan: EnvironmentBuildPlan,
        failed_action: str,
        observation: str,
    ) -> Optional[EnvironmentBuildPlan]:
        haystack = f"{failed_action}\n{observation}".lower()
        for index, fallback in enumerate(plan.fallback_plan):
            trigger = str(fallback.get("trigger", "")).lower()
            suggested = str(fallback.get("suggested_action", "")).lower()
            source = str(fallback.get("source_node_id", "")).lower()
            if self._fallback_matches(haystack, trigger, suggested, source, fallback):
                return self.promote_fallback_edge(
                    plan,
                    index,
                    reason=f"failed action `{failed_action}` matched fallback trigger `{fallback.get('trigger')}`",
                )
        return None

    def _fallback_matches(
        self,
        haystack: str,
        trigger: str,
        suggested: str,
        source: str,
        fallback: dict,
    ) -> bool:
        if not haystack:
            return False

        if "command rejected before execution" in haystack:
            return False

        if "modulenotfounderror" in trigger or "missing module" in trigger:
            return self._matches_missing_python_module(haystack)

        if "poetry full install" in trigger or "pyobjc" in trigger or "macos" in trigger:
            return self._matches_poetry_platform_failure(haystack)

        if "building or importing" in trigger:
            return self._matches_system_dependency_failure(haystack, trigger, source, suggested)

        return self._matches_specific_fallback_terms(haystack, trigger, source, fallback)

    def _matches_missing_python_module(self, haystack: str) -> bool:
        return bool(
            re.search(r"modulenotfounderror:\s+no module named", haystack)
            or re.search(r"importerror:\s+.*no module named", haystack)
            or re.search(r"cannot import name\s+", haystack)
        )

    def _matches_poetry_platform_failure(self, haystack: str) -> bool:
        if "poetry install" not in haystack:
            return False
        platform_terms = (
            "pyobjc",
            "eventkit",
            "foundation",
            "objc",
            "appscript",
            "pywin32",
            "darwin",
            "macos",
            "win32",
        )
        failure_terms = (
            "error",
            "failed",
            "no matching distribution",
            "not supported",
            "requires macos",
            "platform",
            "metadata-generation-failed",
        )
        return any(term in haystack for term in platform_terms) and any(
            term in haystack for term in failure_terms
        )

    def _matches_system_dependency_failure(
        self,
        haystack: str,
        trigger: str,
        source: str,
        suggested: str,
    ) -> bool:
        marker_match = re.search(r"building or importing\s+([a-z0-9_.-]+)", trigger)
        marker = marker_match.group(1).rstrip(".:,;") if marker_match else ""
        if marker and marker not in haystack:
            return False

        native_failure_terms = (
            "fatal error:",
            "no such file or directory",
            "failed building wheel",
            "error: command",
            "pkg-config",
            "cannot open shared object file",
            "header",
        )
        return any(term in haystack for term in native_failure_terms)

    def _matches_specific_fallback_terms(
        self,
        haystack: str,
        trigger: str,
        source: str,
        fallback: dict,
    ) -> bool:
        terms = [
            token
            for token in re.split(r"[^a-z0-9_.+-]+", f"{trigger} {source}")
            if len(token) >= 6
            and token
            not in {
                "python",
                "dependency",
                "install",
                "fails",
                "failure",
                "pytest",
                "collection",
                "sandbox",
                "trigger",
            }
        ]
        if not terms:
            return False
        evidence = " ".join(str(item).lower() for item in fallback.get("evidence", []) or [])
        return any(term in haystack for term in terms) and (
            not evidence or any(term in evidence for term in terms)
        )

    def _recompute_order(self, plan: EnvironmentBuildPlan) -> EnvironmentBuildPlan:
        validator_result = self.graph_validator.validate(
            plan.nodes,
            plan.edges,
            raise_on_error=False,
        )
        warnings = list(plan.validator_warnings)
        warnings.extend(validator_result.warnings)
        warnings.extend(
            f"Planning graph validation error after update: {error}"
            for error in validator_result.errors
        )
        ordered_todo_list = []
        if validator_result.ok:
            ordered_ids = self.topo_sorter.sort(plan.nodes, plan.edges)
            ordered_todo_list = self.todo_generator.generate(ordered_ids, plan.nodes)
        return replace(
            plan,
            ordered_todo_list=ordered_todo_list,
            validator_warnings=self._dedupe_and_cap_warnings(warnings),
        )

    def _dedupe_and_cap_warnings(self, warnings):
        result = []
        seen = set()
        for warning in warnings:
            text = str(warning)
            if text in seen:
                continue
            seen.add(text)
            result.append(text)
        if len(result) <= self.MAX_VALIDATOR_WARNINGS:
            return result
        omitted = len(result) - self.MAX_VALIDATOR_WARNINGS
        return result[: self.MAX_VALIDATOR_WARNINGS] + [
            f"... {omitted} additional planning warnings omitted"
        ]
