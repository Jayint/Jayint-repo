import json

from src.envstate.proof import trace_from_dict
from src.envstate.run_trace import (
    AgentActionRecord,
    CandidateCheckRecord,
    CandidateTransactionRecord,
    RunTracer,
)


def test_agent_actions_and_candidate_transactions_survive_json_roundtrip():
    tracer = RunTracer("demo", loop_mode="v3_graph_execute_agent")
    tracer.record_agent_action(AgentActionRecord(
        cycle=2,
        action_type="probe",
        target_node="pkg:demo",
        probe_command="python -m pip show demo",
        validated=True,
        evidence="demo 1.0",
    ))
    tracer.record_candidate_transaction(CandidateTransactionRecord(
        transaction_id="txn-2-1",
        cycle=2,
        base_checkpoint="exec-1-abc",
        base_prefix_len=1,
        validation_prefix_len=2,
        proposal={"add_providers": [{"id": "pip:demo"}]},
        executed_block_ids=("pip.demo",),
        checks=(CandidateCheckRecord(
            node_id="pkg:demo",
            command="python -m pip show demo",
            rc=0,
            output="demo 1.0",
        ),),
        status="committed",
        failed_block_id=None,
        failed_node_id=None,
        failure="",
        created_checkpoint="exec-2-def",
    ))
    trace = tracer.snapshot(stop_reason="planner_done", gates={})

    restored = trace_from_dict(json.loads(json.dumps(trace.to_dict())))

    assert restored == trace
    assert restored.candidate_transactions[0].status == "committed"
    assert restored.agent_actions[0].validated is True
