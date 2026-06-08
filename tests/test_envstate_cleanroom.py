import tempfile
import unittest

from src.envstate.cleanroom import CleanroomResult, ensure_repo_in_dockerfile, verify_cleanroom
from src.envstate.probes import ProbeSpec


class FakeImages:
    def __init__(self, build_ok=True):
        self.build_ok = build_ok
        self.built = []

    def build(self, **kwargs):
        self.built.append(kwargs)
        if not self.build_ok:
            raise RuntimeError("build failed")
        return ("image-id", iter([]))


class FakeDockerClient:
    def __init__(self, build_ok=True):
        self.images = FakeImages(build_ok=build_ok)


class CleanroomTests(unittest.TestCase):
    def test_success_when_build_probes_and_tests_pass(self):
        client = FakeDockerClient(build_ok=True)
        result = verify_cleanroom(
            client, dockerfile_text="FROM python:3.11-slim\nCOPY . /app\n",
            build_context_dir=tempfile.mkdtemp(),
            probes=[ProbeSpec(kind="cli", name="pg_config", predicate="path exists")],
            test_commands=["pytest -q"],
            run_command=lambda image, cmd: (0, "ok"),
        )
        self.assertIsInstance(result, CleanroomResult)
        self.assertTrue(result.passed)
        # built with a context path (so COPY works), not fileobj
        self.assertIn("path", client.images.built[0])

    def test_failure_when_build_fails(self):
        client = FakeDockerClient(build_ok=False)
        result = verify_cleanroom(
            client, dockerfile_text="FROM bad\n", build_context_dir=tempfile.mkdtemp(),
            probes=[], test_commands=["pytest -q"], run_command=lambda image, cmd: (0, "ok"),
        )
        self.assertFalse(result.passed)
        self.assertIn("build", result.reason.lower())

    def test_failure_when_probe_regresses_in_clean_image(self):
        client = FakeDockerClient(build_ok=True)
        result = verify_cleanroom(
            client, dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probes=[ProbeSpec(kind="cli", name="pg_config", predicate="path exists")],
            test_commands=["pytest -q"],
            run_command=lambda image, cmd: (1, "not found"),  # probe fails in clean image
        )
        self.assertFalse(result.passed)

    def test_failure_when_test_fails_in_clean_image(self):
        client = FakeDockerClient(build_ok=True)
        result = verify_cleanroom(
            client, dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probes=[], test_commands=["pytest -q"],
            run_command=lambda image, cmd: (1, "tests failed"),
        )
        self.assertFalse(result.passed)
        self.assertIn("test", result.reason.lower())
        self.assertEqual(result.failed_tests, ("pytest -q",))

    def test_fails_when_nothing_to_verify(self):
        client = FakeDockerClient(build_ok=True)
        result = verify_cleanroom(
            client, dockerfile_text="FROM python:3.11-slim\n",
            build_context_dir=tempfile.mkdtemp(),
            probes=[], test_commands=[],
            run_command=lambda image, cmd: (0, "ok"),
        )
        self.assertFalse(result.passed)

    def test_replays_explicit_probe_command_verbatim(self):
        # A probe carrying an explicit command (as the agent passes req.evidence.probe_cmd)
        # must be re-run verbatim, NOT reconstructed as `command -v <name>`.
        client = FakeDockerClient(build_ok=True)
        seen = []
        def run_command(image, cmd):
            seen.append(cmd)
            return (0, "ok")
        verify_cleanroom(
            client, dockerfile_text="FROM x\n", build_context_dir=tempfile.mkdtemp(),
            probes=[ProbeSpec(kind="header", name="libpq-fe.h", predicate="p",
                              command="find /usr/include -name 'libpq-fe.h' | grep -q .")],
            test_commands=["pytest -q"],
            run_command=run_command,
        )
        self.assertIn("find /usr/include -name 'libpq-fe.h' | grep -q .", seen)


class EnsureRepoInDockerfileTests(unittest.TestCase):
    def test_inserts_copy_after_workdir(self):
        text = "FROM python:3.11-slim\nWORKDIR /app\n\nRUN pip install -e .\n"
        out = ensure_repo_in_dockerfile(text, "/app")
        lines = out.splitlines()
        self.assertEqual(lines[1], "WORKDIR /app")
        self.assertEqual(lines[2], "COPY . /app")          # injected right after WORKDIR
        self.assertTrue(lines.index("COPY . /app") < lines.index("RUN pip install -e ."))  # before RUN

    def test_is_idempotent_when_copy_present(self):
        text = "FROM x\nWORKDIR /app\nCOPY . /app\nRUN true\n"
        self.assertEqual(ensure_repo_in_dockerfile(text, "/app"), text)

    def test_appends_when_no_workdir(self):
        text = "FROM x\nRUN true\n"
        out = ensure_repo_in_dockerfile(text, "/srv")
        self.assertIn("COPY . /srv", out.splitlines())

    def test_uses_given_workdir(self):
        out = ensure_repo_in_dockerfile("FROM x\nWORKDIR /srv\n", "/srv")
        self.assertIn("COPY . /srv", out.splitlines())
