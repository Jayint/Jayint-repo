import io
import os
import re
import shlex
import tarfile
import docker

class Sandbox:
    def __init__(
        self,
        base_image="ubuntu:22.04",
        workdir="/app",
        volumes=None,
        platform=None,
        seed_dir=None,
        command_timeout_seconds=1200,
    ):
        self.client = docker.from_env()
        self.base_image = base_image
        self.workdir = workdir
        self.volumes = volumes  # Mapping of {local_path: {'bind': container_path, 'mode': 'rw'}}
        self.platform = platform  # Docker platform (e.g., "linux/amd64" for x86_64 emulation on ARM64)
        self.seed_dir = os.path.abspath(seed_dir) if seed_dir else None
        self.command_timeout_seconds = command_timeout_seconds
        self.current_image = base_image
        self.container = None
        self.last_success_image = None  # 记录上一次成功状态的镜像
        self.snapshot_image_ids = set()
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
        if self.seed_dir:
            self._seed_workdir_from_host()
        # Always keep a baseline snapshot so the first failed command can roll back
        # to the initialized workspace rather than the raw base image.
        baseline_image = self.container.commit()
        self._register_snapshot(baseline_image.id)
        self.last_success_image = baseline_image.id
        print(f"[Baseline Snapshot] {self.last_success_image[:12]}")

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
        Executes a bash command with rollback mechanism.
        Returns (success, output).
        """
        print(f"[Container ID: {self.container.short_id}]")
        print(f"Executing: {command}")
        
        # Execute the command
        exec_result = self.container.exec_run(
            ["/bin/bash", "-c", self._wrap_command_with_timeout(command)],
            workdir=self.workdir
        )
        
        exit_code = exec_result.exit_code
        output = exec_result.output.decode('utf-8', errors='replace')

        if self._is_timeout_exit(exit_code):
            output = (
                f"[SYSTEM] Command timed out after {self.command_timeout_seconds} seconds.\n\n"
                f"{output}"
            )
        
        # 判断是否为"信息性退出"（非真正错误）
        is_informational_exit = self._is_informational_exit(exit_code, output)
        
        # 检测输出中是否有测试失败信号（用于 Observation 前缀注入）
        test_fail_prefix = self._get_test_failure_prefix(exit_code, output)
        
        if exit_code == 0 or is_informational_exit:
            # Success: 保存当前成功状态
            if is_informational_exit:
                print(f"Command exited with code {exit_code} (informational, not an error).")
            else:
                print("Command succeeded.")
            
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
            # Failure: 从上一次成功状态回滚
            print(f"Command failed (exit {exit_code}). Rolling back...")
            self.container.stop()
            self.container.remove()
            
            # 从上一次成功的镜像重启（如果存在）
            rollback_image = self.last_success_image if self.last_success_image else self.base_image
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
            # 如果检测到测试失败，在 output 前注入强制提示
            if test_fail_prefix:
                output = test_fail_prefix + output
            return False, output

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

    def _wrap_command_with_timeout(self, command):
        """Enforce a per-command timeout when GNU `timeout` is available in the container."""
        if not self.command_timeout_seconds:
            return command

        timeout_seconds = int(self.command_timeout_seconds)
        quoted_command = shlex.quote(command)
        return (
            "if command -v timeout >/dev/null 2>&1; then "
            f"timeout --foreground --kill-after=30s {timeout_seconds}s /bin/bash -lc {quoted_command}; "
            "else "
            f"/bin/bash -lc {quoted_command}; "
            "fi"
        )

    def _is_timeout_exit(self, exit_code):
        if not self.command_timeout_seconds:
            return False
        return exit_code in {124, 137}
    
    def _should_commit(self, command):
        """
        判断指令是否会对环境产生影响，从而决定是否需要 commit。
        """
        # 常见的不产生副作用的指令
        readonly_commands = [
            'ls', 'cat', 'pwd', 'echo', 'env', 'hostname', 'whoami', 
            'head', 'tail', 'grep', 'find', 'du', 'df', 'top', 'ps', 
            'date', 'which', 'type', 'file'
        ]
        
        # 获取指令的第一个单词
        first_word = command.strip().split()[0].lower() if command.strip() else ""
        
        # 如果指令在只读列表中，则不 commit
        if first_word in readonly_commands:
            return False
            
        # 默认需要 commit
        return True
    
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
                f"until ALL tests pass. Partial pass ({failed_count} failures) is NOT acceptable. "
                f"You MUST fix the failing tests.\n\n"
            )

        # pytest / unittest 格式失败
        pytest_fail = re.search(r'(\d+) failed', output, re.IGNORECASE)
        if pytest_fail:
            failed_count = pytest_fail.group(1)
            return (
                f"[SYSTEM] ⚠️  TEST FAILURE DETECTED: {failed_count} test(s) FAILED.\n"
                f"[SYSTEM] Per the No Excuses Rule, you CANNOT output 'Final Answer: Success' "
                f"until ALL tests pass.\n\n"
            )

        # 通用 FAILED 关键词
        if 'FAILED' in output or 'not ok' in output.lower():
            return (
                "[SYSTEM] ⚠️  TEST FAILURE DETECTED in command output.\n"
                "[SYSTEM] Per the No Excuses Rule, you CANNOT output 'Final Answer: Success' "
                "until ALL tests pass.\n\n"
            )

        return ""

    def get_container_info(self):
        """返回容器的详细信息，用于调试验证"""
        if self.container:
            return {
                'id': self.container.id,
                'short_id': self.container.short_id,
                'name': self.container.name,
                'status': self.container.status
            }
        return None
    
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
