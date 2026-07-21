# tests/depgraph/test_integrate_oracle.py
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("PI_RUN_ORACLE"),
    reason="clean-container resolution oracle: needs network+pip; set PI_RUN_ORACLE=1 to run",
)
@pytest.mark.parametrize("pkg,import_name", [("psycopg2-binary", "psycopg2")])
def test_resolved_provider_makes_the_failing_import_pass(pkg, import_name):
    """Ground truth for resolution accuracy: install the provider integrate() chose,
    then run the exact check that failed. If it now imports, the resolution was correct."""
    venv = Path(tempfile.mkdtemp()) / "v"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = venv / "bin" / "python"
    try:
        pre = subprocess.run([str(py), "-c", f"import {import_name}"], capture_output=True)
        assert pre.returncode != 0, "import should FAIL before install (baseline)"
        inst = subprocess.run([str(py), "-m", "pip", "install", "-q", pkg],
                              capture_output=True, text=True)
        if inst.returncode != 0 and any(
            m in (inst.stderr or "").lower()
            for m in ("could not connect", "network", "timed out",
                      "temporary failure", "connection", "max retries")
        ):
            pytest.skip(f"pip install looks network-related:\n{inst.stderr}")
        assert inst.returncode == 0, f"pip install {pkg} failed:\n{inst.stderr}"
        post = subprocess.run([str(py), "-c", f"import {import_name}"], capture_output=True)
        assert post.returncode == 0, f"provider {pkg} did not satisfy import:{import_name}"
    finally:
        shutil.rmtree(venv.parent, ignore_errors=True)
