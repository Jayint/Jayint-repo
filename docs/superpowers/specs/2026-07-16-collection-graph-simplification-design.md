# Collection-Scope Graph Simplification: A Five-Node Capability Graph + Deterministic Resolver

**Date:** 2026-07-16
**Status:** Proposed; deterministic-resolver POC validated on 9/10 repos (2026-07-16); ready for implementation planning
**Scope:** The *collection-scope* environment graph — everything needed to reach clean `pytest --collect-only`. Python profile first; the node model is chosen to be language-agnostic.
**Out of scope (this doc):** the *execution* plane (running services, real test pass, the `ConnectionError` gap), non-Python profiles, and secrets/config values. These are explicitly relocated, not deleted — see "Demotions."

## Executive decision

Collapse the graph from ten node types to a **five-node capability graph** — `project · file · import · pkg · syslib` — and relocate every other current node type (Platform, Runtime, Tool, Service, Config, Test-as-a-type) to its correct home *outside* the graph. The clean graph is the **residue** of these relocations, not a rewrite: `roots.py`, the import scan, `import_mapping`, and the native overlay already implement the five-node structure. The work is a **prune-and-relocate**, executed additively and gated by the pass-repo regression sweep, never a big-bang.

Two invariants must survive the simplification:

1. **`declares` (manifest), not `import`, is the authoritative root edge.** Imports are a verifier/candidate signal only. Making `import → pkg` generate installs rebuilds a measured regression (imports-as-generator lost 30/0 to imports-as-verifier; the import→dist fallback was deleted because `cv2→opencv-python`, `yaml→PyYAML` produced wrong installs).
2. **Removing a node type = moving its capability to a proven new home.** Nothing is deleted until its replacement exists and passes the pass-repo sweep. In particular, **Services stay exactly as they are** for now — they are the rq redis `1→683` win and are out of scope for this collection-graph work.

## Motivation

### The measured problem

On the Jul-7 50-repo construction run: **build succeeded 34/50, but collection succeeded only 14/50**, and test pass≥0.8 only 15/50. The dominant post-build failure is the project's *own* package not importing under a green build — `ModuleNotFoundError` totalled 1296, led by the project namespace itself (azure 453×, frappe 290×). (`ConnectionError` 1917 is the separate service/execution gap, owned elsewhere.)

The graph currently models the world by **provider tier** (10 node types). Analysis showed real setups are organized instead by three axes — **scope** (runtime vs test), **provider** (language-package vs system vs config), and **provenance** (declared vs discovered). The tier model encodes provider well, collapses scope, and ignores provenance, which is why test-deps and config read as "bolted on" and why the model fractures (same concept as two nodes: `tool:` vs `binary:`, import vs dist).

### Evidence base: the 10-repo gold-Dockerfile pilot (2026-07-16)

Ten random CERTIFIED gold repos analyzed (final certified Dockerfile + distilled agent transcript + declaration surface at the pinned SHA); 108 obligations tagged by scope × provider × provenance. See memory `gold-dockerfile-obligation-pilot` and `gold-set-artifacts-and-sha-drift`; artifacts in the session scratchpad (`records/`, `pilot_bundles/`, `distiller.py`).

Findings that drive this design:

- **Friction, not volume, concentrates in test-scope / config / native.** Of 40 `discovered_by_failure` obligations, only **2** are runtime deps. Runtime deps arrive as *one declared bundle* (`-e .` / `-r requirements.txt`) — solved and front-loadable. (Counting caveat: obligation counts measure decisions, not package volume — declared deps bundle to 1, discovered deps count per-package; report both metrics at the full 50.)
- **Top generalizable blind spot: undeclared test-scope imports.** Test files import packages in *no manifest, not even the repo's CI* (websockets: `werkzeug`, only in `tox.ini`; PerfKit: tensorflow, bigquery; gitingest: `requests`). Only catchable via a test-tree import scan (as candidates) or a collection failure. **Gate on `testpaths`:** the POC showed pal-mcp's apparent "6 undeclared imports" (fastapi/flask/redis/sqlalchemy) lived in `simulator_tests/`, which `testpaths=tests` excludes — so they were gold *over-install*, not a blind spot. A test-import scan must respect collection scope before flagging a package as needed.
- **The native-prereq (syslib) layer is mostly self-inflicted.** 10 of 19 native-prereq obligations were `possibly_overkill`. feast's 14 apt packages are an artifact of running live `pip install -e .[ci]` instead of the repo's `uv pip sync` locked wheels. Policy: **install from the repo's lockfile / prebuilt wheels; most syslib obligations then never arise.** **POC-confirmed:** installing feast's `-e .[ci]` from wheels (uv) imported torch/faiss with *zero* apt packages and collected 116% of gold — the 14 apt packages were over-install.
- **Harness artifacts are ~17%** (`.dockerignore → git clone` in 5/10 repos; pytest imposed over the repo's real runner). These model the certification method, not the repo — must be excluded.
- **Path/config splits cleanly** into a deterministic half (rootdir, pythonpath, import-mode, editable-install, conftest — all declared or computable) and an execution-discovered half (env-gated import-time skips, name-shadowing, monorepo `.pth` reconciliation, app-init shims).

Caveats on the evidence: n=10, single agent policy (Claude Code), **collection-scope gold** (does not exercise execution/services), all-Python. Directional, not validated at scale.

### POC validation: the deterministic resolver, 9/10 repos (2026-07-16)

The minimal deterministic plan — right Python + clone@SHA + editable install + declared test deps + pytest, run `--collect-only` from the rootdir, **no syslibs, no harness tricks** — was executed against 9 of the 10 repos (websockets deferred). Collected-count vs gold:

- **Easy tier:** gitingest 160/160, DDNS 912/912 (editable install *failed* — the declared `pythonpath` still reached full collection), pal-mcp 886/886 (*without* gold's over-installed fastapi/redis), algo 119/129 (92%), Spoolman 0/223 (POC didn't parse the PEP 735 `[dependency-groups]` that declares `httpx`, plus flat-layout `-e .` fail).
- **Hard tier:** feast 3080/2644 (116%; subdir `sdk/python` detected, wheels install, **no native-lib failure**), Archipelago 4001/20943 (19%; gap = nested `WebHostLib/` / `worlds/*` requirements not discovered + `pkg_resources`), PerfKit 197/2563 (7.7%; interpreter residual — needs 3.12, declared nowhere), vizro 64/2500 (2.6%; monorepo path-reconciliation residual).

Conclusion: the deterministic core is **strong for single-package repos** (including the heaviest, feast, via wheels); the residual is **concentrated and named** (interpreter-selection policy, monorepo reconciliation, a small execution tail), **not** native syslibs. Two invariants were demonstrated directly: honoring the repo's declared config reaches collection even when install fails (DDNS), and the minimal plan is *more minimal than gold* while matching its count (pal-mcp). See the residual taxonomy under the resolver, below.

## Target formulation

### The five-node graph

```
project --declares--> pkg        AUTHORITATIVE root source (manifests / extras / PEP 735 groups / tox testenv)
project --contains--> file
file    --imports---> import     (file carries scope: product | test)
import  --satisfied-by?--> pkg   VERIFIER + CANDIDATE. Never generates a root.
pkg     --depends-on--> pkg      resolved transitive closure (version constraints, conflicts, markers)
pkg     --requires--> syslib     dormant native overlay — materialized only if the pkg builds from sdist
```

Nodes: `project`, `file`, `import`, `pkg`, `syslib`. This is the collapse of the current ten: Platform + Runtime merge into scalar context (below); Tool + SystemLib merge into `syslib`; Package splits by scope tag rather than type; Service, Config, and Test-as-a-type are demoted out of the graph.

Edge roles:
- `declares` decides *what to install*. It is the only root source.
- `import → satisfied-by → pkg` **audits** whether the declared set covers what the code actually imports, and **proposes candidates** when it does not. It never becomes a root directly. (This is already how `roots.py` behaves — preserve, do not regress.)
- `pkg → requires → syslib` is a sparse, dormant overlay. With a wheels-first install policy it is usually empty.

### What deserves a graph vs what does not

Test: *a thing deserves graph structure iff resolving one node depends on other nodes* — via transitive edges, provider alternatives, or conflicts. Otherwise it is a scalar or a computed record.

**In the graph:** the package-dependency closure (runtime ∪ test, scope-tagged) with `depends-on`/`conflicts_with`; the sparse `pkg → syslib` native overlay; the `import` candidate/audit overlay over the same package namespace.

**Not in the graph** (relocated — see Demotions): interpreter, pythonpath/rootdir/import-mode, import-time runtime config, services.

### The scope tag

Put `scope ∈ {runtime, test}` as an **attribute on edges**, not a node type:
- a `pkg` is runtime or test by which manifest group `declares` it;
- an `import` is product or test by which `file` it lives in.

Runtime and test are **one scope-tagged closure**, not two graphs, because nodes overlap (e.g. `httpx` was both a product import and a test import in pal-mcp). The tag is what distinguishes an undeclared *test-only* import from a real runtime dep — the pilot's #1 finding — at zero structural cost.

### Demotions — where the removed node types go

| Removed node type | New home | Nature |
|---|---|---|
| Interpreter / Platform / Runtime | scalar **context** the graph resolves *against* | a value (base image + version), not a node. The declared version can be wrong (PerfKit declared py3.9, needs py3.12) — a value correction, not a graph edge. |
| Config (pythonpath / rootdir / import-mode) | **deterministic resolver record** | pure function of the declaration surface; no edges. See below. |
| Config (import-time env / shims) | **repair-appended patch list** | discovered only by executing collection; a flat list, not a node. |
| Service | **separate execution plane** (unchanged for now) | out of scope for this doc; do not touch. Load-bearing for execution (rq redis). |
| Test-as-a-type | the set of test `file` nodes (collection scope) | the target is "collect these test files," not a node. |

## The deterministic resolver

A pure function `declaration-surface → { interpreter, install_plan, rootdir, pythonpath, import_mode }` that front-loads the entire deterministic path/config half. It replaces the Config node type and directly attacks the collect cliff (project-own-package not importable).

Mechanics (all deterministic, no LLM, no execution):
- **interpreter** — the **weakest deterministic field** (POC: a "max CI version" heuristic picked 3.14/3.13 and *missed* PerfKit's required 3.12). Policy: prefer the CI **default / most-common** version and `requires-python` (not the max), cross-check them, pin to a known-stable when they conflict, and flag `declared-but-stale` / `undiscoverable`. The `undiscoverable` case (PerfKit: 3.12 named nowhere) hands off to a try-newer-Python repair rather than pretending certainty.
- **install_plan** — prefer the repo's **lockfile / prebuilt wheels** (uv.lock, hashed requirements) over a live resolve, to avoid sdist builds that manufacture syslib obligations (**POC: feast via wheels needed zero apt libs**). Editable-install the project — **finding the project dir even in a subdir** (feast `sdk/python`, POC-validated) or per-package across a monorepo. Read the *full* declared closure: extras, **PEP 735 `[dependency-groups]`** (Spoolman's `httpx` lives here — the POC's miss), and **all named requirements files, including nested ones** (Archipelago `WebHostLib/`, `worlds/*` — the POC's gap). Detect flat-vs-src layout; the flat-layout auto-discovery hazard (Spoolman: no `[build-system]`/`packages` + sibling top-level dirs) is a static check.
- **rootdir** — replicate pytest's documented inifile-discovery (`pytest.ini` → `pyproject [tool.pytest.ini_options]` → `tox.ini` → `setup.cfg`); it may resolve to a subdir (feast `sdk/python`).
- **pythonpath / import-mode** — read from that config; run pytest from the rootdir so the repo's own config applies; **preserve `conftest.py`** so its `sys.path` setup runs. Do not recompute paths the repo already declares.

### Residual taxonomy — what the resolver does NOT handle

The POC separated failures into two classes; the boundary is "build more resolver" vs "hand to the repair loop":

- **Class A — deterministic, just unimplemented** (all *declared*; belong *in* the resolver): subdir/monorepo project detection (feast — validated), nested/named requirements-file discovery (Archipelago), PEP 735 dependency-group parsing (Spoolman), and a sane interpreter-selection policy. These are the step-1 build priorities.
- **Class B — genuinely execution-discovered** (repair loop / execution plane; stay *out* of the resolver): an interpreter no declaration names (PerfKit 3.12), `pkg_resources` / version conflicts (Archipelago `setuptools<81`), import-order name-shadowing (feast cassandra/snowflake/ray), monorepo `.pth` path-reconciliation (vizro), and build-generated gitignored modules (vizro `_imports_`).

## Certification change (independent, high value)

Certify the project by **import, not by pip exit code**: the observed-plane gate for "project installed" must be `python -c "import <target modules>"` succeeding (run from a clean cwd), not `pip` returning 0. This single change addresses the largest measured gap (34 build-green → 14 collect) and is worth more than any node-type refactor. It can land before the graph work.

## Migration plan (additive-first, pass-repo gated)

The clean graph is the residue of safe relocations. Each step is additive, then validated on the repos that **already pass**, then (only then) prunes the old node type.

0. ✅ **DONE (2026-07-16) — prototyped against 9/10 repos.** The minimal deterministic plan reached (near-)full collection for every single-package repo, including the heaviest (feast, via wheels). Residual is concentrated (interpreter policy, monorepo, small execution tail), not syslibs. See "POC validation" above.
1. **Build the deterministic resolver as a new additive module** (zero removal → zero regression risk). Validate against the pass-repos and the gold set. Highest leverage: front-loads the deterministic majority and the collect cliff. Build priorities, in order, come straight from the POC's Class-A residual: **(a) interpreter-selection policy** (CI-default not max; `undiscoverable` handoff), **(b) install-target discovery** (subdir/monorepo project dirs, nested requirements files, PEP 735 groups), **(c) wheels-first install**.
2. **Add the `scope` tag** as an attribute on existing declares/imports edges. Additive field.
3. **Demote Interpreter to scalar context.** Nearly already a scalar; low risk.
4. **Prune the now-empty node types** (Config/path, Interpreter). Each removal gated by the pass-repo sweep.
5. **Do not touch Services.** The execution plane is a separate, later, separately-gated project.

Do **not** touch the `declares`-vs-`import` edge: `roots.py` is already declares-only with imports as audit. This refactor is prune-and-relocate, not a rewrite of resolution.

## Non-goals / out of scope

- Turning imports into install roots (reintroduces the 30/0 regression).
- Reproducing harness artifacts (`.dockerignore → clone`, pytest-as-imposed-runner).
- The execution plane: services, real test pass, `ConnectionError`. Collection-gold cannot inform it; it needs its own data source and design.
- Front-loading import-time runtime config (undeclared; execution-discovered).

## Open questions / to validate

- **Full 50** to harden the obligation taxonomy / five-node set and produce the two-metric picture (friction-count *and* expanded package-volume). Requires the distiller fix (retain each failed-build output tail paired with the edit it triggered) and a `declared-but-stale` provenance value.
- **Execution plane** design (services/config for test *pass*, not just collect) — separate effort, informed by the `ConnectionError` runtime data, not the gold Dockerfiles.
- **POC completeness** — websockets (the 10th repo) was deferred, and the naive "max CI version" interpreter pick penalized vizro (3.14) and PerfKit (missed 3.12); re-measure both once the corrected interpreter policy exists.
- **Non-Python generalization** — the capability set is abstracted to be language-agnostic but validated only on Python; needs 3–5 non-Python gold setups before the claim is trusted.

## References

- Memory: `gold-dockerfile-obligation-pilot`, `gold-set-artifacts-and-sha-drift`, `package-layer-not-source-aware`, `regression-sweep-is-the-gate`, `front-load-complete-model-not-reactive`.
- Code today: `src/python_deps/depgraph/roots.py` (declares-only roots; imports audited post-install), `src/python_deps/import_mapping.py`, `src/python_deps/evidence.py`, native overlay (`os_resolver.py`, `wheel_preflight.py`).
- Pilot artifacts: session scratchpad `distiller.py`, `pilot_bundles/`, `records/` (10 JSON records).
- Related specs: `2026-07-14-runtime-test-environment-construction-graph-design.md` (intent plane / typed root obligations), `2026-07-16-build-plan-certification-and-execution-evidence-graph-design.md`.
