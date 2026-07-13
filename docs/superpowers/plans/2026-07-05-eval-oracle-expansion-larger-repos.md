# Run Plan — Expand Package-Layer Eval Oracle to 8 Larger Repos

> **Status:** PLAN ONLY — not executed. Formulated 2026-07-05 in worktree `john-v3-multi-lang`.
> **Goal:** grow the package-layer fidelity corpus from 15 → 23 repos by adding 8 larger,
> more-complex, self-hostable Python repos, so pooled recall/precision are more trustworthy and
> the native-syslib + optional-extra dimensions are stressed harder.
> **Authorization:** the eval-oracle Sonnet agents + Docker builds are explicitly authorized;
> graph construction only (never the build-phase repair agent). Commit-local, never push.

## 0. Background (what "the oracle" is)

The package-layer eval compares two sides per repo:
- **OURS** = `run_ours_pkg.py` → `build_graph_construction_only` → PACKAGE closure `{dist: version}` (construction-only, no repair agent).
- **ORACLE** = an **agent-configured working `pip freeze`** — the ground truth. One `oracle/<repo>.json` per repo, schema:
  `{repo, target_python, gate_passed, import_ok, collect_ok, pip_freeze{dist:ver}, install_recipe[], apt_installed[], extra_deps_installed_beyond_extras[], collect_errors_ignored[], notes, blocker}`.
  There is **no runner script** — these were produced ad-hoc by an agent per repo. This plan defines a repeatable oracle-agent contract.

`compare_pkg.py <ours_dir> <oracle_dir>` prints per-repo + pooled recall/precision/version-agreement.
Current baseline (ours_v2 vs oracle, 15 repos): **pooled recall 0.760 / precision 0.536** (0.940/0.505 ex-vizro).

## 1. The 8 repos (Balanced set — user-selected 2026-07-05)

| # | repo | GitHub | pinned ref (record SHA at clone) | target_py | dimension | native / notes | risk |
|---|------|--------|----------------------------------|-----------|-----------|----------------|------|
| 1 | pillow | python-pillow/Pillow | latest stable tag | 3.11 | native/syslib | libjpeg/zlib/freetype; **manylinux wheel exists** → pip gets wheel (tests bundled-lib syslib detection = ~0 unresolved) | low |
| 2 | lxml | lxml/lxml | latest stable tag | 3.11 | native/syslib | libxml2/libxslt; wheel exists → bundled | low |
| 3 | cryptography | pyca/cryptography | latest stable tag | 3.11 | native/rust | openssl+rust in sdist, **wheel bundles openssl** → no rust/apt needed; stresses bundled-syslib no-false-positive | low |
| 4 | psycopg2 | psycopg/psycopg2 | latest stable tag | 3.11 | native/tool | **source dist needs `pg_config` + libpq-dev** (canonical tool-node case); runtime needs libpq5 | **HIGH** — test collect may need a live DB (see §6) |
| 5 | aiohttp | aio-libs/aiohttp | latest stable tag | 3.11 | native+optional | C speedups (wheel), `[speedups]` extras (aiodns/brotli) | low |
| 6 | fastapi | fastapi/fastapi | latest stable tag | 3.11 | optional-heavy | `[all]` extras (uvicorn/httpx/jinja2/…) — precision stress | low |
| 7 | celery | celery/celery | latest stable tag | 3.11 | optional-heavy | many broker/backend extras (redis/amqp/…) — precision stress | med (broker extras sprawl) |
| 8 | pydantic | pydantic/pydantic | latest stable tag | 3.11 | rust+optional | pydantic-core (wheel), moderate test deps | low |

**Interpreter:** default **3.11** for all 8 → keeps the combined 23-repo pooled stats on one interpreter (existing 15 are 3.11). Override only if a repo's `requires-python` excludes 3.11 (record in the manifest).

## 2. Corpus setup (avoid symlink pollution)

`outputs/graph_fidelity/_smoke` is a **symlink into the core-autoresearch worktree** — do NOT clone into it. Instead:

1. `mkdir -p outputs/graph_fidelity/_smoke_large` (a real dir in THIS worktree; `outputs/` is gitignored so it never commits).
2. For each repo: `git clone --depth 1 --branch <latest-stable-tag> https://github.com/<org>/<repo> outputs/graph_fidelity/_smoke_large/<name>` then record the resolved SHA.
3. Write `outputs/graph_fidelity/_smoke_large/MANIFEST.json` = `{<name>: {github, ref, sha, target_python}}` for reproducibility.
4. Do the clones in job-tmp scratch first if disk is tight; monitor `df -h` (62 GB free, ~85% used — fine for 8, watch pandas-class builds if the set ever grows).

## 3. Phase 1 — Oracle generation (agent-configured working `pip freeze`)

Fan-out one **Sonnet** subagent per repo (concurrency cap 3–4 to avoid Docker/network contention; ~2–3 waves). Each agent is the eval oracle — it configures a genuinely working env and records it honestly. It does NOT touch construction code.

**Per-repo agent contract:**
- **Container:** `docker run --platform linux/amd64 python:<target_py>-slim`, repo bind-mounted read-write at `/repo`.
- **Configure (iterate until the GATE passes):**
  - `apt-get update && apt-get install -y build-essential git` + repo-specific `-dev` libs only as build errors demand (e.g. psycopg2 → `libpq-dev`; if a repo forces an sdist build needing headers). Prefer wheels; only apt when a build actually fails.
  - `pip install -e .` + the repo's **test** extras / PEP 735 dependency-groups (e.g. `.[test]`, `.[all]`, `--group test`). Read the repo's own CONTRIBUTING/tox/CI to learn the intended test install.
  - Install the minimum additional packages needed so **both** gates pass; do **not** over-install beyond what import+collect need (the oracle must be a *tight* working set, not a kitchen sink).
- **GATE (both required):**
  - `import_ok`: `python -c "import <top_import_name>"` exits 0.
  - `collect_ok`: `pytest --collect-only -q` exits 0 (or the repo's documented collect cmd). Known-unimportable optional test modules may be recorded in `collect_errors_ignored[]` and excluded via `--ignore`, but only with a one-line justification each.
- **Freeze:** `pip freeze` → parse to `{dist: version}` for `pip_freeze` (exclude the project's own editable line / `-e` self).
- **Emit** `outputs/graph_fidelity/oracle_large/<name>.json` in the EXACT existing schema (see §0), `blocker: null` on success, or a precise blocker string + `gate_passed:false` on failure.
- **Report** back: name, gate_passed, |pip_freeze|, apt_installed, blocker.

**Determinism note:** the oracle is a "works-now" freeze; to reduce PyPI drift, pass `pip install`… under a pinned index date is NOT natively supported, so instead **record the freeze date** in `notes` and rely on membership (recall/precision are version-agnostic). Re-runs may show patch-version drift in `pip_freeze` — expected, does not move recall/precision.

## 4. Phase 2 — Ours generation (construction on the new corpus)

`run_ours_pkg.py` hardcodes `SMOKE = outputs/graph_fidelity/_smoke`. Two clean options (pick A):
- **A (recommended):** add a 1-line corpus-root override — read `SMOKE` from env `OURS_SMOKE_ROOT` (default the existing path). Then:
  `OURS_SMOKE_ROOT=outputs/graph_fidelity/_smoke_large python3 scripts/eval/graph_fidelity/run_ours_pkg.py pillow,lxml,cryptography,psycopg2,aiohttp,fastapi,celery,pydantic outputs/graph_fidelity/ours_large`
- **B:** a sibling `run_ours_pkg_large.py` with the new SMOKE path.

This runs `build_graph_construction_only` (exercises `build_dep_graph` → the SEAM dispatch) on each new repo → `ours_large/<name>.json` with the same `{packages, package_count, audit_repaired, unresolved_imports, …}` shape. Construction-only; no repair agent.

## 5. Phase 3 — Compare + report

1. **New-8 table:** `python3 outputs/graph_fidelity/pkg_lock_ab/compare_pkg.py outputs/graph_fidelity/ours_large outputs/graph_fidelity/oracle_large` → per-repo + pooled recall/precision for the 8.
2. **Combined-23:** merge dirs (symlink the 15 `ours_v2/*` + 8 `ours_large/*` into `ours_all/`; same for oracle) → `compare_pkg ours_all oracle_all` → the headline pooled recall/precision over 23 repos.
3. Save `outputs/graph_fidelity/pkg_lock_ab/report_large.txt` and `report_combined.txt`.
4. Interpret: does adding native/optional-heavy repos move pooled precision (expected: native repos with bundled wheels ≈ neutral; fastapi/celery/aiohttp `[all]`/broker extras likely LOWER precision — the over-inclusion story we want to quantify)? Flag any repo where `gate_passed:false` (oracle blocker) or recall < 0.7 (under-coverage worth a look).

## 6. Risks & mitigations

- **psycopg2 collect needs a live DB (HIGH):** `pytest --collect-only` should only *import* test modules (no connection). If a conftest/test connects at import, the agent: (a) sets a dummy `PSYCOPG2_TESTDB`/`DSN` env, (b) `--ignore` the DB-requiring test dir with `collect_errors_ignored` note, or (c) if unrecoverable, mark `blocker` + substitute **`psycopg` (psycopg3)** which is more modular. Decide at run time; record the choice.
- **Native build blockers:** balanced-8 all have manylinux wheels for the heavy bits (pillow/lxml/cryptography/pydantic/aiohttp), so `pip install` gets wheels → no apt gymnastics. Only psycopg2 (source) forces `pg_config`. If a wheel is unexpectedly absent for the pinned version, agent apts the `-dev` libs and records them.
- **celery extras sprawl:** install only `.[test]` (or the CI's test target), NOT every broker extra — keep the oracle a tight working set; note which extras were included.
- **Oracle non-determinism / PyPI drift:** membership-based recall/precision are stable; version columns may drift. Pin repo refs; record freeze date.
- **Disk/time:** ~8 repos, monitor `df -h`; clone `--depth 1`; `docker system prune` between waves only if space runs low (don't prune the cached `python:3.x-slim` bases).
- **Seam relevance:** `ours` runs through the migrated `build_dep_graph`, so this ALSO extends the seam's byte-identity evidence to 8 harder repos (a free bonus, not the goal).

## 7. Deliverables (all gitignored under `outputs/`, none pushed)

- `outputs/graph_fidelity/_smoke_large/` (+ `MANIFEST.json`) — pinned clones.
- `outputs/graph_fidelity/oracle_large/*.json` — 8 agent-configured freezes (ground truth).
- `outputs/graph_fidelity/ours_large/*.json` — 8 construction closures.
- `outputs/graph_fidelity/pkg_lock_ab/report_large.txt` + `report_combined.txt` — the new tables.
- A CHANGELOG-planner-v3 entry (Observation→Why→What→Verification) summarizing the expanded pooled numbers.
- Optional: a memory note update to [[node-package-fidelity-eval-design]] / medlarge15 corpus rule recording the 23-repo corpus.

## 8. Cost / time estimate

- Oracle agents: 8 × Sonnet + Docker env-config, ~10–30 min each; with 3–4 concurrency ≈ **45–90 min wall**, moderate token spend.
- Ours construction: 8 × ~2–5 min Docker ≈ 20–40 min (can overlap).
- Compare + report: seconds.
- **Total ≈ 1.5–2.5 hrs wall**, runs as a background job; check back for the combined-23 table.

## 9. Defaults chosen (change before running if desired)

- Set = **Balanced 8** (pillow, lxml, cryptography, psycopg2, aiohttp, fastapi, celery, pydantic).
- Interpreter = **3.11** for all.
- Oracle agent model = **Sonnet** (mechanical env-config; adjust to opus if a repo proves stubborn).
- Corpus = fresh `_smoke_large` (no symlink pollution); refs pinned to latest stable tag, SHA recorded.
- Everything commit-local; `outputs/` gitignored; **never push**.

## 10. Execution order (when greenlit)

1. §2 clone+pin 8 repos → `_smoke_large` + MANIFEST.
2. §4-A add `OURS_SMOKE_ROOT` env override to `run_ours_pkg.py` (1 line).
3. §3 fan-out 8 oracle agents (waves of 3–4) → `oracle_large/`.
4. §4 run ours construction → `ours_large/`.
5. §5 compare (new-8 + combined-23) → reports.
6. §7 write CHANGELOG + memory; report pooled deltas. No push.
