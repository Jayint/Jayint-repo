# Runtime-Feedback Loop for the Requirement Dependency Graph — Design

**Date:** 2026-06-26
**Status:** Design — approved in brainstorming. UNCOMMITTED working tree (standing no-commit rule).
**Worktree:** `john-planner-v3` (`/Users/john/john-planner-v3`)
**Background:** `docs/FINDINGS-config-service-edge-dynamism.md` §4 (why runtime feedback before dynamic edges).

---

## 1. Goal

Close the open arc **test-execution → graph**. During the build phase, observe command
failures from **both** command sources that run in the agent's live in-build container —
the graph's deterministic **emit** and the agent's **own commands** — deterministically
classify each failure into a requirement class, and **append the revealed requirement** to
the live `WorldModelMap.dep_graph` as a first-class `DiscoveredBy.RUNTIME` node + edge.

The graph thereby **learns at runtime** the requirements static analysis could not produce:
dynamically-imported modules, `dlopen`-loaded native libraries, runtime-only env vars, and
real service reachability — each grounded in an observed failure, not a guess.

## 2. Background & thesis

The graph today certifies the **necessary** half of its spine — presence, via host-run
`check_command` probes across tiers Platform/System/Runtime/Packages/Services/Config. Static
discovery records what the repo *declares*. The gap where env-construction agents fail is
*declared → actual*: mocked-vs-real services, dynamic imports, `dlopen` libs, env vars only
read on a hot path. A **runtime failure is observed necessity** — strictly stronger evidence
than static declared necessity — and it is exactly the signal that closes that gap.

v1 delivers the **discovery** of runtime necessity (append requirements). It deliberately does
**not** provision/auto-fix them, and does **not** yet certify the **sufficient** half
(`repo_tests_pass` flipping green) — those are later slices (§14).

## 3. Scope (v1)

**In scope**
- Deterministic signature extraction over the per-cycle action ledger (both command sources +
  the test gate, already unified there).
- Append the revealed requirement to the live graph: five classes across four tiers (§7).
- Idempotent annotate-or-append (deterministic node ids); edge hung off the Test goal.
- Appended nodes carry a `check_command`; the **existing** certify pass owns `state`.
- A new flag gating the whole feature, default off, byte-identical when off.

**Out of scope (deferred — §14)**
- Auto-fix / emit-routing / provisioning of discovered requirements (descoped this session).
- Flipping `repo_tests_pass` on gate-pass (the sufficiency loop).
- An LLM/agent classifier (the deterministic classifier ships; the LLM **seam** is designed in,
  not built).
- Finer per-module attribution; multiple discoveries extracted from one observation.

## 4. Architecture & data flow

**One tap, not two.** `emit_drain`'s `run_recipe`, the agent's own `run`, and the
`VERIFY_TEST_CMD` gate all append `make_action_event(cmd=…, stdout=…)` to the **same action
ledger**. So instead of instrumenting two execution sites we read **one** stream: the ledger
events new since the previous ingest.

**Two new pure modules** (no `envstate` imports — unit-testable with plain data):
- `runtime_classify` — the deterministic classifier: `classify_observation(command, output)
  -> Discovery | None`, plus the two new sub-parsers. Reuses the existing classifiers.
- `runtime_ingest` — `ingest_runtime_failures(graph, observations, classifiers=…)
  -> (new_graph, discoveries)`: maps each `Discovery` to an idempotent graph mutation and
  returns a new immutable `DepGraph` plus the `Discovery` records (for logging/advisory).

**One wiring site** — `orchestrator.run_v1`, gated on the new `enable_runtime_feedback`,
default off. Near the end of each cycle, over the ledger slice since the last ingest:

```
new_events = ledger[mark:]                          # emit + agent + gate, unified
mark = len(ledger)
obs = [(e.cmd, e.stdout) for e in new_events]
graph, found = ingest_runtime_failures(current_map.dep_graph, obs)
current_map = merge_map(current_map, dep_graph=graph, dep_advisory=render(graph))
```

**Per-cycle data flow:** `_dep_emit_phase` (certify → emit) → agent work → test gate →
**ingest new ledger events → mutate `dep_graph` → re-render advisory** → next cycle. Because
ingest runs before the next cycle's certify pass, a freshly appended node is certified one
cycle later through the *existing* machinery — no new loop is introduced.

**Graph lifecycle dependency.** Runtime feedback requires the **live emit arm** —
`enable_runtime_feedback` implies `enable_dep_emit` (which in turn implies `enable_dep_graph`).
The A/B is `v1gde` (emit, no feedback) vs `v1gder` (emit + feedback), which isolates the
feedback delta on an identical baseline. A bare `--enable-runtime-feedback` flag therefore
activates emit automatically; the toggles are not independent.

## 5. Components & files

**New (pure):**
- `src/python_deps/depgraph/runtime_classify.py` — `classify_observation` + `Discovery`
  construction; dispatch order in §6.
- `src/python_deps/depgraph/runtime_ingest.py` — `ingest_runtime_failures` (graph mutation),
  idempotent annotate/append.

**New parsers (extend the existing classifier module):**
- `src/python_deps/failure_classifier.py` — add `classify_config_error(command, output)
  -> str | None` and `classify_tool_error(command, output) -> str | None` (siblings of
  `classify_dependency_failure`).

**Modified (wiring / flag plumbing):**
- `src/envstate/orchestrator.py` — the per-cycle ingest call + the carry/re-render guard,
  gated on `enable_runtime_feedback`.
- `agent.py`, `multi_docker_eval_adapter.py`, `run_rat_benchmark.py`,
  `run_repo2run_benchmark.py` — `DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK → enable_runtime_feedback`
  (mirrors the existing `enable_dep_emit` plumbing).

**Reused as-is:** `failure_classifier.classify_dependency_failure`,
`service_scan.classify_service_error`, `import_mapping.map_import_to_package`,
`depgraph/ids.py`, `depgraph/schema.py` (`DiscoveredBy.RUNTIME` and `origin="runtime"` already
exist), `depgraph/certify.py` (owns state, already skip-guards services).

## 6. The classifier (deterministic first, pluggable)

**Interface:** `classify_observation(command, output) -> Discovery | None`. The deterministic
implementation tries sub-classifiers in priority order and returns the first hit (or `None`,
which means "ignore — not requirement-bearing"):

1. `classify_dependency_failure` →
   - `module_not_found` / `import_name_error` → **Package** discovery (import name mapped to a
     distribution via `map_import_to_package`).
   - `native_library_missing` → **SystemLib** discovery (`details["library"]` soname).
   - `no_matching_distribution` / `dependency_conflict` / `syntax_*` / `not_dependency_related`
     → ignored (build-time / non-environmental — already owned elsewhere).
2. `classify_service_error` → **Service** discovery (kind; host:port parsed from the text if
   present).
3. `classify_config_error` (new) → **Config** discovery (env-var name).
4. `classify_tool_error` (new) → **Tool** discovery (executable name).

First match per observation in v1 (multi-extract deferred; the per-cycle re-ingest gives a
natural discover-one-per-cycle cadence).

**Pluggable seam for an LLM residual-pass (not built in v1).** `ingest_runtime_failures`
accepts `classifiers: Sequence[Callable]`, default `[deterministic_classify]`. Later an
`llm_classify` can be appended, running **only on observations the deterministic pass left
unmatched**. The guardrail that keeps the LLM inside the project's invariant: an LLM hit still
produces a node **with a `check_command`** that the existing certify pass validates — it only
ever *proposes a node*, never flips `state`. LLM discoveries are tagged
`confidence="runtime-llm-proposed"` (vs `"runtime-deterministic"`) so they are visible and
gateable. v1 ships the deterministic classifier only.

## 7. Taxonomy (signature → append)

| Failure signature | Classifier | Node type | Tier | `check_command` |
|---|---|---|---|---|
| `ModuleNotFoundError` / `ImportError: cannot import name` | `classify_dependency_failure` → `module_not_found`/`import_name_error` | **Package** (`pkg:<dist>`) | 4 | `python3 -c "import <import_name>"` |
| `libfoo.so.N: cannot open shared object file` | `classify_dependency_failure` → `native_library_missing` | **SystemLib** (`syslib:<soname>`) | 2 | `ldconfig -p \| grep -q <soname>` |
| `command not found` / `FileNotFoundError: … '<tool>'` | `classify_tool_error` (new) | **Tool** (`tool:<name>`) | 2 | `command -v <name>` |
| `KeyError: '<VAR>'` / pydantic `ValidationError` (field required) | `classify_config_error` (new) | **Config** (`config:<VAR>`) | 6 | `printenv <VAR>` |
| `could not connect` / `Connection refused :PORT` | `classify_service_error` → kind | **Service** (`service:<kind>`) | 5 | none (advisory; certify skip-guards) |

**Layers** (so the existing certify/advisory layer-ordering applies unchanged): Package →
`Layer.PIP`, SystemLib → `Layer.SYSTEM`, Tool → `Layer.TOOLCHAIN`, Config → `Layer.CONFIG`,
Service → `Layer.SERVICES`. (Tier is derived from `NodeType` by `schema.tier_for_type`.)

**Ignored (no mutation):** assertion failures, ordinary test-logic errors,
`not_dependency_related`, syntax/version errors, and `no_matching_distribution` /
`dependency_conflict` (build-time install failures the static graph + emit already own). Rule:
**silence over noise** — only a *new or stronger environmental necessity* writes to the graph.

## 8. Trust / state semantics

**Runtime DISCOVERS; certify CERTIFIES.** The codebase's core invariant is "only `certify`
flips `state`" (`certify.py`). Ingest respects it: it appends a node *with* a `check_command`
+ evidence (and `DiscoveredBy.RUNTIME`), but leaves `state` to the next `certify_refresh`. A
freshly appended node is certified next cycle exactly like every other node — no special path.

**Services are the one exception** — certify already skip-guards `SERVICE` nodes (it cannot
certify reachability reliably in the build container), so for a service the runtime
connection-refused *is* the certification signal; the node carries it and renders advisory. No
new state-flip authority is invented for any class certify already handles.

**Trust ordering.** A runtime failure is observed necessity — strictly stronger than static
declared necessity. When runtime contradicts a static node, ingest records the stronger
evidence and lets certify re-adjudicate; runtime never silently downgrades a node.
`Node.data["runtime_confidence"]` records `"runtime-deterministic"` (v1) so provenance is
explicit and auditable.

## 9. Append vs annotate (reconciliation) & attribution

**Deterministic ids** (`import:`, `pkg:`, `syslib:`, `tool:`, `config:`, `service:`) make
reconciliation idempotent:
- **id already present** → *annotate*: merge the runtime evidence, set the runtime confidence /
  `DiscoveredBy.RUNTIME` provenance, never duplicate. (E.g. a package already in the closure
  that fails to import at runtime is annotated, not re-added.)
- **id new** → *append* the node.
- Re-seeing the same failure across cycles updates evidence; it never spawns a second node.

**Attribution.** Each discovered requirement hangs off the **Test goal**:
`test:repo_tests_pass --requires--> <node>` with `origin="runtime"`. A runtime failure during
the build/test phase is literally "the suite requires this." All five target types are legal
`requires` destinations from `Test` per `EDGE_RULES`. Finer attribution to a specific failing
module is deferred (§14).

## 10. Data structures

```python
# runtime_classify.py
@dataclass(frozen=True)
class Observation:
    command: str
    output: str          # combined stdout/stderr text from the ledger event

@dataclass(frozen=True)
class Discovery:
    node_type: NodeType          # PACKAGE | SYSTEM_LIB | TOOL | CONFIG | SERVICE
    name: str                    # distribution / soname / tool / VAR / service-kind
    layer: Layer
    evidence: str                # the failure excerpt that revealed it
    check_command: str | None    # None only for SERVICE (advisory)
    confidence: str = "runtime-deterministic"
    data: dict = field(default_factory=dict)   # service host/port, original import name, …
```

`ingest_runtime_failures(graph, observations, classifiers=(deterministic_classify,))` maps each
non-`None` `Discovery` to a `Node` (id via `ids.py`, `discovered_by=DiscoveredBy.RUNTIME`) and a
`Test --requires--> node` `Edge(origin="runtime")`, applying the §9 annotate/append rule, and
returns `(new_graph, list[Discovery])`.

## 11. Error handling

Ingest **must never break a run** (same contract as `build_advisory_for_repo`): the per-cycle
call is wrapped so any exception logs a warning and returns the graph unchanged. Sub-parsers are
defensive over malformed/empty text. With the flag off, ingest is never called and the loop is
byte-identical to today.

## 12. Flag / arm plumbing

New env flag `DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK="1"` → `enable_runtime_feedback`, default
**off**. `enable_runtime_feedback` implies `enable_dep_emit` (set in `agent.py` __init__ before
`enable_dep_emit`, which ORs it in); `enable_dep_emit` implies `enable_dep_graph`. A bare
`--enable-runtime-feedback` therefore activates the full live emit arm automatically. The
clean A/B is `v1gde` (emit only) vs `v1gder` (emit + feedback) — both arms share identical
graph infrastructure; only the ingest step differs. Plumbing mirrors `enable_dep_emit`
end-to-end (`agent.py` → `multi_docker_eval_adapter.py` → the two benchmark runners).

## 13. Testing strategy (TDD)

- **Unit — parsers:** `classify_config_error`, `classify_tool_error` against fixed fixtures
  (positive shapes + the ignore set). RED→GREEN.
- **Unit — dispatch:** `classify_observation` priority order; residual → `None`; native-lib vs
  module-not-found disambiguation; build-time failures ignored.
- **Unit — ingest (pure, no Docker):** append-new; annotate-existing (idempotent across two
  cycles); ignore-set produces no mutation; edge hung off the Test goal with `origin="runtime"`;
  `check_command` set per class; service appended advisory (no state path).
- **Integration — wiring:** feed a synthetic ledger through the orchestrator with the flag on;
  assert the graph gained the expected `DiscoveredBy.RUNTIME` nodes and the advisory re-rendered;
  assert flag-off is byte-identical.
- **Optional gated e2e:** on `testdrivenio/fastapi-celery-project` (needs postgres/redis/
  rabbitmq + env vars) — confirm the loop surfaces a real runtime discovery static analysis
  missed. Not required for CI.

## 14. Non-goals & future seams

- **Auto-fix / emit-routing** of discovered installable requirements (the cheap self-heal):
  intentionally descoped this session; the appended node is emit-ready (carries a
  `check_command`) so re-enabling is additive.
- **Sufficiency loop:** flip `repo_tests_pass` to SATISFIED on a green gate (the *sufficient*
  half of the spine).
- **LLM residual-pass:** the `classifiers` seam (§6) is built; the LLM classifier is not.
- **Multi-extract per observation** and **finer per-module attribution.**
- **Truncation:** ledger stdout is head+tail truncated, so a signature buried deep in very large
  output can be missed (acceptable for v1).

## 15. Code references

- Tap point A (emit): `src/envstate/depgraph_live.py:117` (`run_recipe` → `report.commands`).
- Tap point B (agent): `src/envstate/build_agent.py:641, 864` → ledger `_append_ledger_event`
  (`:944-978`, stdout stored `:972`).
- Test gate: `src/envstate/orchestrator.py:48, 151-160` (`VERIFY_TEST_CMD`).
- Live graph owner: `src/envstate/world_model.py:100` (`WorldModelMap.dep_graph`),
  `merge_map` (`:249`); per-cycle `_dep_emit_phase` (`orchestrator.py:104-140`).
- Classifiers reused: `src/python_deps/failure_classifier.py:33`
  (`classify_dependency_failure`), `src/python_deps/depgraph/service_scan.py:152`
  (`classify_service_error`).
- Certify invariant / service skip-guard: `src/python_deps/depgraph/certify.py:36-76` (`:61`).
- Schema: `src/python_deps/depgraph/schema.py` (`DiscoveredBy.RUNTIME:71`, `Edge.origin` runtime,
  `EDGE_RULES:87`). Ids: `src/python_deps/depgraph/ids.py`.
