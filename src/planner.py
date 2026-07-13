import re
import os
from typing import Any, Dict, Optional
from src.language_handlers import LanguageHandler
from src.constants import DEFAULT_LLM_MODEL


class Planner:
    DEFAULT_PROMPT_BUDGET_TOKENS = 180000
    DEFAULT_COMPLETION_RESERVE_TOKENS = 8000

    def __init__(
        self,
        client,
        model=DEFAULT_LLM_MODEL,
        language_handler: Optional[LanguageHandler] = None,
        repo_structure: str = "",
        maven_repository_hints: str = "",
        log_dir: str = None,
        prompt_budget_tokens: int = None,
        completion_reserve_tokens: int = None,
        history_token_budget: int = None,
        enable_long_term_memory: bool = False,
        benchmark_evaluation_target: Optional[Dict[str, Any]] = None,
    ):
        self.client = client
        self.model = model
        self.history = []
        self.managed_history = []
        self.managed_history_meta = []
        self.managed_step_to_history_index = {}
        self.language_handler = language_handler
        self.log_dir = log_dir
        self.log_counter = 0
        self.prompt_budget_tokens = (
            prompt_budget_tokens
            if prompt_budget_tokens is not None
            else self.DEFAULT_PROMPT_BUDGET_TOKENS
        )
        self.completion_reserve_tokens = (
            completion_reserve_tokens
            if completion_reserve_tokens is not None
            else self.DEFAULT_COMPLETION_RESERVE_TOKENS
        )
        self.history_token_budget = history_token_budget
        self.enable_long_term_memory = enable_long_term_memory
        self.benchmark_evaluation_target = benchmark_evaluation_target or {}
        
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

        maven_repository_section = ""
        if maven_repository_hints:
            maven_repository_section = (
                "Project Maven Repository Hints:\n"
                f"{maven_repository_hints}\n\n"
            )
        
        prompt_sections = [
            "You are an expert environment configuration agent. Your task is to set up a Docker "
            "environment for a given GitHub repository so that its code can run successfully.",
            "Current State: The repository has already been cloned and copied into the working directory inside the container.",
        ]

        if structure_section:
            prompt_sections.append(structure_section.rstrip())
        if maven_repository_section:
            prompt_sections.append(maven_repository_section.rstrip())
        if language_instructions:
            prompt_sections.append(language_instructions.rstrip())

        action_format = (
            "Action: <bash command to execute, __ROLLBACK__, or __RETRIEVE_MEMORY__>"
            if self.enable_long_term_memory
            else "Action: <bash command to execute, or __ROLLBACK__>"
        )

        prompt_sections.extend([
            "RESPONSE FORMAT (always follow):\n"
            "Thought: <your reasoning>\n"
            f"{action_format}\n"
            "The Observation is produced ONLY by the host system after it executes your Action. You are the planner, not the executor: never write, predict, simulate, or continue with an Observation yourself. Your response must end immediately after the Action line.",

            "READ THESE FIRST (highest-priority rules):\n"
            "- **No Excuses Rule**: If the test command(s) you intend to put in the final `Verification Bundle` fail, partially pass, or show any failure count, you CANNOT output 'Final Answer: Success'. Continue fixing the environment until those final verification commands pass. For ordinary setup with no benchmark target, this means the project's native tests should pass. If a `Benchmark Evaluation Target` is provided, target-covering commands that execute the changed test files are the mandatory final proof; broader project suites are useful diagnostics, but unrelated pre-existing failures in broad suites should be documented and should not cause endless troubleshooting once the target-covering commands pass.\n"
            "- **[SYSTEM] Warnings Are Binding**: If the host-provided command result starts with '[SYSTEM] ⚠️  TEST FAILURE DETECTED', you must attempt to fix the failing tests.\n"
            "- **No Bypassing Tests**: Run the project's real test command. Do not create substitute tests or claim success from manual checks alone.\n"
            "- **No `sudo` In This Container**: Do not use `sudo`. In this container, `sudo` may be unavailable even when you already have permission to install packages directly. If you need a system package such as PostgreSQL, first try installing it directly with commands like `apt-get update && apt-get install -y <package>`.\n"
            "- **Explicit Rollback Tool**: Ordinary command failures do NOT automatically roll back the container. If you believe a failed mutating command left the environment in a bad or uncertain state, you may request a restore to the last successful snapshot by outputting exactly `Action: __ROLLBACK__`.",

            "CRITICAL CONSTRAINTS (Environment Limitations):\n"
            "- You are running INSIDE a Docker container, NOT on a host machine.\n"
            "- FORBIDDEN commands: `docker build`, `docker run`, `docker-compose`, `systemctl`, `dockerd`, `sudo`\n"
            "- If the repository contains a Dockerfile, DO NOT try to build it. Instead, analyze it to understand dependencies and install them directly using package managers (pip, apt, npm, cargo, go, mvn, gem, etc.).\n"
            "- Use ONLY: package managers (pip/uv/apt/yum/npm/yarn/cargo/go/mvn/gradle/gem/bundle/etc.), language runtimes (python/node/go/rust/java/ruby/etc.), and the project's own entry points.",

            "WORKFLOW:\n"
            "1. Inspect dependency, build, README/CI, and test configuration files as needed.\n"
            "2. Install the dependencies, tools, and local services needed by the repository.\n"
            "3. Run the project's native verification command(s). If tools, test dependencies, or services are missing, fix the environment rather than bypassing tests.\n"
            "4. Missing secrets/API keys may be documented only when the remaining failures are clearly secret-only.",

            "ROLLBACK STRATEGY:\n"
            "- **When Rollback Is Appropriate**: Consider `__ROLLBACK__` after a failed package-manager/install step, a failed config edit, a failed database initialization/startup sequence, or any failed multi-step mutation that may have left partial state behind.\n"
            "- **When Rollback Is Usually NOT Appropriate**: Do not use `__ROLLBACK__` for read-only search commands, health checks, connection probes, or ordinary test failures unless you have evidence the environment itself was changed or corrupted.\n"
            "- **Split Mutation From Verification**: Avoid chaining a mutating step and a probe/test in one command. Prefer one action for the mutation, then a separate action for the verification, so you can decide whether rollback is necessary based on what failed.",
        ])

        if self.enable_long_term_memory:
            prompt_sections.append(
                "LONG-TERM MEMORY TOOL:\n"
                "- After a concrete command failure, you may request relevant prior setup lessons by outputting exactly `Action: __RETRIEVE_MEMORY__`.\n"
                "- Prefer this before trying more speculative fixes when a failure is repeated, non-obvious, or related to package managers, apt broken state, Maven mirrors, Python/PHP dependency compatibility, local services/daemons, network/mirror behavior, or unreliable verification signals.\n"
                "- If the latest Observation contains a [Long-Term Memory Hint], seriously consider retrieving memory as the next Action unless the fix is already obvious from that Observation.\n"
                "- Do NOT use memory retrieval as your first action. It is only for learning from a recent failure.\n"
                "- Retrieved memories are suggestions, not proof. You must still run real setup commands and project tests to verify the environment."
            )

        prompt_sections.extend([
            "LOCAL SERVICE RULES:\n"
            "- **External Services Are Part of Environment Setup**: Missing PostgreSQL/MySQL/Redis/RabbitMQ/MinIO/Elasticsearch/Kafka or other required local services is NOT equivalent to missing secrets. If tests fail because a required service is unavailable, connection-refused, not started, or not configured, you MUST treat that as an environment/setup problem and continue fixing it.\n"
            "- **Match The Required Service, Do Not Swap Backends**: If repository config or test output clearly shows that a local database/cache/broker/search/object-store service is required, first try to install and start that same kind of service. Do NOT replace it with a different backend (for example, swapping PostgreSQL for H2 or replacing Redis with a mock) unless the repository itself already provides an official alternative profile, documented test mode, or supported fallback.\n"
            "- **A Client Is Not A Service**: Client packages or CLI probes are not enough. The actual server/daemon must be running and reachable at the host/port expected by the tests.\n"
            "- **Do Not Misclassify Service Failures As Acceptable**: Errors such as database connection refused, missing local broker/storage endpoints, failed migrations caused by unavailable infrastructure, or application boot failures due to missing services are setup failures, not acceptable final-test failures.",

            "FINAL VERIFICATION STRATEGY:\n"
            "- **Final Verification Block**: Before declaring success, run every test command needed to prove the final environment in one final consecutive verification burst. Avoid doing new setup/build steps after the last successful verification command.\n"
            "- **Do Not Truncate Verification Output**: Do NOT pipe project test commands through `head`, `tail`, or similar output-limiting filters when deciding whether the environment works. Run the full test command; long output will be handled by observation compression.\n"
            "- **Differentiate Exploration vs Final Evaluation**: During setup you may run exploratory probes or narrow smoke tests to learn about the project, but the commands in the final `Verification Bundle` must be the ones you want a fresh evaluator to run to validate the configured environment.\n"
            "- **Benchmark Target Priority**: If the initial user message contains a `Benchmark Evaluation Target`, your final `test_commands` must execute the changed test file(s), or must be project-native wrapper commands that you verified definitely include those changed files. Do not rely solely on a broad wrapper that runs unrelated tests while skipping the changed files. If a broader suite fails only in unrelated files after the changed-file tests pass, stop chasing those unrelated failures and use the successful target-covering commands in the final bundle.\n"
            "- **Prefer Project-Native Final Commands**: For the final `Verification Bundle`, prefer the repository's native or standard verification commands (README/CI/build tool entry points, module-aware project test commands, or the most representative reproducible commands you found). Avoid opportunistic one-off smoke checks unless they are truly the best reproducible proof of correctness available.\n"
            "- **Service-Dependent Projects Need Representative Final Tests**: If repository config or test settings clearly depend on local services, do NOT end with only narrow single-test or unit-test commands. Your final `Verification Bundle` should include at least one broader, representative test command that exercises the configured service-dependent environment.",

            "FINAL SUCCESS CONTRACT:\n"
            "- ONLY output 'Final Answer: Success' when all dependencies are installed AND the final verification command(s) you will report run successfully. With no benchmark target, this should be the PROJECT'S native test command(s). With a benchmark target, this must include command(s) that execute the changed test file(s) or verified wrapper command(s) that include them.\n"
            "- Immediately before `Final Answer: Success`, you MUST emit a `Verification Bundle:` JSON object with EXACTLY these keys:\n"
            "  * `runtime_preparation_commands`: exact previously successful commands that must be run again in the eval container immediately before tests because their effects do NOT persist from image build into test execution (for example, daemon startup commands like `redis-server --daemonize yes`). Use `[]` if none are required.\n"
            "  * `test_commands`: exact previously successful commands whose output proved the final environment works. Wrapper commands such as `make all` are allowed if they really executed tests.\n"
            "- Every command inside the bundle must exactly match a command you already executed successfully.\n"
            "- Exclude read-only checks such as `redis-cli ping` from `runtime_preparation_commands`.\n"
            "- Do NOT put installation, dependency, checkout, clone, build, or other Dockerfile-persistent setup commands into `runtime_preparation_commands`. Examples that must stay OUT of runtime preparation: `apt-get install ...`, `pip install ...`, `composer install ...`, `npm install ...`, `bundle install`, `git clone ...`, `make build`.\n"
            "- `runtime_preparation_commands` should usually be short and often empty. It is only for ephemeral runtime actions such as starting a local service, exporting a runtime variable, or preparing a daemon needed by the final tests.\n"
            "- Success responses must follow this exact shape:\n"
            "  Thought: <brief final reasoning>\n"
            "  Verification Bundle:\n"
            "  {\"runtime_preparation_commands\": [...], \"test_commands\": [...]} \n"
            "  Final Answer: Success",

            "IMPORTANT RESPONSE RULES:\n"
            "- Only output ONE Thought and ONE Action at a time.\n"
            "- Stop immediately after the Action.\n"
            "- Never generate command results, `Observation:`, a second `Action:`, `Verification Bundle:`, or `Final Answer:` in the same response as an Action.\n"
            "- Do not simulate command execution results. You must wait for the host-provided command result before planning the next step.",
        ])

        self.system_prompt = "\n\n".join(prompt_sections)

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
                self.history.append({"role": "user", "content": self._build_seed_content(repo_url)})

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

        # 4. Parse the model output before storing it. Some OpenAI-compatible
        # endpoints do not reliably honor stop sequences, so keep only the
        # executable single-step ReAct message in history.
        content = response.choices[0].message.content
        thought = self._extract_tag(content, "Thought")
        action = self._extract_tag(content, "Action")
        final_answer = self.extract_final_answer(content)
        history_content = self.sanitize_assistant_content(content, thought=thought, action=action)

        # Log both the raw provider response and the executable message that the
        # agent will actually use. This keeps overgenerated Observations visible
        # for debugging without making them look like trusted trajectory state.
        self._log_llm_call("output", {
            "content": content,
            "sanitized_content": history_content,
            "overgenerated": self._assistant_output_was_sanitized(content, history_content),
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
        })

        if manage_history:
            self.history.append({"role": "assistant", "content": history_content})
            self._trim_history()

        # 5. 提取 token 使用量
        usage = response.usage
        usage_info = self._extract_usage(usage)

        is_finished = final_answer is not None and not action

        return thought, action, content, is_finished, usage_info

    def init_managed_history(self, repo_url):
        self.managed_history = [{"role": "user", "content": self._build_seed_content(repo_url)}]
        self.managed_history_meta = [{"step_id": None, "kind": "seed"}]
        self.managed_step_to_history_index = {}

    def _build_seed_content(self, repo_url):
        sections = [f"Repository URL: {repo_url}"]
        benchmark_target = self._format_benchmark_evaluation_target()
        if benchmark_target:
            sections.append(benchmark_target)
        return "\n\n".join(sections)

    def _format_benchmark_evaluation_target(self):
        target = self.benchmark_evaluation_target or {}
        changed_test_files = [
            str(path).strip()
            for path in target.get("changed_test_files", []) or []
            if str(path).strip()
        ]
        framework_clues = [
            str(clue).strip()
            for clue in target.get("test_framework_clues", []) or []
            if str(clue).strip()
        ]
        if not changed_test_files and not framework_clues:
            return ""

        lines = [
            "Benchmark Evaluation Target:",
            "Multi-Docker-Eval will later apply a benchmark test patch. You are doing environment setup only.",
            "Do NOT apply the benchmark test patch. Do NOT modify project source/test semantics to satisfy it.",
            "Environment compatibility edits needed only to run the existing test harness are allowed if verified.",
            "Use the following metadata only to choose representative final Verification Bundle test commands.",
        ]
        if changed_test_files:
            lines.append("Changed test files from the benchmark test patch:")
            lines.extend(f"- {path}" for path in changed_test_files[:20])
        if framework_clues:
            lines.append("Test framework clues observed in the benchmark test patch:")
            lines.extend(f"- {clue}" for clue in framework_clues[:12])
        lines.append(
            "Before declaring success, final test_commands must execute these changed test files, "
            "or use a project-native command you have verified definitely includes them. Do not rely solely "
            "on a wrapper command if it only runs unrelated tests. If broader project tests fail only in "
            "unrelated files after these changed-file tests pass, document that briefly in your Thought and "
            "finish with the successful target-covering commands."
        )
        return "\n".join(lines)

    def append_step(self, step_id, assistant_content, observation_content):
        if not self.managed_history:
            raise ValueError("Managed history is not initialized.")

        assistant_index = len(self.managed_history)
        sanitized_assistant_content = self.sanitize_assistant_content(assistant_content)
        self.managed_history.append({"role": "assistant", "content": sanitized_assistant_content})
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
                f.write("================================ Raw AI Message =================================\n\n")
                f.write(f"{data['content']}\n\n")
                sanitized_content = data.get("sanitized_content")
                if sanitized_content:
                    f.write("================================ Executable Message Used By Agent =================================\n\n")
                    f.write(f"{sanitized_content}\n\n")
                    if data.get("overgenerated"):
                        f.write(
                            "Note: The raw model output contained generated Observation/future-step "
                            "content. The agent executed and stored only the executable message above.\n\n"
                        )
                f.write("================================ Metadata =================================\n\n")
                f.write(f"- Model: {self.model}\n")
                f.write(f"- Prompt Tokens: {data['usage']['prompt_tokens']}\n")
                f.write(f"- Completion Tokens: {data['usage']['completion_tokens']}\n")
                f.write(f"- Total Tokens: {data['usage']['total_tokens']}\n")
            
            # Increment counter after completing a full input/output pair
            self.log_counter += 1

    def _trim_history(self):
        """Keep history within a token budget instead of a fixed message count."""
        if len(self.history) <= 1:
            return
        self.history = self._trim_messages_to_budget(self.history)

    def _trim_managed_history(self):
        if len(self.managed_history) <= 1:
            return
        trimmed_history, trimmed_meta = self._trim_managed_messages_to_budget(
            self.managed_history,
            self.managed_history_meta,
        )
        self.managed_history = trimmed_history
        self.managed_history_meta = trimmed_meta
        self._rebuild_managed_step_index()

    def _trim_messages_to_budget(self, messages):
        budget = self._get_history_token_budget()
        if budget <= 0 or len(messages) <= 1:
            return messages

        seed = messages[0]
        seed_tokens = self._estimate_message_tokens(seed)
        remaining_budget = max(0, budget - seed_tokens)
        kept_tail = []

        for message in reversed(messages[1:]):
            message_tokens = self._estimate_message_tokens(message)
            if not kept_tail:
                kept_tail.append(message)
                remaining_budget -= message_tokens
                continue
            if message_tokens > remaining_budget:
                continue
            kept_tail.append(message)
            remaining_budget -= message_tokens

        kept_tail.reverse()
        return [seed] + kept_tail

    def _trim_managed_messages_to_budget(self, messages, meta):
        budget = self._get_history_token_budget()
        if budget <= 0 or len(messages) <= 1:
            return messages, meta

        seed = messages[0]
        seed_meta = meta[0]
        seed_tokens = self._estimate_message_tokens(seed)
        remaining_budget = max(0, budget - seed_tokens)

        step_ranges = []
        current_step_id = None
        current_start = None

        for index in range(1, len(meta)):
            step_id = meta[index].get("step_id")
            if step_id != current_step_id:
                if current_step_id is not None:
                    step_ranges.append((current_step_id, current_start, index))
                current_step_id = step_id
                current_start = index

        if current_step_id is not None:
            step_ranges.append((current_step_id, current_start, len(meta)))

        kept_ranges = []
        for _step_id, start, end in reversed(step_ranges):
            step_tokens = sum(
                self._estimate_message_tokens(messages[idx])
                for idx in range(start, end)
            )
            if not kept_ranges:
                kept_ranges.append((start, end))
                remaining_budget -= step_tokens
                continue
            if step_tokens > remaining_budget:
                continue
            kept_ranges.append((start, end))
            remaining_budget -= step_tokens

        kept_ranges.reverse()
        trimmed_history = [seed]
        trimmed_meta = [seed_meta]
        for start, end in kept_ranges:
            trimmed_history.extend(messages[start:end])
            trimmed_meta.extend(meta[start:end])

        return trimmed_history, trimmed_meta

    def _get_history_token_budget(self):
        if self.history_token_budget is not None:
            return self.history_token_budget

        return max(
            1024,
            self.prompt_budget_tokens
            - self.completion_reserve_tokens
            - self._estimate_text_tokens(self.system_prompt),
        )

    def _estimate_text_tokens(self, text):
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _estimate_message_tokens(self, message):
        content = message.get("content", "") if message else ""
        # Small fixed overhead per chat message keeps the estimate conservative.
        return self._estimate_text_tokens(content) + 8

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

    def _assistant_output_was_sanitized(self, raw_content, sanitized_content):
        raw = (raw_content or "").strip()
        sanitized = (sanitized_content or "").strip()
        return bool(raw and sanitized and raw != sanitized)

    def sanitize_assistant_content(self, text, thought=None, action=None):
        """
        Keep only the single-step ReAct message that should enter planner history.

        Some model/provider combinations may ignore stop sequences and continue by
        inventing Observations or future Actions. Those tokens are useful in raw
        logs for debugging, but they must not become trajectory history.
        """
        if not text:
            return ""

        if thought is None:
            thought = self._extract_tag(text, "Thought")
        if action is None:
            action = self._extract_tag(text, "Action")

        lines = []
        if thought:
            lines.append(f"Thought: {thought}")
        if action:
            lines.append(f"Action: {action}")

        if lines:
            return "\n".join(lines)

        return self._strip_generated_future_trajectory(text)

    def _strip_generated_future_trajectory(self, text):
        stop_pattern = (
            r"\n(?:Observation|Action|Thought|Verification Bundle|Final Answer):"
        )
        match = re.search(stop_pattern, text)
        if match:
            return text[:match.start()].strip()
        return text.strip()

    def _extract_tag(self, text, tag):
        labels = r"Thought|Action|Observation|Verification Bundle|Final Answer"
        pattern = rf"{tag}:\s*(.*?)(?=\n(?:{labels}):|$)"
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

    def extract_final_answer(self, text):
        if not text:
            return None

        for match in re.finditer(r"Final Answer:\s*(Success|Failure)\b", text, re.IGNORECASE):
            prefix = text[:match.start()].rstrip()
            if prefix and prefix[-1] in {'"', "'", "`"}:
                continue
            return match.group(1).capitalize()
        return None
