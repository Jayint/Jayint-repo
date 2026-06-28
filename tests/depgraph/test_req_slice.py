# tests/depgraph/test_req_slice.py
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.schema import Node, NodeType, Layer, State, DiscoveredBy, Attempt
from python_deps.depgraph.req_slice import providers_view, ProviderView, ProviderCand, TriedProvider


def _syslib(**kw):
    base = dict(id="syslib:libxml2", type=NodeType.SYSTEM_LIB, name="libxml2",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
                check_command="pkg-config --exists libxml-2.0",
                fix_candidates=("apt:libxml2-dev",), chosen_fix="apt:libxml2-dev")
    base.update(kw)
    return Node(**base)


def test_candidates_include_chosen_and_action_class():
    pv = providers_view(_syslib())
    ids = [c.id for c in pv.candidates]
    assert "apt:libxml2-dev" in ids
    assert pv.chosen == "apt:libxml2-dev"
    assert next(c.action_class for c in pv.candidates if c.id == "apt:libxml2-dev") == "apt"


def test_chosen_added_to_candidates_when_absent():
    pv = providers_view(_syslib(fix_candidates=()))   # chosen set but not in candidates
    assert "apt:libxml2-dev" in [c.id for c in pv.candidates]


def test_tried_failed_derived_from_failed_attempts_with_reverse_parse():
    node = _syslib(attempts=(
        Attempt(command="apt-get install -y libxml2dev", outcome="failed", check="", cycle=1),
        Attempt(command="apt-get install -y libxml2-dev", outcome="succeeded", check="", cycle=2),
    ))
    pv = providers_view(node)
    assert len(pv.tried_failed) == 1                       # only the failed one
    t = pv.tried_failed[0]
    assert t.command == "apt-get install -y libxml2dev"
    assert t.provider_id == "apt:libxml2dev"                # single-token reverse-parse


def test_batch_command_has_no_provider_id():
    node = _syslib(attempts=(Attempt(command="apt-get install -y a b c", outcome="failed"),))
    assert providers_view(node).tried_failed[0].provider_id is None   # batch -> not attributable


def test_pip_provider_action_class_and_reverse_parse():
    node = Node(id="pkg:lxml==5.0", type=NodeType.PACKAGE, name="lxml", layer=Layer.PIP,
                discovered_by=DiscoveredBy.RESOLVER, state=State.MISSING, version="5.0",
                check_command="python -m pip show lxml", fix_candidates=("pip:lxml",),
                chosen_fix="pip:lxml",
                attempts=(Attempt(command="pip install lxml==5.0", outcome="failed"),))
    pv = providers_view(node)
    assert next(c.action_class for c in pv.candidates if c.id == "pip:lxml") == "pip"
    assert pv.tried_failed[0].provider_id == "pip:lxml"


def test_shell_provider_action_class_from_taxonomy():
    # "shell" is an explicit, audited member of the canonical taxonomy (action_class.ACTION_CLASSES).
    pv = providers_view(_syslib(fix_candidates=("shell:make",), chosen_fix="shell:make"))
    assert next(c.action_class for c in pv.candidates if c.id == "shell:make") == "shell"
