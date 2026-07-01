import json
import os
from typing import Optional

from src.constants import DEFAULT_LLM_MODEL
from src.planning.base_image_planner import BaseImagePlanner
from src.planning.generic_heuristics import GenericHeuristicPlanner
from src.planning.graph_update import GraphUpdateManager
from src.planning.graph_validator import GraphValidator
from src.planning.host_environment import PlanningHostEnvironmentProbe
from src.planning.llm_graph_parser import LLMGraphParser
from src.planning.python_heuristics import PythonHeuristicPlanner
from src.planning.repository_evidence import RepositoryEvidence, RepositoryEvidenceCollector
from src.planning.sandbox_explorer import SandboxPlanningExplorer
from src.planning.schemas import EnvironmentBuildPlan
from src.planning.todo_generator import TodoListGenerator
from src.planning.topo_sorter import TopologicalSorter
from src.evaluation_target import normalize_evaluation_target


class EnvironmentPlanningAgent:
    def __init__(self, client=None, model: str = DEFAULT_LLM_MODEL, evaluation_target: str = "repo2run"):
        self.client = client
        self.model = model
        self.evaluation_target = normalize_evaluation_target(evaluation_target)
        self.evidence_collector = RepositoryEvidenceCollector()
        self.base_image_planner = BaseImagePlanner(client=client, model=model)
        self.host_environment_probe = PlanningHostEnvironmentProbe()
        self.graph_validator = GraphValidator()
        self.graph_update_manager = GraphUpdateManager()
        self.llm_graph_parser = LLMGraphParser()
        self.topo_sorter = TopologicalSorter()
        self.todo_generator = TodoListGenerator()
        self.sandbox_explorer = SandboxPlanningExplorer()
        self.repository_evidence: Optional[RepositoryEvidence] = None
        self.base_image_decision = None
        self.language_handler = None
        self.log_dir: Optional[str] = None
        self.log_counter = 0

    def parse_llm_graph_response(
        self,
        text: str,
        base_repo_summary: Optional[dict] = None,
    ) -> EnvironmentBuildPlan:
        return self.llm_graph_parser.parse_plan_text(
            text,
            base_repo_summary=base_repo_summary,
            plan_source="llm",
        )

    def update_plan_from_execution_feedback(
        self,
        plan: EnvironmentBuildPlan,
        failed_action: str,
        observation: str,
    ) -> EnvironmentBuildPlan:
        updated = self.graph_update_manager.promote_matching_fallback(
            plan,
            failed_action=failed_action,
            observation=observation,
        )
        result = updated or plan
        if self.log_dir:
            self._write_plan_log(self.log_dir, result)
            self._write_execution_feedback_log(
                before_plan=plan,
                after_plan=result,
                failed_action=failed_action,
                observation=observation,
                changed=updated is not None and updated is not plan,
            )
        return result

    def get_token_usage(self):
        return self.base_image_planner.get_token_usage()

    def refine_plan_with_sandbox_exploration(
        self,
        plan: EnvironmentBuildPlan,
        sandbox,
        log_dir: Optional[str] = None,
    ) -> EnvironmentBuildPlan:
        if log_dir:
            self._set_log_dir(log_dir)
        before_snapshot = plan.to_dict()
        refined = self.sandbox_explorer.explore(plan, sandbox, log_dir=log_dir or self.log_dir)
        if self.log_dir:
            self._write_plan_log(self.log_dir, refined)
            self._write_sandbox_refinement_log(
                before_plan=before_snapshot,
                after_plan=refined,
                exploration=getattr(self.sandbox_explorer, "last_exploration", None),
            )
        return refined

    def create_initial_plan(
        self,
        repo_path: str,
        platform: str = "linux",
        language_hint: Optional[str] = None,
        base_image_override: Optional[str] = None,
        log_dir: Optional[str] = None,
    ) -> EnvironmentBuildPlan:
        if log_dir:
            self._set_log_dir(log_dir)
        host_environment = self.host_environment_probe.collect(target_platform=platform)
        evidence = self.evidence_collector.collect(repo_path, log_dir=log_dir)
        self.repository_evidence = evidence

        decision = self.base_image_planner.select(
            evidence,
            platform=platform,
            language_hint=language_hint,
        )
        self.base_image_decision = decision
        self.language_handler = decision.language_handler

        graph_planner = self._graph_planner_for_language(decision.detected_language)
        (
            nodes,
            edges,
            repo_summary,
            risk_notes,
            fallback_plan,
            unresolved_questions,
        ) = graph_planner.build(evidence, decision)
        repo_summary["evaluation_target"] = self.evaluation_target
        self._attach_host_environment(
            nodes,
            repo_summary,
            host_environment=host_environment,
            target_platform=platform,
        )
        if host_environment.get("notes"):
            risk_notes.extend(str(note) for note in host_environment["notes"])

        validator_result = self.graph_validator.validate(nodes, edges, raise_on_error=False)
        ordered_node_ids = []
        ordered_todo_list = []
        if validator_result.ok:
            ordered_node_ids = self.topo_sorter.sort(nodes, edges)
            ordered_todo_list = self.todo_generator.generate(ordered_node_ids, nodes)

        validator_warnings = list(validator_result.warnings)
        validator_warnings.extend(
            f"Planning graph validation error: {error}"
            for error in validator_result.errors
        )
        if base_image_override:
            repo_summary["base_image_override"] = base_image_override
            validator_warnings.append(
                "User-specified base image overrides Planning Agent recommendation: "
                f"{base_image_override}."
            )

        plan = EnvironmentBuildPlan(
            plan_source="heuristic",
            repo_summary=repo_summary,
            nodes=nodes,
            edges=edges,
            ordered_todo_list=ordered_todo_list,
            risk_notes=risk_notes,
            fallback_plan=fallback_plan,
            unresolved_questions=unresolved_questions,
            validator_warnings=validator_warnings,
        )

        if self.log_dir:
            self._write_plan_log(self.log_dir, plan)
            self._write_initial_plan_log(
                plan=plan,
                evidence=evidence,
                decision=decision,
                host_environment=host_environment,
                validator_result=validator_result,
            )
        return plan

    def format_initial_plan(
        self,
        plan: EnvironmentBuildPlan,
        max_todo_items: int = 12,
        max_graph_nodes: int = 16,
        max_graph_edges: int = 24,
    ) -> str:
        summary = plan.repo_summary
        lines = [
            "Initial Environment Plan Summary:",
            f"- Plan source: {plan.plan_source}",
            f"- Primary language: {summary.get('primary_language') or 'unknown'}",
            f"- Package manager: {summary.get('package_manager') or 'unknown'}",
            f"- Test framework: {summary.get('test_framework') or 'unknown'}",
            f"- Recommended base image: {summary.get('recommended_base_image') or 'unknown'}",
        ]
        if summary.get("base_image_override"):
            lines.append(f"- User base image override: {summary['base_image_override']}")
        if summary.get("platform_override"):
            lines.append(f"- Platform override: {summary['platform_override']}")
        if summary.get("detected_runtime"):
            lines.append(f"- Detected runtime: {summary['detected_runtime']}")
        if summary.get("target_platform"):
            lines.append(f"- Target platform: {summary['target_platform']}")
        host_environment = summary.get("planning_host_environment") or {}
        if host_environment:
            host_text = (
                f"{host_environment.get('normalized_os') or host_environment.get('os_name')}"
                f"/{host_environment.get('normalized_arch') or host_environment.get('machine')}"
            )
            lines.append(f"- Planning host environment: {host_text}")
        sandbox_runtime = summary.get("sandbox_runtime_environment") or {}
        if sandbox_runtime:
            lines.append(
                "- Sandbox runtime environment: "
                f"{sandbox_runtime.get('platform') or 'unknown'}"
            )

        dependency_plan = summary.get("dependency_resolution_plan") or {}
        if dependency_plan:
            lines.append("")
            lines.append("Manifest-driven dependency resolution plan:")
            lines.append(f"- Strategy: {dependency_plan.get('strategy')}")
            runtime_specs = dependency_plan.get("runtime_dependency_specs") or []
            test_specs = dependency_plan.get("test_dependency_specs") or []
            excluded = dependency_plan.get("excluded_platform_dependencies") or []
            if runtime_specs:
                lines.append("- Runtime direct deps: " + ", ".join(runtime_specs[:20]))
            if test_specs:
                lines.append("- Test/dev direct deps: " + ", ".join(test_specs[:20]))
            if excluded:
                lines.append("- Excluded platform-specific deps: " + ", ".join(excluded[:12]))
            for step in (dependency_plan.get("steps") or [])[:6]:
                if step.get("command"):
                    lines.append(f"- Step {step.get('name')}: {step.get('command')}")
            for note in (dependency_plan.get("notes") or [])[:3]:
                lines.append(f"- Note: {note}")

        lines.append("")
        lines.append("Typed task graph nodes:")
        for node in plan.nodes[:max_graph_nodes]:
            hint = f" | hint: {node.command_hint}" if node.command_hint else ""
            scope = f" | scope: {node.scope}" if node.scope else ""
            evidence = ", ".join(node.evidence[:3]) if node.evidence else "repository evidence"
            lines.append(
                f"- {node.id} [{node.type}] confidence={node.confidence:.2f}"
                f"{scope} | evidence: {evidence}{hint}"
            )
        if len(plan.nodes) > max_graph_nodes:
            lines.append(f"... {len(plan.nodes) - max_graph_nodes} more graph nodes omitted")

        lines.append("")
        lines.append("Typed task graph edges:")
        for edge in plan.edges[:max_graph_edges]:
            evidence = ", ".join(edge.evidence[:3]) if edge.evidence else "repository evidence"
            lines.append(
                f"- {edge.from_id} -> {edge.to_id} [{edge.type}, {edge.strength}] "
                f"confidence={edge.confidence:.2f} | evidence: {evidence}"
            )
        if len(plan.edges) > max_graph_edges:
            lines.append(f"... {len(plan.edges) - max_graph_edges} more graph edges omitted")

        lines.append("")
        lines.append("Ordered advisory todo-list:")
        for item in plan.ordered_todo_list[:max_todo_items]:
            hint = item.get("command_hint")
            hint_text = f" | hint: {hint}" if hint else ""
            lines.append(
                f"{item.get('step')}. [{item.get('task_type')}] "
                f"{item.get('node_id')}{hint_text}"
            )
        if len(plan.ordered_todo_list) > max_todo_items:
            lines.append(f"... {len(plan.ordered_todo_list) - max_todo_items} more todo items omitted")

        if plan.risk_notes:
            lines.append("")
            lines.append("Risk notes:")
            lines.extend(f"- {note}" for note in plan.risk_notes[:6])

        if plan.fallback_plan:
            lines.append("")
            lines.append("Fallback plan:")
            for fallback in plan.fallback_plan[:6]:
                lines.append(
                    f"- Trigger: {fallback.get('trigger')} | suggested action: "
                    f"{fallback.get('suggested_action')}"
                )

        if plan.unresolved_questions:
            lines.append("")
            lines.append("Unresolved questions:")
            lines.extend(f"- {question}" for question in plan.unresolved_questions[:6])

        if plan.validator_warnings:
            lines.append("")
            lines.append("Validator warnings:")
            lines.extend(f"- {warning}" for warning in plan.validator_warnings[:8])

        lines.append("")
        lines.append(
            "Use this graph as the initial execution map: inspect evidence files first, follow "
            "hard edges before dependent tasks, and treat soft edges/fallbacks as conditional "
            "recovery paths after a matching failure."
        )
        lines.append(
            "All graph nodes and command hints are advisory only. Do not place hinted commands "
            "in the Dockerfile or Verification Bundle unless they are actually executed "
            "successfully in the sandbox."
        )
        return "\n".join(lines)

    def _graph_planner_for_language(self, language: str):
        if language != "python":
            return GenericHeuristicPlanner()
        return PythonHeuristicPlanner(evaluation_target=self.evaluation_target)

    def _attach_host_environment(
        self,
        nodes,
        repo_summary: dict,
        host_environment: dict,
        target_platform: str,
    ):
        repo_summary["target_platform"] = target_platform
        repo_summary["planning_host_environment"] = dict(host_environment)
        for node in nodes:
            if node.type != "runtime":
                continue
            node.metadata["target_platform"] = target_platform
            node.metadata["planning_host_environment"] = dict(host_environment)

    def _write_plan_log(self, log_dir: str, plan: EnvironmentBuildPlan):
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "environment_build_plan.json")
        with open(path, "w", encoding="utf-8") as file_obj:
            json.dump(plan.to_dict(), file_obj, indent=2, ensure_ascii=False)

    def _set_log_dir(self, log_dir: str):
        if not log_dir:
            return
        normalized = os.path.abspath(log_dir)
        if self.log_dir != normalized:
            self.log_dir = normalized
            self.log_counter = 0
        os.makedirs(self.log_dir, exist_ok=True)

    def _write_initial_plan_log(
        self,
        plan: EnvironmentBuildPlan,
        evidence: RepositoryEvidence,
        decision,
        host_environment: dict,
        validator_result,
    ):
        self._write_stage_log(
            "initial plan",
            [
                (
                    "Host Environment",
                    self._json_block(host_environment),
                ),
                (
                    "Repository Evidence Summary",
                    self._json_block(evidence.to_summary_dict()),
                ),
                (
                    "Repository Structure",
                    self._text_block(self._truncate(evidence.repo_structure, 24000)),
                ),
                (
                    "Base Image Decision",
                    self._json_block(
                        {
                            "selected_image": decision.selected_image,
                            "detected_language": decision.detected_language,
                            "detected_runtime": decision.detected_runtime,
                            "platform_override": decision.platform_override,
                            "selection_method": decision.selection_method,
                            "candidate_images": decision.candidate_images,
                            "evidence": decision.evidence,
                        }
                    ),
                ),
                (
                    "Graph Validation",
                    self._json_block(
                        {
                            "ok": validator_result.ok,
                            "warnings": validator_result.warnings,
                            "errors": validator_result.errors,
                        }
                    ),
                ),
                (
                    "Initial EnvironmentBuildPlan",
                    self._json_block(self._plan_digest(plan)),
                ),
            ],
            metadata={"model": self.model, "plan_source": plan.plan_source},
        )

    def _write_sandbox_refinement_log(
        self,
        before_plan: dict,
        after_plan: EnvironmentBuildPlan,
        exploration,
    ):
        sections = [
            (
                "Before Sandbox Refinement",
                self._json_block(self._plan_digest(before_plan)),
            ),
            (
                "Sandbox Probe Findings",
                self._json_block(
                    exploration.to_dict() if exploration else {"skipped": True}
                ),
            ),
            (
                "After Sandbox Refinement",
                self._json_block(self._plan_digest(after_plan)),
            ),
        ]
        self._write_stage_log(
            "sandbox refinement",
            sections,
            metadata={"model": self.model, "plan_source": after_plan.plan_source},
        )

    def _write_execution_feedback_log(
        self,
        before_plan: EnvironmentBuildPlan,
        after_plan: EnvironmentBuildPlan,
        failed_action: str,
        observation: str,
        changed: bool,
    ):
        self._write_stage_log(
            "execution feedback update",
            [
                (
                    "Execution Feedback",
                    self._json_block(
                        {
                            "changed_plan": changed,
                            "failed_action": failed_action,
                            "observation": self._truncate(observation, 16000),
                        }
                    ),
                ),
                (
                    "Before Update",
                    self._json_block(self._plan_digest(before_plan)),
                ),
                (
                    "After Update",
                    self._json_block(self._plan_digest(after_plan)),
                ),
            ],
            metadata={"model": self.model, "plan_source": after_plan.plan_source},
        )

    def _write_stage_log(self, label: str, sections: list, metadata: Optional[dict] = None):
        if not self.log_dir:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        current_index = self.log_counter
        path = os.path.join(self.log_dir, f"{current_index}.md")
        with open(path, "w", encoding="utf-8") as file_obj:
            file_obj.write(f"##### PLANNING AGENT LOG ({label} #{current_index}) #####\n")
            for title, body in sections:
                file_obj.write(f"================================ {title} =================================\n\n")
                file_obj.write(f"{body.rstrip()}\n\n")
            if metadata:
                file_obj.write("================================ Metadata =================================\n\n")
                for key, value in metadata.items():
                    file_obj.write(f"- {key}: {value}\n")
        self.log_counter += 1

    def _plan_digest(self, plan) -> dict:
        if isinstance(plan, EnvironmentBuildPlan):
            payload = plan.to_dict()
        else:
            payload = dict(plan or {})
        graph = payload.get("typed_task_graph", {}) or {}
        summary = payload.get("repo_summary", {}) or {}
        return {
            "plan_source": payload.get("plan_source"),
            "repo_summary": summary,
            "node_count": len(graph.get("nodes", []) or []),
            "edge_count": len(graph.get("edges", []) or []),
            "ordered_todo_list": payload.get("ordered_todo_list", [])[:20],
            "risk_notes": payload.get("risk_notes", [])[:20],
            "fallback_plan": payload.get("fallback_plan", [])[:12],
            "unresolved_questions": payload.get("unresolved_questions", [])[:12],
            "validator_warnings": payload.get("validator_warnings", [])[:20],
        }

    def _json_block(self, payload) -> str:
        return "```json\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n```"

    def _text_block(self, text: str) -> str:
        return "```text\n" + (text or "") + "\n```"

    def _truncate(self, text: str, limit: int) -> str:
        text = text or ""
        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        return text[:limit] + f"\n... ({omitted} characters omitted)"
