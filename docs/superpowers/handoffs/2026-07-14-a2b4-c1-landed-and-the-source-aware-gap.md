# A2/B4 + C1 — what landed, and the architectural gap C1 exposed

**Date:** 2026-07-14 · **Branch:** `john-v3-multi-lang` · **Tests:** `python3 -m pytest tests/depgraph/ -q` → **1559 passed** (baseline at session start: 1497)
🔴 **NOTHING IS COMMITTED.** All work is in the working tree. `pytest tests/` is broken repo-wide (conftest shadowing, pre-existing) — always run per-directory.

---

## 1. Status board

| fix | status |
|---|---|
| **A2/B4** — requirements discovery: hard/soft split | ✅ **LANDED, SAFE.** 2 codex rounds, 10 findings, all fixed |
| **Soft-install renderer** | ✅ LANDED — `pip install -r <f> -c closure.txt \|\| true`, after the pinned closure |
| **C1** — `[tool.uv.sources]` carried into the synthetic pyproject | ⚠️ **BUILT, BEHIND `V3_UV_SOURCES=1`, DEFAULT OFF — NOT SAFE FOR SCORED RUNS** |
| **C1 default path** | ✅ SAFE — sourced deps excluded from roots, emitted as un-installable/un-certifiable MISSING nodes |
| **B3** | ❌ Stays reverted. Re-confirmed this session (see §5) |
| **B6 / B1** | ⏸ Untouched. See §5 |

---

## 2. A2/B4 — DONE, and the invariant now holds by construction

**Problem:** ArchipelagoMW/Archipelago produced **zero pip packages** — its deps live in ~84 `worlds/*/requirements.txt`, and a prefix-glob + 4-directory allowlist could never find them. The first fix (recursive `os.walk`) made things *worse*: every newly-found file became a hard resolve root, so a test-fixture `requirements.txt` pinning `requests==2.0.0` silently constrained the whole closure — **uv reports no conflict, it just locks the old version.**

**The fix — split DISCOVERY from ROLE:**
- **HARD** = *exactly* the pre-walk allowlist set → hard resolve roots, unchanged behaviour.
- **SOFT** = everything else the walk finds → **not** ingested as deps; rendered as
  `pip install -r <file> -c /tmp/closure.txt || true` **after** the pinned closure.
- The `-c` is load-bearing: a soft file may **ADD** packages, never **MOVE** one the closure pinned.
- The `|| true` mirrors what gold actually did: `for f in worlds/*/requirements.txt; do pip install -r "$f" || true; done`.

**The lesson.** I first computed HARD with *new* path-shape predicates (depth, case, symlinks). Codex broke that **three times running** — case-insensitive dir match, cap dropping a hard root, `os.walk` not following a symlinked `tests/` dir. The fix that stuck: **compute HARD by running the old allowlist code directly** (non-recursive glob of root + the four named dirs), and define **SOFT = walk − HARD**. The invariant is now true *by construction*, not by reasoning correctly about every filesystem edge case.

**Also fixed:** the cap applies **only to soft** files (hard files are never truncated); `-c` include targets join `visited` (a constraint file was being re-installed as an install list); the include depth guard is now **loud**, not silent; soft symlinks that escape the repo are rejected with an error.

---

## 3. 🔴 C1 — the real finding. Read this before touching it.

**Problem:** PostHog (**77,642 gold tests, the largest denominator in the corpus**) declares
`hogli = { workspace = true }` (a package sitting at `tools/hogli` **inside the checkout**), plus git-sourced `infi-clickhouse-orm` and `pytest-split`. The synthetic pyproject carried the **names** and discarded `[tool.uv.sources]` — so `uv lock` looked for `hogli` on PyPI, failed, and (being all-or-nothing) took ~500 resolvable packages with it. Zero packages, zero tests.

Carrying the source table through **works** — and then the entire downstream chain throws the provenance away.

### The architectural gap: there is no single chokepoint where a package becomes an install command

The package layer models a package as **`(name, version)`** and installs it **by name** from **at least six independent sites**:

| # | site | how it leaks |
|---|---|---|
| 1 | `emit.py:84` `_is_emittable` | accepts any *versioned* MISSING package, ignores `uninstallable` → emits `pip install forked-sdk==1.2.3` (the **public** one) |
| 2 | `probe.py:557` | `uv pip install --system` **without `--no-deps`** → public `bar` depending on git-sourced `foo` drags in **public `foo`** |
| 3 | `certify.py:90` + `resolve_lock.py:350` | a package's check is only `pip show <name>`; certify flips **any rc-0 → SATISFIED** → **MISSING does not stick** |
| 4 | `resolve.py:637` | sources filtered to *initial roots* → a **transitively**-required sourced package loses its override |
| 5 | `build_script.py:165` | soft requirements `pip install -r` → a nested file's bare `foo` installs **public `foo`** |
| 6 | `build_script.py:397`, `populate.py:52` | pytest bootstrap + PEP-517 build isolation |

**Patching egress points failed three review rounds running.** Each round closed some and codex found more. That is the signal: **you cannot bolt provenance onto a `(name, version)` package layer.**

### What was done instead: `V3_UV_SOURCES`, default OFF

- **OFF (default, SAFE):** a dep carrying a non-PyPI source (`workspace`/`git`/`url`/`path`/**`index`**) is **excluded from resolve roots** — so nothing sourced ever enters the graph and all six egress paths are closed at once. It is **not silent**: each becomes a `State.MISSING` Package node with source evidence, `version=None` (trips `emit._is_emittable`), `check_command=None` (**the only guaranteed immunity** — certify flips MISSING→SATISFIED off *any* rc-0 check and never consults `uninstallable`), `data["uninstallable"]=True`.
  → PostHog fails **loudly**, with a graph saying exactly why. **A loud zero is acceptable. A false green is not.**
- **ON:** everything built this session runs. **Do not use for scored runs.** The flag's docstring cites all six sites.
- Flag is read once in `scripts/run_v3_e2e.py` (`_uv_sources_enabled`, mirrors `V3_INCLUDE_SERVICES`) and threaded as an argument; no module below reads the env.

### The decision that is still open

**The "minimum fix" is not minimal.** Making `[tool.uv.sources]` actually work requires the package layer to become **source-aware**. Two routes:

- **(A) One install chokepoint.** Make provenance a first-class Node property and route *every* install through a single `install_spec(node)` that can emit `name @ git+url@rev`, `-e /abs/path`, `--index-url`. This is the architecturally right answer, it collapses six sites into one, and it's needed **anyway** for repos with no lockfile. Biggest change.
- **(B) `uv export --frozen`.** When the repo ships a `uv.lock`, export and install from that — the export already carries git URLs and hashes, so far less install-layer surgery. Fixes PostHog cheaply. But it only helps repos **with** a lockfile, and it moves the paper story from *"we solve the environment"* to *"we replay the repo's solve and certify it."*

⚠️ **This is a paper-framing call, not just engineering. Get the user's decision. Do not pick it unilaterally.**

**Do this first, either way:** re-measure **Archipelago** and **PostHog** through the tier-2 harness (v3's setup script in the base image it chose) with the A2/B4 work in. That number should drive the C1 choice instead of another round of argument.

---

## 4. Kept unconditionally (flag-independent, all correct)

- **Phase-A repair excludes `evidence.uv_sources` names from ACCEPTED results** — not just the `declared_metadata` rung. `repair.generate_candidates`'s `normalize` rung proposes the name independently, so gating one rung was insufficient. Prevents a git-pinned `acme-sdk` being "repaired" into the **public PyPI** `acme-sdk`.
- `_find_workspace_member_path`: no basename fallback after a declared-name mismatch; ambiguity → `None`, never "pick the first".
- `evidence.uv_sources` / `.uv_workspace_members` / `.uv_indexes` capture; `resolve_lock._non_default_source_evidence`.

---

## 5. B6 / B3 / B1 — honest triage

- **B3 (declared rung in the repair ladder): dead. Do not revive.** Re-confirmed on evidence, not argument: a TDD test this session showed a `[tool.uv.sources]`-carrying dep in a **non-activated `gpu` extra** being resurrected as a root — the exact mutual-exclusion failure B3 was reverted for, reproduced live in different code.
- **B6 (resolve wall-clock budget): worth fixing.** The budget is checked only *between* ladder attempts; an in-flight `uv lock` still runs to the executor's fixed 300s. The test doesn't prove what it claims (it expires the clock *before* the first subprocess). ⚠️ I do **not** know whether PostHog's 2,224s burn was many retries or a few slow locks — that changes B6's urgency, and the C1 re-measurement will show it.
- **B1 (CONFIG → Dockerfile `ENV`): lowest priority.** Inert (`classify_services_clean._config_nodes` never sets what the renderer reads). Fix via `NodeSpec.data`, **not** `chosen_fix` (NodeSpec deliberately carries data, never shell). It's a *capability* fix, not a *correctness* one — nothing is wrong today, a class of Django-shaped repos just never gets env vars. Defer until a number says it matters.

---

## 6. Landmines

1. **A passing test proves nothing about reachability.** A test asserted the **unsafe** fallback path was correct (`hogli` reaching the bare `uv pip compile` command) and I **repeated it to the user as proof the fix worked**. Always read the test, not the agent's summary of it.
2. **Contradictory briefs produce compensating hacks.** I told one agent "keep the retag" and the next "stop dropping, but don't touch `evidence.py`". Boxed in, it added a pass that re-added roots *after* `select_roots`, bypassing scope. Delete the cause, don't compensate for it.
3. **Never run two implementers on one file.** Partition ownership explicitly.
4. `codex exec` **hangs on stdin** — always `< /dev/null`. Long runs get killed by task supervision: launch with `nohup ... &`.
5. Do not use `codex review --uncommitted` (sweeps the user's unrelated WIP).
6. **Codex has now been right every single time.** Re-run it on every diff.
