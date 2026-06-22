# DESIGN: Single-Session Planning Graph V1

**Status:** proposal / design draft
**Date:** 2026-06-20
**Scope:** repo environment construction agent

## 1. Core Idea

The agent should use one readable graph as its single-session working memory for
planning, debugging, and avoiding repeated mistakes during a run.

```text
WorldModelMap = factual environment state and raw evidence
PlanningGraph = current goal / issue / action / observation reasoning state
ExperienceNotes = global reusable lessons attached to the WorldModelMap
```

The PlanningGraph is not a full dependency graph and not a full log store. It
stores the distilled repair story for the current run:

```text
What goal is blocked?
What issue blocks it?
What observation supports that issue?
What action was tried?
Did the action resolve or fail the issue?
```

Global experience is kept as compact text notes attached to the WorldModelMap,
not as first-class graph nodes in V1. These notes can reference graph nodes and
raw evidence, and planner actions can cite the notes that influenced them.

## 2. Motivation

The current contract graph is precise but less intuitive for a reader and for a
planner prompt. It asks the agent to reason through obligations, blockers, and
attempts. This V1 reframes the same idea in a more direct agent-memory form:

```text
Observation = what was seen
Issue = what it means
Action = what the agent did
Goal = why it matters
```

This also handles an important runtime case: the BuildAgent may encounter a bug
and repair it within the same cycle. The final state may be clean, but the
Maintainer should still preserve the transient failure and repair path so the
Planner can avoid repeating mistakes later in the same run.

## 3. Graph Node Types

V1 uses four node types.

```text
Goal
Issue
Action
Observation
```

### 3.1 Goal

A `Goal` is something the environment setup must achieve.

Example goals:

```text
goal:repo_tests_pass
goal:repo_tests_collect
goal:repo_imports_work
goal:repo_deps_installed
goal:repo_services_ready
goal:repo_config_ready
```

Schema:

```json
{
  "id": "goal:repo_tests_pass",
  "type": "Goal",
  "kind": "repo_tests_pass",
  "title": "Repository tests pass",
  "status": "blocked",
  "required": true,
  "layer": "tests",
  "success_check": "python -m pytest -q",
  "description": "The real repository test suite executes successfully.",
  "source_refs": ["goal"],
  "evidence_refs": [],
  "created_cycle": 0,
  "updated_cycle": 3,
  "metadata": {}
}
```

Fields:

```text
id             stable graph id
type           always Goal
kind           repo_tests_pass | repo_tests_collect | repo_imports_work |
               repo_deps_installed | repo_services_ready | repo_config_ready |
               custom
title          short planner-readable name
status         unknown | blocked | satisfied
required       whether this is a hard success target
layer          deps | system | runtime | build | tests | config
success_check  command or probe that can certify the goal
description    planner-readable explanation
source_refs    why this goal exists
evidence_refs  evidence that currently supports status projection
created_cycle  first cycle where the node existed
updated_cycle  last cycle where status/metadata changed
metadata       optional structured extras
```

`Goal.status` should be projected by host evidence where possible. The
Maintainer may explain why a goal is blocked, but must not certify a goal as
satisfied.

### 3.2 Issue

An `Issue` is a normalized problem or hypothesis that blocks a goal or explains
another issue.

Examples:

```text
issue:cv2-import-fails
issue:missing-libgl-so-1
issue:poetry-lock-conflict
issue:redis-connection-refused
```

Schema:

```json
{
  "id": "issue:missing-libgl-so-1",
  "type": "Issue",
  "kind": "missing_system_library",
  "subject": "libGL.so.1",
  "layer": "system",
  "status": "active",
  "signature": "ImportError: libGL.so.1: cannot open shared object file",
  "summary": "cv2 import is blocked by a missing libGL runtime library.",
  "root_or_downstream": "root",
  "severity": "error",
  "confidence": 0.85,
  "evidence_refs": ["obs:cmd12:libgl-missing"],
  "first_seen_cycle": 2,
  "last_seen_cycle": 2,
  "metadata": {}
}
```

Fields:

```text
id                  stable graph id
type                always Issue
kind                module_not_found | missing_binary | missing_system_library |
                    version_conflict | build_failure | service_unreachable |
                    env_var_missing | test_collection_failure | unknown
subject             concrete package/import/binary/library/service/config key
layer               deps | system | runtime | build | tests | config
status              active | resolved | disproven | stale
signature           literal or normalized failure signature
summary             one-line explanation
root_or_downstream  root | downstream | unknown
severity            info | warning | error
confidence          confidence in the Maintainer interpretation, not in evidence
evidence_refs       Observation ids or world evidence refs supporting the issue
first_seen_cycle    first cycle where issue was created
last_seen_cycle     latest cycle where issue was observed or updated
metadata            optional structured extras
```

`Issue` is Maintainer-authored interpretation. It must cite at least one
Observation or world evidence ref.

### 3.3 Action

An `Action` is a command, probe, repair, or planned step.

Examples:

```text
action:pip-install-requirements
action:apt-install-libgl1
action:probe-import-cv2
action:start-redis
```

Schema:

```json
{
  "id": "action:apt-install-libgl1",
  "type": "Action",
  "kind": "system_install",
  "intent": "Install libGL runtime library for cv2 imports.",
  "proposed_by": "planner",
  "commands": ["apt-get update && apt-get install -y libgl1"],
  "outcome": "succeeded",
  "outcome_reason": "Command returned rc=0 and later cv2 import probe passed.",
  "target_node_ids": ["issue:missing-libgl-so-1"],
  "evidence_refs": ["obs:cmd13:apt-installed-libgl1", "obs:cmd14:cv2-import-passed"],
  "source_experience_note_ids": ["exp:opencv-libgl-runtime"],
  "cycle": 3,
  "metadata": {}
}
```

Fields:

```text
id                          stable graph id
type                        always Action
kind                        python_install | system_install | env_config |
                            service_start | build_fix | validation |
                            test_retry | inspect | other
intent                      why this action was chosen
proposed_by                 planner | build_agent | host | maintainer
commands                    concrete shell command(s), if executed
outcome                     pending | succeeded | failed | no_effect | partial
outcome_reason              short explanation of effect
target_node_ids             Goal/Issue ids the action was intended to affect
evidence_refs               Observation ids or world evidence refs showing result
source_experience_note_ids  global experience notes that influenced the action
cycle                       execution cycle
metadata                    optional structured extras
```

The host creates executed Action nodes and owns `outcome` whenever it can be
derived from command/probe evidence. The Maintainer must not fabricate command
execution.

### 3.4 Observation

An `Observation` is a compact runtime fact extracted from command output,
probes, or host state changes.

It is not full stdout/stderr. Full logs stay in the WorldModelMap or evidence
store. Observation nodes are small, planner-readable anchors.

Examples:

```text
obs:cmd12:libgl-missing
obs:cmd14:cv2-import-passed
obs:cmd17:poetry-solver-conflict
obs:cmd21:pytest-collected-tests
```

Schema:

```json
{
  "id": "obs:cmd12:libgl-missing",
  "type": "Observation",
  "kind": "error_signature",
  "text": "ImportError: libGL.so.1: cannot open shared object file",
  "source": "world.command:12",
  "source_action_id": "action:probe-import-cv2",
  "cycle": 2,
  "severity": "error",
  "status": "current",
  "extracted_subjects": ["cv2", "libGL.so.1"],
  "rc": 1,
  "confidence": 1.0,
  "evidence_refs": ["world.command:12"],
  "metadata": {}
}
```

Fields:

```text
id                  stable graph id
type                always Observation
kind                error_signature | command_exit | probe_pass | probe_fail |
                    package_installed | service_unreachable | test_failure |
                    test_pass | state_change
text                compact observed fact or literal signature
source              world evidence pointer
source_action_id    Action that produced this observation, if any
cycle               execution cycle
severity            info | warning | error
status              current | superseded | stale
extracted_subjects  concrete imports/packages/binaries/libs/services found in text
rc                  command return code when applicable
confidence          extractor confidence; host-created observations use 1.0
evidence_refs       raw evidence refs backing the observation
metadata            optional structured extras
```

Observations should be host-created or deterministic-extractor-created. The
Maintainer can interpret Observations, but should not invent raw Observations.

## 4. Graph Edge Types

V1 uses nine edge types.

```text
Goal requires Goal
Goal blocked_by Issue
Action produced Observation
Observation indicates Issue
Observation contradicts Issue
Issue caused_by Issue
Action addresses Issue
Action tests Issue
Action resolved Issue
Action failed Issue
```

### 4.1 Edge Rules

```text
requires      Goal        -> Goal
blocked_by    Goal        -> Issue
produced      Action      -> Observation
indicates     Observation -> Issue
contradicts   Observation -> Issue
caused_by     Issue       -> Issue
addresses     Action      -> Issue
tests         Action      -> Issue
resolved      Action      -> Issue
failed        Action      -> Issue
```

### 4.2 Edge Meaning

`requires`

```text
Goal A requires Goal B.
```

Used for the coarse planning scaffold:

```text
repo_tests_pass requires repo_imports_work
repo_tests_pass requires repo_deps_installed
repo_tests_collect requires repo_imports_work
```

`blocked_by`

```text
Goal is currently blocked by Issue.
```

This is the main planner-facing failure relation.

`produced`

```text
Action produced Observation.
```

Host-owned. This preserves what happened during a cycle even if the BuildAgent
later repairs the failure before the Maintainer runs.

`indicates`

```text
Observation indicates Issue.
```

Maintainer-owned interpretation.

`contradicts`

```text
Observation contradicts Issue.
```

Used when new evidence shows an issue is no longer active or a hypothesis is
wrong.

`caused_by`

```text
Issue A is caused by Issue B.
```

This replaces the overloaded `depends_on` causal use. It lets the planner target
the deeper/root issue.

`addresses`

```text
Action was intended to repair or handle Issue.
```

Usually host-created from planner `target_node_ids`.

`tests`

```text
Action probes whether Issue is present or resolved.
```

Used for validation commands, import probes, service checks, and test retries.

`resolved`

```text
Action resolved Issue.
```

Host-owned when directly supported by evidence, or host-approved after a
Maintainer proposal.

`failed`

```text
Action failed to resolve Issue.
```

Host-owned when command/probe evidence shows failure or no effect.

## 5. Ownership Model

Strict ownership keeps the graph grounded.

```text
Host / runtime owns:
- Action nodes for executed actions
- Observation nodes
- produced edges
- resolved / failed edges when directly verifiable
- Goal satisfaction projection
- raw evidence and command logs

Planner owns:
- proposed action intent through recipe patches
- target_node_ids that become addresses/tests edges after host validation

Maintainer owns:
- Issue nodes
- blocked_by edges
- indicates / contradicts edges
- caused_by edges
- issue classification and summaries
```

The Maintainer may not assert that an Action executed, that a Goal is satisfied,
or that an Issue is resolved without host evidence.

## 6. Runtime Flow

One execution cycle should update the graph like this:

```text
1. Host seeds or refreshes Goal scaffold.

2. Planner reads:
   - current WorldModelMap
   - active PlanningGraph subgraph
   - relevant ExperienceNotes

3. Planner emits a recipe patch with target Goal/Issue ids.

4. Host commits Action nodes before execution.

5. BuildAgent executes commands and may locally repair errors.

6. Host/deterministic extractors create Observation nodes from:
   - command exits
   - stderr/stdout signatures
   - probe passes/fails
   - install/service/test state changes

7. Host links:
   Action produced Observation

8. Maintainer reads the cycle transcript and graph, then adds:
   Observation indicates Issue
   Observation contradicts Issue
   Issue caused_by Issue
   Goal blocked_by Issue

9. Host projects statuses:
   - satisfied goals
   - active/resolved/stale issues
   - action outcomes

10. ExperienceNote retriever matches signals from:
    - WorldModelMap facts
    - active Issues
    - recent Observations
    - failed Actions
```

The planner should not receive every node. It should receive an active view:

```text
active required goals
blocking issues
root issue chains
top supporting observations per issue
actions tried per issue
relevant experience notes
```

## 7. Global Experience Notes

Global experience is attached to the WorldModelMap, not stored as graph nodes in
V1.

Purpose:

```text
Remember durable lessons the agent learned across runs or within long sessions.
Help the planner avoid known mistakes.
Suggest repair strategies when similar signals appear.
```

Example:

```json
{
  "id": "exp:poetry-lock-conflict",
  "scope": "global",
  "trigger_signals": [
    "poetry.lock present",
    "dependency solver conflict",
    "pillow mismatch"
  ],
  "lesson": "Preserve the Poetry lock graph. Prefer Poetry-native install before overriding packages with pip.",
  "avoid": [
    "deleting poetry.lock",
    "pip installing conflicting direct pins before trying poetry install"
  ],
  "recommended_actions": [
    "inspect pyproject.toml and poetry.lock",
    "poetry install --no-root",
    "poetry install --sync when supported"
  ],
  "applicability": {
    "ecosystem": "python",
    "package_manager": "poetry",
    "os_family": "any"
  },
  "confidence": "medium",
  "supporting_graph_refs": [
    "issue:poetry-lock-conflict",
    "action:poetry-install"
  ],
  "supporting_evidence_refs": [
    "world.command:17"
  ],
  "telemetry": {
    "hits": 63,
    "successes": 37,
    "failures": 15
  },
  "last_updated": "2026-06-20"
}
```

Fields:

```text
id                       stable note id
scope                    global | ecosystem | repo
trigger_signals          conditions that make the note relevant
lesson                   concise natural-language repair principle
avoid                    actions or strategies to avoid
recommended_actions      reusable action patterns
applicability            ecosystem/package-manager/os/runtime filters
confidence               low | medium | high
supporting_graph_refs    Goal/Issue/Action/Observation ids that produced the lesson
supporting_evidence_refs raw world evidence refs
telemetry                hits/successes/failures
last_updated             timestamp or logical run id
```

### 7.1 Attachment To WorldModelMap

The WorldModelMap should carry the active retrieved notes for the current run:

```json
{
  "experience_memory": {
    "retrieved_notes": [
      "exp:poetry-lock-conflict",
      "exp:opencv-libgl-runtime"
    ],
    "bindings": [
      {
        "note_id": "exp:opencv-libgl-runtime",
        "matched_node_ids": ["obs:cmd12:libgl-missing", "issue:missing-libgl-so-1"],
        "match_reason": "Recent observation mentions libGL.so.1 missing during cv2 import.",
        "cycle": 2
      }
    ]
  }
}
```

The full note store may live outside the WorldModelMap. The map only needs the
retrieved notes and bindings that are relevant to this run.

### 7.2 Relationship To The Graph

ExperienceNotes can reference graph nodes:

```text
ExperienceNote supporting_graph_refs -> Issue/Action/Observation ids
Action source_experience_note_ids -> ExperienceNote ids
```

But V1 should not make ExperienceNotes graph nodes. This keeps the PlanningGraph
focused on current-run reasoning.

## 8. Planner Prompt Shape

The planner should receive three sections:

```text
1. WorldModelMap Facts
   authoritative environment state

2. Active PlanningGraph
   goal -> issue -> observation/action slice

3. Relevant ExperienceNotes
   reusable lessons, avoid-list, recommended actions, telemetry
```

The planner should use them as:

```text
WorldModelMap tells what is true.
PlanningGraph tells what is blocking progress.
ExperienceNotes tell what has historically worked or failed.
```

If these conflict, the priority is:

```text
WorldModelMap evidence > PlanningGraph interpretation > ExperienceNote prior
```

## 9. Maintainer Prompt Shape

The Maintainer should be asked to produce only a graph patch over Issues and
semantic edges.

Allowed Maintainer operations:

```text
add_issue
add_edge: blocked_by
add_edge: indicates
add_edge: contradicts
add_edge: caused_by
update_issue_classification
update_issue_status_proposal
diagnostic_notes
```

Forbidden Maintainer operations:

```text
create executed Action
create raw Observation
mark Goal satisfied
mark Action succeeded/failed without host evidence
write raw world facts
rewrite command logs
```

## 10. Retrieval Views

The graph should expose planner-facing views rather than raw dumps.

Useful views:

```text
active_goal_path(goal_id)
blocking_issues(goal_id)
root_issue_chain(issue_id)
supporting_observations(issue_id, limit=2)
actions_tried_for(issue_id)
failed_actions_for(issue_id)
recent_observations(limit=5)
experience_notes_for_active_issues()
```

Default planner render:

```text
Goal: repo_tests_pass [blocked]
  blocked_by Issue: cv2 import fails
    indicated_by Observation: ModuleNotFoundError / ImportError snippet
    caused_by Issue: missing libGL.so.1
      actions tried:
        - pip install opencv-python [no_effect]
        - apt-get install libgl1 [pending/succeeded]
      relevant experience:
        - exp:opencv-libgl-runtime, success 37/52
```

## 11. Migration From Current Contract Graph

Current graph:

```text
Contract
Blocker
Attempt

Blocker violates Contract
Attempt addresses Contract
Contract depends_on Contract
```

PlanningGraph V1:

```text
Goal
Issue
Action
Observation

Goal requires Goal
Goal blocked_by Issue
Observation indicates Issue
Issue caused_by Issue
Action addresses/tests/resolved/failed Issue
Action produced Observation
```

Mapping:

```text
Contract(level=goal) -> Goal
Blocker              -> Issue
Attempt              -> Action
Contract(level=atomic) -> usually folded into Issue.subject for V1
violates             -> blocked_by + indicates
depends_on           -> requires for goals, caused_by for issues
addresses            -> addresses Issue
evidence_refs        -> Observation nodes + raw world evidence refs
```

The key schema improvement is splitting `depends_on` into two clearer concepts:

```text
requires  = planning/subgoal relation
caused_by = diagnosis/root-cause relation
```

## 12. Non-Goals

V1 should not:

```text
model every dependency as a graph node
store full stdout/stderr in Observation nodes
turn every command line into planner-visible memory
make ExperienceNotes graph nodes
allow the Maintainer to certify factual state
replace the WorldModelMap
```

## 13. Quality Constraints

To keep the graph useful:

```text
cap observations per action to salient extracted facts
hide unattached observations from planner render by default
require every Issue to cite at least one Observation or world evidence ref
prefer root issue chains over flat issue lists
project statuses from host evidence where possible
redact secrets before text enters any graph node or experience note
keep raw logs in WorldModelMap/evidence store
```

## 14. Decision Log

1. Use `Goal`, `Issue`, `Action`, and `Observation` as the V1 node set.
   - Alternative: keep `Contract`, `Blocker`, `Attempt`.
   - Reason: the new names are more intuitive for single-session planning and
     debugging.

2. Add Observation nodes.
   - Alternative: keep observations only as evidence refs.
   - Reason: BuildAgent may observe and repair failures inside one cycle; the
     graph should preserve that trajectory.

3. Keep raw evidence in WorldModelMap.
   - Alternative: store full logs directly in graph nodes.
   - Reason: the graph should remain readable and compact.

4. Store global experience as text notes attached to the WorldModelMap.
   - Alternative: make XPU/ExperienceNote a graph node.
   - Reason: V1 should keep the PlanningGraph focused on current-run reasoning
     while still allowing reusable lessons to influence the planner.

5. Split `depends_on` into `requires` and `caused_by`.
   - Alternative: keep one overloaded edge.
   - Reason: planning dependency and causal diagnosis are different relations.

## 15. Open Questions

1. Should `Observation contradicts Issue` directly mark an issue `resolved`, or
   should host projection perform the status update separately?
2. Should `Action addresses Issue` be host-only from planner targets, or may the
   Maintainer add missing `addresses` edges after reading logs?
3. What is the maximum number of Observation nodes allowed per cycle before the
   graph becomes too noisy?
4. Should ExperienceNotes be updated online during the run or only at run end?
