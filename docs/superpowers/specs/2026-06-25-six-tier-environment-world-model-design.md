# Six-Tier Certified Environment World Model

**Date:** 2026-06-25
**Status:** Design (awaiting review)
**Branch:** john-planner-v3
**Extends:** `docs/DESIGN-static-probe-certified-dependency-graph.md` (the typed, host-certified node/edge model) and the advisory channel in `docs/superpowers/specs/2026-06-24-unified-depgraph-emit-escalate-design.md`. This document widens *what the graph models* from installable artifacts to the whole repository environment, organized as six tiers.

---

## 1. Problem — the graph models 2 of N environment layers

The certified dependency graph (`src/python_deps/depgraph/`) is the richest representation in the system, but it only models the *installable* environment: Python packages (`uv`), system shared libraries (`ldd`), the build toolchain, and the interpreter. Real repositories fail to reach a green test suite for reasons that live **outside** that slice:

- an **env var** the code reads at import time is unset (`SECRET_KEY = os.environ['SECRET_KEY']`), so collection itself fails;
- a **service** the suite connects to (Postgres, Redis) is not running, which surfaces as a *test* failure, not a *dependency* failure;
- a **data file / model weight / fixture** the tests need is absent;
- the **OS base image / arch / accelerator** is wrong, so a wheel or apt package is unavailable.

These are environment-construction failures the agent hits constantly (`DJANGO_SETTINGS_MODULE`, the collect-only done-gate masking exec failures, `os.environ` `KeyError`s), yet the graph has no node for any of them. The agent therefore reacts to raw tracebacks for everything above tier 4.

**Consequence for the research claim.** Our novelty argument (vs `uv`, SMT-LLM, ProgramBench) rests on *"the certified graph is the agent's evidence base, not a transient solve substrate."* A representation that covers only packages and `.so` files is an arbitrary cut — it is a relabeled dependency resolver. Promoting the *whole* environment to certified tiers is what lets the work make the precise claim framed in §2 (a tier-agnostic certified belief state) rather than a reframing — see §11 for what is and isn't novel.

---

## 2. Thesis

Generalize the package-and-syslib dependency graph into a certified, **tier-agnostic** environment representation — a structure an LLM agent reasons over — and demonstrate it across **six tiers** (Platform / System / Runtime / Packages / Services / Config&Data). The graph keeps its exact structure — typed nodes, `requires` edges, a 3-valued host-certified `state` flipped only by a `check_command`, and immutability — and widens *what* it represents to the full substrate-to-surface environment stack.

**The contribution is the method, not the tier count.** The certification engine is tier-agnostic: any environment layer expressible as `{check_command, fix_candidates, state}` can be added without modifying the engine. The six tiers are the *demonstration domain*, not the claim — a reviewer should read "we show the same certification engine extends from the installable layer to services, env vars, and the OS substrate," not "we discovered six tiers" (which is a standard OCI / twelve-factor stack and is not itself novel; see §11).

**Epistemic spine — necessary vs. sufficient (lead with this).** Each node's `check_command` certifies a *necessary* condition for the suite to pass (`libGL.so.1` present, `DJANGO_SETTINGS_MODULE` set, `postgres:5432` reachable). **No node, and no conjunction of nodes, is *sufficient*** — only a green test run certifies sufficiency. The graph is therefore a **certified necessary-condition monitor** (a "certified belief state" over the environment), not a correctness oracle. This is why presence-not-value (§5) is a *strength*, not a hole: the graph proves which necessary conditions already hold to shrink the agent's search space; the test suite is the sole sufficiency oracle. ("World model" is used informally throughout as the accessible handle for this certified belief state; we do not claim the POMDP/belief-update machinery the term can imply.)

A missing env var, an unreachable Redis, and an absent weights file are all ordinary `state=MISSING` nodes — the same machinery as a missing `.so`, with the certification-mechanics caveats in §5.

---

## 3. The six-tier model

### 3.1 Goal nodes vs. resource nodes

Every node is a **certified predicate** joined by `requires` edges (logical dependency). The distinction we draw is by *position/role*, not a hard layer boundary (it is leaky to call intermediate nodes pure "demand" or "provider" — a `Package` is both a need on `System` and a provider to `Import`):

- **Goal nodes** — `Test → Project → Import`: the objective, with no satisfying `fix_candidate`. They are **not** tiers.
- **Resource nodes** — the six provider tiers below, each carrying `fix_candidates`.

The six tiers are the resource stack the goal consumes:

```
            Test ── Project ── Import          ← demand (the goal)
                       │ requires
   ┌───────────────────▼─────────────────────┐
 6 │ Config & Data   env vars, settings, fixtures, weights
 5 │ Services        Postgres, Redis, ports
 4 │ Packages        pip closure (uv)              ← current Package
 3 │ Runtime         interpreter                   ← current Runtime
 2 │ System          .so libs + toolchain          ← current SystemLib + Tool
 1 │ Platform        OS image, arch, GPU           ← partly implicit today
   └──────────────────────────────────────────────┘
```

### 3.2 Tier definitions and certification

| # | Tier | Node types | a MISSING node's `check_command` | `fix_candidate` |
|---|------|-----------|----------------------------------|-----------------|
| 1 | **Platform** | `PLATFORM` | `cat /etc/os-release`, `uname -m`, `nvidia-smi` | `image:python:3.11-bookworm`, `--platform` |
| 2 | **System** | `SystemLib`, `Tool` *(existing)* | `ldconfig -p`, `dpkg -s` | `apt:<pkg>` |
| 3 | **Runtime** | `Runtime` *(existing)* | `python --version` | base image / pyenv |
| 4 | **Packages** | `Package` *(existing)* | `python -m pip show <pkg>` | `pip:<pkg>` |
| 5 | **Services** | `SERVICE` | `nc -z <host> <port>` / healthcheck | `service:<image>` / start cmd |
| 6 | **Config & Data** | `CONFIG`, `DATA_ASSET` | `printenv <VAR>` / `test -f <path>` | `env:<VAR>=<val>` / `fetch:<url>` / `generate:` |

The lower four tiers form a strict **containment** stack — each is literally built on the one beneath. Tiers 5–6 are different: they are a **runtime-wiring plane** that attaches *sideways* to whatever in the stack demands them (see §7). They also lean more on **failure-driven discovery** (§6).

> **Certification-mode caveat (Services).** Tier 5's `nc -z host port` requires the service to be *running and reachable* during certification — which the current single-container executor (`docker exec` into one `sleep infinity` scratch container) cannot provide (no compose, no network, no sidecar). Tier 5 therefore needs a **separate certification mode** (compose-up → healthcheck → teardown), not the single-`check_command` engine. It is **out of scope for the first slice** and called out as such in §10/§12; the "same engine certifies every tier" claim holds for tiers 1–4 and (with the §5 rebuild caveat) tier 6, **not** tier 5.

---

## 4. Schema changes (hybrid: nodes + tier ordering)

Per the chosen mechanics — new certified node types **plus** an explicit tier label, reusing `requires` edges for ordering (they already cross tiers, e.g. `Package → SystemLib`).

In `src/python_deps/depgraph/schema.py`:

1. **New `NodeType` members:** `PLATFORM = "Platform"`, `SERVICE = "Service"`, `CONFIG = "Config"`, `DATA_ASSET = "DataAsset"`. Existing members unchanged.
2. **New `tier` attribute** on `Node` — an `int` 1–6 (default-safe; defaults derived from `type` so existing construction is unaffected). A `Tier` enum/`TYPE_TO_TIER` map provides the fixed mapping for existing types (`Runtime→3`, `Package→4`, `SystemLib/Tool→2`).
3. **`EDGE_RULES` update** — add `Config`, `Service`, `DataAsset`, `Platform` to the `requires` **destination** set (and `Project`/`Test` remain valid sources). `conflicts_with` unchanged.
4. **`to_dict`** gains `tier` for the visualization/advisory layers.
5. **New `Layer` member `CONFIG`** (and `SERVICES` when tier 5 lands) + `_LAYER_ORDER` extension in `certify.py`. **This is required, not optional:** `certify_all` iterates by `Layer`, *not* by `tier`, so a CONFIG node with no `Layer` mapping would have undefined certification order. `CONFIG` certifies *after* `PIP` (a package-induced config like `DJANGO_SETTINGS_MODULE` may need its inducing package importable to validate).

No change to the certification *logic*, immutability rules, or edge dedup — but the `Layer`/`_LAYER_ORDER` extension above is a small, necessary certify-ordering change (the §5 "no new certification code" wording is corrected accordingly). This is an additive, default-safe schema widening.

---

## 5. Certification generalizes — with three corrections from adversarial review

`certify_all` runs each node's `check_command` in the target container and flips `state`. Because every new tier's node is `{state, check_command, fix_candidates}`, **no new certification *logic*** is required — the new node types are certified by the same pass. But the original "no new certification code / generalizes unchanged" claim was too strong. Three concrete corrections:

1. **`Layer` ordering (required).** `certify_all` iterates by `Layer`, not `tier`; CONFIG needs a `Layer.CONFIG` member + `_LAYER_ORDER` entry (§4.5) or its certification order is undefined.
2. **Rebuild-and-recertify for ENV (required for tier 6).** The executor is one `sleep infinity` scratch container. `printenv X` at certify time sees only ENV *already baked into the base image* — **not** the `ENV` lines `build.py` is emitting. So a CONFIG fix (`bake ENV KEY=value`) cannot be re-certified by a re-exec in the same container; it needs a second `docker build` + fresh executor. The pipeline must gain a **post-emit rebuild-and-recertify** step (today `build_dep_graph` returns after one pass).
3. **Services are a different mode (out of scope here).** `nc -z` needs the service running with shared networking — not the single-container engine (see §3.2 caveat). Deferred to a later phase.

The fix application (bake `ENV`, fetch a file) reuses the existing Dockerfile-emission / env-bake helpers; re-certification (per #2) then flips `missing → satisfied`.

**Presence ≠ value-correctness, and the advisory must not hide it.** For Config, certification proves a variable is *present* (`printenv` non-empty) — not that its *value* is correct. A `DATABASE_URL` pointing nowhere passes presence yet fails the tests; a node that flipped `satisfied` on an **agent-guessed** value would render `T6 ✓` while the suite is red, actively misleading the planner. Therefore an agent-guessed-value node is marked **`PRESENT_UNVERIFIED`** (or the tier-6 advisory block is annotated `values unverified`) — distinct from a `satisfied` node whose value came from a real source (`.env.example`, CI `env:`). Concretely:

- the env-var tier **certifies presence** (deterministic, host-checked), and
- **value-correctness is certified transitively** — a value only proves itself when the suite goes green (the necessary-vs-sufficient spine, §2).

This keeps the graph deterministic and certified (presence is a clean host check) and pushes the guessy part (values, especially secrets) onto the agent, where behavioral test-pass is the real oracle: **graph surfaces the certified necessary condition, agent proposes the value, tests certify sufficiency.**

---

## 6. Discovery — two modes for every tier

- **Static seed** — parse repo files, no execution; high precision, runs up front.
- **Failure-driven completion** — a test-time error surfaces a node the static pass missed, via an `error_handler`-style classifier (regex tiers, à la SMT-LLM) that maps a failure signature → a certified-missing node. This is the "failure → need → provider → attempt" overlay applied beyond packages.

Tiers 1–4 are dominated by static discovery (already built). Tiers 5–6 genuinely need both, because services and config are frequently only discoverable when a test connects/reads at run time.

---

## 7. Config tier (the first vertical slice)

### 7.1 What is modeled — names and presence, not values

A Config node's identity is the **variable name** the repo reads; its certified axis is **presence**. Values are **not** a discovery target — they enter only at the *fix* stage, trust-tiered (§7.4). See the presence-vs-value invariant in §5.

### 7.2 Static discovery sources (trust order)

| Source | Yields |
|---|---|
| **AST scan for `os.environ[X]` / `os.getenv(X)` / `os.environ.get(X)`** | the env vars the code *actually reads* (keystone — the source states the need) |
| **AST scan for framework config-readers** — `pydantic-settings` (`class S(BaseSettings): x: str`), `python-decouple` (`config('X')`), `environs` (`env.str('X')`), `starlette.config.Config`, `flask.Config.from_envvar` | **the dominant modern pattern** in Django/FastAPI repos; a bare-`os.environ`-only scan misses these and yields a thin (low-value) Config layer exactly where it matters most. Field declarations are structured → the var name is parseable directly |
| `.env.example` / `.env.sample` / `.env.template` | declared var names, often with example values |
| pytest config (`pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`, `pytest-env`) | test-time env + config files that must exist to collect |
| `docker-compose.yml` `environment:`, `.github/workflows/*.yml` `env:` | values maintainers actually set |
| Django `DJANGO_SETTINGS_MODULE` references / settings module | the import-time settings need (the collect-only killer) |
| curated **`package → config-obligation`** table | package-induced config (§7.3): `django→DJANGO_SETTINGS_MODULE`, `celery→<broker>`, `boto3→AWS_*` |

**Pre-certification filter (false-positive guard).** Like `seed_predicted_native`, the curated `package → config` table emits *unconditionally* — so `boto3` would emit `AWS_*` even when the suite mocks all AWS, and `django` would emit `DJANGO_SETTINGS_MODULE` even when `pytest.ini` already sets it. Before a CONFIG node reaches the advisory, suppress it if the var is already present in `.env` / `pytest.ini [env]` / the base image (a cheap `printenv` probe). This keeps the advisory free of false-missing noise that would otherwise dilute the A/B signal.

### 7.3 How Config connects to the rest of the graph

A config var never floats free — it anchors to something. Three conceptual connections:

1. **Induced by a package (or the project).** A config need is evidence that some installed thing reads it.
   - **Project-induced** — the repo's *own* code reads it (`os.environ['SECRET_KEY']` in `settings.py`) → anchors to the **Project**.
   - **Package-induced** — a third-party package reads it (`django` ⇒ `DJANGO_SETTINGS_MODULE`) → anchors to the **Package**.
   This is the same shape as the native frontier: just as `psycopg2` (Package) induces `libpq` (SystemLib) via `seed_predicted_native`, `django` (Package) induces `DJANGO_SETTINGS_MODULE` (Config) via a curated `package → config` table — the direct analogue of the existing `package → system-dep` table.
2. **Binds to a Service.** A config var is usually how the app addresses a service (`DATABASE_URL` → the Postgres at tier 5). Config is the wire; the Service is the endpoint.
3. **Parameterizes the lower tiers** (conceptual; *not built in slice 1* — YAGNI). The config *value* can decide whether a lower tier is required: `DATABASE_URL=sqlite://` ⇒ no Postgres service needed; `postgres://…` ⇒ tier-5 Postgres required. Config is partly a control plane over what's required beneath it. Recorded here for the model; deferred until Services land.

**Mental model:** Config hangs off the package/project that reads it, and points at the service it addresses — the chain `django → DJANGO_SETTINGS_MODULE → SECRET_KEY/DATABASE_URL → Postgres` is exactly the cross-tier reasoning a flat package resolver never sees.

### 7.4 Node shape, fix, plumbing

```
type=CONFIG, tier=6, name="DJANGO_SETTINGS_MODULE",
state=UNKNOWN, check_command="printenv DJANGO_SETTINGS_MODULE",
fix_candidates=("env:DJANGO_SETTINGS_MODULE=myproj.settings",)   # value derived from repo
evidence="manage.py:7  os.environ.setdefault('DJANGO_SETTINGS_MODULE', ...)"
discovered_by=STATIC_SCAN
```

When a value **cannot** be derived (`SECRET_KEY` with no example), the node carries a **placeholder fix** — `env:SECRET_KEY=?` — surfacing the *need* but not inventing the value. This mirrors today's "unknown soname → empty `fix_candidates`" honesty: the graph states the need; the **agent proposes the value** (`SECRET_KEY=test`, `DATABASE_URL=sqlite://`). That hand-off is the world-model-feeds-planner story on a non-package tier.

- **Edge:** `Project → CONFIG` (project-induced) or `Package → CONFIG` (package-induced) via a normal `requires` edge.
- **Plumbing:** new static stage `scan_config(repo_path, graph)` in `build.py`, after `scan_to_nodes` and after the resolver (so package-induced lookups can see the closure). CONFIG nodes are certified in-container by the existing `certify_all` (their `printenv` is just another `check_command`). The fix bakes `ENV KEY=value` into the Dockerfile via the existing env-bake helper, then re-certify flips the node `satisfied`.

### 7.5 Slice phasing

1. **Static** env-var discovery (project- and package-induced) + certification + tiered advisory render. **Phase 1 alone proves the model end-to-end.**
2. **Failure-driven** completion: classify `KeyError` / `ImproperlyConfigured` / pydantic `ValidationError` → CONFIG node.

---

## 8. Agent utilization (the research bet)

The certified graph renders into a **tier-organized advisory** the planner reads before acting (reusing `advise.py`, the `dep_advisory` carrier, and the planner-prompt splice). Bottom-up, with root-cause chains:

```
ENV STATE (certified)
  T4 Packages   ✓ django 4.2, ✓ psycopg2 2.9 …
  T5 Services   ✗ postgres:5432  (DATABASE_URL points here)
  T6 Config     ✗ DJANGO_SETTINGS_MODULE   fix: env:…=myproj.settings   [manage.py:7]
                ✗ SECRET_KEY               fix: env:SECRET_KEY=?  (value needed)
WHY test collection fails: T6 DJANGO_SETTINGS_MODULE unset → django (T4) cannot import settings
```

Instead of reacting to a raw traceback, the agent gets a structured, certified, cross-tier picture — the certified-belief-state claim made concrete on tiers `uv` and SMT-LLM never represent.

**We confront a prior negative result, by name.** `docs/superpowers/specs/2026-06-24-unified-depgraph-emit-escalate-design.md` found the *package-tier* advisory "sat inert" while an LLM repair loop did the work, and concluded it was an *"authority problem, not an information problem — more formatting cannot fix it,"* pivoting ADVISE → EMIT. This design **deliberately keeps the advisory channel** rather than pivoting to emit — not because that conclusion is wrong, but because the prior advisory was **thin and topology-destroying** (MISSING frontier + per-layer counts), whereas this one is **causal and cross-tier** (the `WHY` chain above). The open question is therefore sharp and honest: *does a richer, causal, cross-tier advisory earn the authority a thin one did not?* The architecture's central claim is **currently unproven**; the package-tier A/B was inconclusive, and this design does not assume the answer — it makes the experiment able to detect it.

**Validation — measurement designed so a null result is informative** (not a repeat of the inconclusive Task-6 run):

1. **Frozen-repair-loop control arm.** Run an arm with the within-step LLM repair loop *disabled* (planner sees raw tracebacks only). The repair loop is the confounder that equalizes arms by reaching the fix anyway; freezing it isolates the advisory's marginal value.
2. **Config-failure stratum.** Score separately on a held-out set of repos that fail *specifically* on missing env/config (collect-time import failures). The marginal value of the cross-tier advisory should be largest here; a null *here* is genuinely informative.
3. **Cycles-to-fix, not just pass/fail.** Log the cycle index at which the first correct fix is applied. Advisory value shows as navigation speed even when final pass-rates converge.
4. **Honest success** (`ebsr AND pass_rate ≥ 0.8`) as the headline metric — never collect-only or self-reported.
5. *(Optional third arm)* an **emit** arm (derivable fixes baked deterministically pre-planner) would separate the information-problem from the authority-problem outright; noted as future work since this design keeps the advisory channel.

The **falsifiable claim** the work stands on: *an agent given the certified cross-tier advisory reaches green in fewer cycles and at higher honest-success than the same agent on raw logs, on repos that fail across ≥3 tiers.* If the frozen-control arm shows no lift, the prior "authority problem" verdict generalizes and the emit pivot becomes the indicated next step.

---

## 9. Testing strategy (TDD)

- **Unit (pure, no Docker):** config discovery parsers — AST `os.environ`/`getenv` scan, `.env.example` parse, pytest-config parse, `package → config` table lookup. Red→green each.
- **Integration (Docker):** CONFIG node certification (`printenv` in-container), env-bake fix → re-certify flips `missing → satisfied`.
- **E2E:** a real Django repo whose settings read `SECRET_KEY`/`DATABASE_URL` — graph surfaces the config needs, agent fills values, tests collect and pass.
- **Off-state invariant:** with the feature flag off, output is byte-identical to today (graceful, default-off, like the depgraph advisory).

---

## 10. Phase plan

| Phase | Scope |
|---|---|
| **0 — Schema generalization** | `PLATFORM/SERVICE/CONFIG/DATA_ASSET` NodeTypes, `tier` attribute + `TYPE_TO_TIER`, `EDGE_RULES` dst update, `to_dict` tier. No behavior change. |
| **1 — Config slice (static)** | `scan_config` + project/package-induced discovery + certification + tiered advisory render. The proving slice. |
| **2 — Config failure-driven** | Error classifier: `KeyError`/`ImproperlyConfigured`/pydantic `ValidationError` → CONFIG node. |
| **3+ — Remaining tiers** | Services, Platform, Data — one slice each, same template (node type + discovery + check/fix + advisory). |
| **Validation** | The §8 measurement: frozen-repair-loop control arm + Config-failure stratum + cycles-to-fix + honest-success headline (designed so a null result is informative). |

---

## 11. Novelty / prior-art subsumption

**Scope the claim honestly (adversarial review).** What is *not* novel: the per-tier `check_command` primitives (healthchecks, Nix `buildInputs`, `dpkg -s`, twelve-factor config), the six-tier taxonomy itself (a standard OCI/twelve-factor stack), and structured-context injection into an LLM. SMT-LLM already does cross-layer dep modeling + failure-driven constraint generation + curated apt tables — for packages and syslibs. **What survives as the defensible contribution**, stated narrowly: *execution-certified cross-tier **causal chains** — from a failure signature to the root-missing node — applied **beyond** the package/syslib layer (services, env vars, OS substrate), under a host-certified trust boundary (the LLM reads only host-certified state; never writes it), serving as the planner's persistent evidence base rather than a solver's discarded substrate.* The contribution is the **tier-agnostic certification method + the cross-tier causal diagnosis**, not the tier count. This claim is contingent on the §8 A/B showing lift; absent that, it is an engineering contribution, not a finding.

- **vs `uv`** — `uv` solves version SAT inside the PyPI layer and is blind to tiers 1, 5, 6 and the system frontier. We do not re-solve versions; we *certify* across all six tiers and structure them for an agent. `uv` becomes one certified oracle (tier 4) inside the model.
- **vs SMT-LLM** — its constraint graph is internal to a Z3 solver and discarded; cross-layer is a curated apt-build-dep guess table verified by coarse Docker pass/fail. Ours is per-node, execution-certified state across *all* env layers, persisted as the agent's interface.
- **vs ProgramBench** — shares the behavioral/honest-success oracle and build-script-as-deliverable, but has no environment representation; the six-tier certified model is exactly the structured world model its construction agent (and RATBench-style env setup) would consume.

---

## 12. Non-goals (YAGNI)

- No config *value*-correctness modeling beyond presence (the test suite is the value oracle; agent-guessed values are `PRESENT_UNVERIFIED`, §5).
- No control-plane "config parameterizes lower tiers" logic in slice 1 (§7.3.3) — deferred until Services exist.
- No new edge type — `requires` + the `tier` attribute carry ordering.
- No change to the certification *logic*, resolver, or immutability rules — but a `Layer.CONFIG` member + a post-emit rebuild-and-recertify step **are** in scope (§4.5, §5); the original "no change to the certification engine" claim was corrected by review.
- **Tier 5 (Services) certification is out of scope** for slices 1–2 (needs compose-up/healthcheck/teardown orchestration, not the single-container engine, §3.2). The Service *node type* and static discovery may land; live certification does not.
- No **emit-before-planner** pivot in this design — the advisory channel is retained deliberately (§8); emit is noted as the indicated next step if the A/B null-results.

---

## 13. Open questions

1. `tier` as raw `int` vs a `Tier` enum — enum is clearer but touches more call sites; lean `int` + module-level constants.
2. Where does package-induced `scan_config` run relative to native seeding — before or after `seed_predicted_native`? (Both consume the resolved Package layer; order them deterministically.)
3. Config-file-presence nodes (e.g. `conftest.py` must exist) — model as `CONFIG` or fold into `DATA_ASSET`? Provisionally `CONFIG` (it gates collection, not data).

*(Resolved by review: certify ordering uses `Layer`, not `tier` → add `Layer.CONFIG` (§4.5). Agent-guessed values get `PRESENT_UNVERIFIED` (§5). Discovery must cover `pydantic-settings`/`decouple`, not just `os.environ` (§7.2).)*

---

## 14. Adversarial review (2026-06-25) — what changed

Four Sonnet subagents (prior-art skeptic, feasibility engineer, framing advocate, authority/measurement critic) debated the v1 draft. Surviving critiques folded in:

- **Authority (own prior negative result).** §8 was reusing the advisory channel a prior doc proved inert. Resolution chosen: **keep the advisory** (to cleanly test whether a *richer, causal* advisory earns authority the thin one didn't) but make the experiment able to detect a null — frozen-repair-loop control arm, Config-failure stratum, cycles-to-fix metric, honest-success headline; prior result acknowledged by name (§8). Emit pivot recorded as the indicated next step if null (§12).
- **Feasibility.** "Certification generalizes unchanged" was false: `certify_all` iterates by `Layer` not `tier` (→ `Layer.CONFIG`, §4.5); `printenv` at build-certify can't see emitted ENV (→ rebuild-and-recertify, §5); Services need orchestration, not the single-container engine (descoped, §3.2/§12); presence can give false confidence (→ `PRESENT_UNVERIFIED`, §5).
- **Discovery.** Bare `os.environ` scan misses the dominant `pydantic-settings`/`decouple`/`environs` patterns in the exact Django/FastAPI repos we target; curated table emits false positives. Added framework config-readers + a pre-cert filter (§7.2).
- **Framing.** Contribution reframed from "six tiers" (a standard stack) to the **tier-agnostic certification method + cross-tier causal chains** (§2, §11); "world model" kept as an informal handle but precisified to a **certified belief state / necessary-condition monitor** with the **necessary-vs-sufficient** spine as the lede (§2); demand/provider replaced by **goal-nodes vs resource-nodes** (§3.1); the central claim stated as currently **unproven** and contingent on the A/B (§8, §11).
