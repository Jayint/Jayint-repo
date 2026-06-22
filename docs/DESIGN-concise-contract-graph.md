# DESIGN: Concise Contract Graph for Environment Construction

> **Status:** proposed next design direction.
> **Relationship to current `v1g`:** this is a smaller planner-facing graph
> design. It does not replace the existing implemented contract graph doc; it
> clarifies the next target architecture.

---

## 1. Core Idea

The system should separate factual environment memory from planner-facing
reasoning.

```text
WorldModelMap = what is true / what happened
ContractGraph = what matters / what should be fixed next
```

The `WorldModelMap` is host-owned deterministic environment state. It stores
repo layout, manifest dependencies, installed packages, system facts, command
history, outputs, environment revisions, progress, and verified facts.

The `ContractGraph` is not a mirror of all that state. It is a compact
fault-localization and planning graph over the world model. It should tell the
planner:

```text
What does success depend on?
What is currently blocking it?
What has already been tried?
What should the next recipe patch target?
```

---

## 2. Philosophical Meaning

A dependency graph asks:

```text
What is connected to what?
```

A checklist asks:

```text
What is done or not done?
```

A contract graph asks:

```text
What obligation is violated, why does it matter, and what repair should target it?
```

The graph should model the causal repair story, not the whole environment.

```text
goal -> required obligations -> violated obligation <- blocker
                                      ^
                                      |
                                  attempt
```

The best mental model is:

```text
Static success model + dynamic runtime overlay.
```

The static part is what the goal depends on. The dynamic overlay is what is
blocked and what the agent has tried.

This is the key to avoiding a purely local/reactive graph. The graph must start
with a coarse success scaffold, then let runtime evidence fill in specific
faults.

```text
coarse global scaffold + local runtime discoveries = global repair planning
```

---

## 3. Responsibilities

### WorldModelMap

`WorldModelMap` is authoritative for deterministic facts. The Maintainer does
not maintain it.

It should store:

- repo files and layout
- manifest-declared dependencies
- detected imports and dependency metadata
- installed Python packages and system packages
- package manager and build-system facts
- commands executed
- stdout/stderr snippets
- exit codes
- environment revisions
- final verification result
- broad progress flags

If a fact is inventory or provenance, it belongs in the world model.

It should not store:

- open problems
- LLM notes
- hypotheses
- root/downstream labels
- semantic failure interpretation
- planner-facing repair advice

Those belong in the contract graph.

### ContractGraph

`ContractGraph` is the planner-helper.

It should store:

- obligations that matter to running the repo
- current blockers that violate obligations
- attempts that addressed obligations
- dependency relationships between obligations
- evidence references back into `WorldModelMap`

If a fact explains what to fix next, it belongs in the contract graph.

The old `WorldModelMap.open_problems` field should be conceptually replaced by
`Blocker` nodes. During migration it can remain as a compatibility view, but it
should be derived from active blockers rather than independently maintained.

### Planning Scaffold

The `Jayint-Planer` branch's typed task graph is best understood as a
cold-start scaffold for this design. It has nodes such as:

```text
runtime
package_manager
install_strategy
language_dependency
project_build
service
env_var
verification
```

and ordering edges such as:

```text
requires_runtime
uses_package_manager
dependency_before_build
build_before_verify
service_required_by
env_required_by
```

That graph answers:

```text
What setup phases must happen, and in what order?
```

The concise contract graph answers:

```text
What obligations must hold, what is violated, and what repair should target it?
```

They are compatible. A Jayint-Planer-style typed task graph can seed the initial
contract backbone:

```text
runtime                  -> contract:runtime_compatible
package_manager          -> contract:package_manager_available
language_dependency      -> contract:goal:repo_deps_installed
project_build            -> contract:goal:repo_build_ready
service                  -> contract:goal:repo_services_ready or contract:service:<name>_reachable
env_var                  -> contract:goal:repo_config_ready or contract:env_var:<name>_configured
verification             -> contract:goal:repo_tests_pass
hard ordering edge       -> depends_on edge
```

So the existing planning graph is the static execution-order skeleton. The
concise contract graph is the obligation/fault/repair overlay.

---

## 4. Concise Schema

Use three node types.

### Contract

A `Contract` is an operational obligation: something that must become true for
the repo to install, import, build, test, or run.

Minimum fields:

```json
{
  "id": "contract:python_import:cv2",
  "type": "Contract",
  "level": "atomic",
  "kind": "python_import",
  "subject": "cv2",
  "layer": "deps",
  "status": "violated",
  "check": "python -c \"import cv2\"",
  "source_refs": ["world.required:opencv-python"],
  "evidence_refs": ["world.command:17"],
  "description": "The repo needs cv2 importable in the active Python environment."
}
```

Schema:

```text
id              stable unique id
type            always "Contract"
level           goal | atomic
kind            python_import | python_package_installable | system_library | binary |
                service | env_var | build_command | test_command | verification
subject         cv2, libGL.so.1, pg_config, redis, DATABASE_URL, pytest
layer           deps | system | runtime | build | tests | config
check           how the host can verify the obligation
source_refs     why this contract exists
evidence_refs   world-model evidence used to render current status
description     planner-readable explanation
metadata        optional structured extras
```

`status` may be rendered on a contract, but it should be recomputed from
evidence each cycle rather than treated as independent truth. See
["Status Computation"](#10-status-computation).

Example contract kinds:

```text
python_import
python_package_installable
system_library
binary
service
env_var
build_command
test_command
verification
```

### Blocker

A `Blocker` is a normalized runtime symptom or unresolved issue.

```json
{
  "id": "blocker:missing-libgl",
  "type": "Blocker",
  "signature": "ImportError: libGL.so.1: cannot open shared object file",
  "kind": "missing_system_library",
  "layer": "system",
  "root_or_downstream": "root",
  "summary": "cv2 import fails because the native libGL runtime is missing.",
  "evidence_refs": ["world.command:18"],
  "active": true,
  "metadata": {
    "extracted_subject": "libGL.so.1"
  }
}
```

Schema:

```text
id                  stable unique id
type                always "Blocker"
signature           literal or normalized failure signature
kind                module_not_found | missing_binary | missing_system_library |
                    version_conflict | build_failure | service_unreachable |
                    env_var_missing | test_collection_failure | unknown
layer               deps | system | runtime | build | tests | config
root_or_downstream  root | downstream | unknown
summary             short diagnosis
evidence_refs       world commands/probes that show this blocker
active              whether it still appears relevant
metadata            optional parsed details
```

### Attempt

An `Attempt` is a repair action tried or proposed.

```json
{
  "id": "attempt:install-libgl1",
  "type": "Attempt",
  "intent": "Install the native runtime library needed by cv2.",
  "kind": "system_install",
  "proposed_by": "planner",
  "commands": ["apt-get update && apt-get install -y libgl1"],
  "outcome": "pending",
  "outcome_reason": "",
  "evidence_refs": [],
  "created_from_target_node_ids": ["contract:system_library:libGL.so.1"],
  "metadata": {}
}
```

Schema:

```text
id                            stable unique id
type                          always "Attempt"
intent                        what the repair is trying to do
kind                          python_install | system_install | env_config |
                              service_start | build_fix | validation | test_retry |
                              inspect | other
proposed_by                   planner | build_agent | host | maintainer
commands                      commands proposed/executed
outcome                       pending | ok | failed | ok_but_still_blocked
outcome_reason                short planner-facing explanation
evidence_refs                 world commands/probes showing what happened
created_from_target_node_ids  graph nodes the planner/build-agent targeted
metadata                      optional structured extras
```

Use `outcome`, not a binary `failed`/`success` field. Environment repair needs
more than two states:

```text
pending:
  proposed but not executed yet

ok:
  executed and the target contract became satisfied

failed:
  command failed or the repair could not complete

ok_but_still_blocked:
  command succeeded, but the target contract or a dependent contract is still violated
```

Example:

```json
{
  "id": "attempt:install-opencv-python",
  "type": "Attempt",
  "intent": "Install opencv-python so cv2 can import.",
  "kind": "python_install",
  "proposed_by": "planner",
  "commands": ["python -m pip install opencv-python"],
  "outcome": "ok_but_still_blocked",
  "outcome_reason": "Install succeeded, but cv2 import still fails because libGL.so.1 is missing.",
  "evidence_refs": ["world.command:12", "world.command:13"],
  "created_from_target_node_ids": ["contract:python_import:cv2"]
}
```

Command-level truth remains in `WorldModelMap`; `Attempt.outcome` is the
planner-facing interpretation of whether the repair helped.

Use three edge types.

```text
Blocker violates Contract
Attempt addresses Contract
Contract depends_on Contract
```

This is deliberately smaller than the current full graph. It collapses:

```text
RepoArtifact       -> Contract.source_refs
Requirement        -> WorldModelMap.required + Contract.source_refs
Validator          -> Contract.check
VerificationTarget -> goal Contract
Capability         -> Contract.status + evidence_refs
Failure            -> Blocker
OpenProblem        -> Blocker
Transition         -> Attempt
CommandExecution   -> WorldModelMap command evidence
EnvironmentRevision -> WorldModelMap revision evidence
```

---

## 5. Goal Backbone And Runtime Overlay

The graph should model both:

```text
1. What the goal depends on to succeed.
2. What is currently blocked and what has been tried.
```

The `depends_on` edges are the graph backbone.

```text
contract:goal:repo_tests_pass
  depends_on -> contract:goal:repo_tests_collect
  depends_on -> contract:goal:repo_imports_work
  depends_on -> contract:goal:repo_deps_installed
  depends_on -> contract:goal:repo_build_ready
  depends_on -> contract:goal:repo_services_ready
  depends_on -> contract:goal:repo_config_ready
```

A minimally useful cold-start backbone should include:

```text
contract:goal:repo_tests_pass
  depends_on -> contract:goal:repo_tests_collect
  depends_on -> contract:goal:repo_imports_work
  depends_on -> contract:goal:repo_deps_installed
  depends_on -> contract:goal:repo_build_ready
  depends_on -> contract:goal:repo_services_ready
  depends_on -> contract:goal:repo_config_ready

contract:goal:repo_deps_installed
  depends_on -> contract:package_manager_available
  depends_on -> contract:python_version_compatible

contract:goal:repo_imports_work
  depends_on -> contract:goal:repo_deps_installed

contract:goal:repo_tests_collect
  depends_on -> contract:goal:repo_imports_work
  depends_on -> contract:test_runner_available
```

The runtime overlay attaches blockers and attempts.

```text
blocker:missing-libgl
  violates -> contract:system_library:libGL.so.1

attempt:pip-install-opencv
  addresses -> contract:python_import:cv2
```

Together, this localizes faults:

```text
repo_tests_pass
  depends_on -> repo_imports_work
    depends_on -> cv2_importable
      depends_on -> libGL_present
        violated_by <- missing-libGL
```

The planner can infer:

```text
Do not keep reinstalling opencv-python.
The root blocker is libGL.
Patch the build script with libgl1, validate cv2 import, then retry tests.
```

Cold start does not need to be fully complete. It needs to be coarsely complete:
goal and phase contracts should exist, but individual package/import/system
contracts should be promoted only when they become relevant.

---

## 6. Contract Formation

Contracts should be promoted lazily. Do not create a contract for every declared
dependency.

Create contracts when one of these is true:

1. The obligation is a top-level goal.
2. A runtime failure directly names the obligation.
3. The obligation is needed to explain another violated contract.
4. A repair attempt needs a target.
5. A dependency is central to install/import/build/test success.

Do not create contracts for:

- every package in `requirements.txt`
- every installed package
- every transitive dependency
- every repo file
- every successful command

Rule of thumb:

```text
If the fix is "run the normal bulk install", keep it in WorldModelMap.
If the fix requires special reasoning, promote it to a Contract.
```

Example:

```text
WorldModelMap.required:
  opencv-python, torch, pandas, numpy, ...

ContractGraph:
  contract:goal:repo_deps_installed
  contract:goal:repo_imports_work
  contract:python_import:cv2       # promoted after failure
  contract:system_library:libGL.so.1 # promoted after native import failure
```

---

## 7. Cold-Start Process

Initialize in layers, from cheapest deterministic evidence to more expensive
semantic interpretation.

Do not start by creating one graph node per AST import. Start with manifests and
executable probes, then use AST/import scanning as a targeted supplement.

The cold-start target is a stable success scaffold, not a complete dependency
mirror.

```text
Cold start gives the major roads.
Runtime discovers the traffic jams and side streets.
```

### Step 1: Repo Artifact Scan

Collect file-level facts:

```text
requirements.txt
pyproject.toml
setup.py
setup.cfg
package.json
poetry.lock
environment.yml
Dockerfile
Makefile
README
tests/
src/
```

This initializes:

```text
WorldModelMap.repo_layout
WorldModelMap.artifact_refs
```

### Step 2: Manifest Dependency Extraction

Parse manifests deterministically:

```text
requirements.txt -> declared Python deps
pyproject.toml -> build backend, project deps, optional test deps
setup.py/setup.cfg -> install_requires if parseable
package.json -> npm deps/scripts
Cargo.toml -> cargo deps
go.mod -> modules
```

This populates:

```text
WorldModelMap.required
WorldModelMap.build_system
WorldModelMap.dependency_state.declared
```

### Step 3: Runtime And Build Detection

Detect:

```text
language
Python version constraints
package manager
test framework
build commands
system hints
```

This gives the planner initial factual context before any graph reasoning.

### Step 4: Initial World Model Dependency State

Store deterministic dependency information in `WorldModelMap`, not in the
contract graph.

Useful fields:

```text
dependency_state.declared
dependency_state.detected_imports
dependency_state.resolved_edges
dependency_state.version_constraints
dependency_state.package_manager
dependency_state.test_framework
```

Resolved dependency edges should come from tools when available:

```text
pip inspect
pipdeptree
pip check
uv pip tree
npm ls
cargo metadata
poetry show --tree
```

Do not dump the full dependency graph into the planner prompt. Expose only the
relevant slice through promoted contracts.

### Step 5: Seed High-Level Goal Contracts

Create broad goal contracts first:

```text
contract:goal:repo_deps_installed
contract:goal:repo_imports_work
contract:goal:repo_tests_collect
contract:goal:repo_tests_pass
contract:goal:repo_build_ready
contract:goal:repo_services_ready
contract:goal:repo_config_ready
```

Optionally seed a few foundational atomic contracts:

```text
contract:python_version_compatible
contract:package_manager_available
contract:test_runner_available
contract:project_installable
```

Do not create one contract per dependency at cold start.

Initial graph:

```json
{
  "contracts": [
    {
      "id": "contract:goal:repo_deps_installed",
      "type": "Contract",
      "level": "goal",
      "kind": "dependency_install",
      "status": "unknown",
      "check": "python -m pip install -r requirements.txt"
    },
    {
      "id": "contract:goal:repo_tests_collect",
      "type": "Contract",
      "level": "goal",
      "kind": "verification",
      "status": "unknown",
      "check": "python -m pytest --collect-only"
    }
  ],
  "blockers": [],
  "attempts": [],
  "edges": []
}
```

### Step 6: Optional AST / Import Scan

Use AST/import scanning as a supplement.

For Python:

```text
parse .py files with ast
collect top-level imports
map import names to package candidates
ignore stdlib/local modules
rank by occurrence and test-path usage
```

Store results in:

```text
WorldModelMap.dependency_state.detected_imports
```

Promote only:

- imports used by tests or entrypoints
- imports missing during probes
- imports with non-obvious package mapping
- central/high-frequency imports

Examples:

```text
cv2
torch
yaml
sklearn
pandas
redis
django
```

### Step 7: Initial Install / Probe Attempt

The first real action should usually exercise the normal install/test path.

Examples:

```text
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pytest --collect-only
```

Runtime evidence is more valuable than speculative AST contracts. Let the real
package manager and test runner reveal what actually breaks.

### Step 8: Promote Relevant Atomic Contracts

After failures, promote specific contracts:

```text
ModuleNotFoundError: yaml
  -> contract:python_import:yaml

pg_config executable not found
  -> contract:binary:pg_config

ImportError: libGL.so.1
  -> contract:system_library:libGL.so.1

ConnectionRefusedError: Redis
  -> contract:service:redis_reachable
```

Then connect them:

```text
blocker:module-not-found-cv2
  violates -> contract:python_import:cv2

contract:python_import:cv2
  depends_on -> contract:system_library:libGL.so.1
```

Cold-start principle:

```text
Use static analysis to prepare.
Use execution to discover blockers.
Use contracts to promote blockers into planner targets.
```

---

## 8. Planning And Runtime Lifecycle

The graph is a planning overlay refreshed around every execution cycle. It is
not an independent memory the Maintainer freely edits.

```text
WorldModelMap refresh
  -> ContractGraph refresh
  -> Planner chooses RecipePatch
  -> BuildAgent executes + locally repairs
  -> WorldModelMap records evidence
  -> Maintainer updates graph semantics
  -> repeat
```

### Planning Time

The planner receives two distinct sections.

```text
1. Deterministic World State
   authoritative facts: installed, required, env, commands, failures, progress

2. Concise Contract Graph
   planner map: contracts, blockers, attempts, depends_on / violates / addresses
```

The planner uses the graph to:

```text
start at goal contracts
find unknown/violated dependencies
find blockers attached to those contracts
check attempts already made
group unresolved contracts by layer
emit a RecipePatch targeting contract IDs
```

The planner should be a global recipe designer, not a one-command-at-a-time
executor.

### Runtime

The BuildAgent receives the recipe patch and executes it. Its job is local
repair inside scope:

```text
command syntax issue
missing compiler
wheel build error
package-name mismatch
missing header
wrong install order
```

The BuildAgent may adapt locally, but it should not silently redesign the whole
environment strategy. It reports actual commands and outputs back.

Runtime evidence first lands in `WorldModelMap`:

```text
cmd
rc
output
env revision
installed facts
open problems
```

Then the graph is updated from that evidence.

---

## 9. Runtime Update Flow

Runtime errors update the graph through classification and linking.

```text
Command result
  -> raw evidence in WorldModelMap
  -> Blocker extraction
  -> Contract match/create
  -> graph links/status update
  -> planner target selection
```

Matching order:

1. Planner target context.
   If the failed recipe step targeted `contract:python_import:cv2`, attach
   follow-on blockers near that contract.
2. Deterministic error signatures.
   `ModuleNotFoundError: X` maps to `contract:python_import:X`.
   `command not found: X` maps to `contract:binary:X`.
   `lib*.so` import errors map to `contract:system_library:*`.
3. Existing graph context.
   Prefer existing contracts with matching subject/layer.
4. World model dependency state.
   Use declared dependencies, import mappings, resolved edges, and command
   context.
5. LLM Maintainer interpretation.
   Use only when deterministic matching is ambiguous.

Example:

```text
Failed command:
  python -m pip install psycopg2

Output:
  pg_config executable not found
```

Graph update:

```text
blocker:pg-config-missing
  violates -> contract:binary:pg_config

contract:python_package_installable:psycopg2
  depends_on -> contract:binary:pg_config

attempt:pip-install-psycopg2
  addresses -> contract:python_package_installable:psycopg2
```

Fault localization result:

```text
Do not keep retrying pip install psycopg2.
The root repair target is pg_config.
Patch system dependencies first.
```

Another example:

```text
Planner patch targets:
  contract:python_import:cv2

BuildAgent runs:
  python -c "import cv2"

Runtime result:
  ImportError: libGL.so.1

WorldModelMap records:
  command rc=1, output snippet

Maintainer adds:
  blocker:missing-libGL
  contract:system_library:libGL.so.1
  blocker:missing-libGL violates contract:system_library:libGL.so.1
  contract:python_import:cv2 depends_on contract:system_library:libGL.so.1

Next planner sees:
  root target is libGL, not another cv2 package install.
```

---

## 10. Maintainer Responsibilities

The Maintainer should be a semantic graph patcher, not the owner of truth and
not a `WorldModelMap` writer.

The Maintainer's only durable output should be a `ContractGraph` patch.

Host owns:

```text
command evidence
installed facts
environment facts
final verification
contract satisfaction when a check passes
attempt command outcome
```

Maintainer owns:

```text
Blocker interpretation
Blocker -> violates -> Contract
Contract -> depends_on -> Contract
Attempt -> addresses -> Contract
root vs downstream classification
contract descriptions/check suggestions
```

The Maintainer patch should be constrained to semantic updates:

```json
{
  "add_contracts": [],
  "add_blockers": [],
  "add_edges": [],
  "update_blocker_classification": [],
  "update_contract_description": []
}
```

The Maintainer must not assert:

```text
this package is installed
this contract is satisfied
final verification passed
this command succeeded
world-model open problems or notes
```

Those are host-owned facts.

The validator should reject graph patches that:

- reference nonexistent evidence
- reference nonexistent nodes
- mark a contract satisfied
- rewrite command outcomes
- create broad inventory mirrors
- attach local blockers without placing them under the goal backbone when a
  reasonable parent exists

Design principle:

```text
WorldModelMap records reality.
Maintainer explains reality.
ContractGraph focuses planning.
Planner changes the recipe.
BuildAgent handles local execution.
```

### Maintainer Prompt Structure

The Maintainer prompt should be forensic and conservative.

System philosophy:

```text
You are the ContractGraph Maintainer.
You do not maintain WorldModelMap.
You do not plan the next recipe patch.
You interpret host-owned evidence into semantic graph patches.
WorldModelMap is authoritative.
Do not certify installed packages, command success, or final verification.
Do not mark contracts satisfied.
Prefer attaching new blockers to the planner target_node_ids that produced them.
Every local blocker should be placed under the global goal backbone when possible.
Do not create inventory mirrors.
```

Input sections:

```text
# Authoritative World Evidence
- current world facts relevant to this cycle
- latest commands, rc, output snippets
- installed/system/env deltas
- final verification facts, if any

# Current ContractGraph
- active contracts
- active blockers
- recent attempts
- depends_on / violates / addresses edges

# Attempt Context
- planner target_node_ids
- recipe steps that just ran
- commands produced by BuildAgent

# Required Output
- graph patch only
```

Output shape:

```json
{
  "add_contracts": [],
  "add_blockers": [],
  "add_attempts": [],
  "add_edges": [],
  "update_blocker_classification": [],
  "update_contract_description": [],
  "diagnostic_notes": []
}
```

Maintainer reasoning posture:

```text
evidence -> blocker -> violated contract -> parent contract
```

Example:

```text
Input:
  target_node_ids: [contract:python_import:cv2]
  command: python -c "import cv2"
  output: ImportError: libGL.so.1

Output:
  add blocker:missing-libGL
  add contract:system_library:libGL.so.1
  add edge blocker:missing-libGL violates contract:system_library:libGL.so.1
  add edge contract:python_import:cv2 depends_on contract:system_library:libGL.so.1
```

---

## 11. Status Computation

Contract status should mostly be a projection over current evidence, not an
independent mutable fact.

Rendered statuses:

```text
satisfied = host check passed or final goal passed
violated = active blocker violates the contract
unknown = no current proof either way
```

The Maintainer may create a `Blocker` and link it with `violates`, which causes
the rendered contract status to become `violated`. The Maintainer may not mark a
contract `satisfied`; satisfaction requires host evidence.

Attempt outcome is also host-derived where possible:

```text
pending = proposed but not executed
ok = targeted commands succeeded and no blocker remains for target
failed = targeted command failed
ok_but_still_blocked = command succeeded but a blocker still violates target or child contract
```

This avoids stale graph state. If the graph and world model disagree, the world
model wins.

---

## 12. Planner Prompt Interaction

The planner should see the world model and graph as different kinds of
information, not competing truth sources.

The Planner prompt should be strategic and target-driven. The Planner does not
certify facts and does not maintain the graph. It chooses the next recipe patch.

Prompt structure:

```text
# Objective
Make the repository environment pass final verification.

# Deterministic World State
Facts certified by host code. Treat these as authoritative.

# Contract Graph
Planner-facing diagnosis and repair map. Use this to choose targets.
If ContractGraph conflicts with WorldModelMap, trust WorldModelMap.

# Recent Evidence
Latest command outputs / blockers.

# Required Output
Emit a RecipePatch or BuildScriptRevision targeting contract IDs.
```

System philosophy:

```text
You are the global environment recipe planner.
WorldModelMap is authoritative factual state.
ContractGraph is the repair target map.
Use the graph to choose what to target, not to certify truth.
Start from required goal contracts.
Traverse unsatisfied depends_on paths.
Prefer root blockers over downstream symptoms.
Avoid repeating attempts unless the strategy changes.
Group repairs by layer when useful.
Emit a coherent RecipePatch, not a tiny one-command reaction.
Every recipe step must include target_node_ids.
```

Planner selection algorithm:

```text
1. Start from required goal contracts.
2. Traverse unsatisfied depends_on edges.
3. Find blockers violating reachable contracts.
4. Prefer root blockers over downstream symptoms.
5. Avoid repeating attempts that already failed unless strategy changes.
6. Group unresolved contracts by layer: system, deps, runtime, build, tests, config.
7. Emit a RecipePatch that addresses the highest-impact connected frontier.
```

Recommended rendered sections:

```text
# Deterministic World State Summary
- base image/runtime
- package manager/build system
- declared deps vs installed facts
- dependency_state highlights
- recent command evidence
- host progress/final verification state

# ContractGraph Repair Map
- required goal contracts
- violated/unknown contracts on goal paths
- active blockers
- recent attempts and outcomes
- depends_on paths relevant to current goals

# Current Repair Frontier
- unresolved root contracts grouped by layer
- attempts to avoid
- validators/checks to run after repair

# Required Output
- apply_recipe_patch | done | giveup
- RecipePatch steps with target_node_ids
```

Expected planner output:

```json
{
  "action": "apply_recipe_patch",
  "target_node_ids": [
    "contract:python_import:cv2",
    "contract:system_library:libGL.so.1",
    "contract:goal:repo_imports_work"
  ],
  "recipe_patch": {
    "steps": [
      {
        "id": "step:install-libgl1",
        "kind": "system_install",
        "command": "apt-get update && apt-get install -y libgl1",
        "target_node_ids": ["contract:system_library:libGL.so.1"]
      },
      {
        "id": "step:validate-cv2",
        "kind": "validate",
        "command": "python -c \"import cv2\"",
        "target_node_ids": ["contract:python_import:cv2"]
      }
    ]
  },
  "instructions": "Apply these steps. If installation fails due to immediate local build errors, repair within the targeted contracts and retry validation."
}
```

---

## 13. BuildAgent Role

The planner should not emit tiny one-command actions by default. It should emit
a recipe patch or revised build script.

```text
Planner = global recipe designer
BuildAgent = local execution/debugger
Maintainer = state and graph observer
```

The BuildAgent applies the recipe patch and fixes immediate local execution
errors inside the patch scope:

- missing compiler
- missing header
- wheel build failure
- package manager syntax issue
- wrong package name
- missing `PYTHONPATH`

The BuildAgent should not freely redesign the whole recipe. Successful local
repairs are reported back through command evidence; the planner can promote them
into the next recipe revision.

---

## 14. Why This Helps Global Planning

The graph helps global planning when it is used to choose sets of related
repairs, not just the next missing package.

Example:

```text
repo_tests_pass
  depends_on -> repo_imports_work
  depends_on -> repo_services_ready

repo_imports_work
  depends_on -> cv2_importable
  depends_on -> torch_importable

cv2_importable
  depends_on -> libGL_present

repo_services_ready
  depends_on -> redis_reachable
```

Current blockers:

```text
missing-libGL violates libGL_present
redis-connection-refused violates redis_reachable
```

The planner can emit one coherent recipe patch:

```text
apt-get install -y libgl1 redis-server
start redis-server
validate cv2 import
validate redis ping
rerun pytest
```

The graph supports:

- goal impact analysis
- repair grouping
- avoiding repeated failed attempts
- choosing build-script revisions instead of one-off shell commands
- fault localization from symptom to root repair target

---

## 15. Relationship To Jayint-Planer

This design is intentionally close to the `Jayint-Planer` branch, but it changes
the meaning of the graph.

```text
Jayint-Planer graph = typed task / execution-order graph
Concise ContractGraph = obligation / fault / repair graph
```

The static planning graph is useful as the cold-start generator:

```text
runtime -> package_manager -> language_dependency -> project_build -> verification
```

becomes:

```text
repo_tests_pass
  depends_on -> repo_tests_collect
  depends_on -> repo_build_ready
  depends_on -> repo_deps_installed
  depends_on -> package_manager_available
  depends_on -> runtime_compatible
```

Then runtime adds:

```text
Blocker violates Contract
Attempt addresses Contract
new Contract depends_on existing Contract
```

So the relationship is:

```text
Jayint-Planer = static execution-order skeleton
Concise ContractGraph = runtime diagnostic and repair overlay
```

The two can be unified by using the Jayint-Planer planner to seed the initial
goal/phase contracts, then maintaining the concise contract graph during
execution.

---

## 16. Common Questions And Ambiguities

### Does the cold-start graph need to be complete?

No. It should be coarsely complete, not exhaustively complete.

It should include goal and phase contracts:

```text
repo_tests_pass
repo_tests_collect
repo_deps_installed
repo_imports_work
repo_build_ready
repo_services_ready
repo_config_ready
```

It should not include every dependency, import, transitive package, or possible
system package candidate.

### If the Maintainer only updates local blockers, how does this enable global planning?

Every local discovery must be attached to the global goal backbone.

```text
new cv2_importable contract attaches under repo_imports_work
new libGL_present contract attaches under cv2_importable
new redis_reachable contract attaches under repo_services_ready
new pg_config_available contract attaches under repo_deps_installed / psycopg2_installable
```

This prevents the graph from becoming a local error log.

### Is this just a checklist?

No. A checklist has items and statuses. The contract graph has causal
relationships:

```text
Goal depends_on obligation
Blocker violates obligation
Attempt addresses obligation
```

The graph is useful when it explains why an obligation matters and what has
already been tried.

### Should every dependency become a contract?

No. All declared dependencies belong in `WorldModelMap.required`.

Promote an individual dependency into a contract only when it becomes
planning-relevant:

- a failure names it
- it blocks a goal
- it needs special repair
- it explains another violated contract

### Where do dependency relationships belong?

Deterministic dependency relations belong in `WorldModelMap.dependency_state`.

Examples:

```text
package A depends on package B
package X requires Python >=3.10
package Y conflicts with package Z
```

The contract graph should expose only the operational slice relevant to current
planning:

```text
dependency_versions_consistent
torch_importable depends_on python_version_compatible
psycopg2_installable depends_on pg_config_available
```

### How do runtime errors know which contract they relate to?

They are linked using this order:

1. planner target node IDs
2. deterministic error signatures
3. existing graph context
4. world-model dependency state
5. Maintainer interpretation

Planner target IDs are important because they make later failures easier to
attach.

### Who can mark a contract satisfied?

Only host evidence can satisfy a contract.

Examples:

```text
python -c "import cv2" exits 0
redis-cli ping exits 0
python -m pytest -q passes
```

The Maintainer can explain violations and propose checks, but it cannot certify
success.

### What should the planner output?

The planner should output a graph-grounded `RecipePatch` or
`BuildScriptRevision`, not a tiny one-command action by default.

Each recipe step should include `target_node_ids`.

### What should the BuildAgent do?

The BuildAgent executes the planner's recipe patch and performs bounded local
repair inside the targeted scope. It should not silently redesign the global
recipe.

### What remains authoritative?

Authority order:

```text
final verification / host checks
WorldModelMap deterministic facts
ContractGraph rendered planning state
Maintainer semantic interpretation
```

If the graph conflicts with the world model, trust the world model.

---

## 17. Non-Goals

This concise graph is not:

- a full dependency graph
- a full provenance graph
- a graph database
- a complete memory of all commands
- a replacement for `WorldModelMap`
- a substitute for final verification

Full provenance can remain in command logs, ledger traces, and world-model
history. The active graph should stay small enough to be useful in the planner
and Maintainer prompts.

---

## 18. Success Criteria

This design is working if:

1. The planner targets contracts instead of vague failures.
2. The graph stays small on large repos.
3. Runtime failures are attached to the right obligation.
4. Repeated failed attempts are visible and avoided.
5. The planner emits multi-step recipe patches for connected repair frontiers.
6. `WorldModelMap` remains authoritative for factual state.
7. Final verification remains the hard success gate.
