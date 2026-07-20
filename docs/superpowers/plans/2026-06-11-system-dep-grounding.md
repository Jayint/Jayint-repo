# System-Dependency Grounding for the EnvState Map — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development or
> superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the deterministic snapshot to **ground the `system` layer** the way it already
grounds pip: probe what system providers are actually present (apt packages, `pkg-config`
modules, build tools), fold them into the world model, and **deterministically auto-resolve a
`system`-layer `open_problem` once the probe confirms its missing artifact is now present** —
instead of trusting only the Maintainer's (unverified) `resolved` list.

**Branch:** `john-planner-v1`
**Tech:** Python 3.13, pytest, frozen dataclasses, Docker sandbox (`exec_readonly`).

---

## 1. The problem (observed live, burr run 2026-06-11)

The deterministic snapshot grounds **pip** facts (`installed` = `pip list --format=freeze`) and the
interpreter (`env`). It does **not** touch the system layer at all:

- `snapshot._SNAPSHOT_FIELDS` omits `dpkg_packages` / `pkg_config_modules` — so the map has **zero**
  apt facts (`env_keys` was `[python_version, pip_version, arch, which_python]` every cycle).
- `_derive_progress` makes `system` "complete unless an `open_problem` targets it" — no probe signal.
- `_auto_resolve_problems` matches only against pip `installed`, so a `system` problem
  (`pg_config not found`) **can never auto-resolve**; only the Maintainer's `resolved` can clear it —
  an unverified LLM judgment, the exact staleness/hallucination risk the design removed for pip.

### 1.1 The asymmetry that makes this non-trivial

For pip, the failure names the package: `ModuleNotFoundError: flask` ↔ install `flask`. For system
deps, **the failure names the missing artifact, not the fixing package**:

| Failure signature | Missing artifact | apt package that fixes it |
|---|---|---|
| `Error: pg_config executable not found` | `pg_config` (tool) | `libpq-dev` |
| `fatal error: Python.h: No such file` | `Python.h` (header) | `python3-dev` |
| `cannot find -lpq` | `libpq` (library) | `libpq-dev` |
| `No package 'libxml-2.0' found` | `libxml-2.0` (pkg-config) | `libxml2-dev` |
| `gcc: command not found` | `gcc` (tool) | `gcc` / `build-essential` |

So we **cannot** substring-match the signature against installed apt package *names* — they don't
correspond. We match against **what the artifact actually is** (a tool on PATH, a `pkg-config`
module), which is what we probe.

---

## 2. Design decisions

| # | Decision | Choice |
|---|---|---|
| 1 | What to probe | apt names (`dpkg -l`), `pkg-config` module names, **and a curated set of build/config tools** on PATH (`gcc`, `pg_config`, `mysql_config`, `pkg-config`, `cmake`, …) — the tools that appear in failure signatures. |
| 2 | Where it lives in the map | New structured field **`system_installed: tuple[Fact, ...]`** (parallel to `installed`), holding the union of {apt names, pkg-config modules, present tools}, each `Fact(name, detail=source)`. |
| 3 | `env` folding | Compact, prompt-friendly diagnostics only: `os_release` (short id e.g. `debian:bookworm`) + `build_tools` (the present curated tools, comma-joined). The **full** apt/pkg-config lists go in `system_installed`, **not** `env` — a fresh debian slim lists ~120 apt packages; dumping that into every LLM payload is wasteful. |
| 4 | Auto-resolve | New **`_auto_resolve_system_problems`**: extract the missing artifact from each `system`-layer signature via a small set of regexes for the canonical failure shapes, normalize it, and resolve the problem iff the artifact is now present in `system_installed`. Conservative — when the shape isn't recognized, **keep** the problem (defer to the Maintainer's `resolved`). |
| 5 | `progress` | No change. `system` flips back True automatically once its problem is gone (auto-resolve now does that deterministically). |
| 6 | Proactive system TODO | **Out of scope.** There is no manifest source of truth for apt deps, so there is no `system_required`/`missing` — system stays reactive on the *discovery* side. We only add deterministic grounding on the *resolution* side. |
| 7 | Back-compat | `apply_deterministic` reads the snapshot via `getattr(snap, "system_installed", ())` (the snapshot is duck-typed `Any`), so existing `SimpleNamespace(installed=, env=)` test snaps keep working. New map field defaults to `()` with symmetric serialization (like `env` did), so `test_world_model.py` round-trips stay green. |

**Net value:** a `system` `open_problem` (`pg_config not found`) clears the cycle *after* the probe
sees `pg_config` on PATH — host-verified, not LLM-asserted — closing the staleness/hallucination
gap on the system layer. We also stop throwing away the `dpkg`/`pkg-config` signal the extractor
already collects.

---

## 3. File structure

| File | Change | Responsibility |
|---|---|---|
| `src/envstate/extractor.py` | modify | add `system_tools` command (curated `command -v` probe) |
| `src/envstate/world_model.py` | modify | `system_installed` field; `merge_map`; `apply_deterministic` fold; `_auto_resolve_system_problems`; serialization |
| `src/envstate/snapshot.py` | modify | probe system fields → `EnvSnapshot.system_installed` + compact `env` keys |
| `src/envstate/maintainer.py` | modify | send compact system facts (`build_tools`, `os_release`) in the payload; keep bulky lists out |

**Dependency order:** extractor → world_model (field + helpers) → snapshot → maintainer.

---

## Task 1: extractor — `system_tools` probe

**Files:** modify `src/envstate/extractor.py`; test `tests/test_extractor_system_tools.py`

- [ ] **Step 1 — failing test**

```python
# tests/test_extractor_system_tools.py
from src.envstate.extractor import EXTRACTOR_COMMANDS, run_extractor, SYSTEM_TOOL_PROBES


def test_system_tools_command_present_and_curated():
    assert "system_tools" in EXTRACTOR_COMMANDS
    # the failure-artifact tools we care about must be probed
    for t in ("gcc", "pg_config", "pkg-config", "make", "cmake", "mysql_config"):
        assert t in SYSTEM_TOOL_PROBES


def test_system_tools_parsed_as_present_subset():
    # fake exec returns only gcc + pg_config present (one name per line)
    table = {EXTRACTOR_COMMANDS["system_tools"]: (0, "gcc\npg_config\n")}
    res = run_extractor(lambda cmd: table.get(cmd, (1, "")), fields=("system_tools",))
    assert res.fields["system_tools"] == "gcc\npg_config"
```

- [ ] **Step 2 — run, expect FAIL** (`cannot import name 'SYSTEM_TOOL_PROBES'`)
  `/Users/john/john-planner-v1/.venv/bin/python -m pytest tests/test_extractor_system_tools.py -v`

- [ ] **Step 3 — implement.** Add to `src/envstate/extractor.py`:

```python
# Curated build/config tools that appear in system-layer failure signatures.
SYSTEM_TOOL_PROBES: tuple[str, ...] = (
    "gcc", "g++", "cc", "make", "cmake", "pkg-config",
    "pg_config", "mysql_config", "mariadb_config",
    "curl-config", "xml2-config", "xslt-config", "krb5-config", "icu-config",
)
```

Add one entry to `EXTRACTOR_COMMANDS` (echo each present tool on its own line):

```python
    "system_tools": (
        "for t in " + " ".join(SYSTEM_TOOL_PROBES) +
        "; do command -v \"$t\" >/dev/null 2>&1 && echo \"$t\"; done"
    ),
```

(Keep existing `dpkg_packages`, `pkg_config_modules`, `os_release` as-is.)

- [ ] **Step 4 — run, expect PASS.** Also run `tests/test_envstate_extractor.py` (unchanged, still green).
- [ ] **Step 5 — commit:** `git add src/envstate/extractor.py tests/test_extractor_system_tools.py && git commit -m "feat(extractor): add curated system_tools presence probe"`

---

## Task 2: world_model — `system_installed` field + merge_map + serialization

**Files:** modify `src/envstate/world_model.py`; test `tests/test_world_model_system_field.py`

- [ ] **Step 1 — failing test**

```python
# tests/test_world_model_system_field.py
from src.envstate.world_model import initial_map, merge_map, map_to_dict, map_from_dict, Fact


def _base():
    return initial_map(base_image="python:3.12", workdir="/app", language="python",
                       build_system="pip", repo_layout=())


def test_system_installed_defaults_empty():
    assert _base().system_installed == ()


def test_merge_replaces_and_defensive_copies_tuple():
    m = merge_map(_base(), system_installed=(Fact("libpq-dev", "dpkg"), Fact("pg_config", "tool")))
    assert {f.name for f in m.system_installed} == {"libpq-dev", "pg_config"}
    assert _base().system_installed == ()  # original frozen/unchanged


def test_system_installed_round_trips():
    m = merge_map(_base(), system_installed=(Fact("gcc", "tool"),))
    assert map_from_dict(map_to_dict(m)).system_installed == (Fact("gcc", "tool"),)
```

- [ ] **Step 2 — run, expect FAIL** (`unexpected keyword argument 'system_installed'`)

- [ ] **Step 3 — add the field** to `WorldModelMap` (after `env`, both have defaults):

```python
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    system_installed: tuple[Fact, ...] = ()   # NEW: apt names + pkg-config modules + tools present
```

- [ ] **Step 4 — extend `merge_map`** with `system_installed: tuple[Fact, ...] | None = None` and in the `dataclasses.replace(...)`:

```python
        system_installed=system_installed if system_installed is not None else current.system_installed,
```

- [ ] **Step 5 — serialization.** `map_to_dict`: add
  `"system_installed": [_fact_to_dict(f) for f in m.system_installed]`.
  `map_from_dict`: add
  `system_installed=tuple(_fact_from_dict(f) for f in d.get("system_installed", []))`.

- [ ] **Step 6 — run** `tests/test_world_model_system_field.py tests/test_world_model.py` — both PASS
  (round-trip stays green: default `()` + symmetric serialization).

- [ ] **Step 7 — commit:** `... -m "feat(world-model): add system_installed field + merge_map + serialization"`

---

## Task 3: world_model — `_auto_resolve_system_problems`

**Files:** modify `src/envstate/world_model.py`; test `tests/test_auto_resolve_system.py`

**Matching contract:** extract the missing artifact from a `system`-layer signature; resolve iff a
normalized form of it is present in `system_installed`. Recognized shapes (case-insensitive):
`X: command not found` / `X executable not found` / `X: not found` → tool `X`;
`No package 'X' found` → pkg-config module `X`; `cannot find -lX` → `libX`/`X`;
`fatal error: X.h` → header (mapped conservatively — only resolves if `X` or `libX` is present).
Unrecognized shape → keep (defer to Maintainer `resolved`). Only acts on `layer == "system"`.

- [ ] **Step 1 — failing test**

```python
# tests/test_auto_resolve_system.py
from src.envstate.world_model import _auto_resolve_system_problems, Fact, OpenProblem


def _sys(sig):
    return OpenProblem(sig, "x", "system")


def test_pg_config_resolves_when_tool_present():
    probs = (_sys("Error: pg_config executable not found"),)
    kept = _auto_resolve_system_problems(probs, (Fact("pg_config", "tool"),))
    assert kept == ()


def test_pg_config_kept_when_tool_absent():
    probs = (_sys("Error: pg_config executable not found"),)
    assert _auto_resolve_system_problems(probs, (Fact("gcc", "tool"),)) == probs


def test_command_not_found_shape():
    probs = (_sys("gcc: command not found"),)
    assert _auto_resolve_system_problems(probs, (Fact("gcc", "tool"),)) == ()


def test_pkg_config_no_package_shape():
    probs = (_sys("No package 'libxml-2.0' found"),)
    assert _auto_resolve_system_problems(probs, (Fact("libxml-2.0", "pkgconfig"),)) == ()


def test_only_touches_system_layer():
    probs = (OpenProblem("pg_config not found", "x", "deps"),)  # mislabeled deps
    assert _auto_resolve_system_problems(probs, (Fact("pg_config", "tool"),)) == probs


def test_unrecognized_shape_is_kept():
    probs = (_sys("postgres connection refused on :5432"),)
    assert _auto_resolve_system_problems(probs, (Fact("pg_config", "tool"),)) == probs
```

- [ ] **Step 2 — run, expect FAIL** (`cannot import name '_auto_resolve_system_problems'`)

- [ ] **Step 3 — implement** (add to `world_model.py`):

```python
import re  # add to top-of-file imports if absent

# Failure-signature shapes → the missing artifact token.
_SYS_ARTIFACT_PATTERNS = (
    re.compile(r"([A-Za-z0-9_.+-]+)\s*(?::\s*)?(?:command not found|executable not found|: not found)", re.I),
    re.compile(r"no package '([^']+)' found", re.I),
    re.compile(r"cannot find -l(\S+)", re.I),
    re.compile(r"fatal error:\s*([A-Za-z0-9_./+-]+)\.h\b", re.I),
)


def _system_artifact(signature: str) -> str | None:
    """Best-effort extraction of the missing tool/lib/module from a signature."""
    for pat in _SYS_ARTIFACT_PATTERNS:
        m = pat.search(signature)
        if m:
            return m.group(1).strip().lower()
    return None


def _auto_resolve_system_problems(
    open_problems: tuple[OpenProblem, ...],
    system_installed: tuple[Fact, ...],
) -> tuple[OpenProblem, ...]:
    """Drop a layer=='system' problem once the probe confirms its missing artifact
    is present in system_installed (apt names / pkg-config modules / tools on PATH).

    Matches against WHAT THE ARTIFACT IS, not the apt package name (the signature
    names pg_config, not libpq-dev). Conservative: unrecognized shapes are kept.
    Never raises; leaves non-system problems untouched.
    """
    if not system_installed:
        return open_problems
    present = {f.name.lower() for f in system_installed if f.name}
    # also index without a leading 'lib' so 'libpq' matches 'pq' artifacts and vice-versa
    present |= {n[3:] for n in present if n.startswith("lib")}
    present |= {"lib" + n for n in list(present)}

    kept: list[OpenProblem] = []
    for p in open_problems:
        if p.layer != "system":
            kept.append(p)
            continue
        art = _system_artifact(p.signature)
        if art is not None and (art in present or art.replace("lib", "") in present):
            continue  # resolved deterministically
        kept.append(p)
    return tuple(kept)
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `... -m "feat(world-model): system-aware auto-resolve (artifact->provider match)"`

---

## Task 4: world_model — fold `system_installed` into `apply_deterministic`

**Files:** modify `src/envstate/world_model.py`; test `tests/test_apply_deterministic_system.py`

- [ ] **Step 1 — failing test**

```python
# tests/test_apply_deterministic_system.py
from types import SimpleNamespace
from src.envstate.world_model import initial_map, merge_map, apply_deterministic, Fact, OpenProblem


def _snap(installed=(), env=None, system_installed=()):
    return SimpleNamespace(installed=installed, env=env or {"arch": "x86_64"},
                           system_installed=system_installed)


def _man(build_system="pip", required=()):
    return SimpleNamespace(build_system=build_system, required=required)


def _base():
    return initial_map(base_image="python:3.12", workdir="/app", language="python",
                       build_system="unknown", repo_layout=())


def test_system_installed_replaced_from_snapshot():
    snap = _snap(system_installed=(Fact("pg_config", "tool"),))
    m = apply_deterministic(_base(), snap, _man())
    assert {f.name for f in m.system_installed} == {"pg_config"}


def test_system_problem_auto_resolved_and_progress_recovers():
    prior = merge_map(_base(), open_problems=(OpenProblem("Error: pg_config executable not found", "x", "system"),))
    snap = _snap(system_installed=(Fact("pg_config", "tool"),))
    m = apply_deterministic(prior, snap, _man())
    assert m.open_problems == ()              # system problem auto-resolved
    assert m.progress["system"] is True        # layer recovers (no system problem)


def test_back_compat_snapshot_without_system_field():
    # old duck-typed snapshot lacking .system_installed must not crash
    snap = SimpleNamespace(installed=(), env={"arch": "x86_64"})
    m = apply_deterministic(_base(), snap, _man())
    assert m.system_installed == ()
```

- [ ] **Step 2 — run, expect FAIL.**

- [ ] **Step 3 — modify `apply_deterministic`.** In the `if snap.env:` (success) branch add
  `system_installed = tuple(getattr(snap, "system_installed", ()))`; in the degrade branch
  `system_installed = current.system_installed`. Thread it into the first `merge_map(...)` call
  (`system_installed=system_installed`). After the pip auto-resolve, chain the system one:

```python
    resolved = _auto_resolve_problems(current.open_problems, installed)
    resolved = _auto_resolve_system_problems(resolved, system_installed)   # NEW
```

(Progress is recomputed afterward as today — `system` recovers automatically once the problem is gone.)

- [ ] **Step 4 — run** `tests/test_apply_deterministic_system.py tests/test_apply_deterministic.py` — PASS.
- [ ] **Step 5 — commit:** `... -m "feat(world-model): apply_deterministic folds system_installed + auto-resolves system problems"`

---

## Task 5: snapshot — probe system state into `EnvSnapshot`

**Files:** modify `src/envstate/snapshot.py`; test `tests/test_snapshot_system.py`

- [ ] **Step 1 — failing test**

```python
# tests/test_snapshot_system.py
from src.envstate.snapshot import probe_env, EnvSnapshot


def _exec(table):
    def run(cmd):
        for k, v in table.items():
            if k in cmd:
                return v
        return (1, "")
    return run


def test_probe_collects_system_installed_and_compact_env():
    table = {
        "pip list --format=freeze": (0, "flask==3.0.0\n"),
        "uname -m": (0, "x86_64"),
        "dpkg -l": (0, "libpq-dev\nbuild-essential\n"),
        "pkg-config --list-all": (0, "libxml-2.0 libXML\nzlib zlib\n"),
        "command -v": (0, "gcc\npg_config\n"),        # system_tools loop
        "/etc/os-release": (0, "ID=debian\nVERSION_CODENAME=bookworm\n"),
    }
    snap = probe_env(_exec(table))
    sysnames = {f.name.lower() for f in snap.system_installed}
    assert {"libpq-dev", "build-essential", "gcc", "pg_config"} <= sysnames
    assert "libxml-2.0" in sysnames                     # pkg-config module name
    assert snap.env["build_tools"] == "gcc,pg_config"   # compact, prompt-friendly
    assert "debian" in snap.env["os_release"]
    # bulky lists are NOT dumped into env
    assert "dpkg_packages" not in snap.env and "pkg_config_modules" not in snap.env


def test_total_failure_empty_snapshot():
    assert probe_env(lambda cmd: (1, "")) == EnvSnapshot()
```

- [ ] **Step 2 — run, expect FAIL.**

- [ ] **Step 3 — implement.** Extend `_SNAPSHOT_FIELDS`; add `system_installed` to `EnvSnapshot`
  (default `()`); parse the new fields:

```python
_SNAPSHOT_FIELDS = LIGHTWEIGHT_FIELDS + (
    "which_python", "venv", "dpkg_packages", "pkg_config_modules", "system_tools", "os_release",
)

@dataclass(frozen=True)
class EnvSnapshot:
    installed: tuple[Fact, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    system_installed: tuple[Fact, ...] = ()   # NEW


def _names(text: str, *, first_token: bool) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        out.append(line.split()[0] if first_token else line)
    return out


def probe_env(exec_readonly):
    try:
        result = run_extractor(exec_readonly, _SNAPSHOT_FIELDS)
    except Exception:
        return EnvSnapshot()
    fields = result.fields
    installed = _parse_installed(fields.get("installed_pip", ""))

    # system providers: apt names + pkg-config module names + tools on PATH
    sys_facts: list[Fact] = []
    for name in _names(fields.get("dpkg_packages", ""), first_token=True):
        sys_facts.append(Fact(name=name, detail="dpkg"))
    for name in _names(fields.get("pkg_config_modules", ""), first_token=True):
        sys_facts.append(Fact(name=name, detail="pkgconfig"))
    tools = _names(fields.get("system_tools", ""), first_token=True)
    for name in tools:
        sys_facts.append(Fact(name=name, detail="tool"))

    # env: keep ONLY compact, prompt-friendly scalars; drop bulky list fields
    bulky = {"installed_pip", "dpkg_packages", "pkg_config_modules", "system_tools"}
    env = {k: v for k, v in fields.items() if k not in bulky}
    if tools:
        env["build_tools"] = ",".join(tools)
    return EnvSnapshot(installed=installed, env=env, system_installed=tuple(sys_facts))
```

- [ ] **Step 4 — run** `tests/test_snapshot_system.py tests/test_snapshot.py` — PASS
  (existing snapshot test still green: it uses a table without the new commands → those fields are
  empty → `system_installed=()`, and the compact-env assertions there are unaffected).

- [ ] **Step 5 — commit:** `... -m "feat(snapshot): probe dpkg/pkg-config/system-tools into system_installed + compact env"`

---

## Task 6: maintainer — surface compact system facts (not the bulky lists)

**Files:** modify `src/envstate/maintainer.py`; test `tests/test_maintainer_system_payload.py`

- [ ] **Step 1 — failing test:** drive `Maintainer.update` with a capturing fake client (reuse the
  pattern in `tests/test_maintainer_payload.py`), with a map carrying
  `system_installed=(Fact("pg_config","tool"),)` and `env={"os_release": "...", "build_tools": "gcc,pg_config", "dpkg_packages": "<huge>"}`.
  Assert the serialized `current_map` includes `build_tools` + `os_release`, includes a `system_tools`
  list (the curated tool/pkg-config names — small), and does **not** dump a full apt list.

- [ ] **Step 2 — run, expect FAIL.**

- [ ] **Step 3 — implement.** In `Maintainer.update`, add a compact system view to the payload:

```python
        _sys_tools = [f.name for f in current_map.system_installed if f.detail in ("tool", "pkgconfig")]
        # ... inside the "current_map" dict:
        "system_tools": _sys_tools[:40],         # build tools + pkg-config modules (compact)
        "os_release": current_map.env.get("os_release", ""),
        "build_tools": current_map.env.get("build_tools", ""),
```

Add one prompt line so the LLM uses them: *"Use `system_tools` / `build_tools` / `os_release` to
judge whether a `system`-layer problem is fixed and which package manager applies (apt/apk/yum)."*
Do **not** add `system_installed` wholesale (the apt list can be ~120 entries).

- [ ] **Step 4 — run** `tests/test_maintainer_system_payload.py tests/test_maintainer_narrowed.py tests/test_maintainer_payload.py tests/test_v1_maintainer.py` — PASS (output schema + narrowing unchanged).
- [ ] **Step 5 — commit:** `... -m "feat(maintainer): surface compact system facts (build_tools/os_release/system_tools)"`

---

## Final verification

- [ ] `... -m pytest tests/ -k "world_model or snapshot or extractor or apply_deterministic or maintainer or auto_resolve or system" --cov=src/envstate --cov-report=term-missing -q` — all green, ≥80% on changed modules.
- [ ] **[→EVAL] Re-run burr arm-v1** and confirm in the cycle trace: `env` now carries `os_release`/`build_tools`; `system_installed` populates; a seeded `pg_config` system problem clears the cycle after `libpq-dev` is installed (deterministic, not via Maintainer `resolved`).

---

## Non-goals (this iteration)

- **Proactive system `required`/`missing`** — no apt manifest source of truth; system stays reactive on discovery.
- **Header→package resolution** beyond the conservative `libX`/`X` check (e.g. `Python.h`→`python3-dev` mapping table) — future.
- **Problem-driven targeted probing** (`command -v <exact tool from signature>`) — the curated `SYSTEM_TOOL_PROBES` set covers the common cases; a per-problem probe is a future enhancement for arbitrary tools.
- **Non-apt package managers** (apk/yum) planning — `os_release` is surfaced so the LLM can branch, but we don't model them.

## Self-review

- **Asymmetry handled:** matching is artifact→provider (tool/pkg-config), not signature→apt-name (§1.1).
- **Back-compat:** `getattr(snap,'system_installed',())`; new field defaults `()` with symmetric
  serialization → existing `test_world_model.py` / `test_apply_deterministic.py` / `test_snapshot.py`
  stay green.
- **Token discipline:** bulky apt/pkg-config lists live in `system_installed` (host-side matching) and
  are excluded from both `env` and the Maintainer payload; only compact `build_tools`/`os_release`/
  curated `system_tools` reach the LLM.
- **Conservative resolve:** unrecognized signature shapes are kept (defer to Maintainer `resolved`),
  so we never *falsely* clear a system problem — the opposite failure mode from the pip
  `_auto_resolve_problems` substring over-match.
