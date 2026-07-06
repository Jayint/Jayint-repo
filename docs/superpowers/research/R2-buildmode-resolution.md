# R2 — Build-mode classification & resolution robustness

Status: research/design, not implemented. No source file was modified while producing
this document; every claim below was verified by reading the cited `file:line` and,
where marked **[REPRODUCED]**, by actually invoking `uv`/the parser functions against
typer's real checked-out repo and its real `uv`/PyPI behavior.

## 0. One-paragraph summary of the mechanism

typer's apt over-prediction (31 packages, all 67 resolved packages `build_from_source
= None`) is **not** a single bug and **not** actually caused by the `docs_src` residue
the construction log names. It is three small, independently-real scope/robustness bugs
in the *root-selection → era-anchor → uv-lock* path that **compound** into a real,
reproducible `uv lock` failure for typer's specific (huge, CI-tooling-polluted)
dependency-group closure; that failure is **unattributable** by the existing
error-parser, so the pipeline falls through to a degraded `uv pip compile` path that
(by design) never stamps `build_from_source`. Separately, the emitted `docs_src`
residue is a real but *harmless*, unrelated bug (a namespace-package misclassification)
that only *looks* like the smoking gun because it fires in the same log line. On top of
all that, the build-dep prior treats `None` ("we never found out") the same as `True`
("confirmed must build from source") when deciding whether to dump a package's full
Debian `Build-Depends` — which is the actual amplifier that turns "67 unknowns" into
"31 apt packages including valac/libgee/dbus-test-runner."

Two fixes, independent of each other, both needed:

- **Fix A** (resolution robustness): stop the *specific* uv-lock failure from
  happening/cascading for large dev/doc/test dependency-group repos.
- **Fix B** (safe unknown-mode default): even when a closure's build mode genuinely
  can't be determined, stop `None` from behaving like `True` in the one place that
  matters (the Debian-Build-Depends dump).

Fix B alone makes typer's apt prediction collapse to ~0 *today*, with a one-line change,
independent of whether Fix A ever lands. Fix A is the "don't let this happen at all"
robustness layer Fix B is a safety net for.

---

## 1. Sub-cause (a): why does typer's resolve degrade (and not the other 15)?

### 1.1 What `docs_src` actually is (and why it is a red herring, not the cause)

`docs_src/` is a directory of ~255 tutorial scripts at typer's repo root, imported
directly by the test suite (`tests/test_completion/...py`: `from docs_src.arguments.default
import tutorial001 as mod`). It is a **PEP 420 implicit namespace package**: there is no
`__init__.py` directly inside `docs_src/`, and no `.py` file directly inside it either —
only subdirectories, some of which get an `__init__.py` many levels down
(`docs_src/subcommands/tutorial001/__init__.py`). Verified on the actual checkout:

```
$ find docs_src -maxdepth 1 -name "*.py"        # (empty)
$ find docs_src -maxdepth 1 -name "__init__.py"  # (empty)
```

Both of the pipeline's "is this name local, not a PyPI dist" guards only look **one
level deep**:

- `src/python_deps/depgraph/scan.py:75-94` (`_local_module_names`) adds a directory's
  basename to `local_names` only `if "__init__.py" in filenames` **for that directory**
  (line 89-90). `docs_src` itself never satisfies this.
- `src/python_deps/import_graph.py:139-145` (`_looks_like_python_module_dir`) checks
  `(path / "__init__.py").is_file()` or "any `.py` file directly inside `path`" — same
  one-level limitation, called from `collect_project_local_modules`
  (import_graph.py:35-48) against `docs_src`'s own directory listing (all
  subdirectories, no files) → also False.

So `docs_src` is classified `external` by `scan_imports` (import_graph.py:98) and
survives `scan_to_nodes`'s exclusion filters (scan.py:150-179) — `docs` is in
`_EXCLUDED_SEGMENTS` (scan.py:48-55) but the literal segment is `docs_src`, a different
string, so the path-exclusion doesn't catch it either. It becomes a real `Import` node
that Phase-A's fixpoint (`build.py:335-432`) audits every round and can never satisfy
(no PyPI package ships a top-level module literally named `docs_src`), so
`generate_candidates`/`choose_provider` never ACCEPTs a repair candidate for it, and
eventually `_phase_a_fixpoint` logs exactly the diagnostic's line (build.py:418-424):
`"phase-A stopped: no new repair candidate; residue left unresolved (fixpoint/
oscillation): ['docs_src']"`.

This is a real, fixable bug (Fix A.4 below) — but it is a **parallel, independent**
symptom. It does not, by itself, explain why all 67 *other* packages lost their
build-mode signal. `docs_src` is unresolvable regardless of whether the round's
`resolve_closure` call used a real `uv.lock` parse or the degraded fallback; it is not
what *causes* the fallback.

### 1.2 The real mechanism: three compounding, independently-real bugs — **[REPRODUCED]**

**(a1) Requirements-file discovery/role-labeling defaults an unrecognized file to
"runtime".**
`_discover_requirements_files` (`src/python_deps/evidence.py:260-276`) globs every
`requirements*.txt` at repo root. typer ships one most repos wouldn't:
`requirements-github-actions.txt` (CI-workflow-only: `PyGithub`, `pydantic`,
`pydantic-settings`, `httpx>=0.27.0,<0.28.0`). `_requirements_role`
(evidence.py:279-300) recognizes `docs`/`test`/`dev` tokens in the filename and
otherwise **defaults to `"runtime"`** (line 300: `return "runtime"`). "github" and
"actions" match none of those tokens, so this file's rows get `kind="dependency"`
(`_role_kind_source`, evidence.py:303-307) — i.e. **always-in-scope runtime
dependencies** per `roots.py:184-185` (`_in_test_scope`: `kind=="dependency" → always
in`). A CI-only `httpx` floor is now indistinguishable from a real runtime need.

**(a2) `requirements-tests.txt` exact-pins two dev tools with no ABI relevance.**
`mypy==1.4.1` and `ruff==0.2.0` (real lines in typer's `requirements-tests.txt`, role
`"test"` → `kind="dev_group"`, correctly in-scope per `_DEV_GROUP_DENYLIST`
roots.py:154-161, which excludes docs/release/benchmark/examples but not test/dev).
These are ordinary lint/type-checker version pins — nothing like the ABI-sensitive
`opencv-python==4.9.0.80` case `pins.py`'s docstring (lines 1-19) was written for.

**(a3) `compute_exclude_newer` anchors the *entire* resolve to the max release date of
*any* exact pin, runtime or not.**
`src/python_deps/depgraph/pins.py:71-84`: `compute_exclude_newer` takes every
`name==version` pin in the *full* root list (no `kind` filtering — the roots tuple
shape `(import_id, dist_token)` returned by `select_roots` carries no kind at all) and
sets `exclude_newer = max(dates) + 1 day`. Verified via PyPI:

```
ruff 0.2.0   uploaded 2024-02-01T23:21:25   (the max of the two dev-tool pins)
mypy 1.4.1   uploaded 2023-06-25T23:21:14
httpx 0.27.0 uploaded 2024-02-21T13:07:50   (from the CI-only root in a1)
```

`exclude_newer` becomes `2024-02-02`, three weeks *before* httpx 0.27.0 existed. This is
called from `build.py:502-507` (Stage 2a) on the **full** `roots` list `select_roots`
returned (runtime ∪ dev/test groups ∪ the a1 CI-only file), unconditionally.

**Empirical reproduction** (typer's real combined root set, `uv 0.10.4`):

```
$ uv lock --python 3.11                              # no era anchor: OK
Resolved 55 packages in 297ms

$ uv lock --python 3.11 --exclude-newer 2024-02-02    # era-anchored per (a3): FAILS
  × No solution found when resolving dependencies:
  ╰─▶ Because only httpx<=0.26.0 is available and your project depends on
      httpx>=0.27.0,<0.28.0, we can conclude that your project's requirements
      are unsatisfiable.
```

This is a **genuine, reproducible** `uv lock` failure — not a flaky or hypothetical one.
It is entirely a scoping artifact: neither `httpx` (a1's mis-scoped CI dep) nor
`ruff`/`mypy` (a2's incidental dev-tool pins) has anything to do with the ABI problem
`compute_exclude_newer` exists to solve.

**(a4) The failure is unattributable, so the pipeline can't recover surgically — it
falls through to the degraded fallback on the first failure.**
`resolve_errors.py:67-114` defines six regexes covering uv's known failure phrasings
(`_REGISTRY_MISS_RE`, `_NO_VERSION_RE`/`_NO_VERSION_PLAIN_RE`, `_YOU_REQUIRE_RE`,
`_DEPENDS_ON_RE`, `_BUILD_FAILURE_RE`, `_UNUSABLE_RE`, `_PY_INCOMPAT_RE`). None matches
uv's actual phrasing above ("Because only X<=V is available and your project depends on
X>=V2, we can conclude ... unsatisfiable"). Verified directly:

```python
>>> from python_deps.depgraph.resolve_errors import parse_resolver_error, _offending_root_names
>>> diag = parse_resolver_error(open("/tmp/uverr.txt").read())   # the exact stderr above
>>> diag
ResolverDiagnosis(missing=(), constraints=(), conflicts=(), python_incompat=None, raw=...)
>>> _offending_root_names(diag, current_root_names, frozenset())
set()
```

`_offending_root_names` returns an empty set. In `resolve_closure`
(`resolve.py:280-327`): `offending = _offending_root_names(...)` is empty →
`if not offending or remaining == current: break` (line 325-326) fires **immediately, on
the first attempt** — no per-root dropping is even tried. Control falls to
`_pip_compile_fallback` (resolve.py:329-333).

**(a5) The fallback path never calls `native_risk_from_lock`/`_stamp`, so every package
it produces defaults to `build_from_source=None`.**
`_pip_compile_fallback` (resolve.py:404-451) builds nodes via `_package_node(name,
version, provenance="uv pip compile")` and returns — no `_stamp` call anywhere in this
path (contrast the success branch, resolve.py:296-304, which always calls
`native_risk_from_lock` + `_stamp`). `Node.build_from_source` defaults to `None`
(`schema.py:160`). `_compile_command` (resolve.py:339-352) also **never passes
`exclude_newer`**, so the fallback resolves the *unanchored* 55-ish-package closure fine
(matching the diagnostic's "67 packages, all present, all `unk`" — the fallback
succeeds at resolving, it just can't classify build mode). This exactly reproduces the
diagnostic's `src=0, wheel=0, unk=67` row.

Putting 1.1 and 1.2 together: the log line the diagnostic quotes
(`"residue ... docs_src"`) is emitted by the **same round** that used the degraded
fallback (since `docs_src` is unresolvable in *every* round regardless of resolve path)
— but the fallback, and thus the all-`None` stamping, is caused by (a1)-(a4), not by
`docs_src`.

### 1.3 Why 15 other repos don't hit this

None of click/flask/jinja/rich/python-dotenv/requests/httpx/pyyaml/pyzmq/pillow/
cryptography/psycopg2/lxml/pygraphviz/python-semantic-release ships a root-level
`requirements-<ci-tool>.txt` with a `>=`-floored dependency whose release postdates an
unrelated exact-pinned dev tool elsewhere in the same closure. typer's combination —
PDM-backed, huge docs/test/CI requirements-file sprawl, mixed old-exact + new-floor
pins — is unusual but **not exotic**: pinned lint/type-checker versions
(`black==`, `flake8==`, `mypy==`, `ruff==`) alongside a modern floored runtime
dependency is an extremely common OSS pattern (pre-commit-managed repos in particular).
This is a real, generalizable robustness gap, not a typer-specific quirk.

---

## 2. Sub-cause (b): the unsafe unknown-mode default — the actual apt amplifier

Three call sites branch on `build_from_source`, with two different (and, it turns out,
inconsistently justified) semantics for "not confirmed wheel":

| site | gate | cost of a false positive |
|---|---|---|
| `seed.py:76` (`seed_wheel_oracle_prior`) | `build_from_source is not False` (True **or** None) | one generic `build-essential` apt pkg — cheap, universal, virtually never wrong |
| `build_deps.py:309` (`seed_build_deps`) | `build_from_source is False: continue` → proceeds for True **or** None | `build_dep_prior` → `debian_build_deps` (`debian_builddeps.py`) — a full Debian source's `Build-Depends`, arbitrary size, **collision-prone** (R3: pip `click` → Debian `click`, the GNOME clock-manager package, pulling `valac`/`libgee-0.8-dev`/`dbus-test-runner`) |
| `emit.py:78` (`_toolchain_ready`) | blocks package emission until its TOOL deps are SATISFIED, unless `build_from_source is False` | gates emission on whatever the two seeders above produced |

`seed.py`'s own docstring (lines 65-69) is explicit that including `None` is a
**deliberate choice** for the *generic* floor: "known from-source builds **or unknown
build mode from degraded resolution**." That reasoning is sound for a single, harmless
`build-essential` line. It is **not** sound for `build_deps.py:309`, which gates the
expensive, name-collision-prone Debian dump — and which reuses the exact same
"not False" test without a matching justification.

This is the amplifier: once (a1)-(a5) leave all 67 typer packages at `build_from_source
= None`, `seed_build_deps` (build_deps.py:286-359) runs `build_dep_prior` on **every
one of them** (line 309-322), and `build_dep_prior` (build_deps.py:167-223) calls
`debian_build_deps(canonical, executor)` (line 211) unconditionally for each — this is
where R3's Debian-source-name collisions (`click`→clock-manager, etc.) get to fire 67
times instead of 0. `_apt_installable` (build_deps.py:147-154, called at line 214) only
checks that the resulting apt set *installs cleanly together* — it has no concept of
"is this even the right Debian source"; valac/libgee/dbus-test-runner install together
just fine, so this guard doesn't catch the collision.

`emit.py:78`'s gate (`pkg.build_from_source is not False`) then blocks each of these 67
packages from emitting until their bogus TOOL deps are satisfied — which is exactly why
the diagnostic's `emitted apt == predicted apt` in every row: the over-prediction isn't
inert graph noise, it becomes real `setup.sh` content because nothing downstream stops
an unresolved TOOL edge from gating emission.

### Verified: `build_from_source` is *never* left `None` by a real lock parse

`wheel_oracle.risk_from_packages` (`wheel_oracle.py:52-99`) computes a definite boolean
for **every** package entry it processes (`build_from_source = has_sdist and
matching_wheel is None`, line 83) — it has no path that returns `None` for a package it
actually sees. Combined with the diagnostic's own data (unk=0 for all 15 clean repos;
unk=67 only for the one repo whose resolve fell back), this means **in the current
system, `build_from_source is None` is, in practice, a pure proxy for "the lock-parse
path never ran"** — never a case of "we looked and genuinely couldn't tell." That
observation directly licenses the fix below.

---

## 3. Fix A — resolution robustness (targets sub-cause a)

Three surgical, independent changes, ordered cheapest/lowest-risk first. All operate on
the **shared evidence/roots/resolve-error layer** that every Python backend (uv-native,
poetry, pdm, pip-tools) already funnels through before `resolve.py`'s throwaway-uv-lock
step runs (see §5) — none of them is uv-specific in *effect*, only in trigger surface.

### A.1 — Recognize the failure class in `parse_resolver_error` (do this first; smallest, highest-leverage)

`resolve_errors.py` already has precedent for exactly this kind of fix:
`_BUILD_FAILURE_RE` and `_UNUSABLE_RE`'s docstrings (lines 95-114) say outright that
missing a phrasing variant causes "whole-closure collapse" and cite prior incidents
(RATBench mcp-atlassian + docling) that were fixed by adding a regex for uv's exact
wording. typer's failure is the next variant in that same series: uv's PubGrub resolver
phrases a simple "floor above the era-anchor" conflict as `"Because only <pkg>
<op><version> is available and (?:your project|<pkg2>) depends on <pkg><specifier>, we
can conclude ... unsatisfiable"`. Add a regex (`_ONLY_AVAILABLE_RE` or similar)
extracting the constrained package name into `diag.missing`, mirroring
`_BUILD_FAILURE_RE`'s pattern exactly (same file, same `_add_missing` call). This alone
turns `_offending_root_names` from `set()` into `{"httpx"}` for typer's case, letting
`resolve_closure`'s existing per-root-drop retry (resolve.py:318-327, already-written,
already-tested logic) drop `httpx` and re-lock the other 66 roots through the **real**
`parse_uv_lock`/`native_risk_from_lock` path — recovering true build-mode stamps for
everything except the one genuinely-unresolvable-under-the-anchor root.

*Why this generalizes*: this is defense-in-depth, not a point fix — uv's message
format is not a stable contract, and PubGrub-style resolvers routinely phrase small
unsat cores several different ways. Any future repo whose closure produces an
unrecognized-but-real conflict hits the identical cliff (empty diagnosis → immediate
fallback → all-`None`). This is exactly the class of bug the module's own comments
describe as a known, recurring failure mode being patched incrementally — R2 is simply
the next occurrence.

*Risk*: low. Purely additive (one more regex + one more `for m in ... .finditer` loop,
same shape as five existing ones). No existing regex's match set changes.

### A.2 — Don't let CI/tooling-only requirements files masquerade as runtime deps

`evidence.py:279-300` (`_requirements_role`) should not default an **unrecognized**
root-level `requirements*.txt` to `"runtime"`. The safer default, given the glob at
`_discover_requirements_files` (evidence.py:260-276) already only fires for files
matching `requirements*.txt` (i.e., never the canonical `requirements.txt` itself,
which has its own stem with no suffix) — any *suffixed* variant
(`requirements-<x>.txt`) is, by long-standing pip convention, a **secondary,
special-purpose** file (`-dev`, `-test`, `-docs`, `-ci`, `-lint`, `-release`, ...); real
runtime dependencies live in `requirements.txt` (no suffix) or the manifest
(`pyproject.toml [project.dependencies]`, `setup.cfg`). Flip the default branch (line
300) from `return "runtime"` to a new safe bucket — e.g. `return "dev"` (already in the
enum-like set of roles; falls under `kind="dev_group"`, default-included per
`_DEV_GROUP_DENYLIST`, so recall is preserved — the packages still get pulled in for
resolving/testing, they just stop being treated as "hard runtime, never optional" and,
critically, stop being eligible input to A.3 below unless truly runtime-sourced).

*Why this generalizes*: "repo ships a `requirements-<something-not-obviously-docs-or-
test>.txt`" is common (`-ci.txt`, `-lint.txt`, `-release.txt`, `-build.txt`,
`-notebook.txt`) and today **all** of them silently become hard runtime deps. This is a
recall-preserving, precision-improving change with no new denylist to maintain (unlike
R3's Debian-name-collision problem, this fix needs no curated list — it inverts an
*unsafe* default, it doesn't add a new heuristic).

*Risk*: low-medium. A rare repo whose *actual* runtime deps genuinely live only in a
suffixed requirements file (e.g. `requirements-prod.txt`, no manifest deps at all) would
now get those deps classified `dev_group` instead of `dependency`. Since `dev_group` is
default-included (not gated by `needed_extras` the way `optional_dependency` is), the
resolve still includes them — only the era-anchor scoping in A.3 would exclude them from
being pin-authoritative, which is exactly the desired direction for a file we can't
positively identify as the runtime source of truth.

### A.3 — Scope `compute_exclude_newer` to runtime-kind pins only

The actual anchor point of the bug. `pins.py:71-84` receives whatever `roots` list its
caller passes with **zero kind information** — `select_roots` (roots.py:289-360)
returns bare `(import_id, dist_token)` tuples; `kind` (`dependency` / `optional_dependency`
/ `dev_group`) is computed internally by `_in_test_scope` (roots.py:164-192) but
discarded before the function returns. Fix at the `build.py:502-507` call site (Stage
2a): don't feed `compute_exclude_newer` the full post-`select_roots` list. Instead,
re-derive (or have `select_roots` additionally expose) a **runtime-only** view —
concretely, either:

1. Add a small `roots.runtime_only_roots(repo_path, target_env=...)` helper that
   filters `evidence.declared_dependencies` to `kind == "dependency"` only (reusing
   `_manifest_root_token`/`_env_marker_excludes`, skipping `_in_test_scope` entirely
   since runtime is unconditional), and call `compute_exclude_newer` on *that* list at
   build.py:506-507; or
2. Thread `kind` through `select_roots`'s return shape (a 3-tuple
   `(import_id, dist_token, kind)`) and filter in `build.py` before calling
   `compute_exclude_newer`.

Option 1 is the smaller diff (no shape change to a function with several other callers
— see §5). Either way, the *principle* is: an exact pin only earns the right to anchor
the whole closure's resolve era when it's a genuine runtime/ABI-relevant pin (the
opencv-python/numpy case `pins.py`'s docstring describes), never an incidental
dev/lint/typing-tool version pin whose own release cadence is unrelated to the code
actually being tested.

*Why this generalizes*: pinned dev tooling (`black==`, `mypy==`, `ruff==`, `isort==`)
next to a modern floored runtime dependency is one of the most common patterns in
actively-maintained OSS Python repos (anything using `pre-commit` with pinned hook
versions). Today, **every** such repo silently risks the identical unsatisfiable-anchor
failure the moment any runtime dep's floor postdates the oldest pinned dev tool. This is
not a one-off; it's a systemic gap in a mechanism (`pins.py`) that was designed and
validated only against the single-purpose ABI-pin case.

*Risk*: low. `compute_exclude_newer` degrades to `None` (resolve latest, today's
already-safe default) whenever its input has no exact pins at all — restricting its
input to a smaller (runtime-only) root set only ever *removes* candidate pins, so the
failure mode of this change is "anchor slightly differently than today," never "crash"
or "silently drop packages."

### A.4 — (smaller, orthogonal) Recognize implicit-namespace local packages

Fix the one-level-deep limitation in both `scan.py:75-94`
(`_local_module_names`) and `import_graph.py:139-145`
(`_looks_like_python_module_dir`): a top-level directory with **no** `__init__.py` and
**no** direct `.py` file, but which contains *any* `.py` file **anywhere** beneath it
(recursive check, or PEP 420 namespace-package detection via `pkgutil`-style walk),
should still count as a local module name. This removes the `docs_src` residue log line
entirely (a real, if harmless, correctness bug) and is a one-function, easily unit-tested
change (walk `os.walk` already used in `scan.py:87`; extend the "add name" condition to
also fire the first time a `.py` file is found *anywhere* under that top segment, not
only immediately inside it).

*Why this generalizes*: PEP 420 implicit namespace packages (no `__init__.py` anywhere,
or only several levels down) are an increasingly common pattern for exactly this
use-case — bundling non-package example/tutorial trees that are imported by name but
never meant to be `pip install`-able (typer's own `docs_src`, FastAPI's analogous
`docs_src`, Django tutorial-style repos, etc.). Any repo with this pattern hits the same
"phantom unresolvable Import" residue today.

*Risk*: very low — this only ever **removes** false-positive Import nodes (tightens a
false-positive-prone filter), never adds new ones; strictly increases precision of
"local" classification with no plausible recall cost (a name that really is a PyPI
package would already have failed this local-name test, since a genuine local module
never simultaneously (a) has no top-level marker AND (b) is also independently
resolvable from PyPI under the exact same name — the two are not in tension).

Note this fix does **not** by itself change typer's apt over-prediction — it only
removes a cosmetic, unrelated log line. It's worth doing for correctness and to stop
future confusion about "what caused this," but A.1-A.3 are where the actual robustness
gain is.

---

## 4. Fix B — safe unknown-mode default (targets sub-cause b; the direct apt-collapse fix)

### The change

`build_deps.py:309`:
```python
if pkg.build_from_source is False:
    continue
```
→
```python
if pkg.build_from_source is not True:
    continue
```

One line. Requires a **positive, confirmed** "this package has no matching wheel and
must build from source" signal (`build_from_source is True`, set only by
`native_risk_from_lock`/`wheel_oracle.risk_from_packages` off a *real* parsed lock)
before `seed_build_deps` will call `build_dep_prior` → `debian_build_deps` for a
package. `None` ("we don't know") and `False` ("confirmed wheel") both now skip the
expensive, collision-prone Debian dump; only `True` triggers it.

`seed.py:76`'s generic `build-essential` floor is **deliberately left unchanged**
(`is not False`, i.e. still includes `None`) — its own docstring already argues, and
this analysis agrees, that a single harmless generic compiler-presence check is cheap
enough to keep applying under uncertainty. Only the *expensive, name-collision-prone*
prior (build_deps.py) needs the tightened, positive-signal gate. `emit.py:78`
(`_toolchain_ready`) needs no change: with the Debian dump suppressed, unknown-mode
packages simply have no TOOL edges to be gated on beyond the harmless build-essential
floor (which is already emitted, harmlessly, in every one of the diagnostic's 16 rows).

### Why this is safe (not just "smaller blast radius")

`wheel_oracle.risk_from_packages` (`wheel_oracle.py:52-99`) computes a definite boolean
for every package entry a real lock parse sees — it has **no code path that returns
`None`** for anything it actually classified (verified by reading the function: line 83
is an unconditional boolean expression, and every return path in the function's `for`
loop assigns a definite `True`/`False`). Cross-checked against the diagnostic's own
empirical data: **every one of the 15 clean repos has `unk=0`**; the *only* row with
`unk>0` is typer, and it's `unk=67` (100% of its packages) — exactly matching "the
lock-parse path never ran for this closure" (§1.2, a5), not "some packages were
ambiguous." In the current system, `build_from_source is None` is, empirically, a
100%-reliable proxy for "this round used the degraded fallback," never "we looked and
couldn't tell." That is exactly the situation the assignment describes as needing a
safe default, and it is why a blunt `is not True` gate is not a hack — it's gating on
the one bit of information (`True`) that's ever actually *earned* by real evidence.

### Risk / tradeoff

The honest cost: if a package's build mode is unknown **because the primary resolve
degraded**, `seed_build_deps` no longer *proactively* predicts its specific `-dev`
build-time need. Recovery becomes fully reactive: `install_closure`'s real build-error
parsing (stage 4, `build.py:26-27` design note; the same backstop `seed.py`'s own
docstring at lines 16-19 already documents as the existing recovery path for
"any specific -dev a prior doesn't predict") has to catch it when the actual pip build
fails. This is a real trade against this codebase's stated "front-load the complete
model, don't rely on reactive iteration" philosophy — worth naming explicitly rather
than glossing over. It's justified here because (a) the alternative has a *proven*
catastrophic failure mode (31 wrong apt packages, some of them plausibly breaking
`apt-get install` outright via unrelated GUI-app dependencies), while the reactive path
is an existing, already-relied-upon mechanism elsewhere in the design; and (b) Fix A
directly reduces *how often* this tradeoff is even exercised (fewer degraded resolves
→ fewer `None`s to begin with). Existing tests support the change: `test_build_deps.py`'s
own helper (`_pkg(name, version, build_from_source=True)`, line 32) already defaults to
`True` for every test that expects seeding to occur — there is no existing test
asserting seeding *should* happen for an explicit `build_from_source=None` package
(checked: the one `None` in that file, line 143, is `_pkg("psycopg2", None)`, i.e.
`version=None`, an unrelated "unresolved diagnostic package" case) — so this is a
deliberate behavior change with no hidden regression against the current suite, not an
accidental one.

**Defense-in-depth option (not required, worth flagging)**: if telemetry ever shows a
real "successfully-locked, but `_stamp`'s name lookup missed" case producing an
undeserved `None` inside an otherwise-healthy lock (e.g. a canonicalization mismatch
between the Package node's name and the lock's `name` field — `resolve_link.py:35-41`
already has a fallback loop for exactly this kind of miss, so it should be rare), a more
surgical alternative is to stamp an explicit `resolve_degraded: True` marker in
`Node.data` from `_pip_compile_fallback` (resolve.py:404-451) and gate
`seed_build_deps` on `build_from_source is not False and not
pkg.data.get("resolve_degraded")` instead of tightening the field's semantics globally.
This preserves today's proactive prediction for the narrow "successfully locked, but
this one node's risk lookup missed" case while still suppressing the "we never locked
anything real" cascade. Recommended only if evidence emerges that Option `is not True`
is too blunt in practice — today's data doesn't show that, so the one-line fix is the
right first move.

---

## 5. Ecosystem-agnostic seam vs Python-specific, and cross-backend generalization

All four Fix-A changes and the Fix-B change live entirely inside `python_deps/`, but
they sit at, or below, the layer every Python dependency-group *backend* (uv-native
`pyproject.toml`, Poetry, PDM, pip-tools) already normalizes through before reaching
`resolve.py`:

```
poetry [tool.poetry.dependencies] ┐
pdm [tool.pdm.dev-dependencies]   ├─► evidence.py: PythonDependencyEvidence            ┐
requirements*.txt (any backend)   ┤     (declared_dependencies: name, kind, source)    │
PEP 621 / PEP 735 / setup.cfg     ┘                                                    │
                                                                                        ▼
                                                                    roots.py: select_roots()
                                                                    (kind-gated root list)
                                                                                        │
                                                                                        ▼
                                                                    pins.py / resolve.py
                                                                    (uv throwaway-project
                                                                     resolve — TODAY'S
                                                                     universal Python
                                                                     resolve path,
                                                                     regardless of the
                                                                     repo's OWN backend)
```

Because `resolve.py` today **always** re-derives a fresh `uv lock` from scratch
regardless of which build backend a repo declares (confirmed: `resolve_closure` never
reads a committed `poetry.lock`/`pdm.lock`; it only ever writes a *throwaway*
`pyproject.toml` and shells out to `uv lock` — resolve.py:150-183, 217-333), fixes A.1
(error-regex), A.2 (requirements-file role default), and A.3 (era-anchor scoping) apply
uniformly to *every* Python backend's repos the moment their manifests are normalized
into `PythonDependencyEvidence` — which is already true for PEP 621/PEP 735/setup.cfg/
requirements-files today. The one gap: **Poetry's own `[tool.poetry.group.<name>.
dependencies]` syntax and PDM's `[tool.pdm.dev-dependencies]` table have no parser in
`evidence.py` today** (checked: `evidence.py` handles `tool.poetry.dependencies` at
line 87 and the PEP 735 `[dependency-groups]` reader at lines 110-138, but nothing
matches `tool.poetry.group.*` or `tool.pdm.dev-dependencies`) — so a Poetry/PDM repo
using its *native* dev-group syntax (rather than PEP 735 or a `requirements-dev.txt`)
would have those groups silently **absent from roots entirely** today. That's a
separate, recall-oriented gap (worth a small follow-up: two more evidence.py readers,
tagged `kind="dev_group"`, mirroring the existing PEP 735 reader) — flagging it here
because it's the natural next step for "generalizes across uv/poetry/pip-tools/pdm,"
but it's prerequisite work, not part of this fix's diff.

**A complementary, larger-scope lever already anticipated by the architecture**:
`src/ecosystems/base.py:18-24` defines `ClosureMode.LOCK` ("committed lockfile present
-> parse offline. Preferred.") vs `ClosureMode.RESOLVE` ("no lock -> run the resolver...
Python is RESOLVE" — base.py's own comment). Today Python is *hardcoded* to always
RESOLVE, even when a repo ships its own `poetry.lock`/`pdm.lock`/`uv.lock` — i.e. the
pipeline throws away the repo's own already-successful, already-conflict-free,
already-correctly-scoped resolution (solved by the repo's own tooling, at the repo's own
chosen era, using the repo's own dependency-group selection) and re-derives one from
scratch through a generic uv lock that doesn't know about any of those choices. Wiring
`PythonProvider.closure_mode_for` (referenced in `build.py`'s provider-dispatch,
lines 696-712) to prefer `ClosureMode.LOCK` when a lock file is present and current would
sidestep the entire A.1-A.3 failure class for any repo that ships a lock — including
typer, if it shipped a `uv.lock` (it doesn't, since it uses PDM without committing a
lock in this snapshot). This is a bigger, Slice-level architectural change (not a small
patch) and is flagged here as the natural long-term home for this generalization, not as
part of the R2 fix itself.

**Non-Python ecosystems (Rust/Node, per `EcosystemProvider`)**: none of A.1-A.4/B is
Python-specific in *principle* — "an incidental pinned dev-tool anchors an unrelated
resolve," "an unattributable resolver-error message causes a silent fallback that loses
a signal field," and "unknown build-mode should never be treated as a positive signal"
are ecosystem-agnostic *problem shapes*. But today they are implemented entirely inside
`python_deps/` (uv-specific stderr regexes, PyPI-specific upload-date lookups). The
right generalization is architectural, not code-sharing: when Rust/Node providers land,
each should independently define (1) its own resolver-error taxonomy (Cargo/npm have
completely different unsat-core phrasings), and (2) its own equivalent of the Fix-B
"only a *positive* build-required signal should trigger the expensive system-dependency
prior" rule at whatever its analogous `seed_build_deps`-equivalent stage is. The
*rule*, not the regex table, is what should be documented as a cross-ecosystem
principle (candidate home: a short note in `src/ecosystems/base.py`'s docstring next to
`CertifyMode`/`ClosureMode`).

---

## 6. How the eval verifies this (make typer collapse without regressing the other 15 + psycopg2/lxml)

Concrete, layered verification — reusing the exact harness that produced
`DIAGNOSTIC.md` plus new narrow unit tests:

1. **Unit regression, `test_build_deps.py`**: add a case with an explicit
   `_pkg("somepkg", "1.0", build_from_source=None)` and assert `seed_build_deps` adds
   **zero** capability/aptdep nodes and **zero** edges from it (today's implicit
   behavior, once Fix B lands) — while a `build_from_source=True` package (already the
   file's default helper value) still gets the full existing treatment. This directly
   locks in Fix B without needing a container.

2. **Unit regression, `test_pins.py`**: add a case mirroring the reproduced scenario —
   roots containing both a `kind="dev_group"` exact pin (`ruff==0.2.0`) and a
   `kind="dependency"` floor (`httpx>=0.27.0`) with injected `fetch` dates matching the
   real ones captured in §1.2 — assert that after Fix A.3, `compute_exclude_newer`
   (called on the runtime-only view) returns `None` or a date that does **not** exclude
   `httpx==0.27.0`, unlike today.

3. **Unit regression, `test_resolve_errors.py`** (new or extended): hardcode the exact
   captured stderr from §1.2 as a fixture string; assert `parse_resolver_error` now
   populates `diag.missing` with `httpx`, and `_offending_root_names` returns
   `{"httpx"}` — locks in Fix A.1 without any network or `uv` invocation.

4. **Unit regression, `evidence.py` role test**: assert
   `_requirements_role(root, root/"requirements-github-actions.txt")` no longer returns
   `"runtime"` after Fix A.2 (exact filename taken from the real typer fixture already
   checked out at
   `outputs/build_script_eval/_smoke/typer/requirements-github-actions.txt`).

5. **Integration regression (construction-level, no container needed for the resolve
   half)**: re-run `resolve_closure` against typer's *real* declared+dev/test root set
   (the exact list reconstructed in this doc's §1.2 reproduction, or better, driven
   through the real `select_roots(repo_path=".../smoke/typer", ...)` against the
   already-checked-out clone at
   `outputs/build_script_eval/_smoke/typer`) with the host `uv` executor for real;
   assert the returned Package nodes carry `build_from_source in {True, False}` for
   every entry (zero `None`) — this is the end-to-end proof Fix A actually prevents the
   fallback for typer specifically, using the real repo already sitting on disk, no
   network mocking needed beyond what `uv lock`/PyPI already require.

6. **Full-corpus regression (the DIAGNOSTIC.md harness itself)**: re-run the exact
   16-repo construction+render sweep. Expected deltas:
   - **typer**: `unk` 67→0 (Fix A) or, even if some future unattributable uv error
     still slips through Fix A.1's net, `pred/emit apt` 31→~1 regardless (Fix B is a
     safety net independent of Fix A's completeness) — i.e. run the sweep with **only**
     Fix B first to prove the apt-collapse claim in isolation, then with A+B to confirm
     `unk`→0 too.
   - **The 13 other unk=0 rows** (click, flask, jinja, rich, python-dotenv, pyyaml,
     pyzmq, pillow, cryptography, pygraphviz, python-semantic-release, and requests/
     httpx's wheel-only majority): byte-identical apt predictions — Fix B's gate
     (`is not True`) is a no-op wherever `build_from_source` was already definite, and
     Fix A's changes (requirements-role default, era-anchor scoping, error regex) never
     fire because none of these repos has the triggering pattern (checked: none of the
     15 has a root-level `requirements-<suffix>.txt` beyond a plain `requirements.txt`,
     nor a resolve that ever reaches the fallback path today).
   - **psycopg2 and lxml** (the genuinely-source-built control cases, `src=1` each):
     unregressed. Their one native package has `build_from_source=True` from a real
     lock parse (unk=0 for both already) — Fix B's tightened gate is `is not True`,
     which still **includes** `True`, so `seed_build_deps` still runs
     `build_dep_prior`/`debian_build_deps` for them exactly as today (psycopg2 keeps
     predicting `libpq-dev`; lxml keeps its existing — separately R1-scoped —
     under-prediction of `libxml2-dev`/`libxslt1-dev`, unaffected by this fix either
     way). This is the key regression guardrail: Fix B changes behavior **only** for
     packages whose build mode was never actually determined, which today is uniquely
     typer's 67 packages.

This layered plan lets Fix B ship and be verified (steps 1 + 6-partial) **immediately**,
independent of Fix A's three sub-changes, which can land and be verified incrementally
(steps 2-3-4 unit-level, step 5 integration-level, step 6-full as the final gate).
