"""TDD tests for scripts/eval/graph_fidelity/oracle.py — the held-out-recipe
grader/oracle (§2, §3 item 1 of the graph-fidelity eval loop doc).

oracle.py IS the grader: it is SUPPOSED to parse Dockerfile / CI workflow /
docker-compose files (the held-out human recipe). The LEAKAGE INVARIANT (§2)
binds *construction*, not this module — these tests exercise reading those
files directly, on purpose.

Fixtures live under fixtures/oracle/<case>/ as hand-crafted mini repos with a
known-answer recipe, so grading needs no network and no real repo checkout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.eval.graph_fidelity.oracle import OracleResult, parse_oracle

_FIXTURES = Path(__file__).parent / "fixtures" / "oracle"

_ALL_TIER_KEYS = {"SYSTEM_LIB", "RUNTIME", "PACKAGE", "SERVICE", "CONFIG"}


# ---------------------------------------------------------------------------
# Dockerfile: apt list parsing (multi-line `\` continuations + `&&` chains),
# FROM python:X.Y* -> minor, pip pins.
# ---------------------------------------------------------------------------

class TestDockerfileFullRecipe:
    def test_apt_packages_survive_continuation_and_chain_and_version_pin(self):
        result = parse_oracle(_FIXTURES / "dockerfile_full")
        assert result.declared_by_tier["SYSTEM_LIB"] == ("curl", "gcc", "libpq-dev")

    def test_from_python_patch_and_suffix_normalizes_to_major_minor(self):
        result = parse_oracle(_FIXTURES / "dockerfile_full")
        assert result.declared_by_tier["RUNTIME"] == ("3.11",)

    def test_pip_pins_captured_requirements_file_and_editable_dot_excluded(self):
        result = parse_oracle(_FIXTURES / "dockerfile_full")
        packages = set(result.declared_by_tier["PACKAGE"])
        assert packages == {"requests==2.31.0", "flask>=2.0,<3.0"}
        assert not any("requirements" in p for p in packages)
        assert "." not in packages

    def test_source_is_dockerfile_only(self):
        result = parse_oracle(_FIXTURES / "dockerfile_full")
        assert result.source == ("Dockerfile",)
        assert result.held_out is True


class TestDockerfileRuntimeSlimLiteral:
    def test_from_python_3_11_slim_normalizes_to_3_11(self):
        # The literal example from the loop doc: FROM python:3.11-slim -> "3.11".
        result = parse_oracle(_FIXTURES / "dockerfile_runtime_slim")
        assert result.declared_by_tier["RUNTIME"] == ("3.11",)
        assert result.declared_by_tier["SYSTEM_LIB"] == ()
        assert result.declared_by_tier["PACKAGE"] == ()


# ---------------------------------------------------------------------------
# CI workflow: apt install in run steps, actions/setup-python matrix
# expansion, pip install.
# ---------------------------------------------------------------------------

class TestCiWorkflow:
    def test_apt_install_in_run_step_captured(self):
        result = parse_oracle(_FIXTURES / "ci_workflow")
        assert set(result.declared_by_tier["SYSTEM_LIB"]) == {"libgl1", "libglib2.0-0"}

    def test_setup_python_matrix_expands_to_all_versions(self):
        result = parse_oracle(_FIXTURES / "ci_workflow")
        assert set(result.declared_by_tier["RUNTIME"]) == {"3.9", "3.10", "3.11"}

    def test_pip_install_captured_requirements_file_excluded(self):
        result = parse_oracle(_FIXTURES / "ci_workflow")
        assert result.declared_by_tier["PACKAGE"] == ("pytest==7.4.0",)

    def test_source_is_ci_only(self):
        result = parse_oracle(_FIXTURES / "ci_workflow")
        assert result.source == ("ci",)


# ---------------------------------------------------------------------------
# docker-compose: postgres service -> SERVICE, its env -> CONFIG.
# ---------------------------------------------------------------------------

class TestComposePostgres:
    def test_postgres_image_maps_to_service_kind(self):
        result = parse_oracle(_FIXTURES / "compose_postgres")
        assert result.declared_by_tier["SERVICE"] == ("postgres",)

    def test_service_environment_keys_map_to_config(self):
        result = parse_oracle(_FIXTURES / "compose_postgres")
        assert set(result.declared_by_tier["CONFIG"]) == {
            "DATABASE_URL", "DEBUG", "POSTGRES_PASSWORD", "POSTGRES_DB",
        }

    def test_source_is_compose_only(self):
        result = parse_oracle(_FIXTURES / "compose_postgres")
        assert result.source == ("compose",)


# ---------------------------------------------------------------------------
# Empty repo -> empty, conservative result.
# ---------------------------------------------------------------------------

class TestEmptyRepo:
    def test_empty_repo_gives_empty_result_on_every_tier(self):
        result = parse_oracle(_FIXTURES / "empty_repo")
        assert result.source == ()
        assert result.held_out is True
        for tier_values in result.declared_by_tier.values():
            assert tier_values == ()

    def test_nonexistent_directory_does_not_raise(self, tmp_path):
        result = parse_oracle(tmp_path / "does-not-exist")
        assert result.source == ()
        for tier_values in result.declared_by_tier.values():
            assert tier_values == ()


# ---------------------------------------------------------------------------
# Schema shape: always all 5 tier keys, reused from schema.NodeType (§7 —
# reuse the repo's tier vocabulary, don't invent new tier names).
# ---------------------------------------------------------------------------

class TestDeclaredByTierShape:
    def test_all_five_tier_keys_always_present(self):
        result = parse_oracle(_FIXTURES / "empty_repo")
        assert set(result.declared_by_tier.keys()) == _ALL_TIER_KEYS

    def test_tier_keys_match_schema_nodetype_names(self):
        sys.path.insert(0, str(_REPO_ROOT / "src"))
        from python_deps.depgraph.schema import NodeType

        result = parse_oracle(_FIXTURES / "empty_repo")
        for key in result.declared_by_tier:
            assert key in {member.name for member in NodeType}


class TestOracleResultToDict:
    def test_to_dict_is_json_serializable_and_matches_scorecard_schema(self):
        result = parse_oracle(_FIXTURES / "compose_postgres")
        blob = json.dumps(result.to_dict())
        parsed = json.loads(blob)
        assert parsed["held_out"] is True
        assert parsed["source"] == ["compose"]
        assert "postgres" in parsed["declared_by_tier"]["SERVICE"]

    def test_is_pure_dataclass_instance(self):
        result = parse_oracle(_FIXTURES / "dockerfile_full")
        assert isinstance(result, OracleResult)
