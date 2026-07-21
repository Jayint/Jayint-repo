#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 0 reciped (none)
#   graph-hash: sha256:a461bf77bc4e
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest runner (fallback; also baked into v3-base). Best-effort, never aborts.
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest || true

# ==================== PROJECT (editable) ====================
#@node project:fixturepkg  requires=-
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:fixturepkg" >> /tmp/v3_failed_nodes.log
fi
