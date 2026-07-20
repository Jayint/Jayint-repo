# Uniform Graph Alignment — Schema, Certify, Install-Command Generation

> Companion to [2026-06-30-uniform-requirement-graph-design.md](./2026-06-30-uniform-requirement-graph-design.md).
> That doc states the *target* model. This doc is the *delta*: how the current
> `python_deps/depgraph` graph is modified to match it, scoped to three axes —
> the certify command, install-command generation, and the node schema.
>
> Out of scope here (the *next* conversation): static-analysis enhancement and
> combining the LLM with static scans for graph construction. This doc changes
> the **shape and command-generation** of the graph, not how it is discovered.

## Purpose

The uniform spec makes each node a self-describing environment obligation —
*what must be true* (`check_command`), *how to make it true* (`setup_commands`),
*how to prove it* (`check_command` + host certify). The current model splits
"how" across two off-node pipelines (a type-derived compiler for the reciped
tiers; `ScriptPatch`/`manual_blocks` outside the graph for everything else).

This design collapses both pipelines into one on-node field, makes the renderer
a dumb emitter, and adds the two descriptive axes the spec needs (`strength`,
`phase`) — while preserving the two properties that make v3 correct: the
**host is the sole writer of certification state**, and the **pinned dependency
closure is materialized deterministically**, not restated by an LLM.

## Locked Decisions

Three keystone decisions were settled in design discussion:

1. **Command source — deterministic populator + dumb renderer.**
   `setup_commands` lives on the node. A deterministic *populator* fills it for
   the reciped tiers (Package / SystemLib / Tool) at construction; the LLM patch
   fills it for Service / Config / DataAsset / CommandTask at grounding. The
   renderer emits `node.setup_commands` verbatim in topological order — no
   per-type command switch. The pinned closure survives because the populator
   (not an LLM) synthesizes the `pip install --no-deps name==version` lines from
   the already-resolved `version`/pins.

2. **Phase — per-node `phase`, deferred single-phase split.**
   A node carries one `phase`. A multi-phase obligation (a service: install the
   binary in `setup`, start the daemon in `runtime`) is **not** split at
   construction — that would force premature provider commitment in the discovery
   plane. Construction emits one *soft* service node with no commands; the split
   into a `setup`-phase tool node and a `runtime`-phase service node is a
   **grounding-time normalization** (deterministic service table for known
   daemons, else PatchGate-normalization of the LLM repair patch). Every
   *materialized* node is therefore single-phase and the renderer never straddles
   artifact sections.

3. **State — three host-written values; lifecycle on orthogonal axes.**
   `State` stays `{UNKNOWN, MISSING, SATISFIED}` — the pure certification axis,
   written only by `certify.py`. The spec's other three states are represented
   without touching that invariant: `candidate ≡ strength=SOFT`,
   `blocked ≡ attempts ≥ MAX_EMIT_ATTEMPTS or a conflicts_with edge`,
   `invalid ≡ known_invalid / invalid_commands`.

## Revised `Node` Schema

```text
ADD     setup_commands : tuple[str, ...] = ()      # canonical "how"; the renderer's only command source
ADD     strength       : Strength = HARD           # SOFT | HARD  (default HARD; scanner passes SOFT)
ADD     phase          : Phase    = SETUP          # SETUP | RUNTIME | TEST | GATE
KEEP    check_command  : str | None                # canonical "prove"; host-only writer (unchanged)
KEEP    state          : State                     # UNKNOWN | MISSING | SATISFIED (unchanged)
KEEP    layer          : Layer                      # still drives topo order; distinct from phase
DEMOTE  chosen_fix     : str | None                # now provider PROVENANCE (which apt pkg/wheel), not the command
REMOVE  fix_candidates : tuple[str, ...]           # folded into the populator's candidate logic
KEEP    version, build_from_source, artifact, hash,
        resolved_python, resolved_platform, exclude_newer   # populator inputs for the pinned pip line
KEEP    attempts, evidence, provenance, data
```

New enums:

```python
class Strength(enum.Enum):
    SOFT = "soft"   # candidate / hint; does not block dependents or gates
    HARD = "hard"   # required obligation; blocks dependents and gates

class Phase(enum.Enum):
    SETUP   = "setup"    # baked into setup.sh / Dockerfile
    RUNTIME = "runtime"  # run when the container / test session starts
    TEST    = "test"     # needed only for the test command
    GATE    = "gate"     # maturity proof, not environment setup
```

`layer` is unchanged and keeps its job: **ordering and explanation**. `phase`
answers a different question — **where in the artifact the command belongs**.
A Package and a Service can share `layer`-driven ordering yet land in different
artifact sections by `phase`.

### Why `chosen_fix` is demoted, not deleted

`chosen_fix` records *which* provider the resolver/relink chose (`apt:libpq-dev`,
a wheel filename). The resolver, `apt_resolve`, and `relink` machinery still
produce it. What changes: it is no longer the string the renderer turns into a
command. The populator reads `chosen_fix` + `version` and writes the executable
form into `setup_commands`. Provenance is preserved; the canonical command moves
on-node.

## Install-Command Generation: Populator + Dumb Renderer

A new pure pass replaces the render-time `_install_command` / `build_recipe`
type switch:

```text
populate_setup_commands(graph) -> graph        # pure; no Docker/network/LLM
  for each reciped node still missing setup_commands:
    Package   : setup_commands = ["python3 -m pip install --break-system-packages --no-deps {name}=={version}"]
    SystemLib : setup_commands = ["apt-get install -y --no-install-recommends {apt_name}"]   # apt_name from chosen_fix
    Tool      : setup_commands = ["apt-get install -y --no-install-recommends {apt_name}"]
```

Runs after `resolve` (which sets `version`/`chosen_fix`) and before render. The
renderer (`build_script.py`) loses `_install_command` and simply emits
`node.setup_commands`, still hoisting one `apt-get update` and still topo-ordered.
Service / Config / DataAsset are now **executable nodes** carrying their own
`setup_commands` (filled at grounding) instead of comment-only `#@need` blocks —
this is what retires the off-graph `ScriptPatch` path.

The live emit path (`emit.py`) likewise reads `setup_commands` instead of
re-deriving from type. It may still coalesce consecutive Package nodes into one
pinned pip transaction — but that is now a *rendering optimization*, not the
source of truth, and is safe because `--no-deps` + the complete pinned closure
make per-node application order irrelevant.

## Certify Command: Already Aligned

No schema change. `check_command` is already a single per-node string and
`certify.py` is already the **sole** writer of `SATISFIED` (rc 0 → SATISFIED;
rc ≠ 0 → MISSING; no `check_command` → left UNKNOWN). The uniform model's certify
requirements are met today.

One constraint is **unchanged by this design and must stay**: a scratch container
cannot host a daemon, so SERVICE nodes are only certifiable on the live in-image
arm (`allow_service_certify` + `service_confidence == "confirmed"`). Making a
service an executable node does not change *where* it can be certified — off-arm
it stays UNKNOWN/soft. The physical constraint is independent of the schema.

## Grounding-Time Normalization

When a soft node is grounded (deterministic table match, or a gate/runtime
failure routed to repair), it is normalized into single-phase executable nodes:

- **Deterministic service table** (extends the existing `service_tables.py`):
  known daemons → `(apt package, start command, check command)`. Produces a
  `setup`-phase tool node + a `runtime`-phase service node + the `requires` edge.
- **PatchGate-normalization** for everything else: the LLM repair patch proposes
  the recipe; PatchGate splits install (setup) from start (runtime), attaches
  `setup_commands` to nodes, and rejects any orphan command not bound to a node.

`strength` promotion (`SOFT → HARD`) happens here too: a runtime/gate failure
that grounds a soft node promotes it, per the spec's "README mentions redis →
soft; pytest fails on :6379 → hard" rule. The host still owns `State`; the loop
owns `strength`.

## Patch Contract Changes

```text
NodeSpec   : ADD setup_commands, strength, phase    # the LLM writes the recipe onto the node
ProviderSpec : REMOVE                                # provider/requirement split collapses into the node
ScriptPatch  : REMOVE (off-graph block path)         # blocks become node.setup_commands
PatchGate  : validate — hard executable node has setup_commands AND a read-only check_command;
                         every setup command is bound to a node; LLM never sets state=satisfied
```

This is a net simplification of `patch.py` / `patch_gate.py`: one way for the
LLM to express a fix (nodes with `setup_commands`), one validator path, no orphan
script edits.

## Invariants Preserved

```text
Only check_command (run by the host) writes SATISFIED.            # certify.py unchanged
State is host-written and stays 3-valued.                          # decision 3
The pinned closure is materialized deterministically.             # populator, not LLM
Every persistent mutation is a graph node, grounded in evidence,  # uniform spec's central invariant
  emitted as setup_commands, certified by a host check.
No accepted setup command without a target node.                  # PatchGate
Script is always generated from graph nodes.                      # dumb renderer
```

## Explicitly Deferred

- **Static-analysis / LLM-scan construction** — now covered in the companion
  [2026-07-01-static-construction-and-node-enrichment-design.md](./2026-07-01-static-construction-and-node-enrichment-design.md)
  (discovery methods, fix/attempt memory, and reconnecting computed-but-discarded
  signals). This doc changes node shape and command generation only.
- **`NodeType.GATE`** — not adopted. Gates remain host-run observability per the
  gate-ladder decision; the spec's Gate node is documentation, not schema.
- **`evidence_refs` / EvidenceLedger** — node keeps inline `evidence` for now;
  moving to ledger refs is a separate change tied to the construction work.
- **Provider objects / alternatives history** — reintroduce later only if
  multiple providers per requirement become necessary (the uniform model's own
  stated deferral).

## Migration Risk Notes

- `chosen_fix` is read in many places (`emit`, `build_script`, `apt_resolve`,
  `relink`, `resolve_*`). Demoting it to provenance + routing commands through
  `setup_commands` is the largest blast radius; do it behind the populator so the
  reciped tiers' emitted commands are byte-identical to today before any service
  work lands.
- Service / Config / DataAsset becoming executable changes their render output
  from `#@need` comments to real blocks. Gate this so the off-arm artifact (no
  daemon certifiable) still renders sanely.
- `strength` defaults **SOFT**; the populator sets `HARD` explicitly on the reciped
  tiers (Package/SystemLib/Tool). See Review Corrections.

## Review Corrections (2026-07-01)

A code-grounded review surfaced fixes that **supersede the body where they conflict**.
These are implementation prerequisites, not optional.

- **Keystone — flip the executable-node gate.** `emit._is_reciped` /
  `emit._is_emittable` / `block._command_for` currently gate on
  `chosen_fix.startswith("apt:")` or `version`. They MUST gate on
  `bool(node.setup_commands)`. Without this, Service/Config/DataAsset with filled
  `setup_commands` still render as `#@need` stubs and the migration's central goal
  silently fails. (This single change also makes a soft LLM-proposed syslib emittable
  instead of inert — see the construction doc's C-3.)
- **Demote `fix_candidates`/`chosen_fix`, do NOT remove them.** "REMOVE fix_candidates"
  is unsafe: `fix_candidates` feeds `apt_verify.reconcile_apt_names` (the t64 apt-name
  reconciler — the real libGL-class fix) and `req_slice.ProviderView`; `chosen_fix`
  feeds `synthesis.bakeable_config_env`, `block.py`, and `_graph_hash`. Keep both as
  internal provenance / populator inputs; make `setup_commands` the canonical RENDER
  source; migrate readers incrementally (`req_slice` candidates → read `attempts`).
- **`strength` defaults SOFT** (populator sets HARD for reciped tiers). A HARD default
  makes every LLM-classified service node blocking and can stall the done-gate.
- **Keep a command-override path after `ProviderSpec` removal.** Add
  `replace_commands: bool = False` to `NodeSpec` (mirrors `ProviderSpec.override`);
  `validate_proposal`/`apply_proposal` honor it to rewrite `setup_commands` on an
  already-admitted node. Without it, repair can't correct a wrong command and
  convergence breaks.
- **Flag-gate the new PatchGate validation.** "hard node has setup_commands + read-only
  check" goes behind `require_setup_commands: bool = False` until the repair-loop prompt
  and `parse_patch_proposal` emit `setup_commands`; else every current proposal is rejected.
- **Update `parse_patch_proposal` + `_graph_hash` with the new fields.** Add
  `setup_commands`/`strength`/`phase` parsing to `NodeSpec` (else the LLM JSON is
  silently dropped); add `setup_commands` to the `_graph_hash` payload (else
  stale-artifact detection misses command changes).
- **"Byte-identical" applies only to the static renderer.** `build_script.py` emits
  per-node `setup_commands` verbatim (with `--no-deps`); `emit.build_recipe` (live path)
  keeps deriving ONE batched `pip install` (no `--no-deps`) from node metadata — it must
  not naively join the per-node `--no-deps` commands.
- **Companion cross-references.** The companion's 6 States map to our 3 + axes
  (`candidate≡strength=SOFT`, `blocked≡attempt-cap/conflicts_with`, `invalid≡known_invalid`);
  `CommandTask` is NOT adopted this iteration (express bounded setup as a Config/DataAsset
  node with `setup_commands`).

## Phase 1 Landed — Phase 2/3 Entry Conditions (2026-07-01)

Phase 1 (plan `docs/superpowers/plans/2026-07-01-uniform-graph-phase1-populator-renderer.md`)
landed on `v3-core` (commits `0d697b5..fbf82b4`): `Node.setup_commands`/`strength`/`phase` +
`populate_setup_commands` (the single static-path command producer) + renderer reads
`setup_commands` only (`build_script._install_command` deleted, no fallback) + the advisory
`frontier`→`unsatisfied_nodes` de-conflation. `tests/depgraph/` 577 pass; the rendered `setup.sh`
is byte-identical for the reciped tiers. The whole-branch review confirmed the four-commit
integration and surfaced two conditions to **resolve at the opening of the phase that touches them,
not mid-implementation**:

- **Phase 2 — the live-path command divergence is TWO differences, not one.** Migrating
  `emit.build_recipe` / `block._command_for` to read `setup_commands` requires deciding BOTH:
  (1) the `apt-get update &&` placement (already noted above), AND (2) the `--no-deps` axis —
  `block._command_for` (`src/python_deps/depgraph/block.py`) derives its install command WITHOUT
  `--no-deps`, whereas `populate._command_for` pins `--no-deps`. So the Phase-2 opening decision is:
  *what canonical form does `setup_commands` carry* (self-sufficient `apt-get update &&` and/or
  `--no-deps`, or not), and *do the live Block/recipe paths migrate to that form verbatim or keep
  deriving their own batched form*. Until decided, the static and live paths legitimately diverge
  (the "byte-identical applies only to the static renderer" note above).
- **Phase 3 — `_graph_hash` must gain `setup_commands` before custom commands exist.**
  `build_script._graph_hash` hashes only `(id, version, chosen_fix)`. In Phase 1 this stays correct:
  `populate` only writes `setup_commands`/`strength`, never a hashed field, so two graphs that hash
  equal still render identically. That guarantee BREAKS the moment Phase 3 lets LLM patches write
  arbitrary `setup_commands` — two nodes equal in `(id, version, chosen_fix)` but differing in
  `setup_commands` collide to the same manifest hash and skip a needed re-render. Phase-3 entry
  condition: add `setup_commands` to the `_graph_hash` payload (subsumes the earlier Review-Correction
  note). Tracked in the construction spec's Review Corrections.
