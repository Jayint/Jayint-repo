import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.script_prep import strip_graph_framing

# A representative render_build_script() artifact (graph-primary framing + #@ annotations).
_RENDERED = '''#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 1 reciped (1 pip) + 0 needs
#   graph-hash: sha256:a461bf77bc4e
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

#@node pkg:six==1.17.0  version=1.17.0  requires=-
#@check python -m pip show six
python3 -m pip install --break-system-packages --no-deps six==1.17.0

# ==================== PROJECT (editable) ====================
#@node project:undeclared-demo  requires=-
python3 -m pip install --break-system-packages --no-deps -e .
'''


def test_removes_graph_framing_header():
    out = strip_graph_framing(_RENDERED)
    for phrase in ("COMPILED from the certified dependency graph", "DO NOT EDIT",
                   "Edit the graph and re-render", "artifact, not a source",
                   "graph-hash", "nodes:"):
        assert phrase not in out


def test_removes_inline_node_annotations():
    out = strip_graph_framing(_RENDERED)
    assert "#@node" not in out and "#@check" not in out and "#@need" not in out


def test_preserves_executable_body_verbatim():
    out = strip_graph_framing(_RENDERED)
    assert "set -Eeuo pipefail" in out
    assert 'python3 -m pip install --break-system-packages --no-deps six==1.17.0' in out
    assert 'python3 -m pip install --break-system-packages --no-deps -e .' in out
    assert 'ln -sf "$(command -v python3)"' in out
    # the pytest-ensure COMMAND stays even though its comment mentioned the graph
    assert 'python3 -c "import pytest"' in out
    # a non-graph section separator is kept
    assert "PROJECT (editable)" in out


def test_drops_body_comment_that_mentions_graph():
    out = strip_graph_framing(_RENDERED)
    assert "not a graph node" not in out          # the pytest comment mentioned the graph


def test_has_neutral_editable_header_and_no_graph_word():
    out = strip_graph_framing(_RENDERED)
    assert out.startswith("#!/usr/bin/env bash")
    assert "Edit it directly to fix the build" in out
    assert "graph" not in out.lower()          # every graph mention is gone


def test_preserves_trailing_newline_state():
    assert strip_graph_framing(_RENDERED).endswith("\n")
    assert not strip_graph_framing(_RENDERED.rstrip("\n")).endswith("\n")


def test_no_recognizable_header_falls_back_to_filtering():
    # A script with no `set -` anchor: still drops #@ annotations + graph-phrase comments.
    src = 'pip install app\n#@node pkg:x\n# Edit the graph and re-render\npip install x\n'
    out = strip_graph_framing(src)
    assert "#@node" not in out and "Edit the graph" not in out
    assert "pip install app" in out and "pip install x" in out
