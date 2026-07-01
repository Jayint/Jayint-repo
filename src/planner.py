import re
import os
from typing import Any, Dict, Optional
from src.language_handlers import LanguageHandler
from src.constants import DEFAULT_LLM_MODEL
from src.evaluation_target import is_ratbench_target, normalize_evaluation_target


class Planner:
    DEFAULT_PROMPT_BUDGET_TOKENS = 130000
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
        environment_plan_context: str = "",
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
        self.evaluation_target = normalize_evaluation_target(self.benchmark_evaluation_target)
        self.environment_plan_context = environment_plan_context or ""
        
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
        if self.environment_plan_context:
            prompt_sections.append(
                "PLANNING AGENT CONTEXT (advisory execution map):\n"
                "- Before exploratory setup, consult the typed task graph and ordered todo-list below.\n"
                "- Prefer actions that advance the next unmet hard dependency in the todo-list.\n"
                "- If a real Observation contradicts the graph, trust the Observation and adapt.\n"
                "- The current plan is also materialized inside the container at `/tmp/repo2run_environment_plan.md` and `/tmp/repo2run_environment_plan.json`.\n"
                "- You may request the latest host-managed plan at any time with `Action: __VIEW_PLAN__`.\n"
                "- Command hints are not proof and are not final Verification Bundle entries until executed successfully.\n\n"
                f"{self.environment_plan_context.rstrip()}"
            )
        if language_instructions:
            prompt_sections.append(language_instructions.rstrip())

        action_format = (
            "Action: <bash command to execute, __ROLLBACK__, __VIEW_PLAN__, or __RETRIEVE_MEMORY__>"
            if self.enable_long_term_memory
            else "Action: <bash command to execute, __ROLLBACK__, or __VIEW_PLAN__>"
        )

        prompt_sections.extend([
            "SINGLE-STEP RESPONSE PROTOCOL:\n"
            "Thought: <your reasoning>\n"
            f"{action_format}\n"
            "- The Observation is produced ONLY by the host system after it executes your Action.\n"
            "- You are the planner, not the executor: never write, predict, simulate, or continue with an Observation yourself.\n"
            "- Only output ONE Thought and ONE Action at a time.\n"
            "- Stop immediately after the Action.\n"
            "- Never generate command results, `Observation:`, a second `Action:`, `Verification Bundle:`, or `Final Answer:` in the same response as an Action.\n"
            "- Do not simulate command execution results. Your response must end immediately after the Action line.",

            "READ THESE FIRST (highest-priority rules):\n"
            f"{self._no_excuses_rule()}\n"
            f"{self._no_bypassing_tests_rule()}\n"
            "- **Environment-Only Boundary**: Do not create stubs, rewrite tests, or modify source files to make imports pass. You may edit dependency/environment configuration files such as `pyproject.toml`, `.lock` files, `requirements*.txt`, `setup.cfg`, `tox.ini`, or pytest config when they contain dependency or environment conflicts. Fix missing local imports with `PYTHONPATH`, editable install, or package discovery configuration rather than changing source semantics.\n"
            "- **Manifest-First Dependencies**: For Python/Poetry projects, derive dependency installs from `pyproject.toml`, lock files, requirements files, CI, and platform markers before reacting to individual pytest import errors. If the Planning Agent provides a manifest-driven dependency resolution plan, execute its setup steps first. Use pytest errors only to validate or promote a manifest-backed fallback; do not stack arbitrary packages in a long error-driven loop.\n"
            "- **No `sudo` In This Container**: Do not use `sudo`. In this container, `sudo` may be unavailable even when you already have permission to install packages directly. If you need a system package such as PostgreSQL, first try installing it directly with commands like `apt-get update && apt-get install -y <package>`.\n"
            "- **Keep Setup Commands Atomic**: Prefer one state-changing operation per Action. Do not combine package installs, file edits, service setup, generated artifacts, and verification/probe commands with `&&`, `;`, or pipes unless the command is intrinsically atomic and cannot be split. In particular, do not put environment-changing commands and read-only checks/tests in the same Action.\n"
            "- **No Output Truncation Filters**: Do not append `| tail`, `| head`, `| grep`, or similar output-limiting filters to setup, install, or verification commands. The host handles long output. These filters can hide the real failure and make later Dockerfile synthesis wrong.\n"

            "CRITICAL CONSTRAINTS (Environment Limitations):\n"
            "- You are running INSIDE a Docker container, NOT on a host machine.\n"
            "- FORBIDDEN commands: `docker build`, `docker run`, `docker-compose`, `systemctl`, `dockerd`\n"
            "- If the repository contains a Dockerfile, DO NOT try to build it. Instead, analyze it to understand dependencies and install them directly using package managers.\n"

            "WORKFLOW:\n"
            "1. Inspect dependency, build, README/CI, and test configuration files as needed.\n"
            "2. Use the Planning Agent todo-list as your execution map. After a successful planned setup step, inspect the updated next todo with `Action: __VIEW_PLAN__` before starting an unrelated branch.\n"
            "3. Install dependencies from the manifest/lock/CI-derived plan before adding pytest-error-derived packages. If the manifest or lock is internally inconsistent for Linux, repair the dependency configuration and retry from a clean dependency strategy.\n"
            "4. Install the tools and local services needed by the repository.\n"
            f"5. {self._workflow_verification_step()}\n"
            "6. Missing secrets/API keys may be documented only when the remaining failures are clearly secret-only.",

            "ROLLBACK STRATEGY:\n"
            "- Ordinary command failures do NOT automatically roll back the container. Request `Action: __ROLLBACK__` only when a failed mutating step may have left partial or uncertain state behind.\n"
            "- Consider `__ROLLBACK__` after a failed package-manager/install step, a failed config edit, a failed database initialization/startup sequence, or any failed multi-step mutation.\n"
            "- Do not use `__ROLLBACK__` for read-only search commands, health checks, connection probes, or ordinary test failures unless you have evidence the environment itself was changed or corrupted.\n"
            "- If a failed setup command may have partially succeeded, do not rely on that implicit partial state. If a prefix or sub-step is useful, rerun it as its own separate Action so it is confirmed successful; use `__ROLLBACK__` if the partial changes may have polluted the environment.\n"
            "- **Split Mutation From Verification**: Do not chain a mutating step and a probe/test/read-only command in one command. Prefer one action for the mutation, then a separate action for the verification.",
        ])

        if self.enable_long_term_memory:
            prompt_sections.append(
                "LONG-TERM MEMORY TOOL:\n"
                "- After a concrete command failure, you may request relevant prior setup lessons by outputting exactly `Action: __RETRIEVE_MEMORY__`.\n"
                "- Prefer this before trying more speculative fixes when the same problem has resisted several real attempts, multiple speculative fixes have failed, or the next step is still unclear after repeated troubleshooting.\n"
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

            "FINAL VERIFICATION AND SUCCESS:\n"
            f"{self._final_verification_rules()}\n"
            "- **Do Not Truncate Verification Output**: Do NOT pipe the collection command through `head`, `tail`, or similar output-limiting filters when deciding whether the environment works. This same rule applies to setup and install commands. Run the full command; long output will be handled by observation compression.\n"
            f"{self._final_ignore_shortcut_rule()}\n"
            f"{self._service_dependent_rule()}\n"
            "- Immediately before `Final Answer: Success`, you MUST emit a `Verification Bundle:` JSON object with EXACTLY these keys: `runtime_preparation_commands` and `test_commands`.\n"
            "- `runtime_preparation_commands`: exact previously successful commands that must be run again in the eval container immediately before tests because their effects do NOT persist from image build into test execution. Use `[]` if none are required. Exclude read-only checks such as `redis-cli ping`, and do NOT include installation, dependency, checkout, clone, build, or other Dockerfile-persistent setup commands.\n"
            f"{self._test_command_bundle_rule()}\n"
            "- `runtime_preparation_commands` should usually be short and often empty. It is only for ephemeral runtime actions such as starting a local service, exporting a runtime variable, or preparing a daemon needed by the final tests.\n"
            "- Success responses must follow this exact shape:\n"
            "  Thought: <brief final reasoning>\n"
            "  Verification Bundle:\n"
            "  {\"runtime_preparation_commands\": [...], \"test_commands\": [...]} \n"
            "  Final Answer: Success",
        ])

        self.system_prompt = "\n\n".join(prompt_sections)

    def _is_ratbench_target(self) -> bool:
        return is_ratbench_target(self.evaluation_target)

    def _no_excuses_rule(self) -> str:
        if self._is_ratbench_target():
            return (
                "- **No Excuses Rule**: You CANNOT output 'Final Answer: Success' unless the final "
                "verification command(s) you will report have been executed for real and prove that "
                "pytest tests can run in the configured environment. RATBench/ESSR scores full pytest "
                "execution pass rate, not only collection. For ordinary Python setup, first make "
                "`pytest --collect-only -q --disable-warnings` succeed when possible, then run full "
                "pytest (for example `python -m pytest` or the project-native pytest command). "
                "Continue fixing dependency, service, import, configuration, locale, and path failures "
                "that prevent tests from running or unnecessarily reduce pass rate. You may stop with "
                "remaining failures only when pytest has genuinely executed tests and the residual "
                "failures appear to be project logic/assertion issues outside the environment boundary."
            )
        return (
            "- **No Excuses Rule**: You CANNOT output 'Final Answer: Success' unless the final "
            "verification command(s) you will report have been executed for real and prove the "
            "repository's tests are collectable in the configured environment. For ordinary Python "
            "setup, this means `pytest --collect-only -q --disable-warnings` succeeds from the "
            "repository root; for Poetry projects, use `poetry run pytest --collect-only -q "
            "--disable-warnings`. You do NOT need to run the full test suite or make all tests pass. "
            "Collection/import/config errors, missing dependencies, missing services, bad paths, bad "
            "locale, or other fixable environment defects are still setup failures and must be fixed. "
            "If a `Benchmark Evaluation Target` is provided, use the metadata only as a clue for "
            "relevant test framework/files; the final proof should still be Repo2Run-style pytest "
            "collection success."
        )

    def _workflow_verification_step(self) -> str:
        if self._is_ratbench_target():
            return (
                "Run RATBench-style verification in two phases: first use pytest collection "
                "(`pytest --collect-only -q --disable-warnings` or the project-native equivalent) "
                "to diagnose import/config errors, then run full pytest without `--collect-only` to "
                "measure and improve pass rate. If tools, test dependencies, or services are missing, "
                "fix the environment rather than bypassing tests. Temporary `--ignore`, `-k`, or "
                "`--maxfail` narrowing flags are diagnosis only and do not count as final proof."
            )
        return (
            "Run Repo2Run-style verification: `pytest --collect-only -q --disable-warnings` or, "
            "for Poetry projects, `poetry run pytest --collect-only -q --disable-warnings`. If tools, "
            "test dependencies, or services are missing, fix the environment rather than bypassing "
            "tests. Temporary `--ignore` flags are only for diagnosis; they do NOT count as final "
            "proof unless you also change the repository/test configuration so the plain Repo2Run "
            "command now succeeds."
        )

    def _no_bypassing_tests_rule(self) -> str:
        if self._is_ratbench_target():
            return (
                "- **No Bypassing Tests**: Run real pytest collection and real full pytest execution "
                "for the project. Do not create substitute tests, delete tests, rewrite tests, or claim "
                "success from manual import checks alone."
            )
        return (
            "- **No Bypassing Tests**: Run real pytest collection for the project. Do not create "
            "substitute tests or claim success from manual import checks alone."
        )

    def _final_verification_rules(self) -> str:
        if self._is_ratbench_target():
            return (
                "- **Final Verification Block**: Before declaring success, run a full pytest command "
                "without `--collect-only` in one final verification step. Avoid doing new setup/build "
                "steps after the last full pytest execution.\n"
                "- **Target Outcome For Final Verification**: The target is real pytest execution and "
                "maximum environment-achievable pass rate. Prefer a project-native full pytest command "
                "such as `python -m pytest`, `pytest`, or `poetry run pytest`. Collection-only commands "
                "are useful gates but are not final proof for RATBench/ESSR."
            )
        return (
            "- **Final Verification Block**: Before declaring success, run the Repo2Run-style pytest "
            "collection command in one final verification step. Avoid doing new setup/build steps after "
            "the last successful verification command.\n"
            "- **Target Outcome For Final Verification**: The target is successful pytest collection, "
            "not test execution or test passing. Prefer `pytest --collect-only -q --disable-warnings`; "
            "use `poetry run pytest --collect-only -q --disable-warnings` when the project uses Poetry. "
            "If collection succeeds, you may use that command in the final `Verification Bundle`."
        )

    def _final_ignore_shortcut_rule(self) -> str:
        if self._is_ratbench_target():
            return (
                "- **No Final Narrowing Shortcut**: `--ignore`, `-k`, `--maxfail`, or similar narrowing "
                "flags may help diagnose failures, but they are not an acceptable final verification "
                "shortcut by themselves. Final RATBench proof should run the repository's normal pytest "
                "suite as broadly as the configured environment permits."
            )
        return (
            "- **No Final Ignore-Flag Shortcut**: `--ignore`, `-k`, or similar narrowing flags may help "
            "diagnose failures, but they are not an acceptable final verification shortcut by themselves. "
            "Final success means the plain Repo2Run collection command succeeds in the configured repository."
        )

    def _service_dependent_rule(self) -> str:
        if self._is_ratbench_target():
            return (
                "- **Service-Dependent Projects Still Need Real Environment Fixes**: If pytest collection "
                "or full pytest execution fails because repository imports/configuration require local "
                "services, do NOT ignore or mock those failures unless the project documents an official "
                "test profile/fallback. Configure the required service or supported test mode until tests "
                "execute and environment-caused pass-rate loss is minimized."
            )
        return (
            "- **Service-Dependent Projects Still Need Real Environment Fixes**: If pytest collection fails "
            "because repository imports/configuration require local services, do NOT ignore or mock those "
            "failures unless the project documents an official test profile/fallback. Configure the "
            "required service or supported test mode until collection succeeds."
        )

    def _test_command_bundle_rule(self) -> str:
        if self._is_ratbench_target():
            return (
                "- `test_commands`: exact full pytest command(s), without `--collect-only`, that were "
                "previously executed in the final environment and produced real test execution output. "
                "A nonzero exit is acceptable only when tests genuinely ran and the remaining failures "
                "are not environment/setup defects you can fix. Every command inside the bundle must "
                "exactly match a command you already executed. If the verified pytest command used "
                "inline environment variables such as `PATH=... python -m pytest`, keep that inline "
                "prefix in `test_commands` unless the environment assignment was separately executed "
                "successfully as its own runtime preparation command."
            )
        return (
            "- `test_commands`: exact previously successful Repo2Run-style collection command whose output "
            "proved pytest can collect the repository tests in the final environment. Every command inside "
            "the bundle must exactly match a command you already executed successfully."
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
        thought = self._extract_thought(content)
        action = self._extract_tag(content, "Action")
        final_answer = self.extract_final_answer(content)
        if final_answer is not None:
            history_content = self._strip_generated_future_trajectory(content)
        else:
            history_content = self.sanitize_assistant_content(
                content,
                thought=thought,
                action=action,
            )

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

        # A final answer should never be executed as a shell Action. If a model
        # emits both an Action and a final marker in one response, the agent will
        # validate the final Verification Bundle against previously observed
        # commands before accepting it.
        is_finished = final_answer is not None

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
        if not changed_test_files and not framework_clues and not self._is_ratbench_target():
            return ""

        lines = [
            "Benchmark Evaluation Target:",
            f"Evaluation target: {self.evaluation_target}.",
            "You are doing environment setup only.",
            "Do NOT apply the benchmark test patch. Do NOT modify project source/test semantics to satisfy it.",
            "Environment compatibility edits needed only to run the existing test harness are allowed if verified.",
        ]
        if self._is_ratbench_target():
            lines.append(
                "RATBench/ESSR will execute pytest and score pass rate. Treat collection as a diagnostic "
                "gate, then run full pytest and fix environment-caused failures that reduce pass rate."
            )
        else:
            lines.append(
                "Use the following metadata only as clues for the relevant test framework/files when "
                "making pytest collection succeed."
            )
        if changed_test_files:
            lines.append("Changed test files from the benchmark test patch:")
            lines.extend(f"- {path}" for path in changed_test_files[:20])
        if framework_clues:
            lines.append("Test framework clues observed in the benchmark test patch:")
            lines.extend(f"- {clue}" for clue in framework_clues[:12])
        if self._is_ratbench_target():
            lines.append(
                "Before declaring success, the final proof should include a full pytest execution command "
                "from the repository root or project-native test environment. The command should not use "
                "`--collect-only`."
            )
        else:
            lines.append(
                "Before declaring success, the final proof should be successful Repo2Run-style pytest collection "
                "from the repository root. Use the changed-file and framework metadata to diagnose collection "
                "errors, but you do not need to execute or pass those tests."
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
            thought = self._extract_thought(text)
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
            r"(?m)^\s*(?:Observation|Action|Thought|Verification Bundle|Final Answer):"
        )
        match = re.search(stop_pattern, text)
        if match:
            return text[:match.start()].strip()
        return text.strip()

    def _extract_thought(self, text):
        explicit_thought = self._extract_tag(text, "Thought")
        if explicit_thought:
            return explicit_thought
        return self._extract_think_block(text)

    def _extract_think_block(self, text):
        if not text:
            return None

        match = re.search(r"<think>\s*(.*?)\s*</think>", text, re.DOTALL | re.IGNORECASE)
        if not match:
            return None
        return self._clean_extracted_content(match.group(1))

    def _extract_tag(self, text, tag):
        labels = r"Thought|Action|Observation|Verification\ Bundle|Final\ Answer"
        pattern = rf"^\s*{re.escape(tag)}:\s*(.*?)(?=^\s*(?:{labels}):|\Z)"
        match = re.search(pattern, text, re.DOTALL | re.MULTILINE)
        if match:
            return self._clean_extracted_content(match.group(1))
        return None

    def _clean_extracted_content(self, content):
        content = (content or "").strip()
        if not content:
            return None

        content = re.sub(r"^```bash\n?", "", content)
        content = re.sub(r"^```\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        if content.startswith('`') and content.endswith('`'):
            content = content[1:-1].strip()
        return content.strip() or None

    def extract_final_answer(self, text):
        if not text:
            return None

        matches = list(
            re.finditer(
                r"^\s*Final Answer:\s*(Success|Failure)\b",
                text,
                re.IGNORECASE | re.MULTILINE,
            )
        )
        for match in reversed(matches):
            prefix = text[:match.start()].rstrip()
            if prefix and prefix[-1] in {'"', "'", "`"}:
                continue
            trailing = text[match.end():].strip()
            if trailing and not re.fullmatch(r"[.。`]*", trailing):
                continue
            return match.group(1).capitalize()
        return None
