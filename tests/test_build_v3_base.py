"""Task 2 (§4.4a) — the v3-base recipe + build script are internally consistent.

These are static/parity checks (no Docker): the build-script matrix must match
the shared ``V3_BASE_MINORS`` constant the FROM-swap mapper reads, and the
Dockerfile must actually bake the instrument. The live build + the Tier-0
differential probe (`scripts/verify_v3_base.sh`) are exercised out-of-band.
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from multi_docker_eval_adapter import V3_BASE_MINORS


def test_build_script_matrix_matches_constant():
    sh = (_ROOT / "scripts" / "build_v3_base.sh").read_text()
    m = re.search(r"MINORS=\(([^)]*)\)", sh)
    assert m is not None, "build_v3_base.sh must declare a MINORS=(...) array"
    assert tuple(m.group(1).split()) == V3_BASE_MINORS


def test_dockerfile_bakes_the_instrument():
    df = (_ROOT / "docker" / "v3-base" / "Dockerfile").read_text()
    for token in ("pytest", "pytest-timeout", "/usr/local/bin/python", "git"):
        assert token in df, token


def test_dockerfile_is_from_python_slim():
    df = (_ROOT / "docker" / "v3-base" / "Dockerfile").read_text()
    assert "FROM python:${PY_MINOR}-slim" in df
