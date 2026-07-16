# Tier-2 re-run — MEASURED results, and the one bug that matters next

**Date:** 2026-07-14 · **Branch:** `john-v3-multi-lang` · **Local tests:** 1595 passed (was 1497)
🔴 **NOTHING COMMITTED.** All work is in the working tree. Use `pytest tests/depgraph/` — `pytest tests/` is broken repo-wide (pre-existing conftest shadowing).

---

## 1. The measurement (this is the point — everything else is a means to it)

Re-ran **v3 graph construction** with the new code, rebuilt the image from the NEW `setup.sh`,
ran **gold's own collect command** at gold's pinned SHA. VM artifacts: `/opt/tier2_rerun/`.

| repo | before | after |
|---|---|---|
| **ArchipelagoMW/Archipelago** | BUILD_FAILED, **0 tests**, 0 pip packages | **builds; 4,227 gold tests; EBSR 0.2018**; 74 pinned pkgs; 11 soft installs |
| **django-oauth/django-oauth-toolkit** | COLLECT_CRASHED, 0 tests | builds ✅, ENV set ✅, **still 0 — collection crashes** (see §3) |

Harness: `/opt/tier2_rerun.py` (build+collect) and `/opt/construct_rerun.py` (construction only).
Venv: `/opt/v3_rerun_venv` (3.10 + full `requirements.txt`; the `tomli` backport is REQUIRED —
without it `tomllib is None` and the entire declared closure silently vanishes, see `evidence.py:28`).

---

## 2. 🔴 THE NEXT BUG — B1 bakes a WRONG value and REGRESSES the repo

django-oauth-toolkit's collection dies with:

    ImportError: No module named 'idp'

**What we bake:** `ENV DJANGO_SETTINGS_MODULE=idp.settings`
**What gold bakes:** `ENV DJANGO_SETTINGS_MODULE=tests.settings`  → **557/557**
**Authoritative source:** `tox.ini:41` → `DJANGO_SETTINGS_MODULE = tests.settings`
**Where our value came from:** `tests/app/idp/manage.py` — an **example Django app vendored inside the test suite**.

`config_scan.scan_env_defaults` walks EVERY `.py` file and takes an `os.environ.setdefault` default.
It found a fixture app's settings and baked it as an image `ENV`.

🔴 **This is a REGRESSION, not just a miss.** pytest-django reads `DJANGO_SETTINGS_MODULE` from the
ENVIRONMENT in preference to the repo's own pytest config — so our wrong `ENV` **overrode the
correct setting the repo already had**. B1 made this repo worse.

The Fix-3 allowlist made the CATEGORY safe (no hosts/ports/secrets). It did nothing about
**PROVENANCE**. The one variable we allowlisted got a wrong value.

### The fix (precise, and the payoff is proven — gold gets 557/557 with `tests.settings`)

1. **Read the AUTHORITATIVE config first**, in this order, and bake that:
   `tox.ini` `[testenv] setenv`, `pytest.ini`, `setup.cfg` `[tool:pytest]`,
   `pyproject.toml` `[tool.pytest.ini_options]`.
2. **Code scan is a FALLBACK only** — used when no authoritative source exists.
3. **Never** take a value from a path under `tests/app/`, `tests/fixtures/`, `examples/`,
   `sample*/`, `demo*/` — a vendored example app is not the project's configuration.
4. Keep: allowlist, ambiguity→don't-bake, secret denylist, deterministic walk.
5. **Consider not baking at all when the repo's pytest config already sets the var** — pytest-django
   reads it itself; our ENV can only override it, never improve it.

---

## 3. Archipelago's remaining 80% — UNEXPLAINED, do not guess

4,227 of 20,943 gold collected. **97% of gold (20,383) lives under `worlds/<world>/test/`.**
Only **5** collection errors — nowhere near enough to explain 16,716 absent tests. The rest are
**silently skipped**, not failing.

**Next step:** save the collected node-id set and diff against gold PER WORLD. That names exactly
which worlds are absent. **Do not theorise from symptoms** — see §6.

Discovery is NOT the problem: 10 `worlds/*/requirements.txt` exist on disk and all 10 render
(+ `WebHostLib/`). ⚠️ The old handoff's "~84 worlds requirements files" figure is **WRONG** for this SHA.

---

## 4. What landed and is VERIFIED IN PRODUCTION

| fix | evidence |
|---|---|
| **A2/B4** hard/soft requirements split | 11 soft `-r` lines render; 10/10 worlds files found |
| **Fix 1** PEP 508 direct refs (`kivymd @ git+…`) excluded | **closure 0 → 74 packages**; `kivymd` absent |
| **Fix 2** soft-file laundering guard | `kivymd==0.0.0+v3.excluded` renders (line 851) — a soft file naming it now fails LOUDLY instead of installing the PUBLIC PyPI package |
| **Fix 3** B1 allowlist | django config-env **11 → 1**; `POSTGRES_PORT=55432`, `MYSQL_HOST` gone. Archipelago's `SC2CLIENTHOST` correctly not baked |
| **C1** `V3_UV_SOURCES` default OFF | posthog still fails LOUDLY (correct — a green posthog would mean a public `hogli` got installed) |
| **B6** resolve budget | landed; not yet exercised (log level hides the new per-round INFO lines) |
| **B3** | dead. It is a NO-OP: `declared_candidates` only fires on exact name equality, which `normalize` already produces. Only action: stale comment at `build.py:424-428` |

**`0.0.0+v3.excluded`** (Fix 2) is a PEP 440 **local version segment** — valid to parse (so pip says
"no matching distribution", not "invalid requirement"), but PyPI **rejects uploads carrying one**, so
no real release can ever match it. That guarantee comes from the packaging spec, not luck.

---

## 5. Corrections to earlier claims (I got these wrong; do not re-inherit them)

- ❌ *"`kivymd @ git+…` poisoned the whole `uv lock`."* **FALSE.** The `://` heuristic in
  `_parse_requirement_line` was already silently DROPPING it. The real poison was the companion line
  `kivymd>=2.0.1.dev0` — unsatisfiable on public PyPI (latest is 1.2.0). Fix 1 works because it
  excludes by CANONICAL NAME, which removes both lines.
- ❌ *"Task 5+6 proved reachability for `hogli` end-to-end."* **FALSE.** That test asserted `hogli`
  reaching the BARE `uv pip compile` fallback — i.e. it pinned the UNSAFE path as correct.
- ❌ *"~84 `worlds/*/requirements.txt`."* Reality at this SHA: **10**.

---

## 6. Landmines

1. **A passing test proves nothing about reachability.** Four separate features this session passed
   their own tests while never firing in production (B1, B3, and two others). Trace the call path.
2. **My narratives were less reliable than the measurements.** Every time I explained a symptom
   without running it, I was wrong. Run it.
3. `codex exec` hangs on stdin → always `< /dev/null`; long runs get killed by task supervision →
   launch with `nohup … &`.
4. The VM has **Python 3.10 only**. `tomli` is mandatory (see §1).
5. `python3 -u` when redirecting, or the console log stays empty and looks like a hang.
6. Never run two implementer agents on the same file.
7. Codex (`gpt-5.6-terra`, high) has been right **every single time**. Re-run it on every diff.

---

## 7. FINAL MEASURED RESULTS (build + gold's collect, all fixes in)

| repo | gold | before | after | collected | EBSR |
|---|---|---|---|---|---|
| **python-websockets/websockets** | 2,248 | BUILD_FAILED | **OK** | **2,112** | **0.9395** ✅ |
| **ArchipelagoMW/Archipelago** | 20,943 | BUILD_FAILED | **OK** | **4,227** | **0.2018** ✅ |
| **django-oauth/django-oauth-toolkit** | 557 | COLLECT_CRASHED | **env CORRECT** | **557/557** (bare pytest) | ~1.0 ✅ |
| Qiskit/qiskit | 64,558 | BUILD_FAILED | builds, 0 collected | 0 | 0.0 (358 import errors) |
| polarsource/polar | 5,749 | BUILD_FAILED | BUILD_FAILED | — | — |
| idank/explainshell | 559 | BUILD_FAILED | BUILD_FAILED | — | — |
| crytic/slither | 7,296 | COLLECT_CRASHED | COLLECT_CRASHED | — | — |
| mozilla/addons-server | 9,098 | BUILD_FAILED | COLLECT_CRASHED | — | — |

**≈6,900 gold tests that were ZERO this morning are now collected.**

### What each fix is worth (measured, not asserted)

- **B5 capstone gate** → **websockets 0 → 2,112 (EBSR 0.9395)**. It had a healthy 51-pkg closure and the unconditional `pip install -e .` was killing it AFTER the whole env installed. Also got Qiskit to build.
- **Fix 1 (PEP 508 direct refs)** → **Archipelago closure 0 → 74 pkgs → 4,227 tests**.
- **B1 provenance** → **django-oauth-toolkit 557/557**. `tests.settings` (from `tox.ini`), not `idp.settings` (from a vendored example app).

### 🔴 The corpus-shaping fact I should have checked FIRST

**Only 2 of 20 tier-2 failures ever had an empty closure** (posthog, Archipelago). The other 6 `build_failed` repos had **51–126 pinned packages** and failed anyway. Dependency RESOLUTION was never their blocker — the BUILD was. Run `/opt/before_closures.py` before designing any experiment around closure size.

### 🔴 Our dependency selection is wrong in BOTH directions

Two crashes with opposite causes, in one sweep:
- **OVER-install:** `crytic/slither` — we install `pytest_insta`, which needs `config.cache`; gold's collect command passes `-p no:cacheprovider` → `INTERNALERROR: 'Config' object has no attribute 'cache'`. Gold's env did NOT have it.
- **UNDER-install:** `mozilla/addons-server` — its `pyproject.toml` `addopts` has `--reuse-db` (a **pytest-django** flag) and pytest-django is **missing** → `error: unrecognized arguments: --reuse-db`, dying at argparse before collection.

So the closure is not systematically too greedy or too shy. **Both.**

### Still unexplained (do NOT theorise — measure)

- **Archipelago's 80% gap.** 20,383 of 20,943 gold live under `worlds/<world>/test/`; we get 4,227 with only 5 collect errors → most worlds are SILENTLY skipped. Diff the collected node-ids against gold per-world.
- **posthog closure STILL 0** even with all 3 uv-source deps correctly excluded. The exclusion is necessary, NOT sufficient. Suspect the resolve ladder (it burned 2,224s of 2,400s). B6 added per-round timing — RAISE THE LOG LEVEL to see it.
- **polar / explainshell** still BUILD_FAILED; `setup.sh` exits 1. Build logs at `/opt/tier2_rerun/<slug>/build.log`.

### Harness landmine (cost us 3 repos' answers)

`collect_once` raises `FileNotFoundError: r.json` when pytest dies before the plugin writes output. This was ALREADY fixed once in the manifest builder as `safe_collect()` and not carried into `/opt/tier2_rerun.py`. It is now patched to re-run bare pytest and capture the real error — **that patch is what revealed all three causes above.**
