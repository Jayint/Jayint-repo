# Config Lane — Local-Module Spine & Collision Arbitration (hardened design)

**Date:** 2026-07-17
**Status:** Proposed; hardened against an adversarial review; ready for implementation planning
**Scope:** The **config lane** of the two-lane collection-scope graph — first-party imports cured by editable-install + rootdir rather than pip. Covers the local-module node model, the module spine, the internal/external classifier, the config-cure execution, and the collision-zone arbitration. Python profile, collection-scope (goal = clean `pytest --collect-only`).
**Builds on / does not re-open:** `2026-07-16-two-lane-causal-graph-and-import-classification-design.md` (the two-lane model, `declares`-authoritative roots, no identity fallback, RECORD-grounding as the sole install gate) and `2026-07-17-two-lane-model-integration-refactor.md` (how the model slots into today's `build_dep_graph`). Those locked decisions stand; this spec makes the config lane concrete and **corrects three ways the earlier sketch would have shipped silently-wrong environments.**
**Preserved unchanged:** the install lane (`import → package`; the in-flight pipreqs→LLM→grounding pipeline), the system-package / native overlay, and the certify-by-import Project gate already landed (`build.py:194-217`).
**Out of scope:** the execution plane (services, real test *pass*), non-Python profiles.

## Executive decision

The internal provider is a **top-level local-`Module` node**, and the graph spine is `project → module → import → {module | package}`: the project *contains* its top-level first-party modules; each module's imports (attributed by `source_files`) resolve to another local module (internal, config-cured) or an external package (install-cured). The local module is the **join** between the two lanes — it is only importable once the config-cure (`pip install -e .` + rootdir) lands, and it is the *source* of the external imports the install lane must satisfy.

Two structural rules keep it honest, and one hazard governs the whole lane:

- **Classification is static; arbitration is certified.** A deterministic sys.path-accurate ladder routes clear-internal and clear-external names. The residue — a name that is **both** a repo module and a real PyPI distribution (`stem_collisions`) — is **not statically decidable** and is resolved only by importing it in the **cured container**, never by a guess and never by grounding.
- **Grounding cannot protect the collision case.** `pip download items` confirms PyPI `items` because that wheel genuinely provides `import items`. So the entire safety of the config lane rests on **config-first ordering + a cure-verified certificate**, not on the install lane's grounding.

## The model

### Nodes

Construction-relevant types: `project`, `module` (net-new — the Task-3 `NodeType.FILE` scaffold is **renamed `MODULE`**), `import`, `package`, over the preserved native overlay. A `Module` node:

- **id / identity:** the top-level import name (`app`). `import app.agent` rolls up to `module(app)` — the scanner already aggregates to top-level (`import_graph.py:113`).
- **evidence:** a tuple of `(sys_path_root, path)` pairs, not a single path — so two project dirs that each define `utils` do not collapse into one false single-provider node (they carry a cross-root collision flag). [resolves review §14]
- **check:** `python -c "import app"` run under the canonical collection plan (below) — **except** well-known non-importable stems (`setup`, `conftest`, `manage`, `noxfile`, `tasks`), which are certified by file-existence, never by import (importing `setup` executes `setup()`). [resolves review §13]

### The spine — one relation, module-centric

Keep the single `requires` relation; semantics ride `origin` (per the model spec — no new edge types). The spine:

- `project → module(app)` — the repo's top-level first-party modules (`origin="contains"`).
- `module(app) → import(numpy)` — what `app` imports, attributed by mapping each import's `source_files` to their top-level module (`origin="imports"`). One `import(numpy)` node, an edge from every module that imports it.
- `import(numpy) → package(numpy)` (external) **or** `import(app.db) → module(app)` (internal; top-level self-references collapse and are omitted).

**Declared-root reconciliation (no double-count).** Manifest-declared deps remain the *only* resolver roots (imports never generate roots — locked). The spine is **descriptive**; the manifest is **authoritative for install scoping**. A declared-and-imported dep carries both a spine `import → package` edge (via `relink`) and its declared membership; a declared-but-**un**imported dep (a plugin, a runtime-only dep) keeps a direct `project → package` edge. Scope (runtime/test) is read from the manifest group, never re-derived from the carrying module, so no consumer sees two scopes for one node. [resolves review §15] `_add_project_node` (`build.py:244-261`) is updated to consult post-classifier routing rather than drawing a direct edge for every runtime declared dep.

## The classifier — pure, static, sys.path-accurate

A pure Stage-2.5 pass (no container, no execution) over the scanned imports. For each top-level import name, descend a ladder where order is a safety property:

1. **Declared in a manifest → external** (install lane). You never declare your own modules.
2. **In the resolved target interpreter's stdlib → drop.** The stdlib set is the **target's** `sys.stdlib_module_names`, obtained by a one-shot container probe (or a static per-minor table) — never a host fallback, which would reintroduce the `roots.py:246` bug (host-3.13 calling `distutils` stdlib for a 3.8 target). [resolves review §17]
3. **In the repo's sys.path-accurate top-level set → internal** (config lane; `→ module`). The engine is `repo_modules.top_level_names()` — the CPython basedir climb — **extended for PEP 420 namespace roots.** The current climb (`repo_modules._module_for`) stops at `pkga` for `src/mycompany/pkga/__init__.py` with no `mycompany/__init__.py`, minting a false top-level and never surfacing `mycompany`. The extension: a downward namespace check (a dir with no `__init__.py` whose subdirs contain packages), constrained to manifest-declared package roots (`packages` / `package_dir` / `find_namespace_packages`, which `invocation_resolver` already parses). A name minted through a namespace-suspect climb routes to the **collision zone**, not clear-internal/external. [resolves review §6 — the hole that killed the prior module-node spec]
4. **Otherwise → external candidate** (install lane).

**The collision zone** is `repo_modules.stem_collisions` — the broad-stem set minus the sys.path-accurate set — plus namespace-suspect names from rung 3.

**Relocated scan drops.** `scan` stops dropping first-party names, but four drops it does today must land somewhere explicit, not vanish: non-external classifications and `local_names` hits are owned by the ladder; `_`-prefixed names are dropped by the classifier; **excluded-dir-only names** (`examples/docs/scripts/tools`, `scan.py:44-55`) route to the **collision zone, not clear-external** — because `SKIP_WALK_DIRS` hides locals under those dirs from *both* `top_level_names` and `stem_collisions`, so a `conftest sys.path.insert("examples")` + `import items` from `examples/items.py` would otherwise install the PyPI namesake with no flag. [resolves review §12]

**Output:** the classifier emits `Module` nodes and writes the **deferred/collision set into the graph as data** — it decides nothing that needs a container. `classify.py` is the sole sanctioned consumer of `repo_modules`/`stem_collisions`.

## The certificate — one canonical collection invocation

The earlier "same `sys.path` pytest uses" is not implementable by `python -c` (conftest `sys.path` mutations, per-basedir insertion under `importmode=prepend`, `importmode=importlib` inserting nothing, cwd). [resolves review §2] Instead:

Pin **one `TestEnvPlan`** — cwd, `PYTHONPATH`, `importmode`, rootdir — read from the repo's own config (`invocation_resolver.py` already parses `tox.ini`/`pytest.ini`/`setup.cfg`/`pyproject`; `config_scan.py:416-428`). **Both** gates derive from that single plan:

- **Overall cure gate:** `pytest --collect-only` under the plan.
- **Per-name arbitration:** `python -c "import X"` under the plan's `sys.path` + cwd.

**Exception-aware verdict** (load-bearing for the whole safety claim). A probe that raises is not automatically "not local":

- `ModuleNotFoundError` whose missing top-level == the probed name ⇒ **not local** (a genuine config verdict; eligible for fallthrough).
- **Any other exception** (a different `ModuleNotFoundError`, `ImportError` from a missing optional dep, `KeyError` for `DJANGO_SETTINGS_MODULE`) ⇒ **present-but-broken** ⇒ the module *is* local; **never** an install-fallthrough. [resolves review §7]

**Documented residuals** (attached to the flag, not assumed away): a collision name resolvable only via a conftest `sys.path` mutation, and `importmode=importlib` cases, remain possible false-green / false-red sources.

**Config bundle completeness.** The cure bundle folds in the unambiguous env-vars `scan_authoritative_config` discovers (e.g. `DJANGO_SETTINGS_MODULE`); a repo whose collection is unreachable without one gets that var or an explicit expected-failure marker, so the config lane is not blamed for a gap it does not own. [resolves review §16]

## The pipeline — sequencing (hardened)

```
Stage 1    scan               raw top-level Import nodes (drops relocated to classify)
Stage 1.5  target-env         resolved interpreter (+ target stdlib probe)
Stage 2    declared roots     manifest-declared only (unchanged)
Stage 2.5  classify (PURE)    partition → clear-internal (Module) | clear-external | collision(deferred, as data)
Phase A    fixpoint           resolve+install the clear-external closure.
                              `missing` is LANE-AWARE: excludes Module-routed and deferred-collision
                              imports, so first-party names never inflate the bound and never reach
                              the LLM dist-guesser.  Deferral is a first-class fixpoint input.
Stage Xa   config-cure        RUNS IN THE SCRATCH CONTAINER: editable install + rootdir, with a
                              build-isolation FALLBACK CHAIN (below).  Records which rung succeeded.
Stage Xb   arbitrate          ONLY IF the cure succeeded.  Per deferred collision name: the
                              exception-aware probe under the canonical plan.  resolves-local → Module;
                              genuine not-local → fallthrough candidate.  Cure FAILED → all deferred
                              collisions stay UNRESOLVED (honest RED), none install.
Stage Xc   fallthrough        resumed fixpoint over the fallthrough set with THREADED prev_pkg_ids /
                              attempted state (not a fresh call, not a bare `pip install`).
Phase B    native + relink    ldd / wheel-preflight / probe / relink / apt reconcile — re-run over
                              the fallthrough set so a native collision dist (lxml, psycopg2) gets its
                              DT_NEEDED probe, build-dep prior, certified edge, and apt name.
Stage 5    certify            canonical collection gate + per-node checks.
```

**The build-isolation fallback chain** (Stage Xa). PEP 517 isolation runs backend hooks in an env containing only `build-system.requires` — the numpy/cython Phase-A installed are invisible to a legacy `setup.py` that imports them at build time, so "deps now present" is false at the *build* step. The cure attempts, in order: (1) isolated `pip install -e .`; (2) `--no-build-isolation` with `setuptools`/`wheel` + any declared `build-system.requires` ensured present in the main env. The successful rung is recorded as cure evidence. Layout class (flat / src / namespace / dynamic-`__init__`) is read from `invocation_resolver`, not assumed uniform. [resolves review §3]

**The cure must actually execute.** Today nothing runs an editable install in the scratch container (`install_closure` covers Packages only), and `populate._poison_project_certificate` (`populate.py:118-153`) strips the Project `check_command` and forces `MISSING` at render time — which would erase the config lane's output. Reconciliation: the in-container cure produces a **scratch-certified** state on the Project/Module nodes; the render-time poison applies **only when no scratch-certified state exists** (or the two are split into distinct fields). [resolves review §9]

## Collision arbitration & the false-green policy

- **Gate on cure success, not "still MISSING."** A collision fallthrough installs **only if** the cure succeeded (editable rc 0 and/or the covering Module's cert green). If the cure *failed*, every deferred collision stays unresolved — never a batch of flagged wrong-installs on exactly the repos the config lane exists to fix. [resolves review §1 — the blocker]
- **Install + flag, config-first, when the cure did succeed.** A genuine not-local collision installs (avoids a false-RED on an undeclared external) but carries a false-green **flag**: "installed PyPI `items`, but a local `items.py` exists."
- **The flag has a named owner** (not a log line): it propagates to the run manifest / `case_study`; the eval reports flagged repos in a distinct **"certified-with-provisional"** bucket, never as clean passes; the react/repair arm treats it as a standing repair hypothesis. [resolves review §8]
- **Relink-vs-probe precedence.** Two certified-truth mechanisms now exist — `relink.packages_distributions()` and the canonical-plan probe. Precedence: the **probe owns lane routing**; **relink owns `Import→Package` linkage**; a relink edge that lands on a flagged collision import **propagates the provisional marker** rather than laundering it away, and relink skips drawing an edge for an import whose routed provider is a `Module` unless the arbitration fell through. [resolves review §11]

## Module boundary

- **`classify.py`** — pure classifier: the ladder, PEP 420 namespace handling, `Module`-node emission, and the deferred/collision set written as graph data. Sole sanctioned `repo_modules` importer. No Executor.
- **`arbitrate.py`** (net-new) — the container-bound phase: consumes the deferred set, runs the exception-aware probes under the canonical plan **after** the cure, mints fallthrough install-lane roots. A sibling of `relink`/`certify` (which already own certified container truth), not part of the classifier. [resolves review §10]
- **The cure runner** — the step that executes the editable-install fallback chain in the scratch container and stamps scratch-certified state (reconciled with `populate`'s poison).
- **Tripwire rewrite** (`tests/depgraph/test_construction_boundary.py`): structural guard → "only `classify.py` imports `repo_modules`; `scan`/`roots`/`build` stay clean." Behavioral guard → "a collision name is not install-accepted unless (a) the cure succeeded **and** (b) the canonical-plan probe shows it doesn't resolve locally" — tested with a stubbed certificate.

## Kept / changed / net-new

**Kept:** declared-only roots; the install lane; RECORD-grounding as the sole install gate; the native overlay; `relink` for `Import→Package` linkage; certify-by-import on the Project node.
**Changed:** `scan` stops dropping first-party (drops relocated to `classify`); `NodeType.FILE`→`MODULE`; the fixpoint `missing` filter becomes lane-aware and accepts a deferred set; `_add_project_node` consults routing; `populate` poison gated on scratch-cert; `relink` propagates the provisional marker.
**Net-new:** `classify.py`, `arbitrate.py`, the in-container cure runner + build-isolation chain, the `TestEnvPlan` (canonical collection invocation), the `Module` node + spine edges, the provisional-flag owner.

## Migration (additive-first, sweep-gated)

1. `NodeType.MODULE` rename + `TestEnvPlan` derivation from `invocation_resolver` (pure, testable, no behavior change).
2. `classify.py` pure classifier (ladder + namespace handling + relocated drops), behind a flag, validated on pass-repos; scan keeps its drop until green.
3. In-container cure runner + build-isolation chain + poison reconciliation.
4. `arbitrate.py` + lane-aware fixpoint + fallthrough re-entry + Phase-B re-run; the tripwire rewrite lands as a knowing gated step.
5. Flip route-not-drop; wire the spine; delete the old drop path. Regression-sweep gated at every step (this rewrites construction on every repo).

## Open questions / to validate

- **Cure-recovery rate:** how often does the editable-install (with the fallback chain) actually clear the collect cliff on the pilot repos (diff vs the gold Dockerfile)? Gates whether the lane pays off.
- **Collision frequency & the `importmode=importlib` residual:** how large is the undeclared-and-colliding population, and how often does the canonical-plan probe diverge from real collection?
- **Namespace-root coverage:** does the extended rung 3 correctly top the `google-cloud`/newer-`azure` PEP 420 class?
- **Provisional-bucket size:** what fraction of "passes" land in `certified-with-provisional`, and is that honestly reported end-to-end?

## References

- Prior specs: `2026-07-16-two-lane-causal-graph-and-import-classification-design.md`, `2026-07-17-two-lane-model-integration-refactor.md`, `2026-07-16-collection-graph-simplification-design.md`; the withdrawn `2026-07-13` module-node spec (its PEP 420 refutation is resolved here, review §6).
- Code: `build.py` (`_phase_a_fixpoint:346`, `_add_project_node:177`), `scan.py:44-55,152-153`, `repo_modules.py` (`top_level_names`/`stem_collisions`/`_module_for`), `relink.py:49-91`, `populate.py:57,118-153`, `config_scan.py:416-428`, `invocation_resolver.py`, `schema.py`, `tests/depgraph/test_construction_boundary.py`.
- Memory: `self-install-false-green-vector`, `regression-sweep-is-the-gate`, `honest-success-def-and-branch-split`, `config-env-provenance-bug`, `two-phase-declared-roots-construction-landed`.
- Adversarial review that hardened this spec: 3 blockers + 9 majors + 5 minors, all resolved inline (see the §-tags above).
