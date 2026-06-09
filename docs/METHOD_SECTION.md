# 3 Method

This section presents our method for automatically constructing executable
Docker environments for open-source repositories. The key design principle is
to avoid asking an LLM to synthesize a Dockerfile directly from repository
metadata. Instead, the system first lets an agent discover a working environment
inside an executable sandbox, records the evidence produced by that exploration,
compiles the evidence into a Dockerfile, and finally validates and repairs the
Dockerfile in a fresh replay environment.

## 3.1 Problem Formulation

Given a repository instance

$$
x = (R, c, m),
$$

where $R$ is a source repository, $c$ is a target commit, and $m$ denotes optional
benchmark metadata, let $R_c$ be the repository checked out at commit $c$. An
environment state is denoted by $S \in \mathcal{S}$, and a shell command sequence
is denoted by $C \in \mathcal{C}$. Executing commands changes the environment
state:
$$
\delta: \mathcal{S} \times \mathcal{C} \rightarrow \mathcal{S}.
$$

The task is to select a base image $B^* \in \mathcal{B}$ and generate a
replayable Dockerfile $D^*$ whose build process $P_{D^*}$ yields an environment
that satisfies a verifier $\epsilon$:

$$
B^* = I(R_c, m), \qquad
\epsilon(R_c, \delta(B^*, P_{D^*}), V) = 1.
$$

Here $V$ is the set of verification commands observed during sandbox
exploration. In the Repo2Run-style setting, $V$ is typically a pytest collection
command such as `pytest --collect-only -q --disable-warnings`, or its Poetry
variant. The verifier checks whether the repository's test infrastructure can
be effectively invoked in the constructed environment; it does not require all
test assertions to pass.

## 3.2 Overview

The system has five stages.

First, a repository-aware image selector chooses a base Docker image from a
language-specific candidate set. Second, a planner agent interacts with a
Docker sandbox using a single-step ReAct protocol. Each action is executed by
the host, and the resulting observation is recorded. Third, the system extracts
verified test commands and state-changing setup actions from the trajectory.
Fourth, a synthesizer converts the verified trajectory into a Dockerfile while
preserving command order and setup side effects. Finally, a fresh replay
validator rebuilds the Dockerfile and reruns the verification commands. If
replay fails, a bounded repair agent revises the Dockerfile using the sandbox
trajectory and replay logs.

This architecture turns environment construction into an evidence-grounded
search-and-replay problem. The LLM is used to guide exploration and resolve
ambiguous setup decisions, but the final artifact must be justified by commands
that were actually executed.

## 3.3 Repository-Aware Base Image Selection

The first challenge is selecting a base image that is compatible with the
repository's language, dependency manager, and platform assumptions. The image
selector analyzes the repository in two steps. It first uses the directory tree
to identify files that may affect environment construction, such as
`pyproject.toml`, `setup.py`, `requirements.txt`, `package.json`, `Cargo.toml`,
`go.mod`, `pom.xml`, CI workflows, lockfiles, and version files. It then reads
the candidate files and filters out files that do not affect language version,
dependency installation, build commands, or test execution.

After collecting relevant evidence, the selector infers the primary language
using the repository files and language-specific handlers. Each handler provides
a bounded set of candidate images and setup hints for its ecosystem. The LLM is
not allowed to choose an arbitrary image; it must select from the provided
candidate list. ~~**When the repository evidence suggests architecture-sensitive**~~
~~**dependencies, the selector records a platform override such as `linux/amd64`.**~~
~~**The same override is reused during sandbox execution, Docker build, and Docker**~~
~~**run, avoiding mismatches between exploration and replay.**~~

## 3.4 Evidence-Grounded Sandbox Exploration

After cloning $R_c$, the system starts a Docker sandbox from the selected image
and copies the repository into `/app`. The planner operates under a strict
single-step protocol:

```text
Thought: <reasoning>
Action: <one shell command, __ROLLBACK__, or __RETRIEVE_MEMORY__>
```

The planner never writes observations itself. It proposes one action, the host
executes that action inside the sandbox, and only the resulting output is added
back to the context. This prevents the model from fabricating setup progress.

~~The sandbox enforces replay-oriented constraints before command execution. It~~
~~rejects setup or verification commands that pipe output through lossy filters~~
~~such as `tail`, `head`, or `grep`, because these filters can hide the failure~~
~~that later determines the correct Dockerfile. It also rejects compound commands~~
~~that mix multiple independent setup mutations, or combine a mutation with a~~
~~probe or test, unless the chain is a known atomic pattern such as~~
~~`apt-get update && apt-get install`. These constraints make each persistent~~
~~environment change individually observable and therefore replayable.~~

The sandbox maintains snapshots of successful states. Ordinary command failures
do not automatically roll back the container; instead, the observation tells the
agent that a failed mutation may have partially changed the environment. The
agent may explicitly request `__ROLLBACK__`, or rerun a useful prefix as a new
standalone action so that it becomes verified evidence. This design avoids a
mismatch in which the host silently reverts state while the planner still
believes partial changes are present.

没有写压缩机制？？

## 3.5 Verification-Oriented Trajectory Recording

Every executed action is recorded with structured metadata: the command, its
observation, whether it succeeded, whether it mutates the environment, whether
it is read-only or runtime-only, and whether its output is an effective test
signal. Long observations are compressed for planner context, but compression is
constrained to preserve replay-relevant facts such as installed packages,
versions, file edits, generated artifacts, services, test counts, and the first
real failure.

The system does not trust an agent's final success claim by itself. To finish,
the agent must provide a verification bundle containing commands that were
previously executed successfully in the current final environment. The verifier
checks the bundle against recorded actions and normalizes benign variants, such
as a command prefix that ends at the actual test invocation. If a later setup
mutation occurs after a successful verification command, the previous
verification block is invalidated because the environment state has changed.

This mechanism produces two artifacts for synthesis: a compact chronological
summary of the setup trajectory and `agent_run_summary.json`, a structured
record of successful actions, failed actions, verification commands, compression
statistics, and token usage.



## 3.6 Trajectory-to-Dockerfile Synthesis

The synthesizer receives both the setup trajectory summary and the structured
run summary. Its goal is not to invent a new installation strategy, but to
compile the sandbox evidence into a replayable build recipe. The recipe contains
persistent build commands, optional runtime preparation commands, final test
commands, excluded commands, and a rationale.

The central synthesis policy is conservative replay. Successful commands that
may persistently change the environment are retained by default. A command is
removed only when the system can identify it as read-only exploration,
test-only, runtime-only, a local health check, or otherwise unsuitable for a
Docker build layer. This is implemented as a negative filter over observed
successful actions rather than as an exhaustive whitelist of possible package
manager commands. As a result, unfamiliar but successful setup actions are more
likely to be preserved than accidentally discarded.

The synthesizer also preserves the relative order of successful state-changing
commands. It does not sort commands by package manager, merge independent
installation steps, or replace a verified package-manager command with a
different package manager. When a successful command contains multiple segments,
the system splits it into replay units only when this is safe; for example, it
can preserve an `apt-get update` and `apt-get install` pair as an atomic unit.
File rewrites, generated stubs, version pins, backend bootstrap commands, and
other repository modifications are retained when the sandbox trajectory shows
that verification depended on them.

Finally, the build recipe is rendered into a Dockerfile. The renderer adds
bootstrap instructions for robust `apt` and `pip` behavior, sets the working
directory, emits the retained commands as `RUN` instructions, and encodes
multi-line commands using Dockerfile-safe syntax.

## 3.7 Fresh Replay Validation and Repair

Sandbox success is not sufficient: the interactive sandbox may contain state
that is not reproduced by the generated Dockerfile. Typical replay gaps include
missing installation commands, changed command order, lost environment
variables, file rewrites overwritten by later installs, runtime services placed
in build layers, or invalid Dockerfile syntax.

Therefore, after synthesis the system performs fresh replay. It builds the
generated Dockerfile in a clean build context and then runs the verified test
commands inside a new container. A Dockerfile is considered successful only if
the image builds and all selected verification commands execute effectively
under the replay classifier.

When replay fails, the system invokes a bounded Dockerfile repair agent. The
repair input contains the current Dockerfile, the structured run summary, the
build recipe, the selected verification commands, and the build/test logs. The
repair agent must output a full replacement Dockerfile. It is constrained to fix
replay gaps rather than solve the repository from scratch: it should restore
omitted successful setup commands, preserve the original trajectory order, keep
observed file patches instead of inventing equivalent ones, and avoid modifying
the target repository outside Dockerfile commands. The repaired Dockerfile is
normalized and replayed again for a bounded number of rounds.

This stage is part of the method, not merely an evaluation utility. It closes
the gap between "the agent found a working sandbox state" and "the generated
Dockerfile can reproduce that state from scratch."

## 3.8 Output Artifacts

For each repository instance, the system outputs

$$
y = (B^*, D^*, V, \Sigma),
$$

where $B^*$ is the selected base image, $D^*$ is the final replay-validated
Dockerfile, $V$ is the verified command bundle, and $\Sigma$ is a structured
summary containing the sandbox trajectory, action metadata, synthesis recipe,
validation attempts, and repair rounds. These artifacts make the result
auditable: failures can be attributed to clone/setup errors, missing sandbox
verification, Docker build failures, ineffective test execution, or exhausted
repair.
