# Multi-Language Ecosystem Seam — Slice 1 (Seam + Python Wrap) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce an `EcosystemProvider` seam ABOVE `python_deps` and relocate the Python construction path behind a `PythonProvider` wrapper, with **byte-identical Python output before vs after** as the hard, non-negotiable acceptance bar (proven by the hermetic zero-impact gates — oracle (a) suite subset, oracle (b) A/B, a whole-graph `to_dict()` diff — plus the package-layer fidelity EVALUATION as the FINAL baseline-Python no-regression capstone). Rust/Node are NOT built here — this slice lands only the seam that will later admit them.

**Architecture:** A new neutral `src/ecosystems/` layer (`base.py` = two-axis Protocol + enums; `registry.py` = `select_provider` dispatch; `python/provider.py` = a pass-through `PythonProvider`). `build_dep_graph` is refactored into a dispatch shell that calls `select_provider → provider.package_obligations → provider.native_obligations → shared tail`. The Phase-1 body (`build.py:488-608`) and Phase-2 body (`build.py:610-634`) are extracted VERBATIM into module-level helpers in `build.py`; `PythonProvider` delegates to them. Exactly one field (`Node.ecosystem`, default `"python"`, omit-if-default in `to_dict`) is added to the shared schema. The Phase-1/2 engine itself does not change — the seam parameterizes only *what populates each phase*.

**Tech Stack:** Python 3.10+ (`enum`, `typing.Protocol`, frozen dataclasses), pytest. Delegation-only; no new runtime deps. Oracle (a)+(b) are hermetic host-only (no network); oracle (c) is Docker+network (controller-run).

## Global Constraints

These are the binding rules from the spec's Zero-impact strategy and `research_zero_impact.md` §3. They are copied VERBATIM because a violation of any one falsifies the slice.

- **DELEGATE, don't rewrite.** `PythonProvider.package_obligations`/`native_obligations` call helpers that run the existing `build.py` regions **verbatim**. Prefer a mechanical cut (extract `build.py:488-608` and `610-634` into module-level helpers, call them) so "no behavior change" is trivially true and `git diff` reads as "move + call." **The cut is LITERAL, including every inline stage comment** — do NOT paraphrase or collapse the `610-617`/`619-623`/`625-627`/`636-638` block comments. The plan's own code snippets below are *illustrative* and deliberately abbreviate those comments; the implementer copies the real `build.py` lines verbatim via Read+Edit on the live file and applies ONLY the single FIX-1 one-line change to the probe-restamp comprehension (drop the snapshot-exclusion clause — see Task 4). Any other textual delta — even an "inert" comment reword — violates the verbatim-cut rule and must be reverted.
- **No function bodies edited.** If `git diff` shows any edit inside `_phase_a_fixpoint`, `_stamp_audit`, `_restamp`, `reconcile_packages`, `certified_import_links`, `flag_*`, `resolved_record_coverage`, or the record-provider factories → it is a rewrite, stop. Same functions, same order, same args — the stage sequence stays exactly `build.py:490-642`.
- **Preserve module-level symbol identity** patched by tests/conftest: `build.pypi_record_provider`, `build.composite_record_provider`, `coverage._default_wheel_top_levels`, `coverage.pypi_record_provider` (its `__kwdefaults__['fetch']`), `relink.PACKAGES_DIST_CMD`. If `PythonProvider` re-imports these, the autouse `_no_pypi_network` stub must still target the SAME objects. **Do NOT touch `build.py`'s module-level import block.**
- **Keep the composite record-provider default constructed at `build.py:569-571`** inside the moved Phase-1 region. Do NOT hoist it into a provider method — the conftest autouse stub patches the def-time `pypi_record_provider.__kwdefaults__['fetch']`; if the composite is built anywhere else the stub goes inert (INV-8 hermeticity).
- **`to_dict` omit-if-default rule.** `Node.to_dict()` must emit `ecosystem` **only when `self.ecosystem != "python"`**. Always-emit injects `"ecosystem":"python"` into every existing node and fails oracle (c). Python nodes serialize byte-identically.
- **Compute `exclude_newer` exactly once** (INV-1); never re-derive inside the provider. **Keep `target_env` an object** (INV-12); never rebuild from strings. **Do not unify the two `packages_distributions` reads** (INV-3). **Do not reorder/merge the stamp passes** (INV-2/7); keep `pre_resolve_ids` + ALL of `590-608` (aux stages + resolver restamp) inside `package_obligations`. **No LLM in the core** (INV-15).
- **Signature stability.** `build_dep_graph`'s keyword-only params (`host_executor`, `target_python`, `target_platform`, `exclude_newer`, `needed_extras`, `record_provider`) must be surfaced unchanged by the seam so existing callers keep working without edits (`research_zero_impact.md` §3, final bullet — it lists `record_provider`). **This is why `record_provider` is threaded through `package_obligations` too**, even though the spec's illustrative interface snippet elided it (reconciled in Task 6 / Self-Review).
- **The 15-invariant checklist is the attention lens.** INV-1..INV-15 (`research_zero_impact.md` §1) must all still hold. Each move/extract task re-runs the targeted invariant guards (`test_build_phase_order.py` INV-9/4, `test_phase_a_fixpoint.py` INV-4, `test_relink.py` INV-5, `test_record_provider.py` INV-3, `test_schema_audit.py` INV-8/2, `test_pins.py` INV-1, `test_roots.py` INV-11/12, `test_build.py` end-to-end).
- **Carry the pre-existing `roots.py:206-212` HOST-stdlib target-honesty bug UNCHANGED** — the seam neither fixes nor worsens it.
- **Oracle (a) byte-identity is a SUBSET-identity check over the FROZEN baseline** (Task 1 captures the `-rA` node-id/status list). Every one of the pre-existing **1111** node-ids must reproduce its EXACT status after each change. New TDD tests added by this slice are additive and separately green; they are naturally excluded from the baseline comparison. The literal collected count grows by the new test files (Tasks 2, 3, 6, 7) — that is expected and non-violating. **This is a deliberate, spec-consistent reinterpretation** of the spec's "1111 collected" wording: because the SDD itself mandates new RED-first tests, a frozen-at-1111 *gross* count is unsatisfiable by construction, so the binding invariant is the FROZEN-BASELINE SUBSET reproducing identically (enforced by `comm -23`), not the gross count.
- **Commit-local only. NEVER push.** Standing constraint for this branch.
- **Temp/scratch files** go under `/Users/john/.claude/jobs/366037cb/tmp` (the job tmp), never `/tmp`.

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `src/ecosystems/__init__.py` | new neutral package marker | 3 |
| `src/ecosystems/base.py` | `ClosureMode`, `CertifyMode`, `EcosystemProvider` Protocol | 3 |
| `src/ecosystems/registry.py` | `select_provider(repo, providers, *, threshold=0.5)`; `PROVIDERS` tuple (added Task 7) | 3, 7 |
| `src/ecosystems/python/__init__.py` | package marker | 6 |
| `src/ecosystems/python/provider.py` | `PythonProvider` — DELEGATES into `build.py` helpers | 6 |
| `src/python_deps/depgraph/schema.py` | add `Node.ecosystem` field (`158-165` block) + conditional `to_dict` (`206-234`) | 2 |
| `src/python_deps/depgraph/build.py` | extract `_python_package_obligations` (`488-608`) + `_python_native_obligations` (`610-634`); `build_dep_graph` → dispatch shell | 4, 5, 7 |
| `tests/depgraph/test_schema_ecosystem.py` | schema field + byte-identity `to_dict` | 2 |
| `tests/ecosystems/conftest.py` + `test_base.py` + `test_registry.py` | neutral-layer unit tests (own src-shim) | 3 |
| `tests/depgraph/test_python_provider.py` | provider delegation + INV-8 symbol identity (inherits `_no_pypi_network`) | 6 |

---

## Task 1: Freeze the byte-identity baselines

No product change. Capture the byte-identity references the whole slice is measured against, at the pre-extraction HEAD. The "test" is that the references are captured and reproducible.

**Files:** none modified. Writes references under `/Users/john/.claude/jobs/366037cb/tmp/`.

**Interfaces:**
- Produces: `tmp/oracle_a_baseline_rA.txt` (per-test status list), `tmp/oracle_a_baseline_summary.txt`, `tmp/oracle_b_baseline.sha256`, `tmp/ours_v2_frozen/` (copy) + `tmp/oracle_c_baseline.sha256`, `tmp/baseline_head.txt`, `tmp/fullnode_baseline.json` (frozen whole-graph `to_dict()` of a deterministic construction fixture — the full-node-schema oracle for Task 8 Step 3).

- [ ] **Step 1: Record the exact commit + baseline collected count**

```bash
cd /Users/john/john-planner-v3-core-autoresearch
mkdir -p /Users/john/.claude/jobs/366037cb/tmp
git rev-parse HEAD > /Users/john/.claude/jobs/366037cb/tmp/baseline_head.txt
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval --collect-only -q 2>/dev/null | tail -1
# expect: "1111 tests collected"
```

- [ ] **Step 2: Freeze oracle (a) — construction suites pass/skip partition (hermetic)**

```bash
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_a_baseline_summary.txt 2>&1
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval --tb=no -rA -q \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_a_baseline_rA.txt 2>&1
# the node-id/status lines that the subset gate compares:
grep -E '^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) ' \
  /Users/john/.claude/jobs/366037cb/tmp/oracle_a_baseline_rA.txt | sort \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_a_baseline_status.txt
wc -l /Users/john/.claude/jobs/366037cb/tmp/oracle_a_baseline_status.txt
```

- [ ] **Step 3: Freeze oracle (b) — committed A/B JSON baselines**

```bash
shasum -a 256 \
  outputs/graph_fidelity/root_selection_ab.json \
  outputs/graph_fidelity/pkg_layer_ab.json \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_b_baseline.sha256
cat /Users/john/.claude/jobs/366037cb/tmp/oracle_b_baseline.sha256
```

- [ ] **Step 4: Freeze oracle (c) — `ours_v2` closures (the live baseline, immediately before extraction)**

```bash
cp -r outputs/graph_fidelity/pkg_lock_ab/ours_v2 \
  /Users/john/.claude/jobs/366037cb/tmp/ours_v2_frozen
shasum -a 256 /Users/john/.claude/jobs/366037cb/tmp/ours_v2_frozen/*.json \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_c_baseline.sha256
ls /Users/john/.claude/jobs/366037cb/tmp/ours_v2_frozen/*.json | wc -l   # expect 15
```

- [ ] **Step 5: Define the reusable oracle-(a) subset gate** (used verbatim by Tasks 2,4,5,7,8)

The gate asserts every frozen baseline node-id reproduces its EXACT status; new tests are ignored.

```bash
# --- oracle (a) subset gate ---
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval --tb=no -rA -q \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_a_after_rA.txt 2>&1
grep -E '^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) ' \
  /Users/john/.claude/jobs/366037cb/tmp/oracle_a_after_rA.txt | sort \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_a_after_status.txt
# baseline lines NOT reproduced identically in "after" -> MUST print nothing:
comm -23 /Users/john/.claude/jobs/366037cb/tmp/oracle_a_baseline_status.txt \
         /Users/john/.claude/jobs/366037cb/tmp/oracle_a_after_status.txt
```
Expected: empty output (no baseline test changed status). No commit (baselines are gitignored/tmp).

- [ ] **Step 6: Freeze the full-node `to_dict()` fixture baseline** (the whole-graph schema oracle for Task 8 Step 3)

The three oracles above are proxies — none serializes the FULL node schema (`discovered_cycle`, `certified_cycle`, `check_command`, `attempts`, `provenance`, `phase`, `strength`, `cycles`, …). Freeze a byte-exact whole-graph serialization of a small deterministic fixture so Task 8 can diff the entire node schema. Reuse `tests/depgraph/test_build.py`'s FakeExecutor fixture — it drives the psycopg2/pg_config-gap Phase-A **PROBE** path (the exact node FIX-1 protects), so this baseline catches the `discovered_cycle` regression class the other three oracles miss.

```bash
cd /Users/john/john-planner-v3-core-autoresearch
python3 - <<'PY' > /Users/john/.claude/jobs/366037cb/tmp/fullnode_baseline.json
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests/depgraph")               # so test_build + conftest.FakeExecutor import
from test_build import _build                       # deterministic fake-executor fixture
with tempfile.TemporaryDirectory() as d:
    graph = _build(Path(d))                         # HOST-side resolve + container-side probe/certify, all faked
print(json.dumps(graph.to_dict(), sort_keys=True, indent=2))
PY
wc -l /Users/john/.claude/jobs/366037cb/tmp/fullnode_baseline.json   # non-empty; frozen at pre-change HEAD
```
Captured at the pre-extraction HEAD; every product task's structural move must reproduce it byte-for-byte (asserted in Task 8 Step 3).

---

## Task 2: `Node.ecosystem` field + conditional `to_dict` (RED first)

Add the single routing/composition field to the shared schema, placed in the documented default-safe enrichment block, and make `to_dict` omit-if-default so Python nodes serialize byte-identically. This is the FIRST product change and its byte-identity test is RED-first.

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (add field after `exclude_newer`, line 165; convert `to_dict` `return {...}` → `out = {...}` + conditional, lines 206-234)
- Test: `tests/depgraph/test_schema_ecosystem.py` (new; lives under `tests/depgraph/` — src on path, near `test_schema_audit.py`)

**Interfaces:**
- Produces: `Node.ecosystem: str = "python"`; `Node.to_dict()` omits the key for Python nodes, emits it for others. Key set for a Python node is byte-identical to the pre-change set.

- [ ] **Step 1: Write the failing tests**

Create `tests/depgraph/test_schema_ecosystem.py`:

```python
from python_deps.depgraph.schema import DiscoveredBy, Layer, Node, NodeType


def _node(**kw):
    base = dict(
        id="pkg:demo==1.0",
        type=NodeType.PACKAGE,
        name="demo",
        layer=Layer.PACKAGES,
        discovered_by=DiscoveredBy.RESOLVER,
    )
    base.update(kw)
    return Node(**base)


def test_ecosystem_defaults_to_python():
    assert _node().ecosystem == "python"


def test_to_dict_omits_ecosystem_for_python_nodes():
    assert "ecosystem" not in _node().to_dict()


def test_to_dict_emits_ecosystem_for_non_python_nodes():
    assert _node(ecosystem="rust").to_dict()["ecosystem"] == "rust"


def test_to_dict_key_set_is_byte_identical_for_python_nodes():
    expected = {
        "id", "type", "name", "layer", "tier", "discovered_by", "state",
        "version", "check_command", "evidence", "fix_candidates", "chosen_fix",
        "attempts", "provenance", "discovered_cycle", "certified_cycle",
        "build_from_source", "artifact", "hash", "resolved_python",
        "resolved_platform", "exclude_newer", "setup_commands", "strength",
        "phase", "data",
    }
    assert set(_node().to_dict()) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/depgraph/test_schema_ecosystem.py -q`
Expected: FAIL — `AttributeError: 'Node' object has no attribute 'ecosystem'` (and `test_to_dict_emits...` errors with `TypeError: __init__() got an unexpected keyword argument 'ecosystem'`).

- [ ] **Step 3: Add the field**

In `src/python_deps/depgraph/schema.py`, in the default-safe enrichment block, add `ecosystem` immediately after `exclude_newer` (line 165):

```python
    exclude_newer: str | None = None  # uv resolve cutoff (reproducibility)
    # Routing/composition axis (multi-language seam). Default keeps every existing
    # Python node unchanged; conditionally serialized (omit-if-default) in
    # ``to_dict`` so Python nodes stay byte-identical. Rust/Node carry "rust"/"node".
    ecosystem: str = "python"
    data: dict = field(default_factory=dict)  # general per-node metadata bag
```

(The `data` line is the existing line 166 — shown for placement only; do not duplicate it.)

- [ ] **Step 4: Make `to_dict` omit-if-default**

In `src/python_deps/depgraph/schema.py`, convert the `to_dict` body (206-234): change `return {` to `out = {`, and after the closing `}` add the conditional + `return out`:

```python
    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "layer": self.layer.value,
            "tier": self.tier,
            "discovered_by": self.discovered_by.value,
            "state": self.state.value,
            "version": self.version,
            "check_command": self.check_command,
            "evidence": self.evidence,
            "fix_candidates": list(self.fix_candidates),
            "chosen_fix": self.chosen_fix,
            "attempts": [a.to_dict() for a in self.attempts],
            "provenance": self.provenance,
            "discovered_cycle": self.discovered_cycle,
            "certified_cycle": self.certified_cycle,
            "build_from_source": self.build_from_source,
            "artifact": self.artifact,
            "hash": self.hash,
            "resolved_python": self.resolved_python,
            "resolved_platform": self.resolved_platform,
            "exclude_newer": self.exclude_newer,
            "setup_commands": list(self.setup_commands),
            "strength": self.strength.value,
            "phase": self.phase.value,
            "data": dict(self.data),
        }
        # Omit-if-default: Python nodes serialize byte-identically (no "ecosystem"
        # key); only Rust/Node nodes emit it. Load-bearing for oracle (c).
        if self.ecosystem != "python":
            out["ecosystem"] = self.ecosystem
        return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_schema_ecosystem.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Oracle (a) subset gate** (byte-identity of the frozen 1111)

Run the Task 1 Step 5 gate block. Expected: `comm -23` prints nothing. Also run the targeted schema guard explicitly:
`python3 -m pytest tests/depgraph/test_schema_audit.py -q` → PASS.

- [ ] **Step 7: Commit**

```bash
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema_ecosystem.py
git commit -m "feat(schema): Node.ecosystem field (default python) + omit-if-default to_dict for byte-identity"
```

---

## Task 3: `src/ecosystems/base.py` + `registry.select_provider`

Port the two-axis enums and define the minimal construction Protocol; add `select_provider` dispatch. The neutral seam imports only the shared `schema` (and, in the Protocol return annotation, nothing Python-specific) — never the Python pipeline — so future Rust/Node providers depend on it without pulling in `build.py`.

**Files:**
- Create: `src/ecosystems/__init__.py` (empty), `src/ecosystems/base.py`, `src/ecosystems/registry.py`
- Create: `tests/ecosystems/conftest.py` (src-shim), `tests/ecosystems/test_base.py`, `tests/ecosystems/test_registry.py`

**Interfaces:**
- Produces: `ClosureMode{LOCK,RESOLVE,COMPUTE}`, `CertifyMode{INSTALL,COMPILE}`, `EcosystemProvider` Protocol (`name`, `certify_mode`, `detect`, `closure_mode_for`, `package_obligations`, `native_obligations`), `select_provider(repo, providers, *, threshold=0.5, default=None) -> provider` (highest `detect >= threshold`; first-registered wins ties; if NONE clears the threshold, return `default` when one is supplied, else raise `LookupError`). The `default` param is what preserves "`build_dep_graph` never rejects a repo" once dispatch lands (Task 7 passes the `PythonProvider` instance as `default`).

- [ ] **Step 1: Write the failing tests**

Create `tests/ecosystems/conftest.py`:

```python
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
```

Create `tests/ecosystems/test_base.py`:

```python
from ecosystems.base import CertifyMode, ClosureMode, EcosystemProvider


def test_closure_mode_values():
    assert ClosureMode.LOCK.value == "lock"
    assert ClosureMode.RESOLVE.value == "resolve"
    assert ClosureMode.COMPUTE.value == "compute"


def test_certify_mode_values():
    assert CertifyMode.INSTALL.value == "install"
    assert CertifyMode.COMPILE.value == "compile"


def test_provider_protocol_surface():
    for method in ("detect", "closure_mode_for", "package_obligations", "native_obligations"):
        assert hasattr(EcosystemProvider, method)
```

Create `tests/ecosystems/test_registry.py`:

```python
import pytest

from ecosystems.base import CertifyMode
from ecosystems.registry import select_provider


class _Stub:
    def __init__(self, name, score):
        self.name = name
        self._score = score
        self.certify_mode = CertifyMode.INSTALL

    def detect(self, repo):
        return self._score

    def closure_mode_for(self, repo): ...
    def package_obligations(self, *a, **k): ...
    def native_obligations(self, *a, **k): ...


def test_selects_highest_above_threshold():
    assert select_provider("/r", [_Stub("b", 0.4), _Stub("a", 0.9)]).name == "a"


def test_ties_first_registered_wins():
    assert select_provider("/r", [_Stub("a", 0.7), _Stub("b", 0.7)]).name == "a"


def test_below_threshold_raises_lookup_error():
    with pytest.raises(LookupError):
        select_provider("/r", [_Stub("a", 0.2)], threshold=0.5)


def test_threshold_boundary_is_inclusive():
    assert select_provider("/r", [_Stub("a", 0.5)]).name == "a"


def test_default_returned_when_none_clears_threshold():
    fallback = _Stub("fallback", 0.0)
    assert select_provider("/r", [_Stub("a", 0.2)], default=fallback) is fallback
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/ecosystems -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecosystems'`.

- [ ] **Step 3: Create the package + `base.py`**

Create `src/ecosystems/__init__.py` (empty). Create `src/ecosystems/base.py`:

```python
"""Neutral ecosystem seam: the two-axis Protocol + closure/certify enums.

Sits ABOVE ``python_deps`` (Python is one provider among peers). Imports the
SHARED ``DepGraph`` schema but NEVER the Python pipeline (``build.py``), so
Rust/Node providers can depend on this module without pulling in Python code.
Keep the interface minimal — expand toward the full spec §4 (resolve_closure,
project_install, bulk_certify, verify commands) in later slices.
"""

from __future__ import annotations

import enum
from typing import Protocol

from python_deps.depgraph.schema import DepGraph


class ClosureMode(enum.Enum):
    """How a repo's transitive closure is obtained (per-REPO)."""

    LOCK = "lock"        # committed lockfile present -> parse offline. Preferred.
    RESOLVE = "resolve"  # no lock -> run the resolver, then pin. Python is RESOLVE.
    COMPUTE = "compute"  # no lock, no cheap resolver (Java/Gradle). Admitted, deferred.


class CertifyMode(enum.Enum):
    """How the host establishes a PACKAGE-tier node's truth (per-PROVIDER).

    Resource tiers (SystemLib/Tool/Runtime) are ALWAYS presence-certified in every
    ecosystem, regardless of this value; the scheduler routes by (tier, certify_mode).
    """

    INSTALL = "install"  # each Package node certified by one check_command. Python, Node.
    COMPILE = "compile"  # one bulk build certifies the whole closure; per-node attributed. Rust, Go.


class EcosystemProvider(Protocol):
    """The construction subset of the provider interface THIS branch needs."""

    name: str                 # "python" | "rust" | "node"
    certify_mode: CertifyMode

    def detect(self, repo: str) -> float:
        """Confidence 0..1 that ``repo`` belongs to this ecosystem (dispatch gate)."""
        ...

    def closure_mode_for(self, repo: str) -> ClosureMode:
        """Per-repo: LOCK if a committed lock is present, else RESOLVE (COMPUTE deferred)."""
        ...

    def package_obligations(
        self,
        repo: str,
        container_executor: object,
        *,
        host_executor: object | None = None,
        target_python: str | None = None,
        target_platform: str | None = None,
        exclude_newer: str | None = None,
        needed_extras: frozenset[str] = frozenset(),
        record_provider: object | None = None,
    ) -> tuple[DepGraph, list, object, str | None]:
        """PHASE 1 body. Returns ``(graph, roots, target_env, exclude_newer)``;
        only ``graph`` flows onward (the rest are provider-composition / test-
        visibility surface). ``record_provider`` is an opaque provider-specific
        grounding-oracle injection (test seam); Python uses it, other ecosystems
        accept-and-ignore ``None``. Surfaced for signature stability
        (``research_zero_impact.md`` §3)."""
        ...

    def native_obligations(self, graph: DepGraph, container_executor: object) -> DepGraph:
        """PHASE 2 "look then derive": relink -> ldd -> dlopen backstop -> probe restamp."""
        ...
```

- [ ] **Step 4: Create `registry.py`**

Create `src/ecosystems/registry.py`:

```python
"""Provider dispatch: pick the ecosystem whose ``detect`` wins above threshold."""

from __future__ import annotations

from typing import Sequence

from ecosystems.base import EcosystemProvider


def select_provider(
    repo: str,
    providers: Sequence[EcosystemProvider],
    *,
    threshold: float = 0.5,
    default: EcosystemProvider | None = None,
) -> EcosystemProvider:
    """Return the highest-confidence provider whose ``detect(repo) >= threshold``.

    Ties are broken by registration order (first wins). When NO provider clears the
    threshold: return ``default`` if one was supplied, else raise ``LookupError``.
    The ``default`` seam is load-bearing for zero-impact — the pre-seam
    ``build_dep_graph`` refused NO repo, so Task 7 passes the ``PythonProvider``
    instance as ``default`` and a degenerate/manifest-less repo still dispatches to
    Python instead of newly raising ``LookupError``.
    """
    best: EcosystemProvider | None = None
    best_score = -1.0
    for provider in providers:
        score = provider.detect(repo)
        if score >= threshold and score > best_score:
            best = provider
            best_score = score
    if best is None:
        if default is not None:
            return default
        raise LookupError(f"no ecosystem provider detected for {repo!r}")
    return best
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/ecosystems -q`
Expected: PASS (8 tests: 3 in `test_base.py`, 5 in `test_registry.py` incl. the `default`-fallback test).

- [ ] **Step 6: Oracle (a) subset gate** (the new layer is inert; the 1111 must be untouched)

Run the Task 1 Step 5 gate block. Expected: empty output.

- [ ] **Step 7: Commit**

```bash
git add src/ecosystems/__init__.py src/ecosystems/base.py src/ecosystems/registry.py \
        tests/ecosystems/conftest.py tests/ecosystems/test_base.py tests/ecosystems/test_registry.py
git commit -m "feat(ecosystems): neutral seam — ClosureMode/CertifyMode/EcosystemProvider Protocol + select_provider dispatch"
```

---

## Task 4: Extract Phase-1 helper `_python_package_obligations` (MOVE, not edit)

Cut `build.py:488-608` VERBATIM into a module-level helper returning `(graph, roots, target_env, exclude_newer)`; `build_dep_graph` calls it. This task ALSO applies the ONE necessary change to the still-inline Phase B: the probe restamp (`build.py:632`) filters against `pre_resolve_ids` (defined at line 578, which now moves INSIDE the helper). Rather than thread that snapshot across the phase boundary, **drop the snapshot-exclusion clause entirely from the PROBE branch** — it is vacuous there, and a Phase-B-entry re-snapshot would be actively WRONG (proof below). `pre_resolve_ids` STAYS inside `package_obligations`: the resolver restamp at 603-608 still uses it locally, unchanged.

**Files:**
- Modify: `src/python_deps/depgraph/build.py` — add `_python_package_obligations` before `build_dep_graph`; replace `build_dep_graph`'s lines `488-608` with the call; adapt the still-inline Phase B (`610-634`).

**Interfaces:**
- Produces: `_python_package_obligations(repo_path, container_executor, *, host_executor=None, target_python=None, target_platform=None, exclude_newer=None, needed_extras=frozenset(), record_provider=None) -> tuple[DepGraph, list, object, str | None]` — body is the verbatim cut of `build.py:488-608` + a `return`.
- Consumes (unchanged, module-level in `build.py`): `scan_to_nodes`, `_restamp`, `detect_target_env`, `select_roots`, `compute_exclude_newer`, `composite_record_provider`, `default_record_provider`, `pypi_record_provider`, `_phase_a_fixpoint`, `_add_project_node`, `add_subprocess_tool_nodes`, `seed_wheel_oracle_prior`, `_SCAN_CYCLE`/`_RESOLVER_CYCLE`, `DiscoveredBy`/`Node`/`NodeType`/`Layer`/`State`, `LocalSubprocessExecutor`, `RecordProvider`.

**The drop-the-clause proof (why removing `n.id not in pre_resolve_ids` from the PROBE branch is byte-identical):** the original probe restamp is `{n.id for n in graph.nodes if n.id not in pre_resolve_ids and n.discovered_by is DiscoveredBy.PROBE}`. The `n.id not in pre_resolve_ids` clause is **vacuous for the PROBE branch**. `pre_resolve_ids` is the set of node ids present *before* `_phase_a_fixpoint` — i.e. the Stage-1 scan Import/Test nodes and the Stage-1.5 Runtime node — and those are ALL `discovered_by=STATIC_SCAN`, never `PROBE` (`build.py:554` Runtime + `scan.py`). PROBE ids (`tool:…`, soname `syslib:…`, `apt:…`) are minted only by `install_closure`/`ldd_probe`/`import_probe` and never collide with a pre-fixpoint scan id, so every id the `not in pre_resolve_ids` clause would exclude is ALREADY excluded by `is DiscoveredBy.PROBE`. The clause removes nothing → `{n.id for n in graph.nodes if n.discovered_by is DiscoveredBy.PROBE}` is byte-identical to the original set, and needs NO snapshot to cross the phase boundary (so `native_obligations(graph, container_executor)` stays genuinely self-contained, INV-6).

**Why NOT a Phase-B-entry `pre_probe_ids` snapshot (the discarded, WRONG alternative):** `install_closure` runs INSIDE `_phase_a_fixpoint` (Phase A) and, on a build-tool/soname gap with no seeded prediction, creates fresh `discovered_by=PROBE` Tool/SystemLib nodes (`probe.py:335-375` `_make_tool_node`/`_make_syslib_node`, `discovered_cycle` defaulting to 0). A snapshot taken at Phase-B *entry* (after the helper returns) would ALREADY contain those Phase-A-created PROBE ids and wrongly exclude them from `probe_ids`, leaving them `discovered_cycle=0` instead of `_PROBE_CYCLE=3`. That flips `tests/depgraph/test_build.py::test_build_discovered_cycle_per_stage` (`assert graph.get(tool_id("pg_config")).discovered_cycle == 3`) to `AssertionError: assert 0 == 3` — the psycopg2/pg_config fixture exercises exactly this path. Falsified/guarded by oracle (a) + `tests/depgraph/test_build.py::test_build_discovered_cycle_per_stage` (the test that pins probe-cycle stamping of a Phase-A-discovered node; `_restamp` is the only code path that ever sets `discovered_cycle`).

- [ ] **Step 1: Add the helper (verbatim cut of `488-608`)**

Insert, immediately BEFORE `def build_dep_graph(` (line 433), a new module-level function. Its body is the EXACT lines `488-608` of the pre-change file (from `host_executor = host_executor or LocalSubprocessExecutor()` through `graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)`) — **cut/paste, do not retype** — followed by a `return`:

```python
def _python_package_obligations(
    repo_path: str,
    container_executor: Executor,
    *,
    host_executor: Executor | None = None,
    target_python: str | None = None,
    target_platform: str | None = None,
    exclude_newer: str | None = None,
    needed_extras: frozenset[str] = frozenset(),
    record_provider: RecordProvider | None = None,
) -> tuple[DepGraph, list, object, str | None]:
    """Python PHASE 1 — VERBATIM move of build_dep_graph body lines 488-608.

    Scan -> target-env -> declared roots -> era-anchor (ONCE, INV-1) -> Runtime
    node -> composite record-provider default (constructed HERE at the old
    569-571 site, INV-8) -> Phase-A repair fixpoint -> aux-once (project/tools/
    seed) -> resolver restamp (INV-7). Returns (graph, roots, target_env,
    exclude_newer); only ``graph`` flows onward — the other three are provider-
    composition / test-visibility surface (never read again after the fixpoint).
    """
    # <<< VERBATIM build.py:488-608 pasted here, unchanged >>>
    #   host_executor = host_executor or LocalSubprocessExecutor()
    #   ... (Stage 1 .. resolver restamp) ...
    #   graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)
    return graph, roots, target_env, exclude_newer
```

- [ ] **Step 2: Replace `build_dep_graph`'s `488-608` with the call, and adapt the inline Phase B**

`build_dep_graph` keeps its signature and docstring unchanged. Replace everything from the old line 488 through the old line 634 with:

```python
    graph, roots, target_env, exclude_newer = _python_package_obligations(
        repo_path,
        container_executor,
        host_executor=host_executor,
        target_python=target_python,
        target_platform=target_platform,
        exclude_newer=exclude_newer,
        needed_extras=needed_extras,
        record_provider=record_provider,
    )
    # NOTE: only `graph` flows onward; roots/target_env/exclude_newer are
    # provider-composition / test-visibility surface (spec extraction boundary).

    # Everything below is the VERBATIM cut of build.py:610-642 — copy the REAL
    # inline stage comments (this snippet abbreviates them with `... (verbatim
    # NNN-NNN)`; do not ship the abbreviations). The ONLY change vs the original is
    # FIX-1: the `probe_ids` comprehension drops its `n.id not in pre_resolve_ids`
    # clause (vacuous for the PROBE branch; see proof above). NO snapshot is taken.
    # === Phase B — tier descent on the CONVERGED closure, "look then derive". ===
    # Stage 4a — certified Import->Package relink FIRST: Phase B's LOOK, and the
    # SOLE Import->Package source. ... (verbatim build.py:611-617)
    graph = certified_import_links(graph, container_executor)
    # Stage 4.5 — AUTHORITATIVE run-time native-lib discovery (ldd DT_NEEDED
    # ground truth). ... (verbatim build.py:619-623)
    graph = ldd_probe(graph, container_executor)
    # import_probe is the dlopen BACKSTOP only. ... (verbatim build.py:625-627)
    graph = import_probe(graph, container_executor)
    probe_ids = {
        n.id
        for n in graph.nodes
        if n.discovered_by is DiscoveredBy.PROBE      # FIX-1: pre_resolve_ids clause dropped
    }
    graph = _restamp(graph, probe_ids, _PROBE_CYCLE)

    # Stage 4b — release-aware apt-name reconciliation against the TARGET image.
    # ... (verbatim build.py:636-638)
    graph = reconcile_apt_names(graph, container_executor)
    # Stage 5 — host certification in the container (layer-ordered; flips state).
    graph = certify_all(graph, container_executor, cycle=_CERTIFY_CYCLE)

    return graph
```

- [ ] **Step 3: Verify it's a pure move (diff review)**

Run: `git diff src/python_deps/depgraph/build.py`
Expected: the `488-608` block appears IDENTICALLY inside the new `_python_package_obligations` (a move); inside `build_dep_graph` the only new content is the helper call plus the verbatim-cut Phase-B block (`610-642`), whose SOLE delta vs the original is dropping the `n.id not in pre_resolve_ids` clause from the `probe_ids` comprehension (FIX-1) — no `pre_probe_ids` line is added. **The Phase-B inline comments must be copied verbatim from `build.py` — no comment reword/collapse** (that would be a spurious delta beyond the one allowed change; it is exactly the freely-paraphrasing habit that masked the original mis-analysis). **No edit inside any do-not-touch function** (`_phase_a_fixpoint`, `_stamp_audit`, `_restamp`, `reconcile_packages`, `certified_import_links`, `flag_*`, `resolved_record_coverage`, record-provider factories). If any other line changed, revert and redo the cut.

- [ ] **Step 4: Oracle (a) subset gate + targeted invariant guards**

```bash
python3 -m pytest tests/depgraph/test_build_phase_order.py tests/depgraph/test_phase_a_fixpoint.py \
  tests/depgraph/test_relink.py tests/depgraph/test_record_provider.py \
  tests/depgraph/test_schema_audit.py tests/depgraph/test_pins.py \
  tests/depgraph/test_roots.py tests/depgraph/test_build.py -q
# FIX-1 GUARD — the exact test a Phase-B-entry snapshot would have broken:
python3 -m pytest "tests/depgraph/test_build.py::test_build_discovered_cycle_per_stage" -q
```
Expected: PASS — in particular `test_build_discovered_cycle_per_stage` stays green (`tool:pg_config .discovered_cycle == 3`), confirming the dropped-clause restamp still stamps the Phase-A-discovered PROBE node. Then run the Task 1 Step 5 gate block → empty output.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build.py
git commit -m "refactor(build): extract Phase-1 into _python_package_obligations (verbatim move) + self-contain Phase-B probe snapshot"
```

---

## Task 5: Extract Phase-2 helper `_python_native_obligations` (MOVE, not edit)

Cut the now-self-contained inline Phase-B block (`certified_import_links` … `_restamp(probe_ids, _PROBE_CYCLE)`) into a module-level helper `(graph, container_executor) -> graph`; `build_dep_graph` calls it. `reconcile_apt_names` + `certify_all` stay as the direct orchestrator tail. Because Task 4's FIX-1 change already made the block self-contained (no `pre_resolve_ids`/`pre_probe_ids` snapshot crosses the boundary), this is a PURE move — carry the verbatim inline stage comments across too.

**Files:**
- Modify: `src/python_deps/depgraph/build.py` — add `_python_native_obligations`; replace the inline Phase-B block in `build_dep_graph` with the call.

**Interfaces:**
- Produces: `_python_native_obligations(graph: DepGraph, container_executor: Executor) -> DepGraph` — relink → ldd → dlopen backstop → probe restamp.
- Consumes (unchanged): `certified_import_links`, `ldd_probe`, `import_probe`, `_restamp`, `_PROBE_CYCLE`, `DiscoveredBy`.

- [ ] **Step 1: Add the helper (pure cut of the Phase-B block)**

Insert, immediately before `def build_dep_graph(`, after `_python_package_obligations`:

```python
def _python_native_obligations(graph: DepGraph, container_executor: Executor) -> DepGraph:
    """Python PHASE 2 — "look then derive" on the CONVERGED closure.

    relink (certified Import->Package + honest ``unresolved`` flags) -> ldd
    (DT_NEEDED SystemLibs) -> import_probe (dlopen backstop) -> probe restamp
    (INV-9 order; relink FIRST). Self-contained WITHOUT a snapshot: the probe
    restamp stamps every ``discovered_by=PROBE`` node — no ``pre_resolve_ids``/
    ``pre_probe_ids`` exclusion is needed, because that clause is vacuous for the
    PROBE branch AND a Phase-B-entry snapshot would wrongly drop the PROBE Tool/
    SystemLib nodes ``install_closure`` already created during Phase A (FIX-1; see
    Task 4 proof). The verbatim build.py:610-634 inline stage comments carry over
    below — this docstring does NOT replace them.
    """
    # (Stage 4a relink LOOK / Stage 4.5 ldd DT_NEEDED / import_probe dlopen
    #  backstop — verbatim inline comments from build.py:610-634 carry here.)
    graph = certified_import_links(graph, container_executor)
    graph = ldd_probe(graph, container_executor)
    graph = import_probe(graph, container_executor)
    probe_ids = {
        n.id
        for n in graph.nodes
        if n.discovered_by is DiscoveredBy.PROBE      # FIX-1: no snapshot exclusion
    }
    graph = _restamp(graph, probe_ids, _PROBE_CYCLE)
    return graph
```

- [ ] **Step 2: Replace the inline Phase-B block in `build_dep_graph` with the call**

`build_dep_graph`'s body becomes (Phase-1 call from Task 4, then):

```python
    graph = _python_native_obligations(graph, container_executor)

    # === SHARED tail (ecosystem-agnostic; direct orchestrator calls) ===
    graph = reconcile_apt_names(graph, container_executor)
    graph = certify_all(graph, container_executor, cycle=_CERTIFY_CYCLE)

    return graph
```

- [ ] **Step 3: Verify pure move**

Run: `git diff src/python_deps/depgraph/build.py`
Expected: the Phase-B block moved IDENTICALLY into `_python_native_obligations`; `build_dep_graph` now has the two-line `_python_native_obligations(...)` call replacing it. `reconcile_apt_names`/`certify_all` unchanged, same order.

- [ ] **Step 4: Oracle (a) subset gate + phase-order guard**

```bash
python3 -m pytest tests/depgraph/test_build_phase_order.py tests/depgraph/test_build.py \
  tests/depgraph/test_relink.py tests/depgraph/test_ldd_probe.py -q  # (ldd_probe host-only tests)
# FIX-1 GUARD (unchanged by this pure move; must still pass):
python3 -m pytest "tests/depgraph/test_build.py::test_build_discovered_cycle_per_stage" -q
```
Expected: PASS (skip any Docker-gated `*_docker.py`); `test_build_discovered_cycle_per_stage` stays green. Then run the Task 1 Step 5 gate block → empty output.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build.py
git commit -m "refactor(build): extract Phase-2 into _python_native_obligations (pure move); reconcile/certify stay as shared tail"
```

---

## Task 6: `PythonProvider` delegating to the two helpers (TDD)

Add `src/ecosystems/python/provider.py`; wire `detect`/`closure_mode_for`/`package_obligations`/`native_obligations` to the extracted helpers (`certify_mode=INSTALL`). Prove delegation deterministically (monkeypatched helpers) and prove the INV-8 hermeticity symbol identity is preserved (the provider must not fork the record-provider objects the autouse stub patches). End-to-end hermeticity is re-proven by oracle (a) once dispatch lands (Task 7).

**Files:**
- Create: `src/ecosystems/python/__init__.py` (empty), `src/ecosystems/python/provider.py`
- Test: `tests/depgraph/test_python_provider.py` (under `tests/depgraph/` so the autouse `_no_pypi_network` fixture is active — the whole point of the symbol-identity assertion)

**Interfaces:**
- Produces: `PythonProvider` with `name="python"`, `certify_mode=CertifyMode.INSTALL`; `detect(repo)` = `1.0` if `_project_build_manifest` present, else `0.8` if `collect_python_dependency_evidence(repo)` yields ANY declared dependency OR import, else `0.0` (evidence-based per the spec's delegation table: `_project_build_manifest` + `evidence.collect_python_dependency_evidence` — so a manifest-less repo whose Python-ness is only in `requirements.txt`/`setup.cfg`/constraints/imports, with no `*.py`, still scores positive; NO bespoke rglob); `closure_mode_for(repo)` = `LOCK` iff `uv.lock` committed else `RESOLVE`; `package_obligations`/`native_obligations` delegate to `_python_package_obligations`/`_python_native_obligations`. Dispatch NEVER rejects a repo: Task 7 passes this instance as `select_provider(..., default=)`, so even a `detect()==0.0` repo falls back to Python (preserving "`build_dep_graph` never rejects a repo").

- [ ] **Step 1: Write the failing tests**

Create `tests/depgraph/test_python_provider.py`:

```python
from ecosystems.base import CertifyMode, ClosureMode
from ecosystems.python import provider as provmod
from ecosystems.python.provider import PythonProvider


def test_name_and_certify_mode():
    assert PythonProvider().name == "python"
    assert PythonProvider().certify_mode is CertifyMode.INSTALL


def test_detect_manifest_repo_is_1(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    assert PythonProvider().detect(str(tmp_path)) == 1.0


def test_detect_imports_only_repo_clears_threshold(tmp_path):
    (tmp_path / "app.py").write_text("import os\n")  # no manifest; import evidence
    assert PythonProvider().detect(str(tmp_path)) >= 0.5


def test_detect_requirements_only_repo_clears_threshold(tmp_path):
    # No manifest, no *.py — Python-ness is ONLY in requirements.txt. The dropped
    # rglob heuristic would score this 0.0 (regression); evidence-based detect
    # scores it positive via collect_python_dependency_evidence.
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    assert PythonProvider().detect(str(tmp_path)) >= 0.5


def test_detect_non_python_repo_is_0(tmp_path):
    (tmp_path / "README.md").write_text("hi\n")
    assert PythonProvider().detect(str(tmp_path)) == 0.0


def test_closure_mode_resolve_without_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    assert PythonProvider().closure_mode_for(str(tmp_path)) is ClosureMode.RESOLVE


def test_closure_mode_lock_with_uv_lock(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    assert PythonProvider().closure_mode_for(str(tmp_path)) is ClosureMode.LOCK


def test_package_obligations_delegates_and_threads_record_provider(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_helper(repo, ce, **kw):
        seen["repo"] = repo
        seen["ce"] = ce
        seen["kw"] = kw
        return sentinel

    monkeypatch.setattr(provmod, "_python_package_obligations", fake_helper)
    out = PythonProvider().package_obligations(
        "/r", "CE",
        host_executor="HE", target_python="3.11", target_platform="linux-x86_64",
        exclude_newer="2024-01-01", needed_extras=frozenset({"test"}),
        record_provider="RP",
    )
    assert out is sentinel
    assert seen["repo"] == "/r" and seen["ce"] == "CE"
    assert seen["kw"]["host_executor"] == "HE"
    assert seen["kw"]["needed_extras"] == frozenset({"test"})
    assert seen["kw"]["record_provider"] == "RP"          # threaded (INV signature-stability)


def test_native_obligations_delegates(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_native(graph, ce):
        seen["graph"] = graph
        seen["ce"] = ce
        return sentinel

    monkeypatch.setattr(provmod, "_python_native_obligations", fake_native)
    out = PythonProvider().native_obligations("G", "CE")
    assert out is sentinel and seen["graph"] == "G" and seen["ce"] == "CE"


def test_provider_preserves_hermeticity_symbols():
    """INV-8: importing the provider must NOT fork the record-provider symbols the
    autouse _no_pypi_network stub patches (build.py's module imports are untouched;
    the composite default is still built at the old 569-571 site inside the helper).
    End-to-end hermeticity is re-proven by oracle (a) after Task 7."""
    from python_deps.depgraph import build, coverage, relink

    assert build.pypi_record_provider is coverage.pypi_record_provider
    assert build.composite_record_provider is coverage.composite_record_provider
    assert build.default_record_provider is coverage.default_record_provider
    assert "fetch" in coverage.pypi_record_provider.__kwdefaults__  # the patched lever
    assert relink.PACKAGES_DIST_CMD is not None                     # unchanged object
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/depgraph/test_python_provider.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecosystems.python.provider'`.

- [ ] **Step 3: Create the provider**

Create `src/ecosystems/python/__init__.py` (empty). Create `src/ecosystems/python/provider.py`:

```python
"""Python ecosystem provider — a pass-through wrapper over ``build.py``.

Delegates Phase 1/2 to the module-level helpers extracted from
``build_dep_graph`` (``_python_package_obligations`` / ``_python_native_obligations``)
and adds NO behavior. ``certify_mode`` is INSTALL (each Package node certified by
one check_command). Importing this module pulls in ``build.py`` (already fully
loaded whenever ``build_dep_graph`` dispatches), so there is no import cycle.
"""

from __future__ import annotations

from pathlib import Path

from ecosystems.base import CertifyMode, ClosureMode
from python_deps.depgraph.build import (
    _project_build_manifest,
    _python_native_obligations,
    _python_package_obligations,
)
from python_deps.depgraph.executor import Executor
from python_deps.depgraph.repair import RecordProvider
from python_deps.depgraph.schema import DepGraph
from python_deps.evidence import collect_python_dependency_evidence


class PythonProvider:
    name = "python"
    certify_mode = CertifyMode.INSTALL

    def detect(self, repo: str) -> float:
        # Spec delegation table: _project_build_manifest + evidence.
        # collect_python_dependency_evidence ("does this repo declare/import
        # Python?"). Manifest -> 1.0; else ANY declared dependency OR import
        # (covers requirements.txt/setup.cfg/constraints even with no *.py) -> 0.8;
        # else 0.0. Dispatch still never rejects a repo: Task 7 passes this provider
        # as select_provider(..., default=), so a 0.0 repo falls back to Python.
        if _project_build_manifest(repo) is not None:
            return 1.0
        evidence = collect_python_dependency_evidence(repo)
        if evidence.declared_dependencies or evidence.imports:
            return 0.8
        return 0.0

    def closure_mode_for(self, repo: str) -> ClosureMode:
        if (Path(repo) / "uv.lock").is_file():
            return ClosureMode.LOCK
        return ClosureMode.RESOLVE

    def package_obligations(
        self,
        repo: str,
        container_executor: Executor,
        *,
        host_executor: Executor | None = None,
        target_python: str | None = None,
        target_platform: str | None = None,
        exclude_newer: str | None = None,
        needed_extras: frozenset[str] = frozenset(),
        record_provider: RecordProvider | None = None,
    ) -> tuple[DepGraph, list, object, str | None]:
        return _python_package_obligations(
            repo,
            container_executor,
            host_executor=host_executor,
            target_python=target_python,
            target_platform=target_platform,
            exclude_newer=exclude_newer,
            needed_extras=needed_extras,
            record_provider=record_provider,
        )

    def native_obligations(self, graph: DepGraph, container_executor: Executor) -> DepGraph:
        return _python_native_obligations(graph, container_executor)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/depgraph/test_python_provider.py -q`
Expected: PASS (10 tests, incl. the `requirements.txt`-only detect test). The hermeticity-symbol test runs under the autouse `_no_pypi_network` fixture and confirms the patched objects are still identical.

- [ ] **Step 5: Oracle (a) subset gate**

Run the Task 1 Step 5 gate block. Expected: empty output. (The provider is not yet wired into `build_dep_graph`; the gate confirms the new module is inert to the 1111.)

- [ ] **Step 6: Commit**

```bash
git add src/ecosystems/python/__init__.py src/ecosystems/python/provider.py tests/depgraph/test_python_provider.py
git commit -m "feat(ecosystems): PythonProvider delegating to build.py helpers (detect/closure_mode/package+native obligations, certify_mode=INSTALL)"
```

---

## Task 7: Dispatch shell — `build_dep_graph` = `select_provider` → provider methods → shared tail

Rewire `build_dep_graph` to route through the seam: `select_provider(repo, PROVIDERS)` → `provider.package_obligations` → `provider.native_obligations` → the shared `reconcile_apt_names`/`certify_all` tail. Only `graph` flows onward. The direct `_python_package_obligations`/`_python_native_obligations` calls from Tasks 4-5 are replaced by the polymorphic provider calls (the provider wraps the same helpers, so behavior is unchanged). Register `PROVIDERS = (PythonProvider(),)` in `registry.py`.

**Files:**
- Modify: `src/ecosystems/registry.py` — add `PythonProvider` import + `PROVIDERS` tuple.
- Modify: `src/python_deps/depgraph/build.py` — `build_dep_graph` body becomes the dispatch shell (function-local import of the registry to break the build↔provider cycle).
- Modify (test): `tests/depgraph/test_python_provider.py` — add the degenerate-repo dispatch guard (needs the registered `PROVIDERS`, which exists only after Step 1).

**Interfaces:**
- Consumes: `ecosystems.registry.PROVIDERS`, `ecosystems.registry.select_provider`.
- Produces: `build_dep_graph` unchanged signature; body dispatches. `PROVIDERS: tuple[EcosystemProvider, ...] = (PythonProvider(),)`.

- [ ] **Step 1: Register the provider in `registry.py`**

Append to `src/ecosystems/registry.py` (module level, after `select_provider`):

```python
# Registered providers, dispatch order = tie-break order. Rust/Node append here in
# Slices 2/3. Imported at module load; safe because ``build.py`` never imports
# ``ecosystems`` at module level (only ``build_dep_graph`` does, lazily).
from ecosystems.python.provider import PythonProvider  # noqa: E402

PROVIDERS: tuple = (PythonProvider(),)
```

- [ ] **Step 2: Make `build_dep_graph` the dispatch shell**

Replace `build_dep_graph`'s body (the Task-5 form: the two helper calls + tail) with the dispatch shell. Keep the signature and docstring unchanged:

```python
    # Function-local import breaks the build<->provider cycle: by the time this
    # runs, build.py is fully loaded, so ecosystems.python.provider (which imports
    # build helpers) resolves cleanly.
    from ecosystems.registry import PROVIDERS, select_provider

    # default=PROVIDERS[0] (the PythonProvider) preserves "build_dep_graph never
    # rejects a repo": if NO provider clears the detect threshold (degenerate /
    # manifest-less / *.py-less repo), dispatch STILL routes to Python instead of
    # raising LookupError — zero-impact vs the pre-seam unconditional-accept path.
    provider = select_provider(repo_path, PROVIDERS, default=PROVIDERS[0])  # dispatch
    graph, roots, target_env, exclude_newer = provider.package_obligations(  # Phase 1
        repo_path,
        container_executor,
        host_executor=host_executor,
        target_python=target_python,
        target_platform=target_platform,
        exclude_newer=exclude_newer,
        needed_extras=needed_extras,
        record_provider=record_provider,
    )
    # only `graph` flows onward; roots/target_env/exclude_newer are provider-
    # composition / test-visibility surface.
    graph = provider.native_obligations(graph, container_executor)          # Phase 2
    graph = reconcile_apt_names(graph, container_executor)                  # SHARED tail
    graph = certify_all(graph, container_executor, cycle=_CERTIFY_CYCLE)    # SHARED tail
    return graph
```

- [ ] **Step 3: Regression test — a degenerate repo still dispatches to Python (guards the LookupError regression)**

Append to `tests/depgraph/test_python_provider.py` (imports the now-registered `PROVIDERS`):

```python
def test_degenerate_repo_still_dispatches_to_python(tmp_path):
    """No manifest, no *.py, no evidence -> detect()==0.0, but the dispatch
    default (Task 7) must STILL route to PythonProvider, never raise LookupError.
    Reference: test_build_empty_repo_yields_only_test_node (in the frozen 1111)
    must also still pass — its stdlib-import repo has a *.py so it clears the
    threshold directly; this test covers the truly-degenerate case the `default`
    seam guards, which oracle (a) does not otherwise exercise."""
    from ecosystems.registry import PROVIDERS, select_provider

    (tmp_path / "README.md").write_text("no python here\n")
    picked = select_provider(str(tmp_path), PROVIDERS, default=PROVIDERS[0])
    assert picked is PROVIDERS[0] and picked.name == "python"
```

Run: `python3 -m pytest tests/depgraph/test_python_provider.py -q` → PASS (11 tests). Also confirm the frozen-1111 guard `python3 -m pytest "tests/depgraph/test_build.py::test_build_empty_repo_yields_only_test_node" -q` still passes (dispatch always reaches Python).

- [ ] **Step 4: Verify the call order is preserved**

Run: `git diff src/python_deps/depgraph/build.py`
Expected: the only change vs Task 5 is swapping the two direct `_python_*` calls for `select_provider(..., default=PROVIDERS[0])` + `provider.package_obligations`/`provider.native_obligations`; `reconcile_apt_names`/`certify_all` unchanged, same order (INV-9). The `_python_package_obligations`/`_python_native_obligations` module-level helpers remain (now called via the provider).

- [ ] **Step 5: Oracle (a) subset gate + targeted guards (full hermetic pass)**

```bash
python3 -m pytest tests/depgraph/test_build_phase_order.py tests/depgraph/test_phase_a_fixpoint.py \
  tests/depgraph/test_relink.py tests/depgraph/test_record_provider.py \
  tests/depgraph/test_schema_audit.py tests/depgraph/test_pins.py \
  tests/depgraph/test_roots.py tests/depgraph/test_build.py \
  tests/depgraph/test_build_target_env.py tests/depgraph/test_runtime_node.py -q
```
Expected: PASS — these now flow end-to-end through `select_provider` → `PythonProvider` and re-prove hermeticity (no `urlopen` AssertionError). Then run the Task 1 Step 5 gate block → empty output.

- [ ] **Step 6: Commit**

```bash
git add src/ecosystems/registry.py src/python_deps/depgraph/build.py tests/depgraph/test_python_provider.py
git commit -m "feat(build): build_dep_graph dispatch shell (select_provider -> provider obligations -> shared tail); register PythonProvider in PROVIDERS; default-dispatch guard"
```

---

## Task 8: Slice-1 gate — hermetic oracles + full-node diff, then the package-layer EVALUATION capstone

The acceptance task. Fast hermetic checks first — oracle (a) suite subset, oracle (b) A/B, and the new whole-graph `to_dict()` byte-diff — then the heavyweight **package-layer fidelity EVALUATION as the FINAL no-regression step**: the definitive baseline-Python confirmation that per-repo closures AND pooled/per-repo recall/precision are byte-identical to the frozen baseline. The slice lands ONLY when that final evaluation is clean. Controller-run.

**Files:** none modified (measurement + artifact regeneration only).

- [ ] **Step 1: Oracle (a) — construction suites, frozen 1111 partition unchanged**

```bash
cd /Users/john/john-planner-v3-core-autoresearch
# full run + subset gate (Task 1 Step 5):
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval --tb=no -rA -q \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_a_after_rA.txt 2>&1
grep -E '^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) ' \
  /Users/john/.claude/jobs/366037cb/tmp/oracle_a_after_rA.txt | sort \
  > /Users/john/.claude/jobs/366037cb/tmp/oracle_a_after_status.txt
comm -23 /Users/john/.claude/jobs/366037cb/tmp/oracle_a_baseline_status.txt \
         /Users/john/.claude/jobs/366037cb/tmp/oracle_a_after_status.txt
# also confirm the construction-relevant suites are fully green + the 7 known
# off-path failures did not change (they live OUTSIDE these 3 dirs, so are absent
# here — these 3 dirs must be green):
python3 -m pytest tests/depgraph tests/pkg_layer tests/eval -q | tail -1
```
Gate: `comm -23` prints NOTHING (every frozen baseline node-id reproduced its exact status). New seam tests (`tests/ecosystems`, `test_schema_ecosystem.py`, `test_python_provider.py`) are additive and separately green.

- [ ] **Step 2: Oracle (b) — A/B verdict `verifier` 30/0/30/0, JSON byte-clean**

```bash
mkdir -p /Users/john/.claude/jobs/366037cb/tmp/after
python3 scripts/eval/graph_fidelity/root_selection_ab.py \
  --clones-root outputs/graph_fidelity/_smoke \
  --out /Users/john/.claude/jobs/366037cb/tmp/after/root_selection_ab.md
python3 scripts/eval/graph_fidelity/pkg_layer_ab.py \
  --clones-root outputs/graph_fidelity/_smoke \
  --out /Users/john/.claude/jobs/366037cb/tmp/after/pkg_layer_ab.md
# each script writes <out>.json; diff vs committed baselines (must be empty):
diff /Users/john/.claude/jobs/366037cb/tmp/after/root_selection_ab.json \
     outputs/graph_fidelity/root_selection_ab.json
diff /Users/john/.claude/jobs/366037cb/tmp/after/pkg_layer_ab.json \
     outputs/graph_fidelity/pkg_layer_ab.json
# confirm the verdict:
python3 -c "import json;a=json.load(open('/Users/john/.claude/jobs/366037cb/tmp/after/root_selection_ab.json'))['aggregate'];print(a['verdict']);import sys;sys.exit(0 if a['verdict']=='verifier' else 1)"
```
Gate: both `diff`s EMPTY; `aggregate.verdict == "verifier"` with `bad >= good` (30/0/30/0). `--clones-root` = the same clones dir the committed baselines were generated from (`outputs/graph_fidelity/_smoke`); if that layout differs on this box, use the `probe_A/…` clones root that produced the committed JSONs.

- [ ] **Step 3: Full-node `to_dict()` byte-diff (hermetic) — whole-graph schema unchanged**

Re-run the Task 1 Step 6 fixture through the (now dispatched) `build_dep_graph` and diff the WHOLE-graph serialization against the frozen baseline. This is the ONLY oracle that serializes every node field (`discovered_cycle`, `certified_cycle`, `check_command`, `attempts`, `provenance`, `phase`, `strength`, cycles) — it directly catches the FIX-1 `discovered_cycle` regression class and any other full-node-schema drift the three proxy oracles miss.

```bash
cd /Users/john/john-planner-v3-core-autoresearch
python3 - <<'PY' > /Users/john/.claude/jobs/366037cb/tmp/fullnode_after.json
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests/depgraph")
from test_build import _build                       # same deterministic fixture as Task 1 Step 6
with tempfile.TemporaryDirectory() as d:
    graph = _build(Path(d))
print(json.dumps(graph.to_dict(), sort_keys=True, indent=2))
PY
diff /Users/john/.claude/jobs/366037cb/tmp/fullnode_baseline.json \
     /Users/john/.claude/jobs/366037cb/tmp/fullnode_after.json
```
Gate: `diff` prints NOTHING — the whole-graph `to_dict()` (including every Python node's full field set, byte-identical because `ecosystem="python"` is omit-if-default) matches the pre-change baseline exactly. A non-empty diff localizes any full-node-schema regression (e.g. a `discovered_cycle` flip) that the pooled oracles below would not surface.

- [ ] **Step 4: FINAL regression gate — baseline Python case unchanged (package-layer fidelity EVALUATION; Docker+network capstone)**

The definitive baseline-Python no-regression confirmation and the LAST step. Regenerate the 15-repo PACKAGE closures through the migrated pipeline and prove BOTH byte-identical closures AND identical pooled+per-repo recall/precision vs the frozen baseline. This is the heavyweight capstone — the slice lands ONLY when this evaluation is clean.

```bash
python3 /Users/john/.claude/jobs/366037cb/tmp/run_ours_pkg.py \
  anyio,flask,httpx,marshmallow,mvt,postgres-mcp,pytest,python-semantic-release,requests,rich,scrapy,slither,sqlalchemy,typer,vizro \
  /Users/john/.claude/jobs/366037cb/tmp/ours_after
# (1) byte-identity: per-repo PACKAGE closures unchanged vs the COMMITTED frozen baseline:
diff -r outputs/graph_fidelity/pkg_lock_ab/ours_v2 \
        /Users/john/.claude/jobs/366037cb/tmp/ours_after
# (2) fidelity unchanged: compare the regenerated closures vs the frozen oracle ->
#     MUST reprint IDENTICAL pooled recall/precision (frozen headline 0.940/0.505,
#     14-repo ex-vizro) AND identical per-repo numbers:
python3 outputs/graph_fidelity/pkg_lock_ab/compare_pkg.py \
  /Users/john/.claude/jobs/366037cb/tmp/ours_after \
  outputs/graph_fidelity/pkg_lock_ab/oracle
```
Gate — **Final regression gate — baseline Python case unchanged**: (1) `diff -r` over the 15 `*.json` closures produces ZERO differences (ignore non-`*.json` helpers), AND (2) `compare_pkg.py` reprints the SAME pooled recall/precision (0.940/0.505 ex-vizro) and the SAME per-repo numbers as the frozen `ours_v2` baseline. This evaluation catches regressions in INV-2 (AUDIT), INV-5 (taxonomy), INV-10/INV-13 (membership/versions) and is the authoritative baseline-Python no-regression proof.

**Land decision.** Land the slice ONLY if ALL hold: (a) `comm -23` empty (Step 1); (b) both A/B JSON diffs empty + verdict `verifier` 30/0/30/0 (Step 2); (c) the full-node `to_dict()` diff empty (Step 3); and (d) this FINAL evaluation shows byte-identical closures AND identical pooled+per-repo recall/precision (Step 4). If ANY differs, the seam is wrong by definition — bisect the offending task (each task's own oracle-(a) gate localizes it), fix, re-run. Record the green results in `CHANGELOG-planner-v3-e2e-loop.md` (Observation→Why→What→Verification) and update the `two-phase-declared-roots-construction-landed` / relevant memory note. Do NOT push.

---

## What comes next (Slices 2 & 3 — pointer only)

Once the seam is green, Rust and Node land as NEW provider objects appended to `PROVIDERS`, dispatched by `detect()`, against the SAME shared `schema`/`executor`/`certify`/`reconcile_apt_names` core — with `Node(...)` retargeted at the real 27-field schema (populating `layer`/`discovered_by`/`setup_commands`/`strength`/`phase`/`provenance`, the fields the prototype omitted). **Slice 2 (Rust, `certify_mode=COMPILE`):** first COMMIT the prototype's uncommitted `certify/` (incl. `cargo_messages.py`) + provider fixes; PORT `parse_cargo_lock` + `native_tables.RUST_*` + `cargo_messages.py`; add `RustProvider.package_obligations`; graft `(tier, certify_mode)` COMPILE attribution into `certify.py` while pinning the Python INSTALL path byte-identical; gate = Python oracle (a)-(c) still green + a Rust compile-certify e2e (gitui-class). **Slice 3 (Node, INSTALL/LOCK):** PORT `parse_package_lock` + `native_tables.NODE_*`; `NodeProvider.package_obligations` with the cwd-absolute `require(...).version===` check-path fix and `hasInstallScript`/`binding.gyp` toolchain frontier; gate = Python oracles green + a Node install-certify e2e (axios-class). Multi-provider composition, cross-ecosystem `requires` edges, the `EDGE_RULES` §5.5 widening, and the verify resolver stay deferred.

---

## Self-Review

**Spec coverage (Slice-1 task breakdown → this plan):**
- Task 1 Freeze baselines (oracle a `-rA`+summary, b committed JSON sha, c `ours_v2` frozen copy, + full-node `to_dict()` fixture baseline) → Task 1. ✅
- Task 2 `Node.ecosystem` + conditional `to_dict` (RED-first, byte-identity) → Task 2. ✅
- Task 3 `base.py` (PORT `ClosureMode`/`CertifyMode`/Protocol) + `registry.select_provider` → Task 3. ✅
- Task 4 Extract Phase-1 helper (move) → Task 4. ✅
- Task 5 Extract Phase-2 helper (move) → Task 5. ✅
- Task 6 `PythonProvider` delegating + INV-8 hermeticity assertion → Task 6. ✅
- Task 7 Dispatch shell → Task 7. ✅
- Task 8 Slice-1 gate — hermetic (a) 1111 subset unchanged + (b) verifier 30/0/30/0 diff-clean + (NEW) full-node `to_dict()` byte-diff, then the FINAL package-layer fidelity EVALUATION capstone (`diff -r` empty vs frozen `ours_v2` + `compare_pkg.py` identical pooled/per-repo recall/precision, 0.940/0.505 ex-vizro) as the definitive baseline-Python no-regression step → Task 8. ✅
- Rust/Node held to a one-paragraph "what comes next" pointer (Slices 2/3 NOT planned beyond that). ✅

**Three reconciled spec-gap decisions (flagged so a reviewer sees they are deliberate, not sloppy):**
1. **`record_provider` is threaded through `package_obligations`** (Protocol keyword-only `record_provider: object | None = None`; `PythonProvider` types it `RecordProvider | None`; helper takes it; dispatch passes it). The spec's illustrative interface snippet elided it, but real tests inject `record_provider=` into `build_dep_graph` (`test_build_phase_order.py`, `test_build.py:617`, `test_phase_a_fixpoint.py:502`) and `research_zero_impact.md` §3 explicitly lists `record_provider` among the params to "surface unchanged." Dropping it would break those tests and fail oracle (a). Test `test_package_obligations_delegates_and_threads_record_provider` pins it.
2. **Probe-restamp clause dropped — no boundary snapshot** (Task 4/5). The spec's "the snapshot never crosses the phase boundary" claim holds for the *resolver* restamp (603-608) but NOT the *probe* restamp: at `build.py:632` the `probe_ids` comprehension reads `pre_resolve_ids` (snapshotted at line 578, before Phase A). The correct adaptation is to **drop the `n.id not in pre_resolve_ids` clause entirely from the PROBE branch** — it is vacuous (no pre-fixpoint node is `discovered_by=PROBE`; scan/Runtime nodes are `STATIC_SCAN`), so `{n.id for n in graph.nodes if n.discovered_by is DiscoveredBy.PROBE}` is byte-identical to the original. A Phase-B-entry `pre_probe_ids` snapshot was REJECTED (an earlier draft's error): `install_closure` mints PROBE Tool/SystemLib nodes DURING Phase A (`probe.py:335-375`), so such a snapshot would wrongly exclude them and leave `discovered_cycle=0` instead of `_PROBE_CYCLE=3`. Dropping the clause needs no state threading, keeping `native_obligations(graph, container_executor)` clean (INV-6). Falsified/guarded by oracle (a) + the full-node `to_dict()` diff (Task 8 Step 3) + `tests/depgraph/test_build.py::test_build_discovered_cycle_per_stage` — the test that pins probe-cycle stamping of the Phase-A-discovered `tool:pg_config` node (NOT `test_build_phase_order.py`, which has no `discovered_cycle` assertion).
3. **`detect()` is evidence-based + dispatch has a Python `default`** (Task 6/7). The spec's delegation table mandates `detect` delegate to `_project_build_manifest` + `evidence.collect_python_dependency_evidence`. An earlier draft substituted a bespoke `Path(repo).rglob("*.py")` heuristic, which would newly score a manifest-less, `*.py`-less-but-`requirements.txt` repo `0.0` and — via `select_provider`'s new `LookupError` path — reject a repo the pre-seam `build_dep_graph` accepted unconditionally. Fixed two ways: (i) `detect()` scores `0.8` on ANY declared dependency OR import from `collect_python_dependency_evidence` (spec-faithful; `test_detect_requirements_only_repo_clears_threshold` guards the exact case); (ii) `select_provider` grows a `default=` param and Task 7 dispatches with `default=PROVIDERS[0]`, so even a `detect()==0.0` repo falls back to Python — preserving "`build_dep_graph` never rejects a repo" (`test_degenerate_repo_still_dispatches_to_python` + the frozen `test_build_empty_repo_yields_only_test_node` guard it).

**Type consistency:** `_python_package_obligations(...) -> tuple[DepGraph, list, object, str | None]`, `_python_native_obligations(graph, container_executor) -> DepGraph`, `PythonProvider.package_obligations(...) -> tuple[DepGraph, list, object, str | None]` (typed `RecordProvider | None` at the concrete site), `PythonProvider.native_obligations(graph, container_executor) -> DepGraph`, `select_provider(repo, providers, *, threshold=0.5, default=None) -> EcosystemProvider`, `PythonProvider.detect(repo) -> float`, `Node.ecosystem: str = "python"`. The 4-tuple shape is consistent across helper, provider, Protocol, and dispatch unpack. `record_provider` is typed opaque (`object | None`) in the neutral Protocol and precise (`RecordProvider | None`) at the Python concrete site — compatible. `select_provider`'s `default` is typed `EcosystemProvider | None` and is passed the concrete `PythonProvider` instance (`PROVIDERS[0]`) at the dispatch site.

**Placeholder scan:** every code step carries complete code EXCEPT (i) the two verbatim-move bodies (Task 4 `_python_package_obligations` = `build.py:488-608`; Task 5 `_python_native_obligations` = the Task-4 self-contained Phase-B block) and (ii) the deliberately-abbreviated inline stage comments in the Task 4/5 Phase-B snippets (marked `... (verbatim build.py:NNN-NNN)`). Both are DELIBERATELY referenced by exact line range with a "cut/paste, copy the real comments verbatim, apply ONLY the FIX-1 one-line clause drop" instruction plus a `git diff` pure-move verification step — retyping a ~120-line body (or rewording its comments) would risk the exact edits the zero-impact rule forbids, so a byte-preserving move is the correct executable instruction (per Global Constraints "DELEGATE, don't rewrite"). The Task 1 Step 6 / Task 8 Step 3 full-node fixture scripts are complete and runnable.

**Byte-identity discipline:** the acceptance bar is FOUR gates green with artifacts byte-identical — hermetic oracle (a) 1111-subset, oracle (b) A/B, the whole-graph `to_dict()` diff (Task 8 Step 3), and the FINAL package-layer fidelity EVALUATION capstone (byte-identical closures + identical pooled/per-repo recall/precision, 0.940/0.505 ex-vizro) as the definitive baseline-Python no-regression step — the same "no-loss, nothing to stage" standard the two-phase SDD P3.1 A/B regeneration met. Every product task also carries its own oracle-(a) subset gate so a regression is localized to the task that introduced it. No Python output changes — recall/precision/taxonomy/closure are all frozen; this is a pure structural migration.
