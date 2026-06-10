import tempfile
import unittest
from pathlib import Path

from src.image_selector import ImageSelector
from src.planner import Planner
from src.planning import EnvironmentBuildPlan, EnvironmentPlanningAgent, TaskEdge, TaskNode
from src.planning.errors import PlanningValidationError
from src.planning.fallback_generator import FallbackGenerator
from src.planning.graph_validator import GraphValidator
from src.planning.llm_graph_parser import LLMGraphParser
from src.planning.repository_evidence import RepositoryEvidenceCollector
from src.planning.topo_sorter import TopologicalSorter


class PlanningSchemaTests(unittest.TestCase):
    def test_task_edge_maps_json_from_to_to_internal_names(self):
        edge = TaskEdge.from_dict(
            {
                "from": "requirements.txt",
                "to": "pytest",
                "type": "test_dependency_before_verify",
                "strength": "hard",
                "evidence": ["tox.ini"],
                "confidence": 0.9,
            }
        )

        self.assertEqual(edge.from_id, "requirements.txt")
        self.assertEqual(edge.to_id, "pytest")
        self.assertEqual(edge.to_dict()["from"], "requirements.txt")
        self.assertEqual(edge.to_dict()["to"], "pytest")

    def test_environment_build_plan_round_trips_nested_graph(self):
        plan = EnvironmentBuildPlan.from_dict(
            {
                "plan_source": "heuristic",
                "repo_summary": {"primary_language": "python"},
                "typed_task_graph": {
                    "nodes": [
                        {
                            "id": "python:3.11",
                            "type": "runtime",
                            "evidence": ["pyproject.toml"],
                            "confidence": 0.9,
                        }
                    ],
                    "edges": [],
                },
                "ordered_todo_list": [],
                "risk_notes": [],
                "fallback_plan": [],
                "unresolved_questions": [],
                "validator_warnings": ["warning"],
            }
        )

        payload = plan.to_dict()
        self.assertEqual(payload["typed_task_graph"]["nodes"][0]["id"], "python:3.11")
        self.assertEqual(payload["validator_warnings"], ["warning"])


class PlanningGraphTests(unittest.TestCase):
    def test_validator_rejects_hard_edge_cycle_but_allows_soft_cycle(self):
        nodes = [
            TaskNode("a", "runtime", ["file"], 0.9),
            TaskNode("b", "package_manager", ["file"], 0.9),
        ]
        hard_cycle = [
            TaskEdge("a", "b", "requires_runtime", "hard", ["file"], 0.9),
            TaskEdge("b", "a", "uses_package_manager", "hard", ["file"], 0.9),
        ]

        with self.assertRaises(PlanningValidationError):
            GraphValidator().validate(nodes, hard_cycle)

        soft_cycle = [
            TaskEdge("a", "b", "requires_runtime", "hard", ["file"], 0.9),
            TaskEdge("b", "a", "system_required_by", "soft", ["file"], 0.5),
        ]
        result = GraphValidator().validate(nodes, soft_cycle)
        self.assertTrue(result.ok)

    def test_topological_sort_uses_type_priority_for_parallel_nodes(self):
        nodes = [
            TaskNode("verify", "verification", ["tests"], 0.9),
            TaskNode("runtime", "runtime", ["pyproject.toml"], 0.9),
            TaskNode("deps", "language_dependency", ["requirements.txt"], 0.9),
        ]

        ordered = TopologicalSorter().sort(nodes, [])

        self.assertEqual(ordered, ["runtime", "deps", "verify"])


class PlanningAgentTests(unittest.TestCase):
    def test_create_initial_plan_for_python_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text(
                '[project]\nrequires-python = ">=3.10"\ndependencies = ["lxml"]\n',
                encoding="utf-8",
            )
            (root / "requirements.txt").write_text("lxml\npytest\n", encoding="utf-8")
            tests_dir = root / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

            agent = EnvironmentPlanningAgent()
            plan = agent.create_initial_plan(str(root), log_dir=str(root / "logs"))

            self.assertEqual(plan.repo_summary["primary_language"], "python")
            self.assertEqual(plan.repo_summary["recommended_base_image"], "python:3.10")
            node_ids = {node.id for node in plan.nodes}
            self.assertIn("python:3.10", node_ids)
            self.assertIn("requirements.txt", node_ids)
            self.assertIn("pytest --collect-only -q --disable-warnings", node_ids)
            self.assertTrue(any(item["command_hint_is_advisory"] for item in plan.ordered_todo_list))
            self.assertTrue(plan.fallback_plan)
            self.assertTrue((root / "logs" / "environment_build_plan.json").exists())
            initial_log = root / "logs" / "0.md"
            self.assertTrue(initial_log.exists())
            initial_log_text = initial_log.read_text(encoding="utf-8")
            self.assertIn("PLANNING AGENT LOG (initial plan #0)", initial_log_text)
            self.assertIn("Host Environment", initial_log_text)
            self.assertIn("Initial EnvironmentBuildPlan", initial_log_text)

    def test_initial_plan_records_planning_host_environment(self):
        class FakeHostProbe:
            def collect(self, target_platform="linux"):
                return {
                    "role": "planning_host",
                    "os_name": "Darwin",
                    "normalized_os": "macos",
                    "machine": "arm64",
                    "normalized_arch": "arm64",
                    "python_version": "3.12.0",
                    "python_implementation": "CPython",
                    "target_platform": target_platform,
                    "docker_default_platform_env": None,
                    "inside_container": False,
                    "host_is_apple_silicon": True,
                    "notes": ["fake host note"],
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text(
                '[project]\nrequires-python = ">=3.11"\n',
                encoding="utf-8",
            )
            planning_agent = EnvironmentPlanningAgent()
            planning_agent.host_environment_probe = FakeHostProbe()

            plan = planning_agent.create_initial_plan(str(root), platform="linux")

            host = plan.repo_summary["planning_host_environment"]
            self.assertEqual(host["normalized_os"], "macos")
            self.assertEqual(host["normalized_arch"], "arm64")
            self.assertEqual(plan.repo_summary["target_platform"], "linux")
            runtime_nodes = [node for node in plan.nodes if node.type == "runtime"]
            self.assertEqual(
                runtime_nodes[0].metadata["planning_host_environment"]["normalized_os"],
                "macos",
            )
            self.assertIn("fake host note", plan.risk_notes)
            context = planning_agent.format_initial_plan(plan)
            self.assertIn("Planning host environment: macos/arm64", context)

    def test_repository_evidence_collector_writes_structure_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
            log_dir = root / "logs"

            evidence = RepositoryEvidenceCollector().collect(str(root), log_dir=str(log_dir))

            self.assertIn("requirements.txt", evidence.relevant_files)
            self.assertIn("requirements.txt", evidence.docs)
            self.assertTrue((log_dir / "structure.txt").exists())

    def test_image_selector_compatibility_wrapper_uses_planning_agent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text(
                '[project]\nrequires-python = ">=3.9"\n',
                encoding="utf-8",
            )
            selector = ImageSelector(client=None)

            image, handler, docs, platform_override = selector.select_base_image(
                str(root),
                log_dir=str(root / "logs"),
            )

            self.assertEqual(image, "python:3.9")
            self.assertEqual(handler.language, "python")
            self.assertIn("pyproject.toml", docs)
            self.assertIsNone(platform_override)
            self.assertTrue((root / "logs" / "environment_build_plan.json").exists())

    def test_non_python_repo_gets_conservative_runtime_only_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text('{"scripts": {"test": "node test.js"}}\n', encoding="utf-8")
            (root / "index.js").write_text("console.log('ok')\n", encoding="utf-8")

            plan = EnvironmentPlanningAgent().create_initial_plan(str(root))

            self.assertEqual(plan.repo_summary["primary_language"], "javascript")
            self.assertNotEqual(plan.repo_summary["package_manager"], "pip")
            self.assertEqual([node.type for node in plan.nodes], ["runtime"])
            self.assertTrue(plan.unresolved_questions)

    def test_root_python_manifest_wins_over_frontend_subtree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text(
                '[tool.poetry]\nname = "mixed"\n'
                '[tool.poetry.dependencies]\npython = ">=3.10,<3.13"\n',
                encoding="utf-8",
            )
            (root / "poetry.lock").write_text("# lock\n" * 50000, encoding="utf-8")
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text('{"scripts": {"test": "vitest"}}\n', encoding="utf-8")
            (frontend / "tsconfig.json").write_text("{}\n", encoding="utf-8")
            (frontend / "index.ts").write_text("export const ok = true\n", encoding="utf-8")

            plan = EnvironmentPlanningAgent().create_initial_plan(str(root))

            self.assertEqual(plan.repo_summary["primary_language"], "python")
            self.assertEqual(plan.repo_summary["package_manager"], "poetry")
            self.assertEqual(plan.repo_summary["recommended_base_image"], "python:3.10")

    def test_formatted_plan_exposes_graph_and_ordered_todo_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text(
                '[project]\nrequires-python = ">=3.11"\ndependencies = ["pytest"]\n',
                encoding="utf-8",
            )

            planning_agent = EnvironmentPlanningAgent()
            plan = planning_agent.create_initial_plan(str(root))
            context = planning_agent.format_initial_plan(plan)

            self.assertIn("Typed task graph nodes:", context)
            self.assertIn("Typed task graph edges:", context)
            self.assertIn("Ordered advisory todo-list:", context)
            self.assertIn("python:3.11", context)

    def test_sandbox_exploration_refines_plan_with_probe_findings(self):
        class FakeSandbox:
            def __init__(self):
                self.commands = []

            def inspect(self, command):
                self.commands.append(command)
                if "repo2run-planning-probe: runtime" in command:
                    return True, "\n".join(
                        [
                            "RUNTIME_PYTHON 3.10.20",
                            "TOOL_AVAILABLE poetry False",
                            "PYTHON_MODULE_AVAILABLE pkg_resources False",
                        ]
                    )
                if "repo2run-planning-probe: pyproject" in command:
                    return True, "\n".join(
                        [
                            "PYPROJECT_DEP main pyobjc ^10.3.1",
                            "PYPROJECT_PLATFORM_DEP main pyobjc ^10.3.1",
                            "PYTEST_CONFIG testpaths [\"tests\"]",
                        ]
                    )
                if "repo2run-planning-probe: poetry_lock" in command:
                    return True, "\n".join(
                        [
                            "LOCK_PACKAGE pytest 8.3.2",
                            "LOCK_PLATFORM_PACKAGE pyobjc-core markers = sys_platform == \"darwin\"",
                        ]
                    )
                if "repo2run-planning-probe: dependency_strategy" in command:
                    return True, "\n".join(
                        [
                            "INSTALL_STRATEGY avoid_full_poetry_install poetry_platform_specific_dependencies",
                            "INSTALL_STRATEGY_STEP install_build_basics python -m pip install --upgrade pip setuptools wheel",
                            "INSTALL_STRATEGY_STEP editable_no_deps pip install -e . --no-deps",
                            "INSTALL_STRATEGY_AVOID_COMMAND poetry install",
                            "INSTALL_STRATEGY_AVOID_PACKAGE EventKit",
                        ]
                    )
                if "repo2run-planning-probe: focused_import_scan" in command:
                    return True, "FOCUSED_UNDECLARED_IMPORT transitions 2 tests/test_demo.py"
                if "repo2run-planning-probe: import_scan" in command:
                    return True, "\n".join(
                        [
                            "UNDECLARED_IMPORT EventKit 5 src/dspygen/utils/reminder_tools.py",
                            "UNDECLARED_IMPORT transitions 2 src/dspygen/rdddy/base_message.py",
                        ]
                    )
                return True, ""

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text(
                '[tool.poetry]\nname = "demo"\n'
                '[tool.poetry.dependencies]\npython = ">=3.10,<3.13"\n',
                encoding="utf-8",
            )
            (root / "poetry.lock").write_text("# lock\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_demo.py").write_text("def test_ok(): pass\n", encoding="utf-8")

            planning_agent = EnvironmentPlanningAgent()
            plan = planning_agent.create_initial_plan(str(root))
            refined = planning_agent.refine_plan_with_sandbox_exploration(
                plan,
                FakeSandbox(),
                log_dir=str(root / "logs"),
            )

            self.assertIn("sandbox_exploration", refined.plan_source)
            self.assertIn("sandbox_planning_exploration", refined.repo_summary)
            self.assertEqual(
                refined.repo_summary["sandbox_runtime_environment"]["python"],
                "3.10.20",
            )
            self.assertTrue(any("transitions" in note for note in refined.risk_notes))
            self.assertTrue(any("pyobjc" in note.lower() for note in refined.risk_notes))
            self.assertGreaterEqual(refined.repo_summary["sandbox_planning_exploration"]["findings"]["probe_rounds"], 2)
            self.assertIn(
                "sandbox probe: undeclared python imports",
                {node.id for node in refined.nodes},
            )
            node_map = {node.id: node for node in refined.nodes}
            self.assertIn("install strategy: targeted linux deps before poetry", node_map)
            self.assertEqual(node_map["poetry install"].command_hint, "pip install -e . --no-deps")
            dependency_plan = refined.repo_summary["dependency_resolution_plan"]
            self.assertEqual(
                dependency_plan["strategy"],
                "manifest_driven_linux_direct_deps_before_pytest_feedback",
            )
            self.assertIn("pytest==8.3.2", dependency_plan["test_dependency_specs"])
            self.assertIn("pyobjc", dependency_plan["excluded_platform_dependencies"])
            self.assertNotIn("EventKit", node_map["sandbox probe: undeclared python imports"].command_hint or "")
            todo_ids = [item["node_id"] for item in refined.ordered_todo_list]
            self.assertLess(
                todo_ids.index("install strategy: targeted linux deps before poetry"),
                todo_ids.index("pyproject.toml"),
            )
            self.assertLess(
                todo_ids.index("install strategy: targeted linux deps before poetry"),
                todo_ids.index("poetry install"),
            )
            context = planning_agent.format_initial_plan(refined)
            self.assertIn("targeted linux deps before poetry", context)
            self.assertIn("Manifest-driven dependency resolution plan", context)
            self.assertIn("pytest==8.3.2", context)
            self.assertIn("sandbox probe: undeclared python imports", context)
            self.assertTrue((root / "logs" / "sandbox_planning_exploration.json").exists())
            refinement_log = root / "logs" / "0.md"
            self.assertTrue(refinement_log.exists())
            refinement_log_text = refinement_log.read_text(encoding="utf-8")
            self.assertIn("PLANNING AGENT LOG (sandbox refinement #0)", refinement_log_text)
            self.assertIn("Sandbox Probe Findings", refinement_log_text)

    def test_planner_prompt_includes_initial_environment_plan_context(self):
        planner = Planner(
            client=None,
            environment_plan_context="Initial Environment Plan:\n- Recommended base image: python:3.11",
        )

        self.assertIn("PLANNING AGENT CONTEXT", planner.system_prompt)
        self.assertIn("typed task graph", planner.system_prompt)
        self.assertIn("ordered todo-list", planner.system_prompt)
        self.assertIn("Initial Environment Plan", planner.system_prompt)
        self.assertIn("python:3.11", planner.system_prompt)
        self.assertIn("__VIEW_PLAN__", planner.system_prompt)
        self.assertIn("/tmp/repo2run_environment_plan.md", planner.system_prompt)

    def test_fallback_generator_emits_source_and_target_for_soft_edge_promotion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "requirements.txt").write_text("lxml\n", encoding="utf-8")
            evidence = RepositoryEvidenceCollector().collect(str(root))
            dependency = TaskNode("requirements.txt", "language_dependency", ["requirements.txt"], 0.9)

            nodes, edges, fallbacks = FallbackGenerator().generate_system_package_fallbacks(
                evidence,
                [dependency],
            )

            self.assertEqual(nodes[0].id, "libxml2-dev/libxslt1-dev/build-essential")
            self.assertEqual(edges[0].strength, "soft")
            self.assertEqual(fallbacks[0]["source_node_id"], nodes[0].id)
            self.assertEqual(fallbacks[0]["target_node_id"], "requirements.txt")

    def test_llm_graph_parser_extracts_json_and_generates_todo_order(self):
        text = """
        Here is the graph:
        ```json
        {
          "repo_summary": {"primary_language": "python"},
          "typed_task_graph": {
            "nodes": [
              {"id": "python:3.11", "type": "runtime", "evidence": ["pyproject.toml"], "confidence": 0.9},
              {"id": "pip", "type": "package_manager", "evidence": ["requirements.txt"], "confidence": 0.9}
            ],
            "edges": [
              {"from": "python:3.11", "to": "pip", "type": "requires_runtime", "strength": "hard", "evidence": ["requirements.txt"], "confidence": 0.9}
            ]
          },
          "risk_notes": [],
          "fallback_plan": [],
          "unresolved_questions": []
        }
        ```
        """

        plan = LLMGraphParser().parse_plan_text(text)

        self.assertEqual(plan.plan_source, "llm")
        self.assertEqual([item["node_id"] for item in plan.ordered_todo_list], ["python:3.11", "pip"])

    def test_execution_feedback_promotes_matching_fallback_soft_edge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "requirements.txt").write_text("lxml\npytest\n", encoding="utf-8")
            planning_agent = EnvironmentPlanningAgent()
            log_dir = root / "logs"
            plan = planning_agent.create_initial_plan(str(root), log_dir=str(log_dir))

            updated = planning_agent.update_plan_from_execution_feedback(
                plan,
                failed_action="pip install -r requirements.txt",
                observation="fatal error: libxml/xmlversion.h: No such file or directory while building lxml",
            )

            promoted = [
                edge
                for edge in updated.edges
                if edge.type == "system_required_by" and edge.strength == "hard"
            ]
            self.assertTrue(promoted)
            self.assertIn("Promoted fallback edge", "\n".join(updated.validator_warnings))
            feedback_log = log_dir / "1.md"
            self.assertTrue(feedback_log.exists())
            feedback_log_text = feedback_log.read_text(encoding="utf-8")
            self.assertIn("PLANNING AGENT LOG (execution feedback update #1)", feedback_log_text)
            self.assertIn("Execution Feedback", feedback_log_text)
            self.assertIn("changed_plan", feedback_log_text)

    def test_execution_feedback_does_not_promote_after_preflight_rejection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "requirements.txt").write_text("lxml\npytest\n", encoding="utf-8")
            planning_agent = EnvironmentPlanningAgent()
            plan = planning_agent.create_initial_plan(str(root))

            updated = planning_agent.update_plan_from_execution_feedback(
                plan,
                failed_action="pip install -r requirements.txt 2>&1 | tail -20",
                observation=(
                    "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup or test commands "
                    "must not pipe output through `head`, `tail`, or `grep`."
                ),
            )

            self.assertIs(updated, plan)
            self.assertFalse(
                any(edge.type == "system_required_by" and edge.strength == "hard" for edge in updated.edges)
            )

    def test_execution_feedback_does_not_promote_poetry_strategy_for_readonly_grep(self):
        plan = EnvironmentBuildPlan(
            plan_source="test",
            repo_summary={"primary_language": "python", "package_manager": "poetry"},
            nodes=[
                TaskNode("install strategy: targeted linux deps before poetry", "install_strategy", ["probe"], 0.8),
                TaskNode("pytest --collect-only -q --disable-warnings", "verification", ["tests"], 0.9),
            ],
            edges=[],
            ordered_todo_list=[],
            risk_notes=[],
            fallback_plan=[
                {
                    "trigger": "Poetry full install or PyObjC/macOS dependency resolution fails on Linux.",
                    "suggested_action": "Use targeted dependencies before poetry install.",
                    "evidence": ["sandbox dependency strategy probe"],
                    "source_node_id": "install strategy: targeted linux deps before poetry",
                    "target_node_id": "pytest --collect-only -q --disable-warnings",
                    "edge_type": "strategy_before_build",
                    "confidence": 0.82,
                }
            ],
            unresolved_questions=[],
        )

        updated = EnvironmentPlanningAgent().update_plan_from_execution_feedback(
            plan,
            failed_action='grep -i "dslmodel" /app/poetry.lock | head -10',
            observation="dslmodel not found in poetry.lock",
        )

        self.assertIs(updated, plan)
        self.assertEqual(updated.validator_warnings, [])


if __name__ == "__main__":
    unittest.main()
