import sys, pathlib, subprocess, json
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import workspace as W
from src.manifest_builder.__main__ import certify, build_one
from src.manifest_builder.runner import FakeRunner


def _origin(tmp_path):
    o = tmp_path / "origin"; o.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(o), *a], check=True)
    (o / "pkg.py").write_text("x = 1\n")
    (o / "test_a.py").write_text("def test_a():\n    assert 1\ndef test_b():\n    assert 1\n")
    subprocess.run(["git", "-C", str(o), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(o), "commit", "-qm", "init"], check=True)
    sha = subprocess.run(["git", "-C", str(o), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return f"file://{o}", sha


class FakeDocker:
    """Simulates a clean, stable build+collect whose in-image hashes match pristine."""
    def __init__(self, ws):
        self._ws = ws

    def build(self, tag, ctx):
        return 0, "build ok"

    def image_id(self, tag):
        return "sha256:fakeimg"

    def run_detached(self, tag, name, workdir):
        pass

    def rm(self, name):
        pass

    def exec(self, name, argv, env=None, timeout=None):
        if argv and argv[0] == "sha256sum":
            # echo pristine host hashes back (protected_ok == True)
            lines = []
            for p in argv[1:]:
                rel = p[len(self._ws.src_root) + 1:]
                digest = self._ws.pristine_hashes[rel].split(":", 1)[1]
                lines.append(f"{digest}  {p}")
            return 0, "\n".join(lines)
        return 0, ""

    def cp_in(self, name, src, dst):
        pass

    def cp_out(self, name, src, dst):
        with open(dst, "w") as f:
            json.dump({"collected": ["test_a.py::test_a", "test_a.py::test_b"],
                       "collect_errors": [], "skipped_modules": [], "deselected": []}, f)


def test_certify_certifies_clean_env(tmp_path):
    repo_url, sha = _origin(tmp_path)
    ws = W.prepare_workspace(repo_url, sha, str(tmp_path / "wt"))
    plugin = str(_ROOT / "src" / "manifest_builder" / "collect_plugin.py")
    verdict, cert, log, r1, r2 = certify(FakeDocker(ws), ws, plugin, str(tmp_path / "tmp"))
    assert verdict.accepted and cert["status"] == "CERTIFIED"
    assert cert["manifest_size"] == 2


def test_build_one_emits_artifacts(tmp_path):
    repo_url, sha = _origin(tmp_path)
    out = tmp_path / "art"
    # patch prepare_workspace's docker with our fake by driving build_one directly
    ws_holder = {}

    def edit(cwd):
        ws_holder["cwd"] = cwd   # agent "does nothing" — seed already collects cleanly

    class DockerFactory:
        def __call__(self, ws):
            return FakeDocker(ws)

    summary = build_one(repo_url, sha, str(out), FakeRunner(edit_fn=edit),
                        docker_factory=DockerFactory(), attempts=1,
                        workdir=str(tmp_path / "wt2"))
    assert summary["status"] == "CERTIFIED"
    art_dir = pathlib.Path(summary["artifacts_dir"])
    assert (art_dir / "collected-nodeids.json").exists()
    assert json.load(open(art_dir / "collected-nodeids.json")) == \
        ["test_a.py::test_a", "test_a.py::test_b"]


def test_certify_rejects_on_build_failure(tmp_path):
    repo_url, sha = _origin(tmp_path)
    ws = W.prepare_workspace(repo_url, sha, str(tmp_path / "wt"))
    plugin = str(_ROOT / "src" / "manifest_builder" / "collect_plugin.py")

    class DockerBuildFails:
        def build(self, tag, ctx):
            return 1, "build failed: boom"

    verdict, cert, log, r1, r2 = certify(DockerBuildFails(), ws, plugin, str(tmp_path / "tmp"))
    assert not verdict.accepted
    assert cert["status"] == "REJECTED"
    assert cert["manifest_size"] == 0
    assert cert["reject_reasons"]                              # non-empty
    assert cert["completeness"]["skipped_modules"] == []      # well-formed (no KeyError in _cmd_verify)
    assert cert["agent"]["runner"] == "claude code"           # provenance populated on reject path
