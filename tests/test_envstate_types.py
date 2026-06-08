import unittest

from src.envstate.types import (
    BaseFacts,
    EnvStateSnapshot,
    Evidence,
    OpenFailure,
    ProviderFact,
    Requirement,
    Source,
    Status,
)
from src.envstate.serde import snapshot_to_dict, snapshot_from_dict


def _sample_snapshot():
    return EnvStateSnapshot(
        revision=8,
        container_id="abc123",
        base=BaseFacts(image="python:3.11-slim", distro="debian", arch="amd64", python="3.11.9"),
        requirements=(
            Requirement(
                id="lang:psycopg2==2.8.6",
                name="psycopg2",
                kind="LanguagePackage",
                status=Status.REQUIRED,
                source=Source.STATIC_SCAN,
                specifier="==2.8.6",
                required_by=("requirements.txt",),
            ),
            Requirement(
                id="tool:pg_config",
                name="pg_config",
                kind="Tool",
                status=Status.PRESENT,
                source=Source.PROBE,
                required_by=("lang:psycopg2==2.8.6",),
                evidence=Evidence(
                    probe_cmd="command -v pg_config && pg_config --version",
                    rc=0,
                    stdout_predicate="path exists and version prints",
                    env_revision=8,
                    container_id="abc123",
                ),
            ),
        ),
        provider_facts=(
            ProviderFact(
                provider="apt:libpq-dev",
                provides=("tool:pg_config", "header:libpq-fe.h"),
                source=Source.DIAGNOSE,
                diagnose_cmd="apt-file search bin/pg_config",
            ),
        ),
        open_failures=(
            OpenFailure(
                signature="pg_config executable not found",
                first_seen_revision=7,
                last_seen_revision=7,
                hypothesis="psycopg2 source build requires PostgreSQL dev tooling",
            ),
        ),
        plan_notes=("Do not substitute psycopg2-binary for pinned psycopg2.",),
    )


class EnvStateSerdeTests(unittest.TestCase):
    def test_round_trips_through_dict(self):
        snapshot = _sample_snapshot()
        restored = snapshot_from_dict(snapshot_to_dict(snapshot))
        self.assertEqual(restored, snapshot)

    def test_to_dict_matches_design_shape(self):
        data = snapshot_to_dict(_sample_snapshot())
        self.assertEqual(data["revision"], 8)
        self.assertEqual(data["base"]["python"], "3.11.9")
        present = [r for r in data["requirements"] if r["status"] == "PRESENT"][0]
        self.assertEqual(present["evidence"]["rc"], 0)
        self.assertEqual(present["evidence"]["env_revision"], 8)

    def test_snapshot_is_immutable(self):
        snapshot = _sample_snapshot()
        with self.assertRaises(Exception):
            snapshot.revision = 9  # frozen dataclass -> FrozenInstanceError
