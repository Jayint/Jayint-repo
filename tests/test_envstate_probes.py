import unittest

from src.envstate.probes import (
    ProbeResult,
    ProbeSpec,
    build_probe_command,
    evaluate_probe,
    run_probe,
)


class FakeExecutor:
    """Mimics Sandbox.exec_readonly: callable(command) -> (rc, output)."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        return self.mapping.get(command, (127, "command not found"))


class ProbeCommandTests(unittest.TestCase):
    def test_cli_probe_command(self):
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="path exists and version prints")
        self.assertEqual(
            build_probe_command(spec),
            "command -v pg_config && pg_config --version",
        )

    def test_python_import_probe_command(self):
        spec = ProbeSpec(kind="python_import", name="psycopg2", predicate="imports and has __version__")
        self.assertIn("import psycopg2", build_probe_command(spec))

    def test_pkg_config_probe_command(self):
        spec = ProbeSpec(kind="pkg_config", name="libpq", predicate="module known to pkg-config")
        self.assertEqual(build_probe_command(spec), "pkg-config --exists libpq && pkg-config --modversion libpq")

    def test_header_probe_command(self):
        spec = ProbeSpec(kind="header", name="libpq-fe.h", predicate="header on default search path")
        self.assertIn("libpq-fe.h", build_probe_command(spec))


class ProbeEvaluationTests(unittest.TestCase):
    def test_passes_on_rc_zero(self):
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="x")
        self.assertTrue(evaluate_probe(spec, rc=0, stdout="/usr/bin/pg_config\n10.1"))

    def test_fails_on_nonzero_rc(self):
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="x")
        self.assertFalse(evaluate_probe(spec, rc=1, stdout=""))


class RunProbeTests(unittest.TestCase):
    def test_run_probe_returns_result_with_revision_and_container(self):
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="path exists")
        cmd = build_probe_command(spec)
        executor = FakeExecutor({cmd: (0, "/usr/bin/pg_config\n10.1")})
        result = run_probe(executor, spec, env_revision=8, container_id="abc123")
        self.assertIsInstance(result, ProbeResult)
        self.assertTrue(result.passed)
        self.assertEqual(result.env_revision, 8)
        self.assertEqual(result.container_id, "abc123")


from src.envstate.types import BaseFacts, EnvStateSnapshot, Requirement, Source, Status
from src.envstate.probes import certify_probe_result


class CertifyProbeResultTests(unittest.TestCase):
    def test_passing_probe_certifies_present_via_acl(self):
        snap = EnvStateSnapshot(
            revision=8, container_id="abc123", base=BaseFacts(image="python:3.11-slim"),
            requirements=(Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                                      status=Status.REQUIRED, source=Source.LLM_GUESS),),
        )
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="path exists")
        result = run_probe(FakeExecutor({build_probe_command(spec): (0, "/usr/bin/pg_config")}),
                           spec, env_revision=8, container_id="abc123")
        updated = certify_probe_result(snap, "tool:pg_config", result)
        req = [r for r in updated.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.PRESENT)
        self.assertEqual(req.source, Source.PROBE)

    def test_failing_probe_certifies_missing(self):
        snap = EnvStateSnapshot(
            revision=8, container_id="abc123", base=BaseFacts(image="python:3.11-slim"),
            requirements=(Requirement(id="tool:pg_config", name="pg_config", kind="Tool",
                                      status=Status.REQUIRED, source=Source.LLM_GUESS),),
        )
        spec = ProbeSpec(kind="cli", name="pg_config", predicate="path exists")
        result = run_probe(FakeExecutor({}), spec, env_revision=8, container_id="abc123")
        updated = certify_probe_result(snap, "tool:pg_config", result)
        req = [r for r in updated.requirements if r.id == "tool:pg_config"][0]
        self.assertEqual(req.status, Status.MISSING)
