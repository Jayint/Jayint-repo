import os
import argparse
import subprocess
import shutil
from openai import OpenAI
from src.sandbox import Sandbox
from src.planner import Planner
from src.synthesizer import Synthesizer
from dotenv import load_dotenv

# Load environment variables (OPENAI_API_KEY, etc.)
load_dotenv()

class DockerAgent:
    def __init__(self, repo_url, base_image="python:3.10", model="gpt-5", workplace="workplace"):
        self.repo_url = repo_url
        self.workplace = os.path.abspath(workplace)
        
        # 1. Prepare local workplace and clone repo
        self._prepare_workplace()
        
        # 2. Setup Sandbox with volume mounting
        # Mapping local workplace to /app in container
        volumes = {self.workplace: {'bind': '/app', 'mode': 'rw'}}
        self.sandbox = Sandbox(base_image=base_image, workdir="/app", volumes=volumes)
        
        # 3. Initialize LLM client
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_API_BASE")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables.")
            
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url if base_url else None
        )
        self.planner = Planner(self.client, model=model)
        self.synthesizer = Synthesizer(base_image=base_image)

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
                    # 检查是否是成功结论
                    if "Final Answer: Success" in raw_llm_output:
                        configuration_success = True
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
                self.synthesizer.generate_dockerfile()
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
    parser.add_argument("--image", default="python:3.10", help="Base Docker image (default: python:3.10)")
    parser.add_argument("--model", default="gpt-4o", help="LLM model to use (default: gpt-4o)")
    parser.add_argument("--steps", type=int, default=30, help="Maximum number of steps (default: 30)")
    parser.add_argument("--keep-container", action="store_true", help="Keep container running after completion for inspection")
    
    args = parser.parse_args()
    
    agent = DockerAgent(args.repo_url, base_image=args.image, model=args.model)
    agent.run(max_steps=args.steps, keep_container=args.keep_container)
