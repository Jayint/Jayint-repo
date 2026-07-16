# Handoff: graph-construction fixes — 3 landed, 2 dead, 4 need a second pass

**As of 2026-07-13.** Branch `john-v3-multi-lang`. **NOTHING IS COMMITTED — every change is in the working tree.**

---

## 0. Why these fixes exist (the measurement that motivated them)

We built a **gold denominator** (48/50 repos certified, 276,500 pytest node-ids, `/opt/manifest_out_py50/rat_python50_gold.json` on `root@167.233.64.96`), then rebuilt **v3's own `setup.sh` at gold's pinned commit** and ran gold's own collect command inside it (Tier-2, `/opt/manifest_out_py50/tier2_v3.json`).

Result: **v3 produced NO working environment on 12 of 48 repos — 193,870 gold tests, 70% of the pool.** Not a measurement artifact: v3's own `_result_row.json` already says `error`/`build_failed` on every one of them. Where it *does* build, it is near-perfect (16 SHA-aligned repos, mean EBSR **0.809**, 13 of them a clean 1.000 with zero collection errors).

So v3 is **bimodal**: excellent when it builds, total zero when it doesn't. Every fix below targets a cause of the zeros. **Resolution and pinning are sound** — the failures are in *discovery* (what the resolver is handed) and *rendering* (what the script does with it).

---

## 1. Status board

| # | fix | file | status |
|---|---|---|---|
| **A1** | `config_scan` now recognises `os.environ.setdefault` | `config_scan.py:34` | ✅ **LANDED, works** |
| **B2** | test-scoped extras default-included | `roots.py:187` | ✅ **LANDED, codex-approved** |
| **B5** | capstone: installability check + non-fatal installs | `populate.py` | ✅ landed, codex review **pending** |
| **A2/B4** | requirements glob + recursive walk | `evidence.py:388,444` | ⚠️ **LANDED BUT UNSAFE** — see §3 |
| **C1** | `[tool.uv.sources]` / `[tool.uv.workspace]` | `evidence.py:180-291` | ⚠️ **LANDED BUT INCOMPLETE** — see §4 |
| **B6** | resolve wall-clock budget | `resolve.py:142-183,362` | ⚠️ **LANDED BUT INEFFECTIVE** — see §5 |
| **B3** | declared rung in repair ladder | `repair.py` | ❌ **REVERTED — unsound.** §6 |
| **B1** | CONFIG needs → Dockerfile `ENV` | `build_script.py`, adapter | ❌ **INERT — never fires.** §7 |

**Test baseline:** `python3 -m pytest tests/depgraph/ -q` → **1497 passed** (was 1433).

🔴 **`python3 -m pytest tests/` gives 64 failures — PRE-EXISTING, NOT OURS.** `tests/depgraph/*` do a bare `from conftest import FakeExecutor`; in a full-tree run `tests/ecosystems/conftest.py` (committed at `9fbc48b`) shadows it on `sys.path`. **The full suite cannot be run in one pass on this repo.** Use per-directory runs. Do not "fix" this by touching our files.

---

## 2. What was reviewed, and how

Three sonnet subagents implemented with strict TDD under **disjoint file ownership** (they cannot be run in parallel on overlapping files — they will clobber each other). Then **`codex exec -m gpt-5.6-terra`** reviewed each scoped diff. Codex was right every time it disagreed with us, including about my own code.

Reviews are on disk:
`/private/tmp/claude-501/.../scratchpad/review/{codex-discovery.txt, codex-config.txt}` and the patches `fix-*.patch`.
(Regenerate: `git diff -U15 -- <files> > x.patch; codex exec -m gpt-5.6-terra --skip-git-repo-check "<prompt>"`.)
⚠️ Do NOT use `codex review --uncommitted` — it sweeps the user's unrelated `bench/` + `src/eval/graph_quality/` WIP into the review.

---

## 3. 🔴 A2/B4 — recursive requirements walk (LANDED, UNSAFE — FIX FIRST)

**What it fixes:** `evidence.py:_discover_requirements_files` said *"never a full-tree walk"* and globbed `requirements*.txt` (prefix-anchored) only at root + `requirements/`, `tests/`, `test/`, `docs/`.
Consequence: **ArchipelagoMW/Archipelago produced ZERO pip packages** — its deps live in ~84 `worlds/*/requirements.txt` + `WebHostLib/requirements.txt`, and even root `ci-requirements.txt` was missed by the prefix glob. Gold succeeded by looping `for f in worlds/*/requirements.txt; do pip install -r "$f" || true; done`.

**What landed:** substring match (`"requirement" in name`) + bounded `os.walk`, pruned by a new `_REQUIREMENTS_SKIP_DIRS` (`evidence.py:388`), capped at 500 files (`:401`, overflow recorded in `collection_errors`).
Good call by the agent: it **refused** to reuse `scan.py:SKIP_WALK_DIRS` — that set excludes `docs`/`tests`/`test`, which is exactly where requirements files live, and an existing test requires `docs/requirements.txt` to be found.

### 🔴 CODEX (HIGH): the walk POISONS the root set
`_REQUIREMENTS_SKIP_DIRS` does **not** prune `vendor`, `third_party`, `external`, `fixtures`, `testdata`, `examples`.
- `tests/fixtures/upstream/requirements.txt` → ingested as an **included dev/test group**
- `examples/foo/requirements.txt` → ingested as a **runtime root**

These become real resolver roots. That is strictly worse than under-discovery: it injects junk dependencies into every repo with a fixtures dir.

### 🟡 CODEX (MEDIUM): the 500-cap boundary
`evidence.py:484` reports truncation and stops *at* file 500 even when there are exactly 500. Worse: **lexically-early vendored/fixture files can consume the cap before the real `worlds/*` files are reached** — the exact repo this fix exists for.

### TODO
1. Add `vendor`, `vendored`, `third_party`, `thirdparty`, `external`, `fixtures`, `testdata`, `test_data`, `examples`, `example`, `sample`, `samples`, `site-packages`, `node_modules` to `_REQUIREMENTS_SKIP_DIRS`.
2. Fix the off-by-one at the cap boundary; add an exact-500 test.
3. Consider sorting/prioritising so the cap can't be eaten by junk before real files.
4. Re-run codex on the new diff.

---

## 4. 🔴 C1 — `[tool.uv.sources]` (LANDED, INCOMPLETE — HIGHEST CEILING)

**What it fixes:** `resolve_closure` writes a **synthetic throwaway** `pyproject.toml` (`name = "depgraph-resolve-root"`, `resolve.py:150`) from the declared roots and runs `uv lock` from scratch. **It never reads the repo's own `uv.lock`.** PostHog declares:
```toml
[tool.uv.workspace]  members = ["tools/hogli"]
[tool.uv.sources]
hogli = { workspace = true }                       # NOT on PyPI
infi-clickhouse-orm = { git = "...", rev = "..." }
pytest-split = { git = "...", tag = "..." }
```
`hogli` was written out as a plain PyPI name → `uv lock` fails → retry ladder can't attribute it → returns `[], []` → **ZERO packages, 2,224s of a 2,400s budget burned, 77,642 gold tests lost** (the largest repo in the corpus).

**What landed:** `evidence.py:180-291` re-tags any dep whose `[tool.uv.sources]` entry has `workspace`/`git`/`url`/`path` as **`kind="uv_non_pypi_source"`**, which `roots._in_test_scope` drops via its unknown-kind `return False` fallthrough.
⚠️ **Fragile coupling:** that sentinel works *only because* `_in_test_scope` falls through to `False`. It is not an explicit contract. If anyone adds a permissive default there, non-PyPI deps silently reach uv again. **Verified intact after B2's edit — re-verify after any `roots.py` change.**

### 🔴 CODEX (HIGH): sentinels re-enter through Phase-A repair
`build.py:561` (my wiring) passed **every** declared name — including sentinels — into the repair ladder, and `build.py:420` appends an accepted candidate as a **bare PyPI root**. A git-pinned `acme-sdk` whose *public* PyPI namesake exposes `acme_sdk` gets "repaired" into the **wrong public package**.
**→ ALREADY FIXED** (`build.py:561` now filters `kind != "uv_non_pypi_source"`). Keep that filter.

### 🔴 CODEX (HIGH): dropping git/url deps is NOT "safe"
The agent called exclusion "recall-conservative but correct". Codex disagrees, correctly: the dep is silently **absent** with no missing Package node, and the project install is `--no-deps` (`populate.py:60`), so nothing recovers it. PostHog's `pytest-split` is a *test* dependency — dropping it yields an environment that looks fine and cannot collect.

### 🔴 CODEX (HIGH): `index =` sources still resolve from public PyPI
`evidence.py:216` deliberately KEEPS `foo = { index = "internal" }` as a normal dep, but the synthetic project never carries the index config → resolves from **public PyPI**, or a same-name public package. `test_evidence.py:171` asserts this wrong behaviour.

### TODO — this is the architectural decision
**Minimum:** carry the source spec through instead of dropping it — emit `[tool.uv.sources]` into the synthetic pyproject so git/workspace/index deps resolve as the repo intends.
**Maximum (recommended, and what gold does):** when the repo has a lockfile, **`uv export --frozen --no-emit-project`** and parse *that*, instead of re-deriving. This sidesteps workspace/git/index entirely.

⚠️ **This is a paper-framing decision, not just engineering.** The architecture note already says the graph is a *replayer, not a resolver* — under that framing a lockfile is simply the highest-trust evidence source. But it changes the story from "we solve the environment" to "we replay the repo's solve and certify it." **Get the user's call before building it.**

---

## 5. ⚠️ B6 — resolve wall-clock budget (LANDED, INEFFECTIVE)

Ladder was bounded by attempt count only, never time; returned `[], []` on exhaustion. Landed: `DEPGRAPH_RESOLVE_LADDER_BUDGET_S` (default 300s), reduced-root fallback, `_log_ladder_exhaustion`. The agent also found a **real pre-existing bug**: the fallback retried the *full* original root list even after the ladder had reduced it (`resolve.py:421`).

🔴 **CODEX (HIGH): the budget cannot interrupt an in-flight `uv lock`.** It is checked only *between* attempts (`resolve.py:362`); the subprocess itself runs with the executor's fixed 300s default (`:369`). A 10s budget can still burn 300s inside one lock, plus an unbounded compile fallback. The test only expires time *before* the first subprocess — **it does not prove what it claims.**
🟡 MEDIUM: `current or roots` (`:421`) restores all known-bad roots when *every* root was dropped.

**TODO:** pass the remaining budget as the subprocess timeout; fix the empty-`current` case; write a test where time expires *during* an attempt.

---

## 6. ❌ B3 — declared rung in the repair ladder (REVERTED — do not retry naively)

The repair ladder can only **guess** a distribution name from an import (mechanical transforms + a 16-entry table); it never consults the manifest. Idea: propose a distribution the repo **already declares**.

**Why it was reverted — read before re-attempting:**
1. `choose_provider` grounds **all** candidates and ignores their order; ≥2 confirms → `AMBIGUOUS` ("never pick a variant"). So adding a declared candidate **changes no verdict**. The rung is inert as a candidate source.
2. Making a declared confirm *break* the tie is **unsafe**. A missing import's provider is, by construction, one the root filter **excluded** (anything in scope became a root, was installed, and would not be missing). The only declarations the ladder can see are the **gated** ones — and gated is exactly where mutual exclusion lives:
   `cpu = ["foo"]`, `gpu = ["python-foo"]`, `import foo`, both wheels provide `foo` → the tie-break resurrects one arm of a mutually-exclusive pair from an extra the repo never activated.
3. `declared_metadata_match` matches by **normalized name equality**, so it never fires for `yaml`→`PyYAML` or `psycopg2`→`psycopg2-binary` — the cases that actually need help.

The plumbing (`declared_candidates`, `generate_candidates(declared_package_names=...)`, the `build.py` wiring) is **kept**, with a `⚠ KNOWN LIMITATION` docstring. A correct design needs a real pass, not a wider matcher.

---

## 7. ❌ B1 — CONFIG needs → Dockerfile `ENV` (INERT — the wire is missing)

**Why it matters:** 2,386 unrendered config "needs" across 40 repos. Each renders as `# (no command — propose a governed block)`. `django-oauth-toolkit` collection crashes with pytest **exit 4** (`ImproperlyConfigured: DJANGO_SETTINGS_MODULE`) → **0/557**; gold sets `ENV DJANGO_SETTINGS_MODULE=tests.settings` and gets all 557.

**What landed (correct, and unreachable):** `build_script._need_block` emits `#@config-env VAR=value` when the CONFIG node's `chosen_fix` encodes a value; `multi_docker_eval_adapter._render_dockerfile` bakes those into `ENV` lines **before** the `RUN bash setup.sh` step. (`export` inside `setup.sh` dies with the RUN layer — `ENV` is the only route that survives to the later `docker exec pytest`. This part is right.)

**🔴 Why it never fires:** the production minter is `classify_services_clean.py:141 _config_nodes` (**plural**), which builds `NodeSpec(id=f"config:{var}", type="Config", promotion="hint", ...)` — it reads only `scan_env_reads` (the **names**), never `scan_env_defaults` (the **values**), and sets **no `chosen_fix`**. Agent 3's `_config_node` (**singular**) has **zero callers**.

**TODO — and there is a design constraint:** `NodeSpec` has **no `chosen_fix` field, deliberately** — *"the LLM proposes DATA, never shell"* (`patch.py:19`). So do **not** add one. `NodeSpec.data: dict` is merged into `Node.data`; carry the value there (`data={"env_value": ...}`) and have `build_script` read `node.data`, keeping the recipe-as-data invariant. `scan_env_defaults` is **already imported** in `classify_services_clean.py` — it is simply unused.

Files to touch: `src/envstate/classify_services_clean.py` (+ `build_script.py` to read `data` instead of `chosen_fix`). Nobody owned these; no conflict.

---

## 8. Landmines

1. **SHARED BRANCH, user WIP present** (`bench/`, `src/eval/graph_quality/`, `.context/`). **Never `git add -A`/`.`/`-u`, never `git stash`, never commit files you did not author.**
2. **Never run parallel implementer agents on overlapping files.** Partition ownership explicitly; make them *report* required changes in files they don't own rather than reach across.
3. **A passing test proves nothing about reachability.** Two of three agents shipped code that passes its own tests and is dead in production (B3, B1). Trace the production call path before believing any fix.
4. **Watch for tests that construct states production cannot reach** (my B3 test hand-built a `Candidate` `generate_candidates` can never emit). Codex caught this; the suite never would.
5. **Cross-agent composition bugs are real** — B3's wiring + C1's sentinel produced a wrong-package install that neither change had alone.
6. `pytest tests/` is broken repo-wide (conftest shadowing). Use `pytest tests/depgraph/`.

## 9. Recommended order

1. **Fix A2/B4's prune set + cap** (§3) — it can inject junk deps *today*; highest risk-per-line.
2. **Decide C1** (§4) — ask the user: carry `[tool.uv.sources]` through, or `uv export --frozen`? Biggest ceiling (77,642 tests).
3. **Fix B6's in-flight timeout** (§5).
4. **Wire B1** (§7) via `NodeSpec.data` — small, and unlocks the Django class.
5. Read the pending **codex-config** review for B5.
6. Re-run codex on every changed diff. It has been right every time.
