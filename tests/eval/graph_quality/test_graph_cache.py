"""graph_cache: mint (Docker, once) + load (offline, forever) — plan Task 5.

The test that MUST always run is the offline loader: it round-trips the
COMMITTED `src/eval/graph_quality/graphs/*.json` fixtures through
`DepGraph.from_dict`/`to_dict` and asserts they actually carry real graph
content (nodes, edges) — not just that the file parses. Those fixtures were
produced by literally running `mint()` against Docker over the 16 repos under
`outputs/build_script_eval/_smoke/`; see the commit message / handoff for which
repos minted and why any failed.

The mint path itself needs Docker and takes real minutes per repo (some of the
16 — cryptography, lxml, pillow — compile native extensions), so its own test
is `@pytest.mark.docker` AND additionally gated behind an explicit opt-in env
var (`GRAPH_QUALITY_MINT=1`). The existing `@pytest.mark.docker` convention
elsewhere in this repo (`test_ldd_probe_docker.py`, `test_wheel_preflight_
integration.py`) only skips on a MISSING docker binary — fine for their
single-tiny-package probes, but running a real construction pass over even one
of these 16 repos on every `pytest -q` would break "offline by default"
(Global Constraints) the moment Docker happens to be on PATH. The extra opt-in
keeps this test out of the normal suite while still being real, runnable, and
not faked when someone deliberately asks for it.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.schema import DepGraph  # noqa: E402
from src.eval.graph_quality.graph_cache import (  # noqa: E402
    _DEFAULT_OUT_DIR, _DEFAULT_SMOKE_ROOT, load_graphs, mint,
)

_GRAPHS_DIR = Path(_DEFAULT_OUT_DIR)


# --------------------------------------------------------------------------- #
# OFFLINE — must always run. No Docker, no network: pure file I/O + parsing.
# --------------------------------------------------------------------------- #

def test_committed_fixtures_exist():
    """If this is empty, the mint pass never ran (or its output was never
    committed) and every downstream block_parity check has nothing to grade."""
    files = sorted(_GRAPHS_DIR.glob("*.json"))
    assert files, f"no committed graph fixtures under {_GRAPHS_DIR}"


def test_load_graphs_round_trips_every_committed_fixture_losslessly():
    """`DepGraph.from_dict(json) -> to_dict()` must reproduce the file BYTE-for-
    structure exactly. This exercises T1's deserializer against REAL graphs (not
    just the hand-built synthetic fixture in test_schema_roundtrip.py) — a real
    construction pass can produce field combinations (e.g. a genuinely absent
    `chosen_fix`, a populated `attempts` tuple) the synthetic test never tried."""
    files = sorted(_GRAPHS_DIR.glob("*.json"))
    assert files
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        graph = DepGraph.from_dict(raw)
        assert graph.to_dict() == raw, f"{path.name}: round-trip mismatch"


def test_load_graphs_returns_real_nonempty_graphs():
    """Every cached graph must actually carry nodes AND edges — a degenerate
    empty graph would let block_parity report a fraudulent 'zero divergences'
    (nothing to check is not the same as nothing wrong)."""
    graphs = load_graphs()
    assert graphs, "load_graphs() returned nothing"
    for repo, graph in graphs.items():
        assert isinstance(graph, DepGraph)
        assert graph.nodes, f"{repo}: 0 nodes — construction produced an empty graph"
        assert graph.edges, f"{repo}: 0 edges — construction produced a disconnected graph"


def test_load_graphs_keys_are_the_smoke_corpus_repo_names():
    """Sanity: the cache keys off the repo directory name (`<repo>.json` stem),
    matching the 16-repo smoke corpus this eval is scoped to — not some other
    naming scheme that would silently break `block_parity`'s lookups."""
    graphs = load_graphs()
    known_smoke_repos = {
        "click", "cryptography", "flask", "httpx", "jinja", "lxml", "pillow",
        "psycopg2", "pygraphviz", "python-dotenv", "python-semantic-release",
        "pyyaml", "pyzmq", "requests", "rich", "typer",
    }
    assert set(graphs) <= known_smoke_repos, set(graphs) - known_smoke_repos


def test_load_graphs_is_pure_offline_even_when_the_smoke_root_is_missing():
    """`load_graphs()` never reads `outputs/build_script_eval/_smoke/` at all —
    only the committed cache directory. Point `_DEFAULT_SMOKE_ROOT` at nothing
    and confirm loading the (real, already-committed) cache still works."""
    assert not Path("/nonexistent/path/for/this/test/only").exists()
    graphs = load_graphs(out_dir=str(_GRAPHS_DIR))  # explicit — never touches smoke_root
    assert graphs


# --------------------------------------------------------------------------- #
# DOCKER — real mint, opt-in only. Not run by `pytest tests/eval/graph_quality/ -q`.
# --------------------------------------------------------------------------- #

@pytest.mark.docker
def test_mint_builds_a_real_graph_for_one_smoke_repo(tmp_path):
    """The real, non-faked mint path: build ONE cheap repo (click — pure Python,
    no native build) under Docker and confirm a real DepGraph with real content
    comes back. Deliberately scoped to one repo, not all 16: this test exists to
    prove `mint()` itself works, not to re-run the whole corpus on every opt-in
    invocation — the corpus mint is `python3 -m src.eval.graph_quality.graph_cache
    --mint`, run once, by hand, its JSON output committed to `graphs/`."""
    if not os.environ.get("GRAPH_QUALITY_MINT"):
        pytest.skip("set GRAPH_QUALITY_MINT=1 to run the real (Docker, slow) mint smoke")
    if shutil.which("docker") is None:
        pytest.skip("Docker binary not on PATH")

    smoke_root = tmp_path / "smoke"
    smoke_root.mkdir()
    src_click = Path(_DEFAULT_SMOKE_ROOT) / "click"
    if not src_click.is_dir():
        pytest.skip(f"{src_click} not present on this machine")
    shutil.copytree(src_click, smoke_root / "click")

    out_dir = tmp_path / "out"
    status = mint(smoke_root=str(smoke_root), out_dir=str(out_dir))
    assert status.get("click") == "ok", status
    graph = load_graphs(str(out_dir))["click"]
    assert graph.nodes
    assert graph.edges
