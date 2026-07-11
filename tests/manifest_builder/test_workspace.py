import sys, pathlib, subprocess
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import workspace as W


def _origin(tmp_path):
    o = tmp_path / "origin"
    o.mkdir()
    subprocess.run(["git", "-C", str(o), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(o), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(o), "config", "user.name", "t"], check=True)
    (o / "pkg.py").write_text("x = 1\n")
    (o / "test_a.py").write_text("def test_a():\n    assert 1\n")
    subprocess.run(["git", "-C", str(o), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(o), "commit", "-qm", "init"], check=True)
    sha = subprocess.run(["git", "-C", str(o), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return str(o), sha


def test_prepare_clones_pins_and_seeds(tmp_path):
    origin, sha = _origin(tmp_path)
    dest = str(tmp_path / "wt")
    ws = W.prepare_workspace(f"file://{origin}", sha, dest, base_image="python:3.11-slim")
    assert ws.commit_sha == sha
    assert "Dockerfile" not in ws.protected and "pkg.py" in ws.protected
    df = (pathlib.Path(dest) / "Dockerfile").read_text()
    assert "COPY . /src" in df and "python:3.11-slim" in df
    assert ws.pristine_hashes["pkg.py"].startswith("sha256:")


def test_save_and_load_state_roundtrip(tmp_path):
    origin, sha = _origin(tmp_path)
    dest = str(tmp_path / "wt")
    ws = W.prepare_workspace(f"file://{origin}", sha, dest)
    W.save_state(ws)
    ws2 = W.load_state(dest)
    assert ws2.protected == ws.protected and ws2.pristine_hashes == ws.pristine_hashes
    assert ws2.commit_sha == sha
