# Import → Distribution Resolution — Design Spec

**Date:** 2026-07-02
**Branch:** `john-planner-v3-core-autoresearch`
**Status:** Design (not yet implemented). Supersedes the "propose-then-certify" note in
`docs/superpowers/loops/graph-fidelity-LEDGER.md` (Finding B).

**Goal:** Resolve each scanned Python IMPORT name (`cv2`, `github`, `yaml`) to the PyPI
DISTRIBUTION that provides it (`opencv-python`, `PyGithub`, `PyYAML`) **reliably on first
encounter**, without an LLM, without a repair/execute loop, and with the hand-curated
mapping table shrunk to a documented irreducible minimum.

**Design values (from the v3-core ethos):** interpretable, ONE clean path, rule-over-LLM,
deterministic, minimal network, host certifies truth, no repair loop.

---

## 1. Problem

Python code imports *modules*; pip installs *distributions*. The name often differs
(`import cv2` ← `opencv-python`; `import github` ← `PyGithub`). v3-core's current ladder
(`naming.package_roots` → `import_mapping.map_import_to_package`) resolves this as:
declared-manifest match (by normalized-name equality) → 12-entry curated table
(`CURATED_IMPORT_TO_PACKAGE`) → **identity fallback** (`dist := import`, trust=low).

The identity fallback is an unconfirmed guess, and it is the root of **Finding B**:
`import github` misses the table, falls to identity, and the resolver locks the defunct
sdist-only `github==1.2.6` (a real-but-wrong PyPI package). It builds-fails, and because
the renderer emits any versioned package, `setup.sh` ships a broken install. The correct
distribution, `PyGithub`, is never resolved or installed.

A wrong pre-install guess is worse than no guess: it *poisons* the resolve (locks the wrong
root) and can be a security regression (`import Crypto` → the CVE-bearing `pycrypto` instead
of `pycryptodome`).

## 2. What the investigation established (evidentiary basis)

Recorded here so the design decisions are traceable; full detail in the ledger.

1. **No dynamic module→distribution reverse index exists.** PyPI XML-RPC `search`/
   `list_packages` are hard-removed (RuntimeError); the JSON API is forward-only (no
   top-level-module field — verified); the Simple index is names-only; libraries.io is
   auth-gated; deps.dev matches names not modules; PyPI web search is bot-walled.
2. **Arbitrary renames are unreachable by mechanical variants.** `cv2→opencv-python`,
   `bs4→beautifulsoup4`, `sklearn→scikit-learn`, `Crypto→pycryptodome` have zero lexical
   relationship to the import; no variant rule generates them.
3. **The problem splits in two: candidate *generation* vs candidate *certification*.**
   Certification ("does distribution D provide import X?" — read D's wheel contents) is
   solved and cheap. Candidate generation ("which names to even check for X?") is the wall.
4. **Closure-scoped resolution is unambiguous; PyPI-wide drifts.** Within a project's
   resolved dependency closure each top-level module has exactly one provider (uv already
   dedup'd conflicts). PyPI-wide, multiple dists provide the same module (`github` AND
   `PyGithub` both ship `top_level.txt = github` — verified) and there is no reverse index
   to enumerate them.
5. **SMT-LLM (Kowshik-18/SMT-LLM) is the field proof.** A serious implementation with a
   40-entry hardcoded table + a 666-entry learned cache + PyPI existence checks + mechanical
   variants + an Ollama LLM + a runtime execute-and-repair loop **still** keeps the table,
   **still** mis-resolves `github` on its pure path (patched only by a cached LLM answer),
   and **still** accumulates errors (`osgeo→geopandas` is wrong — GDAL provides `osgeo`)
   because it caches existence/LLM *guesses*. "Zero table" is not achievable; drift comes
   from caching unverified guesses.
6. **Finding B is largely NOT a mapping problem.** In vizro, `import github` lives in
   `tools/pycafe/pycafe_utils.py` — a repo-root dev-tooling script, not the installable
   package; `scan.py` excludes `examples/docs/build/...` but not `tools/`. And `PyGithub`
   *is* declared, in `vizro-core/hatch.toml`, a format `evidence.py` never parses. And the
   existing safety net (`relink._drop_superseded_ghosts`) is dead (see §8).

## 3. Design principles

- **Certify-or-flag, never guess-and-cache.** A mapping enters the cache only after it is
  proven against wheel contents. This is what makes a cache safe (contrast SMT-LLM's drift).
- **Demand → certified supply.** Do not "map an import name to a distribution name." Match
  each import (demand) to the already-resolved closure package that provides it (supply),
  proven by certification.
- **Closure-scoped, not PyPI-wide.** Certify against the project's resolved closure
  (bounded, conflict-free, unambiguous), never against all of PyPI.
- **Certify, never existence.** Confirm a distribution *provides the import* (wheel
  `top_level.txt` / root entries / `packages_distributions()`), never merely that a name
  exists (`HEAD 200`). Existence is what returns the wrong `github`/`osgeo`.
- **Rule-over-LLM.** No LLM anywhere in resolution.
- **Certify-then-build, no repair loop.** No ephemeral "install candidate and see if it
  imports" probing. Certify from metadata before anything is committed.
- **Deterministic + era-anchored.** Any network read honors `exclude_newer` (a candidate
  released after the resolve era cannot win). The cache stores `import → dist NAME`
  (version comes from the resolver), which is era-stable.

## 4. Architecture — the resolution ladder

For each scanned import `X`, in order. The reliability of an *initial* (cold, never-cached)
import comes from Tier 1 being **certainty**, not a guess.

**Tier 0 — Classify (skip non-distributions).** stdlib, relative/first-party imports, and
the repo's own local modules are not PyPI distributions. Filter them out before any
resolution. The signal already exists (`scan._local_module_names`, `roots._is_non_distribution`);
the gap is that it is not consistently applied at resolution time — wire it in.

**Tier 1 — Closure-provided? → certify, link, done.** The reliable cold path. A dependency
you import is a dependency you declared, so `X`'s provider is almost always already in the
declared+resolved closure. Certify each closure member against `X` (see §5). On a hit, add a
certified `Import → Package` edge and **do not fabricate a new root**. Certain and
unambiguous. Cache the certified result. This is `relink.certified_import_links` promoted to
the authoritative primary mechanism.

**Tier 2 — Undeclared import → name it, certified.** `X` is provided by nothing in the
closure ⇒ a genuinely undeclared dependency. Only now is a name needed:
- **2a. Cache / curated table hit** (certified learned cache, then the irreducible table of
  §6) → candidate name.
- **2b. Certified variant resolution** → generate mechanical variants
  (`py{X}`, `python-{X}`, `{X}-py`, `{X}-python`, `{X}2`…), fetch each candidate's wheel
  metadata (range read, §5), and **certify it provides `X`**.
- A **unique** certified candidate → add it as a *certified* root and re-resolve to fold it
  into the closure; cache it. **Two candidates certify** (e.g. the `github`/`PyGithub` tie) →
  do not guess; fall to Tier 3.

**Tier 3 — Honest fallback, never a poison root.** For arbitrary-rename-and-undeclared
imports (`cv2` with no `opencv-python` declared anywhere): the irreducible curated table
(§6) is the last-resort override; if `X` is not there either, **flag it
`undeclared-unresolved`** — a real signal the project under-declared — rather than fabricate
a root from an unconfirmed guess.

## 5. Certification primitives

Two ways to answer "does distribution D provide top-level module X?", both map-free and
LLM-free:

- **Post-install (Tier 1, free):** `importlib.metadata.packages_distributions()` run in the
  container after `install_closure`. Ground truth from installed `dist-info`. Already
  implemented (`relink.PACKAGES_DIST_CMD`, `parse_packages_distributions`).
- **Pre-install (Tier 2, new — `wheel_provides.py`):** for a candidate not yet installed,
  read its wheel's top-level modules from PyPI **without a full download** — HTTP Range-GET
  the wheel's zip central directory (as pip's `lazy_wheel` does) and read
  `*.dist-info/top_level.txt`.

Certification caveats the implementation must handle:
- `top_level.txt` is **optional and increasingly absent** (hatchling/flit/pdm wheels). Fall
  back to listing the wheel's root zip entries minus `*.dist-info`/`*.data`.
- **sdist-only** distributions have no wheel to inspect → cannot certify pre-install; skip
  (do not build).
- **Namespace packages** (`google.*`, `zope.*`, `backports.*`) share a top-level across
  dists → certification is inherently ambiguous; Tier 1 (closure) disambiguates, Tier 2
  treats a shared top-level as "ambiguous → Tier 3".

## 6. Cache and table

Two layers, both accepted:

- **Certified learned cache** (`import → dist name`, JSON on disk). Grows only with entries
  proven by §5 certification. Cannot drift (every row was verified). Stores the name, not
  the version. Optionally keyed to `exclude_newer` era if reproducibility across eras is
  required; the base mapping (`cv2→opencv-python`) is era-stable so a plain map is usually
  sufficient.
- **Irreducible curated table** — the small, documented set of arbitrary-rename collisions
  that are *both* unreachable by variants *and* commonly *undeclared*, kept only as the
  Tier-3 override: `cv2`, `PIL`, `bs4`, `sklearn`, `Crypto→pycryptodome`, `github→PyGithub`
  (~6 entries; each row carries a comment saying why it exists). Most repos never hit it,
  because if they truly depend on these they declare the distribution and Tier 1 certifies
  it table-free. This replaces today's 12-entry `CURATED_IMPORT_TO_PACKAGE` as the *primary*
  mechanism — the table is demoted to last resort, not deleted.

## 7. Pipeline changes (grounded in current code)

- **`scan.py`** — add `tools` (and other conventional dev-tooling dirs) to
  `_EXCLUDED_SEGMENTS`; apply `_local_module_names` at resolution time (Tier 0).
- **`roots.py`** — **stop fabricating a resolver root from an unmapped import's guessed
  name** (the scan-gap-fill at `roots.py:290-299`). Seed roots from declared deps only.
  Undeclared imports become certified roots later via Tier 2, not eager guesses.
- **`build.py`** — reorder so the declared closure is resolved/installed first, then
  `certified_import_links` (Tier 1) is authoritative, then a bounded residual pass adds
  certified Tier-2 roots and re-resolves. Current order:
  `select_roots(312) → resolve_closure(342) → link_imports_to_packages(358) →
  certified_import_links(390)`.
- **`relink.py`** — promote `certified_import_links` to the primary mechanism; **fix
  `_drop_superseded_ghosts`** (see §8).
- **`emit.py`** — make `_is_reciped` state-aware so a build-failing / uncertified ghost is
  not rendered into `setup.sh` (see §8).
- **`evidence.py`** — parse more dependency-declaring formats (`hatch.toml`, PDM, pixi
  dev-envs, optional-dependency groups) so the declared closure — Tier 1's candidate source
  — is as complete as possible. Every format parsed moves imports from the risky tiers into
  certain Tier 1.
- **New modules** — `wheel_provides.py` (range-read certification), and a small Tier-2
  resolver (variant generation + certification + certified-cache write).

## 8. Companion Finding-B fixes (independently valuable, ship first)

These fix vizro's B end-to-end with **no table and no resolver**, and harden the pipeline:

1. **Scan-scope** — exclude `tools/`; `import github` (a docs script) then never surfaces.
2. **Dead safety net** — `relink._drop_superseded_ghosts` only drops a ghost when
   `state is State.MISSING`, but it runs *before* `certify_all`, so a "resolved-fine but
   build-fails" ghost is still `UNKNOWN` and never dropped; and `emit._is_reciped` renders
   any `bool(node.version)` package regardless of state. Fix the state-gate/ordering and make
   the renderer state-aware, so a fabricated / build-failing ghost can never leak into
   `setup.sh`. This is the backstop for whatever Tier 3 flags.

## 9. Non-goals

- **No LLM** in resolution (unlike SMT-LLM).
- **No repair/execute loop** — no ephemeral "install and see if it imports" probing.
- **No PyPI-wide search** — closure-scoped only.
- **No "zero table" promise** — SMT-LLM proves it is unachievable; the goal is a *minimal,
  drift-proof* table (§6), not its elimination.

## 10. The irreducible residual (honest)

An import that is **arbitrary-renamed AND declared nowhere AND not in the curated table** has
no candidate source: Tier 1 has nothing to scope to, Tier 2's variants cannot generate the
name, and there is no reverse index. It is flagged `undeclared-unresolved` — usually a real
signal the project under-declared a dependency. This residual cannot be closed dynamically;
the ~6-entry table exists precisely for its most common members.

## 11. Verification

- **Unit tests** per new module; mutation-teeth on the certifier (a wheel that does *not*
  provide the import must fail certification).
- **Edge-case fixtures** (mirroring `scripts/eval/graph_fidelity/edge_cases/`):
  - declared arbitrary rename (`cv2` + `opencv-python` declared) → Tier 1 certain;
  - undeclared close-name (`import foo` → `foo` on PyPI) → Tier 2 variant + certify;
  - undeclared arbitrary rename → Tier 3 flag (no poison root);
  - local/first-party import → Tier 0 skip;
  - the `github`/`PyGithub` tie → both certify → Tier 3 (table or flag), never auto-picked.
- **End-to-end on vizro** (Finding B) via `scripts/eval/graph_fidelity/coverage.py`:
  after the §8 fixes, `github` no longer surfaces and `setup.sh` does not ship a broken root.

## 12. Phasing / ship order

- **Phase 1 (in-lane, ships now):** §8 scan-scope + safety-net, and Tier-0 classification
  wiring. Fixes vizro's B — table-free, resolver-free. TDD + `coverage.py` e2e on vizro.
- **Phase 2 (architectural):** §7 roots-from-declared (delete root fabrication) + promote
  `relink` to authoritative Tier 1. This is what makes the table shrinkable.
- **Phase 3 (general mechanism):** `wheel_provides.py` certifier + Tier-2 certified variant
  resolver + certified cache + Tier-3 irreducible table/flag. Behind the edge-case corpus.
- **Phase 4:** broaden `evidence.py` manifest parsing (`hatch.toml` etc.) to maximize Tier-1
  coverage.

Each phase is independently valuable and independently testable; Phase 1 alone closes
Finding B.
