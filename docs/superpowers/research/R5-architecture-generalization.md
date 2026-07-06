# R5 — Architecture / Generalization

Design for keeping the R1–R4 root-cause fixes ecosystem-agnostic, and for turning
the build-script eval into a continuous generalization guardrail. Cites exact
file:line locations; proposes interface changes, not implementation code.

## 0. What exists today (verified)

- `src/ecosystems/base.py:37-74` — `EcosystemProvider` Protocol has exactly 4
  members: `detect`, `closure_mode_for`, `package_obligations` (Phase 1),
  `native_obligations` (Phase 2). No hook for native build-deps, runtime tools,
  base-image selection, or test/probe commands.
- `src/ecosystems/registry.py:43-45` — `PROVIDERS = (PythonProvider(),)`. Only
  one provider registered; `select_provider` (`registry.py:10-37`) already
  supports N providers + a `default` fallback, so the dispatch machinery itself
  needs no change to add Node/Go/Rust later.
- `src/ecosystems/python/provider.py:26-74` — `PythonProvider` is a thin
  pass-through to `build.py`'s `_python_package_obligations` /
  `_python_native_obligations`. It owns **zero** of the R1–R4 logic directly —
  that logic lives one layer down, in `python_deps/depgraph/*.py`, which
  `build.py` calls unconditionally regardless of provider.
- **All R1–R4 fix code currently lives inside `src/python_deps/depgraph/`**
  (`resolve.py`, `build_deps.py`, `debian_builddeps.py`, `pep725.py`,
  `wheel_preflight.py`, `roots.py`, `subprocess_scan.py`, `ldd_probe.py`,
  `os_resolver.py`) — i.e. inside the Python provider's *implementation*, not
  behind the seam. Nothing here is reachable by a future Node/Rust provider
  today; a naive fix would hardcode the repair inside these files and never
  generalize. That is the risk R5 exists to head off.
- Of that set, three modules are **already ecosystem-agnostic by design** and
  should stay shared, unchanged:
  - `os_resolver.py:1-9` — docstring says it outright: *"Debian/Ubuntu (apt)
    only in V1; the interface is backend-neutral."* `ObservedNeed` (kind:
    soname/header/binary/pkgconfig, context: build/runtime) is already a
    capability schema with no Python coupling.
  - `ldd_probe.py:124-214` — walks `NodeType.PACKAGE` nodes generically via
    `ObservedNeed`/`resolve()`; the *only* Python-specific line is the so-file
    enumeration command itself (`EXT_SO_MAP_CMD`, `ldd_probe.py:66-71`, a
    `python -c "import importlib.metadata..."` one-liner). The reconcile/graph
    logic around it is agnostic.
  - `tables.py:27` (`CLI_TOOL_TO_APT`) and `build_deps.py:53`
    (`PACKAGE_TO_BUILD_NEEDS`) — flat `name -> apt package` / `name ->
    ObservedNeed` tables. Ecosystem-agnostic *format*; ecosystem-specific
    *keys* (PyPI names). This distinction (agnostic mechanism, per-ecosystem
    data) is the pattern to replicate for every R1–R4 fix.
- `src/eval/language_package_eval/coverage.py:88-101` (`base_image_for_repo`)
  and `src/envstate/runtime_base.py:151-169` (`resolve_runtime_base`) are
  **hard Python**: `.python-version` parsing, `requires-python` PEP 621
  parsing, `python:X.Y-slim` tag rewriting. Called directly by
  `scorecard.score_repo` (`scorecard.py:175`) with no provider indirection.
- `src/eval/build_script_eval/replay.py:64,74,76,105` hardcodes
  `python3 -c 'import {top_import}'`, `pip install pytest`,
  `python3 -m pytest --collect-only -q`, `python3 -m pytest -q`.
  `scorecard.py:39-54` (`classify_pytest_result`) hardcodes pytest's exit-code
  semantics (0/1/2-4/5). `coverage.py:524-542`
  (`_ConstructionOnlyExecutor.run`) hardcodes the string match
  `"-m pytest" in command or command.startswith("pytest")` to intercept the
  TEST goal node's real run. `coverage.py:371-389`
  (`top_level_import_name`) is Python-package-directory-shaped (looks for
  `__init__.py`). None of this is reachable through `EcosystemProvider` today.

## 1. Revised `EcosystemProvider` interface

Add methods in three groups. Each is a **thin delegation point**: the Python
implementation calls the exact existing function, so behavior is byte-identical
on migration (see §4).

```python
class EcosystemProvider(Protocol):
    name: str
    certify_mode: CertifyMode

    # --- existing, unchanged ---
    def detect(self, repo: str) -> float: ...
    def closure_mode_for(self, repo: str) -> ClosureMode: ...
    def package_obligations(...) -> tuple[DepGraph, list, object, str | None]: ...
    def native_obligations(self, graph: DepGraph, container_executor: object) -> DepGraph: ...

    # --- NEW: R1 — root project's OWN native build requirements ---
    def own_native_build_needs(
        self, repo: str, container_executor: object,
    ) -> tuple[ObservedNeed, ...]:
        """Is the REPO ITSELF (not a dependency) a native extension needing
        build tools/headers? (ext_modules in setup.py/pyproject, Cargo cdylib,
        node-gyp binding.gyp). Feeds the SAME shared capability->TOOL/SystemLib
        node pipeline dependencies already use (build_deps.py's
        _capability_node/_apt_build_node, build_deps.py:231-283) — today that
        pipeline is only ever fed from per-DEPENDENCY needs; this is the
        missing per-PROJECT feed. Python impl: parse the repo's own
        setup.py/pyproject build-backend + PEP 725 externals
        (pep725.py:289-309) exactly as build_dep_prior does for a dependency,
        just pointed at repo root instead of an installed dist."""
        ...

    # --- NEW: R3 — Debian/system-name-mapping inputs, kept OUT of the
    #     shared apt-resolution machinery (debian_builddeps.py / os_resolver.py
    #     stay ecosystem-generic) ---
    def registry_source_candidates(self, registry_name: str) -> tuple[str, ...]:
        """Ordered Debian-source-name guesses for one registry package name.
        Python impl = today's debian_builddeps.source_candidates
        (debian_builddeps.py:154-179, the 'python3-x'/'x' prefix rules). A
        Node provider supplies 'node-x'-style guesses instead; Rust 'rust-x'."""
        ...

    def own_runtime_markers(self) -> tuple[str, ...]:
        """Substrings marking a Debian source as producing THIS ecosystem's
        own runtime — the collision guard. Python impl = the hardcoded
        'python3-' prefix check in debian_builddeps._builds_python3_binary
        (debian_builddeps.py:81-91) and is_python_source_stanza (:94-96),
        generalized from a literal to a provider-supplied tuple. Node:
        ('nodejs',); Rust: () (Cargo crates aren't Debian-source-mapped the
        same way, so this collision guard is a no-op for Rust — an empty
        tuple is a legal, honest answer, not a stub)."""
        ...

    # --- NEW: R4 — runtime tools reached THROUGH a dependency ---
    def runtime_tool_priors(self) -> Mapping[str, tuple[ObservedNeed, ...]]:
        """registry-name -> curated ObservedNeed(kind='binary', context=
        'runtime') tools a KNOWN dependency shells out to (GitPython -> git).
        Table-driven (same shape as build_deps.PACKAGE_TO_BUILD_NEEDS,
        build_deps.py:53), NOT source-scanned — this is what actually fixes
        R4's false-positive half: cryptography/pyzmq falsely predicted `git`
        because the current detector's signal source is broader than 'this
        repo's own source calls subprocess'; a curated per-DEPENDENCY table
        only fires for deps actually known to shell out, eliminating incidental
        false hits from scanning vendored/unrelated code."""
        ...

    def own_source_tool_needs(self, repo: str) -> tuple[ObservedNeed, ...]:
        """Scan the REPO'S OWN source for known-tool subprocess calls.
        Inherently per-language parsing (AST for Python, ts-morph/babel for
        Node, go/ast for Go) so this stays provider-specific machinery, not
        shared. Python impl = subprocess_scan.scan_subprocess_tools
        (subprocess_scan.py:95-118), which already delegates the
        tool->apt-package table to CLI_TOOL_TO_APT (tables.py:27) — that table
        itself is ecosystem-agnostic content (git is git) and can be reused
        verbatim by other providers rather than reinvented."""
        ...

    # --- NEW: R5b — parameterize the eval's replay ladder (§2) ---
    def base_image_for(self, repo: str) -> tuple[str, str, str]:
        """(image, toolchain_version, reason). Python impl = today's
        coverage.base_image_for_repo (coverage.py:88-101), itself a wrapper
        over envstate.runtime_base.resolve_runtime_base. Node: pick a
        node:X-slim tag off package.json 'engines.node'; Go: golang:X off
        go.mod's 'go X.Y' directive; Rust: rust:X off rust-toolchain.toml."""
        ...

    def primary_entry_symbol(self, repo: str) -> str | None:
        """Best-effort top-level importable/requireable unit for the
        env-works smoke probe. Python impl = coverage.top_level_import_name
        (coverage.py:371-389)."""
        ...

    def env_probe_commands(self, entry_symbol: str | None) -> tuple[str, ...]:
        """Shell commands proving the env 'works' post-install, run before the
        test rung. Python impl returns exactly one command:
        `python3 -c 'import {entry_symbol}'` (replay.py:64) when
        entry_symbol is not None, else ()."""
        ...

    def test_bootstrap_command(self) -> str | None:
        """Installs the test RUNNER itself (not a repo dependency). Python:
        'pip install --no-input --quiet pytest' (replay.py:74). None when the
        runner ships with the toolchain (go test) — no bootstrap rung needed."""
        ...

    def test_collect_command(self) -> str:
        """Dry-run/collection-only command. Python:
        'python3 -m pytest --collect-only -q' (replay.py:76)."""
        ...

    def test_run_command(self) -> str:
        """Full suite run. Python:
        f"{_PYTEST_ENV} python3 -m pytest -q" (replay.py:105)."""
        ...

    def is_test_run_command(self, command: str) -> bool:
        """True if `command` IS this ecosystem's own test-suite invocation —
        used by the construction-only executor to intercept the TEST goal
        node's real run without ever executing it during graph construction.
        Python impl = the exact predicate already inlined at
        coverage.py:536 ('-m pytest' in command or command.strip().startswith
        ('pytest')), lifted out so it isn't hardcoded in _ConstructionOnlyExecutor."""
        ...

    def classify_test_result(self, returncode: int) -> tuple[bool, bool, str | None]:
        """(tests_ran, tests_passed, reason) from the test-run exit code —
        exit-code semantics are test-runner-specific (pytest's 0/1/2-4/5
        differs from `go test`'s 0/1 or `cargo test`'s 0/101). Python impl =
        scorecard.classify_pytest_result verbatim (scorecard.py:39-54)."""
        ...
```

### Where the R1–R4 *mechanism* stays shared (not duplicated per provider)

The provider methods above are all **data/detection** hooks (what needs what,
named how). The actual apt resolution, node construction, and graph wiring stay
as shared, already-agnostic machinery that every provider's answers flow
through unchanged:

- `os_resolver.resolve()` (`os_resolver.py`) — capability -> apt candidate.
  Already backend-labeled as future-multi-backend; no change needed beyond
  consuming `ObservedNeed`s from more sources.
- `build_deps.py`'s node builders (`_capability_node` :231-255,
  `_apt_build_node` :258-283) and the seed loop machinery — generalize their
  *entry point* from "one dependency's `ObservedNeed`s" to "any
  `ObservedNeed` sequence, tagged with an owner id" so `own_native_build_needs`
  (repo-level) and `runtime_tool_priors` (per-dependency) can both feed it
  without a second copy of the node-building logic. Concretely: factor
  `seed_build_deps`'s per-package body (`build_deps.py:296-335`, not shown
  above but is the loop that calls `build_dep_prior` then emits capability +
  apt nodes) into a `seed_capability_needs(graph, owner_id, needs, apt_directives,
  executor)` helper reusable by both the existing per-dependency call site and
  the new project-level / runtime-tool call sites.
- `debian_build_deps()` / `_resolve_source()` (`debian_builddeps.py:221-264`)
  — the apt-cache/Contents-walk mechanism stays shared; only its two
  Python-hardcoded inputs (`source_candidates`, `_builds_python3_binary`)
  become provider-supplied (`registry_source_candidates`,
  `own_runtime_markers`) per above.
- `CLI_TOOL_TO_APT` (`tables.py:27`) is reused as-is by every provider's
  `own_source_tool_needs` — it is tool-name -> apt-package, which is already
  language-neutral content.

### R2's fix is a shared-machinery bug fix, not a provider hook

R2's "unsafe unknown default" lives at `build_deps.py:286-309`
(`seed_build_deps`): the docstring says *"For each source-built Package
(`build_from_source` not False...)"* and the only explicit skip is
`pkg.build_from_source is False` (`build_deps.py:309`) — so `None` (unresolved
build-mode) is treated identically to `True` (confirmed source-built) and
falls through to the full `debian_build_deps()` dump. This is graph-node-driven
code with zero Python string-literals in the gating logic itself — it is
**already ecosystem-agnostic** (any provider's Package nodes carry the same
tri-state `build_from_source`). Fix it once, in place: change the gate so
`None` gets only the generic `build-essential`+`pkgconf` floor (never the
`debian_build_deps()` lookup, which assumes a *confirmed* source build), and
`True` alone gets the full prior. No interface change — every future provider
inherits the safe default for free. (The *other* half of R2 — why typer's
resolution left 67 packages at `build_from_source=None` in the first place —
is a `uv`/PEP-508-marker resolution-oscillation bug specific to
`resolve.py`/`roots.py`'s Phase-A fixpoint, and stays inside `PythonProvider`;
it has no cross-ecosystem generalization claim beyond "resolvers must not
leave residue," which is a testing discipline, not an interface.)

## 2. Parameterizing the eval's replay ladder

Two call sites break the provider seam today and must be re-routed through it:

**`scorecard.score_repo` (`scorecard.py:164-186`)**
```
image, minor, _reason = base_image_for_repo(repo_dir)          # -> provider.base_image_for(repo_dir)
graph = build_graph_construction_only(repo_dir, image, minor)  # already provider-dispatched INSIDE build_dep_graph;
                                                                 # only needs is_test_run_command threaded to
                                                                 # _ConstructionOnlyExecutor instead of the
                                                                 # hardcoded pytest string match (coverage.py:536)
top_import = spec.top_import or top_level_import_name(repo_dir) # -> provider.primary_entry_symbol(repo_dir)
ladder = run_replay_ladder(repo_dir, image, script, top_import, ...)  # -> pass a ReplayProfile (below), not top_import
```

**`replay.run_replay_ladder` (`replay.py:45-116`)** — replace the bare
`top_import: str | None` parameter with a small frozen `ReplayProfile` built
once per repo from the selected provider:

```python
@dataclass(frozen=True)
class ReplayProfile:
    entry_symbol: str | None
    env_probe_commands: tuple[str, ...]
    test_bootstrap_command: str | None
    test_collect_command: str
    test_run_command: str
    classify_test_result: Callable[[int], tuple[bool, bool, str | None]]

def replay_profile_for(provider: EcosystemProvider, repo_dir: str, override: str | None) -> ReplayProfile:
    entry = override or provider.primary_entry_symbol(repo_dir)
    return ReplayProfile(
        entry_symbol=entry,
        env_probe_commands=provider.env_probe_commands(entry),
        test_bootstrap_command=provider.test_bootstrap_command(),
        test_collect_command=provider.test_collect_command(),
        test_run_command=provider.test_run_command(),
        classify_test_result=provider.classify_test_result,
    )
```

`run_replay_ladder` then runs `profile.env_probe_commands` in place of the
single `python3 -c import` line, bootstraps via
`profile.test_bootstrap_command` (skips the rung entirely if `None`), collects
via `profile.test_collect_command`, runs via `profile.test_run_command`, and
classifies via `profile.classify_test_result(run.returncode)` instead of the
free function `classify_pytest_result`. A `ReplayProfile` (not the whole
`EcosystemProvider`) crosses into `replay.py` deliberately — it keeps
`replay.py` decoupled from `ecosystems/` (avoids a new import-cycle risk
between `eval/` and `ecosystems/`) and makes the ladder's dependency on the
provider explicit and minimal: 6 fields, no repo-object, no executor.

`coverage._ConstructionOnlyExecutor.run` (`coverage.py:534-542`) takes the
same `provider.is_test_run_command` predicate instead of the inlined
`"-m pytest" in command or command.startswith("pytest")` check.

**Zero-impact for Python**: `PythonProvider`'s implementations of all 6
`ReplayProfile`-feeding methods return exactly today's literals
(`replay.py:64,74,76,105`, `scorecard.py:39-54`, `coverage.py:536`) — same
strings, same function object for `classify_test_result`
(`= staticmethod(classify_pytest_result)`, no reimplementation). The ladder's
observable behavior (commands run, exit-code interpretation) is byte-identical
before and after; a Node/Go provider only needs to exist and answer these 6
methods (plus `base_image_for` / `primary_entry_symbol`) for the *same* harness
(`replay.py`, `scorecard.py`) to run against it — no new eval module, no
ladder rewrite.

## 3. Eval-as-guardrail: stratified corpus + regression gate

**Extend, don't replace, `corpus.py`.** Today `STRATA = {"S_control",
"S_syslib"}` (`corpus.py:10`) is a closed frozenset gating `select()`
(`corpus.py:46-57`) that raises on an unknown stratum — deliberately strict,
which is the right shape for a controlled gate; it just needs more members.
Add one stratum per repo-type in the diagnostic's taxonomy (§ DIAGNOSTIC.md
line 52-53), each mapped to the R1–R4 concern it exercises:

| stratum | exercises | example repo |
|---|---|---|
| `S_control` (existing) | over-prediction floor (should emit ~0 apt) | click, flask |
| `S_syslib` (existing) | R1/R3: source-built native deps' apt precision | psycopg2, lxml |
| `S_cext_no_wheel` | R1: repo's OWN native build (no wheel, must compile) | pygraphviz |
| `S_rust_ext` | R1 generalized to a different compiler toolchain | cryptography, pydantic-core |
| `S_sdist_only` | R1/R2: build_from_source always True, no wheel branch | — |
| `S_depgroup_heavy` | R2: resolver must not oscillate under many extras/groups | typer |
| `S_src_layout` | root-manifest discovery (`_project_build_manifest`/`primary_entry_symbol`) under `src/` | — |
| `S_monorepo` | multi-package root selection | — |
| `S_backend_variants` | uv / poetry / pdm / flit / setuptools backend parity | — |
| `S_runtime_tool` | R4: tool reached through a dependency | python-semantic-release (git) |
| `S_service_deferred` | explicitly excluded from headline (marks scope, not a gap) | — |

Each `RepoSpec` already carries `stratum`, `feasible`, `top_import`,
`network_in_tests` (`corpus.py:13-22`) — no schema change needed, just more
rows plus widening `STRATA`. (When Node/Go providers land, the SAME
`RepoSpec`/`CORPUS` shape hosts their repos too — `stratum` is a string, not a
Python-specific enum, and `top_import` becomes `entry_symbol_override`-shaped
via the new `primary_entry_symbol` seam, so the corpus format itself doesn't
need an ecosystem field as long as `full_name`/`git_url` dispatch through
`select_provider` normally.)

**The gate, mechanically**: run `score_repo` over the *whole* corpus, commit
the resulting scorecards (`env_works`, `attribution`, `predicted_apt`,
`highest_rung` per repo — the exact dict `_assemble_scorecard` already
produces, `scorecard.py:130-161`) as a JSON snapshot per repo (e.g.
`docs/eval/build_script_eval/snapshots/<repo>.json`). A fix's CI check is then
a **diff of that snapshot, not a re-run of pass/fail**:

1. **Moves N** — the repos in the target stratum whose `attribution` was
   `system_gap`/`render_bug`/`infeasible` before must now read `pass` (or at
   minimum `highest_rung` must strictly advance), proving the fix actually
   closes the diagnosed gap rather than just changing its label.
2. **Regresses 0** — every repo OUTSIDE the target stratum must have an
   *identical* scorecard (same `attribution`, same `predicted_apt` set, same
   `highest_rung`) to the last-committed snapshot. Any diff there is a
   regression, full stop — this is what catches an R1 fix that starts
   over-predicting apt on `S_control` repos, or an R3 collision-guard change
   that starts under-predicting on `S_syslib`.
3. Both checks run over `missing_node_clusters` (`coverage.py:412-432`) too,
   pooled — a genuine fix should shrink or eliminate a named cluster, not
   just move its count between repos.

This makes "generalizes, doesn't overfit" a mechanical, non-negotiable CI gate
rather than a judgment call: a PR that fixes `pygraphviz` (`S_cext_no_wheel`)
by special-casing its name would show 0 in `S_cext_no_wheel`'s Moves-N (only
one repo, no generalization signal) and likely 0 regressions — a strong hint
the corpus needs a *second* `S_cext_no_wheel` repo before the fix can be
trusted; whereas a PR that fixes the `own_native_build_needs` PATH (not the
name) should move every repo in that stratum simultaneously. **Corpus growth
itself is part of the guardrail**: each stratum should carry ≥2 repos before a
fix targeting it is considered proven, specifically to prevent a plausible
one-repo overfit from reading as "generalized."

## 4. Migration path summary (Python stays byte-identical)

1. Add the new `EcosystemProvider` Protocol members (§1) — pure interface
   addition, no behavior change to anything (Protocols are structural; adding
   members doesn't break `PythonProvider` until it's asked to implement them).
2. Implement each new `PythonProvider` method as a **direct call-through** to
   the existing free function/literal it names above — no logic rewritten, only
   relocated behind a method. Any existing unit test asserting on
   `top_level_import_name`, `base_image_for_repo`, `classify_pytest_result`,
   `source_candidates`, `_builds_python3_binary`, `scan_subprocess_tools`
   keeps passing unmodified (those functions still exist and still do exactly
   what they did; the provider method just forwards to them).
3. Re-point the 5 call sites (`scorecard.score_repo`, `replay.run_replay_ladder`,
   `coverage._ConstructionOnlyExecutor.run`, plus the two R1/R3/R4 fix
   landing sites once they're written) at the provider methods instead of the
   free functions/literals. Run the full 16-repo diagnostic sweep before/after
   this rewire and diff every scorecard field — it must be the empty diff,
   which IS the zero-impact proof (same pattern as the `research_zero_impact.md`
   check already referenced in `python/provider.py`'s docstring and the
   memory note on Slice-1's "byte-identical 4 ways" verification).
4. Land R1/R3/R4's actual detection logic as NEW code behind
   `own_native_build_needs` / `registry_source_candidates` /
   `own_runtime_markers` / `runtime_tool_priors` / `own_source_tool_needs`,
   wired through the shared `seed_capability_needs` helper (§1). Land R2's fix
   in place in `seed_build_deps` (shared, no interface change).
5. Only when a Node/Rust/Go provider is registered in `PROVIDERS`
   (`registry.py:45`) does any of this get exercised by a second
   implementation — until then, `select_provider`'s `default=PROVIDERS[0]`
   behavior (`registry.py:33-36`) is unchanged and every repo still dispatches
   to `PythonProvider`.
