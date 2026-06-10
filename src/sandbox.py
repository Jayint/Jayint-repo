import io
import os
import re
import shlex
import tarfile
import docker
from src.synthesizer import Synthesizer

PIP_TRANSIENT_RETRY_ATTEMPTS = 3

SOURCE_SEMANTIC_EXTENSIONS = {
    ".py",
    ".pyi",
    ".ipynb",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".scala",
    ".kt",
    ".swift",
    ".m",
    ".mm",
    ".r",
    ".jl",
    ".lua",
}

ENV_CONFIG_FILENAMES = {
    "pyproject.toml",
    "poetry.lock",
    "pdm.lock",
    "uv.lock",
    "pipfile",
    "pipfile.lock",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "mypy.ini",
    "ruff.toml",
    ".python-version",
    "environment.yml",
    "environment.yaml",
    "dockerfile",
}

ENV_CONFIG_SUFFIXES = (
    ".toml",
    ".lock",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
)


class Sandbox:
    def __init__(
        self,
        base_image="ubuntu:22.04",
        workdir="/app",
        volumes=None,
        platform=None,
        seed_dir=None,
        command_timeout_seconds=None,
        apt_mirror_url=None,
        apt_retries=5,
        apt_http_timeout_seconds=120,
        apt_https_timeout_seconds=120,
        docker_client_timeout_seconds=None,
    ):
        self.client = docker.from_env(timeout=docker_client_timeout_seconds)
        self.base_image = base_image
        self.workdir = workdir
        self.volumes = volumes  # Mapping of {local_path: {'bind': container_path, 'mode': 'rw'}}
        self.platform = platform  # Docker platform (e.g., "linux/amd64" for x86_64 emulation on ARM64)
        self.seed_dir = os.path.abspath(seed_dir) if seed_dir else None
        self.command_timeout_seconds = command_timeout_seconds
        self.docker_client_timeout_seconds = docker_client_timeout_seconds
        self.current_image = base_image
        self.container = None
        self.last_success_image = None  # 记录上一次成功状态的镜像
        self.snapshot_image_ids = set()
        self.runtime_replay_commands = []
        self.package_manager_broken_failure_streak = 0
        self._command_classifier = Synthesizer()
        self.apt_mirror_url = self._resolve_apt_mirror_url(apt_mirror_url)
        self.apt_retries = apt_retries
        self.apt_http_timeout_seconds = apt_http_timeout_seconds
        self.apt_https_timeout_seconds = apt_https_timeout_seconds
        self._setup_initial_container()

    def _setup_initial_container(self):
        """Initializes the container from the base image."""
        print(f"Initializing container from {self.current_image}...")
        if self.platform:
            print(f"[Platform] Using platform: {self.platform}")
            # Pull the image with the correct platform if different from cached version
            try:
                print(f"[Platform] Pulling {self.current_image} for platform {self.platform}...")
                self.client.images.pull(self.current_image, platform=self.platform)
            except Exception as e:
                print(f"[Platform] Pull failed (may already exist): {e}")
        self.container = self.client.containers.run(
            self.current_image,
            detach=True,
            tty=True,
            working_dir=self.workdir,
            command="/bin/bash",
            volumes=self.volumes,
            platform=self.platform
        )
        # Ensure workdir exists
        self.container.exec_run(f"mkdir -p {self.workdir}")
        self._bootstrap_apt_if_supported()
        if self.seed_dir:
            self._seed_workdir_from_host()
        # Always keep a baseline snapshot so the first failed command can roll back
        # to the initialized workspace rather than the raw base image.
        baseline_image = self.container.commit()
        self._register_snapshot(baseline_image.id)
        self.last_success_image = baseline_image.id
        print(f"[Baseline Snapshot] {self.last_success_image[:12]}")

    def _resolve_apt_mirror_url(self, apt_mirror_url):
        configured = (
            apt_mirror_url
            or os.environ.get("JAYINT_APT_MIRROR_URL")
            or os.environ.get("APT_MIRROR_URL")
        )
        if not configured:
            return None
        return configured.rstrip("/")

    def _bootstrap_apt_if_supported(self):
        if not self.container:
            return

        bootstrap_command = self._build_apt_bootstrap_command()
        exec_result = self.container.exec_run(
            ["/bin/bash", "-lc", bootstrap_command],
            workdir=self.workdir,
        )
        exit_code = exec_result.exit_code
        output = exec_result.output.decode("utf-8", errors="replace")
        if exit_code == 0:
            if self.apt_mirror_url:
                print(f"[Apt Bootstrap] Configured retries and mirror: {self.apt_mirror_url}")
            else:
                print("[Apt Bootstrap] Configured apt retries/timeouts.")
            return

        print(
            f"[Apt Bootstrap Warning] Failed to prepare apt mirror/retry settings (exit {exit_code})."
        )
        if output.strip():
            print(output)

    def _build_apt_bootstrap_command(self):
        mirror_block = ""
        if self.apt_mirror_url:
            mirror = shlex.quote(self.apt_mirror_url)
            mirror_block = (
                f"APT_MIRROR_URL={mirror}\n"
                "for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/ubuntu.sources; do\n"
                "  [ -f \"$file\" ] || continue\n"
                "  sed -i \"s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g\" \"$file\"\n"
                "  sed -i \"s|https://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g\" \"$file\"\n"
                "  sed -i \"s|http://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g\" \"$file\"\n"
                "  sed -i \"s|https://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g\" \"$file\"\n"
                "done\n"
                "apt-get update\n"
            )

        return (
            "set -e\n"
            "if ! command -v apt-get >/dev/null 2>&1; then\n"
            "  exit 0\n"
            "fi\n"
            "mkdir -p /etc/apt/apt.conf.d\n"
            "cat >/etc/apt/apt.conf.d/99jayint-retries <<'EOF'\n"
            f"Acquire::Retries \"{self.apt_retries}\";\n"
            f"Acquire::http::Timeout \"{self.apt_http_timeout_seconds}\";\n"
            f"Acquire::https::Timeout \"{self.apt_https_timeout_seconds}\";\n"
            "Acquire::http::Pipeline-Depth \"0\";\n"
            "EOF\n"
            f"{mirror_block}"
        )

    def _seed_workdir_from_host(self):
        """Copy the host workspace into the container so rollback includes repo state."""
        if not os.path.isdir(self.seed_dir):
            raise ValueError(f"seed_dir does not exist or is not a directory: {self.seed_dir}")

        self.container.exec_run(
            ["/bin/bash", "-lc", f"rm -rf {self.workdir}/* {self.workdir}/.[!.]* {self.workdir}/..?* 2>/dev/null || true"]
        )

        archive_stream = io.BytesIO()
        with tarfile.open(fileobj=archive_stream, mode="w") as tar:
            for entry in sorted(os.listdir(self.seed_dir)):
                entry_path = os.path.join(self.seed_dir, entry)
                tar.add(entry_path, arcname=entry, recursive=True)
        archive_stream.seek(0)

        if not self.container.put_archive(self.workdir, archive_stream.getvalue()):
            raise RuntimeError(f"Failed to copy workspace from {self.seed_dir} into container")

    def execute(self, command):
        """
        Executes a bash command.
        Returns (success, output).
        """
        print(f"[Container ID: {self.container.short_id}]")
        print(f"Executing: {command}")

        preflight_rejection_prefix = self._get_preflight_rejection_prefix(command)
        if preflight_rejection_prefix:
            print("Command rejected before execution by sandbox preflight.")
            return False, preflight_rejection_prefix

        wrapped_command = ["/bin/bash", "-c", self._wrap_command_with_timeout(command)]
        try:
            exec_result = self.container.exec_run(
                wrapped_command,
                workdir=self.workdir
            )
        except docker.errors.DockerException as exc:
            print(f"[System] Command execution failed because the container is unusable: {exc}")
            recovery_message = self._restore_last_success_container(
                reason=f"container_exec_error: {exc}"
            )
            return False, (
                f"[SYSTEM] Command execution failed because the container became unusable: {exc}\n"
                f"{recovery_message}"
            )

        exit_code = exec_result.exit_code
        output = exec_result.output.decode('utf-8', errors='replace')
        retry_outputs = []
        attempt_index = 1
        while self._should_retry_transient_pip_failure(command, exit_code, output, attempt_index):
            retry_outputs.append(
                f"[SYSTEM] Transient pip install failure on attempt {attempt_index}; "
                f"retrying the same command.\n{output}"
            )
            attempt_index += 1
            print(
                f"[Sandbox Retry] Transient pip install failure; "
                f"retrying attempt {attempt_index}/{PIP_TRANSIENT_RETRY_ATTEMPTS}."
            )
            try:
                exec_result = self.container.exec_run(
                    wrapped_command,
                    workdir=self.workdir,
                )
            except docker.errors.DockerException as exc:
                print(f"[System] Command retry failed because the container is unusable: {exc}")
                recovery_message = self._restore_last_success_container(
                    reason=f"container_exec_retry_error: {exc}"
                )
                return False, (
                    f"[SYSTEM] Command retry failed because the container became unusable: {exc}\n"
                    f"{recovery_message}"
                )
            exit_code = exec_result.exit_code
            output = exec_result.output.decode('utf-8', errors='replace')

        if retry_outputs:
            output = "\n\n".join([*retry_outputs, output])

        if self._is_timeout_exit(exit_code):
            output = (
                f"[SYSTEM] Command timed out after {self.command_timeout_seconds} seconds.\n\n"
                f"{output}"
            )
        
        # 判断是否为"信息性退出"（非真正错误）
        is_informational_exit = self._is_informational_exit(exit_code, output)
        
        # 检测输出中是否有测试失败信号（用于 Observation 前缀注入）
        test_fail_prefix = self._get_test_failure_prefix(exit_code, output)
        truncated_test_prefix = self._get_truncated_test_output_prefix(command)
        
        if exit_code == 0 or is_informational_exit:
            # Success: 保存当前成功状态
            if is_informational_exit:
                print(f"Command exited with code {exit_code} (informational, not an error).")
            else:
                print("Command succeeded.")

            if truncated_test_prefix:
                output = truncated_test_prefix + output

            self.package_manager_broken_failure_streak = 0
            self._track_runtime_command(command)
            
            # 优化：只对会对环境产生影响的指令进行 commit
            if self._should_commit(command):
                # 创建新的成功快照
                previous_snapshot = self.last_success_image
                success_image = self.container.commit()
                self._register_snapshot(success_image.id)
                self.last_success_image = success_image.id
                if previous_snapshot and previous_snapshot != self.last_success_image:
                    self._remove_snapshot_image(previous_snapshot)
                print(f"[Snapshot Created] {self.last_success_image[:12]}")
            else:
                print("[Skip Snapshot] Command is read-only or informational.")
            
            return True, output
        else:
            print(f"Command failed (exit {exit_code}). Preserving current state for agent decision.")

            failed_mutation_prefix = self._get_failed_mutating_command_prefix(
                command, exit_code
            )
            rollback_candidate_prefix = self._get_package_manager_rollback_prefix(
                command, exit_code, output
            )
            if truncated_test_prefix:
                output = truncated_test_prefix + output
            if test_fail_prefix:
                output = test_fail_prefix + output
            if failed_mutation_prefix:
                output = failed_mutation_prefix + output
            if rollback_candidate_prefix:
                output = rollback_candidate_prefix + output

            if not self._container_is_healthy():
                print("[System] Container became unhealthy after the failed command. Restoring last successful snapshot.")
                recovery_message = self._restore_last_success_container(
                    reason=f"container_unhealthy_after_exit_{exit_code}"
                )
                if output and not output.endswith("\n"):
                    output += "\n"
                output += (
                    "\n[SYSTEM] The container became unhealthy after the failed command, "
                    "so the system restored the last successful snapshot automatically.\n"
                    f"{recovery_message}"
                )

            return False, output

    def inspect(self, command):
        """
        Executes a planning-time diagnostic command without committing a snapshot
        or recording runtime setup state. Callers must pass read-only commands.
        """
        print(f"[Container ID: {self.container.short_id}]")
        print(f"[Planning Inspect] Executing: {command}")

        wrapped_command = ["/bin/bash", "-c", self._wrap_command_with_timeout(command)]
        try:
            exec_result = self.container.exec_run(
                wrapped_command,
                workdir=self.workdir,
            )
        except docker.errors.DockerException as exc:
            return False, f"[SYSTEM] Planning inspect command failed: {exc}"

        exit_code = exec_result.exit_code
        output = exec_result.output.decode("utf-8", errors="replace")
        if self._is_timeout_exit(exit_code):
            output = (
                f"[SYSTEM] Planning inspect command timed out after "
                f"{self.command_timeout_seconds} seconds.\n\n{output}"
            )

        success = exit_code == 0 or self._is_informational_exit(exit_code, output)
        if success:
            print("[Planning Inspect] Command succeeded without snapshot.")
        else:
            print(f"[Planning Inspect] Command failed (exit {exit_code}) without rollback.")
        return success, output

    def write_text_file(self, path, content):
        """Write a host-managed control file into the container without recording setup state."""
        directory = os.path.dirname(path) or "."
        mkdir_result = self.container.exec_run(
            ["/bin/bash", "-c", f"mkdir -p {shlex.quote(directory)}"],
            workdir=self.workdir,
        )
        if mkdir_result.exit_code != 0:
            output = mkdir_result.output.decode("utf-8", errors="replace")
            return False, f"Failed to create container directory {directory}: {output}"

        archive_buffer = io.BytesIO()
        data = (content or "").encode("utf-8")
        tar_info = tarfile.TarInfo(name=os.path.basename(path))
        tar_info.size = len(data)
        tar_info.mode = 0o644
        with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
            archive.addfile(tar_info, io.BytesIO(data))
        archive_buffer.seek(0)

        try:
            self.container.put_archive(directory, archive_buffer.getvalue())
        except docker.errors.DockerException as exc:
            return False, f"Failed to write container file {path}: {exc}"
        return True, f"Wrote container file {path}"

    def rollback(self, reason="agent_requested"):
        """Restore the container to the last successful snapshot on explicit agent request."""
        print(f"[System] Explicit rollback requested ({reason}).")
        try:
            message = self._restore_last_success_container(reason=reason)
        except docker.errors.DockerException as exc:
            return False, f"[SYSTEM] Rollback failed: {exc}"
        return True, message

    def _register_snapshot(self, image_id):
        if image_id:
            self.snapshot_image_ids.add(image_id)

    def _remove_snapshot_image(self, image_id):
        if not image_id:
            return
        try:
            image = self.client.images.get(image_id)
            self.client.images.remove(image.id, force=True)
        except (docker.errors.ImageNotFound, docker.errors.APIError):
            return
        finally:
            self.snapshot_image_ids.discard(image_id)

    def _restore_last_success_container(self, reason="rollback"):
        rollback_image = self.last_success_image if self.last_success_image else self.base_image

        if self.container:
            try:
                self.container.stop()
            except docker.errors.DockerException:
                pass
            try:
                self.container.remove()
            except docker.errors.DockerException:
                pass

        self.container = self.client.containers.run(
            rollback_image,
            detach=True,
            tty=True,
            working_dir=self.workdir,
            command="/bin/bash",
            volumes=self.volumes,
            platform=self.platform
        )
        self.container.exec_run(f"mkdir -p {self.workdir}")
        self._replay_runtime_commands()
        return (
            "[SYSTEM] Restored the container to the last successful snapshot "
            f"because of {reason}.\n"
            "[SYSTEM] Ephemeral runtime services were replayed when possible."
        )

    def _wrap_command_with_timeout(self, command):
        """Enforce a per-command timeout when GNU `timeout` is available in the container."""
        pipefail_command = self._wrap_command_with_pipefail(command)
        if not self.command_timeout_seconds:
            return pipefail_command

        timeout_seconds = int(self.command_timeout_seconds)
        return (
            "if command -v timeout >/dev/null 2>&1; then "
            f"timeout --foreground --kill-after=30s {timeout_seconds}s {pipefail_command}; "
            "else "
            f"{pipefail_command}; "
            "fi"
        )

    def _wrap_command_with_pipefail(self, command):
        quoted_command = shlex.quote(command)
        return f"/bin/bash -o pipefail -lc {quoted_command}"

    def _is_timeout_exit(self, exit_code):
        if not self.command_timeout_seconds:
            return False
        return exit_code in {124, 137}

    def _should_retry_transient_pip_failure(self, command, exit_code, output, attempt_index):
        if attempt_index >= PIP_TRANSIENT_RETRY_ATTEMPTS:
            return False
        if exit_code == 0:
            return False
        if not self._looks_like_pip_install_command(command):
            return False

        normalized_output = (output or "").lower()
        transient_markers = (
            "readtimeouterror",
            "connection reset",
            "connection aborted",
            "connection broken",
            "remote disconnected",
            "temporary failure in name resolution",
            "temporarily unavailable",
            "timed out",
            "timeout",
            "sslerror",
            "max retries exceeded",
            "too many 502 error responses",
            "too many 503 error responses",
            "too many 504 error responses",
            "network is unreachable",
        )
        return any(marker in normalized_output for marker in transient_markers)

    def _looks_like_pip_install_command(self, command):
        for raw_segment, _ in self._command_classifier._split_shell_chain(command or ""):
            normalized = self._command_classifier._normalize_command_segment(raw_segment)
            if self._command_classifier._is_navigation_only_segment(normalized):
                continue
            if re.match(r"^(?:pip|pip2|pip3)\s+install\b", normalized):
                return True
            if re.match(r"^(?:python|python2|python3)\s+-m\s+pip\s+install\b", normalized):
                return True
            if re.match(r"^uv\s+pip\s+install\b", normalized):
                return True
        return False

    def _container_is_healthy(self):
        if not self.container:
            return False

        reload_fn = getattr(self.container, "reload", None)
        if callable(reload_fn):
            try:
                reload_fn()
            except docker.errors.DockerException:
                return False

        status = getattr(self.container, "status", None)
        if status is None:
            return True
        return status == "running"
    
    def _should_commit(self, command):
        """
        判断指令是否会对环境产生影响，从而决定是否需要 commit。
        """
        return self._command_classifier.command_mutates_environment(command or "")

    def _track_runtime_command(self, command):
        """Remember pure runtime service commands so they can be replayed after rollback."""
        normalized_command = (command or "").strip()
        if not normalized_command:
            return

        if not self._command_classifier.is_runtime_service_command(normalized_command):
            return

        runtime_key = self._runtime_service_key(normalized_command)
        runtime_action = self._runtime_service_action(normalized_command)

        if runtime_key and runtime_action == "stop":
            self.runtime_replay_commands = [
                entry for entry in self.runtime_replay_commands if entry["key"] != runtime_key
            ]
            return

        if self._command_classifier.is_persistent_setup_command(normalized_command):
            return

        if runtime_key:
            self.runtime_replay_commands = [
                entry for entry in self.runtime_replay_commands if entry["key"] != runtime_key
            ]

        self.runtime_replay_commands.append(
            {
                "key": runtime_key or normalized_command,
                "command": normalized_command,
            }
        )

    def _replay_runtime_commands(self):
        """Restore ephemeral runtime services after container rollback."""
        if not self.runtime_replay_commands:
            return

        print(
            f"[Runtime Replay] Re-running {len(self.runtime_replay_commands)} runtime command(s) after rollback."
        )
        restored_commands = []
        for entry in self.runtime_replay_commands:
            command = entry["command"]
            exec_result = self.container.exec_run(
                ["/bin/bash", "-c", self._wrap_command_with_timeout(command)],
                workdir=self.workdir,
            )
            exit_code = exec_result.exit_code
            output = exec_result.output.decode("utf-8", errors="replace")
            if exit_code == 0 or self._is_informational_exit(exit_code, output):
                print(f"[Runtime Replay] Restored: {command}")
                restored_commands.append(entry)
                continue

            print(
                f"[Runtime Replay Warning] Failed to restore runtime command (exit {exit_code}): {command}"
            )

        self.runtime_replay_commands = restored_commands

    def _runtime_service_key(self, command):
        normalized = command.strip().lower()
        service_match = re.match(r"^service\s+(\S+)\s+(?:start|restart|reload|stop)\b", normalized)
        if service_match:
            return service_match.group(1)

        executable = normalized.split()[0] if normalized else ""
        executable = executable.strip("\"'`")
        if executable:
            return executable.rsplit("/", 1)[-1]
        return None

    def _runtime_service_action(self, command):
        normalized = command.strip().lower()
        service_match = re.match(r"^service\s+\S+\s+(start|restart|reload|stop)\b", normalized)
        if service_match:
            return service_match.group(1)
        if " --daemonize " in f" {normalized} " or " -detached" in normalized or " --fork" in normalized:
            return "start"
        return "start"
    
    def _is_informational_exit(self, exit_code, output):
        """
        判断是否为信息性退出（如显示帮助信息），而非真正的错误。
        测试命令的失败（如测试未通过）不应被视为信息性退出。
        """
        # Exit code 1-2 通常是参数错误或显示帮助
        if exit_code not in [1, 2]:
            return False
        
        # 检查输出中是否包含帮助信息的特征
        help_indicators = [
            'Usage:',
            'usage:',
            '--help',
            'Options:',
            'Commands:',
            'positional arguments:',
            'optional arguments:'
        ]
        
        # 测试失败的特征（不应被误判为信息性退出）
        test_failure_indicators = [
            'failures:',
            'errors:',
            'FAILED',
            'Failed:',        # run_all / TAP 格式：Failed: 3
            'not ok',         # TAP 协议失败行
            'Test failed',
            'assertion failed',
            'expected',
            'actual',
            'diff:',
            'Traceback (most recent call last):',
            'NameError',
            'ImportError',
            'ModuleNotFoundError',
            'LoadError',
            'Gem::LoadError',
            'bundler: command not found'
        ]
        
        output_lower = output.lower()
        
        # 如果包含测试失败特征，则不是信息性退出
        if any(indicator.lower() in output_lower for indicator in test_failure_indicators):
            return False
        
        return any(indicator.lower() in output_lower for indicator in help_indicators)

    def _get_test_failure_prefix(self, exit_code, output):
        """
        检测命令输出是否包含测试失败信号。
        若是，返回注入到 Observation 头部的强制警告；否则返回空字符串。
        目的：阻止 LLM 以"核心功能通过"为由自我合理化，绕过 No Excuses Rule。
        """
        if exit_code == 0:
            return ""

        # TAP 格式失败：run_all 输出的 "Failed: N"
        tap_fail = re.search(r'Failed:\s+([1-9]\d*)', output)
        if tap_fail:
            failed_count = tap_fail.group(1)
            return (
                f"[SYSTEM] ⚠️  TEST FAILURE DETECTED: {failed_count} test(s) FAILED.\n"
                f"[SYSTEM] Per the No Excuses Rule, you CANNOT output 'Final Answer: Success' "
                f"from this failed command. Repo2Run success requires a real "
                f"`pytest --collect-only -q --disable-warnings` verification, or the Poetry "
                f"equivalent, that proves tests are collectable. Full test failures do not need "
                f"to pass if collection succeeds.\n\n"
            )

        # pytest / unittest 格式失败
        pytest_fail = re.search(r'([1-9]\d*) failed', output, re.IGNORECASE)
        if pytest_fail:
            failed_count = pytest_fail.group(1)
            return (
                f"[SYSTEM] ⚠️  TEST FAILURE DETECTED: {failed_count} test(s) FAILED.\n"
                f"[SYSTEM] Per the No Excuses Rule, you CANNOT output 'Final Answer: Success' "
                f"from this failed command. If full test execution fails, rerun the Repo2Run "
                f"collection command and use it as final proof only if collection succeeds.\n\n"
            )

        pytest_error = re.search(r'([1-9]\d*) errors?', output, re.IGNORECASE)
        if pytest_error:
            error_count = pytest_error.group(1)
            return (
                f"[SYSTEM] ⚠️  TEST FAILURE DETECTED: {error_count} test error(s) reported.\n"
                f"[SYSTEM] Per the No Excuses Rule, you CANNOT output 'Final Answer: Success' "
                f"until Repo2Run-style pytest collection succeeds without collection/import/config "
                f"errors.\n\n"
            )

        if self._command_classifier.observation_has_test_failure_signal(output):
            return (
                "[SYSTEM] ⚠️  TEST FAILURE DETECTED in command output.\n"
                "[SYSTEM] Per the No Excuses Rule, you CANNOT output 'Final Answer: Success' "
                "from this failed command. Final proof must be a successful Repo2Run-style "
                "pytest collection command, not a failed full test run.\n\n"
            )

        # 通用失败关键词。只匹配测试用例行，避免把 "Failed: 0" 当成失败。
        if (
            re.search(r'^\s*(?:FAILED|ERROR)\s+\S+', output, re.MULTILINE)
            or re.search(r'^\s*not ok\b', output, re.IGNORECASE | re.MULTILINE)
        ):
            return (
                "[SYSTEM] ⚠️  TEST FAILURE DETECTED in command output.\n"
                "[SYSTEM] Per the No Excuses Rule, you CANNOT output 'Final Answer: Success' "
                "from this failed command. Final proof must be a successful Repo2Run-style "
                "pytest collection command.\n\n"
            )

        return ""

    def _get_truncated_test_output_prefix(self, command):
        """Warn when a test command is piped through a lossy output filter."""
        if not self._command_classifier.is_truncated_test_output_command(command or ""):
            return ""

        return (
            "[SYSTEM] ⚠️  TRUNCATED TEST OUTPUT: this command pipes a test run through "
            "`head`, `tail`, or `grep`, so the Observation may omit failures, passes, or the final "
            "test summary.\n"
            "[SYSTEM] Do NOT treat this as complete verification. For final verification, "
            "run the full project test command without output-limiting pipes; long output "
            "will be handled by observation compression.\n\n"
        )

    def _get_preflight_rejection_prefix(self, command):
        """Reject commands that are too ambiguous to record/replay safely."""
        return (
            self._get_invalid_output_filter_prefix(command)
            or self._get_protected_source_mutation_prefix(command)
            or self._get_invalid_compound_setup_prefix(command)
        )

    def _get_protected_source_mutation_prefix(self, command):
        protected_target = self._find_protected_source_mutation_target(command or "")
        if not protected_target:
            return ""

        return (
            "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: this Action attempts to "
            f"modify repository source/test code at `{protected_target}`.\n"
            "[SYSTEM] Repo2Run setup may change environment and dependency configuration "
            "files such as `pyproject.toml`, `.lock`, `requirements*.txt`, `setup.cfg`, "
            "or `tox.ini`, but it must not create stubs, rewrite tests, or change source "
            "semantics. Fix missing packages with installs, local imports with PYTHONPATH/"
            "editable install, or dependency conflicts by editing configuration files.\n"
            "[SYSTEM] The command was NOT executed and the environment was not changed.\n\n"
        )

    def _find_protected_source_mutation_target(self, command):
        for raw_segment, _ in self._command_classifier._split_shell_chain(command or ""):
            for target, operation in self._protected_mutation_targets_from_segment(raw_segment):
                if self._is_protected_source_mutation_target(target, operation):
                    return target
        return ""

    def _protected_mutation_targets_from_segment(self, raw_segment):
        segment = raw_segment or ""
        cleaned_segment = self._command_classifier._strip_trailing_redirections(segment)
        targets = []

        for match in re.finditer(r"(?<!\d)(?:>>?|&>)\s*([^\s;&|]+)", segment):
            target = self._clean_shell_path_token(match.group(1))
            if target:
                targets.append((target, "write_file"))

        try:
            tokens = shlex.split(cleaned_segment, posix=True)
        except ValueError:
            tokens = []

        if tokens:
            command = tokens[0].rsplit("/", 1)[-1]
            if command == "tee":
                append_mode = False
                for token in tokens[1:]:
                    if token in {"-a", "--append"}:
                        append_mode = True
                        continue
                    if token.startswith("-"):
                        continue
                    targets.append((token, "append_file" if append_mode else "write_file"))
            elif command == "sed" and any(token == "-i" or token.startswith("-i") for token in tokens[1:]):
                for token in tokens[1:]:
                    if token.startswith("-"):
                        continue
                    targets.append((token, "edit_file"))
            elif command == "touch":
                for token in tokens[1:]:
                    if not token.startswith("-"):
                        targets.append((token, "write_file"))
            elif command in {"mkdir", "install"}:
                for token in tokens[1:]:
                    if not token.startswith("-"):
                        targets.append((token, "make_dir"))
            elif command in {"cp", "mv"} and len(tokens) >= 3:
                targets.append((tokens[-1], "write_path"))

        protected_roots = self._protected_source_root_patterns()
        if re.search(r"\b(?:open|Path)\s*\([^)]*(?:/app|src/|tests?/)", segment):
            for pattern in protected_roots:
                match = re.search(pattern, segment)
                if match:
                    targets.append((match.group(0), "write_file"))
        if re.search(r"\bwrite_text\s*\(", segment) or re.search(r"\bmkdir\s*\(", segment):
            for pattern in protected_roots:
                match = re.search(pattern, segment)
                if match:
                    targets.append((match.group(0), "write_file"))

        return targets

    def _protected_source_root_patterns(self):
        workdir = re.escape((self.workdir or "/app").rstrip("/"))
        return (
            rf"{workdir}/src/[^\s'\";|)]+",
            rf"{workdir}/tests?/[^\s'\";|)]+",
            r"src/[^\s'\";|)]+",
            r"tests?/[^\s'\";|)]+",
        )

    def _clean_shell_path_token(self, token):
        token = (token or "").strip()
        if not token:
            return ""
        token = token.strip("\"'")
        if token in {"/dev/null", "-", "&1"}:
            return ""
        if token.startswith("&"):
            return ""
        return token

    def _is_protected_source_mutation_target(self, target, operation):
        normalized = self._normalize_container_path(target)
        if not normalized:
            return False

        workdir = (self.workdir or "/app").rstrip("/") or "/app"
        if normalized == workdir:
            return False
        if not normalized.startswith(workdir + "/"):
            return False

        rel_path = normalized[len(workdir) + 1 :]
        lowered_rel = rel_path.lower()
        basename = lowered_rel.rsplit("/", 1)[-1]
        _, extension = os.path.splitext(basename)

        if self._is_environment_config_path(lowered_rel):
            return False

        protected_prefixes = ("src/", "tests/", "test/", "spec/", "features/")
        if lowered_rel.startswith(protected_prefixes):
            if operation in {"make_dir", "write_path"}:
                return True
            return extension in SOURCE_SEMANTIC_EXTENSIONS or "." not in basename

        if extension in SOURCE_SEMANTIC_EXTENSIONS:
            return True
        if basename.startswith("test_") or basename.endswith("_test.py"):
            return True
        return False

    def _normalize_container_path(self, target):
        target = self._clean_shell_path_token(target)
        if not target:
            return ""
        if re.search(r"[$*?{}\[\]`]", target):
            return ""
        if target.startswith("~"):
            return ""

        workdir = (self.workdir or "/app").rstrip("/") or "/app"
        if target.startswith("/"):
            normalized = os.path.normpath(target)
        else:
            normalized = os.path.normpath(os.path.join(workdir, target))
        return normalized

    def _is_environment_config_path(self, lowered_rel_path):
        basename = lowered_rel_path.rsplit("/", 1)[-1]
        if basename in ENV_CONFIG_FILENAMES:
            return True
        if basename.startswith(("requirements", "constraints")) and basename.endswith(".txt"):
            return True
        if basename.endswith(ENV_CONFIG_SUFFIXES) and "/" not in lowered_rel_path:
            return True
        return False

    def _get_invalid_output_filter_prefix(self, command):
        """Reject setup/test commands piped through lossy output filters."""
        if not self._command_pipes_setup_or_test_through_output_filter(command or ""):
            return ""

        return (
            "[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: setup or test commands "
            "must not pipe output through `head`, `tail`, or `grep` because those "
            "filters can hide failures and mask the real exit status.\n"
            "[SYSTEM] The command was NOT executed and the environment was not "
            "changed. Rerun the full command without output filtering. Long output "
            "will be handled by observation compression.\n\n"
        )

    def _command_pipes_setup_or_test_through_output_filter(self, command):
        for raw_segment, _ in self._command_classifier._split_shell_chain(command or ""):
            pipeline_components = self._command_classifier._split_pipeline(raw_segment)
            if len(pipeline_components) < 2:
                continue

            saw_setup_or_test = False
            for component in pipeline_components:
                segment_type = self._classify_preflight_segment(component)
                if segment_type == "output_filter" and saw_setup_or_test:
                    return True
                if segment_type in {"setup", "test", "probe"}:
                    saw_setup_or_test = True
        return False

    def _get_invalid_compound_setup_prefix(self, command):
        """Reject multi-action setup chains that should be split into separate Actions."""
        segments = self._classified_preflight_segments(command)
        if len(segments) <= 1:
            return ""

        if self._is_allowed_apt_update_install_chain(segments):
            return ""

        setup_segments = [segment for segment in segments if segment["type"] == "setup"]
        if len(setup_segments) >= 2:
            return self._build_invalid_compound_setup_message(
                "this Action combines multiple independent setup mutations",
            )

        if len(setup_segments) == 1:
            mixed_segments = [
                segment
                for segment in segments
                if segment["type"] in {"test", "probe", "readonly"}
            ]
            if mixed_segments:
                return self._build_invalid_compound_setup_message(
                    "this Action combines a setup mutation with a verification, probe, or read-only check",
                )

        return ""

    def _classified_preflight_segments(self, command):
        segments = []
        for raw_segment, _ in self._command_classifier._split_shell_chain(command or ""):
            segment_type = self._classify_preflight_segment(raw_segment)
            if segment_type == "empty":
                continue
            segments.append({"raw": raw_segment.strip(), "type": segment_type})
        return segments

    def _classify_preflight_segment(self, raw_segment):
        cleaned_segment = self._command_classifier._strip_trailing_redirections(
            raw_segment or ""
        )
        normalized = self._command_classifier._normalize_command_segment(cleaned_segment)
        if not normalized:
            return "empty"
        if self._command_classifier._is_output_truncation_component(normalized):
            return "output_filter"
        if self._command_classifier._is_navigation_only_segment(normalized):
            return "navigation"
        if self._is_setup_preparation_segment(normalized):
            return "preparation"
        if self._command_classifier._is_test_like_segment(normalized):
            return "test"
        if self._is_probe_segment(normalized):
            return "probe"
        if self._command_classifier._is_readonly_command(cleaned_segment):
            return "readonly"
        if self._command_classifier._segment_has_meaningful_setup_activity(normalized):
            return "setup"
        return "other"

    def _is_setup_preparation_segment(self, normalized_segment):
        return (
            normalized_segment.startswith("mkdir -p ")
            or normalized_segment.startswith("mkdir --parents ")
            or normalized_segment.startswith("install -d ")
        )

    def _is_probe_segment(self, normalized_segment):
        probe_patterns = (
            r"^(?:python|python2|python3)\s+-c\b",
            r"^(?:node)\s+-e\b",
            r"^(?:ruby)\s+-e\b",
            r"^(?:php)\s+-r\b",
            r"^(?:pip|pip2|pip3|python\s+-m\s+pip|python3\s+-m\s+pip)\s+(?:check|show|list|freeze)\b",
            r"^(?:npm|yarn|pnpm)\s+(?:list|ls)\b",
        )
        return any(re.search(pattern, normalized_segment) for pattern in probe_patterns)

    def _is_allowed_apt_update_install_chain(self, segments):
        meaningful_segments = [
            segment
            for segment in segments
            if segment["type"] not in {"navigation", "preparation"}
        ]
        if len(meaningful_segments) != 2:
            return False

        first = self._command_classifier._normalize_command_segment(
            meaningful_segments[0]["raw"]
        )
        second = self._command_classifier._normalize_command_segment(
            meaningful_segments[1]["raw"]
        )
        return (
            re.match(r"^(?:apt-get|apt)\s+update\b", first) is not None
            and re.match(r"^(?:apt-get|apt)\s+install\b", second) is not None
        )

    def _build_invalid_compound_setup_message(self, reason):
        return (
            f"[SYSTEM] COMMAND REJECTED BEFORE EXECUTION: {reason}.\n"
            "[SYSTEM] The command was NOT executed and the environment was not changed. "
            "Run each setup mutation, verification, or probe as a separate Action so each "
            "state-changing step can be confirmed independently.\n\n"
        )

    def _get_failed_mutating_command_prefix(self, command, exit_code):
        """Warn when a failed setup command may have left partial environment changes."""
        if exit_code == 0:
            return ""

        if not self._command_classifier.command_mutates_environment(command or ""):
            return ""

        return (
            "[SYSTEM] FAILED SETUP MUTATION: this setup command failed after attempting "
            "to change the environment.\n"
            "[SYSTEM] It may have partially installed packages, modified files, or changed "
            "services. Do not assume useful parts of this failed command are reliably "
            "available for later steps.\n"
            "[SYSTEM] If any prefix or sub-step appears useful, rerun that prefix/sub-step "
            "as its own separate Action so it is confirmed successful. If the partial "
            "changes may have polluted the environment, use `Action: __ROLLBACK__` to "
            "restore the previous snapshot.\n\n"
        )

    def _get_package_manager_rollback_prefix(self, command, exit_code, output):
        """Warn the agent when package-manager failures strongly suggest a dirty dependency state."""
        if exit_code == 0:
            return ""

        normalized_command = (command or "").strip().lower()
        output_lower = (output or "").lower()

        if not self._looks_like_package_manager_command(normalized_command):
            self.package_manager_broken_failure_streak = 0
            return ""

        broken_state_markers = (
            "unmet dependencies",
            "fix-broken install",
            "is not going to be installed",
            "depends:",
            "correcting dependencies... done",
        )
        if not any(marker in output_lower for marker in broken_state_markers):
            self.package_manager_broken_failure_streak = 0
            return ""

        self.package_manager_broken_failure_streak += 1

        if self.package_manager_broken_failure_streak >= 2:
            return (
                "[SYSTEM] STRONG ROLLBACK CANDIDATE: package-manager recovery is still failing "
                "after repeated attempts, and the container likely remains in a broken dependency state.\n"
                "[SYSTEM] Unless you have a concrete repair step that will cleanly resolve the package state, "
                "seriously consider `Action: __ROLLBACK__` before continuing.\n\n"
            )

        return (
            "[SYSTEM] ROLLBACK CANDIDATE: this package-manager failure indicates broken dependencies "
            "or partial package state.\n"
            "[SYSTEM] Consider `Action: __ROLLBACK__` if you believe the failed install left the environment "
            "in an uncertain state, especially before trying unrelated setup work.\n\n"
        )

    def _looks_like_package_manager_command(self, normalized_command):
        package_manager_prefixes = (
            "apt ",
            "apt-get ",
            "yum ",
            "dnf ",
            "apk ",
            "pacman ",
            "zypper ",
        )
        return normalized_command.startswith(package_manager_prefixes)

    def close(self, keep_alive=False):
        """关闭容器，可选择保持容器运行以供验证"""
        if self.container:
            if keep_alive:
                print(f"\n[Container Kept Alive] ID: {self.container.short_id}")
                print(f"To inspect: docker exec -it {self.container.short_id} /bin/bash")
                print(f"To stop later: docker stop {self.container.short_id}")
            else:
                try:
                    self.container.stop()
                    self.container.remove()
                    print("\n[Container Cleaned Up]")
                except docker.errors.DockerException:
                    pass

        for snapshot_id in list(self.snapshot_image_ids):
            try:
                self._remove_snapshot_image(snapshot_id)
                print("[Snapshot Image Cleaned]")
            except docker.errors.DockerException:
                pass
