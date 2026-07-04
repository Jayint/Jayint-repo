# Implementation plan: two-phase declared-roots construction of the Python dep layer

Status: implementation plan (SDD)
Branch: `john-planner-v3-core-autoresearch`
Design (authoritative): [`2026-07-04-declared-roots-two-phase-construction.md`](./2026-07-04-declared-roots-two-phase-construction.md)
Supersedes at runtime: the `select_roots` scan-gap-fill generator (declared ∪ mapped-imports)

This plan turns the two-phase design into ordered, reviewable tasks. Each task is
1–2 files, TDD-first (tests written and RED before implementation), and returns a
NEW `DepGraph` (never mutates). The load-bearing change is P0: **imports stop
generating roots.** P1 adds the Phase-A repair fixpoint on top. P2 reorders into the
clean Phase-A → Phase-B sequence. P3 migrates evals/tests and rolls out.

> **Corrections folded from the risk review** (`removal-map/04-risk-review.md`) — see
> the "Corrections" section below. In brief: (1) P0 does NOT ship alone — deleting the
> gap-fill without the *deterministic* repair regresses under-declared-alias repos, so
> the atomic **merge unit is P0 + P1(deterministic) + P2.1**; (2) the fixpoint needs
> declared-drop-priority + attempted-set termination + per-round node reconcile;
> (3) the Phase-A missing-set oracle is the resolved wheels' **RECORD union**, not
> `packages_distributions()`; (4) a third failure class (import raises for a
> non-native reason) needs an honest flag (new task P2.3).

---

## Corrections (folded from `removal-map/04-risk-review.md`, verified against real code)

Four corrections amend the tasks below. Each is applied in-place in the named task; this
list is the index.

1. **P0 is NOT independently shippable.** `certified_import_links` only adds edges to
   *already-resolved* packages, so for an **under-declared** alias (imports `cv2`, never
   declares `opencv-python`) relink has nothing to link — only the repair overlay recovers
   it. Deleting the gap-fill without the deterministic repair silently strips the
   curated-alias rescue Track B measured (5/5). **The atomic merge/release unit is
   P0 + P1 (deterministic rungs) + P2.1.** The 12-task list is *build* order; the *release
   gate* is that whole deterministic block. Only the LLM rung and P3 (evals/tests/rollout)
   split off. (Applied: P0 header, sequencing note.)
2. **The fixpoint needs three safeguards** (Applied: P1.4):
   - **2a Declared-drop-priority.** `resolve_errors._offending_root_names` drops
     `sorted(root_imposers)[0]` with no declared-vs-AUDIT priority — a repaired root can
     evict a *manifest* dep, whose imports then re-audit missing → re-add → oscillate. A
     declared root must NEVER be dropped to satisfy an AUDIT root.
   - **2b Attempted-set termination.** Stop when a round proposes only `(import, candidate)`
     pairs already tried (a real fixpoint), not merely when "no root was added." Numeric
     backstop `min(len(missing_initial), 5)`. On non-convergence: flag residue, proceed —
     never abort. Log the oscillation signature.
   - **2c Per-round node reconcile.** Package ids bake version (`pkg:name==version`) and
     `DepGraph` is upsert-only, so a version shift between rounds orphans stale nodes/edges.
     Each round must `without_node`/`without_edge` whatever the new resolve no longer
     produces, before merging.
3. **Right oracle for the missing-set.** Phase-A "is it provided?" is audited against the
   **resolved wheels' RECORD union**, NOT `packages_distributions()`. The latter reports
   only what actually *installed*, so a resolved-but-failed-to-build package looks "missing"
   and gets misrouted to repair (adding a spurious alternative) when it is really a Phase-B
   build/system-lib gap. Two oracles: RECORD-union = "is it provided" (Phase A);
   live import / `ldd` = "does it load" (Phase B). (Applied: P1.4, P2.1.)
4. **Third failure class.** Metadata-present-but-import-raises-for-a-non-native-reason is
   caught by neither phase today (`probe.import_probe` only creates a node on a
   `NATIVE_LIBRARY_RE` match). It must be flagged honestly, not silently passed.
   (Applied: new task P2.3.)

---

## Global Constraints (invariants — copied from the design, hold in EVERY task)

These are non-negotiable and must be preserved by every task below. A task that
cannot hold all of them is mis-scoped.

- **Roots = declared (in-scope). Imports never generate roots.** Root selection is
  manifest-declared only; the scan gap-fill is deleted.
- **Imports = audit, not generator.** Imports are authoritative for *demand/audit*
  (catching under-declaration), never for *generating* install roots (the pipreqs
  mistake). The installed environment (`packages_distributions()`) is the naming
  truth (`bs4→beautifulsoup4`, `cv2→opencv-python`, transitive/name-variant all
  resolve by observation).
- **Era-anchored resolve, computed once.** `compute_exclude_newer` is computed a
  single time, before the Phase-A loop, and reused every round.
- **Runtime-scoped audit, exempting try/except-optional.** Flag only runtime,
  non-optional imports. Test-goal nodes are already separated by the scan; optional
  (`try/except ImportError`) imports are exempt by design.
- **Never guess a variant → flag ambiguous.** >1 verified provider ⇒ flag AMBIGUOUS;
  the construction never picks. The surrounding Contract/Closure breaks most ties
  (prefer the variant already in the lock / named by a declared extra).
- **Certify or flag; never fabricate a root.** Construction only certifies (from the
  container) or flags (`unresolved`). A repaired package enters as an under-declared
  root with repair provenance (`discovered_by = AUDIT`), never conflated with a
  manifest declaration.
- **Immutability = return a NEW `DepGraph`.** Every stage/overlay returns a new
  immutable graph; the orchestrator only rebinds `graph`.
- **LLM gated OUT of the deterministic core.** Detection (`packages_distributions`
  coverage) and repair's first rungs (`normalize → curated table → RECORD-ground`)
  are deterministic. The LLM is an injected, gated last rung only (default absent),
  so Phase A's core stays LLM-free and reproducible.
- **Interpretable — ONE clean path.** Prefer one clean path over migration-safe
  fallbacks/flag-gates (v3-core = paper reference impl). No dual-mode toggles.
- **Commit-local, never push.** All work is committed on the branch locally; nothing
  is pushed.

---

## Task list

### P0 — core (the measured win; NOT independently shippable — merges with P1-deterministic + P2.1, see Correction 1)

- **P0.1** — Delete the scan gap-fill: `select_roots` declared-only
- **P0.2** — Port OPTIONAL (try/except-ImportError) context into the scan
- **P0.3** — Runtime-scope + optional-exempt `flag_unresolved_imports`

### P1 — Phase-A fixpoint (repair overlay + bounded re-resolve loop)

- **P1.1** — Add `DiscoveredBy.AUDIT` provenance
- **P1.2** — Repair candidate ladder: generation + 3-way decide (new `repair.py`, pure)
- **P1.3** — RECORD grounding + provider selection (repair.py; injected record provider)
- **P1.4** — Phase-A fixpoint driver: bounded resolve → install → look → repair loop (+ declared-drop-priority, attempted-set stop, per-round node reconcile, RECORD-union oracle — Corrections 2–3)
- **P1.5** — Pre-install PyPI wheel-metadata record provider — make production repair FUNCTIONAL (added during execution; closes P1.4's brief-sanctioned interim gap where the post-install provider can't ground a not-yet-installed candidate → production repair inert. Part of the atomic merge unit, before P2.1.)

### P2 — Phase-B reorder (clean two-phase sequence)

- **P2.1** — Reorder to Phase-A-before-Phase-B: RECORD-union drives the loop condition; relink certifies; ldd/probe/apt/certify as one post-convergence block
- **P2.2** — Retire vestigial machinery: `_drop_superseded_ghosts` + provisional Stage 3a link
- **P2.3** — Honest flag for non-native import failures (Correction 4)

### P3 — migration (evals, tests, rollout)

- **P3.1** — Re-point the A/B generator arm; regenerate `root_selection_ab`/`pkg_layer_ab`; confirm 30/0/30/0 in-tree
- **P3.2** — Rewrite/delete tests that asserted generator behavior
- **P3.3** — Rollout: default-on (no flag-gate), full-suite gate, commit-local

---

## Dependency / sequencing note

```
  ┌──────────────── ATOMIC MERGE UNIT (Correction 1) ────────────────┐
  │  P0.1─P0.2─P0.3   ─►   P1.1─P1.2─P1.3─P1.4   ─►   P2.1  (+ P2.3)  │
  │  delete gap-fill       deterministic repair       reorder         │
  │  + honest audit        (LLM rung stays off)        (ldd after conv)│
  └───────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                            P2.2   (cleanup; needs P2.1)
                                  │
                                  ▼
                    P3.1 ─ P3.2 ─ P3.3   (evals / tests / rollout)
```

- **P0 is built first but does NOT ship alone (Correction 1).** P0.1 (declared-only),
  P0.2 (optional context), P0.3 (honest audit) are the load-bearing deletion — but on
  their own they regress under-declared-alias repos (the gap-fill's one legitimate job).
  P0.2 precedes P0.3 (the flag exemption reads the optional tag P0.2 writes).
- **P1 (deterministic) is part of the same merge unit.** The fixpoint re-homes the alias
  rescue P0 removed. P1.1 → P1.2 → P1.3 → P1.4 is strictly ordered (enum, pure ladder,
  grounding, loop). The LLM rung inside P1.2 stays injected-off (not part of the core).
- **P2.1 completes the merge unit.** The fixpoint (P1.4) is only correct once `ldd`/certify
  run after convergence, so the reorder ships together with P0+P1; **P2.3** (Correction 4)
  rides along. **P2.2** is cleanup that can follow.
- **The atomic merge gate = P0 + P1(deterministic) + P2.1 (+ P2.3).** Do NOT merge P0 to
  mainline behavior without it — alone it silently regresses (Correction 1).
- **P3 needs the merge unit.** The eval re-anchor and test rewrites assert final behavior.

Each task: commit locally with a conventional-commit message; run the full
`tests/depgraph` + `tests/eval/graph_fidelity` + `tests/pkg_layer` suite green
before the next task. Never push.

---

# P0 — core

## P0.1 — Delete the scan gap-fill: `select_roots` declared-only

**Files touched**
- `src/python_deps/depgraph/roots.py` (edit)
- `tests/depgraph/test_roots.py` (rewrite gap-fill assertions)
- `tests/depgraph/test_build.py` (fixture: declare the deps the gap-fill used to fabricate)

**Exact change**
- In `select_roots`, **delete the entire "2. Scan gap-fill" block** (the
  `for import_node_id, dist_name in package_roots(graph, declared_names): …` loop).
  Roots become manifest-declared only (all carry `import_id=None`).
- Remove the now-dead `from python_deps.depgraph.naming import package_roots` import
  and the `declared_names = {…}` local (only the gap-fill used them).
- Keep the `graph` parameter in the signature (accepted, unused) with a docstring
  note — "reserved; imports are audited post-install, not consulted for roots" — to
  avoid churning `build.py` and every caller/test in this task. (Signature slimming
  to `select_roots(repo_path, *, needed_extras, target_env)` is a deferred follow-up.)
- Update the module + function docstrings: drop "scan-gap-filled" from the ladder;
  state "manifest-declared roots only; imports never generate roots (see design)".
- `naming.package_roots` is now **off the construction path** (no construction
  caller). Leave the module in place — it is retained only as the A/B eval's
  "generator" reference (P3.1). Add a one-line docstring banner to `naming.py`
  saying so.

**TDD — write these FIRST (RED), then implement**

In `tests/depgraph/test_roots.py`:
- Rewrite `test_scanned_curated_import_gap_fills_only_uncovered`: with `yaml`
  imported but NOT declared, assert **PyYAML is NOT a root** (declared-only; `yaml`
  is now an audit signal, repaired in Phase A later, never a fabricated root):
  ```python
  assert "PyYAML" not in {dist for _imp, dist in roots}
  assert all(imp is None for imp, _dist in roots)   # every root is declared
  ```
- Keep `test_scanned_import_gap_fills_only_uncovered` (boto3 not fabricated) — now
  it holds for the general reason, not the "unmapped" special case.
- Keep unchanged and green: `test_declared_dependencies_become_roots_with_none_import_id`,
  `test_declared_import_not_duplicated`, `test_manifest_scan_dedup_via_normalization`,
  all py2-shim / stdlib / typing / junk filters, all Task-8 extras tests, all Task-8
  marker tests (they exercise the manifest path only).

In `tests/depgraph/test_build.py`:
- The fixture repo (`_make_repo`) writes only `app.py` importing `cv2`, `PIL`,
  `psycopg2` — today those become roots via gap-fill. Declared-only breaks this
  (empty roots → empty closure). **Add a `pyproject.toml` to the fixture declaring
  the deps** so roots come from the manifest (the "well-declared repo" P0 supports):
  ```python
  (tmp_path / "pyproject.toml").write_text(
      '[project]\nname="fx"\nversion="0"\n'
      'dependencies=["opencv-python","Pillow","psycopg2"]\n'
  )
  ```
  Assert the existing `test_build_produces_all_node_types` still finds the
  `opencv-python`/`Pillow`/`psycopg2` Package nodes and the libGL/pg_config gaps.

**Acceptance criteria**
- `select_roots` returns only `(None, dist)` pairs; an excluded optional extra whose
  module is imported is NOT re-injected (the `needed_extras` regression is gone).
- No construction code imports `naming.package_roots`.
- `select_roots` still returns a NEW list (pure, no graph mutation); `roots.py` has
  no reference to `package_roots`/`declared_names`.
- Full `tests/depgraph` suite green with the updated fixture.

---

## P0.2 — Port OPTIONAL (try/except-ImportError) context into the scan

**Files touched**
- `src/python_deps/import_graph.py` (edit: detect optional imports in AST)
- `src/python_deps/models.py` (edit: `ImportFinding.optional` field)
- `src/python_deps/depgraph/scan.py` (edit: propagate to `Node.data["optional"]`)
- `tests/depgraph/test_scan_optional.py` (new)

**Exact change**
- `models.ImportFinding`: add `optional: bool = False` (keep frozen; default keeps
  every existing construction).
- `import_graph._imports_from_ast`: an import statement is **optional** when it is
  lexically inside a `try` whose `except` handler catches `ImportError`/
  `ModuleNotFoundError` (or a bare `except`). Implement by walking `ast.Try` nodes:
  collect the top-level names imported in `node.body` when any handler's `type`
  resolves to `ImportError`/`ModuleNotFoundError`/`Exception`/bare. Return the set of
  optional top-level names alongside the full import set (change the helper to return
  `(all_names, optional_names)` or a small dataclass; update the one caller).
  - Dedupe rule: a name imported optionally in ≥1 place AND non-optionally elsewhere
    is **non-optional** (a hard runtime need dominates a guarded one). Fold this in
    `_dedupe_findings` (a name is optional only if optional in ALL findings for it).
- `scan.scan_to_nodes` / `_build_import_node`: set `data={"optional": True}` on the
  Import node when the finding is optional (leave `data` empty otherwise, to keep
  the "never needlessly rewrite" property elsewhere).

**TDD — write these FIRST (RED)**

`tests/depgraph/test_scan_optional.py`:
- Optional import tagged:
  ```python
  # try: import ujson\n except ImportError: ujson = None
  g = scan_to_nodes(repo)
  assert g.get(import_id("ujson")).data.get("optional") is True
  ```
- Hard import not tagged: `import requests` at module top → `data.get("optional")` is
  falsy.
- Mixed dominance: `requests` imported hard in `a.py` and guarded in `b.py` →
  NOT optional (hard wins).
- `try/except ValueError` around `import foo` → NOT optional (only ImportError-family
  and bare/`Exception` handlers count).
- Regex-fallback path (syntax error file) → imports default `optional=False` (no AST,
  no false optional tag).

**Acceptance criteria**
- `ImportFinding.optional` populated correctly for the four AST shapes above.
- Import nodes carry `data["optional"]=True` only for genuinely-guarded imports.
- No behavior change for any non-optional import; existing scan tests stay green.

---

## P0.3 — Runtime-scope + optional-exempt `flag_unresolved_imports`

**Files touched**
- `src/python_deps/depgraph/relink.py` (edit `flag_unresolved_imports`)
- `tests/depgraph/test_relink.py` (extend)

**Exact change**
- `flag_unresolved_imports`: an Import node is flagged `unresolved` only when it is
  BOTH unprovided (no outgoing REQUIRES→Package edge) AND **non-optional**
  (`node.data.get("optional") is not True`). An optional import that is unprovided is
  **left unflagged** (exempt by design — a guarded `try/except ImportError` is not an
  under-declaration).
  - Idempotency preserved: an optional-or-provided import that carries a STALE
    `unresolved` flag from a prior pass has the flag + evidence cleared (extend the
    existing "now provided" clearing branch to also cover "now known optional").
- Runtime scope: the function already ranges over `NodeType.IMPORT` nodes only (the
  Test goal is `NodeType.TEST`, never flagged), so runtime-scoping is satisfied by the
  optional exemption + the existing node-type filter. Add a docstring line making this
  explicit ("Test goal is separated by the scan; optional imports are exempt").

**TDD — write these FIRST (RED)**

Extend `tests/depgraph/test_relink.py`:
- `test_optional_unprovided_import_not_flagged`:
  ```python
  opt = Node(id=import_id("ujson"), type=IMPORT, name="ujson",
             layer=NAMING, discovered_by=STATIC_SCAN, data={"optional": True})
  out = flag_unresolved_imports(DepGraph(nodes=(opt,)))
  assert out.get(opt.id).data.get("unresolved") is not True
  ```
- `test_hard_unprovided_import_still_flagged`: same but `data={}` → flagged
  `unresolved` with evidence mentioning the name (unchanged existing behavior).
- `test_transitively_satisfied_import_not_flagged`: `urllib3` with a REQUIRES edge to
  a `requests` Package (mimicking transitive coverage) → NOT flagged.
- `test_name_variant_import_not_flagged`: `cv2` with a certified edge to
  `opencv-python` Package → NOT flagged.
- `test_stale_flag_cleared_when_now_optional`: an import carrying a stale
  `unresolved:True` but with `optional:True` → flag + evidence cleared, other data
  keys preserved.
- Keep all existing flag tests green (`test_unlinked_import_is_flagged_unresolved`,
  idempotence, stale-clear-when-provided, never-rewrite).

**Acceptance criteria**
- Genuinely-missing runtime import → `unresolved` fires. Transitively-satisfied,
  name-variant, and optional imports do NOT fire.
- `flag_unresolved_imports` remains idempotent and returns a NEW graph.
- The design test-plan audit bullets all pass at the unit level.

---

# P1 — Phase-A fixpoint

## P1.1 — Add `DiscoveredBy.AUDIT` provenance

**Files touched**
- `src/python_deps/depgraph/schema.py` (edit enum)
- `tests/depgraph/test_schema_audit.py` (new, tiny)

**Exact change**
- Add `AUDIT = "audit"` to `DiscoveredBy` (alongside `GOAL/STATIC_SCAN/RESOLVER/
  PROBE/RUNTIME`). This is the provenance for a package added by the Phase-A repair
  overlay — an under-declared root discovered by auditing imports against the
  installed environment, never conflated with a manifest declaration (`RESOLVER`) or
  a static-scan import (`STATIC_SCAN`).

**TDD — write FIRST (RED)**
- `DiscoveredBy.AUDIT.value == "audit"`; it is distinct from every other member; a
  `Node(..., discovered_by=DiscoveredBy.AUDIT)` round-trips through `to_dict()`.

**Acceptance criteria**
- Enum member exists and serializes; no existing enum consumer breaks (exhaustive
  `match`/dict lookups over `DiscoveredBy`, if any, updated — grep confirms none
  require a new branch for construction).

---

## P1.2 — Repair candidate ladder: generation + 3-way decide (new `repair.py`, pure)

**Files touched**
- `src/python_deps/depgraph/repair.py` (new — pure, no Executor/network)
- `tests/depgraph/test_repair_ladder.py` (new)

**Exact change** (port the validated spike `underdeclaration_repair_poc.py`, graph-free)
- `@dataclass(frozen=True) Candidate(dist: str, source: str)` where `source ∈
  {"normalize","curated","llm"}`.
- `normalize_candidates(import_name) -> list[str]`: `top`, `top.lower()`, dashed,
  `python-<dashed>`, `<dashed>-python`, canon-deduped (verbatim from the spike).
- `curated_candidates(import_name) -> list[str]`: look up
  `import_mapping.CURATED_IMPORT_TO_PACKAGE` by canon top-level (this is the
  **demoted** curated table — now an untrusted candidate source, not a root
  authority).
- `generate_candidates(import_name, *, llm=None) -> list[Candidate]`: concatenate
  `normalize` then `curated` then (only if `llm` callable is provided)
  `llm(import_name)` mapped to `source="llm"`; canon-dedupe, first (cheapest) source
  wins. **`llm` defaults to `None`** so the deterministic core never calls a model.
- `decide(grounded_dists: list[str]) -> tuple[Verdict, str]`: exactly-one → `ACCEPT`;
  >1 → `AMBIGUOUS`; none → `UNRESOLVED` (verbatim 3-way from the spike). Use a
  `Verdict` `enum`/`Literal` (`ACCEPT/AMBIGUOUS/UNRESOLVED`).

**TDD — write FIRST (RED)** (`test_repair_ladder.py`, no network)
- `normalize_candidates("dateutil")` contains `python-dateutil`; canon-deduped.
- `curated_candidates("yaml")` → `["PyYAML"]`; `curated_candidates("requests")` → `[]`.
- `generate_candidates("yaml")` order: normalize rungs before the curated `PyYAML`;
  with `llm=lambda n: ["extra"]`, an `llm`-sourced `extra` appears last and only then.
- `generate_candidates("yaml", llm=None)` never yields an `llm` source.
- `decide` self-check: `[]→UNRESOLVED`, `["a"]→ACCEPT`, `["a","b"]→AMBIGUOUS`.

**Acceptance criteria**
- Pure module: imports nothing that touches an Executor/network; unit tests run
  container-free and fast.
- LLM rung is opt-in via an injected callable; absent by default.
- Candidate ordering is deterministic-first, LLM-last.

---

## P1.3 — RECORD grounding + provider selection (repair.py; injected record provider)

**Files touched**
- `src/python_deps/depgraph/repair.py` (extend)
- `tests/depgraph/test_repair_grounding.py` (new)

**Exact change**
- Define a `RecordProvider = Callable[[str], set[str] | None]`: given a distribution
  name, return the set of top-level module names its wheel RECORD/`top_level.txt`
  provides, or `None` when unknown (no wheel / not on index). Injecting this keeps
  grounding testable and keeps the network/PyPI detail out of the decision logic.
  (Production wiring in P1.4 supplies a provider backed by the resolved/installed
  dist-info; a PyPI-JSON provider mirroring the spike's `wheel_provides` is an
  acceptable alternative — kept behind the same seam.)
- `record_grounds(candidate_dist, import_name, provider) -> "confirm"|"deny"|"blind"`:
  `confirm` iff the provider lists the import's top-level module; `deny` iff the
  provider returns a set WITHOUT it (prunes transitive-only shims, e.g. the `bs4`
  dummy dist); `blind` when the provider returns `None` (no wheel to read).
- `choose_provider(import_name, candidates, provider) -> RepairDecision`: keep
  candidates whose grounding is not `deny`; among the kept, the grounded (`confirm`)
  set drives `decide`. Return a small frozen `RepairDecision(verdict, dist|None,
  candidates_considered)`:
  - exactly one `confirm` → `ACCEPT(dist)`
  - more than one `confirm` → `AMBIGUOUS` (never picks — Global Constraint)
  - zero `confirm`, ≥1 `blind` → surface the `blind` set for the P1.4 install
    backstop to arbitrate (do not accept on `blind` alone).
  - nothing survives → `UNRESOLVED`.

**TDD — write FIRST (RED)** (`test_repair_grounding.py`, fake provider dict)
- `record_grounds("PyYAML","yaml",prov)` == `"confirm"` when `prov("PyYAML")=={"yaml"}`.
- Shim prune: `prov("bs4")=={"_bs4_shim"}`, `prov("beautifulsoup4")=={"bs4"}` →
  `choose_provider("bs4", [bs4, beautifulsoup4], prov)` == `ACCEPT("beautifulsoup4")`
  (bs4 `deny`-pruned; grounding beats a naive install of the shim).
- Ambiguity: two candidates both `confirm` `attr` → `AMBIGUOUS`, `dist is None`.
- Blind: `prov(x)=None` for the only candidate → verdict defers to backstop (not
  `ACCEPT`); with no other survivors and no backstop signal → `UNRESOLVED`.
- Deterministic: same inputs → same `RepairDecision`.

**Acceptance criteria**
- Grounding prunes shims and hallucinations before acceptance; >1 confirmed provider
  flags AMBIGUOUS and never guesses.
- All grounding logic is pure over the injected provider; no network in the unit test.

---

## P1.4 — Phase-A fixpoint driver: bounded resolve → install → look → repair loop

**Files touched**
- `src/python_deps/depgraph/build.py` (edit `build_dep_graph`; add a private
  `_phase_a_fixpoint` helper + a `reconcile_packages` step — Correction 2c)
- `src/python_deps/depgraph/resolve_errors.py` (edit `_offending_root_names` to give
  declared roots drop-priority over AUDIT roots — Correction 2a)
- `src/python_deps/depgraph/coverage.py` (new pure `resolved_record_coverage(pkg_nodes)`
  = ∪ top-level(RECORD) over the resolved wheels — Correction 3; NOT
  `packages_distributions`)
- `tests/depgraph/test_phase_a_fixpoint.py` (new; needs a sequenced fake executor)

**Exact change**
- Extract the Stage-2a…Stage-4 span (era-anchor → resolve → install) into a bounded
  fixpoint. Pseudocode (matches the design):
  ```
  roots = select_roots(...)                       # declared-only (P0.1); DECLARED set is fixed
  exclude_newer = exclude_newer or compute_exclude_newer(roots)   # once (era-anchor)
  bound = min(len(initial_missing), 5)             # backstop (Correction 2b)
  attempted = set()                                # (import, candidate) pairs already tried
  prev_pkg_ids = set()
  for _ in range(bound + 1):
      pkg_nodes, pkg_edges = resolve_closure(roots, host, target_env=..., exclude_newer, extras)
      # Correction 2c — reconcile: drop stale Package nodes/edges the new resolve no longer emits
      graph = reconcile_packages(graph, pkg_nodes, pkg_edges, prev_pkg_ids)
      prev_pkg_ids = {n.id for n in pkg_nodes}
      graph = install_closure(graph, container)
      # Correction 3 — missing-set from the RESOLVED wheels' RECORD union, NOT packages_distributions
      provided = resolved_record_coverage(pkg_nodes)     # ∪ top-level(RECORD) over resolved wheels
      missing = [ runtime, non-optional Import nodes whose top-level ∉ provided ]
      if not missing: break
      new_pair = False
      for m in missing:
          cands = generate_candidates(m, llm=None)
          decision = choose_provider(m, cands, record_provider)
          if decision.verdict is ACCEPT and (m, decision.dist) not in attempted \
                 and decision.dist not in root_dists:
              roots += [(None, decision.dist)]          # under-declared root (re-stamped AUDIT post-resolve)
              new_pair = True
          attempted |= {(m, c) for c in cands}          # Correction 2b — remember every candidate tried
          # AMBIGUOUS / UNRESOLVED: leave the import to be flagged (P0.3); no root added
      if not new_pair: break                       # only already-tried pairs -> fixpoint reached (honest)
  else:
      logger.warning("phase-A hit bound=%d; residue left unresolved (honest), not aborting", bound)
  ```
- Provenance: a root added by repair is tagged so the Package node it resolves to
  carries `discovered_by = DiscoveredBy.AUDIT`. Thread a `repaired: set[canon_dist]`
  through and, after `resolve_closure`, `replace(...)` the matching Package nodes'
  `discovered_by` to `AUDIT` before adding them (RESOLVER→AUDIT re-stamp for
  repair-sourced dists only). Manifest-declared dists keep `RESOLVER`.
- `resolved_record_coverage(pkg_nodes) -> set[str]` (Correction 3): the union of
  top-level module names read from each RESOLVED wheel's RECORD / `top_level.txt`. This —
  NOT `packages_distributions()` — is the Phase-A "is it provided?" oracle: it reflects
  what the resolved closure *provides*, independent of whether install succeeded, so a
  resolved-but-failed-to-build dist is NOT misrouted to repair (that's a Phase-B gap).
  `packages_distributions()` / the certified relink stays the importability + Import→Package
  certification oracle (Phase B). sdist-only closure members with no wheel are `blind` here
  and fall to the install/import backstop, exactly as in P1.3.
- The `missing` computation reads Import nodes filtered by `data["optional"] != True`
  and no covering `provided` entry (top-level match). stdlib/local are already
  excluded at scan time.
- P1.4 keeps the EXISTING downstream stages (Stage 4.5 ldd, 4a relink, import_probe,
  4b apt, Stage 5 certify) in their current source order after the loop. Because the
  loop's final `install_closure` precedes them, ldd already observes repair-added
  packages. (P2 collapses the duplicate look and formalizes the ordering.)
- Iteration-bound behavior (design open decision 2): on bound-hit, **stop and leave
  residue flagged unresolved (honest); log a warning; never raise** (construction
  must not abort). Re-audit the FULL import set each round (not just newly-missing) so
  a resolution shift can't hide a regression.
- **Declared-drop-priority (Correction 2a):** edit `resolve_errors._offending_root_names`
  so a manifest-declared root is never chosen as the dropped imposer while an AUDIT
  (repaired) root is still in the conflict — never sacrifice a declared dep to keep a
  guessed one. Without this the loop can evict a declared dep and oscillate.
- **Per-round reconcile (Correction 2c):** `reconcile_packages` runs before merging each
  round's `resolve_closure` output — `without_node`/`without_edge` any Package the prior
  round produced that the new resolve no longer emits (version-keyed ids + an upsert-only
  graph would otherwise leave stale orphans).
- **Termination (Correction 2b):** stop when a round adds no NEW `(import, candidate)` pair
  (attempted-set), not merely when no root was added; numeric backstop
  `min(len(initial_missing), 5)`; on bound-hit leave residue `unresolved` and proceed (never
  raise). Re-install and re-audit the FULL import set each round so a resolution shift can't
  hide a regression. (This resolves design open decision 4 as re-install-each-round, but the
  audit reads the RECORD-union oracle per Correction 3, not `packages_distributions`.)

**Test support**: add a `SequencedFakeExecutor` (in `tests/depgraph/conftest.py`)
that returns queued results per command-substring key (pop per call, last repeats),
so `uv lock`/`pip install`/`packages_distributions` can differ across rounds. Keep
the existing `FakeExecutor` unchanged.

**TDD — write FIRST (RED)** (`test_phase_a_fixpoint.py`)
- **Converges on an under-declaration**: repo declares nothing but imports `yaml`
  (hard). Round 1: closure empty, `packages_distributions`={} → missing `{yaml}` →
  repair grounds `PyYAML` → ACCEPT. Round 2: closure has `PyYAML`,
  `packages_distributions`={"yaml":["PyYAML"]} → missing empty → break. Assert:
  a `PyYAML` Package node exists with `discovered_by == AUDIT`; `import:yaml` is NOT
  flagged unresolved; loop ran exactly 2 rounds.
- **Well-declared repo does 0 repair rounds**: declares `requests`, imports
  `requests`; `packages_distributions` covers it round 1 → break with one install
  (assert exactly one `pip install` call; no AUDIT nodes).
- **Iteration bound respected + honest residue**: import `zzznope` (no provider;
  `record_provider` returns `None`/deny for all candidates) → repair can't progress →
  loop stops, `import:zzznope` flagged `unresolved`, no fabricated root, warning
  logged, no exception.
- **Ambiguous does not pick**: import `attr` with a provider grounding both `attrs`
  and `attr` → `AMBIGUOUS` → no root added; the import stays flagged (or carries an
  `ambiguous` marker); assert neither `attrs` nor `attr` silently added.
- **Optional import never triggers repair**: guarded `try: import ujson` with no
  provider → not in `missing` → 0 repair rounds, not flagged.
- **Full-arm smoke** (extend `test_build.py`): the under-declared cv2/PIL/psycopg2
  fixture WITHOUT a pyproject (revert to under-declared) now converges via repair to
  the same Package nodes P0.1's declared fixture produced, with `discovered_by=AUDIT`.
- **Declared dep never evicted (Correction 2a)**: a conflict where a repaired AUDIT root
  and a declared root are mutually incompatible → the declared root survives, the AUDIT
  root is the one dropped/flagged; loop does not oscillate.
- **Attempted-set stop (Correction 2b)**: the only missing import's single grounded
  candidate, once added, is evicted by resolution (re-appears missing) → the loop stops
  after that pair is re-proposed (no re-add), residue flagged, ≤ bound rounds, no
  exception, oscillation warning logged.
- **Stale node reconciled (Correction 2c)**: two sequenced resolves where a transitive
  package's version changes between rounds → the round-1 `pkg:name==v1` node is ABSENT
  after round 2 (only `pkg:name==v2` remains), no orphan edges.
- **RECORD-union oracle, build-failure not misrouted (Correction 3)**: a resolved dist
  that FAILS to install (empty `packages_distributions`) but whose wheel RECORD provides
  the import → `resolved_record_coverage` marks it PROVIDED → NOT in `missing` → repair
  fabricates no alternative; the failure surfaces in Phase B instead.

**Acceptance criteria**
- Injected under-declaration → repair adds the package → re-resolve → import
  satisfied → loop terminates; iteration bound respected; residue honest.
- **Declared roots are never evicted to satisfy an AUDIT root; the loop terminates via
  the attempted-set (Correction 2a/2b); each round leaves no orphaned Package nodes/edges
  (Correction 2c).**
- **The Phase-A missing-set is computed from `resolved_record_coverage` (RECORD-union),
  never `packages_distributions()` (Correction 3).**
- Repaired packages carry `discovered_by=AUDIT`; declared packages keep `RESOLVER`.
- Every round returns a NEW graph; `compute_exclude_newer` computed once.
- Deterministic core: no `llm` argument passed anywhere in `build.py` (LLM stays out).
- Full suite green.

---

## P1.5 — Pre-install PyPI wheel-metadata record provider (make production repair FUNCTIONAL)

**Added during execution.** P1.4 shipped a correct fixpoint but with an interim,
brief-sanctioned `default_record_provider` that reads the container's POST-install
`packages_distributions()`. Because a repair candidate is by definition not yet
installed, that provider returns `None` for it → `record_grounds`=blind →
`choose_provider`≠ACCEPT → **no AUDIT root is ever added in a real build**. The
fixpoint's measured win (Correction-1 alias rescue: `cv2`→opencv-python, Track B 5/5)
therefore does not happen in production until a PRE-install provider exists. uv.lock
carries wheel filenames/hashes but no top-level module names, so the metadata must be
read from the wheel itself.

**Files touched**
- `src/python_deps/depgraph/coverage.py` (add a PyPI-backed provider + a composite)
- `tests/depgraph/test_record_provider.py` (new)
- `src/python_deps/depgraph/build.py` (wire the composite as the default `record_provider`)

**Exact change**
- Add `pypi_record_provider(*, fetch=...) -> RecordProvider`: given a dist name, resolve
  its wheel metadata and return the set of top-level modules from the wheel's
  `top_level.txt`/RECORD, or `None` when unavailable (no wheel / sdist-only / not found).
  **Inject the network fetch behind a seam** exactly like `pins.py` does for its PyPI
  reads (default implementation hits PyPI/reads the wheel; tests inject a fake fetch —
  NO network in unit tests). Mirror the validated shape of
  `underdeclaration_repair_poc.py::wheel_provides` (PyPI JSON → pick a compatible wheel →
  read `top_level.txt`, fall back to RECORD top-levels). Cache per dist. Runs HOST-side
  (like uv resolve / `compute_exclude_newer`), so it is construction-layer, not a build.
- Add `composite_record_provider(installed_provider, candidate_provider) -> RecordProvider`:
  return the installed (post-install, cheap) coverage for a dist when present, else fall
  back to the PyPI wheel read. This keeps cheap coverage for already-installed closure
  members while giving candidates/failed-builds a real pre-install answer.
- In `build.py`, make the DEFAULT `record_provider` the composite (post-install ∘ PyPI),
  so production repair actually grounds candidates. Keep the injected-provider seam intact
  for tests (tests still pass their own fakes).

**TDD — write FIRST (RED)** (`test_record_provider.py`, injected fake fetch — no network)
- `pypi_record_provider` with a fake fetch returning a wheel whose `top_level.txt` lists
  `yaml` → provider("PyYAML") == {"yaml"}; a dist the fake fetch 404s → `None` (blind);
  an sdist-only response (no wheel) → `None`.
- Shim fidelity: fake fetch where `bs4`'s wheel top-level is `_shim` and
  `beautifulsoup4`'s is `bs4` → `pypi_record_provider("bs4")=={"_shim"}` (so grounding
  DENYs it) and `("beautifulsoup4")=={"bs4"}`.
- `composite_record_provider`: installed provider covers `requests` → composite returns it
  WITHOUT calling the PyPI fetch (assert fetch not invoked); a candidate the installed
  provider lacks → composite consults the PyPI provider and returns its set.
- **Production-repair-now-functional** (extend `test_phase_a_fixpoint.py` or a new
  integration-style test with a fake fetch, NOT a null provider): an under-declared `yaml`
  with the COMPOSITE default provider (post-install look empty for the candidate + fake
  PyPI fetch grounding `PyYAML`) → repair ACCEPTs `PyYAML` → AUDIT node added. This is the
  test P1.4 could not write; it proves the default path (not just an injected fake) repairs.
- Determinism + cache: same dist queried twice → fetch called once.

**Acceptance criteria**
- With the composite default provider, an under-declared import whose provider is grounded
  by the (fake-injected) PyPI wheel read is repaired to an AUDIT root — production repair is
  no longer inert.
- No network in any unit test (fetch seam injected); the provider is pure over its injected
  fetch.
- Already-installed closure members are covered by the cheap post-install path without a
  PyPI call (composite short-circuit).
- Full `tests/depgraph` + `tests/pkg_layer` green.

---

# P2 — Phase-B reorder (clean two-phase sequence)

## P2.1 — Reorder to Phase-A-before-Phase-B; relink drives the loop condition

**Files touched**
- `src/python_deps/depgraph/build.py` (edit stage ordering)
- `src/python_deps/depgraph/relink.py` (expose the coverage the loop needs)
- `tests/depgraph/test_build_phase_order.py` (new)

**Exact change**
- **Two oracles, not one (Correction 3).** The Phase-A loop condition is
  `resolved_record_coverage` (RECORD-union of the resolved wheels) — "is it provided?" —
  computed each round from the resolve output. The certified relink
  (`certified_import_links` / `packages_distributions`) is NOT the loop condition; it runs
  ONCE at the start of Phase B, on the converged closure, to add certified Import→Package
  edges and flag the residue (`flag_unresolved_imports`, P0.3). This keeps "is it provided"
  (RECORD, Phase A) separate from "does it load / who actually provides it" (relink +
  import, Phase B), and yields exactly one `packages_distributions()` call per build.
- After the loop converges, run the **single Phase-B tier descent, in this order**:
  `ldd_probe → import_probe → reconcile_apt_names → certify_all`. Move `ldd_probe`
  to run AFTER the converged relink (today it runs before relink). This is the whole
  point of the ordering: `ldd` sees `pyyaml`'s `_yaml.so → libyaml` only because
  `pyyaml` entered the closure during Phase A.
- Keep `seed_wheel_oracle_prior` / `add_subprocess_tool_nodes` / `_add_project_node`
  as Phase-B auxiliaries hung after convergence (they don't grow the Python closure).
- Update the module docstring's staged-pipeline comment to the two-phase shape.

**TDD — write FIRST (RED)** (`test_build_phase_order.py`, using `SequencedFakeExecutor`)
- **ldd runs on the post-repair closure**: an under-declared `yaml` repaired to
  `PyYAML` in Phase A; the container's `ldd` of `_yaml*.so` reports
  `libyaml.so.2 => not found`. Assert a `SystemLib` node for `libyaml` exists — i.e.
  `ldd` ran after the repair add (would be absent if ldd ran pre-convergence).
  Assert the executor's call order: last `packages_distributions` (loop look)
  precedes the `ldd` call.
- **magic/libmagic routes to Phase B, not Phase A**: declared `python-magic` is
  metadata-present (`packages_distributions` has `magic`) → NOT in Phase-A `missing`
  (0 repair rounds) → `import_probe` in Phase B surfaces the `libmagic` `SystemLib`.
- **RECORD drives the loop; one certify (Correction 3)**: assert `packages_distributions`
  is called exactly ONCE for the whole build (at Phase-B start), while the Phase-A loop
  condition is `resolved_record_coverage`; no per-round `packages_distributions` probe.

**Acceptance criteria**
- Phase B (`ldd → import_probe → apt → certify`) runs exactly once, strictly after
  Phase-A convergence, on the final installed closure.
- The relink is the sole coverage look; no duplicate `packages_distributions` call.
- `magic`-class native-load failures surface in Phase B; metadata-absence in Phase A.
- Immutability + full suite green.

---

## P2.2 — Retire vestigial machinery: `_drop_superseded_ghosts` + provisional Stage 3a link

**Files touched**
- `src/python_deps/depgraph/relink.py` (remove `_drop_superseded_ghosts` call/def)
- `src/python_deps/depgraph/build.py` (drop the Stage 3a `link_imports_to_packages` call)
- `src/python_deps/depgraph/resolve_link.py` (leave `link_imports_to_packages`
  importable if still re-exported; drop only the construction call)
- `tests/depgraph/test_relink.py` (delete the ghost-drop tests)

**Exact change**
- With declared-only roots (P0.1), an import never becomes a MISSING placeholder
  Package (the identity-fallback that created ghosts is gone), so
  `relink._drop_superseded_ghosts` can never fire. Delete it and simplify
  `certified_import_links` to `flag_unresolved_imports(<graph + certified edges>)`
  (no ghost sweep). This also decouples relink's position from ldd (design note).
- Drop the provisional Stage 3a `link_imports_to_packages(graph)` call from
  `build.py` (design open decision 3): 4a certifies every Import→Package edge from
  the container, so the pre-install heuristic is redundant. Keep the function defined
  (still unit-tested in isolation) but unwired from construction.

**TDD — write FIRST (RED / update)**
- Delete `test_certified_import_links_drops_superseded_ghost`,
  `…_drops_superseded_versioned_ghost`, `…_keeps_versioned_missing_without_replacement`,
  `…_keeps_ghost_without_replacement`, `test_drop_ghost_never_removes_a_certified_target`
  — they assert machinery being removed. (Document in the commit why: declared-only
  roots produce no identity-fallback ghosts.)
- Keep/adjust `test_certified_import_links_adds_edge`,
  `…_graceful_on_command_failure`, and all `flag_unresolved_imports` tests (P0.3):
  a certified link + honest flag is the entire post-loop contract now.
- Add `test_no_provisional_3a_link_in_build`: spy that `build.py` does NOT call
  `link_imports_to_packages` (or assert the certified edges all carry
  `origin="certified"`, never the 3a heuristic origin).

**Acceptance criteria**
- `certified_import_links` = add certified edges + flag unresolved; no ghost sweep.
- Construction does not call the Stage 3a heuristic; certified edges are the only
  Import→Package source.
- Suite green after deleting the obsolete ghost tests; net line count drops
  (interpretability win).

---

## P2.3 — Honest flag for non-native import failures (Correction 4)

**Files touched**
- `src/python_deps/depgraph/probe.py` (extend `import_probe` result handling)
- `src/python_deps/depgraph/relink.py` (mark the Import node honestly)
- `tests/depgraph/test_import_probe_nonnative.py` (new)

**Exact change**
- Today `probe.import_probe` only creates a node when the import error matches
  `NATIVE_LIBRARY_RE` (a missing `.so`). An import that is metadata-present (a Package
  provides it) but whose `import X` raises for a NON-native reason — a broken package, a
  Python-level `ImportError`/`RuntimeError` at import time, a missing non-system
  sub-dependency — is caught by NEITHER phase: Phase A saw it "provided" (RECORD-union has
  it), and Phase B's `import_probe` ignores the non-native error.
- Extend the import-probe path so that when `import X` fails, the error is NOT a native
  soname miss (no `NATIVE_LIBRARY_RE` match), AND the import IS metadata-present, the Import
  node is flagged honestly — `data["import_error"] = <short reason>` +
  `data["unresolved_runtime"] = True` (distinct from the metadata-absent `unresolved` flag,
  so the two failure classes stay legible). Never silently pass; never fabricate a system
  lib for a non-native error.

**TDD — write FIRST (RED)** (`test_import_probe_nonnative.py`, sequenced fake executor)
- Native miss → `SystemLib` node created (unchanged existing behavior).
- Metadata-present import raising a Python-level `ImportError` (no soname) → the Import node
  carries an honest `import_error` / `unresolved_runtime` flag, NO `SystemLib` fabricated,
  no exception.
- Clean import (rc 0) → no flag.

**Acceptance criteria**
- The third failure class (metadata-present, non-native import failure) is surfaced as an
  honest per-Import flag, distinct from metadata-absence and from system-lib misses.
- No `SystemLib` is fabricated for a non-native error; construction never silently passes it.

---

# P3 — migration

## P3.1 — Re-point the A/B generator arm; regenerate outputs; confirm 30/0/30/0 in-tree

**Files touched**
- `scripts/eval/graph_fidelity/root_selection_ab.py` (edit `score_repo` generator arm)
- `scripts/eval/graph_fidelity/pkg_layer_ab.py` (edit CURRENT arm)
- `outputs/graph_fidelity/root_selection_ab.{json,md}` (regenerate)
- `outputs/graph_fidelity/pkg_layer_ab.{json,md}` (regenerate)

**Exact change**
- Post-P0.1, in-tree `select_roots` is declared-only, so the eval can no longer
  derive the "generator" arm from a single `select_roots` call (generator == verifier
  → divergence 0 → verdict "identical"). Re-anchor the **generator** arm to an
  explicit gap-fill reconstruction using the retained reference helper
  `naming.package_roots(graph, declared_names)`:
  ```python
  verifier = select_roots(repo_dir, graph)                      # declared-only (in-tree)
  gapfill  = package_roots(graph, {r.name for r in declared})   # the old generator adds
  generator = verifier ∪ gapfill                                 # reconstructed generator
  divergence = gapfill-not-in-verifier                           # exactly the 30 adds
  ```
  The eval now measures the SAME 30 divergent gap-fill adds against the in-tree
  declared-only selector — confirming the deletion removed exactly those 30 bad adds
  and zero good ones. Same reconstruction for `pkg_layer_ab.py`'s CURRENT arm.
- Regenerate both `outputs/*.json` + `*.md`; verify aggregate `total_divergence=30,
  good_adds=0, bad_adds=30, verdict="verifier"` for each (the 30/0/30/0 target).

**TDD / verification**
- The pure A/B unit tests (`test_root_selection_ab.py`, `test_pkg_layer_ab.py`)
  test `partition_roots`/`build_divergence`/`adjudicate`/`aggregate` on synthetic
  inputs — keep them green (they don't call the live selector).
- Add `test_generator_arm_reconstructs_gapfill`: given a graph with a curated import
  (`yaml`) not declared, the reconstructed generator arm includes `PyYAML` and the
  verifier omits it → divergence of exactly that add.
- Run both A/B mains against the clone corpus; assert the regenerated aggregate holds
  30/0/30/0.

**Acceptance criteria**
- Both A/B evals run in-tree and report 30 divergence / 0 good / 30 bad / verifier —
  confirming the deletion is a strict no-loss simplification against the real,
  post-deletion selector (not just a parallel module).
- Regenerated outputs committed.

---

## P3.2 — Rewrite/delete tests that asserted generator behavior

**Files touched**
- `tests/depgraph/test_naming.py` (docstring/banner: package_roots is now
  reference-only; keep functional tests since the helper is retained for the eval)
- `tests/eval/graph_fidelity/test_root_selection_ab.py` (update generator-arm test)
- `tests/eval/graph_fidelity/test_pkg_layer_ab.py` (update CURRENT-arm test)
- any `tests/depgraph/test_build*.py` assertion that assumed gap-fill roots

**Exact change**
- Sweep for assertions that depended on imports generating roots (curated gap-fill,
  identity fallback) and either delete them (behavior removed) or re-express them as
  the new contract (declared root + honest unresolved flag + Phase-A repair).
- `test_naming.py`: keep tests (the helper still exists) but add a header comment that
  `package_roots` is off the construction path (A/B reference only), so a future
  reader doesn't re-wire it.
- Ensure no test still asserts a fabricated root for an undeclared/uncurated import.

**TDD / verification**
- `grep` for `gap.fill`, `package_roots(` in `tests/` — every remaining reference is
  either the retained naming helper test or the A/B generator-arm reconstruction.
- Full `tests/depgraph`, `tests/eval/graph_fidelity`, `tests/pkg_layer` green.

**Acceptance criteria**
- No test asserts import→root generation as construction behavior.
- The retained `package_roots` tests are clearly labeled reference-only.

---

## P3.3 — Rollout: default-on (no flag-gate), full-suite gate, commit-local

**Files touched**
- (verification + commit only; a memory/ledger note per the record-observation rule)

**Exact change**
- The two-phase path is the ONLY path (Global Constraint: ONE clean path). No arm
  flag, no dual-mode toggle, no fallback to the generator. Confirm `build.py` has a
  single construction path.
- Run the FULL suite (`pytest -q` over `tests/`) and confirm green; run the two A/B
  evals and confirm 30/0/30/0. Record an Observation→Why→What→Verification entry in
  `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md` and a memory index note.
- Commit each task locally with conventional-commit messages; **do not push**
  (Global Constraint: commit-local-never-push).

**Acceptance criteria**
- One construction path; no flag gating the old generator.
- Full suite + both A/B evals green in-tree.
- Work committed on the branch, nothing pushed; ledger updated.

---

## Notes on preserved invariants (cross-task)

- **Era-anchor** (`compute_exclude_newer`) is computed once, before the P1.4 loop,
  and reused every round (never recomputed per iteration).
- **never-guess-variant**: P1.3 `choose_provider` flags AMBIGUOUS on >1 confirmed
  provider and never picks; P1.4 never adds an ambiguous root.
- **certify-or-flag**: construction certifies (relink/ldd/import_probe/certify) or
  flags (`unresolved`); the only "add" is a RECORD-grounded, install-backstopped
  under-declared root tagged `AUDIT`.
- **immutability**: every task's edits return a NEW `DepGraph`; the orchestrator only
  rebinds `graph`.
- **LLM out of the core**: the `llm` rung is an injected default-`None` callable in
  `repair.py`; `build.py` never passes one. Phase A's detection + first repair rungs
  are fully deterministic.
- **declared-priority (Correction 2a)**: a manifest-declared root is never dropped to
  satisfy an AUDIT root; the fixpoint cannot trade a declared dep for a guessed one.
- **fixpoint termination (Correction 2b)**: the loop stops on the attempted-set (no new
  `(import, candidate)` pair), bounded by `min(len(initial_missing), 5)`, and never aborts —
  residue is left honestly `unresolved`.
- **node reconcile (Correction 2c)**: each round removes the stale version-keyed Package
  nodes/edges the new resolve no longer emits — no orphans accumulate across rounds.
- **two oracles (Correction 3)**: "is it provided" is judged by the resolved wheels' RECORD
  union (Phase A); "does it load / who provides it" by import + `packages_distributions`
  (Phase B), which is called exactly once, at Phase-B start.
- **third failure class (Correction 4)**: a metadata-present import that raises for a
  non-native reason is flagged honestly (P2.3), never silently passed or mis-attributed to
  a system lib.
