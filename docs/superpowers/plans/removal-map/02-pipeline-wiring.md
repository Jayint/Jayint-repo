# Pipeline wiring map — two-phase declared-roots construction

Source of authority: `docs/superpowers/plans/2026-07-04-declared-roots-two-phase-construction.md`.
Subject: `src/python_deps/depgraph/build.py::build_dep_graph`, plus every stage
function it calls (`roots.py`, `resolve.py`/`resolve_link.py`, `relink.py`,
`probe.py`, `ldd_probe.py`, `seed.py`, `subprocess_scan.py`, `apt_verify.py`,
`certify.py`, `pins.py`).

## 1. Current stage order — table

| # | file:function | role (one line) | verdict |
|---|---|---|---|
| 1 | `scan.py:scan_to_nodes` | static AST import scan → Import + Test goal nodes | MODIFY (add an `optional` / try-except-guarded tag to Import nodes — data doesn't exist today; Phase A's audit needs it to exempt guarded imports) |
| 1.5 | `target_env.py:detect_target_env` (called `build.py:305`; standalone `build.py:215 _detect_target_python` kept for isolated unit tests) | probe container's python/platform → one `TargetEnv` | KEEP (must precede root selection; env is invariant across Phase-A iterations, computed once) |
| 2 | `roots.py:select_roots` → `naming.py:package_roots` gap-fill at `roots.py:289-299` | manifest roots ∪ scan-gap-filled mapped-import roots, marker/non-distribution filtered | MODIFY — **delete the gap-fill block** (`roots.py:289-299`, the `naming.package_roots` call and loop); `select_roots` becomes declared-only. `naming.package_roots` itself becomes DELETE (dead once its only caller is gone) or MOVE if repurposed as an input into the NEW repair overlay's curated-table rung |
| 2a | `pins.py:compute_exclude_newer` (`build.py:337-338`) | derive era-anchored `exclude_newer` from pinned roots | KEEP, unchanged call — but semantically becomes "compute once, before the Phase-A loop, from the *initial* declared roots" (never recomputed as repair grows `roots`) |
| (2a-runtime) | inline in `build.py:340-358` | add the `Runtime` obligation node for `target_python` | KEEP, unchanged position (depends only on `target_env`, not on `roots`/resolve; stays outside the loop) |
| 3 | `resolve.py:resolve_closure` | HOST `uv.lock` resolve of `roots` → Package nodes/edges | KEEP; **now runs every Phase-A iteration** (loop body) |
| 3a | `resolve_link.py:link_imports_to_packages` (re-exported via `resolve.py`, called `build.py:375`) | pre-install heuristic Import→Package reconcile (curated-table name match) | MODIFY/demote — plan: "provisional-only (4a certifies)"; can stay inside the loop as a cheap best-effort hint, but does **not** gate the loop's missing/audit determination (see §4 dependency note) |
| 3a' | `build.py:_add_project_node` | Project hub node; declared runtime deps → Project, test/optional deps → Test | MOVE → Phase B (runs once, after Phase A converges) |
| 3a'' | `subprocess_scan.py:add_subprocess_tool_nodes` | static subprocess/`shutil.which` CLI-tool scan → Tool nodes off the Project/Test anchor | MOVE → Phase B (runs once, right after 3a', same anchor dependency) |
| 3b | `seed.py:seed_wheel_oracle_prior` | one `build-essential` Tool prediction for every from-source Package | MOVE → Phase B, **with a caveat** — see §3/§4 (loses its install-stderr reconciliation opportunity for repair-added packages; flagged as an open tension, not silently absorbed) |
| 4 | `probe.py:install_closure` | ONE `pip install` of the resolved closure; build-time gaps → Tool nodes | KEEP; **now runs every Phase-A iteration** (loop body) |
| 4.5 | `ldd_probe.py:ldd_probe` | `ldd` every installed package's extension `.so` → run-time SystemLib (DT_NEEDED ground truth) | KEEP; MOVE — Phase B, runs once on the FINAL closure (this is the entire reason Phase A must converge first) |
| 4a | `relink.py:certified_import_links` (+ `flag_unresolved_imports`, `_drop_superseded_ghosts`) | `packages_distributions()` in-container → certified Import→Package edges; flags imports no Package provides | KEEP; **this IS the Phase-A "look" and loop condition** — runs every iteration, inside the loop, immediately after `install_closure`. `_drop_superseded_ghosts` becomes vestigial (plan: "with declared-only roots no import ever becomes a placeholder package") but is harmless to leave as a no-op safety net |
| — | NEW: repair overlay (no file yet — spikes: `scripts/eval/graph_fidelity/underdeclaration_repair_poc.py`, `llm_grounding_poc.py`) | for each import flagged unresolved: `normalize → curated_table → RECORD-ground → (accept one / flag ambiguous / leave unresolved)` | ADD — drives the Phase-A loop; lives entirely inside the loop, runs after 4a each iteration |
| import_probe | `probe.py:import_probe` | dlopen backstop: `python -c "import X"` on every Import/native-risk Package → SystemLib on `ImportError: lib*.so` | KEEP; MOVE → Phase B (dlopen gaps on the *final* closure; plan's `magic`/`libmagic` example lives here) |
| 4b | `apt_verify.py:reconcile_apt_names` | release-aware apt name remap (t64 transition etc.) against the target image | KEEP; MOVE → Phase B (must run after all apt fix-candidates exist, i.e. after 4.5/import_probe/3b) |
| 5 | `certify.py:certify_all` | host-run `check_command` on every node, execution-layer order, flips `state` | KEEP; MOVE → Phase B (must be last; must see the final graph) |

## 2. Target wiring

### Loop boundary

```
graph = scan_to_nodes(...)                      # 1, outside
graph = restamp(scan nodes, SCAN_CYCLE)
target_env = detect_target_env(...)             # 1.5, outside — once
roots = select_roots(repo_path, graph,          # 2, outside — ONE call,
                      needed_extras, target_env) #   declared-only, no gap-fill
exclude_newer = exclude_newer or compute_exclude_newer(roots)  # 2a, outside — once
graph = graph.with_node(<Runtime obligation node>)             # outside — once

pre_phase_a_ids = {n.id for n in graph.nodes}   # for the eventual _RESOLVER_CYCLE restamp

# ---------------- Phase A: fixpoint ----------------
for _ in range(bound):                          # bound, e.g. len(initial missing)+1
    pkg_nodes, pkg_edges = resolve_closure(roots, host_executor,       # 3
                                            target_env=target_env,
                                            exclude_newer=exclude_newer,
                                            extras=needed_extras)
    graph = reconcile_package_layer(graph, pkg_nodes, pkg_edges)       # ** NEW — see §4(b) **
    graph = link_imports_to_packages(graph)                            # 3a, optional hint
    graph = install_closure(graph, container_executor)                 # 4
    graph = certified_import_links(graph, container_executor)          # 4a — "the look"
    missing = [n for n in graph.nodes
               if n.type is NodeType.IMPORT
               and n.data.get("unresolved") is True
               and not n.data.get("optional")]                         # runtime-scoped audit
    if not missing:
        break                                                          # closure FINAL
    new_roots = [repair_candidate(m) for m in missing]                 # NEW overlay
    new_roots = [r for r in new_roots if r is not None]                # ambiguous/unresolved -> no root
    if not new_roots:
        break                                                          # no forward progress possible
    roots = roots + new_roots                                          # accumulate, never shrink
else:
    log.warning("Phase A did not converge within bound")

graph = restamp(graph, {n.id for n in graph.nodes} - pre_phase_a_ids, RESOLVER_CYCLE)

# ---------------- Phase B: tier descent, once ----------------
pre_phase_b_ids = {n.id for n in graph.nodes}
graph = _add_project_node(graph, repo_path)             # 3a'
graph = add_subprocess_tool_nodes(graph, repo_path)     # 3a''
graph = seed_wheel_oracle_prior(graph)                  # 3b — on the FINAL closure
graph = ldd_probe(graph, container_executor)            # 4.5 — authoritative
graph = import_probe(graph, container_executor)         # dlopen backstop
graph = reconcile_apt_names(graph, container_executor)  # 4b
graph = restamp(graph, {n.id for n in graph.nodes} - pre_phase_b_ids, PROBE_CYCLE)
graph = certify_all(graph, container_executor, cycle=CERTIFY_CYCLE)    # 5
return graph
```

### How a repair result re-enters `resolve_closure`

`resolve_closure`'s signature is untouched: it already takes an opaque
`list[tuple[import_id | None, dist_name]]`. A repair candidate is appended as
`(import_id_of(m), accepted_dist_name)` — the flagged Import node's own id
supplies `import_id`, so **no new tuple shape is needed**. This also gives
provenance for free: after the gap-fill deletion, *every* root with a non-`None`
`import_id` can only have come from Phase-A repair (manifest roots are always
`import_id=None`; the old gap-fill — the only other historical source of a
non-`None` `import_id` root — no longer exists). The design's "never conflate
repair with a manifest declaration" invariant falls out of the tuple shape
itself; no extra provenance field has to be threaded through `resolve_closure`.

### State that must persist across iterations

1. **`roots`** — append-only list; each iteration's `resolve_closure` call
   re-resolves the *whole* accumulated list from scratch (it is not
   incremental/delta — it shells out to `uv lock` fresh every call).
2. **`exclude_newer`** — computed once from the *initial* declared roots and
   frozen; never recomputed from repair-added roots (repair candidates are
   virtually never `==`-pinned, and recomputing against a growing root set
   would fight the "reproducible-build" intent the era anchor exists for).
3. **`graph`** — the single accumulating immutable `DepGraph`, rebound each
   call, exactly as today.
4. **Iteration bound** (design's open decision #2) — e.g. `len(missing_initial)
   + 1`, logged if hit.
5. **A no-forward-progress guard**, beyond what the plan's pseudocode states
   explicitly: if a round's `repair_candidate(m)` accepts nothing new for
   *every* flagged import (all ambiguous or all ungroundable), `missing` will
   be non-empty but `new_roots` empty — the loop must break here rather than
   spin to the iteration bound on names that can never resolve. `resolve_closure`
   already has this exact pattern internally (`if not offending or remaining ==
   current: break`); the Phase-A loop needs its own copy of it.

### What moves after convergence (whole Phase-B block)

3a' (Project hub), 3a'' (subprocess tools), 3b (wheel-oracle prior), 4.5 (`ldd_probe`),
import_probe (dlopen backstop), 4b (apt reconcile), 5 (`certify_all`). 4a does **not**
re-run in Phase B — the last Phase-A iteration's certified edges/flags are already
final once `missing` is empty, and the graph object carries them forward unchanged
(immutable accumulation, nothing is erased between phases).

## 3. Auxiliary nodes — where they belong

- **3a' Project hub / 3a'' subprocess tools** — MOVE to Phase B cleanly, no
  functional objection. Neither reads `roots`, resolve output shape, or
  anything Phase-A-loop-specific: Project hub matches *manifest*-declared deps
  (stable across all iterations, since repair never touches the manifest) to
  whatever Package nodes exist; subprocess-scan is a pure repo-disk AST walk
  keyed only on the Project/Test anchor. Running them once, after Phase A
  converges, is strictly cheaper than running them every iteration for
  identical output.

- **3b wheel-oracle prior — real tension, not a clean move.** `seed_wheel_oracle_prior`
  reads each Package node's `build_from_source` (set by `resolve_closure`'s
  native-risk stamp) and predicts one `tool:build-essential` node. Today it
  runs *immediately before* `install_closure` so that `probe.install_closure`'s
  `reconcile_predicted(...)` can fold a real build failure into the SAME node
  (keeping `discovered_by=RESOLVER`, capturing real stderr as evidence) instead
  of creating a duplicate `discovered_by=PROBE` node. If 3b is deferred
  wholesale to Phase B — i.e., emitted *after* every Phase-A install attempt
  has already happened — that reconciliation window is gone for good: the
  `build-essential` node it creates will only ever be a bare `state=UNKNOWN`
  prediction, certified later purely by `certify_all`'s direct `dpkg -s
  build-essential` check, never enriched with the actual failing-build
  evidence line. **This is not a correctness bug** (certify_all still flips
  state correctly), but it is a real loss of discovery quality/provenance the
  plan doesn't call out explicitly, worth flagging as an open decision
  alongside the plan's existing #1-4: either (a) accept the loss and move 3b
  to Phase B as the plan's "hang here" language suggests (it is explicitly
  described as superseded by ldd anyway), or (b) keep 3b running inside the
  Phase-A loop, immediately before each iteration's `install_closure` call, so
  it can still reconcile against that iteration's real build failures — cheap
  and pure, so there's no performance argument against re-running it every
  round. Recommend (a) for wiring simplicity, since ldd is authoritative and
  build-essential is coarse-grained (either present or not, independent of
  which specific package needed it).

## 4. Ordering dependencies that constrain the reorder

**(a) Direct answer to "does anything between 3 and 4a consume generator
output?" — no.** Deleting the Stage-2 gap-fill only shrinks the `roots` list.
Between Stage 3 (`resolve_closure`) and Stage 4a (`certified_import_links`),
only two things read `roots` at all: `compute_exclude_newer` (2a, already
computed before Stage 3) and `resolve_closure` itself (3). Everything else in
that span — 3a's `link_imports_to_packages`, 3a', 3a'', 3b, `install_closure`
— operates purely on `graph.nodes`/`graph.edges` (Package/Import node
identity, curated tables, disk re-scan), never on the `roots` tuple list. This
is what makes the gap-fill deletion structurally low-risk: it has exactly two
call sites to touch, not a web of downstream consumers.

**(b) NEW, not called out in the plan doc — the Package-layer must be
reconciled, not just accumulated, across Phase-A iterations.** `Node.id` for a
Package bakes in the resolved version (`ids.py:package_id` → `pkg:name==version`).
`DepGraph.with_node` upserts by id (replaces same-id, otherwise appends);
`DepGraph.with_edge` only ever adds, never removes. In the current single-pass
pipeline `resolve_closure` runs exactly once, so this is invisible. Under the
Phase-A loop, `resolve_closure` re-resolves the *entire* accumulated `roots`
list from scratch every iteration — if adding a repair root shifts a
transitive package's resolved version (or a previously-dropped
conflict/missing-diagnostic placeholder resolves cleanly once its conflicting
root is gone), the new iteration's `pkg_nodes` will carry a *different* node
id than the previous iteration's for that distribution. Naively looping
`for node in pkg_nodes: graph = graph.with_node(node)` will **not** remove the
stale old-version node — it becomes an orphaned duplicate that `install_closure`
tries to install alongside the real one, that `certified_import_links` may
attribute edges to ambiguously, and that `certify_all` certifies as a second,
confusing entry. The loop body needs an explicit reconciliation step before
merging each iteration's resolve output: diff the current Package-layer id set
against the new one and `without_node` every Package (and diagnostic
missing/conflict node) that Stage 3 no longer produced, before adding the new
set. This is the one piece of genuinely new bookkeeping the refactor requires
that isn't just "move code between two phases."

**(c) Cycle-stamp bookkeeping (`_restamp`) needs restructuring, not just
relocation.** The current `pre_resolve_ids`/`resolver_ids` and
`pre_probe_ids`/`probe_ids` snapshot-diff pattern assumes each stage group runs
exactly once. Under Phase A's loop, a single before/after snapshot spanning the
*whole* loop (taken before the first iteration, diffed after the loop breaks)
is the simplest correct adaptation — stamping every node discovered across all
iterations (initial resolve + every repair round) as one `_RESOLVER_CYCLE`,
consistent with "the closure is final only when this converges" (the
iterations are a mechanism to reach that one milestone, not separate discovery
cycles). Phase B's `pre_probe_ids`/`_PROBE_CYCLE` snapshot is unaffected — it
already brackets a run-once block, just later in the function now.

**(d) 3a's edges currently widen what Phase A's audit considers "provided."**
`flag_unresolved_imports` (inside 4a) treats *any* incoming `REQUIRES` edge
from an Import to a Package as "provided," regardless of edge `origin`
(`resolver` / `reconcile` / `certified`). So 3a's heuristic edges, if kept
inside the loop before 4a, can suppress an unresolved flag that 4a's container
truth alone might not (or vice versa is not possible — 3a can only add edges,
never remove 4a's). In practice this is not a hidden correctness risk: 4a's
`packages_distributions()` observes the *installed* container directly, so
anything genuinely provided will be picked up by 4a regardless of whether 3a
ran. 3a is therefore safe to keep, drop, or relocate freely without changing
Phase A's convergence behavior — it only affects which edges look
"provisional" vs "certified" in the interim graph, matching the plan's framing
of 3a as optional-cleanup, not load-bearing.

**(e) 4b (apt reconcile) has a real precedence requirement.** It only inspects
`fix_candidates` already present on nodes (`_apt_package`), so it must run
*after* every stage that can populate an `apt:` fix candidate — 3b, 4.5, and
import_probe. Since all three now live in Phase B, 4b's position at the tail
of Phase B (before `certify_all`) is unchanged from today and needs no new
reasoning, but this is worth stating explicitly since it is the one place
where the Phase-B stages still have a strict internal order (3a'/3a'' → 3b →
4.5 → import_probe → 4b → certify_all), not a free-for-all.
