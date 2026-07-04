# Design: Two-phase declared-roots construction of the Python dependency layer

Status: design (replaces the import-generator root-selection arm)
Branch: john-planner-v3-core-autoresearch
Supersedes: the `select_roots` scan-gap-fill generator (declared ∪ mapped-imports)

## Intent

Construct the Python dependency layer of the graph as **declared-roots + certified
audit**, in two ordered phases:

- **Phase A — finalize the Python package set.** A fixpoint: `roots = declared →
  resolve → install → look (packages_distributions) → repair under-declarations →
  re-resolve`, until every runtime import is satisfied. The closure is *final* only
  when this converges.
- **Phase B — derive the lower tiers from the final closure.** A single tier
  descent: `ldd → SystemLib`, `import_probe` dlopen backstop, apt reconcile, host
  certify.

The single load-bearing change from today is: **imports stop generating roots**
(delete the scan-gap-fill); they become the audit that drives Phase A. Everything
that makes "install and look" work already exists — it was just being fed
fabricated roots.

## Motivation

1. **The gap-fill re-injects excluded extras.** `naming.package_roots`
   (`naming.py:53`) re-adds any declared name whose module is imported — including
   `optional_dependency` names Stage 2 excluded via `needed_extras` (they aren't in
   `seen`, so `roots.py:289-299` re-injects them). This over-installs optional
   extras and can reintroduce mutually-exclusive-extras collisions. The A/B eval
   measured this: 30 divergent adds, 0 good / 30 bad → **verifier**.
2. **Imports-as-generator is the pipreqs mistake.** Imports are authoritative for
   *demand/audit* (catching under-declaration), not for *generating* install roots.
   Declared is authoritative for *intent* (incl. non-imported deps: gunicorn,
   pytest, plugins). See the import→dist workflow: observe (packages_distributions)
   for coverage; the mapping ladder only for the residue.
3. **System-dep discovery must see the FINAL closure.** `ldd` derives native deps
   from each installed package's `.so` (`DT_NEEDED`). But the under-declaration
   repair *grows* the closure (`import yaml` → add `pyyaml` → `_yaml.so` needs
   `libyaml`). A single pass that runs `ldd` before repair converges misses the new
   packages' system deps. Hence Phase A must converge before Phase B.

## Core principle

- **Roots = declared (in-scope).** Imports never generate roots.
- **The installed environment is the naming truth** (`packages_distributions()`):
  `bs4→beautifulsoup4`, `cv2→opencv-python`, transitive and name-variant imports all
  resolve by observation, no table.
- **An unsatisfied import has two causes, split by probe:**
  - *metadata-absent* (no dist provides the import name) → under-declaration →
    **Phase A** repair (add a package).
  - *present-but-won't-load* (dist installed, import fails on a native lib, e.g.
    `magic`/`libmagic`) → **Phase B** SystemLib.
- **Certify or flag; never guess a variant** (>1 verified provider → flag ambiguous).
- **Honesty:** a repaired package enters as an under-declared root with repair
  provenance (`discovered_by = AUDIT`), never conflated with a manifest declaration.

## Phase A — finalize the Python package set (fixpoint on metadata coverage)

```
roots = select_roots(declared, needed_extras, target_env)   # declared-only; NO gap-fill
repeat (bounded):
    exclude_newer = exclude_newer or compute_exclude_newer(roots)   # era anchor, once
    closure  = resolve_closure(roots, exclude_newer, extras)
    install_closure(closure)                                        # into target container
    provided = packages_distributions()                            # metadata coverage
    missing  = runtime_imports − stdlib − local − provided.keys()   # runtime-scoped audit
    missing  = [m for m in missing if not is_optional(m)]           # try/except imports exempt
    if not missing:
        break                       # <-- package set is FINAL
    for m in missing:
        roots += repair_candidate(m)   # normalize → table → RECORD-ground → accept / flag
# closure finalized; imports certified against it
```

- **Detection is deterministic** — `packages_distributions()` coverage is a pure
  observation; the loop condition (`missing` empty) needs no model.
- **Repair is deterministic-first** — `normalize → curated table → RECORD-ground`;
  the LLM is a gated last rung only, so Phase A's core stays LLM-free. One verified
  provider → accept as under-declared root; >1 → flag ambiguous; 0 → leave
  `unresolved` (honest).
- **Convergence is well-founded** — the import set is fixed (static scan); each
  round covers ≥1 import; re-audit the *full* set each pass so a resolution shift
  can't hide a regression. Bound iterations (e.g. ≤ |missing_initial| + 1) as a
  backstop; log if the bound is hit.
- **Cost** — a well-declared repo does 0 repair rounds → 1 install → same cost as
  today. Only under-declared repos pay extra rounds.
- **Optimization (optional):** run the coverage check on the *resolved* wheels'
  RECORDs (dist→imports union, pre-install) each round and install only the final
  closure once. Same metadata answer, fewer installs; Phase B re-certifies with the
  real install regardless.

## Phase B — derive the lower tiers from the final closure (tier descent)

```
ldd_probe(final closure)   -> SystemLib      # DT_NEEDED of the WHOLE final set, incl. repair adds
import_probe               -> dlopen backstop # catches present-but-won't-load (magic/libmagic)
reconcile_apt_names        -> release-correct apt names for the target image
certify_all                -> host certification, layer-ordered, flips node state
```

- `ldd` sees `pyyaml`'s `_yaml.so` → `libyaml` because `pyyaml` entered the closure
  in Phase A. That is the entire reason for the ordering.
- Auxiliary discoveries hang here (Project hub node, subprocess CLI tools, the
  `wheel_oracle`/`PACKAGE_TO_SYSTEM_DEPS` proactive prior — a fallback `ldd`
  supersedes).

## Why the phases are a clean sequence (not nested)

1. **The two failure causes are routed by probe** — metadata-absence →
   Phase A (add package); runtime-load-failure → Phase B (add apt). `magic` passes
   Phase A cleanly (`python-magic` *is* in `packages_distributions`) and only
   surfaces in Phase B. The probes never compete for the same signal.
2. **No back-edge B → A** — Phase B repairs add apt/system packages, never Python
   packages, so they don't grow the closure; and the static scan already knows every
   import, so fixing a system lib reveals no new imports. B never sends you back to A.

## Mapping to the current pipeline

| current stage | file | change |
|---|---|---|
| 1 scan → Import + Test nodes | `scan.py` | keep; add `OPTIONAL` context so the audit can exempt try/except imports |
| 1.5 detect target python | `build.py:215` | keep |
| **2 select_roots (declared ∪ gap-fill)** | `roots.py:289-299` | **delete gap-fill; declared-only** |
| 2a exclude_newer | `build.py:338` | keep (compute once, before the loop) |
| 3 resolve_closure | `resolve.py` | keep; now inside the Phase-A loop |
| 3a link_imports (pre-install heuristic) | `build.py:375` | keep, provisional-only (4a certifies) |
| 4 install_closure | `build.py:399` | keep; inside the Phase-A loop |
| **4a certified_import_links (relink + flag)** | `relink.py:165` | keep; **this is the "look"** + loop condition |
| — repair under-declarations | NEW (overlay) | add: drives the Phase-A loop |
| 4.5 ldd_probe | `build.py:405` | keep; **Phase B — after A converges** |
| import_probe | `build.py:411` | keep; Phase B backstop |
| 4b reconcile_apt_names | `build.py:418` | keep; Phase B |
| 5 certify_all | `build.py:421` | keep; Phase B |

## What gets deleted / demoted (net simplification)

- ✂️ `roots.py:289-299` — the scan gap-fill (the generator).
- ✂️ `naming.package_roots` for construction — dead; the `needed_extras`
  re-injection bug goes with it. Relocate to the repair overlay if reused.
- ⬇️ `import_mapping` curated table — pre-install root authority → post-install
  repair candidate (untrusted, RECORD-verified).
- ⬇️ `link_imports_to_packages` (3a) — purely provisional; 4a certifies. Can shrink
  to a best-effort hint (optional cleanup).
- ⬇️ `relink._drop_superseded_ghosts` — with declared-only roots no import ever
  becomes a placeholder package, so there are no identity-fallback ghosts to drop;
  it becomes vestigial (this is also what decouples `relink`'s position from `ldd`).

## Invariants preserved

- **era-anchored resolve** unchanged (`compute_exclude_newer`), computed once.
- **runtime-scoped audit** — flag only runtime, non-optional imports; Test nodes are
  already separated by the scan; optional (`try/except ImportError`) imports are
  exempt by design.
- **never guess a variant** — ambiguity flags, doesn't pick; the surrounding
  Contract/Closure breaks most ties (prefer the variant already in the lock / named
  by a declared extra).
- **system-lib routing** — a repaired import that still won't load is a `SystemLib`
  need (Phase B), not a silent accept.
- **certify-or-flag** — construction only certifies (from the container) or flags
  (`unresolved`); it never fabricates a root. The deletion is what makes that true.
- **immutability** — every stage returns a NEW `DepGraph`.

## Repair ladder (the mapping overlay used inside Phase A)

Validated end-to-end by two spikes (`underdeclaration_repair_poc.py`,
`llm_grounding_poc.py`): grounding names it, the cert-build certifies importability.

```
for each runtime import flagged unresolved (metadata-absent):
    C = normalize(X) ++ curated_table(X) ++ llm(X)     # deterministic first, LLM gated last
    C = [c for c in C if record_grounds(c, X) != deny] # cheap RECORD peek; prunes shims/hallucinations
    decide:
        exactly one grounded  -> add as under-declared root (provenance=AUDIT) -> re-resolve
        more than one         -> flag AMBIGUOUS
        none                  -> leave UNRESOLVED (honest)
```

- RECORD grounding is the naming authority (and *beats* install on transitive shims,
  e.g. the `bs4` dummy dist).
- The cert-build (Phase A install + 4a, run anyway) is the importability backstop and
  the only thing that catches the system-lib class (`magic`/`libmagic`) — routed to
  Phase B.
- Curated table + LLM are untrusted candidate sources; neither is an authority.

## Test plan (TDD)

- `select_roots` returns declared-only; **an excluded optional extra whose module is
  imported is NOT re-injected** (the gap-fill regression).
- Genuinely-missing runtime import → `unresolved` flag fires; **transitively
  satisfied import (`urllib3` via `requests`) does NOT flag**; name-variant (`cv2`)
  does NOT flag; **optional (`try/except ImportError`) unsatisfied import does NOT
  flag**.
- Phase-A fixpoint: injected under-declaration → repair adds the package →
  re-resolve → import now satisfied → loop terminates; iteration bound respected.
- Phase ordering: a repair that adds a native-backed package (`pyyaml`) → Phase B
  `ldd` surfaces its `SystemLib` (`libyaml`) — i.e. `ldd` ran on the post-repair
  closure.
- `magic`-style case: `python-magic` metadata-present → NOT a Phase-A under-decl →
  Phase B `import_probe` surfaces the `libmagic` `SystemLib`.
- Whole-arm A/B (`root_selection_ab` / `pkg_layer_ab`) re-run after the deletion
  lands in `depgraph/` — confirm 30/0/30/0 holds in-tree, not just the parallel
  module.

## Open decisions

1. **Repair in-construction vs deferred loop.** This design puts the Phase-A fixpoint
   *in construction*, because Phase B needs the final closure. The LLM rung stays
   gated so the deterministic core is unaffected. (Alternative: leave repair to the
   downstream repair loop and accept a stale first-pass system tier — rejected: it
   recomputes Phase B anyway.)
2. **Iteration bound value** and behavior on hitting it (flag vs error).
3. **Keep or drop the pre-install heuristic link (3a)** now that 4a certifies.
4. **Pre-install RECORD-union optimization** for the loop (install once at
   convergence) vs re-install each round (simpler, matches current code).
```
