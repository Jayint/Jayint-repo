import sys, pathlib, json
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.collect import parse_collection_result, Docker, collect_once, COLLECT_CMD


def test_parse_clean():
    pj = {"collected": ["t.py::a", "t.py::b"], "collect_errors": [],
          "skipped_modules": [], "deselected": []}
    r = parse_collection_result(0, pj)
    assert r.exit_code == 0 and r.collected == ("t.py::a", "t.py::b") and r.collected_count == 2


def test_parse_nonzero_exit_with_ids_and_errors():
    pj = {"collected": ["t.py::a"], "collect_errors": ["t.py"], "skipped_modules": [],
          "deselected": ["t.py::slow"]}
    r = parse_collection_result(2, pj)
    assert r.exit_code == 2 and r.collect_errors == ("t.py",) and r.deselected == ("t.py::slow",)


def test_docker_build_argv():
    calls = []

    def fake_run(argv, timeout=None):
        calls.append(argv)
        return 0, "built"

    d = Docker(run=fake_run)
    rc, log = d.build("mytag", "/ctx")
    assert rc == 0 and log == "built"
    assert calls[0] == ["docker", "build", "-t", "mytag", "/ctx"]


def test_docker_exec_hardened_run_and_env():
    calls = []

    def fake_run(argv, timeout=None):
        calls.append(argv)
        return 0, ""

    d = Docker(run=fake_run)
    d.run_detached("mytag", "c1", "/src")
    assert "--network" in calls[0] and "none" in calls[0] and "--cap-drop" in calls[0]
    d.exec("c1", ["echo", "hi"], env={"K": "V"})
    assert calls[1][:4] == ["docker", "exec", "-e", "K=V"]


def test_collect_once_reads_plugin_json(tmp_path):
    canned = {"collected": ["t.py::a"], "collect_errors": [], "skipped_modules": [],
              "deselected": []}

    class FakeDocker:
        def exec(self, name, argv, env=None, timeout=None):
            return 0, ""

        def cp_in(self, name, src, dst):
            pass

        def cp_out(self, name, src, dst):
            with open(dst, "w") as f:
                json.dump(canned, f)

    r = collect_once(FakeDocker(), "c1", "/src", "/plugin.py", str(tmp_path / "r.json"))
    assert r.exit_code == 0 and r.collected == ("t.py::a",)
    assert "manifest_collect_plugin" in COLLECT_CMD
