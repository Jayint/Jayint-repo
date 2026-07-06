# E2E Build-Script Effectiveness Eval — Design

**Date:** 2026-07-06
**Branch:** `john-v3-multi-lang`
**Status:** design (pre-plan)

## Goal

Measure the **first-pass effectiveness of the initial `setup.sh`** that graph
construction hands the agent: for a corpus of real repos, does
construction-only `build_dep_graph` → `render_build_script` produce a build
script that, when replayed in a fresh container, yields a working-enough
environment — **language packages installed + system packages present** — with
no agent and no repair loop.

This is a **repo-level roll-up** that composes the existing per-layer precision/
recall scripts (`language_package_eval`, `package_installability`) into one
end-to-end outcome, plus a failure attribution that says *which layer* to fix.
It is **not** a rewrite: it reuses the proven construction/replay primitives
already in `src/eval/language_package_eval/coverage.py`.

**No oracle.** The fresh-container replay *is* the ground truth for "does the
build script work" — grading against a held-out Dockerfile/CI recipe would be
running a noisy proxy alongside the real signal. Failure attribution
(language / system / render) comes from the actual execution error text, not a
recipe diff. See §6.

## Scope & constraints

- **Python-only (hard constraint).** `build_dep_graph` dispatches through
  `src/ecosystems/registry.py`, where `PROVIDERS = (PythonProvider(),)`. Node/Go
  compute package closures but cannot produce a graph or a `setup.sh`, so an
  e2e build-script eval can only run on Python repos today. "Language + system
  package" here means **Python pip closure + apt/syslib tier**. Node/Go remain
  closure-only (`language_package_eval/{node,go}/`) until their providers wire
  into the registry.
- **SERVICE / CONFIG excluded.** Service-node detection is explicitly deferred:
  SERVICE/CONFIG needs are neither predicted-for nor attributed. They resurface
  only as the documented confound on `tests_passed` — see §5.
- **First-pass, no repair.** No agent, no repair loop. Construction intercepts
  the repo's real pytest during the build (via `coverage._ConstructionOnlyExecutor`);
  the *replay* runs in a separate fresh container.
- **Construction reads no recipe (production fidelity, not an eval trick).**
  `build_dep_graph` constructs the graph *without* a Dockerfile/CI file — that is
  the whole point of what it replaces, so the eval simply runs it as-is. With the
  oracle gone there is no held-out ground truth to "leak" against; the container
  execution is the ground truth. Inherited unchanged from
  `build_graph_construction_only`.
- **Bounded, foreground execution.** Every container step runs foreground with a
  per-step timeout (no backgrounded Docker — prior agents stalled on it). The
  test-run rung gets its own wall-clock cap and runs with network off by default.

## Architecture / module layout

New sibling module, `src/eval/build_script_eval/`:

```
src/eval/build_script_eval/
  __init__.py
  corpus.py       # committed manifest: repo -> (git url, ref, stratum, feasible) + STRATA + select()
  fetch.py        # clone/checkout corpus repos into gitignored outputs/ smoke dir
  replay.py       # the replay LADDER (install -> env_works -> tests_ran -> tests_passed), bounded
  scorecard.py    # per-repo scorecard: headline + language/system gaps + attribution (pure core)
  report.py       # stratified aggregate report.md
  __main__.py     # CLI: python -m src.eval.build_script_eval --run / --score / --fetch
tests/eval/build_script_eval/
  test_scorecard.py     # pure: headline gate, attribution labels, gap clustering, strata pooling
  test_corpus.py        # manifest select()/strata validation
  test_replay_ladder.py # ladder classification from synthetic probe output (pure parts)
outputs/build_script_eval/     # gitignored: fetched repos + scorecards + report.md
```

**Reuse boundary:** `scorecard.py`/`replay.py` import the existing primitives;
`coverage.py` and `render_fidelity.py` are **not modified**.

## Per-repo pipeline

Every step below reuses existing code except the replay ladder (§5) and the
repo-level scorer (§6).

1. `coverage.base_image_for_repo(repo)` → `(image, minor, reason)` *(reuse)*
2. `coverage.build_graph_construction_only(repo, image, minor)` → `DepGraph`
   — no agent, no repair, real pytest intercepted. **The construction under test.** *(reuse)*
3. `render_build_script(graph, ())` → the `setup.sh` text *(reuse)*
4. **Static pre-gate:** `render_fidelity.check_render(graph, setup_sh)` — `bash -n`
   valid, single-emit, topo order OK, editable-last. A script that fails this
   never reaches a container and is attributed to `render_bug`. *(reuse)*
5. **Fresh `-slim` replay ladder** (§5) — new `replay.run_replay_ladder(...)`.

## 5. The replay ladder (new — `replay.py`)

A single fresh mounted container (`coverage._MountedContainer`), run the rungs in
order, stop attributing further rungs once one fails but always record how far it
got. Reuses `_MountedContainer`, `_write_file`, `classify_execution_failures`,
`first_failure_evidence`, `top_level_import_name`.

| Rung | Command | Meaning |
|---|---|---|
| `install_ok` | `cd repo && bash -x /setup.sh` (rc 0) | the build script itself ran clean |
| `env_works` | `python3 -c "import <top>"` + `pytest --collect-only -q` green | **HEADLINE gate** — env imports & tests collect |
| `tests_ran` | `pytest -q` reached a pass/fail verdict (exit 0 or 1; **not** 2/collection-error) | tests actually executed — service-*independent* |
| `tests_passed` | `pytest -q` exit 0 | full green — **caveated** diagnostic |

Rules:
- `env_works` requires `install_ok`; `tests_ran` requires `env_works`;
  `tests_passed` requires `tests_ran`. The ladder is monotonic — record the
  highest rung reached.
- **`tests_ran` is the clean signal.** A test that fails because it needs a
  Postgres/Redis service still counts as "ran" (pytest produced a verdict). This
  is why `tests_ran` is service-independent and safe to report as env quality.
- **`tests_passed` is confounded and carries a loud caveat** in every report:
  service/config/fixture/network failures are out of scope until service
  detection lands, so a low `tests_passed` is frequently *not* a graph fault.
  It is a diagnostic, never a gate, never the headline.
- **Bounded execution:** `pytest -q` runs foreground with a per-repo timeout
  (default 600s) and `PYTEST_ADDOPTS=-p no:cacheprovider`. Install rungs need
  network (pip); the test rung optionally isolates it by `docker network
  disconnect` on the *running* replay container just before the `pytest -q` exec
  (a corpus row may opt out to keep network). A timeout is recorded as
  `tests_ran=False, reason="timeout"` — never a hang.
- **Bootstrap hygiene (inherited):** `pytest` is pip-installed as the probe's own
  tool; a failed bootstrap is never misclassified as a graph-caused missing
  PACKAGE (mirrors `coverage.run_execution_probe`).

## 6. Metrics (new pure core — `scorecard.py`)

**Headline — `first_pass_env_works` rate:** per repo = `install_ok AND env_works`
(no dep/import/collect gaps). Corpus pass-rate over **feasible** repos, reported
**overall and per stratum** (`S_syslib` vs `S_control`). Infeasible repos
(`baseline_labels`) are excluded from the denominator.

**Replay-ladder rates:** fraction of feasible repos reaching each rung
(`install_ok`, `env_works`, `tests_ran`, `tests_passed`), overall + per stratum.

**Diagnostic — observed gaps, not a recall fraction.** With no oracle there is no
denominator, so instead of `recall = 8/10` the eval reports **observed-gap counts
+ clusters** from the execution error text — which is more actionable and free of
proxy noise:

- **language gaps:** execution `ModuleNotFoundError` occurrences, deduped per
  repo, clustered across the corpus (`missing_node_clusters`, PACKAGE tier).
- **system gaps:** execution `.so: cannot open shared object` / `command not
  found` / apt-install-failure occurrences, clustered (SYSTEM_LIB / TOOL tier).
  This is the tier the just-landed syslib detector feeds, and — critically — a
  missing `.so` surfaces at the **`import` / `--collect-only` rung**, which is
  service-independent, so the `S_syslib` stratum gets a clean signal before the
  `tests_passed` confound.

The clusters answer "where does the build script under-cover, and how often" —
e.g. "`libpq-dev` missing in 3 repos" — which is the actual fix-next list.

**Predicted-set reporting (for over-prediction):** the graph's predicted apt set
(`coverage.apt_names_in_graph`) and package set (`coverage.package_versions_in_graph`)
are recorded per repo so over-prediction is visible even though there is no oracle
"extra" diff — the `S_control` stratum is the baseline (its predicted apt set
should be empty).

**apt-safety (over-prediction):** did the apt tier install clean? Records the
syslib plan's **`apt == 0` invariant** at the repo level — a `system_gap` from an
apt install *failing* (over-prediction) is reported separately from
under-coverage. On an `S_control` repo, any nonzero apt tier is an
over-prediction regression. Ties the e2e back to the `package_installability`
gate.

**Failure attribution** (one label per *failing* repo, from
`coverage.classify_execution_failures`, which already tiers gaps):

- `render_bug` — static pre-gate failed, or `setup.sh` failed to run, or the
  failing import/collect is the **repo's own package** (the known "renderer never
  emits the PROJECT node's install" gap — a src-layout limitation, not a
  coverage miss).
- `language_gap` — a PACKAGE need missing (`ModuleNotFoundError` after install).
- `system_gap` — a SYSTEM_LIB/TOOL need missing (`.so` not found / command not
  found / apt install broke).
- `infeasible` — repo itself can't build (excluded from headline denominator).

**Actionable clusters:** `coverage.missing_node_clusters` — missing nodes ranked
by `(count, tier, id)`, the "fix this next" list, restricted to PACKAGE /
SYSTEM_LIB / TOOL.

## 7. Corpus & strata (`corpus.py` + `fetch.py`)

Committed manifest: each row = `name, git_url, ref (pinned sha), stratum,
feasible, top_import?`. **No held-out recipe required** (the oracle is gone) —
the only corpus requirement is a **runnable test suite**, so the corpus can be
much broader. Two strata:

- **`S_control`** — pure-Python repos needing no apt (reuse qualifying
  `coverage.py` corpus rows: e.g. `fastapi/typer`,
  `python-semantic-release/python-semantic-release`). Baseline for
  over-prediction: the predicted apt tier should stay empty; a nonzero apt tier
  here is an over-prediction regression.
- **`S_syslib`** — system-package-heavy. Curation rule (replaces the old
  recipe-oracle requirement): pick repos whose **tests import the native
  extension**, so a missing `.so` surfaces at the import/collect rung without
  needing a service. `psycopg2`→libpq, `pyodbc`→unixODBC,
  `mysqlclient`→libmysqlclient, `opencv`→libGL, `Pillow`→libjpeg/zlib,
  `lxml`→libxml2/xslt, `cryptography`→libssl/ffi (final list pinned in the plan).

`fetch.py` clones at the pinned sha into `outputs/build_script_eval/_smoke/`
(gitignored). `select()` filters by name/stratum with fail-fast on unknown
stratum (mirrors `package_installability.corpus.select_corpus`).

## 8. Reused vs new surface

**Reused unchanged (imported):**
`coverage.{base_image_for_repo, build_graph_construction_only, _MountedContainer,
_write_file, classify_execution_failures, first_failure_evidence,
top_level_import_name, apt_names_in_graph, package_versions_in_graph,
missing_node_clusters, _docker_available}` · `render_fidelity.check_render` ·
`build_script.render_build_script`. (`missing_node_clusters` already tolerates an
absent oracle branch — it clusters whatever `execution_missing` gaps are present,
so it needs no change.)

**Dropped from the reuse set** (oracle-only): `oracle.parse_oracle`,
`coverage.{diff_packages, diff_membership, pooled_recall_by_tier,
score_repo_against_oracle}` — not imported by this module.

**New (thin):** `corpus.py`, `fetch.py`, `replay.py` (the ladder — the only new
container logic), `scorecard.py` (repo-level headline + attribution + gap
clustering, **pure**), `report.py` (stratified `report.md`), `__main__.py` (CLI),
and their unit tests.

## 9. Output artifacts

- Per-repo `outputs/build_script_eval/<org>__<repo>.json` — scorecard: image,
  stratum, feasible, graph tier counts, predicted apt/package sets, ladder result
  (highest rung + per-rung detail), language & system **gaps** (observed, typed),
  apt-safety, attribution label, first-failure evidence, concerns (the
  `tests_passed` service-confound caveat).
- `outputs/build_script_eval/report.md` — headline `first_pass_env_works` rate
  (overall + per stratum), the replay-ladder funnel, language- and system-gap
  **counts + clusters**, apt-safety count, attribution histogram, and the standing
  `tests_passed` caveat.

## 10. Testing plan

- **Pure unit tests** (no Docker): headline gate, attribution labeling, ladder
  classification from synthetic probe output, gap clustering, strata pooling,
  manifest `select()`/strata validation. This is the bulk — the new logic is
  mostly pure.
- **Container integration** guarded by `coverage._docker_available` (skip when
  Docker absent), mirroring `coverage.py`'s split.
- No new suite regression: the module is additive; `coverage.py`/`render_fidelity.py`
  untouched.

## 11. Decisions taken (defaults, vetoable)

- **Execution-only, no oracle.** The fresh-container replay is the ground truth;
  a held-out-recipe oracle would be a noisy proxy for the same question and would
  force heavy corpus curation. Attribution comes from execution error text;
  coverage diagnostics are gap counts + clusters, not a recall fraction.
- **New sibling module**, not a mutation of `coverage.py` — keeps the per-layer
  eval intact and importable.
- **Headline = first-pass `env_works`** (install-clean + import + collect);
  `tests_ran`/`tests_passed` are added ladder rungs, not the headline.
- **`tests_passed` reported but caveated** — never a gate; every report states the
  service/config confound.
- **Repo-own-package import failure → `render_bug`**, not `language_gap` (the
  PROJECT-node install gap is a renderer limitation, not a coverage miss).

## 12. Out of scope

- Node / Go e2e setup.sh (no provider wired — closure-only until then).
- SERVICE / CONFIG detection and grading.
- A held-out-recipe oracle / static recall fraction (dropped — ground-truth
  execution only).
- Agent / repair loop (this measures the *first-pass* starting point only).
- Making `tests_passed` a gate or headline.
