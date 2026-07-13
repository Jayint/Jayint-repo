# Safe Construction-Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut v3 graph-construction wall-clock on large repos (mlflow ~25 min → target <10 min) **without changing the produced graph or `setup.sh`** — by making the one unavoidable closure install fast, not by skipping it.

**Architecture:** Phase A of `build_dep_graph` installs the entire resolved closure into a container to certify coverage and surface build-time toolchain gaps; Phase B then `ldd`/`import`-probes the *installed* container for runtime syslibs. A 3-agent adversarial review proved you **cannot** skip native-wheel installs (Phase B needs the `.so` on disk; the preflight probe misses the dlopen tail — e.g. opencv `libxcb` via a Qt plugin, real in `tinygrad`/`darts`). So the safe wins are: (1) swap `pip install` → `uv pip install` (parallel download + hardlink-from-cache install) + a `uv` cache volume; then optionally (2) install once at fixpoint convergence instead of every round, using a pinned-version metadata coverage oracle for intermediate rounds. The persistent **pip** download cache already exists (`jayint_pip_cache`), so cold-run downloads are the current cost.

**Tech Stack:** Python 3.10+ (`tomllib`/`tomli` fallback), `uv` (already a host dependency, `resolve_lock.py:33`), Docker via `src/sandbox.py`, pytest with the in-repo `FakeExecutor` fixture (`tests/depgraph/conftest.py`).

## Global Constraints

- **Byte-identical-graph gate (acceptance for the whole plan):** for a fixed corpus repo, the `DepGraph` produced after each task MUST equal the pre-change output. The renderer already emits an order-independent **`graph-hash: sha256:…`** line (`build_script.py:141` `_graph_hash` — SHA256 over sorted `(node.id, version, chosen_fix)` + sorted `(src, dst, relation)` edges); a matching hash proves PACKAGE/SYSTEM_LIB/TOOL nodes + edges + `build_from_source`/`chosen_fix` are identical. It does **not** cover SERVICE/CONFIG `#@need` lines (un-reciped by design), so pair it with a normalized `#@need` diff. Speed may change; the hash must not. See the Regression Suite section for the 10-repo tripwire + exact commands.
- **No Phase-B consumer changes.** `certified_import_links` (`build.py:631`), `ldd_probe` (`build.py:637`), `import_probe` (`build.py:641`), and `certify_all` must keep running against a container where the **fully converged closure (native wheels included) is really installed**. Any change that leaves a resolved package uninstalled before Phase B is out of scope (see Deferred: C4).
- **`uv` must reproduce pip's installed environment exactly:** same resolved versions on disk, same extension `.so` set, and build-backend failures must still surface the underlying compiler error to `extract_needs` so Tool nodes are created identically.
- **Preserve `INSTALL_TIMEOUT = 900`** (`probe.py:63`) as the per-install ceiling.
- **Unit tests use `FakeExecutor` — no Docker, no network** (see `tests/depgraph/test_probe.py:3`). Network-touching helpers take an injected `fetch=` seam (mirror `coverage.py:194 pypi_record_provider`) so tests pass a fake.
- Formatting: black + ruff, type annotations on all new signatures (per repo Python style rules).

## Already done / not in this plan

- **C2 persistent download cache — ALREADY IMPLEMENTED.** `sandbox.py:91-95` mounts named volume `jayint_pip_cache → /root/.cache/pip` (+ `jayint_apt_cache`) under `enable_cache_volume=True`, which `scripts/run_v3_e2e.py:210` sets. Task 1 only *adds* a sibling `uv` cache mount. Do NOT re-add a pip cache.

## Deferred (separate plans — with rationale)

- **C4 pure-Python-wheel skip:** even skipping a pure-Python wheel's install breaks `certify_all` (`pip show X` → node falsely `MISSING`) and `certified_import_links` (`packages_distributions()` misses it → falsely `unresolved`). Requires teaching those Phase-B consumers to accept a metadata-provides oracle for skipped packages. Minor win; own plan.
- **`wheel_oracle._wheel_matches_platform` hardening (Seam 2):** `wheel_oracle.py:120` `if "linux" not in low` matches **musllinux** against a glibc target, and there is no manylinux **policy-version** check. Dormant in the current corpus (all glibc `-slim` bases) and the `installable` flag (`wheel_oracle.py:179`) already handles the no-wheel-no-sdist case, so this is a correctness follow-up, not a speed change.
- **Wiring `artifact_map.resolve_artifact_map`** (the dead 3-tier oracle, `artifact_map.py:258`, zero callers in `build.py`): a wheel-availability correctness effort orthogonal to speed.

---

## File Structure

- `src/python_deps/depgraph/probe.py` — Task 1: extract the install command into a `uv`-based helper; keep build-gap parsing.
- `src/sandbox.py` — Task 1: add a `jayint_uv_cache → /root/.cache/uv` named-volume mount.
- `src/python_deps/depgraph/coverage.py` — Task 2: add a pinned-version wheel-top-levels reader + `pinned_record_provider`.
- `src/python_deps/depgraph/build.py` — Task 3: move `install_closure` out of the `_phase_a_fixpoint` loop to a single post-convergence call; use the pinned provider for in-loop coverage.
- Tests: `tests/depgraph/test_probe.py` (append + edit `:106`), new `tests/depgraph/test_coverage_pinned.py`, `tests/depgraph/test_build_phase_order.py` (append — file already exists).

**Verified against local branch (`john-v3-multi-lang`@`bedf97c`, 2026-07-08):** all `file:line` anchors exact; baseline `pytest tests/depgraph/` = 1138 passed; uncommitted "Fix A" diff (`emit.py`/`resolve_link.py`/`resolve_lock.py`/`wheel_oracle.py`) is a render-time gate, orthogonal to Tasks 1-3. `RecordProvider` type lives in `repair.py:143` (imported into `coverage.py:34`). Task 2's `_find_wheel` reuse is tag-blind but safe here: top-level module names are stable across a version's wheel variants, and the Task 3 byte-identical gate catches any exception.

---

### Task 1: `uv`-based install path + `uv` cache volume

> **BLOCKING PREREQUISITE (verified absent):** `uv` is **not** in any construction base image (stock `python:3.x`, `src/language_handlers.py:53`) and no provisioning step installs it. Task 1 Step 0 (below) is mandatory — unit tests pass without it (they never touch Docker), but **every real construction install fails with `uv: command not found`** until it lands.
>
> **CRITICAL RISK — install-target parity:** pip installs into the container's *system* site-packages; `import_probe`/`ldd_probe` (Phase B) look there. `uv pip install` defaults to seeking a venv and will install to the WRONG place (or error) unless targeted at the system env. Step 3 uses `--system`; Step 4b **verifies in a real container** that uv lands packages in the same site-packages pip uses. If it doesn't, Phase B breaks silently — this is the highest-risk item in the plan.

**Files:**
- Modify: `src/python_deps/depgraph/probe.py:104` and `:203` (the two `"python -m pip install "` command strings); update the existing assertion at `test_probe.py:106`.
- Modify: `src/sandbox.py:91-95` (cache-volume block)
- Modify: construction base image build (Step 0 — add `uv`)
- Test: `tests/depgraph/test_probe.py`

**Interfaces:**
- Consumes: `Executor.run(command, timeout) -> Result(ok, stderr)` (existing), `_spec`/`_sorted` (`probe.py:544/548`).
- Produces: module-level `_install_cmd(specs: str) -> str` in `probe.py` returning the `uv` install command string, used by both `install_closure` and `_reinstall_survivors`.

- [ ] **Step 0 (blocking): add `uv` to the construction base image**

Add a cached layer installing `uv` (`RUN pip install uv` or the astral installer) to the image `Sandbox` boots, OR bootstrap it in `sandbox.py` before the first install. Confirm: a container from the base image has `uv` on PATH (`docker run --rm <base> uv --version`).

- [ ] **Step 1: Write/adjust the tests** (a pure-function test for the command; update the EXISTING attempt test whose asserted prefix the swap breaks — no new graph-building test or `FakeExecutor` helper is needed, because `fake_executor.responses` is keyed by the substring `"pip install"`, which matches `uv pip install` too, so the existing build-gap tests at `test_probe.py:54-107` keep covering Tool-node discovery unchanged)

```python
# tests/depgraph/test_probe.py  (append) — pure-function test, no executor
from python_deps.depgraph.probe import _install_cmd

def test_install_cmd_uses_uv():
    cmd = _install_cmd("numpy==2.2.6 scipy==1.15.3")
    assert cmd.startswith("uv pip install")
    assert "numpy==2.2.6 scipy==1.15.3" in cmd
```

```python
# tests/depgraph/test_probe.py:106  — UPDATE the existing assertion the swap breaks
# (test_install_closure_records_attempt_on_packages already builds a graph, runs
#  install_closure, and asserts the recorded command — change ONLY the prefix):
    assert node.attempts[0].command.startswith("uv pip install")   # was "python -m pip install"
```

- [ ] **Step 2: Run tests to verify the new one fails and the edited one is red**

Run: `pytest tests/depgraph/test_probe.py -v`
Expected: `test_install_cmd_uses_uv` FAILs with `ImportError: cannot import name '_install_cmd'`; `test_install_closure_records_attempt_on_packages` FAILs on the prefix once you've edited its assertion ahead of the impl.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/probe.py  — add near _spec (line ~544)
def _install_cmd(specs: str) -> str:
    """The closure-install command. uv installs into the container's SYSTEM
    python (where pip installs today, so import_probe/ldd_probe find the .so),
    using hardlinks from its cache (fast on warm cache) + parallel downloads.
    Build-backend errors pass through to stderr identically to pip, so
    extract_needs still surfaces toolchain gaps. Cache stays ON (no --no-cache)."""
    return f"uv pip install --system {specs}"
```

```python
# src/python_deps/depgraph/probe.py:104  — replace
command = _install_cmd(" ".join(_spec(p) for p in _sorted(packages)))
# src/python_deps/depgraph/probe.py:203  — replace (identical shape, `survivors`)
command = _install_cmd(" ".join(_spec(p) for p in _sorted(survivors)))
```

```python
# src/sandbox.py:91-95  — add the uv cache sibling to the existing block
if enable_cache_volume:
    cache = dict(self.volumes or {})
    cache.setdefault("jayint_pip_cache", {"bind": "/root/.cache/pip", "mode": "rw"})
    cache.setdefault("jayint_uv_cache", {"bind": "/root/.cache/uv", "mode": "rw"})
    cache.setdefault("jayint_apt_cache", {"bind": "/var/cache/apt/archives", "mode": "rw"})
    self.volumes = cache
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/depgraph/test_probe.py -v`
Expected: PASS — `test_install_cmd_uses_uv`, the edited `test_install_closure_records_attempt_on_packages`, and all existing `install_closure`/`import_probe` tests (unchanged, because `"pip install"` ⊂ `"uv pip install"`).

- [ ] **Step 4b (blocking real-container check): verify uv install-target parity**

In a real container from the construction base image, confirm `uv pip install --system <pkg>` lands the package in the SAME site-packages `python -m pip install` uses (so Phase B's `import_probe`/`ldd_probe` find it):
Run: `docker run --rm <base> bash -lc 'uv pip install --system six && python -c "import six; print(six.__file__)"'`
Expected: `six` imports and its path is under the system `site-packages` (not a `.venv`). If uv installed elsewhere, adjust `_install_cmd` flags (e.g. `--python "$(command -v python3)" --system`) until parity holds. This gate protects the byte-identical guarantee — a wrong target silently guts Phase B.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/probe.py src/sandbox.py tests/depgraph/test_probe.py
git commit -m "perf(depgraph): install closure via uv + uv cache volume"
```

---

### MEASUREMENT CHECKPOINT (gates Tasks 2–3)

After Task 1, run a real construction on one large repo (mlflow) twice (cold then warm cache) with per-phase timing, and record: total construction time, and the wall-clock spent inside `install_closure` across all fixpoint rounds vs. the first round alone.

- If uv already makes rounds ≥2 near-instant (they re-audit an already-satisfied closure), the **per-round overhead C1 targets is gone** → **stop here**; Tasks 2–3 are not worth the refactor risk. Record the numbers in the commit message and mark Tasks 2–3 as "not needed (measured)."
- Only if rounds ≥2 still cost material time (multi-round repos with large closures) proceed to Tasks 2–3.

Command sketch (VM): set `V3_ADAPTER_RUN_TIMEOUT` high, run `run_v3_e2e.py` on the mlflow checkout, and time `install_closure` via a temporary `logger.info` timestamp around `probe.py:105` (remove before commit) or the existing `_meta.json duration_s`.

---

### Task 2: pinned-version metadata coverage provider

**Files:**
- Modify: `src/python_deps/depgraph/coverage.py` (add after `pypi_record_provider`, line ~217)
- Test: `tests/depgraph/test_coverage_pinned.py` (create)

**Interfaces:**
- Consumes: `_wheel_top_levels(path)` (`coverage.py:138`), `_http_json`/`_find_wheel`/`_UA`/`_HTTP_TIMEOUT`/`_MAX_WHEEL_BYTES` (`coverage.py:105-135`), `RecordProvider = Callable[[str], set[str] | None]`.
- Produces: `_pinned_wheel_top_levels(dist: str, version: str) -> set[str] | None` and `pinned_record_provider(version_map: dict[str, str], *, fetch=_pinned_wheel_top_levels) -> RecordProvider`.

- [ ] **Step 1: Write the failing test**

```python
# tests/depgraph/test_coverage_pinned.py  (create)
from python_deps.depgraph.coverage import pinned_record_provider

def test_pinned_provider_reads_the_pinned_version_not_latest():
    calls = []
    def fake_fetch(dist, version):
        calls.append((dist, version))
        return {"onnxruntime"} if version == "1.23.2" else {"WRONG"}
    prov = pinned_record_provider({"onnxruntime": "1.23.2"}, fetch=fake_fetch)
    assert prov("onnxruntime") == {"onnxruntime"}
    assert calls == [("onnxruntime", "1.23.2")]      # pinned version, fetched once

def test_pinned_provider_none_for_unknown_dist_and_caches():
    prov = pinned_record_provider({}, fetch=lambda d, v: (_ for _ in ()).throw(AssertionError("no version")))
    assert prov("not-in-closure") is None            # no version -> None, fetch never called
    assert prov("not-in-closure") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_coverage_pinned.py -v`
Expected: FAIL — `ImportError: cannot import name 'pinned_record_provider'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/python_deps/depgraph/coverage.py  — add after pypi_record_provider (line ~217)
_PYPI_JSON_PINNED = "https://pypi.org/pypi/{dist}/{version}/json"


def _pinned_wheel_top_levels(dist: str, version: str) -> "set[str] | None":
    """Top-level modules of the EXACT pinned version's wheel (not 'latest').

    Fetches ``/{dist}/{version}/json`` and reuses the same wheel download +
    RECORD/top_level read as :func:`_default_wheel_top_levels`. ``None`` on any
    absence/failure (read as 'blind' by the coverage oracle)."""
    data = _http_json(_PYPI_JSON_PINNED.format(dist=dist, version=version))
    if data is None:
        return None
    url, size = _find_wheel(data)
    if not url or (size and size > _MAX_WHEEL_BYTES):
        return None
    tmp = tempfile.mkdtemp(prefix="rec-whl-")
    try:
        path = os.path.join(tmp, "w.whl")
        with urllib.request.urlopen(  # noqa: S310 — PyPI-hosted wheel
            urllib.request.Request(url, headers=_UA), timeout=_HTTP_TIMEOUT
        ) as response, open(path, "wb") as handle:
            shutil.copyfileobj(response, handle)
        return _wheel_top_levels(path)
    except Exception:  # noqa: BLE001
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pinned_record_provider(
    version_map: dict[str, str], *, fetch=_pinned_wheel_top_levels
) -> "RecordProvider":
    """RecordProvider keyed by NAME that reads the RESOLVED version's provides.

    ``version_map`` is ``{normalized-name -> version}`` from the resolved
    closure's PACKAGE nodes. A dist with no mapped version returns ``None``
    (never guessed). Cached per name; ``None`` cached too."""
    cache: dict[str, "set[str] | None"] = {}

    def provider(dist: str) -> "set[str] | None":
        key = normalize_package_name(dist)
        if key not in cache:
            version = version_map.get(key)
            cache[key] = fetch(dist, version) if version else None
        return cache[key]

    return provider
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_coverage_pinned.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/coverage.py tests/depgraph/test_coverage_pinned.py
git commit -m "feat(depgraph): pinned-version metadata record provider for coverage"
```

---

### Task 3: install once at fixpoint convergence (not per round)

**Files:**
- Modify: `src/python_deps/depgraph/build.py:372-433` (`_phase_a_fixpoint` — drop the in-loop install; use pinned coverage) and the post-convergence install site in `_python_package_obligations` (`build.py:436-607`, before `_python_native_obligations` runs).
- Test: `tests/depgraph/test_build_phase_order.py`

**Interfaces:**
- Consumes: `install_closure` (`probe.py:84`), `resolved_record_coverage` (`coverage.py:39`), `pinned_record_provider` (Task 2), the per-round `pkg_nodes` (which carry `.name`/`.version`).
- Produces: no new public symbol; behavioral invariant — `install_closure` is called **exactly once**, after the fixpoint converges and before `_python_native_obligations`.

- [ ] **Step 1: Write the failing test** (install called exactly once; graph identical to per-round-install baseline)

```python
# tests/depgraph/test_build_phase_order.py  (append)
from python_deps.depgraph import build as build_mod

def test_install_closure_called_once_after_convergence(monkeypatch, multi_round_repo_fixture):
    calls = {"n": 0}
    real = build_mod.install_closure
    def counting_install(graph, executor):
        calls["n"] += 1
        return real(graph, executor)
    monkeypatch.setattr(build_mod, "install_closure", counting_install)
    build_mod.build_dep_graph(**multi_round_repo_fixture)   # a repo that needs >=2 audit rounds
    assert calls["n"] == 1     # was N (once per round) before this task
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_build_phase_order.py::test_install_closure_called_once_after_convergence -v`
Expected: FAIL — `assert 2 == 1` (or N), because `install_closure` currently runs inside the `while True:` loop at `build.py:384`.
(`multi_round_repo_fixture` does not exist yet — **do not invent a mechanism**; factor the proven pattern from `tests/depgraph/test_build_phase_order.py:187-233` (`test_record_coverage_drives_loop_not_per_round_packages_distributions`): a real `tmp_path` repo + `SequencedFakeExecutor` (`conftest.py:83-151`) + an under-declared `import yaml` (no declared dep) that forces ≥2 fixpoint rounds. Invoke via `build_dep_graph(repo_path, container_executor, host_executor=..., record_provider=...)` — only the first two params are positional.)

- [ ] **Step 3: Write minimal implementation**

In `_phase_a_fixpoint` (`build.py:372-433`): delete the in-loop `graph = install_closure(graph, container_executor)` at line 384, and replace the record-provider used by `resolved_record_coverage` with a per-round pinned provider built from the just-resolved `pkg_nodes`:

```python
# src/python_deps/depgraph/build.py  — inside _phase_a_fixpoint, after resolve_closure/reconcile,
# REMOVE:  graph = install_closure(graph, container_executor)
# and build coverage from pinned metadata instead of the (now-empty) installed reader:
version_map = {
    _canon(n.name): n.version
    for n in pkg_nodes
    if n.version
}
provided = resolved_record_coverage(pkg_nodes, pinned_record_provider(version_map))
```

Then add the single install at convergence, in `_python_package_obligations`, immediately after `_phase_a_fixpoint(...)` returns (`build.py:547`) and before the graph is handed to native obligations:

```python
# src/python_deps/depgraph/build.py  — after graph = _phase_a_fixpoint(...) (line ~547)
graph = install_closure(graph, container_executor)   # ONE real full install of the converged closure
```

(Imports: add `pinned_record_provider` to the existing `from python_deps.depgraph.coverage import (...)` block near `build.py:36`.)

- [ ] **Step 4: Run test to verify it passes + no regression**

Run: `pytest tests/depgraph/test_build_phase_order.py tests/depgraph/test_build.py -v`
Expected: PASS — install called once; existing build tests unchanged.

- [ ] **Step 5: Byte-identical integration check** (the real gate — run the full Regression Suite)

Run the 10-repo regression tripwire (see the **Regression Suite** section): compare the `graph-hash` line after this change against the captured baselines, plus the normalized `#@need` diff. All 10 hashes must match and every `#@need` set must be unchanged.
Focus sentinels: **`tinygrad`** and **`darts`** (the corpus's ONLY syslib repos — `libxcb.so.1`/`libgomp.so.1` via the dlopen tail) MUST keep every `#@node syslib:` line. If a syslib node disappears, STOP — the single convergence install is not running before Phase B; fix ordering.
Also confirm live (temporary counter around `install_closure`) that on **`feast-dev/feast`** (the plan's multi-round case) `install_closure` is called **exactly once** — `discovered_by=AUDIT` is not rendered, so round-count can only be verified live.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_build_phase_order.py
git commit -m "perf(depgraph): install converged closure once, not per fixpoint round"
```

---

## Regression Suite (10-repo tripwire)

Run after **every** task. Baseline `setup.sh` for all 50 repos already exists at
`/opt/runs/john-planner-v3/construction-python50-20260707-072356/output/<owner>/<repo>/setup.sh` — no re-run needed to establish the golden.

**The 10 repos (chosen for distinct-path coverage, biased to affordability — 4 large / 5 medium / 1 small):**

| repo | closure | path guarded |
|---|---|---|
| `tinygrad/tinygrad` | 182 reciped (11 syslib, 2 tool) | **dlopen-tail syslib** (`libxcb.so.1`) — 1 of only 2 syslib repos in the corpus |
| `unit8co/darts` | 272 reciped (3 syslib) | **dlopen-tail syslib** (`libgomp.so.1`, lightgbm) + biggest raw pip closure (269) |
| `mlflow/mlflow` | 464 reciped (3 tool) | **biggest closure (461 pkg) + `build_failed` sentinel** — graph must stay identical though the eval build fails |
| `feast-dev/feast` | 188 reciped + 109 needs | **multi-round fixpoint** (plan's named case) + service(11)/config(98) |
| `testcontainers/testcontainers-python` | 232 reciped (60 tool) | large **apt/toolchain** closure, clean success |
| `crytic/slither` | 99 reciped (22 tool) | 2nd distinct toolchain shape (solc/web3), medium scale |
| `supabase/supabase-py` | 38 reciped + 12 needs | service(6)/config(6) at cheap scale |
| `rq/rq` | 41 reciped (pure pip) | **pure-Python sanity** — isolates the pip→uv swap, no native noise |
| `pre-commit/pre-commit` | 21 reciped (pure pip) | **smallest pure-pip closure** — cheapest full run |
| `nginx-proxy/nginx-proxy` | 12 reciped + 6 needs | smallest overall, still tool(1)/service(3)/config(3) |

**Step 1 — snapshot the golden (once, before any change):**
```bash
BASE=/opt/harness/regression_baselines/construction-speed-safe
REPOS="tinygrad/tinygrad unit8co/darts mlflow/mlflow feast-dev/feast testcontainers/testcontainers-python crytic/slither supabase/supabase-py rq/rq pre-commit/pre-commit nginx-proxy/nginx-proxy"
SRC=/opt/runs/john-planner-v3/construction-python50-20260707-072356/output
for r in $REPOS; do mkdir -p "$BASE/before/$r"; cp "$SRC/$r/setup.sh" "$BASE/before/$r/setup.sh"; done
```

**Step 2 — re-run construction on the 10, pinned identically:** same `base_image` and `head_sha` per repo (from each `_result_row.json`). **`mlflow/mlflow` has an empty `head_sha`** — pin it from the materialized checkout (`git -C $SRC/mlflow/mlflow/v3_src rev-parse HEAD`) or reuse that `v3_src` tree, else a moved upstream HEAD yields a false diff. Write new `setup.sh` under `$BASE/after/$r/`.

**Step 3 — two-tier diff:**
```bash
# Primary: order-independent graph-hash (PACKAGE/SYSTEM_LIB/TOOL + edges + build_from_source/chosen_fix)
for r in $REPOS; do
  b=$(grep -oE 'graph-hash: sha256:[0-9a-f]+' "$BASE/before/$r/setup.sh")
  a=$(grep -oE 'graph-hash: sha256:[0-9a-f]+' "$BASE/after/$r/setup.sh")
  [ "$b" = "$a" ] && echo "OK   $r" || echo "DIFF $r  before=$b after=$a"
done
# Secondary: SERVICE/CONFIG needs + localize any hash mismatch. Strip evidence= (pip vs uv
# error text differs legitimately) and sort (uv topo tie-break order may differ; graph doesn't).
normalize() { grep -E '^\s*#@(node|need|check)\s' "$1" | sed -E 's/  evidence=.*$//' | sort; }
for r in $REPOS; do echo "== $r =="; diff <(normalize "$BASE/before/$r/setup.sh") <(normalize "$BASE/after/$r/setup.sh"); done
```
PASS = all 10 hashes match AND the secondary diff is empty. Any surviving `#@` line is a real node/edge/state/check change to investigate.

**Known gaps to close live:** (1) `discovered_by=AUDIT` is never rendered, so Task 3's "install once" is confirmed only via a temporary counter around `install_closure` on `feast` (Task 3 Step 5). (2) `evidence=` rewording under uv is expected noise, stripped above — but a *new* or *vanished* `#@node syslib:`/`tool:` line (not just reworded evidence) on tinygrad/darts is a real regression.

## Self-Review

**1. Spec coverage:**
- C3 (uv install + uv cache) → Task 1. ✓
- C1a (pinned-version coverage provider) → Task 2. ✓
- C1 (install-once-at-convergence) → Task 3. ✓
- C2 (pip cache) → already implemented (`sandbox.py:91-95`); documented, uv-cache sibling added in Task 1. ✓
- C4 + wheel_oracle hardening + artifact_map wiring → explicitly Deferred with rationale. ✓
- Byte-identical acceptance gate → Global Constraints + Task 3 Step 5. ✓

**2. Placeholder scan:** every code step shows real code; commands are exact; no "TBD"/"handle edge cases". The two fixture dependencies (`FakeExecutor.set_result`/`last_command`, `multi_round_repo_fixture`) are flagged with how to add them. ✓

**3. Type consistency:** `_install_cmd(specs: str) -> str`, `_pinned_wheel_top_levels(dist, version) -> set[str] | None`, `pinned_record_provider(version_map: dict[str,str]) -> RecordProvider` are used consistently across tasks; `RecordProvider` is the existing `coverage.py` type. `resolved_record_coverage(pkg_nodes, provider)` signature matches `coverage.py:39`. ✓

**Ordering note:** Task 1 is the high-confidence win and may make Tasks 2–3 unnecessary — the Measurement Checkpoint gates them. Do not implement Tasks 2–3 unless the measurement shows material per-round overhead survives uv.
