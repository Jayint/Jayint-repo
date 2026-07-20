#!/usr/bin/env python3
"""Repo2Run Model - Baseline using Repo2Run agent for environment configuration."""

import os
import subprocess
import sys
import time

import weave

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from libkit.command import init_output_and_repo

from eval.common.base_model import BaseEvalModel
from eval.common.utils import TimeoutException


class Repo2RunModel(BaseEvalModel):
    """Repo2Run evaluation model (baseline using Repo2Run config agent)."""

    llm: str
    num_turn: int  # max_turn for Repo2Run Configuration agent

    @weave.op
    def predict(self, full_name: str) -> dict:
        start_time = time.time()

        print(f"\n{'=' * 60}")
        print(f"Processing: {full_name} (Repo2Run)")
        print(f"{'=' * 60}")

        # Container / image names
        container_name = f"repo2run-{full_name.lower().replace('/', '-')}"
        image_name = f"repo2run-eval-{full_name.lower().replace('/', '-')}"

        # Paths
        repo_path = f"{self.root_path}/input/repo/{full_name}"
        output_dir = f"{self.root_path}/output/{full_name}"
        repo2run_repo_path = f"{self.root_path}/utils/repo/{full_name}"

        try:
            try:
                # Step 1: Init output
                print("📁 Initializing output directory...")
                init_output_and_repo(self.root_path, full_name, renew=True)
                self._check_timeout(start_time, "init output")

                # Step 2: Download repo and get SHA
                print("📥 Downloading repository and getting SHA...")
                self._check_timeout(start_time, "download repo")

                # Clone to the standard location
                clone_cmd = f"git clone --depth=1 https://github.com/{full_name}.git {repo_path}"
                subprocess.run(clone_cmd, shell=True, check=True)

                # Get current HEAD SHA
                sha_result = subprocess.run(
                    "git rev-parse HEAD",
                    cwd=repo_path,
                    shell=True,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                sha = sha_result.stdout.strip()
                print(f"   SHA: {sha}")

                # Step 3: Run Repo2Run agent
                print("🤖 Running Repo2Run Configuration Agent...")
                self._check_timeout(start_time, "run Repo2Run")

                # Repo2Run expects to download into utils/repo/{full_name}
                utils_repo_dir = f"{self.root_path}/utils/repo"
                os.makedirs(utils_repo_dir, exist_ok=True)

                # Invoke Repo2Run
                repo2run_main = os.path.join(
                    self.root_path, "Repo2Run/build_agent/main.py"
                )

                if not os.path.exists(repo2run_main):
                    raise Exception(
                        f"Repo2Run entrypoint not found: {repo2run_main}. "
                        "Make sure Repo2Run is checked out in the expected location."
                    )

                repo2run_cmd = [
                    "python3",
                    repo2run_main,
                    "--full_name",
                    full_name,
                    "--sha",
                    sha,
                    "--root_path",
                    self.root_path,
                    "--num_turn",
                    str(self.num_turn),
                    "--llm",
                    self.llm,
                ]

                # Use remaining time as Repo2Run timeout
                elapsed = time.time() - start_time
                remaining_timeout = max(60, self.timeout - elapsed)

                print(f"   Command: {' '.join(repo2run_cmd)}")
                print(f"   Timeout: {remaining_timeout}s")

                result = subprocess.run(
                    repo2run_cmd,
                    timeout=remaining_timeout,
                    capture_output=False,  # Stream output directly
                )

                if result.returncode != 0:
                    raise Exception(f"Repo2Run failed (exit code: {result.returncode})")

                # Step 4: Validate Repo2Run outputs
                print("🔍 Checking Repo2Run outputs...")
                self._check_timeout(start_time, "check outputs")

                track_json = f"{output_dir}/track.json"
                dockerfile_path = f"{output_dir}/Dockerfile"

                if not os.path.exists(track_json):
                    raise Exception(
                        "Repo2Run did not generate track.json; configuration likely failed."
                    )

                if not os.path.exists(dockerfile_path):
                    raise Exception(
                        "Repo2Run did not generate Dockerfile; environment setup may be incomplete."
                    )

                print(f"   ✓ track.json: {track_json}")
                print(f"   ✓ Dockerfile: {dockerfile_path}")

                # Step 5: Build Docker image
                print("🐳 Building Docker image...")
                self._check_timeout(start_time, "build image")

                # Repo2Run puts the generated Dockerfile in output_dir.
                # It already includes git clone and dependency installation.
                build_cmd = ["docker", "build", "-t", image_name, output_dir]
                subprocess.run(build_cmd, check=True)

                # Step 6: Run test container
                print("🧪 Running test container...")
                self._check_timeout(start_time, "run tests")

                # Remove any existing container
                subprocess.run(
                    f"docker rm -f {container_name} > /dev/null 2>&1", shell=True
                )

                # Mount test tools
                run_pytest_tool_path = os.path.join(
                    self.root_path, "libkit/tools/run_pytest.py"
                )
                run_pytest_collect_tool_path = os.path.join(
                    self.root_path, "libkit/tools/run_pytest_collect.py"
                )

                if not os.path.exists(run_pytest_tool_path):
                    raise Exception(
                        f"run_pytest.py tool not found: {run_pytest_tool_path}"
                    )
                if not os.path.exists(run_pytest_collect_tool_path):
                    raise Exception(
                        f"run_pytest_collect.py tool not found: {run_pytest_collect_tool_path}"
                    )

                # Start container (Repo2Run Dockerfile WORKDIR is /)
                run_cmd = [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "-w",
                    "/repo",
                    "-v",
                    f"{run_pytest_tool_path}:/run_pytest.py",
                    "-v",
                    f"{run_pytest_collect_tool_path}:/run_pytest_collect.py",
                    image_name,
                    "tail",
                    "-f",
                    "/dev/null",
                ]
                subprocess.run(run_cmd, check=True)

                # Ensure logs directory exists
                exec_mkdir = [
                    "docker",
                    "exec",
                    container_name,
                    "mkdir",
                    "-p",
                    "/repo/logs",
                ]
                subprocess.run(exec_mkdir, check=True)

                # Run pytest collect
                print("🔍 Running pytest collect...")
                exec_collect = [
                    "docker",
                    "exec",
                    container_name,
                    "python3",
                    "/run_pytest_collect.py",
                ]
                subprocess.run(exec_collect, check=False)

                # Copy collect results
                print("📤 Copying collect results...")
                res_collect_json_container = (
                    "/repo/logs/run_pytest_collect_results.json"
                )
                res_collect_json_host = os.path.join(
                    output_dir, "run_pytest_collect_results.json"
                )
                cp_cmd_collect = [
                    "docker",
                    "cp",
                    f"{container_name}:{res_collect_json_container}",
                    res_collect_json_host,
                ]
                subprocess.run(cp_cmd_collect, check=True)

                # Run pytest
                print("🧪 Running pytest...")
                exec_test = [
                    "docker",
                    "exec",
                    container_name,
                    "python3",
                    "/run_pytest.py",
                ]
                subprocess.run(exec_test, check=False)

                # Copy test results
                print("📤 Copying test results...")
                res_json_container = "/repo/logs/run_pytest_results.json"
                res_json_host = os.path.join(output_dir, "run_pytest_results.json")

                cp_cmd = [
                    "docker",
                    "cp",
                    f"{container_name}:{res_json_container}",
                    res_json_host,
                ]
                subprocess.run(cp_cmd, check=True)

                execution_time = round(time.time() - start_time, 2)
                print(f"✅ Completed. Time: {execution_time}s")

                return {
                    "status": "success",
                    "root_path": self.root_path,
                    "full_name": full_name,
                }

            except subprocess.TimeoutExpired as e:
                print(f"⏱️  Timeout: {e}")
                return {
                    "status": "timeout",
                    "root_path": self.root_path,
                    "full_name": full_name,
                }
            except TimeoutException as e:
                print(f"⏱️  Timeout: {e}")
                return {
                    "status": "timeout",
                    "root_path": self.root_path,
                    "full_name": full_name,
                }
            except subprocess.CalledProcessError as e:
                print(f"❌ Process execution failed: {e}")
                return {
                    "status": "error",
                    "root_path": self.root_path,
                    "full_name": full_name,
                    "error": str(e),
                }
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback

                traceback.print_exc()
                return {
                    "status": "error",
                    "root_path": self.root_path,
                    "full_name": full_name,
                    "error": str(e),
                }

            finally:
                # Cleanup container and image
                print("🧹 Cleaning up container and image...")
                subprocess.run(
                    f"docker rm -f {container_name} > /dev/null 2>&1", shell=True
                )
                subprocess.run(f"docker rmi {image_name} > /dev/null 2>&1", shell=True)

        except KeyboardInterrupt:
            print("\n⚠️  Interrupted...")
            subprocess.run(
                f"docker rm -f {container_name} > /dev/null 2>&1", shell=True
            )
            subprocess.run(f"docker rmi {image_name} > /dev/null 2>&1", shell=True)
            raise

        finally:
            # Compute duration
            execution_time = round(time.time() - start_time, 2)
            print(f"⏱️  Total time: {execution_time}s")
