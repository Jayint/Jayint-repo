# Graph-Repair-Ablation PILOT — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Build the minimum harness to answer *"does giving the agent the graph structure help it localize a build failure?"* on 5 injections (one per failure class), 1 seed, arms **C0 (no-graph)** vs **C1 (graph)** — validating the grader and confirming the effect exists before scaling to the full 3-arm multi-seed experiment.

**Architecture:** New eval module `src/eval/graph_repair_ablation/`. Reuses: `build_graph_construction_only` (construct), the replay container (`_MountedContainer`), `classify_execution_failures` (symptom parse), and — critically — the existing **`src/envstate/v3_build_agent.py::V3BuildAgent.propose(scope, exec_readonly)`** as the repair agent (a read-only ReAct loop → one typed `PatchProposal`, with an *injectable* LLM `client` so the harness is fully unit-testable with a mock). The only per-arm difference is the context appended to the agent's rendered repair scope.

**Tech Stack:** Python 3.10+, pytest, Docker (final run only), an OpenAI-compatible LLM client (final run only). Design spec: `docs/superpowers/specs/2026-07-06-graph-ablation-localization-experiment-design.md`.

## Global Constraints

- Branch `john-v3-multi-lang` is SHARED: commit LOCALLY only; NEVER push/rebase/reset; `git add` only the specific named files per task, never `-A`.
- Do NOT modify `src/envstate/v3_build_agent.py`, `repair_scope.py`, `coverage.py`, or `schema.py` — this harness is **reuse-by-import only**. If a genuinely required hook is missing, STOP and surface it, don't edit those files.
- Injections perturb the **rendered `setup.sh`** (the construction *output*), never the repo's declarations — this guarantees the failure "survives construction" (no answer-key leakage; §2 of the spec).
- **Arms differ ONLY in the context string appended to the repair scope.** Same agent, model, `max_diag_turns`, container, injection, seed. Anything else that differs between arms is a confound and a bug.
- Deterministic tasks (1–4) and the runner's grading path MUST be unit-tested with a **mock LLM client + fake container** — no real model or Docker in `pytest`. Only the CLI `--run` invocation touches live model/Docker.
- Pilot C1 = a **static rendered graph-context block** appended to the scope (the interactive query-tools C1 is the *scale-up* refinement, per locked decision #2 — noted, not built here).

## File structure

- Create `src/eval/graph_repair_ablation/__init__.py`
- Create `src/eval/graph_repair_ablation/oracle.py` — `Injection` dataclass + `PILOT_INJECTIONS`
- Create `src/eval/graph_repair_ablation/inject.py` — `apply_injection(script, inj) -> str`
- Create `src/eval/graph_repair_ablation/context.py` — `flat_list_context`, `graph_context`
- Create `src/eval/graph_repair_ablation/grade.py` — `grade_localization(trace, inj) -> LocalizationScore`
- Create `src/eval/graph_repair_ablation/run.py` — `run_one(inj, arm, agent_client, ...)` + `aggregate`
- Create `src/eval/graph_repair_ablation/__main__.py` — CLI
- Tests under `tests/eval/graph_repair_ablation/`

---

### Task 1: Oracle manifest

**Files:** Create `src/eval/graph_repair_ablation/__init__.py` (empty), `src/eval/graph_repair_ablation/oracle.py`; Test `tests/eval/graph_repair_ablation/test_oracle.py`

**Interfaces:**
- Produces: `FailureClass` (str constants), `Injection` frozen dataclass, `PILOT_INJECTIONS: tuple[Injection, ...]`, `select(only, classes) -> list[Injection]`.

- [ ] **Step 1: Write the failing test**

```python
from src.eval.graph_repair_ablation.oracle import (
    Injection, PILOT_INJECTIONS, FAILURE_CLASSES, select,
)

def test_five_pilot_injections_one_per_class():
    assert len(PILOT_INJECTIONS) == 5
    assert {i.failure_class for i in PILOT_INJECTIONS} == set(FAILURE_CLASSES)

def test_every_injection_is_wellformed():
    for i in PILOT_INJECTIONS:
        assert i.injection_id and i.repo and i.base_image
        assert i.mutation["op"] in {"strip_line", "add_install_pkg", "add_pin"}
        assert i.correct_action["kind"] in {"install", "drop", "repin"}
        assert i.correct_action["target"]

def test_select_by_class_and_id():
    assert len(select(classes={"SYSLIB_MISSING"})) == 1
    only = PILOT_INJECTIONS[0].injection_id
    assert [x.injection_id for x in select(only={only})] == [only]

def test_select_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        select(classes={"NOPE"})
```

- [ ] **Step 2: Run, verify FAIL** — `python3 -m pytest tests/eval/graph_repair_ablation/test_oracle.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `oracle.py`**

```python
"""Declarative injection manifest for the graph-repair-ablation pilot. Each row is
a KNOWN-root-cause build failure produced by mutating the rendered setup.sh (so it
survives construction). The `correct_action` is the oracle the grader matches an
agent's diagnosis against."""
from __future__ import annotations

from dataclasses import dataclass

FAILURE_CLASSES: frozenset[str] = frozenset({
    "SYSLIB_MISSING", "COMPILER_ABSENT", "VERSION_CONFLICT", "OVERINCLUDE", "TOOL_ABSENT",
})


@dataclass(frozen=True)
class Injection:
    injection_id: str
    repo: str                 # corpus dir name (fetched under the build_script_eval smoke root)
    base_image: str
    failure_class: str        # one of FAILURE_CLASSES
    # how the rendered setup.sh is perturbed:
    #   {"op":"strip_line","match":"<substr>"}         -> drop the apt/tool line
    #   {"op":"add_install_pkg","pkg":"<name>"}         -> append a bad pip pkg to install
    #   {"op":"add_pin","pkg":"<name>","spec":"==x.y"}  -> append a conflicting pin
    mutation: dict
    # the KNOWN cause + the fix a correct agent should propose:
    #   {"kind":"install","target":"apt:libX-dev"} | {"kind":"drop","target":"<pkg>"}
    #   | {"kind":"repin","target":"<pkg>"}
    correct_action: dict
    note: str = ""


# One injection per class. Repos are already fetched by the build_script_eval corpus.
PILOT_INJECTIONS: tuple[Injection, ...] = (
    Injection("syslib_pygraphviz", "pygraphviz", "python:3.11-slim", "SYSLIB_MISSING",
              {"op": "strip_line", "match": "libgraphviz-dev"},
              {"kind": "install", "target": "apt:libgraphviz-dev"},
              "strip the graphviz -dev apt line -> import fails on libcgraph.so"),
    Injection("compiler_pyzmq", "pyzmq", "python:3.11-slim", "COMPILER_ABSENT",
              {"op": "strip_line", "match": "build-essential"},
              {"kind": "install", "target": "apt:build-essential"},
              "strip build-essential -> native build 'gcc failed'"),
    Injection("conflict_requests", "requests", "python:3.11-slim", "VERSION_CONFLICT",
              {"op": "add_pin", "pkg": "urllib3", "spec": "==1.20"},
              {"kind": "repin", "target": "urllib3"},
              "append an incompatible urllib3 pin -> resolver/runtime conflict"),
    Injection("overinclude_dotenv", "python-dotenv", "python:3.11-slim", "OVERINCLUDE",
              {"op": "add_install_pkg", "pkg": "this-optional-pkg-fails-to-build==0.0.0"},
              {"kind": "drop", "target": "this-optional-pkg-fails-to-build"},
              "append an unbuildable OPTIONAL dep -> install fails; correct action is DROP"),
    Injection("tool_semrel", "python-semantic-release", "python:3.11-slim", "TOOL_ABSENT",
              {"op": "strip_line", "match": "git"},
              {"kind": "install", "target": "apt:git"},
              "strip git -> GitPython GIT_PYTHON refresh error"),
)


def select(only: frozenset[str] = frozenset(),
           classes: frozenset[str] = frozenset()) -> list["Injection"]:
    if classes - FAILURE_CLASSES:
        raise ValueError(f"unknown class(es): {sorted(classes - FAILURE_CLASSES)}")
    ids = {i.injection_id for i in PILOT_INJECTIONS}
    if only - ids:
        raise ValueError(f"unknown injection id(s): {sorted(only - ids)}")
    return [i for i in PILOT_INJECTIONS
            if (not only or i.injection_id in only)
            and (not classes or i.failure_class in classes)]
```

- [ ] **Step 4: Run, verify PASS.** Then **Step 5: Commit** `git add src/eval/graph_repair_ablation/__init__.py src/eval/graph_repair_ablation/oracle.py tests/eval/graph_repair_ablation/test_oracle.py && git commit -m "feat(ablation): injection oracle manifest (5 pilot injections)"`

---

### Task 2: Injection layer (setup.sh mutation)

**Files:** Create `src/eval/graph_repair_ablation/inject.py`; Test `tests/eval/graph_repair_ablation/test_inject.py`

**Interfaces:**
- Consumes: `Injection` (Task 1).
- Produces: `apply_injection(script: str, inj: Injection) -> str` (returns a NEW mutated script; raises `ValueError` on an unknown op or a `strip_line` whose `match` is absent — fail loud, never silently no-op).

- [ ] **Step 1: Write the failing test**

```python
from src.eval.graph_repair_ablation.inject import apply_injection
from src.eval.graph_repair_ablation.oracle import Injection

def _inj(mutation):
    return Injection("x", "r", "img", "SYSLIB_MISSING", mutation, {"kind": "install", "target": "t"})

def test_strip_line_removes_matching_line():
    script = "apt-get install -y libgraphviz-dev pkgconf\npip install -e .\n"
    out = apply_injection(script, _inj({"op": "strip_line", "match": "libgraphviz-dev"}))
    assert "libgraphviz-dev" not in out
    assert "pip install -e ." in out          # other lines preserved

def test_strip_line_absent_match_raises():
    import pytest
    with pytest.raises(ValueError):
        apply_injection("pip install -e .\n", _inj({"op": "strip_line", "match": "NOPE"}))

def test_add_install_pkg_appends_bad_pip_pkg():
    out = apply_injection("pip install -e .\n", _inj({"op": "add_install_pkg", "pkg": "badpkg==0.0.0"}))
    assert "badpkg==0.0.0" in out

def test_add_pin_appends_conflicting_pin():
    out = apply_injection("pip install -e .\n", _inj({"op": "add_pin", "pkg": "urllib3", "spec": "==1.20"}))
    assert "urllib3==1.20" in out

def test_unknown_op_raises():
    import pytest
    with pytest.raises(ValueError):
        apply_injection("x\n", _inj({"op": "frobnicate"}))
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement `inject.py`**

```python
"""Perturb a rendered setup.sh to plant a KNOWN-root-cause failure that survives
construction. Pure text transform — returns a new script, never mutates input."""
from __future__ import annotations

from src.eval.graph_repair_ablation.oracle import Injection

_PIP_LINE_HINTS = ("pip install", "pip3 install", "python -m pip install")


def apply_injection(script: str, inj: Injection) -> str:
    op = inj.mutation.get("op")
    if op == "strip_line":
        needle = inj.mutation["match"]
        lines = script.splitlines(keepends=True)
        kept = [ln for ln in lines if needle not in ln]
        if len(kept) == len(lines):
            raise ValueError(f"strip_line match {needle!r} not found in script")
        return "".join(kept)
    if op in ("add_install_pkg", "add_pin"):
        token = (inj.mutation["pkg"] if op == "add_install_pkg"
                 else f'{inj.mutation["pkg"]}{inj.mutation["spec"]}')
        # append an explicit pip install of the offending token as the last step
        # (deterministic; independent of how the base script installs deps).
        sep = "" if script.endswith("\n") else "\n"
        return f"{script}{sep}pip install {token}\n"
    raise ValueError(f"unknown injection op: {op!r}")
```

- [ ] **Step 4: Run, verify PASS. Step 5: Commit** the 2 files.

---

### Task 3: Context providers (the arm treatments)

**Files:** Create `src/eval/graph_repair_ablation/context.py`; Test `tests/eval/graph_repair_ablation/test_context.py`

**Interfaces:**
- Consumes: `DepGraph`, `Node` (`python_deps.depgraph.schema`).
- Produces: `flat_list_context(graph) -> str` (dep names+versions, NO structure) and `graph_context(graph, symptom_ids=()) -> str` (tiered nodes + their `chosen_fix` + the requires-neighborhood). Both are the strings appended to the repair scope per arm.

- [ ] **Step 1: Write the failing test** (build a tiny fixture DepGraph with a Package, a Tool with `chosen_fix`, and a requires edge)

```python
from python_deps.depgraph.schema import DepGraph, Node, Edge, NodeType, Layer, DiscoveredBy
from src.eval.graph_repair_ablation.context import flat_list_context, graph_context

def _graph():
    proj = Node("project:r", NodeType.PROJECT, "r", Layer.PIP, DiscoveredBy.GOAL)
    pkg = Node("pkg:requests==2.0", NodeType.PACKAGE, "requests", Layer.PIP,
               DiscoveredBy.RESOLVER, version="2.0")
    tool = Node("tool:libgraphviz-dev", NodeType.TOOL, "libgraphviz-dev", Layer.TOOLCHAIN,
                DiscoveredBy.RESOLVER, chosen_fix="apt:libgraphviz-dev")
    g = DepGraph((proj, pkg, tool))
    g = g.with_edge(Edge("project:r", "pkg:requests==2.0"))
    g = g.with_edge(Edge("project:r", "tool:libgraphviz-dev"))
    return g

def test_flat_list_has_names_but_no_structure():
    s = flat_list_context(_graph())
    assert "requests" in s and "2.0" in s
    for structural in ("tier", "requires", "TOOL", "chosen_fix", "apt:"):
        assert structural not in s

def test_graph_context_exposes_tier_fix_and_edges():
    s = graph_context(_graph())
    assert "apt:libgraphviz-dev" in s          # the fix hint
    assert "tier" in s.lower()                  # tier structure
    assert "requires" in s.lower()              # an edge relation
    assert "libgraphviz-dev" in s and "requests" in s
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement `context.py`** (pure; read `Node`/`DepGraph` fields only)

```python
"""The two arm treatments, as strings appended to the repair scope.

flat_list_context (C0.5) = the dependency INFORMATION with none of the structure.
graph_context     (C1)   = the same needs, but typed + tiered + provenance + the
                           chosen_fix hint + the requires-neighborhood (the STRUCTURE)."""
from __future__ import annotations

from python_deps.depgraph.schema import DepGraph, NodeType


def flat_list_context(graph: DepGraph) -> str:
    names = []
    for n in graph.nodes:
        if n.type is NodeType.PACKAGE:
            names.append(f"{n.name}=={n.version}" if n.version else n.name)
    names.sort()
    return "Declared dependencies:\n" + "\n".join(f"- {x}" for x in names)


def graph_context(graph: DepGraph, symptom_ids: tuple[str, ...] = ()) -> str:
    lines = ["Dependency graph (typed, tiered):"]
    for n in sorted(graph.nodes, key=lambda x: (x.tier, x.type.value, x.name)):
        if n.type in (NodeType.PROJECT, NodeType.TEST, NodeType.IMPORT):
            continue
        fix = f"  fix={n.chosen_fix}" if n.chosen_fix else ""
        prov = n.discovered_by.value
        lines.append(f"- [{n.type.value} tier={n.tier}] {n.name} "
                     f"state={n.state.value} via={prov}{fix}")
    # requires-neighborhood so the agent can walk symptom -> cause
    lines.append("\nrequires edges (src requires dst):")
    for e in graph.edges:
        lines.append(f"- {e.src} requires {e.dst}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run, verify PASS. Step 5: Commit** the 2 files.

---

### Task 4: Localization grader

**Files:** Create `src/eval/graph_repair_ablation/grade.py`; Test `tests/eval/graph_repair_ablation/test_grade.py`

**Interfaces:**
- Consumes: `Injection` (Task 1); a `Trace` = `{"actions": list[str], "patch": dict | None}` where `actions` are the read-only shell commands the agent ran (in order) and `patch` is the final `PatchProposal.to_dict()` (or a simplified `{"kind","target"}` the runner extracts — Task 5 defines the extraction).
- Produces: `LocalizationScore` frozen dataclass + `grade_localization(trace, inj) -> LocalizationScore` with fields `localized_at_1, localized_at_3: bool`, `first_correct_rank: int | None`, `mislocalized: bool`, `wasted_rate: float`, `success_action: dict | None`.

Grading rule (canonicalized target match):
- **install**: an action or the patch names the target package (`apt:libX-dev` → match on `libX-dev`; `pip` pkg → match on the name via `canon_pip`).
- **drop**: the patch's kind is a drop/skip of the target need (NOT an install of it). An action that tries to *install* the drop-target is a WASTED/mislocalizing action.
- **repin**: an action or the patch changes the target package's pin.
- `first_correct_rank` = 1-based index of the first correct-target action, or `len+1` if only the final patch is correct, or `None` if never.
- `mislocalized` = the final patch targets the wrong root cause (or, for drop-class, tries to install).
- `wasted_rate` = (# actions that don't touch the correct target) / max(1, # actions).

- [ ] **Step 1: Write the failing test** (one per class + wasted/mislocalized)

```python
from src.eval.graph_repair_ablation.grade import grade_localization
from src.eval.graph_repair_ablation.oracle import PILOT_INJECTIONS

BY = {i.failure_class: i for i in PILOT_INJECTIONS}

def test_install_class_localized_at_1():
    inj = BY["SYSLIB_MISSING"]  # correct target apt:libgraphviz-dev
    trace = {"actions": ["apt-cache search libgraphviz-dev"], "patch": {"kind": "install", "target": "apt:libgraphviz-dev"}}
    s = grade_localization(trace, inj)
    assert s.localized_at_1 and s.first_correct_rank == 1 and not s.mislocalized

def test_install_class_wasted_actions_before_localizing():
    inj = BY["SYSLIB_MISSING"]
    trace = {"actions": ["pip show requests", "ls /tmp", "apt-cache search libgraphviz-dev"],
             "patch": {"kind": "install", "target": "apt:libgraphviz-dev"}}
    s = grade_localization(trace, inj)
    assert not s.localized_at_1 and s.localized_at_3
    assert s.first_correct_rank == 3
    assert abs(s.wasted_rate - 2/3) < 1e-6

def test_drop_class_install_attempt_is_mislocalization():
    inj = BY["OVERINCLUDE"]  # correct action: drop the optional pkg
    trace = {"actions": ["pip install this-optional-pkg-fails-to-build"],
             "patch": {"kind": "install", "target": "this-optional-pkg-fails-to-build"}}
    s = grade_localization(trace, inj)
    assert s.mislocalized and not s.localized_at_3

def test_drop_class_correct_when_patch_drops():
    inj = BY["OVERINCLUDE"]
    trace = {"actions": ["cat requirements.txt"], "patch": {"kind": "drop", "target": "this-optional-pkg-fails-to-build"}}
    s = grade_localization(trace, inj)
    assert s.localized_at_3 and not s.mislocalized

def test_repin_class():
    inj = BY["VERSION_CONFLICT"]  # target urllib3
    trace = {"actions": ["pip index versions urllib3"], "patch": {"kind": "repin", "target": "urllib3"}}
    s = grade_localization(trace, inj)
    assert s.localized_at_3 and not s.mislocalized
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement `grade.py`** (pure; canonicalize with `coverage.canon_pip`)

```python
"""Deterministic localization grader: match an agent's diagnostic actions + final
patch against the injection oracle's correct_action. Pure, no LLM."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from eval.language_package_eval.coverage import canon_pip  # noqa: E402
from src.eval.graph_repair_ablation.oracle import Injection  # noqa: E402


@dataclass(frozen=True)
class LocalizationScore:
    localized_at_1: bool
    localized_at_3: bool
    first_correct_rank: int | None
    mislocalized: bool
    wasted_rate: float
    success_action: dict | None


def _target_tokens(action_target: str) -> set[str]:
    """Canonical tokens for a target string ('apt:libgraphviz-dev' -> {'libgraphviz-dev'};
    'requests' -> {canon_pip('requests')})."""
    raw = action_target.split(":", 1)[1] if ":" in action_target else action_target
    return {raw, canon_pip(raw)}


def _action_hits_target(text: str, target: str) -> bool:
    toks = _target_tokens(target)
    low = text.lower()
    return any(t and t.lower() in low for t in toks)


def grade_localization(trace: dict, inj: Injection) -> LocalizationScore:
    kind = inj.correct_action["kind"]
    target = inj.correct_action["target"]
    actions = trace.get("actions", []) or []
    patch = trace.get("patch")

    # per-action correctness: does the action touch the correct target?
    ranks = [i + 1 for i, a in enumerate(actions) if _action_hits_target(a, target)]
    # a drop-class action that INSTALLS the target is NOT localizing; it's the wrong move.
    if kind == "drop":
        ranks = [i + 1 for i, a in enumerate(actions)
                 if _action_hits_target(a, target) and "install" not in a.lower()]

    first_action_rank = ranks[0] if ranks else None

    # final-patch correctness
    patch_correct = False
    mislocalized = False
    if patch:
        p_kind, p_target = patch.get("kind"), patch.get("target", "")
        hits = _action_hits_target(p_target, target)
        if kind == "drop":
            patch_correct = (p_kind == "drop" and hits)
            mislocalized = (p_kind == "install" and hits) or (not hits and p_kind is not None)
        else:
            patch_correct = (p_kind == kind and hits) or hits
            mislocalized = bool(p_target) and not hits

    # first_correct_rank folds in the final patch as rank = len(actions)+1
    first_correct_rank = first_action_rank
    if first_correct_rank is None and patch_correct:
        first_correct_rank = len(actions) + 1

    localized_at_1 = first_correct_rank == 1
    localized_at_3 = first_correct_rank is not None and first_correct_rank <= 3
    wasted = sum(1 for a in actions if not _action_hits_target(a, target))
    wasted_rate = wasted / max(1, len(actions))
    success = {"kind": kind, "target": target} if patch_correct else None
    return LocalizationScore(localized_at_1, localized_at_3, first_correct_rank,
                             mislocalized, wasted_rate, success)
```

- [ ] **Step 4: Run, verify PASS. Step 5: Commit** the 2 files.

---

### Task 5: Runner core — `run_one` (agent wiring, mock-testable)

**Files:** Create `src/eval/graph_repair_ablation/run.py` (part 1); Test `tests/eval/graph_repair_ablation/test_run_one.py`

**REAL INTERFACES (confirmed by recon — see `scratchpad/task5-recon-report.md`; the earlier draft of this task had them WRONG):**
- `V3BuildAgent(client, model).propose(scope, exec_readonly, *, max_diag_turns) -> PatchProposal | None`.
- `exec_readonly` is a **plain callable** `Callable[[str], tuple[int, str]]` (returns `(rc, combined_out)`), NOT a `.run()`/`CommandResult` object (call site `v3_build_agent.py:205` `rc, out = exec_readonly(action)`).
- `render_repair_scope(scope)` is called **inside** `propose()` — you CANNOT pre-render + concatenate. Inject the arm context by patching the name at runtime: `unittest.mock.patch("src.envstate.v3_build_agent.render_repair_scope", augmented)` around the `propose()` call. (This does NOT edit `v3_build_agent.py`.)
- Scope built via `src.envstate.repair_scope.build_repair_scope(graph, *, target_node_id, failed_block, bundle=None, known_invalid=(), constraints=None) -> RepairScope`. For the pilot use `target_node_id = <the project node id>` (always present), `failed_block = mutated_script`, `bundle=None`.
- `PatchProposal` (`src/python_deps/depgraph/patch.py`) is **additive-only** — `add_requirements: tuple[NodeSpec]`, `add_providers: tuple[ProviderSpec]` (ProviderSpec has `id, kind, command, provides`), `add_edges`, `script_patches: tuple[ScriptPatch]` (has `target`), `request_checks`. **No `to_dict`, no top-level `kind`/`target`, and no native "drop"/"repin".** `propose()` returns a non-empty `PatchProposal` or `None`.
- LLM client for the live run: mirror `scripts/run_v3_e2e.py::_run()` — `OpenAI(api_key, base_url, max_retries=0, timeout=...)` with the env fallback chain; `model` from the `--model` arg. (Live-run only; unit tests use a fake client.)

**Interfaces produced:**
- `ARMS = ("C0", "C1")`; `arm_context(arm, graph) -> str` (`""` for C0, `graph_context(graph)` for C1).
- `RecordingExec` — adapts a `_MountedContainer` to the callable `exec_readonly` contract AND records every command:
  ```python
  class RecordingExec:
      def __init__(self, box): self.box, self.calls = box, []
      def __call__(self, command: str) -> tuple[int, str]:
          self.calls.append(command)
          r = self.box.run(command)
          return (r.returncode, (r.stdout or "") + (r.stderr or ""))
  ```
- `normalize_patch(proposal, inj) -> dict | None` — real `PatchProposal` → the `{"kind","target"}` shape `grade_localization` consumes. **Injection-aware** (the only way to express the classes given the additive schema):
  - `proposal is None` → `None`.
  - For a **drop-class** injection (`inj.correct_action["kind"]=="drop"`): if the proposal adds/installs the phantom (`add_providers`/`add_requirements` naming `inj.correct_action["target"]`, or an action did `pip install <phantom>`) → `{"kind":"install","target":phantom}` (a mislocalization the grader will flag); otherwise → `{"kind":"drop","target":phantom}` (the agent avoided the install-thrash, i.e. recognized it as optional). *(Pilot convention — the additive PatchProposal has no native drop; a `script_patches` entry removing the phantom also counts as drop. Document this caveat in the report + the final table.)*
  - For **install/repin**: `add_providers[0]` → `{"kind": inj.correct_action["kind"], "target": provides[0] or id}`; else `add_requirements[0]` → `{"kind": ..., "target": name or id}`; else `None`.
- `run_one(inj, arm, *, agent_client, model, smoke_root, max_diag_turns=4) -> dict` → `{"injection_id","arm","failure_class","trace","score","install_failed"}` where `score` is `LocalizationScore.__dict__`.

**`run_one` flow:**
1. `repo_dir = Path(smoke_root)/inj.repo`; `image, minor, _ = base_image_for_repo(str(repo_dir))`.
2. `graph = build_graph_construction_only(str(repo_dir), image, minor)`; `script = render_build_script(graph, ())`.
3. `mutated = apply_injection(script, inj)`.
4. `with _MountedContainer(image, str(repo_dir)) as box:` write + run `mutated` (`box.run("cd <dir> && bash -x /setup.sh")`). If it did NOT fail, return `install_failed=False` and skip (log loudly — a corpus/injection bug, never silently counted).
5. `scope = build_repair_scope(graph, target_node_id=<project node id>, failed_block=mutated, bundle=None)`.
6. `rec = RecordingExec(box)`; augment render for the arm and run the agent:
   ```python
   import unittest.mock as _m
   from src.envstate.v3_build_agent import render_repair_scope as _rrs
   def _aug(scope):
       ctx = arm_context(arm, graph)
       return _rrs(scope) + (("\n\n" + ctx) if ctx else "")
   with _m.patch("src.envstate.v3_build_agent.render_repair_scope", _aug):
       proposal = V3BuildAgent(agent_client, model).propose(scope, rec, max_diag_turns=max_diag_turns)
   ```
7. `trace = {"actions": rec.calls, "patch": normalize_patch(proposal, inj)}`; `score = grade_localization(trace, inj)`; return the dict.

**Unit test (fake client + fake box — NO Docker, NO LLM).** Provide a fake `agent_client` whose `.chat.completions.create(...)` returns a scripted object (one `Action: apt-cache search libgraphviz-dev` turn, then a `Final Patch` with a fenced ```json add_providers patch). Monkeypatch the module-level `base_image_for_repo`, `build_graph_construction_only`, `render_build_script`, and `_MountedContainer` (a `_FakeBox` returning a failing setup.sh CommandResult then rc0 diagnostics — reuse the shape from `tests/eval/build_script_eval/test_replay_ladder.py`). Assert: (a) `rec.calls` captured the agent's action(s); (b) `normalize_patch` produced the expected `{kind,target}`; (c) **the augmented render for `arm="C1"` contains the `graph_context` block and for `arm="C0"` does not** (capture what `_aug` returns for each arm). Also unit-test `normalize_patch` and `RecordingExec` directly (pure).

- [ ] Step 1: failing tests (the run_one integration test with mocks + direct `normalize_patch`/`RecordingExec` tests).
- [ ] Step 2: run, verify FAIL.
- [ ] Step 3: implement `run.py` part 1 per the REAL interfaces above. Do NOT edit `v3_build_agent.py`/`repair_scope.py`/`patch.py` — reuse-by-import + the runtime `mock.patch` only.
- [ ] Step 4: run ONLY `tests/eval/graph_repair_ablation/test_run_one.py`, verify PASS. **Do NOT `git add`/commit.**

---

### Task 6: CLI + aggregation + report

**Files:** Modify `src/eval/graph_repair_ablation/run.py` (add `aggregate`); Create `src/eval/graph_repair_ablation/__main__.py`; Test `tests/eval/graph_repair_ablation/test_aggregate.py`

**Interfaces:**
- Produces: `aggregate(results: list[dict]) -> dict` — per `(failure_class, arm)`: mean `localized_at_1`, `localized_at_3`, mean `first_correct_rank` (finite only), mean `wasted_rate`, `mislocalized` count. `render_report_md(agg) -> str`. CLI: `python3 -m src.eval.graph_repair_ablation --run [--only id,..] [--class ..] [--arms C0,C1] [--model <slug>]`.

- [ ] **Step 1: Write the failing test** for `aggregate` with synthetic result dicts (two classes × two arms), asserting the C1-vs-C0 localized@1 comparison is computed per class.

```python
from src.eval.graph_repair_ablation.run import aggregate

def _r(cls, arm, l1, wasted=0.0, mis=False):
    return {"failure_class": cls, "arm": arm,
            "score": {"localized_at_1": l1, "localized_at_3": l1, "first_correct_rank": 1 if l1 else None,
                      "mislocalized": mis, "wasted_rate": wasted, "success_action": None}}

def test_aggregate_groups_by_class_and_arm():
    agg = aggregate([_r("SYSLIB_MISSING","C0",False,0.5), _r("SYSLIB_MISSING","C1",True,0.0)])
    assert agg[("SYSLIB_MISSING","C1")]["localized_at_1"] == 1.0
    assert agg[("SYSLIB_MISSING","C0")]["localized_at_1"] == 0.0
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement `aggregate` + `render_report_md` + `__main__.py`.** The CLI: `select` injections → for each × arm build a real `agent_client` (reuse the model-client construction the codebase already uses for `V3BuildAgent`; read how `complete_with_retry`'s client is built) → `run_one` → collect → `aggregate` → write `outputs/graph_repair_ablation/report.md` + `results.json`. One repo/injection must not abort the batch (per-injection try/except, log SKIP).
- [ ] **Step 4: Run unit tests, verify PASS.**
- [ ] **Step 5: Commit** the 3 files.
- [ ] **Step 6 (controller-run gate, NOT a unit test): the live pilot.** `python3 -m src.eval.graph_repair_ablation --run --arms C0,C1` (Docker foreground; macOS has no `timeout`; use `python3 -u` + `tee`). Inspect the per-class C0-vs-C1 localized@1 / wasted_rate table. **Success signal for the pilot:** C1 localizes at least as well as C0 on ≥3/5 classes and strictly better on the OVERINCLUDE (drop) class — enough to justify scaling to the full 3-arm multi-seed experiment. Record the table + a go/no-go note.

---

## Post-pilot (out of scope here, for the next plan)
Add C0.5 (flat-list) + the C1-strong (pre-computed binding) sensitivity arm; multi-seed (N≥3) with bootstrap CIs; the LLM-judge grader cross-check; scale the injection corpus to ≥2 per class. Gate: the pilot must show the effect first.
