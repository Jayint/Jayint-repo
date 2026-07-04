# Spec: Multi-language ecosystem seam migration (zero-impact Python)

Status: spec (ready for SDD)
Branch: `john-planner-v3-core-autoresearch`
Extends: `docs/superpowers/specs/2026-06-27-ecosystem-provider-seam.md` (the prototype seam design — two axes §2, interface §4, schema §5, philosophy §12, per-ecosystem appendix §15). That spec was written against a standalone `src/ecosystems/graph.py` shape-mirror in a separate worktree (`john-planner-v3-ecosystems`). **This spec retargets it at the REAL pipeline on this branch** and makes byte-identical Python output the governing constraint.
Motivation source: three research findings (job `366037cb`) — the current-branch coupling audit (`research_current_arch.md`), the prototype PORT-vs-adapter inventory (`research_prototype_gap.md`), and the zero-impact regression gate (`research_zero_impact.md`). This spec builds ON those; it does not re-derive them.

## Problem

`build_dep_graph` (`src/python_deps/depgraph/build.py:433-644`) is the whole construction pipeline, and it is an **unconditional, Python-shaped dispatch**: it assumes Python at every stage with no ecosystem boundary. The pipeline stages themselves (scan→roots→resolve→repair fixpoint; relink→ldd→probe→reconcile→certify) are already language-agnostic in *shape* — but `build_dep_graph` hard-wires the Python populators inline.

The leak is not in what the stages *call* (that code is already cleanly Python-provider-shaped); the leak is that the orchestrator invokes them unconditionally. The concrete Python-assuming call sites (from `research_current_arch.md` §4):

- `build.py:491` — `scan_to_nodes(repo_path)` (AST scan of `.py`).
- `build.py:531-533` — `select_roots(...)` (PEP 621 / `setup.py` / requirements reading).
- `build.py:539-540` — `compute_exclude_newer(roots)` (PyPI era anchor, defaulted here).
- `build.py:542-560` — Runtime node built inline from `_runtime_id` + a `python3 -c "... sys.version_info ..."` check.
- `build.py:569-571` — `composite_record_provider(default_record_provider(...), pypi_record_provider())` (PyPI record oracle, defaulted here).
- `build.py:600` — `_add_project_node(graph, repo_path)` (pyproject/setup.py hub).
- `build.py:618` — `certified_import_links(...)` (`packages_distributions()` relink).
- `build.py:624` — `ldd_probe(...)` (`.cpython-NN*.so` DT_NEEDED).
- `build.py:628` — `import_probe(...)` (`python -c "import X"` backstop).

Every one of these calls into code that is *already* Python-provider-shaped; the defect is only that `build_dep_graph` calls them **without dispatch**, so a Rust or Node repo has no path through construction at all. There is no `EcosystemProvider`, no `PythonProvider`, no `Node.ecosystem` field yet (grep-confirmed, `research_zero_impact.md`). A separate `ecosystem-provider-seam` prototype worktree built Node + Rust providers against a **lossy standalone graph** that cannot round-trip into this pipeline (`research_prototype_gap.md` §3) — so it is source material to PORT, not code to import.

## Goal (single, fixed)

Introduce an `EcosystemProvider` seam ABOVE `python_deps` so that Rust and Node (later Go/Java/Ruby/PHP) become **sibling construction paths** through the same Phase-1/Phase-2 engine, with the Python path relocated behind a `PythonProvider` **wrapper**.

**The governing acceptance criterion is HARD and non-negotiable: zero impact on the current Python pipeline — byte-identical output before vs after.** The seam is proven correct only when the three-oracle zero-impact gate (§ Zero-impact strategy) stays green with artifacts byte-identical. Multi-language support is the feature; byte-identical Python is the gate that admits it. If any Python output changes, the slice is wrong by definition, not "close enough."

The engine (Phase 1 = scan→roots→resolve→repair fixpoint; Phase 2 = relink→ldd→probe→reconcile→certify) does **not** change. The seam parameterizes only *what populates each phase*, never the phase structure.

## Non-goals

- **Shipping Go or Java.** The seam must *admit* them (the `ClosureMode.COMPUTE` enum value; the deferred-mode note) but this migration ships only Python (wrap) + Rust + Node.
- **Feature-suppression / `cargo metadata` feature-conditioned native frontier.** The Rust lockfile-only path over-emits (the `libgit2-dev`-on-gitui false positive); falsified only by the build. Deferred (`research_prototype_gap.md` §6.7).
- **Runtime `ldd` native frontier for the cross-language tail.** Rust/Node native discovery is curated-table + build-signal only; the dynamic/`dlopen` tail (opencv/Qt/GDAL) stays out of scope. The `pytest`/build gate is the real oracle there.
- **ANY change to Python output.** Recall/precision improvements, taxonomy tweaks, closure changes — all out of scope. This is a pure structural migration; behavior is frozen.
- **Editing the hardened `build.py` internals.** No body inside `_phase_a_fixpoint`, `_stamp_audit`, `_restamp`, `reconcile_packages`, `certified_import_links`, `flag_*`, `resolved_record_coverage`, or the record-provider factories may be edited. The diff must read as "move + call," never "rewrite" (`research_zero_impact.md` §3).
- **The verify resolver, synthesizer/eval recipes, multi-provider composition of one repo.** All deferred to later slices (prototype spec §7, §13); this migration is the seam + two proof providers.

## Current state (what exists, what changes)

- `build_dep_graph` (`build.py:433-644`) runs the full pipeline inline. `_phase_a_fixpoint` (`build.py:333-430`) is the Phase-1 fixpoint. The shared tail is `reconcile_apt_names` (`apt_verify.py:133-171`) + `certify_all` (`certify.py:98`).
- `schema.Node` (`schema.py:140-172`) has **no `ecosystem` field**; `Node.to_dict()` (`schema.py:206-234`) emits every field **unconditionally**.
- The shared-engine surface — `schema.py` (immutable `DepGraph`/`Node`/`Edge`, enums, `EDGE_RULES`), `executor.py`, `certify.py`, `apt_verify.reconcile_apt_names`, `_restamp` + cycle constants — is already language-agnostic and stays untouched (`research_current_arch.md` §3).

What changes: a new neutral `src/ecosystems/` layer; one `Node.ecosystem` field (default `"python"`, conditionally serialized); a `PythonProvider` that DELEGATES to the existing `build.py` regions; a `select_provider` dispatch at the top of construction. Nothing inside the hardened internals.

## Design

### The two axes (encode faithfully from prototype spec §2)

The seam parameterizes exactly two axes; everything else is shared engine.

- **`closure_mode` — per-REPO, not per-language** (how the transitive closure is obtained):
  - `LOCK` — a committed lockfile is present → parse it offline/deterministically. Preferred.
  - `RESOLVE` — no committed lock → run the ecosystem resolver, then pin. **Python is RESOLVE** (loose manifest → `uv lock --exclude-newer` + PyPI era anchor). Node/Rust are typically LOCK, RESOLVE when the lock is missing.
  - `COMPUTE` — no lock and no cheap resolver (Java/Gradle, imperative build files). Enum value present; **deferred**.
- **`certify_mode` — per-PROVIDER, package/closure tier ONLY** (how the host establishes a package node's truth):
  - `INSTALL` — each Package node certified by one `check_command`. Python, Node.
  - `COMPILE` — one bulk `cargo build --message-format json` certifies the whole crate closure; per-node SATISFIED/MISSING is **attributed** from the JSON stream. Rust, Go.
  - **Resource tiers (SystemLib/Tool/Runtime) are ALWAYS presence-certified** in every ecosystem (`dpkg -s`, `command -v`, `pkg-config --exists`, `<rt> --version`). So a compile-mode repo mixes both mechanics. The scheduler routes by `(node.tier, provider.certify_mode)`.

For THIS migration, Python is `(closure_mode≈RESOLVE, certify_mode=INSTALL)` and its behavior is frozen; Rust exercises `COMPILE`, Node exercises the LOCK closure source.

### The `EcosystemProvider` interface (the method set THIS branch needs)

The prototype's Protocol (`base.py`) is narrower than the full spec §4 and uses `verify_candidates` for test commands. This migration needs only the **construction** subset. Keep the interface minimal — expand toward full spec §4 (`resolve_closure`, `project_install`, `feedback_parsers`, `bulk_certify`, verify commands) in later slices.

```python
# src/ecosystems/base.py   (new, neutral home — wrap in place, do NOT rename python_deps)
from typing import Protocol
from python_deps.depgraph.schema import DepGraph

class ClosureMode(enum.Enum):   # PORT-verbatim from prototype base.py
    LOCK = "lock"; RESOLVE = "resolve"; COMPUTE = "compute"

class CertifyMode(enum.Enum):   # PORT-verbatim; package tier only
    INSTALL = "install"; COMPILE = "compile"

class EcosystemProvider(Protocol):
    name: str                    # "python" | "rust" | "node"
    certify_mode: CertifyMode

    def detect(self, repo) -> float: ...
        # confidence 0..1 that this repo belongs to the ecosystem (dispatch gate)

    def closure_mode_for(self, repo) -> ClosureMode: ...
        # per-repo: LOCK if a committed lock is present, else RESOLVE (COMPUTE deferred)

    def package_obligations(
        self, repo, container_executor, *,
        host_executor=None, target_python=None, target_platform=None,
        exclude_newer=None, needed_extras=frozenset(),
    ) -> tuple[DepGraph, list, object, str | None]: ...
        # PHASE 1 body. Returns (graph, roots, target_env, exclude_newer);
        # ONLY `graph` flows onward (roots/target_env/exclude_newer are
        # provider-composition/test-visibility surface — see boundary §2c of research 1).

    def native_obligations(self, graph, container_executor) -> DepGraph: ...
        # PHASE 2 "look then derive": relink -> ldd -> dlopen backstop -> probe restamp
```

Notes that keep the constraint visible:
- **The record-provider grounding oracle stays constructed INSIDE `package_obligations`** at the same site (`build.py:569-571`), NOT hoisted to a provider method. This is load-bearing for hermeticity (INV-8): the conftest autouse stub patches the def-time `pypi_record_provider.__kwdefaults__['fetch']`; if the composite is built anywhere else the stub goes inert. See Zero-impact strategy.
- **The Runtime-tier obligation** (`build.py:542-560`) is inline today; it folds into the `package_obligations` body verbatim (it is Python-coupled: `_runtime_id`, `python3 -c`). A dedicated `runtime_decision` method is a later-slice refinement, not needed to wrap Python.
- `package_obligations` surfaces `build_dep_graph`'s existing keyword-only params unchanged (`host_executor`, `target_python`, `target_platform`, `exclude_newer`, `needed_extras`) so callers (`scripts/eval/graph_fidelity/coverage.py:550`, tests) keep working (INV signature-stability, `research_zero_impact.md` §3).

### The `src/ecosystems/` layer + `PythonProvider` delegation

Neutral seam ABOVE `python_deps`; `python_deps/` is NOT renamed (it is the Python provider's home). Layout:

```
src/ecosystems/
  base.py          # ClosureMode, CertifyMode, EcosystemProvider Protocol (+ RuntimeSpec later)
  registry.py      # select_provider(repo, providers, threshold=0.5) -> provider
  python/provider.py   # PythonProvider — DELEGATES into build.py regions
  rust/…           # Slice 2
  node/…           # Slice 3
```

`PythonProvider` is a **pass-through wrapper**, never a rewrite:

| Interface method | Delegates to (existing, UNCHANGED) |
|---|---|
| `detect` | `_project_build_manifest` (`build.py:140-169`) + `evidence.collect_python_dependency_evidence` — "does this repo declare a Python package?" |
| `closure_mode_for` | reports `RESOLVE` (loose manifest) / `LOCK` (committed `uv.lock`); today's path is RESOLVE-equivalent |
| `package_obligations` | a thin helper that runs `build.py:490-608` **verbatim** (Stage 1 scan → 1.5 target-env → 2 roots → 2a era-anchor ONCE → Runtime node → record-provider default → `_phase_a_fixpoint` → `_add_project_node` → `add_subprocess_tool_nodes` → `seed_wheel_oracle_prior` → resolver restamp), returning `(graph, roots, target_env, exclude_newer)` |
| `native_obligations` | a thin helper that runs `build.py:610-634` **verbatim** (`certified_import_links` → `ldd_probe` → `import_probe` → probe restamp) |

`certify_mode = INSTALL`. The **preferred** implementation is pure delegation: extract the two regions into module-level helpers in `build.py` (a mechanical cut so `git diff` reads as "move," not "edit"), then have `PythonProvider` call them. The new `build_dep_graph` becomes the dispatch shell:

```python
provider = select_provider(repo_path, PROVIDERS)                       # dispatch (NEW)
graph, roots, target_env, exclude_newer = provider.package_obligations(  # Phase 1
    repo_path, container_executor, host_executor=host_executor,
    target_python=target_python, target_platform=target_platform,
    exclude_newer=exclude_newer, needed_extras=needed_extras)
graph = provider.native_obligations(graph, container_executor)          # Phase 2 look/derive
graph = reconcile_apt_names(graph, container_executor)                  # SHARED tail (direct)
graph = certify_all(graph, container_executor, cycle=_CERTIFY_CYCLE)    # SHARED tail (direct)
return graph
```

This preserves the exact call order of `build.py:610-642`. `reconcile_apt_names` and `certify_all` are **NOT** provider methods — they are ecosystem-agnostic and the orchestrator keeps calling them directly, in the same order, so the Python path is byte-identical and Rust/Node reuse them unchanged.

### The PORT decision + the single `Node.ecosystem` schema addition

**Integration is a PORT, not an adapter** (`research_prototype_gap.md` §0, §3.5). The prototype's standalone `graph.py` is a lossy 11-field subset of the real 27-field `schema.Node` — no `layer`/`discovered_by`/`provenance`/`setup_commands`/`strength`/`phase`, which are exactly the fields the target's repair/relink/certify machinery keys off. An adapter would synthesize those fields as guesses → second-class nodes with wrong certify order and no repair provenance. So:

- **DROP** the prototype `graph.py`, both `__main__`, and `demo/` (scaffolding).
- **Retarget** the provider *logic* at the real `python_deps.depgraph.schema.Node`.
- **PORT-verbatim** (schema-independent gems): `ClosureMode`/`CertifyMode`/`RuntimeSpec`; both lockfile parsers (`node/lockfile.py::parse_package_lock`, `rust/lockfile.py::parse_cargo_lock`); `native_tables.py` (curated frontier data); and the crown jewel `certify/cargo_messages.py` (bulk `cargo build --message-format json` → per-crate attribution).
- **Add exactly ONE field** to the real `schema.Node`:

```python
ecosystem: str = "python"   # routing/composition axis; default keeps the Python path unchanged
```

placed in the existing default-safe enrichment block (`schema.py:158-165`, the `build_from_source`/`artifact`/… run that is documented "all default-safe so existing construction is unaffected").

**The byte-identity serialization rule (load-bearing).** `Node.to_dict()` (`schema.py:206-234`) today emits every field unconditionally. Emitting `ecosystem` unconditionally would add `"ecosystem": "python"` to **every** existing Python node's JSON → changes advisory/emit/eval output → **violates byte-identity** and fails oracle (c). Therefore emission MUST be conditional (omit-if-default):

```python
def to_dict(self) -> dict:
    out = { ... existing keys, unchanged order ... }
    if self.ecosystem != "python":       # Python nodes serialize byte-identically
        out["ecosystem"] = self.ecosystem
    return out
```

A dedicated byte-identity test asserts that for a Python-only graph, `to_dict()` output is unchanged from the pre-change baseline. Rust/Node nodes carry `ecosystem="rust"|"node"` and DO emit the key — which is correct and only affects new, non-Python graphs. (See Open decisions for why omit-if-default beats always-emit.)

Additive `EDGE_RULES` widening for resource→resource chains (prototype spec §5.5) is **not needed by this migration** (no Python graph sources an edge from a resource node; Rust/Node package_obligations here emit only goal→resource and package→resource edges already legal) — defer it to the multi-provider-composition slice.

### The exact Phase-1/Phase-2 extraction boundary

From `research_current_arch.md` §1-2, the boundary that keeps the Python path byte-identical:

- **`package_obligations` = `build.py:488-608`** (host-executor default; Stage 1 scan `490-492`; Stage 1.5 target-env `494-519`; Stage 2 roots `521-533`; Stage 2a era-anchor ONCE `535-540`; Runtime node `542-560`; record-provider default `561-571`; `pre_resolve_ids` snapshot `578`; `_phase_a_fixpoint` `579-588`; **plus the aux-once tail `590-608`** — see stage assignment below). Only `graph` flows onward; `roots`/`target_env`/`exclude_newer` are never read again after `build.py:588` (`research_current_arch.md` §2c), so returning them is safe provider surface.
- **`native_obligations` = `build.py:610-634`** (Stage 4a relink `610-618`; Stage 4.5 ldd `619-624`; dlopen backstop `625-628`; probe restamp `629-634`).
- **SHARED tail = `build.py:636-642`** (`reconcile_apt_names` `636-639`; `certify_all` `641-642`) — direct orchestrator calls, not provider methods.

The split point is chosen so `pre_resolve_ids` (`build.py:578`) and the resolver restamp that consumes it (`build.py:603-608`) BOTH live inside `package_obligations` — the snapshot never crosses the phase boundary, so no state threading is needed and INV-7 (stamp bookkeeping) is trivially preserved.

**Explicit assignment of stages 3a′/3a″/3b (resolving the boundary-ambiguous region `build.py:590-608`):** all three aux-once stages AND the resolver restamp stay together at the **tail of `package_obligations`**:
- **3a′ `_add_project_node` (`build.py:600`)** — package-tier (pyproject/setup.py hub) → `package_obligations` tail.
- **3a″ `add_subprocess_tool_nodes` (`build.py:601`)** — native/toolchain-tier → **kept in `package_obligations` tail** (NOT moved to `native_obligations` head).
- **3b `seed_wheel_oracle_prior` (`build.py:602`)** — native seed prior → **kept in `package_obligations` tail**.
- **resolver restamp `build.py:603-608`** (non-probe nodes → `_RESOLVER_CYCLE=2`) → `package_obligations` tail.

Rationale (this is a value-add over `research_current_arch.md`'s "either assignment"): although moving 3a″/3b to the head of `native_obligations` preserves closure *membership*, it would relocate those node additions to AFTER the resolver restamp, so their `discovered_cycle` would be stamped by the PROBE cycle (3) instead of the RESOLVER cycle (2) — a change visible in full-node `to_dict()` serialization and thus a byte-identity risk under INV-7. Keeping the entire `590-608` block together is the byte-identity-safe assignment and is the one this spec mandates.

### How Rust and Node slot in as new paths

Once the seam exists, Rust/Node are **new provider objects** registered in `PROVIDERS`; `select_provider` dispatches by `detect()`. They supply their own `package_obligations`/`native_obligations` against the SAME shared `schema`/`executor`/`certify`/`reconcile_apt_names` core, with `Node(...)` construction retargeted at the real schema (populating `layer`, `discovered_by`, `setup_commands`, `strength`, `phase`, `provenance` — the fields the prototype omitted):

- **Rust** (`certify_mode = COMPILE`): `package_obligations` parses `Cargo.lock` (LOCK) into `cargo:crate@ver` Package nodes + `-sys`/`links` native frontier from `native_tables.RUST_SYS_*`. `native_obligations` is minimal (no ldd). Its COMPILE certification is **not** per-node — it grafts into the shared `certify.py` via the ported `certify/cargo_messages.py` attribution keyed by `(tier, certify_mode)` (a `certify.py` extension, done in Slice 2; the Python `INSTALL` path stays byte-identical because tier-4+INSTALL routes exactly as today).
- **Node** (`certify_mode = INSTALL`): `package_obligations` parses `package-lock.json` v2/v3 (LOCK) into `npm:name@ver` Package nodes + `native_tables.NODE_*` frontier via `hasInstallScript`/`binding.gyp`. Per-package `check_command` = the cwd-absolute `require('<path>/package.json').version===` probe (carry the prototype's uncommitted check-path fix). `native_obligations` is minimal.

Because cross-ecosystem edges are ordinary `requires` edges and ids are ecosystem-prefixed (`cargo:`/`npm:`/`pip:`), the one-graph-appended-not-forked discipline holds without a schema change beyond `ecosystem`.

## Zero-impact strategy (the spine)

This is the governing constraint and it recurs in every section above. Two rules plus one gate.

### DELEGATE, don't rewrite (`research_zero_impact.md` §3)

- **Delegate over copy.** `PythonProvider.package_obligations`/`native_obligations` call helpers that run the existing `build.py` regions **verbatim**. Prefer a mechanical cut (extract `build.py:490-608` and `610-634` into module-level helpers, call them) so "no behavior change" is trivially true and `git diff` reads as "move + call."
- **No function bodies edited.** If `git diff` shows any edit inside `_phase_a_fixpoint`, `_stamp_audit`, `_restamp`, `reconcile_packages`, `certified_import_links`, `flag_*`, `resolved_record_coverage`, or the record-provider factories → it is a rewrite, stop.
- **Same functions, same order, same args** — the stage sequence stays exactly `build.py:490-642`.
- **Preserve module-level symbol identity** patched by tests/conftest: `build.pypi_record_provider`, `build.composite_record_provider`, `coverage._default_wheel_top_levels`, `coverage.pypi_record_provider` (its `__kwdefaults__['fetch']`), `relink.PACKAGES_DIST_CMD`. If `PythonProvider` re-imports these, the autouse `_no_pypi_network` stub must still target the SAME objects — keep the composite record-provider default constructed at `build.py:569-571` (do not hoist it into a provider method).

### The hardened-invariant checklist (must all still hold; `research_zero_impact.md` §1)

The wrap must not disturb any of these. They are grouped by how the seam could break them:

| INV | Invariant | Where the seam must not break it |
|---|---|---|
| INV-1 | Era-anchor `exclude_newer` computed exactly ONCE | keep `compute_exclude_newer` at `build.py:539-540`; never re-derive inside the provider |
| INV-2 | AUDIT provenance never restamped to RESOLVER | `_stamp_audit` stays per-round inside `_phase_a_fixpoint`; don't fold into the cycle restamp |
| INV-3 | TWO separate `packages_distributions()` reads | don't unify the composite installed-leg (memoized round-0) and the single Phase-B relink read |
| INV-4 | Fixpoint 4-way termination | no wrapper early-return; don't touch the missing-set/attempted-set/bound/ACCEPT-guard |
| INV-5 | `unresolved / unresolved_runtime / SystemLib` mutual-exclusion | native_obligations wraps `flag_*` unchanged; don't reimplement `_provided_imports` |
| INV-6 | Immutable graph rebinding | provider holds no graph state; never mutate `node.data`; return fresh graphs |
| INV-7 | Restamp / stamp bookkeeping order | keep `pre_resolve_ids` + all `590-608` aux stages + resolver restamp inside `package_obligations` (stage assignment above) |
| INV-8 | Hermeticity: no live PyPI in non-injecting tests | preserve record-provider symbol identity + construction site (rule above) |
| INV-9 | Phase-A-before-Phase-B; relink FIRST in Phase B | the dispatch shell preserves the exact `610-642` order |
| INV-10 | Coverage oracle is RECORD-union | inside `_phase_a_fixpoint`, untouched |
| INV-11 | `needed_extras` threaded UNCHANGED into both sinks | surface `needed_extras` through `package_obligations` unchanged; don't recompute |
| INV-12 | `target_env` passed as an OBJECT | never rebuild from `(target_python, target_platform)` strings |
| INV-13 | `reconcile_packages` drops stale nodes/edges each round | inside `_phase_a_fixpoint`, untouched |
| INV-14 | `_project_build_manifest` installable gate | `_add_project_node` stays in `package_obligations` tail, unedited |
| INV-15 | No LLM in the deterministic core | `generate_candidates(..., llm=None)` — the wrapper adds no LLM |

Carry the pre-existing `roots.py:206-212` HOST-stdlib target-honesty bug across **unchanged** — the seam neither fixes nor worsens it (`research_current_arch.md` §4 row 6).

### The three-oracle zero-impact gate (slice-1 acceptance; `research_zero_impact.md` §2)

Freeze artifacts at the pre-extraction commit, extract, re-run, diff. ALL THREE must be identical:

- **(a) Construction suites — identical pass/skip partition.** `pytest tests/depgraph tests/pkg_layer tests/eval -q` — **825 + 118 + 168 = 1111** collected. The construction-relevant suites must be fully green identically; the 7 known pre-existing off-path failures may stay red but must not change in count or identity. Runs hermetic (INV-8 → no network). Targeted invariant guards: `test_build_phase_order.py`, `test_phase_a_fixpoint.py`, `test_relink.py`, `test_record_provider.py`, `test_schema_audit.py`, `test_pins.py`, `test_roots.py`, `test_build.py`.
- **(b) A/B verdict stays `verifier` 30/0/30/0, JSON byte-clean.** Run `scripts/eval/graph_fidelity/root_selection_ab.py` and `pkg_layer_ab.py` (`--clones-root <clones>`); emitted `*.json` (`aggregate` + `scorecards`) must diff-clean vs committed `outputs/graph_fidelity/{root_selection_ab,pkg_layer_ab}.json`, `aggregate.verdict == "verifier"` with `bad >= good`. Both arms flow through `select_roots`/`scan_to_nodes` (the wrapped code).
- **(c) Package-layer closures byte-identical per repo.** `python3 /Users/john/.claude/jobs/366037cb/tmp/run_ours_pkg.py <15 repos> /tmp/ours_after`, then `diff -r outputs/graph_fidelity/pkg_lock_ab/ours_v2 /tmp/ours_after` must be EMPTY over the 15 `*.json` closures, and `compare_pkg.py` must reprint the same pooled recall/precision. This one diff simultaneously catches regressions in INV-2 (AUDIT), INV-5 (taxonomy), INV-10/INV-13 (membership/versions). Docker+network heavyweight; (a)+(b) are the fast hermetic gate. `ours_v2` is the live baseline — freeze it immediately before extraction.

The proof of correctness IS this gate staying green with artifacts byte-identical — the same "no-loss, nothing to stage" standard the two-phase SDD P3.1 A/B regeneration met.

## Scope

- **Slice 1 (this migration's core) — seam + Python wrap, oracle-gated:**
  1. `Node.ecosystem` field + conditional `to_dict` + byte-identity test.
  2. `src/ecosystems/base.py` (PORT `ClosureMode`/`CertifyMode`/Protocol) + `registry.select_provider`.
  3. Extract `build.py:490-608` and `610-634` into verbatim helpers; `PythonProvider` delegates.
  4. `build_dep_graph` becomes the dispatch shell (`select_provider` → provider methods → shared tail).
  5. Prove the three-oracle gate green, artifacts byte-identical. **This is the acceptance bar.**
- **Slice 2 — Rust (COMPILE):** PORT `parse_cargo_lock`, `native_tables.RUST_*`, `cargo_messages.py`; `RustProvider.package_obligations`; graft `(tier, certify_mode)` COMPILE attribution into `certify.py` (Python INSTALL path stays byte-identical). Gate: Python oracle (a)–(c) still green + a Rust compile-certify e2e on a small repo (gitui-class).
- **Slice 3 — Node (INSTALL/LOCK):** PORT `parse_package_lock`, `native_tables.NODE_*`; `NodeProvider.package_obligations` with the cwd-absolute check-path fix. Gate: Python oracles still green + a Node install-certify e2e (axios-class).
- **Deferred (admitted, not shipped):** Go/Java; `cargo metadata` feature-suppression; the repo-level verify resolver (prototype §7); multi-provider composition + cross-ecosystem `requires` edges + `EDGE_RULES` §5.5 widening; synthesizer/eval recipe routing (prototype §13); runtime `ldd` cross-lang frontier.

## Risks & mitigations

- **`Node.ecosystem` byte-identity.** Always-emit adds a key to every Python node → fails oracle (c). *Mitigation:* conditional omit-if-default `to_dict` (design above) + a dedicated byte-identity test; this is the FIRST task and its test is RED-first.
- **Hermeticity conftest re-import risk (INV-8).** If `PythonProvider` re-imports `pypi_record_provider`/`composite_record_provider`/`_default_wheel_top_levels`, the autouse `_no_pypi_network` stub (which patches def-time `__kwdefaults__`) goes inert → tests silently hit live PyPI or trip the `urlopen` assertion. *Mitigation:* keep the composite record-provider constructed at `build.py:569-571` inside the moved region; do not hoist it; verify suite (a) stays hermetic (no `urlopen` AssertionError, no network) as an explicit check.
- **Uncommitted-prototype risk.** The prototype's entire `certify/` subsystem (incl. `cargo_messages.py`), the three provider bug-fixes (Node check-path, Rust `_image_tag`, lockfile check-path), and the demo are **untracked** in `john-planner-v3-ecosystems` (`research_prototype_gap.md` §1). *Mitigation:* before Slice 2/3, **commit the ecosystems worktree's `certify/` + provider fixes first** so the PORT source is captured and reviewable; do not port from an uncommitted tree.
- **Port-forward / port-drift risk.** Two schemas drifting forever if an adapter is kept. *Mitigation:* PORT (one schema); keep an adapter only as a throwaway migration scaffold, if at all (`research_prototype_gap.md` §3.5).
- **Boundary-ambiguous cycle-stamp drift (INV-7).** Splitting `590-608` across the phase boundary shifts `discovered_cycle`. *Mitigation:* the mandated stage assignment keeps all of `590-608` in `package_obligations`.
- **Rust COMPILE grafts into shared `certify.py` (Slice 2).** Editing `certify.py` risks the Python certify order. *Mitigation:* route by `(tier, certify_mode)`; tier-4+INSTALL and all resource tiers keep today's exact path; pin Python certify order with a regression test; re-run oracle (a)–(c).

## Open decisions (resolved with recommendations)

1. **Seam home: `src/ecosystems/` vs `src/python_deps/providers/`.** → **`src/ecosystems/`** (RESOLVED). It is neutral and ABOVE `python_deps`; Python becomes one provider among peers. Putting the seam under `python_deps` would re-assert Python as the root namespace and undercut "wrap in place, don't rename, Python is a peer."
2. **`Node.ecosystem` serialization: omit-if-default vs always-emit.** → **omit-if-default** (RESOLVED, byte-identity-safe). Always-emit injects `"ecosystem":"python"` into every existing node and fails oracle (c). Conditional emission makes the Python path byte-identical while Rust/Node nodes correctly carry the key.
3. **First-slice scope: seam+Python only, or seam+Python+one provider.** → **seam + Python first** (RESOLVED). Slice 1's acceptance is the three-oracle gate; landing a real provider in the same slice mixes "prove zero-impact" with "add a feature" and makes a failed gate ambiguous. Rust/Node land in Slices 2/3 behind the same gate.
4. **Stage `590-608` assignment (was boundary-ambiguous in research 1).** → **entire `590-608` block stays in `package_obligations`** (RESOLVED for byte-identity; see extraction boundary + INV-7 rationale). Not "either assignment."

## Suggested task breakdown (SDD)

**Slice 1 — seam + Python wrap (each task independently testable; the three-oracle gate is the slice gate):**

1. **Freeze baselines.** Capture oracle (a) `-q` summary + `--tb=no -rA` list; oracle (b) committed A/B JSONs; oracle (c) `ours_v2` closures. These are the byte-identity references. (No product change.)
2. **`Node.ecosystem` + conditional `to_dict` (TDD, RED first).** Add the field in the default-safe block (`schema.py:158-165`); make `to_dict` omit-if-default. RED test: a Python-only graph's `to_dict()` is byte-identical to the frozen baseline; a `ecosystem="rust"` node emits the key. Re-run oracle (a).
3. **`src/ecosystems/base.py` + `registry.select_provider` (TDD).** PORT `ClosureMode`/`CertifyMode`; define the minimal `EcosystemProvider` Protocol (method set above); `select_provider` = highest `detect()` above threshold. Unit tests on selection only.
4. **Extract Phase-1 helper (move, not edit).** Cut `build.py:490-608` into a module-level helper returning `(graph, roots, target_env, exclude_newer)`; `build_dep_graph` calls it. `git diff` must be pure move. Re-run oracle (a) + targeted invariant guards.
5. **Extract Phase-2 helper (move, not edit).** Cut `build.py:610-634` into a helper `(graph, container_executor) -> graph`; call it. Keep `reconcile_apt_names` + `certify_all` as direct orchestrator tail. Re-run oracle (a).
6. **`PythonProvider` delegating to the two helpers (TDD).** Wire `detect`/`closure_mode_for`/`package_obligations`/`native_obligations` to the helpers. Assert the composite record-provider symbol identity + hermeticity (INV-8) explicitly.
7. **Dispatch shell.** `build_dep_graph` = `select_provider` → `package_obligations` → `native_obligations` → shared tail. Only `graph` flows onward. Re-run oracle (a).
8. **Slice-1 gate.** Prove oracle (a) 1111 partition unchanged, (b) `verifier` 30/0/30/0 JSON byte-clean, (c) `diff -r` empty + same pooled recall/precision. **Land only when all three are byte-identical.**

**Slice 2 — Rust (COMPILE), coarser outline:** commit the prototype `certify/` + fixes first (risk mitigation) → PORT `parse_cargo_lock` + `native_tables.RUST_*` + `cargo_messages.py` → `RustProvider.package_obligations` (retarget `Node(...)` at real schema, populate `layer`/`discovered_by`/`setup_commands`/`strength`/`phase`) → graft `(tier, certify_mode)` COMPILE attribution into `certify.py` (Python path pinned byte-identical) → gate: Python oracle (a)–(c) green + Rust compile-certify e2e (small repo).

**Slice 3 — Node (INSTALL/LOCK), coarser outline:** PORT `parse_package_lock` + `native_tables.NODE_*` → `NodeProvider.package_obligations` (retarget at real schema; carry the cwd-absolute check-path fix; `hasInstallScript`→gyp toolchain frontier) → gate: Python oracle (a)–(c) green + Node install-certify e2e (axios-class), verifying the `optionalDependencies`→UNKNOWN and multi-version-id nuances.
