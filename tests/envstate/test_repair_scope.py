import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import src.envstate.repair_scope as rs
from python_deps.depgraph.block import Block
from python_deps.depgraph.evidence_log import Evidence, EvidenceBundle

def _bundle():
    return EvidenceBundle().with_item(Evidence(
        evidence_id="ev.1.0", container_kind="canonical",
        command="apt-get install -y libplacebodev", rc=1,
        output_excerpt="Unable to locate package libplacebodev", cycle=1,
        block_id="system.libplacebo", node_id="syslib:libplacebo"))

def test_build_scope_carries_failure_and_evidence(monkeypatch):
    monkeypatch.setattr(rs, "build_requirement_slice", lambda g, n: object())
    monkeypatch.setattr(rs, "render_requirement_slice",
                        lambda s: ("target: syslib:libplacebo",))
    fb = Block(block_id="system.libplacebo", wave="system",
               commands=("apt-get install -y libplacebodev",),
               target_node_ids=("syslib:libplacebo",))
    scope = rs.build_repair_scope(
        object(), target_node_id="syslib:libplacebo", failed_block=fb, bundle=_bundle(),
        known_invalid=("apt:libplacebodev",), constraints={"package_manager": "apt"})
    assert scope.failed_command == "apt-get install -y libplacebodev"
    assert "libplacebodev" in scope.failed_output
    assert "ev.1.0" in scope.known_evidence_ids
    assert scope.constraints == (("package_manager", "apt"),)

def test_render_surfaces_avoidlist_and_schema(monkeypatch):
    monkeypatch.setattr(rs, "build_requirement_slice", lambda g, n: object())
    monkeypatch.setattr(rs, "render_requirement_slice", lambda s: ("CHECK: pkg-config",))
    scope = rs.build_repair_scope(
        object(), target_node_id="syslib:libplacebo", failed_block=None, bundle=_bundle(),
        known_invalid=("apt:libplacebodev",), constraints=None)
    text = rs.render_repair_scope(scope)
    assert "apt:libplacebodev" in text          # avoid-list
    assert "CHECK: pkg-config" in text           # slice lines
    assert "add_providers" in text               # schema hint
