import sys, pathlib, subprocess
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import protected as P


def _git(wt, *args):
    subprocess.run(["git", "-C", str(wt), *args], check=True, capture_output=True)


def _repo(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "pkg.py").write_text("x = 1\n")
    (wt / "test_a.py").write_text("def test_a():\n    assert 1\n")
    (wt / "conftest.py").write_text("# root conftest\n")
    (wt / "Dockerfile").write_text("FROM python:3.11-slim\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "init")
    return wt


def test_compute_protected_excludes_dockerfile(tmp_path):
    wt = _repo(tmp_path)
    prot = P.compute_protected(str(wt))
    assert "Dockerfile" not in prot
    assert set(prot) == {"pkg.py", "test_a.py", "conftest.py"}


def test_restore_reverts_edits_removes_cheat_keeps_dockerfile_and_state(tmp_path):
    wt = _repo(tmp_path)
    (wt / "test_a.py").write_text("def test_a():\n    assert 0  # sabotaged\n")   # edit protected
    (wt / "cheat.py").write_text("# untracked cheat\n")                           # untracked cheat
    (wt / ".manifest_ws.json").write_text('{"state": 1}\n')                       # untracked state
    # NB: this repo TRACKS a Dockerfile (committed in _repo); the agent has edited it.
    (wt / "Dockerfile").write_text("FROM python:3.11-slim\nRUN pip install foo\n")  # agent's work
    P.restore_pristine(str(wt))
    assert "assert 1" in (wt / "test_a.py").read_text()               # protected reverted
    assert not (wt / "cheat.py").exists()                             # untracked cheat removed
    assert (wt / ".manifest_ws.json").exists()                        # manifest state preserved
    assert "RUN pip install foo" in (wt / "Dockerfile").read_text()   # tracked Dockerfile kept


def test_hash_host_changes_when_file_changes(tmp_path):
    wt = _repo(tmp_path)
    prot = P.compute_protected(str(wt))
    h1 = P.hash_host(str(wt), prot)
    (wt / "pkg.py").write_text("x = 2\n")
    h2 = P.hash_host(str(wt), prot)
    assert h1["pkg.py"] != h2["pkg.py"]
    assert P.source_tree_sha256(h1) != P.source_tree_sha256(h2)


def test_hash_in_image_parses_sha256sum(tmp_path):
    prot = ("pkg.py", "test_a.py")

    def fake_exec(argv):
        # emulate `sha256sum /src/pkg.py /src/test_a.py`
        lines = "\n".join(f"{'a'*64}  {p}" for p in argv[1:])
        return 0, lines

    got = P.hash_in_image(fake_exec, "/src", prot)
    assert got == {"pkg.py": "sha256:" + "a" * 64, "test_a.py": "sha256:" + "a" * 64}


def test_restore_removes_gitignored_untracked_cheat(tmp_path):
    wt = _repo(tmp_path)
    # repo ignores a path; agent drops an untracked, gitignored cheat file there
    (wt / ".gitignore").write_text("ignored_cheat.py\n")
    _git(wt, "add", ".gitignore")
    _git(wt, "commit", "-qm", "add gitignore")
    (wt / "ignored_cheat.py").write_text("collect_ignore = ['test_a.py']\n")  # untracked + gitignored
    (wt / ".manifest_ws.json").write_text('{"state": 1}\n')                    # untracked manifest state
    (wt / "Dockerfile").write_text("FROM python:3.11-slim\nRUN pip install foo\n")  # agent's work
    P.restore_pristine(str(wt))
    assert not (wt / "ignored_cheat.py").exists()          # gitignored cheat removed (needs -x)
    assert (wt / ".manifest_ws.json").exists()             # manifest state still preserved under -x
    assert "RUN pip install foo" in (wt / "Dockerfile").read_text()  # agent Dockerfile still kept


def test_restore_preserves_verify_shim(tmp_path):
    wt = _repo(tmp_path)
    (wt / "verify").write_text("#!/bin/sh\necho hi\n")   # untracked harness oracle shim
    P.restore_pristine(str(wt))
    assert (wt / "verify").exists()                      # preserved via -e verify
