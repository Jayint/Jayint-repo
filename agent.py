import os
import re
import json
import argparse
import subprocess
import shutil
from openai import OpenAI
from src.sandbox import Sandbox
from src.planner import Planner
from src.synthesizer import Synthesizer
from src.image_selector import select_base_image
from dotenv import load_dotenv

# Load environment variables (OPENAI_API_KEY, etc.)
# override=True ensures .env values take precedence over system env vars
load_dotenv(override=True)

class DockerAgent:
    def __init__(self, repo_url, base_image="auto", model="qwen3-max-2026-01-23", workplace="workplace", base_commit=None):
        self.repo_url = repo_url
        self.workplace = os.path.abspath(workplace)
        
        # 1. Prepare local workplace and clone repo
        self._prepare_workplace()
        
        # 2. If base_commit is specified, checkout before image selection
        # so that LLM analyzes the actual files at base_commit, not the latest HEAD
        if base_commit:
            self._checkout_commit(base_commit)
            print(f"Checked out commit: {base_commit}")
        
        # 3. Initialize LLM client first (needed for image selection)
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_API_BASE")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables.")
            
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url if base_url else None
        )
        
        # 4. Auto-detect base image if set to "auto" or not specified
        platform_override = None
        log_dir = os.path.join(self.workplace, "image_selector_logs")
        if base_image == "auto":
            print("[DockerAgent] Analyzing repository to select optimal base image...")
            selected_image, language_handler, docs, platform_override = select_base_image(
                repo_path=self.workplace,
                client=self.client,
                model=model,
                platform="linux",
                log_dir=log_dir
            )
            base_image = selected_image
            self.language_handler = language_handler
            self.repo_docs = docs
            print(f"[DockerAgent] Selected base image: {base_image}")
            if platform_override:
                print(f"[DockerAgent] Platform override: {platform_override} (for ARM64 compatibility)")
            print(f"[DockerAgent] Image selection logs saved to: {log_dir}")
        else:
            # Use specified base image with legacy detection for Python
            if base_image.startswith("python:"):
                detected = self._detect_python_image()
                if detected:
                    print(f"[Auto-detect] Using base image: {detected} (from project files)")
                    base_image = detected
            self.language_handler = None
            self.repo_docs = ""
        
        # 5. Setup Sandbox with volume mounting
        # Mapping local workplace to /app in container
        volumes = {self.workplace: {'bind': '/app', 'mode': 'rw'}}
        self.sandbox = Sandbox(
            base_image=base_image, 
            workdir="/app", 
            volumes=volumes,
            platform=platform_override  # Use linux/amd64 if ARM64 issues detected
        )
        self.platform_override = platform_override  # Expose for adapter to read
        
        # 6. Initialize Planner and Synthesizer
        # Load repository structure and relevant config files from image_selector_logs if available
        repo_structure = ""
        config_files_content = ""
        
        # Load structure.txt
        structure_file = os.path.join(log_dir, "structure.txt")
        if os.path.exists(structure_file):
            try:
                with open(structure_file, 'r') as f:
                    repo_structure = f.read()
                print(f"[DockerAgent] Loaded repository structure from: {structure_file}")
            except Exception as e:
                print(f"[DockerAgent] Warning: Could not read structure.txt: {e}")
        
        # Load relevant config files from summary.json
        summary_file = os.path.join(log_dir, "summary.json")
        if os.path.exists(summary_file):
            try:
                with open(summary_file, 'r') as f:
                    summary = json.load(f)
                relevant_files = summary.get("relevant_files", [])
                config_contents = []
                for rel_file in relevant_files:
                    file_path = os.path.join(self.workplace, rel_file)
                    if os.path.exists(file_path) and os.path.getsize(file_path) < 50000:  # Skip files > 50KB
                        try:
                            with open(file_path, 'r') as f:
                                content = f.read()
                            # Truncate very long files to first 200 lines
                            lines = content.split('\n')
                            if len(lines) > 200:
                                content = '\n'.join(lines[:200]) + f"\n... ({len(lines) - 200} more lines truncated)"
                            config_contents.append(f"=== {rel_file} ===\n{content}\n")
                        except Exception as e:
                            print(f"[DockerAgent] Warning: Could not read {rel_file}: {e}")
                if config_contents:
                    config_files_content = "\n".join(config_contents)
                    print(f"[DockerAgent] Loaded {len(config_contents)} relevant config files")
            except Exception as e:
                print(f"[DockerAgent] Warning: Could not read summary.json: {e}")
        
        # Combine structure and config files
        combined_repo_info = repo_structure
        if config_files_content:
            combined_repo_info += "\n\n=== Relevant Configuration Files ===\n\n" + config_files_content
        
        # Setup log directory for LLM calls (similar to image_selector_logs)
        setup_log_dir = os.path.join(self.workplace, "setup_logs")
        os.makedirs(setup_log_dir, exist_ok=True)
        
        self.planner = Planner(self.client, model=model, language_handler=self.language_handler, repo_structure=combined_repo_info, log_dir=setup_log_dir)
        self.synthesizer = Synthesizer(base_image=base_image)
        print(f"[DockerAgent] Setup logs will be saved to: {setup_log_dir}")

    def _detect_python_image(self):
        """
        Scan project files to determine the required Python version.
        Returns a docker image tag like 'python:3.9', or None if undetermined.
        Priority: .python-version > pyproject.toml > setup.cfg > setup.py > CI configs > tox.ini
        """
        wp = self.workplace

        def _usable(ver_str):
            """Only accept Python 3.6+; discard Python 2.x or very old 3.x"""
            try:
                parts = ver_str.split('.')
                major, minor = int(parts[0]), int(parts[1])
                return major == 3 and minor >= 6
            except Exception:
                return False

        def _parse_version_spec(spec):
            """Extract a concrete version from a specifier like '>=3.8,<3.11' or '==3.9.*'"""
            spec = spec.strip().replace(' ', '')
            # exact: ==3.9 or ==3.9.*
            m = re.search(r'==\s*(\d+\.\d+)', spec)
            if m and _usable(m.group(1)):
                return m.group(1)
            # lower-bound: >=3.x
            m = re.search(r'>=\s*(\d+\.\d+)', spec)
            if m and _usable(m.group(1)):
                return m.group(1)
            # ~=3.x
            m = re.search(r'~=\s*(\d+\.\d+)', spec)
            if m and _usable(m.group(1)):
                return m.group(1)
            return None

        # 1. .python-version (e.g. "3.9.7" or "3.9")
        pv_file = os.path.join(wp, ".python-version")
        if os.path.exists(pv_file):
            with open(pv_file) as f:
                ver = f.read().strip().split('\n')[0]
            m = re.match(r'(\d+\.\d+)', ver)
            if m and _usable(m.group(1)):
                return f"python:{m.group(1)}"

        # 2. pyproject.toml  requires-python
        pp = os.path.join(wp, "pyproject.toml")
        if os.path.exists(pp):
            with open(pp) as f:
                content = f.read()
            m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                ver = _parse_version_spec(m.group(1))
                if ver:
                    return f"python:{ver}"

        # 3. setup.cfg  python_requires
        sc = os.path.join(wp, "setup.cfg")
        if os.path.exists(sc):
            with open(sc) as f:
                content = f.read()
            m = re.search(r'python_requires\s*=\s*(.+)', content)
            if m:
                ver = _parse_version_spec(m.group(1))
                if ver:
                    return f"python:{ver}"

        # 4. setup.py  python_requires
        sp = os.path.join(wp, "setup.py")
        if os.path.exists(sp):
            with open(sp) as f:
                content = f.read()
            m = re.search(r'python_requires\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                ver = _parse_version_spec(m.group(1))
                if ver:
                    return f"python:{ver}"

        # 5. GitHub Actions workflow files
        actions_dir = os.path.join(wp, ".github", "workflows")
        if os.path.isdir(actions_dir):
            for fname in os.listdir(actions_dir):
                if not fname.endswith(('.yml', '.yaml')):
                    continue
                with open(os.path.join(actions_dir, fname)) as f:
                    content = f.read()
                # python-version: "3.x" or ["3.x", ...]
                versions = re.findall(r'python-version["\s:]+["\[]*(3\.\d+)', content)
                usable = [v for v in versions if _usable(v)]
                if usable:
                    return f"python:{sorted(usable)[0]}"  # lowest usable

        # 6. .travis.yml
        travis = os.path.join(wp, ".travis.yml")
        if os.path.exists(travis):
            with open(travis) as f:
                content = f.read()
            versions = re.findall(r'["\s-]+(3\.\d+)["\s]', content)
            usable = [v for v in versions if _usable(v)]
            if usable:
                return f"python:{sorted(usable)[0]}"

        # 7. tox.ini  envlist (pyXY style, Python 3.6+)
        tox = os.path.join(wp, "tox.ini")
        if os.path.exists(tox):
            with open(tox) as f:
                content = f.read()
            versions = [(int(a), int(b)) for a, b in re.findall(r'py(\d)(\d+)', content)
                        if int(a) == 3 and int(b) >= 6]
            if versions:
                major, minor = sorted(versions)[0]
                return f"python:{major}.{minor}"

        return None

    def _prepare_workplace(self):
        """Clones the repository to the local workplace directory."""
        if os.path.exists(self.workplace):
            print(f"Cleaning up existing workplace at {self.workplace}...")
            shutil.rmtree(self.workplace)
        
        os.makedirs(self.workplace)
        print(f"Cloning {self.repo_url} into {self.workplace}...")
        try:
            subprocess.run(
                ["git", "clone", self.repo_url, "."],
                cwd=self.workplace,
                check=True,
                capture_output=True
            )
            print("Clone successful.")
        except subprocess.CalledProcessError as e:
            print(f"Clone failed: {e.stderr.decode()}")
            raise e

    def _checkout_commit(self, commit: str):
        """Checkout a specific git commit in the workplace directory."""
        try:
            subprocess.run(
                ["git", "checkout", commit],
                cwd=self.workplace,
                check=True,
                capture_output=True
            )
        except subprocess.CalledProcessError as e:
            print(f"Warning: Failed to checkout commit {commit}: {e.stderr.decode()}")

    def run(self, max_steps=30, keep_container=False):
        """Runs the ReAct loop to configure the environment."""
        print(f"Starting agent for repository: {self.repo_url}")
        observation = None
        configuration_success = False  # 成功标志位
        
        try:
            for step in range(max_steps):
                print(f"\n{'='*20} Step {step + 1} {'='*20}")
                
                # 1. Plan next step
                thought, action, raw_llm_output, is_finished, cost_info = self.planner.plan(
                    self.repo_url, observation
                )
                
                # 输出成本信息
                print(f"\n[💰 Cost] Input: {cost_info['input_tokens']} tokens, "
                      f"Output: {cost_info['output_tokens']} tokens, "
                      f"Step: ${cost_info['step_cost']:.6f}, "
                      f"Total: ${cost_info['total_cost']:.6f}")
                
                if is_finished:
                    print("\n[Finished] Agent has reached a conclusion.")
                    print(raw_llm_output)
                    # 检查是否是成功结论：LLM声明成功 AND 有实质性构建指令
                    if "Final Answer: Success" in raw_llm_output:
                        effective_instructions = [
                            instr for instr in self.synthesizer.instructions
                            if not any(
                                instr.strip().startswith(f"RUN {noop}")
                                for noop in ["ls", "cat", "echo", "pwd", "env", "grep", "find", "head", "tail"]
                            )
                        ]
                        if effective_instructions:
                            configuration_success = True
                        else:
                            print("[Warning] Agent claimed success but no effective build instructions were recorded.")
                            print("[Warning] Dockerfile would be empty/useless. Marking as FAILED.")
                    break

                if thought:
                    print(f"\n[Thought]\n{thought}")
                
                if not action:
                    print("\n[Warning] No Action detected. Asking Planner to clarify.")
                    observation = "Error: No command found. Please specify an action in 'Action: <command>' format."
                    continue

                print(f"\n[Action]\n{action}")
                
                # 2. Execute Action in Sandbox
                success, observation = self.sandbox.execute(action)
                
                print(f"\n[Observation]\n{observation if observation.strip() else '(No output)'}")
                
                # 检测 API Key 相关错误
                self._detect_api_key_issues(observation)
                
                # 3. Synthesize if successful
                if success:
                    self.synthesizer.record_success(action)
                else:
                    print(f"\n[System] Command failed. Sandbox rolled back to previous state.")

            # 4. Final Output - 只有配置成功才生成文档
            if configuration_success:
                print(f"\n{'='*20} Environment Configuration Complete {'='*20}")
                # 生成 Dockerfile 到 workplace 目录
                dockerfile_path = os.path.join(self.workplace, "Dockerfile")
                self.synthesizer.generate_dockerfile(file_path=dockerfile_path)
                self.synthesizer.generate_quickstart_with_llm(self.workplace, self.client, model=self.planner.model)
            else:
                print(f"\n{'='*20} Environment Configuration FAILED {'='*20}")
                print("[Warning] Configuration did not complete successfully. No documentation will be generated.")
            
        except Exception as e:
            print(f"An error occurred during execution: {e}")
        finally:
            self.sandbox.close(keep_alive=keep_container)

    def _detect_api_key_issues(self, observation):
        """检测命令输出中是否包含 API Key 相关错误"""
        if not observation:
            return
        
        observation_lower = observation.lower()
        
        # 常见的 API Key 错误模式
        api_key_patterns = [
            ("openai_api_key", ["openai_api_key", "openai api key", "invalid api key", "api key not found"]),
            ("anthropic_api_key", ["anthropic_api_key", "anthropic api key", "claude api key"]),
            ("api_key", ["missing api key", "api key required", "no api key", "api_key not set"]),
            ("access_token", ["access token", "access_token", "invalid token", "token required"]),
        ]
        
        for key_name, patterns in api_key_patterns:
            if any(pattern in observation_lower for pattern in patterns):
                self.synthesizer.record_api_key_hint(key_name, observation[:200])
                print(f"[Detected] API Key requirement: {key_name.upper()}")
                break

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM-based Docker Environment Configuration Agent")
    parser.add_argument("repo_url", help="GitHub repository URL to configure")
    parser.add_argument("--image", default="auto", help="Base Docker image (default: auto-detect, or specify like 'python:3.10', 'node:18')")
    parser.add_argument("--model", default="qwen3-max-2026-01-23", help="LLM model to use (default: qwen3-max-2026-01-23)")
    parser.add_argument("--steps", type=int, default=30, help="Maximum number of steps (default: 30)")
    parser.add_argument("--keep-container", action="store_true", help="Keep container running after completion for inspection")
    
    args = parser.parse_args()
    
    agent = DockerAgent(args.repo_url, base_image=args.image, model=args.model)
    agent.run(max_steps=args.steps, keep_container=args.keep_container)
