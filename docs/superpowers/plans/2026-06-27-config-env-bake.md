# CONFIG→ENV Bake (Config-Tier Node Activation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bake the dep-graph's known-value CONFIG nodes into the synthesized Dockerfile as `ENV VAR=value` lines, so a repo whose tests/imports read an env var get a knowable value (from `.env.example` / package defaults / code defaults) baked into the rebuilt image — turning the Config tier from a permanently-UNKNOWN advisory note into a real, image-persisted fix.

**Architecture:** The agent already bakes *runtime-observed* env vars (ones it `export`ed during the run) into the image via `_bake_test_env_vars` → `extract_env_vars_from_ledger` → `Synthesizer.add_env_instruction`. This plan adds a **second source** into that same loop: the dep-graph CONFIG nodes, which already carry their value in `chosen_fix="env:VAR=value"`. The ledger source (runtime truth) keeps precedence; CONFIG values only fill vars the agent did not set. Same `add_env_instruction` sink, same secret/incidental denylist — no new emit machinery, no certify changes.

**Tech Stack:** Python 3, pytest, `src/envstate/synthesis.py` (env-bake helpers + denylist), `agent.py` (`DockerAgent`, the `_bake_test_env_vars` seam), `src/python_deps/depgraph` (the CONFIG nodes, read-only).

**Validation basis:** The design was adversarially validated by three sonnet agents (declaration-availability, in-container feasibility, architecture-fit) and a code-seam recon. Key facts this plan relies on, all verified against live source:
- CONFIG nodes are created in `config_scan.py:267` `_config_node` with `chosen_fix=f"env:{var}={value}"` (or `f"env:{var}=?"` when no value) and `state=State.UNKNOWN`.
- `agent.py:1665` `_bake_test_env_vars` already loops `for name, value in extract_env_vars_from_ledger(...): self.synthesizer.add_env_instruction(name, value)`.
- `src/envstate/synthesis.py:271` `_ENV_DENYLIST` (shell/build incidentals incl. PYTHONPATH) and `:278` `_RE_SECRET_NAME` (`SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL`) already exist and are applied in `extract_env_vars_from_ledger`'s inner `_record`.
- `Synthesizer.add_env_instruction(name, value)` (synthesizer.py:2767) emits `ENV name="value"` with `$`→`\$` escaping, dedupes exact lines, inserts at the top so it persists across layers.
- The agent reaches the final graph via `final_map, stop_reason = _run_v1_loop(...)` (agent.py:1255); `final_map.dep_graph` is the live `DepGraph`. The agent stores `self._final_installed` from `final_map` (agent.py:1275) but does **not** yet store the graph.
- The synthesizer itself is dep_graph-blind (`synthesize_build_recipe(recipe_input)` has no graph slot), so the bake MUST be wired in `agent.py` where `final_map` is in scope — NOT inside the synthesizer.

## Global Constraints

- **NO COMMITS. NO `git add`.** Leave the working tree dirty (carries the session's standing constraint and the existing large WIP). Every task's final step is "run the tests; do NOT commit." This overrides the writing-plans skill's default commit step.
- **Additive / default-safe.** When `final_map.dep_graph is None` (non-dep-graph arms) the new loop is skipped and behavior is byte-identical. `_bake_test_env_vars` is already wrapped in a best-effort `try/except`; keep it that way — a bake failure must never break the Dockerfile build.
- **Ledger precedence.** Runtime-observed env vars (from the ledger) ALWAYS win over static CONFIG values. A CONFIG value is baked only for a var the ledger did not already bake.
- **Reuse the existing denylist.** Secrets (`_RE_SECRET_NAME`) and incidentals (`_ENV_DENYLIST`) are NEVER baked. Do not introduce a second denylist — reuse the ones in `synthesis.py`.
- **No `?`-value bake.** A CONFIG node whose `chosen_fix` is `env:VAR=?` (value unknown) is skipped — there is nothing to bake.
- **Immutability.** `DepGraph`/`Node` are frozen; the extractor only reads.

## File Structure

- `src/envstate/synthesis.py` — add `bakeable_config_env(graph, *, exclude=frozenset())` next to `extract_env_vars_from_ledger` and the denylist it reuses.
- `agent.py` — store `self._final_dep_graph` from `final_map`; add the second bake source in `_bake_test_env_vars`.
- Tests: `tests/test_synthesis_config_bake.py` (new, pure-function unit tests); `tests/test_agent_config_bake_wiring.py` (new, source-guard for the agent wiring).

## Phase Overview

- **Phase 1 (Tasks 1–3, THIS PLAN)** — Config-tier bake. Self-contained, additive, e2e-validatable. Targets env-var-missing test/collection failures where the value is knowable.
- **Deferred (Phase 2, separate follow-up plan)** — Service-tier co-location (Postgres/Redis install+start via build_commands + runtime_preparation_commands, certify-after-start). Rationale + open questions documented at the end; NOT implemented here.

---

## Task 1: `bakeable_config_env(graph)` — pure extractor

**Files:**
- Modify: `src/envstate/synthesis.py` (add the function near `extract_env_vars_from_ledger`, ~line 290, so it reuses `_ENV_DENYLIST`, `_RE_SECRET_NAME`, `_strip_quotes`).
- Test: `tests/test_synthesis_config_bake.py` (create)

**Interfaces:**
- Consumes: `_ENV_DENYLIST`, `_RE_SECRET_NAME`, `_strip_quotes` (already in `synthesis.py`); CONFIG nodes carry `type is NodeType.CONFIG` and `chosen_fix="env:VAR=value"`.
- Produces: `bakeable_config_env(graph, *, exclude: frozenset[str] = frozenset()) -> list[tuple[str, str]]` — `(VAR, value)` pairs for CONFIG nodes with a known value, secrets/incidentals/`exclude`d names removed, de-duplicated (first occurrence wins).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthesis_config_bake.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, Node, NodeType, Layer, State, DiscoveredBy,
)
from src.envstate.synthesis import bakeable_config_env  # noqa: E402


def _cfg(var, value):
    """A CONFIG node exactly as config_scan._config_node builds it."""
    fix = f"env:{var}={value}"
    return Node(
        id=f"config:{var}", type=NodeType.CONFIG, name=var, layer=Layer.CONFIG,
        discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
        check_command=f"printenv {var}", fix_candidates=(fix,), chosen_fix=fix,
    )


def test_extracts_known_value():
    g = DepGraph().with_node(_cfg("DATABASE_URL", "postgresql://localhost:5432/db"))
    assert bakeable_config_env(g) == [("DATABASE_URL", "postgresql://localhost:5432/db")]


def test_skips_unknown_value():
    g = DepGraph().with_node(_cfg("DEBUG", "?"))     # chosen_fix == "env:DEBUG=?"
    assert bakeable_config_env(g) == []


def test_skips_secret_named_vars():
    g = (DepGraph()
         .with_node(_cfg("API_KEY", "sk-123"))
         .with_node(_cfg("DJANGO_SECRET_KEY", "abc"))
         .with_node(_cfg("DB_PASSWORD", "hunter2")))
    assert bakeable_config_env(g) == []


def test_skips_denylisted_incidentals():
    g = DepGraph().with_node(_cfg("PYTHONPATH", "/app"))   # in _ENV_DENYLIST
    assert bakeable_config_env(g) == []


def test_exclude_param_drops_named_vars():
    g = (DepGraph()
         .with_node(_cfg("DATABASE_URL", "postgresql://localhost/db"))
         .with_node(_cfg("REDIS_URL", "redis://localhost:6379/0")))
    out = bakeable_config_env(g, exclude=frozenset({"DATABASE_URL"}))
    assert out == [("REDIS_URL", "redis://localhost:6379/0")]


def test_value_with_equals_sign_is_preserved():
    g = DepGraph().with_node(_cfg("DATABASE_URL", "postgresql://u:p@h/db?sslmode=require"))
    assert bakeable_config_env(g) == [("DATABASE_URL", "postgresql://u:p@h/db?sslmode=require")]


def test_non_config_nodes_ignored():
    pkg = Node(id="pkg:requests", type=NodeType.PACKAGE, name="requests", layer=Layer.PIP,
               discovered_by=DiscoveredBy.STATIC_SCAN, state=State.MISSING,
               check_command="python -c 'import requests'", version="2.0", chosen_fix="pip:requests")
    assert bakeable_config_env(DepGraph().with_node(pkg)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_synthesis_config_bake.py -q`
Expected: FAIL — `cannot import name 'bakeable_config_env'`.

- [ ] **Step 3: Implement**

Add to `src/envstate/synthesis.py`, immediately AFTER `extract_env_vars_from_ledger` (which ends ~line 334). Use a lazy import of `NodeType` inside the function to keep `synthesis.py` free of a hard top-level dependency on the depgraph package (match the file's existing local-import style):

```python
def bakeable_config_env(graph, *, exclude: frozenset = frozenset()) -> list[tuple[str, str]]:
    """CONFIG nodes with a KNOWN value -> (VAR, value) pairs to bake as image ENV.

    Source = each Config-tier node's ``chosen_fix`` of the form ``env:VAR=value``
    (value != "?"), as built by config_scan._config_node. Applies the SAME secret +
    incidental denylist as the ledger path (_RE_SECRET_NAME / _ENV_DENYLIST), so
    credentials and shell incidentals are never baked. ``exclude`` drops vars the
    caller already baked (the ledger/runtime source takes precedence). First
    occurrence of a var wins; read-only (the frozen graph is never mutated).
    """
    # Local import keeps synthesis.py decoupled from the depgraph package. Safe: this
    # function is only ever called when a dep_graph exists, which implies the
    # enable_dep_graph arm already put src/ on sys.path (agent.py constructor cascade).
    from python_deps.depgraph.schema import NodeType

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for node in graph.nodes:
        if node.type is not NodeType.CONFIG:
            continue
        fix = node.chosen_fix or ""
        if not fix.startswith("env:") or "=" not in fix:
            continue
        var, _, value = fix[len("env:"):].partition("=")
        if not var or value == "?" or value == "":
            continue
        if var in seen or var in exclude:
            continue
        if var in _ENV_DENYLIST or _RE_SECRET_NAME.search(var):
            continue
        seen.add(var)
        out.append((var, _strip_quotes(value)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_synthesis_config_bake.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Run the existing synthesis suite for no regression**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/ -q -k "synthesis or env_var or dropped_env or env_bake"`
Expected: PASS (report the count and exact command if a name is absent).

- [ ] **Step 6: Verify; do NOT commit.**

---

## Task 2: Wire CONFIG bake into `_bake_test_env_vars` (the agent seam)

**Files:**
- Modify: `agent.py` — store `self._final_dep_graph` (init default + assign from `final_map`); add the second bake source in `_bake_test_env_vars`.
- Test: `tests/test_agent_config_bake_wiring.py` (create, source-inspection guard — `DockerAgent` is too heavy to instantiate in a unit test; the behavior of the extractor + `exclude` precedence is already covered by Task 1's unit tests).

**Interfaces:**
- Consumes: `bakeable_config_env(graph, *, exclude=...)` (Task 1); `final_map.dep_graph` (a `DepGraph | None`); the existing `extract_env_vars_from_ledger` and `self.synthesizer.add_env_instruction`.
- Produces: `self._final_dep_graph: DepGraph | None` (a new `DockerAgent` attribute) and the CONFIG-bake loop inside `_bake_test_env_vars`.

- [ ] **Step 1: Write the failing source-guard test**

```python
# tests/test_agent_config_bake_wiring.py
from pathlib import Path

_AGENT = (Path(__file__).resolve().parents[1] / "agent.py").read_text()


def test_final_dep_graph_is_stored_from_final_map():
    # The agent must capture the live graph off final_map so the bake can read it.
    assert "self._final_dep_graph = getattr(final_map, \"dep_graph\", None)" in _AGENT
    # And default it so it is always defined (exception path), like _final_installed.
    assert "self._final_dep_graph = None" in _AGENT


def test_bake_uses_bakeable_config_env_with_ledger_precedence():
    src = _AGENT
    # The CONFIG bake must be reachable from _bake_test_env_vars and pass exclude=
    # (the names already baked from the ledger) so the ledger source wins.
    bake = src[src.index("def _bake_test_env_vars"):src.index("def _verify_cleanroom_or_fail")]
    assert "bakeable_config_env(" in bake
    assert "exclude=" in bake
    assert "_final_dep_graph" in bake
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_agent_config_bake_wiring.py -q`
Expected: FAIL — the strings are not present yet.

- [ ] **Step 3: Store the graph off `final_map`**

In `agent.py`, find the initialization of `self._final_installed = ()` (the "always defined even on exception path" line, ~agent.py:1153). Add a default directly after it:

```python
        self._final_installed = ()  # populated after _run_v1_loop; always defined even on exception path
        self._final_dep_graph = None  # populated after _run_v1_loop; read by _bake_test_env_vars
```

Then find where the result is unpacked and `self._final_installed` is assigned from `final_map` (~agent.py:1275, right after `final_map, stop_reason = _run_v1_loop(...)`):

```python
            self._final_installed = tuple(getattr(final_map, "installed", ()) or ())
```

Add immediately after it:

```python
            self._final_dep_graph = getattr(final_map, "dep_graph", None)
```

- [ ] **Step 4: Add the CONFIG bake source to `_bake_test_env_vars`**

Replace the body of `_bake_test_env_vars` (agent.py:1665-1678) so it tracks the ledger-baked names and then bakes CONFIG values for the remaining vars. Keep the existing `try/except` best-effort wrapper:

```python
    def _bake_test_env_vars(self) -> None:
        """DROPPED_ENV: bake test-required env vars the agent set (export / inline
        prefix) into the image so the rebuilt seed reproduces the working env.
        Then bake known-value Config-tier vars the agent did NOT set (static hints
        from .env.example / package defaults), so a required var with a knowable
        value persists in the rebuilt image. Ledger (runtime truth) takes precedence.
        Best-effort: a failure degrades to today's behavior (Dockerfile still built)."""
        try:
            from src.envstate.synthesis import extract_env_vars_from_ledger
            extra = list(getattr(self, "verified_test_commands", None) or [])
            if getattr(self, "verified_test_command", None):
                extra.append(self.verified_test_command)
            already: set[str] = set()
            if self.action_ledger is not None:
                for name, value in extract_env_vars_from_ledger(self.action_ledger, extra_commands=extra):
                    self.synthesizer.add_env_instruction(name, value)
                    already.add(name)
            # Config-tier bake runs AFTER the ledger bake and imports separately, so a
            # failure in the (newer) config path can never suppress the proven ledger
            # bake — by here those ENV lines are already on the synthesizer.
            graph = getattr(self, "_final_dep_graph", None)
            if graph is not None:
                from src.envstate.synthesis import bakeable_config_env
                for name, value in bakeable_config_env(graph, exclude=frozenset(already)):
                    self.synthesizer.add_env_instruction(name, value)
        except Exception as env_exc:
            print(f"[v1] env-bake skipped: {env_exc}")
```

- [ ] **Step 5: Run the source-guard test to verify it passes**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/test_agent_config_bake_wiring.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the agent-construction / synthesis suites for no regression**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/ -q -k "synthesis or config_bake or agent" --ignore=tests/test_benchmark_arm_v1.py --ignore=tests/test_repo2run_benchmark.py --ignore=tests/test_repo2run_concurrency.py --ignore=tests/test_repo2run_dataset.py`
Expected: PASS except the 5 known pre-existing/environmental failures (missing `pypdf`, the docker-marked probe, the `FakeSynth.observation_pass_ratio` integration test, the adapter django-pytester test) — confirm any failure is one of those, not introduced by this task.

- [ ] **Step 7: Verify; do NOT commit.**

---

## Task 3: End-to-end mechanism proof

**Files:** none (validation only). Uses `agent.py` + `.env` (OpenRouter) + Docker.

This proves the bake FIRES end to end (a CONFIG node's value lands in the generated Dockerfile as an `ENV` line) and that it does not regress a known-good run. Improving a specific repo's pass-rate is a separate *measurement* question (needs a confirmed fail-on-missing-env target) and is out of scope for this mechanism-proof task.

- [ ] **Step 1: Full unit suite**

Run: `cd /Users/john/john-planner-v3 && python3 -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_benchmark_arm_v1.py --ignore=tests/test_repo2run_benchmark.py --ignore=tests/test_repo2run_concurrency.py --ignore=tests/test_repo2run_dataset.py`
Expected: green except the known pre-existing failures (≤5: `pypdf` collection errors, docker-marked probe, `FakeSynth` integration, adapter django-pytester). No NEW failures.

- [ ] **Step 2: Run the dep-graph arm on a repo that declares config, and inspect the Dockerfile**

memU-server declares config via its compose/`.env` and builds cleanly under the graph scheduler (its tests already pass, so it is a safe MECHANISM target — we are checking that CONFIG ENV lines now appear, not chasing a pass-rate delta):

```bash
cd /Users/john/john-planner-v3
python3 agent.py https://github.com/NevaMind-AI/memU-server \
  --model deepseek/deepseek-v4-flash --steps 30 --enable-graph-scheduler \
  > rat_run_v1gs/agent_memU_configbake.log 2>&1
```

- [ ] **Step 3: Confirm CONFIG ENV lines were baked + no regression**

```bash
cd /Users/john/john-planner-v3
echo "=== baked ENV lines in the generated Dockerfile ==="
grep -n '^ENV ' workplace/Dockerfile
echo "=== result held? ==="
python3 -c "import json; d=json.load(open('workplace/agent_run_summary.json')); print('configuration_success', d.get('configuration_success'), 'pass_rate', d.get('in_build_pass_rate'))"
```
Expected: `ENV` lines for memU's known-value config vars now appear in the Dockerfile (beyond the three pip-bootstrap `PIP_*` lines), AND `configuration_success=True` with `in_build_pass_rate` unchanged (no regression). Confirm NO secret-named var (anything matching `_RE_SECRET_NAME`) was baked.

- [ ] **Step 4: Do NOT commit. Report the baked ENV lines + the unchanged result.**

---

## Deferred: Phase 2 — Service-tier co-location (separate follow-up plan)

**Not implemented here.** Phase 2 makes SERVICE nodes (Postgres/Redis declared in compose/CI) real by installing+starting the service *inside the single container* and certifying it. It is deferred because, unlike the Config bake, it is **not** a clean additive seam and has open questions the recon surfaced that must be resolved before a non-placeholder plan can be written:

- **Install vs start split.** A started daemon does not persist in a Docker image layer. The install (`apt-get install -y postgresql`) must go into `build_commands` (a `RUN` layer); the start + provision (`service postgresql start`; `su -c "createdb ..." postgres`; readiness wait) must go into `runtime_preparation_commands`, which the runner executes before tests (verified: `apply_build_recipe` only bakes `build_commands`; `runtime_preparation_commands` run live and are NOT image-baked — synthesizer.py:2726). There is already a `_coalesce_postgres_build_configuration_commands` (synthesizer.py:1282) that groups PG start+psql into one layer — Phase 2 should build on it.
- **Certify-after-start.** `certify.py:59-62` has an explicit early-return that keeps every SERVICE node `UNKNOWN` (the scratch container has no running service). Phase 2 needs a runtime-level certify that runs the node's `pg_isready` check *after* the start commands, not the static scratch-container certify. Lifting the guard naively makes `pg_isready` fail and flips the node MISSING — worse than UNKNOWN.
- **Open infra question.** Where exactly the service install/start commands get injected into the recipe (the agent's `build_commands` assembly vs the dep-graph emit path) needs one more trace before it can be specified.
- **Local-vs-external classifier (prerequisite).** ~36% of the connection-failing repos need *live external* APIs (e.g. nba_api→stats.nba.com) that cannot be co-located. A classifier (compose/CI present + local image → co-locatable; else external → skip) must run before the translator, or that 36% silently gets no benefit. (Validated: 6/11 inspected repos EXTRACTABLE, 4 EXTERNAL-ONLY, 1 UNDECLARED.)
- **In-container recipe (validated, ready to reuse).** Proven live in Docker: Postgres = `apt-get install -y postgresql && service postgresql start` (no systemd needed; cluster auto-initialized) + `su -c "psql -c \"CREATE USER app PASSWORD 'x'; CREATE DATABASE db OWNER app;\"" postgres` (admin MUST run as the `postgres` OS user — root fails silently) + `until pg_isready -h localhost; do sleep 1; done`. Redis = `apt-get install -y redis-server && redis-server --daemonize yes` then `redis-cli ping`→`PONG`. DATABASE_URL/REDIS_URL wired to `localhost` and baked via Phase 1's mechanism.

When Phase 1 has landed and a measurable fail-on-connection target is confirmed (e.g. `django-oauth-toolkit` / `rq`, which *fail* — not `skip` — on a missing local service), write the Phase 2 plan as its own spec→plan cycle.

---

## Self-Review notes (author)

- **Spec coverage:** Task 1 = the pure extractor (reuses the existing denylist, no `?`-bake, `exclude` for precedence). Task 2 = wiring into the existing `_bake_test_env_vars` loop with ledger precedence + storing the graph off `final_map`. Task 3 = mechanism e2e. Phase 2 (services) explicitly deferred with rationale + open questions.
- **Placeholder scan:** every code step shows complete code; the deferred section is labeled deferred (not a placeholder task).
- **Type consistency:** `bakeable_config_env(graph, *, exclude=frozenset()) -> list[tuple[str,str]]` is defined in Task 1 and consumed in Task 2 with `exclude=frozenset(already)`; `self._final_dep_graph` is defined (default + assignment) and read in the same task. `add_env_instruction(name, value)` matches synthesizer.py:2767.
- **Risk note:** Task 2 touches `agent.py` (large), but the change is additive and inside the existing best-effort `try/except`; when `dep_graph is None` the new loop is skipped (byte-identical for non-dep-graph arms). The source-guard test plus Task 1's behavioral tests cover it without instantiating the heavy `DockerAgent`.
- **What this does NOT fix:** service/connection failures (Phase 2), and config vars whose value is genuinely unknowable (`env:VAR=?` — correctly skipped; those still rely on the agent setting them at runtime).
- **Known limitation (reviewed, accepted):** `_RE_SECRET_NAME` is a substring (`.search`) match, so legitimate non-secret vars that merely *contain* a secret-ish token (`TOKENIZER_URL`, `RESET_PASSWORD_URL`, `AUTH_TOKEN_EXPIRY`) are conservatively NOT baked. This is the exact same regex the proven ledger path already uses, so the config path is consistent with it; the bias is safe (never leak a secret) but not exhaustive (a few benign vars get dropped). Accepted as-is — do not loosen the regex in this plan.
- **Review status:** plan reviewed by 3 sonnet agents (code-seam correctness, logic/off-path safety, test design/conformance). 0 Critical/0 blocking; all 8 cited seams confirmed against live source, all 7 Task 1 tests simulated PASS, all Task 2 source-guard strings exact-matched. Two hardening fixes applied: split the config-bake import so it cannot suppress the ledger bake (Task 2 Step 4), and a comment on the lazy-import `sys.path` assumption (Task 1 Step 3).
