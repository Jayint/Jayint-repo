import os
import pathlib
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.protected import compute_protected, hash_host


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


def _init(d):
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")


def test_compute_protected_excludes_symlink_to_dir(tmp_path):
    # Reproduces the NewFuture/DDNS ERROR: a tracked symlink pointing at a directory made
    # `git ls-files` yield a path that open()/sha256 treated as a file -> IsADirectoryError.
    r = tmp_path / "repo"
    _init(r)
    (r / "pkg.py").write_text("x = 1\n")
    d = r / "docs" / "public" / "schema"
    d.mkdir(parents=True)
    (d / "real.json").write_text("{}\n")
    os.symlink("public/schema", r / "docs" / "link_to_schema")   # tracked symlink -> a dir
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")

    prot = compute_protected(str(r))
    assert "pkg.py" in prot                              # regular file kept
    assert "docs/public/schema/real.json" in prot        # regular file kept
    assert "docs/link_to_schema" not in prot             # symlink-to-dir excluded

    # The whole point: hashing the protected set must not raise IsADirectoryError.
    h = hash_host(str(r), prot)
    assert "pkg.py" in h and "docs/public/schema/real.json" in h


def test_compute_protected_keeps_regular_symlink_target_but_drops_link(tmp_path):
    # A symlink to a regular FILE is also excluded (mode 120000), while the real file stays.
    r = tmp_path / "repo2"
    _init(r)
    (r / "conf.py").write_text("a = 2\n")
    os.symlink("conf.py", r / "conf_link.py")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")

    prot = compute_protected(str(r))
    assert "conf.py" in prot
    assert "conf_link.py" not in prot
    hash_host(str(r), prot)   # must not raise


def test_compute_protected_excludes_submodule_gitlink(tmp_path):
    # A submodule gitlink (mode 160000) is a directory on disk and cannot be hashed as a file.
    # Synthesize one without any real submodule/network via update-index --cacheinfo.
    r = tmp_path / "repo3"
    _init(r)
    (r / "pkg.py").write_text("x = 1\n")
    _git(r, "update-index", "--add", "--cacheinfo",
         "160000,1111111111111111111111111111111111111111,vendor/sub")
    _git(r, "add", "pkg.py")
    _git(r, "commit", "-qm", "init")

    prot = compute_protected(str(r))
    assert "pkg.py" in prot
    assert "vendor/sub" not in prot     # gitlink excluded (mode 160000, not a regular file)
    hash_host(str(r), prot)             # must not raise
