import unittest
from types import SimpleNamespace
from unittest import mock

from src.sandbox import Sandbox
from src.synthesizer import Synthesizer


class FakeContainer:
    def __init__(self, results=None, status="running", short_id="fake123"):
        self.results = list(results or [])
        self.calls = []
        self.status = status
        self.short_id = short_id
        self.stopped = False
        self.removed = False

    def exec_run(self, command, workdir=None):
        self.calls.append({"command": command, "workdir": workdir})
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(exit_code=0, output=b"")

    def stop(self):
        self.stopped = True

    def remove(self):
        self.removed = True

    def reload(self):
        return None

    def commit(self):
        return SimpleNamespace(id="snapshot123456")


class FakeContainerManager:
    def __init__(self, containers=None):
        self.containers_to_return = list(containers or [])
        self.run_calls = []

    def run(self, *args, **kwargs):
        self.run_calls.append({"args": args, "kwargs": kwargs})
        if not self.containers_to_return:
            raise AssertionError("No fake container available for run()")
        return self.containers_to_return.pop(0)


class FakeDockerClient:
    def __init__(self, containers=None):
        self.containers = FakeContainerManager(containers=containers)


class SandboxRuntimeReplayTests(unittest.TestCase):
    def _make_sandbox(self, replay_results=None):
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.runtime_replay_commands = []
        sandbox.package_manager_broken_failure_streak = 0
        sandbox._command_classifier = Synthesizer()
        sandbox.command_timeout_seconds = None
        sandbox.workdir = "/app"
        sandbox.container = FakeContainer(results=replay_results)
        sandbox.client = FakeDockerClient()
        sandbox.last_success_image = "snapshot123"
        sandbox.snapshot_image_ids = set()
        sandbox.base_image = "ubuntu:22.04"
        sandbox.volumes = None
        sandbox.platform = None
        return sandbox

    def test_tracks_runtime_service_start_for_replay(self):
        sandbox = self._make_sandbox()

        sandbox._track_runtime_command("service postgresql start")

        self.assertEqual(
            sandbox.runtime_replay_commands,
            [{"key": "postgresql", "command": "service postgresql start"}],
        )

    def test_replaces_existing_runtime_command_for_same_service(self):
        sandbox = self._make_sandbox()

        sandbox._track_runtime_command("service postgresql start")
        sandbox._track_runtime_command("service postgresql restart")

        self.assertEqual(
            sandbox.runtime_replay_commands,
            [{"key": "postgresql", "command": "service postgresql restart"}],
        )

    def test_stop_command_removes_runtime_service_from_replay_list(self):
        sandbox = self._make_sandbox()

        sandbox._track_runtime_command("service rabbitmq-server start")
        sandbox._track_runtime_command("service rabbitmq-server stop")

        self.assertEqual(sandbox.runtime_replay_commands, [])

    def test_does_not_track_persistent_setup_chains_for_runtime_replay(self):
        sandbox = self._make_sandbox()

        sandbox._track_runtime_command(
            "apt-get update && apt-get install -y postgresql && service postgresql start"
        )

        self.assertEqual(sandbox.runtime_replay_commands, [])

    def test_replay_restores_successful_runtime_commands_and_drops_failed_ones(self):
        sandbox = self._make_sandbox(
            replay_results=[
                SimpleNamespace(exit_code=0, output=b"started"),
                SimpleNamespace(exit_code=1, output=b"failed"),
            ]
        )
        sandbox.runtime_replay_commands = [
            {"key": "postgresql", "command": "service postgresql start"},
            {"key": "rabbitmq-server", "command": "service rabbitmq-server start"},
        ]

        sandbox._replay_runtime_commands()

        self.assertEqual(len(sandbox.container.calls), 2)
        self.assertEqual(
            sandbox.container.calls[0]["command"],
            ["/bin/bash", "-c", "/bin/bash -o pipefail -lc 'service postgresql start'"],
        )
        self.assertEqual(sandbox.container.calls[0]["workdir"], "/app")
        self.assertEqual(
            sandbox.container.calls[1]["command"],
            ["/bin/bash", "-c", "/bin/bash -o pipefail -lc 'service rabbitmq-server start'"],
        )
        self.assertEqual(
            sandbox.runtime_replay_commands,
            [{"key": "postgresql", "command": "service postgresql start"}],
        )

    def test_failed_command_does_not_auto_rollback_when_container_is_healthy(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=1, output=b"plain failure")],
            status="running",
        )
        original_container = sandbox.container

        success, output = sandbox.execute("false")

        self.assertFalse(success)
        self.assertEqual(output, "plain failure")
        self.assertIs(sandbox.container, original_container)
        self.assertFalse(original_container.stopped)
        self.assertFalse(original_container.removed)

    def test_failed_mutating_command_adds_prefix_rerun_or_rollback_guidance(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=1, output=b"pytorch3d not found")],
            status="running",
        )

        success, output = sandbox.execute("pip3 install pytorch3d")

        self.assertFalse(success)
        self.assertIn("FAILED SETUP MUTATION", output)
        self.assertIn("partially installed packages", output)
        self.assertIn("rerun that prefix/sub-step as its own separate Action", output)
        self.assertIn("Action: __ROLLBACK__", output)
        self.assertIn("pytorch3d not found", output)

    def test_transient_pip_install_failure_is_retried_before_reporting(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[
                SimpleNamespace(
                    exit_code=1,
                    output=b"pip._vendor.urllib3.exceptions.ReadTimeoutError: timed out",
                ),
                SimpleNamespace(exit_code=0, output=b"Successfully installed robustbench"),
            ],
            status="running",
        )
        sandbox.last_success_image = None

        success, output = sandbox.execute("python -m pip install robustbench")

        self.assertTrue(success)
        self.assertIn("Transient pip install failure on attempt 1", output)
        self.assertIn("Successfully installed robustbench", output)
        self.assertEqual(len(sandbox.container.calls), 2)

    def test_setup_replay_retries_transient_pip_failure(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[
                SimpleNamespace(
                    exit_code=1,
                    output=(
                        b"ReadTimeoutError: timed out\n"
                        b"__INSTALL_FAIL__:python3 -m pip install demo==1.0:7\n"
                    ),
                ),
                SimpleNamespace(exit_code=0, output=b"Successfully installed demo"),
            ],
            status="running",
        )

        result = sandbox.run_install_script(
            "set -Eeuo pipefail\npython3 -m pip install demo==1.0\n"
        )

        self.assertEqual(result.rc, 0)
        self.assertIsNone(result.failing_command)
        self.assertIn("Transient pip failure during setup replay", result.stderr)
        self.assertIn("Successfully installed demo", result.stderr)
        self.assertEqual(len(sandbox.container.calls), 2)

    def test_setup_replay_does_not_retry_semantic_pip_failure(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[
                SimpleNamespace(
                    exit_code=1,
                    output=(
                        b"No matching distribution found for demo==99\n"
                        b"__INSTALL_FAIL__:python3 -m pip install demo==99:7\n"
                    ),
                ),
            ],
            status="running",
        )

        result = sandbox.run_install_script(
            "set -Eeuo pipefail\npython3 -m pip install demo==99\n"
        )

        self.assertEqual(result.rc, 1)
        self.assertEqual(result.failing_command, "python3 -m pip install demo==99")
        self.assertEqual(len(sandbox.container.calls), 1)

    def test_failed_readonly_command_does_not_add_mutation_guidance(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=1, output=b"missing file")],
            status="running",
        )

        success, output = sandbox.execute("cat /app/missing-file")

        self.assertFalse(success)
        self.assertNotIn("FAILED SETUP MUTATION", output)
        self.assertEqual(output, "missing file")

    def test_explicit_rollback_restores_last_success_snapshot(self):
        sandbox = self._make_sandbox()
        original_container = FakeContainer()
        restored_container = FakeContainer()
        sandbox.container = original_container
        sandbox.client = FakeDockerClient(containers=[restored_container])

        success, output = sandbox.rollback()

        self.assertTrue(success)
        self.assertIn("Restored the container to the last successful snapshot", output)
        self.assertTrue(original_container.stopped)
        self.assertTrue(original_container.removed)
        self.assertIs(sandbox.container, restored_container)
        self.assertEqual(restored_container.calls[0]["command"], "mkdir -p /app")

    def test_failed_command_forces_recovery_when_container_is_unhealthy(self):
        sandbox = self._make_sandbox()
        original_container = FakeContainer(
            results=[SimpleNamespace(exit_code=1, output=b"broken state")],
            status="exited",
        )
        restored_container = FakeContainer()
        sandbox.container = original_container
        sandbox.client = FakeDockerClient(containers=[restored_container])

        success, output = sandbox.execute("failing-command")

        self.assertFalse(success)
        self.assertIn("automatically", output)
        self.assertTrue(original_container.stopped)
        self.assertTrue(original_container.removed)
        self.assertIs(sandbox.container, restored_container)

    def test_package_manager_broken_state_failure_adds_rollback_candidate_hint(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[
                SimpleNamespace(
                    exit_code=100,
                    output=(
                        b"You might want to run 'apt --fix-broken install' to correct these.\n"
                        b"The following packages have unmet dependencies:\n"
                        b"postgresql-16 : Depends: postgresql-common but it is not going to be installed\n"
                    ),
                )
            ],
            status="running",
        )

        success, output = sandbox.execute(
            "apt-get update && apt-get install -y postgresql-16 postgresql-client-16 --fix-missing"
        )

        self.assertFalse(success)
        self.assertIn("ROLLBACK CANDIDATE", output)
        self.assertIn("Action: __ROLLBACK__", output)
        self.assertEqual(sandbox.package_manager_broken_failure_streak, 1)

    def test_test_error_count_adds_no_excuses_failure_prefix(self):
        sandbox = self._make_sandbox()

        prefix = sandbox._get_test_failure_prefix(
            1,
            "collected 32 items\n==================== 32 passed, 1 error in 0.31s ====================",
        )

        self.assertIn("TEST FAILURE DETECTED", prefix)
        self.assertIn("Repo2Run-style pytest collection", prefix)
        self.assertIn("collection/import/config errors", prefix)

    def test_zero_failed_summary_does_not_add_no_excuses_failure_prefix(self):
        sandbox = self._make_sandbox()

        prefix = sandbox._get_test_failure_prefix(
            1,
            "Tests run: 10, Failures: 0, Errors: 0, Skipped: 0",
        )

        self.assertEqual(prefix, "")

    def test_zero_failed_ctest_summary_does_not_add_no_excuses_failure_prefix(self):
        sandbox = self._make_sandbox()

        prefix = sandbox._get_test_failure_prefix(
            1,
            "\n".join(
                [
                    "Passed:                           660",
                    "Failed:                             0",
                    "Unexpected successes:               0",
                    "[100%] Built target test",
                ]
            ),
        )

        self.assertEqual(prefix, "")

    def test_repeated_package_manager_recovery_failures_upgrade_hint_strength(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[
                SimpleNamespace(
                    exit_code=100,
                    output=(
                        b"Correcting dependencies... Done\n"
                        b"E: Unable to fetch some archives\n"
                    ),
                ),
                SimpleNamespace(
                    exit_code=100,
                    output=(
                        b"Correcting dependencies... Done\n"
                        b"The following packages have unmet dependencies:\n"
                        b"libpq5 : Depends: something but it is not going to be installed\n"
                    ),
                ),
            ],
            status="running",
        )

        first_success, first_output = sandbox.execute("apt --fix-broken install -y")
        second_success, second_output = sandbox.execute("apt --fix-broken install -y")

        self.assertFalse(first_success)
        self.assertIn("ROLLBACK CANDIDATE", first_output)
        self.assertFalse(second_success)
        self.assertIn("STRONG ROLLBACK CANDIDATE", second_output)
        self.assertEqual(sandbox.package_manager_broken_failure_streak, 2)

    def test_truncated_test_output_is_rejected_before_execution(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[
                SimpleNamespace(
                    exit_code=0,
                    output=b"collected 207 items\n207 passed in 47.25s\n",
                )
            ],
            status="running",
        )

        success, output = sandbox.execute(
            "cd /app && python -m pytest tests -v 2>&1 | head -100"
        )

        self.assertFalse(success)
        self.assertIn("COMMAND REJECTED BEFORE EXECUTION", output)
        self.assertIn("was NOT executed", output)
        self.assertEqual(sandbox.container.calls, [])

    def test_setup_output_filter_is_rejected_before_execution(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=0, output=b"installed")],
            status="running",
        )

        success, output = sandbox.execute("pip3 install pybullet 2>&1 | tail -20")

        self.assertFalse(success)
        self.assertIn("COMMAND REJECTED BEFORE EXECUTION", output)
        self.assertIn("must not pipe output through `head`, `tail`, or `grep`", output)
        self.assertEqual(sandbox.container.calls, [])

    def test_readonly_output_filter_is_allowed(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=0, output=b"setup.py\n")],
            status="running",
        )

        success, output = sandbox.execute("find /app -name setup.py 2>/dev/null | head -20")

        self.assertTrue(success)
        self.assertEqual(output, "setup.py\n")
        self.assertEqual(len(sandbox.container.calls), 1)

    def test_compound_setup_mutations_are_rejected_before_execution(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=0, output=b"installed")],
            status="running",
        )

        success, output = sandbox.execute("pip3 install pybullet && pip3 install pytorch3d")

        self.assertFalse(success)
        self.assertIn("multiple independent setup mutations", output)
        self.assertIn("environment was not changed", output)
        self.assertEqual(sandbox.container.calls, [])

    def test_mutation_plus_probe_is_rejected_before_execution(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=0, output=b"ok")],
            status="running",
        )

        success, output = sandbox.execute("pip3 install pybullet && python -c 'import pybullet'")

        self.assertFalse(success)
        self.assertIn("setup mutation with a verification, probe, or read-only check", output)
        self.assertEqual(sandbox.container.calls, [])

    def test_navigation_then_single_setup_mutation_is_allowed(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=0, output=b"installed")],
            status="running",
        )
        sandbox.last_success_image = None

        success, output = sandbox.execute("cd /app && pip3 install -e .")

        self.assertTrue(success)
        self.assertEqual(output, "installed")
        self.assertEqual(len(sandbox.container.calls), 1)

    def test_apt_update_install_chain_is_allowed(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=0, output=b"installed cmake")],
            status="running",
        )
        sandbox.last_success_image = None

        success, output = sandbox.execute("apt-get update && apt-get install -y cmake")

        self.assertTrue(success)
        self.assertEqual(output, "installed cmake")
        self.assertEqual(len(sandbox.container.calls), 1)

    def test_command_wrapper_uses_pipefail_without_timeout(self):
        sandbox = self._make_sandbox()
        sandbox.command_timeout_seconds = None

        wrapped = sandbox._wrap_command_with_timeout("pip3 install missing-package | tail -20")

        self.assertIn("/bin/bash -o pipefail -lc", wrapped)

    def test_command_wrapper_uses_pipefail_with_timeout(self):
        sandbox = self._make_sandbox()
        sandbox.command_timeout_seconds = 42

        wrapped = sandbox._wrap_command_with_timeout("pip3 install missing-package | tail -20")

        self.assertIn("timeout --version", wrapped)
        self.assertIn("grep -q 'GNU coreutils'", wrapped)
        self.assertIn("timeout --foreground --kill-after=30s 42s /bin/bash -o pipefail -lc", wrapped)


class SandboxAptBootstrapTests(unittest.TestCase):
    def _make_sandbox(self, mirror_url=None):
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.workdir = "/app"
        sandbox.container = FakeContainer()
        sandbox.apt_mirror_url = mirror_url
        sandbox.apt_retries = 5
        sandbox.apt_http_timeout_seconds = 120
        sandbox.apt_https_timeout_seconds = 120
        return sandbox

    def test_build_apt_bootstrap_command_always_configures_retry_settings(self):
        sandbox = self._make_sandbox()

        command = sandbox._build_apt_bootstrap_command()

        self.assertIn('Acquire::Retries "5";', command)
        self.assertIn('Acquire::http::Timeout "120";', command)
        self.assertIn('Acquire::https::Timeout "120";', command)
        self.assertNotIn("apt-get update\n", command)

    def test_build_apt_bootstrap_command_rewrites_sources_when_mirror_is_configured(self):
        sandbox = self._make_sandbox("https://mirror.example.com/ubuntu")

        command = sandbox._build_apt_bootstrap_command()

        self.assertIn("APT_MIRROR_URL=https://mirror.example.com/ubuntu", command)
        self.assertIn('sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g"', command)
        self.assertIn('sed -i "s|http://security.ubuntu.com/ubuntu|${APT_MIRROR_URL}|g"', command)
        self.assertIn("apt-get update", command)

    def test_bootstrap_apt_executes_once_on_container(self):
        sandbox = self._make_sandbox("https://mirror.example.com/ubuntu")

        sandbox._bootstrap_apt_if_supported()

        self.assertEqual(len(sandbox.container.calls), 1)
        self.assertEqual(sandbox.container.calls[0]["command"][0:2], ["/bin/bash", "-lc"])
        self.assertIn("99jayint-retries", sandbox.container.calls[0]["command"][2])

    def test_ensure_bash_bootstraps_through_posix_shell(self):
        sandbox = self._make_sandbox()

        sandbox._ensure_bash_available()

        command = sandbox.container.calls[0]["command"]
        self.assertEqual(command[0:2], ["/bin/sh", "-lc"])
        self.assertIn("apk add --no-cache bash coreutils", command[2])
        self.assertIn(
            "apt-get install -y --no-install-recommends bash coreutils",
            command[2],
        )
        self.assertIn("timeout --version", command[2])

    def test_ensure_bash_fails_clearly_when_bootstrap_fails(self):
        sandbox = self._make_sandbox()
        sandbox.container = FakeContainer(
            results=[SimpleNamespace(exit_code=127, output=b"no package manager")]
        )

        with self.assertRaisesRegex(RuntimeError, "automatic installation failed"):
            sandbox._ensure_bash_available()

    def test_resolve_apt_mirror_url_prefers_explicit_value(self):
        sandbox = Sandbox.__new__(Sandbox)

        resolved = sandbox._resolve_apt_mirror_url("https://mirror.example.com/ubuntu/")

        self.assertEqual(resolved, "https://mirror.example.com/ubuntu")

    @mock.patch("src.sandbox.docker.from_env")
    def test_init_uses_extended_docker_client_timeout(self, mock_from_env):
        fake_container = FakeContainer()
        fake_client = FakeDockerClient(containers=[fake_container])
        mock_from_env.return_value = fake_client

        sandbox = Sandbox(
            base_image="ubuntu:22.04",
            seed_dir=None,
            docker_client_timeout_seconds=600,
        )

        self.assertIs(sandbox.client, fake_client)
        mock_from_env.assert_called_once_with(timeout=600)
        self.assertEqual(fake_client.containers.run_calls[0]["kwargs"]["command"], "/bin/sh")

    def test_extra_hosts_added_when_arm_on(self):
        import os
        orig = os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION")
        os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] = "1"
        try:
            captured = {}
            fake_container = FakeContainer()

            class _Containers:
                def run(self, image, **kwargs):
                    captured.update(kwargs)
                    return fake_container

            class _Client:
                containers = _Containers()

            import src.sandbox as sb
            s = sb.Sandbox.__new__(sb.Sandbox)
            s.client = _Client()
            s.current_image = "python:3.11-slim"
            s.platform = None
            s.workdir = "/app"
            s.volumes = {}
            s.seed_dir = None
            s._bootstrap_apt_if_supported = lambda: None
            s._register_snapshot = lambda *a: None
            s._setup_initial_container()
            self.assertEqual(captured.get("extra_hosts"), {"postgres": "127.0.0.1"})
        finally:
            if orig is None:
                os.environ.pop("DOCKERAGENT_ENABLE_SERVICE_PROVISION", None)
            else:
                os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] = orig

    def test_container_environment_is_forwarded_to_initial_container(self):
        captured = {}
        fake_container = FakeContainer()

        class _Containers:
            def run(self, image, **kwargs):
                captured.update(kwargs)
                return fake_container

        class _Client:
            containers = _Containers()

        s = Sandbox.__new__(Sandbox)
        s.client = _Client()
        s.current_image = "python:3.12-slim"
        s.platform = None
        s.workdir = "/app"
        s.volumes = {}
        s.seed_dir = None
        s.environment = {"PYTEST_ADDOPTS": "--import-mode=importlib"}
        s._bootstrap_apt_if_supported = lambda: None
        s._register_snapshot = lambda *a: None
        s._setup_initial_container()

        self.assertEqual(
            captured.get("environment"),
            {"PYTEST_ADDOPTS": "--import-mode=importlib"},
        )


class SandboxNamedCheckpointTests(unittest.TestCase):
    def test_daemon_native_platform_normalizes_architecture(self):
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(
            info=lambda: {"OSType": "linux", "Architecture": "aarch64"}
        )
        self.assertEqual(sandbox._daemon_native_platform(), "linux/arm64")

        sandbox.client = SimpleNamespace(
            info=lambda: {"OSType": "linux", "Architecture": "x86_64"}
        )
        self.assertEqual(sandbox._daemon_native_platform(), "linux/amd64")

    def test_daemon_native_platform_degrades_when_info_unavailable(self):
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(
            info=lambda: (_ for _ in ()).throw(RuntimeError("offline"))
        )
        self.assertIsNone(sandbox._daemon_native_platform())

    def test_local_platform_image_skips_registry_pull(self):
        image = SimpleNamespace(attrs={"Os": "linux", "Architecture": "amd64"})
        images = SimpleNamespace(get=lambda _tag: image)
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(images=images)
        sandbox.current_image = "python:3.11-slim"
        sandbox.platform = "linux/amd64"

        self.assertTrue(sandbox._local_image_matches_platform())

    def test_local_platform_image_detects_arch_mismatch(self):
        image = SimpleNamespace(attrs={"Os": "linux", "Architecture": "arm64"})
        images = SimpleNamespace(get=lambda _tag: image)
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(images=images)
        sandbox.current_image = "python:3.11-slim"
        sandbox.platform = "linux/amd64"

        self.assertFalse(sandbox._local_image_matches_platform())

    def test_platform_image_ref_pins_reused_image_id(self):
        calls = []
        image = SimpleNamespace(
            id="sha256:native", attrs={"Os": "linux", "Architecture": "arm64"}
        )
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(images=SimpleNamespace(
            get=lambda tag: calls.append(tag) or image
        ))
        sandbox.current_image = "python:3.11-slim"
        sandbox.platform = "linux/arm64"
        self.assertEqual(sandbox._resolved_platform_image_ref(), "sha256:native")
        self.assertEqual(calls, ["python:3.11-slim"])

    def test_platform_image_ref_uses_pulled_image_id_on_mismatch(self):
        old = SimpleNamespace(
            id="sha256:old", attrs={"Os": "linux", "Architecture": "amd64"}
        )
        pulled = SimpleNamespace(
            id="sha256:pulled", attrs={"Os": "linux", "Architecture": "arm64"}
        )
        images = SimpleNamespace(
            get=lambda _tag: old,
            pull=lambda _tag, platform: pulled,
        )
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(images=images)
        sandbox.current_image = "python:3.11-slim"
        sandbox.platform = "linux/arm64"
        self.assertEqual(sandbox._resolved_platform_image_ref(), "sha256:pulled")

    def test_platform_image_ref_rejects_wrong_arch_fallback_after_pull_failure(self):
        old = SimpleNamespace(
            id="sha256:old", attrs={"Os": "linux", "Architecture": "amd64"}
        )
        images = SimpleNamespace(
            get=lambda _tag: old,
            pull=lambda _tag, platform: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(images=images)
        sandbox.current_image = "python:3.11-slim"
        sandbox.platform = "linux/arm64"

        with self.assertRaisesRegex(RuntimeError, "does not match linux/arm64"):
            sandbox._resolved_platform_image_ref()

    def test_stable_local_alias_is_derived_from_image_id_and_platform(self):
        tagged = []
        image = SimpleNamespace(
            id="sha256:1234567890abcdefcafebabe",
            tag=lambda repository, tag: tagged.append((repository, tag)) or True,
        )
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(images=SimpleNamespace(get=lambda _ref: image))
        sandbox.platform = "linux/arm64"

        alias = sandbox._stable_local_image_alias("sha256:1234567890abcdefcafebabe")

        self.assertEqual(alias, "jayint-v3-base:1234567890abcdef-linux-arm64")
        self.assertEqual(
            tagged, [("jayint-v3-base", "1234567890abcdef-linux-arm64")]
        )

    def test_rolling_snapshot_cleanup_cannot_delete_named_checkpoint(self):
        removed = []

        class _Images:
            def get(self, image_id):
                return SimpleNamespace(id=image_id)

            def remove(self, image_id, force=False):
                removed.append((image_id, force))

        sandbox = Sandbox.__new__(Sandbox)
        sandbox.client = SimpleNamespace(images=_Images())
        sandbox.snapshot_image_ids = {"checkpoint-image"}
        sandbox.named_checkpoints = {"exec-1": "checkpoint-image"}

        sandbox._remove_snapshot_image("checkpoint-image")
        self.assertEqual(removed, [])
        self.assertIn("checkpoint-image", sandbox.snapshot_image_ids)

        sandbox._remove_snapshot_image("checkpoint-image", force_named=True)
        self.assertEqual(removed, [("checkpoint-image", True)])
        self.assertNotIn("checkpoint-image", sandbox.snapshot_image_ids)

    def test_restore_named_checkpoint_does_not_replay_runtime_services(self):
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.named_checkpoints = {"base": "base-image", "exec-1": "checkpoint-image"}
        calls = []
        sandbox._replace_container_from_image = (
            lambda image, *, replay_runtime: calls.append((image, replay_runtime))
        )

        sandbox.restore_checkpoint("exec-1")

        self.assertEqual(calls, [("checkpoint-image", False)])
        self.assertEqual(sandbox.current_image, "checkpoint-image")
        self.assertEqual(sandbox.last_success_image, "checkpoint-image")

    def test_create_checkpoint_does_not_rotate_last_success_snapshot(self):
        sandbox = Sandbox.__new__(Sandbox)
        sandbox.container = SimpleNamespace(
            commit=lambda: SimpleNamespace(id="new-checkpoint-image")
        )
        sandbox.snapshot_image_ids = set()
        sandbox.named_checkpoints = {"base": "base-image"}
        sandbox.last_success_image = "rolling-image"

        name = sandbox.create_checkpoint("exec-1")

        self.assertEqual(name, "exec-1")
        self.assertEqual(sandbox.named_checkpoints["exec-1"], "new-checkpoint-image")
        self.assertEqual(sandbox.last_success_image, "rolling-image")
        self.assertIn("new-checkpoint-image", sandbox.snapshot_image_ids)

    def test_no_extra_hosts_off_arm(self):
        import os
        orig = os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION")
        os.environ.pop("DOCKERAGENT_ENABLE_SERVICE_PROVISION", None)
        try:
            captured = {}
            fake_container = FakeContainer()

            class _Containers:
                def run(self, image, **kwargs):
                    captured.update(kwargs)
                    return fake_container

            class _Client:
                containers = _Containers()

            import src.sandbox as sb
            s = sb.Sandbox.__new__(sb.Sandbox)
            s.client = _Client()
            s.current_image = "python:3.11-slim"
            s.platform = None
            s.workdir = "/app"
            s.volumes = {}
            s.seed_dir = None
            s._bootstrap_apt_if_supported = lambda: None
            s._register_snapshot = lambda *a: None
            s._setup_initial_container()
            self.assertNotIn("extra_hosts", captured)
        finally:
            if orig is None:
                os.environ.pop("DOCKERAGENT_ENABLE_SERVICE_PROVISION", None)
            else:
                os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] = orig


if __name__ == "__main__":
    unittest.main()
