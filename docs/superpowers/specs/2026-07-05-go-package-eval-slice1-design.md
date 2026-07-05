# Go Module Package-Analysis Eval — Slice 1 Design

**Date:** 2026-07-05
**Status:** Design (approved in brainstorming; awaiting spec review). Buildable as written.
**Branch:** `john-v3-multi-lang`
**Mirrors:** the Node package-fidelity eval — `src/eval/language_package_eval/node/` (`lockfile.py`, `run_ours_node.py`, `platform_filter.py`, `compare_node.py`) and the Python eval it descends from.
**Extends:** `docs/superpowers/specs/2026-07-04-multi-language-ecosystem-seam-migration.md` (the seam) and `2026-07-04-node-package-fidelity-eval-design.md` (the eval-first pattern this repeats for a third ecosystem).
**Slice shape:** eval-first, offline construction parser + Docker oracle. **No** provider wiring, **no** certify, **no** cgo in this slice (see §8).

---

## 1. What this eval measures, and why Go is a *third distinct* question

The three ecosystems ask genuinely different fidelity questions:

- **Python (RESOLVE):** manifests are loose (`requests>=2`, optional groups) → the story is **recall** (under-coverage: deps the loose manifest never named). Headline was recall 0.940 / precision 0.505.
- **Node (LOCK):** `package-lock.json` *is* the resolved closure → recall is solved by construction; the story is **precision**, dominated by one cause (the lock lists every platform's optional binary; `npm ci` installs one platform's subset).
- **Go (this doc):** Go is **LOCK-*ish* by way of the toolchain, not a lockfile**. There is no npm-style pinned install set on disk. Instead, since **Go 1.17 module-graph pruning**, the *main module's `go.mod` require block enumerates the complete build list* (every directly- and transitively-needed module, the transitive ones tagged `// indirect`). So for a ≥1.17 module, an **offline parse of `go.mod` should equal the toolchain's own `go list -m all` build list** — near-perfect recall *and* precision by construction.

**The Go fidelity question this slice answers:** *Is an offline `go.mod`(≥1.17) / `vendor/modules.txt` parse a sound proxy for the authoritative `go list -m all` build list?* Where does it diverge, and is each divergence a real defect or a known, explainable offline limitation?

Expected divergence sources (the *findings* this slice is built to surface honestly):
1. **Pre-1.17, un-pruned `go.mod`** with no `vendor/` → the require block does **not** list the full build list → genuine **recall** gap. We do **not** paper over this with `go.sum` (which is a *superset* verification DB, not the closure). We flag the repo `resolve-required` and report it as a **known offline limitation**, not a silent miss.
2. **Local `replace` directives** (`replace X => ../local`) → version-less, points at a filesystem path; legitimately diverges from a registry closure. Bucketed as `replace_local`, not counted as error.
3. **`go list -m all` build-list vs compiled-package set** — the build list can retain modules that no package the main module actually compiles still imports (a **precision**-side over-listing). Interesting to measure; module-level is the unit we commit to in slice 1.

This is the mirror-image insight of Node: Node's lock over-lists *platforms*; Go's `go list -m all` over-lists at the *module-graph* granularity — but for pruned ≥1.17 modules both OURS and ORACLE derive from the same pruned graph, so we expect **Δ≈0**, the Go analog of Node's exact-match validation and Python's "collect-recall == run-recall".

---

## 2. Method recap, mirroring the Node/Python evals

| | Node eval (existing) | Go eval (this doc) |
|---|---|---|
| **Ecosystem mode** | LOCK (lockfile on disk) | LOCK via toolchain (pruned `go.mod`) / RESOLVE fallback |
| **OURS** | parse `package-lock.json` → platform-filtered `{name: version}` | parse `go.mod`(≥1.17)/`vendor/modules.txt` → `{module: version}` build list |
| **ORACLE** | `npm ci` → walk `node_modules/**/package.json` | `go list -m all` (or `-mod=vendor`) in `golang:<ver>` container |
| **GATE (oracle authority)** | installed tree | MVS build list from the toolchain itself |
| **OURS runtime** | pure JSON, no container, no network | pure text/JSON, no toolchain, no network |
| **Metrics** | recall, precision, version-agreement on ∩, own-package excluded | identical; own module (main module) excluded |
| **Divergence buckets** | `missing`, `extra`, `platform_optional_extra` | `missing`, `extra`, `replace_local`, `resolve_required` |

Everything structural is the same: construction-only (no agent, no repair loop on OURS), pooled recall/precision across the corpus, per-repo JSON scorecards + a divergence dump.

---

## 3. `gomod.py` — the offline module-closure parser (analog of `node/lockfile.py`)

Pure text/JSON. No `go` toolchain, no network. Public surface:

- `parse_go_mod(path) -> GoMod` — a frozen dataclass carrying:
  - `module_path: str` — the main module (excluded from every closure).
  - `go_version: str` — the `go 1.xx` directive → RUNTIME floor (recorded, not a package).
  - `toolchain: str | None` — optional `toolchain go1.xx.y` line.
  - `requires: tuple[Require, ...]` — each `Require(path, version, indirect: bool)` from `require (...)` blocks and single-line `require` forms; `indirect` is true iff the line carries a `// indirect` comment.
  - `replaces: tuple[Replace, ...]` — `Replace(old_path, old_version|None, new_path, new_version|None)`. A `new_version is None` (filesystem target) marks a **local** replace.
  - `excludes: tuple[Exclude, ...]` — `exclude` directives (remove a specific `path@version` from the graph).
- `parse_vendor_modules_txt(path) -> dict[str, str]` — `{module: version}` from `# <module> <version>` header lines (and the `## explicit` package annotations, retained for the future cgo/import-pruning slice). Authoritative and offline when `vendor/` exists.
- `parse_go_sum(path) -> frozenset[tuple[str, str]]` — `{(module, version)}`. Used **only** to cross-check (`go.sum ⊇ closure` should hold); **never** the closure.
- `module_closure(repo_dir) -> Closure` where `Closure(packages: dict[str,str], source: str, go_version, toolchain, replaced: tuple[str,...], direct: int, indirect: int, resolve_required: bool)`.

### 3.1 The authority ladder (the heart of `module_closure`)

```
if vendor/modules.txt exists:              source = "vendor"          # exact, offline, per-package
elif go_version >= 1.17:                    source = "gomod-pruned"    # require block == full build list
else:                                        source = "resolve-required" # cannot do faithfully offline
```

Then, regardless of source:
- Apply `replace`: an entry for `old` is rewritten to `new@new_version`; a local replace (`new_version is None`) is recorded in `replaced` and **dropped** from the registry closure (it has no registry version to compare — it lives on the filesystem).
- Apply `exclude`: drop any excluded `path@version`.
- Drop the main `module_path`.
- `resolve-required` → `packages = {}`, `resolve_required = True` (honest empty, explicitly flagged).

Version comparison for the `>= 1.17` gate parses `go 1.xx[.y]` as an integer tuple (`1.17` → `(1,17)`); `1.16` and below fail the gate.

---

## 4. `run_ours_go.py` — OURS side (analog of `run_ours_node.py`)

For each repo under `GO_SMOKE_ROOT`, emit `<repo>.json`:

```json
{
  "packages":        {"<module>": "<version>"},   // the offline closure, main module excluded
  "package_count":   0,
  "closure_source":  "vendor | gomod-pruned | resolve-required",
  "go_version":      "1.xx",
  "toolchain":       "go1.xx.y | null",
  "direct_count":    0,
  "indirect_count":  0,
  "replaced":        ["<module> => <target>"],     // diagnostic
  "resolve_required": false,
  "target":          {"goos": "linux", "goarch": "amd64"}  // recorded; does NOT filter in slice 1
}
```

`target` is carried for symmetry with Node and to seed the future per-`GOOS` pruning slice; it does **not** filter the module list here (MVS build list is largely GOOS/GOARCH-independent at the module granularity). Per-repo `try/except` writes an `{"error": "..."}` record and continues, exactly like `run_ours_node.py`.

Env knobs mirror Node: `GO_SMOKE_ROOT` (corpus dir), `GO_TARGET="goos,goarch"` (default `linux,amd64`).

---

## 5. `oracle.py` (Go) — the authoritative build list

Inside a `golang:<ver>` container (Docker-gated exactly like Node's oracle):

- Non-vendored: `go list -m all` → module@version lines; **first line is the main module**, excluded.
- Vendored: `go list -mod=vendor -m all`.
- Emits per-repo `{module: version}` gold closure JSON.

`go list -m all` is *the* MVS build list — precisely what the offline parse approximates. For a pruned ≥1.17 module with no local replaces we expect OURS == ORACLE (Δ≈0); that equality (or its violations) is the deliverable.

The oracle needs Docker + a `golang` image and (for non-vendored, non-cached repos) network to populate the module cache. It is integration-gated behind an env flag / pytest marker; it is **not** required to run the OURS side or the unit tests.

---

## 6. `compare_go.py` — metrics (analog of `compare_node.py`)

Per-repo and pooled:
- **recall** = |OURS ∩ ORACLE| / |ORACLE|
- **precision** = |OURS ∩ ORACLE| / |OURS|
- **version-agreement** on the intersection (exact; and `major.minor` for a looser view).
- Divergence buckets:
  - `missing` — in ORACLE, not in OURS (recall miss). For `resolve-required` repos, the *entire* ORACLE lands here and is re-labelled **`resolve_required`** (a known offline limitation, reported separately from true recall defects).
  - `extra` — in OURS, not in ORACLE (precision miss).
  - `replace_local` — modules with a local (filesystem) `replace`. These are removed from **both** OURS *and* ORACLE before recall/precision are computed (the toolchain's `go list -m all` still emits a locally-replaced module as `old => ../local`, so leaving it in the ORACLE denominator would falsely penalize recall). Reported for visibility, counted in neither metric.

Emits a per-repo scorecard + an aggregate markdown line, mirroring Node's reporter.

---

## 7. Corpus (`GO_SMOKE_ROOT`) — chosen to span the divergence axes

~6 real repos with committed `go.mod`/`go.sum`, each targeting a specific axis so the metrics are *diagnostic*, not just a number:

1. **Clean ≥1.17 pruned module** — expect Δ≈0 (the happy path that validates the whole premise).
2. **`vendor/`ed repo** — exercises `parse_vendor_modules_txt`; expect Δ≈0 against `-mod=vendor`.
3. **Repo with `replace` directives** (both a registry `=> new@vX` and a local `=> ../x`) — exercises the `replaced`/`replace_local` path.
4. **Pre-1.17 repo** — expected `resolve-required`; validates that we flag rather than fake it.
5. **`exclude`-using repo** — exercises exclude handling.
6. **cgo user** (e.g. a `go-sqlite3` dependent) — module closure is correct now; the C-lib obligation is deferred to the native slice, but this repo pre-stages that corpus entry.

Corpus lives under `GO_SMOKE_ROOT` (default `outputs/graph_fidelity/_smoke_go`), same convention as Node's `_smoke_node`.

---

## 8. Explicitly out of scope for slice 1

- **COMPILE certify path** (`go build ./...` / `go test ./...` — the bulk-compile-attributes-closure path the seam reserves as `CertifyMode.COMPILE`, shared with Rust).
- **cgo → `SystemLib` native obligations** (Phase 2 for Go).
- **Per-`GOOS`/`GOARCH` import pruning** (module-level closure is GOOS-stable enough for slice 1).
- **Wiring a `GoProvider`** into `src/ecosystems/` + `PROVIDERS`. This slice validates fidelity first; promotion is a mechanical follow-up once the number is trusted (the Node playbook).

---

## 9. Testing (per repo TDD / 80%-coverage rules)

- **Unit (no Docker):** fixture `go.mod`/`go.sum`/`vendor/modules.txt` files under `tests/eval/go/fixtures/` covering every parse branch — pruned, vendored, single-line & block `require`, `// indirect` tagging, registry replace, local replace, exclude, pre-1.17 gate, malformed input (→ error record). Assert `module_closure` source selection + closure contents.
- **Integration (Docker-gated):** one repo through the real `go list -m all` oracle, asserting recall==precision==1.0 on the clean ≥1.17 module.
- **Compare unit:** synthetic OURS/ORACLE dicts → assert recall/precision/bucket math (incl. `resolve_required` re-labelling).

---

## 10. File manifest

```
src/eval/language_package_eval/go/
  __init__.py
  gomod.py            # §3  offline parser + authority-ladder module_closure
  run_ours_go.py      # §4  OURS extractor -> per-repo JSON
  oracle.py           # §5  Docker `go list -m all` gold closure
  compare_go.py       # §6  recall/precision + buckets
tests/eval/go/
  fixtures/...        # §9  go.mod/go.sum/modules.txt fixtures
  test_gomod.py
  test_compare_go.py
  test_oracle_go.py   # Docker-gated
```
