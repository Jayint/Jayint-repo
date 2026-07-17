#!/usr/bin/env python3
"""DockerAgentModel — plugs the slim v3 core into the RAT eval harness.

This model asks the project-local RATV3Adapter to run scripts/run_v3_e2e.py and
turn the resulting setup.sh into an eval Dockerfile.  The rest of the class is
the RAT-side build/run/scoring wrapper.
"""
# eval/models/dockeragent_model.py   (lives in the RAT repo tree)
import os
import re
import shutil
import subprocess
import sys
import time

import weave

# Two repo roots — DISTINCT (this was the original draft's bug):
RAT_ROOT   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # RAT: libkit/, eval/
AGENT_ROOT = os.environ["DOCKERAGENT_ROOT"]            # OUR repo, e.g. /Users/john/rat-bench-integration
sys.path[:0] = [RAT_ROOT, AGENT_ROOT]

from libkit.command import init_output_and_repo                 # noqa: E402  # RAT repo
from eval.common.base_model import BaseEvalModel                # noqa: E402  # RAT repo
from eval.common.utils import TimeoutException                  # noqa: E402  # RAT repo
from rat_v3_adapter import RATV3Adapter                         # noqa: E402  # OUR repo

RP  = f"{RAT_ROOT}/libkit/tools/run_pytest.py"
RPC = f"{RAT_ROOT}/libkit/tools/run_pytest_collect.py"
_FULL_GIT_OID = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


def _positive_env_seconds(name: str, default: int) -> int:
    """Read a positive timeout without letting malformed env break a run."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _local_image_platform(image: str) -> str:
    """Return the locally cached base image platform without contacting a registry."""
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Os}}/{{.Architecture}}"],
            check=True, capture_output=True, text=True, timeout=60,
        )
        platform = proc.stdout.strip()
        return platform if "/" in platform else ""
    except Exception:
        return ""

class DockerAgentModel(BaseEvalModel):
    llm: str
    num_turn: int = 30
    base_image: str = "auto"

    @weave.op
    def predict(self, full_name: str) -> dict:
        start = time.time()
        slug = full_name.lower().replace("/", "-")
        image, container = f"dockeragent-eval-{slug}", f"dockeragent-{slug}"
        out_dir = f"{self.root_path}/output/{full_name}"
        ctx     = f"{out_dir}/eval_build"                  # CLEAN build context (avoid the agent's huge workplace/)
        ok = {"root_path": self.root_path, "full_name": full_name}
        # Best-effort metadata — populated incrementally; never let collection break the run.
        meta = {
            "requested_model": self.llm,
            "base_image": self.base_image,
            "build_platform": "",
            "head_sha": "",
            "evaluated_head_sha": "",
            "revision_match": None,
            "runtime_service_failures": [],
        }
        pytest_timeout = _positive_env_seconds("RAT_PYTEST_TIMEOUT", 1800)
        collect_timeout = _positive_env_seconds("RAT_PYTEST_COLLECT_TIMEOUT", 300)
        try:
            try:
                init_output_and_repo(self.root_path, full_name, renew=True)
                if os.path.isdir(ctx):
                    shutil.rmtree(ctx)
                os.makedirs(ctx, exist_ok=True)

                # 1) Run THIS checkout's v3 core -> setup.sh -> eval Dockerfile.
                res = RATV3Adapter(root_path=self.root_path, output_dir=out_dir).process_repo(
                    full_name,
                    base_image=self.base_image,
                    model=self.llm,
                    timeout=self.timeout,
                    max_cycles=self.num_turn,
                    execution_mode="incremental",
                    reuse_existing=(os.environ.get("RAT_V3_REUSE_SETUP") == "1"),
                )
                # Propagate resolved base_image from agent result if available.
                try:
                    if res.get("base_image"):
                        meta["base_image"] = res["base_image"]
                except Exception:
                    pass
                raw_head_sha = res.get("head_sha")
                if isinstance(raw_head_sha, str) and _FULL_GIT_OID.fullmatch(raw_head_sha.strip()):
                    meta["head_sha"] = raw_head_sha.strip().lower()
                elif raw_head_sha:
                    return {
                        "status": "error",
                        "failure_reason": "source_revision_invalid",
                        "error": "adapter returned an invalid source head_sha",
                        **ok,
                        **meta,
                    }
                dockerfile = res.get("dockerfile")
                if not dockerfile:
                    adapter_status = res.get("status")
                    return {
                        "status": adapter_status if adapter_status in {"error", "timeout"} else "error",
                        "failure_reason": res.get("failure_reason") or "no_dockerfile",
                        "error": f"agent produced no Dockerfile: {res.get('logs', {}).get('error')}",
                        **ok,
                        **meta,
                    }
                if not meta["head_sha"]:
                    return {
                        "status": "error",
                        "failure_reason": "source_revision_missing",
                        "error": "adapter produced a Dockerfile without an exact source head_sha",
                        **ok,
                        **meta,
                    }
                self._check_timeout(start, "agent")

                # 2) Ensure pytest, write a clean build context, build.
                if "pytest" not in dockerfile:
                    dockerfile = dockerfile.rstrip() + "\nRUN pip install --no-cache-dir pytest\n"
                with open(f"{ctx}/Dockerfile", "w") as f:
                    f.write(dockerfile)
                for name, content in (res.get("setup_scripts") or {}).items():   # any files the Dockerfile COPYs
                    with open(f"{ctx}/{name}", "w") as f:
                        f.write(content)
                try:
                    # The v3 Sandbox reports the exact platform it certified.
                    # Prefer that explicit handoff over a mutable local tag,
                    # which another concurrent worker may have retagged.
                    platform = res.get("platform") or _local_image_platform(meta["base_image"])
                    meta["build_platform"] = platform
                    build_cmd = ["docker", "build"]
                    if platform:
                        build_cmd.extend(["--platform", platform])
                    build_cmd.extend(["-t", image, ctx])
                    subprocess.run(build_cmd, check=True, timeout=3600)
                except subprocess.CalledProcessError as e:
                    return {"status": "error", "failure_reason": "build_failed",
                            "error": str(e), **ok, **meta}
                except subprocess.TimeoutExpired as e:
                    return {"status": "timeout", "failure_reason": "docker_timeout",
                            "error": str(e), **ok, **meta}

                # 3) Mount RAT's tools, run them AT /testbed (CWD == repo), copy result JSONs to out_dir.
                W = "/testbed"
                subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True)
                subprocess.run(["docker","run","-d","--name",container,"-w",W,
                                "-e",f"RAT_PYTEST_TIMEOUT={pytest_timeout}",
                                "-e",f"RAT_PYTEST_COLLECT_TIMEOUT={collect_timeout}",
                                "-v",f"{RP}:/run_pytest.py","-v",f"{RPC}:/run_pytest_collect.py",
                                image,"tail","-f","/dev/null"], check=True, timeout=600)
                subprocess.run(["docker","exec",container,"mkdir","-p",f"{W}/logs"], check=True, timeout=600)

                # Verify the running evaluator checkout independently.  Keep
                # head_sha as the expected source revision; never overwrite it
                # with an unexpected checkout merely to make the row look valid.
                try:
                    sha_proc = subprocess.run(
                        ["docker", "exec", container, "git", "-C", W, "rev-parse", "HEAD"],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    actual_head_sha = sha_proc.stdout.strip()
                except Exception as exc:
                    meta["revision_match"] = False
                    return {
                        "status": "error",
                        "failure_reason": "revision_verification_failed",
                        "error": str(exc),
                        **ok,
                        **meta,
                    }
                if not _FULL_GIT_OID.fullmatch(actual_head_sha):
                    meta["evaluated_head_sha"] = actual_head_sha
                    meta["revision_match"] = False
                    return {
                        "status": "error",
                        "failure_reason": "revision_verification_failed",
                        "error": "evaluator returned an invalid HEAD object id",
                        **ok,
                        **meta,
                    }
                meta["evaluated_head_sha"] = actual_head_sha.lower()
                meta["revision_match"] = meta["evaluated_head_sha"] == meta["head_sha"]
                if not meta["revision_match"]:
                    return {
                        "status": "error",
                        "failure_reason": "revision_mismatch",
                        "error": (
                            f"evaluator HEAD {meta['evaluated_head_sha']} does not match "
                            f"source HEAD {meta['head_sha']}"
                        ),
                        **ok,
                        **meta,
                    }

                # Docker build layers cannot carry a live daemon into this
                # container.  Replay only adapter-declared, in-image runtime
                # preparation commands; no host Docker socket or sidecar access.
                for service in (res.get("runtime_services") or []):
                    kind = service["kind"]
                    start_proc = subprocess.run(
                        ["docker", "exec", container, "sh", "-lc", service["start"]],
                        check=False, capture_output=True, text=True, timeout=120,
                    )
                    check_proc = subprocess.run(
                        ["docker", "exec", container, "sh", "-lc", service["check"]],
                        check=False, capture_output=True, text=True, timeout=120,
                    )
                    if start_proc.returncode != 0 or check_proc.returncode != 0:
                        meta["runtime_service_failures"].append(kind)

                subprocess.run(["docker","exec",container,"python3","/run_pytest_collect.py"],
                               check=False, timeout=collect_timeout + 120)
                subprocess.run(["docker","cp",f"{container}:{W}/logs/run_pytest_collect_results.json",
                                f"{out_dir}/run_pytest_collect_results.json"], check=False, timeout=600)   # check=False: missing
                subprocess.run(["docker","exec",container,"python3","/run_pytest.py"],
                               check=False, timeout=pytest_timeout + 120)
                subprocess.run(["docker","cp",f"{container}:{W}/logs/run_pytest_results.json",
                                f"{out_dir}/run_pytest_results.json"], check=False, timeout=600)            # JSON => scorer default
                return {"status": "success", "failure_reason": None, **ok, **meta}
            except TimeoutException:
                return {"status": "timeout", "failure_reason": "agent_timeout", **ok, **meta}
            except subprocess.TimeoutExpired as e:
                return {"status": "timeout", "failure_reason": "docker_timeout",
                        "error": str(e), **ok, **meta}
            except Exception as e:
                return {"status": "error", "failure_reason": "repo_error",
                        "error": str(e), **ok, **meta}
            finally:
                subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True)
                subprocess.run(f"docker rmi {image} >/dev/null 2>&1", shell=True)
        except KeyboardInterrupt:
            subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True)
            raise
