from __future__ import annotations

import json
import os
import subprocess

from src.manifest_builder.protected import hash_in_image
from src.manifest_builder.types import CollectionResult

COLLECT_CMD = "pytest --collect-only -q -p no:cacheprovider -p manifest_collect_plugin"
HARDENED = ["--network", "none", "--cpus", "2", "--memory", "4g", "--pids-limit", "512",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL"]

_COLLECTION_AFFECTING = ("conftest.py", "pytest.ini", "tox.ini", "setup.cfg",
                         "pyproject.toml", "sitecustomize.py", "usercustomize.py")


def find_injected_collection_files(exec_fn, src_root, protected):
    """Untracked files under src_root that could add or suppress collected tests: pytest
    config/hook files, .pth files, and test modules that are NOT in the pristine (tracked)
    protected set. The tracked-only in-image hash gate can't see these, so any hit is an
    injection. `exec_fn(argv) -> (rc, out)`; returns a sorted list of repo-relative paths."""
    root = src_root.rstrip("/")
    rc, out = exec_fn(["find", root, "-not", "-path", "*/.git/*", "-type", "f"])
    if rc != 0:
        # Fail CLOSED: if the scan itself can't run (missing findutils, permission error, or an
        # adversarial removal of `find`), integrity is unverifiable, so treat it as injected.
        return [f"<injection-scan-failed rc={rc}>"]
    prot = set(protected)
    injected = []
    for line in out.splitlines():
        p = line.strip()
        if not p:
            continue
        rel = p[len(root) + 1:] if p.startswith(root + "/") else p
        base = rel.rsplit("/", 1)[-1]
        suspect = (base in _COLLECTION_AFFECTING or base.endswith(".pth")
                   or (base.startswith("test_") and base.endswith(".py"))
                   or base.endswith("_test.py"))
        if suspect and rel not in prot:
            injected.append(rel)
    return sorted(injected)


class BuildError(RuntimeError):
    pass


def _default_run(argv, timeout=None):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def parse_collection_result(exit_code: int, plugin_json: dict) -> CollectionResult:
    return CollectionResult(
        exit_code=exit_code,
        collected=tuple(plugin_json.get("collected", [])),
        collect_errors=tuple(plugin_json.get("collect_errors", [])),
        skipped_modules=tuple(plugin_json.get("skipped_modules", [])),
        deselected=tuple(plugin_json.get("deselected", [])),
    )


def build_timeout() -> int:
    """Wall-clock cap on `docker build`, in seconds. A repo whose test modules import heavyweight
    frameworks (mlflow's ML "flavor" suites pull sklearn/torch/tensorflow/xgboost/...) legitimately
    needs a multi-GB image, and 1h was not enough to build one. This is an INFRASTRUCTURE cap, not
    a gate clause -- raising it cannot make the 5-clause gate easier to pass, only let an honest
    build finish. Read per call (not at import) so it stays overridable without a module reload."""
    return int(os.environ.get("MANIFEST_BUILD_TIMEOUT", "3600"))


class Docker:
    def __init__(self, run=None):
        self._run = run or _default_run

    def build(self, tag, context_dir):
        # A timeout is just another way for a build to fail, so return a non-zero rc instead of
        # letting TimeoutExpired escape. Escaping bypasses certify()'s BuildError handler and
        # propagates out of build_one, ABANDONING THE REPO -- every remaining attempt is lost to
        # one slow Dockerfile (this is exactly how mlflow lost all 3 attempts). As a non-zero rc
        # it becomes a rejected attempt, and the next attempt still gets its shot.
        t = build_timeout()
        try:
            return self._run(["docker", "build", "-t", tag, context_dir], timeout=t)
        except subprocess.TimeoutExpired:
            return 124, (f"docker build exceeded MANIFEST_BUILD_TIMEOUT={t}s. The image is too "
                         "slow to build: install fewer/lighter dependencies, use prebuilt wheels "
                         "(avoid source builds), and merge RUN layers.")

    def image_id(self, tag):
        rc, out = self._run(["docker", "image", "inspect", "-f", "{{.Id}}", tag])
        return out.strip() if rc == 0 else ""

    def run_detached(self, tag, name, workdir):
        self._run(["docker", "run", "-d", "--name", name, *HARDENED, "-w", workdir,
                   tag, "sleep", "infinity"])

    def exec(self, name, argv, env=None, timeout=None):
        cmd = ["docker", "exec"]
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [name, *argv]
        return self._run(cmd, timeout=timeout)

    def cp_in(self, name, src, dst):
        self._run(["docker", "cp", src, f"{name}:{dst}"])

    def cp_out(self, name, src, dst):
        self._run(["docker", "cp", f"{name}:{src}", dst])

    def rm(self, name):
        self._run(["docker", "rm", "-f", name])

    def rmi(self, tag):
        return self._run(["docker", "rmi", "-f", tag])


def collect_once(docker, name, src_root, plugin_host_path, tmp_out) -> CollectionResult:
    docker.exec(name, ["mkdir", "-p", "/manifest"])
    docker.cp_in(name, plugin_host_path, "/manifest/manifest_collect_plugin.py")
    env = {"PYTHONPATH": "/manifest", "MANIFEST_COLLECT_OUT": "/manifest/out.json",
           "PYTHONDONTWRITEBYTECODE": "1"}
    rc, _ = docker.exec(name, ["bash", "-lc", f"cd {src_root} && {COLLECT_CMD} {src_root}"],
                        env=env, timeout=1800)
    docker.cp_out(name, "/manifest/out.json", tmp_out)
    with open(tmp_out) as f:
        pj = json.load(f)
    return parse_collection_result(rc, pj)


def build_and_collect(docker, workspace, plugin_host_path, tmp_dir, protected):
    tag = f"manifest-{workspace.slug}"
    name = tag + "-run"
    build_rc, build_log = docker.build(tag, workspace.path)
    if build_rc != 0:
        raise BuildError(build_log)
    img = docker.image_id(tag)
    docker.rm(name)
    docker.run_detached(tag, name, workspace.src_root)
    try:
        in_img = hash_in_image(lambda argv: docker.exec(name, argv), workspace.src_root, protected)
        injected = find_injected_collection_files(
            lambda argv: docker.exec(name, argv), workspace.src_root, protected)
        r1 = collect_once(docker, name, workspace.src_root, plugin_host_path,
                          os.path.join(tmp_dir, "r1.json"))
        r2 = collect_once(docker, name, workspace.src_root, plugin_host_path,
                          os.path.join(tmp_dir, "r2.json"))
    finally:
        docker.rm(name)
    return img, build_log, r1, r2, in_img, injected
