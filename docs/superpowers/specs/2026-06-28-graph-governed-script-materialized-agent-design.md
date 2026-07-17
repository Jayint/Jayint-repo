# Graph-Governed, Script-Materialized Environment Agent - Design

**Date:** 2026-06-28  
**Branch:** `john-planner-v3`  
**Status:** Converged design from architecture discussion; ready for implementation planning  
**Lineage:** graph-scheduled agent -> topological-wave executor -> graph-to-script hybrid

## 1. Thesis

Environment setup should not be modeled as an LLM repairing a container from raw logs. It should be
modeled as a host-certified, cross-layer obligation satisfaction problem.

The target v3 agent has one canonical semantic state and one canonical execution artifact:

```text
Certified Environment Obligation Graph = source of truth
Annotated install script                = replayable execution artifact
Host checks                             = truth/certification
LLM BuildAgent                          = bounded patch proposer, not state owner
```

The script is not the state. It is a compiled proof attempt from the graph.

Core separation:

```text
Graph decides WHAT must be true and WHAT is ready.
Script materializes HOW the current graph repair plan is executed.
Build/Lab Agent reasons about failures and proposes structured patches.
PatchGate validates and applies allowed graph/script mutations.
Host certifier decides WHETHER obligations are satisfied.
Maturity gates decide WHETHER the repo is installable, testable, runnable.
```

This preserves the graph's novelty while borrowing the practical strengths of BashFile-style
systems: line/block fault localization, replayability, and direct Dockerfile synthesis.

## 2. Philosophy

The key shift is from an action-first loop to a model-first loop.

Old shape:

```text
error log -> LLM chooses shell command -> mutate live container -> repeat
```

Target shape:

```text
error evidence -> graph obligation / causal hypothesis / script patch
-> host-validates patch
-> execute annotated block
-> host certifies nodes
-> maturity gate probes behavior
```

The LLM still reasons, but it reasons into artifacts:

- typed requirements,
- provider candidates,
- causal edges,
- alternative/retracting repairs,
- annotated script blocks,
- check commands,
- evidence references.

It does not write truth. It does not declare success. It does not freely mutate canonical state.

## 3. Core Artifacts

### 3.1 Certified Environment Obligation Graph

The graph is the canonical semantic model.

Node:

```text
id
type: Platform | Runtime | SystemLib | Tool | Package | Service | Config | DataAsset | BuildGate | TestGate | RunGate
layer / tier
state: UNKNOWN | MISSING | SATISFIED
check_command
evidence_refs
provider_candidates
attempts
metadata
```

For v1, keep state deliberately simple:

```text
UNKNOWN    the host has not checked this obligation yet
MISSING    the host checked it and the canonical local check failed
SATISFIED  the host checked it and the canonical local check passed
```

Do not encode semantic completeness in node state. A `SATISFIED` node only means its own local
check passed. If a later maturity gate still fails, add a more specific missing obligation rather
than weakening or complicating the original node state.

Edge planes:

```text
requires          prerequisite relationship: A cannot work until B works
constrains        requirement restricts a platform/runtime/package choice
conflicts_with    two requirements/providers cannot both hold
alternative_to    mutually substitutable repair or package/platform choices
provides          provider/action can satisfy an obligation
retracts          choosing one repair removes another obligation
caused_by         failure explained by a missing obligation
observed_as       evidence that surfaced an obligation
invalidated_by    later evidence disproves a hypothesis/provider
```

Edges may be hard or soft.

```text
hard edge  affects scheduling/topological order; source cannot be satisfied until target is satisfied
soft edge  is a hypothesis/hint; it does not block scheduling and must be promoted before it acts as truth
```

Static service/config/data evidence should usually create soft edges or hints:

```text
Project --soft_requires--> Service(postgres)
  evidence: DATABASE_URL in .env.example
  promotion_rule: connection_refused_or_missing_DATABASE_URL
```

Runtime evidence or a maturity-gate failure can promote a soft edge to a hard requirement:

```text
psycopg2.OperationalError: connection refused
  -> TestGate --requires--> Service(postgres)
```

The graph should be treated as one artifact with multiple views, not multiple competing graphs.
The old terms `depgraph`, `contract graph`, and `world model` should be collapsed in paper framing
into this single model. Internally, `WorldModelMap` may remain as an envelope.

### 3.2 Annotated Install Script

The script is the canonical replay artifact and fault-localization surface. It is compiled from the
graph's current repair plan.

Example block:

```bash
#@action id=sys.ffmpeg wave=system
#@targets pkgconfig:libavcodec pkgconfig:libavformat tool:pkg-config
#@provides apt:libavcodec-dev apt:libavformat-dev apt:pkg-config
#@check pkg-config --exists libavcodec
#@check pkg-config --exists libavformat
#@check command -v pkg-config
apt-get update
apt-get install -y --no-install-recommends pkg-config libavcodec-dev libavformat-dev
```

Script block fields:

```text
block_id
wave / layer
command(s)
target_node_ids
provider ids
expected effects
check_commands
evidence_refs
mutates_env
can_batch
```

The script may be patched, but only through structured operations:

```text
add_block
replace_block
delete_block
split_block
merge_blocks
attach_check
attach_target
invalidate_block
```

Free-form script edits are not accepted directly into canonical state.

### 3.3 Evidence Ledger

Every command, check, maturity gate, lab experiment, and patch gets an evidence object.

```text
evidence_id
container_kind: canonical | lab | fresh_replay
block_id / node_id / gate_id
command
rc
stdout_stderr_excerpt
timestamp / cycle
```

Graph nodes and causal edges must cite evidence. This prevents unsupported LLM graph growth.

## 4. Authority Model

Strict write permissions:

| Component | May write | Must not write |
|---|---|---|
| Graph scheduler | selected frontier/waves | node truth |
| Script compiler | annotated script blocks from graph | graph truth |
| Build/Lab Agent | PatchProposal with rationale | direct graph mutation, `SATISFIED`, final success |
| PatchGate / StateReducer | validated UNKNOWN/MISSING nodes, providers, edges, script patches | `SATISFIED` without host check |
| Host certifier | node state from `check_command` | speculative nodes/edges |
| Maturity gate runner | gate evidence, done signal when verified | local node truth |

The old LLM Maintainer should not be part of v3. If the term `maintainer` remains in code, it should
mean a deterministic reducer/gate:

```text
PatchProposal -> validation -> canonical graph/script mutation
```

The LLM BuildAgent does the reasoning and emits the patch proposal.

## 5. Graph Construction

### 5.1 Static Constraint Graph Before Container

Before selecting a base image, build a lightweight static constraint graph from repo files only.
This graph is not fully certified; it informs platform choice.

Sources:

```text
pyproject/setup/requirements/lockfiles
.python-version, CI python-version, tox
Dockerfile/devcontainer/compose
README/setup docs
.env.example / pytest.ini / conftest.py
imports and native-risk package tables
CUDA/GPU hints
service declarations in compose/CI
```

Outputs:

```text
runtime constraints
platform constraints
package roots
native-risk hints
service/config/data hints
candidate maturity commands
```

Important rule:

```text
Static config/service/data extraction creates hints, not hard obligations.
Runtime evidence promotes hints into obligations.
Host checks certify satisfaction.
```

Use three levels for uncertain non-package requirements:

```text
Hint
  observed in files or inferred from a package; not scheduled

Candidate obligation
  plausible and checkable; shown to the Build/Lab Agent as context

Active obligation
  promoted by runtime evidence, maturity-gate failure, or explicit test/CI declaration;
  participates in scheduling as a hard requirement
```

Packages/imports are allowed to become hard obligations earlier because they are usually direct
source-level requirements. Config, service, and data obligations should generally wait for stronger
evidence.

### 5.2 Config And Service Discovery

Config/service discovery should be hybrid:

```text
deterministic scanners -> evidence bundle -> LLM semantic classifier -> graph hints/candidates
runtime or gate failure -> promotion to active obligation
host checks -> SATISFIED/MISSING
```

The deterministic layer should collect broad, cheap evidence. The LLM should interpret and normalize
that evidence. It should not scan the whole repo blindly and should not create final truth.

Deterministic collectors:

```text
docker-compose.yml / compose.yaml
  services, images, ports, env, volumes, healthchecks

.github/workflows/* / .gitlab-ci.yml / other CI files
  service containers, env vars, setup commands, test matrices

.devcontainer.json / Dockerfile
  expected OS packages, tools, services, exposed ports

.env.example / sample config files
  env var names, defaults, connection strings

README / docs setup sections
  human-stated setup, services, credentials, fixtures

pytest.ini / conftest.py / test fixtures
  required env, service fixtures, temporary data generation

source code AST / structured grep
  os.environ, getenv, pydantic settings, django/flask settings, URL parsers

Makefile / scripts
  run/test commands, exported env, service boot commands
```

The LLM receives a compact evidence bundle, not raw unbounded files:

```json
{
  "goal": "Infer local install/test/run environment requirements, not deployment requirements.",
  "deterministic_hits": [
    {
      "evidence_id": "ci.12",
      "file": ".github/workflows/test.yml",
      "kind": "ci_service",
      "snippet": "services: postgres: image: postgres:15 ports: ['5432:5432']"
    },
    {
      "evidence_id": "env.03",
      "file": ".env.example",
      "kind": "env_var",
      "name": "DATABASE_URL"
    },
    {
      "evidence_id": "code.44",
      "file": "app/settings.py",
      "kind": "env_read",
      "name": "DATABASE_URL"
    }
  ]
}
```

The LLM returns structured graph candidates:

```json
{
  "requirements": [
    {
      "type": "Service",
      "id": "service:postgres",
      "state": "HINT",
      "check_command": "pg_isready -h localhost -p 5432",
      "evidence_refs": ["ci.12", "env.03", "code.44"],
      "rationale": "CI starts postgres and code reads DATABASE_URL."
    },
    {
      "type": "Config",
      "id": "config:DATABASE_URL",
      "state": "CANDIDATE",
      "check_command": "test -n \"$DATABASE_URL\"",
      "evidence_refs": ["env.03", "code.44"]
    }
  ]
}
```

Promotion policy:

```text
single weak evidence source       -> Hint
multiple aligned static sources   -> Candidate obligation
explicit CI test service          -> Candidate obligation
runtime missing-env failure       -> Active Config obligation
connection refused / auth failure -> Active Service or Config obligation
maturity gate failure             -> promote or add specific obligation
```

CI/CD evidence is useful but should be treated carefully. It may contain deployment-only steps,
optional job matrix entries, secrets, release publishing, cache setup, or infrastructure that is not
needed for local reproduction. The LLM prompt must ask for local installability/testability/
runnability requirements and must mark ambiguous CI-derived findings as `HINT`.

For v1, keep the scope narrow:

```text
regex/AST evidence extraction
one LLM classification pass over the compact bundle
Config/Service/DataAsset nodes as Hint or Candidate
promotion to Active only from explicit test/CI declaration or runtime/gate failure
no hard service/config scheduling from a single weak static clue
```

#### 5.2.1 Handling the existing config/service detectors (decided 2026-06-28)

The codebase already has battle-tested config/service detection. It is REUSED, not
replaced; what changes is where its output lands and the special-casing around it. Three
layers, three fates:

```text
Pure detectors  (config_scan.{scan_env_reads, parse_env_example,
                 scan_framework_config_reads, scan_env_defaults};
                 service_scan.{scan_compose_services, scan_ci_services,
                 scan_env_bindings, service_from_url}; curated tables
                 config_obligations_for_package / services_for_package)
  -> KEEP & REUSE unchanged. These become the deterministic collector tier of §5.2:
     the Phase-1 static_collect wraps them READ-ONLY to emit the compact evidence
     bundle. They feed BOTH the in-container certified graph and the §5.1 pre-container
     StaticConstraintGraph (more reuse, not less).

Graph-mutating wrappers  (config_scan.scan_config / service_scan.scan_services,
                          called in build.py::build_dep_graph)
  -> v1: KEEP as-is.  v3: REFRAME. Instead of injecting Config/Service nodes straight
     into the build graph, their signal flows deterministic-evidence -> LLM classifier
     -> SOFT hint nodes (Node.data["promotion"]="hint"/"candidate", soft edges) ->
     promotion to hard only on runtime/gate failure. Split by evidence strength per the
     promotion policy above: curated package-induced obligations + explicit CI services
     -> deterministic CANDIDATE; weak single-source reads -> HINT via the classifier.

Scheduler carve-out  (schedule._is_actionable's hard-coded "CONFIG advisory-only
                      except service-binding; SERVICE only if confirmed+armed")
  -> RETIRE / GENERALIZE. It collapses into one rule: a node schedules only when its
     inbound requirement edges are HARD (promoted); _dependencies_satisfied respects
     Edge.data["hard"]. The bespoke special case dissolves into the soft/hard model.
```

Idempotence: the wrappers and the classifier may both surface the same var/service.
Canonical `config_id`/`service_id` + PatchGate dedupe make this a no-op (same id ⇒ one
node). Sequencing: Phase 1 is non-destructive (wrap only); the reframe + carve-out
generalization land in the v3 rewrite (Phase 2) and the soft-edge work (Phase 5). Until
then the legacy graph-mutation path keeps working (already soft via the carve-out).

### 5.3 Platform Profiles

Platform is a first-class choice, not just a pre-step.

Candidate `PlatformProfile`:

```text
base_image
distro / release
package_manager
arch
libc
python minor
CUDA/GPU capabilities
preinstalled tools/libs
image family: slim | full | cuda | dev
```

Platform selection scores candidates against static constraints. It may use an LLM for ambiguous
ranking, but the ranking must be grounded in explicit extracted constraints.

After image selection, materialize a `Platform` node in the graph:

```text
Runtime -> Platform
SystemLib/Tool -> Platform
Package -> Runtime
Service -> SystemLib/Tool when in-image provisioning is used
```

### 5.4 Platform Switching Policy

Platform switching should be treated as future work, not part of the normal v1 repair loop.

For v1:

```text
Select one PlatformProfile before canonical build.
Materialize the Platform node.
Compile and run the setup script against that platform.
Do not switch platforms for ordinary missing dependencies.
```

The only v1 exception is a hard platform contradiction discovered before much certified work has
accumulated:

```text
required Python minor cannot be provided
architecture is incompatible
CUDA/GPU is required but absent
package/provider is unavailable for the distro/release
glibc/libc family is incompatible
package cannot install or build on this platform
```

If such a contradiction is accepted, discard the canonical container, keep the graph evidence and
static constraints, choose a new `PlatformProfile`, re-materialize the `Platform` node, regenerate
the script, and fresh replay from block 0.

Deferred platform-switching work:

```text
PlatformContradiction objects with evidence and alternatives
cost model for apt/pip/config vs runtime minor vs base-image switch
provider availability lookup per distro/release
counterfactual lab builds on alternative images
policy for preserving, invalidating, or replaying certificates across platform changes
```

Do not use platform switching as a general repair tactic. It is a structural escape hatch.

### 5.5 Certified Graph In Target Container

After platform/image selection, build the certified graph in the target container context:

```text
static scan -> imports/test/project nodes
resolver -> package closure
runtime node -> target Python
native prediction -> system/tool hints
config/service/data hints -> advisory or confirmed nodes
install/probe/ldd/import probes -> observed system/package obligations
certify_all -> UNKNOWN/MISSING/SATISFIED states
```

Scratch-container certificates are provisional. Live-container certificates are authoritative.

## 6. Topological Waves And Script Compilation

Topo sort is not used to install every node one by one. It is used to preserve causal order while
batching independent ready obligations.

Pipeline:

```text
graph nodes -> topological order -> ready waves -> annotated script blocks
```

Waves:

```text
platform/runtime wave
system/toolchain wave
package wave
service/config/data wave
build/installability gate wave
testability gate wave
runnability gate wave
```

Batching rule:

```text
Merge adjacent actions when:
  same wave/layer
  same package manager
  no dependency edge between them
  low ambiguity
  same rollback/retry behavior
```

Start conservative in v1:

```text
one provider/action -> one block
```

This gives precise fault localization:

```text
failed block -> failed provider/action -> affected graph node(s)
```

Example:

```bash
#@action id=apt.update wave=system
#@targets apt:index
#@check test -d /var/lib/apt/lists
apt-get update

#@action id=apt.pkg-config wave=system
#@targets tool:pkg-config
#@provides apt:pkg-config
#@check command -v pkg-config
apt-get install -y --no-install-recommends pkg-config

#@action id=apt.libplacebo wave=system
#@targets pkgconfig:libplacebo
#@provides apt:libplacebo-dev
#@check pkg-config --exists libplacebo
apt-get install -y --no-install-recommends libplacebo-dev
```

Defer batching to v2:

```text
same safe wave -> one block
```

If a future batched block fails:

```text
batch block failed -> split block -> isolate culprit -> update graph/provider model
```

Batch isolation policy:

```text
1. Classify stderr. If it names an exact culprit, invalidate that provider/action.
2. Split phases, e.g. apt-get update separate from apt-get install.
3. Split packages/providers. v1 can do one package at a time; v2 may binary-split large batches.
4. Invoke the LLM/lab agent only for the isolated culprit or genuinely ambiguous failure.
```

## 7. Block Execution And Certification

Per-command certification is preserved as:

```text
per-block execution + per-node certification
```

Block success is not node truth.

```text
block rc == 0          means the shell action completed
node check rc == 0     means the node is SATISFIED
gate success           means a maturity level passed
```

Each node type should have one canonical local check in v1. Avoid exposing a complex check-strength
taxonomy until the basic loop is proven.

| Node type | Canonical check |
|---|---|
| Platform | `cat /etc/os-release` plus `uname -m`, or a specific assertion over them |
| Runtime | `python3 -c "import sys; assert sys.version_info[:2] == (MAJ, MIN)"` |
| Tool | `command -v <tool>` |
| SystemLib via pkg-config | `pkg-config --exists <name>` |
| SystemLib via soname | `ldconfig -p | grep -q <soname>` |
| Python package | `python3 -m pip show <dist>` |
| Python import | `python3 -c "import <import_name>"` |
| Service | `nc -z <host> <port>`; Postgres uses `pg_isready -h <host> -p <port>` |
| Config | `printenv <VAR>`; service-bound config may use a stronger connection probe |
| DataAsset | `test -s <path>` |
| BuildArtifact | `test -x <path>` or `test -f <path>` |
| Installability gate | repo setup/build/install command exits 0 |
| Testability gate | test command exits 0 with real tests executed |
| Runnability gate | executable/CLI/smoke workflow exits 0 |

Runner:

```text
for block in topo_ordered_script:
    run block.command(s) with strict shell mode
    record Evidence

    if block rc != 0:
        mark block failed
        attach evidence to target nodes
        invoke deterministic classifier or Build/Lab Agent
        stop, patch, or split

    if block rc == 0:
        run attached checks for target nodes
        SATISFIED only for checks that pass
        MISSING for checks that fail
        continue
```

Blocks run with strict shell behavior:

```bash
set -Eeuo pipefail
```

Plain shell scripts continue after a command fails unless `set -e` or an equivalent runner policy is
used. This system should stop on the first failed block. The failed `block_id` becomes the repair
anchor and restart point.

Restart policy:

```text
interactive repair: resume from the failed block in the canonical container
final validation: replay the full accepted script from a clean container
```

Four failure classes:

```text
1. block failed
   The action did not run. Localize to script block and its target nodes.

2. block succeeded but checks failed
   The provider/effect model is wrong or incomplete.

3. checks passed but maturity gate failed
   Known prerequisites were necessary but insufficient. Keep the passed nodes SATISFIED and add a
   more specific hidden requirement.

4. fresh replay failed
   Script/order/reproducibility issue. Add ordering edge or split/repair block.
```

## 8. Maturity Gates

We use two orthogonal axes:

```text
Environment requirement layers = causal build order
Maturity gates                 = behavioral evidence strength
```

Environment layers answer:

```text
What must be true before another thing can work?
```

Maturity gates answer:

```text
How strong is the evidence that the repo works?
```

Maturity ladder:

```text
Installability: dependencies/configure/build/import setup succeeds
Testability:    repo tests or smoke tests execute
Runnability:    real executable/CLI/app workflow runs
```

Gate success only unlocks the next gate. It does not prove future gates.

```text
system checks pass -> ready to retry setup
setup passes       -> ready to compile
compile passes     -> ready to test/run
tests pass         -> stronger evidence, not necessarily full runnability
run gate passes    -> strongest evidence
```

Gate failure creates or revises graph obligations.

## 9. Build/Lab Agent And Patch Proposal

The BuildAgent is the thinking component. It works on one failed block/gate/frontier at a time.

Inputs:

```text
failed block or maturity gate
relevant graph slice
annotated script block(s)
evidence excerpt
available hints/providers
allowed patch operations
```

The agent may run bounded experiments:

```text
canonical container: only graph-approved blocks
lab container: disposable branch for ambiguous exploration
```

The lab container can be dirty. Its state is never promoted directly. It can only yield patch
proposals.

BuildAgent output:

```json
{
  "rationale": {
    "failure": "meson cannot find libplacebo via pkg-config",
    "hypothesis": "libplacebo pkg-config metadata is missing",
    "expected_effect": "installing libplacebo-dev should make pkg-config --exists libplacebo pass"
  },
  "patch": {
    "add_requirements": [
      {
        "id": "pkgconfig:libplacebo",
        "type": "SystemLib",
        "name": "libplacebo.pc",
        "layer": "system",
        "check_command": "pkg-config --exists libplacebo",
        "evidence_ref": "ev:block:meson_setup:stderr"
      }
    ],
    "add_providers": [
      {
        "id": "apt:libplacebo-dev",
        "kind": "apt",
        "command": "apt-get install -y --no-install-recommends libplacebo-dev",
        "provides": ["pkgconfig:libplacebo"]
      }
    ],
    "add_edges": [
      {
        "source": "gate:installability:meson_setup",
        "relation": "requires",
        "target": "pkgconfig:libplacebo"
      }
    ],
    "script_patches": [
      {
        "op": "add_block",
        "block_id": "sys.libplacebo",
        "wave": "system",
        "command": "apt-get update && apt-get install -y --no-install-recommends libplacebo-dev",
        "target_node_ids": ["pkgconfig:libplacebo"],
        "checks": ["pkg-config --exists libplacebo"]
      }
    ],
    "request_checks": ["pkgconfig:libplacebo"]
  }
}
```

The agent may include reasoning, but the accepted state change is the patch.

## 10. PatchGate / StateReducer

The PatchGate is deterministic. It is the v3 replacement for an LLM Maintainer.

Responsibilities:

```text
schema validation
permission checks
evidence ref exists
node ids canonical
dedupe nodes/edges/providers
reject SATISFIED writes
ensure script block targets graph nodes
ensure check commands are read-only
ensure provider command matches allowed action class
apply accepted graph and script patches
request host checks
```

It may normalize:

```text
libplacebodev -> reject or map to libplacebo-dev only with evidence/provider validation
ModuleNotFoundError cv2 -> package/opencv-python candidate
libGL.so.1 missing -> syslib/libGL.so.1 node + apt/libgl1 provider
```

But it should not do creative repair reasoning. Creative reasoning belongs to Build/Lab Agent.

## 11. Runtime Feedback

Runtime failures are observed requirements. They are stronger than static hints.

Sources:

```text
failed script blocks
failed checks
failed maturity gates
lab experiments
fresh replay failures
```

Runtime ingest can add:

```text
Package obligations from import errors
SystemLib obligations from missing sonames/pkg-config failures
Tool obligations from command-not-found
Service obligations from connection failures
Config obligations from missing env vars
DataAsset obligations from missing files
```

State remains UNKNOWN/MISSING until host checks run.

## 12. Example: mpv

Initial maturity gate:

```bash
meson setup build -Dlibmpv=true -Dtests=true --werror
```

Failure:

```text
Dependency "libavcodec" not found, tried pkgconfig
```

BuildAgent/PatchGate create:

```text
Requirement: pkgconfig:libavcodec
Provider: apt:libavcodec-dev
Edge: gate:meson_setup requires pkgconfig:libavcodec
Check: pkg-config --exists libavcodec
Script block: apt install libavcodec-dev libavformat-dev
```

Block executes. Host certifies:

```text
pkg-config --exists libavcodec -> SATISFIED
pkg-config --exists libavformat -> SATISFIED
```

Retry installability gate. It may reveal `libplacebo`, then `libass`. These become additional
system-wave obligations.

Compile gate:

```bash
meson compile -C build
```

If successful:

```text
BuildArtifact build/mpv
check: test -x build/mpv
```

Testability gate:

```bash
meson test -C build
```

If an encode test fails, add encoder support obligations and providers.

Runnability gate:

```bash
build/mpv --no-config --ao=null --vo=null --frames=1 /tmp/test.mp4
```

If media file is missing, add:

```text
DataAsset /tmp/test.mp4
Provider: ffmpeg-generated synthetic media
Check: test -s /tmp/test.mp4
```

Runnability passes only when the actual binary runs successfully.

## 13. Comparison To Prior Script Repair Systems

HerAgent-style systems use an executable BashFile as the persistent state and validate through a
maturity ladder.

This design takes the useful parts:

```text
executable setup artifact
repair through feedback
installability -> testability -> runnability gates
```

But changes the authority model:

```text
HerAgent: LLM repairs BashFile as state.
This design: graph is certified state; script is compiled artifact.
```

Paper framing:

> Unlike script-repair agents, our script is not the state. It is a compiled, annotated proof attempt
> from a certified environment obligation graph. Failures localize back to graph obligations, and
> repairs are accepted only through typed patches and host certification.

## 14. Evaluation Plan

Baselines:

```text
B0 naive install/run
B1 pure ReAct
B2 script-only repair
B3 graph-only scheduler
B4 graph-to-script without causal overlay
B5 graph-to-script + causal overlay + lab experiments
```

Primary metrics:

```text
environment success rate
fresh replay success
full test pass rate
hollow success rate
commands / time / tokens
```

Secondary metrics:

```text
frontier adherence
wrong-layer repair rate
repeated repair rate
line/block fault localization accuracy
node satisfaction precision
number of runtime-discovered obligations
number of script patches
provider invalidation rate
```

Controlled case suite:

```text
missing pkg-config dependency
wrong apt package name
missing shared library
opencv-python vs opencv-python-headless
wrong Python minor
missing service
missing env var
missing data file
block succeeds but check fails
check passes but maturity gate fails
fresh replay order failure
```

Key ablation:

```text
graph as prompt only
vs
graph as scheduler/source of truth with compiled script
```

## 15. Implementation Roadmap

Phase 1: Minimal graph-to-script artifact

```text
Block dataclass
Script compiler from current emittable wave
Annotated setup.sh writer
Block runner
Per-block evidence logging
Per-node certification after block
static config/service/data evidence collectors
compact evidence bundle writer
```

Phase 2: PatchProposal API

```text
BuildAgent structured patch output
PatchGate schema/permission validation
graph patch application
script patch application
request_checks
LLM classification of config/service/data hints and candidates
```

Phase 3: Maturity gates

```text
installability/testability/runnability gate model
gate command mining or LLM extraction as hints
gate failure -> runtime obligation ingest
```

Phase 4: Platform

```text
StaticConstraintGraph
PlatformProfile candidates
Platform node materialization
Runtime/System/Package -> Platform edges
base-image selection from constraints
v1 hard-contradiction guardrail only; no normal platform switching
```

Phase 5: Causal overlay and lab experiments

```text
caused_by/provides/retracts/constrains/alternative edges
lab container branch execution
promotion of successful lab repairs as graph/script patches
counterfactual repair scoring
```

Phase 6: Evaluation harness

```text
fresh replay runner
baseline arms
controlled causal suite
per-cycle graph/script snapshots
metric extraction
```

Future: Platform switching

```text
PlatformContradiction schema
alternative PlatformProfile scoring after failure
lab builds on alternative base images
certificate invalidation/replay policy across platform changes
switch only on structural impossibility, not ordinary missing deps
```

## 16. Non-Negotiable Invariants

```text
1. The graph is the semantic authority.
2. The script is a compiled execution artifact, not truth.
3. A block exiting 0 never certifies graph state by itself.
4. Only host checks write SATISFIED.
5. Maturity gate success is behavioral evidence, not local node certification.
6. LLM output is accepted only as structured PatchProposal.
7. Lab-container success never mutates canonical state directly.
8. Every accepted node/edge/block cites evidence.
9. Final success requires fresh replay or verified canonical run, not LLM declaration.
10. Soft edges/hints do not block scheduling until promoted to hard obligations.
11. v1 uses one canonical local check per node type; stronger semantics are represented by new
    obligations or maturity gates, not complicated node-state meanings.
```

## 17. One-Line Contribution

A host-certified environment obligation graph governs topological repair waves and compiles them
into an annotated, replayable install script; LLMs reason only by proposing typed graph/script
patches, while host checks and maturity gates certify necessary and sufficient environment readiness.

## 18. Planning Decisions (2026-06-28)

Resolved after a three-pass codebase investigation (`.superpowers/sdd/newdesign-{1-reuse-gap,2-plan,3-risks-decisions}.md`). These bind the implementation plan.

1. **Arm strategy — rewrite `run_v3` in place.** v3 *becomes* this agent; no parallel `v4` arm.
   Consequence: the v3 loop body, its tests, and its baselines will churn — a v3 re-baseline is
   required. To preserve the §14 B3-vs-B5 ablation (graph-only vs graph-to-script) despite the
   in-place rewrite, keep an **internal toggle** inside the new loop (script-materialization off →
   graph-only behaviour) rather than a separate arm.
2. **Final-artifact authority — the compiled script replaces ledger-replay now.** The annotated
   `setup.sh` becomes the canonical Dockerfile / fresh-replay source (invariant #1 holds from the
   switch-over). The `synthesis.py`/`agent.py` ledger-replay path is superseded for v3 (kept for v1).
   This moves the script→Dockerfile materialization *into the v3 integration* (Phase 2), not Phase 6.
3. **Maturity-gate bar — keep the pytest done-gate.** `maintainer._verified_test_run_passed`
   (real rc-0 pytest pass) stays the binding `done` condition. Installability/runnability gates
   (Phase 3) are advisory / scheduling-only in v1 — they create obligations, they do not gate `done`.

**Node-state vs Hint/Candidate/Active (resolves the §3.1↔§5.2 inconsistency).** The `State` enum
stays exactly `{UNKNOWN, MISSING, SATISFIED}` (invariant 11). Hint / Candidate / Active is modelled
as **edge-hardness** (`Edge.data["hard"]`) plus a **node metadata tag** (`Node.data["promotion"]`),
never as a node `state` value. The §5.2 example JSON's `"state":"HINT"/"CANDIDATE"` is interpreted
as this promotion tag, not the certified state.

**Sequencing safety.** Although v3 is rewritten in place, Phase 1 builds the graph→script→run→
certify→evidence machinery as **standalone, separately-tested modules** that touch neither `run_v1`
nor `run_v3`; the in-place `run_v3` rewrite + artifact switch are isolated to Phase 2, gated by the
full suite and the re-baseline. Build the parts first, swap last.
