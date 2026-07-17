# Option B — Lazy `apt-file` Install for Unknown Soname Resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the unknown-soname apt-name gap left by option A. When `resolve_soname_apt` gets a table miss AND `apt-file` is absent from the container, lazily install and index it (once per build), then run the existing `apt-file search` path so unknown sonames return a real apt package name instead of `(None, "unresolved")`.

**Proven baseline:** In a container with `apt-file` present and indexed, `resolve_soname_apt("libX11.so.6", executor)` returns `("libx11-6", "apt-file")`. The resolution code is correct; option B only provisions `apt-file` at the right moment.

**Scope IN:**
- Add `ensure_apt_file(executor) -> bool` to `src/python_deps/depgraph/apt_resolve.py` (lazy install+index on demand).
- Update `resolve_soname_apt` to call `ensure_apt_file` on a table miss (gating the existing `apt-file search` path).
- Update `tests/depgraph/test_apt_resolve.py`: add `QueuedFakeExecutor`, new `ensure_apt_file` tests, new lazy-install resolution tests, update one existing test that needs a `command -v apt-file` response.
- Add a one-line "cache" annotation to `NATIVE_LIB_TO_APT` in `tables.py`.

**Scope OUT (non-goals):**
- No changes to `ldd_probe.py`, `probe.py`, `build.py`, or any caller of `resolve_soname_apt` — the fix is entirely inside `apt_resolve.py`.
- No module-level state (no `_apt_file_ready` global or module cache).
- No baking `apt-file` into the base image (that is a separate infra decision).
- No conda-forge or PEP 725 paths.

**Forbidden files (do not touch):** `models.py`, `graph.py`, `external_graph/*`, `resolver.py`, `z3_adapter.py`, `pypi_metadata.py`.

---

## Design Decisions

### D1 — Readiness check: container filesystem as the cache

`command -v apt-file` is the readiness probe. Exit code 0 = apt-file binary present and on PATH; non-zero = absent. After a successful install, `/usr/bin/apt-file` exists and the check succeeds, so every subsequent call to `resolve_soname_apt` with an unknown soname hits `command -v apt-file` → rc=0 → skips the install block entirely. No module-level flag, no hidden state — the container filesystem is the cache, exactly as the task description requests. Cost per unknown soname after the first install: one trivially fast `command -v` call.

### D2 — Lazy install: exact commands and timeouts

Three sequential commands; each must succeed for the next to run:

| Command | Timeout | Rationale |
|---|---|---|
| `apt-get update` | 120 s | Refresh package index; fast on warm cache, ~10–20 s cold |
| `apt-get install -y apt-file` | 120 s | Small binary; installs in seconds |
| `apt-file update` | 180 s | Downloads ~50 MB Contents index; generously bounded |

These match the existing pattern in `probe.py` where `INSTALL_TIMEOUT = 900` is used for pip (generous headroom). Any single failure short-circuits and returns `False`.

Triggered only on a **table miss** — `apt_for_soname(soname)` returns `None`.

### D3 — Idempotent and graceful

- If `command -v apt-file` succeeds: skip all install commands.
- If any install step fails (no network, non-Debian distro, missing `apt-get`): `ensure_apt_file` returns `False`; `resolve_soname_apt` returns `(None, "unresolved")` — identical to today's behavior on a table miss. Never raises, never worse than current.
- A successful install is not re-run: the container filesystem is the cache (D1).

### D4 — Table stays as the fast offline first path

`NATIVE_LIB_TO_APT` is queried first — zero executor calls for known sonames. It is now documented as a **cache** (known-soname precomputed entries) that `apt-file` fills on misses. The table is not deleted or demoted in authority; it remains the preferred offline path.

### D5 — Testability: `QueuedFakeExecutor` for state transitions

`FakeExecutor` returns the same canned result per substring key — it cannot model "absent on call 1, present on call 2" for `command -v apt-file`. The fix is a tiny `QueuedFakeExecutor` defined in `test_apt_resolve.py` (not in `conftest.py` — it is only needed here) that maintains a `deque` per key and pops from the front on each call. This lets tests script exact call sequences (absent → install succeeds → present). It is the minimal change that makes state-transition tests robust without complicating the shared fixture.

The existing `test_resolve_unknown_soname_falls_back_to_apt_file` sets `"apt-file search"` and `"sysconfig"` responses but has no response for `"command -v apt-file"`, so the FakeExecutor returns rc=127 → `ensure_apt_file` tries `apt-get update` → rc=127 → returns False → test would return `(None, "unresolved")` instead of the expected `("libfoo7", "apt-file")`. **This test must be updated** (Task 1, step 4) to add `"command -v apt-file": make_result(returncode=0)` — simulating a container where `apt-file` is already installed.

### D6 — Cost guard: zero cost for table hits and pure-Python builds

`apt_for_soname(soname)` is a pure dict lookup. A table hit short-circuits before any executor call is made — `ensure_apt_file` is never invoked. Packages with no native extensions never reach `resolve_soname_apt` in the first place (Stage 4.5 only runs `ldd` on packages with extension modules). Pure-Python builds pay zero extra executor calls.

---

## Shared Interfaces

### `src/python_deps/depgraph/apt_resolve.py` — EDIT

Add one new public function immediately before `resolve_soname_apt`:

```python
# Timeouts for the three-step lazy apt-file provisioning sequence.
_APT_UPDATE_TIMEOUT = 120   # apt-get update (~10-20 s cold, bounded at 120)
_APT_INSTALL_TIMEOUT = 120  # apt-get install -y apt-file (small binary)
_APT_FILE_UPDATE_TIMEOUT = 180  # apt-file update (~50 MB Contents index)

def ensure_apt_file(executor: Executor) -> bool:
    """Install and index apt-file in the container if absent.

    Uses ``command -v apt-file`` as the readiness check — the container
    filesystem is the cache (once installed, the binary is on PATH and the
    check succeeds on every subsequent call, so the install block is skipped).
    Returns True when apt-file is ready to use.  Returns False on any failure
    (no network, non-Debian, apt-get absent); the caller then returns
    (None, "unresolved") — never worse than today.
    """
    if executor.run("command -v apt-file").ok:
        return True
    for cmd, timeout in [
        ("apt-get update", _APT_UPDATE_TIMEOUT),
        ("apt-get install -y apt-file", _APT_INSTALL_TIMEOUT),
        ("apt-file update", _APT_FILE_UPDATE_TIMEOUT),
    ]:
        if not executor.run(cmd, timeout=timeout).ok:
            return False
    return True
```

Update `resolve_soname_apt` to call `ensure_apt_file` on a table miss, replacing the bare `apt-file search` path:

```python
def resolve_soname_apt(soname: str, executor: Executor) -> tuple[str | None, str]:
    """Resolve a .so soname to an apt package: table first, then apt-file.

    The curated table (NATIVE_LIB_TO_APT) is the offline fast path; a hit
    short-circuits before any executor call.  On a miss, ``ensure_apt_file``
    lazily installs and indexes apt-file in the container if absent (option B:
    once per build, only on an unknown soname), then ``apt-file search``
    resolves the name.  Any failure returns ``(None, "unresolved")`` — never
    worse than the table-only path.
    """
    hit = apt_for_soname(soname)
    if hit:
        return hit, "table"
    if not ensure_apt_file(executor):
        return None, "unresolved"
    triplet = multiarch_triplet(executor)
    result = executor.run(f"apt-file search {shlex.quote(soname)}")
    if not result.ok:
        return None, "unresolved"
    pkg = parse_apt_file_search(result.stdout, soname, triplet)
    if pkg:
        return pkg, "apt-file"
    return None, "unresolved"
```

### `src/python_deps/depgraph/tables.py` — EDIT (one line)

Add a cache annotation comment to `NATIVE_LIB_TO_APT`:

```python
# Fast offline cache: known soname -> apt package.  apt-file fills misses at runtime
# (option B lazy install).  Do NOT delete entries — they short-circuit executor calls.
NATIVE_LIB_TO_APT: dict[str, str] = {
    ...
```

---

## Tasks

> T0 is the only prerequisite. T1 depends on T0. T2 depends on T1.

### Task 0 — Add `ensure_apt_file` + update `resolve_soname_apt`

**Files:** `src/python_deps/depgraph/apt_resolve.py`, `src/python_deps/depgraph/tables.py`

- [ ] Add `_APT_UPDATE_TIMEOUT`, `_APT_INSTALL_TIMEOUT`, `_APT_FILE_UPDATE_TIMEOUT` constants immediately before `ensure_apt_file`.
- [ ] Implement `ensure_apt_file(executor: Executor) -> bool` as specified in Shared Interfaces above, after the timeout constants and before `resolve_soname_apt`.
- [ ] Update `resolve_soname_apt` body: call `ensure_apt_file(executor)` on a table miss; return `(None, "unresolved")` if it returns `False`; otherwise fall through to the existing `multiarch_triplet` + `apt-file search` + `parse_apt_file_search` path unchanged.
- [ ] Add the one-line cache annotation comment to `NATIVE_LIB_TO_APT` in `tables.py`.
- **Acceptance:** `python3 -m pytest tests/depgraph/test_apt_resolve.py -q` — the three existing `resolve_soname_apt` tests plus `test_multiarch_triplet_none_when_probe_fails` all pass. (The existing `test_resolve_unknown_soname_falls_back_to_apt_file` will FAIL after this task because its `FakeExecutor` now hits `command -v apt-file` → rc=127 → install attempt → apt-get update → rc=127 → `ensure_apt_file` returns False → `(None, "unresolved")`. That breakage is expected and fixed in Task 1.)

### Task 1 — Unit tests for `ensure_apt_file` + update broken existing test

**Files:** `tests/depgraph/test_apt_resolve.py`

- [ ] **Step 1:** Define `QueuedFakeExecutor` at module scope in `test_apt_resolve.py` (not in `conftest.py` — it is only needed here). It maintains a `dict[str, collections.deque[CommandResult]]`; `run(command)` pops from the front of the first matching queue (longest-key wins, mirroring `FakeExecutor`); falls back to rc=127 when no entry or queue is empty.

```python
# tests/depgraph/test_apt_resolve.py
import collections
from python_deps.depgraph.executor import CommandResult

class QueuedFakeExecutor:
    """FakeExecutor variant where each key maps to a FIFO of results.

    Enables state-transition tests (e.g. command -v apt-file: absent then present).
    Longest matching key wins (mirrors FakeExecutor). Falls back to rc=127 when
    the queue for a matching key is exhausted or no key matches.
    """
    def __init__(self, queues: dict[str, list[CommandResult]]) -> None:
        self.queues: dict[str, collections.deque[CommandResult]] = {
            k: collections.deque(v) for k, v in queues.items()
        }
        self.calls: list[str] = []
        self.timeouts: list[int] = []

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        self.calls.append(command)
        self.timeouts.append(timeout)
        matches = [k for k in self.queues if k in command and self.queues[k]]
        if matches:
            best = max(matches, key=len)
            return self.queues[best].popleft()
        return CommandResult(command=command, returncode=127, stdout="", stderr="no fake response")
```

- [ ] **Step 2:** Add `ensure_apt_file` unit tests:

  - `test_ensure_apt_file_already_present_returns_true_no_install`: `command -v apt-file` → rc=0; assert returns `True`; assert no `apt-get` call in `calls`.
  - `test_ensure_apt_file_absent_installs_and_returns_true`: queue `command -v apt-file` → [rc=127], `apt-get update` → [rc=0], `apt-get install` → [rc=0], `apt-file update` → [rc=0]; assert returns `True`; assert `calls` contains all four commands in order; assert `apt-file update` timeout >= 120 (the generous slot).
  - `test_ensure_apt_file_update_fails_returns_false`: queue `command -v apt-file` → [rc=127], `apt-get update` → [rc=1]; assert returns `False`; assert no `apt-get install` call.
  - `test_ensure_apt_file_install_fails_returns_false`: queue `command -v apt-file` → [rc=127], `apt-get update` → [rc=0], `apt-get install` → [rc=1]; assert returns `False`.
  - `test_ensure_apt_file_index_fails_returns_false`: all three apt steps queued, `apt-file update` → rc=1; assert returns `False`.

- [ ] **Step 3:** Add `resolve_soname_apt` lazy-install tests (using `QueuedFakeExecutor` or plain `FakeExecutor` where state is not needed):

  - `test_resolve_unknown_soname_lazy_install_resolves`: `command -v apt-file` → rc=127, install sequence all rc=0, `sysconfig` → `"x86_64-linux-gnu"`, `apt-file search libfoo.so.7` → package output; assert returns `("libfoo7", "apt-file")`.
  - `test_resolve_unknown_soname_install_failure_returns_unresolved`: `command -v apt-file` → rc=127, `apt-get update` → rc=1; assert returns `(None, "unresolved")`.
  - `test_resolve_table_hit_pays_zero_cost_no_apt_file_check` (asserts `fake_executor.calls == []` for `libGL.so.1`): verify existing `test_resolve_known_soname_uses_table_without_executor` still passes unchanged (table hits do not call `ensure_apt_file` at all).
  - `test_resolve_second_unknown_soname_skips_install`: use `QueuedFakeExecutor`; queue `command -v apt-file` → [rc=127, rc=0] (first absent, then present after install), install sequence → [rc=0, rc=0, rc=0]; `sysconfig` → [rc=0 "x86_64-linux-gnu", rc=0 "x86_64-linux-gnu"]; `apt-file search libfoo.so.7` → [package output for libfoo7]; `apt-file search libbar.so.3` → [package output for libbar3]; call `resolve_soname_apt("libfoo.so.7", ex)` then `resolve_soname_apt("libbar.so.3", ex)`; assert both succeed; assert exactly two `apt-get` calls total (update + install, not repeated for second soname).

- [ ] **Step 4:** Update the existing `test_resolve_unknown_soname_falls_back_to_apt_file` to add `"command -v apt-file": make_result_fixture(returncode=0)` to `fake_executor.responses` (simulates apt-file already installed → `ensure_apt_file` returns immediately → test exercises the existing apt-file search path as before).

- [ ] **Step 5:** Verify `test_resolve_unknown_soname_unresolved_when_apt_file_missing` still passes without change. With an empty `FakeExecutor`, `command -v apt-file` → rc=127 → `apt-get update` → rc=127 → `ensure_apt_file` returns False → returns `(None, "unresolved")`. Assert unchanged. (Note: `calls` now includes `command -v apt-file` and `apt-get update`, but the test only asserts on return values — it passes.)

- **Acceptance:** `python3 -m pytest tests/depgraph/test_apt_resolve.py -v` — all tests pass, including the updated existing test and the new `ensure_apt_file` + lazy-install tests.

### Task 2 — Full depgraph suite regression

**Files:** read-only (run tests, fix any unexpected breakage)

- [ ] Run `python3 -m pytest tests/depgraph/ -q` and confirm all tests pass.
- [ ] If any test outside `test_apt_resolve.py` fails: it uses a `FakeExecutor` with no `command -v apt-file` response, causing `ensure_apt_file` to return False and resolve_soname_apt to return `(None, "unresolved")` — which is the same as the previous behavior. Any assertion on executor `calls` length may break; add `"command -v apt-file": make_result(returncode=127)` (explicit absent) or `"command -v apt-file": make_result(returncode=0)` (already installed, skips apt-get) to the relevant FakeExecutor responses to restore intent.
- [ ] Confirm no new `apt-get` calls appear in tests that only target known sonames (cost guard).
- **Acceptance:** `python3 -m pytest tests/depgraph/ -q` — full suite green, zero regressions.

---

## Risks and Mitigations

- **Non-Debian containers.** `apt-get` absent → `ensure_apt_file` fails gracefully → `(None, "unresolved")`. Same as today.
- **No network in container.** `apt-get update` or `apt-file update` fails → `ensure_apt_file` returns False → `(None, "unresolved")`. Same as today.
- **`apt-file update` is slow (~50 MB, ~20–30 s).** Bounded at 180 s. Only paid ONCE per build, and only when an unknown soname is actually encountered. Builds with no unknown sonames (all sonames in `NATIVE_LIB_TO_APT` or no native extensions) pay zero cost.
- **`command -v apt-file` round-trip on every unknown soname after first install.** Cost is negligible (the command exits in milliseconds). Avoids all module-level state.
- **`apt-file search` returns no match** even after install. Falls through to `(None, "unresolved")`. Unchanged from the post-D1-install path.
- **Existing test breakage.** Exactly one existing test (`test_resolve_unknown_soname_falls_back_to_apt_file`) breaks because its `FakeExecutor` now encounters `command -v apt-file` → rc=127 → install attempt fails. Fixed in Task 1 Step 4 by adding `"command -v apt-file": rc=0`.

---

## One-Line Summary

Add `ensure_apt_file(executor) -> bool` to `apt_resolve.py` — lazy `apt-get update && apt-get install -y apt-file && apt-file update` (once per container, only on a table miss, filesystem-cached via `command -v apt-file`) — and gate the existing `apt-file search` path on it, so unknown sonames resolve to real apt names instead of `(None, "unresolved")` on slim images.
