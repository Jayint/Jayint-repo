# DESIGN: Static-Probe Certified Dependency Graph

**Status:** concept summary / architecture note  
**Date:** 2026-06-23  
**Scope:** within-run environment setup and diagnosis for Python repository agents  
**Related docs:** `DESIGN-deps-diagnosis-graph.md`, `SPEC-deps-diagnosis-graph.md`, `DESIGN-single-session-planning-graph-v1.md`

## 1. Short Name

This design was temporarily called **PLC-Graph**: Pre-built Layered Certified Graph.

The more accurate name is:

```text
Static-Probe Certified Dependency Graph
```

The caveat is important: the graph is not fully "pre-built" with zero execution. Only the first tier is static. The system/native layer is discovered by a probing phase that performs real installs/imports in a live discovery container that shares the final base image (see 4.6). So the honest description is:

```text
static analysis + resolver upfront, plus pre-flight probing for native/system needs
```

## 2. Core Thesis

Environment setup agents fail when they learn dependencies only by breaking the environment repeatedly.

This graph changes the default loop from:

```text
try command -> fail -> LLM guesses -> try another command
```

to:

```text
scan imports -> map package names -> resolve pip closure -> probe native/system needs
-> build layered executable graph -> certify each need with host checks
-> let the agent handle only the residue and ambiguous cases
```

The graph is not a general world model. It is a **dependency readiness graph**: a typed, layered model of what must be true for the repository's Python environment to run.

## 3. Philosophy

### 3.1 The graph proposes; the host certifies

The most important invariant:

```text
Install success != proven readiness.
Only a host-run check_command can mark a Need as satisfied.
```

Examples:

```text
pip install opencv-python succeeded
```

does not prove:

```text
python -c "import cv2"
```

will succeed.

Likewise:

```text
apt install libgl1 succeeded
```

does not prove the original problem is fixed unless the relevant import or library check passes.

#### What a certificate is

A `certificate` is a claim about the environment that has been backed by actually running a check command and observing its exit code. It is not produced by reasoning, and not by trusting what a tool reported. Concretely it is the tuple:

```text
need          system_library:libGL.so.1 is present
check_command ldconfig -p | grep libGL.so.1
result        exit code 0
scope         image python:3.11-slim@sha256:..., cycle 4
```

When the check returns 0, the host flips the Need to `satisfied`. That flip is the certification. The distinction this design exists to enforce:

```text
"pip install opencv-python succeeded"   -> an action outcome. NOT a certificate.
"python -c 'import cv2' returned 0"     -> a certificate.
```

The package manager's "Successfully installed" is a claim it makes; the certificate is your own independent check passing. They diverge constantly (install succeeds, import dies on a missing .so; `pytest --collect-only` returns 0, suite fails on execution).

Three properties:

```text
revocable    valid now, in this environment; a later install can break it -> back to unknown
scoped       valid only for the exact image/python/arch it ran in (hence provenance)
host-issued  the LLM never writes a check_command and never sets a state to satisfied
```

### 3.2 Use deterministic tools where they are strong

The LLM should not do version arithmetic or manually recurse through PyPI metadata. A real resolver should own the pip layer.

Good split:

```text
static scanner      -> observed import needs
import mapper       -> import names to package candidates
uv / pip resolver   -> pinned Python distribution closure
probe harness       -> native/system/toolchain needs
host checker        -> truth state
LLM agent           -> ambiguity, novel failures, strategy
```

### 3.3 The graph is layered

The graph distinguishes layers that raw logs collapse:

```text
interpreter
system package
toolchain
pip distribution
import/naming
runtime binary
runtime env/service
tests/app behavior
```

Discovery order and execution order differ.

Discovery may go:

```text
import cv2 -> opencv-python -> probe import -> missing libGL.so.1
```

Execution should go:

```text
install libGL/system deps -> install pip closure -> verify import cv2
```

### 3.4 The graph is within-run

This design intentionally does not require a cross-repository knowledge base. It can still use curated local mappings, but the state of this graph belongs to one environment setup run.

Cross-run memory may later cache common mappings or probe results, but that is not part of the core correctness story.

### 3.5 The agent is still needed

The graph does not remove the LLM agent. It changes the agent's job.

Without the graph, the agent must infer everything from scrollback:

```text
what failed?
which layer owns the failure?
what should I install?
did the install prove anything?
did I already try this?
```

With the graph, deterministic machinery handles:

```text
resolved pip closure
install ordering
known checks
state transitions
failure-scoped slices
attempt memory
```

The LLM remains useful for:

```text
cv2: choose opencv-python vs opencv-python-headless
fitz: choose PyMuPDF, not fitz
unknown shared library: infer provider package
runtime config: infer test-safe env values
novel errors: decide env fault vs code/data/service fault
```

## 4. Core Pipeline

### 4.1 Static import scan

Scan repository source and tests for imports:

```python
import cv2
from PIL import Image
import sklearn
```

These become import/name Needs:

```text
Need: python_import:cv2
Layer: naming
Check: python -c "import cv2"
State: unknown
```

Static scanning is evidence, not completeness. Dynamic imports and plugin systems may appear only at runtime.

### 4.2 Import name to distribution mapping

Map import names to PyPI distributions:

```text
cv2     -> opencv-python | opencv-python-headless
PIL     -> Pillow
sklearn -> scikit-learn
yaml    -> PyYAML
fitz    -> PyMuPDF
bs4     -> beautifulsoup4
```

This layer is necessary because Python code imports modules, while pip installs distributions.

Existing project declarations should be treated as high-priority evidence:

```text
pyproject.toml
requirements.txt
setup.cfg
setup.py
uv.lock
```

Static inference should find gaps and inconsistencies; it should not blindly override project manifests.

### 4.3 Resolver creates the pip graph

After import-name mapping, pass root package requirements to a real resolver.

Example:

```text
roots:
  opencv-python
  Pillow
  pandas
```

The resolver produces:

```text
opencv-python==...
Pillow==...
pandas==...
numpy==...
python-dateutil==...
pytz==...
tzdata==...
six==...
```

This should be stored as a graph, not just a flat requirements file:

```text
ImportNeed cv2
  provided_by Distribution opencv-python==...

Distribution opencv-python==...
  depends_on Distribution numpy==...
```

In practice, `uv` can populate much of this layer:

```bash
uv pip compile requirements.in -o requirements.txt
uv tree
uv tree --invert --package urllib3
uv export --format cyclonedx1.5
```

For project mode, `uv.lock` contains package records and dependency edges. `uv export --format cyclonedx1.5` gives machine-readable components and dependency relationships, though the format may be version/feature dependent. The graph should not depend on human-formatted tree text as its only source.

### 4.4 Probe native and system needs

The resolver tells us Python package versions. It does not tell us whether the target system has the shared libraries, binaries, headers, or services those packages require.

Probing does not happen in a throwaway per-package container. It happens in a single live discovery container (see 4.6) started from the final base image. The probe is instrumentation on the install you must do anyway, run as a loop:

```text
1. install the resolved closure in one command
     -> parse install stderr: build-time gaps (compiler/headers/*_config) surface here
2. run the import-probe harness (one `python -c "import X"` per import Need)
     -> parse import stderr: run-time gaps (missing shared libraries) surface here
3. for compiled-extension packages, `ldd` the installed `.so` files
     -> catches link-time libs proactively (misses dlopen'd libs, so step 2 still backstops)
4. batch-install the discovered system providers, then re-probe once
5. repeat until every probe is green
```

Build-time and run-time native deps surface at different points: compiler/header/`*_config` gaps during install (step 1), shared-library gaps during import (step 2). Example probes:

```bash
python -c "import cv2"
python -c "import psycopg2"
python -c "import numpy"
```

If an import probe fails:

```text
ImportError: libGL.so.1: cannot open shared object file
```

add:

```text
Need: system_library:libGL.so.1
Layer: system
Provider: apt:libgl1
Check: ldconfig -p | grep libGL.so.1
Required by: Distribution opencv-python==...
```

If a build fails:

```text
pg_config executable not found
gcc: command not found
Python.h: No such file or directory
```

add:

```text
Need: toolchain:pg_config
Provider: apt:libpq-dev
Check: command -v pg_config

Need: toolchain:gcc
Provider: apt:build-essential
Check: command -v gcc

Need: header:Python.h
Provider: apt:python3-dev
Check: python - <<'PY'
import sysconfig, pathlib
print(pathlib.Path(sysconfig.get_paths()["include"], "Python.h").exists())
PY
```

### 4.5 Runtime residue

Some needs are not pip or system packages:

```text
DATABASE_URL
REDIS_URL
OPENAI_API_KEY
Chrome browser binary
Postgres service
Redis service
test fixture files
model weights
```

These are runtime/config/service Needs. They are often discovered by test execution, env-var scans, Docker Compose files, or service connection failures.

They should not be forced into the pip/system hierarchy. Model them as a separate runtime axis:

```text
runtime_env
runtime_service
runtime_binary
runtime_data
```

### 4.6 Probe environment and certification lifecycle

There are two distinct containers, and the graph is the bridge between them. They are NOT the same instance, but they share the same base image.

```text
[1] PROBE / DISCOVERY container          [2] BUILD artifact
    - mutable, long-lived                    - clean `docker build` from the
    - started FROM the same base image         Dockerfile the graph emitted
    - you exec into it: install/probe/         - immutable, reproducible
      apt/re-probe in a fast loop            - this is what ships and runs tests
    - job: DISCOVER + CERTIFY needs,
      emit the graph + Dockerfile
    - throwaway when done
```

Why two, instead of iterating directly in the build: a Dockerfile build is immutable, so every failed attempt rebuilds a layer (build-scale iteration). The scratch container is mutable, so install/probe/retry is a cheap `docker exec` (exec-scale iteration). You discover in the cheap mutable container; the Dockerfile is the frozen, layer-ordered output (apt before pip).

#### Provisional vs promoted certificates

A certificate earned in the scratch container is real (a host ran a real check) but its scope is the scratch container, which you have been hand-mutating. It may depend on something you forgot to record as a Provider (the recurring lossy-synthesizer failure mode). So a scratch certificate is `provisional`. It is promoted to trustworthy only by:

```text
1. emit the Dockerfile from the certified Provider set only (apt before pip, layer-ordered)
2. clean `docker build` -> container [2]
3. re-run every check_command in that fresh image
```

The clean-rebuild re-certification is the honesty gate. If a check that passed in scratch fails in the clean build, the Dockerfile is missing a Provider -> re-probe. You replay the certified Provider set, never the raw trajectory; anything in the scratch container not tied to a certified Need does not enter the Dockerfile.

Tests run in the build artifact (container [2]), never in the scratch container.

### 4.7 Probing at scale

Probing cost is not O(number of dependencies). Almost none of a large closure needs a probe. The job is to funnel the closure down to the few that do, then batch them.

#### The funnel

```text
full closure (resolver)        ~100 pkgs
  -> import surface tests touch  ~15-25   only probe what is imported, not the whole closure
  -> compiled-extension subset    ~5-15   pure-Python wheels cannot have a missing .so
  -> actually failed a probe       ~0-5   only these get expensive investigation
```

Each narrowing is justified:

```text
import surface   you install all 100 in one command, but only import-probe the names that appear
                 in `import X` in repo+tests; transitive deps are covered for free (a green
                 `import pandas` already loaded numpy/pytz/dateutil)
wheel tag        `*-py3-none-any.whl` has no compiled extension -> cannot miss a shared library;
                 only platform-tagged wheels (`*-cp311-manylinux*`) carry native risk. Readable
                 off resolver output / dist-info with no execution.
lazy escalation  first pass is one install + one batched harness; if green, done. ldd attribution,
                 apt mapping, and retry loops run only for imports that failed.
```

Work is proportional to native failures (rare), not closure size (large but almost entirely free to clear).

#### Batch everything that survives

```text
one install       `pip install -r pinned.txt` installs the whole closure; build-time failures
                  surface in that one command's stderr. No per-package install.
one harness       a single in-container script imports every import-Need, each in its own
                  subprocess; emits structured JSON {module: rc, stderr}. One exec, N imports.
                  Cost tracks weight (torch ~seconds, six ~free), not count.
one ldd sweep     ldd every .so in the compiled-extension subset in one pass (reads ELF headers,
                  no execution) -> all missing link-time libs at once.
one remediation   collect all missing libs from a pass, map them all, apt-get install the batch,
                  re-probe once. Converges in ~2 passes, not one-fix-one-retry.
```

#### Keep it from multiplying

```text
single target   one base image, one Python, Debian/Ubuntu only. No package x version x python x
                distro x arch matrix in V1.
cache           by (name, version, image digest). Never re-probe an unchanged package; a closure
                mutation re-probes only the affected subtree (the revocation rule).
curated table   libGL.so.1 -> apt:libgl1, pg_config -> apt:libpq-dev. Static, not learned, so it
                makes failure->fix O(1) without the deferred cross-run KB.
```

Honest worst case (150-pkg closure, ~20 compiled extensions, torch): one install dominated by the torch download (cost of building the env at all, not a probing cost), one batched harness (~tens of seconds), one ldd sweep (seconds), one batched apt + re-probe (seconds). Probing overhead above the unavoidable install is a minute or two, not 150x anything.

## 5. Graph Model

The graph is made of **concrete entities, one node type per layer**, connected by essentially **one relationship (`requires`)** plus two special cases. This is deliberately not a generic constraint ontology (a single abstract `Need` typed by attribute). The point is that the graph should *read like the pipeline*: imports found by static scan, packages by the resolver, system/tool needs by probing, runtime needs by execution.

Guiding rule:

```text
a relationship between two entities   -> an edge   (requires, alternative_to, conflicts_with)
information about one entity          -> a field   (discovery, state, fix, attempts, evidence)
```

The old `Provider` / `Attempt` / `Failure` node types do not disappear; they were each information *about one entity*, so they become fields (see 5.3, 5.5).

### 5.1 Node types

One concrete type per layer:

```text
type        example                         discovered_by
----------  ------------------------------  -------------
Test        repo_tests_pass                 (goal)
Import      cv2, sklearn                    static_scan   (stage 1)
Package     opencv-python==4.9, numpy==...  resolver      (stage 2)
SystemLib   libGL.so.1                      probe         (stage 3)
Tool        pg_config, gcc, Python.h        probe         (stage 3)
Runtime     DATABASE_URL, postgres          runtime       (stage 4)
```

These are the pipeline's outputs as node types. Formally each is the abstract "Need" with its `kind` promoted to *be* the node type, so nothing the solver needs is lost (see 5.6).

### 5.2 Node fields

Every node carries the same field groups; some fields are empty for some types (an `Import` has no `fix_candidates` of its own — its `Package` is what gets installed).

```text
-- identity --
id                  syslib:libGL.so.1
type                Test | Import | Package | SystemLib | Tool | Runtime
name                libGL.so.1
version             (Package only) 4.9.0.80
layer               interpreter|system|toolchain|pip|naming|runtime|tests

-- discovery (the pipeline: which stage surfaced this) --
discovered_by       static_scan | resolver | probe | runtime
discovered_cycle    3
provenance          tests/test_vision.py:3   (e.g. file/manifest/probe that surfaced it)

-- certification (was: Failure node + state) --
state               unknown | missing | satisfied      (host flips only; revocable)
check_command       ldconfig -p | grep libGL.so.1
evidence            "ImportError: libGL.so.1: cannot open shared object file"
certified_cycle     4

-- fix options (was: Provider node) --
fix_candidates      ["apt:libgl1"]
chosen_fix          apt:libgl1

-- history (was: Attempt nodes) — the anti-loop record --
attempts            [ { command, outcome, check, cycle }, ... ]
```

Certification invariant (see 3.1): only a host-run `check_command` flips `state`. `state` is the certification axis; `attempts[].outcome` is the separate action axis. An attempt succeeding is NOT `state: satisfied` — only the check passing is.

### 5.3 Where Provider / Attempt / Failure went

```text
Provider  -> fix_candidates + chosen_fix          (+ alternative_to edge, see 5.5)
Attempt   -> attempts[]                            (node-local anti-loop history)
Failure   -> state: "missing" + evidence          (the `indicates` edge disappears: the error
                                                    is recorded ON the node it pointed to)
```

### 5.4 Example nodes

System library (found in stage 3 by probing, then fixed and certified):

```jsonc
{
  "id": "syslib:libGL.so.1", "type": "SystemLib", "name": "libGL.so.1", "layer": "system",
  "discovered_by": "probe", "discovered_cycle": 3,
  "state": "satisfied",
  "check_command": "ldconfig -p | grep libGL.so.1",
  "evidence": "ImportError: libGL.so.1: cannot open shared object file",
  "certified_cycle": 4,
  "fix_candidates": ["apt:libgl1"], "chosen_fix": "apt:libgl1",
  "attempts": [
    { "command": "apt-get install -y libgl1", "outcome": "succeeded",
      "check": "ldconfig -p | grep libGL.so.1", "cycle": 4 }
  ]
}
```

Import (found in stage 1; never fixed directly — its Package is):

```jsonc
{
  "id": "import:cv2", "type": "Import", "name": "cv2", "layer": "naming",
  "discovered_by": "static_scan", "provenance": "tests/test_vision.py:3",
  "state": "satisfied",
  "check_command": "python -c \"import cv2; print(cv2.__version__)\"",
  "evidence": null,
  "fix_candidates": [], "attempts": []
}
```

Package (found in stage 2 by the resolver):

```jsonc
{
  "id": "pkg:opencv-python==4.9.0.80", "type": "Package",
  "name": "opencv-python", "version": "4.9.0.80", "layer": "pip",
  "discovered_by": "resolver",
  "state": "satisfied", "check_command": "python -m pip show opencv-python",
  "fix_candidates": ["pip:opencv-python"], "chosen_fix": "pip:opencv-python",
  "attempts": [ { "command": "pip install opencv-python==4.9.0.80", "outcome": "succeeded", "cycle": 2 } ]
}
```

### 5.5 Edges

The whole graph is one relationship plus two special cases:

```text
requires        X needs Y to work
                Test->Import, Import->Package, Package->Package (transitive),
                Package->SystemLib, Package->Tool, Test->Runtime
                attr: origin (scan|resolver|probe|runtime), layer

alternative_to  this package is a swap for that one
                pkg:opencv-python <-> pkg:opencv-python-headless

conflicts_with  these two cannot coexist
                pkg:numpy<2 <-> pkg:numpy>=2
```

There is no `retracts` edge. In the concrete model you draw each package's *actual* requires: `opencv-python` has `requires -> libGL.so.1`; `opencv-python-headless` does not. Swapping to the alternative drops libGL naturally, because the edge was never there. `retracts` only existed in the abstract model, which did not materialize the alternative's subtree.

`requires` is AND-semantics (all required nodes must be satisfied). The OR-choice (which fix satisfies a node) lives in the node's `fix_candidates` field, not in an edge — except the package-swap case, which restructures the graph and so is the `alternative_to` edge.

Execution order is **derived**, not stored: a layer-constrained topological sort over `requires` (see 6). There is no `execution_precedes` edge.

### 5.6 The folding tradeoffs (honest)

```text
shared actions duplicate   one `apt-get install libgl1` may satisfy several SystemLib nodes;
                           node-local `attempts` records it on each, vs once on a shared node.
                           Cheap for a within-run graph.
unattributed failures      a standalone Failure node could sit as "error not yet traced".
                           Folded, `evidence` must attach somewhere — best-guess node, or the
                           Test node as raw evidence — until traced. Slightly weaker for the
                           rare ambiguous-symptom case.
```

### 5.7 Projection to the uniform model

The concrete model is isomorphic to the abstract Need/Provider/Attempt model: a concrete node is a `Need` with `kind` pinned to its type, `requires` is `depends_on`, `conflicts_with` is unchanged, `fix_candidates`/`alternative_to` are `provided_by`/`retracts`, and `attempts[]` are `Attempt` records. For the solver/Z3 layer (conflicts only) you can project the concrete graph down to the uniform one without rewriting anything. The concrete model is what the planner reads; the uniform projection is what the solver consumes.

## 6. Layering and Topological Execution

The graph is built from multiple evidence sources, but execution should be layer-aware.

Recommended execution order:

```text
1. interpreter
2. system packages
3. toolchain packages
4. pip distributions
5. import/name checks
6. runtime binaries
7. runtime env vars
8. runtime services
9. real tests/app behavior
```

This is not a blind topological sort. It is a topological sort constrained by layer priority.

Example:

```text
ImportNeed cv2
  depends_on Distribution opencv-python==...
  depends_on SystemNeed libGL.so.1
```

Even if `libGL.so.1` was discovered after probing `opencv-python`, the repair plan should install `libGL.so.1` before final pip/import certification.

## 7. How This Helps Fault Diagnosis

The graph helps because it preserves the causal path from symptom to root layer.

### 7.1 Missing direct dependency

Failure:

```text
ModuleNotFoundError: No module named 'sklearn'
```

Graph slice:

```text
Need python_import:sklearn
  mapped_to pip_dist:scikit-learn
  provider pip:scikit-learn
  check python -c "import sklearn"
```

### 7.2 Wrong import/package mapping

Failure:

```text
ModuleNotFoundError: No module named 'fitz'
```

Graph can avoid:

```text
pip install fitz
```

and propose:

```text
pip install PyMuPDF
```

### 7.3 Native library failure

Failure:

```text
ImportError: libGL.so.1: cannot open shared object file
```

Graph slice:

```text
python_import:cv2
  <- Distribution opencv-python==...
  depends_on system_library:libGL.so.1 [missing]

candidate repairs:
  apt:libgl1
  pip:opencv-python-headless retracts system_library:libGL.so.1
```

### 7.4 Version conflict

Failure:

```text
A requires numpy<2
B requires numpy>=2
```

Graph should not model this as a missing package. It should model:

```text
Need numpy<2 conflicts_with Need numpy>=2
```

The resolver or Z3-backed unsat explanation owns this layer. The LLM should not compute the intersection manually.

### 7.5 Runtime service/config failure

Failure:

```text
KeyError: DATABASE_URL
```

Graph slice:

```text
runtime_env:DATABASE_URL [missing]
  provider env:DATABASE_URL=<test-safe value>
  check python -c "import os; assert os.environ.get('DATABASE_URL')"
```

If the failure is a connection refusal, the root Need may instead be:

```text
runtime_service:postgres
```

## 8. Agent Interface

The agent should not receive the whole graph. It should receive a failure-scoped slice:

```text
failed check
relevant upstream/downstream Needs
known satisfied lower layers
candidate Providers
previous failed Attempts
uncertainty/provenance
suggested next check
```

Example:

```text
Failed check:
  python -c "import cv2"

Relevant graph:
  python_import:cv2
  -> pip_dist:opencv-python==...
  -> system_library:libGL.so.1 [check_failed]

Candidate repair:
  apt-get install -y libgl1

Certification:
  ldconfig -p | grep libGL.so.1
  python -c "import cv2"
```

The agent's role is to decide among plausible repairs, handle ambiguous cases, and interpret novel errors. The graph's role is to prevent the agent from losing the causal structure.

## 9. Relationship to requirements.txt and uv

This graph should use existing manifests, not replace them.

`requirements.txt` and `pyproject.toml` are declarations. They may be:

```text
complete
partial
stale
over-pinned
platform-specific
missing test/dev dependencies
```

The graph adds:

```text
validation against actual imports/tests
import-name to distribution-name mapping
resolver-normalized transitive closure
system/toolchain dependencies
execution ordering
host certification
diagnostic provenance
```

`uv` is useful for the pip layer:

```text
root requirements -> resolved pinned closure -> dependency edges
```

It does not solve:

```text
import-name mapping
system library discovery
apt/apk/dnf/brew provider mapping
runtime env/service needs
host-certified readiness
```

So the split should be:

```text
uv = authoritative Python package resolver
graph = cross-layer execution and diagnosis model
```

## 10. Technical Challenges

### 10.1 Static import scanning is incomplete

Static scanners miss:

```text
__import__("x")
importlib.import_module(...)
plugin discovery
pytest plugin loading
optional backends
framework auto-registration
extras-driven imports
```

Therefore static imports are an initial evidence set, not a proof of dependency completeness.

### 10.2 Import-name mapping is messy

This is one of the biggest implementation risks.

Examples:

```text
cv2 -> opencv-python / opencv-python-headless
PIL -> Pillow
sklearn -> scikit-learn
yaml -> PyYAML
fitz -> PyMuPDF, not fitz
bs4 -> beautifulsoup4
```

PyPI metadata does not consistently expose top-level import names. A practical implementation needs:

```text
curated mapping table
trust levels
multi-provider candidates
manifest-aware precedence
LLM fallback only as low-trust evidence
```

### 10.3 Project manifests can conflict with inference

Example:

```text
requirements.txt says opencv-python-headless
static mapper says cv2 -> opencv-python
```

The graph needs precedence rules:

```text
1. explicit project declarations
2. lockfiles
3. requirements/pyproject constraints
4. curated import mappings
5. direct-name fallback
6. LLM/heuristic guesses
```

### 10.4 uv gives the pip graph, not the environment graph

uv can expose resolved packages and dependency edges, but it will not identify:

```text
libGL.so.1 -> apt:libgl1
pg_config -> apt:libpq-dev
Chrome needed for Playwright
DATABASE_URL needs a test value/service
```

The graph must add the system, toolchain, runtime, and certification layers.

### 10.5 System dependency mapping is distro-specific

The provider for a library varies by target:

```text
Debian/Ubuntu: apt
Alpine: apk
Fedora: dnf
macOS: brew/system frameworks
```

V1 should probably target Debian/Ubuntu containers only. Otherwise the provider mapping surface grows too quickly.

### 10.6 Probing can be expensive

Probing cost is not O(closure size); section 4.7 defines the funnel and batching that bound it. The explosion below only happens with naive per-package probing:

Naive probing explodes across:

```text
package
version
Python version
platform
distro
architecture
extras
```

Mitigations:

```text
probe only native-risk packages
skip obvious pure-Python wheels
batch compatible probes
cache within-run
reuse resolver output
prioritize imports that tests actually touch
only deep-probe after a cheap import check fails
```

### 10.7 Probe failures are noisy

The same failure can mean different things:

```text
missing system library
wrong package variant
wrong Python version
bad wheel
source build fallback
missing compiler/header
network/index issue
platform mismatch
optional dependency not installed
```

The classifier must be conservative. A low-confidence system Need should not be treated as certified truth.

### 10.8 Check commands are the trust boundary

Bad checks make the graph lie.

Weak check:

```bash
python -c "import cv2"
```

Better in context:

```bash
python -c "import cv2; print(cv2.__version__)"
ldconfig -p | grep libGL.so.1
python -m pip check
python -m pytest -q
```

Checks need to be:

```text
specific enough to prove the Need
cheap enough to run repeatedly
portable enough for the target image
not LLM-authored as arbitrary shell
```

### 10.9 State semantics are subtle

The graph needs revocable truth. A later install can break an earlier satisfied import.

Example:

```text
numpy import satisfied
later pip install downgrades numpy
dependent imports become stale
```

Package-mutating commands should invalidate downstream Needs and force re-certification.

### 10.10 Discovery order and execution order can form apparent cycles

To discover a system Need, the system may first install/probe a pip package. But once discovered, that system Need becomes lower-layer and should execute before final pip/import certification.

This is not a logical contradiction. It means the graph needs separate concepts for:

```text
discovered_by
depends_on
execution_precedes
certified_by
```

### 10.11 Runtime needs are less deterministic

Runtime env/service/data needs are harder than pip/system dependencies.

Examples:

```text
DATABASE_URL may be a real Postgres service or a test SQLite URL
REDIS_URL may require service startup or a fake
OPENAI_API_KEY may need skipping/injection, not a real secret
Playwright may need a browser binary plus OS libraries
```

These should remain the reactive residue layer in V1.

### 10.12 Prompt slices must stay small

The full graph may be useful internally, but it is too much for the agent prompt.

The agent should see:

```text
the failed Need
the root-cause chain
candidate Providers
checks
attempt history
uncertainty
```

not the entire transitive dependency closure.

### 10.13 Security and reproducibility

Probing runs untrusted third-party package code or native extension imports. It should happen in isolated containers with clear network/cache policy.

The graph should record:

```text
base image
Python version
platform
resolver command
package versions
probe command
probe output excerpt
check command
timestamp/cycle
```

Without provenance, cached probe results become unsafe to reuse.

## 11. Suggested V1 Scope

Keep V1 narrow.

Build:

```text
1. static import scanner
2. curated import-to-distribution mapping
3. requirements/pyproject/lockfile ingestion
4. uv-based pip resolution
5. parse uv.lock or CycloneDX export into package dependency graph
6. import check generation
7. Debian/Ubuntu system dependency classifier
8. small curated native failure table
9. host-certified Need state
10. failure-scoped graph slice for the agent
```

Initial curated native table:

```text
libGL.so.1          -> apt:libgl1
libgthread-2.0.so.0 -> apt:libglib2.0-0
libglib-2.0.so.0    -> apt:libglib2.0-0
libpq.so.5          -> apt:libpq5
pg_config           -> apt:libpq-dev
mysql_config        -> apt:default-libmysqlclient-dev
gcc                 -> apt:build-essential
Python.h            -> apt:python3-dev
```

Native-risk package seeds:

```text
opencv-python
opencv-python-headless
psycopg2
mysqlclient
lxml
cryptography
numpy
scipy
pandas
torch
tensorflow
playwright
selenium
```

Do not try to solve all runtime services in V1. Detect and surface them as typed runtime Needs, then let the agent decide.

## 12. Open Design Decisions

1. **Name:** Keep PLC-Graph as a conversation/workflow label, but use "Static-Probe Certified Dependency Graph" in technical docs.

2. **uv source of truth:** Prefer `uv.lock` or `uv export --format cyclonedx1.5` for machine-readable edges; avoid parsing human tree output unless no structured output exists.

3. **Probe granularity:** Decide whether probes run per top-level package, per native-risk package, or batched by resolved environment.

4. **Check registry:** Define deterministic templates for each Need kind. Do not let the LLM generate arbitrary certifying commands.

5. **Provider mappings:** Decide how much curated apt/system knowledge to ship in V1 versus asking the agent to infer missing mappings.

6. **Runtime residue:** Decide which runtime needs get deterministic classifiers in V1: env var missing, service connection refused, missing browser binary, missing data file.

7. **Invalidation rules:** Define exactly which commands invalidate which Need subtrees.

8. **Agent slice format:** Define the compact graph slice schema the Planner receives.

## 13. Evaluation Targets

The design should be evaluated on whether it reduces symptom-treatment loops.

Useful metrics:

```text
number of repeated ineffective installs
time/cycles to first correct layer attribution
resolver invocations
probe invocations
system dependency discovery precision
import check pass rate after graph execution
real test pass rate, not collect-only
number of stale proven states caught by re-certification
agent prompt size per failure
```

Representative failure buckets:

```text
missing import with direct package name
missing import with non-obvious package name
native shared library missing
build toolchain/header missing
version conflict
Python version mismatch
dynamic import discovered only at runtime
missing env var
missing runtime service
browser/system binary missing
```

## 14. Bottom Line

The design is strongest when it is framed as:

```text
a layered, host-certified dependency readiness graph
```

not:

```text
a complete pre-built environment graph
```

Its value is the composition:

```text
static import evidence
+ import/distribution naming layer
+ real resolver for the pip closure
+ pre-flight probing for system/toolchain gaps
+ host-certified Need states
+ failure-scoped agent slices
```

The graph does not replace the agent. It gives the agent a better control surface: typed root-cause chains, known attempted repairs, certified state, and clear uncertainty. The agent remains responsible for ambiguous ecosystem choices and novel runtime failures, but it no longer has to reconstruct the whole dependency story from raw logs every cycle.
