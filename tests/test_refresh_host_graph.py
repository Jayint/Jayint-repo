from src.envstate.contracts.projection import refresh_host_graph
from src.envstate.contracts.goals import GOAL_TESTS_PASS
from src.envstate.world_model import initial_map, merge_map, Fact
from src.envstate.ledger import ActionLedger, ActionEvent


def _ledger(events):
    led = ActionLedger()
    for e in events:
        led.append(e)
    return led


def _base():
    return initial_map(base_image="python:3.11", workdir="/repo", language="python 3.11",
                       build_system="pip", repo_layout=("tests/", "requirements.txt"),
                       required=(Fact("opencv-python", ""),))


def test_seeds_backbone_idempotently():
    m = refresh_host_graph(_base(), _ledger([]), snapshot=None, exec_readonly=None, current_revision=0)
    g1 = m.contract_graph
    m2 = refresh_host_graph(m, _ledger([]), snapshot=None, exec_readonly=None, current_revision=0)
    assert len(m2.contract_graph.nodes) == len(g1.nodes)        # no duplicates
    assert g1.has_node(GOAL_TESTS_PASS)


def test_promotes_atomic_contract_from_failure_signature():
    led = _ledger([ActionEvent(step=1, cmd="python -c 'import cv2'", rc=1,
                               stdout="ImportError: libGL.so.1: cannot open shared object file",
                               env_revision_before=0, env_revision_after=0, mutation_class=None)])
    m = refresh_host_graph(_base(), led, snapshot=None, exec_readonly=None, current_revision=0)
    # The slug of "libGL.so.1" is "libgl-so-1" (non-alphanumerics → dashes), so the
    # contract id is "contract:system_library:libgl-so-1".  We assert by kind to
    # avoid brittle id-string dependence.
    assert any(n.data.get("kind") == "system_library" for n in m.contract_graph.contracts())


def test_done_gate_does_not_satisfy_goal_on_collect_only():
    # collect-only (no "N passed") must NOT mark the goal satisfied even with done_flag=True
    led = _ledger([ActionEvent(step=1, cmd="python -m pytest --collect-only -q", rc=0,
                               stdout="collected 5 items", env_revision_before=0,
                               env_revision_after=0, mutation_class=None)])
    m = merge_map(_base(), done_flag=True)
    m = refresh_host_graph(m, led, snapshot=None, exec_readonly=None, current_revision=0)
    assert m.host_satisfied == frozenset() or GOAL_TESTS_PASS not in m.host_satisfied


def test_done_gate_certifies_goal_on_non_pytest_runner():
    """Non-pytest runners (python -m unittest) with 'Ran N tests' output MUST certify the goal.

    This is the RED test for BUG-6: the old code used ``'pytest' not in ev.cmd`` which
    silently rejected unittest/nose2 runs.  The fix replaces that substring check with
    detector.is_test_command() — the same authoritative classifier used by
    maintainer._verified_test_run_passed.
    """
    led = _ledger([ActionEvent(
        step=1,
        cmd="python -m unittest discover -s tests",
        rc=0,
        stdout=".....\nRan 5 tests in 0.050s\n\nOK",
        env_revision_before=0,
        env_revision_after=0,
        mutation_class=None,
    )])
    m = merge_map(_base(), done_flag=True)
    m = refresh_host_graph(m, led, snapshot=None, exec_readonly=None, current_revision=0)
    assert GOAL_TESTS_PASS in m.host_satisfied, (
        "GOAL_TESTS_PASS must be certified when a real unittest run ('Ran N tests') "
        "is in the ledger and done_flag=True; is_test_command() must recognise "
        "non-pytest runners"
    )


def test_done_gate_collect_only_still_rejected_after_bug6_fix():
    """Honesty guard: --collect-only must still be rejected even after BUG-6 generalisation.

    Ensures that widening the runner classifier does not accidentally allow a
    pytest --collect-only run to certify the goal.
    """
    led = _ledger([ActionEvent(
        step=1,
        cmd="pytest --collect-only -q",
        rc=0,
        stdout="collected 10 items\n",
        env_revision_before=0,
        env_revision_after=0,
        mutation_class=None,
    )])
    m = merge_map(_base(), done_flag=True)
    m = refresh_host_graph(m, led, snapshot=None, exec_readonly=None, current_revision=0)
    assert GOAL_TESTS_PASS not in m.host_satisfied, (
        "collect-only run must NOT certify the goal even after BUG-6 fix"
    )


# ---------------------------------------------------------------------------
# Blocker retirement / status reconciliation against fresh host evidence
# (fixes the frozen-blocker thrash: installed/imported dep or collect-only rc=0
#  must retire the corresponding blocker and advance the projected status).
# ---------------------------------------------------------------------------
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node, Edge
from src.envstate.contracts import ids


def _map_with_import_blocker(installed=(), import_results=()):
    contract = Node("contract:python_import:email-validator", "Contract",
                    {"level": "atomic", "kind": "python_import",
                     "subject": "email-validator", "layer": "deps"})
    blocker = Node("blocker:missing-email-validator", "Blocker",
                   {"signature": "ImportError: email-validator is not installed",
                    "kind": "module_not_found", "active": True,
                    "summary": "email-validator missing", "root_or_downstream": "root"})
    g = ContractGraph(
        nodes=(contract, blocker),
        edges=(Edge("contract:goal:repo_imports_work", "depends_on",
                    "contract:python_import:email-validator"),
               Edge("blocker:missing-email-validator", "violates",
                    "contract:python_import:email-validator")))
    return merge_map(_base(), contract_graph=g, installed=installed,
                     import_results=import_results)


def _active(m, bid):
    n = m.contract_graph.node(bid)
    return bool(n.data.get("active", True)) if n else None


def test_retires_import_blocker_when_dep_installed():
    m = _map_with_import_blocker(installed=(Fact("email-validator", "2.3.0"),))
    out = refresh_host_graph(m, _ledger([]), snapshot=None, exec_readonly=None, current_revision=0)
    assert _active(out, "blocker:missing-email-validator") is False
    assert "contract:python_import:email-validator" in out.host_satisfied


def test_retires_import_blocker_via_import_results_underscore():
    m = _map_with_import_blocker(import_results=(("email_validator", True),))
    out = refresh_host_graph(m, _ledger([]), snapshot=None, exec_readonly=None, current_revision=0)
    assert _active(out, "blocker:missing-email-validator") is False


def test_keeps_import_blocker_when_dep_absent():
    out = refresh_host_graph(_map_with_import_blocker(), _ledger([]), snapshot=None,
                             exec_readonly=None, current_revision=0)
    assert _active(out, "blocker:missing-email-validator") is True


def _map_with_collection_blocker():
    blocker = Node("blocker:collection-errors", "Blocker",
                   {"signature": "Interrupted: 8 errors during collection",
                    "kind": "test_collection_failure", "active": True,
                    "summary": "collection broken", "root_or_downstream": "root"})
    g = ContractGraph(nodes=(blocker,),
                      edges=(Edge("blocker:collection-errors", "violates",
                                  "contract:goal:repo_tests_collect"),))
    return merge_map(_base(), contract_graph=g)


def _collect_led(rc):
    return _ledger([ActionEvent(step=1, cmd="python -m pytest --collect-only -q", rc=rc,
                                stdout="collected 42 items", env_revision_before=0,
                                env_revision_after=0, mutation_class=None)])


def test_collection_pass_retires_collection_blocker_and_satisfies_collect():
    out = refresh_host_graph(_map_with_collection_blocker(), _collect_led(0), snapshot=None,
                             exec_readonly=None, current_revision=0)
    assert _active(out, "blocker:collection-errors") is False
    assert ids.goal_contract_id("repo_tests_collect") in out.host_satisfied


def test_collection_fail_keeps_collection_blocker():
    out = refresh_host_graph(_map_with_collection_blocker(), _collect_led(1), snapshot=None,
                             exec_readonly=None, current_revision=0)
    assert _active(out, "blocker:collection-errors") is True


def test_collection_pass_does_not_satisfy_repo_tests_pass():
    # honesty: collect-only success must NOT certify the real-execution goal
    out = refresh_host_graph(_map_with_collection_blocker(), _collect_led(0), snapshot=None,
                             exec_readonly=None, current_revision=0)
    assert GOAL_TESTS_PASS not in out.host_satisfied
