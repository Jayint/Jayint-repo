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


def test_docker_build_timeout_becomes_a_failed_build_not_a_raise():
    # A TimeoutExpired that ESCAPES bypasses certify()'s BuildError handler and propagates out
    # of build_one, abandoning the repo -- every remaining attempt is lost to one slow
    # Dockerfile. mlflow lost all 3 attempts this way. It must degrade to a non-zero rc.
    import subprocess

    def fake_run(argv, timeout=None):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    rc, log = Docker(run=fake_run).build("mytag", "/ctx")
    assert rc == 124
    assert "MANIFEST_BUILD_TIMEOUT" in log


def test_build_timeout_is_env_configurable(monkeypatch):
    seen = {}

    def fake_run(argv, timeout=None):
        seen["timeout"] = timeout
        return 0, ""

    d = Docker(run=fake_run)
    d.build("t", "/ctx")
    assert seen["timeout"] == 3600                      # default

    monkeypatch.setenv("MANIFEST_BUILD_TIMEOUT", "10800")
    d.build("t", "/ctx")
    assert seen["timeout"] == 10800                     # read per call, so no reload needed


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


def test_build_and_collect_raises_builderror_on_nonzero_build(tmp_path):
    import pytest
    from src.manifest_builder.collect import build_and_collect, BuildError

    class WS:
        slug = "x"; path = "/ctx"; src_root = "/src"

    class DockerBuildFails:
        def build(self, tag, ctx):
            return 1, "build failed: boom"

    with pytest.raises(BuildError):
        build_and_collect(DockerBuildFails(), WS(), "/plugin.py", str(tmp_path), ("pkg.py",))


def test_build_and_collect_removes_container_even_if_collect_raises(tmp_path):
    import pytest
    from src.manifest_builder.collect import build_and_collect

    started = {"v": False}
    rm_after_start = {"v": False}

    class WS:
        slug = "x"; path = "/ctx"; src_root = "/src"

    class DockerCollectExplodes:
        def build(self, tag, ctx):
            return 0, "ok"

        def image_id(self, tag):
            return "sha256:img"

        def run_detached(self, tag, name, workdir):
            started["v"] = True

        def rm(self, name):
            # the pre-run rm fires before run_detached; only the finally rm fires after
            if started["v"]:
                rm_after_start["v"] = True

        def exec(self, name, argv, env=None, timeout=None):
            # hash_in_image issues sha256sum first — answer it so hashing succeeds,
            # then blow up on the collect step (collect_once's first exec is `mkdir`).
            if argv and argv[0] == "sha256sum":
                return 0, "\n".join(f"{'a' * 64}  {p}" for p in argv[1:])
            raise RuntimeError("collect exploded")

        def cp_in(self, name, src, dst):
            pass

        def cp_out(self, name, src, dst):
            pass

    with pytest.raises(RuntimeError):
        build_and_collect(DockerCollectExplodes(), WS(), "/plugin.py", str(tmp_path), ("pkg.py",))
    assert rm_after_start["v"], "container must be rm'd in finally (after start) even when a collect raises"


def test_find_injected_flags_untracked_collection_files():
    from src.manifest_builder.collect import find_injected_collection_files
    protected = ("pkg/mod.py", "tests/conftest.py", "tests/test_real.py")

    def fake_exec(argv):
        # emulate `find /src ... -type f` output
        listing = "\n".join("/src/" + p for p in [
            "pkg/mod.py",                 # tracked source — ok
            "tests/conftest.py",          # tracked conftest — ok
            "tests/test_real.py",         # tracked test — ok
            "evil/conftest.py",           # INJECTED conftest (collect_ignore) — flag
            "test_fake.py",               # INJECTED fake test (manifest inflation) — flag
            "hack.pth",                   # INJECTED .pth — flag
            "pkg/__pycache__/mod.cpython-311.pyc",  # build byproduct — ignore
            ".egg-info/SOURCES.txt",      # build byproduct — ignore
        ])
        return 0, listing

    got = find_injected_collection_files(fake_exec, "/src", protected)
    assert got == ["evil/conftest.py", "hack.pth", "test_fake.py"]


def test_find_injected_empty_when_all_tracked():
    from src.manifest_builder.collect import find_injected_collection_files
    protected = ("a/conftest.py", "a/test_x.py")

    def fake_exec(argv):
        return 0, "/src/a/conftest.py\n/src/a/test_x.py\n/src/a/__pycache__/x.pyc\n"

    assert find_injected_collection_files(fake_exec, "/src", protected) == []


def test_find_injected_fails_closed_when_find_errors():
    from src.manifest_builder.collect import find_injected_collection_files

    def fake_exec_find_fails(argv):
        return 127, "find: not found"   # e.g. findutils missing / find removed

    got = find_injected_collection_files(fake_exec_find_fails, "/src", ("a/test_x.py",))
    assert got and got[0].startswith("<injection-scan-failed")   # non-empty → forces REJECT
