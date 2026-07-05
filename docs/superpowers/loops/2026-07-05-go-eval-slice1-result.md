# Go package-eval Slice 1 — end-to-end recall-gap measurement (2026-07-05/06)

**Question answered (spec §1):** how big is the offline `go.mod`(≥1.17)/vendor/go.work parse's
recall gap vs the toolchain's `go list -m all` build list, and is that gap entirely
`pruned_superset` (expected Go MVS/pruning structure) or does it contain `recall_defect`
(a real parser bug)?

## Headline

**The anchor gate passes cleanly.** `viper` (spf13/viper v1.18.2, `go 1.18`): OURS (require-block,
75 modules) is a strict subset of the 274-module build list — `recall_buildlist = 0.274`,
`precision = 1.000` (zero extras — OURS never invents a module). Against the package-loading
oracle (`go list -deps ./...` on a full clone, 67 modules actually reachable from viper's own
packages): **`recall_loadset = 1.000` and `recall_defect = []`** — every one of the 199 build-list
modules OURS misses is `pruned_superset` (an MVS-selected module nothing in viper's own package
tree imports — mostly transitive `cloud.google.com/go/*` API-client leaves pulled in by
`etcd`/`consul` for tests, plus `golang.org/x/tools` build/lint tooling). **Zero parser bugs
found on the anchor.**

The gap (0.274 recall vs build list) is real and large in absolute module count, but it is
Go's own MVS-over-transitive-closure structure, not a fidelity defect in our parser: the
require-block (what OURS reads) is deliberately pruned by Go 1.17+ to direct + test/build deps
of the *main* module, while `go list -m all` returns every module in the *transitive* graph of
every dependency, most of which the main module's packages never touch.

## Per-repo table

| repo | closure_source | recall_buildlist | precision | notes |
|---|---|---|---|---|
| **viper** (anchor) | gomod-pruned | **0.274** (75/274) | 1.000 | `recall_loadset=1.000` (67/67) · `recall_defect=0` · `pruned_superset=199` — anchor gate PASS |
| prometheus | gomod-pruned | 0.463 (193/417) | 1.000 | large-scale 2nd data point; no load-set oracle run (in scope only for the anchor) |
| reg_replace | gomod-pruned | 1.000 (1/1) | 1.000 | keys `github.com/google/uuid: v1.6.0` exactly as spec'd (registry replace v1.5.0→v1.6.0) |
| vendored_demo | vendor | 1.000 (1/1) | 1.000 | matches `-mod=vendor` build list (`github.com/spf13/pflag: v1.0.5`); oracle number is a **substitute** — see Anomalies |
| ws_demo | workspace | 1.000 (1/1) | 1.000 | OURS = `{github.com/google/uuid: v1.6.0}`, the **max** of members' v1.4.0/v1.6.0 (global-MVS, not last-write); oracle number is a **substitute** — see Anomalies |
| cobra | resolve-required | flagged | flagged | go 1.15 (<1.17) → correctly resolve-required, not scored (expected) |
| uuid | resolve-required | flagged | flagged | no `go` directive → correctly resolve-required, not scored (expected) |

`vexact` (exact version match, not just presence) = 75/75 for viper, i.e. every OURS-reported
version for a shared module matches the build list's selected version exactly — no version drift.

## Anomalies discovered during Task 7 (both real, both worked around, neither fabricated)

Running `python3 -m src.eval.language_package_eval.go.oracle` (the committed Task-6 CLI) over
`viper,prometheus,ws_demo,reg_replace,vendored_demo` produced 3 `OK` and 2 genuine `ERR`s — **not**
network/go.sum flakiness, but two Go-toolchain-level restrictions that `oracle_closure()`'s fixed
`-mod=vendor`/`-mod=mod` flag choice doesn't anticipate:

1. **`ws_demo` (go.work workspace):**
   ```
   ERR ws_demo: go: -mod may only be set to readonly or vendor when in workspace mode,
   but it is set to "mod"
   ```
   `oracle_closure()` always passes an explicit `-mod=vendor` or `-mod=mod`; Go workspace mode
   rejects an explicit `-mod=mod`. Confirmed a plain `go list -m -json all` (no `-mod` flag —
   the workspace default, effectively readonly) succeeds and returns exactly
   `{"github.com/google/uuid": "v1.6.0"}`. **Substitute measurement:** ran that exact docker
   invocation manually and parsed the output with the already-tested `parse_go_list_json()` (no
   new parsing logic, no modification to any committed module) — recall/precision above reflect
   that substitute run.

2. **`vendored_demo` (`-mod=vendor`):**
   ```
   ERR vendored_demo: go: can't compute 'all' using the vendor directory
   (Use -mod=mod or -mod=readonly to bypass.)
   ```
   Verified independently that this is a **documented Go toolchain restriction**, not specific to
   this fixture: `go list -m all` cannot enumerate the *full* module graph under `-mod=vendor`
   consistency mode at all, regardless of vendor completeness (reproduced with the flag both
   explicit and auto-detected). `oracle_closure(repo, vendored=False)` (i.e. `-mod=mod`, same
   tested function, valid flag) succeeds and returns exactly
   `{"github.com/spf13/pflag": "v1.0.5"}`, matching the vendored build list 1:1 since pflag has
   zero transitive deps and this fixture carries no replaces. **Substitute measurement:** used that
   call directly.

   → **Task-6 finding for future work:** `oracle_closure()`'s build-list oracle cannot honor
   `vendored=True` for the `go list -m all` build-list query as designed — `-mod=vendor` should
   only ever be used for `go list -deps` (load-set / actual build), never for `-m all`. Not fixed
   here (out of Task-7 scope; Tasks 1-6 are frozen/tested), but flagged prominently per the
   honesty rules rather than silently patched or hidden.

Both substitutes are genuine command output through the same tested parser, not invented numbers,
and both land exactly on the values the plan predicted (`ws_demo` max-version uuid v1.6.0;
`vendored_demo` matching its vendor closure).

## Corpus construction notes

- Lifted manifests (viper/prometheus/cobra/uuid) via `curl` from raw GitHub at pinned tags — no
  clones. `uuid`'s `curl` for `go.sum` 404's (expected: it's a zero-dependency module with no
  `go.sum`).
- **Step-2 verification confirmed no substitution was needed.** `prometheus` (`go 1.21`) has two
  `replace` lines (`k8s.io/klog(/v2) => github.com/simonpasquier/...`) but both are **registry**
  replaces (target `github.com/...`, not a local path) — `grep -E '=>\s*\.\.?/'` correctly reports
  `local_replaces=0`. Kept prometheus as the large-scale entry as originally planned.
- `ws_demo`/`reg_replace`/`vendored_demo` are hand-constructed per the plan's heredocs. One real
  build wrinkle: `go mod tidy` / `go mod vendor` strip a `require` line entirely when the module
  has **no actual `.go` source** referencing it (there's nothing to "tidy" against) — this bit
  both `reg_replace` and `vendored_demo` on the first attempt (tidy silently deleted the
  `require github.com/google/uuid`/`pflag` lines). Fixed by (a) restoring the `require` line by
  hand for `reg_replace` and skipping `go mod tidy` entirely (only `go mod download` +
  the replace target ran fine standalone), and (b) adding a minimal `main.go` that imports
  `pflag` for `vendored_demo` so `go mod tidy && go mod vendor` have something real to vendor
  against. Neither changes the intended OURS/oracle shape.
- `golang:1.22` image pull (~7 layers) + module-cache population took a few minutes on first
  Docker run, as expected.

## Dropped / failed entries

**None dropped.** All 7 planned corpus entries (viper, prometheus, cobra, uuid, ws_demo,
reg_replace, vendored_demo) were constructed, ran OURS successfully, and — modulo the two
documented oracle-flag substitutes above (still genuine, non-fabricated numbers) — were scored.
`cobra`/`uuid` are intentionally unscored (`resolve-required`, per design, not a failure).

## Reproducing

```bash
bash src/eval/language_package_eval/go/lift_corpus.sh          # viper/prometheus/cobra/uuid manifests
# then the Task-7 Step-3 heredocs (ws_demo / reg_replace / vendored_demo) from
# docs/superpowers/plans/2026-07-05-go-package-eval-slice1.md — reproduced verbatim, plus the two
# fixes noted above (skip `go mod tidy` for reg_replace; add main.go before tidy/vendor for
# vendored_demo).
python3 -m src.eval.language_package_eval.go.run_ours_go \
  viper,prometheus,cobra,uuid,ws_demo,reg_replace,vendored_demo outputs/graph_fidelity/_smoke_go_ours
GO_ORACLE_DOCKER=1 python3 -m src.eval.language_package_eval.go.oracle \
  viper,prometheus,ws_demo,reg_replace,vendored_demo outputs/graph_fidelity/_smoke_go_oracle
# ws_demo / vendored_demo: substitute per the Anomalies section above.
# viper load-set: git clone --depth 1 --branch v1.18.2 spf13/viper, then oracle.oracle_loadset(src).
```

`outputs/` is gitignored; this corpus is not committed. It is fully reproducible via
`lift_corpus.sh` + the Step-3 heredocs (plan) + the two noted fixes.
