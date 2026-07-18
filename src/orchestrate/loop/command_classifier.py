"""Shell-command classifier (spec §4C, was synthesizer.py's Synthesizer).

Pure, stateless shell-command classification for the run loop: split shell chains
and pipelines, normalize segments, and decide what a command IS — a test run, a
runtime service, a persistent setup mutation, a read-only probe, a navigation-only
no-op. Consumed by sandbox.py's ``_command_classifier`` and mirrored by
``src/run_oracle.py`` (guarded by tests/test_run_oracle_parity.py).

Pruned (3b-7) from the 4,080-line Synthesizer: the Dockerfile/recipe-synthesis body
(module builders + 141 recipe methods + the base_image/workdir state) is dead — its
only live surface was these classifier predicates. Renamed Synthesizer ->
CommandClassifier; no instance state, no-arg construction.
"""

import re
import shlex


class CommandClassifier:
    """Stateless shell-command classifier (no instance state; construct with no args)."""

    TEST_COMMAND_PATTERNS = [
        # Python
        r"^pytest\b",
        r"^py\.test\b",
        r"^python3?\s+-m\s+pytest\b",
        r"^(?:poetry|pdm|uv)\s+run\s+pytest\b",
        r"^(?:poetry|pdm|uv)\s+run\s+python3?\s+-m\s+pytest\b",
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

    def _normalize_shell_separator(self, separator):
        if separator == "\n":
            return "\n"
        return separator.strip() if separator else ""

    def _command_rewrites_files(self, command):
        normalized = command or ""
        patterns = (
            r"\bsed\b[^\n;&|]*\s-i(?:\b|['\"]|[A-Za-z])",
            r"\bperl\b[^\n;&|]*-[A-Za-z]*i[A-Za-z]*\b",
            r"\b(?:python|python2|python3)\b.*\b(?:write_text|fileinput|open\s*\()",
            r"\b(?:chmod|touch|cp|mv|rm)\b",
            r"\bcat\b[^;&|]*>",
            r">>",
        )
        return any(re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)

    def command_mutates_environment(self, command):
        """Return True when a successful command changed the effective runtime environment."""
        return self._command_has_meaningful_setup_activity(command)

    def is_runtime_service_command(self, command):
        """Public wrapper for runtime service startup commands such as redis-server."""
        return self._command_matches_segment_predicate(command, self._is_runtime_service_segment)

    def is_truncated_test_output_command(self, command):
        """Return True when a test command pipes output through a lossy filter."""
        if not command or not command.strip():
            return False

        for raw_segment, _ in self._split_shell_chain(command):
            pipeline_components = self._split_pipeline(raw_segment)
            if len(pipeline_components) < 2:
                continue

            saw_test_component = False
            for component in pipeline_components:
                normalized = self._normalize_command_segment(component)
                if not normalized:
                    continue

                if self._is_test_like_segment(normalized):
                    saw_test_component = True
                    continue

                if saw_test_component and self._is_output_truncation_component(normalized):
                    return True

        return False

    def observation_has_test_failure_signal(self, observation):
        """Expose failing test-output detection for final verification guards."""
        return self._observation_has_test_failure_signal(observation)

    def is_persistent_setup_command(self, command):
        """Return True when a successful command would already be replayed via Dockerfile setup."""
        return bool(self._extract_recordable_setup_commands(command))

    def _extract_recordable_setup_commands(self, command):
        """Keep setup/build prefixes of successful commands while excluding the test invocation itself."""
        if not command or not command.strip():
            return []
        if self._looks_like_ephemeral_workspace_repair(command):
            return []
        if self._is_readonly_command(command):
            return []

        if not self.is_test_command(command):
            recordable_command = self._extract_recordable_command_segments(
                command,
                stop_before_test=False,
            )
            return [recordable_command] if recordable_command else []

        setup_prefix = self._extract_recordable_command_segments(
            command,
            stop_before_test=True,
        )
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

        for _, normalized in self._iter_command_segments(command):
            if self._is_test_like_segment(normalized):
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

        if self._observation_looks_like_help_text(observation):
            result["reason"] = "help_or_usage_output"
            return result

        if self.is_truncated_test_output_command(command):
            result["reason"] = "truncated_test_output"
            return result

        if self._observation_has_test_failure_signal(observation):
            result["reason"] = "test_failure_signal"
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
        """Split a shell command into ordered segments while respecting quotes, escapes, and heredocs."""
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
                    if char == "\n":
                        heredoc_descriptor = self._extract_heredoc_descriptor("".join(current))
                        if heredoc_descriptor is not None:
                            delimiter, strip_tabs = heredoc_descriptor
                            current.append(char)
                            index += 1
                            index = self._consume_heredoc_body(
                                command,
                                index,
                                current,
                                delimiter,
                                strip_tabs=strip_tabs,
                            )
                            raw_segment = "".join(current).strip()
                            if raw_segment:
                                separator = "\n" if index < len(command) else ""
                                segments.append((raw_segment, separator))
                            current = []
                            continue

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

    def _extract_heredoc_descriptor(self, segment):
        match = re.search(
            r"<<(?P<strip>-)?\s*(?P<quote>['\"]?)(?P<delimiter>[^\s'\"`]+)(?P=quote)\s*$",
            segment or "",
        )
        if not match:
            return None
        return match.group("delimiter"), bool(match.group("strip"))

    def _consume_heredoc_body(self, command, index, current, delimiter, strip_tabs=False):
        while index < len(command):
            next_newline = command.find("\n", index)
            if next_newline == -1:
                line = command[index:]
                current.append(line)
                return len(command)

            line = command[index:next_newline]
            current.append(line)
            current.append("\n")
            index = next_newline + 1

            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate == delimiter:
                return index

        return index

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
        if self._is_package_manager_inspection_segment(normalized_command):
            return True
        if self._is_python_readonly_probe_segment(normalized_command):
            return True

        executable = normalized_command.split()[0]
        executable = executable.strip("\"'`")
        executable_name = executable.rsplit("/", 1)[-1]
        return executable_name in self.SAFE_READONLY_COMMANDS

    def _is_package_manager_inspection_segment(self, normalized_command):
        normalized = self._strip_dev_null_redirections(normalized_command).strip()
        patterns = (
            r"^(?:python3?|/[\w./-]*python3?)\s+-m\s+pip\s+(?:list|show|freeze|check|index\s+versions)\b",
            r"^(?:pip3?|/[\w./-]*pip3?)\s+(?:list|show|freeze|check|index\s+versions)\b",
            r"^uv\s+pip\s+(?:list|show|freeze|check)\b",
            r"^(?:poetry|pdm)\s+(?:show|list)\b",
            r"^(?:npm|yarn|pnpm)\s+(?:list|ls|why)\b",
            r"^conda\s+(?:list|info)\b",
        )
        return any(re.match(pattern, normalized) for pattern in patterns)

    def _is_python_readonly_probe_segment(self, normalized_command):
        try:
            parts = shlex.split(normalized_command)
        except ValueError:
            return False
        if len(parts) < 3:
            return False

        executable = parts[0].strip("\"'`")
        executable_name = executable.rsplit("/", 1)[-1]
        if executable_name not in {"python", "python3"}:
            return False
        if "-c" not in parts:
            return False

        code_index = parts.index("-c") + 1
        if code_index >= len(parts):
            return False
        code = parts[code_index].strip()
        if not code:
            return False

        dangerous_markers = (
            "=",
            "open(",
            ".write",
            "write_text(",
            "touch(",
            "mkdir(",
            "unlink(",
            "remove(",
            "rmdir(",
            "rename(",
            "replace(",
            "chmod(",
            "chown(",
            "shutil",
            "subprocess",
            "os.system",
            "os.popen",
            "pip ",
            "pip.",
            "install",
            "exec(",
            "eval(",
            "__import__",
            "importlib",
        )
        lowered_code = code.lower()
        if any(marker in lowered_code for marker in dangerous_markers):
            return False

        statements = [part.strip() for part in re.split(r"[;\n]", code) if part.strip()]
        if not statements:
            return False

        imported_names = set()
        for statement in statements:
            if statement.startswith("import "):
                imported_names.update(self._extract_imported_python_names(statement))
            elif statement.startswith("from "):
                imported_names.update(self._extract_from_imported_python_names(statement))

        safe_call_pattern = re.compile(
            r"^(?:print|dir|getattr|hasattr|len|repr|str|type|bool)\s*\(",
            re.IGNORECASE,
        )
        for statement in statements:
            if statement.startswith(("import ", "from ")):
                continue
            if safe_call_pattern.match(statement):
                if not imported_names:
                    return False
                if not any(re.search(rf"\b{re.escape(name)}\b", statement) for name in imported_names):
                    return False
                continue
            return False
        return True

    def _extract_imported_python_names(self, statement):
        imported = statement[len("import "):]
        names = set()
        for item in imported.split(","):
            item = item.strip()
            if not item:
                continue
            if " as " in item:
                name = item.rsplit(" as ", 1)[-1].strip()
            else:
                name = item.split(".", 1)[0].strip()
            if name and re.match(r"^[a-z_][a-z0-9_]*$", name):
                names.add(name)
        return names

    def _extract_from_imported_python_names(self, statement):
        if " import " not in statement:
            return set()
        imported = statement.split(" import ", 1)[1]
        names = set()
        for item in imported.split(","):
            item = item.strip()
            if not item or item == "*":
                continue
            if " as " in item:
                name = item.rsplit(" as ", 1)[-1].strip()
            else:
                name = item.split(".", 1)[0].strip()
            if name and re.match(r"^[a-z_][a-z0-9_]*$", name):
                names.add(name)
        return names

    def _strip_dev_null_redirections(self, command):
        """Ignore harmless output silencing when classifying read-only probes."""
        if not command:
            return ""

        stripped = re.sub(r"\s+(?:[12]?>|&>)\s*/dev/null\b", "", command)
        stripped = re.sub(r"\s+(?:[12]?>|&>)/dev/null\b", "", stripped)
        return stripped

    def _strip_trailing_redirections(self, command):
        stripped = (command or "").strip()
        while True:
            updated = re.sub(r"\s+\d?>&\d\s*$", "", stripped).strip()
            updated = re.sub(r"\s+(?:[12]?>|&>)\s*[^\s]+$", "", updated).strip()
            if updated == stripped:
                return stripped
            stripped = updated

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

    def _is_test_like_segment(self, normalized_command):
        return (
            self._segment_matches_test_pattern(normalized_command, self.TEST_COMMAND_PATTERNS)
            or self._looks_like_test_executable(normalized_command)
        )

    def _is_output_truncation_component(self, normalized_command):
        if not normalized_command:
            return False

        executable = normalized_command.split()[0]
        executable = executable.strip("\"'`")
        executable_name = executable.rsplit("/", 1)[-1]
        return executable_name in {"head", "tail", "grep", "egrep", "fgrep"}

    def _extract_recordable_command_segments(self, command, stop_before_test):
        """Rebuild a command from recordable setup/build segments only."""
        kept_segments = []
        pending_navigation = []
        has_recordable_segment = False

        for raw_segment, separator in self._split_shell_chain(command):
            normalized = self._normalize_command_segment(raw_segment)
            if not normalized:
                continue

            if stop_before_test and self._is_test_like_segment(normalized):
                break

            if self._is_runtime_only_segment(normalized):
                continue

            if self._is_navigation_only_segment(normalized):
                pending_navigation.append((raw_segment.strip(), self._normalize_shell_separator(separator)))
                continue

            if not self._segment_has_meaningful_setup_activity(normalized):
                pending_navigation = []
                continue

            has_recordable_segment = True
            if pending_navigation:
                kept_segments.extend(pending_navigation)
                pending_navigation = []
            kept_segments.append((raw_segment.strip(), self._normalize_shell_separator(separator)))

        if not has_recordable_segment:
            return None

        rebuilt_command = kept_segments[0][0]
        for index in range(1, len(kept_segments)):
            separator = kept_segments[index - 1][1] or "&&"
            segment = kept_segments[index][0]
            if separator == "\n":
                rebuilt_command = f"{rebuilt_command}\n{segment}"
            else:
                rebuilt_command = f"{rebuilt_command} {separator} {segment}"

        if "\n" not in rebuilt_command and not self._command_rewrites_files(rebuilt_command):
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
            # In-image Postgres provisioning runs at RUNTIME (in the eval wrapper),
            # not baked into the image (a RUN-layer daemon dies before CMD). Match
            # the bare form and the as-postgres-user wrapped forms (runuser/su).
            r"(?:^|--\s+|\bpostgres\s+-c\s+\"?)pg_ctlcluster\b.*\bstart\b",
            r"(?:^|--\s+|\bpostgres\s+-c\s+\"?)createdb\b",
            r"(?:^|--\s+|\bpostgres\s+-c\s+\"?)createuser\b",
            # Config-binding obligation: the ALTER USER password reset needs the
            # server running, and the profile.d service-bind write is replaced by
            # the ENV bake + eval-wrapper export. Both are RUNTIME, never baked.
            r"ALTER\s+USER\s+\w+\s+(WITH\s+)?PASSWORD",
            r"/etc/profile\.d/zz_service_bind\.sh",
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

    def _observation_has_test_failure_signal(self, observation):
        """Detect output summaries that report nonzero test failures or errors."""
        if not observation:
            return False

        normalized_observation = self._normalize_observation_text(observation)
        failure_patterns = [
            r"\b[1-9]\d*[ \t]+(?:failed|failures?|errors?)\b",
            r"\b[1-9]\d*[ \t]+tests?[ \t]+failed\b",  # ctest "N tests failed" (audit [7])
            r"\b(?:failures?|errors?):\s*[1-9]\d*\b",
            r"\btests?[ \t]+run:\s*\d+,\s*failures?:\s*[1-9]\d*\b",
            r"\btests?[ \t]+run:\s*\d+,\s*failures?:\s*\d+,\s*errors?:\s*[1-9]\d*\b",
            r"\btest result:\s+failed\b",
            r"^\s*not ok\b",
            r"^\s*(?:FAILED|ERROR)\s+\S+",
            r"\bBUILD FAILURE\b",
            r"\bthere (?:was|were)\s+[1-9]\d*\s+(?:failure|failures|error|errors)\b",
        ]
        return any(
            re.search(pattern, normalized_observation, re.IGNORECASE | re.MULTILINE)
            for pattern in failure_patterns
        )

    def _observation_has_effective_test_signal(self, observation):
        """Detect observation text that strongly suggests real tests were executed."""
        if not observation:
            return False

        normalized_observation = self._normalize_observation_text(observation)
        positive_patterns = [
            r"collected\s+[1-9]\d*\s+items",
            r"\b[1-9]\d*\s+tests?\s+collected\b",
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
            r"^\s*passed:\s*[1-9]\d*\b",
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
