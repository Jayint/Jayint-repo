# Import→Dist Resolution Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the install-lane candidate generator (`repair.generate_candidates`) with the finalized two-source design — a vendored pipreqs map, then an LLM fed the import's used symbols — behind the existing RECORD grounding, with no identity fallback.

**Architecture:** `generate_candidates` becomes: pipreqs-table lookup (deterministic); on a *miss*, an injected LLM guesser fed `(import_name, used_symbols)`. Both feed the unchanged `choose_provider` wheel-RECORD grounding, which stays the sole gate. The `declared`/`normalize`/`curated` rungs are removed. The LLM lives in `src/envstate` (the allowed bridge); `python_deps` stays LLM-free and receives the guesser by injection, defaulting to `None` (deterministic) until the orchestrator opts in.

**Tech Stack:** Python 3, `ast` (import + symbol scan), pytest. LLM via `src/envstate/llm_response.complete_with_retry` + `src/envstate/jsonutil.extract_json_object`, mirroring `src/envstate/llm_classifier.make_llm_classifier`.

## Global Constraints

- **`python_deps/*` stays LLM-free.** The LLM guesser is an injected `Callable[[str, tuple[str, ...]], list[str]]`; its implementation lives only in `src/envstate/`. (Mirrors the `make_llm_classifier` bridge pattern.)
- **No identity fallback.** An import name is never proposed as its own distribution name. Zero candidates ⇒ the import stays `unresolved`. (Do not port pipreqs' `data.get(pkg, pkg)` default.)
- **Grounding is the sole gate, unchanged.** `repair.choose_provider` + `repair.record_grounds` are not modified; every candidate must RECORD-confirm before ACCEPT.
- **Determinism first.** The pipreqs map is deterministic and handles the common tail with no model call; the LLM runs only on a map miss, is cached by `(import_name, sorted(symbols))`, and is called at temperature 0 (the orchestrator's `complete_fn` sets that).
- **Immutability.** All new dataclasses are `@dataclass(frozen=True)`; "mutation" returns a new object (repo rule).
- **Attribution.** The vendored `pipreqs` table is Apache-2.0 — ship a NOTICE crediting `bndr/pipreqs`.

---

## Scope — where this plugs into the existing Phase-A loop

**This plan does not build a loop. It replaces one step inside a loop that already exists.** The Phase-A resolve↔repair fixpoint (`build.py:346`, `_phase_a_fixpoint`) *is* the python-package-layer loop today; this plan changes **only step 4's candidate generation**. Steps 1, 2, 3, and 5 — resolve, install, coverage, and re-resolve-on-ACCEPT — are existing infrastructure and MUST NOT be modified by any task here.

| # | loop step | code | this plan |
|---|---|---|---|
| 1 | resolve current roots → transitive closure, install it | `install_closure` (`build.py:404`) | untouched |
| 2 | coverage = RECORD-union over the resolved closure | `resolved_record_coverage` (`build.py:408`) | untouched |
| 3 | `missing` = non-optional imports the closure doesn't cover | `build.py:409-415` | untouched |
| 4 | per missing import → propose candidates → ground → ACCEPT | `generate_candidates` → `choose_provider` (`build.py:429-432`) | **replaced — candidate *generation* only** (Tasks 1–6); `choose_provider` grounding **unchanged** |
| 5 | ACCEPT adds root → re-resolve (pulls its subtree); loop until fixpoint / bound | `build.py:450` + loop control (`:456` attempted set, `:465` bound) | untouched |

Consequences the tasks rely on:
- **The re-resolve loop (step 5) is not implemented here — the plan's output *feeds* it.** A grounded ACCEPT becomes a root; adding it and re-resolving its transitive subtree (which may cover other missing imports for free) is existing behavior.
- **Anti-oscillation is unchanged.** `generate_candidates` still returns `Candidate` objects carrying `.dist`, so the existing `attempted` set (`build.py:456`) and round bound (`build.py:465`) track the new candidates with no change.
- **The only loop edit any task makes is the call site** at `build.py:429-432` (Task 5): change the arguments passed to `generate_candidates` (add `symbols`, thread `llm`). The surrounding loop body is not touched.

**Scope boundary — install-lane only.** This is the *install-lane* half of the python-package layer ("external/undeclared import → which distribution"). The *config-lane* half — first-party import failures cured by editable-install/rootdir (the file-lane) — is a separate later unit, gated on the certificate-arbitration blocker, and is out of scope here. After this plan the fixpoint resolves external/undeclared imports via the new generator; first-party imports remain dropped at scan (old behavior) until the classifier / route-not-drop lands.

---

## File structure

- **Create** `src/python_deps/depgraph/data/pipreqs_mapping.txt` — the 1157-row `import:dist` table (vendored).
- **Create** `src/python_deps/depgraph/data/NOTICE` — Apache-2.0 attribution.
- **Create** `src/python_deps/depgraph/pipreqs_map.py` — loads the table once; `pipreqs_candidates(import_name) -> list[str]`.
- **Modify** `src/python_deps/import_graph.py` — capture used-symbols per top-level import; add `ImportFinding.symbols`.
- **Modify** `src/python_deps/depgraph/scan.py` — store `symbols` on the Import node's `data`.
- **Modify** `src/python_deps/depgraph/repair.py` — rewrite `generate_candidates`; delete `normalize_candidates`/`curated_candidates`/`declared_candidates`; add `DistGuesser` type.
- **Create** `src/envstate/llm_dist_guess.py` — `make_dist_guesser(complete_fn)` (the LLM rung).
- **Modify** `src/python_deps/depgraph/build.py` — thread `llm_dist_guesser` through `build_dep_graph` → `_phase_a_fixpoint`; pass `symbols`/`llm` at the call site.
- **Modify** `src/envstate/orchestrator.py` — construct the real guesser and inject it.
- **Modify** `tests/depgraph/test_repair_ladder.py` — drop dead-rung tests; rewrite `generate_candidates` tests.

---

## Task 1: Vendor the pipreqs table + loader

**Files:**
- Create: `src/python_deps/depgraph/data/pipreqs_mapping.txt`
- Create: `src/python_deps/depgraph/data/NOTICE`
- Create: `src/python_deps/depgraph/pipreqs_map.py`
- Test: `tests/depgraph/test_pipreqs_map.py`

**Interfaces:**
- Produces: `pipreqs_candidates(import_name: str) -> list[str]` — `[dist]` for a known top-level import name (exact-case match, mirroring pipreqs), else `[]`. Never returns the import name itself.

- [ ] **Step 1: Vendor the data file + NOTICE**

Run:
```bash
mkdir -p src/python_deps/depgraph/data
cp "/private/tmp/claude-501/-Users-john-john-v3-multi-lang/631d1f0f-1da2-481f-87dc-36381fc90c4d/scratchpad/pipreqs/pipreqs/mapping" src/python_deps/depgraph/data/pipreqs_mapping.txt
wc -l src/python_deps/depgraph/data/pipreqs_mapping.txt   # expect 1157
```
Then create `src/python_deps/depgraph/data/NOTICE`:
```
This directory vendors `pipreqs_mapping.txt`, the import-name -> distribution-name
table from bndr/pipreqs (https://github.com/bndr/pipreqs), licensed Apache-2.0.
Only the data table is used. pipreqs' `get_pkg_names` identity fallback
(`data.get(pkg, pkg)`) is deliberately NOT adopted.
```

- [ ] **Step 2: Write the failing test**

```python
# tests/depgraph/test_pipreqs_map.py
from python_deps.depgraph.pipreqs_map import pipreqs_candidates


def test_known_mismatch_maps():
    assert pipreqs_candidates("cv2") == ["opencv-python"]
    assert pipreqs_candidates("yaml") == ["pyyaml"]


def test_miss_returns_empty():
    assert pipreqs_candidates("definitely_not_a_real_import_zzz") == []


def test_never_returns_identity():
    # A miss must NOT echo the import name back (no identity fallback).
    assert pipreqs_candidates("definitely_not_a_real_import_zzz") == []


def test_dotted_import_uses_top_level():
    # cv2.something -> looked up by top-level "cv2"
    assert pipreqs_candidates("cv2.aruco") == ["opencv-python"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/depgraph/test_pipreqs_map.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'python_deps.depgraph.pipreqs_map'`

- [ ] **Step 4: Write the loader**

```python
# src/python_deps/depgraph/pipreqs_map.py
"""Vendored pipreqs import->distribution table as an untrusted candidate source.

The table (``data/pipreqs_mapping.txt``, from bndr/pipreqs, Apache-2.0) is a
best-effort community map; entries are NEVER trusted directly — every candidate
this module proposes is RECORD-grounded downstream (``repair.choose_provider``).
Only pipreqs' *table* is adopted; its ``data.get(pkg, pkg)`` identity fallback is
NOT (that is the wrong-install / self-install-false-green vector).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from python_deps.import_mapping import top_level_import_name

_MAPPING_PATH = Path(__file__).with_name("data") / "pipreqs_mapping.txt"


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    table: dict[str, str] = {}
    text = _MAPPING_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        imp, dist = line.split(":", 1)
        imp, dist = imp.strip(), dist.strip()
        if imp and dist:
            table.setdefault(imp, dist)  # first wins (deterministic)
    return table


def pipreqs_candidates(import_name: str) -> list[str]:
    """``[distribution]`` for a known top-level import name, else ``[]``.

    Exact-case match on the top-level segment (pipreqs keys are real import
    spellings, e.g. ``Crypto``/``PIL``). Never echoes the import name back.
    """
    top = top_level_import_name(import_name)
    hit = _load().get(top)
    return [hit] if hit else []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/depgraph/test_pipreqs_map.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/data/pipreqs_mapping.txt src/python_deps/depgraph/data/NOTICE src/python_deps/depgraph/pipreqs_map.py tests/depgraph/test_pipreqs_map.py
git commit -m "feat(depgraph): vendor pipreqs import->dist table as grounded candidate source"
```

---

## Task 2: Capture used-symbols per import in the scan

**Files:**
- Modify: `src/python_deps/import_graph.py` (add `_symbols_from_ast`; add `symbols` to `ImportFinding`; populate + preserve through dedupe)
- Modify: `src/python_deps/depgraph/scan.py` (store `symbols` in the Import node's `data`)
- Test: `tests/test_import_graph_symbols.py`, and extend `tests/depgraph/test_probe.py`-adjacent scan test (new `tests/depgraph/test_scan_symbols.py`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ImportFinding.symbols: tuple[str, ...]` — the attributes/from-names the code uses on that top-level import, sorted, unioned across files. And each Import node created by `scan_to_nodes` carries `data["symbols"] = (…)`.

- [ ] **Step 1: Write the failing test for symbol extraction**

```python
# tests/test_import_graph_symbols.py
from python_deps.import_graph import _symbols_from_ast


def test_attribute_access():
    src = "import cv2\ncv2.imread('x')\ncv2.VideoCapture(0)\n"
    assert _symbols_from_ast(src)["cv2"] == {"VideoCapture", "imread"}


def test_from_import_names():
    src = "from cv2 import imread, VideoCapture\n"
    assert _symbols_from_ast(src)["cv2"] == {"VideoCapture", "imread"}


def test_alias_resolved_to_top_level():
    src = "import numpy as np\nnp.array([1])\n"
    assert _symbols_from_ast(src)["numpy"] == {"array"}


def test_no_symbols_for_bare_import():
    assert _symbols_from_ast("import cv2\n") == {"cv2": set()}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_import_graph_symbols.py -v`
Expected: FAIL — `ImportError: cannot import name '_symbols_from_ast'`

- [ ] **Step 3: Add `_symbols_from_ast` to `import_graph.py`**

Add this function near the other AST helpers (after `_imports_from_ast`):
```python
def _symbols_from_ast(content: str) -> dict[str, set[str]]:
    """Map each top-level imported module -> the symbols the code uses on it.

    Feeds the install-lane LLM guesser (usage disambiguates look-alike names).
    - ``from cv2 import imread``            -> {"cv2": {"imread"}}
    - ``import cv2; cv2.VideoCapture()``    -> {"cv2": {"VideoCapture"}}
    - ``import numpy as np; np.array()``    -> {"numpy": {"array"}}  (alias resolved)
    A bare, unused import yields an entry with an empty set.
    """
    tree = ast.parse(content)
    alias_to_top: dict[str, str] = {}
    symbols: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".", 1)[0]
                local = (a.asname or a.name).split(".", 1)[0]
                alias_to_top[local] = top
                symbols.setdefault(top, set())
        elif isinstance(node, ast.ImportFrom) and node.module and (node.level or 0) == 0:
            top = node.module.split(".", 1)[0]
            entry = symbols.setdefault(top, set())
            for a in node.names:
                if a.name and a.name != "*":
                    entry.add(a.name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            top = alias_to_top.get(node.value.id)
            if top is not None:
                symbols[top].add(node.attr)

    return symbols
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_import_graph_symbols.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Add `symbols` to `ImportFinding` and populate it**

In `import_graph.py`, add the field to the `ImportFinding` dataclass (keep it last, default `()` so nothing else breaks):
```python
    symbols: tuple[str, ...] = ()
```
In `scan_imports`, accumulate symbols across files. After `imports_by_name: dict[str, set[str]] = defaultdict(set)` (line 55) add:
```python
    symbols_by_top: dict[str, set[str]] = defaultdict(set)
```
Inside the per-file loop, after the `_imports_from_ast` block (around line 88), add (guard the syntax-error path — no symbols there):
```python
        try:
            for top, syms in _symbols_from_ast(content).items():
                symbols_by_top[top] |= syms
        except SyntaxError:
            pass
```
In the finding-builder loop (lines 100-107), add the argument:
```python
                symbols=tuple(sorted(symbols_by_top.get(top_level, ()))),
```

- [ ] **Step 6: Preserve `symbols` through dedupe**

`_dedupe_findings` groups by `(import_name, classification)` and unions `source_files`. Find its grouping loop (`grouped[key].update(finding.source_files)` — approx. `import_graph.py:322`) and, alongside it, union symbols. In the block that rebuilds each `ImportFinding` from the grouped data (approx. lines 327-332), add `symbols=` from a parallel `symbols` accumulator keyed the same way. Concretely, next to the `grouped` dict add `sym_acc: dict[tuple[str, str], set[str]] = defaultdict(set)`, do `sym_acc[key].update(finding.symbols)` in the loop, and pass `symbols=tuple(sorted(sym_acc[(name, classification)]))` into the rebuilt `ImportFinding`.

- [ ] **Step 7: Write the failing scan-node test**

```python
# tests/depgraph/test_scan_symbols.py
from python_deps.depgraph.scan import scan_to_nodes
from python_deps.depgraph.schema import NodeType


def test_import_node_carries_used_symbols(tmp_path):
    (tmp_path / "app.py").write_text("import cv2\ncv2.imread('x')\n")
    graph = scan_to_nodes(str(tmp_path))
    imp = next(n for n in graph.nodes if n.type is NodeType.IMPORT and n.name == "cv2")
    assert imp.data.get("symbols") == ("imread",)
```

- [ ] **Step 8: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_scan_symbols.py -v`
Expected: FAIL — `assert None == ('imread',)`

- [ ] **Step 9: Store symbols on the Import node in `scan.py`**

In `scan.py`, `_build_import_node` (around line 122) currently builds `data = {"optional": True} if optional else {}`. Change the signature and body to thread symbols in without disturbing the existing `optional` tag:
```python
def _build_import_node(
    name: str, source_files: tuple[str, ...], *,
    optional: bool = False, symbols: tuple[str, ...] = (),
) -> Node:
    provenance = ", ".join(source_files) if source_files else None
    data: dict = {}
    if optional:
        data["optional"] = True
    if symbols:
        data["symbols"] = symbols
    return Node(
        id=import_id(name),
        type=NodeType.IMPORT,
        name=name,
        layer=Layer.NAMING,
        discovered_by=DiscoveredBy.STATIC_SCAN,
        state=State.UNKNOWN,
        check_command=_import_check_command(name),
        provenance=provenance,
        data=data,
    )
```
At the call site in `scan_to_nodes` (around line 171), pass `symbols=finding.symbols`:
```python
        graph = graph.with_node(
            _build_import_node(
                finding.import_name, provenance_files,
                optional=finding.optional, symbols=finding.symbols,
            )
        )
```

- [ ] **Step 10: Run both test files + the existing scan/import-graph suites**

Run: `python -m pytest tests/depgraph/test_scan_symbols.py tests/test_import_graph_symbols.py tests/depgraph/test_probe.py -q`
Expected: PASS (new tests green; no regression in probe/scan). Also run `python -m pytest tests/test_import_mapping.py -q` to be safe.

- [ ] **Step 11: Commit**

```bash
git add src/python_deps/import_graph.py src/python_deps/depgraph/scan.py tests/test_import_graph_symbols.py tests/depgraph/test_scan_symbols.py
git commit -m "feat(scan): capture used-symbols per import for the install-lane guesser"
```

---

## Task 3: Rewrite `generate_candidates` (pipreqs → LLM), delete dead rungs

**Files:**
- Modify: `src/python_deps/depgraph/repair.py`
- Modify: `tests/depgraph/test_repair_ladder.py`

**Interfaces:**
- Consumes: `pipreqs_candidates` (Task 1).
- Produces:
  - `DistGuesser = Callable[[str, tuple[str, ...]], list[str]]` (module-level type alias in `repair.py`).
  - `generate_candidates(import_name: str, *, symbols: tuple[str, ...] = (), llm: DistGuesser | None = None) -> list[Candidate]`.
  - `Candidate.source` values are now `"pipreqs"` | `"llm"`.
  - `choose_provider`, `record_grounds`, `RepairDecision`, `Verdict` are **unchanged**.

- [ ] **Step 1: Rewrite the failing tests**

Replace the rung-specific tests (`normalize_candidates`, `curated_candidates`, `declared_candidates` imports and their test functions) and the old `generate_candidates` tests in `tests/depgraph/test_repair_ladder.py`. New import line and tests:
```python
from python_deps.depgraph.repair import Candidate, generate_candidates


def test_generate_pipreqs_hit():
    cands = generate_candidates("cv2")
    assert [(c.dist, c.source) for c in cands] == [("opencv-python", "pipreqs")]


def test_generate_llm_only_on_map_miss():
    seen = {}
    def llm(name, symbols):
        seen["called"] = (name, symbols)
        return ["some-dist"]
    # "cv2" is a pipreqs hit -> llm must NOT be called
    generate_candidates("cv2", symbols=("imread",), llm=llm)
    assert "called" not in seen
    # a miss -> llm IS called, fed the symbols
    cands = generate_candidates("zzz_unknown", symbols=("frobnicate",), llm=llm)
    assert seen["called"] == ("zzz_unknown", ("frobnicate",))
    assert [(c.dist, c.source) for c in cands] == [("some-dist", "llm")]


def test_generate_no_llm_no_identity_fallback():
    # miss + no llm -> empty; NEVER the import name itself
    assert generate_candidates("zzz_unknown", llm=None) == []


def test_generate_canon_dedupes():
    def llm(name, symbols):
        return ["Foo-Bar", "foo_bar"]  # same canonical dist, two spellings
    cands = generate_candidates("zzz_unknown", llm=llm)
    assert len(cands) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/depgraph/test_repair_ladder.py -v`
Expected: FAIL (old symbols removed / new behavior absent).

- [ ] **Step 3: Rewrite `generate_candidates` and delete the dead rungs**

In `repair.py`: delete `normalize_candidates`, `curated_candidates`, `declared_candidates` and the now-unused imports (`declared_metadata_match`, `CURATED_IMPORT_TO_PACKAGE`, `DECLARED_SOURCE`, the `re` import if unused). Add the pipreqs import and the type alias, and replace `generate_candidates`:
```python
from python_deps.depgraph.pipreqs_map import pipreqs_candidates

DistGuesser = Callable[[str, tuple[str, ...]], list[str]]


def generate_candidates(
    import_name: str,
    *,
    symbols: tuple[str, ...] = (),
    llm: DistGuesser | None = None,
) -> list[Candidate]:
    """Ordered candidates for an unresolved import: pipreqs map, else LLM.

    Deterministic step 1 is the vendored pipreqs table; on a *miss* only, an
    injected ``llm`` guesser fed ``(import_name, symbols)`` proposes. No other
    source, and NEVER the import name itself (no identity fallback). Every
    candidate is still RECORD-grounded by ``choose_provider`` — this function
    only proposes. Canon-deduped, first spelling wins.
    """
    top = top_level_import_name(import_name)
    hits = pipreqs_candidates(top)
    if hits:
        raw = [Candidate(dist, "pipreqs") for dist in hits]
    elif llm is not None:
        raw = [Candidate(dist, "llm") for dist in llm(top, symbols)]
    else:
        raw = []

    seen: set[str] = set()
    out: list[Candidate] = []
    for candidate in raw:
        key = normalize_package_name(candidate.dist)
        if key and key not in seen:
            seen.add(key)
            out.append(candidate)
    return out
```
Update the `Candidate.source` docstring comment to `# "pipreqs" | "llm"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/depgraph/test_repair_ladder.py -v`
Expected: PASS. Then run the grounding/decision tests that must be untouched: `python -m pytest tests/depgraph/test_repair_grounding.py -q` (these exercise `choose_provider`/`record_grounds`; they must stay green). If any `test_repair_grounding.py` case imports a now-deleted symbol, update only that import — do not change grounding behavior.

- [ ] **Step 5: Verify the deleted rungs are not referenced elsewhere**

Run:
```bash
grep -rn "normalize_candidates\|curated_candidates\|declared_candidates\|DECLARED_SOURCE" src/ tests/
```
Expected: no hits in `src/` outside comments; only removed test references. (`map_import_to_package`, `declared_metadata_match`, `CURATED_IMPORT_TO_PACKAGE` MUST still appear — they live in `import_mapping.py` and are used by `evidence.py`/`integrate.py`/`runtime_classify.py`/`pkg_layer/`; do not touch those.)

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/repair.py tests/depgraph/test_repair_ladder.py
git commit -m "feat(depgraph): generate_candidates = pipreqs map -> LLM(usage); drop declared/normalize/curated rungs"
```

---

## Task 4: The LLM dist-guesser (envstate bridge)

**Files:**
- Create: `src/envstate/llm_dist_guess.py`
- Test: `tests/envstate/test_llm_dist_guess.py`

**Interfaces:**
- Consumes: `repair.DistGuesser` shape (Task 3).
- Produces: `make_dist_guesser(complete_fn: Callable[[list[dict]], str], *, cache: dict | None = None) -> DistGuesser` — a callable `(import_name, symbols) -> list[str]`, cached by `(import_name, sorted(symbols))`, returning `[]` on unknown/malformed.

- [ ] **Step 1: Write the failing test**

```python
# tests/envstate/test_llm_dist_guess.py
from src.envstate.llm_dist_guess import make_dist_guesser


def test_parses_distributions_from_json():
    def complete_fn(messages):
        return '{"distributions": ["opencv-python"]}'
    guess = make_dist_guesser(complete_fn)
    assert guess("cv2", ("imread",)) == ["opencv-python"]


def test_symbols_are_in_the_prompt():
    captured = {}
    def complete_fn(messages):
        captured["user"] = messages[-1]["content"]
        return '{"distributions": []}'
    make_dist_guesser(complete_fn)("cv2", ("imread", "VideoCapture"))
    assert "imread" in captured["user"] and "VideoCapture" in captured["user"]


def test_cache_avoids_second_call():
    calls = {"n": 0}
    def complete_fn(messages):
        calls["n"] += 1
        return '{"distributions": ["x"]}'
    guess = make_dist_guesser(complete_fn)
    guess("cv2", ("imread",))
    guess("cv2", ("imread",))
    assert calls["n"] == 1


def test_malformed_response_returns_empty():
    guess = make_dist_guesser(lambda m: "not json at all")
    assert guess("cv2", ()) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/envstate/test_llm_dist_guess.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the guesser**

```python
# src/envstate/llm_dist_guess.py
"""LLM import->distribution guesser — the install-lane candidate rung.

Injectable, network-free factory mirroring ``llm_classifier.make_llm_classifier``:
``make_dist_guesser(complete_fn)`` returns a ``(import_name, symbols) -> list[str]``
callable matching ``repair.DistGuesser``. The pure ``python_deps`` repair ladder
stays LLM-free; this src.envstate module is the allowed bridge. Every returned
name is still RECORD-grounded downstream — hallucinations are denied there.
"""
from __future__ import annotations

from collections.abc import Callable

from src.envstate.jsonutil import extract_json_object

_SYSTEM_PROMPT = (
    "You map a Python import to the PyPI distribution(s) that provide it. You are "
    "given the import's top-level name and the attributes/functions the code uses on "
    "it — the usage disambiguates look-alike names. Respond with ONLY a JSON object "
    '{"distributions": [names...]}: real PyPI distribution names, most-likely first, '
    "or an empty list if you do not know. Do NOT return the import name itself unless "
    "you are certain it is the real PyPI distribution name."
)


def _build_messages(import_name: str, symbols: tuple[str, ...]) -> list[dict]:
    used = ", ".join(sorted(symbols)) or "(none observed)"
    user = (
        f"import: {import_name}\nsymbols used: {used}\n\n"
        "Respond with ONLY the JSON object."
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def make_dist_guesser(
    complete_fn: Callable[[list[dict]], str],
    *,
    cache: dict[tuple[str, tuple[str, ...]], list[str]] | None = None,
) -> Callable[[str, tuple[str, ...]], list[str]]:
    store = cache if cache is not None else {}

    def guess(import_name: str, symbols: tuple[str, ...]) -> list[str]:
        key = (import_name, tuple(sorted(symbols)))
        if key in store:
            return store[key]
        try:
            raw = complete_fn(_build_messages(import_name, symbols))
        except Exception:
            store[key] = []
            return []
        obj = extract_json_object(raw) or {}
        dists = obj.get("distributions") if isinstance(obj, dict) else None
        out = (
            [d.strip() for d in dists if isinstance(d, str) and d.strip()]
            if isinstance(dists, list)
            else []
        )
        store[key] = out
        return out

    return guess
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/envstate/test_llm_dist_guess.py -v`
Expected: PASS (4 tests). (Create `tests/envstate/__init__.py` if the directory needs it — check whether sibling `tests/envstate/*` files already exist first with `ls tests/envstate/`.)

- [ ] **Step 5: Commit**

```bash
git add src/envstate/llm_dist_guess.py tests/envstate/test_llm_dist_guess.py
git commit -m "feat(envstate): LLM import->dist guesser fed usage symbols (grounded downstream)"
```

---

## Task 5: Thread the guesser through `build_dep_graph` → fixpoint

**Files:**
- Modify: `src/python_deps/depgraph/build.py`
- Test: `tests/depgraph/test_build.py` (add one case)

**Interfaces:**
- Consumes: `generate_candidates(..., symbols=, llm=)` (Task 3), `repair.DistGuesser`.
- Produces: `build_dep_graph(..., llm_dist_guesser: DistGuesser | None = None)` — default `None` keeps today's deterministic behavior; when passed, the fixpoint uses it on map misses, fed each Import's `data["symbols"]`.

- [ ] **Step 1: Write the failing integration test**

```python
# add to tests/depgraph/test_build.py
def test_fixpoint_uses_pipreqs_candidate_via_grounding(monkeypatch):
    """An unresolved import whose pipreqs dist RECORD-confirms is accepted as a root."""
    from python_deps.depgraph import build as build_mod

    # stub record provider: opencv-python's wheel provides top-level "cv2"
    def fake_provider(dist):
        return {"cv2"} if dist == "opencv-python" else set()

    imp = build_mod.Node(  # a bare unresolved external import
        id="import:cv2", type=build_mod.NodeType.IMPORT, name="cv2",
        layer=build_mod.Layer.NAMING, discovered_by=build_mod.DiscoveredBy.STATIC_SCAN,
    )
    graph = build_mod.DepGraph(nodes=(imp,))
    candidates = build_mod.generate_candidates("cv2")
    decision = build_mod.choose_provider("cv2", candidates, fake_provider)
    assert decision.verdict is build_mod.Verdict.ACCEPT
    assert decision.dist == "opencv-python"
```
(This asserts the pipreqs→grounding path end-to-end at the functions the fixpoint calls; the `llm_dist_guesser=None` default is exercised implicitly.)

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/depgraph/test_build.py::test_fixpoint_uses_pipreqs_candidate_via_grounding -v`
Expected: FAIL — `generate_candidates`/`choose_provider` not exported from `build`, or ACCEPT not reached because pipreqs/repair not wired. (If it already passes because names are importable, proceed — the wiring below is still required for the live path.)

- [ ] **Step 3: Add the `llm` parameter to `_phase_a_fixpoint`**

In `build.py`, `_phase_a_fixpoint` signature (line ~346), add a keyword-only param:
```python
    llm: "repair.DistGuesser | None" = None,
```
At the candidate call site (lines 429-430), replace:
```python
            candidates = generate_candidates(
                imp.name, declared_package_names=declared_package_names, llm=None
            )
```
with:
```python
            candidates = generate_candidates(
                imp.name, symbols=tuple(imp.data.get("symbols", ())), llm=llm
            )
```
(`declared_package_names` stays a fixpoint param for other uses; it is simply no longer passed to `generate_candidates`.)

- [ ] **Step 4: Thread it from `build_dep_graph` down**

In `build.py`, add `llm_dist_guesser: "repair.DistGuesser | None" = None` to `build_dep_graph` (line ~1030) and to `_python_package_obligations` (line ~711). At the `_phase_a_fixpoint(...)` call (line ~920) pass `llm=llm_dist_guesser`, and at the `_python_package_obligations(...)` call pass `llm_dist_guesser=llm_dist_guesser`. Ensure `repair` is imported in `build.py` for the type reference (it already imports `choose_provider`/`generate_candidates` from `repair`; add `from python_deps.depgraph import repair` or reference the alias via the existing import).

- [ ] **Step 5: Run the build test + full build/repair suites**

Run:
```bash
python -m pytest tests/depgraph/test_build.py tests/depgraph/test_repair_ladder.py tests/depgraph/test_repair_grounding.py tests/depgraph/test_phase_a_fixpoint.py -q
```
Expected: PASS. The default `None` path must leave existing `test_phase_a_fixpoint.py` behavior unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_build.py
git commit -m "feat(depgraph): thread injectable dist-guesser + used-symbols into Phase-A fixpoint"
```

---

## Task 6: Inject the real guesser at the orchestrator

**Files:**
- Modify: `src/envstate/orchestrator.py`
- Test: manual smoke (LLM-backed; not unit-tested here — the pieces are covered by Tasks 4-5)

**Interfaces:**
- Consumes: `make_dist_guesser` (Task 4), `build_dep_graph(..., llm_dist_guesser=)` (Task 5), `complete_with_retry` (existing).

- [ ] **Step 1: Build the guesser next to the existing LLM injections**

`orchestrator.py` already imports `make_llm_classifier` and `complete_with_retry` (lines ~975-976). At the same layer, construct the dist-guesser. Add the import:
```python
from src.envstate.llm_dist_guess import make_dist_guesser
```
Where the orchestrator holds its LLM `client`/`model` (the same values it passes to `complete_with_retry` for the classifier), build a `complete_fn` closure and the guesser:
```python
def _dist_complete_fn(messages):
    text, _usage, _parsed = complete_with_retry(
        client, model, messages, max_attempts=2,
    )
    return text

dist_guesser = make_dist_guesser(_dist_complete_fn)
```

- [ ] **Step 2: Pass it into the build**

At the orchestrator's `build_dep_graph(...)` call site, add `llm_dist_guesser=dist_guesser`. (If the orchestrator calls the build indirectly, thread `dist_guesser` to that call; `build_dep_graph`'s new kwarg is the single injection point.)

- [ ] **Step 3: Smoke-check import + wiring**

Run:
```bash
python -c "import src.envstate.orchestrator"
python -m pytest tests/depgraph -q
```
Expected: import clean; full depgraph suite green.

- [ ] **Step 4: Commit**

```bash
git add src/envstate/orchestrator.py
git commit -m "feat(envstate): inject the LLM dist-guesser into the build (live install-lane tail)"
```

---

## Self-review notes (checked against the spec's install-lane section)

- **Two sources only, pipreqs then LLM-on-miss** — Task 3 `generate_candidates`. ✅
- **No identity fallback** — Task 1 (`pipreqs_candidates` never echoes the name), Task 3 (`raw=[]` on a no-llm miss), tested `test_generate_no_llm_no_identity_fallback`. ✅
- **Grounding is the sole gate, unchanged** — Tasks 3/5 leave `choose_provider`/`record_grounds` untouched; Task 5 test drives ACCEPT through grounding. ✅
- **Usage symbols feed the LLM** — Task 2 captures them onto the node; Task 5 passes `imp.data["symbols"]`; Task 4 puts them in the prompt (`test_symbols_are_in_the_prompt`). ✅
- **`python_deps` stays LLM-free** — the guesser is injected; its impl is only in `src/envstate/llm_dist_guess.py`. ✅
- **Determinism / cache** — pipreqs is deterministic; LLM only on miss, cached (`test_cache_avoids_second_call`). ✅
- **Apache-2.0 attribution** — Task 1 NOTICE. ✅
- **Dead code** — Task 3 deletes only the repair-ladder rungs; Step 5 verifies `map_import_to_package`/`declared_metadata_match`/`CURATED_IMPORT_TO_PACKAGE` survive (used elsewhere). `naming.py`, `local_module_names`, `_import_edges` untouched. ✅
- **Not in this plan (deliberate):** the internal/external classifier, route-not-drop, and file-lane (gated on the certificate-arbitration blocker) — this pipeline slots into the *current* fixpoint, where first-party imports are still dropped at scan, so the LLM rung only ever sees external residue. Escalating to the LLM when a *pipreqs* candidate grounds to DENY (vs. only on a lookup miss) is a possible future refinement, out of scope here.
