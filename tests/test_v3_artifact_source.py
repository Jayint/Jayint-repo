import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent import DockerAgent
from src.envstate.ledger import ActionLedger
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State, DiscoveredBy


class _SpySynth:
    """Records the recipe dict; exposes the attr the toggle-OFF ledger path reads."""
    def __init__(self):
        self.applied = None
    def apply_build_recipe(self, recipe):
        self.applied = recipe
    def _extract_recordable_setup_commands(self, cmd):   # used as `distill` by build_commands_from_ledger
        return cmd


def _graph(state):
    # SATISFIED on purpose: proves the artifact compiles the *certified* graph
    # (compile_blocks would yield nothing here; compile_replay_blocks must not).
    return DepGraph().with_node(Node(id="syslib:libpq.so", type=NodeType.SYSTEM_LIB,
        name="libpq.so", layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER,
        state=state, check_command="ldconfig -p | grep -q libpq", chosen_fix="apt:libpq-dev"))


def _agent(materialize, graph):
    a = object.__new__(DockerAgent)               # __new__ bypass (established suite pattern)
    a.enable_script_materialization = materialize
    a._final_dep_graph = graph
    a.enable_envstate = True
    a.action_ledger = ActionLedger()              # real, empty (toggle-off path applies w/ drop_replayed_state)
    a.synthesizer = _SpySynth()
    a.setup_log_dir = None                        # _persist_setup_sh no-ops without a dir
    return a


def test_v3_artifact_source_is_compiled_setup_sh():
    a = _agent(True, _graph(State.SATISFIED))
    assert a._synthesize_final_build_recipe(drop_replayed_state=True) is True
    assert a.build_recipe_source == "compiled_setup_sh"
    assert any("libpq-dev" in c for c in a.synthesizer.applied["build_commands"])


def test_toggle_off_keeps_action_ledger_source():
    a = _agent(False, _graph(State.SATISFIED))
    assert a._synthesize_final_build_recipe(drop_replayed_state=True) is True
    assert a.build_recipe_source == "action_ledger"
