"""Coarse goal/phase backbone seeded once at cold-start (spec §6.2)."""
from __future__ import annotations
from . import ids
from .nodes import Edge, Node

GOAL_NAMES = ("repo_tests_pass", "repo_tests_collect", "repo_imports_work",
              "repo_deps_installed", "repo_build_ready", "repo_services_ready", "repo_config_ready")
FOUNDATIONAL = ("python_version_compatible", "package_manager_available",
                "test_runner_available", "project_installable")
GOAL_IDS = frozenset(ids.goal_contract_id(n) for n in GOAL_NAMES)
FOUNDATIONAL_IDS = frozenset(ids.foundational_contract_id(n) for n in FOUNDATIONAL)
GOAL_TESTS_PASS = ids.goal_contract_id("repo_tests_pass")

_LAYER = {"repo_tests_pass": "tests", "repo_tests_collect": "tests", "repo_imports_work": "deps",
          "repo_deps_installed": "deps", "repo_build_ready": "build",
          "repo_services_ready": "runtime", "repo_config_ready": "config"}
_CHECK = {"repo_tests_pass": "python -m pytest -q",
          "repo_tests_collect": "python -m pytest --collect-only -q --disable-warnings"}

# (source_name, target_id) ordering backbone
_BACKBONE = [
    ("repo_tests_pass", ids.goal_contract_id("repo_tests_collect")),
    ("repo_tests_pass", ids.goal_contract_id("repo_imports_work")),
    ("repo_tests_pass", ids.goal_contract_id("repo_deps_installed")),
    ("repo_tests_pass", ids.goal_contract_id("repo_build_ready")),
    ("repo_tests_pass", ids.goal_contract_id("repo_services_ready")),
    ("repo_tests_pass", ids.goal_contract_id("repo_config_ready")),
    ("repo_tests_collect", ids.goal_contract_id("repo_imports_work")),
    ("repo_tests_collect", ids.foundational_contract_id("test_runner_available")),
    ("repo_imports_work", ids.goal_contract_id("repo_deps_installed")),
    ("repo_deps_installed", ids.foundational_contract_id("package_manager_available")),
    ("repo_deps_installed", ids.foundational_contract_id("python_version_compatible")),
]
BACKBONE_EDGES = tuple(Edge(ids.goal_contract_id(s), "depends_on", t) for s, t in _BACKBONE)


def seed_backbone() -> tuple[list[Node], list[Edge]]:
    nodes: list[Node] = []
    for name in GOAL_NAMES:
        nodes.append(Node(ids.goal_contract_id(name), "Contract",
            {"level": "goal", "kind": name, "subject": "repo", "layer": _LAYER[name],
             "required": name == "repo_tests_pass", "check": _CHECK.get(name, ""),
             "source_refs": ["goal"], "evidence_refs": [],
             "description": f"Goal contract: {name}.", "metadata": {}}))
    for name in FOUNDATIONAL:
        nodes.append(Node(ids.foundational_contract_id(name), "Contract",
            {"level": "atomic", "kind": name, "subject": name, "layer": "runtime",
             "required": False, "check": "", "source_refs": ["foundational"],
             "evidence_refs": [], "description": f"Foundational: {name}.", "metadata": {}}))
    return nodes, list(BACKBONE_EDGES)
