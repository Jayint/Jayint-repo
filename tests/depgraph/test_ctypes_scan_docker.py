"""Task A4 — Docker integration test: ctypes/cffi runtime-lib discovery.

Proves add_ctypes_runtime_libs's grep command (shell-quoting + site-packages
paths) works end-to-end in a real container — the one thing canned-stdout unit
tests can't cover. Package under test: python-magic (its magic/__init__.py calls
ctypes.util.find_library('magic')). Skips cleanly when docker is absent.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from graph.executors import DockerExecutor  # noqa: E402
from graph.model import (  # noqa: E402
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State, project_id, syslib_id,
)
from graph.python.native.ctypes_scan import add_ctypes_runtime_libs  # noqa: E402

pytestmark = pytest.mark.docker

_BASE_IMAGE = "python:3.11-slim"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


@pytest.mark.docker
def test_finds_libmagic_in_real_container() -> None:
    if not _docker_available():
        pytest.skip("Docker binary not on PATH — skipping Docker integration test")
    with DockerExecutor(_BASE_IMAGE, bootstrap_uv=False) as ex:
        ex.run("pip install --no-input python-magic==0.4.27")
        proj = Node(id=project_id("app"), type=NodeType.PROJECT, name="app",
                    layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                    state=State.UNKNOWN)
        out = add_ctypes_runtime_libs(DepGraph(nodes=(proj,)), ex)
        # Assertions stay INSIDE the `with` block so the diagnostic can still
        # query the (living) container if the scan came back empty; on `with`
        # exit DockerExecutor removes the container and .run() would raise.
        node = out.get(syslib_id("libmagic.so"))
        assert node is not None, (
            "ctypes scan did not find libmagic.so. magic install location: "
            + ex.run("python -c \"import magic, os; print(os.path.dirname(magic.__file__))\"").stdout
        )
        assert node.chosen_fix == "apt:libmagic1"
        assert "magic" in (node.evidence or "")
