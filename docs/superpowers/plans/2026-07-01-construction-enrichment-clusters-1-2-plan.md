# Construction Enrichment — Clusters 1 + 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the requirement graph richer and better-grounded from a repo, without ever asserting a native obligation the graph cannot trace to an authoritative source. Cluster 1 replaces the curated `PACKAGE_TO_SYSTEM_DEPS` table with two *derived* priors — the resolver's own wheel-vs-sdist signal (1a) and the repo's own apt declarations in Dockerfile/Aptfile/binder/CI (1b). Cluster 2 widens the construction-time LLM classifier to read raw prose files and propose soft `SystemLib`/`Tool` hints that actually install (staying SOFT) instead of silently doing nothing.

**Architecture:** `build_dep_graph` (`build.py`) stays a legible, ordered sequence of pure `graph → graph` stages. Stage 3's wheel-vs-sdist logic is extracted into its own module (behavior preserved exactly); stage 3b is renamed and re-derived from the resolver's own signal instead of a curated table; a new stage 3c mines the repo's own apt declarations into `STATIC_DECLARATION` nodes. `env_classifier.py` (the sole LLM bridge, structurally outside `build_dep_graph`) gains raw-file evidence, a wider proposable-type allowlist, and a small deterministic post-sanitize step that makes an admitted soft node actually installable without hardening it. `patch_gate.apply_proposal` stamps every LLM-admitted node with a new, dedicated discovery origin — `DiscoveredBy.CLASSIFIER` — so it is attributable and never confused with a real container probe. Node `strength` continues to be decided by `populate_setup_commands` (uniform-graph Phase 1), which gains one narrow rule keyed on that origin so a classifier-admitted node stays SOFT.

**Tech Stack:** Python 3 (frozen dataclasses), pytest, PyYAML (already a soft dependency via `service_scan.py`). Pure `python_deps/depgraph` code for clusters 1a/1b — no Docker, no network, no LLM. Cluster 2's new/changed code lives in `src/envstate/env_classifier.py` (the sole LLM bridge) plus four LLM-adjacent-but-still-pure `python_deps/depgraph` modules: `raw_intake.py` and `static_collect.py` (evidence assembly), `patch_gate.py` (admission), `populate.py` (strength/recipe) — none of the four ever calls the LLM.

## Global Constraints

- **Target branch / worktree:** `/Users/john/john-planner-v3-core` (branch `v3-core`). All commands and commits run there.
- **`python_deps/depgraph` stays LLM-free.** `env_classifier.py` is the only LLM bridge, structurally outside `build_dep_graph`. The two new deterministic stages (`wheel_oracle.py`, `declaration_mine.py`) are pure `graph → graph`, container-free.
- **Host (`certify.py`) is the sole writer of `SATISFIED`.** The LLM proposes SOFT only — this holds structurally end-to-end: `_sanitize` forces every LLM-proposed edge `hard=False`, and (Task 11/12) an LLM-admitted node — identified by the new `DiscoveredBy.CLASSIFIER` origin `patch_gate.apply_proposal` stamps it with (Task 10) — stays `Strength.SOFT` even after it is made installable.
- **No curated package→syslib table remains.** `tables.PACKAGE_TO_SYSTEM_DEPS`, `system_deps_for_package`, and `_NORMALIZED_PACKAGE_SYSTEM_DEPS` are deleted entirely (Task 2). Every pre-install native node traces to a uv.lock artifact (`build_from_source`) or a repo `file:line` (declaration mining). `tables.py` keeps `NATIVE_LIB_TO_APT` / `TOOL_TO_APT` / `NATIVE_RISK_PACKAGES` — those map an *already-observed* soname/tool to its apt package (resolution of an observation), not prediction from nothing.
- **Immutability.** Every node "mutation" returns a NEW object via `dataclasses.replace`; every stage returns a NEW `DepGraph` via `with_node`/`with_edge`. `Node.data` is a frozen `MappingProxyType`.
- **Pure `graph → graph` stages.** `wheel_oracle.risk_from_packages`, `seed_wheel_oracle_prior`, `mine_declarations` take/return only stdlib types + `DepGraph`/`Node`/`Edge` — no Docker, no network, no LLM.
- **`build.py` stays a legible ordered pipeline.** One stage renamed (3b), one stage added (3c), in the existing pure-function-per-stage pattern; no god-files.
- **No new dependencies.** PyYAML is already an optional/soft dependency (`service_scan.py` already does `try: import yaml except ImportError: yaml = None`); the new YAML use in `declaration_mine.py`/`static_collect.py` follows the identical degrade-gracefully pattern.
- **Run tests** with the system interpreter (no Docker): `python3 -m pytest tests/depgraph/ -q`. Measured baseline **577 passed** (this is the "Phase 1" populator/renderer baseline this plan builds on — see `docs/superpowers/plans/2026-07-01-uniform-graph-phase1-populator-renderer.md`). Full-suite baseline (`python3 -m pytest -q`): **1288 passed, 32 skipped**, plus **2 pre-existing failures** in `tests/test_repo2run_dataset.py` (PDF-dataset extraction, unrelated to this work — do not try to fix them). Every task in this plan must leave `tests/depgraph/ -q` at the (growing) task-local passing count with **zero regressions**, and must not newly break the full-suite count beyond the 2 known pre-existing failures. **The per-task expected counts stated throughout this plan are indicative, hand-derived by inspection at plan-writing time — the BINDING gate at every task boundary is "no failures, and no regression versus the prior task's green suite," not exact parity with a hand-computed number.** If an actual run's count differs from the number stated in a step, treat that as a signal to go investigate *why* (a miscounted assumption in this plan vs. an actual regression), not as a plan violation to paper over.
- **The table deletion breaks existing tests beyond `test_tables.py`/`test_seed.py`.** Investigation found the ripple is wider than those two files: `tests/depgraph/test_build.py` has a full end-to-end fixture whose docstring and six assertions are written against `PACKAGE_TO_SYSTEM_DEPS`-predicted `syslib:libgl1`/`tool:libpq-dev` reconciliation, and `tests/depgraph/test_ldd_probe_docker.py` imports `PACKAGE_TO_SYSTEM_DEPS` directly (a dead import once deleted, which fails test *collection* even though the test itself is Docker-gated and normally skipped). Task 2 updates all of these explicitly; do not treat "run the tests" as sufficient without reading `test_build.py`'s new assertions in Task 2 carefully — the fixture's *expected node ids change* (soname-keyed PROBE nodes instead of apt-keyed RESOLVER nodes) because there is no longer a prediction to reconcile into. **The net effect of this whole ripple, counted precisely, is `test_tables.py`'s five deleted assertions with no replacement (−5) — `test_seed.py`'s rewrite, `test_build.py`'s two function replacements, and `test_ldd_probe_docker.py`'s import fix are all 1-for-1/0-for-0 swaps that net to zero; see Task 2 Step 12.**

---

## File Structure

- `src/python_deps/depgraph/wheel_oracle.py` — **create**: `risk_from_packages`, `_wheel_matches_platform`, `_artifact_filename` (extracted from `resolve_lock.py`; self-contained, zero dependency on `resolve_lock.py` to avoid a circular import, and with NO concept of "local source" at all — that filtering stays `resolve_lock.py`'s job, done with its own already-existing `_is_local_source` before it ever calls in here — see Task 1).
- `src/python_deps/depgraph/resolve_lock.py` — **modify**: delete the moved functions' bodies; import + re-export `_artifact_filename`/`_wheel_matches_platform` from `wheel_oracle.py`; keep a thin `native_risk_from_lock` wrapper (unchanged public signature/behavior) that filters local-source entries with its own, already-existing `_is_local_source` (unchanged, still shared with `parse_uv_lock`) and then delegates the per-package decision to `wheel_oracle.risk_from_packages` — a real size reduction, ~469→~404 lines.
- `src/python_deps/depgraph/seed.py` — **modify**: rename `seed_predicted_native` → `seed_wheel_oracle_prior`; delete the table-lookup path; emit one `tool:build-essential` node per `build_from_source=True` package.
- `src/python_deps/depgraph/tables.py` — **modify**: delete `PACKAGE_TO_SYSTEM_DEPS`, `_NORMALIZED_PACKAGE_SYSTEM_DEPS`, `system_deps_for_package`; drop the now-unused `normalize_package_name` import.
- `src/python_deps/depgraph/schema.py` — **modify**: add `DiscoveredBy.STATIC_DECLARATION` and `DiscoveredBy.CLASSIFIER`.
- `src/python_deps/depgraph/declaration_mine.py` — **create**: `mine_declarations(graph, repo_path) -> DepGraph` (stage 3c) + `apt_hits_for_repo(repo_path)` (the shared parser `static_collect.py` also reuses) + a PRIVATE `_is_toolchain_apt(name) -> bool` helper (folded in here rather than added to `ids.py` — this module is its sole consumer; `ids.py` stays untouched, pure `X_id(name)` constructors only — see Task 4's Design note).
- `src/python_deps/depgraph/build.py` — **modify**: rename the stage-3b call; insert the stage-3c call; update the module docstring.
- `src/python_deps/depgraph/probe.py` — **modify**: widen `reconcile_predicted`'s guard from `RESOLVER` to `{RESOLVER, STATIC_DECLARATION}` (the Critical fix; `ldd_probe.py` imports this same function, so one change covers both call sites).
- `src/python_deps/depgraph/raw_intake.py` — **create**: `collect_raw_file_snippets(repo_path)` (+ private `_raw_candidate_paths`/`_install_relevant_region` helpers) — bounded raw-prose evidence (README/INSTALL/Makefile/setup.cfg/docs). Its OWN module, not bolted into `static_collect.py`: that file's stated role is a thin adapter that RESHAPES existing scanner output, not a blind repo scanner — reading raw files is a distinct concern, symmetric with `declaration_mine.py` (which also reads the repo directly).
- `src/python_deps/depgraph/static_collect.py` — **modify**: add `collect_declaration_context_hits` (`decl_apt` + `conda_declaration` evidence, reusing `declaration_mine.apt_hits_for_repo`) — raw-file scanning stays OUT of this file (that's `raw_intake.py`'s job); docstring updated.
- `src/python_deps/depgraph/patch_gate.py` — **modify**: `apply_proposal` stamps every newly-admitted requirement node `discovered_by=DiscoveredBy.CLASSIFIER` (was `DiscoveredBy.PROBE`) — the durable, attributable signal Tasks 11/12 key off downstream.
- `src/python_deps/depgraph/populate.py` — **modify**: `populate_setup_commands` keeps a classifier-admitted node (`discovered_by=DiscoveredBy.CLASSIFIER`) at its current `strength` instead of forcing `HARD`.
- `src/envstate/env_classifier.py` — **modify**: widen `_SYSTEM_PROMPT` to allow `SystemLib`/`Tool` proposals; wire `raw_intake.collect_raw_file_snippets` hits into the evidence bundle `classify()` assembles; add `normalize_emittability(graph, admitted_ids)`, called from `classify()` right after `admit_proposal`.
- `src/python_deps/depgraph/config_tables.py` — **modify**: one-line docstring reference fix (no longer points at a deleted symbol).
- Tests — **create**: `tests/depgraph/test_wheel_oracle.py`, `tests/depgraph/test_declaration_mine.py`, `tests/depgraph/test_raw_intake.py`. **modify**: `tests/depgraph/test_tables.py`, `tests/depgraph/test_seed.py`, `tests/depgraph/test_build.py`, `tests/depgraph/test_ldd_probe_docker.py`, `tests/depgraph/test_schema.py`, `tests/depgraph/test_probe.py`, `tests/depgraph/test_static_collect_bundle.py`, `tests/depgraph/test_patch_gate_apply.py`, `tests/depgraph/test_populate_setup_commands.py`, `tests/test_env_classifier.py`.
- `src/python_deps/depgraph/ids.py` / `tests/depgraph/test_ids.py` — **untouched by this plan.** (An earlier draft gave `is_toolchain_apt` a public home here; folded into `declaration_mine.py` instead — see Task 4's Design note.)

---

### Task 1: Extract `wheel_oracle.py` (pure refactor)

**Files:**
- Create: `src/python_deps/depgraph/wheel_oracle.py`
- Modify: `src/python_deps/depgraph/resolve_lock.py`
- Test: create `tests/depgraph/test_wheel_oracle.py`

**Interfaces:**
- Produces: `wheel_oracle._artifact_filename(artifact: dict) -> str | None`; `wheel_oracle._wheel_matches_platform(filename: str | None, target_platform: str) -> bool`; `wheel_oracle.risk_from_packages(raw_packages: list[dict], target_platform: str) -> dict[str, dict]` (the per-package wheel-vs-sdist decision, extracted from the body of the old `native_risk_from_lock`). `raw_packages` is assumed **already fork-resolved AND already local-source-filtered** by the caller — this module has NO concept of "local source" at all (see Design note below).
- Consumes (resolve_lock.py): imports the three names above from `wheel_oracle.py`. `resolve_lock.native_risk_from_lock(text, target_platform, target_python=None)` becomes a thin orchestrator: TOML-parse + `_select_applicable_packages` (unchanged, stays in `resolve_lock.py` — `parse_uv_lock` also needs it) + filter out local-source entries with its own, already-existing `_is_local_source` (unchanged, defined earlier in the same file, also used by `parse_uv_lock`) + delegate to `risk_from_packages`. Public signature and behavior are byte-identical to today: `native_risk_from_lock` already filtered local-source entries before this refactor (via the same `_is_local_source` call, just interleaved inside the old function's loop); the refactor only moves *where* that filter runs relative to the wheel/sdist decision (filter-then-decide instead of filter-while-deciding), not whether it runs — filtering first and then feeding the surviving entries to `risk_from_packages` produces an identical surviving set, in the same order, so the output dict is byte-identical.
- `resolve.py:74`'s existing `from python_deps.depgraph.resolve_lock import (..., _artifact_filename, _wheel_matches_platform, native_risk_from_lock, ...)` needs **zero changes** — all three names stay importable from `resolve_lock`.

**Design note (resolving an ambiguity the design doc left at the prose level):** the design says "move `native_risk_from_lock` ... out of `resolve_lock.py`". Taken completely literally this creates a circular import: the moved `native_risk_from_lock` needs `_select_applicable_packages` (a fork-resolution helper that must stay in `resolve_lock.py` because `parse_uv_lock` also needs it), so `wheel_oracle.py` would need to import from `resolve_lock.py`, while `resolve_lock.py` needs to import back from `wheel_oracle.py` to re-export at `resolve.py:74`'s expected name. This plan resolves it with a **one-directional split**: `wheel_oracle.py` is fully self-contained (only depends on the raw `list[dict]` of already-fork-resolved, already-local-source-filtered TOML package entries, which the caller supplies), and `resolve_lock.py` keeps a thin `native_risk_from_lock` wrapper. Unlike an earlier draft of this plan, `_is_local_source`/`_LOCAL_SOURCE_KEYS` is **NOT duplicated** into `wheel_oracle.py` — `resolve_lock.py`'s wrapper filters local-source entries with its OWN, already-existing `_is_local_source` (defined at the top of `resolve_lock.py`, unchanged) *before* calling `wheel_oracle.risk_from_packages`, so the new module never needs the concept of "local source" at all. This is both cheaper than duplicating a 3-line predicate into a second module AND avoids the "two copies can silently drift" smell a duplicate would introduce. This extraction *names the concept* ("where is wheel/sdist decided" now has one answer) and modestly reduces the file (~469→~404 lines).

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_wheel_oracle.py`:

```python
from python_deps.depgraph.wheel_oracle import (
    _artifact_filename,
    _wheel_matches_platform,
    risk_from_packages,
)

LINUX_X86 = "x86_64-manylinux_2_28"


def test_artifact_filename_prefers_explicit_filename():
    assert _artifact_filename({"filename": "foo-1.0.tar.gz"}) == "foo-1.0.tar.gz"


def test_artifact_filename_derives_from_url():
    assert _artifact_filename({"url": "https://x/foo-1.0-py3-none-any.whl"}) == "foo-1.0-py3-none-any.whl"


def test_artifact_filename_none_for_non_dict():
    assert _artifact_filename(None) is None


def test_wheel_matches_platform_universal_wheel():
    assert _wheel_matches_platform("foo-1.0-py3-none-any.whl", LINUX_X86) is True


def test_wheel_matches_platform_arch_mismatch():
    assert _wheel_matches_platform("foo-1.0-cp311-cp311-macosx_11_0_arm64.whl", LINUX_X86) is False


def test_wheel_matches_platform_sdist_never_matches():
    assert _wheel_matches_platform("foo-1.0.tar.gz", LINUX_X86) is False


def test_risk_from_packages_build_from_source_when_no_matching_wheel():
    raw = [{
        "name": "psycopg2",
        "sdist": {"filename": "psycopg2-2.9.9.tar.gz", "hash": "sha256:abc"},
        "wheels": [{"filename": "psycopg2-2.9.9-cp311-cp311-macosx_11_0_arm64.whl"}],
    }]
    risk = risk_from_packages(raw, LINUX_X86)
    assert risk["psycopg2"]["build_from_source"] is True
    assert risk["psycopg2"]["artifact"] == "psycopg2-2.9.9.tar.gz"
    assert risk["psycopg2"]["hash"] == "sha256:abc"


def test_risk_from_packages_prefers_matching_wheel():
    raw = [{
        "name": "requests",
        "sdist": {"filename": "requests-2.31.0.tar.gz"},
        "wheels": [{"filename": "requests-2.31.0-py3-none-any.whl", "hash": "sha256:xyz"}],
    }]
    risk = risk_from_packages(raw, LINUX_X86)
    assert risk["requests"]["build_from_source"] is False
    assert risk["requests"]["artifact"] == "requests-2.31.0-py3-none-any.whl"


def test_risk_from_packages_skips_unnamed_entries():
    assert risk_from_packages([{"source": {}}], LINUX_X86) == {}
```

(Note: this test file has NO `test_risk_from_packages_skips_local_source` case — `risk_from_packages` has no concept of "local source" any more; that filtering is `resolve_lock.native_risk_from_lock`'s job, already proven byte-identical by Step 6's untouched `test_resolve.py` run below.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_wheel_oracle.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.wheel_oracle'`.

- [ ] **Step 3: Create `wheel_oracle.py`**

Create `src/python_deps/depgraph/wheel_oracle.py`:

```python
"""Wheel-vs-sdist build-from-source oracle (construction-enrichment cluster 1a).

Extracted from ``resolve_lock.py`` as a pure refactor — behavior preserved
exactly, including the known latent bug that platform markers are evaluated
against the HOST, not the target image (see ``_wheel_matches_platform`` and the
design doc's "Adjacent known issues"; fixing that is a separate correctness
track). Names the concept: "where is wheel/sdist decided" now has one answer.

Self-contained (stdlib only) so ``resolve_lock.py -> wheel_oracle.py`` is the
only import edge (no cycle). This module has NO concept of "local source" (the
synthetic resolve root, or a path/editable dependency) at all — ``resolve_lock.py``
keeps a thin ``native_risk_from_lock`` wrapper that does the uv.lock TOML parse,
the target-python fork selection (shared with ``parse_uv_lock``, so it must
stay there), AND filters out local-source entries with its OWN, already-existing
``_is_local_source`` BEFORE calling :func:`risk_from_packages` here — so the
concept is not duplicated into a second module.
"""

from __future__ import annotations


def _artifact_filename(artifact: dict) -> str | None:
    """Filename of an sdist/wheel lock entry (explicit, or derived from url)."""
    if not isinstance(artifact, dict):
        return None
    name = artifact.get("filename")
    if name:
        return name
    url = artifact.get("url")
    if url:
        return url.rsplit("/", 1)[-1]
    return None


def _wheel_matches_platform(filename: str | None, target_platform: str) -> bool:
    """True when ``filename`` is installable on the (linux) ``target_platform``.

    Universal wheels (``...-none-any.whl``) match every platform.  Otherwise the
    target's arch token (e.g. ``x86_64`` / ``aarch64``) must appear in a *linux*
    platform tag; macOS/Windows wheels never match a linux target.

    KNOWN ISSUE (preserved, not fixed here): ``target_platform`` describes the
    intended CONTAINER target, but nothing here evaluates it against the HOST
    the resolve runs on — a cross-arch resolve can misjudge which wheel is
    installable. See the design doc's "Adjacent known issues"; fixing this is a
    separate correctness track, not this enrichment.
    """
    if not filename:
        return False
    low = filename.lower()
    if not low.endswith(".whl"):
        return False
    if low.endswith("-none-any.whl"):
        return True
    arch = (target_platform.split("-", 1)[0] if target_platform else "").lower()
    if not arch:
        return False
    if "linux" not in low:  # the target is linux; skip macosx_/win_ wheels.
        return False
    return arch in low


def risk_from_packages(raw_packages: list[dict], target_platform: str) -> dict[str, dict]:
    """Map ``package name -> {build_from_source, artifact, hash}`` for already
    fork-resolved, already LOCAL-SOURCE-FILTERED ``[[package]]`` TOML entries
    (one entry per name — the caller, ``resolve_lock.native_risk_from_lock``,
    is responsible for BOTH selecting the target-python-applicable entry when a
    lock forks a package across resolution markers, AND filtering out local-
    source entries with its own ``_is_local_source`` before ever calling this
    function; this function only decides wheel-vs-sdist per entry and has no
    concept of "local source" at all).

    A package that ships an ``sdist`` but no wheel matching ``target_platform``
    must be built from source on the target.  The chosen artifact is the
    matching wheel when one exists, else the sdist.
    """
    risk: dict[str, dict] = {}
    for pkg in raw_packages:
        name = pkg.get("name")
        if not name:
            continue
        sdist = pkg.get("sdist")
        wheels = pkg.get("wheels", []) or []

        matching_wheel = next(
            (
                w
                for w in wheels
                if _wheel_matches_platform(_artifact_filename(w), target_platform)
            ),
            None,
        )
        has_sdist = isinstance(sdist, dict) and bool(sdist)
        build_from_source = has_sdist and matching_wheel is None

        if matching_wheel is not None:
            chosen = matching_wheel
        elif has_sdist:
            chosen = sdist
        elif wheels:
            chosen = wheels[0]
        else:
            chosen = None

        risk[name] = {
            "build_from_source": build_from_source,
            "artifact": _artifact_filename(chosen) if chosen else None,
            "hash": chosen.get("hash") if isinstance(chosen, dict) else None,
        }
    return risk
```

- [ ] **Step 4: Delete the moved section from `resolve_lock.py` and add the thin wrapper**

In `src/python_deps/depgraph/resolve_lock.py`, delete the entire section from the `# --- Pure parser 2: per-package native-build risk ... ---` comment (the block containing `_artifact_filename`, `_wheel_matches_platform`, `native_risk_from_lock`) through the end of the file, and replace it with:

```python
# --------------------------------------------------------------------------- #
# Pure parser 2: per-package native-build risk — delegates to wheel_oracle.py.
# --------------------------------------------------------------------------- #
from python_deps.depgraph.wheel_oracle import (  # noqa: E402
    _artifact_filename,
    _wheel_matches_platform,
    risk_from_packages,
)


def native_risk_from_lock(
    text: str,
    target_platform: str,
    target_python: str | None = None,
) -> dict[str, dict]:
    """Map ``package name -> {build_from_source, artifact, hash}`` from a lock.

    Thin orchestrator: parses the TOML, resolves fork duplicates the same way
    :func:`parse_uv_lock` does (so a forked package's risk reflects the version
    actually installed on the target), filters out local-source entries with
    this module's OWN ``_is_local_source`` (the wheel/sdist decision in
    ``wheel_oracle.py`` has no concept of "local source" at all — the concept
    is not duplicated into that module), then delegates the per-package
    wheel-vs-sdist decision to :func:`wheel_oracle.risk_from_packages`.
    """
    data = tomllib.loads(text)
    raw_packages = _select_applicable_packages(
        data.get("package", []), target_python
    )
    raw_packages = [
        p for p in raw_packages
        if p.get("name") and not _is_local_source(p.get("source", {}))
    ]
    return risk_from_packages(raw_packages, target_platform)
```

(The `# noqa: E402` marks the import as intentionally not at the top of the file — it comes after `_select_applicable_packages` is defined, which is fine since it is a fresh top-level import of an unrelated module, not a forward reference within this file. `_is_local_source` is NOT redefined here — it is the same function already defined near the top of `resolve_lock.py`, used unchanged by `parse_uv_lock`.)

- [ ] **Step 5: Run the new test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_wheel_oracle.py -q`
Expected: PASS (8 passed).

- [ ] **Step 6: Prove byte-identical behavior — run the existing resolve tests unchanged**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_resolve.py -q`
Expected: PASS at the existing count, with **zero edits to `test_resolve.py`** — it imports `native_risk_from_lock` from `python_deps.depgraph.resolve`, which re-exports from `resolve_lock`, which now delegates internally (including the local-source filter, which ran inside the old function's loop and now runs just before the call — same surviving set, same order, same output). If any test fails here, the extraction broke behavior — stop and fix `wheel_oracle.py`/the thin wrapper before proceeding.

- [ ] **Step 7: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS, 577 + 8 = 585 passed.

- [ ] **Step 8: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/wheel_oracle.py src/python_deps/depgraph/resolve_lock.py tests/depgraph/test_wheel_oracle.py
git commit -m "refactor(depgraph): extract wheel_oracle.py from resolve_lock.py (pure refactor, behavior preserved)"
```

---

### Task 2: `seed_wheel_oracle_prior` — delete the curated table

**Files:**
- Modify: `src/python_deps/depgraph/seed.py` (full rewrite)
- Modify: `src/python_deps/depgraph/tables.py` (delete table + dead helpers)
- Modify: `src/python_deps/depgraph/config_tables.py` (one-line docstring reference fix)
- Modify: `tests/depgraph/test_tables.py`, `tests/depgraph/test_seed.py`, `tests/depgraph/test_build.py`, `tests/depgraph/test_ldd_probe_docker.py`

**Interfaces:**
- Produces: `seed_wheel_oracle_prior(graph: DepGraph) -> DepGraph` — replaces `seed_predicted_native`. For every `Package` with `build_from_source=True`, ensures ONE deduped `tool:build-essential` node (`discovered_by=RESOLVER`, `state=UNKNOWN`, `chosen_fix="apt:build-essential"`) exists and adds a `requires` edge from that package to it.
- Removes: `tables.PACKAGE_TO_SYSTEM_DEPS`, `tables.system_deps_for_package`, `tables._NORMALIZED_PACKAGE_SYSTEM_DEPS`, and `seed.py`'s old `_predicted_apts`/`_predicted_node`/`_is_toolchain_apt`/`_GENERIC_TOOLCHAIN_APT`/`_TOOLCHAIN_APT` (these are OLD, now-deleted internals of `seed.py` itself — unrelated to `declaration_mine.py`'s private `_is_toolchain_apt`, added later in Task 4).
- `build.py` still imports `seed_predicted_native` at this point in the sequence — **this task intentionally leaves `build.py` broken until Task 5** is out of scope for a single task's "leave the suite green" rule, so this task ALSO updates the one `build.py` line that names the function (a one-line rename, not a wiring change — Task 5 handles the stage-3c insertion). See Step 5 below.

**Design note (a real, non-obvious ripple this task must fix — found by inspection, not stated in the design doc):** `tests/depgraph/test_build.py` is a full pipeline fixture whose docstring and six assertions assume `PACKAGE_TO_SYSTEM_DEPS` predicts `syslib:libgl1` (for `opencv-python`) and `tool:libpq-dev` (for `psycopg2`), so the probe-observed `libGL.so.1`/`pg_config` gaps reconcile into those apt-keyed RESOLVER nodes. The fixture's canned closure is `uv pip compile`-style text (not real `uv.lock` TOML), so it carries no `build_from_source` signal — `seed_wheel_oracle_prior` predicts **nothing** for this fixture. Without a table and without a `build_from_source` signal, the observed gaps now surface as **fresh, soname/tool-name-keyed `discovered_by=PROBE` nodes** instead of reconciling. This is the documented, expected coverage tradeoff (design doc Risk #2) — this task updates the fixture's assertions to match it explicitly (not paper over it). `tests/depgraph/test_ldd_probe_docker.py` also directly imports `PACKAGE_TO_SYSTEM_DEPS`, which becomes a dead import (breaks test *collection*, independent of the Docker skip-marker) — this task fixes that too.

- [ ] **Step 1: Write the failing test — rewrite `test_seed.py`**

Replace `tests/depgraph/test_seed.py` in full:

```python
"""Wheel-oracle prior seeding (``seed.py``, construction-enrichment cluster 1a).

Replaces the old curated-table prediction: the ONLY signal is the resolver's
own ``build_from_source`` flag. A from-source package predicts a generic
compiler toolchain (``tool:build-essential``); it does NOT predict specific
``-dev`` headers (that used to come from ``PACKAGE_TO_SYSTEM_DEPS``, now
deleted — see the design doc's "What this loses, honestly").
"""

from __future__ import annotations

from python_deps.depgraph.ids import package_id, tool_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)
from python_deps.depgraph.seed import seed_wheel_oracle_prior


def _package(name: str, version: str, *, build_from_source=None) -> Node:
    return Node(
        id=package_id(name, version),
        type=NodeType.PACKAGE,
        name=name,
        layer=Layer.PIP,
        discovered_by=DiscoveredBy.RESOLVER,
        version=version,
        check_command=f"python -m pip show {name}",
        build_from_source=build_from_source,
    )


def test_seed_predicts_build_essential_for_from_source_package():
    pkg = _package("psycopg2", "2.9.9", build_from_source=True)
    graph = DepGraph().with_node(pkg)

    out = seed_wheel_oracle_prior(graph)

    tool = out.get(tool_id("build-essential"))
    assert tool is not None
    assert tool.type is NodeType.TOOL
    assert tool.layer is Layer.TOOLCHAIN
    assert tool.discovered_by is DiscoveredBy.RESOLVER
    assert tool.state is State.UNKNOWN
    assert tool.fix_candidates == ("apt:build-essential",)
    assert tool.chosen_fix == "apt:build-essential"
    deps = {d.id for d in out.requires_of(pkg.id)}
    assert tool_id("build-essential") in deps


def test_seed_no_prediction_when_build_from_source_false():
    pkg = _package("requests", "2.31.0", build_from_source=False)
    graph = DepGraph().with_node(pkg)

    out = seed_wheel_oracle_prior(graph)

    assert [n for n in out.nodes if n.type is NodeType.TOOL] == []


def test_seed_no_prediction_when_build_from_source_none():
    pkg = _package("requests", "2.31.0")  # default None (unknown)
    graph = DepGraph().with_node(pkg)

    out = seed_wheel_oracle_prior(graph)

    assert [n for n in out.nodes if n.type is NodeType.TOOL] == []


def test_seed_dedupes_build_essential_across_multiple_from_source_packages():
    a = _package("psycopg2", "2.9.9", build_from_source=True)
    b = _package("lxml", "5.2.0", build_from_source=True)
    graph = DepGraph().with_node(a).with_node(b)

    out = seed_wheel_oracle_prior(graph)

    tools = [n for n in out.nodes if n.id == tool_id("build-essential")]
    assert len(tools) == 1
    a_deps = {d.id for d in out.requires_of(a.id)}
    b_deps = {d.id for d in out.requires_of(b.id)}
    assert tool_id("build-essential") in a_deps
    assert tool_id("build-essential") in b_deps


def test_seed_predicted_edge_is_resolver_origin():
    pkg = _package("psycopg2", "2.9.9", build_from_source=True)
    graph = DepGraph().with_node(pkg)

    out = seed_wheel_oracle_prior(graph)

    edges = [
        e for e in out.edges
        if e.dst == tool_id("build-essential") and e.relation is EdgeType.REQUIRES
    ]
    assert edges and all(e.origin == "resolver" for e in edges)


def test_seed_no_op_when_no_packages_need_a_build():
    graph = DepGraph()
    out = seed_wheel_oracle_prior(graph)
    assert out.nodes == ()


def test_seed_returns_new_graph_originals_unchanged():
    pkg = _package("psycopg2", "2.9.9", build_from_source=True)
    graph = DepGraph().with_node(pkg)

    out = seed_wheel_oracle_prior(graph)

    assert out is not graph
    assert graph.get(tool_id("build-essential")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_seed.py -q`
Expected: FAIL — `ImportError: cannot import name 'seed_wheel_oracle_prior'`.

- [ ] **Step 3: Rewrite `seed.py`**

Replace `src/python_deps/depgraph/seed.py` in full:

```python
"""Wheel-oracle prior — a build-essential Tool for every from-source package.

Realizes construction-enrichment cluster 1a (design 2026-07-01): the
resolver's own wheel-vs-sdist signal (``Node.build_from_source``, computed by
``wheel_oracle.risk_from_packages``) is the ONLY basis for this prediction —
no curated package->syslib table. A package with no compatible wheel needs a
compiler to build its sdist; that is the one thing this stage predicts.

This REPLACES the deleted ``seed_predicted_native`` / ``PACKAGE_TO_SYSTEM_DEPS``
path. Specific ``-dev`` headers (psycopg2->libpq-dev, Pillow->libjpeg-dev) are
no longer predicted from a table. They are recovered by: declaration mining
(stage 3c, when the repo declares them), ``install_closure`` parsing the real
build error (stage 4), or ``ldd_probe`` for runtime libs (stage 4.5) — an
expected coverage tradeoff, see the design doc's "What this loses, honestly"
and Risk #2.

Pure: every "mutation" returns a NEW ``DepGraph`` (repo immutability rule).
"""

from __future__ import annotations

from python_deps.depgraph.ids import tool_id
from python_deps.depgraph.schema import (
    DepGraph,
    DiscoveredBy,
    Edge,
    EdgeType,
    Layer,
    Node,
    NodeType,
    State,
)

_BUILD_ESSENTIAL_APT = "build-essential"
_BUILD_ESSENTIAL_ID = tool_id(_BUILD_ESSENTIAL_APT)


def _build_essential_node() -> Node:
    fix = f"apt:{_BUILD_ESSENTIAL_APT}"
    return Node(
        id=_BUILD_ESSENTIAL_ID,
        type=NodeType.TOOL,
        name=_BUILD_ESSENTIAL_APT,
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.RESOLVER,
        state=State.UNKNOWN,
        check_command=f"dpkg -s {_BUILD_ESSENTIAL_APT}",
        fix_candidates=(fix,),
        chosen_fix=fix,
        provenance="wheel-oracle (build_from_source)",
    )


def seed_wheel_oracle_prior(graph: DepGraph) -> DepGraph:
    """Emit ONE ``tool:build-essential`` node for every from-source Package.

    For each ``Package`` with ``build_from_source=True`` (the resolver's own
    wheel-vs-sdist signal), add a ``requires`` edge to the single, deduped
    ``build-essential`` Tool node (created once, on first need). Returns a NEW
    graph; a no-op when no package needs a source build.
    """
    new = graph
    packages = [
        n for n in graph.nodes
        if n.type is NodeType.PACKAGE and n.build_from_source
    ]
    if not packages:
        return new
    if new.get(_BUILD_ESSENTIAL_ID) is None:
        new = new.with_node(_build_essential_node())
    for pkg in packages:
        new = new.with_edge(
            Edge(src=pkg.id, dst=_BUILD_ESSENTIAL_ID, relation=EdgeType.REQUIRES, origin="resolver")
        )
    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_seed.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Rename the one `build.py` reference (mechanical; full wiring is Task 5)**

In `src/python_deps/depgraph/build.py`:
- Change `from python_deps.depgraph.seed import seed_predicted_native` to `from python_deps.depgraph.seed import seed_wheel_oracle_prior`.
- Change the call `graph = seed_predicted_native(graph)` to `graph = seed_wheel_oracle_prior(graph)`.

Do not touch the surrounding comments/docstring yet — Task 5 rewrites those together with the stage-3c insertion, so this diff stays minimal and reviewable.

- [ ] **Step 6: Delete the curated table from `tables.py`**

In `src/python_deps/depgraph/tables.py`, delete:
- The `PACKAGE_TO_SYSTEM_DEPS: dict[str, list[str]] = {...}` block (with its preceding comment).
- The `_NORMALIZED_PACKAGE_SYSTEM_DEPS: dict[str, list[str]] = {...}` block.
- The `system_deps_for_package(name: str) -> list[str]:` function.
- The now-unused `from python_deps.import_mapping import normalize_package_name` import line (nothing else in `tables.py` uses it).

Update the module docstring's second paragraph (which says "Targeting Debian/Ubuntu only in V1...") to add one sentence: `"PACKAGE_TO_SYSTEM_DEPS (the curated package->syslib prediction table) was deleted 2026-07-01 — see construction-enrichment cluster 1a; predictions now derive only from the resolver's wheel/sdist signal (seed.py) or the repo's own declarations (declaration_mine.py)."`

- [ ] **Step 7: Fix the dangling docstring reference in `config_tables.py`**

In `src/python_deps/depgraph/config_tables.py`, change:
```python
"""Curated `package -> config-obligation` table (tier-6 analogue of
``tables.PACKAGE_TO_SYSTEM_DEPS``).  A distribution that, once installed, reads
```
to:
```python
"""Curated `package -> config-obligation` table (this is a config-tier table,
unrelated to native/system deps — see construction-enrichment cluster 1a for
why the analogous native-deps table was deleted).  A distribution that, once installed, reads
```

- [ ] **Step 8: Update `test_tables.py`**

In `tests/depgraph/test_tables.py`:
- Remove `PACKAGE_TO_SYSTEM_DEPS` and `system_deps_for_package` from the `from python_deps.depgraph.tables import (...)` block.
- Delete these test functions entirely: `test_system_deps_for_known_packages`, `test_system_deps_for_package_normalizes_name`, `test_system_deps_for_unknown_is_empty_list`, `test_system_deps_returns_fresh_list`, `test_package_to_system_deps_is_dict`.
- Keep everything else unchanged (`apt_for_soname`, `apt_for_tool`, `NATIVE_RISK_PACKAGES`, `NATIVE_LIB_TO_APT`/`TOOL_TO_APT` non-empty checks, the opencv soname-chain test).

This step deletes **5 test functions with NO replacement** — this is the entire source of Task 2's net `-5`.

- [ ] **Step 9: Fix the dead import in `test_ldd_probe_docker.py`**

In `tests/depgraph/test_ldd_probe_docker.py`:
- Change the import block:
```python
from python_deps.depgraph.tables import (  # noqa: E402
    NATIVE_LIB_TO_APT,
    PACKAGE_TO_SYSTEM_DEPS,
)
from python_deps.import_mapping import normalize_package_name  # noqa: E402
```
to:
```python
from python_deps.depgraph.tables import NATIVE_LIB_TO_APT  # noqa: E402
```
(`normalize_package_name` was only used by the now-removed assertion below, so its import is dropped too.)
- In `test_ldd_probe_table_independent_knowledge`, delete the entire "Assertion 1: table independence" block (lines checking `normalized_table_keys`/`PACKAGE_TO_SYSTEM_DEPS`) — the premise is now vacuously true for every package (no curated table exists at all), so it is no longer a meaningful assertion. Renumber the remaining assertions' docstring comments from "2."/"3." to "1."/"2.". Update the module docstring's paragraph 1 (`"Proves that ``ldd_probe`` can discover... not from the curated table."`) to: `"Proves that ``ldd_probe`` discovers native library gaps purely from inspecting the installed binary — there is no curated table to compare against any more (construction-enrichment cluster 1a deleted it); this test now exercises binary-inspection discovery + release-correct apt naming directly."` Remove bullet `* ``pygame`` is NOT in ``PACKAGE_TO_SYSTEM_DEPS`` (verified inline).` from the docstring's bullet list.

This step shrinks one existing test function's body (a dead-code deletion inside it); the test file's *function count* is unchanged (still 1 test function) — net `0`.

- [ ] **Step 10: Update `test_build.py` — the end-to-end fixture (the ripple this task must not skip)**

In `tests/depgraph/test_build.py`, replace the module docstring's paragraph (lines 13-17, starting `Native gaps here all reconcile with a resolver *prediction*...`) with:

```
Native gaps here surface as fresh ``discovered_by=PROBE`` nodes (soname/tool-
name keyed): this fixture's closure is ``uv pip compile``-style text (no
``build_from_source`` signal), so ``seed_wheel_oracle_prior`` (construction-
enrichment cluster 1a; the curated package->syslib table is gone) predicts
nothing for opencv-python/psycopg2, and this fixture declares no apt deps
either (cluster 1b). See ``test_build_native_gaps_are_fresh_probe_nodes_without_a_prior``
for the documented coverage tradeoff.
```

Replace the `syslib_id`/`tool_id` assertions in `test_build_produces_all_node_types`:
```python
    # SystemLib + Tool — the observed gaps reconcile into the apt-keyed
    # predictions (no duplicate soname/tool node is created).
    assert graph.get(syslib_id("libgl1")) is not None
    assert graph.get(tool_id("libpq-dev")) is not None
    assert graph.get(syslib_id("libGL.so.1")) is None
    assert graph.get(tool_id("pg_config")) is None
```
with:
```python
    # SystemLib + Tool — no RESOLVER prediction to reconcile into (no curated
    # table, no build_from_source signal in this fixture), so the observed
    # gaps surface as FRESH PROBE nodes, keyed by soname/tool name.
    assert graph.get(syslib_id("libGL.so.1")) is not None
    assert graph.get(tool_id("pg_config")) is not None
    assert graph.get(syslib_id("libgl1")) is None
    assert graph.get(tool_id("libpq-dev")) is None
```

Replace the edge assertions in `test_build_requires_topology`:
```python
    # Package -> SystemLib / Tool (predicted at resolve, reconciled by probe)
    assert (
        package_id("opencv-python", "4.9.0.80"),
        syslib_id("libgl1"),
    ) in edges
    assert (package_id("psycopg2", "2.9.9"), tool_id("libpq-dev")) in edges
```
with:
```python
    # Package -> SystemLib / Tool (fresh PROBE discovery, no prior to reconcile into)
    assert (
        package_id("opencv-python", "4.9.0.80"),
        syslib_id("libGL.so.1"),
    ) in edges
    assert (package_id("psycopg2", "2.9.9"), tool_id("pg_config")) in edges
```

Replace the two lines in `test_build_certified_states`:
```python
    assert graph.get(syslib_id("libgl1")).state is State.MISSING
    assert graph.get(tool_id("libpq-dev")).state is State.MISSING
```
with:
```python
    assert graph.get(syslib_id("libGL.so.1")).state is State.MISSING
    assert graph.get(tool_id("pg_config")).state is State.MISSING
```

Replace the whole `test_build_reconciled_predictions_keep_resolver_origin` function with:
```python
def test_build_native_gaps_are_fresh_probe_nodes_without_a_prior(tmp_path):
    """No curated table and no build_from_source signal in this fixture ->
    seed_wheel_oracle_prior predicts nothing, so the observed native gaps have
    no RESOLVER prediction to reconcile into and surface as fresh
    discovered_by=PROBE nodes (the design's documented coverage tradeoff —
    Risk #2 in the construction-enrichment design)."""
    graph = _build(tmp_path)

    libgl = graph.get(syslib_id("libGL.so.1"))
    pg_config = graph.get(tool_id("pg_config"))
    assert libgl.discovered_by is DiscoveredBy.PROBE
    assert pg_config.discovered_by is DiscoveredBy.PROBE
    assert libgl.check_command == "ldconfig -p | grep libGL.so.1"
    assert pg_config.check_command == "command -v pg_config"
    assert libgl.fix_candidates == ("apt:libgl1",)        # NATIVE_LIB_TO_APT still resolves it
    assert pg_config.fix_candidates == ("apt:libpq-dev",)  # TOOL_TO_APT still resolves it
    assert any(a.outcome == "failed" for a in libgl.attempts)
```

Replace the cycle assertions in `test_build_discovered_cycle_per_stage`:
```python
    # predicted-then-reconciled nodes keep the resolver discovery cycle (2)
    assert graph.get(syslib_id("libgl1")).discovered_cycle == 2
    assert graph.get(tool_id("libpq-dev")).discovered_cycle == 2
```
with:
```python
    # no RESOLVER prediction to reconcile into -> fresh PROBE-cycle (3) nodes.
    assert graph.get(syslib_id("libGL.so.1")).discovered_cycle == 3
    assert graph.get(tool_id("pg_config")).discovered_cycle == 3
```

Replace the whole `test_build_ldd_probe_reconciles_seed_prediction` function (and its docstring) with:
```python
def test_build_ldd_probe_creates_fresh_node_without_a_prior(tmp_path):
    """Stage 4.5 is LIVE in the pipeline: when install AND import both succeed,
    ldd_probe discovers ``libGL.so.1`` from the installed binary. This
    fixture's closure carries no ``build_from_source`` signal, so
    ``seed_wheel_oracle_prior`` predicts nothing here and there is no RESOLVER
    node to reconcile into — ldd_probe creates a FRESH ``discovered_by=PROBE``
    node (soname-keyed id, apt-resolved fix-candidate via NATIVE_LIB_TO_APT).
    Declaration mining (Task 6's ``test_build_declared_syslib_survives_probe_reconciliation``)
    is the mechanism that restores reconciliation for a repo that DOES declare
    its apt deps."""
    import json

    from conftest import FakeExecutor  # type: ignore

    (tmp_path / "app.py").write_text("import cv2\n")
    ex = FakeExecutor(
        responses={
            "uv pip compile": _r(stdout=_LDD_CLOSURE),
            "pip install": _r(returncode=0),  # install SUCCEEDS
            "locate_file": _r(stdout=json.dumps({"opencv-python": [_CV2_SO_BUILD]})),
            "ldd ": _r(stdout=f"{_CV2_SO_BUILD}:\n\tlibGL.so.1 => not found\n"),
            "apt-cache show libgl1": _r(stdout="Package: libgl1\n"),
        },
        default=_r(returncode=0),
    )

    graph = build_dep_graph(
        str(tmp_path), ex, host_executor=ex, exclude_newer="2024-01-01"
    )

    assert any(c.startswith("ldd ") for c in ex.calls)

    libgl = graph.get(syslib_id("libGL.so.1"))
    assert libgl is not None
    assert libgl.discovered_by is DiscoveredBy.PROBE  # no prior to reconcile into
    assert graph.get(syslib_id("libgl1")) is None       # no apt-keyed prediction exists
    assert libgl.check_command == "ldconfig -p | grep libGL.so.1"
    assert libgl.fix_candidates == ("apt:libgl1",)
    assert (
        package_id("opencv-python", "4.9.0.80"),
        syslib_id("libGL.so.1"),
    ) in {(e.src, e.dst) for e in graph.edges}
```

This step is TWO 1-for-1 function replacements (`test_build_reconciled_predictions_keep_resolver_origin` → `test_build_native_gaps_are_fresh_probe_nodes_without_a_prior`; `test_build_ldd_probe_reconciles_seed_prediction` → `test_build_ldd_probe_creates_fresh_node_without_a_prior`) — the file's function count is unchanged; net `0`.

- [ ] **Step 11: Run the affected test files**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_tables.py tests/depgraph/test_seed.py tests/depgraph/test_build.py tests/depgraph/test_ldd_probe_docker.py -q`
Expected: PASS (test_ldd_probe_docker's one real test still SKIPs — no docker binary — but collection must succeed).

- [ ] **Step 12: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at 580 (585 from Task 1, **−5** from this task). Precisely: `test_seed.py`'s rewrite is a 7-for-7 swap (net 0), `test_build.py`'s two function replacements are 1-for-1 swaps (net 0), `test_ldd_probe_docker.py`'s fix only shrinks one existing test's body (net 0) — so `test_tables.py`'s **−5** (Step 8, five deletions with no replacement) is the ONLY source of this task's net change, not "unchanged" as an earlier draft of this plan claimed. See Global Constraints' indicative-counts note.

- [ ] **Step 13: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/seed.py src/python_deps/depgraph/tables.py \
        src/python_deps/depgraph/config_tables.py src/python_deps/depgraph/build.py \
        tests/depgraph/test_tables.py tests/depgraph/test_seed.py \
        tests/depgraph/test_build.py tests/depgraph/test_ldd_probe_docker.py
git commit -m "refactor(depgraph): seed_wheel_oracle_prior replaces curated PACKAGE_TO_SYSTEM_DEPS table (cluster 1a)"
```

---

### Task 3: Schema — `DiscoveredBy.STATIC_DECLARATION` + `DiscoveredBy.CLASSIFIER`

**Files:**
- Modify: `src/python_deps/depgraph/schema.py`
- Test: `tests/depgraph/test_schema.py` (append)

**Interfaces:** Produces: `DiscoveredBy.STATIC_DECLARATION = "static_declaration"` (cluster 1b's declaration-mining origin, consumed starting Task 4) AND `DiscoveredBy.CLASSIFIER = "classifier"` (cluster 2's LLM-admission origin, consumed starting Task 10). Both are added together here — they are cheap, independent enum members, and doing so now means every later task that needs either one (Task 4 for the former; Tasks 10/11/12 for the latter) has zero schema-ordering dependency left to track.

- [ ] **Step 1: Write the failing tests**

Append to `tests/depgraph/test_schema.py`:

```python
def test_discovered_by_has_static_declaration():
    assert DiscoveredBy.STATIC_DECLARATION.value == "static_declaration"


def test_discovered_by_has_classifier():
    assert DiscoveredBy.CLASSIFIER.value == "classifier"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_schema.py -q`
Expected: FAIL — `AttributeError: STATIC_DECLARATION` (and, once that alone is fixed, `CLASSIFIER` would fail next — Step 3 adds both members together so both tests go green in the same commit).

- [ ] **Step 3: Add the enum members**

In `src/python_deps/depgraph/schema.py`, in `class DiscoveredBy(enum.Enum):`, add `STATIC_DECLARATION` and `CLASSIFIER` right after `RESOLVER = "resolver"`:

```python
class DiscoveredBy(enum.Enum):
    GOAL = "goal"
    STATIC_SCAN = "static_scan"
    RESOLVER = "resolver"
    STATIC_DECLARATION = "static_declaration"
    CLASSIFIER = "classifier"
    PROBE = "probe"
    RUNTIME = "runtime"
```

- [ ] **Step 4: Run test to verify it passes, then the full suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the Task 2 count + 2 = 582.

- [ ] **Step 5: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema.py
git commit -m "feat(depgraph): add DiscoveredBy.STATIC_DECLARATION + DiscoveredBy.CLASSIFIER (cluster 1b/2 prerequisites)"
```

---

### Task 4: `declaration_mine.py` — mine apt declarations (new module, not yet wired)

**Files:**
- Create: `src/python_deps/depgraph/declaration_mine.py`
- Test: `tests/depgraph/test_declaration_mine.py`

**Interfaces:**
- Produces: `mine_declarations(graph: DepGraph, repo_path: str) -> DepGraph` (pure, container-free — the future stage 3c, wired in Task 5). `apt_hits_for_repo(repo_path: str) -> tuple[tuple[str, str, int], ...]` (public: `(package, source-relpath, line)` triples across Dockerfile/Aptfile/binder/CI — the shared parser `static_collect.py`'s `collect_declaration_context_hits` reuses in Task 8, so the parsing logic is written exactly once). A PRIVATE `_is_toolchain_apt(name: str) -> bool` — see Design note.
- Consumes: `DiscoveredBy.STATIC_DECLARATION` (Task 3), `syslib_id`/`tool_id` (`ids.py`, existing, unchanged).
- Not yet called from `build.py` — this task is deliberately standalone and testable via direct calls, so the parser contract is verified in isolation before Task 5 wires it into the pipeline and Task 6 fixes the reconciliation guard it depends on.

**Design note (folding a task an earlier draft split out unnecessarily):** an earlier draft of this plan gave the shape-classifier its own task, adding a PUBLIC `is_toolchain_apt` function to `ids.py`. `ids.py` is, everywhere else in this codebase, a file of pure `X_id(name) -> str` id constructors; a classifier over an ALREADY-DECLARED apt package name's shape is a different kind of function, and this module (`declaration_mine.py`) is its ONE AND ONLY consumer. This plan instead defines `_is_toolchain_apt` PRIVATE and inline, right here, folded into this task — `ids.py` (and `tests/depgraph/test_ids.py`) stay completely untouched by this plan. The helper's tests are folded into this task's test file too (as direct tests of the private function — the same pattern this codebase already uses for `wheel_oracle.py`'s private `_artifact_filename`/`_wheel_matches_platform`).

- [ ] **Step 1: Write the failing tests**

Create `tests/depgraph/test_declaration_mine.py`:

```python
"""Stage 3c parser contract (construction-enrichment cluster 1b).

Deliberately small and precise (correctness over completeness): backslash-join
first, &&/; split (sequencing, not a skip signal), skip the WHOLE command on
shell control flow, keep a token as a package ONLY if it is a bare apt name
(drops flags and $VAR/${VAR}/$(...) substitutions — a segment with ANY dropped
$-token is skipped whole).
"""

from __future__ import annotations

from python_deps.depgraph.declaration_mine import (
    _is_toolchain_apt, apt_hits_for_repo, mine_declarations,
)
from python_deps.depgraph.ids import project_id, syslib_id, tool_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, EdgeType, Layer, Node, NodeType, State,
)


def _project_graph(name="myrepo"):
    return DepGraph().with_node(Node(
        id=project_id(name), type=NodeType.PROJECT, name=name,
        layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
    ))


def test_dockerfile_run_install_creates_systemlib_and_tool_nodes(tmp_path):
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\n"
        "RUN apt-get update && apt-get install -y libpq5 cmake\n"
    )
    out = mine_declarations(_project_graph(), str(tmp_path))

    syslib = out.get(syslib_id("libpq5"))
    tool = out.get(tool_id("cmake"))
    assert syslib is not None and syslib.type is NodeType.SYSTEM_LIB
    assert tool is not None and tool.type is NodeType.TOOL
    assert syslib.discovered_by is DiscoveredBy.STATIC_DECLARATION
    assert syslib.state is State.UNKNOWN
    assert syslib.chosen_fix == "apt:libpq5"
    assert syslib.check_command == "dpkg -s libpq5"
    assert syslib.provenance == "Dockerfile:2"


def test_backslash_continuation_is_joined(tmp_path):
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\n"
        "RUN apt-get install -y \\\n"
        "    libpq5 \\\n"
        "    cmake\n"
    )
    out = mine_declarations(_project_graph(), str(tmp_path))
    assert out.get(syslib_id("libpq5")) is not None
    assert out.get(tool_id("cmake")) is not None
    # provenance cites the line the (joined) command STARTS on.
    assert out.get(syslib_id("libpq5")).provenance == "Dockerfile:2"


def test_control_flow_run_is_skipped_entirely(tmp_path):
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\n"
        "RUN if [ -f /flag ]; then apt-get install -y libpq5; fi\n"
    )
    out = mine_declarations(_project_graph(), str(tmp_path))
    assert out.get(syslib_id("libpq5")) is None


def test_flag_and_var_tokens_dropped_var_skips_whole_segment(tmp_path):
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\n"
        "RUN apt-get install -y --no-install-recommends $PKGS libcurl4\n"
    )
    out = mine_declarations(_project_graph(), str(tmp_path))
    # the segment carried a $-token -> the WHOLE segment is skipped, including
    # libcurl4 (partial extraction of a templated list would be wrong).
    assert out.get(syslib_id("libcurl4")) is None


def test_flag_only_dropped_bare_name_kept(tmp_path):
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\n"
        "RUN apt-get install -y --no-install-recommends libpq5\n"
    )
    out = mine_declarations(_project_graph(), str(tmp_path))
    assert out.get(syslib_id("libpq5")) is not None


def test_aptfile_one_package_per_line(tmp_path):
    (tmp_path / "Aptfile").write_text("libpq5\n# a comment\ncmake\n")
    out = mine_declarations(_project_graph(), str(tmp_path))
    assert out.get(syslib_id("libpq5")) is not None
    assert out.get(tool_id("cmake")) is not None
    assert out.get(syslib_id("libpq5")).provenance == "Aptfile:1"


def test_binder_apt_txt(tmp_path):
    (tmp_path / "binder").mkdir()
    (tmp_path / "binder" / "apt.txt").write_text("ffmpeg\n")
    out = mine_declarations(_project_graph(), str(tmp_path))
    assert out.get(tool_id("ffmpeg")) is not None


def test_ci_workflow_run_apt_install(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "test.yml").write_text(
        "jobs:\n"
        "  t:\n"
        "    steps:\n"
        "      - run: sudo apt-get update && sudo apt-get install -y libpq5\n"
    )
    out = mine_declarations(_project_graph(), str(tmp_path))
    assert out.get(syslib_id("libpq5")) is not None


def test_conda_environment_yml_is_excluded(tmp_path):
    (tmp_path / "environment.yml").write_text(
        "name: env\ndependencies:\n  - python=3.11\n  - ffmpeg\n"
    )
    out = mine_declarations(_project_graph(), str(tmp_path))
    # conda deps never become nodes here -- routed to the LLM bundle instead
    # (static_collect.collect_declaration_context_hits, Task 8).
    assert out.get(tool_id("ffmpeg")) is None


def test_idempotent_merges_provenance_across_two_dockerfiles(tmp_path):
    (tmp_path / "Dockerfile").write_text("RUN apt-get install -y libpq5\n")
    (tmp_path / "Dockerfile.ci").write_text("RUN apt-get install -y libpq5\n")
    out = mine_declarations(_project_graph(), str(tmp_path))
    node = out.get(syslib_id("libpq5"))
    assert node is not None
    assert "Dockerfile:1" in node.provenance
    assert "Dockerfile.ci:1" in node.provenance


def test_existing_resolver_node_at_same_id_is_left_untouched(tmp_path):
    (tmp_path / "Dockerfile").write_text("RUN apt-get install -y build-essential\n")
    graph = _project_graph().with_node(Node(
        id=tool_id("build-essential"), type=NodeType.TOOL, name="build-essential",
        layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RESOLVER, state=State.UNKNOWN,
        chosen_fix="apt:build-essential", provenance="wheel-oracle (build_from_source)",
    ))
    out = mine_declarations(graph, str(tmp_path))
    node = out.get(tool_id("build-essential"))
    assert node.discovered_by is DiscoveredBy.RESOLVER  # untouched, not downgraded
    assert node.provenance == "wheel-oracle (build_from_source)"


def test_project_hub_requires_edge_created(tmp_path):
    (tmp_path / "Dockerfile").write_text("RUN apt-get install -y libpq5\n")
    out = mine_declarations(_project_graph(), str(tmp_path))
    edges = {(e.src, e.dst) for e in out.edges if e.relation is EdgeType.REQUIRES}
    assert (project_id("myrepo"), syslib_id("libpq5")) in edges


def test_mine_declarations_returns_new_graph_originals_unchanged(tmp_path):
    (tmp_path / "Dockerfile").write_text("RUN apt-get install -y libpq5\n")
    graph = _project_graph()
    out = mine_declarations(graph, str(tmp_path))
    assert out is not graph
    assert graph.get(syslib_id("libpq5")) is None


def test_apt_hits_for_repo_returns_triples_reused_by_static_collect(tmp_path):
    (tmp_path / "Dockerfile").write_text("RUN apt-get install -y libpq5\n")
    hits = apt_hits_for_repo(str(tmp_path))
    assert ("libpq5", "Dockerfile", 1) in hits


# --- _is_toolchain_apt (private; folded in here rather than a separate
# ids.py task -- this module is its sole consumer, see Design note above) ---

def test_is_toolchain_apt_fixed_set():
    for name in ("build-essential", "gcc", "g++", "clang", "make", "cmake",
                 "pkg-config", "ninja-build", "autoconf", "automake", "libtool"):
        assert _is_toolchain_apt(name) is True


def test_is_toolchain_apt_dev_header_is_toolchain():
    # a *-dev package is a build-time header need, even though it starts "lib"
    # — matches the existing seed.py/probe.py "-dev => toolchain" convention.
    assert _is_toolchain_apt("libpq-dev") is True


def test_is_toolchain_apt_runtime_lib_is_not_toolchain():
    assert _is_toolchain_apt("libgl1") is False
    assert _is_toolchain_apt("libpq5") is False


def test_is_toolchain_apt_bare_tool_name_not_lib_shaped():
    assert _is_toolchain_apt("ffmpeg") is True
    assert _is_toolchain_apt("curl") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_declaration_mine.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.declaration_mine'`.

- [ ] **Step 3: Create `declaration_mine.py`**

Create `src/python_deps/depgraph/declaration_mine.py`:

```python
"""Stage 3c — mine the repo's own apt declarations into STATIC_DECLARATION nodes.

Realizes construction-enrichment cluster 1b: Dockerfile / Aptfile /
binder/apt.txt / CI workflow `run:` apt-install lines are an AUTHORITATIVE
source (the repo author's own words), so they earn a dedicated deterministic
stage rather than being routed through the LLM (which would downgrade them to
the same soft tier as a README guess — see the design doc). Pure: no
Docker/network/LLM; every "mutation" returns a NEW DepGraph.

Parser contract (deliberately small; correctness over completeness):
  * join backslash-continuation lines first (the dominant multi-line RUN style);
  * split the joined command on `&&` / `;` — sequencing is not a skip signal;
  * skip the WHOLE (joined) command if it contains shell control flow
    (if/case/for/while), checked once, before splitting;
  * for each `apt(-get)? install` segment, keep a token as a package name ONLY
    if it matches a bare apt name (^[a-z0-9][a-z0-9.+-]*$) — this drops every
    flag and every $VAR/${VAR}/$(...) substitution; a segment with ANY dropped
    $-token is skipped whole (partial extraction of a templated list would
    fabricate a wrong package name).
  * conda `environment.yml` is explicitly OUT of scope here (conda names !=
    apt names) — routed to the LLM evidence bundle instead
    (static_collect.collect_declaration_context_hits).

``apt_hits_for_repo`` is the shared (package, source-relpath, line) extraction
this module's own `mine_declarations` builds nodes from; `static_collect.py`
reuses the SAME function for `decl_apt` evidence hits, so the apt-line parser
exists exactly once.

``_is_toolchain_apt`` (below) classifies an ALREADY-DECLARED apt package name
by shape (Tool vs SystemLib). It is PRIVATE — this module is its sole
consumer. An earlier draft of this plan gave it its own task modifying
`ids.py`; `ids.py` stays a file of pure `X_id(name)` id constructors
elsewhere in this codebase (untouched by this plan), so the shape-classifier
is folded in here instead.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import replace

try:  # PyYAML is an existing soft dependency (service_scan.py uses it too).
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from python_deps.depgraph.ids import syslib_id, tool_id
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)

_CONTROL_FLOW_RE = re.compile(r"\b(if|case|for|while)\b")
_APT_INSTALL_RE = re.compile(r"\bapt(?:-get)?\s+install\b")
_BARE_APT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")

# Small fixed toolchain-name set (declaration_mine.py's "is this declared apt
# package a build-time TOOLCHAIN need" classifier — construction-enrichment
# cluster 1b). PRIVATE: this module is the sole consumer.
_TOOLCHAIN_APT_NAMES = frozenset({
    "build-essential", "gcc", "g++", "clang", "make", "cmake",
    "pkg-config", "ninja-build", "autoconf", "automake", "libtool",
})


def _is_toolchain_apt(name: str) -> bool:
    """True when a DECLARED apt package name is a build-time TOOLCHAIN need
    (Tool) rather than a runtime shared library (SystemLib): the small fixed
    toolchain set, or anything not shaped like a plain runtime lib package (a
    'lib*' name that does NOT end '-dev', e.g. 'libgl1'). A '*-dev' package
    (even 'libpq-dev') is a build-time header need, so it classifies as Tool —
    matching the existing seed.py/probe.py '-dev => toolchain' convention.

    This classifies an ALREADY-DECLARED apt package name by shape; it is NOT a
    package->syslib prediction map (the thing construction-enrichment cluster
    1a deleted).
    """
    if name in _TOOLCHAIN_APT_NAMES:
        return True
    return not (name.startswith("lib") and not name.endswith("-dev"))


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _dockerfile_paths(repo_path: str) -> list[str]:
    return sorted(glob.glob(os.path.join(repo_path, "**", "Dockerfile*"), recursive=True))


def _packages_from_segment(segment: str) -> list[str] | None:
    """Bare apt package names after `install` in one &&/;-split segment, or
    None if the segment has no install match. Returns None (skip whole
    segment) when any post-install token carries a `$` substitution."""
    match = _APT_INSTALL_RE.search(segment)
    if not match:
        return None
    tail = segment[match.end():].strip()
    names: list[str] = []
    for tok in tail.split():
        if tok.startswith("-"):
            continue
        if "$" in tok:
            return None  # templated list -> skip the whole segment
        if _BARE_APT_NAME_RE.match(tok):
            names.append(tok)
    return names or None


def _run_style_hits(text: str) -> tuple[tuple[str, int], ...]:
    """(package, 1-based start-line) pairs from apt(-get) install segments in
    RUN-shaped text (a Dockerfile, or one CI workflow `run:` block)."""
    out: list[tuple[str, int]] = []
    buf = ""
    buf_start = 1
    for i, raw_line in enumerate(text.splitlines(), start=1):
        if not buf:
            buf_start = i
        stripped = raw_line.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        logical = (buf + stripped).strip()
        buf = ""
        if not logical or logical.startswith("#"):
            continue
        if _CONTROL_FLOW_RE.search(logical):
            continue
        for segment in re.split(r"&&|;", logical):
            names = _packages_from_segment(segment.strip())
            if names:
                out.extend((n, buf_start) for n in names)
    return tuple(out)


def _list_style_hits(text: str) -> tuple[tuple[str, int], ...]:
    """(package, line) pairs, ONE bare apt name per non-comment/non-blank line
    (the Aptfile / binder/apt.txt convention)."""
    out: list[tuple[str, int]] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if line and _BARE_APT_NAME_RE.match(line):
            out.append((line, i))
    return tuple(out)


def _workflow_run_hits(repo_path: str) -> dict[str, tuple[tuple[str, int], ...]]:
    """{relpath: (package, line)} for apt installs inside .github/workflows/*.yml
    `run:` step scalars. Line numbers are relative to the run: block's own text
    (a documented simplification — good enough for provenance, not byte-exact
    against the whole file)."""
    if yaml is None:
        return {}
    out: dict[str, tuple] = {}
    wf_dir = os.path.join(repo_path, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return out
    for fname in sorted(os.listdir(wf_dir)):
        if not fname.lower().endswith((".yml", ".yaml")):
            continue
        text = _read(os.path.join(wf_dir, fname))
        if text is None:
            continue
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
            continue
        hits: list[tuple[str, int]] = []
        for job in doc["jobs"].values():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                run = step.get("run") if isinstance(step, dict) else None
                if isinstance(run, str):
                    hits.extend(_run_style_hits(run))
        if hits:
            out[os.path.join(".github", "workflows", fname)] = tuple(hits)
    return out


def apt_hits_for_repo(repo_path: str) -> tuple[tuple[str, str, int], ...]:
    """(package, source-relpath, line) triples across Dockerfile/Aptfile/
    binder/apt.txt/.github workflows — the shared source-of-truth both
    mine_declarations (nodes) and static_collect.collect_declaration_context_hits
    (LLM evidence, Task 8) build on."""
    hits: list[tuple[str, str, int]] = []
    for path in _dockerfile_paths(repo_path):
        text = _read(path)
        if text is None:
            continue
        rel = os.path.relpath(path, repo_path)
        hits.extend((pkg, rel, line) for pkg, line in _run_style_hits(text))

    aptfile = os.path.join(repo_path, "Aptfile")
    text = _read(aptfile)
    if text is not None:
        hits.extend((pkg, "Aptfile", line) for pkg, line in _list_style_hits(text))

    binder_apt = os.path.join(repo_path, "binder", "apt.txt")
    text = _read(binder_apt)
    if text is not None:
        hits.extend((pkg, "binder/apt.txt", line) for pkg, line in _list_style_hits(text))

    for rel, file_hits in _workflow_run_hits(repo_path).items():
        hits.extend((pkg, rel, line) for pkg, line in file_hits)

    return tuple(hits)


def _node_for(pkg: str, node_id: str, provenance: str) -> Node:
    fix = f"apt:{pkg}"
    node_type = NodeType.TOOL if _is_toolchain_apt(pkg) else NodeType.SYSTEM_LIB
    layer = Layer.TOOLCHAIN if node_type is NodeType.TOOL else Layer.SYSTEM
    return Node(
        id=node_id, type=node_type, name=pkg, layer=layer,
        discovered_by=DiscoveredBy.STATIC_DECLARATION, state=State.UNKNOWN,
        check_command=f"dpkg -s {pkg}", fix_candidates=(fix,), chosen_fix=fix,
        provenance=provenance,
    )


def _add_or_merge(graph: DepGraph, pkg: str, relpath: str, line: int) -> DepGraph:
    """Idempotent + reconciliation-safe: a node id already present is merged
    (provenance appended, both sources stay attributable) rather than
    duplicated; a RESOLVER/PROBE node already at this id is left as-is (this
    stage never downgrades a higher-confidence node)."""
    node_id = tool_id(pkg) if _is_toolchain_apt(pkg) else syslib_id(pkg)
    existing = graph.get(node_id)
    new_provenance = f"{relpath}:{line}"
    if existing is None:
        return graph.with_node(_node_for(pkg, node_id, new_provenance))
    if existing.discovered_by is DiscoveredBy.STATIC_DECLARATION:
        merged = f"{existing.provenance}; {new_provenance}" if existing.provenance else new_provenance
        return graph.with_node(replace(existing, provenance=merged))
    return graph  # a RESOLVER/PROBE node already owns this id — leave it as-is


def mine_declarations(graph: DepGraph, repo_path: str) -> DepGraph:
    """Stage 3c: repo apt declarations (Dockerfile/Aptfile/binder/CI) ->
    STATIC_DECLARATION Tool/SystemLib nodes. Pure, container-free (reads the
    repo on disk only). Adds a `requires` edge from the graph's Project hub
    node (created by build.py's stage 3a', which runs before this stage) to
    each declared node, when a Project node is present."""
    new = graph
    project = next((n for n in new.nodes if n.type is NodeType.PROJECT), None)
    for pkg, relpath, line in apt_hits_for_repo(repo_path):
        new = _add_or_merge(new, pkg, relpath, line)
        node_id = tool_id(pkg) if _is_toolchain_apt(pkg) else syslib_id(pkg)
        if project is not None:
            new = new.with_edge(Edge(src=project.id, dst=node_id,
                                     relation=EdgeType.REQUIRES, origin="declaration"))
    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_declaration_mine.py -q`
Expected: PASS (18 passed — 14 parser-contract tests + 4 folded `_is_toolchain_apt` tests).

- [ ] **Step 5: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the Task 3 count + 18 = 600.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/declaration_mine.py tests/depgraph/test_declaration_mine.py
git commit -m "feat(depgraph): add declaration_mine.py — mine repo apt declarations into STATIC_DECLARATION nodes, with private _is_toolchain_apt (cluster 1b)"
```

---

### Task 5: Wire stage 3c into `build.py`

**Files:**
- Modify: `src/python_deps/depgraph/build.py`

**Interfaces:** No new interface — this task only wires Task 4's already-tested `mine_declarations` into the pipeline, immediately after `seed_wheel_oracle_prior` (stage 3b) and before `install_closure` (stage 4).

- [ ] **Step 1: Update the module docstring's stage list**

In `src/python_deps/depgraph/build.py`, change:
```python
    1. scan      static import scan          -> Import + Test nodes   (cycle 1)
    2. map       roots.select_roots          -> resolver roots
    3. resolve   uv.lock closure (HOST)      -> Package nodes/edges   (cycle 2)
    3b. seed     predicted native nodes      -> Tool/SystemLib        (cycle 2)
    4. probe     install + import (CONTAINER)-> SystemLib/Tool nodes  (cycle 3)
```
to:
```python
    1. scan      static import scan          -> Import + Test nodes   (cycle 1)
    2. map       roots.select_roots          -> resolver roots
    3. resolve   uv.lock closure (HOST)      -> Package nodes/edges   (cycle 2)
    3b. seed     wheel-oracle prior          -> build-essential Tool  (cycle 2)
    3c. declare  mine_declarations (repo)    -> Tool/SystemLib        (cycle 2)
    4. probe     install + import (CONTAINER)-> SystemLib/Tool nodes  (cycle 3)
```

- [ ] **Step 2: Add the import**

Add, alongside the other stage imports:

```python
from python_deps.depgraph.declaration_mine import mine_declarations
```

- [ ] **Step 3: Insert the stage-3c call**

In `build_dep_graph`, change:
```python
    # Stage 3b — predicted native Tool/SystemLib nodes (resolver-origin).
    # PACKAGE_TO_SYSTEM_DEPS here is a PROACTIVE FALLBACK (pre-install / install-fail
    # hint); Stage 4.5 ldd_probe is the authoritative run-time native-lib source.
    graph = seed_wheel_oracle_prior(graph)
    resolver_ids = {n.id for n in graph.nodes} - pre_resolve_ids
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)
```
to:
```python
    # Stage 3b — wheel-oracle prior: build-essential Tool for every from-source
    # package (RESOLVER-origin; derived from the resolver's own wheel/sdist
    # signal, not a curated table — construction-enrichment cluster 1a).
    graph = seed_wheel_oracle_prior(graph)
    # Stage 3c — mine the repo's own apt declarations (Dockerfile/Aptfile/CI)
    # into STATIC_DECLARATION Tool/SystemLib nodes — an authoritative,
    # author-stated source, kept out of the LLM path (construction-enrichment
    # cluster 1b; see declaration_mine.py's module docstring). Container-free,
    # so it runs here alongside the other pre-container stages.
    graph = mine_declarations(graph, repo_path)
    resolver_ids = {n.id for n in graph.nodes} - pre_resolve_ids
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)
```

(Both stage 3b's and stage 3c's newly-added nodes fall inside the `pre_resolve_ids`→`resolver_ids` window, so they are stamped `discovered_cycle=_RESOLVER_CYCLE` (2) together — both are pre-container, host-side static discovery; no new cycle number is introduced.)

- [ ] **Step 4: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS, unchanged count from Task 4 (600) — this task adds no new tests (`test_build.py`'s fixtures declare no apt deps, so `mine_declarations` is a no-op for them; Task 6 below is where a Dockerfile-bearing fixture first exercises this wiring end-to-end).

- [ ] **Step 5: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/build.py
git commit -m "feat(depgraph): wire mine_declarations as pipeline stage 3c (cluster 1b)"
```

---

### Task 6: Widen `reconcile_predicted`'s guard — the Critical fix

**Files:**
- Modify: `src/python_deps/depgraph/probe.py` (`reconcile_predicted`)
- Modify: `tests/depgraph/test_probe.py` (append a unit test)
- Modify: `tests/depgraph/test_build.py` (append an end-to-end test)

**Interfaces:** `reconcile_predicted(graph, predicted_id, *, check, evidence, command) -> Node | None` — the guard widens from `predicted.discovered_by is not DiscoveredBy.RESOLVER` to `predicted.discovered_by not in (DiscoveredBy.RESOLVER, DiscoveredBy.STATIC_DECLARATION)`. `ldd_probe.py` imports this exact function (`from python_deps.depgraph.probe import reconcile_predicted`), so this single change covers both the build-time (`install_closure`) and run-time (`ldd_probe`) call sites — there is only one function to change, not two.

**Why this is Critical (design doc Risk #1):** without this fix, a probe stage independently creating a node at the same apt-name id would *replace* a declaration-mined node wholesale (via `new.with_node(node)` after `reconcile_predicted` returns `None`), silently losing the author's `file:line` provenance and demoting it from `STATIC_DECLARATION` back to `PROBE` — on exactly the repos cluster 1b targets.

- [ ] **Step 1: Write the failing unit test**

Append to `tests/depgraph/test_probe.py`:

```python
def test_reconcile_accepts_static_declaration_prediction(fake_executor, make_result_fixture):
    # The Critical fix (construction-enrichment cluster 1b): a declaration-mined
    # node (STATIC_DECLARATION) must reconcile in place, keeping its discovery
    # origin and file:line provenance — not be replaced by a fresh PROBE node.
    pkg = _package("psycopg2", "2.9.9")
    declared = Node(
        id=tool_id("libpq-dev"),
        type=NodeType.TOOL,
        name="libpq-dev",
        layer=Layer.TOOLCHAIN,
        discovered_by=DiscoveredBy.STATIC_DECLARATION,
        state=State.UNKNOWN,
        check_command="dpkg -s libpq-dev",
        fix_candidates=("apt:libpq-dev",),
        chosen_fix="apt:libpq-dev",
        provenance="Dockerfile:3",
    )
    graph = DepGraph().with_node(pkg).with_node(declared)
    fake_executor.responses = {
        "pip install": make_result_fixture(
            returncode=1, stderr="Error: pg_config executable not found."
        )
    }

    out = install_closure(graph, fake_executor)

    node = out.get(tool_id("libpq-dev"))
    assert node is not None
    assert node.discovered_by is DiscoveredBy.STATIC_DECLARATION  # NOT clobbered
    assert node.provenance == "Dockerfile:3"                       # NOT lost
    assert node.check_command == "command -v pg_config"            # observed check adopted
    assert graph.get(tool_id("pg_config")) is None
    assert out.get(tool_id("pg_config")) is None                   # no duplicate fresh node
```

(This test file already imports `Node`, `NodeType`, `Layer`, `DiscoveredBy`, `State`, `tool_id`, `DepGraph` — no new imports needed.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_probe.py -k test_reconcile_accepts_static_declaration_prediction -q`
Expected: FAIL — `node.discovered_by` is `DiscoveredBy.PROBE` (a fresh node was created; the old guard rejected the STATIC_DECLARATION prediction).

- [ ] **Step 3: Widen the guard**

In `src/python_deps/depgraph/probe.py`, in `reconcile_predicted`, change:
```python
    predicted = graph.get(predicted_id)
    if predicted is None or predicted.discovered_by is not DiscoveredBy.RESOLVER:
        return None
```
to:
```python
    predicted = graph.get(predicted_id)
    if predicted is None or predicted.discovered_by not in (
        DiscoveredBy.RESOLVER, DiscoveredBy.STATIC_DECLARATION
    ):
        return None
```

Update the function's docstring line `"discovered_by stays RESOLVER per the spec"` to `"discovered_by stays the discovery origin — RESOLVER or STATIC_DECLARATION — per the spec"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_probe.py -q`
Expected: PASS, including the pre-existing `test_reconcile_skips_non_resolver_prediction` (that test uses `discovered_by=DiscoveredBy.PROBE`, still correctly excluded by the widened guard — no change needed there).

- [ ] **Step 5: Write the failing end-to-end test in `test_build.py`**

Append to `tests/depgraph/test_build.py`:

```python
def test_build_declared_syslib_survives_probe_reconciliation(tmp_path):
    """Cluster 1b end-to-end: a repo that DECLARES libgl1 in its Dockerfile
    gets a STATIC_DECLARATION node at stage 3c; the probe-observed
    libGL.so.1 gap (stage 4.5, ldd_probe) then RECONCILES into that declared
    node instead of creating a fresh PROBE one — the Critical fix
    (reconcile_predicted widened to {RESOLVER, STATIC_DECLARATION})."""
    import json

    from conftest import FakeExecutor  # type: ignore

    (tmp_path / "app.py").write_text("import cv2\n")
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\nRUN apt-get update && apt-get install -y libgl1\n"
    )
    ex = FakeExecutor(
        responses={
            "uv pip compile": _r(stdout=_LDD_CLOSURE),
            "pip install": _r(returncode=0),
            "locate_file": _r(stdout=json.dumps({"opencv-python": [_CV2_SO_BUILD]})),
            "ldd ": _r(stdout=f"{_CV2_SO_BUILD}:\n\tlibGL.so.1 => not found\n"),
            "apt-cache show libgl1": _r(stdout="Package: libgl1\n"),
        },
        default=_r(returncode=0),
    )

    graph = build_dep_graph(
        str(tmp_path), ex, host_executor=ex, exclude_newer="2024-01-01"
    )

    libgl1 = graph.get(syslib_id("libgl1"))
    assert libgl1 is not None
    # reconciled IN PLACE: discovery origin + declared provenance survive; only
    # the check_command/evidence/attempt come from the probe.
    assert libgl1.discovered_by is DiscoveredBy.STATIC_DECLARATION
    assert libgl1.provenance == "Dockerfile:2"
    assert libgl1.check_command == "ldconfig -p | grep libGL.so.1"
    assert graph.get(syslib_id("libGL.so.1")) is None  # no duplicate soname node
    assert any(a.outcome == "failed" for a in libgl1.attempts)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_build.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the Task 5 count + 2 = 602.

- [ ] **Step 8: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/probe.py tests/depgraph/test_probe.py tests/depgraph/test_build.py
git commit -m "fix(depgraph): widen reconcile_predicted guard to RESOLVER+STATIC_DECLARATION (Critical fix, cluster 1b Risk #1)"
```

This completes cluster 1 (1a + 1b). Every task from here on is cluster 2. (A reduced-scope alternative was considered and rejected: keep the widened `SystemLib`/`Tool` proposals ADVISORY-only — never actually made installable. That would be simpler, but it delivers **no proactive install**, defeating the point of widening the classifier's type allowlist at all. Tasks 10–12 below are exactly the emittability mechanism chosen instead of that fallback.)

---

### Task 7: `raw_intake.py` — bounded raw-prose evidence (new module)

**Files:**
- Create: `src/python_deps/depgraph/raw_intake.py`
- Test: create `tests/depgraph/test_raw_intake.py`

**Interfaces:**
- Produces: `collect_raw_file_snippets(repo_path: str) -> tuple[DeterministicHit, ...]` (kind=`"raw"`, synthetic id `raw.NN`).
- Consumes: `DeterministicHit` from `static_collect.py` (the existing dataclass — reused, not duplicated; the import direction is one-directional, `raw_intake.py -> static_collect.py`, since `static_collect.py` has no need of anything in `raw_intake.py`).
- Neither this function nor `static_collect.py`'s functions create graph nodes — both return evidence only. Wired into the LLM bundle by Task 9.

**Design note (why this is its own module, not folded into `static_collect.py`):** `static_collect.py`'s stated role is "a thin adapter that RESHAPES existing scanner output" — it does not scan the repo blindly and does not create graph truth. Reading raw, unstructured prose files (README/INSTALL/Makefile/setup.cfg/docs) is a genuinely different concern: there is no existing scanner to reshape output from, this module reads the filesystem directly — symmetric with `declaration_mine.py` (which also reads the repo directly, for a different evidence kind). An earlier draft of this plan bolted `collect_raw_file_snippets` into `static_collect.py`; this plan gives it its own module instead, matching the design doc's stated module layout.

- [ ] **Step 1: Write the failing tests**

Create `tests/depgraph/test_raw_intake.py`:

```python
from python_deps.depgraph.raw_intake import collect_raw_file_snippets


def test_collect_raw_file_snippets_reads_readme(tmp_path):
    (tmp_path / "README.md").write_text(
        "# demo\n\n## Install\n\nRun `apt-get install -y ffmpeg` then `pip install -e .`\n"
    )
    hits = collect_raw_file_snippets(str(tmp_path))
    assert hits and hits[0].kind == "raw" and hits[0].evidence_id == "raw.00"
    assert "ffmpeg" in hits[0].snippet


def test_collect_raw_file_snippets_respects_total_cap(tmp_path):
    (tmp_path / "README.md").write_text("A" * 4000)
    (tmp_path / "INSTALL.md").write_text("B" * 4000)
    hits = collect_raw_file_snippets(str(tmp_path))
    assert sum(len(h.snippet) for h in hits) <= 3000


def test_collect_raw_file_snippets_ignores_unlisted_files(tmp_path):
    (tmp_path / "NOTES.md").write_text("irrelevant\n")
    hits = collect_raw_file_snippets(str(tmp_path))
    assert hits == ()


def test_collect_raw_file_snippets_reads_docs_install_pages(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "installation.md").write_text("Install: apt-get install -y cmake\n")
    (tmp_path / "docs" / "faq.md").write_text("irrelevant\n")
    hits = collect_raw_file_snippets(str(tmp_path))
    assert any("cmake" in h.snippet for h in hits)
    assert all("irrelevant" not in h.snippet for h in hits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_raw_intake.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'python_deps.depgraph.raw_intake'`.

- [ ] **Step 3: Create `raw_intake.py`**

Create `src/python_deps/depgraph/raw_intake.py`:

```python
"""Raw-prose evidence intake for the construction-time LLM classifier
(construction-enrichment cluster 2 §1). Pure: returns evidence only, never
creates graph nodes.

A bounded allowlist of UNSTRUCTURED files (README/INSTALL/Makefile/setup.cfg/
docs install pages) the deterministic scanners in static_collect.py cannot
parse. This is its OWN module, not bolted into static_collect.py — see this
task's Design note. Wired into the LLM's evidence bundle by env_classifier.py
(Task 9).
"""

from __future__ import annotations

import os
import re

from python_deps.depgraph.static_collect import DeterministicHit

_RAW_TOTAL_CAP = 3000
_RAW_PER_FILE_CAP = 500


def _install_relevant_region(text: str, cap: int = _RAW_PER_FILE_CAP) -> str:
    """The first `cap` chars around the first case-insensitive 'install'
    mention, else the first `cap` chars of the file. Deterministic, one scan."""
    if not text:
        return ""
    match = re.search("install", text, re.IGNORECASE)
    start = max(0, match.start() - 50) if match else 0
    return text[start:start + cap]


def _raw_candidate_paths(repo_path: str) -> list[str]:
    """Deterministic allowlist of unstructured install-prose files, repo-relative."""
    paths: list[str] = []
    if not os.path.isdir(repo_path):
        return paths
    for name in sorted(os.listdir(repo_path)):
        if (name.startswith("README") or name.startswith("INSTALL")) and \
                os.path.isfile(os.path.join(repo_path, name)):
            paths.append(name)
    for exact in ("Makefile", "setup.cfg"):
        if os.path.isfile(os.path.join(repo_path, exact)):
            paths.append(exact)
    docs_dir = os.path.join(repo_path, "docs")
    if os.path.isdir(docs_dir):
        for name in sorted(os.listdir(docs_dir)):
            low = name.lower()
            if ("install" in low or "setup" in low) and \
                    os.path.isfile(os.path.join(docs_dir, name)):
                paths.append(os.path.join("docs", name))
    return paths


def collect_raw_file_snippets(repo_path: str) -> tuple[DeterministicHit, ...]:
    """Bounded raw-prose evidence (construction-enrichment cluster 2): a fixed
    allowlist of unstructured files (README/INSTALL/Makefile/setup.cfg/docs
    install pages) the deterministic scanners cannot parse, fed to the LLM
    classifier as VERBATIM snippets (kind='raw'). Per-file cap ~500 chars
    (the install-relevant region); total cap ~3000 chars across all files —
    the last included file is truncated to fit exactly, then collection
    stops. Each snippet gets a synthetic id ('raw.NN') so _sanitize's
    evidence_ref grounding invariant holds unchanged.
    """
    hits: list[DeterministicHit] = []
    budget = _RAW_TOTAL_CAP
    for i, relpath in enumerate(_raw_candidate_paths(repo_path)):
        if budget <= 0:
            break
        text = None
        try:
            with open(os.path.join(repo_path, relpath), encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        snippet = _install_relevant_region(text)[:budget]
        if not snippet:
            continue
        hits.append(DeterministicHit(f"raw.{i:02d}", relpath, "raw", snippet=snippet))
        budget -= len(snippet)
    return tuple(hits)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_raw_intake.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the Task 6 count + 4 = 606.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/raw_intake.py tests/depgraph/test_raw_intake.py
git commit -m "feat(depgraph): add raw_intake.py — bounded raw-prose evidence for the LLM classifier (cluster 2 raw intake)"
```

---

### Task 8: `static_collect.py` — declaration/conda context hits

**Files:**
- Modify: `src/python_deps/depgraph/static_collect.py`
- Test: `tests/depgraph/test_static_collect_bundle.py` (append)

**Interfaces:**
- Produces: `collect_declaration_context_hits(repo_path: str) -> tuple[DeterministicHit, ...]` (kinds `"decl_apt"` and `"conda_declaration"`, synthetic ids `decl.NN`/`conda.NN`).
- Consumes: `declaration_mine.apt_hits_for_repo` (Task 4) for the `decl_apt` hits — reuses the SAME parser `mine_declarations` uses, so Dockerfile/Aptfile/CI apt lines are parsed exactly once across the whole codebase.
- Does not create graph nodes — evidence only, consistent with the existing "thin adapter... does NOT create graph truth" invariant. `decl_apt` hits are for LLM cross-reference (the nodes already exist from stage 3c); the LLM must not re-create them.

**Note (where this evidence is consumed):** this task builds and tests `collect_declaration_context_hits`; **Task 9 wires it into `env_classifier.classify()`'s evidence bundle** (alongside `raw_intake.collect_raw_file_snippets`). This wiring is spec-load-bearing, not optional: the design EXCLUDES conda `environment.yml` from deterministic stage 3c (`declaration_mine.py`) precisely so its deps reach the LLM as `conda_declaration` hits here — a conda-only repo's SystemLib/Tool needs have NO other path into the graph. The `decl_apt` hits (apt packages whose nodes stage 3c already created) ride along for LLM cross-reference; the LLM must not re-create nodes for them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/depgraph/test_static_collect_bundle.py`:

```python
from python_deps.depgraph.static_collect import collect_declaration_context_hits


def test_declaration_context_hits_decl_apt(tmp_path):
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\nRUN apt-get install -y libgl1\n"
    )
    hits = collect_declaration_context_hits(str(tmp_path))
    decl = [h for h in hits if h.kind == "decl_apt"]
    assert decl and decl[0].name == "libgl1" and decl[0].file == "Dockerfile"


def test_declaration_context_hits_conda(tmp_path):
    (tmp_path / "environment.yml").write_text(
        "name: env\ndependencies:\n  - python=3.11\n  - ffmpeg=6.0\n  - pip\n"
    )
    hits = collect_declaration_context_hits(str(tmp_path))
    conda = [h for h in hits if h.kind == "conda_declaration"]
    names = {h.name for h in conda}
    assert {"python", "ffmpeg", "pip"} <= names


def test_declaration_context_hits_empty_repo(tmp_path):
    assert collect_declaration_context_hits(str(tmp_path)) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q`
Expected: FAIL — `ImportError: cannot import name 'collect_declaration_context_hits'`.

- [ ] **Step 3: Add the function**

In `src/python_deps/depgraph/static_collect.py`, update the module docstring's second sentence from `"It does NOT scan the repo blindly and does NOT create graph truth."` to `"It does NOT scan the repo blindly and does NOT create graph truth. collect_declaration_context_hits (below) is a DISTINCT, clearly-named concern (construction-enrichment cluster 2) — it re-uses declaration_mine's parser directly, by design, rather than re-parsing. Raw-prose file scanning (README/INSTALL/Makefile/etc.) lives in the separate raw_intake.py module, not here — this file stays a reshaping adapter, never a blind repo scanner."`

Add near the top, after `import json` and the `from dataclasses import dataclass` line:

```python
import os
import re
```

Add at the end of the file (after `compact_bundle_json`):

```python
def _conda_dependencies(repo_path: str) -> list[tuple[str, str]]:
    """(dependency name, source file name) pairs from a conda environment
    file's top-level `dependencies:` list (string entries only; version pins
    are trimmed to the bare name; pip sub-lists are skipped -- the LLM sees
    those via collect_static_evidence's manifest/package hits instead)."""
    try:
        import yaml as _yaml
    except ImportError:
        return []
    for fname in ("environment.yml", "environment.yaml"):
        path = os.path.join(repo_path, fname)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = _yaml.safe_load(fh)
        except (OSError, _yaml.YAMLError):
            continue
        if not isinstance(doc, dict):
            continue
        deps = doc.get("dependencies")
        if not isinstance(deps, list):
            continue
        out = []
        for dep in deps:
            if isinstance(dep, str):
                name = re.split(r"[=<> ]", dep.strip(), 1)[0]
                if name:
                    out.append((name, fname))
        return out
    return []


def collect_declaration_context_hits(repo_path: str) -> tuple[DeterministicHit, ...]:
    """Context-only evidence for the LLM: apt packages ALREADY declared in
    Dockerfile/Aptfile/binder/CI (kind='decl_apt' -- their NODES were already
    created by declaration_mine.mine_declarations, stage 3c; the LLM sees
    these only for cross-reference, it must not re-create a node for them)
    and conda environment.yml dependencies (kind='conda_declaration' -- conda
    names are NOT apt names, so mine_declarations excludes them entirely; the
    classifier is the only place conda deps are surfaced, soft, name-mapped
    or declined by the model).
    """
    from python_deps.depgraph.declaration_mine import apt_hits_for_repo

    hits: list[DeterministicHit] = []
    n = 0
    for pkg, relpath, _line in apt_hits_for_repo(repo_path):
        hits.append(DeterministicHit(f"decl.{n:02d}", relpath, "decl_apt", name=pkg))
        n += 1
    for pkg, relpath in _conda_dependencies(repo_path):
        hits.append(DeterministicHit(f"conda.{n:02d}", relpath, "conda_declaration", name=pkg))
        n += 1
    return tuple(hits)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_static_collect_bundle.py -q`
Expected: PASS (all existing + 3 new).

- [ ] **Step 5: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the Task 7 count + 3 = 609.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/static_collect.py tests/depgraph/test_static_collect_bundle.py
git commit -m "feat(depgraph): add collect_declaration_context_hits — decl_apt/conda_declaration evidence for the LLM classifier (cluster 2)"
```

---

### Task 9: `env_classifier.py` — widen `_SYSTEM_PROMPT` + wire raw-prose AND declaration/conda evidence into the bundle

**Files:**
- Modify: `src/envstate/env_classifier.py`
- Test: `tests/test_env_classifier.py` (append)

**Interfaces:** `_SYSTEM_PROMPT` gains `SystemLib`/`Tool` to its `type` allowlist and the `syslib:<name>`/`tool:<name>` id/layer forms. `classify()`'s evidence-bundle assembly now ALSO calls BOTH `raw_intake.collect_raw_file_snippets(repo_path)` (Task 7) AND `static_collect.collect_declaration_context_hits(repo_path)` (Task 8), concatenating their hits onto `collect_static_evidence(repo_path, graph)`'s — those two tasks built the functions; this task is where both are actually consumed. Wiring `collect_declaration_context_hits` is NOT optional: the design EXCLUDES conda `environment.yml` from deterministic stage 3c (`declaration_mine.py`) precisely so its dependencies reach the model as `conda_declaration` evidence here — if these hits are not in the bundle, a conda-only repo gets ZERO SystemLib/Tool benefit from anywhere in the pipeline. The `decl_apt` hits (apt packages the repo already declares, whose nodes stage 3c already created) ride along for LLM cross-reference. Because every hit's `evidence_id` (including the synthetic `decl.NN`/`conda.NN`/`raw.NN` ids) is folded into `bundle_ids` by the SAME `frozenset(h.evidence_id for h in hits)` line, `_sanitize`'s grounding invariant (`evidence_ref ∈ bundle_ids`) holds unchanged for a requirement the LLM grounds on a conda hit. No other function changes in this task — `_sanitize`/`validate_proposal`/`_KIND_PREFIX` already support `SystemLib`/`Tool` (verified: `patch_gate._KIND_PREFIX` already maps `NodeType.SYSTEM_LIB -> "syslib:"` and `NodeType.TOOL -> "tool:"`; `NodeType` already has both members). This task's first new test intentionally shows the "before" state: the widened prompt lets the LLM propose a SystemLib/Tool node and it IS admitted to the graph — but it is not yet *installable* (`chosen_fix is None`), which Task 12 fixes.

**Design note:** the design doc says "Drop any prompt instruction that asks the LLM to pick `candidate` vs `hint` by evidence-id type" — inspection of the current `_SYSTEM_PROMPT` found no such per-id-type promotion instruction exists today (it already just says `"promotion in {hint,candidate} (NEVER active)"`, unconditionally). This bullet is therefore a no-op verification, not a code change — noted here so it is not silently skipped as an oversight.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_env_classifier.py`:

```python
def _graph_with_ffmpeg_pkg():
    return DepGraph().with_node(Node(id=package_id("ffmpeg-python", "0.2.0"), type=NodeType.PACKAGE,
        name="ffmpeg-python", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="0.2.0"))


def test_classifier_can_propose_soft_systemlib_node():
    g = _graph_with_ffmpeg_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "syslib:ffmpeg", "type": "SystemLib", "name": "ffmpeg", "layer": "system",
         "state": "candidate", "check_command": None, "evidence_refs": ["pkg.00"]}]})
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    node = out.get("syslib:ffmpeg")
    assert node is not None and node.type is NodeType.SYSTEM_LIB
    assert node.state is State.MISSING           # discovery never certifies
    assert node.chosen_fix is None                # not yet reciped -- Task 12 fixes this
    assert node.strength is Strength.SOFT


def test_prompt_allows_systemlib_and_tool_types():
    assert "SystemLib" in _SYSTEM_PROMPT and "Tool" in _SYSTEM_PROMPT
    assert "syslib:" in _SYSTEM_PROMPT and "tool:" in _SYSTEM_PROMPT


def test_classifier_bundle_includes_raw_intake_snippets(tmp_path):
    # Cluster 2 Sec 1's raw-prose intake is USELESS if nothing ever calls it --
    # this proves classify() actually feeds raw_intake.collect_raw_file_snippets
    # hits into the bundle the LLM sees (Task 7 built the function; this wires it).
    g = _graph_with_ffmpeg_pkg()
    (tmp_path / "README.md").write_text(
        "# demo\n\n## Install\n\nRun `apt-get install -y ffmpeg` first.\n"
    )
    captured = {}

    def _capture(messages):
        captured["user"] = messages[1]["content"]
        return json.dumps({"requirements": []})

    make_construction_classifier(_capture)(g, str(tmp_path))
    assert '"kind": "raw"' in captured["user"]
    assert "ffmpeg" in captured["user"]


def test_classifier_bundle_includes_conda_declaration_hits(tmp_path):
    # Spec-intent hole this task closes: conda environment.yml is EXCLUDED from
    # deterministic stage 3c (declaration_mine.py) precisely so its deps reach
    # the LLM as conda_declaration evidence HERE. If classify() doesn't wire
    # collect_declaration_context_hits into the bundle, a conda-only repo gets
    # zero SystemLib/Tool benefit from anywhere. This proves the hit reaches the
    # bundle AND that its synthetic id is grounded (so _sanitize would accept a
    # requirement citing it).
    import re

    g = _graph_with_ffmpeg_pkg()
    (tmp_path / "environment.yml").write_text(
        "name: env\ndependencies:\n  - python=3.11\n  - ffmpeg=6.0\n  - pip\n"
    )
    captured = {}

    def _capture(messages):
        captured["user"] = messages[1]["content"]
        return json.dumps({"requirements": []})

    make_construction_classifier(_capture)(g, str(tmp_path))
    bundle = captured["user"]
    assert '"kind": "conda_declaration"' in bundle
    assert "ffmpeg" in bundle
    # the synthetic conda.NN id is present in the bundle -> it is in bundle_ids
    # (built from the same hit list), so a requirement grounded on it survives
    # _sanitize's evidence_ref-in-bundle_ids check.
    assert re.search(r'"evidence_id":\s*"conda\.\d+"', bundle)
```

Add the needed imports at the top of `tests/test_env_classifier.py`:
```python
from python_deps.depgraph.schema import Strength
```
(`State` is already imported; `DepGraph`, `Node`, `NodeType`, `Layer`, `DiscoveredBy`, `package_id` are already imported per the existing file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/test_env_classifier.py -q`
Expected: FAIL — `test_prompt_allows_systemlib_and_tool_types` fails (the prompt text doesn't mention `SystemLib`/`Tool` yet); `test_classifier_bundle_includes_raw_intake_snippets` fails (`collect_raw_file_snippets` exists as of Task 7, but `classify()` never calls it yet, so `'"kind": "raw"'` never appears in the captured bundle); `test_classifier_bundle_includes_conda_declaration_hits` fails (`collect_declaration_context_hits` exists as of Task 8, but `classify()` never calls it yet, so `'"kind": "conda_declaration"'` never appears); `test_classifier_can_propose_soft_systemlib_node` may already pass by coincidence of pre-existing gate support — confirm which by running with `-v`; Steps 3–4 below make all four pass regardless.

- [ ] **Step 3: Widen `_SYSTEM_PROMPT`**

In `src/envstate/env_classifier.py`, change:

```python
_SYSTEM_PROMPT = (
    "You classify a compact evidence bundle into environment obligations for running a repo's "
    "tests locally. Output ONLY a JSON object: {\"add_requirements\":[{id,type,name,layer,"
    "check_command,promotion,evidence_ref}], \"add_edges\":[{source,target,relation,hard}]}.\n"
    "type in {Service,Config,DataAsset}; id is 'service:<name>' / 'config:<VAR>' / 'data:<name>'; "
    "layer in {services,config}; promotion in {hint,candidate} (NEVER active); evidence_ref MUST be "
    "an evidence_id from the bundle. Edges connect an existing node (e.g. a pkg: or project: id from "
    "the bundle) to your new node. "
    "Some bundle hits include a \"node_id\" (e.g. \"pkg:psycopg\", \"project:foo\"). To link a "
    "new node to an existing one, add an edge whose source/target are those exact node_id values. "
    "Valid edge relations are ONLY: requires, alternative_to, conflicts_with (default requires). "
    "Do NOT invent other relations, and do NOT create a node per package. " + _GOAL
)
```

to:

```python
_SYSTEM_PROMPT = (
    "You classify a compact evidence bundle into environment obligations for running a repo's "
    "tests locally. Output ONLY a JSON object: {\"add_requirements\":[{id,type,name,layer,"
    "check_command,promotion,evidence_ref}], \"add_edges\":[{source,target,relation,hard}]}.\n"
    "type in {Service,Config,DataAsset,SystemLib,Tool}; id is 'service:<name>' / 'config:<VAR>' / "
    "'data:<name>' / 'syslib:<apt-package-name>' / 'tool:<apt-package-name>'; layer in "
    "{services,config,system,toolchain}; promotion in {hint,candidate} (NEVER active); evidence_ref "
    "MUST be an evidence_id from the bundle. A SystemLib/Tool proposal is a SOFT hint, not a "
    "certified need -- only propose one when the evidence names a SPECIFIC system package or tool "
    "the tests need (e.g. a README 'apt-get install' line, a conda dependency), and use the apt "
    "package name for <name> (not a soname, not a Python import name, not an arbitrary label). "
    "Edges connect an existing node (e.g. a pkg: or project: id from the bundle) to your new node. "
    "Some bundle hits include a \"node_id\" (e.g. \"pkg:psycopg\", \"project:foo\"). To link a "
    "new node to an existing one, add an edge whose source/target are those exact node_id values. "
    "Valid edge relations are ONLY: requires, alternative_to, conflicts_with (default requires). "
    "Do NOT invent other relations, and do NOT create a node per package. " + _GOAL
)
```

- [ ] **Step 4: Wire `raw_intake` AND `collect_declaration_context_hits` into the evidence bundle**

In `src/envstate/env_classifier.py`, inside `classify()`, change:
```python
            from python_deps.depgraph.static_collect import (
                collect_static_evidence, compact_bundle_json)
            from python_deps.depgraph.patch import parse_patch_proposal
            from python_deps.depgraph.patch_gate import admit_proposal
            from src.envstate.jsonutil import extract_json_object

            hits = collect_static_evidence(repo_path, graph)
            if not hits:
                return graph
```
to:
```python
            from python_deps.depgraph.static_collect import (
                collect_static_evidence, collect_declaration_context_hits,
                compact_bundle_json)
            from python_deps.depgraph.raw_intake import collect_raw_file_snippets
            from python_deps.depgraph.patch import parse_patch_proposal
            from python_deps.depgraph.patch_gate import admit_proposal
            from src.envstate.jsonutil import extract_json_object

            hits = (
                collect_static_evidence(repo_path, graph)
                + collect_raw_file_snippets(repo_path)
                + collect_declaration_context_hits(repo_path)
            )
            if not hits:
                return graph
```

(Both `collect_raw_file_snippets` and `collect_declaration_context_hits` degrade gracefully to `()` for a nonexistent/non-directory `repo_path` — `_raw_candidate_paths` checks `os.path.isdir` first, and `collect_declaration_context_hits` reads only files that don't exist there — so every existing test in this file that passes `"/nonexistent-repo"` is unaffected: concatenating empty tuples is a no-op. `bundle_ids` is still built by the single unchanged `frozenset(h.evidence_id for h in hits)` line further down `classify()`, so the newly-appended `decl.NN`/`conda.NN`/`raw.NN` ids automatically become grounding-valid evidence refs — no change needed there.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/test_env_classifier.py -q`
Expected: PASS (all existing + 4 new).

- [ ] **Step 6: Run the full test suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest -q`
Expected: PASS at (baseline + net new tests so far), 32 skipped, still exactly the 2 pre-existing PDF-dataset failures.

- [ ] **Step 7: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/envstate/env_classifier.py tests/test_env_classifier.py
git commit -m "feat(envstate): widen classifier _SYSTEM_PROMPT to allow soft SystemLib/Tool proposals + wire raw-prose and declaration/conda evidence into the bundle (cluster 2)"
```

---

### Task 10: `patch_gate.py` — stamp admitted nodes `discovered_by=CLASSIFIER`

**Files:**
- Modify: `src/python_deps/depgraph/patch_gate.py` (`apply_proposal`)
- Test: `tests/depgraph/test_patch_gate_apply.py` (append)

**Interfaces:** `apply_proposal(graph, proposal) -> ApplyResult` — same signature. The one behavior change: every newly-created requirement node is stamped `discovered_by=DiscoveredBy.CLASSIFIER` (was `DiscoveredBy.PROBE`) — the dedicated, attributable origin for "an LLM proposal the host admitted," replacing the previous overload where an admitted node was indistinguishable, in `discovered_by`, from a genuine `probe.py`/`ldd_probe.py` container discovery. Tasks 11/12 key their SOFT-preservation and emittability-normalization logic off this exact origin.

**Why this is needed (design doc Cluster 2 §3, quoted):** "give classifier-admitted nodes a distinct `discovered_by` — add `DiscoveredBy.CLASSIFIER` (schema.py) and have `patch_gate.apply_proposal` stamp it (today those nodes reuse `DiscoveredBy.PROBE` with `provenance=None`, indistinguishable from a real probe discovery, which hurts attribution)."

**Design note (an inspection finding, not a "fix an existing test" task):** grepped every `tests/depgraph/test_patch_gate*.py` file and `tests/depgraph/test_gsm_invariants_phase2a.py` for an assertion of the form `discovered_by is DiscoveredBy.PROBE` on an `apply_proposal`-*produced* node — **none exists**. The two literal `DiscoveredBy.PROBE` matches in this test tree (`test_gsm_invariants_phase2a.py:31`, `test_patch_gate_validate.py:90`) both construct a `Node(...)` fixture DIRECTLY, standing in for a node that already exists in the graph BEFORE `apply_proposal`/`validate_proposal` run — neither is asserting on `apply_proposal`'s own stamp. So this task does not need to "fix" any pre-existing assertion; it ADDS the first direct test of `apply_proposal`'s discovery-origin stamp, which happened to be untested before.

- [ ] **Step 1: Write the failing test**

Append to `tests/depgraph/test_patch_gate_apply.py`:

```python
def test_admitted_requirement_node_discovered_by_classifier():
    # construction-enrichment cluster 2 Sec 3: an admitted LLM proposal must be
    # attributable to the CLASSIFIER origin, not the generic PROBE tag (which
    # would be indistinguishable from a real container probe discovery).
    res = apply_proposal(_base(), _proposal())
    assert res.graph.get("syslib:libpq.so").discovered_by is DiscoveredBy.CLASSIFIER
```

(`DiscoveredBy`/`apply_proposal`/`_base`/`_proposal` are already imported/defined in this file — no new imports needed.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_patch_gate_apply.py -k test_admitted_requirement_node_discovered_by_classifier -q`
Expected: FAIL — `node.discovered_by` is `DiscoveredBy.PROBE` (the pre-fix stamp).

- [ ] **Step 3: Stamp `CLASSIFIER`**

In `src/python_deps/depgraph/patch_gate.py`, in `apply_proposal`, change:
```python
        g = g.with_node(Node(
            id=r.id, type=NodeType(r.type), name=r.name or r.id.split(":", 1)[-1],
            layer=Layer(r.layer), discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
            check_command=r.check_command, evidence=r.evidence_ref, data=data,
        ))
```
to:
```python
        g = g.with_node(Node(
            id=r.id, type=NodeType(r.type), name=r.name or r.id.split(":", 1)[-1],
            layer=Layer(r.layer), discovered_by=DiscoveredBy.CLASSIFIER, state=State.MISSING,
            check_command=r.check_command, evidence=r.evidence_ref, data=data,
        ))
```

- [ ] **Step 4: Run test to verify it passes, then the full patch_gate test files**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_patch_gate_apply.py tests/depgraph/test_patch_gate.py tests/depgraph/test_patch_gate_admit.py tests/depgraph/test_patch_gate_check_guard.py tests/depgraph/test_patch_gate_readonly.py tests/depgraph/test_patch_gate_validate.py tests/depgraph/test_gsm_invariants_phase2a.py -q`
Expected: PASS — confirmed by inspection (Step 1's Design note) that no other test in these files asserts `discovered_by is DiscoveredBy.PROBE` on an `apply_proposal`-created node, so nothing else regresses. Also confirmed by inspection that `src/envstate/repair_loop.py` (the only non-test caller of `apply_proposal`) never inspects `discovered_by` on the result.

- [ ] **Step 5: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the Task 8 count + 1 = 610 (Task 9 touched only `tests/test_env_classifier.py`, outside `tests/depgraph/`).

- [ ] **Step 6: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/patch_gate.py tests/depgraph/test_patch_gate_apply.py
git commit -m "fix(depgraph): patch_gate.apply_proposal stamps admitted nodes discovered_by=CLASSIFIER, not PROBE (cluster 2 Sec 3, attribution fix)"
```

---

### Task 11: `populate.py` — keep a classifier-admitted node SOFT

**Files:**
- Modify: `src/python_deps/depgraph/populate.py`
- Test: `tests/depgraph/test_populate_setup_commands.py` (append)

**Interfaces:** `populate_setup_commands(graph: DepGraph) -> DepGraph` — same signature as today (uniform-graph Phase 1). The one behavior change: a reciped node is forced to `Strength.HARD` UNLESS `node.discovered_by is DiscoveredBy.CLASSIFIER` — Task 10's `patch_gate.apply_proposal` stamp, a clean, dedicated, named origin. Every existing test fixture in this file uses `discovered_by=RESOLVER`/`STATIC_SCAN`/`PROBE` (real probe discoveries, never `CLASSIFIER`), so the new condition is False for all of them and every existing assertion is unaffected.

**Design note (a cleaner discriminator than an earlier draft's heuristic):** an earlier draft of this plan (written before Task 10 existed) proposed keying this rule off `discovered_by=PROBE with provenance is None` — reasoning that `patch_gate.apply_proposal` was the only Node constructor in the codebase that stamped `PROBE` without also setting `provenance`. That heuristic worked, but coupled `populate.py` to an accidental, undocumented property of `patch_gate.py`'s current implementation (flagged, in that draft, as a real coupling risk for whoever touched `patch_gate.py` next). Task 10 resolves this at the root: `patch_gate.apply_proposal` now stamps a NAMED, dedicated origin (`DiscoveredBy.CLASSIFIER`) instead. This task keys off that clean predicate directly — no provenance heuristic, no coupling risk left to flag.

- [ ] **Step 1: Write the failing tests**

Append to `tests/depgraph/test_populate_setup_commands.py`:

```python
def _classifier_admitted_syslib():
    # Mirrors patch_gate.apply_proposal's Node(...) call (Task 10):
    # discovered_by=DiscoveredBy.CLASSIFIER -- the dedicated, attributable
    # origin an LLM-admitted node carries, as opposed to a real probe.py/
    # ldd_probe.py discovery (discovered_by=PROBE).
    return Node(id="syslib:ffmpeg", type=NodeType.SYSTEM_LIB, name="ffmpeg",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.CLASSIFIER,
                state=State.MISSING, chosen_fix="apt:ffmpeg")


def test_classifier_admitted_node_keeps_soft_strength_but_still_gets_setup_commands():
    n = populate_setup_commands(DepGraph(nodes=(_classifier_admitted_syslib(),))).get("syslib:ffmpeg")
    assert n.setup_commands == ("apt-get install -y --no-install-recommends ffmpeg",)
    assert n.strength is Strength.SOFT


def test_real_probe_node_still_gets_hard():
    # A REAL probe.py/ldd_probe.py discovery is discovered_by=PROBE, never
    # CLASSIFIER -- must still harden.
    n = Node(id="syslib:libgl1", type=NodeType.SYSTEM_LIB, name="libGL.so.1",
             layer=Layer.SYSTEM, discovered_by=DiscoveredBy.PROBE, state=State.MISSING,
             chosen_fix="apt:libgl1", provenance="probe (observed)")
    out = populate_setup_commands(DepGraph(nodes=(n,))).get("syslib:libgl1")
    assert out.strength is Strength.HARD
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_populate_setup_commands.py -q`
Expected: FAIL — `test_classifier_admitted_node_keeps_soft_strength_but_still_gets_setup_commands` fails (`n.strength` is `Strength.HARD` under the current unconditional rule).

- [ ] **Step 3: Add the rule**

In `src/python_deps/depgraph/populate.py`, add `DiscoveredBy` to the schema import:

```python
from python_deps.depgraph.schema import DepGraph, DiscoveredBy, Node, NodeType, Strength
```

Change `populate_setup_commands`:

```python
def populate_setup_commands(graph: DepGraph) -> DepGraph:
    """Return a NEW graph in which every reciped node lacking setup_commands gets
    its install command. strength becomes HARD UNLESS the node is classifier-
    admitted (discovered_by=DiscoveredBy.CLASSIFIER -- the dedicated origin
    patch_gate.apply_proposal stamps on every LLM-admitted node, Task 10,
    construction-enrichment cluster 2 Sec 3), so an LLM-proposed SystemLib/Tool
    installs proactively without being hardened into a blocking obligation
    ("the LLM only ever proposes SOFT" holds structurally end to end).
    Idempotent; leaves Service/Config/DataAsset and already-populated nodes
    untouched.
    """
    new = graph
    for node in graph.nodes:
        if node.setup_commands:
            continue
        if not _is_reciped(node):
            continue
        cmd = _command_for(node)
        if not cmd:
            continue
        strength = node.strength if node.discovered_by is DiscoveredBy.CLASSIFIER else Strength.HARD
        new = new.with_node(replace(node, setup_commands=(cmd,), strength=strength))
    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/test_populate_setup_commands.py -q`
Expected: PASS (all existing + 2 new — including the pre-existing `test_fills_reciped_package_with_pinned_no_deps_pip`/`test_fills_reciped_syslib_with_apt`/`test_fills_reciped_tool_with_apt`, unaffected since none use `discovered_by=CLASSIFIER`).

- [ ] **Step 5: Run the full depgraph suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at the Task 10 count + 2 = 612.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/python_deps/depgraph/populate.py tests/depgraph/test_populate_setup_commands.py
git commit -m "fix(depgraph): populate_setup_commands keeps a classifier-admitted node SOFT (cluster 2 Sec 3)"
```

---

### Task 12: `env_classifier.py` — `normalize_emittability`

**Files:**
- Modify: `src/envstate/env_classifier.py`
- Test: `tests/test_env_classifier.py` (append)

**Interfaces:** Produces: `normalize_emittability(graph: DepGraph, admitted_ids: frozenset[str]) -> DepGraph` — for each id in `admitted_ids` whose node is `SystemLib`/`Tool`, is `discovered_by=DiscoveredBy.CLASSIFIER` (Task 10's stamp), and lacks a `chosen_fix`, derives `chosen_fix=apt:<id-suffix>` (the same convention `declaration_mine.py`'s STATIC_DECLARATION nodes use). Wired into `classify()` immediately after `admit_proposal` succeeds. Consumes: Task 11's `populate.py` rule (this task does NOT call `populate_setup_commands` itself — it only derives `chosen_fix`; `render_build_script`'s existing single populate call site, uniform-graph Phase 1, does the rest later and is where the SOFT-preserving rule actually applies).

This delivers the mechanism the cluster-2 transition note (above Task 7) referenced: a reduced-scope ADVISORY-only alternative (widen the prompt, admit the node, but never make it installable) was considered and rejected because it delivers no proactive install. This task is what actually makes a widened proposal install.

**Why `normalize_emittability` does not call `populate_setup_commands`:** the uniform-graph Phase 1 plan established `render_build_script` as the ONE safe call site for `populate_setup_commands` (it always runs after every `chosen_fix` mutation, avoiding the "Stage 2.5" staleness bug where a command computed too early survives a later mutation via the idempotency guard). Calling `populate_setup_commands` again here — before container-stage `reconcile_apt_names` has a chance to run on a *future* pipeline invocation, or before any later patch touches this node — would reopen exactly that bug. `normalize_emittability` therefore does the strict minimum (derive `chosen_fix`) and leaves command generation entirely to the existing single call site; Task 11's `discovered_by is DiscoveredBy.CLASSIFIER` rule is what lets that later call still know to keep the node SOFT.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_env_classifier.py`:

```python
def test_normalize_emittability_derives_apt_fix_keeps_soft():
    g = _graph_with_ffmpeg_pkg()
    llm_json = json.dumps({"requirements": [
        {"id": "syslib:ffmpeg", "type": "SystemLib", "name": "ffmpeg", "layer": "system",
         "state": "candidate", "check_command": None, "evidence_refs": ["pkg.00"]}]})
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    node = out.get("syslib:ffmpeg")
    assert node.chosen_fix == "apt:ffmpeg"
    assert node.strength is Strength.SOFT     # normalize_emittability never hardens

    # and it is now genuinely installable: populate_setup_commands (render time,
    # the single call site per uniform-graph Phase 1) fills it in, still SOFT.
    from python_deps.depgraph.populate import populate_setup_commands
    populated = populate_setup_commands(out).get("syslib:ffmpeg")
    assert populated.setup_commands == ("apt-get install -y --no-install-recommends ffmpeg",)
    assert populated.strength is Strength.SOFT


def test_normalize_emittability_does_not_touch_existing_deterministic_node():
    # A RESOLVER-origin syslib with a REAL chosen_fix must never be overwritten,
    # even when the LLM independently proposes the same id (apply_proposal
    # dedups; normalize_emittability's discovered_by-is-CLASSIFIER guard is the
    # second, independent line of defense).
    g = DepGraph().with_node(Node(id=package_id("ffmpeg-python", "0.2.0"), type=NodeType.PACKAGE,
        name="ffmpeg-python", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER, version="0.2.0"))
    g = g.with_node(Node(id="syslib:ffmpeg", type=NodeType.SYSTEM_LIB, name="ffmpeg",
        layer=Layer.SYSTEM, discovered_by=DiscoveredBy.RESOLVER, state=State.UNKNOWN,
        chosen_fix="apt:ffmpeg-real", provenance="predicted (native-risk)"))
    llm_json = json.dumps({"requirements": [
        {"id": "syslib:ffmpeg", "type": "SystemLib", "name": "ffmpeg", "layer": "system",
         "state": "candidate", "evidence_refs": ["pkg.00"]}]})
    out = make_construction_classifier(lambda m: llm_json)(g, "/nonexistent-repo")
    node = out.get("syslib:ffmpeg")
    assert node.chosen_fix == "apt:ffmpeg-real"       # untouched
    assert node.discovered_by is DiscoveredBy.RESOLVER
    assert node.provenance == "predicted (native-risk)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/test_env_classifier.py -k normalize_emittability -q`
Expected: FAIL — `node.chosen_fix` is `None` (the function doesn't exist / isn't wired yet).

- [ ] **Step 3: Add `normalize_emittability` and wire it into `classify()`**

In `src/envstate/env_classifier.py`, add the function after `_sanitize` (before `make_construction_classifier`):

```python
def normalize_emittability(graph, admitted_ids):
    """Derive chosen_fix=apt:<id-suffix> for classifier-admitted SystemLib/Tool
    nodes lacking one, so they are reciped (installable) instead of inert
    (construction-enrichment cluster 2 Sec 3).

    _sanitize drops add_providers (an LLM proposal never carries a provider),
    so an admitted syslib:/tool: node has no chosen_fix and, per populate's
    reciped test, would otherwise never be installed -- proposed but inert.
    This derives chosen_fix=apt:<id-suffix> (the same convention
    declaration_mine.py's STATIC_DECLARATION nodes use) for each admitted
    node lacking one.

    Scoped to ``admitted_ids`` (this call's own admissions, not the whole
    graph) AND additionally requires ``discovered_by is DiscoveredBy.CLASSIFIER``
    (patch_gate.apply_proposal's dedicated origin for every LLM-admitted node,
    Task 10 -- a clean, named predicate, replacing an earlier draft's
    ``provenance is None`` heuristic) so a pre-existing, already-fixed
    deterministic node that happens to share an id (apply_proposal dedups by
    id) is never mistaken for a fresh classifier admission.

    Does NOT populate setup_commands itself -- render_build_script's existing
    single populate_setup_commands call site (uniform-graph Phase 1) does
    that later; populate.py recognizes a classifier-admitted node by
    discovered_by=DiscoveredBy.CLASSIFIER and keeps it SOFT there (Task 11).
    """
    from python_deps.depgraph.schema import DiscoveredBy, NodeType

    new = graph
    for node_id in admitted_ids:
        node = new.get(node_id)
        if node is None or node.type not in (NodeType.SYSTEM_LIB, NodeType.TOOL):
            continue
        if node.discovered_by is not DiscoveredBy.CLASSIFIER or node.chosen_fix is not None:
            continue
        apt_name = node.id.split(":", 1)[-1]
        new = new.with_node(replace(node, chosen_fix=f"apt:{apt_name}"))
    return new
```

In `classify()`, change:

```python
            result = admit_proposal(graph, proposal, known_evidence_ids=bundle_ids)
            if not result.accepted:
                logger.warning("env classifier proposal rejected: %s", result.errors)
                return graph
            return result.graph
```

to:

```python
            result = admit_proposal(graph, proposal, known_evidence_ids=bundle_ids)
            if not result.accepted:
                logger.warning("env classifier proposal rejected: %s", result.errors)
                return graph
            admitted_ids = frozenset(r.id for r in proposal.add_requirements)
            return normalize_emittability(result.graph, admitted_ids)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/test_env_classifier.py -q`
Expected: PASS (all existing + 2 new). Explicitly re-run the EXISTING `test_classifier_appends_soft_service_node`, `test_classifier_drops_ungrounded_requirement`, `test_classifier_returns_graph_unchanged_on_junk`, `test_one_illegal_promotion_does_not_void_valid_siblings`, `test_non_read_only_check_command_req_is_dropped_not_voiding`, `test_invalid_relation_edge_dropped_not_voiding`, `test_valid_relation_edge_survives_soft` (Service/Config/DataAsset paths) — `normalize_emittability` must be a no-op for all of them (`node.type not in (SYSTEM_LIB, TOOL)` skips every Service/Config/DataAsset node immediately).

- [ ] **Step 5: Run the full test suite**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest -q`
Expected: PASS, still exactly the 2 pre-existing PDF-dataset failures, 32 skipped, count = full baseline + all tests added across this plan's 12 tasks (indicative total: 1288 + 41 = 1329 — see Self-Review §4).

- [ ] **Step 6: Run the full depgraph suite one final time**

Run: `cd /Users/john/john-planner-v3-core && python3 -m pytest tests/depgraph/ -q`
Expected: PASS at 612 — record the final count in the commit body for traceability.

- [ ] **Step 7: Commit**

```bash
cd /Users/john/john-planner-v3-core
git add src/envstate/env_classifier.py tests/test_env_classifier.py
git commit -m "feat(envstate): normalize_emittability makes classifier-admitted SystemLib/Tool nodes installable while staying SOFT (cluster 2 Sec 3, final wiring)"
```

---

## Deferred to later

Recorded here (not designed, not implemented) so they are visible before someone re-derives them from scratch:

- **`sdist` build-config mining** (`pyproject.toml` `[build-system].requires`, `setup.py` `ext_modules`) **and `Requires-External`** — natural cluster-1 extensions that recover *specific* headers authoritatively, but need an sdist download/unpack or wheel-metadata fetch; heavier and separable from this iteration's pure-local-repo-read stages.
- **v2 compact extracted-signal bundle** — this plan ships v1 raw-file intake (`raw_intake.collect_raw_file_snippets`, per-file/total char caps). A structured extraction pass (e.g. detecting install-command blocks specifically, not just a character window) is future work.
- **The HOST-vs-target platform-marker bug** (`wheel_oracle._wheel_matches_platform` evaluates `target_platform` without checking it against the HOST the resolve runs on) — explicitly preserved by Task 1's "behavior preserved exactly" extraction. Flagged in the module docstring; belongs to the separate correctness track the construction-graph correctness audit (2026-07-01) identified, not this enrichment.
- **Cluster-3 reactive native-dep grounding** (the libGL soname→apt path fully closed at *build time*, beyond what `ldd_probe`/`declaration_mine` already give) — explicitly out of scope per the design doc.
- **Graph-schema finalization** (demote `chosen_fix`, fold `fix_candidates`, patch-contract invariants) and **the live/Docker execution-path command migration** (`--no-deps` decision) — explicitly out of scope per the design doc; unrelated to construction enrichment.
- **Promotion of declaration nodes by evidence weight beyond hard/soft** — e.g. a package declared in three separate CI workflows is not treated as "more certain" than one declared once. Not required by the current strength epistemics table.

---

## Self-Review

**1. Spec coverage (clusters 1 + 2 of the construction-enrichment design):**
- Cluster 1a — reconnect the wheel-oracle prior, delete the curated table → Tasks 1–2. ✓
- Cluster 1b — deterministic declaration-mining stage (+ its `STATIC_DECLARATION` schema prerequisite), Critical reconciliation-guard fix → Tasks 3–6. ✓
- Cluster 2 — raw-prose intake, widened SystemLib/Tool allowlist, `CLASSIFIER` attribution + emittability fix (+ its `CLASSIFIER` schema prerequisite, added alongside `STATIC_DECLARATION` in Task 3) → Tasks 3, 7–12. ✓
- Strength epistemics table honored exactly: Package (manifest) → HARD via reciped+populate (unchanged); build-essential from `build_from_source` → HARD (reciped, Task 2); apt declaration → HARD (reciped, Task 4; survives probe reconciliation via Task 6's Critical fix); LLM SystemLib/Tool → SOFT (attributed via Task 10's `CLASSIFIER` stamp; made installable via Task 12's `normalize_emittability`; kept SOFT via Task 11's `populate.py` rule); `ldd_probe` soname → HARD (unchanged). ✓
- "No curated package→syslib table" — verified deleted (Task 2) with every downstream reference (tests, docstrings) updated, not just the two files the brief named. ✓
- "depgraph stays LLM-free" — `wheel_oracle.py`/`declaration_mine.py`/`raw_intake.py`/`patch_gate.py`/`populate.py` import only stdlib + `python_deps.depgraph.*`; the LLM bridge stays confined to `src/envstate/env_classifier.py`. ✓
- "Host is sole SATISFIED writer; LLM proposes SOFT only" — `normalize_emittability` never touches `state`; Task 11's rule is the only place `strength` is decided for these nodes, and it decides SOFT for classifier-admitted nodes (identified via Task 10's dedicated `CLASSIFIER` origin, not a heuristic). ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step shows complete code, including the ripple-effect files (`test_build.py`, `test_ldd_probe_docker.py`, `config_tables.py`) an earlier design brief's "note" undersold — Task 2 spells out every changed assertion verbatim rather than saying "update the tests." Every function this plan builds is also CONSUMED by it: both `collect_raw_file_snippets` (Task 7) and `collect_declaration_context_hits` (Task 8) are wired into `classify()`'s bundle in Task 9, each with a test proving its hits (including the spec-critical `conda_declaration` hit) reach the bundle and land in `bundle_ids` — no "built but unwired" evidence collector is left behind.

**3. Dependency ordering respected:** Task 1 before Task 2 (wheel_oracle exists before seed.py's docstring/behavior references it). Task 3 (schema: `STATIC_DECLARATION` + `CLASSIFIER`, added together) before Task 4 (`declaration_mine.py` consumes `STATIC_DECLARATION`) AND before Task 10 (`patch_gate.py` consumes `CLASSIFIER`) — pulling both enum members forward into one early task means neither later consumer has a schema-ordering dependency left to track. Task 4 before Task 5 (`build.py` wires an already-tested function). Task 5 before Task 6 (the end-to-end reconciliation test needs stage 3c actually running). Task 6's guard-widening is proven both as a unit test (`test_probe.py`) and an end-to-end pipeline test (`test_build.py`) — the two-lane proof the brief asked for. Task 7 (`raw_intake.py`) has no dependency on Task 8 (`static_collect.py`'s decl-hits) or vice versa — both are independent evidence-assembly modules that an earlier draft bundled into one task before this revision split them. Task 9 depends on BOTH Task 7 AND Task 8 — it wires `collect_raw_file_snippets` (Task 7) and `collect_declaration_context_hits` (Task 8) into `classify()`'s bundle together; wiring the latter is spec-load-bearing (it is the ONLY path conda `environment.yml` deps reach the graph, since stage 3c excludes them). Task 10 (`patch_gate.py` stamps `CLASSIFIER`) before Task 11 (`populate.py`'s SOFT rule keys off that exact stamp) before Task 12 (`normalize_emittability`'s guard also keys off `discovered_by is CLASSIFIER`, and its first new test asserts the populate-time SOFT-preservation behavior directly, end to end through `classify()` → `apply_proposal` → `normalize_emittability` → `populate_setup_commands`).

**4. Every task leaves `tests/depgraph/ -q` green (indicative counts — see Global Constraints' binding-gate note).** Task-local counts, re-derived by inspection against the actual current test files (not assumed): 577 (measured baseline) → **585** (Task 1, +8 — one fewer than an earlier draft's claimed +9, because `risk_from_packages` no longer carries a `test_risk_from_packages_skips_local_source` case) → **580** (Task 2, **−5**, not "unchanged" as an earlier draft claimed — see Task 2 Step 12) → **582** (Task 3, +2 — both new enum members, not +1) → **600** (Task 4, +18 — 14 parser-contract tests + 4 folded `_is_toolchain_apt` tests moved in from the now-removed separate `ids.py` task, not +15) → **600** (Task 5, +0) → **602** (Task 6, +2) → **606** (Task 7, +4, new `raw_intake.py` module) → **609** (Task 8, +3) → [Task 9 lives in `tests/test_env_classifier.py`, outside `tests/depgraph/` — +0 here, +4 to the full suite] → **610** (Task 10, +1, new) → **612** (Task 11, +2) → [Task 12, outside `tests/depgraph/` — +0 here, +2 to the full suite]. **Final `tests/depgraph/ -q`: 612 passed.** Full-suite indicative total: 1288 + 35 (depgraph) + 6 (env_classifier: +4 Task 9, +2 Task 12) = **1329 passed**, 32 skipped, still exactly the 2 pre-existing PDF-dataset failures. The full-suite run is additionally re-run at Tasks 9 and 12 (the two envstate-touching tasks) to catch any cross-directory regression.

**5. Type/interface consistency:** `mine_declarations(graph, repo_path) -> DepGraph` and `apt_hits_for_repo(repo_path) -> tuple[tuple[str,str,int],...]` match between `declaration_mine.py` (Task 4) and their callers (`build.py` Task 5, `static_collect.py` Task 8). `_is_toolchain_apt(name) -> bool` (folded into Task 4, private) matches its sole consumer's usage within the same module — `ids.py` is untouched, so no cross-module interface exists for it at all. `collect_raw_file_snippets(repo_path) -> tuple[DeterministicHit, ...]` (Task 7) and `DeterministicHit` (imported from `static_collect.py`, one-directional) match; `collect_declaration_context_hits(repo_path) -> tuple[DeterministicHit, ...]` (Task 8) matches the same dataclass. `apply_proposal`'s `discovered_by=DiscoveredBy.CLASSIFIER` stamp (Task 10), `populate_setup_commands`'s `discovered_by is DiscoveredBy.CLASSIFIER` check (Task 11), and `normalize_emittability`'s `discovered_by is not DiscoveredBy.CLASSIFIER` guard (Task 12) all key off the exact same enum member — designed together as one mechanism, tested together end-to-end in Task 12's first new test.
