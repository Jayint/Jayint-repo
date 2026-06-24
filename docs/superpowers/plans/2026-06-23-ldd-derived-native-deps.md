# Derive Native Deps from the Binary — `ldd` Discovery + Dynamic apt Names — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop relying on the hand-curated `tables.PACKAGE_TO_SYSTEM_DEPS` as the *source of truth* for a package's run-time native libraries. Instead **derive** them from ground truth: after install, run `ldd` on each package's compiled extension `.so` files and collect the libraries the dynamic linker reports as `=> not found`; resolve those sonames to release-correct apt packages by asking the **target image's** apt database (`apt-cache showpkg` reverse-provides), not a hardcoded name. The curated table is demoted from authority to a proactive fallback (used only before install / when install fails).

This is the root-cause fix for the "apt knowledge hardcoding" problem: it fixes both axes the table got wrong — the *knowledge* (which lib is needed) and the *name* (which apt package provides it on this release) — and it generalizes to packages not in the table.

**Feasibility:** empirically validated by four Sonnet investigations (2026-06-23):
- `ldd` on extension modules (filtered to `*cpython-NNN*.so` / `*.abi3.so`) yields the exact missing sonames with **zero false positives** across 9 packages; opencv → `libGL.so.1`+`libgthread-2.0.so.0`+`libglib-2.0.so.0` (exact). auditwheel-bundled libs resolve via RPATH and are correctly *not* flagged.
- `apt-cache showpkg` "Reverse Provides" resolves the t64 rename (and any future virtual→real rename) authoritatively at ~zero cost.
- PEP 725 (skip — zero adoption) and conda-forge (defer — scoped proactive fallback only) are out of scope here.

---

## Scope

**IN (option A — table-independent *knowledge*, release-correct *names* for known sonames):**
- A `ldd_probe` builder stage that discovers run-time native gaps from installed binaries (works for any package, no table entry needed — the *knowledge* axis).
- A `showpkg`-based virtual-provider upgrade to the apt-name resolver (release-correct *names* for known sonames; generalizes the t64 fix).
- Wiring `ldd_probe` into `build.py`, with the curated table demoted to a proactive fallback (kept, not deleted — it's still the only proactive source when install fails; the seed-vs-probe reasoning).
- Unit tests (FakeExecutor, no Docker) + one Docker-gated integration test proving table-independent *knowledge* (a package *not* in `PACKAGE_TO_SYSTEM_DEPS`).
- **Already landed (review C1):** `reconcile_apt_names` remaps the seed node's `dpkg -s` `check_command` with the name (fix shipped + tested before this plan).

**OUT (deferred / non-goals — see Future TODOs for option B):**
- Release-correct apt *names for UNKNOWN sonames* (not in `NATIVE_LIB_TO_APT`) — needs `apt-file` (~26s + 50 MB index, absent on slim images). Option A surfaces the *need* with empty `fix_candidates`; option B (lazy apt-file) supplies the name.
- `apt-file` as the soname *primary* (Agent 2 "Option B"): keep the existing apt-file *fallback* in `apt_resolve.py`; defer making it primary / lazy-installing it.
- conda-forge proactive KB (needs a wheel-availability filter + a conda→apt table — separate effort).
- PEP 725 `[external]` (no adoption; revisit later).
- Deleting `PACKAGE_TO_SYSTEM_DEPS` / `NATIVE_RISK_PACKAGES` outright — they remain as the proactive/install-fail fallback. The shift is *demotion to fallback*, not removal.
- Build-time tool gaps (`pg_config`) — unchanged (install-stderr parser + `TOOL_TO_APT`; `ldd` can't see a lib when the build never produced a `.so`).

**Forbidden files (do not modify):** `models.py`, `graph.py`, `external_graph/*`, `resolver.py`, `z3_adapter.py`, `pypi_metadata.py`.

---

## Architecture

```
build.py pipeline (scratch container):
  scan → roots → resolve(uv) → seed(predict, FALLBACK) → install
       → [NEW Stage 4.5: ldd_probe]   ← authoritative run-time native-lib discovery
       → relink(packages_distributions) → import_probe(dlopen backstop)
       → reconcile_apt_names → certify
```

- **`ldd_probe`** is the new primary source for run-time SystemLib nodes. It reuses `apt_resolve.resolve_soname_apt` for soname→apt (so it composes with the existing name resolution) and reconciles with any seed prediction of the same node (no duplicates; ldd's observed check/attempt enrich the predicted node, mirroring `probe._reconcile_predicted`).
- **`seed`** (`PACKAGE_TO_SYSTEM_DEPS`) stays as a *proactive hint / install-fail fallback*. When install succeeds, `ldd_probe` is authoritative; seed predictions it doesn't confirm are left for `certify` to judge (known minor over-predict risk — documented, not pruned here).
- **`import_probe`** stays as the dlopen backstop (libs loaded at runtime, not in DT_NEEDED).
- **Name resolution** gains `apt-cache showpkg` reverse-provides in `apt_verify.py`, replacing the fragile `t64_variant()` suffix as the primary remap (suffix kept as last-resort fallback).

**Testability:** `ldd_probe` is a pure parser + thin executor orchestrator (mirrors `probe.py`/`apt_resolve.py`); unit-tested with `FakeExecutor` (canned `ldd` / `apt-cache showpkg` output) — no Docker. One `@pytest.mark.docker` integration test exercises a real opencv-class build.

---

## Shared Interfaces (keystone)

### `src/python_deps/depgraph/probe.py` — EDIT (prerequisite, review H1)
```python
# Promote the private _reconcile_predicted to a PUBLIC, shared helper so ldd_probe
# reuses it instead of importing a private symbol (cross-module coupling). Rename
# _reconcile_predicted -> reconcile_predicted; update its two callers
# (install_closure, import_probe). ldd_probe imports the public name.
def reconcile_predicted(graph, predicted_id, *, check, evidence, command) -> Node | None: ...
```

### `src/python_deps/depgraph/ldd_probe.py` — NEW
```python
# One container round-trip: {canonical_dist_name: [abs ext-.so paths]} for all
# installed dists, via importlib.metadata. The command MUST:
#   * guard `files = dist.files; if files is None: continue`  (review H2/H1: ~9% of
#     installed dists have no RECORD -> files is None -> TypeError crashes the stage);
#   * build ABSOLUTE paths via `dist.locate_file(f)` (review H2/M2: files() are
#     RELATIVE; `ldd <relative>` silently finds nothing);
#   * keep only ext modules: basename matches r"\.cpython-\d{3}.*\.so$|\.abi3\.so$";
#   * EXCLUDE bundled manylinux helpers: path containing "/<dist>.libs/" or basename
#     matching r"^lib[a-z0-9._+-]+-[0-9a-f]{8}\.so" (review: standalone-ldd'ing a
#     bundled helper can't follow the parent RPATH -> false `not found`).
EXT_SO_MAP_CMD: str

def parse_ext_so_map(stdout: str) -> dict[str, list[str]]: ...
def parse_ldd_not_found(stdout: str) -> list[str]:
    """Sonames from `=> not found` lines. Input may be MULTI-FILE ldd output
    (each file prefixed with `<path>:`): ignore any line lacking `=> not found`;
    take the token before `=>`; dedup (a soname can repeat within one file)."""

def ldd_probe(graph: DepGraph, executor: Executor) -> DepGraph:
    """For each Package node (matched to its dist via normalize_package_name on
    BOTH sides — review M3): batch-`ldd` its extension .so files, collect
    `=> not found` sonames -> SystemLib nodes + a `requires` edge Package->SystemLib.
    Resolve soname->apt via resolve_soname_apt; when a seed RESOLVER prediction of
    the same id exists, reconcile_predicted into it (keeps discovered_by=RESOLVER);
    otherwise create a fresh discovered_by=PROBE node. Returns a NEW graph; no-op
    for packages with no extension modules.

    NOTE (option A): resolve_soname_apt is table-first with an apt-file fallback
    that is ABSENT on slim images -> a soname NOT in NATIVE_LIB_TO_APT yields a
    node with EMPTY fix_candidates (the *need* is surfaced; the apt *name* is not).
    This is intentional for now; option B (lazy apt-file) closes it — see Future TODOs."""
```

### `src/python_deps/depgraph/apt_verify.py` — EDIT
```python
def resolve_virtual_provider(name: str, executor: Executor) -> str | None:
    """Real provider of a virtual/renamed package via `apt-cache showpkg`
    'Reverse Provides' (e.g. libglib2.0-0 -> libglib2.0-0t64), else None.
    showpkg ALWAYS exits 0 (review M2): decide on OUTPUT, not rc. There may be
    MULTIPLE Reverse-Provides lines for different versions of the SAME provider
    (review H4): collect provider names, dedup; return the unique name, else None."""

# resolve_installable_apt_name: candidate -> (apt-cache show) -> if absent,
#   resolve_virtual_provider -> verify -> else t64_variant fallback -> else candidate.
```

> **NOTE — already landed (review C1, fixed before this plan):** `reconcile_apt_names`
> now also remaps a seed node's `dpkg -s <name>` `check_command` when it remaps the
> name (else certify checks the stale name and reports MISSING forever once the t64
> package is installed). Tests: `test_reconcile_remaps_seed_node_check_command`,
> `test_reconcile_does_not_touch_soname_ldconfig_check`.

---

## Tasks

> T0 is a prerequisite for T1. **T1 and T2 are independent and may be done in parallel.** T3 depends on T1+T2.

### Task 0 — Make `reconcile_predicted` public (prerequisite; review H1)
- [ ] Rename `probe._reconcile_predicted` → `reconcile_predicted` (public); update its two callers (`install_closure`, `import_probe`).
- [ ] Existing probe tests still green (pure rename; no behavior change).
- **Acceptance:** `pytest tests/depgraph/ -q` green; no private cross-module import remains for ldd_probe to use.

### Task 1 — `ldd_probe.py`: locator + ldd parser + orchestrator (+ unit tests)
- [ ] `EXT_SO_MAP_CMD` + `parse_ext_so_map`: list installed dists' extension `.so` paths. **Guard `dist.files is None` (skip, don't crash)**; **use `dist.locate_file(f)` for ABSOLUTE paths**; keep `*cpython-\d{3}*.so` / `*.abi3.so`; exclude bundled helpers (path contains `/<dist>.libs/` or basename `^lib...-<8hex>.so`).
- [ ] `parse_ldd_not_found`: handle **multi-file** ldd output (per-file `path:` headers) — keep only `=> not found` lines, take the token before `=>`, dedup.
- [ ] `ldd_probe(graph, executor)`: match Package node ↔ dist via `normalize_package_name` on **both** sides; per package, batch-`ldd` its `.so` files; collect not-found sonames; for each, `resolve_soname_apt` → if a seed RESOLVER node of that id exists call `reconcile_predicted` (keeps `discovered_by=RESOLVER`), else create a fresh `discovered_by=PROBE` `SystemLib` node; add `requires` Package→SystemLib edge. No-op when a package has no ext modules.
- [ ] `tests/depgraph/test_ldd_probe.py` (FakeExecutor): opencv-like canned output → syslib nodes for libGL/glib w/ apt fixes + edges; pure-python pkg (empty map) → no nodes; the cpython filter drops bundled `lib*-<hash>.so`; a `files=None` dist is skipped (no crash); reconciling a pre-seeded RESOLVER prediction keeps `discovered_by=RESOLVER` (not PROBE) and creates no duplicate; an unknown soname (table miss, apt-file absent) yields a node with **empty fix_candidates** (option-A behavior, asserted explicitly).
- **Acceptance:** `pytest tests/depgraph/test_ldd_probe.py -q` green; no Docker.

### Task 2 — `showpkg` virtual-provider name upgrade (`apt_verify.py`) (+ tests)
- [ ] `resolve_virtual_provider` parsing `apt-cache showpkg` "Reverse Provides".
- [ ] Slot it into `resolve_installable_apt_name` ahead of the `t64_variant` fallback (showpkg first → t64 suffix last).
- [ ] Extend `tests/depgraph/test_apt_verify.py`: showpkg output remaps `libglib2.0-0 → libglib2.0-0t64`; existing t64-suffix path still covered as fallback; valid name untouched.
- **Acceptance:** apt_verify tests green; the remap no longer depends on the suffix heuristic alone.

### Task 3 — Wire Stage 4.5 + demote the table to fallback (+ regression)
- [ ] Insert `graph = ldd_probe(graph, container_executor)` in `build.py` after `install_closure`, before `certified_import_links`. Update the `build.py` module docstring (stage list) to include Stage 4.5.
- [ ] Confirm seed/`PACKAGE_TO_SYSTEM_DEPS` stays (fallback) and that ldd↔seed reconcile to one node per soname (extend a build test). Add a one-line code comment marking the table as a fallback, ldd as authoritative.
- [ ] **Comment-update (review):** document `NATIVE_RISK_PACKAGES`'s narrowed role at its use site in `probe.py` and at the `import_probe` call in `build.py` — "dlopen backstop only; DT_NEEDED gaps now covered by ldd_probe."
- [ ] Run full `tests/depgraph/` — no regressions (the existing `test_build` FakeExecutor returns nothing for the new `ldd`/`showpkg` commands, so those paths no-op in unit tests; confirm assertions still hold).
- **Acceptance:** full depgraph suite green; the new stage is inert under the existing FakeExecutors.

### Task 4 — Docker integration test: table-independent KNOWLEDGE
- [ ] `@pytest.mark.docker` test (skips when Docker absent): build a graph for a tiny repo depending on a native package **not in `PACKAGE_TO_SYSTEM_DEPS`** but whose sonames **are** in `NATIVE_LIB_TO_APT` (so option-A still yields apt names) — e.g. `pygame` (→ `libgthread-2.0.so.0`/`libglib-2.0.so.0`/`libX11.so.6`), pinned to `python:3.11-slim`.
- [ ] Assert: a ldd-derived `SystemLib` node exists with `discovered_by=PROBE`; its **soname is absent from `PACKAGE_TO_SYSTEM_DEPS`** for that package (proves knowledge came from ldd, not the table); apt fix is release-correct. This proves table-independent *knowledge* (the option-A scope); table-independent *names* for unknown sonames is option B.
- **Acceptance:** with Docker present, the test passes and demonstrates ldd-derived discovery without a `PACKAGE_TO_SYSTEM_DEPS` entry; skipped cleanly without Docker.

### Task 5 — Real-repo verification (manual, not in suite)
- [ ] Re-run the standalone build on the `hardcv` (opencv) repo (as in `scratchpad/integration_phase0.py`): confirm the frontier's `libgl1`/`libglib2.0-0t64` now come from `ldd` (`discovered_by=PROBE`), and that removing the opencv entry from `PACKAGE_TO_SYSTEM_DEPS` does **not** change the result (table-independence on a real build).
- **Acceptance:** opencv system deps are discovered with the table entry absent; names release-correct.

---

## Risks & mitigations
- **`ldd` needs a successful install.** Build-fail packages have no `.so` → fall back to install-stderr parsing (unchanged) + the seed proactive hint. Documented, not a regression.
- **`dlopen`'d libs** (not in DT_NEEDED) — `import_probe` remains the backstop; `ldd` + `import_probe` together cover both.
- **`.so` filter precision** — must target extension modules (`*cpython-*.so`/`*.abi3.so`) and exclude bundled `lib*-<hash>.so`, or a bundled helper produces a false "not found" (Agent 1's confirmed failure mode). Covered by Task 1 tests.
- **Per-package round-trips** — batch `ldd` per package (one command listing all its `.so`s) to bound `docker exec` overhead; scipy's ~80 `.so` ldd in <0.4s, so cost is negligible.
- **Seed over-predict** — a seed prediction ldd doesn't confirm is left for `certify` (could yield a rare unneeded apt). Acceptable; pruning is a later refinement.
- **Option-A name gap (scoped, intentional)** — `ldd_probe` reuses `resolve_soname_apt` (table-first; apt-file fallback is **absent on slim images**). So an *unknown* soname (not in `NATIVE_LIB_TO_APT`) yields a node that surfaces the *need* with **empty `fix_candidates`** — table-independent *knowledge*, but not a release-correct apt *name*. For known sonames, the chain `resolve_soname_apt` (table name) → `reconcile_apt_names`/`showpkg` (release-correct remap) gives the right name. Closing the unknown-soname name gap is **option B** (below). This is a documented limitation, not a regression — current behavior already produces no apt name for unknown sonames.
- **apt-file cost is NOT incurred by default** — because apt-file is absent on slim images, the existing fallback in `resolve_soname_apt` simply returns "unresolved" rather than paying the ~26s/50MB index. (That cost only appears under option B, gated.)

## Future TODOs (option B — deferred; closes the unknown-soname name gap)
Pursue when an A/B (or real-repo coverage) shows that **novel** native packages (sonames absent from `NATIVE_LIB_TO_APT`) matter enough to justify the cost:
- [ ] **Lazy apt-file resolution**: when `resolve_soname_apt` misses the table AND apt-file is absent, install + index apt-file **once per build, on first unknown soname only** (cache the "set up" flag), then resolve. Bounds the ~26s/50MB to builds that actually hit an unknown lib. (Agent 2 "Option B".)
- [ ] **conda-forge proactive KB** (separate effort): map PyPI→conda→system-deps as a *proactive* predictor for the install-fails / pre-install window, gated by a wheel-availability filter to avoid over-prediction. (Agent 4 verdict: scoped fallback only.)
- [ ] **PEP 725 `[external]`** (revisit on adoption): parse author-declared system deps as the highest-priority knowledge source once real packages ship it (currently ~zero). (Agent 3 verdict: future-proof only.)
- [ ] **Seed-prediction pruning**: drop unconfirmed seed predictions after a successful ldd pass to remove the rare over-predict.

## One-line summary
Add a post-install `ldd_probe` stage that reads each installed binary's own `NEEDED`-but-`not found` libraries (ground-truth run-time native deps, any package, no table) and upgrade apt-name resolution to ask the target image's apt DB (`showpkg`) for release-correct names — demoting the hand-curated table from authority to proactive fallback. Scope (option A): table-independent *knowledge* for all packages + release-correct *names* for known sonames; unknown-soname names are option B (lazy apt-file).
