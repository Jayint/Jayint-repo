import re


class Synthesizer:
    def __init__(self, base_image="python:3.10", workdir="/app"):
        self.base_image = base_image
        self.workdir = workdir
        self.instructions = []
        self.setup_commands = []  # 记录真实执行成功的安装/配置指令
        self.api_key_hints = []  # 记录检测到的 API Key 需求

    def record_success(self, command):
        """Records a successful bash command as a RUN instruction."""
        recordable_commands = self._extract_recordable_setup_commands(command)
        for recordable_command in recordable_commands:
            self._record_setup_instruction(recordable_command)

    def is_readonly_command(self, command):
        """Public wrapper used by the agent when tracking verification state."""
        return self._is_readonly_command(command)

    def command_mutates_environment(self, command):
        """Return True when a successful command changed the effective runtime environment."""
        return self._command_has_meaningful_setup_activity(command)

    def is_runtime_service_command(self, command):
        """Public wrapper for runtime service startup commands such as redis-server."""
        return self._command_matches_segment_predicate(command, self._is_runtime_service_segment)

    def is_runtime_healthcheck_command(self, command):
        """Public wrapper for healthcheck-only commands such as redis-cli ping."""
        return self._command_matches_segment_predicate(command, self._is_runtime_healthcheck_segment)

    def observation_has_effective_test_signal(self, observation):
        """Expose test-output validation for agent-reported wrapper commands."""
        return self._observation_has_effective_test_signal(observation)

    def observation_has_empty_test_run_signal(self, observation):
        """Expose empty test-run detection for agent-reported wrapper commands."""
        return self._observation_has_empty_test_run_signal(observation)

    def observation_looks_like_help_text(self, observation):
        """Expose help-text detection for agent-reported wrapper commands."""
        return self._observation_looks_like_help_text(observation)

    def is_persistent_setup_command(self, command):
        """Return True when a successful command would already be replayed via Dockerfile setup."""
        return bool(self._extract_recordable_setup_commands(command))

    def _record_setup_instruction(self, command):
        """Persist a setup/build command into Dockerfile and QuickStart collections."""
        if not command or not command.strip():
            return
        if self._is_readonly_command(command):
            return

        run_instruction = f"RUN {command}"
        if run_instruction in self.instructions:
            return

        self.instructions.append(run_instruction)
        if self._is_setup_command(command) and command not in self.setup_commands:
            self.setup_commands.append(command)

    def _extract_recordable_setup_commands(self, command):
        """Keep setup/build prefixes of successful commands while excluding the test invocation itself."""
        if not command or not command.strip():
            return []
        if self._is_readonly_command(command):
            return []

        if not self.is_test_command(command):
            recordable_command = self._extract_recordable_non_test_command(command)
            return [recordable_command] if recordable_command else []

        setup_prefix = self._extract_setup_prefix_before_test(command)
        return [setup_prefix] if setup_prefix else []
    
    def _is_readonly_command(self, command):
        """判断指令是否是只读/信息查询命令（不应加入 Dockerfile）"""
        readonly_first_words = ['ls', 'cat', 'echo', 'pwd', 'env', 'grep', 'find',
                                'head', 'tail', 'which', 'type', 'file', 'du', 'df',
                                'ps', 'top', 'hostname', 'whoami', 'date', 'id']
        first_word = command.strip().split()[0].lower() if command.strip() else ""
        return first_word in readonly_first_words
    
    def is_test_command(self, command):
        """判断指令是否是测试命令。"""
        if not command or not command.strip():
            return False

        # Read-only commands such as `echo "tests passed"` must never be treated as test runs.
        if self._is_readonly_command(command):
            return False

        test_patterns = [
            # Python
            r"^pytest\b",
            r"^py\.test\b",
            r"^python3?\s+-m\s+pytest\b",
            r"^python3?\s+-m\s+unittest\b",
            r"^tox\b",
            r"^nox\b",
            r"^nosetests\b",
            r"^nose\b",
            # JavaScript / TypeScript
            r"^(?:npm|yarn|pnpm)\s+test\b",
            r"^jest\b",
            r"^mocha\b",
            r"^karma\b",
            r"^vitest\b",
            r"^cypress\b",
            # Rust / Go / Java / Ruby / PHP
            r"^cargo\s+test\b",
            r"^go\s+test\b",
            r"^(?:mvn|\.?/mvnw)\s+test\b",
            r"^(?:gradle|\.?/gradlew)\s+test\b",
            r"^bundle\s+exec\s+rspec\b",
            r"^bundle\s+exec\s+rake\b",
            r"^rake\s+test\b",
            r"^rspec\b",
            r"^(?:\./)?(?:vendor/bin/)?phpunit\b",
            r"^(?:\./)?(?:vendor/bin/)?pest\b",
            # C / C++
            r"^ctest\b",
            r"^cmake\b.*\b--target\b\s*(?:test|tests)\b",
            r"^(?:make|gmake|mingw32-make|ninja)\b.*\b(?:test|tests|check|tdd)\b",
        ]

        for _, normalized in self._iter_command_segments(command):
            if self._segment_matches_test_pattern(normalized, test_patterns):
                return True

            if self._looks_like_test_executable(normalized):
                return True

        return False

    def analyze_test_run(self, command, observation=""):
        """Judge whether a successful command actually executed meaningful tests."""
        result = {
            "is_test_command": False,
            "is_effective_test_run": False,
            "confidence": "none",
            "reason": "not_test_command",
        }

        if not self.is_test_command(command):
            return result

        result["is_test_command"] = True

        if self._is_readonly_command(command):
            result["reason"] = "readonly_command"
            return result

        if self._observation_looks_like_help_text(observation):
            result["reason"] = "help_or_usage_output"
            return result

        if self._observation_has_effective_test_signal(observation):
            result["is_effective_test_run"] = True
            result["confidence"] = "high"
            result["reason"] = "observed_test_execution_signal"
            return result

        # Some runners (notably `go test ./...`) can mix real package results with
        # informational lines such as `[no test files]`. Treat explicit positive
        # execution signals as authoritative before falling back to empty-run hints.
        if self._observation_has_empty_test_run_signal(observation):
            result["reason"] = "no_tests_executed"
            return result

        if observation and any(
            self._looks_like_test_executable(normalized)
            for _, normalized in self._iter_command_segments(command)
        ):
            result["is_effective_test_run"] = True
            result["confidence"] = "medium"
            result["reason"] = "direct_test_executable_with_output"
            return result

        result["reason"] = "no_reliable_test_execution_signal"
        return result

    def _iter_command_segments(self, command):
        """Yield normalized shell command segments split on common separators."""
        for segment, _ in self._split_shell_chain(command):
            normalized = self._normalize_command_segment(segment)
            if normalized:
                yield segment.strip(), normalized

    def _command_matches_segment_predicate(self, command, predicate):
        if not command or not command.strip():
            return False

        for _, normalized in self._iter_command_segments(command):
            if predicate(normalized):
                return True
        return False

    def _split_shell_chain(self, command):
        """Split a shell command into ordered segments while preserving separators."""
        tokens = re.split(r"(\s*(?:&&|\|\||;|\n)\s*)", command)
        segments = []
        i = 0
        while i < len(tokens):
            raw_segment = tokens[i]
            separator = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2

            if raw_segment is None:
                continue
            if not raw_segment.strip():
                continue
            segments.append((raw_segment, separator))
        return segments

    def _normalize_command_segment(self, segment):
        normalized = segment.strip().lower()
        if not normalized:
            return ""

        normalized = re.sub(
            r"^(?:[a-z_][a-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S+)\s+)+",
            "",
            normalized,
        )
        normalized = re.sub(r"^time\s+", "", normalized)
        return normalized.strip()

    def _segment_matches_test_pattern(self, normalized_command, test_patterns):
        return any(re.match(pattern, normalized_command) for pattern in test_patterns)

    def _extract_recordable_non_test_command(self, command):
        """Drop runtime-only service startup/checks from setup commands while preserving useful context."""
        return self._extract_recordable_command_segments(command, stop_before_test=False)

    def _extract_setup_prefix_before_test(self, command):
        """Return the setup/build prefix that ran before the first test segment, if any."""
        return self._extract_recordable_command_segments(command, stop_before_test=True)

    def _extract_recordable_command_segments(self, command, stop_before_test):
        """Rebuild a command from recordable setup/build segments only."""
        kept_segments = []
        pending_navigation = []
        has_recordable_segment = False

        test_patterns = [
            r"^pytest\b",
            r"^py\.test\b",
            r"^python3?\s+-m\s+pytest\b",
            r"^python3?\s+-m\s+unittest\b",
            r"^tox\b",
            r"^nox\b",
            r"^nosetests\b",
            r"^nose\b",
            r"^(?:npm|yarn|pnpm)\s+test\b",
            r"^jest\b",
            r"^mocha\b",
            r"^karma\b",
            r"^vitest\b",
            r"^cypress\b",
            r"^cargo\s+test\b",
            r"^go\s+test\b",
            r"^(?:mvn|\.?/mvnw)\s+test\b",
            r"^(?:gradle|\.?/gradlew)\s+test\b",
            r"^bundle\s+exec\s+rspec\b",
            r"^bundle\s+exec\s+rake\b",
            r"^rake\s+test\b",
            r"^rspec\b",
            r"^(?:\./)?(?:vendor/bin/)?phpunit\b",
            r"^(?:\./)?(?:vendor/bin/)?pest\b",
            r"^ctest\b",
            r"^cmake\b.*\b--target\b\s*(?:test|tests)\b",
            r"^(?:make|gmake|mingw32-make|ninja)\b.*\b(?:test|tests|check|tdd)\b",
        ]

        for raw_segment, separator in self._split_shell_chain(command):
            normalized = self._normalize_command_segment(raw_segment)
            if not normalized:
                continue

            if stop_before_test and (
                self._segment_matches_test_pattern(normalized, test_patterns)
                or self._looks_like_test_executable(normalized)
            ):
                break

            if self._is_runtime_only_segment(normalized):
                continue

            if self._is_navigation_only_segment(normalized):
                pending_navigation.append((raw_segment.strip(), separator.strip() if separator else ""))
                continue

            if not self._segment_has_meaningful_setup_activity(normalized):
                pending_navigation = []
                continue

            has_recordable_segment = True
            if pending_navigation:
                kept_segments.extend(pending_navigation)
                pending_navigation = []
            kept_segments.append((raw_segment.strip(), separator.strip() if separator else ""))

        if not has_recordable_segment:
            return None

        rebuilt_parts = []
        for index, (segment, separator) in enumerate(kept_segments):
            rebuilt_parts.append(segment)
            if index < len(kept_segments) - 1:
                rebuilt_parts.append(separator or "&&")

        rebuilt_command = " ".join(part for part in rebuilt_parts if part).strip()
        rebuilt_command = re.sub(r"\s+", " ", rebuilt_command)
        rebuilt_command = re.sub(r"(?:&&|\|\||;)\s*$", "", rebuilt_command).strip()
        return rebuilt_command or None

    def _segment_has_meaningful_setup_activity(self, normalized_command):
        """Treat navigation-only prefixes as non-recordable, but keep real setup/build work."""
        if not normalized_command:
            return False

        if self._is_navigation_only_segment(normalized_command):
            return False
        if self._is_runtime_only_segment(normalized_command):
            return False
        return not self._is_readonly_command(normalized_command)

    def _is_navigation_only_segment(self, normalized_command):
        return normalized_command.startswith(("cd ", "pushd ", "popd"))

    def _is_runtime_only_segment(self, normalized_command):
        return (
            self._is_runtime_service_segment(normalized_command)
            or self._is_runtime_healthcheck_segment(normalized_command)
        )

    def _is_runtime_service_segment(self, normalized_command):
        service_patterns = (
            r"^service\s+\S+\s+(?:start|restart|reload|stop)\b",
            r"^redis-server\b",
            r"^rabbitmq-server\b.*\b-detached\b",
            r"^memcached\b.*\b-d\b",
            r"^mongod\b.*\b--fork\b",
            r"^apache2ctl\s+start\b",
            r"^nginx\b(?:\s|$)",
        )
        return any(re.search(pattern, normalized_command) for pattern in service_patterns)

    def _is_runtime_healthcheck_segment(self, normalized_command):
        healthcheck_patterns = (
            r"^redis-cli\s+ping\b",
            r"^pg_isready\b",
            r"^mysqladmin\s+ping\b",
            r"^rabbitmq-diagnostics\s+ping\b",
            r"^curl\b.*\b127\.0\.0\.1\b",
            r"^curl\b.*\blocalhost\b",
            r"^wget\b.*\b127\.0\.0\.1\b",
            r"^wget\b.*\blocalhost\b",
        )
        return any(re.search(pattern, normalized_command) for pattern in healthcheck_patterns)

    def _command_has_meaningful_setup_activity(self, command):
        """Detect whether a successful shell command materially changed the runtime environment."""
        if not command or not command.strip():
            return False
        if self._is_readonly_command(command):
            return False

        for _, normalized in self._iter_command_segments(command):
            if not normalized:
                continue

            if self._is_readonly_command(normalized):
                continue

            if self._is_runtime_service_segment(normalized):
                return True

            if self._is_setup_command(normalized):
                return True

            if self._segment_has_meaningful_setup_activity(normalized) and normalized.startswith(
                (
                    "./configure",
                    "configure ",
                    "meson ",
                    "mkdir ",
                    "rm ",
                    "cp ",
                    "mv ",
                    "ln ",
                    "chmod ",
                    "chown ",
                    "sed ",
                    "patch ",
                    "git apply",
                    "git checkout",
                    "python setup.py",
                )
            ):
                return True

        return False

    def _looks_like_test_executable(self, normalized_command):
        """Detect direct execution of built test binaries such as ./FooTests."""
        if not normalized_command:
            return False

        executable = normalized_command.split()[0]
        if not (executable.startswith("./") or executable.startswith("/") or "/" in executable):
            return False

        basename = executable.rsplit("/", 1)[-1]
        if basename in {"configure", "install-sh", "test-driver"}:
            return False

        test_suffixes = (
            "test",
            "tests",
            "unittest",
            "unittests",
            "spec",
            "specs",
            "test.exe",
            "tests.exe",
            "spec.exe",
            "specs.exe",
        )
        if basename.endswith(test_suffixes):
            return True

        return bool(
            re.search(r"(test|tests|unittest|unittests|spec|specs)", basename)
            and ("/test" in executable or "/tests" in executable or executable.startswith("./"))
        )

    def _observation_has_empty_test_run_signal(self, observation):
        """Detect successful commands that clearly did not run any tests."""
        if not observation:
            return False

        normalized = self._normalize_observation_text(observation).lower()
        empty_run_patterns = [
            r"no tests were found",
            r"no tests found",
            r"collected\s+0\s+items",
            r"ran\s+0\s+tests?",
            r"\b0\s+tests?\s+ran\b",
            r"\[no test files\]",
            r"no test cases matched",
            r"no tests to run",
            r"\b0\s+examples?,\s+0\s+failures?\b",
        ]
        return any(re.search(pattern, normalized, re.MULTILINE) for pattern in empty_run_patterns)

    def _observation_has_effective_test_signal(self, observation):
        """Detect observation text that strongly suggests real tests were executed."""
        if not observation:
            return False

        normalized_observation = self._normalize_observation_text(observation)
        positive_patterns = [
            r"collected\s+[1-9]\d*\s+items",
            r"ran\s+[1-9]\d*\s+tests?",
            r"\b[1-9]\d*\s+passed\b",
            r"\b[1-9]\d*\s+failed\b",
            r"\b[1-9]\d*\s+skipped\b",
            r"\bok\s+\([1-9]\d*\s+tests?,",
            r"\b[1-9]\d*\s+tests?,\s+[1-9]\d*\s+ran\b",
            r"\[=+\]\s+running\s+[1-9]\d*\s+tests?",
            r"test result:\s+(?:ok|failed)\.",
            r"\b[1-9]\d*%\s+tests\s+passed\b",
            r"^\s*ok\s+\S+\s+\d+(?:\.\d+)?s(?:\s|$)",
            r"\b[1-9]\d*\s+examples?,\s+\d+\s+failures?\b",
            r"\b[1-9]\d*\s+checks?,\s+\d+\s+ignored\b",
            r"start\s+\d+:",
            r"suites:\s+\d+\s+of\s+[1-9]\d*\s+completed",
            r"asserts:\s+\d+\s+of\s+[1-9]\d*",
            r"^\s*#\s*subtest:",
            r"^\s*not ok\b",
        ]
        return any(
            re.search(pattern, normalized_observation, re.IGNORECASE | re.MULTILINE)
            for pattern in positive_patterns
        )

    def _observation_looks_like_help_text(self, observation):
        """Exclude `--help` or usage screens from being treated as test execution."""
        if not observation:
            return False

        normalized = self._normalize_observation_text(observation).lower()
        help_markers = [
            "usage:",
            "optional arguments:",
            "positional arguments:",
            "show this help",
        ]
        return any(marker in normalized for marker in help_markers)

    def _normalize_observation_text(self, observation):
        """Strip ANSI control codes and zero-width formatting artifacts before pattern matching."""
        normalized = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", observation)
        normalized = normalized.replace("\u200b", "")
        normalized = normalized.replace("\ufeff", "")
        return normalized
    
    def record_api_key_hint(self, key_name, detection_context=""):
        """记录检测到的 API Key 需求"""
        hint = {"key_name": key_name, "context": detection_context}
        if hint not in self.api_key_hints:
            self.api_key_hints.append(hint)
    
    def _is_setup_command(self, command):
        """判断指令是否是环境配置相关的（用于 QuickStart）"""
        setup_keywords = [
            # Python
            'pip install', 'pip3 install', 'poetry install', 'uv pip', 'uv install',
            'conda install', 'pipenv install',
            # JavaScript/TypeScript
            'npm install', 'npm i ', 'yarn add', 'yarn install', 'pnpm install',
            # Rust
            'cargo build', 'cargo install',
            # Go
            'go mod download', 'go get ', 'go install',
            # Java
            'mvn install', 'mvn dependency:resolve', 'gradle build', 'gradlew build',
            # Ruby
            'bundle install', 'gem install',
            # PHP
            'composer install', 'composer require',
            # C/C++
            'make', 'cmake', 'ninja',
            # Dart
            'flutter pub get', 'dart pub get',
            # General
            'git clone', 'wget', 'curl', 'apt install', 'apt-get install', 'yum install',
        ]
        return any(keyword in command.lower() for keyword in setup_keywords)

    def generate_quickstart_with_llm(self, workplace_path, client, model="gpt-4o", file_name="QuickStart.md"):
        """使用 LLM 基于 README.md 和真实安装步骤生成简洁的 QuickStart 文档"""
        import os
        
        if not self.setup_commands:
            print("[Warning] No setup commands recorded. QuickStart.md will not be generated.")
            return
        
        # 过滤掉纯查看类指令，只保留安装配置指令
        relevant_commands = [cmd for cmd in self.setup_commands if self._is_relevant_for_quickstart(cmd)]
        
        if not relevant_commands:
            print("[Warning] No relevant setup commands found.")
            return
        
        # 读取 README.md
        readme_path = os.path.join(workplace_path, "README.md")
        readme_content = ""
        if os.path.exists(readme_path):
            try:
                with open(readme_path, "r", encoding="utf-8", errors="ignore") as f:
                    readme_content = f.read()
            except OSError:
                readme_content = "(README.md not found or unreadable)"
        else:
            readme_content = "(README.md not found)"
        
        # 构造 Prompt
        prompt = f"""
You are a technical documentation assistant. Your task is to create a concise QuickStart.md file.

**Input Information:**

1. **Successfully Executed Setup Commands** (these were verified to work in the Docker environment):
```bash
{chr(10).join(relevant_commands)}
```

2. **Original README.md Content** (extract startup/run commands from here):
```
{readme_content[:3000]}  # 限制长度避免 token 溢出
```

**Your Task:**
Generate a **concise** QuickStart.md with the following structure:

1. **## Setup Steps**: List the setup commands above as executable bash code blocks (Step 1, Step 2, etc.).
2. **## How to Run**: Extract the actual startup/run commands from the README. These should be:
   - Commands that START or RUN the application (NOT installation commands)
   - Verification commands like `--version` or `--help`
   - Example usage commands
   - DO NOT include any installation commands (pip install, apt install, yay, nix, etc.)
3. **## API Key Configuration** (ONLY if the project requires API keys based on README analysis):
   - Detect if the project needs API keys (look for keywords: OPENAI_API_KEY, API_KEY, TOKEN, etc. in README)
   - If API keys are needed, provide TWO methods:
     a) **Method 1: Environment Variables** - Show how to export keys in shell
     b) **Method 2: .env File** - Show how to create .env file with required keys
   - Adapt the variable names based on what the README actually uses
4. **## Notes**: Brief notes about other secrets or configurations if needed.

**Requirements:**
- Be extremely concise
- Only include executable bash commands in code blocks
- Do NOT include terminal output, logs, or help menus
- Each command should be on its own line in the code block
- For API keys section, use actual variable names from the README (e.g., if README mentions ANTHROPIC_API_KEY, use that)

Generate the complete QuickStart.md content now:
"""
        
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            
            quickstart_content = response.choices[0].message.content.strip()
            
            # 写入文件
            output_path = os.path.join(workplace_path, file_name)
            with open(output_path, "w") as f:
                f.write(quickstart_content)
            
            print(f"QuickStart.md successfully generated at {output_path}")
            return quickstart_content
            
        except Exception as e:
            print(f"[Error] Failed to generate QuickStart.md with LLM: {e}")
            return None
    
    def _is_relevant_for_quickstart(self, command):
        """判断指令是否应该出现在 QuickStart 文档中"""
        # 过滤掉纯信息查询指令
        irrelevant_keywords = ['ls', 'cat', 'echo', 'pwd', 'env', 'grep', 'find', 'head', 'tail']
        first_word = command.strip().split()[0].lower()
        return first_word not in irrelevant_keywords
    
    def generate_dockerfile(self, file_path="Dockerfile"):
        """Generates the final Dockerfile."""
        content = [
            f"FROM {self.base_image}",
            f"WORKDIR {self.workdir}",
            ""
        ]
        content.extend(self.instructions)
        
        with open(file_path, "w") as f:
            f.write("\n".join(content))
        
        print(f"Dockerfile successfully generated at {file_path}")
        return "\n".join(content)
