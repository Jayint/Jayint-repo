#!/usr/bin/env python3
"""DockerAgentModel — plugs our DockerAgent into the RAT eval harness.

This model wraps multi_docker_eval_adapter.process_single_instance, builds a
per-repo Docker image from the returned Dockerfile, mounts RAT's pytest runner
tools, runs them inside the container, and copies the result JSON files back out
for the RAT scorers to consume.
"""
# eval/models/dockeragent_model.py   (lives in the RAT repo tree)
import os, sys, time, subprocess, weave

# Two repo roots — DISTINCT (this was the original draft's bug):
RAT_ROOT   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # RAT: libkit/, eval/
AGENT_ROOT = os.environ["DOCKERAGENT_ROOT"]            # OUR repo, e.g. /Users/john/rat-bench-integration
sys.path[:0] = [RAT_ROOT, AGENT_ROOT]

from libkit.command import init_output_and_repo                 # RAT repo
from eval.common.base_model import BaseEvalModel                # RAT repo
from eval.common.utils import TimeoutException                  # RAT repo
from multi_docker_eval_adapter import MultiDockerEvalAdapter    # OUR repo

RP  = f"{RAT_ROOT}/libkit/tools/run_pytest.py"
RPC = f"{RAT_ROOT}/libkit/tools/run_pytest_collect.py"

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
        try:
            try:
                init_output_and_repo(self.root_path, full_name, renew=True)
                os.makedirs(ctx, exist_ok=True)

                # 1) Run OUR agent -> docker_res dict. The eval Dockerfile (a STRING) is self-contained:
                #    it `git clone`s the repo into /testbed and bakes the verified setup recipe.
                res = MultiDockerEvalAdapter(output_dir=out_dir).process_single_instance(
                    {"instance_id": full_name.replace("/", "__"),
                     "repo_url": f"https://github.com/{full_name}", "language": "python"},
                    base_image=self.base_image, model=self.llm, max_steps=self.num_turn,
                    enable_artifact_preflight=False)           # RAT scores it; skip our Multi-Docker-Eval preflight
                res = res.get(full_name.replace("/", "__"), res)        # tolerate {id: result} or result
                dockerfile = res.get("dockerfile")
                if not dockerfile:
                    raise Exception(f"agent produced no Dockerfile: {res.get('logs', {}).get('error')}")
                self._check_timeout(start, "agent")

                # 2) Ensure pytest, write a clean build context, build.
                if "pytest" not in dockerfile:
                    dockerfile = dockerfile.rstrip() + "\nRUN pip install --no-cache-dir pytest\n"
                with open(f"{ctx}/Dockerfile", "w") as f: f.write(dockerfile)
                for name, content in (res.get("setup_scripts") or {}).items():   # any files the Dockerfile COPYs
                    with open(f"{ctx}/{name}", "w") as f: f.write(content)
                subprocess.run(["docker", "build", "-t", image, ctx], check=True)

                # 3) Mount RAT's tools, run them AT /testbed (CWD == repo), copy result JSONs to out_dir.
                W = "/testbed"
                subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True)
                subprocess.run(["docker","run","-d","--name",container,"-w",W,
                                "-v",f"{RP}:/run_pytest.py","-v",f"{RPC}:/run_pytest_collect.py",
                                image,"tail","-f","/dev/null"], check=True)
                subprocess.run(["docker","exec",container,"mkdir","-p",f"{W}/logs"], check=True)
                subprocess.run(["docker","exec",container,"python3","/run_pytest_collect.py"], check=False)
                subprocess.run(["docker","cp",f"{container}:{W}/logs/run_pytest_collect_results.json",
                                f"{out_dir}/run_pytest_collect_results.json"], check=False)   # check=False: missing
                subprocess.run(["docker","exec",container,"python3","/run_pytest.py"], check=False)
                subprocess.run(["docker","cp",f"{container}:{W}/logs/run_pytest_results.json",
                                f"{out_dir}/run_pytest_results.json"], check=False)            # JSON => scorer default
                return {"status": "success", **ok}
            except (TimeoutException, subprocess.TimeoutExpired):
                return {"status": "timeout", **ok}
            except Exception as e:
                return {"status": "error", "error": str(e), **ok}
            finally:
                subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True)
                subprocess.run(f"docker rmi {image} >/dev/null 2>&1", shell=True)
        except KeyboardInterrupt:
            subprocess.run(f"docker rm -f {container} >/dev/null 2>&1", shell=True); raise
