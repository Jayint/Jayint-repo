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
- `parse_go_work(path) -> tuple[str, ...]` — the `use ./dir …` member directories from a `go.work` file (workspace). Empty when there is no `go.work`.
- `module_closure(repo_dir) -> Closure` where `Closure(packages: dict[str,str], source: str, go_version, toolchain, replaced: tuple[str,...], direct: int, indirect: int, resolve_required: bool)`. `source ∈ {"workspace", "vendor", "gomod-pruned", "resolve-required"}`.

### 3.1 The authority ladder (the heart of `module_closure`)

```
if go.work exists:                          source = "workspace"       # union each `use` member's closure (recurse per member)
elif vendor/modules.txt exists:             source = "vendor"          # exact, offline, per-package
elif go_version >= 1.17:                     source = "gomod-pruned"    # require block == full build list
else:                                         source = "resolve-required" # cannot do faithfully offline
```

**Workspace branch:** `go.work` lists `use ./m1 ./m2 …`; `go list -m all` under a workspace returns the *union* build list across all member modules. `module_closure` therefore recurses into each member's `go.mod` (each resolving via the vendor/pruned/resolve rungs above) and unions the results, dropping every member's own `module_path`. A member that is itself `resolve-required` taints the workspace closure to `resolve-required` (honest — we can't complete it offline).

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
  "closure_source":  "workspace | vendor | gomod-pruned | resolve-required",
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
- Workspace: `go list -m all` run at the `go.work` root (the toolchain returns the union build list across members).
- Emits per-repo `{module: version}` gold closure JSON.

`go list -m all` is *the* MVS build list — precisely what the offline parse approximates. For a pruned ≥1.17 module with no local replaces we expect OURS == ORACLE (Δ≈0); that equality (or its violations) is the deliverable.

**Manifest-only corpus (important):** both sides need only the *module graph*, never the source. `go list -m all` downloads dependency **`go.mod` files** into the cache, not their packages. So each corpus entry is just a `{go.mod, go.sum[, vendor/modules.txt][, go.work + member go.mods]}` triple lifted from a real repo — no full clone. The one exception is **local `replace => ../path`**: the oracle fails when that path is absent (and manifest-only means it *is* absent), so local-replace is tested by fixtures (§9, OURS-only), never as a corpus/oracle entry (see §6/§7).

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
  - `replace_local` — modules with a local (filesystem) `replace`. These are removed from **both** OURS *and* ORACLE before recall/precision are computed (the toolchain's `go list -m all` still emits a locally-replaced module as `old => ../local`, so leaving it in the ORACLE denominator would falsely penalize recall). Reported for visibility, counted in neither metric. *Corpus entries never carry a local replace* (the oracle can't resolve a missing path — see §5); this bucket is exercised only by the OURS-side fixtures in §9. A **registry** replace (`=> fork@vX`) resolves normally and stays in both metrics.

Emits a per-repo scorecard + an aggregate markdown line, mirroring Node's reporter.

---

## 7. Corpus (`GO_SMOKE_ROOT`) — edge-case coverage, not statistical bulk

**Sizing rationale.** Go's premise (offline `go.mod`≥1.17 == `go list -m all`) is *deterministic*, not statistical: if module-graph pruning holds, Δ=0 for **every** clean ≥1.17 module regardless of size or domain. So — unlike the Python pkg-layer corpus, which had to grow 15→28 because precision genuinely varied by repo *type* (services 0.85 vs libraries 0.36) — adding "normal" Go repos here just re-confirms Δ=0 and teaches nothing. The corpus is sized for **one entry per structural feature that could break the premise**, not for averaging.

Each entry is a **manifest-only triple** (`{go.mod, go.sum[, vendor/modules.txt][, go.work + member go.mods]}`) lifted from a real repo (§5) — no full clones. Target ~8-9 entries:

| # | Axis it tests | Repo (lift manifest) | Expectation |
|---|---|---|---|
| 1 | **Anchor** — clean ≥1.17, rich closure | `spf13/viper` | Δ≈0. The premise-validating happy path. |
| 2 | Clean ≥1.17, tiny sanity | `spf13/cobra` | Δ≈0, obvious by eye. |
| 3 | **`go.work` workspace** | small multi-module workspace (real, or a constructed 2-member fixture) | union closure across members; `source="workspace"`. |
| 4 | Vendored | a real vendored repo's `go.mod`+`vendor/modules.txt` (e.g. `containerd/containerd`), or self-`go mod vendor` cobra | Δ≈0 vs `-mod=vendor`. |
| 5 | **Registry** `replace` (`=> fork@vX`) | a repo using a registry-level replace | replace applied; stays in both metrics. |
| 6 | Pre-1.17 | an **old tag** of cobra/viper (`go 1.15`/`1.16`) | `resolve-required` — validates we *flag*, not fake. |
| 7 | Zero-dep degenerate | `google/uuid` or `pkg/errors` | empty closure `{}` — guards `compare_go` divide-by-zero. |
| 8 | Large clean closure (scale) | `prometheus/prometheus` or an AWS-SDK service module (**verify: no local replaces**) | Δ=0 still holds at 300+ modules. |
| 9 | cgo — *pre-stage only* | a `mattn/go-sqlite3` dependent | Ordinary module closure now; C-lib obligation is the deferred SystemLib slice. Not a slice-1 fidelity edge case. |

**Not corpus entries** (the oracle can't run on them manifest-only) — these live in the §9 fixtures instead: **local `replace => ../path`**, **`exclude`**, and a **workspace with a missing member** (taint-to-`resolve-required`).

Corpus lives under `GO_SMOKE_ROOT` (default `outputs/graph_fidelity/_smoke_go`), same convention as Node's `_smoke_node`. Each manifest's exact `go` directive + closure size is verified when it is lifted (not asserted blind here).

---

## 8. Explicitly out of scope for slice 1

- **COMPILE certify path** (`go build ./...` / `go test ./...` — the bulk-compile-attributes-closure path the seam reserves as `CertifyMode.COMPILE`, shared with Rust).
- **cgo → `SystemLib` native obligations** (Phase 2 for Go).
- **Per-`GOOS`/`GOARCH` import pruning** (module-level closure is GOOS-stable enough for slice 1).
- **Wiring a `GoProvider`** into `src/ecosystems/` + `PROVIDERS`. This slice validates fidelity first; promotion is a mechanical follow-up once the number is trusted (the Node playbook).

---

## 9. Testing (per repo TDD / 80%-coverage rules)

- **Unit (no Docker):** fixture `go.mod`/`go.sum`/`vendor/modules.txt`/`go.work` files under `tests/eval/go/fixtures/` covering every parse branch — pruned, vendored, `go.work` workspace (2 members) + workspace-with-missing-member (taint to `resolve-required`), single-line & block `require`, `// indirect` tagging, registry replace, **local replace** (OURS-only; oracle can't run on it), **exclude**, pre-1.17 gate, `toolchain` directive, patch-versioned `go 1.21.0` directive, zero-dep (empty closure), malformed input (→ error record). Assert `module_closure` source selection + closure contents.
- **Integration (Docker-gated):** the anchor repo through the real `go list -m all` oracle, asserting recall==precision==1.0 on the clean ≥1.17 module; plus one `-mod=vendor` and one `go.work` oracle run.
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
