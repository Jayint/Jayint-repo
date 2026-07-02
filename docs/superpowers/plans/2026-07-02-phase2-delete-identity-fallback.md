# Phase 2 — Delete the Import→Distribution Identity Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the resolver from fabricating a wrong distribution root when an import cannot be resolved — an unmapped import returns a typed *unresolved* result that every consumer skips (never a guessed root), taking wrong-guesses to zero and letting post-install Tier-1 certification (`relink`) attach the real provider.

**Architecture:** `import_mapping.map_import_to_package` currently ends in an identity fallback (`package_name := import_name`, `source="direct_name"`, `trust="low"`) that produces real-but-wrong PyPI names (`box`→literal `box`, historically `github`→defunct `github`). We make `MappingResult.package_name` nullable and add an `is_unresolved()` predicate; we first harden every downstream consumer to skip an unresolved result (each change keeps the whole suite green via monkeypatch unit tests); then we flip the fallback to return *unresolved* and update the tests whose contract genuinely changes; finally we add an honest post-relink flag for imports no provider ever covered, and certify the whole thing end-to-end on vizro.

**Tech Stack:** Python 3.11+, `pytest`, `uv` (resolver), Docker (`python:3.11-slim` for the e2e replay only). Source under `src/`, tests under `tests/`. Run everything with `PYTHONPATH=src`. Interpreter is `python3` (there is no `python`).

## Global Constraints

- **Commit LOCALLY only — NEVER push.** (Standing user directive for this branch.)
- **TDD:** failing test first → watch it fail → minimal implementation → watch it pass → commit.
- **Paper-clean, ONE path.** No flag-gated dual paths, no migration shims. Rule-over-LLM: no LLM anywhere in resolution. No repair/execute loop.
- **Never guess-and-cache.** An unresolved import produces NO root; it is never fabricated from a guessed name.
- **Ordering discipline (always-green):** Tasks 1–6 add consumer guards that are inert until Task 7 flips the fallback; the suite stays fully green after every one of those commits (their tests monkeypatch the mapper to exercise the guard). Task 7 is the single behavior flip; the suite is green again at the end of Task 7.
- **`Node.state` is the host-certification axis for PACKAGE/SYSTEM_LIB nodes only** — IMPORT nodes are never state-certified. Do NOT overload `state` for "unresolved"; use `Node.data` / `Node.evidence` (existing free-form seams).
- **Exact string vocabulary:** the new source label is the literal `"unresolved"`; the new trust label is the literal `"none"`. Reuse these verbatim everywhere.

---

### Task 1: Nullable `MappingResult` + `is_unresolved` predicate

**Files:**
- Modify: `src/python_deps/import_mapping.py` (dataclass at lines 32-37; `SOURCE_LABELS` at lines 25-29)
- Test: `tests/test_import_mapping.py`

**Interfaces:**
- Produces: `MappingResult.package_name: str | None`; module constant `UNRESOLVED_SOURCE = "unresolved"`; `is_unresolved(result: MappingResult) -> bool`; `unresolved_result(import_name: str) -> MappingResult`.
- Consumes: nothing new.

This task introduces the vocabulary ONLY. It does NOT change what `map_import_to_package` returns yet (the identity fallback still fires), so the whole suite stays green.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_import_mapping.py`:

```python
from python_deps.import_mapping import (
    MappingResult,
    UNRESOLVED_SOURCE,
    is_unresolved,
    unresolved_result,
)


def test_is_unresolved_true_for_unresolved_source():
    r = unresolved_result("somemod")
    assert r.import_name == "somemod"
    assert r.package_name is None
    assert r.source == UNRESOLVED_SOURCE
    assert r.trust == "none"
    assert is_unresolved(r) is True


def test_is_unresolved_false_for_a_real_mapping():
    r = MappingResult("yaml", "PyYAML", "collision_table", "high")
    assert is_unresolved(r) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_import_mapping.py::test_is_unresolved_true_for_unresolved_source -v`
Expected: FAIL with `ImportError: cannot import name 'UNRESOLVED_SOURCE'` (and `is_unresolved`, `unresolved_result`).

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/import_mapping.py`, change the dataclass field type and add the vocabulary. Replace the dataclass (lines 32-37):

```python
@dataclass(frozen=True)
class MappingResult:
    import_name: str
    package_name: str | None
    source: str
    trust: str


UNRESOLVED_SOURCE = "unresolved"


def is_unresolved(result: MappingResult) -> bool:
    """True when the mapper could not resolve the import to a distribution.
    Callers MUST NOT fabricate a root from an unresolved result — skip it and
    let post-install Tier-1 certification (relink) attach a real provider, or
    leave the import honestly unresolved."""
    return result.source == UNRESOLVED_SOURCE


def unresolved_result(import_name: str) -> MappingResult:
    """The canonical 'no distribution guess' result."""
    return MappingResult(
        import_name=import_name,
        package_name=None,
        source=UNRESOLVED_SOURCE,
        trust="none",
    )
```

Add to `SOURCE_LABELS` (lines 25-29) a label for the new source, keeping the dict's style:

```python
SOURCE_LABELS = {
    "collision_table": "Collision Table",
    "declared_metadata": "Jayint declared metadata extension",
    "direct_name": "Lookup Name Variants",
    "unresolved": "Unresolved (no distribution guess)",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_import_mapping.py -v`
Expected: PASS (all existing tests + the 2 new ones).

- [ ] **Step 5: Run the broader suite to confirm nothing regressed**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval -q`
Expected: 837 passed (unchanged — the fallback still returns identity, so no consumer sees `None` yet).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/import_mapping.py tests/test_import_mapping.py
git commit -m "feat(resolver): nullable MappingResult + is_unresolved predicate (Phase 2 seam)"
```

---

### Task 2: `package_roots` skips unresolved (fixes future roots.py crash)

**Files:**
- Modify: `src/python_deps/depgraph/naming.py` (`package_roots`, lines 44-53)
- Test: `tests/depgraph/test_naming.py`

**Interfaces:**
- Consumes: `is_unresolved` (Task 1).
- Produces: `package_roots` returns only *resolved* `(import_id, dist_name)` pairs — `dist_name` is never `None`. This is what makes `roots.select_roots:290-299` (which calls `normalize_package_name(dist_name)`) safe once the fallback flips.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_naming.py` (uses the existing `_graph`/`import_id` helpers in that file):

```python
def test_package_roots_omits_unresolved_import(monkeypatch):
    import python_deps.depgraph.naming as naming
    from python_deps.import_mapping import unresolved_result, MappingResult

    def fake_map(import_name, declared_package_names=None):
        if import_name == "mystery":
            return unresolved_result(import_name)
        return MappingResult(import_name, import_name, "direct_name", "low")

    monkeypatch.setattr(naming, "map_import_to_package", fake_map)
    graph = _graph("requests", "mystery")
    roots = naming.package_roots(graph)
    # requests still resolves; mystery is unresolved -> no root fabricated.
    assert (import_id("mystery"), None) not in roots
    assert all(dist is not None for _imp, dist in roots)
    assert (import_id("requests"), "requests") in roots
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_naming.py::test_package_roots_omits_unresolved_import -v`
Expected: FAIL — today `package_roots` appends `(import_id("mystery"), None)` (line 53 has no guard), so the `all(dist is not None ...)` assertion fails.

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/depgraph/naming.py`, import the predicate and guard the append. Change the loop tail (lines 52-53) from:

```python
        result = map_import_to_package(node.name, declared_package_names=declared)
        roots.append((node.id, result.package_name))
```

to:

```python
        result = map_import_to_package(node.name, declared_package_names=declared)
        if is_unresolved(result):
            continue
        roots.append((node.id, result.package_name))
```

Add the import near the existing `map_import_to_package` import at the top of the file:

```python
from python_deps.import_mapping import is_unresolved, map_import_to_package
```

(If the file currently imports `map_import_to_package` alone, extend that line; do not add a duplicate import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_naming.py -v`
Expected: PASS (existing naming tests unaffected — the fallback still returns identity, so `is_unresolved` is never true in real runs yet).

- [ ] **Step 5: Confirm the suite is still green**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval -q`
Expected: 838 passed (837 + the new test).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/naming.py tests/depgraph/test_naming.py
git commit -m "feat(roots): package_roots skips unresolved mappings (no fabricated root)"
```

---

### Task 3: `link_imports_to_packages` skips unresolved

**Files:**
- Modify: `src/python_deps/depgraph/resolve_link.py` (the import-mapping call at lines 100-101, inside `link_imports_to_packages`)
- Test: `tests/depgraph/test_resolve.py`

**Interfaces:**
- Consumes: `is_unresolved` (Task 1).
- Produces: no behavior change until Task 7; guards `_canon(None)` crash at `resolve_lock.py:81`.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_resolve.py` (match the file's existing import/graph-building idiom; use `monkeypatch` to force an unresolved mapping):

```python
def test_link_imports_skips_unresolved_mapping(monkeypatch):
    import python_deps.depgraph.resolve_link as resolve_link
    from python_deps.import_mapping import unresolved_result

    monkeypatch.setattr(
        resolve_link, "map_import_to_package",
        lambda name, *a, **k: unresolved_result(name),
    )
    # A graph with one IMPORT node and no matching PACKAGE. Reuse the file's
    # existing graph helper if present; otherwise build minimally:
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State
    from python_deps.depgraph.ids import import_id
    imp = Node(id=import_id("mystery"), type=NodeType.IMPORT, name="mystery",
               layer=Layer.NAMING, state=State.UNKNOWN)
    graph = DepGraph(nodes=(imp,), edges=())
    # Must not raise (previously: _canon(None) -> re.sub on None -> TypeError).
    out = resolve_link.link_imports_to_packages(graph)
    assert not any(e.relation.name == "REQUIRES" for e in out.edges)
```

(If `test_resolve.py` already has a `_graph(...)`/`_import_node(...)` helper, use it instead of the inline `Node` construction — read the file first and match its style.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_resolve.py::test_link_imports_skips_unresolved_mapping -v`
Expected: FAIL with `TypeError: expected string or bytes-like object, got 'NoneType'` originating at `resolve_lock.py:81` (`_canon` runs `re.sub` on the `None` dist).

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/depgraph/resolve_link.py`, guard the mapping call. The current code (lines 100-101) is:

```python
        dist = map_import_to_package(node.name).package_name
        pkg_id = canon_to_pkg.get(_canon(dist))
```

Replace with:

```python
        result = map_import_to_package(node.name)
        if is_unresolved(result):
            continue
        dist = result.package_name
        pkg_id = canon_to_pkg.get(_canon(dist))
```

Extend the existing `map_import_to_package` import at the top of the file to also import `is_unresolved`:

```python
from python_deps.import_mapping import is_unresolved, map_import_to_package
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_resolve.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm the suite is still green**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval -q`
Expected: 839 passed.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/resolve_link.py tests/depgraph/test_resolve.py
git commit -m "feat(resolve): link_imports_to_packages skips unresolved mappings"
```

---

### Task 4: Runtime discovery handles unresolved (`runtime_classify` + `runtime_ingest`)

**Files:**
- Modify: `src/python_deps/depgraph/runtime_classify.py` (mapping calls at lines 80 and 92, feeding `Discovery(name=...)`)
- Modify: `src/python_deps/depgraph/runtime_ingest.py` (`_find_existing_node`, `normalize_package_name(d.name)` at line 82)
- Test: `tests/depgraph/test_runtime_parsers.py` (classify) and `tests/depgraph/test_runtime_ingest.py` (ingest)

**Interfaces:**
- Consumes: `is_unresolved` (Task 1).
- Produces: `runtime_classify` emits `Discovery(name=None, ...)` when the import is unresolved (a real "unknown package" discovery, not a crash); `runtime_ingest._find_existing_node` returns `None` for a `None` name instead of crashing inside the blanket `try/except`.

- [ ] **Step 1: Write the failing tests**

For classify, add to `tests/depgraph/test_runtime_parsers.py`:

```python
def test_dispatch_unresolved_import_yields_none_package(monkeypatch):
    import python_deps.depgraph.runtime_classify as rc
    from python_deps.import_mapping import unresolved_result

    monkeypatch.setattr(rc, "map_import_to_package",
                        lambda name, *a, **k: unresolved_result(name))
    disc = rc.dispatch_module_not_found("mystery")   # use the real entry the file exposes
    assert disc is not None
    assert disc.name is None
```

(Read `runtime_classify.py` to use the correct function name and signature the existing `test_dispatch_module_not_found_unknown_import` calls; mirror that test's call shape.)

For ingest, add to the runtime-ingest test file:

```python
def test_find_existing_node_tolerates_none_name():
    import python_deps.depgraph.runtime_ingest as ri
    from python_deps.depgraph.schema import DepGraph, NodeType, Layer
    from python_deps.depgraph.runtime_classify import Discovery

    graph = DepGraph(nodes=(), edges=())
    # Build a Discovery with name=None using the real constructor shape
    # (see tests/depgraph/test_runtime_parsers.py:172 — node_type, name, layer,
    # evidence, check_command; add any other required fields the ctor needs):
    disc = Discovery(
        node_type=NodeType.PACKAGE, name=None, layer=Layer.PIP,
        evidence="unknown import", check_command="python -c 'import mystery'",
    )
    # Must return None cleanly, not raise (previously raised inside a blanket except).
    assert ri._find_existing_node(graph, disc) is None
```

(Read `runtime_classify.py` to confirm `Discovery`'s exact required fields/defaults and adjust the ctor call to match.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_runtime_parsers.py::test_dispatch_unresolved_import_yields_none_package "tests/depgraph/test_runtime_ingest.py::test_find_existing_node_tolerates_none_name" -v`
Expected: classify test FAILS (today `dispatch` sets `name` to the identity guess, not `None`, and has no unresolved branch); ingest test FAILS with `TypeError: ... got 'NoneType'` at `runtime_ingest.py:82`.

- [ ] **Step 3: Write minimal implementation**

In `runtime_classify.py`, at each mapping call (lines 80 and 92), guard so an unresolved result yields `name=None`:

```python
        result = map_import_to_package(import_name)
        pkg_name = None if is_unresolved(result) else result.package_name
```

(Then the existing `Discovery(name=pkg_name, ...)` construction is correct — `name` becomes `None` for unknowns.) Extend the file's `map_import_to_package` import to add `is_unresolved`.

In `runtime_ingest.py`, guard `_find_existing_node` at the top of the function body, before line 82:

```python
    if d.name is None:
        return None
    want = normalize_package_name(d.name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_runtime_parsers.py tests/depgraph/test_runtime_ingest.py -v`
Expected: PASS (existing runtime tests still pass — the fallback still returns identity, so real runs never hit the `None` branch yet).

- [ ] **Step 5: Confirm the suite is still green**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval -q`
Expected: 841 passed.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/runtime_classify.py src/python_deps/depgraph/runtime_ingest.py tests/depgraph/test_runtime_parsers.py tests/depgraph/test_runtime_ingest.py
git commit -m "feat(runtime): classify/ingest tolerate unresolved imports (name=None)"
```

---

### Task 5: `diagnose` routes a None-named discovery correctly

**Files:**
- Modify: `src/python_deps/depgraph/diagnose.py` (`_norm` at lines 60-62; the two match sites at lines 98 and 125)
- Test: `tests/depgraph/test_diagnose_router.py`

**Interfaces:**
- Consumes: nothing new (operates on `Discovery.name` which Task 4 can now set to `None`).
- Produces: a discovery whose `name is None` does NOT silently route to `Mode.ENVIRONMENT` (the current `_norm(None) -> ""` behavior, which risks infinite retry). An unnameable discovery is not a "previously-invalid attempt" — route it exactly as an external/unknown import is routed today when its name is present-but-not-in-invalid-set. **The fix must preserve the existing routing for present names** and only make the `None` case explicit.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_diagnose_router.py` (match its existing fixture/idiom):

```python
def test_none_named_discovery_does_not_route_invalid_attempt():
    # A discovery with no resolvable package name must NOT be treated as a
    # previously-invalid attempt (which requires a real name to compare).
    import python_deps.depgraph.diagnose as diagnose
    ctx = _ctx(invalid_names={"github"})          # reuse the file's context helper
    disc = _discovery(name=None)                  # reuse the file's discovery helper
    mode = diagnose.route(disc, ctx)              # use the real router entry point
    assert mode is not diagnose.Mode.INVALID_ATTEMPT
```

(Read `test_diagnose_router.py` + `diagnose.py` first to use the real router function name, `Mode` members, and the `_ctx`/`_discovery` construction the file already uses.)

- [ ] **Step 2: Run test to verify it fails or confirm current behavior**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_diagnose_router.py::test_none_named_discovery_does_not_route_invalid_attempt -v`
Expected: this may PASS or FAIL depending on current routing; the REAL regression is that `_norm(None) -> ""` compares as a name. Write the assertion to lock the correct contract, then verify Step 4 keeps ALL diagnose tests green after the guard.

- [ ] **Step 3: Write minimal implementation**

In `diagnose.py`, make `_norm` reject `None` explicitly rather than coercing to `""`. Change the two match sites (lines 98, 125) so a `None` name is handled before the `_norm` comparison. Minimal, at each site:

```python
    if disc.name is not None and _norm(disc.name) in ctx.invalid_names:
        ...
```

Leave `_norm` itself intact for present names. This makes a `None`-named discovery skip the invalid-attempt branch instead of matching the empty string.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_diagnose_router.py tests/depgraph/test_diagnose_reconciliations.py -v`
Expected: PASS (all existing diagnose tests unaffected for present names; the new None case routes correctly).

- [ ] **Step 5: Confirm the suite is still green**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval -q`
Expected: 842 passed.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/diagnose.py tests/depgraph/test_diagnose_router.py
git commit -m "feat(diagnose): a None-named discovery is not a previously-invalid attempt"
```

---

### Task 6: Evidence advisory layer handles unresolved

**Files:**
- Modify: `src/python_deps/models.py` (`ImportPackageMapping`, lines 51-59)
- Modify: `src/python_deps/depgraph/evidence.py` (`_build_import_mappings`, lines 206-219; mapping call at line 214)
- Test: `tests/depgraph/test_evidence*.py` (read to find the exact file covering `_build_import_mappings`)

**Interfaces:**
- Consumes: `is_unresolved` (Task 1).
- Produces: `_build_import_mappings` omits unresolved imports from the advisory mapping list (they carry no distribution name to advise).

- [ ] **Step 1: Write the failing test**

Add to the evidence test file (match its idiom):

```python
def test_build_import_mappings_omits_unresolved(monkeypatch):
    import python_deps.depgraph.evidence as evidence
    from python_deps.import_mapping import unresolved_result, MappingResult

    monkeypatch.setattr(
        evidence, "map_import_to_package",
        lambda name, *a, **k: unresolved_result(name) if name == "mystery"
        else MappingResult(name, name, "direct_name", "low"),
    )
    mappings = evidence._build_import_mappings(["requests", "mystery"])
    names = {m.import_name for m in mappings}
    assert "requests" in names
    assert "mystery" not in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_evidence.py::test_build_import_mappings_omits_unresolved -v`
Expected: FAIL — today `_build_import_mappings` builds an `ImportPackageMapping` for every import including the unresolved one (and would carry `package_name=None`).

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/models.py`, make `ImportPackageMapping.package_name` nullable for type-consistency (lines 51-59): change `package_name: str` to `package_name: str | None`.

In `src/python_deps/depgraph/evidence.py`, guard `_build_import_mappings` (around line 214) to skip unresolved:

```python
        result = map_import_to_package(name)
        if is_unresolved(result):
            continue
        mappings.append(
            ImportPackageMapping(
                import_name=result.import_name,
                package_name=result.package_name,
                source=result.source,
                trust=result.trust,
            )
        )
```

(Match the existing construction in that function; only add the `is_unresolved` guard and extend the import to include `is_unresolved`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_evidence.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm the suite is still green**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval -q`
Expected: 843 passed.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/models.py src/python_deps/depgraph/evidence.py tests/depgraph/test_evidence.py
git commit -m "feat(evidence): advisory import-mappings omit unresolved imports"
```

---

### Task 7: FLIP the identity fallback to unresolved (the behavior change)

**Files:**
- Modify: `src/python_deps/import_mapping.py` (identity fallback, lines 75-80)
- Modify (test contract updates): `tests/depgraph/test_naming.py`, `tests/depgraph/test_roots.py`, `tests/depgraph/test_runtime_parsers.py`, `tests/depgraph/test_diagnose_router.py`, `tests/depgraph/test_diagnose_reconciliations.py` (and any other test that asserted an identity-fabricated root/name — find them by running the full suite)

**Interfaces:**
- Consumes: `unresolved_result` (Task 1); all guards from Tasks 2–6.
- Produces: `map_import_to_package` returns `unresolved_result(top_level)` for any import not resolved by the curated table or declared metadata. This is the behavior all prior guards were built for.

- [ ] **Step 1: Write the failing test (the new contract)**

Add to `tests/test_import_mapping.py`:

```python
def test_unmapped_import_is_unresolved_not_identity():
    # An import with no curated-table entry and no declared match must NOT be
    # guessed as its own name (the old identity fallback); it is unresolved.
    r = map_import_to_package("box", declared_package_names=set())
    assert r.package_name is None
    assert r.source == "unresolved"
    assert is_unresolved(r) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_import_mapping.py::test_unmapped_import_is_unresolved_not_identity -v`
Expected: FAIL — today `map_import_to_package("box", set())` returns `MappingResult("box", "box", "direct_name", "low")`.

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/import_mapping.py`, replace the identity-fallback return (lines 75-80):

```python
    return MappingResult(
        import_name=top_level,
        package_name=top_level,
        source="direct_name",
        trust="low",
    )
```

with:

```python
    return unresolved_result(top_level)
```

- [ ] **Step 4: Run the full suite and update the behavioral-contract tests**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval tests/test_import_mapping.py -q`

Expected: the new test PASSES; a small set of tests that hard-coded the OLD identity contract now FAIL on their assertions (NOT on crashes — Tasks 2–6 removed every crash). Update each to the new contract. Known ones from the crash inventory:

- `tests/depgraph/test_naming.py::test_identity_fallback_for_unknown_import` — currently asserts `package_roots(_graph("requests")) == [(import_id("requests"), "requests")]`. Under the new contract an undeclared `requests` import is unresolved and yields NO root. Rewrite as:

```python
def test_unmapped_import_yields_no_root():
    graph = _graph("requests")           # undeclared, no curated entry
    assert package_roots(graph) == []
```

- `tests/depgraph/test_naming.py::test_returns_one_pair_per_import_in_node_order` — drop the expectation that an unmapped import produces a pair; assert only resolved imports (declared or curated) appear, in node order. Update the expected list to exclude the unmapped name.
- `tests/depgraph/test_roots.py::test_scanned_import_gap_fills_only_uncovered` — currently asserts an undeclared `boto3` import gap-fills to `"import:boto3"`. Under the new contract an undeclared+unmapped import is NOT a root. If `boto3` is meant to be covered, declare it in the fixture's `pyproject.toml`; otherwise assert it is absent: `assert "boto3" not in {dist for _imp, dist in roots}`. Choose per the test's intent (read the fixture).
- `tests/depgraph/test_runtime_parsers.py::test_dispatch_module_not_found_unknown_import` and `::test_dispatch_import_name_error_returns_package` — an unknown import now yields `Discovery(name=None, ...)`. Update the `assert d.name == "mylib"` expectations to `assert d.name is None` (or, if the import is a known curated one, keep the mapped name).
- `tests/depgraph/test_diagnose_router.py::test_previously_invalid_name_matches_normalized_form`, `::test_previously_invalid_name_routes_invalid_attempt`, and any `test_diagnose_reconciliations.py` case — these must supply a discovery with a real (curated/declared) name to exercise the invalid-attempt path, since an unmapped name is now `None`. Update the fixtures to use a name that resolves (e.g. a curated-table import) so the routing contract is still tested.

For each updated test, the change is a CONTRACT update (old behavior was "guess the name"; new behavior is "unresolved"), not a workaround — the assertion must encode the new, correct behavior. Re-run after each edit.

- [ ] **Step 5: Run the full suite to confirm green**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval tests/test_import_mapping.py -q`
Expected: all green (0 failed). If any test still fails, it is either (a) another hard-coded-identity contract to update, or (b) a genuine consumer that Tasks 2–6 missed — if (b), STOP and add a guard task for that consumer before proceeding.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/import_mapping.py tests/
git commit -m "feat(resolver): delete identity fallback — unmapped imports are unresolved, never guessed"
```

---

### Task 8: Flag imports no provider ever covered (honest post-relink signal)

**Files:**
- Modify: `src/python_deps/depgraph/relink.py` (after `certified_import_links`, add a pass that flags provider-less IMPORT nodes)
- Test: `tests/depgraph/test_relink*.py` (read to find the exact file)

**Interfaces:**
- Consumes: the graph after `certified_import_links` (Tier-1 post-install linking).
- Produces: an IMPORT node that has NO `requires` edge to any PACKAGE after relink gets `evidence` set to a human-readable "unresolved: no distribution provides import <name>" and `data={"unresolved": True}` (via the existing `Node.data` seam — NOT `Node.state`). This makes truly-undeclared imports visible without fabricating a root. An import that relink DID link (e.g. `box`→`python-box`) is NOT flagged.

- [ ] **Step 1: Write the failing test**

Add to the relink test file (match its idiom — it builds `DepGraph`s with IMPORT/PACKAGE nodes and a fake `Executor`):

```python
def test_unlinked_import_is_flagged_unresolved():
    from python_deps.depgraph.relink import flag_unresolved_imports
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, State
    from python_deps.depgraph.ids import import_id

    linked = Node(id=import_id("box"), type=NodeType.IMPORT, name="box",
                  layer=Layer.NAMING, state=State.UNKNOWN)
    unlinked = Node(id=import_id("mystery"), type=NodeType.IMPORT, name="mystery",
                    layer=Layer.NAMING, state=State.UNKNOWN)
    pkg = Node(id="pkg:python-box", type=NodeType.PACKAGE, name="python-box",
               layer=Layer.PIP, version="7.3.2", state=State.SATISFIED)
    from python_deps.depgraph.schema import Edge, EdgeType
    edge = Edge(src=import_id("box"), dst="pkg:python-box",
                relation=EdgeType.REQUIRES, origin="certified")
    graph = DepGraph(nodes=(linked, unlinked, pkg), edges=(edge,))

    out = flag_unresolved_imports(graph)
    assert out.get(import_id("mystery")).data.get("unresolved") is True
    assert out.get(import_id("box")).data.get("unresolved") is not True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_relink.py::test_unlinked_import_is_flagged_unresolved -v`
Expected: FAIL with `ImportError: cannot import name 'flag_unresolved_imports'`.

- [ ] **Step 3: Write minimal implementation**

In `src/python_deps/depgraph/relink.py`, add a pure function (returns a NEW graph — follow the module's existing immutable `with_node`/`with_edge` idiom):

```python
def flag_unresolved_imports(graph: DepGraph) -> DepGraph:
    """Mark every IMPORT node that no PACKAGE provides (no outgoing REQUIRES
    edge to a Package after Tier-1 relink) as unresolved — an honest signal the
    project under-declared, NOT a fabricated root. Uses Node.data (state is the
    host-certification axis and does not apply to imports)."""
    provided = {
        e.src for e in graph.edges
        if e.relation is EdgeType.REQUIRES
        and (dst := graph.get(e.dst)) is not None
        and dst.type is NodeType.PACKAGE
    }
    new = graph
    for node in graph.nodes:
        if node.type is not NodeType.IMPORT or node.id in provided:
            continue
        new = new.with_node(
            node.replace(
                data={**dict(node.data), "unresolved": True},
                evidence=f"unresolved: no distribution provides import {node.name}",
            )
        )
    return new
```

(Confirm the `Node` copy method name by reading `schema.py` — if it is not `.replace`, use the module's actual immutable-update helper. If `DepGraph.with_node` replaces by id, this is correct.)

Then call it at the end of `certified_import_links` so the flag reflects post-relink truth — change the final `return _drop_superseded_ghosts(new, edges)` to:

```python
    return flag_unresolved_imports(_drop_superseded_ghosts(new, edges))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph/test_relink.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm the suite is still green**

Run: `PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/relink.py tests/depgraph/test_relink.py
git commit -m "feat(relink): flag provider-less imports unresolved (honest under-declaration signal)"
```

---

### Task 9: End-to-end acceptance on vizro + resolver probe

**Files:**
- Test/verify only: `scripts/eval/graph_fidelity/coverage.py` (existing harness — no code change), a scratchpad probe script.

**Interfaces:**
- Consumes: the full Phase-2 pipeline.
- Produces: certification evidence (no repo change).

This task is verification, not implementation. It confirms the refactor achieved its goal end-to-end and recorded the honest result.

- [ ] **Step 1: Re-run the 13-case resolver probe**

Write a scratchpad script that calls `map_import_to_package` on the 13 corpus imports (`requests, numpy, yaml, cv2, PIL, bs4, sklearn, github, Crypto, dateutil, dotenv, attr, google`) with `declared_package_names=set()`.
Expected: **0 wrong guesses.** Table hits resolve correctly (`yaml, cv2, PIL, bs4, sklearn, github, Crypto`); every other import returns `source == "unresolved"` (never a wrong name). Record the exact table.

- [ ] **Step 2: Run the vizro e2e (Docker required)**

Run the `coverage.py` construction→render→fresh-replay path for `mckinsey/vizro` against the checkout at `outputs/graph_fidelity/_smoke/vizro` on `python:3.11-slim` (same entry the prior certification used: `build_graph_construction_only` → `render_build_script(graph, ())` → `run_execution_probe`).
Expected, and assert each:
- `grep -i -E '(^| )box($|[=< ])' setup.sh` → NO literal `box` install line (the wrong root is gone).
- `grep -i github setup.sh` → no match (Finding B stays closed).
- `python-box` IS present as a PACKAGE / install line (declared dependency, still installed).
- The rendered graph links `import:box → pkg:python-box` via certified relink (inspect the graph's edges).
- `install_ok == True`. If `install_ok` is still false, the remaining failure MUST be a NEW, distinct wrong-root of the same class (report its import→dist), NOT `box` or `github` — triage and record it as the next finding rather than a Phase-2 regression.

- [ ] **Step 3: Record the result in the ledger**

Append one Observation/Why/What/Verification block to `docs/superpowers/loops/graph-fidelity-LEDGER.md` with the probe table (0 wrong guesses) and the vizro e2e outcome (`install_ok`, box→python-box linkage, any residual finding).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/loops/graph-fidelity-LEDGER.md
git commit -m "docs(ledger): Phase 2 complete — identity fallback deleted, vizro e2e + 0-wrong-guess probe"
```

---

## Acceptance & Verification — Definition of Done

Run this whole checklist after all 9 tasks (it is also the final whole-branch review's gate).
Phase 2 is DONE only when checks 1–6 pass and check 7 shows no regression. Each check has an
exact command and expected result; if any fails, the refactor is not complete.

**1. Full suite green — and nothing forced.**

```bash
PYTHONPATH=src python3 -m pytest tests/depgraph tests/eval tests/test_import_mapping.py -q
```

Expected: `0 failed`. Sanity on the count: it must be ≥ the 2026-07-02 baseline (`tests/depgraph`
+ `tests/eval` = 837) plus the new tests, with the ~6 behavioral tests from Task 7 **updated**
(present and green), NOT deleted. Confirm no test was removed to force green:

```bash
git diff --stat main...HEAD -- tests/ | tail -1     # net test lines should be POSITIVE
```

**2. The identity fallback is gone (code invariant).**

```bash
grep -n 'source="direct_name"' src/python_deps/import_mapping.py && echo "FAIL: identity return still present" || echo "OK: no identity-fallback return"
PYTHONPATH=src python3 -c 'from python_deps.import_mapping import map_import_to_package as m, is_unresolved; r=m("zzz_not_a_real_pkg", set()); print(r); assert is_unresolved(r), "identity fallback still active"'
```

Expected: no `direct_name` return remains; an unknown import returns `unresolved`
(`package_name=None`), NOT the bare name `zzz_not_a_real_pkg`.

**3. Resolver probe → 0 wrong guesses (the headline metric).**

Re-run the 13-case probe (Task 9, Step 1) — a scratchpad script calling
`map_import_to_package(x, set())` on the corpus.
Expected: `yaml, cv2, PIL, bs4, sklearn, github, Crypto` → correct distribution (curated table);
`requests, numpy, dateutil, dotenv, attr, google` → `unresolved`. **Wrong guesses: 0** (the
pre-Phase-2 baseline was 6). "Correct-or-unresolved, never wrong" is the whole point of Phase 2.

**4. No fabricated root reaches a rendered artifact.**

For a repo with an undeclared, unmapped import, render its `setup.sh` and confirm there is no
bare-import install line (use the vizro artifact from check 5, or a fixture):

```bash
grep -nE 'pip install.* box($|[ =<])' setup.sh && echo "FAIL: fabricated root" || echo "OK: no literal box"
```

Expected: the only pip targets are declared / table / certified distributions — never
`pip install <import_name>` for an unmapped name.

**5. vizro end-to-end (the acceptance case) — `install_ok=True`.**

Full `coverage.py` construction→render→fresh `-slim` replay on `mckinsey/vizro` (Task 9, Step 2).
Expected, ALL true:
- `grep -i github setup.sh` → no match (Finding B stays closed).
- no literal `box` install line; `python-box` IS installed.
- the graph carries a certified edge `import:box → pkg:python-box` (edge `origin="certified"`).
- `install_ok == True`. If still false, the blocker MUST be a NEW same-class ghost (report its
  import→dist), never `box` or `github`.

**6. Honest flag works (no silent drop).**

A truly-undeclared, unmapped import — one nothing in the installed closure provides — must end up
flagged on its IMPORT node after relink (Task 8), not silently absent and not fabricated:

```python
# in a graph where "mystery" is imported but no installed package provides it:
node = out.get(import_id("mystery"))
assert node.data.get("unresolved") is True
assert node.evidence.startswith("unresolved:")
```

Expected: flagged (`data["unresolved"]` + evidence), never a `pkg:mystery` root.

**7. No collateral regression across the corpus.**

Re-run the graph-fidelity eval on the smoke set (or at least any other repo already known to
install green). Expected: no repo that installed before now fails **because a real dependency
became unresolved**. If one does, it means a genuinely-needed provider was undeclared AND unmapped
— that is correct new behavior (an honest under-declaration signal), NOT a Phase-2 bug; triage and
record it, do not "fix" it by restoring guessing.

**Definition of Done:** checks 1–6 pass; check 7 shows no regression (new honest-flags are fine;
a new *install failure on a previously-green repo* must be triaged, not papered over). Record the
final wrong-guess count (0) and the vizro `install_ok` in the ledger (Task 9, Step 3).

**Rollback safety:** every task is a separate commit, and the guards (Tasks 2–6) are inert until
Task 7. If a showstopper emerges post-merge, `git revert` the single Task-7 flip commit restores
the identity fallback while leaving the harmless guards in place — a one-commit escape hatch.

## Notes for the executor

- **Read each file before editing.** Line numbers here are from 2026-07-02; they may drift. The anchor is the quoted current code, not the number.
- **If Task 7 surfaces a failing test that is a genuine consumer crash** (NoneType) rather than a contract assertion, a guard was missed — add a Task-2-style guard task for that consumer, keep it green, then resume Task 7.
- **Docker** is only needed in Task 9. Tasks 1–8 run fully offline.
- **`box` is the litmus test:** `python-box` is declared in vizro-core's `[project.dependencies]`, so after the fallback is deleted, the wrong literal-`box` root disappears and Tier-1 relink attaches `box → python-box` from the container's real `packages_distributions()`. No Tier-2/Tier-3 work is required for vizro.
