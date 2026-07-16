# Handoff — Design a cleaner graph-construction architecture for the two-lane model

Paste the block below into a fresh session to start the design discussion. It is self-contained; it tells the new session what to read, what is already decided, and what to design.

---

I want to design a cleaner **graph-construction architecture** to fit a dependency-graph model we've already specified. **This is a design discussion — use the brainstorming skill and do NOT write any code until we've agreed the architecture.** Verify every claim below against the current code; treat this prompt as orientation, not ground truth.

## Read first (in order)

1. `docs/superpowers/specs/2026-07-16-two-lane-causal-graph-and-import-classification-design.md` — the model, classifier, import→dist resolution, native-overlay boundary, and the "Blockers to resolve before implementation" section. This is the **WHAT**.
2. `docs/superpowers/plans/2026-07-17-import-dist-pipeline.md` — the first implementation unit (install-lane import→dist), already in progress. Its **"Scope — where this plugs into the existing Phase-A loop"** table shows how one unit slots into today's build without touching the loop.
3. The construction code as it stands today:
   - `src/python_deps/depgraph/build.py` — the orchestrator. `build_dep_graph` (~line 1030) and `_phase_a_fixpoint` (~line 346, the resolve↔repair loop: `install_closure` :404, `resolved_record_coverage` :408, missing :409-415, `generate_candidates`→`choose_provider` :429-432, add-root :450).
   - `scan.py`, `roots.py`, `repo_modules.py` (sys.path-accurate `top_level_names`/`stem_collisions`), `resolve.py`, `relink.py` (`certified_import_links` via `packages_distributions()`), `repair.py`, `schema.py`.
   - Native (preserved): `os_resolver.py`, `wheel_preflight.py`/`wheel_oracle.py`, `ldd_probe.py`, `build_deps.py`, `seed.py`, `pep725.py`, `debian_builddeps.py`, `apt_verify.py`.
   - The tripwire: `tests/depgraph/test_construction_boundary.py` (read its docstring — "narrowing the construction-time drop killed two prior designs").

## The problem

The **model** is settled; the construction **pipeline** is the old, organically-grown `build.py` flow built around the retired 10-node model. I want to design how construction should be *architected* to produce the new two-lane model cleanly — a coherent pipeline, not the new model bolted onto the old flow. Map the current construction stages end-to-end first, then propose the restructure.

## Locked decisions — do NOT re-open

- **Node model:** `project · file · import · pkg` (demand + Python layer) over a **preserved native overlay** (`SystemLib` + `Tool`, unmerged). The `NodeType` enum stays a **superset**; construction just stops *emitting* the demoted types (Platform/Runtime/Config/Service/Test). Native overlay is a **flat fan-out off `pkg`** (`pkg→SystemLib`, `pkg→Tool`); those two are leaf sinks (never `requires` sources).
- **Two lanes, cure = provider node type:** `import→file` = first-party = **config-cured** (editable install · rootdir · pythonpath); `import→pkg` = external = **install-cured** (pip/uv). They meet at the **bridge** — a first-party `file` that imports an external `pkg`. `project`'s certify-by-import certificate sits on the join. Same certificate (`python -c "import X"`), different cure.
- **Keep `layer`** (install ordering: native/apt → pip → editable); **drop only `tier`**.
- **Classifier ladder:** declared → target-stdlib → `repo_modules` sys.path-accurate basedir set → external. The engine is the CPython basedir climb, NOT `.py`-stem harvesting.
- **Install-lane import→dist:** pipreqs map → LLM(used-symbols) → RECORD grounding; **no identity fallback**; grounding is the sole gate. (Being implemented now via the plan above.)
- **`relink`/`packages_distributions()`** is the certified `satisfied-by` source (post-install, sole build-path source). The static linker is retired.
- **The Phase-A fixpoint is the existing loop** — the import→dist unit replaces only its step-4 candidate generation.
- **Regression-sweep is the gate:** any construction change must keep already-passing repos green.

## The load-bearing open problem (the reason the core isn't plannable yet)

**The certificate-arbitration owner + the config-cure are one coupled design effort.** Route-not-drop stops dropping first-party imports at scan; the classifier routes them, but the **collision zone** (`stem_collisions` — a name that is both a repo file reachable only via script-style `sys.path[0]` execution AND a real PyPI dist, e.g. typer's `items`) is not statically decidable. Critically: **RECORD grounding does NOT protect this** — it confirms PyPI `items` because that wheel genuinely provides `import items`, so the install-lane would wrongly install it (false green). The only defense today is the broad drop, which route-not-drop removes.

Materials mostly exist already — editable install (`populate.py:57`), pytest-config/rootdir reading (`config_scan.py:467`), the certificate (`packages_distributions()` / `python -c import`). What's missing is the wiring + policy. Design decisions to make:
1. The collision-zone certificate check: exact command + environment (editable + rootdir + conftest + `sys.path[0]`) — probe vs real `pytest --collect-only`.
2. Sequencing: config-cure the project *before* the install lane can accept a collision name (editable install is currently the *last* step).
3. Module boundary: the tripwire forbids `scan`/`roots`/`build` from referencing `repo_modules`/`stem_collisions` — so the classifier + arbitration must live in a **new module** those call, or the structural guard gets rewritten. Decide.
4. Rewriting the tripwire's behavioral guard to the new invariant ("a collision name is not install-accepted unless the config-cured certificate shows it doesn't resolve locally"), tested with a stubbed certificate.
5. The false-green policy: hard gate / soft flag / config-default-cure.

## What I want out of this session

A construction-architecture design that answers: what are the clean pipeline stages for the new model; where the classifier and the config-cure/arbitration owner sit; how the config-lane (file/first-party) is constructed vs the install-lane (existing fixpoint); how certify-by-import, editable-install, classifier, and arbitration sequence around the fixpoint; how the two lanes and the bridge are built; and how the native overlay attaches. End with a design spec (`docs/superpowers/specs/…`) I can turn into plans.

Start by mapping the current `build_dep_graph` stages end-to-end, then propose the restructure. Brainstorm with me one decision at a time.
