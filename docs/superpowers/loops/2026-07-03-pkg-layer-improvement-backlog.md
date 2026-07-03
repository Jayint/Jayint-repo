# pkg_layer — improvement backlog (from 3 Sonnet review agents, 2026-07-03)

Synthesis of a correctness lens, a next-increment lens, and an eval-rigor lens over the new
`src/python_deps/pkg_layer/` module + the A/B eval. Ranked; each item is concrete + grounded.

## Refined verdict (honest)
The A/B result (30 divergent adds, 0 good / 30 bad → verifier) holds NARROWLY: the CURRENT design
over-installs optional extras via the `package_roots`/`needed_extras` bug; the contract-only design
doesn't. But it is **not a strict free dominance** — the review surfaced two places CURRENT is
actually MORE correct, plus a not-yet-real advantage:

1. **NEW drops per-dependency extras.** `depgraph/roots._manifest_root_token` preserves
   `pbs-installer[download,install]` / `cachecontrol[filecache]` (Task-8 fix, so the extra's
   transitive deps resolve). `pkg_layer.planes.DeclaredDep` has no `extras` field and
   `contract.read_contract` never reads `.extras`; `pkg_layer_ab._bare_name` strips the bracket
   before comparing → poetry reports "0 divergence / identical" while NEW would resolve bare
   `pbs-installer`, silently missing the extra's transitive deps. A real NEW-side regression the
   metric structurally hides.
2. **≥3/30 "bad" adds are test-collection-needed.** `pytest_mock` (pip-tools tests, unguarded),
   `tomli_w` (pip-tools conftest.py — breaks whole collection), `bs4` (datasette, ~10 test files)
   are declared under excluded dev/test extras. Under this eval's OWN arbiter (`pytest --collect-only`)
   omitting them is a regression, so "bad" is only right for a runtime-minimal goal. The test tier
   needs them via CI-mined `needed_extras` (which NEITHER design mines yet).
3. **NEW's `under_declared` "completeness" advantage is not yet real.** The Environment plane
   (packages_distributions) is never consulted outside fakes (`provided=()` always), so
   `under_declared` is a static name-match — inflated by the empty closure (transitive imports flag)
   and contaminated by a narrower scan-exclusion list.

Net: contract-only is the right DIRECTION and fixes a real bug, but to actually dominate it needs
per-dep extras + CI-mined needed_extras + a wired Environment plane; the current metric overstates
the win.

## P0 — correctness bugs in the new module (cheap, do first)
- **Share the scan-exclusion list.** `usage.py:38` imports the NARROW `EXCLUDED_DIRS` from
  `import_graph.py` (vcs/build/venv only); `depgraph/scan.py:_EXCLUDED_SEGMENTS` also excludes
  docs/examples/scripts/tools/.github. Result: scrapy `under_declared` includes `docutils`/`sphinx`
  (doc-build only), pydantic includes `mypy`/`mkdocs`. Fix: import/share `_EXCLUDED_SEGMENTS`.
- **File-cap mismatch.** `usage._iter_python_files` is unbounded but gates on `scan_imports`, which
  caps at 1000 files / 500 KB → external imports in files past the cap are silently dropped from
  the honest signal. Fix: reuse `import_graph`'s exact file set (or drop the membership gate and
  classify RUNTIME/stdlib/local like the dynamic branch already does).
- **Add `extras` to `DeclaredDep` + `read_contract` + carry into roots** (fixes regression #1).
- **`resolution_anomaly` landmine.** `construct.build_package_layer` hardcodes `closure=()` →
  `resolution_anomaly` = the full declared set on every non-empty repo. Add an optional
  `closure=` param (symmetric with `provided=`); until wired, don't emit a deterministic wrong value.

## P1 — make the signal real (the "next increment")
- **Wire the Environment plane.** Reuse `depgraph/relink.py:26-44` (`packages_distributions` cmd +
  parser) + the `Executor` protocol → build `ProvidedEdge`s → thread into `build_package_layer`'s
  `provided=`. Converts `under_declared` from a name-match guess to a host-certified needle. This is
  the single gap between "pure construction" and the design's named intent; prerequisite for a real
  completeness gate and the repair-loop verifier. RECOMMENDED next.
- **Wire the Closure plane** via the already-built `UvResolveSource` (`closure.py:81-135`) — uv is a
  host binary (no Docker needed), so add ONE gated live test to verify its provenance filter, then
  call it from `build_package_layer`. Makes `under_declared`/`resolution_anomaly` account for
  transitively-satisfied imports.

## P2 — eval rigor (strengthen / falsify the verdict)
- **Relabel the 30 divergent adds against the eval's OWN arbiter** (test-collection-needed vs
  runtime-needed); adjudicate `under_declared` against gold (currently reported with zero grading).
- **End-goal Track**: render both designs' setup.sh → fresh `-slim` replay via
  `coverage.run_execution_probe` → does each build an env that installs + `pytest --collect-only`s.
  Turns "no loss" from a label-inference into an observed pass/fail. (Predicted to fail for NEW on
  ≥2 repos per finding #2.)
- **Track B for NEW**: run `repair.repair_import` over `fault_injection`'s existing 17 targets with a
  real `Verifier` (throwaway-venv `pip install` check) — the untested "verifier+repair recovers both
  alias and identity" dominance claim (predicted 17/17, currently unproven).
- **Perturbed-corpus Track A**: apply Track-B deletion perturbation through the `pkg_layer_ab`
  divergence/adjudication path, so the headline verdict is computed on a corpus that CAN produce a
  "good" cell (16 well-declared libs structurally can't).
- **Cross-check gold labels** with `usage.scan_usage`'s own OPTIONAL/TYPING tagging (catches the
  pytest_mock/tomli_w/bs4 class of mislabel for free).

## P3 — hygiene
- Unify the 5 inconsistent `_canon`/normalize impls (only pkg_layer + root_selection_ab `.strip()`).
- Add a `repair → edge` adapter (`CandidateEdge`/`ProvidedEdge`) or mark `CandidateEdge` as
  forward-looking; today repair returns `RepairOutcome` with no seam back into `PackageLayer`.
