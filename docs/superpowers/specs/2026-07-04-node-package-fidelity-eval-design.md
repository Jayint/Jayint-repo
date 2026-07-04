# Node/JS Package-Fidelity Eval — Design

**Date:** 2026-07-04
**Status:** Design (awaiting review). Buildable as written.
**Branch:** john-planner-v3-core-autoresearch (eval harness); OURS provider on `ecosystem-provider-seam` worktree.
**Mirrors:** the Python package-layer eval — `scripts/eval/graph_fidelity/coverage.py` (`build_graph_construction_only`), `run_ours_pkg.py`, `outputs/graph_fidelity/pkg_lock_ab/compare_pkg.py`.
**Extends:** `docs/superpowers/specs/2026-06-27-ecosystem-provider-seam.md` (the Node provider under test) and its handoff `HANDOFF-ecosystem-provider-seam.md` §"Validated state" (the axios 54-platform-optional finding, reproduced live below).

---

## 1. What this eval measures, and why it is a *different* question than Python

The Python eval answers **"does our construction-only PACKAGE closure capture the real working dependency set?"** against an agent-configured `pip freeze` oracle. Its 14-repo headline was **recall 0.940 / precision 0.505** — the *story was recall*, because Python is a **RESOLVE**-mode ecosystem: manifests are loose (`requests>=2`, `[project.optional-dependencies]`, PEP 735 groups), so the interesting failure is *under-coverage* — deps the loose manifest never named.

Node is a **LOCK**-mode ecosystem. `package-lock.json` **is** the fully-resolved transitive closure (`lockfile.py` docstring: "The lockfile IS the fully-resolved transitive closure"), and `npm ci` installs *exactly and only* what the lock pins — no resolution step, no version drift. So OURS (the whole lockfile) should have **near-perfect recall** against the installed tree by construction. Recall is a solved problem here.

The Node story is **precision**, and it has one dominant cause: **the lockfile lists every platform's optional binary, but `npm ci` on one platform installs only the matching subset.** OURS (whole-lockfile) over-includes the other-platform binaries. The eval's job is to *measure that precision gap and validate the fix* (os/cpu/libc filtering). This is the mirror image of Python: same two metrics, opposite failure mode.

> **Verified live (this session), not hypothesized.** A repo with one prod dep `esbuild@0.21.5` + one dev dep `rollup@4.18.0`: the current Node provider emits **43 PACKAGE nodes**; `npm ci` in `node:20-slim` (linux/amd64, Debian glibc) installs **5**. Unfiltered precision = **5/43 = 0.116**. Filtering the lockfile by `os`+`cpu`+`libc` reproduces the installed set **exactly** (5/5). Full transcript in §7.

---

## 2. Method recap (both sides), mirroring the Python eval

| | Python eval (existing) | Node eval (this doc) |
|---|---|---|
| **OURS** | `build_graph_construction_only` → `{dist: version}` PACKAGE closure (pip, RESOLVE) | `NodeProvider.package_obligations` → `{npm-name: version}` PACKAGE closure (LOCK), **platform-filtered** |
| **ORACLE** | Sonnet agent configures container until `import <proj>` + `pytest --collect-only` pass → `pip freeze` | Sonnet agent runs `npm ci` until entry loads + tests *collect* → walk installed `node_modules/**/package.json` |
| **GATE** | `import <project>` + `pytest --collect-only -q` | `node -e "require('.')"` (or `main`/`exports`) + runner list/collect (`jest --listTests`, `vitest list`, `mocha --dry-run`, …) |
| **FREEZE** | `pip freeze` | walk `node_modules/**/package.json` → `{name: version}` (authoritative); `npm ls --all --json` for cross-check only |
| **Metrics** | recall, precision, version-agreement (exact + `major.minor`) on ∩, PEP503 names, own-dist excluded | identical, npm-name canonicalization, own-package excluded |
| **Seam gate** | byte-identical closures | byte-identical filtered closures |

Everything else is structurally the same: construction-only (no agent, no repair loop on OURS), pooled recall/precision across the corpus, per-repo JSON scorecards + a markdown report, divergence dump (`missing` = under-coverage, `extra` = over-install).

---

## 3. OURS extractor — `run_ours_node.py`

### 3.1 Runner shape (mirror `run_ours_pkg.py`)

`run_ours_pkg.py` is 76 lines: import the construction-only builder, loop the corpus, `extract(graph)` → `{packages: {name: version}, ...}`, dump per-repo JSON. The Node analogue is **smaller** because the Node provider is standalone (imports nothing from `python_deps`) and needs no container to construct — the lockfile parse is pure JSON.

```python
# run_ours_node.py  (lives in scripts/eval/graph_fidelity/node/, PYTHONPATH includes the ecosystems worktree src)
from ecosystems.node.provider import NodeProvider
from ecosystems.node.platform_filter import filter_for_target   # NEW, §4
from ecosystems.graph import NodeType

TARGET = {"os": "linux", "cpu": "x64", "libc": "glibc"}          # matches node:20-slim linux/amd64

def extract(repo_dir: str) -> dict:
    provider = NodeProvider()
    graph = provider.package_obligations(repo_dir)               # whole-lockfile closure
    kept = filter_for_target(graph, repo_dir, TARGET)            # os/cpu/libc filter (the fix under test)
    packages = {n.name: n.version for n in kept.nodes if n.type is NodeType.PACKAGE}
    return {
        "packages": packages,                                   # {npm-name: version}
        "packages_unfiltered": {n.name: n.version for n in graph.nodes if n.type is NodeType.PACKAGE},
        "package_count": len(packages),
        "runtime_node": provider.runtime_decision(repo_dir).version,
        "target": TARGET,
        # honest signals mirroring the Python 'unresolved'/'audit' flags:
        "native_frontier": sorted({n.name for n in kept.nodes if n.type in (NodeType.SYSTEM_LIB, NodeType.TOOL)}),
        "platform_dropped": sorted(set(...unfiltered...) - set(packages)),  # what the filter excluded
    }
```

**Why emit both filtered and unfiltered.** `packages` is the honest OURS side (what a target-platform install yields); `packages_unfiltered` lets the comparer report the *avoided* precision hit as a separate diagnostic (the ax{ios finding as a number), exactly like Python reports `audit_repaired` and `unresolved_imports` separately from the headline.

### 3.2 Standalone module vs wrapper

**Recommendation: a thin wrapper in the eval tree that imports the ecosystems module.** The provider (`package_obligations`) is standalone and construction-only *today* — no seam integration, no Docker, no `python_deps`. The wrapper's only jobs are (a) the corpus loop + JSON dump, and (b) applying the platform filter (§4). Do **not** run `python -m ecosystems <repo> --json` and parse stdout — importing `NodeProvider` directly is cleaner and gives typed access to `os`/`cpu`/`libc` for the filter.

### 3.3 Construction-only = free here (no container)

Python's OURS must build in a scratch container because it *probes* (import discovery, `pip install --dry-run`). Node's OURS is a pure lockfile parse — **no container, no network, deterministic**. This is a genuine simplification: `run_ours_node.py` runs in milliseconds and has no Docker dependency. (Docker is needed only for the ORACLE, §5.)

### 3.4 Dev-vs-prod scoping — **include devDependencies**

Mirror Python's *testability* scope. Python's oracle gate is `pytest --collect-only`, so test tooling counts. In Node:

- `npm ci` **installs devDependencies by default** (verified live: `rollup` was a `devDependency` and installed). Test frameworks (jest/mocha/vitest) live in devDeps.
- The lockfile marks each entry `"dev": true|false` (verified: `rollup` → `dev:true`, `esbuild` → `dev:false`).

So **OURS keeps both prod and dev** (no `dev` filter), and the ORACLE's `npm ci` also installs both → the two sides align. Emit `dev` as metadata per package so the comparer *can* slice prod-only if we ever want a runtime-only variant, but the headline scope is **dev-inclusive** to match `pytest --collect-only`.

> Edge case: an oracle running `npm ci --omit=dev` (production install) would desync. The oracle protocol (§5) pins plain `npm ci` (dev-inclusive) to match.

---

## 4. The platform filter — the one piece of new code (the central decision, §6 expands)

The provider currently emits **every** lockfile entry. The parser (`LockPackage` in `lockfile.py`) keeps `name, version, path, dependencies, has_install_script, dev, optional` — it **drops `os`/`cpu`/`libc`**. The filter needs those three fields, so:

**Step 1 — extend the parser** (`lockfile.py`): add `os: tuple[str,...]`, `cpu: tuple[str,...]`, `libc: tuple[str,...]` to `LockPackage`, populated from `entry.get("os"/"cpu"/"libc")`. Byte-safe: default to `()` so non-platform packages are unchanged.

**Step 2 — the filter** (`platform_filter.py`, new, pure):

```python
def _match(field: tuple[str, ...], target: str) -> bool:
    """npm os/cpu/libc semantics: allow-list with '!'-negation; empty = all."""
    if not field:
        return True
    allow = [x for x in field if not x.startswith("!")]
    deny  = [x[1:] for x in field if x.startswith("!")]
    if target in deny:
        return False
    return not allow or target in allow          # only-negations => allow

def keep(pkg, target) -> bool:                    # target = {"os","cpu","libc"}
    return (_match(pkg.os, target["os"])
            and _match(pkg.cpu, target["cpu"])
            and _match(pkg.libc, target["libc"]))
```

`filter_for_target(graph, repo_dir, target)` drops PACKAGE nodes failing `keep`, and (for cleanliness) any SYSTEM_LIB/TOOL node whose only in-edges came from a dropped package.

**All three axes are required.** os+cpu alone kept 6/5 (the extra was `@rollup/rollup-linux-x64-musl` — right os, right cpu, wrong libc). Adding `libc:["glibc"]` for the Debian target dropped the musl variant → exactly 5, matching `npm ci`. **Negation matters** in the wild (packages use `"os": ["!win32"]`); the `!`-handling above covers it.

> This filter is the Node analogue of Python's PEP 508 environment-marker evaluation (`sys_platform == 'linux'`, `python_version`) — the same concept (a dependency conditional on the target environment), a different syntax.

---

## 5. ORACLE protocol — the agent-configured working Node env

Mirror the Python oracle: **a Sonnet agent per repo configures a container until a WORKING gate passes, then we snapshot what's installed.** The agent's transcript is discarded; only the final installed tree + gate result are authoritative.

### 5.1 Container & install

- **Base image:** `node:20-slim` by default; if the repo pins `engines.node` / `.nvmrc` / `.node-version` / `volta.node`, use `node:<major>-slim` (the provider's `_node_version` already extracts this — reuse it so OURS and ORACLE agree on the runtime).
- **Platform:** `--platform linux/amd64`, Debian (glibc). This defines the filter target (`os=linux, cpu=x64, libc=glibc`). **State it explicitly** — the whole precision story is platform-relative, so OURS's `TARGET` and the oracle container MUST be the same triple. (An arm64 or Alpine/musl run is a different, equally-valid oracle; just keep the two sides matched.)
- **Install:** `npm ci` (lockfile present → deterministic). Only if a repo has no lock does the agent fall back to `npm install` — but the corpus (§8) is curated to *have* a committed v2/v3 lock, so `npm ci` is the norm.
- **Agent's remit:** fix install-blocking issues only — missing native toolchain (`apt-get install python3 make g++` for node-gyp builds), missing system libs (`libvips-dev` for sharp, `libcairo2-dev` for canvas), postinstall download failures (puppeteer/playwright/sharp fetch binaries at install time — the handoff §14 gotcha). The agent must **not** edit `package.json`/lock or add/remove deps — that would change the closure being measured. If `npm ci` cannot succeed without changing deps, the repo is **infeasible** (recorded, excluded from pooled metrics — mirrors Python's `feasible` label).

### 5.2 The GATE — a runner-agnostic "can the tests be collected?" check

The Python gate is `import <project>` + `pytest --collect-only -q`: the entry loads **and** every test module's imports resolve, *without running the tests*. The Node analogue has two parts:

**(a) Entry loads.** The project's main module resolves and its top-level `require`/`import` graph loads without `MODULE_NOT_FOUND`:
```bash
node -e "require('.')"                     # resolves package.json "main"/"exports"
```
For ESM-only packages (`"type":"module"` with no CJS `main`), use `node --input-type=module -e "import('.')"`. If neither entry is importable in isolation (common for CLIs/apps with side-effecting entrypoints), fall back to importing the package's declared `exports`/`main` file path directly, and on failure degrade to gate-part-(b)-only (record the degradation, like Python skips the import check when `top_level_import_name` returns None).

**(b) Tests collect/load — the uniform gate.** Research across the common runners shows there is **no single universal flag**, but every mainstream runner has a *list-or-dry-run* mode that loads test files (resolving their `require`/`import` graph) **without executing test bodies**. The uniform gate is: **run the repo's own test command in its runner's list/collect mode and require exit 0 with no module-resolution error.**

| Runner (detect via devDeps + config) | Collect-only invocation | Loads test modules? |
|---|---|---|
| **jest** | `jest --listTests` | Resolves config + testMatch; does **not** import test files. Pair with `jest --dry-run`-equivalent... |
| **jest (stronger)** | `jest --listTests` **then** `node -e "require.resolve()"` each, OR run with `--findRelatedTests`/`--onlyChanged` empty | see note below |
| **vitest** | `vitest list` (v1.2+) or `vitest --run --reporter=dot --passWithNoTests --testNamePattern=$^` | `list` collects (loads files to enumerate tests) |
| **mocha** | `mocha --dry-run` (v10+) | Loads spec files, registers tests, runs none |
| **node:test** | `node --test --test-name-pattern='$^'` (match nothing) or `--test-only` with no `only` | Loads test files, executes none |
| **tap** | `tap --dry-run` / `tap list` | Enumerates test files |
| **ava** | `ava --dry-run` (via `--tap` + no-match) | Loads test files |

**Note on jest.** `jest --listTests` only globs filenames — it does *not* load the files, so it does not prove their imports resolve (weaker than `pytest --collect-only`). The robust jest gate is `jest --listTests` to enumerate, then `node --experimental-vm-modules -e "require.resolve(f)"` per file, **or** the simpler and stronger `vitest list`-style approach: run jest with a name pattern that matches nothing (`jest -t 'a^'`) — this **loads** every test file (resolving imports, running `describe` blocks) but executes zero test bodies, which is the true collect-only semantic. **Recommendation:** prefer the "run with an impossible name filter" form (`-t 'a^'` / `--test-name-pattern='$^'`) as the primary uniform gate — it exercises module resolution like `pytest --collect-only` does; fall back to the native `list`/`--dry-run` flag only when the runner supports no name filter.

**Recommended concrete gate (runner-agnostic):**
```
GATE = (node -e "require('.')" OR main/exports loads)  AND
       (test command in <impossible-name-filter | list | --dry-run> mode exits 0
        with no MODULE_NOT_FOUND / missing-native-binary error)
```
Detect the runner from `package.json` `scripts.test` + which of jest/mocha/vitest/ava/tap/node is in devDeps; apply the matching row. Record which runner/flag was used per repo in the scorecard (like Python records `oracle_source`). The gate proves **collectability** (deps + native bindings resolve), *not* that tests pass — matching the Python eval's deliberate scope.

### 5.3 The FREEZE — what is actually installed

**Authoritative = walk `node_modules/**/package.json` → `{name: version}`.** This is the on-disk truth (the Node analogue of `pip freeze` reading site-packages):
```bash
find node_modules -name package.json | while read f; do
  node -e "const p=require('./'+process.argv[1]); if(p.name&&p.version) console.log(p.name+'@'+p.version)" "$f"
done | sort -u
```
Dedupe by `name@version` (a package can be installed at multiple paths under hoisting — same version → one entry; genuinely different versions → keep both, matching how the provider ids nodes with `npm:name@version`).

**Cross-check only = `npm ls --all --json`.** Report it for diagnostics but do **not** treat it as ground truth: `npm ls` walks the *logical* dependency tree and **errors** (`ELSPROBLEMS`, non-zero exit) on peer-dependency conflicts, extraneous packages, or `invalid`/`missing` markers — it describes what the lock *says should* be there, not what *is* on disk. The filesystem walk is immune to those failures and is what `require()` actually sees. **The node_modules walk is authoritative; `npm ls` is advisory.**

> Why the walk beats `npm ls` here specifically: the whole eval is about the *installed* set (post platform-filter), and `npm ls --all` re-expands optionals/peers from the lock — reintroducing exactly the platform-optional entries `npm ci` chose not to install. The disk walk already excludes them.

### 5.4 Oracle output JSON (mirror the Python oracle row)

```json
{ "repo": "...", "node_version": "20", "target": {"os":"linux","cpu":"x64","libc":"glibc"},
  "gate_passed": true, "runner": "vitest", "gate_cmd": "vitest list",
  "installed": {"esbuild":"0.21.5", "rollup":"4.18.0", "@types/estree":"1.0.5",
                "@esbuild/linux-x64":"0.21.5", "@rollup/rollup-linux-x64-gnu":"4.18.0"},
  "npm_ls_ok": false, "npm_ls_problems": ["extraneous: ..."] }
```
`installed` is the FREEZE (the `pip_freeze` analogue the comparer keys on).

---

## 6. THE central decision — platform-optional over-inclusion

**Question.** The lockfile lists all platform-optional binaries; `npm ci` installs only the matching subset. Should OURS (a) **filter** optionals by target `os`/`cpu`/`libc` (like Python's PEP 508 markers), or (b) leave OURS whole-lockfile and have the **comparer** treat platform-mismatched optionals as correctly-excluded (report them separately)?

**Recommendation: (a) filter OURS by the target triple — and *additionally* report the drop as a diagnostic.** Rationale:

1. **It is the real product fix, not an eval trick.** The handoff's own "Known refinements #1" is *"Optional/platform-aware obligations (install side) — parse Node os/cpu, filter platform-inapplicable, tag optional → non-blocking, so MISSING means a real blocker."* The eval exists to *drive and validate that fix*. If we hide the over-inclusion in the comparer, the provider still emits 43 nodes and still mis-certifies 38 of them as MISSING on install (the handoff's axios "57 missing" — 54 of which were platform binaries). Filtering in OURS makes the provider correct, and the eval proves it.

2. **`npm ci` semantics are deterministic and offline** — the filter is a pure function of lockfile fields (`os`/`cpu`/`libc`) + the target triple, needing no network and no resolver. Verified to reproduce `npm ci`'s selection **exactly** (5/5, §7). So there is no accuracy cost to filtering; it is not a heuristic.

3. **PEP 508 parity.** Python's OURS already evaluates environment markers for the target — filtering platform optionals is the identical operation for Node. Keeping the semantics parallel keeps the eval honest as an apples-to-apples cross-ecosystem comparison.

4. **Keep the diagnostic** (`packages_unfiltered` / `platform_dropped` in §3.1) so the report can state, per repo, *"filter avoided N platform-optional false-installs (precision 0.12 → 1.00)"* — turning the fix into a measured result, not a silent behavior.

**What the comparer still does (belt-and-suspenders).** Even with OURS filtered, tag any residual `extra` that carries an `os`/`cpu`/`libc` constraint as `platform_optional_extra` and report it in its own bucket — so a filter bug (e.g. an unhandled `libc` value, or a new `!`-negation form) surfaces as a labeled precision miss instead of hiding in the headline.

**Target-triple must be declared and matched.** The filter is only correct relative to one `(os, cpu, libc)`. OURS's `TARGET` and the oracle container's platform are the same triple, asserted at the top of both runners. Changing the target (e.g. Alpine/musl CI) means re-running both sides with the new triple — never comparing across triples.

---

## 7. Live verification (this session — real npm, real Docker)

Environment: `node v26.4.0`, `npm 11.17.0`, Docker present. Test repo: `package.json` with `dependencies:{esbuild:"0.21.5"}`, `devDependencies:{rollup:"4.18.0"}`; `npm install --package-lock-only` → `lockfileVersion 3`, 44 `packages` entries (43 non-root).

| Observation | Value |
|---|---|
| OURS today (whole lockfile), PACKAGE nodes | **43** (`esbuild`, `rollup`, `@types/estree` + **40** platform-optional `@esbuild/*` / `@rollup/rollup-*` / `fsevents`) |
| Lockfile entries with `os`/`cpu` constraint | 40 (all also `optional:true`) |
| `npm ci` on `node:20-slim` (linux/amd64, glibc), disk-walk installed | **5**: `esbuild@0.21.5`, `rollup@4.18.0`, `@types/estree@1.0.5`, `@esbuild/linux-x64@0.21.5`, `@rollup/rollup-linux-x64-gnu@4.18.0` |
| Unfiltered precision (OURS ∩ installed / OURS) | 5/43 = **0.116** |
| Filter by `os`+`cpu` only | 6 (keeps `@rollup/rollup-linux-x64-musl` — wrong libc) ✗ |
| Filter by `os`+`cpu`+`libc` | **5 — exact match to `npm ci`** ✓ |
| `fsevents` (darwin-only, `os:["darwin"]`) | in lockfile, **not installed** on linux → correctly dropped by filter |

This reproduces the handoff's axios finding (54 platform-optional binaries correctly excluded) on a minimal, controlled case, and additionally pins down that **`libc` is a required third filter axis** (the musl/glibc split), which the os/cpu-only framing in the handoff would miss.

---

## 8. Comparer — `compare_node.py`

Structurally identical to `compare_pkg.py` (recall/precision/version-agreement, canonical names, own-package excluded). Differences are npm-specific:

- **Canonicalization.** npm names are already lowercase and may be scoped (`@scope/name`). Canon = `name.strip()` (npm is case-sensitive-ish but registry-lowercased; do **not** apply PEP 503's `_`/`.`→`-` collapse — npm treats `-`, `_`, `.` as distinct). Keep the `@scope/` prefix intact.
- **Own-package exclusion.** Read `package.json` `name`; exclude it (and `project:<name>` never appears as a PACKAGE node anyway — the provider makes it a PROJECT node). Mirror `compare_pkg.project_names`.
- **Version agreement.** Exact + `major.minor` on the intersection (semver → same `mm()` helper works; `4.18.0` → `4.18`).
- **Metrics (unchanged definitions).**
  - `recall = |ours ∩ installed| / |installed|` — under-coverage. Expected ≈ **1.0** (LOCK mode).
  - `precision = |ours ∩ installed| / |ours|` — over-install. **The number this eval is built to move**: ~0.12 unfiltered → ~1.0 filtered.
- **Divergence buckets.** `missing` (installed − ours; real under-coverage — should be near-empty), `extra` (ours − installed; over-install), and a **new** `platform_optional_extra` sub-bucket: any `extra` node carrying `os`/`cpu`/`libc` — a filter escape, reported separately so it never inflates or hides in the headline.
- **Byte-identity seam gate.** Same as Python: an option to assert the OURS filtered closure serializes byte-identically to a golden — the zero-impact gate for when this provider gets wired behind the real `python_deps` schema. Compare the sorted `{name: version}` map (canonical JSON) so a Python-only run is provably unchanged.

Pooled recall/precision across the corpus (count-weighted, not per-repo-mean), same as `compare_pkg.py`'s `tot_recall_num/den`.

---

## 9. Node-specific pitfalls the design addresses

1. **optionalDependencies + os/cpu/libc platform filtering — §4, §6, §7.** The dominant precision effect. Resolved by filtering OURS on the target triple (all three axes); verified to match `npm ci` exactly.

2. **devDependencies scoping — §3.4.** Both sides dev-inclusive (`npm ci` default; gate is collect-only). Emit `dev` as metadata for an optional prod-only slice.

3. **peerDependencies (npm 7+ auto-install).** npm 7+ installs peer deps automatically into the tree, and the lockfile records them as normal `packages` entries → they appear in **both** OURS (lockfile) and the FREEZE (disk) → they compare consistently, no special handling. **Except** `peerDependenciesMeta.optional` + an unmet optional peer: it may be in the lock but not installed. Treat like any optional: if it carries no `os`/`cpu`, and it's in OURS but not installed, it lands in `extra` honestly (a genuine, small over-include we should observe and, if common, filter on the lock's `optional:true` + peer flag). **Recommendation:** do not special-case peers in v1; measure them, and only add a rule if the corpus shows a systematic peer-driven `extra`.

4. **Package-manager heterogeneity.** The provider parses `package-lock.json` v2/v3 **only** (`_LOCKFILES = ("package-lock.json",)`; pnpm/yarn "added later"). **Corpus is npm-lockfile-v2/v3 exclusively** (§10). yarn (`yarn.lock`) and pnpm (`pnpm-lock.yaml`) are **future** — note them as out of scope; a repo carrying only those is not eligible. (`detect()` scores them for provider *selection*, but `package_obligations` raises `FileNotFoundError` without `package-lock.json` — so the corpus filter is: committed `package-lock.json` present.)

5. **workspaces / monorepos.** Scope **per package**, not per repo root — a root `package.json` with `workspaces` has a single hoisted `package-lock.json` at the root but multiple publishable packages. Mirror the Python `vizro` monorepo-contamination lesson (root pyproject declares no runtime deps → 0 PACKAGE nodes artifact). **Recommendation:** exclude workspace-root monorepos from the corpus (§10); if one is included, point `repo_dir` at the package under test and its nearest lock, and record a concern if the lock is the shared root lock (the closure will include sibling-workspace deps — a known over-include, flag it).

6. **Multiple versions of one package (hoisting).** The lock can install `lodash@4` at root and `lodash@3` nested; the provider already ids nodes by `npm:name@version` + exact `path`, so both survive as distinct nodes. The FREEZE disk-walk also sees both. They compare correctly as long as the comparer keys on `name@version` (not `name`) for the version-agreement step but on `name` for membership — keep membership by canonical name, and when a name maps to multiple versions on either side, compare the *version sets* (report a version-set mismatch rather than forcing one).

7. **postinstall side-effects (puppeteer/playwright/sharp).** A package can be lock-satisfied but its *binary* fetched at install time (network) — the handoff §14 gotcha. This does not affect the **package-set** comparison (the package's own `package.json` is on disk either way), so recall/precision are unaffected. It *can* fail the ORACLE gate (a missing native binary → gate fails). That's correct: the agent must resolve it (or the repo is infeasible). No comparer change; note it as a gate-feasibility factor.

---

## 10. Corpus — Node `medlarge15` analogue

Mirror the `medlarge15` curation rule: ~10–15 diverse real-test repos, **committed `package-lock.json` (v2/v3)**, **no yarn/pnpm-only**, **no giant workspace monorepos**, real test suites (a `scripts.test` that a runner can collect). Aim for a spread of: pure-JS libs (recall sanity), platform-optional-heavy build tools (the precision story), and native-addon repos (node-gyp frontier).

**Candidate list (verify each has a committed `package-lock.json` + `scripts.test` before locking the corpus — marked ⚠ to-verify):**

| Repo | Why chosen | Notes |
|---|---|---|
| `sindresorhus/execa` ⚠ | small, real tests (ava), few deps | recall/precision baseline |
| `chalk/chalk` ⚠ | tiny, zero-native, pure JS | recall = 1.0 sanity |
| `expressjs/express` ⚠ | classic, mocha tests, no native | mid-size closure |
| `lodash/lodash` ⚠ | large pure-JS, many internal modules | version-set/hoisting check |
| `date-fns/date-fns` ⚠ | large, jest | jest collect gate |
| `axios/axios` ✓ | **platform-optional heavy** (rollup toolchain) — the handoff's 54-binary case | the precision headliner |
| `evanw/esbuild` (JS wrapper dir) ⚠ | `@esbuild/*` platform binaries | filter target case |
| `vitejs/vite` ⚠ | rollup/esbuild optionals, vitest | precision + vitest gate (may be pnpm — verify) |
| `websockets/ws` ⚠ | optional native (`bufferutil`, `utf-8-validate`) | optional-native frontier |
| `node-fetch/node-fetch` ⚠ | small, jest | ESM entry (`require('.')` → import fallback) |
| `Automattic/node-canvas` ⚠ | **native addon** (`libcairo`/`libpango` via `NODE_NATIVE_APT`) | syslib frontier + gate |
| `sharp` (lovell/sharp) ⚠ | native (`libvips`), postinstall binary | postinstall gate-feasibility case |
| `winstonjs/winston` ⚠ | mid, mocha, pure JS | filler diversity |
| `pinojs/pino` ⚠ | logging, tap runner | node:test/tap gate coverage |
| `moment/moment` ⚠ | large legacy, karma/grunt — **maybe reject** if no clean `scripts.test` collect | diversity, verify test runner |

**Selection procedure (scripted, before locking):** for each candidate, `gh api repos/<r>/contents/package-lock.json` (exists?) → read `lockfileVersion` ∈ {2,3} → `package.json` has `scripts.test` and a detectable runner → not a workspace root (`"workspaces"` absent, or point at a leaf). Keep the first ~12 that pass, ensuring ≥3 platform-optional-heavy and ≥2 native-addon repos so both frontiers are exercised. (Network access was not used to confirm these live in this session — hence the ⚠; the procedure is deterministic and cheap to run.)

---

## 11. Expected result shape (the hypothesis this eval tests)

Because Node is **LOCK** mode (`npm ci` installs exactly the lock):

- **Recall ≈ 1.00** across the corpus — the lockfile *is* the installed set; OURS can only miss a package if the filter over-drops (a filter bug) or a postinstall adds something not in the lock (rare). Recall being anything below ~0.98 is a **bug signal**, not an ecosystem property. (Contrast Python: recall 0.94 was the *achievement*, because loose manifests genuinely omit deps.)
- **Precision: the whole story.**
  - *Unfiltered* OURS: precision **low and variance-heavy** — ~0.12 on esbuild/rollup-class repos (measured §7), ~1.0 on pure-JS repos (chalk/express). Pooled, dominated by the platform-optional-heavy repos → expect pooled precision well under 0.5, *worse* than Python's 0.505 on the toolchain-heavy corpus.
  - *Filtered* OURS (the fix): precision **→ ~1.00**, with residual misses only from (a) unmet optional peers and (b) any `libc`/`!`-negation form the filter doesn't yet handle — both surfaced in the `platform_optional_extra` bucket.
- **Version agreement ≈ 1.00 exact** on the intersection — LOCK mode pins exact versions, and `npm ci` installs those exact versions, so `vexact` should be near-total (unlike Python's "near-exact"). A version mismatch is a bug signal.

**So the Node eval's contribution is a precision refinement, measured:** it demonstrates the os/cpu/libc filter takes precision from ~0.12 → ~1.0 on the repos where it matters, with recall pinned at 1.0 — the exact mirror image of the Python eval (where recall was the story and precision the loose end). Reporting both ecosystems side-by-side makes the cross-ecosystem claim concrete: *same construction-only closure method, opposite dominant failure mode, each fixed by the ecosystem's environment-conditional-dependency mechanism (PEP 508 markers ↔ npm os/cpu/libc).*

---

## 12. Build order (TDD, mirrors the Python eval's module layout)

1. **Parser extension** (`lockfile.py`): add `os`/`cpu`/`libc` to `LockPackage`. Unit test on the esbuild fixture (assert `@esbuild/linux-x64.os == ("linux",)`, `@rollup/...musl.libc == ("musl",)`). RED→GREEN.
2. **`platform_filter.py`** (pure): `_match` (incl. `!`-negation, empty=all) + `filter_for_target`. Unit test: esbuild fixture + target `{linux,x64,glibc}` → exactly the 5 (regression-lock the §7 numbers).
3. **`run_ours_node.py`**: corpus loop → per-repo JSON `{packages, packages_unfiltered, platform_dropped, ...}`. No container.
4. **Oracle agent + `run_oracle_node.py`**: `node:20-slim --platform linux/amd64`, `npm ci`, runner-detect + collect gate (§5.2), disk-walk FREEZE (§5.3), per-repo JSON. Docker integration, guarded by a `_docker_available()` check (copy the helper from `coverage.py:516`).
5. **`compare_node.py`**: recall/precision/version-agreement + `platform_optional_extra` bucket + pooled totals + byte-identity option (adapt `compare_pkg.py` verbatim; swap canon + own-name).
6. **Corpus lock-in** (§10 procedure) + first full run + report.

Pure logic (1,2,3,5) is unit-tested and container-free; only the oracle (4) needs Docker + the Sonnet agent — exactly the Python eval's split (pure scorer, guarded container probe).
