# Execution-Target Environment Construction Obligation Graph

**Date:** 2026-07-14  
**Status:** Proposed v2; ready for implementation planning  
**Scope:** Language-agnostic environment-construction core with initial Python and Node.js profiles  
**Out of scope:** Runtime configuration values, secrets, external services, and service orchestration

## Executive decision

The environment constructor will add a small **intent plane** before dependency resolution. The intent plane records:

- what runtime, build, or test target was selected;
- which top-level requirements are active for that target;
- why each requirement is active;
- which requirements must survive conflict recovery; and
- what degradation means if a requirement cannot be satisfied.

These records are called **typed root obligations**. They replace bare root tuples such as `(None, "django>=4.2")` at the resolver boundary.

The intent plane is not a replacement for the existing dependency graph and is not a new value in the existing graph `Layer` enum. It is an orthogonal contract:

```text
Intent plane                         Resolved package graph

Runtime obligation: django>=4.2     Provider: django==4.2.17
role: runtime                 ---->  depends on asgiref, sqlparse
criticality: critical
owner: runtime target
```

The current graph largely records what survived resolution. The obligation model records what was supposed to survive.

Dependency discovery will be target-reachability-driven rather than naming-driven. Names such as `tests/`, `dev`, `lint`, `requirements-test.txt`, or `npm test` may identify candidates, but names alone never activate dependencies. An obligation becomes active only through a selected execution target, a semantic declaration belonging to the selected project, a demonstrated consumption relationship, or high-confidence observed evidence.

The core model is language-agnostic. Python and Node.js provide ecosystem profiles for evidence extraction, resolution, project materialization, probing, and exact certification.

## What changes from the current pipeline

The current Python construction path is effectively:

```text
rich repository evidence
        |
        v
bare (None, requirement_string) roots
        |
        v
one priority-blind resolve/install loop
        |
        v
drop an inferred offending root
        |
        v
add Project/Test meaning after resolution
        |
        v
name/version-oriented install and certification
```

The dangerous operation happens before the graph knows why a root existed. Runtime-versus-test edges added after resolution cannot retroactively prevent a runtime root from being removed.

The proposed path is:

```text
repository evidence + caller objective
        |
        v
candidate execution targets
        |
        v
selected RuntimeTarget / TestTarget
        |
        v
target reachability and semantic declarations
        |
        v
typed root obligations
        |
        v
priority-aware resolution
        |
        v
obligation -> provider bindings
        |
        v
source-aware environment plan
        |
        v
lane-based readiness certificate
```

### Current versus proposed responsibilities

| Question | Current graph/pipeline | Proposed obligation model |
|---|---|---|
| Why is this root present? | Often lost at `select_roots()` | Evidence and activation path retained |
| Who needs it? | Usually inferred after resolution | Owned by an execution target or selected project |
| May it be removed? | No reliable semantic policy | Determined by criticality |
| Is a dev-like declaration active? | Often predicted from its name | Only when reachable from a selected target |
| What was originally required? | Can disappear when a root is dropped | Obligation persists even when unresolved |
| Which concrete package satisfies it? | Often conflated with the root | Separate provider binding |
| Did runtime work but tests degrade? | Commonly collapsed to one result | Separate readiness lanes |
| Did the correct source install? | Name/version often treated as enough | Source and artifact are part of provider identity |

### What the obligation model gives us

The model provides five capabilities the current resolver boundary does not have:

1. **Runtime preservation.** A selected-test or tooling conflict cannot silently remove a critical runtime requirement.
2. **Persistent intent.** An unsatisfied requirement remains in the outcome and certificate instead of disappearing from the graph.
3. **Target ownership.** Test dependencies belong to one selected test job rather than a repository-wide dev bucket.
4. **Explainable partial success.** Runtime readiness can remain true while a selected test capability is explicitly degraded.
5. **Targeted repair.** The same missing package can be classified differently when observed during application startup versus test collection.

The model does not discover intent by itself. It does not magically identify the correct command, understand arbitrary repository conventions, or map every import to a distribution. Those remain evidence and target-discovery problems. The model supplies a safe contract once evidence exists.

## Relationship to existing designs

This design supersedes the **dependency-selection policy** in [Role-Aware Declaration Reading](./2026-07-04-role-aware-declaration-reading.md) where broad dev, test, lint, and typing declarations were combined to maximize generic testability.

It does not supersede that design's declaration parsers, role evidence, or provenance collection. Those remain candidate-evidence producers. The change is at the activation and root-selection boundary: candidate evidence becomes an active obligation only when a selected target or semantic project contract justifies it.

This design preserves the lock and source enrichment work in [uv-Enriched Dependency Graph](./2026-06-23-uv-enriched-depgraph.md). It strengthens the downstream contract so package source identity survives resolution, installation, and certification.

The immediate B2, B5, C1, and regression-harness work remains useful as stabilization and measurement. This document describes the architecture those local fixes should converge toward.

## Measured motivation

Observed regressions show both over-selection and under-selection:

- B2 predicted intent from group names such as test, dev, lint, typing, CI, and QA. It produced no measured aggregate gain and allowed optional pressure to contribute to aiida-core's runtime closure collapsing from 216 packages to 2.
- Dropping anthropic-sdk's selected test root `http-snapshot[httpx]==0.1.8` changed a near-complete environment into zero useful collection without retaining an adequate explanation.
- B5's predictive installability gate suppressed a working editable install and changed pre-commit from EBSR 1.0 to 0.
- addons-server lacked the plugin that owns `--reuse-db`, while slither acquired an unrelated pytest plugin that crashed collection.
- Archipelago returned a successful pytest result while collecting only 4,227 of 20,943 tests.
- Source-aware resolution can still be undone by later emitters that reduce provider identity to only name and version.

These are not all solver failures. They are failures to preserve intent, ownership, source identity, and readiness semantics across the pipeline.

## Goals

- Preserve the selected runtime and project-materialization contract through conflict recovery.
- Construct an environment for explicit execution targets rather than a guessed union of repository workflows.
- Make target activation independent of conventional filenames, directory names, and dependency-group names.
- Retain evidence, confidence, ownership, and source constraints for every active root.
- Resolve one coherent runtime-plus-test environment when possible without freezing the first runtime versions chosen.
- Materialize the selected project using ecosystem-appropriate strategies and observable fallbacks.
- Preserve registry, VCS, URL, path, workspace, lock-context, and artifact identity end to end.
- Distinguish runtime, project, runner, collection, and test-result readiness.
- Make repair evidence-driven, attributable, and bounded.
- Support monorepos and incompatible target variants without forcing them into one environment.
- Reuse one core policy across Python, Node.js, and future ecosystem profiles.

## Non-goals

- Provisioning databases, message brokers, browsers, cloud services, or other external services.
- Synthesizing credentials, configuration values, or secrets.
- Inferring the user's intended program or workflow with certainty from an arbitrary repository.
- Guaranteeing that the project or its tests are logically correct.
- Running every CI matrix cell in one environment.
- Installing lint, documentation, typing, benchmark, or release tooling unless a selected target reaches it.
- Replacing ecosystem-native package solvers and lockfile installers.
- Treating naming conventions as authoritative semantic metadata.

## Conceptual model

### Four distinct planes

The architecture separates four concerns that are currently partially conflated:

```text
1. Evidence plane
   What facts were declared or observed?

2. Intent plane
   Which target was selected, and what must it be able to do?

3. Provider graph
   Which concrete packages/artifacts satisfy those obligations?

4. Execution and certification plane
   What was installed, what worked, and what degraded?
```

The existing graph `Layer` and edge `Strength` continue to describe environment topology, ordering, and blocking relationships. Obligation role and criticality answer different questions and must not be encoded by overloading those fields.

### Evidence

A repository fact or runtime observation. Examples include a semantic runtime dependency, a command installing a requirements file, a lockfile provider, an import failure, or a test-runner argument.

Evidence is descriptive. It does not become installation intent merely because its filename or group name resembles a known convention.

### Candidate target

A possible execution target discovered from explicit caller input, package scripts, CI commands, task runners, executable metadata, or low-confidence naming conventions. Candidate discovery does not activate dependencies.

### Execution target

A concrete objective the environment should support. An execution target records its command or import objective, working directory, platform/runtime constraints, project/workspace owner, variant, and selection evidence.

Target kinds are:

- **RuntimeTarget:** start a service, run a CLI, import a library, or execute the selected application entry point.
- **BuildTarget:** produce or install the artifact required by another selected target.
- **TestTarget:** invoke and collect one selected test job.
- **ToolingTarget:** run linting, typing, documentation, benchmarking, or another explicitly selected workflow.

A `BuildTarget` is usually reached as a prerequisite of a runtime or test target rather than selected independently.

### Obligation

A top-level requirement that an active target or selected project contract must attempt to satisfy. It records role, criticality, ownership, provenance, confidence, and source constraint.

### Provider

A concrete resolved distribution, package, workspace member, system artifact, or toolchain that satisfies an obligation. One provider may satisfy multiple obligations. An obligation may permit multiple provider versions.

### Target reachability

The causal path that connects an active target to evidence or an obligation:

```text
TestTarget
    -> executes script
    -> script installs requirement file
    -> requirement file declares package
    -> package becomes a test obligation
```

### Degradation

A recorded inability to satisfy a selected non-critical obligation or preferred materialization strategy. Degradation may preserve a useful runtime environment, but prevents an unqualified success certificate.

### Readiness

Evidence that one capability can proceed. Runtime, project materialization, test runner, collection, and test result are separate claims.

## Architectural invariants

1. **Names create candidates, not active obligations.** A directory, file, group, or script name alone cannot activate a dependency.
2. **Every active obligation has an activation path.** It is justified by a selected target, a semantic declaration owned by the selected project, a demonstrated consumption relationship, or high-confidence observed evidence.
3. **Critical obligations never disappear.** An unresolved critical obligation remains in the outcome and prevents runtime certification.
4. **Obligations are protected; initial versions are not.** A solver may reselect any provider allowed by the obligation.
5. **The selected target owns its target-specific obligations.** Repository-wide dev evidence is not a global test environment.
6. **Role and criticality are separate.** A build tool on a runtime path can be critical; a build tool needed only by a test provider inherits test criticality.
7. **Optional or unselected tooling exerts no solver pressure.** It remains candidate evidence.
8. **Every loss is attributable.** Unresolved or dropped non-critical obligations retain their owner, evidence, conflict, and decision.
9. **Project materialization is explicit.** Dependency installation alone does not prove the selected project is runnable.
10. **Source is part of provider identity.** Equal names and versions from different sources are not automatically equivalent.
11. **Every mutation path consumes the same environment plan.** Probe, image render, live repair, replay, and certification cannot independently reconstruct package intent.
12. **Process success is not certification.** Exact inventory and target-specific postconditions must be checked.
13. **Test readiness is not test correctness.** Assertion failures after valid collection do not automatically imply an environment failure.
14. **Ambiguity is observable.** The system reports uncertain target selection instead of hiding it through broad installation.
15. **Repair cannot weaken an invariant.** Feedback may add or promote obligations but cannot silently demote a critical one.

## Architecture overview

```text
Caller objective                Repository evidence adapters
      |                         manifests, commands, locks,
      |                         imports, probes
      |                                  |
      +----------------+-----------------+
                       v
             Candidate target inventory
                       |
              deterministic selection
                       |
                       v
       RuntimeTarget / BuildTarget / TestTarget
                       |
             semantic ownership and
              consumption reachability
                       |
                       v
              Typed root obligations
       runtime | build | test | tooling
                       |
                       v
         Priority-aware ecosystem resolver
       +----------------------------------+
       | prove critical contract          |
       | attempt fresh combined solve     |
       | attribute non-critical conflicts |
       +----------------------------------+
                       |
                       v
       Obligation -> source-aware providers
                       |
                       v
               EnvironmentPlan
       install | link | build | materialize
                       |
                       v
           Target-specific probes
                       |
              bounded enrichment
                       |
                       v
             ReadinessCertificate
```

## Target discovery and activation

### Selection precedence

Targets are selected in this order:

1. an explicit command or import objective supplied by the user, benchmark, or evaluation harness;
2. a named ecosystem-native target referenced by that explicit objective, such as a tox environment or package-manager script;
3. a single unambiguous semantic command from repository automation;
4. a target observed from an executable build/test workflow;
5. a low-confidence conventional candidate accepted by an explicit fallback policy.

Lower-precedence evidence may complete a selected target but cannot override the caller's objective.

### Naming rule

The following are candidate signals only:

- a directory named `tests`, `spec`, `src`, or `app`;
- a dependency group named `test`, `dev`, `lint`, `qa`, or `ci`;
- a file named `requirements-test.txt` or `dev-requirements.txt`;
- a script named `test`, `verify`, `build`, or `start`;
- a conventional workspace layout.

A name may cause the scanner to inspect a file or propose a target. It cannot by itself create a critical or required package root.

### Consumption relationships

Target activation follows what the target consumes. For example:

```text
selected command: ./machinery/green.sh
    |
    +-- executes machinery/green.sh
            |
            +-- installs -r ingredients/b.txt
            |       |
            |       +-- declares pytest-django
            |
            +-- runs python -m pytest checking_places
```

`pytest-django` becomes a test obligation because the selected target reaches it through an install action. The names `green.sh`, `b.txt`, and `checking_places` carry no semantic authority.

Consumption can be established by:

- semantic manifest relationships;
- parsed task-runner, package-script, tox, nox, CI, Make, or shell commands;
- lockfile reachability from an already active root;
- observed file/process activity during a controlled target attempt;
- high-confidence import ownership and failure origin.

Static command interpretation is bounded and conservative. The constructor does not need a general shell-program analyzer. Unsupported or dynamic behavior remains observable ambiguity and can be clarified by controlled execution.

### Semantic project declarations

Some declarations already define role semantically. Examples include Python project runtime dependencies or Node package runtime dependencies. They become active only for the selected project or workspace member.

The repository may contain many projects. A valid semantic declaration in an unselected member remains inactive.

### Evidence confidence

Evidence carries confidence and authority separately from role:

| Evidence | Default authority |
|---|---|
| Explicit caller target | Authoritative selection |
| Semantic dependency owned by selected project | Strong |
| Command directly installs or invokes an artifact | Strong |
| Lockfile path from an active root | Strong provider evidence |
| Runner loads a plugin or entry point | Strong target evidence |
| Failure with proven origin and package mapping | Strong observed evidence |
| Conventional filename/group/directory | Candidate only |
| Unverified import-to-package guess | Diagnostic only |

Low-confidence evidence cannot silently override stronger declarations or create critical obligations.

### Ambiguity

If several credible targets remain and no caller objective selects one, discovery returns candidate targets and their evidence. A caller may choose one, schedule independent variants, or explicitly accept a fallback.

Ambiguity is never resolved by unioning all candidate environments. A fallback-selected target is recorded as assumed and cannot receive an unqualified target-selection certificate.

## Core data model

The pseudocode uses Python syntax for readability; the model is not Python-specific.

### Execution target

```python
class TargetKind(Enum):
    RUNTIME = "runtime"
    BUILD = "build"
    TEST = "test"
    TOOLING = "tooling"


@dataclass(frozen=True)
class ExecutionTarget:
    id: str
    kind: TargetKind
    command: tuple[str, ...] | None
    import_objective: str | None
    working_directory: str
    project_owner: str
    platform_constraint: PlatformConstraint
    variant: str
    selected_by: EvidenceRef
    selection_confidence: Confidence
    supporting_evidence: tuple[EvidenceRef, ...]
```

A runtime library target may use an import objective rather than a command. A build target may be a prerequisite reached from another target.

### Roles and criticality

```python
class ObligationRole(Enum):
    RUNTIME = "runtime"
    BUILD = "build"
    TEST = "test"
    TOOLING = "tooling"


class Criticality(Enum):
    CRITICAL = "critical"
    REQUIRED = "required"
    OPTIONAL = "optional"
```

Initial policy:

| Activation path | Role | Criticality |
|---|---|---|
| Selected project runtime contract | Runtime | Critical |
| Build/materialization prerequisite on runtime path | Build | Critical |
| Selected test invocation or collection | Test | Required |
| Build prerequisite owned only by selected test path | Build | Required |
| Selected tooling target | Tooling | Required |
| Unselected workflow evidence | Any candidate role | Inactive; no obligation |

### Root obligation

```python
@dataclass(frozen=True)
class RootObligation:
    id: str
    ecosystem: str
    requirement: NormalizedRequirement
    role: ObligationRole
    criticality: Criticality
    owner_targets: tuple[str, ...]
    owner_projects: tuple[str, ...]
    activation_paths: tuple[ActivationPath, ...]
    evidence: tuple[EvidenceRef, ...]
    confidence: Confidence
    source_constraint: PackageSourceConstraint | None
```

Duplicate requirements are aggregated without first-wins loss. Compatible constraints are intersected while retaining all owners and evidence. Incompatible constraints remain visible as explicit conflicts.

### Activation path

```python
@dataclass(frozen=True)
class ActivationPath:
    edges: tuple[EvidenceEdge, ...]

# Example:
# TestTarget -> executes script -> installs file -> declares requirement
```

The activation path is the proof that a candidate became an active obligation.

### Source-aware provider identity

```python
@dataclass(frozen=True)
class ProviderIdentity:
    ecosystem: str
    normalized_name: str
    version: str
    source_kind: SourceKind
    source_location: str | None
    revision: str | None
    subdirectory: str | None
    artifact_digest: str | None
    lock_context: str | None
    peer_context: tuple[str, ...]
```

Not every ecosystem uses every field. `peer_context` is important for Node.js lock identities; `subdirectory` and editable source identity are common for Python direct references and workspaces.

### Resolution outcome

```python
@dataclass(frozen=True)
class ResolutionOutcome:
    providers: tuple[ResolvedProvider, ...]
    obligation_bindings: tuple[ObligationBinding, ...]
    unresolved_critical: tuple[UnresolvedObligation, ...]
    unresolved_required: tuple[UnresolvedObligation, ...]
    dropped_required: tuple[DroppedObligation, ...]
    degradation_reasons: tuple[DegradationReason, ...]
    conflict_explanations: tuple[ConflictExplanation, ...]
```

An outcome with `unresolved_critical` may still produce a diagnostic environment, but cannot certify runtime readiness.

### Environment plan

```python
@dataclass(frozen=True)
class EnvironmentPlan:
    target_ids: tuple[str, ...]
    steps: tuple[EnvironmentStep, ...]
    expected_inventory: tuple[ProviderIdentity, ...]
    project_materialization: ProjectMaterializationPlan
    allowed_failures: tuple[AllowedFailure, ...]
```

Steps may install, link, build, materialize, verify, or probe. Docker rendering, construction probing, live repair, and replay are execution backends over the same plan.

### Readiness certificate

```python
@dataclass(frozen=True)
class ReadinessCertificate:
    runtime_ready: bool
    build_ready: bool
    project_ready: bool
    materialization_mode: str | None
    test_runner_ready: bool
    collection_ready: bool
    test_result: str | None
    environment_degraded: bool
    target_selection_assumed: bool
    selected_targets: tuple[str, ...]
    collected_tests: tuple[str, ...]
    collection_errors: tuple[CollectionError, ...]
    uncovered_test_candidates: tuple[TestCandidate, ...]
    degradation_reasons: tuple[DegradationReason, ...]
```

`materialization_mode` is ecosystem-specific: Python editable/regular/source-tree, Node workspace/root-package/built artifact, or another profile-defined mode.

## Evidence-to-obligation rules

### Core activation rules

An active obligation must satisfy at least one of these rules:

1. It is a semantic runtime or build declaration owned by the selected project.
2. It is reached through a selected target's command, script, group, extra, requirement file, or workspace relationship.
3. It is a transitive provider reached from another active obligation through a solver or lockfile. Transitive providers are not new root obligations.
4. It is added by a high-confidence probe observation with a proven target origin and package mapping.

Conventional naming alone satisfies none of these rules.

### Failure-origin rule

The same missing dependency receives different ownership based on where it was required:

```text
application startup/import path
    -> runtime obligation, critical

selected test collection path
    -> test obligation, required

unselected lint/docs path
    -> inactive evidence
```

Failure origin must be demonstrated by the selected command, stack/probe phase, import owner, or another evidence edge. Directory names alone are insufficient.

### No blind package guessing

A missing module or executable name is not automatically a package name. Activation requires a semantic declaration, lock mapping, installed metadata, maintained high-confidence mapping, or another explicit source. Low-confidence possibilities remain diagnostics.

## Priority-aware resolution

### Resolution algorithm

For a selected project and compatible target variant:

1. Normalize and aggregate active obligations without discarding evidence or owners.
2. Resolve all critical runtime and build obligations by themselves.
3. If the critical set is unsatisfiable, return `unresolved_critical`; do not erase a critical obligation to manufacture a closure.
4. Add required obligations for the selected test or tooling target and resolve the complete set from scratch.
5. Allow every provider version to move within its obligation constraints.
6. If the combined solve fails, obtain a labeled unsatisfiable core when the ecosystem resolver supports it; otherwise isolate one through a bounded deterministic reduction.
7. If the core contains only critical obligations, report a critical failure.
8. If the core contains target-owned required roots, identify the smallest attributable required-obligation set whose removal restores the critical closure.
9. Drop only those required obligations, retain their unresolved records, and retry.
10. Bind every retained obligation to a source-aware provider and emit degradation for every selected capability that could not be retained.

### Optional obligations

An optional obligation is not the same as inactive evidence. It represents an enhancement that a selected target reaches but does not require for its core postcondition. Optional obligations are attempted only after critical and required obligations have a coherent solution. They cannot change or displace that solution, and their failure records degradation only when the selected objective requested the enhancement.

### Protect obligations, not first-selected versions

```text
runtime: framework >=4.2,<5
test:    plugin requires framework >=4.2,<4.4
```

If the runtime-only solve first chooses 4.4, the combined solve may choose 4.3. The protected object is `framework>=4.2,<5`, not provider version 4.4.

### Conflict ownership

A transitive conflict is attributed to the roots that introduced it:

```text
legacy-test-helper==1.0
    -> requires framework<4
    -> conflicts with runtime obligation framework>=4.2,<5
```

The constructor degrades the owning test obligation, not an arbitrary transitive provider or critical runtime root.

### Lock replay versus synthetic solve

Profiles may prefer exact committed lock replay when the lock matches the selected platform and target. The obligation model still matters: it determines which project/target the lock is intended to serve, explains missing target capabilities, and prevents fallback resolution from silently weakening critical intent.

## Project materialization

Project materialization means making the selected project runnable in the form expected by its target. It is broader than “install the project as a package.”

```text
ProjectReady
    +-- requires critical runtime providers
    +-- requires build/materialization prerequisites
    +-- executes ecosystem-specific materialization plan
    +-- verifies target-specific postconditions
```

### Packaging intent is optional

The architecture does not assume `pyproject.toml`, `package.json`, or any other single manifest exists.

A repository may declare packaging intent through legacy metadata, a workspace, build scripts, or an explicit target. A repository with no packaging intent may legitimately run in source-tree or project-root mode. That mode is explicit in the certificate rather than treated as a failed package install.

### Observable fallback

Predictive checks may select flags or add diagnostics but cannot silently suppress a target-relevant materialization attempt. Each attempted strategy records its command, result, and postcondition.

Failure can be non-fatal to image construction while still poisoning the relevant readiness lane. A shell-level `|| true` is acceptable only when a later consumed result records the failed postcondition.

### Python materialization strategies

For a packaged Python project:

1. install critical runtime/build providers;
2. attempt editable installation;
3. fall back to regular installation;
4. optionally use explicit source-tree mode when both fail or no packaging intent exists;
5. verify distribution metadata, imports, entry points, and selected plugin registration.

Source-tree mode does not claim package installation and records which packaging capabilities are unavailable.

### Node.js materialization strategies

For a Node.js project or workspace:

1. replay the selected package-manager lock when valid;
2. establish workspace and root-package links;
3. run target-reachable lifecycle/build steps according to policy;
4. verify module resolution, bin shims, built outputs, and workspace links;
5. record whether readiness came from a root package, linked workspace, or built artifact.

A Node root project need not be installed as a distribution into `node_modules`; the generic certificate therefore uses `project_ready` and `materialization_mode` rather than requiring `project_installed` for every ecosystem.

## Source-aware installation and certification

### One plan, multiple backends

Construction probes, final image/setup rendering, live repair, survivor replay, project materialization, and certification expectations must consume `EnvironmentPlan` rather than recreating requirement strings independently.

Backend-specific syntax is allowed. Semantic divergence is not.

### Source fidelity

Provider identity preserves, when applicable:

- ecosystem and registry/index identity;
- VCS URL and immutable revision;
- direct URL and artifact digest/integrity;
- local path or workspace member;
- subdirectory and editable/link mode;
- lockfile identity and peer-dependency context.

A source-qualified provider that cannot be installed safely remains unresolved or explicitly uninstallable. It must not become eligible for a public-registry install merely because a version was discovered.

Disabling a source feature for an experiment must protect transitive source overrides as well as source-qualified roots. A surviving parent dependency cannot silently resolve a public namesake.

### Exact certification

Certification uses ecosystem-native inventory and resolution evidence:

- Python distribution metadata and `direct_url.json` where applicable;
- Node lockfile `resolved`/`integrity`, package metadata, module resolution, workspace links, and peer context;
- artifact hashes or immutable revisions for direct artifacts;
- target-specific entry points and executables.

A matching name/version with a mismatched constrained source is a certification failure.

## Probe and repair loop

### Probe stages

Profiles implement the following logical stages:

1. exact provider inventory;
2. project materialization postconditions;
3. runtime command or import smoke check;
4. selected test-runner executable/import check;
5. selected test collection/listing;
6. optional test execution according to the evaluation objective.

Each stage consumes the same selected targets and environment plan and emits structured evidence.

### Failure classification

| Observation | Graph action |
|---|---|
| Missing provider during selected runtime startup/import | Add or promote a critical runtime obligation when mapping is proven |
| Missing provider during selected test collection | Add a required test obligation owned by that target when mapping is proven |
| Unknown runner argument with deterministic plugin ownership | Add a required plugin obligation |
| Project materialization postcondition absent | Attempt next strategy; record degradation |
| Source/integrity/revision mismatch | Repair from original provider identity |
| Compiler/header/build tool missing | Add a build obligation inheriting the owning path's criticality |
| Test assertions fail after valid collection | Preserve environment readiness unless separate evidence identifies an environment failure |

### Boundedness

Repair attempts are keyed by normalized targets, obligation set, provider identities, materialization mode, and failure signature. Repeating the same state and signature terminates the loop and preserves terminal evidence.

## Test collection completeness

A successful runner exit does not prove that the intended tests were discovered. Profiles record:

- exact selected command and working directory;
- collected/listed test identifiers when supported;
- collection errors;
- candidate suites reached by the selected target;
- reached locations with zero collected tests;
- exclusions attributable to missing providers, plugins, markers, or imports;
- comparison with benchmark-provided expected inventory when available.

Python profiles may use pytest collection. Node profiles may use Jest/Vitest listing or runner-specific discovery. When no gold inventory exists, the certificate reports evidence and confidence rather than inventing certainty.

Archipelago remains the standing Python sentinel: a small valid subset cannot certify full collection when most selected test worlds were silently skipped.

## Workspace and monorepo behavior

```text
Repository
    +-- Project/WorkspaceMember
    |       +-- RuntimeTarget(s)
    |       +-- BuildTarget(s)
    |       +-- TestTarget(s)
    |       +-- obligations
    +-- Project/WorkspaceMember
            +-- targets
            +-- obligations
```

Semantic declarations activate only for selected members. Nested requirement files, packages, or scripts are not repository-global roots.

Workspace members may satisfy obligations through local/editable providers. Those relationships survive into provider identity, environment planning, and certification.

Incompatible Python versions, Node versions, platforms, peer contexts, extras, dependency groups, or working directories create separate target variants. The correct result is multiple plans, not one compromised union.

## Ecosystem profile contract

Each ecosystem implements the same conceptual interfaces:

```python
class EcosystemProfile(Protocol):
    def discover_evidence(repo, context) -> EvidenceInventory: ...
    def discover_targets(evidence, objective) -> CandidateTargets: ...
    def activate_obligations(targets, evidence) -> tuple[RootObligation, ...]: ...
    def resolve(obligations, platform) -> ResolutionOutcome: ...
    def compile_plan(outcome, targets) -> EnvironmentPlan: ...
    def probe(plan, targets) -> ProbeEvidence: ...
    def certify(plan, targets, evidence) -> ReadinessCertificate: ...
```

The core owns invariants, outcome semantics, and readiness composition. Profiles own syntax and ecosystem mechanics.

### Python profile

Candidate evidence includes:

- `pyproject.toml`, `setup.py`, and `setup.cfg` semantic declarations;
- requirements and constraint files reached by selected commands or metadata;
- lockfiles and direct references;
- tox/nox sessions and explicit pytest commands;
- runner configuration and deterministic plugin implications;
- source imports, test imports, and controlled probe failures.

Python does not require `pyproject.toml`. Legacy packaging, requirements-only projects, and source-tree projects produce the same obligation shape with different evidence and materialization modes.

Example deterministic pytest implications include:

| Observed selected argument | Provider obligation |
|---|---|
| `--reuse-db` / `--nomigrations` | `pytest-django` |
| `-n` / `--numprocesses` | `pytest-xdist` |
| `--timeout` | `pytest-timeout` |

These mappings are maintained evidence, not general name guessing.

### Node.js profile

Candidate evidence includes:

- `package.json` semantic runtime dependencies and package scripts;
- npm, Yarn, pnpm, and workspace lockfiles;
- workspace topology and package-manager configuration;
- selected script command chains;
- Jest, Vitest, Mocha, and other runner configurations;
- source imports/module resolution and controlled probe failures.

`dependencies` versus `devDependencies` is evidence, not final role. For example, TypeScript in `devDependencies` becomes a critical build obligation when the selected runtime requires compiled output. ESLint in the same section remains inactive when no selected target reaches linting.

Node provider identity includes registry/source integrity, workspace/link identity, lock node, and peer-dependency context.

## Worked reference repository

Consider a Node.js/TypeScript repository:

```json
{
  "scripts": {
    "make-product": "tsc",
    "serve-product": "node dist/server.js",
    "launch-product": "pnpm run make-product && pnpm run serve-product",
    "check-product": "vitest run checking_places"
  },
  "dependencies": {
    "express": "^5",
    "pg": "^8",
    "@shop/shared": "workspace:*"
  },
  "devDependencies": {
    "typescript": "^5",
    "vitest": "^3",
    "supertest": "^7",
    "eslint": "^9"
  }
}
```

The caller selects:

```text
RuntimeTarget: pnpm run launch-product
TestTarget:    pnpm run check-product
```

The unusual script and directory names do not matter because the caller selected concrete commands and their command chains establish reachability.

### Activation paths

```text
RuntimeTarget: launch-product
    -> executes pnpm run make-product
    -> reaches BuildTarget: make-product
    -> make-product executes tsc
    -> activates typescript as BUILD/CRITICAL
    -> then executes serve-product
    -> serve-product executes node dist/server.js

Selected project semantic runtime contract
    -> activates express as RUNTIME/CRITICAL
    -> activates pg as RUNTIME/CRITICAL
    -> activates @shop/shared as RUNTIME/CRITICAL

TestTarget: check-product
    -> executes vitest run checking_places
    -> activates vitest as TEST/REQUIRED
    -> selected test imports supertest
    -> activates supertest as TEST/REQUIRED

eslint
    -> no path from a selected target
    -> remains inactive candidate evidence
```

### Obligation and provider graph

```text
ProjectReady: shop-api
|
+-- RuntimeTarget: launch-product
|   |
|   +-- Runtime obligation: express ^5 [CRITICAL]
|   |       +-- provider: express@5.1.0 [npm + integrity]
|   |               +-- router
|   |               +-- body-parser
|   |
|   +-- Runtime obligation: pg ^8 [CRITICAL]
|   |       +-- provider: pg@8.x [npm + integrity]
|   |
|   +-- Runtime obligation: @shop/shared workspace:* [CRITICAL]
|   |       +-- provider: workspace packages/shared
|   |
|   +-- BuildTarget: make-product
|           +-- Build obligation: typescript ^5 [CRITICAL]
|                   +-- provider: typescript@5.x
|
+-- TestTarget: check-product
    |
    +-- requires ProjectReady
    +-- Test obligation: vitest ^3 [REQUIRED]
    |       +-- provider: vitest@3.x
    +-- Test obligation: supertest ^7 [REQUIRED]
            +-- provider: supertest@7.x

Inactive candidate evidence
    +-- eslint ^9
```

### Successful environment plan

```text
1. Replay the exact pnpm lock for the selected workspace/platform.
2. Establish workspace links.
3. Verify source, integrity, lock identity, and peer context.
4. Run the reached build target: pnpm run make-product.
5. Verify dist/server.js and application module resolution.
6. Probe pnpm run launch-product.
7. Verify Vitest and selected test dependencies.
8. List/collect pnpm run check-product tests.
9. Emit the readiness certificate.
```

```text
runtime_ready           = true
build_ready             = true
project_ready           = true
materialization_mode    = built-workspace
test_runner_ready       = true
collection_ready        = true
environment_degraded    = false
target_selection_assumed = false
```

### Required-test conflict

Suppose `legacy-test-helper@1.0` is reached from `check-product` and requires `express<4`.

```text
Test obligation: legacy-test-helper@1.0 [REQUIRED]
    -> express<4
    -> conflicts with runtime obligation express^5 [CRITICAL]
```

The constructor keeps the Express runtime obligation, degrades the attributable test obligation, and records:

```text
runtime_ready        = true
project_ready        = true
collection_ready     = false if the helper blocks collection
environment_degraded = true

reason:
  check-product requires legacy-test-helper@1.0,
  which requires express<4 and conflicts with runtime express^5
```

## Failure semantics

| Condition | Useful environment? | Runtime result | Target result |
|---|---:|---|---|
| Critical closure unsatisfiable | Diagnostic salvage only | Failed | Failed |
| Runtime valid, selected required root conflicts | Yes | May pass | Degraded with attribution |
| Preferred materialization fails, fallback passes | Yes | Pass with recorded fallback | Continue |
| No package metadata, legitimate source/root mode passes | Yes | May pass | Continue with explicit mode |
| Runner missing | Yes | Unaffected | Failed |
| Required plugin missing | Yes | Unaffected | Failed |
| Only partial intended collection occurs | Yes | Unaffected | Incomplete/degraded |
| Tests collect and assertions fail | Yes | Unaffected | Environment may be ready; test result fails separately |
| Provider matches name/version but not constrained source | Repair required | Failed until exact | Failed until exact |
| Target chosen only from low-confidence naming fallback | Possibly | Evidence-dependent | Selection remains assumed |

## Architecture decisions and trade-offs

### ADR-1: Add an intent plane instead of replacing the package graph

**Decision:** Change the root-selection and resolver contracts to preserve typed obligations, then bind them to the existing provider graph.

**Alternatives considered:**

- reconstruct intent after resolution;
- create a completely separate persistent graph system;
- encode criticality in existing graph layer/edge-strength fields.

**Why:** Post-hoc reconstruction is too late, a second graph duplicates state, and existing topology fields answer different questions. The smallest useful change is `select_roots() -> RootObligation[]` plus an explicit `ResolutionOutcome`.

**Accepted cost:** Root producers, resolver error handling, serialization, and tests must carry new fields.

**Revisit trigger:** The provider graph itself gains a solver-native labeled-requirement model with equivalent semantics.

### ADR-2: Target reachability instead of naming-based activation

**Decision:** Names produce candidates only. Active obligations require semantic ownership, selected-target reachability, or proven observed evidence.

**Alternatives considered:**

- maintain allowlists of dev/test/lint group names;
- infer roles from conventional file and directory names;
- install every plausible candidate to maximize coverage.

**Why:** B2 demonstrated that predicted names over-select unrelated tooling and can damage runtime resolution. Nonconventional repositories also invalidate the assumption in the opposite direction.

**Accepted cost:** Some repositories remain ambiguous without an explicit caller target. Command interpretation and controlled probes add complexity.

**Mitigation:** Preserve candidate inventories, confidence, and assumed-target state; ask the harness/caller for intent whenever possible.

**Revisit trigger:** A new semantic ecosystem standard makes a particular declaration authoritative rather than conventional.

### ADR-3: First-class execution-target variants instead of maximal environments

**Decision:** Model runtime, build, test, and tooling objectives as explicit targets and construct compatible variants independently.

**Alternatives considered:**

- union every CI job, dependency group, and test runner;
- install only a generic runner;
- infer one repository-global environment.

**Why:** A maximal union creates unrelated conflicts; a bare runner under-installs; repository-global environments fail monorepos and version matrices.

**Accepted cost:** Multiple targets may require multiple environment plans and builds.

**Revisit trigger:** Compatible targets have provably identical obligation/provider sets and can be deduplicated.

### ADR-4: Critical proof followed by a fresh combined solve

**Decision:** Prove the critical contract, then solve critical plus required obligations from scratch. Degrade only attributable required roots when no combined solution exists.

**Alternatives considered:**

- freeze the initial critical provider versions;
- solve everything once and drop any root;
- always separate runtime and test environments.

**Why:** Freezing rejects valid version adjustments; priority-blind dropping destroys runtime correctness; forced separation duplicates work and can diverge from the application environment tests must exercise.

**Accepted cost:** Conflict attribution may require additional solver calls when labeled unsatisfiable cores are unavailable.

**Revisit trigger:** Ecosystem solvers expose reliable labeled cores or native weighted preferences.

### ADR-5: Observable project materialization instead of predictive suppression

**Decision:** Attempt ecosystem-appropriate materialization strategies and certify their postconditions. Predictive checks cannot silently suppress a target-relevant attempt.

**Alternatives considered:**

- skip attempts predicted to fail;
- rely universally on source-path injection;
- make every attempt failure fatal to image construction.

**Why:** Predictive suppression caused the pre-commit regression. Source-path execution is not equivalent to packaging/linking. Fatal image behavior discards useful diagnostic environments.

**Accepted cost:** Some doomed attempts consume time and logs.

**Revisit trigger:** A detector is proven against the regression corpus with no false-negative suppression and still retains explicit target override.

### ADR-6: Source-aware environment plan instead of patched emitters

**Decision:** All mutation and certification paths consume one structured plan whose provider identity includes source and ecosystem context.

**Alternatives considered:**

- patch each command emitter independently;
- certify name/version only;
- trust that resolver source metadata survives downstream automatically.

**Why:** Fragmented emitters repeatedly erase VCS, URL, private registry, workspace, integrity, and peer-context semantics.

**Accepted cost:** Existing build, probe, repair, and replay paths need a shared intermediate representation.

**Revisit trigger:** One external lock-replay mechanism owns all mutation and provides equivalent exact-source inventory guarantees.

### ADR-7: Language-agnostic core with ecosystem profiles

**Decision:** Keep targets, obligations, outcomes, plans, and readiness semantics in the core; delegate evidence syntax, resolution, materialization, and probing to profiles.

**Alternatives considered:**

- copy the Python pipeline for each language;
- force Python packaging concepts onto Node.js;
- design an abstract universal solver.

**Why:** The safety policy is shared, while package managers and project materialization differ materially. Profiles avoid both duplication and false uniformity.

**Accepted cost:** Profile interfaces and conformance tests are required. Some fields remain ecosystem-specific.

**Revisit trigger:** Repeated profile differences show that a supposedly core concept has no stable cross-ecosystem meaning.

### ADR-8: Lane-based readiness instead of one success bit

**Decision:** Certify runtime, build, project materialization, runner, collection, test result, degradation, and target-selection confidence separately.

**Alternatives considered:**

- use image-build success;
- use test-runner exit status;
- use installed package count.

**Why:** Each proxy has produced false success or false failure. Separate lanes retain useful partial outcomes and identify the correct repair scope.

**Accepted cost:** Callers must define which readiness composition satisfies their objective.

**Revisit trigger:** Higher-level products may add summaries but must retain the underlying lanes.

## Integration seams

### Core seams

| Area | Responsibility |
|---|---|
| Ecosystem base protocol | Evidence, target, obligation, resolver, plan, probe, and certificate interfaces |
| Shared environment models | Execution targets, obligations, activation paths, providers, outcomes, plans, certificates |
| Evaluation adapter | Supply explicit runtime/test objectives and consume readiness lanes |
| Plan backends | Render or execute the same environment semantics |

### Python profile seams

| Current area | Expected change |
|---|---|
| `src/python_deps/models.py` | Add/adapt Python evidence and normalized requirement types to shared contracts |
| `src/python_deps/evidence.py` | Produce candidate evidence without activating by name |
| `src/python_deps/depgraph/roots.py` | Return typed obligations and activation paths instead of bare tuples |
| `src/python_deps/depgraph/resolve.py` | Critical proof, fresh combined solve, conflict attribution, explicit outcome |
| `src/python_deps/depgraph/resolve_errors.py` | Structured labeled conflicts |
| `src/python_deps/depgraph/resolve_lock.py` | Preserve source-aware lock providers |
| `src/python_deps/depgraph/schema.py` and `ids.py` | Source-aware provider identity and obligation bindings |
| `src/python_deps/depgraph/build.py` | Build provider graph from resolved bindings; do not reconstruct root role afterward |
| `src/python_deps/depgraph/populate.py` | Compile resolution into the shared environment plan |
| `src/python_deps/depgraph/build_script.py` | Render the plan without reconstructing intent |
| `src/python_deps/depgraph/emit.py` and `block.py` | Execute/replay plan steps rather than emitting bare public-index installs |
| `src/python_deps/depgraph/probe.py` | Emit target- and phase-owned observations |
| `src/python_deps/depgraph/certify.py` | Exact source-aware lane certification |

The Node.js profile should implement the shared protocol in the ecosystem provider boundary rather than importing Python-specific graph types.

No config/service classification or provisioning changes are required by this design.

## Migration strategy

### Phase 0: Stabilize the measurement baseline

- Restore the intended flag state.
- Finish B5's observable non-fatal materialization correction.
- Keep the detached-runner race fixed before scored concurrent runs.
- Capture construction and collection sentinels.

### Phase 1: Introduce the intent model in shadow mode

- Add shared `ExecutionTarget`, `RootObligation`, `ActivationPath`, and `ResolutionOutcome` models.
- Adapt existing Python roots into obligations without changing resolver behavior.
- Compare old bare roots against new active/candidate inventories.
- Prove that every active root has an activation path.

### Phase 2: Protect the critical contract

- Implement critical-only proof and the fresh combined solve.
- Replace priority-blind root dropping with owned conflict degradation.
- Preserve every unresolved critical/required obligation in the outcome.

### Phase 3: Replace naming activation with target reachability

- Accept explicit runtime and test objectives from the evaluation adapter.
- Trace semantic and command-consumption relationships.
- Demote generic dev/lint/test naming allowlists to candidate discovery.
- Record ambiguous and fallback-selected targets.

### Phase 4: Materialize and certify capabilities

- Introduce generic `project_ready` and materialization modes.
- Route Python editable, regular, and source-tree strategies through the plan.
- Add lane-based readiness and collection completeness.

### Phase 5: Unify source-aware mutation

- Add complete provider source identity.
- Route construction probe, image render, live repair, replay, and certification through one plan.
- Close public-index escape paths for source-qualified providers.

### Phase 6: Add the Node.js profile

- Implement package/lock/workspace evidence adapters.
- Add script-command target reachability.
- Prefer exact package-manager lock replay.
- Add workspace, peer-context, integrity, build-output, module-resolution, and test-listing certification.

### Phase 7: Generalize workspaces and native/build prerequisites

- Produce independent plans for incompatible member/target variants.
- Carry owning-path criticality onto native/build prerequisites.
- Extract only abstractions proven common by both profiles.

## Validation strategy

### Characterization tests

Capture current behavior for:

- root flattening and first-wins deduplication;
- runtime/test meaning added after resolution;
- name-based group activation;
- root dropping after conflicts;
- project-install gating and fallback;
- source-qualified provider rendering;
- name/version certification;
- test-command and plugin discovery.

### Core conformance tests

Every profile must prove:

- a naming convention alone cannot activate an obligation;
- every active root has an owner and activation path;
- duplicate declarations retain all evidence;
- critical obligations cannot enter a dropped set;
- a fresh combined solve may change provider versions within constraints;
- conflicts are attributed to owning roots rather than arbitrary transitive providers;
- unresolved obligations remain in outcomes and certificates;
- unselected target evidence exerts no solver pressure;
- incompatible target variants produce separate plans;
- repeated repair states terminate.

### Python integration sentinels

| Sentinel | Required assertion |
|---|---|
| aiida-core | Optional/test/tooling candidates cannot collapse the critical runtime closure |
| anthropic-sdk | A selected required test root cannot disappear without degraded test readiness |
| pre-commit | Project installation is attempted; successful editable or regular materialization is retained |
| websockets | A failing preferred materialization mode does not discard a useful environment |
| addons-server | Selected `--reuse-db` evidence activates `pytest-django` |
| slither | An unrelated pytest plugin is not activated through broad dev evidence |
| Archipelago | Partial collection does not certify full selected-target readiness |
| Direct/VCS source sentinel | Installed provider exactly matches constrained source |

### Cross-ecosystem reference cases

- A Python repository without `pyproject.toml` produces obligations from legacy semantic metadata or target consumption.
- A Python repository with no packaging metadata can certify explicit source-tree mode without pretending it is installed.
- A repository with nonconventional filenames activates requirements through an explicit command chain.
- A Node dependency in `devDependencies` becomes critical build infrastructure when the runtime target reaches it.
- An unselected Node lint dependency remains inactive despite sharing `devDependencies` with test/build tools.
- A Node workspace provider preserves link identity, integrity, lock node, and peer context.

### Regression gates

For each affected phase:

1. run construction-only evaluation first;
2. compare active/candidate obligation counts, critical closure size, dropped roots, and activation reasons;
3. build and collect at resource-safe concurrency;
4. inspect sentinel deltas and aggregate EBSR/collection metrics;
5. reject gains that depend on harness-generated phantom failures, name-only activation, or unrecorded degradation.

## Observability

Every constructed target emits a machine-readable report containing:

- caller objective and candidate targets;
- selected targets, variants, evidence, and confidence;
- candidate evidence left inactive;
- every typed root obligation and activation path;
- critical-only and combined solver outcomes;
- unresolved/dropped obligations and conflict paths;
- source-aware provider identities and bindings;
- environment-plan steps and observed postconditions;
- materialization attempts and selected mode;
- repair attempts and deduplication keys;
- readiness certificate and collection inventory.

Aggregate evaluation reports at least:

- explicit versus assumed target-selection rate;
- active versus candidate-only declaration counts;
- repositories with unresolved critical obligations;
- critical closure size deltas;
- selected required obligations retained/dropped;
- project materialization-mode rates;
- source-fidelity failures;
- runtime-, runner-, and collection-ready rates;
- partial-collection rate;
- degraded-but-runtime-ready rate.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| No caller supplies an execution target | Return candidates and confidence; use an explicit fallback policy without claiming authoritative selection |
| Static command tracing cannot understand dynamic scripts | Bound interpretation and use controlled execution observations |
| A naming heuristic leaks into activation | Require and validate an `ActivationPath` for every active root |
| Semantic declaration belongs to an unselected workspace member | Scope activation by selected project/member ownership |
| Import evidence promotes a local or optional module incorrectly | Require proven origin and high-confidence provider mapping |
| Conflict isolation requires many solver calls | Cache normalized solve inputs and use native labeled cores where available |
| A required root is dropped when another provider version would work | Always perform a fresh combined solve before degradation |
| Non-fatal materialization hides failure | Consume postconditions into readiness and degradation |
| Source identity leaks through one legacy emitter | Route every mutation backend through the plan and add conformance tests |
| Cross-language abstraction becomes Python-shaped | Require Node profile conformance and move ecosystem mechanics out of core |
| Multiple targets multiply image cost | Deduplicate provably identical plans; keep incompatible variants separate |
| No gold collection inventory exists | Report evidence/confidence without claiming certainty |

## Open implementation decisions

- What caller contract supplies runtime and test objectives to the ecosystem provider?
- Which command formats can be interpreted statically, and when should controlled tracing begin?
- What minimum evidence qualifies an activation path as strong versus assumed?
- Which resolver APIs expose labeled unsatisfiable cores?
- How are compatible duplicate source constraints merged?
- When should Python or Node lock replay be preferred over synthetic solving?
- Which project-readiness probes are safe and deterministic for libraries, CLIs, and services?
- How is collection completeness scored without a benchmark inventory?
- Which shared model package can be adopted without forcing Python types onto Node?

## Recommended implementation sequence

1. Add shared `ExecutionTarget`, `RootObligation`, `ActivationPath`, and `ResolutionOutcome` contracts.
2. Convert Python `select_roots()` output to obligations in shadow mode while preserving current behavior.
3. Implement critical-undroppable resolution and persistent unresolved outcomes.
4. Thread explicit runtime/test objectives from the evaluator and implement target reachability.
5. Remove name-based activation; keep conventions only for candidate discovery.
6. Add project materialization modes and lane-based readiness.
7. Introduce the source-aware `EnvironmentPlan` and route every mutation path through it.
8. Add collection completeness and workspace/member target variants.
9. Implement the Node.js profile against the same conformance suite.
10. Generalize native/build obligations only after Python and Node behavior validates the core boundary.

The current B2 restore, B5 capstone correction, and detached-runner race fix should finish before the next scored baseline. They are prerequisites for trustworthy measurement, not substitutes for this sequence.

## Acceptance checklist

- [ ] The intent plane is distinct from existing graph `Layer` and edge `Strength` semantics.
- [ ] Every selected objective is represented as an explicit execution target with owner and variant.
- [ ] Names and conventions create candidates only; they cannot directly activate obligations.
- [ ] Every active root has role, criticality, owner, evidence, confidence, and an activation path.
- [ ] Semantic declarations activate only for the selected project or workspace member.
- [ ] Duplicate roots preserve all evidence and do not use first-wins loss.
- [ ] No successful runtime certificate contains an unresolved or dropped critical obligation.
- [ ] The combined solve can reselect provider versions within protected constraints.
- [ ] A conflicting required target obligation degrades that target without collapsing runtime readiness.
- [ ] Unresolved obligations remain visible in outcomes and certificates.
- [ ] Unselected lint, typing, docs, benchmark, and release evidence exerts no solver pressure.
- [ ] Project readiness uses an explicit ecosystem materialization mode and observed postconditions.
- [ ] Python projects do not require `pyproject.toml` or conventional directory names.
- [ ] Node roles are not inferred solely from `dependencies` versus `devDependencies`.
- [ ] All install, link, build, repair, and replay paths consume one source-aware environment plan.
- [ ] A versioned but uninstallable source-backed provider cannot escape into a public registry install.
- [ ] Source overrides remain protected when reached transitively.
- [ ] Certification proves ecosystem-appropriate source, integrity, workspace, and project-ready state.
- [ ] Runtime, build, project, runner, collection, test result, degradation, and target confidence are separately reported.
- [ ] Collection completeness identifies empty or partial selected-target coverage.
- [ ] Repair is evidence-driven, target-owned, and bounded.
- [ ] Python and Node profiles pass the shared core conformance tests.
- [ ] Real-repository regression gates pass without harness-generated phantom failures.
