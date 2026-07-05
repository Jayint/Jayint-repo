# Go Module Package-Analysis Eval — Slice 1 Design

**Date:** 2026-07-05
**Status:** Design — **revised 2026-07-05 after a Codex correctness review** (see §0). Buildable as written.
**Branch:** `john-v3-multi-lang`
**Mirrors:** the Node package-fidelity eval — `src/eval/language_package_eval/node/` (`lockfile.py`, `run_ours_node.py`, `platform_filter.py`, `compare_node.py`) and the Python eval it descends from.
**Extends:** `docs/superpowers/specs/2026-07-04-multi-language-ecosystem-seam-migration.md` (the seam) and `2026-07-04-node-package-fidelity-eval-design.md` (the eval-first pattern this repeats for a third ecosystem).
**Slice shape:** eval-first, offline construction parser + Docker oracle. **No** provider wiring, **no** certify, **no** cgo in this slice (see §8).

---

## 0. Design correction (Codex review, 2026-07-05)

The first draft claimed a Go ≥1.17 `go.mod` require block *equals* the `go list -m all` build list, so OURS==ORACLE with **Δ≈0**. **That is wrong**, and the corpus picks were wrong too. Corrections, incorporated below:

1. **Δ=0 is not sound.** Go 1.17 module-graph pruning makes the main `go.mod` complete enough for *package loading*, **not** equal to the MVS build list. `go list -m all` can list **more** modules than a tidy require block — e.g. dep `A` (go 1.17) requires `B` and `C`, the main module only imports a `B`-package, yet `C` stays in the pruned graph and in `go list -m all` while a tidy go.mod omits it. So there is a **systematic, oracle-heavy recall gap**. This slice's job is to **measure that gap**, not assume it away (Go's *recall* story, like Python's).
2. **Corpus picks were wrong** (verified via `proxy.golang.org`): `spf13/cobra@v1.8.0` is `go 1.15` (not ≥1.17); `google/uuid@v1.6.0` has **no `go` directive**. Both would resolve to `resolve-required`, not the "clean pruned" axis they were assigned. Only `viper@v1.18.2` (`go 1.18`) is a valid anchor. §7 fixed.
3. **Semantic bugs** (fixed in §3.1/§6): `replace X vOld => …` must honor the old-version constraint; `exclude` **forbids a version** (MVS selects the next), it does not drop the module; `go.work` is **one global MVS (max version wins)**, not a last-write union.
4. **Oracle hardening** (§5): parse `go list -m -json all` (not a stringly `v`-prefix filter); force `-mod=mod` (non-vendored) / `-mod=vendor` (vendored); require a complete lifted `go.sum` or the oracle fails/mutates files.

---

## 1. What this eval measures, and why Go is a *third distinct* question

The three ecosystems ask genuinely different fidelity questions:

- **Python (RESOLVE):** manifests are loose (`requests>=2`, optional groups) → the story is **recall** (under-coverage: deps the loose manifest never named). Headline was recall 0.940 / precision 0.505.
- **Node (LOCK):** `package-lock.json` *is* the resolved closure → recall is solved by construction; the story is **precision**, dominated by one cause (the lock lists every platform's optional binary; `npm ci` installs one platform's subset).
- **Go (this doc):** Go is **LOCK-*ish* by way of the toolchain, not a lockfile**. There is no npm-style pinned install set on disk. Since **Go 1.17 module-graph pruning**, a tidy main module's `go.mod` require block enumerates the modules needed for **package loading** (direct + `// indirect`) — a close but **not exact** approximation of the `go list -m all` build list. The build list (full pruned-graph MVS) is generally a **superset**: it retains a dependency's siblings that the main module never imports (see §0.1). So OURS (offline require-block parse) is expected to under-count the ORACLE → the Go story is **recall**.

**The Go fidelity question this slice answers:** *How closely does an offline `go.mod`(≥1.17)/`vendor/modules.txt`/`go.work` parse approximate the authoritative `go list -m all` build list, and is the residual gap systematic (real Go semantics) or fixable?* We **measure** the gap; we do not assume it is zero.

Expected divergence sources (the *findings* this slice is built to surface honestly):
1. **Pruned-graph superset (the headline gap).** For a tidy ≥1.17 module, `go list -m all` ⊇ require-block, and can be **strictly** larger: a required dep `A`'s own `go.mod` pulls in modules that provide no package the main module imports, yet they stay in the pruned graph and the build list. Direction: **oracle-heavy → recall gap**. The eval's core measurement.
2. **Pre-1.17 / no `go` directive, no `vendor/`** → un-pruned go.mod does not list the full graph → we flag `resolve-required` and report it as a **known offline limitation**, not a silent miss. We do **not** paper over it with `go.sum` (a superset verification DB, not the closure).
3. **Local `replace` directives** (`replace X => ../local`) → version-less filesystem target whose own go.mod can add/remove deps we cannot see offline; bucketed `replace_local`, excluded from metrics, corpus-excluded (§5/§7).

Mirror-image of Node: Node's lock **over**-lists platforms (precision story); Go's build list **over**-lists relative to what a require-block parse can see (recall story). This is the Go analog of Python's recall investigation, not Node's exact-match validation.

### 0.1 Why the pruned require block ≠ the build list (worked example)

Main `M` (`go 1.18`) imports one package from `A` (`go 1.17`). `A`'s go.mod requires `B` and `C`; `M`'s code only reaches a `B` package.
- **`go list -m all`** (build list over the pruned graph): `A`, `B`, **`C`** — `C` is `A`'s direct require, so it stays in the graph.
- **tidy `M` go.mod** require block: `A` (direct), `B` (`// indirect`, provides an imported package). `C` provides no imported package → **not** recorded.
- **Result:** OURS misses `C` → recall < 1. This is correct Go behavior, not a bug — so the eval reports the gap size, not a pass/fail against Δ=0.

---

## 2. Method recap, mirroring the Node/Python evals

| | Node eval (existing) | Go eval (this doc) |
|---|---|---|
| **Ecosystem mode** | LOCK (lockfile on disk) | LOCK via toolchain (pruned `go.mod`) / RESOLVE fallback |
| **OURS** | parse `package-lock.json` → platform-filtered `{name: version}` | parse `go.mod`(≥1.17)/`vendor/modules.txt` → `{module: version}` require-block closure (an *approximation* of the build list, §0.1) |
| **ORACLE** | `npm ci` → walk `node_modules/**/package.json` | `go list -m -json all` (or `-mod=vendor`) in `golang:<ver>` container |
| **GATE (oracle authority)** | installed tree | MVS build list from the toolchain itself |
| **OURS runtime** | pure JSON, no container, no network | pure text/JSON, no toolchain, no network |
| **Story / headline metric** | precision (over-list) | **recall** (under-list vs the build-list superset, §0.1) |
| **Divergence buckets** | `missing`, `extra`, `platform_optional_extra` | `missing`, `extra`, `replace_local`, `resolve_required` |

Everything structural is the same: construction-only (no agent, no repair loop on OURS), pooled recall/precision across the corpus, per-repo JSON scorecards + a divergence dump.

**Two oracles, reported side by side** (so the recall gap is *attributed*, not just measured):
- **Build-list oracle** — `go list -m all`. The authoritative MVS build list; OURS is expected to under-count it (§0.1). Answers "how far is the require block from the full build list?"
- **Package-loading oracle** *(narrower, diagnostic)* — the modules actually providing imported packages, via `go list -deps -json ./...` → the set of modules whose packages are reachable. A tidy require block should match this **tightly** (≈Δ0). Answers "does the require block correctly capture the package-loading set?" — separating *our parser's* fidelity from *Go's* build-list-vs-load-set gap. **Requires the main module's SOURCE** (it loads imports), so it is **not** manifest-only: it runs on a **full clone of the anchor repo only**, as a one-off diagnostic — not across the manifest-only corpus.

Slice 1 ships the **build-list oracle as primary** (manifest-only, whole corpus → `recall_buildlist`, `precision`). The **package-loading oracle is an optional diagnostic** run on the full-clone anchor to attribute the anchor's residual (`pruned_superset` vs `recall_defect`); `compare_go` accepts it as optional and, when absent, reports `recall_buildlist` alone.

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
- `module_closure(repo_dir) -> Closure` where `Closure(packages: dict[str,str], source: str, go_version, toolchain, replace_local: tuple[str,...], direct: int, indirect: int, resolve_required: bool)`. `replace_local` holds the module keys dropped by a **local** replace (needed by the comparer to exclude them from both sides). `source ∈ {"workspace", "vendor", "gomod-pruned", "resolve-required"}`.

### 3.1 The authority ladder (the heart of `module_closure`)

```
if go.work exists:                          source = "workspace"       # ONE global MVS across members (§ below)
elif vendor/modules.txt exists:             source = "vendor"          # exact, offline, per-package
elif go_version >= 1.17:                     source = "gomod-pruned"    # require block ≈ package-loading set (approx of build list, §0.1)
else:                                         source = "resolve-required" # cannot do faithfully offline
```

**Workspace branch:** `go.work` lists `use ./m1 ./m2 …`. `go list -m all` at the workspace root computes **one** MVS build list across all member modules, so when the same module is required at different versions by two members, the **maximum** version wins (not the last one parsed). `module_closure` recurses into each member's `go.mod`, then merges with **max-version-per-module** (not `dict.update`), dropping every member's own `module_path`. A member that is itself `resolve-required` taints the workspace to `resolve-required`. **Out of scope for slice 1 (flagged, not modeled):** workspace-level `replace` directives in `go.work` and `go.work.sum` — a workspace using them is marked `resolve-required` rather than mis-computed.

Then, regardless of source:
- Apply `replace`: **honor the old-version constraint.** `replace X vOld => Y vN` rewrites `X` **only if** the closure's selected version of `X` is `vOld`; `replace X => Y vN` (no old version) rewrites any selected `X`. The rewrite keys the **old path** with the **new version** (`X: vN`) — matching what `go list -m all` prints (`X vSel => Y vN`) and what the oracle parser extracts, so both sides agree. A local replace (`new_version is None`) is recorded in `replace_local` and **dropped** (its filesystem target's own deps are invisible offline; corpus-excluded, §5).
- Apply `exclude`: `exclude X vBad` **forbids that one version**, it does **not** drop the module — MVS selects the next-highest available version. Offline (single-version closure) we therefore **only** drop `X` if the selected version *is* `vBad` **and** no other required version exists; otherwise we leave `X` as-is. (Modeling MVS's "pick next" fully needs the module graph, so an `exclude` that would force a re-selection taints to `resolve-required`.)
- Drop the main `module_path`.
- `resolve-required` → `packages = {}`, `resolve_required = True` (honest empty, explicitly flagged).

Version comparison for the `>= 1.17` gate parses the `go 1.xx[.y]` directive as an integer tuple (`1.17` → `(1,17)`); `1.16`, a `go` directive that is absent/empty, or an unparseable value all fail the gate → `resolve-required`.

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
  "replace_local":   ["<module>"],                 // module keys dropped by a local replace (excluded from metrics)
  "resolve_required": false,
  "target":          {"goos": "linux", "goarch": "amd64"}  // recorded; does NOT filter in slice 1
}
```

`target` is carried for symmetry with Node and to seed the future per-`GOOS` pruning slice; it does **not** filter the module list here (MVS build list is largely GOOS/GOARCH-independent at the module granularity). Per-repo `try/except` writes an `{"error": "..."}` record and continues, exactly like `run_ours_node.py`.

Env knobs mirror Node: `GO_SMOKE_ROOT` (corpus dir), `GO_TARGET="goos,goarch"` (default `linux,amd64`).

---

## 5. `oracle.py` (Go) — the authoritative build list

Inside a `golang:<ver>` container (Docker-gated exactly like Node's oracle). **Parse `-json`, not stdout columns** — `go list -m -json all` emits one JSON object per module with `Path`, `Version`, `Main` (bool), and `Replace{Path,Version}`. This is robust where the stringly "skip lines whose 2nd token isn't `vN`" filter is not (main modules, replaced modules, pseudo-versions):

- **Non-vendored:** `go list -mod=mod -m -json all`. Force `-mod=mod` so a stray `vendor/` dir or a stale go.mod does not silently change the answer (auto-vendor engages for `go>=1.14` + `vendor/`). `Main: true` objects are the main module(s) → excluded. A `Replace` field keys the **original** `Path` with the replacement `Version` (matching OURS, §3.1).
- **Vendored:** `go list -mod=vendor -m -json all` (only when `vendor/modules.txt` is present and consistent; the toolchain errors on an inconsistent one — surface that as a corpus-validity failure, not a metric).
- **Workspace:** `go list -m -json all` at the `go.work` root (one global MVS across members; `Main: true` members excluded).
- Emits per-repo `{module: version}` gold closure JSON (`{"installed": {...}, "repo": name}`).

**go.sum completeness is a corpus precondition.** Under default `-mod=readonly`/`-mod=mod`, `go list -m all` reads dependency go.mod files and verifies them against `go.sum`; an **incomplete lifted go.sum makes the oracle fail or mutate files**. Lift the repo's *complete* committed `go.sum`, and treat any oracle error as a rejected corpus entry, never a silent zero.

`go list -m all` is *the* MVS build list. OURS (offline require-block parse) is expected to be a **subset** of it (§0.1); the eval **measures** `recall = |OURS∩ORACLE| / |ORACLE|` and attributes the residual using the package-loading oracle (§2). This gap — not a Δ=0 pass/fail — is the deliverable.

**Manifest-only corpus (important):** both sides need only the *module graph*, never the source. `go list -m all` downloads dependency **`go.mod` files** into the cache, not their packages. So each corpus entry is just a `{go.mod, go.sum[, vendor/modules.txt][, go.work + member go.mods]}` triple lifted from a real repo — no full clone. The one exception is **local `replace => ../path`**: the oracle fails when that path is absent (and manifest-only means it *is* absent), so local-replace is tested by fixtures (§9, OURS-only), never as a corpus/oracle entry (see §6/§7).

The oracle needs Docker + a `golang` image and (for non-vendored, non-cached repos) network to populate the module cache. It is integration-gated behind an env flag / pytest marker; it is **not** required to run the OURS side or the unit tests.

---

## 6. `compare_go.py` — metrics (analog of `compare_node.py`)

Per-repo and pooled — **recall is the headline** (§0.1). Reported against **both** oracles (§2):
- **recall_buildlist** = |OURS ∩ BUILD_LIST| / |BUILD_LIST| — expected **< 1** by Go semantics; its shortfall is the pruned-graph superset gap.
- **recall_loadset** = |OURS ∩ LOAD_SET| / |LOAD_SET| — against the package-loading oracle; expected **≈ 1** for a tidy module. This is the real fidelity signal for *our parser*: a shortfall here is a genuine parser/recall defect, not Go structure.
- **precision** = |OURS ∩ BUILD_LIST| / |OURS| — expected ≈ 1 (OURS rarely lists a module the build list lacks; if it does, suspect a stale/over-broad require block).
- **version-agreement** on the intersection (exact; and `major.minor` for a looser view).
- Divergence buckets:
  - `missing` — in BUILD_LIST, not in OURS. Split into **`pruned_superset`** (also absent from LOAD_SET → expected Go structure, not a defect) vs **`recall_defect`** (present in LOAD_SET but missed by OURS → a real parser bug to fix). This split is the point of carrying two oracles.
  - For `resolve-required` repos the *entire* ORACLE is re-labelled **`resolve_required`** (a known offline limitation, reported separately from recall defects).
  - `extra` — in OURS, not in BUILD_LIST (precision miss).
  - `replace_local` — modules with a local (filesystem) `replace`. These are removed from **both** OURS *and* ORACLE before recall/precision are computed (the toolchain's `go list -m all` still emits a locally-replaced module as `old => ../local`, so leaving it in the ORACLE denominator would falsely penalize recall). Reported for visibility, counted in neither metric. *Corpus entries never carry a local replace* (the oracle can't resolve a missing path — see §5); this bucket is exercised only by the OURS-side fixtures in §9. A **registry** replace (`=> fork@vX`) resolves normally and stays in both metrics.

Emits a per-repo scorecard + an aggregate markdown line, mirroring Node's reporter.

---

## 7. Corpus (`GO_SMOKE_ROOT`) — edge-case coverage, not statistical bulk

**Sizing rationale.** The recall gap (§0.1) is a **structural property of Go pruning**, not a statistical distribution over repo types — so we don't need Python's 15→28 volume to average out variance. We need **one entry per structural feature** so the gap is *attributed* (pruned-superset vs parser-defect) rather than just averaged. A modest scale entry confirms the gap doesn't explode on a 300-module closure.

Each entry is a **manifest-only triple** (`{go.mod, go.sum[, vendor/modules.txt][, go.work + member go.mods]}`) lifted from a real repo (§5) — no full clones. **`go` directives below are proxy-verified (2026-07-05), not assumed** — the first draft's picks were wrong (cobra is `go 1.15`, uuid has no `go` directive), which is exactly why each is now pinned and checked:

| # | Axis it tests | Repo@tag (verified `go`) | Expectation |
|---|---|---|---|
| 1 | **Anchor** — clean ≥1.17, rich closure | `spf13/viper@v1.18.2` (**go 1.18** ✓) | `recall_loadset ≈ 1`; `recall_buildlist < 1` measures the pruned-superset gap. |
| 2 | Clean ≥1.17, tiny sanity | any small lib whose lifted go.mod is confirmed `go ≥ 1.17` with a handful of requires (**pick at lift by the gate below — do not pre-name an unverified tag**) | small closure, easy by eye. |
| 3 | **`go.work` workspace** (MVS-max) | constructed 2-member workspace with an **overlapping** dep at two versions | `source="workspace"`; max version wins (guards the MVS-max fix, §3.1). |
| 4 | Vendored | self-`go mod vendor` a ≥1.17 module (no replaces, §5) → real `vendor/modules.txt` | matches `-mod=vendor` build list. |
| 5 | **Registry** `replace` (`=> fork@vX`) | small ≥1.17 module with a registry-level replace (+ complete go.sum for the target) | replace applied, keyed old-path/new-version; stays in both metrics. |
| 6 | Pre-1.17 / no `go` directive | `spf13/cobra@v1.8.0` (**go 1.15** ✓ pre-1.17) and/or `google/uuid@v1.6.0` (**no `go` directive** ✓) | `resolve-required` — validates we *flag*, not fake (these are the *correct* home for the mis-assigned draft picks). |
| 7 | Zero-dep, but **≥1.17** | a verified ≥1.17 module with an empty require block | empty closure `{}` — guards `compare_go` divide-by-zero. (`uuid` does **not** qualify — no `go` directive → it's a #6 case, not this one.) |
| 8 | Large closure (scale) | a large ≥1.17 module **verified free of local replaces** (`prometheus/prometheus` only if its go.mod has no `=> ../` — else pick a cloud-SDK service module) | recall gap stays bounded at 300+ modules. |
| 9 | cgo — *pre-stage only* | a `mattn/go-sqlite3` dependent | Ordinary module closure now; C-lib obligation is the deferred SystemLib slice. Not a slice-1 fidelity edge case. |

**Not corpus entries** (the oracle can't run on them manifest-only) — §9 fixtures instead: **local `replace => ../path`**, **`exclude` that forces a re-selection**, **workspace with a missing member** or **workspace-level replace** (all → `resolve-required`).

Corpus lives under `GO_SMOKE_ROOT` (default `outputs/graph_fidelity/_smoke_go`). **Lift-time gate:** every entry's `go` directive is re-checked against its intended axis before it enters the corpus (a repo that lands in the wrong bucket is re-tagged or replaced, never forced).

---

## 8. Explicitly out of scope for slice 1

- **COMPILE certify path** (`go build ./...` / `go test ./...` — the bulk-compile-attributes-closure path the seam reserves as `CertifyMode.COMPILE`, shared with Rust).
- **cgo → `SystemLib` native obligations** (Phase 2 for Go).
- **Per-`GOOS`/`GOARCH` import pruning** (module-level closure is GOOS-stable enough for slice 1).
- **Wiring a `GoProvider`** into `src/ecosystems/` + `PROVIDERS`. This slice validates fidelity first; promotion is a mechanical follow-up once the number is trusted (the Node playbook).

---

## 9. Testing (per repo TDD / 80%-coverage rules)

- **Unit (no Docker):** fixture `go.mod`/`go.sum`/`vendor/modules.txt`/`go.work` files under `tests/eval/language_package_eval/go/` (inline via `tmp_path`) covering every parse branch — pruned, vendored, `go.work` workspace with an **overlapping dep at two versions → max wins** and workspace-with-missing-member (taint), single-line & block `require`, `// indirect` tagging, **registry replace with `vOld` constraint** (applies only when selected == `vOld`; a non-matching `vOld` is a no-op), **local replace** (OURS-only), **`exclude`** asserting *forbid-version* semantics (module retained when another version is selected; re-selection case taints to `resolve-required`), pre-1.17 / no-`go`-directive gate, `toolchain` directive, patch-versioned `go 1.21.0`, zero-dep (empty closure), malformed input (→ error record). Assert `module_closure` source selection + closure contents.
- **Integration (Docker-gated):** the build-list oracle across manifest-only entries; record `recall_buildlist` per repo (the structural gap, expected `< 1.0`, **not** asserted `== 1`). Separately, a **full clone** of the anchor (`viper@v1.18.2`) through the package-loading oracle → assert `recall_loadset == 1.0` (parser fidelity). Plus one `-mod=vendor` and one `go.work` build-list run. The old "recall==precision==1.0" assertion is **removed** (§0.1).
- **Compare unit:** synthetic OURS/BUILD_LIST/LOAD_SET dicts → assert recall_buildlist / recall_loadset / precision and the `pruned_superset` vs `recall_defect` split of `missing`, plus `resolve_required` re-labelling.

---

## 10. File manifest

```
src/eval/language_package_eval/go/
  __init__.py
  gomod.py            # §3  offline parser + authority-ladder module_closure
  run_ours_go.py      # §4  OURS extractor -> per-repo JSON
  oracle.py           # §5  Docker `go list -m -json all` (build-list) + `go list -deps -json` (load-set)
  compare_go.py       # §6  recall_buildlist / recall_loadset / precision + buckets
tests/eval/language_package_eval/go/
  __init__.py
  test_gomod.py       # §9  parser + module_closure (inline tmp_path fixtures)
  test_compare_go.py
  test_oracle_go.py   # Docker-gated (pure parse_go_list_m test + gated integration)
```
