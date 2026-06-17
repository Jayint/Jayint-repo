"""Host-owned deterministic projection of facts into contract-graph nodes/edges.

No LLM. Every function is pure: facts in, (nodes, edges) out. refresh_host_graph
(Task 14) composes them, dedups against the existing graph by id, validates with
scope='host', and applies.
"""
from __future__ import annotations

from typing import Any, Iterable

from . import ids
from .nodes import Edge, Node
from .schema import redact_secrets

# Manifest files we treat as concrete declaring artifacts (file-level only, spec §5).
_MANIFEST_FILES = (
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "poetry.lock",
    "environment.yml",
)


def project_repo_artifacts(repo_layout: Iterable[str]) -> list[Node]:
    nodes: list[Node] = []
    for entry in repo_layout:
        name = entry.rstrip("/")
        if name in _MANIFEST_FILES:
            nodes.append(Node(ids.artifact_id(name), "RepoArtifact", {"path": name, "artifact_kind": "manifest_file"}))
    return nodes


def _declaring_artifact(artifacts: list[Node]) -> Node | None:
    # Prefer requirements.txt, else pyproject.toml, else first available.
    by_path = {n.data["path"]: n for n in artifacts}
    for pref in ("requirements.txt", "pyproject.toml"):
        if pref in by_path:
            return by_path[pref]
    return artifacts[0] if artifacts else None


def project_requirements(required: Iterable[Any], artifacts: list[Node]) -> tuple[list[Node], list[Edge]]:
    """required: tuple[world_model.Fact]. Each Requirement is declares-anchored or dropped."""
    artifact = _declaring_artifact(artifacts)
    if artifact is None:
        return [], []  # cannot ground -> spec forbids LLM-only requirements
    nodes: list[Node] = []
    edges: list[Edge] = []
    for fact in required:
        rid = ids.requirement_id("python_dependency", ids.slug(fact.name) or fact.name)
        nodes.append(
            Node(rid, "Requirement", {"kind": "python_dependency", "subject": fact.name, "spec": fact.detail or ""})
        )
        edges.append(Edge(artifact.id, "declares", rid))
    return nodes, edges


def project_command_executions(events: Iterable[Any]) -> list[Node]:
    nodes: list[Node] = []
    for ev in events:
        nodes.append(
            Node(
                ids.command_id(ev.step),
                "CommandExecution",
                {
                    "command": redact_secrets(ev.cmd),
                    "exit_code": int(ev.rc),
                    "revision_before": ids.revision_id(ev.env_revision_before),
                    "revision_after": ids.revision_id(ev.env_revision_after),
                    "mutation_class": ev.mutation_class,
                },
            )
        )
    return nodes


def project_environment_revisions(events: Iterable[Any]) -> tuple[list[Node], list[Edge]]:
    nodes: list[Node] = []
    edges: list[Edge] = []
    seen: set[str] = set()
    for ev in events:
        if ev.env_revision_after == ev.env_revision_before:
            continue  # read-only / non-mutating command created no revision
        rid = ids.revision_id(ev.env_revision_after)
        if rid not in seen:
            seen.add(rid)
            nodes.append(Node(rid, "EnvironmentRevision", {"created_by_command_id": ids.command_id(ev.step)}))
        edges.append(Edge(ids.command_id(ev.step), "creates_revision", rid))
    return nodes, edges


def project_capabilities(installed: Iterable[Any], system_installed: Iterable[Any], current_revision: int) -> list[Node]:
    nodes: list[Node] = []
    for fact in installed:
        subj = fact.name
        nodes.append(
            Node(
                ids.capability_id("python_package_importable", ids.slug(subj) or subj, current_revision),
                "Capability",
                {"kind": "python_package_importable", "subject": subj, "revision_id": ids.revision_id(current_revision)},
            )
        )
    for fact in system_installed:
        subj = fact.name
        nodes.append(
            Node(
                ids.capability_id("system_artifact_present", ids.slug(subj) or subj, current_revision),
                "Capability",
                {"kind": "system_artifact_present", "subject": subj, "revision_id": ids.revision_id(current_revision)},
            )
        )
    return nodes


def project_open_problems(open_problems: Iterable[Any]) -> list[Node]:
    nodes: list[Node] = []
    for op in open_problems:
        nodes.append(
            Node(
                ids.open_problem_id(op.signature),
                "OpenProblem",
                {
                    "kind": op.layer,
                    "signature": op.signature,
                    "summary": redact_secrets(op.interpretation),
                    "layer": op.layer,
                    "out_of_scope": bool(op.out_of_scope),
                },
            )
        )
    return nodes


def project_failures(events: Iterable[Any]) -> tuple[list[Node], list[Edge]]:
    """One Failure per failing command (host fact). Maintainer adds the `violates` edge."""
    nodes: list[Node] = []
    edges: list[Edge] = []
    for ev in events:
        if ev.rc == 0:
            continue
        cmd_id = ids.command_id(ev.step)
        fid = f"failure:{cmd_id}"
        nodes.append(
            Node(
                fid,
                "Failure",
                {
                    "kind": "command_failed",
                    "command_id": cmd_id,
                    "summary": redact_secrets((ev.stdout or "")[-400:]),
                },
            )
        )
        edges.append(Edge(fid, "observed_in", cmd_id))
    return nodes, edges


def _verified_test_command_id(events: list[Any]) -> str | None:
    """Latest rc-0 command that looks like a real test execution (for goal evidence).

    Three conditions must all hold (aligned with maintainer._verified_test_run_passed):
      1. rc == 0
      2. "pytest" appears in the command string
      3. "--collect-only" is NOT in the command
      4. stdout shows >=1 passed (via _shows_execution) OR the pytest [100%] completion
         marker (via _shows_pytest_completion) — blocks rc=0 runs that merely collected
         tests or ran zero tests from satisfying the goal.
    """
    # Lazy import to avoid a circular-import chain at module load time.
    from src.envstate.maintainer import _shows_execution, _shows_pytest_completion  # noqa: PLC0415

    for ev in reversed(events):
        if ev.rc != 0:
            continue
        if "pytest" not in ev.cmd:
            continue
        if "--collect-only" in ev.cmd:
            continue
        stdout = getattr(ev, "stdout", "") or ""
        if _shows_execution(stdout) or _shows_pytest_completion(stdout):
            return ids.command_id(ev.step)
    return None


def refresh_host_graph(world_map: Any, ledger: Any, snapshot: Any, exec_readonly: Any, current_revision: int, *, on_error: Any = None) -> Any:
    """Project all host facts into world_map.contract_graph (idempotent). Returns a new map."""
    from . import goals
    from .apply import apply_patch
    from .graph import ContractGraph
    from .nodes import ContractStatusEvent
    from .patch import GraphPatch
    from .validation import validate_patch
    from .validators import run_confirmed_validators
    from ..world_model import merge_map

    graph: ContractGraph = world_map.contract_graph
    events = list(ledger.events())

    artifacts = project_repo_artifacts(world_map.repo_layout)
    req_nodes, req_edges = project_requirements(world_map.required, artifacts)
    cmd_nodes = project_command_executions(events)
    rev_nodes, rev_edges = project_environment_revisions(events)
    cap_nodes = project_capabilities(world_map.installed, world_map.system_installed, current_revision)
    fail_nodes, fail_edges = project_failures(events)
    op_nodes = project_open_problems(world_map.open_problems)
    goal_nodes, goal_edges = goals.seed_goal_template(world_map.required)

    candidate_nodes = (
        artifacts + req_nodes + cmd_nodes + rev_nodes + cap_nodes + fail_nodes + op_nodes + goal_nodes
    )
    candidate_edges = req_edges + rev_edges + fail_edges + goal_edges

    # validators run against the graph AS IT WILL BE (goal/atomic contracts present)
    pre_graph = apply_patch(
        graph,
        GraphPatch(
            add_nodes=tuple(n for n in candidate_nodes if not graph.has_node(n.id)),
        ),
    )
    val_nodes, val_edges, val_events = ([], [], [])
    if exec_readonly is not None:
        val_nodes, val_edges, val_events = run_confirmed_validators(pre_graph, exec_readonly, current_revision)

    # goal satisfaction from the host done-gate
    status_events = list(val_events)
    test_cmd_id = _verified_test_command_id(events)
    if world_map.done_flag and test_cmd_id is not None:
        status_events.append(
            ContractStatusEvent(
                contract_id=goals.GOAL_TESTS_RUN, status="satisfied",
                revision_id=ids.revision_id(current_revision), evidence_ids=(test_cmd_id,),
                summary="host done-gate verified a real test run",
            )
        )

    all_new_nodes = candidate_nodes + val_nodes
    all_new_edges = candidate_edges + val_edges
    existing_edge_keys = {(e.source, e.type, e.target) for e in graph.edges}

    patch = GraphPatch(
        add_nodes=tuple(n for n in all_new_nodes if not graph.has_node(n.id)),
        add_edges=tuple(e for e in all_new_edges if (e.source, e.type, e.target) not in existing_edge_keys),
        add_status_events=tuple(status_events),
    )
    errors = validate_patch(graph, patch, scope="host")
    if errors and on_error is not None:
        on_error(errors)
    new_graph = apply_patch(graph, patch)
    return merge_map(world_map, contract_graph=new_graph)
