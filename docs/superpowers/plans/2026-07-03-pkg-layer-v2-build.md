# Plan — Clean Python package-requirement layer (four-plane) — SEPARATE build

Build the four-plane Python package-requirement layer as a **new, parallel module**
(`src/python_deps/pkg_layer/`) that does NOT modify the existing `depgraph/` code, so it can be
A/B-evaluated against the current system. Design synthesised in
`docs/superpowers/plans/2026-07-03-root-selection-ab-eval.md` and the conversation that produced it.

Through-line: **Contract decides what to install; Closure is the package graph; Usage audits
coverage; Environment is the sole naming + certification authority; under-declaration is fixed by
an explicit trust-ordered repair, never by guessing at construction.**

## Global Constraints (bind every task + every reviewer)

- **SEPARATE module** `src/python_deps/pkg_layer/`. Do NOT modify existing `src/python_deps/depgraph/*`
  or `src/python_deps/*.py`. Reuse them by import only.
- **Reuse primitives, don't reimplement**: import scanning, manifest reading, import→dist mapping,
  and resolve come from existing modules (see "Reusable primitives").
- **Pure / no-Docker**: the Environment plane (`packages_distributions`) and any install live
  behind an injected interface (a Protocol). Unit tests use fixtures/fakes — NO live container in
  this build.
- **TDD**: write the test first (RED), then implement (GREEN). pytest.
- **Immutability**: frozen dataclasses; every transform returns a new object.
- **Certify-or-flag**: nothing enters the layer as truth unless an authority proves it; unresolved
  runtime imports are flagged, never guessed into the graph.
- **Rule-over-LLM**: the LLM (and PyPI probe, curated table) only PROPOSE candidates in the repair
  path; they never become `provided_by` edges. Precedence: `declared > closure > certified > candidate`.
- **Paper-clean**: ONE path per function; no flag-gated dual behaviour inside the module.
- **Commit locally only, NEVER push.**

## Reusable primitives (import these; do not reinvent)

- `from python_deps.import_graph import scan_imports` → `(findings, project_local, errors)`;
  each `ImportFinding` has `.import_name` (top-level, dotted-truncated), `.classification`
  (`"external"|"stdlib"|"local"`), `.source_files: tuple[str,...]`.
- `from python_deps.evidence import collect_python_dependency_evidence` → `PythonDependencyEvidence`
  with `.declared_dependencies: list[PythonRequirement]`; each `PythonRequirement` (frozen) has
  `.name, .specifier, .marker, .extras, .source, .kind ("dependency"|"optional_dependency"), .trust`.
- `from python_deps.import_mapping import map_import_to_package, is_unresolved, CURATED_IMPORT_TO_PACKAGE`.
- `from python_deps.depgraph.resolve import resolve_closure` (host-side `uv lock`) — Task 4 only.
- Canon key: PEP 503 — `re.sub(r"[-_.]+","-",name.strip()).lower()`.

---

## Task 1 — Plane data model (`pkg_layer/planes.py`)

Frozen dataclasses + enums for the four planes. No logic beyond simple queries.

- `class ImportContext(enum.Enum)`: `RUNTIME, TEST, OPTIONAL, TYPING, DYNAMIC`.
- `class Tier(enum.Enum)`: `DIRECT, TRANSITIVE`.
- `class Trust(enum.Enum)`: `DECLARED, CLOSURE, CERTIFIED, CANDIDATE`.
- `@dataclass(frozen=True) class DeclaredDep`: `name: str, kind: str, specifier: str|None, marker: str|None, group: str|None` (group = optional-dependency extra name or None).
- `@dataclass(frozen=True) class ClosurePkg`: `name: str, version: str|None, tier: Tier`.
- `@dataclass(frozen=True) class ImportNode`: `name: str, context: ImportContext, provenance: tuple[str,...]`.
- `@dataclass(frozen=True) class ProvidedEdge`: `import_name: str, dist: str, origin: str` (origin e.g. `"certified"`).
- `@dataclass(frozen=True) class CandidateEdge`: `import_name: str, dist: str, trust: Trust, verify_required: bool` (trust always CANDIDATE here).
- `@dataclass(frozen=True) class PackageLayer`: `contract: tuple[DeclaredDep,...], closure: tuple[ClosurePkg,...], imports: tuple[ImportNode,...], provided: tuple[ProvidedEdge,...]`.
  Query helpers (pure): `runtime_imports() -> tuple[ImportNode,...]`; `direct_packages()`, `transitive_packages()`; `declared_names() -> frozenset[str]` (canon); `provided_imports() -> frozenset[str]` (canon of import names that have a ProvidedEdge).

**Tests** (`tests/pkg_layer/test_planes.py`): construction + each query helper on a small hand-built layer; canon dedup (e.g. `Flask` vs `flask`).
**DoD**: frozen everywhere; queries pure; 100% of the query helpers covered.

---

## Task 2 — Usage plane: context-tagged import scan (`pkg_layer/usage.py`)

Turn a repo into `ImportNode`s tagged by CONTEXT. Reuse `scan_imports` for the raw external
import set + provenance, then run a focused AST pass to assign context.

- `scan_usage(repo_path: str) -> tuple[ImportNode,...]`.
- Context rules (per import site; an import's node context = the STRONGEST runtime-relevance across
  its sites, precedence RUNTIME > OPTIONAL > TYPING > TEST > DYNAMIC when an import appears in
  several contexts — a runtime use anywhere makes it RUNTIME):
  - inside a `try:` whose `except` handles `ImportError`/`ModuleNotFoundError` → `OPTIONAL`.
  - inside an `if TYPE_CHECKING:` block (name `TYPE_CHECKING`, from typing) → `TYPING`.
  - `importlib.import_module("x")` / `__import__("x")` with a string literal → `DYNAMIC` (record the literal as the import name).
  - source file under a test dir (`tests/`, `test/`, `conftest.py`) → `TEST`.
  - otherwise → `RUNTIME`.
- Only `classification == "external"` imports become nodes (stdlib/local dropped, as `scan_imports` already classifies).

**Tests** (`tests/pkg_layer/test_usage.py`): fixtures in `tmp_path` — a guarded `try/except ImportError` → OPTIONAL; a `if TYPE_CHECKING:` import → TYPING; a plain runtime import → RUNTIME; a test-dir import → TEST; an import used BOTH guarded and at runtime → RUNTIME (precedence). At least one real assertion per context.
**DoD**: context precedence correct; provenance populated; reuses `scan_imports` (does not re-walk for the external/stdlib/local split).

---

## Task 3 — Contract plane + Contract-only roots (`pkg_layer/contract.py`)

Read declared deps and select roots from the CONTRACT ONLY (the verifier decision; structurally
cannot re-add optional deps via imports — the bug the A/B found).

- `read_contract(repo_path: str) -> tuple[DeclaredDep,...]` — from `collect_python_dependency_evidence`; map each `PythonRequirement` → `DeclaredDep` (group = the extra name for `kind=="optional_dependency"`, parsed from `.source`/`.extras`, else None).
- `select_roots(contract: tuple[DeclaredDep,...], needed_extras: frozenset[str]) -> tuple[str,...]` — canon-deduped dist names: every `kind=="dependency"` dep, PLUS every `kind=="optional_dependency"` dep whose `group` ∈ `needed_extras`. **Imports are never consulted here.** Returns canon dist tokens (name only; constraint carrying is out of scope for the A/B, which compares membership).

**Tests** (`tests/pkg_layer/test_contract.py`): runtime deps always included; optional dep EXCLUDED when its group ∉ needed_extras and INCLUDED when ∈; canon dedup; an import that would have been gap-filled by the old design is NOT present (assert a known optional-backend name like `brotli` is absent with empty needed_extras).
**DoD**: no import consulted; needed_extras gating correct; the "package_roots re-adds optional" bug is structurally impossible here.

---

## Task 4 — Closure plane (`pkg_layer/closure.py`)

Wrap resolve into ClosurePkg nodes + a direct/transitive tag. Keep the resolve SOURCE pluggable.

- `class ResolveSource(Protocol)`: `resolve(roots: tuple[str,...]) -> tuple[tuple[str, str|None, bool],...]` returning `(name, version, is_direct)` rows.
- `build_closure(roots, source: ResolveSource) -> tuple[ClosurePkg,...]` — maps rows → `ClosurePkg(name, version, Tier.DIRECT if is_direct else Tier.TRANSITIVE)`, canon-deduped.
- `class UvResolveSource`: adapts `resolve_closure` (host uv). `is_direct = canon(name) in {canon(r) for r in roots}`. This is the ONLY place that shells out; it is NOT exercised in unit tests (a fake ResolveSource is).

**Tests** (`tests/pkg_layer/test_closure.py`): a fake `ResolveSource` returning direct+transitive rows → correct Tier tagging + canon dedup. UvResolveSource is NOT run in tests (guarded/documented).
**DoD**: resolve source injected; no live uv in unit tests; direct/transitive from the roots set.

---

## Task 5 — Alignment: typed plane diffs (`pkg_layer/align.py`)

Compute the context-scoped completeness + the other typed diffs. Pure — takes a PackageLayer.

- `@dataclass(frozen=True) class Alignment`: `under_declared: tuple[str,...]` (canon import names), `resolution_anomaly: tuple[str,...]`, `transitive_only: tuple[str,...]`, `installed_unused: tuple[str,...]`.
- `align(layer: PackageLayer) -> Alignment`:
  - `under_declared` = RUNTIME imports whose canon name is NOT in `layer.provided_imports()` AND whose curated/declared dist is not in the closure. **Only `ImportContext.RUNTIME` counts** (optional/typing/test/dynamic never flag). This is the honest needle.
  - `resolution_anomaly` = declared (canon) not present in closure (canon).
  - `transitive_only` = closure TRANSITIVE packages not reachable as any declared dist (informational — here: closure tier==TRANSITIVE).
  - `installed_unused` = provided dists with no importing RUNTIME/TEST import (informational).

**Tests** (`tests/pkg_layer/test_align.py`): a RUNTIME import with no provider → under_declared; the SAME import tagged OPTIONAL/TYPING → NOT under_declared (context scoping is the key assertion); a declared dep absent from closure → resolution_anomaly; transitive pkg → transitive_only.
**DoD**: context scoping proven (optional/typing suppressed); each diff independently asserted.

---

## Task 6 — Repair ladder (`pkg_layer/repair.py`)

Trust-ordered candidate resolution for ONE under-declared RUNTIME import. Pure logic; effects
(PyPI probe, LLM, install-verify) behind injected Protocols. **Auto-accept only single-provider;
multi-provider → flag, never guess the variant** (the DeepSeek provider-ambiguity guardrail).

- `class ProviderProbe(Protocol)`: `candidates(import_name: str) -> tuple[str,...]` (PyPI metadata probe; may be empty).
- `class LlmProbe(Protocol)`: `candidates(import_name: str) -> tuple[str,...]` (may be empty).
- `class Verifier(Protocol)`: `provides(dist: str, import_name: str) -> bool` (install + packages_distributions).
- `@dataclass(frozen=True) class RepairOutcome`: `import_name: str, resolved_dist: str|None, source: str` (`"module_name"|"curated"|"pypi"|"llm"|"flagged_missing"|"flagged_ambiguous"`).
- `repair_import(import_name, probe, llm, verifier) -> RepairOutcome`, ladder order:
  1. **module_name** (identity): candidate = the import name itself; accept iff `verifier.provides(import_name, import_name)`.
  2. **curated**: `map_import_to_package` (non-unresolved) → one candidate; accept iff verifier provides.
  3. **pypi**: `probe.candidates`; if exactly ONE verified provider → accept (`source="pypi"`); if >1 verified → `flagged_ambiguous`.
  4. **llm**: `llm.candidates`; same single-verified-provider rule; >1 → `flagged_ambiguous`.
  5. none → `flagged_missing`.
  A rung that yields >1 provider that each verify is `flagged_ambiguous` (do NOT pick a variant). First single verified provider wins and stops the ladder.

**Tests** (`tests/pkg_layer/test_repair.py`): identity import verified at rung 1; curated alias (cv2→opencv-python) at rung 2 with a fake verifier; pypi single-provider accept; **two verifying providers → flagged_ambiguous** (the psycopg2/psycopg2-binary guardrail); nothing → flagged_missing. Fakes for all three Protocols.
**DoD**: ladder order correct; single-provider-only auto-accept; ambiguity flagged not guessed; LLM/PyPI never bypass the verifier.

---

## Task 7 — Construction wiring + A/B eval (`pkg_layer/construct.py` + eval)

Wire the planes into one builder, and add the A/B eval of the NEW roots vs the CURRENT
`depgraph.roots.select_roots` on the 16-repo corpus.

- `build_package_layer(repo_dir, *, needed_extras=frozenset(), provided=()) -> tuple[PackageLayer, Alignment]` — Contract (T3) + Usage (T2) → roots (T3) → (closure skipped when no resolve source; pass `closure=()`) → PackageLayer (T1) with the injected `provided` edges → `align` (T5). Pure/no-Docker: closure + provided default empty; the builder is exercised with fakes.
- Eval `scripts/eval/graph_fidelity/pkg_layer_ab.py`: for each of the 16 corpus repos, compute NEW verifier roots = `pkg_layer.contract.select_roots(read_contract(repo), frozenset())` and CURRENT roots = `depgraph.roots.select_roots(...)`; report divergence (packages CURRENT has that NEW omits) reusing the `ab_gold_labels.AB_GOLD` classification, and the NEW alignment's `under_declared` count per repo. Emit a markdown + json report; verdict identical to Track A's shape.

**Tests** (`tests/pkg_layer/test_construct.py` + `tests/eval/graph_fidelity/test_pkg_layer_ab.py`): builder returns a PackageLayer + Alignment on a synthetic fixture; the eval's pure scoring (divergence vs gold) on a hand-built input (no clone needed).
**DoD**: builder wires all planes; eval runs pure (clone-independent test); on the 16 clones it reproduces "NEW roots ⊆ CURRENT roots, divergence = the optional-extras the current design over-adds".

---

## Out of scope (this build)

Live install/certify (Docker Environment plane), live PyPI probe, live LLM, live uv resolve on the
corpus, and any modification to the existing `depgraph/` path. Those are follow-ups; here every
external effect is behind a Protocol and faked in tests.
