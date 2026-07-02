# Import → Distribution Resolution — Design Spec

**Date:** 2026-07-02
**Branch:** `john-planner-v3-core-autoresearch`
**Status:** Design (not yet implemented). Adversarially debated and empirically
spot-checked 2026-07-02 (see §13); the fact-check overturned one earlier assumption
(that vizro's fix is to parse `hatch.toml` into the runtime closure — it is not).
Supersedes the "propose-then-certify" note in
`docs/superpowers/loops/graph-fidelity-LEDGER.md` (Finding B).

**Goal:** Resolve each scanned Python IMPORT name (`cv2`, `github`, `yaml`) to the PyPI
DISTRIBUTION that provides it (`opencv-python`, `PyGithub`, `PyYAML`) — with **certainty
when the provider is declared or in the resolved closure**, and an **honest flag (never a
wrong guess) otherwise** — without an LLM, without a repair/execute loop, and with the
hand-curated mapping table shrunk to a documented irreducible minimum.

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
6. **Finding B is largely NOT a mapping problem — it is a scope problem.** In vizro,
   `import github` lives in `tools/pycafe/pycafe_utils.py` — a repo-root CI/docs-tooling
   script that vizro's own root `pyproject.toml` declares is "NOT describing a package, but
   the DEV environment of this mono-repo." `scan.py` already excludes
   `examples/docs/build/scripts/...` but not `tools/`, so this non-package code leaks into
   the install graph. `PyGithub` *is* declared — but only in Hatch **environment** blocks
   (`vizro-core/hatch.toml` `[envs.all]`/`[envs.docs]`, `vizro-ai/hatch.toml` `[envs.docs]`;
   verified 2026-07-02) — i.e. as a docs/test-tooling dependency, never in
   `[project.dependencies]`. So the correct fix is to **exclude the out-of-scope code**
   (`tools/`), NOT to parse those env blocks and pull a docs-tooling dependency into the
   runtime `setup.sh` (that would be scope creep in the wrong direction — see §7). And the
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
  metadata (range read, §5), and **certify it provides `X`**. This reaches only
  *morphological* near-misses: empirically it recovers `github→PyGithub` and `yaml→PyYAML`
  but **not** `cv2`, `bs4`, `sklearn`, `Crypto`, `osgeo` (2 of 7 common renames — verified).
  Tier 2b is a small incremental win, not a general solver; arbitrary renames are owned by
  Tier 3.
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
  the wheel's zip central directory (as pip's `lazy_wheel` does). Derive the provided
  top-levels from the wheel's **root zip entries** (dirs / `.py` files at the archive root,
  minus `*.dist-info`/`*.data`) as the primary source; use `*.dist-info/top_level.txt` only
  as an accelerant when present. Empirically `top_level.txt` is **absent in the majority of
  modern wheels** (3 of 5 sampled: rich, pydantic, beautifulsoup4 — verified), while
  root-entry derivation succeeded on all 5 — so root entries are the primary path, not a
  fallback.

Certification caveats the implementation must handle:
- **Ambiguity is real, not just theoretical.** `packages_distributions()` maps a top-level to
  a *list* — verified multi-dist collisions in a live env: `opentelemetry`→6 dists,
  `google`→{googleapis-common-protos, protobuf}. Tie-break: (1) prefer the closure member
  that is a **declared/requested root**; (2) if still >1, **flag ambiguous → Tier 3**, never
  auto-pick. Never write an ambiguous result to the cache.
- **sdist-only** distributions have no wheel to inspect → cannot certify pre-install; skip
  (do not build).
- **Namespace packages** (`google.*`, `zope.*`, `backports.*`) share a top-level across
  dists → inherently ambiguous; Tier 1 (closure) disambiguates via the declared-root
  tie-break above, Tier 2 treats a shared top-level as "ambiguous → Tier 3".

## 6. Cache and table

Two layers, both accepted:

- **Certified learned cache** (`import → dist name`, JSON on disk). Grows only with entries
  proven by §5 certification. Stores the name, not the version (the resolver supplies the
  version), so a later-yanked dist surfaces as a resolve *failure*, never a silently-wrong
  install. Optionally keyed to `exclude_newer` era; the base mapping (`cv2→opencv-python`)
  is era-stable so a plain map is usually sufficient. **Honest bound:** certification proves
  "this wheel provides top-level module `X`," not "this is the *canonical/intended*
  provider" — a name-squatter shipping the same top-level would also certify. Mitigations,
  not eliminations: closure-scoping + declared-root preference (a squatter is not in the
  declared closure), era-anchoring (`exclude_newer` blocks newly-registered squatters), and
  flag-on-tie (§5). Only the closure-scoped Tier-1 result is fully trustworthy; a Tier-2
  cache row is "certified provision within era," which is why Tier 2 is bounded and Tier 3
  flags rather than guesses.
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
- **`evidence.py`** — parse more *runtime* dependency-declaring formats (PEP-621
  `[project.dependencies]`, `[project.optional-dependencies]`, PDM, poetry) so the declared
  closure — Tier 1's candidate source — is as complete as possible. **Caveat (verified on
  vizro):** Hatch **environment** blocks (`[envs.*].dependencies`, typically in a separate
  `hatch.toml`) are dev/test/docs *tooling* environments, not runtime deps — do NOT fold them
  wholesale into the install closure, or `setup.sh` ships docs-build tooling (this is the
  wrong fix for vizro's B; see §2.6/§8.1). If a test-env's deps are genuinely needed, that
  feeds the *testability* gate specifically, kept separate from the runtime install graph.
- **New modules** — `wheel_provides.py` (range-read certification), and a small Tier-2
  resolver (variant generation + certification + certified-cache write).

## 8. Companion Finding-B fixes (independently valuable, ship first)

These fix vizro's B end-to-end with **no table and no resolver**, and harden the pipeline:

1. **Scan-scope (correct exclusion, not symptom-hiding)** — add `tools/` to
   `_EXCLUDED_SEGMENTS`, alongside the already-excluded `scripts/`, `examples/`, `build/`,
   `benchmarks/`. `tools/pycafe/` is CI/docs tooling outside every installable package (vizro
   declares it so itself — §2.6), so `import github` correctly never surfaces. This is *scope
   correctness* — excluding non-package code from the install graph — not a silent drop of a
   real dependency: a genuine runtime import under a package dir is unaffected. (Residual
   risk: a repo that misuses `tools/` for importable runtime code; low, symmetric with the
   existing `scripts/` exclusion, and the eval loop would surface it.)
2. **Dead safety net — fix the renderer, not the drop-gate.**
   `relink._drop_superseded_ghosts` only drops a ghost when `state is State.MISSING`, but it
   runs *before* `certify_all`, so a "resolved-fine but build-fails" ghost is still `UNKNOWN`
   and never dropped; meanwhile `emit._is_reciped` renders any `bool(node.version)` PACKAGE
   regardless of state. **Safe fix:** make the renderer state-aware — a PACKAGE renders into
   `setup.sh` only when it is **certified (`State.SATISFIED`)**, not merely versioned. Do NOT
   instead widen the drop-gate to include `UNKNOWN`: that would drop not-yet-certified
   packages that would have succeeded (premature deletion). Renderer-gating aligns with "host
   certifies truth" — an uncertified or build-failing ghost simply never renders — and is the
   backstop for whatever Tier 3 flags.

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
  - import under `tools/` (non-package dir) → excluded from the graph, not flagged;
  - a top-level provided by 2 installed dists (`packages_distributions` ambiguity) →
    declared-root tie-break, else flag; never auto-picked;
  - the `github`/`PyGithub` tie → both certify → Tier 3 (table or flag), never auto-picked.
- **End-to-end on vizro** (Finding B) via `scripts/eval/graph_fidelity/coverage.py`:
  after the §8 fixes, `github` no longer surfaces and `setup.sh` does not ship a broken root.

## 12. Phasing / ship order

- **Phase 1 (in-lane, ships now):** §8 scan-scope (`tools/` exclusion) + renderer state-gate
  + Tier-0 classification wiring. Closes vizro's B — table-free, resolver-free — and correctly,
  since vizro's `github` is genuinely out-of-scope dev tooling. TDD + `coverage.py` e2e on
  vizro.
- **Phase 2 (architectural):** §7 roots-from-declared (delete root fabrication at
  `roots.py:290-299`) + promote `relink` to authoritative Tier 1. This is what makes the
  table shrinkable.
- **Phase 3 (general mechanism):** `wheel_provides.py` certifier (root-entries primary) +
  Tier-2 certified variant resolver + certified cache + ambiguity tie-break + Tier-3
  irreducible table/flag. Behind the edge-case corpus.
- **Phase 4:** broaden `evidence.py` **runtime** manifest parsing (PEP-621 optional-deps, PDM,
  poetry) to maximize Tier-1 coverage. Explicitly **not** pulled forward and explicitly
  **scoped to runtime deps**: the debate proposed pulling hatch-env parsing into Phase 1 to
  "resolve vizro honestly," but the fact-check (§2.6) showed vizro's `PyGithub` is a docs/test
  *environment* dep — parsing it would ship tooling into `setup.sh`, so Phase 1's scope
  exclusion is the correct fix and this phase must avoid Hatch env blocks.

Each phase is independently valuable and independently testable; Phase 1 alone closes
Finding B correctly (by scope, not by hiding a real dependency).

## 13. Debate & empirical validation (2026-07-02)

This design was adversarially reviewed by three independent Sonnet agents (proponent,
skeptic, empirical ground-truth) plus a targeted vizro-manifest fact-check. Outcome:

- **Held up:** certify-or-flag is not a new invention (it is `relink.py`'s existing Stage-4a
  pattern); the wheel range-read certification primitive works (0.3–2.9% of bytes fetched);
  Tier-1 closure certification is certain when the provider is in the closure.
- **Corrected by evidence:**
  - `packages_distributions()` ambiguity is real (`opentelemetry`→6, `google`→2) → added the
    §5 tie-break/flag policy.
  - `top_level.txt` is absent in the majority of modern wheels (3/5 sampled) → §5 now leads
    with root-entry derivation, `top_level.txt` demoted to accelerant.
  - Tier-2 variants reach only 2/7 common renames → §4 downgrades Tier 2 to "morphological
    near-misses," Tier 3 owns arbitrary renames.
  - The `_drop_superseded_ghosts` fix was underspecified and both obvious repairs are unsafe
    → §8.2 now fixes the *renderer* (require `SATISFIED`), not the drop-gate.
- **Overturned:** the debate's headline recommendation — "pull `hatch.toml` parsing into
  Phase 1 to resolve vizro's `github→PyGithub` honestly" — was **refuted** by the fact-check:
  vizro's `PyGithub` is declared only in Hatch *environment* (docs/test-tooling) blocks and
  the importing file is non-package dev tooling. Parsing it into the runtime closure would be
  scope creep; the correct fix is the §8.1 scope exclusion. This is why the assumption was
  tested before editing.
