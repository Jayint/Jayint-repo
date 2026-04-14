import os
import re


DEFAULT_APT_RETRIES = 5
DEFAULT_APT_HTTP_TIMEOUT_SECONDS = 120
DEFAULT_APT_HTTPS_TIMEOUT_SECONDS = 120


def _quote_shell_single(text):
    return "'" + text.replace("'", "'\"'\"'") + "'"


def resolve_apt_mirror_url(apt_mirror_url=None):
    mirror = apt_mirror_url or os.environ.get("JAYINT_APT_MIRROR_URL") or os.environ.get("APT_MIRROR_URL")
    if not mirror:
        return None
    return mirror.rstrip("/")


def build_dockerfile_apt_bootstrap_run_instructions(
    apt_mirror_url=None,
    apt_retries=DEFAULT_APT_RETRIES,
    apt_http_timeout_seconds=DEFAULT_APT_HTTP_TIMEOUT_SECONDS,
    apt_https_timeout_seconds=DEFAULT_APT_HTTPS_TIMEOUT_SECONDS,
):
    mirror = resolve_apt_mirror_url(apt_mirror_url)
    instructions = [
        (
            "RUN printf '%s\\n' "
            f"'Acquire::Retries \"{apt_retries}\";' "
            f"'Acquire::http::Timeout \"{apt_http_timeout_seconds}\";' "
            f"'Acquire::https::Timeout \"{apt_https_timeout_seconds}\";' "
            "'Acquire::http::Pipeline-Depth \"0\";' "
            "> /etc/apt/apt.conf.d/99jayint-retries"
        )
    ]

    if mirror:
        quoted_mirror = _quote_shell_single(mirror)
        instructions.append(
            "RUN APT_MIRROR_URL="
            f"{quoted_mirror} && "
            "if [ -f /etc/apt/sources.list ]; then "
            "sed -i "
            "\"s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|https://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|http://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|https://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g\" "
            "/etc/apt/sources.list; "
            "fi && "
            "find /etc/apt/sources.list.d -maxdepth 1 -name '*.list' "
            "-exec sed -i "
            "\"s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|https://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|http://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|https://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g\" {} + "
            "2>/dev/null || true && "
            "if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then "
            "sed -i "
            "\"s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|https://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|http://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g; "
            "s|https://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g\" "
            "/etc/apt/sources.list.d/ubuntu.sources; "
            "fi && "
            "apt-get update"
        )

    return instructions


def is_generated_apt_bootstrap_run_instruction(instruction):
    if not instruction:
        return False
    normalized = " ".join(instruction.split())
    if "99jayint-retries" in normalized:
        return True
    return "APT_MIRROR_URL=" in normalized and "archive.ubuntu.com/ubuntu" in normalized


class Synthesizer:
    SAFE_READONLY_COMMANDS = {
        "cd", "ls", "cat", "echo", "pwd", "whoami", "who", "date", "cal", "df", "du",
        "free", "uname", "uptime", "w", "ps", "pgrep", "top", "dmesg", "tail", "head",
        "grep", "find", "locate", "which", "file", "stat", "cmp", "diff", "xz", "unxz",
        "sort", "wc", "tr", "cut", "paste", "tee", "awk", "env", "printenv", "hostname",
        "xargs",
        "ping", "traceroute", "ssh",
    }
    VERSION_PROBE_COMMANDS = {
        "php", "composer",
        "python", "python3", "pip", "pip3",
        "node", "npm", "yarn", "pnpm",
        "java", "javac", "mvn", "gradle", "gradlew",
        "go", "rustc", "cargo",
        "ruby", "gem", "bundle",
        "gcc", "g++", "cc", "c++", "make", "cmake", "ninja",
        "git",
    }

    def __init__(self, base_image="python:3.10", workdir="/app"):
        self.base_image = base_image
        self.workdir = workdir
        self.instructions = []

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
        """Persist a setup/build command into the generated Dockerfile."""
        if not command or not command.strip():
            return
        if self._is_readonly_command(command):
            return

        run_instruction = f"RUN {command}"
        if run_instruction in self.instructions:
            return

        self.instructions.append(run_instruction)

    def _extract_recordable_setup_commands(self, command):
        """Keep setup/build prefixes of successful commands while excluding the test invocation itself."""
        if not command or not command.strip():
            return []
        if self._looks_like_ephemeral_workspace_repair(command):
            return []
        if self._is_readonly_command(command):
            return []

        if not self.is_test_command(command):
            recordable_command = self._extract_recordable_non_test_command(command)
            return [recordable_command] if recordable_command else []

        setup_prefix = self._extract_setup_prefix_before_test(command)
        return [setup_prefix] if setup_prefix else []

    def _looks_like_ephemeral_workspace_repair(self, command):
        """Skip ad-hoc artifact repair commands that only work because the workspace is already dirty."""
        normalized_command = command.strip().lower()
        if not normalized_command:
            return False

        numbered_duplicate_repair = re.search(
            r"(?:^|(?:&&|\|\||;)\s*)(?:mv|cp)\s+(['\"]?)([^'\"\s]+?)\.(\d+)\1\s+(['\"]?)\2\4(?:\s|$)",
            normalized_command,
        )
        if not numbered_duplicate_repair:
            return False

        return any(
            marker in normalized_command
            for marker in (
                "tar -x",
                "unzip ",
                "gunzip ",
                "ln -s ",
                "ln -sf ",
                "/opt/",
            )
        )
    
    def _is_readonly_command(self, command):
        """Treat safe inspection/search commands as read-only when they do not redirect output."""
        if not command or not command.strip():
            return False
        if self._has_output_redirection(self._strip_dev_null_redirections(command)):
            return False

        saw_component = False
        for raw_segment, _ in self._split_shell_chain(command):
            for component in self._split_pipeline(raw_segment):
                normalized = self._normalize_command_segment(component)
                if not normalized:
                    continue
                if not self._pipeline_component_is_safe_readonly(normalized):
                    return False
                saw_component = True
        return saw_component
    
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
        """Split a shell command into ordered segments while respecting quotes/escapes."""
        segments = []
        current = []
        in_single = False
        in_double = False
        escape = False
        index = 0

        while index < len(command):
            char = command[index]

            if escape:
                current.append(char)
                escape = False
                index += 1
                continue

            if char == "\\":
                current.append(char)
                escape = True
                index += 1
                continue

            if char == "'" and not in_double:
                in_single = not in_single
                current.append(char)
                index += 1
                continue

            if char == '"' and not in_single:
                in_double = not in_double
                current.append(char)
                index += 1
                continue

            if not in_single and not in_double:
                if command.startswith("&&", index) or command.startswith("||", index):
                    raw_segment = "".join(current).strip()
                    separator = command[index:index + 2]
                    if raw_segment:
                        segments.append((raw_segment, separator))
                    current = []
                    index += 2
                    continue

                if char in {";", "\n"}:
                    raw_segment = "".join(current).strip()
                    if raw_segment:
                        segments.append((raw_segment, char))
                    current = []
                    index += 1
                    continue

            current.append(char)
            index += 1

        raw_segment = "".join(current).strip()
        if raw_segment:
            segments.append((raw_segment, ""))
        return segments

    def _split_pipeline(self, segment):
        """Split a shell segment on single-pipe operators while respecting quotes/escapes."""
        components = []
        current = []
        in_single = False
        in_double = False
        escape = False
        index = 0

        while index < len(segment):
            char = segment[index]

            if escape:
                current.append(char)
                escape = False
                index += 1
                continue

            if char == "\\":
                current.append(char)
                escape = True
                index += 1
                continue

            if char == "'" and not in_double:
                in_single = not in_single
                current.append(char)
                index += 1
                continue

            if char == '"' and not in_single:
                in_double = not in_double
                current.append(char)
                index += 1
                continue

            if (
                not in_single
                and not in_double
                and char == "|"
                and not segment.startswith("||", index)
            ):
                component = "".join(current).strip()
                if component:
                    components.append(component)
                current = []
                index += 1
                continue

            current.append(char)
            index += 1

        component = "".join(current).strip()
        if component:
            components.append(component)
        return components

    def _has_output_redirection(self, command):
        in_single = False
        in_double = False
        escape = False

        for char in command:
            if escape:
                escape = False
                continue

            if char == "\\":
                escape = True
                continue

            if char == "'" and not in_double:
                in_single = not in_single
                continue

            if char == '"' and not in_single:
                in_double = not in_double
                continue

            if not in_single and not in_double and char == ">":
                return True

        return False

    def _pipeline_component_is_safe_readonly(self, normalized_command):
        if self._is_version_probe_segment(normalized_command):
            return True

        executable = normalized_command.split()[0]
        executable = executable.strip("\"'`")
        executable_name = executable.rsplit("/", 1)[-1]
        return executable_name in self.SAFE_READONLY_COMMANDS

    def _strip_dev_null_redirections(self, command):
        """Ignore harmless output silencing when classifying read-only probes."""
        if not command:
            return ""

        stripped = re.sub(r"\s+(?:[12]?>|&>)\s*/dev/null\b", "", command)
        stripped = re.sub(r"\s+(?:[12]?>|&>)/dev/null\b", "", stripped)
        return stripped

    def _is_version_probe_segment(self, normalized_command):
        normalized = self._strip_dev_null_redirections(normalized_command).strip()
        if not normalized:
            return False

        parts = normalized.split()
        if len(parts) != 2:
            return False

        executable = parts[0].strip("\"'`")
        executable_name = executable.rsplit("/", 1)[-1]
        if executable_name not in self.VERSION_PROBE_COMMANDS:
            return False

        return parts[1] in {"--version", "-v", "version"}

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
        if self._is_version_probe_segment(normalized_command):
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
            r"tests\s+run:\s*[1-9]\d*,\s*failures:\s*\d+,\s*errors:\s*\d+,\s*skipped:\s*\d+",
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
    
    def _is_setup_command(self, command):
        """判断指令是否是环境配置相关的 setup/build 命令"""
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
    
    def generate_dockerfile(self, file_path="Dockerfile"):
        """Generates the final Dockerfile."""
        apt_bootstrap_instructions = build_dockerfile_apt_bootstrap_run_instructions()
        content = [
            f"FROM {self.base_image}",
            f"WORKDIR {self.workdir}",
            ""
        ]
        if apt_bootstrap_instructions:
            content.extend(apt_bootstrap_instructions)
            content.append("")
        content.extend(self.instructions)
        
        with open(file_path, "w") as f:
            f.write("\n".join(content))
        
        print(f"Dockerfile successfully generated at {file_path}")
        return "\n".join(content)
