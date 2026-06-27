"""
Tests for arm plumbing in run_rat_benchmark.py.

Covers:
- _apply_arm_env("v1gsps") sets SERVICE_PROVISION, GRAPH_SCHEDULER, RUNTIME_PIN all to "1"
- _apply_arm_env("v1gsp") does NOT set SERVICE_PROVISION (regression guard)
- child re-detection: SERVICE_PROVISION=1 maps to "v1gsps" (not mis-detected as "v1gsp")

The module has top-level imports from eval.common (RAT repo) that are not
present in this environment.  We stub those out via sys.modules before
importing run_rat_benchmark so the _apply_arm_env helper can be tested
without any external infrastructure.
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# ── stub out external RAT-repo imports BEFORE importing run_rat_benchmark ─────
_RAT_STUBS = [
    "eval",
    "eval.common",
    "eval.common.scorers",
    "eval.models",
    "eval.models.dockeragent_model",
    "eval.models.rat_model",
    "eval.models.repo2run_model",
    "dotenv",
]

for _mod in _RAT_STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Stub specific names that run_rat_benchmark imports from eval.common.scorers
_scorers_mock = sys.modules["eval.common.scorers"]
_scorers_mock.success_scorer = MagicMock()
_scorers_mock.pytest_pass_rate_scorer = MagicMock()
_scorers_mock.pytest_collect_scorer = MagicMock()

# Stub DockerAgentModel
_da_mock = sys.modules["eval.models.dockeragent_model"]
_da_mock.DockerAgentModel = MagicMock()

# Stub load_dotenv (from dotenv import load_dotenv)
sys.modules["dotenv"].load_dotenv = MagicMock()

# ── import the module under test ──────────────────────────────────────────────
# Insert repo root so 'run_rat_benchmark' resolves to the local copy.
_REPO_ROOT = str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Clear any cached version so stubs take effect.
sys.modules.pop("run_rat_benchmark", None)
import run_rat_benchmark as rrb  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# _apply_arm_env tests
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyArmEnv:
    """Unit-test the _apply_arm_env helper in isolation."""

    def test_v1gsps_sets_service_provision(self):
        """v1gsps must set DOCKERAGENT_ENABLE_SERVICE_PROVISION=1."""
        rrb._apply_arm_env("v1gsps")
        assert os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] == "1"

    def test_v1gsps_inherits_graph_scheduler(self):
        """v1gsps must inherit GRAPH_SCHEDULER=1 from v1gsp lineage."""
        rrb._apply_arm_env("v1gsps")
        assert os.environ["DOCKERAGENT_ENABLE_GRAPH_SCHEDULER"] == "1"

    def test_v1gsps_inherits_runtime_pin(self):
        """v1gsps must inherit RUNTIME_PIN=1 from v1gsp."""
        rrb._apply_arm_env("v1gsps")
        assert os.environ["DOCKERAGENT_ENABLE_RUNTIME_PIN"] == "1"

    def test_v1gsps_inherits_v1_flags(self):
        """v1gsps must set V1, DEP_GRAPH, DEP_EMIT, RUNTIME_FEEDBACK all to 1."""
        rrb._apply_arm_env("v1gsps")
        assert os.environ["DOCKERAGENT_ENABLE_V1"] == "1"
        assert os.environ["DOCKERAGENT_ENABLE_DEP_GRAPH"] == "1"
        assert os.environ["DOCKERAGENT_ENABLE_DEP_EMIT"] == "1"
        assert os.environ["DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK"] == "1"

    def test_v1gsps_clears_contract_graph(self):
        """v1gsps (like v1gsp/v1gs) must NOT set CONTRACT_GRAPH."""
        rrb._apply_arm_env("v1gsps")
        assert os.environ["DOCKERAGENT_ENABLE_CONTRACT_GRAPH"] == "0"

    # ── regression: v1gsp must NOT gain SERVICE_PROVISION ────────────────────

    def test_v1gsp_does_not_set_service_provision(self):
        """v1gsp regression guard: SERVICE_PROVISION must stay 0."""
        rrb._apply_arm_env("v1gsp")
        assert os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] == "0"

    def test_v1gsp_still_sets_runtime_pin(self):
        """v1gsp regression: RUNTIME_PIN must still be 1."""
        rrb._apply_arm_env("v1gsp")
        assert os.environ["DOCKERAGENT_ENABLE_RUNTIME_PIN"] == "1"

    def test_arm0_clears_all_flags(self):
        """arm0 baseline: no feature flags should be set."""
        rrb._apply_arm_env("arm0")
        assert os.environ["DOCKERAGENT_ENABLE_V1"] == "0"
        assert os.environ["DOCKERAGENT_ENABLE_SERVICE_PROVISION"] == "0"
        assert os.environ["DOCKERAGENT_ENABLE_RUNTIME_PIN"] == "0"


# ─────────────────────────────────────────────────────────────────────────────
# Child re-detection ordering test
# ─────────────────────────────────────────────────────────────────────────────

class TestChildReDetection:
    """
    Verify that when SERVICE_PROVISION=1 AND RUNTIME_PIN=1 are both set
    (as they are for v1gsps), the child re-detection resolves to 'v1gsps'
    and NOT 'v1gsp'.
    """

    def _detect_arm_from_env(self) -> str:
        """Mirror the child re-detection block in run_rat_benchmark.py."""
        if os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1":
            return "v1gsps"
        elif os.environ.get("DOCKERAGENT_ENABLE_RUNTIME_PIN") == "1":
            return "v1gsp"
        elif os.environ.get("DOCKERAGENT_ENABLE_GRAPH_SCHEDULER") == "1":
            return "v1gs"
        elif os.environ.get("DOCKERAGENT_ENABLE_RUNTIME_FEEDBACK") == "1":
            return "v1gder"
        elif os.environ.get("DOCKERAGENT_ENABLE_DEP_EMIT") == "1":
            return "v1gde"
        elif os.environ.get("DOCKERAGENT_ENABLE_DEP_GRAPH") == "1":
            return "v1gd"
        elif os.environ.get("DOCKERAGENT_ENABLE_CONTRACT_GRAPH") == "1":
            return "v1g"
        elif os.environ.get("DOCKERAGENT_ENABLE_V1") == "1":
            return "v1"
        else:
            return "arm0"

    def test_v1gsps_env_detects_as_v1gsps_not_v1gsp(self):
        """With v1gsps env vars set, child re-detection must yield 'v1gsps'."""
        rrb._apply_arm_env("v1gsps")
        detected = self._detect_arm_from_env()
        assert detected == "v1gsps", (
            f"Expected 'v1gsps' but detected '{detected}' — "
            "SERVICE_PROVISION branch must come BEFORE RUNTIME_PIN branch."
        )

    def test_v1gsp_env_detects_as_v1gsp(self):
        """With v1gsp env vars set, child re-detection must yield 'v1gsp'."""
        rrb._apply_arm_env("v1gsp")
        detected = self._detect_arm_from_env()
        assert detected == "v1gsp"
