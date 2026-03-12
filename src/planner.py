import re
import os
import json
from typing import Optional
from src.language_handlers import LanguageHandler

class Planner:
    def __init__(self, client, model="gpt-4o", language_handler: Optional[LanguageHandler] = None, repo_structure: str = "", log_dir: str = None):
        self.client = client
        self.model = model
        self.history = []
        self.total_cost = 0.0  # 累计成本
        self.language_handler = language_handler
        self.log_dir = log_dir
        self.log_counter = 0
        
        # Create log directory if specified
        if self.log_dir:
            os.makedirs(self.log_dir, exist_ok=True)
        
        # 2026年2月官方价格 (美元/1M tokens)
        self.pricing = {
            # GPT-5 系列
            "gpt-5.2": {"input": 1.75, "output": 14.00},
            "gpt-5.2-pro": {"input": 21.00, "output": 168.00},
            "gpt-5.2-codex": {"input": 1.75, "output": 14.00},
            "gpt-5.1": {"input": 1.25, "output": 10.00},
            "gpt-5.1-codex": {"input": 1.25, "output": 10.00},
            "gpt-5": {"input": 1.25, "output": 10.00},
            "gpt-5-pro": {"input": 15.00, "output": 120.00},
            "gpt-5-mini": {"input": 0.25, "output": 2.00},
            "gpt-5-nano": {"input": 0.05, "output": 0.40},
            
            # GPT-4 系列
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4-turbo": {"input": 10.00, "output": 30.00},
            "gpt-4": {"input": 30.00, "output": 60.00},
            "gpt-4.1": {"input": 2.00, "output": 8.00},
            "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
            "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
            
            # GPT-3.5 系列
            "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
            
            # o 系列（推理模型）
            "o1": {"input": 15.00, "output": 60.00},
            "o1-pro": {"input": 150.00, "output": 600.00},
            "o3": {"input": 2.00, "output": 8.00},
            "o3-mini": {"input": 1.10, "output": 4.40},
            "o4-mini": {"input": 1.10, "output": 4.40},
        }
        
        # Build system prompt with language-specific instructions if available
        language_instructions = ""
        if self.language_handler:
            language_instructions = self.language_handler.get_setup_instructions() + "\n"
        
        # Add repository structure if available
        structure_section = ""
        if repo_structure:
            structure_section = f"Repository Structure:\n```\n{repo_structure}\n```\n\n"
        
        self.system_prompt = (
            "You are an expert environment configuration agent. Your task is to set up a Docker "
            "environment for a given GitHub repository so that its code can run successfully.\n"
            "Current State: The repository has already been cloned and mounted into the working directory .\n\n"
            + structure_section
            + language_instructions +
            "Use the following ReAct format:\n"
            "Thought: <your reasoning>\n"
            "Action: <bash command to execute>\n"
            "Observation: <result of the command, will be provided by the system, DO NOT GENERATE THIS>\n\n"
            "Mission Guidelines:\n"
            "1. **Analyze & Setup**: Identify dependency files and install all necessary packages/tools.\n"
            "2. **Read README**: After setup, read `README.md` to find 'QuickStart' or startup instructions.\n"
            "3. **Verification** (MANDATORY - Must pass before claiming Success):\n"
            "   - After setup, you MUST run the project's tests to verify the environment works correctly.\n"
            "   - For Ruby projects with gemspec: Run `bundle exec rake` or `bundle exec rspec` or the test command in the project's test files.\n"
            "   - For Python: Run `pytest` or `python -m pytest`.\n"
            "   - For Node.js: Run `npm test` or `yarn test`.\n"
            "   - For PHP: Run `vendor/bin/phpunit` (after composer install).\n"
            "   - **CRITICAL**: If tests fail, you CANNOT output 'Final Answer: Success'. You must continue fixing the environment until tests pass.\n"
            "   - **No Excuses Rule**: You are STRICTLY FORBIDDEN from declaring success when tests are failing, regardless of any reasoning such as: 'the project is old', 'it is a compatibility issue', 'I have spent too much time', 'environment constraints prevent running tests', 'other tests pass', or 'I manually verified functionality'. ALL tests must pass. No exceptions.\n"
            "   - **Partial Pass Is NOT Success**: If the test output shows 'Failed: N' or 'N failed' or any 'not ok' lines, even N=1, you MUST NOT declare success. 400/403 passing is a FAILURE, not a success. Only 0 failures qualifies as success.\n"
            "   - **[SYSTEM] Warnings Are Binding**: If the Observation starts with '[SYSTEM] ⚠️  TEST FAILURE DETECTED', you are ABSOLUTELY FORBIDDEN from outputting 'Final Answer: Success' in your next response. You must attempt to fix the failing tests.\n"
            "   - **No Bypassing Tests**: You MUST run the PROJECT'S test command (e.g., `vendor/bin/phpunit`, `pytest`, `npm test`). You are NOT allowed to:\n"
            "     * Create your own test scripts to verify functionality\n"
            "     * Use alternative verification methods (e.g., manual PHP scripts, simple load tests)\n"
            "     * Claim success based on 'core functionality works' without running the actual test suite\n"
            "   - **Environment Limits Are Not Excuses**: If the environment lacks required tools (e.g., zip, git for composer), you must find a solution (e.g., install them, use alternative base image approach), NOT bypass the tests.\n"
            "   - **Test Dependency Fix**: If tests fail due to missing test libraries (e.g., Ruby's `stub` method not found), install the required library (e.g., `gem install mocha` or add to Gemfile). DO NOT skip tests with `--exclude`.\n"
            "   - **Rollback Mechanism**: The system automatically rolls back to the pre-execution state if a command fails. You do not need to manually revert changes; simply continue with the next approach after a failure.\n"
            "   - **Secret/API_KEY Handling**: Only if tests fail due to missing API_KEYs/secrets (not setup issues), document the required keys and continue.\n"
            "4. **Finalize**: ONLY output 'Final Answer: Success' when:\n"
            "   - All dependencies are installed AND\n"
            "   - The PROJECT'S test command runs successfully (all tests pass, or fail ONLY due to missing secrets, not setup issues)\n\n"
            "CRITICAL CONSTRAINTS (Environment Limitations):\n"
            "- You are running INSIDE a Docker container, NOT on a host machine.\n"
            "- FORBIDDEN commands: `docker build`, `docker run`, `docker-compose`, `systemctl`, `service`, `dockerd`, `sudo`\n"
            "- If the repository contains a Dockerfile, DO NOT try to build it. Instead, analyze it to understand dependencies and install them directly using package managers (pip, apt, npm, cargo, go, mvn, gem, etc.).\n"
            "- Use ONLY: package managers (pip/uv/apt/yum/npm/yarn/cargo/go/mvn/gradle/gem/bundle/etc.), language runtimes (python/node/go/rust/java/ruby/etc.), and the project's own entry points.\n\n"
            "IMPORTANT:\n"
            "- Only output ONE Thought and ONE Action at a time.\n"
            "- Stop immediately after the Action."
        )

    def plan(self, repo_url, last_observation=None):
        """
        Generates the next step in the ReAct loop.
        Returns: thought, action, content, is_finished, cost_info
        """
        # 1. Initialize history with repository information on the first turn
        if not self.history:
            self.history.append({"role": "user", "content": f"Repository URL: {repo_url}"})

        # 2. Append the last observation as a new user message
        if last_observation is not None:
            self.history.append({"role": "user", "content": f"Observation: {last_observation}"})

        # 3. Construct the message list for the API call
        messages = [{"role": "system", "content": self.system_prompt}] + self.history

        # Log the LLM call input if logging is enabled
        self._log_llm_call("input", messages)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            stop=["Observation:"]
        )

        content = response.choices[0].message.content
        
        # Log the LLM call output
        self._log_llm_call("output", {
            "content": content,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
        })
        
        # 4. Append the assistant's response (Thought and Action) to history
        self.history.append({"role": "assistant", "content": content})

        # 5. 计算本次调用成本
        usage = response.usage
        cost_info = self._calculate_cost(usage)

        thought = self._extract_tag(content, "Thought")
        action = self._extract_tag(content, "Action")
        is_finished = "Final Answer:" in content

        return thought, action, content, is_finished, cost_info
    
    def _log_llm_call(self, call_type, data):
        """Log LLM call input/output to file, similar to image_selector_logs format"""
        if not self.log_dir:
            return
        
        log_file = os.path.join(self.log_dir, f"{self.log_counter}.md")
        
        if call_type == "input":
            # Format similar to image_selector_logs
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(f"##### LLM INPUT (setup call #{self.log_counter}) #####\n")
                f.write("================================ Human Message =================================\n\n")
                for msg in data:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if role == "system":
                        f.write(f"[{role.upper()}]\n{content}\n\n")
                    elif role == "user":
                        f.write(f"{content}\n\n")
                    elif role == "assistant":
                        f.write(f"[{role.upper()}]\n{content}\n\n")
        else:
            # Append output to the same file
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write("================================ AI Message =================================\n\n")
                f.write(f"{data['content']}\n\n")
                f.write("================================ Metadata =================================\n\n")
                f.write(f"- Model: {self.model}\n")
                f.write(f"- Prompt Tokens: {data['usage']['prompt_tokens']}\n")
                f.write(f"- Completion Tokens: {data['usage']['completion_tokens']}\n")
                f.write(f"- Total Tokens: {data['usage']['total_tokens']}\n")
            
            # Increment counter after completing a full input/output pair
            self.log_counter += 1

    def _calculate_cost(self, usage):
        """计算单次 API 调用的成本"""
        input_tokens = usage.prompt_tokens
        output_tokens = usage.completion_tokens
        total_tokens = usage.total_tokens
        
        # 获取当前模型的价格，如果没有则使用 gpt-4o 的价格
        price = self.pricing.get(self.model, self.pricing["gpt-4o"])
        
        # 计算成本（美元）
        input_cost = (input_tokens / 1_000_000) * price["input"]
        output_cost = (output_tokens / 1_000_000) * price["output"]
        step_cost = input_cost + output_cost
        
        self.total_cost += step_cost
        
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "step_cost": step_cost,
            "total_cost": self.total_cost
        }

    def _extract_tag(self, text, tag):
        pattern = rf"{tag}:\s*(.*?)(?=\n\w+:|$)"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            content = match.group(1).strip()
            # 1. Remove triple backticks (code blocks)
            content = re.sub(r"^```bash\n?", "", content)
            content = re.sub(r"^```\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
            # 2. Remove single backticks (command substitution characters)
            if content.startswith('`') and content.endswith('`'):
                content = content[1:-1].strip()
            return content.strip()
        return None
