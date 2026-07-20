# GSM Phase 2a — PatchProposal + Deterministic PatchGate (Design)

**Status:** approved 2026-06-28 (brainstorming). Sub-spec of the master design
`docs/superpowers/specs/2026-06-28-graph-governed-script-materialized-agent-design.md`
(§9 Build/Lab Agent And Patch Proposal, §10 PatchGate/StateReducer, §16 Invariants, §18 Decisions).

**One line.** Build the typed `PatchProposal` data model and the deterministic
`PatchGate` (parse → validate → apply) on the dep-graph/block model, as standalone,
separately-tested, pure modules that touch neither `run_v1` nor `run_v3` — the v3
replacement for the LLM Maintainer, ready for 2b to wire into the loop.

## 1. Context and the 2a/2b split

The master spec's "Phase 2" bundles greenfield additive work (the typed patch
pipeline) with risky in-place surgery on the live `run_v3` loop and the shared
artifact finalizer. Per the user decision (2026-06-28) it is split, mirroring the
Phase-1 "build parts first, swap last" approach that landed cleanly:

- **2a (this spec) — additive, standalone, pure.** PatchProposal + PatchGate +
  provider action-class taxonomy + the soft/hard-edge seam. No run loop, no
  `build_agent`, no Docker, no LLM. Separately unit-tested like Phase 1.
- **2b (separate spec/plan) — the integration.** Rewrite `run_v3` to drive
  `compile_blocks → run_blocks → certify_refresh`; route the BuildAgent's output
  through PatchGate (the free-text→structured-output change, invariant #6); fork
  `emit_drain` off v3 and delete the dead `apply_recipe_patch` branch
  (`orchestrator.py:603-639`); switch the Dockerfile/fresh-replay source to the
  compiled `setup.sh`; add the LLM config/service classifier (which *consumes* this
  pipeline); generalize the `schedule._is_actionable` CONFIG/SERVICE carve-out.

2a depends only on the Phase-1 modules (`block`, `script`, `schema`, `ids`,
`schedule`) — all merged (`227de42..f7dd1d4`).

### Patch-authority decision (resolves the §9↔invariants tension)

The master spec's §9 example shows the agent emitting **both** graph patches
(`add_providers`) **and** `script_patches` (op `add_block`, editing the artifact
directly), while invariants #1/#2 say the graph is the authority and the script is
only a compiled projection. **User decision (2026-06-28): keep `script_patches`** —
the agent CAN add blocks directly. 2a reconciles this with the invariants by making
script_patches a **governed block overlay** (§7 below): accepted but validated,
evidence-cited, host-certified, and never able to write node state. The graph stays
the *semantic/state* authority; the artifact gains agent-authored blocks the
compiler cannot synthesize.

## 2. Scope

**In scope (2a):**
- `PatchProposal` and its component specs (`NodeSpec`, `ProviderSpec`, `EdgeSpec`,
  `ScriptPatch`), all frozen dataclasses, with a tolerant parser.
- `PatchGate`: `validate_proposal` (all §10 checks) and `apply_proposal` (pure,
  immutable, returns graph + accepted blocks).
- Provider **action-class taxonomy** (`matches_action_class`) — the §10 piece with
  no existing implementation.
- `compose_script(graph, manual_blocks)` — the recompile-after-mutation entry point
  (compiled blocks ∪ governed manual blocks).
- The **soft/hard-edge seam**: `Edge.data["hard"]` honored by
  `schedule._dependencies_satisfied` (behavior-preserving; all current edges hard).
- A 2a invariant suite + the full-suite regression gate.

**Out of scope (deferred to 2b or later):**
- The BuildAgent emitting a `PatchProposal` (structured output) — 2b.
- Routing the loop through PatchGate; the `emit_drain` fork; the dead-branch delete;
  the Dockerfile artifact switch — 2b.
- The LLM config/service/data classifier that turns static hits into soft-hint
  proposals — 2b (it consumes this pipeline; needs the LLM).
- Generalizing the `schedule._is_actionable` CONFIG/SERVICE carve-out — 2b (where
  the first soft edges actually appear).
- Lab-container experiments and causal edges — Phase 5 (the agent's `rationale` is
  carried as advisory text only in 2a).

## 3. Invariants 2a upholds (from §16)

- **#1 graph is authority / #2 script is a projection:** the persisted script is
  always `compose_script(graph, accepted_manual_blocks)` — recomputed after every
  mutation; the LLM never edits a persisted artifact in place.
- **#3/#4 only host checks write SATISFIED:** `apply_proposal` NEVER sets
  `State.SATISFIED`; new nodes land `MISSING`/`UNKNOWN`. `validate_proposal` rejects
  any proposal that attempts a SATISFIED write.
- **#6 LLM output accepted only as a structured PatchProposal:** the typed model +
  tolerant parser ARE this contract.
- **#8 every accepted node/edge/block cites evidence:** `validate_proposal` requires
  an existing `evidence_ref` on each added requirement/block (a `known_evidence_ids`
  frozenset is passed in; in 2a tests it is supplied directly, in 2b it comes from
  the `EvidenceBundle`).
- **#10 soft edges/hints do not block scheduling:** the `Edge.data["hard"]` filter.
- **#11 State enum unchanged:** `{UNKNOWN, MISSING, SATISFIED}`; Hint/Candidate/Active
  is `Node.data["promotion"]` + `Edge.data["hard"]`, never a state value.

## 4. Data model — `src/python_deps/depgraph/patch.py`

All frozen dataclasses. Field names follow the §9 LLM-facing JSON; the parser maps
them to the dep-graph schema vocabulary (`schema.py`).

```text
NodeSpec      id:str  type:str  name:str  layer:str  check_command:str|None
              evidence_ref:str|None  promotion:str|None   # "hint"|"candidate"|None
ProviderSpec  id:str  kind:str  command:str  provides:tuple[str,...]
EdgeSpec      source:str  relation:str="requires"  target:str  hard:bool=True
ScriptPatch   op:str="add_block"  block_id:str  wave:str  commands:tuple[str,...]
              target_node_ids:tuple[str,...]  checks:tuple[str,...]=()
              provides:tuple[str,...]=()  evidence_ref:str|None=None
PatchProposal rationale:dict                          # advisory only
              add_requirements:tuple[NodeSpec,...]=()
              add_providers:tuple[ProviderSpec,...]=()
              add_edges:tuple[EdgeSpec,...]=()
              script_patches:tuple[ScriptPatch,...]=()
              request_checks:tuple[str,...]=()
              def is_empty() -> bool
```

Mapping to the live schema (verified):
- `EdgeSpec.source/target` → `schema.Edge.src/dst`; `EdgeSpec.relation` (a string)
  → `schema.EdgeType(relation)` (currently `"requires"` → `EdgeType.REQUIRES`);
  `EdgeSpec.hard` → `Edge(data={"hard": hard})` (the `data` bag is a read-only
  `MappingProxyType`, so hardness is set at construction, never mutated).
- `NodeSpec.type/layer` (strings) → `schema.NodeType(...)`/`schema.Layer(...)`;
  added nodes get `discovered_by=DiscoveredBy.PROBE` for agent proposals (or
  `RUNTIME` when the caller is the runtime classifier), `state=State.MISSING`,
  `data={"promotion": ...}` when a promotion tag is present.
- `ProviderSpec` → sets the target requirement node's `chosen_fix` (e.g.
  `"apt:libpq-dev"` / a pip spec / the raw command) per the existing
  `emit._apt_name`/`_pip_spec` contract, so `compile_blocks` regenerates the block.

`parse_patch_proposal(d: dict) -> PatchProposal` — tolerant: missing optional
sections default to `()`, unknown keys are ignored, the §9 example parses verbatim.
It performs **no** validation (that is the gate's job) — it only shapes the dict.

## 5. PatchGate — validation (`src/python_deps/depgraph/patch_gate.py`)

`validate_proposal(graph, proposal, *, known_evidence_ids: frozenset[str]) -> list[str]`
returns a (possibly empty) list of human-readable error strings; empty = accept.
Mirrors the `contracts/validation.py` shape (return error list, immutable input) but
re-authored on the dep-graph vocabulary. Checks (all of §10):

1. **Schema/required fields** — every spec has its required fields; enum strings
   resolve (`NodeType`, `Layer`, `EdgeType`).
2. **Canonical node ids** — each added/referenced id matches the `ids.py` builder
   for its type (`package_id`/`syslib_id`/`tool_id`/`config_id`/`service_id`/…); a
   non-canonical id (e.g. `libplacebodev`) is rejected, not silently mapped.
3. **Evidence exists** — each `add_requirements[*].evidence_ref` and each
   `script_patches[*].evidence_ref` is in `known_evidence_ids` (#8).
4. **Reject SATISFIED writes** — no spec may carry `state == "SATISFIED"` (or any
   state field at all on added nodes); the gate is structurally incapable of
   certifying (#3/#4).
5. **Dedupe** — added node/provider/edge/block ids are unique within the proposal.
   A re-proposed existing node whose `(type, layer, check_command)` are identical is
   a no-op (allowed); a re-proposed id whose `type`/`layer`/`check_command` differ
   from the node already in `graph` is a *conflicting redefinition* and is rejected.
6. **Script block targets exist** — every `ScriptPatch.target_node_ids` entry exists
   in `graph` **after** `add_requirements` are notionally applied (§10 "ensure
   script block targets graph nodes"). A dangling target is rejected.
7. **Check commands read-only** — `check_command`s and `ScriptPatch.checks` contain
   no mutating verb (`apt-get install`, `pip install`, `rm`, `>`, …); §10 "ensure
   check commands are read-only".
8. **Action class (providers only)** — each `ProviderSpec` command satisfies
   `matches_action_class(kind, command)` for its declared `kind` (§8 below).
   `ScriptPatch` commands are the agent's deliberate escape hatch (shell-class by
   nature, per the keep-script_patches decision) and are NOT action-class-constrained;
   they are governed instead by target-exists (check 6), read-only checks (check 7),
   evidence (check 3), and dedupe (check 5).
9. **Edge relation validity** — `(src_type, relation, dst_type)` is allowed by the
   existing schema relation-rules table (`schema.py` "relation -> allowed src/dst").

The gate **normalizes** only safely (§10): it may attach a canonical-id-derived
`name`, but it does NOT do creative repair (no inventing providers, no guessing
package names) — that is the BuildAgent's job.

## 6. PatchGate — apply (`src/python_deps/depgraph/patch_gate.py`)

`apply_proposal(graph, proposal) -> ApplyResult` where
`ApplyResult(graph: DepGraph, blocks: tuple[Block, ...])` is a frozen dataclass.
Pure and immutable (returns a new `DepGraph` via `with_node`/`with_edge`; the input
is untouched), assuming `validate_proposal` already passed (callers validate first;
apply re-asserts the SATISFIED guard defensively):

- `add_requirements` → `graph.with_node(Node(..., state=MISSING, discovered_by=…,
  data={"promotion": …}?))`.
- `add_providers` → bind `chosen_fix` on the named target node(s) (a new node copy).
- `add_edges` → `graph.with_edge(Edge(src, dst, EdgeType(relation),
  data={"hard": hard}))`.
- `script_patches` → converted to governed `Block`s (§7) and returned in
  `ApplyResult.blocks`; they do NOT mutate node state.
- `request_checks` is carried through (2b feeds it to `certify_refresh`); in 2a it
  is validated and echoed, not executed.

`apply_proposal` NEVER writes `State.SATISFIED` — a unit test asserts this over an
adversarial proposal.

## 7. script_patches as a governed overlay + `compose_script`

A `ScriptPatch` (op `add_block`) is accepted as a first-class block, but **governed**:
it must target real graph nodes (#6.6), cite evidence (#8), pass the read-only-check
(#6.7) and action-class (#6.8) validations, and it **never certifies** — host checks
via `certify_refresh` still own node state (#3/#4).

The persisted script is always recomputed from the graph plus the accepted manual
blocks:

```text
compose_script(graph: DepGraph, manual_blocks: tuple[Block, ...]) -> tuple[Block, ...]
    compiled = compile_blocks(graph)                 # Phase-1, pure projection
    merge:   dedupe by block_id (graph-compiled wins on collision),
             manual blocks placed after compiled blocks WITHIN their wave,
             stable order preserved across waves (system → … → pip → tests).
    return the merged tuple   # 2b renders it via render_setup_sh
```

This is the **recompile-after-mutation** entry point: 2b calls `compose_script`
after every accepted patch so the artifact never drifts from the graph. Invariant #1
holds (the graph is the state authority; manual blocks only enrich the projection),
#2 holds (the script still certifies nothing), and the user's chosen expressiveness
is preserved.

## 8. Provider action-class taxonomy — `src/python_deps/depgraph/action_class.py`

The §10 "provider command matches allowed action class" check has no implementation
in the tree. 2a defines it:

```text
ACTION_CLASSES = {
    "apt":   r"^apt-get(\s+update\s*&&\s*apt-get)?\s+install\b",
    "pip":   r"^(python3?\s+-m\s+)?pip\s+install\b",
    "npm":   r"^npm\s+(install|ci)\b",
    "shell": r".",            # explicit escape hatch; flagged in rationale, allowed
}
matches_action_class(kind: str, command: str) -> bool
```

It applies to `ProviderSpec` commands (which carry a `kind`). The §14 "wrong apt
package name" failure case (a `ProviderSpec` with `kind="apt"` but a command that is
not `apt-get install`) is the regression test the gate must reject. `kind="shell"`
is the explicit, auditable escape hatch for genuine recipes; it matches anything but
is recorded as such (2b can choose to require stronger evidence for shell-class
providers). `ScriptPatch` commands are not run through this matcher (§5 check 8).

## 9. Soft/hard-edge seam (minimal, behavior-preserving)

- `Edge` already has a `data` bag. Convention: `data.get("hard", True)` — absence
  means hard (so every existing edge is hard; no migration).
- `schedule._dependencies_satisfied(graph, node)` adds the filter so a soft edge
  never blocks scheduling (#10):

```python
if edge.src == node.id and edge.relation is EdgeType.REQUIRES \
        and edge.data.get("hard", True):
    # ... existing satisfied check ...
```

Because all current edges are hard, this is a no-op for existing behavior; a unit
test with a synthetic soft edge proves a soft, unsatisfied dependency does NOT keep
the node off the frontier. The `_is_actionable` CONFIG/SERVICE carve-out
generalization stays in 2b.

## 10. Testing (pure — no Docker, no LLM)

Phase-1 discipline (strict TDD, frozen dataclasses, `python_deps.*` imports):

- **parse:** §9 example round-trips; missing optional sections default to `()`;
  unknown keys ignored.
- **validate — accept:** a well-formed proposal (canonical ids, evidence present,
  apt provider, requires edge) returns `[]`.
- **validate — reject (one test per class):** SATISFIED write; non-canonical id;
  missing evidence_ref; dangling script-block target; mutating check command;
  action-class mismatch (apt kind, non-apt command); duplicate id; illegal edge
  relation.
- **apply:** immutable (input graph unchanged); requirement node added as MISSING
  with promotion tag; provider sets `chosen_fix`; soft edge carries
  `data["hard"]=False`; `ApplyResult.blocks` carries governed script_patch blocks;
  **never SATISFIED** (adversarial proposal).
- **action_class:** table-driven matcher (apt/pip/npm/shell positive + negative).
- **compose_script:** compiled ∪ manual dedupe by block_id; manual-after-compiled
  within wave; stable wave order; round-trips through `render_setup_sh`/`parse_setup_sh`.
- **soft-edge filter:** synthetic soft edge does not block frontier; hard edge does.
- **2a invariant suite** (`tests/depgraph/test_gsm_invariants_phase2a.py`): "apply
  never yields SATISFIED", "every accepted block targets an existing node", "validate
  is pure (graph unchanged)".
- **Full-suite gate:** `python3 -m pytest tests -q -p no:cacheprovider` shows only
  the 4 known pre-existing failures, 0 new (no leakage into existing import paths).

## 11. File structure

```text
src/python_deps/depgraph/patch.py          # specs + PatchProposal + parse_patch_proposal
src/python_deps/depgraph/patch_gate.py     # validate_proposal, apply_proposal, ApplyResult, compose_script
src/python_deps/depgraph/action_class.py   # ACTION_CLASSES, matches_action_class
src/python_deps/depgraph/schema.py         # (tiny edit) Edge.data["hard"] convention documented; no field change
src/python_deps/depgraph/schedule.py       # (tiny edit) _dependencies_satisfied honors data["hard"]
tests/depgraph/test_patch_parse.py
tests/depgraph/test_patch_gate_validate.py
tests/depgraph/test_patch_gate_apply.py
tests/depgraph/test_action_class.py
tests/depgraph/test_compose_script.py
tests/depgraph/test_soft_edge_seam.py
tests/depgraph/test_gsm_invariants_phase2a.py
```

Each file < 400 lines; pure modules carry no Docker/network/LLM imports. Reuse
(do not reimplement): `block.Block`/`compile_blocks`, `script.render_setup_sh`/
`parse_setup_sh`, `schema.{Node,Edge,EdgeType,NodeType,Layer,State,DiscoveredBy,DepGraph}`,
`ids.*`, `emit._apt_name`/`_pip_spec`, `schedule.{_dependencies_satisfied,scheduler_frontier}`.

## 12. Decisions log

- **2a/2b split** (user, 2026-06-28): additive PatchGate first, integration second.
- **Keep `script_patches`** (user, 2026-06-28): reconciled as a governed overlay
  (§7); graph remains the state authority, script stays a projection.
- **Hard-edge seam in 2a; `_is_actionable` carve-out deferred to 2b** (approved).
- **LLM config/service classifier out of 2a** (approved) — it consumes this pipeline.
- **PatchGate ports the three-stage shape, not the code**, from
  `contracts/{patch,validation,apply}.py` (those are 100% coupled to the old
  Contract/Blocker/Attempt model; only the parse→validate→apply idiom transfers).

## 13. 2b preview (not built here)

For continuity, 2b will: emit `PatchProposal` from the BuildAgent via structured
output (replacing the `Action:`/`Final Answer:` free-text parse,
`build_agent.py:162,207`); call `validate_proposal`→`apply_proposal`→`compose_script`
→`render_setup_sh` each cycle; fork `emit_drain` (`depgraph_live.py:89`) so v3 stops
producing `RecipePatch`; delete the dead `apply_recipe_patch` branch
(`orchestrator.py:603-639`); switch `_finalize_supervisor_artifacts`
(`agent.py:1638`) to the compiled `setup.sh` for v3 (keeping ledger-replay for v1);
add the LLM classifier feeding soft-hint proposals; keep `_verified_test_run_passed`
as the binding done-gate (§18 #3); expose the internal script-materialization toggle
for the §14 B3 ablation.
