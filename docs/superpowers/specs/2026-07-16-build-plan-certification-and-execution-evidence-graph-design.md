# Build-Plan Closure, In-Run Certification, and Execution-Evidence Graph Growth

**Date:** 2026-07-16  
**Status:** Discussion synthesis and proposed architecture; not yet an implementation specification

## Purpose

This note consolidates three related ideas:

1. A build-script run validates the environment plan we already know about.
2. A subsequent pytest run asks whether that valid plan is sufficient for the repository's real execution target, and may discover requirements that were not visible earlier.
3. New execution evidence should incrementally enrich the environment graph without turning every error message into an assumed dependency.

It also compares this design with the external **EnvGraph: Environment Alignment for Repository-Level Code Generation** paper and repository. EnvGraph is useful primarily as an outer-loop philosophy: execute, normalize evidence, ground the failure, diagnose its layer, revise the appropriate artifact, and validate again. Our graph and repair variable are more environment-specific.

This document complements rather than replaces:

- `docs/superpowers/specs/2026-07-14-error-node-grounding-design.md`
- `docs/superpowers/specs/2026-07-14-runtime-test-environment-construction-graph-design.md`
- `docs/superpowers/handoffs/2026-07-14-graph-guided-repair-state-of-play.md`

## 1. Two different questions: plan validity and target sufficiency

The build and test phases should not be understood as two interchangeable ways of checking the same thing.

### Build-script run: is the current plan valid?

The compiled build script materializes the environment plan currently represented by the graph. Its job is to prove that the known plan can be executed and that its stated postconditions hold in one final environment.

For example, if the current plan says:

- install an OS package providing `libGL.so.1`;
- install a particular Python distribution from an approved source;
- expose the repository package on `sys.path`;
- make a named command available;

then the build run should both perform those actions and certify those exact outcomes. A successful run means the existing plan is materially coherent in the target container. It does **not** mean that the plan contains every requirement the tests will reveal.

This is best described as **known-plan closure**:

> Every active hard requirement already represented in the plan was materialized, reconciled with the final environment state, and certified using an appropriate probe.

### Pytest run: is the valid plan sufficient for the target?

Pytest exercises a repository target that may traverse code paths, imports, plugins, native libraries, tools, services, data files, or configuration that static inspection and the initial plan did not expose.

A pytest failure can therefore have two very different meanings:

- **Environment insufficiency:** the current plan was valid, but the target revealed a new environment obligation.
- **Repository logic failure:** the environment is sufficient for the reached target, but the code or assertion is wrong.

Pytest is thus not simply a stronger certificate for the same fixed graph. It is also a source of new evidence that can extend the graph's model of the required environment.

The relationship is:

```text
current graph
    -> compile environment plan
    -> build and certify that plan
    -> execute repository target
       -> pass: sufficient for this target
       -> environment failure: extend/refine graph and rebuild
       -> logic failure: do not invent another installation
```

The distinction prevents two common errors:

- declaring the repository ready merely because all known packages installed; and
- responding to every test failure by adding another package or setup action.

## 2. Certification belongs inside the compiled build execution

Certification should be part of the build artifact's execution semantics rather than a detached, optional phase. However, it must remain distinguishable from the agent-authored setup commands and must be owned by trusted code.

### Why a check after each action is not sufficient

Immediate post-action checks are valuable because they produce precise attribution:

```sh
pip install A       # installs C==1
check A             # passes here

pip install B       # upgrades C to 3
                    # A may now be broken
```

The earlier certificate described an intermediate state, not the final environment. Package installers, linkers, path changes, environment variables, generated configuration, and later build steps can all invalidate earlier observations.

The build therefore needs two levels of certification:

1. **Local postconditions**, immediately after consequential actions, for attribution and early failure.
2. **Final global reconciliation**, after all mutations, to prove that all active requirements coexist in the resulting environment.

Local checks answer “did this action appear to work?” Final checks answer “does the complete plan still hold after every action has run?”

### Proposed compiled artifact

Conceptually, the compiler should assemble:

```text
agent-authored setup body
        +
compiler-owned action instrumentation
        +
compiler-owned final certification footer/harness
        =
trusted compiled build execution
```

The agent may revise the setup body or structured plan. It should not be able to remove the final checks, narrow their scope, append `|| true`, replace a probe with `echo success`, or certify a similarly named artifact from the wrong source.

One possible separation is:

```text
setup-body.sh              # repairable materialization actions
environment-plan.json      # requirements, intended providers, probes
certify-plan.py            # trusted certificate evaluator
compiled-build.sh          # generated wrapper executed in a fresh container
```

This is “certification inside the build-script run” in the semantic sense: one build execution produces both materialization results and a final certificate. It is not a grant for generated shell text to define what counts as success.

### Required certification layers

The final harness should evaluate, where applicable:

- **Plan coverage:** every active hard requirement has a selected materializer and probe.
- **Action completion:** the intended commands completed without masked failures.
- **Package reconciliation:** installed versions and dependency constraints are globally consistent.
- **Source identity:** the installed distribution or artifact came from the intended provider/source, not merely a matching name.
- **Import/capability checks:** required imports, commands, shared libraries, runtimes, and services are actually usable.
- **Repository accessibility:** the intended local package/module is importable from the planned location.
- **Functional smoke probes:** narrow execution checks where presence alone is not meaningful.
- **Certificate completeness:** every hard requirement has a final-state result; missing results are failures, not implicit passes.

Structured markers such as `BEGIN_ACTION`, `END_ACTION`, `BEGIN_CHECK`, and `END_CHECK` should associate output with graph nodes and attempts. The repair loop should consume structured records where possible and retain raw log spans for forensic grounding.

## 3. Position in the agent repair pipeline

The repair controller should treat the build script as a repairable plan implementation and the certificate harness as a trusted judge of that implementation.

### Inner loop: make the existing plan valid

```text
derive graph requirements and provider choices
    -> compile plan and trusted certificate harness
    -> reset to a fresh target container
    -> execute the entire compiled build
    -> reconcile the final state
```

If installation, compilation, or certification fails, the build-plan repair agent operates on this inner loop. Its task is to make the **existing plan** completely materializable without weakening the certificate.

Examples include:

- choosing the correct system package before building a wheel;
- fixing action order;
- resolving an incompatible version choice;
- using the repository itself instead of a namesake registry package;
- adding a missing compiler already demanded by the known native-build path;
- correcting paths, environment variables, or installation flags.

Each attempted repair is recorded against the implicated graph frontier, then evaluated from a clean environment. The system should not treat a mutated, partially repaired container as conclusive evidence that a plan is reproducible.

### Outer loop: discover whether the plan is sufficient

Once the build plan is certified, the controller runs the real target—initially pytest collection and then the selected tests or full suite, depending on the task.

The result is classified before repair:

- collection/setup/import/native-load/tool/config/service failures may supply new environment evidence;
- test assertion and application-logic failures normally belong to repository repair, not environment expansion;
- ambiguous failures remain observations until stronger evidence resolves their layer.

When target execution reveals a new environment obligation, the controller adds evidence to the graph, promotes an obligation only when justified, recompiles the plan, and returns to the clean build loop. This is how pytest **extends** the environment model rather than bypassing it.

A complete readiness verdict therefore requires both:

1. a final-state certificate for every active environment requirement; and
2. evidence that the intended repository target was actually reached and passed at the required scope.

## 4. Incremental graph growth from execution evidence

The graph should grow from evidence, but graph structure and current environment state have different lifecycles.

### Persistent, append-oriented information

The following should normally survive across repair cycles:

- repository intent and topology;
- known capability and provider identities;
- candidate and confirmed dependency edges, with provenance;
- normalized error identities and raw sightings;
- repair attempts and outcomes;
- refutations or demotions of earlier hypotheses;
- certificate history.

This information forms the investigation record. A failed hypothesis should be marked as refuted or superseded, not silently removed.

### Per-cycle, rederived information

The following describes a particular fresh container and should be replaced or rederived on each run:

- installed package versions and origins;
- command availability;
- importability;
- loaded or missing shared libraries;
- service/configuration state;
- currently active failures;
- which checks and targets were reached.

This yields a useful rule:

> Graph knowledge is mostly append-oriented; environment state is snapshot-oriented.

Without this separation, stale installed-state facts can survive a container reset, while valuable failed-attempt evidence can accidentally disappear.

### Error nodes are observations, not requirements

Every meaningful execution failure should first become an observation node, even if it cannot yet be mapped:

```text
error:unbound:<stable-hash>
```

An error node should retain at least:

- normalized identity and failure kind;
- phase and target;
- file/module/command or other anchor, when available;
- cycle and sighting count;
- raw log span or traceback reference;
- blast radius, such as affected test files or collection targets;
- attempted repairs and their outcomes;
- whether it was seen this cycle;
- evidence that later resolved or refuted it.

Normalization should remove volatile paths, line numbers, timestamps, and container IDs where they do not change the failure's identity.

Crucially:

```text
ModuleNotFoundError: No module named 'PIL'
```

proves that capability `import:PIL` failed at that point. It does not by itself prove that distribution `Pillow` is absent, nor authorize installing a registry package named `PIL`. The graph must ground the capability and then establish a provider edge from stronger evidence.

### Grounding order

Ground new errors conservatively, using the strongest available anchor:

1. the repository's own package/module identity;
2. exact ownership from a command, loader record, traceback, or installed metadata;
3. an existing capability node such as an import, command, shared library, runtime, service, or configuration key;
4. the test/collection target that exposed the failure;
5. an unbound error node when no exact mapping is available.

Provider and prerequisite edges should come from sources such as:

- installed distribution metadata and `packages_distributions()`;
- resolver, lockfile, wheel, or package-manager metadata;
- compiler/linker/loader diagnostics;
- a curated high-confidence mapping, explicitly marked as a candidate if not yet verified.

An illustrative grounded path is:

```text
test-target
  -> collects test_file.py
  -> requires import:cv2
  -> provided_by pkg:opencv-python        [present]
  -> runtime_requires syslib:libGL.so.1   [missing]
  -> candidate_provider apt:libgl1
```

The current environment graph does not need to turn every file and Python module into a first-class node immediately. File/module context can remain on the error and target records until it materially improves provider resolution or causal navigation.

### Applying an evidence delta

For each new execution event, the graph update should proceed as follows:

1. Preserve the raw event and execution context.
2. Create or update the stable error node.
3. Anchor it to an existing target/capability without guessing a provider.
4. Add a capability node if its identity is exact and not already represented.
5. Add only evidence-backed provider or prerequisite edges.
6. Keep newly inferred requirements soft/candidate until the promotion rule is satisfied.
7. Re-resolve providers and compile a new plan when a hard obligation is promoted.
8. Certify the obligation in the next clean build cycle.
9. Propagate readiness over active hard edges.

State propagation must distinguish **direct absence** from **downstream blockage**. If package `C` is missing and packages `A` and `B` require it, `C` is `MISSING`; `A` and `B` are `BLOCKED`. They should not all be reported as independently missing.

Likewise, an error not appearing in a later log is not automatically resolved. Resolution requires comparable positive evidence: the same target or capability was reached and succeeded. If execution never reached that point, its status is “not observed this cycle.”

### What the agent should see

The diagnostician does not need an undifferentiated dump of the entire graph. A failure-scoped rendering should show:

- the current failure and raw evidence;
- the shortest grounded causal path currently supported by evidence;
- relevant final-state certificates;
- the unresolved repair frontier;
- prior failed attempts and refuted hypotheses;
- downstream blocked nodes or affected targets;
- uncertainty where the provider or layer is not established.

This turns the graph into structured diagnostic context. The agent still reasons and proposes a repair, while the controller and certificates decide whether the proposed change is admissible and successful.

## 5. Relationship to the external EnvGraph work

### References

Local materials examined for this discussion:

- Paper: `/Users/john/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/2.0b4.0.9/692cbbcfa7bbf944173508d20ffb0845/Message/MessageTemp/1508a32d352f2d10c8642cf8dfe1e0fe/File/EnvGraph__Environment_Alignment_for_Repository_Level_Code_Generation.pdf`
- Code archive: `/Users/john/Downloads/EnvGraph (1).zip`
- Archive project root: `method/envgraph/`
- External-graph builder: `method/envgraph/src/envgraph/graph/builder.py`
- Repository-graph builder: `method/envgraph/src/envgraph/graph/repo_builder.py`
- Failure router: `method/envgraph/src/envgraph/reasoners/failure_router.py`
- Graph prompt renderer/reasoner: `method/envgraph/src/envgraph/reasoners/graph_reasoner.py`
- Execution/verification runner: `method/envgraph/src/envgraph/verifier/runner.py`
- Provided ablation configuration: `method/rq2/ablation.yaml`

### EnvGraph's philosophy

The paper presents repository-level generation as an iterative environment-alignment problem. Its conceptual loop can be summarized as:

```text
BuildState
  -> ExecRepo
  -> NormalizeEvidence
  -> GroundEvidence
  -> Diagnose
  -> Revise
  -> execute again
```

It describes two complementary graph layers:

- an **external graph** for project files, configuration, and package/dependency relationships;
- an **internal repository graph** for files, modules, symbols, and unresolved references.

Failures are routed among external dependency, internal reference, and residual logic categories, with external causes considered before internal and residual repairs. The framework wraps a backbone repository generator and intervenes at multiple points: pre-execution environment suggestion, deterministic post-execution routing, specialized repair prompts/subsystems, and validation or rollback. It is therefore more than a single static diagnosis paragraph appended to an ordinary agent prompt.

### Similarities to our work

The strongest shared principles are:

- diagnose the failure layer before changing anything;
- use real execution evidence to refine a structured model;
- iterate through execution and repair rather than relying on one-shot generation;
- distinguish external environment failures from local-reference and residual logic failures;
- preserve already-valid behavior while repairing the implicated frontier;
- provide a graph-derived, failure-scoped view to the repairing agent.

Our build/certify/pytest loop is an environment-setup specialization of that broader philosophy. The compiled build first aligns the known external environment; test execution then exposes additional alignment evidence or hands the problem to repository-logic repair.

### Important differences

EnvGraph's main repair variable is a **generated repository**. Our primary repair variable is a **reproducible environment plan and its compiled build script for an existing repository**.

Our proposed graph is consequently more explicit about:

- capabilities versus providers;
- Python, OS, toolchain, runtime, shared-library, service, and configuration nodes;
- exact package versions and source identity;
- selected versus candidate providers;
- hard obligations versus soft hypotheses;
- direct missing state versus derived blockage;
- final-state certificates and clean-container reproducibility;
- multi-language repository intent.

The useful transfer is the control-loop philosophy and graph-at-diagnosis boundary, not a literal port of EnvGraph's data model.

### Paper/code caveat

The released code should not be treated as a complete implementation of every abstraction described in the paper.

In the examined archive:

- the external builder initially treats discovered top-level imports as package-like nodes, so standard-library and repository-local imports can appear as missing candidates before later reasoning cleans them up;
- the internal graph is AST/heuristic based and does not robustly model all package-root and `src/`-layout semantics;
- the runtime failure router relies primarily on regex buckets, unresolved-reference counts, and a fixed priority rather than a clearly implemented general `GroundEvidence` stage;
- graph information is used for used-versus-declared comparisons, counts, and compact one-hop prompt summaries, but the released path does not demonstrate general edge-traversal causal debugging;
- the supplied ablation configuration does not expose all of the graph/router toggles one would want for independently reproducing the paper's claimed component effects;
- no substantive automated test suite was found in the archive.

A small local execution of its builders against its own source also showed the practical effect of these heuristics: the external graph initially marked many standard-library or local imports as missing, and the repository parser reported a parse error on one source file containing a byte-order mark.

These observations do not negate the paper's architectural contribution. They mean we should cite EnvGraph as conceptual precedent and empirical motivation while independently specifying and testing the stronger causal, provider, and certification semantics needed by our system.

## 6. Mapping onto the current codebase

The current implementation already contains several pieces of this architecture:

- `src/python_deps/depgraph/schema.py` defines the typed immutable graph, node states, edges, and attempts.
- `src/react_repair/loop.py` already follows a reset → run whole script → certify → pytest → enrich sequence.
- `src/python_deps/depgraph/build_script.py` is the natural build-plan compilation boundary.
- `src/python_deps/depgraph/diagnose.py` distinguishes environment, local, residual, invalid, and ambiguous modes.
- `src/python_deps/depgraph/graph_enrich.py` and `src/python_deps/depgraph/runtime_ingest.py` are the current execution-evidence ingestion path.

The main gaps relative to this note are:

- certification is not yet fully represented as a compiler-owned, final-state contract embedded in the build execution;
- raw traceback/log spans and blast radius need to survive ingestion;
- unmapped diagnostics must become durable unbound error nodes instead of being dropped;
- graph enrichment needs explicit delta, provenance, refutation, and lifecycle semantics;
- promotion from observation to hard environment obligation needs a conservative evidence rule;
- final readiness should combine environment closure with proof that the requested target was reached.

## 7. Concrete controller loop

The controller should send failures from both gates through the same evidence-normalization and diagnosis boundary, but it should not automatically send them to the same repair agent. Diagnosis determines whether the repair variable is the environment plan, repository code, or additional evidence gathering.

### Cycle boundary and container reset

For every new **environment-plan candidate**, the controller should:

1. Resolve the current graph into a structured environment plan.
2. Compile the setup body together with the trusted certification harness.
3. create a fresh container from the immutable base image;
4. execute the complete compiled build artifact;
5. evaluate its final-state certificate;
6. run the requested pytest scope only after certification passes.

The build script is executed rather than “installed.” Starting each candidate from the same base image prevents leftover packages, files, caches, path mutations, or services from making an incomplete plan appear reproducible.

The failed container may be retained temporarily for focused diagnostic probes. Those probes are evidence only; they cannot replace final validation from a clean base. The graph and attempt history persist outside the container.

### Gate-specific routing

Both build and pytest failures are normalized, appended to the graph, and diagnosed. Their likely repair destinations differ:

```text
build/action/certificate failure
  -> ground against plan action, requirement, provider, and certificate
  -> environment/build-plan repair agent
  -> produce a new plan candidate
  -> restart from the base image

pytest failure
  -> ground against target, phase, capability, and causal evidence
  -> classify before repair
       -> environment: extend/refine graph, re-resolve, rebuild from base
       -> repository logic: hand to code-repair agent; do not add packages
       -> ambiguous: execute a discriminating probe, then classify again
```

The environment repair agent should receive more than the raw exception. Its context should include:

- the failed action, probe, or pytest target;
- the relevant raw log/traceback span;
- the failure-scoped graph slice and supported causal path;
- the current structured plan and compiled action provenance;
- requirements that passed their final certificates;
- prior attempts and refuted hypotheses;
- the unresolved frontier it is allowed to repair.

### Controller pseudocode

```python
while attempts_remaining():
    plan = resolve_environment_plan(graph)
    artifact = compile_build_with_trusted_certification(plan)
    env = create_from_base_image()

    build_result = env.run(artifact)

    if not build_result.certified:
        evidence = normalize_build_evidence(build_result)
        graph = apply_evidence_delta(graph, evidence)
        diagnosis = diagnose(evidence, graph)

        if diagnosis.is_repairable_environment_failure:
            proposal = build_agent.repair(
                diagnosis=diagnosis,
                graph_slice=graph.failure_slice(diagnosis),
                current_plan=plan,
                previous_attempts=graph.attempts,
            )
            graph = record_plan_proposal(graph, proposal)
            continue

        return blocked_or_invalid_plan(diagnosis)

    test_result = env.run_pytest(target)

    if test_result.passed:
        return ready(
            build_certificate=build_result.certificate,
            test_evidence=test_result,
        )

    evidence = normalize_test_evidence(test_result)
    graph = apply_evidence_delta(graph, evidence)
    diagnosis = diagnose(evidence, graph)

    if diagnosis.layer == "environment":
        graph = promote_supported_obligations(graph, diagnosis)
        continue

    if diagnosis.layer == "repository":
        return handoff_to_code_repair(diagnosis, graph)

    if diagnosis.layer == "ambiguous":
        probe = select_discriminating_probe(diagnosis, graph)
        probe_result = env.run(probe)
        graph = apply_evidence_delta(graph, probe_result)
        continue
```

In an implementation, `continue` after an ambiguous probe means “diagnose the enriched evidence again,” not necessarily “promote an environment requirement.” A probe can establish that the failure belongs to repository logic.

### Fast feedback versus authoritative completion

Some intermediate work may reuse a certified container for speed:

- diagnostic probes may run in the failed container;
- targeted pytest reruns after a repository-code edit may reuse the environment when the environment plan is unchanged;
- collection, a focused test, and the full suite may be expanded progressively.

These are feedback optimizations. The authoritative completion path remains:

```text
fresh base image
  -> complete compiled build
  -> final-state certification
  -> required pytest target/scope
  -> pass
```

If a repository edit changes dependency declarations, build metadata, imports, native compilation, generated assets, or other environment-relevant inputs, it invalidates the prior plan certificate and immediately returns to the clean build loop.

## 8. Proposed invariants

The implementation should preserve these invariants:

1. **The build plan cannot define its own success.** Trusted probes and the controller own the certificate.
2. **A local certificate is not a final certificate.** All active requirements are rechecked after the last mutation.
3. **A certified plan may still be insufficient.** Target execution is the discovery and sufficiency boundary.
4. **An error is evidence, not an install instruction.** Provider choice requires grounding.
5. **No evidence is silently lost.** Unmapped errors, failed attempts, and refuted hypotheses remain queryable.
6. **State is cycle-scoped; knowledge is historical.** Fresh-container facts are rederived while provenance accumulates.
7. **Not seen is not resolved.** Comparable successful reachability is required.
8. **Missing and blocked are distinct.** Direct failure and downstream consequence must not be conflated.
9. **Every promoted hard obligation receives a final-state certificate.** Uncertified requirements prevent readiness.
10. **Environment repair stops at a certified logic boundary.** Once environment sufficiency is established for the reached path, the system must not continue speculative installation to hide a code failure.

## 9. Concise operational model

The resulting agent pipeline is:

```text
inspect repository and derive initial graph
  -> resolve a structured environment plan
  -> compile setup actions + trusted checks
  -> build from a clean environment
       -> action/check failure:
            repair the existing plan, then rebuild
       -> final certificate failure:
            reconcile conflicts or missing known requirements, then rebuild
       -> certificate passes:
            run pytest target
                -> target passes: ready at this scope
                -> new environment evidence:
                     append/ground evidence
                     promote justified obligation
                     re-resolve and rebuild
                -> repository logic failure:
                     hand off without expanding the environment plan
                -> ambiguous failure:
                     retain observation and gather a discriminating probe
```

In short: **the build loop closes the plan we have; the test loop challenges whether that plan is enough; execution evidence grows the graph; and trusted certification prevents either the agent or a later mutation from claiming success prematurely.**
