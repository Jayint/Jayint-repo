"""Deterministic agent for the mechanics eval. Maps the CURRENT missing capability to a
read-only probe + a typed patch, and reads session memory (logs that it does). This is the
arm-C agent CONTRACT (next_action → probe|patch); the production LLM SessionAgent implements
the same contract with real reasoning."""
from __future__ import annotations

from dataclasses import dataclass

from python_deps.depgraph.patch import PatchProposal, NodeSpec, ProviderSpec, EdgeSpec


@dataclass
class Fix:
    probe: str
    patch: PatchProposal


class ScriptedSolver:
    def __init__(self, cap_to_fix: dict[str, Fix]):
        self.cap_to_fix = cap_to_fix

    def next_action(self, session, failure, log):
        cap = failure.failing_cap
        already = [s.summary for s in session.steps if s.kind == "patch"]
        if already:
            log.d("MEMORY", f"agent recalls prior patches {already}; now targeting {cap!r}")
        fix = self.cap_to_fix.get(cap)
        if fix is None:
            return ("patch", _noop_patch(cap), cap)      # can't solve -> unhelpful (drives stall)
        if not session.probed(cap):
            return ("probe", fix.probe, cap)
        return ("patch", fix.patch, cap)


def syslib_patch(cap, node_id, apt_pkg, check, requirer) -> PatchProposal:
    return PatchProposal(
        rationale={"why": f"{requirer} needs {cap}"},
        add_requirements=(NodeSpec(id=node_id, type="SystemLib", name=cap, layer="system",
                                   check_command=check, evidence_ref="ev.1"),),
        add_providers=(ProviderSpec(id=f"apt:{apt_pkg}", kind="apt",
                                    command=f"apt-get install -y {apt_pkg}", provides=(node_id,)),),
        add_edges=(EdgeSpec(source=requirer, target=node_id, relation="requires", hard=True),))


def tool_patch(cap, node_id, apt_pkg, check, requirer) -> PatchProposal:
    return PatchProposal(
        rationale={"why": f"{requirer} build needs {cap}"},
        add_requirements=(NodeSpec(id=node_id, type="Tool", name=cap, layer="toolchain",
                                   check_command=check, evidence_ref="ev.1"),),
        add_providers=(ProviderSpec(id=f"apt:{apt_pkg}", kind="apt",
                                    command=f"apt-get install -y {apt_pkg}", provides=(node_id,)),),
        add_edges=(EdgeSpec(source=requirer, target=node_id, relation="requires", hard=True),))


def _noop_patch(cap) -> PatchProposal:
    """A valid but useless patch: adds an unrelated syslib that provides nothing real."""
    nid = f"syslib:dummy-{cap}"
    return PatchProposal(
        rationale={"why": "guess"},
        add_requirements=(NodeSpec(id=nid, type="SystemLib", name=f"dummy-{cap}", layer="system",
                                   check_command=f"ldconfig -p | grep -q dummy{cap}",
                                   evidence_ref="ev.1"),),
        add_providers=(ProviderSpec(id="apt:dummy", kind="apt", command="apt-get install -y dummy",
                                    provides=(nid,)),))
