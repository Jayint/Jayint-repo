import re
import os
from typing import Optional
from src.language_handlers import LanguageHandler


class Planner:
    MAX_HISTORY_MESSAGES = 24

    def __init__(self, client, model="gpt-4o", language_handler: Optional[LanguageHandler] = None, repo_structure: str = "", log_dir: str = None):
        self.client = client
        self.model = model
        self.history = []
        self.managed_history = []
        self.managed_history_meta = []
        self.managed_step_to_history_index = {}
        self.language_handler = language_handler
        self.log_dir = log_dir
        self.log_counter = 0
        
        # Create log directory if specified
        if self.log_dir:
            os.makedirs(self.log_dir, exist_ok=True)
        
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
            "Current State: The repository has already been cloned and copied into the working directory inside the container.\n\n"
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
            "   - **Final Verification Block**: Before declaring success, run every test command needed to prove the final environment in one final consecutive verification burst. Avoid doing new setup/build steps after the last successful verification command.\n"
            "4. **Finalize**: ONLY output 'Final Answer: Success' when:\n"
            "   - All dependencies are installed AND\n"
            "   - The PROJECT'S test command runs successfully (all tests pass, or fail ONLY due to missing secrets, not setup issues)\n"
            "   - Immediately before `Final Answer: Success`, you MUST emit a `Verification Bundle:` JSON object with EXACTLY these keys:\n"
            "     * `runtime_preparation_commands`: exact previously successful commands that must be run again in the eval container immediately before tests because their effects do NOT persist from image build into test execution (for example, daemon startup commands like `redis-server --daemonize yes`). Use `[]` if none are required.\n"
            "     * `test_commands`: exact previously successful commands whose output proved the final environment works. Wrapper commands such as `make all` are allowed if they really executed tests.\n"
            "   - Every command inside the bundle must exactly match a command you already executed successfully.\n"
            "   - Exclude read-only checks such as `redis-cli ping` from `runtime_preparation_commands`.\n"
            "   - Do NOT put installation, dependency, checkout, clone, build, or other Dockerfile-persistent setup commands into `runtime_preparation_commands`. Examples that must stay OUT of runtime preparation: `apt-get install ...`, `pip install ...`, `composer install ...`, `npm install ...`, `bundle install`, `git clone ...`, `make build`.\n"
            "   - `runtime_preparation_commands` should usually be short and often empty. It is only for ephemeral runtime actions such as starting a local service, exporting a runtime variable, or preparing a daemon needed by the final tests.\n"
            "   - Success responses must follow this exact shape:\n"
            "     Thought: <brief final reasoning>\n"
            "     Verification Bundle:\n"
            "     {\"runtime_preparation_commands\": [...], \"test_commands\": [...]} \n"
            "     Final Answer: Success\n\n"
            "CRITICAL CONSTRAINTS (Environment Limitations):\n"
            "- You are running INSIDE a Docker container, NOT on a host machine.\n"
            "- FORBIDDEN commands: `docker build`, `docker run`, `docker-compose`, `systemctl`, `service`, `dockerd`, `sudo`\n"
            "- If the repository contains a Dockerfile, DO NOT try to build it. Instead, analyze it to understand dependencies and install them directly using package managers (pip, apt, npm, cargo, go, mvn, gem, etc.).\n"
            "- Use ONLY: package managers (pip/uv/apt/yum/npm/yarn/cargo/go/mvn/gradle/gem/bundle/etc.), language runtimes (python/node/go/rust/java/ruby/etc.), and the project's own entry points.\n\n"
            "IMPORTANT:\n"
            "- Only output ONE Thought and ONE Action at a time.\n"
            "- Stop immediately after the Action."
        )

    def plan(self, repo_url=None, last_observation=None, manage_history=True):
        """
        Generates the next step in the ReAct loop.
        Returns: thought, action, content, is_finished, usage_info
        """
        if manage_history:
            if repo_url is None:
                raise ValueError("repo_url is required when manage_history=True")

            # 1. Initialize history with repository information on the first turn
            if not self.history:
                self.history.append({"role": "user", "content": f"Repository URL: {repo_url}"})

            # 2. Append the last observation as a new user message
            if last_observation is not None:
                self.history.append({"role": "user", "content": f"Observation: {last_observation}"})

            self._trim_history()
            message_history = self.history
        else:
            if repo_url and not self.managed_history:
                self.init_managed_history(repo_url)
            message_history = self.managed_history

        # 3. Construct the message list for the API call
        messages = [{"role": "system", "content": self.system_prompt}] + message_history

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
        
        if manage_history:
            # 4. Append the assistant's response (Thought and Action) to history
            self.history.append({"role": "assistant", "content": content})
            self._trim_history()

        # 5. 提取 token 使用量
        usage = response.usage
        usage_info = self._extract_usage(usage)

        thought = self._extract_tag(content, "Thought")
        action = self._extract_tag(content, "Action")
        is_finished = "Final Answer:" in content

        return thought, action, content, is_finished, usage_info

    def init_managed_history(self, repo_url):
        self.managed_history = [{"role": "user", "content": f"Repository URL: {repo_url}"}]
        self.managed_history_meta = [{"step_id": None, "kind": "seed"}]
        self.managed_step_to_history_index = {}

    def append_step(self, step_id, assistant_content, observation_content):
        if not self.managed_history:
            raise ValueError("Managed history is not initialized.")

        assistant_index = len(self.managed_history)
        self.managed_history.append({"role": "assistant", "content": assistant_content})
        self.managed_history_meta.append({"step_id": step_id, "kind": "assistant"})

        observation_index = len(self.managed_history)
        self.managed_history.append(
            {"role": "user", "content": f"Observation: {observation_content}"}
        )
        self.managed_history_meta.append({"step_id": step_id, "kind": "observation"})

        self.managed_step_to_history_index[step_id] = {
            "assistant": assistant_index,
            "observation": observation_index,
        }
        self._trim_managed_history()

    def replace_observation(self, step_id, observation_content):
        indices = self.managed_step_to_history_index.get(step_id)
        if not indices:
            return False
        observation_index = indices.get("observation")
        if observation_index is None or observation_index >= len(self.managed_history):
            return False
        self.managed_history[observation_index]["content"] = (
            f"Observation: {observation_content}"
        )
        return True
    
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

    #滑动窗口优化法
    def _trim_history(self):
        """Keep the repository URL seed plus the most recent turns to cap prompt growth."""
        if len(self.history) <= self.MAX_HISTORY_MESSAGES:
            return
        repo_seed = self.history[0]
        recent_history = self.history[-(self.MAX_HISTORY_MESSAGES - 1):]
        self.history = [repo_seed] + recent_history

    def _trim_managed_history(self):
        if len(self.managed_history) <= self.MAX_HISTORY_MESSAGES:
            return

        repo_seed = self.managed_history[0]
        repo_seed_meta = self.managed_history_meta[0]
        recent_history = self.managed_history[-(self.MAX_HISTORY_MESSAGES - 1):]
        recent_meta = self.managed_history_meta[-(self.MAX_HISTORY_MESSAGES - 1):]

        self.managed_history = [repo_seed] + recent_history
        self.managed_history_meta = [repo_seed_meta] + recent_meta
        self._rebuild_managed_step_index()

    def _rebuild_managed_step_index(self):
        rebuilt = {}
        for index, meta in enumerate(self.managed_history_meta):
            step_id = meta.get("step_id")
            kind = meta.get("kind")
            if step_id is None or kind not in {"assistant", "observation"}:
                continue
            rebuilt.setdefault(step_id, {})[kind] = index
        self.managed_step_to_history_index = rebuilt

    def _extract_usage(self, usage):
        return {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
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
