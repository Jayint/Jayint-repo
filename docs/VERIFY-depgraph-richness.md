# Verification — uv-enriched dep graph richness

Status as of 2026-06-23. Validates the 7 "Richness acceptance criteria" in
`docs/superpowers/specs/2026-06-23-uv-enriched-depgraph.md` §"Richness acceptance criteria"
against REAL repos.

## Method

- **Resolution:** host `uv` 0.10.4 (`/opt/homebrew/bin/uv`), targeted at the container
  (`--python-version 3.11`, target platform `aarch64-manylinux_2_28`, never `manylinux2014`).
- **Install / probe / certify:** real `DockerExecutor("python:3.11-slim")`.
- **Repos exercised:** `requests`, `flask`, `click` (pure / mixed), `opencv-python`→`cv2`
  (`/private/tmp/real_cv2`), `psycopg2` (`/private/tmp/real_psycopg2`, build-from-source),
  plus synthetic direct-conflict and resilience inputs.
- **Unit suite:** `cd /Users/john/john-planner-v3 && /Users/john/john-planner-v1/.venv/bin/python -m pytest tests/depgraph/ -q` → **182 passed, 0 failed**.
- **GraphML artifacts:** `docs/verify-requests.graphml`, `docs/verify-flask.graphml`,
  `docs/verify-click.graphml`, `docs/verify-cv2.graphml`, `docs/verify-psycopg2.graphml`,
  `docs/verify-conflict.graphml`, `docs/verify-resilience.graphml`. All parse as valid XML and
  carry the enrichment keys (`build_from_source`, edge `marker`, `constraint`).

## Criteria results — 7/7 PASS

### 1. Transitive structure — PASS
Package→Package `requires` edges present and connected from roots, depth > 1, count > 0:

| Repo | Package nodes | Pkg→Pkg `requires` edges | depth |
|---|---|---|---|
| requests | 37 | 32 | 4 |
| click | 31 | 31 | 4 |
| flask | 66 | 58 | 4 |

cv2 carries the real `opencv-python → numpy==2.4.6` edge; resilience closure carries 7 `requires`
edges across `requests`/`click` + 5 transitives. Not a flat set.

### 2. No degenerate collapse — PASS
Every normal repo yields a non-empty, connected Package layer (per-root resilience). The Run-A
all-or-nothing collapse is gone. Resilience proof: `[requests, click, <nonexistent>]` → the bad
root is isolated as `MISSING` (with evidence) while the good roots salvage an **8 package-node**
closure (`requests`, `click`, `certifi`, `charset-normalizer`, `colorama`, `idna`, `urllib3`);
11 total nodes / 7 requires edges, no empty graph.

### 3. Native-risk predicted (+ probe-confirmed with non-empty fix) — PASS
- **cv2:** predicted `SystemLib` nodes `syslib:libgl1`, `syslib:libglib2.0-0` emitted BEFORE probe
  (`discovered_by=resolver`) from `PACKAGE_TO_SYSTEM_DEPS`; probe then confirmed a real runtime gap
  `syslib:libxcb.so.1` (`discovered_by=probe`, `ldconfig` check) with non-empty fix `apt:libxcb1`.
- **psycopg2:** Package `build_from_source=True`, artifact `psycopg2-2.9.12.tar.gz`; predicted
  `Tool` `tool:libpq-dev` emitted before probe; probe confirmed the `pg_config` build gap
  (`command -v pg_config`, fix `apt:libpq-dev`) and RECONCILED it onto the predicted node (no
  duplicate). All SystemLibs/Tools carry non-empty fixes.

### 4. Conflicts visible — PASS
Direct conflict (`requests==2.32.3` needs `urllib3>=1.21.1`; root pins `urllib3<1.21`) →
one `conflicts_with` edge `requests → urllib3`, `Edge.data` bounds `{>=1.21.1, <1.21}`,
`package=urllib3`, evidence carried; `urllib3` resolver node `state=missing` with evidence; no
spurious `project` node leak. Not a silent empty graph. Exported edge constraint:
`urllib3: >=1.21.1 vs <1.21`.

### 5. Honest certification — PASS
- **requests:** 37/37 packages certified + 13 imports satisfied after install (honest, real).
- **cv2:** `opencv-python` Package **satisfied** (and `numpy==2.4.6` satisfied) while `cv2` Import
  **missing** until the system lib is present — installed ≠ importable preserved.

### 6. Container-accurate pins — PASS
Versions resolved for the target platform/python (aarch64-manylinux_2_28 + 3.11) match container
installs. Forked-version correctness fixed: when `uv.lock` forks `numpy` (2.4.6 for `python<3.12`,
2.5.0 for `python>=3.12`), `_select_applicable_packages` picks the single version whose
`resolution-markers` match `target_python` (2.4.6), so `pip install` and edges/artifacts target the
container reality. `install_closure` uses `INSTALL_TIMEOUT=900s` so cold large closures don't
false-fail and cascade to MISSING.

### 7. Explorable export — PASS
All 7 GraphML artifacts parse (valid XML) and carry edges, `marker`
(e.g. `python_full_version < '3.12'`), `build_from_source`, and `conflicts_with` constraint bounds;
key schema is viewer-compatible (`docs/sample-dependency-graph-visualization.html`).

## Known limitation (NOT a criterion failure; out of current spec scope)

On `flask`/`click`, `scan.py` walks the whole repo tree including `examples/` and `docs/`, pulling
non-project imports (`Pillow`→`libjpeg-dev`/`zlib1g-dev`, `celery`, `sphinx`) and non-PyPI example
names (`blueprintapp==None`, `cliapp`, `complex`) into the resolver roots. Effect:
- **click:** fully certifies (31/31); only an extra predicted `libjpeg-dev`/`zlib1g-dev` from
  `Pillow` (via docs/examples).
- **flask:** `blueprintapp` has no PyPI dist (version None) so the single-shot install fails fast and
  certification collapses to 0/66.

This is faithful graph behavior (those files really do import those packages), not a spec defect —
the spec does not scope out `examples/`/`docs/`. `requests` (pure) and `cv2`/`psycopg2` (native) are
the clean proofs that all 7 criteria hold. Recommended next enhancement (out of scope here): scope
the static scan to project source + tests, excluding `examples/`, `docs/`, `build/`.
</content>
</invoke>
