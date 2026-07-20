"""Tier-1 (local, no docker): every real-repo graph renders the pipefail-safe
instrument floor exactly once.

setup.sh runs in TWO contexts — the eval Dockerfile (FROM v3-base when the swap is
opted in) AND the in-loop run_v3 Sandbox on the STOCK python:3.X-slim base — so the
floor must be present across real graphs as a best-effort fallback: guarded, and
pipefail-safe (the pytest floor ends in `|| true` so a pip-less base never aborts).
Complements the byte-exact golden in test_build_script.py by proving it holds on
the 16 real-repo graph fixtures, not just the synthetic one.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from eval.graph_quality.graph_cache import load_graphs
from graph.compile.build_script import render_build_script

_FIXTURES = str(_SRC / "eval" / "graph_quality" / "graphs")
_SHIM = 'command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python'
_FLOOR = ('python3 -c "import pytest" >/dev/null 2>&1 || '
          'python3 -m pip install --break-system-packages pytest || true')


def test_every_fixture_renders_pipefail_safe_floor():
    graphs = load_graphs(_FIXTURES)
    assert graphs, "no graph fixtures found"
    for name in sorted(graphs):
        out = render_build_script(graphs[name])
        assert "set -Eeuo pipefail" in out, name
        assert out.count(_SHIM) == 1, name
        assert out.count(_FLOOR) == 1, name
        # never an unguarded pytest install that would abort the run under set -e
        assert 'pip install --break-system-packages pytest\n' not in out, name
