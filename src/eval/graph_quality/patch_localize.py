"""Patch localization -- is the ★ on the RIGHT node? (design spec §4, plan Task 4)

THE QUESTION: the graph's *render* is the product the agent actually sees. Does the
★ (ACTIONABLE marker) `render_graph_context` emits land on the node whose fix is the
one that actually matters? A graph that KNOWS the answer but buries it among twelve
★s is a failed product -- root-hit@1 alone would score that graph 100%.

🔴 STAR PRECISION IS MANDATORY ALONGSIDE root-hit. Every table this module prints
reports BOTH, plus the raw `n_stars`, never root-hit in isolation.

🔴 REWORK (gpt-5.6-terra adversarial review, superseding the version committed at
3ee51a6): that version was CIRCULAR. `_SCENARIO_BUILDERS` hand-constructed `Node`/
`Edge` objects with hand-set `chosen_fix` and hand-built edges straight to the
"correct" root, then rendered them -- so `root_hit=3/5` measured the GRADER's own
fixtures, not the graph. Worse, it fabricated `chosen_fix` values the real resolver
cannot produce (`libcgraph.so.6 -> apt:libgraphviz-dev` is not in
`os_resolver.PROVIDER_TABLE`). This is the same bug class `tests/react_repair/
test_graph_arm_e2e.py` exists to guard against (see that file's docstring): a
hand-built `chosen_fix` hid the `tool:pg_config` vs `binary:pg_config` node-fracture
bug from 2001 passing tests.

THE FIX. Nothing here hand-builds a capability node, an edge to a root, or a
`chosen_fix` any more. For each of the 5 real `PILOT_INJECTIONS` this module now
hand-authors ONLY two things -- see `_SCENARIO_BUILDERS` below -- and feeds them
through the REAL production chain, byte for byte the same order
`src/react_repair/loop.py`'s `build_and_test` uses every turn:

    (a) the SEED graph: declared PACKAGE node(s) + the TEST goal node. Nothing else.
        No capability nodes, no `chosen_fix`, no edge to a "root".
        ONE DECLARED EXCEPTION, and it is worth naming here rather than burying it in
        the scenario: `_scenario_conflict_requests` (VERSION_CONFLICT) also hand-builds
        a CONFLICTS_WITH edge, because a real one is minted by a LIVE resolver
        (uv/pip, fetching metadata over the network) and that is outside the OFFLINE
        Global Constraint. It cannot flatter the headline -- that row is
        `not_applicable` and is excluded from the root-hit denominator -- and what it
        proves is a structural guarantee of `verdict()`, not a claim about any
        resolver's opinion. Every OTHER cell is real-chain-derived end to end.
    (b) the ERROR TEXT: what a real container emits for this failure class (a raw
        pytest traceback block fed through the real `pytest_summary.summarize`, or a
        raw build-failure string used as `RunResult.output`).

    graph, new_ids = enrich(seed, result, causes, RepoContext())      # REAL
    graph, _       = expand_discovery(graph, new_ids, executor)       # REAL
    graph          = certify_only(graph, appended, executor)          # REAL
    text           = render_graph_context(graph, result, causes, {})  # REAL

`executor` is `enrich_replay._OfflineExecutor` (reused, not reimplemented): every
off-table resolution and every `check_command` fails HONESTLY (rc=127) rather than
being faked, so a capability the real resolver cannot answer stays UNRESOLVED
(`chosen_fix=None`) instead of being handed an invented fix. `certify_only` is the
one step `enrich_replay.py` does not need (it grades discovery/resolution, not the
render) but this module does: without it every runtime-discovered node stays
`State.UNKNOWN` (`runtime_ingest._node_for_discovery`'s own docstring: "certify owns
it") and `verdict()` can never call it ACTIONABLE, so it could never be starred at
all -- see `_replay_turn` below for the exact wiring, mirrored from `loop.py`.

WHAT FELL OUT ONCE THE GRAPH CAME FROM REAL CODE (verified by literally running the
chain above -- see the module's own `_smoke` and `tests/eval/graph_quality/
test_patch_localize.py`, and see the docstrings on the two "structurally
unstarrable" scenarios below for why 2 of the 5 cells can never contribute a root
hit at all):

  1. root_hit dropped from a CIRCULAR 3/5 to an HONEST 1/3 (over the valid
     denominator -- see "THE DENOMINATOR" below). The one surviving hit
     (COMPILER_ABSENT / pyzmq -> gcc -> apt:build-essential) is a genuine table hit
     AND a genuine owner-anchored discovery; nothing was tuned to make this line up.

  2. SYSLIB_MISSING (pygraphviz): `enrich()` DOES discover the SystemLib node
     (`syslib:libcgraph.so.6`, via the real `classify_dependency_failure`'s
     `native_library_missing` path) -- but the render can never surface it, for TWO
     independent, compounding reasons, both confirmed by literally running the
     chain, not assumed:

       (a) `os_resolver.PROVIDER_TABLE` has no `("soname", "libcgraph.so.6")` entry.
           `expand_discovery`'s `_resolve_fix` tries the offline executor's
           apt-file fallback and gets an honest rc=127; `chosen_fix` stays `None`.
           This is a genuine PROVIDER_TABLE coverage gap -- reported, not patched
           (Global Constraints: never edit `src/python_deps/`).

       (b) EVEN IF (a) were fixed, the render still could not show it:
           `graph_context._anchor_for_cause` -- the function that decides which
           graph node a PYTEST cause is "about" -- only matches a QUOTED name in
           `Cause.detail` against a Package node's canonical name (this is exactly
           right for the dominant real-corpus shape, `ModuleNotFoundError: No
           module named 'x'`). A native-library ImportError names the .so file,
           never the package (`ImportError: libcgraph.so.6: cannot open shared
           object file...` -- no quotes anywhere), so `_anchor_for_cause` returns
           `None` and the render falls back to its "NO GRAPH EXPLANATION" note.
           `enrich()` graphed the capability correctly; the renderer never finds an
           anchor to reach it from. This is a render-anchor gap in
           `graph_context.py`, separate from (a), and NOT something this eval fixes.

  3. TOOL_ABSENT (python-semantic-release / GitPython): the ENTIRE discovery layer
     comes up empty -- no node at all, not even an unresolved one. GitPython's REAL
     import-time failure (empirically reproduced: `pip install GitPython` into a
     scratch venv, hide `git` from `$PATH`, `import git`) raises
     `ImportError: Failed to initialize: Bad git executable.` --
     `python_deps.failure_classifier.classify_tool_error` recognizes THREE shapes:
     `"<name>: command not found"` / `"<name>: not found"`
     (`_TOOL_COMMAND_NOT_FOUND_RE`), `"<name> executable not found"`
     (`_TOOL_EXECUTABLE_NOT_FOUND_RE`, modeled on setuptools/psycopg2's `pg_config`
     message), and a `FileNotFoundError` naming the binary
     (`_TOOL_FILENOTFOUNDERROR_RE`). GitPython's real wording matches NONE of the
     three, so `classify_observation` returns `None` and `enrich()` appends nothing.
     `git` also happens to be absent from `PROVIDER_TABLE` (confirmed), but that is
     moot here: the failure never reaches the resolver at all. Reported, not patched.

     The shape of this gap is worth stating precisely, because it is the actionable
     part: the classifier keys on the SHELL's vocabulary for a missing executable,
     and GitPython reports the same physical fact in a LIBRARY's vocabulary ("Bad git
     executable"). Every tool whose absence is first noticed by a Python wrapper
     rather than by the shell is invisible to this layer.

  4. STAR PRECISION IS NO LONGER DECORATIVE. The circular version's only
     `n_stars > 1` case (`test_star_precision_punishes_a_graph_that_stars_everything`)
     bypassed the renderer and hand-fed `grade_stars` a 12-id `frozenset` --
     arithmetic on numbers nobody rendered. This module now measures precision on a
     GENUINELY rendered graph with multiple simultaneous actionable candidates (four
     independently-missing declared packages, one of which is the grading target;
     see `test_star_precision_is_measured_on_a_real_render_with_multiple_candidates`
     in the test module) -- `n_stars=4`, `star_precision=0.25`, both MEASURED, not
     assumed. The 5 real injections themselves turn out to be a poor vehicle for
     this: a build-fail turn has exactly one `failing_command` anchor, and a
     pytest-stream turn's other causes are structurally excluded from a single
     injection's own scenario, so none of the 5 cells naturally produces more than
     one candidate; the precision-discriminating test above is therefore a
     deliberately separate, sixth scenario, not one of `PILOT_INJECTIONS`.

  5. THE COLLAPSE TEST NOW ACTUALLY MINTS THE NODE VIA THE REAL PRODUCERS. The old
     version hand-built ONE `binary:pg_config` node and pointed two packages at it
     -- proving only that the renderer dedups an already-correct graph, exactly the
     shape that let the real `tool:pg_config`/`binary:pg_config` fracture (commit
     b5a9f65) hide behind 2001 green tests. The rebuilt test seeds ONLY the two
     declared packages (psycopg2, asyncpg -- no pg_config anywhere) and drives TWO
     independent `_replay_turn` calls, one per package's own real build failure
     (`Error: pg_config executable not found.`), so the capability node is minted
     by the SAME real discovery path (`runtime_classify._id_for_discovery`'s TOOL
     branch -> `capability_id("binary", "pg_config")`) twice, from two different
     owners, and asserts the two calls converge onto one node.

THE DENOMINATOR: 2 of the 5 `PILOT_INJECTIONS` are UNSTARRABLE BY CONSTRUCTION and
counting them as root-hit misses would be dishonest, not merely pessimistic:

  * VERSION_CONFLICT (`kind: "repin"`): `verdict()` (`graph_context.py`) returns
    BLOCKED for a node on a CONFLICTS_WITH edge BEFORE it can ever return
    ACTIONABLE, and BLOCKED renders `✖`, never `★` (verified below by literally
    rendering the shape). A repin target can never be starred, full stop.
  * OVERINCLUDE (`kind: "drop"`): `inject.apply_injection`'s `add_install_pkg` op
    appends the phantom package to the RENDERED SCRIPT TEXT, after construction ran
    -- no node for it ever exists to seed, let alone star (verified below: the real
    chain, run honestly, produces no node and the render's own "BUILD FAILED -- NO
    GRAPH EXPLANATION" degrade path fires).

`LocalizeScore.not_applicable` marks both; `build_report`'s printed root-hit ratio
is over the denominator that EXCLUDES them (3, not 5). They still get their own
report line -- excluding them from the ratio is not the same as hiding them.

🔴 THERE ARE ONLY 5 INJECTION ENTRIES -- one per `FAILURE_CLASSES` member. Five (3
after excluding the structurally-N/A pair) is a SMOKE TEST, not a measurement.
Every report this module prints says so; never read these numbers as a rate.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from python_deps.depgraph.diagnose import RepoContext  # noqa: E402
from python_deps.depgraph.discovery_expand import expand_discovery  # noqa: E402
from python_deps.depgraph.graph_context import in_conflict, render_graph_context  # noqa: E402
from python_deps.depgraph.graph_enrich import certify_only, enrich  # noqa: E402
from python_deps.depgraph.ids import TEST_NODE_ID, package_id  # noqa: E402
from python_deps.depgraph.naming import normalize_package_name  # noqa: E402
from python_deps.depgraph.schema import (  # noqa: E402
    DepGraph, DiscoveredBy, Edge, EdgeType, Layer, Node, NodeType, State,
)

from src.eval.graph_quality.enrich_replay import _OfflineExecutor  # noqa: E402
from src.eval.graph_repair_ablation.oracle import (  # noqa: E402
    FAILURE_CLASSES, Injection, PILOT_INJECTIONS,
)
from src.react_repair.loop import RunResult  # noqa: E402
from src.react_repair.pytest_summary import Cause, summarize  # noqa: E402


@dataclass(frozen=True)
class LocalizeScore:
    root_hit: bool          # is `correct_action.target` reachable from the ★ set at all?
    star_precision: float   # |★ that hit the target| / |★| -- 0.0 when n_stars == 0
    n_stars: int            # raw count -- the denominator star_precision hides
    mislocalized: bool      # a CONFLICTS_WITH node ended up in the ★ set (never should)
    # Structurally unstarrable BY CONSTRUCTION (repin/drop kind -- see module docstring
    # "THE DENOMINATOR"). EXCLUDED from the root-hit ratio; reported on its own line.
    not_applicable: bool = False
    # A TOOL/SystemLib node this turn's chain discovered has NO `chosen_fix` -- a real
    # `os_resolver.PROVIDER_TABLE` coverage gap (or an offline apt-file miss), surfaced
    # honestly rather than papered over. Independent of whether the node got starred:
    # a capability can be resolver-unresolved AND render-unreachable at the same time
    # (see SYSLIB_MISSING in the module docstring) -- this flag reports the FORMER only.
    unresolved_capability: bool = False


# --------------------------------------------------------------------------- #
# stars() -- parse the ★ ids back OUT of the REAL rendered text.
# --------------------------------------------------------------------------- #

# Only the RECORD header line (`★ <id>    STATE`, column 0 -- see graph_context.py's
# `_record`) is matched. `_down_edges` ALSO appends an inline trailing "  ★" to a
# SUBGRAPH edge line for the same ACTIONABLE dst (`_fmt_state(dst)}{mark}`) -- that
# annotation has no leading "★ <id>" shape (the id sits earlier on the line, before
# "--requires-->") and matching it too would double-count a node that is already a
# full record. Every node that gets the inline mark also gets a full record (both come
# from the same ACTIONABLE check on the same `follow` list), so parsing only the
# records loses no information.
_STAR_LINE_RE = re.compile(r"^★ (\S+)", re.MULTILINE)


def stars(graph: DepGraph, result, causes, *, prev_states: dict | None = None,
          repo_path: str | None = None, target_env: dict | None = None) -> frozenset[str]:
    """Render via the REAL `render_graph_context` and return the set of node ids that
    got a full ★ record. `records` inside the renderer is a dict keyed by node id
    (`records.setdefault(cand.id, cand)`) -- so when two independent failures share one
    root, the render ALREADY collapses them onto one entry before this function ever
    sees the text; `stars()` does not need to (and must not) do any de-duplication of
    its own for that to hold."""
    text = render_graph_context(graph, result, causes, prev_states or {},
                                repo_path=repo_path, target_env=target_env)
    return frozenset(_STAR_LINE_RE.findall(text))


# --------------------------------------------------------------------------- #
# grade_stars() -- pure grading arithmetic, independent of the render pipeline so the
# FORMULA itself (and its conflict safety-net) is unit-testable on its own.
# --------------------------------------------------------------------------- #

def _node_hits_target(node: Node, target: str) -> bool:
    """Does `node` correspond to `correct_action["target"]`?

    An install-class target names a PROVIDER id (`"apt:libpq-dev"`) -- `node.chosen_fix`
    is the strongest signal, since it is literally the fix that node would emit. A
    repin/drop-class target names a bare PACKAGE name (`"urllib3"`) -- matched against
    the node's own canonical name for the (frequent, but not universal -- see `gcc` vs
    `build-essential`) case where a capability's own name coincides with its target.
    """
    if node.chosen_fix is not None and node.chosen_fix == target:
        return True
    bare = target.split(":", 1)[-1]
    return bool(node.name) and normalize_package_name(node.name) == normalize_package_name(bare)


def grade_stars(stars: frozenset[str], target: str, graph: DepGraph) -> LocalizeScore:
    """Score an ALREADY-EXTRACTED star set against `target`.

    `star_precision` is 0.0 (never a ZeroDivisionError, never `None`-as-silence) when
    `n_stars == 0` -- an honest reading of "the render offered no actionable signal at
    all". `root_hit` is likewise `False`, never crashes, on an empty set.
    `not_applicable`/`unresolved_capability` are NOT this function's concern -- they
    depend on the injection's own declared kind and on the graph's capability nodes
    beyond the star set, both supplied by `grade()`, so they default False here and
    this function stays a pure, standalone-testable formula (as it always was).
    """
    n = len(stars)
    star_nodes = [(sid, graph.get(sid)) for sid in stars]
    hits = {sid for sid, node in star_nodes if node is not None and _node_hits_target(node, target)}
    mislocalized = any(node is not None and in_conflict(graph, node) for _sid, node in star_nodes)
    return LocalizeScore(
        root_hit=bool(hits),
        star_precision=(len(hits) / n) if n else 0.0,
        n_stars=n,
        mislocalized=mislocalized,
    )


# Kinds whose target can NEVER be starred by construction (module docstring, "THE
# DENOMINATOR"). Not a judgement call made per-scenario -- it is read straight off the
# oracle's own `correct_action["kind"]`, real metadata this module did not invent.
_NOT_APPLICABLE_KINDS = frozenset({"repin", "drop"})


def _unresolved_capability_ids(graph: DepGraph) -> tuple[str, ...]:
    """TOOL/SystemLib nodes anywhere in `graph` with no `chosen_fix` -- a resolver
    coverage gap (`os_resolver.PROVIDER_TABLE` miss, offline apt-file fallback also a
    miss). Independent of star-reachability: a capability can be sitting in the graph,
    correctly discovered, and simply never resolved to an installable fix."""
    return tuple(
        n.id for n in graph.nodes
        if n.type in (NodeType.TOOL, NodeType.SYSTEM_LIB) and n.chosen_fix is None
    )


def grade(inj: Injection, graph: DepGraph, result, causes: Sequence = ()) -> LocalizeScore:
    """injected fault -> render -> ★ set -> compare to `inj.correct_action["target"]`
    (spec §4.2), PLUS the two honesty flags this rework adds: `not_applicable` (read
    off the injection's own `kind`) and `unresolved_capability` (read off the graph's
    own capability nodes). `causes` is additive beyond the plan's literal 3-arg
    signature (defaulted to `()`) -- some of the 5 real injections are pytest-stream
    failures and `render_graph_context` needs the causes passed through, not dropped.
    """
    star_ids = stars(graph, result, causes)
    score = grade_stars(star_ids, inj.correct_action["target"], graph)
    return replace(
        score,
        not_applicable=inj.correct_action["kind"] in _NOT_APPLICABLE_KINDS,
        unresolved_capability=bool(_unresolved_capability_ids(graph)),
    )


# --------------------------------------------------------------------------- #
# _replay_turn -- the REAL per-turn chain, mirrored byte-for-byte (in ORDER, not
# copy-pasted code) from `src/react_repair/loop.py`'s `build_and_test`: enrich what
# this turn observed, expand each new discovery through construction's own oracles,
# then certify EVERYTHING either step appended before the next render ever sees it.
# `enrich_replay.py`'s `score_pair` does NOT need this last step -- it grades
# discovery/resolution, not the render -- but this module does: a node `enrich()`
# just appended sits at `State.UNKNOWN` (runtime_ingest's own docstring: "certify
# owns it") and `verdict()` can never call an UNKNOWN node ACTIONABLE, so it could
# never be starred without this. The only non-real thing here is the EXECUTOR --
# `enrich_replay._OfflineExecutor`, reused, not reimplemented: no Docker, no network,
# so every off-table resolution and every check_command fails HONESTLY (rc=127)
# rather than being faked or skipped.
# --------------------------------------------------------------------------- #

def _replay_turn(seed: DepGraph, *, result, causes: Sequence = ()) -> DepGraph:
    executor = _OfflineExecutor()
    before_ids = {n.id for n in seed.nodes}
    graph, new_ids = enrich(seed, result, causes, RepoContext())
    graph, _expanded = expand_discovery(graph, new_ids, executor)
    appended = [n.id for n in graph.nodes if n.id not in before_ids]
    graph = certify_only(graph, appended, executor)
    return graph


def _base_graph() -> DepGraph:
    """What real construction genuinely knows before ANY failure: the TEST goal node.
    Nothing else is ever added here -- capability nodes are for `_replay_turn` to
    discover, never for a scenario builder to pre-seed."""
    return DepGraph().with_node(Node(
        id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
        layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL,
    ))


def _declared_pkg(name: str, version: str, *, state: State = State.MISSING,
                  build_from_source: bool | None = True) -> Node:
    """A declared PACKAGE node -- the only OTHER thing a seed may hand-author.
    `state` here is the pre-turn starting condition (matching
    `tests/react_repair/test_graph_arm_e2e.py`'s own convention of hand-setting a
    seed package's state), not a construction-time inference."""
    return Node(id=package_id(name, version), type=NodeType.PACKAGE, name=name,
                layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                version=version, state=state, build_from_source=build_from_source)


# --------------------------------------------------------------------------- #
# Scenario builders for the 5 real PILOT_INJECTIONS. Each returns (graph, result,
# causes) with the graph produced by the REAL chain above -- except
# `_scenario_conflict_requests`, whose CONFLICTS_WITH edge a live resolver would have
# to produce (needs network/uv; out of Global Constraints' OFFLINE scope) and is
# therefore the one deliberate, clearly-labelled exception. See the module docstring
# for what each scenario's real run actually produced.
# --------------------------------------------------------------------------- #

# Real pytest collection-error block for a `import pygraphviz` that succeeds installing
# but fails at import time on the missing graphviz shared library (oracle note: "strip
# the graphviz -dev apt line -> import fails on libcgraph.so"). Shape verified against
# `pytest_summary.summarize`'s own block/E-line grammar.
_PYGRAPHVIZ_PYTEST_OUTPUT = """\
______________ ERROR collecting tests/test_render.py _______________
ImportError while importing test module '/repo/tests/test_render.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_render.py:3: in <module>
    import pygraphviz
/usr/local/lib/python3.11/site-packages/pygraphviz/__init__.py:29: in <module>
    from . import release
E   ImportError: libcgraph.so.6: cannot open shared object file: No such file or directory
"""


def _scenario_syslib_pygraphviz() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """SYSLIB_MISSING. Seed: ONLY the declared `pygraphviz` package (installs fine --
    the failure is at IMPORT time, per the oracle's own note -- so its pre-turn state
    is SATISFIED) + the TEST node. See the module docstring for what actually happens
    when this runs through the real chain: `enrich()` DOES mint `syslib:libcgraph.so.6`
    (a real `native_library_missing` classification), but it is BOTH unresolved
    (PROVIDER_TABLE has no entry) AND unreachable by the render (`_anchor_for_cause`
    needs a quoted name; a soname ImportError has none) -- root_hit=False for two
    compounding, honestly-surfaced reasons, not a render defect this module invented.
    """
    seed = _base_graph().with_node(
        _declared_pkg("pygraphviz", "1.11", state=State.SATISFIED, build_from_source=False)
    )
    causes = tuple(summarize(_PYGRAPHVIZ_PYTEST_OUTPUT))
    result = RunResult(ok=True)
    graph = _replay_turn(seed, result=result, causes=causes)
    return graph, result, causes


# Real setuptools/distutils message for a missing C compiler (oracle note: "strip
# build-essential -> native build 'gcc failed'"). `error: command 'gcc' failed: No
# such file or directory` is the standard distutils CompileError wording when the
# compiler binary itself cannot be found; `/bin/sh: 1: gcc: not found` is what a
# direct shell invocation logs alongside it.
_GCC_BUILD_OUTPUT = (
    "error: command 'gcc' failed: No such file or directory\n"
    "/bin/sh: 1: gcc: not found\n"
)


def _scenario_compiler_pyzmq() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """COMPILER_ABSENT. Seed: ONLY the declared `pyzmq` package (build_from_source=True,
    genuinely MISSING pre-turn) + the TEST node. This is the ONE cell that survives
    de-circularization: `owner_node_for_command` anchors the failure at `pkg:pyzmq`
    (a real BUILD-stream, owner-anchored discovery -- "depth", per `enrich()`'s own
    docstring), and `os_resolver.PROVIDER_TABLE` DOES carry `("binary","gcc") ->
    "build-essential"` (verified on disk), so `expand_discovery`'s real resolver call
    finds it without ever touching the offline executor's apt-file fallback.
    """
    seed = _base_graph().with_node(_declared_pkg("pyzmq", "25.1.2"))
    result = RunResult(ok=False, failing_command="pip install pyzmq==25.1.2",
                       output=_GCC_BUILD_OUTPUT)
    graph = _replay_turn(seed, result=result, causes=())
    return graph, result, ()


# Realistic modern-pip conflict-resolver wording (oracle note: "append an incompatible
# urllib3 pin -> resolver/runtime conflict").
_URLLIB3_CONFLICT_OUTPUT = (
    "ERROR: Cannot install urllib3==1.20 because these package versions have "
    "conflicting dependencies.\n"
    "The conflict is caused by:\n"
    "    requests 2.31.0 depends on urllib3<3 and >=1.21.1\n"
    "    The user requested urllib3==1.20\n"
)


def _scenario_conflict_requests() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """VERSION_CONFLICT (repin kind) -- the ONE deliberate exception to "graph comes
    from the real chain". A genuine CONFLICTS_WITH edge is what a LIVE resolver (uv/pip,
    needing network to fetch requests/urllib3 metadata) produces; that is out of
    Global Constraints' OFFLINE scope, so this scenario hand-builds the SMALLEST graph
    shape that exhibits it -- the same shape the plan's own grading-formula test
    (`test_a_conflicted_root_is_MISLOCALIZED_if_starred`) already uses. What IS real
    here: `render_graph_context` itself, run against this shape, and the property it
    proves is a STRUCTURAL, code-level guarantee of `verdict()`
    (`graph_context.py:128`: a CONFLICTS_WITH node returns BLOCKED before it can ever
    return ACTIONABLE) -- not something that depends on what a live resolver would have
    said about THIS specific pin. `not_applicable=True` in `grade()` reflects exactly
    this: root_hit here is not a miss, it is a structural impossibility.
    """
    graph = (_base_graph()
             .with_node(Node(id=package_id("requests", "2.31.0"), type=NodeType.PACKAGE,
                             name="requests", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                             version="2.31.0", state=State.SATISFIED, build_from_source=False))
             .with_node(Node(id=package_id("urllib3", "1.20"), type=NodeType.PACKAGE,
                             name="urllib3", layer=Layer.PIP, discovered_by=DiscoveredBy.RESOLVER,
                             version="1.20", state=State.MISSING, build_from_source=False))
             .with_edge(Edge(src=package_id("urllib3", "1.20"), dst=package_id("requests", "2.31.0"),
                             relation=EdgeType.CONFLICTS_WITH, origin="resolver",
                             data={"summary": "requests 2.31.0 requires urllib3>=1.21.1,<3"})))
    result = RunResult(ok=False, failing_command="pip install urllib3==1.20",
                       output=_URLLIB3_CONFLICT_OUTPUT)
    return graph, result, ()


# Real pip wording for a name with no matching distribution (oracle note: "append an
# unbuildable OPTIONAL dep -> install fails; correct action is DROP").
_PHANTOM_PKG = "this-optional-pkg-fails-to-build==0.0.0"
_PHANTOM_OUTPUT = (
    f"ERROR: Could not find a version that satisfies the requirement {_PHANTOM_PKG} "
    "(from versions: none)\n"
    f"ERROR: No matching distribution found for {_PHANTOM_PKG}\n"
)


def _scenario_overinclude_dotenv() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """OVERINCLUDE (drop kind). Seed: ONLY the declared `python-dotenv` package
    (installs fine, unrelated to the injected phantom) + the TEST node. Per
    `inject.apply_injection`'s `add_install_pkg` op, the phantom package is appended
    straight to the ALREADY-RENDERED script text -- construction never saw it, so this
    seed honestly carries no node for it. Run through the REAL chain (nothing
    special-cased here, unlike the conflict scenario): `owner_node_for_command` finds
    no owner for the phantom's install command, `classify_dependency_failure` reads
    pip's own "no matching distribution" text and `classify_observation` DELIBERATELY
    ignores that failure type (never chases a name pip already disproved), so `enrich()`
    appends nothing and the render's own "BUILD FAILED -- NO GRAPH EXPLANATION" degrade
    path fires -- verified, not assumed. `not_applicable=True`: there is no node this
    graph could ever have starred for a package it was never told about.
    """
    seed = _base_graph().with_node(
        _declared_pkg("python-dotenv", "1.0.1", state=State.SATISFIED, build_from_source=False)
    )
    result = RunResult(ok=False, failing_command=f"pip install {_PHANTOM_PKG}",
                       output=_PHANTOM_OUTPUT)
    graph = _replay_turn(seed, result=result, causes=())
    return graph, result, ()


# Real GitPython import-time failure, EMPIRICALLY reproduced (not sourced from memory):
# `python3 -m venv`, `pip install GitPython` into it, hide `git` from `$PATH`,
# `python3 -c "import git"`. The exact, actual raised exception.
_GITPYTHON_PYTEST_OUTPUT = """\
______________ ERROR collecting tests/test_release.py _______________
ImportError while importing test module '/repo/tests/test_release.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/test_release.py:4: in <module>
    import git
/usr/local/lib/python3.11/site-packages/git/__init__.py:298: in <module>
    raise ImportError("Failed to initialize: {0}".format(_exc)) from _exc
E   ImportError: Failed to initialize: Bad git executable.
"""


def _scenario_tool_semrel() -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """TOOL_ABSENT (python-semantic-release / GitPython). Seed: ONLY the declared
    `GitPython` package (its own wheel installs fine, so pre-turn state is SATISFIED;
    the failure is GitPython's own top-level `import git` refusing to initialize
    without a `git` binary) + the TEST node. See the module docstring: the REAL,
    empirically-reproduced failure text does not match either shape
    `classify_tool_error` recognizes, so `enrich()` appends NOTHING -- not an
    unresolved capability, no capability at all. `git` is also absent from
    `PROVIDER_TABLE`, but that fact never gets exercised here.
    """
    seed = _base_graph().with_node(
        _declared_pkg("GitPython", "3.1.43", state=State.SATISFIED, build_from_source=False)
    )
    causes = tuple(summarize(_GITPYTHON_PYTEST_OUTPUT))
    result = RunResult(ok=True)
    graph = _replay_turn(seed, result=result, causes=causes)
    return graph, result, causes


_SCENARIO_BUILDERS = {
    "syslib_pygraphviz": _scenario_syslib_pygraphviz,
    "compiler_pyzmq": _scenario_compiler_pyzmq,
    "conflict_requests": _scenario_conflict_requests,
    "overinclude_dotenv": _scenario_overinclude_dotenv,
    "tool_semrel": _scenario_tool_semrel,
}


def scenario_for(inj: Injection) -> tuple[DepGraph, RunResult, tuple[Cause, ...]]:
    """(graph, result, causes) for `inj` -- see the builders above for provenance."""
    try:
        builder = _SCENARIO_BUILDERS[inj.injection_id]
    except KeyError as exc:
        raise ValueError(f"no real-chain scenario for injection {inj.injection_id!r}") from exc
    return builder()


# --------------------------------------------------------------------------- #
# Report -- per failure class, root_hit AND star_precision AND n_stars AND the two
# honesty flags, always. 🔴 n=5 cells (3 in the root-hit denominator): a smoke test.
# --------------------------------------------------------------------------- #

def build_report(injections: Sequence[Injection] = PILOT_INJECTIONS) -> list[dict]:
    rows = []
    for inj in injections:
        graph, result, causes = scenario_for(inj)
        score = grade(inj, graph, result, causes)
        rows.append({
            "injection_id": inj.injection_id,
            "failure_class": inj.failure_class,
            "kind": inj.correct_action["kind"],
            "target": inj.correct_action["target"],
            "root_hit": score.root_hit,
            "star_precision": score.star_precision,
            "n_stars": score.n_stars,
            "mislocalized": score.mislocalized,
            "not_applicable": score.not_applicable,
            "unresolved_capability": score.unresolved_capability,
        })
    return rows


def _print_report(rows: list[dict]) -> None:
    valid = [r for r in rows if not r["not_applicable"]]
    hits = sum(1 for r in valid if r["root_hit"])
    print(f"n={len(rows)} -- ONE injection per failure class "
          f"({len(FAILURE_CLASSES)} FAILURE_CLASSES total). This is a SMOKE TEST, not a "
          f"rate: do not read any column below as a percentage.")
    print(f"root_hit over the VALID denominator (excludes structurally-unstarrable "
          f"repin/drop rows): {hits}/{len(valid)}")
    print()
    header = (f"{'failure_class':<17} {'kind':<8} {'target':<34} {'root_hit':<9} "
              f"{'star_precision':<15} {'n_stars':<8} {'N/A':<6} {'unresolved':<11} "
              f"{'mislocalized'}")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['failure_class']:<17} {row['kind']:<8} {row['target']:<34} "
              f"{str(row['root_hit']):<9} {row['star_precision']:<15.2f} "
              f"{row['n_stars']:<8} {str(row['not_applicable']):<6} "
              f"{str(row['unresolved_capability']):<11} {row['mislocalized']}")


def _smoke() -> None:
    _print_report(build_report())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true",
                     help="grade the 5 real-chain injection scenarios and print the table")
    args = ap.parse_args(argv)

    if args.smoke:
        _smoke()
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
