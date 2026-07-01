# Construction Enrichment — Clusters 1 + 2 Design

> Companion to `2026-07-01-static-construction-and-node-enrichment-design.md` (the full
> construction spec). This document is the **implementable subset**: the two enrichment
> clusters chosen to ship first. Synthesized from a two-architect design debate
> (deterministic-maximalist vs LLM-leaning) + a judge + a readability/interpretability critic.

## Purpose

Make the requirement graph **better-constructed from a repo** — richer, and grounded only in
authoritative sources — without touching the execution plane. Two clusters:

- **Cluster 1 — deterministic priors from authoritative sources.** Replace the curated
  package→syslib table with priors *derived* from (a) the resolver's own wheel-vs-sdist signal
  and (b) the repo's own apt declarations (Dockerfile / Aptfile / binder / CI).
- **Cluster 2 — widened LLM discovery.** Feed the construction-time LLM classifier raw *prose*
  files (README/Makefile/INSTALL/setup.cfg) and let it propose **soft** `SystemLib`/`Tool`
  hints, not just `Service`/`Config`/`DataAsset`.

## Guiding principle (hard constraint — the paper's thesis)

> The graph never asserts a native obligation it cannot ground in an authoritative source: the
> package's resolved metadata (wheel/sdist), the repo's own declarations, the distro index, or a
> real observed failure. **No benchmark-specific lookup tables. No curated package→syslib map.**

The current pipeline violates this in exactly one place: `seed_predicted_native` predicts
SystemLib/Tool nodes from a curated `tables.PACKAGE_TO_SYSTEM_DEPS` map. This design deletes that
map. Every native node created before the install probe will trace to either a uv.lock artifact
record or a specific `file:line` in the repo.

## Scope

**IN:** cluster 1 (wheel-oracle prior + declaration mining) and cluster 2 (raw-prose LLM intake +
SystemLib/Tool widening).

**OUT (do not design or implement here):**
- Graph-schema finalization (demote `chosen_fix`, fold `fix_candidates`, patch-contract, invariants).
- The live/Docker execution-path command migration and the `--no-deps` decision.
- Cluster-3 reactive native-dep grounding (the libGL soname→apt path at build time).
- `sdist` build-config mining (method 3) and `Requires-External` (method 4) — natural cluster-1
  extensions, deferred (they need sdist download/unpack or wheel-metadata fetch; heavier, separable).

## Architecture at a glance

`build_dep_graph` (build.py) stays a legible, ordered sequence of pure `graph → graph` stages.
One stage is renamed, one is new, and stage 3's wheel logic is extracted (behavior preserved); the
LLM classifier (the sole LLM bridge, outside `build_dep_graph`) gains two evidence kinds and a wider
type allowlist. Node `strength` (SOFT/HARD) is NOT set by these stages — it is set later by
`populate_setup_commands` for every *reciped* node (see *Strength epistemics*). The `[TAG]` below is
each stage's `discovered_by`.

```
1.  scan                  scan.py                → Import / Test nodes        [STATIC_SCAN]
2.  roots                 roots.py               → resolver roots (no nodes)
2b. runtime               build.py (inline)      → Runtime node              [STATIC_SCAN]
3.  resolve               resolve.py             → Package nodes + edges      [RESOLVER]
                           ↳ wheel_oracle.py computes build_from_source per package  (EXTRACTED)
3a. link_imports / project_hub
3b. seed_wheel_oracle_prior   seed.py            → build-essential Tool        [RESOLVER]   (REPLACES curated table)
3c. mine_declarations         declaration_mine.py → SystemLib/Tool from apt declarations  [STATIC_DECLARATION]   (NEW STAGE)
4.  install_closure       probe.py               [PROBE]   (reconcile guard widened: RESOLVER + STATIC_DECLARATION)
4.5 ldd_probe             ldd_probe.py           [PROBE]   (same guard widening)
4a–4c. certified_links / import_probe / reconcile_apt_names
5.  certify_all           certify.py             [CERTIFY — sole SATISFIED writer]

Post-pipeline (LLM, outside depgraph):
    classify              env_classifier.py      → Service/Config/DataAsset + soft SystemLib/Tool
       evidence = structured hits + declaration-context hits (decl_apt / conda_declaration) + raw prose snippets
```

---

## Cluster 1a — Wheel-oracle prior (replace the curated table)

**The signal already exists.** `resolve_lock.py` already computes, per package, whether it resolved
to a wheel or an `sdist` (no compatible wheel ⇒ will compile), and stamps `Node.build_from_source`.
That is the legitimate, *derived* form of "predict native needs": an sdist build needs a compiler.

**Two changes:**

1. **Extract `wheel_oracle.py`** (~100 lines) — move the wheel/sdist decision into a self-contained
   `risk_from_packages(...)` (plus the tag/platform helpers). Because `native_risk_from_lock` also
   calls `_select_applicable_packages` (which must stay in `resolve_lock.py` for `parse_uv_lock`), a
   wholesale move would create an import cycle; instead `wheel_oracle.py` is self-contained and
   `resolve_lock.py` keeps a thin, behavior-identical `native_risk_from_lock` wrapper (it filters
   local-source entries with its own `_is_local_source` before delegating, so the concept is not
   duplicated). This *names the concept* and modestly reduces the file (~469→~404). `resolve.py`'s
   import list is unchanged.
   **Behavior preserved exactly**, including the known latent bug that platform markers are
   evaluated against the HOST not the target (see *Adjacent known issues*) — fixing that is a
   separate correctness track, not this design.

2. **`seed_wheel_oracle_prior`** (seed.py, ~50 lines, renamed from `seed_predicted_native`) — for
   every Package with `build_from_source=True`, emit ONE `Tool` node `tool:build-essential`
   (`discovered_by=RESOLVER`, `chosen_fix=apt:build-essential`,
   `check_command="dpkg -s build-essential"`). It becomes HARD the same way every reciped apt node
   does — `populate_setup_commands` later sets `strength=HARD` because it carries an `apt:`
   `chosen_fix` (see *Strength epistemics*). Delete `PACKAGE_TO_SYSTEM_DEPS` and its now-dead helpers
   (`system_deps_for_package`, `_NORMALIZED_PACKAGE_SYSTEM_DEPS`) entirely.

**What this loses, honestly.** The deleted table also predicted *specific* `-dev` headers
(psycopg2→libpq-dev, Pillow→libjpeg-dev). The wheel oracle only knows "a compiler is needed," not
which headers. Those specific headers are now recovered by: (a) declaration mining (1b) when the
repo declares them; (b) `install_closure` parsing the real build error (stage 4); (c) `ldd_probe`
for runtime libs (stage 4.5). For a repo that declares nothing and whose sdist fails to compile,
the gap surfaces as a real, observed failure — which is the authoritative source the paper wants,
not a guessed table entry. **This is an expected coverage tradeoff; see Risks.**

`tables.py` keeps `NATIVE_LIB_TO_APT` / `TOOL_TO_APT` (these map an *already-observed* soname/tool
to its apt package — resolution of an observation, not prediction from nothing) and
`NATIVE_RISK_PACKAGES` (used only to scope which packages `import_probe` dlopen-checks — a probe
hint, never a graph assertion).

---

## Cluster 1b — Declaration mining (new deterministic stage 3c)

**Why deterministic, not LLM.** The repo's own apt declarations are an *authoritative source* and,
per the strength epistemics, "high-confidence / hard-ish." Routing them through the LLM is
epistemically lossy: `env_classifier._sanitize` unconditionally forces every LLM-proposed edge
`hard=False`, so an author's explicit `apt-get install libpq-dev` would be downgraded to the same
tier as a README guess — and the resulting node would be indistinguishable, in `discovered_by`,
from an LLM inference. A named deterministic stage keeps every declaration node attributable to a
`file:line`, which is the paper's entire point.

**`declaration_mine.py`** (~130 lines) — pure `mine_declarations(graph, repo_path) -> DepGraph`,
inserted at stage 3c (after `seed_wheel_oracle_prior`, before `install_closure`; no container
needed). Sources:

- `Dockerfile` (+ `**/Dockerfile*`): `RUN ... apt-get install` / `apt install` package lists.
- `Aptfile`, `binder/apt.txt`: one apt package per line.
- `.github/workflows/*.yml`: `run:` steps containing `apt-get install`.

**Parser contract (deliberately small, precise, correctness over completeness):**
- **Join backslash-continuation lines first.** Multi-line `RUN apt-get install -y \` … `\` … is the
  *dominant* real-world style; joining lines that end in `\` is ~3 lines and is kept IN scope (one
  reviewer's suggestion to drop it would miss most multi-line apt blocks).
- Split the joined command on `&&` / `;` into segments. `&&` sequencing
  (`apt-get update && apt-get install`) is the standard idiom, NOT a skip signal.
- **Skip the whole RUN command if it contains shell control flow** (`if `, `case `, `for `,
  `while `) — those gate their installs conditionally. Checked once on the joined command.
- For each remaining segment matching `apt(-get)? install`, take the tokens after `install` and
  keep a token as a package ONLY if it matches a bare apt name `^[a-z0-9][a-z0-9.+-]*$`. This one
  rule **drops every flag** (any `-…`/`--…`, so unlisted flags like `--fix-missing` can't be
  mis-minted) **and every variable/substitution** (`$PKGS`, `${PKGS}`, `$(…)` — so ARG-driven
  lists don't produce a garbage `$VAR` node). A segment whose tokens included a dropped `$`-token
  is skipped entirely (partial extraction of a templated list would be wrong).
- Strip `#` comments; for `Aptfile` / `binder/apt.txt`, each non-comment line is one package.
- `.github/workflows/*.yml`: parse with the YAML lib (as `service_scan.py` already does for these
  files), pull each `run:` block scalar, then apply the same command parsing above.
- **Conda `environment.yml` is EXCLUDED** — conda names ≠ apt names; routed to the LLM evidence
  bundle only (kind `conda_declaration`), where the model maps it with soft strength or declines.

**Node emission.** Each extracted apt package → one node:
- type `Tool` if the name is a toolchain, else `SystemLib`. A private `_is_toolchain_apt` helper
  matches a small fixed set (`build-essential`, `gcc`, `g++`, `clang`, `make`, `cmake`,
  `pkg-config`, `ninja-build`, `autoconf`, `automake`, `libtool`) plus the "not a `lib*` / `*-dev`
  library" shape. This *classifies an already-declared package by name*; it is NOT a package→syslib
  prediction map, so it honors the no-table principle. It lives **private inside `declaration_mine.py`**
  (its sole consumer); `ids.py` stays a file of pure `X_id(name)` constructors. The redesigned
  `seed.py` emits a fixed `build-essential` node and never classifies arbitrary names.
- `discovered_by=STATIC_DECLARATION`, `chosen_fix=apt:<pkg>`, `check_command="dpkg -s <pkg>"`,
  `provenance="<relpath>:<line>"`, `state=UNKNOWN`. Like any reciped apt node it becomes
  `strength=HARD` via `populate_setup_commands`; a `requires` edge to it (from the declaring
  Project/Package, when known) is `hard=True`.
- Idempotent + **reconciliation-safe**: if a node with that id already exists, merge provenance
  rather than duplicate. Because a probe stage can independently derive the same apt-name id, the
  `reconcile_predicted` guard in `probe.py`/`ldd_probe.py` (today `discovered_by is RESOLVER`) MUST
  widen to `{RESOLVER, STATIC_DECLARATION}` and preserve provenance — else an author-declared node
  is silently replaced by a fresh PROBE node (Risks #1).

---

## Cluster 2 — Widened LLM discovery (raw prose + SystemLib/Tool)

The LLM bridge (`env_classifier.classify`, outside `build_dep_graph`) keeps its current shape; two
additive changes.

1. **Raw prose intake.** Add a NEW module `raw_intake.py` with `collect_raw_file_snippets(repo_path)`
   — a bounded allowlist of *unstructured* files the deterministic scanners cannot parse: `README*`,
   `INSTALL*`, `Makefile`, `setup.cfg`, `docs/` install pages. Per-file cap (≈500 chars of the
   install-relevant region) and total cap (≈3000 chars). Each snippet becomes a `DeterministicHit`
   with a synthetic id (`raw.NN`) added to `bundle_ids`, so `_sanitize`'s grounding invariant
   (`evidence_ref ∈ bundle_ids`) holds unchanged. It is its OWN module, not bolted into
   `static_collect.py` — that file's stated role is a "thin adapter that reshapes scanner output,"
   not a blind repo scanner, so raw-file scanning is a distinct concern (symmetric with
   `declaration_mine.py`). The `decl_apt` / `conda_declaration` context hits — which genuinely
   reshape declaration-scan output — stay in `static_collect.py`. Declaration nodes already came
   from stage 3c; the LLM sees `decl_apt` only for cross-reference.

2. **SystemLib/Tool proposals.** Widen `env_classifier._SYSTEM_PROMPT` to allow
   `type ∈ {Service, Config, DataAsset, SystemLib, Tool}` and the `syslib:<name>` / `tool:<name>`
   id forms. `_KIND_PREFIX` (patch_gate) and `ids.py` already support these prefixes; `_sanitize`
   already validates `NodeType` membership and forces edges soft. Drop any prompt instruction that
   asks the LLM to pick `candidate` vs `hint` by evidence-id type — LLMs don't follow per-id
   promotion reliably; use the `_ALLOWED_PROMOTION` default.

3. **Emittability dependency (must be solved for cluster 2 to install anything).** Today `_sanitize`
   DROPS `add_providers`, so LLM-created nodes never get a `chosen_fix`. A node with no `chosen_fix`
   is not *reciped* → `populate_setup_commands` gives it no `setup_commands` → it is **inert (never
   installed)**. So a widened SystemLib/Tool proposal, as-is, would produce a node that neither
   blocks nor installs — useless. The fix is a small deterministic `normalize_emittability` step in
   `env_classifier`, after `_sanitize`: for each soft `syslib:<name>`/`tool:<name>` node lacking a
   `chosen_fix`, derive `chosen_fix=apt:<name>` from the id suffix (the same convention stage 3c
   uses). That makes it reciped → installed proactively — the construction spec's "SOFT gates
   blocking, not emission." **Then keep it SOFT via a named origin.** Once reciped,
   `populate_setup_commands` currently marks *every* reciped node HARD, which would wrongly harden an
   LLM node. Resolution: give classifier-admitted nodes a distinct `discovered_by` — add
   `DiscoveredBy.CLASSIFIER` (schema.py) and have `patch_gate.apply_proposal` stamp it (today those
   nodes reuse `DiscoveredBy.PROBE` with `provenance=None`, indistinguishable from a real probe
   discovery, which hurts attribution). Then `populate_setup_commands` keeps `strength=SOFT` for
   `discovered_by is CLASSIFIER` and defaults only the deterministic tiers to HARD;
   `normalize_emittability` targets the same clean predicate. One small named signal that both fixes
   the strength issue AND restores node attribution (a paper value) — better than an ad-hoc
   `provenance is None` heuristic. Touches `schema.py`, `patch_gate.py`, `populate.py`, all narrowly.
   (Reduced-scope fallback, rejected: keep widened proposals ADVISORY-only and not auto-installed —
   simpler but delivers no proactive install, so it is not chosen.)

---

## Strength epistemics (how SOFT/HARD is actually set)

Node `strength` is NOT written by the discovery stages. It defaults `SOFT` (schema.py) and is set by
`populate_setup_commands`, which today marks `strength=HARD` on every *reciped* node — one carrying a
pinned `version` (Package) or an `apt:` `chosen_fix` (SystemLib/Tool). Edge hardness is a separate
axis: `_sanitize` forces every LLM-proposed edge `hard=False`. So "who is hard" is an OUTCOME of two
existing mechanisms, not a per-stage attribute:

| Source | `discovered_by` | gets `chosen_fix`? | resulting `strength` | why |
|---|---|---|---|---|
| Package (manifest-declared) | RESOLVER | pinned `version` | HARD (reciped) | the deterministic hard spine (unchanged) |
| `build_from_source` → build-essential | RESOLVER | `apt:build-essential` | HARD (reciped) | closed chain: no wheel ⇒ compiler required |
| apt declaration (Dockerfile/Aptfile/CI) | **STATIC_DECLARATION** (new) | `apt:<pkg>` | HARD (reciped) | author-stated; should block if absent |
| LLM SystemLib/Tool (prose/conda) | **CLASSIFIER** (new) | only via `normalize_emittability` (Cluster 2 §3) | **SOFT** (kept, §3) | inference; soft edges via `_sanitize` |
| `ldd_probe` soname (existing) | PROBE | `apt:<pkg>` | HARD | DT_NEEDED ground truth |

The invariant **"the LLM only ever proposes SOFT"** holds structurally: `_sanitize` forces its edges
soft, and (Cluster 2 §3) its nodes are kept SOFT even after `normalize_emittability` makes them
installable. `certify.py` remains the sole writer of `SATISFIED`.

---

## Module layout (new / changed)

| Module | Responsibility | Change |
|---|---|---|
| `wheel_oracle.py` | self-contained wheel-vs-sdist + platform-tag decision (`risk_from_packages`) | **NEW** (extracted from resolve_lock.py; real reduction ~469→~404) |
| `declaration_mine.py` | `mine_declarations(graph, repo_path)` — stage 3c; apt declarations → STATIC_DECLARATION nodes; owns private `_is_toolchain_apt` | **NEW** (~130) |
| `raw_intake.py` | `collect_raw_file_snippets(repo_path)` — bounded raw prose files → `raw.NN` hits | **NEW** (Cluster 2 §1) |
| `seed.py` | `seed_wheel_oracle_prior` — build-essential from build_from_source | rename; delete table path + dead helpers (~110→~50) |
| `tables.py` | post-observation remaps + probe-scope hint only | **delete** `PACKAGE_TO_SYSTEM_DEPS`, `system_deps_for_package`, `_NORMALIZED_PACKAGE_SYSTEM_DEPS` |
| `resolve_lock.py` | uv.lock → Package nodes/edges | thin `native_risk_from_lock` wrapper: filter local-source entries (via its own `_is_local_source`), then call `wheel_oracle.risk_from_packages` (keeps `resolve.py:74` import list byte-identical, no dup of `_is_local_source` in the new module) |
| `probe.py` / `ldd_probe.py` | build-time + runtime native probes | widen `reconcile_predicted` guard `RESOLVER → {RESOLVER, STATIC_DECLARATION}`; preserve provenance/strength on merge |
| `static_collect.py` | evidence bundle assembly (reshapes scanner output) | add `decl_apt` / `conda_declaration` context hits (reshaping declaration-scan output — matches its role); raw-file scanning goes to `raw_intake.py`, not here |
| `env_classifier.py` | LLM classifier (sole LLM bridge) | widen `_SYSTEM_PROMPT`; add `normalize_emittability` post-sanitize; wire `raw_intake` hits into the bundle; drop per-id promotion |
| `patch_gate.py` | admit LLM proposals | stamp admitted nodes `discovered_by=DiscoveredBy.CLASSIFIER` (was PROBE) |
| `populate.py` | fills `setup_commands` + `strength` | keep `strength=SOFT` for `discovered_by is CLASSIFIER`; default the deterministic tiers to HARD (Cluster 2 §3) |
| `schema.py` | enums | add `DiscoveredBy.STATIC_DECLARATION` and `DiscoveredBy.CLASSIFIER` |
| `build.py` | pipeline orchestrator | rename stage 3b call; insert stage 3c `mine_declarations` |
| tests | — | replace `test_tables.py` / `test_seed.py`; update `test_build.py` e2e fixture + `test_ldd_probe_docker.py` import (table gone); add tests per new module + reconcile-guard + CLASSIFIER-stays-soft |

`build.py` stays a linear, named, self-documenting stage list. `ids.py` is untouched (stays pure id
constructors). No god-files introduced; the `resolve_lock.py` size smell is modestly *reduced*.

---

## Risks + mitigations

1. **STATIC_DECLARATION node clobbered by probe reconciliation (Critical — addressed above).** A
   probe stage can independently create a node with the same apt-name id; `reconcile_predicted`
   (`probe.py`/`ldd_probe.py`) currently reconciles in place only for `discovered_by is RESOLVER`,
   so a declaration node would be *replaced* by a fresh PROBE node, losing its author provenance and
   strength — on exactly the repos cluster 1b targets. *Mitigation (required):* widen that guard to
   `{RESOLVER, STATIC_DECLARATION}` and preserve provenance on merge (see Module layout).
2. **Coverage regression from deleting the table.** Repos that relied on table-predicted `-dev`
   headers (and declare nothing) now surface the need as a real build failure instead of a proactive
   node. *Mitigation:* the intended, paper-defensible behavior; `install_closure`/`ldd_probe` recover
   most at install time. **Measure the benchmark delta before/after** and report it as the honest
   no-overfitting tradeoff. (Flagged to the human: this can move the benchmark pass rate.)
3. **Declaration HARD node that can't install (stale/wrong-distro Aptfile).** *Mitigation:* correct
   behavior — `certify.py` flips it to MISSING, `reconcile_apt_names` (4c) normalizes name drift, the
   repair loop handles the rest. Document in the stage-3c docstring so it is not surprising.
4. **Conditional/templated Dockerfile installs producing false HARD nodes.** *Mitigation:* skip RUN
   commands with control flow (`if`/`case`/`for`/`while`); drop `$VAR`/`$(…)` and flag tokens; only
   unconditional, literal package names qualify.
5. **Conda names mis-mapped to apt.** *Mitigation:* conda is excluded from stage 3c, routed to the
   LLM (soft) only.

## Adjacent known issues (NOT fixed here — flagged)

The construction-correctness audit (2026-07-01) found `_wheel_matches_platform` evaluates platform
markers against the HOST, not the target image — so `build_from_source` (and therefore the new
wheel-oracle prior) can be wrong when host ≠ target arch. The `wheel_oracle.py` extraction
**preserves this behavior**; fixing it belongs to the separate correctness track, not this
enrichment. Noted so the prior's accuracy ceiling is understood.

## Invariants preserved

- `python_deps/depgraph` stays LLM-free; `env_classifier` is the only LLM bridge, structurally
  outside `build_dep_graph`. The two new deterministic stages are pure `graph → graph`, container-free.
- Host (`certify.py`) is the sole writer of `SATISFIED`. The LLM proposes SOFT only.
- No curated package→syslib table. Every pre-install native node traces to a uv.lock artifact or a
  repo `file:line`.
- `build.py` remains a legible ordered pipeline; new stages follow the existing pure-function pattern.

## Explicitly deferred (YAGNI for this iteration)

- `sdist` build-config mining (pyproject `[build-system].requires`, `setup.py` `ext_modules`) and
  `Requires-External` — recover specific headers authoritatively, but need sdist/metadata fetch.
- v2 compact extracted-signal bundle (this ships v1 raw-file intake).
- Multi-arch/target-correct platform-marker evaluation (the adjacent bug above).
- Promotion of declaration nodes by evidence weight beyond hard/soft.
