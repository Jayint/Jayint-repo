# Two-Lane Causal Collection Graph + Deterministic Import Classification

**Date:** 2026-07-16
**Status:** Proposed; ready for implementation planning
**Scope:** The *collection-scope* environment graph (everything needed to reach clean `pytest --collect-only`), Python profile first. Covers (a) the node/edge model, (b) how first-party vs external imports are classified, and (c) how each is certified and cured.
**Builds on:** `2026-07-16-collection-graph-simplification-design.md` (the five-node collapse + deterministic resolver + demotions). This doc refines that one: it adds the *causal* framing (two cure-typed lanes), makes the first-party module graph first-class, and specifies the import classifier concretely. It does not contradict it — `declares`-authoritative roots and the demotion table stand.
**Out of scope:** the execution plane (services, real test *pass*, `ConnectionError`), non-Python profiles, secrets/config values.

## Executive decision

Model collection-scope as a graph of **five node types** — `project · file · import · pkg · syslib` — carrying the **existing certification fields** (`state` / `check_command` / `strength` / `phase` / `attempts` / `certified_cycle`), with `tier` and `layer` dropped as the dead provider-tier encoding. An `import`'s `satisfied-by?` edge resolves to **one of two providers**, and *the provider's node type is the cure*:

- **`import → file`** — a first-party module, provided by the repo's own file. **Cured by CONFIG** (editable install · rootdir · pythonpath · layout).
- **`import → pkg`** — an external distribution. **Cured by INSTALL** (pip / uv).

These are two causal *lanes*, not two graphs: they meet at the **bridge** — a first-party module that imports an external package (`file → import → pkg`). `project`'s certify-by-import certificate sits exactly on that join. The decisive principle: **same certificate, different cure.** `myapp` (first-party) and `numpy` (external) are both checked by `python -c "import X"`; they differ only in the fix. That difference — not the check — is why a first-party module and a `pkg` are distinct providers.

## Motivation

### The measured problem

Jul-7 50-repo run: **build succeeded 34/50, collection only 14/50.** The dominant post-build failure is the project's *own* package not importing under a green build — `ModuleNotFoundError` totalled 1296, led by the project namespace itself (azure 453×, frappe 290×).

This is a **config** failure (rootdir / pythonpath / editable-install / flat-vs-src), not a missing PyPI package. The current graph's causal vocabulary is `goal → import → pkg`, so the only failure it can *express* is "missing external package." It is structurally blind to its own dominant failure mode — and, at construction, it **drops first-party modules entirely** (`scan.py::_local_module_names` → dropped at `scan.py:163`; `repo_modules.py` computes them but is diagnosis-only by its own docstring). Modeling `import → file` (the local-module lane) is therefore a genuine *causal* extension, not a relabel: it introduces an effect (a first-party module fails to import) whose cause the current graph cannot represent.

### Why the classifier is the crux

Routing an import to the right lane *is* the internal/external classification. It must be deterministic where possible and must know when it is unsure — because misrouting is dangerous in both directions (a first-party name misread as external installs a same-named PyPI package; a real external misread as internal is silently never installed).

### Design rationale from the EnvGraph baseline

The released EnvGraph code (`method/envgraph/`) classifies by **over-generate + heuristic-clean + LLM**: every import becomes a `PYTHON_PACKAGE` node (`graph/builder.py:149-189`), and "internal" is decided later by `reasoners/llm.py::_is_internal_module` (l.342-356) — an **OR-union** of three detectors dominated by an over-broad `rglob("*.py")` stem walk (l.270-296). Consequence: any repo `.py` stem shadows a same-named external package (`jupyterhub/traitlets.py` → `import traitlets` reads internal → dropped *before* the LLM sees it → silent false-negative). Its separate path-based `repo_builder.py` graph (`imports_internal` / `unresolved_module`) is the seed of the two-lane idea but is not sys.path-accurate, is separate from installs, and carries no config-vs-install cure distinction. **We take the opposite stance:** one deterministic sys.path-accurate classifier with an explicit ambiguous zone, resolved by a certificate rather than a heuristic or the LLM. See memory `envgraph-import-classification-approach`.

## The model

### Nodes and fields

Five node types: `project`, `file`, `import`, `pkg`, `syslib`. Every node keeps the full certification payload it has today (`schema.py` `Node`). **Drop `tier` and `layer`** (`TYPE_TO_TIER` and the 9-value `Layer` enum existed only for the 10-type provider model). Keep `phase` (artifact placement), `state`, `check_command`, `strength`, `discovered_by`, `attempts`, `certified_cycle`, and the uv-enrichment fields. The simplification is fewer *types* and fewer redundant *axes* — nodes stay rich.

### Edges — one relation, two lanes

Keep the single `requires` relation + `conflicts_with` (`schema.py` `EdgeType`); do **not** promote `declares` / `contains` / `imports` / `satisfied-by` to named edge types (that re-bloats the edge axis while de-bloating nodes). Semantics ride on the existing `origin` field. Two attributes on edges:

- **`scope ∈ {runtime, test}`** — on the edge, not the node (a `pkg` can be both: `httpx` was product *and* test import in pal-mcp). A `pkg` is runtime/test by which manifest group `declares` it; an `import` is product/test by which `file` carries it. Store in `Edge.data`.
- The **lane / cure is implicit** in the `satisfied-by?` target's node type: `→ file` ⟹ config, `→ pkg` ⟹ install. No separate attribute needed.

The **bridge** requires no special structure: a `file` node is simultaneously a provider (for some first-party `import`) and a container of imports (that resolve to `pkg`s). That dual participation *is* the meeting point of the two lanes.

### Same certificate, different cure

| provider | certificate (`check_command` → `state`) | cure |
|---|---|---|
| `project` | `python -c "import <target modules>"` | both lanes green (the join) |
| `file` (first-party target) | file imports / collects | **config** — shared, project-level |
| `import` | `python -c "import X"` | (audit only — no cure of its own) |
| `pkg` | installed (`pip` / import) | **install** — per-package |
| `syslib` | dlopen / ldd probe | apt (dormant overlay) |

**Cure asymmetry (load-bearing).** The config lane's cure is a *single project-level bundle* — `pip install -e .` + correct rootdir + pythonpath — that makes ~all first-party modules importable at once. Per-`file` certificates tell you *whether* the config is right; the *fix* is the shared config, produced by the deterministic resolver (below). The install lane cures each `pkg` individually. This is why the resolver and the local-module lane are tightly coupled: **the resolver is the config lane's cure engine.**

## The import classifier

Runs **after** the deterministic resolver has picked the target interpreter (target-stdlib membership depends on it). For each scanned import, take the top-level segment (`name.split(".")[0]`) and descend a ladder where order is a safety property:

1. **Declared in a manifest → external (install lane).** You never declare your own modules, so a declared name is external by definition. This is the cheapest, strongest rung and it shrinks the ambiguous set to *undeclared* names only. (Uses the declared set already collected by `evidence.collect_python_dependency_evidence` / `roots.select_roots`.)
2. **In the target interpreter's stdlib → drop (not a node).** Classify against the **resolved target** `stdlib_module_names`, not the host's — fixes the `roots.py` `TODO(target-stdlib)` (`tomllib` 3.11+, `distutils` removed 3.12). EnvGraph and today's code both use host stdlib; this is target-honest.
3. **Top-level ∈ the repo's sys.path-accurate module set → first-party (config lane; `satisfied-by → file`).** The engine is `repo_modules.top_level_names()` — the **CPython/pytest basedir climb**, not `.py`-stem harvesting. The distinction is decisive: stem-harvesting says `jupyterhub/traitlets.py` defines `traitlets` (so `import traitlets` looks local — wrong, it's PyPI); the basedir climb yields `jupyterhub.traitlets` whose top-level is `jupyterhub`. This rung is what makes routing correct on src-layouts and nested packages.
4. **Otherwise → external candidate (install lane; `satisfied-by → pkg`).**

### The collision zone → certificate arbitration

The residue is `repo_modules.stem_collisions` — the difference between the broad stem set and the sys.path-accurate set: a bare name that is **both** a repo file reachable only via script-style `sys.path[0]` execution (typer's `import items` from `tutorial/items.py`) **and** a real PyPI distribution (`items`, netbox's `extras`, wagtail's `azure`). This is **not statically decidable** — it depends on how the file is executed, a runtime fact. Today it is flagged AMBIGUOUS; EnvGraph silently OR-drops it. We **resolve it with the certificate**:

1. Route **config-first**: apply the config bundle (editable install + rootdir), then check `python -c "import X"`.
2. Only if still MISSING **and** the name is a plausible distribution, **fall through to install**.
3. **Flag any collision-zone fallback install as a false-green risk.** Installing PyPI `azure` when the code meant the repo's own `azure` can pass tests against the wrong code (memory `self-install-false-green-vector`). Config-first ordering is the safety property; the certificate is the arbiter — never a static guess, never the LLM. Concretely the arbiter is already built: `relink.py` runs `importlib.metadata.packages_distributions()` in the container post-install (the certified reverse index of installed dist → provided imports), and `relink.flag_unresolved_imports` — an import with no `Package` edge after relink and non-optional — is the "still MISSING" signal. An editable-installed first-party package appears in that same map, so `import myapp` resolves through the identical certified mechanism once the config cure runs: one certificate covers both lanes.

The false-green flag must have an **owner**: a policy that treats config as the default cure and install as genuine last resort (or a human gate). A flag nothing consumes is just a log line.

### Install-lane resolution: unresolved import → distribution (no identity fallback)

This fires inside the **Phase-A resolve↔repair fixpoint**, not as a separate later pass. Each round resolves the current roots into their transitive closure, installs it, and computes coverage as the RECORD-union over that whole closure (`resolved_record_coverage`, `build.py:408`) — so an import satisfied *transitively* (`import jinja2` under a declared `flask`) is already covered and never reaches here. Only the genuinely-undeclared-and-absent residue is "missing." For each such import the install lane proposes *which distribution* to install, adds the grounded winner as a **new root**, and re-resolves (pulling in its subtree). `relink.flag_unresolved_imports` at Stage 4a is the *terminal* flag for whatever still has no provider after every repair round. Its safety rests on one invariant plus one mechanism.

**Invariant — no identity fallback.** An import name is *never* guessed to be its own distribution name. A candidate that grounds to nothing leaves the import honestly `unresolved`. This is the deleted `map_import_to_package` identity rung — and, notably, pipreqs' own `data.get(pkg, pkg)` default (take its *table*, not its fallback). Reinstating it is the wrong-install / self-install-false-green vector (memories `self-install-false-green-vector`, `phase2-identity-fallback-deletion`).

**Candidate generation — aggressive, every source untrusted:**
1. **Vendored `pipreqs` mapping table** — ~1157 `import:dist` rows (Apache-2.0; vendor in-repo with a NOTICE crediting bndr/pipreqs). Drops into the existing untrusted-candidate slot (`repair.curated_candidates`), replacing the 15-entry table. Never an authority: generic/stale rows (`App:Zope2`, `ANSI:pexpect`) must still ground, and the first-party classifier already stops a repo-defined name (`App`) from reaching this rung.
2. **LLM rung fed usage context** — the import name *plus the symbols the code uses on it* (`cv2.imread`, `cv2.VideoCapture` → OpenCV), harvested from the AST the scan already walks. Cached by `(import, symbols)`; only ever sees classifier-**external** imports, never a first-party name.

**Strict grounding — the safety net (`repair.choose_provider` + `record_grounds` over the composite RECORD provider).** Every candidate from either source is RECORD-grounded: `pip download --no-deps --only-binary=:all:` the candidate wheel (`wheel_inspect.py`), read its `top_level.txt`/RECORD → **confirm** (ships the import) / **deny** (does not → prune shims & hallucinations) / **blind** (no compatible wheel → install backstop). Verdict: exactly one canonical confirm → ACCEPT (install as an audit root, re-relink); more than one → AMBIGUOUS (never pick a variant); none → `unresolved`.

**Principle — aggressive generation, strict grounding.** Today's bottleneck is generation *coverage* (five mechanical variants + 15 rows), not grounding. Widen the *proposer* (pipreqs table + LLM-with-context) and let the wheel-RECORD check prune wrong guesses; never widen *acceptance*. The map keeps the common tail deterministic and reproducible; the LLM enters only on the residue, and nothing installs unless its real wheel provides the import.

## Kept / changed / net-new (code-grounded)

**Kept** — `roots.select_roots` declared-only roots (`roots.py:393`, imports never generate roots); the certification axis (`schema.py` `Node` fields); **`relink.py`'s certified `Import→Package` edges from the post-install `packages_distributions()` map** — the *sole* build-path `satisfied-by` source (the static `resolve_link.link_imports_to_packages` is retired from the build path); `import_mapping.map_import_to_package` (15-entry curated table + declared-name equality) kept only as a *pre-install* best-effort guess for evidence/repair/classification; the native overlay.

**Changed** —
- **Route, don't drop.** `scan.scan_to_nodes` stops discarding first-party imports (`scan.py:163`); instead it emits `file` nodes and routes each import's `satisfied-by?` to a `file` (internal) or `pkg` (external) via the ladder. This rewrites construction on every already-passing repo → **regression-sweep gated** (memory `regression-sweep-is-the-gate`).
- **Classifier consumes `declares` first and the target interpreter** (rungs 1–2), which it does not today.
- **Drop `tier`/`layer`** from `Node`; collapse `NodeType` 10 → 5 (per the prior spec's demotions).
- **Unresolved-import candidate source** swaps the 15-entry curated table for the vendored ~1157-row `pipreqs` table (Apache-2.0) in the existing `repair.curated_candidates` slot, and adds the usage-context LLM rung — both feeding the *same* `repair.choose_provider` grounding. The no-identity-fallback invariant is unchanged.

**Net-new** — `file` nodes carrying `scope`; the `import → file` (local-module / config) lane; per-`file` collection certificate; `project` certify-by-import certificate; the collision-zone certificate arbitration + false-green flag.

## Migration plan (additive-first, sweep-gated)

1. **Certify-by-import first** (independent, highest measured leverage): the "project installed" gate becomes `python -c "import <targets>"`, not `pip rc0`. Attacks the 34→14 collect cliff directly; lands before any graph change.
2. **Deterministic resolver** produces the config bundle (interpreter, editable install, rootdir, pythonpath). This is the config lane's cure engine; prototype against the 10 pilot repos first (prior spec, step 0).
3. **Add `file` nodes + `scope`** additively; keep the old TEST-hub wiring until the sweep is green.
4. **Flip route-not-drop** in `scan.py` and wire the classifier ladder; validate on the pass-repos before pruning the old drop path.
5. **Drop `tier`/`layer`, collapse node types**; each removal gated by the pass-repo sweep.
6. **Do not touch Services / the execution plane.**

## Non-goals

- Turning imports into install roots (reintroduces the 30/0 imports-as-generator regression).
- Any import-name-as-dist-name identity fallback (pipreqs' `data.get(pkg, pkg)` default included); an import whose candidates ground to nothing stays honestly `unresolved`.
- A heuristic-union or LLM-first classifier (the EnvGraph failure mode).
- Named edge relations for `declares`/`imports`/`satisfied-by` (re-bloats the edge axis).
- Front-loading import-time runtime config; the execution plane (services, real test pass).

## Open questions / to validate

- **Full-50** to harden the scope × provider × provenance taxonomy and the two-metric picture (friction-count vs expanded package-volume).
- **Config-bundle recovery rate**: how often does the resolver's editable-install + rootdir actually clear the collect cliff on the pilot repos (diff against the gold Dockerfile)?
- **Collision-zone frequency**: how large is the undeclared-and-name-colliding population in practice, and does config-first + flagged-install converge without manual intervention?
- **Ordering cost**: the classifier runs after interpreter resolution — confirm no pipeline stage needs the internal/external split earlier.
- **Non-Python generalization** of the two-lane / cure-typed model.

## References

- Memory: `envgraph-import-classification-approach`, `regression-sweep-is-the-gate`, `self-install-false-green-vector`, `package-layer-not-source-aware`, `front-load-complete-model-not-reactive`, `two-phase-declared-roots-construction-landed`.
- Code today: `src/python_deps/depgraph/schema.py` (Node/Edge, NodeType, `tier`/`layer`), `scan.py` (import scan; drops first-party at :163), `repo_modules.py` (sys.path-accurate `top_level_names`, `stem_collisions`), `roots.py` (declared-only roots; `TODO(target-stdlib)`), `relink.py` (certified `satisfied-by` via post-install `packages_distributions()`; `flag_unresolved_imports`), `resolve_link.py` (retired static linker), `import_mapping.py` (curated table + `declared_metadata_match` name-equality), `import_graph.py` (per-name findings with `source_files`), `repair.py` (candidate ladder + `choose_provider` grounding), `wheel_inspect.py` (`pip download` RECORD reader).
- External: `bndr/pipreqs` (Apache-2.0) — `pipreqs/pipreqs/mapping` (1157 `import:dist` rows) vendored as the install-lane candidate table; its `get_pkg_names` identity fallback is deliberately NOT adopted.
- EnvGraph baseline: `method/envgraph/{graph/builder.py, graph/repo_builder.py, reasoners/llm.py, extractors/python_files.py}`.
- Prior specs: `2026-07-16-collection-graph-simplification-design.md`, `2026-07-14-runtime-test-environment-construction-graph-design.md`, `2026-07-16-build-plan-certification-and-execution-evidence-graph-design.md`.
