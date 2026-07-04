# Risk review — two-phase declared-roots construction

Adversarial implementability review of
`docs/superpowers/plans/2026-07-04-declared-roots-two-phase-construction.md`.
Read against the real code: `build.py`, `roots.py`, `naming.py`, `relink.py`,
`resolve.py` (+ `resolve_errors.py`, `resolve_lock.py`), `probe.py`, `scan.py`,
`import_graph.py`, `import_mapping.py`.

Verdict: the *phasing* is sound (Phase B genuinely needs the post-repair closure),
and *detection* infra already exists. But the design under-weights three things that
will hurt a faithful build: the repair overlay is load-bearing and cannot be
decoupled from the deletion; the fixpoint runs on top of a resolver drop-heuristic
that has **no notion of "don't sacrifice a declared root"**; and the "runtime,
non-optional" audit scope is **not reconstructable** from today's graph. Findings
ranked by severity, each with a concrete failure scenario and a mitigation.

---

## R1 (CRITICAL) — Deleting the generator without the repair overlay regresses ≥5 measured repos; relink alone cannot recover

**Claim under attack:** "Everything that makes install-and-look work already exists —
it was just being fed fabricated roots." True for *detection*, false for *recovery*.

**Walk the logic.** The gap-fill's only non-redundant contribution is the **curated
table** rung (`import_mapping.CURATED_IMPORT_TO_PACKAGE`: `cv2→opencv-python`,
`yaml→PyYAML`, …) for an import whose provider is **undeclared and not pulled in
transitively**. For that case:

- Today: `naming.package_roots` maps `cv2→opencv-python`, `roots.py:290-299` injects
  it as a root → resolved, installed, build works.
- Declared-only Phase A *without repair*: `opencv-python` is never a root → never
  installed → `packages_distributions()` has no `cv2` → import flagged
  metadata-absent → **build has no provider**. Regression.
- `certified_import_links` (relink, `relink.py:47-89`) only adds **edges to packages
  already in the closure**; it cannot conjure a root that was never resolved. So
  relink does **not** recover this class. Only the Phase-A repair overlay does
  (`normalize → curated_table → RECORD-ground → add as AUDIT root → re-resolve`).

**Evidence it's real:** memory `[[root-selection-ab-eval-landed]]` — "Track B
generator recovers curated-alias **5/5**". At least 5 corpus repos depend on this
recovery path today.

**Conversely, prove the safe subclass:** a **declared** dep whose dist-name mismatches
its import (e.g. declared `python-dateutil`, import `dateutil`) is *always* a root
(manifest path, `import_id=None`), so it is resolved+installed and relink maps it via
`packages_distributions()`. That subclass is fully covered — no repair needed. The
only gap is genuinely-undeclared curated aliases.

**Mitigation:** the generator deletion and the deterministic repair overlay
(normalize + curated table + RECORD-ground + re-resolve) are **one atomic change**,
never two commits. Land the overlay first (dark, behind the flag in R7), prove
curated-alias recovery holds in-tree (the plan's own "5/5 holds" test), *then* delete
`roots.py:289-299` and `naming.package_roots`. Do **not** merge the deletion on the
promise of a later overlay.

---

## R2 (CRITICAL) — The fixpoint can silently drop a *declared* root and live-lock; the resolver's drop-heuristic has no declared-vs-repair priority

**Claim under attack:** "Convergence is well-founded — each round covers ≥1 import;
re-audit the full set each pass so a resolution shift can't hide a regression. Bound
iterations as a backstop." The re-audit *detects* a regression; it does not *prevent*
the loop the regression creates, and the backstop bound masks it.

**Root cause in real code:** `resolve_closure` has per-root drop resilience
(`resolve.py:306-318`). When adding a repaired root causes a version conflict, it
calls `_offending_root_names` (`resolve_errors.py:354-380`) which, for a conflict on a
direct root, drops that root, and otherwise drops `sorted(root_imposers)[0]` — the
**alphabetically-first** imposer. **There is no distinction between a manifest-declared
root and an AUDIT-added repair root.** A repair root can therefore win a tie and evict
a *declared* dependency.

**Concrete failure scenario.** Repo declares `A` (constrains `libX>=2` transitively),
and runtime-imports `z` (undeclared, provided by dist `Z` which constrains `libX<2`).

1. Round 1: `roots={A}` → install → `z` absent → flag → repair adds `Z`.
2. Round 2: `roots={A, Z}` → resolve conflict on `libX`. `_offending_root_names` names
   an imposer to drop by `sorted(...)[0]`. If it drops **`A`** (declared!), the
   closure loses `A`'s subtree → `A`'s own imports now audit as missing.
3. Round 3: repair re-adds a candidate for `A`'s imports → conflicts with `Z` again →
   drop `Z` → `z` missing again → re-add `Z` → …

Two bad outcomes: (a) **oscillation** between two mutually-exclusive constraints, and
(b — worse) **a declared dependency is silently sacrificed to satisfy an audit-guessed
one.** The bound (`|missing_initial|+1`) just cuts the oscillation off; it does not
stop the declared-dep loss.

**Why the design's well-foundedness argument fails:** the missing set is **not
monotone** under a global re-resolve with drop-resilience — a repair-add can make a
*previously-satisfied* import missing (its provider got dropped). So "each round covers
≥1 import" is false; the measure can grow.

**Mitigation (two parts, both required):**
1. **Priority-drop.** Tag AUDIT roots (`provenance=AUDIT` already planned). Teach
   `_offending_root_names` (or a wrapper the Phase-A loop passes) to **prefer dropping
   AUDIT roots over declared roots**, and to **never evict a declared root to satisfy
   an AUDIT root**. When only a declared root can be dropped, abandon that repair
   instead and leave the import `unresolved`. This makes declared intent strictly
   dominant — matching the design's own "declared is authoritative for intent".
2. **Monotone measure + no-progress stop.** Track a set of already-attempted repair
   candidates; terminate when a round adds **no new** candidate (fixpoint reached, even
   if `missing` is non-empty) — not only when `missing` is empty. Flag the residue
   `unresolved` and proceed. (See R5 for the dedup/attempted-set mechanics.)

---

## R3 (HIGH) — "Runtime, non-optional" audit scope is NOT reconstructable from today's graph; without new scanner work the audit manufactures false under-declarations

**Claim under attack:** the mapping table says scan.py just needs to "add `OPTIONAL`
context" — framed as a minor add. It is the load-bearing prerequisite for the audit
not to regress well-declared repos, and most of it is genuinely new work.

**What the scanner actually produces today** (`import_graph.py:160-172`,
`scan.py:132-173`): `_imports_from_ast` does `ast.walk(tree)` and records **every**
`Import`/`ImportFrom` flatly. It captures **no** context — not try/except, not
`if TYPE_CHECKING`, not function-local vs module-level, not test-only. Every Import
node hangs off the single `TEST_NODE_ID` goal (`scan.py:164-171`); the runtime-vs-test
split in `build.py._add_project_node` is about **declared deps**, not imports. So the
graph cannot answer "is this import runtime and non-optional" at flag time.

**Reconstructable vs not:**
- **try/except ImportError** and **`if TYPE_CHECKING`** guards: **NOT** reconstructable.
  `ast.walk` discards nesting. This needs a real `ast.NodeVisitor` with a handler/guard
  stack (track enclosing `Try` blocks that catch `ImportError`/`ModuleNotFoundError`,
  and `TYPE_CHECKING` bodies) — new scanner code, TDD-first.
- **test-only imports**: *partially* reconstructable — `ImportFinding.source_files`
  already carries paths and `scan.py` already has a `tests`/excluded-segment notion —
  but it is not carried onto the Import node except as a comma-joined `provenance`
  string (fragile), and the single Test-parent edge shape doesn't express it.

**Concrete false-under-declaration scenarios if skipped:**
- `try: import ujson except ImportError: import json` → audit flags `ujson`
  metadata-absent → Phase A injects `ujson` as a root → over-installs an accelerator
  the repo deliberately made optional.
- `if TYPE_CHECKING: import pandas` in a repo with no runtime pandas dep → injects a
  heavy root.
- `try: import psycopg2 except: import pymysql` → both flagged → **both** injected →
  can reintroduce the mutually-exclusive collision the design set out to kill.

**Mitigation:** build the guard-aware AST visitor as **Phase 0** of implementation, and
make the audit **fail toward NOT flagging** — an import is flagged only when *proven*
runtime + non-optional (module-level, not under an `ImportError` handler, not under
`TYPE_CHECKING`, appears in a non-test source file). Absence of context must never
manufacture a root. Emit the OPTIONAL/test flags as `Node.data` on the Import node so
the audit reads them directly.

---

## R4 (HIGH) — The metadata-vs-runtime split misroutes four real cases; the dichotomy is not exhaustive

**Claim under attack:** "`packages_distributions()` cleanly separates under-declaration
(metadata-absent → A) from system-lib (present-but-won't-load → B)… the probes never
compete for the same signal."

**(a) Build-failed-but-resolved packages → misrouted as under-declaration.** Phase A
audits against the **installed** env, but `install_closure` +
`_reinstall_survivors` (`probe.py:197-231`) deliberately **drop build-failing packages
and reinstall the survivors**. A package that *resolved* (in closure) but *failed to
build* is absent from `packages_distributions()`. Its import then audits as
metadata-absent → Phase A tries to "repair" an under-declaration that doesn't exist.
The true cause is a build-toolchain gap (a **Phase B** class: compiler/header/system
lib). Repair adds a wrong/duplicate root and the next install fails identically.
*Mitigation:* run the metadata-absence oracle on the **resolved wheels' RECORD union**
(the design's own "Optimization"), not on `packages_distributions()` — RECORD is
present pre-build, so a build failure no longer looks like under-declaration. See R7/OD4.

**(b) PEP 420 namespace packages → false under-declaration.** `import google.cloud.x`
→ audit keys on `top_level_import_name` = `google`. Pure namespace packages are
historically under-reported by `packages_distributions()` (no single dist owns the bare
`google`), so `google` can be metadata-absent even with `google-cloud-*` installed and
working → Phase A tries to find a dist for `google` → ambiguous/wrong or a spurious
`unresolved` flag. *Mitigation:* before flagging a dotted import, also check whether any
installed dist provides a **submodule prefix** (`google.cloud`) via RECORD, and treat a
namespace hit as satisfied; flag only when no dist provides any prefix.

**(c) Import provided only by an EXCLUDED extra → the fixed bug returns via the back
door.** Stage 2 excludes optional extras not in `needed_extras` (the whole point). If a
runtime code path hard-imports a module only provided by an excluded extra, the import
is metadata-absent → Phase A adds the underlying dist directly as an AUDIT root —
re-injecting exactly what `needed_extras` excluded (motivation #1 of the plan).
*Mitigation:* the repair must consult the excluded-extra requirement set; if the sole
grounded candidate for a missing import is a member of an **excluded** extra, do **not**
inject it — flag `unresolved` with evidence "provided only by excluded extra `<name>`".
Honest, and preserves the exclusion.

**(d) Metadata-present-but-import-raises-non-native → silently dropped by BOTH phases.**
An installed dist whose `import X` raises a plain `ModuleNotFoundError`/`ImportError`
(e.g. a broken optional sub-dependency), *not* an `OSError` on `lib*.so`: Phase A sees
the name in `packages_distributions()` → "satisfied", no flag. Phase B `import_probe`
only creates a node when `NATIVE_LIBRARY_RE` matches (`probe.py:257-261`); a non-native
`ImportError` records a failed Attempt and creates **nothing**. The failure vanishes.
The design advertises a two-cause dichotomy; this is a real third cause.
*Mitigation:* don't claim exhaustiveness. Have the Phase-A/relink "look" also treat a
metadata-present import whose `check_command` (`python -c "import X"`) fails
non-natively as a distinct **`import_error` flag** on the node (honest signal), routed
to neither A's add-package nor B's add-apt — surfaced for the downstream repair loop.

---

## R5 (MEDIUM) — Repair appends roots with no dedup and no attempted-set; feeds duplicate TOML deps and the R2 oscillation

`resolve_closure` → `_write_pyproject` → `_safe_dist_names` (`resolve_lock.py:61-63`)
**filters** injection-unsafe tokens but does **not dedupe**. The design's
`roots += repair_candidate(m)` appends directly. So (i) a candidate already present (or
a re-added drop victim from R2) yields **duplicate keys** in the pyproject
`dependencies` array — uv may reject or silently coalesce, and (ii) nothing stops a
round from re-proposing a candidate a prior round already tried-and-lost.
*Mitigation:* dedupe roots by normalized name on every append, and maintain an
`attempted_candidates` set keyed by (missing-import, candidate-dist); never re-propose a
pair, and terminate when a round proposes no new pair. This is the mechanical half of
R2's "monotone measure."

---

## R6 (MEDIUM) — `exclude_newer` is anchored on the initial roots only; repair-added roots don't move the era anchor

`build.py:337-338` computes `exclude_newer` once from `compute_exclude_newer(roots)`
and the design pins it "before the loop." Correct for stability, but a repair that adds
an **old-era pinned** package (e.g. an ancient `opencv-python`) after the anchor is set
means the anchor no longer reflects the closure's true era — the exact ABI-skew hazard
`compute_exclude_newer` exists to prevent (numpy 2.x pulled under an old pin). Low
frequency (repairs are usually unpinned aliases) but real.
*Mitigation:* keep the anchor fixed by default (stability > churn), but if a repair adds
a root carrying a pin **older** than the current anchor, log it and either (a) leave it
(document the accepted risk) or (b) recompute the anchor once and do a **single** final
re-resolve. Prefer (a) + log unless eval shows ABI breakage.

---

## R7 (MEDIUM) — Rollout: big-bang deletion is unsafe against the A/B harness; flag-gate for exactly one eval cycle

The plan's test line "whole-arm A/B re-run **after** the deletion lands" A/Bs too late —
by then the generator is gone and there is no in-tree control. Given the harness already
has `root_selection_ab` / `pkg_layer_ab` arms, the safe path is a **flag-gated dual-run**:
add a build-level boolean (e.g. `two_phase: bool`) selecting {declared-only + repair
overlay} vs {current gap-fill}, run **both arms in the same harness pass** on
medlarge15 + the unknown-repo corpus, and compare on honest `pass_rate` + closure
fidelity + the cheap LLM-judge (per `[[eval-grade-qualitatively-not-just-numbers]]`).
Only when the two-phase arm ≥ current do you flip the default **and delete the gap-fill
branch + the flag in the same PR**. The generator lives for exactly one eval cycle — not
forever — which is how you A/B without keeping it alive indefinitely.

---

## Open decisions — resolved

**OD1 — Repair in-construction vs deferred loop → IN-CONSTRUCTION, deterministic-only
core, LLM rung flag-gated OFF.** Phase B needs the final closure, so the fixpoint must
live in construction (the design's core argument holds; the deferred alternative
recomputes Phase B anyway). But split the ladder: the **deterministic** rungs
(`normalize` + curated table + RECORD-ground + re-resolve) run in-construction and cover
the measured curated-alias 5/5 cheaply and reproducibly; the **LLM rung** is
nondeterministic and expensive and belongs behind a default-off flag (or handed to the
downstream repair loop). This keeps construction deterministic and fast per
`[[core-branch-paper-interpretability-priority]]`.

**OD2 — Iteration bound value + behavior on hitting it → bound on distinct repair
candidates, terminate on no-progress, flag-and-proceed (never abort).** Set
`max_repair_rounds = min(len(missing_initial), 5)` as a backstop, but the *primary*
stop is "no new (import, candidate) pair proposed this round" (R5's attempted-set) —
this is the real fixpoint condition and it, not the numeric bound, guarantees
termination. On any termination without full coverage: mark the remaining imports
`unresolved` in `Node.data` (honest, exactly `flag_unresolved_imports` today) and
**proceed to Phase B with the best closure**. **Never error/abort** — a repo that builds
fine today but has one exotic under-declaration must still get a graph. Log the bound-hit
and the oscillation signature (a repeating candidate set) for diagnosis.

**OD3 — Keep or drop the pre-install heuristic link (3a) → DROP from the Phase-A
critical path.** With Phase A installing every round and `certified_import_links` (4a)
certifying Import→Package from the container, 3a's provisional edges are redundant for
correctness, and a redundant provisional stage cuts against the one-clean-path
directive. Drop `link_imports_to_packages` from the build path **after** grepping its
other callers (e.g. `advise.py`); if a non-construction consumer needs pre-install
edges, keep the function but stop calling it in `build.py`. Certified relink becomes the
sole Import→Package authority.

**OD4 — Pre-install RECORD-union vs re-install each round → re-install each round for
importability, but use the RECORD-union as the metadata-absence ORACLE (promote the
"optimization" to a correctness fix).** Keep per-round `install_closure` (simpler,
matches current code; a well-declared repo does 0 repair rounds = 1 install = today's
cost). But do **not** audit metadata-absence against `packages_distributions()` of the
post-build install — audit against the **resolved wheels' RECORD union** (dist→provided
imports, pre-build). RECORD is immune to build failures and namespace under-reporting,
which directly fixes R4(a) and mitigates R4(b): a resolved-but-unbuilt package no longer
masquerades as under-declaration. The real install + relink + import_probe remain the
**importability** backstop (Phase B). Net: two oracles for two questions — RECORD for
"is it provided" (Phase A), live import for "does it load" (Phase B).

---

## Top 3 risks (one line each)

1. **R1** — Deleting the gap-fill without the deterministic repair overlay landing
   atomically regresses ≥5 measured curated-alias repos; relink alone cannot recover an
   undeclared alias.
2. **R2** — The Phase-A fixpoint sits on `_offending_root_names`, which has no
   declared-vs-AUDIT drop priority (`sorted[0]`), so a repair root can evict a *declared*
   dependency and live-lock; the missing set is non-monotone, so the iteration bound
   masks rather than prevents it.
3. **R3** — "Runtime, non-optional" audit scope is not reconstructable from today's
   graph (no try/except / `TYPE_CHECKING` / test context); without a new guard-aware AST
   visitor the audit manufactures false under-declarations and can reintroduce
   mutually-exclusive-extra collisions.
