# Spec: Role-aware declaration reading (testability-scoped roots)

Status: spec (ready for SDD)
Branch: `john-planner-v3-core-autoresearch`
Motivation source: the package-layer fidelity eval (`outputs/graph_fidelity/pkg_lock_ab/`, memory `pkg-layer-vs-agent-freeze-eval`) — our Phase-1 PACKAGE closure vs 15 agent-configured working `pip freeze` oracles: recall 0.85 / precision 0.70, versions near-exact. The whole precision/recall deficit is **declaration-role misclassification**, not resolution.

## Problem

Our declaration reader (`evidence.py`) classifies every declared dependency by *source location* into just two roles — runtime (`kind="dependency"`, always a root) and feature-extra (`kind="optional_dependency"`, gated by `needed_extras`). It has no concept of a **dev/test role** or of the **build goal**. Three concrete, code-confirmed failures result:

1. **PEP 735 `[dependency-groups]` is not read at all.** No collector opens the table. 6+ of 15 sampled repos (flask, slither, typer, anyio, sqlalchemy, mvt) declare their test deps there → their test tier is invisible under *any* config. This is the dominant recall miss (pytest, pytest-cov/xdist/mock, coverage, hypothesis, execnet, mypy, ruff — 62% of all misses).
2. **`requirements*.txt` is read as runtime, ungated.** `_collect_requirements_files` tags every line `kind="dependency"`. httpx's root `requirements.txt` pins `mkdocs`, `mkdocs-material`, `twine`, `build`, `mypy`, `ruff`, … as docs/lint tooling — we pull them all as **runtime** roots (httpx precision 0.42; over-inclusion dominated by docs 31 + jupyter 34 + dev-tooling).
3. **`-e .[extras]` / `-r file` lines are dropped** (anything starting with `-` is skipped in `_read_requirement_lines`). httpx's `-e .[brotli,cli,http2,socks,zstd]` vanishes, so the extras its tests need (`h2`, `hpack`, `hyperframe`, `brotli`, `socksio`) are exactly its MISSING list. One file, both a miss and an over-pull.

## Goal (single, fixed)

Make the graph's PACKAGE closure cover **the requirements a repo needs to run its tests** — the *testability* scope. Because the testability env is a strict superset of the installability env (tests run ⇒ app installs), this is the single maximal target; installability comes for free inside it. There is **no multi-goal machinery** — scope is fixed to testability.

The graph certifies NECESSARY (static reading gets recall high); the testability gate (`pytest --collect-only` / `pytest -q`) + the Phase-A repair loop certify SUFFICIENT and mop up the residue. The reader does not need to be perfect — it needs recall high enough that the gate + repair converge.

## Non-goals

- Detecting pytest plugins by entry-point introspection (we capture them only because they're now *declared* in the test group we read).
- Fixing `setup.py` dynamic/variable-indirected `install_requires` (a separate AST-evaluation problem).
- Multi-goal / install-vs-test scope selection (explicitly collapsed away).
- `pkg_layer/` parity (that module is the A/B reference, not the production path; a follow-up if it's promoted).

## Current state (what changes)

- `evidence.py::collect_python_dependency_evidence` runs 5 collectors → flat `evidence.declared_dependencies: list[PythonRequirement]`. `PythonRequirement = (name, specifier, marker, extras, source, kind="dependency", trust="high")`.
- `roots.py::select_roots(repo, graph, needed_extras=frozenset())` filters: `optional_dependency` kept iff `_requirement_group(req.source) in needed_extras`; runtime always kept; PEP 508 markers evaluated vs target; dedup; emit `(None, _manifest_root_token(req))`.
- Group name is parsed from `source` by `_requirement_group` via `_OPTIONAL_GROUP_RE = (?:optional-dependencies|extras_require)\.(.+)$`.

## Design

### Role model (minimal data-model change)

Extend `PythonRequirement.kind` with one new value, **`"dev_group"`** (PEP 735 groups + dev/test requirements files). Existing values unchanged: `"dependency"` (runtime), `"optional_dependency"` (feature extra), `"constraint"`. The group/role sub-label stays embedded in `source` (as it already is for extras) and is extracted by an extended `_requirement_group`. No new field required; `to_dict()` unaffected.

### Change 1 — new collector `_collect_dependency_groups` (PEP 735)

In `evidence.py`, add a collector (registered in the `collectors` tuple) that reads `[dependency-groups]` from `pyproject.toml`:
- Each group value is a list of requirement strings **or** `{include-group = "<name>"}` reference objects.
- **Resolve `include-group` transitively with cycle detection** (PEP 735 permits group→group includes). Flatten to concrete requirement strings, attributed to the *top-level* group being expanded.
- Emit each as `PythonRequirement(..., kind="dev_group", source=f"pyproject.toml:dependency-groups.{group}", trust="medium")`.

### Change 2 — role-classify `_collect_requirements_files` + handle `-e` / `-r`

Replace the "every line is runtime" behavior:
- **Role from filename** (case-insensitive basename match):
  - runtime → `requirements.txt`, `requirements/base.txt`, `requirements/main.txt`, `requirements/prod*.txt` → `kind="dependency"`.
  - dev/test → `requirements-dev*.txt`, `dev-requirements.txt`, `requirements-test*.txt`, `test-requirements.txt`, `requirements/dev*.txt`, `requirements/test*.txt`, `tests/requirements*.txt` → `kind="dev_group"`, `source=f"requirements-file.{role}"` where role ∈ {dev, test}.
  - docs → `requirements-docs*.txt`, `docs/requirements*.txt`, `requirements/docs*.txt` → `kind="dev_group"`, `source="requirements-file.docs"` (dropped by the scope policy, Change 3).
- **`-e .[extras]` lines**: instead of skipping, parse the bracketed extras and record them on a new evidence field `evidence.used_extras: set[str]` (the repo's own dev requirements activating those extras is an authoritative "needed" signal). A bare `-e .` (no extras) is the project itself → ignore.
- **`-r other.txt` / `-c other.txt` lines**: follow the include — read the referenced file (path-resolved relative to the including file), applying filename-role inference to the *referenced* file; guard against cycles and cap include depth (e.g. ≤ 5).
- **Widen discovery** beyond the root glob to an allowlisted set of nested dirs: `requirements/`, `tests/`, `test/`, `docs/` (bounded; do not walk the whole tree).

### Change 3 — fixed testability-scope gating in `select_roots`

Replace the `needed_extras`-membership check with a fixed policy `_in_test_scope(req, in_scope_extras) -> bool`:
- `kind == "dependency"` → **True** (runtime always in).
- `kind == "optional_dependency"` (feature extra) → **True iff** its group ∈ `in_scope_extras`. Extras stay gated because mutually-exclusive extras (cpu/gpu, conflicting DB drivers) make the resolve unsatisfiable — the exact bug `needed_extras` was built to prevent. `in_scope_extras = needed_extras ∪ evidence.used_extras` (the `-e .[…]` signals from Change 2). Extras the *tests import* but no signal names are left to the Phase-A repair loop.
- `kind == "dev_group"` → **True unless** the group name matches the small **docs/release denylist** `{docs, doc, documentation, release, publish, deploy, benchmark, benchmarks, profiling, examples, demo}`. I.e. default-include every dev/test/lint/typing group (recall-first; lint/type deps are small, additive, and are what the maintainer bundles as "for development"), and exclude only the clearly-non-test groups that bloat the closure (docs/sphinx/mkdocs is the big one). `_requirement_group` is extended to parse the group from `dependency-groups.<name>` and `requirements-file.<role>` sources too.
- `kind == "constraint"` → not a root (unchanged; feeds version constraints elsewhere).

`select_roots` keeps its signature (`needed_extras` stays as the caller/CI-supplied extras override); the change is that dev-groups are now included by policy and `requirements.txt` roles are honored. Default construction (`build_dep_graph`, `needed_extras=frozenset()`) now yields a **testability-scoped** closure (runtime + dev/test groups + `-e`-signalled extras) instead of runtime-only.

## Test plan (TDD — RED first, per change)

Change 1 (`tests/.../test_evidence.py` or new `test_dependency_groups.py`):
- `[dependency-groups].test = ["pytest","pytest-cov"]` → two `kind="dev_group"` reqs with `source` naming group `test`.
- `include-group` resolution: `typing = [{include-group="test"}, "mypy"]` → flattens test's members + mypy under group `typing`; a cycle (`a`→`b`→`a`) terminates and is recorded as a collection error, not an infinite loop.
- No `[dependency-groups]` table → collector is a no-op (existing evidence unchanged).

Change 2:
- `requirements-dev.txt` with `pytest` → `kind="dev_group"`, `source="requirements-file.dev"`; `requirements.txt` with `flask` → `kind="dependency"`.
- `-e .[http2,socks]` line → `evidence.used_extras ⊇ {http2, socks}`; bare `-e .` → ignored (no self-dep).
- `-r base.txt` include → base.txt's lines are collected with base.txt's role; a self-referential `-r` cycle terminates.
- docs file (`docs/requirements.txt`) → `kind="dev_group"`, group `docs`.

Change 3 (`tests/depgraph/test_roots.py`):
- runtime dep → root; feature extra NOT in `in_scope_extras` → NOT a root; feature extra IN `in_scope_extras` (via `used_extras`) → root.
- `dev_group` group `test` → root; group `tests`/`lint`/`typing`/`dev` → root; group `docs`/`release` → NOT a root.
- an excluded-optional feature-extra whose module is imported is still NOT re-injected as a root (the declared-only invariant holds — imports never generate roots).
- httpx-shaped fixture: root `requirements.txt` with docs pins + `-e .[http2]` → `h2`-providing extra in scope, `mkdocs` NOT a root (docs denylist), and (regression vs today) docs tooling no longer appears as runtime.

## Validation (measure the lift)

Re-run the package-layer eval (`outputs/graph_fidelity/pkg_lock_ab/`, runner in job tmp `run_ours_pkg.py` + `compare_pkg.py`) after landing. Expected, per-repo attributable because we know the exact packages:
- **Recall ↑** — PEP 735 test groups (flask/slither/typer/anyio/sqlalchemy/mvt) + `.[test]` extras + `requirements-test.txt` now captured.
- **Precision ↑** — httpx docs/lint no longer read as runtime; extras recovered from `-e .[…]` (h2/socksio) close httpx's MISS.
- vizro stays excluded (monorepo scope mismatch, unrelated).
Report the new pooled recall/precision and the per-repo deltas; regenerate the eval artifacts.

## Risks & backward-compat

- **Behavior change: default scope becomes testability-inclusive.** Existing tests asserting runtime-only closures (and any construction test with fixed PACKAGE counts) will need updating to the new scope. This is intended. Enumerate and update them as part of the SDD run; do not weaken assertions to hide the change.
- **A/B eval (30/0/30/0)** measures imports-as-roots gap-fill divergence — orthogonal to dev-groups — but closure *sizes* change, so regenerate any size-sensitive baselines and confirm the verifier-vs-generator verdict is unaffected.
- **Resolve conflicts**: dev-groups are additive/low-conflict; extras remain gated → no reintroduction of the mutually-exclusive-extras collision. Confirm no repo in the eval regresses to an unsatisfiable resolve.
- **Immutability / one-clean-path invariants** unchanged: collectors are pure appends; `select_roots` stays pure and returns a new list.

## Open decision

**Ambiguous catch-all groups (`dev`, `all`).** The spec's default is include-unless-docs/release, which pulls a `dev` group's lint/type tooling (e.g. flask `dev` = ruff/tox). Alternative: a stricter allowlist (only test/tests/testing/unit/integration) that excludes `dev`, trading recall for precision. Recommendation: **default-include** (recall-first, gate-backstopped, matches the goal "cover MOST requirements to run the tests"); revisit if the eval shows the `dev`-group over-pull is material. Decide before implementing Change 3.

## Suggested task breakdown (SDD)

1. Data-model + `_requirement_group` extension (add `dev_group` kind handling + group parsing for `dependency-groups.*` / `requirements-file.*`).
2. Change 1 — PEP 735 `_collect_dependency_groups` with include-group resolution (pure, TDD).
3. Change 2 — requirements role-classify + `-e`/`-r` handling + `used_extras` + nested-glob (pure, TDD).
4. Change 3 — `_in_test_scope` fixed policy in `select_roots` + `in_scope_extras` wiring; update affected construction tests.
5. Validation — re-run the package-layer eval + A/B; regenerate artifacts; record the recall/precision lift.
