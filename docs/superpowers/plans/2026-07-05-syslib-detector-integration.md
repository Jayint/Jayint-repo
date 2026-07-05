# System-Package Detector Integration (v3-core → 2-phase branch) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port v3-core's capability-keyed, predict-then-observe system-package (OS / native-library) detection onto the current `john-v3-multi-lang` branch **without disturbing the clean two-phase architecture**, and land it gated by the `package_installability` eval (also relocated into this branch's `src/eval/` layout).

**Architecture:** v3-core's detection is entirely a Phase-B / downstream concern — root selection is *not* coupled to it. So every module lands as a **port-not-adapter** and is wired at ONE seam: a proactive prior **pre-pass** co-located with `seed_wheel_oracle_prior` (build.py ~567), just before `_python_native_obligations`. Priors seed `RESOLVER`/`UNKNOWN` nodes keyed by `capability_id`/`syslib_id`; the existing reactive probes (`ldd_probe`, `import_probe`) confirm or drop them via the already-present `reconcile_predicted` id-collision mechanism. Phase A (declared-roots fixpoint, RECORD-union oracle, imports-as-audit) is **never touched**.

**Tech Stack:** Python 3.11, pytest, Docker (`python:3.11-slim-bookworm`, `--platform linux/amd64`), `uv`, `packaging`, `pyelftools` (ELF `DT_NEEDED` read in `wheel_inspect.py`), `apt`/`apt-file` (container).

## Global Constraints

- **commit-local, NEVER push.** Each task ends in a local commit; do not push.
- **Two-phase invariant.** Do NOT modify `_phase_a_fixpoint`, the RECORD-union coverage oracle (`coverage.py`), declared-roots (`roots.py`/`resolve.py`), or imports-as-audit. All detection is **additive** in Phase B / the aux-once pre-pass.
- **Port-not-adapter.** Bring v3-core modules via `git show v3-core:<path>` verbatim; the ONLY edits are import-path rewrites. Do NOT bring v3-core's `build.py` staging (its old orchestration) — wire at HEAD's seam only.
- **Additive, never SATISFIED-at-seed.** Proactive priors create nodes with `discovered_by=DiscoveredBy.RESOLVER`, `state=State.UNKNOWN`. Host certification (`certify_all`) is the only place state flips.
- **THE apt=0 invariant (load-bearing).** apt-BREAKING over-prediction (`failure_phase.apt`) is the ONLY failure the detector can *cause* — the reactive loop only ADDS deps, never removes. The ported Bucket-B.1 guards (`_apt_installable` install-simulate, `_resolve_source` python3-* validation) are what keep it at 0. Every gate below asserts `failure_phase.apt == 0`; a nonzero value means a B.1 guard didn't fire (usually the container executor wasn't threaded into `seed_build_deps`).
- **No suite regression.** Baseline (re-established 2026-07-06 at branch HEAD `9c1ef4a`) is **1209 passed, 1 skipped** on `tests/depgraph tests/pkg_layer tests/eval` (the plan's older `1179`/`1182`/etc. absolute counts predate branch advances — treat them as illustrative "baseline + N added" deltas; the real no-regression reference is **1209 passed / 1 skipped**). Every task re-runs it; any failure must be shown identical at branch base (pre-existing) or fixed.
- **Detector import path is unchanged.** Ported modules import each other and the pipeline as `python_deps.depgraph.X` (the production path). Only the *eval's* internal cross-refs get the `src.eval.` prefix.
- **Eval layout:** code under `src/eval/package_installability/`, tests mirror under `tests/eval/package_installability/`, run artifacts under gitignored `outputs/package_installability/`. Committed data (`answer_keys.json`, `seed_records.json`) travels with the module.
- **Docker/`uv`/pytest commands run in the FOREGROUND only** (never backgrounded — prior agents stalled on backgrounded Docker/disk walks).
- **Log each landed task** to `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md` (Observation→Why→What→Verification).

---

## File Structure

**Ported detection modules (new on HEAD, from `v3-core`, `src/python_deps/depgraph/`):**
- `syslib.py` — `make_syslib_node` factory (dedups HEAD's two `_make_syslib_node` copies)
- `os_resolver.py` — capability-keyed `resolve(ObservedNeed) → [ProviderCandidate]` (supersedes `apt_resolve.py`)
- `wheel_inspect.py` — pyelftools ELF `DT_NEEDED` reader (incl. bundled `.libs/`)
- `wheel_preflight.py` — `wheel_preflight_probe` pre-install wheel soname priors
- `pep725.py` — PEP 725 `[external]` DepURL reader
- `debian_builddeps.py` — Debian `Build-Depends` source mapping
- `build_deps.py` — `seed_build_deps`/`build_dep_prior` (the `-dev` prior; the Bucket-B fixes)
- `artifact_map.py` — `resolve_artifact_map` = the **branch oracle** (pipeline stage 3): wheel|sdist per package, stamped onto `Node.build_from_source`. **REQUIRED** — `predict.py` imports it directly, and `wheel_preflight_probe` + `seed_build_deps` both read the `build_from_source` stamp it sets. It becomes the build-path stamp (matching v3-core's stage order); HEAD's `native_risk_from_lock` stamp is superseded for this path.

**Ported supporting modules (from `v3-core`, additive-merge — NOT verbatim overwrite):**
- `failure_signatures.py` — **net-new file** (absent on HEAD); `extract_needs` used by the `probe.py` migration
- `ids.py` — additive: `capability_id` (dispatch) + `header_id`/`binary_id`/`pkgconfig_id`/`linker_id`/`apt_build_id` (`syslib_id` already on HEAD)
- `executor.py` — additive: `DockerExecutor.__init__(platform=…)` → `docker run --platform <p>` (commit `8ab73a6`; also required by the eval move and `_apt_installable`)

**Retired:** `apt_resolve.py` (superseded by `os_resolver.py`).

**Modified on HEAD:** `ldd_probe.py`, `probe.py` (migrate ALL apt authorities + dedup node factory + adopt `failure_signatures.extract_needs`), `build.py` (pre-pass wiring — merge, HEAD-only imports), `seed.py` (specific-first/generic-fallback note), `tables.py` (delete ONLY the os_resolver-superseded apt tables — `NATIVE_LIB_TO_APT`/`TOOL_TO_APT`/`apt_for_soname`/`apt_for_tool` — LAST, after repointing `probe.py`; KEEP `CLI_TOOL_TO_APT`/`apt_for_cli_tool`/`NATIVE_RISK_PACKAGES`). `subprocess_scan.py` is **UNCHANGED** (its `CLI_TOOL_TO_APT` runtime-CLI authority is not superseded — see the scope correction in Task 1.2).

**MUST NOT overwrite (HEAD is newer / canonical):** `schema.py` (HEAD has `ecosystem`, `DiscoveredBy.AUDIT`, `DepGraph.without_edge`; v3-core lacks them — no syslib change needed, so leave it), `target_env.py` (HEAD superset: interpreter-impl trio from the marker-env fix). A blind `git show v3-core:` copy of either REGRESSES the branch.

**Eval relocation:** `evals/package_installability/` → `src/eval/package_installability/`; tests → `tests/eval/package_installability/`; artifacts → `outputs/package_installability/`.

**Ordering:** Phase 0 (eval move) may land first — its docker-free tests do not need the detector, BUT the eval's `--run` and `_apt_installable` both need `executor.py`'s `--platform` param, so land the `executor.py` merge as a Phase-0 prerequisite (Task 0.0). Phases 1→2→3 are strictly ordered (each ports what the next wires). The full eval `--run` gate is the Phase-3 capstone.

---

## Bucket-B / B.1 fix inventory (verified 2026-07-06 by v3-core commit audit)

All **7 Bucket-B + B.1 fixes live entirely in two files** — a verbatim port of `build_deps.py` + `debian_builddeps.py` at v3-core HEAD (`d6d7e54`) captures every one. They are the detector quality the `package_installability` eval measures; the eval gate (Task 3.3) is what proves the port preserved them.

| id | commit | file:function | what it does |
|---|---|---|---|
| B1 | `7746579` | `build_deps.build_dep_prior`/`seed_build_deps` | delete `BROAD_THRESHOLD` tight/broad split — ALL Debian `Build-Depends` names become always-seeded `apt_directives` (no passive pool) |
| B2 | `76ffb14` | `debian_builddeps.is_system_lib` | allowlist→denylist: keep real build tools (`swig`/`cargo`/`proj-bin`); add `librust-` to machinery prefix (drop vendored-crate shadows) |
| B3 | `a259def` | `build_deps.seed_build_deps` | baseline `binary:pkg-config` for EVERY source-built package (Debian omits it as buildd-assumed; slim lacks it); delete dead `_expand_needs` |
| B-min | `d6d7e54` | `build_deps.build_dep_prior` | `covered.update(("pkg-config","pkgconf"))` so an explicit pkg-config token isn't double-seeded vs the B3 baseline |
| **B.1 Fix1** | `61411f7` | `debian_builddeps._resolve_source` | source validation: accept a Debian source only if it has `Build-Depends:` AND its `Binary:` lists a `python3-*` pkg → rejects same-named non-Python sources (Lisp `cffi`, `cups`). New: `_builds_python3_binary`, `_is_python_source_stanza` |
| **B.1 Fix2** | `d7ea03a` | `build_deps.build_dep_prior` | install-simulate guard: `_apt_installable(debian, executor)` runs `apt-get install -s <set>`; if rc≠0 DROP the whole Debian set (fall back to `forced_apt`) — a kitchen-sink source (uWSGI) that can't jointly install no longer breaks the build. New: `_apt_installable` |
| **B.1 min** | `cf847f8` | `build_deps._apt_installable` | shlex-quote apt names in the `apt-get install -s` command |

**Load-bearing wiring for B.1:** `_apt_installable` and `_resolve_source` run **inside `build_dep_prior`, before any `aptdep:` node is written**, and `_apt_installable` is a live `executor.run("apt-get install -s …")` — so `seed_build_deps` MUST receive the **container** `DockerExecutor` (Task 3.2 does: `seed_build_deps(graph, container_executor)`). Platform-correctness is inherited from that executor's `--platform` (Task 0.0), no extra plumbing.

---

## Task 0.0: Merge `executor.py`'s `--platform` param (prerequisite)

**Files:**
- Modify: `src/python_deps/depgraph/executor.py` (additive `platform` param on `DockerExecutor`)
- Modify/create: `tests/depgraph/test_executor.py` (assert `--platform` reaches the docker cmd)

**Interfaces:**
- Produces: `DockerExecutor(image, *, platform: str | None = None)` emitting `docker run --platform <p> …`.
- Consumed by: the eval move (`--platform linux/amd64`) AND `_apt_installable` (via the container executor).

- [ ] **Step 1: Read v3-core's version to see the exact param + call shape**

```bash
git show v3-core:src/python_deps/depgraph/executor.py | grep -n "platform" 
git show 8ab73a6 -- src/python_deps/depgraph/executor.py   # the additive commit
```

- [ ] **Step 2: Apply the additive param to HEAD's `DockerExecutor` (merge, don't overwrite — HEAD may have diverged)**

Add `platform: str | None = None` to `__init__`, store it, and inject `--platform <platform>` into the `docker run`/`docker create` argv when set. Default `None` = current behavior (byte-identical for callers that don't pass it).

- [ ] **Step 3: Test it**

```python
# tests/depgraph/test_executor.py
def test_platform_flag_in_docker_cmd(monkeypatch):
    calls = []
    # monkeypatch the subprocess runner to capture argv
    ex = DockerExecutor("python:3.11-slim-bookworm", platform="linux/amd64")
    # ... assert "--platform" and "linux/amd64" appear in the run argv; absent when platform=None
```

- [ ] **Step 4: Run + full suite**

```bash
python3 -m pytest tests/depgraph/test_executor.py -q
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
```
Expected: PASS; full suite `1179 passed` (additive, no behavior change for existing callers).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/executor.py tests/depgraph/test_executor.py
git commit -m "feat(depgraph): additive --platform param on DockerExecutor (prereq for eval + apt-simulate)"
```

---

## Task 0.1: Relocate the `package_installability` eval harness

**Files:**
- Move: `evals/package_installability/*` → `src/eval/package_installability/*` (11 files incl. `answer_keys.json`, `seed_records.json`)
- Modify: `src/eval/package_installability/__main__.py` (imports, output defaults, bootstrap)
- Modify: every `.py` in the package (internal `evals.package_installability` → `src.eval.package_installability`)

**Interfaces:**
- Produces: an importable `src.eval.package_installability` package; CLI `python3 -m src.eval.package_installability --run/--score/--seed/--derive/--vet`.
- Consumes (later, when present): `python_deps.depgraph.{target_env,artifact_map,seed,build_deps,debian_builddeps,os_resolver}` via `predict.py` — resolves progressively as Phases 1–3 land.

- [ ] **Step 1: Bring the eval from v3-core into the new location** (it lives on v3-core, NOT this branch)

```bash
# the harness exists only on v3-core; check it out at its old path, then relocate:
git checkout v3-core -- evals/package_installability
git mv evals/package_installability src/eval/package_installability
rmdir evals 2>/dev/null || true
ls src/eval/package_installability   # expect: __init__ __main__ answer_key answer_keys.json corpus curation gate predict run score seed_records.json
```

- [ ] **Step 2: Rewrite internal imports**

Replace every `evals.package_installability` with `src.eval.package_installability` across the package:

```bash
grep -rl "evals\.package_installability" src/eval/package_installability/
# For each file, edit the import lines, e.g.:
#   from evals.package_installability.corpus import CORPUS
# ->
#   from src.eval.package_installability.corpus import CORPUS
```

Leave `from python_deps.depgraph.… import …` lines (the detector path) UNCHANGED.

- [ ] **Step 3: Add the dual-bootstrap + retarget output defaults in `__main__.py`**

At the top of `src/eval/package_installability/__main__.py`, before the package imports:

```python
import pathlib, sys
_ROOT = pathlib.Path(__file__).resolve().parents[3]   # was parents[2] under evals/ — DEPTH +1
sys.path.insert(0, str(_ROOT / "src"))                 # -> python_deps.depgraph.* importable
sys.path.insert(0, str(_ROOT))                         # -> src.eval.* importable
```

Retarget the default output directory (was `evals/package_installability/outputs/`):

```python
_OUT_DIR = _ROOT / "outputs" / "package_installability"
# wherever --out/--checkpoint default to a path, root them at _OUT_DIR
```

- [ ] **Step 4: Fix the depth `parents[N]` gotcha everywhere**

The package moved from depth 2 (`evals/pkg/`) to depth 3 (`src/eval/pkg/`). Audit and bump:

```bash
grep -rn "parents\[" src/eval/package_installability/
# Any parents[N] that computed repo-root or src/ must have N incremented by 1.
```

Load committed data via `Path(__file__).parent` (unchanged — travels with the module):
`answer_keys.json`, `seed_records.json`.

- [ ] **Step 5: Add `--only` / `--stratum` corpus-filter flags (fast per-phase spot-checks)**

The eval runs all 70 rows (~30 min); a filter lets you spot-check a handful in ~90s during debugging, then run the full set for the real gate. Add a docker-free `select_corpus` helper + two flags.

In `corpus.py` (co-located with `CORPUS`/`STRATA`):
```python
def select_corpus(only: frozenset[str] = frozenset(), strata: frozenset[str] = frozenset()):
    """Filter CORPUS by package name (--only) and/or stratum (--stratum).
    Empty sets = no filter on that axis. Raises ValueError on an unknown stratum
    (fail-fast on a typo) or an --only name absent from the corpus."""
    if strata - STRATA:
        raise ValueError(f"unknown stratum(s): {sorted(strata - STRATA)}; valid={sorted(STRATA)}")
    names = {s.name for s in CORPUS}
    if only - names:
        raise ValueError(f"unknown --only package(s): {sorted(only - names)}")
    return [s for s in CORPUS
            if (not only or s.name in only) and (not strata or s.stratum in strata)]
```

In `__main__.py` argparse:
```python
ap.add_argument("--only", default="", help="comma-sep package names to run (subset of the corpus)")
ap.add_argument("--stratum", default="", help="comma-sep strata to run, e.g. S1,S4")
```
Parse to `frozenset` (split on `,`, drop blanks) and pass into `run_corpus(...)`, which selects `select_corpus(only, strata)` instead of the full `CORPUS`. `--only`/`--stratum` apply to `--run` and `--derive`; ignored by `--score`/`--seed`. Log the selected row count so a silent 0-row filter is visible.

- [ ] **Step 6: Assert the filter is docker-free-correct**

```bash
python3 -c "import sys; sys.path[:0]=['src','.']; \
from src.eval.package_installability.corpus import select_corpus, CORPUS; \
assert {s.name for s in select_corpus(only=frozenset(['psycopg2','pyodbc']))} == {'psycopg2','pyodbc'}; \
assert all(s.stratum=='S1' for s in select_corpus(strata=frozenset(['S1']))); \
assert len(select_corpus()) == len(CORPUS); \
print('filter OK')"
```
Expected: `filter OK`. (A fuller test lands in Task 0.2's `test_score.py` sibling — `test_corpus_filter.py`.)

- [ ] **Step 7: Confirm the docker-free surface imports and outputs are gitignored**

```bash
python3 -c "import sys; sys.path[:0]=['src','.']; import src.eval.package_installability.score, src.eval.package_installability.answer_key; print('import OK')"
git check-ignore outputs/package_installability/x.json && echo "artifacts ignored"
```
Expected: `import OK` then `outputs/package_installability/x.json` (ignored by the existing `outputs/` rule). `predict.py`/`run.py` may still fail to import (detector absent) — that is expected until Phase 1–3.

- [ ] **Step 8: Commit**

```bash
git add src/eval/package_installability .gitignore
git commit -m "refactor(eval): relocate package_installability harness into src/eval/ layout + --only/--stratum filter"
```

---

## Task 0.2: Promote the docker-free eval cores to pytest

**Files:**
- Create: `tests/eval/package_installability/__init__.py`
- Create: `tests/eval/package_installability/test_score.py`
- Create: `tests/eval/package_installability/test_answer_key.py`
- Create: `tests/eval/package_installability/test_corpus_filter.py` (the `select_corpus` filter from Task 0.1 Step 5 — name/stratum selection, unknown-stratum ValueError, empty=full-corpus)

**Interfaces:**
- Consumes: `src.eval.package_installability.score.score_records`, `…answer_key.minimize`, and `seed_records.json`.

- [ ] **Step 1: Write the failing scorer test (the `--seed` smoke, as pytest)**

```python
# tests/eval/package_installability/test_score.py
import json, pathlib
from src.eval.package_installability.score import score_records

_SEED = pathlib.Path("src/eval/package_installability/seed_records.json")

def test_score_records_on_seed_produces_metric():
    records = json.loads(_SEED.read_text())
    metric = score_records(records)
    # headline is a rate in [0,1]; diagnostics present
    assert 0.0 <= metric.installable_rate <= 1.0
    assert metric.by_mode and metric.by_stratum
```

- [ ] **Step 2: Run it, expect PASS (score.py is pure, already ported)**

```bash
python3 -m pytest tests/eval/package_installability/test_score.py -q
```
Expected: PASS. If `score_records`'s return field differs, align the assertion to the real `InstallabilityMetric` fields (`installable_rate`, `by_mode`, `by_stratum`, `fidelity`, `branch_accuracy`).

- [ ] **Step 3: Write the ddmin minimize test**

```python
# tests/eval/package_installability/test_answer_key.py
from src.eval.package_installability.answer_key import minimize

def test_minimize_keeps_only_load_bearing():
    # gate passes iff "libB" is present; ddmin must reduce to exactly {libB}
    superset = ["libA", "libB", "libC"]
    gate = lambda subset: "libB" in subset
    assert set(minimize(superset, gate)) == {"libB"}

def test_minimize_empty_when_gate_always_true():
    assert minimize(["x", "y"], lambda s: True) == []
```

- [ ] **Step 4: Run both, expect PASS**

```bash
python3 -m pytest tests/eval/package_installability -q
```
Expected: PASS (all).

- [ ] **Step 5: Full suite unchanged**

```bash
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
```
Expected: `1179 + 3 = 1182 passed` (or 1179 + the count you added).

- [ ] **Step 6: Commit**

```bash
git add tests/eval/package_installability
git commit -m "test(eval): pytest the docker-free package_installability cores (scorer + ddmin)"
```

---

## Task 1.1: Port `syslib.py` + `os_resolver.py`

**Files:**
- Create: `src/python_deps/depgraph/syslib.py` (from `v3-core`)
- Create: `src/python_deps/depgraph/os_resolver.py` (from `v3-core`)
- Modify (additive-merge): `src/python_deps/depgraph/ids.py` (add `capability_id` + `header_id`/`binary_id`/`pkgconfig_id`/`linker_id`/`apt_build_id`; `syslib_id` already exists)
- Create: `tests/depgraph/test_os_resolver.py`

**Interfaces:**
- Produces: `syslib.make_syslib_node(soname, *, discovered_by, state, apt=None, evidence=None, provenance=None) -> Node`; `os_resolver.ObservedNeed(kind, name, context=…)`, `os_resolver.ProviderCandidate`, `os_resolver.resolve(need, executor=None) -> list[ProviderCandidate]`, `os_resolver.capability_id(need) -> str`, `os_resolver.check_command_for(need, matched_path="") -> str`; `ids.capability_id` + the per-kind id helpers.

- [ ] **Step 1: Bring the modules; additive-merge `ids.py`**

```bash
git show v3-core:src/python_deps/depgraph/syslib.py     > src/python_deps/depgraph/syslib.py
git show v3-core:src/python_deps/depgraph/os_resolver.py > src/python_deps/depgraph/os_resolver.py
# ids.py: MERGE, do not overwrite (HEAD has syslib_id + possibly multi-lang ids). Add ONLY the
# missing helpers os_resolver needs — diff to see exactly what's new:
git diff HEAD v3-core -- src/python_deps/depgraph/ids.py
```
Copy the new `capability_id`/`header_id`/`binary_id`/`pkgconfig_id`/`linker_id`/`apt_build_id` defs into HEAD's `ids.py`, leaving HEAD-only ids intact.

- [ ] **Step 2: Verify imports resolve on HEAD (no rewrite expected — same package path)**

```bash
python3 -c "import sys; sys.path.insert(0,'src'); from python_deps.depgraph import os_resolver, syslib; print(os_resolver.resolve, syslib.make_syslib_node)"
```
Expected: both callables print. If either imports a name absent on HEAD (e.g. an `ids.capability_id` helper or a `schema` enum), port that minimal helper too (grep the traceback symbol on v3-core) — stay within `src/python_deps/depgraph/`.

- [ ] **Step 3: Bring v3-core's own unit tests for these modules (if present), then a HEAD smoke test**

```bash
git show v3-core:tests/depgraph/test_os_resolver.py > tests/depgraph/test_os_resolver.py 2>/dev/null || true
```
If none exists, write a minimal one:

```python
# tests/depgraph/test_os_resolver.py
from python_deps.depgraph.os_resolver import ObservedNeed, resolve, capability_id

def test_soname_table_fastpath_no_executor():
    # a known soname resolves from the table with executor=None (no apt-file)
    cands = resolve(ObservedNeed(kind="soname", name="libpq.so.5"), executor=None)
    assert cands and any("libpq" in c.apt for c in cands)   # confirm ProviderCandidate apt field name

def test_capability_id_stable_across_kinds():
    assert capability_id(ObservedNeed(kind="soname", name="libpq.so.5")).startswith("syslib:")
```

- [ ] **Step 4: Run the module tests + full suite**

```bash
python3 -m pytest tests/depgraph/test_os_resolver.py -q && \
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
```
Expected: module tests PASS; full suite still `1182 passed` (unchanged — nothing wired yet).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/syslib.py src/python_deps/depgraph/os_resolver.py src/python_deps/depgraph/ids.py tests/depgraph/test_os_resolver.py
git commit -m "feat(depgraph): port capability-keyed os_resolver + syslib node factory + ids helpers (unwired)"
```

---

## Task 1.2: Migrate the reactive path onto `os_resolver` + `syslib`, retire `apt_resolve.py` + the consolidated apt tables

> **Audit correction (2026-07-06):** `probe.py` has THREE apt authorities to migrate, not one — `resolve_soname_apt` (line 275) AND `tables.{TOOL_TO_APT, apt_for_soname, apt_for_tool}`. It also needs the net-new `failure_signatures.extract_needs`. A HEAD-only `subprocess_scan.py` consumes `tables.CLI_TOOL_TO_APT`. So `tables.py`'s consolidated maps can only be deleted AFTER both consumers are repointed — otherwise the delete is an ImportError.

> **SCOPE CORRECTION (2026-07-06, controller — flagged for human review):** The original plan told `subprocess_scan.py` to repoint `CLI_TOOL_TO_APT` onto `os_resolver.resolve(ObservedNeed("binary", tool))` and to DELETE `CLI_TOOL_TO_APT`. That is WRONG and would REGRESS: `CLI_TOOL_TO_APT` = 11 **runtime CLI tools** (git, ffmpeg, curl, java, gpg, wget, unzip, sqlite3, adb, pandoc, openssl) and `os_resolver.PROVIDER_TABLE`'s binary set = 8 **build tools** (pg_config, gcc, g++, make, cc, mysql_config, curl-config, pkg-config) — the two sets are **PROVABLY DISJOINT** (zero overlap). `test_subprocess_scan.py` asserts `adb`/`git`/`java`/`ffmpeg`/`sqlite3` detection, which os_resolver cannot provide → the repoint fails those tests. `os_resolver` supersedes the build-capability tables, NOT the runtime-CLI table. **Corrected scope:** os_resolver replaces `apt_resolve.py` + `tables.{NATIVE_LIB_TO_APT, TOOL_TO_APT, apt_for_soname, apt_for_tool}` (DELETE those). **KEEP `CLI_TOOL_TO_APT` + `apt_for_cli_tool` (distinct runtime-CLI authority, only consumer = `subprocess_scan.py`, leave that file UNCHANGED) and KEEP `NATIVE_RISK_PACKAGES` (native-risk gating, used by `probe.py`).** `test_subprocess_scan.py`'s id-collision test (imports both `CLI_TOOL_TO_APT` and `TOOL_TO_APT`) must be updated when `TOOL_TO_APT` is deleted, keeping the `CLI_TOOL_TO_APT` half.

**Files:**
- Create: `src/python_deps/depgraph/failure_signatures.py` (net-new, from `v3-core`; `extract_needs`)
- Modify: `src/python_deps/depgraph/ldd_probe.py` (import line 28; call site line 170; delete `_make_syslib_node` at 214)
- Modify: `src/python_deps/depgraph/probe.py` (import line 53; ALL apt lookups → `os_resolver`; adopt `failure_signatures.extract_needs`; delete `_make_syslib_node` at 356)
- **UNCHANGED (scope correction): `src/python_deps/depgraph/subprocess_scan.py`** — keeps using `CLI_TOOL_TO_APT`/`apt_for_cli_tool` (distinct runtime-CLI authority; NOT superseded by os_resolver)
- Modify: `src/python_deps/depgraph/apt_verify.py` (docstring: `apt_resolve` → `os_resolver.PROVIDER_TABLE`)
- Delete: `src/python_deps/depgraph/apt_resolve.py`; delete `tables.{NATIVE_LIB_TO_APT, TOOL_TO_APT, apt_for_soname, apt_for_tool}` (LAST). **KEEP `CLI_TOOL_TO_APT`, `apt_for_cli_tool`, `NATIVE_RISK_PACKAGES`.**

**Interfaces:**
- Consumes: `os_resolver.resolve`, `os_resolver.ObservedNeed`, `syslib.make_syslib_node`, `failure_signatures.extract_needs` (Tasks 1.1 + this task).
- Behavior contract: same soname → same node id (`syslib_id(soname)`), preserving `reconcile_predicted` collapse; a single apt-name authority (`os_resolver.PROVIDER_TABLE`) — no residual `tables.apt_for_*` path.

- [ ] **Step 0: Port `failure_signatures.py` (net-new) first**

```bash
git show v3-core:src/python_deps/depgraph/failure_signatures.py > src/python_deps/depgraph/failure_signatures.py
python3 -c "import sys; sys.path.insert(0,'src'); from python_deps.depgraph.failure_signatures import extract_needs; print('OK')"
```

- [ ] **Step 1: Adapter at `ldd_probe.py:170`**

The current call returns `(apt, source)`; `os_resolver.resolve` returns candidates. Replace:

```python
# OLD (ldd_probe.py ~170)
apt, _source = resolve_soname_apt(soname, executor)
```
```python
# NEW
from python_deps.depgraph.os_resolver import ObservedNeed, resolve as os_resolve
_cands = os_resolve(ObservedNeed(kind="soname", name=soname), executor)
apt = _cands[0].apt if _cands else None    # confirm ProviderCandidate apt-name field from the ported module
```
Update the import at line 28: `from python_deps.depgraph.apt_resolve import resolve_soname_apt` → remove.

- [ ] **Step 2: Point `ldd_probe`'s node build at the shared factory**

Replace the local `_make_syslib_node(...)` call (ldd_probe ~197) with `syslib.make_syslib_node(soname, discovered_by=DiscoveredBy.PROBE, state=State.MISSING, apt=apt, evidence=..., provenance=...)` matching the current node's fields, then DELETE the local `_make_syslib_node` def (ldd_probe:214). Add `from python_deps.depgraph.syslib import make_syslib_node`.

- [ ] **Step 3: Migrate ALL of `probe.py`'s apt authorities (not just `resolve_soname_apt`)**

Mirror Steps 1–2 for the soname call at `probe.py:275`, delete the local `_make_syslib_node` (356). THEN find and migrate the other authorities the audit flagged:

```bash
grep -n "resolve_soname_apt\|TOOL_TO_APT\|apt_for_soname\|apt_for_tool\|CLI_TOOL_TO_APT\|extract_needs" src/python_deps/depgraph/probe.py
```
- `apt_for_soname(...)` → `os_resolver.resolve(ObservedNeed("soname", …))`
- `apt_for_tool(...)` / `TOOL_TO_APT[...]` → `os_resolver.resolve(ObservedNeed("binary"|"tool", …))` (match the kind v3-core's `probe.py` uses)
- adopt `failure_signatures.extract_needs(stderr)` where `probe.py` currently regex-scrapes install stderr (v3-core routes stderr → `extract_needs` → `os_resolver.resolve`). Cross-check against `git show v3-core:src/python_deps/depgraph/probe.py` for the exact call shape.
Keep the exact `discovered_by`/`state`/`evidence` values the current local factory used so certification behavior is unchanged.

- [ ] **Step 3b: LEAVE `subprocess_scan.py` UNCHANGED (scope correction)**

Per the scope correction above, `subprocess_scan.py`'s `CLI_TOOL_TO_APT`/`apt_for_cli_tool` is a distinct runtime-CLI authority that os_resolver does NOT supersede (disjoint sets; repointing would fail `test_subprocess_scan.py`'s git/ffmpeg/adb/java/sqlite3 assertions). Do NOT touch this file. `CLI_TOOL_TO_APT`, `apt_for_cli_tool`, and `NATIVE_RISK_PACKAGES` stay in `tables.py`. The ONLY tables deleted in Step 4 are the os_resolver-superseded ones (`NATIVE_LIB_TO_APT`, `TOOL_TO_APT`, `apt_for_soname`, `apt_for_tool`).

- [ ] **Step 4: Retire `apt_resolve.py` and the consolidated apt tables (deletion LAST, after all consumers repointed)**

```bash
# 1. apt_resolve must have zero non-comment refs now:
grep -rn "apt_resolve\|resolve_soname_apt" src/ tests/   # expect ZERO
git rm src/python_deps/depgraph/apt_resolve.py
# 2. the SUPERSEDED tables must have zero consumers before deletion (CLI_TOOL_TO_APT is NOT superseded — keep it):
grep -rn "NATIVE_LIB_TO_APT\|\bTOOL_TO_APT\b\|apt_for_soname\|apt_for_tool" src/ tests/
#    -> if any non-test consumer of THESE remains (probe.py), repoint it to os_resolver FIRST.
#    Only when clean, delete NATIVE_LIB_TO_APT, TOOL_TO_APT, apt_for_soname, apt_for_tool from tables.py.
#    KEEP CLI_TOOL_TO_APT, apt_for_cli_tool, NATIVE_RISK_PACKAGES (leave tables.py's other entries intact).
#    Update test_subprocess_scan.py's id-collision test: it imports both CLI_TOOL_TO_APT and TOOL_TO_APT —
#    keep the CLI_TOOL_TO_APT half, drop the TOOL_TO_APT reference (or reframe against os_resolver ids).
# 3. docstring: apt_verify.py:13 mentions apt_resolve.py -> update to os_resolver.PROVIDER_TABLE.
```
Any test that asserted `tables.apt_for_*` / `apt_resolve` behavior migrates to assert `os_resolver.resolve` (retarget, don't delete coverage).

- [ ] **Step 5: Full suite green (foreground) + docker ldd test**

```bash
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
python3 -m pytest tests/depgraph/test_ldd_probe.py -q   # includes the cpython-tag regression
```
Expected: full suite `1182 passed` (fix any `apt_resolve` test that referenced the retired module — retarget to `os_resolver`; adjust the count in the report). Docker ldd test PASS (or `@pytest.mark.docker`-skipped if docker unavailable — note which).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/{failure_signatures,ldd_probe,probe,subprocess_scan,apt_verify,tables}.py tests/depgraph
git rm src/python_deps/depgraph/apt_resolve.py 2>/dev/null || true
git commit -m "refactor(depgraph): single apt authority (os_resolver) — migrate probe/ldd/subprocess_scan off apt_resolve+tables; +failure_signatures"
```

---

## Task 2.1: Port `wheel_inspect.py` + `wheel_preflight.py` (+ `artifact_map.py` if needed)

**Files:**
- Create: `src/python_deps/depgraph/artifact_map.py`, `wheel_inspect.py`, `wheel_preflight.py` (from `v3-core`)
- Create: `tests/depgraph/test_wheel_inspect.py`

**Interfaces:**
- Produces: `artifact_map.resolve_artifact_map(...) -> {name: build_from_source}` (the branch oracle); `wheel_preflight.wheel_preflight_probe(graph, host_executor, target_env) -> DepGraph`; `wheel_inspect.inspect_wheel_sonames(...)`.
- **artifact_map is REQUIRED** (verified against the handoff): it is pipeline stage 3, `predict.py` imports it, and both `wheel_preflight_probe` and `seed_build_deps` (Task 3) read the `Node.build_from_source` it stamps. Port it here, not conditionally.

- [ ] **Step 1: Bring the three files + confirm `pyelftools` is available**

```bash
for m in artifact_map wheel_inspect wheel_preflight; do
  git show v3-core:src/python_deps/depgraph/$m.py > src/python_deps/depgraph/$m.py
done
python3 -c "import elftools; print('pyelftools', elftools.__version__)"   # add to deps if missing
python3 -c "import sys; sys.path.insert(0,'src'); from python_deps.depgraph import artifact_map, wheel_preflight, wheel_inspect; print('OK')"
```

- [ ] **Step 2: Confirm the artifact_map → build_from_source → wheel_preflight chain**

```bash
git show v3-core:src/python_deps/depgraph/wheel_preflight.py | grep -n "build_from_source"   # reads the stamp
git show v3-core:src/python_deps/depgraph/artifact_map.py     | grep -n "def resolve_artifact_map\|build_from_source"
```
Port any helper `artifact_map` imports that HEAD lacks (grep the traceback). The build.py wiring (Task 2.2) will call `resolve_artifact_map` to stamp `build_from_source` BEFORE `wheel_preflight_probe`, matching v3-core's stage order.

- [ ] **Step 3: ELF soname read test on a fixture**

```python
# tests/depgraph/test_wheel_inspect.py  (mark docker/network-free)
from python_deps.depgraph.wheel_inspect import inspect_wheel_sonames
def test_reads_dt_needed_from_bundled_so(tmp_path):
    # build a tiny wheel containing an ELF .so with a known DT_NEEDED, or reuse a
    # committed fixture wheel; assert the soname set includes the known lib.
    ...
```
If constructing an ELF fixture is heavy, port v3-core's existing `test_wheel_inspect.py` fixture instead:
```bash
git show v3-core:tests/depgraph/test_wheel_inspect.py > tests/depgraph/test_wheel_inspect.py 2>/dev/null || true
```

- [ ] **Step 4: Modules import + suite unchanged**

```bash
python3 -c "import sys; sys.path.insert(0,'src'); from python_deps.depgraph import wheel_preflight, wheel_inspect; print('OK')"
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
```
Expected: `OK`; suite still green (unwired).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/artifact_map.py src/python_deps/depgraph/wheel_inspect.py src/python_deps/depgraph/wheel_preflight.py tests/depgraph/test_wheel_inspect.py
git commit -m "feat(depgraph): port artifact_map branch oracle + wheel_preflight + wheel_inspect (unwired)"
```

---

## Task 2.2: Wire `wheel_preflight_probe` into the Phase-B proactive pre-pass

**Files:**
- Modify: `src/python_deps/depgraph/build.py` (aux-once stage ~567, next to `seed_wheel_oracle_prior`)
- Create/extend: `tests/depgraph/test_build_native_prepass.py`

**Interfaces:**
- Consumes: `wheel_preflight.wheel_preflight_probe(graph, host_executor, target_env)`.
- Contract: additive `RESOLVER`/`UNKNOWN` SystemLib nodes; `_python_native_obligations`'s `ldd_probe` later reconciles them via `syslib_id`. Phase A untouched.

- [ ] **Step 1: Write the failing wiring test**

```python
# tests/depgraph/test_build_native_prepass.py
def test_wheel_preflight_prior_seeds_unknown_syslib(monkeypatch):
    # a wheel-classified package with a known DT_NEEDED soname yields a
    # SystemLib node with discovered_by=RESOLVER, state=UNKNOWN BEFORE ldd runs.
    ...
```

- [ ] **Step 2: Confirm it fails (no pre-pass yet)**

```bash
python3 -m pytest tests/depgraph/test_build_native_prepass.py -q
```
Expected: FAIL.

- [ ] **Step 3: Insert the wheel pre-pass at the aux-once seam**

> **WIRING CORRECTION (2026-07-06, controller — flagged for human review):** The original snippet inserted `resolve_artifact_map` at the seam to stamp `build_from_source`. That is REDUNDANT and mildly risky on HEAD: HEAD ALREADY stamps `build_from_source` (`False` for wheels / `True` for sdist) on EVERY package during Phase-A resolve via `resolve.native_risk_from_lock` → `wheel_oracle.risk_from_packages` (`resolve.py:298`, the `_stamp(...)` loop), and `wheel_preflight_probe` filters exactly on `n.build_from_source is False`. So the stamp `wheel_preflight` needs is already present and correct. Inserting `resolve_artifact_map` would add a SECOND resolver run per build and OVERRIDE the Phase-A stamp in Phase B (the plan's own "confirm nothing reads a stale stamp" flag). **Corrected wiring: wire ONLY `wheel_preflight_probe`, reading the existing native_risk_from_lock stamp; do NOT insert `resolve_artifact_map` at the build seam.** (`resolve_artifact_map` stays ported and is used by the eval's `predict.py` seam — its intended role.) The seam function (the one returning `graph, roots, target_env, exclude_newer` at ~build.py:574) has `host_executor` (defaulted ~453), `container_executor`, `target_env` (~471), and `graph` all in scope.

```python
from python_deps.depgraph.wheel_preflight import wheel_preflight_probe
# ... at ~build.py:567, next to `graph = seed_wheel_oracle_prior(graph)`
# (build_from_source already stamped by Phase-A native_risk_from_lock):
graph = wheel_preflight_probe(graph, host_executor, target_env)   # proactive wheel soname priors (RESOLVER/UNKNOWN)
# `_python_native_obligations` (ldd_probe/import_probe) runs later via build_dep_graph's
# `provider.native_obligations(graph, container_executor)` (~696) and reconciles onto the
# RESOLVER priors via syslib_id — no ordering change needed here.
```
(Additive: `wheel_preflight_probe` only ADDS RESOLVER/UNKNOWN SystemLib nodes for wheel `DT_NEEDED` sonames not already present; non-native repos are byte-identical. `discovered_by=DiscoveredBy.RESOLVER`, `state=State.UNKNOWN` are what the ported `wheel_preflight` sets — both exist on HEAD.)

- [ ] **Step 4: Test passes + full suite green**

```bash
python3 -m pytest tests/depgraph/test_build_native_prepass.py -q
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
```
Expected: both PASS; suite green (byte-identity for non-native repos preserved — priors are additive and dropped when no wheel soname applies).

- [ ] **Step 5: Verify Phase 2 with a targeted integration test — NOT the 70-row eval**

The `package_installability` eval does NOT exercise `wheel_preflight` (its `predict.py` explicitly excludes it and can't even import until `build_deps` lands in Phase 3). So Phase 2 is verified by its own docker integration test, not the eval:

```python
# tests/depgraph/test_wheel_preflight_integration.py  (@pytest.mark.docker)
def test_wheel_preflight_seeds_runtime_soname_reconciled_by_ldd():
    # pyodbc's wheel bundles a DT_NEEDED on libodbc.so.2; wheel_preflight_probe
    # must seed a SystemLib(RESOLVER/UNKNOWN) node for it BEFORE install, and
    # ldd_probe must reconcile onto the SAME syslib_id node (not a duplicate).
    ...
```
Expected: one `syslib:libodbc.so.2` node, `discovered_by=RESOLVER` pre-install → certified after ldd. If docker is unavailable, assert the seed step alone (wheel_preflight_probe on a fixture wheel). Record that the 70-row eval credit for this case is DEFERRED (the runtime-dlopen seam — see optional Task 3.5).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_build_native_prepass.py
git commit -m "feat(build): wire wheel_preflight proactive soname priors into the Phase-B pre-pass"
```

---

## Task 3.1: Port `pep725.py` + `debian_builddeps.py` + `build_deps.py`

**Files:**
- Create: `src/python_deps/depgraph/pep725.py`, `debian_builddeps.py`, `build_deps.py` (from `v3-core`)
- Create: `tests/depgraph/test_build_deps.py` (+ port v3-core's if present)

**Interfaces:**
- Produces: `build_deps.seed_build_deps(graph, executor) -> DepGraph`; `build_deps.build_dep_prior(...)`; `pep725.pep725_external(pypi_name, version, executor) -> list[ObservedNeed]`.

- [ ] **Step 1: Bring the three files (dependency order: pep725, debian_builddeps, then build_deps)**

```bash
for m in pep725 debian_builddeps build_deps; do
  git show v3-core:src/python_deps/depgraph/$m.py > src/python_deps/depgraph/$m.py
done
python3 -c "import sys; sys.path.insert(0,'src'); from python_deps.depgraph import build_deps, pep725, debian_builddeps; print('OK')"
```
Port any missing helper the traceback names (e.g. a `tables.PACKAGE_TO_BUILD_NEEDS` entry, `FLAVOR_OVERRIDES`) verbatim from v3-core.

- [ ] **Step 2: Bring the unit tests**

```bash
for t in test_build_deps test_pep725 test_debian_builddeps; do
  git show v3-core:tests/depgraph/$t.py > tests/depgraph/$t.py 2>/dev/null || true
done
```

- [ ] **Step 3: Run module tests + full suite (unwired)**

```bash
python3 -m pytest tests/depgraph/test_build_deps.py tests/depgraph/test_pep725.py -q
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
```
Expected: module tests PASS; full suite green (nothing wired).

- [ ] **Step 4: Commit**

```bash
git add src/python_deps/depgraph/{pep725,debian_builddeps,build_deps}.py tests/depgraph/test_*.py
git commit -m "feat(depgraph): port sdist build-dep prior (pep725 + debian_builddeps + build_deps, unwired)"
```

---

## Task 3.2: Wire `seed_build_deps` with specific-first / generic-fallback

**Files:**
- Modify: `src/python_deps/depgraph/build.py` (pre-pass, after `wheel_preflight_probe`, alongside `seed_wheel_oracle_prior`)
- Modify: `src/python_deps/depgraph/seed.py` (docstring: generic `build-essential` is now the FLOOR, not the whole story)
- Extend: `tests/depgraph/test_build_native_prepass.py`

**Interfaces:**
- Consumes: `build_deps.seed_build_deps(graph, container_executor)`.
- Decision (the one real judgment call): HEAD deliberately replaced specific `-dev` prediction with generic `build-essential` (precision-over-coverage). v3-core's `build_deps.py` docstring prescribes the reconciliation — **specific priors first, generic `build-essential` as the fallback floor**. Preserve `seed_wheel_oracle_prior`.

- [ ] **Step 1: Failing test — an sdist package gets its specific `-dev` prior AND keeps the generic floor**

```python
def test_sdist_gets_specific_dev_prior_and_generic_floor(monkeypatch):
    # e.g. psycopg2 (sdist) -> a libpq/pg_config capability Tool node (specific)
    # AND the shared build-essential floor still present.
    ...
```

- [ ] **Step 2: Confirm FAIL**

```bash
python3 -m pytest tests/depgraph/test_build_native_prepass.py::test_sdist_gets_specific_dev_prior_and_generic_floor -q
```
Expected: FAIL.

- [ ] **Step 3: Wire it (ordering matters)**

In the pre-pass, after the wheel prior:

```python
from python_deps.depgraph.build_deps import seed_build_deps
graph = wheel_preflight_probe(graph, host_executor, target_env)   # wheel arm
graph = seed_build_deps(graph, container_executor)                # sdist arm: specific -dev priors
graph = seed_wheel_oracle_prior(graph)                            # generic build-essential FLOOR (kept last)
```
(If `seed_wheel_oracle_prior` already runs at ~567, ensure the specific priors run before/beside it and neither erases the other — union, not replace.)

- [ ] **Step 4: Test passes + full suite green**

```bash
python3 -m pytest tests/depgraph/test_build_native_prepass.py -q
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
```
Expected: PASS; suite green. Watch for over-prediction breaking a previously-clean closure (e.g. h5py broad-MPI set) — if a repo regresses, the merge must keep the generic floor and gate the specific set (this is the precision boundary).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build.py src/python_deps/depgraph/seed.py tests/depgraph/test_build_native_prepass.py
git commit -m "feat(build): wire specific -dev build-dep priors (specific-first, build-essential floor)"
```

---

## Task 3.3: Full `package_installability` acceptance gate

**Files:**
- Run only (artifacts to gitignored `outputs/package_installability/`); no source change unless a regression is found.

**Interfaces:**
- Consumes: `predict.py`'s seam = `detect_target_env → resolve_artifact_map → seed_wheel_oracle_prior → seed_build_deps`. NOTE: `predict.py` deliberately EXCLUDES `wheel_preflight` (Phase 2) — this gate measures the **sdist build-dep prior** (Phase 3). It is first runnable at **Task 3.1** (once `build_deps` imports); do not attempt it after Phase 2.
- Gate: installable_rate must hold **≥ 0.9143** (v3-core's post-Bucket-B.1 number — 0.80 was the PRE-Bucket-B baseline; the whole point of porting `build_deps.py` is to reproduce 0.9143) AND **`failure_phase.apt == 0`** AND `branch_accuracy`/`fidelity` not regressed vs v3-core's recorded run. Falling to ~0.80 means the Bucket-B/B.1 fixes did not actually take effect in the wiring.

- [ ] **Step 1: Run the 70-row corpus (foreground, ~one fresh container per row)**

```bash
python3 -m src.eval.package_installability --run \
  --image python:3.11-slim-bookworm --platform linux/amd64 \
  --checkpoint outputs/package_installability/2026-07-05-integration-run.jsonl \
  --out       outputs/package_installability/2026-07-05-integration-run.json
```

- [ ] **Step 2: Score and compare to v3-core's baseline**

```bash
python3 -m src.eval.package_installability --score outputs/package_installability/2026-07-05-integration-run.json
```
Expected: `installable_rate ≥ 0.9143`; **`failure_phase.apt == 0`** (exactly zero, not "≈"); the S6 branch-control rows show zero over-prediction. Diff `by_stratum`/`fidelity` against v3-core's `package-installability-eval-landed` numbers (0.80 → 0.886 Bucket-B → 0.9143 Bucket-B.1).

- [ ] **Step 2b: Bucket-B.1 apt-safety guard fired (regression-critical)**

The B.1 fixes are what keep `failure_phase` apt ≈ 0. Confirm the ported guards actually run (not silently no-op'd by the wiring):
- **uWSGI (Fix2 / `_apt_installable`):** its predicted Debian build-dep set fails `apt-get install -s`, so the set is dropped → uWSGI row must NOT fail with an apt error (`apt_rc==0`). If uWSGI shows `apt_rc≠0`, `seed_build_deps` did not receive the container executor — fix the wiring (Task 3.2).
- **Lisp `cffi` / `cups` (Fix1 / source validation):** must not pull a non-Python Debian source's build-deps. Confirm no `cffi`/`cups`-sourced apt names appear in those rows' predictions.
Record both in the CHANGELOG entry as evidence the B.1 apt-safety survived the port.

- [ ] **Step 3: Triage any regression**

If a row that passed on v3-core now fails on this branch, it is a wiring defect (not a detector defect — the detector code is byte-identical to v3-core). Common causes: pre-pass ran before `build_from_source` was stamped; `host_executor` vs `container_executor` mixed up; the generic floor erased by specific priors. Fix minimally, re-run the affected rows.

- [ ] **Step 4: CHANGELOG the integration**

Append an Observation→Why→What→Verification entry to `docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md`: the port scope, the single seam, the specific-first/generic-fallback decision, the before/after installable_rate, and the fact that Phase A stayed byte-identical.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/CHANGELOG-planner-v3-e2e-loop.md
git commit -m "docs(changelog): syslib detector integration — full package_installability gate (rate >= 0.9143, apt=0)"
```

---

## Task 3.4: Render/emit smoke — the ported nodes reach `setup.sh`

**Files:**
- Create: `tests/depgraph/test_syslib_emit.py` (no source change unless a gap is found)

**Interfaces:**
- Consumes: `build_script.render_build_script` / `emit.py` (HEAD-existing; already gate `NodeType.TOOL` on `build_from_source` and require an `apt:`-prefixed `chosen_fix`).

**Why this task:** the `package_installability` eval runs `predict → apt-get install P` **directly** and never calls `render_build_script`, so it validates DETECTION but NOT emit. The capability-keyed `TOOL`/`SYSTEM_LIB` nodes `seed_build_deps`/`wheel_preflight` now produce must actually render into the system tier of `setup.sh`. HEAD has the machinery (`emit.py:78` gates `NodeType.TOOL` on `build_from_source`; schema has `SYSTEM_LIB`/`TOOL`/`Layer.TOOLCHAIN`) — this task proves the ported nodes flow through it.

- [ ] **Step 1: Failing test — a source-built repo's setup.sh contains the predicted apt line**

```python
# tests/depgraph/test_syslib_emit.py
def test_setup_sh_emits_apt_tier_for_ported_syslib_nodes():
    # build a graph for a source-built package (e.g. psycopg2) through the wired
    # pipeline (fake/real executor), render setup.sh, assert:
    #  - an `apt-get install` line contains the resolved apt name (libpq-dev/pkg-config)
    #  - the apt (system) tier is ordered BEFORE the `pip install --no-deps` line
    #  - a KNOWN-wheel package emits NO apt line for its (skipped) build-essential Tool
    ...
```

- [ ] **Step 2: Run it**

```bash
python3 -m pytest tests/depgraph/test_syslib_emit.py -q
```
Expected: PASS if HEAD's emit already recognizes the ported nodes' `apt:` `chosen_fix` (likely). If it FAILS because `_is_emittable`/`render_build_script` doesn't recognize a ported node shape, that is a real emit gap — fix `emit.py`/`build_script.py` minimally to emit `apt:`-prefixed `TOOL`/`SYSTEM_LIB` nodes, then re-run.

- [ ] **Step 3: Full suite + commit**

```bash
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q
git add tests/depgraph/test_syslib_emit.py src/python_deps/depgraph/emit.py src/python_deps/depgraph/build_script.py 2>/dev/null
git commit -m "test(depgraph): assert ported syslib nodes render into setup.sh apt tier (emit path)"
```

---

## Task 3.5 (OPTIONAL — makes the 70-row eval credit Phase 2): wire `wheel_preflight` into `predict.py`

**Only do this if you want the `package_installability` eval to measure the wheel/runtime-dlopen priors** (it currently doesn't — this is your handoff's deferred "runtime-dlopen seam"). Without it, `pyodbc`/`python-magic` remain reactive-T3 fails in the eval even though Phase 2 seeds their sonames in the real build pipeline.

**Files:**
- Modify: `src/eval/package_installability/predict.py` (add the `wheel_preflight_probe` stage to the predicted-apt seam)

- [ ] **Step 1: Extend the predict seam**

In `predict.py`, after `resolve_artifact_map` stamps `build_from_source`, add `wheel_preflight_probe(graph, host_executor, target_env)` so a wheel's runtime `DT_NEEDED` sonames enter the predicted apt set (mirror the build pipeline's stage 3.5). Update the docstring's "does NOT include wheel_preflight" note.

- [ ] **Step 2: Re-run the full 70 (foreground)**

```bash
python3 -m src.eval.package_installability --run --image python:3.11-slim-bookworm --platform linux/amd64 \
  --out outputs/package_installability/2026-07-06-with-wheelpreflight.json
python3 -m src.eval.package_installability --score outputs/package_installability/2026-07-06-with-wheelpreflight.json
```
Expected: `pyodbc` (libodbc.so.2) and `python-magic` (libmagic1) move from fail → pass; installable_rate rises ABOVE 0.9143; **`failure_phase.apt` stays 0** (wheel sonames are runtime libs, low over-prediction risk). If apt goes nonzero, a wheel-preflight soname mis-resolved to an apt-breaking name — gate it.

- [ ] **Step 3: Commit**

```bash
git add src/eval/package_installability/predict.py outputs/package_installability/.gitkeep 2>/dev/null
git commit -m "feat(eval): predict seam includes wheel_preflight (runtime-dlopen seam) — pyodbc/python-magic first-pass"
```

---

## Self-Review

**Spec coverage:** `executor --platform` prereq = Task 0.0; eval move = Tasks 0.1–0.2; capability resolver + reactive migration (free modernization) = Tasks 1.1–1.2; branch oracle + wheel priors = Tasks 2.1–2.2; sdist build-dep prior (Bucket-B/B.1) = Tasks 3.1–3.2; full eval gate = 3.3; render/emit smoke = 3.4.

**Handoff pipeline cross-check (2026-07-06):** every stage of the authoritative v3-core pipeline is homed —
0 `detect_target_env` (HEAD superset, kept) · 1 `select_roots` (HEAD 2-phase, kept) · 2 `resolve_closure` (HEAD, kept) · 3 `resolve_artifact_map` (**Task 2.1**, branch oracle) · 3.5 `wheel_preflight_probe` (**2.1/2.2**) · 4a `seed_wheel_oracle_prior` (HEAD, generic floor) · 4b `seed_build_deps` (**3.1/3.2**, the prior + Bucket-B/B.1) · 4c `install_closure` (**1.2** migrate) · 4.5 `ldd_probe` (**1.2** migrate) · 5 `certified_import_links`+`import_probe` (HEAD + **1.2**) · → `render_build_script`/`emit` (HEAD, **3.4** smoke). Supporting: `os_resolver`/`syslib`/`ids` (1.1), `pep725`/`debian_builddeps` (3.1), `failure_signatures` (1.2), `executor` (0.0). Out of scope (confirmed no syslib import edges): service track (`service_scan`/`service_recipes`/`config_scan`), generic repair (`patch`/`patch_gate`/`diagnose`/`advise`). Newer-on-HEAD, NOT overwritten: `schema.py`, `target_env.py`.

**Two-phase invariant:** no task touches `_phase_a_fixpoint`, `coverage.py`, `roots.py`, or `resolve.py` root selection. All wiring is in the aux-once pre-pass / Phase B. Held.

**Type/interface consistency:** `os_resolver.resolve(ObservedNeed, executor) -> [ProviderCandidate]` and `syslib.make_syslib_node(soname, *, discovered_by, state, apt=…)` are used identically in Tasks 1.1/1.2; `wheel_preflight_probe(graph, host_executor, target_env)` in 2.1/2.2; `seed_build_deps(graph, executor)` in 3.1/3.2. The one unconfirmed name — `ProviderCandidate`'s apt-name field — is flagged for confirmation at first use (Task 1.1 Step 3, Task 1.2 Step 1).

**Known open item (not a placeholder — a decision):** Task 2.1 Step 2 (port `artifact_map.py` vs reuse HEAD's `build_from_source` stamp) and Task 3.2 (the precision boundary on over-prediction) are genuine forks with a stated default; the implementer records the choice in the commit.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-07-05-syslib-detector-integration.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, task review between tasks, fast iteration. Given this is a port with clear per-task gates, most implementation tasks fit a mid-tier model; the two wiring tasks (2.2, 3.2) and the acceptance gate (3.3) warrant a capable model.
2. **Inline Execution** — batch with checkpoints for review.

Which approach?

---

## Task 1.2 — EXPANDED (full v3-core fidelity, chosen 2026-07-06; supersedes the deferred single-task 1.2)

**Decision (user, 2026-07-06):** finish the reactive-probe migration to FULL v3-core capability-observation fidelity (not the minimal de-dup). Three read-only Sonnet recon agents produced the map below. **Load-bearing facts:** (a) the reactive path (`import_probe`/`install_closure`/`ldd_probe`/`resolve_soname_apt`) is PROVABLY NOT reachable from the eval's `predict.py` — changing it CANNOT move `installable_rate`/`apt` (Task 3.3 stays 0.9143/apt=0); (b) NO architectural blocker — HEAD's `Node.data`, `os_resolver` (byte-identical), and the proactive `seed_build_deps` capability nodes were built for exactly this reconcile; a reactive `PROBE` observation at `capability_id(need)` collapses onto the proactive `RESOLVER`/`UNKNOWN` node at the same id (the `reconcile_predicted` RESOLVER-guard); (c) THE MANDATORY PRESERVATION — v3-core's `import_probe` DROPS HEAD's metadata-present non-native import-failure flagging (`flag_runtime_import_failure`/`_short_import_error`, pinned by `test_import_probe_nonnative.py`, consumed by `run_ours_pkg.py`); the port MUST re-insert it as an empty-`extract_needs`-list fallback. Reactive-path test baseline (2026-07-06): **84 passed** (`test_probe` 33 + `test_ldd_probe` 20 + `test_import_probe_nonnative` ~8 + `test_apt_resolve` 13 + `test_tables` 8… anchor = 84). Full-suite baseline entering 1.2: **1368 passed / 1 skipped**.

**Ordering:** 1.2a (foundation, unwired) → 1.2b (probe.py) → 1.2c (ldd_probe.py) → 1.2d (retire legacy). Each is commit-local, Phase A untouched, eval-invisible.

### Task 1.2a: Port failure-signature foundation (`failure_classifier` SONAME_RES + `failure_signatures`), UNWIRED

**Files:**
- Modify: `src/python_deps/failure_classifier.py` (port v3-core's `SONAME_RES`/glibc diff — ~59 diff lines: add `SONAME_RES` tuple (4 anchored patterns, all require "cannot open shared object file"), `_first_soname_match`/`first_soname`, `_GLIBC_MISMATCH_RE`/`glibc_version_mismatch`, update `classify_dependency_failure`; **KEEP `NATIVE_LIBRARY_RE` for now** — `probe.py:32,258` still imports it until Task 1.2b removes that consumer; the `native_library_missing` failure_type string is UNCHANGED so `runtime_classify.py:102` + `diagnose.py` are unaffected)
- Create: `src/python_deps/depgraph/failure_signatures.py` (net-new, verbatim from v3-core; `extract_needs(stderr, *, context_hint) -> list[ObservedNeed]`; imports `os_resolver.{ObservedNeed,default_context}` (present) + `failure_classifier.SONAME_RES` (added above))
- Create: `tests/depgraph/test_failure_signatures.py` (verbatim from v3-core, 44 tests), `tests/depgraph/test_syslib.py` (verbatim from v3-core, 2 tests — `syslib.make_syslib_node` already exists on HEAD, currently untested)

**Interfaces produced:** `failure_signatures.extract_needs`, `failure_classifier.{SONAME_RES, first_soname, glibc_version_mismatch}`.

- [ ] **Step 1:** Diff `git show HEAD:src/python_deps/failure_classifier.py` vs `v3-core:` — apply ONLY the additive SONAME_RES/glibc changes; keep `NATIVE_LIBRARY_RE` (still imported by probe.py until 1.2b). Confirm `native_library_missing` string unchanged.
- [ ] **Step 2:** `git show v3-core:src/python_deps/depgraph/failure_signatures.py > src/python_deps/depgraph/failure_signatures.py`. Verify `python3 -c "import sys; sys.path.insert(0,'src'); from python_deps.depgraph.failure_signatures import extract_needs; print('OK')"`.
- [ ] **Step 3:** Port v3-core's `test_failure_signatures.py` + `test_syslib.py` verbatim. Run: `python3 -m pytest tests/depgraph/test_failure_signatures.py tests/depgraph/test_syslib.py -q` → expect 46 passed.
- [ ] **Step 4:** Guard the adjacency — `python3 -m pytest tests/depgraph/test_runtime_classify.py tests/depgraph/test_failure_classifier.py -q` (whatever exists) + full suite `tests/depgraph tests/pkg_layer tests/eval` → expect **1368 + 46 = 1414 passed / 1 skipped** (unwired — nothing else changes; `diagnose`/`runtime_classify` byte-behavior preserved). Report exact delta.
- [ ] **Step 5:** Commit `src/python_deps/failure_classifier.py src/python_deps/depgraph/failure_signatures.py tests/depgraph/test_failure_signatures.py tests/depgraph/test_syslib.py` — `feat(depgraph): port failure_signatures + failure_classifier SONAME_RES foundation (unwired)`.

### Task 1.2b: Migrate `probe.py` to capability-observation (PRESERVE the flag-fallback)

**Files:**
- Modify: `src/python_deps/depgraph/probe.py` — (1) imports: drop `NATIVE_LIBRARY_RE`, `apt_resolve.resolve_soname_apt`, `tables.{TOOL_TO_APT, apt_for_soname, apt_for_tool}`, `tool_id`; add `failure_signatures.extract_needs`, `os_resolver.{ObservedNeed, capability_id, check_command_for, resolve}`, `logging`. **KEEP `from python_deps.depgraph.relink import flag_runtime_import_failure`** and `syslib.make_syslib_node`. (2) ADOPT v3-core's `reconcile_predicted` (superset: adds optional `chosen_fix=None, fix_candidates=()` + backfill; backward-compatible with the 5-arg `ldd_probe` caller). (3) ADOPT v3-core's `_ingest_need` + `_make_capability_node`. (4) `install_closure` inner loop → `for need in extract_needs(stderr, context_hint="build"): _ingest_need(...)`. (5) `import_probe`: `for need in extract_needs(stderr, context_hint="runtime"): _ingest_need(...)` — **but if `extract_needs(...)` returns EMPTY for a failed probe, fall through to HEAD's existing branch: `reason = _short_import_error(stderr) or f"import {name} failed"; for node_id in target["attempt_nodes"]: new = flag_runtime_import_failure(new, node_id, reason=reason)`** (THE MANDATORY PRESERVATION — gate on empty-needs, not on soname-nonmatch). (6) DELETE `_tool_gaps`, `_make_tool_node`, `_tool_check`; consolidate the local `_make_syslib_node` onto `syslib.make_syslib_node` (drop the `apt_for_soname` fallback — caller resolves fully). (7) PRESERVE unchanged: `_failed_build_packages`, `_requirers_of_failed`, `_reinstall_survivors`, `_probe_targets`, `_edge_sources`, `_build_owners`, `_spec`, `_sorted`, `_first_line_with`, `_short_import_error`. Signatures of `install_closure`/`import_probe`/`reconcile_predicted` unchanged (callers in `build.py`/`ldd_probe.py` unaffected).
- Modify: `tests/depgraph/test_probe.py` — port v3-core's version (43 tests: +capability-generic tests, `test_make_tool_node_is_self_contained` deleted). **DO NOT touch `tests/depgraph/test_import_probe_nonnative.py`** (HEAD-only, 7-8 tests; the flag-fallback must keep them GREEN — this is the acceptance signal for the preservation).

**Interfaces produced:** `probe.{import_probe, install_closure, reconcile_predicted(…, chosen_fix, fix_candidates), _ingest_need, _make_capability_node}`.

- [ ] **Step 1:** Apply the import + function changes above. Cross-check every call shape against `git show v3-core:src/python_deps/depgraph/probe.py`.
- [ ] **Step 2:** Add the empty-needs fallback in `import_probe` (verify against `test_import_probe_nonnative.py`'s fixtures: a metadata-present `ImportError: cannot import name` and a bare `RuntimeError` at import must still set `data["unresolved_runtime"]`/`import_error`).
- [ ] **Step 3:** Port v3-core `test_probe.py`. Run `python3 -m pytest tests/depgraph/test_probe.py tests/depgraph/test_import_probe_nonnative.py -q` → both green (nonnative = the preservation proof).
- [ ] **Step 4:** Full suite `tests/depgraph tests/pkg_layer tests/eval` → report count (expect ~1414 + (43−33)=~1424, minus any retired probe tests; the exact number is what the implementer reports — no regression, `test_import_probe_nonnative` all pass).
- [ ] **Step 5:** Commit `probe.py tests/depgraph/test_probe.py` — `refactor(probe): capability-observation reactive path (extract_needs/_ingest_need) preserving metadata-present flag fallback`.

### Task 1.2c: Migrate `ldd_probe.py` onto `os_resolver` + shared syslib factory

**Files:**
- Modify: `src/python_deps/depgraph/ldd_probe.py` — imports: remove `apt_resolve.resolve_soname_apt` (+ `Layer` if now unused); add `os_resolver.{ObservedNeed, resolve}`, `syslib.make_syslib_node`. Call site (~170): `cands = resolve(ObservedNeed("soname", soname, context="runtime"), executor); apt = cands[0].package if cands else None`. `reconcile_predicted(...)` call: add `chosen_fix=f"apt:{apt}" if apt else None, fix_candidates=tuple(f"apt:{c.package}" for c in cands)`. Replace the inline `Node(...)` build with `make_syslib_node(soname, discovered_by=DiscoveredBy.PROBE, state=State.MISSING, apt=apt, evidence=…, provenance="ldd (observed)")`. Delete this file's local `_make_syslib_node`. Docstring: `resolve_soname_apt`→`os_resolver.resolve`, `NATIVE_LIB_TO_APT`→`PROVIDER_TABLE`. Batching logic (`parse_ext_so_map`, `parse_ldd_not_found`, ldd loop) UNCHANGED. Signature `ldd_probe(graph, executor)` unchanged.
- Modify: `tests/depgraph/test_ldd_probe.py` — port v3-core's version (21 tests: +`test_ldd_probe_fills_chosen_fix_left_none_by_seed`).

- [ ] **Step 1:** Apply the edits (cross-check `git show v3-core:src/python_deps/depgraph/ldd_probe.py`). **NOTE:** `test_ldd_probe_docker.py` currently imports `NATIVE_LIB_TO_APT` (still present until 1.2d) — do NOT break it here; it stays green until the table is deleted in 1.2d.
- [ ] **Step 2:** `python3 -m pytest tests/depgraph/test_ldd_probe.py -q` → green. Docker ldd test (`test_ldd_probe_docker.py`) — run foreground if docker free, else note skip.
- [ ] **Step 3:** Full suite → report count (expect prior + 1). Commit `ldd_probe.py tests/depgraph/test_ldd_probe.py` — `refactor(ldd_probe): single apt authority (os_resolver.resolve) + shared syslib factory`.

### Task 1.2d: Retire `apt_resolve.py` + the os_resolver-superseded tables (deletion LAST)

**Files:**
- Delete: `src/python_deps/depgraph/apt_resolve.py` (zero non-comment refs after 1.2b/1.2c — verify `grep -rn "apt_resolve\|resolve_soname_apt" src/ tests/` == 0)
- Modify: `src/python_deps/depgraph/tables.py` — delete `NATIVE_LIB_TO_APT`, `TOOL_TO_APT`, `apt_for_soname`, `apt_for_tool`. **KEEP `CLI_TOOL_TO_APT`, `apt_for_cli_tool`, `NATIVE_RISK_PACKAGES`** (runtime-CLI authority + native-risk gating — NOT superseded; `subprocess_scan.py` + `probe.py`'s `_probe_targets` still use them).
- Modify: `src/python_deps/depgraph/apt_verify.py` (docstring: `apt_resolve.py` → `os_resolver.PROVIDER_TABLE`)
- Fix collateral import-breaks: `tests/depgraph/test_ldd_probe_docker.py` (imports `NATIVE_LIB_TO_APT` — repoint to `os_resolver.PROVIDER_TABLE`/`resolve`), `tests/depgraph/test_subprocess_scan.py` (its `test_tables_are_disjoint`/id-collision test imports `TOOL_TO_APT` — reframe to keep only the `CLI_TOOL_TO_APT` half, or assert disjointness vs `os_resolver.PROVIDER_TABLE`).
- Delete: `tests/depgraph/test_apt_resolve.py` (13 tests — coverage lives in `test_os_resolver.py` already). Shrink `tests/depgraph/test_tables.py` to v3-core's version (4 tests — only `NATIVE_RISK_PACKAGES` remains).

- [ ] **Step 1:** `grep -rn "apt_resolve\|resolve_soname_apt\|NATIVE_LIB_TO_APT\|\bTOOL_TO_APT\b\|apt_for_soname\|apt_for_tool" src/ tests/` — repoint every remaining consumer BEFORE deleting (expect only the test collateral above + the tables.py defs).
- [ ] **Step 2:** Delete the module + tables + tests, apply the collateral fixes, update apt_verify docstring.
- [ ] **Step 3:** `grep -rn "apt_resolve\|resolve_soname_apt\|NATIVE_LIB_TO_APT\|\bTOOL_TO_APT\b\|apt_for_soname\|apt_for_tool" src/ tests/` → ZERO (KEEP-list names may still appear: `CLI_TOOL_TO_APT`, `apt_for_cli_tool`, `NATIVE_RISK_PACKAGES`).
- [ ] **Step 4:** Full suite `tests/depgraph tests/pkg_layer tests/eval` → GREEN (count drops by the deleted `test_apt_resolve` 13 + `test_tables` shrink 4; net vs 1.2c reported). `git rm` the deleted files; commit — `refactor(depgraph): retire apt_resolve + os_resolver-superseded tables (single apt authority); keep CLI runtime-tool table`.
- [ ] **Step 5 (final-verify of the whole 1.2):** re-run the reactive-path cluster + confirm `test_import_probe_nonnative.py` still all-green (the preservation held end-to-end). Optionally re-run a quick `--only requests,pyodbc` eval slice to reconfirm the eval is unmoved (it is, by construction — reactive path is eval-invisible).
