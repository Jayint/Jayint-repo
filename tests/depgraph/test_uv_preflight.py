from __future__ import annotations

import os
import sys

from python_deps.depgraph import resolve_lock


def test_find_uv_bin_prefers_active_python_environment(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    uv = bin_dir / "uv"
    python.write_text("")
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)

    monkeypatch.delenv("DEPGRAPH_UV_BIN", raising=False)
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(resolve_lock.shutil, "which", lambda _name: "/other/uv")

    assert resolve_lock._find_uv_bin() == os.fspath(uv)


def test_find_uv_bin_honors_explicit_override(monkeypatch):
    monkeypatch.setenv("DEPGRAPH_UV_BIN", "/custom/uv")
    assert resolve_lock._find_uv_bin() == "/custom/uv"
