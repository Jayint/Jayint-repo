# Build-Script Robustness — Root-Cause Fix Roadmap (synthesis)

**Date:** 2026-07-06 · **Basis:** 16-repo build-script-eval diagnostic + 5 parallel research investigations (R1–R5, each verified against real code and cloned repos). Detailed docs: `R1…R5-*.md` + `…-diagnostic.md` in this dir.

## The reframe (why the corpus expansion mattered)

The first 5-repo run made it look like the system **over-predicts** apt broadly (typer: 31 packages). Expanding to 16 repos flipped the priority: **15/16 predict apt correctly (0–4, cleanly classified).** typer is the lone over-predictor, and only because its resolution *failed*. The genuine systemic robustness gap is the opposite — **under-prediction on source-built native projects** (pygraphviz, lxml get no/partial build-deps) — plus unreliable runtime-tool detection. Fixing per-repo symptoms would have been the "local patch" trap; the diagnostic redirected effort to the causes that generalize.

## The five root causes, prioritized by (impact × safety ÷ effort)

### 1. R2-Fix-B — unsafe unknown-build-mode default  ·  1 line, do first
`build_deps.py:309` gates the Debian-Build-Depends dump on `build_from_source is False` (skip only *confirmed* wheels), so an **unknown** (`None`) package is treated as source-built and dumped. Tighten to **`is not True`** (dump only for a *confirmed* source build). Verified: a real lock-parsed package is never left `None`; `None` only means the resolve degraded to the `uv pip compile` fallback. **Effect: typer 31→~1 apt; zero change to the 15 clean repos or psycopg2/lxml (their native deps are `True`).** Highest leverage, lowest risk — a safety valve independent of the deeper resolution fix.

### 2. R1 — the PROJECT node is excluded from every build-dep stage  ·  the systemic under-prediction fix
The repo-under-test is a `NodeType.PROJECT`, but `seed_build_deps` (`build_deps.py:306-311`) and the generic floor (`seed.py:74-76`) **hard-filter to `NodeType.PACKAGE`** — so pygraphviz/lxml are *never asked* whether they need a compiler. Three-part fix, all verified live:
- **Include the project node** in the build-dep stages + apply an unconditional **`build-essential` floor** when any native-build signal is present on it.
- **Key Debian `Build-Depends` on the project's own name** (reuse `debian_build_deps`, unwired) → lxml's `libxml2-dev`/`libxslt1-dev` (verified via `apt-cache showsrc lxml`).
- **New: AST-scan the project's own `setup.py`/pyproject** for `Extension(libraries=[...])`/`.pyx` → feed the *existing* `ObservedNeed(kind="linker_lib")` → `os_resolver.resolve` pipeline (zero new resolver code) → pygraphviz's `libgraphviz-dev` (verified via `apt-file search libcgraph.so`). This is the **only** source that generalizes to native-ext repos Debian never packaged (PEP 725 adoption ≈ 0). **Effect: pygraphviz apt=[]→installs; lxml gets its specific -dev.** This is the main robustness win.

### 3. R4 — runtime-tool detection (misses AND false-positives)  ·  small, high-value
`git` (via GitPython) is missed for semantic-release yet falsely predicted for cryptography/pyzmq. Three verified fixes:
- **`LIBRARY_REQUIRES_BINARY` table** (mirrors `CLI_TOOL_TO_APT`) keyed on **resolved dependency identity** (GitPython⇒git) → semantic-release. Robust because it needs a resolver-confirmed Package node, not the fragile import-graph.
- **Guard 1:** add `"tools"` to `config_scan.py`'s `_EXCLUDED_SEGMENTS` (it's missing; `scan.py` has it) → kills pyzmq's false git (from `tools/test_sdist.py`).
- **Guard 2:** `_program_from_call` matches a bare `run(`/`call(` without verifying the callee resolves to `subprocess`/`os`/`shutil` → kills cryptography's false git (a local `def run` in `release.py`).

### 4. R3 — Debian-source mapping precision  ·  3 orthogonal layers, more scrutiny
Only bites when the build-dep prior runs (typer's unknown-mode), but all three are needed for full precision:
- **Layer 2 (cheapest):** `parse_build_depends` *discards* Debian's `<!nocheck>`/`<!nodoc>` build-profile tags — `pytest`→`lsof` is literally `lsof <!nocheck>` (a discarded-signal bug, not a collision). Use the tags to drop test/doc-only tokens.
- **Layer 1:** the tiered source-accept gate already scoped in the **existing unimplemented plan `2026-07-06-debian-source-disambiguation.md`** — verify the Debian source provides *the pip dist* (`python3-<dist>`), not just any python3 binary → kills `click`→Ubuntu-Click's Vala/GLib set (verified: click's Debian source is the Ubuntu Click tool).
- **Layer 3 (highest leverage):** flip `is_system_lib` from a denylist to an **allowlist of build-artifact shapes** (`-dev`/`lib*`/curated tools) → drops bare `postgresql` while keeping `libpq-dev` (verified psycopg2 control has unqualified `postgresql`). Subsumes the plan's "widen the denylist" follow-up without name whack-a-mole.

### 5. R2-Fix-A — resolution robustness (the deeper cause behind Fix B)
typer's resolution genuinely fails: `compute_exclude_newer` anchors the whole resolve to an incidental **dev-tool pin** (`ruff==0.2.0`'s date), making `httpx>=0.27.0` unsatisfiable; `parse_resolver_error` returns an **empty diagnosis** for that stderr, so the bad root can't be dropped → immediate fallback to `_pip_compile_fallback` (which never stamps `build_from_source`) → all-67-unknown. Fixes: add the missing resolver-error regex (`resolve_errors.py`); stop `requirements-github-actions.txt` defaulting to `kind="dependency"` (`evidence.py`); stop `compute_exclude_newer` anchoring to ABI-irrelevant dev-tool pins (`pins.py`). **Generalizes broadly** — pinned-lint-tool-next-to-floored-runtime-dep is an extremely common OSS pattern. (`docs_src` is a separate PEP-420 namespace red herring, not the cause.)

## Generalization & architecture (R5) — so these aren't Python-only patches

- **Keep the mechanism shared, move only the data to the provider.** R1's scanner output (`ObservedNeed`) + resolver (`os_resolver.resolve`), R4's curated-table lookup, R3's apt-cache walk are all ecosystem-agnostic; only the *extractor/table data* is Python-specific. Add detection hooks to `EcosystemProvider` (`own_native_build_needs`, `runtime_tool_priors`, `registry_source_candidates`, …) so Rust (`Cargo.toml links=`) / CGo (`#cgo`) reuse the same shape by a data swap. R2-Fix-B is pure shared machinery.
- **Parameterize the eval ladder behind the provider.** Replace `replay.py`/`scorecard.py`'s hardcoded `python3 -c import` / `pytest` with a per-provider `ReplayProfile` (image, entry-symbol, probe/bootstrap/collect/run commands, result classifier). Python's impls are call-throughs to today's literals → a before/after scorecard diff over the corpus is the zero-impact proof.
- **The eval becomes the generalization guardrail.** Extend `corpus.py` `STRATA` with repo-type strata (`S_cext_no_wheel`, `S_rust_ext`, `S_sdist_only`, `S_depgroup_heavy`, `S_src_layout`, `S_monorepo`, `S_backend_variants`, `S_runtime_tool`, …). Commit per-repo scorecard snapshots; every fix's gate = **"Moves N (target repos advance) AND Regresses 0 (all others byte-identical)"**, with **≥2 repos per stratum** required before a fix counts as proven-generalized rather than a one-repo overfit. This is the structural answer to "how do we know it's a root-cause fix, not a local patch."

## Recommended sequencing

1. **R2-Fix-B** (1 line) — immediate, safe; removes the scariest symptom. Prove: typer apt 31→~1, 15 clean repos byte-identical.
2. **R1** (project-node build-deps) — the systemic under-prediction fix; biggest robustness gain. Prove: pygraphviz/lxml advance.
3. **R4** (runtime tools) — small, fixes git both directions. Prove: semantic-release gains git; cryptography/pyzmq drop it.
4. **R3** (Debian precision, 3 layers) — hardens the prior; adopt the existing disambiguation plan. Prove: typer's click-set gone, psycopg2 keeps libpq-dev / drops postgresql.
5. **R2-Fix-A** (resolution robustness) — the deeper cause behind Fix B; broadest generalization.
6. **R5** (provider interface + ReplayProfile + corpus strata) — land *alongside* 1–5 so each fix is placed at the seam and gated by the corpus guardrail.

Each of 1–5 is a small, independently-verifiable change; R5 is the architecture that keeps them from being Python-coupled and proves they generalize.
