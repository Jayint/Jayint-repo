# Graph → Whole Build Script Renderer — Design

> **Extends:** `docs/superpowers/specs/2026-06-28-graph-governed-script-materialized-agent-design.md` (GSM — graph edited, script rendered, host certifies) and `docs/superpowers/specs/2026-06-29-gate-ladder-outer-loop-design.md` (the loop that consumes the artifact). This document specifies **only the pure projection** `DepGraph → setup.sh` — how the graph compiles into one complete, structured, correct, easy-to-debug build script. It deliberately says nothing about *when/how* that script runs in the loop (block-stepping, certification timing, incremental vs fresh, `docker commit`); those are owned by the gate-ladder spec and are out of scope here (§11).

**Status:** DESIGN (decided 2026-06-29, via brainstorming dialogue). Not yet implemented.

---

## 1. Problem

The graph already renders a whole `setup.sh` today (`compile_replay_blocks` → `_merge_manual_blocks` → `render_setup_sh`), and it gets the hard structural thing right: topo-ordered, one annotated block per node, rendered-not-authored. But the output is weak on the three properties we want:

- **Structured** — it is a flat concatenation. No tier/section headers, no top-of-file manifest, no grouping. You read it as one undifferentiated list.
- **Correct** — `render_setup_sh` emits only `#@targets`/`#@provides`/`#@check` and **drops `evidence_refs`**, so the artifact is not a *lossless* projection of the graph (you cannot recover *why* a node is there from the script alone). Per-block `apt-get update` is repeated for every system node.
- **Easy to debug** — it is install-only with `#@check` as comments consumed by the harness, and the block handle `{layer}.{short}` (`block.py:_block_id_for`) can collide for two same-named nodes in a layer.

This spec defines a **new pure renderer** that produces the whole artifact with explicit structure, lossless provenance, and a clean separation between what the host can *prove installable* and what the LLM must *contribute*.

## 2. Philosophy (unchanged)

- **Graph = source of truth; script = compiled artifact.** The renderer is a pure projection of the graph. It never writes `node.state` (it renders; it does not certify — single authority preserved).
- **The host renders only what it can reliably install.** Everything else is surfaced *to* the LLM as information, and re-enters *from* the LLM only through the governed typed-patch channel.
- **Every line in the script is either host-guaranteed or evidence-tagged.** A reader can tell each line's authority at a glance.

## 3. The pure function

```python
# src/python_deps/depgraph/build_script.py  — pure: no Docker, no network, no LLM, no src.envstate
def render_build_script(graph: DepGraph, manual_blocks: tuple[Block, ...] = ()) -> str: ...
```

- **New function, not a change to `render_setup_sh`.** The existing `render_setup_sh` ↔ `parse_setup_sh` pair is a lossless round-trip the live block-stepped loop depends on, and the gate-ladder spec calls out a *byte-identical* guarantee for the `enable_script_materialization=False` (B3) arm that must not be perturbed. The new renderer hoists shared setup and adds section headers, so it is intentionally **not** round-trip-parseable back to one-block-per-node. The two coexist: `render_setup_sh` is the live-loop format; `render_build_script` is the whole-artifact projection.
- **Reuses existing graph logic** — `topo_order`, `_is_reciped`, `_apt_name`, `_pip_spec`, `_LAYER_RANK` (emit.py), `Block` (block.py), `graph.requires_of` (schema.py). No new graph traversal semantics.
- **Execution model: install-only, `set -Eeuo pipefail`, Dockerfile-style.** No inline self-certifying guards in v1; `#@check` is emitted as a comment (how to verify by hand), leaving the door open to a self-certifying upgrade later (§12).

## 4. Scope split — deterministic core vs LLM-governed tail

The split is **not new scope**; it is the seam that already exists as `_is_reciped` (emit.py:108):

```python
def _is_reciped(node):
    if node.type is NodeType.PACKAGE:    return bool(node.version)             # pip, pinned
    if node.type in (SYSTEM_LIB, TOOL):  return chosen_fix.startswith("apt:")  # apt
    return False   # CONFIG / SERVICE / DATA_ASSET / RUNTIME fall through
```

The renderer makes that split intentional and produces a **three-region** script:

1. **Deterministic core** — `_is_reciped` nodes (system + language packages the host can reliably install). Emitted as real commands (`#@node`).
2. **LLM-governed blocks** — `manual_blocks` (typed `ScriptPatch`es already admitted through `PatchGate`), emitted as real commands marked `source=llm-patch` (`#@block`).
3. **Unsatisfied needs** — `CONFIG`/`SERVICE`/`DATA_ASSET` nodes **not** covered by any `manual_block`, emitted as **comment-only stubs** carrying the node's information for the LLM to reference (`#@need`, no command).

**Everything else is omitted.** Goal nodes (`Test`/`Project`/`Import`), `Platform`/`Runtime`/interpreter, and naming nodes are neither `_is_reciped` nor config/service — they are structural or base-image assumptions and produce no script line. The resolved interpreter version is recorded in the manifest header (`python:`, §9) instead of as an install action.

**Lifecycle (closes the loop).** A service/config node is a `#@need` stub until the LLM proposes a block targeting it (via the governed channel, §7/A); on the next render that node is covered by a `manual_block` and becomes a `#@block`. The stub is how the script *asks*; the governed patch is how the LLM *answers*. Every answered line stays evidence-/target-tagged.

## 5. Ordering rule — hard tier sections + intra-tier topology

**Order = hard tier sections, topologically sorted within each tier.**

```
for layer in [INTERPRETER, SYSTEM, TOOLCHAIN, PIP, NAMING, RUNTIME, TESTS, CONFIG, SERVICES]:
    emit section header (skip if the section is empty)
    for node in topo_order(graph, nodes_in_this_layer):   # intra-tier deps (e.g. pip→pip)
        emit line
```

This differs from today's `topo_order`, which is a *global* Kahn pass using tier (`_LAYER_RANK`) only as a **tiebreak** among simultaneously-ready nodes — so today's tier ordering is *emergent*, not *enforced*.

**Why hard tier sectioning is provably safe (cannot violate a dependency):** `EDGE_RULES` (schema.py:88) restricts `requires` **sources** to `{Test, Project, Import, Package, Service, Config}`. `SystemLib`, `Tool`, `Runtime`, `Platform` are *never* requires-sources — they have no outgoing dependencies and are always topological **leaves**. Therefore cross-tier dependencies only ever point **downward** (a Package requires a SystemLib, never the reverse). Partitioning by tier can never separate a dependency from its dependent in the wrong order. The only intra-tier ordering that matters for correctness is **pip→pip** (a build-from-source sdist needing an already-installed build dependency), which `topo_order` over the PIP-layer nodes handles.

Benefits: section headers become **honest** ("everything under SYSTEM runs before everything under PIP" is a structural guarantee, not a sort-key side effect), and the output is maximally readable.

## 6. Output format (worked example)

```bash
#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 4 reciped (1 system, 1 toolchain, 2 pip) + 2 needs (1 service, 1 config)
#   closure: transitive-complete, fully pinned
#   graph-hash: sha256:ab12cd…   python: 3.11   platform: linux/amd64   exclude-newer: 2026-06-01
#
set -Eeuo pipefail

# ==================== SYSTEM ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update                                                # hoisted once (not per-node)

#@node syslib:libpq-dev  provider=apt:libpq-dev  requires=-  unblocks=pkg:psycopg2  evidence=ev:pyproject:psycopg2
apt-get install -y --no-install-recommends libpq-dev

# ==================== TOOLCHAIN ====================
#@node tool:gcc  provider=apt:gcc  evidence=ev:build:psycopg2  toolchain
apt-get install -y --no-install-recommends gcc

# ==================== PIP  (pinned, topo-ordered, --no-deps) ====================
#@node pkg:typing-extensions  version=4.11.0  requires=-  evidence=ev:resolver
python3 -m pip install --break-system-packages --no-deps typing-extensions==4.11.0

#@node pkg:psycopg2  version=2.9.9  requires=syslib:libpq-dev,tool:gcc  build-from-source  evidence=ev:import:psycopg2
python3 -m pip install --break-system-packages --no-deps psycopg2==2.9.9

# ==================== SERVICES / CONFIG  (NOT host-installable — for the agent to satisfy) ====================
#
#@need service:postgres   state=MISSING   requires=pkg:psycopg2
#@check  pg_isready -q
#@evidence ev:readme:db   "README: requires PostgreSQL listening on :5432"
#     (no command — propose a governed block to satisfy this)
#
#@need config:DATABASE_URL   state=MISSING
#@evidence ev:settings:DATABASE_URL   "settings.py reads env DATABASE_URL"
#     (no command)
```

Three structural choices doing the work:

1. **Hoisted `apt-get update`** — once per script, not once per node (the whole-script framing unlocks this; the per-block model could not, since each block had to self-contain its update for independent replay).
2. **`--no-deps` on every pip install** — because the graph is a *complete, pinned transitive closure* installed in topo order, the script tells pip to install exactly what the graph says and never re-resolve. Without it, `pip install A==1` can pull a transitive `C==2` that contradicts the `C==1.5` node — non-deterministic drift. With it, the script is a faithful 1:1 projection of the graph.
3. **Tier section headers + manifest header** — a human reads top-to-bottom, and `set -e` failures land in a *named section* next to a node line that says what it is and why.

## 7. Annotation model (three kinds)

| annotation | authority | has a command? | meaning |
|---|---|---|---|
| `#@node` | host-compiled | yes | deterministic, reliably installable (system + pip) |
| `#@need` | unsatisfied requirement | **no** (comment) | the LLM's worklist — node info to *reference* |
| `#@block` | LLM-authored, governed | yes | a `#@need` filled in via `PatchGate` (`source=llm-patch`) |

**Field sources** (all real `Node` fields, schema.py:117):

- `#@node <id>  version=<version>  provider=<chosen_fix>  requires=<requires_of, reciped, or '-'>  [unblocks=<required_by — what breaks if this fails>]  [build-from-source] [toolchain]  evidence=<evidence via advise._best_evidence_line>` — followed, when the node has a `check_command`, by a separate `#@check <command>` comment line. (Checks are emitted as their own `#@check` line for **all three** kinds, matching the existing `script.py` `#@check` convention; in install-only mode they are comments, not executed.)
- `#@need <id>  state=<state>  requires=<requires_of or '-'>` then `#@check <check_command>` and `#@evidence <evidence>` on following comment lines (no command line).
- `#@block <block_id>  source=llm-patch  targets=<target_node_ids>  evidence=<evidence_ref>` then `#@check <checks>` (read-only, validated by PatchGate) then the command body.

The debug handle is **`node.id`** (globally unique by construction), replacing the collision-prone `{layer}.{short}` block id for `#@node`/`#@need`. `#@block` keeps the patch's `block_id` (the LLM-chosen handle, already unique within the proposal and deduped on merge).

**(A) — how the LLM's answers re-enter (decided).** Config/service blocks flow through the **existing typed `ScriptPatch → PatchGate` path** (`patch_gate.py:97` requires: cite evidence, name target node(s), read-only checks; the command body is the LLM's to write). They are merged into `manual_blocks` and rendered as `#@block`. The LLM never hand-edits the rendered file; it proposes governed blocks that get rendered into it. (The rejected alternative was a free-form escape hatch with no gate and no provenance.)

## 8. Correctness invariants (what the renderer guarantees)

1. **Tier-sectioned + intra-tier topo** (§5) — deps before dependents; cross-tier safe by EDGE_RULES.
2. **Faithful pinned projection** — every package `==version` + `--no-deps`; the deterministic install set ≡ the `_is_reciped` set (no drops, no extras).
3. **Self-sufficient** — single hoisted `apt-get update` + `DEBIAN_FRONTEND=noninteractive`; `--no-install-recommends`.
4. **Byte-reproducible** — pure function, stable sort, **no timestamps / no randomness** ⇒ same graph yields a byte-identical script; output is invariant to node insertion order. `graph-hash` is a content hash (sorted node ids + version + provider + edges).
5. **Fail-fast & localizable** — `set -Eeuo pipefail`; one action per line ⇒ a non-zero rc maps to exactly one named node.
6. **Pure projection** — never writes `node.state`; renders the three regions of §4 and nothing else.

*Optional hardening (flag, off by default):* `--require-hashes` using `node.hash` when every reciped package carries a hash (supply-chain reproducibility).

## 9. Manifest header

A `# …`-commented preamble before `set -Eeuo pipefail` containing: the "DO NOT EDIT — compiled artifact" banner; per-layer counts plus a needs count; the content `graph-hash`; and `python` / `platform` / `exclude-newer` pulled from the resolved closure (`node.resolved_python` / `resolved_platform` / `exclude_newer`, consistent across the closure by construction; each field omitted if absent). The header carries no timestamp (byte-reproducibility, §8.4).

## 10. Testing (all pure, no Docker)

- **Golden snapshot** — a fixture graph (a syslib + a tool + a 2-package set including one `build_from_source`, plus one service and one config node) → assert the exact `setup.sh` string, byte-for-byte, with the opaque `graph-hash` digest masked to a placeholder (its value is separately covered by the determinism property). Pins format, ordering, hoisting, and the three annotation kinds.
- **Properties:**
  - (a) every `_is_reciped` node appears exactly once as an install line;
  - (b) for every REQUIRES edge among reciped nodes, the dependency's line precedes the dependent's;
  - (c) `render(g) == render(g)` and `render(g) == render(shuffle(g.nodes))` (determinism / insertion-order invariance);
  - (d) the deterministic install-target set ≡ `compile_replay_blocks` targets (parity — no installs dropped or added vs the existing path);
  - (e) exactly one `apt-get update` regardless of system-node count;
  - (f) every non-reciped `CONFIG`/`SERVICE`/`DATA_ASSET` node *not* covered by a `manual_block` is emitted as a `#@need` stub with no command; every `manual_block` is emitted as a `#@block`.

## 11. Out of scope (non-goals)

- **Loop integration** — when/how the script runs, certification timing, incremental vs fresh-from-base, `docker commit` checkpoints. Owned by the gate-ladder spec (`2026-06-29-gate-ladder-outer-loop-design.md`).
- **The live block-stepped path** — `render_setup_sh` / `parse_setup_sh` / `block_emit` / `run_blocks` are untouched; this renderer is additive.
- **Inline self-certification** — checks are comments in v1 (`#@check`), not executed guards (§12).
- **Discovery / classification** — turning failures into graph obligations is the repair-loop / classifier's job, not the renderer's.

## 12. Future / open questions

- **Self-certifying upgrade** — promote `#@check` from a comment to an executed guard (`<check> || { echo "::FAIL <id>"; exit 1; }`) so the artifact verifies itself when run standalone. The annotation model already carries the check; this is purely a render-mode switch.
- **Multi-language packages** — "language packages" is pip-only today (the architecture is Python-coupled; an `EcosystemProvider` seam is planned). When a Node provider taxonomy generalizes (npm, etc.), the deterministic core extends along the same `_is_reciped`-style boundary; `#@node` rendering becomes provider-dispatched.
- **`--require-hashes`** — gate on a hash-complete closure (§8 optional hardening).

## 13. Change surface (indicative)

| Component | Change | Effort |
|---|---|---|
| `python_deps/depgraph/build_script.py` (new) | `render_build_script(graph, manual_blocks)`; section grouping; three annotation kinds; manifest + graph-hash | M |
| `python_deps/depgraph/emit.py` | reuse `topo_order` per-layer; possibly expose a small `nodes_by_layer` helper | S |
| (no change) `script.py`, `block.py`, `block_emit.py`, `patch_gate.py` | live-loop path untouched; `manual_blocks` already produced by `admit_proposal` | — |
| test suite | golden snapshot + properties (§10) | M |

## 14. Summary

A new pure `render_build_script(graph, manual_blocks)` projects the graph into one whole, install-only `setup.sh`: **hard tier sections, topologically ordered within each tier** (provably safe — cross-tier deps only point downward), with **hoisted `apt-get update`** and **`--no-deps`-pinned** per-node pip installs (a faithful 1:1 of the fully-pinned transitive closure). The **deterministic core** covers exactly what the host can reliably install (`_is_reciped`: system + pip); **service/config** nodes appear as comment-only **`#@need` stubs** the LLM references, and its answers re-enter only through the governed **`ScriptPatch → PatchGate`** channel, rendered as **`#@block`** lines. Three annotation kinds (`#@node` / `#@need` / `#@block`) make every line's authority legible; a content `graph-hash` and timestamp-free output make it byte-reproducible. Loop integration, certification, and self-certifying checks are explicitly out of scope.
