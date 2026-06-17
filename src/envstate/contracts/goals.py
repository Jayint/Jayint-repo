"""Host goal-contract template keyed to the verification target + readiness."""
from __future__ import annotations

from typing import Any, Iterable

from . import ids
from .graph import ContractGraph
from .nodes import Edge, Node

DEFAULT_VERIFY_CMD = "python -m pytest -q"
GOAL_TESTS_RUN = ids.goal_contract_id("repo_tests_run")
CONTRACT_PYTEST_RUNNABLE = "contract:pytest_runnable"


def seed_goal_template(required: Iterable[Any], verify_cmd: str = DEFAULT_VERIFY_CMD) -> tuple[list[Node], list[Edge]]:
    nodes: list[Node] = [
        Node(ids.verification_target_id("pytest_run"), "VerificationTarget",
             {"kind": "pytest_run", "command_template": verify_cmd}),
        Node(GOAL_TESTS_RUN, "Contract",
             {"level": "goal", "kind": "repo_tests_run", "subject": "repo",
              "predicate": "tests_run_and_pass", "expected": True, "required": True,
              "description": "The repo test suite runs and a majority of tests pass.",
              "validation_state": "validator_confirmed"}),
        Node(CONTRACT_PYTEST_RUNNABLE, "Contract",
             {"level": "atomic", "kind": "pytest_runnable", "subject": "pytest",
              "predicate": "collects", "expected": True,
              "description": "pytest can collect the test suite without import errors.",
              "validation_state": "validator_confirmed"}),
    ]
    edges: list[Edge] = [Edge(GOAL_TESTS_RUN, "depends_on", CONTRACT_PYTEST_RUNNABLE)]
    for fact in required:
        subj = fact.name
        cid = ids.contract_id("python_package_importable", ids.slug(subj) or subj)
        nodes.append(
            Node(cid, "Contract",
                 {"level": "atomic", "kind": "python_package_importable", "subject": subj,
                  "predicate": "is_importable", "expected": True,
                  "description": f"The Python package `{subj}` must be importable.",
                  "validation_state": "validator_confirmed"})
        )
        edges.append(Edge(GOAL_TESTS_RUN, "depends_on", cid))
    return nodes, edges


def _is_satisfied(graph: ContractGraph, contract_id: str) -> bool:
    ev = graph.latest_status(contract_id)
    return ev is not None and ev.status == "satisfied"


def evaluate_goal_readiness(graph: ContractGraph) -> bool:
    """Required goal contracts AND their depends_on atomic contracts are satisfied."""
    required_goals = graph.required_goal_contracts()
    if not required_goals:
        return False
    for goal in required_goals:
        if not _is_satisfied(graph, goal.id):
            return False
        for dep in graph.out_edges(goal.id, "depends_on"):
            if not _is_satisfied(graph, dep.target):
                return False
    return True
