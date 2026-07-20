"""Tier-1 (local, no docker): removing the instrument preamble is subtractive-only.

For every saved real-repo graph fixture, the new render must equal the captured
baseline (which still carries the preamble) MINUS only preamble lines: nothing is
added, and every removed line is a preamble line (the two comments, the symlink,
the pytest floor) or a blank. This locks the render paths the golden byte-diff
gate never covered on REAL graphs. Baselines live in
``tests/depgraph/baselines/render/<name>.sh``, captured from the pre-removal
renderer; regenerate them if the render legitimately changes elsewhere.
"""
import difflib
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from eval.graph_quality.graph_cache import load_graphs
from graph.compile.build_script import render_build_script

_FIXTURES = str(_SRC / "eval" / "graph_quality" / "graphs")
_BASELINES = Path(__file__).resolve().parent / "baselines" / "render"

# The lines §4.4c deletes from setup.sh (now baked into v3-base).
_PREAMBLE_SUBSTRINGS = (
    "Normalize `python` -> python3",
    'ln -sf "$(command -v python3)"',
    "Ensure the pytest test-runner",
    'python3 -c "import pytest"',
)


def _is_preamble(line: str) -> bool:
    return line.strip() == "" or any(sub in line for sub in _PREAMBLE_SUBSTRINGS)


def test_render_is_baseline_minus_preamble():
    graphs = load_graphs(_FIXTURES)
    assert graphs, "no graph fixtures found"
    for name in sorted(graphs):
        baseline = (_BASELINES / f"{name}.sh").read_text()
        new = render_build_script(graphs[name])
        # No instrument line survives in the render.
        assert 'ln -sf "$(command -v python3)"' not in new, name
        assert 'python3 -c "import pytest"' not in new, name
        # Subtractive-only: nothing added; every removed line is preamble/blank.
        diff = list(difflib.unified_diff(
            baseline.splitlines(), new.splitlines(), lineterm=""))
        added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
        removed = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]
        assert added == [], (name, "render ADDED lines beyond preamble removal", added)
        offenders = [l for l in removed if not _is_preamble(l)]
        assert offenders == [], (name, "render removed NON-preamble lines", offenders)
        # And the preamble was genuinely present in the baseline (guards a no-op pass).
        assert any("import pytest" in l for l in removed), (name, "baseline lacked the floor?")
