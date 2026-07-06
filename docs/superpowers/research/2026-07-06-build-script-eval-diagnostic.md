# Build-Script Eval — 16-repo Diagnostic (2026-07-06)

Measured by a construction+render sweep (no replay): for each repo, the graph-predicted
apt (`apt_names_in_graph`) vs the EMITTED apt actually in `setup.sh`, plus package
build-mode counts (`build_from_source`: src=True / wheel=False / unk=None). Repos built
via `build_dep_graph` construction-only (eval mode, PythonProvider). `emit == pred` in
every row, so the graph's apt IS what the build script installs.

| repo | stratum | pred/emit apt | src | wheel | unk | emitted apt | verdict |
|---|---|---|---|---|---|---|---|
| click | control | 0 | 0 | 63 | 0 | — | ✓ clean |
| flask | control | 0 | 0 | 71 | 0 | — | ✓ |
| jinja | control | 0 | 0 | 63 | 0 | — | ✓ |
| rich | control | 0 | 0 | 52 | 0 | — | ✓ |
| python-dotenv | control | 0 | 0 | 47 | 0 | — | ✓ |
| requests | control | 2 | 1 | 59 | 0 | build-essential, pkgconf | ~ (1 src-built dep; generic floor only) |
| httpx | control | 3 | 1 | 82 | 0 | black, build-essential, pkgconf | ~ (`black` as apt is odd) |
| pyyaml | syslib | 0 | 0 | 6 | 0 | — | ✓ (manylinux wheel bundles libyaml) |
| pyzmq | syslib | 1 | 0 | 69 | 0 | git | ✗ false `git` (all wheels, no build) |
| pillow | syslib | 0 | 0 | 9 | 0 | — | ✓ (wheel bundles libjpeg) |
| cryptography | syslib | 1 | 0 | 67 | 0 | git | ✗ false `git` (all wheels) |
| psycopg2 | syslib | 4 | 1 | 1 | 0 | build-essential, libpq-dev, pkgconf, postgresql | ✓ libpq-dev right; `postgresql` over-include |
| lxml | syslib | 2 | 1 | 5 | 0 | build-essential, pkgconf | ✗ UNDER: missing libxml2-dev/libxslt1-dev |
| pygraphviz | syslib | 0 | 0 | 49 | 0 | — | ✗ UNDER: missing build-essential + graphviz-dev (→ gcc fail at build) |
| python-semantic-release | control | 0 | 0 | 32 | 0 | — | ✗ missing runtime `git` (GitPython) |
| typer | control | 31 | 0 | 0 | **67** | build-essential, cargo, dbus-test-runner, valac, libgee-0.8-dev, … | ✗ OVER: resolution oscillated → all-unknown → apt dump |

Earlier full-replay run (5 repos) established env_works: typer ✓, psycopg2 ✓ (env), pygraphviz ✗ (gcc), lxml ✗ (build), semantic-release ✗ (git); tests_passed uniformly False (service confound).

## Root-cause hypotheses (to be investigated + designed by the research agents)

**R1 — Root-project & source-build NATIVE requirements are not modeled (the biggest systemic gap).**
pygraphviz IS a C-extension with no linux wheel → must compile → needs `build-essential` + `graphviz-dev`; the graph predicted `[]`. lxml (source-built here) got only the generic `build-essential`/`pkgconf` floor, missing the specific `libxml2-dev`/`libxslt1-dev`. The pipeline models *dependencies'* build-deps but not the **repo-under-test's OWN** native build requirements, and specific `-dev` prediction under-fires for source-built packages. Generalizes to every native-extension repo type (C/Cython/C++, and — via the seam — Rust/CGo later).
Code: `resolve.py` (native_risk_from_lock / build_from_source stamp), `build_deps.py` (seed_build_deps, the build-essential floor), `debian_builddeps.py`, `pep725.py`, `wheel_preflight.py`, and how the PROJECT/root node's own build is (not) modeled.

**R2 — Build-mode classification & resolution robustness (the over-prediction cause).**
typer alone over-predicted because its resolution oscillated (`phase-A stopped … residue ['docs_src']`) leaving all 67 packages `build_from_source=None`; the build-dep prior then treats unknown as source-built and dumps every package's Debian Build-Depends. Two sub-causes: (a) why does resolution fail/oscillate for typer (large dep-groups: docs/dev/test) when 15 others succeed? (b) the **unsafe unknown-mode default** — unknown should degrade safely, not cascade into an apt dump. Generalizes to any repo whose resolution is incomplete.
Code: `resolve.py`, `roots.py`, Phase-A fixpoint in `build.py`, `wheel_oracle`, `seed_build_deps` gating on build_from_source.

**R3 — Debian-source mapping precision (collisions + over-include), only visible when the build-dep prior runs.**
typer's `click`(pip)→Debian `click` (the Ubuntu Click package manager) dragged in valac/libgee/dbus-test-runner; `postgresql` (server) over-included for psycopg2. The B.1 `python3-*` source-validation guard is insufficient (Debian sources that happen to ship a python3 binary still pass). Design a collision-resistant package→system-build-dep resolution (or move off Debian-source-name mapping toward PyPI build backends / PEP 725 externals).
Code: `debian_builddeps.py` (_resolve_source, is_system_lib, _builds_python3_binary), `build_deps.py`, `os_resolver.py`.

**R4 — Runtime-need detection (tools/services reached THROUGH a library) is unreliable.**
semantic-release needs the `git` binary via GitPython → predicted nothing; cryptography/pyzmq falsely predicted `git`. The class: env needs a tool that no declared dependency names, and the current detector both misses and false-positives. (Runtime TOOLS like git are in scope; live SERVICES are deferred.)
Code: `subprocess_scan.py` (CLI_TOOL_TO_APT), `probe.py`, the import/runtime probes.

**R5 — Architecture / generalization (cross-cutting): keep fixes ecosystem-agnostic + make the eval the guardrail.**
All fixes must land at the `EcosystemProvider` seam / shared layer (not Python-specific code) so robustness carries to Node/Go when their providers wire in; and the eval's replay ladder currently hard-codes `python3 -c import` + `pytest` — parameterize the probe behind the provider so the same harness generalizes. Also: how the eval becomes a continuous regression gate over a growing repo-type corpus (pure-Python, C-ext, Rust-ext, sdist-only, monorepo, src-layout, uv/poetry/pip/setuptools, app-with-services).
Code: `src/ecosystems/{base,registry,python/provider}.py`, `src/eval/build_script_eval/{replay,scorecard}.py`.

## The Python repo-type space the fixes must generalize to
pure-Python (wheel-only) · C/Cython extension w/ manylinux wheel (bundles lib) · C-ext with NO wheel (must source-build: pygraphviz) · Rust-extension (cryptography/pydantic-core) · sdist-only · large dep-groups (docs/dev/test: typer) · src-layout vs flat · monorepo dev-root · uv / poetry / pip-tools / setuptools / flit / pdm backends · app-needing-runtime-tools (git) · app-needing-services (deferred).
