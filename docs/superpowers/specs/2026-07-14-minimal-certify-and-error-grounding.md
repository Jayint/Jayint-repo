# Minimal Certification Patch and Error Grounding

**Date:** 2026-07-14  
**Status:** Proposed minimal vertical slice  
**Scope:** Python package certification plus high-confidence error-to-graph grounding

## Goal

Make the dependency graph immediately useful to the repair agent without attempting to model every possible environment failure.

The first slice should make this distinction reliable:

```text
package absent
package present at the wrong version/source
package present but its import is broken
import provider unresolved
```

The graph remains the desired-environment model. Certification compares that model with the container produced by the latest rebuild. Error grounding corrects or extends the model when runtime evidence exposes an omitted capability.

## Non-goals

- Do not implement the proposed absolute negative verdict (`the environment is certified`).
- Do not build a complete environment inventory or ingest every installed package into the graph.
- Do not add broad service/configuration grounding yet.
- Do not infer a package name directly from an unfamiliar import name.
- Do not change production code under test to make an evaluation pass.

## Part 1: Minimal `certify` refinement

### Problem

A versioned package node currently uses a name-only check:

```text
graph node: pkg:numpy==1.26.4
check:      python -m pip show numpy
```

Any installed NumPy version returns exit code zero, so an agent command that changes NumPy to 2.0 still leaves `pkg:numpy==1.26.4` marked `SATISFIED`.

The most important first change is:

> A versioned Package node is satisfied only when that exact version is installed.

### Batched Python environment snapshot

Run one read-only Python probe after every rebuild rather than one `pip show` process per package:

```python
import importlib.metadata
import json

result = {}
for dist in importlib.metadata.distributions():
    name = dist.metadata.get("Name")
    if not name:
        continue
    direct_url_text = dist.read_text("direct_url.json")
    result[name] = {
        "version": dist.version,
        "direct_url": json.loads(direct_url_text) if direct_url_text else None,
    }

print(json.dumps(result, sort_keys=True))
```

Normalize distribution names using the repository's existing PEP 503 normalization before comparison.

### Package reconciliation

For every modeled Package node:

```text
distribution absent
    -> obligation unsatisfied: package_absent

distribution present, node.version differs
    -> obligation unsatisfied: version_mismatch

distribution present, exact version agrees
    -> package-presence obligation satisfied

node expects a direct/local/VCS source and direct_url disagrees
    -> obligation unsatisfied: source_mismatch
```

An absent `direct_url.json` does not prove which package index supplied an ordinary distribution. Source checks should therefore be enforced only where installed metadata can support the claim.

Record structured certification evidence, not just an exit code:

```text
node:             pkg:numpy==1.26.4
expected_version: 1.26.4
actual_version:   2.0.0
reason:           version_mismatch
build_id:         7
```

The existing `State` enum can remain initially, but `SATISFIED` must require an exact match. A mismatch may temporarily use the existing non-satisfied state while carrying an explicit reason; do not render it as literally absent.

### Project origin from the same snapshot

Use the local project's distribution metadata and `direct_url.json` to record:

```text
installed: true|false
origin: file:///app|other|unknown
editable: true|false|unknown
```

This verifies the graph-generated `--no-deps -e .` capstone and detects a published distribution shadowing the checkout. Existing Import-node probes remain responsible for proving that expected imports resolve successfully.

### Keep presence and importability separate

Do not collapse these nodes:

```text
pkg:Pillow==10.4.0    exact distribution/version fact
import:PIL            functional import fact
```

The package can be present while the import fails because of ABI, native-library, path, or source problems.

### Rebuild scope

Assign a real `build_id`/cycle to every certification pass. State from an earlier container must not masquerade as current state.

Execution reachability is a separate axis:

```text
environment fact: package is absent
producer status:  install command was not reached
repair verdict:   not currently actionable
```

Do not overload package presence with `NOT_REACHED`; combine certification with the script execution report when deciding what to show the agent.

## Part 2: Error-to-graph grounding

### Governing rule

> An error first identifies the failed capability. It does not automatically identify the package or repair action.

For example:

```text
ModuleNotFoundError: No module named 'PIL'
```

directly proves:

```text
import:PIL is not importable in this rebuild
```

It does not directly prove:

```text
pkg:Pillow is absent
```

### Initial supported error shapes

Limit the first implementation to three high-confidence identities:

```text
ModuleNotFoundError: No module named 'X'   -> import:X
SONAME cannot open shared object file     -> syslib:SONAME
COMMAND: command not found                -> binary:COMMAND
```

Anything unmatched remains an explicit unbound FailureAnchor. It must not disappear and must not become evidence that the environment is healthy.

### Grounding pipeline

#### 1. Preserve the runtime observation

Create or update the exact capability node and retain provenance:

```text
anchor_id
build_id
command or pytest subject
pytest phase, when applicable
exact log span
capability kind and key
```

For missing imports, create/update `import:X`, not a Package node with an import check.

#### 2. Find a supported provider edge

Attempt to link the capability to an existing provider using, in order:

1. installed `importlib.metadata.packages_distributions()` evidence;
2. resolver/lock or wheel metadata already represented by the graph;
3. a curated mapping as a candidate requiring verification.

Represent the relation using the graph's existing Import-to-Package edge convention:

```text
import:PIL -> pkg:Pillow==10.4.0
```

If no provider is supported, retain `import:X` as unresolved. Never create `pkg:X` merely because `X` appeared in an import error.

#### 3. Combine grounding with certification

The useful decision table is:

| Import fact | Provider edge | Package certificate | Meaning |
|---|---|---|---|
| failed | confirmed | absent | Strong evidence for restoring/installing the modeled provider |
| failed | confirmed | exact package present | Do not reinstall blindly; inspect native libraries, ABI, source and import path |
| failed | unresolved | n/a | Provider unknown; investigate local project/PYTHONPATH/provider identity |
| passed | confirmed | exact package present | Capability currently satisfied |

The same rule applies to system libraries and tools: the error identifies the capability; provider resolution identifies a possible installation action.

#### 4. Update only supported facts

An exact runtime error may immediately update:

```text
import:PIL.importable = false
```

It may add:

```text
import:PIL -> pkg:Pillow
```

only when provider identity has evidence.

After a repair, clear an anchor only when the same capability or affected pytest subject is reached again and succeeds. An earlier failure hiding it means `not observed`, not `cleared`.

## Agent-facing evidence

Render the combination, not a whole-graph dump:

```text
CURRENT FAILURE
  import:PIL failed during collection of tests/test_image.py

GROUNDING
  import:PIL -> pkg:Pillow==10.4.0    confirmed by resolver metadata

CURRENT CERTIFICATION
  pkg:Pillow==10.4.0                  absent
  import:PIL                          failed

REPAIR IMPLICATION
  Restore the modeled Pillow provider. Do not guess a package named PIL.
```

For a present-but-broken provider:

```text
CURRENT CERTIFICATION
  pkg:opencv-python==4.10.0            exact version present
  import:cv2                           failed
  syslib:libGL.so.1                    absent

REPAIR IMPLICATION
  The Python distribution is present. Investigate/restore the missing runtime prerequisite.
```

## Minimal implementation order

1. Add one batched installed-distribution/version/direct-URL probe.
2. Reconcile Package nodes against exact versions and record structured reasons.
3. Record local-project origin/editable metadata from the same probe.
4. Give each repair rebuild a real certification cycle.
5. Change `ModuleNotFoundError` ingestion to create/update an Import node first.
6. Bind Import to Package only from supported provider evidence.
7. Render the three-way result: provider absent, provider present-but-broken, provider unresolved.
8. Add SONAME and missing-binary grounding using the same capability-first rule.

## Required tests

### Certification

- Graph expects `numpy==1.26.4`; environment has 2.0.0 -> version mismatch, not satisfied.
- Graph and environment both have `numpy==1.26.4` -> package presence satisfied.
- Local project metadata points to `/app` -> local origin confirmed.
- Published project distribution shadows `/app` -> project origin mismatch.
- Agent install changes a graph-pinned dependency -> drift detected on the next rebuild.
- Every certification record carries the current rebuild ID.

### Grounding

- `No module named 'PIL'` creates/updates `import:PIL`, then binds to existing `pkg:Pillow` evidence.
- `No module named 'comfy'` with no supported provider remains an unresolved Import observation; no `pkg:comfy` is created.
- Exact provider present plus failed import does not label the Package absent.
- `libGL.so.1` error grounds to the SystemLib capability before resolving an apt provider.
- `cmake: command not found` grounds to the binary capability before resolving an apt provider.
- Multiple error blocks are all retained; parsing must not stop after the first match.
- A prior anchor is cleared only when the same capability/pytest subject is reached and succeeds.

## Measurement gate

Run this slice in shadow mode before changing agent instructions. Report bad results rather than tuning them away.

Measure:

- number of current `SATISFIED` Package nodes revoked by exact version checking;
- number of project-origin mismatches;
- provider-grounding coverage and precision for the three supported error shapes;
- counts of absent-provider, present-but-broken and unresolved cases;
- later repair actions and rebuilds saved only after the evidence packet is exposed.

If exact certification finds no meaningful drift and the grounded evidence does not improve repair behavior, keep the truthful certification as infrastructure but do not claim graph-guided repair value.

## Relevant current source

- `src/python_deps/depgraph/certify.py`
- `src/python_deps/depgraph/resolve_lock.py`
- `src/python_deps/depgraph/scan.py`
- `src/python_deps/depgraph/runtime_classify.py`
- `src/python_deps/depgraph/runtime_ingest.py`
- `src/python_deps/depgraph/graph_enrich.py`
- `src/python_deps/depgraph/build.py`
- `src/python_deps/depgraph/populate.py`
- `src/react_repair/entry.py`
- `src/react_repair/loop.py`

