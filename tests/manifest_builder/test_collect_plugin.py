import sys, os, json, shutil, subprocess, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
# Host source of the plugin (module file is named collect_plugin.py per the spec).
_PLUGIN_SRC = _ROOT / "src" / "manifest_builder" / "collect_plugin.py"


def _run_collect(target_dir, out_path):
    # Stage the plugin under its registered module name in an ISOLATED directory,
    # mirroring the production adapter which copies collect_plugin.py into the image
    # at /manifest/manifest_collect_plugin.py (Task 5). Isolation is required: putting
    # src/manifest_builder itself on PYTHONPATH would let its types.py shadow the
    # stdlib `types` module and crash the interpreter at startup.
    out_path = pathlib.Path(out_path)
    plugin_dir = out_path.parent / "_plugin"
    plugin_dir.mkdir(exist_ok=True)
    shutil.copy(_PLUGIN_SRC, plugin_dir / "manifest_collect_plugin.py")
    env = dict(os.environ)
    env["MANIFEST_COLLECT_OUT"] = str(out_path)
    env["PYTHONPATH"] = str(plugin_dir) + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "-p", "manifest_collect_plugin", str(target_dir)],
        capture_output=True, text=True, env=env)
    return p.returncode, json.load(open(out_path))


def _write(d, files):
    for name, content in files.items():
        (d / name).write_text(content)


def test_clean_repo_collects_all(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _write(repo, {"test_math.py": "def test_add():\n    assert 1+1==2\n"
                                  "def test_sub():\n    assert 2-1==1\n"})
    rc, r = _run_collect(repo, tmp_path / "c.json")
    assert rc == 0 and r["exit_status"] == 0
    assert {n.split("::")[-1] for n in r["collected"]} == {"test_add", "test_sub"}
    assert r["collect_errors"] == []


def test_importorskip_hidden_module_recorded_not_collected(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _write(repo, {
        "test_ok.py": "def test_ok():\n    assert 1\n",
        "test_opt.py": "import pytest\npytest.importorskip('nonexistent_pkg_zzz')\n"
                       "def test_opt():\n    assert 1\n",
    })
    rc, r = _run_collect(repo, tmp_path / "c.json")
    assert rc == 0
    assert any("test_opt" in m for m in r["skipped_modules"])
    assert not any("test_opt" in c for c in r["collected"])
    assert any("test_ok" in c for c in r["collected"])


def test_broken_import_nonzero_exit_with_partial_collection(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _write(repo, {
        "test_ok.py": "def test_ok():\n    assert 1\n",
        "test_broken.py": "import nonexistent_pkg_zzz\ndef test_x():\n    assert 1\n",
    })
    rc, r = _run_collect(repo, tmp_path / "c.json")
    assert rc == 2 and r["exit_status"] == 2
    assert r["collect_errors"] and any("test_ok" in c for c in r["collected"])
