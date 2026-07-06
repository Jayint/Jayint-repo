# R3 — Debian-source mapping precision (collisions + over-include)

Status: research + design only. No source files modified. Scope: the sdist
build-dep prior in `src/python_deps/depgraph/debian_builddeps.py` +
`src/python_deps/depgraph/build_deps.py` + `src/python_deps/depgraph/os_resolver.py`.

A prior plan already exists for the name-collision half of this problem:
`docs/superpowers/plans/2026-07-06-debian-source-disambiguation.md` (STATUS: PLAN
ONLY, not implemented). This doc (1) independently re-verifies that plan against
**live Debian source data** (not just the repo's test fixtures), (2) finds a
**second, structurally distinct** over-include mechanism the existing plan does not
fix (Debian build-profile qualifiers being discarded), (3) finds a **third**
mechanism (bare service/daemon meta-packages leaking through as "build" deps) that
neither the existing plan nor the qualifier fix touches, and (4) proposes how the
three compose into one pipeline, which one subsumes an already-known follow-up in
the existing plan, and how the eval gates it.

---

## 1. Three independently-verified root causes

I re-fetched **live** Debian source control data (`sources.debian.org`, bookworm
where available) rather than trusting only the repo's synthetic test fixtures, per
the "verify, don't theorize" mandate. All three mechanisms below are confirmed
against real data, not inferred.

### 1.1 Mechanism A — name collision (Debian source ≠ the PyPI project)

`source_candidates()` (`debian_builddeps.py:154-177`) tries, in order: curated
alias → bare `normalize(pypi_name)` → `python-<normalized>` → stripped stem.
`_resolve_source()` (`debian_builddeps.py:221-245`) accepts the **first** candidate
whose `apt-cache showsrc` output passes `_is_python_source_stanza`
(`debian_builddeps.py:94-97`), which only checks: has `Build-Depends:` **and**
`_builds_python3_binary` (`debian_builddeps.py:81-91`) — i.e. *any* `python3-*`
token in the `Binary:` field.

**Live-verified**: `apt-cache showsrc click` on Debian resolves to the source
`click` at `https://gitlab.com/ubports/development/core/click/` — the **Ubuntu
Click packaging-format tool** (Vala/GLib), not Pallets' Click. Its real
Build-Depends includes `valac`, `libglib2.0-dev`, `libjson-glib-dev`,
`gobject-introspection`, `libgee-0.8-dev`. Its binary package list is `click,
click-dev, python3-click-package, libclick-0.4-0, libclick-dev, gir1.2-click-0.4,
click-doc` — `python3-click-package` starts with `python3-`, so
`_builds_python3_binary` returns `True` and the guard passes on the **wrong**
source. `bare "click"` is tried *before* `python-click` in `source_candidates`'
current order, so the wrong source wins the race.

This is a genuine **first-source-name-that-parses wins** bug: the guard checks "is
this *a* Python source" not "is this *THE* Python source for this dist."

The existing plan (`2026-07-06-debian-source-disambiguation.md` §2-§4) already
found this and designed a fix — see §2.1 below. I re-verified its central safety
claim against live data too: Debian names the Pillow binary `python3-pil` (not
`python3-pillow`), confirming that a naive "`python3-<dist>` string-equality"
fix would false-reject Pillow and drop `libjpeg-dev`/`zlib1g-dev`/etc. — a
regression *worse* than today's bloat. The plan's alias-widened accept-set is the
right shape; §2.1 evaluates it as-is.

### 1.2 Mechanism B — Debian build-profile qualifiers are parsed then discarded

`parse_build_depends()` (`debian_builddeps.py:112-134`) strips version/arch/profile
qualifiers with `_QUALIFIER_RE = re.compile(r"\(.*?\)|\[.*?\]|<.*?>")`
(`debian_builddeps.py:77`), applied at `debian_builddeps.py:130`
(`bare = _QUALIFIER_RE.sub("", first_alt).strip()`). The `<...>` group matches
**Debian build-profile annotations** (`<!nocheck>`, `<!nodoc>`, `<!stage1>`,
`<!cross>`, per Debian Policy §7.7 / the build-profiles spec) and throws the
annotation away along with the parens — so a token Debian itself has flagged as
"only needed to build **and test** the .deb, not to build the plain binary" is
silently promoted to an unconditional apt install directive.

**Live-verified**: fetched `sources.debian.org/src/pytest/7.2.1-2/debian/control/`
(the bookworm version — matches this repo's snapshot.debian.org bookworm+ target).
Its real, verbatim Build-Depends includes:
```
lsof <!nocheck>,
dh-sequence-sphinxdoc <!nodoc>,
python3-argcomplete <!nocheck>,
python3-hypothesis <!nocheck>,
python3-pygments <!nocheck>,
python3-requests <!nocheck>,
python3-twisted <!nocheck>,
python3-xmlschema <!nocheck>,
```
`pytest` is the **correctly-resolved** source (no collision — `Package: pytest` /
binary `python3-pytest`). `lsof` is not machinery (`is_machinery("lsof")` is
`False` — it matches neither `_MACHINERY` nor `_MACHINERY_PREFIX`,
`debian_builddeps.py:66-74`), so it survives `is_system_lib` and is emitted
verbatim as an `aptdep:lsof` install directive — reproducing the diagnostic's
`pytest→lsof` finding exactly. I reran `parse_build_depends` + `is_system_lib`
locally against this real stanza and confirmed `lsof` and `procps` both pass
through unfiltered.

This is **structurally different** from Mechanism A: `pytest` is not a wrong
source, and no source-identity fix (§2.1) touches it. It is also **structurally
different** from the existing plan's §5 follow-up ("machinery-filter leakage"),
which frames the fix as "widen the `_MACHINERY` denylist" (add
`openstack-pkg-tools`, `help2man`, `mkdocs`, `pandoc`, ... one name at a time).
That is whack-a-mole against an unbounded set of Debian doc/lint/test tool names.
Debian has *already tagged* the exact same set with `<!nocheck>`/`<!nodoc>` — the
signal exists in the data and is being thrown away by the regex, rather than
needing to be independently rediscovered and hand-enumerated.

I could not confirm the diagnostic's specific `httpx→black` claim against live
data: `sources.debian.org/src/httpx/0.23.3-1/debian/control/` (bookworm) shows
every Build-Depends token is `python3-*`-prefixed (all already dropped by the
existing `_MACHINERY_PREFIX` "python3-" rule, `debian_builddeps.py:71-74`) and
contains no `black` token at all. `black` most likely entered typer's apt list via
a *different* PyPI package in typer's 67-package unknown-mode closure (an
R2 dependency-group-breadth artifact) resolving to its own Debian source, not via
httpx. I flag this rather than force an explanation I can't verify — it doesn't
change the recommended fix, since Mechanism B (or a real-but-unconfirmed
Mechanism A hit) is still the right general lens.

### 1.3 Mechanism C — bare service/daemon meta-packages, unqualified in Debian itself

**Live-verified**: `sources.debian.org/src/psycopg2/2.9.5-1/debian/control/`
(bookworm — matches the diagnostic's `psycopg2` row) has real, **unqualified**
Build-Depends:
```
debhelper-compat (= 13), dh-python, libpq-dev, postgresql,
python3-all-dev:any, python3-setuptools, python3-sphinx
```
`postgresql` — the **full server metapackage** — is there with no `<!nocheck>`
tag at all (Debian's own build actually starts a live postgres instance during
`dh_auto_test`, but the maintainer didn't profile-annotate it). This means
**Mechanism B does not catch this one** — respecting `<!nocheck>` would still
leave `postgresql` in the kept set, because Debian itself didn't mark it.
`postgresql` is not `-dev`-suffixed, not `lib*`, and not in any machinery
pattern, so `is_system_lib("postgresql")` is `True` today and it becomes an
`aptdep:postgresql` node (`build_deps.py:346-354`), rendered before pip
(`Layer.TOOLCHAIN`) — exactly the diagnostic's "`postgresql` over-include."

This needs a **third, independent** defense: a *semantic* distinction between
"this token plausibly ships compiler-usable headers/libs/tools" (safe to treat as
a build directive) and "this token is a runtime service/daemon package" (never a
genuine *build*-time need for a client-library C-extension — compiling against
libpq/mysql/etc. needs headers+`.so`, never the running server binary).
`is_system_lib`'s current shape is a **denylist** (assume system-lib unless it
matches known Debian/Python packaging cruft); the fix in §2.3 flips it to an
**allowlist of plausible build-artifact shapes** for the non-machinery remainder.

**Key finding: Mechanisms A and C are orthogonal and neither subsumes the
other.** Click's *actually-collided* Build-Depends set includes real `-dev`-shaped
tokens (`libglib2.0-dev`, `libjson-glib-dev`, `libgirepository1.0-dev`,
`libgee-0.8-dev`) that an allowlist-shape filter (§2.3) would **not** remove —
they look exactly like legitimate build deps. Only fixing *which source* is
queried (§2.1) removes them. Conversely, `postgresql` for psycopg2 comes from the
**correct** source — no source-identity fix touches it; only the shape filter
(§2.3) removes it. Both layers are required for full precision; each is
independently testable.

---

## 2. Design: three layered, independently-verifiable fixes

All three sit in the existing `debian_build_deps()` pipeline
(`debian_builddeps.py:264-292`) and `build_dep_prior()` (`build_deps.py:167-223`);
none require a new module or a new node type.

### 2.1 Layer 1 — tiered source-identity gate (Mechanism A)

**What** (already fully designed in `2026-07-06-debian-source-disambiguation.md`
§3; I re-verified its safety claims live and endorse it as-is):

- Reorder `source_candidates()` (`debian_builddeps.py:154-177`) so
  `python-<normalized>` is tried **before** bare `normalized` (zero-risk per the
  plan's verified-non-existence check: no Set-B package has a competing wrong
  `python-*` source).
- Replace the single `_is_python_source_stanza` gate
  (`debian_builddeps.py:94-97`, used at `debian_builddeps.py:237` and `:243`)
  with a **candidate-name-keyed tier**:
  - candidate is the curated alias, or starts with `python-` → **LOOSE**
    (existing check: has Build-Depends + any `python3-*` binary). Low-collision
    namespace (Debian Python Modules Team convention).
  - bare/stripped-stem candidate → **WIDENED-STRICT**: accept only if some
    `Binary:` token `python3-<X>` has `normalize(X)` in
    `accept_set(dist) = {normalize(dist), strip_py_prefix(normalize(dist))} ∪
    {normalize(import_name) for import_name, pkg in CURATED_IMPORT_TO_PACKAGE.items()
    if normalize(pkg) == normalize(dist)}`.
  - Repology fallback stays LOOSE (already disambiguated by an external oracle).

**Why it generalizes**: `CURATED_IMPORT_TO_PACKAGE` (`import_mapping.py:7-23`)
already exists and is reused, not duplicated — it's the same table
`map_import_to_package` uses for import→dist resolution, inverted here for
dist→accepted-binary-stem. Any PyPI dist whose Debian source shares a bare name
with an unrelated project (not just click — the same class as Debian's Lisp
`cffi` / `cups` daemon, already guarded against via `_builds_python3_binary`
reading `Binary:` not `Build-Depends:`) is caught the same way: reject the bare
tier unless the produced Python binary's stem plausibly *is* this dist.

**Precision/recall tradeoff**: strictly precision-improving with a bounded,
already-enumerated regression class. The plan's own residual-risk note (§7):
a dist whose Debian binary uses an import-alias **not yet in**
`CURATED_IMPORT_TO_PACKAGE`, not `py`-strippable, with real native deps, and no
`python-<name>` source → bare-strict rejects → falls through to Repology. This is
a **recall** risk (silently degrades to "no Debian prior," not a wrong one),
mitigated by a one-line alias-table addition when discovered. No currently-tested
package (S1 corpus: psycopg2, mysqlclient, pyodbc, pyaudio, python-ldap,
pygraphviz, pycairo, pyzmq, shapely, pyproj) falls in this hole — none of them
has a `python3-*` binary stem divergent from its own name.

**Ecosystem-agnostic seam**: this is Debian-source-resolution-internal; it never
touches `os_resolver.py`'s `ObservedNeed`/`PROVIDER_TABLE` seam (module docstring,
`debian_builddeps.py:16-25`, is explicit that apt names from this module are
carried verbatim, never routed through `resolve()`). It is inherently
Python/Debian-specific (PyPI↔Debian source naming), same as today; a Node/Rust
`EcosystemProvider` would need its own analogous "does distro-source X really
correspond to package Y" gate (e.g. an npm↔Debian mapper would have the identical
collision shape — `bower`/`grunt`-style name squatting), but the *pattern*
("verify identity via the produced-artifact list, tiered by naming-convention
confidence, not just co-occurrence of a marker string") transfers directly.

### 2.2 Layer 2 — respect Debian's own build-profile qualifiers (Mechanism B)

**What**: change `parse_build_depends()` (`debian_builddeps.py:112-134`) to
capture the profile-qualifier group instead of discarding it, and drop any token
tagged `<!nocheck>` or `<!nodoc>` (the two profiles that mean "not needed to
build the shippable binary, only to build-and-test/build-and-document the
Debian package"). Concretely: split `_QUALIFIER_RE` into a version/arch group
(still stripped, as today) and a profile group `<...>` (captured); for each
entry, if the profile group's content (after stripping the `!`/whitespace)
intersects `{"nocheck", "nodoc"}`, drop the token from the returned list
entirely rather than keeping the bare name. Alternative-list entries (`a | b`)
already take only the first alternative (`debian_builddeps.py:129`) — no change
needed there, since Debian never puts a real compile-time `-dev` behind a
`<!nocheck>`-only alternative in the packages checked.

**Why it generalizes**: this is a **pure Debian-control-format fix**, orthogonal
to which source is chosen (Layer 1) and orthogonal to token shape (Layer 3). Any
PyPI package whose Debian packagers wrote a build-time integration/lint/doc test
(pytest's `lsof`/`python3-hypothesis`; by the same mechanism, any future
`black`/`ruff`/`ffmpeg`/`redis-server`-as-test-fixture token that Debian
correctly profile-tags) is caught by construction — no per-name enumeration, no
maintenance burden as new noise packages appear. It **directly subsumes** the
"whack-a-mole" framing of the existing plan's §5 follow-up for every case where
Debian *did* tag the token; §5's remaining hand-curated denylist additions
(`openstack-pkg-tools`, `help2man`, `mkdocs`, `pandoc`, `docbook-to-man`, `furo`,
`autopkgtest`, `pyflakes3`, `sphinx-common`, `doxygen`, `bash-completion`,
`pkg-kde-tools`, the `python3:any`/`python3-all:any` colon-qualifier forms) are
better handled by Layer 3 (§2.3), since most of them are exactly the
non-`-dev`-shaped tooling names an allowlist-shape filter drops for free — see
the overlap note at the end of §2.3.

**Precision/recall tradeoff**: precision-improving, effectively zero recall risk.
No real compile-time system library has ever been observed profile-tagged
`<!nocheck>`/`<!nodoc>` in the packages checked (libpq-dev, libffi-dev, libssl-dev,
etc. are all unconditional in their sources) — by definition, if the *binary*
package build didn't need it, Debian wouldn't ship a working `python3-X` without
it. This is the cheapest, lowest-risk fix of the three (a ~10-line change to one
regex + one filter predicate) and should land first regardless of the other two.

**Ecosystem-agnostic seam**: fully Debian-format-specific (build-profile syntax
is a Debian/`dpkg-dev` concept, not present in npm/cargo/go metadata) — this layer
does not generalize to other ecosystems' providers at all; it is local to
whatever ecosystem's `debian_builddeps`-equivalent module a future
`EcosystemProvider` uses Debian as the "sdist build-dep prior" data source for
(plausible for Rust/Node too, since Debian also packages many Rust crates and
Node modules with the same Build-Depends/profile format) — but the *code* is
Debian-parsing code, not provider-seam code, so it stays exactly where it is.

### 2.3 Layer 3 — allowlist plausible build-artifact shapes (Mechanism C)

**What**: replace `is_system_lib()`'s denylist logic (`debian_builddeps.py:105-109`,
currently `not is_machinery(token)`) with an allowlist for the **non-machinery**
remainder: keep a token iff, after `is_machinery` already filters (unchanged,
still catches `debhelper-compat`/`dh-python`/`python3-*`/`librust-*`/etc.), it
additionally matches one of:
- `*-dev` / `*-dev:any` suffix (headers/libs — `libpq-dev`, `libssl-dev`,
  `unixodbc-dev`, `postgresql-server-dev-all`),
- `lib*` prefix at all (covers non-`-dev`-suffixed runtime/link libs the build
  might genuinely need, e.g. multiarch `libfoo0`),
- a small curated **build-tool literal allowlist** for real non-`-dev`, non-`lib*`
  compile-time tools that are not machinery: `cargo`, `rustc`, `swig`, `meson`,
  `ninja`, `cmake`, `bison`, `flex`, `nasm`, `yasm`, `gperf`, `pkgconf`,
  `pkg-config`, `proj-bin` (already exercised by
  `test_is_system_lib_keeps_non_machinery`, `test_debian_builddeps.py:104-123`) —
  this is additive to that existing test, not a behavior change for any case it
  covers today,
- a `*-config`-suffixed binary name pattern (`pg_config`, `mysql_config`,
  `curl-config` — build-time discovery shims, already independently modeled as
  capability needs in `PACKAGE_TO_BUILD_NEEDS`, `build_deps.py:53-68`, but the
  Debian source may list the `-config` package itself too).

Anything else — bare non-`-dev`, non-`lib*` package names not on the small tool
allowlist (`postgresql`, `lsof`, `procps`, `openstack-pkg-tools`, `help2man`,
`mkdocs`, `pandoc`, `docbook-to-man`, `furo`, `autopkgtest`, `pyflakes3`,
`sphinx-common`, `doxygen`, `bash-completion`, `pkg-kde-tools`, `black`, `valac`) —
is dropped.

**Why it generalizes**: this is the single highest-leverage change in the set.
It flips the question from "have we seen this exact noise-package name before?"
(unbounded, denylist, whack-a-mole — the existing plan's §5 framing) to "does
this token's *name shape* look like something a linker/compiler could use?"
(bounded, allowlist, shape-based). It catches **every** current over-include
example in one mechanism except the true cross-project collision (click's `-dev`
tokens, which are shape-plausible and need Layer 1 instead) — `postgresql`
(Mechanism C), `lsof`/`procps` (Mechanism B, redundantly-but-harmlessly caught
here too), and essentially all of the existing plan's §5 hand-enumerated leak
list, without enumerating any of them by name.

**Precision/recall tradeoff — the honest risk**: this is the layer most likely
to **under-predict**. A real compile-time build tool that is neither `-dev`/`lib*`
-shaped nor on the curated allowlist (some niche codegen tool, a new build
backend's helper binary) would now be silently dropped instead of passed
through. Mitigations: (i) keep the allowlist small but easy to extend (same
posture as `PACKAGE_TO_BUILD_NEEDS`/`FLAVOR_OVERRIDES` — curated, PoC-verified,
additive); (ii) this is a **regression-detectable** risk, not a silent-forever
one: the `package_installability` eval's `S1` stratum
(`src/eval/package_installability/corpus.py` — psycopg2, mysqlclient, pyodbc,
pyaudio, python-ldap, pygraphviz, pycairo, pyzmq, shapely, pyproj, all genuine
native `-dev`-driven packages) has an `answer_apt`/`required_apt` golden set per
`answer_keys.json`; `score.py`'s `fidelity.recall` (`score.py:98-109`) would
visibly drop if this layer over-trims any of them — that's the exact gate in
§3 below. (iii) capability-based needs (curated table + PEP 725 + flavor
overrides) are unaffected by this layer entirely — they're a structurally
separate seed path (`build_dep_prior`, `build_deps.py:180-196`) that doesn't run
through `is_system_lib` at all, so `pg_config`/`mysql_config`/`cairo`/etc. keep
working even if the Debian-Build-Depends layer under-fires on some edge case.

**Ecosystem-agnostic seam**: the *shape heuristic itself* (`-dev` suffix, `lib*`
prefix, `*-config` binaries) is Debian-naming-convention-specific and would not
transfer verbatim to another distro's package-naming scheme (Alpine/apk uses
`-dev` too, incidentally, but Arch doesn't split `-dev` packages at all) — so
this stays local to the Debian-sourced prior, same placement as today. The
*principle* — "distinguish compile-artifact-shaped tokens from
service/tool/doc-shaped tokens before trusting an external distro's dependency
list as an apt install directive" — is the transferable lesson for whatever
Rust/Node analog eventually reads a distro's crate/npm-packaging metadata.

---

## 3. How the eval verifies this (concrete, not aspirational)

Two existing evals already have the right shape to gate this without new
scaffolding:

1. **`src/eval/package_installability`** (`score.py:86-148`,
   `answer_keys.json`, `corpus.py`). Its `click` row already exists in
   `corpus.py` with `modes=_BOTH` (includes `forced_sdist` — confirmed by
   `grep`, the corpus PackageSpec list). Add/confirm an `answer_apt` golden row
   for `click` under `forced_sdist` = `[]` (or whatever click's true from-source
   apt need is — none observed) and re-run: today `predicted_apt` for that row
   would carry `valac`/`libgee-0.8-dev`/`libjson-glib-dev`/etc.; post-fix it must
   not. This directly moves `fidelity.precision` (`score.py:105-108`, currently
   computed over all labelled rows) and, since click's Vala/GLib set are real
   installable packages, would otherwise sit invisibly inside a **passing** row
   today (`harmful_overpred`, `score.py:111-117`, only flags extras on a
   **failing** row — this is itself a scope gap worth flagging: a bloated-but-
   installable row currently under-counts harm. Recommend also asserting
   `set(predicted_apt) - set(answer_apt) == set()` directly on the click row in
   a targeted regression test, not just relying on the aggregate precision
   metric to move).
   The existing `psycopg2` `S1` row (`answer_keys.json`, `required_apt =
   ["build-essential", "libpq-dev"]`) is the **regression sentinel** for Layer 3:
   its `superset` field already includes `libpq5`; assert `predicted_apt` for
   psycopg2 keeps `libpq-dev` (recall unchanged) and drops `postgresql` (not in
   `required_apt`/`superset` today — confirm it's absent post-fix, i.e.
   precision improves without recall moving).
2. **`src/eval/build_script_eval`** (`report.py:38-63`, specifically
   `control_overprediction` at `report.py:50-54,60`). `typer` is already in the
   `S_control` stratum (`corpus.py:29-31`) which asserts pure-Python repos
   predict **zero** apt. Today typer fails this (31-67 apt, `DIAGNOSTIC.md` row).
   Layer 1 removes click's ~6-11 GLib/Vala tokens from typer's apt set; Layer 2
   removes any `<!nocheck>`/`<!nodoc>`-tagged tokens from typer's whole
   67-package unknown-mode closure; Layer 3 removes remaining non-`-dev`/`lib*`
   noise (`openstack-pkg-tools`, doc-tool names, etc.). Note: typer will likely
   **not** reach zero purely from R3 fixes alone — R2's build-mode-oscillation
   bug (67 packages stuck at `build_from_source=None`) is the reason the Debian
   prior runs on all 67 in the first place; R3 makes each individual resolution
   precise, R2 is what stops it from running on packages that don't need it.
   Report both: apt-set-size-before vs after (diagnostic evidence R3 fixes are
   real) and confirm `control_overprediction` still flags typer until R2 also
   lands (honest, not overclaimed).

**Concrete before/after assertions to add** (as unit tests against captured
real Debian stanzas — the existing plan's Task 1 already proposes committing
fixture stanzas for click/python-click/pillow/pyyaml/lxml/psycopg2/pglast/
python-cffi/beautifulsoup4/attrs/python-dotenv; extend that fixture set with the
live pytest/psycopg2 stanzas captured in §1.2/§1.3 of this doc):
- `debian_build_deps("click", ex)` on the real click+python-click fixture pair →
  `python-click`'s clean set (no valac/libgee/libjson-glib/gobject-introspection).
- `debian_build_deps("pytest", ex)` on the real pytest 7.2.1-2 fixture →
  excludes `lsof`, `procps`, and every `<!nocheck>`/`<!nodoc>` token; keeps
  nothing extra (pytest's kept machinery-filtered set is empty in reality, since
  all its non-profile-gated tokens are `python3-*`-prefixed machinery).
- `debian_build_deps("psycopg2", ex)` on the real psycopg2 2.9.5-1 fixture →
  keeps `libpq-dev`, drops bare `postgresql` (Layer 3), independent of any
  profile tag (there is none on this token).
- `is_system_lib` unit table: `postgresql` → `False`, `lsof` → `False`,
  `openstack-pkg-tools` → `False` (all now caught), while every existing
  `test_is_system_lib_keeps_non_machinery` assertion (`test_debian_builddeps.py:
  104-123`) still holds `True` (regression pin for the S1 corpus's real tools).

---

## 4. Recommended sequencing

1. **Layer 2 first** (§2.2) — cheapest (one regex split + one predicate), zero
   measured regression risk, immediately fixes `pytest→lsof` and any future
   Debian-tagged noise without hand-curation.
2. **Layer 1 second** (§2.1) — the existing, already-reviewed plan; land it
   as spec'd (its own Task 3 Docker verification gate is the right acceptance
   bar: `package_installability` `installable_rate ≥ 0.9143` and
   `failure_phase.apt == 0`, plus no real S1 dep dropped).
3. **Layer 3 last** (§2.3) — highest leverage (subsumes most of the existing
   plan's §5 denylist-widening follow-up) but highest regression-scrutiny
   requirement; gate strictly on `package_installability`'s `fidelity.recall`
   holding across the full S1/S2 corpus before merging, per §3.

All three are independent, additive, and individually revertible — none changes
`os_resolver.py`'s `ObservedNeed`/`PROVIDER_TABLE` capability seam
(`build_deps.py` continues deduping capability-resolved apt names out of the raw
Debian list exactly as today, `build_deps.py:196-213`), so nothing about the
capability-node reconciliation path (`probe.py`) is affected by any of this.
