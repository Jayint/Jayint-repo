# R1 — Root-project & source-build native requirements are not modeled

Status: research/design only. No source files were modified to produce this
document. All claims below marked "VERIFIED" were reproduced live against the
actual corpus repos (`outputs/build_script_eval/_smoke/{lxml,pygraphviz}`) and
a scratch `python:3.11-slim` (Debian trixie) container in this session; claims
marked "READ" are code citations without a live repro.

## 1. Root cause, precisely stated and verified

There are **three independent, compounding gaps**, not one. All three converge
on the same symptom (repo-under-test's own native build requirements never
appear in the graph), but a fix to only one of them is insufficient.

### 1.1 The Project node is categorically excluded from every build-dep-prior stage

`_add_project_node` (`src/python_deps/depgraph/build.py:174-230`) creates the
repo-under-test as `NodeType.PROJECT`, not `NodeType.PACKAGE`:

```python
data={"installable": manifest is not None}
```

— no `version`, no `build_from_source`. Every stage that predicts native build
requirements filters on `NodeType.PACKAGE` explicitly:

- `seed_wheel_oracle_prior` (`seed.py:74-76`): `n.type is NodeType.PACKAGE and n.version and n.build_from_source is not False` — **this is the generic build-essential FLOOR**. The Project node fails both the type check and the version check, so **even the safety-net floor never fires for the repo under test.**
- `seed_build_deps` (`build_deps.py:306-311`): `if pkg.type is not NodeType.PACKAGE or not pkg.version: continue` — the curated table (`PACKAGE_TO_BUILD_NEEDS`), PEP 725 (`pep725_external`), and Debian `Build-Depends` (`debian_build_deps`) never run on the Project node for the same reason.

**VERIFIED** — instrumented `build_graph_construction_only` for both repos and dumped every `NodeType.PROJECT` / `NodeType.PACKAGE` node:

```
=== outputs/build_script_eval/_smoke/pygraphviz ===
PROJECT: project:pygraphviz  data={'installable': True}
PACKAGE nodes: 49 total (twine, pytest, cryptography, rich, keyring, ...)
  — every single one is a dev/packaging/test dependency; NONE is "pygraphviz" itself.
  — all 49 have build_from_source=False (correctly: they're all pure wheels).

=== outputs/build_script_eval/_smoke/lxml ===
PROJECT: project:lxml  data={'installable': True}
PACKAGE nodes: beautifulsoup(bfs=True), webencodings, setuptools, html5lib, six, cython
  — all six are lxml's OWN test dependencies; NONE is "lxml" itself.
```

pygraphviz predicts `apt=[]` not because the detector under-fires, but because
**there is no node in the Package layer representing pygraphviz at all** —
nothing is even eligible to be asked "does this need a compiler?".

### 1.2 The resolver never sees the project itself as a distribution

Even if the type filter in 1.1 were relaxed, the Project node still has no
`build_from_source` value to relax *into*. That stamp is computed once, in
`native_risk_from_lock` (`resolve_lock.py`, called from `resolve.py:298-300`),
which classifies packages **parsed out of `uv.lock`**. `uv.lock` is produced
by `resolve_closure` (`resolve.py:217-333`) against a **synthetic throwaway
pyproject** (`_write_pyproject`, `resolve.py:150-182`):

```python
content = (
    "[project]\n"
    'name = "depgraph-resolve-root"\n'
    ...
    "dependencies = [\n"
    f"    {deps}\n"   # <- roots.select_roots()'s declared-dependency list
    "]\n"
)
```

`roots.select_roots` (`roots.py:289-360`) builds `deps` from
`evidence.declared_dependencies` — the project's *declared dependencies*, never
the project itself (there is no `path = "<repo>"` self-dependency entry). So
`uv lock` never resolves "pygraphviz" or "lxml" as a package; `native_risk_from_lock`
never has a data point to classify; there is structurally no `build_from_source`
value the Project node could inherit even with a type-filter relaxation.
(READ, consistent with the empirical PACKAGE-node dumps in 1.1: 0 of 49
pygraphviz packages, 0 of 6 lxml packages, is the root distribution.)

### 1.3 Debian-source mapping is sound (VERIFIED for lxml) but incomplete-by-construction (VERIFIED for pygraphviz)

This bounds how much (1.1)+(1.2) alone would help, and is the reason a purely
structural fix ("promote Project to Package, run it through `uv.lock`") isn't
sufficient either. Reproduced `debian_build_deps`'s exact commands on a live
`python:3.11-slim` (Debian trixie) container:

```
$ apt-cache showsrc lxml   (after ensure_deb_src)
Package: lxml
Binary: python3-lxml, python-lxml-doc
Build-Depends: debhelper (>= 10), dh-python, python3-all-dev, libxml2-dev,
               libxslt1-dev, zlib1g-dev, python3-setuptools, python3-bs4,
               python3-cssselect, python3-html5lib, cython3, python3-sphinx-autoapi

$ apt-cache showsrc pygraphviz
W: Unable to locate package pygraphviz
```

**lxml**: the mechanism is 100% sound — `debian_build_deps("lxml", executor)`
would correctly return `libxml2-dev, libxslt1-dev, zlib1g-dev` (after
machinery-filtering) *if it were ever invoked on the project*. It never is,
per 1.1. This is a pure wiring gap, not a mapping-precision gap.

**pygraphviz**: Debian does not package pygraphviz at all — there is no
Debian source to map to, ever, for this repo, regardless of wiring. PEP 725
`[external]` is also absent (pygraphviz's `pyproject.toml`, read directly, has
no `[external]` table). The curated table (`PACKAGE_TO_BUILD_NEEDS`) also has
no `pygraphviz` entry. **All three of the pipeline's existing precise sources
structurally miss pygraphviz** — a fourth source is required, not just
rewiring the existing three onto the Project node. See §2.3.

### 1.4 Secondary finding (adjacent, not this gap): the floor over-fires on a pure-Python sdist

`beautifulsoup==3.2.2` (the ancient pre-bs4 package; distinct from
`beautifulsoup4`) is lxml's one `build_from_source=True` package. It is pure
Python — it has no C extension — but has shipped no wheel since ~2012, so
`native_risk_from_lock`'s "sdist + no matching wheel ⇒ build_from_source=True"
rule (`resolve_lock.py`, `native_risk_from_lock`) flags it as native-risk
purely from wheel-absence. This is harmless in isolation (build-essential is
free/safe to over-include) but is worth flagging for whoever picks up R2 —
"no wheel" ≠ "needs a compiler"; a cheap discriminator (does the sdist's
`pyproject.toml`/`setup.py` declare `ext_modules`/`cythonize`/a Rust backend?)
would sharpen `build_from_source` into a true native-risk signal rather than a
wheel-availability proxy. Not fixed here; noted because §2.4 below reuses the
exact same static-scan primitive for the Project node's OWN classification, so
the fix is a natural two-for-one once built.

## 2. Design: a Project-native-build-obligations stage

### 2.1 Shape of the fix

Add one new construction stage, symmetrical to `seed_build_deps` /
`seed_wheel_oracle_prior`, that treats the Project node as a first-class
build-dep-prior subject instead of trying to force it through the
`NodeType.PACKAGE` pipeline (which is wrong for it anyway — the project has no
`version` in the PyPI sense and never goes through `uv.lock`, so reusing the
Package-shaped functions by relaxing their type filter is the wrong move; a
parallel, Project-shaped entry point that calls the SAME underlying
capability-resolution primitives is the right one).

```
project_native_obligations(graph, repo_path, host_executor, container_executor) -> DepGraph
```

Called once, in `build.py`, right where `seed_build_deps` is currently called
(`build.py:587`) — same stage boundary, same ordering guarantees (Layer.TOOLCHAIN
nodes render before the Layer.PIP project capstone regardless of edges, per
`build_script.py:212-232`: the renderer walks `_LAYER_ORDER` unconditionally
and appends the Project capstone last, so **no topological-edge threading is
required for correctness** — only node seeding. An edge is still worth adding
for `#@node ... requires=...` annotation fidelity, but is not load-bearing).

Internally it assembles from four sources, in priority order, exactly
mirroring `build_dep_prior`'s existing priority union (`build_deps.py:167-223`)
but reading from the LOCAL CHECKOUT instead of a downloaded sdist:

### 2.2 Source #1 — PEP 725 `[external]` read directly off the local repo (near-zero cost, zero recall risk)

`pep725.needs_from_pyproject` (`pep725.py:193-217`) is already a pure function
of pyproject TEXT — it doesn't need `fetch_sdist_pyproject`'s download step at
all when the pyproject is already on disk (which it always is for the
repo-under-test). New call:

```python
text = Path(repo_path, "pyproject.toml").read_text()
needs = pep725.needs_from_pyproject(text, source=project_name)
```

- **Generalizes**: to every repo type in the space (pure-Python, C-ext,
  Rust-ext, sdist-only, src-layout, any backend) uniformly, the day upstream
  adoption grows — it's backend-agnostic by construction (PEP 725 is a
  `pyproject.toml` table, independent of setuptools/hatchling/maturin/pdm).
- **Precision/recall**: precision ~1.0 (curated `_GENERIC_TO_CAPABILITY` map,
  same as the existing dependency-side path); recall ~0 today (adoption is
  near-zero — this is future-proofing, not the lever that moves pygraphviz/
  lxml today). Confirmed empirically: neither pygraphviz's nor lxml's
  pyproject.toml declares `[external]`.
- **Seam**: Python-specific only in the "read this repo's pyproject.toml" step;
  the DepURL parsing/mapping (`pep725.py`) is already ecosystem-neutral text
  (PEP 725 has no Python-specific grammar). Belongs in `python/provider.py`'s
  `package_obligations` wrapper calling into a shared-ish `pep725.py`-style
  module; a hypothetical Rust/Node provider would need its own manifest reader
  but could reuse the same DepURL→capability table if it ever adopts PEP 725
  (unlikely for non-Python ecosystems; low priority to abstract further).

### 2.3 Source #2 — Debian `Build-Depends` keyed by the PROJECT'S OWN distribution name (closes lxml)

Reuse `debian_build_deps` and `PACKAGE_TO_BUILD_NEEDS.get` **verbatim**,
keyed by `_project_name(repo_path)` (`build.py:128-139`, already computed for
the Project node's `name`) instead of a dependency's name:

```python
canonical = normalize_package_name(_project_name(repo_path))
curated = PACKAGE_TO_BUILD_NEEDS.get(canonical, ())
debian = debian_build_deps(canonical, container_executor)   # apt-cache showsrc <name>
```

- **Generalizes**: to any repo whose distribution name matches (or Repology-
  maps to) a real Debian/Ubuntu source package — i.e., any *previously
  published* native package being re-analyzed (forks, vendored copies, CI
  checkouts of upstream). This is most of the "well-known C/Cython-extension"
  corner of the repo-type space (lxml, psycopg2, pyzmq-if-it-had-no-wheel,
  numpy, scipy, pandas, cryptography's Rust toolchain aside).
- **Does NOT generalize** to unpublished/private/pre-release/renamed projects
  (pygraphviz IS published on PyPI, but Debian simply never packaged it —
  `apt-cache showsrc pygraphviz` → "Unable to locate package", verified live).
  This is why §2.4 is required as an independent source, not a fallback that
  subsumes this one.
- **Precision/recall**: precision inherited from `debian_build_deps`'s existing
  machinery filter (`is_system_lib`/`is_machinery`, `debian_builddeps.py:65-109`)
  and the `_builds_python3_binary` cross-check (`debian_builddeps.py:81-97`),
  both already exercised and correct (verified: lxml's real Debian stanza has
  `Binary: python3-lxml, ...`, passes the gate cleanly). Recall bounded by
  Debian/Ubuntu packaging coverage of PyPI — high for old/popular native
  libraries, ~0 for anything Debian never packaged.
- **Cost**: one `apt-cache showsrc <name>` per repo (after `ensure_deb_src`,
  already paid once per container by `seed_build_deps` for any OTHER
  source-built package in the closure — usually already warm). Requires the
  live container (network to the pinned snapshot mirror); degrades to `[]` on
  any failure, matching the existing dependency-side contract.
- **Seam**: Python-specific by necessity (Debian's *Python*-source naming
  convention, the `python3-*` binary gate) — stays in `python_deps/depgraph/`.
  The GENERAL principle ("look up the project's own distro Build-Depends by
  its own package name, not just its dependencies'") is ecosystem-agnostic and
  should be documented as a `EcosystemProvider.package_obligations` contract
  note so a Rust provider does the RPM/deb-src-equivalent lookup for its own
  crate name too (`apt-cache showsrc` has real coverage for Rust crates
  packaged as `librust-*` source packages on Debian, following the exact same
  shape).

### 2.4 Source #3 (NEW) — static native-build-surface scan of the project's own build manifest (closes pygraphviz; the one genuinely new mechanism)

This is the mechanism that generalizes to repos Debian/PEP-725 will never
cover, because it reads the repo's OWN declaration of what it links against —
the one source of truth that is unconditionally present for any native-
extension project, published or not.

**What to scan, in priority order (setuptools covers the overwhelming
majority of the "C/Cython extension, no wheel" corpus stratum):**

1. `setup.py` — `ast.parse` (never `exec`/`import`: repos are untrusted input)
   walking for `Extension(...)` / `Pybind11Extension(...)` / `cythonize(...)`
   call nodes; extract the `libraries=[...]` keyword's list-of-string-literals
   (skip anything not a literal — e.g. a `libraries=get_libs()` computed value
   is a silent miss, never a wrong guess). pygraphviz's real `setup.py`:
   ```python
   Extension(name="pygraphviz._graphviz", sources=["pygraphviz/graphviz_wrap.c"],
             libraries=["cdt", "cgraph", "gvc"], ...)
   ```
   `libraries=["cdt","cgraph","gvc"]` is a literal list — trivially AST-extractable,
   no execution required.
2. `pyproject.toml` — `[tool.setuptools.ext-modules]` (newer setuptools schema,
   TOML-native — no AST needed, just `tomllib.load`), and presence of
   `[build-system] build-backend` naming `mesonpy`/`scikit-build-core` (CMake/
   Meson-backed extensions — treat as "definitely native, libraries unknown"
   rather than trying to parse CMakeLists.txt/meson.build in v1).
3. `*.pyx` / `*.pxd` file presence anywhere in the repo (Cython) — a coarse
   "this repo compiles Cython" signal even when `libraries=` isn't set (pure
   Cython speedups with no external lib still need `build-essential` +
   `python3-dev`, just not a specific `-dev`).
4. (Future, via the ecosystem seam) `Cargo.toml` `[lib] crate-type = ["cdylib"]`
   + `links = "<libname>"` key for PyO3/maturin-built extensions; a CGo
   provider would scan `#cgo pkg-config: ...` / `#cgo LDFLAGS: -l...` pragmas
   in `.go` files. Same shape, different regex/AST target — see §3.

**How each `libraries=[...]` entry becomes an apt directive — reuse, don't
reinvent:**

```python
from python_deps.depgraph.os_resolver import ObservedNeed, resolve

need = ObservedNeed("linker_lib", "cgraph", context="build", strength="curated",
                     evidence=f"setup.py:Extension.libraries")
cands = resolve(need, container_executor)   # table miss -> apt-file search libcgraph.so
```

`ObservedNeed(kind="linker_lib", ...)` and its full resolution path
(`filter_by_kind`'s `linker_lib` branch matching `lib{name}.so` under
`/usr/lib/`/`/lib/`, `rank`'s `-dev`-preference tie-break, `check_command_for`'s
`find ... -name lib{name}.so`) **already exist** in `os_resolver.py:69-214` and
are already wired through `resolve()` — this source needs ZERO new resolver
code, only a new *extractor* that turns `setup.py`'s `libraries=[...]` into
`ObservedNeed` objects.

**VERIFIED live** (`apt-file search`, same container as §1.3) that this
resolves correctly for all three of pygraphviz's declared libraries:

```
libcgraph.so  -> libgraphviz-dev  (/usr/lib/aarch64-linux-gnu/libcgraph.so)
libgvc.so     -> libgraphviz-dev  (/usr/lib/aarch64-linux-gnu/libgvc.so)
libcdt.so     -> libgraphviz-dev  (/usr/lib/aarch64-linux-gnu/libcdt.so)
```

(Two of the three raw `apt-file search libgvc.so` hits are noise —
`budgie-core`/`gnome-shell` also ship a same-named file under an unrelated
path — but `os_resolver.filter_by_kind`'s existing `linker_lib` branch already
restricts matches to `path.startswith("/usr/lib/")` with exact `lib{name}.so`
basename AND `rank`'s `-dev`-suffix preference, which uniquely selects
`libgraphviz-dev` in all three cases — the corpus already had a bare
`libcgraph6`/`libgvc6`/`libcdt5` runtime-only variant present too, and `-dev`
preference already breaks that tie correctly with zero new logic.) The real
Debian package name is **`libgraphviz-dev`** (not `graphviz-dev`, which the
raw DIAGNOSTIC.md prose uses informally — worth correcting when this ships).

- **Generalizes** (this is the headline claim): to EVERY C/Cython-extension
  repo in the DIAGNOSTIC.md repo-type space, published or not, Debian-packaged
  or not, PEP-725-adopting or not — because it reads the one artifact every
  native-extension repo must have (its own build declaration) to build at all.
  Directly closes the "C-ext with NO wheel" stratum (pygraphviz) that §2.2/2.3
  structurally cannot reach. Via the seam (§3), the identical principle (scan
  the project's OWN build manifest for declared native link targets, map
  through a capability resolver) is exactly the mechanism a Rust/CGo provider
  needs too — Cargo.toml/`links=`/`#cgo` pragmas ARE that ecosystem's
  `Extension(libraries=...)`.
- **Precision/recall**: precision as high as the underlying `linker_lib`
  resolver path (same apt-file/table machinery already used and correct for
  runtime sonames) — false positives only from `apt-file` package-name
  ambiguity, already mitigated by existing path/`-dev` filtering. Recall bound
  by (a) setup.py using literal `libraries=[...]` rather than a computed
  expression (common case; dynamic `libraries=` is rare and degrades safely to
  "miss, fall through to floor" rather than a wrong guess), and (b) coverage
  of non-setuptools backends in v1 (meson-python/scikit-build-core repos get
  the coarse "definitely native" signal from build-backend name, not a
  specific-`-dev` prediction, until a v2 CMake/meson scanner is added).
- **Cost**: pure host-side static parse, zero network for the *extraction*
  step; the *resolution* step needs a container only on an `apt-file`
  fallback miss (same cost profile `seed_build_deps` already pays for every
  OTHER source-built package — not a new class of expense).
- **Seam**: the *extractor* (parse setup.py/pyproject for native build
  declarations) is inherently ecosystem-specific (setuptools `Extension`
  grammar is Python-only) and belongs in `python_deps/depgraph/` (a new
  `project_native_scan.py`, mirroring `debian_builddeps.py`'s module shape).
  The *capability resolution* (`ObservedNeed(kind="linker_lib", ...)` →
  `os_resolver.resolve`) is ALREADY ecosystem-neutral (lives in
  `os_resolver.py`, no Python-specific assumption in `VALID_KINDS`/
  `PROVIDER_TABLE`/`filter_by_kind`) — a Rust provider's Cargo.toml/`links=`
  scanner would produce the exact same `ObservedNeed` shape and call the exact
  same `resolve()`. The right generalization is: put the *scanner interface*
  (`scan_native_build_surface(repo_path) -> list[ObservedNeed]`) on
  `EcosystemProvider` (or a sibling protocol it composes), with each language
  provider supplying its own implementation, all funneling into the one
  shared `os_resolver.resolve()`.

### 2.5 Source #4 — unconditional generic floor for ANY detected native-build signal (closes pygraphviz's "predicted apt=[]" symptom even in the worst case)

Independent of whether §2.2/§2.3/§2.4 resolve anything specific, if the
scanner in §2.4 detects ANY native-build signal at all (an `Extension(...)`
call, a `.pyx` file, a native build-backend name) — even one whose
`libraries=` couldn't be statically extracted — stamp the Project node so
`seed_wheel_oracle_prior`'s existing `build-essential` floor logic
(`seed.py:58-86`) is reachable for it too:

```python
# in project_native_obligations, before the floor stage runs:
if has_native_build_signal:
    new_project = replace(project_node, build_from_source=True)  # or a
        # project-shaped equivalent flag seed_wheel_oracle_prior's filter checks
```

This is the safety-net layer of the design: **precision is irrelevant here —
"this repo compiles something" is a binary, always-safe-to-over-include fact**
(build-essential is free/idempotent to install even when a smarter mechanism
would have found nothing more specific). This is what turns "predicted
apt=[]" into "predicted apt=[build-essential]" as an absolute floor, even for
a hypothetical native-ext repo the AST scanner fails to parse for any reason.

- **Generalizes** unconditionally: "detected native build signal ⇒ compiler
  toolchain floor" needs no per-ecosystem knowledge beyond "does this
  ecosystem's manifest say it compiles" — Rust: always true (COMPILE
  certify_mode, `ecosystems/base.py:26-35`, already modeled as bulk-compile);
  CGo: `import "C"` presence. This is the cheapest, safest, most-generalizing
  single line in the whole design and should ship even if §2.4's specific
  `-dev` extraction is deferred.
- **Precision/recall**: recall-maximizing by design (never wrong to add
  build-essential to a repo that in fact needs it; only "wasteful" — a few
  hundred KB — when the scanner mis-detects a signal that isn't really native,
  which the four signal types in §2.4 make vanishingly rare).

## 3. Where this lands relative to the `EcosystemProvider` seam

| Piece | Location | Ecosystem-agnostic? |
|---|---|---|
| `project_native_obligations` orchestrator | new `python_deps/depgraph/project_native_deps.py`, called from `build.py` at the `seed_build_deps` call site (`build.py:587`) | No — Python-specific orchestration, mirrors `_python_package_obligations`/`_python_native_obligations`'s existing role as the thing `PythonProvider.package_obligations`/`native_obligations` delegate to (`ecosystems/python/provider.py:49-73`) |
| PEP 725 local-read (§2.2) | extend `pep725.py` with a `needs_from_local_pyproject(repo_path)` thin wrapper around the existing pure `needs_from_pyproject` | Text/DepURL parsing already ecosystem-neutral; the "read this repo's own manifest" call site is Python-specific |
| Debian-by-own-name (§2.3) | reuse `debian_builddeps.py` + `PACKAGE_TO_BUILD_NEEDS` verbatim, new caller only | Debian-Python-naming-specific by necessity; principle ("ask the distro about your OWN name, not just deps'") generalizes, mechanism doesn't |
| Native-build-surface scanner (§2.4) | new `python_deps/depgraph/project_native_scan.py` (setup.py AST walk + pyproject `[tool.setuptools.ext-modules]` + `.pyx` glob) | Extractor is Python-specific; **output type (`ObservedNeed`) and consumer (`os_resolver.resolve`) are already ecosystem-neutral** — this is the piece to formalize as a per-provider protocol method (`scan_native_build_surface`) so Rust/CGo plug into the SAME resolver |
| Generic floor (§2.5) | one conditional inside `project_native_obligations`, reusing `seed.py`'s `_build_essential_node`/`_BUILD_ESSENTIAL_ID` singleton (don't duplicate — import and dedupe by id, exactly as `seed_build_deps` already does for `pkg-config`) | Fully agnostic in principle; Rust's `CertifyMode.COMPILE` already treats "everything compiles" as a first-class fact, so this floor is closer to a no-op there (the whole closure already implies a toolchain) |
| Render ordering | **no change needed** — `build_script.py:212-232`'s layer-then-capstone walk already guarantees any newly-seeded `Layer.TOOLCHOICE` node renders before the Project capstone, edge-independent | Already ecosystem-neutral |

Do **not** put the setup.py/pyproject AST-walking code itself behind the
`EcosystemProvider` Protocol as a required method signature in this slice —
land it as a Python-internal module first (matching how `debian_builddeps.py`/
`pep725.py` are Python-internal today despite the underlying `os_resolver`
being shared), and promote `scan_native_build_surface` to an explicit
`EcosystemProvider` protocol method only when a second provider (Rust) is
ready to implement it for real — avoids designing the interface from a single
data point.

## 4. Precision/recall summary across the four sources

| Source | Recall driver | Precision risk | Needs container? | Needs network? |
|---|---|---|---|---|
| §2.2 PEP 725 (local read) | upstream adoption (near-0 today) | none (curated DepURL table) | no | no |
| §2.3 Debian by own name | distro packaging coverage of the PyPI name | machinery-filter false-negative (already tested) | yes (`apt-cache showsrc`) | yes (pinned snapshot mirror) |
| §2.4 Native-build-surface scan | literal-vs-computed `libraries=` in setup.py; backend coverage (setuptools v1; meson/cmake coarse-only) | `apt-file` package-name collision, already mitigated by existing path/`-dev` ranking | only on `linker_lib` table-miss (`apt-file` fallback) | only on that fallback |
| §2.5 Generic floor | "any native signal detected" (broad, cheap) | none (build-essential is always safe to add) | no | no |

## 5. Eval verification plan

Reuse the exact diagnostic harness already in
`/private/tmp/.../scratchpad/sweep.py` and `trace_apt.py` (construction-only,
no replay needed to verify predict-time coverage; a follow-on full-replay run
is the final proof of "env_works" but is out of scope for a predict-time fix).

1. **pygraphviz** — before: `predicted_apt=[]`. After landing §2.4+§2.5:
   expect `predicted_apt ⊇ {build-essential, libgraphviz-dev}` (pkgconf may or
   may not appear depending on whether pygraphviz's build actually shells out
   to `pkg-config` — not required for this fix). Full-replay ladder rung to
   watch: `install_ok` should flip True (currently fails at `gcc`, per the
   diagnostic's `env_works` row); this is the direct pass/fail the DIAGNOSTIC.md
   R1 write-up calls out by name.
2. **lxml** — before: `predicted_apt={build-essential, pkgconf}`. After
   landing §2.3 (alone — §2.4 is not required for lxml since Debian already
   covers it): expect `predicted_apt ⊇ {build-essential, pkgconf, libxml2-dev,
   libxslt1-dev, zlib1g-dev}`, matching the real `apt-cache showsrc lxml`
   Build-Depends captured live in §1.3 (machinery-filtered: `debhelper`,
   `dh-python`, `python3-all-dev`, `python3-setuptools`, `cython3`, and the
   `python3-bs4`/`python3-cssselect`/`python3-html5lib`/`python3-sphinx-autoapi`
   Debian-side test deps are dropped by the existing `is_machinery`/
   `python3-*`-prefix filter in `debian_builddeps.py:71-74` — already correct,
   no change needed there).
3. **Regression guard, the other 14 repos** — every control/syslib repo whose
   Project node currently contributes NOTHING to `predicted_apt` (click,
   flask, jinja, rich, python-dotenv, pyyaml, pillow — all pure-Python or
   wheel-bundled-lib repos with no `Extension()`/`.pyx`/native backend) MUST
   still predict `[]` for their own Project node after this change — the
   scanner's job is to detect a REAL signal, not to add a floor to every repo.
   This is the precision guardrail: re-run `sweep.py` over the full 16-repo
   corpus and assert `n_pkg`/`predicted_apt` for every non-native repo is
   byte-identical to today's baseline (already captured in
   `scratchpad/sweep_out/*.json` — a ready-made before/after diff fixture).
   requests/httpx (control, but each has exactly 1 dependency-side src=True
   package already) are also expected to be unaffected by this change (it only
   adds Project-node predictions; it doesn't touch the existing dependency-side
   `seed_build_deps` path at all).
4. **New unit-level fixtures** (mirroring the existing
   `graph_fidelity/edge_cases/{pgconfig_psycopg2,nowheel_buildessential,pil_pillow}`
   pattern): add `edge_cases/project_native_pygraphviz` (a minimal fixture repo
   with a `setup.py` `Extension(libraries=["foo"])` + a fake `ObservedNeed`
   table entry, asserting the capability node + edge appear on the PROJECT id,
   not floating) and `edge_cases/project_debian_lxml` (asserting the
   Debian-by-own-name path fires for a fixture whose `[project].name` matches
   a stubbed `apt-cache showsrc` fixture output) — both pure/no-Docker, testing
   `project_native_obligations` in isolation the way `build_deps.py`'s own
   tests presumably stub `Executor.run`.

## 6. Out of scope / explicitly deferred

- **pkg-config/.pc probing at PREDICT time**: rejected as a *primary* predict
  mechanism — it requires the target container to already have the library
  installed to query it, which is exactly the chicken-and-egg the predict
  stage exists to avoid. It remains valuable purely as an OBSERVE-time
  cross-check (already the role `probe.py`'s capability-observation path and
  `check_command_for`'s `pkg-config --exists <name>` play for pkgconfig-kind
  needs) — no change proposed there.
- **Non-setuptools native backends (meson-python, scikit-build-core, CMake/
  Meson-driven builds)**: v1 only detects "this is definitely a native build"
  from the build-backend name (routes to §2.5's floor) without attempting to
  parse CMakeLists.txt/meson.build for specific `-lgvc`-style link directives
  — a real but bounded recall gap, left for a follow-up once the setuptools
  path (the majority stratum) is landed and measured.
  parses.
- **Rust/CGo scanner implementations**: designed for (§3's protocol seam) but
  not specified in file-level detail here — no Rust/CGo repo exists in the
  current corpus to verify against; premature to hard-code Cargo.toml/`#cgo`
  parsing rules without a concrete failing case the way pygraphviz/lxml gave
  us for Python.
- **R2 (build-mode classification robustness) and R3 (Debian-source-mapping
  collision precision)**: explicitly separate root causes per DIAGNOSTIC.md;
  this document does not touch typer's resolution-oscillation bug or
  psycopg2's `postgresql`-server over-include. The one place they intersect
  is noted in §1.4 (the `beautifulsoup==3.2.2` wheel-absence-as-native-risk
  false signal) — flagged, not fixed, here.
