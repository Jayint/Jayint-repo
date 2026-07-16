# Post-measurement fixes — direct refs, soft-file laundering, B1 gate

Driven by the 2026-07-14 construction re-run on the VM (`/opt/tier2_rerun/`) + two codex rounds.
Both found the same defects independently.

Tests: `python3 -m pytest tests/depgraph/ tests/test_classify_services_clean.py -q`. Baseline **1582 passed**.
Nothing is committed. Shared branch — never `git add -A`.

---

## Fix 1 — PEP 508 direct references are a non-PyPI source (do FIRST: highest value)

**Evidence.** Archipelago's ROOT `requirements.txt` contains:
```
kivymd @ git+https://github.com/kivymd/KivyMD@5ff9d0d
```
Its pinned closure came out **completely empty — 0 of ~20 packages** (`colorama`, `websockets`, `PyYAML`, `kivy`, `jinja2` … all missing; the `-c` constraints heredoc is empty). `uv lock` is **all-or-nothing**, so one unresolvable root killed every other package.

This is the **PostHog failure through a third door.** `name @ git+url` is semantically identical to `[tool.uv.sources] name = {git = ...}` — "the name says WHAT, not WHERE" — but our exclusion only covers the `[tool.uv.sources]` table.

**Fix.** In `src/python_deps/evidence.py`, recognise a PEP 508 **direct reference** (` @ <url>` — `git+`, `http(s)`, `file:`) on any requirement line (requirements files AND pyproject `dependencies`). Route it through the **same** path as a `[tool.uv.sources]` non-PyPI dep:
- record it in `evidence.uv_sources` (or a sibling set — one canonical "non-PyPI names" set that all consumers read),
- with `V3_UV_SOURCES` **OFF** (the default): **exclude from resolve roots**, and emit a `State.MISSING` Package node with source evidence, `version=None`, `check_command=None`, `data["uninstallable"]=True` (the established immunity construction — certify flips MISSING→SATISFIED on any rc-0 check, and `emit._is_emittable` needs a version).

**Expected effect:** `kivymd` is excluded → `uv lock` succeeds → Archipelago's ~20 real packages pin → **its 20,943 gold tests become reachable.** Verify by re-running construction and asserting the closure is non-empty.

**Tests:** a root `requirements.txt` with `pkg @ git+https://…` → `pkg` is not a root; is a MISSING node; the *other* requirements still resolve. Direct refs in `pyproject` `dependencies` too. `-e .` and bare `-e <url>` lines stay ignored (existing behaviour).

---

## Fix 2 — a soft requirements file must not launder an excluded package

**Confirmed HIGH (codex).** `build_script._pinned_constraint_lines` (`:146`) omits `uninstallable` packages from the constraints file. So a nested `worlds/x/requirements.txt` containing a bare `foo` — where the graph **deliberately excluded** `foo` as a git/private-index dep — installs **public `foo` from PyPI**. C1's gate closes the front door; the soft installer opens the back one.

**Fix.** Emit every **excluded / uninstallable** dist name into the constraints file with an **unsatisfiable** specifier, e.g.
```
kivymd==0.0.0+v3.excluded    # excluded: direct git reference — must not resolve from public PyPI
```
A soft file requesting `kivymd` then **fails loudly** (no matching version) instead of silently installing the public namesake. The `|| true` swallows the failure, so the build continues — the package is simply absent, which is the honest outcome.

⚠️ Cost, state it in the code comment: that soft file's *other* packages don't install either (pip resolves per-file). **Correctness beats completeness** — a wrong package is worse than a missing one.

**Tests:** a graph with an excluded `foo` + a soft file → the constraints file contains the unsatisfiable `foo` pin; a graph with no exclusions → constraints file byte-identical to today.

---

## Fix 3 — B1: replace the DSN denylist with an allowlist

**Evidence.** B1 fires correctly (`#@config-env DJANGO_SETTINGS_MODULE=idp.settings` — the 557-test unlock) but **over-fires dangerously**:
```
#@config-env POSTGRES_HOST=host.docker.internal
#@config-env POSTGRES_PORT=55432          # dev docker-compose port!
#@config-env MYSQL_HOST=127.0.0.1
```
These would **override the certified Service-tier binding** (which provisions Postgres on 5432) — so B1 **can break repos that currently work**. PostHog emits **~190** vars incl. `HOSTNAME`, `TERM`, `LANG`, `PYTEST_CURRENT_TEST`, `SALT_KEY`.

Three confirmed causes:
1. `_looks_like_dsn` (`classify_services_clean.py:51`) only rejects strings containing `://` **with a parsed hostname** — bare hosts/ports sail through.
2. The secret regex (`build_script.py:217`) catches only `SECRET`/`PASSWORD`/`PASSWD`/`TOKEN`/`API_KEY`/`PRIVATE_KEY`/`ACCESS_KEY` — misses generic `*_KEY`, `*_SALT`, `*_CREDENTIALS`.
3. 🔴 `scan_env_defaults` (`config_scan.py:137`) scans **every** non-excluded `.py` file and takes the **first** match — so a value can come from a **test fixture or example app**, and *which file wins is not deterministic*.

**Fix — the denylist was the wrong instrument.** The dangerous category is "anything naming a host, port, or credential", which no denylist will ever enumerate. Invert it:

- **Allowlist**: bake ONLY framework settings-module-shaped vars — `DJANGO_SETTINGS_MODULE`, `SETTINGS_MODULE`, `FLASK_APP`, `FLASK_ENV`, `APP_SETTINGS`. That is the entire evidenced payoff. Everything else stays an inert `#@need` hint exactly as today.
- **Ambiguity → don't bake.** If two files define **different** defaults for the same var, emit nothing (same discipline as `choose_provider`'s AMBIGUOUS: never pick a variant). Make the scan **deterministic** (sorted walk) regardless.
- Keep the existing secret denylist and `"\n"` refusal as belt-and-braces.

**Tests:** `DJANGO_SETTINGS_MODULE` is baked; `POSTGRES_HOST` / `POSTGRES_PORT` / `MYSQL_HOST` are **NOT**; `PYTEST_CURRENT_TEST` / `HOSTNAME` / `TERM` are not; two conflicting defaults → nothing baked; the scan is deterministic across runs.

---

## Verify

Re-run construction on the VM (`/opt/construct_rerun.py`, venv `/opt/v3_rerun_venv`) for
`ArchipelagoMW/Archipelago`, `django-oauth/django-oauth-toolkit`, `PostHog/posthog`. Assert:
- Archipelago's pinned closure is **non-empty** (Fix 1 worked) and the constraints file carries the `kivymd` exclusion pin (Fix 2).
- django-oauth-toolkit emits **only** `DJANGO_SETTINGS_MODULE` (Fix 3).
- PostHog still fails **loudly** (C1 off) — a green PostHog would mean a public `hogli` got installed.
