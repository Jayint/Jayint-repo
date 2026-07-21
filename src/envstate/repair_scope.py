# src/envstate/repair_scope.py
"""The §9 RepairScope packet (2b §6.1): curated, structured context for the v3 typed-patch
agent. Pure: no Docker/network/LLM. Re-derives the Phase-1 RequirementSlice; never raw history."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.req_slice import build_requirement_slice, render_requirement_slice
from python_deps.depgraph.schema import NodeType


PATCH_SCHEMA_HINT = """\
Respond with EXACTLY ONE fenced JSON object and nothing after it:
```json
{
  "rationale": {"why": "<one sentence>"},
  "patch": {
    "add_requirements": [{"id": "syslib:<name>", "type": "SystemLib", "name": "<name>",
       "layer": "system", "check_command": "<read-only check>", "evidence_ref": "<ev.id>"}],
    "add_providers": [{"id": "apt:<pkg>", "kind": "apt",
       "command": "apt-get install -y <pkg>", "provides": ["syslib:<name>"], "override": false}],
    "add_edges": [{"source": "<id>", "target": "<id>", "relation": "requires", "hard": true}],
    "script_patches": [{"op": "add_block", "block_id": "<layer>.<short>", "wave": "system",
       "commands": ["<install>"], "target_node_ids": ["<node id>"],
       "checks": ["<read-only check>"], "provides": ["<id>"], "evidence_ref": "<ev.id>"}]
  }
}
```
Rules: canonical ids (syslib:/pkg:/tool:/service:/config:). check_command MUST be read-only.
Cite an evidence_ref present in the evidence below. Set "override": true to replace a known-bad provider.
For system packages, use kind="apt" with apt-get install on Debian/Ubuntu or
kind="apk" with apk add on Alpine, according to host evidence. Never invent
kind="system", kind="script", or kind="command".
If the failed block is graph-derived, repair its node with add_providers and override=true;
do not append a parallel script block for the same obligation. If the failed block is a
previous script patch, emit op="replace_block" with EXACTLY the same block_id so the bad
commands are replaced in place. Never keep adding alternative blocks after a failed block.
For an existing import:<name> obligation, prefer a graph-native pinned package fix:
add a {"id":"pkg:<dist>","type":"Package","name":"<dist>","version":"<exact>",
"layer":"pip","check_command":"python -m pip show <dist>","evidence_ref":"<ev.id>"}
requirement plus a requires edge from import:<name> to pkg:<dist>. If no exact version
can be justified, add a governed pip script_patches block targeting the existing import node."""


STRUCTURED_ACTION_SCHEMA_HINT = """\
Respond with exactly ONE JSON object and no prose. Allowed actions:
1. {"type":"probe","target_node":"<id>","purpose":"<why needed>","command":"<read-only command>"}
2. {"type":"propose_patch","target_node":"<id>","rationale":{"why":"<one sentence>"},
    "patch":{
      "add_requirements":[{"id":"pkg:<name>","type":"Package","name":"<name>",
        "layer":"pip","check_command":"<read-only check>","evidence_ref":"<ev.id>",
        "promotion":"candidate","version":"<exact PEP-440 version>"}],
      "add_providers":[{"id":"pip:<name>","kind":"pip",
        "command":"python3 -m pip install --break-system-packages <name>==<version>",
        "provides":["pkg:<name>"],"override":false}],
      "add_edges":[{"source":"import:<module>","target":"pkg:<name>",
        "relation":"requires","hard":true}],
      "script_patches":[{"op":"add_block","block_id":"<unique id>","wave":"naming",
        "commands":["<mutation command>"],"target_node_ids":["<failed node id>"],
        "checks":["<read-only check>"],"provides":["<node id>"],
        "evidence_ref":"<ev.id>"}],
      "request_checks":[]}}
3. {"type":"abstain","classification":"non_environment","reason":"<reason>",
    "evidence_refs":["<ev.id>"]}
Every array may be empty, but a patch must make at least one real change. Legal layer/wave
values are: interpreter, system, toolchain, pip, naming, runtime, tests, config, services,
dependencies, build.
Script patches require block_id, wave, commands, non-empty target_node_ids, and the singular
evidence_ref from the available evidence list. Use op=replace_block only for an existing
manual block and preserve its exact block_id. A proposed command must make the Host's stated
node check succeed; changing repository imports alone does not satisfy an `import X` check.
Provider ids such as pip:<name> are actions, not graph nodes, and must never be edge endpoints.
For a missing import, add pkg:<dist>, make the provider provide that package node, and add the
directed edge import:<module> -> pkg:<dist> (`import` requires `package`). The explicit provider
command is the governed Build Plan command, so include dependencies unless they are separate
package nodes in the patch.
When Graph context includes a curated service package/start recipe, propose one governed
services-wave script block targeting the existing service node and use the exact curated
commands. Do not invent an external sidecar or replace the repository's service address.
The Host alone selects checkpoints, creates/commits/aborts candidate containers, executes
Blocks, and certifies SATISFIED. Probe commands are validated before execution. Native
package-manager probes must be non-mutating: use cargo --locked --offline, go
-mod=readonly, Maven -o/--offline, and Gradle --offline query modes."""


def _curated_action_recipe_lines(node) -> tuple[str, ...]:
    """Render host-curated service actions alongside the requirement slice.

    A target obligation can reach the structured-repair path without first being
    rendered as a scheduler ``Task``.  Keeping the recipe on the graph node is
    only useful if that path exposes it to the action proposer as well.  These
    are trusted table/runtime-derived values; certification remains host-owned.
    """
    if node is None or not hasattr(node, "data"):
        return ()
    recipe = node.data.get("start_recipe") or {}
    if not isinstance(recipe, dict) and not hasattr(recipe, "get"):
        return ()
    lines: list[str] = []
    if recipe.get("system_package"):
        lines.append(f"curated service package: {recipe['system_package']}")
    if recipe.get("start"):
        lines.append(f"curated service start (use exactly): {recipe['start']}")
    if recipe.get("createdb"):
        lines.append(f"curated service initialization (use exactly): {recipe['createdb']}")
    return tuple(lines)


@dataclass(frozen=True)
class RepairScope:
    target_node_id: str | None
    failed_command: str | None
    failed_output: str
    slice_lines: tuple[str, ...]
    known_invalid: tuple[str, ...]
    constraints: tuple[tuple[str, str], ...]
    known_evidence_ids: frozenset[str]
    failed_block_id: str | None = None
    failed_block_wave: str | None = None
    failed_block_commands: tuple[str, ...] = ()
    failed_block_targets: tuple[str, ...] = ()
    failed_block_providers: tuple[str, ...] = ()
    failed_block_checks: tuple[str, ...] = ()
    failed_block_evidence_refs: tuple[str, ...] = ()
    target_python: str | None = None
    # (package node id, declared specifier, marker, manifest source)
    manifest_requirements: tuple[tuple[str, str, str, str], ...] = ()
    resolution_status: str | None = None
    resolution_error: str | None = None
    language: str | None = None
    language_role: str | None = None
    version_constraint: str | None = None
    manifest_file: str | None = None
    package_manager: str | None = None
    package_manager_version: str | None = None
    workspace: str | None = None
    ecosystem: str | None = None
    base_image: str | None = None
    target_node_type: str | None = None
    target_node_name: str | None = None
    target_node_version: str | None = None
    target_node_provider: str | None = None


def _manifest_context(graph, node):
    """Find declared package constraints at or adjacent to the failed node."""
    if graph is None or node is None or not hasattr(graph, "requires_of"):
        return (), None, None, None
    nearby = [node]
    nearby.extend(graph.requires_of(node.id))
    nearby.extend(graph.required_by(node.id))
    packages = []
    seen = set()
    for candidate in nearby:
        if (
            candidate.type not in {
                NodeType.PACKAGE,
                NodeType.REQUIREMENT,
                NodeType.DEPENDENCY_SET,
            }
            or candidate.id in seen
        ):
            continue
        seen.add(candidate.id)
        if (
            candidate.manifest_source
            or candidate.declared_specifier
            or candidate.declared_constraint
            or candidate.declared_marker
        ):
            packages.append(candidate)
    requirements = tuple(
        (
            pkg.id,
            pkg.declared_specifier or pkg.declared_constraint or "",
            pkg.declared_marker or "",
            pkg.manifest_source or "",
        )
        for pkg in packages
    )
    primary = packages[0] if packages else (
        node if node.type in {
            NodeType.PACKAGE,
            NodeType.REQUIREMENT,
            NodeType.DEPENDENCY_SET,
        } else None
    )
    target_python = next(
        (item.resolved_python for item in nearby if item.resolved_python), None
    )
    if target_python is None:
        target_python = next(
            (item.version for item in graph.nodes if item.type is NodeType.RUNTIME), None
        )
    return (
        requirements,
        target_python,
        primary.resolution_status if primary is not None else None,
        primary.resolution_error if primary is not None else None,
    )


def build_repair_scope(graph, *, target_node_id, failed_block, bundle,
                       known_invalid=(), constraints=None):
    cons = tuple(sorted((str(k), str(v)) for k, v in dict(constraints or {}).items()))
    # Use hasattr so tests can pass object() as graph with a monkeypatched build_requirement_slice.
    # In production graph is a real DepGraph with .get(); in tests the lambda ignores node.
    node = graph.get(target_node_id) if (graph is not None and target_node_id
                                         and hasattr(graph, 'get')) else None
    slice_lines = ()
    # Enter when (a) node resolved from a real graph, OR (b) graph has no .get (test stub:
    # build_requirement_slice is monkeypatched and ignores node, so None is safe). Never enter
    # when a real DepGraph lookup returned None (avoids build_requirement_slice(graph, None) -> None.id).
    if target_node_id and (node is not None or not hasattr(graph, 'get')):
        slice_lines = tuple(render_requirement_slice(build_requirement_slice(graph, node)))
        slice_lines += _curated_action_recipe_lines(node)
    manifest_requirements, target_python, resolution_status, resolution_error = (
        _manifest_context(graph, node)
    )
    failed_cmd = failed_block.commands[-1] if (failed_block and failed_block.commands) else None
    failed_out = ""
    # bundle may be None on the binding-install path when there is no install-command failure
    # (e.g. install rc 0 but a reciped check still MISSING). On an install failure the binding
    # path supplies a single-item EvidenceBundle built from the InstallResult (see
    # orchestrator._build_install_evidence). Treat a missing bundle as empty evidence rather
    # than crashing on bundle.items.
    _evidence = bundle.items if bundle is not None else ()
    for ev in _evidence:
        block_match = (
            failed_block is None
            or ev.block_id == failed_block.block_id
            or ev.node_id in failed_block.target_node_ids
        )
        if ev.rc != 0 and block_match:
            failed_cmd = ev.command or failed_cmd
            failed_out = ev.output_excerpt or ""
    return RepairScope(
        target_node_id=target_node_id, failed_command=failed_cmd, failed_output=failed_out,
        slice_lines=slice_lines, known_invalid=tuple(known_invalid), constraints=cons,
        known_evidence_ids=frozenset(ev.evidence_id for ev in _evidence),
        failed_block_id=failed_block.block_id if failed_block else None,
        failed_block_wave=failed_block.wave if failed_block else None,
        failed_block_commands=tuple(failed_block.commands) if failed_block else (),
        failed_block_targets=tuple(failed_block.target_node_ids) if failed_block else (),
        failed_block_providers=tuple(failed_block.provider_ids) if failed_block else (),
        failed_block_checks=tuple(failed_block.check_commands) if failed_block else (),
        failed_block_evidence_refs=tuple(failed_block.evidence_refs) if failed_block else (),
        target_python=target_python,
        manifest_requirements=manifest_requirements,
        resolution_status=resolution_status,
        resolution_error=resolution_error,
        language=getattr(node, "language", None),
        language_role=getattr(node, "language_role", None),
        version_constraint=getattr(node, "version_constraint", None),
        manifest_file=getattr(node, "manifest_source", None),
        package_manager=getattr(node, "package_manager", None),
        package_manager_version=(
            str(getattr(node, "data", {}).get("package_manager_version") or "")
            or None
        ),
        workspace=getattr(node, "workspace", None),
        ecosystem=(
            getattr(getattr(node, "ecosystem", None), "value", None)
            if node is not None else None
        ),
        base_image=dict(cons).get("base_image"),
        target_node_type=(node.type.value if node is not None else None),
        target_node_name=(node.name if node is not None else None),
        target_node_version=(node.version if node is not None else None),
        target_node_provider=(node.chosen_fix if node is not None else None),
    )


def target_patch_constraints(scope: RepairScope) -> str:
    """Return the strongest target-specific patch contract, when one applies."""
    if (
        scope.target_node_type == NodeType.PACKAGE.value
        and not scope.target_node_version
        and scope.target_node_id
        and scope.target_node_name
    ):
        target = scope.target_node_id
        name = scope.target_node_name
        return (
            "TARGET-SPECIFIC REQUIRED PATCH SHAPE:\n"
            f"{target} is an EXISTING unresolved Package node. Do not repeat it in "
            "add_requirements. Return a provider-only correction: add_requirements=[], "
            "add_edges=[], script_patches=[], and exactly one add_providers entry with "
            f'kind="pip", provides=["{target}"], override=true, and command '
            f'"python3 -m pip install --break-system-packages {name}==<exact-version>". '
            "Do not invent an import:* node or edge."
        )
    return ""


def render_repair_scope(scope: RepairScope, *, structured_actions: bool = False) -> str:
    parts = []
    if scope.target_node_id:
        parts.append(f"Failing obligation: {scope.target_node_id}")
    if scope.slice_lines:
        parts.append("Graph context:\n" + "\n".join(scope.slice_lines))
    if scope.failed_block_id:
        block_lines = [
            f"id={scope.failed_block_id}",
            f"wave={scope.failed_block_wave or '-'}",
            "targets=" + (", ".join(scope.failed_block_targets) or "-"),
            "providers=" + (", ".join(scope.failed_block_providers) or "-"),
            "commands=" + (" ; ".join(scope.failed_block_commands) or "-"),
            "checks=" + (" ; ".join(scope.failed_block_checks) or "-"),
            "evidence_refs=" + (", ".join(scope.failed_block_evidence_refs) or "-"),
        ]
        parts.append("Failed execution block:\n" + "\n".join(block_lines))
    if scope.failed_command:
        parts.append(f"Failed command: {scope.failed_command}")
    if scope.failed_output:
        parts.append("Failure output:\n" + scope.failed_output)
    if scope.known_invalid:
        parts.append("DO NOT propose these (already failed): " + ", ".join(scope.known_invalid))
    if scope.constraints:
        parts.append("Constraints: " + ", ".join(f"{k}={v}" for k, v in scope.constraints))
    if scope.target_python:
        parts.append(f"Target Python: {scope.target_python}")
    ecosystem_lines = []
    if scope.language:
        ecosystem_lines.append(f"language={scope.language}")
    if scope.language_role:
        ecosystem_lines.append(f"role={scope.language_role}")
    if scope.version_constraint:
        ecosystem_lines.append(f"version_constraint={scope.version_constraint}")
    if scope.ecosystem:
        ecosystem_lines.append(f"ecosystem={scope.ecosystem}")
    if scope.package_manager:
        ecosystem_lines.append(f"package_manager={scope.package_manager}")
    if scope.package_manager_version:
        ecosystem_lines.append(
            f"package_manager_version={scope.package_manager_version}"
        )
    if scope.workspace:
        ecosystem_lines.append(f"workspace={scope.workspace}")
    if scope.manifest_file:
        ecosystem_lines.append(f"manifest={scope.manifest_file}")
    if scope.base_image:
        ecosystem_lines.append(f"base_image={scope.base_image}")
    if ecosystem_lines:
        parts.append("Ecosystem context:\n" + "\n".join(ecosystem_lines))
    if scope.manifest_requirements:
        lines = []
        for node_id, specifier, marker, source in scope.manifest_requirements:
            detail = f"{node_id} {specifier or '(unconstrained)'}"
            if marker:
                detail += f" ; {marker}"
            if source:
                detail += f" [{source}]"
            lines.append(detail)
        parts.append("Original manifest requirements:\n" + "\n".join(lines))
    if scope.resolution_status:
        parts.append(f"Resolution status: {scope.resolution_status}")
    if scope.resolution_error:
        parts.append("Resolution error: " + scope.resolution_error)
    parts.append("Cite evidence by id (available: "
                 + ", ".join(sorted(scope.known_evidence_ids)) + ").")
    parts.append(STRUCTURED_ACTION_SCHEMA_HINT if structured_actions else PATCH_SCHEMA_HINT)
    target_constraints = target_patch_constraints(scope)
    if target_constraints:
        # Keep the target-specific rule LAST so it overrides irrelevant generic
        # missing-import examples in the schema immediately above it.
        parts.append(target_constraints)
    return "\n\n".join(parts)
