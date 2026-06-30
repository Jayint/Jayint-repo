# Uniform Requirement Graph Design

## Purpose

This document defines the simplified v3-core requirement graph model for the environment-building agent.

The graph is the canonical environment contract. The setup script is only a deterministic projection of the graph. The LLM may discover or repair graph structure, but it cannot directly certify state.

Core principle:

```text
LLM discovers or repairs graph structure.
Graph stores the environment contract.
Script is regenerated from the graph.
Host verifier alone marks nodes SATISFIED.
Maturity gates prove repo-level success.
```

The graph does not need to know everything upfront. It needs to faithfully accumulate the solution as failures reveal missing obligations.

## Uniform Node Model

Each graph node is an environment obligation. A node combines:

```text
what must be true
how to make it true
how to prove it is true
```

Canonical shape:

```json
{
  "id": "service:redis",
  "type": "Service",
  "layer": "services",
  "phase": "runtime",
  "strength": "hard",
  "state": "missing",

  "requires": ["tool:redis-cli"],
  "soft_requires": [],

  "setup_commands": [
    "apt-get update && apt-get install -y --no-install-recommends redis-server",
    "redis-server --daemonize yes"
  ],

  "check_command": "redis-cli ping",

  "evidence_refs": ["ev.12.gate"],
  "status_reason": "pytest failed with connection refused on localhost:6379",

  "attempts": [],
  "invalid_commands": []
}
```

Required fields:

```text
id
type
layer
phase
strength
state
setup_commands
check_command
evidence_refs
```

## Node Types

The graph should represent environment obligations, not only package dependencies.

```text
Platform       OS/image/arch/GPU constraints
Runtime        Python/node/java runtime
Tool           gcc, pkg-config, ffmpeg, psql, redis-cli
SystemLib      libpq, libxml2, libavcodec
Package        pip/npm/cargo package
Service        postgres, redis, rabbitmq
Config         DATABASE_URL, settings file, pytest config
DataAsset      fixture file, model weight, generated media
CommandTask    bounded custom setup action
Gate           installability/testability/runnability maturity gate
```

## Layers

Layers are used for script ordering and explanation. Hard dependency edges still decide actual topological order.

```text
platform
runtime
system
toolchain
packages
services
config
data
gates
```

## Phases

Phases define where commands belong in the final artifact.

```text
setup      baked into setup.sh / Dockerfile
runtime    needed when container/test session starts
test       needed only for the test command
gate       maturity command, not environment setup
```

Examples:

```text
pip install psycopg2        -> setup
apt-get install postgresql  -> setup
service postgresql start    -> runtime
pytest -q                   -> gate
```

## States

```text
unknown        discovered but not checked
candidate      soft hint, not blocking
missing        hard obligation not yet satisfied
satisfied      host check passed
blocked        attempted but cannot currently be satisfied
invalid        node or command proven wrong
```

Only deterministic host checks can write `satisfied`.

## Strength

```text
soft    useful hint, does not block execution
hard    required obligation, blocks dependent nodes/gates
```

Static LLM extraction should usually create soft nodes. Runtime or gate failures can promote soft candidates to hard obligations.

Example:

```text
README mentions Redis               -> soft service:redis
pytest fails connecting to :6379     -> promote service:redis to hard
```

## Edges

Edges are structural only. They do not execute commands.

Canonical edge:

```json
{
  "source": "pkg:psycopg2",
  "target": "syslib:libpq",
  "relation": "requires",
  "hard": true,
  "evidence_refs": ["ev.4.build"]
}
```

Allowed relations:

```text
requires
alternative_to
conflicts_with
explains_failure_of
```

For v3-core, `requires` is sufficient.

## Commands

Use `setup_commands: list[str]`, not a single `install_command`, because services, config, data, and custom setup actions often require multiple commands.

Package example:

```json
{
  "id": "pkg:psycopg2",
  "type": "Package",
  "layer": "packages",
  "phase": "setup",
  "strength": "hard",
  "state": "missing",
  "requires": ["syslib:libpq", "tool:gcc"],
  "setup_commands": [
    "python3 -m pip install --break-system-packages psycopg2"
  ],
  "check_command": "python3 -c 'import psycopg2'",
  "evidence_refs": ["ev.3.import"]
}
```

Data asset example:

```json
{
  "id": "data:test_video",
  "type": "DataAsset",
  "layer": "data",
  "phase": "test",
  "strength": "hard",
  "state": "missing",
  "requires": ["tool:ffmpeg"],
  "setup_commands": [
    "ffmpeg -y -f lavfi -i testsrc=size=640x360:rate=24 -t 2 /tmp/test.mp4"
  ],
  "check_command": "test -s /tmp/test.mp4",
  "evidence_refs": ["ev.21.gate"]
}
```

Config example:

```json
{
  "id": "config:DATABASE_URL",
  "type": "Config",
  "layer": "config",
  "phase": "test",
  "strength": "hard",
  "state": "missing",
  "requires": ["service:postgres"],
  "setup_commands": [
    "printf '%s\\n' 'export DATABASE_URL=postgresql://localhost/test_db' >> /etc/profile.d/repo-env.sh"
  ],
  "check_command": "test -n \"$DATABASE_URL\"",
  "evidence_refs": ["ev.14.gate"]
}
```

Note: plain `export X=...` inside an isolated shell command does not persist across Docker layers or executor calls. Config nodes should write a persistent env file, final Dockerfile `ENV`, or another explicit runtime binding artifact.

## Core Invariants

```text
Hard node must have a check_command.
Executable node has setup_commands[].
Only check_command can mark a node SATISFIED.
No accepted setup command without a target node.
No accepted setup command without evidence.
Edges do not execute commands.
Script is always generated from graph nodes.
LLM cannot write SATISFIED.
```

## LLM Patch Contract

The LLM may output graph-first or script-first proposals.

Graph-first proposal:

```json
{
  "diagnosis": {
    "class": "missing_service",
    "evidence_ref": "ev.12.gate",
    "why": "Tests attempted to connect to localhost:6379 and got connection refused."
  },
  "patch": {
    "add_nodes": [
      {
        "id": "service:redis",
        "type": "Service",
        "layer": "services",
        "phase": "runtime",
        "strength": "hard",
        "setup_commands": [
          "apt-get update && apt-get install -y --no-install-recommends redis-server",
          "redis-server --daemonize yes"
        ],
        "check_command": "redis-cli ping",
        "evidence_refs": ["ev.12.gate"]
      }
    ],
    "add_edges": [
      {
        "source": "gate:testability",
        "target": "service:redis",
        "relation": "requires",
        "hard": true
      }
    ]
  }
}
```

Script-first proposal with graph binding:

```json
{
  "diagnosis": {
    "class": "missing_service",
    "evidence_ref": "ev.12.gate",
    "why": "Tests attempted to connect to localhost:6379."
  },
  "script_fix": {
    "commands": [
      "apt-get update && apt-get install -y --no-install-recommends redis-server",
      "redis-server --daemonize yes"
    ],
    "check_command": "redis-cli ping"
  },
  "graph_binding": {
    "node_id": "service:redis",
    "node_type": "Service",
    "layer": "services",
    "phase": "runtime",
    "parent": "gate:testability",
    "strength": "hard"
  }
}
```

PatchGate normalizes both styles into graph nodes. No orphan script edits are accepted.

PatchGate validates:

```text
evidence exists
node id is canonical
node type and layer are valid
hard node has check_command
check_command is read-only
setup_commands are attached to a node
edge endpoints exist or are created in the same patch
LLM does not set state=satisfied
```

## Execution Loop

```text
1. Build initial graph.
2. Promote known deterministic package/system nodes to hard.
3. Keep uncertain service/config/data nodes soft.
4. Topologically select hard missing nodes.
5. Emit node.setup_commands into setup blocks.
6. Run one node block at a time.
7. Run node.check_command.
8. Mark SATISFIED only if check passes.
9. On failure, send graph slice + evidence to LLM.
10. LLM proposes graph/script-bound patch.
11. PatchGate validates and applies.
12. Regenerate script from graph.
13. Repeat until maturity gates pass or budget expires.
```

## Maturity Gates

Represent repo-level success criteria as special nodes:

```text
gate:installability
gate:testability
gate:runnability
```

Their `check_command` is the actual repo-level proof:

```text
pip install -e .
pytest -q
python -m app --help
```

Gate failure does not mean the gate node can be locally installed. It means the graph is insufficient. The LLM should classify the failure and add or promote missing environment nodes.

## Comparison With Current v3

Current v3 has:

```text
Node.check_command
Node.chosen_fix
ProviderSpec in PatchProposal
ScriptPatch/manual_blocks outside the graph
compiler-derived commands based on node type
```

The uniform graph uses:

```text
Node.setup_commands[]
Node.check_command
Node.requires[]
```

So the node itself is the executable and certifiable unit.

Current v3 is more expressive because it separates requirement and provider. That supports multiple providers, providers satisfying multiple nodes, and detailed provider replacement history.

The uniform model is simpler and better for the first clean v3-core branch:

```text
less schema indirection
cleaner script synthesis
cleaner fault localization
services/config/data become normal nodes
easier paper explanation
```

Provider objects can be reintroduced later if alternatives and provider-level history become necessary.

## Minimal v3-Core Components

```text
GraphBuilder
Scheduler
ScriptMaterializer
BlockExecutor
HostVerifier
RepairReasoner
PatchGate
GateRunner
ArtifactSynthesizer
EvidenceLedger
```

The central invariant for the clean refactor branch:

```text
No persistent environment mutation is canonical unless it is represented in a graph node,
grounded in evidence, emitted as setup commands, and certified by a deterministic host check.
```
