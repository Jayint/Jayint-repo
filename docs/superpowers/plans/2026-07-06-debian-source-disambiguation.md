# Debian source disambiguation — fix PyPI↔Debian name-collision over-prediction

> STATUS: PLAN ONLY (research + design verified). NOT implemented. Uncommitted.
> Author when the shared branch is clear (a concurrent build-script-eval SDD effort is running).
> This branch (`john-v3-multi-lang`) is now the canonical graph-construction core; port the
> improved `depgraph` module to v3-core AFTER this lands here.

**Goal:** stop `debian_builddeps.py` attributing an unrelated Debian source's `Build-Depends` to a
PyPI package that merely shares its name (PyPI `click` → Ubuntu's `click` package-manager → GLib/Vala
deps), WITHOUT dropping any real native build-dep (the fix must not turn *bloat* into *breakage*).

**Scope:** ONE file — `src/python_deps/depgraph/debian_builddeps.py` (+ `tests/depgraph/test_debian_builddeps.py`).
Issue #2 (machinery-filter leakage) is a SEPARATE, smaller follow-up — see §5.

---

## 1. Root cause (research-confirmed)

`source_candidates()` (debian_builddeps.py ~154) orders candidates `alias → bare normalized → python-<normalized> → stripped-stem`, and `_resolve_source()` (~221) accepts the FIRST whose `apt-cache showsrc` output passes `_is_python_source_stanza` — which only checks *"has `Build-Depends:` AND its `Binary:` field lists ANY `python3-*` token."*

For PyPI `click`, the **bare** candidate `click` (Ubuntu's Vala/GLib package-manager) is tried before `python-click`, and it ships `python3-click-package` (its own GLib bindings) → the weak guard passes → its Build-Depends (`gobject-introspection`, `valac`, `libgee-0.8-dev`, `libgirepository1.0-dev`, `libglib2.0-dev`, `libjson-glib-dev`) are attributed to Pallets' Click. The correct source `python-click` (→ `python3-click`) is never queried.

`click` is the ONLY true source-collision in the observed postgres-mcp leak. The others (`charset-normalizer`→furo, `uvicorn`→docbook-to-man, `pglast`/`python-dotenv`→help2man, `mkdocs`, `mitmproxy`) are NOT collisions — they are either (a) a correct source whose Build-Depends carry Debian doc/packaging tooling (the §5 machinery-filter gap), or (b) no bare source at all / no `python3-*` binary (already rejected). `pglast`→`libpg-query-dev` is actually CORRECT.

## 2. THE safety finding (why naive strict-equality is UNSAFE)

The obvious fix — "require the source's `python3-<X>` binary to string-equal the PyPI name" — **silently BREAKS Pillow.** Debian names Python binaries by **import name, not dist name**:

| PyPI dist | Debian source | Debian `python3-*` binary | real native deps | exact-equality? |
|---|---|---|---|---|
| Pillow | `pillow` (bare) | **`python3-pil`** | libjpeg-dev, zlib1g-dev, libfreetype6-dev, liblcms2-dev, libtiff-dev, libwebp-dev, libopenjp2-7-dev, tk-dev | `pil` ≠ `pillow` → **FALSE-REJECT** |
| PyYAML | `pyyaml` (bare) | `python3-yaml` | **libyaml-dev** | `yaml` ≠ `pyyaml` → FALSE-REJECT (py-strip saves it) |
| beautifulsoup4 | `beautifulsoup4` | `python3-bs4` | none (pure-Python) | `bs4` ≠ `beautifulsoup4` → FALSE-REJECT (harmless, no dep) |

Pillow resolves ONLY via the bare tier (`python-pillow` does not exist), has NO capability backstop, and its C-extension `pip install` genuinely NEEDS libjpeg/zlib. Naive strict-equality → dropped deps → **build breakage** (strictly worse than today's bloat). `Testsuite: autopkgtest-pkg-python` is useless as a discriminator (click, pyyaml, pillow, lxml, psycopg2, python-cffi, beautifulsoup4 ALL carry only plain `autopkgtest`).

Therefore the strict accept-set MUST be widened with the import-alias table.

## 3. Verified-safe design (tier-aware WIDENED-strict)

Replace the single loose gate in `_resolve_source` with a candidate-NAME-keyed gate (key on the candidate string, NOT its slot position):

1. **Reorder** `source_candidates` so `python-<normalized>` precedes bare `normalized`. Zero-risk: verified `python-lxml/psycopg2/pyyaml/beautifulsoup4/pglast` do NOT exist, so no Set-B package has a competing wrong `python-*` source; also makes `cffi` resolve first-try to `python-cffi` and never touch Lisp `cffi`.

2. **Tier the guard by candidate name:**
   - candidate is the curated `_SOURCE_ALIASES` value **or** `candidate.startswith("python-")` → **LOOSE** = existing `_is_python_source_stanza` (Build-Depends + any `python3-*` binary). The Debian Python Modules Team namespace is low-collision.
   - otherwise (bare / stripped-stem) → **WIDENED-STRICT**: accept iff the source produces a `python3-<X>` binary with `normalize(X) ∈ accept_set(dist)`:
     ```
     accept_set(dist) = { normalize(dist), _strip_py_prefix(normalize(dist)) }
                        ∪ { normalize(import_name)
                            for import_name, pkg in CURATED_IMPORT_TO_PACKAGE.items()
                            if normalize(pkg) == normalize(dist) }
     ```
     (for each `Binary:` token: strip leading `python3-`, PEP-503 `normalize` (`.`/`_`→`-`, lower), compare.)

3. **Repology fallback stays LOOSE** — it is an already-disambiguated name oracle; it is the online net for any future import-aliased package not yet in the curated table.

`CURATED_IMPORT_TO_PACKAGE` is the existing import→dist table in `import_mapping.py` (reuse it inverted; `pil→Pillow`, `yaml→PyYAML`, `bs4→beautifulsoup4`, `attr→attrs`, … already present there — confirm each needed alias exists during Task 1).

## 4. Simulated verdicts (all correct under the widened design)

REJECT: `click` (stem `click-package` ∉ accept-set → bare-strict reject; then `python-click`→`python3-click` loose accept → GLib/Vala set GONE).
ACCEPT via bare widened-strict: `psycopg2`/`lxml`/`pglast` (exact), `pyyaml` (py-strip → keeps libyaml-dev), `pillow` (alias `pil` → keeps libjpeg/zlib — the regression sentinel), `beautifulsoup4` (alias `bs4`).
ACCEPT via python- loose tier: `cffi` (→python3-cffi, keeps libffi-dev), `cryptography` (keeps libssl-dev/cargo), `attrs` (→python3-attr, matched by SOURCE name), `python-dotenv` (prefix-keyed loose — must NOT strict-reject on stem `dotenv`).

## 5. Separate follow-up (issue #2 — machinery-filter leakage; DO NOT fold into this fix)

Even for CORRECTLY-matched sources, Debian packaging/doc/test tooling leaks through `is_system_lib` (its denylist `_MACHINERY`/`_MACHINERY_PREFIX` is short). Observed leaks on correct sources: `pybuild-plugin-pyproject`, `openstack-pkg-tools`, `help2man`, `mkdocs`, `pandoc`, `docbook-to-man`, `furo`, `autopkgtest`, `pyflakes3`, `sphinx-common`, `doxygen`, `bash-completion`, `pkg-kde-tools`, plus the `python3:any`/`python3-all:any` colon-qualifier tokens that slip the `python3-` hyphen-prefix check. Fixing #1 removes the click-only tokens (valac, gobject-introspection, libgee-…); the rest need a widened machinery denylist. Separate task, additive/low-risk. Do it AFTER #1.

## 6. Tasks (when executing — SDD, opus implementer + opus reviewer)

- **Task 1 — port/confirm the accept-set helper + tests (pure, no Docker).** Add `_binary_names(showsrc_stdout) -> list[str]`, `_accept_set(dist) -> set[str]` (using `_strip_py_prefix` + inverted `CURATED_IMPORT_TO_PACKAGE`), and the candidate-name tier predicate. Reorder `source_candidates`. Unit tests against CAPTURED showsrc stanzas (commit fixture stanzas for click/python-click/pillow/pyyaml/lxml/psycopg2/pglast/python-cffi/beautifulsoup4/attrs/python-dotenv/mitmproxy — real bookworm data; no live apt in tests). Assert the §4 accept/reject matrix + the `_strip_py_prefix` unit (`pyyaml→yaml`, `pillow→pillow` proving py-strip alone does NOT save Pillow — the alias table does).
- **Task 2 — wire the tiered gate into `_resolve_source`; keep Repology fallback loose.** Full `tests/depgraph tests/pkg_layer tests/eval` green; confirm `test_debian_builddeps.py`'s existing Lisp-`cffi`/`cups` rejection tests still pass.
- **Task 3 — verification gate (Docker, foreground):** (a) re-run the 70-row `package_installability` eval → installable_rate ≥ 0.9143 AND failure_phase.apt == 0 AND **no real dep dropped** (Pillow/PyYAML/lxml/psycopg2 rows keep their native deps; click's forced_sdist row loses the GLib/Vala set). (b) re-run the whole-closure e2e on postgres-mcp → click's GLib set GONE, real deps (libffi-dev, libssl-dev, cargo, libpg-query-dev) KEPT, apt-tier count drops ~29→~11.

## 7. Residual risk (accept, don't block)

A package whose Debian binary is an import-alias NOT in `CURATED_IMPORT_TO_PACKAGE`, not `py`-strippable, WITH real native deps, and no `python-<name>` source → bare-strict rejects → recovered only via online Repology. Bounded; mitigation is a one-line alias-table addition. None of the tested packages fall in this hole.

## 8. Execution constraints

- commit-local, NEVER push; commit ONLY `debian_builddeps.py` + `test_debian_builddeps.py` (+ fixture file) by explicit path. NEVER `git add -A` — the shared branch has a concurrent build-script-eval session; do not touch its files or `.context/codex-session-id`.
- Verify-then-fix already satisfied at the DESIGN level (this doc); Task 3 is the empirical confirmation.
- After landing here, port the `depgraph` module (incl. this fix) to v3-core.
