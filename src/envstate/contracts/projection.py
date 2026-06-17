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
