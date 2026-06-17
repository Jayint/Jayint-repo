# DESIGN: Contract Graph V1

> **Status:** implemented (June 2026). Arm `v1g` (A2) in `run_repo2run_benchmark.py` and `run_rat_benchmark.py`.
> **Companion:** `docs/DESIGN-environment-state-maintainer.md` §13 for the one-paragraph summary.
> **Implementation:** `src/envstate/contracts/` — 12 modules + `tests/test_contracts_*.py`.

---

## Goal

Add a planner-facing **contract graph** reasoning layer inside `WorldModelMap` for the `john-planner-v1` three-role env-construction agent, so failures, obligations, repairs, and evidence are **explicit, grounded, and auditable**.

**Architecture:** A new `src/envstate/contracts/` subpackage holds a generic JSON-serializable graph (`Node`/`Edge`/`ContractStatusEvent` + `ContractGraph` container), a domain-specific graph-patch model with strict validation, a **host-owned deterministic projection** (manifests, `probe_env`, the `ActionLedger`, and existing `open_problems` → graph nodes), a **host goal-contract template** keyed to the verification command, and a **validator registry** that auto-runs read-only checks. The Maintainer LLM contributes only *semantic* nodes (`Contract`/`Transition`/`Validator`), edges, and non-`satisfied` status events via a validated patch. The Planner reads the full graph each cycle, emits grounded `transition_proposal`s with `target_node_ids`, and may emit an **advisory `done`** that the existing host hard gate plus graph readiness must confirm. The existing `done_flag` anti-gaming gate and `agent.py::_resolve_v1_verified_test_run` final gate remain authoritative.

**Tech Stack:** Python 3.12, frozen `dataclasses`, `pytest`. No new third-party deps. JSON via stdlib; LLM I/O reuses `complete_with_retry` / `extract_json_object`.

---

## Design Decisions (locked)

These were decided with the spec author and constrain every task:

1. **Scope:** Full spec, one phased plan (Phases 0–6).
2. **Completion:** Advisory planner `done` (spec §11/§12). The planner `done` is a *request*; the host `done_flag` gate (`_verified_test_run_passed`) AND graph readiness (required `GoalContract`s `satisfied` with `CommandExecution` evidence) must both hold, and `agent.py::_resolve_v1_verified_test_run` remains the final authority. The planner `done` only changes *when the loop stops early*, never *whether success is claimed*.
3. **Graph population:** **Host projects facts; Maintainer adds semantics.** Host deterministically builds `RepoArtifact`, `Requirement`, `Capability`, `Failure`, `OpenProblem`, `CommandExecution`, `EnvironmentRevision`, `VerificationTarget`, and `GoalContract` (template) nodes. The Maintainer patch may add only `Contract` (atomic), `Transition`, `Validator` nodes, allowed edges, and status events that are **not** `satisfied` (a `satisfied` event must cite a passing `CommandExecution`/confirmed `Validator`).
4. **Goal nodes:** Host template keyed to `VERIFY_TEST_CMD` (`python -m pytest -q`): `contract:goal:repo_tests_run` `depends_on` `contract:pytest_runnable` plus one `contract:python_package_importable:<dep>` per declared dependency.

**Two host-vs-Maintainer ownership deviations from the literal spec (intentional, grounded):**
- **Transitions are committed host-side** from the planner's structured `transition_proposal` (deterministic normalization), not by the Maintainer LLM. Spec §9 says "Maintainer commits"; we move it to the host because the proposal is already structured and the `executed_as` edge is derived from the host-owned ledger. This keeps transitions grounded and removes an LLM round-trip.
- **Capabilities are host-created only** (from `installed`/`system_installed`/probe + a passing `CommandExecution`). The Maintainer patch may never create a `Capability`.

---

## Per-Cycle Ordering

The contract the orchestrator enforces each cycle:

1. `apply_deterministic(map, probe(), manifest)` — existing fact fold (unchanged).
2. **Host graph refresh** — `refresh_host_graph(map, ledger, snapshot, exec_readonly, env_revision)` projects host nodes, seeds the goal template, runs confirmed validators, marks deterministic `satisfied`/`violated` status.
3. **Planner** reads `map` (graph included) → `task` (with `target_node_ids` + `transition_proposal`), `giveup`, or advisory `done`.
4. On `task`: host **commits the Transition** node + `targets`/`repaired_by` edges before dispatch.
5. **BuildAgent** runs the task; host links `Transition --executed_as--> CommandExecution` for the commands produced.
6. **Maintainer** updates `open_problems`/`notes`/`done_flag` (unchanged) **and** applies a validated semantic graph patch.
7. Completion check: `done_flag` (existing authoritative gate) OR advisory-`done` confirmed by `done_flag` AND graph readiness.

---

## File Structure

### New subpackage `src/envstate/contracts/`

| File | Responsibility |
|---|---|
| `__init__.py` | Public exports. |
| `schema.py` | Enums (`NodeType`, `EdgeType`, `ContractStatus`, `ValidationState`, `ContractLevel`), the closed `EDGE_RULES` validity table, `HOST_OWNED_NODE_TYPES` / `MAINTAINER_NODE_TYPES`, and `redact_secrets`. |
| `nodes.py` | `Node`, `Edge`, `ContractStatusEvent` frozen dataclasses + dict (de)serialization. |
| `graph.py` | `ContractGraph` frozen container + immutable query helpers + `to_dict`/`from_dict`. |
| `ids.py` | Deterministic ID builders + slugify. |
| `patch.py` | `GraphPatch` dataclass + `parse_graph_patch(dict)`. |
| `validation.py` | `validate_patch(graph, patch, *, scope)` enforcing all §10 invariants. |
| `apply.py` | `apply_patch(graph, patch) -> ContractGraph` (assumes validated). |
| `projection.py` | Host projection functions + `refresh_host_graph(...)`. |
| `goals.py` | Goal-contract template keyed to verification target + `evaluate_goal_readiness`. |
| `validators.py` | Validator registry + `run_confirmed_validators(graph, exec_readonly, revision_id)`. |
| `render.py` | `render_graph_for_planner(graph)` (markdown) + `serialize_graph_for_maintainer(graph)` (dict). |

### Modified existing files

| File | Change |
|---|---|
| `src/envstate/world_model.py` | Add `contract_graph` to `WorldModelMap`; thread through `initial_map`, `merge_map`, `map_to_dict`, `map_from_dict`. Add `target_node_ids` + `transition_proposal` to `Task`; add `satisfied_goal_contract_ids` to `PlannerDecision`; add `TransitionProposal` dataclass. |
| `src/envstate/planner.py` | Render graph in `render_planning_view`; extend `PLANNER_SYSTEM_PROMPT`; add `done` to `_VALID_ACTIONS`; parse/validate `target_node_ids`/`transition_proposal`/`satisfied_goal_contract_ids`. |
| `src/envstate/build_agent.py` | Pass `target_node_ids`/`transition_proposal` into `_build_task_message`; extract module-level `make_action_event(...)`. |
| `src/envstate/maintainer.py` | Extend `MAINTAINER_SYSTEM_PROMPT` to emit `graph_patch`; serialize graph into user message; parse+validate (Maintainer scope)+apply patch in `parse_v1_maintainer_reply`. |
| `src/envstate/orchestrator.py` | Per-cycle host graph refresh + transition commit + `executed_as` link; advisory `done` handling + readiness gate; new `exec_readonly` + `enable_contract_graph` params. |
| `src/envstate/ledger.py` | Add module-level `make_action_event(...)` factory. |
| `agent.py` | `enable_contract_graph` flag; thread `exec_readonly`, initial graph, and graph telemetry into `_run_v1`; graph readiness into finalize. |
| `run_repo2run_benchmark.py` | New `v1g` arm preset. |
| `run_rat_benchmark.py` + `multi_docker_eval_adapter.py` | `DOCKERAGENT_ENABLE_CONTRACT_GRAPH` env bridge. |

---

## Grounding Contract (who may write what)

```
HOST (deterministic, no LLM)                MAINTAINER (LLM, validated patch)
  RepoArtifact, Requirement                   Contract (atomic)
  Capability, CommandExecution                Validator
  EnvironmentRevision, Failure, OpenProblem   edges: violates/depends_on/implies_contract/
  GoalContract template + VerificationTarget         verified_by/blocks
  Transition (from planner proposal)          status: unknown/violated/repair_attempted
  satisfied/violated status (via validators)  (NEVER satisfied; NEVER host-owned nodes)
```

Every Maintainer patch passes `validate_patch(scope="maintainer")`; on any violation the patch is dropped and the flat fields still apply — so the worst case is "graph didn't grow this cycle," never a regression of the existing run.

---

## Why This Is Safe

The thing that decides *success* — `done_flag` (set by `_verified_test_run_passed`) and the post-loop `_resolve_v1_verified_test_run` re-run — is untouched. The graph can make the planner *act smarter* and can *gate the planner's advisory `done` early-stop*, but it can never assert a passing build the host didn't verify.

**Authority order (unchanged):** `_resolve_v1_verified_test_run` > `done_flag` > graph readiness.

---

## Node Type Ownership

**Host-owned (factual):** `RepoArtifact`, `Requirement`, `Capability`, `Failure`, `OpenProblem`, `CommandExecution`, `EnvironmentRevision`, `VerificationTarget`.

**Maintainer-owned (semantic):** `Contract`, `Transition`, `Validator`.

These sets are disjoint by construction (enforced in `schema.HOST_OWNED_NODE_TYPES` and `schema.MAINTAINER_NODE_TYPES`).

---

## The Torch Example (graph trace)

```
 artifact:requirements.txt --declares--> requirement:python_dependency:torch
                                                    |
                                          implies_contract
                                                    v
   failure:cmd:007 --violates--> contract:python_package_importable:torch <--depends_on-- contract:goal:repo_tests_run
        |                              ^        ^                                                   ^
   observed_in                  repaired_by  verified_by                                     depends_on
        v                              |        |                                                   |
   cmd:007 (rc=1)            transition:install_python_package:torch          contract:pytest_runnable
                                       |                                                   ^
                                  executed_as                                        verified_by
                                       v                                                   |
                              cmd:008 (pip install, rc=0)                         validator:pytest_collect_check
                                       |
                                creates_revision
                                       v
                              envrev:004  --►  capability:python_package_importable:torch@envrev:004
                                                          |
                                                    satisfied_by
                                                          v
                                       contract:python_package_importable:torch  (status: satisfied)
```

---

## Ablation Arms

| Arm | Flag | What it enables |
|---|---|---|
| A0 `arm0` | (default) | Original single-role ReAct DockerAgent |
| A1 `v1` | `--arm v1` | Three-role loop (Planner/BuildAgent/Maintainer), no graph |
| A2 `v1g` | `--arm v1g` | Three-role loop + contract graph (`enable_contract_graph=True`) |

A1 and A2 share identical code paths except for graph construction and rendering — any A2−A1 delta is attributable to the contract graph alone.

---

## Telemetry

Each cycle writes a JSON line to `setup_logs/contract_graph.jsonl`. Offline metrics (node counts, satisfied/violated/unknown distributions, goal readiness trajectory) are computed by `scripts/contract_graph_metrics.py`.
