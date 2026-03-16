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
        # 跳过测试命令，避免在 Dockerfile 构建时运行测试
        if self.is_test_command(command):
            return
        
        # 跳过只读/信息查询命令，不影响环境
        if self._is_readonly_command(command):
            return
        
        # 去重：跳过已记录的相同命令
        run_instruction = f"RUN {command}"
        if run_instruction in self.instructions:
            return
        
        self.instructions.append(run_instruction)
        
        # 同时记录用于 QuickStart 的原始指令（也要去重）
        if self._is_setup_command(command) and command not in self.setup_commands:
            self.setup_commands.append(command)
    
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
            r"^(?:vendor/bin/)?phpunit\b",
            r"^(?:vendor/bin/)?pest\b",
            # C / C++
            r"^ctest\b",
            r"^cmake\b.*\b--target\b\s*(?:test|tests)\b",
            r"^(?:make|gmake|mingw32-make|ninja)\b.*\b(?:test|tests|check|tdd)\b",
        ]

        for normalized in self._iter_command_segments(command):
            if any(re.match(pattern, normalized) for pattern in test_patterns):
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

        if self._observation_has_empty_test_run_signal(observation):
            result["reason"] = "no_tests_executed"
            return result

        if self._observation_has_effective_test_signal(observation):
            result["is_effective_test_run"] = True
            result["confidence"] = "high"
            result["reason"] = "observed_test_execution_signal"
            return result

        if observation and any(self._looks_like_test_executable(seg) for seg in self._iter_command_segments(command)):
            result["is_effective_test_run"] = True
            result["confidence"] = "medium"
            result["reason"] = "direct_test_executable_with_output"
            return result

        result["reason"] = "no_reliable_test_execution_signal"
        return result

    def _iter_command_segments(self, command):
        """Yield normalized shell command segments split on common separators."""
        for segment in re.split(r"(?:&&|\|\||;|\n)+", command):
            normalized = segment.strip().lower()
            if not normalized:
                continue

            # Strip leading environment assignments and `time` prefixes.
            normalized = re.sub(
                r"^(?:[a-z_][a-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S+)\s+)+",
                "",
                normalized,
            )
            normalized = re.sub(r"^time\s+", "", normalized)
            yield normalized

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

        normalized = observation.lower()
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
        ]
        return any(re.search(pattern, observation, re.IGNORECASE | re.MULTILINE) for pattern in positive_patterns)

    def _observation_looks_like_help_text(self, observation):
        """Exclude `--help` or usage screens from being treated as test execution."""
        if not observation:
            return False

        normalized = observation.lower()
        help_markers = [
            "usage:",
            "optional arguments:",
            "positional arguments:",
            "show this help",
        ]
        return any(marker in normalized for marker in help_markers)
    
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
            except:
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
