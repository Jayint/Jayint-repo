# Service Detection as Phase 3 in the Multi-Lang Provider Pipeline — Design

**Status:** design (locked: Option A)
**Branch:** `john-v3-multi-lang` (worktree `/Users/john/john-v3-multi-lang`)
**Upstream:** the finished "clean service detection" tier on `v3-core` @ `8d2d7d4` (feature span `62e7bfc..8d2d7d4`, tags `clean-repl-cr1..cr13`).

**Goal:** Port the finished, reviewed, green clean service-detection tier from `v3-core` into
`john-v3-multi-lang`, and expose it as a first-class **Phase 3** (`service_obligations`) in the
`EcosystemProvider` seam — symmetric with Phase 1 (`package_obligations`) and Phase 2
(`native_obligations`) — without perturbing the Phase 1/2 build path.

**Architecture (one line):** the clean classifier stays an envstate-owned, opaque **injected
callable**; the provider seam gains a thin `service_obligations(graph, repo, service_classifier)`
wrapper the orchestrator calls right after `build_dep_graph` returns, where the bare `classify()`
call sits today. `build_dep_graph` and `python_deps`/`ecosystems` purity are untouched.

**Tech Stack:** Python 3, pytest, `python_deps.depgraph` + `ecosystems` + `envstate`. No new deps.

---

## 1. Context & motivation

`john-v3-multi-lang` forked from `v3-core` at `0e25ee8` — **before** the service-detection guard
robustness *and* before the clean replacement that superseded it. Its current service arm is the
old Slice-C LLM `env_classifier` (emits `service_confidence`/`binding` nodes), and its `certify.py`
still gates SERVICE nodes on the stale `data["service_confidence"] == "confirmed"` (`certify.py:80`).

The clean tier is now **code-complete and green on `v3-core`**: construction is deterministic
(known service kinds → LLM-free recipe; only exotic images take one bounded LLM call), and a Service
is certified/scheduled **iff it carries `data["setup"]`**. We bring that tier here and give its
entrypoint a named home in the provider pipeline.

**Key finding that shapes this design:** multi-lang's orchestration *already* has the exact seam
shape. `advise.build_advisory_for_repo` (`advise.py:304`) builds the graph in a scratch container
(`:323`) and then, if a classifier was injected, calls `classify(graph, repo_path)` (`:327`,
commented *"envstate-injected; pure call here"*). So Phase 3 is largely **formalizing an injection
point that exists**, not inventing a new stage.

---

## 2. The clean Service-node model (the contract being ported)

```
Service node (clean, setup-shape):
  id             service:<name>
  type / layer   SERVICE / SERVICES
  state          MISSING  →  certify flips to SATISFIED iff the probe passes
  check_command  render_probe_poll(setup["probe"])     # bounded, read-only liveness poll
  data.setup     { install:[…], start, probe, createdb?, post:[…], bind:[export VAR=…127.0.0.1…] }
  data.service_kind        "postgres" | "redis" | … | <exotic image>
  data.certify_fail_count  N                            # anti-deadlock demote counter
  discovered_by  CLASSIFIER
```

- **Known kinds** (postgres/redis/mysql/mongo/rabbitmq): deterministic, **LLM-free** `render_setup`.
- **Exotic images** (no recipe): one bounded LLM call (`translate_service` → `apply_arch`/`apply_env`
  → static URL `verify_plan` → probe forced through the read-only firewall `normalize_probe`).
  **This is the only construction-time LLM in the tier.**
- **Config**: advisory hint nodes (`check_command=None`, never scheduled). DSN repoint is folded into
  the owning service's `setup["bind"]` by `repoint.render_bind_steps` — no separate bind step, no edges.
- **Anti-deadlock:** a service that cannot provision fails its probe; after `certify_fail_count == 3`
  it is excluded from the "done"-blocking set (`graph_scheduler.next_decision`) — advisory, not fatal.

There is **no** `service_confidence`, `binding`, `start_recipe`, `bind_recipe`, or `DataAsset` tier.
That machinery is deleted (it never existed on multi-lang; here it is superseded).

---

## 3. The Phase-3 seam (Option A — locked)

### 3.1 The provider interface addition

Add one method to the `EcosystemProvider` Protocol (`src/ecosystems/base.py`) and to
`PythonProvider` (`src/ecosystems/python/provider.py`), mirroring the thin-wrapper shape of
`native_obligations`:

```python
# base.py — Protocol
def service_obligations(
    self,
    graph: DepGraph,
    repo: str,
    service_classifier: object | None = None,
) -> DepGraph:
    """PHASE 3 — service tier. Runs the (opaque, ecosystem-supplied) service
    classifier over the converged graph and returns a new graph with setup-shape
    Service nodes. No classifier => returns graph unchanged (byte-identical)."""
    ...
```

```python
# PythonProvider
def service_obligations(self, graph, repo, service_classifier=None):
    if service_classifier is None:
        return graph
    return service_classifier(graph, repo)     # the injected classify_services_clean closure
```

Today `PROVIDERS == (PythonProvider(),)` — no Go/Node/Rust provider objects exist yet — so the
non-Python case is forward-looking: when those providers are added, the new Protocol method obliges
each to implement the **same trivial no-op guard** (no injected classifier → graph unchanged) until
it has its own service scanner. This is the future hook for per-ecosystem service detection.

**Why the classifier is opaque and injected (not imported):** the clean classifier lives in
`envstate` (it wires the LLM `translate_service` call). `python_deps.depgraph` and `ecosystems`
**must not import `envstate`** (`schedule.py`: *"must not import from src.envstate"*; `build.py`
lazily imports `ecosystems` to avoid the build↔provider cycle). Passing the classifier as an opaque
`Callable[[DepGraph, str], DepGraph]` keeps that boundary intact: envstate flows *in* as data, never
as an import.

### 3.2 The orchestrator call site

In `advise.build_advisory_for_repo`, replace the bare call at `:327`:

```python
# before
if classify is not None:
    graph = classify(graph, repo_path)

# after — Phase 3 via the provider seam
if classify is not None:
    from ecosystems.registry import PROVIDERS, select_provider   # defensive: mirrors build.py's provider-import style
    provider = select_provider(repo_path, PROVIDERS, default=PROVIDERS[0])
    graph = provider.service_obligations(graph, repo_path, classify)
```

- `build_dep_graph`'s signature and return type are **untouched** — the eval harness
  (`src/eval/language_package_eval/coverage.py`) and every other direct caller are unaffected.
- The provider is obtained by re-running `select_provider` (cheap, same dispatch used inside
  `build_dep_graph`); we deliberately do **not** change `build_dep_graph` to return the provider
  (that would perturb its return type). The lazy import is defensive/consistency (a module-level
  `ecosystems.registry` import into `advise.py` does not actually cycle today — verified — but the
  function-local form matches `build.py`'s house style and is cheap insurance).
- The injected `classify` is built by the entrypoint (`run_v3_e2e.py`) via
  `make_construction_classifier(client, model, arch)` and already closes over the LLM client, model,
  and `arch` (`{"dpkg","uname"}` from `choose_base_image(...).platform_override`) — so the provider
  interface stays free of LLM/arch specifics.

### 3.3 Certification model (unchanged)

Service provisioning is a **live-container, per-cycle, env-gated** activity, not a scratch-container
one:

- `build_dep_graph` (Phase 1/2 + its `certify_all` tail) runs in a throwaway **scratch** container.
  Service nodes are added by Phase 3 *after* that returns and are left `MISSING`.
- Real service certification happens later, per repair cycle, in the **live** container via
  `depgraph_live.certify_refresh`, gated by `DOCKERAGENT_ENABLE_SERVICE_PROVISION=1`. The ported
  `certify.py` fixes the gate to key on `data["setup"] is not None` (replacing the stale
  `service_confidence == "confirmed"` check) and owns the immutable `certify_fail_count` demote.

**Consequence for risk:** with the flag default-off, the default build path is byte-identical. The
Phase-3 tier cannot regress the Phase 1/2 package/native build that this branch (and the eval
harness, and the multi-language seam work) depends on.

---

## 4. Data flow

```
run_v3_e2e.py
  ├─ choice = choose_base_image(repo)               → base_image, platform_override
  ├─ arch   = _target_arch(platform_override)        → {"dpkg","uname"}
  ├─ classify = make_construction_classifier(client, model, arch)   # envstate, closes over LLM+arch
  └─ build_advisory_for_repo(repo, base_image, classify=classify)
        ├─ graph = build_dep_graph(repo, scratch)     # PHASE 1 (package) + PHASE 2 (native) + certify tail
        └─ provider = select_provider(repo, PROVIDERS, default=PythonProvider)
           graph = provider.service_obligations(graph, repo, classify)     # PHASE 3
                     └─ classify_services_clean(graph, repo, client, model, arch)
                          ├─ collect_static_evidence(graph)          # harvest evidence_ids
                          ├─ iter_provisioning_spec(compose/env off disk) → ProvisioningSpec[]
                          ├─ per spec: translate_service(...)         # known → render_setup (pure)
                          │                                           # exotic → LLM + verify + normalize_probe
                          └─ admit_proposal(...)                      # setup-shape Service + advisory Config nodes
  (later, live container, env-gated) depgraph_live.certify_refresh → probe → SATISFIED / demote
```

`classify_services_clean` returns a **new immutable `DepGraph`** (or the input unchanged on any
error — it never raises). It adds nodes only; no edges.

---

## 5. The port (mechanical) — hybrid, not cherry-pick

Cherry-picking the 21-commit span is rejected: it assumes `62e7bfc` as base, which carries
v3-core-only guard-era history this branch never forked from. Instead, a file-level hybrid keyed on
divergence from the fork base (`0e25ee8`):

| Strategy | Files | Rationale |
|---|---|---|
| **Copy wholesale from `8d2d7d4`** (7) | `provisioning_spec.py`, `translate_sanitize.py`, `repoint.py`, `service_recipes.py`, `envstate/provision_verify.py`, `envstate/service_translate.py`, `envstate/classify_services_clean.py` | New here (incl. `service_recipes.py`, absent on multi-lang) — no conflict |
| **Clean-take `8d2d7d4` version** (8) | `patch.py`, `patch_gate.py`, `certify.py`, `schedule.py`, `advise.py`, `envstate/graph_scheduler.py`, `envstate/install_localizer.py`, `scripts/run_v3_e2e.py` | multi-lang Δ=0 since fork → take v3-core's version verbatim |
| **3-way merge (trivial)** (4) | `schema.py` (keep both `AUDIT`+`CLASSIFIER` enum members; DATA_ASSET removal merges clean), `ids.py` (auto-merges), `build_script.py`, `populate.py` (proximity/docstring only) | both branches touched; no functional collision |
| **Atomic delete** (1) | `envstate/env_classifier.py` **+** `tests/test_env_classifier.py` | only live caller is `run_v3_e2e.py` (clean-taken, already rewired); delete with the rewire in one commit |

**No missing dependencies.** Every symbol the clean core imports already exists on multi-lang
(`service_tables.KNOWN_SERVICE_KINDS`, `service_scan.{service_from_url,classify_service_error}`,
`static_collect`, `config_scan`, `req_slice`, `emit`, `block`, `action_class`,
`base_image_selection.platform_override`, `build.build_dep_graph`, `executor`,
`depgraph_live.certify_refresh`).

**Note on `advise.py` / `run_v3_e2e.py`:** these are clean-taken as the base, then receive the small
Phase-3 edits from §3.2 (the provider re-select + `service_obligations` call, and confirming
`run_v3_e2e` builds the clean classifier with `(client, model, arch)`). The `v3_build_agent.py`
call site (if present on this branch) is verified during planning — on `v3-core` only
`run_v3_e2e.py` imports the clean entrypoint at HEAD.

**Tests: mirror v3-core@`8d2d7d4`'s test tree for every test file the span touched** — not a
hand-picked subset. Concretely:
- **Add** the new clean-shape tests: `test_provisioning_spec`, `test_translate_sanitize`,
  `test_repoint`, `test_provision_verify`, `test_service_translate`, `test_service_recipes_clean`,
  `test_patch_gate_admit_clean`, `test_certify_setup_service`, `test_schedule_setup`,
  `test_classify_services_clean`, `test_graph_scheduler_setup`.
- **Take v3-core's `8d2d7d4` version** of every test file it *modified* — including
  `test_ids.py`, `test_schema.py`, `test_scheduler_frontier.py`, `test_advise.py`, `test_certify.py`,
  `test_patch_parse.py`, `test_patch_gate_apply.py`, `test_patch_gate_validate.py`, `test_schedule.py`.
  This is what removes the `DATA_ASSET`/`data_asset_id` references that the `ids.py`/`schema.py`
  merges strand (v3-core already fixed these). **This is a real failure the port cannot skip**: the
  `ids.py` 3-way auto-merges *silently* (deleting `data_asset_id` with no conflict marker), so
  `tests/depgraph/test_ids.py` — and the `NodeType.DATA_ASSET` cases in `test_schema.py` /
  `test_scheduler_frontier.py` — would `ImportError`/fail unless their v3-core versions are taken.
- **Delete** the legacy tests v3-core deleted: `test_env_classifier.py`,
  `test_service_confidence_activation.py`, `test_schedule_binding.py`, and `test_patch_gate.py`
  (**the whole file** — it is 100% DataAsset-scoped, not "some cases").
- Any test file **both** branches changed gets a 3-way merge (the plan does per-test-file
  divergence analysis, same method as the source files).
- **Add a new** `test_service_obligations` for the Phase-3 wrapper + `advise.py` call-site wiring
  (multi-lang-specific, no v3-core analog).

---

## 6. Error handling

- **Advisory-level:** `build_advisory_for_repo` already returns `("", None)` on any exception —
  Phase 3 inherits this graceful degradation; a failing service classifier never breaks a run.
- **Classifier-level:** `classify_services_clean` never raises — on error/rejection it returns the
  input graph unchanged (best-effort).
- **`arch` must be non-`None` when exotic services are possible:** the exotic path's `apply_arch`
  does `arch["dpkg"]`, so `arch=None` raises `KeyError` — swallowed by the classifier's try/except,
  which then silently drops *all* service/config nodes for that repo. Harmless in the wired path
  (`run_v3_e2e.py` always passes `arch` from `choose_base_image(...).platform_override`), but the
  Phase-3 wiring must never inject a classifier built with `arch=None`; the plan asserts this.
- **Probe firewall:** `normalize_probe` rewrites any `curl`/`wget` probe to a read-only
  `nc -z 127.0.0.1 <port>` (or the per-kind `pg_isready`/`redis-cli ping`/…), and a single
  un-normalizable probe drops only its own service — never poisons the batch.
- **Anti-deadlock:** `certify_fail_count == 3` demotes an un-provisionable service to advisory so
  "done" stays reachable.

---

## 7. Testing & verification

Scoped, hermetic gates (run these BEFORE any live run; no broad/Docker sweeps):

```bash
PYTHONPATH=src python3 -m pytest tests/depgraph -q -k "not docker"
PYTHONPATH=src python3 -m pytest tests/envstate -q -k "not docker"
PYTHONPATH=src python3 -m pytest tests/test_classify_services_clean.py tests/test_service_obligations.py -q
# residue must be EMPTY — no legacy machinery survived the port.
# NOTE: scope includes tests/ — the ids.py/schema.py merges strand DATA_ASSET refs in tests, and a
# src/-only grep would falsely report clean while pytest fails.
grep -rn '"service_confidence"\|"start_recipe"\|"bind_recipe"\|data_asset_id\|DATA_ASSET' src/ scripts/ tests/
```

- **Phase-3 wiring test** (new): assert `build_advisory_for_repo` routes through
  `provider.service_obligations`, that a `None` classifier is a pass-through (graph unchanged), and
  that an injected classifier's setup-shape nodes reach the graph.
- **Purity test** (new or extended): assert no `import envstate` under `src/python_deps/` or
  `src/ecosystems/`.
- **Live validation (deferred):** a real `run_v3_e2e` pass with `OPENROUTER_API_KEY` +
  `DOCKERAGENT_ENABLE_SERVICE_PROVISION=1` on one service repo — closes the inherited "not yet
  live-validated" gap. Not a gate for landing the port (flag default-off).

---

## 8. Risks & caveats

1. **Not yet live-validated end-to-end** (inherited from v3-core — no real Docker+LLM run of the
   flipped path). Mitigated: flag default-off; hermetic suites gate the port; live run is a
   follow-up, not a blocker.
2. **`env_classifier.py` delete must be atomic** with the `run_v3_e2e.py` rewire and the
   `test_env_classifier.py` deletion, or pytest collection breaks. Handled by sequencing in the plan.
3. **`build_dep_graph` untouched** is a hard invariant — the eval harness consumes its return; the
   Phase-3 call happens strictly outside it.

---

## 9. Out of scope / deferred

- Go/Node service scanners (Phase 3 no-ops for them until they exist).
- The flip-gate eval E7 (`stage_e2e`) and the medlarge15 live benchmark — not on this branch.
- The eval harness (`evals/service_config_detection/stage_*`) — validation-only; ported separately
  if/when the service eval is wanted here.
- Runtime-arm minimality pruning (which declared services are truly dialed) — unchanged, separate
  subsystem (`runtime_classify.py`).

---

## 10. Implementation increments (outline; detailed in the plan)

**Sequencing constraint (differs from the v3-core plan):** v3-core landed this via a gradual
dual-shape coexistence (CR1–CR10 additive, CR11 atomic flip). We cannot replay that here because we
are clean-taking v3-core's **post-flip, setup-only** consumer files (`patch`/`patch_gate`/`certify`/
`schedule`/`advise` at `8d2d7d4` already have the legacy deleted). So on multi-lang the consumer swap
cannot straddle both shapes — the setup-only consumers, the old `env_classifier` deletion, and the
legacy-test deletion must land **together as one atomic swap**. This is safe: the service arm is
env-gated (`DOCKERAGENT_ENABLE_SERVICE_PROVISION` default-off) and peripheral to the Phase 1/2 build,
so the brief swap cannot regress the core pipeline.

1. **Port the pure clean core** (copy `provisioning_spec`, `translate_sanitize`, `repoint`,
   `service_recipes` extensions + their tests) — additive, nothing imports them yet, tree stays green.
2. **Port the envstate clean core** (`provision_verify`, `service_translate`, `classify_services_clean`
   incl. `make_construction_classifier`) + their tests — additive, still nothing wired.
3. **Atomic swap: setup-only consumers + entrypoint rewire + delete legacy** (one landing — the
   `env_classifier.py` delete and the `run_v3_e2e.py` import-rewire MUST be together, else
   `run_v3_e2e.py:68 from …env_classifier import make_construction_classifier` breaks). In this
   increment: clean-take `patch`/`patch_gate`/`certify`/`schedule`/`advise`/`graph_scheduler`/
   `install_localizer`/`run_v3_e2e.py` (its import now resolves to `classify_services_clean`, and it
   builds the classifier with `(client, model, arch)`, `arch` from `platform_override`); 3-way
   `schema`/`ids`/`build_script`/`populate`. **Tests must land in the SAME commit** (the `ids.py`
   merge silently strands `DATA_ASSET`/`data_asset_id` refs): take v3-core's `8d2d7d4` versions of the
   modified test files (`test_ids`, `test_schema`, `test_scheduler_frontier`, `test_advise`,
   `test_certify`, `test_patch_parse`, `test_patch_gate_apply`, `test_patch_gate_validate`,
   `test_schedule`), delete the legacy tests (`test_env_classifier`, `test_service_confidence_activation`,
   `test_schedule_binding`, and the whole `test_patch_gate.py`), and add the new setup-shape tests. Delete
   `env_classifier.py` (atomic with the `run_v3_e2e.py` rewire). Verify any `v3_build_agent.py` call
   site. After this commit only the setup shape exists (the injected classifier flows through the
   *bare* `advise.py` call until step 4); the §7 residue-grep (now including `tests/`) is clean and
   `pytest tests/depgraph -q -k "not docker"` passes.
4. **The Phase-3 seam** — add `service_obligations` to the Protocol + all providers (Python delegates
   to the injected classifier; Go/Node no-op); route the `advise.py` call site through it (§3.2); add
   `test_service_obligations` + the purity test.
5. **Verify** — scoped hermetic suites green (`tests/depgraph`, `tests/envstate`, the new tests);
   purity + residue checks; then `superpowers:finishing-a-development-branch`. Live e2e is a deferred
   follow-up (§7), not a landing gate.
