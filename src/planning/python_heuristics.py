import re
from typing import Dict, List, Tuple

from src.planning.base_image_planner import BaseImageDecision
from src.planning.fallback_generator import FallbackGenerator
from src.planning.repository_evidence import RepositoryEvidence
from src.planning.schemas import TaskEdge, TaskNode
from src.evaluation_target import is_ratbench_target, normalize_evaluation_target


class PythonHeuristicPlanner:
    def __init__(self, fallback_generator: FallbackGenerator = None, evaluation_target: str = "repo2run"):
        self.fallback_generator = fallback_generator or FallbackGenerator()
        self.evaluation_target = normalize_evaluation_target(evaluation_target)

    def build(
        self,
        evidence: RepositoryEvidence,
        base_image_decision: BaseImageDecision,
    ) -> Tuple[List[TaskNode], List[TaskEdge], Dict[str, object], List[str], List[dict], List[str]]:
        nodes: List[TaskNode] = []
        edges: List[TaskEdge] = []
        risk_notes: List[str] = []
        fallback_plan: List[dict] = []
        unresolved_questions: List[str] = []

        runtime_node = self._runtime_node(base_image_decision)
        nodes.append(runtime_node)

        package_manager_id = self._package_manager_id(evidence)
        package_manager_node = TaskNode(
            id=package_manager_id,
            type="package_manager",
            label=f"{package_manager_id} package manager",
            command_hint=self._package_manager_command_hint(package_manager_id),
            evidence=self._package_manager_evidence(evidence),
            confidence=0.9,
            scope="test",
            metadata={},
        )
        nodes.append(package_manager_node)
        edges.append(
            self._edge(
                runtime_node.id,
                package_manager_node.id,
                "requires_runtime",
                "hard",
                package_manager_node.evidence,
                0.9,
            )
        )

        dependency_nodes = self._dependency_nodes(evidence, package_manager_id)
        nodes.extend(dependency_nodes)
        previous_dependency = None
        for node in dependency_nodes:
            edge_type = (
                "dependency_before_test_dependency"
                if previous_dependency
                else "uses_package_manager"
            )
            edges.append(
                self._edge(
                    previous_dependency.id if previous_dependency else package_manager_node.id,
                    node.id,
                    edge_type,
                    "hard",
                    node.evidence,
                    node.confidence,
                )
            )
            previous_dependency = node

        build_node = self._project_build_node(evidence, package_manager_id)
        nodes.append(build_node)
        if previous_dependency:
            edges.append(
                self._edge(
                    previous_dependency.id,
                    build_node.id,
                    "dependency_before_build",
                    "hard",
                    build_node.evidence,
                    build_node.confidence,
                )
            )
        else:
            edges.append(
                self._edge(
                    package_manager_node.id,
                    build_node.id,
                    "uses_package_manager",
                    "hard",
                    build_node.evidence,
                    build_node.confidence,
                )
            )

        verification_nodes = self._verification_nodes(evidence)
        nodes.extend(verification_nodes)
        previous_verify = None
        for verify_node in verification_nodes:
            from_id = previous_verify.id if previous_verify else build_node.id
            edge_type = "light_verify_before_full_verify" if previous_verify else "build_before_verify"
            edges.append(
                self._edge(
                    from_id,
                    verify_node.id,
                    edge_type,
                    "hard",
                    verify_node.evidence,
                    verify_node.confidence,
                )
            )
            previous_verify = verify_node

        system_nodes, system_edges, system_fallbacks = self.fallback_generator.generate_system_package_fallbacks(
            evidence,
            dependency_nodes,
        )
        nodes.extend(system_nodes)
        edges.extend(system_edges)
        fallback_plan.extend(system_fallbacks)
        if system_nodes:
            risk_notes.append(
                "Native Python dependencies may require system packages; keep these as fallback until an install failure confirms them."
            )

        repo_summary = {
            **base_image_decision.to_repo_summary_fields(),
            "package_manager": package_manager_id,
            "dependency_files": [node.id for node in dependency_nodes],
            "test_framework": self._detect_test_framework(evidence),
            "entrypoints": self._detect_entrypoints(evidence),
        }

        return nodes, edges, repo_summary, risk_notes, fallback_plan, unresolved_questions

    def _runtime_node(self, decision: BaseImageDecision) -> TaskNode:
        return TaskNode(
            id=decision.selected_image,
            type="runtime",
            label=f"{decision.detected_language} runtime",
            command_hint=f"FROM {decision.selected_image}",
            evidence=decision.evidence or ["repository evidence"],
            confidence=0.9 if decision.evidence else 0.6,
            scope="test",
            metadata=decision.to_runtime_metadata(),
        )

    def _package_manager_id(self, evidence: RepositoryEvidence) -> str:
        files = self._available_files(evidence)
        if "poetry.lock" in files:
            return "poetry"
        if "pipfile.lock" in files or "pipfile" in files:
            return "pipenv"
        if "environment.yml" in files or "environment.yaml" in files:
            return "conda"
        return "pip"

    def _package_manager_command_hint(self, package_manager_id: str):
        hints = {
            "pip": "python -m pip install --upgrade pip",
            "poetry": "python -m pip install poetry",
            "pipenv": "python -m pip install pipenv",
            "conda": "conda env update -f environment.yml",
        }
        return hints.get(package_manager_id)

    def _package_manager_evidence(self, evidence: RepositoryEvidence) -> List[str]:
        files = self._available_files(evidence)
        for candidate in ("poetry.lock", "Pipfile.lock", "Pipfile", "environment.yml", "requirements.txt", "pyproject.toml"):
            if candidate.lower() in files:
                return [candidate]
        return evidence.relevant_files[:1] or ["repository structure"]

    def _dependency_nodes(self, evidence: RepositoryEvidence, package_manager_id: str) -> List[TaskNode]:
        nodes = []
        dependency_files = []
        for path in evidence.files_content:
            lowered = path.lower()
            if lowered.startswith("requirements") and lowered.endswith(".txt"):
                dependency_files.append(path)
        if "pyproject.toml" in evidence.files_content and not dependency_files:
            dependency_files.append("pyproject.toml")
        if "setup.py" in evidence.files_content and not dependency_files:
            dependency_files.append("setup.py")
        if "setup.cfg" in evidence.files_content and not dependency_files:
            dependency_files.append("setup.cfg")
        if "environment.yml" in evidence.files_content:
            dependency_files.append("environment.yml")

        for path in sorted(dict.fromkeys(dependency_files), key=self._dependency_sort_key):
            scope = "test" if "test" in path.lower() or "dev" in path.lower() else "runtime"
            nodes.append(
                TaskNode(
                    id=path,
                    type="language_dependency",
                    label=f"Python dependency file: {path}",
                    command_hint=self._dependency_command_hint(path, package_manager_id),
                    evidence=[path],
                    confidence=0.9,
                    scope=scope,
                    metadata={},
                )
            )
        return nodes

    def _available_files(self, evidence: RepositoryEvidence):
        return {
            path.replace("\\", "/").lower()
            for path in list(evidence.files_content) + list(evidence.relevant_files)
        }

    def _dependency_command_hint(self, path: str, package_manager_id: str):
        if package_manager_id == "poetry":
            return "poetry install"
        if package_manager_id == "pipenv":
            return "pipenv install --dev"
        if package_manager_id == "conda":
            return f"conda env update -f {path}"
        if path.endswith(".txt"):
            return f"pip install -r {path}"
        if path == "pyproject.toml":
            return "pip install -e ."
        if path in {"setup.py", "setup.cfg"}:
            return "pip install -e ."
        return None

    def _dependency_sort_key(self, path: str):
        lowered = path.lower()
        if lowered == "requirements.txt":
            return 0, path
        if "test" in lowered or "dev" in lowered:
            return 1, path
        return 2, path

    def _project_build_node(self, evidence: RepositoryEvidence, package_manager_id: str) -> TaskNode:
        if self._tox_skip_install(evidence.files_content.get("tox.ini", "")):
            return TaskNode(
                id="source-tree execution",
                type="project_build",
                label="Run tests from source tree",
                command_hint=None,
                evidence=["tox.ini"],
                confidence=0.85,
                scope="test",
                metadata={"skip_install": True},
            )
        if package_manager_id == "poetry":
            return TaskNode(
                id="poetry install",
                type="project_build",
                label="Install project with Poetry",
                command_hint="poetry install",
                evidence=["poetry.lock"],
                confidence=0.9,
                scope="test",
                metadata={},
            )
        if any(path in evidence.files_content for path in ("pyproject.toml", "setup.py", "setup.cfg")):
            build_evidence = [
                path
                for path in ("pyproject.toml", "setup.py", "setup.cfg")
                if path in evidence.files_content
            ]
            return TaskNode(
                id="pip install -e .",
                type="project_build",
                label="Install project in editable mode",
                command_hint="pip install -e .",
                evidence=build_evidence,
                confidence=0.75,
                scope="test",
                metadata={},
            )
        return TaskNode(
            id="source-tree execution",
            type="project_build",
            label="Run tests from source tree",
            command_hint=None,
            evidence=evidence.relevant_files[:1] or ["repository structure"],
            confidence=0.65,
            scope="test",
            metadata={},
        )

    def _verification_nodes(self, evidence: RepositoryEvidence) -> List[TaskNode]:
        test_framework = self._detect_test_framework(evidence)
        if test_framework == "pytest":
            if is_ratbench_target(self.evaluation_target):
                evidence_items = self._pytest_evidence(evidence)
                return [
                    TaskNode(
                        id="pytest --collect-only -q --disable-warnings",
                        type="verification",
                        label="RATBench pytest collection gate",
                        command_hint="pytest --collect-only -q --disable-warnings",
                        evidence=evidence_items,
                        confidence=0.9,
                        scope="test",
                        metadata={"ratbench_target": True, "phase": "collect_gate"},
                    ),
                    TaskNode(
                        id="python -m pytest",
                        type="verification",
                        label="RATBench full pytest execution",
                        command_hint="python -m pytest",
                        evidence=evidence_items,
                        confidence=0.85,
                        scope="test",
                        metadata={"ratbench_target": True, "phase": "full_pytest"},
                    ),
                ]
            return [
                TaskNode(
                    id="pytest --collect-only -q --disable-warnings",
                    type="verification",
                    label="Repo2Run pytest collection",
                    command_hint="pytest --collect-only -q --disable-warnings",
                    evidence=self._pytest_evidence(evidence),
                    confidence=0.9,
                    scope="test",
                    metadata={"repo2run_target": True},
                )
            ]
        return [
            TaskNode(
                id="python -m unittest discover",
                type="verification",
                label="unittest discovery",
                command_hint="python -m unittest discover",
                evidence=evidence.relevant_files[:1] or ["repository structure"],
                confidence=0.55,
                scope="test",
                metadata={"repo2run_target": False},
            )
        ]

    def _detect_test_framework(self, evidence: RepositoryEvidence) -> str:
        files = {path.lower() for path in evidence.files_content}
        if "pytest.ini" in files or "conftest.py" in files:
            return "pytest"
        if any(path.startswith("tests/") and "test_" in path for path in files):
            return "pytest"
        if "pytest" in "\n".join(evidence.files_content.values()).lower():
            return "pytest"
        return "unittest"

    def _pytest_evidence(self, evidence: RepositoryEvidence) -> List[str]:
        candidates = [
            path
            for path in evidence.files_content
            if path.lower() in {"pytest.ini", "tox.ini", "pyproject.toml"}
            or path.lower().startswith("tests/")
        ]
        return candidates[:5] or evidence.relevant_files[:1] or ["repository structure"]

    def _detect_entrypoints(self, evidence: RepositoryEvidence) -> List[str]:
        readme_text = "\n".join(
            text
            for path, text in evidence.files_content.items()
            if path.lower().startswith("readme")
        )
        commands = re.findall(r"(?:python|python3)\s+[\w./-]+\.py(?:\s+[\w:./=-]+)*", readme_text)
        return sorted(dict.fromkeys(command.strip() for command in commands))[:10]

    def _tox_skip_install(self, text: str) -> bool:
        return bool(re.search(r"skip_install\s*=\s*true", text or "", re.IGNORECASE))

    def _edge(self, from_id, to_id, edge_type, strength, edge_evidence, confidence) -> TaskEdge:
        return TaskEdge(
            from_id=from_id,
            to_id=to_id,
            type=edge_type,
            strength=strength,
            evidence=list(edge_evidence),
            confidence=confidence,
        )
