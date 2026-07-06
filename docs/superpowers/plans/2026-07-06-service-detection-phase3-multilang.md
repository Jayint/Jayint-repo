# Service Detection as Phase 3 (Multi-Lang Port) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the finished clean service-detection tier from `v3-core@8d2d7d4` into `john-v3-multi-lang` and expose it as a first-class **Phase 3** (`provider.service_obligations`) in the EcosystemProvider seam, without perturbing the Phase 1/2 build path.

**Architecture:** A file-level hybrid port (copy new modules + take v3-core's post-flip versions of untouched shared files + a few trivial 3-way merges + one atomic delete), followed by a thin `service_obligations` provider method that formalizes the injected-classifier call that already sits after `build_dep_graph` in `advise.build_advisory_for_repo`. The service classifier stays envstate-owned and flows in as an opaque callable, so `python_deps`/`ecosystems` stay pure.

**Tech Stack:** Python 3, pytest. `python_deps.depgraph` + `ecosystems` + `envstate`. No new deps.

**Design doc:** `docs/superpowers/specs/2026-07-06-service-detection-phase3-multilang-design.md` (read it first).

## Global Constraints

- **Source of truth for ported files is `v3-core@8d2d7d4`.** Extract with `git show 8d2d7d4:<path> > <path>` (worktrees share the object store — this works from the multi-lang worktree). Do NOT hand-transcribe ported file bodies.
- **Do NOT modify `build_dep_graph`'s signature or return type** — the eval harness (`src/eval/language_package_eval/coverage.py`) consumes its return. Phase 3 runs strictly *outside* it.
- **`python_deps` and `ecosystems` must never `import envstate`** — the service classifier flows in as an opaque callable only.
- **The clean contract:** a Service is certified/scheduled **iff** it carries `data["setup"]`. No `service_confidence`/`binding`/`start_recipe`/`bind_recipe`/`DataAsset`.
- **Service certify is live-only + env-gated:** services are certified in the live per-cycle container via `depgraph_live.certify_refresh`, gated by `DOCKERAGENT_ENABLE_SERVICE_PROVISION`; the scratch `build_dep_graph.certify_all` never flips them (defaults `allow_service_certify=False`). Default build path stays byte-identical.
- **`arch` must never be `None` when a classifier is injected** — `apply_arch` does `arch["dpkg"]`; `arch=None` silently drops all service nodes. `arch` comes from `choose_base_image(...).platform_override`.
- **Tests run with** `PYTHONPATH=src python3 -m pytest … -q -k "not docker"`. Run `tests/depgraph`, `tests/envstate`, `tests/ecosystems`, and top-level `tests/` **separately** (mixing dirs collides sibling `conftest.py` module names).
- **Residue gate includes `tests/`:** `grep -rn '"service_confidence"\|"start_recipe"\|"bind_recipe"\|data_asset_id\|DATA_ASSET' src/ scripts/ tests/` must be EMPTY after Task 3.
- **Nothing is pushed.** Local commits only; do not push or touch the `v3-core` worktree.

---

### Task 1: Port the pure depgraph clean core (additive)

Brings the pure (no LLM/network) building blocks. Nothing imports them yet, so the tree stays green regardless.

**Files:**
- Create (extract from `8d2d7d4`): `src/python_deps/depgraph/provisioning_spec.py`, `src/python_deps/depgraph/translate_sanitize.py`, `src/python_deps/depgraph/repoint.py`, `src/python_deps/depgraph/service_recipes.py`
- Create (extract from `8d2d7d4`): `tests/depgraph/test_provisioning_spec.py`, `tests/depgraph/test_translate_sanitize.py`, `tests/depgraph/test_repoint.py`, `tests/depgraph/test_service_recipes_clean.py`

**Interfaces (produced, used by later tasks):**
- `provisioning_spec.iter_provisioning_spec(...) -> Iterable[ProvisioningSpec]`; `ProvisioningSpec` dataclass (name/kind/image/params/init_files/probe/port).
- `service_recipes.render_setup(kind, params) -> dict` (keys `install`/`start`/`probe`/`createdb`/`post`), `service_recipes.normalize_probe(probe, port) -> str` (read-only firewall), `service_recipes.render_probe_poll(probe) -> str`.
- `translate_sanitize.apply_arch(plan, arch)`, `translate_sanitize.apply_env(plan)` — `arch = {"dpkg": ..., "uname": ...}`.
- `repoint.render_bind_steps(specs, configs) -> list[str]` (`export VAR=…127.0.0.1…`).

- [ ] **Step 1: Extract the four source modules**

```bash
for f in provisioning_spec translate_sanitize repoint service_recipes; do
  git show 8d2d7d4:src/python_deps/depgraph/$f.py > src/python_deps/depgraph/$f.py
done
```

- [ ] **Step 2: Extract their four test files**

```bash
git show 8d2d7d4:tests/depgraph/test_provisioning_spec.py    > tests/depgraph/test_provisioning_spec.py
git show 8d2d7d4:tests/depgraph/test_translate_sanitize.py   > tests/depgraph/test_translate_sanitize.py
git show 8d2d7d4:tests/depgraph/test_repoint.py             > tests/depgraph/test_repoint.py
git show 8d2d7d4:tests/depgraph/test_service_recipes_clean.py > tests/depgraph/test_service_recipes_clean.py
```

- [ ] **Step 3: Run the four new test files**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_provisioning_spec.py tests/depgraph/test_translate_sanitize.py tests/depgraph/test_repoint.py tests/depgraph/test_service_recipes_clean.py -q`
Expected: PASS (all four green). If any import error mentions a missing symbol, STOP — the port set is incomplete; report it.

- [ ] **Step 4: Confirm nothing else regressed (these are additive)**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph -q -k "not docker"`
Expected: PASS at the pre-existing baseline (the four new files add tests; no existing test changes).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/provisioning_spec.py src/python_deps/depgraph/translate_sanitize.py src/python_deps/depgraph/repoint.py src/python_deps/depgraph/service_recipes.py tests/depgraph/test_provisioning_spec.py tests/depgraph/test_translate_sanitize.py tests/depgraph/test_repoint.py tests/depgraph/test_service_recipes_clean.py
git commit -m "feat(depgraph): port pure clean service core (provisioning_spec/translate_sanitize/repoint/service_recipes)"
```

---

### Task 2: Port the envstate translate core (additive)

Brings the two envstate modules that touch the outside world (LLM + network) but do NOT yet admit nodes. Still nothing wired.

**Files:**
- Create (extract from `8d2d7d4`): `src/envstate/provision_verify.py`, `src/envstate/service_translate.py`
- Create (extract from `8d2d7d4`): `tests/test_provision_verify.py`, `tests/test_service_translate.py`

**Interfaces:**
- Consumes: `service_recipes.render_setup`/`normalize_probe`, `translate_sanitize.apply_arch`/`apply_env` (Task 1).
- Produces: `provision_verify.verify_plan(plan) -> …` (static URL check, never raises); `service_translate.translate_service(client, model, spec, arch, repo) -> dict` (known kind → `render_setup`; exotic → LLM + `apply_arch`/`apply_env` + `verify_plan` + `normalize_probe`).

- [ ] **Step 1: Extract the two source modules** (provision_verify first — service_translate imports it)

```bash
git show 8d2d7d4:src/envstate/provision_verify.py  > src/envstate/provision_verify.py
git show 8d2d7d4:src/envstate/service_translate.py > src/envstate/service_translate.py
```

- [ ] **Step 2: Extract their two test files**

```bash
git show 8d2d7d4:tests/test_provision_verify.py  > tests/test_provision_verify.py
git show 8d2d7d4:tests/test_service_translate.py > tests/test_service_translate.py
```

- [ ] **Step 3: Run the two new test files**

Run: `PYTHONPATH=src python3 -m pytest tests/test_provision_verify.py tests/test_service_translate.py -q`
Expected: PASS (both green; `test_service_translate` uses a mocked client + real HTTP good/404). If a missing-symbol import error appears, STOP and report.

- [ ] **Step 4: Commit**

```bash
git add src/envstate/provision_verify.py src/envstate/service_translate.py tests/test_provision_verify.py tests/test_service_translate.py
git commit -m "feat(envstate): port clean translate core (provision_verify URL check + service_translate router)"
```

---

### Task 3: Atomic swap — setup-only consumers + classifier + entrypoint rewire + delete legacy

**This task is necessarily ONE atomic commit.** Every file that references the old service shape or the `DataAsset` tier changes together: taking v3-core's post-flip consumers makes certify/schedule key on `data["setup"]`, so the old `env_classifier`'s `service_confidence`/`DataAsset` output must be removed in the same commit, and every legacy test retargeted or deleted in the same commit. Intermediate states are red by construction; only the commit must be green.

**Files:**
- Modify (clean-take `8d2d7d4` — multi-lang left these untouched since fork): `src/python_deps/depgraph/patch.py`, `patch_gate.py`, `certify.py`, `schedule.py`, `advise.py`; `src/envstate/graph_scheduler.py`, `install_localizer.py`; `scripts/run_v3_e2e.py`
- Modify (3-way merge): `src/python_deps/depgraph/schema.py`, `ids.py`, `build_script.py`, `populate.py`
- Create (extract from `8d2d7d4`): `src/envstate/classify_services_clean.py`
- Delete: `src/envstate/env_classifier.py`
- Tests — Create (extract): `tests/depgraph/test_certify_setup_service.py`, `test_patch_gate_admit_clean.py`, `test_schedule_setup.py`; `tests/test_classify_services_clean.py`, `tests/test_graph_scheduler_setup.py`
- Tests — Modify (clean-take `8d2d7d4`): `tests/depgraph/test_advise.py`, `test_certify.py`, `test_ids.py`, `test_nonlib_runtime_state_routing.py`, `test_patch_gate_apply.py`, `test_patch_gate_validate.py`, `test_patch_parse.py`, `test_schedule.py`, `test_scheduler_frontier.py`, `test_schema.py`; `tests/envstate/test_graph_scheduler.py`; `tests/test_depgraph_live_certify.py`
- Tests — Delete: `tests/depgraph/test_patch_gate.py`, `test_schedule_binding.py`; `tests/test_env_classifier.py`, `tests/test_service_confidence_activation.py`

**Interfaces:**
- Consumes: Task 1 + Task 2 modules.
- Produces: `classify_services_clean.classify_services_clean(graph, repo_path, client=None, model="", arch=None) -> DepGraph` and `classify_services_clean.make_construction_classifier(client=None, model="", arch=None) -> Callable[[DepGraph, str], DepGraph]` — returns a new graph (never raises), adds setup-shape Service nodes + advisory Config nodes, no edges.

- [ ] **Step 1: Clean-take the eight consumer/orchestrator source files**

```bash
for f in patch patch_gate certify schedule advise; do
  git show 8d2d7d4:src/python_deps/depgraph/$f.py > src/python_deps/depgraph/$f.py
done
git show 8d2d7d4:src/envstate/graph_scheduler.py    > src/envstate/graph_scheduler.py
git show 8d2d7d4:src/envstate/install_localizer.py  > src/envstate/install_localizer.py
git show 8d2d7d4:scripts/run_v3_e2e.py             > scripts/run_v3_e2e.py
```

- [ ] **Step 2: 3-way merge `ids.py` (auto-merges clean)**

```bash
git show 0e25ee8:src/python_deps/depgraph/ids.py > /tmp/ids.base
git show 8d2d7d4:src/python_deps/depgraph/ids.py > /tmp/ids.theirs
git merge-file src/python_deps/depgraph/ids.py /tmp/ids.base /tmp/ids.theirs   # exit 0 = clean
```
Expected: exit 0, no conflict markers. This silently removes `data_asset_id` (that is intended — its tests are retargeted below).

- [ ] **Step 3: 3-way merge `schema.py` — resolve the ONE enum conflict by keeping BOTH members**

```bash
git show 0e25ee8:src/python_deps/depgraph/schema.py > /tmp/schema.base
git show 8d2d7d4:src/python_deps/depgraph/schema.py > /tmp/schema.theirs
git merge-file --diff3 src/python_deps/depgraph/schema.py /tmp/schema.base /tmp/schema.theirs   # exit 1 = 1 conflict
```
Then edit `src/python_deps/depgraph/schema.py`, replacing the single conflict block in `class DiscoveredBy` with both members (drop all `<<<<<<<`/`|||||||`/`=======`/`>>>>>>>` markers):

```python
    RUNTIME = "runtime"
    # An under-declared root added by the Phase-A repair overlay: discovered by
    # auditing imports against the installed environment, never conflated with a
    # manifest declaration (RESOLVER) or a static-scan import (STATIC_SCAN).
    AUDIT = "audit"
    CLASSIFIER = "classifier"   # classify_services_clean.py admitted node
```
(The `NodeType.DATA_ASSET` + `EDGE_RULES` removals elsewhere in the file auto-merge with no conflict — leave them removed.)

- [ ] **Step 4: 3-way merge `build_script.py` — keep multi-lang's `_PROJECT_HEADER`, take v3-core's DATA_ASSET drop**

```bash
git show 0e25ee8:src/python_deps/depgraph/build_script.py > /tmp/bs.base
git show 8d2d7d4:src/python_deps/depgraph/build_script.py > /tmp/bs.theirs
git merge-file --diff3 src/python_deps/depgraph/build_script.py /tmp/bs.base /tmp/bs.theirs   # exit 1
```
Resolve the single conflict so the region reads (keep the `_PROJECT_HEADER` line from multi-lang, take the DATA_ASSET-free `_NEED_TYPES`):

```python
_PROJECT_HEADER = "# ==================== PROJECT (editable) ===================="

_NEED_TYPES: tuple[NodeType, ...] = (NodeType.CONFIG, NodeType.SERVICE)
```
Then confirm `_NEED_WORD` has no `NodeType.DATA_ASSET` key remaining: `grep -n DATA_ASSET src/python_deps/depgraph/build_script.py` → no output.

- [ ] **Step 5: 3-way merge `populate.py` — docstring only**

```bash
git show 0e25ee8:src/python_deps/depgraph/populate.py > /tmp/pop.base
git show 8d2d7d4:src/python_deps/depgraph/populate.py > /tmp/pop.theirs
git merge-file --diff3 src/python_deps/depgraph/populate.py /tmp/pop.base /tmp/pop.theirs   # exit 1
```
Resolve the single docstring conflict to (keep multi-lang's "populatable"/"non-installable projects" wording, drop the `DataAsset` mention per v3-core):

```python
    """Return a NEW graph in which every populatable node lacking setup_commands
    gets its install command + strength=HARD. Idempotent; leaves Service/Config,
    non-installable projects, and already-populated nodes untouched."""
```

- [ ] **Step 6: Add the clean classifier and delete the legacy one**

```bash
git show 8d2d7d4:src/envstate/classify_services_clean.py > src/envstate/classify_services_clean.py
git rm src/envstate/env_classifier.py
```
Confirm `scripts/run_v3_e2e.py` (clean-taken in Step 1) imports `make_construction_classifier` from `classify_services_clean`, not `env_classifier`:
`grep -n "make_construction_classifier\|classify_services_clean\|env_classifier" scripts/run_v3_e2e.py` → import resolves to `classify_services_clean`, no `env_classifier` reference.

- [ ] **Step 7: Add the five new setup-shape tests**

```bash
git show 8d2d7d4:tests/depgraph/test_certify_setup_service.py  > tests/depgraph/test_certify_setup_service.py
git show 8d2d7d4:tests/depgraph/test_patch_gate_admit_clean.py > tests/depgraph/test_patch_gate_admit_clean.py
git show 8d2d7d4:tests/depgraph/test_schedule_setup.py         > tests/depgraph/test_schedule_setup.py
git show 8d2d7d4:tests/test_classify_services_clean.py         > tests/test_classify_services_clean.py
git show 8d2d7d4:tests/test_graph_scheduler_setup.py           > tests/test_graph_scheduler_setup.py
```

- [ ] **Step 8: Clean-take the twelve modified test files**

```bash
for f in test_advise test_certify test_ids test_nonlib_runtime_state_routing test_patch_gate_apply test_patch_gate_validate test_patch_parse test_schedule test_scheduler_frontier test_schema; do
  git show 8d2d7d4:tests/depgraph/$f.py > tests/depgraph/$f.py
done
git show 8d2d7d4:tests/envstate/test_graph_scheduler.py > tests/envstate/test_graph_scheduler.py
git show 8d2d7d4:tests/test_depgraph_live_certify.py    > tests/test_depgraph_live_certify.py
```

- [ ] **Step 9: Delete the four legacy tests**

```bash
git rm tests/depgraph/test_patch_gate.py tests/depgraph/test_schedule_binding.py tests/test_env_classifier.py tests/test_service_confidence_activation.py
```

- [ ] **Step 10: Run the affected suites + residue gate**

Run:
```bash
PYTHONPATH=src python3 -m pytest tests/depgraph -q -k "not docker"
PYTHONPATH=src python3 -m pytest tests/envstate -q -k "not docker"
PYTHONPATH=src python3 -m pytest tests/test_classify_services_clean.py tests/test_graph_scheduler_setup.py tests/test_provision_verify.py tests/test_service_translate.py tests/test_depgraph_live_certify.py -q -k "not docker"
grep -rn '"service_confidence"\|"start_recipe"\|"bind_recipe"\|data_asset_id\|DATA_ASSET' src/ scripts/ tests/
```
Expected: all three pytest runs PASS; the grep prints NOTHING (empty). If the grep prints a file, that reference was missed — resolve before committing.

- [ ] **Step 11: Commit (atomic)**

```bash
git add -A
git commit -m "feat(depgraph,envstate): flip service tier to clean setup-shape; delete env_classifier + DataAsset tier"
```

---

### Task 4: The Phase-3 seam — `provider.service_obligations`

Formalize the injected-classifier call as a provider phase. TDD the new wrapper.

**Files:**
- Modify: `src/ecosystems/base.py` (add `service_obligations` to the `EcosystemProvider` Protocol)
- Modify: `src/ecosystems/python/provider.py` (add `PythonProvider.service_obligations`)
- Modify: `src/python_deps/depgraph/advise.py:326-327` (route the injected `classify` through the provider)
- Create: `tests/ecosystems/test_service_obligations.py`
- Create: `tests/test_purity.py`

**Interfaces:**
- Consumes: `ecosystems.registry.select_provider`, `ecosystems.registry.PROVIDERS`, `PythonProvider` (existing); the injected `classify_services_clean` callable (Task 3).
- Produces: `PythonProvider.service_obligations(graph, repo, service_classifier=None) -> DepGraph`.

- [ ] **Step 1: Write the failing test for the provider wrapper**

Create `tests/ecosystems/test_service_obligations.py`:

```python
"""Phase-3 seam: provider.service_obligations wraps the injected service classifier."""
from __future__ import annotations

from ecosystems.python.provider import PythonProvider
from ecosystems.registry import PROVIDERS
from python_deps.depgraph.schema import DepGraph


def test_none_classifier_is_passthrough():
    g = DepGraph()
    assert PythonProvider().service_obligations(g, "/repo", None) is g


def test_injected_classifier_runs_and_result_flows():
    g = DepGraph()
    sentinel = DepGraph()
    seen = {}

    def fake_classifier(graph, repo):
        seen["repo"] = repo
        seen["graph"] = graph
        return sentinel

    out = PythonProvider().service_obligations(g, "/repo", fake_classifier)
    assert out is sentinel
    assert seen == {"repo": "/repo", "graph": g}


def test_all_registered_providers_expose_service_obligations():
    for p in PROVIDERS:
        assert callable(getattr(p, "service_obligations", None))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/ecosystems/test_service_obligations.py -q`
Expected: FAIL (`AttributeError: 'PythonProvider' object has no attribute 'service_obligations'`).

- [ ] **Step 3: Add `service_obligations` to the Protocol**

In `src/ecosystems/base.py`, append this method to the `EcosystemProvider` Protocol (after `native_obligations`):

```python
    def service_obligations(
        self,
        graph: DepGraph,
        repo: str,
        service_classifier: object | None = None,
    ) -> DepGraph:
        """PHASE 3 — service tier. Runs the (opaque, ecosystem-supplied) service
        classifier over the converged graph and returns a new graph with setup-shape
        Service nodes. ``service_classifier is None`` => returns ``graph`` unchanged.
        The classifier is an injected ``Callable[[DepGraph, str], DepGraph]`` (envstate
        owns it); providers never import envstate."""
        ...
```

- [ ] **Step 4: Implement `PythonProvider.service_obligations`**

In `src/ecosystems/python/provider.py`, add to `class PythonProvider` (after `native_obligations`):

```python
    def service_obligations(
        self, graph: DepGraph, repo: str, service_classifier: object | None = None
    ) -> DepGraph:
        if service_classifier is None:
            return graph
        return service_classifier(graph, repo)
```

- [ ] **Step 5: Run the wrapper test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/ecosystems/test_service_obligations.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Wire the `advise.py` call site through the provider**

In `src/python_deps/depgraph/advise.py`, replace the injected-classify call (currently around `:326-327`):

```python
        if classify is not None:
            graph = classify(graph, repo_path)        # LLM env classifier (envstate-injected; pure call here)
```

with:

```python
        if classify is not None:
            from ecosystems.registry import PROVIDERS, select_provider   # defensive: mirrors build.py's provider-import style
            provider = select_provider(repo_path, PROVIDERS, default=PROVIDERS[0])
            graph = provider.service_obligations(graph, repo_path, classify)   # Phase 3
```

- [ ] **Step 7: Write the purity test**

Create `tests/test_purity.py`:

```python
"""python_deps and ecosystems must never import envstate (the Slice-C boundary)."""
from __future__ import annotations

import pathlib
import re

import ecosystems
import python_deps

_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:src\.)?envstate\b", re.MULTILINE)


def test_no_envstate_import_in_pure_layers():
    roots = [pathlib.Path(python_deps.__file__).parent, pathlib.Path(ecosystems.__file__).parent]
    offenders = []
    for root in roots:
        for py in root.rglob("*.py"):
            for m in _IMPORT.finditer(py.read_text()):
                offenders.append(f"{py}: {m.group(0).strip()}")
    assert not offenders, offenders
```

- [ ] **Step 8: Run the purity test + the ecosystems suite + advise's suite**

Run:
```bash
PYTHONPATH=src python3 -m pytest tests/test_purity.py -q
PYTHONPATH=src python3 -m pytest tests/ecosystems -q
PYTHONPATH=src python3 -m pytest tests/depgraph/test_advise.py -q -k "not docker"
```
Expected: all PASS. (If purity fails on a ported module, a lazy `envstate` import slipped in — review against the design's purity boundary before proceeding.)

- [ ] **Step 9: Commit**

```bash
git add src/ecosystems/base.py src/ecosystems/python/provider.py src/python_deps/depgraph/advise.py tests/ecosystems/test_service_obligations.py tests/test_purity.py
git commit -m "feat(ecosystems): Phase 3 service_obligations provider method + wire advise.py call site"
```

---

### Task 5: Full scoped verification + finish

Confirm the whole port is green at the scoped gates, then finish the branch.

**Files:** none (verification only).

- [ ] **Step 1: Run the scoped hermetic suites**

Run (each dir separately to avoid conftest collisions):
```bash
PYTHONPATH=src python3 -m pytest tests/depgraph -q -k "not docker"
PYTHONPATH=src python3 -m pytest tests/envstate -q -k "not docker"
PYTHONPATH=src python3 -m pytest tests/ecosystems -q
PYTHONPATH=src python3 -m pytest tests/test_classify_services_clean.py tests/test_service_translate.py tests/test_provision_verify.py tests/test_graph_scheduler_setup.py tests/test_depgraph_live_certify.py tests/test_purity.py -q -k "not docker"
```
Expected: all PASS.

- [ ] **Step 2: Residue + purity final gate**

Run:
```bash
grep -rn '"service_confidence"\|"start_recipe"\|"bind_recipe"\|data_asset_id\|DATA_ASSET' src/ scripts/ tests/
grep -rn 'import envstate\|from envstate\|from src.envstate' src/python_deps/ src/ecosystems/ | grep -v __pycache__
```
Expected: BOTH print nothing (empty).

- [ ] **Step 3: Confirm `build_dep_graph` is untouched**

Run: `git diff 0e25ee8 -- src/python_deps/depgraph/build.py | grep -c '^[+-]'` compared to a manual check that no Phase-3 change landed in `build.py` (the only expected `build.py` deltas are the pre-existing multi-lang ones, none from this port). Expected: no service/Phase-3 lines added to `build.py`.

- [ ] **Step 4: Finish the branch**

Use `superpowers:finishing-a-development-branch`. (Live e2e validation — a real `run_v3_e2e` pass with `OPENROUTER_API_KEY` + `DOCKERAGENT_ENABLE_SERVICE_PROVISION=1` — is a deferred follow-up per the design §7, not a landing gate.)

---

## Notes for the executor

- **Task 3 is the risk-bearing task** and must land as one commit; run its Step 10 gates before committing.
- If any `git show 8d2d7d4:<path>` extraction yields an unexpectedly different file (e.g. an import that does not resolve on multi-lang), STOP and report — the earlier verification found none, but the executor is the last check.
- Do not attempt to run Docker-gated tests or the live path; the user has live work in other worktrees.
